#!/usr/bin/env python3
"""W jakim stopniu KONSOLA vs APKA vs ZIOMEK trzymają się reguł — i którą trasę
kurierzy FAKTYCZNIE jeżdżą (realized). Na realnych workach events.db.

Reguły mierzone:
  R-NO-RETURN (backtrack): odbiór PO dostawie (powrót po jedzenie) — ma być 0.
  pickup-order: odbiory w kolejności gotowości/committed (nie wg geografii) — % par OK.
  R6: dostawy >35 min.
  podjazd-struktura: czy odbiory partii są PRZED dostawami partii (nie przeplatane).
Resemblance: zgodność kolejności DOSTAW realized↔konsola vs realized↔apka
  (którą realnie jadą kurierzy).
"""
import sys, importlib.util as u
import statistics as stt
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
_p = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-18/console_vs_app_route.py"
_s = u.spec_from_file_location("cva", _p); M = u.module_from_spec(_s); _s.loader.exec_module(M)


def deliv_order(seq, stops):
    return [stops[s][1] for s in seq if stops[s][0] == "d"]


def pickup_order_ok(seq, stops, ready):
    """% kolejnych par odbiorów w niemalejącej kolejności gotowości."""
    pk = [stops[s][1] for s in seq if stops[s][0] == "p"]
    if len(pk) < 2: return None
    ok = tot = 0
    for a, b in zip(pk, pk[1:]):
        ra, rb = ready.get(a), ready.get(b)
        if ra and rb:
            tot += 1; ok += 1 if ra <= rb else 0
    return (ok, tot)


def interleave_ok(seq, stops):
    """podjazd-struktura: czy NIE ma przeplotu odbiór↔dostawa w środku (poza granicą kursów).
    Prosty proxy: liczba przejść dostawa→odbiór (=nowy kurs) — niskie = czyste partie."""
    return sum(1 for i in range(1, len(seq)) if stops[seq[i]][0] == "p" and stops[seq[i-1]][0] == "d")


def concordance(a_order, ref_order):
    """frakcja par zleceń o tej samej względnej kolejności co ref (realized)."""
    common = [o for o in a_order if o in ref_order]
    if len(common) < 2: return None
    pos_ref = {o: i for i, o in enumerate(ref_order)}
    pos_a = {o: i for i, o in enumerate(a_order)}
    n = ok = 0
    for i in range(len(common)):
        for j in range(i + 1, len(common)):
            x, y = common[i], common[j]
            n += 1
            if (pos_a[x] < pos_a[y]) == (pos_ref[x] < pos_ref[y]): ok += 1
    return (ok, n)


def main():
    comp, bags = M.load(); sys.stderr.write(f"[rule] bags={len(bags)}\n")
    POL = ["ZIOMEK", "APP", "CONSOLE"]
    bt = {p: [] for p in POL}; pk_ok = {p: [0, 0] for p in POL}; r6 = {p: 0 for p in POL}; ncourse = {p: [] for p in POL}
    conc_real = {"CONSOLE": [0, 0], "APP": [0, 0], "ZIOMEK": [0, 0]}
    n = 0
    from datetime import timedelta
    for cid, oids in bags:
        if not (2 <= len(oids) <= 6): continue
        ready = {o: M.pdt(comp[o].get("ready")) for o in oids}
        if any(v is None for v in ready.values()): continue
        stops = []
        for o in oids: stops.append(("p", o)); stops.append(("d", o))
        so = min(oids, key=lambda o: ready[o])
        coords_l = [tuple(comp[o]["pc"]) if k == "p" else tuple(comp[o]["dc"]) for (k, o) in stops]
        start_c = tuple(comp[so]["pc"]); pts = [start_c] + coords_l
        try: M0 = M.osrm_matrix(pts)
        except Exception: continue
        m = len(stops); Mx = [[M0[i+1][j+1] for j in range(m)] + [0.0] for i in range(m)] + [[M0[0][j+1] for j in range(m)] + [0.0]]
        coords = coords_l + [start_c]; START = m; st0 = ready[so]
        ev = []
        for o in oids:
            ev.append((M.pdt(comp[o]["pt"]), stops.index(("p", o)))); ev.append((M.pdt(comp[o]["dt"]), stops.index(("d", o))))
        ev.sort(key=lambda e: e[0])
        real_seq = [i for _, i in ev]
        seqs = {"ZIOMEK": M.brute(stops, Mx, START, st0, ready, "makespan"),
                "APP": M.app_minlat(stops, Mx, START, st0, ready, coords),
                "CONSOLE": M.console_seq(oids, comp, ready, stops)}
        n += 1
        real_do = deliv_order(real_seq, stops)
        for p in POL:
            food, mk, dv = M.simulate(seqs[p], stops, Mx, START, st0, ready)
            if any(v > M.SLA for v in food.values()): r6[p] += 1
            bt[p].append(interleave_ok(seqs[p], stops))
            ncourse[p].append(interleave_ok(seqs[p], stops))
            po = pickup_order_ok(seqs[p], stops, ready)
            if po: pk_ok[p][0] += po[0]; pk_ok[p][1] += po[1]
            c = concordance(deliv_order(seqs[p], stops), real_do)
            if c: conc_real[p][0] += c[0]; conc_real[p][1] += c[1]
    def med(x): return round(stt.median(x), 2) if x else None
    L = ["=== ADHERENCJA REGUŁ + co kurierzy FAKTYCZNIE jeżdżą (realne worki events.db) ===",
         f"worki 2-6 = {n}", "",
         f"{'reguła':<42} {'ZIOMEK':>9} {'APP':>9} {'CONSOLE':>9}", "-"*72]
    L.append(f"{'R-NO-RETURN: backtrack/powroty (median, ↓=lepiej)':<42} {med(bt['ZIOMEK']):>9} {med(bt['APP']):>9} {med(bt['CONSOLE']):>9}")
    L.append(f"{'pickup-order wg gotowości (% par OK, ↑=lepiej)':<42} " + " ".join(
        f"{(100.0*pk_ok[p][0]/pk_ok[p][1] if pk_ok[p][1] else 0):>8.0f}%" for p in POL))
    L.append(f"{'R6 breach (worki >35min, ↓=lepiej)':<42} {r6['ZIOMEK']:>9} {r6['APP']:>9} {r6['CONSOLE']:>9}")
    L.append("")
    L.append("── KTÓRĄ TRASĘ KURIERZY FAKTYCZNIE JADĄ (zgodność kolejności dostaw z realized, ↑) ──")
    for p in ["CONSOLE", "APP", "ZIOMEK"]:
        v = conc_real[p]
        L.append(f"  realized ↔ {p:<8}: {(100.0*v[0]/v[1] if v[1] else 0):.0f}% zgodnych par ({v[0]}/{v[1]})")
    L.append("")
    L.append("Wniosek: wyższa zgodność realized↔X = kurierzy de-facto jadą bliżej X.")
    rep = "\n".join(L)
    open("/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-18/rule_adherence_result.txt", "w").write(rep + "\n")
    print(rep)


if __name__ == "__main__":
    main()
