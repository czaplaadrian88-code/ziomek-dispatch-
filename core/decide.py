"""core.decide — fasada decyzji dispatchu: decide(world, order) (K09, ADR wariant B).

JEDNO wejście do oceny zlecenia. K09 = czysta delegacja 1:1 do publicznego
`dispatch_pipeline.assess_order` (wrapper z buforem efektów K08 + observability)
— fasada NICZEGO nie dodaje ani nie zmienia; bajt-parytet decyzji gwarantowany
konstrukcyjnie (te same argumenty, ta sama funkcja). Kolejne kroki przenoszą
warstwy _assess_order_impl do core/{gates,candidates,selection}.py — call-site'y
już wtedy nie drgną, bo wołają fasadę.

Uwaga testowalność: wywołanie przez atrybut modułu (`_dp.assess_order`), NIE
import symbolu — monkeypatch `dispatch_pipeline.assess_order` w testach/toolach
obowiązuje też przez fasadę.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from dispatch_v2 import dispatch_pipeline as _dp
from dispatch_v2.core.world_state import WorldState

if TYPE_CHECKING:  # pragma: no cover
    from dispatch_v2.dispatch_pipeline import PipelineResult


def decide(world: WorldState, order_event: dict, *, _bypass_early_bird: bool = False) -> "PipelineResult":
    """Oceń zlecenie w świecie `world` — delegacja 1:1 do assess_order.

    `_bypass_early_bird` = tryb pytania o TERAZ (resweep/kontrfaktyki), nie stan
    świata — dlatego kwarg fasady, nie pole WorldState (parytet z assess_order).
    """
    return _dp.assess_order(
        order_event,
        world.fleet_snapshot,
        world.restaurant_meta,
        world.now,
        pending_queue=world.pending_queue,
        demand_context=world.demand_context,
        _bypass_early_bird=_bypass_early_bird,
    )
