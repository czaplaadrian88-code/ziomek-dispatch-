#!/usr/bin/env python3
"""16.05 — dekompozycja przyczyn breachy R6 (>35min food-age). DOWÓD na dowozach.

Dla każdego realnego breacha (sla_log delivery_time_minutes>35) klasyfikujemy:
  A_distance  : direct OSRM(pickup->delivery) >= 35  -> NIEUNIKNIONE (żaden dispatch)
  B_seq       : w worku, realized-OSRM breach ale best-reorder (free) by uratował -> ROUTING
  B_load      : w worku, nawet best-reorder breach, direct<35 -> FLOTA/ASSIGNMENT (worek za duży)
  solo_slow   : solo (nie współwieziony), direct<35 ale realnie >35 -> realne opóźnienie/traffic
Bag-level liczone w przestrzeni OSRM (realized-seq vs best-reorder) — apples-to-apples.
Free-reorder ignoruje committed pickup (optymistyczny => B_seq = GÓRNA granica routingu).
"""
import json, sys, urllib.request
from datetime import timedelta
from itertools import permutations
import importlib.util as u
import statistics as st

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
spec = u.spec_from_file_location("eng", "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-18/realbag_1605_engine.py")
eng = u.module_from_spec(spec); spec.loader.exec_module(eng)
OSRM = "http://localhost:5001"
SLA = 35.0
DWELL_P, DWELL_D = 1.0, 3.5
OUT = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-18/breach_decomp_1605_result.txt"


def osrm_dur(a, b):
    try:
        r = json.load(urllib.request.urlopen(f"{OSRM}/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}?overview=false", timeout=15))
        return r["routes"][0]["duration"] / 60.0
    except Exception:
        return None


def osrm_matrix(points):
    coords = ";".join(f"{p[1]},{p[0]}" for p in points)
    r = json.load(urllib.request.urlopen(f"{OSRM}/table/v1/driving/{coords}?annotations=duration", timeout=25))
    return r["durations"]


def simulate(seq, stops, M, si, st0, ready):
    t = st0; cur = si; pick = {}; dv = {}
    for s in seq:
        t = t + timedelta(minutes=M[cur][s] / 60.0); cur = s
        k, o = stops[s]
        if k == "p":
            r = ready.get(o)
            if r and r > t:
                t = r
            pick[o] = t; t = t + timedelta(minutes=DWELL_P)
        else:
            dv[o] = t; t = t + timedelta(minutes=DWELL_D)
    food = {o: (dv[o] - (pick.get(o) or ready.get(o))).total_seconds() / 60.0 for o in dv if (pick.get(o) or ready.get(o))}
    return food


def vperms(stops):
    n = len(stops); pm = {}
    for i, (k, o) in enumerate(stops):
        pm.setdefault(o, [None, None])[0 if k == "p" else 1] = i
    for perm in permutations(range(n)):
        pos = {s: r for r, s in enumerate(perm)}
        if all(pos[v[0]] < pos[v[1]] for v in pm.values()):
            yield perm


def best_reorder_food(stops, oids, Mx, START, st0, ready):
    """Min-breach food map. N<=5 exact interleaved; N>=6 pickup-all-first +
    delivery brute(<=6) / nearest-neighbour. Heuristic => B_seq lower-counts for
    big bags (konserwatywne: jak heurystyka mówi breach, realnie też ~breach)."""
    n = len(oids)
    if n <= 5:
        best = None
        for perm in vperms(stops):
            f = simulate(list(perm), stops, Mx, START, st0, ready)
            key = (sum(1 for v in f.values() if v > SLA), round(max(f.values()) if f else 0, 2))
            if best is None or key < best[0]:
                best = (key, f)
        return best[1]
    # pickup-all-first (ready order), optimize delivery order
    pidx = {o: stops.index(("p", o)) for o in oids}
    didx = {o: stops.index(("d", o)) for o in oids}
    pickseq = [pidx[o] for o in sorted(oids, key=lambda o: ready[o] or st0)]
    dorder = list(oids)
    if n <= 7:
        best = None
        for perm in permutations(oids):
            seq = pickseq + [didx[o] for o in perm]
            f = simulate(seq, stops, Mx, START, st0, ready)
            key = (sum(1 for v in f.values() if v > SLA), round(max(f.values()) if f else 0, 2))
            if best is None or key < best[0]:
                best = (key, f)
        return best[1]
    # NN deliveries from last pickup
    cur = pickseq[-1]; rem = set(oids); order = []
    while rem:
        nxt = min(rem, key=lambda o: Mx[cur][didx[o]])
        order.append(nxt); cur = didx[nxt]; rem.discard(nxt)
    return simulate(pickseq + [didx[o] for o in order], stops, Mx, START, st0, ready)


def main():
    O, bags = eng.load_1605()
    # all breached deliveries (real)
    breached = {oid: d for oid, d in O.items()
                if isinstance(d.get("delivery_time_minutes"), (int, float)) and d["delivery_time_minutes"] > SLA}
    in_bag = {}
    for bi, (cid, oids) in enumerate(bags):
        for o in oids:
            in_bag[o] = bi
    sys.stderr.write(f"[decomp] total breaches(real)={len(breached)} | co-carried bags={len(bags)}\n")

    A_dist = B_seq = B_load = solo_slow = unresolved = 0
    A_ex, seq_ex, load_ex = [], [], []
    # --- SOLO breaches ---
    solo_breach = [o for o in breached if o not in in_bag]
    for o in solo_breach:
        d = O[o]
        pc = eng.rest_coord(d.get("restaurant")); dc = eng.deliv_coord(d.get("delivery_address"))
        if pc is None or dc is None:
            unresolved += 1; continue
        dd = osrm_dur(pc, dc)
        if dd is None:
            unresolved += 1; continue
        if dd >= SLA:
            A_dist += 1; A_ex.append((o, round(dd, 1), round(d["delivery_time_minutes"], 1), "solo"))
        else:
            solo_slow += 1
    # --- BAG breaches (size 2-4): OSRM realized vs best-reorder ---
    bag_breach_real = 0
    bags_with_breach = [(bi, cid, oids) for bi, (cid, oids) in enumerate(bags)
                        if any(o in breached for o in oids) and 2 <= len(oids) <= 9]
    for bi, cid, oids in bags_with_breach:
        recs = {o: O[o] for o in oids}
        ready = {o: eng.pdt(recs[o].get("picked_up_at")) for o in oids}
        if any(v is None for v in ready.values()):
            unresolved += sum(1 for o in oids if o in breached); continue
        pc = {o: eng.rest_coord(recs[o].get("restaurant")) for o in oids}
        dc = {o: eng.deliv_coord(recs[o].get("delivery_address")) for o in oids}
        if any(pc[o] is None or dc[o] is None for o in oids):
            unresolved += sum(1 for o in oids if o in breached); continue
        stops = []
        for o in oids:
            stops.append(("p", o)); stops.append(("d", o))
        start_o = min(oids, key=lambda o: ready[o])
        pts = [pc[start_o]] + [pc[o] if k == "p" else dc[o] for (k, o) in stops]
        try:
            M0 = osrm_matrix(pts)
        except Exception:
            unresolved += sum(1 for o in oids if o in breached); continue
        n = len(stops)
        Mx = [[M0[i + 1][j + 1] for j in range(n)] + [0.0] for i in range(n)] + [[M0[0][j + 1] for j in range(n)] + [0.0]]
        START = n; st0 = ready[start_o]
        # realized seq from timestamps
        ev = []
        for o in oids:
            ev.append((eng.pdt(recs[o]["picked_up_at"]), stops.index(("p", o))))
            ev.append((eng.pdt(recs[o]["delivered_at"]), stops.index(("d", o))))
        ev.sort(key=lambda e: e[0])
        real_food = simulate([i for _, i in ev], stops, Mx, START, st0, ready)
        real_breach = {o for o in oids if real_food.get(o, 0) > SLA}
        best_food = best_reorder_food(stops, oids, Mx, START, st0, ready)
        best_breach = {o for o in oids if best_food.get(o, 0) > SLA}
        # only count orders that are REAL breaches (sla_log)
        for o in oids:
            if o not in breached:
                continue
            bag_breach_real += 1
            dd = osrm_dur(pc[o], dc[o])
            if dd is not None and dd >= SLA:
                A_dist += 1; A_ex.append((o, round(dd, 1), round(O[o]["delivery_time_minutes"], 1), f"bag{len(oids)}"))
            elif o not in best_breach:
                B_seq += 1
                if len(seq_ex) < 8:
                    seq_ex.append((o, round(real_food.get(o, 0), 1), round(best_food.get(o, 0), 1), f"bag{len(oids)} cid={cid}"))
            else:
                B_load += 1
                if len(load_ex) < 8:
                    load_ex.append((o, round(dd or -1, 1), round(best_food.get(o, 0), 1), f"bag{len(oids)} cid={cid}"))

    tot = A_dist + B_seq + B_load + solo_slow + unresolved
    L = ["=== 16.05 — DEKOMPOZYCJA PRZYCZYN BREACHY R6 (real deliveries >35min) ===",
         f"realnych breachy(sla_log) total={len(breached)} | solo={len(solo_breach)} w-worku={len(breached)-len(solo_breach)} | sklasyfikowanych={tot}",
         "", "── PRZYCZYNA → ile breachy → realna DŹWIGNIA ──",
         f"  A_distance (direct odbiór→dostawa ≥35min)     : {A_dist:>3}  → NIEUNIKNIONE (żaden dispatch; ew. podział miasta/akceptacja)",
         f"  B_seq (lepsza kolejność by uratowała)         : {B_seq:>3}  → ROUTING (sekwencja/objektyw) — GÓRNA granica (free-reorder ignoruje committed)",
         f"  B_load (najlepsza kolejność też breach)       : {B_load:>3}  → FLOTA/ASSIGNMENT (worek za duży, mniej zleceń/kurier)",
         f"  solo_slow (solo, direct<35 a realnie >35)     : {solo_slow:>3}  → realne opóźnienie/traffic/late-start (nie geometria)",
         f"  unresolved (brak coords/OSRM)                 : {unresolved:>3}",
         "",
         f"  ⇒ routing-fixable (B_seq) = {B_seq}/{len(breached)} = {100.0*B_seq/len(breached):.0f}% realnych breachy",
         f"  ⇒ flota/assignment (B_load) = {B_load}/{len(breached)} = {100.0*B_load/len(breached):.0f}%",
         f"  ⇒ nieuniknione dystansem (A) = {A_dist}/{len(breached)} = {100.0*A_dist/len(breached):.0f}%",
         f"  ⇒ wykonawcze/solo (solo_slow) = {solo_slow}/{len(breached)} = {100.0*solo_slow/len(breached):.0f}%",
         ""]
    L.append("── przykłady A_distance (oid, direct_min, real_foodage, typ) ──")
    for e in A_ex[:8]:
        L.append(f"    {e}")
    L.append("── przykłady B_seq (oid, real_OSRM, best_reorder_OSRM, kontekst) — routing by uratował ──")
    for e in seq_ex[:8]:
        L.append(f"    {e}")
    L.append("── przykłady B_load (oid, direct_min, best_reorder_foodage, kontekst) — flota ──")
    for e in load_ex[:8]:
        L.append(f"    {e}")
    rep = "\n".join(L)
    open(OUT, "w").write(rep + "\n")
    print(rep)


if __name__ == "__main__":
    main()
