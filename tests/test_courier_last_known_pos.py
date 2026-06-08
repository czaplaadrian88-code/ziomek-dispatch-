"""Testy persistent last-known-position store (FIX 2026-06-08).

Kontekst (case Piotr Zaw 470, order 479289 Piwo Kaczka Sushi): kurier który
chwilę wcześniej był aktywny (dostawa ~10 min temu) tracił pozycję do
BIALYSTOK_CENTER fiction, bo jego order zniknął z orders_state (prune / cid=None
unlink) ZANIM 30-min recent-activity fallback zdążył go użyć → pos_source=no_gps
→ kara + _demote_blind_empty → mniej zleceń. Store (courier-keyed, niezależny od
orders_state) odtwarza ostatnią ŻYWĄ pozycję w luce GPS, bounded TTL.

Uruchom:
    /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/test_courier_last_known_pos.py -v
albo standalone:
    /root/.openclaw/venvs/dispatch/bin/python tests/test_courier_last_known_pos.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import courier_resolver as CR  # noqa: E402

# Białystok, w bboxie
POS_OK = (53.1400, 23.1500)
# poza bboxem (Warszawa)
POS_FAR = (52.2297, 21.0122)


def _entry(pos, age_min, source="last_delivered", now=None):
    now = now or datetime.now(timezone.utc)
    ts = (now - timedelta(minutes=age_min)).isoformat()
    return {"lat": pos[0], "lon": pos[1], "ts": ts, "source": source}


# ─────────────────────────────────────────────────────────────────────────────
# 1. _rescue_from_last_pos — pure, zero I/O
# ─────────────────────────────────────────────────────────────────────────────

def test_rescue_fresh_entry_ok():
    now = datetime.now(timezone.utc)
    r = CR._rescue_from_last_pos(_entry(POS_OK, age_min=8, source="last_picked_up_delivery", now=now), now)
    assert r is not None
    (lat, lon), src, age = r
    assert (round(lat, 4), round(lon, 4)) == POS_OK
    assert src == "last_picked_up_delivery"
    assert 7.5 < age < 8.5


def test_rescue_stale_beyond_ttl_none():
    now = datetime.now(timezone.utc)
    # TTL=25 → 30 min za stary
    assert CR._rescue_from_last_pos(_entry(POS_OK, age_min=30, now=now), now) is None


def test_rescue_just_under_ttl_ok():
    now = datetime.now(timezone.utc)
    assert CR._rescue_from_last_pos(_entry(POS_OK, age_min=24.0, now=now), now) is not None


def test_rescue_missing_coords_none():
    now = datetime.now(timezone.utc)
    assert CR._rescue_from_last_pos({"ts": now.isoformat(), "source": "gps"}, now) is None


def test_rescue_out_of_bbox_none():
    now = datetime.now(timezone.utc)
    assert CR._rescue_from_last_pos(_entry(POS_FAR, age_min=5, now=now), now) is None


def test_rescue_bad_source_coerced():
    now = datetime.now(timezone.utc)
    r = CR._rescue_from_last_pos(_entry(POS_OK, age_min=5, source="no_gps", now=now), now)
    assert r is not None and r[1] == "last_delivered"


def test_rescue_bad_ts_none():
    now = datetime.now(timezone.utc)
    bad = {"lat": POS_OK[0], "lon": POS_OK[1], "ts": "garbage", "source": "gps"}
    # zły ts → DT_MIN_UTC → ogromny age → None
    assert CR._rescue_from_last_pos(bad, now) is None


def test_rescue_not_dict_none():
    now = datetime.now(timezone.utc)
    assert CR._rescue_from_last_pos(None, now) is None
    assert CR._rescue_from_last_pos("x", now) is None


# ─────────────────────────────────────────────────────────────────────────────
# 2. save/load round-trip + merge-by-ts + prune
# ─────────────────────────────────────────────────────────────────────────────

def _with_tmp_store(fn):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "courier_last_pos.json")
        with mock.patch.object(CR, "COURIER_LAST_POS_PATH", path):
            fn(path)


def test_save_load_roundtrip():
    def body(path):
        now = datetime.now(timezone.utc)
        CR._save_last_known_pos({"470": _entry(POS_OK, 3, "gps", now)})
        loaded = CR._load_last_known_pos()
        assert "470" in loaded and abs(loaded["470"]["lat"] - POS_OK[0]) < 1e-6
    _with_tmp_store(body)


def test_save_merge_keeps_newer_ts():
    def body(path):
        now = datetime.now(timezone.utc)
        # disk = stary wpis
        CR._save_last_known_pos({"470": _entry(POS_OK, age_min=20, source="last_delivered", now=now)})
        # nowy zapis innego procesu = świeższy ts → wygrywa
        CR._save_last_known_pos({"470": _entry(POS_OK, age_min=1, source="gps", now=now)})
        loaded = CR._load_last_known_pos()
        assert loaded["470"]["source"] == "gps"

        # zapis STARSZEGO ts nie cofa świeższego (merge-by-ts)
        CR._save_last_known_pos({"470": _entry(POS_FAR, age_min=15, source="last_delivered", now=now)})
        loaded2 = CR._load_last_known_pos()
        assert loaded2["470"]["source"] == "gps", "starszy ts cofnął świeższy wpis — merge bug"
    _with_tmp_store(body)


def test_save_prune_old_entries():
    def body(path):
        now = datetime.now(timezone.utc)
        CR._save_last_known_pos({
            "fresh": _entry(POS_OK, age_min=5, now=now),
            "ancient": _entry(POS_OK, age_min=CR.LAST_KNOWN_POS_PRUNE_MIN + 60, now=now),
        })
        loaded = CR._load_last_known_pos()
        assert "fresh" in loaded
        assert "ancient" not in loaded, "stary wpis nie został usunięty (prune bug)"
    _with_tmp_store(body)


def test_load_corrupt_returns_empty():
    def body(path):
        with open(path, "w") as f:
            f.write("{not json")
        assert CR._load_last_known_pos() == {}
    _with_tmp_store(body)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Integracja z build_fleet_snapshot
# ─────────────────────────────────────────────────────────────────────────────

def _flag_side_effect(lp_on):
    def _f(name, default=False):
        if name == "ENABLE_COURIER_LAST_KNOWN_POS":
            return lp_on
        return default
    return _f


def _run_fleet(state, names, gps=None, store=None, lp_on=True):
    """build_fleet_snapshot z mockowanymi loaderami + store + flagą."""
    captured = {}

    def _fake_save(upd):
        captured["saved"] = dict(upd)

    with mock.patch.object(CR, "_load_kurier_piny", return_value={}), \
         mock.patch.object(CR, "_load_courier_names", return_value=names), \
         mock.patch.object(CR, "_load_gps_positions", return_value=gps or {}), \
         mock.patch.object(CR, "_load_courier_tiers", return_value={}), \
         mock.patch.object(CR, "_load_last_known_pos", return_value=dict(store or {})), \
         mock.patch.object(CR, "_save_last_known_pos", side_effect=_fake_save), \
         mock.patch.object(CR, "flag", side_effect=_flag_side_effect(lp_on)), \
         mock.patch("dispatch_v2.state_machine.get_all", return_value=state):
        fleet = CR.build_fleet_snapshot()
    return fleet, captured


def test_rescue_idle_no_gps_courier_when_flag_on():
    """Kurier bez orderów, bez GPS, ale ze świeżym wpisem w store →
    odtworzona pozycja zamiast BIALYSTOK_CENTER (case Piotr Zaw)."""
    now = datetime.now(timezone.utc)
    store = {"470": _entry(POS_OK, age_min=9, source="last_delivered", now=now)}
    fleet, _ = _run_fleet({}, {"470": "Piotr Zaw"}, store=store, lp_on=True)
    cs = fleet["470"]
    assert cs.pos_source == "last_delivered", f"oczekiwano rescue, jest {cs.pos_source}"
    assert cs.pos_from_store is True
    assert tuple(round(x, 4) for x in cs.pos) == POS_OK
    assert tuple(cs.pos) != tuple(CR.BIALYSTOK_CENTER)


def test_no_rescue_when_flag_off():
    """Flaga OFF → zachowanie sprzed fixu: no_gps / BIALYSTOK_CENTER (mimo store)."""
    now = datetime.now(timezone.utc)
    store = {"470": _entry(POS_OK, age_min=9, now=now)}
    fleet, _ = _run_fleet({}, {"470": "Piotr Zaw"}, store=store, lp_on=False)
    cs = fleet["470"]
    assert cs.pos_source == "no_gps"
    assert tuple(cs.pos) == tuple(CR.BIALYSTOK_CENTER)


def test_no_rescue_when_store_stale():
    """Wpis starszy niż TTL → brak rescue, no_gps (guard FAIL-02: brak phantom)."""
    now = datetime.now(timezone.utc)
    store = {"470": _entry(POS_OK, age_min=40, now=now)}
    fleet, _ = _run_fleet({}, {"470": "Piotr Zaw"}, store=store, lp_on=True)
    cs = fleet["470"]
    assert cs.pos_source == "no_gps", "stary wpis store ożywił widmo (REGRESJA FAIL-02)"


def test_laundering_guard_rescued_not_re_persisted():
    """Kurier uratowany ze store (pusty bag) NIE jest re-zapisywany ze świeżym ts —
    inaczej pozycja byłaby nieśmiertelna (immortal phantom)."""
    now = datetime.now(timezone.utc)
    store = {"470": _entry(POS_OK, age_min=9, now=now)}
    fleet, captured = _run_fleet({}, {"470": "Piotr Zaw"}, store=store, lp_on=True)
    saved = captured.get("saved", {})
    assert "470" not in saved, "uratowany ze store kurier został re-persystowany (laundering)"


def test_live_gps_courier_is_persisted():
    """Kurier z realnym świeżym GPS → zapisany do store (by uratować go w przyszłej luce)."""
    now = datetime.now(timezone.utc)
    gps = {"470": {"lat": POS_OK[0], "lon": POS_OK[1], "timestamp": now.isoformat()}}
    fleet, captured = _run_fleet({}, {"470": "Piotr Zaw"}, gps=gps, store={}, lp_on=True)
    assert fleet["470"].pos_source == "gps"
    saved = captured.get("saved", {})
    assert "470" in saved and saved["470"]["source"] == "gps"


def test_recent_delivery_still_works_without_store():
    """Świeża dostawa (<30 min) NADAL w stanie → recent-activity fallback działa
    bez store (nie zepsuliśmy istniejącej ścieżki)."""
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(minutes=10)).isoformat()
    state = {
        "479282": {
            "courier_id": "470", "status": "delivered", "order_id": "479282",
            "delivery_coords": list(POS_OK), "delivered_at": ts, "updated_at": ts,
        }
    }
    fleet, _ = _run_fleet(state, {"470": "Piotr Zaw"}, store={}, lp_on=True)
    cs = fleet["470"]
    assert cs.pos_source in ("last_delivered", "last_picked_up_recent")
    assert cs.pos_from_store is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. Guard: pytest na PROD boxie nie zatruwa produkcyjnego store
# ─────────────────────────────────────────────────────────────────────────────

def test_store_io_blocked_on_default_path_under_pytest():
    """Pod pytest + domyślna ścieżka → load/save są no-op (test cid nie ląduje
    w produkcyjnym dispatch_state). PYTEST_CURRENT_TEST ustawia pytest sam."""
    assert CR._store_blocked_under_test() is True
    CR._save_last_known_pos({"999": _entry(POS_OK, 1)})  # no-op, brak wyjątku
    assert CR._load_last_known_pos() == {}


def test_store_io_allowed_on_patched_tmp_path():
    """Test który JAWNIE patchuje ścieżkę na tmp (round-trip) NIE jest blokowany."""
    def body(path):
        assert CR._store_blocked_under_test() is False
        CR._save_last_known_pos({"470": _entry(POS_OK, 1)})
        assert "470" in CR._load_last_known_pos()
    _with_tmp_store(body)


# ─────────────────────────────────────────────────────────────────────────────
# standalone runner
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
            passed += 1
        except Exception:
            print(f"  FAIL {fn.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed + failed} PASS")
    sys.exit(1 if failed else 0)
