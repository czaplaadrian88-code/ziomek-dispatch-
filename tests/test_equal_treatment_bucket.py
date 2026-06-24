"""Równe traktowanie no_gps + pre_shift w bucketach selekcji (Adrian 2026-06-24).

Model Adriana: kurier bez GPS (lub pre_shift), pusty bag, w grafiku → dojedzie w 15 min;
filtrem jest off-switch w konsoli koordynatora, NIE demote. `_demote_blind_empty` już to
szanuje dla no_gps, ale tiering-bucket (`_late_pickup_score_first_key`) + best_effort
(`_best_effort_sort_key`) re-demotowały no_gps/pre_shift do bucketa 2 (359 flipów/tydz,
pomiar `tools/nogps_preshift_bucket_replay.py`). Fix: flaga ENABLE_EQUAL_TREATMENT_BUCKET →
no_gps I pre_shift konkurują PO SCORE (bucket 0). 'none' (poza grafikiem) zostaje 2.
"""
import pytest
from dispatch_v2 import dispatch_pipeline as D


class _Cand:
    def __init__(self, cid, score, pos_source, bag_size, ext=False, breach=False):
        self.courier_id = cid
        self.score = score
        self.plan = type("P", (), {"sla_violations": 0, "total_duration_min": 10.0})()
        self.metrics = {
            "pos_source": pos_source, "r6_bag_size": bag_size,
            "new_pickup_needs_extension": ext, "late_pickup_committed_breach": breach,
            "new_pickup_late_min": 0, "r6_per_order_violations": [],
        }


def _tier_sort(cands):
    return sorted(cands, key=lambda c: D._late_pickup_score_first_key(
        c, D._late_pickup_tier(c), 0, 5.0, 1.5, 60.0))


INF = None
def setup_module(_):
    global INF
    INF = D.INFORMED_POS_SOURCES[0]


@pytest.mark.parametrize("blind_ps", ["no_gps", "pre_shift"])
def test_equal_on_competes_by_score(monkeypatch, blind_ps):
    monkeypatch.setattr(D, "_equal_bucket_on", lambda: True)
    blind = _Cand("BLIND", 95.0, blind_ps, bag_size=0)          # wyższy score
    informed = _Cand("INF", 60.0, INF, bag_size=2, ext=True)    # niższy, gorszy tier
    out = _tier_sort([informed, blind])
    assert out[0].courier_id == "BLIND", \
        f"{blind_ps} z wyższym score MUSI wygrać pod equal-treatment (bucket 0)"


@pytest.mark.parametrize("blind_ps", ["no_gps", "pre_shift"])
def test_equal_off_preserves_demote(monkeypatch, blind_ps):
    monkeypatch.setattr(D, "_equal_bucket_on", lambda: False)
    blind = _Cand("BLIND", 95.0, blind_ps, bag_size=0)
    informed = _Cand("INF", 60.0, INF, bag_size=2, ext=True)
    out = _tier_sort([informed, blind])
    assert out[0].courier_id == "INF", \
        f"flaga OFF = stare zachowanie: {blind_ps} w buckecie 2, informed wygrywa"


def test_none_still_demoted_even_when_on(monkeypatch):
    monkeypatch.setattr(D, "_equal_bucket_on", lambda: True)
    none_c = _Cand("NONE", 95.0, "none", bag_size=0)            # poza grafikiem
    informed = _Cand("INF", 60.0, INF, bag_size=2, ext=True)
    out = _tier_sort([informed, none_c])
    assert out[0].courier_id == "INF", "'none' NIE jest objęte equal-treatment → bucket 2"


def test_best_effort_key_respects_equal(monkeypatch):
    monkeypatch.setattr(D, "_equal_bucket_on", lambda: True)
    blind = _Cand("BLIND", 95.0, "no_gps", bag_size=0)
    informed = _Cand("INF", 60.0, INF, bag_size=2)
    out = sorted([informed, blind], key=D._best_effort_sort_key)
    assert out[0].courier_id == "BLIND", "best_effort też równo (bucket 0) dla no_gps"


def test_demote_excludes_preshift_when_on(monkeypatch):
    monkeypatch.setattr(D, "_equal_bucket_on", lambda: True)
    pre = _Cand("PRE", 95.0, "pre_shift", bag_size=0)
    assert D._is_demotable_blind_empty(pre) is False, \
        "pre_shift NIE demotowany pod equal-treatment (decyzja Adriana 24.06)"


def test_demote_preshift_when_off(monkeypatch):
    monkeypatch.setattr(D, "_equal_bucket_on", lambda: False)
    monkeypatch.setattr(D, "_no_gps_equal_on", lambda: True)
    pre = _Cand("PRE", 95.0, "pre_shift", bag_size=0)
    assert D._is_demotable_blind_empty(pre) is True, \
        "flaga OFF: pre_shift dalej demotowalny"


# --- LEXR6 (live-flip path) — ten sam bucket equal-treatment przed flipem ---
def _olx_bucket(c):
    from dispatch_v2 import objm_lexr6 as olx
    return olx.bucket(c, is_informed=D._is_informed_cand,
                      is_blind_empty=D._is_blind_empty_cand,
                      is_pre_shift=D._is_pre_shift_cand, bucket_fn=D._selection_bucket)


@pytest.mark.parametrize("blind_ps", ["no_gps", "pre_shift"])
def test_lexr6_bucket_equal_on(monkeypatch, blind_ps):
    monkeypatch.setattr(D, "_equal_bucket_on", lambda: True)
    assert _olx_bucket(_Cand("B", 90.0, blind_ps, 0)) == 0, \
        f"LEXR6 bucket: {blind_ps} == informed (0) pod equal-treatment → ta sama grupa tier×bucket"


def test_lexr6_bucket_equal_off(monkeypatch):
    monkeypatch.setattr(D, "_equal_bucket_on", lambda: False)
    assert _olx_bucket(_Cand("B", 90.0, "no_gps", 0)) == 2, \
        "LEXR6 bucket: flaga OFF = stare zachowanie (no_gps → 2)"


def test_lexr6_pick_groups_nogps_with_informed(monkeypatch):
    """Pod equal-treatment LEXR6 grupuje no_gps z informed (ten sam bucket) → lex może
    wybrać no_gps o lepszym R6 zamiast informed feasible[0]."""
    monkeypatch.setattr(D, "_equal_bucket_on", lambda: True)
    from dispatch_v2 import objm_lexr6 as olx
    informed = _Cand("INF", 80.0, INF, 2)        # feasible[0] (najwyższy score)
    informed.metrics["objm_r6_breach_max_min"] = 8.0
    nogps = _Cand("NG", 70.0, "no_gps", 0)        # niższy score, ale 0 R6 breach
    nogps.metrics["objm_r6_breach_max_min"] = 0.0
    out = olx.pick([informed, nogps], late_pickup_tier=D._late_pickup_tier,
                   is_informed=D._is_informed_cand, is_blind_empty=D._is_blind_empty_cand,
                   is_pre_shift=D._is_pre_shift_cand, bucket_fn=D._selection_bucket)
    assert out.courier_id == "NG", \
        "no_gps w tej samej grupie (bucket 0) → lex R6-primary wybiera go (0<8 breach)"
