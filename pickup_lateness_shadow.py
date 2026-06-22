"""pickup_lateness_shadow — READ-ONLY detektor „odbiór będzie spóźniony".

Zadanie #3 (Adrian 2026-06-22, case Grzegorz Rogowski / Piwo Kaczka Sushi):
gdy Ziomek wie ≥LEAD_MIN minut PRZED umówionym odbiorem (`czas_kuriera_warsaw`),
że kurier dojedzie do restauracji PÓŹNIEJ niż umówiono, system ma móc
poinformować restaurację „odbiór będzie później o ~X min, ale kurier ją weźmie".

Ten moduł realizuje WYŁĄCZNIE warstwę DETEKCJI + pomiar (shadow). Transport do
restauracji (jaki kanał) NIE istnieje dzisiaj programowo dla restauracji
gastro.nadajesz (konto restauracji = 403 na API panelu; `czas_kuriera` tylko
do odczytu; brak telefonów/chat_id restauracji w stanie) — decyzja kanału należy
do Adriana. Dlatego tu liczymy i logujemy „co byśmy wysłali", zanim zbudujemy
wysyłkę. Zgodne z zasadą „PRZED każdym tematem: zmierz, udowodnij, dopiero flip".

Czyta TYLKO: courier_plans.json + orders_state.json. NIE modyfikuje stanu,
NIE dotyka silnika/scoringu, NIE restartuje usług. Pisze append-only shadow log.

`predicted_at` pickupu w planie jest już po OSRM + dwell + wait + clamp do
committed (plan_recheck._retime_stops). Czyli to NAJLEPSZA dostępna prognoza
dojazdu do restauracji. Nie ruszamy zamrożonej PREZENTACJI ETA — czytamy
wewnętrznie tylko do detekcji.

Invocation: python3 -m dispatch_v2.pickup_lateness_shadow [--asof ISO] [--quiet]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")

_log = logging.getLogger("pickup_lateness_shadow")
if not _log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"))
    _log.addHandler(h)
    _log.setLevel(logging.INFO)

STATE_DIR = Path("/root/.openclaw/workspace/dispatch_state")
PLANS_PATH = STATE_DIR / "courier_plans.json"
ORDERS_STATE_PATH = STATE_DIR / "orders_state.json"
SHADOW_LOG_PATH = STATE_DIR / "pickup_lateness_shadow.jsonl"

# Próg „warto powiadomić" — odbiór spóźniony o >= tyle minut vs umówiony (poziom BADGE).
LATENESS_THRESHOLD_MIN = float(os.environ.get("PICKUP_LATENESS_THRESHOLD_MIN", "5"))
# Próg ALARMU/komunikatu (modal): do umówionego odbioru zostało jeszcze >= tyle minut
# (Adrian 2026-06-22: „15 min przed, a jeśli wie wcześniej — informuje wcześniej").
# UWAGA: to NIE jest próg tłumienia — logujemy KAŻDE opóźnienie >= LATENESS_THRESHOLD_MIN
# (poziom badge w kaflu, bez progu lead) z zapisanym `lead_min`, a `is_alarm` mówi, czy
# poszedłby też alarm. Pomiar measure-first: z logu wyliczymy oba poziomy.
ALARM_LEAD_MIN = float(os.environ.get("PICKUP_LATENESS_ALARM_LEAD_MIN", "15"))


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    """ISO-8601 → aware UTC datetime. None gdy puste/nie-str/nie-parsuje.
    Identyczny kontrakt jak plan_recheck._parse_dt (offset-aware lub naive→UTC)."""
    if not s or not isinstance(s, str):
        return None
    try:
        v = s.strip()
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _hhmm_warsaw(dt: datetime) -> str:
    return dt.astimezone(WARSAW).strftime("%H:%M")


def _ceil_minute(dt: datetime) -> datetime:
    """Zaokrąglij w górę do pełnej minuty (proponowany nowy czas odbioru)."""
    if dt.second == 0 and dt.microsecond == 0:
        return dt
    return (dt + timedelta(seconds=59)).replace(second=0, microsecond=0)


def detect_late_pickups(
    orders_state: Dict[str, Any],
    plans: Dict[str, Any],
    now: datetime,
    lateness_threshold_min: float = LATENESS_THRESHOLD_MIN,
    alarm_lead_min: float = ALARM_LEAD_MIN,
) -> List[Dict[str, Any]]:
    """Czysta funkcja. Dla każdego ŻYWEGO pickupu (order assigned, jeszcze nie
    odebrany) z umówionym `czas_kuriera_warsaw`, którego prognozowany dojazd
    (`stop.predicted_at`) jest >= lateness_threshold_min po umówionym — zwróć
    zdarzenie (poziom BADGE, BEZ progu lead). `is_alarm` = czy poszedłby też
    alarm/modal (do umówionego zostało jeszcze >= alarm_lead_min minut).

    Zwraca listę dictów (bez ts/event_type — dodaje je emitter)."""
    events: List[Dict[str, Any]] = []
    if not isinstance(plans, dict):
        return events

    for cid, plan in plans.items():
        if not isinstance(plan, dict):
            continue
        if plan.get("invalidated_at"):
            continue
        for stop in (plan.get("stops") or []):
            if not isinstance(stop, dict) or stop.get("type") != "pickup":
                continue
            oid = str(stop.get("order_id"))
            rec = orders_state.get(oid)
            if not isinstance(rec, dict):
                continue
            # Tylko zlecenia jeszcze NIE odebrane (status żywy = assigned).
            # picked_up → odbiór już się stał, nieistotne. Inne = terminal.
            if rec.get("status") != "assigned":
                continue
            committed = _parse_dt(rec.get("czas_kuriera_warsaw"))
            if committed is None:
                continue  # elastyk bez umówionego czasu — nic nie obiecujemy
            predicted = _parse_dt(stop.get("predicted_at"))
            if predicted is None:
                continue

            lateness_min = (predicted - committed).total_seconds() / 60.0
            lead = (committed - now).total_seconds() / 60.0
            if lateness_min < lateness_threshold_min:
                continue  # na czas (lub w tolerancji) — nie zawracamy głowy
            # BEZ progu lead: logujemy każde opóźnienie (poziom badge); is_alarm
            # mówi, czy poszedłby też alarm (lead >= alarm_lead_min).
            events.append({
                "cid": str(cid),
                "order_id": oid,
                "restaurant": rec.get("restaurant"),
                "pickup_address": rec.get("pickup_address"),
                "committed_iso": committed.isoformat(),
                "committed_warsaw_hhmm": _hhmm_warsaw(committed),
                "predicted_iso": predicted.isoformat(),
                "predicted_warsaw_hhmm": _hhmm_warsaw(predicted),
                "suggested_pickup_warsaw_hhmm": _hhmm_warsaw(_ceil_minute(predicted)),
                "lateness_min": round(lateness_min, 1),
                "lead_min": round(lead, 1),
                "is_alarm": lead >= alarm_lead_min,
            })
    return events


def _append_shadow(event: Dict[str, Any]) -> None:
    """Append-only jsonl, fail-soft (wzór feasibility_v2._emit_c2_shadow_diff_event)."""
    try:
        with open(SHADOW_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            f.flush()
    except Exception as e:  # noqa: BLE001 — log nie może wywrócić ticku
        _log.warning("pickup_lateness shadow write failed: %s", e)


def _load(path: Path) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception as e:  # noqa: BLE001
        _log.warning("load %s failed: %s", path.name, e)
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only pickup-lateness shadow detector")
    ap.add_argument("--asof", help="ISO timestamp override (replay/test); default now UTC")
    ap.add_argument("--quiet", action="store_true", help="no stdout summary")
    args = ap.parse_args()

    now = _parse_dt(args.asof) or datetime.now(timezone.utc)
    orders_state = _load(ORDERS_STATE_PATH)
    plans = _load(PLANS_PATH)

    events = detect_late_pickups(orders_state, plans, now)
    ts = now.isoformat()
    for ev in events:
        rec = dict(ev)
        rec["ts"] = ts
        rec["event_type"] = "PICKUP_LATENESS_SHADOW"
        _append_shadow(rec)

    if not args.quiet:
        alarms = sum(1 for e in events if e.get("is_alarm"))
        _log.info("asof=%s badge(late>=%.0fmin)=%d alarm(lead>=%.0fmin)=%d",
                  _hhmm_warsaw(now), LATENESS_THRESHOLD_MIN, len(events),
                  ALARM_LEAD_MIN, alarms)
        for ev in events:
            _log.info(
                "  cid=%s oid=%s %s: umówiony %s → prognoza %s (+%.0f min late, %.0f min do odbioru) "
                "→ proponowany odbiór %s",
                ev["cid"], ev["order_id"], ev.get("restaurant"),
                ev["committed_warsaw_hhmm"], ev["predicted_warsaw_hhmm"],
                ev["lateness_min"], ev["lead_min"], ev["suggested_pickup_warsaw_hhmm"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
