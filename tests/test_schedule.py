# ruff: noqa: E501
from datetime import UTC, datetime

import pytest

from providency.schedule import CaptureInterval, ScheduleError, ScheduleUnit


def test_interval_defaults_and_elapsed_units() -> None:
    interval = CaptureInterval(5, ScheduleUnit.MINUTES, "America/Sao_Paulo")
    anchor = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert interval.next_at(anchor, after=anchor) == datetime(2026, 1, 1, 12, 5, tzinfo=UTC)


def test_calendar_months_preserve_original_day() -> None:
    interval = CaptureInterval(1, ScheduleUnit.MONTHS, "UTC")
    anchor = datetime(2026, 1, 31, 12, 0, tzinfo=UTC)
    february = interval.next_at(anchor, after=anchor)
    march = interval.next_at(anchor, after=february)
    assert february == datetime(2026, 2, 28, 12, 0, tzinfo=UTC)
    assert march == datetime(2026, 3, 31, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    [0, -1, 10081],
)
def test_invalid_interval_is_rejected(value: int) -> None:
    with pytest.raises(ScheduleError):
        CaptureInterval(value, ScheduleUnit.MINUTES, "UTC")
