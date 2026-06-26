"""Testy time-of-day aware G2a-KOORD + min-sample guard w canary monitorze objm-lexr6.

Dowodzą: (1) krzywa per-godzina zmienia werdykt ON≠OFF (poranny off-peak GO zamiast STOP),
(2) peak nadal STOP gdy realny wzrost, (3) guard małej próby degraduje gate'y statystyczne
ale NIE G1-błędy, (4) _expected_koord_tod poprawnie waży okno straddlujące klif,
(5) brak krzywej = zachowanie flat (backward-compat). READ-ONLY tool, zero wpływu na silnik.
"""
from datetime import datetime, timezone
from dispatch_v2.tools import objm_lexr6_canary_monitor as M


def _dt(h, m=0):
    return datetime(2026, 6, 26, h, m, tzinfo=timezone.utc)


# Krzywa SELECT-OFF: poranek wysoki KOORD, peak niski (jak realne dane)
CURVE = {
    "6": 66.7, "7": 41.7, "8": 12.1, "9": 13.5,
    "13": 4.2, "14": 6.4, "15": 3.1, "16": 1.9, "17": 0.9,
}
NVOL = {
    "6": 6, "7": 36, "8": 66, "9": 111,
    "13": 215, "14": 157, "15": 193, "16": 211, "17": 219,
}
BASE_TOD = {"koord_pct": 5.8, "ack_alert_pct": 89.13, "lat_p95": 1892.4,
            "koord_by_hour": CURVE, "n_by_hour": NVOL, "tod_cutoff": "2026-06-26T07:30:00+00:00"}
BASE_FLAT = {"koord_pct": 5.8, "ack_alert_pct": 89.13, "lat_p95": 1892.4}

LOG0 = {"reorders": 4, "errors": 0}
FLAGS = {"select_on": True, "shadow_on": False}


def _cur(n, koord_pct, **kw):
    d = {"n": n, "koord_pct": koord_pct, "ack_alert_pct": 71.0, "auto_pct": 28.0,
         "lat_p50": 600.0, "lat_p95": 1030.0}
    d.update(kw)
    return d


def _g(gates, name):
    return next(x for x in gates if x[0] == name)


def test_expected_koord_straddle_between_buckets():
    # okno 06:33-09:33 obejmuje klif 07(41.7%)→08(12.1%); oczekiwane musi leżeć między
    exp = M._expected_koord_tod(BASE_TOD, _dt(6, 33), _dt(9, 33))
    assert exp is not None
    assert 15.0 < exp < 25.0, exp  # ~19-20% wg wolumenu


def test_morning_go_with_tod_curve():
    # poranne okno, KOORD 19% — NORMA off-peak → GO (n duże by ominąć min-n guard)
    g = M.gates(_cur(200, 19.0), LOG0, FLAGS, BASE_TOD, _dt(6, 33), _dt(9, 33))
    assert _g(g, "G2a-KOORD")[1] == "GO", _g(g, "G2a-KOORD")


def test_morning_stop_under_flat_baseline_ON_neq_OFF():
    # te same dane, ale BEZ krzywej (flat 5.8%) → STOP. Dowód że krzywa zmienia werdykt.
    g = M.gates(_cur(200, 19.0), LOG0, FLAGS, BASE_FLAT, _dt(6, 33), _dt(9, 33))
    assert _g(g, "G2a-KOORD")[1] == "STOP", _g(g, "G2a-KOORD")


def test_peak_still_stops_on_real_regression():
    # peak (13-17), oczek. ~3-4%, ale KOORD 15% → realny wzrost > 5pp → STOP zachowany
    g = M.gates(_cur(600, 15.0), LOG0, FLAGS, BASE_TOD, _dt(13, 0), _dt(16, 0))
    assert _g(g, "G2a-KOORD")[1] == "STOP", _g(g, "G2a-KOORD")


def test_peak_go_when_within_norm():
    # peak, KOORD 6% ~ norma peaku → GO
    g = M.gates(_cur(600, 6.0), LOG0, FLAGS, BASE_TOD, _dt(13, 0), _dt(16, 0))
    assert _g(g, "G2a-KOORD")[1] == "GO", _g(g, "G2a-KOORD")


def test_min_n_guard_downgrades_stats_to_info():
    # mała próba (n=20<30): nawet flat-STOP KOORD → INFO; brak STOP/WARN w gate'ach statystycznych
    g = M.gates(_cur(20, 40.0), LOG0, FLAGS, BASE_FLAT, _dt(6, 33), _dt(9, 33))
    assert _g(g, "G2a-KOORD")[1] == "INFO", _g(g, "G2a-KOORD")
    assert "za mała próba" in _g(g, "G2a-KOORD")[2]


def test_min_n_guard_does_not_suppress_real_errors():
    # mała próba, ale pick-failed>0 → G1-błędy MUSI zostać STOP (realny błąd nigdy nie wyciszany)
    g = M.gates(_cur(5, 0.0), {"reorders": 0, "errors": 2}, FLAGS, BASE_TOD, _dt(8, 0), _dt(9, 0))
    assert _g(g, "G1-błędy")[1] == "STOP", _g(g, "G1-błędy")


def test_backward_compat_no_curve_uses_flat():
    # brak krzywej + duże n → ścieżka flat działa jak przedtem (STOP gdy >baseline+5pp)
    g = M.gates(_cur(300, 12.0), LOG0, FLAGS, BASE_FLAT, _dt(13, 0), _dt(16, 0))
    assert _g(g, "G2a-KOORD")[1] == "STOP"
    assert "flat — brak krzywej tod" in _g(g, "G2a-KOORD")[2]


def test_no_baseline_is_info():
    g = M.gates(_cur(300, 12.0), LOG0, FLAGS, None, _dt(13, 0), _dt(16, 0))
    assert _g(g, "G2a-KOORD")[1] == "INFO"
