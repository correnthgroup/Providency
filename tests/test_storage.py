from pathlib import Path

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
