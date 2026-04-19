"""Regresja dla V3.15 panel_packs fallback fix (assignment lag bug).

Bug: panel_watcher.reconcile emit COURIER_ASSIGNED z lagiem 15-90s dla
świeżych assignments → propozycje widzą kurierów z bagami w panelu jako
"wolnych". panel_client.parse_panel_html zwraca courier_packs {nick:[oid]}
— ground truth, ale wcześniej dead data.

Fix: panel_watcher._diff_and_emit konsumuje courier_packs jako fallback
trigger — mismatch z orders_state wymusza fetch_details + emit
COURIER_ASSIGNED (source=packs_fallback).
"""
import importlib
import json
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common, panel_watcher  # noqa: E402


def _mock_parsed(courier_packs=None, order_ids=None, assigned_ids=None, closed_ids=None):
    """Build minimal `parsed` dict — other sections empty so only packs
    fallback section runs."""
    return {
        "order_ids": order_ids or [],
        "assigned_ids": assigned_ids or set(),
        "unassigned_ids": [],
        "rest_names": {},
        "courier_packs": courier_packs or {},
        "courier_load": {},
        "html_times": {},
        "closed_ids": closed_ids or set(),
        "pickup_addresses": {},
        "delivery_addresses": {},
    }


def _state_dict(orders):
    """orders: list of (oid, cid, status)."""
    return {
        oid: {
            "courier_id": cid,
            "status": status,
            "order_id": oid,
            "delivery_address": "X",
        }
        for oid, cid, status in orders
    }


def _raw_response(oid, cid, status_id=3):
    """Mock raw fetch_order_details response."""
    return {
        "id": int(oid),
        "id_kurier": int(cid) if cid else None,
        "id_status_zamowienia": status_id,
        "street": "Street",
        "nr_domu": "1",
        "czas_odbioru": "35",
        "czas_odbioru_timestamp": "2026-04-19 16:00:00",
        "created_at": "2026-04-19T14:00:00.000000Z",
        "address": {"id": 1, "name": "Rest", "street": "Main", "city": "Białystok"},
        "lokalizacja": {"id": 1, "name": "Białystok"},
    }


def _run_diff(parsed, state_orders=(), kurier_ids=None, raw_fetches=None,
              emit_captures=None, update_captures=None):
    """Wykonuje _diff_and_emit z pełnym mockowaniem."""
    kurier_ids_json = json.dumps(kurier_ids or {})
    state = _state_dict(state_orders) if state_orders else {}
    raw_fetches = raw_fetches or {}

    def fake_fetch(zid, csrf, timeout=10.0):
        return raw_fetches.get(str(zid))

    def fake_emit(*args, **kwargs):
        if emit_captures is not None:
            emit_captures.append(kwargs)
        return True  # return truthy so code path proceeds to update_from_event

    def fake_update(ev):
        if update_captures is not None:
            update_captures.append(ev)

    # Patch panel_watcher module-level imports
    with mock.patch("dispatch_v2.panel_watcher.state_get_all", return_value=state), \
         mock.patch("dispatch_v2.panel_watcher.fetch_order_details", side_effect=fake_fetch), \
         mock.patch("dispatch_v2.panel_watcher.emit", side_effect=fake_emit), \
         mock.patch("dispatch_v2.panel_watcher.update_from_event", side_effect=fake_update), \
         mock.patch("dispatch_v2.panel_watcher._check_panel_override"), \
         mock.patch("dispatch_v2.panel_watcher.geocode", return_value=None), \
         mock.patch("dispatch_v2.panel_watcher.normalize_order", return_value=None), \
         mock.patch("dispatch_v2.panel_watcher.upsert_order"), \
         mock.patch("dispatch_v2.panel_watcher.touch_check_cursor"), \
         mock.patch("builtins.open", mock.mock_open(read_data=kurier_ids_json)):
        return panel_watcher._diff_and_emit(parsed, csrf="test")


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  ✅ {label}")
            results["pass"] += 1
        else:
            print(f"  ❌ {label}  {detail}")
            results["fail"] += 1

    # Baseline flag
    importlib.reload(common)
    importlib.reload(panel_watcher)
    assert common.ENABLE_PANEL_PACKS_FALLBACK is True

    # ---------- TEST 1: catchup emits for missing assignment ----------
    print("\n=== test 1: packs catchup emits COURIER_ASSIGNED for missing ===")
    emits = []
    _run_diff(
        parsed=_mock_parsed(courier_packs={"Michał Li": ["467131"]}),
        state_orders=[("467131", None, "planned")],
        kurier_ids={"Michał Li": 508},
        raw_fetches={"467131": _raw_response("467131", 508, status_id=3)},
        emit_captures=emits,
    )
    ca_emits = [e for e in emits if e.get("payload", {}).get("source") == "packs_fallback"]
    expect("1 COURIER_ASSIGNED emit z source=packs_fallback", len(ca_emits) == 1)
    expect("emit dla oid=467131 cid=508",
           ca_emits and ca_emits[0].get("order_id") == "467131"
           and ca_emits[0].get("courier_id") == "508")

    # ---------- TEST 2: mass catchup (20 orders) ----------
    print("\n=== test 2: mass catchup 15 orders (budget=10 so 10 emit) ===")
    emits = []
    packs_mass = {"Kurier X": [f"{500000 + i}" for i in range(15)]}
    state_mass = [(f"{500000 + i}", None, "planned") for i in range(15)]
    raw_mass = {f"{500000 + i}": _raw_response(f"{500000 + i}", 999, status_id=3) for i in range(15)}
    _run_diff(
        parsed=_mock_parsed(courier_packs=packs_mass),
        state_orders=state_mass,
        kurier_ids={"Kurier X": 999},
        raw_fetches=raw_mass,
        emit_captures=emits,
    )
    ca_emits = [e for e in emits if e.get("payload", {}).get("source") == "packs_fallback"]
    expect(f"Budget=10 limits emits to 10 (got {len(ca_emits)})", len(ca_emits) == 10)

    # ---------- TEST 3: no emit when already in sync ----------
    print("\n=== test 3: no emit when state_cid matches target ===")
    emits = []
    _run_diff(
        parsed=_mock_parsed(courier_packs={"Michał Ro": ["467100"]}),
        state_orders=[("467100", "518", "assigned")],  # already synced
        kurier_ids={"Michał Ro": 518},
        raw_fetches={"467100": _raw_response("467100", 518, status_id=3)},
        emit_captures=emits,
    )
    ca_emits = [e for e in emits if e.get("payload", {}).get("source") == "packs_fallback"]
    expect("no packs_fallback emit when already synced", len(ca_emits) == 0)

    # ---------- TEST 4: no cross-courier contamination ----------
    print("\n=== test 4: no cross-courier contamination ===")
    emits = []
    _run_diff(
        parsed=_mock_parsed(courier_packs={"Adrian R": ["100"], "Sylwia L": ["200"]}),
        state_orders=[("100", None, "planned"), ("200", None, "planned")],
        kurier_ids={"Adrian R": 400, "Sylwia L": 441},
        raw_fetches={
            "100": _raw_response("100", 400, status_id=3),
            "200": _raw_response("200", 441, status_id=3),
        },
        emit_captures=emits,
    )
    ca_emits = [e for e in emits if e.get("payload", {}).get("source") == "packs_fallback"]
    m = {e["order_id"]: e["courier_id"] for e in ca_emits}
    expect("oid=100 → cid=400", m.get("100") == "400")
    expect("oid=200 → cid=441", m.get("200") == "441")

    # ---------- TEST 5: nick ambiguity skip ----------
    print("\n=== test 5: ambiguous nick → skip + warn ===")
    emits = []
    # kurier_ids is {name: cid}, so two entries with same name — JSON dict normally
    # collapses dup keys. Simulate ambiguity via 2 names differing only by whitespace/case.
    # Actually w kodzie: ambiguity detected when _name_to_cid[key] != new cid for same key.
    # JSON dict zwinie same key. Musimy zmusić przez 2 entries "Gabriel " i "Gabriel".
    # Ale strip() obu do "Gabriel" — kod potraktuje jako ambiguous.
    _run_diff(
        parsed=_mock_parsed(courier_packs={"Gabriel": ["300"]}),
        state_orders=[("300", None, "planned")],
        # użycie 2 synonimów (strip zbiega do "Gabriel")
        kurier_ids={"Gabriel": 179, "Gabriel ": 999},
        raw_fetches={"300": _raw_response("300", 179, status_id=3)},
        emit_captures=emits,
    )
    ca_emits = [e for e in emits if e.get("payload", {}).get("source") == "packs_fallback"]
    expect("ambiguous Gabriel skipped (no emit)", len(ca_emits) == 0)

    # ---------- TEST 6: nick not in kurier_ids → skip ----------
    print("\n=== test 6: nick not in kurier_ids.json → skip ===")
    emits = []
    _run_diff(
        parsed=_mock_parsed(courier_packs={"Unknown Courier": ["400"]}),
        state_orders=[("400", None, "planned")],
        kurier_ids={"Michał Ro": 518},  # Unknown not present
        raw_fetches={"400": _raw_response("400", 999, status_id=3)},
        emit_captures=emits,
    )
    ca_emits = [e for e in emits if e.get("payload", {}).get("source") == "packs_fallback"]
    expect("unknown nick skipped (no emit)", len(ca_emits) == 0)

    # ---------- TEST 7: skip terminal status (delivered/returned/cancelled) ----------
    print("\n=== test 7: state terminal status → skip catchup ===")
    emits = []
    _run_diff(
        parsed=_mock_parsed(courier_packs={"Michał Ro": ["500", "501", "502"]}),
        state_orders=[
            ("500", None, "delivered"),
            ("501", None, "returned_to_pool"),
            ("502", None, "cancelled"),
        ],
        kurier_ids={"Michał Ro": 518},
        raw_fetches={
            "500": _raw_response("500", 518, status_id=3),
            "501": _raw_response("501", 518, status_id=3),
            "502": _raw_response("502", 518, status_id=3),
        },
        emit_captures=emits,
    )
    ca_emits = [e for e in emits if e.get("payload", {}).get("source") == "packs_fallback"]
    expect("terminal statuses all skipped", len(ca_emits) == 0)

    # ---------- TEST 8: skip when raw returns IGNORED_STATUSES (7/8/9) ----------
    print("\n=== test 8: raw status_id=7 (delivered w panelu) → skip emit ===")
    emits = []
    _run_diff(
        parsed=_mock_parsed(courier_packs={"Michał Ro": ["600"]}),
        state_orders=[("600", None, "planned")],
        kurier_ids={"Michał Ro": 518},
        raw_fetches={"600": _raw_response("600", 518, status_id=7)},  # delivered
        emit_captures=emits,
    )
    ca_emits = [e for e in emits if e.get("payload", {}).get("source") == "packs_fallback"]
    expect("raw status_id=7 → no emit", len(ca_emits) == 0)

    # ---------- TEST 9: skip koordynator id ----------
    print("\n=== test 9: raw id_kurier=26 (Koordynator) → skip ===")
    emits = []
    _run_diff(
        parsed=_mock_parsed(courier_packs={"Michał Ro": ["700"]}),
        state_orders=[("700", None, "planned")],
        kurier_ids={"Michał Ro": 518},
        raw_fetches={"700": _raw_response("700", 26, status_id=3)},  # koordynator
        emit_captures=emits,
    )
    ca_emits = [e for e in emits if e.get("payload", {}).get("source") == "packs_fallback"]
    expect("koordynator (26) skipped", len(ca_emits) == 0)

    # ---------- TEST 10: flag False disables ----------
    print("\n=== test 10: ENABLE_PANEL_PACKS_FALLBACK=False → no emit ===")
    orig_flag = common.ENABLE_PANEL_PACKS_FALLBACK
    common.ENABLE_PANEL_PACKS_FALLBACK = False
    try:
        emits = []
        _run_diff(
            parsed=_mock_parsed(courier_packs={"Michał Ro": ["800"]}),
            state_orders=[("800", None, "planned")],
            kurier_ids={"Michał Ro": 518},
            raw_fetches={"800": _raw_response("800", 518, status_id=3)},
            emit_captures=emits,
        )
        ca_emits = [e for e in emits if e.get("payload", {}).get("source") == "packs_fallback"]
        expect("flag=False disables packs_fallback", len(ca_emits) == 0)
    finally:
        common.ENABLE_PANEL_PACKS_FALLBACK = orig_flag

    # ---------- TEST 11: kurier_ids load fail graceful ----------
    print("\n=== test 11: kurier_ids.json parse fail → graceful (no crash, no emit) ===")
    emits = []
    # Pass invalid JSON → json.load raises
    with mock.patch("dispatch_v2.panel_watcher.state_get_all", return_value={}), \
         mock.patch("dispatch_v2.panel_watcher.fetch_order_details", return_value=None), \
         mock.patch("dispatch_v2.panel_watcher.emit", return_value=True) as _e, \
         mock.patch("dispatch_v2.panel_watcher.update_from_event"), \
         mock.patch("dispatch_v2.panel_watcher._check_panel_override"), \
         mock.patch("dispatch_v2.panel_watcher.geocode", return_value=None), \
         mock.patch("dispatch_v2.panel_watcher.normalize_order", return_value=None), \
         mock.patch("dispatch_v2.panel_watcher.upsert_order"), \
         mock.patch("dispatch_v2.panel_watcher.touch_check_cursor"), \
         mock.patch("builtins.open", mock.mock_open(read_data="<<invalid json>>")):
        # Should not crash
        result = panel_watcher._diff_and_emit(
            _mock_parsed(courier_packs={"Michał": ["900"]}), csrf="test"
        )
    expect("graceful fallback when kurier_ids corrupted", result is not None)

    # ---------- TEST 12: raw id_kurier overrides nick map ----------
    print("\n=== test 12: raw id_kurier różne od nick map → trust raw ===")
    emits = []
    _run_diff(
        parsed=_mock_parsed(courier_packs={"Michał Ro": ["1000"]}),
        state_orders=[("1000", None, "planned")],
        kurier_ids={"Michał Ro": 518},  # map says 518
        raw_fetches={"1000": _raw_response("1000", 999, status_id=3)},  # raw says 999
        emit_captures=emits,
    )
    ca_emits = [e for e in emits if e.get("payload", {}).get("source") == "packs_fallback"]
    expect("emit uses raw id_kurier=999 (override map 518)",
           len(ca_emits) == 1 and ca_emits[0].get("courier_id") == "999")

    # ---------- TEST 13: regression 467164 Michał Li fixture ----------
    print("\n=== test 13: regression #467164 — 3 missing orders resolve ===")
    emits = []
    _run_diff(
        parsed=_mock_parsed(courier_packs={"Michał Li": ["467131", "467129", "467155"]}),
        state_orders=[
            ("467131", None, "planned"),
            ("467129", None, "planned"),
            ("467155", None, "planned"),
        ],
        kurier_ids={"Michał Li": 508},
        raw_fetches={
            "467131": _raw_response("467131", 508, status_id=3),
            "467129": _raw_response("467129", 508, status_id=3),
            "467155": _raw_response("467155", 508, status_id=3),
        },
        emit_captures=emits,
    )
    ca_emits = [e for e in emits if e.get("payload", {}).get("source") == "packs_fallback"]
    expect("3 emit COURIER_ASSIGNED dla Michał Li cid=508", len(ca_emits) == 3)
    ca_oids = {e["order_id"] for e in ca_emits}
    expect("oids match {467131, 467129, 467155}",
           ca_oids == {"467131", "467129", "467155"})

    # ---------- FINAL ----------
    total = results["pass"] + results["fail"]
    print()
    print("=" * 60)
    print(f"ASSIGNMENT_LAG_FIX V3.15: {results['pass']}/{total} PASS")
    print("=" * 60)
    sys.exit(0 if results["fail"] == 0 else 1)


if __name__ == "__main__":
    main()
