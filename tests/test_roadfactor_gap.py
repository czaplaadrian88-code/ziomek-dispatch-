#!/usr/bin/env python3
"""Testy harnessu ROADFACTOR-GAP (tools/roadfactor_gap.py).

Pokrywa:
  (1) filtr fizyczności — odrzuca nie-fizyczne predicted/real (np. 20013 min sentinel),
      zachowuje normalne; max_plausible_min=None wyłącza filtr.
  (2) resid_stats — konwencja znaku (bias>0 = ETA ZANIŻA / real>pred), MAE, P(real>pred)
      na ręcznie policzonym wejściu.
  (3) simulate_k — corrected=pred×k poprawnie skaluje błąd.
  (4) best_k — znajduje prawdziwe optimum na syntetyku ze ZNANYM mnożnikiem prawdy
      (jeśli real = pred × 1.30, k* musi wyjść ~1.30).
  (5) detekcja podwójnego liczenia — gdy dane są wycentrowane @k=1, k=1.42 PODNOSI MAE
      (czyli dodanie ×1.42 = szkodliwe / no-op).

Czysto offline, bez I/O do prawdziwych logów (poza 1 testem opt-in na realnym pliku, skip
gdy brak). Python3, żadnych zależności poza stdlib."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import roadfactor_gap as R  # noqa: E402


# ───────────────────────── (1) filtr fizyczności ─────────────────────────
def test_load_matched_drops_nonphysical(tmp_path):
    import json
    p = tmp_path / "eta.jsonl"
    rows = [
        {"matched_courier": True, "predicted_delivery_min": 20.0, "real_delivery_min": 22.0},  # OK
        {"matched_courier": True, "predicted_delivery_min": 20013.0, "real_delivery_min": 18.0},  # śmieć pred
        {"matched_courier": True, "predicted_delivery_min": 15.0, "real_delivery_min": 265.0},  # śmieć real
        {"matched_courier": True, "predicted_delivery_min": 0.0, "real_delivery_min": 10.0},   # pred=0
        {"matched_courier": False, "predicted_delivery_min": 20.0, "real_delivery_min": 22.0}, # nie matched
        {"matched_courier": True, "predicted_delivery_min": 20.0, "real_delivery_min": 22.0,
         "was_czasowka": True},                                                                 # czasówka
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows))
    kept = R.load_matched(str(p))
    assert len(kept) == 1
    assert kept[0]["predicted_delivery_min"] == 20.0
    assert R.load_matched.last_dropped == 3        # 3 odrzucone fizycznością (matched+nie-czasówka)


def test_load_matched_filter_can_be_disabled(tmp_path):
    import json
    p = tmp_path / "eta.jsonl"
    rows = [
        {"matched_courier": True, "predicted_delivery_min": 20013.0, "real_delivery_min": 18.0},
        {"matched_courier": True, "predicted_delivery_min": 20.0, "real_delivery_min": 22.0},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows))
    kept = R.load_matched(str(p), max_plausible_min=None)
    assert len(kept) == 2                          # filtr wyłączony → śmieć zostaje


# ───────────────────────── (2) resid_stats ─────────────────────────
def test_resid_stats_sign_convention():
    # err = real − pred. pred=[10,10,10,10]; real=[12,8,16,10] → err=[+2,-2,+6,0]
    s = R.resid_stats([10, 10, 10, 10], [12, 8, 16, 10])
    assert s["n"] == 4
    assert s["mae"] == pytest.approx((2 + 2 + 6 + 0) / 4)        # 2.5
    assert s["bias"] == pytest.approx((2 - 2 + 6 + 0) / 4)       # +1.5 → ZANIŻA
    # P(real>pred): err>0 → +2,+6 → 2/4
    assert s["frac_under_pred"] == pytest.approx(0.5)
    # rel_bias = 100*sum(err)/sum(real) = 100*6/46
    assert s["rel_bias_pct"] == pytest.approx(100 * 6 / 46)


def test_resid_stats_centered_when_pred_equals_real():
    s = R.resid_stats([15, 20, 25], [15, 20, 25])
    assert s["bias"] == pytest.approx(0.0)
    assert s["mae"] == pytest.approx(0.0)


# ───────────────────────── (3) simulate_k ─────────────────────────
def test_simulate_k_scales_prediction():
    recs = [{"predicted_delivery_min": 10.0, "real_delivery_min": 14.0}]
    # k=1.4 → corrected=14 → err=0
    s = R.simulate_k(recs, 1.4)
    assert s["bias"] == pytest.approx(0.0, abs=1e-9)
    # k=1.0 → err = 14-10 = +4 (zaniża)
    s1 = R.simulate_k(recs, 1.0)
    assert s1["bias"] == pytest.approx(4.0)


# ───────────────────────── (4) best_k znajduje prawdę ─────────────────────────
def test_best_k_recovers_known_multiplier():
    # real = pred × 1.30 dokładnie → k* musi wyjść 1.30 (zerowy błąd)
    import random
    random.seed(3)
    recs = []
    for _ in range(300):
        pred = random.uniform(8, 40)
        recs.append({"predicted_delivery_min": pred, "real_delivery_min": pred * 1.30})
    k, mae, bias = R.best_k(recs)
    assert k == pytest.approx(1.30, abs=0.01)
    assert mae == pytest.approx(0.0, abs=1e-6)


def test_best_k_is_one_when_already_calibrated():
    import random
    random.seed(5)
    # real ≈ pred (szum symetryczny) → k* ≈ 1.0, a NA PEWNO nie 1.42
    recs = []
    for _ in range(400):
        pred = random.uniform(10, 35)
        recs.append({"predicted_delivery_min": pred,
                     "real_delivery_min": pred + random.gauss(0, 3)})
    k, mae, bias = R.best_k(recs)
    assert 0.95 <= k <= 1.05


# ───────────────────────── (5) detekcja podwójnego liczenia ─────────────────────────
def test_double_counting_k142_worse_when_centered():
    import random
    random.seed(9)
    recs = []
    for _ in range(400):
        pred = random.uniform(10, 35)
        recs.append({"predicted_delivery_min": pred,
                     "real_delivery_min": pred + random.gauss(0, 4)})
    mae1 = R.simulate_k(recs, 1.00)["mae"]
    mae142 = R.simulate_k(recs, 1.42)["mae"]
    # dodanie ×1.42 do już-wycentrowanego ETA MUSI pogorszyć MAE (podwójne liczenie)
    assert mae142 > mae1
    # i wepchnąć bias mocno na minus (przeszacowanie)
    assert R.simulate_k(recs, 1.42)["bias"] < -3.0


# ───────────────────────── (opt-in) realny plik ─────────────────────────
@pytest.mark.skipif(not os.path.exists(R.ETA_LOG), reason="brak realnego eta_calibration_log")
def test_real_log_runs_and_drops_outliers():
    res = R.run()
    assert res["n"] > 1000
    assert res["dropped"] >= 1               # log ma znane śmieci (20013 min)
    # po filtrze bias nie może być patologiczny (sanity: |bias| < 30 min)
    assert abs(res["overall"]["bias"]) < 30


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
