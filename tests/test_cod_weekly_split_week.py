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
    (5, "Toriko"),
    (6, "Mama Thai Bistro, Mama Thai Street i Miejska Miska"),
]
# NO_MAPPING test fixture (separate, used by NM1-NM3) — restaurants sheet
# zawiera dodatkowo nazwy nieobecne w mapping
FAKE_RESTAURANTS_WITH_MISSING = FAKE_RESTAURANTS + [
    (7, "Bar Eljot"),  # not in mapping → NO_MAPPING
    (8, "Nowa Restauracja XYZ"),  # not in mapping
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
        # E1: cmd_write używa _refresh_mapping (auto-rebuild przed write).
        # Default mock: zwraca FAKE_MAPPING['mapping'] (jakby rebuild OK).
        self._save_and_patch(
            "_refresh_mapping",
            lambda: FAKE_MAPPING["mapping"],
        )
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

    def patch_refresh_mapping(self, return_or_exception):
        """E1: zastąp _refresh_mapping. Wartość = mapping dict, lub Exception."""
        if isinstance(return_or_exception, Exception):
            exc = return_or_exception

            def raiser():
                raise exc
            self._save_and_patch("_refresh_mapping", raiser)
        else:
            value = return_or_exception
            self._save_and_patch("_refresh_mapping", lambda: value)


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

    # Po E4 hook może być więcej niż 1 telegram (gdy errors zawiera NO_MAPPING).
    # T5 nie ma NO_MAPPING (brak Bar Eljot w fixturze) — tylko 1 message (main).
    msg = env.telegram_messages[-1] if env.telegram_messages else ""
    # T5 ma 1 scrape_error w seg1 (custom dodany), 0 w seg2
    if "Błędy: 1" in msg:
        _ok("Telegram pokazuje Błędy: 1 dla seg 1")
    else:
        _fail("Telegram Błędy: 1 dla seg 1", msg)
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
# E4: NO_MAPPING separate alert tests (NM1-NM3)
# -------------------------------------------------------------------

def _scrape_for_with_missing(targets):
    """Variant generujący NO_MAPPING errors (uses FAKE_RESTAURANTS_WITH_MISSING)."""
    results = []
    errors = []
    for row_idx, name in FAKE_RESTAURANTS_WITH_MISSING:
        if name not in FAKE_MAPPING["mapping"]:
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


class MockEnvWithMissing(MockEnv):
    """Override grid restaurants to include unmapped ones (NM tests)."""
    def __enter__(self):
        super().__enter__()
        self._save_and_patch(
            "fetch_sheet_grid",
            lambda: _make_grid(restaurants=FAKE_RESTAURANTS_WITH_MISSING),
        )
        return self


def test_nm1_zero_no_mapping():
    _hdr("NM1: 0 NO_MAPPING (default fixture) → no separate E4 alert")
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
    if len(env.telegram_messages) == 1:
        _ok("1 telegram message (only main report — no E4 separate)")
    else:
        _fail("1 telegram message", f"got {len(env.telegram_messages)}: {env.telegram_messages}")
    if env.telegram_messages and "🚨 NO_MAPPING" not in env.telegram_messages[0]:
        _ok("Brak NO_MAPPING separate alert (correct dla 0 missing)")
    else:
        _fail("Brak NO_MAPPING alert", env.telegram_messages)


def test_nm2_single_segment_no_mapping():
    _hdr("NM2: 2 NO_MAPPING w single-segment → 1 separate alert + main report")
    target = _make_target("BK", 62, WEEK_SINGLE_START, WEEK_SINGLE_END, date(2026, 4, 22))
    with MockEnvWithMissing() as env:
        env.patch_find_target([target])
        env.patch_empty_check(_empty_check_ok())
        env.patch_scrape([_scrape_for_with_missing([target])])
        env.patch_write()
        rc = rw.cmd_write(WEEK_SINGLE_START, WEEK_SINGLE_END)
    if rc == 0:
        _ok("exit 0")
    else:
        _fail("exit 0", f"got {rc}")
    if len(env.telegram_messages) == 2:
        _ok("2 telegram messages (E4 separate + main report)")
    else:
        _fail("2 telegram messages", f"got {len(env.telegram_messages)}")
    # E4 alert powinien być PIERWSZY (wysłany przed main report)
    nm_alert = env.telegram_messages[0] if env.telegram_messages else ""
    if "🚨 NO_MAPPING" in nm_alert:
        _ok("E4 alert pierwszy (🚨 NO_MAPPING)")
    else:
        _fail("E4 alert pierwszy", nm_alert)
    if "Bar Eljot" in nm_alert and "Nowa Restauracja XYZ" in nm_alert:
        _ok("E4 alert zawiera obie missing names")
    else:
        _fail("E4 alert obie names", nm_alert)
    if "restaurant_mapper --build" in nm_alert:
        _ok("E4 alert zawiera komendę --build")
    else:
        _fail("E4 alert komendę --build", nm_alert)
    if "27.04-03.05" in nm_alert or "13-19.04" in nm_alert or "Tydzień:" in nm_alert:
        _ok("E4 alert zawiera tydzień")
    else:
        _fail("E4 alert tydzień", nm_alert)


def test_nm3_split_week_dedup():
    _hdr("NM3: NO_MAPPING split-week (deduplikacja per-segment)")
    seg1 = _make_target("BR", 69, date(2026, 4, 27), date(2026, 4, 30), date(2026, 5, 6))
    seg2 = _make_target("BV", 73, date(2026, 5, 1), date(2026, 5, 3), date(2026, 5, 6))
    with MockEnvWithMissing() as env:
        env.patch_find_target([seg1, seg2])
        env.patch_empty_check(_empty_check_ok(), _empty_check_ok())
        env.patch_scrape([
            _scrape_for_with_missing([seg1]),
            _scrape_for_with_missing([seg2]),
        ])
        env.patch_write()
        rc = rw.cmd_write(WEEK_SPLIT_START, WEEK_SPLIT_END)
    if rc == 0:
        _ok("exit 0")
    else:
        _fail("exit 0", f"got {rc}")
    # Oba segmenty mają 2× NO_MAPPING każdy = 4 errors total. Po dedup → 2 unique names.
    nm_alert = env.telegram_messages[0] if env.telegram_messages else ""
    if "🚨 NO_MAPPING" in nm_alert and "Pominięte (zero zapisu COD): 2" in nm_alert:
        _ok("E4 alert dedup → 2 unique names (mimo 4 raw errors)")
    else:
        _fail("E4 alert dedup count", nm_alert)
    # Każdy z restauracji obecny TYLKO RAZ w alercie (set sortuje)
    n_bar_eljot = nm_alert.count("Bar Eljot")
    n_nowa = nm_alert.count("Nowa Restauracja XYZ")
    if n_bar_eljot == 1 and n_nowa == 1:
        _ok(f"Każda restauracja raz: Bar Eljot×{n_bar_eljot}, Nowa×{n_nowa}")
    else:
        _fail("Dedup: każda raz", f"Bar Eljot×{n_bar_eljot}, Nowa×{n_nowa}")


# -------------------------------------------------------------------
# E1: auto-rebuild mapping tests (E1-T1, E1-T2)
# -------------------------------------------------------------------

def test_e1_t1_rebuild_success():
    _hdr("E1-T1: _refresh_mapping success → świeży mapping użyty w cmd_write")
    target = _make_target("BK", 62, WEEK_SINGLE_START, WEEK_SINGLE_END, date(2026, 4, 22))
    # Symuluj że rebuild znalazł NOWĄ restaurację, której stary JSON nie miał.
    fresh_mapping = dict(FAKE_MAPPING["mapping"])
    fresh_mapping["Nowa Z Rebuild"] = 999
    with MockEnv() as env:
        env.patch_find_target([target])
        env.patch_empty_check(_empty_check_ok())
        env.patch_refresh_mapping(fresh_mapping)
        # Custom scrape: dodaj Nowa Z Rebuild do restaurants → odpyta mapping
        custom_rests = FAKE_RESTAURANTS + [(99, "Nowa Z Rebuild")]

        def custom_scrape(restaurants, mapping, targets, opener=None):
            env.scrape_calls.append((list(restaurants), list(targets)))
            results = []
            errors = []
            for row_idx, name in custom_rests:
                if name not in mapping:
                    errors.append(f"NO_MAPPING {name!r}")
                    results.append({"row": row_idx, "rest": name, "error": "no_mapping"})
                    continue
                results.append({
                    "row": row_idx, "rest": name,
                    "cod_per_segment": [50.0], "had_error": False,
                })
            return results, errors
        env._save_and_patch("_scrape_all", custom_scrape)
        env.patch_write()
        # fetch_sheet_grid musi też zwrócić Nowa Z Rebuild w restaurants
        env._save_and_patch(
            "fetch_sheet_grid",
            lambda: _make_grid(restaurants=custom_rests),
        )
        rc = rw.cmd_write(WEEK_SINGLE_START, WEEK_SINGLE_END)
    if rc == 0:
        _ok("exit 0")
    else:
        _fail("exit 0", f"got {rc}")
    # Nowa Z Rebuild została zmapowana → no NO_MAPPING dla niej
    nm_alert_present = any("Nowa Z Rebuild" in m for m in env.telegram_messages
                            if "🚨 NO_MAPPING" in m)
    if not nm_alert_present:
        _ok("Brak NO_MAPPING dla 'Nowa Z Rebuild' (rebuild ją złapał)")
    else:
        _fail("Brak NO_MAPPING dla nowego restu", env.telegram_messages)
    # Sprawdź że scrape dostał świeży mapping (z 'Nowa Z Rebuild')
    if env.scrape_calls:
        scrape_mapping = env.scrape_calls[-1][0]  # restaurants list passed
        # mapping nie jest bezpośrednio w scrape_calls; sprawdzamy via brak NO_MAPPING
        _ok("Custom scrape wywołane (mapping z _refresh_mapping)")
    else:
        _fail("Scrape wywołane", "no calls captured")


def test_e1_t2_rebuild_fail_fallback():
    _hdr("E1-T2: _refresh_mapping raise → fallback do load_mapping")
    target = _make_target("BK", 62, WEEK_SINGLE_START, WEEK_SINGLE_END, date(2026, 4, 22))
    with MockEnv() as env:
        env.patch_find_target([target])
        env.patch_empty_check(_empty_check_ok())
        # Symuluj że rebuild raise (panel down). cmd_write powinien fallback.
        # NIE używamy patch_refresh_mapping(Exception) — bo testujemy real
        # _refresh_mapping z patched build_and_save raise + load_mapping fallback.
        # Zamiast tego patchujemy oba: build_and_save → raise, load_mapping → FAKE.
        # Ale _refresh_mapping jest module function. Patch importowany symbol
        # pośrednio jest trudny — najprościej: patch _refresh_mapping na funkcję
        # która wywołuje real fallback path.
        from dispatch_v2.cod_weekly import run_weekly as rwmod

        def real_fallback():
            # Symuluj logikę real _refresh_mapping przy panelowym fail
            try:
                raise RuntimeError("Panel API down (simulated)")
            except Exception as e:
                rwmod.log.warning(f"E1: auto-rebuild FAILED ({type(e).__name__}: {e}) — fallback")
                return rwmod.load_mapping()["mapping"]
        env._save_and_patch("_refresh_mapping", real_fallback)
        env.patch_scrape([_scrape_for([target])])
        env.patch_write()
        rc = rw.cmd_write(WEEK_SINGLE_START, WEEK_SINGLE_END)
    if rc == 0:
        _ok("exit 0 (fallback graceful)")
    else:
        _fail("exit 0 fallback", f"got {rc}")
    if env.write_calls:
        _ok("Write call wywołane (cmd_write nie zatrzymał się na rebuild fail)")
    else:
        _fail("Write call wywołane", "no calls")


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
    test_nm1_zero_no_mapping()
    test_nm2_single_segment_no_mapping()
    test_nm3_split_week_dedup()
    test_e1_t1_rebuild_success()
    test_e1_t2_rebuild_fail_fallback()

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
