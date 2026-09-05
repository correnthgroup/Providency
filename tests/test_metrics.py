from pathlib import Path

from providency.metrics import pattern_metrics
from providency.review import ReviewLabel, ReviewRequest, ReviewService
from providency.storage import Storage


def test_metrics_use_latest_revision_and_expose_denominators_and_versions(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "providency.db")
    storage.initialize()
    service = ReviewService(storage)
    for index, label in enumerate(
        (
            ReviewLabel.TRUE_POSITIVE,
            ReviewLabel.FALSE_POSITIVE,
            ReviewLabel.FALSE_NEGATIVE,
            ReviewLabel.TRUE_NEGATIVE,
            ReviewLabel.NO_DECISION,
        )
    ):
        detection_id = storage.record_pattern_detection(
            session_id=None,
            screenshot_sha256=f"{index + 1:064x}",
            screenshot_path=str(tmp_path / f"{index}.webp"),
            pattern_id="bearish_engulfing",
            pattern_version="0.1.0",
            result="MATCH" if index < 2 else "NO_MATCH",
            reason="review fixture",
            evidence={"candles": []},
        )
        service.create(
            ReviewRequest(
                detection_id=detection_id,
                label=label,
                notes="Reviewed fixture.",
                reviewer_id="local:operator",
                evidence_sha256=f"{index + 1:064x}",
                evidence_path=str(tmp_path / "sanitized" / f"{index}.webp"),
            )
        )

    result = pattern_metrics(storage, "bearish_engulfing", limit=100, minimum_sample=20)

    assert result["counts"]["TRUE_POSITIVE"] == 1
    assert result["precision"] == {"numerator": 1, "denominator": 2, "value": 0.5}
    assert result["recall"] == {"numerator": 1, "denominator": 2, "value": 0.5}
    assert result["pattern_versions"] == ["0.1.0"]
    assert result["detector_versions"]
    assert result["reviewed_total"] == 5
    assert result["sample_sufficient"] is False
    assert result["window"]["limit"] == 100
