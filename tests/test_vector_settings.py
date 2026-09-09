from providency.config import AnalysisConfiguration
from providency.vector_settings import merge_vector_fields, parse_display_number


def test_display_number_respects_locale_and_rejects_unknown_values() -> None:
    assert parse_display_number("246.436,00", decimal_separator=",") == 246436
    assert parse_display_number("0,001", decimal_separator=",") == 0.001
    assert parse_display_number("1,234.50", decimal_separator=".") == 1234.5
    for value in ("--", "NaN", "Infinity", "1,2,3", ""):
        assert parse_display_number(value, decimal_separator=",") is None


def test_refresh_preserves_risk_limits_and_never_uses_balance_as_loss_limit() -> None:
    current = AnalysisConfiguration(
        symbol="BTC/BRL", min_rr=3, max_trades=4, max_consecutive_losses=2, max_session_loss=100
    )
    updated = merge_vector_fields(
        current,
        {
            "symbol": "BTC/BRL",
            "primary_timeframe": "15Min",
            "min_rr": 9,
            "max_trades": 999,
            "balance": 100000,
        },
    )
    assert updated.primary_timeframe == "15Min"
    assert updated.min_rr == 3
    assert updated.max_trades == 4
    assert updated.max_consecutive_losses == 2
    assert updated.max_session_loss == 100


def test_asset_change_cannot_reuse_old_instrument_quantity_or_ticks() -> None:
    current = AnalysisConfiguration(symbol="BTC/BRL", quantity=2, tick_size=0.01, tick_value=0.02)
    updated = merge_vector_fields(current, {"symbol": "ETH/BRL", "primary_timeframe": "60Min"})
    assert updated.symbol == "ETH/BRL"
    assert updated.quantity == 0
    assert updated.tick_size == 0
    assert updated.tick_value == 0


def test_refresh_does_not_present_old_tick_as_newly_observed() -> None:
    current = AnalysisConfiguration(symbol='BTC/BRL', tick_size=1, tick_value=1)
    refreshed = merge_vector_fields(current, {'symbol': 'BTC/BRL', 'quantity': .25})
    assert refreshed.tick_size == refreshed.tick_value == 0
