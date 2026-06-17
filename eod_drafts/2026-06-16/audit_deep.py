#!/usr/bin/env python3
"""READ-ONLY audit pass #3: KOORD low-score forensics, oscillation, pos_source,
OSRM degraded, R6/late-pickup. Window Warsaw 2026-06-14..16."""
import json, re
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
            r['_w']=w; recs.append(r)
print('recs',len(recs))

def g(c,k,d=None): return (c or {}).get(k,d) if isinstance(c,dict) else d
def cand_tier(c):
    if g(c,'late_pickup_committed_breach'): return 2
    if g(c,'new_pickup_needs_extension'): return 1
    return 0

# ---------- A) KOORD low-score forensics ----------
koord=[r for r in recs if r.get('verdict')=='KOORD']
low=[r for r in koord if str(r.get('reason','')).startswith('all_candidates_low_score')]
eb=[r for r in koord if str(r.get('reason','')).startswith('early_bird')]
print('\n[A] KOORD total %d | early_bird %d | all_candidates_low_score %d | other %d'%(
    len(koord),len(eb),len(low),len(koord)-len(eb)-len(low)))
rescuable=0; tier2_tradeoff=0; clean_lowscore=0
print('\n[A] per-KOORD-lowscore: best vs best clean alt (score>-100, committed_breach=False):')
for r in low:
    b=r.get('best') or {}; alts=r.get('alternatives') or []
    # best clean alt by score
    clean=[a for a in alts if isinstance(g(a,'score'),(int,float)) and g(a,'score')>-100 and not g(a,'late_pickup_committed_breach')]
    clean.sort(key=lambda a:-(g(a,'score') or 0))
    any_score_gt=[a for a in alts if isinstance(g(a,'score'),(int,float)) and g(a,'score')>-100]
    has_clean=len(clean)>0
    if any_score_gt: rescuable+=1
    if has_clean: clean_lowscore+=1
    else:
        # is there a high-score alt that is tier2 (breaks committed)?
        t2=[a for a in alts if isinstance(g(a,'score'),(int,float)) and g(a,'score')>-100 and g(a,'late_pickup_committed_breach')]
        if t2: tier2_tradeoff+=1
    if len(low)<=40 or has_clean:  # print all if few, else only the suspicious clean ones
        ca=clean[0] if clean else None
        print('  %s ord=%s feas=%s best=%s(score=%.0f,tier=%d,brk=%s,ext=%s,pos=%s,eta=%s) cleanAlt=%s'%(
            r['_w'].strftime('%m-%d %H:%M'), r.get('order_id'), r.get('pool_feasible_count'),
            g(b,'courier_id'), g(b,'score') or 0, cand_tier(b), g(b,'late_pickup_committed_breach'),
            g(b,'new_pickup_needs_extension'), g(b,'pos_source'), g(b,'eta_drive_hhmm'),
            ('%s(score=%.0f,brk=%s,latemin=%s)'%(g(ca,'courier_id'),g(ca,'score') or 0,
              g(ca,'late_pickup_committed_breach'),g(ca,'new_pickup_late_min')) if ca else 'NONE')))
print('\n[A] SUMMARY low-score KOORD=%d: with ANY alt score>-100 = %d | with CLEAN alt(no committed breach) score>-100 = %d | tier2-tradeoff(only breaching alts good) = %d'%(
    len(low),rescuable,clean_lowscore,tier2_tradeoff))

# ---------- B) Oscillation / duplicates ----------
byo=defaultdict(list)
for r in recs: byo[r.get('order_id')].append(r)
multi={o:rs for o,rs in byo.items() if len(rs)>=2}
print('\n[B] multi-record orders:',len(multi))
courier_flip=0; verdict_flip=0; ABA=0
flip_examples=[]
for o,rs in multi.items():
    rs.sort(key=lambda r:r['ts'])
    cids=[g(r.get('best'),'courier_id') for r in rs]
    verds=[r.get('verdict') for r in rs]
    distinct_c=[c for i,c in enumerate(cids) if i==0 or c!=cids[i-1]]  # collapse repeats
    if len(set([c for c in cids if c]))>1: courier_flip+=1
    if len(set(verds))>1: verdict_flip+=1
    # A->B->A pattern
    if len(distinct_c)>=3 and distinct_c[0]==distinct_c[2] and distinct_c[0] is not None:
        ABA+=1; flip_examples.append((o,distinct_c,verds))
print('[B] orders with courier change across re-proposals:',courier_flip)
print('[B] orders with verdict change (PROPOSE<->KOORD):',verdict_flip)
print('[B] A->B->A courier oscillations:',ABA)
for o,dc,vv in flip_examples[:10]:
    print('    ord=%s couriers=%s verdicts=%s'%(o,dc,vv))
# verdict transition pairs
trans=Counter()
for o,rs in multi.items():
    rs.sort(key=lambda r:r['ts'])
    for i in range(1,len(rs)):
        trans[(rs[i-1].get('verdict'),rs[i].get('verdict'))]+=1
print('[B] verdict transitions on re-proposal:',dict(trans))

# ---------- C) pos_source ----------
ps_all=Counter(g(r.get('best'),'pos_source') for r in recs)
ps_koord=Counter(g(r.get('best'),'pos_source') for r in koord)
store=[r for r in recs if g(r.get('best'),'pos_from_store')]
print('\n[C] best.pos_source ALL:',dict(ps_all))
print('[C] best.pos_source KOORD:',dict(ps_koord))
print('[C] best.pos_from_store True (last-known rescue):',len(store))

# ---------- D) OSRM degraded ----------
deg=[r for r in recs if g(r.get('decision_meta'),'degraded_osrm')]
ages=[g(r.get('decision_meta'),'osrm_cache_age_s') for r in recs if isinstance(g(r.get('decision_meta'),'osrm_cache_age_s'),(int,float))]
ages_s=sorted(ages)
print('\n[D] degraded_osrm True: %d / %d (%.0f%%)'%(len(deg),len(recs),100*len(deg)/max(1,len(recs))))
if ages_s: print('[D] osrm_cache_age_s min/median/max: %.0f / %.0f / %.0f'%(ages_s[0],ages_s[len(ages_s)//2],ages_s[-1]))

# ---------- E) R6 / late-pickup on PROPOSE ----------
prop=[r for r in recs if r.get('verdict')=='PROPOSE']
r6b=[r for r in prop if (g(r.get('best'),'objm_r6_breach_count') or 0)>0]
ext=[r for r in prop if g(r.get('best'),'new_pickup_needs_extension')]
brk=[r for r in prop if g(r.get('best'),'late_pickup_committed_breach')]
besteff=[r for r in prop if g(r.get('best'),'best_effort')]
print('\n[E] PROPOSE=%d'%len(prop))
print('[E] PROPOSE best objm_r6_breach_count>0 (>35min bag breach): %d (%.0f%%)'%(len(r6b),100*len(r6b)/max(1,len(prop))))
print('[E] PROPOSE best new_pickup_needs_extension (tier1, deferred pickup): %d'%len(ext))
print('[E] PROPOSE best late_pickup_committed_breach (tier2, breaks committed!): %d'%len(brk))
print('[E] PROPOSE best.best_effort=True: %d'%len(besteff))
if brk:
    print('[E] tier2 PROPOSE examples (breaks committed pickup):')
    for r in brk[:8]:
        b=r.get('best') or {}
        print('    %s ord=%s cid=%s commit_breach_min=%s worst_rest=%s'%(
            r['_w'].strftime('%m-%d %H:%M'),r.get('order_id'),g(b,'courier_id'),
            g(b,'late_pickup_committed_max'),g(b,'late_pickup_committed_worst_restaurant')))

# ---------- F) R1 phantom-pickup signatures ----------
print('\n[F] bug2_pickup_src dist:',dict(Counter(g(r.get('best'),'bug2_pickup_src') for r in recs)))
rtr=[r for r in recs if g(r.get('best'),'return_to_restaurant')]
print('[F] best.return_to_restaurant True:',len(rtr))
