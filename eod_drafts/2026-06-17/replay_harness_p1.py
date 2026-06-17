#!/usr/bin/env python3
"""
REPLAY-HARNESS P1a/P1b — READ-ONLY counterfactual selection replay.

Idea: shadow_decisions logs, PER CANDIDATE, both the additive-score penalty components
(bonus_v3273_wait_courier, bonus_r8_soft_pen, bonus_r9_stopover, v326_fleet_load_adjustment) AND
the true-objective + waste metrics (objm_r6_breach_max_min, new_pickup_late_min,
late_pickup_committed_max, v3273_wait_courier_max_min = idle) AND two already-calibrated objective
values (pln_v, objm_*). So we can RE-RANK the logged feasible pool under counterfactual selection
policies WITHOUT re-running the pipeline, and measure the delivery-objective gain vs idle-waste cost.

Policies compared (within best's tier+bucket group, decision-time):
  A  baseline    = argmax(score)                  [= what Ziomek picked]
  P1a wait_x     = argmax(score with wait penalty * x)
  P1b load_x     = argmax(score with r8/r9/fleet_load * x)
  P1ab           = both scaled 0.5
  C  pln_v       = argmax(pln_v)                  [existing pln_objective shadow — reuse]
  D  objm_obj    = argmin(R6_breach + committed + 0.5*max(0,new_late-5))  [objm metrics — reuse]

Metric per picked candidate (lower=better, all minutes):
  R6_breach = objm_r6_breach_max_min ; new_late = new_pickup_late_min ;
  committed = late_pickup_committed_max ; waste = v3273_wait_courier_max_min (idle at restaurant)

Faithfulness: baseline argmax-score-in-group must reproduce logged best; report match rate.
Excludes E2 (order_id%5==0) — their logged best reflects the (now-fixed) E2 pln resort.
"""
import json
from datetime import datetime, timedelta
from collections import defaultdict

SHADOW="/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
ROT="/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1"

def wday(ts):
    try: d=datetime.fromisoformat(str(ts).replace("Z","+00:00")); return (d+timedelta(hours=2)).strftime("%Y-%m-%d")
    except: return None
def inwin(ts):
    d=wday(ts); return bool(d and "2026-06-10"<=d<="2026-06-16")
def num(x,d=0.0):
    try:
        if x is None: return d
        return float(x)
    except: return d

INFORMED={"gps","last_picked_up_recent","last_picked_up_pickup","last_picked_up_delivery","last_assigned_pickup","last_assigned_delivery","post_wave","pos_from_store","last_known","last_delivered"}
def tier_rank(c):
    if c.get("late_pickup_committed_breach") is True: return 2
    if c.get("new_pickup_needs_extension") is True: return 1
    return 0
def bucket_rank(c):
    ps=c.get("pos_source"); bag=num(c.get("r6_bag_size"))
    if ps in INFORMED: return 0
    if (ps in ("no_gps","pre_shift","blind",None,"")) and bag==0: return 2
    return 1

def score_of(c): return num(c.get("score"))
def wait_pen(c): return num(c.get("bonus_v3273_wait_courier"))
def load_pen(c): return num(c.get("bonus_r8_soft_pen"))+num(c.get("bonus_r9_stopover"))+num(c.get("v326_fleet_load_adjustment"))
def cf_score(c, wx=1.0, lx=1.0):
    return score_of(c) + (wx-1.0)*wait_pen(c) + (lx-1.0)*load_pen(c)
# objective metrics
def m_r6(c): return num(c.get("objm_r6_breach_max_min"))
def m_new(c): return num(c.get("new_pickup_late_min"))
def m_com(c): return num(c.get("late_pickup_committed_max"))
def m_waste(c): return num(c.get("v3273_wait_courier_max_min"))
def obj_cost(c): return m_r6(c)+m_com(c)+0.5*max(0.0,m_new(c)-5.0)

def load_decisions():
    out=[]
    for path in (SHADOW, ROT):
        with open(path) as f:
            for line in f:
                if not line.strip(): continue
                try: r=json.loads(line)
                except: continue
                ts=r.get("ts")
                if not inwin(ts): continue
                if path==ROT and wday(ts)!="2026-06-10": continue   # .1 only for 06-10 (rest in current)
                best=r.get("best") or {}
                if "score" not in best: continue                    # skip stub/KOORD-minimal
                oid=str(r.get("order_id"))
                try: e2 = int(oid)%5==0
                except: e2=False
                cands=[best]+[a for a in (r.get("alternatives") or []) if "score" in a]
                out.append({"oid":oid,"ts":ts,"e2":e2,"best":best,"cands":cands,"verdict":r.get("verdict")})
    return out

def eligible(d):
    best=d["best"]; tb=(tier_rank(best),bucket_rank(best))
    return [c for c in d["cands"] if (tier_rank(c),bucket_rank(c))==tb]

def pick(group, keyfn, maximize=True):
    if not group: return None
    return (max if maximize else min)(group, key=keyfn)

def run():
    decs=load_decisions()
    full=[d for d in decs if not d["e2"]]
    print(f"decisions in-window: {len(decs)} ; non-E2 with full best: {len(full)}")
    # faithfulness: baseline argmax-score-in-group == logged best?
    match=0; evaln=0
    for d in full:
        g=eligible(d)
        bp=pick(g, score_of, True)
        if bp is None: continue
        evaln+=1
        if str(bp.get("courier_id"))==str(d["best"].get("courier_id")): match+=1
    print(f"faithfulness (baseline-rerank == logged best): {match}/{evaln} = {100*match/max(1,evaln):.1f}%\n")

    policies = {
        "A_baseline":      lambda g: pick(g, score_of, True),
        "P1a_wait_x0":     lambda g: pick(g, lambda c: cf_score(c, wx=0.0), True),
        "P1a_wait_x0.5":   lambda g: pick(g, lambda c: cf_score(c, wx=0.5), True),
        "P1b_load_x0":     lambda g: pick(g, lambda c: cf_score(c, lx=0.0), True),
        "P1b_load_x0.5":   lambda g: pick(g, lambda c: cf_score(c, lx=0.5), True),
        "P1ab_both_x0.5":  lambda g: pick(g, lambda c: cf_score(c, wx=0.5, lx=0.5), True),
        "C_pln_v":         lambda g: pick([c for c in g if c.get("pln_v") is not None], lambda c: num(c.get("pln_v")), True),
        "D_objm_obj":      lambda g: pick(g, obj_cost, False),
        "D2_objm_lexR6":   lambda g: pick(g, lambda c:(m_r6(c), m_com(c), m_new(c)), False),
        "D3_objm_lexCom":  lambda g: pick(g, lambda c:(m_com(c), m_r6(c), m_new(c)), False),
    }
    base_pick = {}
    print(f"{'policy':16s} {'n':>4} {'flips':>5} {'ΣΔR6breach':>10} {'ΣΔnew_late':>10} {'ΣΔcommit':>9} {'ΣΔwaste':>8} {'regr_R6':>7} {'R6saved/waste':>13}")
    for name, fn in policies.items():
        n=flips=regr=0; sR6=sNew=sCom=sW=0.0
        for d in full:
            g=eligible(d)
            base=pick(g, score_of, True)
            if base is None: continue
            p=fn(g)
            if p is None: continue
            n+=1
            if name=="A_baseline":
                base_pick[d["oid"]+d["ts"]]=base
                continue
            if str(p.get("courier_id"))!=str(base.get("courier_id")):
                flips+=1
                dR6=m_r6(p)-m_r6(base); dNew=m_new(p)-m_new(base); dCom=m_com(p)-m_com(base); dW=m_waste(p)-m_waste(base)
                sR6+=dR6; sNew+=dNew; sCom+=dCom; sW+=dW
                if dR6>1.0 or dCom>1.0: regr+=1     # flip that worsens a hard rule
        if name=="A_baseline":
            print(f"{name:16s} {n:>4} {'—':>5} {'—':>10} {'—':>10} {'—':>9} {'—':>8} {'—':>7} {'—':>13}")
            continue
        ratio = (-sR6)/sW if sW>0.01 else float('inf') if sR6<0 else 0
        print(f"{name:16s} {n:>4} {flips:>5} {sR6:>10.1f} {sNew:>10.1f} {sCom:>9.1f} {sW:>8.1f} {regr:>7} {ratio:>13.2f}")
    print("\nΔ konwencja: ujemne ΣΔR6breach/new_late/commit = POPRAWA (mniej min spóźnień); dodatnie ΣΔwaste = koszt (więcej idle).")
    print("R6saved/waste = ile min R6-breach zaoszczędzono na 1 min dodanego idle (wyżej=lepiej; inf=poprawa bez kosztu idle).")
    print("regr_R6 = liczba flipów które POGARSZAJĄ R6 lub committed >1min (nad-korekta).")

if __name__=="__main__":
    run()
