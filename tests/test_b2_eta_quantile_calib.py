"""SP-B2-ETAQ — testy generatora tools/eta_quantile_calib.py (2026-06-11).

Format wyjścia = kontrakt sesji A (MAP_CONTRACT_calib_maps_sesjaA.md);
zgodność weryfikowana KONSUMENTEM calib_maps.eta_quantile_calibrate.
"""
import json
from datetime import datetime, timedelta, timezone

from dispatch_v2 import calib_maps
from dispatch_v2.tools import eta_quantile_calib as eqc


def _now():
    return datetime.now(timezone.utc)


def _rec(pred, real, hour=12, matched=True, ts=None):
    return {
        "logged_at": (ts or _now()).isoformat(),
        "predicted_delivery_min": pred,
        "real_delivery_min": real,
        "matched_courier": matched,
        "hour_warsaw": hour,
    }


def _write_log(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_slot_for_hour_matches_consumer_boundaries():
    assert eqc.slot_for_hour_warsaw(11) == "peak_lunch"
    assert eqc.slot_for_hour_warsaw(13) == "peak_lunch"
    assert eqc.slot_for_hour_warsaw(14) == "high_risk"
    assert eqc.slot_for_hour_warsaw(16) == "high_risk"
    assert eqc.slot_for_hour_warsaw(17) == "peak_dinner"
    assert eqc.slot_for_hour_warsaw(19) == "peak_dinner"
    assert eqc.slot_for_hour_warsaw(9) == "off"
    assert eqc.slot_for_hour_warsaw(21) == "off"


def test_bin_edges():
    assert eqc._bin_edges(0.5) == (0.0, 10.0)
    assert eqc._bin_edges(10.0) == (10.0, 15.0)
    assert eqc._bin_edges(39.9) == (30.0, 40.0)
    assert eqc._bin_edges(40.0) == (40.0, eqc.PRED_HI_OPEN)
    assert eqc._bin_edges(95.0) == (40.0, eqc.PRED_HI_OPEN)


def test_quantile_interpolation():
    assert eqc._quantile([10.0], 0.5) == 10.0
    assert eqc._quantile([10.0, 20.0], 0.5) == 15.0
    assert abs(eqc._quantile([1, 2, 3, 4, 5], 0.8) - 4.2) < 1e-9


def test_collect_pairs_filters(tmp_path, monkeypatch):
    log = tmp_path / "eta_calibration_log.jsonl"
    t = _now()
    _write_log(log, [
        _rec(27, 19, 12, True, t),                       # OK matched
        _rec(27, 21, 12, False, t),                      # all-only (unmatched)
        _rec(None, 20, 12, True, t),                     # brak pred → odpada
        _rec(27, None, 12, True, t),                     # brak real → odpada
        _rec(27, 300, 12, True, t),                      # real > MAX → odpada
        _rec(-5, 20, 12, True, t),                       # pred <= 0 → odpada
        _rec(27, 18, 12, True, t - timedelta(days=99)),  # poza oknem
    ])
    monkeypatch.setattr(eqc, "CALIB_LOG", str(log))
    matched, n_all = eqc.collect_pairs(days=28)
    assert len(matched) == 1 and n_all == 2


def test_collect_pairs_hour_fallback_from_picked_up(tmp_path, monkeypatch):
    log = tmp_path / "log.jsonl"
    r = _rec(27, 19, hour=None)
    r["hour_warsaw"] = None
    r["picked_up_at"] = "2026-06-10 15:42:11"
    _write_log(log, [r])
    monkeypatch.setattr(eqc, "CALIB_LOG", str(log))
    matched, _ = eqc.collect_pairs(days=28)
    assert len(matched) == 1
    assert matched[0][2] == "high_risk"


def test_collect_pairs_reads_rotated_sibling(tmp_path, monkeypatch):
    log = tmp_path / "eta_calibration_log.jsonl"
    t = _now()
    _write_log(str(log) + ".1", [_rec(30, 22, 12, True, t - timedelta(days=2))])
    _write_log(log, [_rec(12, 14, 18, True, t)])
    monkeypatch.setattr(eqc, "CALIB_LOG", str(log))
    matched, _ = eqc.collect_pairs(days=28)
    assert len(matched) == 2, "para z .1 musi wejść do okna"


def test_build_buckets_min_n_and_all_fallback():
    # 35 par w peak_lunch → emitowane (slot + all); 5 par w high_risk → tylko
    # do "all" (komórka slotu poniżej MIN_N nie wychodzi)
    pairs = [(27.0, 20.0, "peak_lunch")] * 35 + [(27.0, 30.0, "high_risk")] * 5
    buckets = eqc.build_buckets(pairs)
    slots = {b["slot"] for b in buckets}
    assert slots == {"peak_lunch", "all"}
    allb = [b for b in buckets if b["slot"] == "all"][0]
    assert allb["n"] == 40
    pl = [b for b in buckets if b["slot"] == "peak_lunch"][0]
    assert pl["n"] == 35 and pl["p50"] == 20.0
    assert pl["pred_lo"] == 25.0 and pl["pred_hi"] == 30.0


def test_run_output_consumed_by_calib_maps(tmp_path, monkeypatch):
    """E2E zgodności z kontraktem: wygenerowany plik czyta calib_maps."""
    log = tmp_path / "log.jsonl"
    out = tmp_path / "eta_quantile_map.json"
    _write_log(log, [_rec(27, 20, hour=12) for _ in range(40)])
    monkeypatch.setattr(eqc, "CALIB_LOG", str(log))
    monkeypatch.setattr(eqc, "OUT_PATH", str(out))
    eqc.run(days=28, dry_run=False)

    monkeypatch.setattr(calib_maps, "ETA_QUANTILE_MAP_PATH", str(out))
    calib_maps.reset_caches()
    try:
        # pred=27 w peak_lunch (12:30 Warsaw) → p50=20.0 z mapy
        noon_warsaw = datetime(2026, 6, 10, 10, 30, tzinfo=timezone.utc)  # 12:30 W
        got = calib_maps.eta_quantile_calibrate(27.0, now=noon_warsaw)
        assert got == 20.0
        # poza koszykiem (pred 5, brak n) → None
        assert calib_maps.eta_quantile_calibrate(5.0, now=noon_warsaw) is None
    finally:
        calib_maps.reset_caches()


def test_run_dry_run_no_write(tmp_path, monkeypatch):
    log = tmp_path / "log.jsonl"
    out = tmp_path / "eta_quantile_map.json"
    _write_log(log, [_rec(27, 20)])
    monkeypatch.setattr(eqc, "CALIB_LOG", str(log))
    monkeypatch.setattr(eqc, "OUT_PATH", str(out))
    eqc.run(days=28, dry_run=True)
    assert not out.exists()


def test_run_writes_contract_keys(tmp_path, monkeypatch):
    log = tmp_path / "log.jsonl"
    out = tmp_path / "eta_quantile_map.json"
    _write_log(log, [_rec(27, 20, hour=12) for _ in range(40)])
    monkeypatch.setattr(eqc, "CALIB_LOG", str(log))
    monkeypatch.setattr(eqc, "OUT_PATH", str(out))
    eqc.run(days=28, dry_run=False)
    on_disk = json.loads(out.read_text())
    assert on_disk["version"] == 1
    assert isinstance(on_disk["buckets"], list)
    for b in on_disk["buckets"]:
        assert {"slot", "pred_lo", "pred_hi", "p50", "p80", "n"} <= set(b)
