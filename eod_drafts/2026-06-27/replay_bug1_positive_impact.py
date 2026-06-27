#!/usr/bin/env python3
"""ETAP 5 (read-only): dowód POZYTYWNEGO wpływu Bug #1 fix.
Dla propozycji ZAJĘTYCH kurierów gdzie blind target < free_at (bug), porównuje:
  blind   = target_pickup_at (dziś, floor do gotowości jedzenia)
  realny  = max(eta_pickup_utc, free_at_utc)   [konserwatywnie, bez członu dojazdu]
  faktyczny = picked_up_at z orders_state.json (gdy proponowany kurier == faktyczny)
Teza: |realny - faktyczny| < |blind - faktyczny| (realny bliżej rzeczywistości).
"""
import json, sys
from datetime import datetime, timezone, timedelta

SD = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
OS_PATH = "/root/.openclaw/workspace/dispatch_state/orders_state.json"
WARSAW = timezone(timedelta(hours=2))  # 27.06 = CEST UTC+2


def p(iso):
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def p_pickup(s):
    # "2026-06-27 14:47:01" Warsaw-naive → UTC
    if not s:
        return None
    try:
        d = datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
        return d.replace(tzinfo=WARSAW).astimezone(timezone.utc)
    except Exception:
        return None


EVENTS_DB = "/root/.openclaw/workspace/dispatch_state/events.db"


def _actuals_from_events():
    """{order_id: (picked_up_utc, courier_id)} z COURIER_PICKED_UP (trwałe, nie pruned)."""
    import sqlite3
    pu, cm = {}, {}
    try:
        c = sqlite3.connect(EVENTS_DB)
        for oid, cid, payload, created in c.execute(
                "select order_id,courier_id,payload,created_at from events "
                "where event_type='COURIER_PICKED_UP'"):
            ts = None
            try:
                ts = p_pickup(json.loads(payload).get("timestamp")) if payload else None
            except Exception:
                ts = None
            ts = ts or p(created)
            if ts is not None:
                pu[str(oid)] = ts
                cm[str(oid)] = str(cid or "")
        c.close()
    except Exception as e:
        print("events.db read err:", e)
    return pu, cm


def main():
    since = sys.argv[1] if len(sys.argv) > 1 else "2026-06-27"
    pumap, cmap = _actuals_from_events()

    rows = []
    for line in open(SD, encoding="utf-8", errors="replace"):
        if '"PROPOSE"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("verdict") != "PROPOSE" or (d.get("ts") or "")[:10] < since:
            continue
        b = d.get("best")
        if not isinstance(b, dict):
            continue
        bag = b.get("bag_context") or []
        fa = p(b.get("free_at_utc"))
        tgt = p(b.get("target_pickup_at"))
        eta = p(b.get("eta_pickup_utc"))
        if not bag or fa is None or tgt is None:
            continue
        if (fa - tgt).total_seconds() / 60.0 <= 0.5:   # tylko bugowe (blind < free_at)
            continue
        realistic = max(eta, fa) if eta else fa
        oid = str(d.get("order_id"))
        actual = pumap.get(oid)
        same_courier = cmap.get(oid) == str(b.get("courier_id"))
        rows.append({
            "oid": oid, "cid": b.get("courier_id"), "rest": d.get("restaurant"),
            "blind": tgt, "realistic": realistic, "free_at": fa,
            "actual": actual, "same": same_courier,
            "corr_min": round((realistic - tgt).total_seconds() / 60.0, 1),
        })

    print(f"=== ETAP 5 Bug #1 — dowód pozytywnego wpływu (od {since}) ===")
    print(f"bugowych propozycji (zajęty, blind<free_at): {len(rows)}")
    corr = [r["corr_min"] for r in rows]
    if corr:
        corr.sort()
        print(f"korekta blind→realny (min): median={corr[len(corr)//2]:.1f} "
              f"min={corr[0]:.1f} max={corr[-1]:.1f}  (realny PÓŹNIEJ = przestaje kłamać)")
    # walidacja vs faktyczny odbiór
    matched = [r for r in rows if r["actual"] is not None and r["same"]]
    print(f"\n--- vs FAKTYCZNY picked_up_at (proponowany==faktyczny kurier): {len(matched)} ---")
    if matched:
        eb = [abs((r["blind"] - r["actual"]).total_seconds()) / 60.0 for r in matched]
        er = [abs((r["realistic"] - r["actual"]).total_seconds()) / 60.0 for r in matched]
        print(f"  |blind - faktyczny|   median={sorted(eb)[len(eb)//2]:.1f} min  avg={sum(eb)/len(eb):.1f}")
        print(f"  |realny - faktyczny|  median={sorted(er)[len(er)//2]:.1f} min  avg={sum(er)/len(er):.1f}")
        better = sum(1 for b, r in zip(eb, er) if r < b)
        print(f"  realny BLIŻEJ rzeczywistości: {better}/{len(matched)}")
    print("\n--- szczegóły ---")
    for r in sorted(rows, key=lambda x: -x["corr_min"])[:12]:
        a = r["actual"].astimezone(WARSAW).strftime("%H:%M") if r["actual"] else "—"
        print(f"  oid={r['oid']} {r['rest'][:18]:18} cid={r['cid']} "
              f"blind={r['blind'].astimezone(WARSAW).strftime('%H:%M')} "
              f"realny={r['realistic'].astimezone(WARSAW).strftime('%H:%M')} "
              f"FAKT={a} (+{r['corr_min']}min){' [ten kurier]' if r['same'] else ''}")


if __name__ == "__main__":
    main()
