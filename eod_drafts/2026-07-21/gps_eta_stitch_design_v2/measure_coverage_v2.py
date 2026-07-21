#!/usr/bin/env python3
"""Agregatowy pomiar GPS->ETA v2; wszystkie wejscia sa otwierane read-only.

Pelny pomiar wymaga snapshotu czterech zrodel: eta_calib.db,
gps_delivery_truth.jsonl, restaurant_dwell.json i courier_ground_truth.json.
Join jest fail-closed po (order_id, courier_id), a semantyka obserwabli jest
zgodna z tools/eta_ground_truth.py:340-418. Skrypt nie emituje ID, adresow,
koordynatow ani rekordow jednostkowych i niczego nie zapisuje.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

UTC = timezone.utc
WARSAW = ZoneInfo("Europe/Warsaw")
SCHEMA = "gps_eta_stitch.coverage.v2"
ASSIGNMENT_ACTIONS = {"PANEL_OVERRIDE", "PANEL_AGREE", "ASSIGN_DIRECT", "F7AGREE"}
GPS_TIME_FIELDS = (
    "_computed_at", "computed_at", "written_at", "button_delivered_at",
    "physical_delivered_at", "delivered_day",
)
OUTCOME_TIME_FIELDS = ("written_at", "ts_decision", "picked_up_at")


def _cid(value) -> Optional[str]:
    """Dokladna normalizacja kanonicznego eta_ground_truth._cid."""
    return None if value is None or str(value).strip() == "" else str(value).strip()


def _oid(value) -> Optional[str]:
    return None if value is None or str(value).strip() == "" else str(value).strip()


def _ts(value, *, naive_warsaw: bool = False) -> Optional[datetime]:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(float(value), UTC)
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            if not naive_warsaw:
                return None
            parsed = parsed.replace(tzinfo=WARSAW)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _read_mapping(path: Optional[Path]) -> dict:
    if path is None:
        return {}
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"mapping_not_object:{path.name}")
    return {_oid(key): row for key, row in value.items() if _oid(key) is not None}


def _record_time(row: Mapping, fields: Iterable[str], *, naive_warsaw: bool):
    for field in fields:
        if row.get(field) not in (None, ""):
            return _ts(row.get(field), naive_warsaw=naive_warsaw)
    return None


def _latest_by_oid(
    rows: Iterable[Mapping], fields: Iterable[str], *, as_of: Optional[datetime],
    naive_warsaw: bool,
) -> dict[str, dict]:
    """Kanoniczne latest-by-explicit-time; kolejność pliku nie jest oracle."""
    floor = datetime.min.replace(tzinfo=UTC)
    out: dict[str, tuple[tuple, dict]] = {}
    for row in rows:
        oid = _oid(row.get("order_id", row.get("oid")))
        if oid is None:
            continue
        ts = _record_time(row, fields, naive_warsaw=naive_warsaw)
        if as_of is not None and ts is not None and ts > as_of:
            continue
        canonical = json.dumps(row, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), default=str)
        key = (ts or floor, canonical)
        if oid not in out or key > out[oid][0]:
            out[oid] = (key, dict(row))
    return {oid: pair[1] for oid, pair in out.items()}


def _mapping_after_as_of(
    entry: object, fields: Iterable[str], as_of: datetime, *, naive_warsaw: bool,
) -> bool:
    if not isinstance(entry, Mapping):
        return False
    computed_at = _record_time(entry, fields, naive_warsaw=naive_warsaw)
    return computed_at is not None and computed_at > as_of


def _high_restaurant(entry: object, courier_id: Optional[str], as_of: datetime):
    if not isinstance(entry, Mapping):
        return None, "missing"
    if _mapping_after_as_of(
        entry, ("_computed_at", "computed_at", "written_at"), as_of,
        naive_warsaw=True,
    ):
        return None, "snapshot_version_after_as_of"
    if _cid(entry.get("courier_id")) != courier_id or courier_id is None:
        return None, "courier_mismatch"
    if entry.get("_source") != "gps_geofence":
        return None, "source_unsupported"
    try:
        n_in = int(entry.get("_n_in_geofence") or entry.get("n_in_geofence") or 0)
    except (TypeError, ValueError):
        n_in = 0
    min_dist = entry.get("_min_dist_m", entry.get("min_dist_m"))
    radius = entry.get("_radius_m", entry.get("radius_m"))
    inside = not (isinstance(min_dist, (int, float)) and isinstance(radius, (int, float))) \
        or min_dist <= radius
    if n_in < 2 or not inside:
        return None, "low_confidence"
    # Historyczna nazwa writera; to ostatni punkt WEWNATRZ geofence.
    value = _ts(entry.get("departed_restaurant"), naive_warsaw=True)
    if value is None:
        return None, "last_inside_missing"
    if value > as_of:
        return None, "after_as_of"
    arrival = _ts(entry.get("arrived_at_restaurant"), naive_warsaw=True)
    if arrival is not None and arrival <= as_of and value < arrival:
        return None, "restaurant_visit_order_invalid"
    return value, "gps_geofence"


def _high_delivery(app: object, server: object, courier_id: Optional[str], as_of: datetime):
    """Precedencja identyczna z eta_ground_truth._delivery_arrival_observable."""
    if _mapping_after_as_of(
        app, ("_computed_at", "computed_at", "written_at", "updated_at"), as_of,
        naive_warsaw=True,
    ):
        app = None
        app_reason = "app_snapshot_version_after_as_of"
    else:
        app_reason = "app_missing"
    if isinstance(app, Mapping) and app.get("gps_arrived_at") not in (None, ""):
        if _cid(app.get("courier_id")) != courier_id or courier_id is None:
            app_reason = "app_courier_mismatch"
        elif app.get("gps_arrival_source") != "app_geofence":
            app_reason = "app_source_unsupported"
        else:
            value = _ts(app.get("gps_arrived_at"))
            if value is not None and value <= as_of:
                return value, "app_geofence_arrival"
            app_reason = "app_time_invalid"
    elif app_reason != "app_snapshot_version_after_as_of":
        app_reason = "app_missing"
    if isinstance(server, Mapping) and server.get("physical_delivered_at"):
        if _cid(server.get("courier_id")) != courier_id or courier_id is None:
            return None, "server_courier_mismatch"
        if server.get("confidence") != "high":
            return None, "server_low_confidence"
        value = _ts(server.get("physical_delivered_at"))
        if value is not None and value <= as_of:
            return value, "server_geofence_arrival"
        return None, "server_time_invalid"
    return None, app_reason if app_reason != "app_missing" else "missing"


def _koord_time(hhmm, reference: Optional[datetime]) -> Optional[datetime]:
    if not hhmm or reference is None:
        return None
    try:
        hour, minute = str(hhmm).split(":")[:2]
        local = reference.astimezone(WARSAW)
        return local.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _summary(values: list[float]) -> dict:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    if not finite:
        return {"n": 0, "median_min": None, "mean_min": None}
    return {
        "n": len(finite),
        "median_min": round(statistics.median(finite), 3),
        "mean_min": round(statistics.mean(finite), 3),
    }


def measure_rows(
    trainer_rows: list[dict], gps: dict, dwell: dict, app: dict, as_of: datetime,
    *, min_delivery: float = 2.0, max_delivery: float = 60.0,
    max_pickup_slip_abs: float = 90.0,
) -> dict:
    counts = Counter()
    pickup_click_minus_proxy: list[float] = []
    delivery_click_minus_arrival: list[float] = []
    pair_click_minus_proxy: list[float] = []
    source_counts = Counter()

    for row in trainer_rows:
        counts["trainer_rows"] += 1
        oid = _oid(row.get("order_id"))
        courier_id = _cid(row.get("courier_id"))
        server = gps.get(oid)
        if isinstance(server, Mapping):
            counts["delivery_order_only_presence"] += 1
            if _cid(server.get("courier_id")) == courier_id and courier_id is not None:
                counts["delivery_order_and_courier_presence"] += 1
                if server.get("confidence") == "high":
                    counts["delivery_server_high_same_courier"] += 1
            else:
                counts["delivery_server_courier_mismatch_or_missing"] += 1

        last_inside, pickup_reason = _high_restaurant(dwell.get(oid), courier_id, as_of)
        arrival, delivery_reason = _high_delivery(app.get(oid), server, courier_id, as_of)
        counts[f"pickup_reason:{pickup_reason}"] += 1
        counts[f"delivery_reason:{delivery_reason}"] += 1
        if arrival is not None:
            source_counts[delivery_reason] += 1
            counts["delivery_high_same_courier"] += 1
        if last_inside is not None:
            counts["pickup_high_same_courier"] += 1

        click_pickup = _ts(row.get("ts_pickup"), naive_warsaw=True)
        click_delivery = _ts(row.get("ts_deliver"), naive_warsaw=True)
        koord = _koord_time(row.get("czas_kuriera"), click_pickup)
        if last_inside is not None and click_pickup is not None:
            pickup_click_minus_proxy.append((click_pickup - last_inside).total_seconds() / 60.0)
        if arrival is not None and click_delivery is not None:
            delivery_click_minus_arrival.append((click_delivery - arrival).total_seconds() / 60.0)

        if last_inside is not None and koord is not None:
            pickup_target = (last_inside - koord).total_seconds() / 60.0
            if abs(pickup_target) <= max_pickup_slip_abs:
                counts["pickup_target_eligible"] += 1

        if last_inside is not None and arrival is not None:
            duration = (arrival - last_inside).total_seconds() / 60.0
            if min_delivery <= duration <= max_delivery:
                counts["delivery_pair_target_eligible"] += 1
                if click_pickup is not None and click_delivery is not None:
                    legacy = (click_delivery - click_pickup).total_seconds() / 60.0
                    pair_click_minus_proxy.append(legacy - duration)
            else:
                counts["delivery_pair_invalid_window"] += 1

    denominator = counts["trainer_rows"]
    def coverage(name: str) -> dict:
        value = counts[name]
        return {"n": value, "pct_trainer": round(100.0 * value / denominator, 3) if denominator else None}

    return {
        "schema": SCHEMA,
        "status": "MEASURED",
        "as_of": as_of.isoformat(),
        "trainer": {"n": denominator},
        "delivery_presence_order_only": coverage("delivery_order_only_presence"),
        "delivery_presence_order_and_courier": coverage("delivery_order_and_courier_presence"),
        "delivery_server_high_same_courier": coverage("delivery_server_high_same_courier"),
        "pickup_proxy_high_same_courier": coverage("pickup_high_same_courier"),
        "pickup_target_eligible": coverage("pickup_target_eligible"),
        "delivery_arrival_high_same_courier": coverage("delivery_high_same_courier"),
        "delivery_proxy_pair_target_eligible": coverage("delivery_pair_target_eligible"),
        "rejects": {
            "delivery_server_courier_mismatch_or_missing": counts["delivery_server_courier_mismatch_or_missing"],
            "delivery_pair_invalid_window": counts["delivery_pair_invalid_window"],
        },
        "delivery_arrival_sources": dict(sorted(source_counts.items())),
        "same_population_deltas": {
            "pickup_click_minus_last_inside": _summary(pickup_click_minus_proxy),
            "delivery_click_minus_arrival": _summary(delivery_click_minus_arrival),
            "click_pair_duration_minus_proxy_pair_duration": _summary(pair_click_minus_proxy),
        },
        "model_effect": {
            "status": "NOT_MEASURED_BY_COVERAGE",
            "reason": "coverage/label_shift is not a ceiling; both model families, pace history and conformal offsets require full refit",
        },
    }


def _load_trainer_readonly(path: Path) -> list[dict]:
    # `immutable=1` zapobiega utworzeniu sidecarów -shm/-wal. Wejściem ma być
    # spójny snapshot wykonany przez SQLite backup API, nigdy żywa baza.
    uri_path = quote(str(path.resolve()), safe="/")
    connection = sqlite3.connect(f"file:{uri_path}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(
            "SELECT order_id,courier_id,day,ts_pickup,ts_deliver,actual_deliver_min,"
            "czas_kuriera,pickup_slip_koord_min,osrm_deliv_ff_min FROM eta_calib_features"
        )]
    finally:
        connection.close()


def _file_meta(path: Path) -> dict:
    data = path.read_bytes()
    stat = path.stat()
    return {"name": path.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "sha256": hashlib.sha256(data).hexdigest()}


def _is_readable_file(value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        path = Path(value)
        return path.is_file() and os.access(path, os.R_OK)
    except OSError:
        return False


def gps_audit(gps_path: Path, outcomes_path: Path, snapshot_at: Optional[str]) -> dict:
    gps_rows = _read_jsonl(gps_path)
    as_of = _ts(snapshot_at)
    gps = _latest_by_oid(
        gps_rows, GPS_TIME_FIELDS, as_of=as_of, naive_warsaw=True,
    )
    outcomes = _latest_by_oid(
        _read_jsonl(outcomes_path), OUTCOME_TIME_FIELDS,
        as_of=as_of, naive_warsaw=False,
    )

    def joined(confidence: Optional[str]) -> dict:
        selected = [row for row in gps.values()
                    if confidence is None or row.get("confidence") == confidence]
        any_outcome = [(row, outcomes.get(_oid(row.get("order_id")))) for row in selected]
        any_outcome = [(row, out) for row, out in any_outcome if isinstance(out, Mapping)]
        assigned = [(row, out) for row, out in any_outcome if out.get("action") in ASSIGNMENT_ACTIONS]
        match = mismatch = missing = 0
        for row, out in assigned:
            left, right = _cid(row.get("courier_id")), _cid(out.get("actual_cid"))
            if left is None or right is None:
                missing += 1
            elif left == right:
                match += 1
            else:
                mismatch += 1
        return {"gps_n": len(selected), "has_any_outcome": len(any_outcome),
                "has_assignment_action": len(assigned), "courier_match": match,
                "courier_mismatch": mismatch, "courier_missing_side": missing}

    high_delta = [float(row["delta_button_minus_physical_min"]) for row in gps.values()
                  if row.get("confidence") == "high"
                  and isinstance(row.get("delta_button_minus_physical_min"), (int, float))]
    return {
        "schema": SCHEMA,
        "status": "PARTIAL_GPS_AUDIT_ONLY",
        "snapshot_at": snapshot_at,
        "sources": {"gps": _file_meta(gps_path), "outcomes": _file_meta(outcomes_path)},
        "gps": {
            "records": len(gps_rows), "unique_orders": len(gps),
            "confidence": dict(sorted(Counter(str(row.get("confidence")) for row in gps.values()).items())),
            "missing_courier": sum(_cid(row.get("courier_id")) is None for row in gps.values()),
        },
        "courier_sanity_not_trainer_coverage": {
            "note": "outcomes are not the eta_calib trainer denominator",
            "any_confidence": joined(None), "high_confidence": joined("high"),
        },
        "click_minus_delivery_arrival_high_same_source_population": {
            **_summary(high_delta),
            "pct_click_late": round(100.0 * sum(value > 0 for value in high_delta) / len(high_delta), 3)
            if high_delta else None,
            "pct_abs_ge5": round(100.0 * sum(abs(value) >= 5 for value in high_delta) / len(high_delta), 3)
            if high_delta else None,
        },
        "corrected_trainer_coverage": {
            "status": "UNMEASURED", "n": None, "pct": None,
            "missing_inputs": ["eta_calib.db", "restaurant_dwell.json", "courier_ground_truth.json"],
        },
        "model_effect": {"status": "UNMEASURED_FULL_REFIT_REQUIRED"},
    }


def self_test() -> dict:
    as_of = datetime(2026, 7, 21, 12, tzinfo=UTC)
    rows = [
        {"order_id": "A", "courier_id": "7", "ts_pickup": "2026-07-21T10:05:00+00:00",
         "ts_deliver": "2026-07-21T10:30:00+00:00", "czas_kuriera": "12:00"},
        {"order_id": "B", "courier_id": "8", "ts_pickup": "2026-07-21T10:05:00+00:00",
         "ts_deliver": "2026-07-21T10:30:00+00:00", "czas_kuriera": "12:00"},
        {"order_id": "C", "courier_id": "9", "ts_pickup": "2026-07-21T10:05:00+00:00",
         "ts_deliver": "2026-07-21T10:30:00+00:00", "czas_kuriera": "12:00"},
    ]
    gps = {
        "A": {"order_id": "A", "courier_id": "7", "confidence": "high",
              "physical_delivered_at": "2026-07-21T10:28:00+00:00"},
        "B": {"order_id": "B", "courier_id": "WRONG", "confidence": "high",
              "physical_delivered_at": "2026-07-21T10:28:00+00:00"},
        "C": {"order_id": "C", "courier_id": "9", "confidence": "low",
              "physical_delivered_at": "2026-07-21T10:28:00+00:00"},
    }
    dwell = {
        "A": {"courier_id": "7", "_source": "gps_geofence", "_n_in_geofence": 2,
              "departed_restaurant": "2026-07-21T10:03:00+00:00"},
        "C": {"courier_id": "9", "_source": "gps_geofence", "_n_in_geofence": 2,
              "departed_restaurant": "2026-07-21T10:03:00+00:00"},
    }
    app = {
        "A": {"courier_id": "7", "gps_arrival_source": "app_geofence",
              "gps_arrived_at": "2026-07-21T10:27:00+00:00"},
    }
    result = measure_rows(rows, gps, dwell, app, as_of)
    assert result["delivery_presence_order_only"]["n"] == 3
    assert result["delivery_presence_order_and_courier"]["n"] == 2
    assert result["delivery_server_high_same_courier"]["n"] == 1
    assert result["pickup_proxy_high_same_courier"]["n"] == 2
    assert result["delivery_proxy_pair_target_eligible"]["n"] == 1
    assert result["delivery_arrival_sources"] == {"app_geofence_arrival": 1}

    # Rekord dostępny dopiero po snapshotcie nie może zasłonić wcześniejszego.
    selected = _latest_by_oid([
        {"order_id": "Z", "courier_id": "7", "confidence": "high",
         "physical_delivered_at": "2026-07-21T11:00:00+00:00"},
        {"order_id": "Z", "courier_id": "WRONG", "confidence": "high",
         "physical_delivered_at": "2026-07-21T13:00:00+00:00"},
    ], GPS_TIME_FIELDS, as_of=as_of, naive_warsaw=True)
    assert selected["Z"]["courier_id"] == "7"

    future_dwell = dict(dwell["A"], _computed_at="2026-07-21T13:00:00+00:00")
    future_result = measure_rows(rows[:1], gps, {"A": future_dwell}, {}, as_of)
    assert future_result["pickup_proxy_high_same_courier"]["n"] == 0
    return {"schema": SCHEMA, "self_test": "PASS"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--gps-audit-only", action="store_true")
    parser.add_argument("--trainer-db")
    parser.add_argument("--gps-delivery")
    parser.add_argument("--restaurant-dwell")
    parser.add_argument("--courier-ground-truth")
    parser.add_argument("--outcomes")
    parser.add_argument("--as-of", help="ISO UTC; wymagane w pelnym pomiarze")
    parser.add_argument("--snapshot-at", help="etykieta snapshotu dla audytu GPS")
    args = parser.parse_args(argv)
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.gps_audit_only:
        if not args.gps_delivery or not args.outcomes:
            parser.error("--gps-audit-only wymaga --gps-delivery i --outcomes")
        result = gps_audit(Path(args.gps_delivery), Path(args.outcomes), args.snapshot_at)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    required = {
        "trainer_db": args.trainer_db, "gps_delivery": args.gps_delivery,
        "restaurant_dwell": args.restaurant_dwell, "courier_ground_truth": args.courier_ground_truth,
    }
    missing = [name for name, value in required.items() if not _is_readable_file(value)]
    as_of = _ts(args.as_of)
    if missing or as_of is None:
        print(json.dumps({"schema": SCHEMA, "status": "HOLD_MISSING_INPUTS",
                          "missing": missing + ([] if as_of is not None else ["valid_as_of"])},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    trainer = _load_trainer_readonly(Path(args.trainer_db))
    gps = _latest_by_oid(
        _read_jsonl(Path(args.gps_delivery)), GPS_TIME_FIELDS,
        as_of=as_of, naive_warsaw=True,
    )
    dwell = _read_mapping(Path(args.restaurant_dwell))
    app = _read_mapping(Path(args.courier_ground_truth))
    result = measure_rows(trainer, gps, dwell, app, as_of)
    result["sources"] = {name: _file_meta(Path(value)) for name, value in required.items()}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
