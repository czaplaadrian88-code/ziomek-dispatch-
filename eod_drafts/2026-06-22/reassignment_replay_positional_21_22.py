#!/usr/bin/env python3
"""Replay POZYCYJNY 21-22.06 v2 — pozycja no_gps liczona JAK SILNIK (last-known location).

KOREKTA Adriana 22.06: silnik dla kuriera bez GPS NIE zostawia „brak pozycji" — liczy ją
z ostatniej znanej lokalizacji (gdzie ostatnio odbierał/doręczał). courier_resolver:1018-1048
hierarchia: gps → last_picked_up_(pickup/delivery/interp) → last_assigned_pickup → last_delivered.
v1 backtestu użył tylko surowego gps_history → błędnie odrzucił 24/40 kurierów.

v2: pozycja@T = NAJŚWIEŻSZA realna lokalizacja przed T spośród {fix GPS (gps_history),
ostatni pickup (pickup_coords+picked_up_at), ostatnia dostawa (delivery_coords+delivered_at)}
— z orders_state. + raportuje źródło i wiek (rzetelność). Metryka jak v1: ETA dostawy O
pod A vs B vs best-available (real OSRM `simulate_bag_route_v2`).

OGRANICZENIA: ETA O-centryczna (nie waży wpływu na inne zlecenia B); trasa-only (bez wag/
feasibility); pozycja last-known z wiekiem (raportowany) = przybliżenie, ale TAK liczy silnik.
"""
import sys
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
import json, sqlite3, bisect, logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import statistics as st

logging.getLogger().setLevel(logging.WARNING)
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, OrderSim

WAR = timezone(timedelta(hours=2))
DB = "/root/.openclaw/workspace/dispatch_state/events.db"
CDB = "/root/.openclaw/workspace/dispatch_state/courier_api.db"
OS = "/root/.openclaw/workspace/dispatch_state/orders_state.json"
DAYS = ("2026-06-21", "2026-06-22")
FRESH_H = 3.0
POS_CAP_MIN = 90  # pozycja starsza = nierzetelna (silnik nie używa >60min GPS; last_delivered fresh <30)


def _epoch(iso):
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00").replace(" ", "T"))
        if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except (ValueError, AttributeError):
        return None


od = json.load(open(OS)); orders = od.get("orders", od) if isinstance(od, dict) else od

# gps_history -> per cid sorted [(epoch,lat,lon)]
con = sqlite3.connect(CDB); cur = con.cursor()
gps = defaultdict(list)
for cid, lat, lon, rec in cur.execute("SELECT courier_id,lat,lon,recorded_at FROM gps_history"):
    if lat and lon:
        gps[str(cid)].append((float(rec), float(lat), float(lon)))
con.close()
for c in gps: gps[c].sort()
gps_keys = {c: [x[0] for x in gps[c]] for c in gps}

# courier_events z orders_state: ostatni pickup / ostatnia dostawa = realna lokalizacja
courier_events = defaultdict(list)  # cid -> [(epoch, (lat,lon), source)]
for oid, r in orders.items():
    if not isinstance(r, dict): continue
    cid = str(r.get("courier_id") or "")
    if not cid or cid in ("None", ""): continue
    if r.get("picked_up_at") and r.get("pickup_coords"):
        e = _epoch(r["picked_up_at"])
        if e: courier_events[cid].append((e, tuple(r["pickup_coords"]), "last_picked_up"))
    if r.get("delivered_at") and r.get("delivery_coords"):
        e = _epoch(r["delivered_at"])
        if e: courier_events[cid].append((e, tuple(r["delivery_coords"]), "last_delivered"))
for c in courier_events: courier_events[c].sort()


def engine_pos(cid, te):
    """Pozycja JAK SILNIK no_gps: najświeższa realna lokalizacja przed T spośród
    {fix GPS, ostatni pickup, ostatnia dostawa}. Zwraca (coords, source, age_min) | (None,None,None)."""
    c = str(cid)
    best = None  # (epoch, coords, source)
    arr = gps.get(c)
    if arr:
        i = bisect.bisect_right(gps_keys[c], te) - 1
        if i >= 0:
            best = (arr[i][0], (arr[i][1], arr[i][2]), "gps")
    for e, coords, src in courier_events.get(c, []):
        if e <= te and (best is None or e > best[0]):
            best = (e, coords, src)
    if best is None:
        return (None, None, None)
    age = (te - best[0]) / 60.0
    if age > POS_CAP_MIN:   # >tego = pozycja nierzetelna (silnik nie używa >60min GPS); filtruj fix-śmieci sprzed dni
        return (None, None, None)
    return (best[1], best[2], age)


# audit events for bag reconstruction + reassigns
con = sqlite3.connect(DB); cur = con.cursor()
ev = []
for et, oid, cid, created in cur.execute(
        "SELECT event_type,order_id,courier_id,created_at FROM audit_log "
        "WHERE event_type IN ('COURIER_ASSIGNED','COURIER_DELIVERED') ORDER BY created_at"):
    e = _epoch(created)
    if e is not None: ev.append((e, et, str(oid), str(cid)))
reassigns = {}
for et, oid, cid, created, pl in cur.execute(
        "SELECT event_type,order_id,courier_id,created_at,payload FROM audit_log "
        "WHERE event_type='COURIER_ASSIGNED' ORDER BY created_at"):
    pl = json.loads(pl) if pl else {}
    prev = pl.get("previous_cid")
    if prev and str(prev) not in ("None", str(cid), ""):
        e = _epoch(created)
        if e is None: continue
        if datetime.fromtimestamp(e, WAR).strftime("%Y-%m-%d") in DAYS:
            reassigns[str(oid)] = (str(prev), str(cid), e)
con.close()


def bags_at(te):
    bag = defaultdict(dict)
    for e, typ, oid, cid in ev:
        if e > te: break
        if typ == "COURIER_DELIVERED":
            for c in bag: bag[c].pop(oid, None)
        else:
            for c in list(bag): bag[c].pop(oid, None)
            bag[cid][oid] = e
    return {c: [o for o, ae in d.items() if (te - ae) <= FRESH_H * 3600] for c, d in bag.items()}


def osim(oid, te, as_new=False):
    r = orders.get(str(oid))
    if not r or not r.get("pickup_coords") or not r.get("delivery_coords"): return None
    status, picked = "assigned", None
    if not as_new:
        pe = _epoch(r.get("picked_up_at")) if r.get("picked_up_at") else None
        if pe and pe < te:
            status, picked = "picked_up", datetime.fromtimestamp(pe, timezone.utc)
    return OrderSim(order_id=str(oid), pickup_coords=tuple(r["pickup_coords"]),
                    delivery_coords=tuple(r["delivery_coords"]), picked_up_at=picked,
                    status=status, pickup_ready_at=None)


def eta_for(cid, oid, te, bags):
    pos, src, age = engine_pos(cid, te)
    if pos is None: return None
    O = osim(oid, te, as_new=True)
    if O is None: return None
    bag = [osim(x, te) for x in bags.get(str(cid), []) if str(x) != str(oid)]
    bag = [b for b in bag if b]
    t_utc = datetime.fromtimestamp(te, timezone.utc)
    try:
        plan = simulate_bag_route_v2(pos, bag, O, now=t_utc)
    except Exception:
        return None
    dd = plan.predicted_delivered_at.get(str(oid))
    if dd is None: return None
    if dd.tzinfo is None: dd = dd.replace(tzinfo=timezone.utc)
    return ((dd - t_utc).total_seconds() / 60.0, src, age)


b_better = a_better = tie = 0
improvements = []
best_is_b = best_is_a = best_other = 0
no_posA = no_posB = analyzed = 0
src_used = defaultdict(int); ages = []
sample = []
for oid, (A, B, te) in reassigns.items():
    bags = bags_at(te)
    rA = eta_for(A, oid, te, bags)
    rB = eta_for(B, oid, te, bags)
    if rA is None: no_posA += 1
    if rB is None: no_posB += 1
    if rA is None or rB is None:
        continue
    analyzed += 1
    etaA, etaB = rA[0], rB[0]
    src_used[rA[1]] += 1; src_used[rB[1]] += 1
    if rA[2] is not None: ages.append(rA[2])
    if rB[2] is not None: ages.append(rB[2])
    if etaB < etaA - 1: b_better += 1
    elif etaA < etaB - 1: a_better += 1
    else: tie += 1
    improvements.append(etaA - etaB)
    cands = {A, B} | set(bags.keys())
    best_cid, best_eta = None, None
    for c in cands:
        r = eta_for(c, oid, te, bags)
        if r and (best_eta is None or r[0] < best_eta):
            best_eta, best_cid = r[0], str(c)
    if best_cid == str(B): best_is_b += 1
    elif best_cid == str(A): best_is_a += 1
    else: best_other += 1
    sample.append((oid, f"{A}->{B}", round(etaA, 1), round(etaB, 1),
                   f"posA={rA[1]}/{round(rA[2])}m", f"best={best_cid}@{round(best_eta,1) if best_eta is not None else '?'}"))

print("=" * 74)
print("REPLAY POZYCYJNY v2 przerzutów 21-22.06 — pozycja JAK SILNIK (last-known) + OSRM")
print("=" * 74)
print(f"  przerzutów: {len(reassigns)} | przeanalizowano (pozycja A i B liczona @T): {analyzed}")
print(f"  pominięto: brak JAKIEJKOLWIEK lokalizacji dawcy {no_posA} / biorcy {no_posB}")
print(f"  źródła użytych pozycji: {dict(src_used)}")
if ages:
    print(f"  wiek pozycji (min): mediana {st.median(ages):.0f} | p90 {sorted(ages)[int(0.9*len(ages))-1]:.0f}")
if analyzed:
    print(f"\n  CO BY ZMIENIŁO (ETA dostawy O, próg 1 min):")
    print(f"   • B dowozi O SZYBCIEJ niż A (przerzut route-uzasadniony): {b_better}/{analyzed}"
          f" = {100*b_better//analyzed}%")
    print(f"   • A był szybszy (route-NIE-uzasadniony → roster/idle): {a_better}/{analyzed}")
    print(f"   • remis ±1 min: {tie}/{analyzed}")
    print(f"   • mediana poprawy ETA O (A−B): {st.median(improvements):+.1f} min | średnia {st.mean(improvements):+.1f}")
    pos = [x for x in improvements if x > 2]
    if pos:
        print(f"   • realnie szybciej >2 min: {len(pos)}/{analyzed} (mediana w nich {st.median(pos):+.1f})")
    print(f"\n  CZY B BYŁ NAJLEPSZY (wśród dostępnych @T):")
    print(f"   • best == B (człowiek trafił optimum trasy): {best_is_b}/{analyzed}")
    print(f"   • best == A (lepiej zostawić): {best_is_a}/{analyzed}")
    print(f"   • best == ktoś inny (był lepszy ruch): {best_other}/{analyzed}")
print(f"\n  ⚠ ETA O-centryczna; trasa-only; pozycja last-known z wiekiem powyżej (tak liczy silnik no_gps).")
print(f"\n  Próbka:")
for s in sample[:16]:
    print("   ", s)
