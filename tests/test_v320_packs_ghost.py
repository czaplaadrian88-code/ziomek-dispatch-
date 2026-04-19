"""V3.20 tests — packs ghost detect w panel_watcher._diff_and_emit.

Mock parse_panel_html output + fetch_order_details + state_get_all +
emit/update_from_event. Testujemy tylko logikę filtrowania / guards
przez bezpośrednie wywołanie helper — panel_watcher niestety nie ma
osobnej pure func, więc monkey-patchujemy moduly i wywołujemy _diff_and_emit.
"""
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2 import panel_watcher as pw
from dispatch_v2 import state_machine as sm
from dispatch_v2 import panel_client as pc
from dispatch_v2 import common as cm
from dispatch_v2 import event_bus as eb

passed = 0
failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK {passed}. {label}")
    else:
        failed += 1
        print(f"  FAIL {passed + failed}. {label}")


# ---- mocks ----

_EMIT_CALLS = []


def _mock_emit(event_type, order_id=None, courier_id=None, payload=None, event_id=None):
    rec = {
        "event_type": event_type,
        "order_id": order_id,
        "courier_id": courier_id,
        "payload": payload,
        "event_id": event_id,
    }
    _EMIT_CALLS.append(rec)
    return rec  # truthy → ev is kept


def _mock_update_from_event(ev):
    pass  # no-op for tests


_STATE = {}


def _mock_state_get_all():
    return _STATE


def _mock_state_get_order(oid):
    return _STATE.get(str(oid))


_FETCH_DETAILS = {}


def _mock_fetch_order_details(zid, csrf):
    return _FETCH_DETAILS.get(str(zid))


def _mock_parse_panel_html(html):
    return _PARSED


_PARSED = {}


def _mock_fetch_panel_html():
    return "dummy html"


def _mock_check_panel_override(oid, cid, source):
    pass


def _mock_health_check():
    return {}


_KID_JSON = None


def _install_mocks():
    pw.emit = _mock_emit
    pw.update_from_event = _mock_update_from_event
    pw.state_get_all = _mock_state_get_all
    pw.state_get_order = _mock_state_get_order
    pw.fetch_order_details = _mock_fetch_order_details
    pw.parse_panel_html = _mock_parse_panel_html
    pw.fetch_panel_html = _mock_fetch_panel_html
    pw._check_panel_override = _mock_check_panel_override
    pw._save_plan_on_assign = lambda *a, **kw: None
    pw._advance_plan_on_deliver = lambda *a, **kw: None
    pw._remove_stops_on_return = lambda *a, **kw: None
    pw._update_plan_on_picked_up = lambda *a, **kw: None


def _reset():
    global _EMIT_CALLS
    _EMIT_CALLS = []
    _STATE.clear()
    _FETCH_DETAILS.clear()


def _now_utc():
    return datetime.now(timezone.utc)


def _iso_ago(min_ago):
    return (_now_utc() - timedelta(minutes=min_ago)).isoformat()


# Patch kurier_ids.json load path — use tmpdir
import tempfile
_TMPDIR = Path(tempfile.mkdtemp(prefix="v320_test_"))
_KID_PATH = _TMPDIR / "kurier_ids.json"


# Monkey-patch the json loader section to use our tmpfile.
# Easier: replace builtins.open for panel_watcher — nope, too invasive.
# Alternative: write kurier_ids.json to the real path but use fixture names.
# Safer: just exercise the V3.20 section directly by calling _diff_and_emit
# with carefully crafted parsed + state. Ensure real kurier_ids.json path
# exists with needed nicks.
_REAL_KID = Path("/root/.openclaw/workspace/dispatch_state/kurier_ids.json")


def _load_real_kid():
    import json
    with open(_REAL_KID) as f:
        return json.load(f)


_REAL_KIDS = _load_real_kid()
# Pick first stable cid from real file for mocking (avoid ambiguous)
_TEST_CID = None
_TEST_NICK = None
_cid_seen_nicks = {}
for _n, _c in _REAL_KIDS.items():
    _cid_seen_nicks.setdefault(str(_c), []).append(_n)
for _c, _ns in _cid_seen_nicks.items():
    if len(_ns) == 1:
        _name_counts = sum(1 for k in _REAL_KIDS if k == _ns[0])
        _TEST_CID = _c
        _TEST_NICK = _ns[0]
        break
if not _TEST_CID:
    _TEST_CID = str(list(_REAL_KIDS.values())[0])
    _TEST_NICK = [n for n, c in _REAL_KIDS.items() if str(c) == _TEST_CID][0]


_install_mocks()

# ============================================================
print("=== V3.20: packs ghost detect ===")
print(f"  (using test_cid={_TEST_CID} nick={_TEST_NICK!r})")
# ============================================================


def _run_diff(parsed):
    global _PARSED
    _PARSED = parsed
    return pw._diff_and_emit(parsed, csrf="dummy")


# Test 1 — ghost detected: order in state assigned to CID, panel packs missing it
_reset()
_STATE["GHOST1"] = {
    "status": "picked_up",
    "courier_id": _TEST_CID,
    "assigned_at": _iso_ago(10),
    "delivery_address": "Test Addr",
    "delivery_coords": [53.15, 23.20],
}
_FETCH_DETAILS["GHOST1"] = {
    "id_status_zamowienia": 7,
    "id_kurier": _TEST_CID,
    "czas_doreczenia": "2026-04-19 22:00:00",
}
parsed = {
    "order_ids": [],
    "assigned_ids": set(),
    "closed_ids": set(),  # ghost detect runs BEFORE reconcile section
    "rest_names": {},
    "courier_packs": {_TEST_NICK: []},  # nick jest w packs, ale GHOST1 tam NIE ma
    "delivery_addresses": {"GHOST1": "Test Addr"},
}
stats = _run_diff(parsed)
ghost_emits = [e for e in _EMIT_CALLS if (e.get("payload") or {}).get("source") == "packs_ghost_detect"]
check("ghost emit for missing oid in packs", len(ghost_emits) == 1)
check("stats[packs_ghost_detect]=1", stats.get("packs_ghost_detect") == 1)

# Test 2 — no ghost when oid IN packs
_reset()
_STATE["OK1"] = {
    "status": "picked_up",
    "courier_id": _TEST_CID,
    "assigned_at": _iso_ago(10),
}
parsed = {
    "order_ids": [],
    "assigned_ids": set(),
    "closed_ids": set(),
    "rest_names": {},
    "courier_packs": {_TEST_NICK: ["OK1"]},  # oid obecny
    "delivery_addresses": {},
}
_run_diff(parsed)
ghost_emits = [e for e in _EMIT_CALLS if (e.get("payload") or {}).get("source") == "packs_ghost_detect"]
check("oid in packs → no ghost emit", len(ghost_emits) == 0)

# Test 3 — skip when nick NOT in packs at all (kurier off-shift)
_reset()
_STATE["OFF1"] = {
    "status": "picked_up",
    "courier_id": _TEST_CID,
    "assigned_at": _iso_ago(10),
}
parsed = {
    "order_ids": [],
    "assigned_ids": set(),
    "closed_ids": set(),
    "rest_names": {},
    "courier_packs": {},  # nick w ogóle nie w packs
    "delivery_addresses": {},
}
_run_diff(parsed)
ghost_emits = [e for e in _EMIT_CALLS if (e.get("payload") or {}).get("source") == "packs_ghost_detect"]
check("nick absent from packs → no ghost (kurier off-shift)", len(ghost_emits) == 0)

# Test 4 — age guard: freshly assigned (< 5 min) skipped
_reset()
_STATE["FRESH1"] = {
    "status": "picked_up",
    "courier_id": _TEST_CID,
    "assigned_at": _iso_ago(2),  # 2 min — poniżej threshold 5
}
_FETCH_DETAILS["FRESH1"] = {"id_status_zamowienia": 7, "id_kurier": _TEST_CID}
parsed = {
    "order_ids": [],
    "assigned_ids": set(),
    "closed_ids": set(),
    "rest_names": {},
    "courier_packs": {_TEST_NICK: []},
    "delivery_addresses": {},
}
_run_diff(parsed)
ghost_emits = [e for e in _EMIT_CALLS if (e.get("payload") or {}).get("source") == "packs_ghost_detect"]
check("fresh assignment (<5 min) → age guard, no ghost", len(ghost_emits) == 0)

# Test 5 — koordynator cid skipped
_reset()
_STATE["KOORD1"] = {
    "status": "assigned",
    "courier_id": "26",  # KOORDYNATOR_ID
    "assigned_at": _iso_ago(10),
}
parsed = {
    "order_ids": [],
    "assigned_ids": set(),
    "closed_ids": set(),
    "rest_names": {},
    "courier_packs": {_TEST_NICK: []},
    "delivery_addresses": {},
}
_run_diff(parsed)
ghost_emits = [e for e in _EMIT_CALLS if (e.get("payload") or {}).get("source") == "packs_ghost_detect"]
check("koordynator cid → skip", len(ghost_emits) == 0)

# Test 6 — terminal state skipped
_reset()
_STATE["DONE1"] = {
    "status": "delivered",
    "courier_id": _TEST_CID,
    "assigned_at": _iso_ago(10),
}
parsed = {
    "order_ids": [],
    "assigned_ids": set(),
    "closed_ids": set(),
    "rest_names": {},
    "courier_packs": {_TEST_NICK: []},
    "delivery_addresses": {},
}
_run_diff(parsed)
ghost_emits = [e for e in _EMIT_CALLS if (e.get("payload") or {}).get("source") == "packs_ghost_detect"]
check("delivered state → skip (already terminal)", len(ghost_emits) == 0)

# Test 7 — panel says status != 7 → no emit
_reset()
_STATE["NOTYET1"] = {
    "status": "picked_up",
    "courier_id": _TEST_CID,
    "assigned_at": _iso_ago(10),
}
_FETCH_DETAILS["NOTYET1"] = {"id_status_zamowienia": 5, "id_kurier": _TEST_CID}  # still picked_up
parsed = {
    "order_ids": [],
    "assigned_ids": set(),
    "closed_ids": set(),
    "rest_names": {},
    "courier_packs": {_TEST_NICK: []},  # missing
    "delivery_addresses": {},
}
_run_diff(parsed)
ghost_emits = [e for e in _EMIT_CALLS if (e.get("payload") or {}).get("source") == "packs_ghost_detect"]
check("panel status≠7 → no emit (defensive, let reconcile handle)",
      len(ghost_emits) == 0)

# Test 8 — flag OFF → no-op
_reset()
_STATE["OFFFLAG1"] = {
    "status": "picked_up",
    "courier_id": _TEST_CID,
    "assigned_at": _iso_ago(10),
}
_FETCH_DETAILS["OFFFLAG1"] = {"id_status_zamowienia": 7, "id_kurier": _TEST_CID}
parsed = {
    "order_ids": [],
    "assigned_ids": set(),
    "closed_ids": set(),
    "rest_names": {},
    "courier_packs": {_TEST_NICK: []},
    "delivery_addresses": {},
}
cm.ENABLE_V320_PACKS_GHOST_DETECT = False
_run_diff(parsed)
cm.ENABLE_V320_PACKS_GHOST_DETECT = True
ghost_emits = [e for e in _EMIT_CALLS if (e.get("payload") or {}).get("source") == "packs_ghost_detect"]
check("flag OFF → no ghost emit", len(ghost_emits) == 0)

# Test 9 — budget respected (max per cycle)
_reset()
for i in range(10):
    oid = f"BURST{i}"
    _STATE[oid] = {
        "status": "picked_up",
        "courier_id": _TEST_CID,
        "assigned_at": _iso_ago(10),
    }
    _FETCH_DETAILS[oid] = {"id_status_zamowienia": 7, "id_kurier": _TEST_CID}
parsed = {
    "order_ids": [],
    "assigned_ids": set(),
    "closed_ids": set(),
    "rest_names": {},
    "courier_packs": {_TEST_NICK: []},
    "delivery_addresses": {},
}
_run_diff(parsed)
ghost_emits = [e for e in _EMIT_CALLS if (e.get("payload") or {}).get("source") == "packs_ghost_detect"]
check("budget cap: max 5 ghost per cycle", len(ghost_emits) <= 5)

# Test 10 — empty assigned_at (defensive) → still processed if in state
_reset()
_STATE["NOASSIGNED"] = {
    "status": "picked_up",
    "courier_id": _TEST_CID,
    # no assigned_at field
}
_FETCH_DETAILS["NOASSIGNED"] = {"id_status_zamowienia": 7, "id_kurier": _TEST_CID}
parsed = {
    "order_ids": [],
    "assigned_ids": set(),
    "closed_ids": set(),
    "rest_names": {},
    "courier_packs": {_TEST_NICK: []},
    "delivery_addresses": {},
}
_run_diff(parsed)
ghost_emits = [e for e in _EMIT_CALLS if (e.get("payload") or {}).get("source") == "packs_ghost_detect"]
check("no assigned_at → defensive proceed (ghost detected)",
      len(ghost_emits) == 1)

# ============================================================
total = passed + failed
print()
print("=" * 60)
print(f"V3.20 PACKS GHOST DETECT: {passed}/{total} PASS")
print("=" * 60)

if failed:
    sys.exit(1)
