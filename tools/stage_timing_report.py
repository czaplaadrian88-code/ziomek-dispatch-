#!/usr/bin/env python3
"""Read-only raport Z-P1-03 z ``decision_stage_timing.v1``.

Nie ustawia progow, nie alarmuje i nie steruje backpressure. Pokazuje osobno
wall-time sciezki krytycznej oraz nakladajaca sie sume pracy kandydatow.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from dispatch_v2.observability.stage_timing import SIDECAR_SCHEMA
from dispatch_v2.tools import _rotated_logs


DEFAULT_SIDECAR = Path(
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.stage_timings.jsonl")
DEFAULT_LEDGER = Path(
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl")
HISTOGRAM_BOUNDS_MS = (1, 5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000)
STAGE_FIELDS = (
    "service_wall_ms",
    "event_e2e_ms",
    "record_build_ms",
    "preledger_effects_ms",
    "ledger_append_wall_ms",
    "postledger_effects_ms",
    "event_ack_ms",
    "timing.assess_wall_ms",
    "timing.impl_wall_ms",
    "timing.pre_recheck_wall_ms",
    "timing.candidate_pre_recheck_work_sum_ms",
    "timing.fanout_wall_ms",
    "timing.selection_wall_ms",
    "timing.candidate_work_sum_ms",
    "timing.osrm_work_sum_ms",
    "timing.solver_work_sum_ms",
    "timing.osrm_cache_lock_wait_work_sum_ms",
    "timing.osrm_cache_eviction_work_sum_ms",
)


def _pctile(values: list[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))
    return float(ordered[index])


def histogram(values: Iterable[float]) -> dict[str, int]:
    labels = [f"<={HISTOGRAM_BOUNDS_MS[0]}"] + [
        f"({lower},{upper}]"
        for lower, upper in zip(HISTOGRAM_BOUNDS_MS, HISTOGRAM_BOUNDS_MS[1:])
    ]
    out = {label: 0 for label in labels}
    out[f">{HISTOGRAM_BOUNDS_MS[-1]}"] = 0
    for raw in values:
        value = float(raw)
        for index, bound in enumerate(HISTOGRAM_BOUNDS_MS):
            if value <= bound:
                out[labels[index]] += 1
                break
        else:
            out[f">{HISTOGRAM_BOUNDS_MS[-1]}"] += 1
    return out


def percentiles(values: Iterable[float]) -> dict:
    vals = [float(v) for v in values if isinstance(v, (int, float))]
    return {
        "n": len(vals),
        "p50": _pctile(vals, 0.50),
        "p95": _pctile(vals, 0.95),
        "max": max(vals) if vals else None,
        "histogram": histogram(vals),
    }


def _get(record: dict, dotted: str) -> Any:
    value: Any = record
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def iter_records(path: str | Path, since: Optional[datetime] = None,
                 until: Optional[datetime] = None):
    for record in _rotated_logs.iter_jsonl_records(str(path), since):
        ts = _parse_ts(record.get("ts"))
        if since is not None and (ts is None or ts < since):
            continue
        if until is not None and (ts is None or ts >= until):
            continue
        yield record


def build_report_from_records(records: Iterable[dict],
                              ledger_records: Optional[Iterable[dict]] = None,
                              *, since: Optional[datetime] = None,
                              until: Optional[datetime] = None,
                              sources: Optional[dict] = None,
                              grace_seconds: float = 0.0) -> dict:
    """Buduje raport dla okna polotwartego, kotwiczonego ledgerem.

    ``records`` i ``ledger_records`` moga zawierac margines przed/po oknie.
    Mianownik decyzji wybieramy z glownego ledgera w ``[since, until)``, a
    sidecar laczymy po ``event_ref``. Chroni to przed falszywym missing na
    granicy pomiedzy appendem decyzji, ACK i finalnym markerem ticku.
    """
    def in_window(row: dict) -> bool:
        if since is None and until is None:
            return True
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            return False
        if since is not None and ts < since:
            return False
        if until is not None and ts >= until:
            return False
        return True

    materialized = list(records)
    all_decisions = [
        row for row in materialized
        if row.get("schema") == SIDECAR_SCHEMA and row.get("scope") == "decision"
    ]
    all_tick_rows = [
        row for row in materialized
        if row.get("schema") == SIDECAR_SCHEMA and row.get("scope") == "tick"
    ]
    all_tick_open = [row for row in all_tick_rows if row.get("phase") == "open"]
    all_tick_complete = [
        row for row in all_tick_rows if row.get("phase") == "complete"]
    window_tick_open = [row for row in all_tick_open if in_window(row)]
    window_tick_complete = [row for row in all_tick_complete if in_window(row)]
    window_tick_refs = {
        str(row["tick_ref"])
        for row in (*window_tick_open, *window_tick_complete)
        if row.get("tick_ref")
    }
    all_open_refs = {
        str(row["tick_ref"]) for row in all_tick_open if row.get("tick_ref")}
    all_complete_refs = {
        str(row["tick_ref"])
        for row in all_tick_complete if row.get("tick_ref")}

    ledger = list(ledger_records) if ledger_records is not None else None
    ledger_target = [row for row in ledger or [] if in_window(row)]
    ledger_target_refs = Counter(
        str(row["event_ref"])
        for row in ledger_target
        if row.get("event_ref")
        and isinstance(row.get("timing"), dict)
        and row["timing"].get("schema") == "decision_timing.v1"
    )
    ledger_context_refs = Counter(
        str(row["event_ref"])
        for row in ledger or []
        if row.get("event_ref")
        and isinstance(row.get("timing"), dict)
        and row["timing"].get("schema") == "decision_timing.v1"
    )
    if ledger is None:
        decisions = [row for row in all_decisions if in_window(row)]
    else:
        decisions = [
            row for row in all_decisions
            if row.get("event_ref")
            and str(row["event_ref"]) in ledger_target_refs
        ]

    decision_tick_refs = {
        str(row["tick_ref"]) for row in decisions if row.get("tick_ref")}
    report_tick_refs = window_tick_refs | decision_tick_refs
    tick_open = [
        row for row in all_tick_open
        if str(row.get("tick_ref") or "") in report_tick_refs
    ]
    tick_complete = [
        row for row in all_tick_complete
        if str(row.get("tick_ref") or "") in report_tick_refs
    ]
    open_refs = {str(row["tick_ref"]) for row in tick_open if row.get("tick_ref")}
    complete_refs = {
        str(row["tick_ref"]) for row in tick_complete if row.get("tick_ref")}
    trusted = [
        row for row in decisions
        if row.get("tick_ref") and str(row["tick_ref"]) in all_complete_refs
    ]
    valid = [
        row for row in trusted
        if isinstance(row.get("service_wall_ms"), (int, float))
    ]
    all_decision_tick_counts = Counter(
        str(row["tick_ref"]) for row in all_decisions if row.get("tick_ref"))
    decision_tick_counts = Counter(
        str(row["tick_ref"]) for row in decisions if row.get("tick_ref"))
    decisions_without_tick_ref = sum(1 for row in decisions if not row.get("tick_ref"))
    expected_from_ticks = 0
    actual_from_ticks = 0
    tick_count_mismatches = 0
    outcome_invariant_violations = 0
    for row in tick_complete:
        expected_count = _get(row, "outcomes.decision_rows_buffered")
        tick_ref = row.get("tick_ref")
        if isinstance(expected_count, int) and expected_count >= 0:
            expected_from_ticks += expected_count
            actual_count = all_decision_tick_counts.get(str(tick_ref), 0)
            actual_from_ticks += actual_count
            if tick_ref and actual_count != expected_count:
                tick_count_mismatches += 1
        attempted = _get(row, "outcomes.attempted")
        processed = _get(row, "outcomes.processed")
        failed = _get(row, "outcomes.failed")
        skipped = _get(row, "outcomes.skipped")
        if all(isinstance(value, int) and value >= 0
               for value in (attempted, processed, failed, skipped)):
            if attempted != processed + failed + skipped:
                outcome_invariant_violations += 1
        if (isinstance(processed, int) and processed >= 0
                and isinstance(expected_count, int) and expected_count >= 0
                and processed != expected_count):
            outcome_invariant_violations += 1
    side_ref_counts = Counter(
        str(row["event_ref"]) for row in decisions if row.get("event_ref"))

    if ledger is None:
        ledger_join = {
            "enabled": False,
            "seen": None,
            "expected_rows": None,
            "matched_rows": None,
            "missing_sidecar_rows": None,
            "orphan_sidecar_rows": None,
            "duplicate_sidecar_refs": sum(
                count - 1 for count in side_ref_counts.values() if count > 1),
        }
    else:
        expected = ledger_target_refs
        matched = sum(min(count, side_ref_counts.get(ref, 0))
                      for ref, count in expected.items())
        missing = sum(max(0, count - side_ref_counts.get(ref, 0))
                      for ref, count in expected.items())
        side_window_refs = Counter(
            str(row["event_ref"])
            for row in all_decisions
            if in_window(row) and row.get("event_ref"))
        orphan = sum(max(0, count - ledger_context_refs.get(ref, 0))
                     for ref, count in side_window_refs.items())
        ledger_join = {
            "enabled": True,
            "seen": len(ledger_target),
            "seen_with_grace": len(ledger),
            "expected_rows": sum(expected.values()),
            "matched_rows": matched,
            "missing_sidecar_rows": missing,
            "orphan_sidecar_rows": orphan,
            "duplicate_sidecar_refs": sum(
                count - 1 for count in side_ref_counts.values() if count > 1),
        }

    stages = {}
    for field in STAGE_FIELDS:
        stages[field] = percentiles(
            value for row in valid
            for value in [_get(row, field)]
            if isinstance(value, (int, float))
        )

    queue = {
        "event_age_at_start_ms": percentiles(
            value for row in valid
            if _get(row, "queue.event_clock_skew") is not True
            for value in [_get(row, "queue.event_age_at_start_ms")]
            if isinstance(value, (int, float))
        ),
        "batch_wait_ms": percentiles(
            value for row in valid
            for value in [_get(row, "queue.batch_wait_ms")]
            if isinstance(value, (int, float))
        ),
    }
    for field in (
            "depth_sample", "oldest_event_age_ms", "poll_wall_ms",
            "queue_depth_query_wall_ms", "fleet_snapshot_ms",
            "state_snapshot_ms", "batch_size", "tick_processing_wall_ms"):
        queue[field] = percentiles(
            value for row in tick_complete
            for value in [_get(row, f"queue.{field}")]
            if isinstance(value, (int, float))
        )

    dominant: dict[str, int] = {}
    dominant_fields = (
        "timing.pre_recheck_wall_ms", "timing.fanout_wall_ms",
        "timing.selection_wall_ms", "record_build_ms",
        "preledger_effects_ms", "ledger_append_wall_ms",
        "postledger_effects_ms", "event_ack_ms",
    )
    for row in valid:
        values = {
            name: float(_get(row, name))
            for name in dominant_fields
            if isinstance(_get(row, name), (int, float))
        }
        if values:
            winner = max(values, key=values.get)
            dominant[winner] = dominant.get(winner, 0) + 1

    reconciliation = percentiles(
        float(row["service_unattributed_ms"])
        for row in valid
        if isinstance(row.get("service_unattributed_ms"), (int, float))
    )
    osrm_sources: Counter[str] = Counter()
    for row in valid:
        source_tags = _get(row, "timing.osrm_tags.source")
        if isinstance(source_tags, dict):
            for source, count in source_tags.items():
                if isinstance(count, int) and count >= 0:
                    osrm_sources[str(source)] += count

    tick_outcomes: Counter[str] = Counter()
    for row in tick_complete:
        outcomes = row.get("outcomes")
        if not isinstance(outcomes, dict):
            continue
        for name in ("attempted", "processed", "failed", "skipped",
                     "decision_rows_buffered"):
            value = outcomes.get(name)
            if isinstance(value, int) and value >= 0:
                tick_outcomes[name] += value

    def bool_quality(rows: Iterable[dict], dotted: str) -> dict[str, int]:
        counts = {"true": 0, "false": 0, "missing": 0}
        for row in rows:
            value = _get(row, dotted)
            if value is True:
                counts["true"] += 1
            elif value is False:
                counts["false"] += 1
            else:
                counts["missing"] += 1
        return counts

    panel_missing_reasons: Counter[str] = Counter()
    for row in trusted:
        reason = _get(row, "queue.panel_ingress_missing_reason")
        if reason:
            panel_missing_reasons[str(reason)] += 1
        elif isinstance(_get(row, "queue.panel_ingress_ms"), (int, float)):
            panel_missing_reasons["measured"] += 1
        else:
            panel_missing_reasons["unspecified_missing"] += 1
    depth_sources: Counter[str] = Counter()
    for row in tick_complete:
        source = _get(row, "queue.depth_sample_source")
        depth_sources[str(source) if source else "missing"] += 1
    quality = {
        "event_ack_ok": bool_quality(trusted, "event_ack_ok"),
        "event_clock_skew": bool_quality(trusted, "queue.event_clock_skew"),
        "panel_ingress": dict(sorted(panel_missing_reasons.items())),
        "depth_sample_source": dict(sorted(depth_sources.items())),
        "depth_sample_atomic": bool_quality(
            tick_complete, "queue.depth_sample_atomic"),
        "oldest_event_clock_skew": bool_quality(
            tick_complete, "queue.oldest_event_clock_skew"),
    }

    return {
        "schema": "stage_timing_report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": {
            "since": since.isoformat() if since is not None else None,
            "until_exclusive": until.isoformat() if until is not None else None,
            "semantics": "[since, until)",
            "join_grace_seconds": float(grace_seconds),
        },
        "sources": dict(sources or {}),
        "coverage": {
            "seen": len(materialized),
            "seen_in_window": sum(1 for row in materialized if in_window(row)),
            "decision_rows": len(decisions),
            "trusted_decision_rows": len(trusted),
            "untrusted_decision_rows": len(decisions) - len(trusted),
            "valid": len(valid),
            "invalid": len(trusted) - len(valid),
            "tick_open_rows": len(tick_open),
            "tick_complete_rows": len(tick_complete),
            "incomplete_ticks": len(open_refs - complete_refs),
            "complete_without_open": len(complete_refs - open_refs),
            "decision_rows_without_tick_ref": decisions_without_tick_ref,
            "decision_rows_unknown_tick": sum(
                count for ref, count in decision_tick_counts.items()
                if ref not in all_open_refs and ref not in all_complete_refs),
            "expected_decision_rows_from_ticks": expected_from_ticks,
            "decision_rows_gap_from_ticks": max(
                0, expected_from_ticks - actual_from_ticks),
            "tick_decision_count_mismatches": tick_count_mismatches,
            "tick_outcome_invariant_violations": outcome_invariant_violations,
            "ledger_join": ledger_join,
        },
        "stages": stages,
        "queue": queue,
        "service_reconciliation_error_ms": reconciliation,
        "dominant_stage_counts": dict(sorted(dominant.items())),
        "osrm_source_counts": dict(sorted(osrm_sources.items())),
        "tick_outcomes": dict(sorted(tick_outcomes.items())),
        "quality": quality,
    }


def render_text(report: dict) -> str:
    coverage = report["coverage"]
    join = coverage["ledger_join"]
    lines = [
        "# STAGE TIMING REPORT (READ-ONLY)",
        (f"coverage: valid={coverage['valid']}/{coverage['decision_rows']} "
         f"decision rows; all records={coverage['seen']}"),
        (f"ticks: open={coverage['tick_open_rows']} "
         f"complete={coverage['tick_complete_rows']} "
         f"incomplete={coverage['incomplete_ticks']}"),
        ("ledger join: disabled" if not join["enabled"] else
         f"ledger join: matched={join['matched_rows']}/{join['expected_rows']} "
         f"missing={join['missing_sidecar_rows']} "
         f"orphan={join['orphan_sidecar_rows']}"),
        "",
        "stage                                      n       p50       p95       max",
    ]
    for name, stats in report["stages"].items():
        def fmt(value):
            return "-" if value is None else f"{value:.1f}"
        lines.append(
            f"{name:40s} {stats['n']:6d} {fmt(stats['p50']):>9s} "
            f"{fmt(stats['p95']):>9s} {fmt(stats['max']):>9s}")
    lines.append("")
    lines.append("dominant: " + json.dumps(
        report["dominant_stage_counts"], ensure_ascii=False, sort_keys=True))
    lines.append("quality: " + json.dumps(
        report["quality"], ensure_ascii=False, sort_keys=True))
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only p50/p95/max i histogram etapow decyzji Ziomka")
    parser.add_argument("--path", default=str(DEFAULT_SIDECAR))
    parser.add_argument(
        "--ledger-path", default=str(DEFAULT_LEDGER),
        help="glowny shadow ledger jako mianownik coverage (read-only)")
    parser.add_argument(
        "--no-ledger", action="store_true",
        help="wylacz join coverage; raport jawnie oznaczy brak mianownika")
    parser.add_argument("--days", type=float, default=2.0)
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument(
        "--join-grace-seconds", type=float, default=300.0,
        help="margines odczytu obu logow wokolo okna; join nadal kotwiczy ledger")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    until = _parse_ts(args.until) if args.until else datetime.now(timezone.utc)
    if until is None:
        parser.error("--until musi byc poprawnym ISO-8601")
    since = _parse_ts(args.since) if args.since else until - timedelta(days=args.days)
    if since is None:
        parser.error("--since musi byc poprawnym ISO-8601")
    if since >= until:
        parser.error("wymagane since < until")
    if args.join_grace_seconds < 0:
        parser.error("--join-grace-seconds nie moze byc ujemne")
    grace = timedelta(seconds=args.join_grace_seconds)
    read_since, read_until = since - grace, until + grace
    ledger_records = None if args.no_ledger else iter_records(
        args.ledger_path, read_since, read_until)
    report = build_report_from_records(
        iter_records(args.path, read_since, read_until), ledger_records,
        since=since, until=until,
        sources={
            "sidecar": str(Path(args.path)),
            "ledger": None if args.no_ledger else str(Path(args.ledger_path)),
        },
        grace_seconds=args.join_grace_seconds,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
          if args.as_json else render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
