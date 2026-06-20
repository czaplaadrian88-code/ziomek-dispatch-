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


# ───────────────────────── FORWARD-WINDOWS (pełny held-out, nie tylko R3-logged) ─────────────────────────
# Motywacja: kolumna eta_r3_corrected_delivery_min jest zapisana w logu DOPIERO od 18.06 (flip
# shadow-wiring) → run()/serve_usable widzi tylko ~256 oid (18-20.06). Ale dla wariantów v1 i B
# corrected jest RECOMPUTOWANY z artefaktu (base + booster.predict), więc da się policzyć na
# CAŁYM held-out (>TRAIN_MAX) — wszystkie rekordy z base+real, niezależnie od kolumny R3 w logu.
# To daje uczciwą forward-walidację 7d/14d + WEEKEND zamiast degradacji do ~1,5 dnia.
#
# Tu liczymy okna NA DACIE KOŃCOWEJ (ostatnie N dni held-out) — to jest sens „okno 7d/14d":
# najświeższe N dni jako jeden agregat, plus osobny wycinek weekendu (sob/niedz po dacie).


def _heldout_records(ETA):
    """Wszystkie rekordy held-out (>TRAIN_MAX) z base+real+nie-czasówka. Lustro valid() treningu."""
    return [r for r in ETA if FIX.valid(r) and (r.get("logged_at") or "")[:10] > TRAIN_MAX]


def _slice_last_days(recs, n_days):
    """Wycina rekordy z ostatnich n_days kalendarzowych obecnych w danych (po dacie logu)."""
    days = sorted({(r.get("logged_at") or "")[:10] for r in recs if (r.get("logged_at") or "")[:10]})
    if not days:
        return [], []
    keep = set(days[-n_days:]) if n_days < len(days) else set(days)
    return [r for r in recs if (r.get("logged_at") or "")[:10] in keep], sorted(keep)


def _is_weekend_rec(r):
    """Weekend wg flagi is_weekend zapisanej w logu (sob/niedz w strefie Warszawa)."""
    return bool(r.get("is_weekend"))


def _series_for(recs, *, model_dir, cid2tier, restcnt, pool, use_base=False):
    """Statystyki błędu serii na recs: base (use_base) albo recompute z model_dir."""
    if use_base:
        preds = [H._num(r.get("predicted_delivery_min")) for r in recs]
        reals = [H._num(r.get("real_delivery_min")) for r in recs]
        pr = [(p, g) for p, g in zip(preds, reals) if p is not None and g is not None]
        return H.error_stats([p for p, _ in pr], [g for _, g in pr])
    corr = recompute_corrected(recs, model_dir, cid2tier=cid2tier, restcnt=restcnt, pool=pool)
    preds, reals = [], []
    for r in recs:
        c = corr.get(r.get("oid"))
        g = H._num(r.get("real_delivery_min"))
        if c is not None and g is not None:
            preds.append(c)
            reals.append(g)
    return H.error_stats(preds, reals)


def _window_block(recs, *, cid2tier, restcnt, pool, min_n=10):
    """Dla danego zbioru recs: MAE base / v1(recompute) / B(recompute) + poprawa % vs base."""
    if len(recs) < min_n:
        return {"n": len(recs), "insufficient": True}
    sb = _series_for(recs, model_dir=None, cid2tier=cid2tier, restcnt=restcnt, pool=pool, use_base=True)
    sv1 = _series_for(recs, model_dir=f"{MODELS}/eta_residual_v1",
                      cid2tier=cid2tier, restcnt=restcnt, pool=pool)
    sB = _series_for(recs, model_dir=f"{MODELS}/eta_residual_v2_drop",
                     cid2tier=cid2tier, restcnt=restcnt, pool=pool)

    def impr(s):
        return 100 * (sb["mae"] - s["mae"]) / sb["mae"] if sb and sb["mae"] > 0 else 0.0
    return {
        "n": len(recs), "insufficient": False,
        "base": sb, "v1": sv1, "B": sB,
        "v1_impr": impr(sv1), "B_impr": impr(sB),
        "v1_meets": impr(sv1) >= IMPR_TARGET, "B_meets": impr(sB) >= IMPR_TARGET,
    }


def forward_windows(min_n=10):
    """Forward-walidacja v1 vs B na PEŁNYM held-out: okna 7d, 14d + wycinek WEEKEND.
    Każde okno = ostatnie N dni held-out. v1/B recompute z artefaktów (base+predict)."""
    cid2tier, pool, ETA, restcnt = FIX.load_inputs()
    held = _heldout_records(ETA)
    out = {"n_heldout": len(held), "train_max": TRAIN_MAX,
           "days": sorted({(r.get("logged_at") or "")[:10] for r in held}), "windows": {}}
    for label, n in [("14d", 14), ("7d", 7)]:
        recs, days = _slice_last_days(held, n)
        blk = _window_block(recs, cid2tier=cid2tier, restcnt=restcnt, pool=pool, min_n=min_n)
        blk["days"] = days
        out["windows"][label] = blk
    # WEEKEND — wszystkie weekendowe rekordy w held-out (sob/niedz)
    wk = [r for r in held if _is_weekend_rec(r)]
    blkw = _window_block(wk, cid2tier=cid2tier, restcnt=restcnt, pool=pool, min_n=min_n)
    blkw["days"] = sorted({(r.get("logged_at") or "")[:10] for r in wk})
    out["windows"]["weekend"] = blkw
    return out


def print_forward(res):
    print("=" * 84)
    print("ETA R3 — FORWARD-WALIDACJA v1 vs B_drop | PEŁNY held-out (recompute z artefaktów)")
    print("=" * 84)
    print(f"held-out (>{res['train_max']}, base+real, nie-czasówka): {res['n_heldout']}  "
          f"dni: {res['days'][0]}..{res['days'][-1]} ({len(res['days'])})")
    print("-" * 84)
    print("MAE per OKNO (ostatnie N dni held-out + weekend; ✅ = poprawa ≥%g%% vs baza):" % IMPR_TARGET)
    print(f"  {'okno':9s} {'n':>5s} {'baza':>7s} | {'v1 MAE':>7s} {'v1%':>7s} {'':2s} | "
          f"{'B MAE':>7s} {'B%':>7s} {'':2s} | {'B p95':>7s} {'B P(<0)':>8s}")
    order = [("14d", "14d"), ("7d", "7d"), ("weekend", "WEEKEND")]
    for key, lab in order:
        w = res["windows"][key]
        if w.get("insufficient"):
            print(f"  {lab:9s} {w['n']:5d}  — za mało (min_n)")
            continue
        b, v1, B = w["base"], w["v1"], w["B"]
        v1s = "✅" if w["v1_meets"] else "❌"
        Bs = "✅" if w["B_meets"] else "❌"
        print(f"  {lab:9s} {w['n']:5d} {b['mae']:7.2f} | {v1['mae']:7.2f} {w['v1_impr']:+7.1f} {v1s:2s} | "
              f"{B['mae']:7.2f} {w['B_impr']:+7.1f} {Bs:2s} | {_f(B['p95_abs']):>7s} {B['frac_under']:8.2f}")
    print("-" * 84)
    print("BRAMKA: B_drop musi mieć poprawę ≥%g%% vs baza w KAŻDYM oknie (14d, 7d, weekend)." % IMPR_TARGET)
    gate = all((not res["windows"][k].get("insufficient")) and res["windows"][k]["B_meets"]
               for k, _ in order)
    deltas = []
    for key, lab in order:
        w = res["windows"][key]
        if not w.get("insufficient"):
            deltas.append(f"{lab}: B {w['B_impr']:+.1f}% vs v1 {w['v1_impr']:+.1f}%")
    print("  " + " | ".join(deltas))
    print(f"  WERDYKT BRAMKI: {'GO (≥%g%% w każdym oknie)' % IMPR_TARGET if gate else 'NO-GO (poniżej progu w ≥1 oknie)'}")
    print("=" * 84)
    return res


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="ETA R3 — porównanie wariantów / forward-walidacja")
    ap.add_argument("--forward", action="store_true",
                    help="forward-okna 7d/14d/weekend na PEŁNYM held-out (recompute v1 vs B)")
    ap.add_argument("--min-n", type=int, default=10)
    a = ap.parse_args()
    if a.forward:
        print_forward(forward_windows(min_n=a.min_n))
    else:
        print_report(run())
