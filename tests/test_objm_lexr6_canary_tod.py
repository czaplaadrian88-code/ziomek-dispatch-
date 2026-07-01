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
    # domyślnie brak early_bird: sel == raw, n_sel == n (back-compat dla starych asercji)
    d = {"n": n, "koord_pct": koord_pct, "koord_eb": 0, "n_sel": n,
         "koord_sel": None, "koord_pct_sel": koord_pct,
         "ack_alert_pct": 71.0, "auto_pct": 28.0, "lat_p50": 600.0, "lat_p95": 1030.0}
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


# --- early_bird out of G2a -------------------------------------------------

def test_early_bird_excluded_keys_g2a_off_sel_not_raw():
    # raw KOORD 21% (STOP-owe vs oczek.~19) ale po wykluczeniu early_bird sel=8% → GO.
    cur = _cur(40, 21.0, koord_eb=5, n_sel=35, koord_pct_sel=8.0)
    g = M.gates(cur, LOG0, FLAGS, BASE_TOD, _dt(6, 33), _dt(9, 33))
    assert _g(g, "G2a-KOORD")[1] == "GO", _g(g, "G2a-KOORD")
    assert "excl early_bird" in _g(g, "G2a-KOORD")[2]


def test_real_koord_still_stops_after_eb_exclusion():
    # sel (po wykluczeniu eb) nadal wysoki vs norma pory dnia → STOP zachowany (realna regresja)
    cur = _cur(60, 25.0, koord_eb=2, n_sel=58, koord_pct_sel=25.0)
    g = M.gates(cur, LOG0, FLAGS, BASE_TOD, _dt(6, 33), _dt(9, 33))
    assert _g(g, "G2a-KOORD")[1] == "STOP", _g(g, "G2a-KOORD")


def test_g2a_min_n_uses_n_sel_not_total():
    # total n=50 (≥30) ale n_sel=10 (40 early_bird) → G2a INFO (próba selektor-istotna za mała)
    cur = _cur(50, 30.0, koord_eb=40, n_sel=10, koord_pct_sel=0.0)
    g = M.gates(cur, LOG0, FLAGS, BASE_TOD, _dt(6, 33), _dt(9, 33))
    assert _g(g, "G2a-KOORD")[1] == "INFO"
    assert "selektor-istotna" in _g(g, "G2a-KOORD")[2]


def test_shadow_metrics_excludes_early_bird_end_to_end(tmp_path, monkeypatch):
    # 10 decyzji: 6 PROPOSE + 4 KOORD (3 early_bird + 1 no_candidate). sel: 1/7.
    lines = []
    for i in range(6):
        lines.append({"ts": f"2026-06-26T08:0{i}:00+00:00", "verdict": "PROPOSE", "auto_route": "AUTO"})
    for i in range(3):
        lines.append({"ts": f"2026-06-26T08:1{i}:00+00:00", "verdict": "KOORD", "reason": f"early_bird ({60+i} min ahead)"})
    lines.append({"ts": "2026-06-26T08:20:00+00:00", "verdict": "KOORD", "reason": "no_candidate"})
    p = tmp_path / "shadow_decisions.jsonl"
    p.write_text("\n".join(__import__("json").dumps(x) for x in lines) + "\n", encoding="utf-8")
    from dispatch_v2.tools import ledger_io
    monkeypatch.setitem(ledger_io.LEDGER, "shadow", str(p))  # L1.2: kanon odczytu
    m = M.shadow_metrics(datetime(2026, 6, 26, 0, 0, tzinfo=timezone.utc))
    assert m["n"] == 10
    assert m["koord_eb"] == 3
    assert m["koord_sel"] == 1 and m["n_sel"] == 7
    assert m["koord_pct_sel"] == round(100.0 / 7, 2)
    assert m["koord_pct"] == 40.0  # raw zachowany dla transparencji


# --- G2c reorder PER-DECYZJA (#6a audyt 28.06 — match reorder→ts proposala ±5s, NIE all-tick) ---

def test_g2c_per_decision_counts_matched_decisions_not_alltick():
    # 30 decyzji w t=13:00; 6 z nich ma linię reorder TEGO orderu w ±5s → per-decyzja 20% GO.
    T = _dt(13, 0)
    decev = [(f"o{i}", T) for i in range(30)]
    reoev = {f"o{i}": [T] for i in range(6)}      # 6 decyzji z matchem w tym samym ticku
    cur = _cur(30, 5.0, n_orders=30, shadow_oids=set(f"o{i}" for i in range(30)), decision_events=decev)
    log = {"reorders": 15, "errors": 0,
           "reorder_oids": set(f"o{i}" for i in range(6)), "reorder_events": reoev}
    gc = _g(M.gates(cur, log, FLAGS, BASE_TOD, _dt(13, 0), _dt(16, 0)), "G2c-reorder")
    assert "per-decyzja 20.0%" in gc[2] and "6/30" in gc[2], gc
    assert gc[1] == "GO"  # 20% w paśmie 5-25


def test_g2c_per_decision_ignores_reorder_on_other_tick():
    # KLUCZOWY FIX: order reorderowany w INNYM ticku (poza ±5s od decyzji) NIE liczy per-decyzja,
    # choć all-tick by go policzył. 35 decyzji 13:00; o0..o2 reorder w oknie (match), o3..o9 reorder
    # 60s później (inny tick) → NIE liczone. per-decyzja 3/35=8.6% GO; all-tick 10/35=28.6% (diagn.).
    T = _dt(13, 0)
    decev = [(f"o{i}", T) for i in range(35)]
    reoev = {f"o{i}": [T] for i in range(3)}                       # 3 w oknie
    reoev.update({f"o{i}": [_dt(13, 1)] for i in range(3, 10)})    # 7 poza oknem (+60s)
    cur = _cur(35, 0.0, n_orders=35, shadow_oids=set(f"o{i}" for i in range(35)), decision_events=decev)
    log = {"reorders": 10, "errors": 0,
           "reorder_oids": set(f"o{i}" for i in range(10)), "reorder_events": reoev}
    gc = _g(M.gates(cur, log, FLAGS, BASE_TOD, _dt(13, 0), _dt(16, 0)), "G2c-reorder")
    assert "3/35" in gc[2], gc                 # tylko 3 zmatchowane per-decyzja
    assert "all-tick 10/35" in gc[2]           # diagnostyka pokazuje zawyżone all-tick
    assert gc[1] == "GO"                        # 8.6% w paśmie 5-25 (a NIE WARN jak 28.6% all-tick)


def test_g2c_high_per_decision_rate_warns():
    # realnie wysoka stopa per-decyzja (16/40=40% w ±5s) nadal WARN — sygnał prawdziwy, nie artefakt
    T = _dt(13, 0)
    decev = [(f"o{i}", T) for i in range(40)]
    reoev = {f"o{i}": [T] for i in range(16)}
    cur = _cur(40, 0.0, n_orders=40, shadow_oids=set(f"o{i}" for i in range(40)), decision_events=decev)
    log = {"reorders": 50, "errors": 0,
           "reorder_oids": set(f"o{i}" for i in range(16)), "reorder_events": reoev}
    gc = _g(M.gates(cur, log, FLAGS, BASE_TOD, _dt(13, 0), _dt(16, 0)), "G2c-reorder")
    assert gc[1] == "WARN" and "per-decyzja 40.0%" in gc[2]


def test_compute_tod_curve_excludes_early_bird(tmp_path, monkeypatch):
    # godzina 8: 10 decyzji, 5 KOORD z czego 4 early_bird → sel: koord 1 / n_sel 6
    import json as _j
    lines = [{"ts": "2026-06-25T08:00:00+00:00", "verdict": "PROPOSE"} for _ in range(5)]
    lines += [{"ts": "2026-06-25T08:10:00+00:00", "verdict": "KOORD", "reason": "early_bird (70 min ahead)"} for _ in range(4)]
    lines += [{"ts": "2026-06-25T08:20:00+00:00", "verdict": "KOORD", "reason": "no_candidate"}]
    p = tmp_path / "shadow_decisions.jsonl"
    p.write_text("\n".join(_j.dumps(x) for x in lines) + "\n", encoding="utf-8")
    from dispatch_v2.tools import ledger_io
    monkeypatch.setitem(ledger_io.LEDGER, "shadow", str(p))  # L1.2: kanon odczytu
    kbh, nbh = M.compute_tod_curve(7, datetime(2026, 6, 26, 0, 0, tzinfo=timezone.utc))
    assert nbh["8"] == 6  # 10 - 4 early_bird
    assert kbh["8"] == round(100.0 / 6, 2)  # 1 selektor-istotny KOORD / 6
