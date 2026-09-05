from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from math import isfinite
from typing import Any

from providency.context import ConfluenceResult


class CandidateDecision(StrEnum):
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    tick_size: float
    tick_value: float
    stop_buffer_ticks: int
    min_rr: float
    max_trades: int = 3
    max_consecutive_losses: int = 2
    max_session_loss: float = 100.0


@dataclass(frozen=True, slots=True)
class SessionLimits:
    trades: int | None
    consecutive_losses: int | None
    loss: float | None


@dataclass(frozen=True, slots=True)
class TradeCandidate:
    candidate_id: str
    session_id: str
    symbol: str
    side: str
    pattern_id: str
    pattern_version: str
    configuration_version: str
    primary_capture_sha256: str
    context_capture_sha256: str
    entry: float | None
    stop: float | None
    quantity: int
    risk_amount: float | None
    reference_target: float | None
    reference_rr: float
    confluence: ConfluenceResult
    limits: SessionLimits
    decision: CandidateDecision
    blocking_reasons: tuple[str, ...]
    pattern_detection_id: str | None = None
    applied_state_id: str | None = None
    price_scale: dict[str, Any] | None = None
    configuration_snapshot: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["decision"] = self.decision.value
        payload["confluence"] = self.confluence.to_dict()
        return payload


def build_candidate(
    *,
    session_id: str,
    symbol: str,
    side: str,
    pattern_id: str,
    pattern_version: str,
    configuration_version: str,
    primary_capture_sha256: str,
    context_capture_sha256: str,
    pattern_high: float | None,
    pattern_low: float | None,
    quantity: int,
    confluence: ConfluenceResult,
    policy: RiskPolicy,
    limits: SessionLimits,
    pattern_detection_id: str | None = None,
    applied_state_id: str | None = None,
    price_scale: dict[str, Any] | None = None,
    configuration_snapshot: dict[str, Any] | None = None,
) -> TradeCandidate:
    reasons = list(confluence.blocking_reasons)
    if side not in {"LONG", "SHORT"}:
        reasons.append("Side is missing or unsupported.")
    if pattern_high is None or pattern_low is None:
        reasons.append("Pattern price extremes are missing.")
    elif not all(isfinite(value) for value in (pattern_high, pattern_low)):
        reasons.append("Pattern price extremes are invalid.")
    elif pattern_high <= pattern_low:
        reasons.append("Pattern high must be above pattern low.")
    if quantity <= 0:
        reasons.append("Configured quantity must be positive.")
    if policy.tick_size <= 0 or policy.tick_value <= 0 or policy.min_rr <= 0:
        reasons.append("Risk policy contains invalid values.")
    if limits.trades is None:
        reasons.append("Session trades are missing.")
    elif limits.trades < 0:
        reasons.append("Session trades are invalid.")
    elif limits.trades >= policy.max_trades:
        reasons.append("Maximum trades reached.")
    if limits.consecutive_losses is None:
        reasons.append("Consecutive losses are missing.")
    elif limits.consecutive_losses < 0:
        reasons.append("Consecutive losses are invalid.")
    elif limits.consecutive_losses >= policy.max_consecutive_losses:
        reasons.append("Maximum consecutive losses reached.")
    if limits.loss is None:
        reasons.append("Session loss is missing.")
    elif limits.loss < 0:
        reasons.append("Session loss is invalid.")
    elif limits.loss >= policy.max_session_loss:
        reasons.append("Maximum session loss reached.")

    entry: float | None = None
    stop: float | None = None
    risk_amount: float | None = None
    target: float | None = None
    if pattern_high is not None and pattern_low is not None and policy.tick_size > 0:
        buffer = policy.stop_buffer_ticks * policy.tick_size
        if side == "SHORT":
            entry = pattern_low - buffer
            stop = pattern_high + buffer
            target = entry - (stop - entry) * policy.min_rr
        elif side == "LONG":
            entry = pattern_high + buffer
            stop = pattern_low - buffer
            target = entry + (entry - stop) * policy.min_rr
        if entry is not None and stop is not None and policy.tick_value > 0 and quantity > 0:
            risk_amount = abs(stop - entry) / policy.tick_size * policy.tick_value * quantity

    base = TradeCandidate(
        candidate_id="",
        session_id=session_id,
        symbol=symbol,
        side=side,
        pattern_id=pattern_id,
        pattern_version=pattern_version,
        configuration_version=configuration_version,
        primary_capture_sha256=primary_capture_sha256,
        context_capture_sha256=context_capture_sha256,
        entry=entry,
        stop=stop,
        quantity=quantity,
        risk_amount=risk_amount,
        reference_target=target,
        reference_rr=policy.min_rr,
        confluence=confluence,
        limits=limits,
        decision=CandidateDecision.BLOCKED if reasons else CandidateDecision.ALLOWED,
        blocking_reasons=tuple(reasons),
        pattern_detection_id=pattern_detection_id,
        applied_state_id=applied_state_id,
        price_scale=price_scale,
        configuration_snapshot=configuration_snapshot,
    )
    canonical = json.dumps(base.to_dict(), separators=(",", ":"), sort_keys=True)
    return replace(base, candidate_id=hashlib.sha256(canonical.encode()).hexdigest())
