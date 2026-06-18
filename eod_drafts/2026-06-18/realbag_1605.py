#!/usr/bin/env python3
"""16.05 (real peak Saturday) — niezbity dowód na DOWOZACH, nie tylko regresji.

Źródło: sla_log.jsonl (ACTUAL delivery outcomes 16.05: picked_up_at, delivered_at,
delivery_time_minutes = realny food-age, sla_ok). Coords: geocoding module (cache+
Google, realne adresy) + restaurant_coords.json. Rekonstruujemy realne worki
współwiezione i re-routujemy 4 obiektywy spójnym scorerem OSRM:
  REALIZED_ACT = realny food-age z sla_log (co NAPRAWDĘ było)
  REALIZED_SEQ = realna kolejność (z timestampów), re-scored OSRM
  MAKESPAN     = Ziomek dziś (min total duration)
  FOODAGE      = Opcja B (lexicogr. R6, max_food_age, makespan)
  MINLAT       = apka (sum delivery times)
"""
import json, re, sys, urllib.request
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from itertools import permutations

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2 import geocoding  # noqa

SLA_LOG = "/root/.openclaw/workspace/scripts/logs/sla_log.jsonl"
RC = "/root/.openclaw/workspace/dispatch_state/restaurant_coords.json"
OUT = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-18/realbag_1605_result.txt"
OSRM = "http://localhost:5001"
SLA = 35.0
DWELL_P, DWELL_D = 1.0, 3.5
_rc = {k.lower(): v for k, v in json.load(open(RC)).items() if isinstance(v, dict)}


def pdt(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace(" ", "T")) if "T" not in s else datetime.fromisoformat(s)
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    except Exception:
        return None


def osrm_matrix(points):
    coords = ";".join(f"{p[1]},{p[0]}" for p in points)
    r = json.load(urllib.request.urlopen(f"{OSRM}/table/v1/driving/{coords}?annotations=duration", timeout=25))
    return r["durations"]


def rest_coord(name):
    v = _rc.get((name or "").lower())
    if v and v.get("lat"):
        return (float(v["lat"]), float(v.get("lon") or v.get("lng")))
    try:
        c = geocoding.geocode(name + " Białystok", city="Białystok")
        if c and abs(c[0]) > 1:
            return (float(c[0]), float(c[1]))
    except Exception:
        pass
    return None


def deliv_coord(addr):
    try:
        c = geocoding.geocode(addr, city="Białystok")
        if c and abs(c[0]) > 1:
            return (float(c[0]), float(c[1]))
    except Exception:
        pass
    return None


def load_1605():
    O = {}
    for line in open(SLA_LOG):
        if "2026-05-16" not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if (d.get("logged_at") or "")[:10] != "2026-05-16":
            continue
        oid = d.get("order_id")
        if not oid:
            continue
        O[oid] = d
    by = defaultdict(list)
    for oid, d in O.items():
        pt, dt = pdt(d.get("picked_up_at")), pdt(d.get("delivered_at"))
        if pt and dt and dt > pt:
            by[d.get("courier_id")].append((pt, dt, oid))
    bags = []
    for cid, lst in by.items():
        lst.sort()
        i = 0
        while i < len(lst):
            grp = [lst[i]]; mx = lst[i][1]; j = i + 1
            while j < len(lst) and lst[j][0] < mx:
                grp.append(lst[j]); mx = max(mx, lst[j][1]); j += 1
            if len(grp) >= 2:
                bags.append((cid, [g[2] for g in grp]))
            i = j
    return O, bags


def simulate(seq, stops, M, start_i, start_t, ready):
    t = start_t; cur = start_i; pick = {}; dv = {}
    for si in seq:
        t = t + timedelta(minutes=M[cur][si] / 60.0); cur = si
        k, oid = stops[si]
        if k == "p":
            r = ready.get(oid)
            if r and r > t:
                t = r
            pick[oid] = t; t = t + timedelta(minutes=DWELL_P)
        else:
            dv[oid] = t; t = t + timedelta(minutes=DWELL_D)
    mk = (t - start_t).total_seconds() / 60.0
    food = {o: (dv[o] - (pick.get(o) or ready.get(o))).total_seconds() / 60.0 for o in dv if (pick.get(o) or ready.get(o))}
    return food, mk, dv


def vperms(stops):
    n = len(stops); pm = {}
    for i, (k, o) in enumerate(stops):
        pm.setdefault(o, [None, None])[0 if k == "p" else 1] = i
    for perm in permutations(range(n)):
        pos = {s: r for r, s in enumerate(perm)}
        if all(pos[v[0]] < pos[v[1]] for v in pm.values()):
            yield perm


def best(stops, M, si, st, ready, obj):
    bk = bb = None
    for perm in vperms(stops):
        food, mk, dv = simulate(list(perm), stops, M, si, st, ready)
        r6 = sum(1 for v in food.values() if v > SLA); mf = max(food.values()) if food else 0
        if obj == "makespan":
            key = (round(mk, 3),)
        elif obj == "foodage":
            key = (r6, round(mf, 3), round(mk, 3))
        else:
            key = (round(sum((dv[o] - st).total_seconds() for o in dv), 1), round(mk, 3))
        if bk is None or key < bk:
            bk = key; bb = list(perm)
    return bb


def score(seq, stops, M, si, st, ready):
    food, mk, dv = simulate(seq, stops, M, si, st, ready)
    return {"mf": round(max(food.values()), 1) if food else 0, "r6": sum(1 for v in food.values() if v > SLA), "mk": round(mk, 1)}


def main():
    O, bags = load_1605()
    sys.stderr.write(f"[1605] orders={len(O)} co-carried bags={len(bags)}\n")
    import statistics as st
    POL = ["REALIZED_SEQ", "MAKESPAN", "FOODAGE", "MINLAT"]
    agg = {p: {"mf": [], "r6": 0, "mk": []} for p in POL}
    real_act_mf = []  # actual realized max food-age per bag (from sla_log)
    nbag = 0; skip = 0; ex = []
    for cid, oids in bags:
        if not (2 <= len(oids) <= 4):
            skip += 1; continue
        recs = [O[o] for o in oids]
        ready = {oids[i]: pdt(recs[i].get("picked_up_at")) for i in range(len(oids))}
        if any(v is None for v in ready.values()):
            continue
        pc = {o: rest_coord(O[o].get("restaurant")) for o in oids}
        dc = {o: deliv_coord(O[o].get("delivery_address")) for o in oids}
        if any(pc[o] is None or dc[o] is None for o in oids):
            skip += 1; continue
        stops = []
        for o in oids:
            stops.append(("p", o)); stops.append(("d", o))
        start_o = min(oids, key=lambda o: ready[o])
        pts = [pc[start_o]] + [pc[o] if k == "p" else dc[o] for (k, o) in stops]
        try:
            M0 = osrm_matrix(pts)
        except Exception as e:
            sys.stderr.write(f"osrm {cid}:{e}\n"); continue
        n = len(stops)
        Mx = [[M0[i + 1][j + 1] for j in range(n)] + [0.0] for i in range(n)] + [[M0[0][j + 1] for j in range(n)] + [0.0]]
        START = n; start_t = ready[start_o]
        # realized seq from timestamps
        ev = []
        for o in oids:
            ev.append((pdt(O[o]["picked_up_at"]), stops.index(("p", o))))
            ev.append((pdt(O[o]["delivered_at"]), stops.index(("d", o))))
        ev.sort(key=lambda e: e[0])
        seqs = {"REALIZED_SEQ": [i for _, i in ev],
                "MAKESPAN": best(stops, Mx, START, start_t, ready, "makespan"),
                "FOODAGE": best(stops, Mx, START, start_t, ready, "foodage"),
                "MINLAT": best(stops, Mx, START, start_t, ready, "minlat")}
        nbag += 1
        # actual realized food-age (from sla_log delivery_time_minutes)
        act = [O[o].get("delivery_time_minutes") for o in oids if isinstance(O[o].get("delivery_time_minutes"), (int, float))]
        if act:
            real_act_mf.append(max(act))
        row = {}
        for p in POL:
            s = score(seqs[p], stops, Mx, START, start_t, ready)
            row[p] = s; agg[p]["mf"].append(s["mf"]); agg[p]["mk"].append(s["mk"])
            if s["r6"] > 0:
                agg[p]["r6"] += 1
        if len(ex) < 10 and len(oids) >= 3:
            ex.append((cid, oids, row, max(act) if act else None))

    def med(x): return round(st.median(x), 1) if x else None
    def p90(x): x = sorted(x); return round(x[min(len(x) - 1, int(0.9 * len(x)))], 1) if x else None
    L = ["=== 16.05 (realna sobota-peak) — DOWÓD NA DOWOZACH ===",
         f"co-carried worki size 2-4 zrekonstruowane i re-routowane: {nbag} (skip {skip})", ""]
    # whole-day realized context (all 385)
    allft = [d.get("delivery_time_minutes") for d in O.values() if isinstance(d.get("delivery_time_minutes"), (int, float))]
    br = sum(1 for x in allft if x > SLA)
    L += [f"── KONTEKST: cały dzień 16.05 (sla_log, {len(allft)} dowozów, REALNE) ──",
          f"  realny food-age: median={med(allft)} mean={round(st.mean(allft),1)} p90={p90(allft)} max={round(max(allft),1)}",
          f"  R6 breach (>35min) REALNIE: {br}/{len(allft)} = {100.0*br/len(allft):.1f}%", ""]
    L += [f"{'objektyw':<13} {'med_maxfood':>11} {'p90_maxfood':>11} {'worki_R6>35':>13} {'med_makespan':>12}", "-" * 64]
    L.append(f"{'REALIZED(akt)':<13} {med(real_act_mf):>11} {p90(real_act_mf):>11} "
             f"{sum(1 for x in real_act_mf if x>SLA)}/{len(real_act_mf)}{'':>6} {'(z sla_log)':>12}")
    for p in POL:
        d = agg[p]
        L.append(f"{p:<13} {med(d['mf']):>11} {p90(d['mf']):>11} {d['r6']}/{nbag}{'':>8} {med(d['mk']):>12}")
    L.append("")
    # FOODAGE vs MAKESPAN per-bag
    fr = rg = 0; r6b = r6f = 0; dmk = []
    for i in range(nbag):
        mb, mf = agg["MAKESPAN"]["mf"][i], agg["FOODAGE"]["mf"][i]
        if mb - mf > 0.5: fr += 1
        elif mf - mb > 0.5: rg += 1
        if mb > SLA: r6b += 1
        if mf > SLA: r6f += 1
        dmk.append(agg["FOODAGE"]["mk"][i] - agg["MAKESPAN"]["mk"][i])
    L += ["── OPCJA B vs MAKESPAN (Ziomek dziś) na realnych workach 16.05 ──",
          f"  worki świeższe pod FOODAGE: {fr}/{nbag} | regresja: {rg}",
          f"  worki z R6 breach: MAKESPAN={r6b}/{nbag} -> FOODAGE={r6f}/{nbag}",
          f"  koszt makespanu: median={med(dmk)}min p90={p90(dmk)}min", ""]
    L += ["── przykłady (maxfood/R6/makespan; akt=realny max food-age z sla_log) ──"]
    for cid, oids, row, act in ex[:8]:
        rs = " | ".join(f"{p.split('_')[0][:4]}:{row[p]['mf']}/{row[p]['r6']}/{row[p]['mk']}" for p in POL)
        L.append(f"  cid={cid} n={len(oids)} akt={act} | {rs}")
    rep = "\n".join(L)
    open(OUT, "w").write(rep + "\n")
    print(rep)


if __name__ == "__main__":
    main()
