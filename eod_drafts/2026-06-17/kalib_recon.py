#!/usr/bin/env python3
"""KALIBRACJA recon — schema + pool distribution (READ-ONLY, stdlib json only).
Inspects backfill / obj_replay_capture / shadow_decisions to design the
shortage-regime isolation + replay harness. No dispatch imports, no prod touch.
"""
import json, collections
from datetime import datetime, timezone

DS = "/root/.openclaw/workspace/dispatch_state"
LOGS = "/root/.openclaw/workspace/scripts/logs"
BACKFILL = f"{DS}/backfill_decisions_outcomes_v1.jsonl"
CAPTURE = f"{DS}/obj_replay_capture.jsonl"
SHADOW = [f"{LOGS}/shadow_decisions.jsonl.1", f"{LOGS}/shadow_decisions.jsonl"]


def pts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def first_record(path):
    for line in open(path, "rb"):
        try:
            return json.loads(line)
        except Exception:
            continue
    return None


def sect(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


# ---------- BACKFILL ----------
sect("BACKFILL decisions->outcomes")
r = first_record(BACKFILL)
print("keys:", sorted(r.keys()))
if isinstance(r.get("outcome"), dict):
    print("outcome keys:", sorted(r["outcome"].keys()))
n = 0
poolf = collections.Counter()
poolt = collections.Counter()
tiers = collections.Counter()
flags_seen = collections.Counter()
tss = []
has = collections.Counter()
INTERESTING = ["pool_feasible", "pool_total", "tier", "czasowka", "best_effort",
               "late_pickup", "predicted_r6_max_bag_min", "predicted_travel_min",
               "predicted_drive_min", "proposed_score", "score_margin", "verdict",
               "koord", "saturation", "no_candidates"]
for line in open(BACKFILL, "rb"):
    try:
        d = json.loads(line)
    except Exception:
        continue
    n += 1
    for k in INTERESTING:
        if k in d and d[k] is not None:
            has[k] += 1
    pf = d.get("pool_feasible")
    if isinstance(pf, (int, float)):
        poolf[int(pf)] += 1
    ptt = d.get("pool_total")
    if isinstance(ptt, (int, float)):
        poolt[int(ptt)] += 1
    if d.get("tier") is not None:
        tiers[str(d.get("tier"))] += 1
    t = pts(d.get("decision_ts") or d.get("ts"))
    if t:
        tss.append(t)
print(f"rows={n}")
print("field presence (of INTERESTING):", dict(has))
if tss:
    tss.sort()
    print(f"ts range: {tss[0]} .. {tss[-1]}")
print("pool_feasible histogram:", dict(sorted(poolf.items())[:20]))
print("pool_total histogram:", dict(sorted(poolt.items())[:20]))
print("tier dist:", dict(tiers))
print("SAMPLE:", json.dumps(r, ensure_ascii=False)[:1200])

# ---------- OBJ_REPLAY_CAPTURE ----------
sect("OBJ_REPLAY_CAPTURE geometry")
r = first_record(CAPTURE)
print("keys:", sorted(r.keys()))
groups = collections.defaultdict(int)
keyset = collections.Counter()
ncap = 0
capts = []
sample_group_key = None
for line in open(CAPTURE, "rb"):
    try:
        d = json.loads(line)
    except Exception:
        continue
    ncap += 1
    for k in d.keys():
        keyset[k] += 1
    gid = (d.get("ts"), str(d.get("order_id")))
    groups[gid] += 1
    t = pts(d.get("ts"))
    if t:
        capts.append(t)
print(f"rows={ncap}  distinct (ts,order_id) groups={len(groups)}")
gsz = collections.Counter(groups.values())
print("candidates-per-group histogram (size:count):", dict(sorted(gsz.items())[:25]))
multi = sum(1 for v in groups.values() if v >= 2)
print(f"groups with >=2 candidates: {multi} ({100.0*multi/max(1,len(groups)):.1f}%)")
print("key presence (all):", dict(keyset))
if capts:
    capts.sort()
    print(f"ts range: {capts[0]} .. {capts[-1]}")
# dump one multi-candidate group fully (compact per candidate)
best_g = max(groups, key=lambda k: groups[k])
print(f"\nLARGEST group {best_g} has {groups[best_g]} candidates; dumping its records:")
shown = 0
for line in open(CAPTURE, "rb"):
    try:
        d = json.loads(line)
    except Exception:
        continue
    if (d.get("ts"), str(d.get("order_id"))) == best_g:
        # compact: show candidate-identifying + geometry fields
        comp = {k: d.get(k) for k in ("courier_id", "courier_pos", "bag",
                "new_order", "now", "dwell_pickup", "dwell_dropoff",
                "pickup_ready_at", "score", "chosen", "selected", "is_best",
                "feasible", "pos_source", "tier") if k in d}
        # truncate bag/new_order to len/coords
        if isinstance(comp.get("bag"), list):
            comp["bag_n"] = len(comp["bag"])
            comp.pop("bag", None)
        print("  ", json.dumps(comp, ensure_ascii=False)[:500])
        shown += 1
        if shown >= 12:
            break

# ---------- SHADOW_DECISIONS ----------
sect("SHADOW_DECISIONS proposals")
verdicts = collections.Counter()
shts = []
best_keys = None
alt_keys = None
top_keys = None
nprop = 0
for path in SHADOW:
    try:
        f = open(path, "rb")
    except FileNotFoundError:
        continue
    for line in f:
        try:
            d = json.loads(line)
        except Exception:
            continue
        verdicts[d.get("verdict")] += 1
        t = pts(d.get("ts"))
        if t:
            shts.append(t)
        if d.get("verdict") == "PROPOSE" and isinstance(d.get("best"), dict):
            nprop += 1
            if best_keys is None:
                top_keys = sorted(d.keys())
                best_keys = sorted(d["best"].keys())
                alts = d.get("alternatives")
                if isinstance(alts, list) and alts and isinstance(alts[0], dict):
                    alt_keys = sorted(alts[0].keys())
    f.close()
print("verdict dist:", dict(verdicts))
print(f"PROPOSE w/ best: {nprop}")
if shts:
    shts.sort()
    print(f"ts range: {shts[0]} .. {shts[-1]}")
print("top-level keys:", top_keys)
print("best keys:", best_keys)
print("alternatives[0] keys:", alt_keys)
print("\nDONE")
