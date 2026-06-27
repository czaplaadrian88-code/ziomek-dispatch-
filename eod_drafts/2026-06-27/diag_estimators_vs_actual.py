#!/usr/bin/env python3
"""DIAGNOZA (read-only): który estymator czasu ODBIORU jest najbliżej FAKTYCZNEGO
odbioru (events.db COURIER_PICKED_UP). Tylko same-courier matched, zajęty kurier.
Kandydaci: blind(target_pickup_at) / free_at / realny(max eta,free_at) /
plan_pu(plan.pickup_at[oid]) / new_pe(new_pickup_eta_iso) / debias(target_pickup_debiased).
Cel: czy JAKIKOLWIEK estymator MIERZALNIE bije blind → czy fix da progres."""
import json, sqlite3, glob, statistics as st
from datetime import datetime, timezone, timedelta

LOGS = ["/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
        "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"]
WAR = timezone(timedelta(hours=2))


def p(s):
    if not s: return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def pw(s):
    if not s: return None
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=WAR).astimezone(timezone.utc)
    except Exception:
        return None


def main():
    c = sqlite3.connect("/root/.openclaw/workspace/dispatch_state/events.db")
    act = {}
    for oid, cid, pl in c.execute("select order_id,courier_id,payload from events where event_type='COURIER_PICKED_UP'"):
        try:
            t = pw(json.loads(pl).get("timestamp")) if pl else None
        except Exception:
            t = None
        if t:
            act[str(oid)] = (t, str(cid or ""))

    EST = ["blind", "free_at", "realny", "plan_pu", "new_pe", "debias"]
    errs = {k: [] for k in EST}
    n_busy = n_match = n_buggy = 0
    win = {k: 0 for k in EST}
    seen = set()

    for path in LOGS:
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except Exception:
            continue
        for line in fh:
            if '"PROPOSE"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("verdict") != "PROPOSE":
                continue
            b = d.get("best") or {}
            bag = b.get("bag_context") or []
            if not bag:
                continue
            oid = str(d.get("order_id"))
            cid = str(b.get("courier_id"))
            a = act.get(oid)
            if not a or a[1] != cid:    # tylko same-courier matched
                continue
            key = (oid, cid, d.get("ts"))
            if key in seen:
                continue
            seen.add(key)
            fakt = a[0]
            fa = p(b.get("free_at_utc")); tgt = p(b.get("target_pickup_at")); eta = p(b.get("eta_pickup_utc"))
            if tgt is None:
                continue
            n_busy += 1
            if fa is not None and (fa - tgt).total_seconds() / 60.0 > 0.5:
                n_buggy += 1
            plan = b.get("plan") or {}
            pa = plan.get("pickup_at") if isinstance(plan.get("pickup_at"), dict) else {}
            cands = {
                "blind": tgt, "free_at": fa,
                "realny": (max(eta, fa) if eta and fa else (fa or eta)),
                "plan_pu": p(pa.get(oid)), "new_pe": p(b.get("new_pickup_eta_iso")),
                "debias": p(b.get("target_pickup_debiased")),
            }
            ce = {}
            for k, v in cands.items():
                if v is not None:
                    e = abs((v - fakt).total_seconds()) / 60.0
                    errs[k].append(e); ce[k] = e
            if ce:
                win[min(ce, key=ce.get)] += 1
            n_match += 1

    print("=== DIAGNOZA estymatorów odbioru vs FAKT (same-courier, zajęty) ===")
    print(f"matched busy: {n_match}  (z czego blind<free_at 'bugowe': {n_buggy})")
    print(f"\n{'estymator':10} {'n':>4} {'mediana':>8} {'śr':>6} {'p90':>6}  (|est-fakt| min)")
    for k in EST:
        e = errs[k]
        if not e:
            print(f"{k:10} {0:>4}  —"); continue
        e2 = sorted(e)
        print(f"{k:10} {len(e):>4} {e2[len(e2)//2]:>8.1f} {sum(e)/len(e):>6.1f} {e2[int(len(e2)*0.9)]:>6.1f}")
    print(f"\n--- ile razy każdy był NAJBLIŻSZY faktu (win) z {n_match} ---")
    for k in sorted(win, key=lambda x: -win[x]):
        print(f"  {k:10} {win[k]:>4}  ({100*win[k]/max(1,n_match):.0f}%)")
    bl = errs["blind"]
    if bl:
        print(f"\n--- WERDYKT: czy któryś MIERZALNIE bije blind (mediana |est-fakt|)? ---")
        med_bl = sorted(bl)[len(bl)//2]
        for k in EST:
            if k == "blind" or not errs[k]:
                continue
            mk = sorted(errs[k])[len(errs[k])//2]
            tag = "LEPSZY" if mk < med_bl - 0.5 else ("~równy" if abs(mk-med_bl) <= 0.5 else "GORSZY")
            print(f"  {k:10} mediana {mk:.1f} vs blind {med_bl:.1f} → {tag}")


if __name__ == "__main__":
    main()
