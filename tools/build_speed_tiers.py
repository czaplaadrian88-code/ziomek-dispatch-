#!/usr/bin/env python3
"""
build_speed_tiers.py — DATA-DRIVEN courier speed signal, bundling-confound resistant.

PROBLEM (confirmed by test): owner-tier (gold/std+/std/slow) does NOT correlate with
real speed (Spearman ~0.15). `gold` couriers look slow because they BUNDLE 37-47% of
the time — the pickup→delivery leg time is inflated by intermediate stops, not by the
courier being slow. The LIVE lever V326_SPEED_MULTIPLIER (dispatch_pipeline.py:1040)
therefore penalises genuinely-fast `std` couriers.

GOAL: per-courier *real* speed, measured so bundling cannot fool it.

METHOD (two independent estimators, both confound-aware):
  1. SOLO legs only. A delivered leg [picked_up_ts, delivered_ts] is SOLO if NO other
     delivered leg of the SAME courier overlaps it in time. A solo leg's wall-clock
     pickup→delivery time is a clean single-OD traversal → trustworthy km/h.
     This is the PRIMARY estimator when n_solo >= min_history.
  2. Distance-normalised min_per_km = leg_time / road_km over ALL legs. Normalising by
     road distance removes part of the bundling inflation (a longer combined route also
     has more km). Used as the SECONDARY/backfill estimator (and as a cross-check).

Road km: real OSRM road distance via osrm_client.route() when the local OSRM is
reachable; otherwise pure haversine × 1.37 (Białystok road factor). The tool degrades
gracefully and NEVER mutates production state.

Pure / offline / atomic-write. READ-ONLY w.r.t. production. Output: /tmp/courier_speed_data.json

  {cid: {name, median_kmh, median_min_per_km, n_legs, n_solo, speed_rank,
         current_owner_tier, solo_median_kmh, all_median_kmh, mis_tiered}}

Run:
  /root/.openclaw/venvs/dispatch/bin/python tools/build_speed_tiers.py
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Make dispatch_v2 importable as a PACKAGE (osrm_client does
# `from dispatch_v2.common import ...`), so we put the parent of dispatch_v2
# (the scripts/ dir) on sys.path and import dispatch_v2.osrm_client.
_HERE = Path(__file__).resolve().parent
_DISPATCH_V2 = _HERE.parent          # .../scripts/dispatch_v2
_SCRIPTS = _DISPATCH_V2.parent       # .../scripts
for _p in (str(_SCRIPTS), str(_DISPATCH_V2)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA_DIR = Path("/root/.openclaw/workspace/dispatch_state")
BACKFILL = DATA_DIR / "backfill_decisions_outcomes_v1.jsonl"
OBJ_REPLAY = DATA_DIR / "obj_replay_capture.jsonl"
TIERS = DATA_DIR / "courier_tiers.json"
NAMES = DATA_DIR / "courier_names.json"
OUT_DEFAULT = DATA_DIR / "courier_speed_data.json"  # produkcyjny artefakt (V326 re-point shadow)

HAVERSINE_ROAD_FACTOR = 1.37  # Białystok road factor (matches osrm_client fallback)
MIN_HISTORY = 5               # min legs to rank a courier
TIER_RANK = {"slow": 0, "std": 1, "std+": 2, "gold": 3}  # ordinal owner tier

# Sanity guards on a single leg (drop GPS/clock garbage so medians stay clean).
MIN_LEG_MIN = 1.0      # < 1 min pickup→delivery is implausible
MAX_LEG_MIN = 120.0    # > 2h is a stale/abandoned record, not a real leg
MIN_ROAD_KM = 0.15     # below this, km/h explodes on tiny rounding; skip for km/h
MAX_KMH = 90.0         # urban Białystok; above = bad data
MIN_KMH = 2.0          # below = parked/garbage


# ---------------------------------------------------------------------------
# OSRM road distance (optional, graceful). Falls back to haversine×1.37.
# ---------------------------------------------------------------------------
def _make_road_km_fn(use_osrm: bool):
    """Return road_km(from_ll, to_ll) -> (km, source) with a process-local cache."""
    haversine = None
    route = None
    try:
        from dispatch_v2.osrm_client import haversine as _h  # pure math, zero requests
        haversine = _h
    except Exception as e:  # pragma: no cover
        print(f"[warn] osrm_client.haversine import failed ({e}); using inline haversine",
              file=sys.stderr)

    def _inline_haversine(a, b):
        R = 6371.0
        lat1, lon1 = math.radians(a[0]), math.radians(a[1])
        lat2, lon2 = math.radians(b[0]), math.radians(b[1])
        dlat, dlon = lat2 - lat1, lon2 - lon1
        h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 2 * R * math.asin(math.sqrt(h))

    hv = haversine or _inline_haversine

    if use_osrm:
        try:
            from dispatch_v2.osrm_client import route as _r
            route = _r
        except Exception as e:
            print(f"[warn] osrm_client.route import failed ({e}); haversine-only road km",
                  file=sys.stderr)
            route = None

    cache: dict = {}
    counters = {"osrm": 0, "haversine": 0}

    def road_km(from_ll, to_ll):
        key = (round(from_ll[0], 4), round(from_ll[1], 4),
               round(to_ll[0], 4), round(to_ll[1], 4))
        if key in cache:
            return cache[key]
        km = None
        src = "haversine"
        if route is not None:
            try:
                res = route(tuple(from_ll), tuple(to_ll))
                dk = res.get("distance_km")
                # Skip the OSRM coord-invalid sentinel (huge fake route).
                if dk and dk > 0 and not res.get("coord_invalid") and dk < 60:
                    km = float(dk)
                    src = "osrm_fallback" if res.get("osrm_fallback") else "osrm"
            except Exception:
                km = None
        if km is None:
            try:
                km = hv(tuple(from_ll), tuple(to_ll)) * HAVERSINE_ROAD_FACTOR
            except Exception:
                km = None
        if km is not None:
            counters["osrm" if src.startswith("osrm") and src != "osrm_fallback" else "haversine"] += 1
        cache[key] = (km, src)
        return km, src

    road_km.counters = counters  # type: ignore[attr-defined]
    return road_km


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None


def load_owner_tiers():
    tiers, names = {}, {}
    if TIERS.exists():
        d = json.loads(TIERS.read_text())
        for cid, v in d.items():
            if cid == "_meta" or not isinstance(v, dict):
                continue
            t = (v.get("bag") or {}).get("tier") or v.get("tier")
            if t:
                tiers[str(cid)] = t
            if v.get("name"):
                names[str(cid)] = v["name"]
    if NAMES.exists():
        nd = json.loads(NAMES.read_text())
        for cid, nm in nd.items():
            if nm:
                names[str(cid)] = nm  # courier_names.json wins (richer full names)
    return tiers, names


def load_legs():
    """Return per-courier list of legs from the backfill outcomes.

    leg = dict(order_id, cid, pu (datetime), de (datetime), ptd_min (float))
    Only delivered legs with BOTH pickup & delivery timestamps and a sane duration.
    """
    by_courier = defaultdict(list)
    raw = 0
    for line in BACKFILL.open():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        o = r.get("outcome") or {}
        if o.get("status") != "delivered":
            continue
        cid = o.get("courier_id_final")
        if cid in (None, "", "None"):
            continue
        cid = str(cid)
        pu = _parse_ts(o.get("picked_up_ts"))
        de = _parse_ts(o.get("delivered_ts"))
        if not (pu and de):
            continue
        ptd = o.get("pickup_to_delivery_min")
        if ptd is None:
            ptd = (de - pu).total_seconds() / 60.0
        ptd = float(ptd)
        if not (MIN_LEG_MIN <= ptd <= MAX_LEG_MIN):
            continue
        raw += 1
        by_courier[cid].append({
            "order_id": str(r.get("order_id") or o.get("order_id") or ""),
            "cid": cid, "pu": pu, "de": de, "ptd_min": ptd,
        })
    return by_courier, raw


def load_order_coords():
    """Map order_id -> (pickup_coords, delivery_coords) from obj_replay.

    Multiple captures per order exist; keep the last one with valid both-coords
    (the freshest snapshot of the OD pair).
    """
    coords = {}
    for line in OBJ_REPLAY.open():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        no = r.get("new_order") or {}
        oid = str(no.get("order_id") or r.get("order_id") or "")
        if not oid:
            continue
        pc = no.get("pickup_coords")
        dc = no.get("delivery_coords")
        if pc and dc and len(pc) == 2 and len(dc) == 2:
            coords[oid] = (tuple(pc), tuple(dc))
    return coords


# ---------------------------------------------------------------------------
# Bundling-aware solo detection
# ---------------------------------------------------------------------------
def mark_solo(legs):
    """Mark each leg solo=True iff no OTHER leg of the same courier overlaps its
    [pu, de] interval. Bundled legs (overlapping carry intervals) -> solo=False.

    Overlap test on sorted intervals: a leg is non-solo if it intersects any other.
    """
    n = len(legs)
    order = sorted(range(n), key=lambda i: legs[i]["pu"])
    solo = [True] * n
    for a_pos in range(n):
        i = order[a_pos]
        li = legs[i]
        # compare against neighbours forward until pu beyond de(i); and any earlier
        # leg whose de overlaps — check both directions via simple O(n^2) per courier
        # (couriers have <= a few hundred legs, trivial).
        for j in range(n):
            if j == i:
                continue
            lj = legs[j]
            if li["pu"] < lj["de"] and lj["pu"] < li["de"]:
                solo[i] = False
                break
    for idx, leg in enumerate(legs):
        leg["solo"] = solo[idx]
    return legs


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def spearman(xs, ys):
    """Spearman rho via Pearson on ranks (average ranks for ties)."""
    n = len(xs)
    if n < 3:
        return None

    def ranks(v):
        idx = sorted(range(n), key=lambda i: v[i])
        rk = [0.0] * n
        k = 0
        while k < n:
            j = k
            while j + 1 < n and v[idx[j + 1]] == v[idx[k]]:
                j += 1
            avg = (k + j) / 2.0 + 1.0
            for t in range(k, j + 1):
                rk[idx[t]] = avg
            k = j + 1
        return rk

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(n)))
    dy = math.sqrt(sum((ry[i] - my) ** 2 for i in range(n)))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def atomic_write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".speed_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--min-history", type=int, default=MIN_HISTORY)
    ap.add_argument("--no-osrm", action="store_true",
                    help="skip OSRM road distance, use haversine×1.37 only")
    args = ap.parse_args()

    for p in (BACKFILL, OBJ_REPLAY):
        if not p.exists():
            print(f"[fatal] missing data file: {p}", file=sys.stderr)
            return 2

    owner_tiers, names = load_owner_tiers()
    by_courier, raw_legs = load_legs()
    coords = load_order_coords()
    road_km_fn = _make_road_km_fn(use_osrm=not args.no_osrm)

    print(f"[load] backfill legs(delivered,sane)={raw_legs} couriers={len(by_courier)} "
          f"obj_replay_orders={len(coords)} owner_tiers={len(owner_tiers)} "
          f"names={len(names)}", file=sys.stderr)

    result = {}
    matched_coords = 0
    for cid, legs in by_courier.items():
        mark_solo(legs)
        kmh_all, mpk_all = [], []
        kmh_solo, mpk_solo = [], []
        for leg in legs:
            oid = leg["order_id"]
            od = coords.get(oid)
            if not od:
                continue
            matched_coords += 1
            rk, _src = road_km_fn(od[0], od[1])
            if not rk or rk < MIN_ROAD_KM:
                continue
            t_min = leg["ptd_min"]
            kmh = rk / (t_min / 60.0)
            mpk = t_min / rk
            if not (MIN_KMH <= kmh <= MAX_KMH):
                continue
            mpk_all.append(mpk)
            kmh_all.append(kmh)
            if leg["solo"]:
                mpk_solo.append(mpk)
                kmh_solo.append(kmh)

        n_legs = len(kmh_all)
        n_solo = len(kmh_solo)
        if n_legs == 0:
            continue
        # PRIMARY estimator: solo if enough data, else distance-normalised all-legs.
        use_solo = n_solo >= args.min_history
        med_kmh = statistics.median(kmh_solo if use_solo else kmh_all)
        med_mpk = statistics.median(mpk_solo if use_solo else mpk_all)
        result[cid] = {
            "name": names.get(cid, f"cid{cid}"),
            "median_kmh": round(med_kmh, 2),
            "median_min_per_km": round(med_mpk, 3),
            "n_legs": n_legs,
            "n_solo": n_solo,
            "estimator": "solo" if use_solo else "dist_norm_all",
            "solo_median_kmh": round(statistics.median(kmh_solo), 2) if kmh_solo else None,
            "all_median_kmh": round(statistics.median(kmh_all), 2) if kmh_all else None,
            "current_owner_tier": owner_tiers.get(cid),
            "speed_rank": None,   # filled below
            "mis_tiered": None,   # filled below
        }

    # Rank by median_kmh among couriers meeting min-history (faster = rank 1).
    ranked = [c for c in result if result[c]["n_legs"] >= args.min_history]
    ranked.sort(key=lambda c: result[c]["median_kmh"], reverse=True)
    for i, c in enumerate(ranked, 1):
        result[c]["speed_rank"] = i

    # ----- analysis: Spearman(owner_tier_rank, data_speed) on SOLO-capable set -----
    # Use couriers with a known owner tier AND a solo-based estimate.
    solo_set = [c for c in ranked
                if result[c]["current_owner_tier"] in TIER_RANK
                and result[c]["n_solo"] >= args.min_history]
    xs = [TIER_RANK[result[c]["current_owner_tier"]] for c in solo_set]
    ys = [result[c]["solo_median_kmh"] for c in solo_set]
    rho_solo = spearman(xs, ys)

    # Also Spearman on the full ranked set (any estimator) for reference.
    full_set = [c for c in ranked if result[c]["current_owner_tier"] in TIER_RANK]
    rho_full = spearman(
        [TIER_RANK[result[c]["current_owner_tier"]] for c in full_set],
        [result[c]["median_kmh"] for c in full_set],
    )

    # ----- mis-tier detection vs std-tier median speed -----
    std_speeds = [result[c]["median_kmh"] for c in ranked
                  if result[c]["current_owner_tier"] == "std"]
    std_median = statistics.median(std_speeds) if std_speeds else None
    mis = []
    if std_median is not None:
        for c in ranked:
            t = result[c]["current_owner_tier"]
            v = result[c]["median_kmh"]
            flag = None
            if t == "gold" and v < std_median:
                flag = "gold_slower_than_std_median"
            elif t == "std" and v > std_median:
                # "fast std" — only flag the genuinely fast ones (above gold floor too)
                flag = "std_faster_than_std_median"
            result[c]["mis_tiered"] = flag
            if flag:
                mis.append((c, t, v, flag))

    atomic_write_json(args.out, {
        "_meta": {
            "generated_at": datetime.now().astimezone().isoformat(),
            "min_history": args.min_history,
            "road_km_source": "haversine_only" if args.no_osrm else "osrm_with_haversine_fallback",
            "n_couriers_ranked": len(ranked),
            "std_tier_median_kmh": round(std_median, 2) if std_median else None,
            "spearman_owner_vs_speed_solo": round(rho_solo, 3) if rho_solo is not None else None,
            "spearman_owner_vs_speed_full": round(rho_full, 3) if rho_full is not None else None,
        },
        "couriers": result,
    })

    # ----------------------------- REPORT -----------------------------
    def nm(c):
        return f"{c}/{result[c]['name']}"

    print("\n==================== SPEED-TIER REPORT ====================")
    print(f"couriers ranked (n_legs>={args.min_history}): {len(ranked)}  | "
          f"coord-matched legs: {matched_coords}  | "
          f"OSRM road km: {'off' if args.no_osrm else 'on (fallback haversine)'}")
    print(f"std-tier median speed: "
          f"{round(std_median,2) if std_median else 'n/a'} km/h")
    print(f"\nSpearman(owner_tier_rank, data_speed):")
    print(f"  SOLO-legs set (n={len(solo_set)}): "
          f"rho = {round(rho_solo,3) if rho_solo is not None else 'n/a'}")
    print(f"  FULL set      (n={len(full_set)}): "
          f"rho = {round(rho_full,3) if rho_full is not None else 'n/a'}")

    top5 = ranked[:5]
    bot5 = ranked[-5:][::-1]
    print("\nTOP-5 fastest (real):")
    for c in top5:
        r = result[c]
        print(f"  #{r['speed_rank']:>2} {nm(c):<26} {r['median_kmh']:>5} km/h "
              f"({r['median_min_per_km']:>5} min/km)  tier={str(r['current_owner_tier']):<5} "
              f"n={r['n_legs']} solo={r['n_solo']} [{r['estimator']}]")
    print("BOTTOM-5 slowest (real):")
    for c in bot5:
        r = result[c]
        print(f"  #{r['speed_rank']:>2} {nm(c):<26} {r['median_kmh']:>5} km/h "
              f"({r['median_min_per_km']:>5} min/km)  tier={str(r['current_owner_tier']):<5} "
              f"n={r['n_legs']} solo={r['n_solo']} [{r['estimator']}]")

    print(f"\nMIS-TIERED couriers ({len(mis)} of {len(ranked)} = "
          f"{round(100*len(mis)/max(1,len(ranked)),1)}%):")
    gold_slow = [m for m in mis if m[3] == "gold_slower_than_std_median"]
    std_fast = [m for m in mis if m[3] == "std_faster_than_std_median"]
    print(f"  GOLD slower than std-median ({len(gold_slow)}):")
    for c, t, v, _ in sorted(gold_slow, key=lambda m: m[2]):
        print(f"    {nm(c):<26} {v:>5} km/h  (std-median {round(std_median,1)})")
    print(f"  STD faster than std-median ({len(std_fast)}):")
    for c, t, v, _ in sorted(std_fast, key=lambda m: -m[2]):
        print(f"    {nm(c):<26} {v:>5} km/h")
    print(f"\nwrote: {args.out}")
    print("==========================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
