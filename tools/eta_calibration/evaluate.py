#!/usr/bin/env python3
"""eta_calibration.evaluate — walidacja czasowa (walk-forward) + baseline'y + istotność.

Baseline'y (zasada nadrzędna 9): obecne ETA Ziomka, obietnica koordynatora (odbiór),
naiwny (mediana kuriera). Metryki z 95% CI (bootstrap): MAE, RMSE, MAPE, bias, %±5/10/15,
pokrycie P75/P90, pinball. Istotność: paired bootstrap na delcie MAE + Wilcoxon na |err|
(z korektą Bonferroniego przy wielu porównaniach). Podział WYŁĄCZNIE po czasie.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from typing import Callable, Dict, List, Optional

import numpy as np
from scipy import stats as sps

from dispatch_v2.tools.eta_calibration import models as M


def load_store(db_path: str) -> List[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM eta_calib_features ORDER BY ts_pickup")]
    con.close()
    return rows


def time_split(rows: List[dict], holdout_days: int):
    """Podział po czasie: holdout = ostatnie N dni wg `day`. Zwraca (train, holdout)."""
    days = sorted({r["day"] for r in rows if r.get("day")})
    if not days:
        return [], [], None
    if len(days) <= holdout_days:
        cut = days[len(days) // 2]
    else:
        cut = days[-holdout_days]
    train = [r for r in rows if r.get("day") and r["day"] < cut]
    hold = [r for r in rows if r.get("day") and r["day"] >= cut]
    return train, hold, cut


# ── metryki ──
def _metrics(errs: List[float], denom: Optional[List[float]] = None) -> dict:
    e = np.array([x for x in errs if x is not None and math.isfinite(x)], dtype=float)
    if len(e) == 0:
        return {"n": 0}
    out = dict(
        n=len(e), bias=round(float(np.mean(e)), 2), mae=round(float(np.mean(np.abs(e))), 2),
        rmse=round(float(np.sqrt(np.mean(e ** 2))), 2), med=round(float(np.median(e)), 2),
        w5=round(100 * float(np.mean(np.abs(e) <= 5)), 1),
        w10=round(100 * float(np.mean(np.abs(e) <= 10)), 1),
        w15=round(100 * float(np.mean(np.abs(e) <= 15)), 1),
    )
    if denom is not None:
        d = np.array(denom, dtype=float)
        mask = d > 1e-6
        if mask.sum() > 0:
            out["mape"] = round(100 * float(np.mean(np.abs(e[mask]) / d[mask])), 1)
    return out


def _boot_ci_mae(errs, n_boot=2000, seed_rows=None):
    e = np.abs(np.array([x for x in errs if x is not None], dtype=float))
    if len(e) < 5:
        return (None, None)
    # deterministyczny bootstrap (indeksy z hasza — bez random state)
    rng = np.random.default_rng(12345)
    stat = [float(np.mean(e[rng.integers(0, len(e), len(e))])) for _ in range(n_boot)]
    return (round(float(np.percentile(stat, 2.5)), 2), round(float(np.percentile(stat, 97.5)), 2))


def _paired_delta_ci(err_a, err_b, n_boot=2000):
    """95% CI delty MAE (a - b); <0 = a lepszy. + Wilcoxon p na |err|."""
    a = np.abs(np.array(err_a, dtype=float))
    b = np.abs(np.array(err_b, dtype=float))
    n = min(len(a), len(b))
    if n < 2:
        return dict(delta_mae=None, ci=(None, None), wilcoxon_p=None)
    a, b = a[:n], b[:n]
    d = a - b
    rng = np.random.default_rng(777)
    stat = [float(np.mean(d[rng.integers(0, n, n)])) for _ in range(n_boot)]
    lo, hi = float(np.percentile(stat, 2.5)), float(np.percentile(stat, 97.5))
    try:
        p = float(sps.wilcoxon(a, b).pvalue)
    except Exception:
        p = float("nan")
    return dict(delta_mae=round(float(np.mean(d)), 2), ci=(round(lo, 2), round(hi, 2)), wilcoxon_p=p)


def fit_models(train: List[dict], leg: str, cfg: dict) -> dict:
    """Dopasuj oba challengery wylacznie na przekazanym train."""
    qs = cfg["model"]["quantiles"]
    chist = M.build_courier_history(train)
    return {
        "L1_empirical": M.EmpiricalQuantileModel(
            leg, qs, cfg["model"]["min_n_courier"],
        ).fit(train),
        "L2_lgbm": M.LGBMQuantileModel(
            leg, qs, cfg["model"]["lgbm"],
        ).fit(train, chist),
    }


def fit_model(train: List[dict], leg: str, cfg: dict, model_name: str):
    """Dopasuj wskazany typ modelu; uzywane do exact-support promotion replay."""
    qs = cfg["model"]["quantiles"]
    if model_name == "L1_empirical":
        return M.EmpiricalQuantileModel(
            leg, qs, cfg["model"]["min_n_courier"],
        ).fit(train)
    if model_name == "L2_lgbm":
        return M.LGBMQuantileModel(
            leg, qs, cfg["model"]["lgbm"],
        ).fit(train, M.build_courier_history(train))
    raise ValueError(f"nieznany model challengera: {model_name!r}")


def coverage(actual: List[float], pred_q: List[float]) -> float:
    """% przypadków actual <= pred_q (pokrycie kwantyla)."""
    a = np.array(actual, dtype=float)
    p = np.array(pred_q, dtype=float)
    return round(100 * float(np.mean(a <= p)), 1)


def pinball(actual, pred, q) -> float:
    a = np.array(actual, dtype=float)
    p = np.array(pred, dtype=float)
    d = a - p
    return round(float(np.mean(np.maximum(q * d, (q - 1) * d))), 3)


# ── główna ewaluacja per noga ──
def _conformal_deltas(model, calib_rows, leg, upper_qs) -> dict:
    """Split-conformal (jednostronny górny): δ_q = kwantyl_q(actual − pred_q) na zbiorze
    KALIBRACJI (rozłącznym z fit). pred_q_conformal = pred_q + δ_q → pokrycie ≈ q."""
    scores = {q: [] for q in upper_qs}
    for r in calib_rows:
        t = r.get("pickup_slip_koord_min") if leg == M.PICKUP else r.get("actual_deliver_min")
        q = model.predict_quantiles(r)
        if t is None or q is None:
            continue
        for uq in upper_qs:
            scores[uq].append(t - q[uq])
    return {uq: (float(np.quantile(v, uq)) if len(v) >= 20 else 0.0)
            for uq, v in scores.items()}


def evaluate_leg(train: List[dict], hold: List[dict], leg: str, cfg: dict) -> dict:
    qs = cfg["model"]["quantiles"]
    opq = cfg["model"]["operational_quantile"]
    # MODEL PUNKTOWY: pełny train (najmocniejsze MAE).
    fitted = fit_models(train, leg, cfg)
    l1 = fitted["L1_empirical"]
    l2 = fitted["L2_lgbm"]
    # DELTA CONFORMAL: osobny model na train_fit, kalibrowany na rozłącznym train_calib
    # (ostatnie N dni train). Delta = systematyczna miskalibracja kwantyla → transfer na
    # model pełny (oba trenowane na ~pokrywających się danych). Split-conformal jednostronny.
    # OBIETNICA OPERACYJNA celowana na REALNY % dotrzymanych (Adrian: 20% spóźnień → 80%).
    # Median-anchored split-conformal: offset = kwantyl_{target_eff}(actual − pred_P50) na
    # rozłącznym oknie kalibracji. target_eff = target_ontime + bufor_driftu[noga].
    l2_cal = None
    op_off = 0.0
    p90_off = 0.0
    t_ontime = cfg["model"].get("target_ontime", opq)
    buf = (cfg["model"].get("drift_buffer_ontime") or {}).get(leg, 0.0)
    target_eff = min(0.98, t_ontime + buf)
    if cfg["model"].get("conformal"):
        days = sorted({r["day"] for r in train if r.get("day")})
        ccd = cfg["window"].get("conformal_calib_days", 7)
        calib_cut = days[-ccd] if len(days) > ccd else days[len(days) // 2]
        train_fit = [r for r in train if r["day"] < calib_cut]
        train_calib = [r for r in train if r["day"] >= calib_cut]
        chist_fit = M.build_courier_history(train_fit)
        l2_cal = M.LGBMQuantileModel(leg, qs, cfg["model"]["lgbm"]).fit(train_fit, chist_fit)
        resid = []
        for r in train_calib:
            t = r.get("pickup_slip_koord_min") if leg == M.PICKUP else r.get("actual_deliver_min")
            q = l2_cal.predict_quantiles(r)
            if t is not None and q is not None:
                resid.append(t - q[0.5])
        if len(resid) >= 20:
            op_off = float(np.quantile(resid, target_eff))
            p90_off = float(np.quantile(resid, min(0.98, 0.90 + buf)))

    # cel + predykcje per rekord holdout
    def target(r):
        return r.get("pickup_slip_koord_min") if leg == M.PICKUP else r.get("actual_deliver_min")

    # baseline'y
    train_slip_by_c = defaultdict(list)
    train_dur_by_c = defaultdict(list)
    for r in train:
        if r.get("pickup_slip_koord_min") is not None:
            train_slip_by_c[r["courier_id"]].append(r["pickup_slip_koord_min"])
        if r.get("actual_deliver_min") is not None:
            train_dur_by_c[r["courier_id"]].append(r["actual_deliver_min"])
    gmed_slip = float(np.median([v for vs in train_slip_by_c.values() for v in vs])) if train_slip_by_c else 0.0
    gmed_dur = float(np.median([v for vs in train_dur_by_c.values() for v in vs])) if train_dur_by_c else 20.0

    res = {"leg": leg, "n_train": len(train), "models": {}, "baselines": {}, "significance": {}}
    err_l1, err_l2 = [], []          # błąd P50 (punkt) modeli
    err_base, err_naive, err_eng = [], [], []
    act_list, p_op_l1, p_op_l2, p90_l2 = [], [], [], []
    p_op_l2_conf, p90_l2_conf = [], []   # conformal-adjusted (pokrycie nominalne)
    denom = []

    for r in hold:
        t = target(r)
        if t is None:
            continue
        q1 = l1.predict_quantiles(r)
        q2 = l2.predict_quantiles(r)
        if q2 is None:  # dostawa bez OSRM — pomiń (spójnie dla wszystkich modeli)
            continue
        act_list.append(t)
        denom.append(abs(t) if leg == M.PICKUP else t)
        # L1 / L2 punkt = P50
        err_l1.append(t - q1[0.5])
        err_l2.append(t - q2[0.5])
        p_op_l1.append(q1[opq]); p_op_l2.append(q2[opq]); p90_l2.append(q2[0.9])
        # OBIETNICA operacyjna = P50(model train_fit) + offset celowany na target_ontime.
        # Punkt/MAE z modelu pełnego (l2); obietnica z conformal (celowane pokrycie).
        qc = l2_cal.predict_quantiles(r) if l2_cal is not None else q2
        if qc is None:
            qc = q2
        p_op_l2_conf.append(qc[0.5] + op_off)
        p90_l2_conf.append(qc[0.5] + p90_off)
        # baseline'y
        if leg == M.PICKUP:
            err_base.append(t - 0.0)                              # obietnica koordynatora (slip=0)
            err_naive.append(t - float(np.median(train_slip_by_c.get(r["courier_id"], [gmed_slip]))))
            ep = r.get("eng_pickup_slip_min")
            err_eng.append(ep if ep is not None else None)       # obecne ETA silnika (rozjazd)
        else:
            err_naive.append(t - float(np.median(train_dur_by_c.get(r["courier_id"], [gmed_dur]))))
            ep = r.get("eng_deliver_pred_min")
            err_eng.append((t - ep) if ep is not None else None) # obecne ETA silnika
            err_base.append(None)

    res["n_holdout"] = len(act_list)
    res["models"]["L1_empirical"] = {**_metrics(err_l1, denom), "ci_mae": _boot_ci_mae(err_l1)}
    res["models"]["L2_lgbm"] = {**_metrics(err_l2, denom), "ci_mae": _boot_ci_mae(err_l2)}
    if leg == M.PICKUP:
        res["baselines"]["koordynator_czas_kuriera"] = _metrics(err_base, denom)
    res["baselines"]["naiwny_mediana_kuriera"] = _metrics(err_naive, denom)
    eng_clean = [x for x in err_eng if x is not None]
    if eng_clean:
        res["baselines"]["obecne_ETA_silnika"] = {**_metrics(eng_clean), "n_cov": len(eng_clean)}

    # POKRYCIE = realny % DOTRZYMANYCH obietnic (on-time). Cel: target_ontime (Adrian 80%).
    ontime_op = coverage(act_list, p_op_l2_conf)
    res["coverage"] = {
        "target_ontime": t_ontime,
        "target_eff_calib": round(target_eff, 3),
        "ONTIME_operacyjna": ontime_op,           # realny % dotrzymanych (cel = target)
        "spoznien_pct": round(100 - ontime_op, 1),
        "ONTIME_gorny_P90": coverage(act_list, p90_l2_conf),
        "obietnica_offset_min": round(op_off, 1),  # o ile obietnica > mediana predykcji
        "raw_P80_L2": coverage(act_list, p_op_l2),
        "pinball_op_L2": pinball(act_list, p_op_l2, opq),
    }

    # istotność: najlepszy model vs każdy baseline (paired) + Bonferroni
    champ_err = err_l2 if res["models"]["L2_lgbm"]["mae"] <= res["models"]["L1_empirical"]["mae"] else err_l1
    champ = "L2_lgbm" if champ_err is err_l2 else "L1_empirical"
    res["champion"] = champ
    comps = {}
    if leg == M.PICKUP:
        comps["vs_koordynator"] = _paired_delta_ci(champ_err, err_base)
    comps["vs_naiwny"] = _paired_delta_ci(champ_err, err_naive)
    # vs silnik: dopasuj po indeksach z eng nie-None
    idx = [i for i, x in enumerate(err_eng) if x is not None]
    if idx:
        ce = [champ_err[i] for i in idx]; ee = [err_eng[i] for i in idx]
        comps["vs_silnik"] = _paired_delta_ci(ce, ee)
    # L2 vs L1
    comps["L2_vs_L1"] = _paired_delta_ci(err_l2, err_l1)
    nboncomp = len(comps)
    for k, v in comps.items():
        v["bonferroni_alpha"] = round(cfg["acceptance"]["significance_alpha"] / max(1, nboncomp), 4)
    res["significance"] = comps
    # Prywatny kanal in-process: nigdy nie jest serializowany do jsonl/terminala.
    # calibrate uzywa go do frozen-support evidence i shadow parity.
    res["_models"] = fitted
    return res


def run(cfg: dict) -> dict:
    rows = load_store(cfg["paths"]["db"])
    train, hold, cut = time_split(rows, cfg["window"]["holdout_days"])
    out = {"holdout_cut_day": cut, "n_total": len(rows), "legs": {}}
    for leg in (M.PICKUP, M.DELIVERY):
        out["legs"][leg] = evaluate_leg(train, hold, leg, cfg)
    return out
