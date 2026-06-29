#!/usr/bin/env python3
"""eta_calib_replay_v3 — rozbicie progres/regres PER SLOT i PER OBCIĄŻENIE FLOTY.

READ-ONLY. Cel: znaleźć komórki (slot × obciążenie), gdzie kalibracja p80 daje
ODZYSK przy ~ZERO nowych spóźnień (= bezpieczne do warunkowego włączenia), i te,
gdzie podnosi spóźnienia (= wykluczyć). Buduje na v1/v2.

Metoda: 1 przebieg k-fold OOS (5-fold po dniu) — każdy rekord testowany OOS mapą
zbudowaną bez jego dnia; każdy rekord otagowany slotem i koszykiem obciążenia;
potem agregacja po grupach. Bootstrap CI na Δbreach-rate per slot i per bag-load.

Sygnały obciążenia:
  • bag_size (calib_log, 100% pokrycia, N≈5000) — ile zleceń wiezie kurier = realny
    sterownik R6 (termika worka). Główny cut.
  • pool_feasible (backfill, niezależny zbiór) — dostępna pojemność floty przy decyzji.
    Cross-check fleet-level.

R6: reject gdy pred_bag_time>35. on_time=real≤35. Mapa+sloty=kod produkcji.
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
SLOTS = ["peak_lunch", "high_risk", "peak_dinner", "off"]
random.seed(20260614)


def bag_bin(bs):
    try:
        bs = int(bs)
    except (TypeError, ValueError):
        return "?"
    return "1" if bs <= 1 else "2" if bs == 2 else "3" if bs == 3 else "4" if bs == 4 else "5+"


def load_calib():
    out = []
    for ln in open(CALIB_LOG, encoding="utf-8", errors="replace"):
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if not o.get("matched_courier"):
            continue
        try:
            pred = float(o["predicted_delivery_min"]); real = float(o["real_delivery_min"])
        except (TypeError, ValueError, KeyError):
            continue
        if not (0 < pred <= MAX_MIN and 0 < real <= MAX_MIN) or o.get("hour_warsaw") is None:
            continue
        day = (o.get("delivered_at") or o.get("logged_at") or "")[:10]
        if not day:
            continue
        out.append({"pred": pred, "real": real, "slot": slot_for_hour_warsaw(int(o["hour_warsaw"])),
                    "day": day, "bag": bag_bin(o.get("bag_size"))})
    return out


def calib_of(bk, pred, slot, q):
    lo, hi = _bin_edges(pred)
    for want in (slot, SLOT_ALL):
        b = bk.get((want, lo, hi))
        if b is not None and b.get(q) is not None:
            return max(0.0, b[q])
    return pred


def kfold_tagged(rows, q, k=5):
    """Zwraca pooled listę: (transition, cur_acc, cur_brc, cal_acc, cal_brc, slot, bag)."""
    days = sorted({r["day"] for r in rows})
    fold = {d: i % k for i, d in enumerate(days)}
    pooled = []
    for f in range(k):
        train = [r for r in rows if fold[r["day"]] != f]
        test = [r for r in rows if fold[r["day"]] == f]
        bk = {(b["slot"], b["pred_lo"], b["pred_hi"]): b
              for b in build_buckets([(r["pred"], r["real"], r["slot"]) for r in train])}
        for r in test:
            pred, real = r["pred"], r["real"]
            calib = calib_of(bk, pred, r["slot"], q)
            cr, lr, ot = pred > HARD, calib > HARD, real <= HARD
            tr = "none"
            if cr and not lr:
                tr = "RECOVERED_TP" if ot else "NEW_FALSE_ACCEPT"
            elif not cr and lr:
                tr = "NEW_WRONG_REJECT" if ot else "PREVENTED_BREACH"
            pooled.append((tr, 0 if cr else 1, 0 if (cr or ot) else 1,
                           0 if lr else 1, 0 if (lr or ot) else 1, r["slot"], r["bag"]))
    return pooled


def summ(recs):
    t = defaultdict(int); ca = cb = la = lb = 0
    for tr, a, b, c, d, *_ in recs:
        t[tr] += 1; ca += a; cb += b; la += c; lb += d
    cur = 100 * cb / ca if ca else 0.0
    cal = 100 * lb / la if la else 0.0
    return dict(n=len(recs), rec=t["RECOVERED_TP"], fa=t["NEW_FALSE_ACCEPT"],
                wr=t["NEW_WRONG_REJECT"], pb=t["PREVENTED_BREACH"],
                cur=cur, cal=cal, d=cal - cur)


def ci_drate(recs, B=1500):
    if not recs:
        return (0.0, 0.0)
    n = len(recs); idx = list(range(n)); vals = []
    for _ in range(B):
        s = summ([recs[random.choice(idx)] for _ in range(n)])
        vals.append(s["d"])
    vals.sort()
    return vals[int(0.025 * B)], vals[int(0.975 * B)]


def line(label, s, ci=None):
    tag = ""
    if ci is not None:
        tag = ("  ✅SAFE(CI≤0)" if ci[1] <= 0 else
               "  ⛔RISK(CI>0)" if ci[0] > 0 else "  ⚠nieroz.(CI∋0)")
        tag += f" CI[{ci[0]:+.2f},{ci[1]:+.2f}]"
    lown = " ⟨małe N⟩" if s["n"] < 120 else ""
    bal = f"{s['rec']}:{s['fa']}" + ("→0 spóźn" if s["fa"] == 0 else "")
    print(f"  {label:<16} N={s['n']:<5} odzysk+{s['rec']:<4} spóźn-{s['fa']:<4} "
          f"Δrate {s['d']:+.2f}pp ({s['cur']:.1f}→{s['cal']:.1f})  bil {bal}{tag}{lown}")


def group(pooled, key_idx):
    g = defaultdict(list)
    for r in pooled:
        g[r[key_idx]].append(r)
    return g


def main():
    rows = load_calib()
    print("=" * 96)
    print("REPLAY v3 — PER SLOT × OBCIĄŻENIE FLOTY (gdzie p80 bezpieczne, gdzie regres)")
    print("=" * 96)
    print(f"calib_log matched: N={len(rows)}  dni={len(sorted({r['day'] for r in rows}))}")

    for q in ("p80", "p50"):
        pooled = kfold_tagged(rows, q)
        print(f"\n{'#'*96}\n# KWANTYL {q.upper()}\n{'#'*96}")
        print(f"\n[A] PER SLOT (k-fold OOS, bootstrap CI na Δbreach-rate):")
        gs = group(pooled, 5)
        for sl in SLOTS:
            if sl in gs:
                line(sl, summ(gs[sl]), ci_drate(gs[sl]))
        print(f"\n[B] PER OBCIĄŻENIE — bag_size (ile zleceń wiezie kurier):")
        gb = group(pooled, 6)
        for bb in ["1", "2", "3", "4", "5+"]:
            if bb in gb:
                line(f"bag={bb}", summ(gb[bb]), ci_drate(gb[bb]))
        print(f"\n[C] GRID slot × bag (Δrate pp | odzysk/spóźn | N) — ✅=0 spóźn & odzysk>0:")
        cells = defaultdict(list)
        for r in pooled:
            cells[(r[5], r[6])].append(r)
        hdr = "  slot\\bag      " + "".join(f"{b:>16}" for b in ["1", "2", "3", "4", "5+"])
        print(hdr)
        for sl in SLOTS:
            cellsfmt = []
            for bb in ["1", "2", "3", "4", "5+"]:
                c = cells.get((sl, bb))
                if not c:
                    cellsfmt.append(f"{'—':>16}"); continue
                s = summ(c)
                mark = "✅" if (s["fa"] == 0 and s["rec"] > 0) else ("⛔" if s["d"] > 0.3 else "·")
                cellsfmt.append(f"{mark}{s['d']:+.1f}|{s['rec']}/{s['fa']}|{s['n']:>3}".rjust(16))
            print(f"  {sl:<13}" + "".join(cellsfmt))

    # independent fleet-load cross-check (backfill, pool_feasible)
    print(f"\n{'#'*96}\n# [D] CROSS-CHECK FLEET-LOAD — backfill pool_feasible (niezależny zbiór, p80)\n{'#'*96}")
    bf = []
    for ln in open(BACKFILL, encoding="utf-8", errors="replace"):
        try:
            o = json.loads(ln)
        except Exception:
            continue
        oc = o.get("outcome") or {}
        if oc.get("status") != "delivered":
            continue
        try:
            pred = float(o["predicted_r6_max_bag_min"]); real = float(oc["pickup_to_delivery_min"])
            pf = int(o["pool_feasible"])
        except (TypeError, ValueError, KeyError):
            continue
        if not (0 < pred <= MAX_MIN and 0 < real <= MAX_MIN):
            continue
        try:
            dt = datetime.fromisoformat(str(o["decision_ts"]).replace("Z", "+00:00"))
            slot = time_slot_warsaw(dt); day = dt.astimezone(timezone.utc).date().isoformat()
        except Exception:
            continue
        loadbin = "ciasno(≤2)" if pf <= 2 else "średnio(3-5)" if pf <= 5 else "luźno(≥6)"
        bf.append({"pred": pred, "real": real, "slot": slot, "day": day, "bag": loadbin})
    print(f"    backfill delivered usable (matched+nie): N={len(bf)}  "
          f"(load=dostępni feasible kurierzy przy decyzji)")
    pooled_bf = kfold_tagged(bf, "p80")
    for lb in ["ciasno(≤2)", "średnio(3-5)", "luźno(≥6)"]:
        sub = [r for r in pooled_bf if r[6] == lb]
        if sub:
            line(lb, summ(sub))

    print("\n" + "=" * 96)
    print("DECYZJA: włączać kalibrację p80 TYLKO w komórkach ✅ (odzysk>0, 0 nowych spóźnień,")
    print("CI≤0). Komórki ⛔ (CI>0 / Δrate↑) — wykluczyć (tam regres; zwykle high_risk + duży worek).")
    print("To projekt 'warunkowego flipa' opartego na danych, nie na wierze.")
    print("=" * 96)


if __name__ == "__main__":
    main()
