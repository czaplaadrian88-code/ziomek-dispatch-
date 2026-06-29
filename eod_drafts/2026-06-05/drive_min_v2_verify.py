#!/usr/bin/env python3
"""Weryfikacja forward-looking kalibracji drive_min v2 (READ-ONLY, offline).

Domyka brakujące ogniwo z design doc (§387/439-448): join logu cienia
(raw/calibrated per order+courier) do REALNEGO assign->pickup zbudowanego
z 9 dziennych snapshotów orders_state.

Pytanie: czy `calibrated_drive_min` jest bliżej rzeczywistości niż `raw`,
per pos_source — i GDZIE przestrzeliwuje (calibrated >> real).

Metoda i zastrzeżenia (uczciwie):
 - real = picked_up_at(Warsaw) - assigned_at(UTC), filtr 1..90 min
   (odrzuca glitch'e typu assigned_at nadpisany incydentem 18.05).
 - z logu bierzemy rekord dla ZWYCIĘSKIEGO kuriera (courier_id == realny
   assignee), najbliższy czasowo momentowi assigned_at (decyzja).
"""
import json, glob, datetime as dt, statistics as st
from collections import defaultdict
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
SNAP = "/root/.openclaw/workspace/dispatch_state/snapshots/orders_state_*.json"
LOG  = "/root/.openclaw/workspace/dispatch_state/drive_min_calibration_log_v2.jsonl"

def parse_utc(s):
    if not s: return None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:                      # naive -> Warsaw lokalny
            d = d.replace(tzinfo=WARSAW)
        return d.astimezone(dt.timezone.utc)
    except Exception:
        return None

# 1) Ground truth: order_id -> (real_min, assigned_utc, assignee_cid)
truth = {}
for path in sorted(glob.glob(SNAP)):
    s = json.load(open(path))
    for oid, r in s.items():
        if not isinstance(r, dict) or r.get("status") != "delivered":
            continue
        a = parse_utc(r.get("assigned_at")); p = parse_utc(r.get("picked_up_at"))
        if not a or not p:
            continue
        real = (p - a).total_seconds() / 60.0
        if not (1.0 <= real <= 90.0):             # filtr glitchy
            continue
        cid = r.get("courier_id")
        if cid is None:
            continue
        truth[str(oid)] = (real, a, str(cid))     # późniejszy snapshot nadpisze (najpełniejszy)

# 2) Log cienia: (order_id, courier_id) -> lista (ts, raw, cal, pos, tier, peak)
recs = defaultdict(list)
for ln in open(LOG):
    try: r = json.loads(ln)
    except Exception: continue
    oid = str(r.get("order_id")); cid = str(r.get("courier_id"))
    ts = parse_utc(r.get("ts"))
    if ts is None: continue
    recs[(oid, cid)].append((ts, r.get("raw_drive_min"), r.get("calibrated_drive_min"),
                             r.get("pos_source"), r.get("tier"), bool(r.get("peak_window"))))

# 3) Join: dla zwycięskiego kuriera rekord najbliższy assigned_at
rows = []
for oid, (real, a_utc, cid) in truth.items():
    cand = recs.get((oid, cid))
    if not cand: continue
    ts, raw, cal, pos, tier, peak = min(cand, key=lambda x: abs((x[0] - a_utc).total_seconds()))
    if raw is None or cal is None: continue
    rows.append(dict(oid=oid, cid=cid, real=real, raw=float(raw), cal=float(cal),
                     pos=pos or "None", tier=tier, peak=peak,
                     rr=float(raw) - real, rc=float(cal) - real))

print(f"truth delivered (filtr 1-90min) = {len(truth)} | log par = {len(recs)} | JOIN = {len(rows)}\n")
if not rows:
    print("Brak joinu — sprawdź typy/okno czasu."); raise SystemExit

def med_abs(xs): return st.median([abs(x) for x in xs]) if xs else float("nan")
def med(xs):     return st.median(xs) if xs else float("nan")

rr = [x["rr"] for x in rows]; rc = [x["rc"] for x in rows]
print("=== CAŁOŚĆ (rezid = predykcja - realny; +=za dużo, -=za mało) ===")
print(f"  RAW       : median|resid|={med_abs(rr):5.1f}  median_bias={med(rr):+5.1f}")
print(f"  CALIBRATED: median|resid|={med_abs(rc):5.1f}  median_bias={med(rc):+5.1f}")
print(f"  poprawa median|resid|: {med_abs(rr)-med_abs(rc):+.1f} min")
print(f"  przestrzał kalibracji (cal-real >+10): {sum(1 for x in rc if x>10)}/{len(rc)} ({100*sum(1 for x in rc if x>10)/len(rc):.0f}%)")
print(f"  niedoszacowanie raw  (raw-real <-10): {sum(1 for x in rr if x<-10)}/{len(rr)} ({100*sum(1 for x in rr if x<-10)/len(rr):.0f}%)\n")

print("=== PER pos_source ===")
bypos = defaultdict(list)
for x in rows: bypos[x["pos"]].append(x)
print(f"  {'pos_source':22s} {'n':>4} {'|res|raw':>9} {'|res|cal':>9} {'bias_cal':>9} {'over+10':>8}")
for pos, xs in sorted(bypos.items(), key=lambda kv: -len(kv[1])):
    rrp=[x['rr'] for x in xs]; rcp=[x['rc'] for x in xs]
    over=sum(1 for v in rcp if v>10)
    print(f"  {pos:22s} {len(xs):4d} {med_abs(rrp):9.1f} {med_abs(rcp):9.1f} {med(rcp):+9.1f} {over:4d}/{len(xs):<3d}")

print("\n=== PER bucket raw_drive_min (czy krótkie dojazdy przestrzeliwują?) ===")
def bucket(v): return "0-5" if v<5 else "5-10" if v<10 else "10-20" if v<20 else "20+"
byb=defaultdict(list)
for x in rows: byb[bucket(x["raw"])].append(x)
print(f"  {'raw_bucket':10s} {'n':>4} {'real_med':>9} {'cal_med':>9} {'|res|raw':>9} {'|res|cal':>9}")
for b in ["0-5","5-10","10-20","20+"]:
    xs=byb.get(b,[])
    if not xs: continue
    print(f"  {b:10s} {len(xs):4d} {med([x['real'] for x in xs]):9.1f} {med([x['cal'] for x in xs]):9.1f} {med_abs([x['rr'] for x in xs]):9.1f} {med_abs([x['rc'] for x in xs]):9.1f}")
