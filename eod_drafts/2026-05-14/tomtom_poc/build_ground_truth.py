"""GATE B krok 2 — buduje dataset ground truth z sla_log + GPS.

Dla kazdego realnego, solo (bez bagowania) tripu:
  - geokoduje punkt odbioru (restauracja) i adres dostawy z cache'ow dispatchu
  - wyciaga z GPS `pure_drive_min` (sama jazda) + dwelle restauracja/klient
    przez gps_trip_extractor.extract_trip (klasyfikacja geofence po wsp.)
  - emituje rekord do trips_realworld.jsonl

To jest ground truth dla porownania OSRM vs TomTom (GATE B). Predyktory
(measure_realworld.py) dokladaja swoje ETA; analyze_realworld.py liczy werdykt.

Zrodla wspolrzednych (bez importu stacku dispatchu, bez live-geokodu):
  - restaurant_coords.json — nazwa restauracji -> (lat,lng)
  - geocode_cache.json     — znormalizowany adres -> (lat,lon)
Tropy z nierozwiazanym odbiorem LUB dostawa sa pomijane (mamy nadmiar danych).

CLI:  python3 build_ground_truth.py [--out PLIK] [--limit N]
"""
import argparse
import html
import json
import os
import re
import sqlite3
import statistics
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gps_trip_extractor import DB_PATH, SLA_LOG, WARSAW, extract_trip

RESTAURANT_COORDS = "/root/.openclaw/workspace/dispatch_state/restaurant_coords.json"
GEOCODE_CACHE = "/root/.openclaw/workspace/dispatch_state/geocode_cache.json"
OUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "trips_realworld.jsonl")

MIN_DELIVERY_MIN = 5.0     # ponizej = blad danych / ten sam budynek
MAX_DELIVERY_MIN = 60.0    # powyzej = multi-stop / anomalia
# miasta wielowyrazowe NAJPIERW (dluzsze dopasowanie wygrywa)
KNOWN_CITIES = [
    "Juchnowiec Kościelny", "Stanisławowo", "Księżyno Kolonia",
    "Białystok", "Złotoria", "Wasilków", "Grabówka", "Juchnowiec",
    "Kleosin", "Choroszcz", "Fasty", "Sobolewo", "Ignatki", "Księżyno",
    "Horodniany", "Niewodnica", "Zaścianki", "Dojlidy", "Klepacze",
]


def _norm(address, city):
    """Replika geocoding._normalize — klucz cache 'street, city'."""
    s = address.strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\b(lok|lokal|m|mieszkanie|pietro|piętro)\.?\s*\w+", "", s)
    s = re.sub(r"/[^\s]+", "", s)
    s = s.strip(" ,/")
    c = (city or "").strip().lower()
    if c and c not in s:
        s = f"{s}, {c}"
    return s


def _split_city(addr):
    """Wydziela miasto z konca adresu dostawy (sla_log ma je inline)."""
    a = addr.strip()
    for c in sorted(KNOWN_CITIES, key=len, reverse=True):
        if a.lower().endswith(c.lower()):
            return a[:-len(c)].strip(" ,"), c
    return a, "Białystok"


def _bucket(hour):
    if 11 <= hour <= 13:
        return "peak"
    if 9 <= hour < 11 or 14 <= hour <= 20:
        return "shoulder"
    return "offpeak"


def _load_restaurant_coords():
    raw = json.load(open(RESTAURANT_COORDS, encoding="utf-8"))
    out = {}
    for v in raw.values():
        co, lat, lng = v.get("company"), v.get("lat"), v.get("lng")
        if co and lat and lng:
            out[html.unescape(co).strip().lower()] = (lat, lng)
    return out


def _load_geocode_cache():
    raw = json.load(open(GEOCODE_CACHE, encoding="utf-8"))
    out = {}
    for k, v in raw.items():
        lat = v.get("lat")
        lon = v.get("lon", v.get("lng"))
        if lat is not None and lon is not None:
            out[k] = (lat, lon)
    return out


def _parse_ts(s):
    """sla_log ma mieszane formaty: ISO z offsetem (UTC) lub 'YYYY-MM-DD HH:MM:SS'
    (czas warszawski). Kazdy rozwiazujemy do absolutnego epoch."""
    s = s.strip()
    if "T" in s:
        return datetime.fromisoformat(s).timestamp()
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=WARSAW).timestamp()


def _load_solo_trips():
    """Wczytuje sla_log, dedup po order_id, filtruje, oznacza solo (brak
    nakladania okien [pu,de] u tego samego kuriera)."""
    seen, rows = set(), []
    for line in open(SLA_LOG, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        oid, cid = r.get("order_id"), r.get("courier_id")
        pu, de = r.get("picked_up_at"), r.get("delivered_at")
        dtm = r.get("delivery_time_minutes")
        if not oid or not cid or not pu or not de or oid in seen:
            continue
        if r.get("was_czasowka") or not dtm or not (MIN_DELIVERY_MIN <= dtm <= MAX_DELIVERY_MIN):
            continue
        if not r.get("restaurant") or not r.get("delivery_address"):
            continue
        seen.add(oid)
        rows.append({"oid": oid, "cid": str(cid),
                     "pu": _parse_ts(pu), "de": _parse_ts(de),
                     "restaurant": r["restaurant"], "addr": r["delivery_address"],
                     "sla_min": dtm})
    by_cid = {}
    for t in rows:
        by_cid.setdefault(t["cid"], []).append(t)
    solo = []
    for trips in by_cid.values():
        for t in trips:
            if not any(o is not t and o["pu"] < t["de"] and t["pu"] < o["de"]
                       for o in trips):
                solo.append(t)
    solo.sort(key=lambda t: t["pu"])
    return solo


def _atomic_write(path, lines):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _median_or(values, fallback):
    return statistics.median(values) if values else fallback


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--limit", type=int, default=0, help="0 = bez limitu")
    args = ap.parse_args()

    rest_map = _load_restaurant_coords()
    geo_cache = _load_geocode_cache()
    solo = _load_solo_trips()
    if args.limit:
        solo = solo[:args.limit]
    conn = sqlite3.connect(DB_PATH)
    print(f"solo tropy (non-czasowka, {MIN_DELIVERY_MIN:.0f}-{MAX_DELIVERY_MIN:.0f}min): "
          f"{len(solo)}")

    # --- PASS A: geokod + ekstrakcja GPS dla wszystkich ---
    cand = []   # (trip, r_ll, d_ll, ex)
    skip = {"no_restaurant_coords": 0, "no_delivery_coords": 0}
    qual = {"good": 0, "sparse": 0, "insufficient": 0}
    for t in solo:
        r_ll = rest_map.get(html.unescape(t["restaurant"]).strip().lower())
        if not r_ll:
            skip["no_restaurant_coords"] += 1
            continue
        street, city = _split_city(t["addr"])
        d_ll = geo_cache.get(_norm(street, city)) or geo_cache.get(_norm(t["addr"], city))
        if not d_ll:
            skip["no_delivery_coords"] += 1
            continue
        ex = extract_trip(conn, t["cid"], t["pu"], t["de"], r_ll, d_ll)
        qual[ex["quality"]] += 1
        cand.append((t, r_ll, d_ll, ex))
    conn.close()

    # --- kalibracja tier-2: medianowy czas "nie-jazdy" w oknie z tropow tier-1 ---
    # nondrive = window - pure_drive (residuum restauracji + dwell klienta w oknie)
    nd_by_bucket = {}
    t1 = [(t, ex) for (t, _, _, ex) in cand if ex["quality"] == "good"]
    for t, ex in t1:
        b = _bucket(datetime.fromtimestamp(t["pu"], WARSAW).hour)
        nd_by_bucket.setdefault(b, []).append(ex["window_min"] - ex["pure_drive_min"])
    nd_all = [v for vs in nd_by_bucket.values() for v in vs]
    nd_global = _median_or(nd_all, 4.0)
    nd_med = {b: (_median_or(nd_by_bucket.get(b, []), nd_global)
                  if len(nd_by_bucket.get(b, [])) >= 5 else nd_global)
              for b in ("peak", "shoulder", "offpeak")}
    print(f"tier-2 korekta nondrive_in_window (min) per bucket: "
          + ", ".join(f"{b}={nd_med[b]:.1f}" for b in nd_med))

    # --- PASS B: emisja tier-1 (GPS) + tier-2 (sla minus nondrive) ---
    out_lines = []
    tier_n = {1: 0, 2: 0}
    for t, r_ll, d_ll, ex in cand:
        hour = datetime.fromtimestamp(t["pu"], WARSAW).hour
        bucket = _bucket(hour)
        problems = list(ex["problems"])
        if ex["quality"] == "good":
            tier = 1
            gt = ex["pure_drive_min"]
            nondrive = round(ex["window_min"] - gt, 2)
        else:
            tier = 2
            nondrive = round(nd_med[bucket], 2)
            gt = round(t["sla_min"] - nondrive, 2)
            if gt < 0.5:                       # trop krotszy niz korekta
                gt = 0.5
                problems.append("tier2_underflow")
        tier_n[tier] += 1
        out_lines.append(json.dumps({
            "oid": t["oid"], "courier_id": t["cid"],
            "restaurant": t["restaurant"], "delivery_address": t["addr"],
            "pu_epoch": int(t["pu"]), "de_epoch": int(t["de"]),
            "hour_warsaw": hour, "bucket": bucket,
            "restaurant_ll": [round(r_ll[0], 6), round(r_ll[1], 6)],
            "delivery_ll": [round(d_ll[0], 6), round(d_ll[1], 6)],
            "window_min": ex["window_min"], "sla_delivery_time_min": t["sla_min"],
            "tier": tier, "ground_truth_drive_min": gt,
            "nondrive_applied_min": nondrive,
            "pure_drive_min": ex["pure_drive_min"] if tier == 1 else None,
            "restaurant_dwell_min": ex["restaurant_dwell_min"],
            "customer_dwell_min": ex["customer_dwell_min"],
            "gps_path_km": ex["gps_path_km"], "implied_kmh": ex["implied_kmh"],
            "gps_quality": ex["quality"], "problems": problems,
        }, ensure_ascii=False))
    _atomic_write(args.out, out_lines)

    # --- raport ---
    print(f"pominiete (brak wsp.): {skip}")
    print(f"jakosc GPS: {qual}")
    print(f"zapisano {len(out_lines)} tropow -> {args.out}")
    print(f"  tier-1 (GPS pure_drive): {tier_n[1]}   tier-2 (sla-korekta): {tier_n[2]}\n")
    print("--- ground truth per bucket ---")
    rows = [json.loads(x) for x in out_lines]
    for b in ("peak", "shoulder", "offpeak"):
        bt = [r for r in rows if r["bucket"] == b]
        if not bt:
            continue
        n1 = sum(1 for r in bt if r["tier"] == 1)
        gt = [r["ground_truth_drive_min"] for r in bt]
        print(f"  {b:9} n={len(bt):4} (tier-1={n1:3})  "
              f"ground_truth_drive med={statistics.median(gt):.1f}min")
    # byproduct: zmierzone dwelle (kalibracja DWELL_PICKUP/DROPOFF)
    print("\n--- byproduct: dwelle z tropow tier-1 (kalibracja DWELL_*) ---")
    for b in ("peak", "shoulder", "offpeak"):
        rd = [ex["restaurant_dwell_min"] for t, ex in t1
              if _bucket(datetime.fromtimestamp(t["pu"], WARSAW).hour) == b
              and ex["restaurant_dwell_min"]]
        cd = [ex["customer_dwell_min"] for t, ex in t1
              if _bucket(datetime.fromtimestamp(t["pu"], WARSAW).hour) == b
              and ex["customer_dwell_min"]]
        if rd or cd:
            print(f"  {b:9} dwell_restauracja med={_median_or(rd, 0):.1f}min (n={len(rd)})"
                  f"  dwell_klient med={_median_or(cd, 0):.1f}min (n={len(cd)})")


if __name__ == "__main__":
    main()
