"""V3.25 STEP C (R-04 NEW-COURIER-CAP gradient) — flag-gated regression.

Tests:
- T1 flag default False → no penalty applied
- T2 flag True + tier='new' + bag=0 + advantage=60 → penalty -10
- T3 flag True + tier='new' + bag=0 + advantage=30 → penalty -30
- T4 flag True + tier='new' + bag=0 + advantage=10 → penalty -50
- T5 flag True + tier='new' + bag=2 → HARD SKIP (-1e9)
- T6 flag True + tier!='new' (std+) → no change
- T7 visual flag w metrics ("🆕 NOWY KURIER ...")
- T8 cs.tier_label propagated z courier_tiers.json (522, 500 = 'new')
"""
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common, dispatch_pipeline, courier_resolver  # noqa: E402


class _MockCandidate:
    def __init__(self, courier_id, score, tier_label='std', bag_size=0,
                 bundle_level3_dev=None):
        self.courier_id = courier_id
        self.score = score
        self.metrics = {
            "cs_tier_label": tier_label,
            "cs_tier_bag": tier_label,
            "bag_size_before": bag_size,
            "bundle_level3_dev": bundle_level3_dev,
        }


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  ✅ {label}")
            results["pass"] += 1
        else:
            print(f"  ❌ {label}  {detail}")
            results["fail"] += 1

    # NOTE: post-STEP C flag flip (2026-04-23 22:28), default common.py = True.
    # Force False via env override dla regression coverage legacy path.
    os.environ["ENABLE_V325_NEW_COURIER_CAP"] = "0"
    importlib.reload(common)
    importlib.reload(dispatch_pipeline)

    # ---------- T1: flag forced False via env → no change ----------
    print("\n=== T1: flag forced False via env (legacy path) ===")
    expect("ENABLE_V325_NEW_COURIER_CAP False (env override)",
           common.ENABLE_V325_NEW_COURIER_CAP is False)
    feasible = [
        _MockCandidate("522", 100.0, tier_label='new'),
        _MockCandidate("370", 80.0, tier_label='std+'),
    ]
    out = dispatch_pipeline._v325_new_courier_penalty(feasible, order_id="t1")
    expect("flag=False → score unchanged (522 still 100, 370 still 80)",
           out[0].score == 100.0 and out[1].score == 80.0,
           f"got 522={out[0].score} 370={out[1].score}")

    # Flip flag
    os.environ["ENABLE_V325_NEW_COURIER_CAP"] = "1"
    importlib.reload(common)
    importlib.reload(dispatch_pipeline)
    expect("flag flipped True via env", common.ENABLE_V325_NEW_COURIER_CAP is True)

    # ---------- T2: tier=new + bag=0 + advantage=60 → penalty -10 ----------
    print("\n=== T2: tier=new + bag=0 + advantage=60 → penalty -10 ===")
    feasible = [
        _MockCandidate("522", 200.0, tier_label='new', bag_size=0),
        _MockCandidate("370", 140.0, tier_label='std+', bag_size=0),
    ]
    out = dispatch_pipeline._v325_new_courier_penalty(feasible, order_id="t2")
    new_cand = next(c for c in out if c.courier_id == "522")
    expect("522 score 200 → 190 (penalty -10)", new_cand.score == 190.0,
           f"got {new_cand.score}")
    expect("522 metrics.v325_new_courier_penalty == -10",
           new_cand.metrics.get("v325_new_courier_penalty") == -10)
    expect("522 metrics.v325_new_courier_advantage == 60.0",
           new_cand.metrics.get("v325_new_courier_advantage") == 60.0)

    # ---------- T3: tier=new + bag=0 + advantage=30 → penalty -30 ----------
    print("\n=== T3: advantage=30 → penalty -30 ===")
    feasible = [
        _MockCandidate("522", 110.0, tier_label='new', bag_size=0),
        _MockCandidate("370", 80.0, tier_label='std+', bag_size=0),
    ]
    out = dispatch_pipeline._v325_new_courier_penalty(feasible, order_id="t3")
    new_cand = next(c for c in out if c.courier_id == "522")
    expect("522 score 110 → 80 (penalty -30)", new_cand.score == 80.0,
           f"got {new_cand.score}")
    expect("metrics penalty -30",
           new_cand.metrics.get("v325_new_courier_penalty") == -30)

    # ---------- T4: tier=new + bag=0 + advantage=10 → penalty -50 ----------
    print("\n=== T4: advantage=10 → penalty -50 ===")
    feasible = [
        _MockCandidate("522", 90.0, tier_label='new', bag_size=0),
        _MockCandidate("370", 80.0, tier_label='std+', bag_size=0),
    ]
    out = dispatch_pipeline._v325_new_courier_penalty(feasible, order_id="t4")
    new_cand = next(c for c in out if c.courier_id == "522")
    expect("522 score 90 → 40 (penalty -50)", new_cand.score == 40.0,
           f"got {new_cand.score}")
    expect("metrics penalty -50",
           new_cand.metrics.get("v325_new_courier_penalty") == -50)
    # Re-sort: 370 (80) > 522 (40) → 370 powinien być first
    expect("re-sort: 370 (80) wyprzedził 522 (40)",
           out[0].courier_id == "370",
           f"got order: {[c.courier_id for c in out]}")

    # ---------- T5: tier=new + bag=2 → HARD SKIP ----------
    print("\n=== T5: tier=new + bag=2 → HARD SKIP ===")
    feasible = [
        _MockCandidate("522", 200.0, tier_label='new', bag_size=2),
        _MockCandidate("370", 80.0, tier_label='std+', bag_size=0),
    ]
    out = dispatch_pipeline._v325_new_courier_penalty(feasible, order_id="t5")
    new_cand = next(c for c in out if c.courier_id == "522")
    expect("522 (bag=2 new) → score = -1e9 (HARD SKIP)",
           new_cand.score == -1e9, f"got {new_cand.score}")
    expect("522 flag mentions HARD SKIP",
           "HARD SKIP" in (new_cand.metrics.get("v325_new_courier_flag") or ""),
           f"got {new_cand.metrics.get('v325_new_courier_flag')!r}")
    # Re-sort: 370 powinien być first (522 sortuje na końcu)
    expect("re-sort: 370 first (522 demoted via -1e9)",
           out[0].courier_id == "370")

    # ---------- T6: tier=std+ → no change ----------
    print("\n=== T6: tier=std+ → no change ===")
    feasible = [_MockCandidate("370", 100.0, tier_label='std+', bag_size=2)]
    out = dispatch_pipeline._v325_new_courier_penalty(feasible, order_id="t6")
    expect("370 (std+) score unchanged at 100",
           out[0].score == 100.0)
    expect("370 metrics NIE ma v325_new_courier_penalty",
           "v325_new_courier_penalty" not in out[0].metrics)

    # ---------- T7: visual flag present ----------
    print("\n=== T7: visual flag w metrics ===")
    feasible = [
        _MockCandidate("522", 200.0, tier_label='new', bag_size=0),
        _MockCandidate("370", 140.0, tier_label='std+', bag_size=0),
    ]
    out = dispatch_pipeline._v325_new_courier_penalty(feasible, order_id="t7")
    flag = out[0].metrics.get("v325_new_courier_flag") if out[0].courier_id == "522" else None
    if flag is None:
        flag = next((c.metrics.get("v325_new_courier_flag") for c in out if c.courier_id == "522"), None)
    expect("flag string contains '🆕 NOWY KURIER'",
           flag and "🆕 NOWY KURIER" in flag,
           f"got {flag!r}")

    # ---------- T8: cs.tier_label propagation z courier_tiers.json ----------
    print("\n=== T8: cs.tier_label propagation (production probe) ===")
    importlib.reload(courier_resolver)
    fleet = courier_resolver.build_fleet_snapshot()
    cs_522 = fleet.get('522')
    cs_500 = fleet.get('500')
    cs_393 = fleet.get('393')
    expect("cid=522 (Szymon Sa) tier_label='new'",
           cs_522 and cs_522.tier_label == 'new')
    expect("cid=500 (Grzegorz R) tier_label='new'",
           cs_500 and cs_500.tier_label == 'new')
    expect("cid=393 (Michał K.) tier_label=None (NOT new)",
           cs_393 and cs_393.tier_label is None)

    # Cleanup
    del os.environ["ENABLE_V325_NEW_COURIER_CAP"]
    importlib.reload(common)
    importlib.reload(dispatch_pipeline)

    print(f"\n=== summary: {results['pass']} pass, {results['fail']} fail ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
