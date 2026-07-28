#!/usr/bin/env python3
"""S3/S4: deterministyczny, read-only raport progów z ledgerów produkcyjnych.

Skrypt nie stroi silnika i nie wydaje werdyktu biznesowego. Czyta rotacje JSONL,
agreguje wyłącznie liczby (bez order_id/courier_id w artefaktach), a zapisuje
atomowo tylko wskazany przez operatora raport Markdown i sąsiedni JSON.

Uruchomienie kanoniczne (z katalogu ``scripts``)::

    /root/.openclaw/venvs/dispatch/bin/python \
      -m dispatch_v2.tools.s34_threshold_report \
      --since 2026-07-28T00:00 \
      --timestamp 20260728T220000Z \
      --out /root/handover/S34_PAKIET_PROGOW_20260728T220000Z.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from dispatch_v2.core import carry_freshness as _carry_freshness
from dispatch_v2.core import lex_window_guards as _lex_window_guards

try:
    from dispatch_v2.tools import _rotated_logs
except ModuleNotFoundError:  # bezpieczne także dla bezpośredniego uruchomienia pliku
    import importlib.util

    _helper_path = Path(__file__).with_name("_rotated_logs.py")
    _helper_spec = importlib.util.spec_from_file_location(
        "_s34_rotated_logs", _helper_path
    )
    if _helper_spec is None or _helper_spec.loader is None:
        raise
    _rotated_logs = importlib.util.module_from_spec(_helper_spec)
    _helper_spec.loader.exec_module(_rotated_logs)


SCHEMA = "s34.threshold_report.v2"
LEDGER_SCHEMA = "lex_window_ledger.v2"
EPISODE_SCHEMA = "assignment_episode.v1"

DEFAULT_LEDGER = Path(
    "/root/.openclaw/workspace/dispatch_state/lex_window_ledger_v2.jsonl"
)
DEFAULT_EPISODES = Path(
    "/root/.openclaw/workspace/dispatch_state/assignment_episode.jsonl"
)
DEFAULT_SHADOW = Path(
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
)
DEFAULT_FREEZE = Path(
    "/root/handover/ev-czasy-2026-07-27/freeze-20260727"
)

TOLS = (3.0, 5.0, 8.0, 10.0)
GAINS = (0.5, 1.0, 2.0)
CARRY_CAPS = (35.0, 40.0)
PROPOSAL_FRESHNESS_S = 15.0
T3_MIN_N = 200
ETA_SOURCES = ("live", "warm", "planned")

_TOP_VOLATILE = frozenset(
    {
        "ts",
        "emitted_at",
        "decision_id",
        "attempt_id",
        "run_id",
        "coverage",
    }
)
_CALLER_VOLATILE = frozenset({"pid"})


def parse_dt(value: object) -> datetime | None:
    """ISO-8601 -> UTC; brak strefy jest jawnie interpretowany jako UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _record_dt(record: Mapping[str, Any], keys: Sequence[str]) -> datetime | None:
    for key in keys:
        value = parse_dt(record.get(key))
        if value is not None:
            return value
    return None


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def decision_content_signature(record: Mapping[str, Any]) -> str:
    """Podpis semantycznej treści v2 bez czasowo-procesowej koperty.

    Odpowiednikiem v1 ``ts`` jest w v2 ``emitted_at``. Ponadto v2 generuje
    identyfikatory próby/runu, PID i telemetryczne ``coverage``; pozostawienie
    ich zniweczyłoby deduplikację tej samej decyzji powtórzonej przez proces.
    Semantyczne czasy snapshotu loadgov pozostają w podpisie.
    """
    body: dict[str, Any] = {}
    for key, value in record.items():
        if key in _TOP_VOLATILE:
            continue
        if key == "caller" and isinstance(value, Mapping):
            body[key] = {
                subkey: subvalue
                for subkey, subvalue in value.items()
                if subkey not in _CALLER_VOLATILE
            }
        else:
            body[key] = value
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()


def _iter_records(path: Path, since: datetime) -> Iterator[dict[str, Any]]:
    yield from _rotated_logs.iter_jsonl_records(str(path), cutoff_dt=since)


def _source_state(path: Path, since: datetime) -> tuple[tuple[str, int, int], ...]:
    state: list[tuple[str, int, int]] = []
    for name in _rotated_logs.files_in_window(str(path), cutoff_dt=since):
        file_path = Path(name)
        try:
            stat = file_path.stat()
        except OSError:
            continue
        if file_path.is_file():
            state.append((str(file_path), stat.st_size, stat.st_mtime_ns))
    return tuple(state)


def _source_info(
    path: Path,
    since: datetime,
    before_state: tuple[tuple[str, int, int], ...],
) -> dict[str, Any]:
    files = [Path(name) for name, _size, _mtime in _source_state(path, since)]
    digest = hashlib.sha256()
    size = 0
    for file_path in files:
        digest.update(str(file_path).encode("utf-8"))
        digest.update(b"\0")
        with file_path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
    after_state = _source_state(path, since)
    return {
        "base_path": str(path),
        "rotation_files": len(files),
        "bytes": size,
        "sha256": digest.hexdigest() if files else None,
        "changed_during_read_or_hash": before_state != after_state,
    }


def _nested_number(record: Mapping[str, Any], *paths: Sequence[str]) -> float | None:
    for path in paths:
        cursor: object = record
        for key in path:
            if not isinstance(cursor, Mapping):
                cursor = None
                break
            cursor = cursor.get(key)
        number = _number(cursor)
        if number is not None:
            return number
    return None


def _decision_identity(record: Mapping[str, Any]) -> bool:
    decision = record.get("decision")
    return bool(isinstance(decision, Mapping) and decision.get("identity") is True)


def _candidate_chosen(record: Mapping[str, Any]) -> bool:
    decision = record.get("decision")
    return bool(
        isinstance(decision, Mapping) and decision.get("candidate_chosen") is True
    )


def _window_values(record: Mapping[str, Any]) -> tuple[float | None, float | None]:
    baseline = _nested_number(
        record, ("baseline", "window_viol"), ("raw", "W_baseline")
    )
    chosen = _nested_number(
        record, ("chosen", "window_viol"), ("raw", "W_chosen")
    )
    return baseline, chosen


def _drive_gain(record: Mapping[str, Any]) -> float | None:
    baseline = _nested_number(
        record, ("baseline", "drive_min"), ("raw", "drive_baseline")
    )
    chosen = _nested_number(
        record, ("chosen", "drive_min"), ("raw", "drive_chosen")
    )
    if baseline is None or chosen is None:
        return None
    return baseline - chosen


def _window_class(record: Mapping[str, Any]) -> str:
    baseline, chosen = _window_values(record)
    if baseline is None or chosen is None:
        return "n/d"
    if chosen < baseline:
        return "strict_w"
    if chosen == baseline:
        return "equal_w"
    return "w_regression"


def _delivery_delta(record: Mapping[str, Any]) -> float | None:
    """Najgorsze opóźnienie dropoff chosen-baseline; dwell znosi się w delcie."""
    items = record.get("items")
    if not isinstance(items, list):
        return None
    deltas: list[float] = []
    for item in items:
        if not isinstance(item, Mapping) or item.get("kind") != "dropoff":
            continue
        arrival = _number(item.get("arrival_min"))
        baseline = _number(item.get("baseline_arrival_min"))
        if arrival is not None and baseline is not None:
            deltas.append(arrival - baseline)
    return max(deltas) if deltas else None


def _guard_inputs(
    record: Mapping[str, Any],
) -> tuple[
    _lex_window_guards.Facts,
    _lex_window_guards.Facts,
    list[str],
    list[str],
] | None:
    """Minimalny adapter ledger-v2 -> wejście kanonicznego enforcementu.

    Adapter nie zawiera progów ani polityki przeżycia. Odtwarza wyłącznie fakty
    zapisane przez writer WB1/WB2; brak któregokolwiek pola potrzebnego przez
    ``core.lex_window_guards.evaluate`` daje N/D.
    """
    baseline_w, chosen_w = _window_values(record)
    baseline_drive = _nested_number(
        record, ("baseline", "drive_min"), ("raw", "drive_baseline")
    )
    chosen_drive = _nested_number(
        record, ("chosen", "drive_min"), ("raw", "drive_chosen")
    )
    bag = record.get("bag")
    items = record.get("items")
    if (
        baseline_w is None
        or chosen_w is None
        or baseline_drive is None
        or chosen_drive is None
        or not isinstance(bag, Mapping)
        or not isinstance(items, list)
    ):
        return None
    if not baseline_w.is_integer() or not chosen_w.is_integer():
        return None

    carried_raw = bag.get("carried")
    if not isinstance(carried_raw, list):
        return None
    carried_ids = [str(oid) for oid in carried_raw if oid is not None]
    if len(carried_ids) != len(carried_raw) or len(set(carried_ids)) != len(
        carried_ids
    ):
        return None
    carried_set = set(carried_ids)

    baseline_handoff: dict[str, float | None] = {}
    chosen_handoff: dict[str, float | None] = {}
    baseline_carry: dict[str, float | None] = {}
    chosen_carry: dict[str, float | None] = {}
    assigned_ids: list[str] = []
    seen_dropoffs: set[str] = set()

    for item in items:
        if not isinstance(item, Mapping) or item.get("kind") != "dropoff":
            continue
        oid = item.get("order_id")
        if oid is None:
            return None
        oid_text = str(oid)
        if oid_text in seen_dropoffs:
            return None
        seen_dropoffs.add(oid_text)

        raw_carry = _number(item.get("raw_carry_min"))
        dwell = _number(item.get("dwell_min"))
        arrival = _number(item.get("arrival_min"))
        baseline_arrival = _number(item.get("baseline_arrival_min"))
        if None in (dwell, arrival, baseline_arrival):
            return None
        assert dwell is not None
        assert arrival is not None
        assert baseline_arrival is not None

        chosen_handoff_value = _carry_freshness.handoff_min(arrival, dwell)
        baseline_handoff_value = _carry_freshness.handoff_min(
            baseline_arrival, dwell
        )
        if chosen_handoff_value is None or baseline_handoff_value is None:
            return None
        chosen_handoff[oid_text] = chosen_handoff_value
        baseline_handoff[oid_text] = baseline_handoff_value

        if oid_text in carried_set:
            if raw_carry is None:
                return None
            possession_min = arrival - raw_carry
            chosen_carry_value = _carry_freshness.carry_min(
                chosen_handoff_value, possession_min
            )
            baseline_carry_value = _carry_freshness.carry_min(
                baseline_handoff_value, possession_min
            )
            if chosen_carry_value is None or baseline_carry_value is None:
                return None
            chosen_carry[oid_text] = chosen_carry_value
            baseline_carry[oid_text] = baseline_carry_value
        else:
            assigned_ids.append(oid_text)

    if not carried_set.issubset(seen_dropoffs):
        return None

    baseline = _lex_window_guards.Facts(
        window_viol=int(baseline_w),
        drive_min=baseline_drive,
        handoff_by_order=baseline_handoff,
        carry_by_order=baseline_carry,
    )
    chosen = _lex_window_guards.Facts(
        window_viol=int(chosen_w),
        drive_min=chosen_drive,
        handoff_by_order=chosen_handoff,
        carry_by_order=chosen_carry,
    )
    return baseline, chosen, assigned_ids, carried_ids


def grid_survives(
    record: Mapping[str, Any],
    *,
    tol: float,
    gain: float,
    cap: float,
) -> bool | None:
    """Przeżycie dokładnie wg ``core.lex_window_guards.evaluate``.

    Jedyna lokalna odpowiedzialność to adapter danych. Brak danych potrzebnych
    enforcementowi daje N/D; reporter nie zgaduje ani nie odtwarza guardów.
    """
    adapted = _guard_inputs(record)
    if adapted is None:
        return None
    baseline, chosen, assigned_ids, carried_ids = adapted
    result = _lex_window_guards.evaluate(
        baseline,
        chosen,
        assigned_ids=assigned_ids,
        carried_ids=carried_ids,
        thresholds=_lex_window_guards.Thresholds(
            delay_tol_min=tol,
            carry_cap_min=cap,
            min_gain_min=gain,
            alarm=(cap == 40.0),
        ),
    )
    return result.admissible


def _oracle_492(
    window_class: str,
    delivery_delta: float | None,
    drive_gain: float | None,
) -> bool:
    return bool(
        window_class == "equal_w"
        and delivery_delta is not None
        and drive_gain is not None
        and delivery_delta >= 4.0
        and drive_gain < 3.0
    )


def _features(record: Mapping[str, Any]) -> dict[str, Any]:
    klass = _window_class(record)
    delta = _delivery_delta(record)
    gain = _drive_gain(record)
    return {
        "record": record,
        "class": klass,
        "delivery_delta": delta,
        "drive_gain": gain,
        "oracle_492": _oracle_492(klass, delta, gain),
    }


def _grid(features: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tol in TOLS:
        for gain in GAINS:
            by_cap: dict[str, dict[str, Any]] = {}
            for cap in CARRY_CAPS:
                by_class: dict[str, dict[str, int]] = {}
                for klass in ("strict_w", "equal_w"):
                    counts = Counter({"survive": 0, "die": 0, "n/d": 0})
                    for feature in features:
                        if feature["class"] != klass:
                            continue
                        survives = grid_survives(
                            feature["record"], tol=tol, gain=gain, cap=cap
                        )
                        counts[
                            "n/d"
                            if survives is None
                            else "survive"
                            if survives
                            else "die"
                        ] += 1
                    by_class[klass] = dict(counts)

                oracle = [
                    feature
                    for feature in features
                    if feature.get("oracle_492") is True
                ]
                oracle_killed = 0
                oracle_nd = 0
                for feature in oracle:
                    survives = grid_survives(
                        feature["record"], tol=tol, gain=gain, cap=cap
                    )
                    if survives is None:
                        oracle_nd += 1
                    elif not survives:
                        oracle_killed += 1
                by_cap[str(int(cap))] = {
                    "strict_w": by_class["strict_w"],
                    "equal_w": by_class["equal_w"],
                    "oracle_492": {
                        "n": len(oracle),
                        "killed": oracle_killed,
                        "n/d": oracle_nd,
                        "all_killed": (
                            oracle_killed == len(oracle) if oracle else None
                        ),
                    },
                }
            rows.append(
                {
                    "tol_min": tol,
                    "gain_min": gain,
                    "caps": by_cap,
                }
            )
    return rows


def _histogram(values: Sequence[float]) -> dict[str, int]:
    labels = (
        "<=20",
        "(20,25]",
        "(25,30]",
        "(30,35]",
        "(35,40]",
        ">40",
    )
    counts = Counter({label: 0 for label in labels})
    for value in values:
        if value <= 20:
            label = "<=20"
        elif value <= 25:
            label = "(20,25]"
        elif value <= 30:
            label = "(25,30]"
        elif value <= 35:
            label = "(30,35]"
        elif value <= 40:
            label = "(35,40]"
        else:
            label = ">40"
        counts[label] += 1
    return dict(counts)


def _cap_analysis(features: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    strict = [feature for feature in features if feature["class"] == "strict_w"]
    chosen_carry: list[float] = []
    for feature in strict:
        adapted = _guard_inputs(feature["record"])
        if adapted is None:
            continue
        _baseline, chosen, _assigned_ids, _carried_ids = adapted
        value = _carry_freshness.max_carry_min(chosen.carry_by_order)
        if value is not None:
            chosen_carry.append(value)
    nd = len(strict) - len(chosen_carry)
    result: dict[str, Any] = {
        "method": "core.lex_window_guards.evaluate przez adapter ledger-v2",
        "strict_w_total": len(strict),
        "evaluable": len(chosen_carry),
        "n/d": nd,
        "chosen_post_dwell_histogram_min": _histogram(chosen_carry),
        "caps": {},
    }
    for cap in CARRY_CAPS:
        counts = Counter({"survive": 0, "die": 0, "n/d": 0})
        for feature in strict:
            survives = grid_survives(
                feature["record"], tol=TOLS[0], gain=GAINS[0], cap=cap
            )
            counts[
                "n/d" if survives is None else "survive" if survives else "die"
            ] += 1
        die = counts["die"]
        result["caps"][str(int(cap))] = {
            "survive": counts["survive"],
            "die": die,
            "n/d": counts["n/d"],
            "die_rate_evaluable": (
                round(die / len(chosen_carry), 6) if chosen_carry else None
            ),
        }
    return result


def _get_path(record: Mapping[str, Any], path: Sequence[str]) -> object:
    cursor: object = record
    for key in path:
        if not isinstance(cursor, Mapping):
            return None
        cursor = cursor.get(key)
    return cursor


def _gps_cohort(record: Mapping[str, Any]) -> str | None:
    # Wyłącznie jawne pole zwycięzcy/wybranej pozycji; items.possession_source
    # opisuje jedzenie, a nie dostępność GPS kuriera.
    paths = (
        ("decision", "winner_position_source"),
        ("chosen", "position_source"),
        ("route", "winner_position_source"),
        ("candidates", "winner_position_source"),
        ("winner_position_source",),
    )
    for path in paths:
        value = _get_path(record, path)
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower().replace("-", "_")
        if normalized in {"gps", "live", "live_gps", "pwa_gps", "android_gps"}:
            return "gps"
        if normalized in {
            "no_gps",
            "nogps",
            "warm",
            "planned",
            "pre_shift",
            "last_event",
        }:
            return "no_gps"
    return None


def _alarm_cohort(record: Mapping[str, Any]) -> str | None:
    # EWMA/loadgov.source nie jest certyfikatem Alarmu. Akceptujemy tylko
    # jawny boolean/mode, jeżeli przyszła wersja writera go rzeczywiście poda.
    paths = (
        ("loadgov", "alarm"),
        ("loadgov", "alarm_mode"),
        ("loadgov", "alarm_certified"),
        ("thresholds", "alarm"),
        ("decision", "alarm"),
    )
    for path in paths:
        value = _get_path(record, path)
        if isinstance(value, bool):
            return "alarm" if value else "normal"
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"alarm", "true", "1", "certified"}:
                return "alarm"
            if normalized in {"normal", "false", "0"}:
                return "normal"
    # Dla strict-W writer umieszcza efektywny cap w G2, bo verdict EXEMPT
    # powstaje dopiero po kontroli capu. Equal-W ma w tym polu TOL, więc nie
    # wolno uogólniać tego odczytu na pozostałe rekordy.
    if _window_class(record) == "strict_w":
        g2 = _get_path(record, ("guards", "G2"))
        if isinstance(g2, Mapping) and g2.get("verdict") == "EXEMPT":
            cap = _number(g2.get("threshold_effective"))
            if cap == 40.0:
                return "alarm"
            if cap == 35.0:
                return "normal"
    return None


def _cohort_grid(
    features: Sequence[Mapping[str, Any]],
    extractor,
    expected: Sequence[str],
    missing_reason: str,
) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = {key: [] for key in expected}
    unclassified = 0
    for feature in features:
        cohort = extractor(feature["record"])
        if cohort in grouped:
            grouped[cohort].append(feature)
        else:
            unclassified += 1
    classified = sum(len(values) for values in grouped.values())
    return {
        "status": "OK" if classified else "N/D",
        "reason": None if classified else missing_reason,
        "classified": classified,
        "unclassified": unclassified,
        "groups": {
            key: {"n": len(values), "grid": _grid(values) if values else []}
            for key, values in grouped.items()
        },
    }


def analyze_ledger_records(
    records: Iterable[Mapping[str, Any]], since: datetime
) -> dict[str, Any]:
    parsed = 0
    decisions = 0
    before_since = 0
    wrong_schema = 0
    unique: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    duplicates = 0
    missing_timestamp = 0

    ordered = sorted(
        (record for record in records if isinstance(record, Mapping)),
        key=lambda record: (
            _record_dt(record, ("emitted_at", "ts")) or datetime.min.replace(
                tzinfo=timezone.utc
            ),
            _canonical_json(record),
        ),
    )
    for record in ordered:
        parsed += 1
        if record.get("schema") != LEDGER_SCHEMA:
            wrong_schema += 1
            continue
        if record.get("record_kind") != "decision":
            continue
        decisions += 1
        emitted = _record_dt(record, ("emitted_at", "ts"))
        if emitted is None:
            missing_timestamp += 1
            continue
        if emitted < since:
            before_since += 1
            continue
        signature = decision_content_signature(record)
        if signature in seen:
            duplicates += 1
            continue
        seen.add(signature)
        unique.append(record)

    identity = sum(1 for record in unique if _decision_identity(record))
    eligible_records = [
        record
        for record in unique
        if not _decision_identity(record) and _candidate_chosen(record)
    ]
    noncandidate = len(unique) - identity - len(eligible_records)
    features = [_features(record) for record in eligible_records]
    classes = Counter(feature["class"] for feature in features)

    gps_reason = (
        "lex_window_ledger.v2 nie zawiera jawnego źródła pozycji zwycięzcy; "
        "items.possession_source dotyczy wieku jedzenia, nie GPS kuriera"
    )
    alarm_reason = (
        "loadgov nie zawiera jawnego certyfikatu/mode Alarm; tylko strict-W może "
        "być odtworzone z efektywnego capu G2=35/40, a EWMA nie jest zamiennikiem"
    )
    return {
        "input": {
            "parsed_dict_records": parsed,
            "decision_records": decisions,
            "wrong_schema": wrong_schema,
            "before_since": before_since,
            "missing_timestamp": missing_timestamp,
            "duplicates": duplicates,
            "unique_decisions": len(unique),
            "identity_decisions_excluded_from_grid": identity,
            "noncandidate_decisions_excluded_from_grid": noncandidate,
            "eligible_reorders": len(eligible_records),
        },
        "classes": {
            "strict_w": classes["strict_w"],
            "equal_w": classes["equal_w"],
            "w_regression": classes["w_regression"],
            "n/d": classes["n/d"],
        },
        "oracle_492_class_n": sum(
            1 for feature in features if feature["oracle_492"]
        ),
        "grid": _grid(features),
        "cap": _cap_analysis(features),
        "cohorts": {
            "gps": _cohort_grid(
                features, _gps_cohort, ("gps", "no_gps"), gps_reason
            ),
            "alarm": _cohort_grid(
                features, _alarm_cohort, ("alarm", "normal"), alarm_reason
            ),
        },
    }


def _episode_signature(record: Mapping[str, Any]) -> str:
    generation = record.get("assignment_generation")
    if generation not in (None, ""):
        return "generation:" + str(generation)
    body = {
        key: value
        for key, value in record.items()
        if key not in {"recorded_at"}
    }
    return "content:" + hashlib.sha256(
        _canonical_json(body).encode("utf-8")
    ).hexdigest()


def _nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def analyze_assignment_episodes(
    records: Iterable[Mapping[str, Any]], since: datetime
) -> dict[str, Any]:
    selected: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    stats = Counter()
    ordered = sorted(
        (record for record in records if isinstance(record, Mapping)),
        key=lambda record: (
            _record_dt(
                record,
                (
                    "assignment_at",
                    "proposal_computed_at",
                    "recorded_at",
                ),
            )
            or datetime.min.replace(tzinfo=timezone.utc),
            _canonical_json(record),
        ),
    )
    for record in ordered:
        stats["parsed_dict_records"] += 1
        if record.get("schema") != EPISODE_SCHEMA:
            stats["wrong_schema"] += 1
            continue
        event_time = _record_dt(
            record, ("assignment_at", "proposal_computed_at", "recorded_at")
        )
        if event_time is None:
            stats["missing_timestamp"] += 1
            continue
        if event_time < since:
            stats["before_since"] += 1
            continue
        signature = _episode_signature(record)
        if signature in seen:
            stats["duplicates"] += 1
            continue
        seen.add(signature)
        selected.append(record)

    agreements: list[bool] = []
    ages: list[float] = []
    negative_age = 0
    for record in selected:
        agreement = record.get("agreement")
        if isinstance(agreement, bool):
            agreements.append(agreement)
        else:
            proposal = record.get("proposal")
            winner = (
                proposal.get("winner_cid")
                if isinstance(proposal, Mapping)
                else None
            )
            actual = record.get("actual_assigned_cid")
            if winner not in (None, "") and actual not in (None, ""):
                agreements.append(str(winner) == str(actual))

        proposal_at = parse_dt(record.get("proposal_computed_at"))
        assignment_at = parse_dt(record.get("assignment_at"))
        if proposal_at is None or assignment_at is None:
            continue
        age_s = (assignment_at - proposal_at).total_seconds()
        if age_s < 0:
            negative_age += 1
        else:
            ages.append(age_s)

    agreement_yes = sum(agreements)
    fresh = sum(age <= PROPOSAL_FRESHNESS_S for age in ages)
    return {
        "input": {
            "parsed_dict_records": stats["parsed_dict_records"],
            "wrong_schema": stats["wrong_schema"],
            "before_since": stats["before_since"],
            "missing_timestamp": stats["missing_timestamp"],
            "duplicates": stats["duplicates"],
        },
        "n": len(selected),
        "agreement": {
            "evaluable": len(agreements),
            "agree": agreement_yes,
            "disagree": len(agreements) - agreement_yes,
            "rate": (
                round(agreement_yes / len(agreements), 6) if agreements else None
            ),
            "n/d": len(selected) - len(agreements),
        },
        "proposal_age_seconds": {
            "evaluable": len(ages),
            "n/d": len(selected) - len(ages) - negative_age,
            "negative_invalid": negative_age,
            "min": round(min(ages), 3) if ages else None,
            "p50_nearest_rank": _nearest_rank(ages, 0.50),
            "p80_nearest_rank": _nearest_rank(ages, 0.80),
            "p90_nearest_rank": _nearest_rank(ages, 0.90),
            "p95_nearest_rank": _nearest_rank(ages, 0.95),
            "max": round(max(ages), 3) if ages else None,
            "freshness_threshold_s": PROPOSAL_FRESHNESS_S,
            "within_threshold": fresh,
            "over_threshold": len(ages) - fresh,
            "within_rate": round(fresh / len(ages), 6) if ages else None,
        },
        "t3": {
            "required_n": T3_MIN_N,
            "observed_n": len(selected),
            "n_condition_met": len(selected) >= T3_MIN_N,
        },
    }


def _local_timestamp(
    node: Mapping[str, Any], inherited: datetime | None
) -> datetime | None:
    return _record_dt(
        node,
        (
            "decision_ts",
            "emitted_at",
            "timestamp",
            "ts",
            "created_at",
            "recorded_at",
        ),
    ) or inherited


def _local_order_ids(
    node: Mapping[str, Any], inherited: tuple[str, ...]
) -> tuple[str, ...]:
    for key in ("order_id", "oid"):
        value = node.get(key)
        if value not in (None, ""):
            return (str(value),)
    values = node.get("order_ids")
    if isinstance(values, list):
        result = tuple(str(value) for value in values if value not in (None, ""))
        if result:
            return result
    return inherited


def _local_courier(node: Mapping[str, Any], inherited: str | None) -> str | None:
    for key in ("courier_id", "cid", "winner_cid"):
        value = node.get(key)
        if value not in (None, ""):
            return str(value)
    return inherited


def _eta_observations(
    node: object,
    *,
    inherited_orders: tuple[str, ...] = (),
    inherited_courier: str | None = None,
    inherited_ts: datetime | None = None,
    path: tuple[str, ...] = (),
) -> Iterator[tuple[str | None, str | None, datetime | None, str, bool]]:
    if isinstance(node, Mapping):
        orders = _local_order_ids(node, inherited_orders)
        courier = _local_courier(node, inherited_courier)
        event_ts = _local_timestamp(node, inherited_ts)

        source_value: object = None
        source_key = ""
        if "eta_source" in node:
            source_value = node.get("eta_source")
            source_key = "eta_source"
        elif path and "decision_eta" in path[-1].lower() and "source" in node:
            source_value = node.get("source")
            source_key = "source"
        if isinstance(source_value, str):
            source = source_value.strip().lower()
            canonical = source in ETA_SOURCES
            emitted_orders: tuple[str | None, ...] = orders or (None,)
            for order_id in emitted_orders:
                yield order_id, courier, event_ts, source, canonical

        for key in sorted(node):
            if key == source_key:
                continue
            yield from _eta_observations(
                node[key],
                inherited_orders=orders,
                inherited_courier=courier,
                inherited_ts=event_ts,
                path=path + (str(key),),
            )
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _eta_observations(
                value,
                inherited_orders=inherited_orders,
                inherited_courier=inherited_courier,
                inherited_ts=inherited_ts,
                path=path + (str(index),),
            )


def analyze_eta_sources(
    records: Iterable[Mapping[str, Any]], since: datetime
) -> dict[str, Any]:
    observations: list[tuple[str | None, str | None, datetime | None, str]] = []
    seen: set[tuple[object, ...]] = set()
    stats = Counter()
    for record in records:
        if not isinstance(record, Mapping):
            continue
        stats["parsed_dict_records"] += 1
        record_time = _record_dt(
            record,
            (
                "decision_ts",
                "emitted_at",
                "timestamp",
                "ts",
                "created_at",
                "recorded_at",
            ),
        )
        if record_time is None:
            stats["missing_record_timestamp"] += 1
            continue
        if record_time < since:
            stats["before_since"] += 1
            continue
        stats["records_in_window"] += 1
        for oid, cid, event_ts, source, canonical in _eta_observations(
            record, inherited_ts=record_time
        ):
            key = (
                oid,
                cid,
                event_ts.isoformat() if event_ts else None,
                source,
            )
            if key in seen:
                continue
            seen.add(key)
            stats["eta_source_fields"] += 1
            if canonical:
                observations.append((oid, cid, event_ts, source))
            else:
                stats["other_source_values"] += 1

    counts = Counter(source for _, _, _, source in observations)
    # Switch per order jest policzalny tylko, gdy każdy timestamp tego orderu
    # ma jednoznaczne źródło. Równoległe różne źródła kandydatów = N/D.
    timeline: dict[str, dict[datetime, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    without_order = 0
    without_ts = 0
    for oid, _cid, event_ts, source in observations:
        if oid is None:
            without_order += 1
        elif event_ts is None:
            without_ts += 1
        else:
            timeline[oid][event_ts].add(source)

    switch_counts: list[int] = []
    ambiguous_orders = 0
    for oid in sorted(timeline):
        points = timeline[oid]
        if any(len(sources) != 1 for sources in points.values()):
            ambiguous_orders += 1
            continue
        ordered_sources = [
            next(iter(points[event_ts])) for event_ts in sorted(points)
        ]
        switches = sum(
            left != right
            for left, right in zip(ordered_sources, ordered_sources[1:])
        )
        switch_counts.append(switches)

    status = "OK" if switch_counts else "N/D"
    reason = None
    if status == "N/D":
        reason = (
            "brak co najmniej jednej jednoznacznej, czasowej serii "
            "LIVE/WARM/PLANNED z order_id"
        )
    return {
        "input": {
            "parsed_dict_records": stats["parsed_dict_records"],
            "records_in_window": stats["records_in_window"],
            "before_since": stats["before_since"],
            "missing_record_timestamp": stats["missing_record_timestamp"],
            "eta_source_fields": stats["eta_source_fields"],
            "other_source_values": stats["other_source_values"],
        },
        "counts": {source.upper(): counts[source] for source in ETA_SOURCES},
        "canonical_observations": len(observations),
        "switches_per_order": {
            "status": status,
            "reason": reason,
            "evaluable_orders": len(switch_counts),
            "ambiguous_orders": ambiguous_orders,
            "observations_without_order": without_order,
            "observations_without_timestamp": without_ts,
            "orders_with_any_switch": sum(value > 0 for value in switch_counts),
            "total_switches": sum(switch_counts),
            "max_switches_one_order": max(switch_counts) if switch_counts else None,
        },
    }


def _pct(numerator: int, denominator: int) -> str:
    return "N/D" if not denominator else f"{100.0 * numerator / denominator:.1f}%"


def _fmt(value: object) -> str:
    if value is None:
        return "N/D"
    if isinstance(value, bool):
        return "TAK" if value else "NIE"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _recommendations(ledger: Mapping[str, Any]) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for row in ledger["grid"]:
        cap35 = row["caps"]["35"]
        cap40 = row["caps"]["40"]
        notes.append(
            {
                "tol_min": row["tol_min"],
                "gain_min": row["gain_min"],
                "analytical_note": (
                    "cap35 "
                    f"strict={cap35['strict_w']['survive']}/"
                    f"{cap35['strict_w']['die']}/{cap35['strict_w']['n/d']}, "
                    f"equal={cap35['equal_w']['survive']}/"
                    f"{cap35['equal_w']['die']}/{cap35['equal_w']['n/d']}, "
                    f"oracle={cap35['oracle_492']['killed']}/"
                    f"{cap35['oracle_492']['n']}; cap40 "
                    f"strict={cap40['strict_w']['survive']}/"
                    f"{cap40['strict_w']['die']}/{cap40['strict_w']['n/d']}, "
                    f"equal={cap40['equal_w']['survive']}/"
                    f"{cap40['equal_w']['die']}/{cap40['equal_w']['n/d']}, "
                    f"oracle={cap40['oracle_492']['killed']}/"
                    f"{cap40['oracle_492']['n']}"
                ),
                "business_verdict": "NIE WYDAJE — build analityczny",
            }
        )
    return notes


def _gaps(
    ledger: Mapping[str, Any],
    r2: Mapping[str, Any],
    eta: Mapping[str, Any],
) -> list[str]:
    gaps: list[str] = []
    for name, cohort in ledger["cohorts"].items():
        if cohort["status"] != "OK":
            gaps.append(f"Kohorta {name}: {cohort['reason']}")
        elif cohort["unclassified"]:
            gaps.append(
                f"Kohorta {name}: {cohort['unclassified']} rekordów bez klasyfikacji"
            )
    if ledger["cap"]["n/d"]:
        gaps.append(
            "Cap dwell: "
            f"{ledger['cap']['n/d']} strict-W bez kompletnego items/bag.carried"
        )
    if r2["proposal_age_seconds"]["n/d"] or r2["proposal_age_seconds"][
        "negative_invalid"
    ]:
        gaps.append(
            "R2 age: "
            f"{r2['proposal_age_seconds']['n/d']} N/D, "
            f"{r2['proposal_age_seconds']['negative_invalid']} ujemnych"
        )
    if eta["switches_per_order"]["status"] != "OK":
        gaps.append("ETA switches: " + str(eta["switches_per_order"]["reason"]))
    if not gaps:
        gaps.append("Brak wykrytych luk w wymaganych agregatach.")
    return gaps


def render_markdown(report: Mapping[str, Any]) -> str:
    ledger = report["ledger_v2"]
    r2 = report["r2_assignment_episodes"]
    eta = report["eta_sources"]
    lines = [
        "# S34 — pakiet progów (build analityczny, bez werdyktu)",
        "",
        f"- Timestamp operatora: `{report['timestamp']}`",
        f"- Odcięcie danych: `{report['since_utc']}`",
        "- Tryb: read-only wobec ledgerów; raport zawiera wyłącznie agregaty.",
        "- Ważne: carry v2 jest symulacją od źródła possession, nie dowodem "
        "fizycznego handoffu R6.",
        "",
        "## 1. Ledger v2 i deduplikacja",
        "",
        "| miara | wartość |",
        "|---|---:|",
    ]
    for key, value in ledger["input"].items():
        lines.append(f"| {key} | {_fmt(value)} |")
    lines.extend(
        [
            "",
            "Podpis dedup obejmuje treść decyzji z pominięciem koperty "
            "`ts/emitted_at`, identyfikatorów run/attempt/decision, PID i coverage. "
            "Semantyczne czasy loadgov pozostają w podpisie.",
            "",
            "### Klasy W",
            "",
            "| strict-W | equal-W | W-regression | N/D | oracle-492 |",
            "|---:|---:|---:|---:|---:|",
            (
                f"| {ledger['classes']['strict_w']} | "
                f"{ledger['classes']['equal_w']} | "
                f"{ledger['classes']['w_regression']} | "
                f"{ledger['classes']['n/d']} | "
                f"{ledger['oracle_492_class_n']} |"
            ),
            "",
            "Identity decisions są wyłączone z siatki: guard nie wybiera w nich "
            "kandydata. Każda komórka przechodzi przez kanoniczny enforcement "
            "osobno dla cap=35 i cap=40.",
            "",
            "## 2. Siatka TOL × GAIN",
            "",
            "| TOL | GAIN | strict c35 p/g/N-D | equal c35 p/g/N-D | "
            "strict c40 p/g/N-D | equal c40 p/g/N-D | oracle c35/c40 zabite/n |",
            "|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in ledger["grid"]:
        cap35 = row["caps"]["35"]
        cap40 = row["caps"]["40"]
        lines.append(
            f"| {_fmt(row['tol_min'])} | {_fmt(row['gain_min'])} | "
            f"{cap35['strict_w']['survive']}/{cap35['strict_w']['die']}/"
            f"{cap35['strict_w']['n/d']} | "
            f"{cap35['equal_w']['survive']}/{cap35['equal_w']['die']}/"
            f"{cap35['equal_w']['n/d']} | "
            f"{cap40['strict_w']['survive']}/{cap40['strict_w']['die']}/"
            f"{cap40['strict_w']['n/d']} | "
            f"{cap40['equal_w']['survive']}/{cap40['equal_w']['die']}/"
            f"{cap40['equal_w']['n/d']} | "
            f"{cap35['oracle_492']['killed']}/{cap35['oracle_492']['n']} / "
            f"{cap40['oracle_492']['killed']}/{cap40['oracle_492']['n']} |"
        )

    cap = ledger["cap"]
    lines.extend(
        [
            "",
            "## 3. Cap 35 vs 40 — strict-W, carry po dwell",
            "",
            f"Evaluable: **{cap['evaluable']}/{cap['strict_w_total']}**, "
            f"N/D: **{cap['n/d']}**.",
            "",
            "| cap | przeżywa | ginie | N/D | ginie / evaluable |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    for cap_key in ("35", "40"):
        row = cap["caps"][cap_key]
        lines.append(
            f"| {cap_key} | {row['survive']} | {row['die']} | {row['n/d']} | "
            f"{_pct(row['die'], cap['evaluable'])} |"
        )
    lines.extend(
        [
            "",
            "Histogram maksymalnego chosen carry po dwell:",
            "",
            "| przedział min | n |",
            "|---|---:|",
        ]
    )
    for label, count in cap["chosen_post_dwell_histogram_min"].items():
        lines.append(f"| {label} | {count} |")

    lines.extend(["", "## 4. Kohorty", ""])
    for name, cohort in ledger["cohorts"].items():
        lines.append(f"### {name.upper()}")
        lines.append("")
        lines.append(
            f"Status: **{cohort['status']}**; classified={cohort['classified']}, "
            f"unclassified={cohort['unclassified']}."
        )
        if cohort["reason"]:
            lines.append(f"Powód: {cohort['reason']}.")
        lines.append("")
        if cohort["status"] == "OK":
            lines.extend(
                [
                    "| grupa | n | TOL | GAIN | strict survive/die/N-D | "
                    "equal survive/die/N-D | cap | oracle killed/n |",
                    "|---|---:|---:|---:|---:|---:|---:|---:|",
                ]
            )
            for group_name, group in cohort["groups"].items():
                for row in group["grid"]:
                    for cap_key in ("35", "40"):
                        cap_row = row["caps"][cap_key]
                        strict = cap_row["strict_w"]
                        equal = cap_row["equal_w"]
                        oracle = cap_row["oracle_492"]
                        lines.append(
                            f"| {group_name} | {group['n']} | "
                            f"{_fmt(row['tol_min'])} | {_fmt(row['gain_min'])} | "
                            f"{strict['survive']}/{strict['die']}/{strict['n/d']} | "
                            f"{equal['survive']}/{equal['die']}/{equal['n/d']} | "
                            f"{cap_key} | {oracle['killed']}/{oracle['n']} |"
                        )
            lines.append("")

    agreement = r2["agreement"]
    age = r2["proposal_age_seconds"]
    lines.extend(
        [
            "## 5. R2 assignment episodes",
            "",
            f"- n={r2['n']}; T3 n≥{r2['t3']['required_n']}: "
            f"**{_fmt(r2['t3']['n_condition_met'])}**.",
            f"- Agreement: {agreement['agree']}/{agreement['evaluable']} "
            f"({_fmt(agreement['rate'])}); disagree={agreement['disagree']}; "
            f"N/D={agreement['n/d']}.",
            f"- Proposal age [s], nearest-rank: min={_fmt(age['min'])}, "
            f"p50={_fmt(age['p50_nearest_rank'])}, "
            f"p80={_fmt(age['p80_nearest_rank'])}, "
            f"p90={_fmt(age['p90_nearest_rank'])}, "
            f"p95={_fmt(age['p95_nearest_rank'])}, max={_fmt(age['max'])}.",
            f"- Fresh ≤{_fmt(age['freshness_threshold_s'])} s: "
            f"{age['within_threshold']}/{age['evaluable']} "
            f"({_fmt(age['within_rate'])}); over={age['over_threshold']}.",
            "",
            "## 6. Źródła ETA",
            "",
            "| LIVE | WARM | PLANNED | inne wartości eta_source |",
            "|---:|---:|---:|---:|",
            f"| {eta['counts']['LIVE']} | {eta['counts']['WARM']} | "
            f"{eta['counts']['PLANNED']} | "
            f"{eta['input']['other_source_values']} |",
            "",
            f"Switches/order: **{eta['switches_per_order']['status']}**; "
            f"evaluable_orders={eta['switches_per_order']['evaluable_orders']}, "
            f"orders_with_switch={eta['switches_per_order']['orders_with_any_switch']}, "
            f"total_switches={eta['switches_per_order']['total_switches']}.",
        ]
    )
    if eta["switches_per_order"]["reason"]:
        lines.append("Powód N/D: " + eta["switches_per_order"]["reason"] + ".")

    lines.extend(
        [
            "",
            "## 7. REKOMENDACJE — noty analityczne, bez werdyktu",
            "",
            "Każdy wiersz opisuje wyłącznie wynik na obserwowanym korpusie. Nie jest "
            "zgodą na zmianę progu, flagi ani produkcji.",
            "",
            "| TOL | GAIN | liczby |",
            "|---:|---:|---|",
        ]
    )
    for note in report["recommendations"]:
        lines.append(
            f"| {_fmt(note['tol_min'])} | {_fmt(note['gain_min'])} | "
            f"{note['analytical_note']} |"
        )
    lines.extend(
        [
            "",
            "## 8. LUKI — reimplementacja strict/equal-W usunięta",
            "",
        ]
    )
    lines.extend(f"- {gap}" for gap in report["gaps"])
    lines.extend(
        [
            "",
            "## 9. Ograniczenia metody",
            "",
            "- `raw_carry_min + dwell_min` jest uczciwym post-dwell wynikiem "
            "symulatora v2, ale possession_source może nadal być proxy; to nie "
            "awansuje wyniku do fizycznej prawdy R6.",
            "- Każda komórka gridu TOL/GAIN dla cap=35 i cap=40 woła "
            "`core.lex_window_guards.evaluate`; raport nie ma własnej kopii "
            "reguły capa ani wyjątków strict/equal-W.",
            "- GPS/no-GPS i Alarm są klasyfikowane tylko z jawnych pól. "
            "Brak pola daje N/D; loadgov EWMA nie jest certyfikatem Alarmu.",
            "- ETA liczy wyłącznie literalne LIVE/WARM/PLANNED. Starsze "
            "`haversine/plan/...` pozostają w kategorii inne.",
            "- Ledger v2 zawiera zwycięzcę i tylko skrót top-K bez pełnych "
            "`items` per odrzucony kandydat. Siatka i cap są counterfactualem "
            "na obserwowanych zwycięzcach, nie pełnym replayem permutacji; "
            "istnieje selection bias obecnego guarda.",
            "- Wejścia są żywymi append-only JSONL, nie snapshotem. Zmiana "
            "pliku/rotacji podczas runu jest wykrywana przez size+mtime i "
            "raportowana w LUKACH.",
            "- `--since` bez offsetu jest interpretowane jako UTC.",
            "",
        ]
    )
    return "\n".join(lines)


def _atomic_write(path: Path, data: bytes) -> None:
    if not path.parent.is_dir():
        raise FileNotFoundError(
            f"katalog raportu nie istnieje (skrypt go nie tworzy): {path.parent}"
        )
    fd, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    since = parse_dt(args.since)
    if since is None:
        raise ValueError("--since musi być poprawnym ISO-8601")
    if "\n" in args.timestamp or "\r" in args.timestamp or not args.timestamp.strip():
        raise ValueError("--timestamp musi być niepustą pojedynczą linią")

    ledger_path = Path(args.ledger)
    episodes_path = Path(args.episodes)
    shadow_path = Path(args.shadow)
    out_path = Path(args.out)
    json_path = out_path.with_suffix(".json")
    if out_path.suffix.lower() != ".md":
        raise ValueError("--out musi kończyć się .md; JSON powstaje obok")
    resolved_inputs = {
        path.resolve(strict=False)
        for path in (ledger_path, episodes_path, shadow_path)
    }
    if out_path.resolve(strict=False) in resolved_inputs:
        raise ValueError("--out nie może wskazywać pliku wejściowego")
    if json_path.resolve(strict=False) in resolved_inputs:
        raise ValueError("sąsiedni JSON nie może wskazywać pliku wejściowego")

    before_states = {
        "ledger_v2": _source_state(ledger_path, since),
        "assignment_episodes": _source_state(episodes_path, since),
        "shadow_decisions": _source_state(shadow_path, since),
    }
    ledger_records = list(_iter_records(ledger_path, since))
    episode_records = list(_iter_records(episodes_path, since))
    shadow_records = list(_iter_records(shadow_path, since))
    ledger = analyze_ledger_records(ledger_records, since)
    r2 = analyze_assignment_episodes(episode_records, since)
    eta = analyze_eta_sources(shadow_records, since)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "timestamp": args.timestamp.strip(),
        "since_utc": since.isoformat(),
        "mode": "build_read_only_no_verdict",
        "inputs": {
            "ledger_v2": _source_info(
                ledger_path, since, before_states["ledger_v2"]
            ),
            "assignment_episodes": _source_info(
                episodes_path, since, before_states["assignment_episodes"]
            ),
            "shadow_decisions": _source_info(
                shadow_path, since, before_states["shadow_decisions"]
            ),
            "freeze_reference": str(Path(args.freeze)),
        },
        "method": {
            "grid": (
                "core.lex_window_guards.evaluate via fail-closed ledger-v2 adapter"
            ),
            "grid_caps_min": [35.0, 40.0],
            "grid_cap_separated": True,
            "oracle_492": "equal-W and delivery_delta>=4 and drive_gain<3",
            "proposal_freshness_s": PROPOSAL_FRESHNESS_S,
            "t3_min_n": T3_MIN_N,
            "percentiles": "nearest-rank",
        },
        "ledger_v2": ledger,
        "r2_assignment_episodes": r2,
        "eta_sources": eta,
    }
    report["recommendations"] = _recommendations(ledger)
    report["gaps"] = _gaps(ledger, r2, eta)
    for source_name, source in report["inputs"].items():
        if (
            isinstance(source, Mapping)
            and source.get("changed_during_read_or_hash") is True
        ):
            report["gaps"].append(
                f"Wejście {source_name} zmieniło się podczas odczytu/hashowania; "
                "wynik nie jest korpusem zamrożonym"
            )
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="S3/S4: read-only pakiet progów z ledgerów v2"
    )
    parser.add_argument("--since", required=True, help="ISO-8601; brak offsetu = UTC")
    parser.add_argument(
        "--timestamp",
        required=True,
        help="deterministyczny znacznik raportu podany przez CTO",
    )
    parser.add_argument("--out", required=True, help="docelowy raport .md; JSON obok")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--episodes", default=str(DEFAULT_EPISODES))
    parser.add_argument("--shadow", default=str(DEFAULT_SHADOW))
    parser.add_argument("--freeze", default=str(DEFAULT_FREEZE))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    out_path = Path(args.out)
    json_path = out_path.with_suffix(".json")
    markdown = render_markdown(report).encode("utf-8")
    json_data = (
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    _atomic_write(out_path, markdown)
    _atomic_write(json_path, json_data)
    print(str(out_path))
    print(str(json_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
