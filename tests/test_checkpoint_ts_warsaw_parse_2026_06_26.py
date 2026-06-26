"""ETAP 4 — ON≠OFF dla ENABLE_CHECKPOINT_TS_WARSAW_PARSE (sprint TZ-fix checkpointów,
2026-06-26).

Bug: orders_state `picked_up_at`/`delivered_at` = NAIWNY czas Warsaw (panel Rutcom),
a 4 miejsca w courier_resolver re-parsowały je jako UTC (omijając parse_panel_timestamp
z granicy OrderSim) → dla świeżego odbioru elapsed/age UJEMNE → interpolacja pozycji
(F4 Krok 2) + recent-activity MARTWE (0/16984), a staleness (ZOMBIE-guard + per-status)
zaniżały wiek odbioru o offset Warszawy (~2h) → ghost łapany ~2h za późno.

Fix u źródła: helper `_parse_checkpoint_ts` (flaga ON → parse_panel_timestamp = naive
→ Warszawa; OFF = legacy fromisoformat+UTC, bajt-identyczne). 4 bliźniacze ścieżki:
  S1 _compute_interp_pos      (pozycja — interpolacja)
  S2 recent-activity scan     (pozycja — last_delivered/last_picked_up_recent)
  S3 ZOMBIE-guard             (staleness worka)
  S4 per-status staleness     (staleness worka — gałąź picked_up)

Test toggluje flagę przez patch stałej modułu (conftest._isolate_flags_json wycina
ETAP4 z flags.json → stała modułu steruje), mirror test_f4_courier_pos_interp.
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import courier_resolver as CR  # noqa: E402
from dispatch_v2 import common as C  # noqa: E402

WARSAW = ZoneInfo("Europe/Warsaw")
PICKUP = [53.1400, 23.1600]
DELIVERY = [53.1100, 23.2200]


def _naive_warsaw(now_utc, minutes_ago):
    """Naiwny string czasu Warsaw (format orders_state) sprzed `minutes_ago` minut."""
    return (now_utc.astimezone(WARSAW) - timedelta(minutes=minutes_ago)).strftime(
        "%Y-%m-%d %H:%M:%S")


def _warsaw_offset_min(now_utc):
    return now_utc.astimezone(WARSAW).utcoffset().total_seconds() / 60.0


def _run_fleet(state, *, parse_on, gps=None, osrm_min=10.0,
               k1=False, k2=True):
    """build_fleet_snapshot z izolacją I/O (mirror test_f4._run) + toggle flagi TZ."""
    osrm_mock = mock.Mock(return_value={
        "duration_min": osrm_min, "distance_km": 5.0,
        "duration_s": osrm_min * 60, "distance_m": 5000})
    with mock.patch.object(CR, "_load_kurier_piny", return_value={}), \
         mock.patch.object(CR, "_load_courier_names", return_value={"520": "Test Kurier"}), \
         mock.patch.object(CR, "_load_gps_positions", return_value=gps or {}), \
         mock.patch.object(CR, "_load_last_known_pos", return_value={}), \
         mock.patch.object(CR, "ENABLE_CHECKPOINT_TS_WARSAW_PARSE", parse_on), \
         mock.patch.object(CR, "ENABLE_F4_COURIER_POS_INTERP", k2), \
         mock.patch.object(CR, "ENABLE_F4_COURIER_POS_PICKUP_PROXY", k1), \
         mock.patch.object(CR.osrm_client, "route", osrm_mock), \
         mock.patch("dispatch_v2.state_machine.get_all", return_value=state):
        return CR.build_fleet_snapshot()


# ───────────────────────── helper parse (rdzeń wszystkich 4 sites) ──────────

def test_parse_naive_warsaw_on_vs_off():
    """OFF traktuje naiwny Warsaw jak UTC; ON jak Warszawę → różnica = offset Warsaw.
    ON daje dodatni elapsed (świeży odbiór), OFF ujemny (fikcyjna przyszłość)."""
    now = datetime.now(timezone.utc)
    raw = _naive_warsaw(now, 5)
    with mock.patch.object(CR, "ENABLE_CHECKPOINT_TS_WARSAW_PARSE", False):
        off = CR._parse_checkpoint_ts(raw)
    with mock.patch.object(CR, "ENABLE_CHECKPOINT_TS_WARSAW_PARSE", True):
        on = CR._parse_checkpoint_ts(raw)
    assert off is not None and on is not None
    diff_min = (off - on).total_seconds() / 60.0
    assert abs(diff_min - _warsaw_offset_min(now)) < 0.01, \
        f"różnica {diff_min} != offset Warsaw {_warsaw_offset_min(now)}"
    assert (now - on).total_seconds() / 60.0 > 0, "ON: świeży odbiór = dodatni elapsed"
    assert (now - off).total_seconds() / 60.0 < 0, "OFF: naiwny-jako-UTC = ujemny elapsed"


def test_parse_aware_utc_identical_on_off():
    """Aware-UTC ('T'+offset) — pola updated_at/assigned_at — ON==OFF (regresja-safe)."""
    raw = "2026-06-26T11:33:58+00:00"
    with mock.patch.object(CR, "ENABLE_CHECKPOINT_TS_WARSAW_PARSE", False):
        off = CR._parse_checkpoint_ts(raw)
    with mock.patch.object(CR, "ENABLE_CHECKPOINT_TS_WARSAW_PARSE", True):
        on = CR._parse_checkpoint_ts(raw)
    assert off == on == datetime(2026, 6, 26, 11, 33, 58, tzinfo=timezone.utc)


def test_parse_none_failsoft():
    """None / garbage → None pod OBIEMA flagami (fail-soft niezmieniony)."""
    for v in (None, "", "nonsense"):
        with mock.patch.object(CR, "ENABLE_CHECKPOINT_TS_WARSAW_PARSE", True):
            assert CR._parse_checkpoint_ts(v) is None
        with mock.patch.object(CR, "ENABLE_CHECKPOINT_TS_WARSAW_PARSE", False):
            assert CR._parse_checkpoint_ts(v) is None


# ───────────────────────── S1 interpolacja pozycji (fleet) ──────────────────

def _picked_state(now, minutes_ago):
    return {"900": {
        "courier_id": "520", "status": "picked_up", "order_id": "900",
        "pickup_coords": list(PICKUP), "delivery_coords": list(DELIVERY),
        "assigned_at": now.isoformat(), "updated_at": now.isoformat(),
        "picked_up_at": _naive_warsaw(now, minutes_ago)}}


def test_s1_interp_dead_off_alive_on():
    """OFF: świeży naiwny picked_up_at → elapsed<0 → interp pada → legacy delivery.
    ON: elapsed poprawny → interp odpala (last_picked_up_interp)."""
    now = datetime.now(timezone.utc)
    state = _picked_state(now, 5)  # 5 min temu (Warsaw)
    off = _run_fleet(state, parse_on=False, k1=False, k2=True).get("520")
    on = _run_fleet(state, parse_on=True, k1=False, k2=True).get("520")
    assert off is not None and on is not None
    assert off.pos_source == "last_picked_up_delivery", \
        f"OFF: interp martwe → legacy, got {off.pos_source}"
    assert on.pos_source == "last_picked_up_interp", \
        f"ON: interp żyje, got {on.pos_source}"


# ───────────────────────── S2 recent-activity (fleet) ───────────────────────

def test_s2_recent_activity_dead_off_alive_on():
    """Kurier tylko z delivered (brak active bag, brak GPS/store). OFF: age<0 →
    recent pominięte → no_gps. ON: age poprawny <30 → last_delivered."""
    now = datetime.now(timezone.utc)
    state = {"901": {
        "courier_id": "520", "status": "delivered", "order_id": "901",
        "delivery_coords": list(DELIVERY),
        "assigned_at": now.isoformat(), "updated_at": now.isoformat(),
        "delivered_at": _naive_warsaw(now, 10)}}
    off = _run_fleet(state, parse_on=False).get("520")
    on = _run_fleet(state, parse_on=True).get("520")
    assert off is not None and on is not None
    assert off.pos_source == "no_gps", f"OFF: recent martwe → no_gps, got {off.pos_source}"
    assert on.pos_source == "last_delivered", \
        f"ON: recent żyje → last_delivered, got {on.pos_source}"


# ───────────────────────── S3/S4 staleness (_bag_not_stale) ─────────────────

def _picked_order(now, minutes_ago):
    return {"order_id": "950", "status": "picked_up",
            "updated_at": now.isoformat(),  # świeży (aware UTC) — nie maskuje per-status
            "picked_up_at": _naive_warsaw(now, minutes_ago)}


def test_s3_zombie_guard_staleness_flips():
    """ZOMBIE-guard ON (default). picked_up_at 120 min temu (>90 próg). OFF: wiek
    zaniżony (~0) → NIE stale (True). ON: wiek 120 → ghost → stale (False)."""
    now = datetime.now(timezone.utc)
    order = _picked_order(now, 120)
    with mock.patch.object(C, "STRICT_BAG_RECONCILIATION", True):
        with mock.patch.object(CR, "ENABLE_CHECKPOINT_TS_WARSAW_PARSE", False):
            off = CR._bag_not_stale(order, now)
        with mock.patch.object(CR, "ENABLE_CHECKPOINT_TS_WARSAW_PARSE", True):
            on = CR._bag_not_stale(order, now)
    assert off is True, "OFF: wiek zaniżony → nie-stale"
    assert on is False, "ON: ghost >90min → stale"


def _fake_flag_zombie_off(real):
    def _f(name, default=False):
        if name == "ENABLE_ZOMBIE_PICKUP_AT_GUARD":
            return False
        return real(name, default)
    return _f


def test_s4_perstatus_staleness_flips_zombie_off():
    """ZOMBIE-guard wyłączony → izoluje gałąź per-status (S4). Ta sama inwersja:
    OFF nie-stale, ON stale → dowód że S4 też naprawione (nie tylko S3)."""
    now = datetime.now(timezone.utc)
    order = _picked_order(now, 120)
    with mock.patch.object(C, "STRICT_BAG_RECONCILIATION", True), \
         mock.patch.object(CR, "flag", side_effect=_fake_flag_zombie_off(C.flag)):
        with mock.patch.object(CR, "ENABLE_CHECKPOINT_TS_WARSAW_PARSE", False):
            off = CR._bag_not_stale(order, now)
        with mock.patch.object(CR, "ENABLE_CHECKPOINT_TS_WARSAW_PARSE", True):
            on = CR._bag_not_stale(order, now)
    assert off is True, "OFF S4: wiek zaniżony → nie-stale"
    assert on is False, "ON S4: per-status wiek 120>90 → stale"


def test_assigned_staleness_unaffected_by_flag():
    """status=assigned używa aware-UTC updated_at → ON==OFF (fix nie dotyka UTC pól)."""
    now = datetime.now(timezone.utc)
    order = {"order_id": "951", "status": "assigned",
             "updated_at": (now - timedelta(minutes=120)).isoformat(),  # aware UTC, 120 min
             "assigned_at": (now - timedelta(minutes=120)).isoformat()}
    with mock.patch.object(C, "STRICT_BAG_RECONCILIATION", True):
        with mock.patch.object(CR, "ENABLE_CHECKPOINT_TS_WARSAW_PARSE", False):
            off = CR._bag_not_stale(order, now)
        with mock.patch.object(CR, "ENABLE_CHECKPOINT_TS_WARSAW_PARSE", True):
            on = CR._bag_not_stale(order, now)
    assert off == on is False, "aware-UTC 120min → stale identycznie ON i OFF"


if __name__ == "__main__":
    tests = [
        test_parse_naive_warsaw_on_vs_off,
        test_parse_aware_utc_identical_on_off,
        test_parse_none_failsoft,
        test_s1_interp_dead_off_alive_on,
        test_s2_recent_activity_dead_off_alive_on,
        test_s3_zombie_guard_staleness_flips,
        test_s4_perstatus_staleness_flips_zombie_off,
        test_assigned_staleness_unaffected_by_flag,
    ]
    p = f = 0
    for t in tests:
        try:
            t(); print(f"  ✅ {t.__name__}"); p += 1
        except Exception as e:
            print(f"  ❌ {t.__name__}: {type(e).__name__}: {e}"); f += 1
    print(f"\nPASS={p} FAIL={f} / {len(tests)}")
    sys.exit(0 if f == 0 else 1)
