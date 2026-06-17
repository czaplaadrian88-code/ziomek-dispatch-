#!/usr/bin/env python3
"""
E7 SAFE-REPLAY — bundle_fit/fix_c flip + prep-bias readiness. READ-ONLY.

Reużywa wiernościowej maszyny replay_harness_p1.py (96.9% wierności bazowej
argmax-w-grupie == zalogowany best). Dodaje politykę BUNDLE-06:
  E_bundle   = argmax(score + bonus_bundle_fit_shadow_delta)            [ENABLE_BUNDLE_VALUE_SCORING]
  E_fixc     = argmax(score + fix_c_additive_pen_shadow)                [ENABLE_FIX_C_ADDITIVE_PENALTY]
  E_both     = argmax(score + oba)                                      [oba flagi ON]
Metryki celu liczone jak w harnessie (lower=better, minuty):
  R6_breach=objm_r6_breach_max_min ; new_late=new_pickup_late_min ;
  committed=late_pickup_committed_max ; waste=v3273_wait_courier_max_min (idle).
Wyklucza E2 (order_id%5==0, okno sprzed fixa). Okno: Warsaw 06-10..06-17.

Prep-bias: NIE da się zrobić re-rank replay — `prep_bias_min` w shadow_decisions=0
na wszystkich kandydatach (shadow nie wstrzykuje tabeli). Sekcja prep-bias liczy
gotowość danych: pokrycie świeżego sygnału (ready_at_log) i magnitudę tabeli.
"""
import json, statistics
from datetime import datetime, timedelta
from collections import defaultdict, Counter

SHADOW="/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
ROT="/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1"
READY="/root/.openclaw/workspace/dispatch_state/ready_at_log.jsonl"
PREPTBL="/root/.openclaw/workspace/dispatch_state/restaurant_prep_bias.json"

def wday(ts):
    try: d=datetime.fromisoformat(str(ts).replace("Z","+00:00")); return (d+timedelta(hours=2)).strftime("%Y-%m-%d")
    except: return None
def inwin(ts):
    d=wday(ts); return bool(d and "2026-06-10"<=d<="2026-06-17")
def num(x,d=0.0):
    try: return float(x) if x is not None else d
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
def b_delta(c): return num(c.get("bonus_bundle_fit_shadow_delta"))
def fc_delta(c): return num(c.get("fix_c_additive_pen_shadow"))
def m_r6(c): return num(c.get("objm_r6_breach_max_min"))
def m_new(c): return num(c.get("new_pickup_late_min"))
def m_com(c): return num(c.get("late_pickup_committed_max"))
def m_waste(c): return num(c.get("v3273_wait_courier_max_min"))

def load_decisions():
    out=[]
    for path in (SHADOW, ROT):
        try: f=open(path)
        except: continue
        for line in f:
            if not line.strip(): continue
            try: r=json.loads(line)
            except: continue
            ts=r.get("ts")
            if not inwin(ts): continue
            if path==ROT and wday(ts)!="2026-06-10": continue
            best=r.get("best") or {}
            if "score" not in best: continue
            oid=str(r.get("order_id"))
            try: e2=int(oid)%5==0
            except: e2=False
            cands=[best]+[a for a in (r.get("alternatives") or []) if "score" in a]
            out.append({"oid":oid,"ts":ts,"e2":e2,"best":best,"cands":cands})
        f.close()
    return out

def eligible(d):
    best=d["best"]; tb=(tier_rank(best),bucket_rank(best))
    return [c for c in d["cands"] if (tier_rank(c),bucket_rank(c))==tb]
def pick(group, keyfn, maximize=True):
    if not group: return None
    return (max if maximize else min)(group, key=keyfn)

def run_bundle():
    decs=load_decisions()
    full=[d for d in decs if not d["e2"]]
    print(f"[BUNDLE-06 / FIX-C replay]  decisions in-window={len(decs)} ; non-E2={len(full)}")
    # faithfulness
    match=ev=0
    for d in full:
        g=eligible(d); bp=pick(g, score_of, True)
        if bp is None: continue
        ev+=1
        if str(bp.get("courier_id"))==str(d["best"].get("courier_id")): match+=1
    print(f"wierność (baseline rerank == logged best): {match}/{ev} = {100*match/max(1,ev):.1f}%")
    # how many decisions even have a nonzero bundle/fixc spread within the eligible group?
    has_b=has_fc=0
    for d in full:
        g=eligible(d)
        if len({round(b_delta(c),3) for c in g})>1: has_b+=1
        if len({round(fc_delta(c),3) for c in g})>1: has_fc+=1
    print(f"decyzje z NIEzerowym rozrzutem bundle-delta w grupie: {has_b} ; fix_c: {has_fc}\n")
    policies={
        "A_baseline":  lambda g: pick(g, score_of, True),
        "E_bundle":    lambda g: pick(g, lambda c: score_of(c)+b_delta(c), True),
        "E_fixc":      lambda g: pick(g, lambda c: score_of(c)+fc_delta(c), True),
        "E_both":      lambda g: pick(g, lambda c: score_of(c)+b_delta(c)+fc_delta(c), True),
    }
    hdr=f"{'policy':12s} {'n':>4} {'flips':>5} {'ΣΔR6brch':>9} {'ΣΔnew':>7} {'ΣΔcommit':>8} {'ΣΔwaste':>8} {'regrR6':>6} {'improvR6':>8}"
    print(hdr)
    for name,fn in policies.items():
        n=flips=regr=improv=0; sR6=sNew=sCom=sW=0.0
        for d in full:
            g=eligible(d); base=pick(g, score_of, True)
            if base is None: continue
            p=fn(g)
            if p is None: continue
            n+=1
            if name=="A_baseline": continue
            if str(p.get("courier_id"))!=str(base.get("courier_id")):
                flips+=1
                dR6=m_r6(p)-m_r6(base); dNew=m_new(p)-m_new(base); dCom=m_com(p)-m_com(base); dW=m_waste(p)-m_waste(base)
                sR6+=dR6; sNew+=dNew; sCom+=dCom; sW+=dW
                if dR6>1.0 or dCom>1.0: regr+=1
                if dR6<-1.0 or dCom<-1.0: improv+=1
        if name=="A_baseline":
            print(f"{name:12s} {n:>4} {'—':>5} {'—':>9} {'—':>7} {'—':>8} {'—':>8} {'—':>6} {'—':>8}"); continue
        print(f"{name:12s} {n:>4} {flips:>5} {sR6:>9.1f} {sNew:>7.1f} {sCom:>8.1f} {sW:>8.1f} {regr:>6} {improv:>8}")
    print("\nΔ: ujemne ΣΔR6/new/commit = POPRAWA; dodatnie ΣΔwaste = koszt idle.")
    print("regrR6 = flipy pogarszające R6/committed >1min ; improvR6 = poprawiające >1min.\n")

def run_prep():
    print("="*70)
    print("[PREP-BIAS readiness]")
    # shadow injection check
    decs=load_decisions()
    nz=0; tot=0
    for d in decs:
        for c in d["cands"]:
            tot+=1
            if abs(num(c.get("prep_bias_min")))>0.001: nz+=1
    print(f"shadow_decisions: kandydaci z NIEzerowym prep_bias_min = {nz}/{tot}  → re-rank replay {'MOŻLIWY' if nz>0 else 'NIEMOŻLIWY (shadow nie wstrzykuje tabeli)'}")
    # fresh measured-prep signal
    vals=[]; basis=Counter()
    for l in open(READY):
        try: r=json.loads(l)
        except: continue
        basis[r.get("ready_basis")]+=1
        v=r.get("prep_bias_min")
        if v is not None: vals.append(float(v))
    real=basis.get("waited",0)+basis.get("ready_by_arrival",0)
    print(f"ready_at_log: n={sum(basis.values())} ; ready_basis={dict(basis)}")
    print(f"  realny pomiar prep (waited+ready_by_arrival) = {real} ({100*real/max(1,sum(basis.values())):.0f}%) ; brak sygnału = {basis.get('no_arrival_signal',0)}")
    if vals:
        print(f"  prep_bias_min świeży: min/med/mean/max = {min(vals):.1f}/{statistics.median(vals):.1f}/{statistics.mean(vals):.1f}/{max(vals):.1f}")
    # table magnitude
    t=json.load(open(PREPTBL))
    g=t.get("global",{})
    print(f"tabela: wygenerowana {t.get('generated_at')} ; window_days={t.get('window_days')} ; n_obs={t.get('n_observations','?')}")
    print(f"  global bias_med per okno: " + ", ".join(f"{k}={v.get('bias_med')}" for k,v in g.items()))
    hv=[r for r,wd in t.get("restaurants",{}).items() if any(w.get("high_variance") for w in wd.values())]
    print(f"  restauracje z high_variance (ryzyko): {hv}")

if __name__=="__main__":
    run_bundle()
    run_prep()
