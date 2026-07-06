"""Fix #7 477271 (2026-05-31) — wait penalty steepen + pre_shift explicit demote.

477271 (Zapiecek/Grzegorz): best = Grzegorz pre_shift score 97 (ZAWYŻONY synthetic
clamp) + kurier czeka pod restauracją. Dwie poprawki:
  1. pre_shift → bucket 2 (jak blind+empty) w Opcji B — nie bije aktywnych kurierów
     mimo zawyżonego score (poprzednio bucket „other" tylko przypadkiem).
  2. v3273 wait_courier PER_MIN -5 → -8 (Adrian „kurier ma jak najmniej czekać").
     Legacy (-5) liczone równolegle w cieniu (bonus_v3273_wait_courier_legacy).
"""
import importlib
import inspect

from dispatch_v2 import common, dispatch_pipeline, scoring
from dispatch_v2.core import candidates as _k11c  # K11: cialo petli per-kurier (skan obu zrodel)
from dispatch_v2.scoring import compute_wait_courier_penalty as wcp
from dispatch_v2.dispatch_pipeline import (
    _is_pre_shift_cand, _late_pickup_tier, _late_pickup_score_first_key,
)


class FakeCand:
    def __init__(self, cid, score, pos_source, bag_size=1):
        self.courier_id = cid
        self.name = cid
        self.score = score
        self.feasibility_verdict = "MAYBE"
        self.metrics = {"pos_source": pos_source, "r6_bag_size": bag_size,
                        "new_pickup_late_min": 0.0}


def _sort_optionB(cands):
    orig = {id(c): i for i, c in enumerate(cands)}
    return sorted(cands, key=lambda c: _late_pickup_score_first_key(
        c, _late_pickup_tier(c), orig[id(c)], 5.0, 1.5, 60.0))


# === wait penalty steepen ===

def test_per_min_default_steeper():
    assert common.V3273_WAIT_COURIER_PER_MIN_PENALTY == -8.0
    assert common.V3273_WAIT_COURIER_PER_MIN_PENALTY_LEGACY == -5.0


def test_per_min_env_override(monkeypatch):
    monkeypatch.setenv("V3273_WAIT_COURIER_PER_MIN_PENALTY", "-6.0")
    m = importlib.reload(common)
    assert m.V3273_WAIT_COURIER_PER_MIN_PENALTY == -6.0
    monkeypatch.delenv("V3273_WAIT_COURIER_PER_MIN_PENALTY")
    importlib.reload(common)


def test_wait_penalty_new_vs_legacy():
    # <6 min: per_min nieistotny (interp do first-step) → identyczne
    assert abs(wcp(5, 1)[0] - wcp(5, 1, per_min=-5.0)[0]) < 0.01
    # >6 min: nowy stromszy
    assert wcp(7, 1)[0] == -18.0          # -10 + 1*-8
    assert wcp(7, 1, per_min=-5.0)[0] == -15.0   # legacy -10 + 1*-5
    assert wcp(10, 1)[0] == -42.0         # -10 + 4*-8
    assert wcp(10, 1, per_min=-5.0)[0] == -30.0  # legacy


def test_wait_penalty_invariants_unchanged():
    # sweet spot ≤3, first-step @6, hard-reject >15, bag=0 skip — bez zmian
    assert wcp(3, 1) == (0.0, False)
    assert wcp(6, 1)[0] == -10.0
    assert wcp(16, 1) == (0.0, True)
    assert wcp(10, 0) == (0.0, False)


# === pre_shift explicit demote ===

def test_is_pre_shift_cand():
    assert _is_pre_shift_cand(FakeCand("a", 0, "pre_shift")) is True
    assert _is_pre_shift_cand(FakeCand("a", 0, "gps")) is False


def test_477271_pre_shift_loses_to_active_despite_inflated_score():
    """Grzegorz pre_shift score 97 (zawyżony) NIE może bić aktywnego GPS −4
    (bucket 0 informed > bucket 2 pre_shift)."""
    grzegorz = FakeCand("500", 97.0, "pre_shift", bag_size=1)   # zawyżony synthetic
    gabriel = FakeCand("503", -4.0, "gps", bag_size=1)          # aktywny, niski score
    out = _sort_optionB([grzegorz, gabriel])
    assert out[0].courier_id == "503", "aktywny GPS bije pre_shift mimo score 97 vs -4"
    assert out[-1].courier_id == "500"


def test_pre_shift_with_bag_now_bucket2():
    """pre_shift z bagiem (poprzednio bucket 'other'=1) teraz bucket 2 (jak blind)."""
    pre_bag = FakeCand("1", 50.0, "pre_shift", bag_size=2)   # ma bag, ale pre_shift
    other = FakeCand("2", -10.0, None, bag_size=1)           # None pos → bucket 1 'other'
    out = _sort_optionB([pre_bag, other])
    # other (bucket 1) bije pre_shift+bag (bucket 2) mimo niższego score
    assert out[0].courier_id == "2"


# === source-regression: legacy v3273 w cieniu + serializacja ===

def test_pipeline_computes_legacy_wait():
    src = (inspect.getsource(dispatch_pipeline) + inspect.getsource(_k11c))
    assert "bonus_v3273_wait_courier_legacy" in src
    assert "per_min=getattr(C, \"V3273_WAIT_COURIER_PER_MIN_PENALTY_LEGACY\"" in src
    assert '"bonus_v3273_wait_courier_legacy":' in src  # serializowane do metrics


def test_scoring_per_min_param():
    sig = inspect.signature(scoring.compute_wait_courier_penalty)
    assert "per_min" in sig.parameters


def test_bucket_rank_includes_pre_shift():
    # 2026-06-24: bucket wydzielony do wspólnego `_selection_bucket` (equal-treatment-aware);
    # _late_pickup_score_first_key woła go zamiast inline. pre_shift dalej obsłużony tam.
    key_src = inspect.getsource(dispatch_pipeline._late_pickup_score_first_key)
    assert "_selection_bucket(c)" in key_src
    bucket_src = inspect.getsource(dispatch_pipeline._selection_bucket)
    assert "_is_pre_shift_cand(c)" in bucket_src
