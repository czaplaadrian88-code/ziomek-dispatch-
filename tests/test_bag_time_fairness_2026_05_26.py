"""BUG A shadow (2026-05-26) — Σ bag_time + max + FIFO fairness scoring.

Solver minimalizuje total_drive_min (geo efficiency), nie bag_time fairness —
Case #2 Andersa: TomTom potwierdza wariant Adriana (15.7 < 17.2 min) mimo
wyższego total_drive. Faza 2 = shadow metryki + flag-gated scoring component
(default OFF). Kalibracja wag po 7-14d replay corpus.

Pattern = source-regression (jak test_obj_f3_best_effort_r6_koord) + sanity
check liczników FIFO violations.
"""
import inspect

from dispatch_v2 import common, dispatch_pipeline
from dispatch_v2.core import candidates as _cand_mod
from dispatch_v2.core import selection as _k12s  # K11: cialo petli per-kurier tam mieszka


def test_buga_common_contract_defaults():
    """Flagi default OFF, wagi startowe z planu."""
    assert common.ENABLE_BAG_TIME_FAIRNESS_SCORING is False
    assert common.BAG_TIME_SUM_PENALTY_PER_MIN == 1.0
    assert common.BAG_TIME_MAX_PENALTY_PER_MIN == 0.7
    assert common.BAG_TIME_FIFO_TIE_PENALTY == 5.0


def test_buga_block_present_in_source():
    """Blok BUG A obecny w _v327_eval_courier z guardem plan is not None."""
    src = inspect.getsource(dispatch_pipeline) + inspect.getsource(_cand_mod) + inspect.getsource(_k12s)  # K11: skan obu zrodel
    assert "BUG A shadow (2026-05-26)" in src
    start = src.find("BUG A shadow (2026-05-26)")
    assert start > 0
    section = src[start:start + 4200]  # E7-doklejki 3+4: blok urósł (shadow compute-always)
    assert "sum_bag_time_min_v" in section
    assert "max_bag_time_min_v" in section
    assert "fifo_violations" in section
    assert "plan.pickup_at" in section or "getattr(plan" in section
    assert "BAG_TIME_SUM_PENALTY_PER_MIN" in section
    assert "BAG_TIME_MAX_PENALTY_PER_MIN" in section
    assert "BAG_TIME_FIFO_TIE_PENALTY" in section


def test_buga_flag_gates_bonus_only_metrics_always():
    """Metryki sum/max/fifo zbierane ZAWSZE; bonus_* tylko gdy flag ON."""
    src = inspect.getsource(dispatch_pipeline) + inspect.getsource(_cand_mod) + inspect.getsource(_k12s)  # K11: skan obu zrodel
    start = src.find("BUG A shadow (2026-05-26)")
    section = src[start:start + 4200]  # E7-doklejki 3+4: blok urósł (shadow compute-always)
    # Bonus blok zagnieżdżony pod flag check
    assert "ENABLE_BAG_TIME_FAIRNESS_SCORING" in section
    # Sanity: bag_times computed BEFORE flag check (raw)
    flag_pos = section.find("ENABLE_BAG_TIME_FAIRNESS_SCORING")
    compute_pos = section.find("bag_times_per_order")
    assert 0 < compute_pos < flag_pos


def test_buga_keys_in_enriched_metrics():
    """7 nowych keys w enriched_metrics dict — propagowane do candidate.metrics."""
    src = inspect.getsource(dispatch_pipeline) + inspect.getsource(_cand_mod) + inspect.getsource(_k12s)  # K11: skan obu zrodel
    # Plik znajdzie sekcję dla enriched_metrics
    em_start = src.find('"sum_bag_time_min": round(sum_bag_time_min_v')
    assert em_start > 0
    section = src[em_start:em_start + 1600]  # E7: klucze _shadow w środku
    for key in (
        "sum_bag_time_min", "max_bag_time_min", "fifo_violations",
        "bonus_bag_time_sum", "bonus_bag_time_max", "bonus_fifo_violation",
        "bonus_r5_pickup_detour_penalty",
    ):
        assert f'"{key}"' in section, f"missing key {key}"


def test_buga_final_score_includes_new_bonuses():
    """final_score aggregation include 4 nowe bonus_*."""
    src = inspect.getsource(dispatch_pipeline) + inspect.getsource(_cand_mod) + inspect.getsource(_k12s)  # K11: skan obu zrodel
    fs = src.find("BUG A+B shadow (2026-05-26)")
    assert fs > 0
    section = src[fs:fs + 600]
    assert "final_score" in section
    assert "bonus_bag_time_sum" in section
    assert "bonus_bag_time_max" in section
    assert "bonus_fifo_violation" in section
    assert "bonus_r5_pickup_detour_penalty" in section


def test_buga_fifo_math_pure_function():
    """Sanity: gdy plan pickup [13:00,13:10,13:20] drop [14:00,13:50,13:40]
    → fifo_violations=3 (każda para łamana). Replikuję algorytm tu.
    """
    from datetime import datetime, timezone
    pu = {
        "A": datetime(2026, 5, 26, 13, 0, tzinfo=timezone.utc),
        "B": datetime(2026, 5, 26, 13, 10, tzinfo=timezone.utc),
        "C": datetime(2026, 5, 26, 13, 20, tzinfo=timezone.utc),
    }
    do = {
        "A": datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc),  # delivered last
        "B": datetime(2026, 5, 26, 13, 50, tzinfo=timezone.utc),
        "C": datetime(2026, 5, 26, 13, 40, tzinfo=timezone.utc),  # delivered first
    }
    pickup_order = sorted(pu.items(), key=lambda kv: kv[1])
    pickup_order = [(t, oid) for oid, t in pickup_order]
    fifo_violations = 0
    for i, (_pu_i, oid_i) in enumerate(pickup_order):
        for _pu_j, oid_j in pickup_order[i + 1:]:
            if do[oid_i] > do[oid_j]:
                fifo_violations += 1
    assert fifo_violations == 3, f"oczekiwałem 3, got {fifo_violations}"


def test_buga_fifo_math_perfect_order():
    """Perfect FIFO (drop order matches pickup order) → 0 violations."""
    from datetime import datetime, timezone
    pu = {
        "A": datetime(2026, 5, 26, 13, 0, tzinfo=timezone.utc),
        "B": datetime(2026, 5, 26, 13, 10, tzinfo=timezone.utc),
    }
    do = {
        "A": datetime(2026, 5, 26, 13, 30, tzinfo=timezone.utc),
        "B": datetime(2026, 5, 26, 13, 40, tzinfo=timezone.utc),
    }
    pickup_order = sorted(pu.items(), key=lambda kv: kv[1])
    pickup_order = [(t, oid) for oid, t in pickup_order]
    fifo_violations = 0
    for i, (_pu_i, oid_i) in enumerate(pickup_order):
        for _pu_j, oid_j in pickup_order[i + 1:]:
            if do[oid_i] > do[oid_j]:
                fifo_violations += 1
    assert fifo_violations == 0


def test_buga_shadow_serializer_loc_a_b():
    """Serializer A (candidate) + B (best) — explicit 3 raw keys w obu miejscach.

    bonus_* propaguje się przez auto-prefix; raw metryki potrzebują explicit
    bo nie mają znanego prefixu.
    """
    from dispatch_v2 import shadow_dispatcher
    src = inspect.getsource(shadow_dispatcher)
    # LOC A — _serialize_candidate (1× sekcja BUG A)
    # LOC B — _serialize_result (2× sekcja BUG A)
    assert src.count("BUG A shadow (2026-05-26)") >= 2
    for key in ("sum_bag_time_min", "max_bag_time_min", "fifo_violations"):
        # każdy klucz w obu sekcjach
        assert src.count(f'"{key}"') >= 2, f"key {key} missing in LOC A or B"
