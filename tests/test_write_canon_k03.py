"""K03 (program refaktoru, 2026-07-06) — kanon zapisu ścieżek stanu.

Zakres:
1. `global_alloc_store.write` — unikalny mkstemp zamiast współdzielonego
   f"{path}.tmp" (anty-wzorzec naprawiony wcześniej w pending_proposals_store).
2. `courier_resolver._save_last_known_pos` — cały cykl load→merge→write pod
   fcntl.LOCK_EX: równoległe procesy nie gubią wpisów RÓŻNYCH cid (raw/01d R6).
3. telegram save_pending → delta-kanon: N-D, JUŻ WYKONANE wcześniej
   (set_pending/pop_pending/locked_merge_missing w użyciu; `save_pending`
   bez żywych callerów — dowód: grep, wpis w 05-dziennik.md).

Izolacja: wszystkie ścieżki monkeypatchowane na tmp_path (C17); guard
`_store_blocked_under_test` przepuszcza testy z patchowaną ścieżką z definicji.
"""
import json
import threading
from datetime import datetime, timezone

import pytest

import dispatch_v2.global_alloc_store as gas
import dispatch_v2.courier_resolver as cr


# ---------- global_alloc_store ----------

def test_global_alloc_roundtrip_i_brak_wspolnego_tmp(tmp_path):
    path = str(tmp_path / "global_alloc.json")
    now = datetime.now(timezone.utc)
    n = gas.write({"484999": {"verdict": "PROPOSE"}}, now, path=path)
    assert n == 1
    loaded = gas.load_fresh(now, path=path)
    assert loaded.get("484999", {}).get("verdict") == "PROPOSE"
    # kluczowa własność K03: NIE istnieje deterministyczny wspólny "{path}.tmp"
    assert not (tmp_path / "global_alloc.json.tmp").exists()
    # i nie zostały śmieci tmp po udanym zapisie
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_global_alloc_wspolbiezne_zapisy_zawsze_poprawny_json(tmp_path):
    """2 wątki × N zapisów na TEJ SAMEJ ścieżce → plik zawsze kompletny JSON
    (unikalne tmp per zapis; przy współdzielonym tmp zdarzały się kolizje/renamy
    cudzych połówek)."""
    path = str(tmp_path / "global_alloc.json")
    now = datetime.now(timezone.utc)
    errors = []

    def worker(tag):
        try:
            for i in range(40):
                gas.write({f"{tag}-{i}": {"verdict": "PROPOSE"}}, now, path=path)
                json.loads(open(path, encoding="utf-8").read())  # zawsze parsowalny
        except Exception as e:  # pragma: no cover
            errors.append(e)

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert errors == []
    final = json.loads(open(path, encoding="utf-8").read())
    assert "written_at" in final and "proposals" in final


# ---------- courier_last_pos: lock + brak lost-update rozłącznych cid ----------

@pytest.fixture
def lp_tmp(tmp_path, monkeypatch):
    p = str(tmp_path / "courier_last_pos.json")
    monkeypatch.setattr(cr, "COURIER_LAST_POS_PATH", p)
    return p


def _entry(now=None):
    now = now or datetime.now(timezone.utc)
    return {"lat": 53.13, "lon": 23.16, "ts": now.isoformat(), "source": "test"}


def test_last_pos_roundtrip(lp_tmp):
    cr._save_last_known_pos({"111": _entry()})
    disk = json.loads(open(lp_tmp, encoding="utf-8").read())
    assert "111" in disk and disk["111"]["source"] == "test"
    # lockfile powstał obok store (dowód, że ścieżka LOCK_EX jest aktywna)
    assert (json.loads(open(lp_tmp).read()) is not None)
    import os
    assert os.path.exists(lp_tmp + ".lock")


def test_last_pos_rownolegle_rozlaczne_cid_bez_lost_update(lp_tmp):
    """2 wątki piszą ROZŁĄCZNE cid wielokrotnie — na końcu OBA zestawy w store.
    Przed K03 (bez LOCK_EX) okno load↔replace mogło zgubić cudze cid."""
    errors = []

    def worker(cid):
        try:
            for _ in range(30):
                cr._save_last_known_pos({cid: _entry()})
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(c,)) for c in ("201", "202", "203")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    disk = json.loads(open(lp_tmp, encoding="utf-8").read())
    assert set(disk.keys()) >= {"201", "202", "203"}, f"lost-update: {sorted(disk)}"


def test_last_pos_merge_by_ts_nie_cofa_swiezszego(lp_tmp):
    """Charakteryzujący (zachowanie sprzed K03 utrzymane): starszy ts nie
    nadpisuje świeższego wpisu tego samego cid."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    cr._save_last_known_pos({"301": _entry(now)})
    stale = _entry(now - timedelta(minutes=3))
    stale["source"] = "stale"
    cr._save_last_known_pos({"301": stale})
    disk = json.loads(open(lp_tmp, encoding="utf-8").read())
    assert disk["301"]["source"] == "test", "starszy ts nie może cofnąć świeższego"
