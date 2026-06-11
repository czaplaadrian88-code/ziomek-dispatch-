"""E7-doklejki 3+4 (2026-06-11) — pakiet flipowy BUG A/B.

Werdykty eod_drafts/2026-06-11/VERDICT_bug_a_b.md: flip B (4.0/km) → ≥7 dni →
flip A max+FIFO (SUM=0). Ten pakiet przygotowuje kod tak, by flip = wpisy w
flags.json (hot-reload, bez restartu):
  1. flagi w kanonie ETAP4 (decision_flag: flags.json → stała modułu → False),
  2. kary liczone ZAWSZE do pól *_shadow (lekcja #186 — przy OFF pola były
     zerowe i werdykt wymagał rekonstrukcji), flaga gate'uje tylko score,
  3. stałe kar nadpisywalne z flags.json (FLAGS_JSON_NUMERIC_OVERRIDES),
  4. DETOUR-01: marker r5_detour_extreme (detour > 7.5 km ∧ bag ≥ 2),
     obserwowalność bez zmiany score.
"""
import inspect
import json

from dispatch_v2 import common, dispatch_pipeline, shadow_dispatcher


def test_flags_in_etap4_canon_and_fingerprint():
    assert "ENABLE_BAG_TIME_FAIRNESS_SCORING" in common.ETAP4_DECISION_FLAGS
    assert "ENABLE_R5_PICKUP_DETOUR_PENALTY" in common.ETAP4_DECISION_FLAGS
    fp = common.flag_fingerprint()
    assert "ENABLE_BAG_TIME_FAIRNESS_SCORING=" in fp
    assert "ENABLE_R5_PICKUP_DETOUR_PENALTY=" in fp


def test_flags_default_off_via_decision_flag():
    # conftest wycina klucze ETAP4 z tmp flags.json → fallback = stała modułu
    assert common.decision_flag("ENABLE_BAG_TIME_FAIRNESS_SCORING") is False
    assert common.decision_flag("ENABLE_R5_PICKUP_DETOUR_PENALTY") is False


def test_decision_flag_flags_json_wins(monkeypatch):
    monkeypatch.setattr(
        common, "load_flags",
        lambda: {"ENABLE_R5_PICKUP_DETOUR_PENALTY": True})
    assert common.decision_flag("ENABLE_R5_PICKUP_DETOUR_PENALTY") is True


def test_numeric_overrides_tuple_and_conftest_strip():
    for k in ("BAG_TIME_SUM_PENALTY_PER_MIN", "BAG_TIME_MAX_PENALTY_PER_MIN",
              "BAG_TIME_FIFO_TIE_PENALTY", "R5_DETOUR_PENALTY_PER_KM",
              "R5_DETOUR_FREE_THRESHOLD_KM"):
        assert k in common.FLAGS_JSON_NUMERIC_OVERRIDES
    # tmp-kopia conftest wycina numeryczne klucze → testy sterują stałą modułu
    flags = common.load_flags()
    for k in common.FLAGS_JSON_NUMERIC_OVERRIDES:
        assert k not in flags


def test_live_flags_json_has_canon_keys():
    with open("/root/.openclaw/workspace/scripts/flags.json") as f:
        d = json.load(f)
    assert "ENABLE_BAG_TIME_FAIRNESS_SCORING" in d
    assert "ENABLE_R5_PICKUP_DETOUR_PENALTY" in d


def test_detour_extreme_constant_default():
    assert common.R5_DETOUR_EXTREME_KM == 7.5


def test_bug_a_shadow_computed_before_flag_gate():
    src = inspect.getsource(dispatch_pipeline)
    start = src.find("BUG A shadow (2026-05-26)")
    assert start > 0
    section = src[start:start + 4000]
    sh = section.find("shadow_bag_time_sum = -float")
    gate = section.find('decision_flag("ENABLE_BAG_TIME_FAIRNESS_SCORING")')
    assert 0 < sh < gate  # compute PRZED flagą (lekcja #186)


def test_bug_b_shadow_computed_before_flag_gate_and_extreme_marker():
    src = inspect.getsource(dispatch_pipeline)
    start = src.find("BUG B shadow (2026-05-26)")
    assert start > 0
    section = src[start:start + 3500]
    sh = section.find("shadow_r5_pickup_detour_penalty = -float")
    gate = section.find('decision_flag("ENABLE_R5_PICKUP_DETOUR_PENALTY")')
    assert 0 < sh < gate
    ext = section.find("r5_detour_extreme = bool(")
    assert 0 < ext < gate  # marker liczony zawsze, niezależnie od flagi


def test_enriched_metrics_have_shadow_and_extreme_keys():
    src = inspect.getsource(dispatch_pipeline)
    for key in ("bonus_bag_time_sum_shadow", "bonus_bag_time_max_shadow",
                "bonus_fifo_violation_shadow",
                "bonus_r5_pickup_detour_penalty_shadow", "r5_detour_extreme"):
        assert f'"{key}"' in src, key


def test_serializer_loc_a_b_have_detour_extreme():
    src = inspect.getsource(shadow_dispatcher)
    assert src.count('"r5_detour_extreme"') >= 2  # LOC A (alts) + LOC B (best)
