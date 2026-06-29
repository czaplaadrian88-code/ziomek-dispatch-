"""GATE B krok 3 — pomiar predyktorow OSRM vs TomTom na realnych tropach.

Forward-live: skanuje sla_log, znajduje SWIEZO zamkniete tropy (delivered w
ostatnich --max-age-min minutach), geokoduje punkt odbioru + dostawy i odpytuje
OBA predyktory na ZYWO. Pomiar tuz po zamknieciu tropu => korek TomTom ≈ ten,
ktory kurier mial w trasie (to samo okno godzinowe, ten sam dzien).

Predyktory:
  - OSRM: duration_s z mnoznikiem statycznym (to czego uzywa dispatch)
  - TomTom: travelTimeInSeconds z traffic=true (korek live)
Ground truth dokleja analyze_realworld.py przez join po oid z trips_realworld.jsonl.

Idempotentny: tropy juz w rw_results.jsonl sa pomijane. Rekord zapisywany tylko
gdy OBA predyktory zwroca wynik (blad => retry w nastepnym cyklu, dopoki trop
swiezy — naturalna obrona przed TomTom 429).

CLI (cron co ~10 min):  python3 measure_realworld.py
Backfill/smoke:         python3 measure_realworld.py --input trips_realworld.jsonl --limit 8
"""
import argparse
import html
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure_delta import _load_api_key, _osrm_call, _tomtom_call
from build_ground_truth import (MAX_DELIVERY_MIN, MIN_DELIVERY_MIN, SLA_LOG,
                                WARSAW, _load_geocode_cache,
                                _load_restaurant_coords, _norm, _parse_ts,
                                _split_city)

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rw_results.jsonl")
DEFAULT_MAX_AGE_MIN = 35    # mierz tropy zamkniete w tym oknie (korek ≈ z trasy)
TT_SLEEP_S = 0.6            # odstep miedzy callami — ochrona przed TomTom 429


def _measured_oids(path):
    """oid-y juz zmierzone (sa w pliku wynikowym) — pomijamy."""
    out = set()
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    out.add(json.loads(line)["oid"])
                except Exception:
                    pass
    return out


def _recent_closed_trips(max_age_min):
    """Tropy z sla_log zamkniete w ostatnich max_age_min minutach, dedup po oid,
    non-czasowka, czas dostawy w [MIN,MAX]."""
    now = time.time()
    seen, out = set(), []
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
        de_epoch = _parse_ts(de)
        age_min = (now - de_epoch) / 60.0
        if 0 <= age_min <= max_age_min:
            out.append({"oid": oid, "cid": str(cid), "pu": _parse_ts(pu),
                        "de": de_epoch, "age_min": round(age_min, 1),
                        "restaurant": r["restaurant"], "addr": r["delivery_address"]})
    return out


def _resolve_coords(trip, rest_map, geo_cache):
    r_ll = rest_map.get(html.unescape(trip["restaurant"]).strip().lower())
    street, city = _split_city(trip["addr"])
    d_ll = geo_cache.get(_norm(street, city)) or geo_cache.get(_norm(trip["addr"], city))
    return r_ll, d_ll


def measure_trip(trip, r_ll, d_ll, api_key):
    """Odpytuje OSRM + TomTom dla tropu. Zwraca (row, ok)."""
    osrm = _osrm_call(list(r_ll), list(d_ll))
    time.sleep(TT_SLEEP_S)
    tomtom = _tomtom_call(list(r_ll), list(d_ll), api_key)
    now = datetime.now(timezone.utc)
    row = {
        "oid": trip["oid"], "courier_id": trip["cid"],
        "restaurant": trip["restaurant"], "delivery_address": trip["addr"],
        "restaurant_ll": [round(r_ll[0], 6), round(r_ll[1], 6)],
        "delivery_ll": [round(d_ll[0], 6), round(d_ll[1], 6)],
        "pu_epoch": int(trip["pu"]), "de_epoch": int(trip["de"]),
        "measured_at_utc": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "measure_hour_warsaw": datetime.now(WARSAW).hour,
        "delivered_age_min": trip.get("age_min"),
        "osrm": osrm, "tomtom": tomtom,
    }
    ok = ("error" not in osrm and "error" not in tomtom
          and osrm.get("adjusted_duration_s") and tomtom.get("duration_traffic_s"))
    if ok:
        row["osrm_eta_min"] = round(osrm["adjusted_duration_s"] / 60.0, 2)
        row["osrm_freeflow_min"] = round(
            (osrm.get("raw_duration_s") or osrm["adjusted_duration_s"]) / 60.0, 2)
        row["tomtom_eta_min"] = round(tomtom["duration_traffic_s"] / 60.0, 2)
        row["tomtom_freeflow_min"] = round(tomtom["base_duration_s"] / 60.0, 2)
    return row, ok


def _append(path, row):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-min", type=int, default=DEFAULT_MAX_AGE_MIN)
    ap.add_argument("--limit", type=int, default=0, help="0 = bez limitu")
    ap.add_argument("--input", default="", help="zmierz tropy z pliku jsonl "
                    "(restaurant_ll/delivery_ll/oid) zamiast skanu sla_log")
    ap.add_argument("--out", default=RESULTS, help="plik wynikowy (smoke -> scratch)")
    args = ap.parse_args()
    api_key = _load_api_key()
    rest_map = _load_restaurant_coords()
    geo_cache = _load_geocode_cache()
    measured = _measured_oids(args.out)

    if args.input:
        trips = []
        for line in open(args.input, encoding="utf-8"):
            line = line.strip()
            if not line or '"oid"' not in line:
                continue
            d = json.loads(line)
            if d["oid"] in measured:
                continue
            trips.append({"oid": d["oid"], "cid": d.get("courier_id", "?"),
                          "pu": d.get("pu_epoch", 0), "de": d.get("de_epoch", 0),
                          "age_min": None, "restaurant": d["restaurant"],
                          "addr": d["delivery_address"],
                          "_ll": (tuple(d["restaurant_ll"]), tuple(d["delivery_ll"]))})
    else:
        trips = [t for t in _recent_closed_trips(args.max_age_min)
                 if t["oid"] not in measured]

    if args.limit:
        trips = trips[:args.limit]
    print(f"do zmierzenia: {len(trips)} tropow (juz zmierzonych: {len(measured)})")

    n_ok = n_err = n_nocoord = 0
    for i, t in enumerate(trips, 1):
        if "_ll" in t:
            r_ll, d_ll = t["_ll"]
        else:
            r_ll, d_ll = _resolve_coords(t, rest_map, geo_cache)
        if not r_ll or not d_ll:
            n_nocoord += 1
            continue
        row, ok = measure_trip(t, r_ll, d_ll, api_key)
        if ok:
            _append(args.out, row)
            n_ok += 1
            print(f"  [{i}/{len(trips)}] OK  {t['oid']}  "
                  f"osrm={row['osrm_eta_min']}min  tomtom={row['tomtom_eta_min']}min")
        else:
            n_err += 1
            oe = row["osrm"].get("error", "-")
            te = row["tomtom"].get("error", "-")
            print(f"  [{i}/{len(trips)}] ERR {t['oid']}  osrm={oe}  tomtom={te}")
        time.sleep(TT_SLEEP_S)
    print(f"DONE: ok={n_ok} err={n_err} brak_wsp={n_nocoord} -> {args.out}")


if __name__ == "__main__":
    main()
