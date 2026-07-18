"""D3-gold (werdykt Adriana 29.06 + OD-07: R6 35 dla KAŻDEGO, nigdy klasa kuriera)
— flip OFF `ENABLE_ETA_QUANTILE_R6_BAGCAP` (2026-07-18).

Pinuje semantykę gałęzi gold≤4 w feasibility_v2 (~:1141) na OBIE strony:
  ON  → bramka R6 na skalibrowanym p80 → odzysk (metryka `r6_gold4_gate_recovered`,
        zero violation dla odzyskanego ordera),
  OFF → surowa bramka 35 (violation zapisana, zero metryki) = stan po flipie,
  ON + tier std → ZERO odzysku (wyjątek był WYŁĄCZNIE dla gold — strażnik zakresu
        dla przyszłego usunięcia kodu gałęzi).
Harness lustrzany do test_o2_capz_reseq (mock OSRM hypot + bezpośrednie
check_feasibility_v2); kalibracja mockowana deterministycznie (raw>35 → 34.0).
"""
import math
from datetime import datetime, timedelta, timezone

import dispatch_v2.common as C
from dispatch_v2 import calib_maps
from dispatch_v2 import feasibility_v2 as F
from dispatch_v2 import osrm_client

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


class _O:
    def __init__(self, oid, status="assigned", picked_up_at=None, pickup_ready_at=None):
        self.order_id = oid
        self.status = status
        self.picked_up_at = picked_up_at
        self.pickup_ready_at = pickup_ready_at
        self.pickup_coords = (53.13, 23.16)
        self.delivery_coords = (53.14, 23.17)
        self.address_id = None
        self.order_type = None


def _mock_osrm(monkeypatch):
    monkeypatch.setattr(osrm_client, "table", lambda pa, pb: [
        [{"duration_s": math.hypot(a[0] - b[0], a[1] - b[1]) * 220 * 60,
          "osrm_fallback": False} for b in pb] for a in pa])
    monkeypatch.setattr(osrm_client, "route", lambda a, b: {
        "duration_s": math.hypot(a[0] - b[0], a[1] - b[1]) * 220 * 60})


def _run(monkeypatch, quantile_on, tier="gold"):
    _mock_osrm(monkeypatch)
    # deterministyczna kalibracja: każdy surowy czas → 34.0 (≤35 → odzysk możliwy)
    monkeypatch.setattr(calib_maps, "eta_quantile_calibrate",
                        lambda bt, now=None, quantile=None: 34.0)
    orig = C.flag

    def f(n, d=False):
        if n == "ENABLE_ETA_QUANTILE_R6_BAGCAP":
            return quantile_on
        return orig(n, d)
    monkeypatch.setattr(C, "flag", f)
    # carried A: odebrane 34 min temu → raw bag_time > 35 po dojeździe (hypot ~3-6 min)
    A = _O("A", status="picked_up", picked_up_at=NOW - timedelta(minutes=34))
    N = _O("N", status="assigned", pickup_ready_at=NOW)
    v, r, m, p = F.check_feasibility_v2(
        (53.13, 23.16), [A], N, now=NOW, courier_tier=tier,
        pickup_ready_at=N.pickup_ready_at,
        shift_start=NOW - timedelta(hours=3), shift_end=NOW + timedelta(hours=3))
    return v, r, m


def _violated_oids(m):
    return {oid for oid, _t in (m.get("r6_per_order_violations") or [])} | {
        oid for oid, _t in (m.get("r6_picked_up_violations") or [])}


def test_on_recovers_gold_bag_le4(monkeypatch):
    """ON: gold≤4 z raw>35 → bramka na p80 (34.0) → odzysk + metryka."""
    v, r, m = _run(monkeypatch, quantile_on=True, tier="gold")
    assert m.get("r6_max_bag_time_min", 0) > C.BAG_TIME_HARD_MAX_MIN, \
        "scenariusz musi mieć raw>35 (inaczej test nic nie pinuje)"
    assert m.get("r6_gold4_gate_recovered", 0) >= 1
    assert "A" not in _violated_oids(m)


def test_off_raw_gate_35_for_everyone(monkeypatch):
    """OFF (stan po flipie D3-gold): surowa bramka 35 — violation, zero metryki."""
    v, r, m = _run(monkeypatch, quantile_on=False, tier="gold")
    assert m.get("r6_max_bag_time_min", 0) > C.BAG_TIME_HARD_MAX_MIN
    assert "r6_gold4_gate_recovered" not in m
    assert "A" in _violated_oids(m)


def test_on_never_recovers_non_gold(monkeypatch):
    """Wyjątek dotyczył WYŁĄCZNIE gold — std nie odzyskuje nawet przy ON
    (strażnik zakresu na przyszłe usunięcie gałęzi; OD-07: nigdy klasa kuriera)."""
    v, r, m = _run(monkeypatch, quantile_on=True, tier="std")
    assert "r6_gold4_gate_recovered" not in m
    assert "A" in _violated_oids(m)


def test_const_matches_post_flip_state():
    """Po flipie flags.json=false == const-fallback False (koniec miny const≠json);
    flaga pozostaje w rejestrze ETAP4."""
    assert C.ENABLE_ETA_QUANTILE_R6_BAGCAP is False
    assert "ENABLE_ETA_QUANTILE_R6_BAGCAP" in C.ETAP4_DECISION_FLAGS
