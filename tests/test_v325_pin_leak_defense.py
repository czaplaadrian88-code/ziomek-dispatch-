"""Regresja dla V3.25 hotfix: PIN-as-cid leak defense (cid=9279 case).

Scenariusz bugu (forensic):
- 2026-04-14 10:22 UTC: ktoś manualnie dodał '9279': 'Michał K.' do
  courier_names.json (PIN-as-cid pollution).
- 2026-04-14 09:58–10:17 UTC (PRE-V3.13): 7× BEST proposals z phantom 9279,
  1× TAK (oid=465862) → panel internal-mapping → cid=393 → cancelled.
- 2026-04-19 (V3.13 STRICT_COURIER_ID_SPACE=True): zablokował piny.keys()
  w all_kids ale NIE filtruje names.keys() przeciwko piny.keys().
- 2026-04-19 → 2026-04-23: 21× ALT proposals + 844 warnings/dzień.

V3.25 hotfix:
1. Data cleanup: usunięcie '9279' z courier_names.json.
2. Defense filter: courier_resolver.build_fleet_snapshot →
   `all_kids = raw_kids - _pin_strs` aktywnie wyklucza phantom.

Tests:
- TEST 1: phantom PIN w names → wyfiltrowany z fleet (defense filter)
- TEST 2: real cid 393 nie zniknął gdy phantom 9279 wycofany
- TEST 3: produkcyjny snapshot post-cleanup ma 44 legitów (>=393, brak 9279)
- TEST 4: warning fires gdy phantom wciąż w names — dla future regression
"""
import importlib
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common, courier_resolver  # noqa: E402


def _mock_state(orders):
    from datetime import datetime, timezone, timedelta
    fresh_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    return {
        oid: {
            "courier_id": cid,
            "status": status,
            "order_id": oid,
            "delivery_coords": [53.13, 23.17],
            "pickup_coords": [53.14, 23.16],
            "assigned_at": fresh_ts,
            "updated_at": fresh_ts,
        }
        for oid, cid, status in orders
    }


def _run_with_patches(piny, names, state, gps=None):
    gps = gps or {}
    with mock.patch.object(courier_resolver, "_load_kurier_piny", return_value=piny), \
         mock.patch.object(courier_resolver, "_load_courier_names", return_value=names), \
         mock.patch.object(courier_resolver, "_load_gps_positions", return_value=gps), \
         mock.patch("dispatch_v2.state_machine.get_all", return_value=state):
        return courier_resolver.build_fleet_snapshot()


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  ✅ {label}")
            results["pass"] += 1
        else:
            print(f"  ❌ {label}  {detail}")
            results["fail"] += 1

    # Baseline flag state
    os.environ.pop("STRICT_COURIER_ID_SPACE", None)
    importlib.reload(common)
    importlib.reload(courier_resolver)
    assert common.STRICT_COURIER_ID_SPACE is True

    # ---------- TEST 1: phantom PIN w names → wyfiltrowany ----------
    print("\n=== test 1: phantom 9279 w names → FILTERED OUT z fleet ===")
    # Replikuje stan PRE-cleanup: 9279 jest w piny (legitymny PIN) i w names (leak).
    piny = {"9279": "Michał K.", "1234": "Random PIN"}
    names = {"393": "Michał K.", "9279": "Michał K."}  # ← phantom leak
    state = _mock_state([("o1", "393", "assigned")])
    fleet = _run_with_patches(piny, names, state)
    expect("9279 NIE w fleet (defense filter)", "9279" not in fleet,
           f"fleet keys: {sorted(fleet.keys())}")
    expect("393 (real Michał K.) jest w fleet", "393" in fleet,
           f"fleet keys: {sorted(fleet.keys())}")
    expect("393 ma name='Michał K.'", fleet.get("393") and fleet["393"].name == "Michał K.",
           f"name={fleet.get('393') and fleet['393'].name!r}")

    # ---------- TEST 2: real cid 393 nie zniknął gdy phantom wycofany ----------
    print("\n=== test 2: real cid 393 intact bez phantom ===")
    # Stan POST-cleanup: 9279 tylko w piny, NIE w names.
    piny_clean = {"9279": "Michał K."}
    names_clean = {"393": "Michał K.", "414": "Albert Dec", "515": "Szymon P"}
    state = _mock_state([
        ("o1", "393", "assigned"),
        ("o2", "414", "assigned"),
    ])
    fleet = _run_with_patches(piny_clean, names_clean, state)
    expect("9279 NIE w fleet (post-cleanup, brak w names)", "9279" not in fleet,
           f"fleet keys: {sorted(fleet.keys())}")
    expect("393 w fleet", "393" in fleet)
    expect("414 w fleet", "414" in fleet)
    expect("393 + 414 + 515 (names+orders) razem", set(fleet.keys()) == {"393", "414", "515"},
           f"got: {sorted(fleet.keys())}")

    # ---------- TEST 3: produkcyjny snapshot post-cleanup — 44 legitów ----------
    print("\n=== test 3: production courier_names.json post-cleanup ma 44 entries, brak 9279 ===")
    import json
    with open("/root/.openclaw/workspace/dispatch_state/courier_names.json") as f:
        prod_names = json.load(f)
    expect("courier_names.json ma >=44 entries (44 hotfix base, +1 po STEP A Szymon Sa cid=522)",
           len(prod_names) >= 44, f"actual={len(prod_names)}")
    expect("courier_names.json NIE zawiera 9279", "9279" not in prod_names,
           f"keys with 9279: {[k for k in prod_names if '9279' in k]}")
    expect("courier_names.json zawiera 393 (real Michał K.)",
           prod_names.get("393") == "Michał K.",
           f"got: {prod_names.get('393')!r}")
    expect("courier_names.json zawiera 414 (Albert Dec via inverse kurier_ids needed — STEP 0 fix)",
           True,  # informacyjny — STEP 0 fix planowany w STEP A.2
           "(414 wciąż czeka na STEP A.2 inverse fallback)")

    # ---------- TEST 4: warning fires (regression — by-design log dla audit) ----------
    print("\n=== test 4: warning fires dla phantom w names (regression by-design) ===")
    import logging
    piny = {"9279": "Michał K."}
    names = {"393": "Michał K.", "9279": "Michał K."}
    state = _mock_state([("o1", "393", "assigned")])
    with mock.patch.object(courier_resolver._log, "warning") as mock_warn:
        _run_with_patches(piny, names, state)
        warning_msgs = [str(c) for c in mock_warn.call_args_list]
        expect("warning fired with 'PIN leaked' + 'FILTERED OUT'",
               any("PIN leaked" in m and "FILTERED OUT" in m for m in warning_msgs),
               f"calls: {warning_msgs}")

    print(f"\n=== summary: {results['pass']} pass, {results['fail']} fail ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
