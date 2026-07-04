"""Claim ledger — JEDNO źródło wirtualnej alokacji zlecenia do worka kuriera (L6.C3, 2026-07-04).

Wyekstrahowane z `tools/pending_global_resweep._tentative_assign` (R2 ROOT-8: „nowe zlecenie
i przerzut dzielą JEDNĄ de-konflikcję globalną — TA SAMA global_allocate, wspólny import,
nie 2. kopia"). Konsumenci:
- `tools/pending_global_resweep.global_allocate` (resweep wiszących, shadow + overlay konsoli),
- pośrednio `tools/reassignment_global_select` (de-pile przerzutu; przez global_allocate),
- `shadow_dispatcher._tick` za flagą `ENABLE_ENGINE_CLAIM_LEDGER` (INV-LAYER-4: kolejne eventy
  TEGO SAMEGO ticku widzą claim zwycięzcy poprzedniego, zamiast oceniać niemutowaną flotę —
  korzeń pile-onu: jeden kurier proponowany 127×/32 zlecenia, g_maxpile=7).

Kierunek importu: silnik ← tools (nigdy odwrotnie). Moduł jest LIŚCIEM (zero importów
silnika) — bezpieczny dla każdego procesu.
"""
from __future__ import annotations

import copy as _copy
from typing import Any, Dict


def bag_entry_from_order(rec: dict) -> dict:
    """Wirtualny wpis do worka kuriera (kopia rekordu zlecenia, status=assigned)."""
    e = dict(rec)
    e["status"] = "assigned"
    e["commitment_level"] = "assigned"
    return e


def tentative_assign(fleet: Dict[str, Any], cid: str, order_rec: dict) -> Dict[str, Any]:
    """Płytka kopia floty z `order_rec` DOklejonym do worka kuriera `cid`
    (kontrfaktyk „gdyby ten kurier dostał to zlecenie"). NIE mutuje wejścia."""
    out = dict(fleet)
    cs = out.get(cid)
    if cs is None:
        return out
    cs2 = _copy.copy(cs)
    cs2.bag = list(cs.bag or []) + [bag_entry_from_order(order_rec)]
    out[cid] = cs2
    return out
