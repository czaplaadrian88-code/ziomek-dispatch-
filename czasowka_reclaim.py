"""Shadow-only reclaim czasowki po durable ``PICKUP_TIME_UPDATED``.

Ten modul nie zapisuje gastro, nie zmienia orders_state i nie zwalnia planu.
Jedyny runtime effect etapu SHADOW to idempotentny JSONL. Konstruktor eventu
LIVE jest celowo niepodlaczonym stubem za osobna flaga OFF.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from dispatch_v2 import common as C
from dispatch_v2.core.jsonl_appender import append_jsonl_once


SHADOW_LOG_PATH = Path(
    os.environ.get(
        "CZASOWKA_RECLAIM_SHADOW_LOG_PATH",
        "/root/.openclaw/workspace/dispatch_state/czasowka_reclaim_shadow.jsonl",
    )
)
KOORDYNATOR_CID = "26"
TERMINAL_OR_PICKED_STATUSES = frozenset(
    {"picked_up", "delivered", "returned_to_pool", "cancelled"}
)

# Projekt kolejnego, NIEUZBROJONEGO etapu. Dopiero osobna karta/ACK moze dodac
# konsumenta, ktory po state evencie wykona te efekty i potwierdzi read-back.
LIVE_DOWNSTREAM_REQUIREMENTS = (
    "per-order lifecycle lock + CAS assignment_event_id/courier/pickup",
    "gastro_assign staged/live: cid=26 bez zmiany absolutnego pickup_at",
    "timeout: fresh panel read-back przed jakimkolwiek retry writera",
    "panel read-back: cid=26, status_id in {3,4,6}, nie picked/status5/dzien_odbioru",
    "plan_manager.remove_stops(previous_courier_id, oid) + plan_version CAS",
    "pending_proposals_store.locked_pop(oid)",
    "rekey czasowka_eval_state po (oid,reclaim_generation,pickup_at)",
    "rekey czasowka_proposals_state po (oid,reclaim_generation,pickup_at)",
    "limit reclaimow na tick + circuit breaker",
)


def _as_utc(value: object) -> Optional[datetime]:
    parsed = C.parse_panel_timestamp(value)
    return parsed.astimezone(timezone.utc) if parsed is not None else None


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _is_real_courier(value: object) -> bool:
    return _text(value) not in {"", "0", "None", KOORDYNATOR_CID}


def _is_firmowe(order: dict) -> bool:
    try:
        return int(order.get("address_id")) in C.FIRMOWE_KONTO_ADDRESS_IDS
    except (TypeError, ValueError):
        return False


def _stable_evaluation_id(event: dict) -> str:
    event_id = _text(event.get("event_id"))
    if event_id:
        return event_id
    canonical = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
    return "synthetic:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate_pickup_time_updated(
    event: dict,
    current_order: Optional[dict],
    *,
    courier_orders: Optional[Iterable[dict]] = None,
    external_exemption: Optional[dict] = None,
    evaluated_at: Optional[datetime] = None,
) -> dict:
    """Zbuduj kompletny rekord przyszlej akcji; funkcja jest bez efektow I/O."""
    current = current_order if isinstance(current_order, dict) else {}
    payload = event.get("payload") if isinstance(event, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    evaluated = evaluated_at or C.now_utc()
    if evaluated.tzinfo is None:
        evaluated = evaluated.replace(tzinfo=timezone.utc)
    evaluated = evaluated.astimezone(timezone.utc)

    oid = _text(event.get("order_id") or payload.get("oid"))
    current_cid = _text(current.get("courier_id"))
    observed_cid = _text(payload.get("courier_id_at_observation"))
    event_cid = _text(event.get("courier_id") or payload.get("courier_id"))
    assignment_id = _text(current.get("assignment_event_id"))
    observed_assignment_id = _text(
        payload.get("assignment_event_id_at_observation")
    )
    pickup_at_assignment = current.get("pickup_at_at_assignment")
    # Receipt created_at jest kanonem przyczynowym. Payloadowy fallback sluzy
    # jedynie fixture/starym replayom bez durable wrappera i nie moze nadpisac
    # timestampu utrwalonej intencji.
    observed_at_raw = event.get("durable_observed_at") or payload.get(
        "observed_at"
    )
    old_raw = payload.get("old_pickup_at_warsaw")
    new_raw = payload.get("new_pickup_at_warsaw")
    observed_at = _as_utc(observed_at_raw)
    old_pickup = _as_utc(old_raw)
    new_pickup = _as_utc(new_raw)
    near_boundary = (
        observed_at + timedelta(minutes=float(C.CZASOWKA_PREP_MIN))
        if observed_at is not None
        else None
    )
    far_boundary = (
        near_boundary
        + timedelta(minutes=float(C.CZASOWKA_RECLAIM_HYSTERESIS_MIN))
        if near_boundary is not None
        else None
    )
    delta_min = (
        round((new_pickup - old_pickup).total_seconds() / 60.0, 2)
        if old_pickup is not None and new_pickup is not None
        else None
    )

    is_firmowe = _is_firmowe(current)
    is_parcel = bool(current.get("source") == "parcel" or C.is_paczka_order(current))
    external_exempt = isinstance(external_exemption, dict)
    exempt = current.get("reclaim_exempt") is True or external_exempt
    status = current.get("status")
    context_orders = list(courier_orders) if courier_orders is not None else [current]
    sibling_orders = [
        order
        for order in context_orders
        if isinstance(order, dict)
        and _text(order.get("order_id")) != oid
        and _text(order.get("courier_id")) == current_cid
        and order.get("status") in {"assigned", "picked_up"}
    ]
    carried_siblings = [
        order
        for order in sibling_orders
        if order.get("status") == "picked_up" or order.get("picked_up_at") is not None
    ]
    committed_siblings = [
        order
        for order in sibling_orders
        if bool(_text(order.get("czas_kuriera_warsaw")))
    ]
    target_carried = status == "picked_up" or current.get("picked_up_at") is not None
    # ODR-001/R27: obecność zamrożonego czasu jest sygnałem commitmentu.
    # Wartości nieparsowalnej również nie wolno interpretować jako brak bramki.
    target_committed = bool(_text(current.get("czas_kuriera_warsaw")))
    event_id = _text(event.get("event_id"))
    guards = {
        "event_is_pickup_time_updated": event.get("event_type") == "PICKUP_TIME_UPDATED",
        "order_exists": bool(current),
        "status_is_assigned_string": status == "assigned",
        "status_not_terminal_or_picked": status not in TERMINAL_OR_PICKED_STATUSES,
        "courier_is_real": _is_real_courier(current.get("courier_id")),
        "courier_is_not_koordynator": bool(current_cid and current_cid != KOORDYNATOR_CID),
        "picked_up_at_is_none": current.get("picked_up_at") is None,
        "target_not_carried": not target_carried,
        "target_not_committed": not target_committed,
        "courier_bag_has_no_carried_sibling": not carried_siblings,
        "courier_bag_has_no_committed_sibling": not committed_siblings,
        "assignment_event_id_present": bool(assignment_id),
        "pickup_at_at_assignment_present": pickup_at_assignment is not None,
        "same_assignment_generation": bool(
            assignment_id
            and observed_assignment_id
            and assignment_id == observed_assignment_id
        ),
        "time_event_is_later_than_assignment": bool(
            event_id and assignment_id and event_id != assignment_id
            and observed_assignment_id == assignment_id
        ),
        "courier_unchanged": bool(
            current_cid
            and observed_cid
            and current_cid == observed_cid
            and (not event_cid or event_cid == current_cid)
        ),
        "observed_at_valid": observed_at is not None,
        "old_pickup_valid": old_pickup is not None,
        "new_pickup_valid": new_pickup is not None,
        "current_pickup_matches_event": bool(
            new_raw and _text(current.get("pickup_at_warsaw")) == _text(new_raw)
        ),
        "old_at_or_before_60m_boundary": bool(
            old_pickup is not None
            and near_boundary is not None
            and old_pickup <= near_boundary
        ),
        "new_at_or_after_hysteresis_boundary": bool(
            new_pickup is not None
            and far_boundary is not None
            and new_pickup >= far_boundary
        ),
        "new_later_than_old": bool(
            old_pickup is not None
            and new_pickup is not None
            and new_pickup > old_pickup
        ),
        "not_reclaim_exempt": not exempt,
        "live_scope_excludes_firmowe_and_parcel": not (is_firmowe or is_parcel),
    }

    guard_order = (
        "event_is_pickup_time_updated",
        "order_exists",
        "status_is_assigned_string",
        "status_not_terminal_or_picked",
        "courier_is_real",
        "courier_is_not_koordynator",
        "picked_up_at_is_none",
        "target_not_carried",
        "target_not_committed",
        "courier_bag_has_no_carried_sibling",
        "courier_bag_has_no_committed_sibling",
        "assignment_event_id_present",
        "pickup_at_at_assignment_present",
        "same_assignment_generation",
        "time_event_is_later_than_assignment",
        "courier_unchanged",
        "observed_at_valid",
        "old_pickup_valid",
        "new_pickup_valid",
        "current_pickup_matches_event",
        "old_at_or_before_60m_boundary",
        "new_at_or_after_hysteresis_boundary",
        "new_later_than_old",
        "not_reclaim_exempt",
    )
    technical_candidate = all(guards[name] for name in guard_order)
    would_reclaim = bool(
        technical_candidate
        and guards["live_scope_excludes_firmowe_and_parcel"]
    )
    all_order = (*guard_order, "live_scope_excludes_firmowe_and_parcel")
    rejection_reason = next(
        (name for name in all_order if not guards[name]),
        None,
    )
    rejection_reasons = [name for name in all_order if not guards[name]]
    reclaim_generation = assignment_id or observed_assignment_id or None
    reclaim_intent_id = (
        f"{oid}:{reclaim_generation}" if oid and reclaim_generation else None
    )
    action_reason = "pickup_boundary_crossed"
    record = {
        "schema": "czasowka_reclaim_shadow.v1",
        "record_type": "CZASOWKA_RECLAIM_EVALUATION",
        "recorded_at": evaluated.isoformat(),
        "shadow_evaluation_id": _stable_evaluation_id(event),
        "lifecycle_event_id": event_id or None,
        "reclaim_intent_id": reclaim_intent_id,
        "oid": oid or None,
        "cid": current_cid or event_cid or None,
        "status": status,
        "picked_up_at": current.get("picked_up_at"),
        "assignment_event_id": assignment_id or None,
        "assignment_event_id_at_observation": observed_assignment_id or None,
        "pickup_at_at_assignment": pickup_at_assignment,
        "observed_at": _iso(observed_at),
        "old_pickup_at_warsaw": old_raw,
        "new_pickup_at_warsaw": new_raw,
        "delta_min": delta_min,
        "boundary_min": C.CZASOWKA_PREP_MIN,
        "hysteresis_min": C.CZASOWKA_RECLAIM_HYSTERESIS_MIN,
        "near_boundary_at": _iso(near_boundary),
        "reclaim_boundary_at": _iso(far_boundary),
        "source": payload.get("source"),
        "reclaim_exempt": exempt,
        "reclaim_exempt_reason": (
            external_exemption.get("reason_code")
            if external_exempt
            else current.get("reclaim_exempt_reason")
        ),
        "reclaim_exempt_source": "operator_store" if external_exempt else "order_state",
        "courier_bag_active_sibling_count": len(sibling_orders),
        "courier_bag_carried_sibling_count": len(carried_siblings),
        "courier_bag_committed_sibling_count": len(committed_siblings),
        "is_firmowe": is_firmowe,
        "is_parcel": is_parcel,
        "guards": guards,
        "would_reclaim_candidate": technical_candidate,
        "would_reclaim": would_reclaim,
        "rejection_reason": rejection_reason,
        "rejection_reasons": rejection_reasons,
        "future_action": {
            "event_type": "ORDER_RECLAIMED_TO_CZASOWKA",
            "status": "planned",
            "courier_id": KOORDYNATOR_CID,
            "previous_courier_id": current_cid or event_cid or None,
            "reclaim_generation": reclaim_generation,
            "reclaimed_at": evaluated.isoformat(),
            "reason": action_reason,
        },
        # Jedynki sa latwe do sumowania, ale kanoniczny licznik would_reclaim
        # deduplikuje po reclaim_intent_id w aggregate_shadow_metrics().
        "metric_contribution": {
            "evaluated": 1,
            "would_reclaim_candidate": int(technical_candidate),
            "would_reclaim": int(would_reclaim),
            "firmowe_candidate": int(technical_candidate and is_firmowe),
            "parcel_candidate": int(technical_candidate and is_parcel),
        },
    }
    return record


def record_pickup_time_shadow(
    event: dict,
    current_order: Optional[dict],
    *,
    log_path: Optional[Path] = None,
    decision_log_path: Optional[Path] = None,
    exemption_path: Optional[Path] = None,
    enabled_by_receipt: Optional[bool] = None,
    evaluated_at: Optional[datetime] = None,
) -> Optional[dict]:
    """Zapisz dokladnie raz jedna durable ewaluacje; OFF oznacza zero I/O."""
    if enabled_by_receipt is None:
        if "czasowka_reclaim_shadow_authorized" in event:
            enabled_by_receipt = bool(
                event.get("czasowka_reclaim_shadow_authorized")
            )
        else:
            enabled_by_receipt = C.decision_flag(
                "ENABLE_CZASOWKA_RECLAIM_SHADOW"
            )
    if not enabled_by_receipt or event.get("event_type") != "PICKUP_TIME_UPDATED":
        return None
    from dispatch_v2 import reclaim_exemptions, shadow_dispatcher, state_machine

    snapshot = state_machine.get_all_strict()
    external_exemption = reclaim_exemptions.get_exemption(
        event.get("order_id"), exemption_path
    )
    record = evaluate_pickup_time_updated(
        event,
        current_order,
        courier_orders=snapshot.values(),
        external_exemption=external_exemption,
        evaluated_at=evaluated_at,
    )
    append_jsonl_once(
        log_path or SHADOW_LOG_PATH,
        record,
        dedupe_key="shadow_evaluation_id",
        dedupe_value=record["shadow_evaluation_id"],
        scan_rotated=True,
    )
    canonical_path = decision_log_path
    if canonical_path is None:
        canonical_path = Path(C.load_config()["paths"]["shadow_log"])
    shadow_dispatcher.append_czasowka_reclaim_observation(
        str(canonical_path), record
    )
    return record


def aggregate_shadow_metrics(records: Iterable[dict]) -> dict:
    """Zbiorczy licznik; would_reclaim jest unikalny per (oid,generacja)."""
    evaluations: dict[str, dict] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("record_type") == "CZASOWKA_RECLAIM_EVALUATION" and isinstance(
            record.get("best"), dict
        ):
            best = record["best"]
            record = {
                "shadow_evaluation_id": record.get("event_id"),
                "reclaim_intent_id": best.get("reclaim_intent_id"),
                "would_reclaim_candidate": best.get("would_reclaim_candidate"),
                "would_reclaim": best.get("would_reclaim"),
                "is_firmowe": best.get("reclaim_is_firmowe"),
                "is_parcel": best.get("reclaim_is_parcel"),
                "rejection_reason": best.get("rejection_reason"),
            }
        evaluation_id = _text(record.get("shadow_evaluation_id"))
        if evaluation_id and evaluation_id not in evaluations:
            evaluations[evaluation_id] = record
    reclaim_intents = {
        _text(record.get("reclaim_intent_id"))
        for record in evaluations.values()
        if record.get("would_reclaim") and record.get("reclaim_intent_id")
    }
    candidate_intents = {
        _text(record.get("reclaim_intent_id"))
        for record in evaluations.values()
        if record.get("would_reclaim_candidate") and record.get("reclaim_intent_id")
    }
    firmowe_intents = {
        _text(record.get("reclaim_intent_id"))
        for record in evaluations.values()
        if record.get("would_reclaim_candidate")
        and record.get("is_firmowe")
        and record.get("reclaim_intent_id")
    }
    parcel_intents = {
        _text(record.get("reclaim_intent_id"))
        for record in evaluations.values()
        if record.get("would_reclaim_candidate")
        and record.get("is_parcel")
        and record.get("reclaim_intent_id")
    }
    reasons = Counter(
        _text(record.get("rejection_reason"))
        for record in evaluations.values()
        if record.get("rejection_reason")
    )
    return {
        "schema": "czasowka_reclaim_shadow_metrics.v1",
        "evaluated": len(evaluations),
        "would_reclaim_candidates_distinct": len(candidate_intents),
        "would_reclaim": len(reclaim_intents),
        "firmowe_candidates_distinct": len(firmowe_intents),
        "parcel_candidates_distinct": len(parcel_intents),
        "rejection_reasons": dict(sorted(reasons.items())),
    }


def iter_shadow_records(path: Optional[Path] = None) -> Iterator[dict]:
    """Reader aktywnego JSONL i rotacji numerycznych (.gz takze)."""
    active = path or SHADOW_LOG_PATH
    candidates = [active]
    candidates.extend(sorted(active.parent.glob(active.name + ".[0-9]*")))
    for candidate in candidates:
        try:
            opener = gzip.open if candidate.suffix == ".gz" else open
            with opener(candidate, "rt", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(row, dict):
                        yield row
        except FileNotFoundError:
            continue


def read_shadow_metrics(path: Optional[Path] = None) -> dict:
    if path is not None:
        return aggregate_shadow_metrics(iter_shadow_records(path))
    from dispatch_v2.tools import ledger_io

    return aggregate_shadow_metrics(
        ledger_io.iter_shadow_decisions(None, include_observations=True)
    )


def build_live_reclaim_event(
    pickup_event: dict,
    current_order: Optional[dict],
    *,
    courier_orders: Optional[Iterable[dict]] = None,
    external_exemption: Optional[dict] = None,
    live_enabled: Optional[bool] = None,
    evaluated_at: Optional[datetime] = None,
) -> Optional[dict]:
    """Nieuzbrojony stub: tylko buduje event; nic go w tym etapie nie wywoluje."""
    if live_enabled is None:
        live_enabled = C.decision_flag("ENABLE_CZASOWKA_RECLAIM_LIVE")
    if not live_enabled:
        return None
    if courier_orders is None:
        from dispatch_v2 import reclaim_exemptions, state_machine

        courier_orders = state_machine.get_all_strict().values()
        external_exemption = reclaim_exemptions.get_exemption(
            pickup_event.get("order_id")
        )
    record = evaluate_pickup_time_updated(
        pickup_event,
        current_order,
        courier_orders=courier_orders,
        external_exemption=external_exemption,
        evaluated_at=evaluated_at,
    )
    if not record["would_reclaim"]:
        return None
    action = record["future_action"]
    generation = _text(record["assignment_event_id"])
    intent_digest = hashlib.sha256(
        f"{record['oid']}\0{generation}".encode("utf-8")
    ).hexdigest()[:20]
    return {
        "event_type": "ORDER_RECLAIMED_TO_CZASOWKA",
        # Stabilna tozsamosc przyszlego durable eventu: jedna akcja na
        # (oid,generacja), niezaleznie od liczby time-eventow w tej generacji.
        "event_id": (
            f"{record['oid']}_ORDER_RECLAIMED_TO_CZASOWKA_{intent_digest}"
        ),
        "order_id": record["oid"],
        "courier_id": KOORDYNATOR_CID,
        "czasowka_reclaim_live_authorized": True,
        "payload": {
            **action,
            "expected_assignment_event_id": record["assignment_event_id"],
            "expected_pickup_at_warsaw": record["new_pickup_at_warsaw"],
            "source_lifecycle_event_id": record["lifecycle_event_id"],
        },
    }


__all__ = [
    "LIVE_DOWNSTREAM_REQUIREMENTS",
    "SHADOW_LOG_PATH",
    "aggregate_shadow_metrics",
    "build_live_reclaim_event",
    "evaluate_pickup_time_updated",
    "iter_shadow_records",
    "read_shadow_metrics",
    "record_pickup_time_shadow",
]
