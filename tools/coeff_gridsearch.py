#!/usr/bin/env python3
"""
[D2] GRID-SEARCH OBJ_COMMITTED_PICKUP_PENALTY_COEFF — CZYSTO OFFLINE (READ-ONLY).
2026-06-20. Rekomendacja liczbowa; NIC nie zmienia w live (flagi/kod/restart).

KONTEKST
========
Kara punktualności committed-pickup w funkcji celu solvera JEST ŻYWA od 18.06
(flaga ENABLE_OBJ_COMMITTED_PICKUP_PENALTY=true, coeff flipnięty na ~100).
To zadanie sprawdza na replayu czy INNY coeff jest lepszy. Siatka:
    {25, 50, 75, 100, 125, 150, 200, 300}

JAK COEFF WCHODZI DO CELU (route_simulator_v2.py:1131-1160 → tsp_solver)
========================================================================
Dla każdego węzła pickup, którego ref ma czas_kuriera_warsaw (obietnica dla
restauracji), solver dostaje SOFT upper bound na czas przyjazdu:
    bound_min = (czas_kuriera − now)/min + tolerance
    coeff     = OBJ_COMMITTED_PICKUP_PENALTY_COEFF
SetCumulVarSoftUpperBound → kara = coeff · max(0, przyjazd − bound). SOFT —
NIGDY INFEASIBLE (lekcja 7500 decyzji/d). tolerance = load-aware: 5 strict /
10 niedobór (loadgov_ewma≥4.5). Wyższy coeff → solver mocniej unika ślizgania
committed-odbiorów, kosztem ewentualnego pogorszenia geometrii reszty trasy.

METODOLOGIA (apples-to-apples vs WSPÓLNY baseline OFF)
=====================================================
Replay obj_replay_capture.jsonl (STRUMIENIOWO — 70MB, NIE do RAM). Capture to
WEJŚCIE do funkcji celu (pozycja kuriera, worek committed-orderów, nowy order),
więc re-symulujemy trasę pod każdym coeff. Per kwalifikujący się rekord:
  1. plan OFF (flaga off) — liczony RAZ (nie zależy od coeff)
  2. plan ON dla każdego coeff z siatki
  3. metryka = committed_late_max = max po committed-pickupach z
     (planowany pickup_at − czas_kuriera) [min]
  4. delta = OFF.committed_late_max − ON.committed_late_max
     >0 → ON redukuje spóźnienie committed (DOBRZE)
     <0 → ON przesuwa committed-pickup PÓŹNIEJ (REGRESJA — bound ruszył geometrię)

KWALIFIKACJA rekordu (jak w walidacji N5-S2 18.06, reused verbatim z M.*):
  - okno dni 14-16.06 (kalibrowane capture)
  - FILTR: (pickup_ready nowego − now) ≤ 65 min (dalekie czasówki nie są
    proponowane na żywo)
  - ≥1 committed-pickup w worku (inaczej kara nie ma celu)

MAPOWANIE NA G1/G2/G3 (per coeff, vs OFF)
=========================================
  G1 (on-time / cel)  = NET committed-late minut zaoszczędzonych = sum(reduced)
                        − sum(regr). ⚠ PROXY (committed-deviation w PLANIE), bo
                        capture NIE ma realnego on-time (delivered/actual pickup
                        outcome). Wyższy = lepiej.
  G2a (avg deviation) = średnia regresja committed-late na rekord [min/rec]
                        = sum(regr) / pop. Niżej = lepiej.
  G2b (nowe R6-breach)= liczba decyzji z NOWĄ regresją committed-late
                        (count regr) — odpowiednik „solver zepsuł komuś czas".
  G3 (propose / KOORD)= delta INFEASIBLE/fallback ON−OFF. >0 = więcej decyzji
                        spada do fallbacku (→ KOORD zamiast propose). 0 = bez
                        wzrostu KOORD. Niżej = lepiej.

KOLANO PARETO
=============
Front Pareto na (G1↑, regr_sum↓). Kolano = punkt maksymalizujący stosunek
przyrostu G1 do przyrostu kosztu regresji względem najtańszego sensownego
coeff (znormalizowana odległość od linii bazowej; deterministyczne).

URUCHOMIENIE (wymaga ortools → interpreter venv dispatch; „python3"=Py3):
  /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.coeff_gridsearch \\
      --workers 4 --dump out.json
  (--sample N → próbkuj co k-ty rekord, dla szybkiego smoke; domyślnie pełne 7564)
"""
import argparse
import json
import sys
from collections import defaultdict
from multiprocessing import Pool

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
sys.path.insert(0, "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")

import dispatch_v2.common as C  # noqa: E402
from dispatch_v2.route_simulator_v2 import (  # noqa: E402
    simulate_bag_route_v2, set_committed_pickup_tolerance)
import n5s2_committed_penalty_replay as M  # noqa: E402  (reviewed metric logic)

# Pełna siatka D2 (zadanie). Środek ~150 = oczekiwane kolano.
GRID = [25, 50, 75, 100, 125, 150, 200, 300]

BORDERLINE_MIN = M.BORDERLINE_MIN
EWMA_THRESHOLD = M.EWMA_THRESHOLD
TOL_STRICT = M.TOL_STRICT
TOL_LOOSE = M.TOL_LOOSE
DAYS = M.DAYS

# worker globals (set via initializer)
_BY_OID = None
_COEFFS = None


def _init(by_oid, coeffs):
    global _BY_OID, _COEFFS
    _BY_OID = by_oid
    _COEFFS = coeffs


def _process(rec):
    """Jeden rekord → OFF raz + ON per coeff. Zwraca dict z per-coeff outcome.
    per-coeff: (coeff, kind, abs_delta, inf_on)  kind in {red,regr,border,nolate}.
    """
    (day, d, now, no, bag) = rec
    out = dict(day=day, simfail=False, infeasible_off=False, skip=False, coeffs=[])
    cp = tuple(d["courier_pos"]) if d.get("courier_pos") else None
    if cp is None:
        out["skip"] = True
        return out
    ck_by_oid = {}
    for o in bag:
        if M.has_ck(o):
            ck = M.pts(o.get("czas_kuriera_warsaw"))
            if ck:
                ck_by_oid[str(o.get("order_id"))] = ck
    if not ck_by_oid:
        out["skip"] = True
        return out

    ewma = M.ewma_for(_BY_OID, d.get("order_id"), now)
    tol = TOL_LOOSE if (ewma is not None and ewma >= EWMA_THRESHOLD) else TOL_STRICT

    # ---- OFF raz (nie zależy od coeff) ----
    M.patch_off()
    set_committed_pickup_tolerance(None)
    try:
        plan_off = simulate_bag_route_v2(cp, [M.mk(o) for o in bag], M.mk(no), now=now)
    except Exception:
        out["simfail"] = True
        return out
    out["infeasible_off"] = M.is_fallback(plan_off)
    lo, _ = M.committed_late_max(plan_off, ck_by_oid)

    # ---- ON per coeff ----
    for coeff in _COEFFS:
        C.OBJ_COMMITTED_PICKUP_PENALTY_COEFF = float(coeff)
        M.patch_on()
        set_committed_pickup_tolerance(tol)
        try:
            plan_on = simulate_bag_route_v2(cp, [M.mk(o) for o in bag], M.mk(no), now=now)
        except Exception:
            M.patch_off()
            out["simfail"] = True
            return out
        M.patch_off()
        set_committed_pickup_tolerance(None)

        inf_on = M.is_fallback(plan_on)
        ln, _o = M.committed_late_max(plan_on, ck_by_oid)
        if lo is None or ln is None:
            out["coeffs"].append((coeff, "nolate", 0.0, inf_on))
            continue
        delta = lo - ln
        if abs(delta) < BORDERLINE_MIN:
            out["coeffs"].append((coeff, "border", 0.0, inf_on))
        elif delta > 0:
            out["coeffs"].append((coeff, "red", delta, inf_on))
        else:
            out["coeffs"].append((coeff, "regr", -delta, inf_on))
    return out


# --------------------------------------------------------------------------
# Pareto + kolano (czysta logika — testowane osobno)
# --------------------------------------------------------------------------
def pareto_front(points):
    """points: list of dict z 'coeff','g1'(↑ lepiej),'regr_sum'(↓ lepiej).
    Zwraca podzbiór niedominowanych (front), posortowany rosnąco po regr_sum.
    A dominuje B gdy g1_A>=g1_B AND regr_sum_A<=regr_sum_B i co najmniej jedna ostra.
    """
    front = []
    for p in points:
        dominated = False
        for q in points:
            if q is p:
                continue
            if (q["g1"] >= p["g1"] and q["regr_sum"] <= p["regr_sum"]
                    and (q["g1"] > p["g1"] or q["regr_sum"] < p["regr_sum"])):
                dominated = True
                break
        if not dominated:
            front.append(p)
    return sorted(front, key=lambda x: x["regr_sum"])


def knee_point(points):
    """Kolano = punkt na froncie Pareto o największej znormalizowanej odległości
    prostopadłej od cięciwy łączącej skrajne punkty frontu (metoda Kneedle-like,
    deterministyczna). Oś X = regr_sum (koszt, ↓), oś Y = g1 (zysk, ↑).

    Zwraca (knee_dict, front_list). Gdy front ma <3 punkty: kolano = punkt o
    najlepszym g1 przy minimalnym regr_sum (najtańszy maks-zysk).
    """
    front = pareto_front(points)
    if not front:
        return None, front
    if len(front) < 3:
        # za mało punktów na geometryczne kolano: weź maks G1, remis → niższy coeff
        best = max(front, key=lambda p: (p["g1"], -p["regr_sum"], -p["coeff"]))
        return best, front

    xs = [p["regr_sum"] for p in front]
    ys = [p["g1"] for p in front]
    x0, x1 = xs[0], xs[-1]
    y0, y1 = ys[0], ys[-1]
    dx = (x1 - x0) or 1e-9
    dy = (y1 - y0) or 1e-9

    best = None
    best_score = -1.0
    for p in front:
        # znormalizowane współrzędne 0..1 względem skrajów frontu
        nx = (p["regr_sum"] - x0) / dx
        ny = (p["g1"] - y0) / dy
        # odległość pionowa od cięciwy (która w znorm. układzie biegnie y=x)
        # punkt powyżej cięciwy (więcej zysku przy danym koszcie) = lepsze kolano
        dist = ny - nx
        if dist > best_score:
            best_score = dist
            best = p
    # gdy cięciwa płaska (wszystkie ~równe) — fallback maks G1
    if best is None or best_score <= 0:
        best = max(front, key=lambda p: (p["g1"], -p["regr_sum"], -p["coeff"]))
    return best, front


def build_metrics(stats, coeffs):
    """stats[coeff][day] -> agregaty. Zwraca list per-coeff dict z G1/G2/G3."""
    rows = []
    for coeff in coeffs:
        pop = inf_on = inf_off = 0
        red = []
        regr = []
        border = 0
        nolate = 0
        for day in DAYS:
            P = stats[coeff][day]
            pop += P["pop"]
            inf_on += P["infeasible_on"]
            inf_off += P["infeasible_off"]
            border += P["border"]
            nolate += P["off_nolate"]
            red += P["red"]
            regr += P["regr"]
        red_sum = sum(red)
        regr_sum = sum(regr)
        g1 = round(red_sum - regr_sum, 1)
        g2a = round(regr_sum / pop, 4) if pop else 0.0
        g2b = len(regr)
        g3 = inf_on - inf_off
        rows.append(dict(
            coeff=coeff, pop=pop,
            reduced=len(red), red_sum=round(red_sum, 1),
            regr=g2b, regr_sum=round(regr_sum, 1),
            border=border, nolate=nolate,
            inf_on=inf_on, inf_off=inf_off,
            g1=g1, g2a=g2a, g2b=g2b, g3=g3))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coeffs", default=",".join(str(c) for c in GRID),
                    help="siatka coeff (domyślnie pełna D2)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--sample", type=int, default=0,
                    help="weź co --sample-ty rekord (0=wszystkie 7564)")
    ap.add_argument("--dump", default="",
                    help="zapis JSON z metrykami+frontem+kolanem")
    args = ap.parse_args()
    coeffs = [int(x) for x in args.coeffs.split(",")]

    recs, filt = M.load_records()
    if args.sample and args.sample > 1:
        recs = recs[:: args.sample]
        sampled_note = f"PRÓBKA: co {args.sample}-ty → {len(recs)} z {filt['qualified']}"
    else:
        sampled_note = f"PEŁNA POPULACJA: {len(recs)}"

    print("=" * 100)
    print("[D2] GRID-SEARCH OBJ_COMMITTED_PICKUP_PENALTY_COEFF (READ-ONLY, vs baseline OFF)")
    print("Metryka: committed-late w PLANIE (planowany pickup_at − czas_kuriera), max per worek")
    print("=" * 100)
    print(f"linie capture:            {filt['total']}")
    print(f"w oknie (14-16.06):       {filt['window']}")
    print(f"ODFILTROWANE (ready-now>65m): {filt['filtered']}  (dalekie czasówki nie proponowane)")
    print(f"bez committed w worku:    {filt['no_committed']}  (kara bez celu)")
    print(f"KWALIFIKUJĄCE: {filt['qualified']}")
    print(sampled_note)
    by_oid = M.build_ewma_index()
    print(f"shadow EWMA index order_ids w oknie: {len(by_oid)}")
    print(f"workers: {args.workers}  coeffs: {coeffs}", flush=True)

    with Pool(args.workers, initializer=_init, initargs=(by_oid, coeffs)) as pool:
        results = pool.map(_process, recs, chunksize=16)

    stats = {c: defaultdict(lambda: dict(pop=0, red=[], regr=[],
             infeasible_on=0, infeasible_off=0, border=0, simfail=0, off_nolate=0))
             for c in coeffs}
    n_simfail = n_skip = 0
    for r in results:
        if r.get("skip"):
            n_skip += 1
            continue
        if r["simfail"]:
            n_simfail += 1
            for c in coeffs:
                stats[c][r["day"]]["simfail"] += 1
            continue
        day = r["day"]
        for c in coeffs:
            stats[c][day]["pop"] += 1
            if r["infeasible_off"]:
                stats[c][day]["infeasible_off"] += 1
        for (coeff, kind, val, inf_on) in r["coeffs"]:
            S = stats[coeff][day]
            if inf_on:
                S["infeasible_on"] += 1
            if kind == "nolate":
                S["off_nolate"] += 1
            elif kind == "border":
                S["border"] += 1
            elif kind == "red":
                S["red"].append(val)
            elif kind == "regr":
                S["regr"].append(val)

    print(f"\nrekordów przetworzonych: {len(results)}  (skip={n_skip}, simfail={n_simfail})")

    rows = build_metrics(stats, coeffs)
    knee, front = knee_point(rows)

    # ---- tabela coeff → metryki ----
    print("\n" + "#" * 100)
    print("### TABELA coeff → (G1, G2, G3)")
    print("#" * 100)
    hdr = (f"{'coeff':>6}{'pop':>7}{'reduced':>9}{'red_sum':>10}"
           f"{'regr#(G2b)':>12}{'regr_sum':>10}{'G1_net':>10}"
           f"{'G2a_avg':>10}{'G3_KOORD':>10}{'border':>8}")
    print(hdr)
    print("-" * len(hdr))
    for row in rows:
        mark = "  <== KOLANO" if (knee and row["coeff"] == knee["coeff"]) else ""
        front_mark = " *" if any(f["coeff"] == row["coeff"] for f in front) else "  "
        print(f"{row['coeff']:>6}{row['pop']:>7}{row['reduced']:>9}{row['red_sum']:>10}"
              f"{row['regr']:>12}{row['regr_sum']:>10}{row['g1']:>10}"
              f"{row['g2a']:>10}{row['g3']:>10}{row['border']:>8}{front_mark}{mark}")
    print("\n  Legenda: G1=NET committed-late zaoszczędzony [min] (↑ lepiej, PROXY) | "
          "G2a=śr. regresja [min/rec] (↓) | G2b=#decyzji z regresją (↓) | "
          "G3=delta INFEASIBLE ON−OFF (↓; 0=brak wzrostu KOORD) | * = front Pareto")

    # ---- front Pareto + kolano ----
    print("\n" + "#" * 100)
    print("### FRONT PARETO (G1↑ vs regr_sum↓) i KOLANO")
    print("#" * 100)
    print(f"{'coeff':>6}{'G1_net':>10}{'regr_sum':>10}{'G3_KOORD':>10}")
    for p in front:
        mark = "  <== KOLANO" if (knee and p["coeff"] == knee["coeff"]) else ""
        print(f"{p['coeff']:>6}{p['g1']:>10}{p['regr_sum']:>10}{p['g3']:>10}{mark}")

    if knee:
        print(f"\n  >>> REKOMENDACJA (kolano Pareto): coeff = {knee['coeff']}")
        print(f"      G1 NET committed-late zaoszczędzony: {knee['g1']} min")
        print(f"      G2 regresja: {knee['regr']} decyzji, sum {knee['regr_sum']} min "
              f"(śr {knee['g2a']} min/rec)")
        print(f"      G3 wzrost KOORD (INFEASIBLE ON−OFF): {knee['g3']} "
              f"({'BRAK — propose% nietknięte' if knee['g3'] == 0 else 'UWAGA'})")

    if args.dump:
        with open(args.dump, "w") as fh:
            json.dump(dict(rows=rows, front=front, knee=knee,
                           filt=filt, sampled=sampled_note,
                           processed=len(results), simfail=n_simfail),
                      fh, indent=2, ensure_ascii=False)
        print(f"\ndump -> {args.dump}")


if __name__ == "__main__":
    main()
