from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from providency.approvals import ApprovalError, ApprovalService
from providency.storage import Storage


def allowed_candidate(session_id: str) -> dict[str, object]:
    return {
        "candidate_id": "candidate-1",
        "session_id": session_id,
        "decision": "ALLOWED",
        "symbol": "BTC/BRL",
        "side": "SHORT",
        "quantity": 2,
        "entry": 100.0,
        "stop": 102.0,
        "risk_amount": 4.0,
        "reference_rr": 2.0,
        "confluence": {"passed_total": 5, "applicable_total": 5},
    }


def setup(tmp_path: Path, now: datetime) -> tuple[Storage, ApprovalService, dict[str, object]]:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    session = storage.start_session()
    candidate = allowed_candidate(str(session["id"]))
    storage.record_trade_candidate(
        candidate, max_trades=3, max_consecutive_losses=2, max_session_loss=100
    )
    service = ApprovalService(
        storage,
        chat_id=10,
        user_id=20,
        ttl_seconds=60,
        clock=lambda: now,
        id_factory=lambda: "opaque-approval-id",
    )
    return storage, service, candidate


def test_approval_snapshot_is_immutable_and_duplicate_response_is_rejected(tmp_path: Path) -> None:
    now = datetime(2026, 9, 5, 15, tzinfo=UTC)
    storage, service, candidate = setup(tmp_path, now)

    created = service.create(candidate)
    candidate["entry"] = 999.0
    approved = service.consume(created.yes_callback, chat_id=10, user_id=20)

    assert approved["status"] == "APPROVED"
    assert approved["candidate_snapshot"]["entry"] == 100.0
    with pytest.raises(ApprovalError, match="already answered"):
        service.consume(created.yes_callback, chat_id=10, user_id=20)
    assert len(storage.list_approvals()) == 1


def test_no_rejects_without_recheck(tmp_path: Path) -> None:
    now = datetime(2026, 9, 5, 15, tzinfo=UTC)
    _storage, service, candidate = setup(tmp_path, now)
    created = service.create(candidate)

    rejected = service.consume(created.no_callback, chat_id=10, user_id=20)

    assert rejected["status"] == "REJECTED"
    assert rejected["recheck"] is None


def test_wrong_identity_and_expired_callback_cannot_approve(tmp_path: Path) -> None:
    current = [datetime(2026, 9, 5, 15, tzinfo=UTC)]
    storage, service, candidate = setup(tmp_path, current[0])
    service.clock = lambda: current[0]
    created = service.create(candidate)

    with pytest.raises(ApprovalError, match="unauthorized"):
        service.consume(created.yes_callback, chat_id=11, user_id=20)
    current[0] += timedelta(seconds=61)
    with pytest.raises(ApprovalError, match="expired"):
        service.consume(created.yes_callback, chat_id=10, user_id=20)
    assert storage.list_approvals()[0]["status"] == "EXPIRED"


def test_concurrent_callbacks_produce_one_decision_and_one_would_execute(tmp_path: Path) -> None:
    now = datetime(2026, 9, 5, 15, tzinfo=UTC)
    storage, service, candidate = setup(tmp_path, now)
    created = service.create(candidate)

    def consume() -> str:
        try:
            return str(service.consume(created.yes_callback, chat_id=10, user_id=20)["status"])
        except ApprovalError:
            return "REJECTED_REPLAY"

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: consume(), range(2)))
    assert sorted(statuses) == ["APPROVED", "REJECTED_REPLAY"]

    finished = service.finish_recheck(
        created.approval["id"], passed=True, recheck={"result": "PASS"}, reason="Unchanged."
    )
    assert finished["status"] == "WOULD_EXECUTE"
    retried = service.finish_recheck(
        created.approval["id"], passed=True, recheck={"result": "PASS"}, reason="Retry."
    )
    assert retried["status"] == "WOULD_EXECUTE"
    assert [event["event_type"] for event in storage.list_events()].count("WOULD_EXECUTE") == 1


def test_stop_cancels_pending_proposal(tmp_path: Path) -> None:
    now = datetime(2026, 9, 5, 15, tzinfo=UTC)
    storage, service, candidate = setup(tmp_path, now)
    service.create(candidate)

    storage.stop_session()

    assert storage.list_approvals()[0]["status"] == "CANCELLED"


def test_stopped_session_cannot_create_or_complete_an_approval(tmp_path: Path) -> None:
    now = datetime(2026, 9, 5, 15, tzinfo=UTC)
    storage, service, candidate = setup(tmp_path, now)
    created = service.create(candidate)
    approved = service.consume(created.yes_callback, chat_id=10, user_id=20)
    storage.stop_session()

    result = service.finish_recheck(
        approved["id"], passed=True, recheck={"result": "PASS"}, reason="Too late."
    )

    assert result["status"] == "CANCELLED"
    assert "WOULD_EXECUTE" not in [event["event_type"] for event in storage.list_events()]


def test_candidate_from_stopped_session_cannot_be_proposed(tmp_path: Path) -> None:
    now = datetime(2026, 9, 5, 15, tzinfo=UTC)
    storage, service, candidate = setup(tmp_path, now)
    storage.stop_session()

    with pytest.raises(ValueError, match="session is not running"):
        service.create(candidate)
