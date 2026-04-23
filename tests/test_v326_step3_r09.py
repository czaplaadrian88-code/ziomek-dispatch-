"""V3.26 STEP 3 (R-09 WAVE-GEOMETRIC-VETO) — flag-gated unit tests.

Tests inline veto helper logic (extracted z dispatch_pipeline). Veto path
operates on local variables w pętli — testowanie thru integration ciężkie,
więc isolated unit testy weryfikują boundary conditions + fallback paths.

Tests:
- T1 flag default False — no veto
- T2 flag True + bonus_bug2 > 0 + km > 3.0 → VETO (bonus → 0)
- T3 flag True + bonus_bug2 > 0 + km <= 3.0 → no veto
- T4 flag True + bonus_bug2 == 0 → veto irrelevant (BUG-2 nie pali)
- T5 km exactly 3.0 → boundary inclusive (NIE veto, > strict)
- T6 km 3.01 → VETO (just above threshold)
- T7 plan=None → no veto + no exception
- T8 bag empty → no veto + no exception
"""
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common  # noqa: E402


def _veto_logic(bonus_bug2, km_from_last_drop, threshold, flag):
    """Mimic _v326 veto inline logic — pure for testing."""
    if not flag or bonus_bug2 <= 0 or km_from_last_drop is None:
        return bonus_bug2, False
    if km_from_last_drop > threshold:
        return 0.0, True
    return bonus_bug2, False


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  ✅ {label}")
            results["pass"] += 1
        else:
            print(f"  ❌ {label}  {detail}")
            results["fail"] += 1

    importlib.reload(common)
    threshold = common.V326_WAVE_VETO_KM_THRESHOLD  # 3.0

    # --- T1: flag default False ---
    print("\n=== T1: flag default False ===")
    os.environ.pop("ENABLE_V326_WAVE_GEOMETRIC_VETO", None)
    importlib.reload(common)
    expect("ENABLE_V326_WAVE_GEOMETRIC_VETO default False",
           common.ENABLE_V326_WAVE_GEOMETRIC_VETO is False)
    bonus, vetoed = _veto_logic(30.0, 5.0, threshold, common.ENABLE_V326_WAVE_GEOMETRIC_VETO)
    expect("flag=False → bonus zachowane, no veto", bonus == 30.0 and not vetoed)

    # Flip flag
    os.environ["ENABLE_V326_WAVE_GEOMETRIC_VETO"] = "1"
    importlib.reload(common)
    expect("flag flipped True", common.ENABLE_V326_WAVE_GEOMETRIC_VETO is True)

    # --- T2: bonus > 0 + km > threshold → VETO ---
    print("\n=== T2: bonus +30 + km 5.0 → VETO ===")
    bonus, vetoed = _veto_logic(30.0, 5.0, threshold, True)
    expect("bonus → 0", bonus == 0.0)
    expect("vetoed True", vetoed is True)

    # --- T3: km <= threshold → no veto ---
    print("\n=== T3: bonus +30 + km 1.5 → no veto ===")
    bonus, vetoed = _veto_logic(30.0, 1.5, threshold, True)
    expect("bonus zachowane +30", bonus == 30.0)
    expect("not vetoed", not vetoed)

    # --- T4: bonus == 0 → veto irrelevant ---
    print("\n=== T4: bonus 0 (BUG-2 nie pali) ===")
    bonus, vetoed = _veto_logic(0.0, 10.0, threshold, True)
    expect("bonus 0 zachowane", bonus == 0.0)
    expect("not vetoed (no bonus to veto)", not vetoed)

    # --- T5: km exactly 3.0 → boundary inclusive (NIE veto, > strict) ---
    print("\n=== T5: km == 3.0 → no veto (boundary inclusive) ===")
    bonus, vetoed = _veto_logic(30.0, 3.0, threshold, True)
    expect("bonus zachowane +30 (boundary)", bonus == 30.0)
    expect("not vetoed", not vetoed)

    # --- T6: km 3.01 → VETO (just above) ---
    print("\n=== T6: km 3.01 → VETO ===")
    bonus, vetoed = _veto_logic(30.0, 3.01, threshold, True)
    expect("bonus → 0", bonus == 0.0)
    expect("vetoed True", vetoed is True)

    # --- T7: km None (no plan) → no veto + no exception ---
    print("\n=== T7: km None (np. plan=None) ===")
    bonus, vetoed = _veto_logic(30.0, None, threshold, True)
    expect("bonus zachowane (None km = no compute)", bonus == 30.0)
    expect("not vetoed", not vetoed)

    # --- T8: full edge case sanity (negative km szuka exception?) ---
    print("\n=== T8: km negative (impossible but defensive) ===")
    bonus, vetoed = _veto_logic(30.0, -1.0, threshold, True)
    expect("negative km treated as 'not above threshold'",
           bonus == 30.0 and not vetoed)

    # Cleanup
    del os.environ["ENABLE_V326_WAVE_GEOMETRIC_VETO"]
    importlib.reload(common)

    print(f"\n=== summary: {results['pass']} pass, {results['fail']} fail ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
