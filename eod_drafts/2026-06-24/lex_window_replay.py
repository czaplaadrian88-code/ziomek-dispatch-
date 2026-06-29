#!/usr/bin/env python3
"""OFFLINE replay: production carried-first(+relax) vs LEX-committed-window ordering.

Read-only. Reuses the validated carried_first_replay.py primitives (build / OSRM
table_min / _walk_fast / _stops_from_mine / current_seq) so timing is identical to
production plan_recheck canon. For every captured carried-active bag we compute THREE
sequences and time them via OSRM (committed-pickup floor + dwell), then compare:

  liveA = pure carried-first         (canon: carried dropoffs front + pickups by committed)
  liveB = carried-first + RELAX@20   (= live behaviour today: ENABLE_CARRIED_FIRST_RELAX)
  lexC  = LEX-committed-window       (proposed coherent rule)

LEX key (minimise, lexicographic) = (
    n_pickup_window_violations,   # HARD  R-DECLARED-TIME: pickup later than czas_kuriera + 5
    r6_breaches,                  # HARD  R6: bag-time > 35 min (pickup->delivery, carried age incl)
    round(drive, 1),              # then shortest drive
    round(max_carry, 1),          # then freshest carried food (soft tie-break)
)
NO-RETURN (Z-RULE) kept as a hard reject (mirror prod). Carried-first is NOT enforced:
it emerges only if it is lex-optimal. This tests whether subordinating carried-first to
the hard pickup-window rule is zero-harm on deliveries/R6 while fixing odbiór punctuality.

Usage: python3 lex_window_replay.py [--limit N] [--win 5] [--dump path] [--find OID]
"""
import sys, os, json, argparse, itertools
from datetime import timedelta

sys.path.insert(0, '/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-22')
import logging
logging.getLogger('osrm_client').setLevel(logging.CRITICAL)
import carried_first_replay as H            # noqa: E402  (validated harness primitives)

R6 = H.R6
DWELL_P, DWELL_D = H.DWELL_P, H.DWELL_D
# R-DECLARED-TIME tolerance is LOAD-AWARE in prod: strict 5 / loose 10 at loadgov_ewma>=4.5
# (OBJ_COMMITTED_PICKUP_TOL_STRICT/LOOSE_MIN). Corpus has no per-bag loadgov; run both
# bounds: PICKUP_WINDOW_MIN=5 (normal-load ceiling) and =10 (shortage floor).
PICKUP_WINDOW_MIN = float(os.environ.get('PICKUP_WINDOW_MIN', '5'))
RELAX_SOFT = 20.0                            # live ENABLE_CARRIED_FIRST_RELAX soft cap
RELAX_TOL = 3.0                             # live CARRIED_FIRST_RELAX_DELAY_TOL_MIN
RELAX_EPS = 0.3                            # live CARRIED_FIRST_RELAX_DRIVE_EPS_MIN
_NO_RETURN = os.environ.get('RELAX_NO_RETURN', '1') == '1'


def setup(rec):
    """Mirror analyze_record setup; return per-bag immutables or None."""
    built = H.build(rec)
    if built is None:
        return None
    cpos, mine, resolved = built
    carried = [o for o, v in mine.items() if v.get('status') == 'picked_up']
    pickups = [o for o, v in mine.items() if v.get('status') != 'picked_up']
    if not carried or not pickups:
        return None
    now = H.parse(rec.get('now'))
    if now is None:
        return None
    now_min = now.timestamp() / 60.0
    stops = H._stops_from_mine(mine)
    n = len(stops)
    if n > getattr(H.co.config, 'OPTIMIZE_BRUTE_MAX_STOPS', 8):
        return None
    pts = [(float(cpos[0]), float(cpos[1]))] + [s['coord'] for s in stops]
    Mt = H.table_min(pts)
    if not Mt:
        return None
    leg = [[(c if (c is not None and c < 600) else 9e9) for c in row] for row in Mt]
    kind_pick = [s['kind'] == 'pickup' for s in stops]
    oid_of = [s['order_id'] for s in stops]
    committed_rel = []
    for s in stops:
        if s['kind'] == 'pickup':
            ck = H._epoch_min(H.parse(mine[s['order_id']].get('czas_kuriera_warsaw')))
            committed_rel.append((ck - now_min) if ck is not None else None)
        else:
            committed_rel.append(None)
    carried_age = {}
    for oid in carried:
        pa = H._epoch_min(H.parse(mine[oid].get('picked_up_at')))
        carried_age[oid] = (now_min - pa) if pa is not None else None
    dpos = {s['order_id']: i for i, s in enumerate(stops) if s['kind'] == 'dropoff'}
    ppos = {s['order_id']: i for i, s in enumerate(stops) if s['kind'] == 'pickup'}
    pickup_pairs = [(ppos[o], dpos[o]) for o in ppos]

    def _rkey(oid):
        o = mine.get(oid) or {}
        pc = o.get('pickup_coords')
        if pc and len(pc) >= 2:
            try:
                return ('xy', round(float(pc[0]), 5), round(float(pc[1]), 5))
            except (TypeError, ValueError):
                pass
        return ('name', (o.get('restaurant_name') or o.get('restaurant') or '').strip().lower())
    carried_rest = {_rkey(o) for o in carried}
    carried_rest.discard(None)
    pick_rk = [_rkey(oid_of[i]) if kind_pick[i] else None for i in range(n)]

    def returns(perm):
        seen = {rk: -2 for rk in carried_rest}
        for j, si in enumerate(perm):
            if not kind_pick[si]:
                continue
            rk = pick_rk[si]
            if rk is None:
                continue
            if rk in seen and (j - seen[rk]) >= 2:
                return True
            seen.setdefault(rk, j)
        return False

    return dict(cpos=cpos, mine=mine, resolved=resolved, carried=carried, pickups=pickups,
                now=now, stops=stops, n=n, leg=leg, kind_pick=kind_pick, oid_of=oid_of,
                committed_rel=committed_rel, carried_age=carried_age, dpos=dpos, ppos=ppos,
                pickup_pairs=pickup_pairs, returns=returns)


def metrics(perm, S):
    """Time a perm and return full metrics dict (or None)."""
    w = H._walk_fast(perm, S['n'], S['leg'], S['kind_pick'], S['committed_rel'],
                     DWELL_P, DWELL_D, None, None, None, None, None)
    if w is None:
        return None
    drive, sum_deliv, deliv_t, pick_t = w
    n, kind_pick, oid_of = S['n'], S['kind_pick'], S['oid_of']
    carried, carried_age, ppos = S['carried'], S['carried_age'], S['ppos']
    committed_rel = S['committed_rel']
    # pickup-window violations
    n_viol, min_beyond = 0, 0.0
    for i in range(n):
        if not kind_pick[i]:
            continue
        cr = committed_rel[i]
        if cr is None or pick_t[i] is None:
            continue
        late = pick_t[i] - cr
        if late > PICKUP_WINDOW_MIN:
            n_viol += 1
            min_beyond += (late - PICKUP_WINDOW_MIN)
    # R6 + carry
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
            bag = (age + dt) if age is not None else None
        else:
            bp = pick_t[ppos[oid]]
            bag = (dt - bp) if bp is not None else None
        if bag is not None and bag > R6:
            breaches += 1
    return dict(perm=perm, drive=drive, deliv_t=deliv_t, pick_t=pick_t,
                n_viol=n_viol, min_beyond=min_beyond, breaches=breaches,
                max_carry=dmax, carry=carry)


def analyze(rec):
    S = setup(rec)
    if S is None:
        return None
    n = S['n']
    # all precedence-valid perms (+ NO-RETURN), timed once
    rows = []
    for perm in itertools.permutations(range(n)):
        pos = [0] * n
        for j, si in enumerate(perm):
            pos[si] = j
        if any(pos[p] > pos[d] for p, d in S['pickup_pairs']):
            continue
        if _NO_RETURN and S['returns'](perm):
            continue
        m = metrics(perm, S)
        if m is not None:
            rows.append(m)
    if not rows:
        return None

    # liveA = pure carried-first (canon)
    base = H.co.optimize_route(S['resolved'], H.to_pt(S['cpos']))
    seqA = H.current_seq(base, S['mine'])
    idx_by, used, permA = {}, {}, []
    for i, s in enumerate(S['stops']):
        idx_by.setdefault((s['order_id'], s['kind']), []).append(i)
    for oid, kind in [(s['order_id'], s['kind']) for s in seqA]:
        k = used.get((oid, kind), 0)
        seq_kind = 'pickup' if kind == 'pickup' else 'dropoff'
        permA.append(idx_by[(oid, seq_kind)][k])
        used[(oid, kind)] = k + 1
    mA = metrics(tuple(permA), S)
    if mA is None:
        return None

    # liveB = carried-first + relax@20 (faithful to live)
    bestB = None
    for m in rows:
        if any(m['carry'].get(o, 0.0) > RELAX_SOFT for o in S['carried']):
            continue
        if m['breaches'] > mA['breaches']:
            continue
        bad = False
        for oid in S['pickups']:
            a, b = mA['deliv_t'][S['dpos'][oid]], m['deliv_t'][S['dpos'][oid]]
            if a is not None and b is not None and (b - a) > RELAX_TOL:
                bad = True
                break
            pa, pb = mA['pick_t'][S['ppos'][oid]], m['pick_t'][S['ppos'][oid]]
            if pa is not None and pb is not None and (pb - pa) > RELAX_TOL:
                bad = True
                break
        if bad:
            continue
        if bestB is None or m['drive'] < bestB['drive']:
            bestB = m
    mB = bestB if (bestB is not None and bestB['drive'] < mA['drive'] - RELAX_EPS) else mA

    # lexC = NAIVE lex (pickup-window first, unconstrained) — reference for harm
    def lexkey(m):
        return (m['n_viol'], m['breaches'], round(m['drive'], 1), round(m['max_carry'], 1))
    mC = min(rows, key=lexkey)

    # lexD = CONSTRAINED coherent rule: fix pickup-window ONLY among options that respect
    # ALL hard rules — no new R6 breach, carried food stays <=R6(35), and no OTHER (assigned)
    # delivery delayed > TOL vs carried-first. Among feasible, minimise (pickup-window
    # violations, drive, carried-age). Empty feasible -> stay with live relax (safe).
    # Feasibility anchored to LIVE-SHIPPED plan (mB) so D can never regress vs production.
    carry_cap = max(R6, mB['max_carry'])
    def feasibleD(m):
        if m['max_carry'] > carry_cap:
            return False
        if m['breaches'] > mB['breaches']:
            return False
        for oid in S['pickups']:
            a, b = mB['deliv_t'][S['dpos'][oid]], m['deliv_t'][S['dpos'][oid]]
            if a is not None and b is not None and (b - a) > RELAX_TOL:
                return False
        # carried food protected only by R6 cap (carry_cap) — it is EXPECTED to be
        # delivered later when we honour the next pickup window; that is legal <=35.
        return True
    candD = [m for m in rows if feasibleD(m)]
    mD = min(candD, key=lambda m: (m['n_viol'], round(m['drive'], 1), round(m['max_carry'], 1))) \
        if candD else mB

    return dict(mine=S['mine'], now=rec.get('now'), mA=mA, mB=mB, mC=mC, mD=mD, carried=S['carried'],
                pickups=S['pickups'], dpos=S['dpos'], oid_of=S['oid_of'], kind_pick=S['kind_pick'])


def order_str(m, oid_of, kind_pick):
    return " -> ".join(f"{oid_of[i]}:{'P' if kind_pick[i] else 'D'}" for i in m['perm'])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=5000, help='max qualifying deduped bags')
    ap.add_argument('--dump', default=None, help='jsonl of lex-vs-live changed cases')
    ap.add_argument('--find', default=None, help='only show bags containing this oid')
    args = ap.parse_args()

    seen = set()
    n = 0

    def fresh_agg():
        return {'changed': 0, 'pickfix_bags': 0, 'pickfix_viol': 0, 'pickfix_min': 0.0,
                'r6_harm': 0, 'deliv_harm': 0, 'carry_over_r6': 0,
                'drive_saved': 0.0, 'drive_cost': 0.0, 'fresh_worse': 0.0,
                'tot_viol': 0, 'tot_late_min': 0.0}
    A = {'C': fresh_agg(), 'D': fresh_agg()}          # C=naive, D=constrained-coherent
    live_total_viol = 0
    live_total_late = 0.0
    examples = []
    fh = open(args.dump, 'w') if args.dump else None

    def account(tag, mB, mX, pickups, dpos):
        a = A[tag]
        a['tot_viol'] += mX['n_viol']
        a['tot_late_min'] += mX['min_beyond']
        if mX['perm'] != mB['perm']:
            a['changed'] += 1
        if mX['n_viol'] < mB['n_viol']:
            a['pickfix_bags'] += 1
            a['pickfix_viol'] += (mB['n_viol'] - mX['n_viol'])
            a['pickfix_min'] += (mB['min_beyond'] - mX['min_beyond'])
        if mX['breaches'] > mB['breaches']:
            a['r6_harm'] += 1
        if mX['max_carry'] > R6 and mB['max_carry'] <= R6:
            a['carry_over_r6'] += 1
        max_dd = 0.0
        for oid in pickups:
            x, y = mB['deliv_t'][dpos[oid]], mX['deliv_t'][dpos[oid]]
            if x is not None and y is not None:
                max_dd = max(max_dd, y - x)
        if max_dd > 5.0:
            a['deliv_harm'] += 1
        d_drive = mX['drive'] - mB['drive']
        if d_drive < 0:
            a['drive_saved'] += -d_drive
        else:
            a['drive_cost'] += d_drive
        if mX['max_carry'] > mB['max_carry']:
            a['fresh_worse'] = max(a['fresh_worse'], mX['max_carry'] - mB['max_carry'])
        return max_dd

    with open(H.CAPTURE) as f:
        for line in f:
            if n >= args.limit:
                break
            try:
                rec = json.loads(line)
            except Exception:
                continue
            pre = H.build(rec)
            if pre is None:
                continue
            cpos, mine, _ = pre
            if not any(v.get('status') == 'picked_up' for v in mine.values()):
                continue
            if not any(v.get('status') != 'picked_up' for v in mine.values()):
                continue
            if args.find and args.find not in mine:
                continue
            key = (tuple(sorted(mine.keys())),
                   frozenset(o for o, v in mine.items() if v.get('status') == 'picked_up'),
                   round(float(cpos[0]), 4), round(float(cpos[1]), 4))
            if key in seen:
                continue
            seen.add(key)
            res = analyze(rec)
            if res is None:
                continue
            n += 1
            mA, mB, mC, mD = res['mA'], res['mB'], res['mC'], res['mD']
            oid_of, kind_pick, dpos = res['oid_of'], res['kind_pick'], res['dpos']
            live_total_viol += mB['n_viol']
            live_total_late += mB['min_beyond']
            account('C', mB, mC, res['pickups'], dpos)
            max_dd_D = account('D', mB, mD, res['pickups'], dpos)

            if mD['perm'] != mB['perm'] and mD['n_viol'] < mB['n_viol']:
                case = {"now": res['now'], "oids": sorted(res['mine'].keys()),
                        "live_viol": mB['n_viol'], "lex_viol": mD['n_viol'],
                        "live_beyond5_min": round(mB['min_beyond'], 1), "lex_beyond5_min": round(mD['min_beyond'], 1),
                        "d_drive": round(mD['drive'] - mB['drive'], 1),
                        "carryB": round(mB['max_carry'], 1), "carryD": round(mD['max_carry'], 1),
                        "live": order_str(mB, oid_of, kind_pick), "lex": order_str(mD, oid_of, kind_pick)}
                if fh:
                    fh.write(json.dumps(case, ensure_ascii=False) + "\n")
                if len(examples) < 12 and mD['breaches'] <= mB['breaches'] and max_dd_D <= 5.0:
                    examples.append(case)
    if fh:
        fh.close()

    print(f"\n=== vs LIVE (carried-first + relax@20) — {n} unikalnych carried-active worków ===")
    print(f"\nNaruszenia okna odbioru w LIVE: {live_total_viol} odbiorów / {live_total_late:.0f} min ponad ±{PICKUP_WINDOW_MIN:.0f}\n")
    hdr = f"{'wariant':>26} {'zmienia':>8} {'pickfix worki':>13} {'-min spóźn':>11} {'R6 harm':>8} {'carry>35':>9} {'deliv>5 harm':>12} {'jazda netto':>12} {'fresh+max':>10}"
    print(hdr)
    for tag, name in [('C', 'C NAIWNY (okno-first)'), ('D', 'D SPÓJNY (constrained)')]:
        a = A[tag]
        net = a['drive_saved'] - a['drive_cost']
        print(f"{name:>26} {a['changed']:>8} {a['pickfix_bags']:>13} {a['pickfix_min']:>10.0f}m "
              f"{a['r6_harm']:>8} {a['carry_over_r6']:>9} {a['deliv_harm']:>12} {net:>+10.0f}m {a['fresh_worse']:>9.1f}m")
    aD = A['D']
    dv = live_total_viol - aD['tot_viol']
    pct = (100.0 * dv / live_total_viol) if live_total_viol else 0.0
    print(f"\nWERDYKT D (spójny): -{dv} naruszeń okna ({pct:.0f}%), "
          f"R6 harm={aD['r6_harm']}, carry>35={aD['carry_over_r6']}, deliv>5 harm={aD['deliv_harm']}, "
          f"jazda netto {aD['drive_saved']-aD['drive_cost']:+.0f}m")
    print(f"\n--- przykłady D (naprawia odbiór, 0 harm) ---")
    for c in examples:
        print(f"  oids={c['oids']} viol {c['live_viol']}->{c['lex_viol']} "
              f"beyond5 {c['live_beyond5_min']}->{c['lex_beyond5_min']}min Δdrive {c['d_drive']:+}min "
              f"carry {c['carryB']}->{c['carryD']}")
        print(f"      LIVE: {c['live']}")
        print(f"      D   : {c['lex']}")


if __name__ == '__main__':
    main()
