#!/usr/bin/env python3
"""D5-ODBIOR pomiar na KANONICZNYM targecie GPS (restaurant_last_inside_at).

READ-ONLY. Buduje niezalezny target GPS-proxy odbioru zgodny z KPI_BINDING_V1
(dispatch_v2/tools/eta_ground_truth.py: possession_event = restaurant_last_inside_at,
high-confidence geofence) i porownuje na nim blad KALIBRATORA (pred_p50) vs SILNIKA
(eng_pickup_slip_min). Kontrola na targecie KLIKOWYM (pickup_slip_koord_min) obok.

Zrodla (wszystkie read-only, snapshot bazy w /tmp/d5snap + zywy log kalibratora):
  - /tmp/d5snap/eta_calib.db            (eta_calib_features: czas_kuriera, klik, eng, cid)
  - /tmp/d5snap/restaurant_dwell.json   (writer restaurant_dwell_detector -> geofence GPS)
  - dispatch_state/eta_calib_shadow.jsonl (predykcje kalibratora per-order: pred_p50/pred_op/real)

Wynik: GPS_MEASURE.md (obok) + result.json (obok). Nic nie flipuje.
"""
from __future__ import annotations
import json, sqlite3, datetime as dt, statistics as st
from pathlib import Path

DB = "/tmp/d5snap/eta_calib.db"
DWELL = "/tmp/d5snap/restaurant_dwell.json"
CAL_SHADOW = "/root/.openclaw/workspace/dispatch_state/eta_calib_shadow.jsonl"
OUT = Path(__file__).resolve().parent

# KPI_BINDING_V1 progi PICKUP (cytat z eta_ground_truth.py)
THR = {
    "mae_max_min": 6.0, "min_improvement_vs_engine_pct": 25.0,
    "late_band_pct": [15.0, 22.0], "median_bias_abs_max_min": 1.5,
    "p90_abs_err_max_min": 20.0, "coverage_min_n": 200,
}


def pw(s: str) -> dt.datetime:
    """Parsuj naiwny znacznik Warsaw ('YYYY-MM-DD HH:MM:SS')."""
    return dt.datetime.strptime(s[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")


def highconf(d: dict) -> bool:
    """High-confidence geofence wg _geofence_confidence: gps_geofence, n_in>=2, min_dist<=radius."""
    if d.get("_source") != "gps_geofence":
        return False
    try:
        n = int(d.get("_n_in_geofence") or 0)
    except (TypeError, ValueError):
        n = 0
    md, rad = d.get("_min_dist_m"), d.get("_radius_m")
    inside = (md <= rad) if isinstance(md, (int, float)) and isinstance(rad, (int, float)) else True
    return n >= 2 and inside


def mae(xs): return sum(abs(x) for x in xs) / len(xs)
def p90(xs):
    s = sorted(abs(x) for x in xs); return s[int(0.9 * (len(s) - 1))]
def winz(xs, p=99):
    s = sorted(xs); lo = s[int((1 - p / 100) / 2 * (len(s) - 1))]; hi = s[int((p / 100 + (1 - p / 100) / 2) * (len(s) - 1))]
    return [min(max(x, lo), hi) for x in xs]


def main():
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    feat = {r["order_id"]: dict(r) for r in con.execute("SELECT * FROM eta_calib_features")}
    dw = json.load(open(DWELL))

    # Najswiezsza predykcja kalibratora per oid (leg=pickup); real == klik (potwierdzone).
    cal = {}
    with open(CAL_SHADOW) as f:
        for line in f:
            r = json.loads(line)
            if r.get("leg") != "pickup":
                continue
            o = r["oid"]
            if o not in cal or r["logged_at"] > cal[o]["logged_at"]:
                cal[o] = r

    # Target GPS: slip = restaurant_last_inside_at(departed_restaurant, Warsaw) - czas_kuriera.
    cov = {"dwell_total": len(dw), "highconf": 0, "strict_cid_match": 0,
           "no_cid": 0, "mismatch_excluded": 0}
    gps = {}      # strict: courier-match wymagany LUB brak cid (bez sprzecznosci)
    diffs = []    # GPS_slip - klik na zbiorze mierzonym (z predykcja kalibratora)
    for oid, d in dw.items():
        if not highconf(d):
            continue
        cov["highconf"] += 1
        ft = feat.get(oid)
        if not ft or not ft.get("czas_kuriera"):
            continue
        dep = d.get("departed_restaurant")
        if not dep:
            continue
        ck = dt.datetime.strptime(dep[:10] + " " + ft["czas_kuriera"], "%Y-%m-%d %H:%M")
        slip = (pw(dep) - ck).total_seconds() / 60
        if abs(slip) > 180:  # zabezpieczenie skladania daty czas_kuriera
            continue
        dcid = d.get("courier_id")
        if dcid is None:
            cov["no_cid"] += 1
        elif str(dcid) != str(ft.get("courier_id")):
            cov["mismatch_excluded"] += 1
            continue
        else:
            cov["strict_cid_match"] += 1
        gps[oid] = slip
        if cal.get(oid) and ft.get("pickup_slip_koord_min") is not None:
            diffs.append(slip - ft["pickup_slip_koord_min"])

    def measure(target, use_klik=False):
        rows = []
        for o, slip in target.items():
            c = cal.get(o); ft = feat.get(o)
            if not c:
                continue
            tgt = ft["pickup_slip_koord_min"] if use_klik else slip
            if tgt is None:
                continue
            rows.append((slip if not use_klik else tgt, c["pred_p50"], c["pred_op"],
                         ft.get("eng_pickup_slip_min")))
        cerr = [p50 - t for t, p50, pop, eng in rows]
        both = [(t, p50, eng) for t, p50, pop, eng in rows if eng is not None]
        ce = [p50 - t for t, p50, eng in both]; ee = [eng - t for t, p50, eng in both]
        late = sum(1 for t, p50, pop, eng in rows if t > pop)  # actual pozniej niz obietnica P80
        return {
            "n_cal": len(cerr), "mae": round(mae(cerr), 2),
            "mae_winz_p99": round(mae(winz(cerr)), 2),
            "bias_median": round(st.median(cerr), 2), "p90_abs_err": round(p90(cerr), 2),
            "n_common_engine": len(both), "cal_mae_common": round(mae(ce), 2),
            "eng_mae_common": round(mae(ee), 2),
            "improvement_pct": round(100 * (mae(ee) - mae(ce)) / mae(ee), 1),
            "late_band_pct": round(100 * late / len(rows), 1),
        }

    gps_m = measure(gps, use_klik=False)
    klik_m = measure(gps, use_klik=True)  # kontrola na tym samym zbiorze orderow

    def verdict(m):
        checks = {
            "mae": m["mae"] <= THR["mae_max_min"],
            "improvement": m["improvement_pct"] >= THR["min_improvement_vs_engine_pct"],
            "late_band": THR["late_band_pct"][0] <= m["late_band_pct"] <= THR["late_band_pct"][1],
            "bias": abs(m["bias_median"]) <= THR["median_bias_abs_max_min"],
            "p90": m["p90_abs_err"] <= THR["p90_abs_err_max_min"],
            "coverage": m["n_cal"] >= THR["coverage_min_n"],
        }
        return checks, ("GO" if all(checks.values()) else "WAIT/NO-GO")

    gps_checks, gps_v = verdict(gps_m)
    res = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "target": "GPS restaurant_last_inside_at (KPI_BINDING_V1 possession_event, high-confidence)",
        "thresholds": THR,
        "coverage": cov,
        "gps_target_metrics": gps_m,
        "gps_verdict_checks": gps_checks,
        "gps_verdict": gps_v,
        "klik_control_same_orders": klik_m,
        "gps_minus_klik_shift_median_min": round(st.median(diffs), 2) if diffs else None,
        "note": ("real w eta_calib_shadow == pickup_slip_koord_min (klik) — kalibrator uczony/oceniany "
                 "na kliku; tu przemierzony na GPS. Kalibrator pred_p50=punkt centralny (MAE/bias), "
                 "pred_op=obietnica P80 (late-band). Silnik=eng_pickup_slip_min."),
    }
    (OUT / "result.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return res


if __name__ == "__main__":
    main()
