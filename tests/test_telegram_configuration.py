from pathlib import Path
from threading import Event
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from providency.api import create_app
from providency.config import Settings
from providency.telegram import TelegramClient, TelegramPollBatch


def test_telegram_configuration_persists_and_applies_without_restart(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    payload = dict(
        chat_id=-123456, user_id=789, approval_ttl_seconds=90, recheck_price_tolerance_ticks=2
    )
    with TestClient(create_app(settings, telegram_polling=False)) as client:
        assert client.get("/telegram/status").json()["configuration"]["missing_fields"]
        response = client.put("/telegram/configuration", json=payload)
        assert response.status_code == 200
        assert response.json()["configuration"]["missing_fields"] == []
        assert response.json()["configuration"]["user_id"] == 789
        client.post("/run")
        assert client.put("/telegram/configuration", json=payload).status_code == 409
    with TestClient(create_app(settings, telegram_polling=False)) as client:
        saved = client.get("/telegram/status").json()["configuration"]
        assert saved["chat_id"] == -123456
        assert saved["approval_ttl_seconds"] == 90


def test_telegram_configuration_rejects_invalid_identity_and_external_origin(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path), telegram_polling=False)) as client:
        payload = dict(chat_id=-123, user_id=456)
        assert (
            client.put(
                "/telegram/configuration",
                json=payload,
                headers={"Origin": "https://external.example"},
            ).status_code
            == 403
        )
        for invalid in (
            {"chat_id": 0},
            {"user_id": 0},
            {"user_id": -1},
            {"approval_ttl_seconds": 0},
            {"token": "must-not-be-stored"},
        ):
            assert client.put("/telegram/configuration", json=payload | invalid).status_code == 422
        assert client.get("/telegram/status").json()["configuration"]["chat_id"] == 0


def test_polling_activates_after_first_configuration_without_engine_restart(tmp_path: Path) -> None:
    polled = Event()
    telegram = AsyncMock(spec=TelegramClient)

    async def poll(**kwargs: object) -> TelegramPollBatch:
        polled.set()
        return TelegramPollBatch((), None)

    telegram.poll.side_effect = poll
    with TestClient(create_app(Settings(data_dir=tmp_path), telegram_client=telegram)) as client:
        assert not client.get("/telegram/status").json()["polling"]
        assert (
            client.put("/telegram/configuration", json=dict(chat_id=-123, user_id=456)).status_code
            == 200
        )
        assert client.get("/telegram/status").json()["polling"]
        assert not polled.is_set()
        client.post("/run")
        assert polled.wait(3), "Newly configured Telegram worker did not begin polling"
