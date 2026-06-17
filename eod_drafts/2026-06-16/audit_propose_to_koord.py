#!/usr/bin/env python3
"""(b) READ-ONLY: zlecenia, które miały PROPOSE, a na re-propozycji dostały KOORD.
Dump obu rekordów, by zobaczyć CO się zmieniło (kurier/score/feasible/reason)."""
import json
from datetime import datetime, timedelta, timezone
from collections import defaultdict
LOG='/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl'
W=timezone(timedelta(hours=2)); WIN={'2026-06-14','2026-06-15','2026-06-16'}
def tw(t):
    try: return datetime.fromisoformat(t).astimezone(W)
    except: return None
def g(c,k):
    return (c or {}).get(k) if isinstance(c,dict) else None
recs=[]
for line in open(LOG):
    line=line.strip()
    if not line: continue
    try: r=json.loads(line)
    except: continue
    w=tw(r.get('ts',''))
    if w and w.strftime('%Y-%m-%d') in WIN: recs.append(r)
byo=defaultdict(list)
for r in recs: byo[r.get('order_id')].append(r)
print('(b) PROPOSE -> KOORD transitions:')
n=0
for o,rs in byo.items():
    rs.sort(key=lambda r:r['ts'])
    for i in range(1,len(rs)):
        if rs[i-1].get('verdict')=='PROPOSE' and rs[i].get('verdict')=='KOORD':
            n+=1
            dt=(datetime.fromisoformat(rs[i]['ts'])-datetime.fromisoformat(rs[i-1]['ts'])).total_seconds()/60
            print('\n  ord=%s  rest=%s  Δt=%.1f min  (rekordow zlecenia: %d)'%(o,rs[i].get('restaurant'),dt,len(rs)))
            for tag,r in [('PROPOSE',rs[i-1]),('KOORD  ',rs[i])]:
                b=r.get('best') or {}
                print('    [%s] %s | best=%s "%s" score=%.0f feas=%s pos=%s pf=%s eta=%s | %s'%(
                  tag, tw(r['ts']).strftime('%m-%d %H:%M:%S'),
                  g(b,'courier_id'), g(b,'name'), g(b,'score') or 0, g(b,'feasibility'),
                  g(b,'pos_source'), r.get('pool_feasible_count'), g(b,'eta_drive_hhmm'),
                  str(r.get('reason'))[:90]))
print('\ntotal PROPOSE->KOORD:',n)
