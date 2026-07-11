#!/usr/bin/env python3
"""Prawda champion/challenger dla eta_calibration.

Promocja jest dozwolona tylko na zamrozonym supporcie zapisanym w
odtwarzalnym artefakcie obecnego championa. Artefakt zawiera model ewaluacyjny,
jego predykcje na zahaszowanych kluczach supportu i fingerprint targetow. Nie
zapisuje order_id, adresow, GPS ani targetow per rekord.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from typing import Optional

import numpy as np
from scipy import stats as sps

from dispatch_v2.tools.eta_calibration import models as M

ARTIFACT_SCHEMA = "eta_calib_model.v2"
EVIDENCE_SCHEMA = "eta_calib_promotion_evidence.v1"
SUPPORT_KEY_VERSION = "sha256:eta-calib-support-v1"


def _canonical_json(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_obj(obj: object) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def support_key(row: dict) -> Optional[str]:
    """Nieodwracalny klucz supportu; raw order_id nigdy nie trafia do artefaktu."""
    oid = row.get("order_id")
    day = row.get("day")
    if oid is None or not day:
        return None
    raw = f"eta-calib-support-v1\0{day}\0{oid}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def target_value(row: dict, leg: str) -> Optional[float]:
    raw = row.get("pickup_slip_koord_min") if leg == M.PICKUP else row.get("actual_deliver_min")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _prediction(model, row: dict) -> Optional[float]:
    q = model.predict_quantiles(row)
    if q is None or 0.5 not in q:
        return None
    value = float(q[0.5])
    return value if math.isfinite(value) else None


def _rows_by_support(rows: list[dict]) -> tuple[dict[str, dict], Optional[str]]:
    out: dict[str, dict] = {}
    for row in rows:
        key = support_key(row)
        if key is None:
            continue
        if key in out:
            return {}, "duplicate_support_key"
        out[key] = row
    return out, None


def _target_fingerprint(records: list[tuple[str, float]]) -> str:
    stable = [[key, format(target, ".12g")] for key, target in sorted(records)]
    return _sha256_obj(stable)


def build_evidence(
    rows: list[dict], model, leg: str, model_name: str, holdout_cut_day: str,
) -> dict:
    """Buduje anonimowy, zamrozony evidence artifact z pelnym modelem ewaluacyjnym."""
    records = []
    targets = []
    seen = set()
    for row in rows:
        key = support_key(row)
        target = target_value(row, leg)
        pred = _prediction(model, row)
        if key is None or target is None or pred is None:
            continue
        if key in seen:
            raise ValueError("duplicate_support_key")
        seen.add(key)
        records.append({"key": key, "prediction": pred})
        targets.append((key, target))
    records.sort(key=lambda item: item["key"])
    days = sorted({str(r.get("day")) for r in rows if r.get("day")})
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "feature_contract": M.FEATURE_CONTRACT_VERSION,
        "support_key_version": SUPPORT_KEY_VERSION,
        "leg": leg,
        "model": model_name,
        "holdout_cut_day": holdout_cut_day,
        "holdout_end_day": days[-1] if days else None,
        "n_support": len(records),
        "support_fingerprint": _sha256_obj([r["key"] for r in records]),
        "target_fingerprint": _target_fingerprint(targets),
        "predictions": records,
        "evaluation_model": M.model_to_artifact(model),
    }
    evidence["integrity_sha256"] = _sha256_obj(evidence)
    return evidence


def build_model_payload(
    *, leg: str, model_name: str, runtime_model, evaluation_model,
    evidence_rows: list[dict], holdout_cut_day: str, generated_at: str,
    operational_quantile: float,
) -> dict:
    """Kandydat/champion: model runtime + odtwarzalny model/evidence walidacyjny."""
    payload = {
        "schema": ARTIFACT_SCHEMA,
        "feature_contract": M.FEATURE_CONTRACT_VERSION,
        "generated_at": generated_at,
        "leg": leg,
        "champion": model_name,
        "operational_quantile": operational_quantile,
        "runtime_model": M.model_to_artifact(runtime_model),
        "promotion_evidence": build_evidence(
            evidence_rows, evaluation_model, leg, model_name, holdout_cut_day,
        ),
        "note": "SHADOW-ONLY; promocja wymaga exact-support paired gate.",
    }
    payload["artifact_sha256"] = _sha256_obj(payload)
    return payload


def validate_model_payload(payload: object, leg: str) -> Optional[str]:
    if not isinstance(payload, dict):
        return "artifact_not_object"
    if payload.get("schema") != ARTIFACT_SCHEMA:
        return "artifact_legacy_or_unknown_schema"
    if payload.get("feature_contract") != M.FEATURE_CONTRACT_VERSION:
        return "artifact_feature_contract_mismatch"
    if payload.get("leg") != leg:
        return "artifact_leg_mismatch"
    expected_hash = payload.get("artifact_sha256")
    unsigned = {k: v for k, v in payload.items() if k != "artifact_sha256"}
    if not expected_hash or expected_hash != _sha256_obj(unsigned):
        return "artifact_integrity_mismatch"
    evidence = payload.get("promotion_evidence")
    reason = validate_evidence(evidence, leg)
    if reason:
        return reason
    try:
        M.model_from_artifact(payload.get("runtime_model") or {})
        M.model_from_artifact(evidence.get("evaluation_model") or {})
    except Exception:
        return "artifact_model_unreproducible"
    return None


def validate_evidence(evidence: object, leg: str) -> Optional[str]:
    if not isinstance(evidence, dict):
        return "evidence_missing"
    if evidence.get("schema") != EVIDENCE_SCHEMA:
        return "evidence_schema_mismatch"
    if evidence.get("feature_contract") != M.FEATURE_CONTRACT_VERSION:
        return "evidence_feature_contract_mismatch"
    if evidence.get("support_key_version") != SUPPORT_KEY_VERSION:
        return "evidence_support_key_mismatch"
    if evidence.get("leg") != leg:
        return "evidence_leg_mismatch"
    expected_hash = evidence.get("integrity_sha256")
    unsigned = {k: v for k, v in evidence.items() if k != "integrity_sha256"}
    if not expected_hash or expected_hash != _sha256_obj(unsigned):
        return "evidence_integrity_mismatch"
    predictions = evidence.get("predictions")
    if not isinstance(predictions, list):
        return "evidence_predictions_missing"
    keys = [r.get("key") for r in predictions if isinstance(r, dict)]
    if len(keys) != len(predictions) or len(set(keys)) != len(keys):
        return "evidence_support_not_unique"
    if evidence.get("n_support") != len(keys):
        return "evidence_support_count_mismatch"
    if evidence.get("support_fingerprint") != _sha256_obj(sorted(keys)):
        return "evidence_support_fingerprint_mismatch"
    return None


def load_model_payload(path: str, leg: str) -> tuple[Optional[dict], Optional[str]]:
    if not path or not os.path.exists(path):
        return None, "incumbent_artifact_missing"
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None, "incumbent_artifact_unreadable"
    reason = validate_model_payload(payload, leg)
    return (None, reason) if reason else (payload, None)


def _paired_stats(challenger_err: list[float], incumbent_err: list[float]) -> dict:
    a = np.abs(np.asarray(challenger_err, dtype=float))
    b = np.abs(np.asarray(incumbent_err, dtype=float))
    if len(a) != len(b) or len(a) < 2:
        return {"n": min(len(a), len(b)), "delta_mae": None, "ci": [None, None], "p": None}
    delta = a - b
    rng = np.random.default_rng(20260711)
    n = len(delta)
    stats = [float(np.mean(delta[rng.integers(0, n, n)])) for _ in range(2000)]
    lo, hi = np.percentile(stats, [2.5, 97.5])
    try:
        p = float(sps.wilcoxon(a, b).pvalue)
    except (ValueError, TypeError):
        p = None
    return {
        "n": n,
        "delta_mae": round(float(np.mean(delta)), 6),
        "ci": [round(float(lo), 6), round(float(hi), 6)],
        "p": p,
    }


def hold_decision(leg: str, challenger: str, reason: str) -> dict:
    return {
        "leg": leg,
        "challenger": challenger,
        "status": "HOLD",
        "promote": False,
        "reason": reason,
        "support_exact": False,
        "n_common": 0,
    }


def compare_on_frozen_support(
    challenger_model, rows: list[dict], incumbent_payload: dict, cfg: dict,
    leg: str, challenger_name: str,
) -> dict:
    """Porownuje oba modele na DOKLADNIE tych samych rekordach frozen holdoutu."""
    reason = validate_model_payload(incumbent_payload, leg)
    if reason:
        return hold_decision(leg, challenger_name, reason)
    evidence = incumbent_payload["promotion_evidence"]
    expected = {r["key"]: float(r["prediction"]) for r in evidence["predictions"]}
    rows_by_key, row_reason = _rows_by_support(rows)
    if row_reason:
        return hold_decision(leg, challenger_name, row_reason)
    missing_rows = sorted(set(expected) - set(rows_by_key))
    if missing_rows:
        decision = hold_decision(leg, challenger_name, "frozen_support_rows_missing")
        decision["n_missing"] = len(missing_rows)
        return decision

    frozen_rows = [rows_by_key[key] for key in sorted(expected)]
    targets = []
    for row in frozen_rows:
        value = target_value(row, leg)
        if value is None:
            return hold_decision(leg, challenger_name, "frozen_support_target_missing")
        targets.append((support_key(row), value))
    if _target_fingerprint(targets) != evidence.get("target_fingerprint"):
        return hold_decision(leg, challenger_name, "frozen_support_target_drift")

    incumbent_model = M.model_from_artifact(evidence["evaluation_model"])
    challenger_err, incumbent_err = [], []
    reproducibility_drift = 0
    for row in frozen_rows:
        key = support_key(row)
        target = target_value(row, leg)
        incumbent_pred = _prediction(incumbent_model, row)
        challenger_pred = _prediction(challenger_model, row)
        if incumbent_pred is None or challenger_pred is None:
            return hold_decision(leg, challenger_name, "model_support_mismatch")
        if not math.isclose(incumbent_pred, expected[key], rel_tol=1e-9, abs_tol=1e-9):
            reproducibility_drift += 1
        incumbent_err.append(target - incumbent_pred)
        challenger_err.append(target - challenger_pred)
    if reproducibility_drift:
        decision = hold_decision(leg, challenger_name, "incumbent_not_reproducible")
        decision["n_reproducibility_drift"] = reproducibility_drift
        return decision

    paired = _paired_stats(challenger_err, incumbent_err)
    challenger_mae = float(np.mean(np.abs(challenger_err)))
    incumbent_mae = float(np.mean(np.abs(incumbent_err)))
    improve_pct = (
        100.0 * (incumbent_mae - challenger_mae) / incumbent_mae
        if incumbent_mae > 1e-12 else 0.0
    )
    acceptance = cfg["acceptance"]
    threshold = float(
        acceptance["pickup_mae_improve_pct"]
        if leg == M.PICKUP else acceptance["delivery_mae_improve_pct"]
    )
    alpha = float(acceptance["significance_alpha"])
    min_support = int(acceptance.get("min_paired_records", 30))
    ni_margin_pct = float(acceptance.get("non_inferiority_margin_pct", 0.0))
    ci_hi = paired["ci"][1]
    significant = bool(
        paired["p"] is not None and paired["p"] < alpha
        and ci_hi is not None and ci_hi < 0.0
    )
    non_inferior = bool(
        ci_hi is not None and ci_hi <= incumbent_mae * ni_margin_pct / 100.0
    )
    enough = paired["n"] >= min_support
    material = improve_pct >= threshold
    promote = bool(enough and material and significant and non_inferior)
    reasons = []
    if not enough:
        reasons.append("insufficient_paired_support")
    if not material:
        reasons.append("improvement_below_config_threshold")
    if not significant:
        reasons.append("paired_significance_failed")
    if not non_inferior:
        reasons.append("non_inferiority_failed")
    return {
        "leg": leg,
        "challenger": challenger_name,
        "incumbent": incumbent_payload.get("champion"),
        "status": "PROMOTE" if promote else "HOLD",
        "promote": promote,
        "reason": "criteria_met" if promote else "+".join(reasons),
        "support_exact": True,
        "support_fingerprint": evidence["support_fingerprint"],
        "holdout_cut_day": evidence["holdout_cut_day"],
        "holdout_end_day": evidence.get("holdout_end_day"),
        "n_common": paired["n"],
        "challenger_mae": round(challenger_mae, 6),
        "incumbent_mae": round(incumbent_mae, 6),
        "improve_pct": round(improve_pct, 6),
        "required_improve_pct": threshold,
        "paired": paired,
        "significant": significant,
        "non_inferior": non_inferior,
    }
