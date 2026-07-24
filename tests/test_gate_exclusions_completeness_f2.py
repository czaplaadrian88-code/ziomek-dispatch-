"""F-2 wielki-audyt 2026-07-18 — kompletność `_GATE_RANKING_DELTA_EXCLUSIONS`.

Kontekst: bramka KOORD (`_gate_score_excluding_ranking_deltas`) ma wyłączać KAŻDĄ
flag-gated deltę rankingową z final_score (dyrektywa ALWAYS-PROPOSE). Istniejący
strażnik `test_inv_gate_score_delta` łapie WYŁĄCZNIE idiom
`if C.decision_flag("X"):\\n final_score = final_score + var` — trzy delty używają
innych idiomów (odjęcie / sumowanie zbiorcze) i przeciekają (klasa C13):
  • ENABLE_POST_SHIFT_OVERRUN_PENALTY  → `final_score -= post_shift_overrun_penalty`
  • ENABLE_BAG_TIME_FAIRNESS_SCORING   → `+ bonus_bag_time_sum/max/fifo` (zbiorczo)
  • ENABLE_R5_PICKUP_DETOUR_PENALTY    → `+ bonus_r5_pickup_detour_penalty` (zbiorczo; flaga DZIŚ ON)

Dwa strażniki:
1. RATCHET xfail(strict=True): kompletność pokrycia znanych 3 luk — dziś CZERWONY
   (XFAIL). Naprawa (wpisy w EXCLUSIONS z poprawnym ZNAKIEM — patrz finding F-2:
   naiwny wpis post_shift PODWOIŁBY karę w gate) → XPASS(strict) → zdejmij xfail.
2. Zielony RATCHET anty-nowym-lukom: AST-owy komplet nazw uczestniczących
   w przypisaniach do `final_score` w core/candidates — NOWA delta poza znanym
   zbiorem = RED natychmiast (decyzja: EXCLUSIONS albo świadomy baseline-update).
"""
import ast
import inspect

import dispatch_v2.dispatch_pipeline as DP
from dispatch_v2.core import candidates as _cand

_KNOWN_GAP_FLAGS = {
    "ENABLE_POST_SHIFT_OVERRUN_PENALTY",
    "ENABLE_BAG_TIME_FAIRNESS_SCORING",
    "ENABLE_R5_PICKUP_DETOUR_PENALTY",
}


def test_known_gap_flags_covered_by_exclusions():
    registry_flags = {f for f, _ in DP._GATE_RANKING_DELTA_EXCLUSIONS}
    missing = sorted(_KNOWN_GAP_FLAGS - registry_flags)
    assert not missing, (
        f"flag-gated delty rankingowe bez wpisu w _GATE_RANKING_DELTA_EXCLUSIONS: "
        f"{missing}")


def _final_score_participant_names():
    """AST: wszystkie identyfikatory w RHS przypisań do final_score (+= / -= / =)."""
    tree = ast.parse(inspect.getsource(_cand))
    names = set()

    class V(ast.NodeVisitor):
        def _collect(self, node):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)

        def visit_Assign(self, node):
            if any(isinstance(t, ast.Name) and t.id == "final_score"
                   for t in node.targets):
                self._collect(node.value)
            self.generic_visit(node)

        def visit_AugAssign(self, node):
            if isinstance(node.target, ast.Name) and node.target.id == "final_score":
                self._collect(node.value)
            self.generic_visit(node)

    V().visit(tree)
    names.discard("final_score")
    return names


# Komplet uczestników final_score w core/candidates na 2026-07-18 (baseline).
# NOWA nazwa w tym zbiorze = nowa delta → zdecyduj: EXCLUSIONS (jeśli flag-gated
# rankingowa) albo świadomie dopisz tu z komentarzem-powodem.
_BASELINE_PARTICIPANTS = {
    "score_result", "bundle_bonus", "timing_gap_bonus", "wave_bonus",
    "bonus_penalty_sum", "bonus_bug2_continuation", "v324a_extension_penalty",
    # zmienna pomocnicza sanitizacji:
    "_sv",
    # flag-gated pokryte przez _GATE_RANKING_DELTA_EXCLUSIONS (nazwy zmiennych
    # == klucze metrics — pilnuje tego test_registry_metric_key_equals_...):
    "bonus_sync_spread_shadow_delta", "bonus_loadgov_shadow_delta",
    "bonus_r1_progressive_shadow_delta", "bonus_v319h_guard_shadow_delta",
    "bonus_repo_cost_shadow_delta", "bonus_bundle_fit_shadow_delta",
    "fix_c_additive_pen_shadow",
    # ZNANE LUKI F-2 (xfail wyżej pilnuje ich domknięcia):
    "post_shift_overrun_score_delta",
    "bonus_bag_time_sum", "bonus_bag_time_max", "bonus_fifo_violation",
    "bonus_r5_pickup_detour_penalty",
}


def test_no_new_uncovered_final_score_delta():
    got = _final_score_participant_names()
    new = sorted(got - _BASELINE_PARTICIPANTS)
    assert not new, (
        "NOWE nazwy w przypisaniach do final_score (core/candidates) poza "
        f"baseline F-2: {new} — jeśli to flag-gated delta rankingowa, dopisz do "
        "_GATE_RANKING_DELTA_EXCLUSIONS (z poprawnym znakiem!) i do baseline; "
        "inaczej świadomy baseline-update z powodem.")


def test_baseline_names_still_exist():
    """Anty-zgnilizna baseline'u: nazwy z baseline muszą istnieć w źródle
    (usunięcie delty = też zmiana do odnotowania)."""
    src = inspect.getsource(_cand)
    dead = sorted(n for n in _BASELINE_PARTICIPANTS if n not in src)
    assert not dead, f"baseline F-2 zawiera nazwy nieobecne w źródle: {dead}"


def test_all_f2_signed_deltas_are_removed_from_gate_score(monkeypatch):
    """Oracle behawioralny: każda F-2 kara zmienia ranking, nie bramkę KOORD."""
    cases = {
        "ENABLE_POST_SHIFT_OVERRUN_PENALTY": {
            "post_shift_overrun_score_delta": -40.0,
        },
        "ENABLE_BAG_TIME_FAIRNESS_SCORING": {
            "bonus_bag_time_sum": -10.0,
            "bonus_bag_time_max": -20.0,
            "bonus_fifo_violation": -30.0,
        },
        "ENABLE_R5_PICKUP_DETOUR_PENALTY": {
            "bonus_r5_pickup_detour_penalty": -25.0,
        },
    }

    for flag, metrics in cases.items():
        class Candidate:
            score = -90.0 + sum(metrics.values())

        Candidate.metrics = metrics
        monkeypatch.setattr(DP.C, "decision_flag", lambda name, f=flag: name == f)
        assert DP._gate_score_excluding_ranking_deltas(Candidate()) == -90.0
