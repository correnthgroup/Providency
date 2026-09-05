from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from providency.config import ExecutionMode
from providency.storage import Storage
from providency.vector import (
    AccountEnvironment,
    PositionState,
    ProtectionExecutionResult,
    ProtectionOrderState,
    ProtectionStateObservation,
    TradeSide,
)


class ProtectionAdapterContract(Protocol):
    async def observe_protection_state(self, timeframe: str) -> ProtectionStateObservation: ...

    async def apply_demo_stop(
        self, *, side: TradeSide, quantity: int, stop_price: float, timeframe: str
    ) -> ProtectionExecutionResult: ...

    async def cancel_demo_protection(
        self, *, order_id: str, timeframe: str
    ) -> ProtectionExecutionResult: ...

    async def close_demo_position(
        self, *, side: TradeSide, quantity: int, timeframe: str
    ) -> ProtectionExecutionResult: ...


@dataclass(frozen=True, slots=True)
class ProtectionPolicy:
    version: int
    side: str
    quantity: int
    entry_price: float
    initial_stop: float
    tick_size: float
    buffer_ticks: int
    timeframe: str
    current_stop: float | None = None
    last_closed_candle: str | None = None
    reference_close: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProtectionPolicy:
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class TrailingDecision:
    target_stop: float | None
    stage: str
    policy: ProtectionPolicy
    reason: str


def policy_from_operation(operation: Mapping[str, Any]) -> ProtectionPolicy:
    snapshot = dict(operation["snapshot"])
    candidate = dict(snapshot["candidate"])
    configuration = dict(candidate.get("configuration_snapshot") or {})
    side = "LONG" if str(operation["side"]) == TradeSide.BUY.value else "SHORT"
    policy = ProtectionPolicy(
        version=1,
        side=side,
        quantity=int(operation["quantity"]),
        entry_price=float(candidate["entry"]),
        initial_stop=float(candidate["stop"]),
        tick_size=float(configuration.get("tick_size", candidate.get("tick_size", 0))),
        buffer_ticks=int(
            configuration.get("stop_buffer_ticks", candidate.get("stop_buffer_ticks", 0))
        ),
        timeframe=str(
            configuration.get("trailing_timeframe", candidate.get("trailing_timeframe", ""))
        ),
    )
    if policy.quantity <= 0 or policy.tick_size <= 0 or not policy.timeframe:
        raise ValueError("Protection policy is missing quantity, tick, or trailing timeframe.")
    if policy.side == "LONG" and policy.initial_stop >= policy.entry_price:
        raise ValueError("LONG initial stop must be below entry.")
    if policy.side == "SHORT" and policy.initial_stop <= policy.entry_price:
        raise ValueError("SHORT initial stop must be above entry.")
    return policy


def _same_price(left: float | None, right: float, tick_size: float) -> bool:
    return left is not None and abs(left - right) < tick_size / 2


def _rounded_tick(price: float, tick_size: float) -> float:
    return round(round(price / tick_size) * tick_size, 10)


def trailing_decision(
    policy: ProtectionPolicy,
    *,
    stage: str,
    candle_timeframe: str | None,
    candle_closed_at: str | None,
    candle_open: float | None,
    candle_close: float | None,
) -> TrailingDecision:
    if (
        candle_timeframe != policy.timeframe
        or candle_closed_at is None
        or candle_open is None
        or candle_close is None
    ):
        return TrailingDecision(None, stage, policy, "Closed trailing candle is unreadable.")
    if candle_closed_at == policy.last_closed_candle:
        return TrailingDecision(None, stage, policy, "Closed candle was already processed.")
    profitable = candle_close > candle_open if policy.side == "LONG" else candle_close < candle_open
    updated = ProtectionPolicy(
        **{
            **policy.to_dict(),
            "last_closed_candle": candle_closed_at,
            "reference_close": candle_close if profitable else policy.reference_close,
        }
    )
    if not profitable:
        return TrailingDecision(None, stage, updated, "Closed candle was not profitable.")
    if stage == "PROTECTED":
        return TrailingDecision(policy.entry_price, "BREAKEVEN", updated, "Move to breakeven.")
    if stage not in {"BREAKEVEN", "TRAILING"} or policy.reference_close is None:
        return TrailingDecision(None, stage, updated, "No prior profitable reference exists.")
    buffer = policy.buffer_ticks * policy.tick_size
    raw_target = (
        policy.reference_close - buffer
        if policy.side == "LONG"
        else policy.reference_close + buffer
    )
    target = _rounded_tick(raw_target, policy.tick_size)
    current = policy.current_stop
    monotonic = current is not None and (
        target > current if policy.side == "LONG" else target < current
    )
    reduces_entry_risk = (
        target >= policy.entry_price if policy.side == "LONG" else target <= policy.entry_price
    )
    if not monotonic or not reduces_entry_risk:
        return TrailingDecision(None, stage, updated, "Candidate stop is not risk-reducing.")
    return TrailingDecision(target, "TRAILING", updated, "Advance trailing stop.")


class PositionProtectionService:
    def __init__(
        self, storage: Storage, adapter: ProtectionAdapterContract, *, mode: ExecutionMode
    ) -> None:
        self.storage = storage
        self.adapter = adapter
        self.mode = mode
        self._lock = asyncio.Lock()

    async def protect_filled(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        async with self._lock:
            return await self._protect_filled_unlocked(operation)

    async def _protect_filled_unlocked(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        operation_id = str(operation["operation_id"])
        existing = self.storage.get_protection_policy(operation_id)
        if existing is not None and str(existing["status"]) in {
            "SAFE_STOP",
            "EMERGENCY_PENDING",
            "EMERGENCY_UNCONFIRMED",
            "CLOSED",
        }:
            return existing
        ambiguous_initial = existing is not None and str(existing["status"]) == "APPLYING_INITIAL"
        try:
            desired = (
                ProtectionPolicy.from_dict(existing["policy"])
                if existing is not None
                else policy_from_operation(operation)
            )
            record = existing or self.storage.create_protection_policy(
                operation_id, desired.to_dict()
            )
        except Exception as exc:
            if existing is None:
                raise
            return self._safe_stop(
                existing, f"Protection policy is invalid ({type(exc).__name__})."
            )
        if self.mode is not ExecutionMode.DEMO:
            return self._safe_stop(record, "Demo protection is disabled.")
        observed = await self._observe_or_safe(record, desired)
        if isinstance(observed, dict):
            return observed
        reason = self._position_reason(observed, desired)
        if reason:
            return self._safe_stop(record, reason, observed)
        protection = observed.protection
        protective_side = TradeSide.SELL if desired.side == "LONG" else TradeSide.BUY
        if protection.state is ProtectionOrderState.ACTIVE:
            pending_action = dict(record.get("action") or {})
            pending_stage = str(pending_action.get("intent", ""))
            if pending_stage in {"BREAKEVEN", "TRAILING"}:
                try:
                    intended_price = float(pending_action["price"])
                except (KeyError, TypeError, ValueError):
                    return self._safe_stop(
                        record, "Persisted stop-movement intent is malformed.", observed
                    )
                if not self._confirmed_stop(observed, desired, intended_price):
                    return self._safe_stop(
                        record,
                        "Restart found an unconfirmed stop-movement intent; no retry.",
                        observed,
                    )
                recovered = ProtectionPolicy(
                    **{**desired.to_dict(), "current_stop": intended_price}
                )
                return self.storage.update_protection_policy(
                    str(record["protection_policy_id"]),
                    status=pending_stage,
                    policy=recovered.to_dict(),
                    observation=observed.to_dict(),
                    action={"recovered_intent": pending_stage, "price": intended_price},
                    reason="Stop movement was confirmed during recovery without retry.",
                )
            if (
                protection.side is protective_side
                and protection.quantity == desired.quantity
                and protection.stop_price is not None
                and self._not_riskier(protection.stop_price, desired.initial_stop, desired.side)
            ):
                applied = ProtectionPolicy(
                    **{**desired.to_dict(), "current_stop": protection.stop_price}
                )
                return self.storage.update_protection_policy(
                    str(record["protection_policy_id"]),
                    status=str(record["status"])
                    if str(record["status"]) in {"BREAKEVEN", "TRAILING"}
                    else "PROTECTED",
                    policy=applied.to_dict(),
                    observation=observed.to_dict(),
                    reason="Observed demo stop protects the full position.",
                )
            return self._safe_stop(record, "Observed stop is divergent or partial.", observed)
        if protection.state is not ProtectionOrderState.NONE:
            return self._safe_stop(record, "Protection state is not provably absent.", observed)
        if ambiguous_initial:
            return self._safe_stop(
                record,
                "Restart found an unconfirmed initial-stop attempt; automatic retry is blocked.",
                observed,
            )
        policy_id = str(record["protection_policy_id"])
        self.storage.update_protection_policy(
            policy_id,
            status="APPLYING_INITIAL",
            policy=desired.to_dict(),
            observation=observed.to_dict(),
            action={"intent": "APPLY_INITIAL", "price": desired.initial_stop},
            reason="Initial stop intent persisted before the demo action.",
        )
        try:
            result = await self.adapter.apply_demo_stop(
                side=protective_side,
                quantity=desired.quantity,
                stop_price=desired.initial_stop,
                timeframe=desired.timeframe,
            )
        except Exception as exc:
            return self._safe_stop(
                record, f"Initial stop became ambiguous ({type(exc).__name__}); no retry."
            )
        if not self._confirmed_stop(result.post, desired, desired.initial_stop):
            return self._safe_stop(record, "Initial stop postcondition is divergent.", result.post)
        applied = ProtectionPolicy(**{**desired.to_dict(), "current_stop": desired.initial_stop})
        return self.storage.update_protection_policy(
            policy_id,
            status="PROTECTED",
            policy=applied.to_dict(),
            observation=result.post.to_dict(),
            action=result.to_dict(),
            reason="Initial demo stop is confirmed.",
        )

    async def manage(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        async with self._lock:
            record = await self._protect_filled_unlocked(operation)
            if str(record["status"]) not in {"PROTECTED", "BREAKEVEN", "TRAILING"}:
                return record
            policy = ProtectionPolicy.from_dict(record["policy"])
            observed = await self._observe_or_safe(record, policy)
            if isinstance(observed, dict):
                return observed
            if (reason := self._position_reason(observed, policy)) is not None:
                return self._safe_stop(record, reason, observed)
            if not self._confirmed_stop(observed, policy, policy.current_stop):
                return self._safe_stop(
                    record, "Current stop is absent, partial, or divergent.", observed
                )
            candle = observed.closed_candle
            decision = trailing_decision(
                policy,
                stage=str(record["status"]),
                candle_timeframe=candle.timeframe,
                candle_closed_at=candle.closed_at,
                candle_open=candle.open_price,
                candle_close=candle.close_price,
            )
            policy_id = str(record["protection_policy_id"])
            if decision.target_stop is None:
                return self.storage.update_protection_policy(
                    policy_id,
                    status=str(record["status"]),
                    policy=decision.policy.to_dict(),
                    observation=observed.to_dict(),
                    reason=decision.reason,
                )
            intent = ProtectionPolicy(
                **{**decision.policy.to_dict(), "current_stop": decision.target_stop}
            )
            self.storage.update_protection_policy(
                policy_id,
                status=str(record["status"]),
                policy=decision.policy.to_dict(),
                observation=observed.to_dict(),
                action={"intent": decision.stage, "price": decision.target_stop},
                reason="Stop movement intent persisted before the demo action.",
            )
            side = TradeSide.SELL if policy.side == "LONG" else TradeSide.BUY
            try:
                result = await self.adapter.apply_demo_stop(
                    side=side,
                    quantity=policy.quantity,
                    stop_price=decision.target_stop,
                    timeframe=policy.timeframe,
                )
            except Exception as exc:
                return self._safe_stop(
                    record, f"Stop movement became ambiguous ({type(exc).__name__}); no retry."
                )
            if not self._confirmed_stop(result.post, intent, decision.target_stop):
                return self._safe_stop(
                    record, "Stop movement postcondition is divergent.", result.post
                )
            return self.storage.update_protection_policy(
                policy_id,
                status=decision.stage,
                policy=intent.to_dict(),
                observation=result.post.to_dict(),
                action=result.to_dict(),
                reason=decision.reason,
            )

    async def recover_all(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [
                await self._protect_filled_unlocked(operation)
                for operation in self.storage.filled_operations_requiring_recovery()
            ]

    async def emergency_stop(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        async with self._lock:
            record = self.storage.get_protection_policy(str(operation["operation_id"]))
            if record is None:
                record = self.storage.create_protection_policy(
                    str(operation["operation_id"]), policy_from_operation(operation).to_dict()
                )
            policy = ProtectionPolicy.from_dict(record["policy"])
            emergency = self.storage.create_emergency_operation(
                str(record["protection_policy_id"]),
                {"operation_id": operation["operation_id"], "policy": policy.to_dict()},
            )
            if str(emergency["status"]) != "CREATED":
                return emergency
            if self.mode is not ExecutionMode.DEMO:
                return self.storage.update_emergency_operation(
                    str(emergency["emergency_operation_id"]),
                    status="AMBIGUOUS",
                    reason="Emergency action is only available in DEMO mode.",
                )
            self.storage.update_protection_policy(
                str(record["protection_policy_id"]),
                status="EMERGENCY_PENDING",
                reason="Human-triggered demo emergency is in progress.",
            )
            emergency_id = str(emergency["emergency_operation_id"])
            try:
                observed = await self.adapter.observe_protection_state(policy.timeframe)
                if observed.account.account is not AccountEnvironment.DEMO:
                    raise ValueError("Account is not provably demo.")
                if observed.protection.state is ProtectionOrderState.ACTIVE:
                    if observed.protection.order_id is None:
                        raise ValueError("Protection order identifier is missing.")
                    self.storage.update_emergency_operation(
                        emergency_id,
                        status="CANCEL_UNCONFIRMED",
                        result=observed.to_dict(),
                        reason="One cancellation attempt is in progress.",
                    )
                    cancelled = await self.adapter.cancel_demo_protection(
                        order_id=observed.protection.order_id, timeframe=policy.timeframe
                    )
                    observed = cancelled.post
                    if observed.protection.state not in {
                        ProtectionOrderState.NONE,
                        ProtectionOrderState.CANCELLED,
                    }:
                        raise ValueError("Protection cancellation is unconfirmed.")
                if observed.account.position.state in {PositionState.LONG, PositionState.SHORT}:
                    self.storage.update_emergency_operation(
                        emergency_id,
                        status="CLOSE_UNCONFIRMED",
                        result=observed.to_dict(),
                        reason="One demo close attempt is in progress.",
                    )
                    side = (
                        TradeSide.SELL
                        if observed.account.position.state is PositionState.LONG
                        else TradeSide.BUY
                    )
                    closed = await self.adapter.close_demo_position(
                        side=side, quantity=policy.quantity, timeframe=policy.timeframe
                    )
                    observed = closed.post
                if observed.account.position.state is not PositionState.FLAT:
                    raise ValueError("Demo position close is unconfirmed.")
            except Exception as exc:
                self.storage.update_protection_policy(
                    str(record["protection_policy_id"]),
                    status="EMERGENCY_UNCONFIRMED",
                    reason=f"Emergency is ambiguous ({type(exc).__name__}); no retry.",
                )
                return self.storage.update_emergency_operation(
                    emergency_id,
                    status="AMBIGUOUS",
                    reason=f"Emergency is ambiguous ({type(exc).__name__}); no retry.",
                )
            self.storage.update_protection_policy(
                str(record["protection_policy_id"]),
                status="CLOSED",
                observation=observed.to_dict(),
                reason="Demo position and protection are confirmed closed.",
            )
            return self.storage.update_emergency_operation(
                emergency_id,
                status="CONFIRMED",
                result=observed.to_dict(),
                reason="Demo emergency is confirmed complete.",
            )

    async def _observe_or_safe(
        self, record: Mapping[str, Any], policy: ProtectionPolicy
    ) -> ProtectionStateObservation | dict[str, Any]:
        try:
            return await self.adapter.observe_protection_state(policy.timeframe)
        except Exception as exc:
            return self._safe_stop(record, f"Protection observation failed ({type(exc).__name__}).")

    def _safe_stop(
        self,
        record: Mapping[str, Any],
        reason: str,
        observation: ProtectionStateObservation | None = None,
    ) -> dict[str, Any]:
        return self.storage.update_protection_policy(
            str(record["protection_policy_id"]),
            status="SAFE_STOP",
            observation=observation.to_dict() if observation else None,
            reason=reason,
        )

    @staticmethod
    def _position_reason(
        observed: ProtectionStateObservation, policy: ProtectionPolicy
    ) -> str | None:
        expected = PositionState.LONG if policy.side == "LONG" else PositionState.SHORT
        if observed.account.account is not AccountEnvironment.DEMO:
            return "The applied account is not provably DEMO."
        if observed.account.position.state is PositionState.FLAT:
            return "The persisted filled operation has no matching open position."
        if (
            observed.account.position.state is not expected
            or observed.account.position.quantity != policy.quantity
        ):
            return "Position side or quantity is unknown or divergent."
        return None

    @staticmethod
    def _not_riskier(observed: float, baseline: float, side: str) -> bool:
        return observed >= baseline if side == "LONG" else observed <= baseline

    @staticmethod
    def _confirmed_stop(
        observed: ProtectionStateObservation,
        policy: ProtectionPolicy,
        expected_price: float | None,
    ) -> bool:
        expected_side = TradeSide.SELL if policy.side == "LONG" else TradeSide.BUY
        return (
            expected_price is not None
            and observed.protection.state is ProtectionOrderState.ACTIVE
            and observed.protection.side is expected_side
            and observed.protection.quantity == policy.quantity
            and _same_price(observed.protection.stop_price, expected_price, policy.tick_size)
        )
