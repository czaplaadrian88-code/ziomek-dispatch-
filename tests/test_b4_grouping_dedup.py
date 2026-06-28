"""B4 (audyt 2026-06-28) — greedy/bruteforce NIE dublują super-odbioru grupy.

Bug: bag_pickup_idxs_by_oid mapuje N oidow grupy na ten SAM super_pickup_idx ->
_bruteforce_plan (to_place.extend(.values())) i _greedy_plan (pending_pairs) wkladaly
super-odbior N razy -> podwojny dojazd do restauracji. Bije gdy greedy biegnie:
OR-Tools INFEASIBLE->greedy_fallback (~38/d) lub OR-Tools wylaczony globalnie. Fix = dedup.

Test: wymuszamy grupowanie (monkeypatch grouper) + OR-Tools off; grupowanie NIE moze byc
gorsze od pojedynczych odbiorow tych samych coords (po fixie ~rowne — 1 dwell mniej;
przed fixem grupowane = +caly dodatkowy dojazd do restauracji).
"""
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import route_simulator_v2 as R
from dispatch_v2 import common as C
from dispatch_v2 import same_restaurant_grouper as G
from dispatch_v2.route_simulator_v2 import OrderSim, simulate_bag_route_v2
from dispatch_v2.osrm_client import haversine as _hav

NOW = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)
REST = (53.30, 23.30)   # restauracja A+B (daleko od kuriera = dojazd kosztowny)


def _mock_table(points_a, points_b):
    # duration ~ dystans (te same coords -> 0); double-visit restauracji = realny koszt
    return [[{"duration_s": _hav(tuple(a), tuple(b)) * 120.0, "osrm_fallback": False}
             for b in points_b] for a in points_a]


def _ord(oid, pickup, drop):
    return OrderSim(order_id=oid, pickup_coords=pickup, delivery_coords=drop,
                    status="assigned", pickup_ready_at=NOW)


def _run(monkeypatch, force_group, extra=None):
    monkeypatch.setattr(R.osrm_client, "table", _mock_table)
    monkeypatch.setattr(C, "ENABLE_V326_OR_TOOLS_TSP", False, raising=False)
    monkeypatch.setattr(C, "ENABLE_V326_SAME_RESTAURANT_GROUPING", True, raising=False)
    A = _ord("A", REST, (53.31, 23.31))
    B = _ord("B", REST, (53.32, 23.32))
    bag = [A, B] + (extra or [])
    if force_group:
        grp = G.GroupedOrders(restaurant="REST", pickup_coords=REST, czas_kuriera=NOW, orders=[A, B])
        monkeypatch.setattr(G, "group_orders_by_restaurant", lambda *a, **k: [grp])
    else:
        monkeypatch.setattr(G, "group_orders_by_restaurant", lambda *a, **k: [])
    new = _ord("NEW", (53.105, 23.105), (53.13, 23.13))
    plan = simulate_bag_route_v2((53.10, 23.10), bag, new, now=NOW)
    return plan.total_duration_min, plan.strategy


# Sygnal causal: grupowanie (1 super-odbior) OSZCZEDZA 1 postoj pickup vs pojedyncze
# odbiory tych samych coords. Z bugiem super-odbior wstawiony 2x -> zbedny dwell KASUJE
# oszczednosc -> grouped == indiv (test pada). Z fixem grouped = indiv - ~1 dwell.
def test_bruteforce_grouped_saves_dwell(monkeypatch):
    grouped, strat_g = _run(monkeypatch, force_group=True)
    indiv, _ = _run(monkeypatch, force_group=False)
    assert grouped <= indiv - 0.5, \
        f"grupowanie ma oszczedzic 1 postoj (bug=double-pickup kasuje to); grouped={grouped} indiv={indiv} strat={strat_g}"


def test_greedy_grouped_saves_dwell(monkeypatch):
    C3 = _ord("C", (53.11, 23.11), (53.12, 23.12))   # +1 -> bag_after 4 > BRUTEFORCE_MAX(3) -> greedy
    grouped, strat_g = _run(monkeypatch, force_group=True, extra=[C3])
    indiv, _ = _run(monkeypatch, force_group=False, extra=[C3])
    assert "greedy" in (strat_g or ""), f"oczekiwany greedy path, got strategy={strat_g}"
    assert grouped <= indiv - 0.5, \
        f"greedy grupowanie ma oszczedzic postoj (bug dubluje super-odbior); grouped={grouped} indiv={indiv}"
