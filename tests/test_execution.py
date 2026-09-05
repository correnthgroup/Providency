from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from providency.config import ExecutionMode
from providency.execution import DemoExecutionService
from providency.storage import Storage
from providency.vector import (
    AccountEnvironment,
    DemoAccountState,
    DemoExecutionResult,
    OrderState,
    OrderStateObservation,
    PositionState,
    PositionStateObservation,
    TradeSide,
)


def snapshot(
    *,
    account: AccountEnvironment = AccountEnvironment.DEMO,
    quantity: int | None = 2,
    order: OrderState = OrderState.NONE,
    position: PositionState = PositionState.FLAT,
    position_quantity: int | None = None,
) -> DemoAccountState:
    return DemoAccountState(
        account=account,
        symbol="BTC/BRL",
        quantity=quantity,
        order=OrderStateObservation(
            order,
            "vector-1" if order is not OrderState.NONE else None,
            2 if order is not OrderState.NONE else None,
            1 if order is OrderState.PARTIAL else (2 if order is OrderState.FILLED else 0),
        ),
        position=PositionStateObservation(
            position,
            (
                position_quantity
                if position_quantity is not None
                else (0 if position is PositionState.FLAT else 2)
            ),
            None,
        ),
    )


class FakeDemoAdapter:
    def __init__(self, states: list[DemoAccountState]) -> None:
        self.states = states
        self.prepare_calls = 0
        self.select_calls = 0
        self.submit_calls = 0
        self.submitted_sides: list[TradeSide] = []

    async def observe_demo_state(self) -> DemoAccountState:
        return self.states[0]

    async def prepare_demo_order(self, quantity: int) -> DemoExecutionResult:
        self.prepare_calls += 1
        pre = self.states.pop(0)
        post = self.states[0]
        return DemoExecutionResult(pre=pre, post=post, action="SET_QUANTITY")

    async def select_demo_account(self) -> DemoExecutionResult:
        self.select_calls += 1
        pre = self.states.pop(0)
        post = self.states[0]
        return DemoExecutionResult(pre=pre, post=post, action="SELECT_DEMO_ACCOUNT")

    async def submit_demo_order(
        self, side: TradeSide, *, symbol: str, quantity: int
    ) -> DemoExecutionResult:
        self.submit_calls += 1
        self.submitted_sides.append(side)
        pre = self.states.pop(0)
        post = self.states[0]
        return DemoExecutionResult(pre=pre, post=post, action=side.value)


class AmbiguousSubmitAdapter(FakeDemoAdapter):
    async def submit_demo_order(
        self, side: TradeSide, *, symbol: str, quantity: int
    ) -> DemoExecutionResult:
        self.submit_calls += 1
        raise TimeoutError("Vector did not expose a postcondition in time.")


def approved(storage: Storage, *, side: str = "SHORT") -> dict[str, Any]:
    session = storage.start_session()
    candidate = {
        "candidate_id": "candidate-1",
        "session_id": session["id"],
        "decision": "ALLOWED",
        "symbol": "BTC/BRL",
        "side": side,
        "quantity": 2,
        "configuration_version": "cfg-1",
    }
    storage.record_trade_candidate(
        candidate, max_trades=3, max_consecutive_losses=2, max_session_loss=100
    )
    created = storage.create_approval(
        approval_id="approval-1",
        candidate=candidate,
        chat_id=10,
        user_id=20,
        callback_digest="digest-1",
        created_at="2026-09-05T12:00:00+00:00",
        expires_at="2026-09-05T12:01:00+00:00",
    )
    with storage.connect() as connection:
        connection.execute(
            "UPDATE approvals SET status = 'WOULD_EXECUTE', decided_at = ? WHERE id = ?",
            ("2026-09-05T12:00:30+00:00", created["id"]),
        )
        connection.commit()
    result = storage.get_approval("approval-1")
    assert result is not None
    return result


def test_demo_approval_persists_one_operation_before_one_submit(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    adapter = FakeDemoAdapter(
        [
            snapshot(quantity=1),
            snapshot(quantity=1),
            snapshot(quantity=2),
            snapshot(quantity=2, order=OrderState.FILLED, position=PositionState.SHORT),
        ]
    )
    service = DemoExecutionService(storage, adapter, mode=ExecutionMode.DEMO)
    approval = approved(storage)

    first = asyncio.run(service.execute_approved(approval))
    second = asyncio.run(service.execute_approved(approval))

    assert first["status"] == "FILLED"
    assert second["operation_id"] == first["operation_id"]
    assert adapter.prepare_calls == 1
    assert adapter.select_calls == 1
    assert adapter.submit_calls == 1
    assert len(storage.list_operations()) == 1


def test_unknown_account_blocks_before_quantity_or_submit(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    adapter = FakeDemoAdapter([snapshot(account=AccountEnvironment.UNKNOWN)])
    service = DemoExecutionService(storage, adapter, mode=ExecutionMode.DEMO)

    operation = asyncio.run(service.execute_approved(approved(storage)))

    assert operation["status"] == "BLOCKED"
    assert adapter.prepare_calls == 0
    assert adapter.select_calls == 0
    assert adapter.submit_calls == 0


def test_dry_run_never_reaches_demo_adapter(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    adapter = FakeDemoAdapter([snapshot()])
    service = DemoExecutionService(storage, adapter, mode=ExecutionMode.DRY_RUN)

    operation = asyncio.run(service.execute_approved(approved(storage)))

    assert operation["status"] == "BLOCKED"
    assert adapter.prepare_calls == 0
    assert adapter.select_calls == 0
    assert adapter.submit_calls == 0


def test_concurrent_consumers_cannot_submit_twice(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    adapter = FakeDemoAdapter(
        [
            snapshot(quantity=1),
            snapshot(quantity=1),
            snapshot(quantity=2),
            snapshot(quantity=2, order=OrderState.FILLED, position=PositionState.SHORT),
        ]
    )
    service = DemoExecutionService(storage, adapter, mode=ExecutionMode.DEMO)
    approval = approved(storage)

    async def consume_twice() -> list[dict[str, Any]]:
        return list(
            await asyncio.gather(
                service.execute_approved(approval), service.execute_approved(approval)
            )
        )

    results = asyncio.run(consume_twice())

    assert {item["operation_id"] for item in results} == {results[0]["operation_id"]}
    assert adapter.submit_calls == 1


@pytest.mark.parametrize(
    ("order", "position", "position_quantity", "expected"),
    [
        (OrderState.PENDING, PositionState.FLAT, 0, "PENDING"),
        (OrderState.PARTIAL, PositionState.SHORT, 1, "PARTIAL"),
        (OrderState.REJECTED, PositionState.FLAT, 0, "REJECTED"),
    ],
)
def test_observed_order_states_remain_explicit(
    tmp_path: Path,
    order: OrderState,
    position: PositionState,
    position_quantity: int,
    expected: str,
) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    adapter = FakeDemoAdapter(
        [
            snapshot(quantity=1),
            snapshot(quantity=1),
            snapshot(quantity=2),
            snapshot(
                quantity=2,
                order=order,
                position=position,
                position_quantity=position_quantity,
            ),
        ]
    )
    service = DemoExecutionService(storage, adapter, mode=ExecutionMode.DEMO)

    operation = asyncio.run(service.execute_approved(approved(storage)))

    assert operation["status"] == expected


def test_long_candidate_uses_buy_control(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    adapter = FakeDemoAdapter(
        [
            snapshot(quantity=1),
            snapshot(quantity=1),
            snapshot(quantity=2),
            snapshot(quantity=2, order=OrderState.FILLED, position=PositionState.LONG),
        ]
    )
    service = DemoExecutionService(storage, adapter, mode=ExecutionMode.DEMO)

    operation = asyncio.run(service.execute_approved(approved(storage, side="LONG")))

    assert operation["status"] == "FILLED"
    assert adapter.submitted_sides == [TradeSide.BUY]


def test_timeout_after_submit_is_ambiguous_and_never_retried(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    adapter = AmbiguousSubmitAdapter(
        [snapshot(quantity=1), snapshot(quantity=1), snapshot(quantity=2)]
    )
    service = DemoExecutionService(storage, adapter, mode=ExecutionMode.DEMO)
    approval = approved(storage)

    first = asyncio.run(service.execute_approved(approval))
    second = asyncio.run(service.execute_approved(approval))

    assert first["status"] == "AMBIGUOUS"
    assert second["operation_id"] == first["operation_id"]
    assert adapter.submit_calls == 1
