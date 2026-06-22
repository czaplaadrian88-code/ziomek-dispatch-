"""pickup_lateness_review — agregacja dziennego shadow-logu „odbiór będzie później".

Czyta `dispatch_state/pickup_lateness_shadow.jsonl` (pisany co 5 min przez
dispatch-pickup-lateness-shadow.timer), agreguje zdarzenia z DZISIAJ (Warszawa) i
wysyła Adrianowi na Telegram podsumowanie + heurystyczną rekomendację (deploy vs
strojenie progów). READ-ONLY, nic nie wdraża.

Każdy tick loguje 1 wiersz per spóźnione zlecenie → liczymy DISTINCT order_id
(nie wiersze) + max opóźnienie per zlecenie + czy kiedykolwiek był alarm (is_alarm).

Invocation: python3 -m dispatch_v2.tools.pickup_lateness_review [--date YYYY-MM-DD] [--no-telegram]
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
SHADOW_LOG = Path("/root/.openclaw/workspace/dispatch_state/pickup_lateness_shadow.jsonl")


def _warsaw_date(iso: str) -> str | None:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(WARSAW).strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def _warsaw_hour(iso: str) -> int | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(WARSAW).hour
    except (ValueError, AttributeError):
        return None


_NO_SIGNAL = (
    "📭 Pickup-lateness {day}: 0 spóźnionych odbiorów (measure-first; flaga ON na backendzie,"
    " frontend nie wdrożony).\nBrak sygnału = spokojny dzień ALBO żadne zlecenie nie przekroczyło"
    " progu. Sprawdź, czy timer tikał: logs/pickup_lateness_shadow.log (linie 'badge=.. alarm=..')."
)


def build_report(day: str) -> str:
    # per order: max lateness, ever-alarm, first-seen hour, restaurant
    per_order: dict[str, dict] = {}
    ticks_with_data = set()
    if not SHADOW_LOG.exists():
        return _NO_SIGNAL.format(day=day)  # plik powstaje dopiero przy 1. spóźnieniu
    for line in SHADOW_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        ts = ev.get("ts", "")
        if _warsaw_date(ts) != day:
            continue
        oid = str(ev.get("order_id"))
        ticks_with_data.add(ts)
        o = per_order.setdefault(oid, {
            "max_late": 0.0, "alarm": False, "rest": ev.get("restaurant"),
            "hour": _warsaw_hour(ts), "committed": ev.get("committed_warsaw_hhmm"),
            "worst_new": ev.get("predicted_warsaw_hhmm"),
        })
        lt = float(ev.get("lateness_min") or 0.0)
        if lt > o["max_late"]:
            o["max_late"] = lt
            o["worst_new"] = ev.get("predicted_warsaw_hhmm")
        if ev.get("is_alarm"):
            o["alarm"] = True

    if not per_order:
        return _NO_SIGNAL.format(day=day)

    badge_orders = len(per_order)
    alarm_orders = sum(1 for o in per_order.values() if o["alarm"])
    delays = sorted(o["max_late"] for o in per_order.values())
    med = statistics.median(delays)
    mx = max(delays)
    restos = {o["rest"] for o in per_order.values() if o["rest"]}
    by_hour: dict[int, int] = defaultdict(int)
    for o in per_order.values():
        if o["hour"] is not None:
            by_hour[o["hour"]] += 1
    hours_str = " ".join(f"{h}:00→{n}" for h, n in sorted(by_hour.items()))

    # heurystyczna rekomendacja (Adrian i tak decyduje)
    if alarm_orders == 0:
        rec = ("🟡 0 alarmów (tylko badge) — komunikat/dźwięk by nie poszedł. "
               "Można wdrożyć frontend (badge), ale niski sygnał dla alarmu.")
    elif badge_orders <= 40 and mx <= 45:
        rec = ("🟢 Sygnał wygląda ZDROWO (umiarkowana liczba, opóźnienia realistyczne). "
               "Rekomendacja: WDROŻYĆ frontend (badge + alarm) po Twoim ACK.")
    elif badge_orders > 80:
        rec = ("🔴 DUŻO zdarzeń — ryzyko hałasu u restauracji. "
               "Rozważ podniesienie progu (np. late≥8 min) PRZED wdrożeniem frontendu.")
    else:
        rec = ("🟠 Sygnał umiarkowany — przejrzyj rozkład; prawdopodobnie OK do wdrożenia, "
               "ale zerknij czy opóźnienia nie są artefaktem świeżego planu.")

    return (
        f"🔔 Pickup-lateness REVIEW {day} (measure-first)\n"
        f"• spóźnione odbiory (badge, distinct): {badge_orders}\n"
        f"• z alarmem (lead≥15 min): {alarm_orders}\n"
        f"• opóźnienie +min: mediana {med:.0f} / max {mx:.0f}\n"
        f"• restauracje: {len(restos)} | ticki z danymi: {len(ticks_with_data)}\n"
        f"• wg godziny (W): {hours_str or '—'}\n"
        f"\n{rec}\n"
        f"\n⚠ Real vs artefakt + decyzja deploy = ja/Ty przy przeglądzie. Backend: flaga ON, "
        f"frontend NIE wdrożony. Deploy = rsync dist/ po ACK."
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (Warszawa); default = dziś")
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()
    day = args.date or datetime.now(WARSAW).strftime("%Y-%m-%d")
    report = build_report(day)
    print(report)
    if not args.no_telegram:
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert(report, source="pickup_lateness_review")
        except Exception as e:  # noqa: BLE001 — raport i tak na stdout/log
            print(f"[telegram fail] {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
