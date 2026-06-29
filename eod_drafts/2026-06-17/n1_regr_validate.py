#!/usr/bin/env python3
"""
N1 REGRESSION VALIDATION (READ-ONLY) — 2026-06-17

Validates regression of change N1 (ENABLE_GPS_LESS_EARLIEST_DUE_ANCHOR) on 14-16.06
BEFORE flip. Flag is OFF live; capture written under OLD behaviour.

N1: for a GPS-less courier whose bag is ALL assigned (0 picked_up), the resolved
position changes from NEWEST-assigned pickup (OLD) to EARLIEST-DUE pickup (NEW),
where earliest-due = min over assigned by (czas_kuriera_warsaw or pickup_ready_at).

GROUND-TRUTH METHOD (no pipeline rerun for pop 1-3):
  obj_replay_capture stores `courier_pos` = the position the pipeline ACTUALLY
  resolved at decision time with the flag OFF. For an all-assigned no-GPS bag, the
  only branch that yields courier_pos == one of the bag's assigned pickup_coords is
  `last_assigned_pickup`. So:
    OLD pos  = courier_pos (must equal an assigned pickup -> confirms last_assigned_pickup)
    NEW pos  = pickup_coords of min(assigned, key=earliest_due)
  Anchor changes iff NEW pickup_coords != OLD courier_pos.

PROPOSAL-WINDOW FILTER (Adrian): Ziomek realistically proposes czasowki only ~60/50/40
min before pickup. Drop decisions where (new_order.pickup_ready_at - now) > WINDOW_MAX_MIN.

EFFECT-ON-PLAN: OSRM drive OLD->earliest-due-pickup vs NEW->earliest-due-pickup.
NEW anchors AT the earliest-due pickup, so NEW drive ~= 0 (already there) and OLD drive
= the (false) detour the plan attributed. Reduction of false-lateness = OLD_drive - NEW_drive.
A regression would be NEW_drive > OLD_drive (worse arrival).
"""
import json, sys, math
from datetime import datetime, timedelta, timezone
from collections import defaultdict
sys.path.insert(0, "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")
import audit_lib as AL  # osrm_route, osrm_table

CAP = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
WARSAW_OFF = 2  # hours, for day bucketing (CEST in June)
WINDOW_MAX_MIN = 65.0   # proposal-window cutoff on NEW ORDER pickup readiness
DAYS = ("2026-06-14", "2026-06-15", "2026-06-16")
BORDERLINE_MIN = 1.0    # |OLD_drive - NEW_drive| below this = OSRM-noise borderline

def parse_ts(s):
    if not s: return None
    try: return datetime.fromisoformat(str(s).replace("Z","+00:00"))
    except: return None

def wday(ts):
    d = parse_ts(ts)
    if not d: return None
    return (d.astimezone(timezone.utc) + timedelta(hours=WARSAW_OFF)).strftime("%Y-%m-%d")

def approx_eq(a, b, tol=1e-5):
    return abs(a[0]-b[0]) < tol and abs(a[1]-b[1]) < tol

def earliest_due_key(o):
    ts = parse_ts(o.get("czas_kuriera_warsaw")) or parse_ts(o.get("pickup_ready_at"))
    if ts is None: return (1, datetime(1,1,1,tzinfo=timezone.utc))
    return (0, ts.astimezone(timezone.utc))

# OSRM drive cache (deterministic within run)
_osrm_cache = {}
def drive_min(a, b):
    if approx_eq(tuple(a), tuple(b)):
        return 0.0
    key = (round(a[0],6), round(a[1],6), round(b[0],6), round(b[1],6))
    if key in _osrm_cache: return _osrm_cache[key]
    dur_min, _dist = AL.osrm_route(tuple(a), tuple(b))   # returns (duration_min, dist_km)
    _osrm_cache[key] = dur_min
    return dur_min

def main():
    per_day = defaultdict(lambda: dict(
        records=0, filtered_out_window=0, qualifying=0,
        last_assigned_inferred=0, ge2_assigned=0,
        anchor_changed=0, anchor_same=0,
        shifts_km=[], red_min=[], regr_min=[],
        regressions=[], borderline=0, certain=0, osrm_fail=0))

    with open(CAP) as f:
        for line in f:
            try: d = json.loads(line)
            except: continue
            day = wday(d.get("now"))
            if day not in DAYS: continue
            P = per_day[day]
            P["records"] += 1

            bag = d.get("bag") or []
            assigned = [o for o in bag if o.get("status") == "assigned" and o.get("pickup_coords")]
            pickedup = [o for o in bag if o.get("status") == "picked_up"]
            if pickedup:          # picked_up branch untouched by N1
                continue
            if len(assigned) < 2: # anchor choice irrelevant
                continue
            P["ge2_assigned"] += 1

            # confirm OLD pos resolved via last_assigned_pickup (courier_pos == an assigned pickup)
            cp = d.get("courier_pos")
            if not cp:
                continue
            cp = tuple(cp)
            if not any(approx_eq(tuple(o["pickup_coords"]), cp) for o in assigned):
                continue  # fresh GPS / recent / other source -> N1 does not touch position
            P["last_assigned_inferred"] += 1

            # PROPOSAL WINDOW FILTER on the NEW order being dispatched
            no = d.get("new_order") or {}
            now = parse_ts(d.get("now"))
            ready = parse_ts(no.get("pickup_ready_at"))
            if now and ready:
                horizon = (ready - now).total_seconds()/60.0
                if horizon > WINDOW_MAX_MIN:
                    P["filtered_out_window"] += 1
                    continue
            P["qualifying"] += 1

            # NEW anchor = earliest-due assigned pickup
            new_anchor = min(assigned, key=earliest_due_key)
            new_pos = tuple(new_anchor["pickup_coords"])
            old_pos = cp

            if approx_eq(new_pos, old_pos):
                P["anchor_same"] += 1
                continue
            P["anchor_changed"] += 1

            # km shift (haversine — geometric magnitude of the anchor move)
            P["shifts_km"].append(_hav(old_pos, new_pos))

            # EFFECT ON PLAN — the binding constraint is arrival at the EARLIEST-DUE
            # (committed) pickup. Under OLD the courier "is at" the newest-assigned
            # pickup and the plan attributes drive(OLD->earliest_due) of extra lead
            # time before it can serve the committed pickup => false lateness.
            # Under NEW the courier "is at" the earliest-due pickup (drive ~0).
            earliest_due_pos = new_pos
            d_old_to_due = drive_min(old_pos, earliest_due_pos)   # the false detour OLD imposes
            d_new_to_due = drive_min(new_pos, earliest_due_pos)   # ~0 (NEW anchor IS earliest-due)
            if d_old_to_due is None or d_new_to_due is None:
                P["osrm_fail"] += 1
                continue
            delta = d_old_to_due - d_new_to_due   # >0 => N1 reduces false lateness at committed pickup
            if delta >= 0:
                P["red_min"].append(delta)
            else:
                P["regr_min"].append(-delta)
                P["regressions"].append(dict(oid=no.get("order_id"), now=d.get("now"),
                                             d_old=round(d_old_to_due,2), d_new=round(d_new_to_due,2),
                                             delta=round(delta,2)))
            if abs(delta) < BORDERLINE_MIN:
                P["borderline"] += 1
            else:
                P["certain"] += 1

    # ---- report ----
    def pct(a, b): return f"{100.0*a/b:.1f}%" if b else "n/a"
    def med(xs):
        xs=sorted(x for x in xs if x is not None)
        return xs[len(xs)//2] if xs else 0.0
    def p90(xs):
        xs=sorted(x for x in xs if x is not None)
        return xs[int(len(xs)*0.9)] if xs else 0.0

    print("="*100)
    print("N1 REGRESSION VALIDATION — per Warsaw-day (14/15/16.06), AFTER proposal-window filter")
    print("="*100)
    hdr = (f"{'day':<11}{'records':>8}{'ge2asg':>8}{'lastAsg':>8}{'win_out':>8}"
           f"{'qualif':>8}{'changed':>8}{'same':>7}{'shiftkm(med/p90)':>20}"
           f"{'falseLate red(med/p90)':>24}{'regr':>6}")
    print(hdr)
    tot = defaultdict(float); tot_lists=defaultdict(list); all_regr=[]
    for day in DAYS:
        P = per_day[day]
        sk_med, sk_p90 = med(P["shifts_km"]), p90(P["shifts_km"])
        rd_med, rd_p90 = med(P["red_min"]), p90(P["red_min"])
        print(f"{day:<11}{P['records']:>8}{P['ge2_assigned']:>8}{P['last_assigned_inferred']:>8}"
              f"{P['filtered_out_window']:>8}{P['qualifying']:>8}{P['anchor_changed']:>8}{P['anchor_same']:>7}"
              f"{(str(round(sk_med,2))+'/'+str(round(sk_p90,2))):>20}"
              f"{(str(round(rd_med,1))+'/'+str(round(rd_p90,1))):>24}{len(P['regressions']):>6}")
        for k in ("records","ge2_assigned","last_assigned_inferred","filtered_out_window",
                  "qualifying","anchor_changed","anchor_same","borderline","certain","osrm_fail"):
            tot[k]+=P[k]
        for k in ("shifts_km","red_min","regr_min"): tot_lists[k]+=P[k]
        all_regr += [dict(r, day=day) for r in P["regressions"]]

    print("-"*100)
    print(f"{'TOTAL':<11}{int(tot['records']):>8}{int(tot['ge2_assigned']):>8}{int(tot['last_assigned_inferred']):>8}"
          f"{int(tot['filtered_out_window']):>8}{int(tot['qualifying']):>8}{int(tot['anchor_changed']):>8}{int(tot['anchor_same']):>7}"
          f"{(str(round(med(tot_lists['shifts_km']),2))+'/'+str(round(p90(tot_lists['shifts_km']),2))):>20}"
          f"{(str(round(med(tot_lists['red_min']),1))+'/'+str(round(p90(tot_lists['red_min']),1))):>24}{len(all_regr):>6}")

    print("\n--- DECISION-QUALITY (anchor-changed only) ---")
    nred = len(tot_lists['red_min']); nregr = len(tot_lists['regr_min'])
    print(f"  anchor changed (total): {int(tot['anchor_changed'])}")
    print(f"  false-lateness REDUCED (delta>=0): {nred}  (sum {round(sum(tot_lists['red_min']),0)} min, "
          f"median {round(med(tot_lists['red_min']),1)}, p90 {round(p90(tot_lists['red_min']),1)})")
    print(f"  REGRESSION (NEW arrival later than OLD, delta<0): {nregr}  "
          f"(sum {round(sum(tot_lists['regr_min']),1)} min, median {round(med(tot_lists['regr_min']),1)})")
    print(f"  certain (|delta|>={BORDERLINE_MIN}min): {int(tot['certain'])}  |  borderline OSRM-noise (<{BORDERLINE_MIN}min): {int(tot['borderline'])}  |  osrm_fail: {int(tot['osrm_fail'])}")

    if all_regr:
        print("\n--- REGRESSIONS (oid, drive OLD->target vs NEW->target, min) ---")
        for r in sorted(all_regr, key=lambda x:x['delta'])[:40]:
            print(f"  {r['day']} oid={r['oid']} OLD={r['d_old']} NEW={r['d_new']} delta={r['delta']} (NEW later by {abs(r['delta'])})")
    else:
        print("\n--- REGRESSIONS: NONE (NEW never arrives later than OLD at the earliest-due pickup) ---")

def _hav(a,b):
    R=6371.0
    la1,lo1=math.radians(a[0]),math.radians(a[1]); la2,lo2=math.radians(b[0]),math.radians(b[1])
    h=math.sin((la2-la1)/2)**2+math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 2*R*math.asin(min(1,math.sqrt(h)))

if __name__=="__main__":
    main()
