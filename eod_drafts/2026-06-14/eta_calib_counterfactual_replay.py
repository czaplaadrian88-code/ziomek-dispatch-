#!/usr/bin/env python3
"""eta_calib_counterfactual_replay — czy włączenie kalibracji ETA da PROGRES bez REGRESU?

READ-ONLY. Nie dotyka produkcji: czyta eta_calibration_log.jsonl, mapę buduje
w pamięci/temp, NIE zapisuje do eta_quantile_map.json (prod).

Pytanie biznesowe (raport SYNTEZA §4): bramka R6 (pred_delivery_min > 35 → REJECT)
jest pesymistyczna i odrzuca dobrych kurierów. Kalibracja kwantylowa odejmuje bias.
Ale luźniejsza ETA = R6 przepuszcza WIĘCEJ → ryzyko nowych spóźnień. Ten skrypt
mierzy DOKŁADNIE: ile zleceń odzyskujemy (progres) vs ile nowych spóźnień
wpuszczamy (regres) — out-of-sample, więc liczby są uczciwe.

Wierność produkcji:
  - mapa budowana TYM SAMYM kodem co cron (eta_quantile_calib.build_buckets);
  - slot z TEJ SAMEJ funkcji (slot_for_hour_warsaw), biny z _bin_edges;
  - aplikacja mapy = wierna kopia konsumenta calib_maps.eta_quantile_calibrate
    (fallback slot→'all', identity gdy brak koszyka, clamp ≥0).

R6 (feasibility_v2.py:838+): hard reject gdy bag_time_min > BAG_TIME_HARD_MAX_MIN (35).
W logu: predicted_delivery_min = pred bag-time, real_delivery_min = faktyczny,
sla_ok = real ≤ 35. Para matched_courier=True = pred dotyczy kuriera, który dowiózł.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2.tools.eta_quantile_calib import (  # noqa: E402
    build_buckets, slot_for_hour_warsaw, _bin_edges, MAX_MIN, MIN_N,
)

LOG = "/root/.openclaw/workspace/dispatch_state/eta_calibration_log.jsonl"
HARD = 35.0          # BAG_TIME_HARD_MAX_MIN (R6)
TEST_FRAC = 0.30     # ostatnie 30% dni = zbiór testowy (out-of-sample)
SLOT_ALL = "all"


def load_rows():
    """matched_courier=True, pred&real w (0,MAX_MIN], z hour + datą dostawy."""
    out = []
    for ln in open(LOG, encoding="utf-8", errors="replace"):
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
        out.append({"pred": pred, "real": real, "slot": slot_for_hour_warsaw(int(h)),
                    "day": day, "oid": o.get("oid")})
    return out


def apply_calib(buckets_by_key, pred, slot, quantile):
    """Wierna kopia calib_maps.eta_quantile_calibrate: slot→'all' fallback, identity gdy brak."""
    lo, hi = _bin_edges(pred)
    for want in (slot, SLOT_ALL):
        b = buckets_by_key.get((want, lo, hi))
        if b is not None:
            v = b.get(quantile)
            if v is not None:
                return max(0.0, v)
    return None  # brak koszyka → konsument używa surowej pred (identity)


def classify(rows, buckets, quantile):
    """Macierz przejść R6: surowa pesymistyczna ETA vs skalibrowana."""
    bk = {(b["slot"], b["pred_lo"], b["pred_hi"]): b for b in buckets}
    c = defaultdict(int)
    cur_accept_breach = cur_accept = cal_accept_breach = cal_accept = 0
    for r in rows:
        pred, real, slot = r["pred"], r["real"], r["slot"]
        calib = apply_calib(bk, pred, slot, quantile)
        if calib is None:
            calib = pred
        cur_reject = pred > HARD
        cal_reject = calib > HARD
        on_time = real <= HARD
        # accept-set breach rate (jakość decyzji wśród zaakceptowanych)
        if not cur_reject:
            cur_accept += 1
            cur_accept_breach += 0 if on_time else 1
        if not cal_reject:
            cal_accept += 1
            cal_accept_breach += 0 if on_time else 1
        # przejścia
        if cur_reject and not cal_reject:        # POLUZOWANE (odrzut→akcept)
            c["RECOVERED_TP" if on_time else "NEW_FALSE_ACCEPT"] += 1
        elif not cur_reject and cal_reject:      # ZAOSTRZONE (akcept→odrzut)
            c["NEW_WRONG_REJECT" if on_time else "PREVENTED_BREACH"] += 1
        # else: bez zmiany
        if cur_reject and cal_reject and on_time:
            c["RESIDUAL_WRONG_REJECT"] += 1       # nadal źle odrzucone po kalibracji
    c["cur_accept"], c["cur_accept_breach"] = cur_accept, cur_accept_breach
    c["cal_accept"], c["cal_accept_breach"] = cal_accept, cal_accept_breach
    return c


def report(title, train, test, q):
    buckets = build_buckets([(r["pred"], r["real"], r["slot"]) for r in train])
    c = classify(test, buckets, q)
    recov = c["RECOVERED_TP"]
    fa = c["NEW_FALSE_ACCEPT"]
    wr = c["NEW_WRONG_REJECT"]
    pb = c["PREVENTED_BREACH"]
    cur_rate = 100 * c["cur_accept_breach"] / c["cur_accept"] if c["cur_accept"] else 0
    cal_rate = 100 * c["cal_accept_breach"] / c["cal_accept"] if c["cal_accept"] else 0
    net_breach = fa - pb  # +: więcej spóźnień (regres) ; -: mniej (też zysk)
    print(f"\n  ── {title} | kwantyl={q} | buckets={len(buckets)} | "
          f"train={len(train)} test={len(test)} ──")
    print(f"    PROGRES  RECOVERED_TP (odzyskane, dowiozłyby na czas) : +{recov}")
    print(f"    REGRES   NEW_FALSE_ACCEPT (nowo wpuszczone SPÓŹNIENIA): -{fa}")
    print(f"    REGRES   NEW_WRONG_REJECT (nowo źle odrzucone dobre)  : -{wr}")
    print(f"    zysk     PREVENTED_BREACH (słusznie zatrzymane)       : +{pb}")
    print(f"    netto Δspóźnień w zb. zaakceptowanych                 : {net_breach:+d}"
          f"  (+ = regres)")
    print(f"    breach-rate WŚRÓD ZAAKCEPTOWANYCH: surowa {cur_rate:.1f}%  →  "
          f"kalibr {cal_rate:.1f}%   ({'OK ↓/=' if cal_rate <= cur_rate + 1e-9 else 'WZROST ↑ regres'})")
    if recov + fa:
        print(f"    bilans poluzowania: {recov} dobrych : {fa} spóźnień "
              f"= {recov/max(fa,1):.0f}:1" + (" (brak nowych spóźnień)" if fa == 0 else ""))
    return c


def main():
    rows = load_rows()
    days = sorted({r["day"] for r in rows})
    cut = days[int(len(days) * (1 - TEST_FRAC))]
    train = [r for r in rows if r["day"] < cut]
    test = [r for r in rows if r["day"] >= cut]
    print("=" * 78)
    print("REPLAY KONTRFAKTYCZNY KALIBRACJI ETA — progres vs regres (R6 35 min)")
    print("=" * 78)
    print(f"rekordy matched usable: {len(rows)} | dni: {len(days)} "
          f"({days[0]}..{days[-1]}) | MIN_N={MIN_N}")
    # baseline na zbiorze testowym
    rej = sum(1 for r in test if r["pred"] > HARD)
    rej_ot = sum(1 for r in test if r["pred"] > HARD and r["real"] <= HARD)
    print(f"\nBASELINE (test, surowa ETA): R6 odrzuca {rej}; z tego dowiozłoby na czas "
          f"{rej_ot} ({100*rej_ot/rej:.1f}% błędnych odrzutów)" if rej else "brak odrzutów")

    print("\n" + "#" * 78)
    print("# OUT-OF-SAMPLE (mapa z DAWNYCH dni, test na NOWYCH) — liczby uczciwe")
    print("#" * 78)
    print(f"  cutoff dnia testowego: {cut}")
    for q in ("p50", "p80"):
        report("OUT-OF-SAMPLE", train, test, q)

    print("\n" + "#" * 78)
    print("# IN-SAMPLE (mapa i test z tych samych danych) — dla porównania (optymistyczne)")
    print("#" * 78)
    for q in ("p50", "p80"):
        report("IN-SAMPLE", rows, rows, q)

    print("\n" + "=" * 78)
    print("INTERPRETACJA:")
    print("  • p50 = agresywne (median real) — max odzysk, większe ryzyko nowych spóźnień")
    print("  • p80 = konserwatywne — mniejszy odzysk, ale chroni przed regresem")
    print("  • 'breach-rate wśród zaakceptowanych' nie rośnie  ⇒  brak regresu jakości")
    print("    (więcej przepustowości przy nie-gorszym odsetku spóźnień).")
    print("  • net Δspóźnień to LICZBA do decyzji o flipie. Cel: ≤ 0 lub mały vs odzysk.")
    print("=" * 78)


if __name__ == "__main__":
    main()
