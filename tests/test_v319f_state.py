"""V3.19f state layer tests — czas_kuriera persist + sanity check.

Coverage:
  1. NEW_ORDER z czas_kuriera → state ma oba pola (ISO + HH:MM)
  2. NEW_ORDER bez czas_kuriera → oba pola None
  3. COURIER_ASSIGNED z czas_kuriera update (przedłużenie) → state update
  4. COURIER_ASSIGNED bez czas_kuriera → nie nadpisuje, zachowuje istniejące
  5. Sanity check mismatch (ISO 17:10 + HH:MM 17:20) → CorruptedTimestampError
  6. Sanity partial (jedno pole None) → CorruptedTimestampError
  7. Backward compat — stary rekord bez pola, read works
  8. _verify_czas_kuriera_consistency helper edge cases

Pattern: tmp state file, patch _state_path. Cleanup po każdym teście.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2 import state_machine
from dispatch_v2.state_machine import (
    CorruptedTimestampError,
    _verify_czas_kuriera_consistency,
    update_from_event,
    get_order,
    set_status,
)

passed = 0
failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK {passed}. {label}")
    else:
        failed += 1
        print(f"  FAIL {passed + failed}. {label} {detail}")


class _TmpState:
    """Context manager: temp state file, patched _state_path."""

    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="v319f_state_")
        self.path = os.path.join(self.tmpdir, "orders.json")
        with open(self.path, "w") as f:
            json.dump({}, f)
        self._orig = state_machine._state_path
        state_machine._state_path = lambda: self.path
        return self

    def __exit__(self, *a):
        state_machine._state_path = self._orig
        try:
            os.unlink(self.path)
            os.unlink(self.path + ".lock")
        except FileNotFoundError:
            pass
        try:
            os.rmdir(self.tmpdir)
        except OSError:
            pass


# ============================================================
print("=== V3.19f state: sanity check helper ===")
# ============================================================

# Test 1 — helper both None → True
check("1. helper: both None → True (no-op)",
      _verify_czas_kuriera_consistency(None, None, "oid1") is True)

# Test 2 — helper partial (ISO only) → False
check("2. helper: partial ISO only → False",
      _verify_czas_kuriera_consistency("2026-04-20T17:10:00+02:00", None, "oid2") is False)

# Test 3 — helper partial (HH:MM only) → False
check("3. helper: partial HH:MM only → False",
      _verify_czas_kuriera_consistency(None, "17:10", "oid3") is False)

# Test 4 — helper consistent ISO + HH:MM → True
check("4. helper: ISO 17:10 + HH:MM 17:10 → True",
      _verify_czas_kuriera_consistency(
          "2026-04-20T17:10:00+02:00", "17:10", "oid4") is True)

# Test 5 — helper mismatch → False
check("5. helper: ISO 17:10 + HH:MM 17:20 → False (mismatch)",
      _verify_czas_kuriera_consistency(
          "2026-04-20T17:10:00+02:00", "17:20", "oid5") is False)

# Test 6 — helper malformed ISO → False
check("6. helper: malformed ISO → False",
      _verify_czas_kuriera_consistency("not-iso", "17:10", "oid6") is False)

# Test 7 — helper wraparound: ISO next-day 00:15 + HH:MM 00:15 → True
check("7. helper: wraparound ISO next-day + HH:MM → True",
      _verify_czas_kuriera_consistency(
          "2026-04-21T00:15:00+02:00", "00:15", "oid7") is True)

# ============================================================
print("\n=== V3.19f state: NEW_ORDER persist ===")
# ============================================================

# Test 8 — NEW_ORDER z oba polami → state ma oba pola
with _TmpState():
    update_from_event({
        "event_type": "NEW_ORDER",
        "order_id": "100",
        "payload": {
            "restaurant": "R",
            "pickup_at_warsaw": "2026-04-20T17:06:00+02:00",
            "czas_kuriera_warsaw": "2026-04-20T17:10:00+02:00",
            "czas_kuriera_hhmm": "17:10",
        },
    })
    o = get_order("100")
    check("8. NEW_ORDER persist czas_kuriera_warsaw",
          o.get("czas_kuriera_warsaw") == "2026-04-20T17:10:00+02:00")
    check("8b. NEW_ORDER persist czas_kuriera_hhmm",
          o.get("czas_kuriera_hhmm") == "17:10")

# Test 9 — NEW_ORDER bez czas_kuriera → oba None
with _TmpState():
    update_from_event({
        "event_type": "NEW_ORDER",
        "order_id": "200",
        "payload": {
            "restaurant": "R",
            "pickup_at_warsaw": "2026-04-20T17:06:00+02:00",
            # brak czas_kuriera
        },
    })
    o = get_order("200")
    check("9. NEW_ORDER bez pola: czas_kuriera_warsaw None",
          o.get("czas_kuriera_warsaw") is None)
    check("9b. NEW_ORDER bez pola: czas_kuriera_hhmm None",
          o.get("czas_kuriera_hhmm") is None)

# Test 10 — NEW_ORDER mismatch → CorruptedTimestampError
with _TmpState():
    try:
        update_from_event({
            "event_type": "NEW_ORDER",
            "order_id": "300",
            "payload": {
                "restaurant": "R",
                "czas_kuriera_warsaw": "2026-04-20T17:10:00+02:00",
                "czas_kuriera_hhmm": "17:20",  # MISMATCH
            },
        })
        check("10. NEW_ORDER mismatch → raise CorruptedTimestampError", False,
              detail="no exception raised")
    except CorruptedTimestampError:
        # Record persisted bez czas_kuriera fields
        o = get_order("300")
        check("10. NEW_ORDER mismatch → raise CorruptedTimestampError", True)
        check("10b. Mismatch path: czas_kuriera_warsaw NOT persisted",
              o.get("czas_kuriera_warsaw") is None)
        check("10c. Mismatch path: inne pola persistowane",
              o.get("restaurant") == "R" and o.get("status") == "planned")

# ============================================================
print("\n=== V3.19f state: COURIER_ASSIGNED update ===")
# ============================================================

# Test 11 — COURIER_ASSIGNED z czas_kuriera → update
with _TmpState():
    update_from_event({
        "event_type": "NEW_ORDER",
        "order_id": "400",
        "payload": {"restaurant": "R"},
    })
    update_from_event({
        "event_type": "COURIER_ASSIGNED",
        "order_id": "400",
        "courier_id": "502",
        "payload": {
            "czas_kuriera_warsaw": "2026-04-20T17:10:00+02:00",
            "czas_kuriera_hhmm": "17:10",
        },
    })
    o = get_order("400")
    check("11. COURIER_ASSIGNED z polem → persist",
          o.get("czas_kuriera_warsaw") == "2026-04-20T17:10:00+02:00")
    check("11b. status=assigned, courier_id=502",
          o.get("status") == "assigned" and o.get("courier_id") == "502")

# Test 12 — COURIER_ASSIGNED bez pola → zachowuje istniejące z NEW_ORDER
with _TmpState():
    update_from_event({
        "event_type": "NEW_ORDER",
        "order_id": "500",
        "payload": {
            "restaurant": "R",
            "czas_kuriera_warsaw": "2026-04-20T17:10:00+02:00",
            "czas_kuriera_hhmm": "17:10",
        },
    })
    update_from_event({
        "event_type": "COURIER_ASSIGNED",
        "order_id": "500",
        "courier_id": "502",
        "payload": {},  # brak czas_kuriera
    })
    o = get_order("500")
    check("12. COURIER_ASSIGNED bez pola → zachowuje NEW_ORDER value",
          o.get("czas_kuriera_warsaw") == "2026-04-20T17:10:00+02:00",
          detail=f"got {o.get('czas_kuriera_warsaw')}")

# Test 13 — przedłużenie (panel +15min) → update
with _TmpState():
    update_from_event({
        "event_type": "NEW_ORDER",
        "order_id": "600",
        "payload": {
            "restaurant": "R",
            "czas_kuriera_warsaw": "2026-04-20T17:10:00+02:00",
            "czas_kuriera_hhmm": "17:10",
        },
    })
    update_from_event({
        "event_type": "COURIER_ASSIGNED",
        "order_id": "600",
        "courier_id": "502",
        "payload": {
            # panel "+15min" przedłużenie
            "czas_kuriera_warsaw": "2026-04-20T17:25:00+02:00",
            "czas_kuriera_hhmm": "17:25",
        },
    })
    o = get_order("600")
    check("13. Przedłużenie: update z 17:10 → 17:25",
          o.get("czas_kuriera_hhmm") == "17:25")

# Test 14 — COURIER_ASSIGNED mismatch → CorruptedTimestampError
with _TmpState():
    update_from_event({
        "event_type": "NEW_ORDER",
        "order_id": "700",
        "payload": {"restaurant": "R"},
    })
    try:
        update_from_event({
            "event_type": "COURIER_ASSIGNED",
            "order_id": "700",
            "courier_id": "502",
            "payload": {
                "czas_kuriera_warsaw": "2026-04-20T17:10:00+02:00",
                "czas_kuriera_hhmm": "17:30",  # MISMATCH
            },
        })
        check("14. COURIER_ASSIGNED mismatch → raise", False,
              detail="no exception raised")
    except CorruptedTimestampError:
        o = get_order("700")
        check("14. COURIER_ASSIGNED mismatch → raise CorruptedTimestampError", True)
        check("14b. Mismatch: status=assigned + brak czas_kuriera update",
              o.get("status") == "assigned" and
              o.get("czas_kuriera_warsaw") is None)

# ============================================================
print("\n=== V3.19f state: backward compat ===")
# ============================================================

# Test 15 — stary rekord bez pola → get_order zwraca None dla tego pola
with _TmpState() as t:
    with open(t.path, "w") as f:
        json.dump({
            "999": {
                "order_id": "999",
                "status": "assigned",
                "restaurant": "Legacy",
                # brak czas_kuriera_* — stare rekordy
            }
        }, f)
    o = get_order("999")
    check("15. Legacy record bez pola → czas_kuriera_warsaw None",
          o.get("czas_kuriera_warsaw") is None)
    check("15b. Legacy record nadal readable",
          o.get("restaurant") == "Legacy")

print("\n" + "=" * 60)
print(f"V3.19f STATE: {passed}/{passed + failed} PASS"
      if failed == 0 else
      f"V3.19f STATE: {passed}/{passed + failed} PASS, {failed} FAIL")
print("=" * 60)

if failed:
    sys.exit(1)
