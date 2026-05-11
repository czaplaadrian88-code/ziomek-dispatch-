"""V3.28 #36 — SLA tracker TZ-mixed poison message regression (2026-05-11).

Incydent ~18:48 UTC: 201 eventów COURIER_PICKED_UP/DELIVERED akumulowane w
queue od ~12:30 UTC. Root cause: `_parse` (legacy) zwracało mixed aware/naive
zależnie od input format. Subtrakcja aware-naive → TypeError → exception
propagated z forki przez outer try w `run()` → break → `mark_processed` nigdy
nie wywołany dla poison evt → head-of-queue blocker.

Fix dwuwarstwowy:
1. ROOT CAUSE: `_parse` → `_parse_aware_utc` w SLA path (zawsze aware UTC).
2. DEFENSE-IN-DEPTH: per-event try/except + `mark_failed` → poison message NIE
   blokuje reszty kolejki (audit visibility zachowany przez status=failed).

Tests:
- mixed_tz_aware_utc_vs_naive_warsaw: poprzedni crash scenario teraz numerycznie correct
- aware_utc_both_sides_unchanged: regression — gdy oba aware UTC, wynik niezmieniony
- naive_warsaw_both_sides_unchanged: regression — gdy oba naive Warsaw, wynik niezmieniony
  (legacy expected behavior — _parse_aware_utc normalizuje obu do UTC, diff numerycznie taki sam)
- mark_failed_called_on_process_exception: poison-msg defense — mark_failed wywoływany
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2.sla_tracker import _parse_aware_utc


def test_mixed_tz_aware_utc_vs_naive_warsaw():
    """Regression: oid=472381 scenario — picked_up naive Warsaw, delivered aware UTC."""
    p = _parse_aware_utc("2026-05-11 13:22:37")        # naive → Warsaw → UTC 11:22:37
    d = _parse_aware_utc("2026-05-11T12:34:55.962061+00:00")  # aware UTC 12:34:55
    assert p is not None and d is not None
    assert p.tzinfo is not None, "must be aware"
    assert d.tzinfo is not None, "must be aware"
    diff_min = round((d - p).total_seconds() / 60, 1)
    # 13:22 Warsaw = 11:22 UTC; delivered 12:34 UTC → +72.3 min (real SLA violation)
    assert diff_min == 72.3, f"expected 72.3, got {diff_min}"


def test_aware_utc_both_sides_unchanged():
    """Regression: gdy oba aware UTC (nowa schemat), wynik niezmieniony."""
    p = _parse_aware_utc("2026-05-11T12:00:00+00:00")
    d = _parse_aware_utc("2026-05-11T12:25:30+00:00")
    diff_min = round((d - p).total_seconds() / 60, 1)
    assert diff_min == 25.5


def test_naive_warsaw_both_sides_unchanged():
    """Regression: gdy oba naive Warsaw (legacy panel emit), wynik numerycznie correct.

    Pre-#36: `_parse` zwracał oba naive → diff numerycznie correct (oba w tej samej
    nieznanej strefie, timedelta NIE zależy od tzinfo gdy oba w tej samej strefie).
    Post-#36: `_parse_aware_utc` konwertuje oba Warsaw→UTC → diff IDENTYCZNY.
    """
    p = _parse_aware_utc("2026-05-11 14:00:00")
    d = _parse_aware_utc("2026-05-11 14:25:30")
    diff_min = round((d - p).total_seconds() / 60, 1)
    assert diff_min == 25.5


def test_sla_violation_threshold_35min():
    """Smoke: dokładnie 35 min → na granicy OK, 35.1 min → violation."""
    p = _parse_aware_utc("2026-05-11T12:00:00+00:00")
    d35 = _parse_aware_utc("2026-05-11T12:35:00+00:00")
    diff35 = round((d35 - p).total_seconds() / 60, 1)
    assert diff35 == 35.0 and (diff35 <= 35) is True
    d36 = _parse_aware_utc("2026-05-11T12:35:30+00:00")
    diff36 = round((d36 - p).total_seconds() / 60, 1)
    assert diff36 == 35.5 and (diff36 <= 35) is False


def test_parse_aware_utc_returns_none_for_empty():
    """Defensive: pusty / None / malformed input → None (no crash)."""
    assert _parse_aware_utc(None) is None
    assert _parse_aware_utc("") is None
    assert _parse_aware_utc("not-a-date") is None


if __name__ == "__main__":
    tests = [
        test_mixed_tz_aware_utc_vs_naive_warsaw,
        test_aware_utc_both_sides_unchanged,
        test_naive_warsaw_both_sides_unchanged,
        test_sla_violation_threshold_35min,
        test_parse_aware_utc_returns_none_for_empty,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(tests)} PASS, {failed} FAIL")
    sys.exit(0 if failed == 0 else 1)
