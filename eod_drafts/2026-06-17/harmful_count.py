#!/usr/bin/env python3
"""Full 7-day count: harmful Ziomek proposals + how many D2 fixes on the LIVE-equivalent pool
(post hard-reject). Harmful = best predicts R6 breach (>35) or committed breach. D2 = objm
breach-primary lexicographic within best's tier+bucket group, EXCLUDING hard-rejected candidates."""
import sys
sys.path.insert(0,"/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")
import replay_harness_p1 as H

HARM_R6=2.0      # objm_r6_breach_max_min > 2 = real R6 breach (delivery >37min)
HARM_COM=2.0     # late_pickup_committed_max > 2 = real committed lateness
IMP=1.0          # min improvement to count as a fix
def n(x,d=0.0): return H.num(x,d)

def hard_rejected(c):
    if n(c.get("score"),0.0) <= -1e6: return True          # NEG_INF sentinel
    if c.get("v326_wave_veto") is True: return True
    for k in ("carry_chain_hard_reject","intra_rest_gap_hard_reject",
              "v3273_wait_courier_hard_reject","r6_picked_up_delta_reject",
              "v319h_bug4_cap_violation"):
        if c.get(k) is True: return True
    return False

def clean_group(d):
    best=d["best"]; tb=(H.tier_rank(best),H.bucket_rank(best))
    g=[c for c in d["cands"] if (H.tier_rank(c),H.bucket_rank(c))==tb]
    g=[c for c in g if c is best or not hard_rejected(c)]   # keep live winner, drop hard-rejects
    return g
def lex(c):  # breach-primary
    r6=c.get("objm_r6_breach_max_min")
    return (n(r6,9e9) if r6 is not None else 9e9, n(c.get("late_pickup_committed_max")), n(c.get("new_pickup_late_min")))

decs=H.load_decisions()
def harmful(best):
    return H.m_r6(best)>HARM_R6 or H.m_com(best)>HARM_COM

tot=len(decs); harm=0; fix=0; fix_e2=0; fix_non=0; unfix=0
sR6=sCom=sNew=sW=0.0; fix_feas=0
harm_e2=0
for d in decs:
    best=d["best"]
    if not harmful(best): continue
    harm+=1
    if d["e2"]: harm_e2+=1
    g=clean_group(d)
    if not g: unfix+=1; continue
    p=min(g,key=lex)
    if str(p.get("courier_id"))==str(best.get("courier_id")):
        unfix+=1; continue
    # D2 must STRICTLY reduce harm without adding harm on the other hard axis
    dR6=H.m_r6(p)-H.m_r6(best); dCom=H.m_com(p)-H.m_com(best)
    reduces = (dR6 < -IMP) or (dCom < -IMP)
    no_new_harm = (dR6 <= IMP) and (dCom <= IMP)
    if reduces and no_new_harm:
        fix+=1
        if d["e2"]: fix_e2+=1
        else: fix_non+=1
        sR6+=dR6; sCom+=dCom; sNew+=H.m_new(p)-H.m_new(best); sW+=H.m_waste(p)-H.m_waste(best)
        if str(p.get("feasibility","")).upper() in ("YES","MAYBE"): fix_feas+=1
    else:
        unfix+=1

print(f"=== PEŁNE OKNO 7d (2026-06-10..16) — {tot} decyzji z pełnym best ===\n")
print(f"PROPOZYCJE SZKODLIWE (best: R6-breach>{HARM_R6}min LUB committed>{HARM_COM}min): {harm}  ({100*harm/tot:.1f}% decyzji)")
print(f"  z tego E2-signature (oid%5==0, naprawiane przez Fix B+C): {harm_e2}\n")
print(f"D2 NAPRAWIA (wybiera ściśle mniej-szkodliwego na czystej puli, bez nowej szkody): {fix}")
print(f"   ├─ non-E2 (D2 jest fixem): {fix_non}")
print(f"   └─ E2 (już pokryte Fix B+C live; D2 też by je złapał): {fix_e2}")
print(f"   z {fix} fixów: {fix_feas} bierze kuriera FEASIBLE (YES/MAYBE), reszta = mniej-zły w nasyceniu")
print(f"D2 NIE naprawia (brak lepszego w czystej puli = realna saturacja/least-bad): {unfix}")
print(f"\nMINUTY ODZYSKANE na zbiorze naprawionym ({fix}):")
print(f"   R6-breach: {sR6:.0f} min   committed: {sCom:.0f} min   (Σ twardych reguł: {sR6+sCom:.0f} min mniej spóźnień)")
print(f"   KOSZT: new-pickup-late +{sNew:.0f} min, idle +{sW:.0f} min")
print(f"\nINTERPRETACJA: realnie naprawiamy {fix_non} szkodliwych propozycji/tydzień przez D2 (non-E2),")
print(f"+ {fix_e2} E2-szkodliwych już domknięte przez B+C. {unfix} szkodliwych = saturacja (nic w selekcji nie pomoże, tylko więcej floty).")
