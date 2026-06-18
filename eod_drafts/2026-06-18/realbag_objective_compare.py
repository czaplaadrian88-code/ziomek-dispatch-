#!/usr/bin/env python3
"""Objective comparison on REAL co-carried bags (events.db ground truth).

Mechanism confirmed: Ziomek's live plan = full TSP picked by MAKESPAN
(sla_violations, total_duration). The lever is the OBJECTIVE. We compare, on
REAL bags actually carried by couriers, four delivery sequences scored by ONE
consistent OSRM timeline scorer (per-order food-age = ready->delivery):

  REALIZED  = order couriers actually drove (from event timestamps)
  MAKESPAN  = minimize total route duration            (= Ziomek today)
  FOODAGE   = lexicographic (R6_breaches, max_food_age, makespan)  (= Option B)
  MINLAT    = minimize sum of delivery completion times (= app / Option "align")

Metrics per bag: max_food_age, R6_breach_count(>35min), makespan_min,
czasowka delivery lateness vs parsed target. bag 2-4 exact brute-force.
Read-only, self-contained (raw OSRM table localhost:5001, no engine coupling).
"""
import argparse, json, re, sqlite3, sys, urllib.request
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from itertools import permutations

EVENTS = "/root/.openclaw/workspace/dispatch_state/events.db"
OUT = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-18/realbag_result.txt"
OSRM = "http://localhost:5001"
SLA = 35.0
DWELL_P = 1.0
DWELL_D = 3.5


def pdt(s):
    try:
        d = datetime.fromisoformat(s)
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    except Exception:
        return None


def osrm_matrix(points):
    coords = ";".join(f"{p[1]},{p[0]}" for p in points)
    u = f"{OSRM}/table/v1/driving/{coords}?annotations=duration"
    r = json.load(urllib.request.urlopen(u, timeout=20))
    return r["durations"]


def load_bags(frm):
    con = sqlite3.connect(EVENTS)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT order_id,event_type,courier_id,payload,created_at FROM events "
        "WHERE created_at>? AND event_type IN ('NEW_ORDER','COURIER_PICKED_UP','COURIER_DELIVERED') "
        "ORDER BY created_at", (frm,)).fetchall()
    O = {}
    for r in rows:
        oid = r["order_id"]
        if not oid:
            continue
        o = O.setdefault(oid, {})
        try:
            p = json.loads(r["payload"]) if r["payload"] else {}
        except Exception:
            p = {}
        if r["event_type"] == "NEW_ORDER":
            o["pc"] = p.get("pickup_coords"); o["dc"] = p.get("delivery_coords")
            o["ready"] = p.get("pickup_at_warsaw"); o["type"] = p.get("order_type")
            o["rest"] = p.get("restaurant"); o["uwagi"] = p.get("uwagi") or ""
        elif r["event_type"] == "COURIER_PICKED_UP":
            o["pick_ts"] = r["created_at"]; o["cid"] = r["courier_id"]
        elif r["event_type"] == "COURIER_DELIVERED":
            o["deliv_ts"] = r["created_at"]

    def ok(o):
        return (o.get("pc") and o.get("dc") and o.get("pick_ts") and o.get("deliv_ts")
                and o.get("cid") and len(o["pc"]) == 2 and len(o["dc"]) == 2)
    comp = {oid: o for oid, o in O.items() if ok(o)}
    bycid = defaultdict(list)
    for oid, o in comp.items():
        pt, dt = pdt(o["pick_ts"]), pdt(o["deliv_ts"])
        if pt and dt and dt > pt:
            bycid[o["cid"]].append((pt, dt, oid))
    bags = []
    for cid, lst in bycid.items():
        lst.sort()
        i = 0
        while i < len(lst):
            grp = [lst[i]]; maxend = lst[i][1]; j = i + 1
            while j < len(lst) and lst[j][0] < maxend:
                grp.append(lst[j]); maxend = max(maxend, lst[j][1]); j += 1
            if len(grp) >= 2:
                bags.append((cid, [g[2] for g in grp]))
            i = j
    return comp, bags


_CZ = re.compile(r"(?:na|godz\w*|o)\s*(\d{1,2})[:.](\d{2})")


def czas_target(o):
    """Parse czasówka delivery target HH:MM (Warsaw) from uwagi -> aware UTC dt on pickup day."""
    if o.get("type") != "czasowka":
        return None
    m = _CZ.search(o.get("uwagi", ""))
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if hh > 23 or mm > 59:
        return None
    base = pdt(o.get("pick_ts"))
    if base is None:
        return None
    # Warsaw = UTC+2 (summer). target Warsaw HH:MM -> UTC = -2h
    day = (base + timedelta(hours=2)).date()
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=timezone.utc) - timedelta(hours=2)


def simulate(seq, stops, M, idx, start_i, start_t, ready):
    """seq = list of stop indices (into stops). Return per-order food-age, makespan, deliv times."""
    t = start_t
    cur = start_i
    pick_t = {}
    deliv_t = {}
    for si in seq:
        t = t + timedelta(minutes=M[cur][si] / 60.0)
        cur = si
        kind, oid = stops[si]
        if kind == "p":
            r = ready.get(oid)
            if r is not None and r > t:
                t = r
            pick_t[oid] = t
            t = t + timedelta(minutes=DWELL_P)
        else:
            deliv_t[oid] = t
            t = t + timedelta(minutes=DWELL_D)
    makespan = (t - start_t).total_seconds() / 60.0
    food = {}
    for oid in deliv_t:
        anchor = pick_t.get(oid) or ready.get(oid)
        if anchor is not None:
            food[oid] = (deliv_t[oid] - anchor).total_seconds() / 60.0
    return food, makespan, deliv_t


def valid_perms(stops):
    n = len(stops)
    pmap = {}
    for i, (k, oid) in enumerate(stops):
        pmap.setdefault(oid, [None, None])[0 if k == "p" else 1] = i
    for perm in permutations(range(n)):
        pos = {s: r for r, s in enumerate(perm)}
        if all(pos[v[0]] < pos[v[1]] for v in pmap.values()):
            yield perm


def best_seq(stops, M, start_i, start_t, ready, objective):
    best = None
    bkey = None
    for perm in valid_perms(stops):
        food, mk, dv = simulate(list(perm), stops, M, None, start_i, start_t, ready)
        r6 = sum(1 for v in food.values() if v > SLA)
        maxf = max(food.values()) if food else 0.0
        if objective == "makespan":
            key = (round(mk, 3),)
        elif objective == "foodage":
            key = (r6, round(maxf, 3), round(mk, 3))
        elif objective == "minlat":
            key = (round(sum((dv[o] - start_t).total_seconds() for o in dv), 1), round(mk, 3))
        if bkey is None or key < bkey:
            bkey = key; best = list(perm)
    return best


def score(seq, stops, M, start_i, start_t, ready, orders, ctargets):
    food, mk, dv = simulate(seq, stops, M, None, start_i, start_t, ready)
    r6 = sum(1 for v in food.values() if v > SLA)
    maxf = max(food.values()) if food else 0.0
    cz_late = []
    for oid, tgt in ctargets.items():
        if oid in dv and tgt is not None:
            cz_late.append((dv[oid] - tgt).total_seconds() / 60.0)
    return {"maxf": round(maxf, 1), "r6": r6, "mk": round(mk, 1),
            "cz_late": [round(x, 1) for x in cz_late]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2026-05-18")
    ap.add_argument("--maxbag", type=int, default=4)
    a = ap.parse_args()
    comp, bags = load_bags(a.frm)
    sys.stderr.write(f"[rb] orders={len(comp)} bags={len(bags)}\n")

    POL = ["REALIZED", "MAKESPAN", "FOODAGE", "MINLAT"]
    agg = {p: {"maxf": [], "r6_bags": 0, "mk": [], "cz": []} for p in POL}
    nbag = 0
    cz_bags = 0
    examples = []
    skipped_big = 0
    for cid, oids in bags:
        if len(oids) > a.maxbag:
            skipped_big += 1
            continue
        os_ = [comp[o] for o in oids]
        if any(not o.get("ready") for o in os_):
            continue
        ready = {oids[i]: pdt(os_[i]["ready"]) for i in range(len(oids))}
        if any(v is None for v in ready.values()):
            continue
        ctargets = {oids[i]: czas_target(os_[i]) for i in range(len(oids))}
        ctargets = {k: v for k, v in ctargets.items() if v is not None}
        # stops: pickup + dropoff per order
        stops = []
        for oid in oids:
            stops.append(("p", oid)); stops.append(("d", oid))
        # points for OSRM: start + each stop coord
        start_oid = min(oids, key=lambda o: ready[o])
        start_pt = tuple(comp[start_oid]["pc"])
        pts = [start_pt] + [tuple(comp[oid]["pc"]) if k == "p" else tuple(comp[oid]["dc"])
                            for (k, oid) in stops]
        try:
            M = osrm_matrix(pts)
        except Exception as e:
            sys.stderr.write(f"[rb] osrm fail {cid}: {e}\n"); continue
        # remap: matrix index 0=start, stop i -> matrix i+1. simulate uses indices into stops; adapt M.
        # Build M' indexed by stop index with start as -1 via wrapper:
        n = len(stops)
        SM = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                SM[i][j] = M[i + 1][j + 1]
        start_row = [M[0][j + 1] for j in range(n)]

        # patch simulate to accept start_row: emulate by temp matrix with start as index n
        Mx = [row[:] + [0.0] for row in SM] + [start_row + [0.0]]
        START = n  # virtual start index
        start_t = ready[start_oid]

        def sq_score(seq):
            return score(seq, stops, Mx, START, start_t, ready, os_, ctargets)

        def sq_best(obj):
            return best_seq(stops, Mx, START, start_t, ready, obj)

        # realized sequence from event timestamps
        ev = []
        for oid in oids:
            ev.append((pdt(comp[oid]["pick_ts"]), stops.index(("p", oid))))
            ev.append((pdt(comp[oid]["deliv_ts"]), stops.index(("d", oid))))
        ev.sort(key=lambda e: e[0])
        seqs = {"REALIZED": [i for _, i in ev],
                "MAKESPAN": sq_best("makespan"),
                "FOODAGE": sq_best("foodage"),
                "MINLAT": sq_best("minlat")}
        nbag += 1
        if ctargets:
            cz_bags += 1
        row = {}
        for p in POL:
            s = sq_score(seqs[p])
            row[p] = s
            agg[p]["maxf"].append(s["maxf"])
            agg[p]["mk"].append(s["mk"])
            if s["r6"] > 0:
                agg[p]["r6_bags"] += 1
            agg[p]["cz"].extend(s["cz_late"])
        if len(examples) < 12 and len(oids) >= 3:
            examples.append((cid, oids, row))

    import statistics as st

    def med(xs):
        return round(st.median(xs), 1) if xs else None

    def p90(xs):
        xs = sorted(xs)
        return round(xs[min(len(xs) - 1, int(0.9 * len(xs)))], 1) if xs else None
    L = ["=== REAL-BAG OBJECTIVE COMPARISON (events.db carried bags) ===",
         f"window from {a.frm} | bags scored (size 2-{a.maxbag})={nbag} (skipped >{a.maxbag}: {skipped_big}) "
         f"| czasówka-bags={cz_bags} | SLA={SLA} dwell_p={DWELL_P} dwell_d={DWELL_D}",
         "",
         f"{'policy':<10} {'med_maxfood':>11} {'p90_maxfood':>11} {'R6breach_bags':>14} {'med_makespan':>12}",
         "-" * 62]
    for p in POL:
        d = agg[p]
        L.append(f"{p:<10} {med(d['maxf']):>11} {p90(d['maxf']):>11} "
                 f"{d['r6_bags']}/{nbag} ({100.0*d['r6_bags']/nbag:.0f}%){'':>3} {med(d['mk']):>12}")
    L.append("")
    L.append("── vs MAKESPAN (Ziomek today): improvement counts ──")
    base = {i: None for i in range(nbag)}  # noqa
    # recompute per-bag deltas
    # (store per-bag in parallel arrays)
    L.append("  (food-age fresher / R6 removed vs makespan, per bag)")
    for p in ["FOODAGE", "MINLAT", "REALIZED"]:
        frsh = reg = r6rm = 0
        dms = []
        for i in range(nbag):
            mf_b = agg["MAKESPAN"]["maxf"][i]; mf_p = agg[p]["maxf"][i]
            if mf_b - mf_p > 0.5:
                frsh += 1
            elif mf_p - mf_b > 0.5:
                reg += 1
            dms.append(agg[p]["mk"][i] - agg["MAKESPAN"]["mk"][i])
        # R6 bag-level
        r6_b = sum(1 for i in range(nbag) if agg["MAKESPAN"]["maxf"][i] > SLA)
        r6_p = sum(1 for i in range(nbag) if agg[p]["maxf"][i] > SLA)
        L.append(f"  {p:<9}: fresher={frsh} regressed={reg} | R6-breach bags {r6_b}->{r6_p} "
                 f"| Δmakespan median={med(dms)}min")
    L.append("")
    # czasówka
    for p in POL:
        cz = agg[p]["cz"]
        if cz:
            late = [x for x in cz if x > 0]
            L.append(f"  czasówka deliv lateness [{p}]: n={len(cz)} median={med(cz)} p90={p90(cz)} "
                     f"| late>0: {len(late)}/{len(cz)} | >10min: {sum(1 for x in cz if x>10)}")
    L.append("")
    L.append("── sample bag>=3 examples (maxfood/R6/makespan per policy) ──")
    for cid, oids, row in examples[:8]:
        rs = " | ".join(f"{p}:{row[p]['maxf']}/{row[p]['r6']}/{row[p]['mk']}" for p in POL)
        L.append(f"  cid={cid} n={len(oids)} {rs}")
    rep = "\n".join(L)
    with open(OUT, "w") as f:
        f.write(rep + "\n")
    print(rep)


if __name__ == "__main__":
    main()
