"""Kontrfaktyczny certyfikat Alarmu i wspólna polityka best-effort HARD35.

Certyfikat nie jest wskaźnikiem obciążenia. Powstaje wyłącznie z pełnej puli
kandydatów po pozostałych HARD-ach: NORMAL, gdy istnieje opcja <=35; Alarm,
gdy nie ma żadnej <=35, ale istnieje co najmniej jedna w (35, 40]; twardy brak,
gdy nie istnieje żadna opcja <=40.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional

from dispatch_v2 import common as C

SCHEMA = "alarm_certificate.v1"
SNAPSHOT_PATH = str(C.STATE_DIR / "alarm_certificate.json")
TTL_SECONDS = 120.0
_THERMAL_REASONS = (
    "R6_per_order_",
    "R6_picked_up_delta_",
    "sla_violation",
)


def _utc(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        out = value
    elif isinstance(value, str):
        try:
            out = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=timezone.utc)
    return out.astimezone(timezone.utc)


def _candidate_carry(candidate: Any) -> Optional[float]:
    carry = (getattr(candidate, "metrics", None) or {}).get("carry_eval")
    if not isinstance(carry, dict):
        return None
    if carry.get("schema") != "carry_eval.v1" or carry.get("status") != "EVALUATED":
        return None
    value = carry.get("max_carry_min")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return float(value)


def _passes_other_hards(candidate: Any) -> bool:
    verdict = str(getattr(candidate, "feasibility_verdict", "") or "")
    if verdict == "MAYBE":
        return True
    reason = str(getattr(candidate, "feasibility_reason", "") or "")
    return any(reason.startswith(prefix) for prefix in _THERMAL_REASONS)


def build(
    candidates: Iterable[Any],
    *,
    decision_order_id: str,
    now: datetime,
) -> Dict[str, Any]:
    now = _utc(now) or datetime.now(timezone.utc)
    normal = []
    alarm = []
    over_40 = []
    unknown = []
    excluded_other_hard = []
    fingerprint_rows = []
    for candidate in candidates:
        cid = str(getattr(candidate, "courier_id", "") or "")
        value = _candidate_carry(candidate)
        fingerprint_rows.append((cid, value, getattr(
            candidate, "feasibility_verdict", None)))
        if not _passes_other_hards(candidate):
            excluded_other_hard.append(cid)
            continue
        if value is None:
            unknown.append(cid)
        elif value <= 35.0:
            normal.append(cid)
        elif value <= 40.0:
            alarm.append(cid)
        else:
            over_40.append(cid)

    if normal:
        classification = "NORMAL"
        alarm_on = False
    elif unknown:
        # Brak pomiaru choć jednego kandydata po pozostałych HARD-ach oznacza,
        # że nie umiemy udowodnić kontrfaktu „zero <=35”. Nigdy nie awansuj
        # niewiedzy do Alarmu ani do twardego braku.
        classification = "UNEVALUABLE"
        alarm_on = False
    elif alarm:
        classification = "ALARM_CANDIDATE"
        alarm_on = True
    else:
        classification = "HARD_NO_CANDIDATE"
        alarm_on = False

    counterfactual = {
        "le_35_count": len(normal),
        "between_35_40_count": len(alarm),
        "over_40_count": len(over_40),
        "unknown_count": len(unknown),
        "excluded_other_hard_count": len(excluded_other_hard),
        "le_35_cids": normal,
        "between_35_40_cids": alarm,
    }
    fp = hashlib.sha256(json.dumps(
        fingerprint_rows, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:20]
    return {
        "schema": SCHEMA,
        "decision_order_id": str(decision_order_id),
        "observed_at": now.isoformat(),
        "valid_until": (now + timedelta(seconds=TTL_SECONDS)).isoformat(),
        "classification": classification,
        "alarm": alarm_on,
        "counterfactual": counterfactual,
        "pool_fingerprint": fp,
        "scope_order_ids": [],
    }


def bind_scope(certificate: Dict[str, Any],
               order_ids: Iterable[str]) -> Dict[str, Any]:
    out = dict(certificate)
    out["scope_order_ids"] = sorted({
        str(oid) for oid in order_ids if oid not in (None, "")
    })
    return out


def validate(
    certificate: Any,
    now: datetime,
    *,
    decision_order_id: Optional[str] = None,
    scope_order_ids: Optional[Iterable[str]] = None,
) -> bool:
    if not isinstance(certificate, dict) or certificate.get("schema") != SCHEMA:
        return False
    cf = certificate.get("counterfactual")
    if not isinstance(cf, dict):
        return False
    required = (
        "le_35_count", "between_35_40_count", "over_40_count",
        "unknown_count", "excluded_other_hard_count",
    )
    if any(
        not isinstance(cf.get(key), int)
        or isinstance(cf.get(key), bool)
        or cf.get(key) < 0
        for key in required
    ):
        return False
    if (
        not isinstance(cf.get("le_35_cids"), list)
        or not isinstance(cf.get("between_35_40_cids"), list)
        or len(cf["le_35_cids"]) != cf["le_35_count"]
        or len(cf["between_35_40_cids"]) != cf["between_35_40_count"]
    ):
        return False
    fingerprint = certificate.get("pool_fingerprint")
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 20
        or any(ch not in "0123456789abcdef" for ch in fingerprint)
    ):
        return False
    if not isinstance(certificate.get("scope_order_ids"), list):
        return False
    observed = _utc(certificate.get("observed_at"))
    valid_until = _utc(certificate.get("valid_until"))
    now_utc = _utc(now)
    if observed is None or valid_until is None or now_utc is None:
        return False
    if now_utc < observed or now_utc > valid_until:
        return False
    if decision_order_id is not None and str(
            certificate.get("decision_order_id")
    ) != str(decision_order_id):
        return False
    if scope_order_ids is not None:
        expected = sorted({
            str(oid) for oid in scope_order_ids if oid not in (None, "")
        })
        if certificate.get("scope_order_ids") != expected:
            return False

    alarm = certificate.get("alarm") is True
    classification = certificate.get("classification")
    if alarm:
        return (
            classification == "ALARM_CANDIDATE"
            and cf["le_35_count"] == 0
            and cf["between_35_40_count"] >= 1
            and cf["unknown_count"] == 0
        )
    if classification == "NORMAL":
        return cf["le_35_count"] >= 1
    if classification == "UNEVALUABLE":
        return (
            cf["le_35_count"] == 0
            and cf["unknown_count"] >= 1
        )
    if classification == "HARD_NO_CANDIDATE":
        return (
            cf["le_35_count"] == 0
            and cf["between_35_40_count"] == 0
            and cf["unknown_count"] == 0
        )
    return False


def is_alarm(certificate: Any, now: Optional[datetime] = None) -> bool:
    check_now = now or datetime.now(timezone.utc)
    return validate(certificate, check_now) and certificate.get("alarm") is True


def publish(certificate: Dict[str, Any], *, path: Optional[str] = None) -> str:
    if not C.decision_flag("ENABLE_ALARM_CERTIFICATE_SHADOW"):
        return "flag_off"
    now = _utc(certificate.get("observed_at"))
    if now is None or not validate(certificate, now):
        return "invalid"
    target = path or SNAPSHOT_PATH
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=directory, prefix=".alarm_certificate_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(certificate, fh, ensure_ascii=False, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return "published"


def read(
    now: datetime,
    *,
    path: Optional[str] = None,
    scope_order_ids: Optional[Iterable[str]] = None,
) -> Optional[Dict[str, Any]]:
    if not C.decision_flag("ENABLE_ALARM_CERTIFICATE_SHADOW"):
        return None
    try:
        with open(path or SNAPSHOT_PATH, "r", encoding="utf-8") as fh:
            certificate = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None
    if not validate(
        certificate, now, scope_order_ids=scope_order_ids
    ):
        return None
    return certificate


def hard35_best_effort_choice(
    candidates: Iterable[Any],
    *,
    alarm_certificate: Optional[Dict[str, Any]],
) -> tuple[list, Optional[Any], Dict[str, Any]]:
    """Filtr HARD35 dla best-effort; nigdy nie ukrywa least-damage.

    Zwraca kandydatów w capie. Gdy pusty, zwraca jawnego najmniej szkodliwego
    kandydata jako `alert`, ale nie jako feasible/proposal.
    """
    pool = list(candidates)
    alarm_on = is_alarm(alarm_certificate)
    cap = 40.0 if alarm_on else 35.0
    allowed = []
    known = []
    for candidate in pool:
        value = _candidate_carry(candidate)
        if value is not None:
            known.append((value, candidate))
            if value <= cap:
                allowed.append(candidate)
    meta = {
        "schema": "hard35_enforcement.v1",
        "cap_min": cap,
        "alarm": alarm_on,
        "pool_count": len(pool),
        "within_cap_count": len(allowed),
        "unknown_count": len(pool) - len(known),
    }
    if allowed:
        meta["reason"] = "candidate_within_carry_cap"
        return allowed, None, meta
    alert = min(known, key=lambda row: row[0])[1] if known else (
        pool[0] if pool else None
    )
    meta["reason"] = "no_candidate_within_carry_cap"
    return [], alert, meta
