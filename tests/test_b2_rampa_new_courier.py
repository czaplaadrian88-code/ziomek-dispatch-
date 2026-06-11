"""SP-B2-RAMPA (2026-06-11, roadmapa BARTEK 2.0) — testy rampy nowych kurierów.

Zamiast niewidzialności (sentinel -1e9 / gradient -50): przez pierwsze 30
dostaw nowy kurier dostaje krótkie kursy (km≤2,5 ∧ bag==0 ∧ slot≠14-17)
z malusem -20; kursy poza profilem → -1e9 (zostaje w puli — ALWAYS-PROPOSE).
Po 30 dostawach → normalne reguły R-04 (gradient V325).
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as dp
from dispatch_v2 import shadow_dispatcher

T_LUNCH = datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc)      # 12:00 Warsaw
T_HIGH_RISK = datetime(2026, 6, 11, 13, 0, tzinfo=timezone.utc)  # 15:00 Warsaw

NEG_INF = -1e9


class _Cand:
    def __init__(self, cid, score, tier="new", bag=0, km=1.5):
        self.courier_id = cid
        self.score = score
        self.metrics = {
            "cs_tier_label": tier,
            "cs_tier_bag": tier,
            "bag_size_before": bag,
            "km_to_pickup": km,
            "bundle_level3_dev": None,
        }


@pytest.fixture(autouse=True)
def _ramp_env(tmp_path, monkeypatch):
    """Sterowalny licznik dostaw + pewny stan flag (V325 cap ON, rampa ON)."""
    rel = tmp_path / "courier_reliability.json"
    rel.write_text(json.dumps({
        "fleet_median_breach_rate": 0.08,
        "couriers": {
            "900": {"n_delivered": 45, "breach_rate": 0.05, "confidence": "high"},
            "901": {"n_delivered": 12, "breach_rate": 0.10, "confidence": "low"},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(C, "A2_RELIABILITY_FEED_PATH", str(rel), raising=False)
    monkeypatch.setattr(C, "ENABLE_V325_NEW_COURIER_CAP", True, raising=False)
    dp._NEW_COURIER_DELIV_CACHE.update(mtime=None, data={})
    _set_flag("ENABLE_NEW_COURIER_RAMP", True)
    yield
    dp._NEW_COURIER_DELIV_CACHE.update(mtime=None, data={})


def _set_flag(name, value):
    """Zapis do TMP-kopii flags.json (conftest _isolate_flags_json) + mtime bump."""
    p = str(C.FLAGS_PATH)
    d = json.load(open(p))
    d[name] = value
    with open(p, "w") as f:
        json.dump(d, f)
    st = os.stat(p)
    os.utime(p, (st.st_atime + 10, st.st_mtime + 10))
    C._flags_cache = None  # wymuś reload niezależnie od ziarnistości mtime


def test_ramp_eligible_short_run_gets_minus_20():
    """Nowy (0 dostaw), km 1.5, bag 0, lunch → malus -20 zamiast gradientu."""
    new = _Cand("555", 60.0, km=1.5)
    alt = _Cand("123", 55.0, tier="gold")
    out = dp._v325_new_courier_penalty([new, alt], order_id="t1", now=T_LUNCH)
    assert new.score == 40.0, new.score
    ramp = new.metrics["new_courier_ramp"]
    assert ramp["active"] is True and ramp["eligible"] is True
    assert ramp["deliveries"] == 0 and ramp["malus"] == -20.0
    assert "rampa 0/30" in new.metrics["v325_new_courier_flag"]
    assert new in out


def test_ramp_blocks_long_distance():
    new = _Cand("555", 60.0, km=4.2)
    alt = _Cand("123", 30.0, tier="gold")
    dp._v325_new_courier_penalty([new, alt], order_id="t2", now=T_LUNCH)
    assert new.score == NEG_INF
    ramp = new.metrics["new_courier_ramp"]
    assert ramp["eligible"] is False and ramp["reason"].startswith("dystans")


def test_ramp_blocks_missing_km():
    new = _Cand("555", 60.0, km=None)
    dp._v325_new_courier_penalty([new], order_id="t2b", now=T_LUNCH)
    assert new.score == NEG_INF
    assert new.metrics["new_courier_ramp"]["reason"] == "dystans_brakkm"


def test_ramp_blocks_nonempty_bag():
    """bag=1 przechodził w starym gradiencie — rampa jest surowsza (H13)."""
    new = _Cand("555", 60.0, bag=1, km=1.0)
    dp._v325_new_courier_penalty([new], order_id="t3", now=T_LUNCH)
    assert new.score == NEG_INF
    assert new.metrics["new_courier_ramp"]["reason"] == "bag_niepusty"


def test_ramp_blocks_high_risk_slot():
    new = _Cand("555", 60.0, km=1.0)
    dp._v325_new_courier_penalty([new], order_id="t4", now=T_HIGH_RISK)
    assert new.score == NEG_INF
    ramp = new.metrics["new_courier_ramp"]
    assert ramp["reason"] == "slot_14_17" and ramp["slot"] == "high_risk"


def test_post_ramp_falls_back_to_gradient():
    """cid=900 ma 45 dostaw ≥ 30 → stary gradient (advantage<20 → -50)."""
    new = _Cand("900", 60.0, km=9.9)  # km nieistotny post-rampa
    alt = _Cand("123", 55.0, tier="gold")
    dp._v325_new_courier_penalty([new, alt], order_id="t5", now=T_LUNCH)
    assert new.score == 10.0, new.score  # 60 + (-50)
    assert new.metrics["new_courier_ramp"] == {"active": False, "deliveries": 45}
    assert new.metrics["v325_new_courier_penalty"] == C.V325_NEW_COURIER_PENALTY_LOW_ADVANTAGE


def test_ramp_counter_from_file_below_threshold():
    """cid=901 ma 12 dostaw < 30 → rampa aktywna, deliveries=12 w metrykach."""
    new = _Cand("901", 60.0, km=1.2)
    dp._v325_new_courier_penalty([new], order_id="t6", now=T_LUNCH)
    assert new.metrics["new_courier_ramp"]["deliveries"] == 12
    assert new.score == 40.0


def test_flag_off_restores_legacy_behavior():
    _set_flag("ENABLE_NEW_COURIER_RAMP", False)
    new = _Cand("555", 60.0, km=4.2)  # poza rampą, ale flaga OFF
    alt = _Cand("123", 55.0, tier="gold")
    dp._v325_new_courier_penalty([new, alt], order_id="t7", now=T_LUNCH)
    assert new.score == 10.0  # gradient -50 (advantage 5 < 20)
    assert "new_courier_ramp" not in new.metrics


def test_non_new_tier_untouched():
    gold = _Cand("123", 70.0, tier="gold", km=9.0, bag=2)
    dp._v325_new_courier_penalty([gold], order_id="t8", now=T_LUNCH)
    assert gold.score == 70.0
    assert "new_courier_ramp" not in gold.metrics


def test_always_propose_sole_blocked_candidate_stays_in_pool():
    """Jedyny kandydat poza rampą: -1e9, ale ZOSTAJE w feasible (zero KOORD)."""
    new = _Cand("555", 60.0, km=7.0)
    out = dp._v325_new_courier_penalty([new], order_id="t9", now=T_LUNCH)
    assert len(out) == 1 and out[0] is new
    assert new.score == NEG_INF


def test_ramp_eligible_sorts_above_blocked():
    """Dwóch nowych: rampowy (-20) sortuje się nad zablokowanym (-1e9)."""
    ok = _Cand("555", 50.0, km=1.0)
    blocked = _Cand("556", 90.0, km=8.0)
    out = dp._v325_new_courier_penalty([blocked, ok], order_id="t10", now=T_LUNCH)
    assert out[0] is ok and out[1] is blocked


def test_missing_reliability_file_means_ramp_active(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "A2_RELIABILITY_FEED_PATH", str(tmp_path / "brak.json"), raising=False)
    dp._NEW_COURIER_DELIV_CACHE.update(mtime=None, data={})
    new = _Cand("555", 60.0, km=1.0)
    dp._v325_new_courier_penalty([new], order_id="t11", now=T_LUNCH)
    assert new.score == 40.0
    assert new.metrics["new_courier_ramp"]["deliveries"] == 0


# ── Serializer LOCATION A+B (lekcja #109) ──

def _ser_cand(cid="555"):
    from types import SimpleNamespace
    return SimpleNamespace(
        courier_id=cid, name="Nowy T", score=40.0, plan=None,
        feasibility_verdict="MAYBE", feasibility_reason="ok", best_effort=False,
        metrics={
            "new_courier_ramp": {"active": True, "eligible": True,
                                 "deliveries": 3, "malus": -20.0},
            "v325_new_courier_flag": "🆕 NOWY KURIER (rampa 3/30) — krótki kurs",
        },
    )


def test_serializer_location_a_new_courier_ramp():
    out = shadow_dispatcher._serialize_candidate(_ser_cand())
    assert out["new_courier_ramp"]["eligible"] is True
    assert out["new_courier_ramp"]["deliveries"] == 3


def test_serializer_location_b_best_new_courier_ramp():
    from types import SimpleNamespace
    best = _ser_cand("555")
    result = SimpleNamespace(
        order_id="472001", restaurant="R", delivery_address="A",
        verdict="PROPOSE", reason="ok", best=best, candidates=[best],
        pickup_ready_at=datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc),
    )
    out = shadow_dispatcher._serialize_result(result, event_id="ev", latency_ms=1.0)
    assert out["best"]["new_courier_ramp"]["active"] is True
    assert out["best"]["new_courier_ramp"]["malus"] == -20.0
