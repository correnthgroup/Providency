from pathlib import Path

from fastapi.testclient import TestClient
from synthetic_chart import bearish_engulfing_chart

from providency.api import create_app
from providency.config import AnalysisConfiguration, Settings
from providency.context import PriceAnchor
from providency.vector import (
    AppliedVectorState,
    CaptureDisposition,
    CaptureRegion,
    ChartCapture,
    DesiredVectorState,
    StateSyncResult,
)

ROOT = Path(__file__).parents[1]


def settings(tmp_path: Path, configuration: AnalysisConfiguration | None = None) -> Settings:
    return Settings(
        data_dir=tmp_path,
        patterns_dir=ROOT / "patterns",
        analysis_configuration=configuration,
    )


class FakeVectorAdapter:
    def __init__(self, capture_path: Path) -> None:
        self.capture_path = capture_path
        self.stopped = False

    async def health_check(self) -> dict[str, str]:
        return {"state": "OPEN"}

    async def open_vector(self) -> dict[str, str]:
        return {"state": "WAITING_FOR_MANUAL_LOGIN", "url": "https://vector.example/app"}

    async def capture_primary_chart(self) -> ChartCapture:
        if not self.capture_path.exists():
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

    async def sync_analysis_state(self, desired: DesiredVectorState) -> StateSyncResult:
        applied = AppliedVectorState(
            desired.symbol,
            desired.timeframe,
            {period: float(period) for period in desired.moving_average_periods},
            (),
            (
                PriceAnchor(0, 110, "DOM_GEOMETRY"),
                PriceAnchor(100, 60, "DOM_GEOMETRY"),
            ),
        )
        return StateSyncResult(desired, applied, applied)

    async def capture_primary_context(
        self, desired: DesiredVectorState, context_timeframe: str
    ) -> tuple[ChartCapture, ChartCapture, StateSyncResult]:
        primary = await self.capture_primary_chart()
        context = await self.capture_primary_chart()
        applied = AppliedVectorState(
            desired.symbol,
            desired.timeframe,
            {period: float(period) for period in desired.moving_average_periods},
            (),
            (
                PriceAnchor(0, 110, "DOM_GEOMETRY"),
                PriceAnchor(100, 60, "DOM_GEOMETRY"),
            ),
        )
        return primary, context, StateSyncResult(desired, applied, applied)

    async def stop(self) -> None:
        self.stopped = True


def test_run_stop_and_activity_api(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), recover=False)

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
    app = create_app(settings(tmp_path), recover=False, vector_adapter=adapter)

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


def test_pattern_analysis_api_returns_visual_evidence_and_persists_it(tmp_path: Path) -> None:
    capture_path = bearish_engulfing_chart(tmp_path / "capture.webp")
    adapter = FakeVectorAdapter(capture_path)

    async def valid_capture() -> ChartCapture:
        return ChartCapture(
            disposition=CaptureDisposition.USABLE,
            captured_at="2026-09-05T12:30:00+00:00",
            symbol="BTCUSD",
            timeframe="15m",
            region=CaptureRegion(x=1, y=2, width=320, height=200),
            path=capture_path,
            sha256="b" * 64,
        )

    adapter.capture_primary_chart = valid_capture  # type: ignore[method-assign]
    app = create_app(settings(tmp_path), recover=False, vector_adapter=adapter)

    with TestClient(app) as client:
        result = client.post("/pattern/analyze").json()
        assert result["status"] == "MATCH"
        assert result["pattern_id"] == "bearish_engulfing"
        assert result["pattern_version"] == "0.1.0"
        assert len(result["evidence"]["candles"]) == 5
        assert len(result["measures"]) == 2
        assert result["reason"]

        persisted = client.get("/pattern/detections").json()
        assert persisted[0]["screenshot_sha256"] == "b" * 64
        assert persisted[0]["result"] == "MATCH"


def test_candidate_api_exposes_complete_auditable_preflight(tmp_path: Path) -> None:
    configuration = AnalysisConfiguration(
        symbol="BTC/BRL",
        primary_timeframe="15min",
        context_timeframe="1h",
        short_ma_period=7,
        long_ma_period=70,
        quantity=2,
        tick_size=0.5,
        tick_value=1.0,
        stop_buffer_ticks=1,
        min_rr=2,
        max_trades=3,
        max_consecutive_losses=2,
        max_session_loss=100,
        support_resistance_tolerance=5,
    )
    adapter = FakeVectorAdapter(tmp_path / "capture.webp")
    bearish_engulfing_chart(adapter.capture_path)
    app = create_app(settings(tmp_path, configuration), recover=False, vector_adapter=adapter)

    with TestClient(app) as client:
        client.post("/run")
        sync = client.post("/vector/sync")
        assert sync.status_code == 200
        capture_pair = client.post("/vector/capture-analysis").json()
        response = client.post(
            "/candidate/evaluate",
            json={
                "analysis_capture_id": capture_pair["analysis_capture_id"],
                "pattern_detection_id": capture_pair["pattern_detection_id"],
            },
        )

        assert response.status_code == 200
        candidate = response.json()
        assert candidate["decision"] in {"ALLOWED", "BLOCKED"}
        assert candidate["entry"] is not None
        assert candidate["stop"] is not None
        assert candidate["quantity"] == 2
        assert candidate["configuration_snapshot"]["version"] == configuration.version
        assert candidate["applied_state_id"] == capture_pair["applied_state_id"]
        assert client.get("/candidates").json()[0]["candidate_id"] == candidate["candidate_id"]
