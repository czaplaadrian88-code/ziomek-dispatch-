"""A3 geocode cache TTL + drift detection regression coverage (2026-05-08).

Per audit STATE_OWNERSHIP_EVENT_FLOW F6 2026-05-07: cache (geocode_cache.json
+ restaurant_coords.json) ma `cached_at` ALE TTL nie był enforce'owany.
Combo z MP-#13 OSRM degraded mode: stale geocode + cache hit = silent stale.

Coverage:
  _is_cache_entry_fresh: missing/corrupt cached_at = fresh (defensive),
                         stale + ttl=ON, fresh, clock-skew (negative age)
  geocode() flow:        fresh hit, stale + TTL ON re-geocode, stale + TTL OFF cache hit
  drift detection:       no alert flag OFF, alert flag ON + drift > threshold,
                         no alert flag ON + drift < threshold
  cache_gc_stale:        bulk GC removes stale, keeps fresh + legacy (no cached_at)
  _drift_meters:         sanity 0m identical, ~111m for 0.001 deg lat
"""
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from dispatch_v2 import geocoding


# ---------------------------------------------------------------------------
# _is_cache_entry_fresh
# ---------------------------------------------------------------------------


def test_is_fresh_missing_cached_at_returns_true_defensive():
    """Legacy entries (pre-A3) bez cached_at → treats as fresh, NIE invalidate."""
    assert geocoding._is_cache_entry_fresh({"lat": 53.1, "lon": 23.1}, 86400.0) is True


def test_is_fresh_corrupt_cached_at_returns_true_defensive():
    assert geocoding._is_cache_entry_fresh({"cached_at": "not-a-number"}, 86400.0) is True
    assert geocoding._is_cache_entry_fresh({"cached_at": None}, 86400.0) is True


def test_is_fresh_within_ttl_returns_true():
    now = time.time()
    entry = {"cached_at": now - 100}  # 100s ago
    assert geocoding._is_cache_entry_fresh(entry, 86400.0) is True


def test_is_fresh_beyond_ttl_returns_false():
    now = time.time()
    entry = {"cached_at": now - 86401}  # >24h ago
    assert geocoding._is_cache_entry_fresh(entry, 86400.0) is False


def test_is_fresh_negative_age_clock_skew_returns_true():
    """Clock skew (cache_at w przyszłości) → defensive treat as fresh."""
    now = time.time()
    entry = {"cached_at": now + 100}
    assert geocoding._is_cache_entry_fresh(entry, 86400.0) is True


# ---------------------------------------------------------------------------
# _drift_meters sanity
# ---------------------------------------------------------------------------


def test_drift_meters_zero_identical_coords():
    assert geocoding._drift_meters(53.13, 23.16, 53.13, 23.16) == 0.0


def test_drift_meters_lat_delta_001_deg_approx_111m():
    drift = geocoding._drift_meters(53.13, 23.16, 53.131, 23.16)
    assert 110 <= drift <= 113, f"expected ~111m, got {drift}"


# ---------------------------------------------------------------------------
# cache_gc_stale
# ---------------------------------------------------------------------------


def test_cache_gc_stale_removes_old_keeps_fresh_and_legacy(tmp_path):
    cache_path = tmp_path / "geocode_cache.json"
    now = time.time()
    cache = {
        "stale1, białystok": {"lat": 53.1, "lon": 23.1, "cached_at": now - 86400 * 31},
        "fresh1, białystok": {"lat": 53.2, "lon": 23.2, "cached_at": now - 86400},
        "legacy_no_ts, białystok": {"lat": 53.3, "lon": 23.3},  # no cached_at
        "stale2, białystok": {"lat": 53.4, "lon": 23.4, "cached_at": now - 86400 * 60},
    }
    cache_path.write_text(json.dumps(cache))

    result = geocoding.cache_gc_stale(cache_path, ttl_sec=30 * 86400)

    assert result == {"scanned": 4, "removed": 2, "kept_legacy": 1}
    after = json.loads(cache_path.read_text())
    assert "stale1, białystok" not in after
    assert "stale2, białystok" not in after
    assert "fresh1, białystok" in after
    assert "legacy_no_ts, białystok" in after  # defensive — kept


def test_cache_gc_stale_no_removes_when_all_fresh(tmp_path):
    cache_path = tmp_path / "g.json"
    now = time.time()
    cache_path.write_text(json.dumps({
        "x, białystok": {"lat": 53.1, "lon": 23.1, "cached_at": now - 100}
    }))
    result = geocoding.cache_gc_stale(cache_path, ttl_sec=86400)
    assert result["removed"] == 0


# ---------------------------------------------------------------------------
# geocode() integration — TTL + drift
# ---------------------------------------------------------------------------


def _setup_cache(tmp_path, monkeypatch, entries: dict):
    cache_path = tmp_path / "geocode_cache.json"
    cache_path.write_text(json.dumps(entries))
    monkeypatch.setattr(geocoding, "CACHE_PATH", cache_path)
    return cache_path


def test_geocode_fresh_cache_hit_no_regeocode(tmp_path, monkeypatch):
    now = time.time()
    cache = {"akademicka 26, białystok": {
        "lat": 53.128, "lon": 23.161, "source": "google",
        "cached_at": now - 1000  # fresh
    }}
    _setup_cache(tmp_path, monkeypatch, cache)

    google_called = MagicMock(return_value=(99.99, 99.99))  # would be called if miss
    with patch.object(geocoding, "_google_geocode", google_called):
        result = geocoding.geocode("Akademicka 26", city="Białystok")

    assert result == (53.128, 23.161)
    google_called.assert_not_called()


def test_geocode_stale_ttl_on_triggers_regeocode(tmp_path, monkeypatch):
    now = time.time()
    cache = {"akademicka 26, białystok": {
        "lat": 53.128, "lon": 23.161, "source": "google",
        "cached_at": now - 86400 * 31  # 31d stale
    }}
    _setup_cache(tmp_path, monkeypatch, cache)

    new_coords = (53.1285, 23.1615)
    with patch.object(geocoding, "_google_geocode", return_value=new_coords):
        with patch.object(geocoding, "ENABLE_GEOCODE_CACHE_TTL_FORCE_TEST", True, create=True):
            result = geocoding.geocode("Akademicka 26", city="Białystok")

    assert result == new_coords
    after = json.loads((tmp_path / "geocode_cache.json").read_text())
    assert after["akademicka 26, białystok"]["lat"] == new_coords[0]


def test_geocode_stale_ttl_off_returns_cache_hit(tmp_path, monkeypatch):
    """ENABLE_GEOCODE_CACHE_TTL=False → legacy mode, stale entries served."""
    now = time.time()
    cache = {"akademicka 26, białystok": {
        "lat": 53.128, "lon": 23.161, "source": "google",
        "cached_at": now - 86400 * 365  # year-old
    }}
    _setup_cache(tmp_path, monkeypatch, cache)

    google_called = MagicMock(return_value=(99.0, 99.0))
    # Override _ttl_config: TTL OFF
    with patch.object(geocoding, "_ttl_config", return_value=(False, 30 * 86400, False, 200.0)):
        with patch.object(geocoding, "_google_geocode", google_called):
            result = geocoding.geocode("Akademicka 26", city="Białystok")

    assert result == (53.128, 23.161)  # served stale
    google_called.assert_not_called()


def test_geocode_drift_alert_fires_when_flag_on_and_drift_above_threshold(tmp_path, monkeypatch, caplog):
    now = time.time()
    old = {"lat": 53.13, "lon": 23.16}
    cache = {"x, białystok": {**old, "source": "google", "cached_at": now - 86400 * 31}}
    _setup_cache(tmp_path, monkeypatch, cache)

    new_coords = (53.135, 23.16)  # ~555m drift (5x 0.001 deg ≈ 555m)
    with patch.object(geocoding, "_ttl_config", return_value=(True, 30 * 86400, True, 200.0)):
        with patch.object(geocoding, "_google_geocode", return_value=new_coords):
            with caplog.at_level("WARNING"):
                result = geocoding.geocode("X", city="Białystok")

    drift_warns = [r for r in caplog.records if "GEOCODE_DRIFT_ALERT" in r.message]
    assert len(drift_warns) == 1
    assert "drift=" in drift_warns[0].message
    assert result == new_coords


def test_geocode_drift_alert_silent_when_flag_off(tmp_path, monkeypatch, caplog):
    now = time.time()
    cache = {"x, białystok": {"lat": 53.13, "lon": 23.16, "cached_at": now - 86400 * 31}}
    _setup_cache(tmp_path, monkeypatch, cache)

    with patch.object(geocoding, "_ttl_config", return_value=(True, 30 * 86400, False, 200.0)):
        with patch.object(geocoding, "_google_geocode", return_value=(53.135, 23.16)):  # 555m
            with caplog.at_level("WARNING"):
                geocoding.geocode("X", city="Białystok")

    drift_warns = [r for r in caplog.records if "GEOCODE_DRIFT_ALERT" in r.message]
    assert len(drift_warns) == 0


def test_geocode_drift_alert_silent_when_drift_below_threshold(tmp_path, monkeypatch, caplog):
    now = time.time()
    cache = {"x, białystok": {"lat": 53.13, "lon": 23.16, "cached_at": now - 86400 * 31}}
    _setup_cache(tmp_path, monkeypatch, cache)

    new_coords = (53.1301, 23.16)  # ~11m drift
    with patch.object(geocoding, "_ttl_config", return_value=(True, 30 * 86400, True, 200.0)):
        with patch.object(geocoding, "_google_geocode", return_value=new_coords):
            with caplog.at_level("WARNING"):
                geocoding.geocode("X", city="Białystok")

    drift_warns = [r for r in caplog.records if "GEOCODE_DRIFT_ALERT" in r.message]
    assert len(drift_warns) == 0  # 11m < 200m threshold


def test_geocode_legacy_entry_no_cached_at_treats_as_fresh(tmp_path, monkeypatch):
    """Pre-A3 entries bez cached_at — defensive, NIE invalidate masowo."""
    cache = {"akademicka 26, białystok": {"lat": 53.128, "lon": 23.161}}  # no cached_at
    _setup_cache(tmp_path, monkeypatch, cache)

    google_called = MagicMock(return_value=(99.0, 99.0))
    with patch.object(geocoding, "_google_geocode", google_called):
        result = geocoding.geocode("Akademicka 26", city="Białystok")

    assert result == (53.128, 23.161)
    google_called.assert_not_called()
