#!/usr/bin/env python3
"""Replay carried-first vs RELAXED carried-first over historical bags.

Read-only. Imports the REAL production sequencing functions
(courier_orders.optimize_route / _prioritize_carried_dropoffs /
_reorder_pickups_by_committed) + osrm_client. For every captured bag where
carried-first is active (>=1 carried picked_up + >=1 pickup), compute:
  seqA = current behaviour  (carried-first hard front-load + committed sort)
  seqB = relaxed behaviour  (front-load only carried whose carry-time in the
         geographic-optimal route would exceed soft_max; else trust geometry)
Then time both via OSRM (traffic-adjusted, same as plan_recheck canon) and
classify every changed case improved / neutral / harmed.

Usage: python3 carried_first_replay.py [--limit N] [--soft MIN] [--find OID]
"""
import sys, json, argparse, os
from datetime import datetime, timezone, timedelta

# NO-RETURN guard recompute (Adrian 2026-06-22): gdy =1, brute-force odrzuca permutacje
# wracające do restauracji, z której kurier już wiezie jedzenie (carried), lub rozbijające
# odbiory jednej restauracji — mirror prod _detect_departed_pickup_revisit. RELAX_NO_RETURN=0
# = stary harness (bez no-return) do porównania win-count.
_NO_RETURN = os.environ.get('RELAX_NO_RETURN', '1') == '1'

sys.path.insert(0, '/root/.openclaw/workspace/scripts')
sys.path.insert(0, '/root/.openclaw/workspace/scripts/courier_api')
import logging                          # noqa: E402
logging.getLogger('osrm_client').setLevel(logging.CRITICAL)
import courier_orders as co            # noqa: E402
from dispatch_v2 import osrm_client     # noqa: E402
logging.getLogger('osrm_client').setLevel(logging.CRITICAL)

def _coord_ok(c):
    if not c or len(c) < 2:
        return False
    try:
        a, b = float(c[0]), float(c[1])
    except (TypeError, ValueError):
        return False
    return not (abs(a) < 1e-6 and abs(b) < 1e-6)

CAPTURE = '/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl'
DWELL_P, DWELL_D = 1.0, 3.5             # canon dwell (plan_recheck _gen_one_bag_plan)
R6 = 35.0                              # hard bag-time SLA (R6)

_tbl_cache = {}
def table_min(points):
    """OSRM table -> matrix of minutes (traffic-adjusted duration_s/60). Cached."""
    key = tuple((round(p[0], 5), round(p[1], 5)) for p in points)
    if key in _tbl_cache:
        return _tbl_cache[key]
    m = osrm_client.table(list(points), list(points))
    out = None
    if m:
        out = []
        for row in m:
            r = []
            for cell in row:
                d = (cell or {}).get('duration_s')
                r.append((d / 60.0) if (d is not None and d < 9e8) else None)
            out.append(r)
    _tbl_cache[key] = out
    return out

def to_pt(c):
    return {"lat": float(c[0]), "lon": float(c[1])}

def parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
    except Exception:
        return None

def build(rec):
    cpos = rec.get('courier_pos')
    if not _coord_ok(cpos):
        return None
    bag = rec.get('bag') or []
    no = rec.get('new_order')
    allord = list(bag) + ([no] if isinstance(no, dict) else [])
    mine, resolved, seen = {}, [], set()
    for o in allord:
        oid = str(o.get('order_id'))
        if oid in seen:
            continue
        dc = o.get('delivery_coords')
        if not _coord_ok(dc):
            return None
        pc = o.get('pickup_coords')
        if o.get('status') != 'picked_up' and not _coord_ok(pc):
            return None
        seen.add(oid)
        od = {"status": o.get('status'),
              "czas_kuriera_warsaw": o.get('czas_kuriera_warsaw'),
              "picked_up_at": o.get('picked_up_at'),
              "pickup_coords": pc, "delivery_coords": dc}
        mine[oid] = od
        resolved.append((oid, od, to_pt(pc) if pc else None, to_pt(dc)))
    return (cpos, mine, resolved)

def time_seq(seq, cpos, now, mine):
    """Walk seq from cpos at `now` via OSRM + dwell + committed pickup clamp.
    Returns (drive_min, pickup_clock{oid}, deliver_clock{oid}) or None."""
    pts = [(float(cpos[0]), float(cpos[1]))]
    for st in seq:
        o = mine[st['order_id']]
        c = o['pickup_coords'] if st['kind'] == 'pickup' else o['delivery_coords']
        pts.append((float(c[0]), float(c[1])))
    M = table_min(pts)
    if not M:
        return None
    t = now
    drive = 0.0
    pick, deliv = {}, {}
    for i, st in enumerate(seq):
        leg = M[i][i + 1] if (i + 1) < len(M[i]) else None
        if leg is not None and leg > 600:   # OSRM sentinel (invalid coord) -> skip record
            return None
        leg = leg if leg is not None else 0.0
        drive += leg
        t = t + timedelta(minutes=leg)
        if st['kind'] == 'pickup':
            ck = parse(mine[st['order_id']].get('czas_kuriera_warsaw'))
            if ck is not None and ck > t:
                t = ck
            pick[st['order_id']] = t
            t = t + timedelta(minutes=DWELL_P)
        else:
            deliv[st['order_id']] = t
            t = t + timedelta(minutes=DWELL_D)
    return drive, pick, deliv

def current_seq(base, mine):
    s = co._prioritize_carried_dropoffs(base, mine)[0]
    s = co._reorder_pickups_by_committed(s, mine)[0]
    return s

import itertools

def _stops_from_mine(mine):
    stops = []
    for oid, o in mine.items():
        if o.get('status') != 'picked_up':
            stops.append({'order_id': oid, 'kind': 'pickup',
                          'coord': (float(o['pickup_coords'][0]), float(o['pickup_coords'][1]))})
        stops.append({'order_id': oid, 'kind': 'dropoff',
                      'coord': (float(o['delivery_coords'][0]), float(o['delivery_coords'][1]))})
    return stops

def _walk_perm(perm, stops, M, now, mine):
    """Timed walk of one stop permutation honouring committed pickup clamp + dwell
    (same model as time_seq). Returns dict or None."""
    t = now
    drive = 0.0
    sum_deliv = 0.0
    late_pick = 0.0
    pick, deliv = {}, {}
    prev = 0
    for si in perm:
        leg = M[prev][si + 1]
        if leg is None or leg > 600:
            return None
        drive += leg
        t = t + timedelta(minutes=leg)
        prev = si + 1
        s = stops[si]
        if s['kind'] == 'pickup':
            ck = parse(mine[s['order_id']].get('czas_kuriera_warsaw'))
            if ck is not None and ck > t:
                t = ck
            pick[s['order_id']] = t
            if ck is not None:
                late_pick += max(0.0, (t - ck).total_seconds() / 60.0)
            t = t + timedelta(minutes=DWELL_P)
        else:
            deliv[s['order_id']] = t
            sum_deliv += (t - now).total_seconds() / 60.0
            t = t + timedelta(minutes=DWELL_D)
    carry = {}
    breaches = 0
    max_carry = 0.0
    for oid, o in mine.items():
        d = deliv.get(oid)
        if d is None:
            continue
        if o.get('status') == 'picked_up':
            pa = parse(o.get('picked_up_at'))
            base_t = pa if pa else pick.get(oid)
            if pa is not None:
                carry[oid] = (d - pa).total_seconds() / 60.0
                max_carry = max(max_carry, carry[oid])
        else:
            base_t = pick.get(oid)
        if base_t is not None and (d - base_t).total_seconds() / 60.0 > R6:
            breaches += 1
    return {"sum_deliv": sum_deliv, "drive": drive, "carry": carry,
            "breaches": breaches, "max_carry": max_carry, "late_pick": late_pick,
            "deliv": deliv, "order": [(stops[i]['order_id'], stops[i]['kind']) for i in perm]}


def _epoch_min(dt):
    return None if dt is None else dt.timestamp() / 60.0

def _walk_fast(perm, n, leg, kind_pick, committed_rel, dwellp, dwelld,
               carried_idx, picked_age, dropoff_oid_idx, oid_of_stop, is_carried_stop):
    """Float timing (minutes from now). Returns (drive, sum_deliv, deliv_min[list per
    stop or None], carry_max, breaches, late_pick, carry_per_carried{idx:val}) or None."""
    pos = [0] * n
    for j, si in enumerate(perm):
        pos[si] = j
    t = 0.0
    drive = 0.0
    sum_deliv = 0.0
    late_pick = 0.0
    breaches = 0
    carry_max = 0.0
    deliv_t = [None] * n      # per stop index
    pick_t = [None] * n
    prev = 0
    for si in perm:
        lg = leg[prev][si + 1]
        if lg >= 9e8:
            return None
        drive += lg
        t += lg
        prev = si + 1
        if kind_pick[si]:
            cr = committed_rel[si]
            if cr is not None and cr > t:
                late = 0.0
                t = cr
            else:
                late = (t - cr) if cr is not None else 0.0
            if cr is not None and late > 0:
                late_pick += late
            pick_t[si] = t
            t += dwellp
        else:
            deliv_t[si] = t
            sum_deliv += t
            t += dwelld
    return (drive, sum_deliv, deliv_t, pick_t)

def analyze_record(rec, softs, delay_tol=3.0, drive_eps=0.3):
    built = build(rec)
    if built is None:
        return None
    cpos, mine, resolved = built
    carried = [oid for oid, o in mine.items() if o.get('status') == 'picked_up']
    pickups = [oid for oid, o in mine.items() if o.get('status') != 'picked_up']
    if not carried or not pickups:
        return None
    now = parse(rec.get('now'))
    if now is None:
        return None
    now_min = now.timestamp() / 60.0
    stops = _stops_from_mine(mine)
    n = len(stops)
    if n > getattr(co.config, 'OPTIMIZE_BRUTE_MAX_STOPS', 8):
        return None
    pts = [(float(cpos[0]), float(cpos[1]))] + [s['coord'] for s in stops]
    Mt = table_min(pts)
    if not Mt:
        return None
    leg = [[(c if (c is not None and c < 600) else 9e9) for c in row] for row in Mt]
    kind_pick = [s['kind'] == 'pickup' for s in stops]
    oid_of = [s['order_id'] for s in stops]
    committed_rel = []
    for s in stops:
        if s['kind'] == 'pickup':
            ck = _epoch_min(parse(mine[s['order_id']].get('czas_kuriera_warsaw')))
            committed_rel.append((ck - now_min) if ck is not None else None)
        else:
            committed_rel.append(None)
    # carried age (minutes already in bag) per carried oid, mapped to its dropoff stop idx
    carried_age = {}
    for oid in carried:
        pa = _epoch_min(parse(mine[oid].get('picked_up_at')))
        carried_age[oid] = (now_min - pa) if pa is not None else None
    dpos = {oid: i for i, s in enumerate(stops) if (s['kind'] == 'dropoff') for oid in [s['order_id']]}
    ppos = {oid: i for i, s in enumerate(stops) if (s['kind'] == 'pickup') for oid in [s['order_id']]}
    pickup_pairs = [(ppos[o], dpos[o]) for o in ppos]
    # NO-RETURN (mirror prod _detect_departed_pickup_revisit): restauracje carried =
    # odwiedzone+opuszczone przed trasą (seed -2) → każdy ich odbiór = powrót.
    def _rkey(oid):
        o = mine.get(oid) or {}
        pc = o.get('pickup_coords')
        if pc and len(pc) >= 2:
            try:
                return ('xy', round(float(pc[0]), 5), round(float(pc[1]), 5))
            except (TypeError, ValueError):
                pass
        return ('name', (o.get('restaurant_name') or o.get('restaurant') or '').strip().lower())
    _carried_rest = {_rkey(o) for o in carried}
    _carried_rest.discard(None)
    _pick_rk = [_rkey(oid_of[i]) if kind_pick[i] else None for i in range(n)]

    def _returns(perm):
        seen = {rk: -2 for rk in _carried_rest}
        for j, si in enumerate(perm):
            if not kind_pick[si]:
                continue
            rk = _pick_rk[si]
            if rk is None:
                continue
            if rk in seen and (j - seen[rk]) >= 2:
                return True
            seen.setdefault(rk, j)
        return False

    def metrics_of(walk):
        drive, sum_deliv, deliv_t, pick_t = walk
        _pt = pick_t
        carry, dmax, breaches = {}, 0.0, 0
        for i in range(n):
            if kind_pick[i]:
                continue
            oid = oid_of[i]
            dt = deliv_t[i]
            if dt is None:
                continue
            if oid in carried:
                age = carried_age.get(oid)
                if age is not None:
                    cv = age + dt
                    carry[oid] = cv
                    dmax = max(dmax, cv)
                base_pick = None
            else:
                base_pick = pick_t[ppos[oid]]
            bag = (dt - base_pick) if base_pick is not None else (carried_age.get(oid, 0.0) + dt if oid in carried else None)
            if bag is not None and bag > R6:
                breaches += 1
        return carry, dmax, breaches, deliv_t, drive, _pt

    # baseline carried-first sequence
    base = co.optimize_route(resolved, to_pt(cpos))
    seqA = current_seq(base, mine)
    idx_by, used, permA = {}, {}, []
    for i, s in enumerate(stops):
        idx_by.setdefault((s['order_id'], s['kind']), []).append(i)
    for oid, kind in [(s['order_id'], s['kind']) for s in seqA]:
        k = used.get((oid, kind), 0)
        permA.append(idx_by[(oid, kind)][k])
        used[(oid, kind)] = k + 1
    wA = _walk_fast(permA, n, leg, kind_pick, committed_rel, DWELL_P, DWELL_D, None, None, None, None, None)
    if wA is None:
        return None
    carryA, _, breachesA, delivA, driveA, pickA = metrics_of(wA)
    latepickA = _latepick(permA, n, kind_pick, committed_rel, leg)
    # enumerate valid perms once
    rows = []
    for perm in itertools.permutations(range(n)):
        pos = [0] * n
        for j, si in enumerate(perm):
            pos[si] = j
        if any(pos[p] > pos[d] for p, d in pickup_pairs):
            continue
        w = _walk_fast(perm, n, leg, kind_pick, committed_rel, DWELL_P, DWELL_D, None, None, None, None, None)
        if w is None:
            continue
        carry, dmax, breaches, deliv_t, drive, pick_t = metrics_of(w)
        rows.append((perm, carry, breaches, deliv_t, drive, pick_t))
    out = {}
    for s in softs:
        best = None
        for perm, carry, breaches, deliv_t, drive, pick_t in rows:
            if _NO_RETURN and _returns(perm):
                continue                  # powrót do restauracji carried / rozbicie odbiorów
            if any(carry.get(o, 0.0) > s for o in carried):
                continue
            if breaches > breachesA:
                continue
            bad = False
            for oid in pickups:
                a = delivA[dpos[oid]]
                b = deliv_t[dpos[oid]]
                if a is not None and b is not None and (b - a) > delay_tol:
                    bad = True
                    break
                # guard #4: no pickup later than under carried-first (food waiting at restaurant)
                pa, pb = pickA[ppos[oid]], pick_t[ppos[oid]]
                if pa is not None and pb is not None and (pb - pa) > delay_tol:
                    bad = True
                    break
            if bad:
                continue
            if best is None or drive < best[0]:
                best = (drive, perm, carry, breaches, deliv_t)
        if best is None or best[0] >= driveA - drive_eps:
            out[s] = None
        else:
            drive, perm, carry, breaches, deliv_t = best
            out[s] = {"drive": drive, "max_carry": max(carry.values()) if carry else 0.0,
                      "breaches": breaches, "late_pick": _latepick(perm, n, kind_pick, committed_rel, leg),
                      "deliv": {oid_of[i]: deliv_t[i] for i in range(n) if not kind_pick[i]},
                      "order": [(oid_of[i], 'pickup' if kind_pick[i] else 'dropoff') for i in perm]}
    mA = {"drive": driveA, "max_carry": max(carryA.values()) if carryA else 0.0,
          "breaches": breachesA, "late_pick": latepickA,
          "deliv": {oid_of[i]: delivA[i] for i in range(n) if not kind_pick[i]},
          "order": [(oid_of[i], 'pickup' if kind_pick[i] else 'dropoff') for i in permA]}
    return {"mine": mine, "now": rec.get('now'), "mA": mA, "byhsoft": out}

def _latepick(perm, n, kind_pick, committed_rel, leg):
    t = 0.0
    prev = 0
    lp = 0.0
    for si in perm:
        lg = leg[prev][si + 1]
        if lg >= 9e8:
            return 0.0
        t += lg
        prev = si + 1
        if kind_pick[si]:
            cr = committed_rel[si]
            if cr is not None and cr > t:
                t = cr
            elif cr is not None:
                lp += max(0.0, t - cr)
            t += DWELL_P
        else:
            t += DWELL_D
    return lp


def run_fast(softs, limit=0, dedup=True, dump_soft=None, dump_path=None):
    seen, n_eval = set(), 0
    agg = {s: {"changed": 0, "win_clean": 0, "win_softer": 0, "harm_breach": 0,
               "harm_drive": 0, "harm_deliv": 0, "drive_saved": 0.0, "latepick_saved": 0.0,
               "worst_carry_inc": 0.0, "worst_deliv_delay": 0.0, "harm_cases": []}
           for s in softs}
    fh = open(dump_path, 'w') if (dump_path and dump_soft) else None
    with open(CAPTURE) as f:
        for line in f:
            if limit and n_eval >= limit:
                break
            try:
                rec = json.loads(line)
            except Exception:
                continue
            pre = build(rec)
            if pre is None:
                continue
            cpos, mine, _ = pre
            if not any(o.get('status') == 'picked_up' for o in mine.values()):
                continue
            if not any(o.get('status') != 'picked_up' for o in mine.values()):
                continue
            if dedup:
                key = (tuple(sorted(mine.keys())),
                       frozenset(o for o, v in mine.items() if v.get('status') == 'picked_up'),
                       round(float(cpos[0]), 4), round(float(cpos[1]), 4))
                if key in seen:
                    continue
                seen.add(key)
            res = analyze_record(rec, softs)
            if res is None:
                continue
            n_eval += 1
            mA = res['mA']
            for s in softs:
                mB = res['byhsoft'][s]
                if mB is None or mB['order'] == mA['order']:
                    continue
                A = agg[s]
                A['changed'] += 1
                d_drive = mB['drive'] - mA['drive']
                d_breach = mB['breaches'] - mA['breaches']
                d_carry = mB['max_carry'] - mA['max_carry']
                d_latep = mB['late_pick'] - mA['late_pick']
                ddeliv = {}
                max_dd = 0.0          # ASSIGNED (other) deliveries only
                max_carried_delay = 0.0
                for oid in res['mine']:
                    a, b = mA['deliv'].get(oid), mB['deliv'].get(oid)
                    if a is not None and b is not None:
                        dd = round(b - a, 1)
                        ddeliv[oid] = dd
                        if res['mine'][oid].get('status') == 'picked_up':
                            max_carried_delay = max(max_carried_delay, dd)
                        else:
                            max_dd = max(max_dd, dd)
                A['worst_carry_inc'] = max(A['worst_carry_inc'], d_carry)
                A['worst_deliv_delay'] = max(A['worst_deliv_delay'], max_dd)
                case = {"now": res['now'], "oids": sorted(res['mine'].keys()),
                        "d_drive": round(d_drive, 1), "d_breach": d_breach,
                        "carryA": round(mA['max_carry'], 1), "carryB": round(mB['max_carry'], 1),
                        "d_latepick": round(d_latep, 1), "max_deliv_delay": round(max_dd, 1),
                        "ddeliv": ddeliv, "A": mA['order'], "B": mB['order']}
                if d_breach > 0:
                    A['harm_breach'] += 1; A['harm_cases'].append(case)
                elif d_drive > 0.5:
                    A['harm_drive'] += 1; A['harm_cases'].append(case)
                elif max_dd > 5.0:
                    A['harm_deliv'] += 1; A['harm_cases'].append(case)
                elif d_carry > 0.1:
                    A['win_softer'] += 1
                    A['drive_saved'] += max(0.0, -d_drive); A['latepick_saved'] += max(0.0, -d_latep)
                else:
                    A['win_clean'] += 1
                    A['drive_saved'] += max(0.0, -d_drive); A['latepick_saved'] += max(0.0, -d_latep)
                if fh and s == dump_soft:
                    fh.write(json.dumps(case, ensure_ascii=False) + "\n")
    if fh:
        fh.close()
    print(f"\nUnikalne sytuacje (carried-first aktywne, <=8 stopów): {n_eval}\n")
    print(f"{'soft':>5} {'zmienia':>8} {'win':>6} {'win_carry+':>10} {'HARM_breach':>11} "
          f"{'HARM_drive':>10} {'HARM_deliv>5':>12} {'jazda-':>8} {'spóźn-':>8} {'maxCarry+':>9} {'maxDeliv+':>9}")
    for s in softs:
        A = agg[s]
        win = A['win_clean'] + A['win_softer']
        print(f"{s:>5} {A['changed']:>8} {win:>6} {A['win_softer']:>10} {A['harm_breach']:>11} "
              f"{A['harm_drive']:>10} {A['harm_deliv']:>12} {A['drive_saved']:>7.0f}m {A['latepick_saved']:>7.0f}m "
              f"{A['worst_carry_inc']:>8.1f} {A['worst_deliv_delay']:>8.1f}")
    for s in softs:
        A = agg[s]
        nh = A['harm_breach'] + A['harm_drive'] + A['harm_deliv']
        if nh:
            print(f"\n=== soft={s}: {nh} HARM (breach={A['harm_breach']} drive={A['harm_drive']} deliv>5={A['harm_deliv']}); do 15 najgorszych ===")
            for h in sorted(A['harm_cases'], key=lambda x: (x['d_breach'], x['d_drive'], x['max_deliv_delay']), reverse=True)[:15]:
                print("  ", json.dumps(h, ensure_ascii=False))
    return agg

def relaxed_seq(base, mine, cpos, now, soft_max):
    """Freshness-CONSTRAINED optimum: among all precedence-valid sequences whose
    every carried (picked_up) order is delivered within soft_max of its
    picked_up_at, pick the one minimising sum-of-delivery-times (tie: drive).
    If no sequence can keep carried fresh (e.g. already old) -> fall back to the
    current carried-first behaviour (deliver carried ASAP). >8 stops -> NN fallback
    to current. This only ever relaxes carried-first when carried food stays within
    budget, so it can never push carried food later than soft_max."""
    stops = _stops_from_mine(mine)
    n = len(stops)
    if n > getattr(co.config, 'OPTIMIZE_BRUTE_MAX_STOPS', 8):
        return current_seq(base, mine)
    pts = [(float(cpos[0]), float(cpos[1]))] + [s['coord'] for s in stops]
    M = table_min(pts)
    if not M:
        return current_seq(base, mine)
    pickup_pos, dropoff_pos = {}, {}
    for i, s in enumerate(stops):
        (pickup_pos if s['kind'] == 'pickup' else dropoff_pos)[s['order_id']] = i
    carried = [oid for oid, o in mine.items() if o.get('status') == 'picked_up']
    best_feas = None       # (sum_deliv, drive, perm)
    best_any = None
    for perm in itertools.permutations(range(n)):
        if any(perm.index(pi) > perm.index(dropoff_pos[oid]) for oid, pi in pickup_pos.items()):
            continue
        w = _walk_perm(perm, stops, M, now, mine)
        if w is None:
            continue
        sum_deliv, drive, carry, breaches = w
        key = (round(sum_deliv, 3), round(drive, 3), perm)
        if best_any is None or key < best_any[0]:
            best_any = (key, perm)
        feasible = all(carry.get(oid, 0.0) <= soft_max for oid in carried)
        if feasible and (best_feas is None or key < best_feas[0]):
            best_feas = (key, perm)
    chosen = best_feas[1] if best_feas is not None else None
    if chosen is None:
        return current_seq(base, mine)     # carried can't be kept fresh -> ASAP
    seq = [{'order_id': stops[i]['order_id'], 'kind': stops[i]['kind']} for i in chosen]
    return co._reorder_pickups_by_committed(seq, mine)[0]

def metrics(seq, cpos, now, mine):
    tr = time_seq(seq, cpos, now, mine)
    if tr is None:
        return None
    drive, pick, deliv = tr
    breaches = 0
    max_carry = 0.0          # carried orders only (cold food)
    late_pick = 0.0          # sum of pickup lateness vs committed (assigned only)
    carry_by = {}
    for oid, o in mine.items():
        d = deliv.get(oid)
        if d is None:
            continue
        if o.get('status') == 'picked_up':
            pa = parse(o.get('picked_up_at'))
            base_t = pa if pa else pick.get(oid)
        else:
            base_t = pick.get(oid)
        if base_t is not None:
            bag_min = (d - base_t).total_seconds() / 60.0
            if bag_min > R6:
                breaches += 1
            if o.get('status') == 'picked_up':
                max_carry = max(max_carry, bag_min)
                carry_by[oid] = bag_min
        if o.get('status') != 'picked_up':
            ck = parse(o.get('czas_kuriera_warsaw'))
            pk = pick.get(oid)
            if ck is not None and pk is not None:
                late_pick += max(0.0, (pk - ck).total_seconds() / 60.0)
    return {"drive": drive, "breaches": breaches, "max_carry": max_carry,
            "late_pick": late_pick, "carry_by": carry_by,
            "deliv": deliv, "pick": pick,
            "order": [(s['order_id'], s['kind']) for s in seq]}


def run_detail(soft, limit=0, dedup=True, out_path=None):
    """For a single soft threshold, enumerate EVERY changed case with full
    per-order delivery deltas (B-A) so we can see exactly which deliveries move,
    carried or not. Writes JSONL + prints a categorized summary."""
    seen_keys = set()
    n_eval = n_changed = 0
    cat = {"win_clean": 0, "win_carry_softer": 0, "harm_breach": 0,
           "harm_drive": 0, "harm_late_deliv": 0}
    worst_carry_increase = 0.0
    worst_deliv_delay = 0.0
    recs = []
    fh = open(out_path, 'w') if out_path else None
    with open(CAPTURE) as f:
        for line in f:
            if limit and n_eval >= limit:
                break
            try:
                rec = json.loads(line)
            except Exception:
                continue
            built = build(rec)
            if built is None:
                continue
            cpos, mine, resolved = built
            carried = [o for o in mine.values() if o.get('status') == 'picked_up']
            pickups = [o for o in mine.values() if o.get('status') != 'picked_up']
            if not carried or not pickups:
                continue
            if dedup:
                key = (tuple(sorted(mine.keys())),
                       frozenset(oid for oid, o in mine.items() if o.get('status') == 'picked_up'),
                       round(float(cpos[0]), 4), round(float(cpos[1]), 4))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
            now = parse(rec.get('now'))
            if now is None:
                continue
            try:
                base = co.optimize_route(resolved, to_pt(cpos))
            except Exception:
                continue
            if not base:
                continue
            seqA = current_seq(base, mine)
            seqB = relaxed_seq(base, mine, cpos, now, soft)
            mA = metrics(seqA, cpos, now, mine)
            mB = metrics(seqB, cpos, now, mine)
            if mA is None or mB is None:
                continue
            n_eval += 1
            if mA['order'] == mB['order']:
                continue
            n_changed += 1
            # per-order delivery delta (minutes, +=later under relaxed)
            ddeliv = {}
            for oid in mine:
                a = mA['deliv'].get(oid)
                b = mB['deliv'].get(oid)
                if a and b:
                    ddeliv[oid] = round((b - a).total_seconds() / 60.0, 1)
            # per carried order carry delta
            dcarry = {}
            for oid in mine:
                if mine[oid].get('status') == 'picked_up':
                    a = mA['carry_by'].get(oid)
                    b = mB['carry_by'].get(oid)
                    if a is not None and b is not None:
                        dcarry[oid] = round(b - a, 1)
            d_drive = round(mB['drive'] - mA['drive'], 1)
            d_breach = mB['breaches'] - mA['breaches']
            max_dc = max(dcarry.values()) if dcarry else 0.0
            max_dd = max(ddeliv.values()) if ddeliv else 0.0
            worst_carry_increase = max(worst_carry_increase, max_dc)
            worst_deliv_delay = max(worst_deliv_delay, max_dd)
            if d_breach > 0:
                c = "harm_breach"
            elif d_drive > 0.5:
                c = "harm_drive"
            elif max_dd > 5.0:           # some delivery >5 min later
                c = "harm_late_deliv"
            elif max_dc > 0.1:
                c = "win_carry_softer"
            else:
                c = "win_clean"
            cat[c] += 1
            row = {"cat": c, "now": rec.get('now'), "oids": sorted(mine.keys()),
                   "d_drive": d_drive, "d_breach": d_breach,
                   "d_latepick": round(mB['late_pick'] - mA['late_pick'], 1),
                   "ddeliv": ddeliv, "dcarry": dcarry,
                   "carriedB_max": round(mB['max_carry'], 1),
                   "A": mA['order'], "B": mB['order']}
            recs.append(row)
            if fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    if fh:
        fh.close()
    print(f"soft={soft}  unique_eval={n_eval}  changed={n_changed}")
    print(f"  categories: {json.dumps(cat)}")
    print(f"  worst single carried carry-time INCREASE = {worst_carry_increase:.1f} min")
    print(f"  worst single delivery DELAY              = {worst_deliv_delay:.1f} min")
    for harmcat in ("harm_breach", "harm_drive", "harm_late_deliv"):
        hs = [r for r in recs if r['cat'] == harmcat]
        if hs:
            print(f"\n  --- {harmcat}: {len(hs)} (showing up to 15, worst first) ---")
            for r in sorted(hs, key=lambda x: (x['d_breach'], x['d_drive'], max(x['ddeliv'].values() or [0])), reverse=True)[:15]:
                print("   ", json.dumps(r, ensure_ascii=False))
    return cat, recs

def run(limit, soft, find_oid, dedup=True):
    seen_keys = set()
    n_active = n_eval = n_changed = 0
    improved = neutral = harmed = 0
    harmed_cases = []
    soft_max = soft
    drive_saved_total = 0.0
    latepick_saved_total = 0.0
    with open(CAPTURE) as f:
        for line in f:
            if limit and n_eval >= limit:
                break
            try:
                rec = json.loads(line)
            except Exception:
                continue
            built = build(rec)
            if built is None:
                continue
            cpos, mine, resolved = built
            carried = [o for o in mine.values() if o.get('status') == 'picked_up']
            pickups = [o for o in mine.values() if o.get('status') != 'picked_up']
            if not carried or not pickups:
                continue
            n_active += 1
            if find_oid and find_oid not in mine:
                continue
            if dedup:
                key = (tuple(sorted(mine.keys())),
                       frozenset(oid for oid, o in mine.items() if o.get('status') == 'picked_up'),
                       round(float(cpos[0]), 4), round(float(cpos[1]), 4))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
            now = parse(rec.get('now'))
            if now is None:
                continue
            try:
                base = co.optimize_route(resolved, to_pt(cpos))
            except Exception:
                continue
            if not base:
                continue
            seqA = current_seq(base, mine)
            seqB = relaxed_seq(base, mine, cpos, now, soft_max)
            mA = metrics(seqA, cpos, now, mine)
            mB = metrics(seqB, cpos, now, mine)
            if mA is None or mB is None:
                continue
            n_eval += 1
            changed = mA['order'] != mB['order']
            if find_oid:
                print(f"\n=== {find_oid} @ {rec.get('now')} cid_pos={cpos} ===")
                print(" bag:", {oid: (o['status'], o.get('czas_kuriera_warsaw')) for oid, o in mine.items()})
                print(" base :", [(s['order_id'], s['kind']) for s in base])
                print(" seqA :", mA['order'], f"drive={mA['drive']:.1f} carry={mA['max_carry']:.1f} latePick={mA['late_pick']:.1f} breach={mA['breaches']}")
                print(" seqB :", mB['order'], f"drive={mB['drive']:.1f} carry={mB['max_carry']:.1f} latePick={mB['late_pick']:.1f} breach={mB['breaches']}")
                if n_eval >= 3:
                    break
                continue
            if not changed:
                neutral += 1
                continue
            n_changed += 1
            d_drive = mB['drive'] - mA['drive']
            d_breach = mB['breaches'] - mA['breaches']
            d_carry = mB['max_carry'] - mA['max_carry']
            d_latep = mB['late_pick'] - mA['late_pick']
            # HARM = new R6 breach, or drive worse, or carried food pushed past R6
            is_harm = (d_breach > 0) or (d_drive > 0.5) or (mB['max_carry'] > R6 and d_carry > 0.1)
            if is_harm:
                harmed += 1
                harmed_cases.append({
                    "now": rec.get('now'), "oids": sorted(mine.keys()),
                    "d_drive": round(d_drive, 1), "d_breach": d_breach,
                    "d_carry": round(d_carry, 1), "carryB": round(mB['max_carry'], 1),
                    "carryA": round(mA['max_carry'], 1), "d_latepick": round(d_latep, 1),
                    "A": mA['order'], "B": mB['order'],
                })
            else:
                improved += 1
                drive_saved_total += max(0.0, -d_drive)
                latepick_saved_total += max(0.0, -d_latep)
    if find_oid:
        return
    print(f"soft_max={soft_max:>4}  unique_situations_eval={n_eval}  changed_by_relax={n_changed}")
    print(f"   IMPROVED={improved}  NEUTRAL(no change)={neutral}  HARMED={harmed}")
    print(f"   drive saved (sum, improved) = {drive_saved_total:.0f} min   pickup-lateness saved = {latepick_saved_total:.0f} min")
    if harmed_cases:
        print(f"   --- {len(harmed_cases)} HARMED cases (showing up to 25) ---")
        for h in sorted(harmed_cases, key=lambda x: (x['d_breach'], x['d_drive']), reverse=True)[:25]:
            print("   ", json.dumps(h, ensure_ascii=False))
    return {"soft": soft_max, "eval": n_eval, "changed": n_changed,
            "improved": improved, "harmed": harmed, "harmed_cases": harmed_cases,
            "drive_saved": drive_saved_total, "latepick_saved": latepick_saved_total}

def run_sweep(softs, limit=0, dedup=True, dump=None):
    """Evaluate every unique situation ONCE; score against each soft threshold
    (reuses optimize_route + OSRM cache). Prints a trade table + harm lists."""
    seen_keys = set()
    n_eval = 0
    agg = {s: {"changed": 0, "improved": 0, "harmed": 0, "softer_carry": 0,
               "drive_saved": 0.0, "latepick_saved": 0.0, "harmed_cases": []}
           for s in softs}
    fh = open(dump, 'w') if dump else None
    with open(CAPTURE) as f:
        for line in f:
            if limit and n_eval >= limit:
                break
            try:
                rec = json.loads(line)
            except Exception:
                continue
            built = build(rec)
            if built is None:
                continue
            cpos, mine, resolved = built
            carried = [o for o in mine.values() if o.get('status') == 'picked_up']
            pickups = [o for o in mine.values() if o.get('status') != 'picked_up']
            if not carried or not pickups:
                continue
            if dedup:
                key = (tuple(sorted(mine.keys())),
                       frozenset(oid for oid, o in mine.items() if o.get('status') == 'picked_up'),
                       round(float(cpos[0]), 4), round(float(cpos[1]), 4))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
            now = parse(rec.get('now'))
            if now is None:
                continue
            try:
                base = co.optimize_route(resolved, to_pt(cpos))
            except Exception:
                continue
            if not base:
                continue
            seqA = current_seq(base, mine)
            mA = metrics(seqA, cpos, now, mine)
            if mA is None:
                continue
            n_eval += 1
            for s in softs:
                seqB = relaxed_seq(base, mine, cpos, now, s)
                mB = metrics(seqB, cpos, now, mine)
                if mB is None:
                    continue
                if mA['order'] == mB['order']:
                    continue
                A = agg[s]
                A["changed"] += 1
                d_drive = mB['drive'] - mA['drive']
                d_breach = mB['breaches'] - mA['breaches']
                d_carry = mB['max_carry'] - mA['max_carry']
                d_latep = mB['late_pick'] - mA['late_pick']
                is_harm = (d_breach > 0) or (d_drive > 0.5) or (mB['max_carry'] > R6 and d_carry > 0.1)
                case = {"now": rec.get('now'), "oids": sorted(mine.keys()),
                        "d_drive": round(d_drive, 1), "d_breach": d_breach,
                        "carryA": round(mA['max_carry'], 1), "carryB": round(mB['max_carry'], 1),
                        "d_latepick": round(d_latep, 1), "A": mA['order'], "B": mB['order']}
                if is_harm:
                    A["harmed"] += 1
                    A["harmed_cases"].append(case)
                else:
                    A["improved"] += 1
                    A["drive_saved"] += max(0.0, -d_drive)
                    A["latepick_saved"] += max(0.0, -d_latep)
                    if d_carry > 0.1:
                        A["softer_carry"] += 1
                if fh and s == softs[-1]:
                    fh.write(json.dumps(case, ensure_ascii=False) + "\n")
    if fh:
        fh.close()
    print(f"\nUnique situations evaluated (carried-first active): {n_eval}\n")
    print(f"{'soft':>5} {'changed':>8} {'improved':>9} {'harmed':>7} {'carry+(safe)':>13} "
          f"{'drive_saved':>11} {'latepick_saved':>14}")
    for s in softs:
        A = agg[s]
        print(f"{s:>5} {A['changed']:>8} {A['improved']:>9} {A['harmed']:>7} {A['softer_carry']:>13} "
              f"{A['drive_saved']:>10.0f}m {A['latepick_saved']:>13.0f}m")
    for s in softs:
        A = agg[s]
        if A['harmed_cases']:
            print(f"\n=== soft={s}: {len(A['harmed_cases'])} HARMED (showing up to 20, worst first) ===")
            for h in sorted(A['harmed_cases'], key=lambda x: (x['d_breach'], x['d_drive']), reverse=True)[:20]:
                print("  ", json.dumps(h, ensure_ascii=False))
    return agg


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--soft', type=float, default=25.0)
    ap.add_argument('--sweep', type=str, default=None, help='comma list e.g. 18,20,22,25,28,30')
    ap.add_argument('--find', type=str, default=None)
    ap.add_argument('--no-dedup', action='store_true')
    ap.add_argument('--dump', type=str, default=None)
    ap.add_argument('--detail', type=float, default=None, help='single soft, full per-order enumeration')
    ap.add_argument('--fast', type=str, default=None, help='efficient constrained sweep: comma softs')
    ap.add_argument('--dumpsoft', type=float, default=None)
    a = ap.parse_args()
    if a.fast:
        run_fast([float(x) for x in a.fast.split(',')], a.limit, dedup=not a.no_dedup,
                 dump_soft=a.dumpsoft, dump_path=a.dump)
    elif a.detail is not None:
        run_detail(a.detail, a.limit, dedup=not a.no_dedup, out_path=a.dump)
    elif a.sweep:
        run_sweep([float(x) for x in a.sweep.split(',')], a.limit, dedup=not a.no_dedup, dump=a.dump)
    else:
        run(a.limit, a.soft, a.find, dedup=not a.no_dedup)
