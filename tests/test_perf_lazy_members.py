"""FALA perf-lazy (finding E audytu 2.0) — behawioralne + mutation testy.

ENABLE_PERF_LAZY_MEMBERS zmienia KIEDY liczymy (cache load_flags stat + cache
odczytu planów), NIGDY TREŚĆ decyzji. Testy pilnują:
  1. flags fast-path: te same wartości ON↔OFF; hot-reload pod-chwytany po TTL.
  2. plans read-cache: parytet ON↔OFF; cache BUSTUJE na zapis (mtime); deepcopy
     izoluje callera od współdzielonego obiektu cache.
Mutation (C13): każdy strażnik ma probe „zepsuj mechanizm → test PADA".
"""
import importlib
import json
import time

import pytest


# ─────────────────────────── flags fast-path ───────────────────────────

def _fresh_common(tmp_path, monkeypatch, perf_on):
    """Świeży moduł common z tmp flags.json i podanym stanem perf-lazy."""
    import dispatch_v2.common as C
    fp = tmp_path / "flags.json"
    fp.write_text(json.dumps({"ENABLE_PERF_LAZY_MEMBERS": perf_on,
                              "ENABLE_TESTKEY_A": True, "ENABLE_TESTKEY_B": False}))
    monkeypatch.setattr(C, "FLAGS_PATH", fp)
    monkeypatch.setattr(C, "_flags_cache", None)
    monkeypatch.setattr(C, "_flags_mtime", 0)
    monkeypatch.setattr(C, "_flags_last_stat_mono", 0.0)
    monkeypatch.setattr(C, "_perf_lazy_members", False)
    monkeypatch.setattr(C, "_PERF_FLAGS_STAT_TTL_S", 0.2)
    return C, fp


def test_flags_values_parity_on_off(tmp_path, monkeypatch):
    """flag()/decision_flag() zwracają TE SAME wartości ON i OFF."""
    for perf in (False, True):
        C, _ = _fresh_common(tmp_path, monkeypatch, perf)
        assert C.flag("ENABLE_TESTKEY_A") is True
        assert C.flag("ENABLE_TESTKEY_B") is False
        assert C.flag("ENABLE_ABSENT_KEY", default=False) is False
        assert C._perf_lazy_members is perf  # aktywacja fast-path po 1. reloadzie


def test_flags_hot_reload_after_ttl(tmp_path, monkeypatch):
    """ON: zmiana flags.json pod-chwytana po upływie TTL (staleness ograniczona)."""
    import os
    C, fp = _fresh_common(tmp_path, monkeypatch, True)
    assert C.flag("ENABLE_TESTKEY_B") is False
    fp.write_text(json.dumps({"ENABLE_PERF_LAZY_MEMBERS": True, "ENABLE_TESTKEY_B": True}))
    # Wymuś jednoznacznie nowszy mtime (tmpfs pytest bywa 1s-granularny → dwa
    # szybkie zapisy kolidują na st_mtime; produkcja zmienia flags.json rzadko).
    os.utime(fp, (time.time() + 5, time.time() + 5))
    time.sleep(0.25)  # > TTL(0.2)
    assert C.flag("ENABLE_TESTKEY_B") is True


def test_flags_fastpath_skips_restat_within_ttl(tmp_path, monkeypatch):
    """MUTATION-guard: w oknie TTL fast-path NIE stat'uje. Liczymy staty; ON musi
    mieć DRAMATYCZNIE mniej stat/wywołań niż OFF. Zepsucie fast-path (stat co raz)
    → licznik ON≈OFF → test PADA."""
    counts = {"OFF": 0, "ON": 0}
    for label, perf in (("OFF", False), ("ON", True)):
        C, fp = _fresh_common(tmp_path, monkeypatch, perf)
        orig_stat = type(fp).stat
        def _counting_stat(self, *a, **k):
            if str(self) == str(fp):
                counts[label] += 1
            return orig_stat(self, *a, **k)
        monkeypatch.setattr(type(fp), "stat", _counting_stat)
        for _ in range(200):
            C.flag("ENABLE_TESTKEY_A")
        monkeypatch.undo()  # przywróć stat oraz reszta setattrów następnej iteracji
    assert counts["OFF"] >= 190, counts          # OFF: ~1 stat / wywołanie
    assert counts["ON"] <= 20, counts            # ON: garść stat (TTL)
    assert counts["ON"] < counts["OFF"] / 5, counts


# ─────────────────────────── plans read-cache ───────────────────────────

@pytest.fixture
def pm_tmp(tmp_path, monkeypatch):
    import dispatch_v2.plan_manager as PM
    plans = tmp_path / "courier_plans.json"
    lock = tmp_path / "courier_plans.lock"
    monkeypatch.setattr(PM, "PLANS_FILE", plans)
    monkeypatch.setattr(PM, "LOCK_FILE", lock)
    monkeypatch.setattr(PM, "_perf_plans_cache", {"key": None, "data": None})
    return PM, plans


def _write_plans(path, data):
    path.write_text(json.dumps(data))


def test_plans_content_parity_on_off(pm_tmp, monkeypatch):
    PM, plans = pm_tmp
    body = {"c1": {"stops": [{"type": "dropoff", "order_id": "o1"}], "invalidated_at": None}}
    _write_plans(plans, body)
    monkeypatch.setattr(PM, "_perf_lazy_on", lambda: False)
    off = PM.load_plans()
    monkeypatch.setattr(PM, "_perf_plans_cache", {"key": None, "data": None})
    monkeypatch.setattr(PM, "_perf_lazy_on", lambda: True)
    on = PM.load_plans()
    assert off == on == body


def test_plans_cache_busts_on_write(pm_tmp, monkeypatch):
    """MUTATION-guard: cache po (mtime,size) MUSI odświeżyć po zapisie. Zepsucie
    (stały klucz / ignore mtime) → stale read → test PADA."""
    PM, plans = pm_tmp
    monkeypatch.setattr(PM, "_perf_lazy_on", lambda: True)
    _write_plans(plans, {"c1": {"stops": [], "invalidated_at": None, "v": 1}})
    assert PM.load_plans()["c1"]["v"] == 1
    time.sleep(0.01)
    _write_plans(plans, {"c1": {"stops": [], "invalidated_at": None, "v": 2}})
    assert PM.load_plans()["c1"]["v"] == 2  # PADA gdyby cache nie bustował


def test_plans_deepcopy_isolates_cache(pm_tmp, monkeypatch):
    """MUTATION-guard: mutacja zwróconego planu NIE korumpuje współdzielonego
    cache. Usunięcie deepcopy → mutacja przecieka → następny odczyt widzi śmieć →
    test PADA."""
    PM, plans = pm_tmp
    monkeypatch.setattr(PM, "_perf_lazy_on", lambda: True)
    _write_plans(plans, {"c1": {"stops": [{"type": "dropoff", "order_id": "o1"}],
                                "invalidated_at": None}})
    p1 = PM.load_plan("c1")
    p1["stops"].append({"type": "POISON"})       # caller mutuje swoją kopię
    p1["invalidated_at"] = "HACKED"
    p2 = PM.load_plan("c1")                       # bez zapisu → ten sam mtime → cache
    assert p2 is not None
    assert p2.get("invalidated_at") is None
    assert all(s.get("type") != "POISON" for s in p2["stops"])


def test_load_plan_returns_independent_object(pm_tmp, monkeypatch):
    PM, plans = pm_tmp
    monkeypatch.setattr(PM, "_perf_lazy_on", lambda: True)
    _write_plans(plans, {"c1": {"stops": [], "invalidated_at": None}})
    a = PM.load_plan("c1")
    b = PM.load_plan("c1")
    assert a == b and a is not b
