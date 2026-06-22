#!/usr/bin/env python3
"""rule_weights_sensitivity.py — READ-ONLY replay: leverage + sensitivity of R1/R5/R8 rule_weights.

ZERO writes, ZERO engine calls. Pure arithmetic on logged shadow_decisions.jsonl.

Why this is valid: the rule penalties are LINEAR in the weight (dispatch_pipeline.py:4091/4095/4204):
    bonus_r1_soft_pen = r1_violation_km  * R1_spread_per_km
    bonus_r5_soft_pen = r5_violation_km  * R5_pickup_per_km
    bonus_r8_soft_pen = base(-(span-7)*3) + r8_violation_min * R8_span_per_min   # base is NOT a weight
Violations are NOT logged, so we back them out from the logged penalty + current weight.
Reweighting a candidate's final score under a new weight w_new is then exact:
    R1/R5: score_new = score + bonus*(w_new/w_old - 1)
    R8   : score_new = score + viol8*(w_new - w_old)        # viol8 derived from span+bag; base cancels

We re-rank {best + alternatives} by score and ask: does the top-1 change? That bounds how much the
weights actually drive SELECTION. (Caveat: real selection adds _demote_blind_empty + late-pickup
tiering on top of score; we report how often `best` == score-argmax so the proxy's validity is explicit.)

Run: /root/.openclaw/venvs/dispatch/bin/python eod_drafts/2026-06-21/rule_weights_sensitivity.py
"""
import json
import sys

SHADOW = ["/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
          "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"]

# Current LIVE weights (dispatch_state/rule_weights.json, static since 2026-04-16)
R1_OLD, R5_OLD, R8_OLD = -8.0, -6.0, -1.5
# R8 thresholds + soft base (common.py): hard 15 (bundle2) / 30 (bundle3+); soft base -(span-7)*3
R8_THR2, R8_THR3 = 15.0, 30.0


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _cands(d):
    out = []
    if d.get("best"):
        out.append(d["best"])
    out += (d.get("alternatives") or [])
    return [c for c in out if c.get("score") is not None]


def _viol8(c):
    """Derive r8_violation_min from logged span + bag size (base part is weight-independent)."""
    span = c.get("r8_pickup_span_min")
    if span is None:
        return 0.0
    bag = c.get("r6_bag_size")
    if bag is None:
        bag = c.get("bag_size_before")
    bundle = (int(bag) + 1) if bag is not None else 2  # bag before + new order
    thr = R8_THR2 if bundle <= 2 else R8_THR3
    return max(0.0, _f(span) - thr)


def reweight(c, w1, w5, w8):
    """Final score of candidate c under new (w1,w5,w8). Exact, linear."""
    s = _f(c.get("score"))
    b1 = _f(c.get("bonus_r1_soft_pen"))
    b5 = _f(c.get("bonus_r5_soft_pen"))
    s += b1 * (w1 / R1_OLD - 1.0) if b1 else 0.0
    s += b5 * (w5 / R5_OLD - 1.0) if b5 else 0.0
    v8 = _viol8(c)
    s += v8 * (w8 - R8_OLD)
    return s


def argmax_cid(cands, w1, w5, w8):
    best, bs = None, -1e18
    for i, c in enumerate(cands):
        sc = reweight(c, w1, w5, w8)
        if sc > bs:
            bs, best = sc, c.get("courier_id", i)
    return best


def main():
    decisions = []
    for fn in SHADOW:
        try:
            fh = open(fn)
        except OSError:
            continue
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("verdict") != "PROPOSE":
                continue
            cands = _cands(d)
            if len(cands) >= 2:
                decisions.append(cands)
        fh.close()

    n = len(decisions)
    print(f"=== CORPUS ===\nmulti-candidate PROPOSE decisions: {n}\n")

    # (A) proxy validity: is logged `best` the score-argmax of {best+alts}?
    best_is_argmax = sum(1 for c in decisions
                         if (c[0].get("courier_id") == argmax_cid(c, R1_OLD, R5_OLD, R8_OLD)))
    print("=== (A) PROXY VALIDITY ===")
    print(f"logged best == score-argmax (current weights): {best_is_argmax}/{n} "
          f"({100*best_is_argmax/n:.1f}%)  [rest = demote/tiering reorders; score-rank is the primary driver]\n")

    base_winner = [argmax_cid(c, R1_OLD, R5_OLD, R8_OLD) for c in decisions]

    def flip_rate(w1, w5, w8):
        flips = sum(1 for i, c in enumerate(decisions)
                    if argmax_cid(c, w1, w5, w8) != base_winner[i])
        return flips, 100 * flips / n

    # (B) LEVERAGE: zero-out each rule (and all) → how often does the winner change?
    print("=== (B) LEVERAGE (winner-flip vs current when a rule is REMOVED) ===")
    for label, w in [("zero R1", (0.0, R5_OLD, R8_OLD)),
                     ("zero R5", (R1_OLD, 0.0, R8_OLD)),
                     ("zero R8", (R1_OLD, R5_OLD, 0.0)),
                     ("zero ALL R1+R5+R8", (0.0, 0.0, 0.0))]:
        f, pct = flip_rate(*w)
        print(f"  {label:24s}: {f:4d} flips  ({pct:.1f}%)")
    print()

    # (C) SENSITIVITY GRID: scale each weight (one at a time) → winner-flip rate
    print("=== (C) SENSITIVITY (winner-flip vs current; one weight scaled at a time) ===")
    print(f"  {'scale':>6} | {'R1 (w=' + str(R1_OLD) + ')':>16} | {'R5 (w=' + str(R5_OLD) + ')':>16} | {'R8 (w=' + str(R8_OLD) + ')':>16}")
    for s in (0.5, 0.75, 1.25, 1.5, 2.0):
        _, p1 = flip_rate(R1_OLD * s, R5_OLD, R8_OLD)
        _, p5 = flip_rate(R1_OLD, R5_OLD * s, R8_OLD)
        _, p8 = flip_rate(R1_OLD, R5_OLD, R8_OLD * s)
        print(f"  {s:>6.2f} | w={R1_OLD*s:>6.1f} {p1:>5.1f}%   | w={R5_OLD*s:>6.1f} {p5:>5.1f}%   | w={R8_OLD*s:>6.1f} {p8:>5.1f}%")
    print()

    # (D) PENALTY MAGNITUDE vs WINNING MARGIN: do penalties dominate the score gap?
    print("=== (D) PENALTY MAGNITUDE vs TOP-1 MARGIN ===")
    margins, pen_share = [], []
    for c in decisions:
        scored = sorted((reweight(x, R1_OLD, R5_OLD, R8_OLD) for x in c), reverse=True)
        if len(scored) >= 2:
            margin = scored[0] - scored[1]
            margins.append(margin)
            # winner's total rule penalty magnitude
            w = max(c, key=lambda x: reweight(x, R1_OLD, R5_OLD, R8_OLD))
            tot_pen = abs(_f(w.get("bonus_r1_soft_pen"))) + abs(_f(w.get("bonus_r5_soft_pen"))) + abs(_viol8(w) * R8_OLD)
            if margin > 0:
                pen_share.append(min(tot_pen / margin, 5.0))
    margins.sort()
    if margins:
        p50 = margins[len(margins)//2]
        p10 = margins[len(margins)//10]
        print(f"  top-1 margin (score gap to #2): p10={p10:.2f}  p50={p50:.2f}  "
              f"median winner rule-penalty/margin ratio={sorted(pen_share)[len(pen_share)//2]:.2f}" if pen_share else "")
    # (E) VERDICT BOUNDARY: do the weights move the PROPOSE<->KOORD floor (MIN_PROPOSE_SCORE=-100)?
    # The rule penalties are in the gate score (NOT excluded like sync/loadgov ranking deltas), so a
    # heavier weight could sink a borderline best below -100 (=> KOORD) and a lighter one could lift a
    # KOORD-low best above it (=> PROPOSE). gate_score ~= logged best.score (sync/loadgov are shadow/off).
    MIN_PROP = -100.0
    prop_best, koord_low_best = [], []
    for fn in SHADOW:
        try:
            fh = open(fn)
        except OSError:
            continue
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            b = d.get("best")
            if not b or b.get("score") is None:
                continue
            v, r = d.get("verdict"), (d.get("reason") or "")
            if v == "PROPOSE":
                prop_best.append(b)
            elif v == "KOORD" and "all_candidates_low_score" in r:
                koord_low_best.append(b)
        fh.close()
    print("=== (E) VERDICT-BOUNDARY LEVERAGE (PROPOSE<->KOORD floor = MIN_PROPOSE_SCORE -100) ===")
    print(f"  PROPOSE bests: {len(prop_best)} | KOORD(all_low_score) bests: {len(koord_low_best)}")
    for s in (0.5, 1.5, 2.0):
        to_koord = sum(1 for b in prop_best
                       if reweight(b, R1_OLD*s, R5_OLD*s, R8_OLD*s) < MIN_PROP <= reweight(b, R1_OLD, R5_OLD, R8_OLD))
        to_propose = sum(1 for b in koord_low_best
                         if reweight(b, R1_OLD*s, R5_OLD*s, R8_OLD*s) >= MIN_PROP > reweight(b, R1_OLD, R5_OLD, R8_OLD))
        print(f"  scale {s:>4.2f} (all 3 weights): PROPOSE->KOORD {to_koord:3d}  |  KOORD->PROPOSE {to_propose:3d}")
    print()

    print("\n(Interpretation: high flip% on a weight = selection is sensitive to it = recalibration matters.\n"
          " ~0% flip = low-leverage = recalibration is a no-op for SELECTION regardless of outcome signal.)")


if __name__ == "__main__":
    main()
