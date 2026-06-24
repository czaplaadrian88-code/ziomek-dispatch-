#!/usr/bin/env python3
"""READ-ONLY replay: flip-flagowy pomiar Fix M + Fix K na realnym silniku Ziomka.

baseline = plan_recheck._apply_canon_order_invariants z flagami Fix OFF (stan LIVE dziś).
candidate = ten sam silnik z ENABLE_NONCARRIED_DROPOFF_REORDER=1 + ENABLE_RELAX_COLOC_PICKUP=1.
Worki rekonstruowane z orders_state (historia UTC, momenty przydziału i odbioru).
Mierzy: Δjazda (OSRM), NOWE przekroczenia R6 (twarde SLA), maks. opóźnienie dostawy.
Nic nie zapisuje. 1:1 z tym co wchodzi na produkcję (różnica = dwie flagi)."""
import os, sys, json
os.environ.setdefault("ENABLE_CARRIED_FIRST_RELAX", "1")
os.environ.setdefault("ENABLE_NO_RETURN_TO_DEPARTED_PICKUP", "1")
os.environ.setdefault("ENABLE_PLAN_CANON_ORDER_INVARIANTS", "1")
os.environ.setdefault("ENABLE_CARRIED_AGE_TZ_FIX", "1")
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dispatch_v2 import plan_recheck as PR
from dispatch_v2 import osrm_client
WARSAW = ZoneInfo("Europe/Warsaw")
STATE = "/root/.openclaw/workspace/dispatch_state/orders_state.json"

def _dt(s):
    if not s: return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def _hist_at(o, ev):
    best = None
    for h in o.get("history", []) or []:
        if h.get("event") == ev:
            t = _dt(h.get("at"))
            if t and (best is None or t < best): best = t
    return best

def _ok(c): return isinstance(c, (list, tuple)) and len(c) == 2 and c[0] and c[1]

def build_bags(os_):
    by_c = {}
    for oid, o in os_.items():
        if not isinstance(o, dict) or not o.get("courier_id"): continue
        if not (_ok(o.get("pickup_coords")) and _ok(o.get("delivery_coords"))): continue
        a = _hist_at(o, "COURIER_ASSIGNED") or _dt(o.get("assigned_at"))
        if a is None: continue
        by_c.setdefault(str(o["courier_id"]), []).append({
            "oid": str(oid), "assigned": a,
            "picked": _hist_at(o, "COURIER_PICKED_UP"),
            "delivered": _hist_at(o, "COURIER_DELIVERED") or _dt(o.get("delivered_at")),
            "pc": [float(x) for x in o["pickup_coords"]],
            "dc": [float(x) for x in o["delivery_coords"]],
            "ck": _dt(o.get("czas_kuriera_warsaw"))})
    bags = []
    for cid, ords in by_c.items():
        Ts = sorted({o["assigned"] for o in ords} | {o["picked"] for o in ords if o["picked"]})
        seen = set()
        for T in Ts:
            act = [o for o in ords if o["assigned"] <= T and (o["delivered"] is None or o["delivered"] > T)]
            if len(act) < 2: continue
            evs = sorted([(o["picked"], o["pc"]) for o in ords if o["picked"] and o["picked"] <= T] +
                         [(o["delivered"], o["dc"]) for o in ords if o["delivered"] and o["delivered"] <= T])
            if not evs: continue
            sig = (tuple(sorted(o["oid"] for o in act)),
                   tuple(sorted(o["oid"] for o in act if o["picked"] and o["picked"] <= T)))
            if sig in seen: continue
            seen.add(sig)
            bags.append({"cid": cid, "T": T, "start": evs[-1][1], "act": act})
    return bags

def ostate_and_stops(bag):
    T = bag["T"]; ost = {}
    for o in bag["act"]:
        carried = o["picked"] is not None and o["picked"] <= T
        ost[o["oid"]] = {"status": "picked_up" if carried else "assigned",
                         "pickup_coords": o["pc"], "delivery_coords": o["dc"],
                         "picked_up_at": o["picked"].astimezone(WARSAW).strftime("%Y-%m-%d %H:%M:%S")
                                         if carried else None,
                         "czas_kuriera_warsaw": o["ck"].isoformat() if o["ck"] else None}
    stops = []
    for o in sorted(bag["act"], key=lambda x: x["assigned"]):
        if not (o["picked"] is not None and o["picked"] <= T):
            stops.append({"order_id": o["oid"], "type": "pickup"})
    for o in sorted(bag["act"], key=lambda x: x["assigned"]):
        stops.append({"order_id": o["oid"], "type": "dropoff"})
    return ost, stops

def eval_seq(seq, ost, start, nowmin):
    """Drive (OSRM, kolejne legi) + R6 + czasy dostaw wzdłuż sekwencji."""
    pts = [tuple(start)] + [tuple(ost[s["order_id"]]["pickup_coords"] if s["type"] == "pickup"
                                  else ost[s["order_id"]]["delivery_coords"]) for s in seq]
    m = osrm_client.table(pts, pts)
    if not m: return None
    t = 0.0; drive = 0.0; deliv = {}; pick = {}
    for j, s in enumerate(seq):
        d = (m[j][j + 1] or {}).get("duration_s")
        if d is None or d >= 9e8: return None
        lg = d / 60.0; drive += lg; t += lg
        oid = s["order_id"]; o = ost[oid]
        if s["type"] == "pickup":
            ck = _dt(o.get("czas_kuriera_warsaw"))
            if ck is not None:
                cr = ck.timestamp() / 60.0 - nowmin
                if cr > t: t = cr
            pick[oid] = t; t += 1.0
        else:
            deliv[oid] = t; t += 3.5
    r6 = 0
    for oid, dt in deliv.items():
        bp = pick.get(oid)
        if bp is not None and (dt - bp) > 35.0: r6 += 1
    return drive, r6, deliv

def ids(seq): return [(s["order_id"], s["type"]) for s in seq]

def run():
    os_ = json.load(open(STATE))
    bags = build_bags(os_)
    n_eval = nM = nK = 0
    impr = worse = newR6 = 0
    saved = 0.0; mags = []; rows = []
    for bag in bags:
        ost, stops = ostate_and_stops(bag)
        nowmin = bag["T"].timestamp() / 60.0
        start = bag["start"]
        try:
            PR.ENABLE_NONCARRIED_DROPOFF_REORDER = False
            PR.ENABLE_RELAX_COLOC_PICKUP = False
            base = PR._apply_canon_order_invariants([dict(s) for s in stops], ost, tuple(start), bag["T"])
            PR.ENABLE_NONCARRIED_DROPOFF_REORDER = True
            PR.ENABLE_RELAX_COLOC_PICKUP = True
            cand = PR._apply_canon_order_invariants([dict(s) for s in stops], ost, tuple(start), bag["T"])
        except Exception:
            continue
        n_eval += 1
        if ids(base) == ids(cand): continue
        eb = eval_seq(base, ost, start, nowmin)
        ec = eval_seq(cand, ost, start, nowmin)
        if eb is None or ec is None: continue
        carried = any(ost[s["order_id"]]["status"] == "picked_up" for s in base if s["type"] == "dropoff")
        rule = "K" if carried else "M"
        dsave = eb[0] - ec[0]
        if dsave > 0.05: impr += 1; saved += dsave
        elif dsave < -0.05: worse += 1
        if ec[1] > eb[1]: newR6 += 1
        maxdelay = max((ec[2].get(o, 0) - eb[2].get(o, 0) for o in eb[2]), default=0.0)
        if maxdelay > 0.1: mags.append(round(maxdelay, 1))
        (nK if carried else nM).__class__  # no-op
        if carried: nK += 1
        else: nM += 1
        rows.append({"cid": bag["cid"], "T": bag["T"].astimezone(WARSAW).strftime("%m-%d %H:%M"),
                     "rule": rule, "bd": round(eb[0], 1), "cd": round(ec[0], 1),
                     "save": round(dsave, 1), "maxdelay": round(maxdelay, 1),
                     "r6": f"{eb[1]}->{ec[1]}",
                     "base": ids(base), "cand": ids(cand)})
    print("=" * 80)
    print(f"Worki decyzyjne: {len(bags)} | ocenione: {n_eval} | ZMIANA: {nM+nK} (Fix M: {nM} | Fix K: {nK})")
    print(f"  poprawa jazdy: {impr} | pogorszenie: {worse} | Σ zaoszczędzone: {saved:.1f} min")
    print(f"  NOWE przekroczenia R6 (twarde SLA): {newR6}  <-- musi być 0")
    print(f"  dostawa później vs baseline (miękki koszt, w granicy R6): n={len(mags)} "
          f"med={sorted(mags)[len(mags)//2] if mags else 0:.1f} max={max(mags) if mags else 0:.1f} min")
    if rows:
        print(f"  mediana oszczędności/zmianę: {sorted(r['save'] for r in rows)[len(rows)//2]:.1f} min")
    print("=" * 80)
    for r in sorted(rows, key=lambda r: -r["save"])[:22]:
        print(f"[{r['rule']}] cid={r['cid']} {r['T']} {r['bd']}→{r['cd']} (−{r['save']}) "
              f"maxdelay={r['maxdelay']} R6 {r['r6']}")
        print(f"     base {r['base']}")
        print(f"     cand {r['cand']}")
    json.dump(rows, open("/tmp/claude-0/-root/d6224a44-b307-4e88-87c0-2c1155b82461/scratchpad/reorder_rows.json", "w"),
              indent=1, default=str)

if __name__ == "__main__":
    run()
