"""courier_ranking — daily ranking kurierów z sla_log.jsonl do Telegrama (F1.4c).

Uruchamianie przez cron:
    TZ=Europe/Warsaw python3 -m dispatch_v2.courier_ranking
    TZ=Europe/Warsaw python3 -m dispatch_v2.courier_ranking --dry-run

Czyta sla_log.jsonl → filter logged_at w zakresie dziś Warsaw (00:00 → now) →
agg per courier_id → format Telegram → send.

Dane:
    sla_log.jsonl — per-order record z delivery_time_minutes + sla_ok
    courier_names.json (F1.2) — lookup courier_id → imię

Wysyła raz dziennie o 23:30 Warsaw (po evening briefing), top 10 kurierów.
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dispatch_v2.common import WARSAW, load_config, setup_logger
from dispatch_v2 import telegram_approver  # reuse _load_env + tg_request


SLA_LOG_PATH = "/root/.openclaw/workspace/scripts/logs/sla_log.jsonl"
COURIER_NAMES_PATH = "/root/.openclaw/workspace/dispatch_state/courier_names.json"
TELEGRAM_ENV_PATH = "/root/.openclaw/workspace/.secrets/telegram.env"

MIN_DELIVERIES_FOR_RANKING = 3  # kurier z <3 dostawami odfiltrowany (outliers)
TOP_N_DISPLAY = 10
SLA_THRESHOLD_MIN = 35.0  # fallback jeśli sla_ok jest null

_log = setup_logger("courier_ranking", "/root/.openclaw/workspace/scripts/logs/courier_ranking.log")


# ---- time range ----

def _today_range_utc() -> Tuple[datetime, datetime]:
    """Dziś 00:00 → now Warsaw, w UTC."""
    now_w = datetime.now(WARSAW)
    today_start_w = now_w.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        today_start_w.astimezone(timezone.utc),
        now_w.astimezone(timezone.utc),
    )


# ---- data loading ----

def _load_courier_names() -> Dict[str, str]:
    try:
        with open(COURIER_NAMES_PATH) as f:
            return json.load(f)
    except Exception as e:
        _log.warning(f"courier_names load fail: {e}")
        return {}


def _iter_delivered_in_range(path: str, start_utc: datetime, end_utc: datetime):
    """Yield sla_log records gdzie logged_at ∈ [start_utc, end_utc)."""
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    ts_str = r.get("logged_at", "")
                    if not ts_str:
                        continue
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if start_utc <= ts < end_utc:
                        yield r
                except Exception:
                    continue
    except FileNotFoundError:
        return


# ---- ranking aggregation ----

def compute_ranking(records: List[dict]) -> List[dict]:
    """Agg per courier_id → list sorted by delivery_count desc, avg_time asc.

    Zwraca: [{courier_id, deliveries, avg_time_min, sla_pct, sla_ok_count}, ...]
    """
    per_courier: Dict[str, Dict] = defaultdict(
        lambda: {"deliveries": 0, "total_time": 0.0, "sla_ok_count": 0}
    )
    for r in records:
        cid = str(r.get("courier_id") or "")
        if not cid:
            continue
        dt_min = r.get("delivery_time_minutes")
        if dt_min is None:
            continue  # skip stare wpisy bez delivery_time
        sla_ok = r.get("sla_ok")
        if sla_ok is None:
            # Fallback: liczymy jako OK jeśli delivery_time <= threshold
            sla_ok = dt_min <= SLA_THRESHOLD_MIN
        entry = per_courier[cid]
        entry["deliveries"] += 1
        entry["total_time"] += float(dt_min)
        if sla_ok:
            entry["sla_ok_count"] += 1

    ranking = []
    for cid, e in per_courier.items():
        if e["deliveries"] < MIN_DELIVERIES_FOR_RANKING:
            continue
        avg = e["total_time"] / e["deliveries"]
        sla_pct = 100.0 * e["sla_ok_count"] / e["deliveries"]
        ranking.append({
            "courier_id": cid,
            "deliveries": e["deliveries"],
            "avg_time_min": avg,
            "sla_pct": sla_pct,
            "sla_ok_count": e["sla_ok_count"],
        })
    # Sort: więcej dostaw, potem niższe avg_time
    ranking.sort(key=lambda x: (-x["deliveries"], x["avg_time_min"]))
    return ranking


def _stars(sla_pct: float) -> str:
    if sla_pct >= 95:
        return "⭐⭐⭐⭐⭐"
    if sla_pct >= 90:
        return "⭐⭐⭐⭐"
    if sla_pct >= 75:
        return "⭐⭐⭐"
    if sla_pct >= 50:
        return "⭐⭐"
    return "⭐"


# ---- formatting ----

def format_ranking(ranking: List[dict], names: Dict[str, str]) -> str:
    today_str = datetime.now(WARSAW).strftime("%d.%m")
    if not ranking:
        return (
            f"📊 Ranking kurierów {today_str}\n"
            "\n"
            "(brak dostaw dziś — sla_log pusty lub wszystkie pre-fix)"
        )

    lines = [f"📊 Ranking kurierów {today_str}", ""]
    total_deliveries = sum(r["deliveries"] for r in ranking)
    total_sla_ok = sum(r["sla_ok_count"] for r in ranking)
    fleet_sla_pct = (100.0 * total_sla_ok / total_deliveries) if total_deliveries else 0.0

    for i, r in enumerate(ranking[:TOP_N_DISPLAY], start=1):
        name = names.get(r["courier_id"]) or f"K{r['courier_id']}"
        lines.append(
            f"{i}. {name} — {r['deliveries']} dostaw | "
            f"avg {r['avg_time_min']:.0f}min | "
            f"SLA {r['sla_pct']:.0f}% {_stars(r['sla_pct'])}"
        )

    if len(ranking) > TOP_N_DISPLAY:
        lines.append(f"... ({len(ranking) - TOP_N_DISPLAY} więcej)")

    lines.append("")
    lines.append(
        f"Razem: {len(ranking)} kurierów, {total_deliveries} dostaw, "
        f"SLA floty {fleet_sla_pct:.0f}%"
    )
    return "\n".join(lines)


# ---- telegram send ----

def _send_telegram(text: str) -> dict:
    env = telegram_approver._load_env(TELEGRAM_ENV_PATH)
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in telegram.env")
    try:
        cfg = load_config()
        admin_id = str(cfg["telegram"]["admin_id"])
    except Exception as e:
        raise RuntimeError(f"Missing telegram.admin_id in config.json: {e}")
    return telegram_approver.tg_request(
        token, "sendMessage",
        {"chat_id": admin_id, "text": text},
    )


# ---- main ----

def main() -> int:
    parser = argparse.ArgumentParser(description="Daily courier ranking → Telegram.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print zamiast wysyłki do Telegram")
    args = parser.parse_args()

    start, end = _today_range_utc()
    records = list(_iter_delivered_in_range(SLA_LOG_PATH, start, end))
    _log.info(f"loaded {len(records)} sla_log records in range {start.isoformat()} - {end.isoformat()}")
    ranking = compute_ranking(records)
    names = _load_courier_names()
    body = format_ranking(ranking, names)

    if args.dry_run:
        print(body)
        return 0

    try:
        r = _send_telegram(body)
        if not r.get("ok"):
            _log.error(f"tg send fail: {r.get('error') or r.get('description')}")
            return 2
        _log.info(f"ranking sent OK (chars={len(body)}, couriers={len(ranking)})")
        return 0
    except Exception as e:
        _log.exception("send failed")
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
