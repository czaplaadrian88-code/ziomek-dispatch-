#!/usr/bin/env python3
"""Hermetyczny benchmark narzutu `c7_normal_path.v1`.

Uruchom z package-rootem worktree, np.:
  ZIOMEK_SCRIPTS_ROOT=/tmp/pkgroot PYTHONPATH=/tmp/pkgroot \
    python3 dispatch_v2/tools/benchmark_c7_normal_path.py

Nie czyta ani nie zapisuje żywego stanu. Oczekuje izolowanego flags.json pod
ZIOMEK_SCRIPTS_ROOT (ten sam harness co pytest w worktree).
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import json
import statistics
from types import SimpleNamespace

from dispatch_v2 import c7_normal_path as c7
from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as dp
from dispatch_v2.core.selection import SelectionContext, select_and_emit
from dispatch_v2.route_simulator_v2 import RoutePlanV2


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
OID = "C7-BENCH"


def _candidate(index: int):
    delivered = NOW + timedelta(minutes=24 + index % 9)
    penalty = C.post_shift_overrun_penalty((index * 3) % 42)
    plan = RoutePlanV2(
        sequence=[OID],
        predicted_delivered_at={OID: delivered},
        pickup_at={OID: NOW + timedelta(minutes=8 + index % 5)},
        total_duration_min=30.0 + index % 9,
        strategy="synthetic",
        sla_violations=0,
        osrm_fallback_used=False,
        per_order_delivery_times={OID: 24.0 + index % 9},
    )
    return dp.Candidate(
        courier_id=str(1000 + index),
        name=None,
        score=120.0 - index * 2.7,
        feasibility_verdict="MAYBE",
        feasibility_reason="ok",
        plan=plan,
        metrics={
            "bundle_level3_dev": float(index % 4),
            "pos_source": "gps",
            "post_shift_overrun_min": float((index * 3) % 42),
            "post_shift_overrun_penalty": penalty,
            "post_shift_overrun_score_delta": 0.0,
            "r6_per_order_violations": [],
            "r6_max_bag_time_min": 24.0 + index % 9,
            "objm_r6_breach_max_min": 0.0,
            "late_pickup_committed_max": 0.0,
            "new_pickup_late_min": float(index % 4),
            "czas_kuriera_warsaw": None,
            "loadgov_load_ewma": round((index % 8) / 10.0, 2),
            "r6_bag_size": index % 4,
            "bag_size_before": index % 4,
            "km_to_pickup": 0.5 + index / 10.0,
            "travel_min": 4.0 + index % 6,
        },
    )


def _ctx():
    return SelectionContext(
        now=NOW,
        order_event={"order_id": OID},
        order_id=OID,
        restaurant=None,
        delivery_address=None,
        pickup_coords=None,
        delivery_coords=None,
        pickup_ready_at=NOW + timedelta(minutes=5),
        new_order=SimpleNamespace(order_id=OID),
        fleet_snapshot={},
        v328_fail_causes={},
    )


def _one(pool_size: int) -> float:
    source = [_candidate(i) for i in range(pool_size)]
    prepared = c7.prepare(source)
    with C.post_shift_overrun_override(False):
        actual = select_and_emit(_ctx(), copy.deepcopy(source))
    payload = c7.measure_prepared(
        _ctx(),
        prepared,
        actual,
        code_sha_fn=lambda: "benchmark",
    )
    if payload["status"] != "OK":
        raise RuntimeError(
            f"benchmark parity failed: {payload.get('mismatch_fields')}")
    return float(payload["overhead_ms"])


def _percentile(values, fraction):
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(
        (len(ordered) - 1) * fraction))))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=80)
    parser.add_argument("--warmup", type=int, default=8)
    parser.add_argument("--sizes", type=int, nargs="+", default=[8, 16, 24])
    args = parser.parse_args()
    report = {
        "schema": "c7_normal_path.benchmark.v1",
        "iterations": args.iterations,
        "unit": "ms_per_decision",
        "pools": {},
    }
    for size in args.sizes:
        for _ in range(args.warmup):
            _one(size)
        values = [_one(size) for _ in range(args.iterations)]
        report["pools"][str(size)] = {
            "median_ms": round(statistics.median(values), 3),
            "p95_ms": round(_percentile(values, 0.95), 3),
            "max_ms": round(max(values), 3),
        }
    report["sampling_required"] = (
        report["pools"].get("16", {}).get("p95_ms", 0.0) > 5.0)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
