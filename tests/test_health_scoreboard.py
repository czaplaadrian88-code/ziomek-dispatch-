"""Testy agregatora tablicy zdrowia (Sprint E) — READ-ONLY health_scoreboard.

Import STANDALONE przez ścieżkę względną od pliku testu (NIE `dispatch_v2.tools.*`):
moduł jest nowy i do merge'u żyje tylko w worktree, a `tests/conftest.py` pinuje
`_SCRIPTS_ROOT` na KANON → import pakietowy wziąłby stary kanon / rzucił ImportError
(protokół C12e/g). Moduł jest czystym stdlib, więc spec_from_file_location wystarcza.

Testy NIGDY nie piszą do prod `dispatch_state/` — wyłącznie tmp_path (asercja anty-PROD).
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_MOD_PATH = Path(__file__).resolve().parents[1] / "tools" / "health_scoreboard.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("_hsb_under_test", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    # rejestracja PRZED exec, sprzątanie w finalizatorze fixture
    sys.modules["_hsb_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def hsb():
    mod = _load_module()
    try:
        yield mod
    finally:
        sys.modules.pop("_hsb_under_test", None)


# ─────────────────────────────────────────────────────────────────────────────
# Pomocnicze budowanie fixtur jsonl.
# ─────────────────────────────────────────────────────────────────────────────
def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ── slo_burn: matematyka budżetu ────────────────────────────────────────────
def test_slo_burn_ge(hsb):
    # on-time 81.6% przy celu 80% → budżet 20, zjedzone 18.4 → burn 92%
    burn, color = hsb.slo_burn(81.6, 80.0, "ge")
    assert round(burn, 1) == 92.0
    assert color == hsb.YELLOW
    # on-time 95% → burn 25% → zielony
    burn, color = hsb.slo_burn(95.0, 80.0, "ge")
    assert round(burn, 0) == 25.0
    assert color == hsb.GREEN
    # on-time 78% < cel → burn 110% → czerwony (budżet przekroczony)
    burn, color = hsb.slo_burn(78.0, 80.0, "ge")
    assert burn > 100.0
    assert color == hsb.RED


def test_slo_burn_le(hsb):
    # KOORD 8% przy pułapie 10 → burn 80% → 🟡
    burn, color = hsb.slo_burn(8.0, 10.0, "le")
    assert round(burn, 0) == 80.0
    assert color == hsb.YELLOW
    # 5% → 50% → 🟢
    burn, color = hsb.slo_burn(5.0, 10.0, "le")
    assert color == hsb.GREEN
    # 12% → 120% → 🔴
    burn, color = hsb.slo_burn(12.0, 10.0, "le")
    assert color == hsb.RED


def test_slo_burn_zero(hsb):
    burn, color = hsb.slo_burn(0, 0, "zero")
    assert burn == 0.0 and color == hsb.GREEN
    burn, color = hsb.slo_burn(3, 0, "zero")
    assert color == hsb.RED  # naruszenie twardego inwariantu


def test_slo_burn_none_is_grey(hsb):
    burn, color = hsb.slo_burn(None, 80.0, "ge")
    assert burn is None and color == hsb.GREY


# ── parse_ts / percentile ────────────────────────────────────────────────────
def test_parse_ts(hsb):
    a = hsb.parse_ts("2026-07-08T05:20:47.117228+00:00")
    assert a is not None and a.tzinfo is not None
    # naive → traktowane jako UTC
    b = hsb.parse_ts("2026-07-07T22:30:03.4")
    assert b is not None and b.utcoffset().total_seconds() == 0
    # offset inny niż UTC → skonwertowany do UTC
    c = hsb.parse_ts("2026-07-08T03:17:17+02:00")
    assert c.hour == 1  # 03:17 +02:00 == 01:17 UTC
    assert hsb.parse_ts(None) is None
    assert hsb.parse_ts("nonsense") is None


def test_percentile(hsb):
    vals = list(range(1, 101))  # 1..100 posortowane
    assert hsb._percentile(vals, 0.50) in (50, 51)
    assert hsb._percentile(vals, 0.95) in (95, 96)
    assert hsb._percentile([], 0.5) is None


# ── load_shadow ──────────────────────────────────────────────────────────────
def test_load_shadow_counts(hsb, tmp_path):
    p = tmp_path / "shadow_decisions.jsonl"
    recs = [
        {"ts": "2026-07-08T10:00:00+00:00", "verdict": "PROPOSE", "auto_route": "ALERT",
         "best": {"best_effort": False}, "pool_feasible_count": 3, "latency_ms": 100},
        {"ts": "2026-07-08T10:05:00+00:00", "verdict": "KOORD", "auto_route": "KOORD",
         "best": {"best_effort": True}, "pool_feasible_count": 0, "latency_ms": 300,
         "pickup_extension_redirect": True},
        {"ts": "2026-07-08T10:06:00+00:00", "verdict": "PROPOSE", "auto_route": "AUTO",
         "best": {"best_effort": False}, "pool_feasible_count": 5, "latency_ms": 200},
    ]
    _write_jsonl(p, recs)
    r = hsb.load_shadow(str(p), None)
    assert r["data_ok"] and r["n"] == 3
    assert r["koord"] == 1 and round(r["koord_pct"], 1) == 33.3
    assert r["best_effort"] == 1
    assert r["feas0"] == 1
    assert r["redirects"]["pickup_extension_redirect"] == 1
    assert r["lat_n"] == 3 and r["lat_max"] == 300.0


def test_load_shadow_window_cutoff(hsb, tmp_path):
    p = tmp_path / "shadow_decisions.jsonl"
    recs = [
        {"ts": "2026-07-01T10:00:00+00:00", "verdict": "PROPOSE", "auto_route": "ALERT"},
        {"ts": "2026-07-08T10:00:00+00:00", "verdict": "PROPOSE", "auto_route": "ALERT"},
    ]
    _write_jsonl(p, recs)
    since = hsb.parse_ts("2026-07-08T00:00:00+00:00")
    r = hsb.load_shadow(str(p), since)
    assert r["n"] == 1  # tylko rekord z 07-08


def test_load_shadow_missing_fields(hsb, tmp_path):
    # brak best/latency/pool → bez wywrotki, defaulty
    p = tmp_path / "shadow_decisions.jsonl"
    _write_jsonl(p, [{"ts": "2026-07-08T10:00:00+00:00"}])
    r = hsb.load_shadow(str(p), None)
    assert r["data_ok"] and r["n"] == 1
    assert r["best_effort"] == 0 and r["feas0"] == 1 and r["lat_n"] == 0


# ── load_resweep ─────────────────────────────────────────────────────────────
def test_load_resweep(hsb, tmp_path):
    p = tmp_path / "pending_global_resweep.jsonl"
    recs = [
        {"ts": "2026-07-08T10:00:00+00:00", "would_repropose": True, "no_courier": False,
         "g_claim_ledger_breaches": 0, "reason": "lepszy_kurier"},
        {"ts": "2026-07-08T10:01:00+00:00", "would_repropose": False, "no_courier": False,
         "g_claim_ledger_breaches": 0, "reason": "bez_zmian"},
    ]
    _write_jsonl(p, recs)
    r = hsb.load_resweep(str(p), None)
    assert r["n"] == 2 and r["repropose"] == 1 and r["no_courier"] == 0
    assert r["breaches_sum"] == 0 and r["breaches_recs"] == 0
    assert r["reasons"]["lepszy_kurier"] == 1


def test_load_resweep_breach(hsb, tmp_path):
    p = tmp_path / "pending_global_resweep.jsonl"
    _write_jsonl(p, [
        {"ts": "2026-07-08T10:00:00+00:00", "g_claim_ledger_breaches": 2},
        {"ts": "2026-07-08T10:01:00+00:00", "g_claim_ledger_breaches": 1, "no_courier": True},
    ])
    r = hsb.load_resweep(str(p), None)
    assert r["breaches_sum"] == 3 and r["breaches_recs"] == 2
    assert r["no_courier"] == 1


# ── load_churn (tekstowy raport) ─────────────────────────────────────────────
def test_load_churn_dedup_last_wins(hsb, tmp_path):
    p = tmp_path / "proposal_churn.log"
    p.write_text(
        "=== PER DOBA (UTC) ===\n"
        "dzień       zlec≥2    ≥1%    ≥3%    śr flick_same%\n"
        "2026-07-06      188  85.1%  49.5% 2.968       45.3%\n"
        "2026-07-07      100  50.0%  20.0% 1.500       30.0%\n"
        # drugi przebieg dopisany później — dla 07-07 ma wygrać TEN wiersz
        "2026-07-07      226  61.9%  30.1% 1.921       39.0%\n",
        encoding="utf-8",
    )
    r = hsb.load_churn(str(p))
    assert r["data_ok"]
    assert r["last"]["day"] == "2026-07-07"
    assert r["last"]["n"] == 226  # ostatnie wystąpienie wygrywa
    assert round(r["last"]["ge3_pct"], 1) == 30.1
    assert len(r["series"]) == 2  # 07-06, 07-07 (bez duplikatu)


def test_load_churn_empty(hsb, tmp_path):
    p = tmp_path / "proposal_churn.log"
    p.write_text("=== brak wierszy per-doba ===\n", encoding="utf-8")
    assert hsb.load_churn(str(p))["data_ok"] is False


# ── load_eta_calib ───────────────────────────────────────────────────────────
def test_load_eta_calib(hsb, tmp_path):
    p = tmp_path / "eta_calib_metrics.jsonl"
    rec = {
        "logged_at": "2026-07-08T05:20:47+00:00", "promoted": True,
        "legs": {
            "pickup": {"champion": "L2_lgbm", "champion_mae": 5.33, "n_holdout": 3149,
                        "coverage": {"ONTIME_operacyjna": 81.6, "spoznien_pct": 18.4,
                                     "target_ontime": 0.8}},
            "delivery": {"champion": "L2_lgbm", "champion_mae": 7.38, "n_holdout": 2566,
                          "coverage": {"ONTIME_operacyjna": 83.2, "spoznien_pct": 16.8,
                                       "target_ontime": 0.8}},
        },
    }
    _write_jsonl(p, [rec])
    r = hsb.load_eta_calib(str(p))
    assert r["data_ok"]
    assert r["pickup"]["ontime"] == 81.6 and r["pickup"]["target_ontime_pct"] == 80.0
    assert r["delivery"]["ontime"] == 83.2 and r["delivery"]["mae"] == 7.38


# ── load_night_guard ─────────────────────────────────────────────────────────
def test_load_night_guard(hsb, tmp_path):
    p = tmp_path / "night_guard_history.jsonl"
    _write_jsonl(p, [
        {"ts": "2026-07-07T03:17:14+02:00", "pytest": {"failed": 0, "passed": 4451},
         "entropy": {"poison_live": 12, "poison_instr": 4}, "verdict": "OK"},
        {"ts": "2026-07-08T03:17:17+02:00", "pytest": {"failed": 0, "passed": 4451},
         "entropy": {"poison_live": 12, "poison_instr": 4}, "verdict": "OK"},
    ])
    r = hsb.load_night_guard(str(p))
    assert r["data_ok"] and r["pytest_failed"] == 0 and r["verdict"] == "OK"
    assert r["entropy"]["poison_live"] == 12
    assert len(r["series"]) == 2


# ── load_pickup_slip (ważona mediana) ────────────────────────────────────────
def test_load_pickup_slip_weighted(hsb, tmp_path):
    p = tmp_path / "pickup_slip_monitor.jsonl"
    rec = {
        "ts": "2026-07-07T22:30:03.4", "window_days": 3, "n_total": 669,
        "segments": {
            "srednio": {"solo": {"n": 100, "median": 20.0}, "bundle": {"n": 100, "median": 10.0}},
            "luzno": {"solo": {"n": 100, "median": 30.0}, "bundle": {"n": 0, "median": None}},
        },
    }
    _write_jsonl(p, [rec])
    r = hsb.load_pickup_slip(str(p))
    assert r["data_ok"]
    # solo: (20*100 + 30*100)/200 = 25.0
    assert round(r["solo_median"], 1) == 25.0 and r["solo_n"] == 200
    # bundle: tylko srednio n=100 med=10 → 10.0 (luzno n=0 pominięty)
    assert round(r["bundle_median"], 1) == 10.0 and r["bundle_n"] == 100


# ── EDGE: pusty log / brak danych w oknie ────────────────────────────────────
def test_empty_and_missing_files(hsb, tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert hsb.load_shadow(str(empty), None)["data_ok"] is False
    assert hsb.load_resweep(str(empty), None)["data_ok"] is False
    assert hsb.load_eta_calib(str(empty))["data_ok"] is False
    assert hsb.load_night_guard(str(empty))["data_ok"] is False
    assert hsb.load_pickup_slip(str(empty))["data_ok"] is False
    # nieistniejący plik → też data_ok False, bez wyjątku
    ghost = tmp_path / "does_not_exist.jsonl"
    assert hsb.load_shadow(str(ghost), None)["data_ok"] is False


def test_window_no_data(hsb, tmp_path):
    p = tmp_path / "shadow_decisions.jsonl"
    _write_jsonl(p, [{"ts": "2026-07-01T10:00:00+00:00", "verdict": "PROPOSE"}])
    since = hsb.parse_ts("2026-07-08T00:00:00+00:00")  # po wszystkich rekordach
    r = hsb.load_shadow(str(p), since)
    assert r["data_ok"] is False and r["n"] == 0


# ── rotation-aware ───────────────────────────────────────────────────────────
def test_rotation_aware_reads_siblings(hsb, tmp_path):
    live = tmp_path / "shadow_decisions.jsonl"
    _write_jsonl(live, [{"ts": "2026-07-08T10:00:00+00:00", "verdict": "PROPOSE"}])
    # rotowany starszy sibling
    _write_jsonl(tmp_path / "shadow_decisions.jsonl.1",
                 [{"ts": "2026-07-06T10:00:00+00:00", "verdict": "KOORD", "auto_route": "KOORD"}])
    since = hsb.parse_ts("2026-07-05T00:00:00+00:00")  # okno obejmuje oba
    r = hsb.load_shadow(str(live), since)
    assert r["n"] == 2 and r["koord"] == 1  # sibling .1 doczytany


# ── build_card: integracja SLO ───────────────────────────────────────────────
def _healthy_sources(hsb, tmp_path):
    sh = tmp_path / "shadow.jsonl"
    _write_jsonl(sh, [
        {"ts": "2026-07-08T10:00:00+00:00", "verdict": "PROPOSE", "auto_route": "ALERT",
         "best": {"best_effort": False}, "pool_feasible_count": 3, "latency_ms": 200}
        for _ in range(30)
    ])
    rs = tmp_path / "resweep.jsonl"
    _write_jsonl(rs, [
        {"ts": "2026-07-08T10:00:00+00:00", "would_repropose": False, "no_courier": False,
         "g_claim_ledger_breaches": 0, "reason": "bez_zmian"} for _ in range(60)
    ])
    eta = tmp_path / "eta.jsonl"
    _write_jsonl(eta, [{
        "logged_at": "2026-07-08T05:20:00+00:00", "promoted": True,
        "legs": {"pickup": {"champion_mae": 5.0, "n_holdout": 3000,
                            "coverage": {"ONTIME_operacyjna": 95.0, "spoznien_pct": 5.0, "target_ontime": 0.8}},
                 "delivery": {"champion_mae": 7.0, "n_holdout": 2500,
                              "coverage": {"ONTIME_operacyjna": 95.0, "spoznien_pct": 5.0, "target_ontime": 0.8}}},
    }])
    ch = tmp_path / "churn.log"
    ch.write_text("2026-07-08      200  40.0%  10.0% 1.000       20.0%\n", encoding="utf-8")
    ng = tmp_path / "ng.jsonl"
    _write_jsonl(ng, [{"ts": "2026-07-08T03:17:00+02:00", "pytest": {"failed": 0, "passed": 4451},
                       "entropy": {"poison_live": 12, "poison_instr": 4}, "verdict": "OK"}])
    ps = tmp_path / "ps.jsonl"
    _write_jsonl(ps, [{"ts": "2026-07-08T05:00:00+00:00", "window_days": 3, "n_total": 600,
                       "segments": {"srednio": {"solo": {"n": 100, "median": 15.0}}}}])
    return {
        "shadow": str(sh), "resweep": str(rs), "eta_calib": str(eta),
        "churn": str(ch), "night_guard": str(ng), "pickup_slip": str(ps),
    }


def test_build_card_all_green(hsb, tmp_path):
    now = hsb.parse_ts("2026-07-08T12:00:00+00:00")
    paths = _healthy_sources(hsb, tmp_path)
    sources = hsb.collect_sources(24, now, paths=paths)
    card = hsb.build_card(24, now, sources)
    overall = hsb._overall(card["metrics"])
    assert overall == hsb.GREEN
    assert card["attention"] == []  # brak czerwonych
    # kluczowe metryki obecne
    keys = {m["key"] for m in card["metrics"]}
    assert {"claim_ledger_breaches", "no_courier", "night_pytest",
            "eta_ontime_pickup", "koord_rate", "proposal_flicker"} <= keys


def test_build_card_breach_is_red_and_flagged(hsb, tmp_path):
    now = hsb.parse_ts("2026-07-08T12:00:00+00:00")
    paths = _healthy_sources(hsb, tmp_path)
    # wstrzyknij breach do resweep
    _write_jsonl(Path(paths["resweep"]), [
        {"ts": "2026-07-08T10:00:00+00:00", "g_claim_ledger_breaches": 1, "reason": "x"}
    ])
    sources = hsb.collect_sources(24, now, paths=paths)
    card = hsb.build_card(24, now, sources)
    breach = next(m for m in card["metrics"] if m["key"] == "claim_ledger_breaches")
    assert breach["status"] == hsb.RED
    assert any(k == "claim_ledger_breaches" for k, _, _ in card["attention"])
    assert hsb._overall(card["metrics"]) == hsb.RED


def test_build_card_pytest_fail_red(hsb, tmp_path):
    now = hsb.parse_ts("2026-07-08T12:00:00+00:00")
    paths = _healthy_sources(hsb, tmp_path)
    _write_jsonl(Path(paths["night_guard"]), [
        {"ts": "2026-07-08T03:17:00+02:00", "pytest": {"failed": 3, "passed": 4448},
         "entropy": {"poison_live": 12, "poison_instr": 4}, "verdict": "ALERT"}
    ])
    sources = hsb.collect_sources(24, now, paths=paths)
    card = hsb.build_card(24, now, sources)
    ng = next(m for m in card["metrics"] if m["key"] == "night_pytest")
    assert ng["status"] == hsb.RED


def test_build_card_insufficient_data_grey(hsb, tmp_path):
    now = hsb.parse_ts("2026-07-08T12:00:00+00:00")
    # wszystkie źródła puste → same ⚪, żadnego zmyślonego zielonego
    for n in ("shadow", "resweep", "eta", "churn", "ng", "ps"):
        (tmp_path / f"{n}.x").write_text("", encoding="utf-8")
    paths = {
        "shadow": str(tmp_path / "shadow.x"), "resweep": str(tmp_path / "resweep.x"),
        "eta_calib": str(tmp_path / "eta.x"), "churn": str(tmp_path / "churn.x"),
        "night_guard": str(tmp_path / "ng.x"), "pickup_slip": str(tmp_path / "ps.x"),
    }
    sources = hsb.collect_sources(24, now, paths=paths)
    card = hsb.build_card(24, now, sources)
    # claim_ledger + eta pickup istnieją jako ⚪ (za mało danych), nie 🟢
    cl = next(m for m in card["metrics"] if m["key"] == "claim_ledger_breaches")
    assert cl["status"] == hsb.GREY and "za mało danych" in cl["value"]
    assert hsb._overall(card["metrics"]) == hsb.GREY


# ── main(): pisze WYŁĄCZNIE do tmp out-dir (anty-PROD) ───────────────────────
def test_main_writes_only_to_out_dir(hsb, tmp_path, monkeypatch):
    paths = _healthy_sources(hsb, tmp_path)
    # przekieruj domyślne źródła na fixtury (main czyta stałe modułu)
    monkeypatch.setattr(hsb, "SRC_SHADOW", paths["shadow"])
    monkeypatch.setattr(hsb, "SRC_RESWEEP", paths["resweep"])
    monkeypatch.setattr(hsb, "SRC_ETA_CALIB", paths["eta_calib"])
    monkeypatch.setattr(hsb, "SRC_CHURN", paths["churn"])
    monkeypatch.setattr(hsb, "SRC_NIGHT_GUARD", paths["night_guard"])
    monkeypatch.setattr(hsb, "SRC_PICKUP_SLIP", paths["pickup_slip"])
    out = tmp_path / "out"
    rc = hsb.main(["--window-hours", "24", "--out-dir", str(out),
                   "--asof", "2026-07-08T12:00:00+00:00"])
    assert rc == 0
    md = out / hsb.CARD_MD
    js = out / hsb.CARD_JSON
    assert md.exists() and js.exists()
    # anty-PROD: efektywna ścieżka to tmp, NIE prod dispatch_state
    assert "/dispatch_state/" not in str(md)
    assert str(tmp_path) in str(md)
    payload = json.loads(js.read_text(encoding="utf-8"))
    assert payload["overall"] in (hsb.GREEN, hsb.YELLOW, hsb.RED, hsb.GREY)
    assert payload["window_hours"] == 24
    # karta markdown zawiera nagłówek + sekcję uwag
    assert "Tablica zdrowia" in md.read_text(encoding="utf-8")


def test_main_stdout_does_not_write(hsb, tmp_path, monkeypatch, capsys):
    paths = _healthy_sources(hsb, tmp_path)
    for attr, key in [("SRC_SHADOW", "shadow"), ("SRC_RESWEEP", "resweep"),
                      ("SRC_ETA_CALIB", "eta_calib"), ("SRC_CHURN", "churn"),
                      ("SRC_NIGHT_GUARD", "night_guard"), ("SRC_PICKUP_SLIP", "pickup_slip")]:
        monkeypatch.setattr(hsb, attr, paths[key])
    out = tmp_path / "out2"
    rc = hsb.main(["--stdout", "--asof", "2026-07-08T12:00:00+00:00", "--out-dir", str(out)])
    assert rc == 0
    assert not out.exists()  # --stdout NIE tworzy plików
    captured = capsys.readouterr()
    assert "Tablica zdrowia" in captured.out
