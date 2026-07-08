"""Sprint B (2026-07-08) — INV-FEAS-NO-DOUBLE-BOOK: strażnik spójności claim-ledger.

Kontrakt ② ZIOMEK_INVARIANTS.md l.36: „kurier nie zaproponowany do 2 SPRZECZNYCH
zleceń w 1 ticku (greedy pile-on, K6 — global de-konflikcja)". Mechanizm de-konflikcji
= `claim_ledger.tentative_assign` doklejający zwycięzcę do worka między eventami sweepu/
ticku (korzeń pomiaru: 447 proponowany 127×/32 zlecenia, g_maxpile=7).

INWARIANT: w jednym przebiegu KOLEJNE claimy TEGO SAMEGO kuriera muszą widzieć worek
rosnący DOKŁADNIE o 1. Regres (flota niemutowana / pile-on) → worek nierosnący →
NARUSZENIE. Ten plik pina:
  1. czysty weryfikator `verify_no_stale_claim` (zero-FP na poprawnym śladzie; łapie
     stale i gap) + mutation-probe leksykalny,
  2. WPIĘCIE do `global_allocate` (bliźniak resweep): zero-FP na legalnym bundlingu,
     mutation-probe (neutralizacja `tentative_assign` → pile-on wykryty), flaga ON≡OFF
     co do allocation (strażnik nie reguła), HARD-block raise,
  3. metryka `g_claim_ledger_breaches` dociera do jsonl+summary (measurability),
  4. parytet bliźniaka (`_tick` i `global_allocate` = TEN SAM claim_ledger, single-source).

Reużywa fixtures z `test_pending_global_resweep` (jedno źródło fake'ów, protokół #0 ETAP 3).
"""
import inspect
import json
import sys
import types

import pytest

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import claim_ledger as CL  # noqa: E402
from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2 import shadow_dispatcher as SD  # noqa: E402
from dispatch_v2.tools import pending_global_resweep as PGR  # noqa: E402
from dispatch_v2.tests.test_pending_global_resweep import (  # noqa: E402
    _N, _cand, _cs, _fake_assess, _rec, _result, _setup,
)

_CHECK = "ENABLE_CLAIM_LEDGER_INVARIANT_CHECK"
_HARD = "ENABLE_CLAIM_LEDGER_INVARIANT_HARD"


def _flags(*on):
    """decision_flag patch: zwraca True dla nazw w `on`, False dla reszty."""
    onset = set(on)
    return lambda name: name in onset


class _RecLog:
    """Minimalny logger łapiący .error(...) (do asercji log-loud)."""

    def __init__(self):
        self.errors = []

    def error(self, *a, **k):
        self.errors.append((a, k))


# ═══════════════════════════════════════════════════════════════════════════
# 1. Czysty weryfikator (leaf, zero I/O)
# ═══════════════════════════════════════════════════════════════════════════
def test_verify_empty_and_singletons_clean():
    assert CL.verify_no_stale_claim([]) == []
    # każdy kurier raz → brak par → zero naruszeń
    assert CL.verify_no_stale_claim([("A", "o1", 0), ("B", "o2", 0), ("C", "o3", 2)]) == []


def test_verify_correct_growth_clean():
    """Legalny bundling: worek rośnie o +1 per claim → ZERO naruszeń (nie fałszuje)."""
    trace = [("A", "o1", 0), ("A", "o2", 1), ("A", "o3", 2),   # A pre-empty, rośnie 0→1→2
             ("B", "o4", 3), ("B", "o5", 4)]                    # B pre-bag 3, rośnie 3→4
    assert CL.verify_no_stale_claim(trace) == []


def test_verify_stale_pile_on_detected():
    """Pile-on: A widzi ten sam pusty worek 3× → 2 naruszenia 'stale'."""
    trace = [("A", "o1", 0), ("A", "o2", 0), ("A", "o3", 0)]
    viol = CL.verify_no_stale_claim(trace)
    assert len(viol) == 2
    assert all(v["kind"] == "stale" for v in viol), viol
    assert viol[0]["cid"] == "A" and viol[0]["oid"] == "o2" and viol[0]["seen"] == 0


def test_verify_gap_detected():
    """Worek rośnie o >1 (strukturalnie niemożliwe przy 1 tentative/claim) → 'gap'."""
    viol = CL.verify_no_stale_claim([("A", "o1", 0), ("A", "o2", 2)])
    assert len(viol) == 1 and viol[0]["kind"] == "gap"


def test_verify_interleaved_multi_courier_clean():
    """Przeplot kurierów w sweepie — każdy rośnie o +1 niezależnie → clean."""
    trace = [("A", "o1", 0), ("B", "o2", 0), ("A", "o3", 1), ("B", "o4", 1), ("A", "o5", 2)]
    assert CL.verify_no_stale_claim(trace) == []


def test_verify_mutation_probe_lexical():
    """MUTATION-PROBE weryfikatora: przy poprawnym śladzie ZERO; po degradacji śladu
    (druga wartość zamrożona na 0 = symulacja braku tentative_assign) → naruszenie.
    Gdyby weryfikator był ślepy (zawsze []), oba dałyby [] → probe by nie zabił."""
    good = [("A", "o1", 0), ("A", "o2", 1)]
    bad = [("A", "o1", 0), ("A", "o2", 0)]      # regres: worek nie urósł
    assert CL.verify_no_stale_claim(good) == []
    assert CL.verify_no_stale_claim(bad), "weryfikator ŚLEPY — probe nie zabił"


def test_check_sweep_trace_logs_only_on_violation():
    lg_ok = _RecLog()
    CL.check_sweep_trace([("A", "o1", 0), ("A", "o2", 1)], log=lg_ok, context="t")
    assert lg_ok.errors == [], "log-loud odpalił na CZYSTYM śladzie (fałszywka)"
    lg_bad = _RecLog()
    out = CL.check_sweep_trace([("A", "o1", 0), ("A", "o2", 0)], log=lg_bad, context="t")
    assert out and lg_bad.errors, "brak log-loud na naruszeniu"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Wpięcie do global_allocate (bliźniak resweep)
# ═══════════════════════════════════════════════════════════════════════════
def _assess_bundle(order_event, fleet, now):
    """A jest najlepszy dla o1 I o2 nawet obciążony (kara load mała) → A dostaje OBA
    (legalny bundling). Poprawny sweep: A widzi worek 0 (o1) → 1 (o2) = +1, zero naruszeń."""
    base = {"o1": {"A": 100.0, "B": 10.0}, "o2": {"A": 90.0, "B": 20.0}}
    oid = order_event["order_id"]
    cands = []
    for cid in ("A", "B"):
        cs = fleet.get(cid)
        load = len(cs.bag) if cs is not None else 0
        cands.append(_cand(cid, base[oid][cid] - 5.0 * load))
    return _result(cands, total=2, feasible=2)


def test_global_allocate_clean_bundle_zero_fp(monkeypatch):
    """ZERO-FP: legalny bundling (A dostaje o1+o2) NIE odpala tripwire'a."""
    monkeypatch.setattr(PGR, "_assess", _assess_bundle)
    monkeypatch.setattr(C, "decision_flag", _flags(_CHECK))
    fleet = {c: _cs(c) for c in ("A", "B")}
    diag = {}
    alloc = PGR.global_allocate([("o1", _rec("o1")), ("o2", _rec("o2"))],
                                fleet, _N, _diag_out=diag)
    assert alloc["o1"]["cid"] == "A" and alloc["o2"]["cid"] == "A", alloc
    # ślad: A widział worek 0 (o1) → 1 (o2)
    assert diag["claim_trace"] == [("A", "o1", 0), ("A", "o2", 1)], diag["claim_trace"]
    assert diag["claim_ledger_breaches"] == [], diag["claim_ledger_breaches"]


def test_global_allocate_mutation_probe_pileon(monkeypatch):
    """MUTATION-PROBE wpięcia: neutralizuj `tentative_assign` (flota niemutowana) →
    A wygrywa o1 I o2 widząc TEN SAM pusty worek → tripwire ŁAPIE stale.
    To dokładnie ten bug, przed którym stoi claim-ledger (pile-on)."""
    monkeypatch.setattr(PGR, "_assess", _assess_bundle)
    monkeypatch.setattr(C, "decision_flag", _flags(_CHECK))
    # MUTACJA: tentative_assign = tożsamość (nie doklej do worka)
    monkeypatch.setattr(PGR, "_tentative_assign", lambda fleet, cid, rec: dict(fleet))
    fleet = {c: _cs(c) for c in ("A", "B")}
    diag = {}
    PGR.global_allocate([("o1", _rec("o1")), ("o2", _rec("o2"))],
                        fleet, _N, _diag_out=diag)
    assert diag["claim_trace"] == [("A", "o1", 0), ("A", "o2", 0)], diag["claim_trace"]
    b = diag["claim_ledger_breaches"]
    assert len(b) == 1 and b[0]["kind"] == "stale" and b[0]["cid"] == "A", b


def test_global_allocate_flag_off_equals_on_allocation(monkeypatch):
    """STRAŻNIK NIE REGUŁA: flaga ON vs OFF → IDENTYCZNA allocation (obserwator nie
    zmienia decyzji). Pełny fixture spread (_fake_assess: A/B/C)."""
    monkeypatch.setattr(PGR, "_assess", _fake_assess)
    hanging = [("o1", _rec("o1")), ("o2", _rec("o2")), ("o3", _rec("o3"))]

    monkeypatch.setattr(C, "decision_flag", _flags())  # wszystko OFF
    alloc_off = PGR.global_allocate(hanging, {c: _cs(c) for c in ("A", "B", "C")}, _N)

    monkeypatch.setattr(C, "decision_flag", _flags(_CHECK))  # obserwacja ON
    alloc_on = PGR.global_allocate(hanging, {c: _cs(c) for c in ("A", "B", "C")}, _N)

    assert alloc_off == alloc_on, "obserwator zmienił allocation (to nie strażnik!)"


def test_global_allocate_hard_block_raises(monkeypatch):
    """HARD-block (odłożony za ACK): CHECK+HARD ON + regres → raise AssertionError."""
    monkeypatch.setattr(PGR, "_assess", _assess_bundle)
    monkeypatch.setattr(C, "decision_flag", _flags(_CHECK, _HARD))
    monkeypatch.setattr(PGR, "_tentative_assign", lambda fleet, cid, rec: dict(fleet))
    with pytest.raises(AssertionError, match="NO-DOUBLE-BOOK"):
        PGR.global_allocate([("o1", _rec("o1")), ("o2", _rec("o2"))],
                            {c: _cs(c) for c in ("A", "B")}, _N)


def test_global_allocate_check_off_no_raise_even_on_regress(monkeypatch):
    """HARD bez CHECK = brak weryfikacji → NIE rzuca (obserwacja gated CHECKiem)."""
    monkeypatch.setattr(PGR, "_assess", _assess_bundle)
    monkeypatch.setattr(C, "decision_flag", _flags(_HARD))  # HARD ON, CHECK OFF
    monkeypatch.setattr(PGR, "_tentative_assign", lambda fleet, cid, rec: dict(fleet))
    diag = {}
    PGR.global_allocate([("o1", _rec("o1")), ("o2", _rec("o2"))],
                        {c: _cs(c) for c in ("A", "B")}, _N, _diag_out=diag)
    assert diag["claim_ledger_breaches"] == []  # nie weryfikował → brak wpisu


# ═══════════════════════════════════════════════════════════════════════════
# 3. Metryka dociera do jsonl + summary (measurability, ETAP 4)
# ═══════════════════════════════════════════════════════════════════════════
def test_run_once_emits_breach_metric_clean(tmp_path, monkeypatch):
    """Czysty sweep + CHECK ON → g_claim_ledger_breaches == 0 w jsonl i summary."""
    out = _setup(tmp_path, monkeypatch, {"o1": "A", "o2": "B", "o3": "C"})
    monkeypatch.setattr(C, "decision_flag", _flags(_CHECK))
    s = PGR.run_once(now=_N)
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert rows and all(r["g_claim_ledger_breaches"] == 0 for r in rows), rows
    assert s["claim_ledger_breaches"] == 0


def test_run_once_emits_breach_metric_on_pileon(tmp_path, monkeypatch):
    """Regres (neutralizacja tentative_assign) → pile-on → metryka >0 w jsonl+summary."""
    out = _setup(tmp_path, monkeypatch, {"o1": "A", "o2": "A", "o3": "A"})
    monkeypatch.setattr(C, "decision_flag", _flags(_CHECK))
    monkeypatch.setattr(PGR, "_tentative_assign", lambda fleet, cid, rec: dict(fleet))
    s = PGR.run_once(now=_N)
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert s["claim_ledger_breaches"] > 0, s
    assert all(r["g_claim_ledger_breaches"] == s["claim_ledger_breaches"] for r in rows), rows


def test_run_once_metric_present_when_flag_off(tmp_path, monkeypatch):
    """Flaga OFF: pole obecne = 0 (brak weryfikacji), allocation nietknięta."""
    out = _setup(tmp_path, monkeypatch, {"o1": "A", "o2": "A"})
    monkeypatch.setattr(C, "decision_flag", _flags())  # OFF
    PGR.run_once(now=_N)
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert rows and all(r["g_claim_ledger_breaches"] == 0 for r in rows), rows


# ═══════════════════════════════════════════════════════════════════════════
# 4. Parytet bliźniaków (single-source claim_ledger)
# ═══════════════════════════════════════════════════════════════════════════
def test_twin_single_source_claim_ledger():
    """Oba bliźniaki (global_allocate resweep + _tick shadow) używają TEGO SAMEGO
    `claim_ledger.tentative_assign` — nie 2. kopii → de-konflikcja nie rozjedzie się."""
    ga_src = inspect.getsource(PGR)
    tick_src = inspect.getsource(SD._tick)
    assert "claim_ledger" in ga_src and "tentative_assign" in ga_src, "resweep nie używa claim_ledger"
    assert "tentative_assign" in tick_src, "_tick nie używa claim_ledger.tentative_assign"


def test_twin_both_call_invariant_checker():
    """Oba bliźniaki wołają `check_sweep_trace` (ten sam strażnik INV-FEAS-NO-DOUBLE-BOOK)."""
    ga_src = inspect.getsource(PGR)
    tick_src = inspect.getsource(SD._tick)
    assert "check_sweep_trace" in ga_src, "global_allocate nie strzeże claim-ledger"
    assert "check_sweep_trace" in tick_src, "_tick nie strzeże claim-ledger (bliźniak nieuzbrojony)"


def test_flags_registered_in_etap4():
    """Obie flagi w rejestrze decyzyjnym (fingerprint je widzi; higiena flag)."""
    assert _CHECK in C.ETAP4_DECISION_FLAGS
    assert _HARD in C.ETAP4_DECISION_FLAGS
    assert C.decision_flag(_CHECK) is False  # default OFF (nie w flags.json)
    assert C.decision_flag(_HARD) is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-rx"]))
