#!/usr/bin/env python3
"""Golden, two-revision DECISION replay over one frozen ``world_record`` corpus.

The existing ``world_replay_gate`` checks a compact six-field projection against
the live shadow ledger.  That is useful operationally, but it is not sufficient
to prove that an engine refactor preserves the complete decision object.  This
tool freezes the corpus once, materializes two Git revisions, runs each revision
in an isolated child process, and compares canonical UTF-8 decision bytes.

Each side is evaluated over the full forward/reverse x hash-seed 0/1 matrix.  A
side which changes with record order/hash seed is ``UNSTABLE`` and cannot
certify a refactor.  OSRM/capture gaps become ``INPUT_MISSING`` and are never
compared as decisions; empty or corrupt corpora, replay exceptions, unsupported
values, corrupt artifacts, and unequal record sets also fail closed.
The final report is aggregate-only; raw order/courier identifiers and decision
values remain in ephemeral artifacts and are never printed.

This is a build-time proof tool.  It never merges, deploys, flips flags, restarts
services, or writes to production state.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import dataclasses
import enum
import hashlib
import io
import json
import logging
import math
import os
import re
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional


SCHEMA = "golden_decision_replay.v2"
WORKER_SCHEMA = "golden_decision_replay.worker.v2"
INPUT_MISSING = "INPUT_MISSING"
DEFAULT_RECORD_DIR = "/root/.openclaw/workspace/dispatch_state/world_record"
_WR_SCHEMA_RE = re.compile(r"^wr([1-9][0-9]*)$")

# These values are explicitly post-decision/runtime telemetry.  They are not
# consumed by selection, feasibility, scoring, or the proposal payload.  The
# allowlist is deliberately narrow: a new volatile field makes the harness
# unstable until it is investigated and explicitly classified.
EXCLUDED_RESULT_FIELDS = frozenset({
    "stage_timing",
    "osrm_cache_age_s",
    "osrm_degraded_since_ts",
})
EXCLUDED_METRIC_FIELDS = frozenset({
    "candidate_timing",
    "r07_compute_latency_ms",
})
_MODEL_SHADOW_CONTAINERS = frozenset({"lgbm_shadow", "lgbm_twomodel_shadow"})
EXCLUDED_MODEL_TELEMETRY_FIELDS = frozenset({
    "evaluation_ts",
    "latency_ms",
    "feature_compute_ms",
    "inference_ms",
})
_STABILITY_CASES = (
    ("forward", 0),
    ("reverse", 0),
    ("forward", 1),
    ("reverse", 1),
)


class HarnessError(RuntimeError):
    """Controlled, non-record-bearing harness failure."""


class CanonicalizationError(TypeError):
    """A decision contains a value without an explicit stable encoding."""


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _excluded(path: tuple[str, ...], key: str) -> bool:
    if path == ("result",) and key in EXCLUDED_RESULT_FIELDS:
        return True
    if path and path[-1] == "metrics" and key in EXCLUDED_METRIC_FIELDS:
        return True
    if key in EXCLUDED_MODEL_TELEMETRY_FIELDS and any(
        part in _MODEL_SHADOW_CONTAINERS for part in path
    ):
        return True
    return False


def _mapping_key(value: Any, path: tuple[str, ...]) -> str:
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (bool, int)):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, float) and math.isfinite(value):
        return json.dumps(value, allow_nan=False)
    raise CanonicalizationError(
        f"unsupported mapping key type at {'.'.join(path) or '$'}: "
        f"{type(value).__name__}"
    )


def _canonicalize(value: Any, path: tuple[str, ...], active: set[int]) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError(
                f"non-finite float at {'.'.join(path) or '$'}"
            )
        return value
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, time):
        return {"$time": value.isoformat()}
    if isinstance(value, Path):
        return {"$path": str(value)}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, enum.Enum):
        return {"$enum": _canonicalize(value.value, path + ("$enum",), active)}
    if isinstance(value, (bytes, bytearray)):
        return {"$bytes_b64": base64.b64encode(bytes(value)).decode("ascii")}

    oid = id(value)
    if oid in active:
        raise CanonicalizationError(
            f"cyclic decision value at {'.'.join(path) or '$'}"
        )

    if isinstance(value, Mapping):
        active.add(oid)
        try:
            out: dict[str, Any] = {}
            for raw_key, raw_value in value.items():
                key = _mapping_key(raw_key, path)
                if key in out:
                    raise CanonicalizationError(
                        f"mapping key collision at {'.'.join(path) or '$'}: {key}"
                    )
                if _excluded(path, key):
                    continue
                out[key] = _canonicalize(raw_value, path + (key,), active)
            return {"$mapping": out}
        finally:
            active.remove(oid)

    if isinstance(value, list):
        active.add(oid)
        try:
            return [
                _canonicalize(item, path + (f"[{idx}]",), active)
                for idx, item in enumerate(value)
            ]
        finally:
            active.remove(oid)

    if isinstance(value, tuple):
        active.add(oid)
        try:
            return {
                "$tuple": [
                    _canonicalize(item, path + (f"[{idx}]",), active)
                    for idx, item in enumerate(value)
                ]
            }
        finally:
            active.remove(oid)

    if isinstance(value, (set, frozenset)):
        active.add(oid)
        try:
            items = [_canonicalize(item, path + ("{$item}",), active) for item in value]
            items.sort(key=_json_bytes)
            return {"$set" if isinstance(value, set) else "$frozenset": items}
        finally:
            active.remove(oid)

    fields: dict[str, Any] = {}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            fields[field.name] = getattr(value, field.name)
        try:
            fields.update({k: v for k, v in vars(value).items() if k not in fields})
        except TypeError:
            pass
    elif hasattr(value, "_asdict") and callable(value._asdict):
        fields = dict(value._asdict())
    elif hasattr(value, "__dict__"):
        fields = dict(vars(value))
    else:
        raise CanonicalizationError(
            f"unsupported decision value at {'.'.join(path) or '$'}: "
            f"{type(value).__name__}"
        )

    active.add(oid)
    try:
        out = {}
        for key in sorted(fields):
            if not isinstance(key, str):
                raise CanonicalizationError(
                    f"non-string object field at {'.'.join(path) or '$'}"
                )
            if _excluded(path, key):
                continue
            out[key] = _canonicalize(fields[key], path + (key,), active)
        return {"$object": out}
    finally:
        active.remove(oid)


def decision_snapshot(result: Any) -> Any:
    """Return the complete, canonicalizable decision object.

    Class/module names are intentionally absent so moving a dataclass without
    changing its data does not create a false behavioral difference.  Object,
    mapping, tuple, and set kinds remain explicitly tagged because they can
    affect callers.
    """

    return _canonicalize(result, ("result",), set())


def canonical_decision_bytes(result: Any) -> bytes:
    return _json_bytes(decision_snapshot(result))


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _decode_jsonl_object(line: bytes) -> dict[str, Any]:
    try:
        value = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HarnessError("invalid JSON in corpus") from exc
    if not isinstance(value, dict):
        raise HarnessError("non-object JSON value in corpus")
    return value


def _iter_jsonl(path: Path):
    with path.open("rb") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            yield _decode_jsonl_object(line)


def _iter_worker_records(path: Path, order: str):
    """Stream corpus records without materializing their multi-GB payloads.

    Reverse replay keeps only line offsets (a few integers per record), then
    seeks to each line.  The record itself is decoded one at a time.
    """

    if order == "forward":
        yield from _iter_jsonl(path)
        return
    if order != "reverse":
        raise HarnessError("worker order must be forward or reverse")
    offsets: list[int] = []
    with path.open("rb") as handle:
        while True:
            offset = handle.tell()
            line = handle.readline()
            if not line:
                break
            if line.strip():
                offsets.append(offset)
        for offset in reversed(offsets):
            handle.seek(offset)
            line = handle.readline().strip()
            yield _decode_jsonl_object(line)


def _record_identity(record: dict) -> tuple[str, str]:
    return str(record.get("order_id") or ""), str(record.get("ts") or "")


def record_key(record: dict) -> str:
    raw = _json_bytes({"order_id": _record_identity(record)[0], "ts": _record_identity(record)[1]})
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def freeze_corpus(
    record_dir: str,
    since: Optional[datetime],
    until: Optional[datetime],
    max_n: Optional[int],
    destination: Path,
) -> tuple[dict[str, Any], set[str]]:
    """Stream one deterministic corpus to disk and return metadata + keys.

    A bounded engineering run stops after discovering the first record beyond
    ``max_n`` and marks the scan as incomplete/truncated.  An unbounded
    certification run scans every source line, still retaining only identity
    hashes in memory.
    """

    if max_n is not None and max_n <= 0:
        raise HarnessError("max_n must be positive")
    root = Path(record_dir)
    if not root.is_dir():
        raise HarnessError("record_dir is not a directory")

    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".tmp")
    counters: Counter[str] = Counter()
    seen: dict[tuple[str, str], bytes] = {}
    selected_keys: set[str] = set()
    corpus_hash = hashlib.sha256()
    selected_n = 0
    eligible_seen = 0
    truncated = False
    scan_complete = True

    try:
        with tmp.open("wb") as output:
            stop = False
            for path in sorted(root.glob("world_record-*.jsonl")):
                for record in _iter_jsonl(path):
                    ts = _parse_dt(record.get("ts"))
                    if ts is None:
                        counters["skipped_bad_ts"] += 1
                        continue
                    if since is not None and ts < since:
                        continue
                    if until is not None and ts > until:
                        continue
                    if not record.get("now"):
                        counters["skipped_no_now"] += 1
                        continue
                    schema = record.get("schema")
                    if not isinstance(schema, str) or not _WR_SCHEMA_RE.fullmatch(schema):
                        counters["skipped_pre_or_unknown_schema"] += 1
                        continue

                    identity = _record_identity(record)
                    encoded = _json_bytes(record)
                    digest = hashlib.sha256(encoded).digest()
                    previous = seen.get(identity)
                    if previous is not None:
                        if previous != digest:
                            raise HarnessError("conflicting duplicate world_record identity")
                        counters["deduplicated"] += 1
                        continue
                    seen[identity] = digest
                    eligible_seen += 1

                    if max_n is not None and selected_n >= max_n:
                        truncated = True
                        scan_complete = False
                        stop = True
                        break

                    key = record_key(record)
                    if key in selected_keys:
                        raise HarnessError("record-key collision")
                    selected_keys.add(key)
                    line = encoded + b"\n"
                    output.write(line)
                    corpus_hash.update(line)
                    selected_n += 1
                if stop:
                    break
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp, destination)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    meta = {
        "n": selected_n,
        "eligible_n": eligible_seen if scan_complete else None,
        "eligible_n_lower_bound": eligible_seen,
        "sha256": corpus_hash.hexdigest(),
        "truncated": truncated,
        "scan_complete": scan_complete,
        "selection": "sorted_file_then_line",
        **dict(sorted(counters.items())),
    }
    return meta, selected_keys


def select_corpus(
    record_dir: str,
    since: Optional[datetime],
    until: Optional[datetime],
    max_n: Optional[int],
) -> tuple[list[dict], dict[str, Any]]:
    """Small-corpus convenience wrapper used by tests and diagnostics."""

    with tempfile.TemporaryDirectory(prefix="gdr-select-") as temp_root:
        frozen = Path(temp_root) / "corpus.jsonl"
        meta, _ = freeze_corpus(record_dir, since, until, max_n, frozen)
        if frozen.stat().st_size > 64 * 1024 * 1024:
            raise HarnessError(
                "select_corpus convenience wrapper refuses corpora over 64 MiB; "
                "use freeze_corpus for streaming"
            )
        return list(_iter_jsonl(frozen)), meta


@contextlib.contextmanager
def _suppress_transitive_output():
    previous_disable = logging.root.manager.disable
    with open(os.devnull, "w", encoding="utf-8") as sink:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            logging.disable(logging.CRITICAL)
            try:
                yield
            finally:
                logging.disable(previous_disable)


def _worker_run(code_tree: str, corpus_file: str, artifact: str, order: str) -> int:
    if order not in {"forward", "reverse"}:
        raise HarnessError("worker order must be forward or reverse")
    tree = Path(code_tree).resolve()
    if not (tree / "__init__.py").is_file() or not (tree / "tools" / "world_replay.py").is_file():
        raise HarnessError("code tree is not a dispatch_v2 checkout")

    artifact_path = Path(artifact)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = artifact_path.with_name(artifact_path.name + ".tmp")

    with tempfile.TemporaryDirectory(prefix="gdr-worker-") as temp_root:
        temp = Path(temp_root)
        import_root = temp / "pkgroot"
        import_root.mkdir()
        os.symlink(tree, import_root / "dispatch_v2", target_is_directory=True)
        flags_path = temp / "bootstrap-flags.json"
        flags_path.write_text("{}\n", encoding="utf-8")
        os.environ["DISPATCH_FLAGS_PATH"] = str(flags_path)
        os.environ["DISPATCH_UNDER_PYTEST"] = "1"
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        os.environ["TMPDIR"] = str(temp)
        sys.path.insert(0, str(import_root))
        os.chdir(temp)

        from dispatch_v2.tools import world_replay as replay  # noqa: PLC0415

        package_root = Path(sys.modules["dispatch_v2"].__file__).resolve().parent
        if package_root != tree:
            raise HarnessError("worker imported dispatch_v2 from the wrong tree")

        original_extract = replay._extract
        replay._extract = decision_snapshot
        try:
            with tmp_path.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps({"schema": WORKER_SCHEMA, "order": order}) + "\n")
                with _suppress_transitive_output():
                    for record in _iter_worker_records(Path(corpus_file), order):
                        key = record_key(record)
                        if record.get("capture_status") == INPUT_MISSING:
                            row = {
                                "key": key,
                                "status": INPUT_MISSING,
                                "input_reason": "capture_marked_incomplete",
                                "misses": 0,
                            }
                            handle.write(json.dumps(
                                row, sort_keys=True, separators=(",", ":")) + "\n")
                            continue
                        try:
                            snapshot, misses = replay.replay_one(record)
                            if misses:
                                row = {
                                    "key": key,
                                    "status": INPUT_MISSING,
                                    "input_reason": "osrm_replay_miss",
                                    "misses": int(misses),
                                }
                            else:
                                decision = _json_bytes(snapshot)
                                row = {
                                    "key": key,
                                    "decision_b64": base64.b64encode(decision).decode("ascii"),
                                    "decision_sha256": hashlib.sha256(decision).hexdigest(),
                                    "misses": 0,
                                }
                        except Exception as exc:  # no message: it may contain identifiers
                            incomplete_type = getattr(replay, "IncompleteReplayInput", None)
                            if incomplete_type is not None and isinstance(exc, incomplete_type):
                                row = {
                                    "key": key,
                                    "status": INPUT_MISSING,
                                    "input_reason": "replay_input_incomplete",
                                    "misses": 0,
                                }
                            else:
                                row = {"key": key, "error_type": type(exc).__name__}
                        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, artifact_path)
        finally:
            replay._extract = original_extract
    return 0


def _spawn_worker(
    *,
    python: str,
    code_tree: Path,
    corpus_file: Path,
    artifact: Path,
    order: str,
    hash_seed: int,
    timeout_s: float,
) -> None:
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = str(hash_seed)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    command = [
        python,
        str(Path(__file__).resolve()),
        "--_worker",
        "--code-tree",
        str(code_tree),
        "--corpus-file",
        str(corpus_file),
        "--artifact",
        str(artifact),
        "--order",
        order,
    ]
    try:
        completed = subprocess.run(
            command,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HarnessError("worker timeout") from exc
    if completed.returncode != 0 or not artifact.is_file():
        raise HarnessError(f"worker failed with exit {completed.returncode}")


def _iter_artifact(path: Path):
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        first = handle.readline()
        try:
            header = json.loads(first)
        except json.JSONDecodeError as exc:
            raise HarnessError("invalid worker artifact header") from exc
        if not isinstance(header, dict) or header.get("schema") != WORKER_SCHEMA:
            raise HarnessError("worker artifact schema mismatch")
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise HarnessError("invalid worker artifact row") from exc
            if not isinstance(row, dict):
                raise HarnessError("non-object worker artifact row")
            key = row.get("key")
            if not isinstance(key, str) or key in seen:
                raise HarnessError("invalid or duplicate worker record key")
            seen.add(key)
            if row.get("status") == INPUT_MISSING:
                if "decision_b64" in row or "error_type" in row:
                    raise HarnessError("ambiguous input-missing worker row")
                misses = row.get("misses", 0)
                if isinstance(misses, bool) or not isinstance(misses, int) or misses < 0:
                    raise HarnessError("invalid input-missing miss count")
                if not isinstance(row.get("input_reason"), str):
                    raise HarnessError("invalid input-missing reason")
                raw = None
            elif "status" in row:
                raise HarnessError("unknown worker row status")
            elif "decision_b64" in row:
                try:
                    raw = base64.b64decode(row["decision_b64"], validate=True)
                except Exception as exc:
                    raise HarnessError("invalid decision base64") from exc
                if hashlib.sha256(raw).hexdigest() != row.get("decision_sha256"):
                    raise HarnessError("decision artifact hash mismatch")
                misses = row.get("misses")
                if isinstance(misses, bool) or not isinstance(misses, int):
                    raise HarnessError("invalid decision artifact miss count")
            elif not isinstance(row.get("error_type"), str):
                raise HarnessError("worker row has neither decision nor error")
            else:
                raw = None
            yield key, row, raw


def _load_artifact(path: Path) -> dict[str, dict[str, Any]]:
    """Load a small artifact for tests/diagnostics, never certification runs."""

    if path.stat().st_size > 64 * 1024 * 1024:
        raise HarnessError(
            "_load_artifact refuses artifacts over 64 MiB; "
            "use disk-backed evaluation"
    )
    rows: dict[str, dict[str, Any]] = {}
    for key, row, _ in _iter_artifact(path):
        rows[key] = row
    return rows


def _row_diff(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if _row_input_missing(left) or _row_input_missing(right):
        return False
    if left.get("error_type") != right.get("error_type"):
        return True
    if "error_type" in left or "error_type" in right:
        return False
    return (
        left.get("decision_b64") != right.get("decision_b64")
        or left.get("misses") != right.get("misses")
    )


def _row_input_missing(row: dict[str, Any]) -> bool:
    """Nowy status oraz legacy misses są jednym fail-closed kontraktem."""
    return row.get("status") == INPUT_MISSING or int(row.get("misses") or 0) != 0


def _diff_paths(left: Any, right: Any, path: str = "$", limit: int = 24) -> list[str]:
    found: list[str] = []

    def private_key_path(current: str, key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
        return f"{current}.{{key_sha256:{digest}}}"

    def walk_object(a: dict, b: dict, current: str) -> None:
        for key in sorted(set(a) | set(b)):
            child = f"{current}.{key}"
            if key not in a or key not in b:
                found.append(child)
            else:
                walk(a[key], b[key], child)
            if len(found) >= limit:
                return

    def walk_mapping(a: dict, b: dict, current: str) -> None:
        for key in sorted(set(a) | set(b)):
            child = private_key_path(current, key)
            if key not in a or key not in b:
                found.append(child)
            else:
                walk(a[key], b[key], child)
            if len(found) >= limit:
                return

    def walk(a: Any, b: Any, current: str) -> None:
        if len(found) >= limit:
            return
        if type(a) is not type(b):
            found.append(current)
            return
        if isinstance(a, dict):
            if set(a) == {"$object"} and set(b) == {"$object"}:
                walk_object(a["$object"], b["$object"], current)
                return
            if set(a) == {"$mapping"} and set(b) == {"$mapping"}:
                walk_mapping(a["$mapping"], b["$mapping"], current)
                return
            for key in sorted(set(a) | set(b)):
                child = f"{current}.{key}"
                if key not in a or key not in b:
                    found.append(child)
                else:
                    walk(a[key], b[key], child)
                if len(found) >= limit:
                    return
            return
        if isinstance(a, list):
            if len(a) != len(b):
                found.append(f"{current}.$length")
            for idx, (av, bv) in enumerate(zip(a, b)):
                walk(av, bv, f"{current}[{idx}]")
                if len(found) >= limit:
                    return
            return
        if a != b:
            found.append(current)

    walk(left, right, path)
    return found


def _sample_diff(
    key: str,
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    sample = {
        "record_key": key,
        "before_sha256": left.get("decision_sha256"),
        "after_sha256": right.get("decision_sha256"),
    }
    if "decision_b64" in left and "decision_b64" in right:
        before = json.loads(base64.b64decode(left["decision_b64"]))
        after = json.loads(base64.b64decode(right["decision_b64"]))
        sample["paths"] = _diff_paths(before, after)
    else:
        sample["paths"] = ["$worker_status"]
    return sample


def _run_name(side: str, order: str, seed: int) -> str:
    return f"{side}_{order}_seed{seed}"


def evaluate_runs(
    expected_keys: set[str],
    runs: Mapping[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    expected_runs = {
        _run_name(side, order, seed)
        for side in ("before", "after")
        for order, seed in _STABILITY_CASES
    }
    if set(runs) != expected_runs:
        raise HarnessError("stability run set mismatch")
    key_mismatches = {
        name: {"missing": len(expected_keys - set(rows)), "extra": len(set(rows) - expected_keys)}
        for name, rows in runs.items()
        if set(rows) != expected_keys
    }
    errors = {
        name: dict(sorted(Counter(
            row["error_type"] for row in rows.values() if "error_type" in row
        ).items()))
        for name, rows in runs.items()
    }
    errors = {name: value for name, value in errors.items() if value}
    input_missing = {
        name: sum(1 for row in rows.values() if _row_input_missing(row))
        for name, rows in runs.items()
    }
    input_missing = {name: value for name, value in input_missing.items() if value}
    osrm_misses = {
        name: sum(1 for row in rows.values() if int(row.get("misses") or 0) != 0)
        for name, rows in runs.items()
    }
    osrm_misses = {name: value for name, value in osrm_misses.items() if value}

    def changed_keys(left: dict[str, dict[str, Any]], right: dict[str, dict[str, Any]]) -> list[str]:
        return sorted(
            key for key in expected_keys
            if key in left and key in right and _row_diff(left[key], right[key])
        )

    def unstable_keys(side: str) -> list[str]:
        baseline = runs[_run_name(side, "forward", 0)]
        changed: set[str] = set()
        for order, seed in _STABILITY_CASES[1:]:
            changed.update(changed_keys(baseline, runs[_run_name(side, order, seed)]))
        return sorted(changed)

    before_unstable = unstable_keys("before")
    after_unstable = unstable_keys("after")
    before_baseline = runs[_run_name("before", "forward", 0)]
    after_baseline = runs[_run_name("after", "forward", 0)]
    cross_diffs = changed_keys(before_baseline, after_baseline)

    if key_mismatches or errors:
        verdict = "ERROR"
    elif input_missing:
        verdict = INPUT_MISSING
    elif before_unstable or after_unstable:
        verdict = "UNSTABLE"
    elif cross_diffs:
        verdict = "DIFFS"
    elif not expected_keys:
        verdict = "EMPTY_CORPUS"
    else:
        verdict = "PARITY"

    samples = [
        _sample_diff(key, before_baseline[key], after_baseline[key])
        for key in cross_diffs[:12]
    ]
    return {
        "verdict": verdict,
        "compared_n": len(expected_keys),
        "cross_differences_n": len(cross_diffs),
        "before_unstable_n": len(before_unstable),
        "after_unstable_n": len(after_unstable),
        "key_mismatches": key_mismatches,
        "errors": errors,
        "input_missing_records": input_missing,
        "osrm_miss_records": osrm_misses,
        "difference_samples": samples,
    }


def _stored_row(
    row: dict[str, Any],
    decision: Optional[bytes],
) -> tuple[Optional[bytes], Optional[str], int, Optional[str], Optional[str]]:
    return (
        decision,
        row.get("decision_sha256"),
        int(row.get("misses") or 0),
        row.get("error_type"),
        row.get("status"),
    )


def _stored_row_diff(
    left: tuple[Optional[bytes], Optional[str], int, Optional[str], Optional[str]],
    right: tuple[Optional[bytes], Optional[str], int, Optional[str], Optional[str]],
) -> bool:
    if _stored_row_input_missing(left) or _stored_row_input_missing(right):
        return False
    if left[3] != right[3]:
        return True
    if left[3] is not None or right[3] is not None:
        return False
    return left[0] != right[0] or left[2] != right[2]


def _stored_row_input_missing(
    row: tuple[Optional[bytes], Optional[str], int, Optional[str], Optional[str]],
) -> bool:
    return row[4] == INPUT_MISSING or row[2] != 0


def _sample_stored_diff(
    key: str,
    left: tuple[Optional[bytes], Optional[str], int, Optional[str], Optional[str]],
    right: tuple[Optional[bytes], Optional[str], int, Optional[str], Optional[str]],
) -> dict[str, Any]:
    sample = {
        "record_key": key,
        "before_sha256": left[1],
        "after_sha256": right[1],
    }
    if left[0] is not None and right[0] is not None:
        sample["paths"] = _diff_paths(json.loads(left[0]), json.loads(right[0]))
    else:
        sample["paths"] = ["$worker_status"]
    return sample


def evaluate_artifacts(
    expected_keys: set[str],
    artifacts: Mapping[str, Path],
    database: Path,
) -> dict[str, Any]:
    """Compare large artifacts exactly with only two baselines stored on disk."""

    expected_runs = {
        _run_name(side, order, seed)
        for side in ("before", "after")
        for order, seed in _STABILITY_CASES
    }
    if set(artifacts) != expected_runs:
        raise HarnessError("stability artifact set mismatch")
    if database.exists():
        raise HarnessError("evaluation database already exists")

    key_mismatches: dict[str, dict[str, int]] = {}
    errors: dict[str, dict[str, int]] = {}
    input_missing: dict[str, int] = {}
    osrm_misses: dict[str, int] = {}
    unstable: dict[str, set[str]] = {"before": set(), "after": set()}

    def finish_run(
        name: str,
        seen: set[str],
        error_counts: Counter[str],
        input_missing_count: int,
        osrm_miss_count: int,
    ) -> None:
        if seen != expected_keys:
            key_mismatches[name] = {
                "missing": len(expected_keys - seen),
                "extra": len(seen - expected_keys),
            }
        if error_counts:
            errors[name] = dict(sorted(error_counts.items()))
        if input_missing_count:
            input_missing[name] = input_missing_count
        if osrm_miss_count:
            osrm_misses[name] = osrm_miss_count

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute(
            """
            CREATE TABLE baselines (
                side TEXT NOT NULL,
                record_key TEXT NOT NULL,
                decision BLOB,
                decision_sha256 TEXT,
                misses INTEGER NOT NULL,
                error_type TEXT,
                status TEXT,
                PRIMARY KEY (side, record_key)
            )
            """
        )

        for side in ("before", "after"):
            baseline_name = _run_name(side, "forward", 0)
            seen: set[str] = set()
            error_counts: Counter[str] = Counter()
            input_missing_count = 0
            osrm_miss_count = 0
            with connection:
                for key, row, decision in _iter_artifact(artifacts[baseline_name]):
                    seen.add(key)
                    stored = _stored_row(row, decision)
                    if _stored_row_input_missing(stored):
                        input_missing_count += 1
                        if stored[2] != 0:
                            osrm_miss_count += 1
                    elif stored[3] is not None:
                        error_counts[stored[3]] += 1
                    connection.execute(
                        "INSERT INTO baselines VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (side, key, *stored),
                    )
            finish_run(baseline_name, seen, error_counts,
                       input_missing_count, osrm_miss_count)

            for order, seed in _STABILITY_CASES[1:]:
                name = _run_name(side, order, seed)
                seen = set()
                error_counts = Counter()
                input_missing_count = 0
                osrm_miss_count = 0
                for key, row, decision in _iter_artifact(artifacts[name]):
                    seen.add(key)
                    current = _stored_row(row, decision)
                    if _stored_row_input_missing(current):
                        input_missing_count += 1
                        if current[2] != 0:
                            osrm_miss_count += 1
                    elif current[3] is not None:
                        error_counts[current[3]] += 1
                    baseline = connection.execute(
                        """
                        SELECT decision, decision_sha256, misses, error_type, status
                        FROM baselines WHERE side = ? AND record_key = ?
                        """,
                        (side, key),
                    ).fetchone()
                    if baseline is not None and _stored_row_diff(baseline, current):
                        unstable[side].add(key)
                finish_run(name, seen, error_counts,
                           input_missing_count, osrm_miss_count)

        cross_diffs: list[str] = []
        samples: list[dict[str, Any]] = []
        for key in sorted(expected_keys):
            before = connection.execute(
                """
                SELECT decision, decision_sha256, misses, error_type, status
                FROM baselines WHERE side = 'before' AND record_key = ?
                """,
                (key,),
            ).fetchone()
            after = connection.execute(
                """
                SELECT decision, decision_sha256, misses, error_type, status
                FROM baselines WHERE side = 'after' AND record_key = ?
                """,
                (key,),
            ).fetchone()
            if before is None or after is None or not _stored_row_diff(before, after):
                continue
            cross_diffs.append(key)
            if len(samples) < 12:
                samples.append(_sample_stored_diff(key, before, after))

    if key_mismatches or errors:
        verdict = "ERROR"
    elif input_missing:
        verdict = INPUT_MISSING
    elif unstable["before"] or unstable["after"]:
        verdict = "UNSTABLE"
    elif cross_diffs:
        verdict = "DIFFS"
    elif not expected_keys:
        verdict = "EMPTY_CORPUS"
    else:
        verdict = "PARITY"

    return {
        "verdict": verdict,
        "compared_n": len(expected_keys),
        "cross_differences_n": len(cross_diffs),
        "before_unstable_n": len(unstable["before"]),
        "after_unstable_n": len(unstable["after"]),
        "key_mismatches": key_mismatches,
        "errors": errors,
        "input_missing_records": input_missing,
        "osrm_miss_records": osrm_misses,
        "difference_samples": samples,
    }


def _git_output(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise HarnessError(f"git command failed: {' '.join(args[:2])}")
    return completed.stdout.decode("utf-8", errors="strict").strip()


def _resolve_commit(repo: Path, ref: str) -> str:
    return _git_output(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")


def _materialize_commit(repo: Path, ref: str, destination: Path) -> str:
    sha = _resolve_commit(repo, ref)
    completed = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", sha],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise HarnessError("git archive failed")
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(completed.stdout), mode="r:") as archive:
        archive.extractall(destination, filter="data")
    if not (destination / "__init__.py").is_file():
        raise HarnessError("materialized revision is not a dispatch_v2 repository")
    return sha


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".gdr-", delete=False) as handle:
        tmp = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise


def run_comparison(
    *,
    repo: str,
    before_ref: str,
    after_ref: str,
    record_dir: str,
    since: Optional[datetime],
    until: Optional[datetime],
    max_n: Optional[int],
    python: str,
    worker_timeout_s: float,
) -> dict[str, Any]:
    repo_path = Path(repo).resolve()
    if not repo_path.is_dir():
        raise HarnessError("repo is not a directory")
    if worker_timeout_s <= 0:
        raise HarnessError("worker timeout must be positive")
    with tempfile.TemporaryDirectory(prefix="golden-decision-replay-") as temp_root:
        temp = Path(temp_root)
        corpus_path = temp / "frozen-world-record.jsonl"
        corpus_meta, expected_keys = freeze_corpus(
            record_dir, since, until, max_n, corpus_path
        )
        if not expected_keys:
            return {
                "schema": SCHEMA,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "verdict": "EMPTY_CORPUS",
                "corpus": corpus_meta,
            }
        before_tree = temp / "before"
        after_tree = temp / "after"
        before_sha = _materialize_commit(repo_path, before_ref, before_tree)
        after_sha = _materialize_commit(repo_path, after_ref, after_tree)

        specs = [
            (_run_name(side, order, seed), tree, order, seed)
            for side, tree in (("before", before_tree), ("after", after_tree))
            for order, seed in _STABILITY_CASES
        ]
        artifacts: dict[str, Path] = {}
        for name, tree, order, seed in specs:
            artifact = temp / f"{name}.jsonl"
            _spawn_worker(
                python=python,
                code_tree=tree,
                corpus_file=corpus_path,
                artifact=artifact,
                order=order,
                hash_seed=seed,
                timeout_s=worker_timeout_s,
            )
            artifacts[name] = artifact

        evaluation = evaluate_artifacts(
            expected_keys,
            artifacts,
            temp / "evaluation.sqlite3",
        )

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "before": {"ref": before_ref, "commit": before_sha},
        "after": {"ref": after_ref, "commit": after_sha},
        "corpus": corpus_meta,
        "contract": {
            "bytes": "canonical-json-utf8-exact",
            "result_exclusions": sorted(EXCLUDED_RESULT_FIELDS),
            "metric_exclusions": sorted(EXCLUDED_METRIC_FIELDS),
            "model_telemetry_exclusions": sorted(EXCLUDED_MODEL_TELEMETRY_FIELDS),
            "stability_passes": [
                {"order": order, "pythonhashseed": seed}
                for order, seed in _STABILITY_CASES
            ],
        },
        **evaluation,
    }


def _selftest() -> dict[str, Any]:
    @dataclasses.dataclass
    class Plan:
        sequence: list[str]
        total_duration_min: float

    @dataclasses.dataclass
    class Candidate:
        courier_id: str
        score: float
        plan: Plan
        metrics: dict[str, Any]

    @dataclasses.dataclass
    class Result:
        verdict: str
        best: Candidate
        candidates: list[Candidate]
        stage_timing: dict[str, float]

    base = Result(
        "PROPOSE",
        Candidate("7", 42.0004, Plan(["A", "B"], 18.0), {
            "candidate_timing": {"wall_ms": 1.0},
            "r07_compute_latency_ms": 2.0,
        }),
        [],
        {"assess_wall_ms": 3.0},
    )
    same_semantics = dataclasses.replace(
        base,
        best=dataclasses.replace(base.best, metrics={
            "candidate_timing": {"wall_ms": 999.0},
            "r07_compute_latency_ms": 888.0,
        }),
        stage_timing={"assess_wall_ms": 777.0},
    )
    mutation = dataclasses.replace(
        base,
        best=dataclasses.replace(base.best, plan=Plan(["B", "A"], 18.0)),
    )
    if canonical_decision_bytes(base) != canonical_decision_bytes(same_semantics):
        raise HarnessError("selftest: telemetry exclusion failed")
    if canonical_decision_bytes(base) == canonical_decision_bytes(mutation):
        raise HarnessError("selftest: nested decision mutation escaped")
    paths = _diff_paths(decision_snapshot(base), decision_snapshot(mutation))
    if not any("sequence" in path for path in paths):
        raise HarnessError("selftest: diff path missing")
    return {"schema": SCHEMA, "selftest": "PASS", "mutation_paths": paths}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo")
    parser.add_argument("--before")
    parser.add_argument("--after")
    parser.add_argument("--record-dir", default=DEFAULT_RECORD_DIR)
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--max-n", type=int)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--worker-timeout", type=float, default=1800.0)
    parser.add_argument("--out")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--code-tree", help=argparse.SUPPRESS)
    parser.add_argument("--corpus-file", help=argparse.SUPPRESS)
    parser.add_argument("--artifact", help=argparse.SUPPRESS)
    parser.add_argument("--order", choices=("forward", "reverse"), help=argparse.SUPPRESS)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args._worker:
        try:
            if not all((args.code_tree, args.corpus_file, args.artifact, args.order)):
                raise HarnessError("incomplete worker arguments")
            return _worker_run(args.code_tree, args.corpus_file, args.artifact, args.order)
        except Exception:
            return 2
    if args.selftest:
        try:
            print(json.dumps(_selftest(), sort_keys=True))
            return 0
        except Exception as exc:
            print(json.dumps({"schema": SCHEMA, "selftest": "FAIL", "error": type(exc).__name__}))
            return 2

    try:
        if not all((args.repo, args.before, args.after)):
            raise HarnessError("--repo, --before and --after are required")
        since = _parse_dt(args.since) if args.since else None
        until = _parse_dt(args.until) if args.until else None
        if args.since and since is None:
            raise HarnessError("invalid --since timestamp")
        if args.until and until is None:
            raise HarnessError("invalid --until timestamp")
        if since is not None and until is not None and until < since:
            raise HarnessError("--until must not be earlier than --since")
        report = run_comparison(
            repo=args.repo,
            before_ref=args.before,
            after_ref=args.after,
            record_dir=args.record_dir,
            since=since,
            until=until,
            max_n=args.max_n,
            python=args.python,
            worker_timeout_s=args.worker_timeout,
        )
    except Exception as exc:
        report = {
            "schema": SCHEMA,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "verdict": "ERROR",
            "error": type(exc).__name__,
        }

    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    if args.out:
        try:
            _write_atomic(Path(args.out), encoded)
        except Exception:
            return 2
    print(encoded.decode("utf-8"), end="")
    return {"PARITY": 0, "DIFFS": 1, "UNSTABLE": 1}.get(report.get("verdict"), 2)


if __name__ == "__main__":
    raise SystemExit(main())
