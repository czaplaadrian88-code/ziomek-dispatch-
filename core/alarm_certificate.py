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
EVIDENCE_SCHEMA = "alarm_certificate_evidence.v1"
_THERMAL_REASONS = (
    "R6_per_order_",
    "R6_picked_up_delta_",
    "sla_violation",
)


class _VerifiedCertificate(dict):
    """Certyfikat odczytany razem z hash-bound evidence jedynego writera."""

    def __init__(
        self,
        value: Dict[str, Any],
        *,
        pool_fingerprint: str,
        strategy2_probe_fingerprint: Optional[str],
    ):
        super().__init__(value)
        self.verified_pool_fingerprint = pool_fingerprint
        self.verified_strategy2_probe_fingerprint = (
            strategy2_probe_fingerprint
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


def _pool_counterfactual(
    candidates: Iterable[Any],
) -> tuple[Dict[str, Any], str]:
    normal = []
    alarm = []
    over_40 = []
    unknown = []
    excluded_other_hard = []
    fingerprint_rows = []
    for candidate in candidates:
        cid = str(getattr(candidate, "courier_id", "") or "")
        value = _candidate_carry(candidate)
        fingerprint_rows.append((
            cid,
            value,
            getattr(candidate, "feasibility_verdict", None),
            getattr(candidate, "feasibility_reason", None),
        ))
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

    counterfactual = {
        "le_35_count": len(normal),
        "between_35_40_count": len(alarm),
        "over_40_count": len(over_40),
        "unknown_count": len(unknown),
        "excluded_other_hard_count": len(excluded_other_hard),
        "le_35_cids": normal,
        "between_35_40_cids": alarm,
    }
    fingerprint = hashlib.sha256(json.dumps(
        {
            "counterfactual": counterfactual,
            "rows": fingerprint_rows,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:20]
    return counterfactual, fingerprint


def _strategy2_fingerprint(
    strategy2_probe: Any,
    *,
    decision_order_id: str,
) -> tuple[Optional[str], Optional[bool]]:
    if (
        not isinstance(strategy2_probe, dict)
        or strategy2_probe.get("schema") != "strategy2_probe.v1"
        or strategy2_probe.get("status") != "EVALUATED"
        or str(strategy2_probe.get("order_id")) != str(decision_order_id)
        or not isinstance(strategy2_probe.get("found"), bool)
    ):
        return None, None
    fingerprint = hashlib.sha256(json.dumps(
        strategy2_probe, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:20]
    return fingerprint, strategy2_probe["found"]


def build(
    candidates: Iterable[Any],
    *,
    decision_order_id: str,
    now: datetime,
    strategy2_probe: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    now = _utc(now) or datetime.now(timezone.utc)
    pool = list(candidates)
    counterfactual, pool_fingerprint = _pool_counterfactual(pool)
    s2_fingerprint, s2_found = _strategy2_fingerprint(
        strategy2_probe, decision_order_id=str(decision_order_id))

    if counterfactual["le_35_count"]:
        classification = "NORMAL"
        alarm_on = False
    elif s2_found is True:
        # S1 nie znalazła planu <=35, ale S2 znalazła bezpieczny przyszły slot.
        # Kanon S1 -> S2 -> S3: taki wynik definitywnie unieważnia Alarm.
        classification = "NORMAL_STRATEGY2"
        alarm_on = False
    elif counterfactual["unknown_count"]:
        # Brak pomiaru choć jednego kandydata po pozostałych HARD-ach oznacza,
        # że nie umiemy udowodnić kontrfaktu „zero <=35”. Nigdy nie awansuj
        # niewiedzy do Alarmu ani do twardego braku.
        classification = "UNEVALUABLE"
        alarm_on = False
    elif s2_found is None:
        # Bez zakończonej sondy S2 nie istnieje dowód „zero ratującego S2”.
        classification = "UNEVALUABLE_STRATEGY2"
        alarm_on = False
    elif counterfactual["between_35_40_count"]:
        classification = "ALARM_CANDIDATE"
        alarm_on = True
    else:
        classification = "HARD_NO_CANDIDATE"
        alarm_on = False

    return {
        "schema": SCHEMA,
        "decision_order_id": str(decision_order_id),
        "observed_at": now.isoformat(),
        "valid_until": (now + timedelta(seconds=TTL_SECONDS)).isoformat(),
        "classification": classification,
        "alarm": alarm_on,
        "counterfactual": counterfactual,
        "pool_fingerprint": pool_fingerprint,
        "strategy2_probe_fingerprint": s2_fingerprint,
        "strategy2_found": s2_found,
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
    candidates: Optional[Iterable[Any]] = None,
    strategy2_probe: Optional[Dict[str, Any]] = None,
    verified_pool_fingerprint: Optional[str] = None,
    verified_strategy2_probe_fingerprint: Optional[str] = None,
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
    s2_fingerprint = certificate.get("strategy2_probe_fingerprint")
    if s2_fingerprint is not None and (
        not isinstance(s2_fingerprint, str)
        or len(s2_fingerprint) != 20
        or any(ch not in "0123456789abcdef" for ch in s2_fingerprint)
    ):
        return False
    if certificate.get("strategy2_found") not in (True, False, None):
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

    # F4: publiczna walidacja nigdy nie ufa licznikom ani fingerprintowi
    # dostarczonym przez certyfikat. Musi dostać realną pulę i przeliczyć oba.
    if candidates is not None:
        pool = list(candidates)
        expected = build(
            pool,
            decision_order_id=str(certificate.get("decision_order_id")),
            now=observed,
            strategy2_probe=strategy2_probe,
        )
        return all(
            certificate.get(key) == expected.get(key)
            for key in (
                "classification",
                "alarm",
                "counterfactual",
                "pool_fingerprint",
                "strategy2_probe_fingerprint",
                "strategy2_found",
            )
        )

    if (
        verified_pool_fingerprint != certificate.get("pool_fingerprint")
        or verified_strategy2_probe_fingerprint
        != certificate.get("strategy2_probe_fingerprint")
    ):
        return False
    s2_found = certificate.get("strategy2_found")
    if cf["le_35_count"]:
        expected_classification, expected_alarm = "NORMAL", False
    elif s2_found is True:
        expected_classification, expected_alarm = "NORMAL_STRATEGY2", False
    elif cf["unknown_count"]:
        expected_classification, expected_alarm = "UNEVALUABLE", False
    elif s2_found is None:
        expected_classification, expected_alarm = (
            "UNEVALUABLE_STRATEGY2", False)
    elif cf["between_35_40_count"]:
        expected_classification, expected_alarm = "ALARM_CANDIDATE", True
    else:
        expected_classification, expected_alarm = "HARD_NO_CANDIDATE", False
    return (
        certificate.get("classification") == expected_classification
        and (certificate.get("alarm") is True) == expected_alarm
    )


def is_alarm(
    certificate: Any,
    now: Optional[datetime] = None,
    *,
    candidates: Optional[Iterable[Any]] = None,
    strategy2_probe: Optional[Dict[str, Any]] = None,
) -> bool:
    if not C.decision_flag("ENABLE_ALARM_CERTIFICATE_SHADOW"):
        return False
    check_now = now or datetime.now(timezone.utc)
    verified_pool = getattr(
        certificate, "verified_pool_fingerprint", None)
    verified_s2 = getattr(
        certificate, "verified_strategy2_probe_fingerprint", None)
    return (
        validate(
            certificate,
            check_now,
            candidates=candidates,
            strategy2_probe=strategy2_probe,
            verified_pool_fingerprint=verified_pool,
            verified_strategy2_probe_fingerprint=verified_s2,
        )
        and certificate.get("alarm") is True
    )


def _canonical_bytes(value: Dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _atomic_write_json(target: str, value: Dict[str, Any]) -> None:
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=directory,
        prefix=f".{os.path.basename(target)}_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(_canonical_bytes(value))
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


def publish(
    certificate: Dict[str, Any],
    *,
    path: Optional[str] = None,
    candidates: Optional[Iterable[Any]] = None,
    strategy2_probe: Optional[Dict[str, Any]] = None,
) -> str:
    if not C.decision_flag("ENABLE_ALARM_CERTIFICATE_SHADOW"):
        return "flag_off"
    now = _utc(certificate.get("observed_at"))
    if now is None or not validate(
        certificate,
        now,
        candidates=candidates,
        strategy2_probe=strategy2_probe,
    ):
        return "invalid"
    target = path or SNAPSHOT_PATH
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "certificate_sha256": hashlib.sha256(
            _canonical_bytes(certificate)).hexdigest(),
        "pool_fingerprint": certificate["pool_fingerprint"],
        "strategy2_probe_fingerprint": certificate[
            "strategy2_probe_fingerprint"],
    }
    # Evidence pierwsze, certyfikat drugi. Crash między rename'ami daje mismatch
    # i read fail-closed; nigdy parę starego hash-a z nowym certyfikatem.
    _atomic_write_json(f"{target}.evidence", evidence)
    _atomic_write_json(target, certificate)
    return "published"


def read(
    now: datetime,
    *,
    path: Optional[str] = None,
    scope_order_ids: Optional[Iterable[str]] = None,
    candidates: Optional[Iterable[Any]] = None,
    strategy2_probe: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if not C.decision_flag("ENABLE_ALARM_CERTIFICATE_SHADOW"):
        return None
    try:
        with open(path or SNAPSHOT_PATH, "r", encoding="utf-8") as fh:
            certificate = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None
    verified_pool = None
    verified_s2 = None
    if candidates is None:
        try:
            with open(
                f"{path or SNAPSHOT_PATH}.evidence",
                "r",
                encoding="utf-8",
            ) as fh:
                evidence = json.load(fh)
        except (OSError, ValueError, TypeError):
            return None
        if (
            not isinstance(evidence, dict)
            or evidence.get("schema") != EVIDENCE_SCHEMA
            or evidence.get("certificate_sha256")
            != hashlib.sha256(_canonical_bytes(certificate)).hexdigest()
        ):
            return None
        verified_pool = evidence.get("pool_fingerprint")
        verified_s2 = evidence.get("strategy2_probe_fingerprint")
    if not validate(
        certificate,
        now,
        scope_order_ids=scope_order_ids,
        candidates=candidates,
        strategy2_probe=strategy2_probe,
        verified_pool_fingerprint=verified_pool,
        verified_strategy2_probe_fingerprint=verified_s2,
    ):
        return None
    if candidates is not None:
        verified_pool = certificate["pool_fingerprint"]
        verified_s2 = certificate["strategy2_probe_fingerprint"]
    return _VerifiedCertificate(
        certificate,
        pool_fingerprint=verified_pool,
        strategy2_probe_fingerprint=verified_s2,
    )


def hard35_best_effort_choice(
    candidates: Iterable[Any],
    *,
    alarm_certificate: Optional[Dict[str, Any]],
    validation_candidates: Optional[Iterable[Any]] = None,
    strategy2_probe: Optional[Dict[str, Any]] = None,
) -> tuple[list, Optional[Any], Dict[str, Any]]:
    """Filtr HARD35 dla best-effort; nigdy nie ukrywa least-damage.

    Zwraca kandydatów w capie. Gdy pusty, zwraca jawnego najmniej szkodliwego
    kandydata jako `alert`, ale nie jako feasible/proposal.
    """
    pool = list(candidates)
    validation_pool = (
        list(validation_candidates)
        if validation_candidates is not None
        else pool
    )
    alarm_on = is_alarm(
        alarm_certificate,
        candidates=validation_pool,
        strategy2_probe=strategy2_probe,
    )
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
