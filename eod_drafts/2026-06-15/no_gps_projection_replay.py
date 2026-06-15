#!/usr/bin/env python3
"""REPLAY DOWODOWY: projekcja pozycji bez-GPS vs statyczna kotwica.

Cel: udowodnić na REALNYCH danych (orders_state), że:
  1) bag-time liczony przez Ziomka dla kuriera bez GPS jest ZAWYŻONY względem
     realnego wieku termicznego (real delivered - ready),
  2) projekcja pozycji po trasie (przyczynowa, tylko z danych dostępnych w chwili
     decyzji) daje pozycję BLISKĄ realnej, a kotwica statyczna jest daleko,
  3) ile dzisiejszych KOORD odzyskałaby projekcja (realny wiek termiczny <= 35).

Read-only. Nie dotyka produkcji.
"""
import json, sys, math
from datetime import datetime, timezone, timedelta

LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
ST = "/root/.openclaw/workspace/dispatch_state/orders_state.json"
WAW = timezone(timedelta(hours=2))
R6_HARD = 35.0
KMH_CITY = 24.0  # ~0.4 km/min — do przeliczenia błędu pozycji na minuty


def to_utc(s):
    if not s:
        return None
    s = str(s).strip()
    try:
        if "T" in s and ("+" in s or s.endswith("Z")):
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=WAW)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def haversine_km(a, b):
    if not a or not b:
        return None
    R = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dla, dlo = la2 - la1, lo2 - lo1
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


st = json.load(open(ST, encoding="utf-8"))
orders = st.get("orders", st)


def ready_utc(o):
    """Czas gotowości jedzenia (kotwica termiczna): pickup_at_warsaw lub czas_kuriera."""
    return to_utc(o.get("pickup_at_warsaw")) or to_utc(o.get("czas_kuriera_warsaw"))


def real_thermal_min(oid):
    """Realny wiek termiczny = realna dostawa - gotowość. None gdy brak danych."""
    o = orders.get(str(oid))
    if not o:
        return None
    dv = to_utc(o.get("delivered_at"))
    rd = ready_utc(o)
    if dv is None or rd is None:
        return None
    return (dv - rd).total_seconds() / 60.0


def courier_orders(cid):
    out = []
    for oid, o in orders.items():
        if str(o.get("courier_id")) == str(cid):
            out.append((oid, o))
    return out


def real_pos_at(cid, T):
    """GROUND TRUTH: realna pozycja kuriera w chwili T — interpolacja między
    ostatnim realnym zdarzeniem <=T a następnym >T (po realnych timestampach)."""
    ev = []
    for oid, o in courier_orders(cid):
        pu = to_utc(o.get("picked_up_at"))
        dv = to_utc(o.get("delivered_at"))
        if pu and o.get("pickup_coords"):
            ev.append((pu, tuple(o["pickup_coords"])))
        if dv and o.get("delivery_coords"):
            ev.append((dv, tuple(o["delivery_coords"])))
    ev.sort(key=lambda x: x[0])
    if not ev:
        return None
    prev = None
    nxt = None
    for t, p in ev:
        if t <= T:
            prev = (t, p)
        elif nxt is None:
            nxt = (t, p)
            break
    if prev and nxt:
        span = (nxt[0] - prev[0]).total_seconds()
        f = 0.0 if span <= 0 else max(0.0, min(1.0, (T - prev[0]).total_seconds() / span))
        return (prev[1][0] + f * (nxt[1][0] - prev[1][0]),
                prev[1][1] + f * (nxt[1][1] - prev[1][1]))
    return (prev or nxt)[1]


def projected_pos_at(cid, T):
    """FIX (przyczynowy): pozycja z danych dostępnych W CHWILI T.
    Buduje oś czasu trasy z worka: odebrane (picked_up_at<=T -> w drodze pickup->delivery),
    przypisane (czas_kuriera -> będzie w restauracji o tej porze). Interpoluje na T.
    Używa TYLKO informacji znanych do T (picked_up_at<=T, czas_kuriera, coords)."""
    stops = []  # (czas, coords)
    for oid, o in courier_orders(cid):
        pu = to_utc(o.get("picked_up_at"))
        ck = to_utc(o.get("czas_kuriera_warsaw"))
        pc = tuple(o["pickup_coords"]) if o.get("pickup_coords") else None
        dc = tuple(o["delivery_coords"]) if o.get("delivery_coords") else None
        # odebrane do T: kurier był w pickup o picked_up_at, jedzie do delivery
        if pu and pu <= T and pc:
            stops.append((pu, pc))
            if dc:
                # przewidywany czas dostawy = picked_up_at + szac. dojazd (haversine/KMH)
                d = haversine_km(pc, dc)
                eta = pu + timedelta(minutes=(d / KMH_CITY * 60.0) if d else 8.0)
                stops.append((eta, dc))
        # przypisane (jeszcze nieodebrane): będzie w restauracji o czas_kuriera
        elif ck and pc and not (pu and pu <= T):
            stops.append((ck, pc))
            if dc:
                d = haversine_km(pc, dc)
                eta = ck + timedelta(minutes=(d / KMH_CITY * 60.0) if d else 8.0)
                stops.append((eta, dc))
    stops = [s for s in stops if s[1]]
    stops.sort(key=lambda x: x[0])
    if not stops:
        return None
    prev = None
    nxt = None
    for t, p in stops:
        if t <= T:
            prev = (t, p)
        elif nxt is None:
            nxt = (t, p)
            break
    if prev and nxt:
        span = (nxt[0] - prev[0]).total_seconds()
        f = 0.0 if span <= 0 else max(0.0, min(1.0, (T - prev[0]).total_seconds() / span))
        return (prev[1][0] + f * (nxt[1][0] - prev[1][0]),
                prev[1][1] + f * (nxt[1][1] - prev[1][1]))
    return (prev or nxt)[1]


def static_anchor(cand):
    """Kotwica statyczna, której użył Ziomek — pickup_coords zlecenia dającego pos_source."""
    bc = cand.get("bag_context") or []
    # ostatni picked_up/assigned w worku -> jego pickup_coords (przybliżenie)
    for b in bc:
        o = orders.get(str(b.get("order_id")), {})
        if o.get("pickup_coords"):
            return tuple(o["pickup_coords"])
    return None


# zbierz dzisiejsze KOORD-only (dedup po order_id)
hist = {}
for line in open(LOG, encoding="utf-8", errors="ignore"):
    line = line.strip()
    if not line:
        continue
    try:
        r = json.loads(line)
    except Exception:
        continue
    if "2026-06-15" not in json.dumps(r.get("best", {}).get("new_pickup_eta_iso", "")):
        continue
    hist.setdefault(str(r.get("order_id")), []).append(r)

koord = {oid: rs[-1] for oid, rs in hist.items()
         if "PROPOSE" not in {x.get("verdict") for x in rs} and "KOORD" in {x.get("verdict") for x in rs}}

bag_errs = []          # Ziomek bag_time - real thermal (worst order)
pos_static_err = []    # km kotwica vs real
pos_proj_err = []      # km projekcja vs real
recover = 0
total = 0
detail = []
for oid, r in koord.items():
    T = to_utc(r.get("ts"))
    if T is None:
        continue
    total += 1
    cands = (r.get("candidates") or []) + ([r.get("best")] if r.get("best") else [])
    best_real_thermal = None
    row_done = False
    for c in cands:
        if c.get("pos_source") == "gps":
            continue  # tylko bez-GPS
        cid = c.get("courier_id")
        zt = c.get("r6_max_bag_time_min")
        worst = c.get("r6_worst_oid")
        # czasowy dowód: realny termiczny worst order
        rt = real_thermal_min(worst) if worst else None
        if rt is not None and isinstance(zt, (int, float)) and zt < 1000:
            bag_errs.append(zt - rt)
            if best_real_thermal is None or rt < best_real_thermal:
                best_real_thermal = rt
        # pozycyjny dowód
        if cid is not None:
            rp = real_pos_at(cid, T)
            sp = static_anchor(c)
            pp = projected_pos_at(cid, T)
            es = haversine_km(sp, rp)
            ep = haversine_km(pp, rp)
            if es is not None:
                pos_static_err.append(es)
            if ep is not None:
                pos_proj_err.append(ep)
    if best_real_thermal is not None and best_real_thermal <= R6_HARD:
        recover += 1
        row_done = True
    detail.append((oid, best_real_thermal, row_done))


def stats(xs):
    if not xs:
        return (0, 0, 0, 0)
    xs = sorted(xs)
    n = len(xs)
    return (n, xs[0], xs[n // 2], xs[-1])


print("=" * 70)
print("REPLAY DOWODOWY — projekcja pozycji bez-GPS (dziś, KOORD-only)")
print("=" * 70)
n, lo, md, hi = stats(bag_errs)
print(f"\n[1] CZASOWY DOWÓD — Ziomek bag_time MINUS realny wiek termiczny (worst order)")
print(f"    próbek={n}  min={lo:.1f}  MEDIANA={md:.1f}min  max={hi:.1f}  (dodatnie = Ziomek ZAWYŻYŁ)")
over = sum(1 for e in bag_errs if e > 5)
print(f"    zawyżeń >5 min: {over}/{n} ({100*over/max(n,1):.0f}%)")

ns, los, mds, his = stats(pos_static_err)
npj, lop, mdp, hip = stats(pos_proj_err)
print(f"\n[2] POZYCYJNY DOWÓD — błąd pozycji vs REALNA (interpolacja faktycznych zdarzeń)")
print(f"    KOTWICA STATYCZNA (Ziomek): mediana {mds:.2f} km  (~{mds/KMH_CITY*60:.1f} min dojazdu)")
print(f"    PROJEKCJA (fix):            mediana {mdp:.2f} km  (~{mdp/KMH_CITY*60:.1f} min dojazdu)")
if mds > 0:
    print(f"    -> redukcja błędu pozycji: {100*(1-mdp/mds):.0f}%")

print(f"\n[3] ODZYSK KOORD — realny wiek termiczny worst order <= 35 min (czyli BYŁO wykonalne)")
print(f"    odzyskane projekcją: {recover}/{total} ({100*recover/max(total,1):.0f}%)")

print(f"\n[4] PRÓBKA per zlecenie (real_thermal worst; <=35 = odzyskiwalne):")
for oid, brt, ok in sorted(detail, key=lambda x: (x[1] is None, x[1] or 0)):
    print(f"    {oid}: real_thermal_worst={brt if brt is None else round(brt,1)}  {'ODZYSK' if ok else ''}")
