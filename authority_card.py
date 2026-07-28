"""Runtime-gate podpisanej karty execution authority ``auto.canary.v1``.

Moduł jest jedynym właścicielem serializacji, weryfikacji podpisu audytowego,
liczników karty i zatrzasku AUTO-OFF. Każda niepewność kończy się odmową.
"""
from __future__ import annotations

import glob
import fcntl
import hashlib
import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


CLASS_ID = "auto.canary.v1"
CARD_VERSION = 1
CARD_PATH = "/var/lib/ziomek-authority/cards/auto-canary-v1.json"
STATE_PATH = "/var/lib/ziomek-authority/state/auto-canary-v1.json"
AUDIT_PATH = (
    "/root/.openclaw/workspace/dispatch_state/"
    "coordinator_assign_audit.jsonl"
)
BUILD_SHA_PATH = "/root/.openclaw/workspace/scripts/BUILD_SHA"
AUDIT_TAIL_BYTES = 262144
# SHA-256 kanonicznego tekstu sekcji 3+4 karty
# /root/handover/KARTA_KLASY_AUTO_CANARY_2026-07-29.md.
#
# Algorytm 1:1:
# 1. wybierz od wiersza zaczynającego się ``## 3.`` (włącznie) do ostatniego
#    niepustego wiersza sekcji 4, przed separatorem ``---`` poprzedzającym
#    ``## 5.``;
# 2. usuń spacje i tabulatory z prawego końca każdego wybranego wiersza;
# 3. połącz wiersze pojedynczym ``\n``, bez końcowego ``\n``;
# 4. zakoduj UTF-8 i policz SHA-256.
EXPECTED_STOP_CONTRACT_SHA256 = (
    "91997392295092fd6cd3bc0d54926261d2074e94e74f67731cf4cd50c2aff42d"
)

_BODY_KEYS = frozenset({
    "class_id",
    "card_version",
    "issued_at",
    "valid_from",
    "valid_until",
    "scope",
    "limits",
    "stop_contract_sha256",
    "code_fingerprint",
    "owner_ack",
})
_SCOPE_KEYS = frozenset({
    "new_unassigned_only",
    "empty_bag_only",
    "solo_pickup_delivery_only",
    "normal_mode_only",
    "excluded_contexts",
    "gps",
    "no_gps_recommend_only_parity",
})
_EXCLUDED_CONTEXTS = (
    "reassign",
    "alarm",
    "least_damage",
    "parcel",
    "multi_brand",
    "shared_pickup",
    "coordinator_override",
)
_LIMIT_KEYS = frozenset({
    "max_per_hour",
    "max_in_flight",
    "max_total",
    "require_verification",
})
_STATE_KEYS = frozenset({
    "executed_total",
    "executed_ts",
    "in_flight",
    "pending_verification",
    "auto_off_latch",
    "auto_off_reason",
    "auto_off_ts",
})
_INITIALIZED_STATE_KEYS = _STATE_KEYS | {
    "initialized_for_card",
    "initialized_at",
}
_SYNTHETIC_STATE_KEYS = _STATE_KEYS | {"synthetic"}
_STATE_THREAD_LOCK = threading.RLock()
_STATE_LOCAL = threading.local()


@contextmanager
def state_lock(path: str = STATE_PATH):
    """One reentrant cross-process lock for limits, execution and card state."""
    depth = int(getattr(_STATE_LOCAL, "depth", 0) or 0)
    locked_path = getattr(_STATE_LOCAL, "path", None)
    if depth:
        if locked_path != os.path.abspath(path):
            raise RuntimeError("nested authority state lock path mismatch")
        _STATE_LOCAL.depth = depth + 1
        try:
            yield
        finally:
            _STATE_LOCAL.depth -= 1
        return
    with _STATE_THREAD_LOCK:
        lock_path = os.path.abspath(path) + ".lock"
        os.makedirs(os.path.dirname(lock_path), mode=0o700, exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            _STATE_LOCAL.depth = 1
            _STATE_LOCAL.path = os.path.abspath(path)
            yield
        finally:
            _STATE_LOCAL.depth = 0
            _STATE_LOCAL.path = None
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


@dataclass(frozen=True)
class CardVerdict:
    valid: bool
    reason: str
    card_sha256: Optional[str] = None
    body: Optional[Dict[str, Any]] = None


def canonical_body(body: Dict[str, Any]) -> bytes:
    """Kanoniczne bajty JSON: sortowanie kluczy, zero spacji, UTF-8."""
    return json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def card_sha256(body: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_body(body)).hexdigest()


def template_body() -> Dict[str, Any]:
    """Szkielet klasy; pola czasowe, hashe i fraza wymagają uzupełnienia."""
    return {
        "class_id": CLASS_ID,
        "card_version": CARD_VERSION,
        "issued_at": "",
        "valid_from": "",
        "valid_until": "",
        "scope": {
            "new_unassigned_only": True,
            "empty_bag_only": True,
            "solo_pickup_delivery_only": True,
            "normal_mode_only": True,
            "excluded_contexts": list(_EXCLUDED_CONTEXTS),
            "gps": {"required_source": "LIVE", "max_age_sec": 120},
            "no_gps_recommend_only_parity": True,
        },
        "limits": {
            "max_per_hour": 1,
            "max_in_flight": 1,
            "max_total": 3,
            "require_verification": True,
        },
        "stop_contract_sha256": EXPECTED_STOP_CONTRACT_SHA256,
        "code_fingerprint": {
            "git_sha": "",
            "flag_fingerprint": "",
        },
        "owner_ack": {"phrase": "", "ts": ""},
    }


def _pytest_active() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _is_production_path(path: str) -> bool:
    return os.path.abspath(path) in {
        os.path.abspath(CARD_PATH),
        os.path.abspath(STATE_PATH),
        os.path.abspath(AUDIT_PATH),
    }


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(ch in "0123456789abcdef" for ch in value.lower())


def _schema_reason(body: Any) -> Optional[str]:
    if not isinstance(body, dict) or frozenset(body) != _BODY_KEYS:
        return "card_schema_error"
    if body.get("class_id") != CLASS_ID:
        return "class_id_mismatch"
    if body.get("card_version") != CARD_VERSION:
        return "card_version_mismatch"
    for key in ("issued_at", "valid_from", "valid_until"):
        if _parse_ts(body.get(key)) is None:
            return "card_timestamp_invalid"

    scope = body.get("scope")
    if not isinstance(scope, dict) or frozenset(scope) != _SCOPE_KEYS:
        return "scope_schema_error"
    if any(
        scope.get(key) is not True
        for key in (
            "new_unassigned_only",
            "empty_bag_only",
            "solo_pickup_delivery_only",
            "normal_mode_only",
            "no_gps_recommend_only_parity",
        )
    ):
        return "scope_contract_mismatch"
    if scope.get("excluded_contexts") != list(_EXCLUDED_CONTEXTS):
        return "scope_contract_mismatch"
    gps = scope.get("gps")
    if gps != {"required_source": "LIVE", "max_age_sec": 120}:
        return "scope_contract_mismatch"

    limits = body.get("limits")
    if not isinstance(limits, dict) or frozenset(limits) != _LIMIT_KEYS:
        return "limits_schema_error"
    if any(
        isinstance(limits.get(key), bool)
        or not isinstance(limits.get(key), int)
        for key in ("max_per_hour", "max_in_flight", "max_total")
    ):
        return "limits_schema_error"
    if limits != {
        "max_per_hour": 1,
        "max_in_flight": 1,
        "max_total": 3,
        "require_verification": True,
    }:
        return "limits_contract_mismatch"

    if not _is_sha256(body.get("stop_contract_sha256")):
        return "stop_contract_sha_invalid"
    code_fp = body.get("code_fingerprint")
    if (
        not isinstance(code_fp, dict)
        or frozenset(code_fp) != {"git_sha", "flag_fingerprint"}
        or not isinstance(code_fp.get("git_sha"), str)
        or not code_fp.get("git_sha")
        or not isinstance(code_fp.get("flag_fingerprint"), str)
        or not code_fp.get("flag_fingerprint")
    ):
        return "code_fingerprint_invalid"
    owner_ack = body.get("owner_ack")
    if (
        not isinstance(owner_ack, dict)
        or frozenset(owner_ack) != {"phrase", "ts"}
        or not isinstance(owner_ack.get("phrase"), str)
        or not owner_ack.get("phrase").startswith(
            "podpisuję kartę AUTO-canary ")
        or _parse_ts(owner_ack.get("ts")) is None
    ):
        return "owner_ack_invalid"
    return None


def _last_card_signature(
    audit_path: str,
    class_id: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        size = os.path.getsize(audit_path)
    except FileNotFoundError:
        return None, "audit_missing"
    except OSError:
        return None, "audit_read_error"
    try:
        with open(audit_path, "r", encoding="utf-8", errors="strict") as stream:
            if size > AUDIT_TAIL_BYTES:
                stream.seek(size - AUDIT_TAIL_BYTES)
                stream.readline()
            lines = stream.readlines()
    except (OSError, UnicodeError):
        return None, "audit_read_error"

    found = None
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            return None, "audit_parse_error"
        if not isinstance(row, dict):
            return None, "audit_parse_error"
        if (
            row.get("kind") == "authority_card_signed"
            and row.get("class_id") == class_id
        ):
            found = row
    if found is None:
        return None, "audit_row_missing"
    return found, None


def read_code_git_sha(path: str = BUILD_SHA_PATH) -> Optional[str]:
    """Czyta SHA wbite przy deployu; nigdy nie uruchamia ``git``/subprocessu."""
    try:
        with open(path, "r", encoding="ascii", errors="strict") as stream:
            value = stream.read(256).strip()
    except (OSError, UnicodeError):
        return None
    if not value or any(ch not in "0123456789abcdef" for ch in value.lower()):
        return None
    return value


def verify_card(
    card_path: str = CARD_PATH,
    audit_path: str = AUDIT_PATH,
    now: Optional[datetime] = None,
    code_git_sha: Optional[str] = None,
    flag_fp: Optional[str] = None,
    expected_stop_contract_sha256: str = EXPECTED_STOP_CONTRACT_SHA256,
) -> CardVerdict:
    """Weryfikuje body, ostatni podpis PIN, czas i fingerprint; nigdy nie rzuca."""
    try:
        if (
            _pytest_active()
            and (_is_production_path(card_path) or _is_production_path(audit_path))
            and not os.environ.get("ALLOW_AUTHORITY_CARD_PROD_PATHS_IN_TEST")
        ):
            return CardVerdict(False, "pytest_prod_path_blocked")
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)

        try:
            with open(card_path, "r", encoding="utf-8", errors="strict") as stream:
                body = json.load(stream)
        except FileNotFoundError:
            return CardVerdict(False, "card_missing")
        except (json.JSONDecodeError, UnicodeError):
            return CardVerdict(False, "card_parse_error")
        except OSError:
            return CardVerdict(False, "card_read_error")

        schema_reason = _schema_reason(body)
        if schema_reason:
            return CardVerdict(False, schema_reason)
        digest = card_sha256(body)
        if body["stop_contract_sha256"] != expected_stop_contract_sha256:
            return CardVerdict(False, "stop_contract_mismatch", digest, body)
        signed, audit_error = _last_card_signature(audit_path, CLASS_ID)
        if audit_error:
            return CardVerdict(False, audit_error, digest, body)
        if signed is None:
            return CardVerdict(False, "audit_row_missing", digest, body)
        if signed.get("pin_verified") is not True:
            return CardVerdict(False, "pin_not_verified", digest, body)
        if signed.get("card_sha256") != digest:
            return CardVerdict(False, "sha_mismatch", digest, body)

        valid_from = _parse_ts(body["valid_from"])
        valid_until = _parse_ts(body["valid_until"])
        if valid_from is None or valid_until is None:
            return CardVerdict(False, "card_timestamp_invalid", digest, body)
        if now < valid_from:
            return CardVerdict(False, "card_not_yet_valid", digest, body)
        if now > valid_until:
            return CardVerdict(False, "card_expired", digest, body)

        if not code_git_sha:
            return CardVerdict(False, "code_git_sha_unavailable", digest, body)
        if not flag_fp:
            return CardVerdict(
                False, "flag_fingerprint_unavailable", digest, body
            )
        expected_fp = body["code_fingerprint"]
        if expected_fp["git_sha"] != code_git_sha:
            return CardVerdict(False, "git_sha_mismatch", digest, body)
        if expected_fp["flag_fingerprint"] != flag_fp:
            return CardVerdict(
                False, "flag_fingerprint_mismatch", digest, body
            )
        return CardVerdict(True, "ok", digest, body)
    except Exception:
        return CardVerdict(False, "verification_internal_error")


def empty_state() -> Dict[str, Any]:
    return {
        "executed_total": 0,
        "executed_ts": [],
        "in_flight": None,
        "pending_verification": [],
        "auto_off_latch": False,
        "auto_off_reason": None,
        "auto_off_ts": None,
    }


def _latched_state(reason: str) -> Dict[str, Any]:
    state = empty_state()
    state.update({
        "auto_off_latch": True,
        "auto_off_reason": reason,
        "auto_off_ts": datetime.now(timezone.utc).isoformat(),
        "synthetic": True,
    })
    return state


def _state_valid(state: Any) -> bool:
    if not isinstance(state, dict):
        return False
    keys = frozenset(state)
    if keys == _SYNTHETIC_STATE_KEYS:
        if (
            state.get("synthetic") is not True
            or state.get("auto_off_latch") is not True
        ):
            return False
    elif keys not in {_STATE_KEYS, _INITIALIZED_STATE_KEYS}:
        return False
    if keys == _INITIALIZED_STATE_KEYS:
        if (
            not _is_sha256(state.get("initialized_for_card"))
            or _parse_ts(state.get("initialized_at")) is None
        ):
            return False
    total = state.get("executed_total")
    timestamps = state.get("executed_ts")
    pending = state.get("pending_verification")
    in_flight = state.get("in_flight")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        return False
    if (
        not isinstance(timestamps, list)
        or any(
            isinstance(ts, bool) or not isinstance(ts, (int, float))
            for ts in timestamps
        )
    ):
        return False
    if in_flight is not None and not isinstance(in_flight, str):
        return False
    if (
        not isinstance(pending, list)
        or any(not isinstance(oid, str) for oid in pending)
    ):
        return False
    if not isinstance(state.get("auto_off_latch"), bool):
        return False
    if state.get("auto_off_reason") is not None and not isinstance(
        state.get("auto_off_reason"), str
    ):
        return False
    if state.get("auto_off_ts") is not None and _parse_ts(
        state.get("auto_off_ts")
    ) is None:
        return False
    return True


def _incomplete_temp_exists(path: str) -> bool:
    return bool(glob.glob(f"{glob.escape(path)}.tmp.*"))


def _load_state_unlocked(path: str) -> Dict[str, Any]:
    """Brak stanu oznacza zero; korupcja lub osierocony temp oznacza latch ON."""
    if (
        _pytest_active()
        and _is_production_path(path)
        and not os.environ.get("ALLOW_AUTHORITY_CARD_PROD_PATHS_IN_TEST")
    ):
        return _latched_state("pytest_prod_path_blocked")
    if _incomplete_temp_exists(path):
        return _latched_state("state_atomic_write_incomplete")
    try:
        with open(path, "r", encoding="utf-8", errors="strict") as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return empty_state()
    except Exception:
        return _latched_state("state_corrupt")
    if not _state_valid(state):
        return _latched_state("state_corrupt")
    return state


def load_state(path: str = STATE_PATH) -> Dict[str, Any]:
    with state_lock(path):
        return _load_state_unlocked(path)


def _save_state_unlocked(path: str, state: Dict[str, Any]) -> None:
    """Zapis temp → fsync → rename → fsync katalogu."""
    if not _state_valid(state) or state.get("synthetic") is True:
        raise ValueError("invalid authority card state")
    if (
        _pytest_active()
        and _is_production_path(path)
        and not os.environ.get("ALLOW_AUTHORITY_CARD_PROD_PATHS_IN_TEST")
    ):
        raise RuntimeError("authority card production state blocked under pytest")
    parent = os.path.dirname(os.path.abspath(path))
    tmp = f"{path}.tmp.{os.getpid()}"
    fd = None
    try:
        os.makedirs(parent, mode=0o700, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            json.dump(
                state,
                stream,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_state(path: str, state: Dict[str, Any]) -> None:
    with state_lock(path):
        _save_state_unlocked(path, state)


def initialize_state(
    state_path: str,
    card_sha256: str,
    now: datetime,
) -> Dict[str, Any]:
    """Atomowo utwórz pusty budżet związany z podpisaną kartą.

    Funkcja jest idempotentna wyłącznie dla tego samego SHA. Nigdy nie nadpisuje
    stanu innej karty, bo takie nadpisanie mogłoby wyzerować wykorzystany budżet.
    Tor podpisu wywołuje ją po trwałym receipt ``authority_card_signed`` i przed
    uznaniem podpisu za gotowy do użycia przez executor.
    """
    if not _is_sha256(card_sha256):
        raise ValueError("invalid card_sha256 for authority state")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    initialized = {
        **empty_state(),
        "initialized_for_card": card_sha256.lower(),
        "initialized_at": now.astimezone(timezone.utc).isoformat(),
    }
    with state_lock(state_path):
        if os.path.exists(state_path):
            current = _load_state_unlocked(state_path)
            if (
                current.get("synthetic") is not True
                and current.get("initialized_for_card") == card_sha256.lower()
            ):
                return current
            raise FileExistsError(
                "authority state already exists for another or unknown card"
            )
        _save_state_unlocked(state_path, initialized)
        return initialized


def _audit_value(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized


def _append_audit_row(path: str, row: Dict[str, Any]) -> None:
    """Trwały append wspólnego audytu; błąd jest fail-loud dla writera CLI."""
    if (
        _pytest_active()
        and _is_production_path(path)
        and not os.environ.get("ALLOW_AUTHORITY_CARD_PROD_PATHS_IN_TEST")
    ):
        raise RuntimeError("authority card production audit blocked under pytest")
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, mode=0o700, exist_ok=True)
    line = (
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    lock_fd = os.open(
        os.path.abspath(path) + ".append.lock",
        os.O_RDWR | os.O_CREAT,
        0o600,
    )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if os.fstat(fd).st_size:
                os.lseek(fd, -1, os.SEEK_END)
                if os.read(fd, 1) != b"\n":
                    line = b"\n" + line
            view = memoryview(line)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short authority audit write")
                view = view[written:]
            os.fsync(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        dir_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def check_limits(
    state: Dict[str, Any],
    now_ts: float,
    limits: Dict[str, Any],
) -> Tuple[bool, str]:
    """Limity pochodzą wyłącznie z podpisanej karty, nigdy z ``flags.json``."""
    if not _state_valid(state) or state.get("synthetic") is True:
        return False, "state_corrupt"
    if state.get("auto_off_latch") is True:
        return False, "latch_on"
    if int(state.get("executed_total", 0)) >= int(limits["max_total"]):
        return False, "max_total"
    recent = [
        float(ts)
        for ts in state.get("executed_ts", [])
        if 0.0 <= now_ts - float(ts) < 3600.0
    ]
    state["executed_ts"] = recent
    if len(recent) >= int(limits["max_per_hour"]):
        return False, "max_per_hour"
    if state.get("in_flight") is not None:
        return False, "in_flight"
    if (
        limits.get("require_verification") is True
        and state.get("pending_verification")
    ):
        return False, "pending_verification"
    return True, "ok"


def latch_auto_off_with_status(
    state_path: str = STATE_PATH,
    reason: str = "unspecified",
    now: Optional[datetime] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Merge pod lockiem; bool mówi czy ten call wykonał przejście OFF→ON."""
    with state_lock(state_path):
        state = _load_state_unlocked(state_path)
        if state.get("auto_off_latch") is True:
            if os.path.exists(state_path) and _state_valid(state):
                return state, False
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        state["auto_off_latch"] = True
        state["auto_off_reason"] = str(reason)
        state["auto_off_ts"] = now.astimezone(timezone.utc).isoformat()
        _save_state_unlocked(state_path, state)
        return state, True


def latch_auto_off(
    state_path: str = STATE_PATH,
    reason: str = "unspecified",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Zatrzask monotoniczny; modyfikuje tylko własne pola bieżącego stanu."""
    return latch_auto_off_with_status(state_path, reason, now)[0]


def clear_latch(
    state_path: str = STATE_PATH,
    reason: str = "",
    operator: str = "",
    now: Optional[datetime] = None,
    *,
    audit_path: str = AUDIT_PATH,
) -> Dict[str, Any]:
    """Za ACK ownera zdejmuje wyłącznie boolean latch; budżet zostaje nietknięty.

    Audyt zapisujemy przed stanem. Crash pomiędzy plikami może więc zostawić
    nadmiarowy wiersz audytu, ale nigdy cicho zdjęty latch — kierunek fail-safe.
    """
    reason = _audit_value(reason, "reason")
    operator = _audit_value(operator, "operator")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    with state_lock(state_path):
        state = _load_state_unlocked(state_path)
        if state.get("synthetic") is True:
            raise ValueError(
                "stan uszkodzony — wymagany ręczny reconcile, nie clear"
            )
        if state.get("auto_off_reason") == "state_missing":
            raise ValueError(
                "utracona historia budżetu — wymagany ręczny reconcile stanu, "
                "nie clear"
            )
        if state.get("auto_off_latch") is not True:
            raise ValueError("authority latch is not set")
        _append_audit_row(audit_path, {
            "ts": now.astimezone(timezone.utc).isoformat(),
            "kind": "authority_latch_cleared",
            "class_id": CLASS_ID,
            "operator": operator,
            "reason": reason,
            "previous_auto_off_reason": state.get("auto_off_reason"),
            "previous_auto_off_ts": state.get("auto_off_ts"),
        })
        updated = dict(state)
        updated["auto_off_latch"] = False
        _save_state_unlocked(state_path, updated)
        return updated


def check_scope(
    record: Dict[str, Any],
    result: Any,
    payload: Optional[Dict[str, Any]],
    scope: Dict[str, Any],
) -> Tuple[bool, str]:
    """Sprawdza ``authority_scope.v1`` 1:1, bez heurystyk i fallbacków."""
    context = record.get("authority_scope")
    if (
        not isinstance(context, dict)
        or context.get("schema") != "authority_scope.v1"
        or not isinstance(context.get("predicates"), dict)
    ):
        return False, "scope_evidence_missing"

    predicates = context["predicates"]
    names = {
        1: "1_new_unassigned",
        2: "2_empty_bag",
        3: "3_solo_plan",
        4: "4_mode",
        5: "5_exclusions",
        6: "6_winner_position",
        7: "7_no_gps_parity",
    }

    def _predicate(number: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        evidence = predicates.get(names[number])
        if (
            not isinstance(evidence, dict)
            or isinstance(evidence.get("absent"), str)
        ):
            return None, f"scope_{number}_absent"
        return evidence, None

    def _sources_present(evidence: Dict[str, Any], required: set[str]) -> bool:
        sources = evidence.get("sources")
        return (
            isinstance(sources, dict)
            and required.issubset(sources)
            and all(
                isinstance(sources.get(key), str) and sources[key].strip()
                for key in required
            )
        )

    p1, error = _predicate(1)
    if error:
        return False, error
    assert p1 is not None
    if not _sources_present(
        p1,
        {"event_type", "status_id", "assignment_history", "current_assignment"},
    ):
        return False, "scope_1_absent"
    if (
        p1.get("event_type") != "NEW_ORDER"
        or p1.get("status_id") != 2
        or p1.get("state_status") != "planned"
        or p1.get("prior_assignment_count") != 0
        or p1.get("currently_assigned") is not False
    ):
        return False, "scope_not_new_unassigned"

    p2, error = _predicate(2)
    if error:
        return False, error
    assert p2 is not None
    if not _sources_present(p2, {"bag", "generation"}):
        return False, "scope_2_absent"
    generation = p2.get("generation")
    if (
        p2.get("bag_size") != 0
        or p2.get("active_order_ids") != []
        or p2.get("soon_free_applied") is not False
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 0
    ):
        return False, "scope_bag_not_empty"

    p3, error = _predicate(3)
    if error:
        return False, error
    assert p3 is not None
    if not _sources_present(p3, {"pickups", "deliveries"}):
        return False, "scope_3_absent"
    if (
        p3.get("n_pickups") != 1
        or p3.get("n_deliveries") != 1
    ):
        return False, "scope_not_solo_route"

    p4, error = _predicate(4)
    if error:
        return False, error
    assert p4 is not None
    if not isinstance(p4.get("source"), str) or not p4["source"].strip():
        return False, "scope_4_absent"
    if p4.get("mode") != "normal":
        return False, "scope_not_normal_mode"

    p5, error = _predicate(5)
    if error:
        return False, error
    assert p5 is not None
    excluded_evidence = {
        "reassign": "reassign",
        "alarm": "alarm",
        "least_damage": "least_damage",
        "parcel": "parcel",
        "multi_brand": "multi_brand",
        "shared_pickup": "shared_pickup",
        "coordinator_override": "coordinator_override",
    }
    for excluded in scope["excluded_contexts"]:
        evidence = p5.get(excluded_evidence[excluded])
        if (
            not isinstance(evidence, dict)
            or isinstance(evidence.get("absent"), str)
            or not isinstance(evidence.get("source"), str)
            or not evidence["source"].strip()
        ):
            return False, "scope_5_absent"
        if evidence.get("value") is not False:
            return False, f"scope_excluded_{excluded}"

    p6, error = _predicate(6)
    if error:
        return False, error
    assert p6 is not None
    if not _sources_present(p6, {"position", "age", "contract"}):
        return False, "scope_6_absent"
    gps = scope["gps"]
    age = p6.get("age_seconds")
    if (
        not isinstance(p6.get("pos_source"), str)
        or p6.get("contract") != gps["required_source"]
        or isinstance(age, bool)
        or not isinstance(age, (int, float))
        or float(age) < 0.0
        or float(age) > float(gps["max_age_sec"])
    ):
        return False, "scope_gps_not_live"

    p7, error = _predicate(7)
    if error:
        return False, error
    assert p7 is not None
    if not isinstance(p7.get("source"), str) or not p7["source"].strip():
        return False, "scope_7_absent"
    if p7.get("verified") is not True:
        return False, "scope_no_gps_parity_unproven"
    return True, "ok"


def reserve_execution(
    state_path: str,
    state: Dict[str, Any],
    oid: str,
    now: datetime,
) -> Dict[str, Any]:
    """Trwale rezerwuje intencję wykonania PRZED zewnętrznym runnerem.

    ``state`` pozostaje w sygnaturze dla kompatybilności callerów, ale celowo nie
    jest źródłem zapisu: może być snapshotem sprzed fresh solve. Rezerwacja
    zużywa budżet, ustawia ``in_flight`` i ``pending_verification`` w jednym
    fsyncowanym zapisie. Replay widzi ją nawet po śmierci executora.
    """
    with state_lock(state_path):
        del state
        updated = _load_state_unlocked(state_path)
        if updated.get("synthetic") is True:
            raise ValueError("cannot reserve against synthetic authority state")
        updated["executed_total"] = int(updated.get("executed_total", 0)) + 1
        timestamps = list(updated.get("executed_ts") or [])
        timestamps.append(now.timestamp())
        updated["executed_ts"] = timestamps
        updated["in_flight"] = str(oid)
        pending = list(updated.get("pending_verification") or [])
        if str(oid) not in pending:
            pending.append(str(oid))
        updated["pending_verification"] = pending
        _save_state_unlocked(state_path, updated)
        return updated


def record_success(
    state_path: str,
    state: Dict[str, Any],
    oid: str,
    now: datetime,
) -> Dict[str, Any]:
    """Kompatybilny alias: historyczna funkcja jest dziś rezerwacją pre-runner."""
    return reserve_execution(state_path, state, oid, now)


def rollback_execution_reservation(
    state_path: str,
    oid: str,
    reserved_at: datetime,
    reason: str,
    *,
    audit_path: str = AUDIT_PATH,
) -> Dict[str, Any]:
    """Wycofaj wyłącznie własną rezerwację po udowodnionej odmowie pre-send.

    Audyt jest fsyncowany przed zmianą stanu. Crash pomiędzy zapisami pozostawia
    bezpieczniejszą rezerwację (replay nadal zablokowany), nigdy cichy zwrot
    budżetu bez śladu.
    """
    oid = _audit_value(oid, "oid")
    reason = _audit_value(reason, "reason")
    if reserved_at.tzinfo is None:
        reserved_at = reserved_at.replace(tzinfo=timezone.utc)
    reserved_ts = reserved_at.timestamp()
    with state_lock(state_path):
        state = _load_state_unlocked(state_path)
        if state.get("synthetic") is True:
            raise ValueError("cannot rollback synthetic authority state")
        pending = list(state.get("pending_verification") or [])
        if state.get("in_flight") != oid or oid not in pending:
            raise ValueError(f"execution reservation {oid} is not active")
        timestamps = list(state.get("executed_ts") or [])
        try:
            timestamp_index = len(timestamps) - 1 - timestamps[::-1].index(
                reserved_ts
            )
        except ValueError as exc:
            raise ValueError(
                f"execution reservation {oid} timestamp is missing"
            ) from exc
        if int(state.get("executed_total", 0)) <= 0:
            raise ValueError(f"execution reservation {oid} budget is missing")

        _append_audit_row(audit_path, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": "reservation_rolled_back",
            "class_id": CLASS_ID,
            "oid": oid,
            "reason": reason,
            "reserved_at": reserved_at.astimezone(timezone.utc).isoformat(),
        })
        updated = dict(state)
        updated["executed_total"] = int(state["executed_total"]) - 1
        timestamps.pop(timestamp_index)
        updated["executed_ts"] = timestamps
        updated["pending_verification"] = [
            pending_oid for pending_oid in pending if pending_oid != oid
        ]
        updated["in_flight"] = None
        _save_state_unlocked(state_path, updated)
        return updated


def record_verification(
    state_path: str,
    oid: str,
    operator: str,
    now: datetime,
    *,
    audit_path: str = AUDIT_PATH,
) -> Dict[str, Any]:
    """Zapis koordynatora: zwalnia dokładnie wskazane wykonanie i dopisuje audyt."""
    oid = _audit_value(oid, "oid")
    operator = _audit_value(operator, "operator")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    with state_lock(state_path):
        state = _load_state_unlocked(state_path)
        pending = list(state.get("pending_verification") or [])
        if oid not in pending and state.get("in_flight") != oid:
            raise ValueError(f"execution {oid} is not pending verification")
        _append_audit_row(audit_path, {
            "ts": now.astimezone(timezone.utc).isoformat(),
            "kind": "authority_execution_verified",
            "class_id": CLASS_ID,
            "oid": oid,
            "operator": operator,
        })
        updated = dict(state)
        updated["pending_verification"] = [
            pending_oid for pending_oid in pending if pending_oid != oid
        ]
        if updated.get("in_flight") == oid:
            updated["in_flight"] = None
        _save_state_unlocked(state_path, updated)
        return updated
