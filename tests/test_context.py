from providency.context import (
    ConfluenceStatus,
    analyze_context,
    extract_pivots,
)
from providency.patterns import Candle


def candles(values: list[tuple[float, float, float, float]]) -> list[Candle]:
    return [Candle(open=o, high=h, low=low, close=c) for o, h, low, c in values]


def test_context_reports_each_confluence_state_explicitly() -> None:
    result = analyze_context(
        side="SHORT",
        primary=candles([(10, 11, 9, 10), (9, 10, 8, 9), (8, 9, 7, 8)]),
        context=candles([(12, 13, 10, 11), (11, 12, 9, 10), (10, 11, 8, 9)]),
        short_ma=9,
        long_ma=10,
        moving_averages_enabled=True,
        context_family="BEARISH_REVERSAL",
        compatible_context_families=("BEARISH_REVERSAL",),
        support_resistance_tolerance=1.0,
    )

    assert result.items["trend"].status is ConfluenceStatus.PASS
    assert result.items["structure"].status is ConfluenceStatus.PASS
    assert result.items["context_timeframe"].status is ConfluenceStatus.PASS
    assert result.applicable_total >= 3


def test_missing_values_block_instead_of_passing() -> None:
    result = analyze_context(
        side="SHORT",
        primary=(),
        context=(),
        short_ma=None,
        long_ma=None,
        moving_averages_enabled=True,
        context_family=None,
        compatible_context_families=("BEARISH_REVERSAL",),
        support_resistance_tolerance=1.0,
    )

    assert result.items["trend"].status is ConfluenceStatus.MISSING
    assert result.items["structure"].status is ConfluenceStatus.MISSING
    assert result.items["support_resistance"].status is ConfluenceStatus.MISSING
    assert result.items["context_timeframe"].status is ConfluenceStatus.MISSING
    assert result.blocking_reasons


def test_disabled_moving_averages_are_not_applicable() -> None:
    result = analyze_context(
        side="SHORT",
        primary=candles([(10, 11, 9, 10), (9, 10, 8, 9), (8, 9, 7, 8)]),
        context=candles([(10, 11, 9, 10)]),
        short_ma=None,
        long_ma=None,
        moving_averages_enabled=False,
        context_family="BEARISH_REVERSAL",
        compatible_context_families=("BEARISH_REVERSAL",),
        support_resistance_tolerance=1.0,
    )

    assert result.items["trend"].status is ConfluenceStatus.NOT_APPLICABLE


def test_pivots_are_extracted_with_an_explicit_window() -> None:
    values = candles(
        [
            (1.5, 2, 1, 1.6),
            (2, 4, 2, 3),
            (2, 3, 1.5, 2.5),
            (3, 5, 2.5, 4),
            (1.5, 2, 1, 1.8),
        ]
    )

    pivots = extract_pivots(values, window=1)

    assert [(pivot.index, pivot.kind) for pivot in pivots] == [
        (1, "HIGH"),
        (2, "LOW"),
        (3, "HIGH"),
    ]
