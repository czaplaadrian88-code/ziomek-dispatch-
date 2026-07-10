"""Working-override (Adrian 2026-06-01) — komenda "X pracuje" działa dla:
  (1) powracających po /stop (zdjęcie z excluded — zachowane),
  (2) kurierów SPOZA grafiku którzy zaczynają (NOWE — syntetyczny wpis grafiku, cid-keyed).

Coverage:
  parse layer (manual_overrides):
    1  "X pracuje" → dodaje working entry (cid) + uczciwy komunikat
    2  "X nie pracuje" → excluded + USUWA working entry
    3  "X pracuje do 22" → end=22:00 (parse 'do HH')
    4  _parse_shift_bounds 'od/do HH:MM' + default 24:00
    5  reset → czyści excluded ORAZ working
    6  brak cid → NIE dodaje do grafiku, instruuje /dopisz
  fleet layer (courier_resolver.dispatchable_fleet):
    7  spoza grafiku + working → DISPATCHOWALNY (autorytatywna gałąź)
    8  spoza grafiku BEZ working → "brak w grafiku" (regresja-guard)
    9  cid-keying: working dla cid=457 NIE przecieka na innego kuriera cid=999 (anti-ambiguity)
    10 brak GPS + working → syntetyczna pozycja (working_override_synthetic)
    11 flaga OFF → working ignorowane (off-grafik → excluded)
    12 "pracuje do HH" które już minęło → working_override_ended (skip)

Uruchamia się przez pytest (jak reszta dispatch tests).
"""
import os
import sys

os.environ.setdefault("ENABLE_WORKING_OVERRIDE", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import schedule_utils  # noqa: E402
from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2 import manual_overrides as mo  # noqa: E402
from dispatch_v2 import courier_resolver as cr  # noqa: E402
from dispatch_v2.courier_resolver import CourierState, dispatchable_fleet  # noqa: E402

_BIA = (53.1325, 23.1688)


def _patch_names(monkeypatch):
    monkeypatch.setattr(mo, "_load_names",
                        lambda: ["Adrian Cit", "Bartek O", "Adrian R"])
    monkeypatch.setattr(mo, "_load_name_to_cid",
                        lambda: {"Adrian Cit": 457, "Bartek O": 123, "Adrian R": 999})


def _patch_overrides_file(monkeypatch, tmp_path):
    monkeypatch.setattr(mo, "OVERRIDES_PATH", str(tmp_path / "manual_overrides.json"))


def _patch_schedule(monkeypatch, schedule, stale=False):
    monkeypatch.setattr(schedule_utils, "load_schedule", lambda: schedule)
    monkeypatch.setattr(schedule_utils, "is_schedule_stale", lambda: stale)
    monkeypatch.setattr(mo, "get_excluded_cids", lambda: set())


# ---------------- parse layer ----------------

def test_1_pracuje_adds_working(tmp_path, monkeypatch):
    _patch_overrides_file(monkeypatch, tmp_path)
    _patch_names(monkeypatch)
    action, resp = mo.parse_command("Adrian pracuje")
    assert action == "include"
    w = mo.get_working()
    assert "457" in w and w["457"]["end"] == "24:00"
    assert "proponował" in resp  # uczciwe potwierdzenie, NIE mylące "przywrócony"


def test_2_nie_pracuje_removes_working(tmp_path, monkeypatch):
    _patch_overrides_file(monkeypatch, tmp_path)
    _patch_names(monkeypatch)
    mo.parse_command("Adrian pracuje")
    assert "457" in mo.get_working()
    action, resp = mo.parse_command("Adrian nie pracuje")
    assert action == "exclude"
    assert "457" not in mo.get_working()
    assert "Adrian Cit" in mo.get_excluded()


def test_3_pracuje_do_hour(tmp_path, monkeypatch):
    _patch_overrides_file(monkeypatch, tmp_path)
    _patch_names(monkeypatch)
    mo.parse_command("Bartek pracuje do 22")
    assert mo.get_working()["123"]["end"] == "22:00"


def test_4_parse_bounds():
    s, e, ex = mo._parse_shift_bounds("bartek pracuje od 15:30 do 23")
    assert s == "15:30" and e == "23:00" and ex is True  # jawny 'do' → end_explicit
    _s, e2, ex2 = mo._parse_shift_bounds("adrian pracuje")
    assert e2 == "24:00" and ex2 is False  # domyślny koniec → NIE jawny


def test_5_reset_clears_working(tmp_path, monkeypatch):
    _patch_overrides_file(monkeypatch, tmp_path)
    _patch_names(monkeypatch)
    mo.parse_command("Adrian pracuje")
    mo.parse_command("Bartek nie pracuje")
    action, _ = mo.parse_command("reset")
    assert action == "reset"
    assert mo.get_working() == {}
    assert mo.get_excluded() == []


def test_6_unknown_cid_no_grafik_add(tmp_path, monkeypatch):
    _patch_overrides_file(monkeypatch, tmp_path)
    monkeypatch.setattr(mo, "_load_names", lambda: ["Ghost X"])
    monkeypatch.setattr(mo, "_load_name_to_cid", lambda: {})  # brak cid
    action, resp = mo.parse_command("Ghost pracuje")
    assert action == "include"
    assert mo.get_working() == {}        # bez cid nie da się zakotwiczyć
    assert "dopisz" in resp.lower()


# ---------------- fleet layer ----------------

def test_7_offgrafik_with_working_dispatchable(monkeypatch):
    _patch_schedule(monkeypatch, {"Bartek O": {"start": "08:00", "end": "22:00"}})
    monkeypatch.setattr(mo, "get_excluded", lambda: [])
    monkeypatch.setattr(mo, "get_working", lambda: {"457": {"start": "00:00", "end": "24:00"}})
    cs = CourierState(courier_id="457", pos=(53.13, 23.16), pos_source="gps", name="Adrian Cit")
    res = dispatchable_fleet(fleet={"457": cs})
    assert any(c.courier_id == "457" for c in res)


def test_8_offgrafik_no_working_excluded(monkeypatch):
    _patch_schedule(monkeypatch, {"Bartek O": {"start": "08:00", "end": "22:00"}})
    monkeypatch.setattr(mo, "get_excluded", lambda: [])
    monkeypatch.setattr(mo, "get_working", lambda: {})
    cs = CourierState(courier_id="457", pos=(53.13, 23.16), pos_source="gps", name="Adrian Cit")
    res = dispatchable_fleet(fleet={"457": cs})
    assert all(c.courier_id != "457" for c in res)  # brak w grafiku


def test_9_cidkeyed_no_leak_to_other_courier(monkeypatch):
    # Override dla 457 (Adrian Cit) NIE może uczynić dispatchowalnym 999 (Adrian R) —
    # to dokładnie landmine ambiguity z V3.25 ("Jakub OL"→"Jakub Leoniuk"), którego
    # unikamy przez cid-keying zamiast name-merge do grafiku.
    _patch_schedule(monkeypatch, {"Bartek O": {"start": "08:00", "end": "22:00"}})
    monkeypatch.setattr(mo, "get_excluded", lambda: [])
    monkeypatch.setattr(mo, "get_working", lambda: {"457": {"start": "00:00", "end": "24:00"}})
    cs = CourierState(courier_id="999", pos=(53.13, 23.16), pos_source="gps", name="Adrian R")
    res = dispatchable_fleet(fleet={"999": cs})
    assert all(c.courier_id != "999" for c in res)


def test_10_synthetic_pos_when_no_gps(monkeypatch):
    _patch_schedule(monkeypatch, {"Bartek O": {"start": "08:00", "end": "22:00"}})
    monkeypatch.setattr(mo, "get_excluded", lambda: [])
    monkeypatch.setattr(mo, "get_working", lambda: {"457": {"start": "00:00", "end": "24:00"}})
    cs = CourierState(courier_id="457", pos=None, pos_source="none", name="Adrian Cit")
    res = dispatchable_fleet(fleet={"457": cs})
    got = [c for c in res if c.courier_id == "457"]
    assert got and got[0].pos is not None
    assert got[0].pos_source == "working_override_synthetic"


def test_11_flag_off_ignores_working(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_WORKING_OVERRIDE", False)
    _patch_schedule(monkeypatch, {"Bartek O": {"start": "08:00", "end": "22:00"}})
    monkeypatch.setattr(mo, "get_excluded", lambda: [])
    monkeypatch.setattr(mo, "get_working", lambda: {"457": {"start": "00:00", "end": "24:00"}})
    cs = CourierState(courier_id="457", pos=(53.13, 23.16), pos_source="gps", name="Adrian Cit")
    res = dispatchable_fleet(fleet={"457": cs})
    assert all(c.courier_id != "457" for c in res)  # off-grafik, override wyłączony


def test_12_working_ended_skipped(monkeypatch):
    # "pracuje do 00:01" — o ile teraz NIE jest 00:00-00:01, zmiana już minęła → skip.
    _patch_schedule(monkeypatch, {"Bartek O": {"start": "08:00", "end": "22:00"}})
    monkeypatch.setattr(mo, "get_excluded", lambda: [])
    monkeypatch.setattr(mo, "get_working", lambda: {"457": {"start": "00:00", "end": "00:01"}})
    cs = CourierState(courier_id="457", pos=(53.13, 23.16), pos_source="gps", name="Adrian Cit")
    res = dispatchable_fleet(fleet={"457": cs})
    # bezpieczny assert tylko gdy teraz > 00:01 Warsaw (prawie zawsze w testach CI/dev)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now_w = datetime.now(ZoneInfo("Europe/Warsaw"))
    if (now_w.hour, now_w.minute) > (0, 1):
        assert all(c.courier_id != "457" for c in res)


def test_13_real_shift_wins_over_working(monkeypatch):
    # FALLBACK: kurier NA realnej zmianie (grafik 00:00–23:59) + working "do 24:00" →
    # realny grafik wygrywa, shift_end = realny (NIE rozszerzony do końca dnia). To chroni
    # powracającego po /stop, który jest w grafiku, przed wydłużeniem zmiany.
    _patch_schedule(monkeypatch, {"Bartek O": {"start": "00:00", "end": "23:59"}})
    monkeypatch.setattr(mo, "get_excluded", lambda: [])
    monkeypatch.setattr(mo, "get_working", lambda: {"123": {"start": "00:00", "end": "24:00"}})
    cs = CourierState(courier_id="123", pos=(53.13, 23.16), pos_source="gps", name="Bartek O")
    res = dispatchable_fleet(fleet={"123": cs})
    got = [c for c in res if c.courier_id == "123"]
    assert got, "Bartek powinien być dispatchowalny (na realnej zmianie)"
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now_w = datetime.now(ZoneInfo("Europe/Warsaw"))
    if now_w.hour < 23:  # poza minutą brzegową 23:59 — deterministycznie na zmianie
        assert got[0].shift_end is not None and got[0].shift_end.strftime("%H:%M") == "23:59", \
            f"realny grafik powinien wygrać (23:59), got {got[0].shift_end}"


def test_14_jest_only_no_grafik_add(tmp_path, monkeypatch):
    # 'jest' (słaby keyword) tylko un-excluduje — NIE dodaje do grafiku (anti false-positive
    # typu "gdzie jest bartek").
    _patch_overrides_file(monkeypatch, tmp_path)
    _patch_names(monkeypatch)
    action, _resp = mo.parse_command("Bartek jest")
    assert action == "include"
    assert mo.get_working() == {}  # 'jest' nie tworzy working-override
