from pathlib import Path

from fastapi.testclient import TestClient

from providency.api import create_app
from providency.config import Settings
from providency.telegram import TelegramClient


class NoCredentials:
    def get_password(self, service: str, username: str) -> None:
        return None


def test_temporary_token_is_not_persisted_or_returned_and_clears_on_shutdown(
    tmp_path: Path,
) -> None:
    token = "123456:temporary-test-only-not-real"
    telegram = TelegramClient(NoCredentials())
    settings = Settings(data_dir=tmp_path)
    with TestClient(
        create_app(settings, telegram_client=telegram, telegram_polling=False)
    ) as client:
        response = client.put("/telegram/session-token", json={"token": token})
        assert response.status_code == 200
        assert response.json() == {"session_only": True}
        assert token not in response.text
        assert telegram._token() == token
        status = client.get("/telegram/credential-status").json()
        assert status["session_only"] and status["valid_format"] and not status["saved"]
        assert client.delete("/telegram/session-token").json() == {"session_only": False}
        assert not client.get("/telegram/credential-status").json()["valid_format"]
        client.put("/telegram/session-token", json={"token": token})
    assert telegram._session_token is None
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert token.encode() not in path.read_bytes(), path


def test_session_token_rejects_external_origin_invalid_format_and_running_session(
    tmp_path: Path,
) -> None:
    telegram = TelegramClient(NoCredentials())
    with TestClient(
        create_app(Settings(data_dir=tmp_path), telegram_client=telegram, telegram_polling=False)
    ) as client:
        payload = {"token": "123456:temporary-test-only-not-real"}
        headers = {"Origin": "https://external.example"}
        assert (
            client.put("/telegram/session-token", json=payload, headers=headers).status_code == 403
        )
        assert client.delete("/telegram/session-token", headers=headers).status_code == 403
        invalid = client.put("/telegram/session-token", json={"token": "invalid-secret"})
        assert invalid.status_code == 422
        assert "invalid-secret" not in invalid.text
        client.post("/run")
        assert client.put("/telegram/session-token", json=payload).status_code == 409
        assert client.delete("/telegram/session-token").status_code == 409
