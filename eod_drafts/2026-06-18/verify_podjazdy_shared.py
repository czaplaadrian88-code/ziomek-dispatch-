#!/usr/bin/env python3
"""Dowód: route_podjazdy.order_podjazdy == panel _build_route (kolejność stopów)
w KONFIGURACJI PRODUKCYJNEJ (plan-aware bundling). Uruchamiać venv-em panelu
(ma deps fleet_state). Porównuje na przypadkach pokrywających: grupowanie po
restauracji, podział kursów (gap>10min), carried-first, rangę dostaw z planu Ziomka.

⚠ Parytet flag (lekcja 2026-06-24, [[console-app-time-route-divergence-2026-06-23]]):
oba renderery MUSZĄ iść tą samą ścieżką co produkcja, inaczej dowód testuje
nieistniejącą konfigurację. Produkcja: panel `PANEL_FLAG_TRUST_CANON_ORDER=1` +
`PANEL_FLAG_PLAN_AWARE_PODJAZDY=1` (nadajesz-panel.service); apka woła
`order_podjazdy(plan_aware=True)` (courier-api `ENABLE_PLAN_AWARE_PODJAZDY=1`).
Ustawiamy env PRZED importem fleet_state (flag() czyta os.getenv na żywo)."""
import os
# mirror produkcji ZANIM zaimportujemy fleet_state (flag() czyta env w czasie wywołania)
os.environ.setdefault("PANEL_FLAG_TRUST_CANON_ORDER", "1")
os.environ.setdefault("PANEL_FLAG_PLAN_AWARE_PODJAZDY", "1")
import sys
sys.path.insert(0, "/root/.openclaw/workspace/nadajesz_clone/panel/backend")
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from app.integrations.ziomek.fleet_state import _build_route, BagOrder  # noqa
from dispatch_v2 import route_podjazdy as RP  # noqa


def panel_order(bag, plan_doc):
    stops, _src = _build_route(plan_doc, bag, None, {b.order_id: {} for b in bag})
    # PlanStop → [(type, order_ids)]
    return [(s.type, list(s.order_ids)) for s in stops]


def bo(oid, rest, ck=None, status="assigned"):
    return BagOrder(order_id=oid, status=status, restaurant=rest, delivery_address="x",
                    czas_kuriera_warsaw=ck, pickup_coords=[53.13, 23.15], delivery_coords=[53.12, 23.16])


CASES = {
    "same-restaurant grouping": ([bo("A", "Sushi", "2026-06-18T15:20:00+02:00"),
                                  bo("B", "Sushi", "2026-06-18T15:21:00+02:00"),
                                  bo("C", "Kebab", "2026-06-18T15:22:00+02:00")], None),
    "run-split by 10min gap": ([bo("A", "R1", "2026-06-18T18:47:00+02:00"),
                                bo("B", "R1", "2026-06-18T19:20:00+02:00")], None),  # 33min → 2 kursy
    "carried-first": ([bo("A", "R1", "2026-06-18T15:20:00+02:00", status="picked_up"),
                       bo("B", "R2", "2026-06-18T15:21:00+02:00")], None),
    "plan_drop_rank order": ([bo("A", "R1", "2026-06-18T15:20:00+02:00"),
                              bo("B", "R2", "2026-06-18T15:21:00+02:00")],
                             {"stops": [{"order_id": "A", "type": "pickup"}, {"order_id": "B", "type": "pickup"},
                                        {"order_id": "B", "type": "dropoff"}, {"order_id": "A", "type": "dropoff"}]}),
    "mixed 4": ([bo("A", "Sushi", "2026-06-18T15:20:00+02:00"),
                 bo("B", "Sushi", "2026-06-18T15:20:30+02:00"),
                 bo("C", "Toriko", "2026-06-18T15:22:00+02:00"),
                 bo("D", "Kebab", "2026-06-18T15:50:00+02:00")], None),  # D = osobny kurs (>10min)
    "no czas (single run)": ([bo("A", "R1"), bo("B", "R2"), bo("C", "R1")], None),
}

ok = 0
for name, (bag, plan) in CASES.items():
    p = panel_order(bag, plan)
    s = RP.order_podjazdy(bag, plan, plan_aware=True)   # mirror produkcji (courier-api)
    same = p == s
    ok += same
    print(f"[{'OK ' if same else 'DIFF'}] {name}")
    if not same:
        print(f"    panel : {p}")
        print(f"    shared: {s}")
print(f"\n{ok}/{len(CASES)} przypadków identycznych (shared == konsola)")
sys.exit(0 if ok == len(CASES) else 1)
