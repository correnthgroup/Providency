from __future__ import annotations

import math
import re
from dataclasses import asdict
from typing import Any

from providency.config import AnalysisConfiguration

IMPORTABLE_FIELDS = frozenset(
    {
        "symbol",
        "primary_timeframe",
        "quantity",
        "tick_size",
        "tick_value",
    }
)


def parse_display_number(value: str, *, decimal_separator: str) -> float | None:
    """Parse a known UI locale, never infer a price tick from displayed decimals."""
    value = value.strip().replace("\u00a0", "").replace(" ", "")
    grouping = "." if decimal_separator == "," else ","
    decimal = re.escape(decimal_separator)
    thousands = re.escape(grouping)
    if not re.fullmatch(rf"[+-]?(?:\d+|\d{{1,3}}(?:{thousands}\d{{3}})+)(?:{decimal}\d+)?", value):
        return None
    parsed = float(value.replace(grouping, "").replace(decimal_separator, "."))
    return parsed if math.isfinite(parsed) else None


def merge_vector_fields(
    current: AnalysisConfiguration,
    observed: dict[str, Any],
) -> AnalysisConfiguration:
    values = asdict(current)
    for name in ('quantity', 'tick_size', 'tick_value'):
        values[name] = 0
    if observed.get("symbol") and observed["symbol"] != current.symbol:
        defaults = asdict(AnalysisConfiguration())
        for name in IMPORTABLE_FIELDS:
            values[name] = defaults[name]
    values.update({key: value for key, value in observed.items() if key in IMPORTABLE_FIELDS})
    return AnalysisConfiguration.from_mapping(values)
