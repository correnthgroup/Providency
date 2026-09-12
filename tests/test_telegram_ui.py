import io
import json
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from providency.api import create_app
from providency.config import Settings
from providency.telegram import TelegramClient


def test_operator_can_save_token_discover_and_confirm_without_environment_variables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROVIDENCY_LAUNCH_ID", raising=False)
    saved = []
    monkeypatch.setattr("keyring.set_password", lambda *args: saved.append(args))
    telegram = AsyncMock(spec=TelegramClient)
    telegram.discover_destinations.return_value = [
        dict(chat_id=-123, user_id=456, chat_name="Group", user_name="Operator")
    ]
    telegram.health_check.return_value = dict(state="READY", bot_username="providency_bot")
    settings = Settings(data_dir=tmp_path)
    with TestClient(
        create_app(settings, telegram_client=telegram, telegram_polling=False)
    ) as client:

        def local_request(request: object, **_kwargs: object) -> io.BytesIO:
            response = client.request(
                request.get_method(),
                urlsplit(request.full_url).path,
                json=json.loads(request.data) if request.data else None,
            )
            response.raise_for_status()
            return io.BytesIO(response.content)

        monkeypatch.setattr("urllib.request.urlopen", local_request)
        ui = AppTest.from_file(
            str(Path(__file__).parents[1] / "src/providency/ui.py"), default_timeout=15
        ).run()
        ui.radio[0].set_value("Configurações").run()
        assert not ui.exception
        next(field for field in ui.text_input if field.label == "Token do bot").set_value(
            "123456:fixture-token-only-not-real"
        )
        next(b for b in ui.button if b.label == "Salvar token no cofre").click().run()
        assert saved == [("Providency", "telegram-bot-token", "123456:fixture-token-only-not-real")]
        assert next(f for f in ui.text_input if f.label == "Token do bot").value == ""
        next(b for b in ui.button if b.label == "Testar bot").click().run()
        assert any("@providency_bot" in s.value for s in ui.success)
        next(b for b in ui.button if b.label == "Identificar grupo e usuário").click().run()
        assert client.get("/telegram/status").json()["configuration"]["chat_id"] == 0
        next(b for b in ui.button if b.label == "Confirmar destino e aprovador").click().run()
        assert not ui.exception
        assert client.get("/telegram/status").json()["configuration"]["user_id"] == 456
        assert b"fixture-token-only" not in settings.database_path.read_bytes()


def test_telegram_timeout_is_not_reported_as_engine_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROVIDENCY_LAUNCH_ID", raising=False)
    requested_timeouts = []
    with TestClient(create_app(Settings(data_dir=tmp_path), telegram_polling=False)) as client:

        def local_request(request: object, **kwargs: object) -> io.BytesIO:
            path = urlsplit(request.full_url).path
            if path == "/telegram/health":
                requested_timeouts.append(kwargs["timeout"])
                raise TimeoutError("fixture timeout")
            response = client.request(
                request.get_method(), path, json=json.loads(request.data) if request.data else None
            )
            return io.BytesIO(response.content)

        monkeypatch.setattr("urllib.request.urlopen", local_request)
        ui = AppTest.from_file(
            str(Path(__file__).parents[1] / "src/providency/ui.py"), default_timeout=15
        ).run()
        ui.radio[0].set_value("Configurações").run()
        next(b for b in ui.button if b.label == "Testar bot").click().run()
        assert not ui.exception
        assert requested_timeouts[0] > 25
        assert any("demorou" in error.value for error in ui.error)
        assert not any("Core Engine não está disponível" in error.value for error in ui.error)
        assert client.get("/health").status_code == 200


def test_temporary_token_button_does_not_write_to_keyring(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PROVIDENCY_LAUNCH_ID", raising=False)
    writes = []
    monkeypatch.setattr("keyring.set_password", lambda *args: writes.append(args))
    monkeypatch.setattr("keyring.get_password", lambda *args: None)
    telegram = TelegramClient()
    settings = Settings(data_dir=tmp_path)
    with TestClient(
        create_app(settings, telegram_client=telegram, telegram_polling=False)
    ) as client:

        def local_request(request, **kwargs):
            response = client.request(
                request.get_method(),
                urlsplit(request.full_url).path,
                json=json.loads(request.data) if request.data else None,
            )
            response.raise_for_status()
            return io.BytesIO(response.content)

        monkeypatch.setattr("urllib.request.urlopen", local_request)
        ui = AppTest.from_file(
            str(Path(__file__).parents[1] / "src/providency/ui.py"), default_timeout=15
        ).run()
        ui.radio[0].set_value("Configurações").run()
        token = "123456:temporary-ui-test-only-not-real"
        next(f for f in ui.text_input if f.label == "Token do bot").set_value(token)
        next(b for b in ui.button if b.label == "Usar somente nesta sessão").click().run()
        assert not ui.exception
        assert writes == []
        assert telegram._token() == token
        assert next(f for f in ui.text_input if f.label == "Token do bot").value == ""
        assert client.get("/telegram/credential-status").json()["session_only"]
        assert token.encode() not in settings.database_path.read_bytes()


def test_invalid_token_stays_in_field_for_correction(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PROVIDENCY_LAUNCH_ID", raising=False)
    writes = []
    monkeypatch.setattr("keyring.set_password", lambda *args: writes.append(args))
    with TestClient(create_app(Settings(data_dir=tmp_path), telegram_polling=False)) as client:

        def local_request(request, **kwargs):
            response = client.request(
                request.get_method(),
                urlsplit(request.full_url).path,
                json=json.loads(request.data) if request.data else None,
            )
            response.raise_for_status()
            return io.BytesIO(response.content)

        monkeypatch.setattr("urllib.request.urlopen", local_request)
        ui = AppTest.from_file(
            str(Path(__file__).parents[1] / "src/providency/ui.py"),
            default_timeout=15,
        ).run()
        ui.radio[0].set_value("Configurações").run()
        token = "invalid-token-for-ui-regression"
        next(field for field in ui.text_input if field.label == "Token do bot").set_value(token)
        next(
            button for button in ui.button if button.label == "Usar somente nesta sessão"
        ).click().run()

        assert not ui.exception
        assert writes == []
        assert (
            next(field for field in ui.text_input if field.label == "Token do bot").value == token
        )
        assert any("Token inválido" in error.value for error in ui.error)
