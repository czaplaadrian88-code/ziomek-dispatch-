"""INV-R6-ANCHOR-CONSISTENCY (audyt 2026-06-24, spec odporności §6.A4): R6 (35-min termik)
liczone w DWÓCH miejscach — route_simulator `_compute_per_order_delivery_minutes` (POD/ETA)
i feasibility `check_feasibility_v2` (twarda bramka). MUSZĄ używać tej samej kotwicy
termicznej, inaczej bramka ≠ scoring. Fix: wspólny `r6_thermal_anchor`. Test = behawior
kotwicy + strażnik, że OBA miejsca go wołają (nie da się ich rozjechać edycją).
"""
import inspect
from datetime import datetime, timedelta, timezone

from dispatch_v2 import route_simulator_v2 as RS
from dispatch_v2 import feasibility_v2 as FE

NOW = datetime(2026, 6, 24, 13, 0, 0, tzinfo=timezone.utc)


class _O:
    def __init__(self, oid, status="assigned", picked_up_at=None, pickup_ready_at=None):
        self.order_id = oid
        self.status = status
        self.picked_up_at = picked_up_at
        self.pickup_ready_at = pickup_ready_at


def test_picked_uses_picked_up_at():
    pu = NOW - timedelta(minutes=8)
    o = _O("A", status="picked_up", picked_up_at=pu, pickup_ready_at=NOW - timedelta(minutes=20))
    anchor, src, is_picked = RS.r6_thermal_anchor(o, is_new=False, plan_pickup_at={}, now=NOW)
    assert is_picked and src == "picked_up_at" and anchor == pu


def test_not_picked_uses_pickup_ready_at():
    pra = NOW - timedelta(minutes=5)
    o = _O("B", status="assigned", pickup_ready_at=pra)
    anchor, src, is_picked = RS.r6_thermal_anchor(o, is_new=False, plan_pickup_at={}, now=NOW)
    assert (not is_picked) and src == "pickup_ready_at" and anchor == pra


def test_new_order_never_picked_even_if_flags_set():
    # new_order: is_new=True → is_picked False ZAWSZE (mimo picked_up_at) → pickup_ready_at
    pra = NOW - timedelta(minutes=3)
    o = _O("NEW", status="picked_up", picked_up_at=NOW, pickup_ready_at=pra)
    anchor, src, is_picked = RS.r6_thermal_anchor(o, is_new=True, plan_pickup_at={}, now=NOW)
    assert (not is_picked) and src == "pickup_ready_at" and anchor == pra


def test_fallback_tsp_then_now():
    tsp = NOW - timedelta(minutes=2)
    o = _O("C", status="assigned")               # brak pickup_ready_at
    anchor, src, _ = RS.r6_thermal_anchor(o, is_new=False, plan_pickup_at={"C": tsp}, now=NOW)
    assert src == "tsp_pickup_at" and anchor == tsp
    o2 = _O("D", status="assigned")
    anchor2, src2, _ = RS.r6_thermal_anchor(o2, is_new=False, plan_pickup_at={}, now=NOW)
    assert src2 == "now" and anchor2 == NOW


def test_both_sites_use_shared_anchor():
    # strażnik: oba miejsca liczące R6 wołają r6_thermal_anchor (nie inline → brak dryftu)
    rs_src = inspect.getsource(RS._compute_per_order_delivery_minutes)
    fe_src = inspect.getsource(FE.check_feasibility_v2)
    assert "r6_thermal_anchor(" in rs_src, "route_simulator R6 musi używać wspólnej kotwicy"
    assert "r6_thermal_anchor(" in fe_src, "feasibility R6 musi używać wspólnej kotwicy"
    # żadne z nich nie może mieć INLINE rozjazdu (powrót do osobnej selekcji picked/ready)
    for src, who in ((rs_src, "route_simulator"), (fe_src, "feasibility")):
        assert 'anchor = pra.astimezone' not in src, \
            f"{who}: wrócił inline anchor (rozjazd) — użyj r6_thermal_anchor"
