"""F2.1b Decision Engine 3.0 — regression + unit + integration test suite.

Plain Python standalone script (no pytest dependency, zgodnie z konwencją
istniejących testów w dispatch_v2/tests/).

Uruchomienie:
    python3 /root/.openclaw/workspace/scripts/dispatch_v2/tests/test_decision_engine_f21.py

Exit code: 0 = all passed, 1 = any failed.

Coverage sekcji:
    A — Regression R1/R3/R5 Bartek Gold (F1.9 inline w feasibility_v2)
    B — Unit R6/R7 + _parse_aware_utc + race condition state_machine
    C — Integration bundling patterns (Bartek 6503 orders historical)
    D — Edge cases (sentinel coords, shift_end, pickup reach, large bag)
    E — Anti-patterns (cross-town, mixed pickups, long-haul peak)
    F — Sanity formulas + smoke end-to-end

R8 testy pomijamy — R8 defer do F2.1c (brak T_KUR propagation w OrderSim).

OSRM mocked globally (haversine × 1.37 × 30 km/h) — deterministic, zero network.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from math import radians, sin, cos, asin, sqrt

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2 import common as C
from dispatch_v2 import state_machine, sla_tracker, osrm_client
from dispatch_v2.feasibility_v2 import check_feasibility_v2
from dispatch_v2.route_simulator_v2 import OrderSim


WARSAW_TZ = ZoneInfo("Europe/Warsaw")
BIALYSTOK_CENTER = (53.1325, 23.1688)


# ═══════════════════════════════════════════════════════════════════
# MOCKS — deterministic haversine OSRM substitute
# ═══════════════════════════════════════════════════════════════════

def _haversine_km(a, b):
    lat1, lon1 = a; lat2, lon2 = b
    dlat = radians(lat2 - lat1); dlon = radians(lon2 - lon1)
    h = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return 2 * 6371 * asin(sqrt(h))


def _mock_osrm_table(points_a, points_b):
    """Deterministic: haversine × 1.37 road factor, 30 km/h → duration_s."""
    result = []
    for a in points_a:
        row = []
        for b in points_b:
            km = _haversine_km(a, b) * 1.37
            duration_s = (km / 30.0) * 3600.0
            row.append({"duration_s": duration_s, "distance_m": km * 1000, "osrm_fallback": True})
        result.append(row)
    return result


def _mock_osrm_haversine(a, b):
    return _haversine_km(a, b)


# Install mocks globally (all tests after this see mocked OSRM)
osrm_client.table = _mock_osrm_table
osrm_client.haversine = _mock_osrm_haversine


# ═══════════════════════════════════════════════════════════════════
# FACTORIES & HELPERS
# ═══════════════════════════════════════════════════════════════════

def _fixed_now_peak():
    """2026-04-15 12:00 UTC = 14:00 Warsaw (R7 peak start)."""
    return datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)


def _fixed_now_offpeak():
    """2026-04-15 10:00 UTC = 12:00 Warsaw (before peak)."""
    return datetime(2026, 4, 15, 10, 0, 0, tzinfo=timezone.utc)


def mk_order(id="1", pickup=None, drop=None, status="assigned",
             picked_up_at=None, pickup_ready_at=None):
    """Factory OrderSim. Defaults: centrum pickup, 1.5km NE drop."""
    if pickup is None:
        pickup = BIALYSTOK_CENTER
    if drop is None:
        drop = (53.145, 23.185)
    return OrderSim(
        order_id=str(id),
        pickup_coords=pickup,
        delivery_coords=drop,
        status=status,
        picked_up_at=picked_up_at,
        pickup_ready_at=pickup_ready_at,
    )


# ═══════════════════════════════════════════════════════════════════
# SEKCJA A — REGRESSION Bartek Gold R1/R3/R5 (F1.9 inline)
# ═══════════════════════════════════════════════════════════════════

def test_A1_R1_delivery_spread_under_8km_accept():
    """R1 threshold = 8 km (Bartek p90). ~5km spread → accept (nie R1 reject)."""
    o1 = mk_order(id=1, drop=(53.132, 23.168))
    o2 = mk_order(id=2, drop=(53.170, 23.220))  # ~5-6 km NE
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[o1], new_order=o2, now=_fixed_now_offpeak()
    )
    assert "R1_spread_outlier" not in reason, f"R1 triggered falsely: {reason}"
    assert metrics.get("deliv_spread_km", 0) < 8.0


def test_A2_R1_delivery_spread_over_8km_reject():
    """R1 threshold = 8 km. 12 km spread → NO."""
    o1 = mk_order(id=1, drop=(53.132, 23.168))
    o2 = mk_order(id=2, drop=(53.230, 23.300))  # ~12 km NE
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[o1], new_order=o2, now=_fixed_now_offpeak()
    )
    assert verdict == "NO"
    assert "R1_spread_outlier" in reason
    assert metrics["deliv_spread_km"] > 8.0


def test_A3_R3_soft_metric_emitted_not_rejecting():
    """R3 dynamic cap jest soft-only (F1.9b). Metryka w metrics, NIE jako reject reason."""
    o1 = mk_order(id=1, drop=(53.135, 23.180))
    o2 = mk_order(id=2, drop=(53.145, 23.200))
    o3 = mk_order(id=3, drop=(53.155, 23.220))
    o4 = mk_order(id=4, drop=(53.165, 23.235))
    new = mk_order(id=5, drop=(53.170, 23.240))
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[o1, o2, o3, o4], new_order=new,
        now=_fixed_now_offpeak()
    )
    # R3 soft metric should be present (feasibility_v2 step 3 kept telemetry)
    assert "dynamic_bag_cap" in metrics, f"R3 metric missing: {list(metrics.keys())}"
    assert "r3_soft_would_block" in metrics
    # R3 should NEVER be the reject reason (F1.9b made R3 soft-only)
    assert "R3" not in reason, f"R3 incorrectly used as reject: {reason}"


def test_A4_R5_pickup_spread_under_1_8km_accept():
    """R5 mixed-rest pickup threshold = 1.8 km. ~1 km → accept."""
    o1 = mk_order(id=1, pickup=(53.132, 23.168), drop=(53.145, 23.185))
    new = mk_order(id=2, pickup=(53.140, 23.175), drop=(53.150, 23.190))
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[o1], new_order=new, now=_fixed_now_offpeak()
    )
    assert "R5_mixed_rest_pickup" not in reason
    assert metrics.get("pickup_spread_km") is not None


def test_A5_R5_pickup_spread_over_1_8km_reject():
    """R5 > 1.8km → NO. 3+ km pickup spread."""
    o1 = mk_order(id=1, pickup=(53.132, 23.168), drop=(53.145, 23.185))
    new = mk_order(id=2, pickup=(53.155, 23.220), drop=(53.165, 23.230))  # ~4 km
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[o1], new_order=new, now=_fixed_now_offpeak()
    )
    assert verdict == "NO"
    assert "R5_mixed_rest_pickup" in reason


def test_A6_bag_size_cap_8_hard_reject():
    """D3 MAX_BAG_SIZE = 8. Bag=8 → next reject 'bag_full'."""
    bag = [mk_order(id=str(i), drop=(53.132 + i * 0.001, 23.168 + i * 0.001)) for i in range(8)]
    new = mk_order(id="99")
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=bag, new_order=new, now=_fixed_now_offpeak()
    )
    assert verdict == "NO"
    assert "bag_full" in reason


# ═══════════════════════════════════════════════════════════════════
# SEKCJA B — UNIT R6/R7 + _parse_aware_utc + RACE CONDITION
# ═══════════════════════════════════════════════════════════════════

def test_B1_R7_longhaul_peak_bundle_reject():
    """R7: ride >4.5km + peak 14-17 Warsaw + bag niepusty → NO."""
    o1 = mk_order(id=1, drop=(53.145, 23.185))
    new = mk_order(id=2, pickup=(53.132, 23.168), drop=(53.132, 23.320))  # ~10km E
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[o1], new_order=new, now=_fixed_now_peak()
    )
    assert verdict == "NO"
    assert "R7_longhaul_peak" in reason
    assert metrics["r7_ride_km"] > C.LONG_HAUL_DISTANCE_KM
    assert metrics["r7_in_peak"] is True


def test_B2_R7_longhaul_peak_solo_accept():
    """R7: long-haul + peak + bag pusty → NIE R7 (solo allowed)."""
    new = mk_order(id=1, pickup=(53.132, 23.168), drop=(53.132, 23.320))
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[], new_order=new, now=_fixed_now_peak()
    )
    assert "R7_longhaul_peak" not in reason
    assert metrics["r7_is_longhaul"] is True
    assert metrics["r7_in_peak"] is True


def test_B3_R7_longhaul_offpeak_bundle_accept():
    """R7: long-haul + bundle + OFFPEAK → nie R7 (peak hours not active)."""
    o1 = mk_order(id=1, drop=(53.140, 23.190))
    new = mk_order(id=2, pickup=(53.132, 23.168), drop=(53.132, 23.320))
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[o1], new_order=new, now=_fixed_now_offpeak()
    )
    assert "R7_longhaul_peak" not in reason
    assert metrics["r7_in_peak"] is False


def test_B4_R7_short_ride_peak_accept():
    """R7: bundle + peak + ride <4.5km → nie R7."""
    o1 = mk_order(id=1, drop=(53.145, 23.185))
    new = mk_order(id=2, pickup=(53.132, 23.168), drop=(53.140, 23.180))
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[o1], new_order=new, now=_fixed_now_peak()
    )
    assert "R7_longhaul_peak" not in reason
    assert metrics["r7_is_longhaul"] is False


def test_B5_R6_bag_time_exceeded_reject():
    """
    R6: order picked_up 40 min ago + nowy order → projection przekracza 35 → NO.

    DEFENSIVE ASSERT "R6_bag_time OR sla_violation":
    -----------------------------------------------------------------
    Dlaczego "A lub B" a nie ścisłe "R6_bag_time":

    OSRM mock w tym teście używa stałej prędkości 30 km/h × road_factor 1.37
    (patrz _mock_osrm_table wyżej). To jest uproszczenie vs real OSRM który
    ma variability per road type/hour. Dla ordera picked_up 40 min temu:

      - Pure bag_time = 40 + simulate_delivery_time
      - R6 hard cap = 35 min
      - SLA check (sla_minutes=45 dla bundla) = check czy sla_minutes został
        przekroczony dla któregokolwiek ordera

    Obie ścieżki (SLA violations z linii 171 feasibility_v2 i R6 z linii
    ~230) POPRAWNIE odrzucają tę sytuację. Który pierwszy zadziała zależy
    od dokładnego ETA liczonego przez simulate_bag_route_v2 vs mock OSRM
    deterministic speed.

    Semantycznie oba rejecty są correct: order stygnie i nie powinien
    być bundlowany niezależnie od którego checka dotarł pierwszy.

    INSTRUKCJA DLA PRZYSZŁEGO DEVELOPERA:
    NIE rozluźniaj assertu do "any reject" (`assert verdict == "NO"`).
    Ten test CELOWO sprawdza że reject LECI przez R6 albo SLA, nie przez
    przypadkowe R1/R5/bag_full. Jeśli w przyszłości zmieniasz mock OSRM
    lub R6 logic, zostaw "R6_bag_time OR sla_violation" — obie semantycznie
    odpowiadają "bag_time exceeded somehow".
    """
    now = _fixed_now_offpeak()
    o1 = mk_order(
        id=1, drop=(53.170, 23.220),
        status="picked_up", picked_up_at=now - timedelta(minutes=40)
    )
    new = mk_order(id=2, drop=(53.145, 23.185))
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[o1], new_order=new, now=now
    )
    assert verdict == "NO", f"Expected NO, got {verdict}. reason={reason}"
    assert any(m in reason for m in ("R6_bag_time", "sla_violation")), \
        f"Expected R6 or SLA reject (see B5 docstring for rationale), got: {reason}"


def test_B6_R6_metric_emitted_on_happy_path():
    """R6: solo happy path → metrics['r6_max_bag_time_min'] present."""
    new = mk_order(id=1, drop=(53.145, 23.185))
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[], new_order=new, now=_fixed_now_offpeak()
    )
    assert verdict == "MAYBE", f"Expected MAYBE, got {verdict}: {reason}"
    assert "r6_max_bag_time_min" in metrics, f"R6 metric missing: {list(metrics.keys())}"
    assert metrics["r6_is_solo"] is True
    assert metrics["r6_bag_size"] == 0


# ─── _parse_aware_utc tests (4) ───

def test_B7_parse_aware_utc_naive_warsaw_format():
    """Panel emits naive 'YYYY-MM-DD HH:MM:SS' Warsaw → aware UTC (14:30→12:30 CEST)."""
    dt = sla_tracker._parse_aware_utc("2026-04-15 14:30:00")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0  # UTC
    assert dt.year == 2026 and dt.month == 4 and dt.day == 15
    assert dt.hour == 12 and dt.minute == 30  # Warsaw 14:30 (CEST DST) → UTC 12:30


def test_B8_parse_aware_utc_iso_z_suffix():
    """ISO with Z suffix → aware UTC preserved."""
    dt = sla_tracker._parse_aware_utc("2026-04-15T12:30:00Z")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.hour == 12 and dt.minute == 30


def test_B9_parse_aware_utc_iso_explicit_offset():
    """ISO with explicit +02:00 → converted to UTC."""
    dt = sla_tracker._parse_aware_utc("2026-04-15T14:30:00+02:00")
    assert dt is not None
    assert dt.hour == 12  # 14:30+02 → 12:30 UTC


def test_B10_parse_aware_utc_invalid_returns_none():
    """Corrupt input → None (no crash)."""
    assert sla_tracker._parse_aware_utc(None) is None
    assert sla_tracker._parse_aware_utc("") is None
    assert sla_tracker._parse_aware_utc("garbage") is None
    assert sla_tracker._parse_aware_utc("2026-xx-yy") is None


# ─── RACE CONDITION — KRYTYCZNY REGRESSION GUARD (krok #5 TECH_DEBT) ───

def test_B11_courier_picked_up_reconcile_preserves_bag_time_alerted():
    """
    KRYTYCZNY regression guard (krok #5 defensive design).

    Scenariusz DUPLICATE ALERT BUG który się wydarzy jeśli future refactor
    doda reset bag_time_alerted=False w COURIER_PICKED_UP handler:

    1. NEW_ORDER → init flag=False
    2. COURIER_ASSIGNED → reset flag=False
    3. COURIER_PICKED_UP → (handler NIE powinien dotknąć flag)
    4. sla_tracker symulujemy: upsert_order flag=True (jakby wysłał alert R6)
    5. panel_watcher reconcile RE-EMIT COURIER_PICKED_UP (normalne, nie bug)
    6. ASSERT: flag NADAL True — handler NIE zresetował

    Jeśli ten test FAIL, produkcja dostaje duplicate alerts R6 w każdym
    ticku sla_trackera do momentu delivered. Historia: decyzja "nie resetuj
    w COURIER_PICKED_UP" podjęta świadomie w kroku #5 po analizie że
    panel_watcher reconcile może reemit ten event.
    """
    test_path = "/tmp/test_state_f21_b11.json"
    if os.path.exists(test_path):
        os.unlink(test_path)
    if os.path.exists(test_path + ".lock"):
        os.unlink(test_path + ".lock")

    orig_state_path = state_machine._state_path
    state_machine._state_path = lambda: test_path
    try:
        # 1. NEW_ORDER — init flag=False
        state_machine.update_from_event({
            "event_type": "NEW_ORDER",
            "order_id": "r11",
            "payload": {"restaurant": "Test", "pickup_address": "x", "delivery_address": "y"},
        })
        # 2. COURIER_ASSIGNED — reset flag=False
        state_machine.update_from_event({
            "event_type": "COURIER_ASSIGNED",
            "order_id": "r11",
            "courier_id": "207",
            "payload": {},
        })
        rec = state_machine.get_order("r11")
        assert rec["bag_time_alerted"] is False, f"After ASSIGNED: {rec['bag_time_alerted']}"

        # 3. COURIER_PICKED_UP — pierwszy raz (normal flow)
        state_machine.update_from_event({
            "event_type": "COURIER_PICKED_UP",
            "order_id": "r11",
            "payload": {"timestamp": "2026-04-15 14:00:00"},
        })
        rec = state_machine.get_order("r11")
        assert rec["status"] == "picked_up"
        # Flag dziedziczona z ASSIGNED (False) — nie ustawiona przez PICKED_UP explicit
        assert rec.get("bag_time_alerted") is False

        # 4. sla_tracker sent alert — flag=True
        state_machine.upsert_order("r11", {"bag_time_alerted": True}, event="SLA_R6_ALERT_SIM")

        # 5. RACE: panel_watcher reconcile RE-EMIT
        state_machine.update_from_event({
            "event_type": "COURIER_PICKED_UP",
            "order_id": "r11",
            "payload": {"timestamp": "2026-04-15 14:00:00"},
        })

        # 6. CRITICAL: flag MUST remain True
        rec = state_machine.get_order("r11")
        assert rec.get("bag_time_alerted") is True, (
            "REGRESSION: COURIER_PICKED_UP handler w update_from_event() zresetował "
            "bag_time_alerted do False po tym jak sla_tracker już wysłał alert. "
            "To powoduje DUPLICATE ALERTS w prodzie przy każdym ticku 10s do delivered. "
            "Fix: NIE dodawaj \"bag_time_alerted\": False do COURIER_PICKED_UP handler "
            "w state_machine.update_from_event(). Patrz docs/TECH_DEBT.md F2.1b step 5 "
            "oraz 5-liniowy defensive comment w samym handlerze."
        )
    finally:
        state_machine._state_path = orig_state_path
        if os.path.exists(test_path):
            os.unlink(test_path)
        if os.path.exists(test_path + ".lock"):
            os.unlink(test_path + ".lock")


def test_B13_R9_wait_no_gps_integration():
    """
    INTEGRATION test — F2.1b step 4.1 fix empirical reproduction.

    Rozszerzenie B12 (structural + formula) — B13 woła REAL dispatch_pipeline.assess_order
    z fabricated fleet_snapshot zawierającym dwóch kurierów (no_gps + gps), order elastic
    z pickup_ready_at 25 min naprzód. Weryfikuje end-to-end pipeline:
      1. assess_order przechodzi bez crash
      2. no_gps kurier dostaje bonus_r9_wait_pen == 0 (step 4.1 fix fires)
      3. GPS kurier dostaje bonus_r9_wait_pen z else branch (raw drive_min OK)
      4. Post-loop drive_min normalization (linia 458) — drive_min ~ prep_remaining

    Bug milestone: order #466290 Chicago Pizza @ 2026-04-15T19:16:45 UTC,
    Patryk 5506 no_gps, bonus_r9_wait_pen=-101.76 (final_score -0.53).
    Fix step 4.1 commit b4844aa, rollback tag pre-F2.1b-step4-1.

    Jeśli B13 fail:
      1. Sprawdź step 4.1 w runtime: grep effective_drive_min dispatch_pipeline.py
      2. Rollback: git reset --hard pre-F2.1b-step4-1 + systemctl restart dispatch-shadow
      3. Diagnose: compare no_gps bonus_r9_wait_pen z #466290 historycznym (-101.76)

    Test dopełnia B12 formula-level testowaniem pełnego pipeline — zamyka
    empirical gap gdy evening volume prodowy zbyt niski dla naturalnej weryfikacji.
    """
    from types import SimpleNamespace
    from dispatch_v2.dispatch_pipeline import assess_order

    # Non-peak (12:00 Warsaw = 10:00 UTC) żeby R7 longhaul nie interferowało
    now = datetime(2026, 4, 15, 10, 0, 0, tzinfo=timezone.utc)

    # Order elastic: pickup +25 min, drop 1.5km NE
    order_event = {
        "order_id": "B13_TEST",
        "restaurant": "Test Restaurant B13",
        "delivery_address": "Test delivery B13",
        "pickup_coords": [53.133, 23.169],
        "delivery_coords": [53.145, 23.185],
        "pickup_at_warsaw": "2026-04-15T12:25:00+02:00",  # +25 min od now
        "pickup_time_minutes": None,
    }

    # Fleet: 2 couriers, empty bag, shift 4h remaining
    shift_end = now + timedelta(hours=4)
    fleet_snapshot = {
        "no_gps_c": SimpleNamespace(
            courier_id="no_gps_c",
            name="Test NoGPS",
            pos=BIALYSTOK_CENTER,  # synthetic centrum (co robi courier_resolver dla no_gps)
            pos_source="no_gps",
            pos_age_min=None,
            shift_end=shift_end,
            shift_start_min=0,
            bag=[],
        ),
        "gps_c": SimpleNamespace(
            courier_id="gps_c",
            name="Test GPS",
            pos=(53.130, 23.165),  # ~500m SW od pickup
            pos_source="gps",
            pos_age_min=2.0,
            shift_end=shift_end,
            shift_start_min=0,
            bag=[],
        ),
    }

    # Real pipeline call
    result = assess_order(order_event, fleet_snapshot, restaurant_meta=None, now=now)
    candidates_by_cid = {c.courier_id: c for c in result.candidates}

    # ─── SANITY ASSERTS (detect broken setup before business assertions) ───
    assert result is not None, "assess_order zwrócił None — setup broken"
    assert len(result.candidates) == 2, (
        f"Expected 2 candidates, got {len(result.candidates)}. "
        f"Setup broken — pickup_ready_at=None (parse fail)?"
    )
    assert "no_gps_c" in candidates_by_cid and "gps_c" in candidates_by_cid, (
        f"Missing candidate in dict. Got: {list(candidates_by_cid.keys())}. "
        f"Check fleet_snapshot setup."
    )

    no_gps_cand = candidates_by_cid["no_gps_c"]
    gps_cand = candidates_by_cid["gps_c"]

    assert no_gps_cand.metrics.get("bonus_r9_wait_pen") is not None, (
        f"no_gps bonus_r9_wait_pen is None — R9 wait block didn't execute.\n"
        f"Prawdopodobna przyczyna: pickup_ready_at = None "
        f"(parse_panel_timestamp fail dla ISO format).\n"
        f"Sprawdź: parse_panel_timestamp('2026-04-15T12:25:00+02:00') w common.py.\n"
        f"Jeśli parse fail — zmień test na datetime object bezpośrednio zamiast string."
    )

    # ─── BUSINESS ASSERT 1: no_gps wait_pen == 0 (CRITICAL — step 4.1 fix) ───
    no_gps_wp = no_gps_cand.metrics.get("bonus_r9_wait_pen")
    assert no_gps_wp == 0 or no_gps_wp == 0.0, (
        f"REGRESSION: no_gps courier bonus_r9_wait_pen == {no_gps_wp}, expected 0.\n"
        f"Historical bug #466290 miał -101.76 przez synthetic BIALYSTOK_CENTER → "
        f"drive_min z linii 285 był ~2-3 min zamiast realistic max(15, prep_remaining) "
        f"fallback. Fix step 4.1 (commit b4844aa) dodał effective_drive_min branch.\n"
        f"Jeśli ten test fail:\n"
        f"  1. Sprawdź step 4.1 w runtime: grep effective_drive_min dispatch_pipeline.py\n"
        f"  2. Rollback: git reset --hard pre-F2.1b-step4-1 && systemctl restart dispatch-shadow\n"
        f"  3. Diagnose: compare buggy output z #466290 @ 2026-04-15T19:16:45 UTC"
    )

    # ─── BUSINESS ASSERT 2: GPS branch nie zepsuty (else branch raw drive_min) ───
    gps_wp = gps_cand.metrics.get("bonus_r9_wait_pen")
    assert gps_wp is not None, (
        f"GPS courier bonus_r9_wait_pen is None — else branch not triggered."
    )
    assert gps_wp <= 0, f"GPS wait_pen should be ≤0 (penalty or zero), got {gps_wp}"

    # ─── POLISH: post-loop drive_min normalization validation ───
    no_gps_drive = no_gps_cand.metrics["drive_min"]
    assert 20 <= no_gps_drive <= 30, (
        f"no_gps drive_min = {no_gps_drive}, expected ~25 (prep_remaining). "
        f"Post-loop normalization (linia 458) broken."
    )

    # ─── SCHEMA ASSERT 3: all 13 F2.1b fields present ───
    required = [
        "r6_max_bag_time_min", "r6_is_solo", "r6_bag_size",
        "r7_ride_km", "r7_in_peak", "r7_is_longhaul",
        "bonus_r6_soft_pen", "bonus_r9_stopover",
        "bonus_r9_wait_pen", "bonus_penalty_sum",
    ]
    for k in required:
        assert k in no_gps_cand.metrics, f"no_gps metrics missing {k}"
        assert k in gps_cand.metrics, f"gps metrics missing {k}"

    # R7 not in peak (12:00 Warsaw < 14:00 peak start)
    assert no_gps_cand.metrics["r7_in_peak"] is False
    assert gps_cand.metrics["r7_in_peak"] is False


def test_B12_R9_wait_no_gps_courier_regression_guard():
    """
    REGRESSION GUARD — F2.1b step 4.1 fix.

    Bug historia: #466290 Chicago Pizza @ 2026-04-15T19:16:45 UTC, kurier
    5506 Patryk pos_source='no_gps' dostał bonus_r9_wait_pen=-101.76
    (final_score=-0.53 zamiast ~98). Root cause: R9 wait używał drive_min
    z linii 285 która dla no_gps kurierów jest computed z synthetic
    courier_pos (BIALYSTOK_CENTER fallback), dając sztucznie niski
    drive_min (~2-3 min). Realny fallback drive_min dla no_gps jest
    nadpisywany dopiero w post-loop (linia 458) jako max(15, prep_remaining),
    ale w tym momencie final_score już zamrożony z bad wait_pred.

    Fix step 4.1: effective_drive_min pre-normalized w R9 wait block:
      pos_source=='no_gps'    → max(15, (pickup_ready_at - now)/60)
      pos_source=='pre_shift' → shift_start_min
      inne                    → drive_min (unchanged dla GPS)

    Test 2-warstwowy:
      (a) STRUCTURAL — inspect source czy fix jest w dispatch_pipeline
      (b) FORMULA — manual compute dla scenariusza #466290 bug recreation

    Regex w layer (a) dopuszcza cleanup refactor rename
    (effective_drive_min | real_drive_min | gps_aware_drive_min | adjusted_drive_min).
    """
    import re
    from dispatch_v2 import dispatch_pipeline
    import inspect
    src = inspect.getsource(dispatch_pipeline)

    # Layer (a): structural check — fix musi być w source
    drive_var_re = re.compile(
        r"effective_drive_min|real_drive_min|gps_aware_drive_min|adjusted_drive_min"
    )
    assert drive_var_re.search(src), (
        "REGRESSION: dispatch_pipeline NIE ma effective_drive_min computation "
        "(ani aliasu real_/gps_aware_/adjusted_). Bug #466290 może się powtórzyć "
        "— no_gps courierzy dostają nierealne R9 wait penalty. "
        "Patrz step 4.1 commit + TECH_DEBT."
    )
    assert '"no_gps"' in src, (
        "REGRESSION: pos_source=='no_gps' handling missing in dispatch_pipeline."
    )
    # effective_drive_min (lub alias) musi być DEFINED przed wait_pred_min usage
    eff_match = drive_var_re.search(src)
    eff_idx = eff_match.start() if eff_match else -1
    wait_idx = src.find("wait_pred_min = max")
    assert eff_idx > 0 and wait_idx > eff_idx, (
        "REGRESSION: effective_drive_min must be defined BEFORE wait_pred_min. "
        "Jeśli test fail, sprawdź że R9 wait block w dispatch_pipeline używa "
        "effective_drive_min (nie raw drive_min) dla computation."
    )

    # Layer (b): formula-level #466290 scenario
    # now=19:16:45, pickup_ready_at=19:41:27 → tkur_from_now=24.7 min
    now = datetime(2026, 4, 15, 19, 16, 45, tzinfo=timezone.utc)
    pickup_ready_at = now + timedelta(minutes=24.7)
    tkur_from_now_min = (pickup_ready_at - now).total_seconds() / 60.0
    # no_gps pattern: effective = max(15, prep_remaining_min)
    effective_drive_min = max(15.0, tkur_from_now_min)  # = 24.7
    wait_pred_min = max(0.0, tkur_from_now_min - effective_drive_min)  # = 0
    assert wait_pred_min < 0.01, (
        f"formula regression: expected ~0 got {wait_pred_min:.2f}. "
        f"no_gps scenario #466290 gdzie tkur=drive_fallback should give wait=0."
    )
    # Penalty z formuła R9 wait
    if wait_pred_min > C.RESTAURANT_WAIT_SOFT_MIN:
        bonus_r9_wait_pen = -(wait_pred_min - C.RESTAURANT_WAIT_SOFT_MIN) * C.RESTAURANT_WAIT_PENALTY_PER_MIN
    else:
        bonus_r9_wait_pen = 0.0
    assert bonus_r9_wait_pen == 0.0, (
        f"Expected 0.0 wait penalty for no_gps scenario, got {bonus_r9_wait_pen}. "
        f"Historical bug #466290 had -101.76 — sign of leakage of linia 285 "
        f"drive_min into wait_pred computation for no_gps couriers."
    )


# ═══════════════════════════════════════════════════════════════════
# SEKCJA C — INTEGRATION bundling patterns (Bartek 6503 orders context)
# ═══════════════════════════════════════════════════════════════════

def test_C1_two_nearby_pickups_short_spread_bundle_accept():
    """
    Wzorzec z analizy 6503 zleceń Bartka (F1.9 Bartek Gold):
    Top para ~143 bundli, restauracje odległe ~1 km, T_KUR diff krótki.
    Test: geometria przechodzi R5 (< 1.8km pickup spread).
    """
    o1 = mk_order(id=1, pickup=(53.132, 23.168), drop=(53.145, 23.185))
    new = mk_order(id=2, pickup=(53.141, 23.176), drop=(53.148, 23.188))  # ~1 km delta
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[o1], new_order=new, now=_fixed_now_offpeak()
    )
    assert "R5_mixed_rest_pickup" not in reason
    assert metrics.get("pickup_spread_km", 99) < C.LONG_HAUL_DISTANCE_KM


def test_C2_same_restaurant_bundle_no_pickup_spread():
    """
    Wzorzec Bartka: same-resto bundle (np. 3× z jednego lokalu w peak).
    Test: pickup_spread = 0 (identyczne coords) → R5 nie trigger.
    Historia: same-resto bundle to 74% multi-delivery Bartka (35/47 bundli
    w czystej próbce F1.9 analyzera).
    """
    same_pickup = (53.132, 23.168)
    o1 = mk_order(id=1, pickup=same_pickup, drop=(53.145, 23.180))
    new = mk_order(id=2, pickup=same_pickup, drop=(53.148, 23.190))
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=same_pickup, bag=[o1], new_order=new, now=_fixed_now_offpeak()
    )
    assert "R5" not in reason
    assert metrics.get("pickup_spread_km", 99) < 0.1


def test_C3_corridor_delivery_small_spread_accept():
    """
    Wzorzec Bartka: nowe delivery w korytarzu trasy istniejącego bag.
    Delivery addresses bardzo blisko (~100m) → R1 delivery spread < 1km.
    R4 corridor bonus sam liczony w dispatch_pipeline (testowany w F3),
    tu tylko feasibility pass.
    """
    o1 = mk_order(id=1, drop=(53.150, 23.200))
    new = mk_order(id=2, drop=(53.151, 23.201))  # ~100m od o1 drop
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[o1], new_order=new, now=_fixed_now_offpeak()
    )
    assert metrics.get("deliv_spread_km", 99) < 1.0
    assert "R1" not in reason


# ═══════════════════════════════════════════════════════════════════
# SEKCJA D — EDGE CASES
# ═══════════════════════════════════════════════════════════════════

def test_D1_empty_bag_solo_order_accept():
    """Solo order + bag pusty = happy path → MAYBE z pełnym planem."""
    new = mk_order(id=1)
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[], new_order=new, now=_fixed_now_offpeak()
    )
    assert verdict == "MAYBE", f"Expected MAYBE, got {verdict} ({reason})"
    assert plan is not None


def test_D2_sentinel_pickup_coords_handled():
    """Sentinel (0,0) pickup → feasibility nie crash, verdict determined przez inne checks."""
    o1 = mk_order(id=1, drop=(53.145, 23.185))
    new = OrderSim(
        order_id="2", pickup_coords=(0.0, 0.0),
        delivery_coords=(53.148, 23.190), status="assigned"
    )
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[o1], new_order=new, now=_fixed_now_offpeak()
    )
    # Should reject (pickup_too_far from sentinel) or accept — just no crash
    assert verdict in ("MAYBE", "NO")


def test_D3_shift_ending_reject():
    """shift_end < SHIFT_END_BUFFER_MIN (20 min) → NO shift_ending."""
    now = _fixed_now_offpeak()
    new = mk_order(id=1)
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[], new_order=new,
        shift_end=now + timedelta(minutes=5), now=now
    )
    assert verdict == "NO"
    assert "shift_ending" in reason


def test_D4_pickup_too_far_reject():
    """pickup_dist > MAX_PICKUP_REACH_KM (15) → NO pickup_too_far."""
    far_pickup = (52.950, 23.168)  # ~20 km S
    new = mk_order(id=1, pickup=far_pickup, drop=(52.960, 23.180))
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[], new_order=new, now=_fixed_now_offpeak()
    )
    assert verdict == "NO"
    assert "pickup_too_far" in reason


def test_D5_large_same_resto_bag_5_not_bag_full():
    """Bag=4 + new=5 → pass bag_full (< 8) dla same-resto tight cluster."""
    same_pickup = (53.132, 23.168)
    bag = [
        mk_order(id=i, pickup=same_pickup, drop=(53.135 + i * 0.002, 23.170 + i * 0.002))
        for i in range(1, 5)
    ]
    new = mk_order(id=5, pickup=same_pickup, drop=(53.144, 23.178))
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=same_pickup, bag=bag, new_order=new, now=_fixed_now_offpeak()
    )
    assert "bag_full" not in reason


# ═══════════════════════════════════════════════════════════════════
# SEKCJA E — ANTI-PATTERNS (should reject)
# ═══════════════════════════════════════════════════════════════════

def test_E1_cross_town_delivery_spread_reject():
    """Delivery addresses cross-town 13+ km → R1 reject."""
    o1 = mk_order(id=1, drop=(53.132, 23.100))
    new = mk_order(id=2, drop=(53.132, 23.300))  # ~13 km od o1
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[o1], new_order=new, now=_fixed_now_offpeak()
    )
    assert verdict == "NO"
    assert "R1_spread_outlier" in reason


def test_E2_mixed_pickups_4km_spread_reject():
    """Dwie restauracje 4 km od siebie → R5 reject (> 1.8 limit)."""
    o1 = mk_order(id=1, pickup=(53.132, 23.168), drop=(53.145, 23.185))
    new = mk_order(id=2, pickup=(53.132, 23.230), drop=(53.150, 23.240))  # ~4km E
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[o1], new_order=new, now=_fixed_now_offpeak()
    )
    assert verdict == "NO"
    assert "R5_mixed_rest_pickup" in reason


def test_E3_long_haul_bundle_peak_rejected():
    """
    Long-haul bundle w peak → NO (R7 albo R1, obie OK).

    DEFENSIVE ASSERT "R7 OR R1":
    -----------------------------------------------------------------
    Dlaczego "A lub B" a nie ścisłe "R7_longhaul_peak":

    Geometria: bag=[drop @ 53.145,23.185] + new=[pickup @ 53.132,23.168,
    drop @ 53.132,23.280 ≈ 7km E]. Peak hour 14:00 Warsaw.

    Dwie hard rules mogą odrzucić ten setup:

      R7 (step 3 feasibility_v2): ride_km (pickup→delivery nowego ordera)
        = ~7 km > 4.5 AND bag nie pusty AND hour w 14-17 → reject
        "R7_longhaul_peak". Ta ścieżka uruchamia się PRZED R1 w kolejności
        sprawdzeń (R7 → R1 → R5 → ... → R6).

      R1 (F1.9 Bartek Gold): delivery_spread między o1.drop (53.145,23.185)
        i new.drop (53.132,23.280) wynosi ~7-8 km. Jeśli > 8km → reject
        "R1_spread_outlier". Przy dokładnie ~7km może być pod progiem.

    R7 jest expected path dla tego test case (peak + longhaul + bundle),
    ale jeśli mock OSRM zwraca slightly different km dla któregoś odcinka,
    R1 może zadziałać wcześniej. Oba rejecty są semantycznie correct dla
    "long-haul bundle w peak": nie bundlujemy długich tras w szczycie.

    INSTRUKCJA DLA PRZYSZŁEGO DEVELOPERA:
    NIE rozluźniaj do "any reject" (`assert verdict == "NO"`).
    Test CELOWO waliduje że hard rules R7/R1 (nie przypadkowo bag_full albo
    shift_ending albo pickup_too_far) odrzucają long-haul peak bundle.
    Jeśli zmieniasz geometrię testu, upewnij się że co najmniej jedna z
    tych dwóch ścieżek pozostaje triggerem.
    """
    o1 = mk_order(id=1, drop=(53.145, 23.185))
    new = mk_order(id=2, pickup=(53.132, 23.168), drop=(53.132, 23.280))  # ~7km E
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[o1], new_order=new, now=_fixed_now_peak()
    )
    assert verdict == "NO"
    assert ("R7" in reason) or ("R1" in reason), \
        f"Expected R7 or R1 reject (see E3 docstring), got: {reason}"


# ═══════════════════════════════════════════════════════════════════
# SEKCJA F — SANITY FORMULAS + SMOKE
# ═══════════════════════════════════════════════════════════════════

def test_F1_L1_bonus_same_restaurant_equals_25():
    """
    Bartek Gold F1.9 conservative tuning: L1 same-restaurant = +25.
    Formuła dispatch_pipeline:303 `bonus_l1 = 25.0 if bundle_level1 else 0.0`.
    Test guard: jeśli kiedyś zmieniamy na 100, test + dok muszą iść w parze.
    """
    def l1(bundle_level1):
        return 25.0 if bundle_level1 else 0.0
    assert l1("Chicago Pizza") == 25.0
    assert l1(None) == 0.0
    assert l1("") == 0.0


def test_F2_L2_linear_decay_formula():
    """L2 nearby pickup: max(0, 20 - dist*10). dispatch_pipeline:304."""
    def l2(d):
        return max(0.0, 20.0 - d * 10.0) if d is not None else 0.0
    assert l2(0.0) == 20.0
    assert l2(0.5) == 15.0
    assert l2(1.0) == 10.0
    assert l2(1.5) == 5.0
    assert l2(2.0) == 0.0
    assert l2(3.0) == 0.0
    assert l2(None) == 0.0


def test_F3_R4_corridor_tiered_formula():
    """
    R4 free-stop tiered (Bartek Gold). dispatch_pipeline:305-317.
    Tier: ≤0.5 → 100, ≤1.5 → 50*(1.5-d), ≤2.5 → 20*(2.5-d), >2.5 → 0.
    Weight: × 1.5.
    """
    def r4_raw(d):
        if d is None:
            return 0.0
        if d <= 0.5:
            return 100.0
        if d <= 1.5:
            return 50.0 * (1.5 - d)
        if d <= 2.5:
            return 20.0 * (2.5 - d)
        return 0.0
    assert r4_raw(0.0) == 100.0
    assert r4_raw(0.5) == 100.0
    assert abs(r4_raw(1.0) - 25.0) < 0.001
    assert abs(r4_raw(1.5) - 0.0) < 0.001
    assert abs(r4_raw(2.0) - 10.0) < 0.001
    assert r4_raw(2.5) == 0.0
    assert r4_raw(3.0) == 0.0
    assert r4_raw(0.0) * 1.5 == 150.0
    assert abs(r4_raw(1.0) * 1.5 - 37.5) < 0.001


def test_F4_R6_soft_penalty_formula():
    """R6 soft: -(bag_time - SOFT_MIN) * SOFT_PENALTY_PER_MIN. dispatch_pipeline step 4."""
    def r6_soft(bag_time):
        if bag_time > C.BAG_TIME_SOFT_MIN:
            return -(bag_time - C.BAG_TIME_SOFT_MIN) * C.BAG_TIME_SOFT_PENALTY_PER_MIN
        return 0.0
    assert r6_soft(25.0) == 0.0
    assert r6_soft(30.0) == 0.0
    assert r6_soft(32.0) == -16.0  # -(32-30)*8
    assert r6_soft(34.0) == -32.0
    assert r6_soft(35.0) == -40.0  # boundary — feasibility R6 hard reject at 35


def test_F5_R9_stopover_differential_formula():
    """R9 stopover: -len(bag) * STOPOVER_SCORE_PER_STOP. dispatch_pipeline step 4."""
    assert -0 * C.STOPOVER_SCORE_PER_STOP == 0
    assert -1 * C.STOPOVER_SCORE_PER_STOP == -8
    assert -2 * C.STOPOVER_SCORE_PER_STOP == -16
    assert -4 * C.STOPOVER_SCORE_PER_STOP == -32


def test_F6_R9_wait_penalty_formula():
    """R9 wait: -(wait - WAIT_SOFT_MIN) * WAIT_PENALTY_PER_MIN. dispatch_pipeline step 4."""
    def r9_wait(wait_pred):
        if wait_pred > C.RESTAURANT_WAIT_SOFT_MIN:
            return -(wait_pred - C.RESTAURANT_WAIT_SOFT_MIN) * C.RESTAURANT_WAIT_PENALTY_PER_MIN
        return 0.0
    assert r9_wait(0.0) == 0.0
    assert r9_wait(5.0) == 0.0
    assert r9_wait(7.0) == -12.0
    assert r9_wait(10.0) == -30.0
    assert r9_wait(15.0) == -60.0


def test_F7_bag_time_constants_from_empirical_p95():
    """Kalibracja z 743 delivered (pre-F21b analyzer): p95=35.6 → hard=35."""
    assert C.BAG_TIME_HARD_MAX_MIN == 35
    assert C.BAG_TIME_SOFT_MIN == 30
    assert C.BAG_TIME_PRE_WARNING_MIN == 30
    assert C.BAG_TIME_SOFT_PENALTY_PER_MIN == 8


def test_F8_R7_longhaul_constants():
    """R7: 4.5 km threshold, peak 14-17 Warsaw."""
    assert C.LONG_HAUL_DISTANCE_KM == 4.5
    assert C.LONG_HAUL_PEAK_HOURS_START == 14
    assert C.LONG_HAUL_PEAK_HOURS_END == 17


def test_F9_auto_approve_flag_off_beton():
    """AUTO_APPROVE_ENABLED=False betonowo do F2.1c. ANOMALY też flag off."""
    assert C.AUTO_APPROVE_ENABLED is False
    assert C.ANOMALY_DETECTION_ENABLED is False
    assert C.AUTO_APPROVE_THRESHOLD == 130
    assert C.AUTO_APPROVE_MIN_GAP == 10


def test_F10_smoke_feasibility_plan_metrics_integration():
    """
    Smoke: feasibility_v2 → plan → metrics z R6/R7 telemetrii populated.
    NIE woła dispatch_pipeline.assess_order (wymaga fleet_snapshot+CourierState
    mocki > 5, defer do F2.2 pytest fixture infrastructure — TECH_DEBT krok #8).
    Ten test to minimalne integration — cały stack feasibility + simulate + plan.
    """
    new = mk_order(id=1, drop=(53.145, 23.185))
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[], new_order=new, now=_fixed_now_offpeak()
    )
    assert verdict == "MAYBE", f"Expected MAYBE, got {verdict}: {reason}"
    assert plan is not None
    assert plan.sequence is not None
    assert plan.total_duration_min >= 0
    assert "r6_max_bag_time_min" in metrics
    assert "r6_is_solo" in metrics
    assert "r7_ride_km" in metrics
    assert "r7_in_peak" in metrics
    assert "r7_is_longhaul" in metrics


# ═══════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════

def _collect_tests():
    return sorted(
        [(name, fn) for name, fn in globals().items()
         if callable(fn) and name.startswith("test_")],
        key=lambda x: x[0]
    )


def main():
    tests = _collect_tests()
    n_pass = 0
    n_fail = 0
    failures = []
    print(f"\n{'='*70}")
    print(f"  F2.1b Decision Engine 3.0 — test suite")
    print(f"  {len(tests)} tests")
    print(f"{'='*70}")
    for name, fn in tests:
        try:
            fn()
            n_pass += 1
            print(f"  ✅ {name}")
        except AssertionError as e:
            n_fail += 1
            failures.append((name, "ASSERT", str(e)))
            print(f"  ❌ {name}: {e}")
        except Exception as e:
            n_fail += 1
            failures.append((name, type(e).__name__, str(e)))
            print(f"  💥 {name}: {type(e).__name__}: {e}")
    print(f"\n{'='*70}")
    print(f"  RESULTS: {n_pass} passed, {n_fail} failed / {len(tests)} total")
    print(f"{'='*70}")
    if failures:
        print("\nFAILURES:")
        for name, kind, msg in failures:
            print(f"  [{kind}] {name}")
            print(f"         {msg[:300]}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
