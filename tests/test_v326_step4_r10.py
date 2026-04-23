"""V3.26 STEP 4 (R-10 FLEET-LOAD-BALANCE) — flag-gated regression.

Tests:
- T1 flag default False → no adjustment
- T2 fleet avg 2.5, candidate bag 1 → delta=-1.5 → bonus +15
- T3 fleet avg 2.5, candidate bag 4 → delta=+1.5 → penalty -15
- T4 fleet avg 2.5, candidate bag 2 → delta=-0.5 → no adjustment
- T5 fleet avg 2.5, candidate bag 3 → delta=+0.5 → no adjustment
- T6 empty fleet (no bag data) → fallback no adjustment, WARNING log
- T7 metrics propagated (v326_fleet_bag_avg, v326_fleet_load_delta, v326_fleet_load_adjustment)
- T8 re-sort: underloaded courier moves up rankings
"""
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common, dispatch_pipeline  # noqa: E402


class _MockCandidate:
    def __init__(self, courier_id, name, score, bag_size_before=0):
        self.courier_id = courier_id
        self.name = name
        self.score = score
        self.metrics = {"bag_size_before": bag_size_before}


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
    os.environ["ENABLE_V326_FLEET_LOAD_BALANCE"] = "0"
    importlib.reload(common)
    importlib.reload(dispatch_pipeline)
    expect("ENABLE_V326_FLEET_LOAD_BALANCE False (env override)",
           common.ENABLE_V326_FLEET_LOAD_BALANCE is False)
    feasible = [_MockCandidate('123', 'X', 100.0, bag_size_before=0)]
    candidates = [_MockCandidate('123', 'X', 100.0, bag_size_before=0),
                  _MockCandidate('124', 'Y', 50.0, bag_size_before=5)]
    out = dispatch_pipeline._v326_fleet_load_balance(feasible, candidates, "t1")
    expect("score unchanged", out[0].score == 100.0)

    # Flip flag
    os.environ["ENABLE_V326_FLEET_LOAD_BALANCE"] = "1"
    importlib.reload(common)
    importlib.reload(dispatch_pipeline)
    expect("flipped", common.ENABLE_V326_FLEET_LOAD_BALANCE is True)

    # ---------- Setup: fleet avg 2.5 ----------
    # candidates bag sizes: [1, 2, 3, 4] → avg = 2.5
    def _setup_avg_25():
        return [
            _MockCandidate('A', 'a', 100.0, bag_size_before=1),
            _MockCandidate('B', 'b', 100.0, bag_size_before=2),
            _MockCandidate('C', 'c', 100.0, bag_size_before=3),
            _MockCandidate('D', 'd', 100.0, bag_size_before=4),
        ]

    # ---------- T2: bag=1 → delta -1.5 → bonus +15 ----------
    print("\n=== T2: fleet avg 2.5, candidate bag 1 → +15 ===")
    candidates = _setup_avg_25()
    feasible = [c for c in candidates if c.courier_id == 'A']
    out = dispatch_pipeline._v326_fleet_load_balance(feasible, candidates, "t2")
    expect("bag=1 candidate score 100 → 115", out[0].score == 115.0,
           f"got {out[0].score}")
    expect("v326_fleet_bag_avg == 2.5", out[0].metrics["v326_fleet_bag_avg"] == 2.5)
    expect("v326_fleet_load_delta == -1.5", out[0].metrics["v326_fleet_load_delta"] == -1.5)
    expect("v326_fleet_load_adjustment == 15.0",
           out[0].metrics["v326_fleet_load_adjustment"] == 15.0)

    # ---------- T3: bag=4 → delta +1.5 → -15 ----------
    print("\n=== T3: fleet avg 2.5, candidate bag 4 → -15 ===")
    candidates = _setup_avg_25()
    feasible = [c for c in candidates if c.courier_id == 'D']
    out = dispatch_pipeline._v326_fleet_load_balance(feasible, candidates, "t3")
    expect("bag=4 score 100 → 85", out[0].score == 85.0, f"got {out[0].score}")

    # ---------- T4: bag=2 → delta -0.5 → 0 ----------
    print("\n=== T4: fleet avg 2.5, candidate bag 2 → 0 (within threshold) ===")
    candidates = _setup_avg_25()
    feasible = [c for c in candidates if c.courier_id == 'B']
    out = dispatch_pipeline._v326_fleet_load_balance(feasible, candidates, "t4")
    expect("bag=2 score unchanged 100", out[0].score == 100.0,
           f"got {out[0].score}")
    expect("adjustment 0", out[0].metrics["v326_fleet_load_adjustment"] == 0.0)

    # ---------- T5: bag=3 → delta +0.5 → 0 ----------
    print("\n=== T5: fleet avg 2.5, candidate bag 3 → 0 ===")
    candidates = _setup_avg_25()
    feasible = [c for c in candidates if c.courier_id == 'C']
    out = dispatch_pipeline._v326_fleet_load_balance(feasible, candidates, "t5")
    expect("bag=3 score unchanged 100", out[0].score == 100.0)

    # ---------- T6: empty fleet (no bag data) → fallback ----------
    print("\n=== T6: empty fleet → fallback no adjustment + WARNING ===")
    candidates = [_MockCandidate('X', 'x', 100.0)]
    candidates[0].metrics = {}  # no bag_size_before
    feasible = candidates[:]
    from unittest import mock as _mock
    with _mock.patch.object(dispatch_pipeline.log, 'warning') as mw:
        out = dispatch_pipeline._v326_fleet_load_balance(feasible, candidates, "t6")
        msgs = [str(c) for c in mw.call_args_list]
        expect("warning fired",
               any('brak bag_size data' in m for m in msgs),
               f"calls: {msgs}")
    expect("score unchanged on empty fleet",
           out[0].score == 100.0)

    # ---------- T7: metrics propagated ----------
    print("\n=== T7: metrics fully propagated ===")
    candidates = _setup_avg_25()
    feasible = [c for c in candidates if c.courier_id == 'A']
    out = dispatch_pipeline._v326_fleet_load_balance(feasible, candidates, "t7")
    m = out[0].metrics
    for k in ['v326_fleet_bag_avg', 'v326_fleet_load_delta', 'v326_fleet_load_adjustment']:
        expect(f"metric {k} present", k in m, f"got keys: {list(m.keys())}")

    # ---------- T8: re-sort underloaded moves up ----------
    print("\n=== T8: re-sort — underloaded courier moves up ===")
    candidates = _setup_avg_25()
    # Make BEST initially be high-bag (D bag=4 score 100), low-bag (A bag=1 score 95) is 2nd
    candidates[0].score = 95.0  # A
    candidates[3].score = 100.0  # D
    feasible = [candidates[3], candidates[0]]  # D first, A second
    out = dispatch_pipeline._v326_fleet_load_balance(feasible, candidates, "t8")
    # After: A 95+15=110, D 100-15=85 → A becomes first
    expect("A (underloaded) becomes BEST after re-sort",
           out[0].courier_id == 'A',
           f"got: {[c.courier_id for c in out]}")

    # Cleanup
    del os.environ["ENABLE_V326_FLEET_LOAD_BALANCE"]
    importlib.reload(common)
    importlib.reload(dispatch_pipeline)

    print(f"\n=== summary: {results['pass']} pass, {results['fail']} fail ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
