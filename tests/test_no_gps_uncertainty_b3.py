"""B3 (2026-06-20) — no_gps używalny z karą niepewności zamiast demote-do-KOORD.

Testuje czysty helper `_no_gps_uncertainty_rescue` + kontrakt flagi.
Pattern jak test_b2_zarazwolny_soon_free: SimpleNamespace kandydaci, monkeypatch
flagi. Default OFF = kod inertny (None → KOORD jak dziś).

Kalibracja: kara NO_GPS_UNCERTAINTY_MIN=12 (mediana narzutu fikcji, pomiar
tools/no_gps_eta_error.py). Escape R6: kara + r6_bag > 38 → None.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as dp


def _cand(cid, score, pos_source="no_gps", bag_size=0, r6_bag=20.0,
          committed_breach=False):
    return SimpleNamespace(
        courier_id=cid,
        name=f"K{cid}",
        score=score,
        metrics={
            "pos_source": pos_source,
            "r6_bag_size": bag_size,
            "r6_max_bag_time_min": r6_bag,
            "late_pickup_committed_breach": committed_breach,
            "travel_min": 15.0,
            "drive_min": 15.0,
        },
    )


# gate-score fn: tu = surowy score (bez delt) — wystarcza do testu logiki.
def _gate_score(c):
    return getattr(c, "score", None)


def _on(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_NO_GPS_UNCERTAINTY_PENALTY", True, raising=False)
    # decision_flag czyta flags.json → globals; upewnij że flags.json nie ma klucza
    monkeypatch.setattr(C, "load_flags", lambda: {})


def test_flag_default_off_module():
    assert C.ENABLE_NO_GPS_UNCERTAINTY_PENALTY is False
    assert hasattr(C, "NO_GPS_UNCERTAINTY_MIN")
    assert hasattr(C, "NO_GPS_UNCERTAINTY_R6_HARD_MAX_MIN")


def test_off_returns_none(monkeypatch):
    monkeypatch.setattr(C, "load_flags", lambda: {})
    monkeypatch.setattr(C, "ENABLE_NO_GPS_UNCERTAINTY_PENALTY", False, raising=False)
    feasible = [_cand("518", score=60.0)]
    assert dp._no_gps_uncertainty_rescue(feasible, -100.0, _gate_score, order_id=1) is None


def test_on_rescues_good_nogps(monkeypatch):
    _on(monkeypatch)
    feasible = [_cand("518", score=60.0, r6_bag=20.0)]  # 20+12=32 <= 38 → OK
    out = dp._no_gps_uncertainty_rescue(feasible, -100.0, _gate_score, order_id=1)
    assert out is not None
    cand, unc = out
    assert cand.courier_id == "518"
    assert unc == C.NO_GPS_UNCERTAINTY_MIN


def test_on_skips_low_score_nogps(monkeypatch):
    _on(monkeypatch)
    # score below MIN_PROPOSE → not "skądinąd dobry" → None
    feasible = [_cand("518", score=-150.0)]
    assert dp._no_gps_uncertainty_rescue(feasible, -100.0, _gate_score, order_id=1) is None


def test_on_escape_r6_breach_after_penalty(monkeypatch):
    _on(monkeypatch)
    # r6_bag 30 + 12 = 42 > 38 hard cap → escape → None (zostaje KOORD)
    feasible = [_cand("518", score=60.0, r6_bag=30.0)]
    assert dp._no_gps_uncertainty_rescue(feasible, -100.0, _gate_score, order_id=1) is None


def test_on_r6_within_cap_after_penalty(monkeypatch):
    _on(monkeypatch)
    # r6_bag 25 + 12 = 37 <= 38 → OK
    feasible = [_cand("518", score=60.0, r6_bag=25.0)]
    assert dp._no_gps_uncertainty_rescue(feasible, -100.0, _gate_score, order_id=1) is not None


def test_on_skips_committed_late_breach(monkeypatch):
    _on(monkeypatch)
    feasible = [_cand("518", score=60.0, r6_bag=20.0, committed_breach=True)]
    assert dp._no_gps_uncertainty_rescue(feasible, -100.0, _gate_score, order_id=1) is None


def test_on_only_blind_empty_eligible(monkeypatch):
    _on(monkeypatch)
    # gps courier with good score is NOT blind+empty → not rescued by this path
    feasible = [_cand("518", score=60.0, pos_source="gps")]
    assert dp._no_gps_uncertainty_rescue(feasible, -100.0, _gate_score, order_id=1) is None
    # no_gps but bag>0 → not empty → not eligible
    feasible2 = [_cand("519", score=60.0, pos_source="no_gps", bag_size=2)]
    assert dp._no_gps_uncertainty_rescue(feasible2, -100.0, _gate_score, order_id=1) is None


def test_on_picks_best_score_among_eligible(monkeypatch):
    _on(monkeypatch)
    feasible = [
        _cand("518", score=10.0, r6_bag=20.0),
        _cand("519", score=80.0, r6_bag=20.0),   # best
        _cand("520", score=40.0, r6_bag=20.0),
    ]
    out = dp._no_gps_uncertainty_rescue(feasible, -100.0, _gate_score, order_id=1)
    assert out is not None and out[0].courier_id == "519"


def test_build_result_applies_penalty_to_metrics(monkeypatch):
    _on(monkeypatch)
    cand = _cand("518", score=60.0, r6_bag=20.0)
    top = [cand]
    res = dp._build_no_gps_uncertainty_result(
        [cand], top, -100.0, -999.0, _gate_score, 1, [cand],
        None, "R", "addr")
    assert res is not None
    assert res.verdict == "PROPOSE"
    assert "no_gps_uncertainty_propose" in res.reason
    # penalty applied to metrics
    m = cand.metrics
    assert m["no_gps_uncertainty_applied_min"] == C.NO_GPS_UNCERTAINTY_MIN
    assert m["travel_min"] == 15.0 + C.NO_GPS_UNCERTAINTY_MIN
    assert m["r6_max_bag_time_min"] == 20.0 + C.NO_GPS_UNCERTAINTY_MIN


def test_build_result_none_when_no_candidate(monkeypatch):
    _on(monkeypatch)
    cand = _cand("518", score=-150.0)  # too low → no rescue
    res = dp._build_no_gps_uncertainty_result(
        [cand], [cand], -100.0, -999.0, _gate_score, 1, [cand],
        None, "R", "addr")
    assert res is None


def test_empty_feasible_none(monkeypatch):
    _on(monkeypatch)
    assert dp._no_gps_uncertainty_rescue([], -100.0, _gate_score, order_id=1) is None
