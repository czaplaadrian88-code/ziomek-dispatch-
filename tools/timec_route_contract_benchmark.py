"""Powtarzalny mikrobenchmark hot-path TIME-C (bez I/O i bez produkcji)."""
from __future__ import annotations

import argparse
import json
import time

from dispatch_v2 import live_eta, route_order


def _percentile(values: list[int], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction)))
    return ordered[index] / 1_000.0


def _measure(fn, iterations: int) -> dict[str, float]:
    samples: list[int] = []
    for _ in range(1_000):
        fn()
    for _ in range(iterations):
        started = time.perf_counter_ns()
        fn()
        samples.append(time.perf_counter_ns() - started)
    return {
        "p50_us": round(_percentile(samples, 0.50), 3),
        "p95_us": round(_percentile(samples, 0.95), 3),
    }


def run(iterations: int = 30_000) -> dict:
    stops = [
        {
            "kind": "pickup" if index % 2 == 0 else "dropoff",
            "order_ids": [str(index)],
            "stop_id": (
                f"pickup:{index}" if index % 2 == 0 else f"dropoff:{index}"
            ),
        }
        for index in range(12)
    ]
    snapshot = {
        "plan_version": 41,
        "sequence_hash": route_order.route_sequence_hash(stops),
        "orders": {"1": {"pickup_at": None, "delivery_at": "2026-08-02T08:10:00Z"}},
    }

    def legacy():
        return live_eta.eta_for(snapshot, "1", "dropoff")

    def contract_off():
        accepted, _meta = live_eta.bind_snapshot_to_route(
            snapshot, stops, current_plan_version=41, enforce=False
        )
        return live_eta.eta_for(accepted, "1", "dropoff")

    def contract_on():
        accepted, _meta = live_eta.bind_snapshot_to_route(
            snapshot, stops, current_plan_version=41, enforce=True
        )
        return live_eta.eta_for(accepted, "1", "dropoff")

    return {
        "schema": "timec.route_contract_benchmark.v1",
        "iterations": iterations,
        "physical_stops": len(stops),
        "legacy_before": _measure(legacy, iterations),
        "contract_off_after": _measure(contract_off, iterations),
        "contract_on_after": _measure(contract_on, iterations),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=30_000)
    args = parser.parse_args(argv)
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    print(json.dumps(run(args.iterations), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
