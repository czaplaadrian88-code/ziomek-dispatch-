"""Reconciliation health snapshot — exposable via HTTP /health/reconcile.

Zwraca dict z aggregate counts + status classification.

Integracja:
  Existing parser_health_endpoint.py runs HTTPServer on port 8888.
  Caller (extension parser_health_endpoint OR osobny module) wywołuje
  get_reconciliation_health() → JSON response.

Z3: pure function, no side effects, no caching (snapshot taken na call).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from dispatch_v2.reconciliation import reconcile_log


def get_reconciliation_health(
    log_path: Optional[Path] = None,
    hours: int = 24,
) -> Dict[str, Any]:
    """Aggregate reconciliation stats za ostatnie N godzin.

    Returns:
      {
        "last_run_ts": "ISO-8601" | null,
        "discrepancies_24h": {
            "phantoms": int,
            "ghosts": int,
            "auto_resyncs": int,
            "manual_alerts": int,
            "hard_cap_hits": int,
        },
        "status": "ok" | "degraded" | "critical",
        "endpoint_version": "1"
      }
    """
    base = reconcile_log.query_recent_summary(log_path=log_path, hours=hours)
    base["endpoint_version"] = "1"
    return base
