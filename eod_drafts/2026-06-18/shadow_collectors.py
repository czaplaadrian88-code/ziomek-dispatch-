#!/usr/bin/env python3
"""Kolektory SHADOW (read-only, zero wpływu na produkcję):
 (1) czysta pętla pomiaru: zgodność + błąd ETA wg REŻIMU OBCIĄŻENIA (join eta_cal↔backfill),
 (2) klasyfikator odraczania/alarmu floty: would_defer / would_alarm + lead-time.
Wyjścia w dispatch_state/ (strumienie shadow + podsumowanie). Można odpalać codziennie (idempotentne)."""
import json, statistics as st
from collections import defaultdict
from datetime import datetime

DS = "/root/.openclaw/workspace/dispatch_state"
def num(x): return x if isinstance(x,(int,float)) else None
def norm(x): return str(x).strip() if x not in (None,"","None") else None
def ts(s):
    try: return datetime.fromisoformat(s)
    except: return None
def pct(n,d): return 100*n/d if d else float("nan")

ETA=[json.loads(l) for l in open(f"{DS}/eta_calibration_log.jsonl")]
BF=[json.loads(l) for l in open(f"{DS}/backfill_decisions_outcomes_v1.jsonl")]

# kontekst obciążenia per order_id z backfilla
load_ctx={}
for r in BF:
    oid=norm(r.get("order_id"))
    if oid: load_ctx[oid]={"pool_feasible":num(r.get("pool_feasible")),"pool_total":num(r.get("pool_total")),"tier":r.get("tier")}

# ═══ (1) CZYSTA PĘTLA POMIARU ═══
print("="*70); print("(1) CZYSTA PĘTLA POMIARU — zgodność + błąd ETA wg obciążenia"); print("="*70)
def agree(r):
    m=r.get("matched_courier")
    if isinstance(m,bool): return m
    b=norm(r.get("best_courier_id")); a=norm(r.get("real_courier_id"))
    return None if (b is None or a is None) else (b==a)
out=[]
for r in ETA:
    oid=norm(r.get("oid") or r.get("order_id"))
    a=agree(r); e=num(r.get("eta_error_min")); bs=num(r.get("bag_size"))
    lc=load_ctx.get(oid,{})
    rec={"oid":oid,"day":(r.get("logged_at") or "")[:10],"agree":a,"eta_error_min":e,
         "bag_size":bs,"pool_feasible":lc.get("pool_feasible"),"sla_ok":r.get("sla_ok"),
         "restaurant":r.get("restaurant"),"hour":num(r.get("hour_warsaw"))}
    out.append(rec)
with open(f"{DS}/outcomes_clean_shadow.jsonl","w") as f:
    for rec in out: f.write(json.dumps(rec,ensure_ascii=False)+"\n")
ag=[r["agree"] for r in out if r["agree"] is not None]
print(f"  zapisano {len(out)} rek. → outcomes_clean_shadow.jsonl")
print(f"  CZYSTA ZGODNOŚĆ ogółem: {pct(sum(ag),len(ag)):.1f}% (n={len(ag)})")
# wg reżimu obciążenia (pool_feasible jeśli jest, inaczej bag_size)
def regime(r):
    p=r.get("pool_feasible")
    if p is not None: return "niedobór(pool≤2)" if p<=2 else "normalnie(pool≥3)"
    b=r.get("bag_size"); return "duży worek(≥4)" if (b or 0)>=4 else "mały worek(≤3)"
byreg=defaultdict(lambda:{"ag":[], "err":[]})
for r in out:
    g=regime(r);
    if r["agree"] is not None: byreg[g]["ag"].append(r["agree"])
    if r["eta_error_min"] is not None: byreg[g]["err"].append(abs(r["eta_error_min"]))
print("  wg reżimu (zgodność | błąd ETA MAE):")
for g,d in sorted(byreg.items()):
    a=pct(sum(d["ag"]),len(d["ag"])) if d["ag"] else float("nan")
    m=st.mean(d["err"]) if d["err"] else float("nan")
    print(f"    {g:18s} zgodność={a:5.1f}% | ETA MAE={m:5.1f} (n={len(d['ag'])}/{len(d['err'])})")
# stabilność dzienna
byday=defaultdict(list)
for r in out:
    if r["agree"] is not None and r["day"]: byday[r["day"]].append(r["agree"])
dr=[pct(sum(v),len(v)) for d,v in sorted(byday.items()) if len(v)>=30]
print(f"  stabilność dzienna: std={st.pstdev(dr) if len(dr)>1 else 0:.1f}pp / {len(dr)} dni")
errbig=[r for r in out if r["bag_size"] and r["bag_size"]>=4 and r["eta_error_min"] is not None]
print(f"  >>> ETA MAE rośnie z obciążeniem? — to wejście do PRZYSZŁEGO predyktora (TEST 1 padł na wersji naiwnej)")

# ═══ (2) ODRACZANIE + ALARM FLOTY ═══
print("\n"+"="*70); print("(2) SHADOW odraczanie + alarm floty (would_defer / would_alarm)"); print("="*70)
SCORE_DEFER=20.0; ALARM_POOL=3; ALARM_RUN=3
ddec=[]
for r in BF:
    d=ts(r.get("decision_ts")); pf=num(r.get("pool_feasible")); sc=num(r.get("proposed_score"))
    if d is None: continue
    would_defer = (pf is not None and pf<=2 and sc is not None and sc<SCORE_DEFER)
    ddec.append({"ts":r.get("decision_ts"),"day":(r.get("decision_ts") or "")[:10],"pool":pf,
                 "score":sc,"would_defer":would_defer,"order_id":norm(r.get("order_id"))})
# alarm: per dzień, okno przesuwne — alarm gdy ALARM_RUN kolejnych decyzji ma pool<ALARM_POOL; lead do pierwszego pool==0
bydayd=defaultdict(list)
for r in ddec:
    if r["pool"] is not None: bydayd[r["day"]].append(r)
alarms=0; leads=[]
defer_n=sum(1 for r in ddec if r["would_defer"])
for day,lst in bydayd.items():
    lst.sort(key=lambda x:x["ts"])
    fired_at=None
    for i in range(len(lst)-ALARM_RUN+1):
        win=lst[i:i+ALARM_RUN]
        if all(w["pool"]<ALARM_POOL for w in win):
            fired_at=ts(win[0]["ts"]); alarms+=1; break
    # pierwszy pool==0 tego dnia
    zero=[ts(w["ts"]) for w in lst if w["pool"]==0]
    if fired_at and zero:
        lead=(min(zero)-fired_at).total_seconds()/60.0
        if lead>0: leads.append(lead)
with open(f"{DS}/defer_alarm_shadow.jsonl","w") as f:
    for r in ddec: f.write(json.dumps(r,ensure_ascii=False)+"\n")
print(f"  zapisano {len(ddec)} rek. → defer_alarm_shadow.jsonl")
print(f"  would_defer: {defer_n} ({pct(defer_n,len(ddec)):.1f}% decyzji) — kandydaci do odroczenia zamiast forsowania")
print(f"  would_alarm: zadziałał w {alarms} dniach (próg pool<{ALARM_POOL} przez {ALARM_RUN} decyzji z rzędu)")
if leads: print(f"  wyprzedzenie alarmu przed 1. pool=0: med={st.median(leads):.0f} min (n={len(leads)})")
print("\nGOTOWE — strumienie shadow zapisane, zero wpływu na produkcję.")
