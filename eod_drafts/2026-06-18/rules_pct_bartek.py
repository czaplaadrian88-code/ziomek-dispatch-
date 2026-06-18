#!/usr/bin/env python3
"""Kto NAJŚCIŚLEJ trzyma reguły (w %) + weryfikacja na worku Bartka (4 zlecenia).
Trzy silniki: ZIOMEK (makespan), APP (min-latency/NN), CONSOLE (podjazdy)."""
import sys, json, sqlite3, importlib.util as u
from datetime import datetime, timezone, timedelta
import statistics as stt
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
_s = u.spec_from_file_location("cva", "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-18/console_vs_app_route.py")
M = u.module_from_spec(_s); _s.loader.exec_module(M)
SLA = 35.0


def backtrack(seq, stops):
    return sum(1 for i in range(1, len(seq)) if stops[seq[i]][0] == "p" and stops[seq[i-1]][0] == "d")


def pk_pairs_ok(seq, stops, ready):
    pk = [stops[s][1] for s in seq if stops[s][0] == "p"]
    ok = tot = 0
    for a, b in zip(pk, pk[1:]):
        if ready.get(a) and ready.get(b):
            tot += 1; ok += 1 if ready[a] <= ready[b] else 0
    return ok, tot


def run_three(oids, comp, ready):
    stops = []
    for o in oids: stops.append(("p", o)); stops.append(("d", o))
    so = min(oids, key=lambda o: ready[o] or datetime(2099, 1, 1, tzinfo=timezone.utc))
    coords_l = [tuple(comp[o]["pc"]) if k == "p" else tuple(comp[o]["dc"]) for (k, o) in stops]
    start_c = tuple(comp[so]["pc"]); pts = [start_c] + coords_l
    M0 = M.osrm_matrix(pts); m = len(stops)
    Mx = [[M0[i+1][j+1] for j in range(m)] + [0.0] for i in range(m)] + [[M0[0][j+1] for j in range(m)] + [0.0]]
    coords = coords_l + [start_c]; START = m; st0 = ready[so]
    seqs = {"ZIOMEK": M.brute(stops, Mx, START, st0, ready, "makespan"),
            "APP": M.app_minlat(stops, Mx, START, st0, ready, coords),
            "CONSOLE": M.console_seq(oids, comp, ready, stops)}
    return stops, Mx, START, st0, seqs


def main():
    comp, bags = M.load()
    POL = ["ZIOMEK", "APP", "CONSOLE"]
    # --- % adherencji na wszystkich workach ---
    bt0 = {p: 0 for p in POL}; pk = {p: [0, 0] for p in POL}; r6clean = {p: 0 for p in POL}; nb = 0
    for cid, oids in bags:
        if not (2 <= len(oids) <= 6): continue
        ready = {o: M.pdt(comp[o].get("ready")) for o in oids}
        if any(v is None for v in ready.values()): continue
        try: stops, Mx, START, st0, seqs = run_three(oids, comp, ready)
        except Exception: continue
        nb += 1
        for p in POL:
            food, mk, dv = M.simulate(seqs[p], stops, Mx, START, st0, ready)
            if backtrack(seqs[p], stops) == 0: bt0[p] += 1
            a, t = pk_pairs_ok(seqs[p], stops, ready); pk[p][0] += a; pk[p][1] += t
            if not any(v > SLA for v in food.values()): r6clean[p] += 1
    L = [f"=== ADHERENCJA REGUŁ W % (n={nb} realnych worków) ===", "",
         f"{'reguła':<40} {'ZIOMEK':>9} {'APP':>9} {'CONSOLE':>9}", "-"*70]
    L.append(f"{'R-NO-RETURN: worki BEZ powrotu (czyste)':<40} " + " ".join(f"{100.0*bt0[p]/nb:>8.0f}%" for p in POL))
    L.append(f"{'pickup-order wg gotowości (% par OK)':<40} " + " ".join(f"{(100.0*pk[p][0]/pk[p][1] if pk[p][1] else 0):>8.0f}%" for p in POL))
    L.append(f"{'R6 ≤35min: worki bez przekroczenia':<40} " + " ".join(f"{100.0*r6clean[p]/nb:>8.0f}%" for p in POL))
    # overall = średnia 3 reguł
    ov = {p: (100.0*bt0[p]/nb + (100.0*pk[p][0]/pk[p][1] if pk[p][1] else 0) + 100.0*r6clean[p]/nb)/3 for p in POL}
    L.append(f"{'— ŁĄCZNY wskaźnik adherencji (śr. 3 reguł)':<40} " + " ".join(f"{ov[p]:>8.0f}%" for p in POL))
    L.append("")

    # --- WOREK BARTKA (cztery zlecenia, badane dziś) ---
    BAR = ["481634", "481635", "481641", "481618"]  # sushi→Kopernika, sushi→Kijowska, toriko→Młynowa, kebab→Konopnickiej(czasówka 15:40)
    con = sqlite3.connect("/root/.openclaw/workspace/dispatch_state/events.db"); con.row_factory = sqlite3.Row
    bag = {}
    for oid in BAR:
        r = con.execute("SELECT payload FROM events WHERE order_id=? AND event_type='NEW_ORDER' ORDER BY created_at LIMIT 1", (oid,)).fetchone()
        if not r: continue
        p = json.loads(r["payload"])
        bag[oid] = {"pc": p.get("pickup_coords"), "dc": p.get("delivery_coords"),
                    "ready": p.get("pickup_at_warsaw"), "rest": (p.get("restaurant") or "").replace("&amp;", "&"),
                    "addr": p.get("delivery_address"), "type": p.get("order_type"), "uwagi": p.get("uwagi") or ""}
    oids = [o for o in BAR if o in bag and bag[o]["pc"] and bag[o]["dc"] and bag[o]["ready"]]
    short = {"481634": "sushi→Kopernika", "481635": "sushi→Kijowska", "481641": "toriko→Młynowa", "481618": "KEBAB→Konopnickiej[czasówka15:40]"}
    if len(oids) >= 2:
        ready = {o: M.pdt(bag[o]["ready"]) for o in oids}
        stops, Mx, START, st0, seqs = run_three(oids, bag, ready)
        L.append(f"=== WOREK BARTKA: {len(oids)} zleceń (badane dziś) ===")
        for o in oids:
            L.append(f"   {o} {short.get(o,'?'):<32} rest={bag[o]['rest']} ready={bag[o]['ready'][11:16]}")
        L.append("")
        for p in POL:
            food, mk, dv = M.simulate(seqs[p], stops, Mx, START, st0, ready)
            order_str = " → ".join(f"{'↑' if stops[s][0]=='p' else '↓'}{short.get(stops[s][1],stops[s][1]).split('→')[0] if stops[s][0]=='p' else short.get(stops[s][1],stops[s][1]).split('→')[1]}" for s in seqs[p])
            kebab_dv = dv.get("481618")
            keb = f"{kebab_dv.astimezone(timezone(timedelta(hours=2))).strftime('%H:%M')}" if kebab_dv else "?"
            ages = ", ".join(f"{short.get(o,o).split('→')[0]}:{food.get(o,0):.0f}" for o in oids)
            L.append(f"  [{p}] backtrack={backtrack(seqs[p],stops)} R6breach={sum(1 for v in food.values() if v>SLA)} makespan={mk:.0f}min")
            L.append(f"      kolejność: {order_str}")
            L.append(f"      food-age per zlecenie: {ages}  | KEBAB-czasówka dostawa={keb} (cel 15:40)")
        L.append("")
        L.append("  (food-age = minuty odbiór→dostawa; czasówka kebaba: żaden silnik nie ma okna-dostawy → patrz która najbliżej 15:40)")
    rep = "\n".join(L)
    open("/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-18/rules_pct_bartek_result.txt", "w").write(rep + "\n")
    print(rep)


if __name__ == "__main__":
    main()
