#!/usr/bin/env python3
"""Read-only D5 measurement for labelled decision-time pickup P50/P80.

The checker joins the assignment-time snapshot to strict GPS/CID truth and
emits aggregate data only.  Legacy ``decision_eta.v1`` rows remain readable,
but they are explicitly uncomputable for P50/P80 until the optional K6 fields
are present.  No threshold in this module is copied: D4/D5 come from the
owner-bound ``KPI_BINDING_V1`` contract.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from dispatch_v2.tools import _rotated_logs
from dispatch_v2.tools.eta_ground_truth import KPI_BINDING_V1


STATE = Path("/root/.openclaw/workspace/dispatch_state")
DEFAULT_ETA_LOG = STATE / "decision_eta_log.jsonl"
DEFAULT_OUTCOMES = STATE / "decision_outcomes.jsonl"
DEFAULT_DWELL = STATE / "restaurant_dwell.json"
DEFAULT_DB = STATE / "eta_calib.db"
WARSAW = ZoneInfo("Europe/Warsaw")
ALLOWED_ACTIONS = frozenset({
    "PANEL_OVERRIDE", "PANEL_AGREE", "ASSIGN_DIRECT", "F7AGREE",
})
CONFIDENCE_LEVEL = 0.95
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20260727


def _parse_ts(value: Any, *, naive_warsaw: bool = False) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=WARSAW if naive_warsaw else timezone.utc
        )
    return parsed.astimezone(timezone.utc)


def _cid(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _high_confidence(row: Mapping[str, Any]) -> bool:
    if row.get("_source") != "gps_geofence":
        return False
    try:
        points = int(row.get("_n_in_geofence") or 0)
    except (TypeError, ValueError):
        return False
    minimum = _number(row.get("_min_dist_m"))
    radius = _number(row.get("_radius_m"))
    inside = minimum is None or radius is None or minimum <= radius
    return points >= 2 and inside


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * fraction))
    return ordered[index]


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _load_features(db_path: str | Path) -> dict[str, dict[str, Any]]:
    uri = f"file:{Path(db_path)}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT order_id, courier_id, czas_kuriera, eng_pickup_slip_min,
                   slot, is_bundle, was_czasowka, load
              FROM eta_calib_features
            """
        )
        return {str(row["order_id"]): dict(row) for row in rows}
    finally:
        connection.close()


def _scheduled_pickup(departed: datetime, hhmm: Any) -> datetime | None:
    if hhmm in (None, ""):
        return None
    try:
        hour, minute = (int(part) for part in str(hhmm).split(":")[:2])
        local_day = departed.astimezone(WARSAW).date()
        local = datetime(
            local_day.year,
            local_day.month,
            local_day.day,
            hour,
            minute,
            tzinfo=WARSAW,
        )
    except (TypeError, ValueError):
        return None
    return local.astimezone(timezone.utc)


def _iter_records(path: str | Path, since: datetime):
    yield from _rotated_logs.iter_jsonl_records(str(path), since)


def _prediction(candidate: Mapping[str, Any]) -> tuple[dict | None, str | None]:
    pred_op = _number(candidate.get("pred_op"))
    p80 = _number(candidate.get("p80"))
    version = candidate.get("prediction_version")
    provenance = candidate.get("prediction_provenance")
    if pred_op is None or p80 is None:
        return None, "missing_labelled_pred_op_or_p80"
    if p80 < pred_op:
        return None, "non_monotonic_pred_op_p80"
    if version in (None, "") or not isinstance(provenance, Mapping):
        return None, "missing_prediction_provenance_or_version"
    quantiles = provenance.get("quantiles")
    if provenance.get("producer") != (
        "eta_calib_serving.predict_pickup_quantiles_batch"
    ):
        return None, "bad_prediction_producer"
    if quantiles != {"pred_op": 0.5, "p80": 0.8}:
        return None, "bad_prediction_quantile_labels"
    return {
        "pred_op": pred_op,
        "p80": p80,
        "version": str(version),
        "model_version": str(
            provenance.get("model_artifact_sha256_12") or "unknown"
        ),
    }, None


def _bootstrap_improvement_ci(
    pairs: list[tuple[float, float]],
) -> list[float] | None:
    """Predeclared paired percentile bootstrap for improvement vs engine."""
    if not pairs:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    values: list[float] = []
    size = len(pairs)
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = [pairs[rng.randrange(size)] for _ in range(size)]
        engine_mean = statistics.fmean(pair[0] for pair in sample)
        if engine_mean == 0:
            continue
        prediction_mean = statistics.fmean(pair[1] for pair in sample)
        values.append(100.0 * (engine_mean - prediction_mean) / engine_mean)
    if not values:
        return None
    alpha = (1.0 - CONFIDENCE_LEVEL) / 2.0
    return [
        _rounded(_percentile(values, alpha)),
        _rounded(_percentile(values, 1.0 - alpha)),
    ]


def _wilson_ci(successes: int, total: int) -> list[float] | None:
    if total <= 0:
        return None
    # z=1.959963984540054 for a predeclared two-sided 95% interval.
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1.0 + z * z / total
    centre = (proportion + z * z / (2.0 * total)) / denominator
    radius = (
        z
        * math.sqrt(
            proportion * (1.0 - proportion) / total
            + z * z / (4.0 * total * total)
        )
        / denominator
    )
    return [
        _rounded(100.0 * max(0.0, centre - radius)),
        _rounded(100.0 * min(1.0, centre + radius)),
    ]


def calculate(
    *,
    eta_log_path: str | Path,
    outcomes_path: str | Path,
    dwell_path: str | Path,
    db_path: str | Path,
    since: datetime,
) -> dict[str, Any]:
    eta_rows = [
        row for row in _iter_records(eta_log_path, since)
        if row.get("source") == "shadow_dispatcher"
        and _parse_ts(row.get("decision_ts")) is not None
    ]
    eta_rows.sort(key=lambda row: _parse_ts(row["decision_ts"]))
    first_eta = _parse_ts(eta_rows[0]["decision_ts"]) if eta_rows else None

    outcomes_by_order: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _iter_records(outcomes_path, since):
        timestamp = _parse_ts(row.get("ts_decision"))
        if (
            row.get("action") not in ALLOWED_ACTIONS
            or _cid(row.get("actual_cid")) is None
            or timestamp is None
        ):
            continue
        outcomes_by_order[str(row.get("order_id"))].append(row)
    for rows in outcomes_by_order.values():
        rows.sort(key=lambda row: _parse_ts(row["ts_decision"]))

    eta_by_order: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eta_rows:
        eta_by_order[str(row.get("order_id"))].append(row)

    features = _load_features(db_path)
    dwell = json.loads(Path(dwell_path).read_text(encoding="utf-8"))
    reasons: Counter[str] = Counter()
    complete: list[dict[str, Any]] = []
    labelled: list[dict[str, Any]] = []

    for order_id, gps in dwell.items():
        reasons["dwell_total"] += 1
        if not isinstance(gps, Mapping) or not _high_confidence(gps):
            reasons["not_high_confidence"] += 1
            continue
        reasons["high_confidence"] += 1
        departed = _parse_ts(
            gps.get("departed_restaurant"), naive_warsaw=True
        )
        dwell_cid = _cid(gps.get("courier_id"))
        feature = features.get(str(order_id))
        if departed is None:
            reasons["departed_missing_or_invalid"] += 1
            continue
        if dwell_cid is None:
            reasons["dwell_cid_missing"] += 1
            continue
        if feature is None:
            reasons["feature_missing"] += 1
            continue
        if _cid(feature.get("courier_id")) != dwell_cid:
            reasons["dwell_feature_cid_mismatch"] += 1
            continue
        scheduled = _scheduled_pickup(departed, feature.get("czas_kuriera"))
        if scheduled is None:
            reasons["scheduled_pickup_missing_or_invalid"] += 1
            continue
        actual_slip = (departed - scheduled).total_seconds() / 60.0
        if abs(actual_slip) > 180:
            reasons["target_abs_gt_180"] += 1
            continue

        outcomes = [
            row for row in outcomes_by_order.get(str(order_id), [])
            if _cid(row.get("actual_cid")) == dwell_cid
            and _parse_ts(row.get("ts_decision")) <= departed
        ]
        if not outcomes:
            reasons["matching_assignment_outcome_missing"] += 1
            continue
        outcome_ts = _parse_ts(outcomes[-1]["ts_decision"])
        if first_eta is None or outcome_ts < first_eta:
            reasons["outcome_before_decision_eta_window"] += 1
            continue
        reasons["eligible_denominator"] += 1

        snapshots = [
            row for row in eta_by_order.get(str(order_id), [])
            if _parse_ts(row.get("decision_ts")) <= outcome_ts
        ]
        if not snapshots:
            reasons["no_eta_snapshot_at_or_before_outcome"] += 1
            continue
        snapshot = snapshots[-1]
        courier = next(
            (
                candidate for candidate in snapshot.get("candidates", [])
                if _cid(candidate.get("cid")) == dwell_cid
            ),
            None,
        )
        if courier is None:
            reasons["actual_courier_not_in_snapshot_pool"] += 1
            continue
        leg = next(
            (
                item for item in courier.get("legs", [])
                if str(item.get("order_id")) == str(order_id)
            ),
            None,
        )
        if leg is None:
            reasons["order_leg_missing"] += 1
            continue
        predicted_at = _parse_ts(leg.get("pickup_eta_at"))
        if predicted_at is None:
            reasons["pickup_eta_missing_or_invalid"] += 1
            continue
        legacy_slip = (predicted_at - scheduled).total_seconds() / 60.0
        legacy_error = legacy_slip - actual_slip
        if abs(legacy_error) > 180:
            reasons["prediction_abs_error_gt_180"] += 1
            continue

        engine_slip = _number(feature.get("eng_pickup_slip_min"))
        base = {
            "actual_slip": actual_slip,
            "legacy_error": legacy_error,
            "engine_error": (
                engine_slip - actual_slip
                if engine_slip is not None else None
            ),
            "segment": str(feature.get("slot") or "unknown"),
        }
        complete.append(base)
        reasons["complete_case"] += 1

        prediction, prediction_error = _prediction(courier)
        if prediction is None:
            reasons[prediction_error or "prediction_unavailable"] += 1
            continue
        p50_error = prediction["pred_op"] - actual_slip
        if abs(p50_error) > 180:
            reasons["labelled_prediction_abs_error_gt_180"] += 1
            continue
        labelled.append({
            **base,
            "p50_error": p50_error,
            "p80_late": actual_slip > prediction["p80"],
            "prediction_version": prediction["version"],
            "model_version": prediction["model_version"],
        })
        reasons["prediction_complete_case"] += 1

    coverage_gate = KPI_BINDING_V1["coverage_gate"]
    thresholds = KPI_BINDING_V1["thresholds"]
    eligible_n = reasons["eligible_denominator"]
    complete_n = len(complete)
    prediction_n = len(labelled)
    complete_pct = 100.0 * complete_n / eligible_n if eligible_n else None
    prediction_pct = 100.0 * prediction_n / eligible_n if eligible_n else None
    min_n = int(coverage_gate["min_n"])
    min_pct = float(coverage_gate["min_complete_case_pct"])

    p50_errors = [row["p50_error"] for row in labelled]
    p50_abs = [abs(value) for value in p50_errors]
    common = [
        row for row in labelled if row["engine_error"] is not None
    ]
    pairs = [
        (abs(row["engine_error"]), abs(row["p50_error"])) for row in common
    ]
    engine_mean = (
        statistics.fmean(pair[0] for pair in pairs) if pairs else None
    )
    prediction_mean = (
        statistics.fmean(pair[1] for pair in pairs) if pairs else None
    )
    improvement = (
        100.0 * (engine_mean - prediction_mean) / engine_mean
        if engine_mean not in (None, 0.0) and prediction_mean is not None
        else None
    )
    late_n = sum(1 for row in labelled if row["p80_late"])
    late_pct = 100.0 * late_n / prediction_n if prediction_n else None

    prediction_ready = (
        prediction_n >= min_n
        and prediction_pct is not None
        and prediction_pct >= min_pct
    )
    if prediction_ready:
        instrument_reason = None
    elif prediction_n < min_n:
        instrument_reason = (
            "missing_labelled_pred_op_or_p80"
            if reasons["missing_labelled_pred_op_or_p80"]
            else "labelled_prediction_sample_below_min_n"
        )
    else:
        instrument_reason = "labelled_prediction_coverage_below_min_pct"
    if complete_n < min_n or complete_pct is None or complete_pct < min_pct:
        verdict = "WYMAGA_WIECEJ_DANYCH"
    elif not prediction_ready:
        verdict = "HOLD_PREDICTION_UNCOMPUTABLE"
    else:
        late_low, late_high = thresholds["late_band_pct"]
        checks = [
            statistics.fmean(p50_abs) <= thresholds["pickup"]["mae_max_min"],
            improvement is not None
            and improvement
            >= thresholds["pickup"]["min_improvement_vs_engine_pct"],
            abs(statistics.median(p50_errors))
            <= thresholds["median_bias_abs_max_min"],
            _percentile(p50_abs, 0.9)
            <= thresholds["p90_abs_err_max_min"],
            late_pct is not None and late_low <= late_pct <= late_high,
        ]
        verdict = "PASS_D5" if all(checks) else "HOLD_D5_THRESHOLDS"

    return {
        "schema": "gps_decision_eta_remeasure.v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": {
            "target": "high-confidence GPS restaurant_last_inside_at",
            "prediction_anchor": KPI_BINDING_V1["prediction_anchor"],
            "prediction_target": "pickup_slip_vs_czas_kuriera_min",
            "identity": (
                "strict order_id + dwell CID + feature CID + outcome CID"
            ),
            "privacy": "aggregate-only; no order/courier identifiers emitted",
            "read_only": True,
        },
        "window": {
            "since": since.isoformat(),
            "first_decision_eta_at": (
                first_eta.isoformat() if first_eta else None
            ),
            "eta_snapshot_rows": len(eta_rows),
        },
        "funnel": dict(sorted(reasons.items())),
        "coverage": {
            "eligible_n": eligible_n,
            "complete_n": complete_n,
            "complete_pct": _rounded(complete_pct),
            "prediction_complete_n": prediction_n,
            "prediction_complete_pct": _rounded(prediction_pct),
            "gate_min_n": min_n,
            "gate_min_pct": min_pct,
        },
        "instrument": {
            "calculable": prediction_ready,
            "reason": instrument_reason,
            "required_optional_fields": [
                "pred_op",
                "p80",
                "prediction_version",
                "prediction_provenance",
            ],
            "prediction_versions": sorted({
                row["prediction_version"] for row in labelled
            }),
            "model_versions": sorted({
                row["model_version"] for row in labelled
            }),
        },
        "metrics": {
            "p50_mae_min": (
                _rounded(statistics.fmean(p50_abs)) if p50_abs else None
            ),
            "p50_bias_median_min": (
                _rounded(statistics.median(p50_errors))
                if p50_errors else None
            ),
            "p90_abs_error_min": _rounded(_percentile(p50_abs, 0.9)),
            "p80_late_n": late_n if labelled else None,
            "p80_late_pct": _rounded(late_pct),
            "engine_common_n": len(common),
            "improvement_vs_engine_pct": _rounded(improvement),
        },
        "confidence": {
            "level": CONFIDENCE_LEVEL,
            "paired_bootstrap": {
                "metric": "improvement_vs_engine_pct",
                "resamples": BOOTSTRAP_RESAMPLES,
                "seed": BOOTSTRAP_SEED,
                "ci": _bootstrap_improvement_ci(pairs),
            },
            "p80_late_wilson_pct_ci": _wilson_ci(late_n, prediction_n),
        },
        "thresholds": {
            "binding_version": KPI_BINDING_V1["binding_version"],
            "coverage_gate": coverage_gate,
            "d5": thresholds,
        },
        "verdict": verdict,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eta-log", default=str(DEFAULT_ETA_LOG))
    parser.add_argument("--outcomes", default=str(DEFAULT_OUTCOMES))
    parser.add_argument("--dwell", default=str(DEFAULT_DWELL))
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument(
        "--since",
        required=True,
        help="inclusive ISO timestamp; naive values are rejected",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    since = _parse_ts(args.since)
    if since is None:
        parser.error("--since musi być strefowym timestampem ISO")
    report = calculate(
        eta_log_path=args.eta_log,
        outcomes_path=args.outcomes,
        dwell_path=args.dwell,
        db_path=args.db,
        since=since,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    if report["verdict"] in {
        "WYMAGA_WIECEJ_DANYCH", "HOLD_PREDICTION_UNCOMPUTABLE",
    }:
        return 2
    return 0 if report["verdict"] == "PASS_D5" else 1


if __name__ == "__main__":
    raise SystemExit(main())
