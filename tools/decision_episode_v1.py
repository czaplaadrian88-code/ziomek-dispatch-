#!/usr/bin/env python3
"""Deterministyczny, read-only census epizodow decyzji operatora.

Wejsciem sa wylacznie zapisane rekordy ``learning_log``. Narzedzie nie
rekonstruuje nieobserwowanych kandydatow ani kontrfaktycznego swiata. Dane
zrodlowe sa tylko czytane; plik wyjsciowy powstaje jedynie po podaniu
``--out``.

Schema i ograniczenia semantyczne: ``docs/decision_episode_v1_schema.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

try:
    from . import _rotated_logs
except ImportError:  # pragma: no cover - samodzielne uruchomienie z tools/
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _rotated_logs  # type: ignore


UTC = timezone.utc
WARSAW = ZoneInfo("Europe/Warsaw")
SCHEMA_VERSION = "decision_episode_v1"
CENSUS_SCHEMA_VERSION = "decision_episode_v1.census.v1"
A8_CUTOFF = datetime(2026, 7, 19, 23, 39, 21, tzinfo=UTC)
SHADOW_FALLBACK_SECONDS = 15 * 60
ASSIGNMENT_FALLBACK_SECONDS = 30
ACTOR_FALLBACK_SECONDS = 30
WINDOW_MINUTES = (15, 30, 60)

DEFAULT_SHADOW = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
DEFAULT_LEARNING = "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl"
DEFAULT_AUDIT = (
    "/root/.openclaw/workspace/dispatch_state/coordinator_assign_audit.jsonl"
)
DEFAULT_EVENTS_DB = "/root/.openclaw/workspace/dispatch_state/events.db"
DEFAULT_GPS = (
    "/root/.openclaw/workspace/dispatch_state/gps_delivery_truth.jsonl"
)
DEFAULT_OUTCOMES = (
    "/root/.openclaw/workspace/dispatch_state/decision_outcomes.jsonl"
)
DEFAULT_RESTAURANT_DWELL = (
    "/root/.openclaw/workspace/dispatch_state/restaurant_dwell.json"
)
DEFAULT_COURIER_TRUTH = (
    "/root/.openclaw/workspace/dispatch_state/courier_ground_truth.json"
)

TRUTH_CLASSES = (
    "OBSERVED",
    "SAME_WORLD_REPLAY",
    "STRUCTURAL_STRESS",
    "UNIDENTIFIABLE",
)
EMITTED_TRUTH_CLASSES = frozenset({"OBSERVED", "UNIDENTIFIABLE"})
MISSING_REASON_ORDER = (
    "ACTOR_UNKNOWN",
    "JOIN_AMBIGUOUS",
    "PRE_A8_CONTAMINATED",
    "OUT_OF_RECORDED_POOL",
    "WORLD_INCOMPLETE",
    "OUTCOME_PROXY_ONLY",
    "HANDOFF_UNBOUND",
    "SHIFT_EXPOSURE_UNKNOWN",
)
MISSING_REASONS = frozenset(MISSING_REASON_ORDER)
ACTOR_PROVENANCE = (
    "ACTOR_ATTESTED_CONSOLE",
    "ACTOR_UNKNOWN_GASTRO_DIRECT",
    "ACTOR_TEST_FILTERED",
)

# Jawny allowlist domen oraz jawne konta techniczne/testowe z audytu konsoli.
# Kazdy adres spoza allowlisty lub o testowym local-part jest filtrowany
# fail-closed. Surowy e-mail nigdy nie trafia do outputu.
ALLOWED_ACTOR_DOMAINS = frozenset({"nadajesz.pl"})
FILTERED_ACTOR_IDENTITIES = frozenset({
    "",
    "t",
    "test@op",
    "test@nadajesz.pl",
    "admin@ziomek.pl",
})
FILTERED_LOCAL_PARTS = frozenset({"admin", "test"})

# Bezpieczna, bez-PII czesc zapisanego wektora cech. Pola nieobecne pozostaja
# null; nie sa zerowane ani forward-fillowane.
CANDIDATE_FEATURES = (
    "score",
    "travel_min_cal",
    "travel_min",
    "km_to_pickup",
    "pickup_dist_km",
    "pln_v",
    "pln_v_payaware",
    "pln_delta_km",
    "deliv_spread_km",
    "pickup_spread_km",
    "objm_r6_breach_max_min",
    "objm_r6_breach_count",
    "late_pickup_committed_max",
    "new_pickup_late_min",
    "bag_size_before",
    "r6_bag_size",
    "loadgov_load_ewma",
    "pos_source",
    "cs_tier_label",
    "feasibility",
    "best_effort",
    "free_at_min",
    "shift_remaining_min",
    "n_waves",
    "paczka_is",
)
PLAN_FIELDS = (
    "pickup_at",
    "total_duration_min",
    "strategy",
)
RULE_VERDICT_FIELDS = (
    "schema",
    "status",
    "phase",
    "enforcement",
    "decision_verdict",
    "selection_mode",
    "policy_pending",
    "always_propose_enabled",
)
DECISION_META_FIELDS = (
    "degraded_osrm",
    "osrm_cache_age_s",
    "osrm_degraded_since_ts",
)


class ContractError(ValueError):
    """Wejscie lub konfiguracja lamie kontrakt ekstraktora."""


def canonical_json(value: object, *, pretty: bool = False) -> str:
    """Stabilna reprezentacja JSON; brak czasu wykonania w outputcie."""
    if pretty:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
            default=_json_default,
        ) + "\n"
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    ) + "\n"


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return iso_utc(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"nieobslugiwany typ JSON: {type(value).__name__}")


def _stable_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_timestamp(value: object, *, naive_policy: str = "reject") -> Optional[datetime]:
    """Parsuje ISO/epoch do UTC; polityka naive jest zawsze jawna."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            dt = datetime.fromtimestamp(float(value), UTC)
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
        if naive_policy == "warsaw":
            dt = dt.replace(tzinfo=WARSAW)
        elif naive_policy == "utc":
            dt = dt.replace(tzinfo=UTC)
        else:
            raise ContractError(f"timestamp bez strefy: {value!r}")
    return dt.astimezone(UTC)


def iso_utc(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _id(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _oid(record: Mapping) -> Optional[str]:
    return _id(record.get("order_id", record.get("oid")))


def _cid(value: object) -> Optional[str]:
    return _id(value)


def _record_timestamp(
    record: Mapping, fields: Sequence[str], *, naive_policy: str = "reject"
) -> Optional[datetime]:
    for field in fields:
        if record.get(field) not in (None, ""):
            return parse_timestamp(record[field], naive_policy=naive_policy)
    return None


def _strict_rotated_jsonl(path: str, *, optional: bool = False) -> tuple[list[dict], dict]:
    """Czyta .N/.N.gz w kolejnosci kanonicznego helpera, ale fail-loud."""
    sources = [p for p in _rotated_logs.files_in_window(path, None) if os.path.isfile(p)]
    if not sources:
        if optional:
            return [], {"available": False, "records": 0, "files": 0}
        raise ContractError(f"brak wejscia: {path}")
    records: list[dict] = []
    for source in sources:
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
            raise ContractError(f"nie mozna odczytac: {source}") from exc
    return records, {"available": True, "records": len(records), "files": len(sources)}


def _read_mapping(path: str) -> tuple[dict, dict]:
    if not os.path.isfile(path):
        return {}, {"available": False, "records": 0, "files": 0}
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ContractError(f"niepoprawna mapa JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"wejscie nie jest mapa: {path}")
    return {str(key): item for key, item in value.items()}, {
        "available": True,
        "records": len(value),
        "files": 1,
    }


def _read_events_db(path: str) -> tuple[dict, dict]:
    """Czyta SQLite przez URI mode=ro, bez tworzenia pliku i bez migracji."""
    if not os.path.isfile(path):
        return {
            "assignments": [],
            "new_orders": [],
            "picked": [],
            "delivered": [],
        }, {"available": False, "records": 0, "files": 0}
    uri = "file:" + os.path.abspath(path) + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.execute("PRAGMA query_only=ON")
        assignments = []
        picked = []
        delivered = []
        for row in connection.execute(
            "SELECT event_id,event_type,order_id,courier_id,payload,created_at "
            "FROM audit_log WHERE event_type IN "
            "('COURIER_ASSIGNED','COURIER_PICKED_UP','COURIER_DELIVERED')"
        ):
            event_id, event_type, order_id, courier_id, payload, created_at = row
            try:
                parsed_payload = json.loads(payload or "{}")
            except (json.JSONDecodeError, ValueError):
                parsed_payload = {}
            item = {
                "event_id": _id(event_id),
                "event_type": event_type,
                "order_id": _id(order_id),
                "courier_id": _cid(courier_id),
                "payload": parsed_payload if isinstance(parsed_payload, dict) else {},
                "ts": parse_timestamp(created_at),
            }
            if event_type == "COURIER_ASSIGNED":
                assignments.append(item)
            elif event_type == "COURIER_PICKED_UP":
                picked.append(item)
            else:
                delivered.append(item)
        new_orders = []
        for row in connection.execute(
            "SELECT event_id,order_id,created_at FROM events WHERE event_type='NEW_ORDER'"
        ):
            event_id, order_id, created_at = row
            new_orders.append({
                "event_id": _id(event_id),
                "order_id": _id(order_id),
                "ts": parse_timestamp(created_at),
            })
    except (sqlite3.Error, OSError) as exc:
        raise ContractError(f"nie mozna czytac events.db: {path}") from exc
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass
    sort_key = lambda row: (row.get("ts") or datetime.min.replace(tzinfo=UTC), row.get("event_id") or "")
    for rows in (assignments, picked, delivered, new_orders):
        rows.sort(key=sort_key)
    total = len(assignments) + len(picked) + len(delivered) + len(new_orders)
    return {
        "assignments": assignments,
        "new_orders": new_orders,
        "picked": picked,
        "delivered": delivered,
    }, {"available": True, "records": total, "files": 1}


def _index_latest(records: Iterable[Mapping], time_fields: Sequence[str]) -> dict[str, dict]:
    index: dict[str, tuple[tuple, dict]] = {}
    floor = datetime.min.replace(tzinfo=UTC)
    for raw in records:
        oid = _oid(raw)
        if not oid:
            continue
        record = dict(raw)
        ts = _record_timestamp(record, time_fields)
        key = (ts or floor, _stable_hash(record))
        if oid not in index or key > index[oid][0]:
            index[oid] = (key, record)
    return {oid: item[1] for oid, item in index.items()}


def _shadow_candidates(shadow: Mapping) -> list[dict]:
    candidates: list[dict] = []
    best = shadow.get("best")
    if isinstance(best, Mapping):
        candidates.append(dict(best))
    alternatives = shadow.get("alternatives")
    if isinstance(alternatives, list):
        candidates.extend(dict(x) for x in alternatives if isinstance(x, Mapping))
    # Pierwszy zapis danego courier_id wygrywa. To usuwa historyczny duplikat
    # best w alternatives bez dopisywania jakiejkolwiek cechy.
    unique: list[dict] = []
    seen: set[str] = set()
    for candidate in candidates:
        cid = _cid(candidate.get("courier_id"))
        if cid is None or cid in seen:
            continue
        seen.add(cid)
        unique.append(candidate)
    return unique


def _candidate_by_id(shadow: Mapping, courier_id: Optional[str]) -> Optional[dict]:
    if courier_id is None:
        return None
    for candidate in _shadow_candidates(shadow):
        if _cid(candidate.get("courier_id")) == courier_id:
            return candidate
    return None


def _predicted_delivery(plan: Mapping, order_id: Optional[str]) -> object:
    value = plan.get("predicted_delivered_at")
    if isinstance(value, Mapping) and order_id is not None:
        if order_id in value:
            return value[order_id]
        for key, item in value.items():
            if str(key) == order_id:
                return item
        return None
    # Starszy zapis bywa pojedynczym timestampem; nadal jest wartoscia direct.
    if isinstance(value, (str, int, float)) or value is None:
        return value
    return None


def _safe_candidate(candidate: Optional[Mapping], order_id: Optional[str]) -> Optional[dict]:
    if not isinstance(candidate, Mapping):
        return None
    result = {"courier_id": _cid(candidate.get("courier_id"))}
    for field in CANDIDATE_FEATURES:
        result[field] = candidate.get(field)
    plan = candidate.get("plan")
    if isinstance(plan, Mapping):
        result["plan"] = {field: plan.get(field) for field in PLAN_FIELDS}
        result["plan"]["predicted_delivered_at"] = _predicted_delivery(plan, order_id)
    else:
        result["plan"] = None
    return result


def _safe_decision_context(shadow: Optional[Mapping]) -> Optional[dict]:
    if not isinstance(shadow, Mapping):
        return None
    verdict = shadow.get("rule_verdict")
    meta = shadow.get("decision_meta")
    return {
        "pickup_ready_at": shadow.get("pickup_ready_at"),
        "rule_verdict": (
            {field: verdict.get(field) for field in RULE_VERDICT_FIELDS}
            if isinstance(verdict, Mapping) else None
        ),
        "decision_meta": (
            {field: meta.get(field) for field in DECISION_META_FIELDS}
            if isinstance(meta, Mapping) else None
        ),
    }


def _normalize_human_name(value: object) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).strip().casefold().split())
    return text or None


def _legacy_audit_assign_signature(record: Mapping) -> bool:
    if record.get("mode") != "live":
        return False
    if record.get("kind") not in (None, ""):
        return False
    required = ("actor", "command", "courier", "order_id", "ts")
    if any(key not in record for key in required):
        return False
    command = record.get("command")
    if not isinstance(command, str):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    basenames = {os.path.basename(token) for token in tokens[:4]}
    return "gastro_assign.py" in basenames


def _effective_audit_assign(record: Mapping) -> Optional[str]:
    """Zwraca provenance wykonanego assign lub None.

    Od 21.07 audyt ma docelowe ``kind=assign``. Snapshot przejsciowy ma 73
    rekordy bez kind, lecz z waskim podpisem wywolania gastro_assign.py; tylko
    67 z nich ma potwierdzony sukces wykonania.
    """
    if record.get("mode") != "live":
        return None
    failed = record.get("ok") is False or record.get("rc") not in (None, 0)
    if record.get("kind") == "assign":
        return None if failed else "kind_assign"
    if not _legacy_audit_assign_signature(record):
        return None
    if record.get("ok") is not True or record.get("rc") != 0:
        return None
    return "legacy_gastro_assign_signature"


def _actor_status(value: object) -> tuple[str, Optional[str]]:
    normalized = str(value or "").strip().casefold()
    if normalized in FILTERED_ACTOR_IDENTITIES:
        return "filtered", None
    if normalized.count("@") != 1:
        return "filtered", None
    local, domain = normalized.split("@", 1)
    if not local or domain not in ALLOWED_ACTOR_DOMAINS:
        return "filtered", None
    if local in FILTERED_LOCAL_PARTS or local.startswith("test"):
        return "filtered", None
    pseudonym = "actor_sha256:" + hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()[:16]
    return "attested", pseudonym


def _prepare_audit(records: Iterable[Mapping]) -> tuple[list[dict], dict]:
    assigns = []
    inventory = Counter()
    for raw in records:
        if _legacy_audit_assign_signature(raw):
            inventory["observed_legacy_live_assign_signature"] += 1
        schema = _effective_audit_assign(raw)
        if schema is None:
            if _legacy_audit_assign_signature(raw):
                inventory["excluded_failed_legacy_live_assign"] += 1
            else:
                inventory["excluded_non_live_assign"] += 1
            continue
        oid = _oid(raw)
        courier = _normalize_human_name(raw.get("courier"))
        ts = _record_timestamp(raw, ("ts",))
        if oid is None or courier is None or ts is None:
            inventory["excluded_incomplete_assign"] += 1
            continue
        actor_state, actor_id = _actor_status(raw.get("actor"))
        assigns.append({
            "order_id": oid,
            "courier_name_key": courier,
            "ts": ts,
            "actor_state": actor_state,
            "actor_id": actor_id,
            "schema": schema,
        })
        inventory[f"included_{schema}"] += 1
        inventory[f"actor_{actor_state}"] += 1
    assigns.sort(key=lambda x: (x["ts"], x["order_id"], x["courier_name_key"], x.get("actor_id") or ""))
    return assigns, dict(sorted(inventory.items()))


def _unique_join(candidates: Sequence[dict], method: str, anchor: Optional[datetime]) -> tuple[Optional[dict], dict]:
    if len(candidates) == 1:
        match = candidates[0]
        delta = None
        if anchor is not None and match.get("ts") is not None:
            delta = round((match["ts"] - anchor).total_seconds(), 6)
        return match, {
            "status": "UNIQUE",
            "method": method,
            "match_count": 1,
            "delta_seconds": delta,
        }
    return None, {
        "status": "UNMATCHED" if not candidates else "AMBIGUOUS",
        "method": method,
        "match_count": len(candidates),
        "delta_seconds": None,
    }


def _join_assignment(
    learning: Mapping,
    assignments_by_event: Mapping[str, list[dict]],
    assignments_by_pair: Mapping[tuple[str, str], list[dict]],
    learning_at: datetime,
) -> tuple[Optional[dict], dict]:
    oid = _oid(learning)
    actual = _cid(learning.get("actual_courier_id", learning.get("courier_id")))
    lifecycle = _id(learning.get("lifecycle_event_id"))
    if lifecycle:
        exact_by_id = assignments_by_event.get(lifecycle, [])
        exact = [
            row for row in exact_by_id
            if row.get("order_id") == oid and row.get("courier_id") == actual
        ]
        if exact_by_id:
            return _unique_join(exact, "lifecycle_event_id", learning_at)
    if oid is None or actual is None:
        return _unique_join([], "order_courier_time_30s", learning_at)
    candidates = [
        row for row in assignments_by_pair.get((oid, actual), [])
        if row.get("ts") is not None
        and abs((row["ts"] - learning_at).total_seconds()) <= ASSIGNMENT_FALLBACK_SECONDS
    ]
    return _unique_join(candidates, "order_courier_time_30s", learning_at)


def _valid_embedded_shadow(learning: Mapping) -> Optional[dict]:
    decision = learning.get("decision")
    if not isinstance(decision, Mapping):
        return None
    if _oid(decision) != _oid(learning):
        return None
    event_id = _id(decision.get("event_id"))
    if event_id is None:
        return None
    best = decision.get("best")
    proposed = _cid(learning.get("proposed_courier_id"))
    if not isinstance(best, Mapping) or _cid(best.get("courier_id")) != proposed:
        return None
    record = dict(decision)
    record["ts"] = parse_timestamp(record.get("ts"))
    return record


def _join_shadow(
    learning: Mapping,
    shadow_by_event: Mapping[str, list[dict]],
    shadow_by_order: Mapping[str, list[dict]],
    anchor: datetime,
) -> tuple[Optional[dict], dict]:
    embedded = _valid_embedded_shadow(learning)
    if embedded is not None:
        return embedded, {
            "status": "UNIQUE",
            "method": "learning.decision",
            "match_count": 1,
            "delta_seconds": (
                round((embedded["ts"] - anchor).total_seconds(), 6)
                if embedded.get("ts") is not None else None
            ),
        }
    decision = learning.get("decision")
    event_id = _id(decision.get("event_id")) if isinstance(decision, Mapping) else None
    if event_id:
        exact_by_id = shadow_by_event.get(event_id, [])
        exact = [row for row in exact_by_id if _oid(row) == _oid(learning)]
        if exact_by_id:
            return _unique_join(exact, "shadow.event_id", anchor)
    oid = _oid(learning)
    proposed = _cid(learning.get("proposed_courier_id"))
    eligible = []
    for row in shadow_by_order.get(oid or "", []):
        ts = row.get("ts")
        best = row.get("best")
        if ts is None or ts > anchor:
            continue
        if (anchor - ts).total_seconds() > SHADOW_FALLBACK_SECONDS:
            continue
        if not isinstance(best, Mapping) or _cid(best.get("courier_id")) != proposed:
            continue
        eligible.append(row)
    if not eligible:
        return _unique_join([], "latest_order_best_before_15m", anchor)
    latest_ts = max(row["ts"] for row in eligible)
    latest = [row for row in eligible if row["ts"] == latest_ts]
    return _unique_join(latest, "latest_order_best_before_15m", anchor)


def _join_actor(
    audit_by_pair: Mapping[tuple[str, str], list[dict]],
    order_id: Optional[str],
    candidate_name: Optional[str],
    anchor: Optional[datetime],
) -> tuple[Optional[dict], dict, str, Optional[str]]:
    if order_id is None or candidate_name is None or anchor is None:
        return None, {
            "status": "AMBIGUOUS",
            "method": "order_courier_time_30s",
            "match_count": 0,
            "delta_seconds": None,
        }, "ACTOR_UNKNOWN_GASTRO_DIRECT", None
    rows = audit_by_pair.get((order_id, candidate_name), [])
    exact = [row for row in rows if row["ts"] == anchor]
    if exact:
        match, join = _unique_join(exact, "order_courier_exact_ts", anchor)
    else:
        fallback = [
            row for row in rows
            if abs((row["ts"] - anchor).total_seconds()) <= ACTOR_FALLBACK_SECONDS
        ]
        match, join = _unique_join(fallback, "order_courier_time_30s", anchor)
    if match is None:
        return None, join, "ACTOR_UNKNOWN_GASTRO_DIRECT", None
    if match["actor_state"] == "filtered":
        return match, join, "ACTOR_TEST_FILTERED", None
    return match, join, "ACTOR_ATTESTED_CONSOLE", match["actor_id"]


def _is_reassignment(
    learning: Mapping,
    assignment: Optional[Mapping],
    assignments_by_order: Mapping[str, list[dict]],
    learning_at: datetime,
) -> bool:
    if learning.get("panel_source") == "panel_reassign":
        return True
    if assignment is None:
        payload = None
    else:
        payload = assignment.get("payload")
    if not isinstance(payload, Mapping):
        payload = {}
    previous = _cid(payload.get("previous_cid"))
    actual = _cid(learning.get("actual_courier_id", learning.get("courier_id")))
    if previous is not None and previous != actual:
        return True
    oid = _oid(learning)
    anchor = assignment.get("ts") if assignment else learning_at
    if oid is None or anchor is None:
        return False
    # Dwa writery potrafia zapisac to samo pierwsze przypisanie (_diff/_packs).
    # Wczesniejszy rekord tego samego kuriera nie jest reassignem; wczesniejszy
    # inny kurier jest obserwowanym poprzednim wlascicielem lifecycle.
    return any(
        row.get("ts") is not None
        and row["ts"] < anchor
        and row.get("courier_id") not in (None, actual)
        for row in assignments_by_order.get(oid, [])
    )


def _rate(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": round(numerator / denominator, 6) if denominator else None,
    }


def _minutes(later: Optional[datetime], earlier: Optional[datetime]) -> Optional[float]:
    if later is None or earlier is None or later < earlier:
        return None
    return round((later - earlier).total_seconds() / 60.0, 6)


def _outcomes_for_episode(
    order_id: Optional[str],
    actual_cid: Optional[str],
    proposed_cid: Optional[str],
    assignment_at: Optional[datetime],
    events: Mapping[str, list[dict]],
    outcome_record: Optional[Mapping],
    gps_record: Optional[Mapping],
    dwell_record: Optional[Mapping],
    courier_record: Optional[Mapping],
) -> tuple[dict, dict]:
    def first_lifecycle_status(rows: Sequence[Mapping]) -> Optional[datetime]:
        candidates = [
            row.get("ts") for row in rows
            if row.get("order_id") == order_id
            and row.get("courier_id") == actual_cid
            and row.get("ts") is not None
            and (assignment_at is None or row["ts"] >= assignment_at)
        ]
        return min(candidates) if candidates else None

    status_pickup = first_lifecycle_status(events["picked"])
    status_delivered = first_lifecycle_status(events["delivered"])
    events_status_match = status_pickup is not None or status_delivered is not None
    status_pickup_source = "events.db" if status_pickup is not None else None
    status_delivered_source = "events.db" if status_delivered is not None else None
    decision_outcome_match = False
    if isinstance(outcome_record, Mapping):
        outcome_cid = _cid(outcome_record.get("actual_cid"))
        if actual_cid is not None and outcome_cid == actual_cid:
            proxy_pickup = _record_timestamp(outcome_record, ("picked_up_at",))
            proxy_delivered = _record_timestamp(outcome_record, ("delivered_at",))
            # decision_outcomes jest tylko kontrola parytetu; nie wypelnia
            # primary observables statusu ani fizycznego outcome.
            decision_outcome_match = proxy_pickup is not None or proxy_delivered is not None

    eta_match = False
    restaurant_last_inside = None
    restaurant_last_inside_source = None
    if isinstance(dwell_record, Mapping):
        dwell_cid = _cid(dwell_record.get("courier_id"))
        if (
            actual_cid is not None
            and dwell_cid == actual_cid
            and dwell_record.get("_source") == "gps_geofence"
        ):
            # Historyczne pole departed_restaurant = ostatni punkt wewnatrz
            # geofence, nie dowod possession/pickupu.
            try:
                restaurant_last_inside = _record_timestamp(
                    dwell_record, ("departed_restaurant",), naive_policy="warsaw"
                )
            except ContractError:
                restaurant_last_inside = None
            eta_match = restaurant_last_inside is not None
            if eta_match:
                restaurant_last_inside_source = "restaurant_dwell_gps_geofence"
    if isinstance(courier_record, Mapping):
        courier_cid = _cid(courier_record.get("courier_id"))
        if actual_cid is not None and courier_cid == actual_cid:
            if status_pickup is None:
                status_pickup = _record_timestamp(
                    courier_record, ("picked_up_at", "pickup_at")
                )
                if status_pickup is not None:
                    status_pickup_source = "courier_ground_truth_status"
            if status_delivered is None:
                status_delivered = _record_timestamp(
                    courier_record, ("delivered_at", "button_delivered_at")
                )
                if status_delivered is not None:
                    status_delivered_source = "courier_ground_truth_status"
            eta_match = eta_match or status_pickup is not None or status_delivered is not None

    delivery_arrival = None
    delivery_arrival_confidence = None
    gps_match = False
    if isinstance(gps_record, Mapping):
        gps_cid = _cid(gps_record.get("courier_id"))
        if actual_cid is not None and gps_cid == actual_cid:
            delivery_arrival = _record_timestamp(gps_record, ("physical_delivered_at",))
            gps_match = delivery_arrival is not None
            if gps_match:
                delivery_arrival_confidence = gps_record.get("confidence")

    windows = {}
    for minutes in WINDOW_MINUTES:
        end = assignment_at + timedelta(minutes=minutes) if assignment_at else None
        if assignment_at is None:
            new_orders = utilized = backlog = None
        else:
            new_orders = sum(
                1 for row in events["new_orders"]
                if row.get("ts") is not None and assignment_at < row["ts"] <= end
            )
            utilized_orders = {
                row["order_id"]
                for row in events["first_assignments_by_courier"].get(proposed_cid, [])
                if row.get("courier_id") == proposed_cid
                and row.get("order_id") != order_id
                and row.get("ts") is not None
                and assignment_at < row["ts"] <= end
            }
            utilized = len(utilized_orders)
            created_before = {
                row["order_id"] for row in events["new_orders"]
                if row.get("order_id") is not None
                and row.get("ts") is not None
                and row["ts"] <= assignment_at
            }
            backlog = sum(
                1 for oid in created_before
                if oid in events["first_assignment"]
                and assignment_at < events["first_assignment"][oid] <= end
            )
        windows[f"plus_{minutes}m"] = {
            "window_end": iso_utc(end),
            "new_order_count": new_orders,
            "factual_proposed_courier_first_assignment_count": utilized,
            "preexisting_backlog_assigned_count": backlog,
            "shift_exposure_state": "SHIFT_EXPOSURE_UNKNOWN",
            "censored": True,
            "truth_class": "OBSERVED",
        }

    proxy_available = any((
        restaurant_last_inside,
        status_pickup,
        delivery_arrival,
        status_delivered,
    ))
    result = {
        "windows": windows,
        "overlap_group_id": None,
        "restaurant_last_inside_at": iso_utc(restaurant_last_inside),
        "restaurant_last_inside_source": restaurant_last_inside_source,
        "status_pickup_at": iso_utc(status_pickup),
        "status_pickup_source": status_pickup_source,
        "delivery_arrival_at": iso_utc(delivery_arrival),
        "delivery_arrival_confidence": delivery_arrival_confidence,
        "status_delivered_at": iso_utc(status_delivered),
        "status_delivered_source": status_delivered_source,
        "proxy_restaurant_to_arrival_min": _minutes(
            delivery_arrival, restaurant_last_inside
        ),
        "proxy_status_pickup_to_status_delivered_min": _minutes(
            status_delivered, status_pickup
        ),
        "r6_physical_possession_to_handoff_min": None,
        "truth_state": "HANDOFF_UNBOUND",
        "truth_class": "UNIDENTIFIABLE",
    }
    return result, {
        "decision_outcomes": decision_outcome_match,
        "events_status": events_status_match,
        "eta_ground_truth": eta_match,
        "gps_delivery_truth": gps_match,
        "proxy_available": proxy_available,
    }


def _assign_overlap_groups(episodes: list[dict]) -> None:
    """Nadaje komponenty overlapu dla tego samego Z w oknach +60 min."""
    by_courier: dict[str, list[dict]] = defaultdict(list)
    for episode in episodes:
        cid = episode.get("proposed_courier_id")
        assignment_at = parse_timestamp(episode.get("assignment_at"))
        if cid is not None and assignment_at is not None:
            by_courier[cid].append(episode)
    for cid, rows in by_courier.items():
        rows.sort(key=lambda row: (row["assignment_at"], row["episode_id"]))
        component: list[dict] = []
        component_end: Optional[datetime] = None

        def flush() -> None:
            if not component:
                return
            token = _stable_hash({
                "courier_id": cid,
                "episode_ids": sorted(row["episode_id"] for row in component),
            })[:20]
            group_id = "overlap_sha256:" + token
            for row in component:
                row["outcomes"]["overlap_group_id"] = group_id

        for row in rows:
            start = parse_timestamp(row["assignment_at"])
            end = start + timedelta(minutes=60)
            if component and component_end is not None and start > component_end:
                flush()
                component = []
                component_end = None
            component.append(row)
            component_end = max(component_end, end) if component_end else end
        flush()


def extract_decision_episodes(
    *,
    learning_records: Sequence[Mapping],
    shadow_records: Sequence[Mapping],
    audit_records: Sequence[Mapping],
    events: Mapping[str, list[dict]],
    outcome_records: Sequence[Mapping] = (),
    gps_records: Sequence[Mapping] = (),
    restaurant_dwell: Optional[Mapping[str, Mapping]] = None,
    courier_truth: Optional[Mapping[str, Mapping]] = None,
) -> tuple[list[dict], dict]:
    """Buduje direct-only epizody i techniczne liczniki audytu."""
    restaurant_dwell = restaurant_dwell or {}
    courier_truth = courier_truth or {}
    shadow_by_event: dict[str, list[dict]] = defaultdict(list)
    shadow_by_order: dict[str, list[dict]] = defaultdict(list)
    for raw in shadow_records:
        record = dict(raw)
        record["ts"] = parse_timestamp(record.get("ts"))
        if record["ts"] is None:
            continue
        event_id = _id(record.get("event_id"))
        oid = _oid(record)
        if event_id:
            shadow_by_event[event_id].append(record)
        if oid:
            shadow_by_order[oid].append(record)
    for rows in shadow_by_event.values():
        rows.sort(key=lambda row: (row["ts"], _stable_hash(row)))
    for rows in shadow_by_order.values():
        rows.sort(key=lambda row: (row["ts"], _stable_hash(row)))

    assignments_by_event: dict[str, list[dict]] = defaultdict(list)
    assignments_by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    assignments_by_order: dict[str, list[dict]] = defaultdict(list)
    for row in events["assignments"]:
        if row.get("event_id"):
            assignments_by_event[row["event_id"]].append(row)
        if row.get("order_id") and row.get("courier_id"):
            assignments_by_pair[(row["order_id"], row["courier_id"])].append(row)
        if row.get("order_id"):
            assignments_by_order[row["order_id"]].append(row)

    event_context = dict(events)
    event_context["first_assignment"] = {}
    for row in events["assignments"]:
        oid = row.get("order_id")
        ts = row.get("ts")
        if oid is not None and ts is not None:
            event_context["first_assignment"].setdefault(oid, ts)
    event_context["first_assignments_by_courier"] = defaultdict(list)
    for oid, first_ts in event_context["first_assignment"].items():
        first_rows = [
            row for row in assignments_by_order.get(oid, [])
            if row.get("ts") == first_ts
        ]
        # Duplikaty lifecycle tego samego pierwszego przypisania licza sie raz.
        if first_rows:
            first = sorted(first_rows, key=lambda row: row.get("event_id") or "")[0]
            cid = first.get("courier_id")
            if cid is not None:
                event_context["first_assignments_by_courier"][cid].append(first)

    audit_assigns, audit_inventory = _prepare_audit(audit_records)
    audit_by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in audit_assigns:
        audit_by_pair[(row["order_id"], row["courier_name_key"])].append(row)

    outcomes_by_oid = _index_latest(
        outcome_records, ("written_at", "delivered_at", "ts_decision")
    )
    gps_by_oid = _index_latest(
        gps_records, ("physical_delivered_at", "button_delivered_at")
    )

    prepared_learning = []
    for raw in learning_records:
        action = raw.get("action")
        if action not in ("PANEL_OVERRIDE", "PANEL_AGREE"):
            continue
        learning_at = parse_timestamp(raw.get("ts"))
        if learning_at is None:
            raise ContractError("learning_log: brak ts")
        prepared_learning.append((learning_at, _stable_hash(raw), dict(raw)))
    prepared_learning.sort(key=lambda item: (item[0], item[1]))

    episodes: list[dict] = []
    for learning_at, learning_hash, learning in prepared_learning:
        oid = _oid(learning)
        proposed = _cid(learning.get("proposed_courier_id"))
        actual = _cid(learning.get("actual_courier_id", learning.get("courier_id")))
        assignment, assignment_join = _join_assignment(
            learning, assignments_by_event, assignments_by_pair, learning_at
        )
        assignment_at = assignment.get("ts") if assignment else None
        latency = learning.get("latency_s")
        proposal_fallback = learning_at
        if isinstance(latency, (int, float)) and not isinstance(latency, bool):
            proposal_fallback = learning_at - timedelta(seconds=float(latency))
        shadow_anchor = assignment_at or proposal_fallback
        shadow, shadow_join = _join_shadow(
            learning, shadow_by_event, shadow_by_order, shadow_anchor
        )
        shadow_event_id = _id(shadow.get("event_id")) if shadow else None
        if learning.get("action") == "PANEL_OVERRIDE":
            proposal_at = (
                shadow.get("ts") if shadow and shadow.get("ts") else proposal_fallback
            )
        else:
            proposal_at = proposal_fallback
        candidates = _shadow_candidates(shadow or {})
        proposed_candidate_raw = _candidate_by_id(shadow or {}, proposed)
        human_candidate_raw = _candidate_by_id(shadow or {}, actual)
        human_in_pool = human_candidate_raw is not None

        human_name = _normalize_human_name(
            human_candidate_raw.get("name") if human_candidate_raw else None
        )
        actor_match, actor_join, actor_provenance, actor_id = _join_actor(
            audit_by_pair, oid, human_name, assignment_at or learning_at
        )

        reassign = _is_reassignment(
            learning, assignment, assignments_by_order, learning_at
        )
        category = "REASSIGN" if reassign else "FIRST_ASSIGNMENT"
        lifecycle_event_id = _id(learning.get("lifecycle_event_id"))
        if lifecycle_event_id:
            decision_key = lifecycle_event_id
            decision_key_source = "lifecycle_event_id"
        elif shadow_event_id:
            decision_key = shadow_event_id
            decision_key_source = "shadow.event_id"
        else:
            decision_key = "fallback_sha256:" + _stable_hash({
                "order_id": oid,
                "proposal_at": iso_utc(proposal_at),
                "actual_courier_id": actual,
            })
            decision_key_source = "fallback_hash"
        episode_id = "decision_episode_v1:" + _stable_hash({
            "decision_key": decision_key,
            "learning_at": iso_utc(learning_at),
            "action": learning.get("action"),
            "actual_courier_id": actual,
        })

        outcome, outcome_coverage = _outcomes_for_episode(
            oid,
            actual,
            proposed,
            assignment_at,
            event_context,
            outcomes_by_oid.get(oid or ""),
            gps_by_oid.get(oid or ""),
            restaurant_dwell.get(oid or ""),
            courier_truth.get(oid or ""),
        )

        missing = set()
        if actor_provenance != "ACTOR_ATTESTED_CONSOLE":
            missing.add("ACTOR_UNKNOWN")
        if any(
            join["status"] != "UNIQUE"
            for join in (assignment_join, shadow_join)
        ) or actor_join["match_count"] > 1:
            missing.add("JOIN_AMBIGUOUS")
        cohort = "POST_A8" if proposal_at >= A8_CUTOFF else "PRE_A8"
        if cohort == "PRE_A8":
            missing.add("PRE_A8_CONTAMINATED")
        if shadow is not None and shadow_join["status"] == "UNIQUE" and not human_in_pool:
            missing.add("OUT_OF_RECORDED_POOL")
        # Snapshot wyboru nie jest pelnym world_record, nawet gdy liczba
        # zapisanych kandydatow rowna sie pool_total_count.
        missing.add("WORLD_INCOMPLETE")
        if outcome_coverage["proxy_available"]:
            missing.add("OUTCOME_PROXY_ONLY")
        missing.add("HANDOFF_UNBOUND")
        missing.add("SHIFT_EXPOSURE_UNKNOWN")
        missing_reasons = [reason for reason in MISSING_REASON_ORDER if reason in missing]

        pool_total = shadow.get("pool_total_count") if shadow else None
        episode = {
            "schema_version": SCHEMA_VERSION,
            "episode_id": episode_id,
            "decision_key": decision_key,
            "decision_key_source": decision_key_source,
            "lifecycle_event_id": lifecycle_event_id,
            "shadow_event_id": shadow_event_id,
            "order_id": oid,
            "category": category,
            "first_choice_eligible": not reassign,
            "action": learning.get("action").removeprefix("PANEL_"),
            "panel_source": learning.get("panel_source"),
            "cohort": cohort,
            "proposal_at": iso_utc(proposal_at),
            "assignment_at": iso_utc(assignment_at),
            "learning_at": iso_utc(learning_at),
            "proposed_courier_id": proposed,
            "actual_courier_id": actual,
            "proposed_score_recorded": learning.get("proposed_score"),
            "latency_s": learning.get("latency_s"),
            "actor": "ATTESTED_CONSOLE" if actor_id else "ACTOR_UNKNOWN",
            "actor_id": actor_id,
            "actor_provenance": actor_provenance,
            "joins": {
                "assignment": assignment_join,
                "shadow": shadow_join,
                "actor": actor_join,
                "lifecycle_key": None,
            },
            "recorded_pool": {
                "candidate_count": len(candidates),
                "candidate_ids": [_cid(row.get("courier_id")) for row in candidates],
                "pool_total_count": pool_total,
                "pool_feasible_count": (
                    shadow.get("pool_feasible_count") if shadow else None
                ),
                "world_complete": False,
                "human_in_recorded_pool": human_in_pool,
            },
            "proposed_candidate": _safe_candidate(proposed_candidate_raw, oid),
            "human_candidate": _safe_candidate(human_candidate_raw, oid),
            "decision_context": _safe_decision_context(shadow),
            "outcomes": outcome,
            "source_coverage": {
                "learning_log": True,
                "shadow": shadow is not None,
                "events_db": assignment is not None,
                "coordinator_assign_audit": actor_match is not None,
                "eta_ground_truth": outcome_coverage["eta_ground_truth"],
                "gps_delivery_truth": outcome_coverage["gps_delivery_truth"],
                "decision_outcomes": outcome_coverage["decision_outcomes"],
            },
            "truth_class": "OBSERVED",
            "comparison_truth_class": "UNIDENTIFIABLE",
            "analysis_state": "HOLD" if missing_reasons else "ELIGIBLE",
            "missing_reasons": missing_reasons,
        }
        if episode["truth_class"] not in EMITTED_TRUTH_CLASSES:
            raise AssertionError("D1 nie moze emitowac replay/stress")
        if any(reason not in MISSING_REASONS for reason in missing_reasons):
            raise AssertionError("niekanoniczny enum braku")
        episodes.append(episode)

    decision_key_counts = Counter(row["decision_key"] for row in episodes)
    collision_groups = sum(1 for count in decision_key_counts.values() if count > 1)
    collision_rows = 0
    for episode in episodes:
        count = decision_key_counts[episode["decision_key"]]
        if count == 1:
            episode["joins"]["lifecycle_key"] = {
                "status": "UNIQUE",
                "method": episode["decision_key_source"],
                "match_count": 1,
                "delta_seconds": None,
            }
            continue
        collision_rows += 1
        episode["joins"]["lifecycle_key"] = {
            "status": "AMBIGUOUS",
            "method": episode["decision_key_source"],
            "match_count": count,
            "delta_seconds": None,
        }
        if "JOIN_AMBIGUOUS" not in episode["missing_reasons"]:
            episode["missing_reasons"] = [
                reason for reason in MISSING_REASON_ORDER
                if reason in set(episode["missing_reasons"]) | {"JOIN_AMBIGUOUS"}
            ]
        episode["analysis_state"] = "HOLD"

    _assign_overlap_groups(episodes)
    episodes.sort(key=lambda row: (row["learning_at"], row["episode_id"]))
    technical = {
        "audit_assign_inventory": audit_inventory,
        "decision_key_collision_groups": collision_groups,
        "decision_key_collision_rows": collision_rows,
        "learning_action_records_ignored": len(learning_records) - len(prepared_learning),
    }
    return episodes, technical


def _cohort_census(episodes: Sequence[Mapping], cohort: str) -> dict:
    rows = [row for row in episodes if row.get("cohort") == cohort]
    first = [row for row in rows if row.get("first_choice_eligible") is True]
    actions = Counter(row.get("action") for row in rows)
    first_actions = Counter(row.get("action") for row in first)
    unique = sum(
        1 for row in first
        if row["joins"]["assignment"]["status"] == "UNIQUE"
        and row["joins"]["shadow"]["status"] == "UNIQUE"
        and row["joins"]["lifecycle_key"]["status"] == "UNIQUE"
    )
    assignment_unique = sum(
        1 for row in first if row["joins"]["assignment"]["status"] == "UNIQUE"
    )
    shadow_unique = sum(
        1 for row in first if row["joins"]["shadow"]["status"] == "UNIQUE"
    )
    actor_unique = sum(
        1 for row in first if row["joins"]["actor"]["status"] == "UNIQUE"
    )
    h_evaluable = [
        row for row in first
        if row.get("action") == "OVERRIDE"
        and row["joins"]["shadow"]["status"] == "UNIQUE"
    ]
    h_in_pool = sum(
        1 for row in h_evaluable if row["recorded_pool"]["human_in_recorded_pool"]
    )
    actor_clean = sum(
        1 for row in first
        if row.get("actor_provenance") == "ACTOR_ATTESTED_CONSOLE"
    )
    audit_any = sum(
        1 for row in first
        if row["source_coverage"]["coordinator_assign_audit"]
    )
    missing = Counter(
        reason for row in first for reason in row.get("missing_reasons", [])
    )
    coverage = {}
    for source in (
        "learning_log",
        "shadow",
        "events_db",
        "eta_ground_truth",
        "gps_delivery_truth",
        "decision_outcomes",
    ):
        count = sum(1 for row in first if row["source_coverage"].get(source))
        coverage[source] = _rate(count, len(first))
    actors = Counter(
        row["actor_id"] for row in first
        if row.get("actor_provenance") == "ACTOR_ATTESTED_CONSOLE"
        and row.get("actor_id")
    )
    actor_actions = Counter(
        row.get("action") for row in first
        if row.get("actor_provenance") == "ACTOR_ATTESTED_CONSOLE"
    )
    actor_provenance = Counter(row.get("actor_provenance") for row in first)
    return {
        "truth_class": "OBSERVED",
        "learning_actions_all": {
            "AGREE": actions.get("AGREE", 0),
            "OVERRIDE": actions.get("OVERRIDE", 0),
            "total": len(rows),
        },
        "first_choice_actions": {
            "AGREE": first_actions.get("AGREE", 0),
            "OVERRIDE": first_actions.get("OVERRIDE", 0),
            "total": len(first),
        },
        "reassign_count": len(rows) - len(first),
        "unique_join_rate": _rate(unique, len(first)),
        "assignment_unique_join_rate": _rate(assignment_unique, len(first)),
        "shadow_unique_join_rate": _rate(shadow_unique, len(first)),
        "actor_unique_join_rate": _rate(actor_unique, len(first)),
        "human_in_recorded_pool_rate": _rate(h_in_pool, len(h_evaluable)),
        "human_in_recorded_pool_population": "first-choice OVERRIDE with unique shadow",
        "n_actor_clean": actor_clean,
        "actor_attested_actions": {
            "AGREE": actor_actions.get("AGREE", 0),
            "OVERRIDE": actor_actions.get("OVERRIDE", 0),
            "total": actor_clean,
        },
        "actor_attestation_rate": _rate(actor_clean, len(first)),
        "audit_match_including_test_filtered_rate": _rate(audit_any, len(first)),
        "actor_distribution_after_filter": dict(sorted(actors.items())),
        "actor_provenance_distribution": dict(sorted(actor_provenance.items())),
        "source_coverage": coverage,
        "missing_reason_distribution": {
            reason: missing.get(reason, 0) for reason in MISSING_REASON_ORDER
        },
    }


def build_census(
    episodes: Sequence[Mapping],
    source_inventory: Mapping[str, Mapping],
    technical: Mapping,
) -> dict:
    return {
        "schema_version": CENSUS_SCHEMA_VERSION,
        "truth_class": "OBSERVED",
        "cutoff_a8_2": iso_utc(A8_CUTOFF),
        "population": "learning_log PANEL_OVERRIDE/PANEL_AGREE; first-choice metrics exclude REASSIGN",
        "cohorts": {
            "POST_A8": _cohort_census(episodes, "POST_A8"),
            "PRE_A8": _cohort_census(episodes, "PRE_A8"),
        },
        "source_inventory": {
            key: dict(value) for key, value in sorted(source_inventory.items())
        },
        "technical": dict(technical),
    }


def build_from_paths(args: argparse.Namespace) -> dict:
    learning, learning_meta = _strict_rotated_jsonl(args.learning)
    shadow, shadow_meta = _strict_rotated_jsonl(args.shadow)
    audit, audit_meta = _strict_rotated_jsonl(args.audit, optional=True)
    gps, gps_meta = _strict_rotated_jsonl(args.gps, optional=True)
    outcomes, outcomes_meta = _strict_rotated_jsonl(args.outcomes, optional=True)
    dwell, dwell_meta = _read_mapping(args.restaurant_dwell)
    courier, courier_meta = _read_mapping(args.courier_ground_truth)
    events, events_meta = _read_events_db(args.events_db)
    episodes, technical = extract_decision_episodes(
        learning_records=learning,
        shadow_records=shadow,
        audit_records=audit,
        events=events,
        outcome_records=outcomes,
        gps_records=gps,
        restaurant_dwell=dwell,
        courier_truth=courier,
    )
    source_inventory = {
        "learning_log": learning_meta,
        "shadow": shadow_meta,
        "coordinator_assign_audit": audit_meta,
        "events_db": events_meta,
        "gps_delivery_truth": gps_meta,
        "decision_outcomes": outcomes_meta,
        "restaurant_dwell": dwell_meta,
        "courier_ground_truth": courier_meta,
    }
    census = build_census(episodes, source_inventory, technical)
    if args.census_only:
        return census
    return {
        "schema_version": SCHEMA_VERSION + ".bundle.v1",
        "truth_class": "OBSERVED",
        "episodes": episodes,
        "census": census,
    }


def _atomic_write_explicit(path: str, text: str) -> None:
    target = Path(path)
    if not target.parent.is_dir():
        raise ContractError(f"katalog --out nie istnieje: {target.parent}")
    fd, temporary = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only ekstraktor i census decision_episode_v1"
    )
    parser.add_argument("--shadow", default=DEFAULT_SHADOW)
    parser.add_argument("--learning", default=DEFAULT_LEARNING)
    parser.add_argument("--audit", default=DEFAULT_AUDIT)
    parser.add_argument("--events-db", default=DEFAULT_EVENTS_DB)
    parser.add_argument("--gps", default=DEFAULT_GPS)
    parser.add_argument("--outcomes", default=DEFAULT_OUTCOMES)
    parser.add_argument("--restaurant-dwell", default=DEFAULT_RESTAURANT_DWELL)
    parser.add_argument("--courier-ground-truth", default=DEFAULT_COURIER_TRUTH)
    parser.add_argument(
        "--census-only", action="store_true", help="emituj tylko raport jakosci"
    )
    parser.add_argument(
        "--out", help="jawna sciezka zapisu; bez tej opcji wynik trafia na stdout"
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build_from_paths(args)
        text = canonical_json(result, pretty=args.pretty)
        if args.out:
            _atomic_write_explicit(args.out, text)
            # Stdout pozostaje deterministycznym potwierdzeniem bez danych PII.
            confirmation = {
                "bytes": len(text.encode("utf-8")),
                "out": os.path.abspath(args.out),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
            sys.stdout.write(canonical_json(confirmation))
        else:
            sys.stdout.write(text)
        return 0
    except (ContractError, OSError) as exc:
        print(f"decision_episode_v1: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
