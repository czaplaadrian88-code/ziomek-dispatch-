#!/usr/bin/env python3
"""Symulacja C (quality-aware pln_v) vs B (within-tier czysty pln_v) na oknie.
Czy kara R6/late zmienia within-tier picki i redukuje R6/spóźnienia — jakim kosztem pay."""
import json, statistics
from datetime import datetime, timedelta, timezone
W=timezone(timedelta(hours=2)); WIN={'2026-06-14','2026-06-15','2026-06-16'}
LOG='/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl'
def tw(t):
    try: return datetime.fromisoformat(t).astimezone(W)
    except: return None
def g(c,k,d=None): return (c or {}).get(k,d) if isinstance(c,dict) else d
def tier(c):
    if g(c,'late_pickup_committed_breach'): return 2
    if g(c,'new_pickup_needs_extension'): return 1
    return 0
INFORMED={'gps','last_assigned_pickup','last_picked_up_pickup','last_picked_up_recent','last_picked_up_delivery','last_delivered','post_wave'}
def bucket(c):
    ps=g(c,'pos_source')
    if ps in (None,'','no_gps','pre_shift'): return 2
    if ps in INFORMED: return 0
    return 1
Q_R6=0.5; Q_LATE=0.3; Q_FREE=5.0
def penalty(c):
    return Q_R6*max(0.0,float(g(c,'objm_r6_breach_max_min') or 0.0))+Q_LATE*max(0.0,float(g(c,'new_pickup_late_min') or 0.0)-Q_FREE)
def r6m(c): return float(g(c,'objm_r6_breach_max_min') or 0.0)
def latem(c): return float(g(c,'new_pickup_late_min') or 0.0)
pln=[]
for line in open(LOG):
    line=line.strip()
    if not line: continue
    try: r=json.loads(line)
    except: continue
    w=tw(r.get('ts',''))
    if not w or w.strftime('%Y-%m-%d') not in WIN: continue
    if g(r.get('best'),'pln_ab_arm')=='pln': pln.append(r)
def cands(r):
    return [c for c in [r.get('best')]+(r.get('alternatives') or []) if isinstance(c,dict) and isinstance(g(c,'pln_v'),(int,float))]
def pick(cs, quality):
    def key(c):
        pv=-((g(c,'pln_v') or 0)-(penalty(c) if quality else 0))
        return (1 if tier(c)==2 else 0, bucket(c), pv)
    return sorted(cs, key=key)[0]
changed=0; r6b=r6a=0.0; lb=la=0.0; pay=[]; ex=[]
n=0
for r in pln:
    cs=cands(r)
    if len(cs)<2: continue
    n+=1
    b=pick(cs,False); c=pick(cs,True)
    r6b+=r6m(b); r6a+=r6m(c); lb+=latem(b); la+=latem(c)
    if str(g(b,'courier_id'))!=str(g(c,'courier_id')):
        changed+=1; pay.append((g(b,'pln_v') or 0)-(g(c,'pln_v') or 0))
        ex.append((r.get('order_id'),'B=%s(r6=%.0f)'%(g(b,'courier_id'),r6m(b)),'C=%s(r6=%.0f)'%(g(c,'courier_id'),r6m(c))))
print('pln-arm z >=2 kandydatami:', n)
print('C zmienia within-tier pick w:', changed,'decyzjach')
print('Σ R6-breach-min (winners): B=%.0f → C=%.0f  (redukcja %.0f)'%(r6b,r6a,r6b-r6a))
print('Σ new_pickup_late_min: B=%.0f → C=%.0f  (redukcja %.0f)'%(lb,la,lb-la))
if pay: print('pay oddany na zmianach Δpln_v: median %.2f | mean %.2f | suma %.1f'%(statistics.median(pay),statistics.mean(pay),sum(pay)))
print('przykłady:', ex[:8])
