#!/usr/bin/env python3
"""Testy `pending_global_resweep.py` — globalny re-ranking wiszących propozycji.
Mockujemy `_assess` (→ zero realnego assess_order/OSRM) i `C.flag`. PipelineResult/
Candidate udawane przez types.SimpleNamespace; CourierState-like też (ma .bag, kopiowalne).
"""
import sys
import json
import types
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2 import common as C
from dispatch_v2.tools import pending_global_resweep as PGR

_N = datetime(2026, 6, 24, 19, 15, 0, tzinfo=timezone.utc)


# ---- fake'i ----
def _cand(cid, score):
    return types.SimpleNamespace(
        courier_id=cid, score=float(score), name=cid,
        feasibility_verdict="MAYBE",
        metrics={"km_to_pickup": 1.0, "r6_max_bag_time_min": 20.0,
                 "r1_new_drop_cosine": 0.1, "deliv_spread_km": 3.0})


def _result(cands, total=3, feasible=3):
    cands = sorted(cands, key=lambda c: -c.score)
    best = cands[0] if cands and cands[0].score is not None else None
    return types.SimpleNamespace(best=best, candidates=cands, verdict="PROPOSE",
                                 pool_total_count=total, pool_feasible_count=feasible)


def _cs(cid, bag=None):
    return types.SimpleNamespace(courier_id=cid, bag=list(bag or []), name=cid)


def _rec(oid, rest="Pizza"):
    return {"order_id": oid, "status": "planned", "restaurant": rest,
            "delivery_address": f"addr-{oid}",
            "pickup_coords": [53.12, 23.14], "delivery_coords": [53.13, 23.20]}


# scoring bazowy: A jest „najlepszy" dla wszystkich gdy pusty, ale każde zlecenie
# w worku obniża jego score o 50 → orderzy w różne strony rozjeżdżają się na B/C.
_BASE = {
    "o1": {"A": 100, "B": 10, "C": 10},
    "o2": {"A": 90, "B": 80, "C": 10},
    "o3": {"A": 85, "B": 10, "C": 75},
}
_LOAD_PEN = 50.0


def _fake_assess(order_event, fleet, now):
    oid = order_event["order_id"]
    cands = []
    for cid in ("A", "B", "C"):
        cs = fleet.get(cid)
        load = len(cs.bag) if cs is not None else 0
        cands.append(_cand(cid, _BASE[oid][cid] - _LOAD_PEN * load))
    return _result(cands)


# ---------- global_allocate: rdzeń rozjazdu kierunków ----------
def test_global_allocate_spreads_directions(monkeypatch):
    monkeypatch.setattr(PGR, "_assess", _fake_assess)
    fleet = {c: _cs(c) for c in ("A", "B", "C")}
    hanging = [("o1", _rec("o1")), ("o2", _rec("o2")), ("o3", _rec("o3"))]
    alloc = PGR.global_allocate(hanging, fleet, _N)
    # mimo że A jest najlepszy „na sucho" dla wszystkich, globalnie rozjeżdża się:
    assert alloc["o1"]["cid"] == "A"
    assert alloc["o2"]["cid"] == "B"
    assert alloc["o3"]["cid"] == "C"
    # nie pomylił scoringu — pierwszy (najpewniejszy) idzie do A
    assert alloc["o1"]["score"] == 100.0


def test_global_allocate_does_not_mutate_input_fleet(monkeypatch):
    monkeypatch.setattr(PGR, "_assess", _fake_assess)
    fleet = {c: _cs(c) for c in ("A", "B", "C")}
    PGR.global_allocate([("o1", _rec("o1")), ("o2", _rec("o2"))], fleet, _N)
    assert all(len(fleet[c].bag) == 0 for c in ("A", "B", "C"))  # wejście nietknięte


def test_global_allocate_no_courier(monkeypatch):
    monkeypatch.setattr(PGR, "_assess", lambda oe, fl, now: _result([], total=0, feasible=0))
    alloc = PGR.global_allocate([("o1", _rec("o1"))], {"A": _cs("A")}, _N)
    assert alloc["o1"]["no_courier"] is True
    assert alloc["o1"]["cid"] is None


# ---------- run_once: end-to-end (pending+state z tmp) ----------
def _setup(tmp_path, monkeypatch, proposed_best):
    """proposed_best: {oid: cid} = co Ziomek zaproponował (greedy)."""
    pending = {}
    orders = {}
    for oid, cid in proposed_best.items():
        orders[oid] = _rec(oid)
        pending[oid] = {"order_id": oid, "message_id": 1, "sent_at": "2026-06-24T19:15:04+00:00",
                        "expires_at": "2026-06-24T19:20:04+00:00",
                        "decision_record": {"auto_route": "ALERT",
                                             "best": {"courier_id": cid, "score": _BASE[oid][cid]}}}
    pp = tmp_path / "pending.json"; pp.write_text(json.dumps(pending))
    op = tmp_path / "orders.json"; op.write_text(json.dumps(orders))
    out = tmp_path / "out.jsonl"
    monkeypatch.setattr(PGR, "PENDING_PATH", str(pp))
    monkeypatch.setattr(PGR, "ORDERS_STATE", str(op))
    monkeypatch.setattr(PGR, "OUT_JSONL", str(out))
    monkeypatch.setattr(PGR, "PINGPONG_STATE_PATH", str(tmp_path / "pingpong-state.json"))
    monkeypatch.setattr(PGR, "_assess", _fake_assess)
    monkeypatch.setattr(PGR.CR, "dispatchable_fleet", lambda: [_cs(c) for c in ("A", "B", "C")])
    monkeypatch.setattr(C, "flag", lambda n, d=False: True if n == PGR.FLAG else d)
    monkeypatch.setattr(C, "load_flags", lambda: {})
    return out


def test_run_once_flag_off_noop(monkeypatch):
    monkeypatch.setattr(C, "flag", lambda n, d=False: False)
    assert PGR.run_once(now=_N).get("skipped") == "flag_off"


def test_run_once_spread_fix(tmp_path, monkeypatch):
    # Ziomek (greedy) zaproponował A do WSZYSTKICH trzech — pile-on jednego kuriera
    km_by_cid = {"A": 9.5, "B": 1.25, "C": 2.5}

    def _assess_with_distinct_km(order_event, fleet, now):
        result = _fake_assess(order_event, fleet, now)
        for cand in result.candidates:
            cand.metrics["km_to_pickup"] = km_by_cid[cand.courier_id]
        return result

    out = _setup(tmp_path, monkeypatch, {"o1": "A", "o2": "A", "o3": "A"})
    monkeypatch.setattr(PGR, "_assess", _assess_with_distinct_km)
    s = PGR.run_once(now=_N)
    assert s["hanging"] == 3
    assert s["maxpile_before"] == 3 and s["maxpile_after"] == 1
    assert s["spread_improved"] is True
    assert s["would_repropose"] == 2  # o2,o3 przerzucone na B,C
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    by = {r["order_id"]: r for r in rows}
    assert by["o1"]["would_repropose"] is False and by["o1"]["reason"] == "bez_zmian"
    assert by["o2"]["new_cid"] == "B" and by["o2"]["reason"] == "rozjazd_kierunkow"
    assert by["o3"]["new_cid"] == "C" and by["o3"]["would_repropose"] is True
    # G5: dystans starego i nowego kuriera pochodzi z tej samej oceny; wartości
    # są różne, więc test nie przejdzie po omyłkowym skopiowaniu new_km.
    assert by["o2"]["proposed_km"] == km_by_cid["A"]
    assert by["o2"]["new_km_to_pickup"] == km_by_cid["B"]
    assert by["o2"]["delta_km"] == km_by_cid["B"] - km_by_cid["A"]
    assert {"restaurant", "delivery_address", "proposed_name", "new_name"}.isdisjoint(
        by["o2"])
    state_payload = json.loads((tmp_path / "pingpong-state.json").read_text())
    assert state_payload["schema"] == 1
    assert set(state_payload["orders"]["o2"]) == {
        "current_cid", "previous_cid", "last_swap_ts", "flip_count"}
    _assert_pingpong_guard_scenarios()
    _assert_review_g5g6_summary()


def _guard_row(oid, proposed, target):
    return {"order_id": oid, "proposed_cid": proposed, "new_cid": target,
            "new_score": 20.0, "no_courier": False}


def _assert_pingpong_guard_scenarios():
    """A→B przechodzi; powrót B→A z normalnym, lecz nie 2× marginem jest blokowany."""
    state = {}
    first = _guard_row("o1", "A", "B")
    PGR._annotate_pingpong_rows(
        [first], {"o1": {"A": 0.0, "B": 20.0}}, state, _N,
        margin=15.0, margin_multiplier=2.0, cooldown_min=10.0)
    assert first["would_pingpong_block"] is False

    third = _guard_row("o1", "A", "A")
    PGR._annotate_pingpong_rows(
        [third], {"o1": {"B": 0.0, "A": 20.0}}, state,
        _N + timedelta(minutes=11), margin=15.0,
        margin_multiplier=2.0, cooldown_min=10.0)
    assert third["pingpong_is_return"] is True
    assert third["would_pingpong_block"] is True
    assert state["o1"]["current_cid"] == "B"

    # A→B→C nie jest kontr-podmianą i nie podlega guardowi.
    state = {}
    first = _guard_row("o1", "A", "B")
    PGR._annotate_pingpong_rows(
        [first], {"o1": {"A": 0.0, "B": 20.0}}, state, _N,
        margin=15.0, margin_multiplier=2.0, cooldown_min=10.0)
    next_row = _guard_row("o1", "A", "C")
    PGR._annotate_pingpong_rows(
        [next_row], {"o1": {"B": 0.0, "C": 20.0}}, state,
        _N + timedelta(minutes=1), margin=15.0,
        margin_multiplier=2.0, cooldown_min=10.0)
    assert next_row["pingpong_is_return"] is False
    assert next_row["would_pingpong_block"] is False
    assert state["o1"]["current_cid"] == "C"

    # Nawet silny powrót czeka na cooldown; po nim przechodzi.
    state = {}
    first = _guard_row("o1", "A", "B")
    PGR._annotate_pingpong_rows(
        [first], {"o1": {"A": 0.0, "B": 35.0}}, state, _N,
        margin=15.0, margin_multiplier=2.0, cooldown_min=10.0)

    too_soon = _guard_row("o1", "A", "A")
    PGR._annotate_pingpong_rows(
        [too_soon], {"o1": {"B": 0.0, "A": 35.0}}, state,
        _N + timedelta(minutes=5), margin=15.0,
        margin_multiplier=2.0, cooldown_min=10.0)
    assert too_soon["would_pingpong_block"] is True
    assert too_soon["pingpong_elapsed_min"] == 5.0

    after_cooldown = _guard_row("o1", "A", "A")
    PGR._annotate_pingpong_rows(
        [after_cooldown], {"o1": {"B": 0.0, "A": 35.0}}, state,
        _N + timedelta(minutes=11), margin=15.0,
        margin_multiplier=2.0, cooldown_min=10.0)
    assert after_cooldown["would_pingpong_block"] is False
    assert state["o1"]["current_cid"] == "A"

    # HARD feasibility ma pierwszeństwo przed SOFT hysterezą.
    state = {"o1": {"current_cid": "B", "previous_cid": "A",
                    "last_swap_ts": _N.isoformat(), "flip_count": 1}}
    row = _guard_row("o1", "A", "A")
    PGR._annotate_pingpong_rows(
        [row], {"o1": {"A": 20.0}}, state, _N + timedelta(minutes=1),
        margin=15.0, margin_multiplier=2.0, cooldown_min=10.0)
    assert row["pingpong_is_return"] is True
    assert row["pingpong_hard_escape_current_infeasible"] is True
    assert row["would_pingpong_block"] is False


def _assert_review_g5g6_summary():
    from dispatch_v2.tools import pending_global_resweep_review as review
    got = review.summarize_g5_g6([
        {"would_repropose": True, "delta_km": -1.5,
         "pingpong_is_return": False, "would_pingpong_block": False},
        {"would_repropose": True, "delta_km": 2.0,
         "pingpong_is_return": True, "would_pingpong_block": True},
        {"would_repropose": True, "delta_km": None,
         "pingpong_is_return": False, "would_pingpong_block": None,
         "pingpong_state_error": "OSError"},
    ])
    assert got["g5"]["measured_rows"] == 2
    assert got["g5"]["missing_rows"] == 1
    assert got["g5"]["delta_km_median"] == 0.25
    assert got["g6"]["return_attempt_rows"] == 1
    assert got["g6"]["would_block_rows"] == 1


def test_run_once_single_rerank_better_courier(tmp_path, monkeypatch):
    # 1 wiszące zlecenie o2; Ziomek proponował A (score 90), ale A obciążony „w tle":
    # damy A startowy worek => jego score spadnie poniżej B.
    out = _setup(tmp_path, monkeypatch, {"o2": "A"})
    monkeypatch.setattr(PGR.CR, "dispatchable_fleet",
                        lambda: [_cs("A", bag=[{"order_id": "x"}]), _cs("B"), _cs("C")])
    s = PGR.run_once(now=_N)
    assert s["hanging"] == 1
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    r = rows[0]
    assert r["new_cid"] == "B"           # A=90-50=40 < B=80
    assert r["would_repropose"] is True
    assert r["reason"] in ("lepszy_kurier", "rozjazd_kierunkow")


def test_run_once_no_change(tmp_path, monkeypatch):
    # Ziomek zaproponował zgodnie z globalną alokacją → brak repropose.
    out = _setup(tmp_path, monkeypatch, {"o1": "A", "o2": "B", "o3": "C"})
    s = PGR.run_once(now=_N)
    assert s["would_repropose"] == 0
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert all(r["reason"] == "bez_zmian" for r in rows)


def test_run_once_skips_already_assigned(tmp_path, monkeypatch):
    out = _setup(tmp_path, monkeypatch, {"o1": "A"})
    # podmień status o1 na assigned → nie jest już „wiszące"
    op = json.loads((tmp_path / "orders.json").read_text())
    op["o1"]["status"] = "assigned"
    (tmp_path / "orders.json").write_text(json.dumps(op))
    s = PGR.run_once(now=_N)
    assert s["hanging"] == 0


# ---------- Faza C: global_allocate_results (pełne wyniki → shadow_decisions) ----------
def test_global_allocate_results_returns_full_results_consistent_with_alloc(monkeypatch):
    """Zwraca {oid: PipelineResult}; best każdego wyniku = ten sam kurier co allocation
    (jedno źródło logiki), i jest feasible-first (verdict MAYBE)."""
    monkeypatch.setattr(PGR, "_assess", _fake_assess)
    fleet = {c: _cs(c) for c in ("A", "B", "C")}
    hanging = [("o1", _rec("o1")), ("o2", _rec("o2")), ("o3", _rec("o3"))]
    alloc = PGR.global_allocate(hanging, {c: _cs(c) for c in ("A", "B", "C")}, _N)
    results = PGR.global_allocate_results(hanging, fleet, _N)
    assert set(results.keys()) == {"o1", "o2", "o3"}
    for oid in ("o1", "o2", "o3"):
        assert results[oid].best is not None
        assert str(results[oid].best.courier_id) == str(alloc[oid]["cid"])
        assert results[oid].best.feasibility_verdict == "MAYBE"  # feasible-first
    # rozjazd kierunków zachowany w wynikach (nie wszystkie na A)
    assert len({str(results[o].best.courier_id) for o in ("o1", "o2", "o3")}) == 3


def test_global_allocate_results_does_not_mutate_input_fleet(monkeypatch):
    monkeypatch.setattr(PGR, "_assess", _fake_assess)
    fleet = {c: _cs(c) for c in ("A", "B", "C")}
    PGR.global_allocate_results([("o1", _rec("o1")), ("o2", _rec("o2"))], fleet, _N)
    assert all(len(fleet[c].bag) == 0 for c in ("A", "B", "C"))


def test_global_allocate_backcompat_results_out_none_identical(monkeypatch):
    """Bez _results_out allocation bajt-identyczny jak z _results_out (zero zmiany zachowania)."""
    monkeypatch.setattr(PGR, "_assess", _fake_assess)
    hanging = [("o1", _rec("o1")), ("o2", _rec("o2")), ("o3", _rec("o3"))]
    alloc_plain = PGR.global_allocate(hanging, {c: _cs(c) for c in ("A", "B", "C")}, _N)
    collected = {}
    alloc_coll = PGR.global_allocate(hanging, {c: _cs(c) for c in ("A", "B", "C")}, _N,
                                     _results_out=collected)
    assert alloc_plain == alloc_coll
    assert set(collected.keys()) == {"o1", "o2", "o3"}


def test_global_allocate_results_failsoft_on_assess_error(monkeypatch):
    def _boom(oe, fl, now):
        raise RuntimeError("assess exploded")
    monkeypatch.setattr(PGR, "_assess", _boom)
    out = PGR.global_allocate_results([("o1", _rec("o1"))], {"A": _cs("A")}, _N)
    assert out == {}


def test_global_allocate_results_no_courier_still_recorded(monkeypatch):
    """Brak feasible (best=None) → wynik nadal w mapie (konsola pokaże KOORD/no-courier)."""
    monkeypatch.setattr(PGR, "_assess", lambda oe, fl, now: _result([], total=0, feasible=0))
    out = PGR.global_allocate_results([("o1", _rec("o1"))], {"A": _cs("A")}, _N)
    assert "o1" in out and out["o1"].best is None


# ---------- Faza C: zapis global_alloc.json dla konsoli (flaga ENABLE_GLOBAL_ALLOC_WRITE) ----------
def test_run_once_writes_global_alloc_when_flag_on(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"o1": "A", "o2": "A", "o3": "A"})
    monkeypatch.setattr(C, "flag",
                        lambda n, d=False: True if n in (PGR.FLAG, "ENABLE_GLOBAL_ALLOC_WRITE") else d)
    captured = {}
    from dispatch_v2 import shadow_dispatcher as SD, global_alloc_store as GAS
    monkeypatch.setattr(SD, "_serialize_result",
                        lambda res, eid, lat: {"order_id": eid,
                                               "best": {"courier_id": getattr(getattr(res, "best", None), "courier_id", None)}})
    monkeypatch.setattr(GAS, "write", lambda props, now, **k: (captured.__setitem__("props", props), len(props))[1])
    PGR.run_once(now=_N)
    assert "props" in captured                                 # write zawołany
    assert set(captured["props"].keys()) == {"o1", "o2", "o3"}  # WSZYSTKIE wiszące, nie tylko zmienione


def test_run_once_no_global_alloc_write_when_flag_off(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"o1": "A"})   # _setup: tylko PGR.FLAG ON, write-flag OFF
    called = {"w": False}
    from dispatch_v2 import global_alloc_store as GAS
    monkeypatch.setattr(GAS, "write", lambda *a, **k: called.update(w=True) or 0)
    PGR.run_once(now=_N)
    assert called["w"] is False                  # write NIE zawołany przy fladze OFF
