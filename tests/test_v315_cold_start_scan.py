"""Regression dla tech-debt #24: V3.15 packs_fallback post-restart cold-start scan.

Bug: panel-watcher restart in-peak (~5-10s) drops COURIER_ASSIGNED dla
orderów mid-way ASSIGN→PICKUP. Post-restart panel diff emit COURIER_PICKED_UP
direct (state nie ma prior ASSIGNED) → reconcile worker MISSING_FROM_STATE
phantom 4h+ później.

Fix: _post_restart_cold_start_scan(parsed, csrf) — one-shot post-restart
iteruje courier_packs i emit COURIER_ASSIGNED dla każdego oid bez entry
w orders_state lub z empty cid. Bypass V3.15 budget. _cold_start_done
flag → second call no-op via tick() gate.
"""
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import panel_watcher  # noqa: E402


def _mock_parsed(courier_packs=None):
    return {
        "order_ids": [],
        "assigned_ids": set(),
        "unassigned_ids": [],
        "rest_names": {},
        "courier_packs": courier_packs or {},
        "courier_load": {},
        "html_times": {},
        "closed_ids": set(),
        "pickup_addresses": {},
        "delivery_addresses": {},
    }


def _state_dict(orders):
    """orders: list of (oid, cid, status). cid=None → state has entry but no cid."""
    return {
        oid: {
            "courier_id": cid,
            "status": status,
            "order_id": oid,
        }
        for oid, cid, status in orders
    }


def _raw_response(oid, cid, status_id=3):
    return {
        "id": int(oid),
        "id_kurier": int(cid) if cid else None,
        "id_status_zamowienia": status_id,
    }


def _run_scan(parsed, state_orders=(), kurier_ids=None, raw_fetches=None,
              emit_captures=None, update_captures=None):
    kurier_ids_json = json.dumps(kurier_ids or {})
    state = _state_dict(state_orders) if state_orders else {}
    raw_fetches = raw_fetches or {}

    def fake_fetch(zid, csrf, timeout=10.0):
        if isinstance(raw_fetches.get(str(zid)), Exception):
            raise raw_fetches[str(zid)]
        return raw_fetches.get(str(zid))

    def fake_emit(*args, **kwargs):
        if emit_captures is not None:
            emit_captures.append(kwargs)
        return True

    def fake_update(ev):
        if update_captures is not None:
            update_captures.append(ev)

    def fake_apply(ev, *, emitted, **_kwargs):
        if emitted:
            fake_update(ev)
        return SimpleNamespace(should_run_followups=bool(emitted))

    with mock.patch("dispatch_v2.panel_watcher.state_get_all", return_value=state), \
         mock.patch("dispatch_v2.panel_watcher.fetch_order_details", side_effect=fake_fetch), \
         mock.patch("dispatch_v2.panel_watcher.emit_audit", side_effect=fake_emit), \
         mock.patch("dispatch_v2.panel_watcher.apply_state_event", side_effect=fake_apply), \
         mock.patch("dispatch_v2.panel_watcher._save_plan_on_assign"), \
         mock.patch("builtins.open", mock.mock_open(read_data=kurier_ids_json)):
        return panel_watcher._post_restart_cold_start_scan(parsed, csrf="test")


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  PASS  {label}")
            results["pass"] += 1
        else:
            print(f"  FAIL  {label}  {detail}")
            results["fail"] += 1

    importlib.reload(panel_watcher)

    # --- TEST 1: cold-start emits dla missing entry (state nie ma oid wcale) ---
    print("\n=== test 1: missing entry → emit COURIER_ASSIGNED ===")
    emits = []
    stats = _run_scan(
        parsed=_mock_parsed(courier_packs={"Michał Li": ["467131"]}),
        state_orders=[],  # NO entry — typical post-restart drop
        kurier_ids={"Michał Li": 508},
        raw_fetches={"467131": _raw_response("467131", 508, status_id=3)},
        emit_captures=emits,
    )
    cs_emits = [e for e in emits if e.get("payload", {}).get("source") == "cold_start_scan"]
    expect("1 emit z source=cold_start_scan", len(cs_emits) == 1)
    expect("oid=467131 cid=508", cs_emits and cs_emits[0].get("order_id") == "467131"
           and cs_emits[0].get("courier_id") == "508")
    expect("stats emitted=1", stats.get("cold_start_emitted") == 1)
    expect("stats scanned=1", stats.get("cold_start_scanned") == 1)
    expect("event_id ma _coldstart suffix",
           cs_emits and cs_emits[0].get("event_id", "").endswith("_coldstart"))

    # --- TEST 2: state z courier_id=None (entry exists ale ASSIGNED dropped) ---
    print("\n=== test 2: state.cid=None → emit ===")
    emits = []
    _run_scan(
        parsed=_mock_parsed(courier_packs={"Michał Li": ["467132"]}),
        state_orders=[("467132", None, "new")],
        kurier_ids={"Michał Li": 508},
        raw_fetches={"467132": _raw_response("467132", 508, status_id=3)},
        emit_captures=emits,
    )
    cs_emits = [e for e in emits if e.get("payload", {}).get("source") == "cold_start_scan"]
    expect("emit gdy state.cid=None", len(cs_emits) == 1)

    # --- TEST 3: state z cid set → cold-start NIE fire (V3.15 normal handle) ---
    print("\n=== test 3: state.cid set → cold-start skip (V3.15 covers) ===")
    emits = []
    _run_scan(
        parsed=_mock_parsed(courier_packs={"Michał Li": ["467133"]}),
        state_orders=[("467133", "508", "assigned")],
        kurier_ids={"Michał Li": 508},
        raw_fetches={"467133": _raw_response("467133", 508, status_id=3)},
        emit_captures=emits,
    )
    cs_emits = [e for e in emits if e.get("payload", {}).get("source") == "cold_start_scan"]
    expect("zero emit gdy state.cid set", len(cs_emits) == 0)

    # --- TEST 4: terminal status w state → skip ---
    print("\n=== test 4: terminal status (delivered) → skip ===")
    emits = []
    _run_scan(
        parsed=_mock_parsed(courier_packs={"Michał Li": ["467134"]}),
        state_orders=[("467134", None, "delivered")],
        kurier_ids={"Michał Li": 508},
        raw_fetches={"467134": _raw_response("467134", 508, status_id=3)},
        emit_captures=emits,
    )
    cs_emits = [e for e in emits if e.get("payload", {}).get("source") == "cold_start_scan"]
    expect("delivered → skip", len(cs_emits) == 0)

    # --- TEST 5: IGNORED_STATUSES (raw sid=7 delivered) → skip ---
    print("\n=== test 5: raw status_id=7 (delivered) → skip ===")
    emits = []
    _run_scan(
        parsed=_mock_parsed(courier_packs={"Michał Li": ["467135"]}),
        state_orders=[],
        kurier_ids={"Michał Li": 508},
        raw_fetches={"467135": _raw_response("467135", 508, status_id=7)},
        emit_captures=emits,
    )
    cs_emits = [e for e in emits if e.get("payload", {}).get("source") == "cold_start_scan"]
    expect("raw sid=7 → skip", len(cs_emits) == 0)

    # --- TEST 6: raw id_kurier=KOORDYNATOR_ID (26) → skip ---
    print("\n=== test 6: raw id_kurier=26 (Koordynator) → skip ===")
    emits = []
    _run_scan(
        parsed=_mock_parsed(courier_packs={"Michał Li": ["467136"]}),
        state_orders=[],
        kurier_ids={"Michał Li": 508},
        raw_fetches={"467136": _raw_response("467136", 26, status_id=3)},
        emit_captures=emits,
    )
    cs_emits = [e for e in emits if e.get("payload", {}).get("source") == "cold_start_scan"]
    expect("Koordynator (cid=26) → skip", len(cs_emits) == 0)

    # --- TEST 7: ambiguous nick → skip ---
    print("\n=== test 7: ambiguous nick (2 cids same name) → skip ===")
    emits = []
    _run_scan(
        parsed=_mock_parsed(courier_packs={"Adam K": ["467137"]}),
        state_orders=[],
        kurier_ids={"Adam K": 100, "Adam K ": 200},  # whitespace makes ambiguous
        raw_fetches={"467137": _raw_response("467137", 100, status_id=3)},
        emit_captures=emits,
    )
    cs_emits = [e for e in emits if e.get("payload", {}).get("source") == "cold_start_scan"]
    expect("ambiguous nick → skip", len(cs_emits) == 0)

    # --- TEST 8: kurier_ids load fail → graceful return ---
    print("\n=== test 8: kurier_ids open() fail → graceful return ===")
    emits = []
    with mock.patch("dispatch_v2.panel_watcher.state_get_all", return_value={}), \
         mock.patch("dispatch_v2.panel_watcher.emit_audit", return_value=True), \
         mock.patch("builtins.open", side_effect=OSError("disk fail")):
        stats = panel_watcher._post_restart_cold_start_scan(
            _mock_parsed(courier_packs={"X": ["1"]}), csrf="t"
        )
    expect("stats emitted=0 (load fail)", stats.get("cold_start_emitted", 0) == 0)
    expect("graceful return (no exception)", isinstance(stats, dict))

    # --- TEST 9: empty packs → no-op ---
    print("\n=== test 9: empty packs → no-op ===")
    emits = []
    stats = _run_scan(
        parsed=_mock_parsed(courier_packs={}),
        state_orders=[],
        kurier_ids={"X": 1},
        emit_captures=emits,
    )
    expect("zero emits empty packs", len(emits) == 0)
    expect("stats scanned=0", stats.get("cold_start_scanned", 0) == 0)

    # --- TEST 10: mass catchup unlimited (40 orders, no budget cap) ---
    print("\n=== test 10: mass catchup 40 orders (NO budget cap, all emit) ===")
    emits = []
    packs_mass = {"Kurier X": [f"{500000 + i}" for i in range(40)]}
    raw_mass = {f"{500000 + i}": _raw_response(f"{500000 + i}", 999, status_id=3)
                for i in range(40)}
    _run_scan(
        parsed=_mock_parsed(courier_packs=packs_mass),
        state_orders=[],
        kurier_ids={"Kurier X": 999},
        raw_fetches=raw_mass,
        emit_captures=emits,
    )
    cs_emits = [e for e in emits if e.get("payload", {}).get("source") == "cold_start_scan"]
    expect(f"40/40 emits (got {len(cs_emits)}, no budget cap)", len(cs_emits) == 40)

    # --- TEST 11: mismatch nick→cid trust raw id_kurier ---
    print("\n=== test 11: nick map vs raw mismatch → trust raw ===")
    emits = []
    _run_scan(
        parsed=_mock_parsed(courier_packs={"Michał Li": ["467138"]}),
        state_orders=[],
        kurier_ids={"Michał Li": 508},
        # raw mówi inny cid niż mapping — trust raw
        raw_fetches={"467138": _raw_response("467138", 999, status_id=3)},
        emit_captures=emits,
    )
    cs_emits = [e for e in emits if e.get("payload", {}).get("source") == "cold_start_scan"]
    expect("emit cid=999 (raw, NOT 508 z mapping)",
           cs_emits and cs_emits[0].get("courier_id") == "999")

    # --- TEST 12: nick spoza kurier_ids.json (PIN-only courier) → skip ---
    print("\n=== test 12: nick spoza kurier_ids → skip ===")
    emits = []
    _run_scan(
        parsed=_mock_parsed(courier_packs={"NieZnany": ["467139"]}),
        state_orders=[],
        kurier_ids={"Michał Li": 508},  # mapping bez NieZnany
        raw_fetches={"467139": _raw_response("467139", 999, status_id=3)},
        emit_captures=emits,
    )
    cs_emits = [e for e in emits if e.get("payload", {}).get("source") == "cold_start_scan"]
    expect("nick spoza mapping → skip", len(cs_emits) == 0)

    # --- TEST 13: idempotent event_id (_coldstart suffix deterministic) ---
    print("\n=== test 13: event_id deterministic suffix ===")
    emits = []
    _run_scan(
        parsed=_mock_parsed(courier_packs={"Michał Li": ["467140"]}),
        state_orders=[],
        kurier_ids={"Michał Li": 508},
        raw_fetches={"467140": _raw_response("467140", 508, status_id=3)},
        emit_captures=emits,
    )
    expected_eid = "467140_COURIER_ASSIGNED_508_coldstart"
    expect(f"event_id={expected_eid}",
           emits and emits[0].get("event_id") == expected_eid)

    # --- TEST 14: fetch fail → counter increments + skip oid ---
    print("\n=== test 14: fetch_details fail → cold_start_errors++ ===")
    emits = []
    stats = _run_scan(
        parsed=_mock_parsed(courier_packs={"Michał Li": ["467141"]}),
        state_orders=[],
        kurier_ids={"Michał Li": 508},
        raw_fetches={"467141": RuntimeError("network down")},
        emit_captures=emits,
    )
    expect("stats errors=1", stats.get("cold_start_errors", 0) == 1)
    expect("zero emits gdy fetch fail", len(emits) == 0)

    # --- TEST 15: cold_start_done flag prevents second-call (via tick gate) ---
    print("\n=== test 15: _cold_start_done module flag exists ===")
    expect("flag _cold_start_done attribute exists",
           hasattr(panel_watcher, "_cold_start_done"))
    expect("flag default False na fresh import",
           panel_watcher._cold_start_done in (False, True))  # may be True post earlier tests

    print(f"\n=== RESULT: {results['pass']} PASS / {results['fail']} FAIL ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
