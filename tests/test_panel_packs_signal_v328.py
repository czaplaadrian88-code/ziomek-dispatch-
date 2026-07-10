"""V3.28 P3 (B+C) — panel_packs reverse lookup signal + min latency gate (Adrian 2026-05-10).

B: gdy panel widzi nick→[oids] ALE state.bag pusty → bonus_state_panel_mismatch = -50/oid
C: gdy max_packs_age >60s AND >=2 stale_signal candidates → KOORD escalate

Fixture base: 472242 Baanko 17:41 — Mateusz O bag=0 w state mimo 7 queued w panelu.

Hermetyzacja (Z-P2-07, 2026-07-10): cache pisany/czytany WYLACZNIE w tmp_path — zero
I/O do produkcyjnego dispatch_state. Konsument `courier_resolver._load_panel_packs_cache`
czyta STALA MODULU `PANEL_PACKS_CACHE_PATH` late-bound (BEZ default-arg sciezki — C17-safe),
wiec `monkeypatch.setattr(courier_resolver, "PANEL_PACKS_CACHE_PATH", <tmp>)` retarget-uje
dokladnie to, co funkcja faktycznie konsumuje.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta

import pytest

from dispatch_v2 import courier_resolver
from dispatch_v2.courier_resolver import (
    CourierState,
    _load_panel_packs_cache,
    PANEL_PACKS_CACHE_MAX_AGE_S,
)


def _write_packs_cache(cache_path, packs: dict, ts_offset_s: float = 0.0):
    """Zapisz cache do PODANEJ sciezki (tmp). ts = now - ts_offset_s."""
    ts = (datetime.now(timezone.utc) - timedelta(seconds=ts_offset_s)).isoformat()
    data = {"ts": ts, "packs": packs, "tick": 1, "orders_in_panel": 10}
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def test_load_cache_fresh(tmp_path, monkeypatch):
    """Cache zapisany teraz → age_s ~ 0, packs return dict."""
    cache = tmp_path / "panel_packs_cache.json"
    monkeypatch.setattr(courier_resolver, "PANEL_PACKS_CACHE_PATH", str(cache))
    _write_packs_cache(cache, {"Mateusz O": ["472001", "472002"]}, ts_offset_s=0)
    ts, packs, age = _load_panel_packs_cache()
    assert ts is not None
    assert packs == {"Mateusz O": ["472001", "472002"]}
    assert age is not None and age < 5


def test_load_cache_stale(tmp_path, monkeypatch):
    """Cache z 200s ago — funkcja zwraca dane ale age > 120s sentinel."""
    cache = tmp_path / "panel_packs_cache.json"
    monkeypatch.setattr(courier_resolver, "PANEL_PACKS_CACHE_PATH", str(cache))
    _write_packs_cache(cache, {"Bartek O": ["472003"]}, ts_offset_s=200)
    ts, packs, age = _load_panel_packs_cache()
    assert ts is not None
    assert age > 120
    # Caller is responsible for filtering by age (PANEL_PACKS_CACHE_MAX_AGE_S=120)


def test_cache_missing_returns_none(tmp_path, monkeypatch):
    """Brak cache file → (None, {}, None). Sciezka w tmp, ktora NIE istnieje."""
    cache = tmp_path / "does_not_exist_panel_packs_cache.json"
    monkeypatch.setattr(courier_resolver, "PANEL_PACKS_CACHE_PATH", str(cache))
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
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
