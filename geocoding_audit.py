"""Geocoding audit trail (#18 tech debt).

Atomic JSONL appender dla każdego geocode call. Single source of truth
"jakie coords użyte w czasie decyzji" — enabler dla LGBM training reproducibility,
replay tooling, p50/p95 latency dystrybucja per source.

Schema (per line):
    ts_utc, entity_type, address, city, lat, lon, source, latency_ms, error

source ∈ {cache, google, osrm, none}
entity_type ∈ {address, restaurant}

Defense-in-depth: log fail NIGDY nie crashuje main flow (geocode behavior unchanged).
Flag ENABLE_GEOCODING_AUDIT_LOG (default True, env override) — kill switch.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

_log = logging.getLogger(__name__)

LOG_PATH = "/root/.openclaw/workspace/scripts/logs/geocoding_log.jsonl"


def _flag_enabled() -> bool:
    """Resolve flag at call-time (env override → flags.json → default True)."""
    env = os.environ.get("ENABLE_GEOCODING_AUDIT_LOG")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes")
    try:
        from dispatch_v2 import common as C
        return bool(C.flag("ENABLE_GEOCODING_AUDIT_LOG", True))
    except Exception:
        return True


def log_geocode(
    entity_type: str,
    address: str,
    city: Optional[str],
    lat: Optional[float],
    lon: Optional[float],
    source: str,
    latency_ms: float,
    error: Optional[str] = None,
    log_path: Optional[str] = None,
) -> None:
    """Append JSONL under the shared writer/logrotate namespace lock."""
    if not _flag_enabled():
        return
    record = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "entity_type": entity_type,
        "address": address,
        "city": city,
        "lat": lat,
        "lon": lon,
        "source": source,
        "latency_ms": round(latency_ms, 2),
    }
    if error is not None:
        record["error"] = str(error)[:500]
    path = log_path or LOG_PATH
    try:
        from dispatch_v2.core.jsonl_appender import append_jsonl
        append_jsonl(path, record)
    except Exception as e:
        _log.warning(f"geocoding_audit log fail (non-fatal): {e}")
