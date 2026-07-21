#!/usr/bin/env python3
"""Read-only coverage replay for R-04 telemetry in decision JSONL.

The instrument is keyed by ``Candidate.courier_id``.  This tool first checks
both serializer twins in ``shadow_dispatcher.py`` and then projects how many
best-candidate rows would receive a non-null ``r04`` for a supplied suggestion
snapshot.  It never writes the input file and reports aggregates only.

When ``--suggestions`` is omitted, the latest non-null R-04 payload observed
for each candidate in the JSONL is used as a reconstructed suggestion set.
That mode is evidence of key coverage, not an exact historical replay.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, deque
import json
from pathlib import Path
from typing import Iterable, Iterator, Mapping


class SerializerIdentityError(RuntimeError):
    """Raised when either serializer no longer keys R-04 by candidate identity."""


def _is_candidate_identity_arg(node: ast.AST, variable: str) -> bool:
    """Match ``str(<variable>.courier_id or "")`` structurally."""
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "str"
        and len(node.args) == 1
        and not node.keywords
    ):
        return False
    value = node.args[0]
    return bool(
        isinstance(value, ast.BoolOp)
        and isinstance(value.op, ast.Or)
        and len(value.values) == 2
        and isinstance(value.values[0], ast.Attribute)
        and value.values[0].attr == "courier_id"
        and isinstance(value.values[0].value, ast.Name)
        and value.values[0].value.id == variable
        and isinstance(value.values[1], ast.Constant)
        and value.values[1].value == ""
    )


def _r04_calls(function: ast.FunctionDef) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == "r04"):
                continue
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "_r04_field_for_cid"
            ):
                calls.append(value)
    return calls


def verify_serializer_identity(source_path: Path) -> dict[str, str]:
    """Verify LOCATION A and B use the dataclass identity, not metrics."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"), str(source_path))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    expected = {
        "_serialize_candidate": ("c", "c.courier_id"),
        "_serialize_result": ("best", "best.courier_id"),
    }
    result: dict[str, str] = {}
    for function_name, (variable, label) in expected.items():
        function = functions.get(function_name)
        if function is None:
            raise SerializerIdentityError(f"missing serializer: {function_name}")
        calls = _r04_calls(function)
        if len(calls) != 1 or len(calls[0].args) != 1:
            raise SerializerIdentityError(
                f"{function_name}: expected exactly one R-04 lookup"
            )
        if not _is_candidate_identity_arg(calls[0].args[0], variable):
            actual = ast.unparse(calls[0].args[0])
            raise SerializerIdentityError(
                f"{function_name}: R-04 lookup is {actual!r}, expected {label}"
            )
        result[function_name] = label
    return result


def _candidate_rows(record: Mapping[str, object]) -> Iterator[Mapping[str, object]]:
    best = record.get("best")
    if isinstance(best, dict):
        yield best
    alternatives = record.get("alternatives")
    if isinstance(alternatives, list):
        for candidate in alternatives:
            if isinstance(candidate, dict):
                yield candidate


def _candidate_id(candidate: Mapping[str, object]) -> str:
    value = candidate.get("courier_id")
    return "" if value is None else str(value)


def _embedded_suggestions(
    records: Iterable[Mapping[str, object]],
) -> tuple[dict[str, dict], int]:
    """Reconstruct only identity-consistent suggestions already present in JSONL."""
    suggestions: dict[str, dict] = {}
    rejected_identity_mismatches = 0
    for record in records:
        for candidate in _candidate_rows(record):
            cid = _candidate_id(candidate)
            payload = candidate.get("r04")
            if not cid or not isinstance(payload, dict) or not payload:
                continue
            payload_cid = payload.get("courier_id")
            if payload_cid is None or str(payload_cid) != cid:
                rejected_identity_mismatches += 1
                continue
            suggestions[cid] = payload
    return suggestions, rejected_identity_mismatches


def load_suggestions(path: Path) -> dict[str, dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("suggestion snapshot must be a JSON object")
    nested = raw.get("suggestions")
    if isinstance(nested, dict):
        raw = nested
    return {
        str(cid): payload
        for cid, payload in raw.items()
        if cid != "_meta" and isinstance(payload, dict) and payload
    }


def replay_r04_field_for_cid(
    cid: str, suggestions: Mapping[str, dict]
) -> dict | None:
    """Replay the null/non-null contract of production ``_r04_field_for_cid``."""
    if not cid:
        return None
    suggestion = suggestions.get(str(cid))
    return suggestion if isinstance(suggestion, dict) and suggestion else None


def _current_status(best: Mapping[str, object]) -> str:
    if "r04" not in best:
        return "missing"
    payload = best.get("r04")
    if payload is None:
        return "null"
    return "filled" if isinstance(payload, dict) and payload else "invalid"


def replay_snapshot(
    jsonl_path: Path,
    *,
    last_n: int,
    suggestions_path: Path | None = None,
) -> dict[str, object]:
    if last_n <= 0:
        raise ValueError("last_n must be positive")

    window: deque[dict] = deque(maxlen=last_n)
    parsed = 0
    malformed = 0
    embedded: dict[str, dict] = {}
    rejected = 0
    with jsonl_path.open("r", encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(record, dict):
                malformed += 1
                continue
            parsed += 1
            window.append(record)
            if suggestions_path is None:
                observed, mismatches = _embedded_suggestions((record,))
                embedded.update(observed)
                rejected += mismatches

    if suggestions_path is None:
        suggestions = embedded
        provenance = "embedded_r04_latest_projection"
        direct_snapshot = False
    else:
        suggestions = load_suggestions(suggestions_path)
        provenance = "explicit_tier_suggestions_snapshot"
        direct_snapshot = True

    current = Counter()
    would_fill = 0
    newly_fillable = 0
    candidate_ids: set[str] = set()
    matched_ids: set[str] = set()
    missing_candidate_id = 0
    for record in window:
        best = record.get("best")
        if not isinstance(best, dict):
            current["missing_best"] += 1
            continue
        status = _current_status(best)
        current[status] += 1
        cid = _candidate_id(best)
        if not cid:
            missing_candidate_id += 1
            continue
        candidate_ids.add(cid)
        if replay_r04_field_for_cid(cid, suggestions) is not None:
            would_fill += 1
            matched_ids.add(cid)
            if status != "filled":
                newly_fillable += 1

    rows = len(window)
    return {
        "input": {
            "records_parsed": parsed,
            "malformed_or_non_object_lines": malformed,
            "records_in_window": rows,
            "requested_last_n": last_n,
        },
        "suggestion_source": {
            "mode": provenance,
            "direct_mapping_snapshot": direct_snapshot,
            "historical_timing_reconstructed": False,
            "suggestion_ids": len(suggestions),
            "rejected_identity_mismatches": rejected,
        },
        "current_r04": dict(sorted(current.items())),
        "projection": {
            "would_be_filled": would_fill,
            "newly_fillable_vs_current_record": newly_fillable,
            "would_remain_unfilled": rows - would_fill,
            "coverage_pct": round(100.0 * would_fill / rows, 2) if rows else 0.0,
            "unique_best_candidate_ids": len(candidate_ids),
            "matched_unique_candidate_ids": len(matched_ids),
            "missing_best_candidate_id": missing_candidate_id,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only R-04 serializer-key and JSONL coverage replay"
    )
    parser.add_argument("jsonl", type=Path, help="read-only shadow_decisions JSONL")
    parser.add_argument("--last", type=int, default=719, dest="last_n")
    parser.add_argument(
        "--suggestions",
        type=Path,
        help="optional tier_suggestions.json snapshot; otherwise derive from JSONL",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "shadow_dispatcher.py",
        help="shadow_dispatcher.py checked for LOCATION A+B identity lookups",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        serializer_guard = verify_serializer_identity(args.source)
        result = replay_snapshot(
            args.jsonl,
            last_n=args.last_n,
            suggestions_path=args.suggestions,
        )
    except (OSError, ValueError, SerializerIdentityError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    result["serializer_guard"] = serializer_guard
    result["ok"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
