"""K06 (refaktor, 2026-07-06) — world_replay: plumbing sandboxu replayu.

Testujemy INFRASTRUKTURĘ (nie decyzję): rehydracja floty (iso→datetime,
list→tuple), OsrmReplayer (FIFO per klucz, jednokrotne zuzycie wyniku,
licznik missów), replay_one (zamrożenie flag K05 z nagrania, OSRM z nagrania,
efekty K08 połknięte-nie-flushowane, world_record wyłączony, restauracja
patchy po wyjściu). Prawdziwy replay end-to-end = bieg na korpusie (bramka
~09-10.07), nie unit.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import dispatch_v2.common as C
import dispatch_v2.dispatch_pipeline as dp
import dispatch_v2.effects_buffer as eb
import dispatch_v2.osrm_client as osrm
import dispatch_v2.world_record as wr
from dispatch_v2.tools import world_replay as wrep


def test_rehydrate_fleet_typy():
    fleet = wrep.rehydrate_fleet({
        "123": {"courier_id": "123", "pos": [53.13, 23.16],
                "shift_start": "2026-07-06T08:00:00+00:00",
                "bag": [{"order_id": "1"}]},
    })
    cs = fleet["123"]
    assert cs.courier_id == "123"
    assert cs.pos == (53.13, 23.16), "lista z JSON → tuple"
    assert cs.shift_start == datetime(2026, 7, 6, 8, 0, tzinfo=timezone.utc), "iso → datetime"
    assert cs.bag == [{"order_id": "1"}]


def test_osrm_replayer_fifo_i_missy():
    calls = [
        {"kind": "route", "key": [[1.0, 2.0], [3.0, 4.0]], "result": {"duration_min": 5.0}},
        {"kind": "route", "key": [[1.0, 2.0], [3.0, 4.0]], "result": {"duration_min": 6.0}},
    ]
    r = wrep.OsrmReplayer(calls)
    assert r.route((1.0, 2.0), (3.0, 4.0))["duration_min"] == 5.0, "FIFO: 1. wynik"
    assert r.route((1.0, 2.0), (3.0, 4.0))["duration_min"] == 6.0, "FIFO: 2. wynik"
    exhausted = r.route((1.0, 2.0), (3.0, 4.0))
    assert exhausted.get("replay_miss") is True, "wyczerpane → sentinel, nie reuse"
    assert len(r.misses) == 1
    out = r.route((9.0, 9.0), (8.0, 8.0))
    assert out.get("replay_miss") is True and len(r.misses) == 2, "nieznany klucz = miss"


def test_osrm_single_recorded_result_is_consumed_exactly_once():
    r = wrep.OsrmReplayer([
        {"kind": "route", "key": [[1.0, 2.0], [3.0, 4.0]],
         "result": {"duration_min": 5.0}},
    ])
    assert r.route((1.0, 2.0), (3.0, 4.0)) == {"duration_min": 5.0}
    extra = r.route((1.0, 2.0), (3.0, 4.0))
    assert extra["replay_miss"] is True
    assert len(r.misses) == 1


def _rec(flags=None):
    return {
        "order_id": "486200", "ts": "2026-07-06T15:00:05+00:00",
        "now": "2026-07-06T15:00:00+00:00",
        "schema": "wr1",
        "flags": flags or {"ENABLE_X_TESTOWA": True},
        "order_event": {"order_id": "486200"},
        "fleet": {"123": {"courier_id": "123", "pos": [53.13, 23.16]}},
        "live_inputs": {"reliability": {}, "plans": {}, "eta_quantile": {},
                        "prep_bias": {}, "loadgov": [None, None, None, 0],
                        "k07": None, "courier_last_pos": {}},
        "osrm_calls": [{"kind": "route", "key": [[53.13, 23.16], [53.11, 23.15]],
                        "result": {"duration_min": 7.0}}],
        "verdict": "PROPOSE",
    }


def test_replay_one_sandbox_i_restauracja(monkeypatch, tmp_path):
    rec = _rec()
    widziane = {}

    def fake_assess(order_event, fleet, meta, now):
        widziane["flags_override"] = dict(C._FLAGS_SNAPSHOT_OVERRIDE or {})
        widziane["flag_przez_C"] = C.flag("ENABLE_X_TESTOWA", False)
        widziane["osrm"] = osrm.route((53.13, 23.16), (53.11, 23.15))
        widziane["now"] = now
        widziane["fleet_cid"] = next(iter(fleet)); widziane["pos"] = fleet["123"].pos
        # efekt K08 w trakcie replayu — MUSI zostać połknięty (zero zapisu)
        dp._append_difficult_case_log({"oid": "486200"})
        widziane["wr_enabled"] = wr.enabled()
        return SimpleNamespace(verdict="PROPOSE", reason="ok",
                               best=SimpleNamespace(courier_id="123", score=42.0),
                               pool_feasible_count=1, pool_total_count=1)

    monkeypatch.setattr(dp, "assess_order", fake_assess)
    p = tmp_path / "difficult.jsonl"
    monkeypatch.setattr(dp.C, "DIFFICULT_CASE_LOG_PATH", str(p), raising=False)

    out, misses = wrep.replay_one(rec)

    assert widziane["flags_override"] == {"ENABLE_X_TESTOWA": True}, "K05: flagi z nagrania"
    assert widziane["flag_przez_C"] is True, "C.flag czyta zamrożone nagranie"
    assert widziane["osrm"] == {"duration_min": 7.0}, "OSRM z nagrania, zero sieci"
    assert widziane["now"] == datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc)
    assert widziane["pos"] == (53.13, 23.16)
    assert widziane["wr_enabled"] is False, "replay nie nagrywa się ponownie"
    assert not p.exists(), "efekt K08 połknięty (divert bez flusha)"
    assert misses == 0
    assert out == {"verdict": "PROPOSE", "reason": "ok", "best_cid": "123",
                   "best_score": 42.0, "pool_feasible": 1, "pool_total": 1}

    # restauracja świata po replayu
    assert C._FLAGS_SNAPSHOT_OVERRIDE is None
    assert eb._ACTIVE is False and eb._Q == []
    assert osrm.route is not None and osrm.route.__name__ == "route", "oryginalny route przywrócony"


def test_extract_z_zapisu_shadow():
    shadow = {"verdict": "PROPOSE", "reason": "ok",
              "best": {"courier_id": "123", "score": 42.0004},
              "pool_feasible_count": 3, "pool_total_count": 9}
    assert wrep._extract(shadow) == {"verdict": "PROPOSE", "reason": "ok",
                                     "best_cid": "123", "best_score": 42.0,
                                     "pool_feasible": 3, "pool_total": 9}
