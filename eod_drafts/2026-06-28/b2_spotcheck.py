"""B2 LIVE spot-check (po restarcie 28.06 18:16 UTC). READ-ONLY.

Dla rekordow E2 'pln' arm PO restarcie: porownaj zywy pick (best=top[0], teraz liczony
nowym _selection_bucket) ze STARYM stale-bucketem. Roznica = fix dziala na zywo i promuje
no_gps/pre_shift, ktorych stary kod by zdemotowal. Potwierdza tez: live best == canon pick.
"""
import json, glob, sys
from types import SimpleNamespace

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
import dispatch_v2.dispatch_pipeline as dp
import dispatch_v2.common as C

SINCE = sys.argv[1] if len(sys.argv) > 1 else "2026-06-28T18:16:40"  # restart
UNTIL = sys.argv[2] if len(sys.argv) > 2 else "9999"
q_r6 = float(getattr(C, "PLN_QUALITY_R6_COEFF", 0.5))
q_late = float(getattr(C, "PLN_QUALITY_LATE_COEFF", 0.3))
q_free = float(getattr(C, "PLN_QUALITY_LATE_FREE_MIN", 5.0))


def _pln_q(m):
    pv = m.get("pln_v")
    if not isinstance(pv, (int, float)):
        return float("inf")
    r6 = m.get("objm_r6_breach_max_min") or 0.0
    late = m.get("new_pickup_late_min") or 0.0
    return -(pv - q_r6 * max(0.0, float(r6)) - q_late * max(0.0, float(late) - q_free))


def _stale_bucket(c):
    if dp._is_informed_cand(c):
        return 0
    if dp._is_blind_empty_cand(c) or dp._is_pre_shift_cand(c):
        return 2
    return 1


def _key(c, bf, orig):
    return (1 if dp._late_pickup_tier(c) == 2 else 0, bf(c), _pln_q(c.metrics), orig)


recs = []
for p in sorted(glob.glob("/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl*")):
    for line in open(p, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        ts = r.get("ts") or ""
        if SINCE <= ts < UNTIL and (r.get("best") or {}).get("pln_ab_arm") == "pln":
            recs.append(r)

n_arm = n_ngps = n_live_ne_stale = n_live_eq_canon = 0
for r in recs:
    cands = [r["best"]] + list(r.get("alternatives") or [])
    if len(cands) < 2:
        continue
    n_arm += 1
    objs = [SimpleNamespace(metrics=c, courier_id=c.get("courier_id")) for c in cands]
    if any(o.metrics.get("pos_source") in ("no_gps", "pre_shift") for o in objs):
        n_ngps += 1
    live = objs[0].courier_id  # best = zywy pick (top[0])
    stale = objs[min(range(len(objs)), key=lambda i: _key(objs[i], _stale_bucket, i))].courier_id
    canon = objs[min(range(len(objs)), key=lambda i: _key(objs[i], dp._selection_bucket, i))].courier_id
    if live != stale:
        n_live_ne_stale += 1
    if live == canon:
        n_live_eq_canon += 1

print(f"=== B2 LIVE spot-check (okno {SINCE} .. {UNTIL}) ===")
print(f"E2 'pln' arm rekordow (>=2 kand): {n_arm}")
print(f"  z kandydatem no_gps/pre_shift:   {n_ngps}")
print(f"  ZYWY pick != stary stale-bucket: {n_live_ne_stale}  (= fix zmienia pick na zywo = equal-treatment)")
print(f"  ZYWY pick == canon (_selection): {n_live_eq_canon}/{n_arm}  (powinno ~= all = fix deployed)")
if n_arm == 0:
    print("  UWAGA: 0 rekordow E2-arm po restarcie (maly ruch) — przedluzyc okno.")
