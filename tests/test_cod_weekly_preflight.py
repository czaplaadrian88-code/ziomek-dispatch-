"""F2.1d COD Weekly — testy preflight check (E5, 2026-05-04).

Scenariusze WC1-WC3 (week_calculator) + P1-P6 (cmd_preflight).
Custom test runner — NO pytest.

Run:
    /root/.openclaw/venvs/sheets/bin/python3 \\
        -m dispatch_v2.tests.test_cod_weekly_preflight
"""
import sys
from datetime import date, datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2.cod_weekly import run_weekly as rw
from dispatch_v2.cod_weekly.week_calculator import (
    get_current_week_ending_sunday,
)
from dispatch_v2.cod_weekly.sheet_writer import (
    NoTargetColumnError, AmbiguousTargetError,
)

WARSAW = ZoneInfo("Europe/Warsaw")

_passed = 0
_failed = 0
_failures = []


def _ok(name):
    global _passed
    _passed += 1
    print(f"  [OK] {name}")


def _fail(name, detail=""):
    global _failed
    _failed += 1
    detail_short = str(detail)[:200]
    _failures.append(f"{name}: {detail_short}")
    print(f"  [FAIL] {name}: {detail_short}")


def _hdr(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# -------------------------------------------------------------------
# WC1-WC3: get_current_week_ending_sunday
# -------------------------------------------------------------------
def test_wc1_sunday():
    _hdr("WC1: niedziela 23:00 → tydzień kończący się dziś")
    sun = datetime(2026, 5, 3, 23, 0, tzinfo=WARSAW)
    s, e = get_current_week_ending_sunday(sun)
    if (s, e) == (date(2026, 4, 27), date(2026, 5, 3)):
        _ok("Sun 03.05 23:00 → (27.04, 03.05)")
    else:
        _fail("Sun 03.05 → (27.04, 03.05)", f"got {s}..{e}")


def test_wc2_monday():
    _hdr("WC2: poniedziałek 08:00 → tydzień rozpoczynający się dziś")
    mon = datetime(2026, 5, 4, 8, 0, tzinfo=WARSAW)
    s, e = get_current_week_ending_sunday(mon)
    if (s, e) == (date(2026, 5, 4), date(2026, 5, 10)):
        _ok("Mon 04.05 08:00 → (04.05, 10.05)")
    else:
        _fail("Mon 04.05 → (04.05, 10.05)", f"got {s}..{e}")


def test_wc3_midweek():
    _hdr("WC3: środek tygodnia (sobota) → ten sam tydzień bieżący")
    sat = datetime(2026, 5, 9, 12, 0, tzinfo=WARSAW)
    s, e = get_current_week_ending_sunday(sat)
    if (s, e) == (date(2026, 5, 4), date(2026, 5, 10)):
        _ok("Sat 09.05 12:00 → (04.05, 10.05)")
    else:
        _fail("Sat 09.05 → (04.05, 10.05)", f"got {s}..{e}")


# -------------------------------------------------------------------
# Test fixtures dla preflight
# -------------------------------------------------------------------

WEEK_SINGLE_START = date(2026, 5, 4)
WEEK_SINGLE_END = date(2026, 5, 10)

WEEK_SPLIT_START = date(2026, 4, 27)
WEEK_SPLIT_END = date(2026, 5, 3)


def _make_target(col_letter, col_idx, seg_start, seg_end, payday):
    return {
        "col_idx": col_idx, "col_letter": col_letter,
        "segment_start": seg_start, "segment_end": seg_end, "payday": payday,
    }


def _make_grid():
    return {
        "ws": MagicMock(),
        "row1": ["x"] * 70,
        "row2": ["x"] * 70,
        "restaurants": [(3, "X"), (4, "Y")],
    }


class MockEnv:
    def __init__(self):
        self.telegram_messages = []
        self._saved = {}

    def __enter__(self):
        self._save("fetch_sheet_grid", lambda: _make_grid())
        self._save("_try_alert", self._capture)
        return self

    def __exit__(self, *args):
        for name, original in self._saved.items():
            setattr(rw, name, original)

    def _save(self, name, replacement):
        if name not in self._saved:
            self._saved[name] = getattr(rw, name)
        setattr(rw, name, replacement)

    def _capture(self, text):
        self.telegram_messages.append(text)
        return True

    def patch_find_target(self, value_or_exception):
        if isinstance(value_or_exception, Exception):
            exc = value_or_exception

            def raiser(*a, **kw):
                raise exc
            self._save("find_target_cod_columns", raiser)
        else:
            value = value_or_exception
            self._save("find_target_cod_columns", lambda *a, **kw: value)

    def break_fetch(self, exception):
        exc = exception

        def raiser():
            raise exc
        self._save("fetch_sheet_grid", raiser)


# -------------------------------------------------------------------
# P1: Arkusz OK single-month → exit 0, no telegram
# -------------------------------------------------------------------
def test_p1_single_ok():
    _hdr("P1: Arkusz OK single-month → exit 0, no telegram spam")
    target = _make_target("BS", 70, WEEK_SINGLE_START, WEEK_SINGLE_END, date(2026, 5, 13))
    with MockEnv() as env:
        env.patch_find_target([target])
        rc = rw.cmd_preflight(WEEK_SINGLE_START, WEEK_SINGLE_END)
    if rc == 0:
        _ok("exit 0")
    else:
        _fail("exit 0", f"got {rc}")
    if not env.telegram_messages:
        _ok("Brak telegramu (no spam)")
    else:
        _fail("Brak telegramu", env.telegram_messages)


# -------------------------------------------------------------------
# P2: Arkusz OK split-month → exit 0, no telegram
# -------------------------------------------------------------------
def test_p2_split_ok():
    _hdr("P2: Arkusz OK split-month (oba bloki) → exit 0, no telegram")
    seg1 = _make_target("BR", 69, date(2026, 4, 27), date(2026, 4, 30), date(2026, 5, 6))
    seg2 = _make_target("BV", 73, date(2026, 5, 1), date(2026, 5, 3), date(2026, 5, 6))
    with MockEnv() as env:
        env.patch_find_target([seg1, seg2])
        rc = rw.cmd_preflight(WEEK_SPLIT_START, WEEK_SPLIT_END)
    if rc == 0:
        _ok("exit 0 split OK")
    else:
        _fail("exit 0 split OK", f"got {rc}")
    if not env.telegram_messages:
        _ok("Brak telegramu split OK")
    else:
        _fail("Brak telegramu split OK", env.telegram_messages)


# -------------------------------------------------------------------
# P3: NoTargetColumnError single-month → exit 1, instrukcja
# -------------------------------------------------------------------
def test_p3_no_target_single():
    _hdr("P3: Brak kolumny single-month → exit 1, instrukcja Rafałowi")
    with MockEnv() as env:
        env.patch_find_target(NoTargetColumnError("Brak bloku z payday=13-05-2026"))
        rc = rw.cmd_preflight(WEEK_SINGLE_START, WEEK_SINGLE_END)
    if rc == 1:
        _ok("exit 1")
    else:
        _fail("exit 1", f"got {rc}")
    msg = env.telegram_messages[0] if env.telegram_messages else ""
    if "Brak kolumny" in msg and "tydzień 04-10.05.2026" in msg:
        _ok("Telegram header z numerem tygodnia")
    else:
        _fail("Telegram header", msg.split("\n")[0])
    if "13-05-2026" in msg:
        _ok("Instrukcja zawiera payday 13-05-2026")
    else:
        _fail("Instrukcja zawiera payday", msg)
    if "04-10.05.2026" in msg:
        _ok("Instrukcja zawiera zakres '04-10.05.2026'")
    else:
        _fail("Instrukcja zawiera zakres", msg)
    if "weryfikacja" in msg.lower():
        _ok("Instrukcja zawiera krok weryfikacji")
    else:
        _fail("Instrukcja krok weryfikacji", msg)


# -------------------------------------------------------------------
# P4: AmbiguousTargetError split-month → exit 1, instrukcja split
# -------------------------------------------------------------------
def test_p4_ambiguous_split():
    _hdr("P4: AmbiguousTargetError split-month → exit 1, instrukcja split")
    with MockEnv() as env:
        env.patch_find_target(AmbiguousTargetError(
            "Oczekiwano 2 kandydatów dla rozbitego tygodnia, znaleziono 1"
        ))
        rc = rw.cmd_preflight(WEEK_SPLIT_START, WEEK_SPLIT_END)
    if rc == 1:
        _ok("exit 1")
    else:
        _fail("exit 1", f"got {rc}")
    msg = env.telegram_messages[0] if env.telegram_messages else ""
    if "Tydzień krosuje miesiąc" in msg:
        _ok("Instrukcja split-month detected")
    else:
        _fail("Instrukcja split-month", msg)
    if "DWA bloki" in msg:
        _ok("Instrukcja mówi o DWÓCH blokach")
    else:
        _fail("Instrukcja DWA bloki", msg)
    if "Blok 1/2" in msg and "Blok 2/2" in msg:
        _ok("Per-segment instrukcja (Blok 1/2 + 2/2)")
    else:
        _fail("Per-segment instrukcja", msg)
    if "27-30.04.2026" in msg and "01-03.05.2026" in msg:
        _ok("Oba zakresy podane (27-30.04 + 01-03.05)")
    else:
        _fail("Oba zakresy podane", msg)
    if "06-05-2026" in msg:
        _ok("Wspólny payday 06-05-2026 podany")
    else:
        _fail("Wspólny payday 06-05-2026", msg)


# -------------------------------------------------------------------
# P5: ValueError malformed range → exit 1
# -------------------------------------------------------------------
def test_p5_malformed():
    _hdr("P5: ValueError malformed range → exit 1")
    with MockEnv() as env:
        env.patch_find_target(ValueError("Nie mogę sparsować zakresu w pos 4 kol BS: '???'"))
        rc = rw.cmd_preflight(WEEK_SPLIT_START, WEEK_SPLIT_END)
    if rc == 1:
        _ok("exit 1")
    else:
        _fail("exit 1", f"got {rc}")
    if env.telegram_messages and "ValueError" in env.telegram_messages[0]:
        _ok("Telegram zawiera ValueError")
    else:
        _fail("Telegram ValueError", env.telegram_messages)


# -------------------------------------------------------------------
# P6: fetch_sheet_grid raises → exit 1
# -------------------------------------------------------------------
def test_p6_sheet_unavailable():
    _hdr("P6: fetch_sheet_grid raise → exit 1, telegram 'nie mogę otworzyć'")
    with MockEnv() as env:
        env.break_fetch(RuntimeError("API quota exceeded"))
        rc = rw.cmd_preflight(WEEK_SPLIT_START, WEEK_SPLIT_END)
    if rc == 1:
        _ok("exit 1")
    else:
        _fail("exit 1", f"got {rc}")
    msg = env.telegram_messages[0] if env.telegram_messages else ""
    if "Nie mogę otworzyć arkusza" in msg:
        _ok("Telegram 'Nie mogę otworzyć arkusza'")
    else:
        _fail("Telegram 'Nie mogę otworzyć'", msg)
    if "API quota exceeded" in msg:
        _ok("Telegram zawiera oryginalny błąd")
    else:
        _fail("Telegram zawiera oryginalny błąd", msg)


def main():
    test_wc1_sunday()
    test_wc2_monday()
    test_wc3_midweek()
    test_p1_single_ok()
    test_p2_split_ok()
    test_p3_no_target_single()
    test_p4_ambiguous_split()
    test_p5_malformed()
    test_p6_sheet_unavailable()
    print(f"\n{'=' * 70}")
    print(f"PASSED: {_passed}, FAILED: {_failed}")
    if _failures:
        print("\nFailures:")
        for f in _failures:
            print(f"  - {f}")
    print('=' * 70)
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
