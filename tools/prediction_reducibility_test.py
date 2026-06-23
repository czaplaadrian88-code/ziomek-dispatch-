#!/usr/bin/env python3
"""prediction_reducibility_test — ile ogona naprawi DOKŁADNIEJSZA predykcja czasu? (Adrian 2026-06-23)

READ-ONLY. Insight Adriana: zamiast ZMIĘKCZAĆ R6, urealnić PREDYKCJĘ — wtedy R6=35 łapie realne
breach i nie odrzuca fałszywie, ogon maleje BEZ obniżania standardu. Pytanie: ile predykcji da
się urealnić PRZY DECYZJI? Test rozbija błąd predykcji na:
  • SYSTEMATYCZNY per-restauracja (Ziomek stale nie doszacowuje restauracji X) → naprawialny
    per-restaurant priorem TERAZ (decision-time).
  • LOSOWY (prep-slip zależny od chwili) → nienaprawialny przy decyzji → tylko real-time „gotowe”.

Mierzy: per-restauracja błąd delivery (real_delivery_min − predicted_delivery_min) median+p90;
oraz ile PORAŻEK i ZASKOCZEŃ siedzi w restauracjach o systematycznym niedoszacowaniu (median > próg).

Uruchom:
  cd /root/.openclaw/workspace/scripts
  PYTHONPATH=. /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tools/prediction_reducibility_test.py
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
R6 = 35.0


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


def _p90(xs):
    xs = sorted(x for x in xs if x is not None)
    return round(xs[min(len(xs) - 1, int(len(xs) * 0.9))], 1) if xs else None


def _pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-01")
    ap.add_argument("--to", dest="dto", default="2026-06-23")
    ap.add_argument("--min-vol", type=int, default=30)
    ap.add_argument("--sys-bias", type=float, default=5.0, help="median niedoszacowania >to = systematyczne")
    args = ap.parse_args()
    dfrom = datetime.fromisoformat(args.dfrom).replace(tzinfo=WARSAW)
    dto = datetime.fromisoformat(args.dto).replace(tzinfo=WARSAW)

    rows = []
    for r in _read_jsonl(ETA_CALIB):
        if r.get("was_czasowka"):
            continue
        t = _dt(r.get("picked_up_at"))
        pd, rd = _num(r.get("predicted_delivery_min")), _num(r.get("real_delivery_min"))
        if t is None or not (dfrom <= t < dto) or pd is None or rd is None:
            continue
        rows.append({"rest": r.get("restaurant") or "?", "pred": pd, "real": rd,
                     "err": rd - pd, "r6max": _num(r.get("r6_max_bag_time_min")), "oid": r.get("oid")})
    n = len(rows)
    fails = [r for r in rows if r["real"] > R6]
    surp = [r for r in fails if (r["r6max"] or 0) <= R6]
    print(f"[prediction_reducibility_test]  {args.dfrom}..{args.dto}  n={n}  porażki {len(fails)}  zaskoczenia {len(surp)}")

    # globalny błąd predykcji delivery
    print(f"\n=== Błąd predykcji DELIVERY (real − predicted_delivery_min) ===")
    print(f"  cały zbiór: median {_med([r['err'] for r in rows])}  p90 {_p90([r['err'] for r in rows])}")
    print(f"  na PORAŻKACH: median {_med([r['err'] for r in fails])}  p90 {_p90([r['err'] for r in fails])}  (o tyle real > obiecane)")

    # per-restauracja: systematyczny bias?
    by = defaultdict(list)
    for r in rows:
        by[r["rest"]].append(r)
    sys_rests = {}
    for rest, rs in by.items():
        if len(rs) < args.min_vol:
            continue
        med = _med([r["err"] for r in rs])
        sys_rests[rest] = (med, len(rs), _pct(sum(1 for r in rs if r["real"] > R6), len(rs)))
    # systematycznie niedoszacowane
    under = {k: v for k, v in sys_rests.items() if v[0] is not None and v[0] > args.sys_bias}
    print(f"\n=== Per-restauracja (≥{args.min_vol} dostaw): systematyczne NIEDOSZACOWANIE (median err > +{args.sys_bias:.0f}) ===")
    print(f"  restauracji systematycznie niedoszacowanych: {len(under)} z {len(sys_rests)}")
    for rest, (med, vol, br) in sorted(under.items(), key=lambda x: -x[1][0])[:12]:
        print(f"    {rest[:30]:30s} median_err {med:+5.1f}  breach {br:5.1f}%  (n={vol})")

    # ile ogona NAPRAWIALNE per-restaurant priorem
    under_rests = set(under)
    fail_in_under = sum(1 for r in fails if r["rest"] in under_rests)
    surp_in_under = sum(1 for r in surp if r["rest"] in under_rests)
    # kontrfaktyk: jeśli dodamy per-rest median_err do predykcji, ile zaskoczeń Ziomek by PRZEWIDZIAŁ (pred+bias>35)
    would_know = 0
    for r in surp:
        bias = sys_rests.get(r["rest"], (0, 0, 0))[0] or 0
        if r["rest"] in under_rests and (r["r6max"] or 0) + bias > R6:
            would_know += 1
    print(f"\n=== ILE OGONA NAPRAWIALNE DOKŁADNIEJSZĄ PREDYKCJĄ (per-restaurant prior) ===")
    print(f"  porażki w restauracjach systematycznie niedoszacowanych: {fail_in_under}/{len(fails)} = {_pct(fail_in_under,len(fails))}%")
    print(f"  ZASKOCZENIA tamże: {surp_in_under}/{len(surp)} = {_pct(surp_in_under,len(surp))}%")
    print(f"  zaskoczenia które Ziomek by PRZEWIDZIAŁ po dodaniu per-rest biasu (r6max+bias>35): {would_know}/{len(surp)} = {_pct(would_know,len(surp))}%")
    print(f"  → reszta zaskoczeń ({_pct(len(surp)-would_know,len(surp))}%) = LOSOWY prep-slip → tylko real-time „gotowe”.")

    print("\n  WERDYKT (do oceny):")
    print("   • Jeśli duży % zaskoczeń w systematycznie-niedoszacowanych restauracjach → per-restaurant prep prior")
    print("     URELNIA predykcję → R6=35 zostaje, ogon maleje (ścieżka Adriana, lepsza niż zmiękczanie).")
    print("   • Jeśli mały % → predykcja nieredukowalna przy decyzji → real-time „gotowe” jedyny fix dla tej części.")
    print("  (read-only; zero wpływu na decyzje/stan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
