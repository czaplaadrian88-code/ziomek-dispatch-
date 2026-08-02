#!/usr/bin/env python3
"""Mechanical preflight for reverting the committed-pickup code contract.

Hot rollback is the decision flag set to OFF and needs no data conversion.
A code revert to pre-v4 readers is a separate, guarded operation: all exact
authority transactions must be terminal, the v4 queue must be fenced against
new writers, and pending unclaimed receipts must be projected to legacy ISO
timestamps only after an exact durable backup.

This tool never drains or mutates the durable event outbox. Any unfinished
authority row is a hard blocker to code rollback.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Mapping, Sequence

_HERE = os.path.dirname(os.path.abspath(__file__))
_PACKAGE_ROOT = os.path.dirname(_HERE)
_SCRIPTS_ROOT = os.path.dirname(_PACKAGE_ROOT)
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)

from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2 import coordinator_time_recheck as queue  # noqa: E402
from dispatch_v2 import event_bus  # noqa: E402
from dispatch_v2 import state_machine  # noqa: E402
from dispatch_v2.committed_pickup_authority import (  # noqa: E402
    ASSIGNMENT_CK_FORWARD_SNAPSHOT_FIELD,
    ASSIGNMENT_CK_PASSIVE_SNAPSHOT_FIELD,
    COMMITTED_PICKUP_AUTHORITY_FLAGS,
    MANUAL_CK_AUTHORITY_FLAG,
    NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD,
    NEW_ORDER_TIME_INTENT_FIELD,
    RUTCOM_FORWARD_AUTHORITY_FLAG,
    committed_time_contract_is_complete,
    is_forward_authority_outbox_artifact,
    is_committed_pickup_outbox_artifact,
    state_has_committed_pickup_artifact,
)

FLAG = RUTCOM_FORWARD_AUTHORITY_FLAG
AUTHORITY_FLAGS = (
    MANUAL_CK_AUTHORITY_FLAG,
    RUTCOM_FORWARD_AUTHORITY_FLAG,
)
if AUTHORITY_FLAGS != COMMITTED_PICKUP_AUTHORITY_FLAGS:
    raise RuntimeError("committed authority flag tuple drift")

FORWARD_WRITER_UNITS = (
    "dispatch-panel-watcher.service",
    "dispatch-shadow.service",
)


def _pre_v4_coordinator_time_row_blocks_forward(
    row: object,
    orders_state: Mapping[str, object],
) -> bool:
    """Detect old raw coordinator time work the v4 authority must not reinterpret.

    The forward deploy gate drains this work under the old writer while writers
    are quiesced. Runtime does not gain a source-label fallback for it.
    """
    if not isinstance(row, Mapping):
        return False
    event = row.get("state_event")
    if not isinstance(event, Mapping):
        return False
    payload = event.get("payload")
    event_type = event.get("event_type")
    if (
        event_type not in {"CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"}
        or not isinstance(payload, Mapping)
        or payload.get("source") != "coordinator_force"
        or payload.get("committed_authority") is not None
    ):
        return False
    order_id = str(event.get("order_id") or "")
    if (
        not order_id
        or str(row.get("order_id") or "") != order_id
    ):
        return True
    order = orders_state.get(order_id)
    if not isinstance(order, Mapping):
        return True
    # Tylko rekord sklasyfikowany przez kanoniczny owner jako nie-czasówka
    # zachowuje zgodną legacy semantykę. Brak rekordu nie jest takim dowodem.
    return bool(C.is_czasowka_order(order))


def _pre_v16_assignment_ck_row_blocks_forward(
    row: object,
    orders_state: Mapping[str, object],
) -> bool:
    """Detect an unfinished assignment whose CK policy still hot-reads flags."""
    if not isinstance(row, Mapping):
        return False
    event = row.get("state_event")
    if not isinstance(event, Mapping) or event.get("event_type") != (
        "COURIER_ASSIGNED"
    ):
        return False
    has_forward = ASSIGNMENT_CK_FORWARD_SNAPSHOT_FIELD in event
    has_passive = ASSIGNMENT_CK_PASSIVE_SNAPSHOT_FIELD in event
    if has_forward or has_passive:
        # Pełna para jest receipt-bound; częściowa para jest trwale fail-closed.
        # W obu przypadkach semantyka nie zależy już od późniejszego flipu.
        return False
    payload = event.get("payload")
    if not isinstance(payload, Mapping) or not any(
        payload.get(field) not in (None, "")
        for field in ("czas_kuriera_warsaw", "czas_kuriera_hhmm")
    ):
        return False
    order_id = str(event.get("order_id") or "")
    if not order_id or str(row.get("order_id") or "") != order_id:
        return True
    order = orders_state.get(order_id)
    if not isinstance(order, Mapping):
        return True
    # Tylko jawny elastyk dowodzi, że CK assignmentu nie zmieni semantyki po
    # włączeniu ownera czasówki. Brak typu to dokładnie thin cold-start i blokuje.
    return str(order.get("order_type") or "") != "elastic"


def _active_time_contract_incomplete(order: object) -> bool:
    """Return True for active gastro state unsafe for a forward-authority flip."""
    if not isinstance(order, Mapping):
        return False
    if order.get("status") in {"delivered", "returned_to_pool", "cancelled"}:
        return False
    if order.get("source") == "parcel" or order.get("order_type") == "parcel":
        return False
    # Release gate musi używać dokładnie tego samego klasyfikatora co producer
    # i FSM. Legacy rekord może nie mieć order_type, a nadal być czasówką z
    # kanonicznego prep>=60; brak kuriera nie zmienia jego kontraktu czasu.
    if C.is_czasowka_order(dict(order)):
        return not committed_time_contract_is_complete(order)
    order_type = str(order.get("order_type") or "")
    if order_type == "elastic":
        return False
    # Historyczny thin cold-start nie utrwalił order_type ani źródła, ale ma
    # aktywne przypisanie. Nie zgadujemy jego klasy przy zmianie authority.
    return bool(order.get("courier_id"))


def _unbound_new_order_time_row_blocks_forward(row: object) -> bool:
    """Detect NEW_ORDER work that could create a split tuple after the flip."""
    if not isinstance(row, Mapping):
        return False
    event = row.get("state_event")
    if not isinstance(event, Mapping) or event.get("event_type") != "NEW_ORDER":
        return False
    # Presence is enough. Older readers do not understand either top-level
    # field, regardless of the sanitized payload or truthiness of its value.
    if (
        NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD in event
        or NEW_ORDER_TIME_INTENT_FIELD in event
    ):
        return True
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return True
    if (
        payload.get("source") == "parcel"
        or payload.get("order_type") == "parcel"
    ):
        return False
    if C.is_czasowka_order(dict(payload)):
        return True
    if payload.get("order_type") == "elastic":
        return False
    looks_like_time_order = bool(
        C.is_czasowka_order(dict(payload))
        or any(
            payload.get(field) not in (None, "")
            for field in (
                "pickup_at_warsaw",
                "czas_kuriera_warsaw",
                "czas_kuriera_hhmm",
            )
        )
    )
    if not looks_like_time_order:
        return False
    # Even a receipt-bound/sanitized NEW_ORDER is not safe while unfinished:
    # the outbox commits before state apply, so it may still create a pending
    # aggregate shell after a supposedly green flip. Drain every time-order
    # NEW_ORDER under quiescence; the current state scan then owns the handoff.
    return True


def _probe_forward_writer_quiescence() -> tuple[bool, dict]:
    """Verify the exact production writer units are loaded and inactive."""
    states = {}
    for unit in FORWARD_WRITER_UNITS:
        try:
            result = subprocess.run(
                [
                    "systemctl",
                    "show",
                    unit,
                    "--property=LoadState",
                    "--property=ActiveState",
                    "--no-pager",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            parsed = {}
            for line in result.stdout.splitlines():
                key, separator, value = line.partition("=")
                if separator:
                    parsed[key] = value
            states[unit] = {
                "load_state": parsed.get("LoadState"),
                "active_state": parsed.get("ActiveState"),
                "probe_exit": result.returncode,
            }
        except Exception as exc:  # fail closed; expose only exception class
            states[unit] = {
                "load_state": None,
                "active_state": None,
                "probe_exit": None,
                "probe_error": type(exc).__name__,
            }
    verified = all(
        state.get("load_state") == "loaded"
        and state.get("active_state") == "inactive"
        and state.get("probe_exit") == 0
        for state in states.values()
    ) and set(states) == set(FORWARD_WRITER_UNITS)
    return bool(verified), states


def collect_status(
    *,
    writer_quiescence_verified: bool = False,
    writer_states: Mapping[str, object] | None = None,
) -> dict:
    unfinished = event_bus.list_unfinished_state_applies()
    authority_rows = [
        row
        for row in unfinished
        if is_committed_pickup_outbox_artifact(row)
    ]
    queue_status = queue.legacy_rollback_status()
    authority_flag_states = {
        MANUAL_CK_AUTHORITY_FLAG: C.decision_flag(
            MANUAL_CK_AUTHORITY_FLAG
        ),
        RUTCOM_FORWARD_AUTHORITY_FLAG: C.decision_flag(
            RUTCOM_FORWARD_AUTHORITY_FLAG
        ),
    }
    enabled_authority_flags = [
        name for name, enabled in authority_flag_states.items() if enabled
    ]
    flag_enabled = authority_flag_states[FLAG]
    try:
        state_snapshot = state_machine.get_all_strict()
        state_scan_ok = bool(
            isinstance(state_snapshot, dict)
            and all(
                isinstance(order, dict)
                for order in state_snapshot.values()
            )
        )
        orders_state = state_snapshot if isinstance(state_snapshot, dict) else {}
    except Exception:
        orders_state = {}
        state_scan_ok = False
    active_committed_state_count = sum(
        1
        for order in orders_state.values()
        if isinstance(order, dict)
        and order.get("status")
        not in {"delivered", "returned_to_pool", "cancelled"}
        and state_has_committed_pickup_artifact(order)
    )
    active_incomplete_time_contract_count = sum(
        1
        for order in orders_state.values()
        if _active_time_contract_incomplete(order)
    )
    forward_authority_rows = []
    for row in unfinished:
        current_order = (
            orders_state.get(str(row.get("order_id") or ""))
            if isinstance(row, Mapping)
            else None
        )
        if is_forward_authority_outbox_artifact(
            row,
            current_order,
            is_czasowka=bool(
                isinstance(current_order, Mapping)
                and C.is_czasowka_order(dict(current_order))
            ),
        ):
            forward_authority_rows.append(row)
    unbound_new_order_time_rows = [
        row
        for row in unfinished
        if _unbound_new_order_time_row_blocks_forward(row)
    ]
    pre_v4_coordinator_time_rows = [
        row
        for row in unfinished
        if _pre_v4_coordinator_time_row_blocks_forward(row, orders_state)
    ]
    pre_v16_assignment_ck_rows = [
        row
        for row in unfinished
        if _pre_v16_assignment_ck_row_blocks_forward(row, orders_state)
    ]
    safe_for_forward_deploy = bool(
        not flag_enabled
        and writer_quiescence_verified
        and state_scan_ok
        and queue_status["records"] == 0
        # Każdy unfinished row z kanonicznej klasy authority/raw CK musi
        # terminalizować się przed flipem. Inaczej ten sam durable event może
        # mieć inny handler/oracle outcome po zmianie flagi. Szczegółowe
        # pre-v4/pre-v16 liczniki niżej są diagnostyką, nie konkurencyjną
        # definicją bezpieczeństwa.
        and not forward_authority_rows
        and not unbound_new_order_time_rows
        and not pre_v4_coordinator_time_rows
        and not pre_v16_assignment_ck_rows
        and active_incomplete_time_contract_count == 0
    )
    common_safe = bool(
        not enabled_authority_flags
        and not authority_rows
        and queue_status["safe_queue_projection"]
        and state_scan_ok
        and active_committed_state_count == 0
        and not unbound_new_order_time_rows
    )
    safe_to_prepare = bool(
        common_safe and not queue_status["rollback_fence_present"]
    )
    safe_for_code_revert = bool(
        common_safe
        and queue_status["rollback_fence_present"]
        and queue_status["rollback_prepared"]
        and queue_status["pending_v4_records"] == 0
        and queue_status["claimed_records"] == 0
        and queue_status["successor_records"] == 0
    )
    return {
        "schema": "rutcom_committed_authority.rollback_preflight.v4",
        "flag": FLAG,
        "flag_enabled": flag_enabled,
        "authority_flags": list(AUTHORITY_FLAGS),
        "authority_flag_states": authority_flag_states,
        "enabled_authority_flags": enabled_authority_flags,
        "unfinished_outbox_total": len(unfinished),
        "unfinished_authority_outbox": len(authority_rows),
        "unfinished_authority_event_ids": [
            str(row.get("event_id") or "") for row in authority_rows
        ],
        "unfinished_forward_authority_outbox": len(
            forward_authority_rows
        ),
        "unfinished_forward_authority_event_ids": [
            str(row.get("event_id") or "")
            for row in forward_authority_rows
        ],
        "unfinished_unbound_new_order_time_outbox": len(
            unbound_new_order_time_rows
        ),
        "unfinished_unbound_new_order_time_event_ids": [
            str(row.get("event_id") or "")
            for row in unbound_new_order_time_rows
        ],
        "state_scan_ok": state_scan_ok,
        "active_committed_state_count": active_committed_state_count,
        "active_incomplete_time_contract_count": (
            active_incomplete_time_contract_count
        ),
        "unfinished_pre_v4_coordinator_time_outbox": len(
            pre_v4_coordinator_time_rows
        ),
        "unfinished_pre_v4_coordinator_time_event_ids": [
            str(row.get("event_id") or "")
            for row in pre_v4_coordinator_time_rows
        ],
        "unfinished_pre_v16_assignment_ck_outbox": len(
            pre_v16_assignment_ck_rows
        ),
        "unfinished_pre_v16_assignment_ck_event_ids": [
            str(row.get("event_id") or "")
            for row in pre_v16_assignment_ck_rows
        ],
        "queue": queue_status,
        "writer_quiescence_verified": bool(writer_quiescence_verified),
        "writer_states": dict(writer_states or {}),
        "safe_for_forward_deploy": safe_for_forward_deploy,
        "safe_to_prepare": safe_to_prepare,
        "safe_for_code_revert": safe_for_code_revert,
    }


def _print(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _cmd_status(_args: argparse.Namespace) -> int:
    status = collect_status()
    _print(status)
    return 0 if status["safe_for_code_revert"] else 1


def _cmd_forward_status(args: argparse.Namespace) -> int:
    if not args.quiesced:
        raise RuntimeError(
            "forward-status requires --quiesced after stopping all forward "
            "writer units; the tool also verifies systemd mechanically"
        )
    quiesced, writer_states = _probe_forward_writer_quiescence()
    status = collect_status(
        writer_quiescence_verified=quiesced,
        writer_states=writer_states,
    )
    _print(status)
    return 0 if status["safe_for_forward_deploy"] else 1


def _cmd_prepare(args: argparse.Namespace) -> int:
    if not args.apply or not args.quiesced:
        raise RuntimeError(
            "prepare requires both --apply and --quiesced; quiesce all code "
            "writers and verify the effective OFF fingerprint first"
        )
    before = collect_status()
    if not before["safe_to_prepare"]:
        _print({"prepared": False, "before": before})
        return 2
    receipt = queue.prepare_legacy_rollback(args.queue_backup)
    after = collect_status()
    result = {
        "prepared": after["safe_for_code_revert"],
        "queue_conversion_receipt": receipt,
        "before": before,
        "after": after,
    }
    _print(result)
    return 0 if result["prepared"] else 3


def _cmd_release_fence(args: argparse.Namespace) -> int:
    if not args.apply or not args.v4_code_active:
        raise RuntimeError(
            "release-fence requires --apply and --v4-code-active after the "
            "v4 processes and OFF fingerprint are verified"
        )
    before = collect_status()
    if (
        before["enabled_authority_flags"]
        or before["unfinished_authority_outbox"]
        or not before["queue"]["safe_queue_projection"]
    ):
        _print({"released": False, "before": before})
        return 2
    released = queue.release_legacy_rollback_fence()
    after = collect_status()
    result = {
        "released": released and not after["queue"]["rollback_fence_present"],
        "before": before,
        "after": after,
    }
    _print(result)
    return 0 if result["released"] else 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="read-only rollback preflight")
    status.set_defaults(func=_cmd_status)

    forward_status = sub.add_parser(
        "forward-status",
        help="read-only dark-deploy compatibility preflight",
    )
    forward_status.add_argument("--quiesced", action="store_true")
    forward_status.set_defaults(func=_cmd_forward_status)

    prepare = sub.add_parser(
        "prepare",
        help="fence v4 queue and project safe pending receipts to legacy ISO",
    )
    prepare.add_argument("--queue-backup", required=True)
    prepare.add_argument("--quiesced", action="store_true")
    prepare.add_argument("--apply", action="store_true")
    prepare.set_defaults(func=_cmd_prepare)

    release = sub.add_parser(
        "release-fence",
        help="re-open v4 queue writers after a verified roll-forward",
    )
    release.add_argument("--v4-code-active", action="store_true")
    release.add_argument("--apply", action="store_true")
    release.set_defaults(func=_cmd_release_fence)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # fail closed for operator automation
        _print({"error": type(exc).__name__, "message": str(exc)})
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
