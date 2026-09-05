from pathlib import Path

from fastapi.testclient import TestClient

from providency.api import create_app
from providency.config import Settings


def test_run_stop_and_activity_api(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path), recover=False)

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/state").json() == {"engine_state": "STOPPED", "session": None}

        started = client.post("/run").json()
        assert started["status"] == "RUNNING"
        assert client.get("/state").json()["engine_state"] == "RUNNING"

        stopped = client.post("/stop").json()
        assert stopped["id"] == started["id"]
        assert stopped["status"] == "STOPPED"

        events = client.get("/events").json()
        assert [event["event_type"] for event in events] == [
            "SESSION_STOPPED",
            "SESSION_STARTED",
        ]
