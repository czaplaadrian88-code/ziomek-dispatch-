#!/usr/bin/env python3
"""Emit markdown-ready tables for the final report from dominated_cases.json."""
import json, sys
sys.path.insert(0,"/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")
import audit_lib as L
dom=json.load(open(f"{L.SCRATCH}/dominated_cases.json"))
def num(x):
    try: return float(x)
    except: return None

real=[c for c in dom if not c["is_artifact"] and str(c["verdict"]).upper()!="KOORD"]
print("### Ranking TOP-25 zdominowanych propozycji (06-11..16; sev = waga dowodu)\n")
print("| oid | dz | restauracja | bucket | E2 | best→dom | dR6 | dCommit | dNew | best_score_top? | outcome a2d |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
shown=0
for c in sorted(real, key=lambda r:-r["_sev"]):
    td=c["top_dominator"]; de=td["deltas"]
    e2="✓" if c["e2_sig"] else ""
    st="score-top" if c["best_is_score_top"] else "override"
    a2d=c.get("outcome_assign_to_delivery_min")
    a2d=f"{a2d:.0f}" if isinstance(a2d,(int,float)) else "—"
    rest=str(c["restaurant"])[:14]
    print(f"| {c['order_id']} | {c['day'][-2:]} | {rest} | {c['bucket'].replace('PROPOSE_','').replace('_',' ')} | {e2} | {c['best_cid']}→{td['cid']} | {de['d_r6_max_bag']:.1f} | {de['d_committed_max']:.1f} | {de['d_new_pickup_late']:.1f} | {st} | {a2d} |")
    shown+=1
    if shown>=25: break

# committed-avoidable detail (the R-DECLARED-TIME core)
print("\n### Committed-breach UNIKALNY (R-DECLARED-TIME) — best łamie cudzy odbiór, a alternatywa nie\n")
print("| oid | dz | E2 | best→dom | dCommit(min) | best łamie? | dom łamie? |")
print("|---|---|---|---|---|---|---|")
ca=[c for c in dom if c["bucket"]=="PROPOSE_committed_avoidable"]
for c in sorted(ca,key=lambda r:-num(r['top_dominator']['deltas']['d_committed_max'])):
    td=c["top_dominator"]; de=td["deltas"]
    print(f"| {c['order_id']} | {c['day'][-2:]} | {'✓' if c['e2_sig'] else ''} | {c['best_cid']}→{td['cid']} | {de['d_committed_max']:.1f} | {c.get('best_committed_breach')} | {num(td.get('committed_max') or 0)>0.5} |")
print(f"\nΣ committed-min unikalnych: {sum(num(c['top_dominator']['deltas']['d_committed_max']) or 0 for c in ca):.0f}")
