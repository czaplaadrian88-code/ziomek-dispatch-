"""DARMOWY werdykt recalib — krzywa godzinowa vs obecna tabela V326.

Ramię OSRM-recalib = osrm_freeflow_min × NOWA_KRZYWA[godz_odbioru_warsaw].
Liczone OFFLINE z już logowanego `osrm_freeflow_min` (ZERO API) na wszystkich
tropach z rw_results.jsonl ⨝ trips_realworld.jsonl (po oid).

DLACZEGO INNA METRYKA NIŻ GATE B:
  GATE B mierzy bcRMSE (residual PO odjęciu biasu per bucket) — bo stały offset
  zawsze skalibrujesz. Ale recalib NAPRAWIA WŁAŚNIE TEN BIAS w mnożniku. Produkcja
  NIE robi osobnej korekty biasu → kurier odczuwa SUROWY błąd. Dlatego główne
  metryki tutaj:
    - bias (signed mean err)  → czy recalib zeruje systematyczne niedoszacowanie
    - surowy MAE / RMSE       → realny błąd odczuwany (bez bias-correction)
    - mediana rezyduum/godz   → mirror hourly_multiplier_curve.md na pełnym zbiorze
  bcRMSE raportowane tylko dla ciągłości (oczekiwane ~neutralne).

OBA ramiona (obecne + recalib) rekonstruowane z TEGO SAMEGO freeflow i TEJ SAMEJ
godziny odbioru (g["hour_warsaw"]) + weekday z pu_epoch → czysty A/B, zero konfundów.
Recalib zmienia TYLKO weekday → weekend liczony obecną tabelą w obu (identyczny).

CLI:  python3 analyze_recalib.py
"""
import json
import math
import os
import statistics
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "rw_results.jsonl")
GROUND_TRUTH = os.path.join(HERE, "trips_realworld.jsonl")
WARSAW = ZoneInfo("Europe/Warsaw")
BUCKETS = ("peak", "shoulder", "offpeak")

# OBECNA tabela weekday — kopia common.py:V326_OSRM_TRAFFIC_TABLE["weekday"]
# (V3.27.3 TASK G, 2026-04-27). [lo, hi) godziny.
CURRENT_WD = [
    (0, 6, 1.0), (6, 8, 1.0), (8, 10, 1.1), (10, 12, 1.1), (12, 13, 1.2),
    (13, 14, 1.2), (14, 15, 1.2), (15, 16, 1.5), (16, 17, 1.3), (17, 19, 1.2),
    (19, 20, 1.1), (20, 21, 1.0), (21, 24, 1.0),
]
# WARIANT B (do wdrożenia) — krzywa median-based (hourly_multiplier_curve.md)
# z drobnym ściągnięciem 17-18: 1.30/1.35 → 1.25 (doc-curve przestrzeliwała +0.5/+0.36).
RECALIB_WD = [
    (0, 9, 1.0), (9, 10, 1.15), (10, 12, 1.25), (12, 13, 1.40), (13, 14, 1.50),
    (14, 15, 1.35), (15, 17, 1.55), (17, 18, 1.25), (18, 19, 1.25), (19, 20, 1.25),
    (20, 21, 1.10), (21, 24, 1.05),
]


def _lookup(table, hour):
    for lo, hi, mult in table:
        if lo <= hour < hi:
            return mult
    return 1.0


def _load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and '"oid"' in line:
            rows.append(json.loads(line))
    return rows


def _median(xs):
    return statistics.median(xs) if xs else 0.0


def _rmse(xs):
    return math.sqrt(statistics.mean(x * x for x in xs)) if xs else 0.0


def _p90(values):
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * 0.9))]


def _block(records, label):
    """Per-bucket: bias (signed mean), surowy RMSE/MAE, bcRMSE, win-rate recalib."""
    by_bucket = {}
    for r in records:
        by_bucket.setdefault(r["bucket"], []).append(r)
    print(f"\n=== {label} (n={len(records)}) ===")
    print(f"{'bucket':9} {'n':>4} | {'OBECNY':^25} | {'RECALIB':^25} | {'recal':>6}")
    print(f"{'':9} {'':>4} | {'bias':>7} {'rawRMSE':>7} {'rawMAE':>6} | "
          f"{'bias':>7} {'rawRMSE':>7} {'rawMAE':>6} | {'win%':>6}")
    print("-" * 88)
    stats = {}
    order = [b for b in BUCKETS if b in by_bucket] + ["RAZEM"]
    for b in order:
        rs = records if b == "RAZEM" else by_bucket[b]
        if not rs:
            continue
        ec = [r["err_cur"] for r in rs]
        er = [r["err_rec"] for r in rs]
        cur_bias, rec_bias = statistics.mean(ec), statistics.mean(er)
        cur_mae = statistics.mean(abs(x) for x in ec)
        rec_mae = statistics.mean(abs(x) for x in er)
        wins = sum(1 for a, b2 in zip(ec, er) if abs(b2) < abs(a))
        ties = sum(1 for a, b2 in zip(ec, er) if abs(b2) == abs(a))
        winrate = (wins + 0.5 * ties) / len(rs)
        print(f"{b:9} {len(rs):>4} | {cur_bias:>+7.2f} {_rmse(ec):>7.2f} "
              f"{cur_mae:>6.2f} | {rec_bias:>+7.2f} {_rmse(er):>7.2f} "
              f"{rec_mae:>6.2f} | {winrate*100:>5.0f}%")
        stats[b] = {"n": len(rs), "cur_bias": cur_bias, "rec_bias": rec_bias,
                    "cur_mae": cur_mae, "rec_mae": rec_mae,
                    "cur_rmse": _rmse(ec), "rec_rmse": _rmse(er), "winrate": winrate}
    return stats


def _per_hour(records):
    """Mediana rezyduum per godzina odbioru — mirror hourly_multiplier_curve.md."""
    by_h = {}
    for r in records:
        by_h.setdefault(r["hour"], []).append(r)
    print("\n=== MEDIANA REZYDUUM per GODZINA ODBIORU (weekday) — mirror krzywej ===")
    print("  rezid = predykcja − realna jazda (min). Ujemne = NIEDOSZACOWANIE.")
    print(f"{'godz':>4} {'n':>4} | {'cur_mult':>8} {'rec_mult':>8} | "
          f"{'rezid OBECNY':>13} {'rezid RECALIB':>14}")
    print("-" * 62)
    for h in sorted(by_h):
        rs = by_h[h]
        med_c = _median([r["err_cur"] for r in rs])
        med_r = _median([r["err_rec"] for r in rs])
        cm = _lookup(CURRENT_WD, h)
        rm = _lookup(RECALIB_WD, h)
        flag = "  <<" if abs(med_c) >= 1.0 and abs(med_r) < abs(med_c) else ""
        print(f"{h:>4} {len(rs):>4} | {cm:>8.2f} {rm:>8.2f} | "
              f"{med_c:>+13.2f} {med_r:>+14.2f}{flag}")


def main():
    gt = {}
    for r in _load_jsonl(GROUND_TRUTH):
        if "tier2_underflow" not in (r.get("problems") or []):
            gt[r["oid"]] = r
    preds = _load_jsonl(RESULTS)

    wd, we = [], []           # weekday / weekend records
    sanity = []               # |recon_current − logged osrm_eta_min|
    for p in preds:
        g = gt.get(p["oid"])
        ff = p.get("osrm_freeflow_min")
        if not g or ff is None:
            continue
        gtv = g["ground_truth_drive_min"]
        if gtv is None:
            continue
        dow = datetime.fromtimestamp(g["pu_epoch"], WARSAW).weekday()
        hour = g["hour_warsaw"]
        if dow <= 4:
            cm, rm = _lookup(CURRENT_WD, hour), _lookup(RECALIB_WD, hour)
        else:
            cm = rm = None     # weekend: obie tabele identyczne (recalib nie dotyka)
        rec = {
            "oid": p["oid"], "bucket": g["bucket"], "tier": g["tier"],
            "hour": hour, "gt": gtv, "ff": ff,
        }
        if cm is not None:
            cur_eta, rec_eta = ff * cm, ff * rm
            rec["err_cur"] = cur_eta - gtv
            rec["err_rec"] = rec_eta - gtv
            wd.append(rec)
            if p.get("osrm_eta_min") is not None:
                sanity.append(abs(cur_eta - p["osrm_eta_min"]))
        else:
            we.append(rec)

    print("=" * 88)
    print("DARMOWY WERDYKT RECALIB — krzywa godzinowa vs obecna tabela V326 (offline, $0)")
    print("=" * 88)
    print(f"rw_results: {len(preds)}   ground truth (bez tier2_underflow): {len(gt)}")
    print(f"po join: weekday={len(wd)}  weekend={len(we)} (weekend nietknięty — pomijam)")
    if sanity:
        print(f"sanity: |rekonstr. obecny − logowany osrm_eta_min| śr={statistics.mean(sanity):.2f}min "
              f"med={_median(sanity):.2f}min (rozjazd = measure-hour vs pickup-hour, OK jeśli mały)")
    if not wd:
        print("\nBrak weekday tropów po join — sprawdź dane.")
        return

    full = _block(wd, "WEEKDAY — pełen sample (tier-1 + tier-2)")
    t1 = [r for r in wd if r["tier"] == 1]
    if len(t1) >= 8:
        _block(t1, "WEEKDAY — cross-check tier-1 (GPS gold)")
    else:
        print(f"\ntier-1 cross-check: tylko {len(t1)} tropów — za mało (info only)")

    _per_hour(wd)

    # ── WERDYKT ──
    print("\n" + "=" * 88)
    print("WERDYKT (kryterium Krok 0 z planu): recalib zeruje systematyczne")
    print("niedoszacowanie (bias→0) NIE psując surowego rozrzutu (MAE)?")
    print("-" * 88)
    raz = full["RAZEM"]
    for b in [b for b in BUCKETS if b in full] + ["RAZEM"]:
        s = full[b]
        d_bias = abs(s["rec_bias"]) - abs(s["cur_bias"])    # <0 = mniejszy |bias|
        d_mae = s["rec_mae"] - s["cur_mae"]                  # <0 = mniejszy MAE
        verb_bias = "↓ POPRAWA" if d_bias < -0.05 else ("↑ GORZEJ" if d_bias > 0.05 else "≈ bez zmian")
        verb_mae = "↓ POPRAWA" if d_mae < -0.05 else ("↑ GORZEJ" if d_mae > 0.05 else "≈ bez zmian")
        print(f"  {b:9} n={s['n']:>4} | |bias| {abs(s['cur_bias']):.2f}→{abs(s['rec_bias']):.2f} "
              f"({verb_bias})  |  MAE {s['cur_mae']:.2f}→{s['rec_mae']:.2f} ({verb_mae})")
    print("-" * 88)
    bias_better = abs(raz["rec_bias"]) < abs(raz["cur_bias"]) - 0.10
    mae_not_worse = raz["rec_mae"] <= raz["cur_mae"] + 0.05
    if bias_better and mae_not_worse:
        print("  ✅ RECALIB WYGRYWA → promuj krzywę do common.py:V326_OSRM_TRAFFIC_TABLE")
        print(f"     (RAZEM bias {raz['cur_bias']:+.2f}→{raz['rec_bias']:+.2f} min, "
              f"MAE {raz['cur_mae']:.2f}→{raz['rec_mae']:.2f} min — $0, zero ryzyka)")
    elif bias_better and not mae_not_worse:
        print("  ⚠ recalib zbija bias ale podnosi MAE (przestrzelenie na części godzin)")
        print("     → rozważ łagodniejsze zaokrąglenie krzywej; NIE promuj 1:1")
    else:
        print("  ❌ recalib NIE poprawia biasu RAZEM → zostaw obecną tabelę")
    print("\n  CAVEAT: krzywa wyliczona z PODZBIORU tego zbioru (595 weekday tropów,")
    print("  03.06) → częściowo in-sample. Prawdziwy out-of-sample = forward-live A/B.")
    print("  Ten werdykt = potwierdzenie kierunku + skala, nie niezależna walidacja.")


if __name__ == "__main__":
    main()
