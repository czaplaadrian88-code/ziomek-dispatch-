"""REFERENCE implementation of the guarded carried-first relaxation.

To be ported (mirrored) into:
  - dispatch_v2/plan_recheck.py  (canon; stops use key 'type', coords dict lat/lng)
  - courier_api/courier_orders.py (build_view; steps use key 'kind', coord tuple)

Design (validated by replay over historical bags, 2026-06-22):
  carried-first (deliver picked_up food before any new pickup) is correct in the
  large majority of cases — the geographic optimum often delivers carried food
  late. So we do NOT trust geometry. Instead we keep carried-first as the baseline
  and accept a shorter alternative ONLY when it is a strict, safe improvement:

    Among precedence-valid sequences, find min-drive subject to
      (1) every carried order delivered within SOFT_MAX of its picked_up_at,
      (2) NO assigned (non-carried) delivery later than in the carried-first
          baseline by more than DELAY_TOL,
      (3) NO more R6 (>35 min bag-time) breaches than the baseline,
    and adopt it only if it saves > DRIVE_EPS minutes of driving.
    Otherwise keep the carried-first baseline unchanged.

  => Worst case == current behaviour (the function can only no-op or improve).
  => Deterministic; carried food is hard-bounded by SOFT_MAX, so it can never be
     pushed past its freshness budget (the original 06-01 intent is preserved).
"""
from datetime import timedelta
import itertools

R6_MAX_MIN = 35.0


def relax_carried_first(seq_baseline, order_by_id, start_latlng, now,
                        leg_min, *, soft_max_min, delay_tol_min=3.0,
                        drive_eps_min=0.3, dwell_pick=1.0, dwell_drop=3.5,
                        max_stops=8, parse_dt, get_status, get_picked_up_at,
                        get_committed):
    """seq_baseline: list of (order_id, kind) in carried-first order ('pickup'|'dropoff').
    order_by_id: oid -> order obj. start_latlng: (lat,lng). leg_min(a,b)->minutes.
    Returns a NEW list of (order_id, kind) (improved) or seq_baseline unchanged.
    All clock math mirrors the surface's own timing (committed pickup clamp+dwell)."""
    if not seq_baseline or len(seq_baseline) > max_stops:
        return seq_baseline
    carried = {oid for oid in order_by_id if get_status(order_by_id[oid]) == 'picked_up'}
    if not carried:
        return seq_baseline
    assigned = [oid for oid in order_by_id if oid not in carried]

    # coords per stop key
    def coord(oid, kind):
        o = order_by_id[oid]
        c = o.get('pickup_coords') if kind == 'pickup' else o.get('delivery_coords')
        return (float(c[0]), float(c[1]))

    stops = list(seq_baseline)  # the universe of stops (same membership, any order)

    def walk(order):
        t = now
        drive = 0.0
        prev = start_latlng
        deliv, pick = {}, {}
        for oid, kind in order:
            c = coord(oid, kind)
            leg = leg_min(prev, c)
            if leg is None:
                return None
            drive += leg
            t = t + timedelta(minutes=leg)
            prev = c
            if kind == 'pickup':
                ck = parse_dt(get_committed(order_by_id[oid]))
                if ck is not None and ck > t:
                    t = ck
                pick[oid] = t
                t = t + timedelta(minutes=dwell_pick)
            else:
                deliv[oid] = t
                t = t + timedelta(minutes=dwell_drop)
        # carry (carried) + breaches (all)
        carry, breaches = {}, 0
        for oid in order_by_id:
            d = deliv.get(oid)
            if d is None:
                continue
            if oid in carried:
                pa = parse_dt(get_picked_up_at(order_by_id[oid]))
                base_t = pa if pa else pick.get(oid)
                if pa is not None:
                    carry[oid] = (d - pa).total_seconds() / 60.0
            else:
                base_t = pick.get(oid)
            if base_t is not None and (d - base_t).total_seconds() / 60.0 > R6_MAX_MIN:
                breaches += 1
        return {'drive': drive, 'deliv': deliv, 'carry': carry, 'breaches': breaches}

    mA = walk(seq_baseline)
    if mA is None:
        return seq_baseline

    # precedence map on the stop universe
    pickups = [s for s in stops if s[1] == 'pickup']
    dpos = {oid: i for i, (oid, k) in enumerate(stops) if k == 'dropoff'}
    ppos = {oid: i for i, (oid, k) in enumerate(stops) if k == 'pickup'}

    best = None
    for perm in itertools.permutations(range(len(stops))):
        order = [stops[i] for i in perm]
        place = {(oid, k): j for j, (oid, k) in enumerate(order)}
        if any(place[(oid, 'pickup')] > place[(oid, 'dropoff')] for oid in ppos):
            continue
        m = walk(order)
        if m is None:
            continue
        if any(m['carry'].get(o, 0.0) > soft_max_min for o in carried):
            continue
        if m['breaches'] > mA['breaches']:
            continue
        ok = True
        for oid in assigned:
            a, b = mA['deliv'].get(oid), m['deliv'].get(oid)
            if a and b and (b - a).total_seconds() / 60.0 > delay_tol_min:
                ok = False
                break
        if not ok:
            continue
        if best is None or m['drive'] < best[0]:
            best = (m['drive'], order)
    if best is not None and best[0] < mA['drive'] - drive_eps_min:
        return best[1]
    return seq_baseline
