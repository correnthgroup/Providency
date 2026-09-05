from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from providency.storage import Storage
from providency.vision import DETECTOR_VERSION


class ReviewLabel(StrEnum):
    TRUE_POSITIVE = "TRUE_POSITIVE"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    FALSE_NEGATIVE = "FALSE_NEGATIVE"
    TRUE_NEGATIVE = "TRUE_NEGATIVE"
    NO_DECISION = "NO_DECISION"
    OPERATIONAL_FAILURE = "OPERATIONAL_FAILURE"


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    label: ReviewLabel
    notes: str
    reviewer_id: str
    evidence_sha256: str
    evidence_path: str
    detection_id: str | None = None
    candidate_id: str | None = None

    def validate(self) -> None:
        if (self.detection_id is None) == (self.candidate_id is None):
            raise ValueError("A review must reference exactly one detection or candidate.")
        if not self.notes.strip() or len(self.notes.strip()) > 500:
            raise ValueError("Review notes must contain between 1 and 500 characters.")
        if not self.reviewer_id.startswith("local:") or len(self.reviewer_id) > 100:
            raise ValueError("Reviewer identity must be a short local identifier.")
        if len(self.evidence_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.evidence_sha256.lower()
        ):
            raise ValueError("Evidence hash must be a SHA-256 hex digest.")
        if not self.evidence_path.strip():
            raise ValueError("A sanitized evidence path is required.")


class ReviewService:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def create(self, request: ReviewRequest) -> dict[str, object]:
        request.validate()
        pattern_id: str
        pattern_version: str
        session_id: str | None
        expected_hash: str | None = None
        if request.detection_id is not None:
            detection = self.storage.get_pattern_detection(request.detection_id)
            if detection is None:
                raise ValueError("Pattern detection was not found.")
            pattern_id = str(detection["pattern_id"])
            pattern_version = str(detection["pattern_version"])
            session_id = (
                str(detection["session_id"]) if detection["session_id"] is not None else None
            )
            expected_hash = str(detection["screenshot_sha256"])
        else:
            candidate = self.storage.get_trade_candidate(str(request.candidate_id))
            if candidate is None:
                raise ValueError("Trade candidate was not found.")
            pattern_id = str(candidate.get("pattern_id", "bearish_engulfing"))
            pattern_version = str(candidate.get("pattern_version", "unknown"))
            session_id = str(candidate["session_id"])
            primary_hash = candidate.get("primary_capture_sha256")
            expected_hash = str(primary_hash) if primary_hash else None
        if expected_hash is not None and expected_hash != request.evidence_sha256:
            raise ValueError("Review evidence hash does not match the historical decision.")
        return self.storage.create_human_review(
            session_id=session_id,
            detection_id=request.detection_id,
            candidate_id=request.candidate_id,
            label=request.label.value,
            notes=request.notes.strip(),
            reviewer_id=request.reviewer_id,
            evidence_sha256=request.evidence_sha256.lower(),
            evidence_path=request.evidence_path,
            detector_version=DETECTOR_VERSION,
            pattern_id=pattern_id,
            pattern_version=pattern_version,
        )
