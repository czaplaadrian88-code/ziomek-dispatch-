"""ontime_lib — współdzielony kontrakt do liczenia SLA on-time (≤35 min) per order.

Enabler dla całej roadmapy SLA: dziś nie da się policzyć % dostaw na czas, bo
`sla_log.jsonl` nie istnieje (dokumentuje to `daily_briefing.py`). Ten moduł jest
JEDYNYM źródłem definicji on-time — importują go zarówno worker `sla_join_worker.py`
(track A), jak i harnessy replay tracków C i D. Trzymaj API czyste i stabilne.

Definicja on-time (uzgodniona z zadaniem A1):
    delivery_time_minutes = delivered_at − pickup_ready_at   (w minutach)
    on_time               = delivery_time_minutes <= 35.0
    grace=True            gdy brak pickup_ready_at → on_time = None
                          (nie da się policzyć SLA bez punktu odniesienia
                          „jedzenie gotowe"; takie zamówienie NIE jest liczone
                          jako breach — wchodzi w „grace", poza mianownikiem SLA)

Model danych (rozpoznany na żywych logach, stan 2026-06-20):
  * Dostawy (delivered_at / picked_up_at / status / kurier):
        `backfill_decisions_outcomes_v1.jsonl`
        rekord: {"order_id": ..., "outcome": {"delivered_ts", "picked_up_ts",
                 "status", "courier_id_final", ...}}
        — to kanoniczny log ZAMKNIĘTYCH dostaw (delivered_ts wypełnione).
  * pickup_ready_at (znacznik „jedzenie gotowe do odbioru"):
        `learning_log.jsonl` (+ zrotowany `learning_log.jsonl.1`)
        rekord ma top-level `pickup_ready_at` (najnowsze decyzje) lub
        zagnieżdżone `decision.pickup_ready_at` (starsze). order_id wiąże oba.

Strefa czasowa: wszystkie znaczniki w logach są ISO-8601 z offsetem UTC
(`+00:00`). `delivery_time_minutes` to różnica dwóch chwil — niezależna od
strefy. Do prezentacji / bucketowania peak↔off-peak normalizujemy do
Europe/Warsaw (`to_warsaw`), a naiwne (bez offsetu) timestampy traktujemy
jako UTC (fail-soft) — patrz `parse_ts`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any, Iterable, Optional

# Próg SLA on-time (minuty od pickup_ready_at do delivered_at).
ON_TIME_THRESHOLD_MIN = 35.0

# Europe/Warsaw bez zależności od tzdata: CET=+1, CEST=+2.
# Liczymy offset DST samodzielnie (ostatnia niedziela marca → ostatnia niedziela
# października), bo zależność od pakietu `zoneinfo`/`tzdata` bywa krucha na tym hoście.
_WARSAW_STD = timezone(timedelta(hours=1))   # CET
_WARSAW_DST = timezone(timedelta(hours=2))   # CEST


def _last_sunday(year: int, month: int) -> datetime:
    """Ostatnia niedziela danego miesiąca, 01:00 UTC (moment przełączenia DST w UE)."""
    if month == 12:
        nxt = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        nxt = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    d = nxt - timedelta(days=1)
    # cofnij do niedzieli (weekday(): Mon=0..Sun=6)
    d = d - timedelta(days=(d.weekday() - 6) % 7)
    return d.replace(hour=1, minute=0, second=0, microsecond=0)


def warsaw_tz_for(dt_utc: datetime) -> timezone:
    """Zwraca offset Warszawy (CET/CEST) właściwy dla danej chwili UTC."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    dt_utc = dt_utc.astimezone(timezone.utc)
    start = _last_sunday(dt_utc.year, 3)    # marzec → CEST
    end = _last_sunday(dt_utc.year, 10)     # październik → CET
    return _WARSAW_DST if start <= dt_utc < end else _WARSAW_STD


def parse_ts(value: Any) -> Optional[datetime]:
    """Parsuje ISO-8601 → aware datetime (UTC).

    Fail-soft:
      * None / "" / nie-string → None
      * sufiks „Z" akceptowany jako UTC
      * naiwny timestamp (bez offsetu) → traktowany jako UTC
        (naprawa null/naiwnych stref wymagana przez zadanie)
    Zwraca zawsze datetime z tzinfo=UTC albo None.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        # epoch seconds (rzadkie, ale fail-soft)
        try:
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_warsaw(value: Any) -> Optional[datetime]:
    """Parsuje i konwertuje znacznik do strefy Europe/Warsaw (aware)."""
    dt = parse_ts(value)
    if dt is None:
        return None
    return dt.astimezone(warsaw_tz_for(dt))


def is_peak(dt_warsaw_or_value: Any) -> Optional[bool]:
    """Czy chwila wypada w oknie peak (godziny szczytu) wg czasu warszawskiego.

    Peak = okna obiadowo-wieczorne ruchu food: 12:00–14:59 oraz 17:00–20:59.
    Zwraca None gdy timestamp nieparsowalny. Klasyfikacja używana wyłącznie do
    raportowania peak vs off-peak (NIE wpływa na definicję on-time).
    """
    if isinstance(dt_warsaw_or_value, datetime):
        dt = dt_warsaw_or_value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(warsaw_tz_for(dt.astimezone(timezone.utc)))
    else:
        dt = to_warsaw(dt_warsaw_or_value)
    if dt is None:
        return None
    h = dt.hour
    return (12 <= h < 15) or (17 <= h < 21)


def compute_on_time(
    order_id: Any,
    decisions_index: dict,
    deliveries_index: dict,
) -> dict:
    """Liczy realny on-time dla jednego zamówienia.

    Args:
        order_id: id zamówienia (porównywane jako str).
        decisions_index: dict order_id(str) -> rekord decyzji; musi nieść
            `pickup_ready_at` (patrz `build_indices`). To źródło punktu
            odniesienia „jedzenie gotowe".
        deliveries_index: dict order_id(str) -> rekord dostawy; musi nieść
            `delivered_at` (a opcjonalnie `picked_up_at`, `status`,
            `courier_id`). To źródło chwili doręczenia.

    Returns dict (kontrakt — nie zmieniaj kluczy bez zgody tracków C/D):
        {
          "order_id": str,
          "delivered_at": str|None,      # ISO UTC (znormalizowany) albo None
          "pickup_ready_at": str|None,   # ISO UTC (znormalizowany) albo None
          "delivery_time_minutes": float|None,
          "on_time": bool|None,          # None gdy grace lub brak dostawy
          "grace": bool,                 # True gdy brak pickup_ready_at
          "status": str|None,            # status outcome (np. 'delivered')
          "courier_id": str|None,
          "picked_up_at": str|None,      # ISO UTC albo None (diagnostyka)
          "delivered_at_warsaw": str|None,  # do bucketowania peak/off-peak
          "is_peak": bool|None,
          "reason": str|None,            # gdy nie da się policzyć
        }

    Reguły:
      * Brak rekordu dostawy lub brak delivered_at → on_time=None,
        reason="no_delivery". (zamówienie nie jest „zamkniętą dostawą")
      * Brak pickup_ready_at → grace=True, on_time=None, reason="grace_no_ready".
      * delivered_at < pickup_ready_at → delivery_time_minutes ujemny;
        zwracamy wartość jak jest (NIE zerujemy) i ustawiamy
        reason="negative_delivery_time" jako flagę data-quality, ale
        on_time liczymy normalnie (ujemny < 35 → on_time=True).
    """
    oid = str(order_id)
    deliv = deliveries_index.get(oid)
    dec = decisions_index.get(oid)

    result = {
        "order_id": oid,
        "delivered_at": None,
        "pickup_ready_at": None,
        "delivery_time_minutes": None,
        "on_time": None,
        "grace": False,
        "status": None,
        "courier_id": None,
        "picked_up_at": None,
        "delivered_at_warsaw": None,
        "is_peak": None,
        "reason": None,
    }

    if not deliv:
        result["reason"] = "no_delivery"
        return result

    delivered_raw = deliv.get("delivered_at")
    delivered_dt = parse_ts(delivered_raw)
    if delivered_dt is None:
        result["reason"] = "no_delivery"
        return result

    result["delivered_at"] = delivered_dt.isoformat()
    result["status"] = deliv.get("status")
    result["courier_id"] = (
        str(deliv["courier_id"]) if deliv.get("courier_id") is not None else None
    )
    pu = parse_ts(deliv.get("picked_up_at"))
    if pu is not None:
        result["picked_up_at"] = pu.isoformat()
    dw = delivered_dt.astimezone(warsaw_tz_for(delivered_dt))
    result["delivered_at_warsaw"] = dw.isoformat()
    result["is_peak"] = is_peak(dw)

    ready_raw = dec.get("pickup_ready_at") if dec else None
    ready_dt = parse_ts(ready_raw)
    if ready_dt is None:
        # grace — brak punktu odniesienia, NIE liczymy jako breach
        result["grace"] = True
        result["reason"] = "grace_no_ready"
        return result

    result["pickup_ready_at"] = ready_dt.isoformat()
    dt_min = (delivered_dt - ready_dt).total_seconds() / 60.0
    result["delivery_time_minutes"] = round(dt_min, 4)
    result["on_time"] = bool(dt_min <= ON_TIME_THRESHOLD_MIN)
    if dt_min < 0:
        result["reason"] = "negative_delivery_time"
    return result


# --------------------------------------------------------------------------- #
# Ładowanie indeksów                                                          #
# --------------------------------------------------------------------------- #
def _iter_jsonl(path: str) -> Iterable[dict]:
    """Strumieniowy czytnik JSONL, fail-soft per linia (porzuca złe linie)."""
    try:
        f = open(path, "r", errors="replace")
    except FileNotFoundError:
        return
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, dict):
                yield obj


def _extract_pickup_ready(rec: dict) -> Optional[str]:
    """Wyciąga pickup_ready_at z rekordu learning_log (top-level lub w decision)."""
    v = rec.get("pickup_ready_at")
    if v:
        return v
    dec = rec.get("decision")
    if isinstance(dec, dict):
        v = dec.get("pickup_ready_at")
        if v:
            return v
    return None


def build_decisions_index(
    paths: Iterable[str],
    since: Optional[datetime] = None,
) -> dict:
    """Buduje order_id(str) -> {"pickup_ready_at", "ts"} z logów decyzji.

    `paths` w kolejności OD NAJSTARSZEGO do NAJNOWSZEGO (np.
    [learning_log.jsonl.1, learning_log.jsonl]) — późniejsze wpisy nadpisują
    wcześniejsze, więc wygrywa najświeższy znany pickup_ready_at dla zamówienia.
    `since`: jeśli podane, pomija rekordy ze znacznikiem `ts` starszym niż since
    (fail-soft: rekord bez `ts` nigdy nie jest odfiltrowany).
    """
    idx: dict = {}
    for path in paths:
        for rec in _iter_jsonl(path):
            oid = rec.get("order_id")
            if oid is None:
                continue
            pr = _extract_pickup_ready(rec)
            if not pr:
                continue
            if since is not None:
                ts = parse_ts(rec.get("ts"))
                if ts is not None and ts < since:
                    continue
            idx[str(oid)] = {"pickup_ready_at": pr, "ts": rec.get("ts")}
    return idx


def build_deliveries_index(
    paths: Iterable[str],
    since: Optional[datetime] = None,
    closed_only: bool = True,
) -> dict:
    """Buduje order_id(str) -> rekord dostawy z backfill_decisions_outcomes.

    Mapuje pola outcome → płaski kontrakt:
        delivered_at  <- outcome.delivered_ts
        picked_up_at  <- outcome.picked_up_ts
        status        <- outcome.status
        courier_id    <- outcome.courier_id_final
    `closed_only=True`: bierze tylko rekordy z wypełnionym delivered_ts
    (czyli ZAMKNIĘTE dostawy — mianownik pokrycia SLA).
    `since`: filtruje po delivered_ts (fail-soft).
    Przy wielu rekordach na ten sam order_id wygrywa NAJPÓŹNIEJSZY delivered_ts.
    """
    idx: dict = {}
    for path in paths:
        for rec in _iter_jsonl(path):
            oid = rec.get("order_id")
            if oid is None:
                continue
            outcome = rec.get("outcome")
            if not isinstance(outcome, dict):
                continue
            delivered = outcome.get("delivered_ts")
            if closed_only and not delivered:
                continue
            d_dt = parse_ts(delivered)
            if since is not None and d_dt is not None and d_dt < since:
                continue
            flat = {
                "delivered_at": delivered,
                "picked_up_at": outcome.get("picked_up_ts"),
                "status": outcome.get("status"),
                "courier_id": outcome.get("courier_id_final"),
                "assigned_first_at": outcome.get("assigned_first_ts"),
                "pickup_to_delivery_min": outcome.get("pickup_to_delivery_min"),
            }
            oid = str(oid)
            prev = idx.get(oid)
            if prev is not None:
                prev_dt = parse_ts(prev.get("delivered_at"))
                if prev_dt is not None and d_dt is not None and d_dt <= prev_dt:
                    continue
            idx[oid] = flat
    return idx


# Domyślne ścieżki logów (jedno miejsce prawdy dla workera i harnessów replay).
DISPATCH_STATE_DIR = "/root/.openclaw/workspace/dispatch_state"
DEFAULT_DECISION_LOGS = [
    f"{DISPATCH_STATE_DIR}/learning_log.jsonl.1",  # starszy (zrotowany) — pierwszy
    f"{DISPATCH_STATE_DIR}/learning_log.jsonl",     # najnowszy — nadpisuje
]
DEFAULT_DELIVERY_LOGS = [
    f"{DISPATCH_STATE_DIR}/backfill_decisions_outcomes_v1.jsonl",
]


def build_indices(
    decision_paths: Optional[Iterable[str]] = None,
    delivery_paths: Optional[Iterable[str]] = None,
    since: Optional[datetime] = None,
) -> tuple[dict, dict]:
    """Ładuje oba indeksy (decyzje + dostawy) za jednym zamachem.

    Returns (decisions_index, deliveries_index) — gotowe do `compute_on_time`.
    Domyślne ścieżki wskazują żywe logi w dispatch_state.
    """
    decision_paths = list(decision_paths) if decision_paths else list(DEFAULT_DECISION_LOGS)
    delivery_paths = list(delivery_paths) if delivery_paths else list(DEFAULT_DELIVERY_LOGS)
    dec = build_decisions_index(decision_paths, since=since)
    deliv = build_deliveries_index(delivery_paths, since=since)
    return dec, deliv
