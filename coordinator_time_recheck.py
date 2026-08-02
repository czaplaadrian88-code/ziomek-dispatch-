"""Kolejka WYMUSZONEGO odświeżenia czasów (czas_kuriera / pickup_at) z rutcomu na
ŻĄDANIE koordynatora (przycisk „Odśwież czas" w konsoli).

Po co: automatyczny re-check (panel_watcher ORDER-TIME RE-CHECK) świadomie pomija
elastyki w statusie `planned` (koszt) i blokuje cofnięcia czas_kuriera elastyka
(forward-only, anty-migotanie vs śmieciowe przeklepywanie gastro). Gdy KOORDYNATOR
RĘCZNIE zmieni czas w rutcomie i kliknie przycisk, to ŚWIADOMA akcja człowieka —
chcemy ściągnąć nowy czas dla DOWOLNEGO zlecenia (też planned) i w OBIE strony, bez
osłabiania automatu. Kliknięcie = dyskryminator „to nie śmieć, to decyzja".

Przepływ: panel (subprocess w venv Ziomka, jak courier_block) → `enqueue(oids)` →
panel_watcher raz na tick → `pending_with_receipts()` → re-check z trwałym
receiptem v5 → jednorazowy claim dokładnego eventu → `ack_receipts()` dopiero
po udanym fetch/apply. Claim jest niezmiennym headem per OID. Ponowny klik
podczas jego obsługi tworzy coalesced successor, który staje się headem dopiero
po exact ACK poprzednika. Sam bool ``deliberate`` nie jest autorytetem dla
czasówki. Atomic + flock + TTL; crash po odczycie nie gubi intencji. Z3: jedno
źródło prawdy zapisu.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Optional

from dispatch_v2 import common as C
from dispatch_v2.committed_pickup_authority import (
    COMMITTED_TIME_POLICY_SNAPSHOT_FIELD,
    MANUAL_CK_AUTHORITY_FLAG,
    RUTCOM_FORWARD_AUTHORITY_FLAG,
    CommittedPickupPolicySnapshot,
    deserialize_coordinator_receipt_policy,
    serialize_committed_time_policy,
)

QUEUE_PATH = "/root/.openclaw/workspace/dispatch_state/coordinator_time_recheck.json"
LOCK_PATH = QUEUE_PATH + ".lock"
DEFAULT_TTL_MIN = 5.0  # klik starszy niż to = „przeterminowany" (watcher mógł stać) → ignoruj
RECEIPT_SCHEMA = "coordinator_time_recheck.v6"
PRE_POLICY_RECEIPT_SCHEMA = "coordinator_time_recheck.v5"
LEGACY_RECEIPT_SCHEMA = "coordinator_time_recheck.v4"
CLAIM_SCHEMA = "coordinator_time_recheck.claim.v1"
SUCCESSOR_FIELD = "successor"
ELIGIBLE_AT_FIELD = "eligible_at"
_CLAIM_CONTINUATION_FIELD = "continue_after_ack"
CONTINUATION_DEPTH_FIELD = "continuation_depth"
_MAX_LEGACY_CONTINUATION_DEPTH = 1
_LEGACY_CLAIMABLE_EVENT_TYPES = frozenset(
    {"CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"}
)
_LEGACY_EVENT_SEMANTIC_KEYS = frozenset(
    {"event_type", "order_id", "courier_id", "payload"}
)
_LEGACY_DURABLE_BOOLEAN_KEYS = {
    "CZAS_KURIERA_UPDATED": frozenset(
        {"saved_plans_authorized", "committed_invalidates_view_authorized"}
    ),
    "PICKUP_TIME_UPDATED": frozenset(
        {
            "saved_plans_authorized",
            "committed_invalidates_view_authorized",
            "czasowka_reclaim_shadow_authorized",
            "czasowka_reclaim_live_authorized",
        }
    ),
}
_FUTURE_SKEW = timedelta(seconds=30)
_ROLLBACK_FENCE_SCHEMA = "coordinator_time_recheck.rollback_fence.v2"
_ROLLBACK_TARGET = "pre-v4-legacy-queue"
_FORWARD_FENCE_SCHEMA = "coordinator_time_recheck.forward_fence.v1"
_FORWARD_FENCE_TARGET = "rutcom-forward-authority-rollout"
_QUEUE_RECEIPT_SOURCES = frozenset(
    {"coordinator_panel", "coordinator_console", "legacy_coordinator_queue"}
)
_BASE_UNCLAIMED_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "request_id",
        "order_id",
        "requested_at",
        ELIGIBLE_AT_FIELD,
        "source",
        CONTINUATION_DEPTH_FIELD,
    }
)
_UNCLAIMED_RECEIPT_FIELDS = (
    _BASE_UNCLAIMED_RECEIPT_FIELDS
    | {COMMITTED_TIME_POLICY_SNAPSHOT_FIELD}
)
_V5_UNCLAIMED_RECEIPT_FIELDS = _BASE_UNCLAIMED_RECEIPT_FIELDS
_V5_UNCLAIMED_RECEIPT_FIELDS_NO_DEPTH = (
    _V5_UNCLAIMED_RECEIPT_FIELDS - {CONTINUATION_DEPTH_FIELD}
)
_V4_UNCLAIMED_RECEIPT_FIELDS = (
    _BASE_UNCLAIMED_RECEIPT_FIELDS - {ELIGIBLE_AT_FIELD}
)
_V4_UNCLAIMED_RECEIPT_FIELDS_NO_DEPTH = (
    _V4_UNCLAIMED_RECEIPT_FIELDS - {CONTINUATION_DEPTH_FIELD}
)


def _rollback_fence_path() -> str:
    return QUEUE_PATH + ".legacy-rollback-fence"


def _forward_fence_path() -> str:
    return QUEUE_PATH + ".forward-authority-fence"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def receipt_base(receipt: object) -> Optional[dict]:
    """Publiczny, niemutowalny dowód kliknięcia bez transportowego claimu."""
    if not isinstance(receipt, Mapping):
        return None
    depth_raw = receipt.get(CONTINUATION_DEPTH_FIELD, 0)
    if (
        isinstance(depth_raw, bool)
        or not isinstance(depth_raw, int)
        or depth_raw < 0
        or depth_raw > _MAX_LEGACY_CONTINUATION_DEPTH
    ):
        return None
    schema = receipt.get("schema")
    requested_at = receipt.get("requested_at")
    if schema in {RECEIPT_SCHEMA, PRE_POLICY_RECEIPT_SCHEMA}:
        eligible_at = receipt.get(ELIGIBLE_AT_FIELD)
    elif schema == LEGACY_RECEIPT_SCHEMA:
        # v4 had one overloaded clock. Normalize it only in the public proof;
        # the raw exact-shape validator below still attests the old envelope.
        eligible_at = requested_at
    else:
        return None
    base = {
        "schema": schema,
        "request_id": receipt.get("request_id"),
        "order_id": receipt.get("order_id"),
        "requested_at": requested_at,
        ELIGIBLE_AT_FIELD: eligible_at,
        "source": receipt.get("source"),
        CONTINUATION_DEPTH_FIELD: depth_raw,
    }
    if schema == RECEIPT_SCHEMA:
        try:
            policy = deserialize_coordinator_receipt_policy(receipt)
        except (TypeError, ValueError):
            return None
        base[COMMITTED_TIME_POLICY_SNAPSHOT_FIELD] = (
            serialize_committed_time_policy(policy)
        )
    if not all(str(base.get(k) or "").strip() for k in (
        "request_id",
        "order_id",
        "requested_at",
        ELIGIBLE_AT_FIELD,
        "source",
    )):
        return None
    return base


def _receipt_clock_pair(
    receipt: object,
) -> Optional[tuple[datetime, datetime]]:
    """Return normalized audit/eligibility clocks for one valid envelope.

    ``requested_at`` is immutable audit time. ``eligible_at`` may move only
    forward when a parked successor becomes executable; accepting an earlier
    eligibility epoch would let a malformed envelope rewrite that causality.
    """
    base = receipt_base(receipt)
    if base is None:
        return None
    try:
        requested_at = datetime.fromisoformat(str(base["requested_at"]))
        eligible_at = datetime.fromisoformat(
            str(base[ELIGIBLE_AT_FIELD])
        )
    except (TypeError, ValueError):
        return None
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=timezone.utc)
    if eligible_at.tzinfo is None:
        eligible_at = eligible_at.replace(tzinfo=timezone.utc)
    if eligible_at < requested_at:
        return None
    return requested_at, eligible_at


def _json_copy(value: object):
    """Detached JSON value; public queue APIs never leak mutable live records."""
    return json.loads(json.dumps(value, ensure_ascii=False))


def _queue_snapshot_sha256(data: Mapping[str, object]) -> str:
    payload = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_continuation_depth(continuation_depth: object) -> int:
    if (
        isinstance(continuation_depth, bool)
        or not isinstance(continuation_depth, int)
        or not 0 <= continuation_depth <= _MAX_LEGACY_CONTINUATION_DEPTH
    ):
        raise ValueError("invalid coordinator continuation depth")
    return continuation_depth


def _new_receipt(
    order_id: str,
    *,
    requested_at: str,
    source: str,
    policy_snapshot: CommittedPickupPolicySnapshot,
    continuation_depth: int = 0,
) -> dict:
    continuation_depth = _validate_continuation_depth(continuation_depth)
    if type(policy_snapshot) is not CommittedPickupPolicySnapshot:
        raise TypeError(
            "policy_snapshot must be CommittedPickupPolicySnapshot"
        )
    if policy_snapshot.producer != "coordinator_queue":
        raise ValueError("coordinator receipt requires queue-owned policy")
    return {
        "schema": RECEIPT_SCHEMA,
        "request_id": uuid.uuid4().hex,
        "order_id": str(order_id),
        "requested_at": requested_at,
        ELIGIBLE_AT_FIELD: requested_at,
        "source": source,
        CONTINUATION_DEPTH_FIELD: continuation_depth,
        COMMITTED_TIME_POLICY_SNAPSHOT_FIELD: (
            serialize_committed_time_policy(policy_snapshot)
        ),
    }


def _new_pre_policy_receipt(
    order_id: str,
    *,
    requested_at: str,
    source: str,
    continuation_depth: int = 0,
) -> dict:
    """Compatibility envelope that can never gain committed authority."""
    continuation_depth = _validate_continuation_depth(continuation_depth)
    return {
        "schema": PRE_POLICY_RECEIPT_SCHEMA,
        "request_id": uuid.uuid4().hex,
        "order_id": str(order_id),
        "requested_at": requested_at,
        ELIGIBLE_AT_FIELD: requested_at,
        "source": source,
        CONTINUATION_DEPTH_FIELD: continuation_depth,
    }


def _coordinator_policy_snapshot() -> CommittedPickupPolicySnapshot:
    """Capture one click-time lease while the queue flock is held."""
    return CommittedPickupPolicySnapshot(
        producer="coordinator_queue",
        manual_passthrough_enabled=bool(
            C.decision_flag(MANUAL_CK_AUTHORITY_FLAG)
        ),
        rutcom_forward_authority_enabled=bool(
            C.decision_flag(RUTCOM_FORWARD_AUTHORITY_FLAG)
        ),
        passive_guard_enabled=bool(
            C.flag("ENABLE_CZASOWKA_CK_PASSIVE_GUARD", True)
        ),
    )


def _head_record(record: object) -> Optional[dict]:
    """Stable transaction identity, excluding only its queued successor."""
    if not isinstance(record, Mapping):
        return None
    head = dict(record)
    head.pop(SUCCESSOR_FIELD, None)
    return head


def _same_head(left: object, right: object) -> bool:
    left_head = _head_record(left)
    right_head = _head_record(right)
    return bool(
        left_head is not None
        and right_head is not None
        and left_head == right_head
    )


def _valid_unclaimed_receipt_shape(
    record: object,
    *,
    order_id: str,
) -> bool:
    """One exact shape owner shared by runtime and rollback projection."""
    if not isinstance(record, Mapping):
        return False
    base = receipt_base(record)
    return bool(
        base is not None
        and _receipt_clock_pair(record) is not None
        and str(base.get("order_id") or "") == str(order_id)
        and base.get("source") in _QUEUE_RECEIPT_SOURCES
        and record.get("claim") is None
        and SUCCESSOR_FIELD not in record
        and frozenset(record) in (
            {_UNCLAIMED_RECEIPT_FIELDS}
            if record.get("schema") == RECEIPT_SCHEMA
            else (
                {
                    _V5_UNCLAIMED_RECEIPT_FIELDS,
                    _V5_UNCLAIMED_RECEIPT_FIELDS_NO_DEPTH,
                }
                if record.get("schema") == PRE_POLICY_RECEIPT_SCHEMA
                else {
                    _V4_UNCLAIMED_RECEIPT_FIELDS,
                    _V4_UNCLAIMED_RECEIPT_FIELDS_NO_DEPTH,
                }
            )
        )
    )


def _successor_record(
    record: Mapping[str, object],
    *,
    order_id: str,
) -> tuple[bool, Optional[dict]]:
    """Return (present, valid successor); invalid is an explicit blocker."""
    if SUCCESSOR_FIELD not in record:
        return False, None
    raw = record.get(SUCCESSOR_FIELD)
    if not _valid_unclaimed_receipt_shape(raw, order_id=order_id):
        return True, None
    return True, _json_copy(dict(raw))


def _fresh_receipt(
    receipt: object,
    *,
    order_id: str,
    now: datetime,
    ttl_min: float = DEFAULT_TTL_MIN,
) -> bool:
    base = receipt_base(receipt)
    if base is None or str(base["order_id"]) != str(order_id):
        return False
    if base["source"] not in _QUEUE_RECEIPT_SOURCES:
        return False
    clocks = _receipt_clock_pair(receipt)
    if clocks is None:
        return False
    _requested_at, eligible_at = clocks
    age = now - eligible_at
    return -_FUTURE_SKEW <= age <= timedelta(minutes=ttl_min)


def _event_sha256(event: Mapping[str, object]) -> str:
    raw = json.dumps(
        dict(event),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _legacy_claim_event_key(
    event: Mapping[str, object],
    *,
    order_id: str,
) -> Optional[str]:
    """Zwróć neutralny klucz wyłącznie dla exact legacy force-time eventu.

    Claim transportowy nie nadaje authority committed czasówce. Dlatego ścieżka
    legacy akceptuje tylko dwa historyczne typy, exact OID i surowe źródło
    ``coordinator_force`` oraz odrzuca każdy częściowy artefakt nowego kontraktu.
    """
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    event_type = event.get("event_type")
    if (
        event_type not in _LEGACY_CLAIMABLE_EVENT_TYPES
        or str(event.get("order_id") or "") != str(order_id)
        or payload.get("source") != "coordinator_force"
    ):
        return None
    if any(str(key).startswith("committed_") for key in payload):
        return None
    if any(str(key).startswith("committed_") for key in event):
        return None
    if (
        event.get("event_id") is not None
        or event.get("event_id_hint") is not None
    ):
        return None
    return (
        f"coordinator-force-time:{order_id}:{event_type}:"
        f"{_event_sha256(event)}"
    )


def _claim_event_key(
    event: Mapping[str, object],
    *,
    order_id: str,
) -> Optional[str]:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    committed_key = str(payload.get("committed_pickup_event_key") or "")
    if committed_key:
        return committed_key
    return _legacy_claim_event_key(event, order_id=order_id)


def _claimed_event(
    record: object,
    *,
    order_id: str,
) -> Optional[dict]:
    """Zweryfikuj pełne, nieprzeterminowujące się zobowiązanie claim→event.

    TTL ogranicza czas na *utworzenie* claimu. Po atomowym związaniu kliknięcia
    z dokładnym eventem rekord żyje aż do exact ACK; inaczej crash dłuższy niż
    pięć minut bezpowrotnie kasowałby już autoryzowaną intencję.
    """
    if not isinstance(record, Mapping):
        return None
    base = receipt_base(record)
    if base is None or str(base.get("order_id") or "") != str(order_id):
        return None
    if base.get("source") not in _QUEUE_RECEIPT_SOURCES:
        return None
    if _receipt_clock_pair(record) is None:
        return None
    claim = record.get("claim")
    event = claim.get("event") if isinstance(claim, Mapping) else None
    if not isinstance(event, Mapping) or claim.get("schema") != CLAIM_SCHEMA:
        return None
    event_copy = dict(event)
    payload = event_copy.get("payload")
    if not isinstance(payload, Mapping):
        return None
    event_key = _claim_event_key(event_copy, order_id=order_id)
    if (
        str(event_copy.get("order_id") or "") != str(order_id)
        or not event_key
        or claim.get("event_sha256") != _event_sha256(event_copy)
        or claim.get("event_key")
        != event_key
    ):
        return None
    if payload.get("committed_pickup_event_key"):
        proof = payload.get("committed_authority_proof")
        observation = (
            proof.get("observation") if isinstance(proof, Mapping) else None
        )
        event_receipt = receipt_base(
            observation.get("authority_receipt")
            if isinstance(observation, Mapping)
            else None
        )
        if event_receipt != base:
            return None
    elif _legacy_claim_event_key(event_copy, order_id=order_id) is None:
        return None
    return json.loads(json.dumps(event_copy, ensure_ascii=False))


@contextlib.contextmanager
def _lockfile():
    """File lock dla atomic read-modify-write (panel pisze, watcher drenuje)."""
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        fh.close()


def _load() -> dict:
    if not os.path.exists(QUEUE_PATH):
        return {}
    try:
        with open(QUEUE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(
            f"coordinator time queue unreadable: {QUEUE_PATH}"
        ) from exc
    if not isinstance(d, dict):
        raise RuntimeError(
            f"coordinator time queue root is not an object: {QUEUE_PATH}"
        )
    return d


def rollback_records_snapshot() -> dict:
    """Return an exact, detached queue view without TTL cleanup or writes."""
    with _lockfile():
        return _json_copy(_load())


def rollback_record_is_unclaimed(
    record: object,
    *,
    order_id: str,
) -> bool:
    """Expose the runtime's exact unclaimed-envelope oracle to preflight."""
    return _valid_unclaimed_receipt_shape(record, order_id=str(order_id))


def receipt_policy_snapshot(
    record: object,
) -> Optional[CommittedPickupPolicySnapshot]:
    """Read the exact v6 policy without consulting mutable live flags."""
    try:
        return deserialize_coordinator_receipt_policy(record)
    except (TypeError, ValueError):
        return None


def _save(data: dict) -> None:
    """Atomic write (temp + fsync + rename). Caller trzyma lock."""
    p = Path(QUEUE_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="coordinator_time_recheck.", suffix=".tmp",
                               dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, QUEUE_PATH)
        dir_fd = os.open(str(p.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _atomic_replace_bytes(path: Path, payload: bytes) -> None:
    """Exact-byte atomic restore used only inside the rollback transaction."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.restore.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _unlink_durable(path: Path) -> None:
    """Remove one exact transaction-owned artifact and fsync its directory."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    dir_fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _write_once(path: Path, payload: bytes) -> None:
    """Create and fsync a 0600 artifact without overwriting prior evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        dir_fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def _forward_rollout_fence_status_unlocked(
    queue_data: Optional[Mapping[str, object]] = None,
) -> dict:
    """Validate the crash-safe fence that closes enqueue→flag-flip TOCTOU."""
    fence = Path(_forward_fence_path())
    result = {
        "forward_fence_present": fence.exists(),
        "forward_fence_valid": False,
        "forward_fence_error": None,
        "forward_fence_id": None,
        "forward_fence_queue_sha256": None,
    }
    if not result["forward_fence_present"]:
        return result
    try:
        receipt = json.loads(fence.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        result["forward_fence_error"] = "fence_unreadable_or_invalid_json"
        return result
    expected_keys = {
        "schema",
        "created_at",
        "target",
        "fence_id",
        "queue_sha256",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != expected_keys:
        result["forward_fence_error"] = "fence_invalid_shape"
        return result
    if receipt.get("schema") != _FORWARD_FENCE_SCHEMA:
        result["forward_fence_error"] = "fence_schema_mismatch"
        return result
    if receipt.get("target") != _FORWARD_FENCE_TARGET:
        result["forward_fence_error"] = "fence_target_mismatch"
        return result
    try:
        created_at = datetime.fromisoformat(str(receipt.get("created_at")))
        fence_id = str(uuid.UUID(str(receipt.get("fence_id"))))
    except (TypeError, ValueError):
        result["forward_fence_error"] = "fence_identity_invalid"
        return result
    if created_at.tzinfo is None:
        result["forward_fence_error"] = "fence_created_at_naive"
        return result
    expected_sha = str(receipt.get("queue_sha256") or "")
    if len(expected_sha) != 64:
        result["forward_fence_error"] = "fence_queue_sha256_invalid"
        return result
    if queue_data is None:
        try:
            queue_data = _load()
        except RuntimeError:
            result["forward_fence_error"] = "queue_unreadable"
            return result
    if _queue_snapshot_sha256(queue_data) != expected_sha:
        result["forward_fence_error"] = "fenced_queue_changed"
        return result
    result.update(
        {
            "forward_fence_valid": True,
            "forward_fence_id": fence_id,
            "forward_fence_queue_sha256": expected_sha,
        }
    )
    return result


def forward_rollout_fence_status() -> dict:
    """Read-only proof that no new coordinator request can cross the flip."""
    with _lockfile():
        data = _load()
        return _forward_rollout_fence_status_unlocked(data)


def acquire_forward_rollout_fence() -> dict:
    """Atomically freeze enqueue against the exact current queue snapshot."""
    with _lockfile():
        if os.path.exists(_rollback_fence_path()):
            raise RuntimeError("legacy rollback fence is already active")
        current = _forward_rollout_fence_status_unlocked(_load())
        if current["forward_fence_present"]:
            if not current["forward_fence_valid"]:
                raise RuntimeError(
                    "existing forward rollout fence is invalid: "
                    + str(current["forward_fence_error"])
                )
            return {"acquired": False, **current}
        data = _load()
        receipt = {
            "schema": _FORWARD_FENCE_SCHEMA,
            "created_at": _utc_now().isoformat(),
            "target": _FORWARD_FENCE_TARGET,
            "fence_id": str(uuid.uuid4()),
            "queue_sha256": _queue_snapshot_sha256(data),
        }
        payload = json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        _write_once(Path(_forward_fence_path()), payload)
        status = _forward_rollout_fence_status_unlocked(data)
        if not status["forward_fence_valid"]:
            raise RuntimeError(
                "forward rollout fence postcondition failed: "
                + str(status["forward_fence_error"])
            )
        return {"acquired": True, **status}


def release_forward_rollout_fence(fence_id: str) -> bool:
    """Release only the exact supervised fence receipt after success/abort."""
    expected_id = str(uuid.UUID(str(fence_id)))
    with _lockfile():
        status = _forward_rollout_fence_status_unlocked(_load())
        if not status["forward_fence_present"]:
            return False
        if not status["forward_fence_valid"]:
            raise RuntimeError(
                "cannot release invalid forward rollout fence: "
                + str(status["forward_fence_error"])
            )
        if status["forward_fence_id"] != expected_id:
            raise RuntimeError("forward rollout fence id mismatch")
        _unlink_durable(Path(_forward_fence_path()))
        return True


def enqueue(oids, *, source: str = "coordinator_panel") -> int:
    """Dopisz oid(y) do kolejki ze stemplem teraz (UTC). Zwraca liczbę dopisanych.
    Wołane przez panel (subprocess) po kliknięciu „Odśwież czas". Ponowny klik
    zastępuje tylko nieclaimowany request. Żywy claim pozostaje niezmiennym
    headem, a najnowszy klik jest jednym coalesced successorem."""
    oids = [str(o).strip() for o in (oids or []) if str(o).strip()]
    if not oids:
        return 0
    if source not in {"coordinator_panel", "coordinator_console"}:
        raise ValueError(f"unsupported coordinator receipt source: {source!r}")
    now = _utc_now().isoformat()
    with _lockfile():
        if os.path.exists(_forward_fence_path()):
            raise RuntimeError(
                "coordinator time queue fenced for forward authority rollout"
            )
        if os.path.exists(_rollback_fence_path()):
            raise RuntimeError(
                "coordinator time queue fenced for legacy code rollback"
            )
        policy_snapshot = _coordinator_policy_snapshot()
        data = _load()
        for o in oids:
            next_receipt = _new_receipt(
                o,
                requested_at=now,
                source=source,
                policy_snapshot=policy_snapshot,
            )
            current = data.get(o)
            if isinstance(current, dict) and current.get("claim") is not None:
                # Crash-window claim→outbox jest pierwszym trwałym journalem.
                # Nigdy nie nadpisuj go nową intencją. Wielokrotne re-clicki
                # koalescują wyłącznie oczekującego, jeszcze nieclaimowanego
                # successora — dokładnie jak dawniej odświeżały TTL requestu.
                # Zanim dołączymy nową generację, ten sam kanoniczny oracle co
                # recovery/ACK musi potwierdzić exact head i dotychczasowego
                # successora. Inaczej re-click zniszczyłby poison evidence.
                if _claimed_event(current, order_id=o) is None:
                    raise RuntimeError(
                        f"coordinator time queue poison claimed record for oid={o}"
                    )
                has_successor, successor = _successor_record(
                    current,
                    order_id=o,
                )
                if has_successor and successor is None:
                    raise RuntimeError(
                        f"coordinator time queue poison successor for oid={o}"
                    )
                preserved = _json_copy(current)
                preserved[SUCCESSOR_FIELD] = next_receipt
                data[o] = preserved
            elif isinstance(current, dict) and not _valid_unclaimed_receipt_shape(
                current, order_id=o
            ):
                raise RuntimeError(
                    f"coordinator time queue poison record for oid={o}"
                )
            else:
                data[o] = next_receipt
        _save(data)
    return len(oids)


def verify_pending_receipt(
    receipt: object,
    *,
    order_id: str,
    now: Optional[datetime] = None,
) -> bool:
    """Dowiedź, że receipt jest świeżym, niezużytym rekordem kanonicznej kolejki."""
    if not isinstance(receipt, Mapping):
        return False
    now = now or _utc_now()
    with _lockfile():
        current = _load().get(str(order_id))
        return bool(
            isinstance(current, dict)
            and dict(current) == dict(receipt)
            and current.get("claim") is None
            and SUCCESSOR_FIELD not in current
            and _valid_unclaimed_receipt_shape(current, order_id=str(order_id))
            and _fresh_receipt(current, order_id=str(order_id), now=now)
        )


def current_receipt(
    order_id: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """Zwróć świeży bieżący rekord (unclaimed lub claimed) jednego OID."""
    now = now or _utc_now()
    oid = str(order_id)
    with _lockfile():
        current = _load().get(oid)
        if isinstance(current, dict) and (
            _claimed_event(current, order_id=oid) is not None
            or (
                _valid_unclaimed_receipt_shape(current, order_id=oid)
                and _fresh_receipt(current, order_id=oid, now=now)
            )
        ):
            return _json_copy(current)
    return None


def upgrade_legacy_receipt(
    order_id: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """Atomowo podnieś świeży ``oid -> timestamp`` do nieautoryzującego v4.

    Historyczny rekord nie zawiera źródła ani request_id, więc nie wolno mu
    nadać authority czasówki. Osobne źródło kolejki jest akceptowane tylko
    przez neutralny legacy claim; policy owner committed czasu je odrzuca.
    """
    now = now or _utc_now()
    oid = str(order_id)
    with _lockfile():
        if os.path.exists(_forward_fence_path()):
            return None
        data = _load()
        current = data.get(oid)
        if isinstance(current, Mapping):
            if (
                _claimed_event(current, order_id=oid) is not None
                or (
                    _valid_unclaimed_receipt_shape(current, order_id=oid)
                    and _fresh_receipt(current, order_id=oid, now=now)
                )
            ):
                return _json_copy(current)
            return None
        try:
            requested = datetime.fromisoformat(str(current))
        except (TypeError, ValueError):
            return None
        if requested.tzinfo is None:
            requested = requested.replace(tzinfo=timezone.utc)
        if not (
            -_FUTURE_SKEW
            <= now - requested
            <= timedelta(minutes=DEFAULT_TTL_MIN)
        ):
            return None
        digest = hashlib.sha256(
            f"{oid}:{requested.isoformat()}".encode("utf-8")
        ).hexdigest()[:32]
        upgraded = {
            "schema": PRE_POLICY_RECEIPT_SCHEMA,
            "request_id": f"legacy-{digest}",
            "order_id": oid,
            "requested_at": requested.isoformat(),
            ELIGIBLE_AT_FIELD: requested.isoformat(),
            "source": "legacy_coordinator_queue",
            CONTINUATION_DEPTH_FIELD: 0,
        }
        data[oid] = upgraded
        _save(data)
        return _json_copy(upgraded)


def claim_receipt(
    receipt: object,
    *,
    order_id: str,
    event: Mapping[str, object],
    now: Optional[datetime] = None,
    continue_after_ack: bool = False,
) -> Optional[dict]:
    """Jednorazowo zwiąż klik z dokładnym eventem; retry tego samego jest OK."""
    if not isinstance(receipt, Mapping) or not isinstance(event, Mapping):
        return None
    now = now or _utc_now()
    oid = str(order_id)
    event_copy = json.loads(json.dumps(dict(event), ensure_ascii=False))
    event_sha = _event_sha256(event_copy)
    event_key = _claim_event_key(event_copy, order_id=oid)
    if not event_key:
        return None
    is_legacy_force = _legacy_claim_event_key(
        event_copy, order_id=oid
    ) is not None
    with _lockfile():
        if os.path.exists(_forward_fence_path()):
            return None
        if os.path.exists(_rollback_fence_path()):
            return None
        data = _load()
        current = data.get(oid)
        if not isinstance(current, dict):
            return None
        existing_claim = current.get("claim")
        if isinstance(existing_claim, dict):
            if (
                receipt_base(receipt) == receipt_base(current)
                and _claimed_event(current, order_id=oid) == event_copy
                and existing_claim.get("event_key") == event_key
                and existing_claim.get("event_sha256") == event_sha
            ):
                return _json_copy(current)
            return None
        if SUCCESSOR_FIELD in current:
            return None
        if not _valid_unclaimed_receipt_shape(current, order_id=oid):
            return None
        if not _fresh_receipt(current, order_id=oid, now=now):
            return None
        if dict(current) != dict(receipt):
            return None
        claimed = dict(current)
        claimed["claim"] = {
            "schema": CLAIM_SCHEMA,
            "event_key": event_key,
            "event_sha256": event_sha,
            "claimed_at": now.isoformat(),
            "event": event_copy,
        }
        current_base = receipt_base(current)
        if (
            continue_after_ack
            and is_legacy_force
            and current_base is not None
            and current_base[CONTINUATION_DEPTH_FIELD]
            < _MAX_LEGACY_CONTINUATION_DEPTH
        ):
            # Jeden response może zawierać dwa niezależne legacy pola czasu.
            # Po terminalnym exact evencie świeży successor wykonuje ponowny
            # diff i wiąże drugą zmianę osobnym claimem. Nie tworzymy batcha,
            # którego częściowy apply byłby nowym źródłem prawdy.
            claimed["claim"][_CLAIM_CONTINUATION_FIELD] = True
        if _claimed_event(claimed, order_id=oid) != event_copy:
            # Nie zapisuj poison-claimu. Dotyczy zwłaszcza częściowego
            # committed eventu z kluczem, ale bez receipt-bound proofu.
            return None
        data[oid] = claimed
        _save(data)
        return _json_copy(claimed)


def get_claimed_event(
    receipt: object,
    *,
    order_id: str,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """Zwróć exact event claimu tylko, gdy claim nadal żyje w kolejce."""
    if not isinstance(receipt, Mapping):
        return None
    now = now or _utc_now()
    oid = str(order_id)
    with _lockfile():
        current = _load().get(oid)
        if (
            not isinstance(current, dict)
            or not _same_head(current, receipt)
        ):
            return None
        return _claimed_event(current, order_id=oid)


def verify_claimed_event(
    event: Mapping[str, object],
    *,
    now: Optional[datetime] = None,
) -> bool:
    """State/transport: event jest dokładnie jednorazowym claimem żywej kolejki."""
    oid = str(event.get("order_id") or "") if isinstance(event, Mapping) else ""
    candidate = dict(event) if isinstance(event, Mapping) else {}
    durable_boolean_keys = _LEGACY_DURABLE_BOOLEAN_KEYS.get(
        str(candidate.get("event_type") or ""), frozenset()
    )
    if (
        oid
        and _claim_event_key(candidate, order_id=oid) is None
        and set(candidate)
        == _LEGACY_EVENT_SEMANTIC_KEYS
        | durable_boolean_keys
        | {"event_id"}
        and isinstance(candidate.get("event_id"), str)
        and bool(candidate.get("event_id"))
        and all(
            isinstance(candidate.get(key), bool)
            for key in durable_boolean_keys
        )
    ):
        # Durable bridge dodaje wyłącznie transportowy event_id i zamrożony
        # marker downstream. Claim nadal wiąże dokładną semantyczną kopertę;
        # żadnego innego top-level pola nie wolno znormalizować.
        semantic_candidate = {
            key: candidate.get(key)
            for key in _LEGACY_EVENT_SEMANTIC_KEYS
        }
        try:
            from dispatch_v2.committed_pickup_apply import (
                time_update_event_key,
            )

            expected_key = time_update_event_key(oid, semantic_candidate)
        except Exception:
            return False
        durable_event_id = str(candidate.get("event_id") or "")
        if not (
            durable_event_id == expected_key
            or durable_event_id.startswith(f"{expected_key}_v")
        ):
            return False
        candidate = semantic_candidate
    if (
        not oid
        or not isinstance(event, Mapping)
        or _claim_event_key(candidate, order_id=oid) is None
    ):
        return False
    now = now or _utc_now()
    with _lockfile():
        current = _load().get(oid)
        if not isinstance(current, dict):
            return False
        return _claimed_event(current, order_id=oid) == candidate


def pending_with_receipts(
    ttl_min: float = DEFAULT_TTL_MIN,
) -> dict[str, dict | None]:
    """Zwróć świeże ``oid -> receipt`` bez kasowania intencji.

    Legacy wpis ``oid -> timestamp`` nadal wymusza kosztowny fetch i zachowuje
    kompatybilność elastyka, ale zwraca ``None``. Nie może więc udawać dowodu
    jawnej zmiany committed czasu czasowki. Przeterminowane/uszkodzone wpisy sa
    usuwane; świeże znikaja wyłącznie przez jawny CAS ``ack_receipts``.
    """
    now = _utc_now()
    cutoff = now - timedelta(minutes=ttl_min)
    fresh: dict[str, dict | None] = {}
    with _lockfile():
        if os.path.exists(_forward_fence_path()):
            return fresh
        data = _load()
        if not data:
            return fresh
        retained = {}
        for oid, raw_receipt in data.items():
            receipt = raw_receipt if isinstance(raw_receipt, dict) else None
            if receipt is not None and receipt.get("claim") is not None:
                # Claim jest transakcją trwałą do exact ACK. Uszkodzony claim
                # także zostaje w kolejce (fail-closed + dowód operatorski), ale
                # nie trafia do worklisty i nie może zostać przypadkiem ACK-nięty.
                retained[str(oid)] = raw_receipt
                if _claimed_event(receipt, order_id=str(oid)) is not None:
                    fresh[str(oid)] = _json_copy(receipt)
                continue
            if receipt is not None and SUCCESSOR_FIELD in receipt:
                # Successor bez claimu jest niemożliwym stanem transakcji.
                # Nie jest work itemem, ale pozostaje trwałym poison-evidence
                # dla audit/rollback zamiast zniknąć jako rzekomo wygasły TTL.
                retained[str(oid)] = raw_receipt
                continue
            ts = (
                receipt.get("requested_at")
                if receipt is not None
                else raw_receipt
            )
            if receipt is not None:
                if not _valid_unclaimed_receipt_shape(
                    receipt, order_id=str(oid)
                ):
                    # Nieznana/partial koperta to dowód korupcji, nie wygasły
                    # request. Zachowaj ją dla operatora i rollback audit.
                    retained[str(oid)] = raw_receipt
                    continue
                if _fresh_receipt(
                    receipt,
                    order_id=str(oid),
                    now=now,
                    ttl_min=ttl_min,
                ) and SUCCESSOR_FIELD not in receipt:
                    fresh[str(oid)] = _json_copy(receipt)
                    retained[str(oid)] = raw_receipt
                continue
            try:
                t = datetime.fromisoformat(str(ts))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                if cutoff <= t <= now + _FUTURE_SKEW:
                    fresh[str(oid)] = None
                    retained[str(oid)] = raw_receipt
            except (ValueError, TypeError):
                continue
        if retained != data:
            _save(retained)
    return fresh


def ack_receipts(receipts: dict[str, dict | None]) -> int:
    """Terminalizuj dokładny head; nowszy klik promuj, nigdy go nie kasuj."""
    if not receipts:
        return 0
    removed = 0
    with _lockfile():
        if os.path.exists(_forward_fence_path()):
            return 0
        data = _load()
        for oid, claimed in receipts.items():
            oid = str(oid)
            if oid not in data:
                continue
            current = data.get(oid)
            if isinstance(claimed, dict):
                if not isinstance(current, dict):
                    continue
                if current.get("claim") is not None:
                    if (
                        not _same_head(current, claimed)
                        or str(current.get("order_id") or "") != oid
                    ):
                        continue
                elif (
                    dict(current) != dict(claimed)
                    or str(current.get("order_id") or "") != oid
                ):
                    continue
            elif isinstance(current, dict):
                # Legacy claim nie moze skasowac nowszego v4 receipt.
                continue
            if isinstance(current, dict) and current.get("claim") is not None:
                if _claimed_event(current, order_id=oid) is None:
                    continue
                has_successor, successor = _successor_record(
                    current,
                    order_id=oid,
                )
                if has_successor and successor is None:
                    # Corrupt successor nie może zniknąć pod ACK poprzednika.
                    continue
                if successor is not None:
                    successor_base = receipt_base(successor)
                    if successor_base is None:
                        continue
                    # Preserve the actual click time for audit, but start the
                    # execution TTL only when the non-expiring claimed head
                    # releases this successor.
                    promoted_schema = successor_base["schema"]
                    if promoted_schema == LEGACY_RECEIPT_SCHEMA:
                        # v4 cannot express a fresh eligibility epoch. Promote
                        # only its transport envelope to non-authorizing v5;
                        # the original click audit remains unchanged.
                        promoted_schema = PRE_POLICY_RECEIPT_SCHEMA
                    data[oid] = {
                        "schema": promoted_schema,
                        "request_id": successor_base["request_id"],
                        "order_id": successor_base["order_id"],
                        "requested_at": successor_base["requested_at"],
                        ELIGIBLE_AT_FIELD: _utc_now().isoformat(),
                        "source": successor_base["source"],
                        CONTINUATION_DEPTH_FIELD: successor_base[
                            CONTINUATION_DEPTH_FIELD
                        ],
                    }
                    if successor_base["schema"] == RECEIPT_SCHEMA:
                        data[oid][
                            COMMITTED_TIME_POLICY_SNAPSHOT_FIELD
                        ] = successor_base[
                            COMMITTED_TIME_POLICY_SNAPSHOT_FIELD
                        ]
                elif (
                    isinstance(current.get("claim"), Mapping)
                    and current["claim"].get(_CLAIM_CONTINUATION_FIELD) is True
                ):
                    base = receipt_base(current)
                    if base is None:
                        continue
                    continuation_args = {
                        "requested_at": _utc_now().isoformat(),
                        "source": str(base["source"]),
                        "continuation_depth": (
                            int(base[CONTINUATION_DEPTH_FIELD]) + 1
                        ),
                    }
                    if base["schema"] == RECEIPT_SCHEMA:
                        continuation = _new_receipt(
                            oid,
                            policy_snapshot=(
                                deserialize_coordinator_receipt_policy(base)
                            ),
                            **continuation_args,
                        )
                    else:
                        continuation = _new_pre_policy_receipt(
                            oid,
                            **continuation_args,
                        )
                    data[oid] = continuation
                else:
                    del data[oid]
            else:
                if isinstance(current, dict) and not _valid_unclaimed_receipt_shape(
                    current, order_id=oid
                ):
                    continue
                del data[oid]
            removed += 1
        if removed:
            _save(data)
    return removed


def drain_with_receipts(
    ttl_min: float = DEFAULT_TTL_MIN,
) -> dict[str, dict | None]:
    """Kompatybilny one-shot consumer tylko dla nieclaimowanych requestów.

    Claimed head niesie exact event, którego oid-only consumer nie zastosował.
    Pomijamy go, więc tylko produkcyjny claim-aware watcher może wykonać ACK.
    """
    fresh = pending_with_receipts(ttl_min=ttl_min)
    drainable = {
        oid: receipt
        for oid, receipt in fresh.items()
        if not (
            isinstance(receipt, dict)
            and receipt.get("claim") is not None
        )
    }
    ack_receipts(drainable)
    return drainable


def drain(ttl_min: float = DEFAULT_TTL_MIN) -> set:
    """Kompatybilny widok oid-only dla starych callerow i narzedzi."""
    return set(drain_with_receipts(ttl_min=ttl_min))


def _legacy_rollback_projection(data: dict) -> tuple[dict, dict]:
    """Pure v4/legacy queue audit and projection understood by pre-v4 code."""
    projection: dict[str, str] = {}
    blockers: list[str] = []
    counts = {
        "records": len(data),
        "legacy_records": 0,
        "pending_v4_records": 0,
        "claimed_records": 0,
        "successor_records": 0,
        "invalid_records": 0,
        "policy_bound_records": 0,
    }
    for oid_raw, raw in data.items():
        oid = str(oid_raw)
        if not isinstance(oid_raw, str) or not oid:
            counts["invalid_records"] += 1
            blockers.append(f"{oid}:invalid_order_id")
            continue
        if not isinstance(raw, Mapping):
            try:
                datetime.fromisoformat(str(raw))
            except (TypeError, ValueError):
                counts["invalid_records"] += 1
                blockers.append(f"{oid}:invalid_legacy_timestamp")
                continue
            counts["legacy_records"] += 1
            projection[oid] = str(raw)
            continue

        if raw.get("claim") is not None:
            counts["claimed_records"] += 1
            if SUCCESSOR_FIELD in raw:
                counts["successor_records"] += 1
            blockers.append(f"{oid}:claimed_transaction")
            continue
        if SUCCESSOR_FIELD in raw:
            counts["successor_records"] += 1
            counts["invalid_records"] += 1
            blockers.append(f"{oid}:orphan_successor")
            continue
        base = receipt_base(raw)
        if not _valid_unclaimed_receipt_shape(raw, order_id=oid):
            counts["invalid_records"] += 1
            blockers.append(f"{oid}:invalid_v4_receipt")
            continue
        if raw.get("schema") == RECEIPT_SCHEMA:
            counts["policy_bound_records"] += 1
            blockers.append(f"{oid}:policy_bound_receipt")
            continue
        clocks = _receipt_clock_pair(raw)
        if clocks is None:
            counts["invalid_records"] += 1
            blockers.append(f"{oid}:invalid_v4_timestamp")
            continue
        counts["pending_v4_records"] += 1
        # Pre-v4 ma jeden zegar TTL. Dla v5 musi on zachować moment, od
        # którego rekord faktycznie stał się wykonywalny. ``requested_at``
        # pozostaje w exact backupie jako audit kliknięcia, ale projected
        # scalar bierze ``eligible_at``; inaczej successor czekający za
        # nieprzeterminowanym claimem znika natychmiast po code rollbacku.
        projection[oid] = str(base[ELIGIBLE_AT_FIELD])

    status = {
        **counts,
        "safe_queue_projection": not blockers,
        "blockers": blockers,
    }
    return projection, status


def _persistent_rollback_backup_path(value: object) -> Optional[Path]:
    """Normalize one narrow, persistent backup file target."""
    try:
        backup = Path(str(value))
    except (TypeError, ValueError):
        return None
    if not backup.is_absolute():
        return None
    resolved = backup.resolve(strict=False)
    if resolved == Path("/tmp") or resolved == Path("/run"):
        return None
    if str(resolved).startswith(("/tmp/", "/run/")):
        return None
    if resolved in {
        Path(QUEUE_PATH).resolve(strict=False),
        Path(_rollback_fence_path()).resolve(strict=False),
        Path(_forward_fence_path()).resolve(strict=False),
    }:
        return None
    return resolved


def _rollback_fence_status_unlocked() -> dict:
    """Validate the fence as a receipt, not as a mere pathname marker."""
    fence = Path(_rollback_fence_path())
    result = {
        "rollback_fence_present": fence.exists(),
        "rollback_prepared": False,
        "rollback_fence_error": None,
        "rollback_backup_path": None,
        "rollback_backup_sha256": None,
        "rollback_projected_queue_sha256": None,
    }
    if not result["rollback_fence_present"]:
        return result
    try:
        raw_fence = fence.read_text(encoding="utf-8")
    except OSError:
        result["rollback_fence_error"] = "fence_unreadable"
        return result
    try:
        receipt = json.loads(raw_fence)
    except (TypeError, ValueError, json.JSONDecodeError):
        result["rollback_fence_error"] = "fence_invalid_json"
        return result
    if not isinstance(receipt, Mapping):
        result["rollback_fence_error"] = "fence_not_object"
        return result
    if receipt.get("schema") != _ROLLBACK_FENCE_SCHEMA:
        result["rollback_fence_error"] = "fence_schema_mismatch"
        return result
    if receipt.get("target") != _ROLLBACK_TARGET:
        result["rollback_fence_error"] = "fence_target_mismatch"
        return result

    backup = _persistent_rollback_backup_path(receipt.get("backup_path"))
    if backup is None or str(backup) != str(receipt.get("backup_path") or ""):
        result["rollback_fence_error"] = "backup_path_invalid"
        return result
    expected_backup_sha = str(receipt.get("backup_sha256") or "")
    expected_queue_sha = str(receipt.get("projected_queue_sha256") or "")
    result["rollback_backup_path"] = str(backup)
    result["rollback_backup_sha256"] = expected_backup_sha or None
    result["rollback_projected_queue_sha256"] = expected_queue_sha or None
    if len(expected_backup_sha) != 64:
        result["rollback_fence_error"] = "backup_sha256_invalid"
        return result
    if len(expected_queue_sha) != 64:
        result["rollback_fence_error"] = "projected_queue_sha256_invalid"
        return result
    try:
        backup_bytes = backup.read_bytes()
    except FileNotFoundError:
        result["rollback_fence_error"] = "backup_missing"
        return result
    except OSError:
        result["rollback_fence_error"] = "backup_unreadable"
        return result
    if hashlib.sha256(backup_bytes).hexdigest() != expected_backup_sha:
        result["rollback_fence_error"] = "backup_sha256_mismatch"
        return result
    try:
        projected_bytes = Path(QUEUE_PATH).read_bytes()
    except FileNotFoundError:
        result["rollback_fence_error"] = "projected_queue_missing"
        return result
    except OSError:
        result["rollback_fence_error"] = "projected_queue_unreadable"
        return result
    if hashlib.sha256(projected_bytes).hexdigest() != expected_queue_sha:
        result["rollback_fence_error"] = "projected_queue_sha256_mismatch"
        return result
    result["rollback_prepared"] = True
    return result


def legacy_rollback_status() -> dict:
    """Read-only proof whether queue data can be read by the pre-v4 code."""
    with _lockfile():
        _projection, status = _legacy_rollback_projection(_load())
        status.update(_rollback_fence_status_unlocked())
        return status


def prepare_legacy_rollback(backup_path: str) -> dict:
    """Fence v4 writers, backup exact bytes and downgrade only safe requests.

    Claimed/corrupt records are never coerced. The caller must separately prove
    that no unfinished authority outbox exists and that the decision flag is
    OFF; the operator CLI owns that cross-store preflight.
    """
    backup = _persistent_rollback_backup_path(backup_path)
    if backup is None:
        raise ValueError(
            "rollback queue backup must be an absolute persistent file "
            "outside queue/fence paths"
        )
    if backup.exists():
        raise FileExistsError(str(backup))

    with _lockfile():
        if os.path.exists(_forward_fence_path()):
            raise RuntimeError("forward rollout fence is already active")
        data = _load()
        projection, status = _legacy_rollback_projection(data)
        if not status["safe_queue_projection"]:
            raise RuntimeError(
                "legacy rollback blocked by queue: "
                + ",".join(status["blockers"])
            )

        fence = Path(_rollback_fence_path())
        if fence.exists():
            raise RuntimeError("legacy rollback fence already exists")

        queue_path = Path(QUEUE_PATH)
        queue_existed = queue_path.exists()
        try:
            original = queue_path.read_bytes()
        except FileNotFoundError:
            original = b"{}\n"
        backup_sha256 = hashlib.sha256(original).hexdigest()
        # Backup powstaje jako pierwszy. Sama jego porażka nie może zostawić
        # markera sugerującego gotowość do code revert ani zmienić kolejki.
        _write_once(backup, original)
        projection_started = False
        fence_created = False
        try:
            # Projection and the final fence are one supervised transaction.
            # Any failure after the first mutation restores the exact original
            # queue bytes before the exception leaves this lock.
            projection_started = True
            _save(projection)
            _projection, after = _legacy_rollback_projection(_load())
            if not after["safe_queue_projection"] or after["pending_v4_records"]:
                raise RuntimeError("legacy queue projection postcondition failed")
            projected_bytes = queue_path.read_bytes()
            projected_sha256 = hashlib.sha256(projected_bytes).hexdigest()
            # Fence jest ostatnim commitem transakcji operatorskiej i zawiera
            # weryfikowalny receipt obu wcześniejszych artefaktów. Do tego momentu
            # status nigdy nie może autoryzować powrotu do kodu pre-v4.
            fence_payload = json.dumps(
                {
                    "schema": _ROLLBACK_FENCE_SCHEMA,
                    "created_at": _utc_now().isoformat(),
                    "target": _ROLLBACK_TARGET,
                    "backup_path": str(backup),
                    "backup_sha256": backup_sha256,
                    "projected_queue_sha256": projected_sha256,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
            _write_once(fence, fence_payload)
            fence_created = True
            prepared = _rollback_fence_status_unlocked()
            if not prepared["rollback_prepared"]:
                raise RuntimeError(
                    "legacy rollback receipt postcondition failed: "
                    + str(prepared.get("rollback_fence_error") or "unknown")
                )
        except Exception as original_error:
            restore_errors = []
            if fence_created:
                try:
                    _unlink_durable(fence)
                except Exception as exc:
                    restore_errors.append(
                        f"fence_cleanup:{type(exc).__name__}:{exc}"
                    )
            if projection_started:
                try:
                    if queue_existed:
                        _atomic_replace_bytes(queue_path, original)
                    else:
                        _unlink_durable(queue_path)
                except Exception as exc:
                    restore_errors.append(
                        f"queue_restore:{type(exc).__name__}:{exc}"
                    )
            if restore_errors:
                raise RuntimeError(
                    "legacy rollback failed and exact restore failed: "
                    + ";".join(restore_errors)
                ) from original_error
            raise
        return {
            "backup_path": str(backup),
            "backup_sha256": backup_sha256,
            "projected_queue_sha256": projected_sha256,
            "converted_v4_records": status["pending_v4_records"],
            "legacy_records": after["legacy_records"],
            "rollback_fence_path": str(fence),
        }


def release_legacy_rollback_fence() -> bool:
    """Re-open v4 writers after a verified roll-forward to this code."""
    with _lockfile():
        _projection, status = _legacy_rollback_projection(_load())
        if not status["safe_queue_projection"]:
            raise RuntimeError(
                "rollback fence release blocked by queue: "
                + ",".join(status["blockers"])
            )
        fence = Path(_rollback_fence_path())
        if not fence.exists():
            return False
        os.unlink(fence)
        dir_fd = os.open(str(fence.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return True
