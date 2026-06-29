#!/usr/bin/env python3
import json
from collections import Counter
W=json.load(open("/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17/wf_results.json"))
v=W["deepdive_verdicts"]
verify=W["verify"]

def oid_e2(o):
    try: return int(o)%5==0
    except: return False

# correct mislabels: cases whose culprit_axis names E2-pln but were labeled REAL -> E2
for x in v:
    ax=str(x.get("culprit_axis") or "")
    if x["classification"]=="score_miscalibration_REAL" and ("E2-pln" in ax or "_pln_pure_resort" in ax):
        x["classification"]="E2_fixed"; x["_corrected"]="E2"
# apply adversarial refutation: 480818 refuted 2/2
by={}
for r in verify: by.setdefault(str(r.get("order_id")),[]).append(r)
def verdict(oid):
    rs=by.get(str(oid)) or []
    if not rs: return None
    ref=sum(1 for r in rs if r.get("refuted")); holds=sum(1 for r in rs if r.get("better_option_holds"))
    if holds==0 and ref>0: return "REFUTED"
    if holds>ref: return "CONFIRMED"
    return "CONTESTED"
for x in v:
    vd=verdict(x["order_id"])
    if vd=="REFUTED" and x["classification"]=="score_miscalibration_REAL":
        x["classification"]="hidden_disqualifier"; x["_corrected"]="adversarial_refuted"
    elif vd: x["_verify"]=vd

cls=Counter(x["classification"] for x in v)
print("=== CORRECTED CLASSIFICATION (52 deep-dived) ===")
for k,n in cls.most_common(): print(f"  {k:28s} {n}")

real=[x for x in v if x["classification"]=="score_miscalibration_REAL"]
print(f"\n=== {len(real)} score_miscalibration_REAL (corrected) — by culprit axis ===")
axkey=[]
for x in real:
    ax=str(x.get("culprit_axis") or "")
    tag="wait_courier" if "v3273" in ax or "wait_courier" in ax else \
        "bag_load_r8r9" if ("r8" in ax or "r9" in ax or "bag-size" in ax) else \
        "new_courier_v325" if "v325" in ax or "new_courier" in ax else \
        "timing_gap" if "timing_gap" in ax else \
        "chain_bonus_r1_bundle" if ("r1_corridor" in ax or "bundle" in ax or "bonus_l2" in ax) else \
        "r5_detour" if "r5" in ax else \
        "wave_veto?" if "veto" in ax else "other"
    axkey.append(tag)
    vf=x.get("_verify","")
    print(f"  {x['order_id']} {x['best_cid']}->{x['dominator_cid']} dR6={x.get('d_r6')} dCom={x.get('d_committed')} conf={x.get('confidence')} {vf}  [{tag}] {ax[:55]}")
print("\n  axis histogram:", dict(Counter(axkey).most_common()))

print("\n=== verify outcomes (cases fully tested) ===")
for oid,rs in by.items():
    vd=verdict(oid)
    print(f"  {oid}: {vd} (holds={sum(1 for r in rs if r.get('better_option_holds'))}/{len(rs)} refuted={sum(1 for r in rs if r.get('refuted'))}/{len(rs)})")

for c in ("E2_fixed","saturation_leastbad","minor_noise","hidden_disqualifier","data_artifact"):
    ids=[x["order_id"] for x in v if x["classification"]==c]
    print(f"\n{c} ({len(ids)}): {ids}")
pr=[x["order_id"] for x in v if x.get("policy_review")]
print(f"\npolicy_review: {pr}")
