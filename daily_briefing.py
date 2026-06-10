"""daily_briefing — morning/evening Telegram summary dla Adriana (F1.4b).

Uruchamianie przez cron:
    TZ=Europe/Warsaw python3 -m dispatch_v2.daily_briefing morning
    TZ=Europe/Warsaw python3 -m dispatch_v2.daily_briefing evening
    TZ=Europe/Warsaw python3 -m dispatch_v2.daily_briefing morning --dry-run

Morning (cron 08:00) pokazuje wczorajszy dzień + status systemów.
Evening (cron 22:00) pokazuje dzisiejszy dzień + top problem restauracje (dynamic).

Sources:
    state_machine.get_all()              — delivered_at filter po zakresie
    learning_log.jsonl                   — action counter (TAK/NIE/INNY/KOORD/TIMEOUT)
    restaurant_meta.json                 — static top prep_variance_high (morning)

Nie uzywane (delivery_time_minutes=null w sla_log):
    sla_log.jsonl SLA%                   — nie da sie policzyc (no data)
"""
import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from dispatch_v2.common import WARSAW, load_config, parse_panel_timestamp, setup_logger
from dispatch_v2 import telegram_approver  # reuse _load_env + tg_request


LEARNING_LOG_PATH = "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl"
RESTAURANT_META_PATH = "/root/.openclaw/workspace/dispatch_state/restaurant_meta.json"
TELEGRAM_ENV_PATH = "/root/.openclaw/workspace/.secrets/telegram.env"

_log = setup_logger("daily_briefing", "/root/.openclaw/workspace/scripts/logs/daily_briefing.log")


# ---- time ranges ----

def _yesterday_range_utc() -> Tuple[datetime, datetime]:
    """Wczoraj 00:00 → dzis 00:00 Warsaw, w UTC."""
    now_w = datetime.now(WARSAW)
    today_start_w = now_w.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start_w = today_start_w - timedelta(days=1)
    return (
        yesterday_start_w.astimezone(timezone.utc),
        today_start_w.astimezone(timezone.utc),
    )


def _today_range_utc() -> Tuple[datetime, datetime]:
    """Dzis 00:00 → now Warsaw, w UTC."""
    now_w = datetime.now(WARSAW)
    today_start_w = now_w.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        today_start_w.astimezone(timezone.utc),
        now_w.astimezone(timezone.utc),
    )


# ---- counters ----

def _count_delivered_in_range(start_utc: datetime, end_utc: datetime) -> int:
    from dispatch_v2 import state_machine
    count = 0
    for oid, o in state_machine.get_all().items():
        if o.get("status") != "delivered":
            continue
        d = o.get("delivered_at") or o.get("czas_doreczenia")
        dt = parse_panel_timestamp(d) if d else None
        if dt is not None and start_utc <= dt < end_utc:
            count += 1
    return count


def _iter_learning_in_range(path: str, start_utc: datetime, end_utc: datetime):
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    ts_str = r.get("ts", "")
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


def _count_learning_in_range(path: str, start_utc: datetime, end_utc: datetime) -> Counter:
    counts: Counter = Counter()
    for r in _iter_learning_in_range(path, start_utc, end_utc):
        counts[r.get("action", "?")] += 1
    return counts


def _top_nie_restaurants(
    path: str, start_utc: datetime, end_utc: datetime, top_n: int = 3
) -> list:
    """Top N restauracji z najwiekszym count action=NIE w zakresie."""
    nie_per_rest: Counter = Counter()
    for r in _iter_learning_in_range(path, start_utc, end_utc):
        if r.get("action") != "NIE":
            continue
        rest = ((r.get("decision") or {}).get("restaurant")) or "?"
        nie_per_rest[rest] += 1
    return nie_per_rest.most_common(top_n)


# ---- acceptance (PANEL_AGREE / PANEL_OVERRIDE) — ETAP 3 krok 3 (Z-03) ----
# PANEL_AGREE liczy też source=telegram (ASSIGN_DIRECT NIE wchodzi do wzoru
# osobno → zero podwójnego liczenia). Peak za project_overview: 11-14 / 17-20
# Warsaw (NIE 12-14/18-20 z klasyfikatora — finding Z-20).

_PEAK_HOURS_WARSAW = frozenset(range(11, 14)) | frozenset(range(17, 20))
# Pola best NIE będące komponentami score (agregaty / warianty nieaktywne).
_COMPONENT_SKIP = {"bonus_penalty_sum"}
_COMPONENT_EXTRA = ("timing_gap_bonus", "bundle_bonus")
_CZASOWKA_PREP_MIN = 60.0


def _parse_any_iso(ts_str) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _acceptance_line(lc: Counter) -> Optional[str]:
    """Dzienna linia acceptance: AGREE/(AGREE+OVERRIDE). None gdy brak danych."""
    agree = lc.get("PANEL_AGREE", 0)
    override = lc.get("PANEL_OVERRIDE", 0)
    total = agree + override
    if total == 0:
        return None
    rate = 100.0 * agree / total
    return f"• Acceptance (panel): {agree}/{total} = {rate:.1f}% (OVERRIDE: {override})"


def _accept_rec_dims(r: dict) -> Tuple[str, str, str]:
    """(tier, pora, typ) dla rekordu PANEL_AGREE / PANEL_OVERRIDE.

    AGREE niesie pola wprost (proposed_tier/pickup_ready_at/order_created_at);
    OVERRIDE embeduje pełny decision (ta sama decyzja co w shadow_decisions
    po order_id — pending_proposals.decision_record pochodzi z shadow)."""
    if r.get("action") == "PANEL_AGREE":
        tier = r.get("proposed_tier")
        pra, oca = r.get("pickup_ready_at"), r.get("order_created_at")
    else:
        d = r.get("decision") or {}
        best = d.get("best") or {}
        tier = best.get("dwell_tier")
        if not tier:
            tier = str(best.get("v319h_bug4_tier_cap_used") or "").split("/")[0] or None
        pra, oca = d.get("pickup_ready_at"), d.get("order_created_at")
    ts = _parse_any_iso(r.get("ts"))
    pora = "?"
    if ts is not None:
        pora = "peak" if ts.astimezone(WARSAW).hour in _PEAK_HOURS_WARSAW else "off"
    typ = "?"
    t_pra, t_oca = _parse_any_iso(pra), _parse_any_iso(oca)
    if t_pra is not None and t_oca is not None:
        prep_min = (t_pra - t_oca).total_seconds() / 60.0
        typ = "czasówka" if prep_min >= _CZASOWKA_PREP_MIN else "elastyk"
    return (tier or "?", pora, typ)


def _top_override_components(override_recs: list, top_n: int = 3) -> list:
    """Top N komponentów score u OVERRIDE'owanych zwycięzców (decision.best),
    ranking po |średniej|. Zwraca [(komponent, śr, n)]."""
    vals: Dict[str, list] = {}
    for r in override_recs:
        best = ((r.get("decision") or {}).get("best")) or {}
        for k, v in best.items():
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not v:
                continue
            is_bonus = (
                k.startswith("bonus_")
                and k not in _COMPONENT_SKIP
                and not k.endswith(("_raw", "_legacy"))
                and "shadow" not in k
            )
            if is_bonus or k in _COMPONENT_EXTRA:
                vals.setdefault(k, []).append(float(v))
    rows = [
        (k, sum(v) / len(v), len(v))
        for k, v in vals.items()
    ]
    rows.sort(key=lambda x: -abs(x[1]))
    return rows[:top_n]


def _acceptance_breakdown_lines(
    path: str, start_utc: datetime, end_utc: datetime
) -> list:
    """Sekcja „Acceptance 7d": overall + per tier / pora / typ + top-3
    komponenty score OVERRIDE'owanych zwycięzców. [] gdy brak danych."""
    agree_recs, override_recs = [], []
    for r in _iter_learning_in_range(path, start_utc, end_utc):
        a = r.get("action")
        if a == "PANEL_AGREE":
            agree_recs.append(r)
        elif a == "PANEL_OVERRIDE":
            override_recs.append(r)
    total = len(agree_recs) + len(override_recs)
    if total == 0:
        return []

    def _rates(dim_idx: int) -> str:
        agg: Dict[str, list] = {}
        for r in agree_recs:
            agg.setdefault(_accept_rec_dims(r)[dim_idx], [0, 0])[0] += 1
        for r in override_recs:
            agg.setdefault(_accept_rec_dims(r)[dim_idx], [0, 0])[1] += 1
        parts = []
        for key in sorted(agg, key=lambda k: -(agg[k][0] + agg[k][1])):
            a, o = agg[key]
            parts.append(f"{key} {100.0 * a / (a + o):.0f}% ({a}/{a + o})")
        return " | ".join(parts)

    rate = 100.0 * len(agree_recs) / total
    lines = [
        "Acceptance 7d (panel):",
        f"• Razem: {len(agree_recs)}/{total} = {rate:.1f}%",
        f"• Tier: {_rates(0)}",
        f"• Pora: {_rates(1)}",
        f"• Typ: {_rates(2)}",
    ]
    top = _top_override_components(override_recs)
    if top:
        comp = " | ".join(f"{k} śr {m:+.1f} (n={n})" for k, m, n in top)
        lines.append(f"• Top komponenty OVERRIDE'owanych zwycięzców: {comp}")
    return lines


# ---- static meta ----

def _top_problem_static(top_n: int = 3) -> list:
    """Top N prep_variance_high restauracji z restaurant_meta.json (static)."""
    try:
        meta = json.loads(Path(RESTAURANT_META_PATH).read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = []
    for name, r in (meta.get("restaurants") or {}).items():
        flags = r.get("flags") or {}
        if not flags.get("prep_variance_high"):
            continue
        pv = r.get("prep_variance_min") or {}
        med = pv.get("median")
        if med is None:
            continue
        rows.append((name, med))
    rows.sort(key=lambda x: -x[1])
    return rows[:top_n]


# ---- systemd ----

def _systemd_status_block() -> str:
    services = [
        ("dispatch-panel-watcher", "watcher"),
        ("dispatch-sla-tracker",   "tracker"),
        ("dispatch-shadow",        "shadow"),
        ("dispatch-telegram",      "telegram"),
    ]
    parts = []
    for full, short in services:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", full],
                capture_output=True, text=True, timeout=5,
            )
            ok = (r.stdout.strip() == "active")
        except Exception:
            ok = False
        parts.append(f"{'✅' if ok else '❌'} {short}")
    return " ".join(parts)


# ---- formatting ----

def _format_agreement(lc: Counter) -> Tuple[int, int, float]:
    tak = lc.get("TAK", 0)
    total = sum(v for k, v in lc.items() if k in ("TAK", "NIE", "INNY", "KOORD", "TIMEOUT"))
    rate = (100.0 * tak / total) if total > 0 else 0.0
    return tak, total, rate


def format_morning() -> str:
    start, end = _yesterday_range_utc()
    yesterday_date = (end - timedelta(hours=12)).astimezone(WARSAW).strftime("%d.%m")

    delivered = _count_delivered_in_range(start, end)
    lc = _count_learning_in_range(LEARNING_LOG_PATH, start, end)
    tak, total, rate = _format_agreement(lc)
    timeout = lc.get("TIMEOUT", 0)
    top_problem = _top_problem_static(top_n=3)

    lines = [
        f"📅 Rytuał startowy (08:00 {datetime.now(WARSAW).strftime('%d.%m')})",
        "",
        f"Wczoraj ({yesterday_date}):",
        f"• Delivered: {delivered}",
        f"• Propozycje: {total}",
        f"• Agreement: {tak}/{total} = {rate:.1f}%",
    ]
    acceptance = _acceptance_line(lc)
    if acceptance:
        lines.append(acceptance)
    if timeout:
        lines.append(f"• Timeouts: {timeout}")
    lines.append("")
    # ETAP 3 krok 3: trailing 7 dni — tygodniowa widoczność bez nowego crona
    week_start = end - timedelta(days=7)
    breakdown = _acceptance_breakdown_lines(LEARNING_LOG_PATH, week_start, end)
    if breakdown:
        lines.extend(breakdown)
        lines.append("")
    if top_problem:
        lines.append("Top problem restauracji (static):")
        for name, med in top_problem:
            lines.append(f"• {name}: prep median {med:.0f} min")
        lines.append("")
    lines.append("Systemy teraz:")
    lines.append(_systemd_status_block())
    return "\n".join(lines)


def format_evening() -> str:
    start, end = _today_range_utc()
    today_date = datetime.now(WARSAW).strftime("%d.%m")

    delivered = _count_delivered_in_range(start, end)
    lc = _count_learning_in_range(LEARNING_LOG_PATH, start, end)
    tak, total, rate = _format_agreement(lc)
    timeout = lc.get("TIMEOUT", 0)
    nie = lc.get("NIE", 0)
    koord = lc.get("KOORD", 0)
    inny = lc.get("INNY", 0)
    top_nie = _top_nie_restaurants(LEARNING_LOG_PATH, start, end, top_n=3)

    lines = [
        f"🌙 Wrap-up (22:00 {today_date})",
        "",
        f"Dziś ({today_date}):",
        f"• Delivered: {delivered}",
        f"• Propozycje: {total}",
        f"• Agreement: {tak}/{total} = {rate:.1f}%",
    ]
    acceptance = _acceptance_line(lc)
    if acceptance:
        lines.append(acceptance)
    details = []
    if nie:
        details.append(f"NIE:{nie}")
    if inny:
        details.append(f"INNY:{inny}")
    if koord:
        details.append(f"KOORD:{koord}")
    if timeout:
        details.append(f"TIMEOUT:{timeout}")
    if details:
        lines.append("• " + " | ".join(details))
    lines.append("")
    if top_nie:
        lines.append("Top problem dziś (wg NIE):")
        for name, cnt in top_nie:
            lines.append(f"• {name}: {cnt}× NIE")
    else:
        lines.append("Top problem dziś: (brak NIE dziś)")
    lines.append("")
    lines.append("Systemy: " + _systemd_status_block())
    return "\n".join(lines)


# ---- telegram send ----

def _send_telegram(text: str) -> dict:
    """Token z .secrets/telegram.env, admin_id z config.json (match telegram_approver)."""
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
    parser = argparse.ArgumentParser(description="Daily briefing (morning/evening).")
    parser.add_argument("mode", choices=["morning", "evening"],
                        help="morning = wczoraj stats; evening = dzis stats")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print zamiast wysyłki do Telegram")
    args = parser.parse_args()

    if args.mode == "morning":
        body = format_morning()
    else:
        body = format_evening()

    if args.dry_run:
        print(body)
        return 0

    try:
        r = _send_telegram(body)
        if not r.get("ok"):
            _log.error(f"tg send fail: {r.get('error') or r.get('description')}")
            return 2
        _log.info(f"{args.mode} briefing sent OK (chars={len(body)})")
        return 0
    except Exception as e:
        _log.exception("send failed")
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
