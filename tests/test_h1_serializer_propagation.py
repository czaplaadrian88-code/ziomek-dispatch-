"""H1 regression guard (sprint 2026-04-25):
shadow_dispatcher._serialize_candidate MUSI auto-propagować v325_/v326_/v319_/
r07_/bonus_/rule_ prefixed keys z metrics do output dict.

Cross-review finding B#H1: serializer trzymał hardcoded explicit list,
14+ kluczy (v325_reject_reason, v326_speed_*, v326_fleet_*, etc.)
droppowanych do learning_log → analityka downstream broken.

Test mockuje minimal Candidate i sprawdza że prefixed keys trafiają do output.
"""
from dataclasses import dataclass, field
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2.shadow_dispatcher import _serialize_candidate, _propagate_prefixed_metrics  # noqa: E402


@dataclass
class _MockCand:
    metrics: dict = field(default_factory=dict)
    plan: object = None
    courier_id: int = 123
    name: str = "Test"
    score: float = 100.0
    feasibility_verdict: str = "FEASIBLE"
    feasibility_reason: str = ""
    best_effort: bool = False


def test_v325_v326_keys_propagated():
    cand = _MockCand(metrics={
        "v325_reject_reason": "PRE_SHIFT_TOO_EARLY",
        "v325_new_courier_penalty": -30,
        "v325_pickup_ref_source": "r07_chain_eta",
        "v325_pre_shift_too_early_min": 5.5,
        "v325_pre_shift_soft_penalty": -10,
        "v325_pickup_post_shift_excess_min": 2.0,
        "v325_new_courier_advantage": "+15min vs avg",
        "v325_new_courier_flag": "🆕",
        "v326_speed_multiplier": 0.85,
        "v326_speed_tier_used": "fast",
        "v326_speed_score_adjustment": 5.0,
        "v326_fleet_bag_avg": 2.3,
        "v326_fleet_load_delta": 1.2,
        "v326_fleet_load_adjustment": 0.5,
    })
    result = _serialize_candidate(cand)
    expected_keys = [
        "v325_reject_reason", "v325_new_courier_penalty",
        "v325_pickup_ref_source", "v325_pre_shift_too_early_min",
        "v325_pre_shift_soft_penalty", "v325_pickup_post_shift_excess_min",
        "v325_new_courier_advantage", "v325_new_courier_flag",
        "v326_speed_multiplier", "v326_speed_tier_used",
        "v326_speed_score_adjustment", "v326_fleet_bag_avg",
        "v326_fleet_load_delta", "v326_fleet_load_adjustment",
    ]
    missing = [k for k in expected_keys if k not in result]
    assert not missing, f"keys NOT propagated: {missing}"
    # Spot-check values preserved
    assert result["v325_reject_reason"] == "PRE_SHIFT_TOO_EARLY"
    assert result["v326_speed_multiplier"] == 0.85
    assert result["v326_fleet_bag_avg"] == 2.3


def test_unknown_prefix_not_propagated():
    cand = _MockCand(metrics={
        "random_key": "should_not_appear",
        "foo_bar": 1,
        "panel_meta": "x",
    })
    result = _serialize_candidate(cand)
    assert "random_key" not in result
    assert "foo_bar" not in result
    assert "panel_meta" not in result


def test_explicit_fields_take_precedence():
    # bonus_l1 jest jawnie w dict literal `m.get("bonus_l1")`. Auto-prop
    # ma `if k in base: continue` — explicit value pozostaje, no overwrite.
    cand = _MockCand(metrics={"bonus_l1": 5.0})
    result = _serialize_candidate(cand)
    assert result["bonus_l1"] == 5.0
    # Sanity: courier_id z c.courier_id, NIE z metrics
    cand2 = _MockCand(courier_id=999, metrics={"courier_id": -1})
    result2 = _serialize_candidate(cand2)
    assert result2["courier_id"] == 999


def test_propagate_helper_handles_none_metrics():
    base = {"x": 1}
    _propagate_prefixed_metrics(base, None)
    assert base == {"x": 1}
    _propagate_prefixed_metrics(base, {})
    assert base == {"x": 1}


if __name__ == "__main__":
    test_v325_v326_keys_propagated()
    print("test_v325_v326_keys_propagated: PASS")
    test_unknown_prefix_not_propagated()
    print("test_unknown_prefix_not_propagated: PASS")
    test_explicit_fields_take_precedence()
    print("test_explicit_fields_take_precedence: PASS")
    test_propagate_helper_handles_none_metrics()
    print("test_propagate_helper_handles_none_metrics: PASS")
    print("ALL 4/4 PASS")
