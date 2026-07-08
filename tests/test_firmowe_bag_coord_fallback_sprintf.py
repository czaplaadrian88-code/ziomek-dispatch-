"""Sprint F (2026-07-08) — źródło (0,0)/COORD_GUARD dla paczek firmowych.

Klasa błędu: bag-order FIRMOWY (aid∈FIRMOWE_KONTO_ADDRESS_IDS) persystuje
`pickup_coords=None` (świadomy reject→KOORD parsera uwag — pickup nadawcy w
uwagach nierozwiązywalny). Gdy taki order jest w worku jako `assigned` (jeszcze
nie `picked_up`), `_bag_dict_to_ordersim` próbuje runtime re-geokod
(`_repair_bag_coords`); gdy geokod padnie (sieć/TTL w peaku) → fallback CICHY
`(0.0, 0.0)` → `route_simulator` dokłada węzeł pickup (0,0) → `osrm_client.table`
→ COORD_GUARD (sentinel 9999 → holder cicho wykluczany, choroba L2.1).

Fix (Adrian 2026-07-08, opcja A): flaga `ENABLE_FIRMOWE_BAG_COORD_FALLBACK`
(default OFF = legacy bajt-w-bajt). ON → odbiór firmowy nierozwiązywalny dostaje
FIRMOWE_KONTO_FALLBACK_COORDS (centrala Nadajesz, w bbox) zamiast (0,0). Dotyczy
WYŁĄCZNIE odbioru FIRMOWEGO; delivery i nie-firmowe zostają legacy (guard
backstop). Guard OSRM nie znika — przestaje strzelać na tej klasie.

Testy behawioralne (C13): wołają realny `_bag_dict_to_ordersim` + realny
`simulate_bag_route_v2` z przechwyconym `osrm_client.table` → asercja
współrzędnych węzła ORAZ liczby trafień guardu (0,0). Mutacja capa (ON→(0,0))
zabiłaby asercję.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dispatch_v2 import common as C            # noqa: E402
from dispatch_v2 import dispatch_pipeline as DP  # noqa: E402
from dispatch_v2 import osrm_client            # noqa: E402
from dispatch_v2 import route_simulator_v2 as R  # noqa: E402

# Firmowy odbiór nierozwiązywalny: pickup_coords=None, delivery poprawne, aid=161,
# status=assigned + BRAK picked_up_at (route_simulator dokłada węzeł pickup).
_FIRMOWE_BASE = {
    "order_id": "F1",
    "status": "assigned",
    "picked_up_at": None,
    "pickup_coords": None,
    "delivery_coords": [53.129985, 23.147467],
    "restaurant": "Nadajesz.pl",
    "pickup_address": "Piasta 13",
    "delivery_address": "Kijowska 12 Białystok",
    "address_id": "161",
}
_NEW_ORDER = None  # zbudowany w fixture-lite niżej (valid coords)
_VALID_NEW_PICKUP = (53.1322335, 23.1653257)
_VALID_NEW_DROP = (53.1211175, 23.1307918)


def _mk_new():
    return R.OrderSim(
        order_id="NEW", pickup_coords=_VALID_NEW_PICKUP,
        delivery_coords=_VALID_NEW_DROP, picked_up_at=None,
        status="assigned", pickup_ready_at=None)


def _sim_capture_guard_hits(bag_sim):
    """Wołaj realny simulate_bag_route_v2 z przechwyconym table() — policz ile razy
    współrzędna spoza bbox (w tym (0,0)) trafia do OSRM (= trafienia COORD_GUARD)."""
    hits = []
    orig = osrm_client.table

    def _patched(origins, destinations):
        bad = [o for o in (origins or []) if not C.coords_in_bialystok_bbox(o)] + \
              [d for d in (destinations or []) if not C.coords_in_bialystok_bbox(d)]
        if bad:
            hits.append(tuple(bad[:2]))
        return orig(origins, destinations)

    osrm_client.table = _patched
    try:
        R.simulate_bag_route_v2((53.13, 23.16), bag_sim, _mk_new(),
                                now=datetime.now(timezone.utc), sla_minutes=35)
    finally:
        osrm_client.table = orig
    return hits


# ── rejestracja + default ─────────────────────────────────────────────────────

def test_flag_registered_and_off_by_default():
    assert C.ENABLE_FIRMOWE_BAG_COORD_FALLBACK is False
    assert "ENABLE_FIRMOWE_BAG_COORD_FALLBACK" in C.ETAP4_DECISION_FLAGS


# ── jednostkowo: _bag_dict_to_ordersim (repair wyłączony = symulacja padu geokodu) ─

def test_off_firmowe_repair_fail_emits_zero_zero(monkeypatch):
    """Flaga OFF + repair pad → legacy (0.0, 0.0) (bajt-w-bajt)."""
    monkeypatch.setattr(C, "ENABLE_FIRMOWE_BAG_COORD_FALLBACK", False)
    monkeypatch.setattr(C, "ENABLE_BAG_COORD_REPAIR", False)  # symuluj pad re-geokodu
    sim = DP._bag_dict_to_ordersim(dict(_FIRMOWE_BASE))
    assert tuple(sim.pickup_coords) == (0.0, 0.0)


def test_on_firmowe_repair_fail_uses_centrala(monkeypatch):
    """Flaga ON + repair pad + firmowe → FIRMOWE_KONTO_FALLBACK_COORDS (w bbox)."""
    monkeypatch.setattr(C, "ENABLE_FIRMOWE_BAG_COORD_FALLBACK", True)
    monkeypatch.setattr(C, "ENABLE_BAG_COORD_REPAIR", False)
    sim = DP._bag_dict_to_ordersim(dict(_FIRMOWE_BASE))
    assert tuple(sim.pickup_coords) == tuple(C.FIRMOWE_KONTO_FALLBACK_COORDS)
    assert C.coords_in_bialystok_bbox(sim.pickup_coords)


def test_on_non_firmowe_repair_fail_stays_zero(monkeypatch):
    """Flaga ON, ale NIE-firmowe (inny aid) → zostaje (0,0) legacy (guard backstop)."""
    monkeypatch.setattr(C, "ENABLE_FIRMOWE_BAG_COORD_FALLBACK", True)
    monkeypatch.setattr(C, "ENABLE_BAG_COORD_REPAIR", False)
    d = dict(_FIRMOWE_BASE, address_id="96", order_id="N1")
    sim = DP._bag_dict_to_ordersim(d)
    assert tuple(sim.pickup_coords) == (0.0, 0.0)


def test_on_firmowe_repair_success_unchanged(monkeypatch):
    """Flaga ON, ale repair się UDAJE → realny geokod (fallback NIE wchodzi)."""
    monkeypatch.setattr(C, "ENABLE_FIRMOWE_BAG_COORD_FALLBACK", True)
    monkeypatch.setattr(C, "ENABLE_BAG_COORD_REPAIR", True)
    sim = DP._bag_dict_to_ordersim(dict(_FIRMOWE_BASE))
    # repair geokoduje 'Nadajesz.pl'/'Piasta 13' → w bbox, ale NIE centrala-fallback
    assert C.coords_in_bialystok_bbox(sim.pickup_coords)
    assert tuple(sim.pickup_coords) != tuple(C.FIRMOWE_KONTO_FALLBACK_COORDS)


def test_delivery_fallback_stays_legacy_zero(monkeypatch):
    """Delivery firmowe nierozwiązywalne → (0,0) legacy (centrala jako DOSTAWA błędna)."""
    monkeypatch.setattr(C, "ENABLE_FIRMOWE_BAG_COORD_FALLBACK", True)
    monkeypatch.setattr(C, "ENABLE_BAG_COORD_REPAIR", False)
    d = dict(_FIRMOWE_BASE, delivery_coords=None, delivery_address=None,
             pickup_coords=[53.13, 23.16])  # pickup ok, delivery zły
    sim = DP._bag_dict_to_ordersim(d)
    assert tuple(sim.delivery_coords) == (0.0, 0.0)


# ── e2e: route_simulator → table() → COORD_GUARD (ON≠OFF na trafieniach) ──────

def test_e2e_off_fires_guard_on_eliminates(monkeypatch):
    """Dowód pozytywnego wpływu na ŹRÓDŁO: OFF → (0,0) trafia do table (guard hit);
    ON → centrala → 0 trafień. Ten sam realny silnik trasy w obu."""
    monkeypatch.setattr(C, "ENABLE_BAG_COORD_REPAIR", False)  # peak geokod-fail

    monkeypatch.setattr(C, "ENABLE_FIRMOWE_BAG_COORD_FALLBACK", False)
    sim_off = DP._bag_dict_to_ordersim(dict(_FIRMOWE_BASE))
    hits_off = _sim_capture_guard_hits([sim_off])

    monkeypatch.setattr(C, "ENABLE_FIRMOWE_BAG_COORD_FALLBACK", True)
    sim_on = DP._bag_dict_to_ordersim(dict(_FIRMOWE_BASE))
    hits_on = _sim_capture_guard_hits([sim_on])

    assert len(hits_off) >= 1, "OFF: (0,0) MUSI trafić do table (guard hit) — legacy"
    assert hits_on == [], "ON: firmowa centrala → ZERO trafień (0,0) w table"
