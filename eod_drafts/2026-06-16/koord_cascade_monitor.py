#!/usr/bin/env python3
"""(c) Monitor kaskady KOORD (READ-ONLY compute).
Liczy KOORD `all_candidates_low_score` z feasible>=1 dla dnia Warsaw.
Przy ENABLE_ALWAYS_PROPOSE_ON_SATURATION=ON powinno byc ~0; >0 => regres bramki
(dispatch_pipeline.py:5313-5336) -> do zbadania. NIE wysyla alertu (compute-only);
podpiecie send_admin_alert(priority=low -> cichy bot/Powiadomienia) + timer systemd
= osobny krok instalacyjny po ACK Adriana.
Uzycie: python3 koord_cascade_monitor.py [YYYY-MM-DD]  (domyslnie: ostatni dzien w logu)
Exit code 2 gdy cascade>0 (do uzycia jako OnFailure trigger przy instalacji)."""
import json, sys
from datetime import datetime, timedelta, timezone
from collections import Counter
LOG='/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl'
W=timezone(timedelta(hours=2))
def tw(t):
    try: return datetime.fromisoformat(t).astimezone(W)
    except: return None
target=sys.argv[1] if len(sys.argv)>1 else None
days=Counter(); cascade=Counter()
for line in open(LOG):
    line=line.strip()
    if not line: continue
    try: r=json.loads(line)
    except: continue
    w=tw(r.get('ts',''))
    if not w: continue
    d=w.strftime('%Y-%m-%d'); days[d]+=1
    if (r.get('verdict')=='KOORD'
            and str(r.get('reason','')).startswith('all_candidates_low_score')
            and (r.get('pool_feasible_count') or 0)>=1):
        cascade[d]+=1
if not days:
    print('brak danych'); sys.exit(0)
if target is None: target=max(days)
c=cascade.get(target,0); tot=days.get(target,0)
verdict='OK (~0, polityka always-propose dziala)' if c==0 else 'REGRES? zbadaj bramke KOORD / ALWAYS_PROPOSE'
print('KOORD-cascade monitor | dzien=%s | rekordow=%d | KOORD all_candidates_low_score feasible>=1 = %d | %s'%(
    target, tot, c, verdict))
print('  ostatnie dni:')
for d in sorted(days)[-6:]:
    print('    %s: cascade=%d / rekordow=%d'%(d, cascade.get(d,0), days.get(d,0)))
sys.exit(2 if c>0 else 0)
