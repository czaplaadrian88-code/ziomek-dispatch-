"""core.world_state — WorldState: komplet wejść jednej decyzji dispatchu (K09, ADR wariant B).

WorldState grupuje wejścia, które dziś są już WSTRZYKIWANE do assess_order
argumentami (flota, meta restauracji, zegar, kolejka pending, kontekst popytu).
K09 = czysta delegacja 1:1 — żadnej nowej semantyki; pola odpowiadają dokładnie
parametrom `dispatch_pipeline.assess_order`. Kolejne kroki (K10-K13) będą
przez ten obiekt podawać także FlagSnapshot/TravelTimeProvider (dziś: mechanizmy
K05/K06 działają procesowo, nie przez pola — świadomie, zero zmiany zachowania).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class WorldState:
    """Świat widziany przez decyzję: dokładnie to, co dziś dostaje assess_order.

    fleet_snapshot — snapshot floty {cid: CourierState} (już wzbogacony, np.
        dispatchable_fleet(); WorldState go NIE buduje — buduje powłoka).
    restaurant_meta — meta restauracji (dict) albo None.
    now — zegar decyzji (aware UTC) albo None (impl bierze now w środku — legacy;
        nagrywalność wymaga jawnego now, patrz K06a).
    pending_queue / demand_context — opcjonalne wejścia C7/E2 (dziś nikt ich nie
        podaje na call-site'ach produkcyjnych; zachowane dla parytetu sygnatury).
    """

    fleet_snapshot: Dict[str, Any] = field(default_factory=dict)
    restaurant_meta: Optional[dict] = None
    now: Optional[datetime] = None
    pending_queue: Optional[list] = None
    demand_context: Optional[dict] = None
