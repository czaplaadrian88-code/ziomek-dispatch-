"""V3.26 STEP 2 (R-05 SPEED-MULTIPLIER) — flag-gated regression.

Tests:
- T1 flag default False → no adjustment
- T2 gold tier (multi 0.889) → score +5.55 boost
- T3 std+ tier (multi 1.056) → score -2.80 penalty (per backtest)
- T4 slow tier (multi 1.111) → score -5.55 penalty
- T5 std tier → no change (multi 1.0)
- T6 new tier → -15 penalty (multi 1.30 policy)
- T7 unknown tier → fallback std + WARNING log
- T8 metrics propagated (cs_tier_bag → v326_speed_*)
- T9 re-sort happens (gold boosted past std previously top)
- T10 NIE rusza feasibility metrics (eta_pickup raw zachowane)
"""
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common, dispatch_pipeline  # noqa: E402


class _MockCandidate:
    def __init__(self, courier_id, name, score, tier_bag='std', metrics=None):
        self.courier_id = courier_id
        self.name = name
        self.score = score
        self.metrics = metrics or {}
        self.metrics.setdefault('cs_tier_bag', tier_bag)
        self.metrics.setdefault('eta_pickup_utc', '2026-04-23T22:00:00+00:00')


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  ✅ {label}")
            results["pass"] += 1
        else:
            print(f"  ❌ {label}  {detail}")
            results["fail"] += 1

    # ---------- T1: flag forced False (post-V3.26 flag flip default True) ----------
    print("\n=== T1: flag forced False via env (legacy) ===")
    os.environ["ENABLE_V326_SPEED_MULTIPLIER"] = "0"
    importlib.reload(common)
    importlib.reload(dispatch_pipeline)
    expect("ENABLE_V326_SPEED_MULTIPLIER False (env override)",
           common.ENABLE_V326_SPEED_MULTIPLIER is False)
    feasible = [_MockCandidate('123', 'Bartek O.', 100.0, 'gold')]
    out = dispatch_pipeline._v326_speed_multiplier_adjust(feasible, "t1")
    expect("flag=False → score unchanged", out[0].score == 100.0,
           f"got {out[0].score}")

    # Flip flag
    os.environ["ENABLE_V326_SPEED_MULTIPLIER"] = "1"
    importlib.reload(common)
    importlib.reload(dispatch_pipeline)
    expect("flipped via env", common.ENABLE_V326_SPEED_MULTIPLIER is True)

    # ---------- T2: gold tier ----------
    print("\n=== T2: gold (multi 0.889) → +5.55 boost ===")
    feasible = [_MockCandidate('413', 'Mateusz O', 100.0, 'gold')]
    out = dispatch_pipeline._v326_speed_multiplier_adjust(feasible, "t2")
    expected = 100.0 + (1.0 - 0.889) * 50.0  # +5.55
    expect(f"gold score 100 → {expected:.2f}",
           abs(out[0].score - expected) < 0.01,
           f"got {out[0].score}")
    expect("metrics.v326_speed_multiplier == 0.889",
           out[0].metrics.get("v326_speed_multiplier") == 0.889)
    expect("metrics.v326_speed_tier_used == 'gold'",
           out[0].metrics.get("v326_speed_tier_used") == 'gold')

    # ---------- T3: std+ tier ----------
    print("\n=== T3: std+ (multi 1.056) → -2.80 penalty ===")
    feasible = [_MockCandidate('370', 'Jakub OL', 100.0, 'std+')]
    out = dispatch_pipeline._v326_speed_multiplier_adjust(feasible, "t3")
    expected = 100.0 + (1.0 - 1.056) * 50.0  # -2.80
    expect(f"std+ score 100 → {expected:.2f}",
           abs(out[0].score - expected) < 0.01,
           f"got {out[0].score}")

    # ---------- T4: slow tier ----------
    print("\n=== T4: slow (multi 1.111) → -5.55 penalty ===")
    feasible = [_MockCandidate('511', 'Łukasz B', 100.0, 'slow')]
    out = dispatch_pipeline._v326_speed_multiplier_adjust(feasible, "t4")
    expected = 100.0 + (1.0 - 1.111) * 50.0
    expect(f"slow score 100 → {expected:.2f}",
           abs(out[0].score - expected) < 0.01,
           f"got {out[0].score}")

    # ---------- T5: std tier ----------
    print("\n=== T5: std (multi 1.0) → no change ===")
    feasible = [_MockCandidate('457', 'Adrian Cit', 100.0, 'std')]
    out = dispatch_pipeline._v326_speed_multiplier_adjust(feasible, "t5")
    expect("std score 100 unchanged", out[0].score == 100.0,
           f"got {out[0].score}")
    expect("v326_speed_score_adjustment == 0",
           out[0].metrics.get("v326_speed_score_adjustment") == 0.0)

    # ---------- T6: new tier ----------
    print("\n=== T6: new (multi 1.30) → -15 penalty ===")
    feasible = [_MockCandidate('522', 'Szymon Sa', 100.0, 'new')]
    out = dispatch_pipeline._v326_speed_multiplier_adjust(feasible, "t6")
    expected = 100.0 + (1.0 - 1.30) * 50.0  # -15.0
    expect(f"new score 100 → {expected:.1f}",
           abs(out[0].score - expected) < 0.01,
           f"got {out[0].score}")

    # ---------- T7: unknown tier → fallback std ----------
    print("\n=== T7: unknown tier → fallback std + WARNING ===")
    feasible = [_MockCandidate('999', 'Phantom', 100.0, 'unknown_xyz')]
    from unittest import mock as _mock
    with _mock.patch.object(dispatch_pipeline.log, 'warning') as mock_warn:
        out = dispatch_pipeline._v326_speed_multiplier_adjust(feasible, "t7")
        warning_msgs = [str(c) for c in mock_warn.call_args_list]
        expect("warning fired w log", any('unknown tier' in m for m in warning_msgs),
               f"calls: {warning_msgs}")
    expect("unknown tier → multiplier 1.0 fallback",
           out[0].metrics.get("v326_speed_multiplier") == 1.0)
    expect("unknown tier → score unchanged 100",
           out[0].score == 100.0)
    expect("v326_speed_tier_used == 'std' fallback",
           out[0].metrics.get("v326_speed_tier_used") == 'std')

    # ---------- T8: metrics propagation full ----------
    print("\n=== T8: metrics fully propagated ===")
    feasible = [_MockCandidate('413', 'Mateusz O', 100.0, 'gold')]
    out = dispatch_pipeline._v326_speed_multiplier_adjust(feasible, "t8")
    m = out[0].metrics
    expect("v326_speed_tier_used", m.get("v326_speed_tier_used") == 'gold')
    expect("v326_speed_multiplier", m.get("v326_speed_multiplier") == 0.889)
    expect("v326_speed_score_adjustment exists", "v326_speed_score_adjustment" in m)

    # ---------- T9: re-sort: slow demoted past gold ----------
    print("\n=== T9: re-sort — gold boost flips ranking ===")
    feasible = [
        _MockCandidate('511', 'Łukasz B', 102.0, 'slow'),  # original BEST
        _MockCandidate('413', 'Mateusz O', 99.0, 'gold'),  # original 2nd
    ]
    out = dispatch_pipeline._v326_speed_multiplier_adjust(feasible, "t9")
    # slow: 102 - 5.55 = 96.45; gold: 99 + 5.55 = 104.55 → gold becomes BEST
    expect("post-adjustment gold becomes BEST (re-sort)",
           out[0].courier_id == '413',
           f"got order: {[c.courier_id for c in out]}")

    # ---------- T10: feasibility metrics untouched ----------
    print("\n=== T10: eta_pickup_utc raw zachowane (NIE rusza feasibility) ===")
    feasible = [_MockCandidate('413', 'Mateusz O', 100.0, 'gold')]
    eta_before = feasible[0].metrics["eta_pickup_utc"]
    out = dispatch_pipeline._v326_speed_multiplier_adjust(feasible, "t10")
    eta_after = out[0].metrics["eta_pickup_utc"]
    expect("eta_pickup_utc UNCHANGED (multiplier nie rusza feasibility)",
           eta_before == eta_after,
           f"before={eta_before} after={eta_after}")

    # Cleanup
    del os.environ["ENABLE_V326_SPEED_MULTIPLIER"]
    importlib.reload(common)
    importlib.reload(dispatch_pipeline)

    print(f"\n=== summary: {results['pass']} pass, {results['fail']} fail ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
