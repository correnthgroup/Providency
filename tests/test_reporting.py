from pathlib import Path

from providency.reporting import build_session_report, export_session_report
from providency.storage import Storage


def test_session_report_reconstructs_funnel_and_blocking_reasons(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    session = storage.start_session()
    detection_id = storage.record_pattern_detection(
        session_id=session["id"],
        screenshot_sha256="a" * 64,
        screenshot_path=str(tmp_path / "capture.webp"),
        pattern_id="bearish_engulfing",
        pattern_version="0.1.0",
        result="NO_MATCH",
        reason="Preceding closes are not ascending.",
        evidence={"candles": []},
    )
    storage.record_trade_candidate(
        {
            "candidate_id": "candidate-1",
            "session_id": session["id"],
            "pattern_detection_id": detection_id,
            "decision": "BLOCKED",
            "blocking_reasons": ["Pattern is not confirmed.", "Price scale is missing."],
        },
        max_trades=1,
        max_consecutive_losses=1,
        max_session_loss=1,
    )

    first = build_session_report(storage, session["id"])
    second = build_session_report(storage, session["id"])

    assert first == second
    assert first["funnel"]["detections"] == 1
    assert first["funnel"]["confirmed_patterns"] == 0
    assert first["funnel"]["candidates"] == 1
    assert first["funnel"]["approved"] == 0
    assert first["blocking_reasons"]["Pattern is not confirmed."] == 1
    exported = export_session_report(storage, session["id"], tmp_path / "exports")
    assert exported.is_file()
    assert exported.read_text(encoding="utf-8").endswith("\n")
