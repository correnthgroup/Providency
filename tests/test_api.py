from pathlib import Path

from fastapi.testclient import TestClient

from providency.api import create_app
from providency.config import Settings
from providency.vector import CaptureDisposition, CaptureRegion, ChartCapture


class FakeVectorAdapter:
    def __init__(self, capture_path: Path) -> None:
        self.capture_path = capture_path
        self.stopped = False

    async def health_check(self) -> dict[str, str]:
        return {"state": "OPEN"}

    async def open_vector(self) -> dict[str, str]:
        return {"state": "WAITING_FOR_MANUAL_LOGIN", "url": "https://vector.example/app"}

    async def capture_primary_chart(self) -> ChartCapture:
        self.capture_path.write_bytes(b"webp")
        return ChartCapture(
            disposition=CaptureDisposition.USABLE,
            captured_at="2026-09-05T12:30:00+00:00",
            symbol="BTCUSD",
            timeframe="15m",
            region=CaptureRegion(x=1, y=2, width=640, height=360),
            path=self.capture_path,
            sha256="a" * 64,
        )

    async def stop(self) -> None:
        self.stopped = True


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


def test_vector_open_capture_and_activity_api(tmp_path: Path) -> None:
    adapter = FakeVectorAdapter(tmp_path / "capture.webp")
    app = create_app(Settings(data_dir=tmp_path), recover=False, vector_adapter=adapter)

    with TestClient(app) as client:
        assert client.get("/vector/health").json() == {"state": "OPEN"}
        assert client.post("/vector/open").json()["state"] == "WAITING_FOR_MANUAL_LOGIN"

        capture = client.post("/vector/capture").json()
        assert capture["disposition"] == "USABLE"
        assert capture["symbol"] == "BTCUSD"
        assert capture["timeframe"] == "15m"

        event = client.get("/events").json()[0]
        assert event["event_type"] == "VECTOR_CAPTURE_USABLE"
        assert event["details"]["sha256"] == "a" * 64

    assert adapter.stopped
