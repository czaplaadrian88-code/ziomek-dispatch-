"""K04 (program refaktoru, 2026-07-06, ADR-R04) — world_record: nagrywanie
wejść decyzji. Testy: ON≠OFF (flaga), fail-soft, rekorder OSRM (w tym wątki
puli kandydatów), serializacja dataclass/datetime, zapis jsonl + retencja.

Izolacja (C17): RECORD_DIR patchowany na tmp_path (guard _blocked_under_test
przepuszcza tylko nie-domyślną ścieżkę); enabled() patchowane wprost —
nie dotykamy flags.json.
"""
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

import dispatch_v2.osrm_client as osrm
import dispatch_v2.world_record as wr


@pytest.fixture
def rec_dir(tmp_path, monkeypatch):
    d = tmp_path / "world_record"
    d.mkdir()
    monkeypatch.setattr(wr, "RECORD_DIR", str(d))
    return d


# ---------- flaga OFF = czysta delegacja ----------

def test_off_deleguje_bez_nagrania(rec_dir, monkeypatch):
    monkeypatch.setattr(wr, "enabled", lambda: False)
    called = {}
    out = wr.around_assess(lambda: called.setdefault("r", "WYNIK"))
    assert out == "WYNIK" and called["r"] == "WYNIK"
    assert list(rec_dir.iterdir()) == [], "OFF nie może niczego pisać"


# ---------- flaga ON = nagranie kompletu ----------

def test_on_nagrywa_flagi_flote_osrm(rec_dir, monkeypatch):
    monkeypatch.setattr(wr, "enabled", lambda: True)
    monkeypatch.setattr(wr.C, "load_flags", lambda: {"ENABLE_X": True, "PROG": 35})

    @dataclass
    class FakeCS:
        cid: str
        pos: tuple
        shift_start: datetime

    fleet = {"123": FakeCS("123", (53.13, 23.16), datetime(2026, 7, 6, 8, 0, tzinfo=timezone.utc))}
    order = {"order_id": "486001", "restaurant": "Testownia"}
    now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)

    def fake_assess():
        # symulacja konsumpcji OSRM w decyzji — także z wątku (pula kandydatów)
        osrm._wr_log("route", [[53.13, 23.16], [53.11, 23.15]], {"duration_min": 7.5})
        t = threading.Thread(
            target=lambda: osrm._wr_log("table", [[[53.13, 23.16]], [[53.11, 23.15]]], [[7.5]])
        )
        t.start(); t.join()
        class R: verdict = "PROPOSE"
        return R()

    out = wr.around_assess(fake_assess, order_event=order, fleet_snapshot=fleet, now=now)
    assert out.verdict == "PROPOSE"

    files = list(rec_dir.glob("world_record-*.jsonl"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text().splitlines()[0])
    assert rec["schema"] == "wr1"  # v1 (2026-07-06): +live_inputs (K07/loadgov/pliki)
    assert rec["order_id"] == "486001"
    assert rec["verdict"] == "PROPOSE"
    assert rec["flags"] == {"ENABLE_X": True, "PROG": 35} and rec["flags_sha1"]
    assert rec["fleet"]["123"]["cid"] == "123"
    assert rec["fleet"]["123"]["shift_start"].startswith("2026-07-06T08:00")
    kinds = {c["kind"] for c in rec["osrm_calls"]}
    assert kinds == {"route", "table"}, "rekorder musi złapać też wywołanie z wątku"
    assert rec["n_osrm"] == 2
    assert rec["now"].startswith("2026-07-06T12:00")


# ---------- fail-soft: błąd nagrywania nie zmienia wyniku ----------

def test_fail_soft_gdy_zapis_pada(rec_dir, monkeypatch):
    monkeypatch.setattr(wr, "enabled", lambda: True)
    monkeypatch.setattr(wr, "_capture", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    out = wr.around_assess(lambda: "OK")
    assert out == "OK"


def test_wyjatek_decyzji_propaguje_a_rekorder_sie_zamyka(rec_dir, monkeypatch):
    monkeypatch.setattr(wr, "enabled", lambda: True)
    with pytest.raises(ValueError):
        wr.around_assess(lambda: (_ for _ in ()).throw(ValueError("boom")))
    # rekorder nie może zostać aktywny po wyjątku
    assert osrm._WR_ACTIVE is False


# ---------- rekorder osrm: aktywacja/dezaktywacja ----------

def test_rekorder_nieaktywny_nie_zbiera():
    osrm.world_record_stop()  # upewnij stan wyjściowy
    osrm._wr_log("route", ["x"], {"d": 1})
    assert osrm.world_record_stop() == []
    osrm.world_record_start()
    osrm._wr_log("route", ["x"], {"d": 1})
    calls = osrm.world_record_stop()
    assert len(calls) == 1 and calls[0]["kind"] == "route"


def test_wrappery_route_table_naprawde_naglywaja(monkeypatch):
    """route()/table() (wrappery K04) wołają impl i logują wynik do rekordera."""
    monkeypatch.setattr(osrm, "_route_impl_k04", lambda a, b, use_cache=True: {"duration_min": 3.3})
    monkeypatch.setattr(osrm, "_table_impl_k04", lambda o, d: [[1.1]])
    osrm.world_record_start()
    r = osrm.route((53.13, 23.16), (53.11, 23.15))
    t = osrm.table([(53.13, 23.16)], [(53.11, 23.15)])
    calls = osrm.world_record_stop()
    assert r == {"duration_min": 3.3} and t == [[1.1]]
    assert [c["kind"] for c in calls] == ["route", "table"]
    assert calls[0]["result"] == {"duration_min": 3.3}


# ---------- toggle PRAWDZIWYM mechanizmem flagi (C-FLAG-EFFECT) ----------

def test_toggle_enable_world_record_przez_flags_json(rec_dir, monkeypatch, tmp_path):
    """ENABLE_WORLD_RECORD togglowane realną ścieżką C.flag/flags.json:
    false → zero zapisu (delegacja 1:1); true → nagranie powstaje."""
    import json as _json
    import os as _os
    import dispatch_v2.common as C

    p = tmp_path / "flags.json"

    def put(val, t):
        p.write_text(_json.dumps({"ENABLE_WORLD_RECORD": val}))
        _os.utime(p, (t, t))

    monkeypatch.setattr(C, "FLAGS_PATH", p)
    monkeypatch.setattr(C, "_flags_cache", None)
    monkeypatch.setattr(C, "_flags_mtime", 0)
    monkeypatch.setattr(C, "_flags_last_stat_mono", 0.0)
    monkeypatch.setattr(C, "_perf_lazy_members", False)
    monkeypatch.setattr(C, "_FLAGS_SNAPSHOT_OVERRIDE", None)

    put(False, 1_000_000)
    assert wr.around_assess(lambda: "OFF") == "OFF"
    assert list(rec_dir.glob("world_record-*.jsonl")) == [], "OFF = zero nagrania"

    put(True, 1_000_100)
    assert wr.around_assess(lambda: "ON", order_event={"order_id": "1"},
                            fleet_snapshot={}, now=None) == "ON"
    files = list(rec_dir.glob("world_record-*.jsonl"))
    assert len(files) == 1, "ON = nagranie powstaje (efekt flagi)"


# ---------- retencja ----------

def test_gc_kasuje_stare_pliki(rec_dir, monkeypatch):
    old = rec_dir / "world_record-20260601.jsonl"   # > RETENTION_DAYS temu
    fresh = rec_dir / "world_record-20260705.jsonl"
    old.write_text("{}\n"); fresh.write_text("{}\n")
    wr._gc(datetime(2026, 7, 6, tzinfo=timezone.utc))
    assert not old.exists(), "plik starszy niż retencja musi zniknąć"
    assert fresh.exists()
