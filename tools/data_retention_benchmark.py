#!/usr/bin/env python3
"""Synthetic-only size/latency Pareto benchmark for private-ledger v1."""
from __future__ import annotations

import gzip
import json
import os
import statistics
import tempfile
import time
from pathlib import Path

from dispatch_v2.privacy.private_ledger import SecureJsonlWriter, redact_record


def _synthetic_record(index: int) -> dict:
    alternatives = [
        {
            "courier_id": f"synthetic-courier-{candidate}",
            "name": f"SYNTHETIC-PERSON-{candidate}",
            "score": candidate * 1.25,
            "delivery_coords": [1.0 + candidate / 1000, 2.0 + candidate / 1000],
            "metrics": {"latency_ms": candidate + 0.5, "reason": "SYNTHETIC-FREE-TEXT"},
        }
        for candidate in range(24)
    ]
    return {
        "schema": "synthetic-decision.v1",
        "ts": f"2026-01-01T00:00:{index % 60:02d}+00:00",
        "order_id": f"synthetic-order-{index}",
        "delivery_address": "SYNTHETIC-ADDRESS",
        "pickup_coords": [1.25, 2.5],
        "verdict": "PROPOSE",
        "best": alternatives[0],
        "alternatives": alternatives[1:],
        "pool_feasible_count": 24,
    }


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))
    return ordered[index]


def run(samples: int = 200) -> dict:
    if samples < 10:
        raise ValueError("samples must be >= 10")
    key = os.urandom(32)  # ephemeral synthetic benchmark material, never emitted
    legacy_sizes: list[int] = []
    private_sizes: list[int] = []
    redact_ms: list[float] = []
    append_ms: list[float] = []
    legacy_blob = bytearray()
    private_blob = bytearray()
    with tempfile.TemporaryDirectory(prefix="data0_benchmark_") as tmp:
        target = Path(tmp) / "private" / "benchmark.jsonl"
        writer = SecureJsonlWriter(target)
        for index in range(samples):
            record = _synthetic_record(index)
            legacy_line = (json.dumps(record, ensure_ascii=False) + "\n").encode()
            started = time.perf_counter_ns()
            envelope = redact_record(record, key=key, scope="synthetic-benchmark",
                                     ledger="shadow_decisions")
            redact_ms.append((time.perf_counter_ns() - started) / 1_000_000)
            private_line = (json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
            started = time.perf_counter_ns()
            writer.append(envelope)
            append_ms.append((time.perf_counter_ns() - started) / 1_000_000)
            legacy_sizes.append(len(legacy_line))
            private_sizes.append(len(private_line))
            legacy_blob.extend(legacy_line)
            private_blob.extend(private_line)
    legacy_total = sum(legacy_sizes)
    private_total = sum(private_sizes)
    legacy_gz = len(gzip.compress(bytes(legacy_blob), compresslevel=6))
    private_gz = len(gzip.compress(bytes(private_blob), compresslevel=6))
    return {
        "schema": "private_ledger_benchmark.v1",
        "synthetic_only": True,
        "samples": samples,
        "size": {
            "legacy_bytes": legacy_total,
            "private_bytes": private_total,
            "private_vs_legacy_ratio": round(private_total / legacy_total, 6),
            "legacy_gzip_bytes": legacy_gz,
            "private_gzip_bytes": private_gz,
            "private_gzip_vs_legacy_gzip_ratio": round(private_gz / legacy_gz, 6),
        },
        "latency_ms": {
            "redact_p50": round(statistics.median(redact_ms), 6),
            "redact_p95": round(_percentile(redact_ms, 0.95), 6),
            "secure_append_p50": round(statistics.median(append_ms), 6),
            "secure_append_p95": round(_percentile(append_ms, 0.95), 6),
        },
    }


def main() -> int:
    print(json.dumps(run(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
