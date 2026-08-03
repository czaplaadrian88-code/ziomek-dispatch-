"""core.proposal_output — kanoniczny kontrakt TYPU WYJŚCIA decyzji (A-3, OD-1).

JEDNO źródło prawdy na pytanie „jakiego RODZAJU decyzję wyemitował silnik dla
zlecenia wymagającego decyzji?". Semantyka ownera (OD-1, 2026-08-02): silnik
ZAWSZE emituje wynik — nigdy cicho „nic". Trzy kanoniczne typy owner-facing:

  EXECUTABLE_PROPOSAL — istnieje wykonalne przypisanie w regułach tieru
                        (verdict PROPOSE: feasible / best_effort / solo).
  LEAST_DAMAGE_ALERT  — TYLKO tier 3 (Alarm), gdy kończą się możliwości
                        przypisania: jawny „najlepszy z najgorszych" (best-of-worst)
                        dla koordynatora. NIE auto-przypisanie — ALERT dla człowieka.
                        Kanon = istniejący marker `hard35_least_damage_alert`
                        (verdict KOORD + best nazwany + best_effort; noc 28.07,
                        core/selection.py + telegram_approver._is_hard35_owner_alert).
  OWNER_EXCEPTION     — ścieżka nadpisania przez właściciela (owner ACK +
                        reason-code). PRODUCENT poza zakresem A-3 (extension point).

Poza tą trójką istnieją OPERACYJNE eskalacje do koordynatora (pusta pula/no_solo,
stale-state, geometry-blind, commit-divergence, difficult-geometry) — świadomie
NIE są propozycjami. Kontrakt `tests/test_verdict_gate_guards.py`: ALWAYS-PROPOSE
neutralizuje TYLKO bramki jakości; eskalacje operacyjne ZOSTAJĄ KOORD (bezpieczeństwo
> propozycja). Znaczymy je jako COORDINATOR_ESCALATION — to NIE „ciche nic": niosą
jawny reason do konsoli koordynatora. „Ciche nic" = zlecenie wymagające decyzji, dla
którego silnik nie emituje ANI propozycji, ANI alertu, ANI nadpisania, ANI jawnej
eskalacji z reasonem (np. verdict KOORD, best=None, reason pusty — regres).

Not-now hold (`early_bird`) oraz niedyspozycjonowalny reject geokodu (verdict SKIP)
oraz obserwacja cyklu (verdict OBSERVE) NIE wymagają aktywnej decyzji TERAZ i są
poza kontraktem always-propose (mają własny reason; nie są „cichym nic").

Moduł jest CZYSTĄ funkcją nad PipelineResult — zero mutacji verdiktu/decyzji, zero
I/O, zero zależności od `common`. Konsumenci (serializer shadow, panel, konsola,
telegram, upsert A-4, claim) czytają `classify()`/`output_label()`; nikt nie
re-derywuje typu z verdict+reason samodzielnie (koniec duplikacji). Wpięcie do
serializera shadow za flagą decyzyjną `ENABLE_ALWAYS_PROPOSE` (default OFF,
shadow-first): OFF = brak pól (bajt-parytet baseline), ON = `proposal_output_type`
+ `proposal_output_silent`.
"""
from __future__ import annotations

from typing import Any, Optional

# ── Kanoniczne typy (OD-1). Wartości = stabilne stringi w shadow_decisions.jsonl ──
EXECUTABLE_PROPOSAL = "EXECUTABLE_PROPOSAL"
LEAST_DAMAGE_ALERT = "LEAST_DAMAGE_ALERT"
OWNER_EXCEPTION = "OWNER_EXCEPTION"
# Nie owner-facing „propozycja", ale JAWNY sygnał (NIE „ciche nic"): operacyjna
# eskalacja do koordynatora. Osobno od trójki — konsumenci propozycji używają
# OWNER_TYPES; monitoring/serializer widzą pełną etykietę przez output_label().
COORDINATOR_ESCALATION = "COORDINATOR_ESCALATION"

OWNER_TYPES = (EXECUTABLE_PROPOSAL, LEAST_DAMAGE_ALERT, OWNER_EXCEPTION)
ALL_LABELS = OWNER_TYPES + (COORDINATOR_ESCALATION,)

# Marker istniejącej tier-3 least-damage (parytet z telegram._is_hard35_owner_alert).
LEAST_DAMAGE_REASON_PREFIX = "hard35_least_damage_alert"
# Reason-code / atrybut ścieżki nadpisania właściciela (extension point; producent
# poza A-3 — np. manual_overrides / owner-ACK card).
OWNER_EXCEPTION_REASON_PREFIX = "owner_exception"
# Verdykty poza kontraktem always-propose (nie „zlecenie wymagające decyzji TERAZ").
_NON_DECISION_VERDICTS = frozenset({"SKIP", "OBSERVE"})
# Prefiks reasonu not-now hold (early-bird ≥ próg): re-ocena bliżej gotowości.
_HOLD_REASON_PREFIX = "early_bird"


def _verdict(result: Any) -> str:
    return str(getattr(result, "verdict", "") or "")


def _reason(result: Any) -> str:
    return str(getattr(result, "reason", "") or "")


def is_least_damage_alert(result: Any) -> bool:
    """Tier-3 best-of-worst — kanoniczny marker (parytet telegram/_is_hard35_owner_alert)."""
    return (
        _verdict(result) == "KOORD"
        and _reason(result).startswith(LEAST_DAMAGE_REASON_PREFIX)
    )


def is_owner_exception(result: Any) -> bool:
    """Jawne nadpisanie właściciela (owner ACK + reason-code). Extension point A-3."""
    if bool(getattr(result, "owner_exception", False)):
        return True
    return _reason(result).startswith(OWNER_EXCEPTION_REASON_PREFIX)


def requires_decision(result: Any) -> bool:
    """Czy zlecenie wymaga AKTYWNEJ decyzji dyspozytorskiej TERAZ.

    NIE wymaga (poza kontraktem always-propose): verdict SKIP (geokod-reject,
    niedyspozycjonowalne) / OBSERVE (obserwacja cyklu) oraz not-now hold
    (`early_bird`). Te niosą własny reason i nie są „cichym nic".
    """
    if _verdict(result) in _NON_DECISION_VERDICTS:
        return False
    if _reason(result).startswith(_HOLD_REASON_PREFIX):
        return False
    return True


def classify(result: Any) -> Optional[str]:
    """Kanoniczny typ owner-facing (jeden z OWNER_TYPES) albo None.

    None = wynik NIE jest propozycją/alertem/nadpisaniem (operacyjna eskalacja,
    hold albo „ciche nic"). Rozróżnienie eskalacja↔ciche-nic: `is_silent_nothing`.
    Kolejność jest kontraktem: owner-exception > least-damage (tier 3) > PROPOSE.
    """
    if is_owner_exception(result):
        return OWNER_EXCEPTION
    if is_least_damage_alert(result):
        return LEAST_DAMAGE_ALERT
    if _verdict(result) == "PROPOSE":
        return EXECUTABLE_PROPOSAL
    return None


def is_coordinator_escalation(result: Any) -> bool:
    """Operacyjna eskalacja: verdict KOORD z reasonem, NIE least-damage/owner-exception.

    Jawna (best może być None — pusta pula/no_solo), z reasonem widocznym w
    konsoli. To NIE „ciche nic". Wymaga aktywnej decyzji (early_bird wykluczony).
    """
    return (
        requires_decision(result)
        and _verdict(result) == "KOORD"
        and bool(_reason(result))
        and classify(result) is None
    )


def output_label(result: Any) -> Optional[str]:
    """Pełna etykieta wyjścia dla serializera/monitoringu: jeden z OWNER_TYPES,
    COORDINATOR_ESCALATION, albo None (poza kontraktem lub „ciche nic")."""
    canonical = classify(result)
    if canonical is not None:
        return canonical
    if is_coordinator_escalation(result):
        return COORDINATOR_ESCALATION
    return None


def is_silent_nothing(result: Any) -> bool:
    """„Ciche nic": zlecenie wymaga decyzji, a silnik nie zaklasyfikował ŻADNEGO
    wyjścia (brak propozycji, alertu, nadpisania i jawnej eskalacji z reasonem)."""
    return requires_decision(result) and output_label(result) is None


def never_silent_nothing(result: Any) -> bool:
    """Inwariant OD-1: żadne zlecenie wymagające decyzji nie jest „cichym nic"."""
    return not is_silent_nothing(result)
