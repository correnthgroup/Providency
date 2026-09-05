import asyncio
from pathlib import Path

from test_protection import FakeProtectionAdapter, filled_operation, observed

from providency.config import ExecutionMode
from providency.protection import PositionProtectionService
from providency.storage import Storage
from providency.vector import ProtectionExecutionResult, ProtectionOrderState, TradeSide


class AmbiguousCloseAdapter(FakeProtectionAdapter):
    async def close_demo_position(
        self, *, side: TradeSide, quantity: int, timeframe: str
    ) -> ProtectionExecutionResult:
        self.close_calls += 1
        raise TimeoutError("No close postcondition")


def test_ambiguous_emergency_is_never_retried(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    operation = filled_operation(storage)
    adapter = AmbiguousCloseAdapter(
        observed(protection=ProtectionOrderState.ACTIVE, stop_price=95.0)
    )
    service = PositionProtectionService(storage, adapter, mode=ExecutionMode.DEMO)
    asyncio.run(service.protect_filled(operation))

    first = asyncio.run(service.emergency_stop(operation))
    second = asyncio.run(service.emergency_stop(operation))

    assert first["status"] == "AMBIGUOUS"
    assert second["emergency_operation_id"] == first["emergency_operation_id"]
    assert adapter.cancel_calls == 1
    assert adapter.close_calls == 1
