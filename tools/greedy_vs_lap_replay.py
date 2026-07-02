#!/usr/bin/env python3
"""
greedy_vs_lap_replay.py  (READ-ONLY analysis, Krok 0b roadmapy)

Cel: policzyc DOLNA GRANICE (lower bound) straty silnika dyspozytorskiego
wynikajacej z przypisania GREEDY (per-order best + lex) w porownaniu z
globalnie optymalnym przypisaniem kurier<->zlecenie (LAP, algorytm wegierski).

Zrodlo danych: shadow_decisions.jsonl  (jeden rekord = jedno NOWE zlecenie w
momencie przyjscia; pole `best` + `alternatives` = PELNA lista wykonalnych
kandydatow z ich `score` [obiektyw silnika, wyzej=lepiej] oraz `travel_min_cal`
[skalibrowane minuty dojazdu do odbioru]).

Model:
  * Zlecenia grupowane w okna czasowe (tumbling, domyslnie 5 min) -> zlecenia
    w tym samym oknie traktujemy jako "otwarte jednoczesnie" i konkurujace o te
    sama pule kurierow. (To PRZYBLIZENIE — patrz OGRANICZENIA w raporcie.)
  * Dla kazdej grupy budujemy macierz kosztu(zlecenie, kurier):
        basis 'score': koszt = -score   (minimalizacja == maksymalizacja score)
        basis 'eta'  : koszt = travel_min_cal (minuty dojazdu do odbioru)
  * GREEDY (wierny silnikowi): zlecenia w kolejnosci przyjscia; kazde bierze
    swojego najlepszego (max score) jeszcze WOLNEGO kuriera; jak wszyscy jego
    wykonalni zajeci -> "nieprzypisane" z kosztem fallback (najgorszy wlasny
    wykonalny kandydat = proxy "czeka, dostanie slaba opcje").
  * LAP: globalnie optymalne rozlaczne przypisanie (Hungarian) w tej samej
    przestrzeni (te same fallbacki jako dedykowane kolumny-atrapy), wiec
    LAP_cost <= GREEDY_cost zawsze -> Delta >= 0 = kierunkowy LOWER BOUND.

WAZNE: to LOWER BOUND KIERUNKOWY, nie obietnica zysku. Kazde przypisanie zmienia
przyszly stan floty (bag+1, pozycja), wiec ceteris-paribus jest NIEPRAWDZIWE.
Pomiar jest na POZIOMIE PROPOZYCJI (shadow), nie wykonania.

Uzycie:
  python3 greedy_vs_lap_replay.py [--since YYYY-MM-DD] [--until YYYY-MM-DD]
                                  [--window MIN] [--log PATH] [--json]
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime

DEFAULT_LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
SENTINEL = -1e6          # score <= to == kandydat efektywnie niewykonalny
BIG = 1e9               # koszt zabroniony w macierzy


# ---------------------------------------------------------------- Hungarian
def hungarian(cost):
    """Min-cost perfect assignment na KWADRATOWEJ macierzy cost[n][n].
    Zwraca (total_cost, row->col). O(n^3), bez zaleznosci zewnetrznych."""
    n = len(cost)
    if n == 0:
        return 0.0, []
    INF = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)   # p[j] = row przypisany do col j (1-indexed)
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
            j1 = -1
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(0, n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    match = [0] * n   # row(0-idx) -> col(0-idx)
    for j in range(1, n + 1):
        if p[j] != 0:
            match[p[j] - 1] = j - 1
    total = sum(cost[i][match[i]] for i in range(n))
    return total, match


# ---------------------------------------------------------------- load
def parse_ts(s):
    return datetime.fromisoformat(s)


def load_orders(path, since, until):
    orders = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = d.get("ts")
            if not ts:
                continue
            day = ts[:10]
            if since and day < since:
                continue
            if until and day > until:
                continue
            best = d.get("best") or {}
            alts = d.get("alternatives") or []
            cand = {}
            for c in [best] + list(alts):
                cid = c.get("courier_id")
                sc = c.get("score")
                if cid is None or sc is None:
                    continue
                if sc <= SENTINEL:
                    continue
                tmc = c.get("travel_min_cal")
                if tmc is None:
                    tmc = c.get("drive_min")
                # przy duplikacie zostaw lepszy score
                if cid in cand and cand[cid]["score"] >= sc:
                    continue
                cand[cid] = {"score": float(sc),
                             "eta": float(tmc) if tmc is not None else None,
                             "feas": c.get("feasibility"),
                             "pos": c.get("pos_source")}
            if not cand:
                continue
            orders.append({"ts": ts, "dt": parse_ts(ts),
                           "oid": str(d.get("order_id")),
                           "day": day, "cand": cand,
                           "best_cid": best.get("courier_id")})
    orders.sort(key=lambda o: o["dt"])
    return orders


# ---------------------------------------------------------------- grouping
def group_tumbling(orders, window_min):
    w = window_min * 60
    buckets = defaultdict(list)
    for o in orders:
        key = int(o["dt"].timestamp() // w)
        buckets[key].append(o)
    return [g for g in buckets.values() if len(g) >= 2]


# ---------------------------------------------------------------- costing
def cost_of(o, cid, basis):
    c = o["cand"][cid]
    if basis == "score":
        return -c["score"]
    # eta basis
    return c["eta"] if c["eta"] is not None else None


def order_fallback(o, basis):
    """Najgorszy WLASNY wykonalny koszt (proxy: czeka, dostaje slaba opcje)."""
    vals = []
    for cid in o["cand"]:
        v = cost_of(o, cid, basis)
        if v is not None:
            vals.append(v)
    return max(vals) if vals else BIG


def metric_sum(group, mapping, basis):
    """Suma metryki `basis` po przypisaniu oid->cid (None => fallback)."""
    by_oid = {o["oid"]: o for o in group}
    tot = 0.0
    for oid, cid in mapping.items():
        o = by_oid[oid]
        if cid is None:
            tot += order_fallback(o, basis)
        else:
            v = cost_of(o, cid, basis)
            tot += v if v is not None else order_fallback(o, basis)
    return tot


def greedy_assign(group, basis):
    """Wierny silnikowi greedy: kolejnosc przyjscia, kazdy bierze najlepszego
    WOLNEGO kuriera wg objektywu `basis`. Zwraca mapping oid->cid|None."""
    used = set()
    assign = {}
    for o in sorted(group, key=lambda x: x["dt"]):
        # ranking wg wybranego objektywu (score: max score; eta: min eta)
        if basis == "score":
            ranked = sorted(o["cand"].items(), key=lambda kv: -kv[1]["score"])
        else:
            ranked = sorted(
                o["cand"].items(),
                key=lambda kv: (kv[1]["eta"] if kv[1]["eta"] is not None
                                else float("inf")))
        chosen = None
        for cid, _ in ranked:
            if cid in used:
                continue
            if cost_of(o, cid, basis) is None:
                continue
            chosen = cid
            break
        if chosen is not None:
            used.add(chosen)
        assign[o["oid"]] = chosen
    return assign


def lap_assign(group, basis):
    """Globalny optimum (Hungarian) wg objektywu `basis`. Kolumny = kurierzy
    (unia) + dedykowana atrapa na kazde zlecenie (fallback = nieprzypisane).
    Zwraca mapping oid->cid|None."""
    orders = list(group)
    couriers = sorted({cid for o in orders for cid in o["cand"]
                       if cost_of(o, cid, basis) is not None})
    n = len(orders)
    cidx = {c: i for i, c in enumerate(couriers)}
    ncol = len(couriers) + n     # + atrapy (po 1 na zlecenie)
    size = max(n, ncol)
    M = [[BIG] * size for _ in range(size)]
    fallbacks = [order_fallback(o, basis) for o in orders]
    for i, o in enumerate(orders):
        for cid in o["cand"]:
            v = cost_of(o, cid, basis)
            if v is None:
                continue
            M[i][cidx[cid]] = v
        M[i][len(couriers) + i] = fallbacks[i]   # dedykowana atrapa
    for i in range(n, size):                      # wiersze-atrapy (padding)
        for j in range(size):
            M[i][j] = 0.0
    _, match = hungarian(M)
    mapping = {}
    for i, o in enumerate(orders):
        j = match[i]
        mapping[o["oid"]] = couriers[j] if j < len(couriers) else None
    return mapping


# ---------------------------------------------------------------- run
def run(orders, window_min):
    groups = group_tumbling(orders, window_min)
    days = {o["day"] for o in orders}
    tot = {
        "groups": 0, "orders_in_groups": 0, "conflict_pairs": 0,
        # objektyw SCORE (objektyw silnika) -> delty w score i w minutach
        "d_score": 0.0, "d_eta_at_score": 0.0, "groups_gain_score": 0,
        # objektyw ETA (czysta luka dopasowania w minutach)
        "d_eta_pure": 0.0, "groups_gain_eta": 0,
    }
    examples = []
    for g in groups:
        # --- objektyw SCORE (to co silnik faktycznie optymalizuje) ---
        g_map_s = greedy_assign(g, "score")
        l_map_s = lap_assign(g, "score")
        d_score = metric_sum(g, g_map_s, "score") - metric_sum(g, l_map_s, "score")
        d_eta_at_score = (metric_sum(g, g_map_s, "eta")
                          - metric_sum(g, l_map_s, "eta"))
        # --- objektyw ETA (czysta luka minut, gdyby celem byl dojazd) ---
        g_map_e = greedy_assign(g, "eta")
        l_map_e = lap_assign(g, "eta")
        d_eta_pure = metric_sum(g, g_map_e, "eta") - metric_sum(g, l_map_e, "eta")

        d_score = 0.0 if d_score < 1e-6 else d_score
        d_eta_pure = 0.0 if d_eta_pure < 1e-6 else d_eta_pure

        bc = defaultdict(int)
        for o in g:
            if o["best_cid"]:
                bc[o["best_cid"]] += 1
        conflict = sum(1 for v in bc.values() if v >= 2)

        tot["groups"] += 1
        tot["orders_in_groups"] += len(g)
        tot["conflict_pairs"] += conflict
        tot["d_score"] += d_score
        tot["d_eta_at_score"] += d_eta_at_score
        tot["d_eta_pure"] += d_eta_pure
        if d_score > 0:
            tot["groups_gain_score"] += 1
        if d_eta_pure > 0:
            tot["groups_gain_eta"] += 1
        if d_score > 0 and len(examples) < 15:
            examples.append({
                "day": g[0]["day"], "ts": g[0]["ts"], "n": len(g),
                "d_score": round(d_score, 1),
                "d_eta_at_score_min": round(d_eta_at_score, 2)})
    return tot, examples, len(groups), len(days)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--window", type=int, default=5, help="okno grupowania (min)")
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    orders = load_orders(args.log, args.since, args.until)
    if not orders:
        print("Brak zlecen w zakresie.", file=sys.stderr)
        sys.exit(1)

    tot, examples, ngroups, ndays = run(orders, args.window)
    N = len(orders)
    ndays = ndays or 1
    out = {
        "log": args.log, "window_min": args.window,
        "range": [orders[0]["day"], orders[-1]["day"]],
        "total_orders": N, "days": ndays,
        "multi_order_groups": ngroups,
        "orders_in_groups": tot["orders_in_groups"],
        "conflict_pairs": tot["conflict_pairs"],
        # objektyw silnika (score)
        "score_objective": {
            "delta_total": round(tot["d_score"], 1),
            "delta_per_day": round(tot["d_score"] / ndays, 1),
            "delta_per_order_all": round(tot["d_score"] / N, 3),
            "groups_with_gain": tot["groups_gain_score"],
            "pct_groups_gain": round(100 * tot["groups_gain_score"] / max(ngroups, 1), 1),
            # ta sama score-optymalna przetasowka wyrazona w minutach dojazdu:
            "delta_eta_min_total": round(tot["d_eta_at_score"], 1),
            "delta_eta_min_per_day": round(tot["d_eta_at_score"] / ndays, 2),
            "delta_eta_min_per_order_all": round(tot["d_eta_at_score"] / N, 3),
        },
        # objektyw ETA (czysta luka minut dojazdu do odbioru)
        "eta_objective": {
            "delta_min_total": round(tot["d_eta_pure"], 1),
            "delta_min_per_day": round(tot["d_eta_pure"] / ndays, 2),
            "delta_min_per_order_all": round(tot["d_eta_pure"] / N, 3),
            "groups_with_gain": tot["groups_gain_eta"],
            "pct_groups_gain": round(100 * tot["groups_gain_eta"] / max(ngroups, 1), 1),
        },
        "examples": examples,
    }

    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return

    so, eo = out["score_objective"], out["eta_objective"]
    print(f"# greedy_vs_lap_replay  log={args.log}")
    print(f"zakres: {out['range'][0]}..{out['range'][1]} | {ndays} dni | "
          f"{N} zlecen | okno {args.window} min")
    print(f"grupy wielo-zleceniowe: {ngroups} | zlecen w grupach: "
          f"{tot['orders_in_groups']} | pary-konflikty (ten sam best): "
          f"{tot['conflict_pairs']}")
    print("\n== OBJEKTYW SILNIKA = SCORE (greedy-by-score vs LAP-by-score) ==")
    print(f"  DELTA score total:         {so['delta_total']}  "
          f"(/dzien {so['delta_per_day']}, /zlecenie {so['delta_per_order_all']})")
    print(f"  grupy z zyskiem:           {so['groups_with_gain']} "
          f"({so['pct_groups_gain']}% grup)")
    print(f"  ta sama przetasowka w MINUTACH dojazdu do odbioru:")
    print(f"    DELTA min total:         {so['delta_eta_min_total']}  "
          f"(/dzien {so['delta_eta_min_per_day']}, "
          f"/zlecenie {so['delta_eta_min_per_order_all']})")
    print("\n== OBJEKTYW = ETA (czysta luka min dojazdu; greedy-by-eta vs LAP-by-eta) ==")
    print(f"  DELTA min total:           {eo['delta_min_total']}  "
          f"(/dzien {eo['delta_min_per_day']}, "
          f"/zlecenie {eo['delta_min_per_order_all']})")
    print(f"  grupy z zyskiem:           {eo['groups_with_gain']} "
          f"({eo['pct_groups_gain']}% grup)")


if __name__ == "__main__":
    main()
