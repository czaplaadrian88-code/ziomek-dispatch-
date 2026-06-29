#!/usr/bin/env python3
"""
N1 SELECTION-REGRESSION ANALYSIS (READ-ONLY) — 2026-06-17, shadow_decisions ground truth.

N1 only changes a candidate's resolved position, and (verified by n1_regr_validate +
n1_plan_replay) the change moves an affected courier CLOSER to its committed pickup ->
its travel/ETA/wait improve -> its SCORE can only rise or stay (never fall). So:

  * If the logged WINNER is N1-affectable -> it improves -> still wins. NO flip.
  * A flip can occur ONLY if a NON-winning affected candidate rises past the winner.
    The flip target is a courier whose plan N1 reveals as MORE on-time for its committed
    pickup (lower committed lateness) -> not a quality regression by construction, BUT we
    bound how often it could happen and inspect the winner-vs-affected-runnerup margin.

This script measures, per decision (14-16.06):
  - winner pos_source / score / committed-late / new-order-late
  - among NON-winning alternatives that are N1-affectable (pos_source=last_assigned_pickup,
    bag_size_before>=2): the score gap to the winner (winner_score - alt_score).
    A flip is *possible* only if N1's score uplift for that alt could exceed the gap.
  - We DO NOT have the exact uplift here (needs coords); we report the gap distribution so
    the plausible-flip envelope is explicit, and flag any decision where an affected
    non-winner is already within a small gap of the winner (flip-risk shortlist).

Proposal-window filter: decision's new-order pickup readiness <= WINDOW_MAX_MIN.
"""
import json, sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict, Counter

SHADOW="/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
DAYS=("2026-06-14","2026-06-15","2026-06-16")
WINDOW_MAX_MIN=65.0
GAP_SHORTLIST=40.0   # affected non-winner within this score gap of winner => inspect

def pts(s):
    if not s: return None
    try: return datetime.fromisoformat(str(s).replace("Z","+00:00")).astimezone(timezone.utc)
    except: return None
def wday(ts):
    d=pts(ts); return (d+timedelta(hours=2)).strftime("%Y-%m-%d") if d else None
def num(x,dv=0.0):
    try: return float(x) if x is not None else dv
    except: return dv

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
def is_sentinel(c):
    return num(c.get("score"),0.0) < -1e6   # hard-reject / sentinel score

def affectable(c):
    return (c.get("pos_source")=="last_assigned_pickup") and ((c.get("bag_size_before") or 0)>=2)

def main():
    per_day=defaultdict(lambda: dict(dec=0, win_out=0, qual=0,
        winner_affectable=0, has_affect_runnerup=0, shortlist=[], gaps=[]))
    for line in open(SHADOW):
        try: d=json.loads(line)
        except: continue
        day=wday(d.get("ts"))
        if day not in DAYS: continue
        P=per_day[day]; P["dec"]+=1
        # proposal window on the dispatched order
        now=pts(d.get("ts")); ready=pts(d.get("pickup_ready_at"))
        if now and ready and (ready-now).total_seconds()/60.0 > WINDOW_MAX_MIN:
            P["win_out"]+=1; continue
        P["qual"]+=1
        b=d.get("best") or {}
        if d.get("verdict")!="PROPOSE":  # only proposals are real selections
            pass
        wscore=num(b.get("score"))
        if affectable(b):
            P["winner_affectable"]+=1
        # Real selection group: candidates in winner's (tier_rank, bucket_rank), non-sentinel.
        wtb=(tier_rank(b), bucket_rank(b))
        alts=d.get("alternatives") or []
        # affected NON-winners that compete in the SAME tier/bucket as the winner
        aff_alts=[a for a in alts
                  if affectable(a) and not is_sentinel(a)
                  and (tier_rank(a),bucket_rank(a))==wtb]
        if aff_alts:
            P["has_affect_runnerup"]+=1
            for a in aff_alts:
                gap=wscore-num(a.get("score"))   # >0 winner ahead; <=0 alt already outscores (yet not picked => tie-break/other)
                P["gaps"].append(gap)
                if 0<=gap<=GAP_SHORTLIST:
                    P["shortlist"].append(dict(oid=d.get("order_id"), day=day,
                        winner_cid=b.get("courier_id"), winner_ps=b.get("pos_source"),
                        winner_score=round(wscore,1),
                        alt_cid=a.get("courier_id"), alt_score=round(num(a.get("score")),1),
                        gap=round(gap,1),
                        alt_committed_late=round(num(a.get("late_pickup_committed_max")),1),
                        win_committed_late=round(num(b.get("late_pickup_committed_max")),1),
                        alt_new_late=round(num(a.get("new_pickup_late_min")),1),
                        win_new_late=round(num(b.get("new_pickup_late_min")),1)))

    print("="*100)
    print("N1 SELECTION-REGRESSION ENVELOPE — shadow_decisions, per Warsaw-day, after proposal-window filter")
    print("(N1 can only RAISE an affected candidate's score; flip possible only if affected NON-winner overtakes winner)")
    print("="*100)
    print(f"{'day':<11}{'decisions':>10}{'win_out':>9}{'qualif':>8}{'winnerAffect':>13}{'hasAffRunnerup':>15}{'shortlist(<=40gap)':>19}")
    T=defaultdict(int); Tshort=[]; Tgaps=[]
    for day in DAYS:
        P=per_day[day]
        print(f"{day:<11}{P['dec']:>10}{P['win_out']:>9}{P['qual']:>8}{P['winner_affectable']:>13}{P['has_affect_runnerup']:>15}{len(P['shortlist']):>19}")
        for k in ("dec","win_out","qual","winner_affectable","has_affect_runnerup"): T[k]+=P[k]
        Tshort+=P["shortlist"]; Tgaps+=P["gaps"]
    print("-"*100)
    print(f"{'TOTAL':<11}{T['dec']:>10}{T['win_out']:>9}{T['qual']:>8}{T['winner_affectable']:>13}{T['has_affect_runnerup']:>15}{len(Tshort):>19}")

    print("\n--- INTERPRETATION ---")
    print(f"  winner already N1-affectable (improves, STAYS winner, no flip, plan more honest): {T['winner_affectable']}")
    print(f"  decisions with an affected NON-winner (flip-eligible population): {T['has_affect_runnerup']}")
    if Tgaps:
        Tgaps.sort()
        print(f"  affected-non-winner score gap to winner: min={round(min(Tgaps),1)}, median={round(Tgaps[len(Tgaps)//2],1)}, "
              f"<=10:{sum(1 for g in Tgaps if g<=10)}, <=40:{sum(1 for g in Tgaps if g<=40)}, n={len(Tgaps)}")
    if Tshort:
        print(f"\n--- FLIP-RISK SHORTLIST (affected non-winner within {GAP_SHORTLIST} score of winner) ---")
        print("    (committed/new late shown to judge if a flip would be toward BETTER or WORSE committed timeliness)")
        for r in sorted(Tshort,key=lambda x:x['gap'])[:40]:
            verdict="BETTER-committed" if r['alt_committed_late']<r['win_committed_late'] else ("EQUAL" if r['alt_committed_late']==r['win_committed_late'] else "WORSE-committed")
            print(f"  {r['day']} oid={r['oid']} winner cid={r['winner_cid']}({r['winner_ps']},s={r['winner_score']},cl={r['win_committed_late']},nl={r['win_new_late']}) "
                  f"| affAlt cid={r['alt_cid']}(s={r['alt_score']},cl={r['alt_committed_late']},nl={r['alt_new_late']}) gap={r['gap']} -> {verdict}")
    else:
        print("\n  FLIP-RISK SHORTLIST: empty (no affected non-winner within small score gap of winner).")

if __name__=="__main__":
    main()
