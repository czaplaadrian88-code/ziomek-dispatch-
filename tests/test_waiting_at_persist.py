"""ETAP 5 KROK 5 (2026-06-10) — persystencja waiting_at (id_status=4) w pu_reconcile.

Kurier pod restauracją (panel sid=4) → panel_watcher zapisuje waiting_at do
orders_state (pierwszy raz, idempotent). Konsument: sla_tracker E6
(arrival_source=status4 zamiast commit_fallback).

Mocki wzorem tests/test_assignment_lag_fix.py — pełna izolacja I/O (lekcja #180:
upsert_order patchowany capture'em, zero zapisów do żywego orders_state).
"""
import json
from types import SimpleNamespace
from unittest import mock

from dispatch_v2 import common, panel_watcher


def _parsed():
    return {
        "order_ids": [],
        "assigned_ids": set(),
        "unassigned_ids": [],
        "rest_names": {},
        "courier_packs": {},
        "courier_load": {},
        "html_times": {},
        "closed_ids": set(),
        "pickup_addresses": {},
        "delivery_addresses": {},
    }


def _state(orders):
    return {
        oid: dict(
            {"courier_id": cid, "status": status, "order_id": oid,
             "delivery_address": "X"},
            **extra,
        )
        for oid, cid, status, extra in orders
    }


def _raw(oid, cid, status_id, dzien_odbioru=None):
    return {
        "id": int(oid),
        "id_kurier": int(cid) if cid else None,
        "id_status_zamowienia": status_id,
        "dzien_odbioru": dzien_odbioru,
        "street": "Street",
        "nr_domu": "1",
        "czas_odbioru": "35",
        "czas_odbioru_timestamp": "2026-06-10 16:00:00",
        "created_at": "2026-06-10T14:00:00.000000Z",
        "address": {"id": 1, "name": "Rest", "street": "Main", "city": "Białystok"},
        "lokalizacja": {"id": 1, "name": "Białystok"},
    }


def _run(state, raw_fetches, flag_overrides=None):
    """_diff_and_emit z pełnym mockowaniem; zwraca capture wywołań upsert_order."""
    upserts = []
    overrides = flag_overrides or {}
    real_flag = common.flag

    def fake_flag(name, default=False):
        if name in overrides:
            return overrides[name]
        return real_flag(name, default)

    def fake_fetch(zid, csrf, timeout=10.0):
        return raw_fetches.get(str(zid))

    def fake_upsert(oid, data, event=None):
        upserts.append({"oid": oid, "data": data, "event": event})
        return dict(data, order_id=oid)

    with mock.patch("dispatch_v2.panel_watcher.state_get_all", return_value=state), \
         mock.patch("dispatch_v2.panel_watcher.fetch_order_details", side_effect=fake_fetch), \
         mock.patch("dispatch_v2.panel_watcher.emit", return_value=True), \
         mock.patch("dispatch_v2.panel_watcher.emit_audit", return_value=True), \
         mock.patch(
             "dispatch_v2.panel_watcher.apply_state_event",
             return_value=SimpleNamespace(should_run_followups=True),
         ), \
         mock.patch("dispatch_v2.panel_watcher._check_panel_override"), \
         mock.patch("dispatch_v2.panel_watcher.geocode", return_value=None), \
         mock.patch("dispatch_v2.panel_watcher.normalize_order", return_value=None), \
         mock.patch("dispatch_v2.panel_watcher.upsert_order", side_effect=fake_upsert), \
         mock.patch("dispatch_v2.panel_watcher.touch_check_cursor"), \
         mock.patch("dispatch_v2.panel_watcher.flag", side_effect=fake_flag), \
         mock.patch("builtins.open", mock.mock_open(read_data=json.dumps({}))):
        panel_watcher._diff_and_emit(_parsed(), csrf="test")
    return [u for u in upserts if "waiting_at" in (u["data"] or {})]


def test_sid4_sets_waiting_at():
    calls = _run(
        state=_state([("479001", "100", "assigned", {})]),
        raw_fetches={"479001": _raw("479001", "100", status_id=4)},
    )
    assert len(calls) == 1
    assert calls[0]["oid"] == "479001"
    assert calls[0]["event"] == "WAITING_AT_RESTAURANT_OBSERVED"
    # waiting_at = aware UTC ISO — parsowalne przez konsumenta E6
    assert common.parse_panel_timestamp(calls[0]["data"]["waiting_at"]) is not None


def test_idempotent_no_overwrite():
    calls = _run(
        state=_state([
            ("479002", "100", "assigned",
             {"waiting_at": "2026-06-10T15:00:00+00:00"}),
        ]),
        raw_fetches={"479002": _raw("479002", "100", status_id=4)},
    )
    assert calls == []


def test_flag_off_no_persist():
    calls = _run(
        state=_state([("479003", "100", "assigned", {})]),
        raw_fetches={"479003": _raw("479003", "100", status_id=4)},
        flag_overrides={"ENABLE_WAITING_AT_PERSIST": False},
    )
    assert calls == []


def test_sid5_picked_up_does_not_set_waiting_at():
    calls = _run(
        state=_state([("479004", "100", "assigned", {})]),
        raw_fetches={
            "479004": _raw("479004", "100", status_id=5,
                           dzien_odbioru="2026-06-10 16:05:00"),
        },
    )
    assert calls == []


def test_sid3_in_transit_does_not_set_waiting_at():
    calls = _run(
        state=_state([("479005", "100", "assigned", {})]),
        raw_fetches={"479005": _raw("479005", "100", status_id=3)},
    )
    assert calls == []
