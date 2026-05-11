"""V3.28 P3-D6 path B sprint: geometry-blind fallback KOORD escalation.

Tech debt #29 path B + Lekcja #108. Pre-filter w dispatch_pipeline po feasible
list construction:
- Gdy len(feasible) >= 2 AND wszystkie plan.strategy=='ortools_rejected_v3274'
  AND wszyscy mają r1_avg_pairwise_cosine < 0 → verdict=KOORD.
- Cross-cutting concern: route_simulator (P3-D6 path A tie-break) działa
  per-plan-permutation, path B działa per-candidate-pool — orthogonal.

Empirical baseline: case 472338 Ogniomistrz 10.05 (cos=-0.326, deliv_spread=12.63km
+ V3.27.4 frozen pickup window forced greedy). Adrian panel override → cid=500.
Per Adrian doktryna Z2 'jakość ponad szybkość': better eskalować Adriana
(KOORD path) niż auto-propose low-quality bundle.
"""
import inspect


def test_pathb_gate_predicate_present():
    """Source regression: gate condition `_all_greedy_fallback AND _all_negative_cos`."""
    from dispatch_v2 import dispatch_pipeline
    src = inspect.getsource(dispatch_pipeline)
    # Path B comment header
    assert "P3-D6 path B" in src
    # Gate variables
    assert "_all_greedy_fallback" in src
    assert "_all_negative_cos" in src


def test_pathb_strategy_check_ortools_rejected_v3274():
    """Source regression: strategy comparison value matches greedy fallback marker."""
    from dispatch_v2 import dispatch_pipeline
    src = inspect.getsource(dispatch_pipeline)
    # The strategy string used dla greedy fallback w route_simulator (linia ~1036)
    assert 'strategy", "") == "ortools_rejected_v3274"' in src


def test_pathb_cos_negative_check():
    """Source regression: cos<0 condition na r1_avg_pairwise_cosine."""
    from dispatch_v2 import dispatch_pipeline
    src = inspect.getsource(dispatch_pipeline)
    # Path B section uses metrics.get("r1_avg_pairwise_cosine") < 0
    pathb_start = src.find("P3-D6 path B 2026-05-11")
    assert pathb_start > 0
    pathb_section = src[pathb_start:pathb_start + 2000]
    assert 'r1_avg_pairwise_cosine"' in pathb_section
    assert "< 0" in pathb_section


def test_pathb_pool_feasible_min_2():
    """Source regression: gate only triggers gdy len(feasible) >= 2 (>=1 single cand zostaje, no escalation)."""
    from dispatch_v2 import dispatch_pipeline
    src = inspect.getsource(dispatch_pipeline)
    pathb_start = src.find("P3-D6 path B 2026-05-11")
    pathb_section = src[pathb_start:pathb_start + 2000]
    assert "if len(feasible) >= 2:" in pathb_section


def test_pathb_emits_koord_verdict():
    """Source regression: verdict='KOORD' z reason geometry_blind_fallback."""
    from dispatch_v2 import dispatch_pipeline
    src = inspect.getsource(dispatch_pipeline)
    assert "geometry_blind_fallback" in src
    pathb_start = src.find("P3-D6 path B 2026-05-11")
    pathb_section = src[pathb_start:pathb_start + 2500]
    assert 'verdict="KOORD"' in pathb_section


def test_pathb_positioned_after_stale_state_before_low_score():
    """Order matters: state_likely_stale (priority) → P3-D6 path B → all_candidates_low_score (broad)."""
    from dispatch_v2 import dispatch_pipeline
    src = inspect.getsource(dispatch_pipeline)
    stale_pos = src.find("state_likely_stale")
    pathb_pos = src.find("P3-D6 path B 2026-05-11")
    low_score_pos = src.find("all_candidates_low_score")
    assert stale_pos > 0 and pathb_pos > 0 and low_score_pos > 0
    assert stale_pos < pathb_pos < low_score_pos, (
        f"Order broken: stale={stale_pos}, pathb={pathb_pos}, low_score={low_score_pos}"
    )


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
