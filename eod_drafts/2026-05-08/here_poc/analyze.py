"""Phase 0 PoC analysis — czyta najnowszy results_*.jsonl, summary stats per bucket."""
import glob
import json
import statistics
import sys

RESULTS_DIR = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-08/here_poc"


def _latest_results_file():
    files = sorted(glob.glob(RESULTS_DIR + "/results_*.jsonl"))
    if not files:
        raise SystemExit("Brak plików results_*.jsonl w " + RESULTS_DIR)
    return files[-1]


def _load(path):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _stats(values):
    if not values:
        return {"n": 0}
    s = sorted(values)
    p95_idx = int(len(s) * 0.95) if len(s) > 1 else 0
    return {
        "n": len(s),
        "median": round(statistics.median(s), 2),
        "p95": round(s[p95_idx], 2),
        "min": s[0],
        "max": s[-1],
    }


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else _latest_results_file()
    rows = _load(path)
    success = sum(1 for r in rows if "delta_vs_osrm_static_min" in r)
    print("Source: " + path)
    print("Total: " + str(len(rows)) + " | Success: " + str(success))
    print()
    buckets = {}
    for r in rows:
        if "delta_vs_osrm_static_min" not in r:
            continue
        b = r["bucket"]
        if b not in buckets:
            buckets[b] = {
                "deltas_static_abs": [],
                "deltas_freeflow": [],
                "lat_here": [],
                "lat_osrm": [],
                "here_overhead": [],
            }
        buckets[b]["deltas_static_abs"].append(abs(r["delta_vs_osrm_static_min"]))
        buckets[b]["deltas_freeflow"].append(r["delta_vs_osrm_freeflow_min"])
        buckets[b]["lat_here"].append(r["here"]["latency_ms"])
        buckets[b]["lat_osrm"].append(r["osrm"]["latency_ms"])
        buckets[b]["here_overhead"].append(r.get("here_traffic_overhead_min", 0))
    header = (
        "bucket".ljust(10) + " "
        + "n".rjust(3) + " "
        + "|d_stat| med".rjust(13) + " "
        + "|d_stat| p95".rjust(13) + " "
        + "d_free med".rjust(11) + " "
        + "here_ovh med".rjust(13) + " "
        + "lat_here p95".rjust(13) + " "
        + "lat_osrm p95".rjust(13)
    )
    print(header)
    print("-" * len(header))
    for b in ["peak", "shoulder", "offpeak"]:
        if b not in buckets:
            continue
        d = buckets[b]
        sd = _stats(d["deltas_static_abs"])
        sf = _stats(d["deltas_freeflow"])
        soh = _stats(d["here_overhead"])
        lh = _stats(d["lat_here"])
        lo = _stats(d["lat_osrm"])
        line = (
            b.ljust(10) + " "
            + str(sd["n"]).rjust(3) + " "
            + str(sd.get("median", "-")).rjust(13) + " "
            + str(sd.get("p95", "-")).rjust(13) + " "
            + str(sf.get("median", "-")).rjust(11) + " "
            + str(soh.get("median", "-")).rjust(13) + " "
            + str(lh.get("p95", "-")).rjust(13) + " "
            + str(lo.get("p95", "-")).rjust(13)
        )
        print(line)
    print()
    print("GATE A criteria: peak |d_stat| median >= 2.0 AND lat_here p95 <= 400 -> PROCEED Phase 1")


if __name__ == "__main__":
    main()
