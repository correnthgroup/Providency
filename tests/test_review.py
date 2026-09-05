from pathlib import Path

import pytest

from providency.review import ReviewLabel, ReviewRequest, ReviewService
from providency.storage import Storage


def _detection(storage: Storage, tmp_path: Path) -> str:
    return storage.record_pattern_detection(
        session_id=None,
        screenshot_sha256="a" * 64,
        screenshot_path=str(tmp_path / "capture.webp"),
        pattern_id="bearish_engulfing",
        pattern_version="0.1.0",
        result="MATCH",
        reason="All rules passed.",
        evidence={"candles": []},
    )


def test_review_revisions_are_append_only_and_keep_the_historical_decision(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    detection_id = _detection(storage, tmp_path)
    service = ReviewService(storage)

    first = service.create(
        ReviewRequest(
            detection_id=detection_id,
            label=ReviewLabel.FALSE_POSITIVE,
            notes="Body boundary was read incorrectly.",
            reviewer_id="local:operator",
            evidence_sha256="a" * 64,
            evidence_path=str(tmp_path / "sanitized" / "capture.webp"),
        )
    )
    revised = service.create(
        ReviewRequest(
            detection_id=detection_id,
            label=ReviewLabel.TRUE_POSITIVE,
            notes="Second review after checking the closed candle.",
            reviewer_id="local:operator",
            evidence_sha256="a" * 64,
            evidence_path=str(tmp_path / "sanitized" / "capture.webp"),
        )
    )

    assert first["revision"] == 1
    assert revised["revision"] == 2
    assert revised["previous_review_id"] == first["id"]
    assert [item["label"] for item in storage.list_human_reviews()] == [
        "TRUE_POSITIVE",
        "FALSE_POSITIVE",
    ]
    assert storage.get_pattern_detection(detection_id)["result"] == "MATCH"


def test_review_rejects_a_mismatched_evidence_hash(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    detection_id = _detection(storage, tmp_path)

    with pytest.raises(ValueError, match="hash"):
        ReviewService(storage).create(
            ReviewRequest(
                detection_id=detection_id,
                label=ReviewLabel.FALSE_POSITIVE,
                notes="Mismatch must fail closed.",
                reviewer_id="local:operator",
                evidence_sha256="b" * 64,
                evidence_path=str(tmp_path / "sanitized" / "capture.webp"),
            )
        )


def test_candidate_review_uses_the_frozen_primary_capture_hash(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    session = storage.start_session()
    storage.record_trade_candidate(
        {
            "candidate_id": "candidate-1",
            "session_id": session["id"],
            "decision": "BLOCKED",
            "pattern_id": "bearish_engulfing",
            "pattern_version": "0.1.0",
            "primary_capture_sha256": "a" * 64,
        },
        max_trades=1,
        max_consecutive_losses=1,
        max_session_loss=1,
    )

    with pytest.raises(ValueError, match="hash"):
        ReviewService(storage).create(
            ReviewRequest(
                candidate_id="candidate-1",
                label=ReviewLabel.NO_DECISION,
                notes="Candidate evidence must stay linked.",
                reviewer_id="local:operator",
                evidence_sha256="b" * 64,
                evidence_path=str(tmp_path / "sanitized" / "candidate.webp"),
            )
        )
