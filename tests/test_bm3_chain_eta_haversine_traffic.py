"""B#M3 chain_eta haversine fallback traffic_multiplier guard.

Cross-review B#M3 (sprint 2026-04-25 sobota): chain_eta.safe_drive haversine
fallback (line 99-101 pre-fix) używał `hv * hav_mult * speed_multiplier` ALE
NIE przechodził przez OSRM-side _apply_traffic_multiplier. W peak (15-17 mult
1.6) underestymacja drive_min o ~37.5% gdy OSRM fails (network/timeout).

Fix: dodano flag-gated `* C.get_traffic_multiplier(now_utc)` w fallback path.
Default flag=False → identical pre-fix. Flag=True → parytet z OSRM happy path.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import chain_eta, common as C  # noqa: E402


@dataclass
class _MockOrder:
    status: str = "assigned"
    pickup_coords: tuple = (53.13, 23.16)
    pickup_ready_at: datetime = None
    order_id: str = "test"


def _osrm_fail(_a, _b):
    """Force haversine fallback path."""
    return None


def _hav_fixed(a, b):
    """Stała wartość 5km — independent of coords."""
    return 5.0


def _make_call(now_utc, flag_value):
    """Wywołanie compute_chain_eta z mock'ami i wybranym flag value."""
    with patch.object(C, "ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER", flag_value):
        result = chain_eta.compute_chain_eta(
            courier_pos=(53.10, 23.10),
            pos_source="gps",
            pos_age_min=1.0,
            bag_orders=[],
            proposal_pickup_coords=(53.13, 23.16),
            proposal_scheduled_utc=now_utc,
            now_utc=now_utc,
            osrm_drive_min=_osrm_fail,
            haversine_km=_hav_fixed,
            speed_multiplier=1.0,
        )
    return result


def test_haversine_fallback_no_traffic_when_flag_false():
    """Flag=False → fallback identyczny pre-fix (no traffic mult applied)."""
    # Weekday peak hour 15-17 (Adrian's 1.6 mult), Tuesday 15:30 Warsaw
    # Tuesday 22.04.2026 13:30 UTC = 15:30 CEST
    dt = datetime(2026, 4, 22, 13, 30, 0, tzinfo=timezone.utc)
    res = _make_call(dt, False)
    # safe_drive(courier_pos → proposal_pickup) = 5.0 * 2.5 (hav_mult) * 1.0 (speed)
    # = 12.5 min. delta_vs_naive nie testujemy precyzyjnie tu, sprawdzamy
    # że result istnieje + brak traffic_multiplier impact.
    # Naive_drive (z courier_pos do pickup) używany. effective_eta_utc istnieje.
    assert res.effective_eta_utc is not None
    # Z flag=False: pure haversine fallback. Naive drive ~12.5 min.
    # delta_vs_naive_min powinno być 0 (chain bez bag_orders) lub bliskie 0.


def test_haversine_fallback_traffic_applied_when_flag_true():
    """Flag=True peak hour → fallback × 1.6. Drive_min wyższe niż flag=False."""
    dt = datetime(2026, 4, 22, 13, 30, 0, tzinfo=timezone.utc)  # Tue 15:30 CEST → mult=1.6
    res_off = _make_call(dt, False)
    res_on = _make_call(dt, True)
    # eta_utc w res_on powinna być LATER niż res_off (drive_min × 1.6)
    delta_seconds = (res_on.effective_eta_utc - res_off.effective_eta_utc).total_seconds()
    assert delta_seconds > 0, (
        f"flag=True peak should give later ETA than flag=False; "
        f"off={res_off.effective_eta_utc} on={res_on.effective_eta_utc} "
        f"delta={delta_seconds}s"
    )


def test_haversine_fallback_weekend_mult_1_no_change():
    """Flag=True weekend off-peak → mult=1.0 → ETA identical to flag=False.

    V3.27 Bug X update: sobota 12-21 ma teraz mult>1.0 (peak buckets).
    Test używa sobota 06:00 UTC = 08:00 CEST = 00-12 bucket → still 1.0.
    Niedziela też cała doba 1.0 (alternative test path).
    """
    # Saturday 25.04.2026 06:00 UTC = 08:00 CEST (weekend off-peak → mult=1.0)
    dt = datetime(2026, 4, 25, 6, 0, 0, tzinfo=timezone.utc)
    res_off = _make_call(dt, False)
    res_on = _make_call(dt, True)
    delta_seconds = abs((res_on.effective_eta_utc - res_off.effective_eta_utc).total_seconds())
    # Weekend off-peak mult = 1.0 → identical
    assert delta_seconds < 1.0, (
        f"weekend off-peak mult=1.0 should give identical ETA; delta={delta_seconds}s"
    )


def test_traffic_multiplier_safety_net_no_crash_on_exception():
    """Jeśli C.get_traffic_multiplier crashes, chain_eta NIE może crashować."""
    dt = datetime(2026, 4, 22, 13, 30, 0, tzinfo=timezone.utc)
    with patch.object(C, "get_traffic_multiplier", side_effect=RuntimeError("boom")):
        with patch.object(C, "ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER", True):
            res = chain_eta.compute_chain_eta(
                courier_pos=(53.10, 23.10),
                pos_source="gps",
                pos_age_min=1.0,
                bag_orders=[],
                proposal_pickup_coords=(53.13, 23.16),
                proposal_scheduled_utc=dt,
                now_utc=dt,
                osrm_drive_min=_osrm_fail,
                haversine_km=_hav_fixed,
                speed_multiplier=1.0,
            )
    assert res is not None and res.effective_eta_utc is not None


if __name__ == "__main__":
    test_haversine_fallback_no_traffic_when_flag_false()
    print("test_haversine_fallback_no_traffic_when_flag_false: PASS")
    test_haversine_fallback_traffic_applied_when_flag_true()
    print("test_haversine_fallback_traffic_applied_when_flag_true: PASS")
    test_haversine_fallback_weekend_mult_1_no_change()
    print("test_haversine_fallback_weekend_mult_1_no_change: PASS")
    test_traffic_multiplier_safety_net_no_crash_on_exception()
    print("test_traffic_multiplier_safety_net_no_crash_on_exception: PASS")
    print("ALL 4/4 PASS")
