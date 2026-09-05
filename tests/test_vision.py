from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from synthetic_chart import BEARISH, BULLISH, bearish_engulfing_chart, write_chart

from providency.patterns import PatternMatcher, PatternPackage, PatternStatus
from providency.vision import CandleDetector, VisionDetectionError

ROOT = Path(__file__).parents[1]
REAL_FIXTURE = ROOT / "tests" / "fixtures" / "vector" / "no_match_real_001.png"


def test_detector_extracts_boxes_relative_ohlc_and_match(tmp_path: Path) -> None:
    screenshot = bearish_engulfing_chart(tmp_path / "match.png")
    window = CandleDetector().detect(screenshot)

    assert len(window.candles) == 5
    assert [item.color for item in window.candles] == [
        "BULLISH",
        "BULLISH",
        "BULLISH",
        "BULLISH",
        "BEARISH",
    ]
    assert all(item.box.body_width == 9 for item in window.candles)
    assert window.candles[-1].candle.open > window.candles[-2].candle.close
    assert window.candles[-1].candle.close < window.candles[-2].candle.open

    matcher = PatternMatcher(
        PatternPackage.load(ROOT / "patterns" / "bearish_engulfing" / "pattern.yaml")
    )
    result = matcher.evaluate(
        [item.candle for item in window.candles[-2:]],
        [item.candle.close for item in window.candles[:3]],
    )
    assert result.status is PatternStatus.MATCH


@pytest.mark.parametrize("kind", ["missing", "small", "oversized", "blank"])
def test_detector_rejects_unusable_images(tmp_path: Path, kind: str) -> None:
    path = tmp_path / f"{kind}.png"
    if kind == "small":
        Image.new("RGB", (100, 80), "black").save(path)
    elif kind == "oversized":
        Image.new("RGB", (8200, 120), "black").save(path)
    elif kind == "blank":
        Image.new("RGB", (320, 200), "black").save(path)

    with pytest.raises(VisionDetectionError):
        CandleDetector().detect(path)


def test_detector_rejects_overlapping_candles(tmp_path: Path) -> None:
    path = write_chart(
        tmp_path / "overlap.png",
        [
            (100, 70, 80, 100, 110, BULLISH),
            (104, 72, 82, 102, 112, BEARISH),
        ],
    )

    with pytest.raises(VisionDetectionError, match="overlaps"):
        CandleDetector().detect(path)


def test_incomplete_compatible_window_is_forming(tmp_path: Path) -> None:
    path = write_chart(
        tmp_path / "forming.png",
        [
            (40, 135, 140, 150, 156, BULLISH),
            (90, 120, 126, 136, 143, BULLISH),
            (140, 105, 111, 121, 128, BULLISH),
            (190, 85, 92, 103, 110, BULLISH),
        ],
    )
    window = CandleDetector().detect(path)
    matcher = PatternMatcher(
        PatternPackage.load(ROOT / "patterns" / "bearish_engulfing" / "pattern.yaml")
    )

    result = matcher.evaluate(
        [window.candles[-1].candle],
        [item.candle.close for item in window.candles[:3]],
    )

    assert result.status is PatternStatus.FORMING


def test_real_sanitized_vector_fixture_is_no_match() -> None:
    window = CandleDetector().detect(REAL_FIXTURE)
    matcher = PatternMatcher(
        PatternPackage.load(ROOT / "patterns" / "bearish_engulfing" / "pattern.yaml")
    )
    selected = window.candles[-5:]

    result = matcher.evaluate(
        [item.candle for item in selected[-2:]],
        [item.candle.close for item in selected[:3]],
    )

    assert len(window.candles) == 176
    assert result.status is PatternStatus.NO_MATCH
    assert result.reason == "Preceding closes are not strictly ascending."
