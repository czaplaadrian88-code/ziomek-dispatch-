"""Jeden evaluator czasu odbioru dla wszystkich lifecycle writerów planu.

Case 491870 pokazał, że R27 był oceniany przy NEW_ORDER, ale finalny plan po
panel override / recanon / regeneracji nie niósł tej oceny. Ten moduł jest
czysty i nie wysyła powiadomień. Writer zapisuje wynik w tym samym CAS co plan,
a istniejący Ops13 konsumuje aktywne zdarzenia klasy ``Alarm``.

Ważne: ``Alarm`` tutaj oznacza wyłącznie widoczność planowego opóźnienia odbioru
>40 min vs committed. Nie jest ``core.alarm_certificate`` i nie daje prawa do
podniesienia R6 35→40 ani do osłabienia jakiegokolwiek HARD.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional

from dispatch_v2 import common as C


SCHEMA = "dispatch.pickup_time_rules.v1"
ALARM_EVENT_TYPE = "PICKUP_COMMITTED_LATE"
ALARM_EVENT_CLASS = "Alarm"
ALARM_CHANNEL = "coordinator_console"


def plan_alarm_limit_min() -> float:
    """Publiczny odczyt dialu dla konsumentów; wartość nadal należy do common."""
    return float(C.PICKUP_PLAN_ALARM_LATE_MIN)


def parse_datetime(value: Any) -> Optional[datetime]:
    """Panel/state ISO/datetime → aware UTC; naive oznacza Warsaw.

    ``czas_kuriera_warsaw`` bywa zapisywany bez offsetu. Traktowanie takiego
    napisu jak UTC przesuwa regułę o dwie godziny; delegujemy więc najpierw do
    wspólnego parsera panelu, z tolerancyjnym fallbackiem dla formatu bez sekund.
    """
    parsed = C.parse_panel_timestamp(value)
    if parsed is not None:
        return parsed
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=C.WARSAW)
    return dt.astimezone(timezone.utc)


def committed_pickup_delta(committed: Any, predicted: Any) -> Optional[Dict[str, Any]]:
    """Wspólna arytmetyka R27: znak (+ późno), moduł i znormalizowane czasy."""
    committed_dt = parse_datetime(committed)
    predicted_dt = parse_datetime(predicted)
    if committed_dt is None or predicted_dt is None:
        return None
    delta_min = (predicted_dt - committed_dt).total_seconds() / 60.0
    return {
        "committed_at": committed_dt.isoformat(),
        "predicted_at": predicted_dt.isoformat(),
        "delta_min": delta_min,
        "absolute_delta_min": abs(delta_min),
        "direction": "pickup_early" if delta_min < 0 else "pickup_late",
    }


def evaluate_committed_pickup(order_id: Any, committed: Any, predicted: Any) -> Optional[Dict[str, Any]]:
    """Ocena jednego odbioru według kanonicznych progów, bez I/O."""
    delta = committed_pickup_delta(committed, predicted)
    if delta is None:
        return None
    strict_limit = float(C.OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN)
    alarm_limit = plan_alarm_limit_min()
    late_min = max(0.0, float(delta["delta_min"]))
    return {
        "order_id": str(order_id),
        **delta,
        "delta_min": round(float(delta["delta_min"]), 3),
        "absolute_delta_min": round(float(delta["absolute_delta_min"]), 3),
        "late_min": round(late_min, 3),
        "r27_strict_limit_min": strict_limit,
        "r27_strict_breach": float(delta["absolute_delta_min"]) > strict_limit,
        "plan_alarm_limit_min": alarm_limit,
        # Granica jest ŚCIŚLE >40, zgodnie z poleceniem ownera; 40.000 = bez Alarmu.
        "plan_alarm": late_min > alarm_limit,
    }


def evaluate_plan(
    courier_id: Any,
    stops: Iterable[Mapping[str, Any]],
    orders_state: Mapping[str, Any],
    now: datetime,
    *,
    source: str,
) -> Dict[str, Any]:
    """Ewaluuj finalne pickupy planu po wszystkich reorderach/pinach/retime.

    Tylko aktywny ``assigned`` z prawdziwym committed jest oceniany. Brak danych
    jest jawnie liczony, ale nie syntetyzuje alarmu. Wynik nie zawiera PII.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    rows = []
    missing = []
    for stop in stops or ():
        if not isinstance(stop, Mapping) or stop.get("type") != "pickup":
            continue
        oid = str(stop.get("order_id"))
        record = orders_state.get(oid)
        if not isinstance(record, Mapping) or record.get("status") != "assigned":
            continue
        row = evaluate_committed_pickup(
            oid,
            record.get("czas_kuriera_warsaw"),
            stop.get("predicted_at"),
        )
        if row is None:
            missing.append(oid)
            continue
        rows.append(row)

    alarms = [
        {
            "event_class": ALARM_EVENT_CLASS,
            "event_type": ALARM_EVENT_TYPE,
            "channel": ALARM_CHANNEL,
            "requires_coordinator_confirmation": True,
            "courier_id": str(courier_id),
            "order_id": row["order_id"],
            "committed_at": row["committed_at"],
            "predicted_at": row["predicted_at"],
            "lateness_min": row["late_min"],
            "threshold_min": row["plan_alarm_limit_min"],
        }
        for row in rows
        if row["plan_alarm"]
    ]
    return {
        "schema": SCHEMA,
        "evaluated_at": now.astimezone(timezone.utc).isoformat(),
        "source": str(source),
        "r27_strict_limit_min": float(C.OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN),
        "plan_alarm_limit_min": plan_alarm_limit_min(),
        "evaluated_count": len(rows),
        "missing_count": len(missing),
        "missing_order_ids": sorted(missing),
        "r27_breach_count": sum(bool(row["r27_strict_breach"]) for row in rows),
        "alarm_count": len(alarms),
        "orders": rows,
        "alarms": alarms,
    }


def active_alarm_events(
    plan: Mapping[str, Any],
    orders_state: Mapping[str, Any],
    *,
    courier_id: Any = None,
):
    """Odfiltruj zdarzenia nadal aktywne w aktualnej wersji planu/stanu.

    Konsument konsoli używa tej projekcji: stare metadane po terminalnym prune nie
    mogą utrzymać alarmu, jeśli w planie nie ma już pickupu lub order nie jest assigned.
    """
    if not isinstance(plan, Mapping) or plan.get("invalidated_at"):
        return []
    pickup_stops = {
        str(stop.get("order_id")): stop
        for stop in (plan.get("stops") or ())
        if isinstance(stop, Mapping) and stop.get("type") == "pickup"
    }
    evaluation = plan.get("pickup_time_rules")
    if not isinstance(evaluation, Mapping) or evaluation.get("schema") != SCHEMA:
        return []
    result = []
    for event in evaluation.get("alarms") or ():
        if not isinstance(event, Mapping):
            continue
        oid = str(event.get("order_id"))
        record = orders_state.get(oid)
        stop = pickup_stops.get(oid)
        if not (isinstance(record, Mapping) and isinstance(stop, Mapping)
                and record.get("status") == "assigned"):
            continue
        if not (event.get("event_class") == ALARM_EVENT_CLASS
                and event.get("event_type") == ALARM_EVENT_TYPE
                and event.get("channel") == ALARM_CHANNEL
                and event.get("requires_coordinator_confirmation") is True):
            continue
        event_courier_id = str(event.get("courier_id"))
        if courier_id is not None and event_courier_id != str(courier_id):
            continue
        # Reassign pozostawia czasem starą wersję planu poprzedniego kuriera.
        # Sam status `assigned` nie wystarcza: plan, event i bieżący owner zlecenia
        # muszą wskazywać ten sam cid, inaczej alarm jest nieaktywnym artefaktem.
        if record.get("courier_id") is None or event_courier_id != str(record.get("courier_id")):
            continue
        # Fail-closed integrity: zdarzenie musi nadal odpowiadać AKTUALNEMU
        # committed i pickupowi tej wersji planu. Konsument nie tworzy Alarmu
        # z surowych danych (brak eventu => brak Alarmu), ale nie ufa też
        # zmutowanemu/staremu payloadowi.
        current = evaluate_committed_pickup(
            oid,
            record.get("czas_kuriera_warsaw"),
            stop.get("predicted_at"),
        )
        if current is None or current.get("plan_alarm") is not True:
            continue
        try:
            same_numbers = (
                abs(float(event.get("lateness_min")) - float(current["late_min"])) <= 1e-3
                and abs(float(event.get("threshold_min"))
                        - float(current["plan_alarm_limit_min"])) <= 1e-9
            )
        except (TypeError, ValueError):
            same_numbers = False
        if not same_numbers:
            continue
        if (parse_datetime(event.get("committed_at")) !=
                parse_datetime(current.get("committed_at"))
                or parse_datetime(event.get("predicted_at")) !=
                parse_datetime(current.get("predicted_at"))):
            continue
        result.append(dict(event))
    return result
