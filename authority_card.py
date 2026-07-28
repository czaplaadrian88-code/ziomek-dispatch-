"""Runtime-gate podpisanej karty execution authority ``auto.canary.v1``.

Moduł jest jedynym właścicielem serializacji, weryfikacji podpisu audytowego,
liczników karty i zatrzasku AUTO-OFF. Każda niepewność kończy się odmową.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
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
        "stop_contract_sha256": "",
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
    })
    return state


def _state_valid(state: Any) -> bool:
    if not isinstance(state, dict) or frozenset(state) != _STATE_KEYS:
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


def load_state(path: str = STATE_PATH) -> Dict[str, Any]:
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


def save_state(path: str, state: Dict[str, Any]) -> None:
    """Zapis temp → fsync → rename → fsync katalogu."""
    if not _state_valid(state):
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


def check_limits(
    state: Dict[str, Any],
    now_ts: float,
    limits: Dict[str, Any],
) -> Tuple[bool, str]:
    """Limity pochodzą wyłącznie z podpisanej karty, nigdy z ``flags.json``."""
    if not _state_valid(state):
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


def latch_auto_off(
    state_path: str = STATE_PATH,
    reason: str = "unspecified",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Zatrzask jest monotoniczny: kolejne wywołanie nie zmienia pierwszej przyczyny."""
    state = load_state(state_path)
    if state.get("auto_off_latch") is True:
        if os.path.exists(state_path) and _state_valid(state):
            return state
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    state["auto_off_latch"] = True
    state["auto_off_reason"] = str(reason)
    state["auto_off_ts"] = now.astimezone(timezone.utc).isoformat()
    save_state(state_path, state)
    return state


def check_scope(
    record: Dict[str, Any],
    result: Any,
    payload: Optional[Dict[str, Any]],
    scope: Dict[str, Any],
) -> Tuple[bool, str]:
    """Sprawdza jawny snapshot siedmiu predykatów, bez heurystyk i fallbacków."""
    payload = payload or {}
    context = record.get("authority_scope")
    if context is None:
        context = payload.get("authority_scope")
    if context is None:
        context = getattr(result, "authority_scope", None)
    if not isinstance(context, dict):
        return False, "scope_evidence_missing"

    required = {
        "new_unassigned",
        "prior_assignment_count",
        "courier_bag_size",
        "route_pickups",
        "route_deliveries",
        "mode",
        "is_reassign",
        "is_alarm",
        "is_least_damage",
        "is_parcel",
        "is_multi_brand",
        "is_shared_pickup",
        "has_coordinator_override",
        "gps_source",
        "gps_age_sec",
        "no_gps_recommend_only_parity",
    }
    if not required.issubset(context):
        return False, "scope_evidence_missing"
    if (
        record.get("event_id") is None
        or "_NEW_ORDER_" not in str(record.get("event_id"))
        or payload.get("status_id") != 2
        or context.get("new_unassigned") is not True
        or context.get("prior_assignment_count") != 0
    ):
        return False, "scope_not_new_unassigned"
    if context.get("courier_bag_size") != 0:
        return False, "scope_bag_not_empty"
    if (
        context.get("route_pickups") != 1
        or context.get("route_deliveries") != 1
    ):
        return False, "scope_not_solo_route"
    if context.get("mode") != "normal":
        return False, "scope_not_normal_mode"

    excluded_evidence = {
        "reassign": "is_reassign",
        "alarm": "is_alarm",
        "least_damage": "is_least_damage",
        "parcel": "is_parcel",
        "multi_brand": "is_multi_brand",
        "shared_pickup": "is_shared_pickup",
        "coordinator_override": "has_coordinator_override",
    }
    for excluded in scope["excluded_contexts"]:
        if context.get(excluded_evidence[excluded]) is not False:
            return False, f"scope_excluded_{excluded}"

    gps = scope["gps"]
    age = context.get("gps_age_sec")
    if (
        context.get("gps_source") != gps["required_source"]
        or isinstance(age, bool)
        or not isinstance(age, (int, float))
        or float(age) < 0.0
        or float(age) > float(gps["max_age_sec"])
    ):
        return False, "scope_gps_not_live"
    if context.get("no_gps_recommend_only_parity") is not True:
        return False, "scope_no_gps_parity_unproven"
    return True, "ok"


def record_success(
    state_path: str,
    state: Dict[str, Any],
    oid: str,
    now: datetime,
) -> Dict[str, Any]:
    """Po potwierdzonym assignie zapisuje trzy sprzężone liczniki jednym rename."""
    updated = dict(state)
    updated["executed_total"] = int(updated.get("executed_total", 0)) + 1
    timestamps = list(updated.get("executed_ts") or [])
    timestamps.append(now.timestamp())
    updated["executed_ts"] = timestamps
    updated["in_flight"] = str(oid)
    pending = list(updated.get("pending_verification") or [])
    if str(oid) not in pending:
        pending.append(str(oid))
    updated["pending_verification"] = pending
    save_state(state_path, updated)
    return updated
