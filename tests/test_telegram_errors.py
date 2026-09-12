import io
import urllib.error

import pytest

from providency.telegram import TelegramClient, TelegramError, valid_token_format


class Credentials:
    def get_password(self, service: str, username: str) -> str:
        return "123456:test-only-credential-not-real"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [401, 404, 403, 429, 502])
async def test_http_errors_are_actionable_and_never_expose_token(monkeypatch, code) -> None:
    def fail(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, code, "private detail", {}, io.BytesIO())

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(TelegramError) as raised:
        await TelegramClient(Credentials()).health_check()
    assert str(code) in str(raised.value)
    assert "test-only" not in str(raised.value)
    assert "private detail" not in str(raised.value)


@pytest.mark.asyncio
async def test_credential_is_reused_by_new_client_without_returning_secret() -> None:
    for _ in range(2):
        assert await TelegramClient(Credentials()).credential_status() == {
            "saved": True,
            "valid_format": True,
        }


@pytest.mark.parametrize(
    "value", ["", "bot123:abc", "123:short", "https://t.me/foo", "fixture-token-only"]
)
def test_rejects_incomplete_credential(value) -> None:
    assert not valid_token_format(value)


@pytest.mark.asyncio
async def test_send_network_failure_marks_delivery_ambiguous(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise TimeoutError("private request URL")

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(TelegramError) as raised:
        await TelegramClient(Credentials()).send_observation_summary(chat_id=1, text="test")
    assert raised.value.ambiguous_delivery
    assert "private request" not in str(raised.value)
