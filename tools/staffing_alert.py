#!/usr/bin/env python3
"""staffing_alert — W0.6 (advisory A3 część 1): alert kuriero-godzin, SHADOW-only.

„O HH:00 zabraknie N kuriero-godzin" — baseline-first nowcast (BEZ ML; LightGBM
dopiero gdy bije baseline ≥10% pinball). Model (wzór Gemini/Nemotron):
 - arrivals baseline per (weekday, hour) z 61 d, shrink do globalnej godziny;
 - skala dnia = EWMA(α=0,3) RATIO faktyczne/baseline z godzin już obserwowanych;
 - `effective_couriers` = aktywni obserwowani do tej pory dziś (proxy rostered × GPS);
 - `load_hat(peak)` = przewidziane arrivals w oknie szczytu / effective;
 - shortfall (kuriero-godziny) = ceil(arrivals_peak / TARGET_L − effective) gdy load_hat≥CRISIS_L.
Alert wymaga lead ≥ LEAD_MIN (90′) przed przewidzianym szczytem.

ZERO wpływu na decyzje. Wynik → jsonl (live) lub raport backtestu.
Bramka (walk-forward 61 d): fire na 10 najcięższych dniach ≥90′ przed szczytem
breach; false-alarm <10% na zdrowych.

Użycie:
  python3 tools/staffing_alert.py --backtest --data <sla_log.jsonl>
  (live: --live → dispatch_state/staffing_alert.jsonl; szkielet, wpięcie kafla = tura późniejsza)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime

# ── parametry modelu (baseline; tuning w backteście) ──
CRISIS_L = 5.0          # próg load_hat (zam./kurier w oknie szczytu) → kryzys (E-3 kolano ~6, margines)
TARGET_L = 4.0          # docelowy load na kuriera → z niego liczymy niedobór kuriero-godzin
LEAD_MIN = 90           # wymagany lead alertu przed szczytem (min)
SHRINK = 8.0            # shrink baseline godziny do globalnej
EWMA_A = 0.3            # skala dnia (ratio) EWMA
DAY_START_H = 9         # od której godziny liczymy (przed 9 pomijalny wolumen)
PEAK_WINDOW = (13, 18)  # okno szczytu (Warsaw) do oceny load_hat


def _parse(dt):
    try:
        return datetime.strptime(dt[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def load_day_hour(path):
    """{date: {hour: {arrivals, active_cids set, food, breach}}} z sla_log.
    arrival-hour ≈ godzina (delivered − delivery_time) [proxy złożenia]."""
    dh = defaultdict(lambda: defaultdict(lambda: {"arr": 0, "cids": set(),
                                                  "food": 0, "breach": 0}))
    for l in open(path, encoding="utf-8"):
        try:
            r = json.loads(l)
        except json.JSONDecodeError:
            continue
        dt = _parse(r.get("delivered_at"))
        m = r.get("delivery_time_minutes")
        if dt is None or m is None:
            continue
        cid = str(r.get("courier_id") or "")
        # arrival hour (order-ready proxy)
        ah = dt.hour  # delivered hour ~ demand hour (bin tolerance)
        d = dt.date().isoformat()
        cell = dh[d][dt.hour]
        cell["arr"] += 1
        cell["cids"].add(cid)
        if not r.get("was_czasowka"):
            cell["food"] += 1
            if float(m) > 35:
                cell["breach"] += 1
    return dh


def build_baseline(dh, exclude_day=None):
    """arrivals baseline per (weekday, hour) shrunk do globalnej godziny."""
    wk_hr = defaultdict(list)
    hr = defaultdict(list)
    for d, hours in dh.items():
        if d == exclude_day:
            continue
        wd = datetime.fromisoformat(d).weekday()
        for h, c in hours.items():
            wk_hr[(wd, h)].append(c["arr"])
            hr[h].append(c["arr"])
    base = {}
    gmean_h = {h: (sum(v) / len(v) if v else 0.0) for h, v in hr.items()}
    for (wd, h), v in wk_hr.items():
        n = len(v)
        m = sum(v) / n if n else 0.0
        base[(wd, h)] = (n * m + SHRINK * gmean_h.get(h, 0.0)) / (n + SHRINK)
    return base, gmean_h


def alert_for_day(day, dh, base, gmean_h):
    """Walk-forward: dla każdej godziny T zwraca (fire_hour, shortfall, peak_hour)
    pierwszego alertu (lub None). effective = aktywni do tej pory."""
    wd = datetime.fromisoformat(day).weekday()
    hours = dh[day]
    seen_cids = set()
    ratio_ewma = None
    for T in range(DAY_START_H, PEAK_WINDOW[1]):
        c = hours.get(T)
        if c:
            seen_cids |= c["cids"]
            b = base.get((wd, T), gmean_h.get(T, 0.0))
            if b > 0:
                r = c["arr"] / b
                ratio_ewma = r if ratio_ewma is None else (ratio_ewma + EWMA_A * (r - ratio_ewma))
        effective = max(1, len(seen_cids))
        scale = ratio_ewma if ratio_ewma is not None else 1.0
        # przewidziane arrivals w oknie szczytu (godziny jeszcze przed nami)
        peak_arr = 0.0
        peak_hour = None
        for ph in range(max(T + 1, PEAK_WINDOW[0]), PEAK_WINDOW[1]):
            pred = base.get((wd, ph), gmean_h.get(ph, 0.0)) * scale
            if pred > peak_arr:
                peak_arr, peak_hour = pred, ph
        if peak_hour is None:
            continue
        load_hat = peak_arr / effective
        lead_min = (peak_hour - T) * 60
        if load_hat >= CRISIS_L and lead_min >= LEAD_MIN:
            shortfall = math.ceil(peak_arr / TARGET_L - effective)
            if shortfall > 0:
                return {"fire_hour": T, "peak_hour": peak_hour, "lead_min": lead_min,
                        "shortfall_courier_hours": shortfall, "load_hat": round(load_hat, 2),
                        "effective": effective}
    return None


def backtest(path, top_n=10, healthy_breach_pct=12.0):
    dh = load_day_hour(path)
    # ranking dni po breach%
    day_stat = {}
    for d, hours in dh.items():
        food = sum(c["food"] for c in hours.values())
        breach = sum(c["breach"] for c in hours.values())
        if food >= 30:
            day_stat[d] = {"food": food, "breach_pct": 100 * breach / food,
                           "peak_breach_hour": max(hours, key=lambda h: hours[h]["breach"])}
    ranked = sorted(day_stat, key=lambda d: day_stat[d]["breach_pct"], reverse=True)
    heavy = ranked[:top_n]
    healthy = [d for d in ranked if day_stat[d]["breach_pct"] < healthy_breach_pct]
    # heavy: fire ≥90' przed szczytem breach
    heavy_hits = []
    for d in heavy:
        base, gm = build_baseline(dh, exclude_day=d)  # walk-forward: dzień d out
        a = alert_for_day(d, dh, base, gm)
        pbh = day_stat[d]["peak_breach_hour"]
        lead_ok = a is not None and (pbh - a["fire_hour"]) * 60 >= LEAD_MIN
        heavy_hits.append({"day": d, "breach_pct": round(day_stat[d]["breach_pct"], 1),
                           "fired": a is not None, "fire_hour": a["fire_hour"] if a else None,
                           "peak_breach_hour": pbh,
                           "lead_min_to_breach": (pbh - a["fire_hour"]) * 60 if a else None,
                           "lead_ge90": lead_ok,
                           "shortfall": a["shortfall_courier_hours"] if a else None})
    # healthy: false-alarm
    healthy_fires = 0
    for d in healthy:
        base, gm = build_baseline(dh, exclude_day=d)
        a = alert_for_day(d, dh, base, gm)
        if a is not None:
            healthy_fires += 1
    hit_rate = 100 * sum(1 for h in heavy_hits if h["lead_ge90"]) / max(1, len(heavy))
    false_rate = 100 * healthy_fires / max(1, len(healthy))
    # DIAGNOSTYKA SEPAROWALNOŚCI (dlaczego bramka nie przechodzi na delivery-only):
    import statistics as _st
    opc = {}  # orders/courier/dzień = jedyny proxy pojemności z delivery-logu
    for d, hours in dh.items():
        food = sum(c["food"] for c in hours.values())
        if food < 30:
            continue
        cids = set()
        for c in hours.values():
            cids |= c["cids"]
        opc[d] = food / max(1, len(cids))
    heavy_opc = [opc[d] for d in heavy if d in opc]
    healthy_opc = [opc[d] for d in healthy if d in opc]
    return {
        "params": {"CRISIS_L": CRISIS_L, "TARGET_L": TARGET_L, "LEAD_MIN": LEAD_MIN},
        "n_days": len(day_stat), "n_heavy": len(heavy), "n_healthy": len(healthy),
        "heavy_hit_rate_pct": round(hit_rate, 1),
        "healthy_false_alarm_pct": round(false_rate, 1),
        # DELIVERY-ONLY: sygnał pojemności (orders/courier) NIE separuje czysto —
        # heavy i healthy nakładają się; 16.05 (najgorszy, capacity-bound) ma NISKI
        # popyt. Czysty sygnał (E-3 in-flight L≥6 + lead z prognozy arrivals) wymaga
        # ROSTERU (grafik GRF-02) + telemetrii assign/kolejki — poza tym korpusem.
        "sep_orders_per_courier": {
            "heavy_med": round(_st.median(heavy_opc), 1) if heavy_opc else None,
            "healthy_med": round(_st.median(healthy_opc), 1) if healthy_opc else None},
        "verdict": ("PASS" if (hit_rate >= 70.0 and false_rate < 10.0)
                    else "DATA_LIMITED_NEEDS_ROSTER"),
        "note": "Bramka fire≥90%/false<10% NIE-osiągalna na delivery-only 61d (sygnał "
                "pojemności nakłada się). Tool gotowy; wejście: effective=rostered(grafik)×GPS "
                "+ in-flight z events.db + arrivals-forecast (W3.2/P-01). Empiryczny lead istnieje "
                "(P-01: kolejka≥10@14:00 = 3h45' przed 1. slotem ≥50% late) — wymaga telemetrii kolejki.",
        "heavy": heavy_hits,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/root/ziomek-advisory/data/sla_log_FULL_frozen_20260706.jsonl")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--top-n", type=int, default=10)
    a = ap.parse_args(argv)
    if a.backtest:
        res = backtest(a.data, a.top_n)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res["verdict"] == "PASS" else 1
    print("użyj --backtest (live-kafel = tura późniejsza po ACK)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
