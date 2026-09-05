from __future__ import annotations

from typing import Any

from providency.patterns import PatternEvidence, PatternMatcher, PatternMatchResult
from providency.storage import Storage
from providency.vector import CaptureDisposition, ChartCapture
from providency.vision import CandleDetector, VisionDetectionError


class EngineService:
    def __init__(self, storage: Storage, *, recover: bool = True) -> None:
        self.storage = storage
        self.storage.initialize()
        if recover:
            self.storage.recover_interrupted_session()

    def snapshot(self) -> dict[str, Any]:
        session = self.storage.running_session()
        return {
            "engine_state": "RUNNING" if session else "STOPPED",
            "session": session,
        }

    def run(self) -> dict[str, Any]:
        return self.storage.start_session()

    def stop(self) -> dict[str, Any] | None:
        return self.storage.stop_session()


class PatternAnalysisService:
    def __init__(
        self,
        storage: Storage,
        matcher: PatternMatcher,
        detector: CandleDetector | None = None,
    ) -> None:
        self.storage = storage
        self.matcher = matcher
        self.detector = detector or CandleDetector()

    def analyze(self, capture: ChartCapture) -> PatternMatchResult:
        if (
            capture.disposition is not CaptureDisposition.USABLE
            or capture.path is None
            or capture.sha256 is None
        ):
            issue = capture.issue.value if capture.issue else "capture metadata is incomplete"
            return self.matcher.no_match(f"Visual analysis blocked: {issue}.")
        try:
            window = self.detector.detect(capture.path)
        except VisionDetectionError as exc:
            result = self.matcher.no_match(str(exc))
            self._persist(capture, result)
            return result

        evidence = PatternEvidence(
            screenshot_sha256=capture.sha256,
            screenshot_path=str(capture.path),
            image_width=window.image_width,
            image_height=window.image_height,
            candles=window.candles,
        )
        sequence_size = self.matcher.package.sequence_candles
        context_size = self.matcher.package.preceding_candles
        selected = window.candles[-(sequence_size + context_size) :]
        if len(selected) >= context_size + 1:
            context = selected[:context_size]
            sequence = selected[context_size:]
        else:
            context = ()
            sequence = selected[-sequence_size:]
        result = self.matcher.evaluate(
            [item.candle for item in sequence],
            [item.candle.close for item in context],
            evidence=evidence,
        )
        self._persist(capture, result)
        return result

    def _persist(self, capture: ChartCapture, result: PatternMatchResult) -> None:
        assert capture.sha256 is not None
        assert capture.path is not None
        session = self.storage.running_session()
        self.storage.record_pattern_detection(
            session_id=str(session["id"]) if session else None,
            screenshot_sha256=capture.sha256,
            screenshot_path=str(capture.path),
            pattern_id=result.pattern_id,
            pattern_version=result.pattern_version,
            result=result.status.value,
            reason=result.reason,
            evidence=result.to_dict(),
        )
        self.storage.record_event(
            session_id=str(session["id"]) if session else None,
            level="INFO" if result.status.value == "MATCH" else "WARNING",
            component="pattern",
            event_type=f"PATTERN_{result.status.value}",
            message=f"{result.pattern_id}: {result.reason}",
            details={
                "screenshot_sha256": capture.sha256,
                "pattern_id": result.pattern_id,
                "pattern_version": result.pattern_version,
                "result": result.status.value,
                "reason": result.reason,
            },
        )
