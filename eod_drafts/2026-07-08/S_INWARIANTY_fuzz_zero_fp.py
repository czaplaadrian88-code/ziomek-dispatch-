"""Zero-FP dowód INV-FEAS-NO-DOUBLE-BOOK (Sprint B).

Fuzz replay: N losowych sweepów de-konflikcji przez PRAWDZIWY global_allocate (z fake
assess reagującym na obciążenie worka), CHECK ON. Prawda-oracle: przy nietkniętym
`tentative_assign` worek rośnie o +1 per claim → tripwire MUSI dać ZERO naruszeń,
NAWET gdy kurier legalnie dostaje 2-3 zlecenia (bundling — właśnie tam naiwny cap
by fałszował). Kontr-próba: neutralizacja tentative_assign → naruszenia >0.
"""
import random
import types

import dispatch_v2.common as C
import dispatch_v2.tools.pending_global_resweep as PGR

random.seed(20260708)  # deterministyczny

_CHECK = "ENABLE_CLAIM_LEDGER_INVARIANT_CHECK"


def _cs(cid, bag=None):
    return types.SimpleNamespace(courier_id=cid, bag=list(bag or []), name=cid)


def _rec(oid):
    return {"order_id": oid, "status": "planned", "restaurant": "R",
            "delivery_address": f"a-{oid}", "pickup_coords": [53.1, 23.1],
            "delivery_coords": [53.1, 23.2]}


def _make_assess(base, load_pen):
    def _assess(order_event, fleet, now):
        oid = order_event["order_id"]
        cands = []
        for cid in base[oid]:
            cs = fleet.get(cid)
            load = len(cs.bag) if cs is not None else 0
            sc = base[oid][cid] - load_pen * load
            cands.append(types.SimpleNamespace(
                courier_id=cid, score=float(sc), name=cid,
                feasibility_verdict="MAYBE",
                metrics={"km_to_pickup": 1.0, "r6_max_bag_time_min": 20.0,
                         "r1_new_drop_cosine": 0.1, "deliv_spread_km": 3.0}))
        cands.sort(key=lambda c: -c.score)
        best = cands[0] if cands else None
        return types.SimpleNamespace(best=best, candidates=cands, verdict="PROPOSE",
                                     pool_total_count=len(cands), pool_feasible_count=len(cands))
    return _assess


def run(n_trials, neutralize=False):
    C.decision_flag = lambda name: name == _CHECK  # CHECK ON
    orig_ta = PGR._tentative_assign
    if neutralize:
        PGR._tentative_assign = lambda fleet, cid, rec: dict(fleet)
    total_breaches = 0
    trials_with_bundle = 0   # >=1 kurier z >=2 zleceniami (legalny bundling)
    total_claims = 0
    trials_with_breach = 0
    try:
        from datetime import datetime, timezone
        now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
        for _ in range(n_trials):
            ncour = random.randint(2, 6)
            nord = random.randint(1, 8)
            cids = [chr(ord("A") + i) for i in range(ncour)]
            base = {}
            for j in range(nord):
                oid = f"o{j}"
                base[oid] = {c: random.uniform(0, 100) for c in cids}
            load_pen = random.uniform(1, 60)
            PGR._assess = _make_assess(base, load_pen)
            fleet = {c: _cs(c) for c in cids}
            diag = {}
            alloc = PGR.global_allocate([(f"o{j}", _rec(f"o{j}")) for j in range(nord)],
                                        fleet, now, _diag_out=diag)
            b = diag["claim_ledger_breaches"]
            total_breaches += len(b)
            if b:
                trials_with_breach += 1
            total_claims += len(diag["claim_trace"])
            from collections import Counter
            pile = Counter(c for c, _, _ in diag["claim_trace"])
            if pile and max(pile.values()) >= 2:
                trials_with_bundle += 1
    finally:
        PGR._tentative_assign = orig_ta
    return {"n_trials": n_trials, "total_claims": total_claims,
            "trials_with_bundle": trials_with_bundle,
            "trials_with_breach": trials_with_breach,
            "total_breaches": total_breaches}


if __name__ == "__main__":
    intact = run(5000, neutralize=False)
    print("INTACT  (tentative_assign żywy):", intact)
    assert intact["total_breaches"] == 0, "FALSE POSITIVE! tripwire odpalił na poprawnym sweepie"
    assert intact["trials_with_bundle"] > 0, "fuzz nie wygenerował bundlingu — słaby test"
    mutated = run(5000, neutralize=True)
    print("MUTATED (tentative_assign no-op):", mutated)
    assert mutated["total_breaches"] > 0, "tripwire ŚLEPY — mutacja nie wykryta"
    print("\nZERO-FP DOWÓD: 0 fałszywek na", intact["n_trials"],
          "sweepach (", intact["trials_with_bundle"], "z bundlingiem,",
          intact["total_claims"], "claimów); mutacja wykryta w",
          mutated["trials_with_breach"], "sweepach.")
