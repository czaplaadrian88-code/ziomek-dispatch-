"""Working-override GRAFIK-CAP (Adrian 2026-06-07) — fix "Ziomek proponuje kuriera po zmianie".

Bug (live 2026-06-07): komenda "Mateusz pracuje" wpisana o 17:43 (domyślny koniec 24:00)
trzymała Mateusza O (cid 413) w puli dispatchowalnej aż do północy, mimo że jego realny
grafik kończył się o 21:00 → Ziomek proponował go ~21:52 (zlecenie z Restauracji Kumar's),
koordynator nadpisywał ręcznie. Override (FALLBACK) ustawiał shift_end = koniec override'a
(24:00), ignorując realny koniec grafiku.

Fix: gdy override z DOMYŚLNYM końcem został dodany W TRAKCIE/PRZED realną zmianą
(added_at <= grafik_end), efektywny shift_end przycinany do min(override_end, grafik_end).

Coverage:
  helper pure (_effective_working_override_shift_end):
    1  domyślny 24:00 + dodany w trakcie + grafik 21:00 → cap do 21:00 (BUG Mateusza)
    2  jawny "do 23" (end_explicit) → 23:00 (NIE capowane — uszanuj operatora)
    3  dodany PO końcu grafiku (added_at > grafik_end) → 24:00 (realna druga zmiana)
    4  spoza grafiku (brak entry) → 24:00 (brak innego źródła końca)
    5  flaga OFF → 24:00 (legacy, bez przycięcia)
    6  legacy entry bez "end_explicit" (żywy wpis 413) → cap do 21:00 (backward-compat)
    7  added_at brak/parse-fail → override_end (fail-open, bez capa)
  fleet layer (courier_resolver.dispatchable_fleet):
    8  reprodukcja live: Mateusz O (413) po zmianie + domyślny override → WYKLUCZONY (flaga ON)
    9  ta sama sytuacja, flaga OFF → DISPATCHOWALNY (regresja-guard / legacy)
   10 jawny "do <przyszłość>" po zmianie grafiku → DISPATCHOWALNY (nie capowany)

Uruchamia się przez pytest (jak reszta dispatch tests).
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

os.environ.setdefault("ENABLE_WORKING_OVERRIDE", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import schedule_utils  # noqa: E402
from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2 import manual_overrides as mo  # noqa: E402
from dispatch_v2.courier_resolver import (  # noqa: E402
    CourierState, dispatchable_fleet, _effective_working_override_shift_end as _eff,
)

WAW = ZoneInfo("Europe/Warsaw")
_EARLY = "2000-01-01T00:00:00+00:00"   # zawsze <= grafik_end dziś (dodany "w trakcie")
_LATE = "2099-01-01T00:00:00+00:00"    # zawsze >  grafik_end dziś (dodany "po zmianie")


def _hm(dt):
    return dt.strftime("%H:%M") if dt is not None else None


# ---------------- helper pure (deterministyczne) ----------------

def test_1_default_added_during_caps_to_grafik():
    end = _eff({"end": "24:00", "end_explicit": False, "added_at": _EARLY},
               {"start": "13:00", "end": "21:00"}, True)
    assert _hm(end) == "21:00"


def test_2_explicit_end_not_capped():
    end = _eff({"end": "23:00", "end_explicit": True, "added_at": _EARLY},
               {"start": "13:00", "end": "21:00"}, True)
    assert _hm(end) == "23:00"


def test_3_added_after_grafik_end_not_capped():
    # grafik skończył się o 15:00, override dodany "po" (2099) = realna druga zmiana → 24:00
    end = _eff({"end": "24:00", "end_explicit": False, "added_at": _LATE},
               {"start": "13:00", "end": "15:00"}, True)
    assert _hm(end) == "00:00"  # 24:00 = jutro 00:00


def test_4_no_grafik_entry_keeps_override_end():
    end = _eff({"end": "24:00", "end_explicit": False, "added_at": _EARLY}, None, True)
    assert _hm(end) == "00:00"


def test_5_flag_off_keeps_override_end():
    end = _eff({"end": "24:00", "end_explicit": False, "added_at": _EARLY},
               {"start": "13:00", "end": "21:00"}, False)
    assert _hm(end) == "00:00"


def test_6_legacy_entry_without_explicit_key_caps():
    # Żywy wpis 413 nie ma pola "end_explicit" → traktowany jako domyślny → cap.
    end = _eff({"end": "24:00", "added_at": _EARLY},
               {"start": "13:00", "end": "21:00"}, True)
    assert _hm(end) == "21:00"


def test_7_missing_added_at_fails_open():
    # Brak added_at → nie da się ocenić "w trakcie vs po" → bez capa (fail-open na override_end).
    end = _eff({"end": "24:00", "end_explicit": False},
               {"start": "13:00", "end": "21:00"}, True)
    assert _hm(end) == "00:00"


# ---------------- fleet layer (dispatchable_fleet) ----------------

def _patch_fleet(monkeypatch, schedule, working):
    monkeypatch.setattr(schedule_utils, "load_schedule", lambda: schedule)
    monkeypatch.setattr(schedule_utils, "is_schedule_stale", lambda: False)
    # Wymuś gałąź FALLBACK deterministycznie (kurier NIE na realnej zmianie teraz).
    monkeypatch.setattr(schedule_utils, "is_on_shift", lambda name, sch: (False, "po zmianie"))
    monkeypatch.setattr(schedule_utils, "match_courier", lambda name, sch: name if name in sch else None)
    monkeypatch.setattr(mo, "get_excluded", lambda: [])
    monkeypatch.setattr(mo, "get_working", lambda: working)


def _grafik_ended_end_hhmm():
    """Koniec grafiku 1h temu (Warsaw) jako HH:MM — deterministycznie w przeszłości,
    poza brzegiem północy. Zwraca None gdy now < 01:00 (wrap → pomiń strict assert)."""
    now_w = datetime.now(WAW)
    if now_w.hour < 1:
        return None
    return (now_w - timedelta(hours=1)).strftime("%H:%M")


def test_8_live_mateusz_after_shift_excluded(monkeypatch):
    end_hhmm = _grafik_ended_end_hhmm()
    if end_hhmm is None:
        return  # brzeg północy — pomijamy strict assert (logikę pokrywają testy 1-7)
    sched = {"Mateusz O": {"start": "00:00", "end": end_hhmm}}
    working = {"413": {"start": "17:43", "end": "24:00", "added_at": _EARLY, "name": "Mateusz O"}}
    _patch_fleet(monkeypatch, sched, working)
    cs = CourierState(courier_id="413", pos=(53.13, 23.16), pos_source="gps", name="Mateusz O")
    res = dispatchable_fleet(fleet={"413": cs})
    assert all(c.courier_id != "413" for c in res), \
        "Mateusz O po realnej zmianie z domyślnym override NIE powinien być proponowany (cap ON)"


def test_9_flag_off_legacy_includes(monkeypatch):
    end_hhmm = _grafik_ended_end_hhmm()
    if end_hhmm is None:
        return
    monkeypatch.setattr(C, "ENABLE_WORKING_OVERRIDE_GRAFIK_CAP", False)
    sched = {"Mateusz O": {"start": "00:00", "end": end_hhmm}}
    working = {"413": {"start": "17:43", "end": "24:00", "added_at": _EARLY, "name": "Mateusz O"}}
    _patch_fleet(monkeypatch, sched, working)
    cs = CourierState(courier_id="413", pos=(53.13, 23.16), pos_source="gps", name="Mateusz O")
    res = dispatchable_fleet(fleet={"413": cs})
    assert any(c.courier_id == "413" for c in res), \
        "flaga OFF = legacy: override 24:00 trzyma kuriera (regresja-guard)"


def test_10_explicit_future_end_after_shift_included(monkeypatch):
    now_w = datetime.now(WAW)
    if not (1 <= now_w.hour <= 22):
        return  # potrzebujemy past grafik_end ORAZ future override end w tej samej dobie
    grafik_end = (now_w - timedelta(hours=1)).strftime("%H:%M")
    override_end = (now_w + timedelta(hours=1)).strftime("%H:%M")
    sched = {"Mateusz O": {"start": "00:00", "end": grafik_end}}
    working = {"413": {"start": "17:43", "end": override_end, "end_explicit": True,
                       "added_at": _EARLY, "name": "Mateusz O"}}
    _patch_fleet(monkeypatch, sched, working)
    cs = CourierState(courier_id="413", pos=(53.13, 23.16), pos_source="gps", name="Mateusz O")
    res = dispatchable_fleet(fleet={"413": cs})
    assert any(c.courier_id == "413" for c in res), \
        "jawny 'pracuje do <przyszłość>' po zmianie grafiku ma być respektowany (nie capowany)"
