#!/usr/bin/env python3
"""SEL-01 (Front D, audyt 03.06) — offline replay: kierunkowość w KLUCZU selekcji.

Pytanie: gdyby r1_avg_pairwise_cosine wszedł do klucza selekcji jako wymiar
PO tier-2 i PO bucket pos_source (czyli NIE nadpisując uzasadnień late-pickup/
scarcity, które pogrzebały SELECTION_VETO 08.06) — ile decyzji by się zmieniło
i jakim kosztem score?

Warianty:
  V1 dir-bucket:   klucz = (tier2, bucket, dir_bucket, -adj, rank)
                   dir_bucket = 1 gdy cos < THR (None → 0, brak danych nie karze)
  V2 dir-tiebreak: w obrębie (tier2, bucket): jeśli |adj(A)−adj(B)| ≤ DELTA
                   i B ma cos ≥ OK_THR a A cos < BAD_THR → B wygrywa.
                   (implementacja: adj kwantyzowany do koszyków DELTA, cos jako
                   tie-break w koszyku)

Rekonstruowany klucz LIVE = (tier2, bucket, -adj, rank) na tych samych danych —
flip = różnica między rekonstrukcjami (bias serializacji się znosi).

Dane: shadow_decisions.jsonl (+ .1) — best + alternatives = finalny top-16 live.
"""
import json
import sys
from collections import Counter

LOGS = [
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
]

INFORMED = {"gps", "last_assigned_pickup", "last_picked_up_delivery",
            "last_picked_up_recent", "last_delivered", "post_wave",
            "last_picked_up_pickup"}
BLIND = {"no_gps", "pre_shift", "none"}

FREE_MIN, COEFF, CAP = 5.0, 1.5, 60.0


def bucket_of(m):
    ps = m.get("pos_source")
    bsize = m.get("r6_bag_size") or m.get("bag_size_before") or m.get("r7_bag_size") or 0
    if ps in INFORMED:
        return 0
    if (ps in BLIND and (bsize or 0) == 0) or ps == "pre_shift":
        return 2
    return 1


def tier_of(m):
    if m.get("late_pickup_committed_breach"):
        return 2
    if m.get("new_pickup_needs_extension"):
        return 1
    return 0


def adj_of(m):
    s = m.get("score") or 0.0
    lm = m.get("new_pickup_late_min")
    pen = 0.0
    if isinstance(lm, (int, float)) and lm > FREE_MIN:
        pen = min(CAP, COEFF * (lm - FREE_MIN))
    return s - pen


def live_key(m, rank):
    return (1 if tier_of(m) == 2 else 0, bucket_of(m), -adj_of(m), rank)


def v1_key(m, rank, thr):
    cos = m.get("r1_avg_pairwise_cosine")
    dirb = 1 if (isinstance(cos, (int, float)) and cos < thr) else 0
    return (1 if tier_of(m) == 2 else 0, bucket_of(m), dirb, -adj_of(m), rank)


def v2_key(m, rank, delta, bad_thr):
    # kwantyzacja adj do koszyków DELTA; w koszyku najpierw nie-cross, potem adj
    cos = m.get("r1_avg_pairwise_cosine")
    dirb = 1 if (isinstance(cos, (int, float)) and cos < bad_thr) else 0
    q = -int(adj_of(m) // delta)
    return (1 if tier_of(m) == 2 else 0, bucket_of(m), q, dirb, -adj_of(m), rank)


def main():
    decisions = 0
    with_cos = 0
    live_cross_wins = Counter()  # thr -> count
    variants = {
        "V1_thr-0.3": lambda m, r: v1_key(m, r, -0.3),
        "V1_thr-0.5": lambda m, r: v1_key(m, r, -0.5),
        "V1_thr-0.7": lambda m, r: v1_key(m, r, -0.7),
        "V2_d10_thr-0.5": lambda m, r: v2_key(m, r, 10.0, -0.5),
        "V2_d15_thr-0.5": lambda m, r: v2_key(m, r, 15.0, -0.5),
        "V2_d10_thr-0.3": lambda m, r: v2_key(m, r, 10.0, -0.3),
    }
    flips = {k: [] for k in variants}
    recon_mismatch = 0

    for path in LOGS:
        try:
            fh = open(path)
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("verdict") != "PROPOSE":
                    continue
                best = d.get("best") or {}
                alts = d.get("alternatives") or []
                if not best or not alts:
                    continue
                pool = [best] + alts
                decisions += 1
                if any(isinstance(m.get("r1_avg_pairwise_cosine"), (int, float)) for m in pool):
                    with_cos += 1

                ranked = list(enumerate(pool))  # rank = pozycja live (0 = best)
                live_sorted = sorted(ranked, key=lambda t: live_key(t[1], t[0]))
                live_winner = live_sorted[0][1]
                # sanity: rekonstrukcja live klucza powinna oddać best
                if str(live_winner.get("courier_id")) != str(best.get("courier_id")):
                    recon_mismatch += 1
                    continue

                bcos = best.get("r1_avg_pairwise_cosine")
                for thr in (-0.3, -0.5, -0.7):
                    if isinstance(bcos, (int, float)) and bcos < thr:
                        live_cross_wins[thr] += 1

                for name, keyfn in variants.items():
                    vs = sorted(ranked, key=lambda t: keyfn(t[1], t[0]))
                    w = vs[0][1]
                    if str(w.get("courier_id")) != str(best.get("courier_id")):
                        flips[name].append({
                            "oid": d.get("order_id"),
                            "ts": d.get("ts"),
                            "old_cid": best.get("courier_id"),
                            "old_cos": bcos,
                            "old_score": best.get("score"),
                            "old_tier": tier_of(best),
                            "old_bucket": bucket_of(best),
                            "old_bag": best.get("bag_size_before"),
                            "new_cid": w.get("courier_id"),
                            "new_cos": w.get("r1_avg_pairwise_cosine"),
                            "new_score": w.get("score"),
                            "new_tier": tier_of(w),
                            "new_bucket": bucket_of(w),
                            "new_bag": w.get("bag_size_before"),
                            "score_cost": round((best.get("score") or 0.0) - (w.get("score") or 0.0), 1),
                            "new_km_to_pickup": w.get("km_to_pickup"),
                            "old_km_to_pickup": best.get("km_to_pickup"),
                        })

    print(f"PROPOSE z best+alternatives: {decisions} (z cosine w puli: {with_cos})")
    print(f"rekonstrukcja live-klucza nie oddała best: {recon_mismatch} "
          f"({recon_mismatch/max(1,decisions+recon_mismatch)*100:.1f}%) — wykluczone")
    print()
    print("Live zwycięzca cross-directional:")
    for thr in (-0.3, -0.5, -0.7):
        n = live_cross_wins[thr]
        print(f"  cos<{thr}: {n} ({n/max(1,decisions)*100:.1f}%)")
    print()
    for name in variants:
        fl = flips[name]
        n = len(fl)
        print(f"== {name}: {n} flipów ({n/max(1,decisions)*100:.2f}%)")
        if not fl:
            continue
        costs = sorted(f["score_cost"] for f in fl)
        med = costs[len(costs)//2]
        same_tier = sum(1 for f in fl if f["old_tier"] == f["new_tier"])
        same_bucket = sum(1 for f in fl if f["old_bucket"] == f["new_bucket"])
        to_informed = sum(1 for f in fl if f["new_bucket"] == 0)
        new_solo = sum(1 for f in fl if (f["new_bag"] or 0) == 0)
        improved = sum(1 for f in fl
                       if isinstance(f["new_cos"], (int, float)) and isinstance(f["old_cos"], (int, float))
                       and f["new_cos"] > f["old_cos"])
        print(f"   koszt score: mediana {med}, max {costs[-1]}, min {costs[0]}")
        print(f"   same-tier: {same_tier}/{n}, same-bucket: {same_bucket}/{n}, "
              f"nowy=informed: {to_informed}/{n}, nowy=pusty worek: {new_solo}/{n}, "
              f"cos poprawiony: {improved}/{n}")
    # dump szczegóły do JSON dla ręcznego przeglądu
    out = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-12/sel01_flips_detail.json"
    with open(out, "w") as f:
        json.dump(flips, f, indent=1, default=str)
    print(f"\nDetale flipów: {out}")


if __name__ == "__main__":
    main()
