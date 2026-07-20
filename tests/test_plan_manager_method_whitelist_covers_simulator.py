"""Anty-dryf (20.07): whitelist optimization_method w plan_manager.save_plan MUSI pokrywać
wszystkie strategie emitowane przez route_simulator_v2 (bug: ortools odrzucany od V3.26 —
1701 cichych fail). Nowa strategia w symulatorze bez wpisu tutaj = czerwony test, nie cichy drop."""
import inspect, re
from dispatch_v2 import plan_manager, route_simulator_v2


def _whitelist():
    src = inspect.getsource(plan_manager)
    m = re.search(r'optimization_method"\] not in \{([^}]+)\}', src)
    assert m, "nie znaleziono whitelisty w plan_manager"
    return set(re.findall(r'"([a-z_0-9]+)"', m.group(1)))


def test_whitelist_covers_simulator_strategies():
    sim = set(re.findall(r'strategy\s*=\s*"([a-z_0-9]+)"', inspect.getsource(route_simulator_v2)))
    sim |= set(re.findall(r'"strategy":\s*"([a-z_0-9]+)"', inspect.getsource(route_simulator_v2)))
    missing = sim - _whitelist()
    assert not missing, f"strategie symulatora poza whitelistą save_plan: {sorted(missing)}"


def test_ortools_accepted():
    assert "ortools" in _whitelist()
