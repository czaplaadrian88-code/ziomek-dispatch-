"""Phase 0 PoC — HERE Routing v8 vs OSRM delta measurement w Białymstoku.

Standalone script. ZERO touch produkcji. Read-only import dispatch_v2.osrm_client.
Output: results_<unix_ts>.jsonl per-call row + summary printed.

GATE A criteria (analyze.py): peak |delta_static| median >= 2.0 min AND
  lat_here p95 <= 400 ms -> PROCEED Phase 1 here_client.py post-merge.
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

HERE_BASE = "https://router.hereapi.com/v8/routes"
HERE_TIMEOUT_S = 5.0
WARSAW = ZoneInfo("Europe/Warsaw")
ENV_PATH = "/root/.openclaw/workspace/.env"
TRIPS_PATH = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-08/here_poc/trips_sample.jsonl"
RESULTS_DIR = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-08/here_poc"


def _load_api_key():
    """Czyta HERE_API_KEY z env lub .env. Fail-loud jeśli brak."""
    key = os.environ.get("HERE_API_KEY")
    if key:
        return key
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith("HERE_API_KEY"):
                    v = line.split("=", 1)[1].strip()
                    if len(v) >= 2 and v[0] in ('"', "'") and v[-1] == v[0]:
                        v = v[1:-1]
                    if v:
                        return v
    except FileNotFoundError:
        pass
    raise SystemExit("FAIL: HERE_API_KEY brak w env i w " + ENV_PATH)


def _bucket(hour):
    """peak: 15-17 | shoulder: 12-14, 18-19 | offpeak: reszta. Mirror dispatch_v2/traffic.py."""
    if 15 <= hour <= 17:
        return "peak"
    if (12 <= hour <= 14) or (18 <= hour <= 19):
        return "shoulder"
    return "offpeak"


def _here_call(from_ll, to_ll, api_key):
    """HTTP GET HERE Routing v8 summary. Returns dict z duration_traffic_s, base_duration_s, length_m, latency_ms LUB error."""
    t0 = time.time()
    params = {
        "transportMode": "car",
        "origin": str(from_ll[0]) + "," + str(from_ll[1]),
        "destination": str(to_ll[0]) + "," + str(to_ll[1]),
        "return": "summary",
        "apikey": api_key,
    }
    url = HERE_BASE + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=HERE_TIMEOUT_S) as r:
            data = json.loads(r.read().decode())
        latency_ms = round((time.time() - t0) * 1000)
        if not data.get("routes"):
            return {"error": "no_routes", "latency_ms": latency_ms}
        s = data["routes"][0]["sections"][0]["summary"]
        return {
            "duration_traffic_s": s["duration"],
            "base_duration_s": s["baseDuration"],
            "length_m": s["length"],
            "latency_ms": latency_ms,
        }
    except urllib.error.HTTPError as e:
        return {"error": "http_" + str(e.code), "latency_ms": round((time.time() - t0) * 1000)}
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
    """Parallel OSRM + HERE call dla jednej trip. Returns row dict do JSONL."""
    from_ll = trip["from_ll"]
    to_ll = trip["to_ll"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_osrm = pool.submit(_osrm_call, from_ll, to_ll)
        f_here = pool.submit(_here_call, from_ll, to_ll, api_key)
        osrm = f_osrm.result()
        here = f_here.result()
    # Cron measurement: zawsze real-time hour Warsaw (stub `hour_warsaw` ignorowany —
    # bucket label musi reflektować KIEDY HERE call faktycznie poszedł, nie hardcoded label).
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
        "here": here,
    }
    if "error" not in osrm and "error" not in here and osrm.get("adjusted_duration_s") and here.get("duration_traffic_s"):
        osrm_adj_min = osrm["adjusted_duration_s"] / 60.0
        osrm_raw_min = (osrm.get("raw_duration_s") or osrm["adjusted_duration_s"]) / 60.0
        here_traffic_min = here["duration_traffic_s"] / 60.0
        here_freeflow_min = here["base_duration_s"] / 60.0
        row["delta_vs_osrm_static_min"] = round(here_traffic_min - osrm_adj_min, 2)
        row["delta_vs_osrm_freeflow_min"] = round(here_traffic_min - osrm_raw_min, 2)
        row["here_traffic_overhead_min"] = round(here_traffic_min - here_freeflow_min, 2)
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
            ok_here = "error" not in row.get("here", {})
            mark = "OK" if (ok_osrm and ok_here) else "ERR"
            label = trip.get("oid") or trip.get("label") or "?"
            d = row.get("delta_vs_osrm_static_min", "?")
            print("  [" + str(i) + "/" + str(len(trips)) + "] " + mark + " " + str(label) + " bucket=" + row["bucket"] + " delta_static=" + str(d) + "min")
    print("DONE -> " + out_path)
    return out_path


if __name__ == "__main__":
    main()
