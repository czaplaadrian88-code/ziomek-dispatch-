"""Z-06 część kodowa (audyt 2026-06-10) — semantyka pos_from_store w gate'ach GPS.

Bug: rescue z last-known-pos store (TTL 25 min, FIX 2026-06-08) replay'uje
PIERWOTNY label pos_source ("gps") → przechodzi gate świeżego GPS w FAIL-12
("kurier FIZYCZNIE pracuje — świeży GPS TEN TICK"). Pozycja sprzed ≤25 min
to NIE jest dowód pracy w tym ticku.

Fix: check_feasibility_v2(..., pos_from_store) + warunek FAIL-12:
len(bag)>0 OR (pos_source=="gps" AND not pos_from_store). Flaga
ENABLE_FAIL12_STOREPOS_STRICT env default ON + hot-reload kill-switch.
Analogicznie C4 strict_gps w auto_proximity_classifier (przyszłe tiery).
"""
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2 import common as C
from dispatch_v2.feasibility_v2 import check_feasibility_v2
from dispatch_v2.route_simulator_v2 import OrderSim


_COURIER_POS = (53.13, 23.16)


def _mk_order(oid='1000', pickup_offset_min=10):
    pra = datetime.now(timezone.utc) + timedelta(minutes=pickup_offset_min)
    return OrderSim(
        order_id=oid,
        pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17),
        pickup_ready_at=pra,
    )


def _feas(bag_n=0, pos_source=None, pos_from_store=False):
    order = _mk_order()
    bag = [_mk_order(oid=f'b{i}', pickup_offset_min=5) for i in range(bag_n)]
    return check_feasibility_v2(
        courier_pos=_COURIER_POS, bag=bag, new_order=order,
        shift_end=None, shift_start=None,
        pickup_ready_at=order.pickup_ready_at,
        pos_source=pos_source,
        pos_from_store=pos_from_store,
    )


def _with_flags(**flags):
    """Context-less helper: ustaw flagi modułowe, zwróć poprzednie wartości."""
    prev = {k: getattr(C, k) for k in flags}
    for k, v in flags.items():
        setattr(C, k, v)
    return prev


def _restore(prev):
    for k, v in prev.items():
        setattr(C, k, v)


def test_storepos_blocks_failopen_strict_on():
    """KIERUNKOWY: gps ze store + pusty bag → NO_ACTIVE_SHIFT + fail12_storepos_blocked."""
    prev = _with_flags(
        ENABLE_V325_SCHEDULE_HARDENING=True,
        ENABLE_D2_STALE_SCHEDULE_SOFT=False,
        ENABLE_FAIL12_SCHEDULE_FAILOPEN=True,
        ENABLE_FAIL12_STOREPOS_STRICT=True,
    )
    try:
        verdict, reason, metrics, _ = _feas(bag_n=0, pos_source="gps", pos_from_store=True)
        assert verdict == "NO", f"expected NO, got {verdict} ({reason!r})"
        assert "v325_NO_ACTIVE_SHIFT" in reason
        assert metrics.get("fail12_storepos_blocked") is True
        assert "fail12_schedule_failopen" not in metrics
    finally:
        _restore(prev)


def test_live_gps_still_failopen():
    """Regresja FAIL-12: ŻYWY gps (pos_from_store=False) → fail-open jak dotąd."""
    prev = _with_flags(
        ENABLE_V325_SCHEDULE_HARDENING=True,
        ENABLE_D2_STALE_SCHEDULE_SOFT=False,
        ENABLE_FAIL12_SCHEDULE_FAILOPEN=True,
        ENABLE_FAIL12_STOREPOS_STRICT=True,
    )
    try:
        verdict, reason, metrics, _ = _feas(bag_n=0, pos_source="gps", pos_from_store=False)
        assert "v325_NO_ACTIVE_SHIFT" not in reason
        assert metrics.get("fail12_schedule_failopen") is True
        assert metrics.get("fail12_signal") == "gps"
        assert "fail12_storepos_blocked" not in metrics
    finally:
        _restore(prev)


def test_kill_switch_off_restores_legacy():
    """Kill-switch strict OFF: store-gps znów przechodzi fail-open (rollback path)."""
    prev = _with_flags(
        ENABLE_V325_SCHEDULE_HARDENING=True,
        ENABLE_D2_STALE_SCHEDULE_SOFT=False,
        ENABLE_FAIL12_SCHEDULE_FAILOPEN=True,
        ENABLE_FAIL12_STOREPOS_STRICT=False,
    )
    try:
        verdict, reason, metrics, _ = _feas(bag_n=0, pos_source="gps", pos_from_store=True)
        assert "v325_NO_ACTIVE_SHIFT" not in reason
        assert metrics.get("fail12_schedule_failopen") is True
    finally:
        _restore(prev)


def test_bag_signal_unaffected_by_storepos():
    """Bag>0 = twardy dowód pracy — fail-open via bag nawet gdy pos ze store."""
    prev = _with_flags(
        ENABLE_V325_SCHEDULE_HARDENING=True,
        ENABLE_D2_STALE_SCHEDULE_SOFT=False,
        ENABLE_FAIL12_SCHEDULE_FAILOPEN=True,
        ENABLE_FAIL12_STOREPOS_STRICT=True,
    )
    try:
        verdict, reason, metrics, _ = _feas(bag_n=1, pos_source="gps", pos_from_store=True)
        assert "v325_NO_ACTIVE_SHIFT" not in reason
        assert metrics.get("fail12_signal") == "bag"
    finally:
        _restore(prev)


def test_fail12_off_no_storepos_metric():
    """FAIL-12 OFF: hard reject bez fail12_storepos_blocked (metryka tylko gdy
    strict ZABLOKOWAŁ aktywny fail-open)."""
    prev = _with_flags(
        ENABLE_V325_SCHEDULE_HARDENING=True,
        ENABLE_D2_STALE_SCHEDULE_SOFT=False,
        ENABLE_FAIL12_SCHEDULE_FAILOPEN=False,
        ENABLE_FAIL12_STOREPOS_STRICT=True,
    )
    try:
        verdict, reason, metrics, _ = _feas(bag_n=0, pos_source="gps", pos_from_store=True)
        assert verdict == "NO"
        assert "fail12_storepos_blocked" not in metrics
    finally:
        _restore(prev)


def test_default_kwarg_backwards_compatible():
    """Wywołania bez pos_from_store (inne call-sites/testy) → default False = legacy."""
    prev = _with_flags(
        ENABLE_V325_SCHEDULE_HARDENING=True,
        ENABLE_D2_STALE_SCHEDULE_SOFT=False,
        ENABLE_FAIL12_SCHEDULE_FAILOPEN=True,
        ENABLE_FAIL12_STOREPOS_STRICT=True,
    )
    try:
        order = _mk_order()
        verdict, reason, metrics, _ = check_feasibility_v2(
            courier_pos=_COURIER_POS, bag=[], new_order=order,
            shift_end=None, pickup_ready_at=order.pickup_ready_at,
            pos_source="gps",
        )
        assert "v325_NO_ACTIVE_SHIFT" not in reason
    finally:
        _restore(prev)


def test_c4_strict_gps_rejects_store_pos():
    """C4 classifier (przyszłe strict_gps=True): best na store-gps → ACK, nie AUTO."""
    from dispatch_v2.auto_proximity_classifier import classify_auto_route, ROUTE_ACK, ROUTE_AUTO

    def _cand(cid, score, pos_from_store):
        return SimpleNamespace(
            courier_id=cid, score=score, feasibility_verdict="MAYBE",
            plan=SimpleNamespace(sla_violations=0),
            metrics={"pos_source": "gps", "pos_from_store": pos_from_store},
            best_effort=False,
        )

    flags = {
        "AUTO_PROXIMITY_ENABLED": False,
        "AUTO_PROXIMITY_SHADOW_ONLY": True,
        "AUTO_PROXIMITY_THRESHOLD": "T1",
        "AUTO_PROXIMITY_THRESHOLDS": {
            "T1": {"min_pool_feasible": 2, "min_score_margin": 15.0,
                   "tiers": ("gold", "std+"), "min_score": 50.0, "strict_gps": True},
        },
        "PARSER_DEGRADED": False,
        "ENABLE_KEBAB_KROL_DINNER_EXCLUSION": False,
    }
    fleet = {"c1": SimpleNamespace(
        tier_bag="gold",
        shift_end=datetime.now(timezone.utc) + timedelta(hours=2),
        shift_start=datetime.now(timezone.utc) - timedelta(hours=4),
        pos_source="gps",
    )}

    def _result(best, cands):
        return SimpleNamespace(
            verdict="PROPOSE", best=best, candidates=cands,
            pool_feasible_count=len(cands), pool_total_count=len(cands),
            pickup_ready_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )

    best_store = _cand("c1", 80.0, pos_from_store=True)
    route, reason = classify_auto_route(
        _result(best_store, [best_store, _cand("c2", 60.0, False)]), fleet, flags=flags)
    assert route == ROUTE_ACK
    assert reason == "C4_pos_source=gps_from_store", reason

    best_live = _cand("c1", 80.0, pos_from_store=False)
    route, reason = classify_auto_route(
        _result(best_live, [best_live, _cand("c2", 60.0, False)]), fleet, flags=flags)
    assert route == ROUTE_AUTO, reason
