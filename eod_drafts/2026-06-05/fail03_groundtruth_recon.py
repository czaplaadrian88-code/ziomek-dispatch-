#!/usr/bin/env python3
"""Ground-truth reconstruction dla 72 FAIL-03 silent orders (06-04/05).

Dla kazdego zlecenia-w-ciszy odtwarza REALNY stan w momencie decyzji T:
- pozycje CALEJ floty z gps_history (nearest fix <= T, tol 20min) -> haversine do pickup
- worek kazdego kuriera z replay audit_log (assigned/picked_up minus delivered) <= T
- realny outcome wyboru czlowieka: defer odbioru + leg dowozu + czy breach R6 (35min)
Cel: czy istniala opcja OBIEKTYWNIE lepsza niz best_effort Ziomka ORAZ niz czlowiek.
"""
import json, glob, sqlite3, bisect
from datetime import datetime
from math import radians, sin, cos, asin, sqrt

R6_MAX = 35.0
GPS_TOL_S = 1200          # 20 min — fix swiezy => apka zywa => realnie dostepny
EV_DB = "dispatch_state/events.db"
GPS_DB = "dispatch_state/courier_api.db"

def hav(a, b):
    if not a or not b or a[0] is None or b[0] is None:
        return None
    la1, lo1, la2, lo2 = map(radians, [a[0], a[1], b[0], b[1]])
    d = 2*asin(sqrt(sin((la2-la1)/2)**2 + cos(la1)*cos(la2)*sin((lo2-lo1)/2)**2))
    return round(6371*d, 2)

def epoch(iso):
    try: return datetime.fromisoformat(iso).timestamp()
    except Exception: return None

# ---- 1) K1 silent orders (dedup po oid) ----
silent = {}
namemap = {}
for f in sorted(glob.glob("scripts/logs/shadow_decisions.jsonl*")):
    if f.endswith(".gz"): continue
    for line in open(f, encoding="utf-8", errors="replace"):
        try: d = json.loads(line)
        except Exception: continue
        ap = d.get("always_propose_would_redirect_shadow")
        if not ap: continue
        best = d.get("best") or {}
        alts = d.get("alternatives") or []
        for c in [best]+alts:
            if c.get("courier_id"): namemap[str(c["courier_id"])] = c.get("name") or str(c["courier_id"])
        silent[str(d.get("order_id"))] = {
            "oid": str(d.get("order_id")), "ts": d.get("ts"), "path": ap.get("path"),
            "mtp": ap.get("minutes_to_pickup"),
            "z_best_cid": str(best.get("courier_id")), "z_best_score": round(best.get("score") or 0, 1),
            "z_best_breach": best.get("max_bag_time_min"), "z_best_feas": best.get("feasibility"),
            "z_best_km": best.get("km_to_pickup"),
            "alt_cids": [str(a.get("courier_id")) for a in alts],
        }
oids = list(silent)

# ---- 2) events.db: NEW_ORDER coords/ready + outcome + bag-replay ----
con = sqlite3.connect(EV_DB); cur = con.cursor()
# coords + ready z NEW_ORDER
for oid in oids:
    row = cur.execute("SELECT payload FROM events WHERE order_id=? AND event_type='NEW_ORDER' LIMIT 1", (oid,)).fetchone()
    pc = pr = dc = None
    if row:
        try:
            p = json.loads(row[0]) or {}
            pc = p.get("pickup_coords"); dc = p.get("delivery_coords")
            pr = epoch(p.get("pickup_at_warsaw")) if p.get("pickup_at_warsaw") else None
        except Exception: pass
    silent[oid].update(pickup_coords=pc, delivery_coords=dc, pickup_ready_ep=pr)

# outcome: picked_up/delivered (po decyzji) wyboru czlowieka
def outcome(oid, ts_ep):
    rows = cur.execute("SELECT event_type,courier_id,created_at FROM audit_log WHERE order_id=? AND event_type IN ('COURIER_ASSIGNED','COURIER_PICKED_UP','COURIER_DELIVERED') ORDER BY created_at", (oid,)).fetchall()
    assigned = pu = dl = None; hcid = None
    for et, cid, ca in rows:
        e = epoch(ca)
        if et == "COURIER_ASSIGNED" and e and e >= ts_ep-120 and assigned is None:
            assigned = e; hcid = str(cid)
        if et == "COURIER_PICKED_UP" and pu is None and e: pu = e; pu_cid = str(cid)
        if et == "COURIER_DELIVERED" and e: dl = e
    if hcid is None and rows:
        hcid = str(rows[0][1])
    return hcid, pu, dl

# preload audit dla bag-replay (assign/pickup/deliver/return)
allev = cur.execute("SELECT order_id,event_type,courier_id,created_at FROM audit_log WHERE event_type IN ('COURIER_ASSIGNED','COURIER_PICKED_UP','COURIER_DELIVERED','ORDER_RETURNED_TO_POOL')").fetchall()
allev = [(epoch(ca), oid, et, str(cid)) for oid, et, cid, ca in allev if epoch(ca)]
allev.sort()
ev_ts = [e[0] for e in allev]

def bag_at(T):
    """order->(courier,status) wg eventow <= T; zwraca dict courier-> set(active oids)."""
    idx = bisect.bisect_right(ev_ts, T)
    st = {}
    for i in range(idx):
        _, oid, et, cid = allev[i]
        if et == "COURIER_ASSIGNED": st[oid] = (cid, "assigned")
        elif et == "COURIER_PICKED_UP":
            c0 = st.get(oid, (cid, ""))[0]; st[oid] = (c0, "picked_up")
        elif et in ("COURIER_DELIVERED", "ORDER_RETURNED_TO_POOL"):
            st[oid] = (st.get(oid, (None,))[0], "done")
    bags = {}
    for oid, (cid, status) in st.items():
        if status in ("assigned", "picked_up") and cid:
            bags.setdefault(cid, set()).add(oid)
    return bags

# ---- 3) gps_history: preload per courier sorted ----
gcon = sqlite3.connect(GPS_DB); gcur = gcon.cursor()
gps = {}
for cid, lat, lon, rec in gcur.execute("SELECT courier_id,lat,lon,recorded_at FROM gps_history WHERE recorded_at > 1780400000"):
    gps.setdefault(str(cid), []).append((rec, lat, lon))
for c in gps: gps[c].sort()

def pos_at(cid, T):
    arr = gps.get(cid)
    if not arr: return None
    ts = [a[0] for a in arr]
    i = bisect.bisect_right(ts, T) - 1
    if i < 0: return None
    rec, lat, lon = arr[i]
    if T - rec > GPS_TOL_S: return None
    return (lat, lon)

# ---- 4) per-order analiza ----
out = []
for oid in oids:
    s = silent[oid]
    T = epoch(s["ts"])
    pc = s["pickup_coords"]; pr = s["pickup_ready_ep"]
    hcid, pu, dl = outcome(oid, T)
    s["human_cid"] = hcid
    defer = round((pu - pr)/60, 1) if (pu and pr) else None     # ile czlowiek odroczyl odbior
    leg = round((dl - pu)/60, 1) if (pu and dl) else None        # realny leg dowozu (R6 metric)
    realized = ("breach" if (leg and leg > R6_MAX) else ("ok" if leg else "?"))
    # ground-truth flota w T: kazdy kurier z GPS fix <=20min + jego worek
    bags = bag_at(T)
    fleet = []
    cand_cids = set(list(gps.keys()) + list(bags.keys()))
    for cid in cand_cids:
        p = pos_at(cid, T)
        km = hav(p, pc) if (p and pc) else None
        fleet.append((cid, km, len(bags.get(cid, set()))))
    # GT-optimum proxy: GPS-swiezy, worek<=2, najblizej pickup
    avail = [(km, bag, cid) for cid, km, bag in fleet if km is not None and bag <= 2]
    avail.sort()
    gt = avail[0] if avail else None
    gt_cid = gt[2] if gt else None
    gt_km = gt[0] if gt else None
    gt_bag = gt[1] if gt else None
    # km wyboru czlowieka i Ziomka best (jesli GPS swiezy)
    hp = pos_at(hcid, T) if hcid else None
    h_km = hav(hp, pc) if (hp and pc) else None
    h_bag = len(bags.get(hcid, set())) if hcid else None
    out.append({**s, "human_defer_min": defer, "human_leg_min": leg, "realized": realized,
                "human_km": h_km, "human_bag": h_bag,
                "gt_cid": gt_cid, "gt_km": gt_km, "gt_bag": gt_bag,
                "gt_n_avail": len(avail)})

# ---- 5) raport ----
print(f"{'oid':7}{'T':6}{'path':9}{'mtp':>5}|{'Zbest':>6}{'estBr':>6}|{'human':>6}{'defer':>6}{'leg':>5}{'R6':>7}|{'GTopt':>6}{'km':>5}{'bag':>4}{'navl':>5}")
def hm(ep):
    return datetime.utcfromtimestamp(ep).strftime("%H:%M") if ep else "?"
for r in sorted(out, key=lambda x: x["ts"]):
    p = r["path"].replace("best_effort_r6_breach_v2","r6v2").replace("all_candidates_low_score","low").replace("best_effort_low_score","blow")
    print(f"{r['oid']:7}{hm(epoch(r['ts'])):6}{p:9}{str(r['mtp']):>5}|"
          f"{r['z_best_cid']:>6}{str(r['z_best_breach']):>6}|"
          f"{str(r['human_cid']):>6}{str(r['human_defer_min']):>6}{str(r['human_leg_min']):>5}{r['realized']:>7}|"
          f"{str(r['gt_cid']):>6}{str(r['gt_km']):>5}{str(r['gt_bag']):>4}{r['gt_n_avail']:>5}")

# ---- 6) agregaty ----
n = len(out)
legs = [r for r in out if r["human_leg_min"] is not None]
ok = [r for r in legs if r["realized"] == "ok"]
breach = [r for r in legs if r["realized"] == "breach"]
defers = [r["human_defer_min"] for r in out if r["human_defer_min"] is not None]
print(f"\n=== AGREGATY (n={n}) ===")
print(f"outcome wyboru czlowieka (n_z_legiem={len(legs)}):")
print(f"  dowiozl ≤35min (R6 OK realnie): {len(ok)} ({100*len(ok)/len(legs):.0f}%)")
print(f"  realny breach >35min:           {len(breach)} ({100*len(breach)/len(legs):.0f}%)")
if defers:
    ds = sorted(defers)
    print(f"  odroczenie odbioru przez czlowieka: median={ds[len(ds)//2]:.0f}min  >10min={sum(1 for x in ds if x>10)}/{len(ds)}  (=defer-pickup strategia)")
if ok:
    okd = [r["human_defer_min"] for r in ok if r["human_defer_min"] is not None]
    if okd: print(f"  z tych co dowiezli OK: median defer={sorted(okd)[len(okd)//2]:.0f}min (R6 trzymane PRZEZ odroczenie odbioru)")
# better-option check
better = [r for r in out if r["gt_cid"] and r["human_km"] is not None and r["gt_km"] is not None
          and r["gt_cid"] != r["human_cid"] and r["gt_km"] + 0.5 < r["human_km"] and r["gt_bag"] <= (r["human_bag"] or 9)]
print(f"\nground-truth 'lepsza opcja istniala' (GT-kurier blizej >0.5km I worek<=human, !=human): {len(better)}/{n}")
for r in better[:12]:
    print(f"  oid={r['oid']} human={r['human_cid']}({r['human_km']}km b{r['human_bag']}) -> GT={r['gt_cid']}({r['gt_km']}km b{r['gt_bag']}) realized={r['realized']}")

json.dump(out, open("/root/silent_orders_corpus.json","w"), ensure_ascii=False, indent=1, default=str)
print(f"\nkorpus -> /root/silent_orders_corpus.json ({n} rekordow)")
