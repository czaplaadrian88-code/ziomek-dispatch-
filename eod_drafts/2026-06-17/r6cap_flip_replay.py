#!/usr/bin/env python3
"""
R6-SOFT-PEN CAP — flip replay (READ-ONLY). Dobór progu capa + dowód 0-flipów.

Cap zmienia score: capped_score = score - raw_r6 + max(raw_r6, floor).
Re-rank w grupie tier+bucket (jak replay_harness_p1) → flip gdy argmax kandydat
się zmienia. floor=0 flipów = bezpieczny do flipu. Wyklucza E2 (order_id%5==0).
"""
import json
from datetime import datetime, timedelta

SH="/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
ROT="/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1"
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
def raw_r6(c): return num(c.get("bonus_r6_soft_pen"))

def load():
    out=[]
    for path in (SH,ROT):
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
            if e2: continue
            cands=[best]+[a for a in (r.get("alternatives") or []) if "score" in a]
            out.append({"best":best,"cands":cands})
        f.close()
    return out

def group(d):
    b=d["best"]; tb=(tier_rank(b),bucket_rank(b))
    return [c for c in d["cands"] if (tier_rank(c),bucket_rank(c))==tb]
def argmax(g, key): return max(g, key=key) if g else None

def run():
    decs=load()
    print(f"decyzje non-E2: {len(decs)}")
    # faithfulness baseline
    m=ev=0
    for d in decs:
        g=group(d); bp=argmax(g, score_of)
        if bp is None: continue
        ev+=1
        if str(bp.get("courier_id"))==str(d["best"].get("courier_id")): m+=1
    print(f"wierność baseline argmax==logged best: {m}/{ev} = {100*m/max(1,ev):.1f}%\n")
    print(f"{'floor':>8} {'capped_cands':>12} {'decyzje_z_cap':>13} {'FLIPY':>6} {'maxΔscore_pick':>14}")
    for floor in (-300.0,-500.0,-1000.0,-2000.0,-5000.0,-50000.0):
        flips=capped=decs_with=0; maxd=0.0
        for d in decs:
            g=group(d)
            base=argmax(g, score_of)
            if base is None: continue
            any_cap=False
            def capscore(c):
                r=raw_r6(c)
                if r<floor:
                    return score_of(c)-r+floor
                return score_of(c)
            for c in g:
                if raw_r6(c)<floor: capped+=1; any_cap=True
            if any_cap: decs_with+=1
            newp=argmax(g, capscore)
            if newp and str(newp.get("courier_id"))!=str(base.get("courier_id")):
                flips+=1
                maxd=max(maxd, abs(capscore(newp)-capscore(base)))
        print(f"{floor:>8.0f} {capped:>12} {decs_with:>13} {flips:>6} {maxd:>14.1f}")
    print("\nFLIPY=0 → cap bezpieczny do flipu (nie zmienia żadnego wyboru). Wybierz najgłębszy próg z FLIPY=0.")

if __name__=="__main__":
    run()
