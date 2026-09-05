from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from math import isfinite
from typing import Any

from providency.patterns import Candle


class PriceScaleError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PriceAnchor:
    y: float
    price: float
    source: str


@dataclass(frozen=True, slots=True)
class PriceScale:
    anchors: tuple[PriceAnchor, ...]
    tick_size: float
    slope: float
    intercept: float
    max_anchor_error: float

    @classmethod
    def from_anchors(cls, anchors: Sequence[PriceAnchor], *, tick_size: float) -> PriceScale:
        selected = tuple(anchors)
        if len(selected) < 2:
            raise PriceScaleError("At least two visible price anchors are required.")
        if not isfinite(tick_size) or tick_size <= 0:
            raise PriceScaleError("Tick size must be positive.")
        if any(not isfinite(anchor.y) or not isfinite(anchor.price) for anchor in selected):
            raise PriceScaleError("Price anchors must be finite.")
        mean_y = sum(anchor.y for anchor in selected) / len(selected)
        mean_price = sum(anchor.price for anchor in selected) / len(selected)
        denominator = sum((anchor.y - mean_y) ** 2 for anchor in selected)
        if denominator == 0:
            raise PriceScaleError("Price anchors need distinct vertical coordinates.")
        slope = (
            sum((anchor.y - mean_y) * (anchor.price - mean_price) for anchor in selected)
            / denominator
        )
        if slope >= 0:
            raise PriceScaleError("Visible price must decrease toward the bottom of the chart.")
        intercept = mean_price - slope * mean_y
        max_error = max(abs((slope * anchor.y + intercept) - anchor.price) for anchor in selected)
        if max_error > tick_size:
            raise PriceScaleError("Price anchors are not linear within the configured tick.")
        return cls(selected, tick_size, slope, intercept, max_error)

    def price_at(self, y: float) -> float:
        raw = self.slope * y + self.intercept
        return round(raw / self.tick_size) * self.tick_size

    def y_at(self, price: float) -> float:
        return (price - self.intercept) / self.slope

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchors": [asdict(anchor) for anchor in self.anchors],
            "tick_size": self.tick_size,
            "slope": self.slope,
            "intercept": self.intercept,
            "max_anchor_error": self.max_anchor_error,
        }


class ConfluenceStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    MISSING = "MISSING"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class Pivot:
    index: int
    kind: str
    price: float


def extract_pivots(candles: Sequence[Candle], *, window: int) -> tuple[Pivot, ...]:
    if window < 1:
        raise ValueError("Pivot window must be positive.")
    pivots: list[Pivot] = []
    for index in range(window, len(candles) - window):
        candle = candles[index]
        neighbors = tuple(candles[index - window : index]) + tuple(
            candles[index + 1 : index + window + 1]
        )
        if all(candle.high > neighbor.high for neighbor in neighbors):
            pivots.append(Pivot(index, "HIGH", candle.high))
        if all(candle.low < neighbor.low for neighbor in neighbors):
            pivots.append(Pivot(index, "LOW", candle.low))
    return tuple(pivots)


@dataclass(frozen=True, slots=True)
class ConfluenceItem:
    status: ConfluenceStatus
    reason: str
    evidence: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


@dataclass(frozen=True, slots=True)
class ConfluenceResult:
    items: dict[str, ConfluenceItem]

    @property
    def applicable_total(self) -> int:
        return sum(
            item.status is not ConfluenceStatus.NOT_APPLICABLE for item in self.items.values()
        )

    @property
    def passed_total(self) -> int:
        return sum(item.status is ConfluenceStatus.PASS for item in self.items.values())

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        return tuple(
            f"{name}: {item.reason}"
            for name, item in self.items.items()
            if item.status in {ConfluenceStatus.FAIL, ConfluenceStatus.MISSING}
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": {name: item.to_dict() for name, item in self.items.items()},
            "passed_total": self.passed_total,
            "applicable_total": self.applicable_total,
            "blocking_reasons": list(self.blocking_reasons),
        }


def _trend(
    side: str,
    short_ma: float | None,
    long_ma: float | None,
    enabled: bool,
) -> ConfluenceItem:
    if not enabled:
        return ConfluenceItem(ConfluenceStatus.NOT_APPLICABLE, "Moving averages are disabled.")
    if short_ma is None or long_ma is None:
        return ConfluenceItem(
            ConfluenceStatus.MISSING, "Both enabled moving-average values are required."
        )
    compatible = short_ma < long_ma if side == "SHORT" else short_ma > long_ma
    return ConfluenceItem(
        ConfluenceStatus.PASS if compatible else ConfluenceStatus.FAIL,
        "Moving-average alignment is compatible."
        if compatible
        else "Moving-average alignment is incompatible.",
        {"short": short_ma, "long": long_ma, "side": side},
    )


def _structure(side: str, candles: Sequence[Candle]) -> ConfluenceItem:
    if len(candles) < 3:
        return ConfluenceItem(
            ConfluenceStatus.MISSING, "At least three candles are required for structure."
        )
    selected = candles[-3:]
    bearish = all(
        a.high > b.high and a.low > b.low
        for a, b in zip(selected, selected[1:], strict=False)
    )
    bullish = all(
        a.high < b.high and a.low < b.low
        for a, b in zip(selected, selected[1:], strict=False)
    )
    compatible = bearish if side == "SHORT" else bullish
    pivots = extract_pivots(candles, window=1)
    return ConfluenceItem(
        ConfluenceStatus.PASS if compatible else ConfluenceStatus.FAIL,
        "Recent highs and lows are compatible with the side."
        if compatible
        else "Recent highs and lows are not compatible with the side.",
        {
            "highs": [item.high for item in selected],
            "lows": [item.low for item in selected],
            "pivots": [asdict(pivot) for pivot in pivots],
        },
    )


def _support_resistance(side: str, candles: Sequence[Candle], tolerance: float) -> ConfluenceItem:
    if len(candles) < 3:
        return ConfluenceItem(
            ConfluenceStatus.MISSING,
            "At least three candles are required for support/resistance.",
        )
    if tolerance <= 0:
        return ConfluenceItem(ConfluenceStatus.MISSING, "Region tolerance is invalid.")
    selected = candles[-3:]
    current = selected[-1].close
    reference = (
        max(item.high for item in selected[:-1])
        if side == "SHORT"
        else min(item.low for item in selected[:-1])
    )
    distance = abs(current - reference)
    compatible = distance <= tolerance
    return ConfluenceItem(
        ConfluenceStatus.PASS if compatible else ConfluenceStatus.FAIL,
        "Price is inside the compatible structure region."
        if compatible
        else "Price is outside the compatible structure region.",
        {"current": current, "reference": reference, "distance": distance},
    )


def _context_family(family: str | None, compatible_families: Sequence[str]) -> ConfluenceItem:
    if family is None:
        return ConfluenceItem(
            ConfluenceStatus.MISSING, "Context timeframe confirmation is missing."
        )
    compatible = family in compatible_families
    return ConfluenceItem(
        ConfluenceStatus.PASS if compatible else ConfluenceStatus.FAIL,
        "Context family is compatible." if compatible else "Context family is incompatible.",
        {"family": family, "compatible_families": list(compatible_families)},
    )


def analyze_context(
    *,
    side: str,
    primary: Sequence[Candle],
    context: Sequence[Candle],
    short_ma: float | None,
    long_ma: float | None,
    moving_averages_enabled: bool,
    context_family: str | None,
    compatible_context_families: Sequence[str],
    support_resistance_tolerance: float,
) -> ConfluenceResult:
    return ConfluenceResult(
        items={
            "trend": _trend(side, short_ma, long_ma, moving_averages_enabled),
            "structure": _structure(side, context),
            "support_resistance": _support_resistance(side, primary, support_resistance_tolerance),
            "context_timeframe": _context_family(context_family, compatible_context_families),
        }
    )
