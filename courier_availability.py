"""Kanoniczny, CID-keyed kontrakt dostępności kuriera (R-POOL-TRUTH).

Moduł jest jedynym właścicielem trwałego klucza ``availability_by_cid``.
``dispatchable_fleet`` jest jedynym konsumentem decyzji :func:`resolve`.
Grafik pozostaje planem automatycznie włączającym kuriera w swoim dotychczasowym
oknie dispatchowym; jawny ON/OFF koordynatora albo skuteczne przypisanie ma
pierwszeństwo i trwa do kolejnego jawnego OFF/ON.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Dict, Iterator, Mapping, Optional
from zoneinfo import ZoneInfo

from dispatch_v2.shift_interval import (
    EffectiveShiftWindow,
    ShiftInterval,
    ShiftEndStatus,
    ShiftWindowSource,
    canonical_operator_window,
    parse_shift_interval,
)


OVERRIDES_PATH = "/root/.openclaw/workspace/dispatch_state/manual_overrides.json"
GRAFIK_FULL_NAMES_PATH = (
    "/root/.openclaw/workspace/dispatch_state/grafik_full_names.json"
)
STORE_KEY = "availability_by_cid"
LEGACY_REVISION_KEY = "legacy_updated_at"

# R4: granica doby operacyjnej. DOKŁADNIE ta sama, na której bliźniaczy
# `manual_overrides_daily_reset.py` (timer `dispatch-overrides-reset.timer`,
# `OnCalendar=*-*-* 06:00:00 Europe/Warsaw`) kasuje `excluded`/`excluded_cids`/
# `working`. Kontrakt CID-keyed przejął semantykę tamtej trójki, więc MUSI
# dziedziczyć też jej horyzont — inaczej ta sama klasa stanu ma dwa różne
# cykle życia. Patrz `docs/R4_OPERATOR_ON_MAP.md`.
OPERATIONAL_DAY_TZ = ZoneInfo("Europe/Warsaw")
OPERATIONAL_DAY_RESET_HOUR = 6


class AvailabilityState(str, Enum):
    OPERATOR_ON = "OPERATOR_ON"
    OPERATOR_OFF = "OPERATOR_OFF"
    SCHEDULED_ON = "SCHEDULED_ON"
    OFF_PLANNED = "OFF_PLANNED"
    UNKNOWN_DATA_ERROR = "UNKNOWN_DATA_ERROR"


class AvailabilityProvenance(str, Enum):
    COORDINATOR_CONSOLE = "coordinator_console"
    ASSIGNMENT_EVENT = "assignment_event"
    SCHEDULE_ON_SHIFT = "schedule_on_shift"
    SCHEDULE_PRE_SHIFT = "schedule_pre_shift"
    SCHEDULE_EMPTY_DAY = "schedule_empty_day"
    SCHEDULE_OUTSIDE_WINDOW = "schedule_outside_window"
    OPERATOR_STORE_ERROR = "operator_store_error"
    SCHEDULE_LOAD_ERROR = "schedule_load_error"
    SCHEDULE_IDENTITY_ERROR = "schedule_identity_error"
    SCHEDULE_ENTRY_ERROR = "schedule_entry_error"


@dataclass(frozen=True)
class AvailabilityDecision:
    cid: str
    state: AvailabilityState
    provenance: AvailabilityProvenance
    dispatchable: bool
    schedule_name: Optional[str] = None
    schedule_entry: Optional[dict] = None
    # Dokładny stempel kanonicznego rekordu operatora. To metadata, nie nowy
    # zegar ani writer; resolver puli może dzięki niemu nie brać początku
    # przyszłego grafiku dla OPERATOR_ON bez jawnego okna konsoli.
    operator_since: Optional[datetime] = None
    # Kanoniczna projekcja tej samej decyzji „pracuje od/do", którą przy
    # wyłączonym kontrakcie niesie legacy `working`. Field-level provenance
    # pozwala zachować ten fakt po późniejszym assignment_event.
    operator_window: Optional[dict] = None
    # Jedyny sparsowany, Warsaw-aware przedział. Writer i reader używają tego
    # samego parsera; konsumenci nie interpretują ponownie surowych HH:MM.
    operator_interval: Optional[ShiftInterval] = None
    # Exact CID->schedule evaluation frozen in this decision. Consumers must
    # not re-run schedule matching with a different name or clock.
    real_on_shift_now: bool = False
    detail: Optional[str] = None
    # R4: rekord operatorski BYŁ, ale wygasł — decyzję podjął grafik. Wyłącznie
    # obserwowalność (konsument stempluje log puli); nie wchodzi do polityki.
    operator_expired: bool = False
    # Grafik parsowany dokładnie raz na zamrożonym ``context.now``. Pole istnieje
    # niezależnie od dispatchability/authority, żeby żaden konsument nie wracał
    # do surowego HH:MM ani do hostowego boola ``is_on_shift``.
    schedule_interval: Optional[ShiftInterval] = None
    # Kanoniczny kontrakt czasu pool→feasibility→HARD report. Brak końca ma typed
    # status; nigdy nie jest maskowany datą-sentinelem.
    effective_shift_window: EffectiveShiftWindow = field(
        default_factory=lambda: EffectiveShiftWindow.unknown(
            ShiftEndStatus.UNKNOWN_NO_WINDOW
        )
    )


@dataclass(frozen=True)
class AvailabilityContext:
    operator_records: Mapping[str, dict]
    operator_error: Optional[str]
    # Legacy rollback projection loaded from the exact same file snapshot as
    # operator_records. It is consulted only while the behavior flag is OFF,
    # but sharing the read prevents HARD-report/fleet split-brain.
    legacy_working_by_cid: Mapping[str, dict]
    legacy_error: Optional[str]
    schedule: Mapping[str, Any]
    schedule_error: Optional[str]
    schedule_names_by_cid: Mapping[str, str]
    identity_error: Optional[str]
    # R4: „teraz" zamrożone RAZ na wywołanie `dispatchable_fleet()` — inaczej
    # kurierzy z tej samej pętli mogliby wygasać na różnych znacznikach czasu.
    now: Optional[datetime] = None
    expiry_enabled: bool = False


class ConsoleMutationKind(str, Enum):
    ON = "ON"
    OFF = "OFF"
    CLEAR = "CLEAR"


@dataclass(frozen=True)
class ConsoleAvailabilityMutation:
    """Czysta komenda konsoli, nie snapshot store'u.

    Mutation opisuje intencję. Dopiero owner store'u aplikuje ją do świeżo
    odczytanego payloadu pod canonical lockiem, więc caller nie może nadpisać
    równoległej komendy starym pełnym JSON-em.
    """

    kind: ConsoleMutationKind
    cid: Optional[str]
    courier_name: str
    aliases: tuple[str, ...]
    at: datetime
    project_operator: bool = True
    working_entry: Optional[Mapping[str, Any]] = None
    operator_window: Optional[Mapping[str, Any]] = None

    @classmethod
    def on(
        cls,
        cid: Any,
        courier_name: str,
        *,
        working_entry: Mapping[str, Any],
        operator_window: Mapping[str, Any],
        aliases: tuple[str, ...] = (),
        at: datetime,
        project_operator: bool = True,
    ) -> "ConsoleAvailabilityMutation":
        return cls(
            kind=ConsoleMutationKind.ON,
            cid=_canon_cid(cid),
            courier_name=str(courier_name),
            aliases=tuple(dict.fromkeys((str(courier_name), *aliases))),
            at=at,
            project_operator=project_operator,
            working_entry=MappingProxyType(dict(working_entry)),
            operator_window=MappingProxyType(dict(operator_window)),
        )

    @classmethod
    def off(
        cls,
        cid: Any,
        courier_name: str,
        *,
        at: datetime,
        project_operator: bool = True,
    ) -> "ConsoleAvailabilityMutation":
        try:
            key = _canon_cid(cid)
        except ValueError:
            if project_operator:
                raise
            key = None
        return cls(
            kind=ConsoleMutationKind.OFF,
            cid=key,
            courier_name=str(courier_name),
            aliases=(str(courier_name),),
            at=at,
            project_operator=project_operator,
        )

    @classmethod
    def clear(
        cls,
        cid: Any,
        courier_name: str,
        *,
        aliases: tuple[str, ...] = (),
        at: datetime,
        project_operator: bool = True,
    ) -> "ConsoleAvailabilityMutation":
        try:
            key = _canon_cid(cid)
        except ValueError:
            if project_operator:
                raise
            key = None
        return cls(
            kind=ConsoleMutationKind.CLEAR,
            cid=key,
            courier_name=str(courier_name),
            aliases=tuple(dict.fromkeys((str(courier_name), *aliases))),
            at=at,
            project_operator=project_operator,
        )

    def apply_legacy(self, payload: Mapping[str, Any]) -> dict:
        """Pure: zwraca nową projekcję bez mutacji ``payload`` ani self."""

        merged = dict(payload)
        excluded = list(payload.get("excluded", []) or [])
        excluded_cids = list(payload.get("excluded_cids", []) or [])
        working_raw = payload.get("working", {})
        working = dict(working_raw) if isinstance(working_raw, Mapping) else {}

        if self.kind in {ConsoleMutationKind.ON, ConsoleMutationKind.CLEAR}:
            aliases = set(self.aliases)
            excluded = [name for name in excluded if name not in aliases]
            if self.cid is not None:
                excluded_cids = [
                    value for value in excluded_cids if str(value) != self.cid
                ]
            if self.kind is ConsoleMutationKind.ON:
                if self.working_entry is None:
                    raise ValueError("ON mutation requires working entry")
                working[self.cid] = dict(self.working_entry)
        elif self.kind is ConsoleMutationKind.OFF:
            if self.courier_name not in excluded:
                excluded.append(self.courier_name)
            if (
                self.cid is not None
                and self.cid not in [str(value) for value in excluded_cids]
            ):
                excluded_cids.append(self.cid)
            if self.cid is not None:
                working.pop(self.cid, None)

        merged["excluded"] = excluded
        merged["excluded_cids"] = excluded_cids
        merged["working"] = working
        return merged


@dataclass(frozen=True)
class ConsoleMutationResult:
    before_payload: Mapping[str, Any]
    payload: Mapping[str, Any]
    stored_record: Optional[Mapping[str, Any]]
    applied: bool
    attempts: int


def _canon_cid(cid: Any) -> str:
    raw = str(cid or "").strip()
    if not raw or not raw.isdigit():
        raise ValueError("courier availability requires a numeric cid")
    return str(int(raw))


def _effective_overrides_path(path: Optional[str]) -> str:
    if path:
        return path
    state_dir = os.environ.get("DISPATCH_STATE_DIR")
    if state_dir:
        return str(Path(state_dir) / "manual_overrides.json")
    return OVERRIDES_PATH


def effective_overrides_path() -> str:
    """Jedyny kanoniczny path store'u ``availability_by_cid``.

    R-POOL-TRUTH: writer domyślny (``set_operator_availability(path=None)``) i
    konsument puli (``courier_resolver.dispatchable_fleet``) MUSZĄ czytać/pisać
    dokładnie ten sam efektywny plik. Zabronione jest, by resolver liczył ścieżkę
    z innego źródła (np. stałej ``manual_overrides.OVERRIDES_PATH``) — to tworzyło
    dwa store tej samej prawdy i rozbieżność writer↔resolver pod
    ``DISPATCH_STATE_DIR``.
    """
    return _effective_overrides_path(None)


def _parse_store_ts(value: Any) -> Optional[datetime]:
    """Parsuje ``updated_at`` rekordu; None gdy brak/nie-ISO (nie blokuje write)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _operator_window_fields(
    value: Any,
    *,
    added_at: Optional[str] = None,
    provenance: Optional[str] = None,
) -> Optional[dict]:
    """Waliduje writer/read-side przez jeden typed parser.

    ``provenance`` należy do faktu okna, nie do całego rekordu authority.
    Dzięki temu późniejszy assignment może zmienić źródło ON, ale nie kasuje
    nadal obowiązującego jawnego końca koordynatora.
    """

    field_provenance = (
        provenance
        if provenance is not None
        else value.get("provenance") if isinstance(value, Mapping) else None
    )
    if field_provenance != AvailabilityProvenance.COORDINATOR_CONSOLE.value:
        return None
    return canonical_operator_window(
        value,
        added_at=added_at,
        provenance=field_provenance,
    )


def _schedule_interval(
    entry: Optional[Mapping[str, Any]],
    now: datetime,
) -> Optional[ShiftInterval]:
    """Jedyny read-side parser grafiku dla kontraktu CID."""

    if not isinstance(entry, Mapping):
        return None
    return parse_shift_interval(entry, anchor=now)


def _operator_interval_with_schedule_cap(
    operator_interval: ShiftInterval,
    schedule_interval: Optional[ShiftInterval],
    cap_enabled: bool,
) -> tuple[ShiftInterval, ShiftWindowSource]:
    """Nakłada istniejący GRAFIK-CAP bez ponownego parsowania zegarów."""

    if (
        not cap_enabled
        or operator_interval.end_explicit
        or schedule_interval is None
        or operator_interval.added_at > schedule_interval.end_at
        or operator_interval.end_at <= schedule_interval.end_at
    ):
        return operator_interval, ShiftWindowSource.OPERATOR_WINDOW
    return (
        replace(
            operator_interval,
            end_at=schedule_interval.end_at,
            end=schedule_interval.end,
            provenance=ShiftWindowSource.OPERATOR_WINDOW_GRAFIK_CAP.value,
        ),
        ShiftWindowSource.OPERATOR_WINDOW_GRAFIK_CAP,
    )


def _operator_effective_window(
    provenance: AvailabilityProvenance,
    operator_since: Optional[datetime],
    operator_interval: Optional[ShiftInterval],
    schedule_interval: Optional[ShiftInterval],
    now: datetime,
    cap_enabled: bool,
) -> EffectiveShiftWindow:
    """Łączy authority i czas w jeden typed kontrakt.

    Aktywny dokładny grafik wygrywa czasowo. Poza nim jawne okno konsoli niesie
    swój przedział (z istniejącym GRAFIK-CAP). Assignment bez okna zachowuje
    jedynie prawdziwy stempel początku i jawny brak końca.
    """

    if schedule_interval is not None and schedule_interval.contains(now):
        return EffectiveShiftWindow.known(
            schedule_interval,
            ShiftWindowSource.SCHEDULE,
        )
    if operator_interval is not None:
        interval, source = _operator_interval_with_schedule_cap(
            operator_interval,
            schedule_interval,
            cap_enabled,
        )
        return EffectiveShiftWindow.known(interval, source)
    if provenance is AvailabilityProvenance.ASSIGNMENT_EVENT:
        return EffectiveShiftWindow.unknown(
            ShiftEndStatus.UNKNOWN_WINDOWLESS_ASSIGNMENT,
            ShiftWindowSource.ASSIGNMENT_EVENT,
            start_at=operator_since,
        )
    return EffectiveShiftWindow.unknown(
        ShiftEndStatus.UNKNOWN_NO_WINDOW,
        start_at=operator_since,
    )


def _operational_day_start_after(moment: datetime) -> datetime:
    """Pierwsza granica doby operacyjnej ŚCIŚLE po ``moment`` (UTC-aware).

    Liczone na dacie lokalnej, nie arytmetyką na aware-datetime: dodanie
    ``timedelta(days=1)`` do momentu z offsetem daje przy zmianie czasu przesuniętą
    godzinę ścienną, a granicą jest właśnie godzina ścienna 06:00 (tak samo, jak
    rozumie ją `OnCalendar` systemd). 06:00 nigdy nie wpada w lukę DST w
    Europe/Warsaw (przeskoki 02:00/03:00), więc odwzorowanie jest jednoznaczne.
    """
    local = moment.astimezone(OPERATIONAL_DAY_TZ)
    boundary_time = time(hour=OPERATIONAL_DAY_RESET_HOUR)
    boundary = datetime.combine(local.date(), boundary_time, tzinfo=OPERATIONAL_DAY_TZ)
    if boundary <= local:
        boundary = datetime.combine(
            local.date() + timedelta(days=1), boundary_time, tzinfo=OPERATIONAL_DAY_TZ
        )
    return boundary.astimezone(timezone.utc)


def _operator_record_expired(record: Mapping[str, Any], now: datetime) -> bool:
    """Czy rekord operatorski przestał być prawdą o BIEŻĄCEJ dobie operacyjnej.

    Zasada jedna: *niedowodliwa świeżość nigdy nie nadaje dostępności*. Gdy
    ``updated_at`` jest pusty albo nie-ISO (rekord tknięty ręcznie — writer
    :func:`set_operator_availability` zawsze stempluje), ``OPERATOR_ON`` wygasa,
    bo wpuszczenie do puli wymaga przesłanki, a jej nie ma; ``OPERATOR_OFF``
    zostaje, bo ZDJĘCIE ograniczenia wymaga dowodu, że granica doby minęła — a nie
    braku takiego dowodu.
    """
    stamped = _parse_store_ts(record.get("updated_at"))
    if stamped is None:
        return record.get("state") == AvailabilityState.OPERATOR_ON.value
    return now >= _operational_day_start_after(stamped)


def _read_json_dict(path: str) -> tuple[dict, Optional[str]]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}, None
    except Exception as exc:
        return {}, type(exc).__name__
    if not isinstance(data, dict):
        return {}, "root_not_object"
    return data, None


def _validated_operator_records(
    data: Mapping[str, Any],
    error: Optional[str] = None,
) -> tuple[dict, Optional[str]]:
    if error:
        return {}, error
    records = data.get(STORE_KEY, {})
    if not isinstance(records, dict):
        return {}, "availability_store_not_object"
    clean: Dict[str, dict] = {}
    for raw_cid, raw_record in records.items():
        try:
            cid = _canon_cid(raw_cid)
        except ValueError:
            return {}, "availability_store_invalid_cid"
        if not isinstance(raw_record, dict):
            return {}, "availability_store_invalid_record"
        state = raw_record.get("state")
        provenance = raw_record.get("provenance")
        if state not in {
            AvailabilityState.OPERATOR_ON.value,
            AvailabilityState.OPERATOR_OFF.value,
        }:
            return {}, "availability_store_invalid_state"
        if provenance not in {
            AvailabilityProvenance.COORDINATOR_CONSOLE.value,
            AvailabilityProvenance.ASSIGNMENT_EVENT.value,
        }:
            return {}, "availability_store_invalid_provenance"
        clean[cid] = dict(raw_record)
    return clean, None


def _operator_records(path: str) -> tuple[dict, Optional[str]]:
    data, error = _read_json_dict(path)
    return _validated_operator_records(data, error)


def _schedule_names(path: str) -> tuple[dict, Optional[str]]:
    raw, error = _read_json_dict(path)
    if error:
        return {}, error
    names: Dict[str, str] = {}
    for name, raw_cid in raw.items():
        if not isinstance(name, str) or not name.strip():
            return {}, "grafik_identity_invalid_name"
        try:
            cid = _canon_cid(raw_cid)
        except ValueError:
            return {}, "grafik_identity_invalid_cid"
        if cid in names and names[cid] != name:
            return {}, "grafik_identity_duplicate_cid"
        names[cid] = name
    return names, None


def _expiry_flag_enabled() -> bool:
    """Fail-closed odczyt kill-switcha R4: awaria/brak klucza = zachowanie sprzed R4."""
    try:
        from dispatch_v2 import common as _common

        return bool(_common.decision_flag("ENABLE_OPERATOR_AVAILABILITY_EXPIRY"))
    except Exception:
        return False


def load_context(
    schedule: Optional[Mapping[str, Any]],
    *,
    schedule_error: Optional[str] = None,
    overrides_path: str = OVERRIDES_PATH,
    grafik_names_path: str = GRAFIK_FULL_NAMES_PATH,
    now: Optional[datetime] = None,
    expiry_enabled: Optional[bool] = None,
) -> AvailabilityContext:
    """Ładuje oba CID-keyed wejścia raz na wywołanie ``dispatchable_fleet``.

    R4: flaga wygasania czytana TU (raz na wywołanie, nie per kurier) i zamrażana
    w kontekście razem z ``now`` — cała pętla puli rozstrzyga na jednym stanie
    flagi i jednym znaczniku czasu. Hot-reload działa MIĘDZY wywołaniami, tak samo
    jak dla pozostałych flag czytanych w ``dispatchable_fleet``.
    """
    store, store_error = _read_json_dict(overrides_path)
    records, operator_error = _validated_operator_records(store, store_error)
    raw_working = store.get("working", {}) if store_error is None else {}
    legacy_error = None
    if not isinstance(raw_working, dict):
        legacy_working: Dict[str, dict] = {}
        legacy_error = "working_store_not_object"
    else:
        legacy_working = {
            str(raw_cid): dict(raw_entry)
            for raw_cid, raw_entry in raw_working.items()
            if isinstance(raw_entry, dict)
        }
    names, identity_error = _schedule_names(grafik_names_path)
    schedule_map: Mapping[str, Any] = schedule if isinstance(schedule, Mapping) else {}
    if schedule is not None and not isinstance(schedule, Mapping):
        schedule_error = schedule_error or "schedule_not_object"
    if schedule is None:
        schedule_error = schedule_error or "schedule_missing"
    return AvailabilityContext(
        operator_records=records,
        operator_error=operator_error,
        legacy_working_by_cid=legacy_working,
        legacy_error=legacy_error or store_error,
        schedule=schedule_map,
        schedule_error=schedule_error,
        schedule_names_by_cid=names,
        identity_error=identity_error,
        now=now or datetime.now(timezone.utc),
        expiry_enabled=(
            _expiry_flag_enabled() if expiry_enabled is None else bool(expiry_enabled)
        ),
    )


def resolve(
    context: AvailabilityContext,
    cid: Any,
    *,
    is_on_shift: Callable[[str, Mapping[str, Any]], tuple[bool, str]],
    mins_to_shift_start: Callable[[dict], Optional[float]],
    pre_shift_window_min: float,
    cap_enabled: bool = True,
) -> AvailabilityDecision:
    """Rozstrzyga jedną dostępność. Nie używa nazw floty ani fuzzy fallbacków.

    R4: rekord operatorski jest prawdą tylko o SWOJEJ dobie operacyjnej. Po jej
    granicy jest traktowany jak nieobecny, więc decyzja spada na grafik — czyli
    dokładnie ta sama ścieżka, którą już dziś realizuje ``None`` (neutral) z konsoli.
    Żadnego nowego stanu ani gałęzi u konsumenta. Flaga OFF = zachowanie sprzed R4.

    ``is_on_shift`` i ``mins_to_shift_start`` pozostają w sygnaturze wyłącznie
    dla kompatybilności callerów. CID path nie ufa ich hostowej implementacji:
    prawdę czasu wyznacza jeden ``ShiftInterval`` zakotwiczony w ``context.now``.
    """
    del is_on_shift, mins_to_shift_start
    key = _canon_cid(cid)
    now = context.now or datetime.now(timezone.utc)
    if context.operator_error:
        return AvailabilityDecision(
            key,
            AvailabilityState.UNKNOWN_DATA_ERROR,
            AvailabilityProvenance.OPERATOR_STORE_ERROR,
            False,
            detail=context.operator_error,
            effective_shift_window=EffectiveShiftWindow.unknown(
                ShiftEndStatus.UNKNOWN_DATA_ERROR
            ),
        )

    operator = context.operator_records.get(key)
    expired = False
    if operator and context.expiry_enabled:
        expired = _operator_record_expired(operator, now)
        if expired:
            operator = None

    if operator:
        state = AvailabilityState(operator["state"])
        provenance = AvailabilityProvenance(operator["provenance"])
        operator_since = _parse_store_ts(operator.get("updated_at"))
        operator_window = None
        operator_interval = None
        if state is AvailabilityState.OPERATOR_ON and "operator_window" in operator:
            operator_window = _operator_window_fields(operator.get("operator_window"))
            if operator_window is None:
                return AvailabilityDecision(
                    key,
                    state,
                    provenance,
                    False,
                    operator_since=operator_since,
                    detail="operator_window_invalid",
                    effective_shift_window=EffectiveShiftWindow.unknown(
                        ShiftEndStatus.UNKNOWN_DATA_ERROR,
                        start_at=operator_since,
                    ),
                )
            operator_interval = parse_shift_interval(
                operator_window,
                require_metadata=True,
                expected_provenance=(
                    AvailabilityProvenance.COORDINATOR_CONSOLE.value
                ),
            )
            if operator_interval is None:
                return AvailabilityDecision(
                    key,
                    state,
                    provenance,
                    False,
                    operator_since=operator_since,
                    detail="operator_window_invalid",
                    effective_shift_window=EffectiveShiftWindow.unknown(
                        ShiftEndStatus.UNKNOWN_DATA_ERROR,
                        start_at=operator_since,
                    ),
                )
        # Availability authority and schedule time metadata are separate facts.
        # Exact CID->schedule metadata is parsed independently of dispatchable:
        # także OPERATOR_OFF i OUTSIDE_WINDOW zachowują znany koniec dla raportu
        # HARD. Nie fuzzy-matchujemy i nie fabrykujemy brakującego wpisu.
        schedule_name = None
        schedule_entry = None
        schedule_interval = None
        if (
            context.schedule_error is None
            and context.identity_error is None
        ):
            schedule_name = context.schedule_names_by_cid.get(key)
            raw_schedule_entry = (
                context.schedule.get(schedule_name)
                if schedule_name is not None and schedule_name in context.schedule
                else None
            )
            schedule_entry = (
                raw_schedule_entry if isinstance(raw_schedule_entry, dict) else None
            )
            schedule_interval = _schedule_interval(schedule_entry, now)
        real_on_shift_now = (
            schedule_interval.contains(now)
            if schedule_interval is not None
            else False
        )
        if state is AvailabilityState.OPERATOR_ON:
            effective_window = _operator_effective_window(
                provenance,
                operator_since,
                operator_interval,
                schedule_interval,
                now,
                cap_enabled,
            )
        elif schedule_interval is not None:
            effective_window = EffectiveShiftWindow.known(
                schedule_interval,
                ShiftWindowSource.SCHEDULE,
            )
        else:
            effective_window = EffectiveShiftWindow.unknown(
                ShiftEndStatus.UNKNOWN_NO_WINDOW
            )
        if operator_interval is not None:
            # Baseline precedence: an active exact schedule wins over the
            # console projection even when its explicit window has just ended.
            # Outside an active schedule, ended OPERATOR_ON remains fail-closed.
            if operator_interval.ended(now) and not real_on_shift_now:
                return AvailabilityDecision(
                    key,
                    state,
                    provenance,
                    False,
                    schedule_name=schedule_name,
                    schedule_entry=schedule_entry,
                    operator_since=operator_since,
                    operator_window=operator_window,
                    operator_interval=operator_interval,
                    real_on_shift_now=real_on_shift_now,
                    detail="operator_window_ended",
                    schedule_interval=schedule_interval,
                    effective_shift_window=effective_window,
                )
        return AvailabilityDecision(
            key,
            state,
            provenance,
            state is AvailabilityState.OPERATOR_ON,
            schedule_name=schedule_name,
            schedule_entry=schedule_entry,
            operator_since=operator_since,
            operator_window=operator_window,
            operator_interval=operator_interval,
            real_on_shift_now=real_on_shift_now,
            schedule_interval=schedule_interval,
            effective_shift_window=effective_window,
        )

    decision = _resolve_from_schedule(
        context,
        key,
        pre_shift_window_min=pre_shift_window_min,
    )
    return replace(decision, operator_expired=True) if expired else decision


def _resolve_from_schedule(
    context: AvailabilityContext,
    key: str,
    *,
    pre_shift_window_min: float,
) -> AvailabilityDecision:
    """Grafikowa część :func:`resolve` oparta wyłącznie o typed interval."""
    unknown_data = EffectiveShiftWindow.unknown(
        ShiftEndStatus.UNKNOWN_DATA_ERROR
    )
    if context.schedule_error:
        return AvailabilityDecision(
            key,
            AvailabilityState.UNKNOWN_DATA_ERROR,
            AvailabilityProvenance.SCHEDULE_LOAD_ERROR,
            False,
            detail=context.schedule_error,
            effective_shift_window=unknown_data,
        )
    if context.identity_error:
        return AvailabilityDecision(
            key,
            AvailabilityState.UNKNOWN_DATA_ERROR,
            AvailabilityProvenance.SCHEDULE_IDENTITY_ERROR,
            False,
            detail=context.identity_error,
            effective_shift_window=unknown_data,
        )

    schedule_name = context.schedule_names_by_cid.get(key)
    if schedule_name is None or schedule_name not in context.schedule:
        return AvailabilityDecision(
            key,
            AvailabilityState.UNKNOWN_DATA_ERROR,
            AvailabilityProvenance.SCHEDULE_IDENTITY_ERROR,
            False,
            schedule_name=schedule_name,
            detail="cid_has_no_exact_schedule_entry",
            effective_shift_window=unknown_data,
        )

    entry = context.schedule[schedule_name]
    if entry is None:
        return AvailabilityDecision(
            key,
            AvailabilityState.OFF_PLANNED,
            AvailabilityProvenance.SCHEDULE_EMPTY_DAY,
            False,
            schedule_name=schedule_name,
            effective_shift_window=EffectiveShiftWindow.unknown(
                ShiftEndStatus.UNKNOWN_NO_WINDOW
            ),
        )
    if not isinstance(entry, dict):
        return AvailabilityDecision(
            key,
            AvailabilityState.UNKNOWN_DATA_ERROR,
            AvailabilityProvenance.SCHEDULE_ENTRY_ERROR,
            False,
            schedule_name=schedule_name,
            detail="schedule_entry_not_object",
            effective_shift_window=unknown_data,
        )

    now = context.now or datetime.now(timezone.utc)
    schedule_interval = _schedule_interval(entry, now)
    if schedule_interval is None:
        return AvailabilityDecision(
            key,
            AvailabilityState.UNKNOWN_DATA_ERROR,
            AvailabilityProvenance.SCHEDULE_ENTRY_ERROR,
            False,
            schedule_name=schedule_name,
            schedule_entry=entry,
            detail="schedule_interval_invalid",
            effective_shift_window=unknown_data,
        )
    effective_window = EffectiveShiftWindow.known(
        schedule_interval,
        ShiftWindowSource.SCHEDULE,
    )
    if schedule_interval.contains(now):
        return AvailabilityDecision(
            key,
            AvailabilityState.SCHEDULED_ON,
            AvailabilityProvenance.SCHEDULE_ON_SHIFT,
            True,
            schedule_name=schedule_name,
            schedule_entry=entry,
            real_on_shift_now=True,
            schedule_interval=schedule_interval,
            effective_shift_window=effective_window,
        )
    mins = (schedule_interval.start_at - now).total_seconds() / 60.0
    if mins is not None and 0 < mins <= pre_shift_window_min:
        return AvailabilityDecision(
            key,
            AvailabilityState.SCHEDULED_ON,
            AvailabilityProvenance.SCHEDULE_PRE_SHIFT,
            True,
            schedule_name=schedule_name,
            schedule_entry=entry,
            schedule_interval=schedule_interval,
            effective_shift_window=effective_window,
        )
    return AvailabilityDecision(
        key,
        AvailabilityState.OFF_PLANNED,
        AvailabilityProvenance.SCHEDULE_OUTSIDE_WINDOW,
        False,
        schedule_name=schedule_name,
        schedule_entry=entry,
        schedule_interval=schedule_interval,
        effective_shift_window=effective_window,
    )


@contextmanager
def _store_lock(path: str) -> Iterator[None]:
    lock_path = path + ".availability.lock"
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: str, data: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=target.name + ".availability.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        dir_fd = os.open(str(target.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def save_legacy_payload(
    data: dict,
    *,
    path: Optional[str] = None,
) -> None:
    """Zapis legacy pól bez prawa nadpisania kanonicznego kontraktu.

    ``manual_overrides`` może rozpocząć RMW przed równoległym assignmentem. Pod
    wspólnym lockiem ponownie czytamy bieżący store i zawsze zachowujemy jego
    ``availability_by_cid``; dzięki temu stary payload nie kasuje nowszego ON/OFF.
    """
    if not isinstance(data, dict):
        raise ValueError("manual overrides payload must be an object")
    effective_path = _effective_overrides_path(path)
    expected_revision = data.get(LEGACY_REVISION_KEY, "")
    with _store_lock(effective_path):
        current, error = _read_json_dict(effective_path)
        if error:
            raise RuntimeError(f"availability store unreadable: {error}")
        current_revision = current.get(LEGACY_REVISION_KEY, "")
        if current_revision != expected_revision:
            raise RuntimeError(
                "concurrent manual overrides update; retry command"
            )
        merged = dict(data)
        current_records = current.get(STORE_KEY, {})
        if not isinstance(current_records, dict):
            raise RuntimeError("availability store is not an object")
        merged[STORE_KEY] = current_records
        updated_at = datetime.now(timezone.utc).isoformat()
        merged["updated_at"] = updated_at
        merged[LEGACY_REVISION_KEY] = updated_at
        _atomic_write(effective_path, merged)


def reset_legacy_fields(*, path: Optional[str] = None) -> Dict[str, int]:
    """Czyści lifecycle legacy jednym RMW pod kanonicznym lockiem.

    CID authority jest zachowane. Zwracamy wyłącznie liczności, żeby entrypoint
    resetu nie logował nazw ani CID-ów.
    """

    effective_path = _effective_overrides_path(path)
    with _store_lock(effective_path):
        current, error = _read_json_dict(effective_path)
        if error:
            raise RuntimeError(f"availability store unreadable: {error}")
        records = current.get(STORE_KEY, {})
        if not isinstance(records, dict):
            raise RuntimeError("availability store is not an object")
        counts = {
            "excluded": len(current.get("excluded", []) or []),
            "excluded_cids": len(current.get("excluded_cids", []) or []),
            "working": len(current.get("working", {}) or {}),
        }
        updated_at = datetime.now(timezone.utc).isoformat()
        merged = dict(current)
        merged["excluded"] = []
        merged["excluded_cids"] = []
        merged["working"] = {}
        merged[STORE_KEY] = records
        merged["updated_at"] = updated_at
        merged[LEGACY_REVISION_KEY] = updated_at
        _atomic_write(effective_path, merged)
    return counts


def _normalized_event_time(at: Optional[datetime]) -> tuple[datetime, str]:
    when = at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    return when, when.isoformat()


def _new_operator_record(
    state: Optional[AvailabilityState],
    provenance: AvailabilityProvenance,
    updated_at: str,
    operator_window: Optional[Mapping[str, Any]],
) -> Optional[dict]:
    if state is None:
        return None
    record = {
        "state": state.value,
        "provenance": provenance.value,
        "updated_at": updated_at,
    }
    if operator_window is not None:
        canonical_window = _operator_window_fields(
            operator_window,
            added_at=updated_at,
            provenance=AvailabilityProvenance.COORDINATOR_CONSOLE.value,
        )
        if canonical_window is None:
            raise ValueError("invalid operator window")
        record["operator_window"] = canonical_window
    return record


def _merge_operator_record(
    records: Mapping[str, Any],
    key: str,
    incoming: Optional[dict],
    provenance: AvailabilityProvenance,
    when: datetime,
) -> tuple[dict, Optional[dict], bool]:
    """Łączy niezależne fakty authority i okna w jednym ownerze.

    Root ``provenance/updated_at`` opisuje ostatni fakt ON/OFF.  Zagnieżdżone
    ``operator_window.provenance/added_at`` opisuje osobny jawny fakt czasu.
    Assignment nie ma prawa deklarować końca, dlatego zachowuje poprawne okno
    konsoli zamiast zastępować cały rekord.
    """

    updated = dict(records)
    if incoming is None:
        updated.pop(key, None)
        return updated, None, True

    existing = updated.get(key)
    if (
        provenance is AvailabilityProvenance.ASSIGNMENT_EVENT
        and isinstance(existing, dict)
    ):
        existing_ts = _parse_store_ts(existing.get("updated_at"))
        if existing_ts is not None and existing_ts >= when:
            return updated, existing, False
        if (
            incoming.get("state") == AvailabilityState.OPERATOR_ON.value
            and existing.get("state") == AvailabilityState.OPERATOR_ON.value
            and "operator_window" in existing
        ):
            preserved_window = _operator_window_fields(
                existing.get("operator_window")
            )
            if preserved_window is None:
                raise ValueError("existing operator window is invalid")
            incoming = dict(incoming)
            incoming["operator_window"] = preserved_window

    updated[key] = incoming
    return updated, incoming, True


class _RetryableConsoleMutationConflict(RuntimeError):
    pass


def _console_mutation_incoming(
    mutation: ConsoleAvailabilityMutation,
    updated_at: str,
) -> Optional[dict]:
    if not mutation.project_operator:
        return None
    if mutation.kind is ConsoleMutationKind.ON:
        return _new_operator_record(
            AvailabilityState.OPERATOR_ON,
            AvailabilityProvenance.COORDINATOR_CONSOLE,
            updated_at,
            mutation.operator_window,
        )
    if mutation.kind is ConsoleMutationKind.OFF:
        return _new_operator_record(
            AvailabilityState.OPERATOR_OFF,
            AvailabilityProvenance.COORDINATOR_CONSOLE,
            updated_at,
            None,
        )
    return None


def _commit_console_mutation_once(
    mutation: ConsoleAvailabilityMutation,
    effective_path: str,
    attempt: int,
) -> ConsoleMutationResult:
    when, updated_at = _normalized_event_time(mutation.at)
    if mutation.project_operator and mutation.cid is None:
        raise ValueError("operator projection requires numeric cid")
    if mutation.kind is ConsoleMutationKind.ON:
        if mutation.working_entry is None or mutation.operator_window is None:
            raise ValueError("ON mutation requires both projections")
        if parse_shift_interval(mutation.working_entry, anchor=when) is None:
            raise ValueError("invalid legacy working interval")
    incoming = _console_mutation_incoming(mutation, updated_at)

    with _store_lock(effective_path):
        current, error = _read_json_dict(effective_path)
        if error:
            raise RuntimeError(f"availability store unreadable: {error}")
        records = current.get(STORE_KEY, {})
        if not isinstance(records, dict):
            raise RuntimeError("availability store is not an object")

        # Causal ordering is event-time, not lock-acquisition order. Dzięki temu
        # opóźniony ON nie odwróci nowszego STOP, nawet jeśli wejdzie do locka po nim.
        existing = records.get(mutation.cid)
        existing_at = (
            _parse_store_ts(existing.get("updated_at"))
            if mutation.project_operator and isinstance(existing, Mapping)
            else None
        )
        if existing_at is not None and existing_at > when:
            return ConsoleMutationResult(
                before_payload=current,
                payload=current,
                stored_record=existing,
                applied=False,
                attempts=attempt,
            )

        projected = mutation.apply_legacy(current)
        if mutation.project_operator:
            merged_records, stored, _changed = _merge_operator_record(
                records,
                mutation.cid,
                incoming,
                AvailabilityProvenance.COORDINATOR_CONSOLE,
                when,
            )
        else:
            merged_records = dict(records)
            stored = records.get(mutation.cid)

        # External writer, który nie respektuje locka, może podmienić plik w
        # czasie czystej projekcji. Ponowny odczyt wykrywa tę klasę i uruchamia
        # bounded retry na świeżym snapshotcie. Okno po tej sondzie pozostaje
        # hostowym HOLD-em aż zewnętrzny writer dołączy do locka.
        latest, latest_error = _read_json_dict(effective_path)
        if latest_error:
            raise RuntimeError(f"availability store unreadable: {latest_error}")
        if latest != current:
            raise _RetryableConsoleMutationConflict(
                "store changed outside canonical lock"
            )

        merged = dict(projected)
        merged[STORE_KEY] = merged_records
        merged["updated_at"] = updated_at
        legacy_changed = any(
            projected.get(key) != current.get(key)
            for key in ("excluded", "excluded_cids", "working")
        )
        if legacy_changed:
            merged[LEGACY_REVISION_KEY] = updated_at
        _atomic_write(effective_path, merged)
        return ConsoleMutationResult(
            before_payload=current,
            payload=merged,
            stored_record=stored,
            applied=True,
            attempts=attempt,
        )


def commit_console_mutation(
    mutation: ConsoleAvailabilityMutation,
    *,
    path: Optional[str] = None,
    max_attempts: int = 3,
) -> ConsoleMutationResult:
    """Aplikuje typed mutation do świeżego snapshotu z ograniczonym retry."""

    if not isinstance(mutation, ConsoleAvailabilityMutation):
        raise TypeError("console writer requires ConsoleAvailabilityMutation")
    if (
        not isinstance(max_attempts, int)
        or isinstance(max_attempts, bool)
        or not 1 <= max_attempts <= 5
    ):
        raise ValueError("max_attempts must be an integer in 1..5")
    effective_path = _effective_overrides_path(path)
    last_conflict: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _commit_console_mutation_once(
                mutation,
                effective_path,
                attempt,
            )
        except _RetryableConsoleMutationConflict as exc:
            last_conflict = exc
    raise RuntimeError(
        f"concurrent manual overrides update after {max_attempts} attempts"
    ) from last_conflict


def commit_console_projection(
    legacy_payload: Mapping[str, Any],
    cid: Any,
    state: Optional[AvailabilityState],
    *,
    base_payload: Optional[Mapping[str, Any]] = None,
    path: Optional[str] = None,
    at: Optional[datetime] = None,
    operator_window: Optional[Mapping[str, Any]] = None,
) -> Optional[dict]:
    """Atomowo zapisuje obie projekcje jednej komendy konsoli.

    Legacy ``working/excluded`` jest rollback projection, a CID record jest
    ownerem kontraktu ON.  Oba trafiają do jednego RMW pod tym samym lockiem i
    jednego ``fsync+rename``; żaden obserwator nie zobaczy połowy komendy.
    """

    if not isinstance(legacy_payload, Mapping):
        raise ValueError("manual overrides payload must be an object")
    if state is not None and state not in {
        AvailabilityState.OPERATOR_ON,
        AvailabilityState.OPERATOR_OFF,
    }:
        raise ValueError("persistent availability accepts only OPERATOR_ON/OFF")
    if operator_window is not None and state is not AvailabilityState.OPERATOR_ON:
        raise ValueError("operator window requires coordinator_console OPERATOR_ON")

    effective_path = _effective_overrides_path(path)
    key = _canon_cid(cid)
    when, updated_at = _normalized_event_time(at)
    incoming = _new_operator_record(
        state,
        AvailabilityProvenance.COORDINATOR_CONSOLE,
        updated_at,
        operator_window,
    )
    with _store_lock(effective_path):
        current, error = _read_json_dict(effective_path)
        if error:
            raise RuntimeError(f"availability store unreadable: {error}")
        current_records = current.get(STORE_KEY, {})
        if not isinstance(current_records, dict):
            raise RuntimeError("availability store is not an object")
        merged_records, stored, _changed = _merge_operator_record(
            current_records,
            key,
            incoming,
            AvailabilityProvenance.COORDINATOR_CONSOLE,
            when,
        )
        merged = dict(current)
        if base_payload is None:
            # Backward-compatible direct caller: pełna projekcja. Konsola
            # przekazuje base_payload i dostaje CAS per zmienione pole.
            merged.update(
                {
                    key: value
                    for key, value in legacy_payload.items()
                    if key not in {
                        STORE_KEY,
                        "updated_at",
                        LEGACY_REVISION_KEY,
                    }
                }
            )
            changed_keys = {
                key
                for key in legacy_payload
                if key not in {
                    STORE_KEY,
                    "updated_at",
                    LEGACY_REVISION_KEY,
                }
            }
        else:
            if current.get(LEGACY_REVISION_KEY, "") != base_payload.get(
                LEGACY_REVISION_KEY, ""
            ):
                raise RuntimeError(
                    "concurrent manual overrides update; retry command"
                )
            changed_keys = {
                key
                for key in set(base_payload) | set(legacy_payload)
                if key not in {
                    STORE_KEY,
                    "updated_at",
                    LEGACY_REVISION_KEY,
                }
                and base_payload.get(key) != legacy_payload.get(key)
            }
            defaults = {
                "excluded": [],
                "excluded_cids": [],
                "working": {},
            }
            for changed_key in changed_keys:
                expected = base_payload.get(
                    changed_key, defaults.get(changed_key)
                )
                current_value = current.get(
                    changed_key, defaults.get(changed_key)
                )
                if current_value != expected:
                    raise RuntimeError(
                        "concurrent manual overrides update; retry command"
                    )
            for changed_key in changed_keys:
                if changed_key in legacy_payload:
                    merged[changed_key] = legacy_payload[changed_key]
                else:
                    merged.pop(changed_key, None)
        merged[STORE_KEY] = merged_records
        merged["updated_at"] = updated_at
        if changed_keys:
            merged[LEGACY_REVISION_KEY] = updated_at
        _atomic_write(effective_path, merged)
    return stored


def set_operator_availability(
    cid: Any,
    state: Optional[AvailabilityState],
    provenance: AvailabilityProvenance,
    *,
    path: Optional[str] = None,
    at: Optional[datetime] = None,
    operator_window: Optional[Mapping[str, Any]] = None,
) -> Optional[dict]:
    """Jedyny writer ``availability_by_cid``.

    ``None`` usuwa jawny stan (bursztynowy/neutralny przed zmianą), więc decyzja
    znów wynika wyłącznie z grafiku. Ostatni jawny ON/OFF wygrywa.
    """
    path = _effective_overrides_path(path)
    key = _canon_cid(cid)
    if state is not None and state not in {
        AvailabilityState.OPERATOR_ON,
        AvailabilityState.OPERATOR_OFF,
    }:
        raise ValueError("persistent availability accepts only OPERATOR_ON/OFF")
    if provenance not in {
        AvailabilityProvenance.COORDINATOR_CONSOLE,
        AvailabilityProvenance.ASSIGNMENT_EVENT,
    }:
        raise ValueError("invalid persistent availability provenance")
    if operator_window is not None and not (
        state is AvailabilityState.OPERATOR_ON
        and provenance is AvailabilityProvenance.COORDINATOR_CONSOLE
    ):
        raise ValueError(
            "operator window requires coordinator_console OPERATOR_ON"
        )
    when, updated_at = _normalized_event_time(at)
    record = _new_operator_record(
        state,
        provenance,
        updated_at,
        operator_window,
    )
    with _store_lock(path):
        data, error = _read_json_dict(path)
        if error:
            raise RuntimeError(f"availability store unreadable: {error}")
        records = data.get(STORE_KEY, {})
        if not isinstance(records, dict):
            raise RuntimeError("availability store is not an object")
        # R-POOL-TRUTH precedencja + field-level merge żyją w jednym helperze,
        # więc assignment i konsola nie mają bliźniaczych polityk.
        records, stored, changed = _merge_operator_record(
            records,
            key,
            record,
            provenance,
            when,
        )
        if not changed:
            return stored
        data[STORE_KEY] = records
        data["updated_at"] = updated_at
        _atomic_write(path, data)
    return stored
