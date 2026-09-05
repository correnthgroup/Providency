import asyncio
from pathlib import Path

from test_protection import FakeProtectionAdapter, filled_operation, observed

from providency.config import ExecutionMode
from providency.protection import PositionProtectionService
from providency.storage import Storage
from providency.vector import ProtectionOrderState


def test_restart_with_unknown_protection_enters_safe_stop(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    filled_operation(storage)
    adapter = FakeProtectionAdapter(observed(protection=ProtectionOrderState.UNKNOWN))
    service = PositionProtectionService(storage, adapter, mode=ExecutionMode.DEMO)

    recovered = asyncio.run(service.recover_all())

    assert recovered[0]["status"] == "SAFE_STOP"
    assert storage.has_blocking_protection()
    assert adapter.stop_calls == 0


def test_restart_confirms_persisted_breakeven_intent_without_retry(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    operation = filled_operation(storage)
    adapter = FakeProtectionAdapter(
        observed(protection=ProtectionOrderState.ACTIVE, stop_price=95.0)
    )
    service = PositionProtectionService(storage, adapter, mode=ExecutionMode.DEMO)
    protected = asyncio.run(service.protect_filled(operation))
    policy = {**protected["policy"], "last_closed_candle": "candle-1"}
    storage.update_protection_policy(
        protected["protection_policy_id"],
        status="PROTECTED",
        policy=policy,
        action={"intent": "BREAKEVEN", "price": 100.0},
        reason="Simulated crash after external action.",
    )
    adapter.state = observed(protection=ProtectionOrderState.ACTIVE, stop_price=100.0)

    recovered = asyncio.run(service.recover_all())

    assert recovered[0]["status"] == "BREAKEVEN"
    assert recovered[0]["policy"]["current_stop"] == 100.0
    assert adapter.stop_calls == 0
