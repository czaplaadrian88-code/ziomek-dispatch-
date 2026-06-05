"""BUG-D — Per-distance-bin traffic multiplier (V3.28+).

TomTom sample 2026-05-26 ujawnił że flat per-hour multiplier zaniża krótkie
segmenty centrum (~2.3× real vs OSRM ff) i lekko zawyża długie międzydzielnicowe
(~1.15× real vs OSRM ff). `get_traffic_multiplier_v2(dt_utc, distance_km)` dodaje
additive boost per distance bucket — tylko w peak (base > 1.0), floor at 1.0.

Empirical reference: `eod_drafts/2026-05-26/measurements.md`.
"""
from datetime import datetime, timezone


def _peak_dt(hour_warsaw: int) -> datetime:
    """Weekday peak Warsaw → UTC aware datetime."""
    return datetime(2026, 5, 26, hour_warsaw - 2, 30, tzinfo=timezone.utc)


def _offpeak_dt() -> datetime:
    """Weekday off-peak Warsaw 03:00 → UTC aware datetime."""
    return datetime(2026, 5, 26, 1, 0, tzinfo=timezone.utc)


def test_v2_no_distance_returns_base():
    """Backward compat: distance_km=None → identical to get_traffic_multiplier."""
    from dispatch_v2.common import get_traffic_multiplier, get_traffic_multiplier_v2
    dt = _peak_dt(16)
    assert get_traffic_multiplier_v2(dt, None) == get_traffic_multiplier(dt)


def test_v2_offpeak_returns_base_regardless_of_distance():
    """Off-peak (base=1.0): no boost applied, returns 1.0 dla dowolnego distance."""
    from dispatch_v2.common import get_traffic_multiplier_v2
    dt = _offpeak_dt()
    assert get_traffic_multiplier_v2(dt, 0.5) == 1.0
    assert get_traffic_multiplier_v2(dt, 3.0) == 1.0
    assert get_traffic_multiplier_v2(dt, 10.0) == 1.0


def test_v2_peak_short_segment_boost():
    """Peak short (<2 km): base + 1.0. RECALIB 2026-06-05: base(16:30)=1.55 → 2.55.

    UWAGA recalib↔BUG-D: empirical target był 2.3× (stara base 1.3 + 1.0 boost).
    Recalib podniósł base do 1.55 → boost +1.0 teraz PRZESTRZELIWA do 2.55. BUG-D
    additive boosts wymagają rekalibracji PRZED promocją flagi (shadow OFF →
    nie dotyka produkcji; test weryfikuje faktyczny output funkcji, nie target)."""
    from dispatch_v2.common import get_traffic_multiplier_v2
    dt = _peak_dt(16)  # 16:30 Warsaw, base=1.55 z (15,17,1.55) post-recalib
    assert get_traffic_multiplier_v2(dt, 1.5) == 2.55
    assert get_traffic_multiplier_v2(dt, 0.3) == 2.55  # boundary toward 0
    assert get_traffic_multiplier_v2(dt, 1.99) == 2.55  # upper boundary exclusive


def test_v2_peak_medium_segment_boost():
    """Peak medium (2-5 km): base + 0.4. RECALIB: base(16:30)=1.55 → 1.95."""
    from dispatch_v2.common import get_traffic_multiplier_v2
    dt = _peak_dt(16)  # base=1.55 post-recalib
    assert abs(get_traffic_multiplier_v2(dt, 2.0) - 1.95) < 0.001
    assert abs(get_traffic_multiplier_v2(dt, 4.0) - 1.95) < 0.001
    assert abs(get_traffic_multiplier_v2(dt, 4.99) - 1.95) < 0.001


def test_v2_peak_long_segment_reduction():
    """Peak long (>=5 km): base - 0.15. RECALIB: base(16:30)=1.55 → 1.40."""
    from dispatch_v2.common import get_traffic_multiplier_v2
    dt = _peak_dt(16)  # base=1.55 post-recalib
    assert abs(get_traffic_multiplier_v2(dt, 5.0) - 1.40) < 0.001
    assert abs(get_traffic_multiplier_v2(dt, 7.0) - 1.40) < 0.001
    assert abs(get_traffic_multiplier_v2(dt, 20.0) - 1.40) < 0.001


def test_v2_floor_at_1_0():
    """Boost ujemny nie obniża poniżej 1.0 (OSRM ff floor — nigdy szybciej niż brak ruchu).

    RECALIB: hour 19 podniesione do 1.25 (long → 1.10, już nie floor) → test używa
    hour 20 (base 1.10 z (20,21,1.10)) → long boost -0.15 = 0.95 → floored to 1.0."""
    from dispatch_v2.common import get_traffic_multiplier_v2
    dt_120 = _peak_dt(20)  # 20:30 Warsaw, base 1.10 → long -0.15 = 0.95 → floor 1.0
    assert get_traffic_multiplier_v2(dt_120, 10.0) == 1.0


def test_v2_naive_datetime_raises():
    """Parity z get_traffic_multiplier: aware datetime required."""
    from dispatch_v2.common import get_traffic_multiplier_v2
    naive = datetime(2026, 5, 26, 14, 30)
    try:
        get_traffic_multiplier_v2(naive, 1.5)
        raise AssertionError("expected TypeError")
    except TypeError:
        pass


def test_apply_traffic_multiplier_records_v2_shadow():
    """osrm_client._apply_traffic_multiplier records traffic_multiplier_v2_shadow field."""
    from dispatch_v2.osrm_client import _apply_traffic_multiplier
    result = {"duration_s": 100.0, "distance_km": 1.5, "distance_m": 1500}
    dt = _peak_dt(16)  # RECALIB base=1.55, short 1.5km → v2=2.55
    out = _apply_traffic_multiplier(result, dt)
    assert "traffic_multiplier_v2_shadow" in out
    assert out["traffic_multiplier_v2_shadow"] == 2.55
    # Raw preserved
    assert out["osrm_raw_duration_s"] == 100.0


def test_apply_traffic_multiplier_v2_shadow_handles_missing_distance():
    """Result bez distance_km: v2_shadow fallbacks do v1 (no distance correction)."""
    from dispatch_v2.osrm_client import _apply_traffic_multiplier
    result = {"duration_s": 100.0}  # no distance_km
    dt = _peak_dt(16)
    out = _apply_traffic_multiplier(result, dt)
    assert "traffic_multiplier_v2_shadow" in out
    assert abs(out["traffic_multiplier_v2_shadow"] - 1.55) < 0.001  # RECALIB base mult, no boost


def test_apply_traffic_multiplier_legacy_v1_when_flag_off():
    """Flag ENABLE_V326_DISTANCE_BIN_TRAFFIC_BOOST=False (default): applied mult = v1."""
    from dispatch_v2 import common, osrm_client
    # Default state: flag is False
    assert common.ENABLE_V326_DISTANCE_BIN_TRAFFIC_BOOST is False
    # Also need ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER=True (live mode), default True from env
    if not common.ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER:
        # Shadow mode: no mutation; skip this assertion path
        return
    result = {"duration_s": 100.0, "distance_km": 1.5}
    dt = _peak_dt(16)
    out = osrm_client._apply_traffic_multiplier(result, dt)
    # RECALIB v1 applied: 100 * 1.55 = 155
    assert out["duration_s"] == 155.0
    assert abs(out["traffic_multiplier"] - 1.55) < 0.001
    # v2_shadow recorded but NOT applied (overshoots 2.55 — recalibrate before promote)
    assert out["traffic_multiplier_v2_shadow"] == 2.55


def test_empirical_case_3_toriko_gk():
    """Case #3 (measurements.md): Toriko→GK 1.47km @ 18:30 Wt → TomTom 6.5min, OSRM ff 3.1min.
    Real ratio 2.10×. V2 predicts: base(17-19=1.2) + 1.0 short boost = 2.2× → 6.82 min.
    """
    from dispatch_v2.common import get_traffic_multiplier_v2
    dt = _peak_dt(18)  # 18:30 Warsaw
    mult = get_traffic_multiplier_v2(dt, 1.47)
    osrm_ff = 3.1
    predicted = osrm_ff * mult
    # Expected ~6.8 min (vs TomTom 6.5 real) — within 5% tolerance
    assert 6.5 < predicted < 7.1, f"predicted={predicted:.2f}"


def test_empirical_case_d_bacieczki_jp61b_long():
    """Case F (measurements.md): Bacieczki→JP61B 2.88km @ 18-19 Wt → TomTom 4.7min, OSRM ff 4.6min.
    Real ratio 1.02×. RECALIB: 2-5km medium bin: base(18-19=1.25) + 0.4 = 1.65.
    Acknowledged over-prediction dla tej kategorii — sample n=4 medium variable (1.02-2.35×).
    """
    from dispatch_v2.common import get_traffic_multiplier_v2
    dt = _peak_dt(18)
    mult = get_traffic_multiplier_v2(dt, 2.88)
    assert abs(mult - 1.65) < 0.001  # RECALIB 1.25 + 0.4 medium


def test_table_structure_sorted_ascending():
    """V326_OSRM_DISTANCE_BIN_BOOST_PEAK buckets in ascending distance order (for first-match)."""
    from dispatch_v2.common import V326_OSRM_DISTANCE_BIN_BOOST_PEAK
    boundaries = [max_km for max_km, _ in V326_OSRM_DISTANCE_BIN_BOOST_PEAK]
    assert boundaries == sorted(boundaries)
    # Last must be inf (catch-all)
    import math
    assert math.isinf(boundaries[-1])


# ─── BUG-D Faza 2a — stats per-bucket + tool ────────────────────────────


def test_distance_bin_classification():
    """get_distance_bin_v2 boundary correctness: <2 short, 2-5 medium, >=5 long, None=none."""
    from dispatch_v2.common import get_distance_bin_v2
    assert get_distance_bin_v2(None) == "none"
    assert get_distance_bin_v2(0.0) == "short"
    assert get_distance_bin_v2(1.99) == "short"
    assert get_distance_bin_v2(2.0) == "medium"
    assert get_distance_bin_v2(4.99) == "medium"
    assert get_distance_bin_v2(5.0) == "long"
    assert get_distance_bin_v2(50.0) == "long"


def test_apply_traffic_multiplier_increments_v2_stats():
    """_apply_traffic_multiplier should bump _osrm_stats v2 counters per call."""
    from dispatch_v2 import osrm_client
    # Reset baseline
    osrm_client._osrm_stats["traffic_mult_v2_sum"] = 0.0
    osrm_client._osrm_stats["traffic_mult_v2_calls"] = 0
    for bd in osrm_client._osrm_stats["traffic_mult_v2_bins"].values():
        bd["count"] = 0
        bd["sum"] = 0.0

    dt = _peak_dt(16)  # RECALIB base 1.55
    # Short call: 1.5km → mult v2 = 2.55
    osrm_client._apply_traffic_multiplier({"duration_s": 60.0, "distance_km": 1.5}, dt)
    # Medium call: 3.0km → mult v2 = 1.95
    osrm_client._apply_traffic_multiplier({"duration_s": 120.0, "distance_km": 3.0}, dt)
    # Long call: 7.0km → mult v2 = 1.40
    osrm_client._apply_traffic_multiplier({"duration_s": 300.0, "distance_km": 7.0}, dt)
    # Missing distance call → mult v2 = 1.55 (fallback to v1 base)
    osrm_client._apply_traffic_multiplier({"duration_s": 60.0}, dt)

    stats = osrm_client._osrm_stats
    assert stats["traffic_mult_v2_calls"] == 4
    assert stats["traffic_mult_v2_bins"]["short"]["count"] == 1
    assert abs(stats["traffic_mult_v2_bins"]["short"]["sum"] - 2.55) < 0.001
    assert stats["traffic_mult_v2_bins"]["medium"]["count"] == 1
    assert abs(stats["traffic_mult_v2_bins"]["medium"]["sum"] - 1.95) < 0.001
    assert stats["traffic_mult_v2_bins"]["long"]["count"] == 1
    assert abs(stats["traffic_mult_v2_bins"]["long"]["sum"] - 1.40) < 0.001
    assert stats["traffic_mult_v2_bins"]["none"]["count"] == 1


def test_v2_stats_log_format_parseable_by_tool():
    """Tool regex V2_LINE_RE must match _maybe_log_stats output format."""
    from dispatch_v2.tools.osrm_traffic_v2_stats import V2_LINE_RE
    # Sample log line as emitted by _maybe_log_stats
    sample = (
        "2026-05-28 12:00:00 [INFO] osrm_client: "
        "OSRM traffic-mult-v2 hourly (shadow): calls=1247 avg_mult_v2=1.687 "
        "bins={'short': {'n': 412, 'avg': 2.31}, 'medium': {'n': 587, 'avg': 1.62}, 'long': {'n': 248, 'avg': 1.12}}"
    )
    m = V2_LINE_RE.search(sample)
    assert m is not None
    mode, calls, avg, bins_repr = m.groups()
    assert mode == "shadow"
    assert calls == "1247"
    assert avg == "1.687"
    import ast
    bins = ast.literal_eval(bins_repr)
    assert bins["short"]["n"] == 412
    assert bins["medium"]["avg"] == 1.62


def test_tool_aggregate_weighted_average():
    """aggregate() computes weighted avg by call count, not record count."""
    from dispatch_v2.tools.osrm_traffic_v2_stats import aggregate
    from datetime import datetime, timezone
    records = [
        {"ts": datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc), "mode": "shadow", "calls": 100,
         "avg_mult": 2.0, "bins": {"short": {"n": 100, "avg": 2.0}}},
        {"ts": datetime(2026, 5, 28, 11, 0, tzinfo=timezone.utc), "mode": "shadow", "calls": 300,
         "avg_mult": 1.5, "bins": {"short": {"n": 300, "avg": 1.5}}},
    ]
    agg = aggregate(records)
    # Weighted: (2.0*100 + 1.5*300) / 400 = 650/400 = 1.625
    assert agg["total_calls"] == 400
    assert abs(agg["weighted_avg_mult"] - 1.625) < 0.001
    assert agg["bins"]["short"]["n"] == 400
    assert abs(agg["bins"]["short"]["avg"] - 1.625) < 0.001


# ─── BUG-D Faza 2b — TLS leg tracking + per-route aggregate + serialization ───


def test_aggregate_legs_empty_returns_none():
    """aggregate_legs([]) → None, aggregate_legs(None) → None."""
    from dispatch_v2.traffic_v2_aggregator import aggregate_legs
    assert aggregate_legs([]) is None
    assert aggregate_legs(None) is None


def test_aggregate_legs_single_leg():
    """Single-leg route: avg == max == min, totals = single value."""
    from dispatch_v2.traffic_v2_aggregator import aggregate_legs
    legs = [{"distance_km": 1.5, "raw_min": 2.5, "v1_mult": 1.3, "v2_mult": 2.3, "bin": "short"}]
    out = aggregate_legs(legs)
    assert out["n_legs"] == 1
    assert out["avg_v2_mult"] == 2.3
    assert out["max_v2_mult"] == 2.3
    assert out["min_v2_mult"] == 2.3
    assert out["bins_count"] == {"short": 1, "medium": 0, "long": 0, "none": 0}
    assert out["total_raw_min"] == 2.5
    assert abs(out["total_v2_predicted_min"] - 5.75) < 0.001  # 2.5 * 2.3
    assert abs(out["total_v1_predicted_min"] - 3.25) < 0.001  # 2.5 * 1.3
    assert abs(out["v2_v1_delta_min"] - 2.5) < 0.001


def test_aggregate_legs_multi_leg_mixed_bins():
    """Multi-leg mixed bins: aggregate sums + per-bin count + weighted totals."""
    from dispatch_v2.traffic_v2_aggregator import aggregate_legs
    legs = [
        {"distance_km": 1.5, "raw_min": 3.0, "v1_mult": 1.3, "v2_mult": 2.3, "bin": "short"},
        {"distance_km": 3.0, "raw_min": 6.0, "v1_mult": 1.3, "v2_mult": 1.7, "bin": "medium"},
        {"distance_km": 7.0, "raw_min": 15.0, "v1_mult": 1.3, "v2_mult": 1.15, "bin": "long"},
    ]
    out = aggregate_legs(legs)
    assert out["n_legs"] == 3
    assert out["bins_count"] == {"short": 1, "medium": 1, "long": 1, "none": 0}
    assert out["total_raw_min"] == 24.0
    # Σ raw × v2: 3*2.3 + 6*1.7 + 15*1.15 = 6.9 + 10.2 + 17.25 = 34.35
    assert abs(out["total_v2_predicted_min"] - 34.35) < 0.001
    # Σ raw × v1: 24 * 1.3 = 31.2
    assert abs(out["total_v1_predicted_min"] - 31.2) < 0.001
    assert abs(out["v2_v1_delta_min"] - 3.15) < 0.001
    # avg = (2.3 + 1.7 + 1.15) / 3 = 5.15 / 3 ≈ 1.717
    assert abs(out["avg_v2_mult"] - 1.717) < 0.001
    assert out["max_v2_mult"] == 2.3
    assert out["min_v2_mult"] == 1.15
    # Per-leg breakdown preserved as defensive copy
    assert len(out["legs"]) == 3
    assert out["legs"][0]["bin"] == "short"


def test_aggregate_legs_tolerant_to_missing_fields():
    """Legs z brakującymi raw_min lub v2_mult nie crashują — skipped w averages."""
    from dispatch_v2.traffic_v2_aggregator import aggregate_legs
    legs = [
        {"distance_km": 1.5, "raw_min": 3.0, "v1_mult": 1.3, "v2_mult": 2.3, "bin": "short"},
        {"distance_km": None, "raw_min": None, "v1_mult": 1.3, "v2_mult": None, "bin": "none"},
    ]
    out = aggregate_legs(legs)
    assert out["n_legs"] == 2
    # avg/max/min liczone tylko z non-None v2_mult
    assert out["avg_v2_mult"] == 2.3
    assert out["bins_count"]["none"] == 1


def test_tls_tracking_isolated_per_thread():
    """ThreadPoolExecutor: każdy thread ma własną legs list, brak cross-contamination."""
    from concurrent.futures import ThreadPoolExecutor
    from dispatch_v2.osrm_client import start_v2_request_tracking, stop_v2_request_tracking, _apply_traffic_multiplier
    from datetime import datetime, timezone

    def _worker(distance_km: float) -> int:
        start_v2_request_tracking()
        dt = datetime(2026, 5, 26, 14, 30, tzinfo=timezone.utc)
        _apply_traffic_multiplier({"duration_s": 60.0, "distance_km": distance_km}, dt)
        legs = stop_v2_request_tracking()
        return len(legs)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_worker, [1.0, 3.0, 7.0, 1.5]))
    # Każdy thread powinien dostać dokładnie 1 leg (NIE 4 z cross-contamination)
    assert all(r == 1 for r in results), f"unexpected: {results}"


def test_tls_tracking_stop_without_start_returns_none():
    """stop_v2_request_tracking bez wcześniejszego start → None (defense-in-depth)."""
    from dispatch_v2.osrm_client import stop_v2_request_tracking, _request_legs
    # Reset TLS state to ensure clean slate
    _request_legs.legs = None
    assert stop_v2_request_tracking() is None


def test_tls_tracking_double_stop_idempotent():
    """stop → stop (second call) zwraca None (cleanup zostawia legs=None)."""
    from dispatch_v2.osrm_client import start_v2_request_tracking, stop_v2_request_tracking
    start_v2_request_tracking()
    assert stop_v2_request_tracking() == []  # empty list, started but no calls
    assert stop_v2_request_tracking() is None  # second call: TLS cleared


def test_candidate_dataclass_has_traffic_v2_shadow_route_field():
    """Candidate dataclass new field default None, type compatible z aggregate output."""
    from dispatch_v2.dispatch_pipeline import Candidate
    c = Candidate(
        courier_id="123", name="Test", score=0.0,
        feasibility_verdict="MAYBE", feasibility_reason="ok",
        plan=None,
    )
    assert c.traffic_v2_shadow_route is None
    # Assign aggregate result
    c.traffic_v2_shadow_route = {"n_legs": 2, "avg_v2_mult": 1.5}
    assert c.traffic_v2_shadow_route["n_legs"] == 2


def test_serialize_candidate_writes_traffic_v2_shadow_route_loc_a():
    """shadow_dispatcher._serialize_candidate LOC A copies dataclass attribute."""
    from dispatch_v2.shadow_dispatcher import _serialize_candidate
    from dispatch_v2.dispatch_pipeline import Candidate

    payload = {"n_legs": 3, "avg_v2_mult": 1.6, "bins_count": {"short": 1, "medium": 1, "long": 1, "none": 0}}
    c = Candidate(
        courier_id="123", name="X", score=0.0,
        feasibility_verdict="MAYBE", feasibility_reason="ok",
        plan=None, metrics={"km_to_pickup": 1.5},
    )
    c.traffic_v2_shadow_route = payload
    out = _serialize_candidate(c)
    assert out["traffic_v2_shadow_route"] == payload


def test_serialize_candidate_none_when_attr_missing():
    """LOC A: brak attribute (legacy Candidate albo None) → None (no crash)."""
    from dispatch_v2.shadow_dispatcher import _serialize_candidate
    from dispatch_v2.dispatch_pipeline import Candidate

    c = Candidate(
        courier_id="123", name="X", score=0.0,
        feasibility_verdict="MAYBE", feasibility_reason="ok",
        plan=None,
    )
    # Default = None
    out = _serialize_candidate(c)
    assert out["traffic_v2_shadow_route"] is None
