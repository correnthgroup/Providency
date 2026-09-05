from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from providency.patterns import (
    Candle,
    CandleMeasures,
    PatternMatcher,
    PatternPackage,
    PatternPackageError,
    PatternStatus,
    compare,
)

ROOT = Path(__file__).parents[1]
PACKAGE_DIR = ROOT / "patterns" / "bearish_engulfing"


@pytest.fixture
def matcher() -> PatternMatcher:
    return PatternMatcher(PatternPackage.load(PACKAGE_DIR / "pattern.yaml"))


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("positive/001.yaml", PatternStatus.MATCH),
        ("negative/001.yaml", PatternStatus.NO_MATCH),
        ("forming/001.yaml", PatternStatus.FORMING),
    ],
)
def test_bearish_engulfing_ohlc_fixtures(
    matcher: PatternMatcher, fixture: str, expected: PatternStatus
) -> None:
    payload: dict[str, Any] = yaml.safe_load((PACKAGE_DIR / fixture).read_text(encoding="utf-8"))
    candles = [Candle.from_mapping(item) for item in payload["candles"]]

    result = matcher.evaluate(candles, payload["preceding_closes"])

    assert result.status is expected
    assert result.reason
    assert len(result.measures) == len(candles)


def test_candle_measures_follow_predicate_contract() -> None:
    measures = CandleMeasures.from_candle(Candle(open=10, high=15, low=5, close=12))

    assert measures.range == 10
    assert measures.body_high == 12
    assert measures.body_low == 10
    assert measures.body_midpoint == 11
    assert measures.body_to_range == pytest.approx(0.2)
    assert measures.upper_wick_to_range == pytest.approx(0.3)
    assert measures.lower_wick_to_range == pytest.approx(0.5)
    assert measures.body_color == "BULLISH"


@pytest.mark.parametrize(
    ("operator", "actual", "expected", "outcome"),
    [
        ("EQ", "BULLISH", "BULLISH", True),
        ("GT", 3, 2, True),
        ("GTE", 3, 3, True),
        ("LT", 2, 3, True),
        ("LTE", 3, 3, True),
        ("BETWEEN_EXCLUSIVE", 2, (1, 3), True),
        ("BETWEEN_EXCLUSIVE", 1, (1, 3), False),
    ],
)
def test_supported_operators(operator: str, actual: Any, expected: Any, outcome: bool) -> None:
    assert compare(operator, actual, expected) is outcome


def test_invalid_ohlc_is_rejected() -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        Candle(open=10, high=9, low=5, close=8)


def test_loader_rejects_unknown_pattern(tmp_path: Path) -> None:
    package = tmp_path / "pattern.yaml"
    package.write_text(
        "id: unknown\nversion: 1.0.0\nenabled: true\nrecognition: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(PatternPackageError, match="Only bearish_engulfing"):
        PatternPackage.load(package)
