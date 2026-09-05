from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

from providency.storage import Storage


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    WOULD_EXECUTE = "WOULD_EXECUTE"


class ApprovalError(RuntimeError):
    pass


def callback_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CreatedApproval:
    approval: dict[str, Any]
    yes_callback: str
    no_callback: str


class ApprovalService:
    def __init__(
        self,
        storage: Storage,
        *,
        chat_id: int,
        user_id: int,
        ttl_seconds: int,
        dry_run: bool = True,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.storage = storage
        self.chat_id = chat_id
        self.user_id = user_id
        self.ttl_seconds = ttl_seconds
        self.dry_run = dry_run
        self.clock = clock or (lambda: datetime.now(UTC))
        self.id_factory = id_factory or (lambda: str(uuid4()))

    def create(self, candidate: Mapping[str, Any]) -> CreatedApproval:
        if candidate.get("decision") != "ALLOWED":
            raise ApprovalError("Only an ALLOWED candidate can become a proposal.")
        if self.chat_id == 0 or self.user_id == 0 or self.ttl_seconds <= 0:
            raise ApprovalError("Telegram approval configuration is incomplete.")
        self.expire()
        existing = self.storage.get_approval_for_candidate(str(candidate["candidate_id"]))
        if existing is not None:
            callback_id = str(existing["id"])
            return CreatedApproval(
                existing, f"yes:{callback_id}", f"no:{callback_id}"
            )
        approval_id = self.id_factory()
        now = self.clock().astimezone(UTC)
        expires_at = now + timedelta(seconds=self.ttl_seconds)
        approval = self.storage.create_approval(
            approval_id=approval_id,
            candidate=candidate,
            chat_id=self.chat_id,
            user_id=self.user_id,
            callback_digest=callback_digest(approval_id),
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
        )
        callback_id = str(approval["id"])
        return CreatedApproval(approval, f"yes:{callback_id}", f"no:{callback_id}")

    def consume(self, callback_data: str, *, chat_id: int, user_id: int) -> dict[str, Any]:
        try:
            action, token = callback_data.split(":", 1)
        except ValueError as exc:
            raise ApprovalError("Telegram callback payload is invalid.") from exc
        if action not in {"yes", "no"} or not token:
            raise ApprovalError("Telegram callback payload is invalid.")
        status = ApprovalStatus.APPROVED if action == "yes" else ApprovalStatus.REJECTED
        result = self.storage.consume_approval(
            callback_digest=callback_digest(token),
            chat_id=chat_id,
            user_id=user_id,
            status=status.value,
            decided_at=self.clock().astimezone(UTC).isoformat(),
        )
        if result is None:
            raise ApprovalError("Approval is invalid, expired, already answered, or unauthorized.")
        return result

    def expire(self) -> int:
        return self.storage.expire_approvals(self.clock().astimezone(UTC).isoformat())

    def cancel_session(self, session_id: str, reason: str) -> int:
        return self.storage.cancel_pending_approvals(
            session_id=session_id,
            reason=reason,
            decided_at=self.clock().astimezone(UTC).isoformat(),
        )

    def finish_recheck(
        self,
        approval_id: str,
        *,
        passed: bool,
        recheck: Mapping[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        status = ApprovalStatus.WOULD_EXECUTE if passed else ApprovalStatus.CANCELLED
        result = self.storage.finish_approval_recheck(
            approval_id=approval_id,
            status=status.value,
            reason=reason,
            recheck=recheck,
            decided_at=self.clock().astimezone(UTC).isoformat(),
            emit_would_execute=self.dry_run,
        )
        if result is None:
            current = self.storage.get_approval(approval_id)
            if current is not None and current["status"] in {
                ApprovalStatus.CANCELLED.value,
                ApprovalStatus.WOULD_EXECUTE.value,
            }:
                return current
            raise ApprovalError("Approval cannot complete recheck from its current state.")
        return result


def render_proposal(approval: Mapping[str, Any], execution_mode: str = "DRY_RUN") -> str:
    candidate = approval["candidate_snapshot"]
    confluence = candidate["confluence"]
    expiry = str(approval["expires_at"])
    return "\n".join(
        (
            f"Providency — POSSIBLE {execution_mode} OPERATION",
            "",
            f"Proposal: {approval['id']}",
            f"Symbol: {candidate['symbol']}",
            f"Side: {candidate['side']}",
            f"Quantity: {candidate['quantity']}",
            f"Entry: {candidate['entry']}",
            f"Stop: {candidate['stop']}",
            f"Risk: {candidate['risk_amount']}",
            f"Reference RR: {candidate['reference_rr']}",
            f"Confluence: {confluence['passed_total']}/{confluence['applicable_total']}",
            f"Expires at: {expiry}",
            "",
            (
                "Approve? A new capture and full preflight will run before "
                + ("one demo submission." if execution_mode == "DEMO" else "WOULD_EXECUTE.")
            ),
        )
    )
