"""V3.19f parse layer tests — czas_kuriera propagation.

Coverage:
  1. _czas_kuriera_to_datetime same-day (typical)
  2. _czas_kuriera_to_datetime wraparound forward (pickup 23:45 + czas_kuriera 00:15)
  3. _czas_kuriera_to_datetime wraparound backward (pickup 00:15 + czas_kuriera 23:45)
  4. _czas_kuriera_to_datetime None / malformed / out-of-range
  5. _czas_kuriera_to_datetime fallback do now gdy pickup_at null
  6. fetch_order_details merge czas_kuriera z top-level (Finding #1 fix)
  7. fetch_order_details unhandled keys → debug log, dropped
  8. normalize_order returns czas_kuriera_hhmm + czas_kuriera_warsaw
  9. normalize_order czas_kuriera missing → oba pola None
 10. Edge: czas_kuriera < pickup (panel error) → zapisany (warning w state layer)
"""
import json
import sys
import os
from datetime import datetime, timezone, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2 import panel_client
from dispatch_v2.panel_client import (
    _czas_kuriera_to_datetime,
    normalize_order,
    fetch_order_details,
)

WARSAW = ZoneInfo("Europe/Warsaw")

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


# ============================================================
print("=== V3.19f: _czas_kuriera_to_datetime ===")
# ============================================================

# Test 1 — same-day typical
pickup_c = datetime(2026, 4, 20, 12, 5, tzinfo=WARSAW)
out_c = _czas_kuriera_to_datetime("12:00", pickup_c)
expected_c = datetime(2026, 4, 20, 12, 0, tzinfo=WARSAW)
check("1. same-day: pickup 12:05 + czas=12:00 → 2026-04-20 12:00",
      out_c == expected_c, detail=f"got {out_c}")

# Test 2 — wraparound forward (Q5 case a)
pickup_a = datetime(2026, 4, 20, 23, 45, tzinfo=WARSAW)
out_a = _czas_kuriera_to_datetime("00:15", pickup_a)
expected_a = datetime(2026, 4, 21, 0, 15, tzinfo=WARSAW)
check("2. wraparound fwd: pickup 23:45 + czas=00:15 → 2026-04-21 00:15",
      out_a == expected_a, detail=f"got {out_a}")

# Test 3 — wraparound backward (Q5 case b)
pickup_b = datetime(2026, 4, 20, 0, 15, tzinfo=WARSAW)
out_b = _czas_kuriera_to_datetime("23:45", pickup_b)
expected_b = datetime(2026, 4, 19, 23, 45, tzinfo=WARSAW)
check("3. wraparound bwd: pickup 00:15 + czas=23:45 → 2026-04-19 23:45",
      out_b == expected_b, detail=f"got {out_b}")

# Test 4 — typical czasówka przedłużenie (pickup 16:42 + kurier 17:10)
pickup_d = datetime(2026, 4, 20, 16, 42, tzinfo=WARSAW)
out_d = _czas_kuriera_to_datetime("17:10", pickup_d)
expected_d = datetime(2026, 4, 20, 17, 10, tzinfo=WARSAW)
check("4. typical przedłużenie: pickup 16:42 + czas=17:10 → same day 17:10",
      out_d == expected_d, detail=f"got {out_d}")

# Test 5 — None input
check("5. None input → None", _czas_kuriera_to_datetime(None, pickup_c) is None)

# Test 6 — malformed input
check("6. malformed 'abc:def' → None",
      _czas_kuriera_to_datetime("abc:def", pickup_c) is None)

# Test 7 — out-of-range hour
check("7. out-of-range '25:00' → None",
      _czas_kuriera_to_datetime("25:00", pickup_c) is None)

# Test 8 — out-of-range minute
check("8. out-of-range '10:99' → None",
      _czas_kuriera_to_datetime("10:99", pickup_c) is None)

# Test 9 — fallback do now gdy pickup null
now_fallback = datetime(2026, 4, 20, 14, 0, tzinfo=WARSAW)
out_fb = _czas_kuriera_to_datetime("14:30", None, now_warsaw=now_fallback)
expected_fb = datetime(2026, 4, 20, 14, 30, tzinfo=WARSAW)
check("9. pickup=None + now=14:00 + czas=14:30 → 14:30 today",
      out_fb == expected_fb, detail=f"got {out_fb}")

# Test 10 — empty string
check("10. empty string → None",
      _czas_kuriera_to_datetime("", pickup_c) is None)

# ============================================================
print("\n=== V3.19f: fetch_order_details merge (Finding #1 fix) ===")
# ============================================================

# Test 11 — merge top-level czas_kuriera do zlecenie dict
_mock_response = json.dumps({
    "zlecenie": {"id": "123", "id_status_zamowienia": 3, "czas_odbioru": 15},
    "czas_kuriera": "17:10",
}).encode()


class _MockResp:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


def _mock_open_ok(req, timeout=10):
    return _MockResp(_mock_response)


with mock.patch.object(panel_client, "_open_with_relogin", _mock_open_ok), \
        mock.patch.object(panel_client, "login", return_value=(None, "tok", None)):
    out = fetch_order_details("123", "tok")
check("11. Finding #1 fix: zlecenie dict ma klucz czas_kuriera='17:10'",
      isinstance(out, dict) and out.get("czas_kuriera") == "17:10",
      detail=f"got {out}")

# Test 12 — brak top-level czas_kuriera → zlecenie bez tego klucza
_mock_response_no = json.dumps({
    "zlecenie": {"id": "456", "id_status_zamowienia": 3},
}).encode()


def _mock_open_no_ck(req, timeout=10):
    return _MockResp(_mock_response_no)


with mock.patch.object(panel_client, "_open_with_relogin", _mock_open_no_ck), \
        mock.patch.object(panel_client, "login", return_value=(None, "tok", None)):
    out2 = fetch_order_details("456", "tok")
check("12. No top-level czas_kuriera → zlecenie dict bez klucza",
      isinstance(out2, dict) and "czas_kuriera" not in out2)

# Test 13 — unhandled top-level keys loguje debug (smoke, nie patchujemy log)
_mock_response_extra = json.dumps({
    "zlecenie": {"id": "789", "id_status_zamowienia": 3},
    "czas_kuriera": "18:00",
    "unknown_field_xyz": "should_be_logged_as_debug",
}).encode()


def _mock_open_extra(req, timeout=10):
    return _MockResp(_mock_response_extra)


with mock.patch.object(panel_client, "_open_with_relogin", _mock_open_extra), \
        mock.patch.object(panel_client, "login", return_value=(None, "tok", None)):
    out3 = fetch_order_details("789", "tok")
check("13. Unhandled key NIE trafia do zlecenie dict",
      isinstance(out3, dict) and "unknown_field_xyz" not in out3)
check("13b. Explicit handled 'czas_kuriera' nadal merge'owane",
      isinstance(out3, dict) and out3.get("czas_kuriera") == "18:00")

# ============================================================
print("\n=== V3.19f: normalize_order 2 fields ===")
# ============================================================

# Test 14 — normalize_order z czas_kuriera → 2 fields output
raw_fixture = {
    "id": "467438",
    "id_status_zamowienia": 3,
    "id_kurier": 502,
    "czas_odbioru": 35,  # elastic
    "czas_odbioru_timestamp": "2026-04-20 17:06:20",
    "street": "Waszyngtona",
    "nr_domu": "24",
    "nr_mieszkania": "124",
    "czas_kuriera": "17:10",  # declared courier arrival
    "address": {"id": 186, "street": "Rynek Kościuszki 32", "name": "Grill Kebab"},
    "lokalizacja": {"name": "Białystok"},
}
norm = normalize_order(raw_fixture, "Grill Kebab")
check("14. normalize_order czas_kuriera_hhmm == '17:10'",
      norm.get("czas_kuriera_hhmm") == "17:10",
      detail=f"got {norm.get('czas_kuriera_hhmm')}")
check("14b. normalize_order czas_kuriera_warsaw is ISO Warsaw",
      isinstance(norm.get("czas_kuriera_warsaw"), str) and
      norm["czas_kuriera_warsaw"].startswith("2026-04-20T17:10") and
      "+02:00" in norm["czas_kuriera_warsaw"],
      detail=f"got {norm.get('czas_kuriera_warsaw')}")

# Test 15 — normalize_order bez czas_kuriera → oba pola None
raw_no_ck = {
    "id": "999",
    "id_status_zamowienia": 3,
    "id_kurier": 1,
    "czas_odbioru": 20,
    "czas_odbioru_timestamp": "2026-04-20 12:00:00",
    "street": "Test",
    "nr_domu": "1",
    "address": {"id": 1, "street": "Rest", "name": "R"},
    "lokalizacja": {"name": "Białystok"},
}
norm2 = normalize_order(raw_no_ck, "R")
check("15. Brak czas_kuriera → czas_kuriera_hhmm=None",
      norm2.get("czas_kuriera_hhmm") is None)
check("15b. Brak czas_kuriera → czas_kuriera_warsaw=None",
      norm2.get("czas_kuriera_warsaw") is None)

# Test 16 — edge: czas_kuriera < pickup (panel inconsistency)
# Nie rollback, persist zachowany (warning logowany w state_machine Step 3)
raw_weird = {
    "id": "888",
    "id_status_zamowienia": 3,
    "id_kurier": 1,
    "czas_odbioru": 30,
    "czas_odbioru_timestamp": "2026-04-20 17:00:00",  # pickup 17:00
    "street": "Test",
    "nr_domu": "1",
    "czas_kuriera": "16:30",  # kurier przed pickupem (weird)
    "address": {"id": 1, "street": "Rest", "name": "R"},
    "lokalizacja": {"name": "Białystok"},
}
norm3 = normalize_order(raw_weird, "R")
check("16. czas_kuriera < pickup persist (parse nie rollbackuje)",
      norm3.get("czas_kuriera_warsaw") is not None and
      norm3.get("czas_kuriera_hhmm") == "16:30")

# Test 17 — regression: pickup_at_warsaw dalej działa
check("17. Regression pickup_at_warsaw zachowany",
      norm.get("pickup_at_warsaw") is not None and
      norm.get("pickup_at_warsaw").startswith("2026-04-20T17:06"))

print("\n" + "=" * 60)
print(f"V3.19f PARSE: {passed}/{passed + failed} PASS"
      if failed == 0 else
      f"V3.19f PARSE: {passed}/{passed + failed} PASS, {failed} FAIL")
print("=" * 60)

if failed:
    sys.exit(1)
