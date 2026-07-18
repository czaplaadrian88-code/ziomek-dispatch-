"""D3-gold — strażnik PO USUNIĘCIU kodu gałęzi gold≤4 (2026-07-20; flip OFF 18.07).

Werdykt D3 (Adrian 29.06) + OD-07: R6 = surowe 35 min dla KAŻDEGO kuriera,
nigdy klasa kuriera. Gałąź quantile-p80 (feasibility_v2, LIVE 14.06→OFF 18.07)
USUNIĘTA — te testy pinują, że:
  1. gold z raw>35 dostaje violation jak każdy (zero odzysku, zero metryki),
  2. std identycznie (parytet klas),
  3. nazwa flagi zniknęła z rejestru ETAP4 i z common (bez martwych rejestracji),
  4. nawet „włączona" flaga-widmo w flags.json NICZEGO nie zmienia (kod nie czyta).
Harness lustrzany do test_o2_capz_reseq (mock OSRM + check_feasibility_v2).
"""
import math
from datetime import datetime, timedelta, timezone

import dispatch_v2.common as C
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


def _run(monkeypatch, tier="gold", ghost_flag_on=False):
    _mock_osrm(monkeypatch)
    if ghost_flag_on:
        orig = C.flag

        def f(n, d=False):
            if n == "ENABLE_ETA_QUANTILE_R6_BAGCAP":
                return True  # flaga-widmo: kod jej NIE czyta → zero efektu
            return orig(n, d)
        monkeypatch.setattr(C, "flag", f)
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


def test_gold_raw_gate_35_no_recovery(monkeypatch):
    """gold z raw>35 → violation jak każdy; metryka odzysku NIE istnieje."""
    v, r, m = _run(monkeypatch, tier="gold")
    assert m.get("r6_max_bag_time_min", 0) > C.BAG_TIME_HARD_MAX_MIN, \
        "scenariusz musi mieć raw>35 (inaczej test nic nie pinuje)"
    assert "r6_gold4_gate_recovered" not in m
    assert "A" in _violated_oids(m)


def test_std_identical_to_gold(monkeypatch):
    """Parytet klas: std dostaje identyczną bramkę (OD-07: nigdy klasa kuriera)."""
    v_g, _, m_g = _run(monkeypatch, tier="gold")
    v_s, _, m_s = _run(monkeypatch, tier="std")
    assert _violated_oids(m_g) == _violated_oids(m_s) == {"A"}


def test_ghost_flag_in_flags_json_is_inert(monkeypatch):
    """Nawet gdyby klucz wrócił do flags.json=true — kod go nie czyta, zero efektu
    (strażnik przeciw cichej reanimacji wyjątku)."""
    v, r, m = _run(monkeypatch, tier="gold", ghost_flag_on=True)
    assert "r6_gold4_gate_recovered" not in m
    assert "A" in _violated_oids(m)


def test_flag_fully_deregistered():
    """Nazwa zniknęła z rejestru ETAP4 i z common — bez martwych rejestracji."""
    assert "ENABLE_ETA_QUANTILE_R6_BAGCAP" not in C.ETAP4_DECISION_FLAGS
    assert not hasattr(C, "ENABLE_ETA_QUANTILE_R6_BAGCAP")
