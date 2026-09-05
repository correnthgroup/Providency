from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from providency.approvals import ApprovalError, ApprovalService, render_proposal
from providency.config import ExecutionMode, Settings
from providency.context import (
    ConfluenceItem,
    ConfluenceResult,
    ConfluenceStatus,
    PriceAnchor,
    PriceScale,
    PriceScaleError,
    analyze_context,
)
from providency.execution import DemoExecutionService
from providency.patterns import Candle, PatternMatcher, PatternPackage
from providency.protection import PositionProtectionService
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


def _absolute_candles(
    evidence: dict[str, Any], scale: PriceScale
) -> list[Candle]:
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
) -> FastAPI:
    resolved = settings or Settings.from_env()
    service = EngineService(Storage(resolved.database_path), recover=recover)
    adapter = vector_adapter or VectorAdapter(resolved)
    matcher = PatternMatcher(
        PatternPackage.load(resolved.pattern_catalog_dir / "bearish_engulfing" / "pattern.yaml")
    )
    analysis = PatternAnalysisService(service.storage, matcher)
    configuration = resolved.trading_configuration
    telegram_configuration = resolved.telegram
    telegram = telegram_client or TelegramClient()
    approvals = ApprovalService(
        service.storage,
        chat_id=telegram_configuration.chat_id,
        user_id=telegram_configuration.user_id,
        ttl_seconds=telegram_configuration.approval_ttl_seconds,
        dry_run=resolved.execution_mode is ExecutionMode.DRY_RUN,
    )
    protection = PositionProtectionService(service.storage, adapter, mode=resolved.execution_mode)
    execution = DemoExecutionService(
        service.storage,
        adapter,
        mode=resolved.execution_mode,
        protection=protection,
    )
    telegram_offset: int | None = None
    telegram_poll_error = False
    stop_polling = asyncio.Event()
    service.storage.record_configuration(configuration.version, configuration.to_dict())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task: asyncio.Task[None] | None = None
        if telegram_polling and not telegram_configuration.missing_fields:
            task = asyncio.create_task(poll_worker())
        try:
            yield
        finally:
            stop_polling.set()
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await adapter.stop()

    app = FastAPI(title="Providency Core Engine", version="0.7.0", lifespan=lifespan)

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

    @app.get("/configuration")
    def configured_state() -> dict[str, Any]:
        return {
            "desired": configuration.to_dict(),
            "applied": service.storage.latest_applied_state(),
            "execution_mode": resolved.execution_mode.value,
        }

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
                str(current["id"])
                if (current := service.storage.running_session())
                else None
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
            ConfluenceStatus.PASS
            if detection["result"] == "MATCH"
            else ConfluenceStatus.FAIL,
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
                max(
                    float(item["box"]["y"]) + float(item["box"]["height"])
                    for item in formation
                )
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

    def recheck_differences(
        original: dict[str, Any], fresh: dict[str, Any]
    ) -> list[str]:
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
        if (
            resolved.execution_mode is ExecutionMode.DEMO
            and finished["status"] == "WOULD_EXECUTE"
        ):
            return await execution.execute_approved(finished)
        return finished

    async def process_callback(
        callback_data: str, *, chat_id: int, user_id: int
    ) -> dict[str, Any]:
        approval = approvals.consume(callback_data, chat_id=chat_id, user_id=user_id)
        if approval["status"] == "APPROVED":
            return await finish_approved_recheck(approval)
        return approval

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
            if service.storage.running_session() is None:
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
        }

    @app.get("/telegram/health")
    async def telegram_health() -> dict[str, Any]:
        if telegram_configuration.missing_fields:
            raise HTTPException(status_code=409, detail="Telegram configuration is incomplete.")
        try:
            return await telegram.health_check()
        except TelegramError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

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
