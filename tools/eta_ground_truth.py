#!/usr/bin/env python3
"""Kanoniczny, wersjonowany dataset/report prawdy ETA (Z-P1-02, Faza A).

Narzędzie jest wyłącznie obserwacyjne. Rozdziela:

* predykcję sprzed decyzji operatora,
* proxy przyciskowe pickup/delivery,
* przyjazd oraz ostatni punkt GPS wewnątrz geofence restauracji,
* przyjazd GPS pod adres dostawy.

Pole wejściowe ``departed_restaurant`` nie potwierdza wyjazdu ani fizycznego
pickupu: writer zapisuje w nim ostatni punkt wewnątrz geofence znaleziony w
oknie wokół kliknięcia. Analogicznie geofence dostawy potwierdza przyjazd pod
adres, nie przekazanie przesyłki klientowi.

Nie dobiera progu biznesowego i nie zapisuje map ETA. Każdy raport ma jawne
okno ``[start, end)``, jeden bazowy mianownik oraz lineage wejść. Output nie
zawiera surowych order/courier id, nazw, adresów ani koordynatów.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

try:
    from dispatch_v2 import common as dispatch_common
    from dispatch_v2.tools import _rotated_logs, ledger_io
except ImportError:  # pragma: no cover - standalone z katalogu tools/
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from dispatch_v2 import common as dispatch_common
    from dispatch_v2.tools import _rotated_logs, ledger_io


UTC = timezone.utc
WARSAW = ZoneInfo("Europe/Warsaw")
DATASET_SCHEMA = "eta_truth.dataset.v1"
MANIFEST_SCHEMA = "eta_truth.manifest.v1"
REPORT_SCHEMA = "eta_truth.report.v1"

# Tylko te akcje timerowego decision_outcomes są dowodem faktycznej decyzji
# przypisania. TIMEOUT_SUPERSEDED/no_verdict nie mogą kotwiczyć predykcji.
ASSIGNMENT_ACTIONS = frozenset({
    "PANEL_OVERRIDE",
    "PANEL_AGREE",
    "ASSIGN_DIRECT",
    "F7AGREE",
})

DEFAULT_RESTAURANT_DWELL = "/root/.openclaw/workspace/dispatch_state/restaurant_dwell.json"
DEFAULT_COURIER_TRUTH = "/root/.openclaw/workspace/dispatch_state/courier_ground_truth.json"
RUNTIME_OUTPUT_FORBIDDEN_ROOTS = (
    "/root/.openclaw/workspace/dispatch_state",
    "/root/.openclaw/workspace/scripts/logs",
)
BEHAVIOR_DEPENDENCIES = (
    "common.py",
    "tools/_rotated_logs.py",
    "tools/ledger_io.py",
)


class ContractError(ValueError):
    """Wejście łamie kontrakt i nie wolno go cicho sklasyfikować jako dane."""


def _json_default(value):
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"nieobsługiwany typ JSON: {type(value).__name__}")


def canonical_json(value) -> str:
    """Stabilna reprezentacja używana przez fingerprinty i JSONL."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_records(records) -> str:
    if isinstance(records, Mapping):
        payload = canonical_json(records)
    else:
        payload = "\n".join(sorted(canonical_json(r) for r in records))
    return _sha256_text(payload)


def parse_timestamp(value, *, naive_policy: str = "reject") -> Optional[datetime]:
    """Parsuje timestamp do UTC; polityka naive jest zawsze jawna.

    ``reject`` jest kanonem dla predykcji i zdarzeń outcome. ``warsaw`` służy
    wyłącznie źródłom, których writer ma zatwierdzony kontrakt naive-Warsaw.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), UTC)
        except (ValueError, OverflowError, OSError) as exc:
            raise ContractError(f"niepoprawny epoch: {value!r}") from exc
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ContractError(f"niepoprawny timestamp: {value!r}") from exc
    if dt.tzinfo is None:
        if naive_policy == "reject":
            raise ContractError(f"timestamp bez strefy: {value!r}")
        if naive_policy == "warsaw":
            dt = dt.replace(tzinfo=WARSAW)
        elif naive_policy == "utc":
            dt = dt.replace(tzinfo=UTC)
        else:
            raise ContractError(f"nieznana polityka naive: {naive_policy}")
    return dt.astimezone(UTC)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _minutes(later: Optional[datetime], earlier: Optional[datetime]) -> Optional[float]:
    if later is None or earlier is None:
        return None
    return round((later - earlier).total_seconds() / 60.0, 6)


def _oid(record: Mapping) -> Optional[str]:
    value = record.get("order_id")
    if value is None:
        value = record.get("oid")
    return None if value is None else str(value).strip()


def _cid(value) -> Optional[str]:
    return None if value is None or str(value).strip() == "" else str(value).strip()


def _map_value(mapping, oid: str):
    if not isinstance(mapping, Mapping):
        return None
    if oid in mapping:
        return mapping[oid]
    # Historyczne rekordy sporadycznie mają klucze int.
    for key, value in mapping.items():
        if str(key) == oid:
            return value
    return None


def _record_time(record: Mapping, fields: Sequence[str], *, naive_policy="reject"):
    for field in fields:
        if record.get(field) not in (None, ""):
            return parse_timestamp(record[field], naive_policy=naive_policy)
    return None


def _latest_by_oid(records: Iterable[Mapping], fields: Sequence[str], *, source: str,
                   naive_policy="reject") -> dict[str, dict]:
    out: dict[str, tuple[tuple, dict]] = {}
    floor = datetime.min.replace(tzinfo=UTC)
    for raw in records:
        if not isinstance(raw, Mapping):
            raise ContractError(f"{source}: rekord nie jest obiektem")
        record = dict(raw)
        oid = _oid(record)
        if not oid:
            continue
        ts = _record_time(record, fields, naive_policy=naive_policy)
        key = (ts or floor, canonical_json(record))
        if oid not in out or key > out[oid][0]:
            out[oid] = (key, record)
    return {oid: pair[1] for oid, pair in out.items()}


def _records_available_as_of(
    records: Sequence[Mapping],
    fields: Sequence[str],
    *,
    as_of: datetime,
    naive_policy: str,
) -> tuple[list[dict], int]:
    """Odrzuca rekordy, których jawny czas dostępności jest po ``as_of``.

    Brak wszystkich pól nie jest tu dopowiadany: rekord przechodzi dalej, a
    wyspecjalizowany kontrakt zdarzenia oznaczy brak kotwicy. Dzięki temu
    filtr nie zamienia braku metadanych w wymyślony czas powstania.
    """
    available: list[dict] = []
    filtered = 0
    for record in records:
        ts = _record_time(record, fields, naive_policy=naive_policy)
        if ts is not None and ts > as_of:
            filtered += 1
            continue
        available.append(dict(record))
    return available, filtered


def _outcome_schema(records: Sequence[Mapping]) -> str:
    schemas = set()
    for record in records:
        if "order_id" in record:
            schemas.add("decision_outcomes.timer.v1")
        elif "oid" in record:
            schemas.add("decision_outcome_join.legacy")
        else:
            schemas.add("unknown")
    if len(schemas) > 1:
        raise ContractError("mieszany schemat decision_outcomes: " + ",".join(sorted(schemas)))
    schema = next(iter(schemas), "empty")
    if schema == "unknown":
        raise ContractError("nieznany schemat decision_outcomes")
    return schema


def _shadow_index(records: Iterable[Mapping]) -> dict[str, list[tuple[datetime, dict]]]:
    out: dict[str, list[tuple[datetime, dict]]] = defaultdict(list)
    for raw in records:
        if not isinstance(raw, Mapping):
            raise ContractError("shadow: rekord nie jest obiektem")
        record = dict(raw)
        oid = _oid(record)
        if not oid:
            continue
        ts = parse_timestamp(record.get("ts"), naive_policy="reject")
        if ts is None:
            continue
        out[oid].append((ts, record))
    for oid in out:
        out[oid].sort(key=lambda pair: (pair[0], canonical_json(pair[1])))
    return dict(out)


def _candidates(record: Mapping) -> list[dict]:
    result = []
    best = record.get("best")
    if isinstance(best, Mapping) and best:
        result.append(dict(best))
    for candidate in record.get("alternatives") or []:
        if isinstance(candidate, Mapping):
            result.append(dict(candidate))
    return result


def _select_preassignment(shadow_rows: Sequence[tuple[datetime, dict]], assignment_at: datetime,
                          actual_cid: str):
    """Jedyny anchor v1: najnowszy rekord istniejący PRZED decyzją operatora.

    Najpierw wybieramy rekord po czasie, dopiero potem kandydata. Nie skanujemy
    wstecz za rekordem zawierającym zrealizowanego kuriera i nie używamy planu
    po przypisaniu.
    """
    eligible = [pair for pair in shadow_rows if pair[0] <= assignment_at]
    if not eligible:
        return None, None, "no_shadow_before_assignment"
    ts, record = eligible[-1]
    for candidate in _candidates(record):
        if _cid(candidate.get("courier_id")) == actual_cid:
            return (ts, record), candidate, None
    return (ts, record), None, "actual_courier_absent_preassignment"


def _geofence_confidence(entry: Mapping) -> str:
    try:
        n_in = int(entry.get("_n_in_geofence") or entry.get("n_in_geofence") or 0)
    except (TypeError, ValueError):
        n_in = 0
    min_dist = entry.get("_min_dist_m", entry.get("min_dist_m"))
    radius = entry.get("_radius_m", entry.get("radius_m"))
    inside = True
    if isinstance(min_dist, (int, float)) and isinstance(radius, (int, float)):
        inside = min_dist <= radius
    return "high" if n_in >= 2 and inside else "low"


def _courier_matches(entry: Mapping, actual_cid: Optional[str]) -> bool:
    source_cid = _cid(entry.get("courier_id"))
    # Order-keyed mapa nie wystarcza do atrybucji GPS po reassignie. Writerzy
    # obu źródeł zapisują courier_id; brak którejkolwiek strony jest więc
    # brakiem dowodu, a nie zgodnością przez domniemanie.
    return source_cid is not None and actual_cid is not None and source_cid == actual_cid


def _mapping_entry_after_as_of(
    entry: Optional[Mapping],
    fields: Sequence[str],
    *,
    as_of: datetime,
    naive_policy: str,
) -> bool:
    if not isinstance(entry, Mapping):
        return False
    computed_at = _record_time(entry, fields, naive_policy=naive_policy)
    return computed_at is not None and computed_at > as_of


def _restaurant_geofence_observables(
    entry: Optional[Mapping], actual_cid: Optional[str], reasons: list[str]
):
    if not isinstance(entry, Mapping):
        reasons.append("restaurant_geofence_observable_missing")
        return None, None, None, None
    if not _courier_matches(entry, actual_cid):
        reasons.append("restaurant_geofence_courier_mismatch")
        return None, None, None, None
    # Faktyczny writer: panel/backend/tools/restaurant_dwell_detector.py.
    if entry.get("_source") != "gps_geofence":
        reasons.append("restaurant_geofence_source_unsupported")
        return None, None, None, None
    confidence = _geofence_confidence(entry)
    if confidence != "high":
        reasons.append("restaurant_geofence_low_confidence")
        return None, None, None, confidence
    arrival = parse_timestamp(entry.get("arrived_at_restaurant"), naive_policy="warsaw")
    # Nazwa pola writera jest historyczna. Jego algorytm zapisuje ostatni punkt
    # pozostający WEWNĄTRZ geofence, nie potwierdzone przecięcie granicy na
    # zewnątrz. Kontrakt outputu celowo nie nazywa tego departure/pickup.
    last_inside = parse_timestamp(entry.get("departed_restaurant"), naive_policy="warsaw")
    if arrival is None:
        reasons.append("restaurant_arrival_missing")
    if last_inside is None:
        reasons.append("restaurant_last_inside_missing")
    return last_inside, arrival, "gps_geofence", confidence


def _delivery_arrival_observable(
    app_entry: Optional[Mapping],
    server_entry: Optional[Mapping],
    actual_cid: Optional[str],
    as_of: datetime,
    reasons: list[str],
):
    if isinstance(app_entry, Mapping) and app_entry.get("gps_arrived_at") not in (None, ""):
        if not _courier_matches(app_entry, actual_cid):
            reasons.append("app_delivery_courier_mismatch")
        elif app_entry.get("gps_arrival_source") == "app_geofence":
            ts = parse_timestamp(app_entry.get("gps_arrived_at"), naive_policy="reject")
            if ts is not None and ts <= as_of:
                return ts, "app_geofence_arrival", "high"
            reasons.append("app_delivery_arrival_after_as_of")
        else:
            reasons.append("app_delivery_non_geofence")
    if isinstance(server_entry, Mapping) and server_entry.get("physical_delivered_at"):
        if not _courier_matches(server_entry, actual_cid):
            reasons.append("server_delivery_courier_mismatch")
        elif server_entry.get("confidence") == "high":
            ts = parse_timestamp(server_entry.get("physical_delivered_at"), naive_policy="reject")
            if ts is not None and ts <= as_of:
                return ts, "server_geofence_arrival", "high"
            reasons.append("server_delivery_arrival_after_as_of")
        else:
            reasons.append("server_delivery_low_confidence")
    reasons.append("delivery_arrival_observable_missing")
    return None, None, None


def _package_classification(record: Mapping) -> str:
    """Klasyfikacja wyłącznie kanonem ``common.is_paczka_order``.

    `False` z kanonu jest fail-safe także dla braku/corrupt address_id. Do
    pomiaru coverage rozróżniamy więc jawny poprawny address_id (znane
    non-paczka) od braku/corrupt (unknown), bez zgadywania po order_type.
    """
    aid = record.get("address_id")
    try:
        int(aid)
    except (TypeError, ValueError):
        return "unknown"
    if dispatch_common.is_paczka_order(dict(record)):
        return "paczka"
    return "non_paczka"


def _package_record(
    sla_record: Mapping,
    shadow_rows: Sequence[tuple[datetime, dict]],
    assignment_at: Optional[datetime],
) -> tuple[dict, str]:
    """Uzupełnia tylko address_id z rekordu istniejącego przed assignment.

    Nie używa nazwy, order_type ani późniejszego rekordu shadow. Jeśli nie ma
    bezpiecznej kotwicy assignment, klasyfikacja pozostaje ``unknown``.
    """
    record = dict(sla_record)
    if _package_classification(record) != "unknown":
        return record, "sla"
    if assignment_at is None:
        return record, "unknown"
    eligible = [pair for pair in shadow_rows if pair[0] <= assignment_at]
    if not eligible:
        return record, "unknown"
    shadow = eligible[-1][1]
    candidate = {"address_id": shadow.get("address_id")}
    if _package_classification(candidate) == "unknown":
        return record, "unknown"
    record["address_id"] = candidate["address_id"]
    return record, "shadow_preassignment"


def _cohort_accepts(record: Mapping, cohort: str, *, apply_package_exclusion: bool) -> bool:
    value = record.get("was_czasowka")
    if cohort == "all_completed":
        accepted = True
    elif cohort == "non_czasowka":
        accepted = value is False
    elif cohort == "czasowka":
        accepted = value is True
    else:
        raise ContractError(f"nieznana kohorta: {cohort}")
    if not accepted:
        return False
    if apply_package_exclusion and _package_classification(record) == "paczka":
        return False
    return True


def _quantile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    vals = sorted(float(v) for v in values)
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def _support_hash(rows: Sequence[Mapping], base_cohort_hash: str) -> str:
    # Ordinal sam w sobie (`row_000001`) koliduje między datasetami o tym
    # samym rozmiarze. Wiążemy go z fingerprintem całej kohorty; nadal nie
    # emitujemy odwracalnego hasha pojedynczego order_id.
    tokens = sorted(str(row["row_id"]) for row in rows)
    return _sha256_text(canonical_json({
        "base_cohort_hash": base_cohort_hash,
        "member_ordinals": tokens,
    }))


def _metric_block(
    rows: Sequence[Mapping],
    field: str,
    n_base: int,
    base_cohort_hash: str,
) -> dict:
    support = [r for r in rows if isinstance(r.get(field), (int, float))]
    values = [float(r[field]) for r in support]
    if not values:
        return {
            "n": 0,
            "denominator_base": n_base,
            "coverage_pct": 0.0,
            "support_hash": _support_hash([], base_cohort_hash),
            "mean_bias_min": None,
            "median_bias_min": None,
            "mae_min": None,
            "p10_error_min": None,
            "p90_error_min": None,
            "min_error_min": None,
            "max_error_min": None,
        }
    return {
        "n": len(values),
        "denominator_base": n_base,
        "coverage_pct": round(100.0 * len(values) / n_base, 3) if n_base else 0.0,
        "support_hash": _support_hash(support, base_cohort_hash),
        "mean_bias_min": round(statistics.fmean(values), 3),
        "median_bias_min": round(statistics.median(values), 3),
        "mae_min": round(statistics.fmean(abs(v) for v in values), 3),
        "p10_error_min": round(_quantile(values, 0.1), 3),
        "p90_error_min": round(_quantile(values, 0.9), 3),
        "min_error_min": round(min(values), 3),
        "max_error_min": round(max(values), 3),
    }


def _behavior_content_fingerprint(content_hashes: Mapping[str, str]) -> str:
    return _sha256_text(canonical_json(dict(content_hashes)))


def _code_lineage() -> dict:
    module_path = Path(__file__).resolve()
    root = module_path.parents[1]
    relative_module = str(module_path.relative_to(root))
    tracked = (relative_module,) + BEHAVIOR_DEPENDENCIES
    content_hashes = {
        relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
        for relative in tracked
    }
    head = "unknown"
    statuses: dict[str, Optional[str]] = {relative: None for relative in tracked}
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        )
        head = result.stdout.strip()
        for relative in tracked:
            result = subprocess.run(
                ["git", "status", "--porcelain", "--", relative],
                cwd=root,
                text=True,
                capture_output=True,
                check=True,
                timeout=5,
            )
            statuses[relative] = result.stdout.strip()
    except Exception:
        pass
    status_known = all(value is not None for value in statuses.values())
    dirty = any(bool(value) for value in statuses.values() if value is not None)
    dependency_lineage = {
        relative: {
            "sha256": content_hashes[relative],
            "git_dirty": (
                None if statuses[relative] is None else bool(statuses[relative])
            ),
        }
        for relative in BEHAVIOR_DEPENDENCIES
    }
    return {
        "git_head": head,
        "module_path": relative_module,
        "module_sha256": content_hashes[relative_module],
        "dependencies": dependency_lineage,
        "behavior_content_fingerprint": _behavior_content_fingerprint(content_hashes),
        "git_status_known": status_known,
        "git_dirty": dirty if status_known else None,
        "git_status_fingerprint": _sha256_text(canonical_json(statuses)),
    }


def build_dataset(*, sla_records: Iterable[Mapping], shadow_records: Iterable[Mapping],
                  outcome_records: Iterable[Mapping], restaurant_dwell: Mapping,
                  courier_ground_truth: Mapping, gps_delivery_records: Iterable[Mapping],
                  start: datetime, end: datetime, as_of: datetime,
                  cohort: str) -> tuple[list[dict], dict]:
    """Buduje dataset v1 oraz manifest z jednego, wspólnego mianownika."""
    start = parse_timestamp(start, naive_policy="reject")
    end = parse_timestamp(end, naive_policy="reject")
    as_of = parse_timestamp(as_of, naive_policy="reject")
    if start is None or end is None or as_of is None:
        raise ContractError("start/end/as_of są wymagane")
    if not start < end:
        raise ContractError("okno wymaga start < end")
    if as_of < end:
        raise ContractError("as_of nie może poprzedzać końca okna")

    sla_records = [dict(r) for r in sla_records]
    shadow_records = [dict(r) for r in shadow_records]
    outcome_records = [dict(r) for r in outcome_records]
    gps_delivery_records = [dict(r) for r in gps_delivery_records]
    restaurant_dwell = dict(restaurant_dwell or {})
    courier_ground_truth = dict(courier_ground_truth or {})

    sla_available, sla_after_as_of = _records_available_as_of(
        sla_records,
        ("logged_at", "delivered_at"),
        as_of=as_of,
        naive_policy="warsaw",
    )
    outcomes_available, outcomes_after_as_of = _records_available_as_of(
        outcome_records,
        ("written_at", "ts_decision", "picked_up_at"),
        as_of=as_of,
        naive_policy="reject",
    )
    gps_available, gps_after_as_of = _records_available_as_of(
        gps_delivery_records,
        (
            "_computed_at",
            "computed_at",
            "written_at",
            "physical_delivered_at",
            "button_delivered_at",
            "delivered_day",
        ),
        as_of=as_of,
        naive_policy="warsaw",
    )
    shadow_available, shadow_after_as_of = _records_available_as_of(
        shadow_records,
        ("ts",),
        as_of=as_of,
        naive_policy="reject",
    )
    outcome_schema = _outcome_schema(outcomes_available)
    sla_idx = _latest_by_oid(
        sla_available,
        ("logged_at", "delivered_at"),
        source="sla",
        naive_policy="warsaw",
    )
    outcome_idx = _latest_by_oid(
        outcomes_available,
        ("written_at", "ts_decision", "picked_up_at"),
        source="outcomes",
        naive_policy="reject",
    )
    gps_idx = _latest_by_oid(
        gps_available,
        (
            "_computed_at",
            "computed_at",
            "written_at",
            "button_delivered_at",
            "physical_delivered_at",
            "delivered_day",
        ),
        source="gps_delivery_truth",
        naive_policy="warsaw",
    )
    shadow_idx = _shadow_index(shadow_available)

    package_assignment_anchors: dict[str, datetime] = {}
    for oid, outcome in outcome_idx.items():
        if (outcome.get("action") not in ASSIGNMENT_ACTIONS
                or _cid(outcome.get("actual_cid") or outcome.get("real_cid")) is None):
            continue
        anchor = parse_timestamp(outcome.get("ts_decision"), naive_policy="reject")
        if anchor is not None and anchor <= as_of:
            package_assignment_anchors[oid] = anchor

    base_candidates = []
    base = []
    paczki_excluded = 0
    package_sources: dict[str, str] = {}
    for oid, record in sla_idx.items():
        delivered = parse_timestamp(record.get("delivered_at"), naive_policy="warsaw")
        if delivered is None or not (start <= delivered < end):
            continue
        if not _cohort_accepts(record, cohort, apply_package_exclusion=False):
            continue
        package_anchor = package_assignment_anchors.get(oid)
        if package_anchor is not None and package_anchor > delivered:
            package_anchor = None
        classified_record, package_source = _package_record(
            record, shadow_idx.get(oid, []), package_anchor)
        package_sources[oid] = package_source
        base_candidates.append((delivered, oid, classified_record))
        if not _cohort_accepts(
                classified_record, cohort, apply_package_exclusion=True):
            paczki_excluded += 1
            continue
        base.append((delivered, oid, classified_record))
    base.sort(key=lambda item: (item[0], item[1]))

    # Aggregate membership fingerprint: surowe id nie wychodzi per-row ani do raportu.
    base_hash = _sha256_text(canonical_json({
        "schema": DATASET_SCHEMA,
        "cohort": cohort,
        "window_start": _iso(start),
        "window_end": _iso(end),
        "as_of": _iso(as_of),
        "package_classifier": "common.is_paczka_order(address_id)",
        "members": sorted(oid for _, oid, _ in base),
    }))
    rows: list[dict] = []
    courier_pseudonyms: dict[str, str] = {}
    restaurant_entries_after_as_of = 0
    courier_entries_after_as_of = 0

    for row_number, (delivered_anchor, oid, sla) in enumerate(base, 1):
        reasons: list[str] = []
        sla_cid = _cid(sla.get("courier_id"))
        actual_cid = sla_cid
        proxy_pickup = parse_timestamp(sla.get("picked_up_at"), naive_policy="warsaw")
        proxy_delivery = parse_timestamp(sla.get("delivered_at"), naive_policy="warsaw")

        assignment_at = prediction_at = None
        predicted_pickup = predicted_delivery = None
        prediction_fingerprint = None
        outcome = outcome_idx.get(oid)
        if outcome is None:
            reasons.append("decision_outcome_missing")
        else:
            outcome_cid = _cid(outcome.get("actual_cid") or outcome.get("real_cid"))
            action = outcome.get("action")
            if action not in ASSIGNMENT_ACTIONS:
                reasons.append("assignment_action_unsupported")
            elif outcome_cid is None:
                reasons.append("actual_courier_missing")
            elif sla_cid and sla_cid != outcome_cid:
                reasons.append("outcome_courier_mismatch")
            else:
                actual_cid = outcome_cid
                assignment_at = parse_timestamp(outcome.get("ts_decision"), naive_policy="reject")
                if assignment_at is None:
                    reasons.append("assignment_anchor_missing")
                elif assignment_at > as_of:
                    reasons.append("assignment_after_as_of")
                    assignment_at = None
                elif assignment_at > delivered_anchor:
                    reasons.append("assignment_after_cohort_anchor")
                    assignment_at = None
                else:
                    selected, candidate, missing = _select_preassignment(
                        shadow_idx.get(oid, []), assignment_at, actual_cid
                    )
                    if missing:
                        reasons.append(missing)
                    if candidate is not None:
                        prediction_at, shadow = selected
                        value = shadow.get("flag_fingerprint")
                        prediction_fingerprint = str(value) if value is not None else None
                        plan = candidate.get("plan") if isinstance(candidate.get("plan"), Mapping) else {}
                        pickup_raw = _map_value(plan.get("pickup_at"), oid)
                        delivery_raw = _map_value(plan.get("predicted_delivered_at"), oid)
                        predicted_pickup = parse_timestamp(pickup_raw, naive_policy="reject")
                        predicted_delivery = parse_timestamp(delivery_raw, naive_policy="reject")
                        if predicted_pickup is None:
                            reasons.append("predicted_pickup_missing")
                        if predicted_delivery is None:
                            reasons.append("predicted_delivery_missing")

        if actual_cid is not None and actual_cid not in courier_pseudonyms:
            courier_pseudonyms[actual_cid] = f"courier_{len(courier_pseudonyms) + 1:04d}"
        courier_pseudonym = courier_pseudonyms.get(actual_cid) if actual_cid else None

        pickup_entry = _map_value(restaurant_dwell, oid)
        if _mapping_entry_after_as_of(
            pickup_entry if isinstance(pickup_entry, Mapping) else None,
            ("_computed_at", "computed_at", "written_at"),
            as_of=as_of,
            naive_policy="warsaw",
        ):
            restaurant_entries_after_as_of += 1
            reasons.append("restaurant_snapshot_version_after_as_of")
            pickup_entry = None
        (
            restaurant_last_inside,
            restaurant_arrival,
            restaurant_source,
            restaurant_conf,
        ) = _restaurant_geofence_observables(
            pickup_entry if isinstance(pickup_entry, Mapping) else None,
            actual_cid,
            reasons,
        )
        app_entry = _map_value(courier_ground_truth, oid)
        if _mapping_entry_after_as_of(
            app_entry if isinstance(app_entry, Mapping) else None,
            ("_computed_at", "computed_at", "written_at", "updated_at"),
            as_of=as_of,
            naive_policy="warsaw",
        ):
            courier_entries_after_as_of += 1
            reasons.append("courier_truth_snapshot_version_after_as_of")
            app_entry = None
        server_entry = gps_idx.get(oid)
        delivery_arrival, delivery_source, delivery_conf = _delivery_arrival_observable(
            app_entry if isinstance(app_entry, Mapping) else None,
            server_entry,
            actual_cid,
            as_of,
            reasons,
        )
        if restaurant_arrival and restaurant_arrival > as_of:
            restaurant_arrival = None
            reasons.append("restaurant_arrival_after_as_of")
        if restaurant_last_inside and restaurant_last_inside > as_of:
            restaurant_last_inside = None
            reasons.append("restaurant_last_inside_after_as_of")
        if (restaurant_arrival and restaurant_last_inside
                and restaurant_last_inside < restaurant_arrival):
            restaurant_last_inside = None
            reasons.append("restaurant_visit_order_invalid")
        if delivery_arrival and delivery_arrival > as_of:
            delivery_arrival = None
            reasons.append("delivery_arrival_after_as_of")

        pair_valid = True
        if (restaurant_last_inside and delivery_arrival
                and delivery_arrival < restaurant_last_inside):
            pair_valid = False
            reasons.append("observable_event_order_invalid")

        planned_post = None
        if predicted_pickup and predicted_delivery:
            if predicted_delivery < predicted_pickup:
                reasons.append("predicted_event_order_invalid")
            else:
                planned_post = _minutes(predicted_delivery, predicted_pickup)

        pickup_last_inside_error = _minutes(restaurant_last_inside, predicted_pickup)
        delivery_arrival_error = _minutes(delivery_arrival, predicted_delivery)
        observed_post = (
            _minutes(delivery_arrival, restaurant_last_inside) if pair_valid else None
        )
        if not pair_valid:
            pickup_last_inside_error = None
            delivery_arrival_error = None

        row = {
            "schema_version": DATASET_SCHEMA,
            "row_id": f"row_{row_number:06d}",
            "courier_pseudonym": courier_pseudonym,
            "base_cohort_hash": base_hash,
            "cohort": cohort,
            "package_classification": _package_classification(sla),
            "package_classification_source": package_sources.get(oid, "unknown"),
            "cohort_anchor_at": _iso(delivered_anchor),
            "was_czasowka": sla.get("was_czasowka") if isinstance(sla.get("was_czasowka"), bool) else None,
            "prediction_anchor": "pre_assignment",
            "assignment_at": _iso(assignment_at),
            "prediction_at": _iso(prediction_at),
            "prediction_flag_fingerprint": prediction_fingerprint,
            "predicted_pickup_at": _iso(predicted_pickup),
            "predicted_delivery_at": _iso(predicted_delivery),
            "proxy_pickup_at": _iso(proxy_pickup),
            "proxy_delivery_at": _iso(proxy_delivery),
            "restaurant_arrival_at": _iso(restaurant_arrival),
            "restaurant_last_inside_at": _iso(restaurant_last_inside),
            "restaurant_last_inside_source": (
                "restaurant_geofence_last_inside"
                if restaurant_last_inside is not None else None
            ),
            "restaurant_geofence_source": restaurant_source,
            "restaurant_geofence_confidence": restaurant_conf,
            "delivery_arrival_at": _iso(delivery_arrival),
            "delivery_arrival_source": delivery_source,
            "delivery_arrival_confidence": delivery_conf,
            "observable_pair_valid": pair_valid,
            "pickup_last_inside_error_min": pickup_last_inside_error,
            "delivery_arrival_error_min": delivery_arrival_error,
            "observed_last_inside_to_delivery_arrival_min": observed_post,
            "planned_pickup_to_delivery_min": planned_post,
            "missing_reasons": sorted(set(reasons)),
        }
        rows.append(row)

    n_base = len(rows)
    pickup_metrics = _metric_block(
        rows, "pickup_last_inside_error_min", n_base, base_hash
    )
    delivery_metrics = _metric_block(
        rows, "delivery_arrival_error_min", n_base, base_hash
    )
    common = [r for r in rows if r.get("pickup_last_inside_error_min") is not None
              and r.get("delivery_arrival_error_min") is not None]
    common_metrics = {
        "n": len(common),
        "denominator_base": n_base,
        "coverage_pct": round(100.0 * len(common) / n_base, 3) if n_base else 0.0,
        "support_hash": _support_hash(common, base_hash),
        "pickup_last_inside": _metric_block(
            common, "pickup_last_inside_error_min", len(common), base_hash
        ),
        "delivery_arrival": _metric_block(
            common, "delivery_arrival_error_min", len(common), base_hash
        ),
    }
    package_denominator = len(base_candidates)
    package_known = sum(
        1 for _, _, record in base_candidates
        if _package_classification(record) != "unknown"
    )
    package_coverage_pct = (
        round(100.0 * package_known / package_denominator, 3)
        if package_denominator else 0.0
    )
    package_status = "complete" if package_denominator and package_known == package_denominator else "unresolved"
    package_source_counts = {
        source: sum(1 for _, oid, _ in base_candidates
                    if package_sources.get(oid, "unknown") == source)
        for source in ("sla", "shadow_preassignment", "unknown")
    }
    truth_coverage = {
        "restaurant_arrival": {
            "n": sum(1 for row in rows if row.get("restaurant_arrival_at") is not None),
            "denominator_base": n_base,
        },
        "restaurant_last_inside": {
            "n": sum(1 for row in rows if row.get("restaurant_last_inside_at") is not None),
            "denominator_base": n_base,
        },
        "delivery_arrival": {
            "n": sum(1 for row in rows if row.get("delivery_arrival_at") is not None),
            "denominator_base": n_base,
        },
    }
    for coverage in truth_coverage.values():
        coverage["coverage_pct"] = (
            round(100.0 * coverage["n"] / n_base, 3) if n_base else 0.0
        )

    source_hashes = {
        "sla": _hash_records(sla_available),
        "shadow": _hash_records(shadow_available),
        "outcomes": _hash_records(outcomes_available),
        "restaurant_dwell": _hash_records(restaurant_dwell),
        "courier_ground_truth": _hash_records(courier_ground_truth),
        "gps_delivery_truth": _hash_records(gps_available),
    }
    input_source_hashes = {
        "sla": _hash_records(sla_records),
        "shadow": _hash_records(shadow_records),
        "outcomes": _hash_records(outcome_records),
        "restaurant_dwell": _hash_records(restaurant_dwell),
        "courier_ground_truth": _hash_records(courier_ground_truth),
        "gps_delivery_truth": _hash_records(gps_delivery_records),
    }
    code_lineage = _code_lineage()
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "dataset_schema": DATASET_SCHEMA,
        "report_schema": REPORT_SCHEMA,
        # `git_head` sam nie identyfikuje kodu w dirty worktree. Dokładny hash
        # modułu jest częścią kontraktu lineage także przed commitem.
        "code_revision": code_lineage["git_head"],
        "code_lineage": code_lineage,
        "window": {
            "start_inclusive": _iso(start),
            "end_exclusive": _iso(end),
            "as_of": _iso(as_of),
        },
        "cohort": {
            "policy": cohort,
            "anchor": "sla.button_delivery_at",
            "n_base": n_base,
            "base_cohort_hash": base_hash,
            "package_exclusion_coverage": {
                "known": package_known,
                "denominator_base": package_denominator,
                "coverage_pct": package_coverage_pct,
                "paczki_excluded": paczki_excluded,
                "status": package_status,
                "source_counts": package_source_counts,
            },
            "package_semantics": (
                "non_czasowka_is_not_equivalent_to_food; paczki are classified only "
                "through common.is_paczka_order when address_id is present and valid"
            ),
        },
        "prediction_contract": {
            "anchor": "latest_shadow_at_or_before_operator_decision",
            "candidate": "actual_courier_in_selected_preassignment_record",
            "assignment_actions": sorted(ASSIGNMENT_ACTIONS),
            "actual_courier_required_in_outcome": True,
            "post_assignment_fallback": False,
        },
        "truth_contract": {
            "restaurant_observable": (
                "strict_source_gps_geofence; arrival_and_last_inside_near_button_pickup; "
                "last_inside_is_not_confirmed_departure_or_pickup"
            ),
            "delivery_precedence": [
                "app_geofence_arrival",
                "high_confidence_server_geofence_arrival",
            ],
            "delivery_arrival_is_customer_handoff": False,
            "button_fallback_to_physical": False,
            "error_sign": "actual_minus_predicted_positive_means_later",
            "canonical_kpi_event": "unbound",
        },
        "business_kpi": {
            "status": (
                "blocked_package_exclusion_unresolved"
                if package_status != "complete" else "not_bound"
            ),
            "thresholds": [],
        },
        "metrics": {
            "pickup_last_inside": pickup_metrics,
            "delivery_arrival": delivery_metrics,
            "common_support": common_metrics,
            "truth_coverage": truth_coverage,
        },
        "lineage": {
            "outcome_schema": outcome_schema,
            "source_counts": {
                "sla": len(sla_available),
                "shadow": len(shadow_available),
                "outcomes": len(outcomes_available),
                "restaurant_dwell": len(restaurant_dwell),
                "courier_ground_truth": len(courier_ground_truth),
                "gps_delivery_truth": len(gps_available),
            },
            "input_source_counts": {
                "sla": len(sla_records),
                "shadow": len(shadow_records),
                "outcomes": len(outcome_records),
                "restaurant_dwell": len(restaurant_dwell),
                "courier_ground_truth": len(courier_ground_truth),
                "gps_delivery_truth": len(gps_delivery_records),
            },
            "records_filtered_after_as_of": {
                "sla": sla_after_as_of,
                "shadow": shadow_after_as_of,
                "outcomes": outcomes_after_as_of,
                "gps_delivery_truth": gps_after_as_of,
                "restaurant_dwell": restaurant_entries_after_as_of,
                "courier_ground_truth": courier_entries_after_as_of,
            },
            "snapshot_reconstructability": {
                "sla": True,
                "shadow": True,
                "outcomes": True,
                "restaurant_dwell": False,
                "courier_ground_truth": False,
                "gps_delivery_truth": False,
                "reason": (
                    "whole-map/derived-index sources do not retain complete version "
                    "history; CLI rejects historical as_of older than their file mtime"
                ),
            },
            "source_hashes": source_hashes,
            "source_hash_scope": {
                "sla": "records_available_as_of_full_source",
                "shadow": "records_available_as_of_full_source",
                "outcomes": "records_available_as_of_full_source",
                "gps_delivery_truth": "records_available_as_of_derived_source",
                "restaurant_dwell": (
                    "full_snapshot_nonversioned; dataset_effective_hash_unavailable"
                ),
                "courier_ground_truth": (
                    "full_snapshot_nonversioned; dataset_effective_hash_unavailable"
                ),
            },
            "input_source_hashes": input_source_hashes,
        },
        "data_gaps": [
            "delivery_arrival_is_not_customer_handoff",
            "restaurant_last_inside_is_not_confirmed_departure_or_pickup",
            "restaurant_observable_is_conditioned_on_button_pickup_window",
            "address_id_package_classification_is_incomplete_in_sla_v1",
            "gps_observable_coverage_is_non_random",
        ],
    }
    manifest["dataset_hash"] = _sha256_text(canonical_json(rows))
    return rows, manifest


def _fmt_metric(block: Mapping) -> str:
    if not block.get("n"):
        return "brak complete-case"
    return (
        f"n={block['n']}/{block['denominator_base']} ({block['coverage_pct']:.3f}%), "
        f"MAE={block['mae_min']:.3f} min, bias średni={block['mean_bias_min']:+.3f} min, "
        f"bias mediana={block['median_bias_min']:+.3f} min, "
        f"p10={block['p10_error_min']:+.3f}, p90={block['p90_error_min']:+.3f}, "
        f"min={block['min_error_min']:+.3f}, max={block['max_error_min']:+.3f}"
    )


def build_report(rows: Sequence[Mapping], manifest: Mapping) -> str:
    """Agregat bez wierszy per-order i bez kwalifikacji biznesowej."""
    cohort = manifest["cohort"]
    metrics = manifest["metrics"]
    package = cohort["package_exclusion_coverage"]
    truth_coverage = metrics["truth_coverage"]
    lines = [
        f"# {REPORT_SCHEMA} — pomiar obserwowalnych zdarzeń ETA",
        "",
        "Raport jest wyłącznie pomiarem. Kontrakt KPI biznesowego nie jest związany; "
        "nie zastosowano progów ani automatycznej decyzji.",
        "",
        "## Okno i wspólny mianownik",
        "",
        f"- Okno UTC: `[{manifest['window']['start_inclusive']}, "
        f"{manifest['window']['end_exclusive']})`.",
        f"- Stan wejść nie później niż: `{manifest['window']['as_of']}`.",
        f"- Kohorta: `{cohort['policy']}`; anchor: `{cohort['anchor']}`.",
        f"- Bazowy mianownik: **{cohort['n_base']}**; "
        f"fingerprint: `{cohort['base_cohort_hash']}`.",
        f"- Rozpoznanie paczek przez `common.is_paczka_order`: "
        f"{package['known']}/{package['denominator_base']} "
        f"({package['coverage_pct']:.3f}%), status `{package['status']}`, "
        f"jawnie wykluczone: {package['paczki_excluded']}.",
        f"- Źródła klasyfikacji paczek: "
        f"`{canonical_json(package.get('source_counts') or {})}`.",
        "- `non_czasowka` nie oznacza automatycznie gastronomii. Przy niepełnym "
        "pokryciu `address_id` kontrakt KPI pozostaje zablokowany.",
        "",
        "## Pokrycie i błąd",
        "",
        "Konwencja: `actual − predicted`; wartość dodatnia oznacza zdarzenie późniejsze.",
        "",
        "- Błąd ostatniego punktu GPS wewnątrz geofence restauracji wobec "
        f"predykcji pickup: {_fmt_metric(metrics['pickup_last_inside'])}.",
        "- Błąd przyjazdu GPS pod adres wobec predykcji delivery: "
        f"{_fmt_metric(metrics['delivery_arrival'])}.",
        f"- Wspólny complete-case obu nóg: n={metrics['common_support']['n']}/"
        f"{metrics['common_support']['denominator_base']} "
        f"({metrics['common_support']['coverage_pct']:.3f}%), fingerprint "
        f"`{metrics['common_support']['support_hash']}`.",
        f"- Pokrycie przyjazdu do restauracji: "
        f"{truth_coverage['restaurant_arrival']['n']}/"
        f"{truth_coverage['restaurant_arrival']['denominator_base']} "
        f"({truth_coverage['restaurant_arrival']['coverage_pct']:.3f}%).",
        f"- Pokrycie ostatniego punktu w geofence restauracji: "
        f"{truth_coverage['restaurant_last_inside']['n']}/"
        f"{truth_coverage['restaurant_last_inside']['denominator_base']} "
        f"({truth_coverage['restaurant_last_inside']['coverage_pct']:.3f}%).",
        f"- Pokrycie przyjazdu pod adres: {truth_coverage['delivery_arrival']['n']}/"
        f"{truth_coverage['delivery_arrival']['denominator_base']} "
        f"({truth_coverage['delivery_arrival']['coverage_pct']:.3f}%).",
        "",
        "## Kontrakt prawdy",
        "",
        "- Restauracja: tylko `_source=gps_geofence`; `departed_restaurant` jest "
        "ostatnim punktem wewnątrz geofence blisko kliknięcia, nie potwierdzonym "
        "wyjazdem ani pickupem.",
        "- Dostawa: bezpośredni przyjazd geofence apki; następnie wysokiej pewności "
        "rekonstrukcja serwerowa. Przyjazd pod adres nie potwierdza przekazania klientowi.",
        "- Klik pickup/delivery pozostaje wyłącznie proxy i nie uzupełnia braków GPS.",
        "- Predykcja: najnowszy rekord shadow istniejący nie później niż decyzja operatora; "
        "brak późniejszego fallbacku. Kotwiczą tylko jawne akcje przypisania z "
        "`actual_cid`.",
        "",
        "## Lineage",
        "",
        f"- Dataset: `{manifest['dataset_schema']}`; hash: `{manifest['dataset_hash']}`.",
        f"- Kod: `{manifest['code_revision']}`; schema outcomes: "
        f"`{manifest['lineage']['outcome_schema']}`.",
        f"- Moduł: sha256=`{manifest['code_lineage']['module_sha256']}`; "
        f"git_dirty=`{str(manifest['code_lineage']['git_dirty']).lower()}`.",
        f"- Fingerprint kodu i zależności: "
        f"`{manifest['code_lineage']['behavior_content_fingerprint']}`.",
    ]
    extraction = manifest["lineage"].get("extraction_arguments")
    if isinstance(extraction, Mapping):
        lines.append(
            "- Ekstrakcja shadow: requested lookback="
            f"`{extraction['prediction_lookback_hours_requested']}h`, effective="
            f"`{extraction['prediction_lookback_hours_effective']}h`, cutoff="
            f"`{extraction['shadow_cutoff']}`."
        )
    for name, digest in sorted(manifest["lineage"]["source_hashes"].items()):
        lines.append(
            f"- `{name}`: n={manifest['lineage']['source_counts'][name]}, "
            f"scope=`{manifest['lineage']['source_hash_scope'][name]}`, "
            f"sha256=`{digest}`."
        )
    lines.extend([
        "",
        "## Jawne luki danych",
        "",
    ])
    for gap in manifest.get("data_gaps", []):
        lines.append(f"- `{gap}`")
    return "\n".join(lines) + "\n"


def _file_metadata(path: str) -> dict:
    target = os.path.abspath(path)
    try:
        stat_before = os.stat(target)
        digest = hashlib.sha256()
        with open(target, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        stat_after = os.stat(target)
    except OSError as exc:
        raise ContractError(f"nie można zesnapshotować wejścia: {target}") from exc
    identity_before = (
        stat_before.st_dev,
        stat_before.st_ino,
        stat_before.st_size,
        stat_before.st_mtime_ns,
    )
    identity_after = (
        stat_after.st_dev,
        stat_after.st_ino,
        stat_after.st_size,
        stat_after.st_mtime_ns,
    )
    if identity_before != identity_after:
        raise ContractError(f"źródło zmieniło się podczas hashowania: {target}")
    return {
        "path": target,
        "path_id": _sha256_text(target)[:16],
        "size": stat_after.st_size,
        "mtime_ns": stat_after.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }


def _snapshot_file_set(path: str, *, rotation_aware: bool = False,
                       cutoff: Optional[datetime] = None) -> list[dict]:
    paths = (
        _rotated_logs.files_in_window(path, cutoff)
        if rotation_aware else [path]
    )
    return sorted((_file_metadata(item) for item in paths), key=lambda item: item["path"])


def _assert_sources_unchanged(before: Mapping[str, Sequence[Mapping]],
                              after: Mapping[str, Sequence[Mapping]]) -> None:
    if canonical_json(before) != canonical_json(after):
        changed = sorted(
            name for name in set(before) | set(after)
            if canonical_json(before.get(name)) != canonical_json(after.get(name))
        )
        raise ContractError(
            "źródło zmieniło się podczas snapshotu: " + ",".join(changed)
        )


def _assert_snapshot_reconstructable(
    snapshots: Mapping[str, Sequence[Mapping]],
    *,
    as_of: datetime,
) -> None:
    """Fail-loud dla historycznego ``as_of`` na źródłach bez historii wersji."""
    cutoff_ns = int(as_of.timestamp() * 1_000_000_000)
    non_versioned = ("restaurant_dwell", "courier_ground_truth", "gps_delivery_truth")
    offenders = []
    for name in non_versioned:
        for metadata in snapshots.get(name, []):
            if int(metadata["mtime_ns"]) > cutoff_ns:
                offenders.append(f"{name}:{metadata['path_id']}")
    if offenders:
        raise ContractError(
            "historyczny as_of jest nierekonstruowalny dla niewersjonowanych źródeł: "
            + ",".join(sorted(offenders))
        )


def _read_jsonl(path: str) -> list[dict]:
    records = []
    try:
        with open(path, encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ContractError(f"{path}:{lineno}: niepoprawny JSON") from exc
                if not isinstance(value, dict):
                    raise ContractError(f"{path}:{lineno}: rekord nie jest obiektem")
                records.append(value)
    except FileNotFoundError:
        raise ContractError(f"brak wejścia: {path}")
    return records


def _read_rotation_aware_jsonl(path: str, cutoff: Optional[datetime] = None) -> list[dict]:
    """Fail-loud JSONL reader dla żywego pliku oraz ``.N``/``.N.gz``.

    `_rotated_logs.iter_jsonl_records` celowo zachowuje historyczną semantykę
    pomijania uszkodzonych linii. Dataset audytowy nie może tak tracić danych,
    dlatego wykorzystuje tylko jego kanon kolejności/kompresji, a parsuje ściśle.
    """
    paths = [item for item in _rotated_logs.files_in_window(path, cutoff)
             if os.path.exists(item)]
    if not paths:
        raise ContractError(f"brak wejścia: {path}")
    records: list[dict] = []
    for source in paths:
        try:
            with _rotated_logs.open_maybe_gz(source) as handle:
                for lineno, line in enumerate(handle, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        value = json.loads(line)
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise ContractError(
                            f"{source}:{lineno}: niepoprawny JSON"
                        ) from exc
                    if not isinstance(value, dict):
                        raise ContractError(
                            f"{source}:{lineno}: rekord nie jest obiektem"
                        )
                    records.append(value)
        except OSError as exc:
            raise ContractError(f"nie można odczytać wejścia: {source}") from exc
    return records


def _read_mapping(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise ContractError(f"brak wejścia: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"niepoprawny JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"wejście nie jest mapą: {path}")
    return value


def _atomic_write_0600(path: str, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        os.chmod(target, 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _resolved_path(path: str) -> str:
    return os.path.realpath(os.path.abspath(path))


def _validate_output_paths(
    outputs: Mapping[str, str],
    *,
    input_paths: Sequence[str],
) -> None:
    if set(outputs) != {"dataset", "manifest", "report"}:
        raise ContractError("output bundle wymaga dataset/manifest/report")
    resolved_outputs = {name: _resolved_path(path) for name, path in outputs.items()}
    if len(set(resolved_outputs.values())) != len(resolved_outputs):
        raise ContractError("outputy dataset/manifest/report muszą mieć różne ścieżki")
    resolved_inputs = {_resolved_path(path) for path in input_paths}
    collisions = sorted(
        name for name, path in resolved_outputs.items() if path in resolved_inputs
    )
    if collisions:
        raise ContractError("output koliduje ze źródłem: " + ",".join(collisions))
    for name, path in resolved_outputs.items():
        for root in RUNTIME_OUTPUT_FORBIDDEN_ROOTS:
            root = _resolved_path(root)
            try:
                inside = os.path.commonpath((path, root)) == root
            except ValueError:
                inside = False
            if inside:
                raise ContractError(f"output {name} wskazuje runtime: {path}")


def _stage_0600(path: str, text: str) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.stage.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        return tmp
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _fsync_parent(path: str) -> None:
    fd = os.open(str(Path(path).parent), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_output_bundle(
    *,
    dataset_path: str,
    manifest_path: str,
    report_path: str,
    dataset_text: str,
    manifest_text: str,
    report_text: str,
    fault_after: Optional[str] = None,
) -> None:
    """Publikuje generation bundle; kompletny manifest zawsze jest ostatni.

    Trzech rename nie da się zrobić jedną operacją atomową. Dlatego przed
    pierwszym rename publikowany jest manifest ``complete=false``. Konsument
    akceptuje generację wyłącznie gdy ostatni rename wstawi complete manifest,
    a hashe jego dataset/report zgadzają się z plikami.
    """
    _validate_output_paths(
        {
            "dataset": dataset_path,
            "manifest": manifest_path,
            "report": report_path,
        },
        input_paths=[],
    )
    try:
        manifest = json.loads(manifest_text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ContractError("manifest bundle nie jest JSON") from exc
    generation = manifest.get("generation") if isinstance(manifest, Mapping) else None
    if not isinstance(generation, Mapping) or generation.get("complete") is not True:
        raise ContractError("manifest bundle nie ma complete=true")
    if generation.get("dataset_file_sha256") != hashlib.sha256(
        dataset_text.encode("utf-8")
    ).hexdigest():
        raise ContractError("manifest ma niezgodny hash datasetu")
    if generation.get("report_file_sha256") != hashlib.sha256(
        report_text.encode("utf-8")
    ).hexdigest():
        raise ContractError("manifest ma niezgodny hash raportu")

    staged: dict[str, str] = {}
    targets = {
        "dataset": dataset_path,
        "report": report_path,
        "manifest": manifest_path,
    }
    try:
        staged["dataset"] = _stage_0600(dataset_path, dataset_text)
        staged["report"] = _stage_0600(report_path, report_text)
        staged["manifest"] = _stage_0600(manifest_path, manifest_text)

        incomplete = canonical_json({
            "schema_version": MANIFEST_SCHEMA,
            "generation": {
                "generation_id": generation.get("generation_id"),
                "complete": False,
                "reason": "publication_in_progress_or_interrupted",
            },
        }) + "\n"
        _atomic_write_0600(manifest_path, incomplete)
        _fsync_parent(manifest_path)

        for name in ("dataset", "report", "manifest"):
            os.replace(staged[name], targets[name])
            staged.pop(name)
            os.chmod(targets[name], 0o600)
            _fsync_parent(targets[name])
            if fault_after == name:
                raise RuntimeError(f"injected bundle fault after {name}")
    finally:
        for tmp in staged.values():
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _parse_cli_time(value: str) -> datetime:
    result = parse_timestamp(value, naive_policy="reject")
    if result is None:
        raise argparse.ArgumentTypeError("timestamp jest pusty")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Versioned observable ETA dataset/report (measurement-only)."
    )
    parser.add_argument("--start", required=True, type=_parse_cli_time,
                        help="początek UTC inclusive, ISO z offsetem")
    parser.add_argument("--end", required=True, type=_parse_cli_time,
                        help="koniec UTC exclusive, ISO z offsetem")
    parser.add_argument("--as-of", required=True, type=_parse_cli_time,
                        help="jawny czas snapshotu wejść")
    parser.add_argument("--cohort", required=True,
                        choices=("all_completed", "non_czasowka", "czasowka"))
    parser.add_argument("--sla", default=ledger_io.LEDGER["sla"])
    parser.add_argument("--shadow", default=ledger_io.LEDGER["shadow"])
    parser.add_argument("--outcomes", default=ledger_io.LEDGER["outcomes"])
    parser.add_argument("--restaurant-dwell", default=DEFAULT_RESTAURANT_DWELL)
    parser.add_argument("--courier-ground-truth", default=DEFAULT_COURIER_TRUTH)
    parser.add_argument("--gps-delivery-truth", default=ledger_io.LEDGER["gps_truth"])
    parser.add_argument("--dataset-out", required=True)
    parser.add_argument("--manifest-out", required=True)
    parser.add_argument("--report-out", required=True)
    parser.add_argument("--prediction-lookback-hours", type=int, default=48)
    args = parser.parse_args(argv)

    effective_lookback_hours = max(0, args.prediction_lookback_hours)
    shadow_cutoff = args.start - timedelta(hours=effective_lookback_hours)
    source_specs = {
        "sla": (args.sla, args.sla == ledger_io.LEDGER["sla"], args.start),
        "shadow": (args.shadow, args.shadow == ledger_io.LEDGER["shadow"], shadow_cutoff),
        # Outcomes/GPS zawsze mają kontrakt rotation-aware, również dla
        # jawnie podanej ścieżki replay/fixture.
        "outcomes": (args.outcomes, True, None),
        "restaurant_dwell": (args.restaurant_dwell, False, None),
        "courier_ground_truth": (args.courier_ground_truth, False, None),
        "gps_delivery_truth": (
            args.gps_delivery_truth,
            True,
            None,
        ),
    }
    snapshot_before = {
        name: _snapshot_file_set(path, rotation_aware=rotated, cutoff=cutoff)
        for name, (path, rotated, cutoff) in source_specs.items()
    }
    _assert_snapshot_reconstructable(snapshot_before, as_of=args.as_of)
    outputs = {
        "dataset": args.dataset_out,
        "manifest": args.manifest_out,
        "report": args.report_out,
    }
    _validate_output_paths(
        outputs,
        input_paths=[
            metadata["path"]
            for snapshots in snapshot_before.values()
            for metadata in snapshots
        ],
    )

    sla_records = (
        list(ledger_io.iter_sla(args.start))
        if args.sla == ledger_io.LEDGER["sla"] else _read_jsonl(args.sla)
    )
    shadow_records = (
        list(ledger_io.iter_shadow_decisions(shadow_cutoff))
        if args.shadow == ledger_io.LEDGER["shadow"] else _read_jsonl(args.shadow)
    )
    outcome_records = _read_rotation_aware_jsonl(args.outcomes)
    gps_delivery_records = _read_rotation_aware_jsonl(args.gps_delivery_truth)
    restaurant_dwell = _read_mapping(args.restaurant_dwell)
    courier_ground_truth = _read_mapping(args.courier_ground_truth)

    snapshot_after = {
        name: _snapshot_file_set(path, rotation_aware=rotated, cutoff=cutoff)
        for name, (path, rotated, cutoff) in source_specs.items()
    }
    _assert_sources_unchanged(snapshot_before, snapshot_after)

    rows, manifest = build_dataset(
        sla_records=sla_records,
        shadow_records=shadow_records,
        outcome_records=outcome_records,
        restaurant_dwell=restaurant_dwell,
        courier_ground_truth=courier_ground_truth,
        gps_delivery_records=gps_delivery_records,
        start=args.start,
        end=args.end,
        as_of=args.as_of,
        cohort=args.cohort,
    )
    manifest["lineage"]["file_snapshots"] = snapshot_before
    manifest["lineage"]["file_snapshot_hash"] = _sha256_text(canonical_json(snapshot_before))
    manifest["lineage"]["extraction_arguments"] = {
        "prediction_lookback_hours_requested": args.prediction_lookback_hours,
        "prediction_lookback_hours_effective": effective_lookback_hours,
        "shadow_cutoff": _iso(shadow_cutoff),
    }
    manifest["lineage"]["snapshot_reconstructability"]["cli_file_mtime_guard_passed"] = True
    dataset_text = "".join(canonical_json(row) + "\n" for row in rows)
    report_text = build_report(rows, manifest)
    dataset_file_hash = hashlib.sha256(dataset_text.encode("utf-8")).hexdigest()
    report_file_hash = hashlib.sha256(report_text.encode("utf-8")).hexdigest()
    generation_id = _sha256_text(canonical_json({
        "dataset_file_sha256": dataset_file_hash,
        "report_file_sha256": report_file_hash,
        "file_snapshot_hash": manifest["lineage"]["file_snapshot_hash"],
        "module_sha256": manifest["code_lineage"]["module_sha256"],
    }))
    manifest["generation"] = {
        "generation_id": generation_id,
        "dataset_file_sha256": dataset_file_hash,
        "report_file_sha256": report_file_hash,
        "complete": True,
        "manifest_written_last": True,
    }
    manifest_text = canonical_json(manifest) + "\n"
    _write_output_bundle(
        dataset_path=args.dataset_out,
        manifest_path=args.manifest_out,
        report_path=args.report_out,
        dataset_text=dataset_text,
        manifest_text=manifest_text,
        report_text=report_text,
    )
    print(canonical_json({
        "dataset_schema": DATASET_SCHEMA,
        "n_base": len(rows),
        "base_cohort_hash": manifest["cohort"]["base_cohort_hash"],
        "dataset_hash": manifest["dataset_hash"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
