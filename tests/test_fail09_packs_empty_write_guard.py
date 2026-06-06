"""FAIL-09 / PACKS-01 (2026-06-06) — guard: nie nadpisuj świeżego niepustego
panel_packs_cache pustką (pusty parse / HTTP 200 login-page).

Testuje czystą decyzję `_should_skip_empty_packs_write` — bez mockowania całego
tick() (parse/fetch/diff). Zachowanie inline w panel_watcher.tick deleguje do tej
funkcji 1:1, więc pokrycie logiki = pełne.
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2.panel_watcher import _should_skip_empty_packs_write  # noqa: E402

NOW = datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc)
MAX_AGE = 180.0


def _ts(age_s: float) -> str:
    """ISO-Z timestamp dla cache starego o `age_s` sekund względem NOW."""
    return (NOW - timedelta(seconds=age_s)).isoformat().replace("+00:00", "Z")


def test_new_packs_nonempty_never_skips():
    # Realne dane → zawsze zapisuj, niezależnie od poprzedniego cache.
    skip, age, n = _should_skip_empty_packs_write(
        {"Bartek": ["1"]}, {"packs": {"Anna": ["2"]}, "ts": _ts(5)}, MAX_AGE, NOW)
    assert skip is False


def test_empty_new_with_fresh_nonempty_prev_skips():
    # Pusty parse a chwilę temu panel widział kurierów z workami → POMIŃ (regresja).
    skip, age, n = _should_skip_empty_packs_write(
        {}, {"packs": {"Anna": ["2"], "Bartek": ["3", "4"]}, "ts": _ts(30)}, MAX_AGE, NOW)
    assert skip is True
    assert n == 2
    assert age is not None and abs(age - 30.0) < 1.0


def test_empty_new_with_empty_prev_writes_through():
    # zero→zero: nic nie tracimy, normalny zapis (np. pusty panel w nocy).
    skip, age, n = _should_skip_empty_packs_write(
        {}, {"packs": {}, "ts": _ts(10)}, MAX_AGE, NOW)
    assert skip is False


def test_empty_new_with_stale_prev_writes_through():
    # Poprzedni niepusty ale STARY (> max_age) → i tak odrzuci go czytnik (TTL),
    # więc write-through prościej; brak stale-forever.
    skip, age, n = _should_skip_empty_packs_write(
        {}, {"packs": {"Anna": ["2"]}, "ts": _ts(MAX_AGE + 60)}, MAX_AGE, NOW)
    assert skip is False
    assert age is not None and age > MAX_AGE


def test_empty_new_no_prev_cache_writes_through():
    # Brak poprzedniego cache (None / nie-dict) → nie pomijaj (first run / FileNotFound).
    assert _should_skip_empty_packs_write({}, None, MAX_AGE, NOW)[0] is False
    assert _should_skip_empty_packs_write({}, "garbage", MAX_AGE, NOW)[0] is False


def test_empty_new_with_unparseable_ts_writes_through():
    # Niepusty poprzedni ale ts nieczytelny → prev_age None → nie pomijaj (bezpiecznie).
    skip, age, n = _should_skip_empty_packs_write(
        {}, {"packs": {"Anna": ["2"]}, "ts": "not-a-date"}, MAX_AGE, NOW)
    assert skip is False
    assert age is None


def test_empty_new_age_exactly_at_boundary_skips():
    # age == max_age_s → granica włączająca (<=) → POMIŃ.
    skip, age, n = _should_skip_empty_packs_write(
        {}, {"packs": {"Anna": ["2"]}, "ts": _ts(MAX_AGE)}, MAX_AGE, NOW)
    assert skip is True


def test_empty_new_missing_ts_writes_through():
    # Niepusty poprzedni bez pola ts → prev_age None → nie pomijaj.
    skip, age, n = _should_skip_empty_packs_write(
        {}, {"packs": {"Anna": ["2"]}}, MAX_AGE, NOW)
    assert skip is False
    assert age is None
