#!/usr/bin/env python3
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
LOG='/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl'
WARSAW=timezone(timedelta(hours=2)); WIN={'2026-06-14','2026-06-15','2026-06-16'}
def tw(ts):
    try: return datetime.fromisoformat(ts).astimezone(WARSAW)
    except: return None
recs=[]
with open(LOG) as f:
    for line in f:
        if not line.strip(): continue
        try: r=json.loads(line)
        except: continue
        w=tw(r.get('ts',''))
        if w and w.strftime('%Y-%m-%d') in WIN:
            r['_d']=w.strftime('%Y-%m-%d'); r['_h']=w.hour; recs.append(r)

def sub(r):
    v=r.get('verdict'); rs=str(r.get('reason',''))
    if v=='PROPOSE':
        return 'PROPOSE_besteff' if (r.get('best') or {}).get('best_effort') else 'PROPOSE'
    if rs.startswith('early_bird'): return 'KOORD_earlybird'
    if rs.startswith('all_candidates_low_score'):
        return 'KOORD_lowscore_emptypool' if (r.get('pool_feasible_count') or 0)==0 else 'KOORD_lowscore_feas'
    if (r.get('pool_feasible_count') or 0)==0: return 'KOORD_emptypool_other'
    return 'KOORD_other'

perday=defaultdict(Counter)
for r in recs: perday[r['_d']][sub(r)]+=1
print('=== verdict subtype per day ===')
cats=['PROPOSE','PROPOSE_besteff','KOORD_earlybird','KOORD_lowscore_feas','KOORD_lowscore_emptypool','KOORD_emptypool_other','KOORD_other']
print('%-28s %8s %8s %8s'%('subtype','06-14','06-15','06-16'))
for c in cats:
    print('%-28s %8d %8d %8d'%(c,perday['2026-06-14'][c],perday['2026-06-15'][c],perday['2026-06-16'][c]))
tot=lambda d: sum(perday[d].values())
print('%-28s %8d %8d %8d'%('TOTAL',tot('2026-06-14'),tot('2026-06-15'),tot('2026-06-16')))
koord=lambda d: sum(v for k,v in perday[d].items() if k.startswith('KOORD'))
for d in ['2026-06-14','2026-06-15','2026-06-16']:
    print('  KOORD%% %s = %.1f%%'%(d,100*koord(d)/max(1,tot(d))))

# KOORD_lowscore_feas per hour on 14.06 (the cascade)
print('\n=== KOORD_lowscore_feas per hour ===')
ph=defaultdict(Counter)
for r in recs:
    if sub(r)=='KOORD_lowscore_feas': ph[r['_d']][r['_h']]+=1
for d in ['2026-06-14','2026-06-15','2026-06-16']:
    if ph[d]: print(' ',d,dict(sorted(ph[d].items())))

# Courier 518 profile across window (where it appears as best or alt)
print('\n=== courier 518 profile (as best or alternative) ===')
prof=Counter(); seen=0; as_best=0
for r in recs:
    cands=[r.get('best')]+ (r.get('alternatives') or [])
    for c in cands:
        if isinstance(c,dict) and str(c.get('courier_id'))=='518':
            seen+=1
            prof[(c.get('pos_source'), c.get('r6_bag_size'), c.get('feasibility'))]+=1
            if c is r.get('best'): as_best+=1
            break
print('518 appears in %d window recs, as BEST in %d'%(seen,as_best))
print('518 (pos_source,r6_bag_size,feasibility) dist:')
for k,v in prof.most_common(12): print('   ',k,v)
# 518 name
for r in recs:
    for c in [r.get('best')]+(r.get('alternatives') or []):
        if isinstance(c,dict) and str(c.get('courier_id'))=='518':
            print('518 name:',c.get('name'),'| sample score',c.get('score'),'| km_to_pickup',c.get('km_to_pickup')); break
    else: continue
    break

# pos_source gps vs estimated per day
print('\n=== best.pos_source gps-share per day ===')
for d in ['2026-06-14','2026-06-15','2026-06-16']:
    dd=[r for r in recs if r['_d']==d]
    gps=sum(1 for r in dd if (r.get('best') or {}).get('pos_source')=='gps')
    nonnull=sum(1 for r in dd if (r.get('best') or {}).get('pos_source'))
    print('  %s gps=%d / pos!=None=%d (%.0f%%)'%(d,gps,nonnull,100*gps/max(1,nonnull)))
