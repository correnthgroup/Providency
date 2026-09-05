from __future__ import annotations

from collections import Counter
from typing import Any

from providency.review import ReviewLabel
from providency.storage import Storage


def _ratio(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": round(numerator / denominator, 6) if denominator else None,
    }

def pattern_metrics(
    storage: Storage,
    pattern_id: str,
    *,
    limit: int = 500,
    minimum_sample: int = 20,
) -> dict[str, Any]:
    reviews = storage.list_latest_human_reviews(
        pattern_id=pattern_id,
        limit=limit,
    )
    counts = Counter(str(review["label"]) for review in reviews)
    complete_counts = {label.value: counts[label.value] for label in ReviewLabel}
    true_positive = complete_counts[ReviewLabel.TRUE_POSITIVE.value]
    false_positive = complete_counts[ReviewLabel.FALSE_POSITIVE.value]
    false_negative = complete_counts[ReviewLabel.FALSE_NEGATIVE.value]
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    timestamps = [str(review["created_at"]) for review in reviews]
    return {
        "pattern_id": pattern_id,
        "reviewed_total": len(reviews),
        "counts": complete_counts,
        "precision": _ratio(true_positive, precision_denominator),
        "recall": _ratio(true_positive, recall_denominator),
        "sample_sufficient": precision_denominator >= minimum_sample,
        "minimum_sample": minimum_sample,
        "pattern_versions": sorted({str(item["pattern_version"]) for item in reviews}),
        "detector_versions": sorted({str(item["detector_version"]) for item in reviews}),
        "window": {
            "limit": limit,
            "oldest_reviewed_at": min(timestamps) if timestamps else None,
            "newest_reviewed_at": max(timestamps) if timestamps else None,
        },
    }
