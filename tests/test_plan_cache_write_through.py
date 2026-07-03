"""perf-lazy plan mtime-cache: write-through + st_ino w kluczu (fix 03.07).

Root cause flake `test_v319c_sub_a::script_run` (4/30 FAIL przy PERF_LAZY=ON,
0/30 OFF): założenie „os.replace bumpuje mtime → cache sam się unieważnia"
fałszywe — zapisy w tym samym ticku zegara jądra dają identyczny st_mtime_ns
(a size bywa równy przy ciałach różniących się tylko treścią oidów) → czytelnik
dostawał stan sprzed zapisu. Fix: (1) `_write_raw` czyści cache (write-through,
in-process zawsze świeże), (2) klucz = (mtime_ns, size, st_ino) — atomic write
przez os.replace = nowy inode (cross-proces), (3) ENABLE_PERF_LAZY_MEMBERS w
TEST_ISOLATED_INFRA_FLAGS (testy nie dziedziczą żywego flipu).
"""
import copy
import tempfile
from pathlib import Path

import pytest

from dispatch_v2 import common as cm
from dispatch_v2 import plan_manager as pm

_PROD_STATE = "/root/.openclaw/workspace/dispatch_state"


@pytest.fixture()
def tmp_plans(tmp_path, monkeypatch):
    plans = tmp_path / "courier_plans.json"
    lock = tmp_path / "courier_plans.lock"
    assert not str(plans).startswith(_PROD_STATE)  # anty-PROD (mina 02-03.07)
    monkeypatch.setattr(pm, "PLANS_FILE", plans)
    monkeypatch.setattr(pm, "LOCK_FILE", lock)
    # czysty cache między testami
    monkeypatch.setattr(pm, "_perf_plans_cache", {"key": None, "data": None})
    return plans


def _body(oid_new, oid_bag):
    return {
        "start_pos": {"lat": 53.13, "lng": 23.15, "source": "gps",
                      "source_ts": "2026-07-03T05:00:00+00:00"},
        "start_ts": "2026-07-03T05:00:00+00:00",
        "stops": [
            {"order_id": oid_new, "type": "pickup",
             "coords": {"lat": 53.10, "lng": 23.10}, "dwell_min": 2.0,
             "status_at_plan_time": "assigned"},
            {"order_id": oid_bag, "type": "dropoff",
             "coords": {"lat": 53.18, "lng": 23.22}, "dwell_min": 1.0,
             "status_at_plan_time": "picked_up"},
            {"order_id": oid_new, "type": "dropoff",
             "coords": {"lat": 53.15, "lng": 23.20}, "dwell_min": 1.0,
             "status_at_plan_time": "assigned"},
        ],
        "optimization_method": "bruteforce",
    }


def test_write_raw_clears_cache(tmp_plans, monkeypatch):
    """Write-through: KAŻDY zapis czyści in-process cache (deterministyczny
    strażnik chokepointu — łapie mutację usuwającą clear)."""
    monkeypatch.setattr(cm, "ENABLE_PERF_LAZY_MEMBERS", True)
    pm.save_plan("1", _body("N1", "B1"))
    assert pm.load_plan("1") is not None  # grzeje cache
    assert pm._perf_plans_cache["key"] is not None
    with pm._locked(exclusive=True):
        plans = pm._read_raw()
        pm._write_raw(plans)
    assert pm._perf_plans_cache["key"] is None
    assert pm._perf_plans_cache["data"] is None


def test_rapid_same_size_writes_always_fresh(tmp_plans, monkeypatch):
    """Repro klasy flake'a: szybkie zapisy o TEJ SAMEJ długości (oidy N01..N99)
    w pętli — każdy odczyt musi widzieć ostatni zapis. Pre-fix: przy zapisach
    w tym samym ticku zegara cache serwował poprzedni stan (≈pewny FAIL przy
    setkach iteracji); post-fix deterministycznie świeże."""
    monkeypatch.setattr(cm, "ENABLE_PERF_LAZY_MEMBERS", True)
    for i in range(300):
        oid = f"N{i % 90 + 10}"  # stała długość → stały size pliku
        pm.save_plan("7", _body(oid, "B77"))
        got = pm.load_plan("7")
        assert got is not None, f"iter {i}: cache zgubił świeży zapis"
        pickup = [s for s in got["stops"] if s["type"] == "pickup"]
        assert pickup and pickup[0]["order_id"] == oid, \
            f"iter {i}: odczyt widzi stary plan ({pickup!r} != {oid})"


def test_wipe_recreate_same_size_fresh(tmp_plans, monkeypatch):
    """Wzorzec test_v319c_sub_a (_wipe → save nowego kuriera, ciało tej samej
    długości): load po odtworzeniu pliku NIE może zwrócić danych poprzednika."""
    monkeypatch.setattr(cm, "ENABLE_PERF_LAZY_MEMBERS", True)
    for i in range(200):
        cid_a, cid_b = "13", "14"
        pm.save_plan(cid_a, _body("N4", "B4"))
        assert pm.load_plan(cid_a) is not None
        tmp_plans.unlink()
        pm.save_plan(cid_b, _body("N5", "B5"))
        got = pm.load_plan(cid_b)
        assert got is not None, f"iter {i}: load({cid_b}) widzi plik poprzednika"
        assert pm.load_plan(cid_a) is None, f"iter {i}: duch planu {cid_a}"


def test_parity_on_off(tmp_plans, monkeypatch):
    """ON≠OFF tylko w mechanice (cache), nie w treści: identyczny wynik odczytu."""
    pm.save_plan("5", _body("N9", "B9"))
    monkeypatch.setattr(cm, "ENABLE_PERF_LAZY_MEMBERS", False)
    off = pm.load_plan("5")
    monkeypatch.setattr(cm, "ENABLE_PERF_LAZY_MEMBERS", True)
    monkeypatch.setattr(pm, "_perf_plans_cache", {"key": None, "data": None})
    on = pm.load_plan("5")
    assert off == on


def test_atomic_write_changes_cache_key(tmp_plans, monkeypatch):
    """Inwariant klucza cross-proces: każdy atomic write (os.replace ze świeżego
    tempfile) daje NOWY st_ino → klucz cache'a RÓŻNY nawet przy identycznym
    mtime_ns i size (deterministyczny strażnik komponentu st_ino)."""
    import os
    pm.save_plan("2", _body("N1", "B1"))
    st1 = pm.PLANS_FILE.stat()
    k1 = (st1.st_mtime_ns, st1.st_size, st1.st_ino)
    with pm._locked(exclusive=True):
        pm._write_raw(pm._read_raw())  # identyczna treść → identyczny size
    # wymuś identyczny mtime (symulacja zapisu w tym samym ticku zegara)
    os.utime(pm.PLANS_FILE, ns=(st1.st_atime_ns, st1.st_mtime_ns))
    st2 = pm.PLANS_FILE.stat()
    k2 = (st2.st_mtime_ns, st2.st_size, st2.st_ino)
    assert k1 != k2, "klucz cache MUSI się różnić po atomic write (st_ino)"
    assert st1.st_ino != st2.st_ino


def test_perf_lazy_in_test_isolated_infra_flags():
    """Determinizm suity: żywy flip PERF_LAZY nie przecieka do testów
    (script-runnery dostają kopię flags.json BEZ tej flagi)."""
    assert "ENABLE_PERF_LAZY_MEMBERS" in cm.TEST_ISOLATED_INFRA_FLAGS
