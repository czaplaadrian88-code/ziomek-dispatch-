#!/usr/bin/env python3
"""r6_overpessimism_test — czy twarda bramka R6 (35 min) jest ZA OSTRA? (Adrian 2026-06-23)

READ-ONLY. Analiza ogona ujawniła: 815 „przewidzianych breach" (r6_max>35 przy decyzji)
dostarczyło NA CZAS vs 147 trafnych (~85% fałszywych alarmów). R6 to twarda bramka (odrzuca
kandydata) → fałszywe odrzucenia wypychają w best-effort / gorszy pick. Test:

PART A — KALIBRACJA: bin r6_max_bag_time_min (predykcja przy decyzji) × realny breach (real>35).
  Jeśli predykcja [35,40) realnie psuje się RZADKO → próg 35 za ostry, relaks do ~40 bezpieczny.
  ⚠ r6_max = max-po-bagu (czasem bag-mate), real = ten order; proxy, ale to wielkość której bramka używa.

PART B — LEJEK BEST-EFFORT: ile decyzji best-effort (pool_feasible=0) miałoby ≥1 feasible kuriera,
  gdyby próg R6 = 38 / 40 (kandydat NO z r6_max w (35,T]). = ile best-effort uratowałby relaks.

Uruchom:
  cd /root/.openclaw/workspace/scripts
  PYTHONPATH=. /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tools/r6_overpessimism_test.py
"""
import argparse
import json
import os
import statistics as st
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
BASE = "/root/.openclaw/workspace"
ETA_CALIB = f"{BASE}/dispatch_state/eta_calibration_log.jsonl"

# L1.2 (2026-07-02): odczyt shadow_decisions ROTATION-AWARE przez kanon
# (_rotated_logs/ledger_io) — stary hardkod [żywy, .1] gubił .2.gz po rotacji
# (logrotate size 100M / daily + delaycompress). files_in_window daje pełny
# łańcuch (.N.gz→.1→żywy) chronologicznie; ścieżka = ledger_io.LEDGER (jedno
# źródło). Dedup po oid (seen set) = first-wins (0 kolizji między plikami w oknie
# → identycznie). Per-rekord filtr okna [dfrom,dto) NIETKNIĘTY, metryki BEZ ZMIAN.
try:
    from dispatch_v2.tools import _rotated_logs, ledger_io
except ImportError:
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
    from dispatch_v2.tools import _rotated_logs, ledger_io

SHADOW_LOGS = _rotated_logs.files_in_window(ledger_io.LEDGER["shadow"])
R6 = 35.0


def _read_jsonl(path):
    if not os.path.exists(path):
        return
    for line in _rotated_logs.open_maybe_gz(path):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue


def _dt(s):
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        return d.replace(tzinfo=WARSAW) if d.tzinfo is None else d
    except (ValueError, TypeError):
        return None


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 1) if xs else None


def _pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-01")
    ap.add_argument("--to", dest="dto", default="2026-06-23")
    args = ap.parse_args()
    dfrom = datetime.fromisoformat(args.dfrom).replace(tzinfo=WARSAW)
    dto = datetime.fromisoformat(args.dto).replace(tzinfo=WARSAW)

    # PART A — kalibracja R6 (eta_calibration)
    rows = []
    for r in _read_jsonl(ETA_CALIB):
        if r.get("was_czasowka"):
            continue
        t = _dt(r.get("picked_up_at"))
        if t is None or not (dfrom <= t < dto):
            continue
        rm, rd = _num(r.get("r6_max_bag_time_min")), _num(r.get("real_delivery_min"))
        if rm is not None and rd is not None:
            rows.append((rm, rd))
    print(f"[r6_overpessimism_test]  {args.dfrom}..{args.dto}  rekordów z r6_max+real: {len(rows)}")
    print(f"\n=== PART A — KALIBRACJA R6 (predykcja r6_max × realny breach) ===")
    print(f"  {'r6_max przewidz.':18s} {'n':>6s} {'real-breach%':>13s} {'real-czas med':>14s}")
    bins = [(0, 25), (25, 30), (30, 35), (35, 40), (40, 45), (45, 999)]
    for lo, hi in bins:
        sub = [(rm, rd) for rm, rd in rows if lo <= rm < hi]
        br = sum(1 for _, rd in sub if rd > R6)
        lbl = f"{lo}-{hi if hi < 999 else '∞'}"
        if sub:
            print(f"  {lbl:18s} {len(sub):6d} {_pct(br,len(sub)):12.1f}% {_med([rd for _,rd in sub]):13}")

    # fałszywe alarmy przy różnych progach
    print(f"\n  Fałszywe alarmy bramki (predykcja>próg ale real≤35) wg progu:")
    for thr in (35.0, 38.0, 40.0, 42.0):
        flagged = [(rm, rd) for rm, rd in rows if rm > thr]
        fa = sum(1 for _, rd in flagged if rd <= R6)
        tp = sum(1 for _, rd in flagged if rd > R6)
        print(f"    próg {thr:.0f}: oznaczonych {len(flagged):4d}  fałszywych(real≤35) {fa:4d} ({_pct(fa,len(flagged))}%)  trafnych {tp}")
    # realny breach kandydatów [35,40) — czy relaks bezpieczny
    band = [(rm, rd) for rm, rd in rows if 35 <= rm < 40]
    if band:
        print(f"  → pasmo r6_max [35,40): n={len(band)}, REALNY breach {_pct(sum(1 for _,rd in band if rd>R6),len(band))}%  (niski = relaks do 40 bezpieczny)")

    # PART B — lejek best-effort (shadow)
    print(f"\n=== PART B — LEJEK BEST-EFFORT (shadow): ile uratowałby relaks R6 ===")
    be_total = 0
    rescuable = {38: 0, 40: 0}
    seen = set()
    for path in SHADOW_LOGS:
        for r in _read_jsonl(path):
            ts = _dt(r.get("ts"))
            if ts is None or not (dfrom <= ts < dto):
                continue
            oid = str(r.get("order_id"))
            if oid in seen or r.get("verdict") != "PROPOSE":
                continue
            seen.add(oid)
            best = r.get("best") or {}
            is_be = bool(best.get("best_effort")) or (_num(r.get("pool_feasible_count")) == 0)
            if not is_be:
                continue
            be_total += 1
            # kandydaci odrzuceni z r6_max w (35,T] = uratowalni relaksem do T
            cands = [best] + (r.get("alternatives") or [])
            for T in (38, 40):
                if any(c.get("feasibility") == "NO" and (_num(c.get("r6_max_bag_time_min")) or 0) > 35
                       and (_num(c.get("r6_max_bag_time_min")) or 999) <= T for c in cands):
                    rescuable[T] += 1
    print(f"  decyzji best-effort (pool=0 / best_effort): {be_total}")
    for T in (38, 40):
        print(f"    z ≥1 kandydatem NO r6_max∈(35,{T}] (uratowalny relaksem R6→{T}): {rescuable[T]} = {_pct(rescuable[T],be_total)}% best-effortów")

    print("\n  WERDYKT (do oceny):")
    print("   • [35,40) realny-breach NISKI (np. <25%) + dużo best-effortów uratowalnych → R6 za ostry, relaks/zmiękczenie warte shadow-testu w silniku.")
    print("   • [35,40) realny-breach WYSOKI lub mało uratowalnych → R6=35 słuszny, nie ruszać.")
    print("  ⚠ r6_max = bag-max proxy; pełny flip = shadow w feasibility_v2 z monitorem realnego R6.")
    print("  (read-only; zero wpływu na decyzje/stan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
