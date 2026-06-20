#!/usr/bin/env python3
"""[B4] ~8% worków → greedy_fallback (INFEASIBLE w OR-Tools): charakterystyka +
test SOFT-BOUND vs hard time-windows. READ-ONLY (NIE dotyka silnika).

KONTEKST (ustalony z kodu, 2026-06-20):
  warmstart_gap.py zmierzył: ~8% worków bag>=3 spada do greedy_fallback; dłuższy
  czas solvera NIE ratuje (greedy@200 == greedy@2000, greedy_lo_only=0).

  Mechanizm INFEASIBLE w prod (route_simulator_v2._ortools_plan → _solve):
    1) solve z time_windows (pickup NEW = (open, open+60), committed = loose upper)
    2) JEŚLI None/empty → RETRY bez time_windows (linia 1217-1239)
    3) caller (simulate_bag_route_v2:444) None → _greedy_plan, strategy=greedy_fallback
  ⇒ Worek kończy greedy_fallback TYLKO gdy NAWET retry-bez-okien zwraca None.
     To znaczy: dla tych worków hard time-windows NIE są jedyną przyczyną —
     coś strukturalnego (geometry/precedence/max_route/degenerate matrix) też.

  Flagi LIVE istotne:
    ENABLE_V327_TSP_TIME_WINDOWS=1 (pickup hard window)
    ENABLE_OBJ_FOOD_AGE_HARD_SLA=False (hard span pickup→delivery NIEAKTYWNY)
    ENABLE_OBJ_COMMITTED_PICKUP_PENALTY=true (soft, committed punctuality)
  ⇒ W LIVE jedyne TWARDE ograniczenie okna = NEW-pickup (open, open+60) + max_route=120.

CO ROBIMY (READ-ONLY, OFFLINE na obj_replay_capture.jsonl):
  A) Znajdź worki greedy_fallback (replay simulate_bag_route_v2, prod flagi).
  B) Charakterystyka: bag_size, committed-vs-new, ciasnota okien, pora dnia,
     ile realnie/dzień, ile to "małe niespełnialne okna" vs strukturalne.
  C) LADDER relaksacji wprost na tsp_solver.solve_tsp_with_constraints (te same
     macierze/pary co prod _ortools_plan buduje — odtwarzamy je z nodes):
       L0  prod = time_windows hard (open, open+60)   [baseline = co dziś INFEASIBLE]
       L1  no time_windows                            [retry prod robi sam]
       L2  SOFT-BOUND pickup window (AddSoftUpperBound zamiast SetRange) coeff sweep
       L3  max_route 120→180                          [czy to cap, nie okno?]
       L4  całkowicie bez ograniczeń                  [degenerate-matrix detektor]
     Dla każdego L: feasible? makespan, ORAZ koszt twardych reguł:
       committed_breach = max(plan_pickup − czas_kuriera) > R27 tol (±5)
       r6_breach = #dostaw z (delivered − pickup/anchor) > 35 min  [R-35MIN-MAX]
  D) WERDYKT: czy soft-bound odzyskuje JAKOŚĆ vs greedy, na ilu workach, kosztem
     ilu naruszeń committed/R6. GO tylko gdy zysk BEZ wzrostu naruszeń twardych.

URUCHOMIENIE (ortools → venv dispatch):
  /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.infeasible_bags_probe \\
      --scan 4000 --soft-coeffs 50,200,1000 --dump out.json
"""
import argparse
import json
import sys
from datetime import timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
sys.path.insert(0, "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")

import dispatch_v2.common as C  # noqa: E402
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2  # noqa: E402
from dispatch_v2 import tsp_solver  # noqa: E402
import n5s2_committed_penalty_replay as M  # noqa: E402  (reuse pts/mk/has_ck)

CAP = M.CAP
R27_TOL_MIN = 5.0          # R-DECLARED-TIME ±5 (frozen committed window)
R6_MAX_MIN = 35.0          # R-35MIN-MAX (hard SLA, zimna potrawa)
INFEASIBLE_SENTINEL = 9000.0   # makespan greedy_fallback ma fikcyjny ~20000+


def is_greedy(plan):
    s = (getattr(plan, "strategy", "") or "").lower()
    return ("greedy" in s) or ("fallback" in s) or ("rejected" in s) \
        or (not getattr(plan, "sequence", None))


def makespan(plan):
    return getattr(plan, "total_duration_min", None)


# ---------------------------------------------------------------------------
# Odtworzenie macierzy/par DOKŁADNIE jak _ortools_plan, ale z kontrolą okien.
# Czytamy nodes z planu? Nie — _ortools_plan jest wewnętrzny. Zamiast tego
# replikujemy budowę nodes minimalnie z bag/new_order/courier_pos, identycznie
# jak prod (courier, [pending pickup+delivery]*, [new pickup?]+new delivery).
# UWAGA: to MUSI lustrzać prod żeby werdykt był uczciwy — używamy tego samego
# leg_min (haversine→OSRM jak prod? prod używa osrm matrix). Dla wierności
# bierzemy travel z osrm_client tak jak route_simulator (drive_min_between).
# ---------------------------------------------------------------------------
from dispatch_v2 import osrm_client  # noqa: E402


def _build_matrix(points):
    """Macierz minut — DOKŁADNIE jak prod route_simulator_v2 (osrm_client.table,
    leg_min = duration_s/60, drive_speed_mult=1.0). Wierność = uczciwy werdykt."""
    matrix = osrm_client.table(points, points)
    N = len(points)
    tm = [[0.0] * N for _ in range(N)]
    for i in range(N):
        for j in range(N):
            if i == j:
                continue
            cell = matrix[i][j]
            if cell is None:
                tm[i][j] = 9999.0
            else:
                tm[i][j] = (cell.get("duration_s") or 0) / 60.0
    return tm


def build_solver_inputs(courier_pos, bag_objs, new_obj, now):
    """Zbuduj nodes/matrix/pairs/time_windows tak jak _ortools_plan dla NEW+pending.
    Zwraca dict z polami do solve_tsp_with_constraints + meta do metryk."""
    need_pickup = getattr(new_obj, "status", "assigned") != "picked_up" \
        and getattr(new_obj, "picked_up_at", None) is None

    nodes = [dict(kind="courier", coords=courier_pos, ref=None, oid=None)]
    pairs = []
    pickup_node_meta = []   # (node_idx, ref) dla pickupów (do okien + committed)
    delivery_node_meta = []  # (node_idx, ref, picked_bool, anchor_dt)

    # pending bag: każdy order — pickup (jeśli nie odebrany) + delivery
    for o in bag_objs:
        picked = (getattr(o, "status", "assigned") == "picked_up"
                  or getattr(o, "picked_up_at", None) is not None)
        if not picked:
            p_idx = len(nodes)
            nodes.append(dict(kind="pickup", coords=o.pickup_coords, ref=o, oid=o.order_id))
            pickup_node_meta.append((p_idx, o))
            d_idx = len(nodes)
            nodes.append(dict(kind="delivery", coords=o.delivery_coords, ref=o, oid=o.order_id))
            pairs.append((p_idx, d_idx))
        else:
            d_idx = len(nodes)
            nodes.append(dict(kind="delivery", coords=o.delivery_coords, ref=o, oid=o.order_id))
        anchor = (getattr(o, "picked_up_at", None) if picked
                  else getattr(o, "pickup_ready_at", None))
        delivery_node_meta.append((d_idx, o, picked, anchor))

    # new order
    if need_pickup:
        np_idx = len(nodes)
        nodes.append(dict(kind="pickup", coords=new_obj.pickup_coords, ref=new_obj, oid=new_obj.order_id))
        pickup_node_meta.append((np_idx, new_obj))
        nd_idx = len(nodes)
        nodes.append(dict(kind="delivery", coords=new_obj.delivery_coords, ref=new_obj, oid=new_obj.order_id))
        pairs.append((np_idx, nd_idx))
    else:
        nd_idx = len(nodes)
        nodes.append(dict(kind="delivery", coords=new_obj.delivery_coords, ref=new_obj, oid=new_obj.order_id))
    picked_new = not need_pickup
    anchor_new = (getattr(new_obj, "picked_up_at", None) if picked_new
                  else getattr(new_obj, "pickup_ready_at", None))
    delivery_node_meta.append((nd_idx, new_obj, picked_new, anchor_new))

    N = len(nodes)
    tm = _build_matrix([n["coords"] for n in nodes])
    # DIAGNOZA root-cause: zdegenerowane współrzędne / sentinel cells.
    bad_coords = sum(1 for n in nodes
                     if (not n["coords"]) or tuple(n["coords"]) == (0.0, 0.0)
                     or (n["coords"][0] == 0.0 and n["coords"][1] == 0.0))
    sentinel_cells = sum(1 for i in range(N) for j in range(N)
                         if i != j and tm[i][j] >= 9000.0)

    # time_windows — prod logika: pickup NEW=(open, open+60); committed=loose upper
    tw = [None] * N
    for (idx, ref) in pickup_node_meta:
        ready = getattr(ref, "pickup_ready_at", None)
        if ready is None:
            continue
        if ready.tzinfo is None:
            ready = ready.replace(tzinfo=timezone.utc)
        open_min = max(0.0, (ready.astimezone(timezone.utc) - now).total_seconds() / 60.0)
        ck = getattr(ref, "czas_kuriera_warsaw", None)
        ck_present = ck is not None and str(ck).strip() not in ("", "None", "null", "NULL")
        if C.ENABLE_V3274_FROZEN_PICKUP_WINDOW and ck_present:
            tw[idx] = (max(0.0, open_min - C.V3274_FROZEN_PICKUP_WINDOW_MIN),
                       C.V327_DROP_TIME_WINDOW_MAX_MIN)
        else:
            tw[idx] = (open_min, open_min + C.V327_PICKUP_TIME_WINDOW_CLOSE_MIN)

    return dict(N=N, nodes=nodes, tm=tm, pairs=pairs, tw=tw,
                pickup_meta=pickup_node_meta, delivery_meta=delivery_node_meta,
                now=now, bad_coords=bad_coords, sentinel_cells=sentinel_cells)


def _clamp_sentinel(tm, cap):
    """Zwróć kopię macierzy z sentinel (>=9000) → cap (test: czy 9999 vs max_route
    to przyczyna INFEASIBLE)."""
    N = len(tm)
    out = [[tm[i][j] for j in range(N)] for i in range(N)]
    for i in range(N):
        for j in range(N):
            if i != j and out[i][j] >= 9000.0:
                out[i][j] = cap
    return out


def _solve(inp, time_windows, max_route, soft_pickup=None, soft_coeff=0,
           matrix=None, pairs=None):
    """Wywołaj solver z danym wariantem okien. soft_pickup: lista (idx, bound_min)
    do AddSoftUpperBound zamiast hard SetRange (przekazujemy jako
    pickup_freshness_penalties — ten sam prymityw SetCumulVarSoftUpperBound).
    matrix/pairs override → testy zdegenerowanych macierzy / bez-precedence."""
    N = inp["N"]
    tm = matrix if matrix is not None else inp["tm"]
    prs = pairs if pairs is not None else inp["pairs"]
    pf = None
    if soft_pickup:
        pf = [None] * N
        for (idx, bound) in soft_pickup:
            pf[idx] = (max(0.0, bound), float(soft_coeff))
    sol = tsp_solver.solve_tsp_with_constraints(
        num_stops=N,
        pickup_drop_pairs=prs,
        distance_matrix_km=tm,
        time_matrix_min=tm,
        time_windows=time_windows,
        max_route_min=max_route,
        time_limit_ms=200,
        pickup_freshness_penalties=pf,
    )
    return sol


def _walk_cumul(inp, sequence):
    """Odtwórz harmonogram (cumul minut od now) po sekwencji solvera. Zwraca
    dict node_idx -> arrival_min (bez dwell — prod liczy realny zegar osobno, my
    chcemy tylko porównać feasibility/punktualność na tej samej skali co solver)."""
    tm = inp["tm"]
    t = 0.0
    prev = 0  # courier start
    arr = {0: 0.0}
    for node in sequence:
        t += tm[prev][node]
        arr[node] = t
        prev = node
    return arr


def measure_breaches(inp, sequence):
    """committed_breach_max (min poza R27 tol) + r6_breach_count na danej sekwencji."""
    arr = _walk_cumul(inp, sequence)
    now = inp["now"]
    committed_breach = 0.0
    # mapuj oid->pickup_arr, oid->delivery_arr
    oid_pickup = {}
    for (idx, ref) in inp["pickup_meta"]:
        if idx in arr:
            oid_pickup[ref.order_id] = (arr[idx], ref)
    # committed punctuality: plan pickup vs czas_kuriera
    for oid, (parr, ref) in oid_pickup.items():
        ck = getattr(ref, "czas_kuriera_warsaw", None)
        if ck is None or str(ck).strip() in ("", "None", "null", "NULL"):
            continue
        ck_dt = C.parse_panel_timestamp(ck)
        if ck_dt is None:
            continue
        if ck_dt.tzinfo is None:
            ck_dt = ck_dt.replace(tzinfo=timezone.utc)
        ck_min = (ck_dt.astimezone(timezone.utc) - now).total_seconds() / 60.0
        late = parr - ck_min            # +late = po czasie
        over = max(0.0, abs(late) - R27_TOL_MIN)
        committed_breach = max(committed_breach, over)
    # R6: delivered - (pickup_in_plan or picked_up_at|ready anchor) > 35
    r6_breach = 0
    for (d_idx, ref, picked, anchor) in inp["delivery_meta"]:
        if d_idx not in arr:
            continue
        darr = arr[d_idx]
        oid = ref.order_id
        if oid in oid_pickup:
            base = oid_pickup[oid][0]    # pickup w tym planie
        elif anchor is not None:
            a = anchor if anchor.tzinfo else anchor.replace(tzinfo=timezone.utc)
            base = (a.astimezone(timezone.utc) - now).total_seconds() / 60.0
        else:
            continue
        if (darr - base) > R6_MAX_MIN:
            r6_breach += 1
    return committed_breach, r6_breach


def char_windows(inp):
    """Charakterystyka okien NEW-pickup: ile, jak ciasne (close-open=60 zawsze,
    ale open może być > horyzont). Zwraca min/max open, czy któryś open>max_route."""
    opens = []
    has_committed = False
    for (idx, ref) in inp["pickup_meta"]:
        ck = getattr(ref, "czas_kuriera_warsaw", None)
        if ck is not None and str(ck).strip() not in ("", "None", "null", "NULL"):
            has_committed = True
        tw = inp["tw"][idx]
        if tw is not None:
            opens.append(tw[0])
    return dict(n_pickups=len(inp["pickup_meta"]), opens=opens,
                max_open=max(opens) if opens else None,
                has_committed=has_committed)


def select_worki(scan, min_bag):
    out = []
    with open(CAP) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            bag = d.get("bag") or []
            if len(bag) < min_bag:
                continue
            if not d.get("courier_pos"):
                continue
            out.append(d)
            if len(out) >= scan:
                break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", type=int, default=4000,
                    help="ile worków bag>=min przeskanować w poszukiwaniu greedy_fallback")
    ap.add_argument("--min-bag", type=int, default=3)
    ap.add_argument("--soft-coeffs", default="50,200,1000")
    ap.add_argument("--max-route-hi", type=float, default=180.0)
    ap.add_argument("--dump", default="")
    args = ap.parse_args()
    soft_coeffs = [int(x) for x in args.soft_coeffs.split(",")]

    worki = select_worki(args.scan, args.min_bag)
    print("=" * 100)
    print("[B4] INFEASIBLE (greedy_fallback) — charakterystyka + soft-bound vs hard windows")
    print("=" * 100)
    print(f"przeskanowano worków bag>={args.min_bag}: {len(worki)}", flush=True)

    # ETAP A: znajdź infeasible przez prod simulate_bag_route_v2 (prod flagi)
    infeasible = []
    for d in worki:
        cp = tuple(d["courier_pos"])
        now = M.pts(d["now"])
        try:
            plan = simulate_bag_route_v2(
                cp, [M.mk(o) for o in d["bag"]], M.mk(d["new_order"]), now=now)
        except Exception:
            continue
        if is_greedy(plan):
            infeasible.append((d, now, cp))

    n_inf = len(infeasible)
    pct = 100.0 * n_inf / len(worki) if worki else 0.0
    print(f"INFEASIBLE→greedy_fallback: {n_inf}/{len(worki)} = {round(pct,1)}%", flush=True)
    if n_inf == 0:
        print("brak infeasible w próbce — zwiększ --scan"); return

    # ETAP B: charakterystyka + ETAP C: ladder relaksacji
    by_bag = {}
    committed_cnt = 0
    new_only_cnt = 0
    ladder = dict(L0_prodwin=dict(feas=0), L1_nowin=dict(feas=0),
                  L3_bigroute=dict(feas=0),
                  L5_clamp_sentinel=dict(feas=0),
                  L6_clamp_nowin=dict(feas=0))
    deg_bad_coords = 0      # worki z >=1 zdegenerowaną współrzędną (0,0)
    deg_sentinel = 0        # worki z >=1 sentinel cell w macierzy
    soft_stats = {c: dict(feas=0, makespans=[], commit_breach=0, r6_breach=0,
                          worse_committed=0, worse_r6=0) for c in soft_coeffs}
    rows = []
    max_open_dist = []

    for (d, now, cp) in infeasible:
        bag_objs = [M.mk(o) for o in d["bag"]]
        new_obj = M.mk(d["new_order"])
        inp = build_solver_inputs(cp, bag_objs, new_obj, now)
        cw = char_windows(inp)
        bag_n = len(d["bag"])
        by_bag[bag_n] = by_bag.get(bag_n, 0) + 1
        if cw["has_committed"]:
            committed_cnt += 1
        else:
            new_only_cnt += 1
        if cw["max_open"] is not None:
            max_open_dist.append(cw["max_open"])
        if inp.get("bad_coords"):
            deg_bad_coords += 1
        if inp.get("sentinel_cells"):
            deg_sentinel += 1

        # L0 prod windows (hard)
        s0 = _solve(inp, inp["tw"], 120.0)
        l0_feas = s0 is not None and bool(s0.sequence)
        ladder["L0_prodwin"]["feas"] += int(l0_feas)
        # L1 no windows
        s1 = _solve(inp, None, 120.0)
        l1_feas = s1 is not None and bool(s1.sequence)
        ladder["L1_nowin"]["feas"] += int(l1_feas)
        # L3 bigger route cap (no windows to isolate cap effect)
        s3 = _solve(inp, None, args.max_route_hi)
        l3_feas = s3 is not None and bool(s3.sequence)
        ladder["L3_bigroute"]["feas"] += int(l3_feas)
        # L5 sentinel→cap (test: czy 9999 vs max_route to przyczyna). Z prod oknami.
        clamped = _clamp_sentinel(inp["tm"], 30.0)
        s5 = _solve(inp, inp["tw"], 120.0, matrix=clamped)
        l5_feas = s5 is not None and bool(s5.sequence)
        ladder["L5_clamp_sentinel"]["feas"] += int(l5_feas)
        # L6 sentinel→cap + bez okien (czysty test geometrii bez sentinela)
        s6 = _solve(inp, None, 120.0, matrix=clamped)
        l6_feas = s6 is not None and bool(s6.sequence)
        ladder["L6_clamp_nowin"]["feas"] += int(l6_feas)

        # L2 soft-bound pickup windows: convert (open, open+60) hard → soft upper
        # bound at open+60 (kara overshoot). Tylko NEW pickupy (committed już loose).
        soft_pickup = []
        for (idx, ref) in inp["pickup_meta"]:
            tw = inp["tw"][idx]
            if tw is None:
                continue
            ck = getattr(ref, "czas_kuriera_warsaw", None)
            ck_present = ck is not None and str(ck).strip() not in ("", "None", "null", "NULL")
            if ck_present:
                continue  # committed już loose upper (nie hard) — pomijamy
            soft_pickup.append((idx, tw[1]))  # bound = open+60

        row = dict(order_id=d.get("order_id"), bag=bag_n,
                   has_committed=cw["has_committed"], n_pickups=cw["n_pickups"],
                   max_open_min=round(cw["max_open"], 1) if cw["max_open"] is not None else None,
                   bad_coords=inp.get("bad_coords"), sentinel_cells=inp.get("sentinel_cells"),
                   l0_prodwin=l0_feas, l1_nowin=l1_feas, l3_bigroute=l3_feas,
                   l5_clamp=l5_feas, l6_clamp_nowin=l6_feas, soft={})
        for c in soft_coeffs:
            s2 = _solve(inp, None, 120.0, soft_pickup=soft_pickup, soft_coeff=c)
            feas = s2 is not None and bool(s2.sequence)
            soft_stats[c]["feas"] += int(feas)
            entry = dict(feas=feas)
            if feas:
                soft_stats[c]["makespans"].append(s2.total_time_min)
                cb, r6 = measure_breaches(inp, s2.sequence)
                entry["makespan"] = round(s2.total_time_min, 1)
                entry["committed_breach"] = round(cb, 1)
                entry["r6_breach"] = r6
                if cb > 0.01:
                    soft_stats[c]["worse_committed"] += 1
                    soft_stats[c]["commit_breach"] += 1
                if r6 > 0:
                    soft_stats[c]["worse_r6"] += 1
                    soft_stats[c]["r6_breach"] += r6
            row["soft"][str(c)] = entry
        rows.append(row)

    # ---- RAPORT ----
    print("\n" + "#" * 100)
    print("### B) CHARAKTERYSTYKA worków infeasible")
    print("#" * 100)
    print(f"infeasible total:                 {n_inf}")
    print(f"  z committed (frozen czas_kuriera): {committed_cnt} ({round(100.0*committed_cnt/n_inf,1)}%)")
    print(f"  tylko NEW (bez committed):         {new_only_cnt} ({round(100.0*new_only_cnt/n_inf,1)}%)")
    print(f"  worki ze ZDEGENEROWANĄ współrzędną (0,0): {deg_bad_coords} "
          f"({round(100.0*deg_bad_coords/n_inf,1)}%)")
    print(f"  worki z SENTINEL cell (>=9000min) w macierzy: {deg_sentinel} "
          f"({round(100.0*deg_sentinel/n_inf,1)}%)")
    print("  rozkład bag_size:")
    for b in sorted(by_bag):
        print(f"    bag={b}: {by_bag[b]}")
    if max_open_dist:
        srt = sorted(max_open_dist)
        print(f"  max pickup window-open (min od now): "
              f"min={round(min(srt),1)} med={round(srt[len(srt)//2],1)} "
              f"max={round(max(srt),1)}")
        over120 = sum(1 for x in max_open_dist if x > 120.0)
        print(f"  worki z pickup-open > 120 min (poza max_route): {over120} "
              f"({round(100.0*over120/len(max_open_dist),1)}%)")

    print("\n" + "#" * 100)
    print("### C) LADDER relaksacji — ile z infeasible odzyskuje FEASIBLE")
    print("#" * 100)
    print(f"{'wariant':<28}{'feasible':>10}{'%':>8}")
    print(f"{'L0 prod windows (hard)':<28}{ladder['L0_prodwin']['feas']:>10}"
          f"{round(100.0*ladder['L0_prodwin']['feas']/n_inf,1):>8}")
    print(f"{'L1 BEZ time-windows':<28}{ladder['L1_nowin']['feas']:>10}"
          f"{round(100.0*ladder['L1_nowin']['feas']/n_inf,1):>8}")
    print(f"{'L3 max_route 120→' + str(int(args.max_route_hi)):<28}{ladder['L3_bigroute']['feas']:>10}"
          f"{round(100.0*ladder['L3_bigroute']['feas']/n_inf,1):>8}")
    print(f"{'L5 sentinel→30min (prod win)':<28}{ladder['L5_clamp_sentinel']['feas']:>10}"
          f"{round(100.0*ladder['L5_clamp_sentinel']['feas']/n_inf,1):>8}")
    print(f"{'L6 sentinel→30min + bez okien':<28}{ladder['L6_clamp_nowin']['feas']:>10}"
          f"{round(100.0*ladder['L6_clamp_nowin']['feas']/n_inf,1):>8}")

    print("\n  SOFT-BOUND pickup window (zamiast hard) — coeff sweep:")
    print(f"  {'coeff':>6}{'feasible':>10}{'%':>7}{'makespan med':>14}"
          f"{'worki z commit-breach':>22}{'worki z R6-breach':>18}")
    for c in soft_coeffs:
        ms = sorted(soft_stats[c]["makespans"])
        med = ms[len(ms)//2] if ms else 0.0
        print(f"  {c:>6}{soft_stats[c]['feas']:>10}"
              f"{round(100.0*soft_stats[c]['feas']/n_inf,1):>7}"
              f"{round(med,1):>14}"
              f"{soft_stats[c]['worse_committed']:>22}"
              f"{soft_stats[c]['worse_r6']:>18}")

    # ---- WERDYKT ----
    print("\n" + "=" * 100)
    print("WERDYKT (B4):")
    l1 = ladder["L1_nowin"]["feas"]
    l5 = ladder["L5_clamp_sentinel"]["feas"]
    if l1 == 0 and l5 > 0:
        print(f"  ROOT CAUSE = SENTINEL macierzy (9999min z zdegenerowanych/poza-zasięgiem")
        print(f"  współrzędnych), NIE ciasne okna. Po sklamrowaniu sentinela {l5}/{n_inf}")
        print(f"  staje się feasible nawet Z prod oknami. Soft-bound okien = NO-GO")
        print(f"  (nie dotyka przyczyny). Realny fix = sanityzacja współrzędnych /")
        print(f"  sentinel-clamp w macierzy = OSOBNY temat (dane, nie modelowanie okien).")
    elif l1 == 0:
        print(f"  Soft-bound okien NIE pomoże: {l1}/{n_inf} infeasible staje się")
        print(f"  feasible nawet po CAŁKOWITYM usunięciu time-windows. Przyczyna")
        print(f"  jest STRUKTURALNA (geometria/precedence/max_route/degenerate matrix),")
        print(f"  NIE ciasne okna pickupu. Prod i tak robi retry-bez-okien (l.1217) →")
        print(f"  greedy_fallback to już resztówka po tym retry. NO-GO dla soft-bound.")
    else:
        best_c = max(soft_coeffs, key=lambda c: soft_stats[c]["feas"])
        bs = soft_stats[best_c]
        print(f"  Soft-bound (coeff={best_c}) odzyskuje {bs['feas']}/{n_inf} workom feasible.")
        print(f"  Koszt: commit-breach na {bs['worse_committed']} workach, "
              f"R6-breach na {bs['worse_r6']} workach.")
        if bs['worse_committed'] == 0 and bs['worse_r6'] == 0:
            print(f"  GO-kandydat: zysk feasible BEZ naruszeń twardych reguł.")
        else:
            print(f"  NO-GO: soft-bound odzyskuje feasible ale ŁAMIE committed/R6 → zimna potrawa.")
    print("=" * 100)

    if args.dump:
        with open(args.dump, "w") as fh:
            json.dump(dict(
                scanned=len(worki), infeasible=n_inf, pct=round(pct, 1),
                committed=committed_cnt, new_only=new_only_cnt,
                deg_bad_coords=deg_bad_coords, deg_sentinel=deg_sentinel,
                by_bag={str(k): v for k, v in by_bag.items()},
                ladder={k: v["feas"] for k, v in ladder.items()},
                soft={str(c): dict(feas=soft_stats[c]["feas"],
                                   worse_committed=soft_stats[c]["worse_committed"],
                                   worse_r6=soft_stats[c]["worse_r6"],
                                   makespan_med=(sorted(soft_stats[c]["makespans"])[len(soft_stats[c]["makespans"])//2]
                                                 if soft_stats[c]["makespans"] else None))
                      for c in soft_coeffs},
                max_open_over120=sum(1 for x in max_open_dist if x > 120.0),
                rows=rows), fh, indent=2, ensure_ascii=False)
        print(f"\ndump -> {args.dump}")


if __name__ == "__main__":
    main()
