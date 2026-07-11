#!/usr/bin/env python3
"""Read-only OSRM upstream health report (Z-P2-06).

The report performs direct, strict HTTP probes through
``osrm_client.health_check``.  Direct upstream truth is authoritative.  The
cache/circuit snapshot is explicitly process-local to this fresh reporter PID;
it does not inspect the in-memory state of dispatch-shadow or panel.  Their
actual counters are emitted by the hourly ``OSRM telemetry`` record and the
per-decision shadow stage timing.

The command never uses or mutates route/table caches, fallback values,
circuit-breaker counters, flags or runtime state files.  It increments only
the fresh reporter process's own probe-observation counters.

Examples::

    python -m dispatch_v2.tools.osrm_health_report --json
    python -m dispatch_v2.tools.osrm_health_report --timeout 0.5

Exit status: 0 only for fully healthy upstream+serving state, 1 for degraded.
No timer/service is installed by this module; deployment remains an ACK gate.
"""
from __future__ import annotations

import argparse
import json

from dispatch_v2 import osrm_client as oc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Direct authoritative OSRM upstream probe. Cache/circuit fields "
            "are local to this reporter PID, not dispatch-shadow."
        ))
    parser.add_argument("--timeout", type=float, default=1.0, help="timeout per endpoint [s]")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def _text_report(health: dict) -> str:
    probe = health.get("probe") or {}
    endpoints = probe.get("endpoints") or {}
    endpoint_text = ", ".join(
        f"{name}={'OK' if row.get('ok') else 'FAIL'}"
        f"/{row.get('latency_ms')}ms"
        + (f"/{row.get('error_kind')}" if row.get("error_kind") else "")
        for name, row in endpoints.items()
    )
    circuit = health.get("circuit") or {}
    return (
        f"OSRM {str(health.get('status', 'unknown')).upper()} "
        f"direct_upstream_ok={health.get('upstream_ok')} | {endpoint_text} | "
        f"state_scope={health.get('state_scope')} "
        f"pid={health.get('pid')} role={health.get('process_role')} "
        f"local_serving_degraded={health.get('serving_degraded')} "
        f"local_circuit_open={circuit.get('open')}"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    health = oc.health_check(timeout_s=args.timeout)
    if args.json:
        print(json.dumps(health, ensure_ascii=False, sort_keys=True))
    else:
        print(_text_report(health))
    return 0 if health.get("status") == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
