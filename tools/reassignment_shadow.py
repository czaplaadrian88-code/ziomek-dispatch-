#!/usr/bin/env python3
"""reassignment_shadow.py — OFFLINE, READ-ONLY. Dźwignia REASSIGNMENT (przerzuty
zleceń między kurierami przed odbiorem), audyt autonomii 2026-06-07.

DLACZEGO OFFLINE: jak a2_selection_shadow — shadow w hot-path raz wywalił produkcję
(V3.27.4 NameError). Tu liczymy WYŁĄCZNIE z logów (events.db audit_log + geocode_cache
+ shadow_decisions), zero ryzyka. Zgodne z Z2/Z3.

CO MIERZY (v1 — walidacja reguły człowieka na 1037 realnych przerzutach):
  Realny przerzut = COURIER_ASSIGNED z previous_cid != cid, order jeszcze nieodebrany.
  Hipoteza Adriana: koordynator przerzuca O do kuriera, którego trasa O LEPIEJ PASUJE
  ("ktoś i tak tam jedzie"), nie po obciążeniu (load-balance = tylko 11% ruchów).
  Proxy geometryczny: odległość DOSTAWY O do CENTROIDU dostaw bagu DAWCY (A) vs BIORCY (B)
  w chwili przerzutu T. Jeśli centroid B bliżej D_O niż centroid A => przerzut
  geometrycznie uzasadniony (O wpada w klaster B). Baseline losowy ~50%.

  Bag rekonstruowany single-pass z audit_log (COURIER_ASSIGNED add / DELIVERED remove /
  reassign move), okno świeżości 3h (pomija stare niezamknięte trupy).

OGRANICZENIA v1: centroid dostaw to proxy (nie pełny route_simulator); pomija pickup-
detour i okno czasowe R6. v2 (live-candidate, forward) doda route-sim + missed-opportunity.

Użycie: /root/.openclaw/venvs/dispatch/bin/python tools/reassignment_shadow.py
"""
import json, os, sqlite3, math, re, sys, statistics as st
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from zoneinfo import ZoneInfo

# L1.2 (2026-07-02): odczyt shadow_decisions ROTATION-AWARE przez kanon
# (_rotated_logs/ledger_io) — stary hardkod [żywy, .1] gubił .2.gz po rotacji
# (logrotate size 100M / daily + delaycompress). Indeks odel jest first-wins per
# oid (0 kolizji między plikami w oknie → identycznie); ścieżka = ledger_io.LEDGER.
try:
    from dispatch_v2.tools import _rotated_logs, ledger_io
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from dispatch_v2.tools import _rotated_logs, ledger_io

WAR = ZoneInfo("Europe/Warsaw")  # DST-safe CET/CEST — L2 audyt 2.0 (był fixed +2)
DB = "/root/.openclaw/workspace/dispatch_state/events.db"
GEOC = "/root/.openclaw/workspace/dispatch_state/geocode_cache.json"
SHADOW_DECISIONS = ledger_io.LEDGER["shadow"]
FRESH_H = 3.0  # okno świeżości bagu (h)


def tw(i):
    try:
        return datetime.fromisoformat(i.replace("Z", "+00:00")).astimezone(WAR).replace(tzinfo=None)
    except Exception:
        return None


def hav(a, b):
    if not a or not b:
        return None
    (la1, lo1), (la2, lo2) = a, b
    p = math.pi / 180
    x = math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(x))


def na(a):  # delivery addr -> klucz cache 'ulica numer, białystok'
    a = (a or "").strip().lower().split("/")[0].strip()
    a = re.sub(r"\s+\d+$", "", a).strip() if re.search(r"\d+\s+\d+$", a) else a
    return a + ", białystok"


def main():
    geo = {}
    d = json.load(open(GEOC))
    for k, v in d.items():
        if isinstance(v, dict) and "lat" in v and "lon" in v:
            geo[k.strip().lower()] = (v["lat"], v["lon"])
    # oid -> delivery_address (z shadow)
    odel = {}
    for r in _rotated_logs.iter_jsonl_records(SHADOW_DECISIONS, None):
        oid = str(r.get("order_id") or "")
        if oid and oid not in odel:
            odel[oid] = r.get("delivery_address") or ""

    def dcoord(oid):
        a = odel.get(oid)
        return geo.get(na(a)) if a else None

    # single-pass bag reconstruction + snapshot przy każdym przerzucie
    con = sqlite3.connect(DB)
    cur = con.cursor()
    rows = cur.execute(
        "SELECT event_type,order_id,courier_id,created_at,payload FROM audit_log "
        "WHERE event_type IN ('COURIER_ASSIGNED','COURIER_DELIVERED') ORDER BY created_at"
    ).fetchall()
    con.close()

    bag = defaultdict(dict)   # cid -> {oid: assign_ts}
    moves = []                # (T, A, B, O, bagA_oids, bagB_oids)
    for et, oid, cid, created, pl in rows:
        pl = json.loads(pl) if pl else {}
        oid = str(oid); cid = str(cid); t = tw(created)
        if et == "COURIER_DELIVERED":
            bag[cid].pop(oid, None)
            continue
        # COURIER_ASSIGNED
        prev = pl.get("previous_cid")
        if prev and str(prev) != "None" and str(prev) != cid:
            A = str(prev); B = cid
            if t:
                lo = t - timedelta(hours=FRESH_H)
                bagA = [o for o, ts in bag[A].items() if o != oid and ts and ts >= lo]
                bagB = [o for o, ts in bag[B].items() if o != oid and ts and ts >= lo]
                moves.append((t, A, B, oid, bagA, bagB))
            bag[A].pop(oid, None)
            bag[cid][oid] = t
        else:
            # zwykłe przypisanie (usuń z ewentualnego poprzedniego właściciela)
            for c in list(bag.keys()):
                bag[c].pop(oid, None)
            bag[cid][oid] = t

    # geometria: czy centroid B bliżej D_O niż centroid A
    def centroid(oids):
        cs = [dcoord(o) for o in oids]
        cs = [c for c in cs if c]
        if not cs:
            return None
        return (sum(c[0] for c in cs) / len(cs), sum(c[1] for c in cs) / len(cs))

    n_eval = 0; b_closer = 0; impr = []; skip_geo = 0; skip_empty = 0
    for t, A, B, O, bagA, bagB in moves:
        do = dcoord(O)
        if not do:
            skip_geo += 1; continue
        cA = centroid(bagA); cB = centroid(bagB)
        if cA is None or cB is None:
            skip_empty += 1; continue
        dA = hav(do, cA); dB = hav(do, cB)
        if dA is None or dB is None:
            skip_geo += 1; continue
        n_eval += 1
        if dB < dA:
            b_closer += 1
        impr.append(dA - dB)   # >0 = O bliżej klastra B (przerzut uzasadniony)

    def P(x, n):
        return f"{100*x/n:.0f}%" if n else "—"
    print("=" * 64)
    print("REASSIGNMENT SHADOW v1 — walidacja reguły człowieka (geometria)")
    print("=" * 64)
    print(f"  realnych przerzutów: {len(moves)} | oceniono geometrycznie: {n_eval} "
          f"(pominięto: brak geo O {skip_geo}, pusty bag A/B {skip_empty})")
    if n_eval:
        med = st.median(impr)
        print(f"  centroid BIORCY bliżej dostawy O niż DAWCY (przerzut uzasadniony): "
              f"{b_closer}/{n_eval} = {P(b_closer, n_eval)}  (baseline losowy ~50%)")
        print(f"  mediana poprawy (dist do klastra A − B): {med:+.2f} km "
              f"| średnia {st.mean(impr):+.2f} km")
        pos = [x for x in impr if x > 0.3]
        print(f"  ruchy z realną poprawą >0.3km: {P(len(pos), n_eval)} "
              f"(mediana poprawy w nich {st.median(pos):+.2f} km)" if pos else "")
    print("\n  WNIOSEK: jeśli B-bliżej >> 50% => przerzut jest geometryczny (reguła = "
          "'O do kuriera, którego klaster O pasuje') => to budujemy w v2 (forward shadow).")


if __name__ == "__main__":
    main()
