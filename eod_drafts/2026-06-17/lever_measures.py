#!/usr/bin/env python3
"""Lever A/B/C/D measurements by SHORTAGE regime (READ-ONLY, reuses replay_harness_p1 helpers).
(A/B) late-pickup tiering + extension distribution by pool_feasible bucket
(R6)  bonus_r6_soft_pen pathology + objm breach by bucket
(sel) selection-policy elasticity (flips/ΔR6/Δcommit/regr) per bucket — leverage of C/D reorder by regime
(policy) coordinator_idle -100 demote frequency at scarcity
Precise contaminated-window exclusion; keeps pool_feasible_count."""
import sys, json, collections
from datetime import datetime, timezone
sys.path.insert(0, "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")
import replay_harness_p1 as H

SHADOW = ["/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
          "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"]
CONT = [(datetime(2026, 6, 6, 17, 53, tzinfo=timezone.utc), datetime(2026, 6, 10, 18, 24, tzinfo=timezone.utc)),
        (datetime(2026, 6, 11, 14, 28, tzinfo=timezone.utc), datetime(2026, 6, 12, 18, 32, tzinfo=timezone.utc))]
ORDER = ["0_collapse", "1-2_scarce", "3-4_tight", "5+_normal"]


def pts(s):
    try:
        return datetime.fromisoformat(str(s))
    except Exception:
        return None


def cont(t):
    return t is None or any(lo <= t <= hi for lo, hi in CONT)


def pfb(pf):
    if pf is None:
        return "?"
    pf = int(pf)
    return "0_collapse" if pf == 0 else "1-2_scarce" if pf <= 2 else "3-4_tight" if pf <= 4 else "5+_normal"


def load():
    out = []
    for path in SHADOW:
        try:
            f = open(path, "rb")
        except FileNotFoundError:
            continue
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("verdict") != "PROPOSE":
                continue
            best = r.get("best")
            if not isinstance(best, dict) or "score" not in best:
                continue
            if cont(pts(r.get("ts"))):
                continue
            oid = str(r.get("order_id"))
            try:
                e2 = int(oid) % 5 == 0
            except Exception:
                e2 = False
            cands = [best] + [a for a in (r.get("alternatives") or []) if isinstance(a, dict) and "score" in a]
            out.append({"oid": oid, "pf": r.get("pool_feasible_count"), "best": best, "cands": cands, "e2": e2})
        f.close()
    return out


decs = load()
byb = collections.defaultdict(list)
for d in decs:
    byb[pfb(d["pf"])].append(d)
print(f"clean PROPOSE={len(decs)}  buckets={ {b: len(byb[b]) for b in ORDER} }")


def pct(x, n):
    return f"{100.0*x/max(1,n):.1f}%"

# ---------- (A/B) late-pickup tiering + extension ----------
print("\n" + "=" * 92)
print("(A/B) DEFER/EXTEND on BEST: tier1=new-pickup needs extension, tier2=breaks committed pickup")
print("=" * 92)
print(f"{'bucket':12s} {'n':>5} {'tier1_ext%':>10} {'tier2_commitBreach%':>20} {'ext_min_p50':>12} {'new_late_p50':>13} {'committed_p50':>14}")
for b in ORDER:
    g = byb[b]
    n = len(g) or 1
    t1 = sum(1 for d in g if d["best"].get("new_pickup_needs_extension") is True)
    t2 = sum(1 for d in g if d["best"].get("late_pickup_committed_breach") is True)
    exts = sorted(H.num(d["best"].get("v324a_extension_min")) for d in g)
    nl = sorted(H.num(d["best"].get("new_pickup_late_min")) for d in g)
    cm = sorted(H.num(d["best"].get("late_pickup_committed_max")) for d in g)
    md = lambda a: round(a[len(a) // 2], 1) if a else 0
    print(f"{b:12s} {len(g):>5} {pct(t1,n):>10} {pct(t2,n):>20} {md(exts):>12} {md(nl):>13} {md(cm):>14}")

# ---------- (R6) pathology + breach ----------
print("\n" + "=" * 92)
print("(R6) bonus_r6_soft_pen pathology + objm_r6_breach_max_min on BEST, by bucket")
print("=" * 92)
print(f"{'bucket':12s} {'n':>5} {'r6pen<-1000(artefact)':>22} {'r6pen_p50':>10} {'objm_breach_p50':>16} {'breach>2min%':>12}")
for b in ORDER:
    g = byb[b]
    n = len(g) or 1
    art = sum(1 for d in g if H.num(d["best"].get("bonus_r6_soft_pen")) < -1000)
    pens = sorted(H.num(d["best"].get("bonus_r6_soft_pen")) for d in g)
    br = sorted(H.m_r6(d["best"]) for d in g)
    brc = sum(1 for d in g if H.m_r6(d["best"]) > 2.0)
    md = lambda a: round(a[len(a) // 2], 1) if a else 0
    print(f"{b:12s} {len(g):>5} {art:>5} ({pct(art,n)})       {md(pens):>10} {md(br):>16} {pct(brc,n):>12}")

# ---------- (sel) selection-policy elasticity by bucket ----------
print("\n" + "=" * 92)
print("(SEL) SELECTION-POLICY elasticity per bucket — flips + Σ minute deltas vs baseline argmax(score)")
print("  (within best tier+bucket group, non-E2). Negative ΣΔ = improvement. regr=flips worsening R6/committed>1min")
print("=" * 92)
POL = {
 "wait_x0.5":   lambda g: max(g, key=lambda c: H.cf_score(c, wx=0.5)),
 "load_x0.5":   lambda g: max(g, key=lambda c: H.cf_score(c, lx=0.5)),
 "D2_lexR6":    lambda g: min(g, key=lambda c: (H.m_r6(c), H.m_com(c), H.m_new(c))),
}
for b in ORDER:
    grp = [d for d in byb[b] if not d["e2"] and len(H.eligible(d)) >= 2]
    print(f"\n  --- {b}  (multi-cand non-E2 decisions: {len(grp)}) ---")
    if not grp:
        print("    (no multi-candidate decisions — selection policy CANNOT act)")
        continue
    for name, fn in POL.items():
        flips = regr = 0
        sR6 = sCom = sNew = sW = 0.0
        for d in grp:
            g = H.eligible(d)
            base = max(g, key=H.score_of)
            p = fn(g)
            if str(p.get("courier_id")) != str(base.get("courier_id")):
                flips += 1
                dR6 = H.m_r6(p) - H.m_r6(base); dCom = H.m_com(p) - H.m_com(base)
                sR6 += dR6; sCom += dCom; sNew += H.m_new(p) - H.m_new(base); sW += H.m_waste(p) - H.m_waste(base)
                if dR6 > 1.0 or dCom > 1.0:
                    regr += 1
        print(f"    {name:12s} flips={flips:>3}/{len(grp):<3} ΣΔR6={sR6:>8.0f} ΣΔcommit={sCom:>7.0f} "
              f"ΣΔnew={sNew:>7.0f} ΣΔidle={sW:>7.0f} regr={regr}")

# ---------- (policy) coordinator idle demote ----------
print("\n" + "=" * 92)
print("(POLICY) bonus_coordinator_idle = -100 demote: how often a feasible coordinator is in-pool at scarcity")
print("=" * 92)
for b in ORDER:
    g = byb[b]
    incand = sum(1 for d in g if any(c.get("is_coordinator") is True for c in d["cands"]))
    isbest = sum(1 for d in g if d["best"].get("is_coordinator") is True)
    demoted = sum(1 for d in g if any(c.get("is_coordinator") is True and H.num(c.get("bonus_coordinator_idle")) < -1 for c in d["cands"]))
    print(f"  {b:12s} n={len(g):>4}  coord_in_pool={incand:>3} ({pct(incand,len(g) or 1)})  coord_is_best={isbest}  coord_demoted(-100)={demoted}")
