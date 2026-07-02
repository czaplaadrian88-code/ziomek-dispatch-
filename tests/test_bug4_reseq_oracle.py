"""Testy przyrządu bug4_reseq_oracle (naprawa kłamiącego przyrządu, oś=OBJEKTYW).

Ładowanie SELF-LOCATING (C12e): tool z tego worktree po ścieżce względnej od pliku
testu (NIE hardcode /workspace/wt-*), sprzątanie sys.modules w try/finally. Silnik
`dispatch_v2` importuje się z KANONU (conftest pinuje _SCRIPTS_ROOT) — faithful.
"""
import importlib.util
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

_TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "bug4_reseq_oracle.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("bug4_reseq_oracle_uut", _TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return mod


@pytest.fixture()
def O():
    mod = _load_tool()
    try:
        yield mod
    finally:
        sys.modules.pop("bug4_reseq_oracle_uut", None)


def _osim():
    from dispatch_v2 import route_simulator_v2 as R
    return R


NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)
POS = (53.1325, 23.1688)


def _carried_plus_future(R):
    """Kanoniczny case: carried A (odebrane dawno) + B (jedzenie za 25 min).
    opt = carried-first (A przed B); frozen = B najpierw."""
    A = R.OrderSim("A", (53.120, 23.120), (53.133, 23.220),
                   picked_up_at=NOW - timedelta(minutes=40), status="picked_up")
    B = R.OrderSim("B", (53.120, 23.120), (53.118, 23.115), status="assigned",
                   pickup_ready_at=NOW + timedelta(minutes=25))
    return {"A": A, "B": B}


# ── ORACLE (druga metoda) ────────────────────────────────────────────────────
def test_oracle_engine_matches_independent(O):
    R = _osim()
    sims = _carried_plus_future(R)
    nodes, pidx, didx = O._build_nodes(POS, sims)
    frozen = [pidx["B"], didx["B"], didx["A"]]
    res = O.score_bag(POS, sims, frozen, NOW)
    # odtwórz opt seq i policz NIEZALEŻNĄ metodą
    opt_seq = O._seq_from_deliv(nodes, pidx, didx, res, sims, NOW)
    opt_plan = [("pickup" if nodes[i]["kind"] == "pickup" else "delivery", nodes[i]["order_id"])
                for i in opt_seq]
    ind_total, _, _ = O.independent_total_min(POS, sims, opt_plan, NOW)
    assert abs(res["opt_total"] - ind_total) < 0.5, \
        f"oracle mismatch engine={res['opt_total']} independent={ind_total}"


# ── INWARIANT-TRIPWIRE (poprawna oś) ─────────────────────────────────────────
def test_objective_invariant_opt_not_worse_than_frozen(O):
    R = _osim()
    sims = _carried_plus_future(R)
    nodes, pidx, didx = O._build_nodes(POS, sims)
    frozen = [pidx["B"], didx["B"], didx["A"]]
    res = O.score_bag(POS, sims, frozen, NOW)
    assert res["invariant_ok"] is True
    assert res["obj_delta_min"] >= -O._EPS
    # realny reseq: opt dostarcza A przed B, frozen odwrotnie
    assert res["opt_deliv_order"] == ["A", "B"]
    assert res["frozen_deliv_order"] == ["B", "A"]
    assert res["deliv_seq_differs"] is True


def test_drive_axis_is_wrong_axis_regression(O):
    """KEYSTONE: stary przyrząd (drive) uznałby to za suspect/beneficjum-brak,
    a na OBJEKTYWIE reseq wyraźnie pomaga. Chroni przed powrotem drive-osi."""
    R = _osim()
    sims = _carried_plus_future(R)
    nodes, pidx, didx = O._build_nodes(POS, sims)
    frozen = [pidx["B"], didx["B"], didx["A"]]
    res = O.score_bag(POS, sims, frozen, NOW)
    # objektyw: opt istotnie lepszy
    assert res["obj_delta_min"] > 5.0
    # drive: mylący (frozen ma MNIEJ jazdy niż opt lub porównywalnie) → drive to zła oś
    assert res["drive_delta_min"] < res["obj_delta_min"]


def test_determinism_two_runs(O):
    R = _osim()
    sims = _carried_plus_future(R)
    nodes, pidx, didx = O._build_nodes(POS, sims)
    frozen = [pidx["B"], didx["B"], didx["A"]]
    r1 = O.score_bag(POS, sims, frozen, NOW)
    r2 = O.score_bag(POS, sims, frozen, NOW)
    assert (r1["opt_total"], r1["opt_deliv_order"]) == (r2["opt_total"], r2["opt_deliv_order"])


# ── RE-WERDYKT z logu: reklasyfikacja drive-suspectów ────────────────────────
def test_reverdict_reclassifies_same_node_suspects(O, tmp_path):
    jsonl = tmp_path / "shadow.jsonl"
    import json
    rows = [
        # same-node drive-suspect (reorder) → wrong-axis FP
        {"ts": "2026-06-29T10:00:00+00:00", "cid": "1", "invariant_violation": True,
         "deliv_seq_differs": True,
         "frozen_seq": ["B:pickup", "A:dropoff", "B:dropoff"],
         "fresh_seq": ["A:dropoff", "B:pickup", "B:dropoff"], "delta_min": -3.0},
        # zwykły materialny reseq (nie suspect)
        {"ts": "2026-06-29T10:01:00+00:00", "cid": "2", "invariant_violation": False,
         "deliv_seq_differs": True,
         "frozen_seq": ["X:dropoff", "Y:dropoff"], "fresh_seq": ["Y:dropoff", "X:dropoff"],
         "delta_min": 4.0},
        # brak reseq
        {"ts": "2026-06-29T10:02:00+00:00", "cid": "3", "invariant_violation": False,
         "deliv_seq_differs": False,
         "frozen_seq": ["Z:dropoff"], "fresh_seq": ["Z:dropoff"], "delta_min": 0.0},
    ]
    with open(jsonl, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    out = O.reverdict_from_log(str(jsonl), since="2026-06-29")
    assert out["n"] == 3
    assert out["old_drive_suspect"] == 1
    assert out["wrong_axis_fp"] == 1
    assert out["corrected_contamination_suspect"] == 0
    assert out["deliv_seq_differs"] == 2


# ── MUTATION x2 (C13: zmutuj cel, potwierdź że test PADA) ─────────────────────
def test_mutation_enumeration_is_load_bearing(O):
    """Mutacja 1: jeśli enumeracja zwraca TYLKO frozen (nie eksploruje reseq),
    obj_delta==0 i deliv_seq_differs==False → benefit znika. Potwierdza, że pełna
    enumeracja jest nośna (nie martwa)."""
    R = _osim()
    sims = _carried_plus_future(R)
    nodes, pidx, didx = O._build_nodes(POS, sims)
    frozen = [pidx["B"], didx["B"], didx["A"]]
    base = O.score_bag(POS, sims, frozen, NOW)
    assert base["obj_delta_min"] > 5.0 and base["deliv_seq_differs"] is True
    orig = O._valid_sequences
    try:
        O._valid_sequences = lambda s, p, d: iter([frozen])  # mutacja: tylko frozen
        mut = O.score_bag(POS, sims, frozen, NOW)
        assert abs(mut["obj_delta_min"]) < O._EPS, "mutacja nie zmieniła wyniku — enumeracja martwa!"
        assert mut["deliv_seq_differs"] is False
    finally:
        O._valid_sequences = orig


def test_mutation_wait_term_is_load_bearing(O):
    """Mutacja 2: niezależna wycena BEZ członu postoju (max(arrival,ready)) rozjeżdża
    się z silnikiem dla case z przyszłą gotowością → potwierdza, że oracle realnie
    testuje modelowanie POSTOJU (nie tylko jazdę)."""
    R = _osim()
    sims = _carried_plus_future(R)
    nodes, pidx, didx = O._build_nodes(POS, sims)
    frozen = [pidx["B"], didx["B"], didx["A"]]
    res = O.score_bag(POS, sims, frozen, NOW)
    # FROZEN order (B:pickup najpierw) DOJEŻDŻA po jedzenie ZA WCZEŚNIE → POSTÓJ wiąże.
    # Niezależna wycena frozen (z postojem) MUSI zgadzać się z silnikiem frozen_total.
    frozen_plan = [("pickup", "B"), ("delivery", "B"), ("delivery", "A")]
    faithful, _, _ = O.independent_total_min(POS, sims, frozen_plan, NOW)
    assert abs(res["frozen_total"] - faithful) < 0.5, \
        f"frozen oracle mismatch engine={res['frozen_total']} independent={faithful}"

    # mutacja: usuń wait (nie skacz do ready) — na FROZEN (gdzie postój wiąże) ZANIŻY → mismatch
    from dispatch_v2 import route_simulator_v2 as R2

    def _nowait(pos, sims, node_plan, now):
        t = now
        cur = pos
        for kind, oid in node_plan:
            s = sims[oid]
            coords = s.pickup_coords if kind == "pickup" else s.delivery_coords
            m = O._osrm_route_min(cur, coords) or 0.0
            t = t + timedelta(minutes=m)
            cur = coords
            if kind == "pickup":
                t = t + timedelta(minutes=R2.DWELL_PICKUP_MIN)  # BRAK wait jump
            else:
                t = t + timedelta(minutes=R2.DWELL_DROPOFF_MIN)
        return (t - now).total_seconds() / 60.0

    mutated = _nowait(POS, sims, frozen_plan, NOW)
    assert abs(res["frozen_total"] - mutated) > 0.5, \
        "człon POSTOJU niewidoczny — oracle nie testuje oczekiwania na jedzenie!"
