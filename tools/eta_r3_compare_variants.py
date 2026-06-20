#!/usr/bin/env python3
"""ETA R3 — porównanie wariantów po naprawie skew pool_feasible (offline, 2026-06-20).

Re-walidacja forward na NOWYCH danych (serve 18-20.06, held-out > TRAIN_MAX) czterech serii:
  • baza      — predicted_delivery_min (OSRM, bez korekty)
  • obecny(v1)— eta_residual_v1 (PRODUKCYJNY, ZE skew pool_feasible) — wartość z LOGU
  • A_retrain — eta_residual_v2_retrain (okno backfill-era, realny pool, 9 cech) — RECOMPUTE
  • B_drop    — eta_residual_v2_drop (pełne dane, 8 cech bez pool) — RECOMPUTE

Dla A/B liczymy corrected = base + model.predict(features) (log ma tylko v1). Cechy
rekonstruowane LUSTREM treningu (tools/eta_r3_fix_skew.feats) per wariant (9 vs 8 cech).
Metryki (MAE, mediana|e|, p90, p95, błąd-ze-znakiem P(err<0)) + okna + KS-parity — z
zwalidowanego harnessu C5 (import eta_r3_forward_val). KS-parity dla B pomija pool_feasible
(cecha usunięta), dla A liczona na oknie treningowym backfill-era vs serve.

NIE trenuje (modele już zbudowane przez eta_r3_fix_skew.py), NIE dotyka produkcji, fail-soft."""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import eta_r3_forward_val as H          # zwalidowany harness C5
import eta_r3_fix_skew as FIX           # feats() + loadery treningowe

MODELS = "/root/.openclaw/workspace/scripts/ml_data_prep/models"
TRAIN_MAX = FIX.TRAIN_MAX
BACKFILL_START = FIX.BACKFILL_START
IMPR_TARGET = H.IMPROVEMENT_TARGET_PCT


def _load_booster(model_dir):
    import lightgbm as lgb
    return lgb.Booster(model_file=f"{model_dir}/model.txt"), \
        json.load(open(f"{model_dir}/features.json"))


def recompute_corrected(records, model_dir, *, cid2tier, restcnt, pool):
    """corrected = base + booster.predict(feats) per rekord. Zwraca dict oid->corrected.
    drop_pool wnioskowany z liczby cech w features.json (8 → drop)."""
    booster, fn = _load_booster(model_dir)
    drop_pool = ("pool_feasible" not in fn)
    out = {}
    X = []
    keys = []
    for r in records:
        base = H._num(r.get("predicted_delivery_min"))
        if base is None:
            continue
        fv = FIX.feats(r, cid2tier=cid2tier, restcnt=restcnt, pool=pool, drop_pool=drop_pool)
        X.append(fv)
        keys.append((r.get("oid"), base))
    if not X:
        return out
    preds = booster.predict(np.array(X, dtype=float))
    for (oid, base), resid in zip(keys, preds):
        out[oid] = round(base + float(resid), 2)
    return out


def series_stats(serve_usable, corrected_by_oid=None, use_log_r3=False):
    """Statystyki błędu serii na zbiorze usable. corrected_by_oid: dict oid->corrected (A/B);
    use_log_r3: bierz eta_r3_corrected_delivery_min z logu (wariant obecny v1);
    None+False: seria BAZOWA (predicted_delivery_min)."""
    preds, reals = [], []
    for r in serve_usable:
        real = H._num(r.get("real_delivery_min"))
        if real is None:
            continue
        if use_log_r3:
            p = H._num(r.get("eta_r3_corrected_delivery_min"))
        elif corrected_by_oid is not None:
            p = corrected_by_oid.get(r.get("oid"))
        else:
            p = H._num(r.get("predicted_delivery_min"))
        if p is None:
            continue
        preds.append(p)
        reals.append(real)
    return H.error_stats(preds, reals)


def per_window_mae(serve_records, corrected_by_oid, *, use_log_r3=False, min_n=10):
    """MAE per okno kroczące (degraduje do dziennych jak C5) dla danej serii. Zwraca list dict."""
    out = []
    for w in H.rolling_windows(serve_records, window_days=7):
        recs = [r for r in w["records"]
                if H._valid_for_mae(r) and H._num(r.get("real_delivery_min")) is not None]
        # seria musi mieć predykcję
        if use_log_r3:
            recs = [r for r in recs if H._num(r.get("eta_r3_corrected_delivery_min")) is not None]
        elif corrected_by_oid is not None:
            recs = [r for r in recs if corrected_by_oid.get(r.get("oid")) is not None]
        if len(recs) < min_n:
            out.append({"start": w["start"], "end": w["end"], "daily": w.get("daily", False),
                        "n": len(recs), "insufficient": True})
            continue
        s = series_stats(recs, corrected_by_oid=corrected_by_oid, use_log_r3=use_log_r3)
        sb = H.error_stats([H._num(r.get("predicted_delivery_min")) for r in recs],
                           [H._num(r.get("real_delivery_min")) for r in recs])
        impr = 100 * (sb["mae"] - s["mae"]) / sb["mae"] if sb["mae"] > 0 else 0.0
        out.append({"start": w["start"], "end": w["end"], "daily": w.get("daily", False),
                    "n": len(recs), "insufficient": False, "mae": s["mae"],
                    "base_mae": sb["mae"], "impr": impr, "p95": s["p95_abs"],
                    "frac_under": s["frac_under"], "meets": impr >= IMPR_TARGET})
    return out


def ks_parity_for(fn_list, train_records, serve_records, *, cid2tier, restcnt, pool):
    """KS-parity per cecha z fn_list (one-hot tier_ord). Rekonstrukcja cech jak w treningu
    danego wariantu (8 lub 9). Zwraca list dict jak H._parity_row."""
    drop_pool = ("pool_feasible" not in fn_list)

    def cols(recs):
        c = {f: [] for f in fn_list}
        for r in recs:
            fv = FIX.feats(r, cid2tier=cid2tier, restcnt=restcnt, pool=pool, drop_pool=drop_pool)
            # FIX.feats zwraca w kolejności FN_FULL/FN_DROP — zgodnej z fn_list
            for f, v in zip(fn_list, fv):
                c[f].append(v)
        return c
    tc, sc = cols(train_records), cols(serve_records)
    rows = []
    for f in fn_list:
        if f == "tier_ord":
            for lvl in sorted(FIX.TIER_ORD.values()):
                ta = [1.0 if v == lvl else 0.0 for v in tc[f]]
                sa = [1.0 if v == lvl else 0.0 for v in sc[f]]
                D, p = H.ks_2samp(ta, sa)
                rows.append(H._parity_row("tier_ord==%d" % lvl, ta, sa, D, p))
        else:
            D, p = H.ks_2samp(tc[f], sc[f])
            rows.append(H._parity_row(f, tc[f], sc[f], D, p))
    return rows


def run():
    cid2tier, pool, ETA, restcnt = FIX.load_inputs()
    serve = H.load_serve_records()
    serve_usable = [r for r in serve if H._valid_for_mae(r)
                    and H._num(r.get("eta_r3_corrected_delivery_min")) is not None]
    # zbiory treningowe do parity
    tr_full = [r for r in ETA if FIX.valid(r) and (r.get("logged_at") or "")[:10] <= TRAIN_MAX]
    tr_era = [r for r in tr_full if (r.get("logged_at") or "")[:10] >= BACKFILL_START]

    corrA = recompute_corrected(serve_usable, f"{MODELS}/eta_residual_v2_retrain",
                                cid2tier=cid2tier, restcnt=restcnt, pool=pool)
    corrB = recompute_corrected(serve_usable, f"{MODELS}/eta_residual_v2_drop",
                                cid2tier=cid2tier, restcnt=restcnt, pool=pool)

    res = {
        "n_usable": len(serve_usable),
        "overall": {
            "base": series_stats(serve_usable),
            "v1": series_stats(serve_usable, use_log_r3=True),
            "A": series_stats(serve_usable, corrected_by_oid=corrA),
            "B": series_stats(serve_usable, corrected_by_oid=corrB),
        },
        "windows": {
            "v1": per_window_mae(serve, None, use_log_r3=True),
            "A": per_window_mae(serve, corrA),
            "B": per_window_mae(serve, corrB),
        },
        "parity": {
            "v1_FULL": ks_parity_for(FIX.FN_FULL, tr_full, serve,
                                     cid2tier=cid2tier, restcnt=restcnt, pool=pool),
            "A_era": ks_parity_for(FIX.FN_FULL, tr_era, serve,
                                   cid2tier=cid2tier, restcnt=restcnt, pool=pool),
            "B_drop": ks_parity_for(FIX.FN_DROP, tr_full, serve,
                                    cid2tier=cid2tier, restcnt=restcnt, pool=pool),
        },
    }
    return res


def _f(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


def print_report(res):
    print("=" * 80)
    print("ETA R3 — RE-WALIDACJA po naprawie skew pool_feasible | serve 18-20.06 held-out")
    print("=" * 80)
    print(f"usable (base+R3+real, nie-czasówka): {res['n_usable']}")
    ov = res["overall"]
    base = ov["base"]
    print("-" * 80)
    print("MAE CAŁOŚĆ (serie vs baza OSRM):")
    print(f"  {'seria':12s} {'n':>4s} {'MAE':>7s} {'popr%':>7s} {'med|e|':>7s} {'p90':>7s} {'p95':>7s} {'P(err<0)':>9s}")
    for key, lab in [("base", "baza"), ("v1", "obecny v1"), ("A", "A_retrain"), ("B", "B_drop")]:
        s = ov[key]
        impr = 100 * (base["mae"] - s["mae"]) / base["mae"] if base["mae"] > 0 else 0.0
        print(f"  {lab:12s} {s['n']:4d} {s['mae']:7.2f} {impr:+7.1f} {_f(s['median_abs']):>7s} "
              f"{_f(s['p90_abs']):>7s} {_f(s['p95_abs']):>7s} {s['frac_under']:9.2f}")
    print("-" * 80)
    print("MAE PER OKNO (degradacja do dziennych — forward ~1,5 dnia; ✅ = ≥%g%%):" % IMPR_TARGET)
    for key, lab in [("v1", "obecny v1"), ("A", "A_retrain"), ("B", "B_drop")]:
        print(f"  [{lab}]")
        for w in res["windows"][key]:
            tag = "dzień" if w.get("daily") else "okno "
            if w.get("insufficient"):
                print(f"    {tag} {w['start']}..{w['end']} n={w['n']:<4d} — za mało")
                continue
            star = "✅" if w["meets"] else "❌"
            print(f"    {tag} {w['start']}..{w['end']} n={w['n']:<4d} "
                  f"MAE {w['base_mae']:5.2f}→{w['mae']:5.2f} ({w['impr']:+5.1f}% {star}) "
                  f"p95={_f(w['p95'])} P(err<0)={w['frac_under']:.2f}")
    print("-" * 80)
    print("KS-PARITY pool_feasible (czy skew zniknął):")
    for key, lab in [("v1_FULL", "obecny v1 (FULL train)"), ("A_era", "A (okno backfill-era)"),
                     ("B_drop", "B (drop — cecha usunięta)")]:
        rows = {r["feature"]: r for r in res["parity"][key]}
        if "pool_feasible" in rows:
            r = rows["pool_feasible"]
            flag = "⚠ SKEW" if r["skew"] else "OK (brak skew)"
            print(f"  {lab:28s} pool_feasible D={_f(r['D'],3)} p={_f(r['p'],4)} "
                  f"train μ={_f(r['train_mean'])} serve μ={_f(r['serve_mean'])} → {flag}")
        else:
            print(f"  {lab:28s} pool_feasible — USUNIĘTA z modelu (skew nie dotyczy)")
    print("-" * 80)
    print("Pozostałe cechy ze skew per wariant (p<%g):" % H.KS_ALPHA)
    for key, lab in [("v1_FULL", "v1"), ("A_era", "A"), ("B_drop", "B")]:
        sk = [r["feature"] for r in res["parity"][key] if r["skew"]]
        print(f"  {lab}: {', '.join(sk) if sk else 'brak'}")
    print("=" * 80)
    return res


if __name__ == "__main__":
    print_report(run())
