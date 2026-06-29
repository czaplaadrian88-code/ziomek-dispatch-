#!/usr/bin/env python3
"""
N2 SELECTION REGRESSION over 14-16.06 — READ-ONLY (shadow_decisions golden source).

shadow_decisions.jsonl logs best + alternatives, each with full objective breakdown,
score, feasibility, AND v3273_wait_courier_per_pickup (the EXACT pipeline-computed wait
per pickup + hard_reject flag). The 14-16.06 logs were written under OLD code (N2 flag
OFF), so logged score/bonus_v3273_wait_courier/feasibility reflect OLD behavior.

We reconstruct, per decision, the feasible pool + selection winner under OLD vs NEW:

  picked_count (validated 663/663 vs obj_replay):
      = bag_size_before - #(non-new bag oids appearing in per_pickup with a wait)
    (a bag order appears in per_pickup only if picked_up_at is None = pending)

  OLD feasible iff NOT( feasibility==NO ) AND NOT( v3273_hard_reject AND has_pending )
      where has_pending == (any non-new bag oid in per_pickup) OR bag_size_before>picked_count
      (i.e. >=1 assigned-not-picked bag order). [verdict gate line 4838-4844]
  NEW feasible: v3273 hard-reject only stands if picked_count>=1.
      picked_count==0 -> hard-reject lifted (candidate re-enters feasible).

  NEW score (empty-handed picked_count==0 candidates):
      score_new = score_old - bonus_v3273_wait_courier_old + idle_soft_penalty(max_wait)
    (for picked_count>=1 candidates score unchanged; for rescued OLD-reject the OLD
     bonus_v3273 was 0 already, so score_new = score_old + idle_soft_penalty(max_wait))

  Selection key (mirror _late_pickup_score_first_key, LIVE flags ENABLE_LATE_PICKUP_*):
      (1 if tier==2 else 0, bucket, -adjusted_score, orig_rank)
    tier: 2 if late_pickup_committed_breach else 1 if new_pickup_needs_extension else 0
    bucket: 0 informed / 2 blind+empty|pre_shift / 1 other
    adjusted_score = score - late_pickup_soft_penalty(new_pickup_late_min, free=5, coeff=1.5, cap=60)

Then compare OLD winner vs NEW winner. For each decision where winner changes, classify
POPRAWA / NEUTRAL / REGRESJA on delivery-objective axes of the chosen winner:
    wait (v3273_wait_courier_max_min), committed (late_pickup_committed_max),
    R6 (objm_r6_breach_max_min / r6_max_bag_time_min), new_pickup_late_min.
"""
import json, sys
from datetime import datetime, timedelta

SHADOW = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
DAYS = ("2026-06-14", "2026-06-15", "2026-06-16")
IDLE_SOFT_THR = 5.0
IDLE_SOFT_PER_MIN = -4.0
EPS = 1.0  # min tolerance for "worse/better" on an axis

INFORMED = {"gps", "last_picked_up_recent", "last_picked_up_pickup", "last_picked_up_delivery",
            "last_assigned_pickup", "last_assigned_delivery", "post_wave", "pos_from_store",
            "last_known", "last_delivered"}

def wday(ts):
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (d + timedelta(hours=2)).strftime("%Y-%m-%d")
    except Exception:
        return None

def num(x, d=0.0):
    try:
        if x is None: return d
        return float(x)
    except Exception:
        return d

def idle_soft_pen(wait):
    if wait is None or wait <= IDLE_SOFT_THR:
        return 0.0
    return (wait - IDLE_SOFT_THR) * IDLE_SOFT_PER_MIN

def picked_count(c):
    """#picked_up in bag = bag_size_before - #(non-new bag oids in per_pickup)."""
    bc = c.get("bag_context") or []
    bag_oids = set(str(b.get("order_id")) for b in bc)
    bsb = c.get("bag_size_before")
    if bsb is None:
        bsb = len(bag_oids)
    pp = c.get("v3273_wait_courier_per_pickup") or []
    pp_oids = set(str(x.get("oid")) for x in pp)
    pending_in_pp = len(bag_oids & pp_oids)
    return max(0, int(bsb) - pending_in_pp), int(bsb)

def has_pending_assigned(c):
    """>=1 bag order assigned-not-picked (pending pickup)."""
    pc, bsb = picked_count(c)
    return (bsb - pc) >= 1

def old_feasible(c):
    """OLD verdict gate (line 4838-4844): reject (NO) iff hard_reject AND has_pending_pickup.
    Free-courier (no pending, all picked up) is KEPT even with hard_reject (skip_free=True)."""
    if str(c.get("feasibility")) == "NO":
        return False
    # OLD: hard_reject computed with bag_size=len(bag); the LOGGED v3273_wait_courier_hard_reject
    # IS that OLD value (logs written pre-N2). Gate fires only when courier has a pending pickup.
    if c.get("v3273_wait_courier_hard_reject") and has_pending_assigned(c):
        return False
    return True

def new_hard_reject(c):
    """NEW (N2): hard_reject regime counts only PICKED-UP food. If picked==0 the reject is
    lifted entirely (compute_wait_courier_penalty returns (0,False) for bag_size<1). If
    picked>=1 the OLD hard_reject still holds (cooling food unchanged)."""
    pc, _ = picked_count(c)
    if pc == 0:
        return False
    return bool(c.get("v3273_wait_courier_hard_reject"))

def new_feasible(c):
    if str(c.get("feasibility")) == "NO":
        return False
    # gate fires only when hard_reject (NEW) AND pending pickup exists
    if new_hard_reject(c) and has_pending_assigned(c):
        return False
    return True

def tier(c):
    if c.get("late_pickup_committed_breach"):
        return 2
    if c.get("new_pickup_needs_extension"):
        return 1
    return 0

def bucket(c):
    ps = c.get("pos_source")
    bag = num(c.get("r6_bag_size"))
    if ps == "pre_shift":
        return 2
    if ps in INFORMED:
        return 0
    if (ps in ("no_gps", "blind", None, "")) and bag == 0:
        return 2
    return 1

def late_soft_pen(c, free=5.0, coeff=1.5, cap=60.0):
    lm = c.get("new_pickup_late_min")
    if not isinstance(lm, (int, float)) or lm <= free:
        return 0.0
    return min(cap, coeff * (lm - free))

def new_score(c):
    """N2-adjusted score for empty-handed candidates."""
    s = num(c.get("score"))
    pc, _ = picked_count(c)
    if pc == 0:
        mw = num(c.get("v3273_wait_courier_max_min"))
        s = s - num(c.get("bonus_v3273_wait_courier")) + idle_soft_pen(mw)
    return s

def sel_key(c, score_val, orig_rank):
    adj = score_val - late_soft_pen(c)
    return (1 if tier(c) == 2 else 0, bucket(c), -adj, orig_rank)

def winner(cands, feas_fn, score_fn):
    pool = [(i, c) for i, c in enumerate(cands) if feas_fn(c)]
    if not pool:
        return None
    return min(pool, key=lambda ic: sel_key(ic[1], score_fn(ic[1]), ic[0]))[1]

def load_decisions():
    out = []
    with open(SHADOW) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            d = wday(r.get("ts"))
            if d not in DAYS:
                continue
            best = r.get("best") or {}
            if "score" not in best:
                continue
            cands = [best] + [a for a in (r.get("alternatives") or []) if "score" in a]
            out.append({"oid": str(r.get("order_id")), "ts": r.get("ts"), "day": d,
                        "verdict": r.get("verdict"), "best": best, "cands": cands})
    return out

def axis_cmp(new_c, old_c):
    """Return ('POPRAWA'|'NEUTRAL'|'REGRESJA', detail). Lower is better on all axes.

    Severity-aware. The serious regressions are hard-rule breaches introduced by the swap:
      - committed breach crossing: late_pickup_committed_breach False->True (tier-2!), or
        committed_max crossing 5.0 (HARD_MAX),
      - R6 breach introduced: objm_r6_breach_max_min 0 -> >EPS.
    A swap that only worsens a SOFT axis (wait, sub-breach committed, new_late) while improving
    another is a TRADE-OFF, not a regression — classified by NET minute delta.
    """
    nw_wait, ow_wait = num(new_c.get("v3273_wait_courier_max_min")), num(old_c.get("v3273_wait_courier_max_min"))
    nw_com, ow_com = num(new_c.get("late_pickup_committed_max")), num(old_c.get("late_pickup_committed_max"))
    nw_r6, ow_r6 = num(new_c.get("objm_r6_breach_max_min")), num(old_c.get("objm_r6_breach_max_min"))
    nw_nl, ow_nl = num(new_c.get("new_pickup_late_min")), num(old_c.get("new_pickup_late_min"))
    nw_cb = bool(new_c.get("late_pickup_committed_breach"))
    ow_cb = bool(old_c.get("late_pickup_committed_breach"))
    axes = [("wait", nw_wait, ow_wait), ("committed", nw_com, ow_com), ("R6", nw_r6, ow_r6), ("new_late", nw_nl, ow_nl)]
    detail = {a: (round(o, 1), round(n, 1)) for a, n, o in axes if abs(n - o) > EPS}
    # HARD breach introduced?
    hard_breach = []
    if nw_cb and not ow_cb:
        hard_breach.append("committed_breach_introduced")
    if nw_com > 5.0 + EPS and ow_com <= 5.0 + EPS:
        hard_breach.append("committed_crosses_5min")
    if nw_r6 > EPS and ow_r6 <= EPS:
        hard_breach.append("R6_breach_introduced")
    # NET minute change across the lateness axes (lower better): negative = improvement.
    net = (nw_wait - ow_wait) + (nw_com - ow_com) + (nw_r6 - ow_r6) + (nw_nl - ow_nl)
    if hard_breach:
        return "REGRESJA", {"hard_breach": hard_breach, "net_min": round(net, 1), "delta": detail}
    if net > EPS:
        return "REGRESJA_SOFT", {"net_min": round(net, 1), "delta": detail}
    if net < -EPS:
        return "POPRAWA", {"net_min": round(net, 1), "delta": detail}
    return "NEUTRAL", {"net_min": round(net, 1), "delta": detail}

if __name__ == "__main__":
    decs = load_decisions()
    print(f"decisions in window (scored best): {len(decs)}\n")

    per_day = {d: {"n": 0, "faithful": 0, "winner_changed": 0,
                   "POPRAWA": 0, "NEUTRAL": 0, "REGRESJA": 0, "REGRESJA_SOFT": 0,
                   "regress_list": [], "change_list": []} for d in DAYS}

    for dd in decs:
        d = dd["day"]
        cands = dd["cands"]
        st = per_day[d]
        st["n"] += 1
        st.setdefault("indeterminate", 0)
        # FAITHFULNESS gate: my selection key must reproduce the logged best as OLD #1 over
        # the logged candidate pool. If not, my key is missing a production gate (R6/carry-chain/
        # intra-gap/etc.) for this decision -> I CANNOT adjudicate a N2 change here. Mark
        # indeterminate (NOT a regression). This is the honesty guard the brief requires.
        ow_rerank = winner(cands, old_feasible, lambda c: num(c.get("score")))
        logged_best_cid = str(dd["best"].get("courier_id"))
        faithful = (ow_rerank is not None and str(ow_rerank.get("courier_id")) == logged_best_cid)
        if faithful:
            st["faithful"] += 1
        # ANCHOR: OLD winner = LOGGED BEST (production ground truth).
        ow = dd["best"]
        # NEW winner = best under N2-feasible pool with N2-adjusted scores.
        nw = winner(cands, new_feasible, new_score)
        if ow is None or nw is None:
            continue
        if str(nw.get("courier_id")) == str(ow.get("courier_id")):
            continue
        # winner differs under N2 (within logged pool)
        st["winner_changed"] += 1
        if not faithful:
            # model can't reproduce logged best -> can't trust a N2-change verdict here.
            st["indeterminate"] += 1
            continue
        best_pc, _ = picked_count(ow)
        nw_rescued = (not old_feasible(nw)) and new_feasible(nw)
        best_rescored = (best_pc == 0)
        verdict, detail = axis_cmp(nw, ow)
        st[verdict] += 1
        rec = {"oid": dd["oid"], "ts": dd["ts"],
               "old_cid": str(ow.get("courier_id")), "old_name": ow.get("name"),
               "old_picked": best_pc, "old_feas": ow.get("feasibility"),
               "old_score": round(num(ow.get("score")), 1),
               "new_cid": str(nw.get("courier_id")), "new_name": nw.get("name"),
               "new_picked": picked_count(nw)[0], "new_feas": nw.get("feasibility"),
               "new_was_old_reject": nw_rescued,
               "best_rescored_emptyhanded": best_rescored,
               "new_score_adj": round(new_score(nw), 1),
               "verdict": verdict, "detail": detail}
        st["change_list"].append(rec)
        if verdict == "REGRESJA":
            st["regress_list"].append(rec)
        elif verdict == "REGRESJA_SOFT":
            st.setdefault("regress_soft_list", []).append(rec)

    tot = {"n": 0, "faithful": 0, "winner_changed": 0, "POPRAWA": 0, "NEUTRAL": 0, "REGRESJA": 0, "REGRESJA_SOFT": 0, "indeterminate": 0}
    for d in DAYS:
        per_day[d].setdefault("indeterminate", 0)
        per_day[d].setdefault("regress_soft_list", [])
    for d in DAYS:
        st = per_day[d]
        for k in tot:
            tot[k] += st[k]
        adj = st['POPRAWA'] + st['NEUTRAL'] + st['REGRESJA'] + st['REGRESJA_SOFT']
        print(f"=== {d} ===")
        print(f"  decisions: {st['n']}   model-faithfulness(key reproduces logged best): {st['faithful']}/{st['n']} = {100*st['faithful']/max(1,st['n']):.1f}%")
        print(f"  winner differs under N2 (in logged pool): {st['winner_changed']}  (indeterminate/unfaithful: {st.get('indeterminate',0)})")
        print(f"  ADJUDICATED N2 winner changes (faithful): {adj}  -> POPRAWA={st['POPRAWA']} NEUTRAL={st['NEUTRAL']} REGRESJA_HARD={st['REGRESJA']} REGRESJA_SOFT(net+min,no breach)={st['REGRESJA_SOFT']}")
        for rec in st["regress_list"]:
            print(f"    !!! REGRESJA-HARD oid={rec['oid']} OLD cid={rec['old_cid']}({rec['old_name']},picked={rec['old_picked']}) -> NEW cid={rec['new_cid']}({rec['new_name']},picked={rec['new_picked']},rescued={rec['new_was_old_reject']})  breach={rec['detail'].get('hard_breach')} net={rec['detail'].get('net_min')}min delta={rec['detail'].get('delta')}")
        for rec in st["regress_soft_list"]:
            print(f"    reg-soft oid={rec['oid']} OLD {rec['old_cid']}(pk{rec['old_picked']}) -> NEW {rec['new_cid']}(pk{rec['new_picked']},resc={rec['new_was_old_reject']}) net=+{rec['detail'].get('net_min')}min delta={rec['detail'].get('delta')}")
        nonreg = [r for r in st["change_list"] if r["verdict"] in ("POPRAWA", "NEUTRAL")][:3]
        for rec in nonreg:
            print(f"    {rec['verdict']:8s} oid={rec['oid']} OLD {rec['old_cid']}(pk{rec['old_picked']}) -> NEW {rec['new_cid']}(pk{rec['new_picked']},resc={rec['new_was_old_reject']}) net={rec['detail'].get('net_min')}min delta={rec['detail'].get('delta')}")
        print()

    print("=" * 70)
    print("RAZEM 14-16.06 (selekcja)")
    print("=" * 70)
    adj = tot['POPRAWA'] + tot['NEUTRAL'] + tot['REGRESJA'] + tot['REGRESJA_SOFT']
    print(f"  decisions: {tot['n']}  model-faithfulness: {tot['faithful']}/{tot['n']} = {100*tot['faithful']/max(1,tot['n']):.1f}%")
    print(f"  winner differs under N2 total: {tot['winner_changed']}  (indeterminate/unfaithful: {tot['indeterminate']})")
    print(f"  ADJUDICATED (faithful): {adj}  POPRAWA={tot['POPRAWA']} NEUTRAL={tot['NEUTRAL']} REGRESJA_HARD={tot['REGRESJA']} REGRESJA_SOFT={tot['REGRESJA_SOFT']}")
    # dump
    out = {d: {k: per_day[d][k] for k in ("n", "faithful", "winner_changed", "POPRAWA", "NEUTRAL", "REGRESJA", "REGRESJA_SOFT", "indeterminate", "regress_list", "regress_soft_list", "change_list")} for d in DAYS}
    with open("/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17/n2_selection_3day.json", "w") as g:
        json.dump(out, g, ensure_ascii=False, indent=2)
    print("\n[written n2_selection_3day.json]")
