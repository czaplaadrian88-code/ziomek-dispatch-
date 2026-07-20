"""Phase B: load-bearing tests for the full DECISION golden replay harness."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dispatch_v2.tools import golden_decision_replay as G


@dataclasses.dataclass
class _Plan:
    sequence: list[str]
    total_duration_min: float


@dataclasses.dataclass
class _Candidate:
    courier_id: str
    score: float
    plan: _Plan
    metrics: dict


@dataclasses.dataclass
class _Result:
    verdict: str
    reason: str
    best: _Candidate
    candidates: list[_Candidate]
    stage_timing: dict
    osrm_cache_age_s: float | None = None
    osrm_degraded_since_ts: float | None = None


def _result(*, score=42.0004, sequence=None, timing=1.0, winner="7"):
    best = _Candidate(
        winner,
        score,
        _Plan(sequence or ["A", "B"], 18.25),
        {
            "decision_metric": 9,
            "candidate_timing": {"wall_ms": timing},
            "r07_compute_latency_ms": timing,
            "lgbm_shadow": {
                "winner_cid": winner,
                "evaluation_ts": f"volatile-{timing}",
                "latency_ms": timing,
                "feature_compute_ms": timing,
                "inference_ms": timing,
            },
        },
    )
    return _Result(
        "PROPOSE",
        "ok",
        best,
        [best],
        {"assess_wall_ms": timing},
        osrm_cache_age_s=timing,
        osrm_degraded_since_ts=timing,
    )


def _legacy_projection(result):
    """The old gate's six-field shape cannot see plan/candidate mutations."""
    return {
        "verdict": result.verdict,
        "reason": result.reason,
        "best_cid": result.best.courier_id,
        "best_score": round(result.best.score, 3),
        "pool_feasible": None,
        "pool_total": None,
    }


def test_full_snapshot_catches_mutation_hidden_from_legacy_projection():
    before = _result(sequence=["A", "B"])
    after = _result(sequence=["B", "A"])

    assert _legacy_projection(before) == _legacy_projection(after)
    assert G.canonical_decision_bytes(before) != G.canonical_decision_bytes(after)
    paths = G._diff_paths(G.decision_snapshot(before), G.decision_snapshot(after))
    assert any("plan.sequence" in path for path in paths)


def test_exact_float_bytes_are_not_rounded_to_old_three_decimals():
    before = _result(score=42.0004)
    after = _result(score=42.00049)

    assert _legacy_projection(before) == _legacy_projection(after)
    assert G.canonical_decision_bytes(before) != G.canonical_decision_bytes(after)


def test_only_explicit_post_decision_telemetry_is_excluded():
    before = _result(timing=1.0)
    after = _result(timing=999.0)
    assert G.canonical_decision_bytes(before) == G.canonical_decision_bytes(after)

    changed_semantics = _result(timing=999.0, winner="8")
    assert G.canonical_decision_bytes(before) != G.canonical_decision_bytes(changed_semantics)


def test_canonicalizer_is_order_stable_but_container_type_strict():
    left = SimpleNamespace(payload={"b": 2, "a": 1}, route=("A", "B"))
    right = SimpleNamespace(payload={"a": 1, "b": 2}, route=("A", "B"))
    list_mutant = SimpleNamespace(payload={"a": 1, "b": 2}, route=["A", "B"])

    assert G.canonical_decision_bytes(left) == G.canonical_decision_bytes(right)
    assert G.canonical_decision_bytes(left) != G.canonical_decision_bytes(list_mutant)


def test_diff_paths_hashes_dynamic_mapping_keys():
    private_key = "private-courier-123"
    before = SimpleNamespace(assignments={private_key: {"score": 1}})
    after = SimpleNamespace(assignments={private_key: {"score": 2}})

    paths = G._diff_paths(G.decision_snapshot(before), G.decision_snapshot(after))
    rendered = json.dumps(paths)

    assert private_key not in rendered
    assert "key_sha256" in rendered


def test_unsupported_or_nonfinite_value_fails_closed():
    with pytest.raises(G.CanonicalizationError):
        G.canonical_decision_bytes(SimpleNamespace(value=object()))
    with pytest.raises(G.CanonicalizationError):
        G.canonical_decision_bytes(SimpleNamespace(value=float("nan")))


def _write_records(root: Path, rows: list[dict]) -> None:
    (root / "world_record-20260719.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def _record(oid, ts, *, schema="wr1", now=True, payload=None):
    return {
        "order_id": str(oid),
        "ts": ts,
        "now": ts if now else None,
        "schema": schema,
        "payload": payload,
    }


def test_corpus_is_frozen_deduplicated_schema_aware_and_deterministic(tmp_path):
    wr1 = _record("2", "2026-07-19T10:00:00+00:00", payload={"b": 2, "a": 1})
    wr2 = _record("3", "2026-07-19T11:00:00+00:00", schema="wr2")
    _write_records(tmp_path, [
        _record("0", "2026-07-19T08:00:00+00:00", schema="wr0"),
        _record("1", "2026-07-19T09:00:00+00:00", now=False),
        wr1,
        dict(wr1),
        wr2,
    ])

    first, first_meta = G.select_corpus(str(tmp_path), None, None, None)
    second, second_meta = G.select_corpus(str(tmp_path), None, None, None)

    assert [row["order_id"] for row in first] == ["2", "3"]
    assert first == second
    assert first_meta["sha256"] == second_meta["sha256"]
    assert first_meta["deduplicated"] == 1
    assert first_meta["skipped_no_now"] == 1
    assert first_meta["skipped_pre_or_unknown_schema"] == 1

    bounded, bounded_meta = G.select_corpus(str(tmp_path), None, None, 1)
    assert [row["order_id"] for row in bounded] == ["2"]
    assert bounded_meta["truncated"] is True
    assert bounded_meta["scan_complete"] is False
    assert bounded_meta["eligible_n"] is None


def test_reverse_worker_iterator_uses_offsets_not_a_record_list(tmp_path):
    corpus = tmp_path / "corpus.jsonl"
    rows = [
        _record("1", "2026-07-19T09:00:00+00:00"),
        _record("2", "2026-07-19T10:00:00+00:00"),
        _record("3", "2026-07-19T11:00:00+00:00"),
    ]
    corpus.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    reverse = list(G._iter_worker_records(corpus, "reverse"))
    assert [row["order_id"] for row in reverse] == ["3", "2", "1"]


def test_conflicting_duplicate_record_fails_closed(tmp_path):
    first = _record("2", "2026-07-19T10:00:00+00:00", payload=1)
    second = _record("2", "2026-07-19T10:00:00+00:00", payload=2)
    _write_records(tmp_path, [first, second])

    with pytest.raises(G.HarnessError, match="conflicting duplicate"):
        G.select_corpus(str(tmp_path), None, None, None)


def test_invalid_or_non_object_corpus_row_fails_closed(tmp_path):
    source = tmp_path / "world_record-20260719.jsonl"
    source.write_bytes(b"{broken-json\n")
    with pytest.raises(G.HarnessError, match="invalid JSON"):
        G.select_corpus(str(tmp_path), None, None, None)

    source.write_bytes(b"[]\n")
    with pytest.raises(G.HarnessError, match="non-object"):
        G.select_corpus(str(tmp_path), None, None, None)


def _row(value, *, misses=0, error=None):
    if error:
        return {"key": "unused", "error_type": error}
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return {
        "key": "unused",
        "decision_b64": base64.b64encode(raw).decode(),
        "decision_sha256": hashlib.sha256(raw).hexdigest(),
        "misses": misses,
    }


def _input_missing_row(*, misses=1):
    return {
        "key": "unused",
        "status": G.INPUT_MISSING,
        "input_reason": "osrm_replay_miss",
        "misses": misses,
    }


def _stable_runs(before, after):
    return {
        f"{side}_{order}_seed{seed}": value
        for side, value in (("before", before), ("after", after))
        for order, seed in G._STABILITY_CASES
    }


def _artifact_matrix(root, before, after, *, overrides=None):
    root.mkdir()
    overrides = overrides or {}
    artifacts = {}
    for side, value in (("before", before), ("after", after)):
        for order, seed in G._STABILITY_CASES:
            name = f"{side}_{order}_seed{seed}"
            rows = overrides.get(name, value)
            path = root / f"{name}.jsonl"
            lines = [json.dumps({"schema": G.WORKER_SCHEMA, "order": order})]
            lines.extend(
                json.dumps({**row, "key": key}, sort_keys=True, separators=(",", ":"))
                for key, row in sorted(rows.items())
            )
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            artifacts[name] = path
    return artifacts


def test_evaluator_distinguishes_parity_diff_instability_and_error():
    keys = {"sha256:k"}
    a = {"sha256:k": _row({"winner": "A"})}
    b = {"sha256:k": _row({"winner": "B"})}
    miss = {"sha256:k": _row({"winner": "A"}, misses=1)}

    assert G.evaluate_runs(keys, _stable_runs(a, a))["verdict"] == "PARITY"
    diff = G.evaluate_runs(keys, _stable_runs(a, b))
    assert diff["verdict"] == "DIFFS" and diff["cross_differences_n"] == 1
    assert diff["difference_samples"][0]["paths"] == ["$.winner"]

    unstable = _stable_runs(a, a)
    unstable["after_reverse_seed0"] = b
    unstable["after_forward_seed1"] = b
    assert G.evaluate_runs(keys, unstable)["verdict"] == "UNSTABLE"

    errored = _stable_runs(a, a)
    errored["after_reverse_seed1"] = miss
    missing = G.evaluate_runs(keys, errored)
    assert missing["verdict"] == "INPUT_MISSING"
    assert missing["cross_differences_n"] == 0
    assert missing["difference_samples"] == []

    explicitly_missing = G.evaluate_runs(
        keys, _stable_runs(a, {"sha256:k": _input_missing_row(misses=0)}))
    assert explicitly_missing["verdict"] == "INPUT_MISSING"
    assert explicitly_missing["cross_differences_n"] == 0

    worker_error = G.evaluate_runs(
        keys, _stable_runs(a, {"sha256:k": _row(None, error="RuntimeError")}))
    assert worker_error["verdict"] == "ERROR"


def test_evaluator_requires_the_full_stability_matrix():
    with pytest.raises(G.HarnessError, match="stability run set mismatch"):
        G.evaluate_runs(set(), {})


def test_disk_backed_evaluator_matches_all_verdict_classes(tmp_path):
    keys = {"sha256:k"}
    a = {"sha256:k": _row({"winner": "A"})}
    b = {"sha256:k": _row({"winner": "B"})}
    miss = {"sha256:k": _row({"winner": "A"}, misses=1)}

    parity = _artifact_matrix(tmp_path / "parity", a, a)
    assert G.evaluate_artifacts(keys, parity, tmp_path / "parity.sqlite")[
        "verdict"
    ] == "PARITY"

    changed = _artifact_matrix(tmp_path / "changed", a, b)
    diff = G.evaluate_artifacts(keys, changed, tmp_path / "changed.sqlite")
    assert diff["verdict"] == "DIFFS"
    assert diff["difference_samples"][0]["paths"] == ["$.winner"]

    unstable = _artifact_matrix(
        tmp_path / "unstable",
        a,
        a,
        overrides={"after_reverse_seed0": b},
    )
    assert G.evaluate_artifacts(keys, unstable, tmp_path / "unstable.sqlite")[
        "verdict"
    ] == "UNSTABLE"

    errored = _artifact_matrix(
        tmp_path / "errored",
        a,
        a,
        overrides={"after_reverse_seed1": miss},
    )
    missing = G.evaluate_artifacts(keys, errored, tmp_path / "errored.sqlite")
    assert missing["verdict"] == "INPUT_MISSING"
    assert missing["cross_differences_n"] == 0
    assert missing["difference_samples"] == []

    explicit = _artifact_matrix(
        tmp_path / "input-missing",
        a,
        {"sha256:k": _input_missing_row(misses=0)},
    )
    explicit_report = G.evaluate_artifacts(
        keys, explicit, tmp_path / "input-missing.sqlite")
    assert explicit_report["verdict"] == "INPUT_MISSING"
    assert explicit_report["cross_differences_n"] == 0

    worker_error = _artifact_matrix(
        tmp_path / "worker-error",
        a,
        {"sha256:k": _row(None, error="RuntimeError")},
    )
    assert G.evaluate_artifacts(
        keys, worker_error, tmp_path / "worker-error.sqlite")["verdict"] == "ERROR"


def test_diagnostic_artifact_loader_refuses_large_inputs(tmp_path):
    artifact = tmp_path / "large-artifact.jsonl"
    artifact.write_text(
        json.dumps({"schema": G.WORKER_SCHEMA, "order": "forward"}) + "\n",
        encoding="utf-8",
    )
    with artifact.open("ab") as handle:
        handle.truncate(64 * 1024 * 1024 + 1)

    with pytest.raises(G.HarnessError, match="disk-backed evaluation"):
        G._load_artifact(artifact)


def test_worker_imports_requested_tree_and_captures_full_result(tmp_path):
    tree = tmp_path / "tree"
    (tree / "tools").mkdir(parents=True)
    (tree / "__init__.py").write_text("", encoding="utf-8")
    (tree / "tools" / "__init__.py").write_text("", encoding="utf-8")
    (tree / "tools" / "world_replay.py").write_text(
        "from types import SimpleNamespace\n"
        "def _extract(value): return {}\n"
        "def replay_one(rec):\n"
        "    plan = SimpleNamespace(sequence=['A', 'B'], total_duration_min=12.5)\n"
        "    best = SimpleNamespace(courier_id=str(rec['winner']), score=1.25, plan=plan, metrics={})\n"
        "    result = SimpleNamespace(verdict='PROPOSE', best=best, candidates=[best])\n"
        "    return _extract(result), int(rec.get('misses', 0))\n",
        encoding="utf-8",
    )
    corpus = tmp_path / "corpus.jsonl"
    complete = _record("private-order-a", "2026-07-19T10:00:00+00:00") | {"winner": "7"}
    osrm_missing = _record("private-order-b", "2026-07-19T10:01:00+00:00") | {
        "winner": "8", "misses": 1}
    capture_missing = _record("private-order-c", "2026-07-19T10:02:00+00:00") | {
        "winner": "9", "capture_status": "INPUT_MISSING"}
    corpus.write_text(
        "\n".join(json.dumps(row) for row in (
            complete, osrm_missing, capture_missing)) + "\n",
        encoding="utf-8",
    )
    artifact = tmp_path / "artifact.jsonl"

    G._spawn_worker(
        python=sys.executable,
        code_tree=tree,
        corpus_file=corpus,
        artifact=artifact,
        order="forward",
        hash_seed=0,
        timeout_s=60,
    )
    rows = G._load_artifact(artifact)

    assert len(rows) == 3
    decision_rows = [row for row in rows.values() if "decision_b64" in row]
    assert len(decision_rows) == 1
    row = decision_rows[0]
    snapshot = json.loads(base64.b64decode(row["decision_b64"]))
    result = snapshot["$object"]
    best = result["best"]["$object"]
    assert best["plan"]["$object"]["sequence"] == ["A", "B"]
    assert best["score"] == 1.25
    assert "private-order" not in json.dumps({"keys": list(rows)})
    input_rows = [row for row in rows.values() if row.get("status") == "INPUT_MISSING"]
    assert len(input_rows) == 2
    assert {row["input_reason"] for row in input_rows} == {
        "osrm_replay_miss", "capture_marked_incomplete"}
    assert all("decision_b64" not in row for row in input_rows)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def _write_fake_replay(repo: Path, sequence: list[str]) -> None:
    (repo / "tools" / "world_replay.py").write_text(
        "from types import SimpleNamespace\n"
        "def _extract(value): return {}\n"
        "def replay_one(rec):\n"
        f"    plan = SimpleNamespace(sequence={sequence!r}, total_duration_min=12.5)\n"
        "    best = SimpleNamespace(courier_id='7', score=1.25, plan=plan, metrics={})\n"
        "    result = SimpleNamespace(verdict='PROPOSE', reason='ok', best=best, candidates=[best])\n"
        "    return _extract(result), 0\n",
        encoding="utf-8",
    )


def test_end_to_end_git_revisions_surface_nested_decision_mutation(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "__init__.py").write_text("", encoding="utf-8")
    (repo / "tools" / "__init__.py").write_text("", encoding="utf-8")
    _write_fake_replay(repo, ["A", "B"])
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=GDR Test", "-c", "user.email=gdr@example.invalid",
         "commit", "-qm", "before")
    before = _git(repo, "rev-parse", "HEAD")

    _write_fake_replay(repo, ["B", "A"])
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=GDR Test", "-c", "user.email=gdr@example.invalid",
         "commit", "-qm", "after")
    after = _git(repo, "rev-parse", "HEAD")

    records = tmp_path / "records"
    records.mkdir()
    _write_records(records, [_record("private-order", "2026-07-19T10:00:00+00:00")])
    report = G.run_comparison(
        repo=str(repo),
        before_ref=before,
        after_ref=after,
        record_dir=str(records),
        since=None,
        until=None,
        max_n=None,
        python=sys.executable,
        worker_timeout_s=60,
    )

    assert report["verdict"] == "DIFFS"
    assert report["cross_differences_n"] == 1
    assert report["before_unstable_n"] == report["after_unstable_n"] == 0
    assert any(
        "plan.sequence" in path
        for path in report["difference_samples"][0]["paths"]
    )
    assert "private-order" not in json.dumps(report)


def test_builtin_mutation_selftest_passes():
    result = G._selftest()
    assert result["selftest"] == "PASS"
    assert any("sequence" in path for path in result["mutation_paths"])
