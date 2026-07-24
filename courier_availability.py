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
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Mapping, Optional


OVERRIDES_PATH = "/root/.openclaw/workspace/dispatch_state/manual_overrides.json"
GRAFIK_FULL_NAMES_PATH = (
    "/root/.openclaw/workspace/dispatch_state/grafik_full_names.json"
)
STORE_KEY = "availability_by_cid"


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
    detail: Optional[str] = None


@dataclass(frozen=True)
class AvailabilityContext:
    operator_records: Mapping[str, dict]
    operator_error: Optional[str]
    schedule: Mapping[str, Any]
    schedule_error: Optional[str]
    schedule_names_by_cid: Mapping[str, str]
    identity_error: Optional[str]


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


def _operator_records(path: str) -> tuple[dict, Optional[str]]:
    data, error = _read_json_dict(path)
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


def load_context(
    schedule: Optional[Mapping[str, Any]],
    *,
    schedule_error: Optional[str] = None,
    overrides_path: str = OVERRIDES_PATH,
    grafik_names_path: str = GRAFIK_FULL_NAMES_PATH,
) -> AvailabilityContext:
    """Ładuje oba CID-keyed wejścia raz na wywołanie ``dispatchable_fleet``."""
    records, operator_error = _operator_records(overrides_path)
    names, identity_error = _schedule_names(grafik_names_path)
    schedule_map: Mapping[str, Any] = schedule if isinstance(schedule, Mapping) else {}
    if schedule is not None and not isinstance(schedule, Mapping):
        schedule_error = schedule_error or "schedule_not_object"
    if schedule is None:
        schedule_error = schedule_error or "schedule_missing"
    return AvailabilityContext(
        operator_records=records,
        operator_error=operator_error,
        schedule=schedule_map,
        schedule_error=schedule_error,
        schedule_names_by_cid=names,
        identity_error=identity_error,
    )


def resolve(
    context: AvailabilityContext,
    cid: Any,
    *,
    is_on_shift: Callable[[str, Mapping[str, Any]], tuple[bool, str]],
    mins_to_shift_start: Callable[[dict], Optional[float]],
    pre_shift_window_min: float,
) -> AvailabilityDecision:
    """Rozstrzyga jedną dostępność. Nie używa nazw floty ani fuzzy fallbacków."""
    key = _canon_cid(cid)
    if context.operator_error:
        return AvailabilityDecision(
            key,
            AvailabilityState.UNKNOWN_DATA_ERROR,
            AvailabilityProvenance.OPERATOR_STORE_ERROR,
            False,
            detail=context.operator_error,
        )

    operator = context.operator_records.get(key)
    if operator:
        state = AvailabilityState(operator["state"])
        return AvailabilityDecision(
            key,
            state,
            AvailabilityProvenance(operator["provenance"]),
            state is AvailabilityState.OPERATOR_ON,
        )

    if context.schedule_error:
        return AvailabilityDecision(
            key,
            AvailabilityState.UNKNOWN_DATA_ERROR,
            AvailabilityProvenance.SCHEDULE_LOAD_ERROR,
            False,
            detail=context.schedule_error,
        )
    if context.identity_error:
        return AvailabilityDecision(
            key,
            AvailabilityState.UNKNOWN_DATA_ERROR,
            AvailabilityProvenance.SCHEDULE_IDENTITY_ERROR,
            False,
            detail=context.identity_error,
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
        )

    entry = context.schedule[schedule_name]
    if entry is None:
        return AvailabilityDecision(
            key,
            AvailabilityState.OFF_PLANNED,
            AvailabilityProvenance.SCHEDULE_EMPTY_DAY,
            False,
            schedule_name=schedule_name,
        )
    if not isinstance(entry, dict):
        return AvailabilityDecision(
            key,
            AvailabilityState.UNKNOWN_DATA_ERROR,
            AvailabilityProvenance.SCHEDULE_ENTRY_ERROR,
            False,
            schedule_name=schedule_name,
            detail="schedule_entry_not_object",
        )

    try:
        on_shift, _reason = is_on_shift(schedule_name, context.schedule)
        if on_shift:
            return AvailabilityDecision(
                key,
                AvailabilityState.SCHEDULED_ON,
                AvailabilityProvenance.SCHEDULE_ON_SHIFT,
                True,
                schedule_name=schedule_name,
                schedule_entry=entry,
            )
        mins = mins_to_shift_start(entry)
    except Exception as exc:
        return AvailabilityDecision(
            key,
            AvailabilityState.UNKNOWN_DATA_ERROR,
            AvailabilityProvenance.SCHEDULE_ENTRY_ERROR,
            False,
            schedule_name=schedule_name,
            schedule_entry=entry,
            detail=type(exc).__name__,
        )
    if mins is not None and 0 < mins <= pre_shift_window_min:
        return AvailabilityDecision(
            key,
            AvailabilityState.SCHEDULED_ON,
            AvailabilityProvenance.SCHEDULE_PRE_SHIFT,
            True,
            schedule_name=schedule_name,
            schedule_entry=entry,
        )
    return AvailabilityDecision(
        key,
        AvailabilityState.OFF_PLANNED,
        AvailabilityProvenance.SCHEDULE_OUTSIDE_WINDOW,
        False,
        schedule_name=schedule_name,
        schedule_entry=entry,
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


def save_legacy_payload(data: dict, *, path: str = OVERRIDES_PATH) -> None:
    """Zapis legacy pól bez prawa nadpisania kanonicznego kontraktu.

    ``manual_overrides`` może rozpocząć RMW przed równoległym assignmentem. Pod
    wspólnym lockiem ponownie czytamy bieżący store i zawsze zachowujemy jego
    ``availability_by_cid``; dzięki temu stary payload nie kasuje nowszego ON/OFF.
    """
    if not isinstance(data, dict):
        raise ValueError("manual overrides payload must be an object")
    with _store_lock(path):
        current, error = _read_json_dict(path)
        if error:
            raise RuntimeError(f"availability store unreadable: {error}")
        merged = dict(data)
        current_records = current.get(STORE_KEY, {})
        if not isinstance(current_records, dict):
            raise RuntimeError("availability store is not an object")
        merged[STORE_KEY] = current_records
        merged["updated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write(path, merged)


def set_operator_availability(
    cid: Any,
    state: Optional[AvailabilityState],
    provenance: AvailabilityProvenance,
    *,
    path: Optional[str] = None,
    at: Optional[datetime] = None,
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
    when = at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    updated_at = when.astimezone(timezone.utc).isoformat()
    record = None if state is None else {
        "state": state.value,
        "provenance": provenance.value,
        "updated_at": updated_at,
    }
    with _store_lock(path):
        data, error = _read_json_dict(path)
        if error:
            raise RuntimeError(f"availability store unreadable: {error}")
        records = data.get(STORE_KEY, {})
        if not isinstance(records, dict):
            raise RuntimeError("availability store is not an object")
        records = dict(records)
        if record is None:
            records.pop(key, None)
        else:
            # R-POOL-TRUTH precedencja: opóźniony ``COURIER_ASSIGNED`` (starszy w
            # czasie zdarzenia) NIE może wskrzesić/nadpisać nowszej jawnej decyzji
            # (np. koordynatorskiego OFF). O zwycięstwie decyduje czas zdarzenia,
            # nie kolejność przetworzenia. Jawny COORDINATOR_CONSOLE wygrywa remis.
            existing = records.get(key)
            if (
                provenance is AvailabilityProvenance.ASSIGNMENT_EVENT
                and isinstance(existing, dict)
            ):
                existing_ts = _parse_store_ts(existing.get("updated_at"))
                if existing_ts is not None and existing_ts >= when:
                    return existing
            records[key] = record
        data[STORE_KEY] = records
        data["updated_at"] = updated_at
        _atomic_write(path, data)
    return record
