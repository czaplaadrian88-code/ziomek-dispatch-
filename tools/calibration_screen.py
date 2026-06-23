#!/usr/bin/env python3
"""calibration_screen — przesiew dowozów do KALIBRACJI Ziomka (Adrian 2026-06-23).

READ-ONLY, zero zmian. Problem: surowy "override 84%" jest BEZ WARTOŚCI jako sygnał jakości,
bo Ziomek często proponuje kuriera, który tylko WYGLĄDA na wolnego (no_gps/centrum-fikcja),
a realnie nie pracował (był w domu, bo nie było roboty / robił co innego) — koordynator
SŁUSZNIE go pominął. Takie decyzje NIE są miarodajne do kalibracji.

Ten skrypt wybiera podzbiór MIARODAJNY: decyzje, w których porównanie Ziomek-vs-rzeczywistość
jest UCZCIWE — bo proponowany kurier był FAKTYCZNIE w pracy w tym czasie, była realna pula
wyboru, i znamy realny wynik dostawy.

Sygnał "czy kurier realnie pracował" = aktywność w sla_log (jego pickupy/dostawy). Jeśli
best_cid (pick Ziomka) miał aktywność w oknie ±W min wokół tej decyzji → pracował →
miarodajne. Jeśli nie → w domu/skończył → odsiew. Pula wyboru = ≥min_fleet kurierów
aktywnych w oknie.

Źródła (47 dni, cały czerwiec): eta_calibration_log.jsonl (best_cid+real_cid+sla_ok+outcome)
+ sla_log.jsonl (aktywność = ground-truth kto kiedy pracował).

Uruchom:
  cd /root/.openclaw/workspace/scripts
  PYTHONPATH=. /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tools/calibration_screen.py \
      --from 2026-06-01 --to 2026-06-23 --window 90 --min-fleet 2 --out /root/.openclaw/workspace/dispatch_state/calibration_set_june.jsonl
"""
import argparse
import bisect
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
BASE = "/root/.openclaw/workspace"
SLA_LOG = f"{BASE}/scripts/logs/sla_log.jsonl"
ETA_CALIB = f"{BASE}/dispatch_state/eta_calibration_log.jsonl"
OUT_DEFAULT = f"{BASE}/dispatch_state/calibration_set_june.jsonl"


def _read_jsonl(path):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue


def _parse_dt(s):
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:  # sla_log / eta picked_up_at = Warsaw-naive
            dt = dt.replace(tzinfo=WARSAW)
        return dt
    except (ValueError, TypeError):
        return None


def _cid(v):
    return None if v is None else str(v).strip()


def _pct(n, d):
    return (100.0 * n / d) if d else 0.0


def main():
    ap = argparse.ArgumentParser(description="Przesiew dowozów do kalibracji (read-only).")
    ap.add_argument("--from", dest="dfrom", default="2026-06-01")
    ap.add_argument("--to", dest="dto", default="2026-06-23")
    ap.add_argument("--window", type=float, default=90.0, help="±min: okno aktywności = 'realnie pracował'")
    ap.add_argument("--min-fleet", type=int, default=2, help="min kurierów aktywnych w oknie = realny wybór")
    ap.add_argument("--out", default=OUT_DEFAULT)
    args = ap.parse_args()
    dfrom = datetime.fromisoformat(args.dfrom).replace(tzinfo=WARSAW)
    dto = datetime.fromisoformat(args.dto).replace(tzinfo=WARSAW)
    W = timedelta(minutes=args.window)

    # 1. Indeks aktywności z sla_log: globalna posortowana lista (dt, cid) = kto kiedy pracował
    acts = []
    for r in _read_jsonl(SLA_LOG):
        c = _cid(r.get("courier_id"))
        if c is None:
            continue
        for k in ("picked_up_at", "delivered_at"):
            dt = _parse_dt(r.get(k))
            if dt is not None:
                acts.append((dt, c))
    acts.sort(key=lambda x: x[0])
    act_dts = [a[0] for a in acts]

    def fleet_in_window(t):
        lo = bisect.bisect_left(act_dts, t - W)
        hi = bisect.bisect_right(act_dts, t + W)
        return {acts[i][1] for i in range(lo, hi)}

    # 2. Przesiew decyzji
    funnel = {"wszystkie": 0, "czasowka/poza_oknem": 0, "nie_PROPOSE": 0, "brak_wyniku": 0,
              "pick_nieaktywny_dzien_lub_okno": 0, "brak_wyboru_flota<min": 0, "MIARODAJNE": 0}
    reliable = []
    # set (cid, day) dla "pracował tego dnia"
    worked_day = set()
    for dt, c in acts:
        worked_day.add((c, dt.astimezone(WARSAW).date().isoformat()))

    for r in _read_jsonl(ETA_CALIB):
        funnel["wszystkie"] += 1
        t = _parse_dt(r.get("picked_up_at"))
        if r.get("was_czasowka") or t is None or not (dfrom <= t < dto):
            funnel["czasowka/poza_oknem"] += 1
            continue
        if r.get("verdict") != "PROPOSE":
            funnel["nie_PROPOSE"] += 1
            continue
        if r.get("sla_ok") is None:
            funnel["brak_wyniku"] += 1
            continue
        best = _cid(r.get("best_courier_id"))
        real = _cid(r.get("real_courier_id"))
        day = t.astimezone(WARSAW).date().isoformat()
        fleet = fleet_in_window(t)
        # KLUCZ: pick Ziomka realnie pracował (aktywność w oknie ±W) — nie był w domu
        if not best or (best, day) not in worked_day or best not in fleet:
            funnel["pick_nieaktywny_dzien_lub_okno"] += 1
            continue
        if len(fleet) < args.min_fleet:
            funnel["brak_wyboru_flota<min"] += 1
            continue
        funnel["MIARODAJNE"] += 1
        reliable.append({
            "oid": r.get("oid"), "picked_up_at": r.get("picked_up_at"), "day": day,
            "ziomek_best_cid": best, "real_cid": real, "agreement": (best == real),
            "real_ontime": r.get("sla_ok"), "real_delivery_min": r.get("real_delivery_min"),
            "predicted_delivery_min": r.get("predicted_delivery_min"), "eta_error_min": r.get("eta_error_min"),
            "is_bundle": r.get("is_bundle"), "bucket": r.get("bucket"), "restaurant": r.get("restaurant"),
            "fleet_active_in_window": len(fleet),
        })

    # 3. Zapis + raport
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for x in reliable:
            fh.write(json.dumps(x, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, args.out)

    print(f"[calibration_screen] okno {args.dfrom}..{args.dto}  aktywność ±{args.window:.0f} min  min_flota {args.min_fleet}")
    print(f"  aktywności sla_log: {len(acts)}")
    print("\n=== LEJEK PRZESIEWU ===")
    for k, v in funnel.items():
        print(f"  {k:32s} {v:6d}")
    if not reliable:
        print("\n  brak miarodajnych — poluzuj progi.")
        return 0

    agree = [x for x in reliable if x["agreement"]]
    over = [x for x in reliable if not x["agreement"]]
    ok = lambda xs: [x for x in xs if x["real_ontime"] is True]
    print(f"\n=== PODZBIÓR MIARODAJNY (n={len(reliable)}) → {args.out} ===")
    print(f"  zgodność Ziomek==koordynator: {len(agree)} = {_pct(len(agree),len(reliable)):.1f}%  (na CZYSTYM podzbiorze, nie surowe 19%)")
    print(f"  on-time gdy WZIĘTO Ziomka:    {len(ok(agree))}/{len(agree)} = {_pct(len(ok(agree)),len(agree)):.1f}%")
    print(f"  on-time gdy NADPISANO:        {len(ok(over))}/{len(over)} = {_pct(len(ok(over)),len(over)):.1f}%")
    print(f"  (oba kurierzy realnie pracowali → różnica = PRAWDZIWA preferencja, nie 'pick był w domu')")
    print("\n  ⚠ progi do strojenia: --window (okno 'pracował') i --min-fleet (realny wybór).")
    print("  Dla okna <12 dni mam też pos_source/pool z shadow (dokładniejszy filtr fikcji) — do dołożenia jeśli chcesz.")
    print("  (read-only; zero wpływu na decyzje/stan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
