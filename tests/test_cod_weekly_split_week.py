"""F2.1d COD Weekly — testy split-week support (DIFF 3, 2026-05-04).

Custom test runner — NO pytest. Mocks dla fetch_sheet_grid, find_target,
_scrape_all, write_cod_column_skip_filled, validate_column_empty_ratio,
_try_alert. Each test wraps cmd_write z MockEnv context manager.

Run:
    /root/.openclaw/venvs/sheets/bin/python3 \\
        -m dispatch_v2.tests.test_cod_weekly_split_week
"""
import sys
from datetime import date
from unittest.mock import MagicMock

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2.cod_weekly import run_weekly as rw
from dispatch_v2.cod_weekly.sheet_writer import (
    NoTargetColumnError, AmbiguousTargetError,
)

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
# Test fixtures
# -------------------------------------------------------------------

WEEK_SINGLE_START = date(2026, 4, 13)
WEEK_SINGLE_END = date(2026, 4, 19)

WEEK_SPLIT_START = date(2026, 4, 27)
WEEK_SPLIT_END = date(2026, 5, 3)

FAKE_RESTAURANTS = [
    (3, "Arsenał Panteon"),
    (4, "Bar Eljot"),  # NO_MAPPING test
    (5, "Toriko"),
    (6, "Mama Thai Bistro, Mama Thai Street i Miejska Miska"),
]

FAKE_MAPPING = {
    "mapping": {
        "Arsenał Panteon": 14,
        "Toriko": 231,
        "Mama Thai Bistro, Mama Thai Street i Miejska Miska": [154, 230, 215],
    }
}

# CODs per (segment_start, restaurant) — deterministic
_CODS = {
    date(2026, 4, 13): {
        "Arsenał Panteon": 100.0, "Toriko": 50.0,
        "Mama Thai Bistro, Mama Thai Street i Miejska Miska": -20.0,
    },
    date(2026, 4, 27): {
        "Arsenał Panteon": 80.0, "Toriko": 40.0,
        "Mama Thai Bistro, Mama Thai Street i Miejska Miska": -10.0,
    },
    date(2026, 5, 1): {
        "Arsenał Panteon": 60.0, "Toriko": 30.0,
        "Mama Thai Bistro, Mama Thai Street i Miejska Miska": -5.0,
    },
}


def _make_target(col_letter, col_idx, seg_start, seg_end, payday):
    return {
        "col_idx": col_idx, "col_letter": col_letter,
        "segment_start": seg_start, "segment_end": seg_end, "payday": payday,
    }


def _make_grid(restaurants=None):
    return {
        "ws": MagicMock(),
        "row1": ["x"] * 70,
        "row2": ["x"] * 70,
        "restaurants": restaurants or FAKE_RESTAURANTS,
    }


def _empty_check_ok():
    return {
        "ok": True, "empty_count": 65, "total": 68,
        "ratio": 0.96, "filled_sample": [],
    }


def _empty_check_fail():
    return {
        "ok": False, "empty_count": 24, "total": 68,
        "ratio": 0.35,
        "filled_sample": [(15, "120.50"), (28, "-44.20")],
    }


def _scrape_for(targets):
    """Generuj fake scrape results dla listy targets (1 lub więcej, ale
    cmd_write woła per-segment z 1-elementową listą)."""
    results = []
    errors = []
    for row_idx, name in FAKE_RESTAURANTS:
        if name == "Bar Eljot":
            errors.append(f"NO_MAPPING {name!r}")
            results.append({"row": row_idx, "rest": name, "error": "no_mapping"})
            continue
        per_seg = []
        for t in targets:
            cod_map = _CODS.get(t["segment_start"], {})
            per_seg.append(cod_map.get(name, 0.0))
        results.append({
            "row": row_idx, "rest": name,
            "cod_per_segment": per_seg, "had_error": False,
        })
    return results, errors


def _write_result_ok(n_written=2, n_skipped=0):
    return {
        "dry_run": False,
        "written_rows": [(f"X{i}", float(i)) for i in range(n_written)],
        "skipped_filled": [
            {"row": 100 + i, "existing": str(i * 10.0)}
            for i in range(n_skipped)
        ],
        "skipped_errors": [],
    }


# -------------------------------------------------------------------
# Test infrastructure — mock context manager
# -------------------------------------------------------------------

class MockEnv:
    """Patch run_weekly module-level functions; capture telegram + writes."""
    def __init__(self):
        self.telegram_messages = []
        self.write_calls = []   # list of (col_letter, row_to_value)
        self.scrape_calls = []  # list of (restaurants, targets)
        self._saved = {}

    def __enter__(self):
        self._save_and_patch("fetch_sheet_grid", lambda: _make_grid())
        self._save_and_patch("load_mapping", lambda: FAKE_MAPPING)
        self._save_and_patch("_try_alert", self._capture_telegram)
        return self

    def __exit__(self, *args):
        for name, original in self._saved.items():
            setattr(rw, name, original)

    def _save_and_patch(self, name, replacement):
        if name not in self._saved:
            self._saved[name] = getattr(rw, name)
        setattr(rw, name, replacement)

    def _capture_telegram(self, text):
        self.telegram_messages.append(text)
        return True

    def patch_find_target(self, targets_or_exception):
        if isinstance(targets_or_exception, Exception):
            exc = targets_or_exception

            def raiser(*a, **kw):
                raise exc
            self._save_and_patch("find_target_cod_columns", raiser)
        else:
            value = targets_or_exception
            self._save_and_patch(
                "find_target_cod_columns",
                lambda *a, **kw: value,
            )

    def patch_empty_check(self, *side_effects):
        it = iter(side_effects)

        def fake(*a, **kw):
            v = next(it)
            if isinstance(v, Exception):
                raise v
            return v
        self._save_and_patch("validate_column_empty_ratio", fake)

    def patch_scrape(self, side_effects=None, exception=None):
        """side_effects: list of (results, errors) tuples per call.
        exception: gdy ustawione, wszystkie calls raise."""
        if exception is not None:
            exc = exception

            def raiser(*a, **kw):
                self.scrape_calls.append(a)
                raise exc
            self._save_and_patch("_scrape_all", raiser)
            return
        it = iter(side_effects or [])

        def fake(restaurants, mapping, targets, opener=None):
            self.scrape_calls.append((list(restaurants), list(targets)))
            try:
                return next(it)
            except StopIteration:
                return _scrape_for(targets)
        self._save_and_patch("_scrape_all", fake)

    def patch_write(self, *side_effects):
        it = iter(side_effects)

        def fake(ws, col_letter, row_to_value, dry_run=False):
            self.write_calls.append((col_letter, dict(row_to_value)))
            try:
                v = next(it)
            except StopIteration:
                v = _write_result_ok(n_written=len(row_to_value))
            if isinstance(v, Exception):
                raise v
            return v
        self._save_and_patch("write_cod_column_skip_filled", fake)


# -------------------------------------------------------------------
# T1: Happy path single-month (REGRESSION)
# -------------------------------------------------------------------
def test_t1_single_month_happy():
    _hdr("T1: Happy path single-month (regresja)")
    target = _make_target("BK", 62, WEEK_SINGLE_START, WEEK_SINGLE_END, date(2026, 4, 22))
    with MockEnv() as env:
        env.patch_find_target([target])
        env.patch_empty_check(_empty_check_ok())
        env.patch_scrape([_scrape_for([target])])
        env.patch_write()
        rc = rw.cmd_write(WEEK_SINGLE_START, WEEK_SINGLE_END)

    if rc == 0:
        _ok("exit 0")
    else:
        _fail("exit 0", f"got {rc}")

    if len(env.write_calls) == 1 and env.write_calls[0][0] == "BK":
        _ok("1 write call do BK")
    else:
        _fail("1 write call do BK", env.write_calls)

    if env.telegram_messages:
        msg = env.telegram_messages[0]
        if "split-month" not in msg:
            _ok("Brak 'split-month' w telegramie (single)")
        else:
            _fail("Brak 'split-month' w telegramie", msg[:120])
        if "Wpisano dla tygodnia 13-19.04.2026" in msg:
            _ok("Telegram header poprawny single")
        else:
            _fail("Telegram header poprawny single", msg.split("\n")[0])
        if "Kolumna: BK" in msg:
            _ok("Telegram pokazuje 'Kolumna: BK'")
        else:
            _fail("Telegram 'Kolumna: BK'", msg[:200])
    else:
        _fail("1 telegram message", "no telegram captured")


# -------------------------------------------------------------------
# T2: Happy path split-month (oba segmenty OK)
# -------------------------------------------------------------------
def test_t2_split_month_happy():
    _hdr("T2: Happy path split-month (oba segmenty OK)")
    seg1 = _make_target("BR", 69, date(2026, 4, 27), date(2026, 4, 30), date(2026, 5, 6))
    seg2 = _make_target("BV", 73, date(2026, 5, 1), date(2026, 5, 3), date(2026, 5, 6))
    with MockEnv() as env:
        env.patch_find_target([seg1, seg2])
        env.patch_empty_check(_empty_check_ok(), _empty_check_ok())
        env.patch_scrape([_scrape_for([seg1]), _scrape_for([seg2])])
        env.patch_write()
        rc = rw.cmd_write(WEEK_SPLIT_START, WEEK_SPLIT_END)

    if rc == 0:
        _ok("exit 0")
    else:
        _fail("exit 0", f"got {rc}")

    cols = [c for c, _ in env.write_calls]
    if cols == ["BR", "BV"]:
        _ok("2 write calls (BR, BV) w kolejności")
    else:
        _fail("2 write calls (BR, BV)", cols)

    msg = env.telegram_messages[0] if env.telegram_messages else ""
    if "split-month (2 segmenty)" in msg:
        _ok("Telegram header pokazuje split-month")
    else:
        _fail("split-month w nagłówku", msg.split("\n")[:2])
    if "Segment 1/2" in msg and "Segment 2/2" in msg:
        _ok("Per-segment headers obecne")
    else:
        _fail("Per-segment headers obecne", msg[:300])
    if "Tydzień łącznie" in msg:
        _ok("Aggregate section obecna")
    else:
        _fail("Aggregate section obecna", msg)


# -------------------------------------------------------------------
# T3: NoTargetColumnError → exit 1
# -------------------------------------------------------------------
def test_t3_no_target_column():
    _hdr("T3: NoTargetColumnError → exit 1")
    with MockEnv() as env:
        env.patch_find_target(NoTargetColumnError("Brak bloku z payday=06-05-2026"))
        rc = rw.cmd_write(WEEK_SPLIT_START, WEEK_SPLIT_END)

    if rc == 1:
        _ok("exit 1 (NoTargetColumnError)")
    else:
        _fail("exit 1", f"got {rc}")
    if env.telegram_messages and "Target column fail" in env.telegram_messages[0]:
        _ok("Alert wysłany")
    else:
        _fail("Alert wysłany", env.telegram_messages)


# -------------------------------------------------------------------
# T4: Partial fail (segment 2 empty_check fail)
# -------------------------------------------------------------------
def test_t4_partial_empty_check_fail():
    _hdr("T4: Segment 1 OK, segment 2 empty_check FAIL → exit 0 + PARTIAL")
    seg1 = _make_target("BR", 69, date(2026, 4, 27), date(2026, 4, 30), date(2026, 5, 6))
    seg2 = _make_target("BV", 73, date(2026, 5, 1), date(2026, 5, 3), date(2026, 5, 6))
    with MockEnv() as env:
        env.patch_find_target([seg1, seg2])
        env.patch_empty_check(_empty_check_ok(), _empty_check_fail())
        env.patch_scrape([_scrape_for([seg1])])  # tylko seg1 dochodzi do scrape
        env.patch_write()
        rc = rw.cmd_write(WEEK_SPLIT_START, WEEK_SPLIT_END)

    if rc == 0:
        _ok("exit 0 (partial)")
    else:
        _fail("exit 0", f"got {rc}")

    if len(env.write_calls) == 1 and env.write_calls[0][0] == "BR":
        _ok("Tylko 1 write call (BR) — seg 2 NIE zapisany")
    else:
        _fail("1 write call (BR)", env.write_calls)

    msg = env.telegram_messages[0] if env.telegram_messages else ""
    if "PARTIAL" in msg:
        _ok("Telegram zawiera PARTIAL")
    else:
        _fail("Telegram PARTIAL", msg.split("\n")[0])
    if "❌ FAILED" in msg and "empty_check_fail" in msg:
        _ok("Failed segment ma reason=empty_check_fail")
    else:
        _fail("Failed segment reason", msg)


# -------------------------------------------------------------------
# T5: Scrape errors w segment 1, segment 2 OK
# -------------------------------------------------------------------
def test_t5_scrape_errors_segment1():
    _hdr("T5: Scrape errors w seg 1 (cz. ok), seg 2 OK")
    seg1 = _make_target("BR", 69, date(2026, 4, 27), date(2026, 4, 30), date(2026, 5, 6))
    seg2 = _make_target("BV", 73, date(2026, 5, 1), date(2026, 5, 3), date(2026, 5, 6))

    def custom_seg1():
        results, errors = _scrape_for([seg1])
        errors.append("SCRAPE_ERROR Toriko BR: parse fail company=231")
        return results, errors

    with MockEnv() as env:
        env.patch_find_target([seg1, seg2])
        env.patch_empty_check(_empty_check_ok(), _empty_check_ok())
        env.patch_scrape([custom_seg1(), _scrape_for([seg2])])
        env.patch_write()
        rc = rw.cmd_write(WEEK_SPLIT_START, WEEK_SPLIT_END)

    if rc == 0:
        _ok("exit 0 (oba segmenty zapisane mimo errora)")
    else:
        _fail("exit 0", f"got {rc}")

    if len(env.write_calls) == 2:
        _ok("2 write calls")
    else:
        _fail("2 write calls", len(env.write_calls))

    msg = env.telegram_messages[0] if env.telegram_messages else ""
    # Sprawdzamy że error jest w sekcji per-segment "Błędy: 2" (NO_MAPPING + scrape_error)
    if "Błędy: 2" in msg:
        _ok("Telegram pokazuje Błędy: 2 dla seg 1")
    else:
        _fail("Telegram Błędy: 2 dla seg 1", msg)
    if "SCRAPE_ERROR Toriko" in msg or "scrape_error" in msg.lower() or "ERRORS" in msg:
        _ok("Telegram zawiera referencję do scrape error")
    else:
        _fail("Telegram zawiera scrape error ref", msg)


# -------------------------------------------------------------------
# T6: Write exception w segment 2 (segment 1 zostaje persisted)
# -------------------------------------------------------------------
def test_t6_write_exception_segment2():
    _hdr("T6: Write exception w segment 2 → seg 1 persists, exit 0 PARTIAL")
    seg1 = _make_target("BR", 69, date(2026, 4, 27), date(2026, 4, 30), date(2026, 5, 6))
    seg2 = _make_target("BV", 73, date(2026, 5, 1), date(2026, 5, 3), date(2026, 5, 6))
    with MockEnv() as env:
        env.patch_find_target([seg1, seg2])
        env.patch_empty_check(_empty_check_ok(), _empty_check_ok())
        env.patch_scrape([_scrape_for([seg1]), _scrape_for([seg2])])
        env.patch_write(
            _write_result_ok(n_written=3),  # seg1 OK
            RuntimeError("API rate limit exceeded"),  # seg2 fails
        )
        rc = rw.cmd_write(WEEK_SPLIT_START, WEEK_SPLIT_END)

    if rc == 0:
        _ok("exit 0 (partial — segment 1 zapisany)")
    else:
        _fail("exit 0", f"got {rc}")

    if len(env.write_calls) == 2:
        _ok("Oba write calls wywołane (seg2 raise w środku)")
    else:
        _fail("Oba write calls wywołane", len(env.write_calls))

    msg = env.telegram_messages[0] if env.telegram_messages else ""
    if "PARTIAL" in msg and "write_exception" in msg:
        _ok("Telegram pokazuje partial + write_exception")
    else:
        _fail("Telegram partial + write_exception", msg)


# -------------------------------------------------------------------
# T7: Malformed range (ValueError) → exit 1
# -------------------------------------------------------------------
def test_t7_malformed_range():
    _hdr("T7: ValueError (malformed range) → exit 1")
    with MockEnv() as env:
        env.patch_find_target(ValueError("Nie mogę sparsować zakresu w pos 4 kol BS: '???'"))
        rc = rw.cmd_write(WEEK_SPLIT_START, WEEK_SPLIT_END)
    if rc == 1:
        _ok("exit 1 (ValueError)")
    else:
        _fail("exit 1", f"got {rc}")


# -------------------------------------------------------------------
# T8: AmbiguousTargetError → exit 1
# -------------------------------------------------------------------
def test_t8_ambiguous_target():
    _hdr("T8: AmbiguousTargetError → exit 1")
    with MockEnv() as env:
        env.patch_find_target(AmbiguousTargetError("2 bloki dla miesiąca 5: BO i BS"))
        rc = rw.cmd_write(WEEK_SPLIT_START, WEEK_SPLIT_END)
    if rc == 1:
        _ok("exit 1 (AmbiguousTargetError)")
    else:
        _fail("exit 1", f"got {rc}")


# -------------------------------------------------------------------
# T9: Cross-year split (29.12.2025-04.01.2026)
# -------------------------------------------------------------------
def test_t9_cross_year_split():
    _hdr("T9: Cross-year split (29.12-04.01)")
    seg1 = _make_target("XX", 0, date(2025, 12, 29), date(2025, 12, 31), date(2026, 1, 7))
    seg2 = _make_target("YY", 4, date(2026, 1, 1), date(2026, 1, 4), date(2026, 1, 7))
    with MockEnv() as env:
        env.patch_find_target([seg1, seg2])
        env.patch_empty_check(_empty_check_ok(), _empty_check_ok())
        env.patch_scrape([_scrape_for([seg1]), _scrape_for([seg2])])
        env.patch_write()
        rc = rw.cmd_write(date(2025, 12, 29), date(2026, 1, 4))

    if rc == 0:
        _ok("exit 0 (cross-year split processed)")
    else:
        _fail("exit 0 cross-year", f"got {rc}")
    if len(env.write_calls) == 2:
        _ok("2 write calls cross-year")
    else:
        _fail("2 write calls cross-year", len(env.write_calls))


# -------------------------------------------------------------------
# T10: Idempotency split (skipped_filled niepusty)
# -------------------------------------------------------------------
def test_t10_idempotency_split():
    _hdr("T10: Idempotency split (skipped_filled niepusty)")
    seg1 = _make_target("BR", 69, date(2026, 4, 27), date(2026, 4, 30), date(2026, 5, 6))
    seg2 = _make_target("BV", 73, date(2026, 5, 1), date(2026, 5, 3), date(2026, 5, 6))
    with MockEnv() as env:
        env.patch_find_target([seg1, seg2])
        env.patch_empty_check(_empty_check_ok(), _empty_check_ok())
        env.patch_scrape([_scrape_for([seg1]), _scrape_for([seg2])])
        env.patch_write(
            _write_result_ok(n_written=1, n_skipped=2),
            _write_result_ok(n_written=2, n_skipped=1),
        )
        rc = rw.cmd_write(WEEK_SPLIT_START, WEEK_SPLIT_END)

    if rc == 0:
        _ok("exit 0 (idempotency)")
    else:
        _fail("exit 0", f"got {rc}")

    msg = env.telegram_messages[0] if env.telegram_messages else ""
    if "Skip: 2" in msg and "Skip: 1" in msg:
        _ok("Per-segment skip counts (2, 1) w telegramie")
    else:
        _fail("Per-segment skip counts (2, 1)", msg)


# -------------------------------------------------------------------
# T11: Telegram aggregation per restaurant cross-segment
# -------------------------------------------------------------------
def test_t11_telegram_aggregation():
    _hdr("T11: Telegram aggregation per-restaurant cross-segment")
    seg1 = _make_target("BR", 69, date(2026, 4, 27), date(2026, 4, 30), date(2026, 5, 6))
    seg2 = _make_target("BV", 73, date(2026, 5, 1), date(2026, 5, 3), date(2026, 5, 6))
    with MockEnv() as env:
        env.patch_find_target([seg1, seg2])
        env.patch_empty_check(_empty_check_ok(), _empty_check_ok())
        env.patch_scrape([_scrape_for([seg1]), _scrape_for([seg2])])
        env.patch_write()
        rc = rw.cmd_write(WEEK_SPLIT_START, WEEK_SPLIT_END)

    msg = env.telegram_messages[0] if env.telegram_messages else ""
    # Arsenał Panteon: 80 (seg1) + 60 (seg2) = 140
    if "Arsenał Panteon: +140.00" in msg:
        _ok("Aggregated TOP 5 plus: Arsenał +140 (sum 80+60)")
    else:
        _fail("Aggregated Arsenał +140", msg)
    # Mama Thai: -10 + -5 = -15
    if "Mama Thai" in msg and "-15.00" in msg:
        _ok("Aggregated Mama Thai -15 (sum -10+-5) w TOP 5 minus")
    else:
        _fail("Aggregated Mama Thai -15", msg)
    if "(oba segmenty zsumowane)" in msg:
        _ok("Suffix 'oba segmenty zsumowane'")
    else:
        _fail("Suffix obecny", msg)
    if rc == 0:
        _ok("exit 0")
    else:
        _fail("exit 0", f"got {rc}")


# -------------------------------------------------------------------
# T12: Total fail — all segments failed
# -------------------------------------------------------------------
def test_t12_total_fail():
    _hdr("T12: All segments fail → exit 1")
    seg1 = _make_target("BR", 69, date(2026, 4, 27), date(2026, 4, 30), date(2026, 5, 6))
    seg2 = _make_target("BV", 73, date(2026, 5, 1), date(2026, 5, 3), date(2026, 5, 6))
    with MockEnv() as env:
        env.patch_find_target([seg1, seg2])
        env.patch_empty_check(_empty_check_fail(), _empty_check_fail())
        env.patch_scrape([])  # nigdy nie zostanie wywołane (empty check raczej)
        env.patch_write()
        rc = rw.cmd_write(WEEK_SPLIT_START, WEEK_SPLIT_END)

    if rc == 1:
        _ok("exit 1 (all failed)")
    else:
        _fail("exit 1", f"got {rc}")
    if len(env.write_calls) == 0:
        _ok("Zero write calls")
    else:
        _fail("Zero write calls", len(env.write_calls))
    msg = env.telegram_messages[0] if env.telegram_messages else ""
    if "❌ FAILED" in msg:
        _ok("Telegram FAILED header")
    else:
        _fail("Telegram FAILED header", msg)


# -------------------------------------------------------------------
# Runner
# -------------------------------------------------------------------
def main():
    test_t1_single_month_happy()
    test_t2_split_month_happy()
    test_t3_no_target_column()
    test_t4_partial_empty_check_fail()
    test_t5_scrape_errors_segment1()
    test_t6_write_exception_segment2()
    test_t7_malformed_range()
    test_t8_ambiguous_target()
    test_t9_cross_year_split()
    test_t10_idempotency_split()
    test_t11_telegram_aggregation()
    test_t12_total_fail()

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
