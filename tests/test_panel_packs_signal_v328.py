"""V3.28 P3 (B+C) — panel_packs reverse lookup signal + min latency gate (Adrian 2026-05-10).

B: gdy panel widzi nick→[oids] ALE state.bag pusty → bonus_state_panel_mismatch = -50/oid
C: gdy max_packs_age >60s AND >=2 stale_signal candidates → KOORD escalate

Fixture base: 472242 Baanko 17:41 — Mateusz O bag=0 w state mimo 7 queued w panelu.
"""
from __future__ import annotations
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from dispatch_v2 import courier_resolver
from dispatch_v2.courier_resolver import (
    CourierState,
    _load_panel_packs_cache,
    PANEL_PACKS_CACHE_PATH,
    PANEL_PACKS_CACHE_MAX_AGE_S,
)


def _write_packs_cache(packs: dict, ts_offset_s: float = 0.0):
    """Write fresh cache (now - ts_offset_s)."""
    ts = (datetime.now(timezone.utc) - timedelta(seconds=ts_offset_s)).isoformat()
    data = {"ts": ts, "packs": packs, "tick": 1, "orders_in_panel": 10}
    fd, tmp = tempfile.mkstemp(prefix="test_panel_packs.", suffix=".tmp",
                                dir=os.path.dirname(PANEL_PACKS_CACHE_PATH))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, PANEL_PACKS_CACHE_PATH)


def test_load_cache_fresh():
    """Cache zapisany teraz → age_s ~ 0, packs return dict."""
    _write_packs_cache({"Mateusz O": ["472001", "472002"]}, ts_offset_s=0)
    ts, packs, age = _load_panel_packs_cache()
    assert ts is not None
    assert packs == {"Mateusz O": ["472001", "472002"]}
    assert age is not None and age < 5


def test_load_cache_stale():
    """Cache z 200s ago — funkcja zwraca dane ale age > 120s sentinel."""
    _write_packs_cache({"Bartek O": ["472003"]}, ts_offset_s=200)
    ts, packs, age = _load_panel_packs_cache()
    assert ts is not None
    assert age > 120
    # Caller is responsible for filtering by age (PANEL_PACKS_CACHE_MAX_AGE_S=120)


def test_cache_missing_returns_none():
    """Brak cache file → (None, {}, None)."""
    if os.path.exists(PANEL_PACKS_CACHE_PATH):
        os.unlink(PANEL_PACKS_CACHE_PATH)
    ts, packs, age = _load_panel_packs_cache()
    assert ts is None
    assert packs == {}
    assert age is None


def test_max_age_constant():
    """PANEL_PACKS_CACHE_MAX_AGE_S = 120s default — 2 panel_watcher ticks worth."""
    assert PANEL_PACKS_CACHE_MAX_AGE_S == 120.0


def test_courier_state_has_panel_packs_fields():
    """CourierState z nowymi polami panel_packs_oids_signal + age."""
    cs = CourierState(courier_id="413")
    assert hasattr(cs, "panel_packs_oids_signal")
    assert cs.panel_packs_oids_signal == []
    assert hasattr(cs, "panel_packs_cache_age_s")
    assert cs.panel_packs_cache_age_s is None


if __name__ == "__main__":
    import traceback
    tests = [
        test_load_cache_fresh,
        test_load_cache_stale,
        test_cache_missing_returns_none,
        test_max_age_constant,
        test_courier_state_has_panel_packs_fields,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            print(f"  FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    import sys
    sys.exit(0 if failed == 0 else 1)
