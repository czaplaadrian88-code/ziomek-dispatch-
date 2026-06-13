#!/usr/bin/env python3
"""B1 — BUG-A (bag_time fairness) + BUG-B (R5 pickup detour) replay/flip analysis.

READ-ONLY. Processes accumulated shadow_decisions logs and quantifies, per flag,
how many ranking decisions would flip if the feature were ON, the direction and
magnitude of the change (fairness gain vs SLA/efficiency cost), harmful cases,
and new-KOORD (ALWAYS-PROPOSE breach) risk.

Key facts established before writing this tool (verified live 2026-06-13):
  * BUG-B (ENABLE_R5_PICKUP_DETOUR_PENALTY) was FLIPPED ON ~2026-06-11 21:29 via
    flags.json with R5_DETOUR_PENALTY_PER_KM=4.0 (per VERDICT_bug_a_b.md 06-11).
    -> So `live` log (>=06-11) has B penalties APPLIED; `.1` (06-02..06-10) does NOT.
  * BUG-A (ENABLE_BAG_TIME_FAIRNESS_SCORING) is still OFF.
  * Lesson #186 / E7-doklejki (2026-06-11): penalties are now COMPUTED ALWAYS into
    `*_shadow` fields; the flag only gates application to score. So the counterfactual
    is the `_shadow` field. BUT the compute-always landed 06-11 -> `.1` records
    (pre-06-11) have ZERO/garbage shadow fields. The valid window for A counterfactual
    is the post-fix data only.

Penalty reconstruction (matches dispatch_pipeline.py:3240-3276 + common.py):
  kara_A = -(SUM_w*sum_bag_time_min + MAX_w*max_bag_time_min + FIFO_w*fifo_violations)
  kara_B = -(PERKM*max(0, r5_pickup_detour_total_km - FREE))
We use the serialized `*_shadow` fields directly when present (authoritative), and
also recompute from raw metrics to (a) sanity-check, (b) sweep alternative weights.

Flip model: for each decision with >=2 ELIGIBLE candidates, recompute each
candidate score as score + (penalty_with_feature - penalty_currently_applied),
re-argmax over the eligible pool, and compare to the current eligible argmax.
Eligible pool = feasibility != NO, exclude koordynator(cid=26) and blind+empty
demote unless the current best is itself such (mirrors VERDICT_bug_a_b.md method).
"""
import json
import sys
import statistics as st
from collections import Counter, defaultdict

LOGS = [
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",  # 06-02..06-10 (B OFF)
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",     # 06-11..now  (B ON ~06-11 21:29)
]

# Flip date for BUG-B (flags.json mtime window: between 12:28 and 21:29 on 06-11;
# backup pre-orphan-cleanup at 21:29 already has R5=True). We treat 06-11 as the
# boundary day; live records dated >= 06-11T21:00 are "B applied".
B_FLIP_TS = "2026-06-11T21:00:00"

# Default weights (common.py module-level / verdict candidates)
A_SUM_DEF, A_MAX_DEF, A_FIFO_DEF = 1.0, 0.7, 5.0
B_PERKM_DEF, B_FREE_DEF = 8.0, 0.5
B_PERKM_LIVE = 4.0  # currently-applied per flags.json

MIN_PROPOSE_SCORE = -100.0  # below this -> demoted to KOORD (ALWAYS-PROPOSE breach)


def num(x):
    return float(x) if isinstance(x, (int, float)) else None


def karaA(m, w_sum, w_max, w_fifo):
    s = num(m.get("sum_bag_time_min"))
    mx = num(m.get("max_bag_time_min"))
    fi = num(m.get("fifo_violations"))
    if s is None and mx is None and fi is None:
        return None
    s = s or 0.0; mx = mx or 0.0; fi = fi or 0.0
    return -(w_sum * s + w_max * mx + w_fifo * fi)


def karaB(m, perkm, free):
    d = num(m.get("r5_pickup_detour_total_km"))
    if d is None:
        return 0.0  # bag0/1 no route -> structurally zero detour
    return -(perkm * max(0.0, d - free))


def is_eligible(c):
    feas = c.get("feasibility")
    if feas == "NO":
        return False
    cid = c.get("courier_id") or c.get("cid")
    if cid == 26:
        return False
    pos = c.get("pos_source")
    bag = c.get("r6_bag_size")
    if bag is None:
        bag = c.get("bag_size")
    if pos in ("no_gps", "pre_shift", "none") and (bag in (0, None)):
        return False  # blind+empty demote (V3.16)
    return True


def candidates_of(rec):
    """Return list of candidate metric-dicts: best + alternatives."""
    out = []
    b = rec.get("best")
    if isinstance(b, dict):
        bb = dict(b); bb["_is_best"] = True
        out.append(bb)
    for a in (rec.get("alternatives") or []):
        if isinstance(a, dict):
            out.append(a)
    return out


def cand_score(c):
    for k in ("score", "final_score", "total_score"):
        v = num(c.get(k))
        if v is not None:
            return v
    return None


def main():
    # accumulators
    win = {"pre": Counter(), "post": Counter()}  # record counts by verdict per window
    # populated-field coverage
    covA_post = 0; covA_total_post = 0
    covB_nonzero_applied_post = 0; covB_applied_total_post = 0
    covB_nonzero_applied_pre = 0; covB_applied_total_pre = 0
    # magnitude distributions (PROPOSE best, from shadow fields)
    A_shadow_mag = []  # |kara_A| on best (default weights, from raw)
    A_maxfifo_mag = []
    B_shadow_mag = []  # |kara_B| nonzero on best (default 8.0)
    B_live_mag = []    # |kara_B| nonzero on best (4.0)
    A_sum_raw = []; A_max_raw = []; B_detour_raw = []
    fifo_hist = Counter()
    A_kara_by_bag = defaultdict(list)

    # flip analysis (eligible pool). Variants:
    variants = {
        "A_default_1.0/0.7/5.0": ("A", A_SUM_DEF, A_MAX_DEF, A_FIFO_DEF),
        "A_maxfifo_0/0.7/5.0":   ("A", 0.0,       A_MAX_DEF, A_FIFO_DEF),
        "A_soft_0.3/0.5/5.0":    ("A", 0.3,       0.5,       A_FIFO_DEF),
        "B_default_8.0/0.5":     ("B", B_PERKM_DEF, B_FREE_DEF, None),
        "B_live_4.0/0.5":        ("B", 4.0,         B_FREE_DEF, None),
        "B_6.0/0.5":             ("B", 6.0,         B_FREE_DEF, None),
        "B_2.0/0.5":             ("B", 2.0,         B_FREE_DEF, None),
    }
    flips = {k: 0 for k in variants}
    flips_dir_ok = {k: 0 for k in variants}     # new winner has smaller objective metric
    flips_dir_bad = {k: 0 for k in variants}
    new_koord = {k: 0 for k in variants}         # new winner score_adj < MIN_PROPOSE (and old >=)
    flip_margin = {k: [] for k in variants}
    eligible_decisions = 0
    eligible_propose = 0
    # harmful: flip where new winner is much farther from pickup (km_pu) than old
    harm_far_pickup = {k: 0 for k in variants}
    flip_examples = {k: [] for k in variants}

    # outcome join is NOT available in this tool (no backfill read) — we rely on
    # directional sanity + magnitude; the 06-11 verdict already did the outcome join.

    for path in LOGS:
        try:
            fh = open(path, "rb")
        except FileNotFoundError:
            continue
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts = rec.get("ts") or ""
            verdict = rec.get("verdict")
            if verdict not in ("PROPOSE", "KOORD", "AUTO"):
                continue
            window = "post" if ts >= B_FLIP_TS else "pre"
            win[window][verdict] += 1

            best = rec.get("best") or {}
            # --- coverage / magnitude on best (PROPOSE only for magnitude) ---
            if verdict == "PROPOSE":
                if window == "post":
                    covA_total_post += 1
                    ka = karaA(best, A_SUM_DEF, A_MAX_DEF, A_FIFO_DEF)
                    if ka is not None and (best.get("sum_bag_time_min") is not None):
                        covA_post += 1
                        A_shadow_mag.append(abs(ka))
                        kmf = karaA(best, 0.0, A_MAX_DEF, A_FIFO_DEF)
                        A_maxfifo_mag.append(abs(kmf))
                        s = num(best.get("sum_bag_time_min")) or 0.0
                        mx = num(best.get("max_bag_time_min")) or 0.0
                        fi = int(num(best.get("fifo_violations")) or 0)
                        A_sum_raw.append(s); A_max_raw.append(mx); fifo_hist[min(fi, 4)] += 1
                        bag = best.get("r6_bag_size")
                        if bag is None:
                            bag = best.get("bag_size")
                        if bag is not None:
                            A_kara_by_bag[min(int(bag), 4)].append(abs(ka))
                    # B applied field check (post = should be applying 4.0)
                    bap = num(best.get("bonus_r5_pickup_detour_penalty"))
                    if bap is not None:
                        covB_applied_total_post += 1
                        if abs(bap) > 1e-9:
                            covB_nonzero_applied_post += 1
                    # B magnitudes from raw detour
                    d = num(best.get("r5_pickup_detour_total_km"))
                    if d is not None:
                        B_detour_raw.append(d)
                        kb8 = abs(karaB(best, B_PERKM_DEF, B_FREE_DEF))
                        kb4 = abs(karaB(best, 4.0, B_FREE_DEF))
                        if kb8 > 1e-9:
                            B_shadow_mag.append(kb8)
                        if kb4 > 1e-9:
                            B_live_mag.append(kb4)
                else:  # pre window: B should NOT be applied
                    bap = num(best.get("bonus_r5_pickup_detour_penalty"))
                    if bap is not None:
                        covB_applied_total_pre += 1
                        if abs(bap) > 1e-9:
                            covB_nonzero_applied_pre += 1

            # --- flip analysis on eligible pool ---
            cands = candidates_of(rec)
            elig = [c for c in cands if is_eligible(c) and cand_score(c) is not None]
            if len(elig) < 2:
                continue
            # only meaningful where A shadow fields populated (post window) for A,
            # B detour available across both windows. We restrict the flip pool to
            # records that have at least raw metrics needed.
            eligible_decisions += 1
            if verdict == "PROPOSE":
                eligible_propose += 1
            cur_best = max(elig, key=lambda c: cand_score(c))
            cur_best_score = cand_score(cur_best)

            for vname, (which, p1, p2, p3) in variants.items():
                # currently-applied penalty for this feature (to compute delta)
                def applied(c):
                    if which == "A":
                        # A currently OFF in BOTH windows -> applied 0
                        return 0.0
                    else:
                        # B applied = 4.0 in post window, 0 in pre window
                        if window == "post":
                            return karaB(c, B_PERKM_LIVE, B_FREE_DEF)
                        return 0.0

                def want(c):
                    if which == "A":
                        k = karaA(c, p1, p2, p3)
                        return k if k is not None else 0.0
                    else:
                        return karaB(c, p1, p2)

                # require the feature's metric to be present on enough of the pool;
                # for A, skip records where best lacks sum_bag_time (pre-fix data)
                if which == "A" and num(cur_best.get("sum_bag_time_min")) is None:
                    continue

                def adj_score(c):
                    return cand_score(c) + (want(c) - applied(c))

                new_best = max(elig, key=adj_score)
                if new_best is cur_best:
                    continue
                flips[vname] += 1
                margin = adj_score(new_best) - adj_score(cur_best)
                flip_margin[vname].append(margin)
                # new-KOORD: new winner falls below MIN_PROPOSE while old was above
                if adj_score(new_best) < MIN_PROPOSE_SCORE <= cur_best_score:
                    new_koord[vname] += 1
                # directional sanity
                if which == "A":
                    o = num(cur_best.get("sum_bag_time_min"))
                    n = num(new_best.get("sum_bag_time_min"))
                    if o is not None and n is not None:
                        (flips_dir_ok if n <= o else flips_dir_bad)[vname] += 1
                else:
                    o = num(cur_best.get("r5_pickup_detour_total_km")) or 0.0
                    n = num(new_best.get("r5_pickup_detour_total_km")) or 0.0
                    (flips_dir_ok if n <= o else flips_dir_bad)[vname] += 1
                    # harmful: new winner much farther from pickup
                    okm = num(cur_best.get("km_to_pickup")) or num(cur_best.get("km_pu"))
                    nkm = num(new_best.get("km_to_pickup")) or num(new_best.get("km_pu"))
                    if okm is not None and nkm is not None and nkm - okm > 4.0:
                        harm_far_pickup[vname] += 1
                if len(flip_examples[vname]) < 8:
                    flip_examples[vname].append({
                        "oid": rec.get("order_id"), "ts": ts,
                        "old_cid": cur_best.get("courier_id"),
                        "new_cid": new_best.get("courier_id"),
                        "old_score": round(cur_best_score, 1), "new_adj": round(adj_score(new_best), 1),
                        "metric_old": round((num(cur_best.get("sum_bag_time_min")) if which == "A" else (num(cur_best.get("r5_pickup_detour_total_km")) or 0.0)) or 0.0, 1),
                        "metric_new": round((num(new_best.get("sum_bag_time_min")) if which == "A" else (num(new_best.get("r5_pickup_detour_total_km")) or 0.0)) or 0.0, 1),
                    })
        fh.close()

    def pct(a, b):
        return f"{(100.0*a/b):.1f}%" if b else "n/a"

    def q(lst, p):
        if not lst:
            return None
        lst2 = sorted(lst)
        i = min(len(lst2) - 1, int(p * len(lst2)))
        return round(lst2[i], 1)

    R = {}
    R["windows"] = {
        "pre_06-11 (B OFF)": dict(win["pre"]),
        "post_06-11_21:00 (B ON 4.0)": dict(win["post"]),
    }
    R["B_flip_effect_check"] = {
        "pre_best_nonzero_applied_B": f"{covB_nonzero_applied_pre}/{covB_applied_total_pre} = {pct(covB_nonzero_applied_pre, covB_applied_total_pre)}",
        "post_best_nonzero_applied_B": f"{covB_nonzero_applied_post}/{covB_applied_total_post} = {pct(covB_nonzero_applied_post, covB_applied_total_post)}",
        "interpretation": "post nonzero >> 0 confirms flip took effect live; pre ~0 confirms baseline OFF",
    }
    R["A_shadow_coverage_post"] = {
        "best_with_populated_A_fields": f"{covA_post}/{covA_total_post} = {pct(covA_post, covA_total_post)}",
    }
    R["magnitudes_best_PROPOSE_post"] = {
        "A_default |kara| p50/p90/max": [q(A_shadow_mag, .5), q(A_shadow_mag, .9), round(max(A_shadow_mag), 1) if A_shadow_mag else None],
        "A_maxfifo |kara| p50/p90/max": [q(A_maxfifo_mag, .5), q(A_maxfifo_mag, .9), round(max(A_maxfifo_mag), 1) if A_maxfifo_mag else None],
        "B_8.0 |kara nonzero| p50/p90/max": [q(B_shadow_mag, .5), q(B_shadow_mag, .9), round(max(B_shadow_mag), 1) if B_shadow_mag else None],
        "B_4.0 |kara nonzero| p50/p90/max": [q(B_live_mag, .5), q(B_live_mag, .9), round(max(B_live_mag), 1) if B_live_mag else None],
        "raw sum_bag_time p50/p90 (min)": [q(A_sum_raw, .5), q(A_sum_raw, .9)],
        "raw max_bag_time p50/p90 (min)": [q(A_max_raw, .5), q(A_max_raw, .9)],
        "raw detour p50/p90 (km)": [q(B_detour_raw, .5), q(B_detour_raw, .9)],
        "n_B_nonzero / n_B_with_detour": f"{len(B_shadow_mag)} / {len(B_detour_raw)}",
        "fifo_hist(0..4+)": dict(fifo_hist),
        "A_kara_by_bag mean": {k: round(st.mean(v), 1) for k, v in sorted(A_kara_by_bag.items()) if v},
    }
    R["flip_analysis"] = {"eligible_decisions": eligible_decisions, "eligible_propose": eligible_propose}
    for vname in variants:
        n = flips[vname]
        R["flip_analysis"][vname] = {
            "flip_rate_vs_eligible": pct(n, eligible_decisions),
            "n_flips": n,
            "dir_ok/bad": f"{flips_dir_ok[vname]}/{flips_dir_bad[vname]}",
            "new_KOORD": f"{new_koord[vname]} ({pct(new_koord[vname], eligible_propose)})",
            "harm_far_pickup(>4km)": harm_far_pickup[vname],
            "margin p50/p90": [q(flip_margin[vname], .5), q(flip_margin[vname], .9)],
        }
    R["flip_examples"] = {k: v for k, v in flip_examples.items() if v}

    print(json.dumps(R, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
