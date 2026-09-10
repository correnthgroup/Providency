# ruff: noqa: E501
from pathlib import Path

from fastapi.testclient import TestClient

from providency.api import create_app
from providency.config import RuntimeMode, Settings, TelegramConfiguration
from providency.telegram import TelegramPollBatch
from providency.vector import CaptureDisposition, CaptureIssue, ChartCapture


class ObservationAdapter:
    async def health_check(self) -> dict[str, str]:
        return {"state": "OPEN"}

    async def capture_chart(self, chart_id: str) -> ChartCapture:
        return ChartCapture(
            CaptureDisposition.NO_DECISION,
            "2026-09-10T12:00:00+00:00",
            CaptureIssue.LOADING,
            chart_id=chart_id,
        )

    async def capture_primary_chart(self) -> ChartCapture:
        return await self.capture_chart("chart-1")

    async def stop(self) -> None:
        return None


class ObservationTelegram:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def health_check(self) -> dict[str, str]:
        return {"state": "READY"}

    async def discover_destinations(self) -> list[dict[str, object]]:
        return []

    async def send_observation_summary(self, *, chat_id: int, text: str) -> dict[str, object]:
        self.messages.append(text)
        return {"message_id": 1, "chat_id": chat_id}

    async def send_proposal(self, **_: object) -> dict[str, object]:
        return {"message_id": 1}

    async def poll(self, *, offset: int | None = None) -> TelegramPollBatch:
        return TelegramPollBatch((), offset)

    async def answer_callback(self, callback_query_id: str, text: str) -> None:
        return None


def test_observation_routes_are_idempotent_and_separate_from_finance(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            data_dir=tmp_path,
            patterns_dir=Path(__file__).parents[1] / "patterns",
            execution_mode=RuntimeMode.OBSERVATION_ONLY,
            telegram_configuration=TelegramConfiguration(chat_id=10, user_id=20),
        ),
        recover=False,
        vector_adapter=ObservationAdapter(),  # type: ignore[arg-type]
        telegram_client=ObservationTelegram(),  # type: ignore[arg-type]
        telegram_polling=False,
    )
    with TestClient(app) as client:
        catalog = client.get("/patterns/catalog")
        assert catalog.status_code == 200
        assert len(catalog.json()) == 6
        configured = client.put(
            "/observation/configuration",
            json={
                "interval": {"value": 5, "unit": "minutes", "timezone": "UTC"},
                "charts": [{"id": "chart-1", "symbol": "BTCUSDT", "timeframe": "15m"}],
                "enabled_pattern_ids": ["bearish_engulfing"],
            },
        )
        assert configured.status_code == 200
        first = client.post("/observation/start")
        second = client.post("/observation/start")
        assert first.status_code == second.status_code == 200
        assert first.json()["session_id"] == second.json()["session_id"]
        assert client.post("/run").status_code == 409
        stopped = client.post("/observation/stop")
        assert stopped.status_code == 200
        assert stopped.json()["phase"] == "STOPPED"
