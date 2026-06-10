#!/usr/bin/env python3
"""ETAP 2 replay — walidacja offline Z-02 (sign-guard + Unknown-split) i Z-10
(margin na finalnym rankingu) na 7d korpusie shadow_decisions.jsonl.

Read-only; woła REALNE funkcje common.* (Lekcja #151 — zero ręcznej matematyki).

Z-02: pola v327_* NIE były serializowane (Z-09 naprawia od dziś) → rekonstrukcja:
  - strefy: C.drop_zone_from_address(delivery_address) dla nowego ordera +
    bag_context[].delivery_address per kandydat (caveat: bag_context nie ma
    delivery_city → default Białystok; decision-level delivery_city użyte dla nowego),
  - mult_old = bundle_score_multiplier(min_drop_proximity_factor(zones)),
  - score_pre_mult = score_logged / mult_old (mult aplikowany bezwarunkowo pre-fix),
  - score_new = apply_bundle_score_mult(pre_mult, mult_new, guard ON),
    mult_new z min_drop_proximity_factor_split + cap Unknown 0.7,
  - re-rank feasible (MAYBE) per decyzja → flipy zwycięzcy.

Z-10: czysta analiza logu — best vs score-top wśród feasible + delta marginu
  (stary top1−top2 vs nowy score(best)−max(reszta)).
"""
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2 import common as C  # noqa: E402

LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
DAYS = 7
EPS = 1e-9


def _zones_for_candidate(decision, cand):
    """(new_zone, bag_zones) — None gdy kandydat bez bagu / bez adresów."""
    bc = cand.get("bag_context") or []
    if not bc:
        return None
    new_zone = C.drop_zone_from_address(
        decision.get("delivery_address"), decision.get("delivery_city"))
    bag_zones = [
        C.drop_zone_from_address(b.get("delivery_address"), None)
        for b in bc
    ]
    return [new_zone] + bag_zones


def _mult_old(zones):
    mf = C.min_drop_proximity_factor(zones)
    return C.bundle_score_multiplier(mf)


def _mult_new(zones):
    mf_known, has_unknown = C.min_drop_proximity_factor_split(zones)
    mult = C.bundle_score_multiplier(mf_known)
    if has_unknown:
        mult = min(mult, C.V327_BUNDLE_UNKNOWN_SCORE_MULT)
    return mult


def main():
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS)
    n_dec = 0
    z02 = Counter()
    z02_flips = []
    z10 = Counter()
    z10_margin_deltas = []
    z10_divergence = []

    with open(LOG) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = d.get("ts")
            try:
                if datetime.fromisoformat(str(ts).replace("Z", "+00:00")) < cutoff:
                    continue
            except Exception:
                continue
            if d.get("verdict") != "PROPOSE":
                continue
            best = d.get("best")
            if not best:
                continue
            n_dec += 1

            cands = [best] + (d.get("alternatives") or [])
            feasible = [c for c in cands if c.get("feasibility") == "MAYBE"
                        and c.get("score") is not None]
            if not feasible:
                continue

            # ── Z-10 ──
            best_score = float(best.get("score") or 0.0)
            others = [float(c.get("score") or 0.0) for c in feasible
                      if str(c.get("courier_id")) != str(best.get("courier_id"))]
            if others:
                top_other = max(others)
                new_margin = best_score - top_other
                scores_sorted = sorted(
                    (float(c.get("score") or 0.0) for c in feasible), reverse=True)
                old_margin = (scores_sorted[0] - scores_sorted[1]
                              if len(scores_sorted) >= 2 else 0.0)
                z10_margin_deltas.append(new_margin - old_margin)
                if best_score < top_other - EPS:
                    z10["best_not_score_top"] += 1
                    z10_divergence.append({
                        "oid": d.get("order_id"), "ts": ts,
                        "best_cid": best.get("courier_id"),
                        "best_score": round(best_score, 1),
                        "top_other": round(top_other, 1),
                        "old_margin": round(old_margin, 1),
                        "auto_route": d.get("auto_route"),
                    })
                else:
                    z10["best_is_score_top"] += 1

            # ── Z-02 ──
            scored = []
            any_mult = False
            for c in feasible:
                s_logged = float(c.get("score") or 0.0)
                zones = _zones_for_candidate(d, c)
                if not zones:
                    scored.append((s_logged, s_logged, c, 1.0, 1.0))
                    continue
                m_old = _mult_old(zones)
                m_new = _mult_new(zones)
                if m_old != 1.0:
                    any_mult = True
                    pre = s_logged / m_old
                else:
                    pre = s_logged
                s_new, _g = C.apply_bundle_score_mult(pre, m_new, sign_guard_on=True)
                scored.append((s_logged, s_new, c, m_old, m_new))
            if not any_mult:
                z02["no_mult_in_pool"] += 1
                continue
            z02["pool_with_mult"] += 1
            old_win = max(scored, key=lambda x: x[0])
            new_win = max(scored, key=lambda x: x[1])
            if str(old_win[2].get("courier_id")) != str(new_win[2].get("courier_id")):
                z02["winner_flip"] += 1
                z02_flips.append({
                    "oid": d.get("order_id"), "ts": ts,
                    "old_winner": old_win[2].get("name"),
                    "old_score": round(old_win[0], 1),
                    "old_mult": round(old_win[3], 2),
                    "new_winner": new_win[2].get("name"),
                    "new_winner_old_score": round(new_win[0], 1),
                    "new_score": round(new_win[1], 1),
                    "new_mult": round(new_win[4], 2),
                    "old_winner_bag": len(old_win[2].get("bag_context") or []),
                    "new_winner_bag": len(new_win[2].get("bag_context") or []),
                })

    print(f"=== ETAP 2 replay ({DAYS}d, {n_dec} decyzji PROPOSE) ===\n")
    print("── Z-02 sign-guard + Unknown-split ──")
    for k, v in sorted(z02.items()):
        print(f"  {k}: {v}")
    print(f"  flipy zwycięzcy: {len(z02_flips)}")
    for fl in z02_flips[:15]:
        print("   ", json.dumps(fl, ensure_ascii=False))
    print("\n── Z-10 margin finalny ──")
    for k, v in sorted(z10.items()):
        print(f"  {k}: {v}")
    if z10_margin_deltas:
        import statistics
        nz = [x for x in z10_margin_deltas if abs(x) > EPS]
        print(f"  margin delta ≠0: {len(nz)}/{len(z10_margin_deltas)}"
              f" (median nz: {statistics.median(nz) if nz else 0:.1f} pkt)")
    print(f"  best≠score-top przykłady ({len(z10_divergence)}):")
    for dv in z10_divergence[:10]:
        print("   ", json.dumps(dv, ensure_ascii=False))


if __name__ == "__main__":
    main()
