from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, ImageDraw

REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {
    "account",
    "account_id",
    "balance",
    "chat_id",
    "credential",
    "email",
    "name",
    "password",
    "phone",
    "secret",
    "token",
    "user_id",
}


@dataclass(frozen=True, slots=True)
class RedactionRegion:
    x: int
    y: int
    width: int
    height: int

    def validate(self, image_width: int, image_height: int) -> None:
        if self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("Redaction regions must have positive in-image dimensions.")
        if self.x + self.width > image_width or self.y + self.height > image_height:
            raise ValueError("Redaction region exceeds the screenshot bounds.")


@dataclass(frozen=True, slots=True)
class SanitizedEvidence:
    path: Path
    sha256: str
    source_sha256: str
    metadata: dict[str, Any]
    redactions: tuple[RedactionRegion, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "source_sha256": self.source_sha256,
            "metadata": self.metadata,
            "redactions": [
                {"x": item.x, "y": item.y, "width": item.width, "height": item.height}
                for item in self.redactions
            ],
        }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize_metadata(value: Any, *, key: str = "") -> Any:
    normalized_key = key.lower().replace("-", "_")
    if normalized_key in SENSITIVE_KEYS or any(
        normalized_key.startswith(f"{sensitive}_")
        or normalized_key.endswith(f"_{sensitive}")
        for sensitive in SENSITIVE_KEYS
    ):
        return REDACTED
    if isinstance(value, dict):
        return {
            str(item_key): sanitize_metadata(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_metadata(item) for item in value]
    return value


class EvidenceSanitizer:
    def __init__(self, destination: Path) -> None:
        self.destination = destination.resolve()

    def sanitize(
        self,
        source: Path,
        *,
        source_sha256: str | None,
        redactions: tuple[RedactionRegion, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> SanitizedEvidence:
        resolved_source = source.resolve(strict=True)
        actual_source_hash = file_sha256(resolved_source)
        if source_sha256 is not None and actual_source_hash != source_sha256:
            raise ValueError("Source screenshot hash does not match the persisted evidence.")
        self.destination.mkdir(parents=True, exist_ok=True)
        temporary = self.destination / f".{uuid4().hex}.webp"
        try:
            with Image.open(resolved_source) as opened:
                image = opened.convert("RGB")
            draw = ImageDraw.Draw(image)
            for region in redactions:
                region.validate(image.width, image.height)
                draw.rectangle(
                    (region.x, region.y, region.x + region.width - 1, region.y + region.height - 1),
                    fill="black",
                )
            image.save(temporary, format="WEBP", lossless=True, exif=b"")
            sanitized_hash = file_sha256(temporary)
            destination = self.destination / f"{sanitized_hash}.webp"
            if destination.exists():
                temporary.unlink()
            else:
                temporary.replace(destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        clean_metadata = sanitize_metadata(metadata or {})
        if not isinstance(clean_metadata, dict):
            raise ValueError("Evidence metadata must be a mapping.")
        return SanitizedEvidence(
            path=destination,
            sha256=sanitized_hash,
            source_sha256=actual_source_hash,
            metadata=clean_metadata,
            redactions=redactions,
        )

    def apply_retention(
        self,
        *,
        retention_days: int,
        now: datetime | None = None,
    ) -> list[Path]:
        if retention_days < 1:
            raise ValueError("Evidence retention must be at least one day.")
        if not self.destination.exists():
            return []
        threshold = (now or datetime.now(UTC)) - timedelta(days=retention_days)
        trash = self.destination / ".trash"
        moved: list[Path] = []
        for path in sorted(self.destination.glob("*.webp")):
            modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            if modified >= threshold:
                continue
            trash.mkdir(exist_ok=True)
            target = trash / path.name
            if target.exists():
                target = trash / f"{path.stem}-{uuid4().hex}.webp"
            shutil.move(str(path), str(target))
            moved.append(target)
        return moved
