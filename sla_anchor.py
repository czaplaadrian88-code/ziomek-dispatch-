"""JEDNO ŹRÓDŁO 35-minutowego HARD-a Ziomka z JAWNĄ kotwicą (S1, 2026-07-02).

Kontekst (audyt 2.0 L09 / guard-teatr §4 / finding `feas-r6-sla-anchor-gap`):
35-minutowy HARD żyje w rozjeżdżających się bliźniakach z DOMYŚLANĄ kotwicą:
  - R6 carried-age (feasibility_v2 per-order) — kotwica READY (`r6_thermal_anchor`,
    od gotowości jedzenia: picked_up_at / pickup_ready_at / tsp pickup_at / now),
  - SLA dostawy (`route_simulator_v2._count_sla_violations` + feasibility SLA-loop)
    — kotwica NOW (tsp `pickup_at` / picked_up_at / now).
Kotwica READY jest już jednoźródłowa (`route_simulator_v2.r6_thermal_anchor`),
ale kotwica NOW była POWIELONA inline w DWÓCH bliźniakach (route_simulator +
feasibility) — źródło L-TEATR-1/2 (bramki maskowały się wzajemnie na wspólnym 35).

Ten moduł konsoliduje obliczenie naruszenia 35-min do JEDNEJ funkcji z anchorem
podawanym JAWNIE (`kind` ∈ {'now','ready'} + timestamp), używanej przez wszystkie
bliźniaki za flagą `ENABLE_SLA_ANCHOR_UNIFIED` (default OFF; OFF = inline bez zmian,
bajt-w-bajt). Próg NIE jest tu duplikowany — bierzemy go z ISTNIEJĄCYCH stałych
(`common.BAG_TIME_HARD_MAX_MIN` dla R6 termiki; param `sla_minutes` =
`feasibility_v2.DEFAULT_SLA_MINUTES` dla SLA). Zero nowej stałej 35.

Czyste funkcje, zero I/O — bezpieczne w bruteforce/greedy (per-plan, per-order).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional


def now_anchor(order, plan_pickup_at: Dict[str, datetime], now: datetime) -> datetime:
    """Kotwica NOW (SLA dostawy) — JEDNO źródło dla `_count_sla_violations` oraz
    feasibility SLA-loop. 1:1 z powieloną inline logiką obu bliźniaków:
      tsp `pickup_at[oid]` (odbiór zaplanowany w TYM planie)
      → `picked_up_at` (realny odbiór, znormalizowany do UTC)
      → `now` (last resort).
    """
    oid = getattr(order, "order_id", None)
    if oid in plan_pickup_at:
        return plan_pickup_at[oid]
    pu = getattr(order, "picked_up_at", None)
    if pu is not None:
        if pu.tzinfo is None:
            pu = pu.replace(tzinfo=timezone.utc)
        return pu.astimezone(timezone.utc)
    return now


def ready_anchor(order, is_new: bool, plan_pickup_at: Dict[str, datetime],
                 now: datetime):
    """Kotwica READY (R6 termiczna) — cienki re-export nad `r6_thermal_anchor`
    (już jednoźródłowa w route_simulator_v2). Zwraca (anchor_utc, src, is_picked)
    identycznie jak oryginał. Import leniwy = brak cyklu import route_simulator↔sla_anchor.
    """
    from dispatch_v2.route_simulator_v2 import r6_thermal_anchor
    return r6_thermal_anchor(order, is_new, plan_pickup_at, now)


def anchor(order, *, kind: str, now: datetime,
           plan_pickup_at: Optional[Dict[str, datetime]] = None,
           is_new: bool = False) -> datetime:
    """Jawny selektor kotwicy. `kind`:
      - 'now'   → kotwica SLA (dostawy),
      - 'ready' → kotwica R6 (termiczna, od gotowości).
    NIE zgaduje — wołający deklaruje kotwicę. Zwraca tylko timestamp (dla 'ready'
    pełną trójkę daje `ready_anchor`)."""
    pk = plan_pickup_at or {}
    if kind == "now":
        return now_anchor(order, pk, now)
    if kind == "ready":
        a, _src, _picked = ready_anchor(order, is_new, pk, now)
        return a
    raise ValueError(f"sla_anchor.anchor: nieznana kotwica kind={kind!r}")


def elapsed_min(pred_delivered_at: datetime, anchor_ts: datetime) -> float:
    """Minuty od JAWNEJ kotwicy do predykowanej dostawy. 1:1 z inline
    `(pred - pu).total_seconds()/60.0` w obu bliźniakach (bez re-normalizacji
    pred — wołający zawsze podaje aware timestamp z symulatora)."""
    return (pred_delivered_at - anchor_ts).total_seconds() / 60.0


def exceeds(pred_delivered_at: datetime, anchor_ts: datetime,
            threshold_min: float) -> bool:
    """True gdy elapsed od kotwicy > próg. Próg podawany JAWNIE (z istniejącej
    stałej — `BAG_TIME_HARD_MAX_MIN` dla R6 / `DEFAULT_SLA_MINUTES` dla SLA)."""
    return elapsed_min(pred_delivered_at, anchor_ts) > threshold_min


def hard_minutes() -> float:
    """Kanoniczny dial 35-min HARD termiki (R6). Czytany z `common.BAG_TIME_HARD_MAX_MIN`
    — bez lokalnej duplikacji stałej (INV-FEAS-R6-ONE-SOURCE)."""
    from dispatch_v2 import common as _C
    return float(getattr(_C, "BAG_TIME_HARD_MAX_MIN", 35.0))
