"""Sprint 2 Etap 2.1 tests — Kebab Król dinner carry-penalty exclusion.

Forensic Agent D (/tmp/kebab_krol_diagnostic.md):
  KK R6 breach 22.5% w dinner peak (17-21 Warsaw) vs 7-8% peer baseline.
  Root cause = carry/bag-stack penalty. Fix: conditional ALERT zamiast routingu
  AUTO. Default flag ON (low risk, KK-only).

Test matrix:
  - lunch (12 Warsaw) → routing normalny (np. AUTO przy spełnionych warunkach)
  - dinner (18 Warsaw) → ALERT 'kk_dinner_carry_risk_v2'
  - boundary 16:59 Warsaw → routing normalny
  - boundary 17:00 Warsaw → ALERT
  - boundary 20:59 Warsaw → ALERT
  - boundary 21:00 Warsaw → routing normalny
  - flag OFF → no-op nawet w dinner
  - inna restauracja w dinner → no-op
  - czasówka KK → ACK 'czasowka_60min' (Bartek wave-line judgment)
  - case-insensitive matching + Polish diacritics
"""
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2.auto_proximity_classifier import (
    classify_auto_route,
    ROUTE_AUTO,
    ROUTE_ACK,
    ROUTE_ALERT,
    KEBAB_KROL_NAME_SUBSTR,
    KEBAB_KROL_DINNER_START_HOUR_WARSAW,
    KEBAB_KROL_DINNER_END_HOUR_WARSAW,
)


_WARSAW_TZ = ZoneInfo("Europe/Warsaw")


# ---------------------------------------------------------------------------
# Helpers (minimal — KK test surface tylko)
# ---------------------------------------------------------------------------

def _warsaw_now_at(hour, minute=0):
    """Build a UTC datetime equivalent to Warsaw hour:minute today (DST-safe)."""
    today = datetime.now(_WARSAW_TZ).date()
    warsaw_dt = datetime(today.year, today.month, today.day, hour, minute, tzinfo=_WARSAW_TZ)
    return warsaw_dt.astimezone(timezone.utc)


def _make_candidate(courier_id="c1", score=80.0, verdict="MAYBE"):
    return SimpleNamespace(
        courier_id=courier_id,
        score=score,
        feasibility_verdict=verdict,
        plan=SimpleNamespace(sla_violations=0),
        metrics={},
        best_effort=False,
    )


def _make_result(pickup_ready_at=None):
    if pickup_ready_at is None:
        pickup_ready_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    return SimpleNamespace(
        verdict="PROPOSE",
        best=_make_candidate(),
        candidates=[
            _make_candidate("c1", 80.0),
            _make_candidate("c2", 60.0),
            _make_candidate("c3", 40.0),
        ],
        pool_feasible_count=3,
        pool_total_count=5,
        pickup_ready_at=pickup_ready_at,
    )


def _fleet_gold(best):
    return {
        best.courier_id: SimpleNamespace(
            tier_bag="gold",
            shift_end=datetime.now(timezone.utc) + timedelta(hours=2),
            shift_start=datetime.now(timezone.utc) - timedelta(hours=4),
            pos_source="gps",
        )
    }


_FLAGS_BASE = {
    "AUTO_PROXIMITY_ENABLED": True,
    "AUTO_PROXIMITY_SHADOW_ONLY": True,
    "AUTO_PROXIMITY_THRESHOLD": "T3",  # T3 = aggressive (allows AUTO routing)
    "PARSER_DEGRADED": False,
    "ENABLE_KEBAB_KROL_DINNER_EXCLUSION": True,
}


# ---------------------------------------------------------------------------
# Tests — 10 cases pokrywające pełen surface
# ---------------------------------------------------------------------------

def test_kk_lunch_routes_normally_not_alert():
    """Lunch (12 Warsaw) → routing standardowy (NIE alert KK)."""
    result = _make_result()
    fleet = _fleet_gold(result.best)
    order_event = {"restaurant": "Kebab Król - Sienkiewicza 73", "prep_minutes": 30}
    now = _warsaw_now_at(12, 30)
    route, reason = classify_auto_route(
        result, fleet_snapshot=fleet, flags=_FLAGS_BASE, order_event=order_event, now=now,
    )
    assert "kk_dinner_carry_risk_v2" not in reason, (
        f"expected KK lunch NOT to trigger exclusion, got route={route} reason={reason}"
    )


def test_kk_dinner_18_warsaw_triggers_alert():
    """Dinner (18 Warsaw) → ALERT 'kk_dinner_carry_risk_v2'."""
    result = _make_result()
    fleet = _fleet_gold(result.best)
    order_event = {"restaurant": "Kebab Król", "prep_minutes": 30}
    now = _warsaw_now_at(18, 30)
    route, reason = classify_auto_route(
        result, fleet_snapshot=fleet, flags=_FLAGS_BASE, order_event=order_event, now=now,
    )
    assert route == ROUTE_ALERT, f"expected ALERT, got {route} ({reason})"
    assert reason == "kk_dinner_carry_risk_v2", f"unexpected reason: {reason}"


def test_kk_boundary_1659_no_exclusion():
    """16:59 Warsaw → exclusion NIE fires (start = 17:00 inclusive)."""
    result = _make_result()
    fleet = _fleet_gold(result.best)
    order_event = {"restaurant": "kebab król podlasie", "prep_minutes": 30}
    now = _warsaw_now_at(16, 59)
    route, reason = classify_auto_route(
        result, fleet_snapshot=fleet, flags=_FLAGS_BASE, order_event=order_event, now=now,
    )
    assert "kk_dinner_carry_risk_v2" not in reason, (
        f"16:59 Warsaw should NOT trigger; route={route} reason={reason}"
    )


def test_kk_boundary_1700_triggers_alert():
    """17:00 Warsaw → ALERT (start = inclusive)."""
    result = _make_result()
    fleet = _fleet_gold(result.best)
    order_event = {"restaurant": "Kebab Król", "prep_minutes": 30}
    now = _warsaw_now_at(17, 0)
    route, reason = classify_auto_route(
        result, fleet_snapshot=fleet, flags=_FLAGS_BASE, order_event=order_event, now=now,
    )
    assert route == ROUTE_ALERT, f"17:00 Warsaw expected ALERT, got {route}"
    assert reason == "kk_dinner_carry_risk_v2"


def test_kk_boundary_2059_triggers_alert():
    """20:59 Warsaw → ALERT (end exclusive = 21:00)."""
    result = _make_result()
    fleet = _fleet_gold(result.best)
    order_event = {"restaurant": "Kebab Król", "prep_minutes": 30}
    now = _warsaw_now_at(20, 59)
    route, reason = classify_auto_route(
        result, fleet_snapshot=fleet, flags=_FLAGS_BASE, order_event=order_event, now=now,
    )
    assert route == ROUTE_ALERT, f"20:59 Warsaw expected ALERT, got {route}"
    assert reason == "kk_dinner_carry_risk_v2"


def test_kk_boundary_2100_no_exclusion():
    """21:00 Warsaw → exclusion NIE fires (end = 21:00 exclusive)."""
    result = _make_result()
    fleet = _fleet_gold(result.best)
    order_event = {"restaurant": "Kebab Król", "prep_minutes": 30}
    now = _warsaw_now_at(21, 0)
    route, reason = classify_auto_route(
        result, fleet_snapshot=fleet, flags=_FLAGS_BASE, order_event=order_event, now=now,
    )
    assert "kk_dinner_carry_risk_v2" not in reason, (
        f"21:00 Warsaw should NOT trigger; route={route} reason={reason}"
    )


def test_kk_flag_off_no_exclusion_even_dinner():
    """ENABLE_KEBAB_KROL_DINNER_EXCLUSION=False → no-op nawet w dinner."""
    result = _make_result()
    fleet = _fleet_gold(result.best)
    order_event = {"restaurant": "Kebab Król", "prep_minutes": 30}
    now = _warsaw_now_at(18, 30)
    flags = {**_FLAGS_BASE, "ENABLE_KEBAB_KROL_DINNER_EXCLUSION": False}
    route, reason = classify_auto_route(
        result, fleet_snapshot=fleet, flags=flags, order_event=order_event, now=now,
    )
    assert "kk_dinner_carry_risk_v2" not in reason, (
        f"flag OFF should not trigger; route={route} reason={reason}"
    )


def test_kk_other_restaurant_dinner_no_exclusion():
    """Inna restauracja w dinner → no-op (KK substring-only)."""
    result = _make_result()
    fleet = _fleet_gold(result.best)
    order_event = {"restaurant": "Pizza Hut", "prep_minutes": 30}
    now = _warsaw_now_at(19, 0)
    route, reason = classify_auto_route(
        result, fleet_snapshot=fleet, flags=_FLAGS_BASE, order_event=order_event, now=now,
    )
    assert "kk_dinner_carry_risk_v2" not in reason, (
        f"non-KK should not trigger; route={route} reason={reason}"
    )


def test_kk_czasowka_dinner_returns_ack_not_alert():
    """KK czasówka (prep_minutes >= 60) → ACK 'czasowka_60min' wygrywa.

    Czasówki wykluczone z KK exclusion przez kolejność: edge detection (czasowka
    ACK) odpala SIĘ PRZED KK guard. Pattern spójny z spec — Bartek wave-line
    judgment dla czasówek.
    """
    result = _make_result()
    fleet = _fleet_gold(result.best)
    order_event = {"restaurant": "Kebab Król", "prep_minutes": 90}
    now = _warsaw_now_at(18, 30)
    route, reason = classify_auto_route(
        result, fleet_snapshot=fleet, flags=_FLAGS_BASE, order_event=order_event, now=now,
    )
    assert route == ROUTE_ACK, f"expected ACK (czasowka), got {route} ({reason})"
    assert "czasowka_60min" in reason, f"expected czasowka reason, got {reason}"


def test_kk_case_insensitive_polish_diacritics():
    """KK matching case-insensitive z polskimi znakami diakrytycznymi (król/Król/KRÓL)."""
    result = _make_result()
    fleet = _fleet_gold(result.best)
    now = _warsaw_now_at(18, 30)
    for name in ("kebab król", "Kebab Król", "KEBAB KRÓL podlasie", "kebab król - Rynek Kościuszki 30"):
        order_event = {"restaurant": name, "prep_minutes": 30}
        route, reason = classify_auto_route(
            result, fleet_snapshot=fleet, flags=_FLAGS_BASE, order_event=order_event, now=now,
        )
        assert route == ROUTE_ALERT, f"KK variant '{name}' should ALERT, got {route} ({reason})"
        assert reason == "kk_dinner_carry_risk_v2", f"variant '{name}' bad reason: {reason}"


# ---------------------------------------------------------------------------
# Module-level smoke (script-style runner; conftest dispatches via subprocess)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback
    tests = [
        test_kk_lunch_routes_normally_not_alert,
        test_kk_dinner_18_warsaw_triggers_alert,
        test_kk_boundary_1659_no_exclusion,
        test_kk_boundary_1700_triggers_alert,
        test_kk_boundary_2059_triggers_alert,
        test_kk_boundary_2100_no_exclusion,
        test_kk_flag_off_no_exclusion_even_dinner,
        test_kk_other_restaurant_dinner_no_exclusion,
        test_kk_czasowka_dinner_returns_ack_not_alert,
        test_kk_case_insensitive_polish_diacritics,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
            traceback.print_exc()
    if failed:
        sys.exit(1)
    print(f"{len(tests)}/{len(tests)} PASS")
    sys.exit(0)
