from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from providency.config import ExecutionMode
from providency.protection import (
    PositionProtectionService,
    ProtectionPolicy,
    trailing_decision,
)
from providency.storage import Storage
from providency.vector import (
    AccountEnvironment,
    ClosedCandleObservation,
    DemoAccountState,
    OrderState,
    OrderStateObservation,
    PositionState,
    PositionStateObservation,
    ProtectionExecutionResult,
    ProtectionOrderObservation,
    ProtectionOrderState,
    ProtectionStateObservation,
    TradeSide,
)


def demo_account(side: PositionState = PositionState.LONG, quantity: int = 2) -> DemoAccountState:
    return DemoAccountState(
        AccountEnvironment.DEMO,
        "BTC/BRL",
        quantity,
        OrderStateObservation(OrderState.FILLED, "entry-1", quantity, quantity),
        PositionStateObservation(side, quantity, 100.0),
    )


def observed(
    *,
    side: PositionState = PositionState.LONG,
    protection: ProtectionOrderState = ProtectionOrderState.NONE,
    stop_price: float | None = None,
    quantity: int = 2,
    candle_at: str | None = None,
    candle_open: float | None = None,
    candle_close: float | None = None,
) -> ProtectionStateObservation:
    protective_side = TradeSide.SELL if side is PositionState.LONG else TradeSide.BUY
    return ProtectionStateObservation(
        demo_account(side, quantity),
        ProtectionOrderObservation(
            protection,
            "stop-1" if protection is ProtectionOrderState.ACTIVE else None,
            protective_side if protection is ProtectionOrderState.ACTIVE else None,
            quantity if protection is ProtectionOrderState.ACTIVE else None,
            stop_price,
        ),
        ClosedCandleObservation("30min", candle_at, candle_open, candle_close),
    )


class FakeProtectionAdapter:
    def __init__(self, state: ProtectionStateObservation) -> None:
        self.state = state
        self.stop_calls = 0
        self.cancel_calls = 0
        self.close_calls = 0

    async def observe_protection_state(self, timeframe: str) -> ProtectionStateObservation:
        assert timeframe == "30min"
        return self.state

    async def apply_demo_stop(
        self, *, side: TradeSide, quantity: int, stop_price: float, timeframe: str
    ) -> ProtectionExecutionResult:
        self.stop_calls += 1
        pre = self.state
        self.state = observed(
            side=pre.account.position.state,
            protection=ProtectionOrderState.ACTIVE,
            stop_price=stop_price,
            quantity=quantity,
            candle_at=pre.closed_candle.closed_at,
            candle_open=pre.closed_candle.open_price,
            candle_close=pre.closed_candle.close_price,
        )
        return ProtectionExecutionResult(pre, self.state, "APPLY_STOP")

    async def cancel_demo_protection(
        self, *, order_id: str, timeframe: str
    ) -> ProtectionExecutionResult:
        self.cancel_calls += 1
        pre = self.state
        self.state = ProtectionStateObservation(
            pre.account,
            ProtectionOrderObservation(ProtectionOrderState.CANCELLED, order_id, None, None, None),
            pre.closed_candle,
        )
        return ProtectionExecutionResult(pre, self.state, "CANCEL_PROTECTION")

    async def close_demo_position(
        self, *, side: TradeSide, quantity: int, timeframe: str
    ) -> ProtectionExecutionResult:
        self.close_calls += 1
        pre = self.state
        account = DemoAccountState(
            AccountEnvironment.DEMO,
            "BTC/BRL",
            quantity,
            pre.account.order,
            PositionStateObservation(PositionState.FLAT, 0, None),
        )
        self.state = ProtectionStateObservation(account, pre.protection, pre.closed_candle)
        return ProtectionExecutionResult(pre, self.state, "CLOSE_POSITION")


class AmbiguousStopAdapter(FakeProtectionAdapter):
    async def apply_demo_stop(
        self, *, side: TradeSide, quantity: int, stop_price: float, timeframe: str
    ) -> ProtectionExecutionResult:
        self.stop_calls += 1
        raise TimeoutError("No stop postcondition")


def filled_operation(storage: Storage, *, side: str = "BUY") -> dict[str, Any]:
    session = storage.start_session()
    candidate = {
        "candidate_id": "candidate-1",
        "session_id": session["id"],
        "decision": "ALLOWED",
        "symbol": "BTC/BRL",
        "side": "LONG" if side == "BUY" else "SHORT",
        "quantity": 2,
        "entry": 100.0,
        "stop": 95.0 if side == "BUY" else 105.0,
        "configuration_version": "cfg-1",
        "configuration_snapshot": {
            "tick_size": 0.5,
            "stop_buffer_ticks": 1,
            "trailing_timeframe": "30min",
        },
    }
    storage.record_trade_candidate(
        candidate, max_trades=3, max_consecutive_losses=2, max_session_loss=100
    )
    storage.create_approval(
        approval_id="approval-1",
        candidate=candidate,
        chat_id=10,
        user_id=20,
        callback_digest="digest",
        created_at="2026-09-05T12:00:00+00:00",
        expires_at="2026-09-05T12:01:00+00:00",
    )
    with storage.connect() as connection:
        connection.execute("UPDATE approvals SET status = 'WOULD_EXECUTE' WHERE id = 'approval-1'")
        connection.commit()
    operation = storage.create_operation(
        approval_id="approval-1",
        candidate_id="candidate-1",
        session_id=str(session["id"]),
        symbol="BTC/BRL",
        side=side,
        quantity=2,
        configuration_version="cfg-1",
        snapshot={"candidate": candidate, "mode": "DEMO"},
    )
    return storage.update_operation(str(operation["operation_id"]), status="FILLED")


@pytest.mark.parametrize(
    ("side", "stage", "current", "reference", "expected"),
    [
        ("LONG", "PROTECTED", 95.0, None, 100.0),
        ("LONG", "BREAKEVEN", 100.0, 103.0, 102.5),
        ("SHORT", "BREAKEVEN", 100.0, 97.0, 97.5),
    ],
)
def test_trailing_is_symmetric_and_monotonic(
    side: str, stage: str, current: float, reference: float | None, expected: float
) -> None:
    policy = ProtectionPolicy(
        1,
        side,
        2,
        100.0,
        95.0 if side == "LONG" else 105.0,
        0.5,
        1,
        "30min",
        current,
        "candle-0",
        reference,
    )

    decision = trailing_decision(
        policy,
        stage=stage,
        candle_timeframe="30min",
        candle_closed_at="candle-1",
        candle_open=100.0,
        candle_close=104.0 if side == "LONG" else 96.0,
    )

    assert decision.target_stop == expected


def test_repeated_or_risk_increasing_candle_never_moves_stop() -> None:
    policy = ProtectionPolicy(1, "LONG", 2, 100.0, 95.0, 0.5, 1, "30min", 104.0, "candle-1", 103.0)
    repeated = trailing_decision(
        policy,
        stage="TRAILING",
        candle_timeframe="30min",
        candle_closed_at="candle-1",
        candle_open=103.0,
        candle_close=105.0,
    )
    riskier = trailing_decision(
        policy,
        stage="TRAILING",
        candle_timeframe="30min",
        candle_closed_at="candle-2",
        candle_open=103.0,
        candle_close=105.0,
    )
    assert repeated.target_stop is None
    assert riskier.target_stop is None


def test_filled_position_gets_one_confirmed_initial_stop(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    operation = filled_operation(storage)
    adapter = FakeProtectionAdapter(observed())
    service = PositionProtectionService(storage, adapter, mode=ExecutionMode.DEMO)

    first = asyncio.run(service.protect_filled(operation))
    second = asyncio.run(service.protect_filled(operation))

    assert first["status"] == "PROTECTED"
    assert second["status"] == "PROTECTED"
    assert adapter.stop_calls == 1
    assert not storage.has_blocking_protection()


def test_ambiguous_stop_enters_safe_stop_without_retry(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    operation = filled_operation(storage)
    adapter = AmbiguousStopAdapter(observed())
    service = PositionProtectionService(storage, adapter, mode=ExecutionMode.DEMO)

    first = asyncio.run(service.protect_filled(operation))
    second = asyncio.run(service.protect_filled(operation))

    assert first["status"] == "SAFE_STOP"
    assert second["status"] == "SAFE_STOP"
    assert adapter.stop_calls == 1
    assert storage.has_blocking_protection()


def test_restart_recovers_existing_stop_without_new_action(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    filled_operation(storage)
    adapter = FakeProtectionAdapter(
        observed(protection=ProtectionOrderState.ACTIVE, stop_price=95.0)
    )
    service = PositionProtectionService(storage, adapter, mode=ExecutionMode.DEMO)

    recovered = asyncio.run(service.recover_all())

    assert recovered[0]["status"] == "PROTECTED"
    assert adapter.stop_calls == 0


def test_management_moves_breakeven_then_trails_once_per_closed_candle(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    operation = filled_operation(storage)
    adapter = FakeProtectionAdapter(observed())
    service = PositionProtectionService(storage, adapter, mode=ExecutionMode.DEMO)
    asyncio.run(service.protect_filled(operation))
    adapter.state = observed(
        protection=ProtectionOrderState.ACTIVE,
        stop_price=95.0,
        candle_at="candle-1",
        candle_open=100.0,
        candle_close=101.0,
    )

    breakeven = asyncio.run(service.manage(operation))
    adapter.state = observed(
        protection=ProtectionOrderState.ACTIVE,
        stop_price=100.0,
        candle_at="candle-2",
        candle_open=101.0,
        candle_close=103.0,
    )
    trailing = asyncio.run(service.manage(operation))
    repeated = asyncio.run(service.manage(operation))

    assert breakeven["status"] == "BREAKEVEN"
    assert trailing["status"] == "TRAILING"
    assert trailing["policy"]["current_stop"] == 100.5
    assert repeated["policy"]["current_stop"] == 100.5
    assert adapter.stop_calls == 3


def test_emergency_cancel_and_close_are_attempted_at_most_once(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    operation = filled_operation(storage)
    adapter = FakeProtectionAdapter(
        observed(protection=ProtectionOrderState.ACTIVE, stop_price=95.0)
    )
    service = PositionProtectionService(storage, adapter, mode=ExecutionMode.DEMO)
    asyncio.run(service.protect_filled(operation))

    first = asyncio.run(service.emergency_stop(operation))
    second = asyncio.run(service.emergency_stop(operation))

    assert first["status"] == "CONFIRMED"
    assert second["emergency_operation_id"] == first["emergency_operation_id"]
    assert adapter.cancel_calls == 1
    assert adapter.close_calls == 1
