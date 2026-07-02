"""Lane S2 grafik (audyt 2.0 finding H) — strażnik fixów H1 + H1b + H2.

Cel:
  H1  — dzień grafiku liczony w Warsaw wall-clock (ZoneInfo), NIE naive-UTC.
        Okno Warsaw 00:00-02:00 przestaje ładować WCZORAJSZY grafik.
  H1b — schedule_utils.load_schedule liczy `today` przez _now_warsaw (nie UTC).
  H2  — literówka godziny w JEDNEJ komórce NIE kasuje całego wpisu kuriera
        (za flagą ENABLE_GRAFIK_ENTRY_SALVAGE, default OFF); ON = kurier ZOSTAJE
        + WARNING (literówka WIDOCZNA).

Testy BEHAWIORALNE (C13): zamrażamy `now` na realny instant i sprawdzamy WYNIK;
kill-testy mutują fix (ZoneInfo→naive) i wymagają, by wynik się ZEPSUŁ = strażnik
ma zęby. Plus PARYTET: staged kopia różni się od żywego pliku WYŁĄCZNIE
zamierzonymi hunkami (strażnik „nieaktualizowanego mirrora", klasa L8-mapy).

WAŻNE (izolacja, C12(e)): pliki grafiku żyją w `scripts/` (POZA repo dispatch_v2),
więc pracujemy na STAGED kopiach w `deploy_staging/scripts/` ładowanych PO ŚCIEŻCE
(importlib), sys.modules sprzątane try/finally.
"""
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest

# ── ścieżki (C12(e): względnie od pliku testu) ───────────────────────────
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STAGED_FETCH = os.path.join(_REPO, "deploy_staging", "scripts", "fetch_schedule.py")
_STAGED_UTILS = os.path.join(_REPO, "deploy_staging", "scripts", "schedule_utils.py")
# Żywe pliki (READ-ONLY) — do testu PARYTETU.
_LIVE_FETCH = "/root/.openclaw/workspace/scripts/fetch_schedule.py"
_LIVE_UTILS = "/root/.openclaw/workspace/scripts/schedule_utils.py"

# Instant graniczny: 2026-07-14 23:30 UTC = Warsaw 2026-07-15 01:30 (CEST, lato) →
# „dziś" po Warsaw = 15.07, po naive-UTC = 14.07 (bug). Analog zimowy: 14.12 23:30
# UTC = Warsaw 15.12 00:30 (CET).
_SUMMER_UTC = datetime(2026, 7, 14, 23, 30, tzinfo=timezone.utc)
_WINTER_UTC = datetime(2026, 12, 14, 23, 30, tzinfo=timezone.utc)


def _load_by_path(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FrozenDT(datetime):
    """datetime z zamrożonym now(). now(tz) = konwersja instantu do tz;
    now(None) = naive UTC (dokładnie to, co robi serwer w bugu H1)."""
    _utc = _SUMMER_UTC

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls._utc.astimezone(timezone.utc).replace(tzinfo=None)
        return cls._utc.astimezone(tz)


@pytest.fixture()
def fetch():
    assert os.path.exists(_STAGED_FETCH), f"brak staged fetch_schedule: {_STAGED_FETCH}"
    mod = _load_by_path(_STAGED_FETCH, "fetch_schedule_staged_wt")
    try:
        yield mod
    finally:
        sys.modules.pop("fetch_schedule_staged_wt", None)


@pytest.fixture()
def utils():
    assert os.path.exists(_STAGED_UTILS), f"brak staged schedule_utils: {_STAGED_UTILS}"
    mod = _load_by_path(_STAGED_UTILS, "schedule_utils_staged_wt")
    try:
        yield mod
    finally:
        sys.modules.pop("schedule_utils_staged_wt", None)


# ══════════════════════════════════════════════════════════════════════════
# H1 — today Warsaw wall-clock (LATO + ZIMA) + kill/mutation
# ══════════════════════════════════════════════════════════════════════════
def test_h1_summer_window_uses_warsaw_day(fetch, monkeypatch):
    # Warsaw 15.07 01:30 (okno 00-02) — poprawny fix daje 15.07, nie 14.07.
    _FrozenDT._utc = _SUMMER_UTC
    monkeypatch.setattr(fetch, "datetime", _FrozenDT)
    assert fetch._today_warsaw() == "15-07-26"


def test_h1_summer_mutation_naive_utc_breaks(fetch, monkeypatch):
    # MUTATION-CHECK #1 (C13): _WARSAW→None ⇒ datetime.now(None) = naive UTC =
    # bug H1 → 14.07 (wczoraj). Dowód że strażnik gryzie.
    _FrozenDT._utc = _SUMMER_UTC
    monkeypatch.setattr(fetch, "datetime", _FrozenDT)
    good = fetch._today_warsaw()
    monkeypatch.setattr(fetch, "_WARSAW", None)
    bug = fetch._today_warsaw()
    assert good == "15-07-26"
    assert bug == "14-07-26"
    assert good != bug


def test_h1_winter_window_uses_warsaw_day(fetch, monkeypatch):
    # ZIMA (CET=+1): Warsaw 15.12 00:30 — fix daje 15.12, naive-UTC dałby 14.12.
    _FrozenDT._utc = _WINTER_UTC
    monkeypatch.setattr(fetch, "datetime", _FrozenDT)
    assert fetch._today_warsaw() == "15-12-26"


def test_h1_winter_mutation_naive_utc_breaks(fetch, monkeypatch):
    _FrozenDT._utc = _WINTER_UTC
    monkeypatch.setattr(fetch, "datetime", _FrozenDT)
    good = fetch._today_warsaw()
    monkeypatch.setattr(fetch, "_WARSAW", None)
    bug = fetch._today_warsaw()
    assert good == "15-12-26" and bug == "14-12-26" and good != bug


# ══════════════════════════════════════════════════════════════════════════
# H1b — schedule_utils.load_schedule liczy `today` przez _now_warsaw
# ══════════════════════════════════════════════════════════════════════════
def test_h1b_now_warsaw_summer_and_winter(utils, monkeypatch):
    monkeypatch.setattr(utils, "datetime", _FrozenDT)
    _FrozenDT._utc = _SUMMER_UTC
    assert utils._now_warsaw().strftime("%d-%m-%y") == "15-07-26"
    _FrozenDT._utc = _WINTER_UTC
    assert utils._now_warsaw().strftime("%d-%m-%y") == "15-12-26"


def test_h1b_now_warsaw_mutation_naive_utc_breaks(utils, monkeypatch):
    # _TZ→None ⇒ naive UTC = 14.07 (bug). Kill-test dla _now_warsaw.
    _FrozenDT._utc = _SUMMER_UTC
    monkeypatch.setattr(utils, "datetime", _FrozenDT)
    good = utils._now_warsaw().strftime("%d-%m-%y")
    monkeypatch.setattr(utils, "_TZ", None)
    bug = utils._now_warsaw().strftime("%d-%m-%y")
    assert good == "15-07-26" and bug == "14-07-26"


def test_h1b_load_schedule_source_uses_now_warsaw():
    # H1b + usunięcie fixed-offset fallbacku — asercja źródła (staged plik).
    src = open(_STAGED_UTILS, encoding="utf-8").read()
    assert 'today = _now_warsaw().strftime("%d-%m-%y")' in src
    assert 'today = datetime.now().strftime("%d-%m-%y")' not in src   # bug H1b usunięty
    assert "timezone(timedelta(hours=2))" not in src                  # fixed-offset bomba usunięta
    assert "except ImportError:" not in src                           # fail-loud, bez fallbacku


# ══════════════════════════════════════════════════════════════════════════
# H2 — literówka godziny NIE kasuje kuriera (za flagą, default OFF)
# ══════════════════════════════════════════════════════════════════════════
_CSV = (
    ",Imię,15-07-26,\n"
    ",Jan Kowalski,10:00,18:00\n"
    ",Anna Nowak,10:00,1O:00\n"          # end = literówka: litera 'O' zamiast zera
)
_TARGET = "15-07-26"


def _flags_file(tmp_path, value):
    p = tmp_path / "flags.json"
    p.write_text(json.dumps({"ENABLE_GRAFIK_ENTRY_SALVAGE": value}), encoding="utf-8")
    return str(p)


def test_h2_off_drops_courier_with_typo(fetch, monkeypatch, tmp_path):
    # OFF (default) = zachowanie DZIŚ: literówka → cały wpis None → kurier znika.
    monkeypatch.setattr(fetch, "FLAGS_PATH", _flags_file(tmp_path, False))
    sched = fetch.parse_schedule(_CSV, _TARGET)
    assert sched["Jan Kowalski"] == {"start": "10:00", "end": "18:00"}   # zdrowy nietknięty
    assert sched["Anna Nowak"] is None                                    # literówka → wypada z puli


def test_h2_on_salvages_courier_and_logs(fetch, monkeypatch, tmp_path):
    # ON = ratunek: kurier ZOSTAJE z parse_degraded; WARNING robi literówkę widoczną.
    monkeypatch.setattr(fetch, "FLAGS_PATH", _flags_file(tmp_path, True))
    logs = []
    monkeypatch.setattr(fetch, "log", lambda m: logs.append(m))
    sched = fetch.parse_schedule(_CSV, _TARGET)
    assert sched["Jan Kowalski"] == {"start": "10:00", "end": "18:00"}
    assert sched["Anna Nowak"] == {"start": "10:00", "end": None, "parse_degraded": True}
    # literówka WIDOCZNA w logu: nazwa kuriera + surowa komórka.
    warn = [m for m in logs if "degraded" in m]
    assert warn, "brak WARNING o degraded parse"
    assert "Anna Nowak" in warn[0] and "1O:00" in warn[0]


def test_h2_on_off_differ_on_same_input(fetch, monkeypatch, tmp_path):
    # ON≠OFF na TYM SAMYM wejściu = dowód że flaga zmienia decyzję (nie no-op).
    monkeypatch.setattr(fetch, "log", lambda m: None)
    monkeypatch.setattr(fetch, "FLAGS_PATH", _flags_file(tmp_path, False))
    off = fetch.parse_schedule(_CSV, _TARGET)["Anna Nowak"]
    monkeypatch.setattr(fetch, "FLAGS_PATH", _flags_file(tmp_path, True))
    on = fetch.parse_schedule(_CSV, _TARGET)["Anna Nowak"]
    assert off is None and on is not None and on.get("parse_degraded") is True


def test_h2_flag_missing_file_defaults_off(fetch, monkeypatch, tmp_path):
    # Brak pliku flag → default OFF (fail-safe: awaria odczytu ≠ zmiana zachowania).
    monkeypatch.setattr(fetch, "FLAGS_PATH", str(tmp_path / "does_not_exist.json"))
    sched = fetch.parse_schedule(_CSV, _TARGET)
    assert sched["Anna Nowak"] is None


def test_h2_flag_malformed_json_defaults_off(fetch, monkeypatch, tmp_path):
    bad = tmp_path / "flags.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(fetch, "FLAGS_PATH", str(bad))
    sched = fetch.parse_schedule(_CSV, _TARGET)
    assert sched["Anna Nowak"] is None


def test_h2_both_hours_broken_still_none_even_on(fetch, monkeypatch, tmp_path):
    # Guard: gdy OBIE komórki nieparsowalne (zero informacji) → None nawet z flagą ON.
    monkeypatch.setattr(fetch, "log", lambda m: None)
    monkeypatch.setattr(fetch, "FLAGS_PATH", _flags_file(tmp_path, True))
    csv_both = ",Imię,15-07-26,\n,Zły Wpis,XX,YY\n"
    sched = fetch.parse_schedule(csv_both, _TARGET)
    assert sched["Zły Wpis"] is None


# ══════════════════════════════════════════════════════════════════════════
# PARYTET — staged vs żywy różnią się WYŁĄCZNIE zamierzonymi hunkami
# (strażnik nieaktualizowanego mirrora). MUTATION #2 (C13) w raporcie: podmiana
# żywego pliku bez aktualizacji staged → nowa linia removed/added → ten test PADA.
# ══════════════════════════════════════════════════════════════════════════
def _unified_changes(live_path, staged_path):
    import difflib
    live = open(live_path, encoding="utf-8").read().splitlines()
    staged = open(staged_path, encoding="utf-8").read().splitlines()
    removed, added = [], []
    for ln in difflib.unified_diff(live, staged, lineterm=""):
        if ln.startswith(("+++", "---", "@@")):
            continue
        if ln.startswith("-"):
            removed.append(ln[1:])
        elif ln.startswith("+"):
            added.append(ln[1:])
    return removed, added


# JAWNA LISTA DOZWOLONYCH HUNKÓW.
_EXACT_REMOVED = {
    "fetch": {
        '        start_fmt = parse_hour(row[col_start].strip() if len(row) > col_start else "")',
        '        end_fmt   = parse_hour(row[col_end].strip()   if len(row) > col_end   else "")',
        '        schedule[name] = {"start": start_fmt, "end": end_fmt} if (start_fmt and end_fmt) else None',
        '    today = datetime.now().strftime("%d-%m-%y")',
    },
    "utils": {
        "try:",
        "    from zoneinfo import ZoneInfo",
        '    _TZ = ZoneInfo("Europe/Warsaw")',
        "    def _now_warsaw():",
        "        return datetime.now(_TZ).replace(tzinfo=None)",
        "except ImportError:",
        "    _TZ = timezone(timedelta(hours=2))",
        '                today = datetime.now().strftime("%d-%m-%y")',
    },
}
# Każdy anchor MUSI wystąpić w blokach added (fix nie zgubiony).
_REQUIRED_ANCHORS = {
    "fetch": [
        "STAGING COPY",
        "from zoneinfo import ZoneInfo",
        '_WARSAW = ZoneInfo("Europe/Warsaw")',
        "FLAGS_PATH =",
        "def _flag(name, default=False):",
        "def _today_warsaw():",
        'salvage_on = _flag("ENABLE_GRAFIK_ENTRY_SALVAGE", False)',
        '"parse_degraded": True',
        "today = _today_warsaw()",
    ],
    "utils": [
        "STAGING COPY",
        "from zoneinfo import ZoneInfo",
        '_TZ = ZoneInfo("Europe/Warsaw")',
        'today = _now_warsaw().strftime("%d-%m-%y")',
    ],
}
# Dokładne liczby zmian = tripwire na KAŻDĄ nieujętą linię (nawet generyczną).
_EXPECTED_COUNTS = {  # (removed_nonblank, added_total)
    "fetch": (4, 51),
    "utils": (10, 15),
}
_PATHS = {"fetch": (_LIVE_FETCH, _STAGED_FETCH), "utils": (_LIVE_UTILS, _STAGED_UTILS)}


@pytest.mark.parametrize("key", ["fetch", "utils"])
def test_parity_removed_lines_are_expected(key):
    live, staged = _PATHS[key]
    removed, _ = _unified_changes(live, staged)
    for r in removed:
        if r.strip():
            assert r in _EXACT_REMOVED[key], (
                f"[{key}] NIEOCZEKIWANA usunięta linia — żywy plik zmieniony a staged "
                f"NIEZAKTUALIZOWANY (stale mirror)? {r!r}"
            )


@pytest.mark.parametrize("key", ["fetch", "utils"])
def test_parity_required_fix_anchors_present(key):
    live, staged = _PATHS[key]
    _, added = _unified_changes(live, staged)
    joined = "\n".join(added)
    for a in _REQUIRED_ANCHORS[key]:
        assert a in joined, f"[{key}] brak oczekiwanej zmiany (fix zgubiony?): {a!r}"


@pytest.mark.parametrize("key", ["fetch", "utils"])
def test_parity_change_counts_exact(key):
    live, staged = _PATHS[key]
    removed, added = _unified_changes(live, staged)
    exp_rem, exp_add = _EXPECTED_COUNTS[key]
    got_rem = len([r for r in removed if r.strip()])
    assert got_rem == exp_rem, f"[{key}] usunięte linie {got_rem} != {exp_rem} (diff żywy↔staged się zmienił)"
    assert len(added) == exp_add, f"[{key}] dodane linie {len(added)} != {exp_add} (diff żywy↔staged się zmienił)"
