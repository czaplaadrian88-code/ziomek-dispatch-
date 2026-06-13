#!/usr/bin/env python3
"""B5 — GPS / anchor reliability measurement (READ-ONLY).

Mines pos_source distribution + last-known-pos rescue rate + anchor staleness
+ GPS-02 shadow filter telemetry from:
  - shadow_decisions.jsonl  (+ rotated .1)   — per-PROPOSE decision records
  - gps_quality_shadow.jsonl                 — GPS-02 (accuracy+teleport) shadow
  - courier_last_pos.json                    — current last-known-pos store snapshot

Pure measurement. No recommendation. No writes to live data. No flag changes.
Run: /root/.openclaw/venvs/dispatch/bin/python B5_pos_source_measure.py
"""
from __future__ import annotations
import json
import statistics
from collections import Counter, defaultdict

SHADOW_FILES = [
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",  # rotated (older)
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",    # current
]
GPS_QUALITY_LOG = "/root/.openclaw/workspace/dispatch_state/gps_quality_shadow.jsonl"
LAST_POS_STORE = "/root/.openclaw/workspace/dispatch_state/courier_last_pos.json"

# Taxonomy buckets (per the audit ask: live GPS / last-known-store / fiction-center / anchor)
LIVE_GPS = {"gps"}
# anchor = position derived from bag/history geometry (NOT store, NOT raw GPS)
ANCHOR_SOURCES = {
    "last_picked_up_pickup", "last_picked_up_delivery", "last_picked_up_interp",
    "last_assigned_pickup", "last_delivered", "last_picked_up_recent",
}
# post_wave = projected END-of-route position (derived label, not a raw fix); kept separate
PROJECTED = {"post_wave"}
# fiction = synthetic BIALYSTOK_CENTER (no real position)
FICTION = {"no_gps", "pre_shift", "none", None}


def _pctl(sorted_vals, q):
    if not sorted_vals:
        return None
    i = max(0, min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def iter_decisions():
    for path in SHADOW_FILES:
        try:
            f = open(path, "r", encoding="utf-8")
        except FileNotFoundError:
            continue
        with f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    yield path, json.loads(ln)
                except json.JSONDecodeError:
                    continue


def bucket_of(pos_source, pos_from_store):
    if pos_from_store is True:
        return "last_known_store"
    if pos_source in LIVE_GPS:
        return "live_gps"
    if pos_source in ANCHOR_SOURCES:
        return "anchor"
    if pos_source in PROJECTED:
        return "projected_post_wave"
    return "fiction_center"


def main():
    out = []
    p = out.append

    # ---- 1. shadow_decisions: BEST courier pos_source (= the chosen courier) ----
    best_src = Counter()
    best_bucket = Counter()
    first_ts = last_ts = None
    n_dec = 0
    n_best = 0
    store_ages = []                       # pos_age_min where pos_from_store=True (best)
    store_src = Counter()                 # which label store-rescue carried (best)
    gps_age_best = []                     # pos_age_min where best.pos_source == gps
    anchor_age_best = []                  # pos_age_min for anchor best (non-store)
    # per-candidate (best + alternatives) — true environment availability
    cand_src = Counter()
    cand_bucket = Counter()
    n_cand = 0
    n_store_cand = 0
    # how many decisions had ANY gps candidate / ANY store candidate / ANY live-ish
    dec_with_gps_cand = 0
    dec_with_store_cand = 0
    dec_with_any_informed = 0
    dec_all_fiction = 0
    # best-was-store but a gps alt existed (would GPS-02 have changed anything?)
    store_best_with_gps_alt = 0

    for _path, d in iter_decisions():
        n_dec += 1
        ts = d.get("ts")
        if ts:
            if first_ts is None:
                first_ts = ts
            last_ts = ts
        cands = []
        b = d.get("best")
        if isinstance(b, dict):
            cands.append(("best", b))
        for a in (d.get("alternatives") or []):
            if isinstance(a, dict):
                cands.append(("alt", a))

        dec_has_gps = dec_has_store = dec_has_informed = False
        for role, c in cands:
            ps = c.get("pos_source")
            pfs = c.get("pos_from_store")
            bk = bucket_of(ps, pfs)
            n_cand += 1
            cand_src[ps] += 1
            cand_bucket[bk] += 1
            if pfs is True:
                n_store_cand += 1
            if bk in ("live_gps",):
                dec_has_gps = True
            if bk == "last_known_store":
                dec_has_store = True
            if bk in ("live_gps", "anchor", "last_known_store", "projected_post_wave"):
                dec_has_informed = True

        if dec_has_gps:
            dec_with_gps_cand += 1
        if dec_has_store:
            dec_with_store_cand += 1
        if dec_has_informed:
            dec_with_any_informed += 1
        else:
            dec_all_fiction += 1

        if isinstance(b, dict):
            n_best += 1
            ps = b.get("pos_source")
            pfs = b.get("pos_from_store")
            best_src[ps] += 1
            bk = bucket_of(ps, pfs)
            best_bucket[bk] += 1
            age = b.get("pos_age_min")
            if pfs is True:
                store_src[ps] += 1
                if isinstance(age, (int, float)):
                    store_ages.append(age)
                # did a gps alternative exist while best came from store?
                if any(
                    (a.get("pos_source") in LIVE_GPS and a.get("pos_from_store") is not True)
                    for a in (d.get("alternatives") or []) if isinstance(a, dict)
                ):
                    store_best_with_gps_alt += 1
            elif ps in LIVE_GPS and isinstance(age, (int, float)):
                gps_age_best.append(age)
            elif ps in ANCHOR_SOURCES and isinstance(age, (int, float)):
                anchor_age_best.append(age)

    p("=" * 78)
    p("B5 — POS_SOURCE / ANCHOR RELIABILITY MEASUREMENT (read-only)")
    p("=" * 78)
    p(f"shadow_decisions records parsed : {n_dec}")
    p(f"  with a 'best' courier         : {n_best}")
    p(f"  time span                     : {first_ts}  ->  {last_ts}")
    p(f"total candidates (best+alts)    : {n_cand}")
    p("")

    p("-" * 78)
    p("1A. BEST courier pos_source (the courier actually picked) — raw labels")
    p("-" * 78)
    for k, v in best_src.most_common():
        p(f"  {str(k):<26} {v:>5}  ({100*v/max(1,n_best):5.1f}%)")
    p("")
    p("1B. BEST courier — bucketed (audit taxonomy)")
    p("-" * 78)
    order = ["live_gps", "last_known_store", "anchor", "projected_post_wave", "fiction_center"]
    for k in order:
        v = best_bucket.get(k, 0)
        p(f"  {k:<26} {v:>5}  ({100*v/max(1,n_best):5.1f}%)")
    p("")

    p("-" * 78)
    p("2. ALL candidates pos_source (environment availability, best+alternatives)")
    p("-" * 78)
    for k in order:
        v = cand_bucket.get(k, 0)
        p(f"  {k:<26} {v:>5}  ({100*v/max(1,n_cand):5.1f}%)")
    p("")
    p("  raw candidate labels:")
    for k, v in cand_src.most_common():
        p(f"    {str(k):<26} {v:>5}  ({100*v/max(1,n_cand):5.1f}%)")
    p("")

    p("-" * 78)
    p("3. LAST-KNOWN-POS RESCUE (pos_from_store=True) — how often it fires")
    p("-" * 78)
    p(f"  candidates rescued from store : {n_store_cand} / {n_cand} ({100*n_store_cand/max(1,n_cand):.1f}% of candidates)")
    n_best_store = best_bucket.get("last_known_store", 0)
    p(f"  BEST came from store          : {n_best_store} / {n_best} ({100*n_best_store/max(1,n_best):.1f}% of decisions)")
    p(f"  decisions with >=1 store cand : {dec_with_store_cand} / {n_dec} ({100*dec_with_store_cand/max(1,n_dec):.1f}%)")
    p(f"  store-rescue carried label    : {dict(store_src)}")
    if store_ages:
        s = sorted(store_ages)
        p(f"  store-rescued BEST staleness (pos_age_min):")
        p(f"    n={len(s)} min={min(s):.1f} median={statistics.median(s):.1f} "
          f"mean={statistics.mean(s):.1f} p90={_pctl(s,0.9):.1f} max={max(s):.1f}")
        p(f"    TTL=25min invariant -> at/over 20min: {sum(1 for a in s if a>=20)} | "
          f"at/over 25min (MUST be 0): {sum(1 for a in s if a>=25)}")
    p(f"  store-BEST while a GPS alt existed: {store_best_with_gps_alt} "
      f"(rescue chosen over a live-GPS courier — expected rare/0 if GPS scored higher)")
    p("")

    p("-" * 78)
    p("4. STALENESS by best-bucket (pos_age_min) — anchor reliability proxy")
    p("-" * 78)
    for label, arr in (("live_gps best", gps_age_best),
                       ("anchor best (non-store)", anchor_age_best),
                       ("store-rescued best", store_ages)):
        if arr:
            s = sorted(arr)
            p(f"  {label:<26} n={len(s):>4} median={statistics.median(s):5.1f} "
              f"mean={statistics.mean(s):5.1f} p90={_pctl(s,0.9):5.1f} max={max(s):5.1f} min")
        else:
            p(f"  {label:<26} n=0 (no pos_age_min recorded for this bucket)")
    p("  NOTE: anchor sources (last_picked_up_*/last_assigned_pickup) often log")
    p("        pos_age_min=None (point geometry, not a timestamped fix) -> staleness")
    p("        of an anchor is NOT directly recorded here; treat as not-measured.")
    p("")

    p("-" * 78)
    p("5. DECISION-LEVEL POSITION QUALITY")
    p("-" * 78)
    p(f"  decisions with >=1 live-GPS candidate : {dec_with_gps_cand} / {n_dec} ({100*dec_with_gps_cand/max(1,n_dec):.1f}%)")
    p(f"  decisions with >=1 informed candidate : {dec_with_any_informed} / {n_dec} ({100*dec_with_any_informed/max(1,n_dec):.1f}%)")
    p(f"  decisions ALL-fiction (no real pos)   : {dec_all_fiction} / {n_dec} ({100*dec_all_fiction/max(1,n_dec):.1f}%)")
    p("")

    # ---- 6. GPS-02 shadow filter telemetry ----
    p("-" * 78)
    p("6. GPS-02 (accuracy+teleport) SHADOW FILTER — gps_quality_shadow.jsonl")
    p("-" * 78)
    q_n = 0
    q_filter_active = Counter()
    q_accept = Counter()
    q_low_acc = 0
    q_teleport = 0
    q_has_acc = Counter()
    q_acc_vals = []
    q_jump_vals = []
    q_speed_vals = []
    q_anchor_age = []
    q_first = q_last = None
    q_reject_examples = []
    try:
        qf = open(GPS_QUALITY_LOG, "r", encoding="utf-8")
    except FileNotFoundError:
        qf = None
    if qf:
        with qf:
            for ln in qf:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                q_n += 1
                ts = d.get("ts")
                if ts:
                    if q_first is None:
                        q_first = ts
                    q_last = ts
                q_filter_active[d.get("filter_active")] += 1
                acc = d.get("accept")
                q_accept[acc] += 1
                if d.get("low_accuracy"):
                    q_low_acc += 1
                if d.get("teleport"):
                    q_teleport += 1
                q_has_acc[d.get("has_accuracy_field")] += 1
                a = d.get("accuracy_m")
                if isinstance(a, (int, float)):
                    q_acc_vals.append(a)
                j = d.get("jump_km")
                if isinstance(j, (int, float)):
                    q_jump_vals.append(j)
                sp = d.get("implied_speed_kmh")
                if isinstance(sp, (int, float)):
                    q_speed_vals.append(sp)
                aa = d.get("anchor_age_min")
                if isinstance(aa, (int, float)):
                    q_anchor_age.append(aa)
                if acc is False and len(q_reject_examples) < 8:
                    q_reject_examples.append({
                        "kid": d.get("kid"), "reasons": d.get("reasons"),
                        "accuracy_m": d.get("accuracy_m"), "jump_km": d.get("jump_km"),
                        "implied_speed_kmh": d.get("implied_speed_kmh"),
                        "filter_active": d.get("filter_active"),
                    })
    p(f"  records                       : {q_n}")
    p(f"  time span                     : {q_first}  ->  {q_last}")
    p(f"  filter_active distribution    : {dict(q_filter_active)}  (False = pure shadow, no fleet effect)")
    p(f"  accept distribution           : {dict(q_accept)}")
    if q_n:
        n_rej = q_accept.get(False, 0)
        p(f"  WOULD-REJECT (accept=False)   : {n_rej} / {q_n} ({100*n_rej/q_n:.1f}% of assessed GPS fixes)")
        p(f"    of which low_accuracy       : {q_low_acc}")
        p(f"    of which teleport          : {q_teleport}")
    p(f"  has_accuracy_field            : {dict(q_has_acc)}")
    if q_acc_vals:
        s = sorted(q_acc_vals)
        p(f"  accuracy_m (fixes w/ field)   : n={len(s)} median={statistics.median(s):.1f} "
          f"mean={statistics.mean(s):.1f} p90={_pctl(s,0.9):.1f} max={max(s):.1f}  (threshold {150.0:.0f}m)")
    if q_jump_vals:
        s = sorted(q_jump_vals)
        p(f"  jump_km (anchor present)      : n={len(s)} median={statistics.median(s):.2f} "
          f"p90={_pctl(s,0.9):.2f} max={max(s):.2f}  (teleport jump threshold 2.0km)")
    if q_speed_vals:
        s = sorted(q_speed_vals)
        p(f"  implied_speed_kmh (computable): n={len(s)} median={statistics.median(s):.1f} "
          f"p90={_pctl(s,0.9):.1f} max={max(s):.1f}  (teleport speed threshold 120km/h)")
    if q_anchor_age:
        s = sorted(q_anchor_age)
        p(f"  teleport-anchor age (min)     : n={len(s)} median={statistics.median(s):.1f} "
          f"max={max(s):.1f}  (usable only <=8.0min)")
    p(f"  reject examples (<=8)         :")
    for ex in q_reject_examples:
        p(f"    {ex}")
    if q_n and q_accept.get(False, 0) == 0:
        p("  -> ZERO would-rejects in the shadow window: nothing for the flip to act on yet.")
    p("")

    # ---- 7. current last-known-pos store snapshot ----
    p("-" * 78)
    p("7. courier_last_pos.json — current store snapshot (point-in-time)")
    p("-" * 78)
    try:
        store = json.load(open(LAST_POS_STORE, "r", encoding="utf-8"))
    except Exception as e:
        store = {}
        p(f"  (could not read store: {e})")
    if isinstance(store, dict):
        src_c = Counter(v.get("source") for v in store.values() if isinstance(v, dict))
        p(f"  entries now                   : {len(store)}")
        p(f"  source distribution           : {dict(src_c)}")
    p("")
    p("=" * 78)
    p("END B5 measurement")
    p("=" * 78)

    print("\n".join(out))


if __name__ == "__main__":
    main()
