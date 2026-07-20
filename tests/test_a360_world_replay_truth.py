"""A360-R0: niezalezny frozen oracle prawdy world replay."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from dispatch_v2.tools import world_replay as WR
from dispatch_v2.tools import world_replay_gate as G

FIXTURE = Path(__file__).parent / "fixtures" / "world_replay_truth_frozen.json"
AS_OF = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def _extract(cid="synthetic-a", pool=2):
    return {"verdict": "PROPOSE", "reason": "ok", "best_cid": cid,
            "best_score": 1.0, "pool_feasible": pool, "pool_total": 3}


def _live_inputs():
    return {"reliability": {}, "plans": {}, "eta_quantile": {},
            "prep_bias": {}, "loadgov": [None, None, None, 0], "k07": None,
            "courier_last_pos": {}}


def _record(oid, minute, schema="wr1", live_inputs=True):
    ts = f"2026-07-11T11:{minute:02d}:00+00:00"
    rec = {"order_id": oid, "ts": ts, "now": ts, "schema": schema,
           "order_event": {"order_id": oid}, "fleet": {}, "flags": {},
           "osrm_calls": []}
    if live_inputs:
        rec["live_inputs"] = _live_inputs()
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
         "alternatives": [{"courier_id": "synthetic-b", "score": 0.5}],
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


def test_paired_replay_import_contract_uses_same_critical_fields():
    from dispatch_v2.tools import paired_flag_replay as paired

    assert frozenset(G.CORE_FIELDS) == WR.CRITICAL_FIELDS
    assert paired._CORE_FIELDS == WR.CRITICAL_FIELDS


def test_mutation_control_critical_axis_has_teeth(monkeypatch):
    case = json.loads(FIXTURE.read_text(encoding="utf-8"))[2]
    assert WR.classify_replay(case["recorded"], case["replayed"])["class"] == "CRITICAL_DIFF"
    monkeypatch.setattr(WR, "CRITICAL_FIELDS", frozenset())
    assert WR.classify_replay(case["recorded"], case["replayed"])["class"] == "SOFT_DIFF"


@pytest.mark.parametrize(("shape", "best", "alternatives"), [
    ("middle-best", {"courier_id": "200", "score": 2.0}, ["100", "300"]),
    ("solo-fallback", {"courier_id": "200", "score": 2.0}, ["100", "300"]),
    ("best-none", None, ["100", "200"]),
])
def test_extract_accepts_a8_2_deduplicated_alternative_shapes(
        shape, best, alternatives):
    """R0 czyta kanoniczne ``best`` po A8-2, niezależnie od pozycji winnera.

    A8-2 usuwa wybranego cid z ``alternatives`` (także dla nowego obiektu
    solo-fallback), a przy best=None zachowuje całą zdeduplikowaną pulę.
    """
    shadow = {
        "verdict": "PROPOSE" if best is not None else "KOORD",
        "reason": shape,
        "best": best,
        "alternatives": [
            {"courier_id": cid, "score": float(cid) / 100}
            for cid in alternatives
        ],
        "pool_feasible_count": 2,
        "pool_total_count": 3,
    }
    extracted = WR._extract(shadow)
    expected_cid = best["courier_id"] if best is not None else None
    expected_score = best["score"] if best is not None else None
    assert extracted["best_cid"] == expected_cid
    assert extracted["best_score"] == expected_score
    assert expected_cid is None or expected_cid not in alternatives


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


@pytest.mark.parametrize(("live_inputs", "reason"), [
    ({}, "missing_live_input:reliability"),
    *[
        ({key: value for key, value in _live_inputs().items() if key != missing},
         f"missing_live_input:{missing}")
        for missing in WR.REQUIRED_LIVE_INPUT_KEYS
    ],
    ({**_live_inputs(), "reliability": []}, "invalid_live_input:reliability"),
    ({**_live_inputs(), "plans": None}, "invalid_live_input:plans"),
    ({**_live_inputs(), "eta_quantile": []}, "invalid_live_input:eta_quantile"),
    ({**_live_inputs(), "prep_bias": "bad"}, "invalid_live_input:prep_bias"),
    ({**_live_inputs(), "courier_last_pos": []},
     "invalid_live_input:courier_last_pos"),
    ({**_live_inputs(), "loadgov": {}}, "invalid_live_input:loadgov"),
    ({**_live_inputs(), "loadgov": [1, 2, 3]}, "invalid_live_input:loadgov"),
    ({**_live_inputs(), "k07": []}, "invalid_live_input:k07"),
])
def test_partial_or_invalid_live_inputs_stop_before_replay(
        tmp_path, monkeypatch, live_inputs, reason):
    rec = _record("synthetic-invalid", 7)
    rec["live_inputs"] = live_inputs
    (tmp_path / "world_record-20260711.jsonl").write_text(
        json.dumps(rec) + "\n", encoding="utf-8")
    calls = []

    def forbidden(_rec):
        calls.append(_rec)
        raise AssertionError("replay_one/live fallback attempted")

    monkeypatch.setattr(G.WR, "replay_one", forbidden)
    report = G.run_gate(None, None, record_dir=str(tmp_path), shadow_index={})
    assert report["class_counts"]["INPUT_MISS"] == 1
    assert report["input_miss_reasons"] == {reason: 1}
    assert calls == []


def test_validator_direct_replay_and_cli_fail_closed(tmp_path, monkeypatch, capsys):
    valid = _record("synthetic-direct", 8)
    assert WR.validate_live_inputs(valid) is None
    invalid = {**valid, "live_inputs": {**_live_inputs()}}
    invalid["live_inputs"].pop("loadgov")
    assert WR.validate_live_inputs(invalid) == "missing_live_input:loadgov"
    with pytest.raises(WR.IncompleteReplayInput, match="missing_live_input:loadgov"):
        WR.replay_one(invalid)

    record_file = tmp_path / "world_record.jsonl"
    record_file.write_text(json.dumps(invalid) + "\n", encoding="utf-8")
    calls = []

    def forbidden(_rec):
        calls.append(_rec)
        raise AssertionError("CLI called replay_one")

    monkeypatch.setattr(WR, "replay_one", forbidden)
    rc = WR.main(["--order-id", invalid["order_id"],
                  "--record-file", str(record_file)])
    assert rc == 2
    assert "INPUT_MISS reason=missing_live_input:loadgov" in capsys.readouterr().out
    assert calls == []


@pytest.mark.parametrize("live_inputs", [{}, {"plans": {}}])
def test_serve_partial_inputs_cannot_patch_live_paths(tmp_path, live_inputs):
    class DummyCommon:
        A2_RELIABILITY_FEED_PATH = "/dispatch_state/a2_reliability.json"

    class DummyPipeline:
        pass

    patched = []
    with pytest.raises(WR.IncompleteReplayInput):
        WR._serve_live_inputs({"live_inputs": live_inputs}, DummyPipeline,
                              DummyCommon, str(tmp_path),
                              lambda *args: patched.append(args))
    assert patched == []
    assert DummyCommon.A2_RELIABILITY_FEED_PATH == "/dispatch_state/a2_reliability.json"


def test_required_set_mutation_control_has_teeth():
    rec = _record("synthetic-mutation", 9)
    rec["live_inputs"].pop("loadgov")
    assert WR.validate_live_inputs(rec) == "missing_live_input:loadgov"


@pytest.mark.parametrize(("patch", "reason"), [
    ({"order_id": ""}, "missing_order_id"),
    ({"ts": "not-a-timestamp"}, "invalid_ts"),
    ({"now": None}, "missing_now"),
    ({"now": "not-a-timestamp"}, "invalid_now"),
    ({"schema": "wr2"}, "unknown_schema"),
    ({"order_event": []}, "missing_order_event"),
    ({"fleet": []}, "missing_fleet"),
    ({"flags": []}, "missing_flags"),
    ({"osrm_calls": ()}, "invalid_osrm_calls"),
])
def test_outer_record_validator_is_shared_and_fail_closed(monkeypatch, patch, reason):
    rec = _record("synthetic-outer", 10)
    rec.update(patch)
    assert WR.validate_replay_record(rec) == reason
    assert G._input_miss_reason(rec) == reason
    with pytest.raises(WR.IncompleteReplayInput, match=reason):
        WR.replay_one(rec)


def test_direct_cli_rejects_invalid_outer_before_replay(tmp_path, monkeypatch, capsys):
    rec = _record("synthetic-cli-outer", 11)
    rec["osrm_calls"] = {}
    record_file = tmp_path / "world_record.jsonl"
    record_file.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(WR, "replay_one", lambda record: calls.append(record))
    rc = WR.main(["--order-id", rec["order_id"],
                  "--record-file", str(record_file)])
    assert rc == 2
    assert "INPUT_MISS reason=invalid_osrm_calls" in capsys.readouterr().out
    assert calls == []


def test_gate_rejects_invalid_outer_before_shadow_join_and_replay(tmp_path, monkeypatch):
    rec = _record("synthetic-gate-outer", 11)
    rec["osrm_calls"] = {}
    (tmp_path / "world_record-20260711.jsonl").write_text(
        json.dumps(rec) + "\n", encoding="utf-8")
    joined = []
    replayed = []

    def forbidden_join(*args):
        joined.append(args)
        raise AssertionError("shadow join called for invalid outer record")

    def forbidden_replay(record):
        replayed.append(record)
        raise AssertionError("replay called for invalid outer record")

    monkeypatch.setattr(G, "_join_shadow", forbidden_join)
    monkeypatch.setattr(G.WR, "replay_one", forbidden_replay)
    report = G.run_gate(None, None, record_dir=str(tmp_path), shadow_index={})
    assert report["input_miss_reasons"] == {"invalid_osrm_calls": 1}
    assert report["class_counts"]["INPUT_MISS"] == 1
    assert joined == [] and replayed == []


def test_extra_osrm_call_actual_replay_and_gate_are_osrm_miss(tmp_path, monkeypatch):
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2 import osrm_client

    rec = _record("synthetic-extra-osrm", 12)
    rec["osrm_calls"] = [
        {"kind": "route", "key": [[53.1, 23.1], [53.2, 23.2]],
         "result": {"duration_min": 5.0, "distance_km": 1.0}},
    ]
    (tmp_path / "world_record-20260711.jsonl").write_text(
        json.dumps(rec) + "\n", encoding="utf-8")
    shadow = tmp_path / "shadow.jsonl"
    shadow.write_text(json.dumps({
        "order_id": rec["order_id"], "ts": rec["ts"], "verdict": "PROPOSE",
        "reason": "ok", "best": {"courier_id": "synthetic-a", "score": 1.0},
        "alternatives": [{"courier_id": "synthetic-b", "score": 0.5}],
        "pool_feasible_count": 2, "pool_total_count": 3,
    }) + "\n", encoding="utf-8")

    seen = []

    def fake_assess(*_args, **_kwargs):
        seen.append(osrm_client.route((53.1, 23.1), (53.2, 23.2)))
        seen.append(osrm_client.route((53.1, 23.1), (53.2, 23.2)))
        return SimpleNamespace(
            verdict="PROPOSE", reason="ok",
            best=SimpleNamespace(courier_id="synthetic-a", score=1.0),
            pool_feasible_count=2, pool_total_count=3)

    monkeypatch.setattr(dp, "assess_order", fake_assess)
    replayed, misses = WR.replay_one(rec)
    assert replayed == _extract()
    assert seen[0]["duration_min"] == 5.0
    assert seen[1]["replay_miss"] is True
    assert misses == 1

    seen.clear()
    report = G.run_gate(None, None, record_dir=str(tmp_path),
                        shadow_file=str(shadow), as_of=AS_OF)
    assert report["class_counts"]["OSRM_MISS"] == 1
    assert report["missy_n"] == 1


def test_plan_snapshot_redirects_data_and_lock_together(tmp_path):
    from dispatch_v2 import common as C
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2 import plan_manager as pm

    saved = {}

    def patch(obj, name, value):
        saved.setdefault((id(obj), name), (obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    sandbox = tmp_path / "serve"
    sandbox.mkdir()
    try:
        WR._serve_live_inputs({"live_inputs": _live_inputs()}, dp, C,
                              str(sandbox), patch)
        assert Path(pm.PLANS_FILE).parent == sandbox
        assert Path(pm.LOCK_FILE).parent == sandbox
        assert Path(pm.LOCK_FILE).is_file()
        assert "/dispatch_state/" not in str(pm.LOCK_FILE)
    finally:
        for obj, name, value in saved.values():
            setattr(obj, name, value)


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
