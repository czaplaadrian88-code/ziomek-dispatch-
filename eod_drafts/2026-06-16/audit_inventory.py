#!/usr/bin/env python3
"""READ-ONLY audit pass #1 over shadow_decisions.jsonl (Ziomek proposals).
Window: Warsaw days 2026-06-14..16. Streams the file, no full load.
Produces: structural samples + verdict/reason vocab + cheap aggregates."""
import json, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

LOG = '/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl'
WARSAW = timezone(timedelta(hours=2))  # CEST, no DST transition in June
WIN = {'2026-06-14', '2026-06-15', '2026-06-16'}

def to_warsaw(ts):
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(WARSAW)
    except Exception:
        return None

per_day_all = Counter()           # all days present in file
per_day = Counter()               # window only
per_dayhour = Counter()
verdict_c = Counter()
verdict_by_day = defaultdict(Counter)
reason_c = Counter()
restaurant_c = Counter()
auto_route_c = Counter()
verdict_autoroute = Counter()     # (verdict, auto_route_bool)
pool_feas = Counter()
pool_total = Counter()
feas0_verdict = Counter()         # verdict when pool_feasible_count == 0
latencies = []
flag_true = Counter()
prep_anom = Counter()
order_records = defaultdict(int)  # order_id -> n records (window)
order_first_last = {}             # order_id -> [first_ts, last_ts]
best_keys = Counter()
alt_keys = Counter()
dmeta_keys = Counter()
samples = []
FLAGS = ['auto_route','best_effort_r6_redirect','commit_divergence_redirect',
         'difficult_case_redirect','pickup_extension_redirect','late_pickup_shadow',
         'r6_danger_shadow','r6_breach_guard_shadow','selection_veto_shadow',
         'loadaware_shadow','prep_variance_anomaly']

n_total = 0
n_win = 0
with open(LOG, 'r') as f:
    for line in f:
        if not line.strip():
            continue
        n_total += 1
        try:
            r = json.loads(line)
        except Exception:
            continue
        w = to_warsaw(r.get('ts',''))
        if w is None:
            continue
        d = w.strftime('%Y-%m-%d')
        per_day_all[d] += 1
        if d not in WIN:
            continue
        n_win += 1
        per_day[d] += 1
        per_dayhour[(d, w.hour)] += 1
        v = r.get('verdict')
        verdict_c[v] += 1
        verdict_by_day[d][v] += 1
        reason_c[str(r.get('reason'))[:90]] += 1
        restaurant_c[str(r.get('restaurant'))[:40]] += 1
        ar = bool(r.get('auto_route'))
        auto_route_c[ar] += 1
        verdict_autoroute[(v, ar)] += 1
        pf = r.get('pool_feasible_count')
        pool_feas[pf] += 1
        pool_total[r.get('pool_total_count')] += 1
        if pf == 0:
            feas0_verdict[v] += 1
        lat = r.get('latency_ms')
        if isinstance(lat,(int,float)):
            latencies.append(lat)
        for fl in FLAGS:
            if r.get(fl):
                flag_true[fl] += 1
        if r.get('prep_variance_anomaly'):
            prep_anom[r.get('prep_bias_min')] += 1
        oid = r.get('order_id')
        order_records[oid] += 1
        if oid not in order_first_last:
            order_first_last[oid] = [r.get('ts'), r.get('ts')]
        else:
            order_first_last[oid][1] = r.get('ts')
        b = r.get('best')
        if isinstance(b, dict):
            for k in b: best_keys[k] += 1
        a = r.get('alternatives')
        if isinstance(a, list) and a and isinstance(a[0], dict):
            for k in a[0]: alt_keys[k] += 1
        dm = r.get('decision_meta')
        if isinstance(dm, dict):
            for k in dm: dmeta_keys[k] += 1
        if len(samples) < 3:
            samples.append(r)

def pct(lst, p):
    if not lst: return None
    s = sorted(lst); i = min(len(s)-1, int(round((p/100)*(len(s)-1))))
    return s[i]

print('== COVERAGE ==')
print('total lines parsed:', n_total, ' window records:', n_win)
print('per-day ALL days in file:')
for d in sorted(per_day_all): print('   ', d, per_day_all[d])
print('per-day WINDOW:')
for d in sorted(per_day): print('   ', d, per_day[d])
print('\n16.06 per-hour (Warsaw):')
for (d,h) in sorted(per_dayhour):
    if d=='2026-06-16': print('    16.06 %02d:00  %d' % (h, per_dayhour[(d,h)]))
print('\n== VERDICT distribution (window) ==')
for v,c in verdict_c.most_common(): print('   %-28s %d (%.1f%%)' % (str(v), c, 100*c/max(1,n_win)))
print('\nverdict x day:')
for d in sorted(verdict_by_day):
    print('  ',d, dict(verdict_by_day[d]))
print('\n== AUTO_ROUTE bool ==', dict(auto_route_c))
print('verdict x auto_route:')
for k,c in verdict_autoroute.most_common(): print('   ',k,c)
print('\n== LATENCY ms == n=%d median=%s p90=%s p99=%s max=%s' % (
    len(latencies), pct(latencies,50), pct(latencies,90), pct(latencies,99), max(latencies) if latencies else None))
print('\n== POOL_FEASIBLE_COUNT == (verdict when 0 below)')
for k in sorted(pool_feas, key=lambda x:(x is None, x)): print('   feasible=%s : %d' % (k, pool_feas[k]))
print('  feas==0 verdict breakdown:', dict(feas0_verdict))
print('\n== FLAGS true count (window) ==')
for fl in FLAGS: print('   %-28s %d' % (fl, flag_true.get(fl,0)))
print('prep_variance_anomaly bias_min dist:', dict(prep_anom))
print('\n== TOP RESTAURANTS ==')
for r,c in restaurant_c.most_common(20): print('   %-40s %d' % (r,c))
print('\n== TOP REASON strings (window) ==')
for r,c in reason_c.most_common(40): print('   %5d  %s' % (c, r))
print('\n== RECORDS PER ORDER (window) ==')
rpo = Counter(order_records.values())
print('  distribution n_records->n_orders:', dict(sorted(rpo.items())))
multi = {o:n for o,n in order_records.items() if n>=4}
print('  distinct orders in window:', len(order_records), ' orders with >=4 records:', len(multi))
top = sorted(order_records.items(), key=lambda x:-x[1])[:15]
print('  top re-proposed orders (order_id, n):')
for o,n in top:
    fl = order_first_last.get(o)
    print('     ', o, n, fl)
print('\n== STRUCT: best keys ==', dict(best_keys.most_common()))
print('== STRUCT: alternatives[0] keys ==', dict(alt_keys.most_common()))
print('== STRUCT: decision_meta keys ==', dict(dmeta_keys.most_common()))
print('\n== SAMPLE RECORD (1st in window, pretty, truncated) ==')
print(json.dumps(samples[0], ensure_ascii=False, indent=1)[:2600] if samples else 'none')
