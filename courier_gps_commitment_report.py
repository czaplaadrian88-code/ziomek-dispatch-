"""Faza 2b — analiza shadow-logu (courier_gps_commitment_shadow.jsonl) +
rekomendacja flipu 2b-LIVE. Uruchamiane jednorazowo ~7 dni po starcie shadow
(dispatch-gps-commitment-shadow-report.timer) → raport + Telegram do Adriana.

Decyzja 2b-LIVE (wpięcie mutacji commitment z GPS w state_machine) wymaga ACK
Adriana — ten skrypt DOSTARCZA dane + rekomendację, nie flipuje niczego.

Rubryka rekomendacji (konserwatywna):
- would_apply < MIN_SIGNAL_EVENTS  → HOLD, za mało danych
- anomalie/would_apply > ANOMALY_MAX_RATIO → DO NOT FLIP (fałszywe geofence?)
- median(gps_ahead) ≥ MIN_AHEAD_SEC → RECOMMEND FLIP (GPS realnie wyprzedza panel)
- inaczej → HOLD (wartość niska)
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

SHADOW_LOG_PATH = "/root/.openclaw/workspace/dispatch_state/courier_gps_commitment_shadow.jsonl"
REPORT_DIR = "/root/.openclaw/workspace/scripts/logs"

MIN_SIGNAL_EVENTS = 30        # min would_apply, by w ogóle rozważać flip
ANOMALY_MAX_RATIO = 0.10      # max anomalie / would_apply
MIN_AHEAD_SEC = 120           # GPS musi wyprzedzać panel medianowo ≥ 2 min


def _iso_to_epoch(iso):
    if not iso or not isinstance(iso, str):
        return None
    try:
        s = iso.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _pct(values, q):
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def _median(values):
    return _pct(values, 0.5)


def load_records(path: str = SHADOW_LOG_PATH, since_epoch=None) -> list:
    out = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if since_epoch is not None:
                    ts = _iso_to_epoch(r.get("observed_at"))
                    if ts is not None and ts < since_epoch:
                        continue
                out.append(r)
    except (FileNotFoundError, OSError):
        pass
    return out


def analyze(records: list) -> dict:
    by_type = {}
    pickup_ahead_secs, timing_deltas, orders = [], [], set()
    counts = {"pickup_ahead": 0, "delivered_ahead": 0, "timing": 0,
              "mismatch": 0, "orphan": 0, "would_apply": 0}
    for r in records:
        t = r.get("divergence_type")
        by_type[t] = by_type.get(t, 0) + 1
        orders.add(r.get("order_id"))
        if r.get("would_apply"):
            counts["would_apply"] += 1
        if t == "GPS_PICKUP_AHEAD":
            counts["pickup_ahead"] += 1
            v = r.get("gps_ahead_sec")
            if isinstance(v, (int, float)):
                pickup_ahead_secs.append(v)
        elif t == "GPS_DELIVERED_AHEAD":
            counts["delivered_ahead"] += 1
        elif t == "GPS_PICKUP_TIMING":
            counts["timing"] += 1
            v = r.get("timing_delta_sec")
            if isinstance(v, (int, float)):
                timing_deltas.append(v)
        elif t == "COURIER_MISMATCH":
            counts["mismatch"] += 1
        elif t == "GPS_ORPHAN":
            counts["orphan"] += 1
    return {
        "records": len(records),
        "unique_orders": len(orders),
        "by_type": by_type,
        "would_apply": counts["would_apply"],
        "pickup_ahead": counts["pickup_ahead"],
        "delivered_ahead": counts["delivered_ahead"],
        "timing_divergences": counts["timing"],
        "anomalies": counts["mismatch"] + counts["orphan"],
        "courier_mismatch": counts["mismatch"],
        "orphan": counts["orphan"],
        "pickup_ahead_median_sec": _median(pickup_ahead_secs),
        "pickup_ahead_p90_sec": _pct(pickup_ahead_secs, 0.9),
        "timing_median_sec": _median(timing_deltas),
        "timing_p90_sec": _pct(timing_deltas, 0.9),
    }


def recommend(s: dict):
    """Zwraca (verdict_code, reasoning)."""
    signal = s["would_apply"]
    anomalies = s["anomalies"]
    ratio = (anomalies / signal) if signal else (1.0 if anomalies else 0.0)
    if signal < MIN_SIGNAL_EVENTS:
        return ("HOLD_NEED_MORE_DATA",
                f"Za mało sygnału (would_apply={signal} < {MIN_SIGNAL_EVENTS}). "
                f"Przedłuż shadow aż n≥{MIN_SIGNAL_EVENTS}.")
    if ratio > ANOMALY_MAX_RATIO:
        return ("DO_NOT_FLIP_ANOMALIES",
                f"Anomalie {anomalies} = {ratio:.0%} would_apply (> {ANOMALY_MAX_RATIO:.0%}). "
                f"Możliwe fałszywe geofence / courier mismatch — zbadaj geofence przed flipem.")
    ahead = s["pickup_ahead_median_sec"] or 0
    if ahead >= MIN_AHEAD_SEC:
        return ("RECOMMEND_FLIP",
                f"GPS wyprzedza panel medianowo {int(ahead)}s (would_apply={signal}, "
                f"anomalie {anomalies}={ratio:.0%}). Wartość 2b-LIVE potwierdzona. "
                f"Następny krok: konsument w state_machine za flagą "
                f"ENABLE_COURIER_GPS_COMMITMENT + replay + ACK.")
    return ("HOLD_LOW_VALUE",
            f"GPS nie wyprzedza panelu istotnie (median {int(ahead)}s < {MIN_AHEAD_SEC}s). "
            f"Wartość pełnego flipu niska — rozważ tylko korektę timingu.")


def format_report(stats: dict, verdict: str, reasoning: str, window_days: float) -> str:
    bt = ", ".join(f"{k}={v}" for k, v in sorted(stats["by_type"].items())) or "brak"
    return (
        f"📊 Faza 2b shadow — analiza ({window_days:.0f}d)\n"
        f"Rekomendacja: {verdict}\n"
        f"{reasoning}\n"
        f"—\n"
        f"rekordy={stats['records']} | zlecenia={stats['unique_orders']}\n"
        f"would_apply={stats['would_apply']} "
        f"(pickup_ahead={stats['pickup_ahead']}, delivered_ahead={stats['delivered_ahead']})\n"
        f"GPS ahead median={stats['pickup_ahead_median_sec']}s p90={stats['pickup_ahead_p90_sec']}s\n"
        f"timing rozjazd: n={stats['timing_divergences']} "
        f"median={stats['timing_median_sec']}s p90={stats['timing_p90_sec']}s\n"
        f"anomalie={stats['anomalies']} (mismatch={stats['courier_mismatch']}, orphan={stats['orphan']})\n"
        f"typy: {bt}"
    )


def run(window_days: float = 7.0, send_telegram: bool = True,
        quiet_until_actionable: bool = False) -> dict:
    """Analiza okna + rekomendacja. Zawsze loguje do pliku/stdout.

    quiet_until_actionable=True (tryb cyklicznego timera): Telegram odzywa się
    DOPIERO gdy są realne dane do decyzji (verdict != HOLD_NEED_MORE_DATA).
    Dzięki temu cotygodniowy job milczy póki kurierzy nie jeżdżą na apce v2,
    a pingnie Adriana dokładnie wtedy, gdy decyzja o 2b-LIVE ma sens.
    """
    since = datetime.now(timezone.utc).timestamp() - window_days * 86400
    records = load_records(SHADOW_LOG_PATH, since_epoch=since)
    stats = analyze(records)
    verdict, reasoning = recommend(stats)
    report = format_report(stats, verdict, reasoning, window_days)
    print(report, flush=True)

    try:
        os.makedirs(REPORT_DIR, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with open(f"{REPORT_DIR}/gps_commitment_shadow_report_{stamp}.md", "w") as f:
            f.write(report + "\n")
    except OSError as e:
        print(f"[report] write fail: {type(e).__name__}: {e}", flush=True)

    actionable = verdict != "HOLD_NEED_MORE_DATA"
    notify = send_telegram and (actionable or not quiet_until_actionable)
    if notify:
        try:
            import telegram_utils
            telegram_utils.send_admin_alert(report)
        except Exception as e:  # Telegram nie może wywalić raportu
            print(f"[report] telegram fail: {type(e).__name__}: {e}", flush=True)

    return {"stats": stats, "verdict": verdict, "reasoning": reasoning,
            "actionable": actionable, "notified": notify}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-days", type=float, default=7.0)
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--quiet-until-actionable", action="store_true",
                    help="Telegram tylko gdy są dane do decyzji (tryb cyklicznego timera)")
    args = ap.parse_args()
    run(window_days=args.window_days, send_telegram=not args.no_telegram,
        quiet_until_actionable=args.quiet_until_actionable)
    sys.exit(0)
