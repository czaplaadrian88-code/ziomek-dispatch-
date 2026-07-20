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
        assert PW._release_plan_on_reassign("207", "999001") is True
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
    assert PW._release_plan_on_reassign("207", "999001") is False, \
        "OFF → False (dedupe released_this_tick zostaje pusty → packs bajt-w-bajt)"
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
    assert PW._release_plan_on_reassign("207", "999001") is False
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


def test_remove_stops_failure_swallowed_recanon_still_runs(tmp_path, monkeypatch):
    """remove_stops rzuca → warning, helper NIE propaguje (diff loop żyje),
    recanon dalej próbowany (osobne try/except — lustrzane do sąsiadów).
    Store zasiany, żeby pre-check v2 (stop istnieje) dopuścił do remove_stops."""
    _seed_store(tmp_path, monkeypatch)
    _flags_on(monkeypatch)

    def _boom(cid, oid):
        raise RuntimeError("disk full")
    monkeypatch.setattr(PM, "remove_stops", _boom)
    recanons = []
    monkeypatch.setattr(PR, "recanon_courier", lambda cid, **kw: recanons.append(cid))
    assert PW._release_plan_on_reassign("207", "999001") is True   # nie rzuca
    assert recanons == ["207"]


def test_recanon_failure_swallowed(tmp_path, monkeypatch):
    """recanon best-effort — wyjątek nie może wywrócić handlera (≤ stan sprzed fixu).
    Store zasiany, żeby przepływ realnie DOSZEDŁ do recanon (pre-check v2)."""
    _seed_store(tmp_path, monkeypatch)
    _flags_on(monkeypatch)
    monkeypatch.setattr(PM, "remove_stops", lambda cid, oid: None)

    def _boom(cid, **kw):
        raise RuntimeError("gps missing")
    monkeypatch.setattr(PR, "recanon_courier", _boom)
    assert PW._release_plan_on_reassign("207", "999001") is True   # nie rzuca


def test_release_double_call_second_noop_version_stable(tmp_path, monkeypatch):
    """v3 (Sol flip-gate): idempotencja U ŹRÓDŁA — remove_stops robi no-op
    (zero zapisu/bumpu) WEWNĄTRZ swojego exclusive locka, gdy stopa nie ma.
    DRUGIE wywołanie helpera dla tej samej pary → store bajt-w-bajt, wersja
    STOI. Recanon (best-effort, samo-bramkujący) idzie przy obu wywołaniach —
    pre-check w helperze usunięty (dwa locki = TOCTOU)."""
    pf, _ = _seed_store(tmp_path, monkeypatch)
    _flags_on(monkeypatch)
    recanons = []
    monkeypatch.setattr(PR, "recanon_courier", lambda cid, **kw: recanons.append(cid) or True)
    assert PW._release_plan_on_reassign("207", "999001") is True
    after1 = json.loads(pf.read_text(encoding="utf-8"))
    assert after1["207"]["plan_version"] == 4
    assert PW._release_plan_on_reassign("207", "999001") is True   # no-op w PM
    after2 = json.loads(pf.read_text(encoding="utf-8"))
    assert after2 == after1, "drugie wywołanie NIE pisze do store (zero bumpa)"
    assert recanons == ["207", "207"], \
        "recanon best-effort przy obu (idempotencję gwarantuje plan_manager)"


def test_plan_manager_remove_stops_absent_oid_pure_noop(tmp_path, monkeypatch):
    """v3 (Sol flip-gate, poziom plan_manager): oid nieobecny w planie →
    czysty no-op WEWNĄTRZ exclusive locka — zero zapisu, wersja bez zmiany.
    (Poprzednio bump-always: zapis + plan_version+1 mimo braku zmiany treści
    = pusty SSE-refresh apki.)"""
    pf, seeded = _seed_store(tmp_path, monkeypatch)
    PM.remove_stops("207", "404404")            # oid spoza planu
    assert json.loads(pf.read_text(encoding="utf-8")) == seeded, \
        "brak stopa → remove_stops NIE pisze i NIE bumpuje (czysty no-op)"


def test_release_race_newer_plan_without_stop_no_bump(tmp_path, monkeypatch):
    """v3 (Sol flip-gate, wyścig symulowany sekwencyjnie): NOWSZY plan starego
    kuriera BEZ stopa (np. regen ticku wszedł między dowolny odczyt a wywołanie)
    → release NIE bumpuje wersji (decyzja wewnątrz locka remove_stops, nie w
    osobnym pre-checku)."""
    pf, seeded = _seed_store(tmp_path, monkeypatch)
    # symulacja: przerzucane zlecenie NIE występuje już w planie starego
    plans = json.loads(pf.read_text(encoding="utf-8"))
    plans["207"]["stops"] = [{"order_id": "999002", "type": "dropoff"}]
    pf.write_text(json.dumps(plans), encoding="utf-8")
    _flags_on(monkeypatch)
    recanons = []
    monkeypatch.setattr(PR, "recanon_courier", lambda cid, **kw: recanons.append(cid) or True)
    assert PW._release_plan_on_reassign("207", "999001") is True
    after = json.loads(pf.read_text(encoding="utf-8"))
    assert after == plans, "nowszy plan bez stopa → zero zapisu/bumpu"
    assert after["207"]["plan_version"] == 3


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


def _run_diff_with_recorders(parsed, state, raw_fetches, kurier_ids=None,
                             audit_emitted=True):
    """_diff_and_emit z pełnym mockowaniem; zwraca (calls, stats) gdzie calls
    = chronologia ('release', cid, oid) / ('return_release', cid, oid) /
    ('signal', oid, cid)."""
    calls = []

    def fake_fetch(zid, csrf, timeout=10.0):
        return raw_fetches.get(str(zid))

    with mock.patch("dispatch_v2.panel_watcher.state_get_all", return_value=state), \
         mock.patch("dispatch_v2.panel_watcher.fetch_order_details", side_effect=fake_fetch), \
         mock.patch("dispatch_v2.panel_watcher.emit", return_value=True), \
         mock.patch("dispatch_v2.panel_watcher.emit_audit",
                    return_value=audit_emitted), \
         mock.patch("dispatch_v2.panel_watcher.update_from_event"), \
         mock.patch("dispatch_v2.panel_watcher._check_panel_agree"), \
         mock.patch("dispatch_v2.panel_watcher._check_panel_override"), \
         mock.patch("dispatch_v2.panel_watcher._release_plan_on_reassign",
                    side_effect=lambda cid, oid: (
                        calls.append(("release", cid, oid)), True)[1]), \
         mock.patch("dispatch_v2.panel_watcher._remove_stops_on_return",
                    side_effect=lambda cid, oid: calls.append(
                        ("return_release", cid, oid))), \
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


def test_disappeared_return_flag_on_releases_plan_of_state_courier(monkeypatch):
    """RETURN 8/9 po zniknięciu z HTML: przy ON zwalnia STARY plan ze state,
    nigdy kuriera z surowej odpowiedzi (ten może być już inny albo pusty)."""
    monkeypatch.setattr(C, "ENABLE_REASSIGN_OLD_PLAN_RELEASE", True)
    for oid, status_id, raw_cid in (
            ("467490", 8, 310),
            ("467491", 9, None)):
        state = {oid: {"order_id": oid, "courier_id": "207",
                       "status": "assigned", "delivery_address": "X"}}
        calls, _ = _run_diff_with_recorders(
            parsed=_mock_parsed(),
            state=state,
            raw_fetches={oid: _raw_response(oid, raw_cid, status_id)},
        )
        assert calls == [("return_release", "207", oid)]


def test_disappeared_return_flag_off_keeps_old_plan(monkeypatch):
    """OFF zachowuje dotychczasową ścieżkę RETURN: event/state bez release planu."""
    monkeypatch.setattr(C, "ENABLE_REASSIGN_OLD_PLAN_RELEASE", False)
    oid = "467492"
    state = {oid: {"order_id": oid, "courier_id": "207",
                   "status": "assigned", "delivery_address": "X"}}
    calls, _ = _run_diff_with_recorders(
        parsed=_mock_parsed(),
        state=state,
        raw_fetches={oid: _raw_response(oid, None, 8)},
    )
    assert calls == []


def test_disappeared_return_deduped_event_does_not_release(monkeypatch):
    """Brak nowego eventu (dedupe) = brak transition state i brak release planu."""
    monkeypatch.setattr(C, "ENABLE_REASSIGN_OLD_PLAN_RELEASE", True)
    oid = "467493"
    state = {oid: {"order_id": oid, "courier_id": "207",
                   "status": "assigned", "delivery_address": "X"}}
    calls, _ = _run_diff_with_recorders(
        parsed=_mock_parsed(),
        state=state,
        raw_fetches={oid: _raw_response(oid, None, 9)},
        audit_emitted=False,
    )
    assert calls == []


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


def test_reassign_branch_int_state_cid_no_false_release():
    """v2 (Sol pkt 1): INT 207 w state vs \"207\" z panelu — bez normalizacji
    robił FAŁSZYWY reassign (a z fixem release zdarłby stop AKTUALNEMU
    kurierowi). Po normalizacji: ten sam kurier → ZERO emit/release/signal."""
    state = {"467502": {"order_id": "467502", "courier_id": 207,   # INT!
                        "status": "assigned", "delivery_address": "X"}}
    calls, stats = _run_diff_with_recorders(
        parsed=_mock_parsed(order_ids=["467502"], assigned_ids={"467502"}),
        state=state,
        raw_fetches={"467502": _raw_response("467502", 207)},
    )
    assert calls == [], "int state-cid == panel-cid → ŻADNEGO fałszywego release"
    assert stats["assigned"] == 0


def test_reassign_branch_int_state_cid_real_reassign_uses_str():
    """v2: realny przerzut przy INT cid w state — helper dostaje STR starego."""
    state = {"467503": {"order_id": "467503", "courier_id": 207,   # INT!
                        "status": "assigned", "delivery_address": "X"}}
    calls, _ = _run_diff_with_recorders(
        parsed=_mock_parsed(order_ids=["467503"], assigned_ids={"467503"}),
        state=state,
        raw_fetches={"467503": _raw_response("467503", 310)},
    )
    assert calls == [("release", "207", "467503"), ("signal", "467503", "310")]


def test_one_tick_both_paths_single_release_single_signal():
    """v2 (Sol pkt 2): oba tory widzą TEN SAM stale snapshot state — jeden realny
    przerzut łapany przez branch reassign ORAZ packs w JEDNYM ticku dawał
    2× release + 2× signal. Dedupe released_this_tick: dokładnie 1 release,
    1 signal (packs skip; zbiór zasilany tylko przy fladze ON)."""
    state = {"467800": {"order_id": "467800", "courier_id": "207",
                        "status": "assigned", "delivery_address": "X"}}
    calls, stats = _run_diff_with_recorders(
        parsed=_mock_parsed(order_ids=["467800"], assigned_ids={"467800"},
                            courier_packs={"Nowy K": ["467800"]}),
        state=state,
        raw_fetches={"467800": _raw_response("467800", 310)},
        kurier_ids={"Nowy K": 310},
    )
    assert calls == [("release", "207", "467800"), ("signal", "467800", "310")], \
        "jeden przerzut = DOKŁADNIE jeden release i jeden signal w ticku"
    assert stats["assigned"] == 1


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
