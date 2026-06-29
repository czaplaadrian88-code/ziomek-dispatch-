"""Phase 0 PoC — TomTom Routing v1 (traffic=true) vs OSRM delta measurement w Białymstoku.

Standalone script. ZERO touch produkcji. Read-only import dispatch_v2.osrm_client.
Output: results_<date>.jsonl per-call row + summary printed.

GATE A criteria (analyze.py): peak |delta_static| median >= 2.0 min AND
  lat_tomtom p95 <= 400 ms -> PROCEED Phase 1 tomtom_client.py post-merge.

Mirror logiczny HERE PoC z 2026-05-08 — identyczna metodologia, swap providera.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

TOMTOM_BASE = "https://api.tomtom.com/routing/1/calculateRoute"
TOMTOM_TIMEOUT_S = 5.0
WARSAW = ZoneInfo("Europe/Warsaw")
ENV_PATH = "/root/.openclaw/workspace/.env"
TRIPS_PATH = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-14/tomtom_poc/trips_sample.jsonl"
RESULTS_DIR = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-14/tomtom_poc"


def _load_api_key():
    """Czyta TOMTOM_API_KEY z env lub .env. Fail-loud jeśli brak."""
    key = os.environ.get("TOMTOM_API_KEY")
    if key:
        return key
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith("TOMTOM_API_KEY"):
                    v = line.split("=", 1)[1].strip()
                    if len(v) >= 2 and v[0] in ('"', "'") and v[-1] == v[0]:
                        v = v[1:-1]
                    if v:
                        return v
    except FileNotFoundError:
        pass
    raise SystemExit("FAIL: TOMTOM_API_KEY brak w env i w " + ENV_PATH)


def _bucket(hour):
    """peak: 15-17 | shoulder: 12-14, 18-19 | offpeak: reszta. Mirror dispatch_v2/traffic.py."""
    if 15 <= hour <= 17:
        return "peak"
    if (12 <= hour <= 14) or (18 <= hour <= 19):
        return "shoulder"
    return "offpeak"


def _tomtom_call(from_ll, to_ll, api_key):
    """HTTP GET TomTom Routing v1 z traffic=true. Returns dict z duration_traffic_s,
    base_duration_s (no-traffic), length_m, latency_ms LUB error."""
    t0 = time.time()
    locations = "{:.6f},{:.6f}:{:.6f},{:.6f}".format(from_ll[0], from_ll[1], to_ll[0], to_ll[1])
    params = {
        "traffic": "true",
        "travelMode": "car",
        "routeType": "fastest",
        "computeTravelTimeFor": "all",  # zwraca traffic + noTraffic w summary
        "key": api_key,
    }
    url = TOMTOM_BASE + "/" + urllib.parse.quote(locations, safe=":,") + "/json?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=TOMTOM_TIMEOUT_S) as r:
            data = json.loads(r.read().decode())
        latency_ms = round((time.time() - t0) * 1000)
        routes = data.get("routes") or []
        if not routes:
            return {"error": "no_routes", "latency_ms": latency_ms}
        s = routes[0].get("summary") or {}
        # TomTom zwraca travelTimeInSeconds (z trafficiem live gdy traffic=true) +
        # noTrafficTravelTimeInSeconds (free-flow baseline). lengthInMeters identyczne semantycznie z HERE length.
        duration_traffic_s = s.get("travelTimeInSeconds")
        base_duration_s = s.get("noTrafficTravelTimeInSeconds") or s.get("travelTimeInSeconds")
        length_m = s.get("lengthInMeters")
        if duration_traffic_s is None:
            return {"error": "missing_travelTime", "latency_ms": latency_ms, "raw_summary": s}
        return {
            "duration_traffic_s": duration_traffic_s,
            "base_duration_s": base_duration_s,
            "length_m": length_m,
            "latency_ms": latency_ms,
        }
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:200]
        except Exception:
            pass
        return {"error": "http_" + str(e.code) + ":" + body, "latency_ms": round((time.time() - t0) * 1000)}
    except Exception as e:
        return {"error": type(e).__name__ + ":" + repr(e)[:200], "latency_ms": round((time.time() - t0) * 1000)}


def _osrm_call(from_ll, to_ll):
    """Wywołuje dispatch_v2.osrm_client.route z use_cache=False. Mierzy latency self.
    Returns dict z raw_duration_s, adjusted_duration_s, traffic_mult, latency_ms, fallback LUB error."""
    from dispatch_v2 import osrm_client
    t0 = time.time()
    try:
        result = osrm_client.route(tuple(from_ll), tuple(to_ll), use_cache=False)
        latency_ms = round((time.time() - t0) * 1000)
        raw = result.get("osrm_raw_duration_s") or result.get("duration_s")
        adjusted = result.get("duration_s")
        mult = result.get("traffic_multiplier") or result.get("traffic_multiplier_shadow") or 1.0
        return {
            "raw_duration_s": raw,
            "adjusted_duration_s": adjusted,
            "traffic_mult": mult,
            "latency_ms": latency_ms,
            "fallback": result.get("osrm_fallback", False),
        }
    except Exception as e:
        return {"error": type(e).__name__ + ":" + repr(e)[:200], "latency_ms": round((time.time() - t0) * 1000)}


def measure_one(trip, api_key):
    """Parallel OSRM + TomTom call dla jednej trip. Returns row dict do JSONL."""
    from_ll = trip["from_ll"]
    to_ll = trip["to_ll"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_osrm = pool.submit(_osrm_call, from_ll, to_ll)
        f_tt = pool.submit(_tomtom_call, from_ll, to_ll, api_key)
        osrm = f_osrm.result()
        tomtom = f_tt.result()
    # Cron measurement: real-time hour Warsaw (stub `hour_warsaw` w fixture ignorowany).
    hour = datetime.now(WARSAW).hour
    row = {
        "ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "oid": trip.get("oid"),
        "label": trip.get("label", ""),
        "from_ll": from_ll,
        "to_ll": to_ll,
        "hour_warsaw": hour,
        "bucket": _bucket(hour),
        "osrm": osrm,
        "tomtom": tomtom,
    }
    if "error" not in osrm and "error" not in tomtom and osrm.get("adjusted_duration_s") and tomtom.get("duration_traffic_s"):
        osrm_adj_min = osrm["adjusted_duration_s"] / 60.0
        osrm_raw_min = (osrm.get("raw_duration_s") or osrm["adjusted_duration_s"]) / 60.0
        tt_traffic_min = tomtom["duration_traffic_s"] / 60.0
        tt_freeflow_min = tomtom["base_duration_s"] / 60.0
        row["delta_vs_osrm_static_min"] = round(tt_traffic_min - osrm_adj_min, 2)
        row["delta_vs_osrm_freeflow_min"] = round(tt_traffic_min - osrm_raw_min, 2)
        row["tomtom_traffic_overhead_min"] = round(tt_traffic_min - tt_freeflow_min, 2)
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--input", default=TRIPS_PATH)
    args = parser.parse_args()
    api_key = _load_api_key()
    trips = []
    with open(args.input) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if "_comment" in d or "_format" in d:
                continue
            trips.append(d)
    trips = trips[:args.limit]
    out_path = RESULTS_DIR + "/results_" + date.today().isoformat() + ".jsonl"
    print("Measuring " + str(len(trips)) + " trips -> " + out_path + " (append)")
    with open(out_path, "a") as out:
        for i, trip in enumerate(trips, 1):
            row = measure_one(trip, api_key)
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            ok_osrm = "error" not in row.get("osrm", {})
            ok_tt = "error" not in row.get("tomtom", {})
            mark = "OK" if (ok_osrm and ok_tt) else "ERR"
            label = trip.get("oid") or trip.get("label") or "?"
            d = row.get("delta_vs_osrm_static_min", "?")
            print("  [" + str(i) + "/" + str(len(trips)) + "] " + mark + " " + str(label) + " bucket=" + row["bucket"] + " delta_static=" + str(d) + "min")
    print("DONE -> " + out_path)
    return out_path


if __name__ == "__main__":
    main()
