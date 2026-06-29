#!/usr/bin/env python3
"""eta_calib_replay_v2 — większa próba + PEWNOŚĆ (k-fold OOS, bootstrap CI, walk-forward,
niezależny cross-check). READ-ONLY, nie dotyka prod-mapy.

Rozszerza v1 (eta_calib_counterfactual_replay.py). v1 testował na 1 splicie (1506
rekordów, małe liczby spóźnień → szum). v2 daje pewność:

  1) K-FOLD OOS (5-fold po DNIU) — KAŻDY z ~5000 rekordów testowany out-of-sample
     (mapa zbudowana bez jego dnia) → pełna próba OOS, nie 30%.
  2) BOOTSTRAP 95% CI — przedział ufności na: odzyskane, nowe spóźnienia,
     netto Δspóźnień i Δbreach-rate. Jeśli CI(Δrate) całe > 0 → regres PEWNY.
  3) WALK-FORWARD (rolling: mapa z dni < d, test na dniu d) — wierne produkcji.
  4) CROSS-CHECK na NIEZALEŻNYM zbiorze backfill_decisions_outcomes_v1.jsonl
     (predicted_r6_max_bag_min → outcome.pickup_to_delivery_min) — inna geneza danych.

R6: reject gdy pred_bag_time > 35. on_time = real ≤ 35. matched = pred dotyczy kuriera,
który dowiózł. Mapa i sloty = kod produkcji (build_buckets / slot_for_hour_warsaw).
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2.tools.eta_quantile_calib import (  # noqa: E402
    build_buckets, slot_for_hour_warsaw, _bin_edges, MAX_MIN, MIN_N,
)
from dispatch_v2.calib_maps import time_slot_warsaw  # noqa: E402

CALIB_LOG = "/root/.openclaw/workspace/dispatch_state/eta_calibration_log.jsonl"
BACKFILL = "/root/.openclaw/workspace/dispatch_state/backfill_decisions_outcomes_v1.jsonl"
HARD = 35.0
SLOT_ALL = "all"
random.seed(20260614)   # deterministyczny bootstrap


# ---------- ładowanie ----------
def load_calib_rows():
    out = []
    for ln in open(CALIB_LOG, encoding="utf-8", errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if not o.get("matched_courier"):
            continue
        try:
            pred = float(o.get("predicted_delivery_min"))
            real = float(o.get("real_delivery_min"))
        except (TypeError, ValueError):
            continue
        if not (0.0 < pred <= MAX_MIN and 0.0 < real <= MAX_MIN):
            continue
        h = o.get("hour_warsaw")
        if h is None:
            continue
        day = (o.get("delivered_at") or o.get("logged_at") or "")[:10]
        if not day:
            continue
        out.append({"pred": pred, "real": real, "slot": slot_for_hour_warsaw(int(h)), "day": day})
    return out


def load_backfill_rows():
    """Niezależny zbiór: predicted_r6_max_bag_min vs outcome.pickup_to_delivery_min, matched."""
    out = []
    for ln in open(BACKFILL, encoding="utf-8", errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        oc = o.get("outcome") or {}
        if oc.get("status") != "delivered":
            continue
        if o.get("proposed_courier_id") is None or oc.get("courier_id_final") is None:
            continue
        if str(o["proposed_courier_id"]) != str(oc["courier_id_final"]):
            continue  # matched only
        try:
            pred = float(o.get("predicted_r6_max_bag_min"))
            real = float(oc.get("pickup_to_delivery_min"))
        except (TypeError, ValueError):
            continue
        if not (0.0 < pred <= MAX_MIN and 0.0 < real <= MAX_MIN):
            continue
        ts = o.get("decision_ts")
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            slot = time_slot_warsaw(dt)
            day = dt.astimezone(timezone.utc).date().isoformat()
        except Exception:
            continue
        out.append({"pred": pred, "real": real, "slot": slot, "day": day})
    return out


# ---------- aplikacja mapy (wierna konsumentowi) ----------
def buckets_index(buckets):
    return {(b["slot"], b["pred_lo"], b["pred_hi"]): b for b in buckets}


def calib_of(bk, pred, slot, q):
    lo, hi = _bin_edges(pred)
    for want in (slot, SLOT_ALL):
        b = bk.get((want, lo, hi))
        if b is not None and b.get(q) is not None:
            return max(0.0, b[q])
    return pred  # identity


def per_record_outcomes(test_rows, buckets, q):
    """Lista per-rekord: (transition, cur_accept, cur_breach, cal_accept, cal_breach)."""
    bk = buckets_index(buckets)
    res = []
    for r in test_rows:
        pred, real, slot = r["pred"], r["real"], r["slot"]
        calib = calib_of(bk, pred, slot, q)
        cur_rej = pred > HARD
        cal_rej = calib > HARD
        on_time = real <= HARD
        trans = "none"
        if cur_rej and not cal_rej:
            trans = "RECOVERED_TP" if on_time else "NEW_FALSE_ACCEPT"
        elif not cur_rej and cal_rej:
            trans = "NEW_WRONG_REJECT" if on_time else "PREVENTED_BREACH"
        res.append((trans,
                    0 if cur_rej else 1, 0 if (cur_rej or on_time) else 1,
                    0 if cal_rej else 1, 0 if (cal_rej or on_time) else 1))
    return res


def summarize(recs):
    t = defaultdict(int)
    cur_acc = cur_brc = cal_acc = cal_brc = 0
    for trans, ca, cb, la, lb in recs:
        t[trans] += 1
        cur_acc += ca; cur_brc += cb; cal_acc += la; cal_brc += lb
    cur_rate = 100 * cur_brc / cur_acc if cur_acc else 0.0
    cal_rate = 100 * cal_brc / cal_acc if cal_acc else 0.0
    return {
        "recovered": t["RECOVERED_TP"], "new_fa": t["NEW_FALSE_ACCEPT"],
        "new_wr": t["NEW_WRONG_REJECT"], "prevented": t["PREVENTED_BREACH"],
        "net_breach": t["NEW_FALSE_ACCEPT"] - t["PREVENTED_BREACH"],
        "cur_rate": cur_rate, "cal_rate": cal_rate, "d_rate": cal_rate - cur_rate,
        "n": len(recs),
    }


# ---------- 1) K-FOLD OOS ----------
def kfold_oos(rows, q, k=5):
    days = sorted({r["day"] for r in rows})
    fold_of = {d: i % k for i, d in enumerate(days)}  # round-robin po dniach
    all_recs = []
    per_fold = []
    for f in range(k):
        train = [r for r in rows if fold_of[r["day"]] != f]
        test = [r for r in rows if fold_of[r["day"]] == f]
        if not test:
            continue
        bks = build_buckets([(r["pred"], r["real"], r["slot"]) for r in train])
        recs = per_record_outcomes(test, bks, q)
        all_recs.extend(recs)
        per_fold.append(summarize(recs))
    return summarize(all_recs), per_fold, all_recs


# ---------- 2) BOOTSTRAP CI na pooled OOS records ----------
def bootstrap_ci(recs, B=2000):
    n = len(recs)
    idx = list(range(n))
    rec_arr = recs
    rcv = []; fa = []; net = []; drate = []
    for _ in range(B):
        sample = [rec_arr[random.choice(idx)] for _ in range(n)]
        s = summarize(sample)
        rcv.append(s["recovered"]); fa.append(s["new_fa"])
        net.append(s["net_breach"]); drate.append(s["d_rate"])

    def ci(v):
        v = sorted(v)
        return v[int(0.025 * B)], v[int(0.975 * B)]
    return {"recovered": ci(rcv), "new_fa": ci(fa), "net_breach": ci(net), "d_rate": ci(drate)}


# ---------- 3) WALK-FORWARD ----------
def walk_forward(rows, q, min_train_days=14):
    days = sorted({r["day"] for r in rows})
    all_recs = []
    for i, d in enumerate(days):
        if i < min_train_days:
            continue
        train = [r for r in rows if r["day"] < d]
        test = [r for r in rows if r["day"] == d]
        if not test:
            continue
        bks = build_buckets([(r["pred"], r["real"], r["slot"]) for r in train])
        all_recs.extend(per_record_outcomes(test, bks, q))
    return summarize(all_recs)


def pr(label, s):
    bal = f"{s['recovered']}:{s['new_fa']}" + (
        f" = {s['recovered']/max(s['new_fa'],1):.0f}:1" if s['new_fa'] else " (0 nowych spóźnień)")
    print(f"  {label:<22} N={s['n']:<5} odzysk +{s['recovered']:<4} "
          f"nowe_spóźn -{s['new_fa']:<4} netΔbreach {s['net_breach']:<+6d} "
          f"rate {s['cur_rate']:.1f}%→{s['cal_rate']:.1f}% (Δ{s['d_rate']:+.2f}pp)  bilans {bal}")


def main():
    rows = load_calib_rows()
    days = sorted({r["day"] for r in rows})
    print("=" * 92)
    print("REPLAY v2 — WIĘKSZA PRÓBA + PEWNOŚĆ (k-fold OOS / bootstrap CI / walk-forward / cross-check)")
    print("=" * 92)
    print(f"Zbiór główny (eta_calibration_log, matched): N={len(rows)}  dni={len(days)} "
          f"({days[0]}..{days[-1]})  MIN_N={MIN_N}")

    for q in ("p50", "p80"):
        print(f"\n{'#'*92}\n# KWANTYL {q.upper()}\n{'#'*92}")
        agg, per_fold, pooled = kfold_oos(rows, q, k=5)
        print(f"\n[1] K-FOLD OOS (5-fold po dniu — KAŻDY rekord testowany OOS, pełna próba):")
        pr("k-fold pooled", agg)
        print(f"      per-fold odzysk: {[f['recovered'] for f in per_fold]}  "
              f"nowe_spóźn: {[f['new_fa'] for f in per_fold]}  "
              f"Δrate: {[round(f['d_rate'],2) for f in per_fold]}")
        ci = bootstrap_ci(pooled, B=2000)
        print(f"\n[2] BOOTSTRAP 95% CI (na {agg['n']} rekordach OOS, B=2000):")
        print(f"      odzyskane dobre   : {agg['recovered']}  CI[{ci['recovered'][0]}, {ci['recovered'][1]}]")
        print(f"      NOWE spóźnienia   : {agg['new_fa']}  CI[{ci['new_fa'][0]}, {ci['new_fa'][1]}]")
        print(f"      netto Δspóźnień   : {agg['net_breach']:+d}  CI[{ci['net_breach'][0]:+d}, {ci['net_breach'][1]:+d}]")
        verdict = ("REGRES PEWNY (CI całe >0)" if ci['d_rate'][0] > 0 else
                   "BRAK REGRESU (CI całe ≤0)" if ci['d_rate'][1] <= 0 else
                   "NIEROZSTRZYGNIĘTE (CI obejmuje 0)")
        print(f"      Δbreach-rate (pp) : {agg['d_rate']:+.2f}  CI[{ci['d_rate'][0]:+.2f}, {ci['d_rate'][1]:+.2f}]  → {verdict}")
        print(f"\n[3] WALK-FORWARD (mapa z dni<d, test dnia d — wierne produkcji):")
        pr("walk-forward", walk_forward(rows, q, min_train_days=14))

    # 4) cross-check niezależny
    bf = load_backfill_rows()
    print(f"\n{'#'*92}\n# [4] CROSS-CHECK — NIEZALEŻNY zbiór backfill_decisions_outcomes (N={len(bf)})\n{'#'*92}")
    if len(bf) >= MIN_N:
        bfdays = sorted({r['day'] for r in bf})
        print(f"    dni={len(bfdays)} ({bfdays[0]}..{bfdays[-1]})")
        for q in ("p50", "p80"):
            agg, _, _ = kfold_oos(bf, q, k=5)
            pr(f"backfill {q}", agg)
    else:
        print("    za mało rekordów do sensownego foldu")

    print("\n" + "=" * 92)
    print("JAK CZYTAĆ: 'Δbreach-rate CI' to werdykt o regresie. Jeśli całe CI > 0 → kalibracja")
    print("PEWNIE podnosi odsetek spóźnień wśród zaakceptowanych (regres jakości, mimo odzysku).")
    print("p80 powinno mieć Δrate bliższe 0 niż p50. Cross-check potwierdza kierunek na innych danych.")
    print("=" * 92)


if __name__ == "__main__":
    main()
