"""Kanoniczny downstream lifecycle, odtwarzalny z exact state_event outboxu.

Funkcja celowo przyjmuje tylko trwaly event. Dzieki temu retry innego
call-site'u nie podmienia timestampu, kuriera ani source danymi nowego ticku.
Import panel_watcher jest lazy, aby nie tworzyc cyklu przy starcie uslugi.
Outbox gwarantuje ponowienie do trwalego receiptu; callbacki pozostaja
at-least-once w skrajnym crash-window po efekcie, a przed zapisem receiptu.
"""
from __future__ import annotations

from typing import Optional

from dispatch_v2 import state_machine


_PANEL_LEARNING_SOURCES = {
    "panel_initial",
    "panel_diff",
    "panel_reassign",
}


def _cid(event: dict, current: Optional[dict]) -> str:
    return str(
        event.get("courier_id")
        or event.get("previous_courier_id")
        or (current or {}).get("courier_id")
        or ""
    )


def _has_newer_same_type_marker(event: dict, current: Optional[dict]) -> bool:
    event_id = str(event.get("event_id") or "")
    event_type = "".join(
        ch.lower() if ch.isalnum() else "_"
        for ch in str(event.get("event_type") or "")
    ).strip("_")
    if not event_id or not event_type or not current:
        return False
    marker = str(current.get(f"last_lifecycle_event_id_{event_type}") or "")
    return bool(marker and marker != event_id)


def apply(event: dict) -> None:
    """Domknij plan/recanon/learning dla jednego trwalego lifecycle eventu."""
    from dispatch_v2 import panel_watcher as pw  # lazy: panel importuje ten modul

    etype = str(event.get("event_type") or "")
    oid = str(event.get("order_id") or "")
    payload = event.get("payload") or {}
    # Natywny tor paczek historycznie nie mutowal courier_plans w tych
    # call-site'ach. C3 atomizuje event->state, ale nie rozszerza przy okazji
    # semantyki biznesowej planu; plan_recheck zachowuje dotychczasowy routing.
    if payload.get("source") in {"parcel_assign", "parcel_status_inbox"}:
        return
    # Durable callback nie moze pomylic uszkodzonego/brakujacego pliku z
    # prawdziwym brakiem zlecenia. Wyjatek zostawia receipt do retry.
    current = state_machine.get_order_strict(oid) if oid else None
    courier_id = _cid(event, current)
    lifecycle_event_id = str(event.get("event_id") or "") or None

    if etype == "COURIER_ASSIGNED":
        # Cleanup provenance belongs to this exact durable event.  Current
        # state may already be two assignments ahead when FIFO reaches it.
        previous_cid = str(event.get("previous_courier_id") or "")
        event_cid = str(event.get("courier_id") or "")
        if previous_cid and previous_cid != event_cid:
            pw._release_plan_on_reassign(
                previous_cid, oid, _raise_on_error=True
            )

    if etype == "ORDER_RETURNED_TO_POOL":
        # A later assignment must not redirect an old return callback to the
        # new courier.  Empty means the order really had no courier to prune.
        cleanup_cid = str(
            event.get("courier_id") or event.get("previous_courier_id") or ""
        )
        if cleanup_cid:
            pw._remove_stops_on_return(
                cleanup_cid, oid, _raise_on_error=True
            )
        return

    # State transitions may run ahead of the fair downstream lanes. A newer
    # generation of the same event type makes the old callback obsolete even
    # if the current status happens to match again (resurrection -> delivery).
    if _has_newer_same_type_marker(event, current):
        return

    # Manual resurrection jest jawna korekta po blednym DELIVERED. Receipt mogl
    # miec state=applied jeszcze przed korekta, lecz jego callback nie moze po
    # niej ponownie usunac aktywnego zlecenia z planu. Marker korekty powstaje w
    # tym samym atomowym zapisie orders_state co delivered->active.
    if (
        etype == "COURIER_DELIVERED"
        and (current or {}).get("status") in ("assigned", "picked_up")
        and (current or {}).get("last_lifecycle_event_id_order_resurrected")
    ):
        return

    if etype == "COURIER_ASSIGNED":
        event_cid = str(event.get("courier_id") or "")
        current_cid = str((current or {}).get("courier_id") or "")
        if not event_cid or current_cid != event_cid:
            return
        source = str(payload.get("source") or "")
        if source in _PANEL_LEARNING_SOURCES:
            pw._check_panel_agree(
                oid,
                courier_id,
                source,
                lifecycle_event_id=lifecycle_event_id,
                _raise_on_error=True,
            )
            pw._check_panel_override(
                oid,
                courier_id,
                source,
                lifecycle_event_id=lifecycle_event_id,
                _raise_on_error=True,
            )
        pw._save_plan_on_assign_signal(
            oid, courier_id, _raise_on_error=True
        )
        return

    if etype == "COURIER_DELIVERED":
        pw._advance_plan_on_deliver(
            courier_id,
            oid,
            payload.get("timestamp"),
            (current or {}).get("delivery_coords"),
            _raise_on_error=True,
        )
        return

    if etype == "COURIER_PICKED_UP":
        pw._update_plan_on_picked_up(
            courier_id,
            oid,
            payload.get("timestamp"),
            _raise_on_error=True,
        )
        return

    if etype == "ORDER_RESURRECTED":
        # If DELIVERED callback won the race first, it may already have pruned
        # this now-active order.  If correction won first, the stale delivery
        # callback observes active state and becomes a no-op.  In the former
        # ordering this exact durable callback invalidates the incomplete plan;
        # in the latter the helper sees the order still covered and is a no-op.
        if (current or {}).get("status") not in ("assigned", "picked_up"):
            return
        if str(
            (current or {}).get("last_lifecycle_event_id_order_resurrected") or ""
        ) != str(event.get("event_id") or ""):
            return
        pw._invalidate_plan_on_bag_change(
            oid, courier_id, _raise_on_error=True
        )
        return

    if etype in ("CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"):
        pw._invalidate_plan_on_committed_change(
            oid, courier_id, _raise_on_error=True
        )
        return

    # NEW_ORDER nie ma ogolnego plan/recanon downstream. Gated AUTO_KOORD jest
    # zewnetrznym poleceniem panelowym i zachowuje dotychczasowy event-created
    # kontrakt w call-site (nie udajemy exactly-once dla obcej uslugi).
