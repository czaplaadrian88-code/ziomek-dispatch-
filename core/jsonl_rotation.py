"""Canonical rename-based JSONL rotation with legacy-inode fencing.

The wrapper holds every cooperative ``<name>.append.lock`` and then proves
that no process still has an active or rotated JSONL inode open.  Logrotate
uses rename+create, never copytruncate: a legacy writer racing after the scan
either keeps the renamed inode (which remains linked) or opens the newly
created active path.  On the next run an inherited old fd blocks compression,
renumbering and retention until it closes.  This makes rollout and rollback
safe without trusting a process marker or an operator-created ACK file.
"""
from __future__ import annotations

import sys

_ORIGINAL_SYS_PATH = tuple(sys.path)
_RUNTIME_PYTHON_VERSION = (
    f"python{sys.version_info.major}.{sys.version_info.minor}"
)
_RUNTIME_PYTHON_ZIP = (
    f"python{sys.version_info.major}{sys.version_info.minor}.zip"
)
_TRUSTED_STDLIB_PATHS = frozenset(
    path
    for prefix in dict.fromkeys(
        str(prefix).rstrip("/")
        for prefix in (sys.base_prefix, sys.prefix, sys.exec_prefix)
        if prefix
    )
    for path in (
        f"{prefix}/{sys.platlibdir}/{_RUNTIME_PYTHON_VERSION}",
        f"{prefix}/{sys.platlibdir}/{_RUNTIME_PYTHON_VERSION}/lib-dynload",
        f"{prefix}/{sys.platlibdir}/{_RUNTIME_PYTHON_ZIP}",
    )
)
_TRUSTED_SITE_PATHS = frozenset(
    path
    for prefix in dict.fromkeys(
        str(prefix).rstrip("/")
        for prefix in (sys.base_prefix, sys.prefix, sys.exec_prefix)
        if prefix
    )
    for path in (
        f"{prefix}/{sys.platlibdir}/{_RUNTIME_PYTHON_VERSION}/site-packages",
        f"{prefix}/{sys.platlibdir}/{_RUNTIME_PYTHON_VERSION}/dist-packages",
        f"{prefix}/local/{sys.platlibdir}/{_RUNTIME_PYTHON_VERSION}/dist-packages",
        f"{prefix}/{sys.platlibdir}/python{sys.version_info.major}/dist-packages",
    )
)
sys.path[:] = [
    entry
    for entry in sys.path
    if entry and entry in _TRUSTED_STDLIB_PATHS
]

import argparse
import fcntl
import functools
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Sequence

if __package__ in (None, ""):
    _package_dir = Path(__file__).resolve().parent.parent
    _package_init = _package_dir / "__init__.py"
    if not _package_init.is_file():
        raise RuntimeError("cannot locate physical dispatch_v2 package")
    if any(
        name == "dispatch_v2" or name.startswith("dispatch_v2.")
        for name in sys.modules
    ):
        raise RuntimeError("conflicting preloaded dispatch_v2 package")
    _trusted_local_paths = (
        str(_package_dir),
        str(Path(__file__).resolve().parent),
    )
    sys.path[:] = list(
        dict.fromkeys(
            (
                *_trusted_local_paths,
                *(
                    entry
                    for entry in _ORIGINAL_SYS_PATH
                    if entry in _TRUSTED_STDLIB_PATHS
                    or entry in _TRUSTED_SITE_PATHS
                ),
            )
        )
    )
    _package_spec = importlib.util.spec_from_file_location(
        "dispatch_v2",
        _package_init,
        submodule_search_locations=[str(_package_dir)],
    )
    if _package_spec is None or _package_spec.loader is None:
        raise RuntimeError("cannot attest physical dispatch_v2 package")
    _package_module = importlib.util.module_from_spec(_package_spec)
    sys.modules["dispatch_v2"] = _package_module
    try:
        _package_spec.loader.exec_module(_package_module)
    except BaseException:
        sys.modules.pop("dispatch_v2", None)
        raise
else:
    sys.path[:] = _ORIGINAL_SYS_PATH
from dispatch_v2.common import (
    JSONL_LOGROTATE_CONFIG_PATH,
    JSONL_LOGROTATE_STATE_PATH,
    JSONL_ROTATION_REGISTRY_PATH,
    LOGS_DIR,
    STATE_DIR,
    resolve_shadow_decisions_writer_path,
    validate_jsonl_path,
    validate_runtime_control_path,
)
from dispatch_v2.core.jsonl_appender import open_attested_regular_file

JSONL_PATHS_MARKER = "@@DISPATCH_V2_JSONL_PATHS@@"
JSONL_PATH_REGISTRY = JSONL_ROTATION_REGISTRY_PATH
JSONL_WRITER_ROLE_STATIC = "static"
JSONL_WRITER_ROLE_SHADOW = "shadow"
JSONL_WRITER_ROLES = frozenset(
    (JSONL_WRITER_ROLE_STATIC, JSONL_WRITER_ROLE_SHADOW)
)
_MANAGED_REGISTRATION_CACHE: set[str] = set()
_MANAGED_REGISTRATION_CACHE_LOCK = threading.Lock()
SAFE_LOGROTATE_POLICY_DIRECTIVE = re.compile(
    r"(?:"
    r"daily|weekly|monthly|yearly|hourly|"
    r"compress|nocompress|delaycompress|missingok|notifempty|sharedscripts|"
    r"rotate [0-9]+|"
    r"(?:maxsize|size|minsize) [0-9]+[kMGT]?|"
    r"(?:su|create [0-7]{3,4}) [A-Za-z0-9_.-]+ [A-Za-z0-9_.-]+"
    r")"
)


class OpenJsonlInodeError(RuntimeError):
    """Rotation cannot prove that every legacy data-file descriptor is closed."""


class JsonlRotationConfigError(RuntimeError):
    """The wrapper cannot derive one safe logrotate config from its manifest."""


def _read_jsonl_path_registry(
    registry_path: Path,
) -> tuple[tuple[str, str], ...]:
    try:
        fd = open_attested_regular_file(
            registry_path,
            os.O_RDONLY,
            label="JSONL path registry",
        )
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise JsonlRotationConfigError(
            f"cannot open JSONL path registry {registry_path}: {exc}"
        ) from exc
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise JsonlRotationConfigError(
            f"cannot read JSONL path registry {registry_path}: {exc}"
        ) from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "writers"}
        or payload.get("schema_version") != 2
        or not isinstance(payload.get("writers"), list)
        or not payload["writers"]
    ):
        raise JsonlRotationConfigError(
            f"invalid JSONL path registry schema: {registry_path}"
        )
    try:
        entries = []
        for entry in payload["writers"]:
            if (
                not isinstance(entry, dict)
                or set(entry) != {"path", "role"}
                or not isinstance(entry["path"], str)
                or entry["role"] not in JSONL_WRITER_ROLES
            ):
                raise JsonlRotationConfigError(
                    f"invalid JSONL path registry schema: {registry_path}"
                )
            entries.append(
                (str(validate_jsonl_path(entry["path"])), entry["role"])
            )
    except ValueError as exc:
        raise JsonlRotationConfigError(
            f"invalid path in JSONL path registry {registry_path}: {exc}"
        ) from exc
    paths = [path for path, _role in entries]
    if len(paths) != len(set(paths)):
        raise JsonlRotationConfigError(
            f"duplicate JSONL writer path in registry: {registry_path}"
        )
    _normalize_jsonl_paths(paths)
    return tuple(sorted(entries))


def registered_jsonl_paths(
    registry_path: str | os.PathLike = JSONL_PATH_REGISTRY,
) -> tuple[str, ...]:
    """Read the monotonic cross-process set of paths used by managed writers."""
    try:
        path = validate_runtime_control_path(
            registry_path,
            label="JSONL rotation registry",
        )
    except ValueError as exc:
        raise JsonlRotationConfigError(str(exc)) from exc
    return tuple(path for path, _role in _read_jsonl_path_registry(path))


def registered_jsonl_writer_roles(
    registry_path: str | os.PathLike = JSONL_PATH_REGISTRY,
) -> tuple[tuple[str, str], ...]:
    """Return the durable path→role history used for collision decisions."""
    try:
        path = validate_runtime_control_path(
            registry_path,
            label="JSONL rotation registry",
        )
    except ValueError as exc:
        raise JsonlRotationConfigError(str(exc)) from exc
    return _read_jsonl_path_registry(path)


@contextmanager
def hold_jsonl_registry_lock(
    registry_path: str | os.PathLike = JSONL_PATH_REGISTRY,
) -> Iterator[Path]:
    """Freeze registry publication while deriving and rotating one manifest."""
    try:
        registry = validate_runtime_control_path(
            registry_path,
            label="JSONL rotation registry",
        )
    except ValueError as exc:
        raise JsonlRotationConfigError(str(exc)) from exc
    registry.parent.mkdir(parents=True, exist_ok=True)
    lock_path = registry.with_name(registry.name + ".lock")
    lock_fd = open_attested_regular_file(
        lock_path,
        os.O_RDWR | os.O_CREAT,
        0o600,
        label="JSONL registry lock",
        exclusive_lock=True,
    )
    try:
        os.fchmod(lock_fd, 0o600)
        yield registry
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def register_jsonl_writer_path(
    path_value: str | os.PathLike,
    *,
    registry_path: str | os.PathLike = JSONL_PATH_REGISTRY,
    writer_role: str = JSONL_WRITER_ROLE_STATIC,
    config_path: str | os.PathLike | None = None,
    state_path: str | os.PathLike | None = None,
) -> Path:
    """Publish one typed writer path before append; preserve role history."""
    if writer_role not in JSONL_WRITER_ROLES:
        raise JsonlRotationConfigError(
            f"invalid JSONL writer role: {writer_role!r}"
        )
    try:
        writer_path = validate_jsonl_path(path_value)
        registry = validate_runtime_control_path(
            registry_path,
            label="JSONL rotation registry",
        )
    except ValueError as exc:
        raise JsonlRotationConfigError(str(exc)) from exc
    validated_config, validated_state = _validated_logrotate_control_paths(
        config_path,
        state_path,
    )
    _assert_control_namespaces_disjoint(
        registry,
        validated_config,
        validated_state,
    )
    if writer_role == JSONL_WRITER_ROLE_SHADOW:
        _assert_shadow_writer_role_is_distinct(writer_path)
    static_manifest = static_managed_jsonl_paths()
    _normalize_jsonl_paths((*static_manifest, writer_path))
    _assert_data_control_namespaces_disjoint(
        (*static_manifest, writer_path),
        registry,
        validated_config,
        validated_state,
    )
    with hold_jsonl_registry_lock(registry) as registry:
        entries = dict(_read_jsonl_path_registry(registry))
        current_roles = {
            str(validate_jsonl_path(path)): JSONL_WRITER_ROLE_STATIC
            for path in static_manifest
        }
        requested_path = str(writer_path)
        current_roles[requested_path] = writer_role
        for current_path, current_role in current_roles.items():
            historic_role = entries.get(current_path)
            if historic_role is not None and historic_role != current_role:
                raise JsonlRotationConfigError(
                    "historic JSONL writer role conflict: "
                    f"path={current_path} historic={historic_role} "
                    f"requested={current_role}"
                )
        entries[requested_path] = writer_role
        normalized = _normalize_jsonl_paths(entries)
        _normalize_jsonl_paths((*static_manifest, *normalized))
        _assert_data_control_namespaces_disjoint(
            (*static_manifest, *normalized),
            registry,
            validated_config,
            validated_state,
        )
        payload = {
            "schema_version": 2,
            "writers": [
                {"path": path, "role": entries[path]}
                for path in normalized
            ],
        }
        fd, raw_temp = tempfile.mkstemp(
            prefix=f".{registry.name}.",
            suffix=".tmp",
            dir=str(registry.parent),
        )
        temp_path = Path(raw_temp)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=True, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, registry)
            directory_fd = os.open(str(registry.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
    return writer_path


def register_shadow_jsonl_writer_path(
    path_value: str | os.PathLike,
    *,
    registry_path: str | os.PathLike = JSONL_PATH_REGISTRY,
    config_path: str | os.PathLike | None = None,
    state_path: str | os.PathLike | None = None,
) -> Path:
    """Publish a dynamic shadow-schema writer with an explicit durable role."""
    return register_jsonl_writer_path(
        _assert_shadow_writer_role_is_distinct(path_value),
        registry_path=registry_path,
        writer_role=JSONL_WRITER_ROLE_SHADOW,
        config_path=config_path,
        state_path=state_path,
    )


def register_shadow_decisions_writer_path(
    config: dict | None = None,
    *,
    registry_path: str | os.PathLike = JSONL_PATH_REGISTRY,
    config_path: str | os.PathLike | None = None,
    state_path: str | os.PathLike | None = None,
) -> Path:
    """Resolve, validate and publish the effective shadow writer snapshot."""
    return register_shadow_jsonl_writer_path(
        resolve_shadow_decisions_writer_path(config),
        registry_path=registry_path,
        config_path=config_path,
        state_path=state_path,
    )


def static_managed_jsonl_paths() -> tuple[str, ...]:
    """Paths whose shared appender must publish its process-effective root."""
    return (
        str(STATE_DIR / "learning_log.jsonl"),
        str(STATE_DIR / "v319c_read_shadow_log.jsonl"),
        str(LOGS_DIR / "sla_log.jsonl"),
        str(STATE_DIR / "consumer_stuck_alert_evaluations.jsonl"),
        str(STATE_DIR / "obj_replay_capture.jsonl"),
        str(STATE_DIR / "eta_calibration_log.jsonl"),
        str(STATE_DIR / "drive_min_enriched.jsonl"),
        str(STATE_DIR / "drive_min_calibration_log_v2.jsonl"),
        str(STATE_DIR / "plan_recheck_log.jsonl"),
        str(STATE_DIR / "czasowka_eval_log.jsonl"),
        str(STATE_DIR / "decision_eta_log.jsonl"),
        str(STATE_DIR / "czasowka_reclaim_shadow.jsonl"),
        str(STATE_DIR / "uwagi_bridge_envelope.jsonl"),
        str(LOGS_DIR / "geocoding_log.jsonl"),
    )


@functools.lru_cache(maxsize=16)
def _validated_static_managed_jsonl_paths(
    raw_paths: tuple[str, ...],
) -> tuple[Path, ...]:
    """Cache only process-owned path syntax; physical signatures stay live."""
    try:
        return tuple(validate_jsonl_path(path) for path in raw_paths)
    except ValueError as exc:
        raise JsonlRotationConfigError(str(exc)) from exc


def register_managed_jsonl_writer_path(
    path_value: str | os.PathLike,
) -> Path:
    """Publish a known rotated path or fence an unmanaged writer namespace."""
    try:
        candidate = validate_jsonl_path(path_value)
        raw_static_paths = tuple(
            os.fspath(path)
            for path in static_managed_jsonl_paths()
        )
        static_paths = _validated_static_managed_jsonl_paths(
            raw_static_paths
        )
    except ValueError as exc:
        raise JsonlRotationConfigError(str(exc)) from exc
    if candidate not in static_paths:
        _attest_unmanaged_writer_namespace(
            str(candidate),
            raw_static_paths,
            os.fspath(JSONL_PATH_REGISTRY),
            os.fspath(JSONL_LOGROTATE_CONFIG_PATH),
            os.fspath(JSONL_LOGROTATE_STATE_PATH),
        )
        _assert_unmanaged_writer_disjoint_from_registered(
            candidate,
            _physical_namespace_signature(candidate),
        )
        return candidate
    cache_key = str(candidate)
    with _MANAGED_REGISTRATION_CACHE_LOCK:
        if cache_key not in _MANAGED_REGISTRATION_CACHE:
            register_jsonl_writer_path(
                candidate,
                writer_role=JSONL_WRITER_ROLE_STATIC,
            )
            _MANAGED_REGISTRATION_CACHE.add(cache_key)
    return candidate


@functools.lru_cache(maxsize=256)
def _attest_unmanaged_writer_namespace(
    candidate_value: str,
    raw_static_paths: tuple[str, ...],
    registry_value: str,
    config_value: str,
    state_value: str,
) -> None:
    """Cache stable relationships; the caller still validates data each time."""
    candidate = Path(candidate_value)
    static_paths = _validated_static_managed_jsonl_paths(raw_static_paths)
    candidate_signature = _physical_namespace_signature(candidate)
    static_signatures = tuple(
        (static_path, _physical_namespace_signature(static_path))
        for static_path in static_paths
    )
    for static_path, static_signature in static_signatures:
        if candidate_signature == static_signature:
            raise JsonlRotationConfigError(
                "unmanaged JSONL writer shares a managed physical "
                f"namespace alias: writer={candidate} "
                f"managed={static_path}"
            )
    try:
        registry = validate_runtime_control_path(
            registry_value,
            label="JSONL rotation registry",
        )
    except ValueError as exc:
        raise JsonlRotationConfigError(str(exc)) from exc
    validated_config, validated_state = _validated_logrotate_control_paths(
        config_value,
        state_value,
    )
    _assert_control_namespaces_disjoint(
        registry,
        validated_config,
        validated_state,
    )
    _assert_data_control_namespaces_disjoint(
        (candidate,),
        registry,
        validated_config,
        validated_state,
    )
    _assert_unmanaged_writer_disjoint_from_static(
        candidate,
        candidate_signature,
        static_signatures,
    )


def resolve_jsonl_paths(
    config: dict | None = None,
    *,
    registry_path: str | os.PathLike = JSONL_PATH_REGISTRY,
    config_path: str | os.PathLike | None = None,
    state_path: str | os.PathLike | None = None,
) -> tuple[str, ...]:
    """Build one manifest including every path ever published by the writer."""
    configured_writers = _configured_jsonl_writers(config)
    configured = tuple(path for path, _role in configured_writers)
    try:
        registry = validate_runtime_control_path(
            registry_path,
            label="JSONL rotation registry",
        )
    except ValueError as exc:
        raise JsonlRotationConfigError(str(exc)) from exc
    validated_config, validated_state = _validated_logrotate_control_paths(
        config_path,
        state_path,
    )
    _assert_control_namespaces_disjoint(
        registry,
        validated_config,
        validated_state,
    )
    registered_writers = registered_jsonl_writer_roles(registry)
    historic_roles = dict(registered_writers)
    for current_path, current_role in configured_writers:
        historic_role = historic_roles.get(current_path)
        if historic_role is not None and historic_role != current_role:
            raise JsonlRotationConfigError(
                "historic JSONL writer role conflict: "
                f"path={current_path} historic={historic_role} "
                f"requested={current_role}"
            )
    combined = _normalize_jsonl_paths(
        (*configured, *(path for path, _role in registered_writers))
    )
    _assert_data_control_namespaces_disjoint(
        combined,
        registry,
        validated_config,
        validated_state,
    )
    return combined


def _configured_jsonl_writers(
    config: dict | None = None,
) -> tuple[tuple[str, str], ...]:
    """Return typed process/config writers without touching registry storage."""
    try:
        shadow_path = resolve_shadow_decisions_writer_path(config)
    except ValueError as exc:
        raise JsonlRotationConfigError(str(exc)) from exc
    shadow_path = _assert_shadow_writer_role_is_distinct(shadow_path)
    roles = {
        str(validate_jsonl_path(path)): JSONL_WRITER_ROLE_STATIC
        for path in static_managed_jsonl_paths()
    }
    roles[str(shadow_path)] = JSONL_WRITER_ROLE_SHADOW
    normalized = _normalize_jsonl_paths(roles)
    return tuple((path, roles[path]) for path in normalized)


def _configured_jsonl_paths(config: dict | None = None) -> tuple[str, ...]:
    """Compatibility view of the typed process/config writer snapshot."""
    return tuple(path for path, _role in _configured_jsonl_writers(config))


def _assert_shadow_writer_role_is_distinct(
    shadow_path: str | os.PathLike,
) -> Path:
    """Prevent the dynamic shadow schema from sharing a static writer file."""
    try:
        validated = validate_jsonl_path(shadow_path)
    except ValueError as exc:
        raise JsonlRotationConfigError(str(exc)) from exc
    shadow_signature = _physical_namespace_signature(validated)
    if any(
        shadow_signature == _physical_namespace_signature(path)
        for path in static_managed_jsonl_paths()
    ):
        raise JsonlRotationConfigError(
            "shadow_log overlaps static managed writer: "
            f"{validated}"
        )
    return validated


def _physical_namespace_signature(
    path_value: str | os.PathLike,
) -> tuple[tuple[int, int], tuple[str, ...]]:
    """Identify an existing inode anchor plus the unresolved suffix below it."""
    path = Path(path_value)
    suffix: list[str] = []
    current = path
    while True:
        try:
            metadata = current.stat()
        except FileNotFoundError:
            parent = current.parent
            if parent == current:
                raise JsonlRotationConfigError(
                    f"cannot anchor filesystem namespace: {path}"
                )
            suffix.append(current.name)
            current = parent
            continue
        except OSError as exc:
            raise JsonlRotationConfigError(
                f"cannot identify filesystem namespace {path}: {exc}"
            ) from exc
        return (
            (metadata.st_dev, metadata.st_ino),
            tuple(reversed(suffix)),
        )


def _physical_namespaces_overlap(
    first: str | os.PathLike,
    second: str | os.PathLike,
) -> bool:
    return _physical_namespace_signatures_overlap(
        _physical_namespace_signature(first),
        _physical_namespace_signature(second),
    )


def _physical_namespace_signatures_overlap(
    first_signature: tuple[tuple[int, int], tuple[str, ...]],
    second_signature: tuple[tuple[int, int], tuple[str, ...]],
) -> bool:
    """Compare already-attested namespaces without repeating filesystem I/O."""
    first_anchor, first_suffix = first_signature
    second_anchor, second_suffix = second_signature
    if first_anchor != second_anchor:
        return False
    common = min(len(first_suffix), len(second_suffix))
    return first_suffix[:common] == second_suffix[:common]


def _namespace_paths_overlap(first: Path, second: Path) -> bool:
    return (
        first == second
        or first in second.parents
        or second in first.parents
        or _physical_namespaces_overlap(first, second)
    )


def _namespace_paths_overlap_attested(
    first: Path,
    first_signature: tuple[tuple[int, int], tuple[str, ...]],
    second: Path,
    second_signature: tuple[tuple[int, int], tuple[str, ...]],
) -> bool:
    """Preserve lexical and physical overlap checks with cached attestations."""
    return (
        first == second
        or first in second.parents
        or second in first.parents
        or _physical_namespace_signatures_overlap(
            first_signature,
            second_signature,
        )
    )


def _is_numeric_rotation_of(candidate: Path, active: Path) -> bool:
    return (
        _physical_namespace_signature(candidate.parent)
        == _physical_namespace_signature(active.parent)
        and _is_numeric_rotation_name(candidate.name, active.name)
    )


def _is_numeric_rotation_name(candidate_name: str, active_name: str) -> bool:
    return (
        re.fullmatch(
            re.escape(active_name) + r"\.[0-9]+(?:\.gz)?",
            candidate_name,
        )
        is not None
    )


def _assert_unmanaged_writer_disjoint_from_static(
    candidate: Path,
    candidate_signature: tuple[tuple[int, int], tuple[str, ...]],
    static_signatures: Sequence[
        tuple[Path, tuple[tuple[int, int], tuple[str, ...]]]
    ],
) -> None:
    """Reserve every active, lock, nested and numeric managed namespace."""
    candidate_lock = candidate.with_name(
        candidate.name + ".append.lock"
    )
    candidate_lock_signature = _physical_namespace_signature(candidate_lock)
    if _physical_namespace_signatures_overlap(
        candidate_signature,
        candidate_lock_signature,
    ):
        raise JsonlRotationConfigError(
            "unmanaged JSONL writer overlaps its namespace lock: "
            f"writer={candidate} lock={candidate_lock}"
        )
    candidate_parent_signature = _physical_namespace_signature(
        candidate.parent
    )
    for static_path, static_signature in static_signatures:
        if _physical_namespace_signatures_overlap(
            candidate_signature,
            static_signature,
        ):
            raise JsonlRotationConfigError(
                "unmanaged JSONL writer overlaps a managed nested namespace: "
                f"writer={candidate} managed={static_path}"
            )
        static_lock = static_path.with_name(
            static_path.name + ".append.lock"
        )
        static_lock_signature = _physical_namespace_signature(static_lock)
        if (
            _physical_namespace_signatures_overlap(
                candidate_signature,
                static_lock_signature,
            )
            or _physical_namespace_signatures_overlap(
                candidate_lock_signature,
                static_signature,
            )
            or _physical_namespace_signatures_overlap(
                candidate_lock_signature,
                static_lock_signature,
            )
        ):
            raise JsonlRotationConfigError(
                "unmanaged JSONL writer overlaps a managed namespace lock: "
                f"writer={candidate} managed={static_path}"
            )
        if (
            candidate_parent_signature
            == _physical_namespace_signature(static_path.parent)
            and (
                _is_numeric_rotation_name(candidate.name, static_path.name)
                or _is_numeric_rotation_name(static_path.name, candidate.name)
            )
        ):
            raise JsonlRotationConfigError(
                "unmanaged JSONL writer overlaps a managed rotation "
                f"namespace: writer={candidate} managed={static_path}"
            )


def _assert_unmanaged_writer_disjoint_from_registered(
    candidate: Path,
    candidate_signature: tuple[tuple[int, int], tuple[str, ...]],
) -> None:
    """Typed registry history reserves writer namespaces for EVERY role.

    The durable registry (static + shadow) is read live on each call: an
    unmanaged writer may keep re-attesting its own registered path, but it
    must never occupy another registered writer's active, lock, nested or
    numeric rotation namespace.
    """
    reserved = tuple(
        (
            Path(registered_path),
            _physical_namespace_signature(Path(registered_path)),
        )
        for registered_path, _role in registered_jsonl_writer_roles(
            JSONL_PATH_REGISTRY
        )
        if registered_path != str(candidate)
    )
    if reserved:
        _assert_unmanaged_writer_disjoint_from_static(
            candidate,
            candidate_signature,
            reserved,
        )


def _logrotate_state_temp_path(state_path: Path) -> Path:
    """Return logrotate's deterministic state rewrite sidecar."""
    return state_path.with_name(state_path.name + ".tmp")


def _is_registry_atomic_temp_path(
    candidate: Path,
    registry_path: Path,
    *,
    candidate_parent_signature: (
        tuple[tuple[int, int], tuple[str, ...]] | None
    ) = None,
    registry_parent_signature: (
        tuple[tuple[int, int], tuple[str, ...]] | None
    ) = None,
) -> bool:
    """Recognize the complete ``mkstemp`` family used for registry replace."""
    prefix = f".{registry_path.name}."
    suffix = ".tmp"
    return (
        (
            candidate_parent_signature
            if candidate_parent_signature is not None
            else _physical_namespace_signature(candidate.parent)
        )
        == (
            registry_parent_signature
            if registry_parent_signature is not None
            else _physical_namespace_signature(registry_path.parent)
        )
        and candidate.name.startswith(prefix)
        and candidate.name.endswith(suffix)
        and len(candidate.name) > len(prefix) + len(suffix)
    )


def _validated_logrotate_control_paths(
    config_path: str | os.PathLike | None,
    state_path: str | os.PathLike | None,
) -> tuple[Path, Path]:
    """Resolve the one canonical config/state pair with typed validators."""
    raw_config = (
        JSONL_LOGROTATE_CONFIG_PATH
        if config_path is None
        else config_path
    )
    raw_state = (
        JSONL_LOGROTATE_STATE_PATH
        if state_path is None
        else state_path
    )
    try:
        config = validate_runtime_control_path(
            raw_config,
            label="JSONL logrotate config",
        )
        state = validate_runtime_control_path(
            raw_state,
            label="JSONL logrotate state",
        )
        validate_runtime_control_path(
            _logrotate_state_temp_path(state),
            label="JSONL logrotate state temporary sidecar",
        )
    except ValueError as exc:
        raise JsonlRotationConfigError(str(exc)) from exc
    return config, state


def _assert_control_namespaces_disjoint(
    registry_path: Path,
    config_path: Path,
    state_path: Path,
) -> None:
    """Ensure registry/config/state cannot alias or contain one another."""
    controls = (
        ("registry", registry_path),
        (
            "registry lock",
            registry_path.with_name(registry_path.name + ".lock"),
        ),
        ("config", config_path),
        ("state", state_path),
        ("state temporary sidecar", _logrotate_state_temp_path(state_path)),
    )
    control_descriptors = tuple(
        (
            control_role,
            control_path,
            _physical_namespace_signature(control_path),
            _physical_namespace_signature(control_path.parent),
        )
        for control_role, control_path in controls
    )
    registry_parent_signature = _physical_namespace_signature(
        registry_path.parent
    )
    for (
        control_role,
        control_path,
        _control_signature,
        control_parent_signature,
    ) in control_descriptors[1:]:
        if _is_registry_atomic_temp_path(
            control_path,
            registry_path,
            candidate_parent_signature=control_parent_signature,
            registry_parent_signature=registry_parent_signature,
        ):
            raise JsonlRotationConfigError(
                "logrotate control namespace overlaps registry atomic "
                f"temporary namespace: {control_role}={control_path} "
                f"registry={registry_path}"
            )
    for index, (
        first_role,
        first,
        first_signature,
        _first_parent_signature,
    ) in enumerate(control_descriptors):
        for (
            second_role,
            second,
            second_signature,
            _second_parent_signature,
        ) in control_descriptors[index + 1:]:
            if _namespace_paths_overlap_attested(
                first,
                first_signature,
                second,
                second_signature,
            ):
                raise JsonlRotationConfigError(
                    "logrotate control namespace overlap: "
                    f"{first_role}={first} {second_role}={second}"
                )


def _assert_data_control_namespaces_disjoint(
    paths: Iterable[str | os.PathLike],
    registry_path: Path,
    config_path: Path,
    state_path: Path,
) -> None:
    """Reserve all control paths before any managed writer can append."""
    controls = (
        (
            "registry namespace",
            registry_path,
        ),
        (
            "registry namespace",
            registry_path.with_name(registry_path.name + ".lock"),
        ),
        (
            "logrotate config control namespace",
            config_path,
        ),
        (
            "logrotate state control namespace",
            state_path,
        ),
        (
            "logrotate state temporary control namespace",
            _logrotate_state_temp_path(state_path),
        ),
    )
    control_descriptors = tuple(
        (
            control_role,
            control_path,
            _physical_namespace_signature(control_path),
            _physical_namespace_signature(control_path.parent),
        )
        for control_role, control_path in controls
    )
    registry_parent_signature = _physical_namespace_signature(
        registry_path.parent
    )
    for raw_path in paths:
        try:
            data_path = validate_jsonl_path(raw_path)
        except ValueError as exc:
            raise JsonlRotationConfigError(str(exc)) from exc
        data_lock = data_path.with_name(
            data_path.name + ".append.lock"
        )
        data_signature = _physical_namespace_signature(data_path)
        data_lock_signature = _physical_namespace_signature(data_lock)
        data_parent_signature = _physical_namespace_signature(
            data_path.parent
        )
        for reserved_path in (data_path, data_lock):
            if _is_registry_atomic_temp_path(
                reserved_path,
                registry_path,
                candidate_parent_signature=data_parent_signature,
                registry_parent_signature=registry_parent_signature,
            ):
                raise JsonlRotationConfigError(
                    "JSONL writer overlaps registry atomic temporary "
                    "control namespace: "
                    f"writer={data_path} registry={registry_path}"
                )
        for (
            control_role,
            control_path,
            control_signature,
            control_parent_signature,
        ) in control_descriptors:
            if (
                _namespace_paths_overlap_attested(
                    control_path,
                    control_signature,
                    data_path,
                    data_signature,
                )
                or _namespace_paths_overlap_attested(
                    control_path,
                    control_signature,
                    data_lock,
                    data_lock_signature,
                )
                or (
                    control_parent_signature == data_parent_signature
                    and _is_numeric_rotation_name(
                        control_path.name,
                        data_path.name,
                    )
                )
            ):
                if (
                    control_role == "logrotate config control namespace"
                    and control_parent_signature == data_parent_signature
                    and _is_numeric_rotation_name(
                        control_path.name,
                        data_path.name,
                    )
                ):
                    raise JsonlRotationConfigError(
                        "logrotate config overlaps data rotation namespace: "
                        f"config={control_path} data={data_path}"
                    )
                raise JsonlRotationConfigError(
                    f"JSONL writer overlaps {control_role} and physical "
                    "namespace alias boundary: "
                    f"writer={data_path} control={control_path}"
                )


def _normalize_jsonl_paths(
    paths: Iterable[str | os.PathLike],
) -> tuple[str, ...]:
    try:
        candidates = tuple(validate_jsonl_path(path) for path in paths)
    except ValueError as exc:
        raise JsonlRotationConfigError(str(exc)) from exc
    normalized = tuple(sorted({str(path) for path in candidates}))
    if not normalized:
        raise JsonlRotationConfigError("JSONL rotation manifest is empty")
    path_objects = tuple(Path(path) for path in normalized)
    for index, path in enumerate(path_objects):
        data_lock = path.with_name(path.name + ".append.lock")
        if _namespace_paths_overlap(path, data_lock):
            raise JsonlRotationConfigError(
                "JSONL data path overlaps its own namespace lock: "
                f"{path!s}, {data_lock!s}"
            )
        for other in path_objects[index + 1:]:
            other_lock = other.with_name(other.name + ".append.lock")
            if _physical_namespace_signature(path) == (
                _physical_namespace_signature(other)
            ):
                raise JsonlRotationConfigError(
                    "multiple JSONL paths share a physical namespace alias: "
                    f"{path!s}, {other!s}"
                )
            if _namespace_paths_overlap(path, other):
                raise JsonlRotationConfigError(
                    "nested JSONL data paths are forbidden: "
                    f"{path!s}, {other!s}"
                )
            if (
                _namespace_paths_overlap(data_lock, other)
                or _namespace_paths_overlap(other_lock, path)
                or _namespace_paths_overlap(data_lock, other_lock)
            ):
                raise JsonlRotationConfigError(
                    "JSONL data path overlaps namespace lock: "
                    f"{path!s}, {other!s}"
                )
            if (
                _is_numeric_rotation_of(other, path)
                or _is_numeric_rotation_of(path, other)
            ):
                raise JsonlRotationConfigError(
                    "JSONL data paths overlap a rotation namespace: "
                    f"{path!s}, {other!s}"
                )
    identities: dict[tuple[int, int], str] = {}
    for raw_path in normalized:
        path = Path(raw_path)
        try:
            metadata = path.stat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise JsonlRotationConfigError(
                f"cannot identify JSONL path {path}: {exc}"
            ) from exc
        identity = (metadata.st_dev, metadata.st_ino)
        prior = identities.get(identity)
        if prior is not None and prior != raw_path:
            raise JsonlRotationConfigError(
                "multiple JSONL path aliases reference one inode: "
                f"{prior!r}, {raw_path!r}"
            )
        identities[identity] = raw_path
    return normalized


def _quote_logrotate_path(path: str) -> str:
    try:
        validated = str(validate_jsonl_path(path))
    except ValueError as exc:
        raise JsonlRotationConfigError(str(exc)) from exc
    return '"' + validated.replace('"', '\\"') + '"'


def render_jsonl_logrotate_config(
    template: str,
    paths: Iterable[str | os.PathLike],
) -> str:
    """Wstaw do polityki dokładnie ten sam manifest, który jest lockowany."""
    if template.count(JSONL_PATHS_MARKER) != 1:
        raise JsonlRotationConfigError(
            "logrotate policy must contain exactly one JSONL paths marker"
        )
    inline_comments = [
        line
        for line in template.splitlines()
        if "#" in line and line.split("#", 1)[0].strip()
    ]
    if inline_comments:
        raise JsonlRotationConfigError(
            "inline comments are forbidden in logrotate policy"
        )
    significant = [
        line.split("#", 1)[0].strip()
        for line in template.splitlines()
        if line.split("#", 1)[0].strip()
    ]
    if (
        len(significant) < 3
        or significant[0] != JSONL_PATHS_MARKER
        or significant[1] != "{"
        or significant[-1] != "}"
        or significant.count("{") != 1
        or significant.count("}") != 1
    ):
        raise JsonlRotationConfigError(
            "logrotate policy may contain only one marker-owned path block"
        )
    unsafe_directives = [
        directive
        for directive in significant[2:-1]
        if SAFE_LOGROTATE_POLICY_DIRECTIVE.fullmatch(directive) is None
    ]
    if unsafe_directives:
        raise JsonlRotationConfigError(
            "unsafe logrotate policy directive: "
            + ", ".join(unsafe_directives)
        )
    path_lines = "\n".join(
        _quote_logrotate_path(path) for path in _normalize_jsonl_paths(paths)
    )
    return template.replace(JSONL_PATHS_MARKER, path_lines)


@contextmanager
def materialize_jsonl_logrotate_config(
    template_path: str | os.PathLike,
    paths: Iterable[str | os.PathLike],
) -> Iterator[tuple[Path, int]]:
    """Seal validated config bytes in memory and expose only inherited fd."""
    source = Path(template_path)
    try:
        template = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise JsonlRotationConfigError(
            f"cannot read logrotate policy {source}: {exc}"
        ) from exc
    rendered = render_jsonl_logrotate_config(template, paths)
    try:
        fd = os.memfd_create(
            f"dispatch-v2-{source.name}",
            os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
        )
    except OSError as exc:
        raise JsonlRotationConfigError(
            f"cannot create sealed logrotate config: {exc}"
        ) from exc
    try:
        os.fchmod(fd, 0o600)
        payload = rendered.encode("utf-8")
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise OSError("short write to sealed logrotate config")
            offset += written
        os.lseek(fd, 0, os.SEEK_SET)
        seals = (
            fcntl.F_SEAL_SEAL
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_WRITE
        )
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, seals)
        yield Path(f"/proc/self/fd/{fd}"), fd
    finally:
        os.close(fd)


def _rotation_candidates(paths: Iterable[str | os.PathLike]) -> list[Path]:
    candidates: set[Path] = set()
    for raw_path in paths:
        path = Path(raw_path)
        if path.exists():
            candidates.add(path)
        try:
            siblings = path.parent.glob(path.name + ".[0-9]*")
            candidates.update(
                candidate for candidate in siblings if candidate.is_file()
            )
        except OSError as exc:
            raise OpenJsonlInodeError(
                f"cannot enumerate JSONL rotations for {path}: {exc}"
            ) from exc
    return sorted(candidates)


def assert_no_open_jsonl_inodes(
    paths: Iterable[str | os.PathLike],
    *,
    proc_root: str | os.PathLike = "/proc",
) -> None:
    """Fail closed if any process references an inode logrotate may move/remove."""
    identities: dict[tuple[int, int], Path] = {}
    for candidate in _rotation_candidates(paths):
        try:
            stat_result = candidate.stat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise OpenJsonlInodeError(
                f"cannot stat JSONL candidate {candidate}: {exc}"
            ) from exc
        identities[(stat_result.st_dev, stat_result.st_ino)] = candidate
    if not identities:
        return

    try:
        processes = list(Path(proc_root).iterdir())
    except OSError as exc:
        raise OpenJsonlInodeError(f"cannot enumerate process fds: {exc}") from exc
    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            descriptors = list((process / "fd").iterdir())
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise OpenJsonlInodeError(
                f"cannot inspect process {process.name} fds: {exc}"
            ) from exc
        for descriptor in descriptors:
            try:
                opened = descriptor.stat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise OpenJsonlInodeError(
                    f"cannot inspect process fd {descriptor}: {exc}"
                ) from exc
            identity = (opened.st_dev, opened.st_ino)
            if identity in identities:
                raise OpenJsonlInodeError(
                    "JSONL inode still open; rotation deferred: "
                    f"pid={process.name} path={identities[identity]}"
                )


@contextmanager
def hold_jsonl_rotation_locks(
    paths: Iterable[str | os.PathLike],
) -> Iterator[None]:
    """Hold all writer namespace locks in deterministic pathname order."""
    lock_fds: list[int] = []
    try:
        normalized = _normalize_jsonl_paths(paths)
        for raw_path in normalized:
            path = Path(raw_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = path.with_name(path.name + ".append.lock")
            fd = open_attested_regular_file(
                lock_path,
                os.O_RDWR | os.O_CREAT,
                0o644,
                label="JSONL namespace lock",
                exclusive_lock=True,
            )
            lock_fds.append(fd)
        yield
    finally:
        for fd in reversed(lock_fds):
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def run_logrotate(
    config_path: str | os.PathLike,
    *,
    logrotate_bin: str = "/usr/sbin/logrotate",
    state_path: str | os.PathLike | None = None,
    force: bool = False,
    debug: bool = False,
    verbose: bool = False,
    config: dict | None = None,
    registry_path: str | os.PathLike = JSONL_PATH_REGISTRY,
    proc_root: str | os.PathLike = "/proc",
) -> int:
    """Run canonical logrotate after lock + direct open-inode attestation."""
    try:
        registry = validate_runtime_control_path(
            registry_path,
            label="JSONL rotation registry",
        )
    except ValueError as exc:
        raise JsonlRotationConfigError(str(exc)) from exc
    validated_config, validated_state = _validated_logrotate_control_paths(
        config_path,
        state_path,
    )
    _assert_control_namespaces_disjoint(
        registry,
        validated_config,
        validated_state,
    )
    configured_paths = _configured_jsonl_paths(config)
    _assert_data_control_namespaces_disjoint(
        configured_paths,
        registry,
        validated_config,
        validated_state,
    )
    with hold_jsonl_registry_lock(registry) as frozen_registry:
        manifest = resolve_jsonl_paths(
            config,
            registry_path=frozen_registry,
            config_path=validated_config,
            state_path=validated_state,
        )
        normalized_paths = _normalize_jsonl_paths(manifest)
        _assert_control_namespaces_disjoint(
            frozen_registry,
            validated_config,
            validated_state,
        )
        _assert_data_control_namespaces_disjoint(
            normalized_paths,
            frozen_registry,
            validated_config,
            validated_state,
        )
        with hold_jsonl_rotation_locks(normalized_paths):
            assert_no_open_jsonl_inodes(normalized_paths, proc_root=proc_root)
            with materialize_jsonl_logrotate_config(
                validated_config,
                normalized_paths,
            ) as (generated_config, config_fd):
                command = [str(logrotate_bin)]
                command.extend(("--state", str(validated_state)))
                if force:
                    command.append("--force")
                if debug:
                    command.append("--debug")
                if verbose:
                    command.append("--verbose")
                command.append(str(generated_config))
                completed = subprocess.run(
                    command,
                    check=False,
                    pass_fds=(config_fd,),
                )
    return int(completed.returncode)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "config",
        nargs="?",
        default=str(JSONL_LOGROTATE_CONFIG_PATH),
    )
    parser.add_argument("--logrotate-bin", default="/usr/sbin/logrotate")
    parser.add_argument(
        "--state",
        "-s",
        default=str(JSONL_LOGROTATE_STATE_PATH),
    )
    parser.add_argument("--force", "-f", action="store_true")
    parser.add_argument("--debug", "-d", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)
    return run_logrotate(
        args.config,
        logrotate_bin=args.logrotate_bin,
        state_path=args.state,
        force=args.force,
        debug=args.debug,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    raise SystemExit(main())
