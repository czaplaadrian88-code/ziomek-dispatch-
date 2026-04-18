"""F2.2 C7: Pending queue + demand context providers (SKELETON).

Thin providers for future wave_scoring + commitment_emitter wire-up:
- get_pending_queue(): orders in status='planned' (state_machine.get_by_status)
- compute_demand_context(): hour, dayofweek, regime (PEAK lookup), density stub

Both gated by ENABLE_PENDING_QUEUE_VIEW flag (default False → minimal defaults).

When flag enabled:
- get_pending_queue reads state_machine live
- compute_demand_context enriches with PEAK regime from sekcja 3.5 cells

Per F2.2_SECTION_4_ARCHITECTURE_SPEC sekcja 4.3 + 5.C7.
"""
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from dispatch_v2.common import ENABLE_PENDING_QUEUE_VIEW

WARSAW = ZoneInfo("Europe/Warsaw")

_PEAK_REGIMES_CSV = Path(
    "/root/.openclaw/workspace/docs/wave_audit_outputs/2026-04-18/wave_audit_peak_regimes_2026-04-18.csv"
)

# Module-level cached PEAK cells (lazy load)
_PEAK_CELLS: Optional[set] = None


def _load_peak_cells() -> set:
    global _PEAK_CELLS
    if _PEAK_CELLS is not None:
        return _PEAK_CELLS
    cells: set = set()
    try:
        if _PEAK_REGIMES_CSV.exists():
            with open(_PEAK_REGIMES_CSV) as f:
                for r in csv.DictReader(f):
                    if r.get("regime") == "PEAK":
                        cells.add((int(r["hour"]), int(r["dayofweek"])))
    except Exception:
        pass
    _PEAK_CELLS = cells
    return cells


def get_pending_queue() -> List[Dict[str, Any]]:
    """Returns list of orders in status='planned'.

    Returns empty list if ENABLE_PENDING_QUEUE_VIEW=False (defensive default).
    When True, reads state_machine.get_by_status("planned").
    """
    if not ENABLE_PENDING_QUEUE_VIEW:
        return []
    try:
        from dispatch_v2 import state_machine
        return state_machine.get_by_status("planned")
    except Exception:
        return []


def compute_demand_context(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Returns demand context dict with hour, dayofweek, regime.

    Minimal output always populated (hour, dayofweek, regime='NORMAL', n_orders_last_15min=0).
    When ENABLE_PENDING_QUEUE_VIEW=True → regime enriched from PEAK cells lookup.
    n_orders_last_15min density computation deferred (future C7 iteration).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    # Normalize to Warsaw time for hour/dayofweek calc
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    warsaw_now = now.astimezone(WARSAW)
    hour = warsaw_now.hour
    dayofweek = warsaw_now.weekday()  # Mon=0, Sun=6

    context: Dict[str, Any] = {
        "hour": hour,
        "dayofweek": dayofweek,
        "regime": "NORMAL",
        "n_orders_last_15min": 0,
        "generated_at": warsaw_now.isoformat(),
    }

    if not ENABLE_PENDING_QUEUE_VIEW:
        return context

    # Enrich regime from PEAK cells (sekcja 3.5 empirical lookup)
    peak_cells = _load_peak_cells()
    if (hour, dayofweek) in peak_cells:
        context["regime"] = "PEAK"

    # TODO(C7 full iteration): compute n_orders_last_15min from event_bus recent events
    # For skeleton stays 0.

    return context
