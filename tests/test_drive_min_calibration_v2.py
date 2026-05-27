"""Sprint Drive_min Calibration v2 (2026-05-27) — unit tests.

Coverage:
  - Per pos_source offset (6 mapped cells + 1 placeholder F4-K2).
  - Floor guard (raw <8 → calibrated == 8).
  - Unknown / None pos_source → no-op (offset 0).
  - Flag MAIN OFF + SHADOW ON → metrics enriched ale drive_min NIE podmieniony.
  - Flag MAIN ON → drive_min podmieniony na calibrated.
  - Flag SHADOW ON → shadow log JSONL entry generowany per call.
  - Flag SHADOW OFF → brak wpisu do log.
  - Edge: pos_source=None, drive_min=None, tier=None.
  - Empirical regression: cell values match `/tmp/drive_min_bias_report.txt` §1.2.

Standalone-runnable (pytest collects `test_*` functions too).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import drive_min_calibration as dmc
from dispatch_v2 import auto_proximity_classifier as apc


# ───────────────── compute_pos_source_offset ─────────────────

def test_offset_no_gps_matches_empirical():
    """no_gps median Δ +6.5 min (n=1797)."""
    assert dmc.compute_pos_source_offset("no_gps", 10.0, False, "gold") == 6.5


def test_offset_pre_shift():
    """pre_shift median Δ +15.3 min (n=455)."""
    assert dmc.compute_pos_source_offset("pre_shift", 8.0, False, "std") == 15.3


def test_offset_gps_fresh():
    """gps median Δ +35.1 min (n=41) — parked-fresh slip-through."""
    assert dmc.compute_pos_source_offset("gps", 5.0, True, "std+") == 35.1


def test_offset_last_assigned_pickup():
    """last_assigned_pickup median Δ +30.9 (n=317)."""
    assert dmc.compute_pos_source_offset("last_assigned_pickup", 12.0, False, "gold") == 30.9


def test_offset_last_picked_up_pickup():
    """last_picked_up_pickup median Δ +34.7 (n=194, najgorszy stale-pos)."""
    assert dmc.compute_pos_source_offset("last_picked_up_pickup", 8.0, True, "std") == 34.7


def test_offset_last_picked_up_delivery():
    """last_picked_up_delivery median Δ +30.5 (n=16)."""
    assert dmc.compute_pos_source_offset("last_picked_up_delivery", 9.0, False, None) == 30.5


def test_offset_post_wave():
    """post_wave median Δ +30.9 (n=193)."""
    assert dmc.compute_pos_source_offset("post_wave", 11.0, False, "std+") == 30.9


def test_offset_last_picked_up_interp_placeholder():
    """F4-K2 placeholder = 10.0 (re-calibrate after LIVE)."""
    assert dmc.compute_pos_source_offset("last_picked_up_interp", 7.0, False, "gold") == 10.0


def test_offset_unknown_pos_source_is_zero():
    """Defensive: nowy/nieznany pos_source → 0.0 (no-op, NIE crash)."""
    assert dmc.compute_pos_source_offset("brand_new_enum_value", 10.0, False, "std") == 0.0


def test_offset_none_pos_source_is_zero():
    """None pos_source → 0.0."""
    assert dmc.compute_pos_source_offset(None, 10.0, False, "gold") == 0.0


# ───────────────── apply_calibration ─────────────────

def test_apply_calibration_basic_no_gps():
    """raw=10, no_gps offset +6.5 → 16.5, no floor."""
    calibrated, debug = dmc.apply_calibration(
        10.0, {"pos_source": "no_gps", "tier": "gold", "peak_window": False}
    )
    assert calibrated == 16.5
    assert debug["floor_hit"] is False
    assert debug["offset_applied"] == 6.5
    assert debug["raw_drive_min"] == 10.0
    assert debug["calibration_version"] == dmc.CALIBRATION_VERSION


def test_apply_calibration_floor_hit_low_raw_no_offset():
    """raw=2, unknown pos_source offset 0 → pre_floor=2, calibrated=8 (floor)."""
    calibrated, debug = dmc.apply_calibration(
        2.0, {"pos_source": "unknown_xyz", "tier": "gold"}
    )
    assert calibrated == 8.0
    assert debug["floor_hit"] is True
    assert debug["offset_applied"] == 0.0
    assert debug["pre_floor_value"] == 2.0


def test_apply_calibration_no_floor_when_offset_lifts_above():
    """raw=4, no_gps +6.5 → 10.5 > 8 — floor NIE fire."""
    calibrated, debug = dmc.apply_calibration(
        4.0, {"pos_source": "no_gps"}
    )
    assert calibrated == 10.5
    assert debug["floor_hit"] is False


def test_apply_calibration_high_raw_high_offset():
    """raw=18, last_picked_up_pickup +34.7 → 52.7 — no floor (large value)."""
    calibrated, debug = dmc.apply_calibration(
        18.0, {"pos_source": "last_picked_up_pickup", "tier": "std+"}
    )
    assert calibrated == 52.7
    assert debug["floor_hit"] is False


def test_apply_calibration_none_raw_defaults_to_zero():
    """None raw → defensive 0.0, then offset/floor apply."""
    calibrated, debug = dmc.apply_calibration(
        None, {"pos_source": "no_gps"}
    )
    # 0 + 6.5 = 6.5 < 8 → floor
    assert calibrated == 8.0
    assert debug["floor_hit"] is True
    assert debug["raw_drive_min"] == 0.0


def test_apply_calibration_empty_ctx():
    """Empty ctx dict → pos_source=None offset=0, floor applies if raw<8."""
    calibrated, debug = dmc.apply_calibration(3.0, {})
    assert calibrated == 8.0
    assert debug["pos_source"] is None
    assert debug["floor_hit"] is True


def test_apply_calibration_peak_window_logged_but_not_used():
    """peak_window=True przekazany do debug (Faza 2 forward-compat) ale current
    implementation go ignoruje — offset niezmieniony."""
    cal_off, dbg_off = dmc.apply_calibration(
        10.0, {"pos_source": "no_gps", "peak_window": False}
    )
    cal_on, dbg_on = dmc.apply_calibration(
        10.0, {"pos_source": "no_gps", "peak_window": True}
    )
    assert cal_off == cal_on  # No effect dziś
    assert dbg_off["peak_window"] is False
    assert dbg_on["peak_window"] is True


# ───────────────── Integration: auto_proximity_classifier._maybe_apply ─────────────────

class _FakeCS:
    """Duck-type CourierState dla testów."""
    def __init__(self, pos_source="no_gps", tier_bag="gold"):
        self.pos_source = pos_source
        self.tier_bag = tier_bag


def _isolated_shadow_log(tmpdir):
    """Pass-through env var dla shadow log redirect."""
    path = os.path.join(tmpdir, "drive_min_calibration_log_v2.jsonl")
    os.environ["DRIVE_MIN_CALIBRATION_SHADOW_LOG_PATH"] = path
    return path


def test_maybe_apply_flag_off_no_op_on_main_path():
    """Flag MAIN=OFF + SHADOW=OFF → metrics niezmienione, NIE log."""
    with tempfile.TemporaryDirectory() as td:
        log_path = _isolated_shadow_log(td)
        try:
            metrics = {"drive_min": 10.0, "pos_source": "no_gps"}
            out = apc._maybe_apply_drive_min_calibration(
                metrics=metrics,
                cs=_FakeCS(),
                flags={
                    "ENABLE_DRIVE_MIN_CALIBRATION_V2": False,
                    "ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW": False,
                },
                now=None, order_id="O1", courier_id="C1", tier="gold",
            )
            # Drive_min main path NOT substituted.
            assert out["drive_min"] == 10.0
            # Enriched audit fields jednak present (we apply calibration but only swap if MAIN).
            assert out["drive_min_raw"] == 10.0
            assert out["drive_min_calibrated"] == 16.5
            # NO shadow log written.
            assert not os.path.exists(log_path)
        finally:
            os.environ.pop("DRIVE_MIN_CALIBRATION_SHADOW_LOG_PATH", None)


def test_maybe_apply_flag_off_with_shadow_on_logs_entry():
    """Flag MAIN=OFF + SHADOW=ON (default) → metrics["drive_min"] niezmieniony, log written."""
    with tempfile.TemporaryDirectory() as td:
        log_path = _isolated_shadow_log(td)
        try:
            metrics = {"drive_min": 10.0, "pos_source": "no_gps"}
            out = apc._maybe_apply_drive_min_calibration(
                metrics=metrics,
                cs=_FakeCS(),
                flags={
                    "ENABLE_DRIVE_MIN_CALIBRATION_V2": False,
                    "ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW": True,
                },
                now=None, order_id="O42", courier_id="C42", tier="gold",
            )
            assert out["drive_min"] == 10.0  # main path unchanged
            assert out["drive_min_calibrated"] == 16.5
            assert os.path.exists(log_path)
            with open(log_path) as f:
                entries = [json.loads(line) for line in f]
            assert len(entries) == 1
            assert entries[0]["order_id"] == "O42"
            assert entries[0]["courier_id"] == "C42"
            assert entries[0]["pos_source"] == "no_gps"
            assert entries[0]["raw_drive_min"] == 10.0
            assert entries[0]["calibrated_drive_min"] == 16.5
            assert entries[0]["offset_applied"] == 6.5
            assert entries[0]["main_path_active"] is False
        finally:
            os.environ.pop("DRIVE_MIN_CALIBRATION_SHADOW_LOG_PATH", None)


def test_maybe_apply_flag_on_substitutes_drive_min():
    """Flag MAIN=ON → metrics["drive_min"] podmieniony na calibrated."""
    with tempfile.TemporaryDirectory() as td:
        _isolated_shadow_log(td)
        try:
            metrics = {"drive_min": 8.0, "pos_source": "last_picked_up_pickup"}
            out = apc._maybe_apply_drive_min_calibration(
                metrics=metrics,
                cs=_FakeCS(pos_source="last_picked_up_pickup", tier_bag="std+"),
                flags={
                    "ENABLE_DRIVE_MIN_CALIBRATION_V2": True,
                    "ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW": True,
                },
                now=None, order_id="O1", courier_id="C1", tier="std+",
            )
            # 8 + 34.7 = 42.7 (>8 floor not hit)
            assert out["drive_min"] == 42.7
            assert out["drive_min_raw"] == 8.0
            assert out["drive_min_calibrated"] == 42.7
            assert out["drive_min_calibration_offset"] == 34.7
            assert out["drive_min_calibration_floor_hit"] is False
        finally:
            os.environ.pop("DRIVE_MIN_CALIBRATION_SHADOW_LOG_PATH", None)


def test_maybe_apply_floor_hit_logged_as_floor_applied():
    """Floor hit case → log entry ma floor_applied=True."""
    with tempfile.TemporaryDirectory() as td:
        log_path = _isolated_shadow_log(td)
        try:
            metrics = {"drive_min": 1.0, "pos_source": "no_gps"}  # 1+6.5=7.5 < 8
            apc._maybe_apply_drive_min_calibration(
                metrics=metrics,
                cs=_FakeCS(),
                flags={
                    "ENABLE_DRIVE_MIN_CALIBRATION_V2": False,
                    "ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW": True,
                },
                now=None, order_id="O1", courier_id="C1", tier="gold",
            )
            with open(log_path) as f:
                entry = json.loads(f.readline())
            assert entry["floor_applied"] is True
            assert entry["calibrated_drive_min"] == 8.0
        finally:
            os.environ.pop("DRIVE_MIN_CALIBRATION_SHADOW_LOG_PATH", None)


def test_maybe_apply_no_drive_min_propagates_unchanged():
    """metrics bez drive_min → propagate as-is, no log."""
    with tempfile.TemporaryDirectory() as td:
        log_path = _isolated_shadow_log(td)
        try:
            metrics = {"pos_source": "no_gps", "score": 50.0}  # no drive_min
            out = apc._maybe_apply_drive_min_calibration(
                metrics=metrics,
                cs=_FakeCS(),
                flags={
                    "ENABLE_DRIVE_MIN_CALIBRATION_V2": True,
                    "ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW": True,
                },
                now=None, order_id="O1", courier_id="C1", tier="gold",
            )
            # Returns same dict, no enriched fields, no log written.
            assert out is metrics or out == metrics
            assert "drive_min_calibrated" not in out
            assert not os.path.exists(log_path)
        finally:
            os.environ.pop("DRIVE_MIN_CALIBRATION_SHADOW_LOG_PATH", None)


def test_maybe_apply_pos_source_fallback_from_courier_state():
    """metrics bez pos_source → fallback do cs.pos_source."""
    with tempfile.TemporaryDirectory() as td:
        _isolated_shadow_log(td)
        try:
            metrics = {"drive_min": 10.0}  # no pos_source w metrics
            out = apc._maybe_apply_drive_min_calibration(
                metrics=metrics,
                cs=_FakeCS(pos_source="gps", tier_bag="std"),
                flags={
                    "ENABLE_DRIVE_MIN_CALIBRATION_V2": True,
                    "ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW": False,
                },
                now=None, order_id="O1", courier_id="C1", tier="std",
            )
            # gps offset 35.1 → 10 + 35.1 = 45.1
            assert out["drive_min"] == 45.1
            assert out["drive_min_calibration_offset"] == 35.1
        finally:
            os.environ.pop("DRIVE_MIN_CALIBRATION_SHADOW_LOG_PATH", None)


# ───────────────── Empirical regression (cell values lock) ─────────────────

def test_offset_table_matches_empirical_report():
    """Gdy ktoś zmieni offsets — ten test złapie. Wartości z
    `/tmp/drive_min_bias_report.txt` §1.2 (n=3013 rows merged backfill)."""
    expected = {
        "no_gps": 6.5,
        "pre_shift": 15.3,
        "gps": 35.1,
        "last_assigned_pickup": 30.9,
        "last_picked_up_pickup": 34.7,
        "last_picked_up_delivery": 30.5,
        "post_wave": 30.9,
        "last_picked_up_interp": 10.0,  # F4-K2 placeholder
    }
    assert dmc.OFFSET_TABLE == expected, (
        f"OFFSET_TABLE drift detected: {dmc.OFFSET_TABLE} != {expected}. "
        f"Jeśli świadoma re-kalibracja — zaktualizuj ten test wraz z OFFSET_TABLE."
    )


def test_floor_min_constant():
    """FLOOR_MIN=8.0 — physical floor (parking+DWELL+entry+handover). Step 1.10."""
    assert dmc.FLOOR_MIN == 8.0


def test_calibration_version_set():
    """Version tag bumpowany przy każdej re-kalibracji (cron monthly)."""
    assert dmc.CALIBRATION_VERSION == "v1_2026-05-27"


# Standalone runner — pozwala uruchomić ten plik jako script.
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
