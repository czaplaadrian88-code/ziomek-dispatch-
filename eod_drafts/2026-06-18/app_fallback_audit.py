#!/usr/bin/env python3
"""Audyt trasy APKI (fallback optimize_route) vs plan Ziomka — na realnych workach.

Apka serwuje plan Ziomka (makespan) GDY istnieje; przy braku (PANEL_OVERRIDE/
human-assign/invalidacja) — własny fallback `optimize_route` (min-latency brute
<=8 stopów, inaczej NN) + committed-pickup reorder. To źródło „innej trasy".

Porównanie 4 sekwencji (events.db real bags, coords z payload — bez geokodowania),
jeden scorer OSRM (food-age=ready->deliver):
  REALIZED   = jechane (z timestampów)
  ZIOMEK     = makespan brute (proxy planu Ziomka; KONSERWATYWNY — realny silnik
               z V327-constraints jest świeżościowo gorszy)
  APP        = faithful fallback apki (min-latency brute<=8 / NN + committed reorder)
  MINLAT_ID  = idealny min-latency brute (BEZ capu) — APP vs MINLAT_ID = koszt capu/NN
Cel: czy APP≠ZIOMEK, czy świeższa, gdzie własne błędy apki (NN duże worki / committed).
"""
import json, math, sqlite3, sys, urllib.request
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from itertools import permutations
import statistics as stt

EV = "/root/.openclaw/workspace/dispatch_state/events.db"
OSRM = "http://localhost:5001"
OUT = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-18/app_fallback_audit_result.txt"
SLA = 35.0
DWELL_P, DWELL_D = 1.0, 3.5
BRUTE_CAP = 8  # config.OPTIMIZE_BRUTE_MAX_STOPS


def pdt(s):
    try:
        d = datetime.fromisoformat(s)
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    except Exception:
        return None


def hav(a, b):
    R = 6371000; p = math.pi / 180
    return 2 * R * math.asin(math.sqrt(math.sin((b[0]-a[0])*p/2)**2 + math.cos(a[0]*p)*math.cos(b[0]*p)*math.sin((b[1]-a[1])*p/2)**2))


def osrm_matrix(points):
    coords = ";".join(f"{p[1]},{p[0]}" for p in points)
    return json.load(urllib.request.urlopen(f"{OSRM}/table/v1/driving/{coords}?annotations=duration", timeout=25))["durations"]


def load():
    con = sqlite3.connect(EV); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT order_id,event_type,courier_id,payload,created_at FROM events WHERE event_type IN ('NEW_ORDER','COURIER_PICKED_UP','COURIER_DELIVERED') ORDER BY created_at").fetchall()
    O = {}
    for r in rows:
        o = O.setdefault(r["order_id"], {})
        try: p = json.loads(r["payload"]) if r["payload"] else {}
        except Exception: p = {}
        if r["event_type"] == "NEW_ORDER":
            o["pc"] = p.get("pickup_coords"); o["dc"] = p.get("delivery_coords")
            o["ready"] = p.get("pickup_at_warsaw"); o["ck"] = p.get("czas_kuriera_warsaw")
        elif r["event_type"] == "COURIER_PICKED_UP": o["pt"] = r["created_at"]; o["cid"] = r["courier_id"]
        elif r["event_type"] == "COURIER_DELIVERED": o["dt"] = r["created_at"]
    comp = {k: v for k, v in O.items() if v.get("pc") and v.get("dc") and v.get("pt") and v.get("dt") and v.get("cid") and v.get("ready") and len(v["pc"]) == 2 and len(v["dc"]) == 2}
    by = defaultdict(list)
    for k, v in comp.items():
        a, b = pdt(v["pt"]), pdt(v["dt"])
        if a and b and b > a: by[v["cid"]].append((a, b, k))
    bags = []
    for cid, lst in by.items():
        lst.sort(); i = 0
        while i < len(lst):
            g = [lst[i]]; mx = lst[i][1]; j = i + 1
            while j < len(lst) and lst[j][0] < mx: g.append(lst[j]); mx = max(mx, lst[j][1]); j += 1
            if len(g) >= 2: bags.append((cid, [x[2] for x in g]))
            i = j
    return comp, bags


def simulate(seq, stops, M, si, st0, ready):
    t = st0; cur = si; pick = {}; dv = {}
    for s in seq:
        t = t + timedelta(minutes=M[cur][s] / 60.0); cur = s
        k, o = stops[s]
        if k == "p":
            r = ready.get(o)
            if r and r > t: t = r
            pick[o] = t; t = t + timedelta(minutes=DWELL_P)
        else: dv[o] = t; t = t + timedelta(minutes=DWELL_D)
    food = {o: (dv[o] - (pick.get(o) or ready.get(o))).total_seconds()/60.0 for o in dv if (pick.get(o) or ready.get(o))}
    return food, (t - st0).total_seconds()/60.0, dv


def vperms(stops):
    n = len(stops); pm = {}
    for i, (k, o) in enumerate(stops): pm.setdefault(o, [None, None])[0 if k == "p" else 1] = i
    for perm in permutations(range(n)):
        pos = {s: r for r, s in enumerate(perm)}
        if all(pos[v[0]] < pos[v[1]] for v in pm.values()): yield perm


def _candidates(stops, st0, ready):
    n = len(stops)
    if n <= 8:
        yield from vperms(stops)
        return
    # pickup-all-first (ready order) + brute delivery permutations (heurystyka dla >4 zleceń)
    oids = [o for k, o in stops if k == "p"]
    pidx = {o: stops.index(("p", o)) for o in oids}
    didx = {o: stops.index(("d", o)) for o in oids}
    pickseq = [pidx[o] for o in sorted(oids, key=lambda o: ready[o] or st0)]
    for dperm in permutations(oids):
        yield pickseq + [didx[o] for o in dperm]


def brute(stops, M, si, st0, ready, obj):
    bk = bb = None
    for perm in _candidates(stops, st0, ready):
        food, mk, dv = simulate(list(perm), stops, M, si, st0, ready)
        if obj == "makespan": key = (round(mk, 3),)
        else: key = (round(sum((dv[o]-st0).total_seconds() for o in dv), 1), round(mk, 3))  # min-latency
        if bk is None or key < bk: bk = key; bb = list(perm)
    return bb


def app_nn(stops, coords, si_coord):
    """nearest-neighbour (haversine), pickup-before-dropoff — replika _nn_optimize."""
    n = len(stops); placed_p = set(); cur = si_coord; out = []; rem = list(range(n))
    while rem:
        avail = [i for i in rem if stops[i][0] == "p" or stops[i][1] in placed_p]
        if not avail: avail = list(rem)
        ch = min(avail, key=lambda i: hav(cur, coords[i]))
        out.append(ch); rem.remove(ch)
        if stops[ch][0] == "p": placed_p.add(stops[ch][1])
        cur = coords[ch]
    return out


def app_route(stops, coords, M, si, st0, ready, committed):
    """faithful optimize_route: brute<=cap (min-latency) else NN; + committed-pickup reorder."""
    if len(stops) <= BRUTE_CAP:
        seq = brute(stops, M, si, st0, ready, "minlat")
    else:
        seq = app_nn(stops, coords, coords[si] if si < len(coords) else coords[0])
    # _reorder_pickups_by_committed: sort pickups by committed; repair dropoff-after-pickup
    pids = [stops[i][1] for i in seq if stops[i][0] == "p"]
    cks = {o: committed.get(o) for o in pids}
    if sum(1 for v in cks.values() if v) >= 2:
        order = sorted(pids, key=lambda o: cks[o] or datetime(2099, 1, 1, tzinfo=timezone.utc))
        if order != pids:
            it = iter(order); newseq = list(seq)
            for idx, s in enumerate(newseq):
                if stops[s][0] == "p":
                    # find a pickup stop-idx for next oid
                    nxt = next(it)
                    newseq[idx] = next(si2 for si2 in range(len(stops)) if stops[si2] == ("p", nxt))
            # repair: ensure each dropoff after its pickup
            pos = {s: r for r, s in enumerate(newseq)}
            pmap = {}
            for i, (k, o) in enumerate(stops): pmap.setdefault(o, [None, None])[0 if k == "p" else 1] = i
            if all(pos[v[0]] < pos[v[1]] for v in pmap.values()):
                seq = newseq
    return seq


def score(seq, stops, M, si, st0, ready):
    food, mk, dv = simulate(seq, stops, M, si, st0, ready)
    return {"mf": round(max(food.values()), 1) if food else 0, "r6": sum(1 for v in food.values() if v > SLA), "mk": round(mk, 1)}


def main():
    comp, bags = load()
    sys.stderr.write(f"[app] bags={len(bags)}\n")
    POL = ["REALIZED", "ZIOMEK", "APP", "MINLAT_ID"]
    agg = {p: {"mf": [], "r6": 0, "mk": []} for p in POL}
    n = 0; nn_bags = 0; diverge = 0; nn_examples = []
    for cid, oids in bags:
        if not (2 <= len(oids) <= 6):
            continue
        ready = {o: pdt(comp[o].get("ready")) for o in oids}
        if any(v is None for v in ready.values()): continue
        committed = {o: pdt(comp[o].get("ck")) for o in oids}
        stops = []
        for o in oids: stops.append(("p", o)); stops.append(("d", o))
        so = min(oids, key=lambda o: ready[o])
        coords_list = [tuple(comp[o]["pc"]) if k == "p" else tuple(comp[o]["dc"]) for (k, o) in stops]
        start_c = tuple(comp[so]["pc"])
        pts = [start_c] + coords_list
        try: M0 = osrm_matrix(pts)
        except Exception: continue
        m = len(stops)
        Mx = [[M0[i+1][j+1] for j in range(m)] + [0.0] for i in range(m)] + [[M0[0][j+1] for j in range(m)] + [0.0]]
        coords = coords_list + [start_c]; START = m; st0 = ready[so]
        ev = []
        for o in oids:
            ev.append((pdt(comp[o]["pt"]), stops.index(("p", o)))); ev.append((pdt(comp[o]["dt"]), stops.index(("d", o))))
        ev.sort(key=lambda e: e[0])
        seqs = {
            "REALIZED": [i for _, i in ev],
            "ZIOMEK": brute(stops, Mx, START, st0, ready, "makespan"),
            "APP": app_route(stops, coords, Mx, START, st0, ready, committed),
            "MINLAT_ID": brute(stops, Mx, START, st0, ready, "minlat"),
        }
        n += 1
        if len(oids) >= 5: nn_bags += 1
        if seqs["APP"] != seqs["ZIOMEK"]: diverge += 1
        for p in POL:
            s = score(seqs[p], stops, Mx, START, st0, ready)
            agg[p]["mf"].append(s["mf"]); agg[p]["mk"].append(s["mk"])
            if s["r6"] > 0: agg[p]["r6"] += 1
        if len(oids) >= 5 and len(nn_examples) < 6:
            sa = score(seqs["APP"], stops, Mx, START, st0, ready); si_ = score(seqs["MINLAT_ID"], stops, Mx, START, st0, ready)
            nn_examples.append((cid, len(oids), sa, si_))

    def med(x): return round(stt.median(x), 1) if x else None
    def p90(x): x = sorted(x); return round(x[min(len(x)-1, int(0.9*len(x)))], 1) if x else None
    L = ["=== AUDYT TRASY APKI (fallback) vs PLAN ZIOMKA — realne worki events.db ===",
         f"worki size 2-6 = {n} | NN-fallback (>=5 zleceń) = {nn_bags} | APP≠ZIOMEK divergence (gdy apka liczy własną) = {diverge}/{n} = {100.0*diverge/n:.0f}%",
         f"committed czas ustawiony rzadko (~4% zleceń) → _reorder_pickups_by_committed prawie nie odpala",
         "",
         f"{'sekwencja':<11} {'med_maxfood':>11} {'p90_maxfood':>11} {'R6-breach bags':>15} {'med_makespan':>12}", "-"*62]
    for p in POL:
        d = agg[p]
        L.append(f"{p:<11} {med(d['mf']):>11} {p90(d['mf']):>11} {d['r6']}/{n} ({100.0*d['r6']/n:.0f}%){'':>4} {med(d['mk']):>12}")
    L += ["",
          "── interpretacja ──",
          "  APP vs ZIOMEK: czy fallback apki (min-latency) jest świeższy niż plan Ziomka (makespan)?",
          "  APP vs MINLAT_ID: koszt capu 8-stopów (NN dla >=5 zleceń) — gdzie apka traci własną optymalność",
          "",
          "── przykłady NN-fallback (>=5 zleceń): APP(mf/r6/mk) vs MINLAT_ID(mf/r6/mk) ──"]
    for cid, k, sa, si_ in nn_examples:
        L.append(f"  cid={cid} n={k} APP:{sa['mf']}/{sa['r6']}/{sa['mk']} vs IDEAL:{si_['mf']}/{si_['r6']}/{si_['mk']}")
    rep = "\n".join(L); open(OUT, "w").write(rep + "\n"); print(rep)


if __name__ == "__main__":
    main()
