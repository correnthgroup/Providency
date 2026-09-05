import pytest

from providency.config import TelegramConfiguration


def test_telegram_configuration_never_serializes_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDENCY_TELEGRAM_CHAT_ID", "-100123")
    monkeypatch.setenv("PROVIDENCY_TELEGRAM_USER_ID", "42")
    monkeypatch.setenv("PROVIDENCY_APPROVAL_TTL_SECONDS", "60")
    monkeypatch.setenv("PROVIDENCY_TELEGRAM_BOT_TOKEN", "must-not-be-read")

    configuration = TelegramConfiguration.from_env()

    assert configuration.missing_fields == ()
    assert configuration.to_dict()["token"] == "MASKED"
    assert "must-not-be-read" not in str(configuration.to_dict())


def test_invalid_ttl_and_identity_fail_closed() -> None:
    configuration = TelegramConfiguration(
        chat_id=0,
        user_id=-1,
        approval_ttl_seconds=-1,
        recheck_price_tolerance_ticks=-1,
    )

    assert configuration.missing_fields == (
        "chat_id",
        "user_id",
        "approval_ttl_seconds",
        "recheck_price_tolerance_ticks",
    )
