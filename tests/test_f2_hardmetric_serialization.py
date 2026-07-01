"""F2 (audyt Ziomka 2026-06-28) — serializacja metryk HARD do shadow_decisions.

post_shift_overrun_* (P2, WIODACY term selekcji best_effort; replay widzial 0 ->
ETAP-5 flipa ENABLE_POST_SHIFT_OVERRUN_PENALTY nie dalo sie policzyc) + end_of_day_
salvage* (LIVE relaksacja HARD konca zmiany BEZ sladu) musza trafiac do logu.

Mechanizm (od L1.1 2026-07-01) = deny-lista _METRICS_EXCLUDE -> wspolny
_propagate_prefixed_metrics (kazdy klucz metrics serializowany, chyba ze jawnie
wykluczony z powodem), wolany w OBU lokalizacjach serializera: LOCATION A
(_serialize_candidate) + LOCATION B (_serialize_result best). Test helpera =
pokrycie twin A+B (oba ta sama sciezka). Historycznie: _AUTO_PROP_PREFIXES.
"""
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import shadow_dispatcher as SD


def test_hard_metric_keys_reach_ledger():
    # L1.1 (2026-07-01): allowlist prefiksów zastąpiona deny-listą — klucze
    # HARD trafiają do ledgera z konstrukcji (nie przez rejestrację prefiksu).
    base = {}
    SD._propagate_prefixed_metrics(base, {
        "post_shift_overrun_min": 1.0,
        "end_of_day_salvage": True,
    })
    assert "post_shift_overrun_min" in base
    assert "end_of_day_salvage" in base


def test_post_shift_overrun_propagated():
    base = {}
    SD._propagate_prefixed_metrics(base, {
        "post_shift_overrun_min": 7.5,
        "post_shift_overrun_penalty": -22.0,
        "unrelated_metric_xyz": 1,
    })
    assert base.get("post_shift_overrun_min") == 7.5
    assert base.get("post_shift_overrun_penalty") == -22.0
    # L1.1: NOWY kontrakt — każdy klucz metrics serializowany (deny-list),
    # więc „obcy" klucz też trafia do ledgera (koniec cichych dziur).
    assert base.get("unrelated_metric_xyz") == 1


def test_end_of_day_salvage_propagated():
    base = {}
    SD._propagate_prefixed_metrics(base, {
        "end_of_day_salvage": True,
        "end_of_day_salvage_close_iso": "2026-06-28T23:00:00+02:00",
        "end_of_day_salvage_pickup_excess_min": 3.2,
        "end_of_day_salvage_dropoff_excess_min": 5.1,
    })
    assert base.get("end_of_day_salvage") is True
    assert base.get("end_of_day_salvage_pickup_excess_min") == 3.2
    assert base.get("end_of_day_salvage_dropoff_excess_min") == 5.1


def test_existing_explicit_field_not_overwritten():
    # _propagate skip gdy klucz juz w base (l.258) — nie nadpisuje explicit pola serializera
    base = {"post_shift_overrun_penalty": -99.0}
    SD._propagate_prefixed_metrics(base, {"post_shift_overrun_penalty": -1.0})
    assert base["post_shift_overrun_penalty"] == -99.0


def test_both_serializer_locations_use_shared_helper():
    """Twin A+B: oba miejsca serializacji wolaja ten sam _propagate_prefixed_metrics,
    wiec dodanie prefiksu dziala identycznie dla alternatyw (A) i best (B)."""
    import inspect
    src = inspect.getsource(SD)
    # A: _serialize_candidate konczy sie propagacja; B: _serialize_result best.
    assert src.count("_propagate_prefixed_metrics(") >= 3  # 1 def + >=2 call-site (A, B)
