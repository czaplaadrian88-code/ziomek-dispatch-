"""JEDNA metryka świeżości niesionego jedzenia (handoff/carry) — WB2, CZASY 492.

Wymóg 13.2 p.3 diagnozy (`/root/handover/CZASY_INCYDENT_492_DIAGNOZA_2026-07-27.md`,
zbieżny werdykt 3 recenzentów): w jednym punkcie decyzyjnym żyły CZTERY polityki
świeżości liczone na RÓŻNYCH zegarach:

  * `CARRIED_FIRST_RELAX_SOFT_MAX_MIN` = 20 (relax L4),
  * `carry_cap = max(35.0, bcarry)` w `_lex_committed_window_reorder` (L5) —
    liczone na czasie PRZYJAZDU (przed dwell),
  * `O2_CAPZ_Z_MIN` = 20 w `route_simulator_v2._capz_reseq_plan` —
    liczone na `predicted_delivered_at`, czyli PO dwellu,
  * `BAG_TIME_HARD_MAX_MIN` = 35 (R6 / pin).

Rozjazd „przed dwell" vs „po dwell" to nie kosmetyka: dropoff dwell wynosi
domyślnie 3,5 min, czyli DOKŁADNIE tyle, ile wynosi cały budżet tolerancji
delty (D2: 3 min). Dwie warstwy porównujące „to samo" na zegarach różniących
się o więcej niż ich własna tolerancja nie są dwiema tolerancjami — to jeden
błąd rachunkowy w dwóch egzemplarzach.

Ten moduł jest KANONICZNYM właścicielem definicji. Nie ustanawia polityki:
progi zostają tam, gdzie były (Opcja 3 cap-Z = Z 20, G2 = 35/40) — moduł
odpowiada wyłącznie za to, CO się mierzy i JAK się to agreguje.

Definicja (za werdyktem Sola RUN3-b, sekcja 3 G2):

    handoff_at_i = arrival_at_i + dwell_dropoff_i      # moment przekazania
    carry_i      = handoff_at_i - possession_at_i      # wiek jedzenia u kuriera

`possession_at` to fizyczne wejście jedzenia do torby (`picked_up_at`, a dla
zleceń jeszcze nieodebranych kotwica ready). Dla TEGO SAMEGO zlecenia kotwica
jest stała między permutacjami, więc:

    carry_i(kandydat) - carry_i(baseline) == handoff_i(kandydat) - handoff_i(baseline)

Stąd względny guard świeżości i guard opóźnienia dostawy to MATEMATYCZNIE ten
sam predykat (Sol RUN3-b: „nie implementować dwóch niezależnych checkerów").
`delta_min()` niżej jest tym jednym predykatem — wołają go G1 i G2-delta.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Any, Callable, Dict, Iterable, Optional
from zoneinfo import ZoneInfo

#: Handoff = przyjazd + dwell dostawy. Ratchet: test WB2 czerwienieje, jeśli
#: którakolwiek warstwa wróci do liczenia świeżości na czasie przyjazdu.
HANDOFF_INCLUDES_DROPOFF_DWELL = True
CARRY_EVAL_SCHEMA = "carry_eval.v1"
THERMAL_SCOPE_SCHEMA = "carry_thermal_scope.v1"
THERMAL_EXEMPT_REASONS = frozenset({
    "paczka_r6_thermal_exempt",
    "paczki_only_flex",
})
HARD35_LE35 = "LE35"
HARD35_OVER35 = "OVER35"
HARD35_EXEMPT = "EXEMPT"
HARD35_UNKNOWN = "UNKNOWN"
# Jawna allowlista dowodów fizycznego possession. Sam niepusty opis źródła
# nie jest provenance: panel/click/plan pozostają proxy nawet wtedy, gdy niosą
# poprawnie sformatowany timestamp.
BOUND_POSSESSION_SOURCES = frozenset({
    "gps_geofence",
    "gps_bag_sensor",
    "physical_handoff_event",
    "restaurant_handoff_event",
})
BOUND_EVENT_GATE_STATUS = "BOUND"
BOUND_POSSESSION_CONTRACT_VERSIONS = frozenset({
    "physical_possession.v1",
})


def hard35_evaluation(
    carry_eval: Any,
) -> tuple[str, Optional[float]]:
    """Kanoniczna walidacja i interpretacja carry dla wszystkich konsumentów.

    Funkcja nie wylicza carry ponownie. Waliduje jedynie zapis writera
    ``feasibility_v2`` i zwraca wspólny stan dla S2, Alarmu oraz HARD35.
    Brak starego ``thermal_scope`` zachowuje kompatybilność rekordów sprzed
    migracji; obecny writer zawsze publikuje jawny APPLICABLE/EXEMPT.
    """
    if (
        not isinstance(carry_eval, Mapping)
        or carry_eval.get("schema") != CARRY_EVAL_SCHEMA
    ):
        return HARD35_UNKNOWN, None

    scope = carry_eval.get("thermal_scope")
    if scope is not None:
        if (
            not isinstance(scope, Mapping)
            or scope.get("schema") != THERMAL_SCOPE_SCHEMA
            or scope.get("status") not in {"APPLICABLE", "EXEMPT"}
            or not isinstance(scope.get("order_count"), int)
            or isinstance(scope.get("order_count"), bool)
            or scope.get("order_count") < 1
        ):
            return HARD35_UNKNOWN, None
        if scope.get("status") == "EXEMPT":
            if scope.get("reason") not in THERMAL_EXEMPT_REASONS:
                return HARD35_UNKNOWN, None
            return HARD35_EXEMPT, None
        if scope.get("reason") is not None:
            return HARD35_UNKNOWN, None

    if carry_eval.get("status") != "EVALUATED":
        return HARD35_UNKNOWN, None
    rows = carry_eval.get("orders")
    if not isinstance(rows, list) or not rows:
        return HARD35_UNKNOWN, None
    values = []
    for row in rows:
        if not isinstance(row, Mapping):
            return HARD35_UNKNOWN, None
        raw = row.get("carry_min")
        if (
            not isinstance(raw, (int, float))
            or isinstance(raw, bool)
            or not math.isfinite(float(raw))
            or float(raw) < 0.0
        ):
            return HARD35_UNKNOWN, None
        value = float(raw)
        if (
            row.get("le_35") is not (value <= 35.0)
            or row.get("le_40") is not (value <= 40.0)
        ):
            return HARD35_UNKNOWN, None
        values.append(value)
    if (
        carry_eval.get("evaluated_count") != len(values)
        or carry_eval.get("unknown_count") != 0
        or carry_eval.get("invalid_count") != 0
        or carry_eval.get("all_le_35") is not all(v <= 35.0 for v in values)
        or carry_eval.get("all_le_40") is not all(v <= 40.0 for v in values)
    ):
        return HARD35_UNKNOWN, None
    raw_max = carry_eval.get("max_carry_min")
    if (
        not isinstance(raw_max, (int, float))
        or isinstance(raw_max, bool)
        or not math.isfinite(float(raw_max))
        or float(raw_max) < 0.0
        or not math.isclose(
            float(raw_max),
            max(values),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    ):
        return HARD35_UNKNOWN, None
    maximum = float(raw_max)
    return (
        HARD35_LE35 if maximum <= 35.0 else HARD35_OVER35,
        maximum,
    )


def hard35_status(carry_eval: Any) -> str:
    """Status-only adapter wspólnej, pełnej walidacji kontraktu."""
    return hard35_evaluation(carry_eval)[0]


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def handoff_min(arrival_min: Optional[float],
                dwell_min: Optional[float]) -> Optional[float]:
    """Moment PRZEKAZANIA paczki klientowi (OD-01: arrival ≠ handoff).

    Oba argumenty w tej samej skali (minuty względem wspólnej kotwicy albo
    minuty bezwzględne). `None` propaguje — brak czasu oznacza kandydata
    nieocenialnego, nigdy kandydata domyślnie dobrego.
    """
    if arrival_min is None:
        return None
    return float(arrival_min) + float(dwell_min or 0.0)


def carry_min(handoff: Optional[float],
              possession: Optional[float]) -> Optional[float]:
    """Wiek jedzenia w torbie w chwili przekazania = handoff − possession."""
    if handoff is None or possession is None:
        return None
    return float(handoff) - float(possession)


def max_carry_min(carry_by_order: Dict[str, Optional[float]]) -> float:
    """Najstarsza NIESIONA sztuka. Pusty zbiór = 0.0 (parytet z cap-Z).

    Jedyny agregator „max wieku niesionego" w repo — cap-Z reseq i G2 wołają
    ten sam kod, więc nie mogą się rozjechać po zaokrągleniu ani po tym, czy
    `None` liczy się jako zero.
    """
    vals = [float(v) for v in carry_by_order.values() if v is not None]
    return max(vals) if vals else 0.0


def delta_min(baseline: Optional[float],
              candidate: Optional[float]) -> Optional[float]:
    """Pogorszenie kandydata vs baseline (dodatnie = gorzej).

    JEDEN predykat delty dla G1 (opóźnienie dostawy) i G2 (świeżość) — patrz
    dowód równoważności w docstringu modułu. Porównanie na SUROWYCH minutach,
    bez `round()`: zaokrąglenie prezentacyjne w progu decyzyjnym potrafi
    przepuścić kandydata dokładnie na granicy tolerancji.
    """
    if baseline is None or candidate is None:
        return None
    return float(candidate) - float(baseline)


def worst_delta(baseline_by_order: Dict[str, Optional[float]],
                candidate_by_order: Dict[str, Optional[float]],
                order_ids: Iterable[str]):
    """Największa delta i jej zlecenie: `(oid, delta)` albo `(None, None)`.

    Brak którejkolwiek wartości dla rozważanego zlecenia zwraca
    `(oid, None)` — sygnał „nieocenialny", który wywołujący MUSI potraktować
    jako odrzucenie kandydata (fail-closed), nie jako brak pogorszenia.
    """
    worst_oid = None
    worst = None
    for oid in order_ids:
        d = delta_min(baseline_by_order.get(oid), candidate_by_order.get(oid))
        if d is None:
            return oid, None
        if worst is None or d > worst:
            worst_oid, worst = oid, d
    return worst_oid, worst


def _as_utc(
    value: Any,
    *,
    naive_timezone=timezone.utc,
) -> Optional[datetime]:
    """Datetime/ISO -> aware UTC. Brak lub zły format pozostaje niewiedzą."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=naive_timezone)
    return parsed.astimezone(timezone.utc)


def _possession(order: Any, plan: Any) -> tuple[Optional[datetime], str, str]:
    """Jedyny resolver possession dla carry v2.

    `bound` wymaga jawnego GPS-geofence/fizycznego eventu z pełnym bindingiem.
    Niezwiązany ``physical_possession_at`` nie może wygrać z poprawnym klikowym
    fallbackiem. Klik `picked_up_at` jest jawnie nazwany ``proxy_fallback``.
    Przed fizycznym odbiorem termika biegnie od gotowości jedzenia; projektowany
    pickup jest dopiero awaryjnym proxy, gdy ready anchor nie istnieje.
    """
    physical = _as_utc(_field(order, "physical_possession_at"))
    physical_source = str(
        _field(order, "physical_possession_source", "") or ""
    ).strip()
    if physical is not None:
        event_gate_status = str(
            _field(order, "event_gate_status", "") or ""
        ).strip()
        contract_version = str(
            _field(order, "contract_version", "") or ""
        ).strip()
        bound = (
            physical_source in BOUND_POSSESSION_SOURCES
            and event_gate_status == BOUND_EVENT_GATE_STATUS
            and contract_version in BOUND_POSSESSION_CONTRACT_VERSIONS
        )
        if bound:
            return physical, "bound", physical_source

    # Panelowe ``picked_up_at`` bez offsetu jest czasem Europe/Warsaw.
    # Ta normalizacja żyje w kanonicznym resolverze, nie w konsumentach.
    picked = _as_utc(
        _field(order, "picked_up_at"),
        naive_timezone=ZoneInfo("Europe/Warsaw"),
    )
    if picked is not None:
        return picked, "proxy_fallback", "picked_up_at_click"

    # R6/HARD35: jedzenie jeszcze nieodebrane starzeje się od chwili gotowości,
    # nie od późniejszego miejsca pickup w planie.  Odwrócenie tych dwóch
    # fallbacków maskowało 50 min realnej termiki jako 25 min carry.
    ready = _as_utc(_field(order, "pickup_ready_at"))
    if ready is not None:
        return ready, "proxy", "pickup_ready_at"

    oid = str(_field(order, "order_id", "") or "")
    planned = _as_utc((_field(plan, "pickup_at", {}) or {}).get(oid))
    if planned is not None:
        return planned, "proxy", "planned_pickup_at"
    return None, "proxy", "missing_possession"


def evaluate_plan(
    plan: Any,
    orders: Iterable[Any],
    *,
    include_order: Optional[Callable[[Any], bool]] = None,
) -> Dict[str, Any]:
    """Wersjonowany, per-order pomiar ``handoff - possession``.

    `plan.predicted_delivered_at` jest kanonicznym handoffem symulatora: czas
    zapisuje się dopiero PO dwellu dostawy. Funkcja nie zna progów polityki
    poza dwoma wymaganymi klasyfikatorami 35/40 i niczego nie zapisuje.
    """
    rows = []
    unknown = 0
    invalid = 0
    evaluated = []
    drops = _field(plan, "predicted_delivered_at", {}) or {}
    for order in orders:
        if include_order is not None and not include_order(order):
            continue
        oid = str(_field(order, "order_id", "") or "")
        handoff = _as_utc(drops.get(oid))
        possession, binding, possession_source = _possession(order, plan)
        value = None
        reason = None
        if handoff is not None and possession is not None:
            raw_value = (handoff - possession).total_seconds() / 60.0
            if raw_value < 0.0:
                raw_value = None
                invalid += 1
                reason = "negative_carry"
            else:
                # ``carry_eval.v1`` jest kontraktem decyzyjnym, nie rendererem.
                # Wiersz i agregaty MUSZĄ nieść te same surowe minuty; inaczej
                # ``le_*`` liczone na raw oraz ``max_carry_min`` nie mogą zostać
                # zweryfikowane z zaokrąglonego wiersza. Zaokrąglenie należy
                # wyłącznie do konsumenta prezentacyjnego.
                value = raw_value
                evaluated.append(raw_value)
        else:
            raw_value = None
            unknown += 1
            reason = "missing_handoff_or_possession"
        row = {
            "order_id": oid,
            "carry_min": value,
            "le_35": None if raw_value is None else raw_value <= 35.0,
            "le_40": None if raw_value is None else raw_value <= 40.0,
            "source": binding,
            "possession_source": possession_source,
            "handoff_source": "predicted_delivery_with_dropoff_dwell",
        }
        if reason is not None:
            row["reason"] = reason
        rows.append(row)

    max_carry = max(evaluated) if evaluated else None
    status = (
        "EVALUATED"
        if rows and unknown == 0 and invalid == 0
        else "UNEVALUABLE"
    )
    return {
        "schema": CARRY_EVAL_SCHEMA,
        "status": status,
        "orders": rows,
        "evaluated_count": len(evaluated),
        "unknown_count": unknown,
        "invalid_count": invalid,
        "max_carry_min": max_carry,
        "all_le_35": (
            None if status != "EVALUATED" else all(v <= 35.0 for v in evaluated)
        ),
        "all_le_40": (
            None if status != "EVALUATED" else all(v <= 40.0 for v in evaluated)
        ),
    }
