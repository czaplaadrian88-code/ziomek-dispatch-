"""GATE B — ekstraktor realnej trasy kuriera z GPS.

Dla pojedynczego tripu (pickup -> delivery) wyciaga z `gps_history`:
  - epizody STOPU (restauracja / klient / w trasie) wykryte geometrycznie
  - `pure_drive_min` = sama jazda restauracja->klient (korki/swiatla ZOSTAJA w jezdzie)
  - dwelle u restauracji i u klienta (byproduct -> kalibracja DWELL_PICKUP/DROPOFF)
  - flage jakosci pokrycia GPS

To jest ground truth dla porownania OSRM vs TomTom (GATE B). Stop w trasie
(korek > MIN_STOP_DUR) NIE jest odejmowany — to wlasnie ruch, ktory predyktor
ma trafic. Odejmujemy tylko stopy przy POI (odbior + wreczenie).

CLI walidacyjne:  python3 gps_trip_extractor.py --validate 484 [--limit 8]
Modul:            from gps_trip_extractor import extract_trip
"""
import argparse
import json
import math
import sqlite3
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

DB_PATH = "/root/.openclaw/workspace/dispatch_state/courier_api.db"
SLA_LOG = "/root/.openclaw/workspace/scripts/logs/sla_log.jsonl"
WARSAW = ZoneInfo("Europe/Warsaw")

# --- parametry detekcji (kalibrowalne na walidacji) ---
# KLUCZOWE: gdy kurier stoi, aplikacja przestaje slac pingi -> stop = LUKA w GPS.
# Detekcja: gap >= MIN_STOP_DUR_SEC przy przesunieciu <= STOP_MAX_DISP_M = postoj.
MAX_ACCURACY_M = 150.0      # pingi z gorsza dokladnoscia odrzucane
MIN_STOP_DUR_SEC = 100.0    # luka krotsza = swiatla/zwolnienie, NIE stop
STOP_MAX_DISP_M = 130.0     # przesuniecie przez luke <= R -> kurier stal (nie jechal)
CLUSTER_RADIUS_M = 55.0     # zapasowo: klaster nieruchomych pingow (rzadkie)
MERGE_GAP_SEC = 60.0        # dwa stopy blisko w czasie + miejscu -> scalamy (jitter GPS)
MERGE_DIST_M = 110.0
GEOFENCE_R_M = 130.0        # stop w tym promieniu od POI -> klasyfikacja po wsp.
POI_TOUCH_BUFFER_SEC = 75   # fallback bez wsp.: stop MUSI obejmowac moment pu/de (±bufor)
PRE_BUFFER_SEC = 1200       # pobieramy GPS od pu-20min (zeby zlapac dojazd do restauracji)
POST_BUFFER_SEC = 420


def haversine_m(a, b):
    """Odleglosc w metrach miedzy (lat,lon) a i b."""
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _load_pings(conn, courier_id, t0, t1):
    """Pingi GPS kuriera w oknie [t0, t1] (epoch), posortowane, odfiltrowane po accuracy."""
    rows = conn.execute(
        "SELECT recorded_at, lat, lon, speed, accuracy FROM gps_history "
        "WHERE courier_id=? AND recorded_at>=? AND recorded_at<=? ORDER BY recorded_at",
        (str(courier_id), t0, t1),
    ).fetchall()
    pings = []
    for rec, lat, lon, spd, acc in rows:
        if lat is None or lon is None:
            continue
        if acc is not None and acc > MAX_ACCURACY_M:
            continue
        pings.append({"t": int(rec), "lat": float(lat), "lon": float(lon),
                      "speed": (float(spd) if spd is not None else None)})
    return pings


def _detect_stops(pings):
    """Wykrywa postoje. Glowny sygnal: LUKA w GPS (gdy kurier stoi, aplikacja
    przestaje slac pingi). Luka >= MIN_STOP_DUR_SEC przy malym przesunieciu
    granicznych pingow = postoj. Plus zapasowo klastry nieruchomych pingow."""
    stops = []
    # 1. gap-based — dominujacy mechanizm w tych danych
    for i in range(len(pings) - 1):
        a, b = pings[i], pings[i + 1]
        dt = b["t"] - a["t"]
        if dt < MIN_STOP_DUR_SEC:
            continue
        disp = haversine_m((a["lat"], a["lon"]), (b["lat"], b["lon"]))
        if disp <= STOP_MAX_DISP_M:
            stops.append({"t_start": a["t"], "t_end": b["t"], "dur_sec": dt,
                          "lat": (a["lat"] + b["lat"]) / 2,
                          "lon": (a["lon"] + b["lon"]) / 2, "n": 2})
    # 2. cluster-based — gdyby ktorys telefon slal pingi na postoju
    n = len(pings)
    i = 0
    while i < n:
        cluster = [pings[i]]
        clat, clon = pings[i]["lat"], pings[i]["lon"]
        j = i + 1
        while j < n and haversine_m((clat, clon),
                                    (pings[j]["lat"], pings[j]["lon"])) <= CLUSTER_RADIUS_M:
            cluster.append(pings[j])
            clat = sum(p["lat"] for p in cluster) / len(cluster)
            clon = sum(p["lon"] for p in cluster) / len(cluster)
            j += 1
        if cluster[-1]["t"] - cluster[0]["t"] >= MIN_STOP_DUR_SEC and len(cluster) >= 3:
            stops.append({"t_start": cluster[0]["t"], "t_end": cluster[-1]["t"],
                          "dur_sec": cluster[-1]["t"] - cluster[0]["t"],
                          "lat": clat, "lon": clon, "n": len(cluster)})
            i = j
        else:
            i += 1
    stops.sort(key=lambda s: s["t_start"])
    return _merge_stops(stops)


def _merge_stops(stops):
    """Scala sasiednie stopy rozdzielone krotka przerwa (jitter GPS w jednym miejscu)."""
    if not stops:
        return stops
    merged = [stops[0]]
    for s in stops[1:]:
        prev = merged[-1]
        gap = s["t_start"] - prev["t_end"]
        close = haversine_m((prev["lat"], prev["lon"]), (s["lat"], s["lon"])) <= MERGE_DIST_M
        if gap <= MERGE_GAP_SEC and close:
            tot = prev["n"] + s["n"]
            prev["lat"] = (prev["lat"] * prev["n"] + s["lat"] * s["n"]) / tot
            prev["lon"] = (prev["lon"] * prev["n"] + s["lon"] * s["n"]) / tot
            prev["t_end"] = max(prev["t_end"], s["t_end"])
            prev["dur_sec"] = prev["t_end"] - prev["t_start"]
            prev["n"] = tot
        else:
            merged.append(s)
    return merged


def _classify(stops, pu, de, restaurant_ll, delivery_ll):
    """Nadaje kazdemu stopowi typ: restaurant / customer / enroute.
    Priorytet: geofence po wspolrzednych; fallback czasowy przy pu/de."""
    for s in stops:
        s["type"] = "enroute"
        sc = (s["lat"], s["lon"])
        dr = haversine_m(sc, restaurant_ll) if restaurant_ll else None
        dc = haversine_m(sc, delivery_ll) if delivery_ll else None
        s["dist_restaurant_m"] = round(dr, 1) if dr is not None else None
        s["dist_customer_m"] = round(dc, 1) if dc is not None else None
        if dr is not None and dr <= GEOFENCE_R_M and (dc is None or dr <= dc):
            s["type"] = "restaurant"
        elif dc is not None and dc <= GEOFENCE_R_M:
            s["type"] = "customer"
    # fallback czasowy gdy brak wspolrzednych lub geofence nic nie zlapal:
    # restauracja = postoj obejmujacy moment odbioru, klient = moment doreczenia
    b = POI_TOUCH_BUFFER_SEC
    if not any(s["type"] == "restaurant" for s in stops):
        cand = [s for s in stops if s["t_start"] <= pu + b and s["t_end"] >= pu - b]
        if cand:
            cand[-1]["type"] = "restaurant"   # ostatni przed wyjazdem
    if not any(s["type"] == "customer" for s in stops):
        cand = [s for s in stops if s["t_start"] <= de + b and s["t_end"] >= de - b]
        if cand:
            cand[0]["type"] = "customer"      # pierwszy po dojezdzie
    return stops


def _gps_path_km(pings):
    """Dlugosc realnie przejechanej trasy z toru GPS (km)."""
    return sum(
        haversine_m((pings[i]["lat"], pings[i]["lon"]),
                    (pings[i + 1]["lat"], pings[i + 1]["lon"]))
        for i in range(len(pings) - 1)
    ) / 1000.0


def extract_trip(conn, courier_id, pu_epoch, de_epoch,
                 restaurant_ll=None, delivery_ll=None):
    """Glowna funkcja. Zwraca dict z pure_drive_min, dwellami i jakoscia GPS.

    pu_epoch / de_epoch — epoch (UTC) momentu odbioru i doreczenia.
    restaurant_ll / delivery_ll — (lat,lon) lub None (wtedy klasyfikacja czasowa).
    """
    window_min = (de_epoch - pu_epoch) / 60.0
    all_pings = _load_pings(conn, courier_id, pu_epoch - PRE_BUFFER_SEC,
                            de_epoch + POST_BUFFER_SEC)
    trip_pings = [p for p in all_pings if pu_epoch <= p["t"] <= de_epoch]

    res = {
        "courier_id": str(courier_id),
        "window_min": round(window_min, 2),
        "gps_pings_window": len(trip_pings),
        "gps_pings_total": len(all_pings),
        "quality": "insufficient",
        "stops": [], "restaurant_dwell_min": None, "customer_dwell_min": None,
        "enroute_stop_min": 0.0, "pure_drive_min": None,
        "gps_path_km": None, "max_gap_sec": None,
        "drive_pings": 0, "drive_max_gap_sec": None,
        "drive_path_km": None, "implied_kmh": None, "problems": [],
    }
    if len(trip_pings) < 4:
        return res

    gaps = [trip_pings[i + 1]["t"] - trip_pings[i]["t"] for i in range(len(trip_pings) - 1)]
    res["max_gap_sec"] = max(gaps) if gaps else None
    res["gps_path_km"] = round(_gps_path_km(trip_pings), 2)

    # stopy szukamy na pelnym oknie (z buforem) — restauracja moze byc sprzed pu
    stops = _classify(_detect_stops(all_pings), pu_epoch, de_epoch,
                      restaurant_ll, delivery_ll)
    res["stops"] = [
        {"type": s["type"], "dur_min": round(s["dur_sec"] / 60.0, 1),
         "t_start": s["t_start"], "t_end": s["t_end"],
         "dist_restaurant_m": s.get("dist_restaurant_m"),
         "dist_customer_m": s.get("dist_customer_m")}
        for s in stops
    ]

    rest = [s for s in stops if s["type"] == "restaurant"]
    cust = [s for s in stops if s["type"] == "customer"]
    enr = [s for s in stops if s["type"] == "enroute"
           and pu_epoch <= s["t_start"] and s["t_end"] <= de_epoch]
    res["enroute_stop_min"] = round(sum(s["dur_sec"] for s in enr) / 60.0, 1)

    # dwell u restauracji = pelny czas postoju (byproduct: kalibracja DWELL_PICKUP)
    if rest:
        r = rest[-1]
        res["restaurant_dwell_min"] = round(r["dur_sec"] / 60.0, 1)
        drive_start = max(r["t_end"], pu_epoch)
    else:
        drive_start = pu_epoch
    # dwell u klienta = pelny czas postoju (byproduct: kalibracja DWELL_DROPOFF)
    if cust:
        c = cust[0]
        res["customer_dwell_min"] = round(c["dur_sec"] / 60.0, 1)
        drive_end = min(c["t_start"], de_epoch)
    else:
        drive_end = de_epoch

    pure = (drive_end - drive_start) / 60.0
    res["pure_drive_min"] = round(pure, 2) if pure > 0 else None

    # --- ocena jakosci: liczona na SEGMENCIE JAZDY, nie na calym oknie ---
    # (luka GPS przy pu/de = wykryty stop, NIE brak danych — nie karzemy za to)
    drive_pings = [p for p in trip_pings if drive_start <= p["t"] <= drive_end]
    dg = [drive_pings[i + 1]["t"] - drive_pings[i]["t"]
          for i in range(len(drive_pings) - 1)]
    res["drive_pings"] = len(drive_pings)
    res["drive_max_gap_sec"] = max(dg) if dg else None
    if len(drive_pings) >= 2:
        res["drive_path_km"] = round(_gps_path_km(drive_pings), 2)
    pd = res["pure_drive_min"]
    if pd and res["drive_path_km"] is not None:
        res["implied_kmh"] = round(res["drive_path_km"] / (pd / 60.0), 1)

    problems = []
    if not pd:
        problems.append("no_drive")
    if not cust:
        problems.append("no_customer_stop")          # drive_end skazony dwellem
    if res["gps_pings_window"] < 8:
        problems.append("too_few_pings")
    if len(drive_pings) < 4:
        problems.append("drive_uncovered")
    if (res["drive_max_gap_sec"] or 0) > 160:
        problems.append("drive_gap")
    if res["implied_kmh"] is not None and not (8.0 <= res["implied_kmh"] <= 95.0):
        problems.append("implausible_speed")
    res["problems"] = problems

    hard = {"no_drive", "no_customer_stop", "too_few_pings"}
    if (set(problems) & hard) or len(problems) >= 2:
        res["quality"] = "insufficient"
    elif problems:
        res["quality"] = "sparse"
    else:
        res["quality"] = "good"
    return res


# ----------------------------------------------------------------------
# CLI walidacyjne
# ----------------------------------------------------------------------
def _parse_warsaw(s):
    """sla_log ma mieszane formaty: ISO z offsetem (UTC) lub czas warszawski."""
    s = s.strip()
    if "T" in s:
        return datetime.fromisoformat(s).timestamp()
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=WARSAW).timestamp()


def _load_sla_trips(courier_id):
    """Tripy kuriera z sla_log, dedup po order_id, z flaga solo (brak nakladania okien)."""
    seen, trips = set(), []
    for line in open(SLA_LOG, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if str(r.get("courier_id")) != str(courier_id):
            continue
        oid = r.get("order_id")
        pu, de = r.get("picked_up_at"), r.get("delivered_at")
        if not oid or not pu or not de or oid in seen:
            continue
        seen.add(oid)
        trips.append({"oid": oid, "pu": _parse_warsaw(pu), "de": _parse_warsaw(de),
                      "pu_s": pu, "de_s": de, "restaurant": r.get("restaurant") or "?",
                      "addr": r.get("delivery_address") or "?",
                      "czasowka": bool(r.get("was_czasowka")),
                      "sla_min": r.get("delivery_time_minutes")})
    trips.sort(key=lambda t: t["pu"])
    for i, t in enumerate(trips):
        t["solo"] = not any(
            o is not t and o["pu"] < t["de"] and t["pu"] < o["de"] for o in trips
        )
    return trips


def _validate(courier_id, limit):
    conn = sqlite3.connect(DB_PATH)
    trips = _load_sla_trips(courier_id)
    solo = [t for t in trips if t["solo"] and not t["czasowka"]
            and t["sla_min"] and 5 <= t["sla_min"] <= 60]
    print(f"kurier {courier_id}: {len(trips)} tripow, "
          f"{len(solo)} solo non-czasowka 5-60min — walidacja {min(limit, len(solo))}\n")
    counts = {"good": 0, "sparse": 0, "insufficient": 0}
    for t in solo[:limit]:
        r = extract_trip(conn, courier_id, t["pu"], t["de"])
        counts[r["quality"]] += 1
        print(f"=== {t['oid']}  {t['pu_s']} -> {t['de_s'][11:]}  "
              f"({t['restaurant'][:24]} -> {t['addr'][:30]})")
        print(f"    window={r['window_min']}min  sla_log={t['sla_min']}min  "
              f"pingi={r['gps_pings_window']}  jazda={r['drive_pings']}p"
              f"/gap{r['drive_max_gap_sec']}s  drive_path={r['drive_path_km']}km  "
              f"v={r['implied_kmh']}km/h  JAKOSC={r['quality'].upper()}"
              + (f"  [{','.join(r['problems'])}]" if r["problems"] else ""))
        for s in r["stops"]:
            within = t["pu"] - 60 <= s["t_start"] <= t["de"] + 60
            tag = "  " if within else " ~"
            print(f"   {tag}stop[{s['type']:10}] {s['dur_min']:>4}min")
        print(f"    -> dwell_restauracja={r['restaurant_dwell_min']}  "
              f"dwell_klient={r['customer_dwell_min']}  "
              f"stopy_w_trasie={r['enroute_stop_min']}min")
        print(f"    -> PURE_DRIVE={r['pure_drive_min']}min  "
              f"(z window {r['window_min']}min "
              f"= {round(100*(r['pure_drive_min'] or 0)/r['window_min'])}%)\n")
    print(f"podsumowanie jakosci: {counts}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", metavar="COURIER_ID",
                    help="walidacja ekstrakcji na solo-tripach kuriera")
    ap.add_argument("--limit", type=int, default=8)
    args = ap.parse_args()
    if args.validate:
        _validate(args.validate, args.limit)
    else:
        ap.print_help()
        sys.exit(1)
