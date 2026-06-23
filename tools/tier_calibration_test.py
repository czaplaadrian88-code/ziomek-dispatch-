#!/usr/bin/env python3
"""tier_calibration_test — czy Ziomek myli się dla któregoś TIERU / czy kurierzy źle otierowani?
(Adrian 2026-06-23)

READ-ONLY. Tier (gold/std+/std/slow) steruje DWELL_BY_TIER + speed multiplier (ETA+scoring).
Jeśli tier źle ustawiony LUB kurier źle otierowany → predykcja i ranking dla niego błędne.
courier_tiers.json wygenerowany 2026-04-20 (KWIECIEŃ) → sprawdzamy czy czerwcowa wydajność pasuje.

PART A — per-TIER kalibracja: breach% + błąd predykcji (real−predicted_delivery) per tier.
  Tier z systematycznym błędem → DWELL/speed tego tieru źle skalibrowany.
PART B — per-KURIER mis-tier: czerwcowa wydajność (breach%, median eta_error) vs jego tier.
  Kurier odstający od swojego tieru → kandydat do prze-tierowania. ⚠ confound: trasa/restauracja.

Uruchom:
  cd /root/.openclaw/workspace/scripts
  PYTHONPATH=. /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tools/tier_calibration_test.py
"""
import argparse
import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
BASE = "/root/.openclaw/workspace"
ETA_CALIB = f"{BASE}/dispatch_state/eta_calibration_log.jsonl"
TIERS = f"{BASE}/dispatch_state/courier_tiers.json"
R6 = 35.0
TIER_ORDER = ["gold", "std+", "std", "slow", "new", "?"]


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
    ap.add_argument("--min-courier", type=int, default=25)
    args = ap.parse_args()
    dfrom = datetime.fromisoformat(args.dfrom).replace(tzinfo=WARSAW)
    dto = datetime.fromisoformat(args.dto).replace(tzinfo=WARSAW)

    raw = json.load(open(TIERS))
    cid_tier, cid_name, cid_p90, cid_inactive = {}, {}, {}, {}
    for cid, v in raw.items():
        if cid == "_meta" or not isinstance(v, dict):
            continue
        cid_tier[cid] = (v.get("bag") or {}).get("tier") or "?"
        cid_name[cid] = v.get("name")
        cid_p90[cid] = (v.get("speed") or {}).get("delivery_time_p90_min")
        cid_inactive[cid] = bool(v.get("inactive"))

    rows = []
    for r in _read_jsonl(ETA_CALIB):
        if r.get("was_czasowka"):
            continue
        t = _dt(r.get("picked_up_at"))
        rd, pd = _num(r.get("real_delivery_min")), _num(r.get("predicted_delivery_min"))
        cid = str(r.get("real_courier_id")) if r.get("real_courier_id") is not None else None
        if t is None or not (dfrom <= t < dto) or rd is None or cid is None:
            continue
        rows.append({"cid": cid, "tier": cid_tier.get(cid, "?"), "real": rd, "pred": pd,
                     "err": (rd - pd) if pd is not None else None, "breach": rd > R6})
    n = len(rows)
    print(f"[tier_calibration_test]  {args.dfrom}..{args.dto}  n={n}  (tiery z courier_tiers.json, KWIECIEŃ)")

    # PART A — per tier
    print(f"\n=== PART A — KALIBRACJA PER TIER (czy Ziomek myli się dla któregoś?) ===")
    print(f"  {'tier':6s} {'n':>6s} {'breach%':>8s} {'err_med(real-pred)':>20s} {'real_med':>9s}")
    by_tier = defaultdict(list)
    for r in rows:
        by_tier[r["tier"]].append(r)
    for tr in TIER_ORDER:
        rs = by_tier.get(tr)
        if not rs:
            continue
        br = _pct(sum(1 for r in rs if r["breach"]), len(rs))
        print(f"  {tr:6s} {len(rs):6d} {br:7.1f}% {str(_med([r['err'] for r in rs])):>19} {str(_med([r['real'] for r in rs])):>9}")
    print("  [err_med >0 = real wolniej niż obietnica → DWELL/speed tieru za optymistyczny; <0 = za pesymistyczny]")

    # PART B — per courier mis-tier
    print(f"\n=== PART B — KURIERZY ODSTAJĄCY OD SWOJEGO TIERU (≥{args.min_courier} dostaw, aktywni) ===")
    by_cid = defaultdict(list)
    for r in rows:
        by_cid[r["cid"]].append(r)
    tier_breach = {tr: _pct(sum(1 for r in by_tier.get(tr, []) if r["breach"]), len(by_tier.get(tr, []))) for tr in TIER_ORDER if by_tier.get(tr)}
    cand = []
    for cid, rs in by_cid.items():
        if len(rs) < args.min_courier or cid_inactive.get(cid):
            continue
        tr = cid_tier.get(cid, "?")
        br = _pct(sum(1 for r in rs if r["breach"]), len(rs))
        em = _med([r["err"] for r in rs])
        rm = _med([r["real"] for r in rs])
        delta_vs_tier = round(br - tier_breach.get(tr, br), 1)
        cand.append((cid, cid_name.get(cid), tr, len(rs), br, em, rm, cid_p90.get(cid), delta_vs_tier))
    # najbardziej odstający: breach DUŻO wyższy niż tier-średnia, lub err_med duży dodatni
    print(f"  {'kurier':16s} {'tier':5s} {'n':>4s} {'breach%':>7s} {'vs_tier':>8s} {'err_med':>8s} {'real_med':>8s} {'p90_kwie':>9s}")
    for cid, nm, tr, nn, br, em, rm, p90, dvt in sorted(cand, key=lambda x: -x[8])[:14]:
        flag = "  ⚠MIS-TIER?" if (dvt > 8 and (em or 0) > 5) else ""
        print(f"  {str(nm)[:16]:16s} {tr:5s} {nn:4d} {br:6.1f}% {dvt:+7.1f} {str(em):>8} {str(rm):>8} {str(p90):>9}{flag}")

    print("\n  WERDYKT (do oceny):")
    print("   • PART A: tier z breach% i err_med WYRAŹNIE odstającym → jego DWELL/speed do rekalibracji (silnik).")
    print("   • PART B ⚠MIS-TIER: kurier breach ≫ swój tier + err_med dodatni → prze-tierować (niżej). Confound trasy → zweryfikować z Adrianem (Lekcja: concrete cid↔tier wymaga ACK).")
    print("  (read-only; zero wpływu na decyzje/stan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
