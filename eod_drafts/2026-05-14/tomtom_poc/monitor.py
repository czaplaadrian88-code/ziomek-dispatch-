"""TomTom PoC real-time monitor — early-warning + audit trail.

Polls results_*.jsonl every 60s, detects new batch (row count growth), computes
per-bucket aggregate, appends to timeseries jsonl. Alerts via send_admin_alert
on anomaly. Self-terminates after `--until` UTC timestamp.

Reusable harness pattern — `--results-file` + `--timeseries-file` parametrize.
Cross-ref: Lekcja #109 (downstream observable), tech-debt #38 prep pattern.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2.telegram_utils import send_admin_alert  # noqa: E402

POLL_INTERVAL_S = 60
LAT_P95_ALERT_MS = 500.0
ERR_RATE_ALERT_PCT = 60.0
JOB_MISS_MIN = 70  # alert if no growth N min post expected fire
EXPECTED_JOB_FIRES_UTC = [
    ("12:00", "shoulder"), ("13:00", "peak"), ("14:00", "peak"),
    ("15:00", "peak"), ("16:00", "shoulder"), ("17:00", "shoulder"),
]


def _percentile(values, pct):
    if not values:
        return None
    sv = sorted(values)
    idx = min(int(len(sv) * pct), len(sv) - 1)
    return sv[idx]


def _aggregate(rows):
    ok = [r for r in rows if "error" not in r.get("tomtom", {})]
    err = [r for r in rows if "error" in r.get("tomtom", {})]
    by_bucket = {}
    for r in ok:
        by_bucket.setdefault(r["bucket"], []).append(r)
    snapshot = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "n_total": len(rows), "n_ok": len(ok), "n_err": len(err),
        "err_rate_pct": round(100 * len(err) / max(1, len(rows)), 1),
        "per_bucket": {},
    }
    for b, rs in by_bucket.items():
        deltas = [abs(r["delta_vs_osrm_static_min"]) for r in rs
                  if r.get("delta_vs_osrm_static_min") is not None]
        lats = [r["tomtom"]["latency_ms"] for r in rs if r["tomtom"].get("latency_ms")]
        snapshot["per_bucket"][b] = {
            "n": len(rs),
            "abs_delta_median_min": round(_percentile(deltas, 0.5) or 0, 2),
            "abs_delta_p95_min": round(_percentile(deltas, 0.95) or 0, 2),
            "lat_p95_ms": _percentile(lats, 0.95),
        }
    return snapshot


def _check_anomalies(snap, alerted):
    fires = []
    if snap["err_rate_pct"] > ERR_RATE_ALERT_PCT and "err_rate" not in alerted:
        fires.append(f"err_rate {snap['err_rate_pct']}% > {ERR_RATE_ALERT_PCT}% (n_err={snap['n_err']}/{snap['n_total']})")
        alerted.add("err_rate")
    for b, s in snap["per_bucket"].items():
        key = f"lat_{b}"
        if s["lat_p95_ms"] and s["lat_p95_ms"] > LAT_P95_ALERT_MS and key not in alerted:
            fires.append(f"{b} p95_lat {s['lat_p95_ms']}ms > {LAT_P95_ALERT_MS}ms (n={s['n']})")
            alerted.add(key)
    return fires


def _check_job_miss(last_growth_ts_utc, alerted):
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    for hhmm, bucket in EXPECTED_JOB_FIRES_UTC:
        fire_iso = f"{today}T{hhmm}:00+00:00"
        fire = datetime.fromisoformat(fire_iso)
        if now < fire:
            continue
        age_min = (now - fire).total_seconds() / 60
        if age_min < JOB_MISS_MIN:
            continue
        key = f"miss_{hhmm}"
        if key in alerted:
            continue
        if last_growth_ts_utc and last_growth_ts_utc >= fire:
            alerted.add(key)
            continue
        alerted.add(key)
        return f"job miss {hhmm} UTC ({bucket}) — no result growth {int(age_min)} min post expected fire"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-file", required=True)
    ap.add_argument("--timeseries-file", required=True)
    ap.add_argument("--until-utc", required=True, help="ISO 8601, e.g. 2026-05-14T18:40:00+00:00")
    args = ap.parse_args()

    results = Path(args.results_file)
    ts_out = Path(args.timeseries_file)
    until = datetime.fromisoformat(args.until_utc)

    last_n = -1
    last_growth = None
    alerted = set()
    print(f"[{datetime.now(timezone.utc).isoformat()}] monitor start; until={until.isoformat()}")

    while datetime.now(timezone.utc) < until:
        try:
            rows = [json.loads(l) for l in open(results) if l.strip()] if results.exists() else []
        except Exception as e:
            print(f"[warn] read fail: {e}")
            rows = []

        if len(rows) != last_n:
            snap = _aggregate(rows)
            with open(ts_out, "a") as f:
                f.write(json.dumps(snap, ensure_ascii=False) + "\n")
            print(f"[{snap['ts_utc']}] n={snap['n_total']} ok={snap['n_ok']} err={snap['n_err']} buckets={list(snap['per_bucket'].keys())}")
            for fire in _check_anomalies(snap, alerted):
                send_admin_alert(f"⚠ TomTom PoC anomaly: {fire}")
                print(f"[ALERT] {fire}")
            if len(rows) > last_n:
                last_growth = datetime.now(timezone.utc)
            last_n = len(rows)

        miss = _check_job_miss(last_growth, alerted)
        if miss:
            send_admin_alert(f"⚠ TomTom PoC: {miss}")
            print(f"[ALERT] {miss}")

        time.sleep(POLL_INTERVAL_S)

    print(f"[{datetime.now(timezone.utc).isoformat()}] monitor done (until reached)")


if __name__ == "__main__":
    main()
