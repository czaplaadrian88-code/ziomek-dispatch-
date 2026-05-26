"""BUG E hotfix (2026-05-26) — best_effort + >=1 order łamie R6 → verdict=KOORD.

Stricter superset OBJ F3: bez progu min-breach (ANY breach), mierzone per order
z plan.pickup_at/predicted_delivered_at (NIE objm_r6_breach_max_min). Diagnoza
26.05: 4 z 9 case'ów (D/E/F/G) odjeżdżały best_effort PROPOSE z carry 43-90 min
bo OBJ F3 próg 20 łapie tylko bag_time > 55.

Pattern = source-regression (jak test_obj_f3_best_effort_r6_koord): bramki
głęboko w assess_order — sprawdzamy obecność + pozycję + predykat + werdykt
w źródle, plus kontrakt flagi w common.
"""
import inspect

from dispatch_v2 import common, dispatch_pipeline


def test_buge_gate_comment_header_present():
    src = inspect.getsource(dispatch_pipeline)
    assert "BUG E hotfix (2026-05-26)" in src


def test_buge_flag_in_source():
    """Bramka czyta flagę ENABLE_BEST_EFFORT_R6_KOORD_REDIRECT."""
    src = inspect.getsource(dispatch_pipeline)
    assert "ENABLE_BEST_EFFORT_R6_KOORD_REDIRECT" in src


def test_buge_uses_plan_pickup_predicted_delivered():
    """Bag_time liczony z plan.pickup_at i predicted_delivered_at (per order)."""
    src = inspect.getsource(dispatch_pipeline)
    # Frament pokazujący że nowy check bierze plan i mapy pickup_at + delivered_at
    start = src.find("BUG E hotfix (2026-05-26): best_effort")
    assert start > 0
    section = src[start:start + 2200]
    assert "pickup_at" in section
    assert "predicted_delivered_at" in section
    assert "BAG_TIME_HARD_MAX_MIN" in section


def test_buge_emits_koord_verdict():
    """Bramka emituje verdict=KOORD z reason best_effort_r6_breach_v2."""
    src = inspect.getsource(dispatch_pipeline)
    start = src.find("BUG E hotfix (2026-05-26): best_effort")
    assert start > 0
    section = src[start:start + 2400]
    assert 'verdict="KOORD"' in section
    assert "best_effort_r6_breach_v2" in section


def test_buge_positioned_before_obj_f3():
    """Nowa bramka stricter — odpala PRZED OBJ F3 (luźniejszą)."""
    src = inspect.getsource(dispatch_pipeline)
    marker = src.find("best.best_effort = True")
    buge_gate = src.find("BUG E hotfix (2026-05-26): best_effort")
    objf3_gate = src.find("Sprint OBJ F3 / BUG-4 (2026-05-18): best_effort")
    assert marker > 0 and buge_gate > 0 and objf3_gate > 0
    assert marker < buge_gate < objf3_gate, (
        f"pozycja bramek błędna: marker={marker} buge={buge_gate} objf3={objf3_gate}")


def test_buge_surfaces_redirect_dict_for_telegram():
    """Wynik niesie dict best_effort_r6_redirect z breach_count + max + lista oid."""
    src = inspect.getsource(dispatch_pipeline)
    start = src.find("BUG E hotfix (2026-05-26): best_effort")
    section = src[start:start + 3200]
    assert "best_effort_r6_redirect" in section
    assert "breach_count" in section
    assert "max_bag_time_min" in section
    assert "orders_in_breach" in section


def test_buge_uses_breach_count_ge_one():
    """Trigger condition: >=1 order w breach, NIE wszyscy."""
    src = inspect.getsource(dispatch_pipeline)
    start = src.find("BUG E hotfix (2026-05-26): best_effort")
    section = src[start:start + 2400]
    # min jeden order musi przekraczać 35 min
    assert "_be_breach_orders" in section
    assert "len(_be_breach_orders) >= 1" in section


def test_buge_common_contract():
    """common: flaga default ON (env override possible)."""
    assert hasattr(common, "ENABLE_BEST_EFFORT_R6_KOORD_REDIRECT")
    # Default ON (env unset → "1") — bezpieczne, eskalacja do KOORD
    assert common.ENABLE_BEST_EFFORT_R6_KOORD_REDIRECT is True
