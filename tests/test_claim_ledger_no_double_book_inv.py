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
     co do allocation (strażnik nie reguła), HARD drop bez zatrzymania sweepu,
  3. metryki breach/drop docierają do jsonl+summary (measurability),
  4. parytet bliźniaka (`_tick` i `global_allocate` = TEN SAM drop gate, single-source).

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


def test_check_feral_claim_uses_same_oracle_and_logs_drop():
    """Werdykt per-claim dzieli oracle z tripwire'em i sam nigdy nie rzuca."""
    accepted = [("A", "o1", 0), ("B", "o2", 0)]
    assert CL.check_feral_claim(accepted, ("A", "o3", 1)) == []
    lg = _RecLog()
    viol = CL.check_feral_claim(
        accepted, ("A", "o3", 0), log=lg, context="test")
    assert len(viol) == 1 and viol[0]["kind"] == "stale"
    assert lg.errors and "DROP_FERAL_CLAIM" in lg.errors[0][0][0]


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


def _assess_drop_continue(order_event, fleet, now):
    """o1/o2 claimują A, niezależne o3 claimuje B; score ustala kolejność."""
    base = {
        "o1": {"A": 100.0, "B": 10.0},
        "o2": {"A": 90.0, "B": 20.0},
        "o3": {"A": 10.0, "B": 80.0},
    }
    oid = order_event["order_id"]
    return _result([_cand(cid, score) for cid, score in base[oid].items()],
                   total=2, feasible=2)


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
    """HARD OFF: nawet wykryty feral ma bajtowo dzisiejsze zachowanie CHECK-only."""
    monkeypatch.setattr(PGR, "_assess", _assess_bundle)
    monkeypatch.setattr(PGR, "_tentative_assign", lambda fleet, cid, rec: dict(fleet))
    hanging = [("o1", _rec("o1")), ("o2", _rec("o2"))]

    monkeypatch.setattr(C, "decision_flag", _flags())  # wszystko OFF
    alloc_off = PGR.global_allocate(hanging, {c: _cs(c) for c in ("A", "B")}, _N)

    monkeypatch.setattr(C, "decision_flag", _flags(_CHECK))  # obserwacja ON
    alloc_on = PGR.global_allocate(hanging, {c: _cs(c) for c in ("A", "B")}, _N)

    expected = {
        "o1": {
            "cid": "A", "name": "A", "score": 100.0,
            "feasibility": "MAYBE", "km": 1.0, "r6": 20.0,
            "cos": 0.1, "spread": 3.0,
            "cand_scores": {"A": 100.0, "B": 10.0},
            "pool_total": 2, "pool_feasible": 2, "no_courier": False,
        },
        "o2": {
            "cid": "A", "name": "A", "score": 90.0,
            "feasibility": "MAYBE", "km": 1.0, "r6": 20.0,
            "cos": 0.1, "spread": 3.0,
            "cand_scores": {"A": 90.0, "B": 20.0},
            "pool_total": 2, "pool_feasible": 2, "no_courier": False,
        },
    }
    assert alloc_off == expected  # golden kształtu sprzed DROP-FERAL-CLAIM
    assert json.dumps(alloc_off, sort_keys=True) == json.dumps(alloc_on, sort_keys=True)
    assert alloc_on["o2"]["cid"] == "A"  # breach obserwowany, lecz HARD nadal OFF


def test_global_allocate_hard_drops_feral_and_continues(monkeypatch):
    """HARD ON: tylko o2 drop; o1/o3 identyczne jak OFF, sweep dochodzi do końca."""
    monkeypatch.setattr(PGR, "_assess", _assess_drop_continue)
    hanging = [(oid, _rec(oid)) for oid in ("o1", "o2", "o3")]
    # Wymuszony regres źródłowy: claim nie doładowuje floty, więc drugi A jest feralny.
    monkeypatch.setattr(PGR, "_tentative_assign", lambda fleet, cid, rec: dict(fleet))

    monkeypatch.setattr(C, "decision_flag", _flags(_CHECK))
    alloc_off = PGR.global_allocate(
        hanging, {c: _cs(c) for c in ("A", "B")}, _N)

    monkeypatch.setattr(C, "decision_flag", _flags(_CHECK, _HARD))
    diag = {}
    results = {}
    alloc_hard = PGR.global_allocate(
        hanging, {c: _cs(c) for c in ("A", "B")}, _N,
        _results_out=results, _diag_out=diag)

    assert alloc_hard["o2"]["feral_claim_dropped"] is True
    assert alloc_hard["o2"]["cid"] is None
    assert alloc_hard["o1"] == alloc_off["o1"]
    assert alloc_hard["o3"] == alloc_off["o3"]  # reszta alokacji nietknięta
    assert alloc_hard["o3"]["cid"] == "B"       # dowód: sweep nie zatrzymał się
    assert len(diag["claim_ledger_feral_drops"]) == 1
    assert len(diag["claim_ledger_breaches"]) == 1
    assert set(results) == {"o1", "o3"}  # feral nie wycieka do konsoli/live


def test_global_allocate_drop_mutation_probe_turns_oracle_red(monkeypatch):
    """MUTATION-PROBE: neutralizacja wspólnej bramki dropu zabija asercję ochronną."""
    monkeypatch.setattr(PGR, "_assess", _assess_bundle)
    monkeypatch.setattr(C, "decision_flag", _flags(_CHECK, _HARD))
    monkeypatch.setattr(PGR, "_tentative_assign", lambda fleet, cid, rec: dict(fleet))
    monkeypatch.setattr(PGR, "_check_feral_claim", lambda *a, **k: [])
    alloc = PGR.global_allocate(
        [("o1", _rec("o1")), ("o2", _rec("o2"))],
        {c: _cs(c) for c in ("A", "B")}, _N)
    with pytest.raises(AssertionError, match="drop gate neutralized"):
        assert alloc["o2"].get("feral_claim_dropped"), "drop gate neutralized"


def test_global_allocate_check_off_no_raise_even_on_regress(monkeypatch):
    """HARD bez CHECK = brak weryfikacji i dropu (bramka zależna od CHECK)."""
    monkeypatch.setattr(PGR, "_assess", _assess_bundle)
    monkeypatch.setattr(C, "decision_flag", _flags(_HARD))  # HARD ON, CHECK OFF
    monkeypatch.setattr(PGR, "_tentative_assign", lambda fleet, cid, rec: dict(fleet))
    diag = {}
    PGR.global_allocate([("o1", _rec("o1")), ("o2", _rec("o2"))],
                        {c: _cs(c) for c in ("A", "B")}, _N, _diag_out=diag)
    assert diag["claim_ledger_breaches"] == []  # nie weryfikował → brak wpisu
    assert "claim_ledger_feral_drops" not in diag


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


def test_run_once_hard_drop_metric_reaches_jsonl_and_summary(tmp_path, monkeypatch):
    """HARD ON: suma inkrementów dropu w JSONL = licznik summary = 1."""
    out = _setup(tmp_path, monkeypatch, {"o1": "A", "o2": "A"})
    monkeypatch.setattr(PGR, "_assess", _assess_bundle)
    monkeypatch.setattr(C, "decision_flag", _flags(_CHECK, _HARD))
    monkeypatch.setattr(PGR, "_tentative_assign", lambda fleet, cid, rec: dict(fleet))
    summary = PGR.run_once(now=_N)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert summary["claim_ledger_feral_drops"] == 1
    assert summary["spread_improved"] is False  # sam drop nie zmienia reszty decyzji
    assert sum(row["g_claim_ledger_feral_drops"] for row in rows) == 1
    assert sum(row["feral_claim_dropped"] for row in rows) == 1
    dropped = next(row for row in rows if row["feral_claim_dropped"])
    assert dropped["reason"] == "drop_feral_claim"
    assert dropped["would_repropose"] is False


def test_run_once_hard_off_has_byte_compatible_metric_shape(tmp_path, monkeypatch):
    """HARD OFF nie dodaje nowego pola do dzisiejszego jsonl ani summary."""
    out = _setup(tmp_path, monkeypatch, {"o1": "A", "o2": "A"})
    monkeypatch.setattr(PGR, "_assess", _assess_bundle)
    monkeypatch.setattr(C, "decision_flag", _flags(_CHECK))
    monkeypatch.setattr(PGR, "_tentative_assign", lambda fleet, cid, rec: dict(fleet))
    summary = PGR.run_once(now=_N)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert "claim_ledger_feral_drops" not in summary
    assert all("g_claim_ledger_feral_drops" not in row for row in rows)


# ═══════════════════════════════════════════════════════════════════════════
# 4. Bliźniak shadow._tick: ten sam drop, tick kontynuuje
# ═══════════════════════════════════════════════════════════════════════════
def _run_shadow_drop_case(tmp_path, monkeypatch, *, hard):
    from dispatch_v2 import auto_assign_executor as AAE
    from dispatch_v2 import live_eta_cache as LEC
    from dispatch_v2 import pending_proposals_store as PPS

    class _CS:
        def __init__(self, cid):
            self.courier_id = cid
            self.bag = []
            self.name = cid

    class _Best:
        def __init__(self, cid):
            self.courier_id = cid
            self.name = cid
            self.score = 10.0

    class _Res:
        def __init__(self, cid):
            self.verdict = "PROPOSE"
            self.best = _Best(cid)
            self.would_auto_assign = False

    cid_by_oid = {"o1": "A", "o2": "A", "o3": "A", "o4": "B"}
    events = [
        {
            "event_id": f"e{i}", "order_id": oid,
            "payload": {
                "order_id": oid,
                "pickup_coords": [53.13, 23.16],
                "delivery_coords": [53.14, 23.17],
            },
        }
        for i, oid in enumerate(("o1", "o2", "o3", "o4"), 1)
    ]
    records = []
    processed = []
    pending_oids = []
    auto_verdicts = []
    eta_cids = []
    monkeypatch.setattr(
        SD.event_bus, "get_pending",
        lambda limit=None, event_types=None: list(events))
    monkeypatch.setattr(
        SD.event_bus, "mark_processed",
        lambda eid: processed.append(eid) or True)
    monkeypatch.setattr(SD.event_bus, "mark_failed", lambda *a, **k: None)
    monkeypatch.setattr(SD, "dispatchable_fleet", lambda: [_CS("A"), _CS("B")])
    monkeypatch.setattr(SD.state_machine, "get_all", lambda: {})
    monkeypatch.setattr(
        SD, "process_event",
        lambda ev, fleet, meta, now=None: _Res(cid_by_oid[ev["order_id"]]))
    monkeypatch.setattr(SD, "_probe_same_restaurant_race", lambda *a, **k: None)
    monkeypatch.setattr(
        SD, "_always_propose_would_redirect_shadow", lambda *a, **k: None)
    monkeypatch.setattr(
        SD, "_serialize_result",
        lambda result, eid, latency_ms: {
            "order_id": eid,
            "verdict": "PROPOSE",
            "reason": "PROPOSE",
            "best": {
                "courier_id": result.best.courier_id,
                "name": result.best.name,
                "plan": {
                    "predicted_delivered_at": {"x": "2026-07-20T12:00:00Z"},
                },
            },
            "latency_ms": 0.0,
        })
    monkeypatch.setattr(
        SD, "_append_decision", lambda path, record: records.append(dict(record)))
    enabled = {"ENABLE_ENGINE_CLAIM_LEDGER", _CHECK}
    if hard:
        enabled.add(_HARD)
    monkeypatch.setattr(C, "decision_flag", _flags(*enabled))
    monkeypatch.setattr(
        C, "flag",
        lambda name, default=False: (
            True if name == "ENABLE_PENDING_PROPOSALS_WRITE" else default))
    monkeypatch.setattr(
        PPS, "upsert_proposals",
        lambda upserts, now: pending_oids.extend(oid for oid, _ in upserts)
        or len(upserts))
    monkeypatch.setattr(
        AAE, "maybe_execute",
        lambda record, result, payload: auto_verdicts.append(record["verdict"]))
    monkeypatch.setattr(
        LEC, "upsert",
        lambda **kwargs: eta_cids.append(kwargs.get("courier_id")))
    # Wymuszony mutant źródła: drugi A widzi ten sam worek co pierwszy.
    monkeypatch.setattr(CL, "tentative_assign", lambda fleet, cid, rec: dict(fleet))

    stats = SD._tick(str(tmp_path / "shadow.jsonl"), None)
    effects = {
        "pending_oids": pending_oids,
        "auto_verdicts": auto_verdicts,
        "eta_cids": eta_cids,
    }
    return stats, records, processed, effects


def test_shadow_tick_hard_off_is_byte_compatible(tmp_path, monkeypatch):
    stats, records, processed, effects = _run_shadow_drop_case(
        tmp_path, monkeypatch, hard=False)
    assert stats == {"processed": 4, "failed": 0, "skipped": 0}
    assert [record["verdict"] for record in records] == [
        "PROPOSE", "PROPOSE", "PROPOSE", "PROPOSE"]
    assert processed == ["e1", "e2", "e3", "e4"]
    assert all("g_claim_ledger_feral_drops" not in record for record in records)
    assert effects["pending_oids"] == ["o1", "o2", "o3", "o4"]
    assert effects["auto_verdicts"] == [
        "PROPOSE", "PROPOSE", "PROPOSE", "PROPOSE"]
    assert effects["eta_cids"] == ["A", "A", "A", "B"]


def test_shadow_tick_hard_drops_feral_and_continues(tmp_path, monkeypatch):
    stats, records, processed, effects = _run_shadow_drop_case(
        tmp_path, monkeypatch, hard=True)
    assert stats["processed"] == 4 and stats["failed"] == 0
    assert stats["claim_ledger_feral_drops"] == 2
    assert stats["claim_ledger_breaches"] == 2
    assert [record["verdict"] for record in records] == [
        "PROPOSE", "DROP_FERAL_CLAIM", "DROP_FERAL_CLAIM", "PROPOSE"]
    assert records[1]["g_claim_ledger_feral_drops"] == 1
    assert records[1]["claim_ledger_feral_drop"] is True
    assert sum(r["g_claim_ledger_feral_drops"] for r in records) == 2
    assert processed == ["e1", "e2", "e3", "e4"]  # drop nie przerwał ticku
    assert effects["pending_oids"] == ["o1", "o4"]
    assert effects["auto_verdicts"] == ["PROPOSE", "PROPOSE"]
    assert effects["eta_cids"] == ["A", "B"]
    totals = {"processed": 0, "failed": 0, "skipped": 0}
    SD._accumulate_tick_stats(totals, stats)
    assert totals["claim_ledger_feral_drops"] == 2
    assert totals["claim_ledger_breaches"] == 2


# ═══════════════════════════════════════════════════════════════════════════
# 5. Parytet bliźniaków (single-source claim_ledger)
# ═══════════════════════════════════════════════════════════════════════════
def test_twin_single_source_claim_ledger():
    """Oba bliźniaki (global_allocate resweep + _tick shadow) używają TEGO SAMEGO
    `claim_ledger.tentative_assign` — nie 2. kopii → de-konflikcja nie rozjedzie się."""
    ga_src = inspect.getsource(PGR)
    tick_src = inspect.getsource(SD._tick)
    assert "claim_ledger" in ga_src and "tentative_assign" in ga_src, "resweep nie używa claim_ledger"
    assert "tentative_assign" in tick_src, "_tick nie używa claim_ledger.tentative_assign"


def test_twin_both_call_invariant_checker():
    """Oba bliźniaki wołają TEN SAM per-claim drop i nie mają produkcyjnego raise."""
    ga_src = inspect.getsource(PGR.global_allocate)
    tick_src = inspect.getsource(SD._tick)
    assert "check_feral_claim" in ga_src, "global_allocate bez wspólnego drop gate"
    assert "check_feral_claim" in tick_src, "_tick bez wspólnego drop gate"
    assert "raise AssertionError" not in ga_src
    assert "raise AssertionError" not in tick_src


def test_flags_registered_in_etap4():
    """Obie flagi w rejestrze decyzyjnym (fingerprint je widzi; higiena flag)."""
    assert _CHECK in C.ETAP4_DECISION_FLAGS
    assert _HARD in C.ETAP4_DECISION_FLAGS
    assert C.decision_flag(_CHECK) is False  # default OFF (nie w flags.json)
    assert C.decision_flag(_HARD) is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-rx"]))
