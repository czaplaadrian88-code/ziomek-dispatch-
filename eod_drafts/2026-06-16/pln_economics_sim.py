#!/usr/bin/env python3
"""Ekonomia E2-pln + symulacja polityk selekcji (READ-ONLY, okno 14-16.06).
Pytanie: ile pln "zarabia" (pay-aware) vs ile traci na jakosci (committed breach,
score) + co zmienia fix within-tier. pln NIE moze liczyc tylko po wynagrodzeniu —
zla jakosc = utrata klienta."""
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
def bmin(c): return (g(c,'late_pickup_committed_max') or 0) if g(c,'late_pickup_committed_breach') else 0
def r6b(c): return 1 if (g(c,'objm_r6_breach_count') or 0)>0 else 0

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
    out=[]
    for i,c in enumerate([r.get('best')]+(r.get('alternatives') or [])):
        if isinstance(c,dict) and isinstance(g(c,'pln_v'),(int,float)): out.append((i,c))
    return out

POL={
 'CURRENT (czysty pln_v)':  lambda i,c:(-(g(c,'pln_v') or -1e9), i),
 'SCORE-only (kill pln)':   lambda i,c:(1 if tier(c)==2 else 0, bucket(c), -(g(c,'score') or -1e9), i),
 'WITHIN-TIER (fix B)':     lambda i,c:(1 if tier(c)==2 else 0, bucket(c), -(g(c,'pln_v') or -1e9), i),
}
usable=[r for r in pln if len(cands(r))>=1]
print('pln-arm decyzji:', len(pln),'| z kandydatami pln_v:', len(usable),
      '| z >=2 kandydatami:', sum(1 for r in pln if len(cands(r))>=2))
cur_key=POL['CURRENT (czysty pln_v)']
for name,key in POL.items():
    nb=0; bms=[]; plnv=[]; sc=[]; r6=0; chg=0
    for r in usable:
        cs=cands(r)
        win=sorted(cs,key=lambda t:key(t[0],t[1]))[0][1]
        cur=sorted(cs,key=lambda t:cur_key(t[0],t[1]))[0][1]
        if str(g(win,'courier_id'))!=str(g(cur,'courier_id')): chg+=1
        if tier(win)==2: nb+=1; bms.append(bmin(win))
        r6+=r6b(win); plnv.append(g(win,'pln_v') or 0); sc.append(g(win,'score') or 0)
    print('\n%-25s | committed-breach: %2d | R6>35: %2d | Σpln_v=%.0f | Σscore=%.0f (śr %.0f) | zmiana vs CURRENT: %d'%(
        name, nb, r6, sum(plnv), sum(sc), statistics.mean(sc) if sc else 0, chg))
    if bms: print('     breach_min median/max: %.1f / %.1f'%(statistics.median(bms),max(bms)))

print('\n== EKONOMIA flipów (CURRENT pln vs SCORE-quality) — pay zysk vs jakość strata ==')
dpv=[];dsc=[];dbr=[]
for r in usable:
    cs=cands(r)
    if len(cs)<2: continue
    cur=sorted(cs,key=lambda t:POL['CURRENT (czysty pln_v)'](t[0],t[1]))[0][1]
    sco=sorted(cs,key=lambda t:POL['SCORE-only (kill pln)'](t[0],t[1]))[0][1]
    if str(g(cur,'courier_id'))!=str(g(sco,'courier_id')):
        dpv.append((g(cur,'pln_v') or 0)-(g(sco,'pln_v') or 0))
        dsc.append((g(sco,'score') or 0)-(g(cur,'score') or 0))
        dbr.append(bmin(cur))
print('flipów pln≠quality:', len(dpv))
if dpv:
    print('  ZYSK pay (Δpln_v): suma %.0f | median %.2f | mean %.2f'%(sum(dpv),statistics.median(dpv),statistics.mean(dpv)))
    print('  STRATA jakości (Δscore, ile gorszy delivery): suma %.0f | median %.0f | mean %.0f'%(sum(dsc),statistics.median(dsc),statistics.mean(dsc)))
    pos=[x for x in dbr if x>0]
    print('  KOSZT klienta (łamie committed): %d/%d flipów, min spóźnienia: median %.1f max %.1f suma %.0f'%(
        len(pos),len(dpv),statistics.median(pos) if pos else 0,max(dbr) if dbr else 0,sum(dbr)))
    win_pay=sum(1 for x in dpv if x>0)
    print('  ile flipów pln dało WYŻSZY pln_v niż quality-pick: %d/%d'%(win_pay,len(dpv)))
