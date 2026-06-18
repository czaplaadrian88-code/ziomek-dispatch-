"""WSPÓLNE źródło kolejności trasy kuriera — PODJAZDY (kursy).

JEDNO źródło prawdy dla kolejności stopów, importowane przez:
  - konsolę koordynatora (panel `fleet_state._build_route` → deleguje tutaj),
  - apkę kuriera (`courier_api/courier_orders` → renderuje tę samą kolejność).
Tak koordynator i kurier widzą DOKŁADNIE to samo (cel: jedno źródło).

Wierna ekstrakcja logiki z panelu `fleet_state` (2026-06-18). PURE — bez I/O,
bez OSRM, bez datetime.now → deterministyczne, łatwe do testów i identyczne
na obu powierzchniach. ETA / wrapping zostają per-powierzchnia (to prezentacja,
nie kolejność).

Reguła PODJAZDÓW: odbiory dzielone na kursy (kolejne zlecenia w oknie
≤PICKUP_MERGE_MIN min = jeden podjazd), w kursie odbiory grupowane po
restauracji, carried (picked_up) na początek; per kurs: WSZYSTKIE odbiory →
WSZYSTKIE dostawy (kolejność dostaw wg rangi planu Ziomka, inaczej wg czasu
odbioru). Minimalizuje powroty po jedzenie (R-NO-RETURN) i przeplot.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
PICKUP_MERGE_MIN = 10          # próg sklejania odbiorów w jeden podjazd (= fleet_state)
_SENTINEL = datetime.max.replace(tzinfo=WARSAW)
_BIG = 1 << 30


def _iso(s):
    """Parsuj ISO (z 'Z' lub offsetem) → aware datetime; None gdy się nie da."""
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    except Exception:
        return None


def _attr(o, name):
    """Duck-typing: obsłuż zarówno obiekt (BagOrder) jak i dict."""
    if isinstance(o, dict):
        return o.get(name)
    return getattr(o, name, None)


def _pickup_dt(o):
    return _iso(_attr(o, "czas_kuriera_warsaw"))


def pickup_runs(to_pick):
    """Podziel odbiory na PODJAZDY (kursy) + grupuj po restauracji wewnątrz kursu.
    Wejście/wyjście: listy zleceń (obiekty BagOrder-podobne albo dict-y)."""
    ordered = sorted(to_pick, key=lambda o: (_pickup_dt(o) or _SENTINEL, str(_attr(o, "order_id"))))
    runs = []
    prev = None
    for o in ordered:
        dt = _pickup_dt(o)
        if runs and prev is not None and dt is not None and (dt - prev) <= timedelta(minutes=PICKUP_MERGE_MIN):
            runs[-1].append(o)
        else:
            runs.append([o])
        if dt is not None:
            prev = dt
    out = []
    for run in runs:
        first_seen = {}
        for i, o in enumerate(run):
            first_seen.setdefault(_attr(o, "restaurant") or "", i)
        out.append(sorted(run, key=lambda o: (first_seen[_attr(o, "restaurant") or ""], _pickup_dt(o) or _SENTINEL)))
    return out


def plan_drop_rank(plan_doc) -> dict:
    """Względna kolejność DOSTAW z planu Ziomka (courier_plans.json stops)."""
    rank = {}
    di = 0
    if isinstance(plan_doc, dict):
        for s in (plan_doc.get("stops") or []):
            if not isinstance(s, dict):
                continue
            oid = str(s.get("order_id"))
            typ = "pickup" if s.get("type") == "pickup" else "dropoff"
            if typ == "dropoff" and oid not in rank:
                rank[oid] = di
                di += 1
    return rank


def order_podjazdy(bag, plan_doc=None) -> list[tuple[str, list[str]]]:
    """JEDYNE źródło kolejności. Zwraca listę stopów [(typ, [order_ids]), ...]
    gdzie typ ∈ {'pickup','dropoff'} a order_ids to zgrupowane zlecenia
    (odbiory tej samej restauracji w jednym podjeździe = jeden stop).

    bag: lista obiektów/dict-ów z polami: order_id, status, restaurant,
         czas_kuriera_warsaw. plan_doc: dict planu Ziomka (opcjonalny).
    """
    if not bag:
        return []
    rank = plan_drop_rank(plan_doc)

    def _drop_key(o):
        oid = str(_attr(o, "order_id"))
        return (rank.get(oid, _BIG), _attr(o, "czas_kuriera_warsaw") or "~")

    carried = sorted((o for o in bag if _attr(o, "status") == "picked_up"), key=_drop_key)
    to_pick = [o for o in bag if _attr(o, "status") != "picked_up"]

    order: list[tuple[str, list[str]]] = [("dropoff", [str(_attr(o, "order_id"))]) for o in carried]
    for run in pickup_runs(to_pick):
        i = 0
        while i < len(run):
            rest = _attr(run[i], "restaurant")
            grp = [str(_attr(run[i], "order_id"))]
            i += 1
            while i < len(run) and _attr(run[i], "restaurant") == rest:
                grp.append(str(_attr(run[i], "order_id")))
                i += 1
            order.append(("pickup", grp))
        drops = sorted(run, key=_drop_key)
        order += [("dropoff", [str(_attr(o, "order_id"))]) for o in drops]
    return order
