import pytest

from providency.config import ExecutionMode, Settings, TelegramConfiguration


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


def test_execution_mode_defaults_to_dry_run_and_has_no_live_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROVIDENCY_EXECUTION_MODE", raising=False)
    assert Settings.from_env().execution_mode is ExecutionMode.DRY_RUN
    assert {mode.value for mode in ExecutionMode} == {"DRY_RUN", "DEMO"}


def test_unknown_execution_mode_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROVIDENCY_EXECUTION_MODE", "LIVE")
    with pytest.raises(ValueError, match="LIVE"):
        Settings.from_env()


def test_trailing_timeframe_is_explicit_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDENCY_TRAILING_TIMEFRAME", "30min")
    assert Settings.from_env().trading_configuration.trailing_timeframe == "30min"


def test_evidence_retention_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROVIDENCY_EVIDENCE_RETENTION_DAYS", "0")
    with pytest.raises(ValueError, match="retention"):
        Settings.from_env()
