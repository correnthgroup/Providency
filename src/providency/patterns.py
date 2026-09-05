from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class PatternStatus(StrEnum):
    MATCH = "MATCH"
    FORMING = "FORMING"
    NO_MATCH = "NO_MATCH"


class PatternPackageError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Candle:
    open: float
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close)
        if not all(isinstance(value, (int, float)) for value in values):
            raise ValueError("OHLC values must be numeric.")
        if self.high <= self.low:
            raise ValueError("Candle range must be positive.")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("Candle OHLC values are inconsistent.")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> Candle:
        try:
            values = {field: float(payload[field]) for field in ("open", "high", "low", "close")}
            return cls(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Candle requires valid open, high, low and close values.") from exc


@dataclass(frozen=True, slots=True)
class CandleMeasures:
    range: float
    body_high: float
    body_low: float
    body_midpoint: float
    body_to_range: float
    upper_wick_to_range: float
    lower_wick_to_range: float
    body_color: str

    @classmethod
    def from_candle(cls, candle: Candle) -> CandleMeasures:
        candle_range = candle.high - candle.low
        body_high = max(candle.open, candle.close)
        body_low = min(candle.open, candle.close)
        if candle.close > candle.open:
            color = "BULLISH"
        elif candle.close < candle.open:
            color = "BEARISH"
        else:
            color = "DOJI"
        return cls(
            range=candle_range,
            body_high=body_high,
            body_low=body_low,
            body_midpoint=(candle.open + candle.close) / 2,
            body_to_range=abs(candle.close - candle.open) / candle_range,
            upper_wick_to_range=(candle.high - body_high) / candle_range,
            lower_wick_to_range=(body_low - candle.low) / candle_range,
            body_color=color,
        )


@dataclass(frozen=True, slots=True)
class CandleBox:
    x: int
    y: int
    width: int
    height: int
    body_x: int
    body_y: int
    body_width: int
    body_height: int


@dataclass(frozen=True, slots=True)
class VisualCandle:
    candle: Candle
    box: CandleBox
    color: str


@dataclass(frozen=True, slots=True)
class PatternEvidence:
    screenshot_sha256: str
    screenshot_path: str
    image_width: int
    image_height: int
    candles: tuple[VisualCandle, ...]


@dataclass(frozen=True, slots=True)
class PatternMatchResult:
    pattern_id: str
    pattern_version: str
    status: PatternStatus
    reason: str
    candles: tuple[Candle, ...]
    measures: tuple[CandleMeasures, ...]
    evidence: PatternEvidence | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


@dataclass(frozen=True, slots=True)
class Rule:
    field: str
    operator: str
    value: Any
    candle: int | None = None


@dataclass(frozen=True, slots=True)
class PatternPackage:
    id: str
    version: str
    enabled: bool
    sequence_candles: int
    preceding_candles: int
    context_rules: tuple[Rule, ...]
    sequence_rules: tuple[Rule, ...]
    family: str
    compatible_context_families: tuple[str, ...]

    @classmethod
    def load(cls, path: Path) -> PatternPackage:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise PatternPackageError(f"Cannot read pattern package: {path}.") from exc
        root = _mapping(raw, "pattern.yaml")
        pattern_id = root.get("id")
        version = root.get("version")
        if pattern_id != "bearish_engulfing" or not isinstance(version, str):
            raise PatternPackageError(
                "Only bearish_engulfing with an explicit version is supported."
            )
        recognition = _mapping(root.get("recognition"), "recognition")
        context = _mapping(recognition.get("context", {}), "recognition.context")
        sequence = _mapping(recognition.get("sequence"), "recognition.sequence")
        context_rules = _rules(context.get("rules", []), context=True)
        sequence_rules = _rules(sequence.get("rules", []), context=False)
        try:
            sequence_candles = int(sequence["candles"])
            preceding_candles = int(context.get("preceding_candles", 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise PatternPackageError("Pattern candle counts must be integers.") from exc
        if sequence_candles != 2 or preceding_candles < 0:
            raise PatternPackageError("bearish_engulfing must use two sequence candles.")
        return cls(
            id=pattern_id,
            version=version,
            enabled=root.get("enabled") is True,
            sequence_candles=sequence_candles,
            preceding_candles=preceding_candles,
            context_rules=context_rules,
            sequence_rules=sequence_rules,
            family=str(root.get("family", "")),
            compatible_context_families=tuple(
                str(value) for value in root.get("compatible_context_families", [])
            ),
        )


SUPPORTED_FIELDS = {
    "OPEN",
    "HIGH",
    "LOW",
    "CLOSE",
    "RANGE",
    "BODY_HIGH",
    "BODY_LOW",
    "BODY_MIDPOINT",
    "BODY_TO_RANGE",
    "UPPER_WICK_TO_RANGE",
    "LOWER_WICK_TO_RANGE",
    "BODY_COLOR",
}
SUPPORTED_OPERATORS = {"EQ", "GT", "GTE", "LT", "LTE", "BETWEEN_EXCLUSIVE"}


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PatternPackageError(f"{label} must be a mapping.")
    return value


def _rules(value: Any, *, context: bool) -> tuple[Rule, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise PatternPackageError("Pattern rules must be a list.")
    rules: list[Rule] = []
    for item in value:
        raw = _mapping(item, "rule")
        field = raw.get("field")
        operator = raw.get("operator")
        candle = raw.get("candle")
        if context:
            if field != "CLOSE_SEQUENCE" or operator != "EQ":
                raise PatternPackageError("Unsupported context predicate.")
        elif field not in SUPPORTED_FIELDS or operator not in SUPPORTED_OPERATORS:
            raise PatternPackageError(f"Unsupported predicate: {field} {operator}.")
        if not context and not isinstance(candle, int):
            raise PatternPackageError("Sequence rules require an integer candle index.")
        rules.append(Rule(str(field), str(operator), raw.get("value"), candle))
    return tuple(rules)


def compare(operator: str, actual: Any, expected: Any) -> bool:
    if operator == "EQ":
        return bool(actual == expected)
    if not isinstance(actual, (int, float)) or isinstance(actual, bool):
        return False
    if operator == "BETWEEN_EXCLUSIVE":
        if not isinstance(expected, tuple) or len(expected) != 2:
            return False
        lower, upper = expected
        return bool(lower < actual and actual < upper)
    if not isinstance(expected, (int, float)) or isinstance(expected, bool):
        return False
    comparisons = {
        "GT": actual > expected,
        "GTE": actual >= expected,
        "LT": actual < expected,
        "LTE": actual <= expected,
    }
    return comparisons.get(operator, False)


class PatternMatcher:
    def __init__(self, package: PatternPackage) -> None:
        if not package.enabled:
            raise PatternPackageError("bearish_engulfing is disabled.")
        self.package = package

    def evaluate(
        self,
        candles: Sequence[Candle],
        preceding_closes: Sequence[float],
        *,
        evidence: PatternEvidence | None = None,
    ) -> PatternMatchResult:
        selected = tuple(candles[: self.package.sequence_candles])
        measures = tuple(CandleMeasures.from_candle(candle) for candle in selected)
        context_failure = self._context_failure(preceding_closes)
        if context_failure:
            return self.no_match(context_failure, selected, measures, evidence)
        for index, rule in enumerate(self.package.sequence_rules, start=1):
            if rule.candle is None or rule.candle >= len(selected):
                continue
            expected = self._expected(rule.value, selected, measures)
            if expected is _MISSING:
                continue
            actual = self._field(selected[rule.candle], measures[rule.candle], rule.field)
            if not compare(rule.operator, actual, expected):
                return self.no_match(
                    f"Rule {index} failed: candle {rule.candle} {rule.field} {rule.operator}.",
                    selected,
                    measures,
                    evidence,
                )
        if len(selected) < self.package.sequence_candles:
            return self._result(
                PatternStatus.FORMING,
                (
                    f"{len(selected)} of {self.package.sequence_candles} "
                    "required candles are available."
                ),
                selected,
                measures,
                evidence,
            )
        return self._result(
            PatternStatus.MATCH,
            "All bearish_engulfing rules passed after bar close.",
            selected,
            measures,
            evidence,
        )

    def no_match(
        self,
        reason: str,
        candles: Sequence[Candle] = (),
        measures: Sequence[CandleMeasures] = (),
        evidence: PatternEvidence | None = None,
    ) -> PatternMatchResult:
        return self._result(PatternStatus.NO_MATCH, reason, candles, measures, evidence)

    def _result(
        self,
        status: PatternStatus,
        reason: str,
        candles: Sequence[Candle],
        measures: Sequence[CandleMeasures],
        evidence: PatternEvidence | None,
    ) -> PatternMatchResult:
        return PatternMatchResult(
            self.package.id,
            self.package.version,
            status,
            reason,
            tuple(candles),
            tuple(measures),
            evidence,
        )

    def _context_failure(self, closes: Sequence[float]) -> str | None:
        required = self.package.preceding_candles
        if len(closes) < required:
            return f"Required preceding close context is missing ({len(closes)}/{required})."
        selected = tuple(float(value) for value in closes[-required:]) if required else ()
        for rule in self.package.context_rules:
            expected = rule.value
            pairs = zip(selected, selected[1:], strict=False)
            if expected == "ASCENDING" and not all(a < b for a, b in pairs):
                return "Preceding closes are not strictly ascending."
            pairs = zip(selected, selected[1:], strict=False)
            if expected == "DESCENDING" and not all(a > b for a, b in pairs):
                return "Preceding closes are not strictly descending."
            if expected not in {"ASCENDING", "DESCENDING"}:
                return "Unsupported close sequence value."
        return None

    def _expected(
        self,
        value: Any,
        candles: Sequence[Candle],
        measures: Sequence[CandleMeasures],
    ) -> Any:
        if isinstance(value, Mapping):
            if "lower_field" in value and "upper_field" in value:
                index = value.get("candle")
                if not isinstance(index, int) or index >= len(candles):
                    return _MISSING
                return (
                    self._field(candles[index], measures[index], str(value["lower_field"])),
                    self._field(candles[index], measures[index], str(value["upper_field"])),
                )
            index = value.get("candle")
            field = value.get("field")
            if not isinstance(index, int) or index >= len(candles) or not isinstance(field, str):
                return _MISSING
            return self._field(candles[index], measures[index], field)
        return value

    @staticmethod
    def _field(candle: Candle, measures: CandleMeasures, field: str) -> Any:
        values: dict[str, Any] = {
            "OPEN": candle.open,
            "HIGH": candle.high,
            "LOW": candle.low,
            "CLOSE": candle.close,
            "RANGE": measures.range,
            "BODY_HIGH": measures.body_high,
            "BODY_LOW": measures.body_low,
            "BODY_MIDPOINT": measures.body_midpoint,
            "BODY_TO_RANGE": measures.body_to_range,
            "UPPER_WICK_TO_RANGE": measures.upper_wick_to_range,
            "LOWER_WICK_TO_RANGE": measures.lower_wick_to_range,
            "BODY_COLOR": measures.body_color,
        }
        if field not in values:
            raise PatternPackageError(f"Unsupported field: {field}.")
        return values[field]


_MISSING = object()
