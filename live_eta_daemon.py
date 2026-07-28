"""Jedyny producent żywego ETA.

Proces zbiera jeden snapshot planów, orders_state i GPS, buduje kanoniczne trasy
przez ``route_order`` i publikuje cały cykl jednym atomowym zapisem. Wszystkie
powierzchnie są read-only.
"""
from __future__ import annotations

import json
import logging
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from dispatch_v2 import live_eta, osrm_client, plan_manager, route_order

ORDERS_STATE_PATH = Path(
    "/root/.openclaw/workspace/dispatch_state/orders_state.json"
)
GPS_PATH = Path(
    "/root/.openclaw/workspace/dispatch_state/gps_positions_pwa.json"
)
ACTIVE_STATUSES = frozenset({"assigned", "picked_up"})
_log = logging.getLogger("live_eta")
_running = True


def _load_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _valid_coord(value: object) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return False
    try:
        lat, lon = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return False
    return -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0.0, 0.0)


def _start(gps_row: object) -> tuple[float, float] | None:
    """Legacy start (R3 flaga OFF): celowo bez kontroli timestampu."""
    if not isinstance(gps_row, Mapping):
        return None
    value = (gps_row.get("lat"), gps_row.get("lon"))
    return (float(value[0]), float(value[1])) if _valid_coord(value) else None


def _warm_source_enabled() -> bool:
    """Hot-reload z kanonicznego flags.json; brak/błąd = bezpieczne OFF."""
    try:
        from dispatch_v2 import common

        return common.decision_flag("ENABLE_LIVE_ETA_WARM_SOURCE")
    except Exception:
        return False


def _source_start(
    courier_id: str,
    gps_row: object,
    orders_state: Mapping[str, object],
    now: datetime,
) -> tuple[tuple[float, float] | None, str]:
    """R3: jedyny selektor startu i klasy źródła LIVE/WARM/PLANNED."""
    if isinstance(gps_row, Mapping):
        coord = _start(gps_row)
        gps_at = live_eta._as_utc(gps_row.get("timestamp"))
        if coord is not None and gps_at is not None:
            age_s = (now - gps_at).total_seconds()
            if (
                live_eta.classify_position_contract("gps", age_s)
                == live_eta.SOURCE_LIVE
            ):
                return coord, live_eta.SOURCE_LIVE

    # Reużywamy jedynego istniejącego parsera history[].at i mapowania eventu
    # na współrzędne. Jego ogólny limit 6 h jest tylko preselekcją; kontrakt R3
    # stosuje tutaj własny, ostrzejszy próg 180 s.
    try:
        from dispatch_v2 import plan_recheck

        event = plan_recheck._last_event_anchor(
            courier_id, dict(orders_state), now
        )
    except Exception:
        event = None
    if event is not None:
        coord, event_at = event
        age_s = (now - event_at).total_seconds()
        if (
            live_eta.classify_position_contract("last_event", age_s)
            == live_eta.SOURCE_WARM
        ):
            return coord, live_eta.SOURCE_WARM
    return None, live_eta.SOURCE_PLANNED


def _available_floor(courier_id: str, now: datetime) -> str | None:
    try:
        from dispatch_v2 import courier_resolver

        value, _source = courier_resolver.resolve_available_from_by_cid(
            courier_id, now
        )
        return value.isoformat() if value is not None and value > now else None
    except Exception:
        return None


def build_routes(
    plans: Mapping[str, object],
    orders_state: Mapping[str, object],
    gps: Mapping[str, object],
    *,
    now: datetime,
    warm_source_enabled: bool | None = None,
) -> list[dict]:
    """Autorytatywne wejście kalkulatora dla wszystkich aktywnych worków."""
    source_contract = (
        _warm_source_enabled()
        if warm_source_enabled is None
        else bool(warm_source_enabled)
    )
    bags: dict[str, list[dict]] = {}
    for order_id, raw in orders_state.items():
        if not isinstance(raw, Mapping) or raw.get("status") not in ACTIVE_STATUSES:
            continue
        courier_id = raw.get("courier_id")
        if courier_id is None:
            continue
        bags.setdefault(str(courier_id), []).append(
            {**dict(raw), "order_id": str(order_id)}
        )

    routes: list[dict] = []
    for courier_id, bag in sorted(bags.items()):
        raw_plan = plans.get(courier_id)
        plan = (
            raw_plan
            if isinstance(raw_plan, Mapping)
            and raw_plan.get("invalidated_at") is None
            else None
        )
        sequence = route_order.order_podjazdy(
            bag,
            plan_doc=plan,
            plan_aware=True,
            trust_canon=True,
        )
        available_floor = _available_floor(courier_id, now)
        plan_steps = {
            (str(step.get("order_id")), step.get("type")): step
            for step in ((plan or {}).get("stops") or [])
            if isinstance(step, Mapping)
        }
        stops: list[dict] = []
        route_complete = True
        by_id = {str(order["order_id"]): order for order in bag}
        for kind, order_ids in sequence:
            first = by_id.get(str(order_ids[0]))
            if first is None:
                if source_contract:
                    stops.append(
                        {
                            "kind": kind,
                            "order_ids": [str(order_id) for order_id in order_ids],
                            "coord": None,
                            "floor_at": [],
                            "dwell_s": 120.0 if kind == "pickup" else 60.0,
                            "planned_at": None,
                            "unpriced_reason": "missing_order",
                        }
                    )
                    continue
                route_complete = False
                break
            coord = (
                first.get("pickup_coords")
                if kind == "pickup"
                else first.get("delivery_coords")
            )
            if not _valid_coord(coord):
                if source_contract:
                    coord = None
                else:
                    route_complete = False
                    break
            if coord is None and not source_contract:
                route_complete = False
                break
            floors: list[object] = []
            if kind == "pickup":
                for order_id in order_ids:
                    order = by_id.get(str(order_id)) or {}
                    floors.extend(
                        (
                            order.get("czas_kuriera_warsaw"),
                            order.get("pickup_at_warsaw"),
                        )
                    )
                floors.append(available_floor)
            dwell_values = []
            planned_values: list[datetime] = []
            for order_id in order_ids:
                step = plan_steps.get((str(order_id), kind)) or {}
                try:
                    dwell_values.append(float(step.get("dwell_min")) * 60.0)
                except (TypeError, ValueError):
                    pass
                planned = live_eta._as_utc(step.get("predicted_at"))
                if planned is not None:
                    planned_values.append(planned)
            stops.append(
                {
                    "kind": kind,
                    "order_ids": [str(order_id) for order_id in order_ids],
                    "coord": coord,
                    "floor_at": floors,
                    "dwell_s": max(
                        dwell_values,
                        default=120.0 if kind == "pickup" else 60.0,
                    ),
                    **(
                        {
                            "planned_at": (
                                live_eta._iso_utc(max(planned_values))
                                if planned_values
                                else None
                            ),
                            "unpriced_reason": (
                                "bad_coords" if coord is None else None
                            ),
                        }
                        if source_contract
                        else {}
                    ),
                }
            )
        if not route_complete and not source_contract:
            stops = []
        if source_contract:
            start, start_source = _source_start(
                courier_id, gps.get(courier_id), orders_state, now
            )
        else:
            start, start_source = _start(gps.get(courier_id)), None
        routes.append(
            {
                "courier_id": courier_id,
                "start": start,
                "stops": stops,
                **(
                    {
                        "start_source": start_source,
                        "source_contract": True,
                    }
                    if source_contract
                    else {}
                ),
            }
        )
    return routes


def _duration_provider(
    points: Sequence[tuple[float, float]],
) -> Sequence[float] | None:
    matrix = osrm_client.table(points, points)
    if not matrix:
        return None
    out: list[float] = []
    for index in range(len(points) - 1):
        try:
            raw = matrix[index][index + 1].get("duration_s")
            value = float(raw)
        except (AttributeError, IndexError, TypeError, ValueError):
            return None
        if value >= 9e8:
            return None
        out.append(max(0.0, value))
    return out


def refresh_once(*, now: datetime | None = None) -> dict[str, dict]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    routes = build_routes(
        plan_manager.load_plans(),
        _load_json(ORDERS_STATE_PATH),
        _load_json(GPS_PATH),
        now=current,
    )
    return live_eta.write_cycle(
        routes,
        now=current,
        duration_provider=_duration_provider,
    )


def _stop(_signum, _frame) -> None:
    global _running
    _running = False


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while _running:
        started = time.monotonic()
        try:
            snapshots = refresh_once()
            _log.info("LIVE_ETA cycle couriers=%d", len(snapshots))
        except Exception:
            _log.exception("LIVE_ETA cycle failed")
        wait = max(0.1, live_eta.CYCLE_SECONDS - (time.monotonic() - started))
        time.sleep(wait)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
