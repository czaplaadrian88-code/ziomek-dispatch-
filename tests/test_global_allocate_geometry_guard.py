"""A1-INVARIANTS (Sprint 1 Z2, 2026-07-05) — de-VOID `global_allocate` geometria.

VOID (oracle Fazy 1, 30.06): przyrząd de-pile CERTYFIKOWAŁ liczbę (alokacje,
score), będąc ŚLEPYM na geometrię — 35% worków po de-pile miało spread>8km
(łamie R1), a werdykt `would_repropose` tego nie widział. Konsekwencja
zakodowana w L6.C (04.07): flip `PENDING_RESWEEP_LIVE` bez geometrii w
selekcji = bramka `live_gate_open()` HOLD.

Ten plik przypina de-VOID na trzech osiach (mutation-probe ×2 w dowodzie
Z2 — `eod_drafts/2026-07-05/A1_INVARIANTS_devoid_dowod.md`):
1. CERTYFIKATOR NIE-ŚLEPY: każdy rekord alokacji niesie pola geometrii
   z metryk silnika (spread/km/r6/cos) — nie da się ich po cichu uciąć.
2. GEOMETRIA DOCIERA DO WERDYKT-LOGU: wiersze jsonl mają new_deliv_spread_km
   + g_spread_improved (podstawa pomiarowa dla bramki PENDING_RESWEEP_LIVE).
3. OSIĄGALNOŚĆ BRAMKI (C5/#18): jedyna ścieżka LIVE w run_once KONSULTUJE
   `live_gate_open()` zanim cokolwiek zrobi; gate zamknięty => zero akcji live.
   (Semantyka samej bramki ON≠OFF: `test_l6c_geometry_claim` l.202-212.)

Reużywa fixtures z `test_pending_global_resweep` (jedno źródło fake'ów,
zero 2. kopii — protokół #0 ETAP 3).
"""
import json
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2.tools import pending_global_resweep as PGR  # noqa: E402
from dispatch_v2.tests.test_pending_global_resweep import (  # noqa: E402
    _N, _cs, _fake_assess, _rec, _setup,
)


def test_allocation_records_carry_geometry(monkeypatch):
    """Kontrakt ⑤: rekord alokacji MUSI nieść geometrię z metryk silnika.

    To jest sedno de-VOID: certyfikator, który gubi spread/km/r6, znów
    certyfikuje samą liczbę. Wartości = dokładnie te z metrics kandydata."""
    monkeypatch.setattr(PGR, "_assess", _fake_assess)
    fleet = {c: _cs(c) for c in ("A", "B", "C")}
    alloc = PGR.global_allocate(
        [("o1", _rec("o1")), ("o2", _rec("o2")), ("o3", _rec("o3"))], fleet, _N)
    for oid, a in alloc.items():
        assert a.get("no_courier") is False, a
        # fake engine daje: km=1.0, r6=20.0, cos=0.1, spread=3.0
        assert a.get("km") == 1.0, f"{oid}: km_to_pickup zgubiony: {a}"
        assert a.get("r6") == 20.0, f"{oid}: r6_max_bag_time_min zgubiony: {a}"
        assert a.get("cos") == 0.1, f"{oid}: r1_new_drop_cosine zgubiony: {a}"
        assert a.get("spread") == 3.0, f"{oid}: deliv_spread_km zgubiony: {a}"


def test_jsonl_rows_carry_geometry_and_global_spread(tmp_path, monkeypatch):
    """Geometria dociera do werdykt-logu (jsonl), nie tylko do dict-u w RAM:
    per-wiersz new_deliv_spread_km/new_km_to_pickup/new_r6_min + globalne
    g_spread_improved/g_maxpile_* — to jest podstawa pomiarowa bramki
    PENDING_RESWEEP_LIVE (bez tego znów certyfikujemy liczbę)."""
    out = _setup(tmp_path, monkeypatch, {"o1": "A", "o2": "A", "o3": "A"})
    PGR.run_once(now=_N)
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert rows, "sweep nie zapisał wierszy"
    for r in rows:
        assert r.get("new_deliv_spread_km") == 3.0, r
        assert r.get("new_km_to_pickup") == 1.0, r
        assert r.get("new_r6_min") == 20.0, r
        assert "g_spread_improved" in r and "g_maxpile_before" in r, r


def test_geometry_only_improvement_fires_verdict(tmp_path, monkeypatch):
    """Werdykt `would_repropose` MUSI widzieć czystą poprawę GEOMETRII
    (rozbicie pile-on), gdy score NIE daje pretekstu (delta < margin).

    Probe G2 na `test_run_once_spread_fix` pokazał, że tamten fixture nie
    izoluje geometrii (better_now też True) — ten test to domyka:
    po alokacji o1→A obciążony A (85−6=79) przegrywa z B (80) o LEDWIE
    1 pkt (< margin 15 ⇒ better_now=False), więc jedyny powód przerzutu
    = rozjazd kierunków (maxpile 2→1). Mutacja
    `would = changed and better_now` (ślepa na spread_improved) = FAIL."""
    import types

    base = {"o1": {"A": 100.0, "B": 10.0}, "o2": {"A": 85.0, "B": 80.0}}

    def _assess_small_pen(order_event, fleet, now):
        oid = order_event["order_id"]
        cands = []
        for cid in ("A", "B"):
            cs = fleet.get(cid)
            load = len(cs.bag) if cs is not None else 0
            cands.append(types.SimpleNamespace(
                courier_id=cid, score=base[oid][cid] - 6.0 * load, name=cid,
                feasibility_verdict="MAYBE",
                metrics={"km_to_pickup": 1.0, "r6_max_bag_time_min": 20.0,
                         "r1_new_drop_cosine": 0.1, "deliv_spread_km": 3.0}))
        cands.sort(key=lambda c: -c.score)
        return types.SimpleNamespace(
            best=cands[0], candidates=cands, verdict="PROPOSE",
            pool_total_count=2, pool_feasible_count=2)

    out = _setup(tmp_path, monkeypatch, {"o1": "A", "o2": "A"})
    monkeypatch.setattr(PGR, "_assess", _assess_small_pen)
    monkeypatch.setattr(PGR.CR, "dispatchable_fleet",
                        lambda: [_cs("A"), _cs("B")])
    s = PGR.run_once(now=_N)
    rows = {r["order_id"]: r for r in
            (json.loads(l) for l in out.read_text().splitlines())}
    r2 = rows["o2"]
    assert r2["new_cid"] == "B", f"fixture: o2 miał przejść do B, jest {r2}"
    assert r2["delta_vs_now"] is not None and r2["delta_vs_now"] < 15.0, r2
    assert s["spread_improved"] is True, s
    assert r2["would_repropose"] is True and r2["reason"] == "rozjazd_kierunkow", (
        f"werdykt ślepy na geometrię — czysta poprawa spreadu nie odpala: {r2}")


def test_live_path_consults_geometry_gate(tmp_path, monkeypatch):
    """Osiągalność bramki (C5/#18): FLAG_LIVE=ON → run_once WOŁA live_gate_open()
    i przy zamkniętej bramce nie wykonuje ŻADNEJ akcji live (live_acted=0).
    Łapie klasę „gate istnieje, ale przyszły konsument go ominął"."""
    _setup(tmp_path, monkeypatch, {"o1": "A"})
    monkeypatch.setattr(
        C, "flag",
        lambda n, d=False: True if n in (PGR.FLAG, PGR.FLAG_LIVE) else d)
    calls = []

    def _spy_gate():
        calls.append(1)
        return False  # geometria OFF → HOLD

    monkeypatch.setattr(PGR, "live_gate_open", _spy_gate)
    s = PGR.run_once(now=_N)
    assert calls, ("PENDING_RESWEEP_LIVE=ON a live_gate_open() NIE został "
                   "skonsultowany — ścieżka live ominęła bramkę L6.C")
    assert s.get("live_acted") == 0, s


def test_gate_closed_by_default_blocks_live_flip():
    """Domyślna konfiguracja (geometria OFF) = bramka ZAMKNIĘTA. Utrwala
    „VOID global_allocate MUSI blokować flip PENDING_RESWEEP_LIVE" jako
    wykonywalny fakt, nie notatkę."""
    import unittest.mock as mock
    with mock.patch.object(C, "decision_flag", lambda n, d=False: False):
        assert PGR.live_gate_open() is False
