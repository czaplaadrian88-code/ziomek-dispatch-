"""Kontrakt `pool_feasible` MUSI być trwały w rekordzie kalibracyjnym ETA (sesja 341).

DEFEKT (zmierzony 05.08.2026 na żywych danych): `outcomes_clean_shadow.jsonl` — korpus,
z którego suwak autonomii liczy zgodność wg reżimu obciążenia — ma `pool_feasible`
nie-null tylko w **8,8 %** rekordów (1800/20462). Przyczyna NIE jest w kolektorze:
`eta_calibration_logger.extract_row` trzyma tę liczbę w ręce (przekazuje ją do modelu
R3 jako cechę), ale **nie zapisuje jej do wiersza**. Kolektor musi ją więc doklejać
joinem po `oid` z `backfill_decisions_outcomes_v1.jsonl`, który jest co noc odtwarzany
od zera z okna ostatnich dni — więc pokrycie ginie razem z rotacją źródła.

Ten plik przypina kontrakt u ŹRÓDŁA: liczba, którą decyzja już zna, ma trafić do
trwałego rekordu w momencie jego powstania — bez joinu z krótkożyciowym korpusem.

Oracle negatywny: przed poprawką `test_pool_feasible_jest_zapisane_do_wiersza` jest RED.
"""
from datetime import datetime, timezone

from dispatch_v2 import eta_calibration_logger as L

_TS = datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)


def _index(oid, rec):
    """Kształt jak build_shadow_index(): oid -> [(ts_dt, rekord)]."""
    return {oid: [(_TS, rec)]}


def _shadow_rec(oid, *, pool_feasible_count, pool_total=None):
    """Rekord decyzji taki, jaki pisze shadow_dispatcher (pola wg shadow_decisions.jsonl)."""
    rec = {
        "ts": "2026-08-05T10:00:00+00:00",
        "order_id": oid,
        "verdict": "PROPOSE",
        "pool_feasible_count": pool_feasible_count,
        "best": {
            "courier_id": "509",
            # _bag_final: r6_bag_size + 1 (bag PRZED doliczeniem tego zlecenia)
            "r6_bag_size": 0,
            "r6_max_bag_time_min": 22.0,
            "plan": {
                "per_order_delivery_times": {oid: 31.0},
                "predicted_delivered_at": {oid: "2026-08-05T10:31:00+00:00"},
                "total_duration_min": 31.0,
                "strategy": "greedy",
            },
        },
    }
    if pool_total is not None:
        rec["auto_route_context"] = {"auto_route_pool_total": pool_total}
    return rec


def _sla_rec(oid):
    return {
        "order_id": oid,
        "courier_id": "509",
        "picked_up_at": "2026-08-05T10:00:00+00:00",
        "delivered_at": "2026-08-05T10:33:00+00:00",
        "delivery_time_minutes": 33.0,
        "restaurant": "Testowa",
        "delivery_address": "ul. Testowa 1",
        "sla_ok": True,
    }


def test_pool_feasible_jest_zapisane_do_wiersza():
    """RED przed fixem: liczba znana decyzji ginie i korpus musi ją doklejać joinem."""
    oid = "900001"
    row = L.extract_row(_sla_rec(oid), _index(oid, _shadow_rec(oid, pool_feasible_count=7)))
    assert row["pool_feasible"] == 7, (
        "pool_feasible MUSI być trwałe w rekordzie ETA — inaczej pokrycie korpusu "
        "zależy od joinu z backfillem odtwarzanym z krótkiego okna"
    )


def test_pool_total_tez_trwaly_gdy_kontekst_istnieje():
    oid = "900002"
    row = L.extract_row(
        _sla_rec(oid), _index(oid, _shadow_rec(oid, pool_feasible_count=3, pool_total=11))
    )
    assert row["pool_feasible"] == 3
    assert row["pool_total"] == 11


def test_brak_liczby_w_decyzji_daje_none_a_nie_wyjatek():
    """Fail-soft: stare rekordy bez pola nie mogą wywalić loggera ani udawać zera."""
    oid = "900003"
    row = L.extract_row(_sla_rec(oid), _index(oid, _shadow_rec(oid, pool_feasible_count=None)))
    assert row["pool_feasible"] is None
    assert row["pool_total"] is None


def test_klucze_istnieja_takze_bez_dopasowanej_decyzji():
    """Schemat jest STAŁY — konsument nie musi zgadywać, czy klucz w ogóle będzie."""
    row = L.extract_row(_sla_rec("900004"), {})
    assert "pool_feasible" in row and row["pool_feasible"] is None
    assert "pool_total" in row and row["pool_total"] is None


def test_zero_jest_zerem_a_nie_brakiem():
    """pool_feasible=0 (best-effort, brak wykonalnych) to INFORMACJA, nie brak danych."""
    oid = "900005"
    row = L.extract_row(_sla_rec(oid), _index(oid, _shadow_rec(oid, pool_feasible_count=0)))
    assert row["pool_feasible"] == 0


def test_istniejace_pola_nietkniete():
    """Zmiana jest ADDYTYWNA — żadne dotychczasowe pole nie zmienia wartości."""
    oid = "900006"
    row = L.extract_row(_sla_rec(oid), _index(oid, _shadow_rec(oid, pool_feasible_count=5)))
    assert row["oid"] == oid
    assert row["real_courier_id"] == "509"
    assert row["best_courier_id"] == "509"
    assert row["predicted_delivery_min"] == 31.0
    assert row["bag_size"] == 1
    assert row["verdict"] == "PROPOSE"
    assert row["sla_ok"] is True
