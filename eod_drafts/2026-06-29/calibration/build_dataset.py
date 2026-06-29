#!/usr/bin/env python3
"""
FOUNDATION dataset builder for Ziomek dispatch calibration.
READ-ONLY on repo. Writes ONLY to scratchpad.

Spine = backfill_decisions_outcomes_v1.jsonl (13d, richest decision features).
Left-join: shadow_decisions (load/bag/alts), decision_outcomes (clean UTC r6),
sla_log (master outcomes, NAIVE Warsaw), gps_delivery_truth (physical UTC),
eta_calibration (predicted/real delivery min + bag), ready_at (prep/dwell),
drive_min_calibration_v2 (drive cal).

TZ rule: space_naive / ISO-without-offset timestamps in sla & eta_cal are
NAIVE WARSAW -> convert to UTC (DST-aware via Europe/Warsaw). Everything else
(decision-side, decision_outcomes, gps_truth, ready_at, predicted_delivered_at)
is already UTC/aware.
"""
import json, re
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import defaultdict

WAW = ZoneInfo("Europe/Warsaw")
UTC = ZoneInfo("UTC")
BASE = "/root/.openclaw/workspace"
OUT = "/tmp/claude-0/-root/f14f1e5b-ad36-45b3-941e-c61aa4e524a1/scratchpad"

def parse_ts(s, naive_is_warsaw=True):
    """Return aware UTC datetime or None. Handles ISO-aware, ISO-naive, 'YYYY-MM-DD HH:MM:SS'."""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    try:
        if "T" in s and ("+" in s or s.endswith("Z")):
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.astimezone(UTC)
        # naive (space or ISO-naive)
        s2 = s.replace("T", " ")
        # strip fractional if present
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(\.\d+)?", s2)
        if not m:
            return None
        dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        if m.group(2):
            dt = dt.replace(microsecond=int(float("0"+m.group(2))*1e6))
        if naive_is_warsaw:
            dt = dt.replace(tzinfo=WAW)
        else:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None

def iso(dt):
    return dt.isoformat() if dt else None

def minutes_between(a, b):
    """b - a in minutes (a,b aware dt)."""
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 60.0

# ---------------------------------------------------------------- load sources
def load_jsonl(path):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out

print("Loading sources...")
backfill = load_jsonl(f"{BASE}/dispatch_state/backfill_decisions_outcomes_v1.jsonl")
shadow   = load_jsonl(f"{BASE}/scripts/logs/shadow_decisions.jsonl")
dec_out  = load_jsonl(f"{BASE}/dispatch_state/decision_outcomes.jsonl")
sla      = load_jsonl(f"{BASE}/scripts/logs/sla_log.jsonl")
gps      = load_jsonl(f"{BASE}/dispatch_state/gps_delivery_truth.jsonl")
etacal   = load_jsonl(f"{BASE}/dispatch_state/eta_calibration_log.jsonl")
readyat  = load_jsonl(f"{BASE}/dispatch_state/ready_at_log.jsonl")
drivemin = load_jsonl(f"{BASE}/dispatch_state/drive_min_calibration_log_v2.jsonl")

# ------------------------------------------------- dedup helpers (last wins)
def index_last(rows, key, tsfield, tsparse=lambda x: x):
    """Index by str(key); keep row with max tsfield (string compare on ts)."""
    idx = {}
    best_ts = {}
    for r in rows:
        k = r.get(key)
        if k is None:
            continue
        k = str(k)
        t = r.get(tsfield)
        tv = str(t) if t is not None else ""
        if k not in idx or tv >= best_ts[k]:
            idx[k] = r
            best_ts[k] = tv
    return idx

# backfill: dedup by order_id, last decision_ts
bf_idx = index_last(backfill, "order_id", "decision_ts")
sh_idx = index_last(shadow, "order_id", "ts")
do_idx = index_last(dec_out, "order_id", "written_at")
sla_idx = index_last(sla, "order_id", "logged_at")
gps_idx = index_last(gps, "order_id", "physical_delivered_at")
eta_idx = index_last(etacal, "oid", "logged_at")
ra_idx = index_last(readyat, "order_id", "ts")
dr_idx = index_last(drivemin, "order_id", "ts")

print(f"unique: backfill={len(bf_idx)} shadow={len(sh_idx)} dec_out={len(do_idx)} "
      f"sla={len(sla_idx)} gps={len(gps_idx)} eta={len(eta_idx)} ready={len(ra_idx)} drive={len(dr_idx)}")

# ------------------------------------------------- load_bucket logic
def load_bucket_from_ewma(e):
    if e <= 2.0: return "luzno"
    if e <= 2.7: return "srednio"
    if e <= 3.5: return "ciasno"
    return "niedobor"

def load_bucket_from_pool(pf):
    if pf is None: return None, None
    if pf >= 3: return "luzno", "pool_feasible"
    if pf == 2: return "srednio", "pool_feasible"   # pool can't separate srednio/ciasno
    return "niedobor", "pool_feasible"               # pf<=1

# ------------------------------------------------- build joined rows
rows_out = []
for oid, bf in bf_idx.items():
    sh = sh_idx.get(oid)
    do = do_idx.get(oid)
    sl = sla_idx.get(oid)
    gp = gps_idx.get(oid)
    et = eta_idx.get(oid)
    ra = ra_idx.get(oid)
    dr = dr_idx.get(oid)
    shb = (sh or {}).get("best") or {}
    bfout = bf.get("outcome") or {}

    # ---- decision-side timestamps (UTC)
    decision_ts = parse_ts(bf.get("decision_ts"), naive_is_warsaw=False)
    dec_hour_waw = decision_ts.astimezone(WAW).hour if decision_ts else None
    dec_hour_utc = decision_ts.hour if decision_ts else None

    # ---- LOAD metrics
    load_ewma = shb.get("loadgov_load_ewma") if shb else None
    load_now = shb.get("loadgov_load_now") if shb else None
    active_couriers = shb.get("loadgov_active_couriers") if shb else None
    active_orders = shb.get("loadgov_active_orders") if shb else None
    fleet_bag_avg = shb.get("v326_fleet_bag_avg") if shb else None
    pool_feasible = bf.get("pool_feasible")
    pool_total = bf.get("pool_total")
    if pool_feasible is None and sh:
        pool_feasible = sh.get("pool_feasible_count")
    if pool_total is None and sh:
        pool_total = sh.get("pool_total_count")

    if load_ewma is not None:
        load_bucket = load_bucket_from_ewma(load_ewma)
        load_source = "ewma"
    else:
        load_bucket, load_source = load_bucket_from_pool(pool_feasible)

    # ---- tier / courier class
    tier = bf.get("tier")
    if tier is None and sh:
        arc = sh.get("auto_route_context") or {}
        tier = arc.get("auto_route_tier_best")
    if tier is None and dr:
        tier = dr.get("tier")

    # ---- bag size: prefer eta_cal (wide coverage) then shadow
    bag_size = None
    bag_source = None
    if et and et.get("bag_size") is not None:
        bag_size = et.get("bag_size"); bag_source = "eta_cal"
    elif shb and shb.get("bag_size_before") is not None:
        bag_size = shb.get("bag_size_before"); bag_source = "shadow_bag_before"
    is_bundle = et.get("is_bundle") if et else None
    if is_bundle is None and bag_size is not None:
        is_bundle = bag_size >= 2

    # ---- predicted times (decision side)
    predicted_travel_min = bf.get("predicted_travel_min")
    predicted_drive_min = bf.get("predicted_drive_min")
    predicted_r6_max_bag_min = bf.get("predicted_r6_max_bag_min")
    predicted_delivery_min = et.get("predicted_delivery_min") if et else None
    predicted_delivered_at = parse_ts(et.get("predicted_delivered_at"), naive_is_warsaw=False) if et else None
    calibrated_drive_min = dr.get("calibrated_drive_min") if dr else None
    raw_drive_min = dr.get("raw_drive_min") if dr else None

    # ---- ACTUAL outcome timestamps
    picked_up_utc = None
    pickup_source = None
    for src, val, naive in [
        ("decision_outcomes", (do or {}).get("picked_up_at"), False),
        ("ready_at", (ra or {}).get("picked_up_at_iso"), False),
        ("backfill_outcome", bfout.get("picked_up_ts"), False),
        ("sla", (sl or {}).get("picked_up_at"), True),
    ]:
        dt = parse_ts(val, naive_is_warsaw=naive)
        if dt:
            picked_up_utc = dt; pickup_source = src; break

    delivered_utc = None
    delivered_source = None
    for src, val, naive in [
        ("gps_physical", (gp or {}).get("physical_delivered_at"), False),
        ("decision_outcomes", (do or {}).get("delivered_at"), False),
        ("backfill_outcome", bfout.get("delivered_ts"), False),
        ("sla", (sl or {}).get("delivered_at"), True),
    ]:
        dt = parse_ts(val, naive_is_warsaw=naive)
        if dt:
            delivered_utc = dt; delivered_source = src; break

    # ---- actual delivery DURATION (pickup->delivery), TZ-invariant where possible
    actual_delivery_min = None
    actual_delivery_min_source = None
    # priority: eta_cal real_delivery_min == sla duration (universal); then sla; then backfill outcome; then computed
    if et and et.get("real_delivery_min") is not None:
        actual_delivery_min = et.get("real_delivery_min"); actual_delivery_min_source = "eta_cal_real"
    elif sl and sl.get("delivery_time_minutes") is not None:
        actual_delivery_min = sl.get("delivery_time_minutes"); actual_delivery_min_source = "sla"
    elif bfout.get("pickup_to_delivery_min") is not None:
        actual_delivery_min = bfout.get("pickup_to_delivery_min"); actual_delivery_min_source = "backfill_outcome"
    elif picked_up_utc and delivered_utc:
        actual_delivery_min = minutes_between(picked_up_utc, delivered_utc); actual_delivery_min_source = "computed_utc"

    # gps-based duration cross-check (physical delivered - picked_up)
    actual_delivery_min_gps = None
    if gp and picked_up_utc:
        pd = parse_ts(gp.get("physical_delivered_at"), naive_is_warsaw=False)
        actual_delivery_min_gps = minutes_between(picked_up_utc, pd)

    # ---- ETA ERROR  (positive = engine OPTIMISTIC: delivered LATER than predicted)
    # AUTHORITATIVE (span-safe) = actual_delivered_at_utc - predicted_delivered_at_utc.
    # Matches eta_calibration_logger headline metric (delivered_at - predicted_delivered_at).
    # NOTE: prompt wrote "predicted - actual" but labelled positive=optimistic; the
    # load-bearing semantic (positive=optimistic=under-promised) == actual - predicted.
    eta_error_min = None
    if predicted_delivered_at is not None and delivered_utc is not None:
        eta_error_min = round(minutes_between(predicted_delivered_at, delivered_utc), 3)
    # secondary duration-based error (carries ~+11min span offset of per_order_delivery_times)
    eta_error_dur_min = None
    if predicted_delivery_min is not None and actual_delivery_min is not None:
        eta_error_dur_min = round(actual_delivery_min - predicted_delivery_min, 3)
    # gps-physical timestamp variant (preferred actual when geofence present)
    eta_error_min_gps = None
    if predicted_delivered_at is not None and gp and gp.get("physical_delivered_at"):
        pd_phys = parse_ts(gp.get("physical_delivered_at"), naive_is_warsaw=False)
        eta_error_min_gps = round(minutes_between(predicted_delivered_at, pd_phys), 3)

    # ---- pickup slip & dwell (from ready_at, all UTC)
    declared_ready = parse_ts((ra or {}).get("declared_ready_iso"), naive_is_warsaw=False) if ra else None
    arrived_at = parse_ts((ra or {}).get("arrived_at_iso"), naive_is_warsaw=False) if ra else None
    ra_pickup = parse_ts((ra or {}).get("picked_up_at_iso"), naive_is_warsaw=False) if ra else None
    pickup_slip_min = None
    if ra_pickup and declared_ready:
        pickup_slip_min = round(minutes_between(declared_ready, ra_pickup), 3)
    # fallback: decision_outcomes pickup_lateness_min
    pickup_lateness_do = (do or {}).get("pickup_lateness_min")
    dwell_actual_min = None
    if ra_pickup and arrived_at:
        dwell_actual_min = round(minutes_between(arrived_at, ra_pickup), 3)
    prep_bias_min = (ra or {}).get("prep_bias_min")
    wait_min = (ra or {}).get("wait_min")

    # ---- KOORD flagging (backfill is all PROPOSE, but cross-check decision_outcomes)
    proposed_cid = bf.get("proposed_courier_id")
    do_verdict = (do or {}).get("verdict")
    do_proposed_cid = (do or {}).get("proposed_cid")
    is_propose = bool(proposed_cid) and bf.get("verdict") == "PROPOSE"
    is_koord = (do_verdict in (None, "no_verdict", "KOORD")) and not do_proposed_cid if do else False

    # ---- r6 actual / breach
    r6_actual_min = (do or {}).get("r6_actual_min")
    r6_breach = (do or {}).get("r6_breach")

    row = {
        "order_id": oid,
        # decision-side
        "decision_ts_utc": iso(decision_ts),
        "decision_hour_warsaw": dec_hour_waw,
        "decision_hour_utc": dec_hour_utc,
        "restaurant": bf.get("restaurant"),
        "verdict": bf.get("verdict"),
        "is_propose": is_propose,
        "is_koord": is_koord,
        "proposed_courier_id": proposed_cid,
        "proposed_score": bf.get("proposed_score"),
        "courier_id_final": bfout.get("courier_id_final") or (do or {}).get("picked_up_courier"),
        "auto_route": bf.get("auto_route"),
        # courier class / load / bag
        "tier": tier,
        "pos_source": bf.get("pos_source") or (shb.get("pos_source") if shb else None),
        "load_ewma": load_ewma,
        "load_now": load_now,
        "load_active_couriers": active_couriers,
        "load_active_orders": active_orders,
        "fleet_bag_avg": fleet_bag_avg,
        "pool_feasible": pool_feasible,
        "pool_total": pool_total,
        "load_bucket": load_bucket,
        "load_source": load_source,
        "bag_size": bag_size,
        "bag_source": bag_source,
        "is_bundle": is_bundle,
        "czasowka": bf.get("czasowka"),
        "best_effort": bf.get("best_effort"),
        "shift_end_edge": bf.get("shift_end_edge"),
        "score_margin": bf.get("score_margin"),
        # predicted
        "predicted_travel_min": predicted_travel_min,
        "predicted_drive_min": predicted_drive_min,
        "predicted_r6_max_bag_min": predicted_r6_max_bag_min,
        "predicted_delivery_min": predicted_delivery_min,
        "predicted_delivered_at_utc": iso(predicted_delivered_at),
        "raw_drive_min": raw_drive_min,
        "calibrated_drive_min": calibrated_drive_min,
        # actual outcome
        "picked_up_at_utc": iso(picked_up_utc),
        "pickup_source": pickup_source,
        "delivered_at_utc": iso(delivered_utc),
        "delivered_source": delivered_source,
        "actual_delivery_min": actual_delivery_min,
        "actual_delivery_min_source": actual_delivery_min_source,
        "actual_delivery_min_gps": round(actual_delivery_min_gps, 3) if actual_delivery_min_gps is not None else None,
        "assign_to_delivery_min": bfout.get("assign_to_delivery_min"),
        "assign_to_pickup_min": bfout.get("assign_to_pickup_min"),
        "outcome_status": bfout.get("status"),
        "sla_ok": (sl or {}).get("sla_ok"),
        "r6_actual_min": r6_actual_min,
        "r6_breach": r6_breach,
        "gps_confidence": (gp or {}).get("confidence"),
        "gps_dwell_min": (gp or {}).get("dwell_min"),
        # KEY derived
        "eta_error_min": eta_error_min,            # timestamp-based, +=optimistic (AUTHORITATIVE)
        "eta_error_min_gps": eta_error_min_gps,    # same vs gps physical delivered
        "eta_error_dur_min": eta_error_dur_min,    # duration-based (has ~+11min span offset; secondary)
        "pickup_slip_min": pickup_slip_min,
        "pickup_lateness_do_min": pickup_lateness_do,
        "dwell_actual_min": dwell_actual_min,
        "prep_bias_min": prep_bias_min,
        "wait_min": wait_min,
        # prov / extras
        "has_shadow": sh is not None,
        "has_gps_truth": gp is not None,
        "n_alternatives": len(sh.get("alternatives") or []) if sh else None,
        "order_type": (ra or {}).get("order_type"),
        "eta_cal_bucket": (et or {}).get("bucket"),
        "drive_peak_window": (dr or {}).get("peak_window"),
    }
    # carry alternatives (compact) for the weight-track
    if sh and sh.get("alternatives"):
        alts = []
        for a in sh["alternatives"]:
            alts.append({
                "courier_id": a.get("courier_id"),
                "score": a.get("score"),
                "feasibility": a.get("feasibility"),
                "best_effort": a.get("best_effort"),
                "travel_min": a.get("travel_min"),
                "travel_min_cal": a.get("travel_min_cal"),
                "drive_min": a.get("drive_min"),
                "bundle_bonus": a.get("bundle_bonus"),
                "bonus_penalty_sum": a.get("bonus_penalty_sum"),
                "r6_max_bag_time_min": a.get("r6_max_bag_time_min"),
                "r6_bag_size": a.get("r6_bag_size"),
                "pos_source": a.get("pos_source"),
            })
        row["alternatives"] = alts
    rows_out.append(row)

# sort by decision_ts
rows_out.sort(key=lambda r: r["decision_ts_utc"] or "")

outpath = f"{OUT}/decisions_outcomes_loadbucketed.jsonl"
with open(outpath, "w") as f:
    for r in rows_out:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"WROTE {len(rows_out)} rows -> {outpath}")
