#!/usr/bin/env python3
"""Testy [C1] prep_bias_build — czysty sygnał kuchni, shrinkage, znak biasu.

Krytyczne kontrakty:
  * przypadki kontaminowane (kurier spóźniony / brak GPS) MUSZĄ być wykluczone,
  * shrinkage aktywny dla n < próg,
  * znak biasu poprawny: kuchnia wolniejsza niż deklaruje -> bias dodatni.
"""

import json
import os
import sys

import pytest

# moduł leży w tools/ obok testów (tests/ i tools/ to rodzeństwo)
_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.join(os.path.dirname(_HERE), "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

import prep_bias_build as pbb  # noqa: E402


def _rec(rest, declared, arrived, picked, basis, arr_src, prep_bias, ts=None):
    return {
        "ts": ts or "2026-06-15T10:00:00+00:00",
        "order_id": "x",
        "restaurant": rest,
        "courier_id": "1",
        "order_type": "elastic",
        "declared_ready_iso": declared,
        "arrived_at_iso": arrived,
        "picked_up_at_iso": picked,
        "arrival_source": arr_src,
        "wait_min": 0.0,
        "prep_bias_min": prep_bias,
        "ready_basis": basis,
    }


# ---------------------------------------------------------------------------
# Rozróżnienie "kurier czekał" vs "kurier spóźniony / brak GPS"
# ---------------------------------------------------------------------------

def test_waited_is_clean_signal():
    # kurier dotarł (real GPS status4) i czekał -> czysty sygnał
    r = _rec("A", "2026-06-15T10:00:00+00:00", "2026-06-15T09:58:00+00:00",
             "2026-06-15T10:05:00+00:00", "waited", "status4", 5.0)
    assert pbb.is_clean_signal(r) is True


def test_no_arrival_signal_excluded():
    # commit_fallback / brak dotarcia GPS -> kurier mógł być spóźniony -> WYKLUCZ
    r = _rec("A", "2026-06-15T10:00:00+00:00", None,
             "2026-06-15T10:08:00+00:00", "no_arrival_signal", "commit_fallback", 8.0)
    assert pbb.is_clean_signal(r) is False


def test_ready_by_arrival_excluded():
    # kuchnia gotowa zanim kurier dojechał -> mierzy kuriera, nie kuchnię -> WYKLUCZ
    r = _rec("A", "2026-06-15T10:00:00+00:00", "2026-06-15T09:57:00+00:00",
             "2026-06-15T09:58:00+00:00", "ready_by_arrival", "status4", -2.0)
    assert pbb.is_clean_signal(r) is False


def test_waited_but_commit_fallback_excluded():
    # gdyby basis='waited' ale brak realnego dotarcia/commit_fallback -> nie ufamy
    r = _rec("A", "2026-06-15T10:00:00+00:00", None,
             "2026-06-15T10:05:00+00:00", "waited", "commit_fallback", 5.0)
    assert pbb.is_clean_signal(r) is False


def test_extract_clean_drops_contaminated():
    records = [
        _rec("A", "2026-06-15T10:00:00+00:00", "2026-06-15T09:58:00+00:00",
             "2026-06-15T10:05:00+00:00", "waited", "status4", 5.0),
        _rec("A", "2026-06-15T11:00:00+00:00", None,
             "2026-06-15T11:09:00+00:00", "no_arrival_signal", "commit_fallback", 9.0),
        _rec("A", "2026-06-15T12:00:00+00:00", "2026-06-15T11:57:00+00:00",
             "2026-06-15T11:58:00+00:00", "ready_by_arrival", "status4", -2.0),
    ]
    by_rest, n_clean, excl = pbb.extract_clean(records)
    assert n_clean == 1
    assert by_rest["A"][0][1] == 5.0  # tylko czysty rekord
    assert excl["no_arrival_signal"] == 1
    assert excl["ready_by_arrival"] == 1


# ---------------------------------------------------------------------------
# Znak biasu
# ---------------------------------------------------------------------------

def test_positive_bias_when_kitchen_slower():
    # kuchnia wolniejsza niż deklaruje -> pickup późniejszy -> bias DODATNI
    records = [
        _rec("Slow", "2026-06-15T10:00:00+00:00", "2026-06-15T09:55:00+00:00",
             "2026-06-15T10:08:00+00:00", "waited", "status4", 8.0),
    ] * 10  # n>=threshold by uniknąć shrinkage
    payload = pbb.build_from_records(records)
    assert payload["Slow"]["bias_median_min"] > 0
    assert payload["Slow"]["shrunk"] is False


def test_negative_bias_when_kitchen_faster():
    # kuchnia szybsza/punktualna, kurier czeka krótko, bias może być ujemny
    records = [
        _rec("Fast", "2026-06-15T10:00:00+00:00", "2026-06-15T09:50:00+00:00",
             "2026-06-15T09:58:00+00:00", "waited", "status4", -2.0),
    ] * 10
    payload = pbb.build_from_records(records)
    assert payload["Fast"]["bias_median_min"] < 0


# ---------------------------------------------------------------------------
# Shrinkage
# ---------------------------------------------------------------------------

def test_shrinkage_active_for_small_sample():
    # mała restauracja (n<threshold) z ekstremalnym biasem -> ściągnięta ku globalnej
    big = [
        _rec("Big", "2026-06-15T10:00:00+00:00", "2026-06-15T09:58:00+00:00",
             "2026-06-15T10:02:00+00:00", "waited", "status4", 2.0)
    ] * 30  # ustala globalną medianę ~2.0
    small = [
        _rec("Tiny", "2026-06-15T10:00:00+00:00", "2026-06-15T09:50:00+00:00",
             "2026-06-15T10:30:00+00:00", "waited", "status4", 30.0)
    ] * 2  # n=2 < threshold, surowa mediana 30
    payload = pbb.build_from_records(big + small)
    assert payload["Tiny"]["shrunk"] is True
    assert payload["Big"]["shrunk"] is False
    # ściągnięta wartość MUSI być między globalną (~2) a surową (30)
    assert payload["Tiny"]["bias_median_min"] < 30.0
    assert payload["Tiny"]["bias_median_min"] > payload["Big"]["bias_median_min"]


def test_no_shrinkage_at_or_above_threshold():
    n = pbb.SHRINK_THRESHOLD
    records = [
        _rec("Exact", "2026-06-15T10:00:00+00:00", "2026-06-15T09:58:00+00:00",
             "2026-06-15T10:07:00+00:00", "waited", "status4", 7.0)
    ] * n
    payload = pbb.build_from_records(records)
    assert payload["Exact"]["shrunk"] is False
    assert payload["Exact"]["bias_median_min"] == 7.0


# ---------------------------------------------------------------------------
# Struktura wyjścia / EWMA / fail-soft
# ---------------------------------------------------------------------------

def test_output_schema_keys():
    records = [
        _rec("A", "2026-06-15T10:00:00+00:00", "2026-06-15T09:58:00+00:00",
             "2026-06-15T10:05:00+00:00", "waited", "status4", 5.0)
    ] * 9
    payload = pbb.build_from_records(records)
    assert "_global" in payload
    g = payload["_global"]
    for k in ("bias_median_min", "bias_p80_min", "n_clean", "n_total",
              "n_restaurants", "n_excluded_contaminated", "shrink_threshold",
              "ewma_halflife_days"):
        assert k in g, k
    entry = payload["A"]
    for k in ("bias_median_min", "bias_p80_min", "ewma_min", "n", "shrunk"):
        assert k in entry, k


def test_ewma_recent_weighted_more():
    # stare rekordy z biasem 0, świeże z biasem 20 -> EWMA bliżej 20 niż mediana
    old = [
        _rec("R", "2026-05-01T10:00:00+00:00", "2026-05-01T09:58:00+00:00",
             "2026-05-01T10:00:00+00:00", "waited", "status4", 0.0,
             ts="2026-05-01T10:00:00+00:00")
    ] * 5
    new = [
        _rec("R", "2026-06-20T10:00:00+00:00", "2026-06-20T09:58:00+00:00",
             "2026-06-20T10:20:00+00:00", "waited", "status4", 20.0,
             ts="2026-06-20T10:00:00+00:00")
    ] * 5
    payload = pbb.build_from_records(old + new)
    # n=10>=threshold (brak shrinkage), surowa mediana=10, EWMA przesunięta ku 20
    assert payload["R"]["bias_median_min"] == 10.0
    assert payload["R"]["ewma_min"] > 10.0


def test_empty_and_bad_lines_failsoft():
    # brak danych -> pusta tabela + _global, bez wyjątku
    payload = pbb.build_from_records([])
    assert payload["_global"]["n_clean"] == 0
    assert payload["_global"]["n_restaurants"] == 0
    # uszkodzona linia w pliku nie wywala loadera
    tmp = os.path.join(_HERE, "_tmp_bad.jsonl")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write('{"restaurant": "A", "ready_basis": "waited", "arrived_at_iso": "2026-06-15T09:58:00+00:00", "arrival_source": "status4", "prep_bias_min": 5.0, "ts": "2026-06-15T10:00:00+00:00"}\n')
        f.write("NOT JSON\n")
        f.write("\n")
    try:
        recs, n_total, n_bad = pbb.load_records(tmp)
        assert n_total == 2  # 2 niepuste linie
        assert n_bad == 1
        assert len(recs) == 1
    finally:
        os.remove(tmp)


def test_real_log_smoke_if_present():
    """Smoke na realnym logu jeśli istnieje — nie zapisuje pliku."""
    if not os.path.exists(pbb.DEFAULT_LOG):
        pytest.skip("brak realnego ready_at_log.jsonl")
    payload = pbb.build(write=False)
    g = payload["_global"]
    assert g["n_total"] > 0
    assert g["n_clean"] >= 0
    # czystych nigdy więcej niż total
    assert g["n_clean"] <= g["n_total"]
    # każdy wpis ma poprawny n>=1
    for r, v in payload.items():
        if r == "_global":
            continue
        assert v["n"] >= 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
