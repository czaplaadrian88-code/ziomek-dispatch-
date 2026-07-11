"""A360-R0: niezalezny frozen oracle prawdy world replay."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from dispatch_v2.tools import world_replay as WR
from dispatch_v2.tools import world_replay_gate as G

FIXTURE = Path(__file__).parent / "fixtures" / "world_replay_truth_frozen.json"
AS_OF = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def _extract(cid="synthetic-a", pool=2):
    return {"verdict": "PROPOSE", "reason": "ok", "best_cid": cid,
            "best_score": 1.0, "pool_feasible": pool, "pool_total": 3}


def _record(oid, minute, schema="wr1", live_inputs=True):
    ts = f"2026-07-11T11:{minute:02d}:00+00:00"
    rec = {"order_id": oid, "ts": ts, "now": ts, "schema": schema,
           "order_event": {"order_id": oid}, "fleet": {}, "flags": {},
           "osrm_calls": []}
    if live_inputs:
        rec["live_inputs"] = {}
    return rec


def _write_corpus(root: Path):
    records = [
        _record("synthetic-input", 1, schema="wr0"),
        _record("synthetic-osrm", 2),
        _record("synthetic-critical", 3),
        _record("synthetic-soft", 4),
        _record("synthetic-parity", 5),
    ]
    record_file = root / "world_record-20260711.jsonl"
    record_file.write_text("\n".join(json.dumps(r) for r in records) + "\n",
                           encoding="utf-8")
    shadow_file = root / "shadow.jsonl"
    shadows = [
        {"order_id": r["order_id"], "ts": r["ts"], "verdict": "PROPOSE",
         "reason": "ok", "best": {"courier_id": "synthetic-a", "score": 1.0},
         "pool_feasible_count": 2, "pool_total_count": 3}
        for r in records[1:]
    ]
    shadow_file.write_text("\n".join(json.dumps(r) for r in shadows) + "\n",
                           encoding="utf-8")
    return record_file, shadow_file


def _fake_replay(rec):
    oid = rec["order_id"]
    if oid == "synthetic-osrm":
        return _extract(cid="synthetic-b"), 1
    if oid == "synthetic-critical":
        return _extract(cid="synthetic-b"), 0
    if oid == "synthetic-soft":
        return _extract(pool=1), 0
    return _extract(), 0


def test_frozen_known_answer_all_five_classes():
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    got = [
        WR.classify_replay(case["recorded"], case["replayed"],
                           case["osrm_misses"], case["input_miss_reason"])["class"]
        for case in cases
    ]
    assert got == [case["expected"] for case in cases]
    assert got == list(WR.REPLAY_CLASSES)


def test_mutation_control_critical_axis_has_teeth(monkeypatch):
    case = json.loads(FIXTURE.read_text(encoding="utf-8"))[2]
    assert WR.classify_replay(case["recorded"], case["replayed"])["class"] == "CRITICAL_DIFF"
    monkeypatch.setattr(WR, "CRITICAL_FIELDS", frozenset())
    assert WR.classify_replay(case["recorded"], case["replayed"])["class"] == "SOFT_DIFF"


def test_gate_exactly_once_coverage_freshness_and_determinism(tmp_path, monkeypatch):
    _, shadow = _write_corpus(tmp_path)
    monkeypatch.setattr(G.WR, "replay_one", _fake_replay)
    kwargs = dict(since=None, until=None, record_dir=str(tmp_path),
                  shadow_file=str(shadow), as_of=AS_OF)
    first = G.run_gate(**kwargs)
    second = G.run_gate(**kwargs)
    expected = {name: 1 for name in WR.REPLAY_CLASSES}
    assert first["class_counts"] == expected
    assert sum(first["class_counts"].values()) == first["denominator"] == 5
    assert first["input_miss_reasons"] == {"schema_pre_wr1": 1}
    assert first["coverage"] == {"input_pct": 80.0, "osrm_pct": 75.0,
                                 "oracle_pct": 60.0}
    assert first["freshness"] == {"newest_record_at": "2026-07-11T11:05:00+00:00",
                                  "age_seconds": 3300.0}
    assert first == second
    assert first["corpus_fingerprint"] == second["corpus_fingerprint"]


def test_incomplete_record_never_uses_live_or_network_fallback(tmp_path, monkeypatch):
    rec = _record("synthetic-no-live", 6, live_inputs=False)
    (tmp_path / "world_record-20260711.jsonl").write_text(
        json.dumps(rec) + "\n", encoding="utf-8")
    shadow = tmp_path / "shadow.jsonl"
    shadow.write_text("", encoding="utf-8")

    def forbidden(_rec):
        raise AssertionError("network/live fallback attempted")

    monkeypatch.setattr(G.WR, "replay_one", forbidden)
    report = G.run_gate(None, None, record_dir=str(tmp_path),
                        shadow_file=str(shadow), as_of=AS_OF)
    assert report["class_counts"]["INPUT_MISS"] == 1
    assert report["input_miss_reasons"] == {"missing_live_inputs": 1}


def test_cli_uses_only_temp_record_ledger_verdict_and_redacts_ids(tmp_path, monkeypatch):
    _, shadow = _write_corpus(tmp_path)
    monkeypatch.setattr(G.WR, "replay_one", _fake_replay)
    verdict = tmp_path / "verdict.txt"
    for path in (tmp_path, shadow, verdict):
        assert "/dispatch_state/" not in str(path)
    rc = G.main(["--record-dir", str(tmp_path), "--shadow-file", str(shadow),
                 "--out", str(verdict), "--as-of", AS_OF.isoformat()])
    assert rc == 1
    text = verdict.read_text(encoding="utf-8")
    assert "denominator=5" in text
    assert "synthetic-input" not in text
    assert "synthetic-osrm" not in text
    assert "synthetic-a" not in text and "synthetic-b" not in text
    assert "INPUT_MISS ref=" in text and "OSRM_MISS ref=" in text
