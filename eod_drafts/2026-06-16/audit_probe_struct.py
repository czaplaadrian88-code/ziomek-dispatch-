#!/usr/bin/env python3
"""Probe structure of best/alternatives/decision_meta and KOORD reasons."""
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

LOG = '/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl'
WARSAW = timezone(timedelta(hours=2))
WIN = {'2026-06-14','2026-06-15','2026-06-16'}

def to_w(ts):
    try:
        dt = datetime.fromisoformat(ts)
        return dt.astimezone(WARSAW)
    except Exception:
        return None

recs=[]
with open(LOG) as f:
    for line in f:
        if not line.strip(): continue
        try: r=json.loads(line)
        except: continue
        w=to_w(r.get('ts',''))
        if w and w.strftime('%Y-%m-%d') in WIN:
            recs.append(r)
print('window recs:',len(recs))

bk=Counter(); ak=Counter(); dk=Counter()
for r in recs:
    b=r.get('best')
    if isinstance(b,dict):
        for k in b: bk[k]+=1
    a=r.get('alternatives')
    if isinstance(a,list) and a and isinstance(a[0],dict):
        for k in a[0]: ak[k]+=1
    dm=r.get('decision_meta')
    if isinstance(dm,dict):
        for k in dm: dk[k]+=1
print('\nbest keys:',dict(bk.most_common()))
print('alt[0] keys:',dict(ak.most_common()))
print('decision_meta keys:',dict(dk.most_common()))

def first_with(verdict):
    for r in recs:
        if r.get('verdict')==verdict and isinstance(r.get('best'),dict):
            return r
    return None

for V in ('PROPOSE','KOORD'):
    r=first_with(V)
    print('\n===== sample',V,'event',r.get('event_id'),'order',r.get('order_id'),'=====')
    print('reason:',r.get('reason'))
    print('pool_feasible/total:',r.get('pool_feasible_count'),'/',r.get('pool_total_count'))
    print('best:',json.dumps(r.get('best'),ensure_ascii=False,indent=1)[:1500])
    a=r.get('alternatives') or []
    print('n_alternatives:',len(a))
    if a: print('alt[0]:',json.dumps(a[0],ensure_ascii=False,indent=1)[:900])
    dm=r.get('decision_meta') or {}
    print('decision_meta top-level types:',{k:type(v).__name__ for k,v in dm.items()})

# KOORD deep-dive
koord=[r for r in recs if r.get('verdict')=='KOORD']
print('\n===== KOORD deep-dive (n=%d) =====' % len(koord))
print('KOORD reason (full) distribution:')
cr=Counter(str(r.get('reason')) for r in koord)
for rs,c in cr.most_common(30): print('   %4d  %s' % (c,rs))
print('\nKOORD by pool_feasible_count:')
cf=Counter(r.get('pool_feasible_count') for r in koord)
for k in sorted(cf,key=lambda x:(x is None,x)): print('   feasible=%s : %d'%(k,cf[k]))
print('\nKOORD with pool_feasible>=1 : reason samples (first 18):')
n=0
for r in koord:
    if (r.get('pool_feasible_count') or 0)>=1:
        n+=1
        if n<=18:
            w=to_w(r.get('ts'))
            print('   %s ord=%s feas=%s | %s' % (w.strftime('%m-%d %H:%M'), r.get('order_id'),
                  r.get('pool_feasible_count'), str(r.get('reason'))[:120]))
print('   total KOORD with feasible>=1:',n)
