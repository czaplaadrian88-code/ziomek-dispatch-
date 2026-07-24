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
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

SCHEMA_VERSION = 1
CYCLE_SECONDS = 10
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


def _normalize_stops(stops: Iterable[Mapping[str, object]]) -> list[dict]:
    """Ujednolić stop i scalić kolejne pozycje pod tym samym adresem.

    ``order_ids`` pozwala jednemu fizycznemu odbiorowi/dostawie nadać jeden ETA
    wszystkim zleceniom na tej pozycji.
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
        point = _coord(raw.get("coord"))
        if not order_ids or point is None:
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
            "kind": kind,
            "order_ids": sorted(dict.fromkeys(order_ids)),
            "coord": [point[0], point[1]],
            "floor_at": _iso_utc(floor) if floor is not None else None,
            "dwell_s": max(0.0, dwell_s),
        }
        if (
            out
            and out[-1]["kind"] == normalized["kind"]
            and out[-1]["coord"] == normalized["coord"]
        ):
            out[-1]["order_ids"] = sorted(
                dict.fromkeys(out[-1]["order_ids"] + normalized["order_ids"])
            )
            old_floor = _as_utc(out[-1]["floor_at"])
            if floor is not None and (old_floor is None or floor > old_floor):
                out[-1]["floor_at"] = _iso_utc(floor)
            out[-1]["dwell_s"] = max(out[-1]["dwell_s"], normalized["dwell_s"])
            continue
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
) -> dict:
    """JEDYNY kalkulator ETA: jeden snapshot całej trasy, jeden duration-provider."""
    start_coord = _coord(start)
    normalized = _normalize_stops(stops)
    generated_at = _iso_utc(now)
    base = {
        "schema_version": SCHEMA_VERSION,
        "courier_id": str(courier_id),
        "cycle_id": int(cycle_id),
        "generated_at": generated_at,
        "stops": [],
        "orders": {},
    }
    if start_coord is None or not normalized:
        return base
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
            snapshot = calculate_live_eta(
                courier_id=courier_id,
                start=route.get("start"),
                stops=route.get("stops") or [],
                now=current,
                duration_provider=duration_provider,
                cycle_id=cycle_id,
            )
            entries[courier_id] = {"snapshot": snapshot}
        store = {
            "schema_version": SCHEMA_VERSION,
            "cycle_id": cycle_id,
            "input_fingerprint": input_fingerprint,
            "generated_at": _iso_utc(current),
            "entries": entries,
        }
        _atomic_write_store(store)
        return {
            courier_id: entry["snapshot"]
            for courier_id, entry in entries.items()
        }


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
