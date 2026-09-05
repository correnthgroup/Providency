from pathlib import Path

import pytest

from providency.service import EngineService
from providency.storage import Storage


def test_session_lifecycle_and_wal(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    service = EngineService(storage, recover=False)

    with storage.connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    session = service.run()
    assert session["status"] == "RUNNING"
    assert service.run()["id"] == session["id"]
    assert service.snapshot()["engine_state"] == "RUNNING"

    stopped = service.stop()
    assert stopped is not None
    assert stopped["status"] == "STOPPED"
    assert service.stop() is None
    assert service.snapshot()["engine_state"] == "STOPPED"
    assert [event["event_type"] for event in storage.list_events()] == [
        "SESSION_STOPPED",
        "SESSION_STARTED",
    ]


def test_restart_marks_running_session_as_interrupted(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    first_service = EngineService(storage, recover=False)
    session = first_service.run()

    restarted_service = EngineService(storage, recover=True)

    assert restarted_service.snapshot()["engine_state"] == "STOPPED"
    events = storage.list_events()
    assert events[0]["event_type"] == "SESSION_RECOVERED"
    assert events[0]["session_id"] == session["id"]


def test_pattern_detection_persists_evidence_without_image_blob(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()

    detection_id = storage.record_pattern_detection(
        session_id=None,
        screenshot_sha256="a" * 64,
        screenshot_path=str(tmp_path / "capture.webp"),
        pattern_id="bearish_engulfing",
        pattern_version="0.1.0",
        result="MATCH",
        reason="All rules passed.",
        evidence={"candles": [{"open": 1, "high": 2, "low": 0, "close": 1.5}]},
    )

    detection = storage.list_pattern_detections()[0]
    assert detection["id"] == detection_id
    assert detection["screenshot_sha256"] == "a" * 64
    assert detection["pattern_version"] == "0.1.0"
    assert detection["result"] == "MATCH"
    assert detection["evidence"]["candles"][0]["close"] == 1.5
    with storage.connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(pattern_detections)")}
    assert "image_blob" not in columns


def test_configuration_applied_state_and_candidate_are_auditable(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    session = storage.start_session()
    desired = {"version": "config-1", "symbol": "BTC/BRL"}
    storage.record_configuration("config-1", desired)
    storage.record_applied_state(
        configuration_version="config-1",
        matches_desired=False,
        state={"missing_fields": ["timeframe"]},
    )
    candidate = {
        "candidate_id": "candidate-1",
        "session_id": session["id"],
        "decision": "BLOCKED",
        "blocking_reasons": ["timeframe is missing"],
    }

    storage.record_trade_candidate(
        candidate, max_trades=1, max_consecutive_losses=1, max_session_loss=1
    )
    storage.record_trade_candidate(
        candidate, max_trades=1, max_consecutive_losses=1, max_session_loss=1
    )

    assert storage.latest_configuration()["desired"] == desired
    assert not storage.latest_applied_state()["matches_desired"]
    assert storage.list_trade_candidates() == [candidate]


def test_allowed_candidate_limit_is_transactional(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    session = storage.start_session()
    first = {
        "candidate_id": "candidate-1",
        "session_id": session["id"],
        "decision": "ALLOWED",
    }
    second = {**first, "candidate_id": "candidate-2"}

    storage.record_trade_candidate(
        first, max_trades=1, max_consecutive_losses=1, max_session_loss=1
    )
    with storage.connect() as connection:
        connection.execute(
            "UPDATE session_risk_state SET trades = 1 WHERE session_id = ?", (session["id"],)
        )
        connection.commit()
    with pytest.raises(ValueError, match="transactionally"):
        storage.record_trade_candidate(
            second, max_trades=1, max_consecutive_losses=1, max_session_loss=1
        )
