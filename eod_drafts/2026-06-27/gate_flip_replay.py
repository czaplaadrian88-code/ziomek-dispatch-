#!/usr/bin/env python3
"""Gate-flip replay (#3, Adrian 27.06): ile decyzji bramki feasibility_v2:1135
przeskakuje PASS->NO pod anchor-fix (pickup_at -> ready). Read-only, scratchpad.

Wierność: REALNY silnikowy `simulate_bag_route_v2` (plan z pickup_at+delivered_at)
+ `r6_thermal_anchor` + replikacja logiki bramki 1135-1188 (split pre-existing vs
blocking) dla OBU kotwic. Korpus = obj_replay_capture.jsonl (wejścia solvera 1:1).
"""
import sys, json
from datetime import timezone
sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, r6_thermal_anchor  # noqa
from dispatch_v2.tools.obj_harness import _ordersim_from_capture, _dt  # noqa
import dispatch_v2.common as C  # noqa

UTC = timezone.utc
SLA = 35.0  # R6 cap normalny (tier-3=40 = rzadkie dni, pomijam — magnituda)
CAP = int(sys.argv[1]) if len(sys.argv) > 1 else 1500

def _aware(dt):
    if dt is None: return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

def gate(plan, bag, new_order, now, anchor):
    """Replika feasibility_v2:1135-1188 dla wybranej kotwicy.
    Zwraca (n_viol, n_blocking, verdict PASS/NO)."""
    delivered = plan.predicted_delivered_at
    pickup_at = plan.pickup_at
    new_pu = _aware(pickup_at.get(new_order.order_id))
    n_viol = 0; n_pre = 0
    for o in list(bag) + [new_order]:
        pred = _aware(delivered.get(o.order_id))
        if pred is None:
            continue
        if anchor == 'pickup':
            if o.order_id in pickup_at:
                pu = _aware(pickup_at[o.order_id])
            elif getattr(o, 'picked_up_at', None) is not None:
                pu = _aware(o.picked_up_at)
            else:
                pu = now
        else:  # ready-anchor (anchor-fix)
            pu, _src, _ip = r6_thermal_anchor(o, o is new_order, pickup_at, now)
        elapsed = (pred - pu).total_seconds() / 60.0
        if elapsed > SLA:
            n_viol += 1
            is_picked = (o is not new_order) and (
                getattr(o, 'picked_up_at', None) is not None
                or getattr(o, 'status', None) == 'picked_up')
            if is_picked and new_pu is not None and pred <= new_pu:
                n_pre += 1
    n_block = n_viol - n_pre
    return n_viol, n_block, ('NO' if n_block > 0 else 'PASS')

def main():
    path = '/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl'
    seen = 0; ran = 0; err = 0
    # liczniki per bag-class
    stats = {'flip_pass_to_no': 0, 'viol_0_to_pos': 0, 'same': 0,
             'pickup_PASS': 0, 'ready_PASS': 0, 'pickup_NO': 0, 'ready_NO': 0}
    flips_examples = []
    for line in open(path):
        if ran >= CAP:
            break
        try:
            rec = json.loads(line)
        except Exception:
            continue
        bag_raw = rec.get('bag') or []
        if len(bag_raw) < 1:          # gate-bypass interesujący tylko gdy worek niepusty
            continue
        seen += 1
        try:
            bag = [_ordersim_from_capture(b) for b in bag_raw]
            no = _ordersim_from_capture(rec.get('new_order') or {})
            cpos = rec.get('courier_pos')
            if not cpos or no.order_id is None:
                continue
            now = _dt(rec.get('now'))
            dp = rec.get('dwell_pickup'); dd = rec.get('dwell_dropoff')
            if dp is None or dd is None:
                dp, dd = C.dwell_for_tier(rec.get('tier'))
            plan = simulate_bag_route_v2(tuple(cpos), bag, no, now=now,
                                         dwell_pickup=dp, dwell_dropoff=dd)
            if plan is None or not plan.predicted_delivered_at:
                continue
            vp, bp, verp = gate(plan, bag, no, now, 'pickup')
            vr, br, verr = gate(plan, bag, no, now, 'ready')
            ran += 1
            stats['pickup_PASS'] += (verp == 'PASS'); stats['pickup_NO'] += (verp == 'NO')
            stats['ready_PASS'] += (verr == 'PASS'); stats['ready_NO'] += (verr == 'NO')
            if verp == 'PASS' and verr == 'NO':
                stats['flip_pass_to_no'] += 1
                if len(flips_examples) < 8:
                    flips_examples.append((no.order_id, len(bag), vp, vr, bp, br))
            elif vp == 0 and vr > 0:
                stats['viol_0_to_pos'] += 1
            else:
                stats['same'] += 1
        except Exception as e:
            err += 1
            if err <= 3:
                print(f'  [err] {type(e).__name__}: {e}')
            continue
    print(f'\n=== GATE-FLIP REPLAY (korpus obj_replay_capture, worki >=1) ===')
    print(f'przejrzane worki: {seen} | zsymulowane: {ran} | błędy: {err}')
    if ran:
        fp = stats['flip_pass_to_no']
        print(f'\nWERDYKT bramki (anchor-fix pickup->ready):')
        print(f'  pickup-anchor (DZIŚ):  PASS={stats["pickup_PASS"]}  NO={stats["pickup_NO"]}')
        print(f'  ready-anchor  (FIX):   PASS={stats["ready_PASS"]}  NO={stats["ready_NO"]}')
        print(f'\n  >>> FLIP PASS->NO (bramka odrzuci, dziś przepuszcza): {fp} ({100*fp/ran:.1f}% worków)')
        print(f'      (te NIE znikają — spadają do best_effort, dalej proponowane: always-propose)')
        print(f'  sla 0->>0 ale werdykt bez zmian (pre-existing bypass): {stats["viol_0_to_pos"]} ({100*stats["viol_0_to_pos"]/ran:.1f}%)')
        print(f'  bez zmiany: {stats["same"]} ({100*stats["same"]/ran:.1f}%)')
        if flips_examples:
            print(f'\n  przykłady flipów (oid, bag, viol_pickup, viol_ready, block_pickup, block_ready):')
            for ex in flips_examples:
                print(f'    {ex}')

if __name__ == '__main__':
    main()
