# ruff: noqa: E501
from __future__ import annotations

import asyncio
import html
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast
from uuid import uuid4

from providency.catalog import PatternCatalog, PatternCatalogError
from providency.config import ExecutionMode, RuntimeMode
from providency.patterns import PatternEvidence, PatternMatcher, PatternStatus
from providency.schedule import CaptureInterval, ScheduleError
from providency.storage import Storage
from providency.telegram import TelegramClientContract, TelegramError
from providency.vector import CaptureDisposition, ChartCapture, VectorAdapterContract
from providency.vision import CandleDetector, VisionDetectionError


class ObservationPhase(StrEnum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    CAPTURING = "CAPTURING"
    ANALYZING = "ANALYZING"
    NOTIFYING = "NOTIFYING"
    WAITING = "WAITING"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


class CycleStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ObservationConfiguration:
    interval: CaptureInterval = CaptureInterval()
    charts: tuple[dict[str, Any], ...] = ()
    enabled_pattern_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "interval": self.interval.to_dict(),
            "charts": [dict(chart) for chart in self.charts],
            "enabled_pattern_ids": list(self.enabled_pattern_ids),
        }

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> ObservationConfiguration:
        allowed = {"interval", "charts", "enabled_pattern_ids"}
        unknown = set(value) - allowed
        if unknown:
            raise ScheduleError(
                f"Campos desconhecidos na observação: {', '.join(sorted(unknown))}."
            )
        charts = value.get("charts", ())
        if not isinstance(charts, (list, tuple)):
            raise ScheduleError("Os gráficos confirmados precisam ser uma lista.")
        normalized: list[dict[str, Any]] = []
        for chart in charts:
            if not isinstance(chart, dict) or not chart.get("id"):
                raise ScheduleError("Cada gráfico confirmado precisa de um id estável.")
            normalized.append({str(key): item for key, item in chart.items()})
        pattern_ids = value.get("enabled_pattern_ids", ())
        if not isinstance(pattern_ids, (list, tuple)):
            raise ScheduleError("enabled_pattern_ids precisa ser uma lista.")
        return cls(
            interval=CaptureInterval.from_mapping(dict(value.get("interval", {}))),
            charts=tuple(normalized),
            enabled_pattern_ids=tuple(str(item) for item in pattern_ids),
        )


@dataclass(frozen=True, slots=True)
class ObservationResult:
    chart_id: str
    symbol: str | None
    timeframe: str | None
    capture: str
    analysis: str
    pattern_results: tuple[dict[str, Any], ...]
    reason: str
    evidence: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chart_id": self.chart_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "capture": self.capture,
            "analysis": self.analysis,
            "pattern_results": [dict(item) for item in self.pattern_results],
            "reason": self.reason,
            "evidence": self.evidence,
        }


def _now() -> datetime:
    return datetime.now(UTC)


class ObservationCoordinator:
    """Owns the informational monitor and never calls a financial service."""

    def __init__(
        self,
        storage: Storage,
        adapter: VectorAdapterContract,
        catalog: PatternCatalog,
        telegram: TelegramClientContract | None = None,
        *,
        execution_mode: ExecutionMode | RuntimeMode = ExecutionMode.DRY_RUN,
        clock: Callable[[], datetime] = _now,
        analyzer: Callable[[ChartCapture, Sequence[Any]], Awaitable[tuple[dict[str, Any], ...]]]
        | None = None,
        telegram_chat_id: int | None = None,
    ) -> None:
        self.storage = storage
        self.adapter = adapter
        self.catalog = catalog
        self.telegram = telegram
        self.execution_mode = execution_mode
        self.clock = clock
        self.analyzer = analyzer
        self.telegram_chat_id = telegram_chat_id
        self.detector = CandleDetector()
        self.configuration = ObservationConfiguration(
            enabled_pattern_ids=tuple(item.package.id for item in catalog.enabled)
        )
        stored = storage.observation_configuration()
        if stored is not None:
            self.configuration = ObservationConfiguration.from_mapping(stored)
        self.phase = ObservationPhase.STOPPED
        self.session_id: str | None = None
        self.current_cycle_id: str | None = None
        self.last_cycle: dict[str, Any] | None = None
        self.next_capture_at: datetime | None = None
        self.error: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self.storage.recover_interrupted_observation_session()

    def state(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "phase": self.phase.value,
            "current_cycle_id": self.current_cycle_id,
            "last_cycle": self.last_cycle,
            "next_capture_at": self.next_capture_at.isoformat() if self.next_capture_at else None,
            "timezone": self.configuration.interval.timezone,
            "configuration": self.configuration.to_dict(),
            "error": self.error,
        }

    def configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.phase not in {ObservationPhase.STOPPED, ObservationPhase.ERROR}:
            raise RuntimeError("Pare o monitor antes de alterar o intervalo.")
        configuration = ObservationConfiguration.from_mapping(payload)
        self._validate_configuration(configuration)
        self.configuration = configuration
        self.storage.save_observation_configuration(configuration.to_dict())
        return self.state()

    def _validate_configuration(self, configuration: ObservationConfiguration) -> None:
        self.catalog.validate_enabled()
        enabled = {item.package.id for item in self.catalog.enabled}
        requested = set(configuration.enabled_pattern_ids) or enabled
        unknown = requested - enabled
        if unknown:
            raise PatternCatalogError(
                f"Padrões habilitados inválidos: {', '.join(sorted(unknown))}."
            )
        if configuration.charts and len(
            {str(chart["id"]) for chart in configuration.charts}
        ) != len(configuration.charts):
            raise ValueError("Gráficos duplicados não podem iniciar a observação.")

    async def start(self) -> dict[str, Any]:
        async with self._lock:
            if self.phase not in {ObservationPhase.STOPPED, ObservationPhase.ERROR}:
                return self.state()
            if self.execution_mode is ExecutionMode.DEMO:
                raise RuntimeError("O modo DEMO não é permitido na observação informativa.")
            self._validate_configuration(self.configuration)
            if not self.configuration.charts:
                raise RuntimeError("Confirme pelo menos um gráfico no onboarding.")
            if (
                self.storage.open_operations()
                or self.storage.filled_operations_requiring_recovery()
            ):
                raise RuntimeError("Resolva a recuperação financeira pendente antes da observação.")
            if self.telegram is not None:
                self._chat_id()
            self._stop = asyncio.Event()
            self.session_id = self.storage.start_observation_session(self.configuration.to_dict())
            self.phase = ObservationPhase.STARTING
            self.error = None
            self.last_cycle = None
            self._task = asyncio.create_task(self._run())
            return self.state()

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            if self.phase is ObservationPhase.STOPPED:
                return self.state()
            self.phase = ObservationPhase.STOPPING
            self._stop.set()
            task = self._task
        if task is not None and task is not asyncio.current_task():
            try:
                await asyncio.wait_for(task, timeout=25)
            except TimeoutError:
                self.error = (
                    "A captura ou entrega ainda está em voo; nenhuma nova captura será iniciada."
                )
        async with self._lock:
            if self.session_id:
                self.storage.stop_observation_session(self.session_id, self.error)
            self.phase = ObservationPhase.STOPPED
            self._task = None
            self.next_capture_at = None
            return self.state()

    async def close(self) -> None:
        await self.stop()

    async def run_cycle_once(self) -> dict[str, Any]:
        if self.session_id is None:
            self.session_id = self.storage.start_observation_session(self.configuration.to_dict())
        cycle_id = str(uuid4())
        self.current_cycle_id = cycle_id
        started = self.clock()
        self.storage.record_observation_cycle(
            cycle_id,
            self.session_id,
            started,
            CycleStatus.CANCELLED.value,
            "",
            len(self.configuration.charts),
        )
        results: list[ObservationResult] = []
        usable = 0
        failed = 0
        for chart in self.configuration.charts:
            if self._stop.is_set():
                break
            chart_id = str(chart["id"])
            capture: ChartCapture | None = None
            try:
                self.phase = ObservationPhase.CAPTURING
                capture = await self._capture(chart_id)
                result = await self._analyze(chart_id, chart, capture)
            except Exception as exc:  # each chart is isolated; the cycle remains auditable
                failed += 1
                result = ObservationResult(
                    chart_id,
                    chart.get("symbol"),
                    chart.get("timeframe"),
                    "FAILED",
                    "ANALYSIS_ERROR",
                    (),
                    str(exc),
                )
            else:
                if result.capture == CaptureDisposition.USABLE.value:
                    usable += 1
                if result.analysis == "ANALYSIS_ERROR" or result.capture == "FAILED":
                    failed += 1
            if capture is not None:
                result = replace(result, evidence=capture.to_dict())
            results.append(result)
            self.storage.record_observation_result(cycle_id, result.to_dict())
        status = (
            CycleStatus.CANCELLED
            if self._stop.is_set()
            else CycleStatus.FAILED
            if failed == len(results)
            else CycleStatus.PARTIAL
            if failed
            else CycleStatus.COMPLETE
        )
        summary = self._summary(cycle_id, started, results, status)
        self.storage.record_observation_cycle(
            cycle_id, self.session_id, started, status.value, summary, len(results)
        )
        if self.telegram is not None and self.configuration.charts:
            self.phase = ObservationPhase.NOTIFYING
            await self._notify(cycle_id, summary)
        self.last_cycle = {
            "id": cycle_id,
            "status": status.value,
            "results": [item.to_dict() for item in results],
            "summary": summary,
        }
        self.current_cycle_id = None
        return self.last_cycle

    async def _run(self) -> None:
        try:
            anchor = self.clock()
            await self.run_cycle_once()
            while not self._stop.is_set():
                if self.last_cycle and self.last_cycle["status"] == CycleStatus.FAILED.value:
                    raise RuntimeError(
                        "Nenhum gráfico pôde ser analisado. Confira a conexão e refaça a preparação antes de iniciar."
                    )
                self.phase = ObservationPhase.WAITING
                self.next_capture_at = self.configuration.interval.next_at(
                    anchor, after=self.clock()
                )
                delay = max(0.0, (self.next_capture_at - self.clock()).total_seconds())
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except TimeoutError:
                    await self.run_cycle_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = str(exc)
            self.phase = ObservationPhase.ERROR
            if self.session_id:
                self.storage.record_observation_event(self.session_id, "ERROR", self.error)

    async def _capture(self, chart_id: str) -> ChartCapture:
        capture_chart = getattr(self.adapter, "capture_chart", None)
        if callable(capture_chart):
            return cast(ChartCapture, await capture_chart(chart_id))
        return await self.adapter.capture_primary_chart()

    async def _analyze(
        self, chart_id: str, chart: dict[str, Any], capture: ChartCapture
    ) -> ObservationResult:
        if capture.disposition is CaptureDisposition.USABLE and any(
            chart.get(key) and chart[key] != getattr(capture, key)
            for key in ("symbol", "timeframe")
        ):
            return ObservationResult(
                chart_id,
                chart.get("symbol"),
                chart.get("timeframe"),
                "NO_DECISION",
                "NO_DECISION",
                (),
                "O gráfico mudou; refaça a preparação.",
            )
        if capture.disposition is not CaptureDisposition.USABLE:
            return ObservationResult(
                chart_id,
                capture.symbol,
                capture.timeframe,
                capture.disposition.value,
                "NO_DECISION",
                (),
                capture.issue.value if capture.issue else "Captura ambígua",
            )
        self.phase = ObservationPhase.ANALYZING
        if self.analyzer is not None:
            patterns = await self.analyzer(capture, self.catalog.enabled)
        else:
            assert capture.path is not None
            try:
                window = await asyncio.to_thread(self.detector.detect, capture.path)
            except VisionDetectionError as exc:
                return ObservationResult(
                    chart_id,
                    capture.symbol,
                    capture.timeframe,
                    capture.disposition.value,
                    "ANALYSIS_ERROR",
                    (),
                    str(exc),
                )
            patterns_list: list[dict[str, Any]] = []
            active_ids = set(self.configuration.enabled_pattern_ids) or {
                item.package.id for item in self.catalog.enabled
            }
            for item in (item for item in self.catalog.enabled if item.package.id in active_ids):
                package = item.package
                matcher = PatternMatcher(package)
                # The rightmost candle may still be forming. A visible successor is
                # required for BAR_CLOSE; never confirm the final candle from a clock.
                candidates = (
                    window.candles[:-1]
                    if package.confirmation_mode == "BAR_CLOSE"
                    else window.candles
                )
                selected = candidates[-(package.sequence_candles + package.preceding_candles) :]
                context = selected[: package.preceding_candles]
                sequence = selected[package.preceding_candles :]
                evidence = PatternEvidence(
                    screenshot_sha256=str(capture.sha256),
                    screenshot_path=str(capture.path),
                    image_width=window.image_width,
                    image_height=window.image_height,
                    candles=window.candles,
                )
                result = matcher.evaluate(
                    [entry.candle for entry in sequence],
                    [entry.candle.close for entry in context],
                    evidence=evidence,
                )
                patterns_list.append(
                    {
                        "pattern_id": result.pattern_id,
                        "pattern_version": result.pattern_version,
                        "display_name_pt": package.display_name_pt,
                        "status": (
                            PatternStatus.FORMING.value
                            if result.status is PatternStatus.MATCH
                            and package.confirmation_mode == "BAR_CLOSE"
                            and not capture.live_candle_visible
                            else result.status.value
                        ),
                        "reason": result.reason,
                    }
                )
            patterns = tuple(patterns_list)
        return ObservationResult(
            chart_id,
            capture.symbol or chart.get("symbol"),
            capture.timeframe or chart.get("timeframe"),
            capture.disposition.value,
            "USABLE",
            tuple(patterns),
            "Leitura concluída.",
        )

    async def _notify(self, cycle_id: str, text: str) -> None:
        sender = getattr(self.telegram, "send_observation_summary", None)
        if not callable(sender):
            raise TelegramError("O cliente Telegram não implementa mensagens informativas.")
        outbox_id = self.storage.create_observation_outbox(cycle_id, text)
        self.storage.mark_observation_outbox_sending(outbox_id)
        try:
            delivered = await sender(chat_id=self._chat_id(), text=text)
        except Exception as exc:
            status = "UNKNOWN_DELIVERY" if getattr(exc, "ambiguous_delivery", False) else "FAILED"
            self.storage.finish_observation_outbox(outbox_id, status, error=str(exc))
            self.phase = ObservationPhase.ERROR
            raise
        self.storage.finish_observation_outbox(outbox_id, "SENT", response=delivered)

    def _chat_id(self) -> int:
        configuration = self.storage.telegram_configuration() or {}
        chat_id = configuration.get("chat_id", self.telegram_chat_id)
        if type(chat_id) is not int or chat_id == 0:
            raise TelegramError("Configure um chat Telegram explícito antes de iniciar.")
        return chat_id

    @staticmethod
    def _summary(
        cycle_id: str, started: datetime, results: Sequence[ObservationResult], status: CycleStatus
    ) -> str:
        lines = [
            f"Providency — ciclo {cycle_id[:8]}, {started.astimezone().strftime('%d/%m/%Y %H:%M')}."
        ]
        for item in results:
            name = html.escape(str(item.symbol or item.chart_id))
            if item.timeframe:
                name += f" · {html.escape(item.timeframe)}"
            if item.analysis == "ANALYSIS_ERROR":
                lines.append(f"{name}: não pôde ser analisado ({html.escape(item.reason)}).")
                continue
            matches = [
                str(pattern.get("display_name_pt", pattern.get("pattern_id", "padrão")))
                for pattern in item.pattern_results
                if pattern.get("status") == PatternStatus.MATCH.value
            ]
            if matches:
                lines.append(f"{name}: {', '.join(matches)} identificado(s).")
            elif item.analysis == "NO_DECISION":
                lines.append(f"{name}: leitura indisponível ({html.escape(item.reason)}).")
            elif any(
                pattern.get("status") == PatternStatus.FORMING.value
                for pattern in item.pattern_results
            ):
                lines.append(
                    f"{name}: nenhum padrão confirmado; formação ou fechamento ainda não confirmado."
                )
            else:
                lines.append(f"{name}: nenhum padrão identificado.")
        if status is CycleStatus.PARTIAL:
            lines.append("Análise incompleta: consulte o log para os gráficos sem leitura.")
        return " ".join(lines)
