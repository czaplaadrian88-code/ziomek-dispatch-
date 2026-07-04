"""L6.C (2026-07-04) — geometria w selekcji (C2) + claim ledger (C3) + bramka LIVE (C3c).

Dowody protokołu #0 ETAP 4:
 - flaga zmienia decyzję (ON≠OFF) dla ENABLE_LEXQUAL_GEOMETRY_TIEBREAK,
   LEXQUAL_TIME_QUANT_MIN i ENABLE_ENGINE_CLAIM_LEDGER;
 - OFF = bajt-parytet (krotka 3-elem. jak przed sprintem; flota niemutowana);
 - claim ledger: wspólne źródło claim_ledger.tentative_assign (zero 2. kopii) +
   INV-LAYER-4 (drugi event tego samego ticku widzi worek doładowany claimem);
 - bramka C3c: PENDING_RESWEEP_LIVE bez geometrii = HOLD (zakodowane).
"""
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import pytest  # noqa: E402

from dispatch_v2 import objm_lexr6  # noqa: E402
from dispatch_v2 import claim_ledger  # noqa: E402


class _Cand:
    def __init__(self, cid, r6=None, committed=0.0, new_late=0.0, spread=None):
        self.courier_id = cid
        self.metrics = {
            "late_pickup_committed_max": committed,
            "new_pickup_late_min": new_late,
        }
        if r6 is not None:
            self.metrics["objm_r6_breach_max_min"] = r6
        if spread is not None:
            self.metrics["deliv_spread_km"] = spread


def _geom_on(monkeypatch, quant=0.0):
    monkeypatch.setattr(
        objm_lexr6.C, "decision_flag",
        lambda name, default=False: name == "ENABLE_LEXQUAL_GEOMETRY_TIEBREAK",
    )
    monkeypatch.setattr(
        objm_lexr6.C, "flag",
        lambda name, default=False: quant if name == "LEXQUAL_TIME_QUANT_MIN" else default,
    )


# ── C2: człon geometrii ──────────────────────────────────────────────────────

def test_geometry_off_tuple_parity():
    """OFF (default) = krotka 3-elem. bajt-identyczna z przed-sprintem — spread ignorowany."""
    c = _Cand("A", r6=12.0, committed=1.0, new_late=2.0, spread=24.0)
    assert objm_lexr6.lex_qual(c) == (12.0, 1.0, 2.0)


def test_geometry_on_appends_last_term(monkeypatch):
    _geom_on(monkeypatch)
    c = _Cand("A", r6=12.0, committed=1.0, new_late=2.0, spread=24.0)
    assert objm_lexr6.lex_qual(c) == (12.0, 1.0, 2.0, 24.0)
    # empty-bag / brak klucza → 0.0 (nie karzemy solo)
    assert objm_lexr6.lex_qual(_Cand("B", r6=12.0, committed=1.0, new_late=2.0)) == \
        (12.0, 1.0, 2.0, 0.0)


def test_geometry_breaks_exact_tie(monkeypatch):
    """ON≠OFF na decyzji: identyczna oś czasowa → ciaśniejszy worek wygrywa TYLKO przy ON."""
    tight = _Cand("TIGHT", r6=5.0, spread=1.2)
    wide = _Cand("WIDE", r6=5.0, spread=18.0)
    pool = [wide, tight]  # wide pierwszy → OFF: stabilny min bierze wide
    assert min(pool, key=objm_lexr6.lex_qual).courier_id == "WIDE"
    _geom_on(monkeypatch)
    assert min(pool, key=objm_lexr6.lex_qual).courier_id == "TIGHT"


def test_geometry_subordinate_to_r6(monkeypatch):
    """INV-LAYER-5: geometria NIE dominuje osi czasowej — gorszy R6 przegrywa
    nawet z fatalnym spreadem po drugiej stronie."""
    _geom_on(monkeypatch)
    better_time = _Cand("TIME", r6=4.0, spread=20.0)
    tighter_geom = _Cand("GEOM", r6=9.0, spread=0.5)
    assert min([tighter_geom, better_time], key=objm_lexr6.lex_qual).courier_id == "TIME"


def test_quantization_merges_near_ties(monkeypatch):
    """quant=0 → 0.4 min różnicy R6 rozstrzyga czasowo; quant=1.0 → kubełki się
    zlewają i geometria decyduje (sens pokrętła pod scarcity)."""
    near_a = _Cand("NEARTIME", r6=10.2, spread=18.0)
    near_b = _Cand("NEARGEOM", r6=10.6, spread=1.0)
    _geom_on(monkeypatch, quant=0.0)
    assert min([near_a, near_b], key=objm_lexr6.lex_qual).courier_id == "NEARTIME"
    _geom_on(monkeypatch, quant=1.0)
    assert min([near_a, near_b], key=objm_lexr6.lex_qual).courier_id == "NEARGEOM"


def test_quantization_inert_without_geometry(monkeypatch):
    """Kwantyzacja NIE działa bez flagi geometrii (pokrętło podrzędne)."""
    monkeypatch.setattr(objm_lexr6.C, "decision_flag", lambda n, d=False: False)
    monkeypatch.setattr(
        objm_lexr6.C, "flag",
        lambda n, d=False: 5.0 if n == "LEXQUAL_TIME_QUANT_MIN" else d,
    )
    c = _Cand("A", r6=10.2, committed=0.0, new_late=0.0, spread=9.0)
    assert objm_lexr6.lex_qual(c) == (10.2, 0.0, 0.0)


def test_quantization_keeps_no_r6_sentinel(monkeypatch):
    """9e9 (brak R6-breach) zostaje poza kwantyzacją — sentinel nietknięty."""
    _geom_on(monkeypatch, quant=5.0)
    c = _Cand("A", r6=None, spread=2.0)
    q = objm_lexr6.lex_qual(c)
    assert q[0] == 9e9 and q[3] == 2.0


# ── C3: claim ledger (wspólne źródło) ────────────────────────────────────────

def test_tentative_assign_no_mutation_and_bag_grows():
    class _CS:
        def __init__(self):
            self.bag = [{"order_id": "1"}]
    fleet = {"447": _CS()}
    out = claim_ledger.tentative_assign(fleet, "447", {"order_id": "2"})
    assert len(fleet["447"].bag) == 1, "wejście NIE może być zmutowane"
    assert len(out["447"].bag) == 2
    assert out["447"].bag[1]["status"] == "assigned"
    # nieznany cid = no-op (kopia floty)
    out2 = claim_ledger.tentative_assign(fleet, "999", {"order_id": "3"})
    assert len(out2) == 1 and len(out2["447"].bag) == 1


def test_resweep_imports_shared_claim_source():
    """Zero 2. kopii: resweep MUSI importować claim z modułu silnika."""
    from dispatch_v2.tools import pending_global_resweep as pgr
    assert pgr._tentative_assign is claim_ledger.tentative_assign
    assert pgr._bag_entry_from_order is claim_ledger.bag_entry_from_order


def test_tick_claim_ledger_on_off(monkeypatch):
    """INV-LAYER-4 wiring: 2 eventy NEW_ORDER w 1 ticku. OFF → flota niemutowana
    (2. event ocenia ten sam worek). ON → 2. event widzi claim zwycięzcy 1."""
    from dispatch_v2 import shadow_dispatcher as SD

    class _CS:
        def __init__(self, cid):
            self.courier_id = cid
            self.bag = []
            self.name = f"K{cid}"

    seen_bag_sizes = []

    class _Best:
        def __init__(self, cid):
            self.courier_id = cid
            self.score = 10.0

    class _Res:
        def __init__(self, cid):
            self.verdict = "PROPOSE"
            self.best = _Best(cid)

    def _fake_process_event(ev, fleet, meta):
        seen_bag_sizes.append(len(fleet["447"].bag or []))
        return _Res("447")

    def _mk_ev(i):
        return {"event_id": f"e{i}", "order_id": f"48{i}",
                "payload": {"order_id": f"48{i}",
                            "pickup_coords": [53.13, 23.16],
                            "delivery_coords": [53.14, 23.17]}}

    events = [_mk_ev(1), _mk_ev(2)]
    monkeypatch.setattr(SD.event_bus, "get_pending",
                        lambda limit=None, event_types=None: list(events))
    monkeypatch.setattr(SD.event_bus, "mark_processed", lambda eid: None)
    monkeypatch.setattr(SD, "dispatchable_fleet", lambda: [_CS("447")])
    monkeypatch.setattr(SD.state_machine, "get_all", lambda: {})
    monkeypatch.setattr(SD, "process_event", _fake_process_event)
    monkeypatch.setattr(SD, "_probe_same_restaurant_race",
                        lambda *a, **k: None)
    monkeypatch.setattr(SD, "_serialize_result",
                        lambda result, eid, latency_ms: {"best": {}})
    monkeypatch.setattr(SD, "_append_shadow_log", lambda *a, **k: None,
                        raising=False)

    def _run(claim_on):
        seen_bag_sizes.clear()
        monkeypatch.setattr(
            SD.C, "decision_flag",
            lambda name, default=False: (
                claim_on if name == "ENABLE_ENGINE_CLAIM_LEDGER" else False))
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            SD._tick(os.path.join(td, "shadow.jsonl"), None)
        return list(seen_bag_sizes)

    assert _run(False) == [0, 0], "OFF: flota niemutowana między eventami"
    assert _run(True) == [0, 1], "ON: 2. event MUSI widzieć claim zwycięzcy 1. eventu"


# ── C3c: bramka LIVE ─────────────────────────────────────────────────────────

def test_live_gate_holds_without_geometry(monkeypatch, caplog):
    from dispatch_v2.tools import pending_global_resweep as pgr
    monkeypatch.setattr(pgr.C, "decision_flag", lambda n, d=False: False)
    with caplog.at_level("WARNING"):
        assert pgr.live_gate_open() is False
    assert any("HOLD" in r.message and "geometrii" in r.message
               for r in caplog.records)


def test_live_gate_opens_with_geometry(monkeypatch):
    from dispatch_v2.tools import pending_global_resweep as pgr
    monkeypatch.setattr(
        pgr.C, "decision_flag",
        lambda n, d=False: n == "ENABLE_LEXQUAL_GEOMETRY_TIEBREAK")
    assert pgr.live_gate_open() is True
