"""Kanoniczne, żywe ETA dla wszystkich powierzchni Ziomka.

Ten moduł jest jedynym właścicielem obliczenia ``zegar + trasa -> ETA``.
Konsola, most kuriera i Android nie wyliczają czasu samodzielnie: dostają
ten sam wersjonowany snapshot i wyłącznie go serializują/renderują.

Snapshot jest współdzielony między procesami w ``dispatch_state``. Jedyny
``live_eta_daemon`` wiąże zegar, pozycję, kolejność, współrzędne i floory,
liczy trasy raz na cykl i publikuje je jednym atomowym zapisem. Readery nigdy
nie wywołują routingu. Następny cykl albo zmiana wejścia tworzy nową wartość —
ETA nie jest zamrożone.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from dispatch_v2 import common as C
from dispatch_v2 import route_order

SCHEMA_VERSION = 1
CYCLE_SECONDS = 10
# R3 (2026-07-28): jeden kanoniczny kontrakt świeżości i nazw źródeł dla
# snapshotu live ETA. Nie używać progów courier_resolvera — to inny produktowy
# kontrakt (flota/scoring), podczas gdy tutaj owner zatwierdził LIVE <=120 s
# oraz WARM(last_event) <=180 s.
LIVE_POSITION_MAX_AGE_SECONDS = 120
WARM_EVENT_MAX_AGE_SECONDS = 180
SOURCE_LIVE = "live"
SOURCE_WARM = "warm"
SOURCE_PLANNED = "planned"
ETA_SOURCES = frozenset({SOURCE_LIVE, SOURCE_WARM, SOURCE_PLANNED})
_log = logging.getLogger("live_eta")


def classify_position_contract(source: object, age_seconds: object) -> str:
    """R3: jeden klasyfikator źródła LIVE/WARM/PLANNED.

    ``gps`` kwalifikuje się jako LIVE wyłącznie w domkniętym oknie 0..120 s,
    a ``last_event`` jako WARM wyłącznie w oknie 0..180 s. Każdy brak, przyszły
    timestamp, nieznane źródło lub przekroczenie progu jest PLANOWE.
    """
    if isinstance(age_seconds, bool) or not isinstance(
        age_seconds, (int, float)
    ):
        return SOURCE_PLANNED
    age = float(age_seconds)
    if source == "gps" and 0.0 <= age <= LIVE_POSITION_MAX_AGE_SECONDS:
        return SOURCE_LIVE
    if source == "last_event" and 0.0 <= age <= WARM_EVENT_MAX_AGE_SECONDS:
        return SOURCE_WARM
    return SOURCE_PLANNED
# Snapshot starszy niż tyle = martwy/zawieszony daemon → NIE serwuj (konsument fallback).
# 6 pominiętych cykli; bez tego panel/kafel/mapa/apka pokazują starą godzinę bez końca.
STALE_AFTER_SECONDS = 6 * CYCLE_SECONDS
WARSAW = ZoneInfo("Europe/Warsaw")

SNAPSHOT_FILE = Path(
    "/root/.openclaw/workspace/dispatch_state/live_eta_snapshot.json"
)
LOCK_FILE = Path(
    "/root/.openclaw/workspace/dispatch_state/live_eta_snapshot.lock"
)

DurationProvider = Callable[[Sequence[tuple[float, float]]], Sequence[float] | None]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: object) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _coord(value: object) -> tuple[float, float] | None:
    if isinstance(value, Mapping):
        lat, lon = value.get("lat"), value.get("lon")
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        lat, lon = value[0], value[1]
    else:
        return None
    try:
        return round(float(lat), 6), round(float(lon), 6)
    except (TypeError, ValueError):
        return None


def _normalize_stops(
    stops: Iterable[Mapping[str, object]], *, source_contract: bool = False
) -> list[dict]:
    """Ujednolić stopy już zgrupowane przez kanoniczny ``route_order``.

    ``stop_id`` + ``order_ids`` są jedyną tożsamością fizycznego stopu.
    Współrzędne służą wyłącznie routingowi i NIGDY nie scalają membershipu.
    """
    out: list[dict] = []
    for raw in stops:
        kind = str(raw.get("kind") or raw.get("type") or "")
        if kind not in {"pickup", "dropoff"}:
            continue
        ids_raw = raw.get("order_ids")
        if isinstance(ids_raw, (list, tuple)):
            order_ids = [str(v) for v in ids_raw if v is not None]
        elif raw.get("order_id") is not None:
            order_ids = [str(raw.get("order_id"))]
        else:
            order_ids = []
        stop_id = raw.get("stop_id")
        point = _coord(raw.get("coord"))
        if (
            not isinstance(stop_id, str)
            or not stop_id
            or not order_ids
            or (point is None and not source_contract)
        ):
            continue
        floor_raw = raw.get("floor_at")
        floor_values = (
            floor_raw
            if isinstance(floor_raw, (list, tuple))
            else [floor_raw]
        )
        floor = max(
            (
                dt
                for dt in (_as_utc(v) for v in floor_values)
                if dt is not None
            ),
            default=None,
        )
        dwell_s = float(raw.get("dwell_s") or (120 if kind == "pickup" else 60))
        normalized = {
            "stop_id": stop_id,
            "kind": kind,
            "order_ids": sorted(dict.fromkeys(order_ids)),
            "coord": [point[0], point[1]] if point is not None else None,
            "floor_at": _iso_utc(floor) if floor is not None else None,
            "dwell_s": max(0.0, dwell_s),
        }
        if source_contract:
            planned = _as_utc(raw.get("planned_at"))
            normalized["planned_at"] = (
                _iso_utc(planned) if planned is not None else None
            )
            normalized["unpriced_reason"] = (
                str(raw.get("unpriced_reason") or "bad_coords")
                if point is None
                else None
            )
        out.append(normalized)
    return out


def calculate_live_eta(
    *,
    courier_id: object,
    start: object,
    stops: Iterable[Mapping[str, object]],
    now: datetime,
    duration_provider: DurationProvider,
    cycle_id: int,
    start_source: object = None,
    source_contract: bool = False,
    plan_version: object = None,
    sequence_hash: str | None = None,
) -> dict:
    """JEDYNY kalkulator ETA: jeden snapshot całej trasy, jeden duration-provider."""
    start_coord = _coord(start)
    normalized = _normalize_stops(list(stops), source_contract=source_contract)
    rendered_hash = route_order.route_sequence_hash(normalized)
    if sequence_hash is not None and sequence_hash != rendered_hash:
        raise ValueError("route sequence hash does not match rendered stops")
    generated_at = _iso_utc(now)
    base = {
        "schema_version": SCHEMA_VERSION,
        "courier_id": str(courier_id),
        "cycle_id": int(cycle_id),
        "generated_at": generated_at,
        "plan_version": plan_version,
        "sequence_hash": rendered_hash,
        "stops": [],
        "orders": {},
    }
    if not source_contract and (start_coord is None or not normalized):
        return base
    if source_contract:
        return _calculate_source_eta(
            base=base,
            start_coord=start_coord,
            start_source=start_source,
            normalized=normalized,
            now=now,
            duration_provider=duration_provider,
        )
    points = [start_coord] + [
        (float(stop["coord"][0]), float(stop["coord"][1])) for stop in normalized
    ]
    legs = duration_provider(points)
    if legs is None or len(legs) != len(normalized):
        return base

    cursor = now.astimezone(timezone.utc)
    for index, (stop, raw_leg) in enumerate(zip(normalized, legs)):
        try:
            leg_s = max(0.0, float(raw_leg))
        except (TypeError, ValueError):
            return base
        arrival = cursor + timedelta(seconds=leg_s)
        floor = _as_utc(stop.get("floor_at"))
        if floor is not None and floor > arrival:
            arrival = floor
        eta_at = _iso_utc(arrival)
        eta_hhmm = arrival.astimezone(WARSAW).strftime("%H:%M")
        projected = {
            "position": index,
            "stop_id": stop["stop_id"],
            "kind": stop["kind"],
            "order_ids": stop["order_ids"],
            "eta_at": eta_at,
            "eta_hhmm": eta_hhmm,
        }
        base["stops"].append(projected)
        field = "pickup_at" if stop["kind"] == "pickup" else "delivery_at"
        for oid in stop["order_ids"]:
            slot = base["orders"].setdefault(
                oid, {"pickup_at": None, "delivery_at": None}
            )
            slot[field] = eta_at
        cursor = arrival + timedelta(seconds=float(stop["dwell_s"]))
    return base


def _calculate_source_eta(
    *,
    base: dict,
    start_coord: tuple[float, float] | None,
    start_source: object,
    normalized: list[dict],
    now: datetime,
    duration_provider: DurationProvider,
) -> dict:
    """R3: wyceń każdy stop niezależnie i zawsze opisz źródło.

    Brak współrzędnych jest lokalną dziurą ``planned`` zamiast kasowania całej
    trasy. Gdy plan ma ``predicted_at``, stanowi on konserwatywną kotwicę czasu
    dla dalszych poprawnych stopów; bez niej kalkulator kontynuuje od ostatniej
    znanej geometrii i co najmniej bieżącego kursora. Takie dalsze ETA pozostają
    ``planned`` (nigdy nie podszywają się pod LIVE/WARM). Stary reader nadal
    widzi wyłącznie ``orders``.
    """
    route_source = (
        str(start_source)
        if str(start_source) in {SOURCE_LIVE, SOURCE_WARM}
        else SOURCE_PLANNED
    )
    cursor = now.astimezone(timezone.utc)
    anchor = start_coord
    degraded_to_planned = route_source == SOURCE_PLANNED

    for index, stop in enumerate(normalized):
        coord = _coord(stop.get("coord"))
        eta_at: str | None = None
        eta_hhmm: str | None = None
        reason: str | None = None
        source = (
            SOURCE_PLANNED
            if degraded_to_planned or coord is None
            else route_source
        )
        arrival: datetime | None = None

        if coord is None:
            reason = str(stop.get("unpriced_reason") or "bad_coords")
            planned = _as_utc(stop.get("planned_at"))
            floor = _as_utc(stop.get("floor_at"))
            time_anchor = max(
                (value for value in (planned, floor) if value is not None),
                default=cursor,
            )
            # Nie znamy przejazdu do brakującego punktu, więc samego stopu NIE
            # wyceniamy. Dalszą trasę wolno jednak policzyć od ostatniej znanej
            # geometrii, po przesunięciu zegara co najmniej do planu/flooru+dwell;
            # wszystkie takie dalsze stopy są jawnie PLANNED, nigdy LIVE/WARM.
            cursor = max(cursor, time_anchor) + timedelta(
                seconds=float(stop["dwell_s"])
            )
            degraded_to_planned = True
        elif anchor is None:
            reason = "no_position"
        else:
            legs = duration_provider([anchor, coord])
            if legs is None or len(legs) != 1:
                reason = "osrm_fail"
            else:
                try:
                    leg_s = max(0.0, float(legs[0]))
                except (TypeError, ValueError):
                    reason = "osrm_fail"
                else:
                    arrival = cursor + timedelta(seconds=leg_s)
                    floor = _as_utc(stop.get("floor_at"))
                    planned = _as_utc(stop.get("planned_at"))
                    if floor is not None and floor > arrival:
                        arrival = floor
                    if degraded_to_planned and planned is not None and planned > arrival:
                        arrival = planned
                    eta_at = _iso_utc(arrival)
                    eta_hhmm = arrival.astimezone(WARSAW).strftime("%H:%M")

        projected = {
            "position": index,
            "stop_id": stop["stop_id"],
            "kind": stop["kind"],
            "order_ids": stop["order_ids"],
            "eta_at": eta_at,
            "eta_hhmm": eta_hhmm,
            "source": source,
        }
        if reason is not None:
            projected["unpriced_reason"] = reason
        base["stops"].append(projected)

        field = "pickup_at" if stop["kind"] == "pickup" else "delivery_at"
        for oid in stop["order_ids"]:
            slot = base["orders"].setdefault(
                oid, {"pickup_at": None, "delivery_at": None}
            )
            slot[field] = eta_at

        if arrival is not None:
            cursor = arrival + timedelta(seconds=float(stop["dwell_s"]))
            anchor = coord
        elif coord is not None and reason == "osrm_fail":
            # OSRM jednej nogi nie może zatruć pozostałych; następny stop nadal
            # próbuje od ostatniej zweryfikowanej kotwicy.
            degraded_to_planned = True
    return base


def _read_store() -> dict:
    try:
        with SNAPSHOT_FILE.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("schema_version") == SCHEMA_VERSION and isinstance(
            data.get("entries"), dict
        ):
            return data
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {"schema_version": SCHEMA_VERSION, "entries": {}}


def _read_store_fresh() -> dict:
    """Kanoniczny store TYLKO jeśli świeży. Martwy/zawieszony daemon (brak/stary
    ``generated_at``) ⇒ traktuj jak brak danych (puste entries), by żaden konsument
    nie serwował przeterminowanej godziny. Jedyne miejsce kontroli świeżości."""
    store = _read_store()
    generated = _as_utc(store.get("generated_at"))
    if generated is None:
        return {"schema_version": SCHEMA_VERSION, "entries": {}}
    if (_utc_now() - generated).total_seconds() > STALE_AFTER_SECONDS:
        return {"schema_version": SCHEMA_VERSION, "entries": {}}
    return store


def _atomic_write_store(store: Mapping[str, object]) -> None:
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(SNAPSHOT_FILE.parent), prefix=".live_eta_snapshot.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                store,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, SNAPSHOT_FILE)
        dir_fd = os.open(SNAPSHOT_FILE.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_cycle(
    routes: Iterable[Mapping[str, object]],
    *,
    now: datetime | None = None,
    duration_provider: DurationProvider,
    cycle_seconds: int = CYCLE_SECONDS,
) -> dict[str, dict]:
    """Jedyny writer: policzyć i atomowo opublikować cały cykl wszystkich kurierów.

    ``routes`` powstaje wyłącznie w ``live_eta_daemon`` z aktualnych planów,
    orders_state i GPS. Endpointy/UI nie mogą wywołać tej funkcji.
    """
    current = (now or _utc_now()).astimezone(timezone.utc)
    cycle_id = int(current.timestamp() // max(1, int(cycle_seconds)))
    route_list = list(routes)
    input_fingerprint = hashlib.sha256(
        json.dumps(
            route_list, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        existing = _read_store()
        if (
            existing.get("cycle_id") == cycle_id
            and existing.get("input_fingerprint") == input_fingerprint
        ):
            # Producent zwraca RAW cache bieżącego cyklu (świeży z definicji: zgodny
            # cycle_id+fingerprint) — NIE przez read_all(), bo to konsumencki reader
            # ze staleness-guardem (który przy odtwarzaniu historycznego now dałby pusto).
            return {
                str(cid): entry["snapshot"]
                for cid, entry in existing.get("entries", {}).items()
                if isinstance(entry, dict) and isinstance(entry.get("snapshot"), dict)
            }
        entries: dict[str, dict] = {}
        for route in route_list:
            courier_id = str(route.get("courier_id"))
            if not courier_id or courier_id == "None":
                continue
            try:
                snapshot = calculate_live_eta(
                    courier_id=courier_id,
                    start=route.get("start"),
                    start_source=route.get("start_source"),
                    stops=route.get("stops") or [],
                    now=current,
                    duration_provider=duration_provider,
                    cycle_id=cycle_id,
                    source_contract=bool(route.get("source_contract")),
                    plan_version=route.get("plan_version"),
                    sequence_hash=route.get("sequence_hash"),
                )
            except Exception as exc:  # one malformed courier cannot poison a cycle
                _log.warning(
                    "LIVE_ETA route fail-soft courier_id=%s error=%s",
                    courier_id,
                    type(exc).__name__,
                )
                continue
            entries[courier_id] = {"snapshot": snapshot}
        store = {
            "schema_version": SCHEMA_VERSION,
            "cycle_id": cycle_id,
            "input_fingerprint": input_fingerprint,
            "generated_at": _iso_utc(current),
            "entries": entries,
        }
        _atomic_write_store(store)
        snapshots = {
            courier_id: entry["snapshot"]
            for courier_id, entry in entries.items()
        }
        try:
            if C.decision_flag("ENABLE_LIVE_ETA_HISTORY_LOG"):
                from dispatch_v2 import live_eta_history

                live_eta_history.record_live_eta_cycle(snapshots)
        except Exception as exc:  # telemetry cannot invalidate a published cycle
            _log.warning("live ETA history hook fail-safe: %s", type(exc).__name__)
        return snapshots


def read_latest(courier_id: object) -> dict | None:
    """Read-only projekcja ostatniego kanonicznego snapshotu danego kuriera."""
    entry = _read_store_fresh().get("entries", {}).get(str(courier_id))
    snapshot = entry.get("snapshot") if isinstance(entry, dict) else None
    return snapshot if isinstance(snapshot, dict) else None


def read_all() -> dict[str, dict]:
    """Read-only mapa wszystkich snapshotów, bez jakiejkolwiek rekalkulacji."""
    out: dict[str, dict] = {}
    for courier_id, entry in _read_store_fresh().get("entries", {}).items():
        snapshot = entry.get("snapshot") if isinstance(entry, dict) else None
        if isinstance(snapshot, dict):
            out[str(courier_id)] = snapshot
    return out


def eta_for(snapshot: Mapping[str, object] | None, order_id: object, kind: str) -> str | None:
    """Jedyny reader pola powierzchniowego; zwraca ISO UTC albo ``None``."""
    if not isinstance(snapshot, Mapping):
        return None
    orders = snapshot.get("orders")
    row = orders.get(str(order_id)) if isinstance(orders, Mapping) else None
    if not isinstance(row, Mapping):
        return None
    field = "pickup_at" if kind == "pickup" else "delivery_at"
    value = row.get(field)
    return str(value) if value else None


def bind_snapshot_to_route(
    snapshot: Mapping[str, object] | None,
    rendered_stops: Iterable[object],
    *,
    current_plan_version: object,
    enforce: bool,
) -> tuple[Mapping[str, object] | None, dict[str, object]]:
    """Zwiąż snapshot ETA z dokładnie tą trasą i generacją, którą renderuje reader.

    OFF jest czystym legacy pass-through. ON jest fail-closed: snapshot bez
    kontraktu, z inną sekwencją albo z inną wersją planu nie może dostarczyć ETA.
    Konsumenci dostają status do DTO/telemetrii, ale nie implementują porównania.
    """
    snapshot_hash = (
        snapshot.get("sequence_hash") if isinstance(snapshot, Mapping) else None
    )
    snapshot_plan_version = (
        snapshot.get("plan_version") if isinstance(snapshot, Mapping) else None
    )
    if not enforce:
        return (
            snapshot if isinstance(snapshot, Mapping) else None,
            {
                "status": "unchecked",
                "current_plan_version": current_plan_version,
                "current_sequence_hash": None,
                "snapshot_plan_version": snapshot_plan_version,
                "snapshot_sequence_hash": snapshot_hash,
            },
        )

    try:
        current_hash = route_order.route_sequence_hash(rendered_stops)
        route_error = None
    except (TypeError, ValueError) as exc:
        current_hash = None
        route_error = type(exc).__name__
    contract: dict[str, object] = {
        "status": "unchecked",
        "current_plan_version": current_plan_version,
        "current_sequence_hash": current_hash,
        "snapshot_plan_version": snapshot_plan_version,
        "snapshot_sequence_hash": snapshot_hash,
    }
    if route_error is not None:
        contract["route_error"] = route_error
    if not isinstance(snapshot, Mapping):
        contract["status"] = "missing_snapshot"
        return None, contract
    if current_hash is None:
        contract["status"] = "invalid_rendered_sequence"
        return None, contract
    if not isinstance(snapshot_hash, str) or not snapshot_hash:
        contract["status"] = "unversioned_snapshot"
        return None, contract
    if snapshot_hash != current_hash:
        contract["status"] = "sequence_hash_mismatch"
        return None, contract
    if snapshot_plan_version != current_plan_version:
        contract["status"] = "plan_version_mismatch"
        return None, contract
    contract["status"] = "matched"
    return snapshot, contract
