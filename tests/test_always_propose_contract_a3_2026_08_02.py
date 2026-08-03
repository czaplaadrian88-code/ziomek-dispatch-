"""A-3 ALWAYS-PROPOSE (OD-1, 2026-08-02) — kontrakt TYPU WYJŚCIA + inwariant
„nigdy ciche nic". Bramka testowa Przykazania #0 ETAP 4:

  • ORACLE      — feasible→EXECUTABLE_PROPOSAL; tier-3 bez planu→LEAST_DAMAGE_ALERT
                  (jawny best-of-worst); owner-override→OWNER_EXCEPTION; eskalacje
                  operacyjne (no_solo/stale/geometry/commit) = JAWNE, NIE „ciche nic";
                  hold/geokod poza kontraktem. NEGATYWNY oracle: KOORD best=None +
                  reason pusty = „ciche nic" WYKRYTE (repro defektu).
  • MUTATION    — usunięcie klasyfikacji always-propose → „ciche nic" wraca → oracle RED.
  • EFFECT ON≠OFF — serializer shadow emituje pola TYLKO przy ENABLE_ALWAYS_PROPOSE=ON
                  (OFF = bajt-parytet baseline, brak kluczy).
  • CONSUMER PARITY — konsumenci nowego typu (telegram/least-damage) nie wywracają się
                  na rekordzie z dodatkowymi polami; istniejące zachowanie bez zmian.
  • RATCHET     — flaga w ETAP4_DECISION_FLAGS (conftest strip) + w rejestrze lifecycle;
                  każda znana bramka KOORD dostaje etykietę (nowa bramka bez etykiety = RED).

Klasyfikator (core.proposal_output) jest CZYSTĄ funkcją — testy nie dotykają decyzji.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import json
import pathlib

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import shadow_dispatcher
from dispatch_v2.core import proposal_output as po

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
_REGISTRY = pathlib.Path(__file__).resolve().parents[1] / "tools" / "flag_lifecycle_registry.json"


def _candidate(cid="c1", score=50.0, best_effort=False):
    return SimpleNamespace(
        courier_id=cid, name="C", score=score, plan=None,
        feasibility_verdict="MAYBE", feasibility_reason="ok",
        best_effort=best_effort, metrics={"pos_source": "gps"},
    )


def _r(verdict, reason, *, best="__default__", owner_exception=False):
    """Minimalny duck-typed wynik dla klasyfikatora."""
    if best == "__default__":
        best = _candidate()
    ns = SimpleNamespace(verdict=verdict, reason=reason, best=best)
    if owner_exception:
        ns.owner_exception = True
    return ns


def _full_result(verdict, reason, best):
    """Pełniejszy wynik dla realnego _serialize_result (mirror wzorca z09)."""
    return SimpleNamespace(
        order_id="471000", restaurant="R", delivery_address="A",
        verdict=verdict, reason=reason, best=best, candidates=[best] if best else [],
        pickup_ready_at=NOW,
    )


# ── ORACLE — 3 typy owner-facing ─────────────────────────────────────────────
def test_feasible_propose_is_executable_proposal():
    r = _r("PROPOSE", "feasible=3 best=c1")
    assert po.classify(r) == po.EXECUTABLE_PROPOSAL
    assert po.output_label(r) == po.EXECUTABLE_PROPOSAL
    assert po.never_silent_nothing(r)
    assert po.is_silent_nothing(r) is False


def test_best_effort_propose_is_still_executable_proposal():
    # ALWAYS-PROPOSE-ON-SATURATION: best_effort PROPOSE = wciąż wykonalna propozycja.
    r = _r("PROPOSE", "best_effort (0 feasible, r6_violations=0, legacy_sla_v=0)",
           best=_candidate(best_effort=True))
    assert po.classify(r) == po.EXECUTABLE_PROPOSAL


def test_hard35_least_damage_is_least_damage_alert():
    # Tier-3 (Alarm): 0 planów w capie → jawny best-of-worst dla koordynatora.
    r = _r("KOORD", "hard35_least_damage_alert (cap=35; 0 within cap; visible_cid=c37)",
           best=_candidate(cid="c37", best_effort=True))
    assert po.is_least_damage_alert(r) is True
    assert po.classify(r) == po.LEAST_DAMAGE_ALERT
    assert po.never_silent_nothing(r)


def test_owner_exception_by_attr_and_by_reason_code():
    assert po.classify(_r("PROPOSE", "x", owner_exception=True)) == po.OWNER_EXCEPTION
    assert po.classify(_r("KOORD", "owner_exception:manual_override (ack=...)")) == po.OWNER_EXCEPTION


def test_classify_precedence_owner_gt_leastdamage_gt_propose():
    # owner-exception bije least-damage bije PROPOSE (kolejność = kontrakt).
    r = _r("KOORD", "hard35_least_damage_alert (cap=35)", owner_exception=True)
    assert po.classify(r) == po.OWNER_EXCEPTION
    r2 = _r("PROPOSE", "hard35_least_damage_alert (cap=35)")  # verdict PROPOSE, marker w reason
    # KOORD-marker nie odpala przy PROPOSE (is_least_damage_alert wymaga KOORD) → EXECUTABLE.
    assert po.classify(r2) == po.EXECUTABLE_PROPOSAL


def test_owner_types_are_exactly_the_three():
    assert po.OWNER_TYPES == (
        po.EXECUTABLE_PROPOSAL, po.LEAST_DAMAGE_ALERT, po.OWNER_EXCEPTION)


# ── ORACLE — eskalacje operacyjne = JAWNE, NIE „ciche nic" ────────────────────
def test_no_solo_empty_pool_is_escalation_not_silent():
    # Pusta pula: nikt niedyspozycjonowalny nawet solo → best=None, ale JAWNY reason.
    r = _r("KOORD", "no_solo_candidates (fleet_n=5) — wszyscy odrzuceni nawet solo",
           best=None)
    assert po.classify(r) is None                     # nie jest owner-propozycją
    assert po.is_coordinator_escalation(r) is True
    assert po.output_label(r) == po.COORDINATOR_ESCALATION
    assert po.is_silent_nothing(r) is False           # KLUCZOWE: NIE „ciche nic"
    assert po.never_silent_nothing(r)


@pytest.mark.parametrize("reason", [
    "state_likely_stale (panel_packs_age=90.0s, n_stale_signal=3; pool=4)",
    "geometry_blind_fallback (all 3 kandydaci strategy=greedy_fallback + cos<0; escalate)",
    "commit_divergence_gate (best=c1 worst_oid=9 divergence=16.0min > 10min threshold)",
    "difficult_geometry_redirect (best=c1 max_score=-40.0; floor=-30; geometryczny eskalator KOORD)",
])
def test_operational_koords_are_escalation_not_silent(reason):
    r = _r("KOORD", reason)
    assert po.output_label(r) == po.COORDINATOR_ESCALATION
    assert po.never_silent_nothing(r)


# ── ORACLE — poza kontraktem (hold / niedyspozycjonowalne / obserwacja) ───────
def test_early_bird_hold_not_requires_decision():
    r = _r("KOORD", "early_bird (pickup_at 90min ahead) hold KOORD", best=None)
    assert po.requires_decision(r) is False
    assert po.is_silent_nothing(r) is False


def test_skip_and_observe_not_requires_decision():
    assert po.requires_decision(_r("SKIP", "geocode_reject outside bbox", best=None)) is False
    assert po.requires_decision(_r("OBSERVE", "czasowka_reclaim_shadow")) is False
    assert po.is_silent_nothing(_r("SKIP", "geocode_reject", best=None)) is False


# ── NEGATYWNY ORACLE — „ciche nic" MUSI być wykryte ──────────────────────────
def test_genuine_silent_nothing_is_detected():
    # Regres: zlecenie wymaga decyzji, verdict KOORD, best=None, reason PUSTY →
    # ani propozycja, ani alert, ani eskalacja z reasonem = „ciche nic".
    r = _r("KOORD", "", best=None)
    assert po.requires_decision(r) is True
    assert po.output_label(r) is None
    assert po.is_silent_nothing(r) is True
    assert po.never_silent_nothing(r) is False


# ── MUTATION — usunięcie always-propose → „ciche nic" wraca → RED ─────────────
def test_mutation_removing_classification_reintroduces_silent(monkeypatch):
    r = _r("PROPOSE", "feasible=3 best=c1")
    assert po.classify(r) == po.EXECUTABLE_PROPOSAL   # fix obecny
    assert po.never_silent_nothing(r)
    # MUTANT: usuń rozpoznanie wyjścia (klasyfikacja + eskalacja martwe).
    monkeypatch.setattr(po, "classify", lambda result: None)
    monkeypatch.setattr(po, "is_coordinator_escalation", lambda result: False)
    assert po.is_silent_nothing(r) is True            # „ciche nic" WRÓCIŁO
    assert po.never_silent_nothing(r) is False        # oracle RED


def test_mutation_least_damage_recognition_is_load_bearing(monkeypatch):
    r = _r("KOORD", "hard35_least_damage_alert (cap=35; 0 within cap)",
           best=_candidate(best_effort=True))
    assert po.classify(r) == po.LEAST_DAMAGE_ALERT    # fix obecny
    # MUTANT: tier-3 nierozpoznany → alert znika (RED dla oracle least-damage).
    monkeypatch.setattr(po, "is_least_damage_alert", lambda result: False)
    assert po.classify(r) != po.LEAST_DAMAGE_ALERT


# ── EFFECT ON≠OFF — serializer shadow ────────────────────────────────────────
def test_serializer_omits_type_when_flag_off(monkeypatch):
    monkeypatch.setattr(C, "load_flags", lambda: {})  # ENABLE_ALWAYS_PROPOSE OFF
    out = shadow_dispatcher._serialize_result(
        _full_result("PROPOSE", "feasible=1 best=c1", _candidate()),
        event_id="ev", latency_ms=1.0)
    assert "proposal_output_type" not in out          # bajt-parytet baseline
    assert "proposal_output_silent" not in out


def test_serializer_emits_type_when_flag_on(monkeypatch):
    monkeypatch.setattr(C, "load_flags", lambda: {"ENABLE_ALWAYS_PROPOSE": True})
    out = shadow_dispatcher._serialize_result(
        _full_result("PROPOSE", "feasible=1 best=c1", _candidate()),
        event_id="ev", latency_ms=1.0)
    assert out["proposal_output_type"] == po.EXECUTABLE_PROPOSAL
    assert out["proposal_output_silent"] is False


def test_serializer_emits_least_damage_alert_when_flag_on(monkeypatch):
    monkeypatch.setattr(C, "load_flags", lambda: {"ENABLE_ALWAYS_PROPOSE": True})
    out = shadow_dispatcher._serialize_result(
        _full_result("KOORD", "hard35_least_damage_alert (cap=35; 0 within cap)",
                     _candidate(cid="c37", best_effort=True)),
        event_id="ev", latency_ms=1.0)
    assert out["proposal_output_type"] == po.LEAST_DAMAGE_ALERT
    assert out["proposal_output_silent"] is False


@pytest.mark.parametrize(
    "reason,expected_class",
    [
        ("state_likely_stale (age=90s)", "STALE"),
        ("geometry_blind_fallback (pool=2)", "GEOMETRY"),
        ("commit_divergence_gate (delta=16min)", "COMMIT"),
        ("difficult_geometry_redirect (score=-40)", "DIFFICULT"),
        ("no_solo_candidates (fleet_n=2)", "UNKNOWN"),
    ],
)
def test_serializer_emits_explicit_escalation_class_when_flag_on(
    monkeypatch, reason, expected_class
):
    monkeypatch.setattr(C, "load_flags", lambda: {"ENABLE_ALWAYS_PROPOSE": True})
    out = shadow_dispatcher._serialize_result(
        _full_result("KOORD", reason, None),
        event_id="ev",
        latency_ms=1.0,
    )
    assert out["proposal_output_type"] == po.COORDINATOR_ESCALATION
    assert out["coordinator_escalation_class"] == expected_class


def test_serializer_flags_silent_nothing_when_flag_on(monkeypatch):
    monkeypatch.setattr(C, "load_flags", lambda: {"ENABLE_ALWAYS_PROPOSE": True})
    out = shadow_dispatcher._serialize_result(
        _full_result("KOORD", "", None),  # regres: best=None, reason pusty
        event_id="ev", latency_ms=1.0)
    assert out["proposal_output_type"] is None
    assert out["proposal_output_silent"] is True


# ── CONSUMER PARITY — nowy typ nie wywraca konsumentów ───────────────────────
def test_telegram_consumers_unaffected_by_new_fields():
    from dispatch_v2 import telegram_approver as TA
    # PROPOSE z nowymi polami → nadal owner-facing (istniejące zachowanie bez zmian).
    rec_propose = {
        "order_id": "o1", "verdict": "PROPOSE", "reason": "feasible=1 best=c1",
        "proposal_output_type": po.EXECUTABLE_PROPOSAL, "proposal_output_silent": False,
    }
    assert TA._is_owner_facing_shadow_record(rec_propose) is True
    assert TA._is_hard35_owner_alert(rec_propose) is False
    # KOORD least-damage z nowymi polami → nadal owner-alert (marker reason nietknięty).
    rec_alert = {
        "order_id": "o2", "verdict": "KOORD",
        "reason": "hard35_least_damage_alert (cap=35; 0 within cap)",
        "proposal_output_type": po.LEAST_DAMAGE_ALERT, "proposal_output_silent": False,
    }
    assert TA._is_hard35_owner_alert(rec_alert) is True
    assert TA._is_owner_facing_shadow_record(rec_alert) is True
    # Zwykły KOORD (eskalacja) z etykietą → NIE owner-facing (bez zmian).
    rec_koord = {
        "order_id": "o3", "verdict": "KOORD", "reason": "no_solo_candidates (fleet_n=5)",
        "proposal_output_type": po.COORDINATOR_ESCALATION, "proposal_output_silent": False,
    }
    assert TA._is_owner_facing_shadow_record(rec_koord) is False


def test_new_fields_are_json_serializable():
    # upsert_proposals / shadow log / panel czytają JSON — pola muszą się serializować.
    for label in (po.EXECUTABLE_PROPOSAL, po.LEAST_DAMAGE_ALERT, po.OWNER_EXCEPTION,
                  po.COORDINATOR_ESCALATION, None):
        s = json.dumps({"proposal_output_type": label, "proposal_output_silent": False})
        assert json.loads(s)["proposal_output_type"] == label


# ── RATCHET — flaga zarejestrowana + każda bramka etykietowana ───────────────
def test_ratchet_flag_in_etap4_decision_flags():
    # Lekcja A-4: flaga DECYZYJNA MUSI być w ETAP4 (conftest strip = hermetyczność).
    assert "ENABLE_ALWAYS_PROPOSE" in C.ETAP4_DECISION_FLAGS


def test_ratchet_flag_registered_in_lifecycle_registry():
    reg = json.loads(_REGISTRY.read_text(encoding="utf-8"))["flags"]
    assert "ENABLE_ALWAYS_PROPOSE" in reg
    entry = reg["ENABLE_ALWAYS_PROPOSE"]
    assert entry["default"] is False
    assert "engine" in entry["worlds"]


# KANON bramek KOORD (parytet z test_verdict_gate_guards.EXPECTED_GATES): każda
# MUSI dostać etykietę ≠ None (nowa bramka bez etykiety = „ciche nic" = RED).
_KOORD_GATE_REASONS = {
    "early_bird": "early_bird (...)",                       # hold → poza kontraktem (None OK)
    "state_likely_stale": "state_likely_stale (...)",
    "geometry_blind_fallback": "geometry_blind_fallback (...)",
    "all_candidates_low_score": "all_candidates_low_score (best=c1 score=-9<-100; feasible=2)",
    "commit_divergence_gate": "commit_divergence_gate (...)",
    "difficult_geometry_redirect": "difficult_geometry_redirect (...)",
    "best_effort_r6_breach_v2": "best_effort_r6_breach_v2 (...)",
    "best_effort_r6_breach": "best_effort_r6_breach (...)",
    "best_effort_low_score": "best_effort_low_score (...)",
    "no_solo_candidates": "no_solo_candidates (fleet_n=5) — wszyscy odrzuceni",
    "hard35_least_damage_alert": "hard35_least_damage_alert (cap=35; 0 within cap)",
}


@pytest.mark.parametrize("gate_id,reason", sorted(_KOORD_GATE_REASONS.items()))
def test_ratchet_every_koord_gate_is_labeled_or_out_of_scope(gate_id, reason):
    r = _r("KOORD", reason, best=None)
    if gate_id == "early_bird":
        # not-now hold → świadomie poza always-propose (nie „ciche nic").
        assert po.requires_decision(r) is False
        assert po.is_silent_nothing(r) is False
        return
    # Każda inna bramka wymaga decyzji i MUSI mieć etykietę (nigdy „ciche nic").
    assert po.requires_decision(r) is True
    assert po.output_label(r) is not None
    assert po.output_label(r) in po.ALL_LABELS
    assert po.never_silent_nothing(r)


def test_ratchet_least_damage_and_ownerexc_gates_map_to_owner_types():
    # Semantyka: hard35 → LEAST_DAMAGE_ALERT; owner-exception → OWNER_EXCEPTION.
    assert po.output_label(_r(
        "KOORD", "hard35_least_damage_alert (cap=35)", best=_candidate())
    ) == po.LEAST_DAMAGE_ALERT
    assert po.output_label(_r(
        "KOORD", "owner_exception:x")) == po.OWNER_EXCEPTION
