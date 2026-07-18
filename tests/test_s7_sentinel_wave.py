"""Fala #7 sentinel-as-data (2026-07-18, GO Adriana) — strażniki.

11 rozsianych użyć sentinela pozycji jako DANYCH skolapsowane do nazwanych
kanałów-obron: `courier_resolver._synthetic_pos_fallback` (5 miejsc floty),
`chain_eta._center_pos_fallback` (2), `dispatch_pipeline._osrm_guard_sentinel_coords`
(2 producentów backstopu OSRM) + rewrite gałęzi -1000 bez tworzenia sentinela.
Parytet tożsamościowy (wartości/labele/logi bez zmian). Testy pinują:
  1. ORACLE #7 == 0 w żywym silniku (trwały anty-regres entropii),
  2. parytet wartości helperów,
  3. resolver: pos+pos_source ZAWSZE parą (+shift_start_min),
  4. parytet gałęzi -1000 na wszystkich klasach wejść.
"""
import types

from dispatch_v2 import chain_eta
from dispatch_v2 import courier_resolver as CR
from dispatch_v2 import dispatch_pipeline as DP


def test_entropy7_oracle_zero_in_live_engine():
    """Trwały strażnik fali: oracle #7 (AST) = 0 poison w żywym silniku."""
    from dispatch_v2.tools.entropy_dashboard import live_engine_py, sentinel_poison
    poison, _instr = sentinel_poison(live_engine_py())
    assert poison == [], f"sentinel-as-data wrócił do silnika: {poison}"


def test_helpers_value_parity():
    assert chain_eta._center_pos_fallback() == chain_eta.BIALYSTOK_CENTER
    assert DP._osrm_guard_sentinel_coords() == (0.0, 0.0)
    assert DP._osrm_guard_sentinel_coords(None) == (0.0, 0.0)
    assert DP._osrm_guard_sentinel_coords(()) == (0.0, 0.0)
    assert DP._osrm_guard_sentinel_coords([53.1, 23.2]) == (53.1, 23.2)
    assert DP._osrm_guard_sentinel_coords((53.1, 23.2)) == (53.1, 23.2)


def test_resolver_synthetic_pos_always_pairs_source():
    cs = types.SimpleNamespace(pos=None, pos_source=None)
    CR._synthetic_pos_fallback(cs, "no_gps")
    assert cs.pos == CR.BIALYSTOK_CENTER and cs.pos_source == "no_gps"
    assert not hasattr(cs, "shift_start_min") or cs.shift_start_min is None
    cs2 = types.SimpleNamespace(pos=None, pos_source=None, shift_start_min=None)
    CR._synthetic_pos_fallback(cs2, "pre_shift", shift_start_min=12.0)
    assert (cs2.pos, cs2.pos_source, cs2.shift_start_min) == (
        CR.BIALYSTOK_CENTER, "pre_shift", 12.0)


def _score(order_event, pos=(53.14, 23.17)):
    cs = types.SimpleNamespace(pos=pos, tier_bag="std")
    return DP._v328_simple_heuristic_score("1", cs, order_event)


def test_minus1000_branch_parity_all_input_classes():
    """Parytet po rewrite bez sentinela: None/brak/()/(0.0,y)/(None,y) → -1000
    (jak przed falą — (None,y) szło przez except na ten sam wynik); valid →
    liczbowy score ≠ -1000."""
    assert _score({}) == -1000.0
    assert _score({"pickup_coords": None}) == -1000.0
    assert _score({"pickup_coords": ()}) == -1000.0
    assert _score({"pickup_coords": (0.0, 23.1)}) == -1000.0
    assert _score({"pickup_coords": (None, 23.1)}) == -1000.0
    ok = _score({"pickup_coords": (53.13, 23.16)})
    assert ok != -1000.0 and isinstance(ok, float)
    assert _score({"pickup_coords": (53.13, 23.16)}, pos=None) == -1000.0
