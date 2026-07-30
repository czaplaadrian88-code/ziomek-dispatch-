"""Jedyny typed parser lokalnych okien zmiany.

Godziny ``HH:MM`` nie są chwilą w czasie bez daty i strefy.  Ten moduł
kotwiczy je w ``Europe/Warsaw`` względem jawnego znacznika ``added_at`` (okno
operatora) albo przekazanego ``anchor`` (grafik).  Dzięki temu writer, reader i
HARD-gate używają tego samego przedziału także przez północ i zmianę offsetu.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo


WARSAW = ZoneInfo("Europe/Warsaw")


@dataclass(frozen=True)
class ShiftInterval:
    """Domknięty z lewej, otwarty z prawej przedział pracy."""

    start_at: datetime
    end_at: datetime
    added_at: datetime
    start: str
    end: str
    end_explicit: bool
    provenance: Optional[str] = None

    def contains(self, moment: datetime) -> bool:
        current = _aware(moment).astimezone(WARSAW)
        return self.start_at <= current < self.end_at

    def ended(self, moment: datetime) -> bool:
        return _aware(moment).astimezone(WARSAW) >= self.end_at


class ShiftEndStatus(str, Enum):
    """Dlaczego efektywny koniec jest znany albo świadomie nieznany."""

    KNOWN = "KNOWN"
    UNKNOWN_WINDOWLESS_ASSIGNMENT = "UNKNOWN_WINDOWLESS_ASSIGNMENT"
    UNKNOWN_NO_WINDOW = "UNKNOWN_NO_WINDOW"
    UNKNOWN_DATA_ERROR = "UNKNOWN_DATA_ERROR"


class ShiftWindowSource(str, Enum):
    """Typed provenance efektywnego okna, niezależna od authority dostępności."""

    SCHEDULE = "SCHEDULE"
    OPERATOR_WINDOW = "OPERATOR_WINDOW"
    OPERATOR_WINDOW_GRAFIK_CAP = "OPERATOR_WINDOW_GRAFIK_CAP"
    ASSIGNMENT_EVENT = "ASSIGNMENT_EVENT"
    NONE = "NONE"


@dataclass(frozen=True)
class EffectiveShiftWindow:
    """Jedyny kontrakt czasu pracy konsumowany przez pool i HARD-gates.

    Brak końca nie jest kodowany fikcyjną datą.  ``interval`` istnieje wyłącznie
    dla statusu ``KNOWN``; bezokienny assignment zachowuje prawdziwy start
    zdarzenia w ``start_at`` i jawny typed status.
    """

    start_at: Optional[datetime]
    interval: Optional[ShiftInterval]
    end_status: ShiftEndStatus
    source: ShiftWindowSource

    def __post_init__(self) -> None:
        if self.end_status is ShiftEndStatus.KNOWN:
            if self.interval is None:
                raise ValueError("KNOWN effective shift window requires interval")
            if self.start_at != self.interval.start_at:
                raise ValueError("effective shift start must come from its interval")
        elif self.interval is not None:
            raise ValueError("unknown effective shift window cannot carry interval")

    @property
    def end_at(self) -> Optional[datetime]:
        return self.interval.end_at if self.interval is not None else None

    @classmethod
    def known(
        cls,
        interval: ShiftInterval,
        source: ShiftWindowSource,
    ) -> "EffectiveShiftWindow":
        return cls(
            start_at=interval.start_at,
            interval=interval,
            end_status=ShiftEndStatus.KNOWN,
            source=source,
        )

    @classmethod
    def unknown(
        cls,
        status: ShiftEndStatus,
        source: ShiftWindowSource = ShiftWindowSource.NONE,
        *,
        start_at: Optional[datetime] = None,
    ) -> "EffectiveShiftWindow":
        if status is ShiftEndStatus.KNOWN:
            raise ValueError("use EffectiveShiftWindow.known for known interval")
        return cls(
            start_at=start_at,
            interval=None,
            end_status=status,
            source=source,
        )


def parse_aware_timestamp(value: Any) -> Optional[datetime]:
    """ISO timestamp → UTC-aware; naive input jest niejednoznaczny i odpada."""

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def parse_shift_interval(
    value: Any,
    *,
    anchor: Optional[datetime] = None,
    require_metadata: bool = False,
    expected_provenance: Optional[str] = None,
) -> Optional[ShiftInterval]:
    """Parsuje jedno okno bez fallbacków i zgadywania strefy.

    ``require_metadata=True`` jest kontraktem trwałego okna operatora:
    ``added_at``, ``end_explicit`` i field-level ``provenance`` są obowiązkowe.
    Dla grafiku caller przekazuje ``anchor``; surowe godziny nie dostają własnej
    daty ani drugiej implementacji parsera.
    """

    if not isinstance(value, Mapping):
        return None
    start_raw = value.get("start")
    end_raw = value.get("end")
    start_minutes = _clock_minutes(start_raw, allow_24=False)
    end_minutes = _clock_minutes(end_raw, allow_24=True)
    if start_minutes is None or end_minutes is None:
        return None

    if require_metadata:
        end_explicit = value.get("end_explicit")
        provenance = value.get("provenance")
        added_at = parse_aware_timestamp(value.get("added_at"))
        if not isinstance(end_explicit, bool) or added_at is None:
            return None
        if not isinstance(provenance, str) or not provenance:
            return None
        if expected_provenance is not None and provenance != expected_provenance:
            return None
        anchor_moment = added_at
    else:
        raw_explicit = value.get("end_explicit", False)
        if not isinstance(raw_explicit, bool):
            return None
        end_explicit = raw_explicit
        provenance = (
            value.get("provenance")
            if isinstance(value.get("provenance"), str)
            else None
        )
        embedded_anchor = parse_aware_timestamp(value.get("added_at"))
        # Grafik/legacy przekazuje jawny decision-time anchor i zachowuje
        # dotychczasową semantykę bieżącej doby. Tylko trwały operator window
        # (require_metadata) jest z definicji kotwiczony we własnym added_at.
        anchor_moment = anchor or embedded_anchor
        added_at = embedded_anchor
        if anchor_moment is None:
            return None
        if added_at is None:
            added_at = _aware(anchor_moment).astimezone(timezone.utc)

    try:
        anchor_local = _aware(anchor_moment).astimezone(WARSAW)
    except (TypeError, ValueError):
        return None
    start_at, end_at = _interval_for_anchor(
        anchor_local,
        start_minutes,
        end_minutes,
    )
    return ShiftInterval(
        start_at=start_at,
        end_at=end_at,
        added_at=added_at,
        start=_canonical_clock(start_minutes, allow_24=False),
        end=_canonical_clock(end_minutes, allow_24=True),
        end_explicit=end_explicit,
        provenance=provenance,
    )


def canonical_operator_window(
    value: Any,
    *,
    added_at: Optional[str] = None,
    provenance: str,
) -> Optional[dict]:
    """Waliduje writer-side i zwraca jedyny trwały shape okna."""

    if not isinstance(value, Mapping):
        return None
    candidate = {
        "start": value.get("start"),
        "end": value.get("end"),
        "end_explicit": value.get("end_explicit"),
        "added_at": added_at if added_at is not None else value.get("added_at"),
        "provenance": provenance,
    }
    interval = parse_shift_interval(
        candidate,
        require_metadata=True,
        expected_provenance=provenance,
    )
    if interval is None:
        return None
    return {
        "start": interval.start,
        "end": interval.end,
        "end_explicit": interval.end_explicit,
        "added_at": interval.added_at.isoformat(),
        "provenance": provenance,
    }


def _aware(moment: datetime) -> datetime:
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise ValueError("shift interval anchor must be timezone-aware")
    return moment


def _clock_minutes(value: Any, *, allow_24: bool) -> Optional[int]:
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if (
        len(parts) != 2
        or not all(part.isascii() and part.isdigit() for part in parts)
    ):
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if hour == 24:
        return 24 * 60 if allow_24 and minute == 0 else None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour * 60 + minute


def _canonical_clock(minutes: int, *, allow_24: bool) -> str:
    if allow_24 and minutes == 24 * 60:
        return "24:00"
    hour, minute = divmod(minutes, 60)
    return f"{hour:02d}:{minute:02d}"


def _at_minutes(day: date, minutes: int) -> datetime:
    if minutes == 24 * 60:
        return datetime.combine(day + timedelta(days=1), time.min, tzinfo=WARSAW)
    hour, minute = divmod(minutes, 60)
    return datetime.combine(day, time(hour, minute), tzinfo=WARSAW)


def _interval_for_anchor(
    anchor_local: datetime,
    start_minutes: int,
    end_minutes: int,
) -> tuple[datetime, datetime]:
    base_day = anchor_local.date()
    start_at = _at_minutes(base_day, start_minutes)
    end_at = _at_minutes(base_day, end_minutes)
    if end_at <= start_at:
        end_at += timedelta(days=1)

    # O 00:30 grafik 20:00–02:00 oznacza wciąż zmianę rozpoczętą wczoraj,
    # nie przyszłą zmianę dzisiejszego wieczoru.  Ten sam wybór działa dla
    # operatorowego ``added_at`` po północy.
    previous_start = start_at - timedelta(days=1)
    previous_end = end_at - timedelta(days=1)
    if previous_start <= anchor_local < previous_end:
        return previous_start, previous_end
    return start_at, end_at
