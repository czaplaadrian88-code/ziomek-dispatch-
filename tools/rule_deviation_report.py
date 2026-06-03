#!/usr/bin/env python3
"""rule_deviation_report.py — odchylenia od reguł biznesowych Ziomka z REALNYCH danych.

OFFLINE, READ-ONLY. Dwa kąty:
  REALIZED (backfill_decisions_outcomes) — co faktycznie się stało (ground truth).
  PROPOSED (shadow_decisions best)       — co Ziomek REKOMENDUJE vs reguły.

Reguły (progi z common.py):
  R6  dostawa <= 35 min (BAG_TIME_HARD_MAX_MIN); soft 30-35
  R1  rozrzut dostaw worka <= 8 km (BUNDLE_MAX_DELIV_SPREAD_KM)
  R5  rozrzut odbiorów <= 1.8 km
  R8  pickup span worka: bag2<=15, bag3+<=30 min (PICKUP_SPAN_HARD_*)
  R-DECLARED-TIME / late-pickup: odbiór <= +5 min (LATE_PICKUP_HARD_MAX_MIN)
  ETA: realny czas dostawy vs predykcja (bias)
  R-FLEET-LEVEL: koncentracja floty (top-3 udział)

Output: raport tekstowy + (opcjonalnie --json) dispatch_state/rule_deviation_report.json.
Uruchom: /root/.openclaw/venvs/dispatch/bin/python tools/rule_deviation_report.py
"""
import argparse
import json
import os
import statistics
import sys
from collections import Counter

BACKFILL = "/root/.openclaw/workspace/dispatch_state/backfill_decisions_outcomes_v1.jsonl"
SHADOW_LIVE = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
SHADOW_ROT = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1"
RELIABILITY = "/root/.openclaw/workspace/dispatch_state/courier_reliability.json"
OUT_JSON = "/root/.openclaw/workspace/dispatch_state/rule_deviation_report.json"

R6_HARD = 35.0
R6_SOFT = 30.0
R1_DELIV_SPREAD_KM = 8.0
R5_PICKUP_SPREAD_KM = 1.8
R8_SPAN_BAG2 = 15.0
R8_SPAN_BAG3 = 30.0
LATE_PICKUP_MAX = 5.0


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _pct(a, p):
    if not a:
        return None
    s = sorted(a)
    return s[min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1))))]


def _rate(num, den):
    return round(num / den, 3) if den else None


def _stream(path, limit=None):
    if not os.path.exists(path):
        return
    with open(path, errors="replace") as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def realized_deviations():
    """Z backfillu — co faktycznie się stało (delivered)."""
    deliv = [r for r in _stream(BACKFILL) if (r.get("outcome") or {}).get("status") == "delivered"]
    p2d = [r["outcome"]["pickup_to_delivery_min"] for r in deliv if _num(r["outcome"].get("pickup_to_delivery_min"))]
    resid = [r["outcome"]["pickup_to_delivery_min"] - r["predicted_drive_min"]
             for r in deliv if _num(r["outcome"].get("pickup_to_delivery_min")) and _num(r.get("predicted_drive_min"))]
    a2p = [r["outcome"]["assign_to_pickup_min"] for r in deliv if _num(r["outcome"].get("assign_to_pickup_min"))]
    # fleet concentration: udział top-3 realnych wykonawców vs propozycje Ziomka
    final = Counter(str((r["outcome"] or {}).get("courier_id_final")) for r in deliv if (r["outcome"] or {}).get("courier_id_final"))
    prop = Counter(str(r.get("proposed_courier_id")) for r in deliv if r.get("proposed_courier_id"))
    def top3(c):
        tot = sum(c.values())
        return round(sum(n for _, n in c.most_common(3)) / tot, 3) if tot else None
    out = {
        "n_delivered": len(deliv),
        "R6_breach_rate": _rate(sum(1 for v in p2d if v > R6_HARD), len(p2d)),
        "R6_soft_zone_30_35_rate": _rate(sum(1 for v in p2d if R6_SOFT < v <= R6_HARD), len(p2d)),
        "R6_p2d_median": round(statistics.median(p2d), 1) if p2d else None,
        "R6_p2d_p90": round(_pct(p2d, 90), 1) if p2d else None,
        "R6_p2d_max": round(max(p2d), 1) if p2d else None,
        "ETA_residual_median_min": round(statistics.median(resid), 1) if resid else None,
        "ETA_underpredict_rate": _rate(sum(1 for v in resid if v > 0), len(resid)),
        "assign_to_pickup_median_min": round(statistics.median(a2p), 1) if a2p else None,
        "fleet_top3_share_ZIOMEK_proposed": top3(prop),
        "fleet_top3_share_HUMAN_final": top3(final),
    }
    return out


def proposed_deviations(max_lines):
    """Z shadow_decisions (best = rekomendacja Ziomka) na PROPOSE."""
    n = 0
    bundles = 0
    dev = Counter()
    cnt = Counter()  # mianowniki per reguła
    spans = []
    for path in (SHADOW_LIVE, SHADOW_ROT):
        for d in _stream(path, limit=max_lines):
            if d.get("verdict") != "PROPOSE":
                continue
            b = d.get("best") or {}
            if not b.get("courier_id"):
                continue
            n += 1
            bag = b.get("r6_bag_size") or 0
            is_bundle = (not b.get("r6_is_solo", True)) or (bag and bag >= 2)
            if is_bundle:
                bundles += 1
            # R6 proposed
            r6 = b.get("r6_max_bag_time_min")
            if _num(r6):
                cnt["R6"] += 1
                if r6 > R6_HARD:
                    dev["R6"] += 1
            # R1 deliv spread (worki)
            ds = b.get("deliv_spread_km")
            if is_bundle and _num(ds):
                cnt["R1"] += 1
                if ds > R1_DELIV_SPREAD_KM:
                    dev["R1"] += 1
            # R5 pickup spread (worki)
            ps = b.get("pickup_spread_km")
            if is_bundle and _num(ps):
                cnt["R5"] += 1
                if ps > R5_PICKUP_SPREAD_KM:
                    dev["R5"] += 1
            # R8 pickup span (worki)
            sp = b.get("r8_pickup_span_min")
            if is_bundle and _num(sp):
                cnt["R8"] += 1
                spans.append(sp)
                cap = R8_SPAN_BAG2 if bag <= 2 else R8_SPAN_BAG3
                if sp > cap:
                    dev["R8"] += 1
            # late pickup committed
            lpb = b.get("late_pickup_committed_breach")
            npl = b.get("new_pickup_late_min")
            cnt["LATE"] += 1
            if lpb is True or (_num(npl) and npl > LATE_PICKUP_MAX):
                dev["LATE"] += 1
    return {
        "n_proposals": n,
        "bundle_share": _rate(bundles, n),
        "R6_proposed_breach_rate": _rate(dev["R6"], cnt["R6"]),
        "R1_deliv_spread_over_8km_rate_bundles": _rate(dev["R1"], cnt["R1"]),
        "R5_pickup_spread_over_1_8km_rate_bundles": _rate(dev["R5"], cnt["R5"]),
        "R8_pickup_span_over_cap_rate_bundles": _rate(dev["R8"], cnt["R8"]),
        "R8_pickup_span_median_min": round(statistics.median(spans), 1) if spans else None,
        "late_pickup_over_5min_rate": _rate(dev["LATE"], cnt["LATE"]),
    }


def worst_couriers():
    try:
        d = json.load(open(RELIABILITY, encoding="utf-8"))
    except Exception:
        return []
    c = d.get("couriers", {})
    return sorted(
        ({"cid": k, "breach_rate": v["breach_rate"], "speed_vs_pred": v["speed_vs_pred_median"],
          "n": v["n_delivered"]} for k, v in c.items()),
        key=lambda x: -x["breach_rate"],
    )[:6]


def render(real, prop, worst):
    P = print
    P("=" * 76)
    P("  ODCHYLENIA OD REGUŁ BIZNESOWYCH ZIOMKA — z realnych danych")
    P("=" * 76)
    P(f"\n■ REALIZED (co faktycznie się stało, n={real['n_delivered']} dostaw):")
    P(f"  R6 dostawa >35 min (TWARDA):     {fmt(real['R6_breach_rate'])}   "
      f"[mediana {real['R6_p2d_median']} / p90 {real['R6_p2d_p90']} / max {real['R6_p2d_max']} min]")
    P(f"  R6 strefa ostrzeg. 30-35 min:    {fmt(real['R6_soft_zone_30_35_rate'])}")
    P(f"  ETA niedoszacowane (real>predykcja): {fmt(real['ETA_underpredict_rate'])}  "
      f"[mediana bias {real['ETA_residual_median_min']:+} min]")
    P(f"  Koncentracja floty top-3:  Ziomek proponuje {fmt(real['fleet_top3_share_ZIOMEK_proposed'])}  "
      f"vs człowiek realnie {fmt(real['fleet_top3_share_HUMAN_final'])}")
    P(f"\n■ PROPOSED (co Ziomek REKOMENDUJE vs reguły, n={prop['n_proposals']} propozycji, "
      f"worki {fmt(prop['bundle_share'])}):")
    P(f"  R6 propon. >35 min:              {fmt(prop['R6_proposed_breach_rate'])}")
    P(f"  R1 rozrzut dostaw >8 km (worki): {fmt(prop['R1_deliv_spread_over_8km_rate_bundles'])}")
    P(f"  R5 rozrzut odbiorów >1.8 km:     {fmt(prop['R5_pickup_spread_over_1_8km_rate_bundles'])}")
    P(f"  R8 pickup span > cap (worki):    {fmt(prop['R8_pickup_span_over_cap_rate_bundles'])}  "
      f"[mediana span {prop['R8_pickup_span_median_min']} min]")
    P(f"  Late-pickup >5 min (odbiór):     {fmt(prop['late_pickup_over_5min_rate'])}")
    if worst:
        P(f"\n■ Najwięksi sprawcy R6 (per kurier, z courier_reliability):")
        for w in worst:
            P(f"  cid={w['cid']:<5} breach {fmt(w['breach_rate'])}  wolniej +{w['speed_vs_pred']:.0f} min  (n={w['n']})")
    P("\n" + "=" * 76)


def fmt(x):
    return f"{x*100:.1f}%" if isinstance(x, (int, float)) else "—"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="zapisz też JSON")
    ap.add_argument("--max-lines", type=int, default=200000)
    ap.add_argument("--quiet", action="store_true", help="bez tabeli (tylko JSON)")
    args = ap.parse_args()
    real = realized_deviations()
    prop = proposed_deviations(args.max_lines)
    worst = worst_couriers()
    if not args.quiet:
        render(real, prop, worst)
    if args.json or args.quiet:
        payload = {"realized": real, "proposed": prop, "worst_couriers": worst}
        tmp = OUT_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, OUT_JSON)
        print(f"\n✓ Zapisano: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
