"""Zaufanie do ETA ``free_at`` dla eskalacji best-effort (wyłącznie SHADOW).

Moduł rozdziela dwa poziomy:

* :func:`eta_trust_metrics` jest funkcją czystą: z metryk kandydata i jawnego
  snapshotu dowodów buduje ``eta_trust_*``;
* :func:`load_eta_trust_evidence` tylko odczytuje istniejące artefakty
  ``eta_calib`` i zwraca snapshot. Każdy brak lub dryf kończy się fail-closed.

Sygnał nie zmienia feasibility, scoringu, planu ani wyboru kuriera. Konsument
``dispatch_pipeline._best_effort_objm_shadow`` używa go wyłącznie do
warunkowego progu telemetrii Tier 2 (90 min przy zaufaniu, 30 min bez niego).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Mapping, Optional

from dispatch_v2.courier_resolver import is_position_known


ETA_TRUST_MIN_COURIER_DELIVERY_N = 30
ETA_TRUST_MIN_ERROR_N = 200
ETA_TRUST_MAX_DELIVERY_MAE_MIN = 8.0
# Job eta-calib jest dzienny (05:20 UTC). 36 h toleruje opoznienie jednego
# cyklu, ale po opuszczeniu calej kolejnej doby sygnal przechodzi fail-closed.
ETA_TRUST_MAX_EVIDENCE_AGE_MIN = 36.0 * 60.0
ETA_TRUST_HOLDOUT_DAYS = 14
ETA_TRUST_UNTRUSTED_MAX_FREE_MIN = 30.0

_DEFAULT_STATE_DIR = "/root/.openclaw/workspace/dispatch_state"


@dataclass(frozen=True)
class EtaTrustEvidence:
    """Niemutowalny, agregatowy dowód jakości ETA (bez danych zleceń)."""

    load_reason: str = "unavailable"
    courier_delivery_n: Mapping[str, int] = field(default_factory=dict)
    artifact_model: Optional[str] = None
    artifact_verified: bool = False
    current_model_verified: bool = False
    current_model_mae_min: Optional[float] = None
    current_model_n: int = 0
    rolling_model: Optional[str] = None
    rolling_mae_min: Optional[float] = None
    rolling_n: int = 0
    evaluated_at: Optional[str] = None
    holdout_cut_day: Optional[str] = None


def unavailable_evidence(reason: str) -> EtaTrustEvidence:
    """Jawny snapshot fail-closed dla adaptera/runtime."""
    return EtaTrustEvidence(load_reason=str(reason or "unavailable"))


def _finite_float(value) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _nonnegative_int(value) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, result)


def _parse_utc(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _evidence_age_min(evidence: EtaTrustEvidence, now: datetime) -> Optional[float]:
    """Najgorszy wiek: wykonania joba oraz konca 14-dniowego okna danych."""
    now_utc = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    now_utc = now_utc.astimezone(timezone.utc)
    ages = []
    evaluated = _parse_utc(evidence.evaluated_at)
    if evaluated is None:
        return None
    ages.append((now_utc - evaluated).total_seconds() / 60.0)
    try:
        cut = date.fromisoformat(str(evidence.holdout_cut_day))
    except (TypeError, ValueError):
        return None
    # time_split wybiera poczatek ostatnich 14 dni. cut+14d jest konserwatywnym
    # przyblizeniem konca danych; ujemny wiek (bieg w trakcie dnia) = 0.
    data_end = datetime.combine(
        cut + timedelta(days=ETA_TRUST_HOLDOUT_DAYS), time.min, tzinfo=timezone.utc,
    )
    ages.append(max(0.0, (now_utc - data_end).total_seconds() / 60.0))
    if any(age < -5.0 for age in ages):
        return None
    return max(ages)


def eta_trust_metrics(
    courier_id,
    candidate_metrics: Mapping[str, object],
    evidence: EtaTrustEvidence,
    now: datetime,
) -> dict:
    """Pure: policz mierzalne ``eta_trust_*`` dla jednego kandydata.

    Warunki sa koniunkcja: znana i niesyntetyczna pozycja, pelne pokrycie
    delivery per kurier, integralny i zweryfikowany aktualny champion oraz
    swiezy rolling OOT tej samej rodziny modelu poniżej progow D4/D5.
    """
    metrics = candidate_metrics or {}
    pos_source = metrics.get("pos_source")
    pos_known = is_position_known(pos_source)
    # ``False`` musi byc jawne. Brak pola nie jest dowodem niesyntetycznosci.
    pos_nonsynthetic = metrics.get("road_km_from_synthetic_pos") is False
    cid = str(courier_id or "")
    courier_n = _nonnegative_int(evidence.courier_delivery_n.get(cid))
    model_match = bool(
        evidence.artifact_model
        and evidence.rolling_model
        and evidence.artifact_model == evidence.rolling_model
    )
    age_min = _evidence_age_min(evidence, now)

    reasons = []
    if not pos_known:
        reasons.append("position_unknown")
    if not pos_nonsynthetic:
        reasons.append("position_synthetic_or_unproven")
    if courier_n < ETA_TRUST_MIN_COURIER_DELIVERY_N:
        reasons.append("courier_coverage_below_min")
    if evidence.load_reason != "ok" or not evidence.artifact_verified:
        reasons.append("calibration_artifact_unavailable")
    if not evidence.current_model_verified:
        reasons.append("current_model_unverified")
    if not model_match:
        reasons.append("rolling_model_mismatch")
    if evidence.current_model_n < ETA_TRUST_MIN_ERROR_N:
        reasons.append("current_error_support_below_min")
    if (evidence.current_model_mae_min is None
            or evidence.current_model_mae_min > ETA_TRUST_MAX_DELIVERY_MAE_MIN):
        reasons.append("current_error_above_max")
    if evidence.rolling_n < ETA_TRUST_MIN_ERROR_N:
        reasons.append("recent_error_support_below_min")
    if (evidence.rolling_mae_min is None
            or evidence.rolling_mae_min > ETA_TRUST_MAX_DELIVERY_MAE_MIN):
        reasons.append("recent_error_above_max")
    if age_min is None or age_min > ETA_TRUST_MAX_EVIDENCE_AGE_MIN:
        reasons.append("error_evidence_stale")

    return {
        "eta_trust_ok": not reasons,
        "eta_trust_reason": "ok" if not reasons else "+".join(reasons),
        "eta_trust_pos_known": pos_known,
        "eta_trust_pos_nonsynthetic": pos_nonsynthetic,
        "eta_trust_calib_n": courier_n,
        "eta_trust_calib_min_n": ETA_TRUST_MIN_COURIER_DELIVERY_N,
        "eta_trust_artifact_verified": evidence.artifact_verified,
        "eta_trust_current_verified": evidence.current_model_verified,
        "eta_trust_model_match": model_match,
        "eta_trust_current_mae_min": evidence.current_model_mae_min,
        "eta_trust_recent_mae_min": evidence.rolling_mae_min,
        "eta_trust_error_max_min": ETA_TRUST_MAX_DELIVERY_MAE_MIN,
        "eta_trust_recent_n": evidence.rolling_n,
        "eta_trust_error_min_n": ETA_TRUST_MIN_ERROR_N,
        "eta_trust_evidence_age_min": (
            round(age_min, 1) if age_min is not None else None
        ),
        "eta_trust_evidence_max_age_min": ETA_TRUST_MAX_EVIDENCE_AGE_MIN,
        "eta_trust_error_scope": "delivery_p50_rolling_oot_same_model",
    }


def trusted_tier2_max_free_min(configured_max, eta_trusted: bool) -> float:
    """Pure: 90/configured dla dobrego ETA, literalny fallback ownera 30."""
    if not eta_trusted:
        return ETA_TRUST_UNTRUSTED_MAX_FREE_MIN
    value = _finite_float(configured_max)
    return value if value is not None else ETA_TRUST_UNTRUSTED_MAX_FREE_MIN


def _artifact_hash(payload: Mapping[str, object]) -> str:
    unsigned = {k: v for k, v in payload.items() if k != "artifact_sha256"}
    raw = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _last_json_record(path: Path) -> Optional[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(record, dict):
            return record
    return None


def _load_uncached(delivery_map_path: Path, metrics_path: Path) -> EtaTrustEvidence:
    try:
        payload = json.loads(delivery_map_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return unavailable_evidence("delivery_artifact_missing_or_invalid")
    if not isinstance(payload, dict):
        return unavailable_evidence("delivery_artifact_not_object")
    artifact_verified = bool(
        payload.get("schema") == "eta_calib_model.v2"
        and payload.get("leg") == "delivery"
        and payload.get("artifact_sha256") == _artifact_hash(payload)
    )
    runtime_model = payload.get("runtime_model") or {}
    if not isinstance(runtime_model, dict):
        runtime_model = {}
    cid_map = runtime_model.get("cid_map") or {}
    history = runtime_model.get("courier_history") or {}
    courier_n = {}
    if runtime_model.get("kind") == "L2_lgbm" and isinstance(cid_map, dict) \
            and isinstance(history, dict):
        for cid in cid_map:
            row = history.get(str(cid)) or history.get(cid) or {}
            if isinstance(row, dict):
                courier_n[str(cid)] = _nonnegative_int(row.get("n_pace"))

    record = _last_json_record(metrics_path)
    if record is None:
        return EtaTrustEvidence(
            load_reason="metrics_missing_or_invalid",
            courier_delivery_n=courier_n,
            artifact_model=str(payload.get("champion") or "") or None,
            artifact_verified=artifact_verified,
        )
    decision = ((record.get("decision") or {}).get("delivery") or {})
    leg = ((record.get("legs") or {}).get("delivery") or {})
    writes = ((record.get("map_writes") or {}).get("delivery") or {})
    artifact_model = str(payload.get("champion") or "") or None
    artifact_sha = str(payload.get("artifact_sha256") or "")
    rolling_model = str(leg.get("champion") or "") or None
    rolling_mae = _finite_float(leg.get("champion_mae"))
    rolling_n = _nonnegative_int(leg.get("n_holdout"))

    promoted_now = bool(
        writes.get("champion_written")
        and artifact_sha
        and str(writes.get("artifact_sha256") or "") == artifact_sha
        and artifact_model == rolling_model
    )
    incumbent_verified = bool(
        decision.get("support_exact")
        and artifact_model
        and str(decision.get("incumbent") or "") == artifact_model
    )
    current_verified = bool(artifact_verified and (promoted_now or incumbent_verified))
    if promoted_now:
        current_mae = rolling_mae
        current_n = rolling_n
    else:
        current_mae = _finite_float(decision.get("incumbent_mae"))
        current_n = _nonnegative_int(decision.get("n_common"))

    return EtaTrustEvidence(
        load_reason="ok",
        courier_delivery_n=courier_n,
        artifact_model=artifact_model,
        artifact_verified=artifact_verified,
        current_model_verified=current_verified,
        current_model_mae_min=current_mae,
        current_model_n=current_n,
        rolling_model=rolling_model,
        rolling_mae_min=rolling_mae,
        rolling_n=rolling_n,
        evaluated_at=str(record.get("logged_at") or "") or None,
        holdout_cut_day=str(record.get("holdout_cut_day") or "") or None,
    )


_CACHE_LOCK = threading.Lock()
_CACHE_KEY = None
_CACHE_VALUE: Optional[EtaTrustEvidence] = None


def _file_signature(path: Path):
    try:
        st = path.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None, None


def load_eta_trust_evidence(
    delivery_map_path: Optional[str] = None,
    metrics_path: Optional[str] = None,
) -> EtaTrustEvidence:
    """Wczytaj snapshot; domyslne pliki cache'owane po ``mtime_ns+size``."""
    state = Path(os.environ.get("DISPATCH_STATE_DIR", _DEFAULT_STATE_DIR))
    delivery = Path(delivery_map_path) if delivery_map_path else (
        state / "eta_calib_delivery_map.json"
    )
    metrics = Path(metrics_path) if metrics_path else state / "eta_calib_metrics.jsonl"
    if delivery_map_path is not None or metrics_path is not None:
        return _load_uncached(delivery, metrics)
    key = (str(delivery), _file_signature(delivery), str(metrics), _file_signature(metrics))
    global _CACHE_KEY, _CACHE_VALUE
    with _CACHE_LOCK:
        if key != _CACHE_KEY or _CACHE_VALUE is None:
            _CACHE_VALUE = _load_uncached(delivery, metrics)
            _CACHE_KEY = key
        return _CACHE_VALUE
