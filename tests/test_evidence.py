import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image

from providency.evidence import EvidenceSanitizer, RedactionRegion, sanitize_metadata


def test_sanitization_redacts_regions_strips_metadata_and_uses_webp(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    image = Image.new("RGB", (120, 80), "white")
    image.save(source, pnginfo=None)
    sanitizer = EvidenceSanitizer(tmp_path / "sanitized")

    result = sanitizer.sanitize(
        source,
        source_sha256=None,
        redactions=(RedactionRegion(x=0, y=0, width=40, height=20),),
        metadata={"symbol": "BTC/BRL", "account_id": "secret", "balance": 1234},
    )

    assert result.path.suffix == ".webp"
    assert result.path.is_file()
    assert result.metadata == {
        "account_id": "[REDACTED]",
        "balance": "[REDACTED]",
        "symbol": "BTC/BRL",
    }
    with Image.open(result.path) as sanitized:
        assert sanitized.getpixel((10, 10)) == (0, 0, 0)
        assert not sanitized.getexif()


def test_retention_moves_expired_files_to_recoverable_trash(tmp_path: Path) -> None:
    sanitizer = EvidenceSanitizer(tmp_path / "sanitized")
    source = tmp_path / "source.png"
    Image.new("RGB", (120, 80), "white").save(source)
    result = sanitizer.sanitize(source, source_sha256=None)
    old = datetime.now(UTC) - timedelta(days=40)
    os.utime(result.path, (old.timestamp(), old.timestamp()))

    moved = sanitizer.apply_retention(retention_days=30, now=datetime.now(UTC))

    assert len(moved) == 1
    assert not result.path.exists()
    assert moved[0].is_file()
    assert moved[0].parent.name == ".trash"


def test_recursive_metadata_sanitization_removes_sensitive_values() -> None:
    assert sanitize_metadata(
        {"safe": {"timeframe": "15m"}, "telegram": {"chat_id": 99}, "token": "abc"}
    ) == {
        "safe": {"timeframe": "15m"},
        "telegram": {"chat_id": "[REDACTED]"},
        "token": "[REDACTED]",
    }
