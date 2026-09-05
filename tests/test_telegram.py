import json
import urllib.request
from typing import Any

import pytest

from providency.approvals import render_proposal
from providency.telegram import TelegramClient, TelegramError


class MemoryCredentials:
    def __init__(self, token: str | None) -> None:
        self.token = token

    def get_password(self, service: str, username: str) -> str | None:
        assert (service, username) == ("Providency", "telegram-bot-token")
        return self.token


def test_missing_token_fails_closed_without_environment_fallback() -> None:
    client = TelegramClient(MemoryCredentials(None))

    with pytest.raises(TelegramError, match="credential store"):
        client._token()


def test_credential_backend_failure_is_redacted() -> None:
    class BrokenCredentials:
        def get_password(self, service: str, username: str) -> str | None:
            raise RuntimeError("secret backend detail")

    client = TelegramClient(BrokenCredentials())

    with pytest.raises(TelegramError, match="unavailable") as raised:
        client._token()
    assert "secret backend detail" not in str(raised.value)


def test_rendered_proposal_uses_immutable_snapshot_and_contains_no_callback_secret() -> None:
    approval: dict[str, Any] = {
        "id": "approval-1",
        "expires_at": "2026-09-05T15:01:00+00:00",
        "candidate_snapshot": {
            "symbol": "BTC/BRL",
            "side": "SHORT",
            "quantity": 2,
            "entry": 100.0,
            "stop": 102.0,
            "risk_amount": 4.0,
            "reference_rr": 2.0,
            "confluence": {"passed_total": 5, "applicable_total": 5},
        },
    }

    message = render_proposal(approval)

    assert "BTC/BRL" in message
    assert "Confluence: 5/5" in message
    assert "opaque-token" not in message


@pytest.mark.asyncio
async def test_client_sends_only_message_and_opaque_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"ok": True, "result": {"message_id": 7}}).encode()

    def urlopen(request: urllib.request.Request, timeout: int) -> Response:
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data or b"{}")
        assert timeout == 15
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    client = TelegramClient(MemoryCredentials("secret-bot-token"))

    result = await client.send_proposal(
        chat_id=10, text="immutable", yes_callback="yes:opaque", no_callback="no:opaque"
    )

    assert result == {"message_id": 7, "chat_id": 10}
    assert captured["url"].endswith("/sendMessage")
    assert "secret-bot-token" not in json.dumps(captured["payload"])
    keyboard = captured["payload"]["reply_markup"]["inline_keyboard"]
    assert keyboard[0][0]["callback_data"] == "yes:opaque"
