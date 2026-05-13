"""V3.28 tech debt #38: empirical fixture artifact dla scoring rebalance sprintu.

Order 472791 Pani Pierożek → Poleska 85A 13.05.2026 09:54:01 UTC = 11:54 Warsaw:
- proposed_courier_id=514 (Tomasz Ch K-514), score=-13.27
- actual_courier_id=470 (Adrian panel override → Piotr K-470)
- Tomek bag=2 (Sweet Fit→Zachodnia W + Rany Julek→Wyszyńskiego N, both 'assigned' pre-pickup)
- Piotr K-470 (picked_up Wiosenna S, ~4 min od deliveru 472778) — w pool, NIE w fixture
  alternatives (serializer zapisuje tylko BEST; pool_total=12, pool_feasible=1)

Motywuje sprint Fix 2+3 (NIE Fix 1 — render only, LIVE 2026-05-13 commit 1d87307).
5-warstwowa diagnoza:
- W-A: effective_start_pos = anchor proximity (~1.5km Rany Julek) ukrywa post-pickup
       overload trajectory cost (~6km Zachodnia W → Poleska)
- W-B: trajectory smoothness intra-courier-only, brak inter-courier penalty
- W-C: geometry_blind_fallback wymaga koniunkcji (all_greedy + all_negative_cos),
       Piotr positive cos → escalation off
- W-D: MIN_PROPOSE_SCORE=-100 zbyt liberalne (Tomek -13.27 ≫ -100)
- W-E: picked_up courier brak almost-done bonus (Piotr ETA-to-deliv 4 min,
       widziany jako 'bag=1 obciążony' zamiast 'de facto wolny za 4 min')

Pattern (Adrian doktryna Z3 'buduj na lata', Lekcja #82 empirical-first):
Każdy 'wallpaper test case' (Adrian rozpoznaje jako bad, Ziomek nie reject'uje)
zachować jako fixture dla regression. Sprint Fix 2+3 replay'uje ten case żeby
verify score reduction post-fix.

FORWARD MARKERS post-Fix 2+3 (NIE auto-validated tutaj — manual gate przy sprincie):
- (Fix 2) effective_start_pos = max(anchor_dist, tail_dist) → Tomek km_to_pickup
  worst-case ~6 km (tail Zachodnia W → Pani Pierożek), bonus_r1_corridor=+20.0
  redukuje się lub flips (corridor wymaga proximity).
- (Fix 3a) Piotr K-470 dostaje s_almost_free_bonus=+30 + effective_start_pos
  = current_delivery_target Wiosenna → wygrywa Tomka. Wymaga osobnego state-snapshot
  fixture (orders_state.json @ 11:54 13.05 — brak backupu; rekonstrukcja w sprincie).
- (Fix 3b) MIN_PROPOSE_SCORE=-50 + S_bag_pending_pickup=-8 × 2 pending=-16 baseline:
  Tomek -13.27 → ~-29 + (W-A worst-case W-A delta) → < -50 → KOORD zamiast PROPOSE.

Cross-ref: #472338 (P3-D5/D6 R1 corridor + trajectory smoothness sprint), Lekcja #116
(multi-source UI Fix 1 sibling, commit 1d87307), Lekcja #82 (empirical fixture-first).
"""
import json
from pathlib import Path


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "calibration_cases" /
    "2026-05-13_472791_pani_pierozek_picked_up_loss.json"
)


def _load():
    with FIXTURE_PATH.open() as f:
        return json.load(f)


def test_fixture_file_exists():
    assert FIXTURE_PATH.exists(), f"Missing fixture: {FIXTURE_PATH}"


def test_fixture_loads_as_json():
    rec = _load()
    assert isinstance(rec, dict)


def test_fixture_order_id_is_472791():
    rec = _load()
    assert rec["order_id"] == "472791"


def test_fixture_panel_override_action():
    rec = _load()
    assert rec["action"] == "PANEL_OVERRIDE"


def test_fixture_proposed_was_tomek_514():
    rec = _load()
    assert rec["proposed_courier_id"] == "514"


def test_fixture_actual_was_piotr_470():
    rec = _load()
    assert rec["actual_courier_id"] == "470"


def test_fixture_proposed_score_negative_mediocre():
    """Empirical reference: pre-Fix score=-13.27 (in W-D liberal MIN_PROPOSE_SCORE=-100
    zone, NIE odrzucony mimo strukturalnie bad). Post-Fix 3b expected <-50 (skip)."""
    rec = _load()
    proposed = rec["proposed_score"]
    assert -15.0 < proposed < -10.0, f"Expected score ~-13.27, got {proposed}"


def test_fixture_tomek_bag_size_2():
    """W-A driver: bag=2 (pending pickup Sweet Fit + Rany Julek). Post-Fix 3b:
    S_bag_pending_pickup=-8 × 2 = -16 baseline modifier."""
    rec = _load()
    best = rec["decision"]["best"]
    assert best["r6_bag_size"] == 2


def test_fixture_tomek_r1_corridor_bonus_positive():
    """W-C driver: Tomek dostał R1 corridor bonus +20 (cos=0.881 high — wszystkie 3
    stops w tej samej strefie). Post-Fix 2 (worst-case effective_start_pos): bonus
    może zniknąć lub flip jeśli tail-pos distance redefiniuje 'po drodze'."""
    rec = _load()
    best = rec["decision"]["best"]
    assert best["bonus_r1_corridor"] == 20.0
    assert best["r1_avg_pairwise_cosine"] > 0.85


def test_fixture_tomek_pos_source_pre_shift():
    """Tomek był pre-shift (synthetic position BIALYSTOK_CENTER per Faza 7 helper).
    effective_start_at=12:00 Warsaw — shift start. Pre-Fix: scoring traktuje go
    jako anchor=last_assigned_pickup ~1.5km od Pani Pierożek."""
    rec = _load()
    best = rec["decision"]["best"]
    assert best["pos_source"] == "pre_shift"


def test_fixture_no_alternatives_serialized():
    """Serializer zapisuje tylko BEST candidate; pool_total=12, feasible=1,
    Piotr K-470 wśród 11 odrzuconych w feasibility-stage. Forward marker:
    sprint Fix 2+3 wymaga state-snapshot orders_state.json @ 11:54 13.05 dla
    pełnej replay (BRAK backupu — rekonstrukcja TBD)."""
    rec = _load()
    alts = rec["decision"].get("alternatives", [])
    assert len(alts) == 0


def test_fixture_pool_total_12_feasible_1():
    """W-E latent driver: pool_total=12 kandydatów, feasible=1 (Tomek). 11
    odrzuconych w feasibility (włącznie z Piotr K-470 — wymaga state-snapshot
    do verify). Post-Fix 3a: Piotr powinien przejść do feasible przez
    almost-done bonus + effective_start_pos = current_delivery_target."""
    rec = _load()
    ctx = rec["decision"]["auto_route_context"]
    assert ctx["auto_route_pool_total"] == 12
    assert ctx["auto_route_pool_feasible"] == 1


def test_fixture_auto_route_alert_mass_fail():
    """Auto route ALERT (NIE AUTO) — mass_fail signal: feasible coverage 1/12 = 8%.
    Sanity check: ALERT branch correct dla diagnozy 'feasibility-stage rejection
    too aggressive' (W-E latent issue)."""
    rec = _load()
    d = rec["decision"]
    assert d["auto_route"] == "ALERT"
    assert "mass_fail" in d["auto_route_reason"]


def test_fixture_ts_matches_handoff_narrative():
    """Sanity: ts=09:54:01 UTC = 11:54:01 Warsaw, matches sprint_timeline narrative."""
    rec = _load()
    assert rec["ts"].startswith("2026-05-13T09:57"), f"Got {rec['ts']}"
    assert rec["decision"]["ts"].startswith("2026-05-13T09:54:01"), \
        f"Got {rec['decision']['ts']}"


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
