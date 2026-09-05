from providency.protection import ProtectionPolicy, trailing_decision


def test_open_or_unreadable_candle_does_not_advance_policy() -> None:
    policy = ProtectionPolicy(1, "LONG", 1, 100, 95, 0.5, 1, "30min", 95)
    decision = trailing_decision(
        policy,
        stage="PROTECTED",
        candle_timeframe="30min",
        candle_closed_at=None,
        candle_open=100,
        candle_close=101,
    )
    assert decision.target_stop is None
    assert decision.policy == policy
