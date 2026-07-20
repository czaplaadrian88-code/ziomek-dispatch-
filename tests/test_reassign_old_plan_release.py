"""REASSIGN-RELEASE (2026-07-20): zwolnienie planu STAREGO kuriera po przerzuceniu.

Bug (zgłoszenie ownera „u kurierów są po przerzuceniu opóźnienia w pokazywaniu"):
branch reassign (source=panel_reassign) i bliźniak PANEL_PACKS FALLBACK emitowały
COURIER_ASSIGNED i sygnalizowały TYLKO NOWEMU kurierowi (_save_plan_on_assign_signal
→ bump/invalidate → SSE → apka nowego odświeża). courier_plans STAREGO dalej
zawierał stop, plan_version stał → apka starego pokazywała zabrane zlecenie do
fallbacku 180 s (PlanPoller.FULL_REFRESH_FALLBACK_MS) / 5-min plan_recheck.
Handlery deliver (advance_plan) i cancel/return (_remove_stops_on_return) robią
to poprawnie dla swoich tranzycji — reassign-loser był jedyną tranzycją KURCZĄCĄ
worek bez remove_stops (protokół #0 „Recanon": kurcząca tranzycja MUSI wołać
plan_manager.remove_stops/advance_plan PRZED recanon).

Fix: _release_plan_on_reassign (lustrzane do _remove_stops_on_return), za flagą
ENABLE_REASSIGN_OLD_PLAN_RELEASE (default OFF; deploy ciemny, flip za ACK).
Wywołane w OBU bliźniaczych torach: branch reassign + PANEL_PACKS FALLBACK
(guard _state_cid != _target_cid liczony PO trust-raw).
"""
import inspect
import json
import logging

from unittest import mock

import dispatch_v2.panel_watcher as PW
import dispatch_v2.common as C
import dispatch_v2.plan_manager as PM
import dispatch_v2.plan_recheck as PR


# ---------------------------------------------------------------- fixtures ---

def _seed_store(tmp_path, monkeypatch):
    """Fake courier_plans: 2 kurierów, stop przerzucanego zlecenia u STAREGO (207)."""
    plans = {
        "207": {
            "courier_id": "207",
            "plan_version": 3,
            "invalidated_at": None,
            "stops": [
                {"order_id": "999001", "type": "dropoff"},
                {"order_id": "999002", "type": "dropoff"},
            ],
        },
        "310": {
            "courier_id": "310",
            "plan_version": 7,
            "invalidated_at": None,
            "stops": [{"order_id": "888001", "type": "dropoff"}],
        },
    }
    pf = tmp_path / "courier_plans.json"
    pf.write_text(json.dumps(plans), encoding="utf-8")
    monkeypatch.setattr(PM, "PLANS_FILE", pf)
    monkeypatch.setattr(PM, "LOCK_FILE", tmp_path / "courier_plans.lock")
    return pf, plans


def _flags_on(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_SAVED_PLANS", True)
    monkeypatch.setattr(C, "ENABLE_REASSIGN_OLD_PLAN_RELEASE", True)


# ------------------------------------------------------- helper unit-level ---

def test_release_removes_stop_and_bumps_version(tmp_path, monkeypatch, caplog):
    """Flaga ON: stop znika z planu STAREGO, plan_version+1, cudzy plan i cudzy
    stop nietknięte, recanon(reason='reassign_out'), telemetria REASSIGN-RELEASE."""
    pf, _ = _seed_store(tmp_path, monkeypatch)
    _flags_on(monkeypatch)
    recanons = []
    monkeypatch.setattr(PR, "recanon_courier",
                        lambda cid, **kw: recanons.append((cid, kw.get("reason"))) or True)
    with caplog.at_level(logging.INFO):
        PW._release_plan_on_reassign("207", "999001")
    after = json.loads(pf.read_text(encoding="utf-8"))
    assert [s["order_id"] for s in after["207"]["stops"]] == ["999002"], \
        "stop przerzuconego zlecenia MUSI zniknąć z planu starego"
    assert after["207"]["plan_version"] == 4, \
        "remove_stops MUSI bumpnąć plan_version (sygnał dla apki starego)"
    assert after["310"] == json.loads(json.dumps({
        "courier_id": "310", "plan_version": 7, "invalidated_at": None,
        "stops": [{"order_id": "888001", "type": "dropoff"}]})), \
        "plan INNEGO kuriera nietknięty"
    assert recanons == [("207", "reassign_out")], \
        "po remove_stops recanon RESZTY worka starego (reason=reassign_out)"
    assert "REASSIGN-RELEASE cid_old=207 oid=999001" in caplog.text


def test_flag_off_plan_untouched(tmp_path, monkeypatch):
    """ON≠OFF: flaga OFF (default) → plan starego BAJT-W-BAJT nietknięty, zero recanon."""
    pf, seeded = _seed_store(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "ENABLE_SAVED_PLANS", True)
    monkeypatch.setattr(C, "ENABLE_REASSIGN_OLD_PLAN_RELEASE", False)
    recanons = []
    monkeypatch.setattr(PR, "recanon_courier", lambda cid, **kw: recanons.append(cid))
    PW._release_plan_on_reassign("207", "999001")
    assert json.loads(pf.read_text(encoding="utf-8")) == seeded, \
        "flaga OFF → zachowanie sprzed fixu (plan starego stoi)"
    assert recanons == []


def test_flag_default_is_off():
    """Deploy ciemny: stała-fallback modułu MUSI być False (flip tylko flags.json+ACK)."""
    assert C.ENABLE_REASSIGN_OLD_PLAN_RELEASE is False
    assert "ENABLE_REASSIGN_OLD_PLAN_RELEASE" in C.ETAP4_DECISION_FLAGS, \
        "rejestracja ETAP4 = strip w testach + widoczność w flag_fingerprint"


def test_saved_plans_off_noop(tmp_path, monkeypatch):
    """Lustrzana brama ENABLE_SAVED_PLANS (jak _remove_stops_on_return): OFF → no-op."""
    pf, seeded = _seed_store(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "ENABLE_SAVED_PLANS", False)
    monkeypatch.setattr(C, "ENABLE_REASSIGN_OLD_PLAN_RELEASE", True)
    recanons = []
    monkeypatch.setattr(PR, "recanon_courier", lambda cid, **kw: recanons.append(cid))
    PW._release_plan_on_reassign("207", "999001")
    assert json.loads(pf.read_text(encoding="utf-8")) == seeded
    assert recanons == []


def test_empty_old_cid_noop(monkeypatch):
    """state_courier pusty → no-op (bez remove_stops, bez recanon)."""
    _flags_on(monkeypatch)
    removed, recanons = [], []
    monkeypatch.setattr(PM, "remove_stops", lambda cid, oid: removed.append((cid, oid)))
    monkeypatch.setattr(PR, "recanon_courier", lambda cid, **kw: recanons.append(cid))
    PW._release_plan_on_reassign("", "999001")
    assert removed == [] and recanons == []


def test_remove_stops_failure_swallowed_recanon_still_runs(monkeypatch):
    """remove_stops rzuca → warning, helper NIE propaguje (diff loop żyje),
    recanon dalej próbowany (osobne try/except — lustrzane do sąsiadów)."""
    _flags_on(monkeypatch)

    def _boom(cid, oid):
        raise RuntimeError("disk full")
    monkeypatch.setattr(PM, "remove_stops", _boom)
    recanons = []
    monkeypatch.setattr(PR, "recanon_courier", lambda cid, **kw: recanons.append(cid))
    PW._release_plan_on_reassign("207", "999001")            # nie rzuca
    assert recanons == ["207"]


def test_recanon_failure_swallowed(monkeypatch):
    """recanon best-effort — wyjątek nie może wywrócić handlera (≤ stan sprzed fixu)."""
    _flags_on(monkeypatch)
    monkeypatch.setattr(PM, "remove_stops", lambda cid, oid: None)

    def _boom(cid, **kw):
        raise RuntimeError("gps missing")
    monkeypatch.setattr(PR, "recanon_courier", _boom)
    PW._release_plan_on_reassign("207", "999001")            # nie rzuca


# ------------------------------------------------- branch-level (diff loop) ---

def _mock_parsed(courier_packs=None, order_ids=None, assigned_ids=None,
                 closed_ids=None):
    """Minimalne `parsed` (wzorzec test_assignment_lag_fix)."""
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


def _raw_response(oid, cid, status_id=3):
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


def _run_diff_with_recorders(parsed, state, raw_fetches, kurier_ids=None):
    """_diff_and_emit z pełnym mockowaniem; zwraca (calls, stats) gdzie calls
    = chronologia ('release', cid, oid) / ('signal', oid, cid)."""
    calls = []

    def fake_fetch(zid, csrf, timeout=10.0):
        return raw_fetches.get(str(zid))

    with mock.patch("dispatch_v2.panel_watcher.state_get_all", return_value=state), \
         mock.patch("dispatch_v2.panel_watcher.fetch_order_details", side_effect=fake_fetch), \
         mock.patch("dispatch_v2.panel_watcher.emit", return_value=True), \
         mock.patch("dispatch_v2.panel_watcher.emit_audit", return_value=True), \
         mock.patch("dispatch_v2.panel_watcher.update_from_event"), \
         mock.patch("dispatch_v2.panel_watcher._check_panel_agree"), \
         mock.patch("dispatch_v2.panel_watcher._check_panel_override"), \
         mock.patch("dispatch_v2.panel_watcher._release_plan_on_reassign",
                    side_effect=lambda cid, oid: calls.append(("release", cid, oid))), \
         mock.patch("dispatch_v2.panel_watcher._save_plan_on_assign_signal",
                    side_effect=lambda oid, cid: calls.append(("signal", oid, cid))), \
         mock.patch("dispatch_v2.panel_watcher.geocode", return_value=None), \
         mock.patch("dispatch_v2.panel_watcher.normalize_order", return_value=None), \
         mock.patch("dispatch_v2.panel_watcher.upsert_order"), \
         mock.patch("dispatch_v2.panel_watcher.touch_check_cursor"), \
         mock.patch("builtins.open",
                    mock.mock_open(read_data=json.dumps(kurier_ids or {}))):
        stats = PW._diff_and_emit(parsed, csrf="test")
    return calls, stats


def test_reassign_branch_release_old_then_signal_new():
    """Branch reassign: helper wołany z (STARY cid, zid) PRZED sygnałem nowemu."""
    state = {"467500": {"order_id": "467500", "courier_id": "207",
                        "status": "assigned", "delivery_address": "X"}}
    calls, stats = _run_diff_with_recorders(
        parsed=_mock_parsed(order_ids=["467500"], assigned_ids={"467500"}),
        state=state,
        raw_fetches={"467500": _raw_response("467500", 310)},
    )
    assert calls == [("release", "207", "467500"), ("signal", "467500", "310")], \
        "NAJPIERW zwolnij plan starego, POTEM sygnał nowemu"
    assert stats["assigned"] == 1


def test_reassign_branch_same_courier_no_release():
    """Panel pokazuje TEGO SAMEGO kuriera → żadnego release ani sygnału (no-op)."""
    state = {"467501": {"order_id": "467501", "courier_id": "207",
                        "status": "assigned", "delivery_address": "X"}}
    calls, _ = _run_diff_with_recorders(
        parsed=_mock_parsed(order_ids=["467501"], assigned_ids={"467501"}),
        state=state,
        raw_fetches={"467501": _raw_response("467501", 207)},
    )
    assert calls == []


def test_packs_fallback_release_previous_courier():
    """Bliźniak: PANEL_PACKS FALLBACK przy zmianie kuriera (previous_cid) też
    zwalnia plan starego przed sygnałem nowemu."""
    state = {"467600": {"order_id": "467600", "courier_id": "207",
                        "status": "assigned", "delivery_address": "X"}}
    calls, _ = _run_diff_with_recorders(
        parsed=_mock_parsed(courier_packs={"Nowy K": ["467600"]}),
        state=state,
        raw_fetches={"467600": _raw_response("467600", 310)},
        kurier_ids={"Nowy K": 310},
    )
    assert calls == [("release", "207", "467600"), ("signal", "467600", "310")]


def test_packs_fallback_trust_raw_guard_no_release():
    """Trust-raw przywraca kuriera ZE STATE (zła mapa nicków → raw wygrywa):
    release NIE wolno wołać — zdarłby stop z AKTUALNEGO planu kuriera."""
    state = {"467700": {"order_id": "467700", "courier_id": "207",
                        "status": "assigned", "delivery_address": "X"}}
    calls, _ = _run_diff_with_recorders(
        parsed=_mock_parsed(courier_packs={"Nowy K": ["467700"]}),
        state=state,
        raw_fetches={"467700": _raw_response("467700", 207)},   # raw == state
        kurier_ids={"Nowy K": 310},                             # mapa kłamie
    )
    assert [c for c in calls if c[0] == "release"] == [], \
        "guard _state_cid != _target_cid liczony PO trust-raw MUSI blokować release"
    assert ("signal", "467700", "207") in calls


# ------------------------------------------------------- strażnicy dryfu ---

def test_release_helper_shape_mirrors_return_handler():
    """Strażnik: helper woła remove_stops ORAZ recanon_courier (symetria P-5,
    klasa „tranzycja kurcząca worek": cancel/deliver/reassign-loser)."""
    src = inspect.getsource(PW._release_plan_on_reassign)
    assert "remove_stops" in src
    assert "recanon_courier" in src
    assert "reassign_out" in src


def test_both_twin_paths_call_release():
    """Strażnik kompletności (protokół #0 ETAP 3): OBA bliźniacze tory emitujące
    COURIER_ASSIGNED przy ZMIANIE kuriera wołają release — branch reassign
    (panel_reassign) i PANEL_PACKS FALLBACK."""
    src = inspect.getsource(PW._diff_and_emit)
    assert "_release_plan_on_reassign(state_courier, zid)" in src, \
        "branch reassign musi zwalniać plan starego"
    assert "_release_plan_on_reassign(_state_cid, _oid_str)" in src, \
        "PANEL_PACKS FALLBACK (bliźniak) musi zwalniać plan starego"
