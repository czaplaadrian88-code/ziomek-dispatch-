"""SP-B2-ETAQ — testy generatora tools/eta_quantile_calib.py (2026-06-11)."""
import json
from datetime import datetime, timedelta, timezone

from dispatch_v2.tools import eta_quantile_calib as eqc


def _now():
    return datetime.now(timezone.utc)


def _rec(pred, real, slot="peak", matched=True, ts=None):
    return {
        "logged_at": (ts or _now()).isoformat(),
        "predicted_delivery_min": pred,
        "real_delivery_min": real,
        "matched_courier": matched,
        "bucket": slot,
    }


def _write_log(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_bin_label_edges():
    assert eqc.bin_label(0.5) == "0-10"
    assert eqc.bin_label(10.0) == "10-15"
    assert eqc.bin_label(39.9) == "30-40"
    assert eqc.bin_label(40.0) == "40+"
    assert eqc.bin_label(95.0) == "40+"


def test_quantile_interpolation():
    assert eqc._quantile([10.0], 0.5) == 10.0
    assert eqc._quantile([10.0, 20.0], 0.5) == 15.0
    assert abs(eqc._quantile([1, 2, 3, 4, 5], 0.8) - 4.2) < 1e-9


def test_collect_pairs_filters(tmp_path, monkeypatch):
    log = tmp_path / "eta_calibration_log.jsonl"
    t = _now()
    _write_log(log, [
        _rec(27, 19, "peak", True, t),                    # OK matched
        _rec(27, 21, "peak", False, t),                   # all-only (unmatched)
        _rec(None, 20, "peak", True, t),                  # brak pred → odpada
        _rec(27, None, "peak", True, t),                  # brak real → odpada
        _rec(27, 300, "peak", True, t),                   # real > MAX → odpada
        _rec(-5, 20, "peak", True, t),                    # pred <= 0 → odpada
        _rec(27, 18, "peak", True, t - timedelta(days=99)),  # poza oknem
    ])
    monkeypatch.setattr(eqc, "CALIB_LOG", str(log))
    matched, allp = eqc.collect_pairs(days=28)
    assert len(matched) == 1 and len(allp) == 2


def test_collect_pairs_reads_rotated_sibling(tmp_path, monkeypatch):
    log = tmp_path / "eta_calibration_log.jsonl"
    t = _now()
    _write_log(str(log) + ".1", [_rec(30, 22, "peak", True, t - timedelta(days=2))])
    _write_log(log, [_rec(12, 14, "shoulder", True, t)])
    monkeypatch.setattr(eqc, "CALIB_LOG", str(log))
    matched, _ = eqc.collect_pairs(days=28)
    assert len(matched) == 2, "para z .1 musi wejść do okna"


def test_build_map_quantiles_and_bias():
    # 5 par w koszyku 25-30/peak: real = 16..24 → p50=20
    pairs = [(27.0, float(r), "peak") for r in (16, 18, 20, 22, 24)]
    built = eqc.build_map(pairs)
    cell = built["map"]["peak"]["25-30"]
    assert cell["n"] == 5
    assert cell["p50"] == 20.0
    assert cell["bias_med"] == 20.0 - 27.0
    assert built["global"]["25-30"]["n"] == 5


def test_unknown_slot_falls_to_offpeak(tmp_path, monkeypatch):
    log = tmp_path / "log.jsonl"
    _write_log(log, [_rec(12, 13, slot="dziwny", matched=True)])
    monkeypatch.setattr(eqc, "CALIB_LOG", str(log))
    matched, _ = eqc.collect_pairs(days=28)
    assert matched[0][2] == "offpeak"


def test_run_writes_valid_self_describing_json(tmp_path, monkeypatch):
    log = tmp_path / "log.jsonl"
    out = tmp_path / "eta_quantile_map.json"
    _write_log(log, [_rec(27, 20, "peak", True) for _ in range(40)])
    monkeypatch.setattr(eqc, "CALIB_LOG", str(log))
    monkeypatch.setattr(eqc, "OUT_PATH", str(out))

    payload = eqc.run(days=28, dry_run=False)
    on_disk = json.loads(out.read_text())
    assert on_disk["n_pairs_matched"] == 40
    assert on_disk["min_n"] == eqc.MIN_N
    assert on_disk["pred_bins"] == eqc.PRED_BINS
    assert on_disk["map"]["peak"]["25-30"]["p50"] == 20.0
    assert payload["generated_at"] == on_disk["generated_at"]


def test_run_dry_run_no_write(tmp_path, monkeypatch):
    log = tmp_path / "log.jsonl"
    out = tmp_path / "eta_quantile_map.json"
    _write_log(log, [_rec(27, 20)])
    monkeypatch.setattr(eqc, "CALIB_LOG", str(log))
    monkeypatch.setattr(eqc, "OUT_PATH", str(out))
    eqc.run(days=28, dry_run=True)
    assert not out.exists()
