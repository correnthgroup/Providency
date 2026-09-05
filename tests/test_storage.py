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
