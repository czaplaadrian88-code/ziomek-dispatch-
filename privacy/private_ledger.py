"""Writer-aware privacy contract for Ziomek JSONL ledgers.

The production default is deliberately ``compat``: callers delegate to the
existing JSONL appender and retain the exact legacy record shape.  ``mirror``
is a reserved HOLD value and fails before either write until a retry-safe
dual-write transaction/outbox exists.  ``private`` writes only the
pseudonymised record.  No non-default mode is enabled by this source change.

Private files are opened relative to a validated 0700 directory with
``O_NOFOLLOW`` and mode 0600.  A stable, equally protected lock file provides
the append/rename/reopen handshake.  Rotation always uses rename; truncation is
not implemented anywhere in this module.

This module intentionally does not invent encryption.  Stable pseudonyms use
HMAC-SHA256 with an injected scope/key.  Exact replay of genuinely sensitive
production coordinates requires a separately approved sealer contract; the
reader here supports legacy and pseudonymised v1 records without pretending
that redaction is reversible.
"""
from __future__ import annotations

import base64
import errno
import fcntl
import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol


SCHEMA = "private_ledger.v1"
STATUS_SCHEMA = "private_ledger_status.v1"
VALID_MODES = frozenset({"compat", "mirror", "private"})
DEFAULT_PRIVATE_ROOT = "/root/.openclaw/workspace/dispatch_state/private_ledger"


class PrivateLedgerError(RuntimeError):
    """Privacy policy, key, path, or filesystem invariant failed closed."""


class KeyProvider(Protocol):
    def key_for_scope(self, scope: str) -> bytes: ...


@dataclass(frozen=True)
class LedgerConfig:
    mode: str = "compat"
    root: str = DEFAULT_PRIVATE_ROOT
    scope: str | None = None
    key_file: str | None = None

    @classmethod
    def from_env(cls) -> "LedgerConfig":
        return cls(
            mode=os.environ.get("ZIOMEK_PRIVATE_LEDGER_MODE", "compat").strip().lower(),
            root=os.environ.get("ZIOMEK_PRIVATE_LEDGER_ROOT", DEFAULT_PRIVATE_ROOT),
            scope=os.environ.get("ZIOMEK_PRIVATE_LEDGER_SCOPE") or None,
            key_file=os.environ.get("ZIOMEK_PRIVATE_LEDGER_KEY_FILE") or None,
        )

    def validate(self) -> None:
        if self.mode not in VALID_MODES:
            raise PrivateLedgerError("invalid private-ledger mode")
        if self.mode == "mirror":
            raise PrivateLedgerError(
                "mirror mode is HOLD: retry-safe dual-write protocol is not approved"
            )
        if self.mode != "compat":
            if not self.scope or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", self.scope):
                raise PrivateLedgerError("private-ledger scope missing or invalid")
            if not self.key_file:
                raise PrivateLedgerError("private-ledger key file missing")


@dataclass(frozen=True)
class AppendOutcome:
    mode: str
    legacy_written: bool
    private_written: bool
    degraded: bool = False
    error_type: str | None = None


def _validate_regular_fd(fd: int, *, expected_mode: int, label: str) -> os.stat_result:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise PrivateLedgerError(f"{label} is not a regular file")
    if st.st_uid != os.geteuid():
        raise PrivateLedgerError(f"{label} owner mismatch")
    if st.st_nlink != 1:
        raise PrivateLedgerError(f"{label} hardlink count is not one")
    if stat.S_IMODE(st.st_mode) != expected_mode:
        raise PrivateLedgerError(f"{label} mode mismatch")
    return st


def _validate_path_inode(dir_fd: int, name: str, fd_stat: os.stat_result, label: str) -> None:
    try:
        path_stat = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError as exc:
        raise PrivateLedgerError(f"{label} path disappeared") from exc
    if not stat.S_ISREG(path_stat.st_mode):
        raise PrivateLedgerError(f"{label} path is not regular")
    if (path_stat.st_dev, path_stat.st_ino) != (fd_stat.st_dev, fd_stat.st_ino):
        raise PrivateLedgerError(f"{label} path was replaced")


def ensure_secure_directory(path: str | os.PathLike[str], *, create: bool = True) -> int:
    """Create/validate a leaf directory through a component-wise dirfd walk.

    ``O_NOFOLLOW`` only on the leaf is insufficient: an attacker could replace
    an ancestor with a symlink.  Every component is therefore opened relative
    to the already pinned parent descriptor.  Newly created components and the
    final private root must be current-user owned 0700 directories.
    """
    p = Path(path)
    if ".." in p.parts:
        raise PrivateLedgerError("private-ledger parent traversal refused")
    flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    anchor = "/" if p.is_absolute() else "."
    parts = p.parts[1:] if p.is_absolute() else p.parts
    try:
        current_fd = os.open(anchor, flags)
    except OSError as exc:
        raise PrivateLedgerError("cannot pin private-ledger anchor") from exc
    try:
        for index, component in enumerate(parts):
            if component in {"", "."}:
                continue
            created = False
            was_missing = False
            try:
                next_fd = os.open(component, flags, dir_fd=current_fd)
            except FileNotFoundError:
                if not create:
                    raise PrivateLedgerError(
                        "private-ledger directory component missing (no-create reader)"
                    )
                was_missing = True
                try:
                    os.mkdir(component, 0o700, dir_fd=current_fd)
                    created = True
                except FileExistsError:
                    # Concurrent writer won create.  Re-open through the same
                    # pinned parent; O_NOFOLLOW still rejects a symlink race.
                    created = False
                except OSError as exc:
                    raise PrivateLedgerError(
                        "cannot create private-ledger directory component"
                    ) from exc
                try:
                    next_fd = os.open(component, flags, dir_fd=current_fd)
                except OSError as exc:
                    raise PrivateLedgerError(
                        "private-ledger raced component is invalid"
                    ) from exc
            except OSError as exc:
                raise PrivateLedgerError(
                    "private-ledger ancestor symlink or invalid component refused"
                ) from exc
            os.close(current_fd)
            current_fd = next_fd
            st = os.fstat(current_fd)
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
                raise PrivateLedgerError("private-ledger directory owner/type mismatch")
            if (created or was_missing) and stat.S_IMODE(st.st_mode) != 0o700:
                raise PrivateLedgerError("new private-ledger directory mode mismatch")
            if index == len(parts) - 1 and stat.S_IMODE(st.st_mode) != 0o700:
                raise PrivateLedgerError("private-ledger directory mode mismatch")
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


class FileKeyProvider:
    """Read a synthetic/real deployment key through a hardened file boundary.

    The file must be a root/current-user owned, single-link regular file with
    mode 0600.  Raw bytes or strict base64 (prefix ``base64:``) are accepted.
    Values are never included in exceptions or representations.
    """

    def __init__(self, path: str | os.PathLike[str]):
        self._path = str(path)

    def key_for_scope(self, scope: str) -> bytes:
        del scope  # scope is applied by the HMAC domain separator below
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        key_path = Path(self._path)
        dir_fd = ensure_secure_directory(key_path.parent, create=False)
        try:
            fd = os.open(key_path.name, flags, dir_fd=dir_fd)
        except OSError as exc:
            os.close(dir_fd)
            raise PrivateLedgerError("cannot open private-ledger key") from exc
        try:
            key_st = _validate_regular_fd(fd, expected_mode=0o600, label="key")
            _validate_path_inode(dir_fd, key_path.name, key_st, "key")
            raw = os.read(fd, 4097)
            if len(raw) > 4096:
                raise PrivateLedgerError("private-ledger key is too large")
            _validate_path_inode(dir_fd, key_path.name, key_st, "key")
        finally:
            os.close(fd)
            os.close(dir_fd)
        raw = raw.strip()
        if raw.startswith(b"base64:"):
            try:
                raw = base64.b64decode(raw[7:], validate=True)
            except ValueError as exc:
                raise PrivateLedgerError("private-ledger key encoding invalid") from exc
        if len(raw) < 32:
            raise PrivateLedgerError("private-ledger key is too short")
        return raw


class _Pseudonymizer:
    def __init__(self, key: bytes, scope: str):
        self._key = hmac.new(key, b"ziomek-private-ledger\0" + scope.encode(), hashlib.sha256).digest()
        self.scope = scope

    def token(self, kind: str, value: Any) -> str:
        blob = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        digest = hmac.new(self._key, kind.encode() + b"\0" + blob, hashlib.sha256).digest()
        # 96-bit stable token: compact enough for high-volume ledgers while the
        # collision budget remains far beyond the expected local cardinality.
        token = base64.urlsafe_b64encode(digest[:12]).decode("ascii").rstrip("=")
        return f"p:{kind}:{token}"


_ID_KIND = {
    "order_id": "order", "oid": "order", "bag_oids": "order",
    "courier_id": "courier", "cid": "courier", "best_cid": "courier",
    "selected_courier_id": "courier", "event_id": "event",
    "address_id": "account", "restaurant_id": "account",
}
_SENSITIVE_CONTAINER_PARTS = (
    "coords", "coordinate", "location", "position", "pos", "gps",
    "osrm_calls", "fleet", "live_inputs", "bag_context",
)
_EXACT_LOCATION_KEYS = frozenset({"lat", "lng", "lon", "latitude", "longitude"})
_SENSITIVE_TEXT_PARTS = (
    "address", "street", "city", "phone", "email", "customer", "name",
    "restaurant", "notes", "uwagi", "reason", "error", "exception",
)
_SAFE_TEXT_KEYS = frozenset({
    "schema", "ts", "timestamp", "now", "written_at", "generated_at",
    "verdict", "status", "feasibility", "strategy", "source", "pos_source",
    "auto_route", "mode", "classification", "pseudonym_scope", "ledger",
    "kind", "phase", "event_type", "flags_sha1",
})
_DYNAMIC_MAPPING_CONTAINERS = frozenset({
    "courier_times", "courier_positions", "courier_scores", "couriers_by_id",
    "order_times", "order_scores", "orders_by_id", "per_courier", "per_order",
})


def _norm_key(key: Any) -> str:
    return str(key).strip().lower()


def _id_kind(key: str) -> str | None:
    if key in _ID_KIND:
        return _ID_KIND[key]
    if key.endswith("_order_id") or key.endswith("_oid"):
        return "order"
    if key.endswith("_courier_id") or key.endswith("_cid"):
        return "courier"
    if key.endswith("_event_id"):
        return "event"
    return None


def _is_location_key(key: str) -> bool:
    return (
        key in _EXACT_LOCATION_KEYS
        or key.endswith(("_lat", "_lng", "_lon", "_latitude", "_longitude"))
        or any(part in key for part in _SENSITIVE_CONTAINER_PARTS)
    )


def _is_dynamic_mapping_container(key: str) -> bool:
    """Explicit schema contract for maps whose keys are data, not field names."""
    return key in _DYNAMIC_MAPPING_CONTAINERS


def _redact(value: Any, pseudo: _Pseudonymizer, *, key: str = "", depth: int = 0) -> Any:
    if depth > 24:
        return "<redacted:depth>"
    kind = _id_kind(key)
    if kind and value is not None:
        if isinstance(value, (list, tuple, set, frozenset)):
            return [pseudo.token(kind, item) for item in value]
        return pseudo.token(kind, value)
    if _is_location_key(key):
        return "<redacted:location-or-replay-input>"
    if any(part in key for part in _SENSITIVE_TEXT_PARTS):
        if value is None:
            return None
        if any(part in key for part in ("name", "restaurant", "customer")):
            return pseudo.token("text", value)
        return "<redacted:text>"
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        dynamic_keys = _is_dynamic_mapping_container(key)
        for raw_key, item in value.items():
            child_key = _norm_key(raw_key)
            rendered_key = str(raw_key)
            if dynamic_keys or re.fullmatch(r"\d{2,}", rendered_key) or re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{20,}", rendered_key
            ):
                rendered_key = pseudo.token("mapping-key", rendered_key)
            elif not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,95}", rendered_key):
                rendered_key = pseudo.token("mapping-key", rendered_key)
            out[rendered_key] = _redact(item, pseudo, key=child_key, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact(item, pseudo, key=key, depth=depth + 1) for item in value]
    if isinstance(value, str):
        if key in _SAFE_TEXT_KEYS:
            return value
        # Unknown strings are free text and therefore sensitive by default.
        return "<redacted:free-text>"
    return value


def redact_record(record: Mapping[str, Any], *, key: bytes, scope: str,
                  ledger: str) -> dict[str, Any]:
    """Return a versioned pseudonymised envelope; never mutate ``record``."""
    pseudo = _Pseudonymizer(key, scope)
    envelope = {
        "schema": SCHEMA,
        "ts": record.get("ts") if isinstance(record.get("ts"), str) else None,
        "ledger": ledger,
        "classification": "pseudonymized",
        "pseudonym_scope": scope,
        "record": _redact(dict(record), pseudo),
    }
    auth_key = hmac.new(
        key, b"ziomek-private-ledger-auth\0" + scope.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    canonical = json.dumps(
        envelope, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    auth_digest = hmac.new(auth_key, canonical, hashlib.sha256).digest()
    envelope["auth"] = "hmac-sha256-b64:" + base64.urlsafe_b64encode(
        auth_digest,
    ).decode("ascii").rstrip("=")
    return envelope


class SecureJsonlWriter:
    """0600 append writer with stable lock and replace detection."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)

    def append(self, record: Mapping[str, Any] | list[Any], *,
               pre_write_hook: Callable[[int, int, str], None] | None = None,
               post_lock_hook: Callable[[int, int, str], None] | None = None) -> None:
        payload = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        dir_fd = ensure_secure_directory(self.path.parent)
        name = self.path.name
        lock_name = f".{name}.lock"
        lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            lock_fd = os.open(lock_name, lock_flags, 0o600, dir_fd=dir_fd)
            try:
                lock_st = _validate_regular_fd(lock_fd, expected_mode=0o600, label="ledger lock")
                _validate_path_inode(dir_fd, lock_name, lock_st, "ledger lock")
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                if post_lock_hook is not None:
                    post_lock_hook(dir_fd, lock_fd, lock_name)
                _validate_path_inode(dir_fd, lock_name, lock_st, "ledger lock")
                base_flags = (os.O_WRONLY | os.O_APPEND
                              | getattr(os, "O_NOFOLLOW", 0)
                              | getattr(os, "O_CLOEXEC", 0))
                data_created = False
                try:
                    data_fd = os.open(name, base_flags, dir_fd=dir_fd)
                except FileNotFoundError:
                    try:
                        data_fd = os.open(
                            name, base_flags | os.O_CREAT | os.O_EXCL,
                            0o600, dir_fd=dir_fd,
                        )
                        data_created = True
                    except FileExistsError:
                        try:
                            data_fd = os.open(name, base_flags, dir_fd=dir_fd)
                        except OSError as exc:
                            raise PrivateLedgerError(
                                "ledger create race resolved to invalid path"
                            ) from exc
                    except OSError as exc:
                        raise PrivateLedgerError("ledger create failed") from exc
                except OSError as exc:
                    if exc.errno in {errno.ELOOP, errno.EMLINK}:
                        raise PrivateLedgerError("ledger symlink refused") from exc
                    raise
                try:
                    data_st = _validate_regular_fd(data_fd, expected_mode=0o600, label="ledger")
                    _validate_path_inode(dir_fd, name, data_st, "ledger")
                    if pre_write_hook is not None:
                        pre_write_hook(dir_fd, data_fd, name)
                    _validate_path_inode(dir_fd, name, data_st, "ledger")
                    offset = 0
                    while offset < len(payload):
                        written = os.write(data_fd, payload[offset:])
                        if written <= 0:
                            raise OSError("secure ledger write returned zero")
                        offset += written
                    os.fsync(data_fd)
                    _validate_path_inode(dir_fd, name, data_st, "ledger")
                    if data_created:
                        # Persist the first ledger directory entry only after
                        # its contents are durable.  Later appends need data
                        # fsync only; rename/reopen has its own dir fsyncs.
                        os.fsync(dir_fd)
                finally:
                    os.close(data_fd)
            finally:
                os.close(lock_fd)
        finally:
            os.close(dir_fd)


def rotate_secure_jsonl(path: str | os.PathLike[str], archive_name: str, *,
                        crash_hook: Callable[[str], None] | None = None) -> Path:
    """Rename/reopen rotation under the same lock used by appends.

    A crash after rename but before create is safe: the next append recreates a
    new 0600 current file.  Existing archives are never replaced.
    """
    current = Path(path)
    if Path(archive_name).name != archive_name or archive_name in {"", ".", ".."}:
        raise PrivateLedgerError("archive name must be a plain basename")
    dir_fd = ensure_secure_directory(current.parent)
    lock_name = f".{current.name}.lock"
    lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        lock_fd = os.open(lock_name, lock_flags, 0o600, dir_fd=dir_fd)
        try:
            lock_st = _validate_regular_fd(lock_fd, expected_mode=0o600, label="ledger lock")
            _validate_path_inode(dir_fd, lock_name, lock_st, "ledger lock")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            _validate_path_inode(dir_fd, lock_name, lock_st, "ledger lock")
            try:
                os.stat(archive_name, dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise PrivateLedgerError("archive already exists")
            read_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            data_fd = os.open(current.name, read_flags, dir_fd=dir_fd)
            try:
                data_st = _validate_regular_fd(data_fd, expected_mode=0o600, label="ledger")
                _validate_path_inode(dir_fd, current.name, data_st, "ledger")
                os.fsync(data_fd)
            finally:
                os.close(data_fd)
            os.rename(current.name, archive_name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.fsync(dir_fd)
            if crash_hook is not None:
                crash_hook("after_rename")
            create_flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            new_fd = os.open(current.name, create_flags, 0o600, dir_fd=dir_fd)
            try:
                new_st = _validate_regular_fd(new_fd, expected_mode=0o600, label="new ledger")
                _validate_path_inode(dir_fd, current.name, new_st, "new ledger")
                os.fsync(new_fd)
            finally:
                os.close(new_fd)
            os.fsync(dir_fd)
            if crash_hook is not None:
                crash_hook("after_reopen")
        finally:
            os.close(lock_fd)
    finally:
        os.close(dir_fd)
    return current.parent / archive_name


def configured_reader_key_provider() -> KeyProvider | None:
    """Return the configured secure key provider, never a default/fallback key."""
    path = os.environ.get("ZIOMEK_PRIVATE_LEDGER_KEY_FILE")
    return FileKeyProvider(path) if path else None


def path_is_private_ledger(path: str | os.PathLike[str]) -> bool:
    """Whether path is lexically inside the configured/default private root."""
    candidate = os.path.abspath(os.fspath(path))
    roots = {os.path.abspath(DEFAULT_PRIVATE_ROOT)}
    configured = os.environ.get("ZIOMEK_PRIVATE_LEDGER_ROOT")
    if configured:
        roots.add(os.path.abspath(configured))
    for root in roots:
        try:
            if os.path.commonpath((candidate, root)) == root:
                return True
        except ValueError:
            continue
    return False


def decode_ledger_record(record: Any, *, key_provider: KeyProvider | None = None) -> Any:
    """Old/new reader with fail-loud authentication for recognised private v1.

    Legacy dictionaries pass through unchanged.  Once a record identifies as
    ``private_ledger.v1``, missing key, malformed envelope, unsupported
    classification, or bad authentication is an input failure and propagates.
    It must never become an invisible skipped row.
    """
    if isinstance(record, dict) and record.get("schema") == SCHEMA:
        if record.get("classification") != "pseudonymized":
            raise PrivateLedgerError("private-ledger classification unsupported")
        scope = record.get("pseudonym_scope")
        auth = record.get("auth")
        inner = record.get("record")
        if (not isinstance(scope, str) or not scope or not isinstance(inner, dict)
                or not isinstance(auth, str) or not auth.startswith("hmac-sha256-b64:")):
            raise PrivateLedgerError("private-ledger record payload invalid")
        if key_provider is None:
            raise PrivateLedgerError("private-ledger key required")
        key = key_provider.key_for_scope(scope)
        unsigned = dict(record)
        unsigned.pop("auth", None)
        canonical = json.dumps(
            unsigned, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        auth_key = hmac.new(
            key, b"ziomek-private-ledger-auth\0" + scope.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        expected = base64.urlsafe_b64encode(
            hmac.new(auth_key, canonical, hashlib.sha256).digest(),
        ).decode("ascii").rstrip("=")
        if not hmac.compare_digest(auth[len("hmac-sha256-b64:"):], expected):
            raise PrivateLedgerError("private-ledger authentication failed")
        return inner
    return record


def iter_decoded_jsonl(lines: Iterator[str], *, key_provider: KeyProvider | None = None,
                       strict_json: bool = False) -> Iterator[dict[str, Any]]:
    """Decode JSONL while preserving legacy corruption semantics only for legacy.

    A non-empty malformed line is fatal when the file is declared private or
    after a private envelope has identified a mixed stream as private-aware.
    """
    seen_private = False
    for line in lines:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            if strict_json or seen_private:
                raise PrivateLedgerError(
                    "malformed or truncated private-ledger JSON record"
                ) from exc
            continue
        if isinstance(raw, dict) and raw.get("schema") == SCHEMA:
            seen_private = True
        decoded = decode_ledger_record(raw, key_provider=key_provider)
        if isinstance(decoded, dict):
            yield decoded


def iter_ledger_records(path: str | os.PathLike[str], *,
                        key_provider: KeyProvider | None = None,
                        private_file: bool | None = None) -> Iterator[dict[str, Any]]:
    strict_json = path_is_private_ledger(path) if private_file is None else private_file
    if not strict_json:
        with open(path, encoding="utf-8") as fh:
            yield from iter_decoded_jsonl(
                fh, key_provider=key_provider, strict_json=False,
            )
        return

    private_path = Path(path)
    dir_fd = ensure_secure_directory(private_path.parent, create=False)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        try:
            data_fd = os.open(private_path.name, flags, dir_fd=dir_fd)
        except OSError as exc:
            raise PrivateLedgerError("cannot open secure private-ledger reader") from exc
        try:
            data_st = _validate_regular_fd(
                data_fd, expected_mode=0o600, label="private-ledger reader",
            )
            _validate_path_inode(
                dir_fd, private_path.name, data_st, "private-ledger reader",
            )
            with os.fdopen(data_fd, "r", encoding="utf-8") as fh:
                data_fd = -1
                yield from iter_decoded_jsonl(
                    fh, key_provider=key_provider, strict_json=True,
                )
        finally:
            if data_fd >= 0:
                os.close(data_fd)
    finally:
        os.close(dir_fd)


def _private_target(config: LedgerConfig, ledger: str, legacy_path: str | os.PathLike[str]) -> Path:
    safe_ledger = re.sub(r"[^A-Za-z0-9_.-]+", "_", ledger).strip("._")
    if not safe_ledger:
        raise PrivateLedgerError("ledger name invalid")
    return Path(config.root) / safe_ledger / Path(legacy_path).name


def _minimal_status(config: LedgerConfig, ledger: str, error_type: str) -> None:
    status_path = Path(config.root) / "status" / f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', ledger)}.jsonl"
    SecureJsonlWriter(status_path).append({
        "schema": STATUS_SCHEMA,
        "ts": datetime.now(timezone.utc).isoformat(),
        "ledger": ledger,
        "status": "private_write_degraded",
        "error_type": error_type,
    })


def append_ledger_record(ledger: str, legacy_path: str | os.PathLike[str],
                         record: Mapping[str, Any], *,
                         config: LedgerConfig | None = None,
                         key_provider: KeyProvider | None = None) -> AppendOutcome:
    """Route one producer record according to the explicit compatibility mode."""
    cfg = config or LedgerConfig.from_env()
    if cfg.mode == "compat":
        from dispatch_v2.core.jsonl_appender import append_jsonl
        append_jsonl(legacy_path, dict(record))
        return AppendOutcome("compat", legacy_written=True, private_written=False)

    # Invalid mode/config must fail before it can silently omit both artifacts.
    cfg.validate()
    legacy_written = False

    try:
        provider = key_provider or FileKeyProvider(cfg.key_file or "")
        key = provider.key_for_scope(cfg.scope or "")
        envelope = redact_record(record, key=key, scope=cfg.scope or "", ledger=ledger)
        SecureJsonlWriter(_private_target(cfg, ledger, legacy_path)).append(envelope)
        return AppendOutcome(cfg.mode, legacy_written=legacy_written, private_written=True)
    except Exception as exc:
        error_type = type(exc).__name__
        try:
            _minimal_status(cfg, ledger, error_type)
        except Exception:
            pass
        # Callers must observe the failure.  In private mode no sensitive
        # compatibility fallback is permitted.  The identifier-free status is
        # supplemental telemetry, never a substitute for propagation.
        if isinstance(exc, PrivateLedgerError):
            raise
        raise PrivateLedgerError(
            f"private-ledger write failed ({error_type})"
        ) from exc


def private_mode_active(config: LedgerConfig | None = None) -> bool:
    return (config or LedgerConfig.from_env()).mode == "private"


def legacy_gc_allowed(config: LedgerConfig | None = None) -> bool:
    """Legacy writer GC is valid only while a legacy artifact is intentional."""
    return (config or LedgerConfig.from_env()).mode == "compat"
