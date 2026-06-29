"""GATE B krok 4 — werdykt: OSRM vs TomTom vs realna jazda kuriera.

Laczy rw_results.jsonl (predykcje OSRM/TomTom) z trips_realworld.jsonl
(ground truth = czas czystej jazdy) po oid i liczy, ktory predyktor lepiej
trafia w rzeczywistosc.

Metodyka:
  err_i      = predykcja_i - ground_truth_drive   (signed)
  bias_i     = srednia(err_i) w obrebie bucketu    (handover, offset systemowy)
  residual_i = err_i - bias_i                       (blad NIEkalibrowalny)
  bcRMSE_i   = sqrt(srednia(residual_i^2))           <- glowna metryka

Staly offset (handover, baza tier-1/tier-2) zawsze skalibrujesz w ETA dispatchu
— liczy sie rozrzut residuali. Wygrywa predyktor z nizszym bcRMSE.

GATE B (bucket PEAK): TomTom wygrywa <=> bcRMSE nizszy o >=0.75 min I >=10%
ORAZ win-rate > 55%. Inaczej OSRM (prostsze, bez zaleznosci od API — Z3).
Werdykt na pelnym sample + cross-check na tier-1 (zloty standard GPS).

CLI:  python3 analyze_realworld.py [--results PLIK] [--ground-truth PLIK]
"""
import argparse
import json
import math
import os
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "rw_results.jsonl")
GROUND_TRUTH = os.path.join(HERE, "trips_realworld.jsonl")
BUCKETS = ("peak", "shoulder", "offpeak")

# --- progi werdyktu GATE B ---
MIN_IMPROVEMENT_MIN = 0.75   # bcRMSE TomTom nizszy o tyle minut
MIN_IMPROVEMENT_REL = 0.10   # ... i o tyle wzglednie
MIN_WINRATE = 0.55           # ... oraz win-rate TomTom
MIN_SAMPLE = 25              # peak ponizej -> werdykt niemiarodajny


def _load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and '"oid"' in line:
            rows.append(json.loads(line))
    return rows


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def _p90(values):
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * 0.9))]


def _block(records, label):
    """Bias-correction per bucket + metryki. records: dict z bucket/err_osrm/
    err_tomtom/gt/osrm/tomtom. Zwraca {bucket: stats}."""
    by_bucket = {}
    for r in records:
        by_bucket.setdefault(r["bucket"], []).append(r)
    stats = {}
    print(f"\n=== {label} ===")
    print(f"{'bucket':9} {'n':>4} | {'OSRM bias':>9} {'bcRMSE':>7} {'bcMAE':>6} "
          f"{'p90':>5} | {'TT bias':>8} {'bcRMSE':>7} {'bcMAE':>6} {'p90':>5} | "
          f"{'TT win%':>7}")
    print("-" * 92)
    order = [b for b in BUCKETS if b in by_bucket] + ["RAZEM"]
    pooled = []
    for b in order:
        rs = records if b == "RAZEM" else by_bucket[b]
        if not rs:
            continue
        # bias = srednia bledu w bucketcie (per-bucket, RAZEM tez wlasna)
        ob = statistics.mean(r["err_osrm"] for r in rs)
        tb = statistics.mean(r["err_tomtom"] for r in rs)
        o_res = [r["err_osrm"] - ob for r in rs]
        t_res = [r["err_tomtom"] - tb for r in rs]
        o_rmse = math.sqrt(statistics.mean(x * x for x in o_res))
        t_rmse = math.sqrt(statistics.mean(x * x for x in t_res))
        o_mae = statistics.mean(abs(x) for x in o_res)
        t_mae = statistics.mean(abs(x) for x in t_res)
        o_p90 = _p90([abs(x) for x in o_res])
        t_p90 = _p90([abs(x) for x in t_res])
        wins = sum(1 for ro, rt in zip(o_res, t_res) if abs(rt) < abs(ro))
        ties = sum(1 for ro, rt in zip(o_res, t_res) if abs(rt) == abs(ro))
        winrate = (wins + 0.5 * ties) / len(rs)
        print(f"{b:9} {len(rs):>4} | {ob:>+9.2f} {o_rmse:>7.2f} {o_mae:>6.2f} "
              f"{o_p90:>5.2f} | {tb:>+8.2f} {t_rmse:>7.2f} {t_mae:>6.2f} "
              f"{t_p90:>5.2f} | {winrate*100:>6.0f}%")
        stats[b] = {"n": len(rs), "osrm_bcrmse": o_rmse, "tt_bcrmse": t_rmse,
                    "winrate": winrate, "osrm_bias": ob, "tt_bias": tb}
    # korelacja predyktor<->rzeczywistosc
    g = [r["gt"] for r in records]
    print(f"  korelacja Pearson (predykcja vs realna jazda): "
          f"OSRM r={_pearson(g, [r['osrm'] for r in records]):.3f}  "
          f"TomTom r={_pearson(g, [r['tomtom'] for r in records]):.3f}")
    return stats


def _verdict(peak):
    if not peak or peak["n"] < MIN_SAMPLE:
        n = peak["n"] if peak else 0
        return f"NIEMIARODAJNY — peak n={n} < {MIN_SAMPLE}, zbieraj dalej"
    o, t = peak["osrm_bcrmse"], peak["tt_bcrmse"]
    impr = o - t
    rel = impr / o if o > 0 else 0.0
    win = peak["winrate"]
    head = (f"peak bcRMSE: OSRM {o:.2f} vs TomTom {t:.2f}  "
            f"(TomTom {impr:+.2f} min, {rel*100:+.0f}%)  win-rate TomTom {win*100:.0f}%")
    if impr >= MIN_IMPROVEMENT_MIN and rel >= MIN_IMPROVEMENT_REL and win > MIN_WINRATE:
        v = "TomTom WYGRYWA → PROCEED Phase 1 (tomtom_client.py)"
    elif impr <= -MIN_IMPROVEMENT_MIN:
        v = "OSRM wyraznie lepszy → zostac przy OSRM"
    else:
        v = "remis / marginalne → OSRM (Z3: prostsze, bez zaleznosci od API)"
    return head + "\n  VERDICT: " + v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=RESULTS)
    ap.add_argument("--ground-truth", default=GROUND_TRUTH)
    args = ap.parse_args()

    gt = {}
    for r in _load_jsonl(args.ground_truth):
        if "tier2_underflow" not in (r.get("problems") or []):
            gt[r["oid"]] = r
    preds = _load_jsonl(args.results)

    records = []
    for p in preds:
        g = gt.get(p["oid"])
        if not g or p.get("osrm_eta_min") is None or p.get("tomtom_eta_min") is None:
            continue
        gtv = g["ground_truth_drive_min"]
        records.append({
            "oid": p["oid"], "bucket": g["bucket"], "tier": g["tier"], "gt": gtv,
            "osrm": p["osrm_eta_min"], "tomtom": p["tomtom_eta_min"],
            "err_osrm": p["osrm_eta_min"] - gtv,
            "err_tomtom": p["tomtom_eta_min"] - gtv,
        })

    print(f"predykcje rw_results: {len(preds)}   ground truth: {len(gt)}   "
          f"po join: {len(records)}")
    if not records:
        print("\nBrak polaczonych tropow — uruchom measure_realworld.py (forward-live) "
              "i odczekaj az nazbiera pomiarow.")
        return

    full = _block(records, "PELEN SAMPLE (tier-1 + tier-2)")
    t1 = [r for r in records if r["tier"] == 1]
    t1_stats = _block(t1, f"CROSS-CHECK tier-1 (zloty standard GPS, n={len(t1)})") \
        if len(t1) >= 8 else {}

    print("\n" + "=" * 60)
    print("GATE B — werdykt (pelen sample, bucket PEAK):")
    print("  " + _verdict(full.get("peak")))
    if t1_stats.get("peak"):
        print("\ntier-1 cross-check (peak):")
        print("  " + _verdict(t1_stats.get("peak")))
    elif t1:
        print(f"\ntier-1 cross-check: tylko {len(t1)} tropow tier-1 — za malo, "
              "zbieraj wiecej od kurierow z GPS (484/370/400/509/393)")


if __name__ == "__main__":
    main()
