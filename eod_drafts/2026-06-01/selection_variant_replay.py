#!/usr/bin/env python3
"""Selekcja przeciw-kierunkowa — faithful re-ranking na dzisiejszych pulach.

Read-only. Dla każdej decyzji bierze logowaną pulę konkurencyjną (best+alternatives,
feasible) z PEŁNYM rozbiciem score. Zmienia TYLKO składnik kierunkowy (R1 corridor +
progressive) wg wariantu, przelicza adj_score, i wybiera zwycięzcę pod 3 modelami:
  M_score  — czysty argmax adj_score (idealny, gdyby selekcja była score-first)
  M_bucket — (bucket informed>other>blind, potem adj_score) — wierniejszy klucz selekcji
  live     — faktyczny logowany zwycięzca (best)
Dekompozycja: live≠M_score = override (tier/bucket); live≠M_bucket = reszta (tier).

Składnik kierunkowy faithful: legacy clip (-35/-40) × spread_mult + progressive (-45/-60/-100),
flagi jak prod (gradient OFF, progressive ON). dir_old = logged (corridor+progressive),
więc S0 = tożsamość (walidacja).
"""
import json, sys, statistics
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

SHADOW = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
INFORMED = ("gps","last_assigned_pickup","last_picked_up_delivery","last_picked_up_recent",
            "last_delivered","post_wave","last_picked_up_pickup")
BLIND = ("no_gps","pre_shift","none")

# ── faithful directionality (legacy clip, gradient OFF) ──
def _base(cos):
    if cos is None: return 0.0
    if cos > 0.85: return 20.0
    if cos > 0.5: return 5.0
    if cos > 0.0: return 0.0
    if cos > -0.5: return -35.0
    return -40.0

def _spread_mult(spread):
    if spread is None or spread <= 8.0: return 1.0
    return min(2.0, 1.0 + (spread - 8.0) * 0.125)

def _progressive_target(cos):
    if cos is None: return None
    if cos < -0.7: return -100.0
    if cos < -0.5: return -60.0
    if cos < -0.3: return -45.0
    return None

def dir_total(cos, spread, dist, variant):
    """Pełny wkład kierunkowy (corridor+progressive) wg wariantu."""
    base = _base(cos)
    # spread mult tylko po stronie ujemnej (jak prod)
    if base < 0:
        base *= _spread_mult(spread)
    # S2/S3: mocniejsza strona ujemna (base legacy ×1.25)
    if variant in ("S2","S3") and base < 0:
        base *= 1.25
    # S1/S3: spread-aware po stronie DODATNIEJ (luka 477752: +20 mimo 13km)
    if variant in ("S1","S3","S5") and base > 0 and spread is not None and spread > 8.0:
        damp = max(0.0, 1.0 - (spread - 8.0) * 0.12)
        base *= damp
        if spread > 12.0:
            base -= (spread - 12.0) * 3.0   # daleki rozrzut → realna kara mimo „tego samego azymutu"
    # progressive (cos<-0.3)
    tgt = _progressive_target(cos)
    total = base
    if tgt is not None:
        if variant in ("S2","S3"):
            tgt *= 1.5   # -150/-90/-67.5
        total = min(base, tgt)   # prod: base + min(tgt-base,0)
    # S4/S5: kara za odległość NOWEGO dropu od korytarza (cosine bywa myląca)
    if variant in ("S4","S5") and dist is not None and dist > 3.0:
        total -= min(40.0, (dist - 3.0) * 6.0)
    return total

VARIANTS = ["S0","S1","S2","S3","S4","S5"]
VDESC = {
 "S0":"baseline (live)", "S1":"spread-aware bonus (+strona)", "S2":"mocniejsza kara ujemna ×1.5",
 "S3":"S1+S2", "S4":"kara za new_drop_dist", "S5":"S1+S4 (spread-aware + dist)"}

def bucket(pos, bag):
    if pos in INFORMED: return 0
    if (pos in BLIND and (bag or 0) == 0) or pos == "pre_shift": return 2
    return 1

def feas(c):
    return (c.get("feasibility") in ("MAYBE","YES")) or c.get("best_effort")

def cand_dir_old(c):
    return float(c.get("bonus_r1_corridor") or 0.0) + float(c.get("bonus_r1_progressive_shadow_delta") or 0.0)

def adj(c, variant):
    cos=c.get("r1_avg_pairwise_cosine"); sp=c.get("deliv_spread_km"); dist=c.get("r1_new_drop_dist_km")
    s=float(c.get("score") or 0.0)
    if variant=="S0":
        return s
    return s - cand_dir_old(c) + dir_total(cos, sp, dist, variant)

def winner(pool, variant, model):
    cs=[(c, adj(c,variant)) for c in pool]
    if model=="score":
        return max(cs, key=lambda x: x[1])[0]
    # bucket model
    return min(cs, key=lambda x: (bucket(x[0].get("pos_source"), x[0].get("r6_bag_size")), -x[1]))[0]

def is_cross(c, thr=-0.3):
    cos=c.get("r1_avg_pairwise_cosine")
    return cos is not None and cos < thr

def main():
    rows=[]
    with open(SHADOW) as f:
        for line in f:
            if '"2026-06-01' not in line[:40]: continue
            d=json.loads(line)
            pool=[d["best"]]+[a for a in (d.get("alternatives") or [])]
            pool=[c for c in pool if feas(c)]
            if not pool: continue
            rows.append((d, pool, d["best"]))
    print(f"decyzje z feasible pulą: {len(rows)}  (mediana puli {statistics.median(len(p) for _,p,_ in rows):.0f})")

    # FIDELITY + DEKOMPOZYCJA (S0)
    same_score=same_bucket=0; live_cross=0
    fixable_by_score=fixable_by_bucket=0
    for d,pool,best in rows:
        ws=winner(pool,"S0","score"); wb=winner(pool,"S0","bucket")
        if str(ws.get("courier_id"))==str(best.get("courier_id")): same_score+=1
        if str(wb.get("courier_id"))==str(best.get("courier_id")): same_bucket+=1
    print(f"\nFIDELITY S0: live==M_score {same_score}/{len(rows)} ({100*same_score/len(rows):.0f}%) | live==M_bucket {same_bucket}/{len(rows)} ({100*same_bucket/len(rows):.0f}%)")
    print("  (różnica live vs M_score = override tier/bucket; live vs M_bucket = sam tier)")

    # cross-direction live winners — przyczyna
    cd=[(d,pool,best) for d,pool,best in rows if is_cross(best,-0.3)]
    print(f"\nCROSS-DIR live winners (cos<-0.3): {len(cd)}")
    won_on_score=tier_override=only_option=0
    for d,pool,best in cd:
        non_cross=[c for c in pool if not is_cross(c,-0.3) and str(c.get('courier_id'))!=str(best.get('courier_id'))]
        ws=winner(pool,"S0","score")
        if not non_cross:
            only_option+=1
        elif str(ws.get("courier_id"))==str(best.get("courier_id")):
            won_on_score+=1   # cross wygrał też na score → kara kierunku za słaba
        else:
            tier_override+=1  # lepszy (score) ne-cross istniał, przegrał → tier/bucket override
    print(f"   wygrał TEŻ na score (kara kierunku za słaba → S1/S2/S4 pomogą): {won_on_score}")
    print(f"   przegrał lepszy-score nie-cross (TIER/BUCKET override → S_select): {tier_override}")
    print(f"   brak nie-cross alternatywy w puli (scarcity floty): {only_option}")

    # WARIANTY: cross-direction rate zwycięzcy
    print("\n" + "="*104)
    print(f"{'wariant':28s} | {'M_score: cos<-.3 / <-.7 / sp>8':32s} | {'M_bucket: cos<-.3 / <-.7 / sp>8':32s}")
    print("-"*104)
    def rate(rows, variant, model, pred):
        n=0;k=0
        for d,pool,best in rows:
            w=winner(pool,variant,model); n+=1
            if pred(w): k+=1
        return 100*k/n
    for v in VARIANTS:
        ms_c3=rate(rows,v,"score",lambda c:is_cross(c,-0.3)); ms_c7=rate(rows,v,"score",lambda c:is_cross(c,-0.7))
        ms_sp=rate(rows,v,"score",lambda c:(c.get("deliv_spread_km") or 0)>8)
        mb_c3=rate(rows,v,"bucket",lambda c:is_cross(c,-0.3)); mb_c7=rate(rows,v,"bucket",lambda c:is_cross(c,-0.7))
        mb_sp=rate(rows,v,"bucket",lambda c:(c.get("deliv_spread_km") or 0)>8)
        print(f"{v+' '+VDESC[v]:28s} | {ms_c3:5.1f}% / {ms_c7:5.1f}% / {ms_sp:5.1f}%          | {mb_c3:5.1f}% / {mb_c7:5.1f}% / {mb_sp:5.1f}%")
    print("="*104)
    print("(M_score = idealny score-first; M_bucket = z bucketem informed>other>blind = wierniejszy klucz)")

    # ── WARIANTY KLUCZA SELEKCJI (veto kierunkowe na override) ──
    # baseline selekcji = M_bucket (najwierniejszy dostępny model). Veto: jeśli
    # zwycięzca M_bucket jest mocno-cross (cos<thr_block) a w puli jest feasible
    # nie-cross (cos>thr_ok) → wybierz najlepszy-score nie-cross (= „odrocz odbiór,
    # nie łam kierunku"). Warianty progu + „tylko do informed".
    def winner_veto(pool, thr_block, thr_ok, informed_only):
        base=winner(pool,"S0","bucket")
        bc=base.get("r1_avg_pairwise_cosine")
        if bc is None or bc >= thr_block:
            return base, False
        alts=[c for c in pool
              if (c.get("r1_avg_pairwise_cosine") is None or c.get("r1_avg_pairwise_cosine") > thr_ok)
              and str(c.get("courier_id"))!=str(base.get("courier_id"))]
        if informed_only:
            alts=[c for c in alts if c.get("pos_source") in INFORMED]
        if not alts:
            return base, False
        best_alt=max(alts, key=lambda c: float(c.get("score") or 0.0))
        return best_alt, True

    print("\nKLUCZ SELEKCJI — veto kierunkowe (baseline=M_bucket; mierzymy cross-dir zwycięzcy + #flipów + typ flipu)")
    print("="*104)
    print(f"{'wariant selekcji':40s} | {'cos<-.3':7s} {'cos<-.5':7s} {'cos<-.7':7s} | {'flipy':6s} {'→pusty':7s} {'→bag-aligned':12s}")
    print("-"*104)
    SEL=[("baseline M_bucket",None,None,False),
         ("veto cos<-.5 → nie-cross(any)",-0.5,-0.1,False),
         ("veto cos<-.5 → nie-cross(informed)",-0.5,-0.1,True),
         ("veto cos<-.7 → nie-cross(any)",-0.7,-0.1,False)]
    for name,tb,to,inf in SEL:
        c3=c5=c7=flips=to_empty=to_bag=0; n=0
        for d,pool,best in rows:
            n+=1
            if tb is None:
                w=winner(pool,"S0","bucket"); fl=False
            else:
                w,fl=winner_veto(pool,tb,to,inf)
            if is_cross(w,-0.3): c3+=1
            if is_cross(w,-0.5): c5+=1
            if is_cross(w,-0.7): c7+=1
            if fl:
                flips+=1
                if (w.get("r6_bag_size") or 0)==0: to_empty+=1
                else: to_bag+=1
        print(f"{name:40s} | {100*c3/n:5.1f}% {100*c5/n:5.1f}% {100*c7/n:5.1f}% | {flips:6d} {to_empty:7d} {to_bag:12d}")
    print("="*104)

if __name__=="__main__":
    main()
