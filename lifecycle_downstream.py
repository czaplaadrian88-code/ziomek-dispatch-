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


def _is_current_active_courier(current: Optional[dict], courier_id: str) -> bool:
    """Czy cleanup starego eventu trafilby w aktualna generacje worka."""
    return bool(
        courier_id
        and current
        and current.get("status") in ("assigned", "picked_up")
        and str(current.get("courier_id") or "") == str(courier_id)
    )


def _protect_current_generation(
    current: Optional[dict],
    courier_id: str,
) -> bool:
    """Nie pozwól staremu cleanupowi dotknąć aktywnej nowszej generacji.

    Brak pokrycia OID w planie tej generacji należy do jej własnego ASSIGNED
    receipt/gap-fillu. Stary receipt nie może używać swojej historycznej flagi
    invalidacji do naprawy nowszego planu ani pozostawać pending, gdy ta flaga
    była OFF — zablokowałby wtedy fair downstream przed właściwym callbackiem.
    """
    if not _is_current_active_courier(current, courier_id):
        return False
    # Receipt nie może zostać zamknięty na uszkodzonym store tylko dlatego,
    # że generation guard zabronił mutacji. Odczyt jest strict, ale celowo nie
    # interpretuje pokrycia planu ani nie naprawia go flagami starego eventu.
    from dispatch_v2 import plan_manager

    plan_manager.load_plans(_raise_on_corrupt=True)
    return True


def _cleanup_plan_version(courier_id: str) -> int:
    """Strict CAS token planu odczytany pod lifecycle state lockiem."""
    from dispatch_v2 import plan_manager

    plan = plan_manager.load_plans(_raise_on_corrupt=True).get(str(courier_id))
    version = (plan or {}).get("plan_version", 0)
    if not isinstance(version, int) or isinstance(version, bool):
        raise RuntimeError(
            f"invalid cleanup plan_version cid={courier_id}: {version!r}"
        )
    return version


def _saved_plans_enabled(saved_plans_authorized: Optional[bool]) -> bool:
    if saved_plans_authorized is not None:
        return bool(saved_plans_authorized)
    from dispatch_v2.common import ENABLE_SAVED_PLANS

    return bool(ENABLE_SAVED_PLANS)


def _cleanup_return_courier(
    pw: object,
    courier_id: str,
    order_id: str,
    saved_plans_authorized: Optional[bool],
    recanon_authorized: Optional[bool],
) -> None:
    """Wykonaj jeden retryable cleanup z ochroną nowszej generacji."""
    if not courier_id or not _saved_plans_enabled(saved_plans_authorized):
        return
    # Serializuj state-generation check z cleanupem. CAS planu domyka writerow
    # plan_recheck, którzy nie biorą tego locka.
    cleanup_order_generation: Optional[dict] = None
    with state_machine.lifecycle_apply_lock():
        current_order = state_machine.get_order_strict(order_id)
        cleanup_order_generation = (
            dict(current_order) if isinstance(current_order, dict) else None
        )
        if _protect_current_generation(
            cleanup_order_generation,
            courier_id,
        ):
            return
        expected_plan_version = (
            _cleanup_plan_version(courier_id)
            if saved_plans_authorized is not False
            else None
        )
        pw._remove_stops_on_return(  # type: ignore[attr-defined]
            courier_id,
            order_id,
            _raise_on_error=True,
            _saved_plans_authorized_by_receipt=saved_plans_authorized,
            _recanon_authorized_by_receipt=recanon_authorized,
            _expected_plan_version=expected_plan_version,
            _skip_recanon=True,
        )
    # Retime/recanon może być wolny; state lock chroni wyłącznie check+CAS
    # mutation, zgodnie z kontraktem rozdzielonych lane'ów.
    pw._recanon_after_plan_cleanup(  # type: ignore[attr-defined]
        courier_id,
        order_id,
        reason="return",
        _raise_on_error=True,
        _recanon_authorized_by_receipt=recanon_authorized,
        _expected_order_generation=(order_id, cleanup_order_generation),
    )
    with state_machine.lifecycle_apply_lock():
        _protect_current_generation(
            state_machine.get_order_strict(order_id),
            courier_id,
        )


def apply(event: dict) -> None:
    """Domknij plan/recanon/learning dla jednego trwalego lifecycle eventu."""
    from dispatch_v2 import panel_watcher as pw  # lazy: panel importuje ten modul

    etype = str(event.get("event_type") or "")
    oid = str(event.get("order_id") or "")
    payload = event.get("payload") or {}
    # Natywny tor paczek historycznie nie mutowal courier_plans w tych
    # call-site'ach. C3 atomizuje event->state, ale nie rozszerza przy okazji
    # semantyki biznesowej planu; plan_recheck zachowuje dotychczasowy routing.
    if (
        payload.get("source") in {"parcel_assign", "parcel_status_inbox"}
        and etype != "PICKUP_TIME_UPDATED"
    ):
        return
    # Durable callback nie moze pomylic uszkodzonego/brakujacego pliku z
    # prawdziwym brakiem zlecenia. Wyjatek zostawia receipt do retry.
    current = state_machine.get_order_strict(oid) if oid else None
    courier_id = _cid(event, current)
    lifecycle_event_id = str(event.get("event_id") or "") or None
    saved_plans_authorized = (
        bool(event.get("saved_plans_authorized"))
        if "saved_plans_authorized" in event
        else None
    )
    recanon_authorized = (
        bool(event.get("recanon_on_write_authorized"))
        if "recanon_on_write_authorized" in event
        else None
    )
    redecide_authorized = (
        bool(event.get("immediate_redecide_authorized"))
        if "immediate_redecide_authorized" in event
        else None
    )
    invalidate_authorized = (
        bool(event.get("invalidate_plan_on_bag_change_authorized"))
        if "invalidate_plan_on_bag_change_authorized" in event
        else None
    )
    committed_authorized = (
        bool(event.get("committed_invalidates_view_authorized"))
        if "committed_invalidates_view_authorized" in event
        else None
    )
    panel_agree_authorized = (
        bool(event.get("panel_agree_authorized"))
        if "panel_agree_authorized" in event
        else None
    )
    panel_learning_context = None
    if "panel_learning_context" in event:
        panel_learning_context = event.get("panel_learning_context")
        if not isinstance(panel_learning_context, dict):
            raise RuntimeError("durable panel learning context is not an object")

    if etype == "PICKUP_TIME_UPDATED":
        # Osobny, retryable efekt receipt-bound PO zapisie czasu do state.
        # Funkcja tylko loguje shadow; nie buduje ani nie emituje eventu LIVE.
        from dispatch_v2.czasowka_reclaim import record_pickup_time_shadow

        record_pickup_time_shadow(
            event,
            current,
            enabled_by_receipt=(
                bool(event.get("czasowka_reclaim_shadow_authorized"))
                if "czasowka_reclaim_shadow_authorized" in event
                else None
            ),
        )
        if payload.get("source") in {"parcel_assign", "parcel_status_inbox"}:
            return

    if etype == "COURIER_ASSIGNED":
        # Cleanup provenance belongs to this exact durable event.  Current
        # state may already be two assignments ahead when FIFO reaches it.
        previous_cid = str(event.get("previous_courier_id") or "")
        event_cid = str(event.get("courier_id") or "")
        if (
            previous_cid
            and previous_cid != event_cid
            and event.get("reassign_old_plan_release_authorized") is True
        ):
            if not _saved_plans_enabled(saved_plans_authorized):
                current = state_machine.get_order_strict(oid)
            else:
                release_attempted = False
                release_order_generation: Optional[dict] = None
                with state_machine.lifecycle_apply_lock():
                    current = state_machine.get_order_strict(oid)
                    if not _protect_current_generation(
                        current,
                        previous_cid,
                    ):
                        expected_plan_version = _cleanup_plan_version(previous_cid)
                        release_attempted = True
                        release_order_generation = (
                            dict(current) if isinstance(current, dict) else None
                        )
                        pw._release_plan_on_reassign(
                            previous_cid,
                            oid,
                            _raise_on_error=True,
                            _authorized_by_receipt=True,
                            _saved_plans_authorized_by_receipt=(
                                saved_plans_authorized
                            ),
                            _recanon_authorized_by_receipt=recanon_authorized,
                            _expected_plan_version=expected_plan_version,
                            _skip_recanon=True,
                        )
                if release_attempted:
                    pw._recanon_after_plan_cleanup(
                        previous_cid,
                        oid,
                        reason="reassign_out",
                        _raise_on_error=True,
                        _recanon_authorized_by_receipt=recanon_authorized,
                        _expected_order_generation=(
                            oid,
                            release_order_generation,
                        ),
                    )
                    with state_machine.lifecycle_apply_lock():
                        _protect_current_generation(
                            state_machine.get_order_strict(oid),
                            previous_cid,
                        )
                        # Dalsze guardy ASSIGNED pracuja na swiezym markerze.
                        current = state_machine.get_order_strict(oid)
                else:
                    current = state_machine.get_order_strict(oid)

    if etype == "ORDER_RETURNED_TO_POOL":
        # A later assignment must not redirect an old return callback to the
        # new courier.  Empty means the order really had no courier to prune.
        cleanup_cid = str(
            event.get("courier_id") or event.get("previous_courier_id") or ""
        )
        _cleanup_return_courier(
            pw,
            cleanup_cid,
            oid,
            saved_plans_authorized,
            recanon_authorized,
        )
        # Disappeared-panel path ma dwa niezalezne zrodla CID: raw moze juz
        # wskazywac innego kuriera, current z chwili atomowej tranzycji trzeciego,
        # a snapshot nadal identyfikuje plan do skurczenia. Snapshot jest zapisany
        # w exact state_event PRZED callbackiem, więc crash/błąd zostawia
        # downstream=pending dla sweepa.
        snapshot_cleanup_cid = str(
            payload.get("return_snapshot_cleanup_courier_id") or ""
        )
        previous_cid = str(event.get("previous_courier_id") or "")
        # Nie tylko disappeared-panel ma dwa zrodla CID. Kazdy RETURN moze
        # zostac wyemitowany ze starego snapshotu, gdy atomowy pre-read widzi
        # juz innego kuriera; exact previous_courier_id musi wtedy zostac
        # skonsumowany nawet bez flagowanego snapshot payloadu.
        if (
            (
                snapshot_cleanup_cid
                or event.get("return_previous_cleanup_authorized") is True
            )
            and previous_cid
            and previous_cid != cleanup_cid
            and previous_cid != snapshot_cleanup_cid
        ):
            _cleanup_return_courier(
                pw,
                previous_cid,
                oid,
                saved_plans_authorized,
                recanon_authorized,
            )
        if snapshot_cleanup_cid:
            # Obecnosc snapshot CID jest trwala autoryzacja z chwili enqueue.
            # OFF blokuje nowe wpisy, ale nie moze terminalizowac rozpoczetego
            # callbacku pomiedzy remove_stops i recanonem.
            # Powtórzenie tego samego CID jest celowe: zachowuje dotychczasowy
            # call graph pięciu przypiętych przypadków, a remove_stops ma
            # store-level idempotencję. Tryb strict nie pozwala domknąć receiptu
            # po częściowym remove bez trwałego recanonu.
            _cleanup_return_courier(
                pw,
                snapshot_cleanup_cid,
                oid,
                saved_plans_authorized,
                recanon_authorized,
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
                _enabled_by_receipt=panel_agree_authorized,
                _context_by_receipt=panel_learning_context,
            )
            pw._check_panel_override(
                oid,
                courier_id,
                source,
                lifecycle_event_id=lifecycle_event_id,
                _raise_on_error=True,
                _context_by_receipt=panel_learning_context,
            )
        pw._save_plan_on_assign_signal(
            oid,
            courier_id,
            _raise_on_error=True,
            _saved_plans_authorized_by_receipt=saved_plans_authorized,
            _recanon_authorized_by_receipt=recanon_authorized,
            _redecide_authorized_by_receipt=redecide_authorized,
            _invalidate_authorized_by_receipt=invalidate_authorized,
        )
        return

    if etype == "COURIER_DELIVERED":
        pw._advance_plan_on_deliver(
            courier_id,
            oid,
            payload.get("timestamp"),
            (current or {}).get("delivery_coords"),
            _raise_on_error=True,
            _saved_plans_authorized_by_receipt=saved_plans_authorized,
            _recanon_authorized_by_receipt=recanon_authorized,
        )
        return

    if etype == "COURIER_PICKED_UP":
        # State lane moze wyprzedzic downstream: PICKUP T1, DELIVERED T2 i
        # RESURRECTED T3 sa juz zastosowane, gdy retry T1 dochodzi do planu.
        # Nie wolno wtedy oznaczyc nowej aktywnej generacji jako picked_up.
        if (
            (current or {}).get("status") != "picked_up"
            or not courier_id
            or str((current or {}).get("courier_id") or "") != courier_id
        ):
            return
        pw._update_plan_on_picked_up(
            courier_id,
            oid,
            payload.get("timestamp"),
            _raise_on_error=True,
            _saved_plans_authorized_by_receipt=saved_plans_authorized,
            _recanon_authorized_by_receipt=recanon_authorized,
            _redecide_authorized_by_receipt=redecide_authorized,
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
            oid,
            courier_id,
            _raise_on_error=True,
            _saved_plans_authorized_by_receipt=saved_plans_authorized,
            _enabled_by_receipt=invalidate_authorized,
        )
        return

    if etype in ("CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"):
        pw._invalidate_plan_on_committed_change(
            oid,
            courier_id,
            _raise_on_error=True,
            _saved_plans_authorized_by_receipt=saved_plans_authorized,
            _enabled_by_receipt=committed_authorized,
        )
        return

    # NEW_ORDER nie ma ogolnego plan/recanon downstream. Gated AUTO_KOORD jest
    # zewnetrznym poleceniem panelowym i zachowuje dotychczasowy event-created
    # kontrakt w call-site (nie udajemy exactly-once dla obcej uslugi).
