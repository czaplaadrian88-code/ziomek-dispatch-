#!/usr/bin/env python3
"""pos_age_outcome — czy WIEK pozycji kuriera przewiduje gorszą dostawę? (rec#3 no-GPS, 2026-06-23)

READ-ONLY, zero zmian kodu/decyzji. Mierzy hipotezę lewara "no-GPS pos_age trust-discount":
"kandydat z NIEŚWIEŻĄ wnioskowaną pozycją dowozi gorzej". Jeśli prawda → warto zapełnić
`pos_age_min` dla źródeł `last_*` (39% wygranych, dziś None) i zbudować zniżkę zaufania.
Jeśli nie → lewar słabszy niż zakładano (NIE budować).

Test korzysta z faktu, że źródła gps/last_delivered/last_picked_up_recent/store JUŻ logują
`best.pos_age_min`. Join: shadow_decisions (oid → best.pos_source, best.pos_age_min, best_cid)
× eta_calibration_log (oid → real_courier, sla_ok, real_delivery_min, matched).

CZYSTY test = podzbiór ZGODNOŚCI (best==real, czyli pick Ziomka FAKTYCZNIE pojechał) →
wtedy wiek best == wiek kuriera, którego wynik znamy. Kubełkuje po wieku, liczy on-time%.

Uruchom:
  cd /root/.openclaw/workspace/scripts
  PYTHONPATH=. /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tools/pos_age_outcome.py --days 14
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
BASE = "/root/.openclaw/workspace"
SHADOW_LOGS = [f"{BASE}/scripts/logs/shadow_decisions.jsonl", f"{BASE}/scripts/logs/shadow_decisions.jsonl.1"]
ETA_CALIB = f"{BASE}/dispatch_state/eta_calibration_log.jsonl"
AGE_BUCKETS = [(0, 5), (5, 15), (15, 30), (30, 999)]


def _read_jsonl(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
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
        if dt.tzinfo is None:  # eta_calibration picked_up_at = Warsaw-naive
            dt = dt.replace(tzinfo=WARSAW)
        return dt
    except (ValueError, TypeError):
        return None


def _cid(v):
    return None if v is None else str(v).strip()


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _pct(n, d):
    return (100.0 * n / d) if d else 0.0


def main():
    ap = argparse.ArgumentParser(description="Czy wiek pozycji przewiduje gorszą dostawę? (read-only)")
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()
    now = datetime.now(WARSAW)
    cutoff = now - timedelta(days=args.days)

    # shadow: oid -> best.{pos_source, pos_age_min, courier_id} (pierwsza/najwcześniejsza decyzja)
    sh = {}
    for path in SHADOW_LOGS:
        for r in _read_jsonl(path):
            oid = r.get("order_id")
            ts = _parse_dt(r.get("ts"))
            if oid is None or ts is None or ts < cutoff:
                continue
            b = r.get("best") or {}
            rec = {"src": b.get("pos_source"), "age": _num(b.get("pos_age_min")), "cid": _cid(b.get("courier_id"))}
            cur = sh.get(str(oid))
            if cur is None or ts < cur["_ts"]:
                rec["_ts"] = ts
                sh[str(oid)] = rec

    # eta_calibration: oid -> outcome
    out = {}
    for r in _read_jsonl(ETA_CALIB):
        if r.get("was_czasowka"):
            continue
        pu = _parse_dt(r.get("picked_up_at"))
        if pu is None or pu < cutoff:
            continue
        out[str(r.get("oid"))] = {
            "real_cid": _cid(r.get("real_courier_id")),
            "sla_ok": r.get("sla_ok"),
            "real_min": _num(r.get("real_delivery_min")),
        }

    joined = [(sh[o], out[o]) for o in sh if o in out]
    print(f"[pos_age_outcome] {now.isoformat()}  okno={args.days}d")
    print(f"  shadow oid: {len(sh)}  | z wynikiem (eta_calib): {len(out)}  | złączone: {len(joined)}")
    if not joined:
        print("  brak złączeń — nic do oceny.")
        return 0

    # 1) POKRYCIE pos_age wg źródła (pokazuje lukę 'last_*' = None)
    by_src = {}
    for s, _o in joined:
        src = s["src"] or "?"
        d = by_src.setdefault(src, {"n": 0, "age": 0})
        d["n"] += 1
        if s["age"] is not None:
            d["age"] += 1
    print("\n=== 1. POKRYCIE wieku pozycji wg źródła (None = luka do zapełnienia) ===")
    for src, d in sorted(by_src.items(), key=lambda x: -x[1]["n"]):
        print(f"  {src:24s} n={d['n']:4d}  ma_wiek={d['age']:4d} ({_pct(d['age'],d['n']):3.0f}%)")

    # 2) CZYSTY TEST: zgodność (best==real) + wiek znany → on-time% wg kubełka wieku
    clean = [(s, o) for s, o in joined
             if s["age"] is not None and s["cid"] and o["real_cid"]
             and s["cid"] == o["real_cid"] and o["sla_ok"] is not None]
    print(f"\n=== 2. CZYSTY TEST (best==real, wiek znany): on-time% wg wieku pozycji  [n={len(clean)}] ===")
    if len(clean) < 20:
        print("  ⚠ za mało danych na czysty test (<20) — patrz test szeroki niżej.")
    for lo, hi in AGE_BUCKETS:
        sub = [o for s, o in clean if lo <= s["age"] < hi]
        ok = [o for o in sub if o["sla_ok"] is True]
        lbl = f"{lo}-{hi if hi < 999 else '∞'} min"
        if sub:
            print(f"  wiek {lbl:10s}  on-time {len(ok):3d}/{len(sub):3d} = {_pct(len(ok),len(sub)):5.1f}%")
        else:
            print(f"  wiek {lbl:10s}  (brak)")

    # 3) TEST SZEROKI (wszystkie z wiekiem; outcome realnego kuriera — kontekst, słabszy)
    broad = [(s, o) for s, o in joined if s["age"] is not None and o["sla_ok"] is not None]
    print(f"\n=== 3. TEST SZEROKI (każdy z wiekiem, outcome realnego kuriera — kontekst) [n={len(broad)}] ===")
    for lo, hi in AGE_BUCKETS:
        sub = [o for s, o in broad if lo <= s["age"] < hi]
        ok = [o for o in sub if o["sla_ok"] is True]
        lbl = f"{lo}-{hi if hi < 999 else '∞'} min"
        if sub:
            print(f"  wiek {lbl:10s}  on-time {len(ok):4d}/{len(sub):4d} = {_pct(len(ok),len(sub)):5.1f}%")
        else:
            print(f"  wiek {lbl:10s}  (brak)")

    print("\n  WERDYKT: jeśli on-time WYRAŹNIE spada z wiekiem → lewar realny (zapełnij last_*, zbuduj zniżkę).")
    print("  Jeśli płasko → wiek nie przewiduje jakości → NIE budować (kolejne 'oczywiste' się odwraca).")
    print("  (read-only; zero wpływu na decyzje/stan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
