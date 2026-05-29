"""Faza 2b — testy analizatora shadow + rubryki rekomendacji."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import courier_gps_commitment_report as rep


def _rec(t, **kw):
    d = {"divergence_type": t, "order_id": kw.get("oid", "1"),
         "would_apply": t in ("GPS_PICKUP_AHEAD", "GPS_DELIVERED_AHEAD")}
    d.update(kw)
    return d


def test_analyze_counts_and_percentiles():
    recs = [
        _rec("GPS_PICKUP_AHEAD", oid="1", gps_ahead_sec=100),
        _rec("GPS_PICKUP_AHEAD", oid="2", gps_ahead_sec=300),
        _rec("GPS_PICKUP_AHEAD", oid="3", gps_ahead_sec=500),
        _rec("GPS_DELIVERED_AHEAD", oid="4"),
        _rec("GPS_PICKUP_TIMING", oid="5", timing_delta_sec=180),
        _rec("COURIER_MISMATCH", oid="6"),
        _rec("GPS_ORPHAN", oid="7"),
    ]
    s = rep.analyze(recs)
    assert s["records"] == 7 and s["unique_orders"] == 7
    assert s["would_apply"] == 4          # 3 pickup_ahead + 1 delivered_ahead
    assert s["pickup_ahead"] == 3 and s["delivered_ahead"] == 1
    assert s["anomalies"] == 2            # mismatch + orphan
    assert s["pickup_ahead_median_sec"] == 300
    assert s["timing_median_sec"] == 180


def test_recommend_hold_low_sample():
    s = rep.analyze([_rec("GPS_PICKUP_AHEAD", oid=str(i), gps_ahead_sec=300) for i in range(5)])
    verdict, _ = rep.recommend(s)
    assert verdict == "HOLD_NEED_MORE_DATA"


def test_recommend_do_not_flip_anomalies():
    recs = [_rec("GPS_PICKUP_AHEAD", oid=str(i), gps_ahead_sec=300) for i in range(40)]
    recs += [_rec("COURIER_MISMATCH", oid=f"m{i}") for i in range(10)]  # 25% anomalii
    verdict, _ = rep.recommend(rep.analyze(recs))
    assert verdict == "DO_NOT_FLIP_ANOMALIES"


def test_recommend_flip_when_strong_signal():
    recs = [_rec("GPS_PICKUP_AHEAD", oid=str(i), gps_ahead_sec=300) for i in range(40)]
    recs += [_rec("COURIER_MISMATCH", oid="m1")]  # ~2.5% anomalii < 10%
    verdict, _ = rep.recommend(rep.analyze(recs))
    assert verdict == "RECOMMEND_FLIP"


def test_recommend_hold_low_value():
    # dużo sygnału, mało anomalii, ale GPS wyprzedza tylko ~30s (< 120s)
    recs = [_rec("GPS_PICKUP_AHEAD", oid=str(i), gps_ahead_sec=30) for i in range(40)]
    verdict, _ = rep.recommend(rep.analyze(recs))
    assert verdict == "HOLD_LOW_VALUE"


def test_load_records_filters_by_since(tmp_path):
    p = tmp_path / "shadow.jsonl"
    p.write_text(
        json.dumps({"observed_at": "2026-05-20T10:00:00+00:00", "divergence_type": "GPS_PICKUP_AHEAD"}) + "\n" +
        json.dumps({"observed_at": "2026-05-29T10:00:00+00:00", "divergence_type": "GPS_PICKUP_AHEAD"}) + "\n"
    )
    since = rep._iso_to_epoch("2026-05-25T00:00:00+00:00")
    recs = rep.load_records(str(p), since_epoch=since)
    assert len(recs) == 1
    assert recs[0]["observed_at"].startswith("2026-05-29")


def test_run_quiet_until_actionable_no_send_on_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(rep, "SHADOW_LOG_PATH", str(tmp_path / "empty.jsonl"))
    monkeypatch.setattr(rep, "REPORT_DIR", str(tmp_path / "out"))
    out = rep.run(window_days=7, send_telegram=True, quiet_until_actionable=True)
    assert out["verdict"] == "HOLD_NEED_MORE_DATA"
    assert out["actionable"] is False
    assert out["notified"] is False        # cichy: brak danych → brak Telegrama
