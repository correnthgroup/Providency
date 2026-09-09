from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Protocol

from providency.config import ExecutionMode
from providency.storage import Storage
from providency.vector import (
    AccountEnvironment,
    DemoAccountState,
    DemoExecutionResult,
    OrderState,
    PositionState,
    TradeSide,
)


class DemoAdapterContract(Protocol):
    async def observe_demo_state(self) -> DemoAccountState: ...

    async def prepare_demo_order(self, quantity: int) -> DemoExecutionResult: ...

    async def select_demo_account(self) -> DemoExecutionResult: ...

    async def submit_demo_order(
        self, side: TradeSide, *, symbol: str, quantity: int
    ) -> DemoExecutionResult: ...


class FilledProtectionContract(Protocol):
    async def protect_filled(self, operation: Mapping[str, Any]) -> dict[str, Any]: ...


class DemoExecutionService:
    def __init__(
        self,
        storage: Storage,
        adapter: DemoAdapterContract,
        *,
        mode: ExecutionMode,
        protection: FilledProtectionContract | None = None,
    ) -> None:
        self.storage = storage
        self.adapter = adapter
        self.mode = mode
        self.protection = protection
        self._execution_lock = asyncio.Lock()

    async def execute_approved(self, approval: Mapping[str, Any]) -> dict[str, Any]:
        async with self._execution_lock:
            return await self._execute_approved_unlocked(approval)

    async def _execute_approved_unlocked(self, approval: Mapping[str, Any]) -> dict[str, Any]:
        approval_id = str(approval["id"])
        existing = self.storage.get_operation_for_approval(approval_id)
        if existing is not None:
            return existing

        await self._reconcile_open_operations_unlocked()
        candidate = dict(approval["candidate_snapshot"])
        candidate_side = str(candidate["side"]).upper()
        if candidate_side not in {"LONG", "SHORT"}:
            raise ValueError("Approved candidate side is neither LONG nor SHORT.")
        side = TradeSide.BUY if candidate_side == "LONG" else TradeSide.SELL
        operation = self.storage.create_operation(
            approval_id=approval_id,
            candidate_id=str(approval["candidate_id"]),
            session_id=str(approval["session_id"]),
            symbol=str(candidate["symbol"]),
            side=side.value,
            quantity=float(candidate["quantity"]),
            configuration_version=str(candidate["configuration_version"]),
            snapshot={
                "approval": dict(approval),
                "candidate": candidate,
                "mode": self.mode.value,
            },
        )
        operation_id = str(operation["operation_id"])
        if not float(candidate["quantity"]).is_integer():
            return self.storage.update_operation(
                operation_id,
                status="BLOCKED",
                reason=(
                    "Fractional quantity is preserved; the demo executor supports whole units only."
                ),
            )
        if self.mode is not ExecutionMode.DEMO:
            return self.storage.update_operation(
                operation_id,
                status="BLOCKED",
                reason="Demo execution is disabled; DRY_RUN remains active.",
            )
        if self.storage.has_blocking_protection() or (
            self.storage.has_blocking_operation() and self.storage.open_operations() != [operation]
        ):
            return self.storage.update_operation(
                operation_id,
                status="BLOCKED",
                reason="Another unresolved operation blocks new exposure.",
            )

        try:
            observed = await self.adapter.observe_demo_state()
            self.storage.record_operation_observation(
                operation_id, phase="PRECHECK", observation=observed.to_dict()
            )
            reason = self._precheck_reason(observed, operation)
            if reason is not None:
                return self.storage.update_operation(
                    operation_id,
                    status="BLOCKED",
                    reason=reason,
                    precheck=observed.to_dict(),
                )

            selected = await self.adapter.select_demo_account()
            self.storage.record_operation_observation(
                operation_id, phase="ACCOUNT_POST", observation=selected.post.to_dict()
            )
            if selected.post.account is not AccountEnvironment.DEMO:
                return self.storage.update_operation(
                    operation_id,
                    status="BLOCKED",
                    reason="Demo-account postcondition is divergent.",
                    precheck=selected.to_dict(),
                )

            prepared = await self.adapter.prepare_demo_order(int(operation["quantity"]))
            self.storage.record_operation_observation(
                operation_id, phase="QUANTITY_POST", observation=prepared.post.to_dict()
            )
            if (
                prepared.post.account is not AccountEnvironment.DEMO
                or prepared.post.quantity != int(operation["quantity"])
                or prepared.post.symbol != str(operation["symbol"])
            ):
                return self.storage.update_operation(
                    operation_id,
                    status="BLOCKED",
                    reason="Demo quantity or account postcondition is divergent.",
                    precheck=prepared.to_dict(),
                )
            self.storage.update_operation(
                operation_id, status="PRECHECKED", precheck=prepared.to_dict()
            )
            running = self.storage.running_session()
            if running is None or str(running["id"]) != str(operation["session_id"]):
                return self.storage.update_operation(
                    operation_id,
                    status="BLOCKED",
                    reason="The approved session stopped before the demo submit attempt.",
                )
            # Persist uncertainty before the one external submit attempt. A failure from this
            # point is reconciled and never retried automatically.
            self.storage.update_operation(
                operation_id,
                status="SUBMITTED_UNCONFIRMED",
                reason="A single demo submit attempt is in progress or awaiting evidence.",
            )
            submitted = await self.adapter.submit_demo_order(
                side,
                symbol=str(operation["symbol"]),
                quantity=int(operation["quantity"]),
            )
            self.storage.record_operation_observation(
                operation_id, phase="SUBMIT_POST", observation=submitted.post.to_dict()
            )
            status, reason = self._observed_status(submitted.post, operation)
            updated = self.storage.update_operation(
                operation_id,
                status=status,
                reason=reason,
                submission=submitted.to_dict(),
                reconciliation=submitted.post.to_dict(),
            )
            await self._protect_if_filled(updated)
            return updated
        except Exception as exc:
            return self.storage.update_operation(
                operation_id,
                status="AMBIGUOUS",
                reason=(
                    f"Demo action became ambiguous ({type(exc).__name__}); "
                    "automatic retry is blocked."
                ),
            )

    async def reconcile_open_operations(self) -> list[dict[str, Any]]:
        async with self._execution_lock:
            return await self._reconcile_open_operations_unlocked()

    async def _reconcile_open_operations_unlocked(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for operation in self.storage.open_operations():
            operation_id = str(operation["operation_id"])
            try:
                observed = await self.adapter.observe_demo_state()
                self.storage.record_operation_observation(
                    operation_id, phase="RECONCILIATION", observation=observed.to_dict()
                )
                status, reason = self._observed_status(observed, operation, reconciling=True)
                updated = self.storage.update_operation(
                    operation_id,
                    status=status,
                    reason=reason,
                    reconciliation=observed.to_dict(),
                )
                await self._protect_if_filled(updated)
                results.append(updated)
            except Exception as exc:
                results.append(
                    self.storage.update_operation(
                        operation_id,
                        status="AMBIGUOUS",
                        reason=(
                            f"Reconciliation failed ({type(exc).__name__}); "
                            "new exposure is blocked."
                        ),
                    )
                )
        return results

    async def _protect_if_filled(self, operation: Mapping[str, Any]) -> None:
        if self.protection is not None and str(operation["status"]) == "FILLED":
            try:
                await self.protection.protect_filled(operation)
            except (KeyError, TypeError, ValueError):
                # Preserve observed FILLED evidence. A missing policy remains blocking and
                # visible through has_blocking_protection until it can be understood.
                return

    @staticmethod
    def _precheck_reason(state: DemoAccountState, operation: Mapping[str, Any]) -> str | None:
        if state.account is not AccountEnvironment.DEMO:
            return "The applied Vector account is not provably DEMO."
        if state.symbol != str(operation["symbol"]):
            return "The observed symbol does not match the approved proposal."
        if state.position.state is not PositionState.FLAT:
            return "The observed position is not provably FLAT."
        if state.order.state is not OrderState.NONE:
            return "An order already exists or order state is unknown."
        return None

    @staticmethod
    def _observed_status(
        state: DemoAccountState,
        operation: Mapping[str, Any],
        *,
        reconciling: bool = False,
    ) -> tuple[str, str]:
        if (
            state.account is not AccountEnvironment.DEMO
            or state.symbol != str(operation["symbol"])
            or state.order.state is OrderState.UNKNOWN
            or state.position.state is PositionState.UNKNOWN
        ):
            return (
                "AMBIGUOUS",
                "Account, symbol, order, or position is not readable; retry is blocked.",
            )
        if state.order.state is not OrderState.NONE and (
            state.order.order_id is None
            or state.order.requested_quantity != int(operation["quantity"])
        ):
            return (
                "AMBIGUOUS",
                "Observed order cannot be correlated by identifier and requested quantity.",
            )
        expected_position = (
            PositionState.LONG
            if str(operation["side"]) == TradeSide.BUY.value
            else PositionState.SHORT
        )
        if state.position.state is expected_position and state.position.quantity == int(
            operation["quantity"]
        ):
            return "FILLED", "Observed demo position matches the persisted operation."
        direct = {
            OrderState.PENDING: "PENDING",
            OrderState.PARTIAL: "PARTIAL",
            OrderState.FILLED: "AMBIGUOUS",
            OrderState.REJECTED: "REJECTED",
            OrderState.CANCELLED: "CANCELLED",
        }
        if state.order.state in direct:
            status = direct[state.order.state]
            return status, f"Vector reports order state {state.order.state.value}."
        if reconciling and str(operation["status"]) in {"CREATED", "PRECHECKED"}:
            return (
                "BLOCKED",
                "Restart found no submitted order; the persisted intent remains closed.",
            )
        return (
            "SUBMITTED_UNCONFIRMED",
            "No correlated order or position is visible yet; retry is blocked.",
        )
