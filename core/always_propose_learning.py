"""Trwały kontrakt learning-loop A-3: decyzja silnika -> wybór człowieka.

Pełna decyzja ma jednego właściciela w ``shadow_decisions.jsonl``, a finalna
para ucząca ma jednego właściciela w istniejącym ``learning_log.jsonl``.
Ten moduł utrzymuje jedynie kompaktowy, atomowy indeks przyczynowy pomiędzy
tymi zdarzeniami oraz buduje PII-free rekord końcowy.  Indeks nie jest logiem
i nie kopiuje tras, adresów, nazw ani pełnej puli.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Optional

from dispatch_v2 import common as C
from dispatch_v2.core.learning_actor import actor_status, effective_live_assign
from dispatch_v2.core import proposal_output
from dispatch_v2.identity.schema import canon_cid

INDEX_SCHEMA = "always_propose.decision_context_index.v1"
CONTEXT_SCHEMA = "always_propose.decision_context.v1"
LEARNING_SCHEMA = "always_propose.learning_pair.v1"
EXPLANATION_SCHEMA = "always_propose.owner_exception_explanation.v1"
CONTEXT_TTL_HOURS = 48
AUDIT_LOOKBACK_SECONDS = 10 * 60
AUDIT_TAIL_BYTES = 512 * 1024

CONTEXT_INDEX_PATH = str(C.STATE_DIR / "always_propose_decision_context.json")
COORDINATOR_AUDIT_PATH = str(C.STATE_DIR / "coordinator_assign_audit.jsonl")

_BEST_OF_WORST_FIELDS = (
    "schema",
    "selection_rule",
    "fleet_count",
    "observed_candidate_count",
    "hard_safe_count",
    "soft_evidence_complete",
    "proposal_output_type",
    "recommend_only",
)
_CANDIDATE_FIELDS = (
    "courier_id",
    "hard_safe",
    "feasibility_verdict",
    "feasibility_reason",
    "pickup_dist_km",
    "score",
    "has_plan",
)
_CONTEXT_OPTIONAL_FIELDS = ("stored_at", "expires_at")
_ACTOR_FIELDS = (
    "status",
    "actor_id",
    "provenance",
    "reason",
    "audit_schema",
    "audit_at",
    "audit_ref",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _path(path: Optional[str]) -> str:
    if path:
        return str(path)
    # Root hermetic harness ustawia DISPATCH_STATE_DIR przed testami. Resolve w
    # call-time (jak state_machine), aby import modułu nigdy nie przypiął testu
    # do żywego dispatch_state.
    state_override = os.environ.get("DISPATCH_STATE_DIR")
    if state_override:
        return str(Path(state_override) / "always_propose_decision_context.json")
    return CONTEXT_INDEX_PATH


def _audit_path(path: Optional[str]) -> str:
    if path:
        return str(path)
    state_override = os.environ.get("DISPATCH_STATE_DIR")
    if state_override:
        return str(Path(state_override) / "coordinator_assign_audit.jsonl")
    return COORDINATOR_AUDIT_PATH


@contextmanager
def _locked(path: str, *, exclusive: bool):
    lock_path = f"{path}.lock"
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _empty_index() -> dict:
    return {"schema": INDEX_SCHEMA, "entries": {}}


def _load_index(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return _empty_index()
    if not isinstance(value, dict) or value.get("schema") != INDEX_SCHEMA:
        raise ValueError("invalid always-propose context index schema")
    if not isinstance(value.get("entries"), dict):
        raise ValueError("invalid always-propose context index entries")
    return value


def _save_index(path: str, value: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                value,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        directory_fd = os.open(str(target.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _sanitize_best_of_worst(value: Any) -> Optional[dict]:
    if not isinstance(value, Mapping):
        return None
    result = {field: value.get(field) for field in _BEST_OF_WORST_FIELDS}
    raw_candidate = value.get("candidate")
    if isinstance(raw_candidate, Mapping):
        result["candidate"] = {
            field: raw_candidate.get(field) for field in _CANDIDATE_FIELDS
        }
        result["candidate"]["courier_id"] = canon_cid(
            raw_candidate.get("courier_id")
        ) or None
    else:
        result["candidate"] = None
    return result


def decision_context_from_record(record: Mapping[str, Any]) -> Optional[dict]:
    """Zredukuj shadow record do stabilnego causal pointera bez PII."""
    oid = str(record.get("order_id") or "").strip()
    event_id = str(record.get("event_id") or "").strip()
    if not oid or not event_id:
        return None

    output_type = record.get("proposal_output_type")
    if output_type not in proposal_output.ALL_LABELS:
        output_type = proposal_output.output_label(SimpleNamespace(
            verdict=record.get("verdict"),
            reason=record.get("reason"),
            best=record.get("best"),
            owner_exception=bool(record.get("owner_exception", False)),
        ))
    if output_type not in proposal_output.ALL_LABELS:
        return None

    best = record.get("best")
    best_cid = (
        canon_cid(best.get("courier_id"))
        if isinstance(best, Mapping) else ""
    )
    best_score = best.get("score") if isinstance(best, Mapping) else None
    return {
        "schema": CONTEXT_SCHEMA,
        "order_id": oid,
        "decision_event_id": event_id,
        "decision_at": record.get("ts"),
        "proposal_output_type": output_type,
        "verdict": str(record.get("verdict") or ""),
        "reason": str(record.get("reason") or ""),
        "best_courier_id": best_cid or None,
        "best_score": best_score,
        "proposal_best_of_worst": _sanitize_best_of_worst(
            record.get("proposal_best_of_worst")
        ),
        "source": "shadow_decisions",
    }


def _sanitize_context(value: Mapping[str, Any]) -> Optional[dict]:
    if value.get("schema") != CONTEXT_SCHEMA:
        return None
    oid = str(value.get("order_id") or "").strip()
    event_id = str(value.get("decision_event_id") or "").strip()
    output_type = value.get("proposal_output_type")
    if not oid or not event_id or output_type not in proposal_output.ALL_LABELS:
        return None
    result = {
        "schema": CONTEXT_SCHEMA,
        "order_id": oid,
        "decision_event_id": event_id,
        "decision_at": value.get("decision_at"),
        "proposal_output_type": output_type,
        "verdict": str(value.get("verdict") or ""),
        "reason": str(value.get("reason") or ""),
        "best_courier_id": canon_cid(value.get("best_courier_id")) or None,
        "best_score": value.get("best_score"),
        "proposal_best_of_worst": _sanitize_best_of_worst(
            value.get("proposal_best_of_worst")
        ),
        "source": "shadow_decisions",
    }
    for field in _CONTEXT_OPTIONAL_FIELDS:
        if field in value:
            result[field] = value.get(field)
    return result


def _sweep_expired(entries: dict, now: datetime) -> bool:
    removed = False
    for oid, entry in list(entries.items()):
        expires = _parse_timestamp(
            entry.get("expires_at") if isinstance(entry, Mapping) else None
        )
        if expires is not None and expires <= now:
            entries.pop(oid, None)
            removed = True
    return removed


def remember_decision(
    record: Mapping[str, Any],
    *,
    path: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """Idempotentnie zapamiętaj pierwszy kontekst exact ``event_id``."""
    context = decision_context_from_record(record)
    if context is None:
        return None
    observed_at = (now or _utc_now()).astimezone(timezone.utc)
    target = _path(path)
    oid = context["order_id"]
    event_id = context["decision_event_id"]
    with _locked(target, exclusive=True):
        index = _load_index(target)
        entries = index["entries"]
        changed = _sweep_expired(entries, observed_at)
        existing = entries.get(oid)
        if (
            isinstance(existing, Mapping)
            and str(existing.get("decision_event_id") or "") == event_id
        ):
            if changed:
                _save_index(target, index)
            return dict(existing)
        stored = {
            **context,
            "stored_at": _iso(observed_at),
            "expires_at": _iso(
                observed_at + timedelta(hours=CONTEXT_TTL_HOURS)
            ),
        }
        entries[oid] = stored
        _save_index(target, index)
        return dict(stored)


def peek_decision(order_id: Any, *, path: Optional[str] = None) -> Optional[dict]:
    """Odczytaj ostatni kontekst zamówienia; atomic replace daje spójny obraz."""
    target = _path(path)
    with _locked(target, exclusive=False):
        entry = _load_index(target)["entries"].get(str(order_id))
    if not isinstance(entry, Mapping):
        return None
    expires = _parse_timestamp(entry.get("expires_at"))
    if expires is not None and expires <= _utc_now():
        return None
    return dict(entry)


def acknowledge_decision(
    order_id: Any,
    decision_event_id: Any,
    *,
    path: Optional[str] = None,
) -> bool:
    """CAS-pop: usuń indeks tylko jeśli nadal wskazuje skonsumowaną decyzję."""
    target = _path(path)
    if not os.path.exists(target):
        return False
    oid = str(order_id)
    event_id = str(decision_event_id or "")
    if not oid or not event_id:
        return False
    with _locked(target, exclusive=True):
        index = _load_index(target)
        current = index["entries"].get(oid)
        if (
            not isinstance(current, Mapping)
            or str(current.get("decision_event_id") or "") != event_id
        ):
            return False
        index["entries"].pop(oid, None)
        _save_index(target, index)
    return True


def acknowledge_from_learning(record: Mapping[str, Any]) -> bool:
    engine = record.get("engine_context")
    engine_event_id = record.get("engine_decision_event_id")
    if not engine_event_id and isinstance(engine, Mapping):
        engine_event_id = engine.get("decision_event_id")
    return acknowledge_decision(record.get("order_id"), engine_event_id)


def _unknown_actor(reason: str) -> dict:
    return {
        "status": "UNKNOWN",
        "actor_id": None,
        "provenance": "coordinator_assign_audit",
        "reason": reason,
    }


def _sanitize_assigned_by(value: Optional[Mapping[str, Any]]) -> dict:
    if not isinstance(value, Mapping):
        return _unknown_actor("actor_snapshot_absent")
    status = str(value.get("status") or "UNKNOWN")
    actor_id = str(value.get("actor_id") or "") or None
    if status == "ATTESTED" and not (
        actor_id and actor_id.startswith("actor_sha256:")
    ):
        return _unknown_actor("invalid_actor_pseudonym")
    sanitized = {
        "status": "ATTESTED" if status == "ATTESTED" else "UNKNOWN",
        "actor_id": actor_id if status == "ATTESTED" else None,
    }
    for field in _ACTOR_FIELDS:
        if field in {"status", "actor_id"}:
            continue
        if value.get(field) is not None:
            sanitized[field] = value.get(field)
    return sanitized


def capture_assigned_by(
    order_id: Any,
    observed_at: Any,
    *,
    audit_path: Optional[str] = None,
) -> dict:
    """Atestuj najnowszy skuteczny console assign; nigdy nie emituj e-maila."""
    anchor = _parse_timestamp(observed_at)
    if anchor is None:
        return _unknown_actor("invalid_assignment_timestamp")
    target = _audit_path(audit_path)
    try:
        with open(target, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - AUDIT_TAIL_BYTES))
            raw_tail = handle.read().decode("utf-8", errors="ignore")
    except FileNotFoundError:
        return _unknown_actor("audit_absent")
    except OSError:
        return _unknown_actor("audit_unavailable")

    matches: list[tuple[datetime, str, str]] = []
    filtered_actor_seen = False
    oid = str(order_id)
    for line in raw_tail.splitlines():
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        if str(row.get("order_id") or "") != oid:
            continue
        audit_schema = effective_live_assign(row)
        if audit_schema is None:
            continue
        at = _parse_timestamp(row.get("ts"))
        state, actor_id = actor_status(row.get("actor"))
        if at is None:
            continue
        age_s = (anchor - at).total_seconds()
        if age_s < 0 or age_s > AUDIT_LOOKBACK_SECONDS:
            continue
        if state != "attested" or actor_id is None:
            filtered_actor_seen = True
            continue
        matches.append((at, actor_id, audit_schema))
    if not matches:
        return _unknown_actor(
            "actor_filtered" if filtered_actor_seen
            else "no_unique_recent_live_assign"
        )

    latest_at = max(item[0] for item in matches)
    latest = [item for item in matches if item[0] == latest_at]
    actor_ids = {item[1] for item in latest}
    if len(actor_ids) != 1:
        return _unknown_actor("ambiguous_recent_live_assign")
    actor_id = next(iter(actor_ids))
    audit_ref = "audit_sha256:" + hashlib.sha256(
        f"{oid}|{_iso(latest_at)}|{actor_id}".encode("utf-8")
    ).hexdigest()[:20]
    return {
        "status": "ATTESTED",
        "actor_id": actor_id,
        "provenance": "coordinator_assign_audit",
        "audit_schema": latest[0][2],
        "audit_at": _iso(latest_at),
        "audit_ref": audit_ref,
    }


def _proposed_cid(context: Mapping[str, Any]) -> Optional[str]:
    best = canon_cid(context.get("best_courier_id"))
    if best:
        return best
    best_of_worst = context.get("proposal_best_of_worst")
    candidate = (
        best_of_worst.get("candidate")
        if isinstance(best_of_worst, Mapping) else None
    )
    if isinstance(candidate, Mapping):
        return canon_cid(candidate.get("courier_id")) or None
    return None


def resolution_kind(
    context: Mapping[str, Any], actual_courier_id: Any
) -> Optional[str]:
    """Jedyna klasyfikacja D1/D3; panel jest tylko konsumentem."""
    actual = canon_cid(actual_courier_id)
    if not actual:
        return None
    output_type = context.get("proposal_output_type")
    if output_type == proposal_output.COORDINATOR_ESCALATION:
        return proposal_output.COORDINATOR_ESCALATION
    proposed = _proposed_cid(context)
    if (
        proposed
        and actual != proposed
        and output_type in {
            proposal_output.EXECUTABLE_PROPOSAL,
            proposal_output.LEAST_DAMAGE_ALERT,
        }
    ):
        return proposal_output.OWNER_EXCEPTION
    return None


def build_learning_record(
    *,
    context: Mapping[str, Any],
    actual_courier_id: Any,
    assigned_at: Any,
    assigned_by: Optional[Mapping[str, Any]],
    panel_source: str,
    assignment_lifecycle_event_id: Optional[str],
) -> Optional[dict]:
    """Zbuduj końcową parę uczącą D1 albo D3 dla istniejącego JSONL."""
    safe_context = _sanitize_context(context)
    if safe_context is None:
        raise ValueError("invalid always-propose decision context")
    kind = resolution_kind(safe_context, actual_courier_id)
    if kind is None:
        return None
    oid = safe_context["order_id"]
    engine_event_id = safe_context["decision_event_id"]
    actual = canon_cid(actual_courier_id)
    proposed = _proposed_cid(safe_context)
    assignment_id = (
        str(assignment_lifecycle_event_id)
        if assignment_lifecycle_event_id else None
    )
    parsed_assigned_at = _parse_timestamp(assigned_at)
    if parsed_assigned_at is None:
        raise ValueError("invalid assignment timestamp")
    observed_at = _iso(parsed_assigned_at)
    actor = _sanitize_assigned_by(assigned_by)
    identity_payload = {
        "order_id": oid,
        "engine_decision_event_id": engine_event_id,
        "assignment_lifecycle_event_id": assignment_id,
        "actual_courier_id": actual,
        "kind": kind,
    }
    learning_event_id = "a3_learning_sha256:" + hashlib.sha256(
        json.dumps(
            identity_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:24]
    action = (
        "COORDINATOR_ESCALATION_RESOLVED"
        if kind == proposal_output.COORDINATOR_ESCALATION
        else "PANEL_OVERRIDE"
    )
    reason = (
        str(safe_context.get("reason") or "")
        if kind == proposal_output.COORDINATOR_ESCALATION
        else "nieokreślony"
    )
    record = {
        "schema": LEARNING_SCHEMA,
        "learning_event_id": learning_event_id,
        "ts": observed_at,
        "order_id": oid,
        "action": action,
        "learning_event_type": kind,
        "proposal_output_type": kind,
        "reason": reason,
        "proposed_courier_id": proposed,
        "proposed_score": safe_context.get("best_score"),
        "actual_courier_id": actual,
        "panel_source": panel_source,
        "engine_decision_event_id": engine_event_id,
        # Backward-compatible exact shadow join dla konsumentów E1.
        "lifecycle_event_id": engine_event_id,
        "assigned_by": actor,
        "assignment": {
            "courier_id": actual,
            "assigned_at": observed_at,
            "source": panel_source,
            "assigned_by": actor,
            "lifecycle_event_id": assignment_id,
        },
        "engine_context": safe_context,
    }
    if assignment_id:
        record["assignment_lifecycle_event_id"] = assignment_id
    if kind == proposal_output.OWNER_EXCEPTION:
        record["explanation"] = {
            "schema": EXPLANATION_SCHEMA,
            "status": "PENDING",
            "reason": "nieokreślony",
            "explains_learning_event_id": learning_event_id,
            "explanation_event_id": None,
        }
    return record
