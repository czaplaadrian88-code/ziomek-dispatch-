"""OSRM-TABLE-03 (2026-06-12): per-cell cache table() — zero zmiany wyników.

Pokrycie:
  - _decompose_miss_rects: zimny cache (pełna macierz), wzorzec kuriera
    (wiersz+kolumna), rozproszone, pokrycie wszystkich missów.
  - table(): flaga OFF → legacy; ON: 1. call legacy+seed, 2. identyczny call
    = full hit (zero HTTP), ruch kuriera = dekompozycja (2 cienkie calle),
    wyniki IDENTYCZNE z fresh; fail dekompozycji → legacy; fail legacy →
    _table_fallback (stare failure semantics); izolacja mutacji (cache raw).
"""
import pytest

from dispatch_v2 import osrm_client as oc
from dispatch_v2.osrm_client import _decompose_miss_rects


# ─────────────────────────── _decompose_miss_rects ───────────────────────────

def _covers(rects, miss):
    cov = set()
    for rows, cols in rects:
        cov |= {(i, j) for i in rows for j in cols}
    return set(miss) <= cov


def test_decompose_cold_cache_full_matrix():
    n = 4
    miss = [(i, j) for i in range(n) for j in range(n)]
    rects = _decompose_miss_rects(miss, n, n)
    assert rects == [(list(range(n)), list(range(n)))]


def test_decompose_courier_row_and_column():
    # punkt 0 = nowy kurier: wiersz 0 pełny miss + kolumna 0 w pozostałych
    n = 5
    miss = [(0, j) for j in range(n)] + [(i, 0) for i in range(1, n)]
    rects = _decompose_miss_rects(miss, n, n)
    assert _covers(rects, miss)
    fetched = sum(len(r) * len(c) for r, c in rects)
    assert fetched == n + (n - 1)  # 1×N + (N-1)×1 — cienkie, nie N×N
    assert rects[0] == ([0], list(range(n)))
    assert rects[1] == ([1, 2, 3, 4], [0])


def test_decompose_scattered_covers_all():
    n = 6
    miss = [(1, 2), (3, 4), (3, 2)]
    rects = _decompose_miss_rects(miss, n, n)
    assert _covers(rects, miss)
    assert len(rects) <= 2


def test_decompose_empty():
    assert _decompose_miss_rects([], 3, 3) == []


# ────────────────────────────────── table() ──────────────────────────────────

A = (53.1300, 23.1600)
B = (53.1400, 23.1700)
C_ = (53.1500, 23.1800)
COURIER1 = (53.1200, 23.1500)
COURIER2 = (53.1250, 23.1550)


def _raw_cell(i, j):
    return {"duration_s": 60 * (i + j + 1), "duration_min": float(i + j + 1),
            "distance_m": 1000 * (i + j + 1), "distance_km": float(i + j + 1),
            "osrm_fallback": False}


@pytest.fixture()
def _cache_env(monkeypatch):
    """Flaga ON, czysty cache, deterministyczny _table_http z licznikiem."""
    monkeypatch.setattr(oc, "_common_flag",
                        lambda name, default=False: name == "ENABLE_OSRM_TABLE_CELL_CACHE")
    monkeypatch.setattr(oc, "_table_cell_cache", {})
    monkeypatch.setattr(oc, "_osrm_is_circuit_open", lambda: False)

    calls = []

    def fake_http(origins, destinations):
        calls.append((len(origins), len(destinations)))
        # wartości deterministyczne per para (hash z koordów)
        return [[{"duration_s": round((o[0] + d[0]) * 100, 1),
                  "duration_min": round((o[0] + d[0]) * 100 / 60, 1),
                  "distance_m": 1, "distance_km": 0.0, "osrm_fallback": False}
                 for d in destinations] for o in origins]

    monkeypatch.setattr(oc, "_table_http", fake_http)
    return calls


def test_flag_off_pure_legacy(monkeypatch, _cache_env):
    calls = _cache_env
    monkeypatch.setattr(oc, "_common_flag", lambda name, default=False: False)
    pts = [COURIER1, A, B]
    oc.table(pts, pts)
    oc.table(pts, pts)
    assert calls == [(3, 3), (3, 3)]  # zero cache przy flagi OFF
    assert oc._table_cell_cache == {}


def test_second_identical_call_full_hit(_cache_env):
    calls = _cache_env
    pts = [COURIER1, A, B]
    m1 = oc.table(pts, pts)
    assert calls == [(3, 3)]
    m2 = oc.table(pts, pts)
    assert calls == [(3, 3)]  # zero nowego HTTP — full hit
    # wyniki identyczne (zero zmiany wyników)
    for i in range(3):
        for j in range(3):
            assert m1[i][j]["duration_s"] == m2[i][j]["duration_s"]
            assert m1[i][j]["distance_m"] == m2[i][j]["distance_m"]


def test_courier_moved_thin_decomposition(_cache_env):
    calls = _cache_env
    pts1 = [COURIER1, A, B, C_]
    oc.table(pts1, pts1)
    assert calls == [(4, 4)]
    pts2 = [COURIER2, A, B, C_]  # tylko kurier się zmienił
    m2 = oc.table(pts2, pts2)
    # dekompozycja: wiersz kuriera (1×4) + kolumna kuriera (3×1)
    assert calls[1:] == [(1, 4), (3, 1)]
    # wyniki identyczne z fresh call
    fresh = [[{"duration_s": round((o[0] + d[0]) * 100, 1)} for d in pts2] for o in pts2]
    for i in range(4):
        for j in range(4):
            assert m2[i][j]["duration_s"] == fresh[i][j]["duration_s"]


def test_decompose_http_fail_falls_to_legacy(monkeypatch, _cache_env):
    calls = _cache_env
    pts1 = [COURIER1, A, B]
    oc.table(pts1, pts1)

    orig_http = oc._table_http
    fail_first = {"n": 0}

    def flaky_http(origins, destinations):
        if len(origins) < 3:  # cienkie calle dekompozycji padają
            fail_first["n"] += 1
            return None
        return orig_http(origins, destinations)

    monkeypatch.setattr(oc, "_table_http", flaky_http)
    pts2 = [COURIER2, A, B]
    m = oc.table(pts2, pts2)
    assert fail_first["n"] >= 1          # dekompozycja próbowana i padła
    assert m[0][1]["duration_s"] is not None  # legacy full call uratował wynik
    assert not m[0][1].get("osrm_fallback")


def test_all_http_fail_uses_table_fallback(monkeypatch, _cache_env):
    monkeypatch.setattr(oc, "_table_http", lambda o, d: None)
    pts = [COURIER1, A, B]
    m = oc.table(pts, pts)
    assert m[0][1].get("osrm_fallback") is True  # haversine fallback jak dotąd
    # fallback NIE zasila cache
    assert oc._table_cell_cache == {}


def test_cache_mutation_isolation(_cache_env):
    pts = [COURIER1, A, B]
    m1 = oc.table(pts, pts)
    m1[0][1]["duration_s"] = -999  # mutacja wyniku przez konsumenta
    m2 = oc.table(pts, pts)
    assert m2[0][1]["duration_s"] != -999  # cache trzyma RAW kopię
