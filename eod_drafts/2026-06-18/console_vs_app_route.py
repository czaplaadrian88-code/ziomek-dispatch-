#!/usr/bin/env python3
"""DLACZEGO konsola koordynatora daje lepszą trasę niż apka — pomiar na realnych workach.

Konsola (`fleet_state._build_route`): PODJAZDY — odbiory dzielone na kursy (gap
<=PICKUP_MERGE_MIN=10min), w kursie odbiory zgrupowane po restauracji, carried-first,
potem per kurs WSZYSTKIE odbiory → WSZYSTKIE dostawy. Apka: surowa kolejność planu
Ziomka (ETA-sort, przeplot) / fallback min-latency `optimize_route`.

Mierzymy na realnych workach (events.db): food-age, makespan + „powroty po jedzenie"
(pickup-po-dropoffie = back-and-forth do restauracji — to czego podjazdy unikają).
"""
import json, math, sqlite3, sys, urllib.request
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from itertools import permutations
import statistics as stt

EV = "/root/.openclaw/workspace/dispatch_state/events.db"
OSRM = "http://localhost:5001"
OUT = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-18/console_vs_app_result.txt"
SLA = 35.0; DWELL_P, DWELL_D = 1.0, 3.5; CAP = 8; MERGE = 10
FAR = datetime(2099, 1, 1, tzinfo=timezone.utc)


def pdt(s):
    try:
        d = datetime.fromisoformat(s); return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    except Exception: return None


def osrm_matrix(points):
    c = ";".join(f"{p[1]},{p[0]}" for p in points)
    return json.load(urllib.request.urlopen(f"{OSRM}/table/v1/driving/{c}?annotations=duration", timeout=25))["durations"]


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
            o["ready"] = p.get("pickup_at_warsaw"); o["rest"] = (p.get("restaurant") or "").replace("&amp;", "&")
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
    food = {o: (dv[o] - (pick.get(o) or ready.get(o))).total_seconds() / 60.0 for o in dv if (pick.get(o) or ready.get(o))}
    return food, (t - st0).total_seconds() / 60.0, dv


def vperms(stops):
    n = len(stops); pm = {}
    for i, (k, o) in enumerate(stops): pm.setdefault(o, [None, None])[0 if k == "p" else 1] = i
    for perm in permutations(range(n)):
        pos = {s: r for r, s in enumerate(perm)}
        if all(pos[v[0]] < pos[v[1]] for v in pm.values()): yield perm


def brute(stops, M, si, st0, ready, obj):
    n = len(stops)
    if n <= CAP: cand = vperms(stops)
    else:
        oids = [o for k, o in stops if k == "p"]; pidx = {o: stops.index(("p", o)) for o in oids}; didx = {o: stops.index(("d", o)) for o in oids}
        ps = [pidx[o] for o in sorted(oids, key=lambda o: ready[o] or st0)]
        cand = (ps + [didx[o] for o in dp] for dp in permutations(oids))
    bk = bb = None
    for perm in cand:
        food, mk, dv = simulate(list(perm), stops, M, si, st0, ready)
        key = (round(mk, 3),) if obj == "makespan" else (round(sum((dv[o] - st0).total_seconds() for o in dv), 1), round(mk, 3))
        if bk is None or key < bk: bk = key; bb = list(perm)
    return bb


def console_seq(oids, comp, ready, stops):
    """Replika _build_route: PODJAZDY (kursy gap<=MERGE, same-rest grouping, pickups→drops)."""
    idx = {(("p" if k == "p" else "d"), o): i for i, (k, o) in enumerate(stops)}
    to_pick = sorted(oids, key=lambda o: ready[o] or FAR)
    runs = []; prev = None
    for o in to_pick:
        dt = ready[o]
        if runs and prev and dt and (dt - prev) <= timedelta(minutes=MERGE): runs[-1].append(o)
        else: runs.append([o])
        if dt: prev = dt
    seq = []
    for run in runs:
        fs = {}
        for i, o in enumerate(run): fs.setdefault(comp[o]["rest"], i)
        run2 = sorted(run, key=lambda o: (fs[comp[o]["rest"]], ready[o] or FAR))
        for o in run2: seq.append(idx[("p", o)])          # wszystkie odbiory kursu
        for o in sorted(run2, key=lambda o: ready[o] or FAR): seq.append(idx[("d", o)])  # potem dostawy
    return seq


def app_minlat(stops, M, si, st0, ready, coords):
    if len(stops) <= CAP: return brute(stops, M, si, st0, ready, "minlat")
    # NN (haversine) fallback jak _nn_optimize
    def hav(a, b):
        R = 6371000; p = math.pi / 180
        return 2 * R * math.asin(math.sqrt(math.sin((b[0]-a[0])*p/2)**2 + math.cos(a[0]*p)*math.cos(b[0]*p)*math.sin((b[1]-a[1])*p/2)**2))
    n = len(stops); placed = set(); cur = coords[si]; out = []; rem = list(range(n))
    while rem:
        av = [i for i in rem if stops[i][0] == "p" or stops[i][1] in placed] or list(rem)
        ch = min(av, key=lambda i: hav(cur, coords[i])); out.append(ch); rem.remove(ch)
        if stops[ch][0] == "p": placed.add(stops[ch][1])
        cur = coords[ch]
    return out


def backtrack(seq, stops):
    """ile razy ODBIÓR następuje po DOSTAWIE (powrót po jedzenie = to czego podjazdy unikają)."""
    c = 0
    for i in range(1, len(seq)):
        if stops[seq[i]][0] == "p" and stops[seq[i-1]][0] == "d": c += 1
    return c


def score(seq, stops, M, si, st0, ready):
    food, mk, dv = simulate(seq, stops, M, si, st0, ready)
    return {"mf": round(max(food.values()), 1) if food else 0, "r6": sum(1 for v in food.values() if v > SLA), "mk": round(mk, 1), "bt": backtrack(seq, stops)}


def main():
    comp, bags = load(); sys.stderr.write(f"[cmp] bags={len(bags)}\n")
    POL = ["REALIZED", "ZIOMEK", "APP", "CONSOLE"]
    agg = {p: {"mf": [], "r6": 0, "mk": [], "bt": []} for p in POL}
    n = 0; cons_beats_app = 0; ex = []
    for cid, oids in bags:
        if not (2 <= len(oids) <= 6): continue
        ready = {o: pdt(comp[o].get("ready")) for o in oids}
        if any(v is None for v in ready.values()): continue
        stops = []
        for o in oids: stops.append(("p", o)); stops.append(("d", o))
        so = min(oids, key=lambda o: ready[o])
        coords_l = [tuple(comp[o]["pc"]) if k == "p" else tuple(comp[o]["dc"]) for (k, o) in stops]
        start_c = tuple(comp[so]["pc"]); pts = [start_c] + coords_l
        try: M0 = osrm_matrix(pts)
        except Exception: continue
        m = len(stops); Mx = [[M0[i+1][j+1] for j in range(m)] + [0.0] for i in range(m)] + [[M0[0][j+1] for j in range(m)] + [0.0]]
        coords = coords_l + [start_c]; START = m; st0 = ready[so]
        ev = []
        for o in oids:
            ev.append((pdt(comp[o]["pt"]), stops.index(("p", o)))); ev.append((pdt(comp[o]["dt"]), stops.index(("d", o))))
        ev.sort(key=lambda e: e[0])
        seqs = {"REALIZED": [i for _, i in ev], "ZIOMEK": brute(stops, Mx, START, st0, ready, "makespan"),
                "APP": app_minlat(stops, Mx, START, st0, ready, coords), "CONSOLE": console_seq(oids, comp, ready, stops)}
        n += 1
        sc = {p: score(seqs[p], stops, Mx, START, st0, ready) for p in POL}
        for p in POL:
            agg[p]["mf"].append(sc[p]["mf"]); agg[p]["mk"].append(sc[p]["mk"]); agg[p]["bt"].append(sc[p]["bt"])
            if sc[p]["r6"] > 0: agg[p]["r6"] += 1
        if sc["CONSOLE"]["mf"] < sc["APP"]["mf"] - 0.5: cons_beats_app += 1
        if len(ex) < 10 and len(oids) >= 3 and abs(sc["CONSOLE"]["mf"] - sc["APP"]["mf"]) > 1:
            ex.append((cid, len(oids), sc))
    def med(x): return round(stt.median(x), 1) if x else None
    def p90(x): x = sorted(x); return round(x[min(len(x)-1, int(0.9*len(x)))], 1) if x else None
    L = ["=== KONSOLA (podjazdy) vs APKA — dlaczego konsola daje lepszą trasę (realne worki) ===",
         f"worki 2-6 = {n} | CONSOLE świeższa od APP (food-age, >0.5min): {cons_beats_app}/{n} = {100.0*cons_beats_app/n:.0f}%", "",
         f"{'trasa':<10} {'med_food':>9} {'p90_food':>9} {'R6 bags':>8} {'med_makespan':>12} {'med_backtrack':>14}", "-"*64]
    for p in POL:
        d = agg[p]
        L.append(f"{p:<10} {med(d['mf']):>9} {p90(d['mf']):>9} {d['r6']}/{n}{'':>3} {med(d['mk']):>12} {med(d['bt']):>14}")
    L += ["", "backtrack = ile razy ODBIÓR po DOSTAWIE (powrót po jedzenie). Podjazdy konsoli to minimalizują.",
          "", "── przykłady (CONSOLE vs APP się różnią): food/r6/makespan/backtrack ──"]
    for cid, k, sc in ex[:8]:
        L.append(f"  cid={cid} n={k} | ZIOMEK {sc['ZIOMEK']['mf']}/{sc['ZIOMEK']['bt']}bt | APP {sc['APP']['mf']}/{sc['APP']['bt']}bt | CONSOLE {sc['CONSOLE']['mf']}/{sc['CONSOLE']['bt']}bt")
    rep = "\n".join(L); open(OUT, "w").write(rep + "\n"); print(rep)


if __name__ == "__main__":
    main()
