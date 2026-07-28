"""AUTON-02 / T2 — silnikowa bramka autoryzacji WŁAŚCICIELA na auto-assign (ODR-002).

Po co ta warstwa (a nie sam PIN w panelu): PIN-gate `d42da13` żyje w panelu
(`nadajesz_clone`, POST /coordinator/auto-assign) i chroni WYŁĄCZNIE ścieżkę
przycisku w konsoli. `ENABLE_AUTO_ASSIGN` mieszka w `flags.json` — pliku, który
pisze każdy proces/agent/merge z prawem zapisu. Panelowy PIN da się więc ominąć
w całości, edytując plik. Dowód realny: 2026-07-21 20:57 flaga weszła do gita
workspace w cudzym merge'u, bez śladu decyzji (memory
`enable-auto-assign-true-bez-pin-2026-07-26`).

Kontrakt tej bramki: sam `ENABLE_AUTO_ASSIGN=true` NIE jest już upoważnieniem.
Stan ON musi być WYTŁUMACZALNY świeżym, PIN-owanym podniesieniem zapisanym przez
panel w `coordinator_assign_audit.jsonl` (`kind=auto_assign_toggle`, `ok=true`,
`value=true`, `pin_verified=true`). Cokolwiek innego — brak pliku, brak wiersza,
`pin_verified` inne niż true, ostatni toggle na false, wiersz przeterminowany —
= ZERO wykonania (fail-closed).

Uruchom: /root/.openclaw/venvs/dispatch/bin/python -m pytest \
    tests/test_auto_assign_owner_auth_gate.py -q
"""
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from dispatch_v2 import auto_assign_executor as E
from dispatch_v2 import common as C

NOW = datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_t5_card_gate(monkeypatch):
    """Ten plik izoluje krok 1b; parser/hash/scope/liczniki kroku 1c mają
    własne testy w ``test_authority_card.py``."""
    monkeypatch.setattr(
        E,
        "_authority_card_gate",
        lambda *args, **kwargs: (
            True,
            "ok",
            {"state": {}, "state_path": None, "enforced": False},
        ),
    )


def _record(verdict="PROPOSE", oid="480300", cid="101", name="Kurier Testowy",
            target_min=12):
    tgt = (NOW + timedelta(minutes=target_min)).isoformat()
    return {
        "verdict": verdict,
        "order_id": oid,
        "best": {"courier_id": cid, "name": name, "score": 55.0,
                 "target_pickup_at": tgt},
    }


def _result(would=True):
    return SimpleNamespace(would_auto_assign=would)


@pytest.fixture
def runner_spy():
    calls = []

    def runner(oid, name, minutes):
        calls.append((oid, name, minutes))
        return True, "ok"
    runner.calls = calls
    return runner


@pytest.fixture
def notify_spy():
    msgs = []

    def notify(text):
        msgs.append(text)
    notify.msgs = msgs
    return notify


@pytest.fixture
def state_path(tmp_path):
    return str(tmp_path / "auto_assign_state.json")


@pytest.fixture
def isolated_llog(tmp_path, monkeypatch):
    p = tmp_path / "learning_log.jsonl"
    p.write_text("")
    monkeypatch.setattr(E, "LEARNING_LOG_PATH", str(p))
    return p


@pytest.fixture
def audit_path(tmp_path, monkeypatch):
    """Dziennik audytu koordynatora pod kontrolą testu — NIGDY żywy plik hosta."""
    p = tmp_path / "coordinator_assign_audit.jsonl"
    monkeypatch.setattr(E, "COORDINATOR_AUDIT_PATH", str(p))
    return p


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)


def _write_rows(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _toggle(*, value=True, pin_verified=True, ok=True, ts=None, minutes_ago=5):
    ts = ts or (NOW - timedelta(minutes=minutes_ago)).isoformat()
    return {"ts": ts, "kind": "auto_assign_toggle", "actor": "ac@nadajesz.pl",
            "requested": value, "ok": ok, "rc": 0, "value": value,
            "from": not value, "pin_verified": pin_verified}


def _run(record=None, **kw):
    return E.maybe_execute(record or _record(), _result(), {}, now=NOW, **kw)


# ---------------- NEGATYWNY ORACLE: ON bez upoważnienia = zero wykonania ----------------

def test_on_without_audit_file_blocked(flag_on, audit_path, runner_spy,
                                       notify_spy, state_path, isolated_llog):
    """Świeży host / skasowany dziennik = brak dowodu = fail-closed, nie 'otwarte'."""
    assert not audit_path.exists()
    out = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out is not None and out.get("blocked") == "owner_auth_missing"
    assert out.get("reason") == "audit_unreadable"
    assert runner_spy.calls == []
    assert not os.path.exists(state_path)


def test_on_without_any_toggle_row_blocked(flag_on, audit_path, runner_spy,
                                           notify_spy, state_path, isolated_llog):
    """Dziennik istnieje, ale nikt nigdy nie podniósł flagi przyciskiem — TO jest
    dokładnie kształt incydentu 20-21.07 (flaga z merge'a, zero toggle'a)."""
    _write_rows(audit_path, [{"ts": (NOW - timedelta(hours=1)).isoformat(),
                              "kind": "assign_attempt", "actor": "x"}])
    out = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out.get("blocked") == "owner_auth_missing"
    assert out.get("reason") == "no_toggle_row"
    assert runner_spy.calls == []


def test_on_toggle_without_pin_blocked(flag_on, audit_path, runner_spy,
                                       notify_spy, state_path, isolated_llog):
    """Podniesienie zapisane, ale BEZ zweryfikowanego PIN-u (np. hash pusty w
    starym deployu panelu, albo wiersz sprzed d42da13 — pole nie istnieje)."""
    _write_rows(audit_path, [_toggle(pin_verified=False)])
    out = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out.get("blocked") == "owner_auth_missing"
    assert out.get("reason") == "not_pin_verified"
    assert runner_spy.calls == []


def test_on_toggle_pin_field_absent_blocked(flag_on, audit_path, runner_spy,
                                            notify_spy, state_path, isolated_llog):
    """Wiersz sprzed PIN-gate (pole `pin_verified` w ogóle nie istnieje) NIE
    upoważnia — brak pola to nie zgoda."""
    row = _toggle()
    row.pop("pin_verified")
    _write_rows(audit_path, [row])
    out = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out.get("blocked") == "owner_auth_missing"
    assert out.get("reason") == "not_pin_verified"


def test_on_last_toggle_was_disable_blocked(flag_on, audit_path, runner_spy,
                                            notify_spy, state_path, isolated_llog):
    """Owner PIN-em podniósł, potem WYŁĄCZYŁ (killswitch bez PIN-u), a flaga jest
    znów ON → stan nietłumaczalny (ktoś podniósł poza panelem) = fail-closed."""
    _write_rows(audit_path, [
        _toggle(value=True, minutes_ago=90),
        _toggle(value=False, pin_verified=False, minutes_ago=30),
    ])
    out = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out.get("blocked") == "owner_auth_missing"
    assert out.get("reason") == "last_toggle_disabled"
    assert runner_spy.calls == []


def test_on_failed_toggle_does_not_authorize(flag_on, audit_path, runner_spy,
                                             notify_spy, state_path, isolated_llog):
    """Wiersz z `ok=false` (flags_admin padł) to PRÓBA, nie upoważnienie."""
    _write_rows(audit_path, [_toggle(ok=False)])
    out = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out.get("blocked") == "owner_auth_missing"
    assert out.get("reason") == "no_toggle_row"


def test_on_stale_authorization_blocked(flag_on, audit_path, runner_spy,
                                        notify_spy, state_path, isolated_llog):
    """⭐ INCYDENT 1:1 — owner podniósł PIN-em 2026-07-20 13:31Z, a flaga została ON
    przez TYDZIEŃ. Upoważnienie ODR-002 jest ZDARZENIEM, nie stanem wieczystym:
    po TTL wygasa i executor odmawia, dopóki owner nie podniesie ponownie."""
    _write_rows(audit_path, [_toggle(ts="2026-07-20T13:31:28.063397+00:00")])
    out = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out.get("blocked") == "owner_auth_missing"
    assert out.get("reason") == "authorization_stale"
    assert runner_spy.calls == []


def test_on_future_dated_authorization_blocked(flag_on, audit_path, runner_spy,
                                               notify_spy, state_path, isolated_llog):
    """Wiersz z PRZYSZŁOŚCI nie upoważnia. Bez tego TTL jest do obejścia jedną
    datą (`(now - ts)` wychodzi ujemne → 'młodsze niż TTL' → wieczna zgoda), a
    przy okazji przesunięty zegar hosta cicho otwierałby bramkę."""
    _write_rows(audit_path, [_toggle(minutes_ago=-600)])
    out = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out.get("blocked") == "owner_auth_missing"
    assert out.get("reason") == "authorization_future"
    assert runner_spy.calls == []


def test_small_clock_skew_tolerated(flag_on, audit_path, runner_spy, notify_spy,
                                    state_path, isolated_llog):
    """…ale sekundowy rozjazd zegarów panel↔silnik nie może blokować ownera."""
    _write_rows(audit_path, [_toggle(minutes_ago=-1)])
    out = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out.get("executed") is True


def test_on_unparsable_ts_blocked(flag_on, audit_path, runner_spy, notify_spy,
                                  state_path, isolated_llog):
    """Nieczytelny znacznik czasu = nie da się dowieść świeżości = odmowa."""
    _write_rows(audit_path, [_toggle(ts="wczoraj")])
    out = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out.get("blocked") == "owner_auth_missing"
    assert out.get("reason") == "authorization_stale"


def test_on_corrupt_lines_do_not_authorize(flag_on, audit_path, runner_spy,
                                           notify_spy, state_path, isolated_llog):
    """Śmieci w pliku nie wywracają executora ANI nie przechodzą jako zgoda."""
    audit_path.write_text("{nie-json\n[]\n\n", encoding="utf-8")
    out = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out.get("blocked") == "owner_auth_missing"
    assert out.get("reason") == "authorization_audit_corrupt"


def test_corrupt_tail_after_valid_toggle_invalidates_authorization(
    flag_on,
    audit_path,
    runner_spy,
    notify_spy,
    state_path,
    isolated_llog,
):
    """RED I4: ucięty ogon po poprawnym toggle nie może odsłonić starszej zgody."""
    audit_path.write_text(
        json.dumps(_toggle(minutes_ago=5)) + "\n{broken",
        encoding="utf-8",
    )
    out = _run(
        assign_runner=runner_spy,
        notifier=notify_spy,
        state_path=state_path,
    )
    assert out.get("blocked") == "owner_auth_missing"
    assert out.get("reason") == "authorization_audit_corrupt"
    assert runner_spy.calls == []


# ---------------- ŚCIEŻKA POZYTYWNA: świeże PIN-owane podniesienie ----------------

def test_fresh_pin_raise_passes_gate(flag_on, audit_path, runner_spy, notify_spy,
                                     state_path, isolated_llog):
    """Z upoważnieniem bramka PRZEPUSZCZA — dowód, że test negatywny mierzy
    bramkę, a nie to, że executor i tak nic nie robi."""
    _write_rows(audit_path, [_toggle(minutes_ago=5)])
    out = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out is not None
    assert out.get("blocked") != "owner_auth_missing"
    assert out.get("executed") is True
    assert runner_spy.calls == [("480300", "Kurier Testowy", 12)]


def test_fresh_raise_after_older_disable_passes(flag_on, audit_path, runner_spy,
                                                notify_spy, state_path, isolated_llog):
    """Kolejność ma znaczenie: liczy się OSTATNI udany toggle, nie jakikolwiek."""
    _write_rows(audit_path, [
        _toggle(value=True, minutes_ago=600),
        _toggle(value=False, pin_verified=False, minutes_ago=300),
        _toggle(value=True, minutes_ago=3),
    ])
    out = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out.get("executed") is True


def test_ttl_boundary_configurable(flag_on, audit_path, monkeypatch, runner_spy,
                                   notify_spy, state_path, isolated_llog):
    """TTL to pokrętło operacyjne (flags.json/env), nie stała wmurowana w kod —
    karta canary ma własne okno ważności i musi móc je zacieśnić."""
    _write_rows(audit_path, [_toggle(minutes_ago=120)])
    monkeypatch.setenv("AUTO_ASSIGN_OWNER_AUTH_TTL_SEC", "60")
    out = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out.get("reason") == "authorization_stale"
    monkeypatch.setenv("AUTO_ASSIGN_OWNER_AUTH_TTL_SEC", "999999")
    out2 = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out2.get("executed") is True


# ---------------- KONTRAKT ZERO-I/O przy OFF (nie wolno go zepsuć) ----------------

def test_off_path_never_touches_audit(audit_path, monkeypatch, runner_spy,
                                      notify_spy, state_path, isolated_llog):
    """Przy ENABLE_AUTO_ASSIGN=false executor ma robić `return None` PIERWSZĄ
    linią — zero pracy, zero I/O (kontrakt AUTON-01). Bramka autoryzacji nie
    może tego naruszyć: dokładamy odczyt WYŁĄCZNIE na ścieżce ON."""
    reads = []
    real_open = open

    def spy_open(path, *a, **kw):
        reads.append(str(path))
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", spy_open)
    out = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out is None
    assert not any("coordinator_assign_audit" in r for r in reads)
    assert runner_spy.calls == []


# ---------------- KOLEJNOŚĆ: bramka PRZED jakąkolwiek pracą ----------------

def test_auth_gate_precedes_quality_gate(flag_on, audit_path, runner_spy,
                                         notify_spy, state_path, isolated_llog):
    """Brak upoważnienia wygrywa nad wszystkim innym — także gdy bramka
    jakościowa i tak by odrzuciła. Inaczej odmowa autoryzacji byłaby niewidoczna
    w logu (zwykłe `None`), a operator nie wiedziałby, że flaga jest 'lewa'."""
    _write_rows(audit_path, [_toggle(pin_verified=False)])
    out = E.maybe_execute(_record(), _result(would=False), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy,
                          state_path=state_path)
    assert out is not None and out.get("blocked") == "owner_auth_missing"


# ---------------- MUTACJE (zdjęcie bramki MUSI zaczerwienić) ----------------

def test_mutation_remove_auth_gate_reintroduces_incident(monkeypatch, flag_on,
                                                         audit_path, runner_spy,
                                                         notify_spy, state_path,
                                                         isolated_llog):
    """Odwrócenie bramki przywraca dokładnie stan z 20-26.07: `ENABLE_AUTO_ASSIGN`
    podniesione poza panelem (zero wierszy toggle) wykonuje realne przypisanie."""
    _write_rows(audit_path, [{"ts": NOW.isoformat(), "kind": "assign_attempt"}])
    real = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert real.get("blocked") == "owner_auth_missing"
    assert runner_spy.calls == []
    # MUTACJA: bramka zawsze przepuszcza (== kod sprzed T2).
    monkeypatch.setattr(E, "_owner_authorization", lambda *a, **k: (True, "ok"))
    mut = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert mut.get("executed") is True and mut != real
    assert runner_spy.calls == [("480300", "Kurier Testowy", 12)]


def test_mutation_ignoring_pin_field_reintroduces_bypass(monkeypatch, flag_on,
                                                         audit_path, runner_spy,
                                                         notify_spy, state_path,
                                                         isolated_llog):
    """Gdyby bramka przestała PATRZEĆ na `pin_verified`, sam fakt kliknięcia
    przycisku (bez PIN-u) znów by autoryzował — czyli ODR-002 bez egzekucji."""
    _write_rows(audit_path, [_toggle(pin_verified=False)])
    real = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert real.get("reason") == "not_pin_verified"
    monkeypatch.setattr(E, "_last_successful_toggle",
                        lambda p: _toggle(pin_verified=True))
    mut = _run(assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert mut.get("executed") is True and mut != real
