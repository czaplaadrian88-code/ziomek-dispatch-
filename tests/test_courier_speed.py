#!/usr/bin/env python3
"""
test_courier_speed.py — guards for tools/courier_speed_build.py.

The point of these tests is the *anti-artefact* contract:
  * a fix-pair where the courier stands still (≈0 km/h) at a node MUST NOT be counted
    as driving (DWELL, not MOTION) — this is the exact DRIVE_MIN_V2 trap;
  * GPS gaps and teleports MUST be dropped, not silently treated as motion;
  * a synthetic courier moving at a constant, known pace yields a STABLE multiplier
    across two time-halves (real signal, not noise);
  * a courier with a tiny sample is SHRUNK toward 1.0.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# tools/ is a sibling of tests/ under dispatch_v2; put dispatch_v2 on the path.
_HERE = Path(__file__).resolve().parent
_DV2 = _HERE.parent
for _p in (str(_DV2), str(_DV2 / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import courier_speed_build as csb  # noqa: E402


T0 = datetime(2026, 6, 13, 10, 0, 0, tzinfo=timezone.utc)


def _fix(sec, lat, lon, speed, jump_km, accept=True, teleport=False, low_acc=False):
    return {
        "ts": T0 + timedelta(seconds=sec),
        "lat": lat,
        "lon": lon,
        "speed": speed,
        "jump_km": jump_km,
        "fix_age_min": 0.5,
        "accept": accept,
        "teleport": teleport,
        "low_accuracy": low_acc,
    }


def _cfg(**kw):
    return csb.Cfg(**kw)


# --------------------------------------------------------------------------- #
# (D1) DWELL at a node is NOT driving.
# --------------------------------------------------------------------------- #
def test_dwell_segment_is_not_motion():
    cfg = _cfg()
    # parked at the restaurant: ~0 km/h, displacement < 100 m
    a = _fix(0, 53.1200, 23.1500, speed=0.0, jump_km=0.0)
    b = _fix(120, 53.1200, 23.1501, speed=0.3, jump_km=0.01)
    kind, dt, dist = csb.classify_segment(a, b, cfg)
    assert kind == "DWELL", f"standing-still pair must be DWELL, got {kind}"
    # and it contributes ZERO motion minutes
    motion, dwell_min, gaps, n_seg = csb.courier_motion_samples([a, b], cfg)
    assert motion == [], "dwell must not produce motion samples"
    assert dwell_min > 0, "dwell time should be accumulated separately"
    assert n_seg == 0


def test_long_dwell_does_not_inflate_multiplier():
    """The DRIVE_MIN_V2 artefact in miniature: a real drive followed by a long park
    must give the SAME multiplier as the drive alone (dwell excluded)."""
    cfg = _cfg(ref_kmh=30.0)
    # 6 min real driving over 3 km => 30 km/h => mult 1.0 vs ref 30
    drive = [
        _fix(0, 53.1000, 23.1000, speed=30.0, jump_km=0.0),
        _fix(180, 53.1100, 23.1200, speed=30.0, jump_km=1.5),
        _fix(360, 53.1200, 23.1400, speed=30.0, jump_km=1.5),
    ]
    # then ~20 minutes parked at the customer (≈0 km/h, no displacement). Fixes arrive
    # every ~2 min while parked — within the trust window — so each pair is real DWELL,
    # exactly the postój that DRIVE_MIN_V2 mis-booked as driving.
    park = []
    for k in range(1, 11):  # 10 fixes * 2 min = 20 min of standing
        park.append(_fix(360 + k * 120, 53.1200, 23.1400, speed=0.0, jump_km=0.0))
    m_drive, *_ = csb.courier_motion_samples(drive, cfg)
    m_all, dwell_min, *_ = csb.courier_motion_samples(drive + park, cfg)
    mult_drive, *_ = csb.mult_from_motion(m_drive, cfg)
    mult_all, *_ = csb.mult_from_motion(m_all, cfg)
    assert math.isclose(mult_drive, mult_all, rel_tol=1e-6), (
        f"dwell leaked into multiplier: drive={mult_drive} all={mult_all}")
    assert dwell_min > 15.0, "the long park should be booked as dwell, not driving"
    # sanity: ~30 km/h vs ref 30 => mult ~1.0
    assert abs(mult_all - 1.0) < 0.05


# --------------------------------------------------------------------------- #
# (D2) GPS gaps / teleports are dropped.
# --------------------------------------------------------------------------- #
def test_gap_segment_dropped():
    cfg = _cfg(gap_max_min=3.0)
    a = _fix(0, 53.10, 23.10, speed=30.0, jump_km=0.0)
    b = _fix(600, 53.20, 23.30, speed=40.0, jump_km=20.0)  # 10 min later => GAP
    kind, dt, dist = csb.classify_segment(a, b, cfg)
    assert kind == "GAP"


def test_teleport_segment_dropped():
    cfg = _cfg()
    a = _fix(0, 53.10, 23.10, speed=30.0, jump_km=0.0)
    b = _fix(60, 53.40, 23.60, speed=30.0, jump_km=40.0, teleport=True)
    kind, *_ = csb.classify_segment(a, b, cfg)
    assert kind == "GAP", "teleport endpoint must be dropped as untrusted"


def test_impossible_speed_capped_out():
    cfg = _cfg(max_kmh=90.0)
    a = _fix(0, 53.10, 23.10, speed=30.0, jump_km=0.0)
    b = _fix(30, 53.15, 23.20, speed=600.0, jump_km=5.0)  # 600 km/h => garbage
    kind, *_ = csb.classify_segment(a, b, cfg)
    assert kind == "GAP"


# --------------------------------------------------------------------------- #
# (D3) Multiplier maths and stability for a synthetic, known-pace courier.
# --------------------------------------------------------------------------- #
def _constant_pace_track(kmh, n, start_sec, step_sec, km_per_step):
    """A clean MOTION track at a constant pace (no dwell, no gaps)."""
    fixes = []
    lat = 53.10
    for i in range(n):
        lat += 0.01
        fixes.append(_fix(start_sec + i * step_sec, lat, 23.10, speed=kmh,
                          jump_km=(0.0 if i == 0 else km_per_step)))
    return fixes


def test_multiplier_value_matches_ref():
    """A courier moving at exactly REF_KMH has multiplier ~1.0; at half the speed ~2.0."""
    cfg = _cfg(ref_kmh=30.0, shrink_k=0.0)
    # 30 km/h: each 60 s step covers 0.5 km
    at_ref = _constant_pace_track(30.0, 40, 0, 60, 0.5)
    motion, *_ = csb.courier_motion_samples(at_ref, cfg)
    mult, mm, mk = csb.mult_from_motion(motion, cfg)
    assert abs(mult - 1.0) < 0.02, f"ref-pace courier should be ~1.0, got {mult}"

    # 15 km/h: each 60 s step covers 0.25 km => twice as slow => mult ~2.0
    slow = _constant_pace_track(15.0, 40, 0, 60, 0.25)
    motion2, *_ = csb.courier_motion_samples(slow, cfg)
    mult2, *_ = csb.mult_from_motion(motion2, cfg)
    assert abs(mult2 - 2.0) < 0.05, f"half-speed courier should be ~2.0, got {mult2}"


def test_multiplier_stable_across_halves():
    """Same constant pace across the whole window => stability gate says STABLE for that
    courier (rel_delta ~ 0)."""
    cfg = _cfg(ref_kmh=30.0, min_motion_min=2.0)
    # 60 fixes over an hour at 18 km/h, split in half => both halves same pace
    track = _constant_pace_track(18.0, 60, 0, 60, 0.3)
    motion, *_ = csb.courier_motion_samples(track, cfg)
    stab = csb.stability_report({"C1": motion}, cfg)
    assert stab["couriers_both_halves"] == 1
    pair = stab["pairs"][0]
    assert pair["rel_delta"] < 0.05, f"constant pace must be stable, rel_delta={pair['rel_delta']}"
    assert math.isclose(pair["mult_h1"], pair["mult_h2"], rel_tol=0.05)


def test_unstable_courier_flagged():
    """A courier fast in half 1 and slow in half 2 => large rel_delta (artefact-like)."""
    cfg = _cfg(ref_kmh=30.0, min_motion_min=2.0)
    fast = _constant_pace_track(40.0, 30, 0, 60, 0.6667)            # half 1
    # half 2 starts well after half 1 ends
    slow = _constant_pace_track(10.0, 30, 100000, 60, 0.1667)
    motion_fast, *_ = csb.courier_motion_samples(fast, cfg)
    motion_slow, *_ = csb.courier_motion_samples(slow, cfg)
    stab = csb.stability_report({"C1": motion_fast + motion_slow}, cfg)
    assert stab["couriers_both_halves"] == 1
    assert stab["pairs"][0]["rel_delta"] > 0.5, "fast→slow courier must look unstable"


def test_rank_flip_across_halves_is_artefact():
    """The credibility gate: if the fast/slow ORDERING of couriers flips between halves
    (negative correlation), the per-courier signal is noise -> shadow-only verdict, even
    if each courier's own absolute multiplier barely moved."""
    cfg = _cfg(ref_kmh=30.0, min_motion_min=2.0)
    # Multipliers are ref/actual_kmh. We want a clear spread (CV >= 0.08) AND a flipped
    # ordering between halves so the correlation goes negative.
    #   C1: slow (18.75 km/h => mult 1.6) in half 1, ref-pace (30 => 1.0) in half 2
    #   C2: ref-pace in half 1, slow in half 2  -> opposite ordering each half
    c1 = (_constant_pace_track(18.75, 25, 0, 60, 0.3125)
          + _constant_pace_track(30.0, 25, 100000, 60, 0.5))
    c2 = (_constant_pace_track(30.0, 25, 0, 60, 0.5)
          + _constant_pace_track(18.75, 25, 100000, 60, 0.3125))
    m1, *_ = csb.courier_motion_samples(c1, cfg)
    m2, *_ = csb.courier_motion_samples(c2, cfg)
    stab = csb.stability_report({"C1": m1, "C2": m2}, cfg)
    assert stab["couriers_both_halves"] == 2
    assert stab["between_courier_cv"] >= 0.08, "test needs real spread to exercise the rank gate"
    assert stab["pearson_r"] < 0, "ranks flipped -> correlation must be negative"
    assert stab["verdict"] == "UNSTABLE_rank_flips_artefact_shadow_only"


def test_no_per_courier_signal_when_everyone_same():
    """If all couriers move at the SAME pace (no spread), there is nothing to personalise:
    verdict says use ONE fleet multiplier, not per-courier."""
    cfg = _cfg(ref_kmh=30.0, min_motion_min=2.0)
    tracks = {}
    for i, cid in enumerate(("A", "B", "C")):
        # all ~24 km/h, just shifted in start time so halves are populated
        full = (_constant_pace_track(24.0, 25, i * 10, 60, 0.4)
                + _constant_pace_track(24.0, 25, 100000 + i * 10, 60, 0.4))
        m, *_ = csb.courier_motion_samples(full, cfg)
        tracks[cid] = m
    stab = csb.stability_report(tracks, cfg)
    assert stab["between_courier_cv"] is not None and stab["between_courier_cv"] < 0.08
    assert stab["verdict"] == "NO_PER_COURIER_SIGNAL_use_fleet_mult"


# --------------------------------------------------------------------------- #
# (D4) Shrinkage for small samples.
# --------------------------------------------------------------------------- #
def test_shrinkage_pulls_small_sample_to_one():
    cfg = _cfg(shrink_k=120.0)
    raw = 2.0  # courier looks twice as slow as OSRM
    # tiny sample: 4 motion-minutes => heavy shrink toward 1.0
    s_small = csb.shrink(raw, motion_min=4.0, cfg=cfg)
    # large sample: 600 motion-minutes => barely shrunk
    s_big = csb.shrink(raw, motion_min=600.0, cfg=cfg)
    assert 1.0 < s_small < 1.2, f"small sample must be pulled near 1.0, got {s_small}"
    assert s_big > 1.5, f"large sample should keep most of the signal, got {s_big}"
    assert s_small < s_big


def test_shrinkage_noop_when_mult_is_one():
    cfg = _cfg()
    assert math.isclose(csb.shrink(1.0, 10.0, cfg), 1.0, abs_tol=1e-9)


def test_nan_safe():
    cfg = _cfg()
    assert csb.mult_from_motion([], cfg)[0] != csb.mult_from_motion([], cfg)[0]  # nan
    assert csb.shrink(float("nan"), 10.0, cfg) != csb.shrink(float("nan"), 10.0, cfg)


# --------------------------------------------------------------------------- #
# (smoke) The build() entrypoint runs on the real GPS file if present and never throws.
# --------------------------------------------------------------------------- #
def test_build_smoke_on_real_data_if_present():
    if not csb.GPS_SHADOW.exists():
        return
    cfg = _cfg()
    table, stab, fleet = csb.build(cfg, days=30)
    assert isinstance(table, dict)
    assert "verdict" in stab
    assert "fleet_mult" in fleet
    for cid, row in table.items():
        # every reported multiplier must be finite or explicitly None (never inf/garbage)
        for k in ("mult_median", "ewma", "shrunk"):
            v = row[k]
            assert v is None or math.isfinite(v)
        # shrunk multiplier must be in a sane band for an urban courier
        if row["shrunk"] is not None:
            assert 0.2 <= row["shrunk"] <= 6.0, f"{cid} shrunk out of band: {row['shrunk']}"
