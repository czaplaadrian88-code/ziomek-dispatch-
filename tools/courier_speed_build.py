#!/usr/bin/env python3
"""
courier_speed_build.py — per-courier REAL/OSRM drive-time multiplier, MOTION-ONLY.

WHY THIS EXISTS (the DRIVE_MIN_V2 artefact)
-------------------------------------------
A previous attempt computed the multiplier from leg-level wall-clock deltas, e.g.
    actual_assign_to_pickup_min / osrm_drive_min
On THIS data that ratio has median ~5.3 and max ~629 — pure garbage. The reason is
that the actual wall-clock of a leg includes dwell (parked at the restaurant / at the
customer), waiting, and the tail of a previous delivery — NONE of which OSRM models
(OSRM is free-flow driving only). Mixing dwell into "driving" inflates and destabilises
the multiplier. That is the artefact we must NOT reproduce.

HONEST METHOD
-------------
The only file with enough resolution to separate *moving* from *standing* is the GPS
quality shadow log (`gps_quality_shadow.jsonl`): one record per GPS fix, per courier,
with an already-computed `implied_speed_kmh`, `jump_km` (distance from previous fix),
`fix_age_min`, `accept`, `teleport`, `low_accuracy`. We walk each courier's fixes in
time order and classify every consecutive fix-pair as a SEGMENT:

  GAP    — dt between fixes too long (> GAP_MAX_MIN) OR an endpoint is rejected /
           teleport / low_accuracy / NaN. We cannot trust what happened in between,
           so the segment is dropped entirely (neither distance nor time counted).
  DWELL  — the courier is essentially standing: implied_speed_kmh <= DWELL_KMH and the
           displacement jump_km is tiny. This is the postój under the restaurant /
           customer that fooled DRIVE_MIN_V2. Dropped from "driving".
  MOTION — implied_speed_kmh > MOTION_KMH and within a sane urban cap (<= MAX_KMH).
           Only these segments count toward the multiplier.

Per courier we sum MOTION distance D_motion (km) and MOTION time T_motion (min). The
multiplier compares the moving portion against what OSRM would have spent on the SAME
distance at its own free-flow reference speed REF_KMH:

    osrm_equiv_min = D_motion / REF_KMH * 60
    mult           = T_motion / osrm_equiv_min      ( == REF_KMH / actual_motion_kmh )

So a courier that moves at OSRM's free-flow pace gets mult ~ 1.0; a courier slowed by
traffic/lights gets mult > 1.0; a fast courier gets mult < 1.0. Because numerator and
denominator span the SAME (motion-only) distance, dwell and gaps cannot inflate it.

REF_KMH default (28.2) is OSRM's OWN median implied free-flow km/h on this corpus
(derived from predicted.km_to_pickup / predicted.drive_min pairs) — so mult is anchored
to OSRM's units, not an arbitrary constant. Override with --ref-kmh.

ROBUSTNESS
----------
  * median over 30d  + EWMA (recency-weighted) per courier.
  * SHRINKAGE toward 1.0 for small samples: mult_shrunk = 1.0 + (mult-1.0)*n/(n+K).
    A courier with few motion-minutes is pulled back to "OSRM is right" until proven.
  * Stability gate (--validate / always reported): split the window into two halves and
    measure whether each courier's multiplier agrees across halves. If the per-courier
    multiplier is NOT stable across time it is SHADOW (an artefact), not a real signal.

This tool is PURE / OFFLINE / READ-ONLY w.r.t. production. It only writes its own
output JSON via atomic replace. It never imports or mutates engine modules.

Run:
  python3 tools/courier_speed_build.py
  python3 tools/courier_speed_build.py --validate --days 30 --ref-kmh 28.2
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths (all under dispatch_state; output is our own artefact).
# --------------------------------------------------------------------------- #
DATA_DIR = Path("/root/.openclaw/workspace/dispatch_state")
GPS_SHADOW = DATA_DIR / "gps_quality_shadow.jsonl"        # per-fix trace (motion source)
ENRICHED = DATA_DIR / "drive_min_enriched.jsonl"          # per-leg actual/osrm + courier id
NAMES = DATA_DIR / "courier_names.json"                   # cid -> display name (optional)
OUT_DEFAULT = DATA_DIR / "courier_speed_mult.json"

# --------------------------------------------------------------------------- #
# Tunables (all overridable on the CLI). Defaults chosen from the data probe.
# --------------------------------------------------------------------------- #
MOTION_KMH = 5.0      # a fix-pair faster than this counts as MOTION (task: ">5 km/h")
DWELL_KMH = 5.0       # at/under this AND tiny displacement => DWELL (parked at a node)
DWELL_JUMP_KM = 0.10  # ~100 m: displacement under this near ~0 speed => standing still
MAX_KMH = 90.0        # urban Białystok hard cap; above = GPS garbage, drop the segment
GAP_MAX_MIN = 3.0     # fixes more than this apart => GAP (untrusted), drop segment
REF_KMH = 28.2        # OSRM's own median implied free-flow km/h on this corpus
SHRINK_K = 120.0      # shrinkage strength (in motion-minutes); larger => more caution
MIN_MOTION_MIN = 8.0  # below this many motion-minutes a courier is "small sample"
EWMA_HALF_LIFE_DAYS = 7.0  # recency half-life for the EWMA estimator


def _parse_ts(s):
    """Parse an ISO-8601 timestamp (with or without tz / fractional sec) -> aware UTC."""
    if not s or not isinstance(s, str):
        return None
    t = s.strip().replace("Z", "+00:00")
    # Some records use a space separator ("2026-06-05 13:08:25"); normalise.
    if "T" not in t and " " in t:
        t = t.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        # last resort: trim fractional seconds beyond microseconds
        try:
            base, _, frac = t.partition(".")
            dt = datetime.fromisoformat(base)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# GPS fix loading + segment classification.
# --------------------------------------------------------------------------- #
def load_fixes(path: Path, since=None):
    """Stream the GPS shadow log -> {cid: [fix, ...]} sorted by time.

    A fix is a small dict with the fields we need. Bad lines are skipped (fail-soft).
    """
    by_cid: dict[str, list] = defaultdict(list)
    if not path.exists():
        return by_cid
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = o.get("kid")
            if cid is None:
                continue
            cid = str(cid)
            ts = _parse_ts(o.get("ts"))
            if ts is None:
                continue
            if since is not None and ts < since:
                continue
            pos = o.get("pos")
            lat = lon = None
            if isinstance(pos, (list, tuple)) and len(pos) == 2:
                try:
                    lat, lon = float(pos[0]), float(pos[1])
                except (TypeError, ValueError):
                    lat = lon = None
            by_cid[cid].append(
                {
                    "ts": ts,
                    "lat": lat,
                    "lon": lon,
                    "speed": o.get("implied_speed_kmh"),
                    "jump_km": o.get("jump_km"),
                    "fix_age_min": o.get("fix_age_min"),
                    "accept": bool(o.get("accept", True)),
                    "teleport": bool(o.get("teleport", False)),
                    "low_accuracy": bool(o.get("low_accuracy", False)),
                }
            )
    for cid in by_cid:
        by_cid[cid].sort(key=lambda f: f["ts"])
    return by_cid


def classify_segment(a: dict, b: dict, cfg) -> tuple[str, float, float]:
    """Classify the fix-pair (a -> b). Returns (kind, dt_min, dist_km).

    kind in {"GAP", "DWELL", "MOTION"}. For GAP we return (kind, 0, 0) — the segment is
    discarded. For DWELL/MOTION we return the real dt and distance so the caller can
    accumulate. `b` carries implied_speed_kmh and jump_km computed by the GPS layer.
    """
    dt_min = (b["ts"] - a["ts"]).total_seconds() / 60.0
    if dt_min <= 0:
        return ("GAP", 0.0, 0.0)

    # Bad-GPS endpoint => untrusted, drop regardless of timing.
    if not b["accept"] or b["teleport"] or b["low_accuracy"]:
        return ("GAP", 0.0, 0.0)

    # Distance for this pair: prefer the GPS-layer jump_km on b; else haversine.
    dist = b.get("jump_km")
    if not isinstance(dist, (int, float)) or dist < 0:
        if None in (a["lat"], a["lon"], b["lat"], b["lon"]):
            return ("GAP", 0.0, 0.0)
        dist = _haversine(a["lat"], a["lon"], b["lat"], b["lon"])
    dist = float(dist)

    spd = b.get("speed")
    if not isinstance(spd, (int, float)) or spd < 0:
        # derive from dist/dt if the layer didn't give one
        spd = dist / (dt_min / 60.0) if dt_min > 0 else 0.0
    spd = float(spd)

    # MOTION sanity cap: implausibly fast => GPS garbage, not a real moving segment.
    if spd > cfg.max_kmh:
        return ("GAP", 0.0, 0.0)

    # DWELL: standing at a node — ~zero speed AND tiny displacement. We test this BEFORE
    # the gap cut-off: a courier parked at the restaurant keeps emitting fixes, sometimes
    # several minutes apart; that displacement-free interval is genuine dwell (postój),
    # NOT an untrusted gap, and counting it as dwell is the whole point (it is what fooled
    # DRIVE_MIN_V2). We only require the displacement to stay tiny over the interval.
    if spd <= cfg.dwell_kmh and dist <= cfg.dwell_jump_km:
        return ("DWELL", dt_min, dist)

    # GAP: a DISPLACED pair separated by more than the trust window. We don't know the
    # path taken in between, so neither distance nor time may count as motion.
    if dt_min > cfg.gap_max_min:
        return ("GAP", 0.0, 0.0)

    # MOTION: genuinely moving.
    if spd > cfg.motion_kmh:
        return ("MOTION", dt_min, dist)

    # In-between (e.g. 5–slightly, but displaced more than dwell jump): treat as
    # slow-crawl MOTION so we don't silently drop real driving in congestion.
    if dist > cfg.dwell_jump_km:
        return ("MOTION", dt_min, dist)
    return ("DWELL", dt_min, dist)


def _haversine(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


# --------------------------------------------------------------------------- #
# Per-courier accumulation -> motion km / motion min -> multiplier samples.
# --------------------------------------------------------------------------- #
def courier_motion_samples(fixes: list, cfg):
    """Walk one courier's fixes -> list of per-segment (ts, dt_min, dist_km) MOTION
    samples + accumulators. We keep per-segment samples so we can split by time for the
    stability gate and compute EWMA by recency.
    """
    motion = []          # list of (ts, dt_min, dist_km) for MOTION segments
    dwell_min = 0.0
    gap_count = 0
    n_motion_seg = 0
    for a, b in zip(fixes, fixes[1:]):
        kind, dt_min, dist = classify_segment(a, b, cfg)
        if kind == "MOTION":
            motion.append((b["ts"], dt_min, dist))
            n_motion_seg += 1
        elif kind == "DWELL":
            dwell_min += dt_min
        else:  # GAP
            gap_count += 1
    return motion, dwell_min, gap_count, n_motion_seg


def mult_from_motion(motion_segs, cfg) -> tuple[float, float, float]:
    """(mult, motion_min, motion_km) from a list of (ts, dt_min, dist_km) MOTION segs.

    mult = motion_min / (motion_km / REF_KMH * 60). Returns (nan,0,0) if no distance.
    """
    t = sum(s[1] for s in motion_segs)
    d = sum(s[2] for s in motion_segs)
    if d <= 0 or t <= 0:
        return (float("nan"), t, d)
    osrm_equiv = d / cfg.ref_kmh * 60.0
    if osrm_equiv <= 0:
        return (float("nan"), t, d)
    return (t / osrm_equiv, t, d)


def ewma_mult(motion_segs, cfg, now=None) -> float:
    """Recency-weighted multiplier: each motion segment weighted by its distance AND an
    exponential recency decay (half-life EWMA_HALF_LIFE_DAYS). Returns the distance-and-
    recency-weighted actual km/h converted to a multiplier vs REF_KMH.
    """
    if not motion_segs:
        return float("nan")
    if now is None:
        now = max(s[0] for s in motion_segs)
    lam = math.log(2.0) / max(cfg.ewma_half_life_days, 0.01)
    num = 0.0  # weighted motion minutes
    den = 0.0  # weighted osrm-equiv minutes
    for ts, dt_min, dist in motion_segs:
        age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
        w = math.exp(-lam * age_days)
        num += w * dt_min
        den += w * (dist / cfg.ref_kmh * 60.0)
    if den <= 0:
        return float("nan")
    return num / den


def shrink(mult: float, motion_min: float, cfg) -> float:
    """Pull `mult` toward 1.0 for small samples (in motion-minutes)."""
    if not math.isfinite(mult):
        return float("nan")
    w = motion_min / (motion_min + cfg.shrink_k)
    return 1.0 + (mult - 1.0) * w


# --------------------------------------------------------------------------- #
# Stability gate: split window in half, compare per-courier multiplier.
# --------------------------------------------------------------------------- #
def stability_report(by_cid_motion: dict, cfg) -> dict:
    """For each courier with motion in BOTH halves, compute mult_h1, mult_h2 and the
    relative delta. Aggregate into a verdict: stable (real signal) vs unstable (artefact).
    """
    # global time span over all motion segments
    all_ts = [s[0] for segs in by_cid_motion.values() for s in segs]
    if not all_ts:
        return {"verdict": "no_data", "couriers_both_halves": 0, "pairs": []}
    t0, t1 = min(all_ts), max(all_ts)
    mid = t0 + (t1 - t0) / 2

    pairs = []
    for cid, segs in by_cid_motion.items():
        h1 = [s for s in segs if s[0] < mid]
        h2 = [s for s in segs if s[0] >= mid]
        m1, mm1, _ = mult_from_motion(h1, cfg)
        m2, mm2, _ = mult_from_motion(h2, cfg)
        # require a minimum motion sample in EACH half to judge stability
        if not (math.isfinite(m1) and math.isfinite(m2)):
            continue
        if mm1 < cfg.min_motion_min or mm2 < cfg.min_motion_min:
            continue
        rel = abs(m1 - m2) / max(1e-9, (m1 + m2) / 2.0)
        pairs.append(
            {"courier_id": cid, "mult_h1": round(m1, 3), "mult_h2": round(m2, 3),
             "motion_min_h1": round(mm1, 1), "motion_min_h2": round(mm2, 1),
             "rel_delta": round(rel, 3)}
        )

    out = {
        "split_at": mid.isoformat(),
        "window": [t0.isoformat(), t1.isoformat()],
        "couriers_both_halves": len(pairs),
        "pairs": sorted(pairs, key=lambda p: p["rel_delta"]),
    }
    if len(pairs) >= 2:
        h1s = [p["mult_h1"] for p in pairs]
        h2s = [p["mult_h2"] for p in pairs]
        allm = h1s + h2s
        out["pearson_r"] = round(_pearson(h1s, h2s), 3)
        out["spearman_r"] = round(_spearman(h1s, h2s), 3)
        out["median_rel_delta"] = round(statistics.median(p["rel_delta"] for p in pairs), 3)
        out["mean_rel_delta"] = round(statistics.mean(p["rel_delta"] for p in pairs), 3)
        # Between-courier spread: if every courier's multiplier is nearly the same, there
        # is no PER-COURIER signal to extract — only a shared global multiplier. We report
        # the coefficient of variation so the reader can tell "real per-courier trait" from
        # "one number for the whole fleet".
        gmean = statistics.mean(allm)
        gstd = statistics.pstdev(allm)
        out["fleet_mult_mean"] = round(gmean, 3)
        out["between_courier_cv"] = round(gstd / gmean, 3) if gmean else None
        r = out.get("pearson_r")
        mrd = out["median_rel_delta"]
        cv = out["between_courier_cv"] or 0.0
        # The credibility gate, in order of what kills a per-courier signal first:
        #   1. no between-courier spread => nothing to personalise; the correlation sign is
        #      meaningless on a flat field. Use ONE fleet multiplier.
        #   2. negative correlation => the fast/slow ordering FLIPS between halves: the
        #      spread is shuffled noise, NOT a per-courier trait. Never call this a signal.
        #   3. only a positive correlation AND modest half-to-half error is a real
        #      per-courier signal worth a per-courier multiplier.
        if cv < 0.08:
            # halves agree on "everyone ~same" (or there is no variance at all);
            # the only trustworthy output is the fleet mean.
            out["verdict"] = "NO_PER_COURIER_SIGNAL_use_fleet_mult"
        elif r is None or not math.isfinite(r):
            out["verdict"] = "INSUFFICIENT_no_variance"
        elif r < 0:
            out["verdict"] = "UNSTABLE_rank_flips_artefact_shadow_only"
        elif r >= 0.5 and mrd <= 0.15:
            out["verdict"] = "STABLE_real_signal"
        elif r >= 0.3:
            out["verdict"] = "WEAK_use_with_shrinkage"
        else:
            out["verdict"] = "UNSTABLE_artefact_shadow_only"
    elif len(pairs) == 1:
        out["verdict"] = "INSUFFICIENT_one_courier"
    else:
        out["verdict"] = "INSUFFICIENT_no_courier_in_both_halves"
    return out


def _rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return float("nan")
    return num / (dx * dy)


def _spearman(xs, ys):
    if len(xs) < 2:
        return float("nan")
    return _pearson(_rank(xs), _rank(ys))


# --------------------------------------------------------------------------- #
# Driver.
# --------------------------------------------------------------------------- #
class Cfg:
    """Plain config bag so functions stay pure and easily testable."""

    def __init__(self, **kw):
        self.motion_kmh = kw.get("motion_kmh", MOTION_KMH)
        self.dwell_kmh = kw.get("dwell_kmh", DWELL_KMH)
        self.dwell_jump_km = kw.get("dwell_jump_km", DWELL_JUMP_KM)
        self.max_kmh = kw.get("max_kmh", MAX_KMH)
        self.gap_max_min = kw.get("gap_max_min", GAP_MAX_MIN)
        self.ref_kmh = kw.get("ref_kmh", REF_KMH)
        self.shrink_k = kw.get("shrink_k", SHRINK_K)
        self.min_motion_min = kw.get("min_motion_min", MIN_MOTION_MIN)
        self.ewma_half_life_days = kw.get("ewma_half_life_days", EWMA_HALF_LIFE_DAYS)


def build(cfg: Cfg, days: int, gps_path: Path = GPS_SHADOW, names_path: Path = NAMES):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    fixes_by_cid = load_fixes(gps_path, since=since)

    names = {}
    if names_path.exists():
        try:
            names = json.loads(names_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            names = {}

    table = {}
    motion_by_cid = {}
    for cid, fixes in fixes_by_cid.items():
        motion, dwell_min, gaps, n_seg = courier_motion_samples(fixes, cfg)
        motion_by_cid[cid] = motion
        mult, motion_min, motion_km = mult_from_motion(motion, cfg)
        ew = ewma_mult(motion, cfg)
        shrunk = shrink(mult, motion_min, cfg)
        actual_kmh = (motion_km / (motion_min / 60.0)) if motion_min > 0 else float("nan")
        small = motion_min < cfg.min_motion_min
        table[cid] = {
            "courier_id": cid,
            "name": names.get(cid) or names.get(str(cid)),
            "mult_median": _r(mult),
            "ewma": _r(ew),
            "shrunk": _r(shrunk),
            "n": n_seg,                       # number of MOTION segments
            "n_fixes": len(fixes),
            "motion_min": _r(motion_min, 1),
            "motion_km": _r(motion_km, 2),
            "dwell_min": _r(dwell_min, 1),
            "gap_segments": gaps,
            "actual_motion_kmh": _r(actual_kmh, 1),
            "small_sample": small,
        }

    stab = stability_report(motion_by_cid, cfg)

    # Fleet-wide multiplier: pool ALL motion segments and compute one number. This is the
    # most robust output when per-courier ranking is not stable (see stability verdict) —
    # it just says "OSRM under/over-estimates urban drive time by X% across the fleet".
    all_segs = [s for segs in motion_by_cid.values() for s in segs]
    fleet_mult, fleet_min, fleet_km = mult_from_motion(all_segs, cfg)
    fleet = {
        "fleet_mult": _r(fleet_mult),
        "fleet_motion_min": _r(fleet_min, 1),
        "fleet_motion_km": _r(fleet_km, 2),
        "fleet_actual_kmh": _r((fleet_km / (fleet_min / 60.0)) if fleet_min > 0 else float("nan"), 1),
        "osrm_drive_min_bias_pct": _r((fleet_mult - 1.0) * 100.0 if math.isfinite(fleet_mult) else float("nan"), 1),
        "n_couriers": len(table),
    }
    return table, stab, fleet


def _r(x, nd=3):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return None
    return round(float(x), nd)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30, help="lookback window (default 30)")
    ap.add_argument("--ref-kmh", type=float, default=REF_KMH, help="OSRM free-flow reference km/h")
    ap.add_argument("--motion-kmh", type=float, default=MOTION_KMH)
    ap.add_argument("--dwell-kmh", type=float, default=DWELL_KMH)
    ap.add_argument("--shrink-k", type=float, default=SHRINK_K)
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--gps", default=str(GPS_SHADOW))
    ap.add_argument("--validate", action="store_true", help="print the stability gate verdict")
    ap.add_argument("--dry-run", action="store_true", help="do not write the output file")
    args = ap.parse_args(argv)

    cfg = Cfg(ref_kmh=args.ref_kmh, motion_kmh=args.motion_kmh,
              dwell_kmh=args.dwell_kmh, shrink_k=args.shrink_k)
    table, stab, fleet = build(cfg, args.days, gps_path=Path(args.gps))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "motion_only_gps_segments",
        "ref_kmh": cfg.ref_kmh,
        "params": {
            "motion_kmh": cfg.motion_kmh, "dwell_kmh": cfg.dwell_kmh,
            "dwell_jump_km": cfg.dwell_jump_km, "max_kmh": cfg.max_kmh,
            "gap_max_min": cfg.gap_max_min, "shrink_k": cfg.shrink_k,
            "min_motion_min": cfg.min_motion_min, "days": args.days,
        },
        "fleet_summary": fleet,
        "stability": stab,
        "couriers": table,
    }

    if not args.dry_run:
        _atomic_write_json(Path(args.out), payload)

    # Human report (stderr-free; this is a CLI artefact).
    mults = [v["shrunk"] for v in table.values() if v["shrunk"] is not None]
    print(f"[courier_speed_build] couriers={len(table)} with_motion_mult={len(mults)}")
    if mults:
        mults_sorted = sorted(mults)
        print(f"  shrunk mult: min {mults_sorted[0]:.2f}  med {statistics.median(mults_sorted):.2f}  max {mults_sorted[-1]:.2f}")
    print(f"  fleet mult: {fleet.get('fleet_mult')}  "
          f"(OSRM drive-min bias {fleet.get('osrm_drive_min_bias_pct')}%, "
          f"motion {fleet.get('fleet_motion_min')} min / {fleet.get('fleet_motion_km')} km)")
    print(f"  stability verdict: {stab.get('verdict')}  "
          f"(couriers_both_halves={stab.get('couriers_both_halves')}, "
          f"pearson_r={stab.get('pearson_r')}, between_courier_cv={stab.get('between_courier_cv')}, "
          f"median_rel_delta={stab.get('median_rel_delta')})")
    if not args.dry_run:
        print(f"  wrote {args.out}")
    if args.validate:
        print(json.dumps(stab, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
