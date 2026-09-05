from __future__ import annotations

import asyncio
from pathlib import Path

from providency.config import ExecutionMode
from providency.execution import DemoExecutionService
from providency.storage import Storage
from providency.vector import (
    AccountEnvironment,
    DemoAccountState,
    OrderState,
    OrderStateObservation,
    PositionState,
    PositionStateObservation,
)


class ReconciliationAdapter:
    def __init__(self, state: DemoAccountState) -> None:
        self.state = state

    async def observe_demo_state(self) -> DemoAccountState:
        return self.state


def state(order: OrderState, position: PositionState) -> DemoAccountState:
    return DemoAccountState(
        account=AccountEnvironment.DEMO,
        symbol="BTC/BRL",
        quantity=2,
        order=OrderStateObservation(order, "vector-1", 2, 2 if order is OrderState.FILLED else 0),
        position=PositionStateObservation(
            position, 2 if position is not PositionState.FLAT else 0, 100.0
        ),
    )


def pending_operation(storage: Storage) -> dict[str, object]:
    with storage.connect() as connection:
        connection.execute(
            "INSERT INTO sessions(id, started_at, status) VALUES ('session-1', ?, 'RUNNING')",
            ("2026-09-05T12:00:00+00:00",),
        )
        connection.execute("INSERT INTO session_risk_state(session_id) VALUES ('session-1')")
        connection.execute(
            "INSERT INTO trade_candidates(id, session_id, created_at, decision, candidate_json) "
            "VALUES ('candidate-1', 'session-1', ?, 'ALLOWED', ?)",
            ("2026-09-05T12:00:01+00:00", '{"candidate_id":"candidate-1"}'),
        )
        connection.execute(
            "INSERT INTO approvals(id, candidate_id, session_id, created_at, expires_at, "
            "decided_at, "
            "status, chat_id, user_id, callback_digest, candidate_json) VALUES "
            "('approval-1', 'candidate-1', 'session-1', ?, ?, ?, 'WOULD_EXECUTE', "
            "10, 20, 'd', '{}')",
            (
                "2026-09-05T12:00:02+00:00",
                "2026-09-05T12:01:02+00:00",
                "2026-09-05T12:00:03+00:00",
            ),
        )
        connection.commit()
    return storage.create_operation(
        approval_id="approval-1",
        candidate_id="candidate-1",
        session_id="session-1",
        symbol="BTC/BRL",
        side="SELL",
        quantity=2,
        configuration_version="cfg-1",
        snapshot={"approval_id": "approval-1"},
    )


def test_restart_reconciliation_converges_filled_position(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    operation = pending_operation(storage)
    storage.update_operation(str(operation["operation_id"]), status="SUBMITTED_UNCONFIRMED")
    service = DemoExecutionService(
        storage,
        ReconciliationAdapter(state(OrderState.FILLED, PositionState.SHORT)),
        mode=ExecutionMode.DEMO,
    )

    reconciled = asyncio.run(service.reconcile_open_operations())

    assert reconciled[0]["status"] == "FILLED"
    assert reconciled[0]["reconciliation"]["position"]["state"] == "SHORT"


def test_unknown_reconciliation_is_ambiguous_and_blocks(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    operation = pending_operation(storage)
    storage.update_operation(str(operation["operation_id"]), status="PENDING")
    unknown = DemoAccountState(
        account=AccountEnvironment.UNKNOWN,
        symbol=None,
        quantity=None,
        order=OrderStateObservation(OrderState.UNKNOWN, None, None, None),
        position=PositionStateObservation(PositionState.UNKNOWN, None, None),
    )
    service = DemoExecutionService(storage, ReconciliationAdapter(unknown), mode=ExecutionMode.DEMO)

    reconciled = asyncio.run(service.reconcile_open_operations())

    assert reconciled[0]["status"] == "AMBIGUOUS"
    assert storage.has_blocking_operation()
