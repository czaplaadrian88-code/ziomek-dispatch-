"""V3.26 hotfix 2026-04-24 — button label formula aligned z compute_assign_time.

Pure function tests z mocked datetime.now().
Fixes inconsistency: button label used travel_min only, compute_assign_time
used max(travel_min, prep_min). Post-fix: both use same formula.

Formula: time_min = max(5, min(60, ceil(max(travel_min, prep_min) / 5) * 5))

8 cases:
- 5 main (Adrian spec): pre_shift clamp, in-shift normal, already-late,
  equal, prep<<travel
- 3 edges: no pickup_ready_at fallback, clamp 60, clamp 5
"""
import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import telegram_approver  # noqa: E402


def _run(travel_min, prep_min, order_id="test-123"):
    """Simulate build_keyboard for single candidate with travel_min + prep_min
    (translated do pickup_ready_at = frozen_now + prep_min)."""
    # Freeze now at fixed UTC
    frozen_now = datetime(2026, 4, 24, 10, 0, 0, tzinfo=timezone.utc)
    pickup_ready = frozen_now + timedelta(minutes=prep_min) if prep_min is not None else None
    pickup_ready_iso = pickup_ready.isoformat() if pickup_ready else None
    cand = {
        "courier_id": "999",
        "name": "Test Courier",
        "travel_min": travel_min,
    }
    # Mock datetime.now in telegram_approver module
    with mock.patch.object(telegram_approver, "datetime") as mock_dt:
        mock_dt.now.return_value = frozen_now
        mock_dt.fromisoformat = datetime.fromisoformat  # passthrough
        kbd = telegram_approver.build_keyboard(
            order_id, candidates=[cand], pickup_ready_at=pickup_ready_iso
        )
    # Extract time_min from first button text
    btn_text = kbd["inline_keyboard"][0][0]["text"]  # "✅ Test Courier {N}min"
    # Parse number: "✅ Test Courier 10min" → 10
    import re
    m = re.search(r'(\d+)min', btn_text)
    if not m:
        return None
    return int(m.group(1))


def main():
    results = {"pass": 0, "fail": 0}
    def expect(label, actual, expected):
        ok = actual == expected
        if ok:
            print(f"  ✅ {label}: got {actual}")
            results["pass"] += 1
        else:
            print(f"  ❌ {label}: got {actual}, expected {expected}")
            results["fail"] += 1

    importlib.reload(telegram_approver)

    # --- 5 main Adrian cases ---
    print("\n=== Main test cases (Adrian spec) ===")
    expect("T1 pre_shift clamp (468163 type) travel=3 prep=20",
           _run(3, 20), 20)
    expect("T2 in-shift normal travel=10 prep=5",
           _run(10, 5), 10)
    expect("T3 already-late travel=5 prep=-2",
           _run(5, -2), 5)
    expect("T4 equal travel=10 prep=10",
           _run(10, 10), 10)
    expect("T5 prep<<travel travel=15 prep=2",
           _run(15, 2), 15)

    # --- 3 edge cases ---
    print("\n=== Edge cases ===")
    # T6: no pickup_ready_at fallback (prep=0) → uses travel only
    expect("T6 prep_min fallback (pickup_ready=None) travel=8 → ceil(8/5)*5=10",
           _run(8, None), 10)
    # T7: clamp 60 upper
    expect("T7 clamp upper travel=100 prep=0",
           _run(100, 0), 60)
    # T8: clamp 5 lower
    expect("T8 clamp lower travel=0 prep=0",
           _run(0, 0), 5)

    # --- Bonus: verify callback_data carries same value ---
    print("\n=== Callback_data consistency ===")
    frozen_now = datetime(2026, 4, 24, 10, 0, 0, tzinfo=timezone.utc)
    pickup_ready = (frozen_now + timedelta(minutes=20)).isoformat()
    cand = {"courier_id": "999", "name": "X", "travel_min": 3}
    with mock.patch.object(telegram_approver, "datetime") as mock_dt:
        mock_dt.now.return_value = frozen_now
        mock_dt.fromisoformat = datetime.fromisoformat
        kbd = telegram_approver.build_keyboard("t1", candidates=[cand], pickup_ready_at=pickup_ready)
    btn = kbd["inline_keyboard"][0][0]
    # label "20min" + callback "ASSIGN:t1:999:20"
    expect("label and callback_data both carry 20",
           ("20min" in btn["text"]) and btn["callback_data"].endswith(":20"), True)

    print(f"\n=== summary: {results['pass']} pass, {results['fail']} fail ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
