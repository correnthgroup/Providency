# ruff: noqa: E501
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel, Field, SecretStr, field_validator

from providency import __version__
from providency.approvals import ApprovalError, ApprovalService, render_proposal
from providency.catalog import PatternCatalog, PatternCatalogError
from providency.config import (
    AnalysisConfiguration,
    ExecutionMode,
    RuntimeMode,
    Settings,
    TelegramConfiguration,
)
from providency.context import (
    ConfluenceItem,
    ConfluenceResult,
    ConfluenceStatus,
    PriceAnchor,
    PriceScale,
    PriceScaleError,
    analyze_context,
)
from providency.evidence import EvidenceSanitizer, RedactionRegion
from providency.execution import DemoExecutionService
from providency.metrics import pattern_metrics
from providency.observation import ObservationCoordinator, ObservationPhase
from providency.onboarding import Onboarding
from providency.patterns import Candle, PatternMatcher, PatternPackage
from providency.protection import PositionProtectionService
from providency.reporting import build_session_report, export_session_report
from providency.review import ReviewLabel, ReviewRequest, ReviewService
from providency.risk import RiskPolicy, SessionLimits, build_candidate
from providency.service import EngineService, PatternAnalysisService
from providency.storage import Storage
from providency.telegram import TelegramClient, TelegramClientContract, TelegramError
from providency.vector import (
    ChartCapture,
    DesiredVectorState,
    VectorAdapter,
    VectorAdapterContract,
    VectorAdapterError,
)
from providency.vector_settings import merge_vector_fields


class SessionPayload(BaseModel):
    id: str
    started_at: str
    ended_at: str | None
    status: Literal["RUNNING", "STOPPED", "INTERRUPTED"]


class StatePayload(BaseModel):
    engine_state: Literal["STOPPED", "RUNNING"]
    session: SessionPayload | None


class EventPayload(BaseModel):
    id: str
    session_id: str | None
    created_at: str
    level: str
    component: str
    event_type: str
    message: str
    details: dict[str, Any]


class CandidateEvaluationPayload(BaseModel):
    analysis_capture_id: str
    pattern_detection_id: str


class EmergencyStopPayload(BaseModel):
    confirm_demo_close: bool = False


class ShutdownPayload(BaseModel):
    confirm: Literal[True]


class SessionTelegramTokenPayload(BaseModel):
    token: SecretStr


class RedactionPayload(BaseModel):
    x: int
    y: int
    width: int
    height: int


class EvidenceSanitizePayload(BaseModel):
    detection_id: str
    redactions: list[RedactionPayload] = Field(default_factory=list)


class ObservationConfigurationPayload(BaseModel):
    model_config = {"extra": "forbid"}
    interval: dict[str, Any] = Field(default_factory=dict)
    charts: list[dict[str, Any]] = Field(default_factory=list)
    enabled_pattern_ids: list[str] = Field(default_factory=list)


class ReviewPayload(BaseModel):
    detection_id: str | None = None
    candidate_id: str | None = None
    label: ReviewLabel
    notes: str
    reviewer_id: str = "local:operator"
    evidence_sha256: str
    evidence_path: str


class TelegramConfigurationPayload(BaseModel):
    model_config = {"extra": "forbid"}
    chat_id: int = Field(strict=True)
    user_id: int = Field(strict=True, gt=0)
    approval_ttl_seconds: int = Field(default=60, strict=True, gt=0)
    recheck_price_tolerance_ticks: int = Field(default=1, strict=True, ge=0)

    @field_validator("chat_id")
    @classmethod
    def nonzero_chat(cls, value: int) -> int:
        if value == 0:
            raise ValueError("Informe o chat de destino.")
        return value


class ConfigurationPayload(BaseModel):
    schema_version: int = 1
    symbol: str = ""
    primary_timeframe: str = ""
    context_timeframe: str = ""
    trailing_timeframe: str = ""
    short_ma_period: int | None = Field(default=None, ge=1)
    long_ma_period: int | None = Field(default=None, ge=1)
    quantity: float = Field(default=0, ge=0, allow_inf_nan=False)
    tick_size: float = Field(default=0, ge=0)
    tick_value: float = Field(default=0, ge=0)
    stop_buffer_ticks: int = Field(default=1, ge=0)
    min_rr: float = Field(default=2, gt=0)
    max_trades: int = Field(default=0, ge=0)
    max_consecutive_losses: int = Field(default=0, ge=0)
    max_session_loss: float = Field(default=0, ge=0)
    pivot_window: int = Field(default=3, ge=1)
    support_resistance_tolerance: float = Field(default=0, ge=0)


def _absolute_candles(evidence: dict[str, Any], scale: PriceScale) -> list[Candle]:
    image_height = float(evidence["image_height"])
    result: list[Candle] = []
    for item in evidence["candles"]:
        candle = item["candle"]
        try:
            result.append(
                Candle(
                    open=scale.price_at(image_height - float(candle["open"])),
                    high=scale.price_at(image_height - float(candle["high"])),
                    low=scale.price_at(image_height - float(candle["low"])),
                    close=scale.price_at(image_height - float(candle["close"])),
                )
            )
        except (KeyError, TypeError, ValueError):
            return []
    return result


def _relative_candles(evidence: dict[str, Any]) -> list[Candle]:
    try:
        return [Candle(**item["candle"]) for item in evidence["candles"]]
    except (KeyError, TypeError, ValueError):
        return []


def create_app(
    settings: Settings | None = None,
    *,
    recover: bool = True,
    vector_adapter: VectorAdapterContract | None = None,
    telegram_client: TelegramClientContract | None = None,
    telegram_polling: bool = True,
    request_shutdown: Callable[[], None] | None = None,
) -> FastAPI:
    resolved = settings or Settings.from_env()
    onboarding = Onboarding(resolved)
    vector_snapshot: dict[str, Any] | None = None
    service = EngineService(Storage(resolved.database_path), recover=recover)

    async def vector_pages() -> list[Any]:
        return await onboarding.pages("web.vectorcrypto.com")

    adapter = vector_adapter or VectorAdapter(
        resolved, pages_provider=vector_pages, browser_lock=onboarding.lock
    )
    catalog = PatternCatalog(
        resolved.pattern_catalog_dir, illustration_dir=resolved.data_dir / "catalog-illustrations"
    )
    matcher = PatternMatcher(
        PatternPackage.load(resolved.pattern_catalog_dir / "bearish_engulfing" / "pattern.yaml")
    )
    analysis = PatternAnalysisService(service.storage, matcher)
    stored_configuration = service.storage.latest_configuration()
    configuration = resolved.analysis_configuration or (
        AnalysisConfiguration.from_mapping(stored_configuration["desired"])
        if stored_configuration is not None
        else resolved.trading_configuration
    )
    stored_telegram = service.storage.telegram_configuration()
    telegram_configuration = resolved.telegram_configuration or (
        TelegramConfiguration(**stored_telegram) if stored_telegram else resolved.telegram
    )
    telegram = telegram_client or TelegramClient()
    financial_mode = (
        resolved.execution_mode
        if isinstance(resolved.execution_mode, ExecutionMode)
        else ExecutionMode.DRY_RUN
    )
    observation = ObservationCoordinator(
        service.storage,
        adapter,
        catalog,
        telegram,
        execution_mode=resolved.execution_mode,
        telegram_chat_id=telegram_configuration.chat_id,
    )
    approvals = ApprovalService(
        service.storage,
        chat_id=telegram_configuration.chat_id,
        user_id=telegram_configuration.user_id,
        ttl_seconds=telegram_configuration.approval_ttl_seconds,
        dry_run=resolved.execution_mode is ExecutionMode.DRY_RUN,
    )
    protection = PositionProtectionService(service.storage, adapter, mode=financial_mode)
    execution = DemoExecutionService(
        service.storage,
        adapter,
        mode=financial_mode,
        protection=protection,
    )
    reviews = ReviewService(service.storage)
    evidence = EvidenceSanitizer(resolved.data_dir / "sanitized-evidence")
    telegram_offset: int | None = None
    telegram_poll_error = False
    stop_polling = asyncio.Event()
    shutting_down = False
    active_commands = 0
    active_callbacks = 0
    service.storage.record_configuration(configuration.version, configuration.to_dict())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task: asyncio.Task[None] | None = None
        if telegram_polling:
            task = asyncio.create_task(poll_worker())
        try:
            yield
        finally:
            stop_polling.set()
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await observation.close()
            await adapter.stop()
            await onboarding.close()
            clear_token = getattr(telegram, "clear_session_token", None)
            if callable(clear_token):
                clear_token()

    app = FastAPI(title="Providency Core Engine", version=__version__, lifespan=lifespan)
    onboarding.install(app)

    @app.middleware("http")
    async def lifecycle_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        nonlocal active_commands
        command = request.method not in {"GET", "HEAD", "OPTIONS"}
        if not command or request.url.path == "/shutdown":
            return await call_next(request)
        if shutting_down:
            return JSONResponse(
                status_code=409,
                content={"detail": {"human_message": "O Providency está encerrando."}},
            )
        if resolved.execution_mode is RuntimeMode.OBSERVATION_ONLY:
            financial_prefixes = (
                "/run",
                "/stop",
                "/candidate",
                "/approvals",
                "/operations",
                "/execution",
            )
            financial_mutation = request.method not in {"GET", "HEAD", "OPTIONS"} and (
                request.url.path in {"/configuration", "/vector/sync"}
                or request.url.path.startswith(financial_prefixes)
            )
            if financial_mutation:
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": {
                            "error_code": "PV-OBSERVATION-001",
                            "short_title": "Modo somente leitura",
                            "human_message": "Esta instalação está em OBSERVATION_ONLY; ações financeiras estão bloqueadas.",
                            "impact": "Nenhuma ordem, sincronização financeira ou WOULD_EXECUTE será produzida.",
                            "suggested_actions": [
                                "Use as rotas /observation para observar os gráficos."
                            ],
                        }
                    },
                )
        active_commands += 1
        try:
            return await call_next(request)
        finally:
            active_commands -= 1

    @app.post("/shutdown", status_code=202)
    async def shutdown(
        payload: ShutdownPayload, request: Request, background: BackgroundTasks
    ) -> dict[str, str]:
        nonlocal shutting_down
        allowed_origins = {
            f"http://127.0.0.1:{resolved.ui_port}",
            f"http://localhost:{resolved.ui_port}",
        }
        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed_origins:
            raise HTTPException(status_code=403, detail="Use o painel local do Providency.")
        if request_shutdown is None:
            raise HTTPException(status_code=409, detail="Inicie o aplicativo pelo launcher.")
        if shutting_down:
            return {"status": "SHUTTING_DOWN"}
        if active_commands or active_callbacks:
            raise HTTPException(
                status_code=409,
                detail="Aguarde o comando em andamento terminar e tente encerrar novamente.",
            )
        if (
            service.storage.open_operations()
            or service.storage.filled_operations_requiring_recovery()
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Há uma operação pendente ou posição demo sob gerenciamento. "
                    "Resolva a operação e confirme o encerramento da posição antes de sair."
                ),
            )
        # No await between the preconditions and the guard: callbacks cannot enter here.
        shutting_down = True
        stop_polling.set()
        session = service.storage.running_session()
        if session:
            approvals.cancel_session(str(session["id"]), "Providency is shutting down.")
        service.stop()
        service.storage.record_event(
            session_id=str(session["id"]) if session else None,
            level="INFO",
            component="runtime",
            event_type="APPLICATION_SHUTDOWN_REQUESTED",
            message="Providency shutdown requested by the local operator.",
        )
        # Uvicorn drains requests and runs lifespan cleanup after sending this response.
        background.add_task(request_shutdown)
        return {"status": "SHUTTING_DOWN"}

    def vector_error(exc: VectorAdapterError) -> HTTPException:
        session = service.storage.running_session()
        return HTTPException(
            status_code=409,
            detail={
                "error_code": exc.code,
                "short_title": "Vector Web is not ready",
                "human_message": str(exc),
                "impact": "No market decision was produced.",
                "suggested_actions": [
                    "Open Vector Web from Providency.",
                    "Complete login manually and keep the chart visible.",
                    "Try the capture again.",
                ],
                "technical_details": {"adapter_error": exc.code},
                "component": "vector",
                "session_id": str(session["id"]) if session else None,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/state", response_model=StatePayload)
    def state() -> dict[str, Any]:
        return service.snapshot()

    @app.post("/run", response_model=SessionPayload)
    async def run() -> dict[str, Any]:
        session = service.run()
        if resolved.execution_mode is ExecutionMode.DEMO:
            health = await adapter.health_check()
            if health.get("state") == "OPEN":
                await execution.reconcile_open_operations()
                await protection.recover_all()
        return session

    @app.post("/stop", response_model=SessionPayload | None)
    def stop() -> dict[str, Any] | None:
        return service.stop()

    @app.get("/events", response_model=list[EventPayload])
    def events(limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
        return service.storage.list_events(limit)

    @app.get("/observation/configuration")
    def observation_configuration() -> dict[str, Any]:
        state = observation.state()
        configuration = dict(state["configuration"])
        configuration.update(
            {
                "state": observation.phase.value,
                "editable": observation.phase in {ObservationPhase.STOPPED, ObservationPhase.ERROR},
            }
        )
        return configuration

    @app.put("/observation/configuration")
    def configure_observation(
        payload: ObservationConfigurationPayload, request: Request
    ) -> dict[str, Any]:
        origin = request.headers.get("origin")
        if origin is not None and origin not in {
            f"http://127.0.0.1:{resolved.ui_port}",
            f"http://localhost:{resolved.ui_port}",
        }:
            raise HTTPException(403, "Use o painel local do Providency.")
        try:
            return observation.configure(payload.model_dump())
        except (ValueError, PatternCatalogError) as exc:
            status = 409 if "Pare" in str(exc) else 422
            raise HTTPException(
                status, detail={"error_code": "PV-OBSERVATION-002", "human_message": str(exc)}
            ) from exc

    @app.get("/observation/state")
    def observation_state() -> dict[str, Any]:
        return observation.state()

    @app.post("/observation/start")
    async def observation_start() -> dict[str, Any]:
        if vector_adapter is None and observation.phase in {
            ObservationPhase.STOPPED,
            ObservationPhase.ERROR,
        }:
            prepared = onboarding.state()
            if not prepared["connected"] or prepared["stage"] != "READY":
                raise HTTPException(
                    409, "Conclua a preparação da Vector e do Telegram antes de iniciar."
                )
            observation.configure(
                {**observation.configuration.to_dict(), "charts": prepared["charts"]}
            )
        if not observation.configuration.charts:
            onboarding_charts = onboarding.state().get("charts", [])
            if onboarding_charts:
                try:
                    observation.configure(
                        {**observation.configuration.to_dict(), "charts": onboarding_charts}
                    )
                except (ValueError, PatternCatalogError) as exc:
                    raise HTTPException(409, detail=str(exc)) from exc
        try:
            return await observation.start()
        except (ValueError, RuntimeError, PatternCatalogError) as exc:
            raise HTTPException(
                409, detail={"error_code": "PV-OBSERVATION-003", "human_message": str(exc)}
            ) from exc

    @app.post("/observation/stop")
    async def observation_stop() -> dict[str, Any]:
        return await observation.stop()

    @app.get("/observation/cycles")
    def observation_cycles(
        session_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict[str, Any]]:
        return service.storage.list_observation_cycles(
            session_id=session_id, limit=limit, offset=offset
        )

    @app.get("/patterns/catalog")
    def patterns_catalog() -> list[dict[str, Any]]:
        entries = catalog.to_list()
        references = service.storage.observation_pattern_references()
        for entry in entries:
            reference = references.get((entry["id"], entry["version"]))
            if reference:
                path = Path(reference["path"]).resolve()
                if path.is_relative_to(resolved.capture_dir.resolve()) and path.is_file():
                    entry["reference_capture"] = reference
        return entries

    @app.get("/sessions/{session_id}/report")
    def session_report(session_id: str) -> dict[str, Any]:
        try:
            return build_session_report(service.storage, session_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/sessions/{session_id}/report/export")
    def session_report_export(session_id: str) -> dict[str, str]:
        try:
            path = export_session_report(
                service.storage,
                session_id,
                resolved.data_dir / "reports",
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"path": str(path)}

    @app.get("/configuration")
    def configured_state() -> dict[str, Any]:
        return {
            "desired": configuration.to_dict(),
            "applied": service.storage.latest_applied_state(),
            "execution_mode": resolved.execution_mode.value,
            "vector_snapshot": vector_snapshot,
            "browser_connected": onboarding.bridge.connected,
        }

    @app.post("/configuration/refresh")
    async def refresh_configuration(request: Request) -> dict[str, Any]:
        nonlocal configuration, vector_snapshot
        if (
            request.headers.get("origin")
            not in {
                None,
                f"http://127.0.0.1:{resolved.ui_port}",
                f"http://localhost:{resolved.ui_port}",
            }
            or request.headers.get("content-type", "").split(";")[0] != "application/json"
        ):
            raise HTTPException(403, "Origem ou formato da solicitação não autorizado.")
        if service.storage.running_session() is not None:
            raise HTTPException(409, "Pare a operação antes de atualizar os parâmetros.")
        async with onboarding.lock:
            previous_version = configuration.version
            vector_snapshot = None
            try:
                async with asyncio.timeout(8):
                    snapshot = await VectorAdapter.read_browser_settings(
                        await onboarding.pages("web.vectorcrypto.com")
                    )
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            except (PlaywrightError, TimeoutError) as exc:
                raise HTTPException(
                    409, "A leitura da Vector falhou. Confira a extensão e tente novamente."
                ) from exc
            if (
                configuration.version != previous_version
                or service.storage.running_session() is not None
            ):
                raise HTTPException(
                    409, "A sessão ou configuração mudou durante a leitura. Tente novamente."
                )
            configuration = merge_vector_fields(configuration, snapshot["fields"])
            service.storage.record_configuration(configuration.version, configuration.to_dict())
            vector_snapshot = snapshot
            return configured_state()

    @app.put("/configuration")
    def update_configuration(payload: ConfigurationPayload) -> dict[str, Any]:
        nonlocal configuration
        if service.storage.running_session() is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "human_message": (
                        "Pare a operação antes de alterar os parâmetros. "
                        "Isso preserva a configuração usada pela sessão atual."
                    )
                },
            )
        configuration = AnalysisConfiguration.from_mapping(payload.model_dump())
        service.storage.record_configuration(configuration.version, configuration.to_dict())
        return configured_state()

    @app.get("/execution/status")
    def execution_status() -> dict[str, Any]:
        return {
            "mode": resolved.execution_mode.value,
            "demo_only": True,
            "blocking_operation": service.storage.has_blocking_operation(),
            "blocking_protection": service.storage.has_blocking_protection(),
            "latest": service.storage.list_operations(1),
            "latest_protection": service.storage.list_protection_policies(1),
        }

    @app.get("/operations")
    def operations(limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
        return service.storage.list_operations(limit)

    @app.post("/operations/reconcile")
    async def reconcile_operations() -> list[dict[str, Any]]:
        if resolved.execution_mode is not ExecutionMode.DEMO:
            raise HTTPException(status_code=409, detail="Demo execution is not enabled.")
        return await execution.reconcile_open_operations()

    @app.get("/protections")
    def protections(limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
        return service.storage.list_protection_policies(limit)

    @app.post("/operations/{operation_id}/protection/manage")
    async def manage_protection(operation_id: str) -> dict[str, Any]:
        if resolved.execution_mode is not ExecutionMode.DEMO:
            raise HTTPException(status_code=409, detail="Demo protection is not enabled.")
        operation = service.storage.get_operation(operation_id)
        if operation is None or operation["status"] != "FILLED":
            raise HTTPException(status_code=404, detail="Filled demo operation was not found.")
        return await protection.manage(operation)

    @app.post("/operations/{operation_id}/emergency-stop")
    async def emergency_stop(operation_id: str, payload: EmergencyStopPayload) -> dict[str, Any]:
        if resolved.execution_mode is not ExecutionMode.DEMO:
            raise HTTPException(status_code=409, detail="Demo emergency is not enabled.")
        if not payload.confirm_demo_close:
            raise HTTPException(
                status_code=400,
                detail="Explicit confirmation of demo cancellation and close is required.",
            )
        operation = service.storage.get_operation(operation_id)
        if operation is None or operation["status"] != "FILLED":
            raise HTTPException(status_code=404, detail="Filled demo operation was not found.")
        return await protection.emergency_stop(operation)

    @app.get("/vector/health")
    async def vector_health() -> dict[str, str]:
        return await adapter.health_check()

    @app.post("/vector/open")
    async def vector_open() -> dict[str, str]:
        try:
            return await adapter.open_vector()
        except VectorAdapterError as exc:
            raise vector_error(exc) from exc

    @app.post("/vector/capture")
    async def vector_capture() -> dict[str, Any]:
        try:
            capture = await adapter.capture_primary_chart()
        except VectorAdapterError as exc:
            raise vector_error(exc) from exc
        payload = capture.to_dict()
        session = service.storage.running_session()
        usable = payload["disposition"] == "USABLE"
        service.storage.record_event(
            session_id=str(session["id"]) if session else None,
            level="INFO" if usable else "WARNING",
            component="vector",
            event_type="VECTOR_CAPTURE_USABLE" if usable else "VECTOR_CAPTURE_BLOCKED",
            message=(
                "Vector chart capture is usable."
                if usable
                else f"Vector chart capture was blocked: {payload['issue']}."
            ),
            details=payload,
        )
        return payload

    @app.post("/vector/sync")
    async def vector_sync() -> dict[str, Any]:
        desired = DesiredVectorState(
            configuration.symbol,
            configuration.primary_timeframe,
            configuration.moving_average_periods,
            True,
        )
        try:
            result = await adapter.sync_analysis_state(desired)
        except VectorAdapterError as exc:
            raise vector_error(exc) from exc
        state_id = service.storage.record_applied_state(
            configuration_version=configuration.version,
            matches_desired=result.matches_desired,
            state=result.to_dict(),
        )
        return {"applied_state_id": state_id, **result.to_dict()}

    @app.post("/vector/capture-analysis")
    async def vector_capture_analysis() -> dict[str, Any]:
        desired = DesiredVectorState(
            configuration.symbol,
            configuration.primary_timeframe,
            configuration.moving_average_periods,
            True,
        )
        try:
            primary, context, restored = await adapter.capture_primary_context(
                desired, configuration.context_timeframe
            )
        except VectorAdapterError as exc:
            raise vector_error(exc) from exc
        state_id = service.storage.record_applied_state(
            configuration_version=configuration.version,
            matches_desired=restored.matches_desired,
            state=restored.to_dict(),
        )
        capture_id = service.storage.record_analysis_capture(
            session_id=(
                str(current["id"]) if (current := service.storage.running_session()) else None
            ),
            applied_state_id=state_id,
            primary=primary.to_dict(),
            context=context.to_dict(),
        )
        pattern_result, detection_id = analysis.analyze_with_id(primary)
        return {
            "analysis_capture_id": capture_id,
            "primary": primary.to_dict(),
            "context": context.to_dict(),
            "restored": restored.to_dict(),
            "applied_state_id": state_id,
            "pattern_detection_id": detection_id,
            "pattern": pattern_result.to_dict(),
        }

    @app.post("/pattern/analyze")
    async def pattern_analyze() -> dict[str, Any]:
        try:
            capture = await adapter.capture_primary_chart()
        except VectorAdapterError as exc:
            raise vector_error(exc) from exc
        payload = capture.to_dict()
        session = service.storage.running_session()
        usable = capture.disposition.value == "USABLE"
        service.storage.record_event(
            session_id=str(session["id"]) if session else None,
            level="INFO" if usable else "WARNING",
            component="vector",
            event_type="VECTOR_CAPTURE_USABLE" if usable else "VECTOR_CAPTURE_BLOCKED",
            message=(
                "Vector chart capture is usable."
                if usable
                else f"Vector chart capture was blocked: {payload['issue']}."
            ),
            details=payload,
        )
        return analysis.analyze(capture).to_dict()

    @app.get("/pattern/detections")
    def pattern_detections(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        return service.storage.list_pattern_detections(limit)

    @app.post("/evidence/sanitize")
    def sanitize_evidence(payload: EvidenceSanitizePayload) -> dict[str, Any]:
        detection = service.storage.get_pattern_detection(payload.detection_id)
        if detection is None:
            raise HTTPException(status_code=404, detail="Pattern detection was not found.")
        try:
            source_path = Path(str(detection["screenshot_path"])).resolve(strict=True)
            if not source_path.is_relative_to(resolved.capture_dir.resolve()):
                raise ValueError("Evidence source is outside the Providency capture directory.")
            result = evidence.sanitize(
                source_path,
                source_sha256=str(detection["screenshot_sha256"]),
                redactions=tuple(
                    RedactionRegion(
                        x=item.x,
                        y=item.y,
                        width=item.width,
                        height=item.height,
                    )
                    for item in payload.redactions
                ),
                metadata={
                    "detection_id": detection["id"],
                    "pattern_id": detection["pattern_id"],
                    "pattern_version": detection["pattern_version"],
                    "result": detection["result"],
                },
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return result.to_dict()

    @app.post("/evidence/retention")
    def apply_evidence_retention() -> dict[str, Any]:
        try:
            moved = evidence.apply_retention(retention_days=resolved.evidence_retention_days)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "retention_days": resolved.evidence_retention_days,
            "moved_to_recoverable_trash": [str(path) for path in moved],
        }

    @app.post("/reviews")
    def create_review(payload: ReviewPayload) -> dict[str, Any]:
        try:
            evidence_path = Path(payload.evidence_path).resolve(strict=True)
            if not evidence_path.is_relative_to(evidence.destination):
                raise ValueError("Review evidence must be a Providency sanitized copy.")
            return reviews.create(
                ReviewRequest(
                    detection_id=payload.detection_id,
                    candidate_id=payload.candidate_id,
                    label=payload.label,
                    notes=payload.notes,
                    reviewer_id=payload.reviewer_id,
                    evidence_sha256=payload.evidence_sha256,
                    evidence_path=str(evidence_path),
                )
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/reviews")
    def list_reviews(limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
        return service.storage.list_human_reviews(limit)

    @app.get("/metrics/patterns/{pattern_id}")
    def metrics(
        pattern_id: str,
        limit: int = Query(default=500, ge=1, le=500),
        minimum_sample: int = Query(default=20, ge=1, le=10000),
    ) -> dict[str, Any]:
        return pattern_metrics(
            service.storage,
            pattern_id,
            limit=limit,
            minimum_sample=minimum_sample,
        )

    @app.post("/candidate/evaluate")
    def candidate_evaluate(payload: CandidateEvaluationPayload) -> dict[str, Any]:
        session = service.storage.running_session()
        if session is None:
            raise HTTPException(status_code=409, detail="RUN is required before evaluation.")
        capture_pair = service.storage.get_analysis_capture(payload.analysis_capture_id)
        detection = service.storage.get_pattern_detection(payload.pattern_detection_id)
        if capture_pair is None or capture_pair["session_id"] != session["id"]:
            raise HTTPException(status_code=409, detail="Analysis capture is missing or stale.")
        if detection is None or detection["session_id"] != session["id"]:
            raise HTTPException(status_code=409, detail="Pattern detection is missing.")
        primary_capture = ChartCapture.from_dict(capture_pair["primary"])
        context_capture = ChartCapture.from_dict(capture_pair["context"])
        if detection["screenshot_sha256"] != primary_capture.sha256:
            raise HTTPException(
                status_code=409, detail="Pattern detection does not belong to the primary capture."
            )
        applied = service.storage.get_applied_state(str(capture_pair["applied_state_id"]))
        applied_ok = bool(
            applied
            and applied["configuration_version"] == configuration.version
            and applied["matches_desired"]
        )
        post = applied["state"]["post"] if applied else {}
        anchors = tuple(PriceAnchor(**item) for item in post.get("price_anchors", []))
        scale: PriceScale | None = None
        with suppress(PriceScaleError):
            scale = PriceScale.from_anchors(anchors, tick_size=configuration.tick_size)
        evidence = detection["evidence"].get("evidence")
        primary = _absolute_candles(evidence, scale) if evidence and scale else []
        context_result = analysis.analyze(context_capture)
        context_evidence = context_result.evidence
        context = (
            _relative_candles(context_result.to_dict()["evidence"])
            if context_evidence is not None
            else []
        )
        moving_averages = post.get("moving_averages", {})
        confluence = analyze_context(
            side="SHORT",
            primary=primary,
            context=context,
            short_ma=(
                moving_averages.get(str(configuration.short_ma_period))
                if configuration.short_ma_period is not None
                else None
            ),
            long_ma=(
                moving_averages.get(str(configuration.long_ma_period))
                if configuration.long_ma_period is not None
                else None
            ),
            moving_averages_enabled=bool(configuration.moving_average_periods),
            context_family=(
                matcher.package.family if context_result.status.value == "MATCH" else None
            ),
            compatible_context_families=matcher.package.compatible_context_families,
            support_resistance_tolerance=configuration.support_resistance_tolerance,
        )
        items = dict(confluence.items)
        items["pattern"] = ConfluenceItem(
            ConfluenceStatus.PASS if detection["result"] == "MATCH" else ConfluenceStatus.FAIL,
            "Primary pattern is confirmed."
            if detection["result"] == "MATCH"
            else f"Primary pattern status is {detection['result']}.",
        )
        items["applied_state"] = ConfluenceItem(
            ConfluenceStatus.PASS if applied_ok else ConfluenceStatus.MISSING,
            "Applied Vector state matches the desired state."
            if applied_ok
            else "Verified applied Vector state is missing or divergent.",
        )
        items["primary_restoration"] = ConfluenceItem(
            ConfluenceStatus.PASS if applied_ok else ConfluenceStatus.FAIL,
            "Primary timeframe was restored."
            if applied_ok
            else "Primary timeframe restoration was not verified.",
        )
        if scale is not None:
            items["price_scale"] = ConfluenceItem(
                ConfluenceStatus.PASS,
                "Price scale is linear within the configured tick.",
                scale.to_dict(),
            )
        else:
            items["price_scale"] = ConfluenceItem(
                ConfluenceStatus.MISSING,
                "Stored price anchors are missing or invalid for the configured tick.",
            )
        if configuration.missing_fields:
            items["configuration"] = ConfluenceItem(
                ConfluenceStatus.MISSING,
                "Required configuration is missing: " + ", ".join(configuration.missing_fields),
            )
        else:
            items["configuration"] = ConfluenceItem(
                ConfluenceStatus.PASS, "Required configuration is complete."
            )
        confluence = ConfluenceResult(items)
        formation = evidence["candles"][-matcher.package.sequence_candles :] if evidence else []
        high = (
            scale.price_at(min(float(item["box"]["y"]) for item in formation))
            if scale is not None and formation
            else None
        )
        low = (
            scale.price_at(
                max(float(item["box"]["y"]) + float(item["box"]["height"]) for item in formation)
            )
            if scale is not None and formation
            else None
        )
        stored_limits = service.storage.session_limits(str(session["id"]))
        limits = (
            SessionLimits(
                trades=int(stored_limits["trades"]),
                consecutive_losses=int(stored_limits["consecutive_losses"]),
                loss=float(stored_limits["loss"]),
            )
            if stored_limits is not None
            else SessionLimits(trades=None, consecutive_losses=None, loss=None)
        )
        candidate = build_candidate(
            session_id=str(session["id"]),
            symbol=configuration.symbol,
            side="SHORT",
            pattern_id=matcher.package.id,
            pattern_version=matcher.package.version,
            configuration_version=configuration.version,
            primary_capture_sha256=str(primary_capture.sha256),
            context_capture_sha256=str(context_capture.sha256),
            pattern_high=high,
            pattern_low=low,
            quantity=configuration.quantity,
            confluence=confluence,
            policy=RiskPolicy(
                tick_size=configuration.tick_size,
                tick_value=configuration.tick_value,
                stop_buffer_ticks=configuration.stop_buffer_ticks,
                min_rr=configuration.min_rr,
                max_trades=configuration.max_trades,
                max_consecutive_losses=configuration.max_consecutive_losses,
                max_session_loss=configuration.max_session_loss,
            ),
            limits=limits,
            pattern_detection_id=payload.pattern_detection_id,
            applied_state_id=str(applied["id"]) if applied else None,
            price_scale=scale.to_dict() if scale else None,
            configuration_snapshot=configuration.to_dict(),
        )
        result = candidate.to_dict()
        service.storage.record_trade_candidate(
            result,
            max_trades=configuration.max_trades,
            max_consecutive_losses=configuration.max_consecutive_losses,
            max_session_loss=configuration.max_session_loss,
        )
        service.storage.record_event(
            session_id=str(session["id"]),
            level="INFO" if result["decision"] == "ALLOWED" else "WARNING",
            component="risk",
            event_type=f"CANDIDATE_{result['decision']}",
            message=(
                "Trade candidate passed local preflight."
                if result["decision"] == "ALLOWED"
                else "Trade candidate was blocked by local preflight."
            ),
            details={
                "candidate_id": result["candidate_id"],
                "blocking_reasons": result["blocking_reasons"],
            },
        )
        return result

    def recheck_differences(original: dict[str, Any], fresh: dict[str, Any]) -> list[str]:
        differences: list[str] = []
        exact_fields = (
            "session_id",
            "symbol",
            "side",
            "pattern_id",
            "pattern_version",
            "configuration_version",
            "quantity",
            "primary_capture_sha256",
            "context_capture_sha256",
        )
        for field in exact_fields:
            if original.get(field) != fresh.get(field):
                differences.append(f"{field} changed")
        price_tolerance = (
            telegram_configuration.recheck_price_tolerance_ticks * configuration.tick_size
        )
        for field in ("entry", "stop"):
            before = original.get(field)
            after = fresh.get(field)
            if before is None or after is None:
                if before != after:
                    differences.append(f"{field} changed")
            elif abs(float(before) - float(after)) > price_tolerance:
                differences.append(f"{field} changed beyond tolerance")
        risk_tolerance = (
            telegram_configuration.recheck_price_tolerance_ticks
            * configuration.tick_value
            * configuration.quantity
        )
        risk_change = abs(
            float(original.get("risk_amount", 0)) - float(fresh.get("risk_amount", 0))
        )
        if risk_change > risk_tolerance:
            differences.append("risk_amount changed beyond tolerance")
        rr_change = abs(
            float(original.get("reference_rr", 0)) - float(fresh.get("reference_rr", 0))
        )
        if rr_change > 1e-9:
            differences.append("reference_rr changed")
        if fresh.get("decision") != "ALLOWED":
            differences.append("fresh risk preflight is blocked")
        original_configuration = json.dumps(
            original.get("configuration_snapshot"), separators=(",", ":"), sort_keys=True
        )
        fresh_configuration = json.dumps(
            fresh.get("configuration_snapshot"), separators=(",", ":"), sort_keys=True
        )
        if original_configuration != fresh_configuration:
            differences.append("configuration snapshot changed")
        return differences

    async def finish_approved_recheck(approval: dict[str, Any]) -> dict[str, Any]:
        try:
            capture = await vector_capture_analysis()
            fresh = candidate_evaluate(
                CandidateEvaluationPayload(
                    analysis_capture_id=capture["analysis_capture_id"],
                    pattern_detection_id=capture["pattern_detection_id"],
                )
            )
            differences = recheck_differences(approval["candidate_snapshot"], fresh)
            recheck = {
                "analysis_capture_id": capture["analysis_capture_id"],
                "pattern_detection_id": capture["pattern_detection_id"],
                "candidate": fresh,
                "differences": differences,
            }
        except Exception as exc:
            return approvals.finish_recheck(
                str(approval["id"]),
                passed=False,
                recheck={"error_type": type(exc).__name__},
                reason="Recheck failed closed before any financial action.",
            )
        finished = approvals.finish_recheck(
            str(approval["id"]),
            passed=not differences,
            recheck=recheck,
            reason=(
                "Fresh capture and complete preflight match the proposal."
                if not differences
                else "Recheck cancelled the proposal: " + "; ".join(differences)
            ),
        )
        if resolved.execution_mode is ExecutionMode.DEMO and finished["status"] == "WOULD_EXECUTE":
            return await execution.execute_approved(finished)
        return finished

    async def process_callback(callback_data: str, *, chat_id: int, user_id: int) -> dict[str, Any]:
        nonlocal active_callbacks
        if shutting_down:
            raise ApprovalError("Providency is shutting down.")
        active_callbacks += 1
        try:
            approval = approvals.consume(callback_data, chat_id=chat_id, user_id=user_id)
            if approval["status"] == "APPROVED":
                return await finish_approved_recheck(approval)
            return approval
        finally:
            active_callbacks -= 1

    async def poll_once() -> list[dict[str, Any]]:
        nonlocal telegram_offset
        batch = await telegram.poll(offset=telegram_offset)
        telegram_offset = batch.next_offset
        results: list[dict[str, Any]] = []
        for callback in batch.callbacks:
            try:
                result = await process_callback(
                    callback.data, chat_id=callback.chat_id, user_id=callback.user_id
                )
                results.append(result)
                await telegram.answer_callback(callback.callback_query_id, str(result["status"]))
            except ApprovalError:
                await telegram.answer_callback(callback.callback_query_id, "Rejected")
        return results

    async def poll_worker() -> None:
        nonlocal telegram_poll_error
        while not stop_polling.is_set():
            approvals.expire()
            if telegram_configuration.missing_fields or service.storage.running_session() is None:
                await asyncio.sleep(1)
                continue
            try:
                await poll_once()
                if telegram_poll_error:
                    current = service.storage.running_session()
                    service.storage.record_event(
                        session_id=str(current["id"]) if current else None,
                        level="INFO",
                        component="telegram",
                        event_type="TELEGRAM_POLL_RECOVERED",
                        message="Telegram approval polling recovered.",
                    )
                    telegram_poll_error = False
                await asyncio.sleep(1)
            except TelegramError:
                if not telegram_poll_error:
                    current = service.storage.running_session()
                    service.storage.record_event(
                        session_id=str(current["id"]) if current else None,
                        level="WARNING",
                        component="telegram",
                        event_type="TELEGRAM_POLL_FAILED",
                        message="Telegram polling failed; approvals remain fail-closed.",
                    )
                    telegram_poll_error = True
                await asyncio.sleep(5)

    @app.get("/telegram/status")
    def telegram_status() -> dict[str, Any]:
        return {
            "configuration": telegram_configuration.to_dict(),
            "polling": telegram_polling and not telegram_configuration.missing_fields,
            "poll_error": telegram_poll_error,
            "browser_destination": onboarding.state()["destination"],
            "session_active": service.storage.running_session() is not None,
        }

    @app.put("/telegram/configuration")
    async def configure_telegram(
        payload: TelegramConfigurationPayload,
        request: Request,
    ) -> dict[str, Any]:
        nonlocal telegram_configuration, telegram_poll_error
        if request.headers.get("origin") not in {
            None,
            f"http://127.0.0.1:{resolved.ui_port}",
            f"http://localhost:{resolved.ui_port}",
        }:
            raise HTTPException(403, "Use o painel local do Providency.")
        if service.storage.running_session() is not None or active_callbacks:
            raise HTTPException(409, "Pare a sessão antes de alterar o Telegram.")
        values = payload.model_dump()
        service.storage.save_telegram_configuration(values)
        telegram_configuration = TelegramConfiguration(**values)
        approvals.chat_id = telegram_configuration.chat_id
        approvals.user_id = telegram_configuration.user_id
        approvals.ttl_seconds = telegram_configuration.approval_ttl_seconds
        telegram_poll_error = False
        return telegram_status()

    @app.get("/telegram/health")
    async def telegram_health() -> dict[str, Any]:
        try:
            return await telegram.health_check()
        except TelegramError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/telegram/credential-status")
    async def telegram_credential_status() -> dict[str, bool]:
        reader = getattr(telegram, "credential_status", None)
        status = await reader() if callable(reader) else {}
        return {
            key: status.get(key) is True if isinstance(status, dict) else False
            for key in ("saved", "valid_format", "unavailable", "session_only")
        }

    @app.put("/telegram/session-token")
    async def use_telegram_session_token(
        payload: SessionTelegramTokenPayload, request: Request
    ) -> dict[str, bool]:
        if request.headers.get("origin") not in {
            None,
            f"http://127.0.0.1:{resolved.ui_port}",
            f"http://localhost:{resolved.ui_port}",
        }:
            raise HTTPException(403, "Use o painel local do Providency.")
        if observation.phase not in {ObservationPhase.STOPPED, ObservationPhase.ERROR}:
            raise HTTPException(409, "Pare o bot antes de trocar a credencial.")
        if service.storage.running_session() is not None or active_callbacks:
            raise HTTPException(409, "Pare a sessão antes de trocar a credencial.")
        setter = getattr(telegram, "use_session_token", None)
        if not callable(setter):
            raise HTTPException(409, "Cliente Telegram sem suporte a credencial temporária.")
        try:
            setter(payload.token.get_secret_value().strip())
        except TelegramError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"session_only": True}

    @app.delete("/telegram/session-token")
    async def clear_telegram_session_token(request: Request) -> dict[str, bool]:
        if request.headers.get("origin") not in {
            None,
            f"http://127.0.0.1:{resolved.ui_port}",
            f"http://localhost:{resolved.ui_port}",
        }:
            raise HTTPException(403, "Use o painel local do Providency.")
        if observation.phase not in {ObservationPhase.STOPPED, ObservationPhase.ERROR}:
            raise HTTPException(409, "Pare o bot antes de remover a credencial.")
        if service.storage.running_session() is not None or active_callbacks:
            raise HTTPException(409, "Pare a sessão antes de remover a credencial.")
        clearer = getattr(telegram, "clear_session_token", None)
        if callable(clearer):
            clearer()
        return {"session_only": False}

    @app.get("/telegram/discovery")
    async def telegram_discovery() -> list[dict[str, Any]]:
        if service.storage.running_session() is not None:
            raise HTTPException(409, "Pare a sessão antes de identificar o Telegram.")
        try:
            return await telegram.discover_destinations()
        except TelegramError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/approvals/{candidate_id}")
    async def create_approval(candidate_id: str) -> dict[str, Any]:
        candidate = service.storage.get_trade_candidate(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Candidate was not found.")
        try:
            created = approvals.create(candidate)
            if created.approval["status"] != "PENDING":
                return created.approval
            if created.approval["telegram"] is not None:
                return created.approval
            sent = await telegram.send_proposal(
                chat_id=telegram_configuration.chat_id,
                text=render_proposal(created.approval, resolved.execution_mode.value),
                yes_callback=created.yes_callback,
                no_callback=created.no_callback,
            )
            return service.storage.mark_approval_sent(str(created.approval["id"]), sent)
        except (ApprovalError, TelegramError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/telegram/poll-once")
    async def telegram_poll_once() -> list[dict[str, Any]]:
        try:
            return await poll_once()
        except TelegramError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/approvals/expire")
    def expire_approvals() -> dict[str, int]:
        return {"expired": approvals.expire()}

    @app.get("/approvals")
    def list_approvals(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        return service.storage.list_approvals(limit)

    @app.get("/candidates")
    def candidates(limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
        return service.storage.list_trade_candidates(limit)

    return app
