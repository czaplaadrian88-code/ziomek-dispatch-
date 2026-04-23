"""V3.26 STEP 1 (R-11 TRANSPARENCY-RATIONALE) — flag-gated regression.

Tests:
- T1 flag default False → rationale None
- T2 flag True + normal best → top_3_factors + dominant + advantage + dlaczego PL
- T3 advantage < 5 → close_call True (warning)
- T4 advantage > 50 → clear_winner True
- T5 brak czynników (zero metrics) → "brak wyróżniających czynników" string
- T6 backwards compat — old learning_log entry bez v326_rationale czytalny
- T7 LOCATION A consistent z LOCATION B (rationale field present w obu)
- T8 telegram _reason_line: gdy flag True + rationale — używa rationale string
"""
import importlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common, dispatch_pipeline, shadow_dispatcher  # noqa: E402


class _MockCandidate:
    def __init__(self, courier_id, name, score, metrics=None,
                 feasibility_verdict='MAYBE', feasibility_reason='ok'):
        self.courier_id = courier_id
        self.name = name
        self.score = score
        self.metrics = metrics or {}
        self.feasibility_verdict = feasibility_verdict
        self.feasibility_reason = feasibility_reason
        self.plan = None
        self.best_effort = False


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  ✅ {label}")
            results["pass"] += 1
        else:
            print(f"  ❌ {label}  {detail}")
            results["fail"] += 1

    # ---------- T1: flag default False ----------
    print("\n=== T1: flag default False → rationale=None ===")
    os.environ.pop("ENABLE_V326_TRANSPARENCY_RATIONALE", None)
    importlib.reload(common)
    importlib.reload(dispatch_pipeline)
    expect("ENABLE_V326_TRANSPARENCY_RATIONALE default False",
           common.ENABLE_V326_TRANSPARENCY_RATIONALE is False)
    best = _MockCandidate('123', 'Bartek O.', 100.0, {'bundle_bonus': 30})
    r = dispatch_pipeline._v326_build_rationale(best, [best])
    expect("rationale = None gdy flag False", r is None)

    # Flip flag
    os.environ["ENABLE_V326_TRANSPARENCY_RATIONALE"] = "1"
    importlib.reload(common)
    importlib.reload(dispatch_pipeline)
    expect("flag flipped True via env", common.ENABLE_V326_TRANSPARENCY_RATIONALE is True)

    # ---------- T2: normal best → factors + advantage + PL ----------
    print("\n=== T2: normal best → top_3 + advantage + dlaczego PL ===")
    best = _MockCandidate('123', 'Bartek O.', 100.0, {
        'km_to_pickup': 1.5,
        'bundle_bonus': 22,
        'v319h_bug2_continuation_bonus': 30,
        'bonus_r9_stopover': -8,
    })
    next_b = _MockCandidate('370', 'Jakub OL', 78.0, {})
    r = dispatch_pipeline._v326_build_rationale(best, [best, next_b])
    expect("top_3_factors len <= 3", len(r['top_3_factors']) <= 3,
           f"got {len(r['top_3_factors'])}")
    expect("dominant_factor obecny", r['dominant_factor'] is not None)
    expect("advantage_vs_next == 22.0",
           r['advantage_vs_next'] == 22.0,
           f"got {r['advantage_vs_next']}")
    expect("next_best_name == 'Jakub OL'", r['next_best_name'] == 'Jakub OL')
    expect("dlaczego zawiera PL słowa (trajektoria/fala/przewaga)",
           any(w in r['dlaczego'] for w in ['trajektoria', 'fala', 'przewaga']),
           f"got {r['dlaczego']!r}")
    # Dominant = trajektoria (contribution 30 > fala 22)
    expect("dominant_factor == 'trajektoria'", r['dominant_factor'] == 'trajektoria',
           f"got {r['dominant_factor']!r}")

    # ---------- T3: close_call (advantage < 5) ----------
    print("\n=== T3: advantage 3 → close_call True ===")
    next_b3 = _MockCandidate('370', 'Jakub OL', 97.0, {})  # advantage = 3
    r = dispatch_pipeline._v326_build_rationale(best, [best, next_b3])
    expect("close_call == True dla advantage 3", r['close_call'] is True,
           f"advantage={r['advantage_vs_next']} close_call={r['close_call']}")
    expect("dlaczego zawiera 'close call'",
           "close call" in r['dlaczego'].lower(),
           f"got {r['dlaczego']!r}")

    # ---------- T4: clear_winner (advantage > 50) ----------
    print("\n=== T4: advantage 60 → clear_winner True ===")
    next_b4 = _MockCandidate('370', 'Jakub OL', 40.0, {})  # advantage = 60
    r = dispatch_pipeline._v326_build_rationale(best, [best, next_b4])
    expect("clear_winner == True dla advantage 60", r['clear_winner'] is True,
           f"advantage={r['advantage_vs_next']}")
    expect("dlaczego zawiera 'clear winner'",
           "clear winner" in r['dlaczego'].lower(), f"got {r['dlaczego']!r}")

    # ---------- T5: brak czynników (zero contributions) ----------
    print("\n=== T5: zero metrics → 'brak wyróżniających' string ===")
    empty = _MockCandidate('999', 'Empty', 50.0, {})
    r = dispatch_pipeline._v326_build_rationale(empty, [empty])
    expect("top_3 empty",
           r['top_3_factors'] == [], f"got {r['top_3_factors']}")
    expect("dominant None", r['dominant_factor'] is None)
    expect("dlaczego zawiera 'brak wyróżniających'",
           "brak wyróżniających" in r['dlaczego'], f"got {r['dlaczego']!r}")

    # ---------- T6: backwards compat — old metrics bez v326_rationale ----------
    print("\n=== T6: serializer backwards compat ===")
    importlib.reload(shadow_dispatcher)
    cand = _MockCandidate('123', 'Bartek O.', 90.0, {})
    cand.feasibility_verdict = 'MAYBE'
    cand.feasibility_reason = 'ok'
    cand.plan = None
    sc = shadow_dispatcher._serialize_candidate(cand)
    expect("v326_rationale field obecny w serialized (None gdy brak)",
           "v326_rationale" in sc and sc["v326_rationale"] is None,
           f"got {sc.get('v326_rationale')!r}")

    # ---------- T7: LOCATION A + B consistency ----------
    print("\n=== T7: serializer LOCATION A + B mają v326_rationale ===")
    cand2 = _MockCandidate('123', 'Bartek', 90.0, {'v326_rationale': {'dlaczego': 'test'}})
    cand2.feasibility_verdict = 'MAYBE'
    cand2.feasibility_reason = 'ok'
    cand2.plan = None
    sc_a = shadow_dispatcher._serialize_candidate(cand2)  # LOCATION A
    expect("LOCATION A propagates v326_rationale",
           sc_a.get("v326_rationale", {}).get("dlaczego") == "test")
    # LOCATION B test wymaga PipelineResult — tylko sprawdź że kod ma propagation
    import inspect
    src = inspect.getsource(shadow_dispatcher._serialize_result)
    expect("LOCATION B (_serialize_result) zawiera 'v326_rationale'",
           '"v326_rationale"' in src, "search 'v326_rationale' w _serialize_result source")

    # ---------- T8: telegram _reason_line z rationale ----------
    print("\n=== T8: telegram _reason_line używa v326_rationale gdy flag True ===")
    from dispatch_v2 import telegram_approver
    importlib.reload(telegram_approver)
    c_with_rat = {
        'km_to_pickup': 2.0,
        'bundle_level1': None,
        'bundle_level2': None,
        'bundle_level3': None,
        'free_at_min': 5,
        'v326_rationale': {'dlaczego': 'fala +22, trajektoria +30 · przewaga +18'},
    }
    line = telegram_approver._reason_line(c_with_rat, [c_with_rat])
    expect("_reason_line zawiera 'fala +22'", "fala +22" in line, f"got {line!r}")
    expect("_reason_line ma '💡' prefix", "💡" in line)

    # Cleanup
    del os.environ["ENABLE_V326_TRANSPARENCY_RATIONALE"]
    importlib.reload(common)
    importlib.reload(dispatch_pipeline)
    importlib.reload(telegram_approver)

    print(f"\n=== summary: {results['pass']} pass, {results['fail']} fail ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
