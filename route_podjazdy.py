"""ALIAS wsteczny → `dispatch_v2.route_order` (JEDNO ŹRÓDŁO kolejności trasy).

Sprint 30 (2026-07-07): reguła kolejności PODJAZDÓW została PROMOWANA do
`route_order.py` (dom kanoniczny). Ten moduł re-eksportuje z niego, żeby wszyscy
dotychczasowi importerzy `route_podjazdy` (apka `courier_orders`, golden
`test_route_order_golden`, narzędzia parytetu `route_order_{golden_corpus_gen,
live_parity_check}`, monitor panelu) działali BEZ ZMIAN. Zero drugiej kopii —
`route_order` zawiera implementację, tu tylko nazwy.

Bajt-identyczność projekcji vs poprzednia wersja (HEAD sprzed promocji) dowiedziona
na korpusie golden + żywych workach: `eod_drafts/2026-07-07/S30A_routeorder_0diff.md`.
Historyczna dokumentacja modułu (reguła, trust_canon, lustro konsoli) = docstring
`route_order.py`.
"""
from __future__ import annotations

from dispatch_v2.route_order import (  # noqa: F401
    WARSAW,
    PICKUP_MERGE_MIN,
    _SENTINEL,
    _BIG,
    _iso,
    _attr,
    _pickup_dt,
    _plan_pickup_clusters,
    pickup_runs,
    plan_drop_rank,
    _canon_order_from_plan,
    order_podjazdy,
    order_route,
    repair_dropoffs_after_pickups,
    build_stop_sequence,
)

__all__ = [
    "WARSAW", "PICKUP_MERGE_MIN",
    "_plan_pickup_clusters", "pickup_runs", "plan_drop_rank",
    "_canon_order_from_plan", "order_podjazdy", "order_route",
    "repair_dropoffs_after_pickups", "build_stop_sequence",
    "_iso", "_attr", "_pickup_dt",
]
