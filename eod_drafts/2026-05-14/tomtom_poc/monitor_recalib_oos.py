"""A2 — MONITOR recalib OUT-OF-SAMPLE (walidacja na żywo, $0, read-only).

Werdykt recalib (06-05) był CZĘŚCIOWO IN-SAMPLE (krzywa wyliczona z tych samych
~595 tropów). Ten monitor zamyka tę lukę: bierze TYLKO tropy z odbiorem PO dacie
treningu (--since, default 2026-06-04) = czysty OUT-OF-SAMPLE i sprawdza, czy
krzywa recalib (LIVE w common.py) trzyma przewagę nad starą tabelą V326 na
ŚWIEŻYCH danych, których nie widziała.

Czyta ŻYWĄ tabelę z dispatch_v2.common (fail-soft fallback do kopii), więc po
ewentualnym wdrożeniu weekendu monitoruje też weekend automatycznie.

Metryki (surowe, bez bias-correction — produkcja nie koryguje biasu):
  bias (signed mean err) · rawMAE · rawRMSE · win% recalib · trend per-dzień.

CLI:
  python3 monitor_recalib_oos.py                 # OOS od 2026-06-04, weekday
  python3 monitor_recalib_oos.py --since 2026-06-04 --notify
"""
import argparse
import json
import math
import os
import statistics
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "rw_results.jsonl")
GROUND_TRUTH = os.path.join(HERE, "trips_realworld.jsonl")
WARSAW = ZoneInfo("Europe/Warsaw")
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

# STARA tabela weekday (pre-recalib, V3.27.3 TASK G) — baseline A/B.
OLD_WD = [
    (0, 6, 1.0), (6, 8, 1.0), (8, 10, 1.1), (10, 12, 1.1), (12, 13, 1.2),
    (13, 14, 1.2), (14, 15, 1.2), (15, 16, 1.5), (16, 17, 1.3), (17, 19, 1.2),
    (19, 20, 1.1), (20, 21, 1.0), (21, 24, 1.0),
]
# STARE tabele weekend (pre-recalib-weekend 2026-06-12, V3.27 Bug X) — baseline A/B.
OLD_SAT = [(0, 12, 1.0), (12, 15, 1.1), (15, 17, 1.2), (17, 21, 1.2), (21, 24, 1.0)]
OLD_SUN = [(0, 24, 1.0)]
# Fallback gdy import common padnie (kopia LIVE 2026-06-05).
_FALLBACK_LIVE = {
    "weekday": [
        (0, 9, 1.0), (9, 10, 1.15), (10, 12, 1.25), (12, 13, 1.4), (13, 14, 1.5),
        (14, 15, 1.35), (15, 17, 1.55), (17, 18, 1.25), (18, 19, 1.25),
        (19, 20, 1.25), (20, 21, 1.1), (21, 24, 1.05),
    ],
    "saturday": [(0, 12, 1.0), (12, 15, 1.1), (15, 17, 1.2), (17, 21, 1.2), (21, 24, 1.0)],
    "sunday": [(0, 24, 1.0)],
}


def _live_table():
    try:
        from dispatch_v2 import common as C
        return C.V326_OSRM_TRAFFIC_TABLE, "common.py (LIVE)"
    except Exception as e:
        print(f"[warn] import common padł ({e}) → fallback kopia", file=sys.stderr)
        return _FALLBACK_LIVE, "fallback-copy"


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


def _rmse(xs):
    return math.sqrt(statistics.mean(x * x for x in xs)) if xs else 0.0


def _metrics(records, key_old, key_live):
    eo = [r[key_old] for r in records]
    el = [r[key_live] for r in records]
    wins = sum(1 for a, b in zip(eo, el) if abs(b) < abs(a))
    ties = sum(1 for a, b in zip(eo, el) if abs(b) == abs(a))
    return {
        "n": len(records),
        "old_bias": statistics.mean(eo), "live_bias": statistics.mean(el),
        "old_mae": statistics.mean(abs(x) for x in eo),
        "live_mae": statistics.mean(abs(x) for x in el),
        "old_rmse": _rmse(eo), "live_rmse": _rmse(el),
        "winrate": (wins + 0.5 * ties) / len(records) if records else 0.0,
    }


def run(since, day_kind="weekday"):
    """day_kind: 'weekday' (recalib 06-05) lub 'weekend' (recalib sob/ndz 06-12)."""
    gt = {}
    for r in _load_jsonl(GROUND_TRUTH):
        if "tier2_underflow" not in (r.get("problems") or []):
            gt[r["oid"]] = r
    live, live_src = _live_table()
    live_wd = live.get("weekday", _FALLBACK_LIVE["weekday"])
    live_sat = live.get("saturday", _FALLBACK_LIVE["saturday"])
    live_sun = live.get("sunday", _FALLBACK_LIVE["sunday"])

    since_d = datetime.strptime(since, "%Y-%m-%d").date()
    recs, by_date = [], {}
    for p in _load_jsonl(RESULTS):
        g = gt.get(p["oid"])
        ff = p.get("osrm_freeflow_min")
        if not g or ff is None or g.get("ground_truth_drive_min") is None:
            continue
        pu = datetime.fromtimestamp(g["pu_epoch"], WARSAW)
        wd = pu.weekday()
        if day_kind == "weekday":
            if wd > 4:
                continue
            old_t, live_t = OLD_WD, live_wd
        else:                       # weekend
            if wd <= 4:
                continue
            old_t = OLD_SAT if wd == 5 else OLD_SUN
            live_t = live_sat if wd == 5 else live_sun
        if pu.date() < since_d:     # OUT-OF-SAMPLE gate
            continue
        h = g["hour_warsaw"]
        gtv = g["ground_truth_drive_min"]
        rec = {
            "oid": p["oid"], "date": pu.date().isoformat(), "hour": h, "tier": g["tier"],
            "err_old": ff * _lookup(old_t, h) - gtv,
            "err_live": ff * _lookup(live_t, h) - gtv,
        }
        recs.append(rec)
        by_date.setdefault(rec["date"], []).append(rec)

    lines = []
    lines.append("=" * 70)
    lines.append(f"A2 — MONITOR recalib OUT-OF-SAMPLE ({day_kind}, świeże dane po deployu)")
    lines.append("=" * 70)
    lines.append(f"żywa tabela: {live_src}  |  OOS od: {since}  |  tropów OOS: {len(recs)}")
    if not recs:
        lines.append("\nBrak tropów OOS w oknie — za wcześnie, zbieraj dalej (crony lecą).")
        return "\n".join(lines), None

    m = _metrics(recs, "err_old", "err_live")
    lines.append("")
    lines.append(f"{'metryka':12} {'STARA V326':>12} {'RECALIB(LIVE)':>14}  {'Δ':>8}")
    lines.append("-" * 70)
    lines.append(f"{'bias':12} {m['old_bias']:>+12.2f} {m['live_bias']:>+14.2f}  "
                 f"{abs(m['live_bias'])-abs(m['old_bias']):>+8.2f}")
    lines.append(f"{'rawMAE':12} {m['old_mae']:>12.2f} {m['live_mae']:>14.2f}  "
                 f"{m['live_mae']-m['old_mae']:>+8.2f}")
    lines.append(f"{'rawRMSE':12} {m['old_rmse']:>12.2f} {m['live_rmse']:>14.2f}  "
                 f"{m['live_rmse']-m['old_rmse']:>+8.2f}")
    lines.append(f"{'recalib win%':12} {'':>12} {m['winrate']*100:>13.0f}%")

    # trend per-dzień
    lines.append("")
    lines.append("TREND per-dzień (bias stara → recalib | n):")
    for d in sorted(by_date):
        dm = _metrics(by_date[d], "err_old", "err_live")
        lines.append(f"  {d}  bias {dm['old_bias']:>+6.2f} → {dm['live_bias']:>+6.2f}"
                     f"  | MAE {dm['old_mae']:.2f}→{dm['live_mae']:.2f} | n={dm['n']}")

    # werdykt
    lines.append("")
    lines.append("-" * 70)
    bias_holds = abs(m["live_bias"]) < abs(m["old_bias"]) - 0.10
    mae_ok = m["live_mae"] <= m["old_mae"] + 0.05
    thin = m["n"] < 25
    if bias_holds and mae_ok:
        verd = "✅ RECALIB TRZYMA przewagę OOS — nie rollbackować"
    elif not bias_holds and m["live_bias"] * m["old_bias"] > 0 and abs(m["live_bias"]) > abs(m["old_bias"]) + 0.3:
        verd = "🔴 REGRESJA OOS — recalib gorszy na świeżych danych, rozważ rollback"
    else:
        verd = "≈ neutralnie OOS — trzymaj, zbieraj dalej"
    if thin:
        verd += f"  ⚠ n={m['n']}<25 SYGNAŁ SŁABY (info)"
    lines.append("WERDYKT OOS: " + verd)
    lines.append("-" * 70)
    return "\n".join(lines), {"metrics": m, "verdict": verd, "thin": thin}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-06-04",
                    help="data odcięcia OOS (YYYY-MM-DD); domyślnie dzień po deployu")
    ap.add_argument("--day-kind", choices=("weekday", "weekend"), default="weekday",
                    help="weekday=recalib 06-05, weekend=recalib sob/ndz 06-12 "
                         "(dla weekend użyj --since 2026-06-13)")
    ap.add_argument("--notify", action="store_true", help="wyślij werdykt na Telegram")
    args = ap.parse_args()

    out, res = run(args.since, args.day_kind)
    print(out)
    stamp = res is not None
    suffix = "" if args.day_kind == "weekday" else "_weekend"
    try:
        with open(os.path.join(HERE, f"monitor_recalib_oos_latest{suffix}.txt"), "w",
                  encoding="utf-8") as f:
            f.write(out)
    except Exception:
        pass
    if args.notify:
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            ok = send_admin_alert("📊 Recalib OOS monitor\n\n" + out[-3500:])
            print(f"\ntelegram send_admin_alert={ok}")
        except Exception as e:
            print(f"\ntelegram fail: {e}")
    return 0 if stamp else 1


if __name__ == "__main__":
    sys.exit(main())
