# ruff: noqa: E501
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ScheduleError(ValueError):
    pass


class ScheduleUnit(StrEnum):
    MINUTES = "minutes"
    HOURS = "hours"
    DAYS = "days"
    MONTHS = "months"


_LIMITS = {
    ScheduleUnit.MINUTES: 10080,
    ScheduleUnit.HOURS: 8760,
    ScheduleUnit.DAYS: 366,
    ScheduleUnit.MONTHS: 120,
}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ScheduleError("O horário precisa conter fuso.")
    return value.astimezone(UTC)


def _localize(naive: datetime, timezone: ZoneInfo) -> datetime:
    """Resolve local calendar times without silently accepting DST gaps."""
    candidate = naive.replace(tzinfo=timezone, fold=0)
    round_trip = candidate.astimezone(UTC).astimezone(timezone).replace(tzinfo=None)
    if round_trip != naive:
        # A nonexistent local time is moved to the first valid minute.
        for offset in range(1, 181):
            candidate = (naive + timedelta(minutes=offset)).replace(tzinfo=timezone, fold=0)
            round_trip = candidate.astimezone(UTC).astimezone(timezone).replace(tzinfo=None)
            if round_trip == naive + timedelta(minutes=offset):
                return candidate
        raise ScheduleError("Não foi possível resolver a transição de horário local.")
    return candidate


@dataclass(frozen=True, slots=True)
class CaptureInterval:
    value: int = 5
    unit: ScheduleUnit = ScheduleUnit.MINUTES
    timezone: str = "America/Sao_Paulo"

    def __post_init__(self) -> None:
        try:
            unit = ScheduleUnit(self.unit)
        except ValueError as exc:
            raise ScheduleError("Unidade de captura desconhecida.") from exc
        if type(self.value) is not int or self.value < 1:
            raise ScheduleError("O intervalo precisa ser um inteiro positivo.")
        if self.value > _LIMITS[unit]:
            raise ScheduleError(f"O intervalo excede o limite de {unit.value}.")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ScheduleError("Use um fuso IANA válido.") from exc

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def to_dict(self) -> dict[str, object]:
        return {"value": self.value, "unit": self.unit.value, "timezone": self.timezone}

    @classmethod
    def from_mapping(cls, value: dict[str, object]) -> CaptureInterval:
        allowed = {"value", "unit", "timezone"}
        unknown = set(value) - allowed
        if unknown:
            raise ScheduleError(f"Campos desconhecidos no intervalo: {', '.join(sorted(unknown))}.")
        interval_value = value.get("value", 5)
        if type(interval_value) is not int:
            raise ScheduleError("O intervalo precisa ser um inteiro positivo.")
        return cls(
            value=interval_value,
            unit=ScheduleUnit(str(value.get("unit", ScheduleUnit.MINUTES.value))),
            timezone=str(value.get("timezone", "America/Sao_Paulo")),
        )

    def next_at(self, anchor_utc: datetime, *, after: datetime | None = None) -> datetime:
        anchor = _as_utc(anchor_utc)
        current = _as_utc(after) if after is not None else anchor
        if self.unit in {ScheduleUnit.MINUTES, ScheduleUnit.HOURS}:
            seconds = self.value * (60 if self.unit is ScheduleUnit.MINUTES else 3600)
            elapsed = max(0, int((current - anchor).total_seconds()))
            steps = elapsed // seconds + 1
            return anchor + timedelta(seconds=steps * seconds)

        local_anchor = anchor.astimezone(self.zone).replace(tzinfo=None)
        step = self.value if self.unit is ScheduleUnit.DAYS else None
        index = 1
        while True:
            if step is not None:
                naive = local_anchor + timedelta(days=step * index)
            else:
                month_index = local_anchor.month - 1 + self.value * index
                year, month0 = divmod(month_index, 12)
                month = month0 + 1
                day = min(local_anchor.day, calendar.monthrange(local_anchor.year + year, month)[1])
                naive = local_anchor.replace(year=local_anchor.year + year, month=month, day=day)
            candidate = _localize(naive, self.zone).astimezone(UTC)
            if candidate > current and candidate > anchor:
                return candidate
            index += 1

    def summary(self) -> str:
        label = self.unit.value[:-1] if self.value == 1 else self.unit.value
        return f"a cada {self.value} {label} · fuso {self.timezone}"


def next_capture_at(
    interval: CaptureInterval,
    anchor_utc: datetime,
    now_utc: datetime,
) -> datetime:
    return interval.next_at(anchor_utc, after=now_utc)
