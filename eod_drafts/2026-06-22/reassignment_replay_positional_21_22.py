#!/usr/bin/env python3
"""Replay POZYCYJNY 21-22.06 — czy przerzut A→B był uzasadniony trasą W MOMENCIE przerzutu T.

Głębszy niż arrival-time backtest: używa REALNYCH pozycji floty w chwili T (gps_history
z courier_api.db, 147k fixów) + worków zrekonstruowanych z audit_log @T + PRAWDZIWEGO
routera `simulate_bag_route_v2` (OSRM). Metryka O-centryczna: predicted ETA dostawy
zlecenia O pod kurierem A (obecny) vs B (biorca) vs best-available.

 • etaB < etaA  → B dowozi O szybciej → przerzut route-uzasadniony.
 • best == B    → B był trasowo-optymalny wśród dostępnych.
 • improvement  → etaA − etaB (min uratowane dla O).

OGRANICZENIA (uczciwie): (1) ETA O-centryczna — NIE waży wpływu na pozostałe zlecenia B
(pełny shadow to robi); (2) geometria/trasa-only, bez wag scoringu/feasibility; (3) pozycja
= ostatni fix ≤30 min przed T (luki GPS → kurier pomijany). To PRZYBLIŻENIE pełnego shadow,
ale przy realnych pozycjach+OSRM — znacznie wierniejsze niż arrival-time.
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
POS_FRESH_S = 60 * 60  # 60 min = próg „realnej" pozycji wg prod (CLAUDE.md)


def _epoch(iso):
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00").replace(" ", "T"))
        if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except (ValueError, AttributeError):
        return None


# orders_state
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


def pos_at(cid, te):
    c = str(cid); arr = gps.get(c)
    if not arr: return None
    i = bisect.bisect_right(gps_keys[c], te) - 1
    if i < 0: return None
    e, la, lo = arr[i]
    return (la, lo) if (te - e) <= POS_FRESH_S else None


def fix_age_min(cid, te):
    """Wiek najbliższego fixa GPS przed T (min) lub None gdy kurier nie ma fixów."""
    c = str(cid); arr = gps.get(c)
    if not arr: return None
    i = bisect.bisect_right(gps_keys[c], te) - 1
    if i < 0: return None
    return (te - arr[i][0]) / 60.0


# audit events for bag reconstruction
con = sqlite3.connect(DB); cur = con.cursor()
ev = []
for et, oid, cid, created in cur.execute(
        "SELECT event_type,order_id,courier_id,created_at FROM audit_log "
        "WHERE event_type IN ('COURIER_ASSIGNED','COURIER_DELIVERED') ORDER BY created_at"):
    e = _epoch(created)
    if e is not None: ev.append((e, et, str(oid), str(cid)))
# reassigns
reassigns = {}
for et, oid, cid, created, pl in cur.execute(
        "SELECT event_type,order_id,courier_id,created_at,payload FROM audit_log "
        "WHERE event_type='COURIER_ASSIGNED' ORDER BY created_at"):
    pl = json.loads(pl) if pl else {}
    prev = pl.get("previous_cid")
    if prev and str(prev) not in ("None", str(cid), ""):
        e = _epoch(created)
        if e is None: continue
        d = datetime.fromtimestamp(e, WAR)
        if d.strftime("%Y-%m-%d") in DAYS:
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
    p = pos_at(cid, te)
    if p is None: return None
    O = osim(oid, te, as_new=True)
    if O is None: return None
    bag = [osim(x, te) for x in bags.get(str(cid), []) if str(x) != str(oid)]
    bag = [b for b in bag if b]
    t_utc = datetime.fromtimestamp(te, timezone.utc)
    try:
        plan = simulate_bag_route_v2(p, bag, O, now=t_utc)
    except Exception:
        return None
    dd = plan.predicted_delivered_at.get(str(oid))
    if dd is None: return None
    if dd.tzinfo is None: dd = dd.replace(tzinfo=timezone.utc)
    return ((dd - t_utc).total_seconds() / 60.0, plan.sla_violations)


# main
b_better = a_better = tie = 0
improvements = []
best_is_b = best_is_a = best_other = 0
no_posA = no_posB = analyzed = 0
age_donor = []; age_recip = []
sample = []
for oid, (A, B, te) in reassigns.items():
    age_donor.append(fix_age_min(A, te)); age_recip.append(fix_age_min(B, te))
    bags = bags_at(te)
    rA = eta_for(A, oid, te, bags)
    rB = eta_for(B, oid, te, bags)
    if rA is None: no_posA += 1
    if rB is None: no_posB += 1
    if rA is None or rB is None:
        continue
    analyzed += 1
    etaA, etaB = rA[0], rB[0]
    if etaB < etaA - 1: b_better += 1
    elif etaA < etaB - 1: a_better += 1
    else: tie += 1
    improvements.append(etaA - etaB)
    # best available
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
                   f"best={best_cid}@{round(best_eta,1) if best_eta is not None else '?'}"))

print("=" * 72)
print(f"REPLAY POZYCYJNY przerzutów 21-22.06 (real pos@T + OSRM, ETA dostawy O)")
print("=" * 72)
print(f"  przerzutów: {len(reassigns)} | przeanalizowano (pos A i B znane @T): {analyzed}")
print(f"  pominięto: brak pozycji dawcy {no_posA} / biorcy {no_posB} (fix >60min lub brak)")


def _bucket(ages):
    return (f"≤30min:{sum(1 for a in ages if a is not None and a <= 30)} "
            f"30-60:{sum(1 for a in ages if a is not None and 30 < a <= 60)} "
            f"60-120:{sum(1 for a in ages if a is not None and 60 < a <= 120)} "
            f">120:{sum(1 for a in ages if a is not None and a > 120)} "
            f"BRAK_FIXA:{sum(1 for a in ages if a is None)}")
print(f"\n  POKRYCIE GPS w momencie przerzutu (n={len(age_donor)}) — wiek najbliższego fixa:")
print(f"   • dawcy A:  {_bucket(age_donor)}")
print(f"   • biorcy B: {_bucket(age_recip)}")
if analyzed:
    print(f"\n  CO BY ZMIENIŁO (ETA dostawy O, próg 1 min):")
    print(f"   • B dowozi O SZYBCIEJ niż A (przerzut route-uzasadniony): {b_better}/{analyzed}"
          f" = {100*b_better//analyzed}%")
    print(f"   • A był szybszy (przerzut route-NIE-uzasadniony, pewnie roster/idle): {a_better}/{analyzed}")
    print(f"   • remis (±1 min): {tie}/{analyzed}")
    print(f"   • mediana poprawy ETA O (A−B): {st.median(improvements):+.1f} min"
          f" | średnia {st.mean(improvements):+.1f}")
    pos = [x for x in improvements if x > 2]
    if pos:
        print(f"   • realnie szybciej >2 min: {len(pos)}/{analyzed} (mediana w nich {st.median(pos):+.1f} min)")
    print(f"\n  CZY B BYŁ NAJLEPSZY (wśród dostępnych @T):")
    print(f"   • best == B (człowiek trafił optimum trasy): {best_is_b}/{analyzed}")
    print(f"   • best == A (lepiej było zostawić): {best_is_a}/{analyzed}")
    print(f"   • best == ktoś inny (był jeszcze lepszy ruch): {best_other}/{analyzed}")
print(f"\n  ⚠ ETA O-centryczna (nie waży wpływu na pozostałe zlecenia B); trasa-only, bez wag/feasibility.")
print(f"\n  Próbka (oid, A→B, etaA, etaB, best):")
for s in sample[:16]:
    print("   ", s)
