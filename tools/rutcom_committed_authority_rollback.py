#!/usr/bin/env python3
"""Mechanical preflight for reverting the committed-pickup code contract.

Hot rollback is the decision flag set to OFF and needs no data conversion.
A code revert to pre-v4 readers is a separate, guarded operation: all exact
authority transactions must be terminal and the exact empty queue must be
fenced after a durable backup. The fence binds one roll-forward code manifest;
it can be released only by the same transaction ID after those exact deployed
bytes are measured again with every writer mechanically quiesced.

This tool never drains or mutates the durable event outbox. Any unfinished
authority row is a hard blocker to code rollback.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
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
    project_time_event_order,
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
DEPLOYED_DISPATCH_ROOT = Path(
    "/root/.openclaw/workspace/scripts/dispatch_v2"
)
DEPLOYED_SCRIPTS_ROOT = DEPLOYED_DISPATCH_ROOT.parent
DISPATCH_PYTHON = "/root/.openclaw/venvs/dispatch/bin/python"
FORWARD_WRITER_UNIT_MODULES = {
    "dispatch-panel-watcher.service": "dispatch_v2.panel_watcher",
    "dispatch-shadow.service": "dispatch_v2.shadow_dispatcher",
}
if tuple(FORWARD_WRITER_UNIT_MODULES) != FORWARD_WRITER_UNITS:
    raise RuntimeError("forward writer unit module registry drift")


def _deployed_rollforward_code_manifest() -> dict:
    """Hash the exact canonical deployment; no caller-selected root exists."""
    root = Path(DEPLOYED_DISPATCH_ROOT).resolve(strict=True)
    if not root.is_dir():
        raise RuntimeError("canonical deployed dispatch root is not a directory")
    runtime_root = Path(_PACKAGE_ROOT).resolve(strict=True)
    if runtime_root != root:
        raise RuntimeError(
            "mutating rollback tool must execute from canonical deployed root"
        )
    files = {}
    for relative_path in queue.ROLLFORWARD_CODE_PATHS:
        declared = root / relative_path
        if declared.is_symlink():
            raise RuntimeError(
                f"deployed roll-forward file is a symlink: {relative_path}"
            )
        target = declared.resolve(strict=True)
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise RuntimeError(
                f"deployed roll-forward file escapes root: {relative_path}"
            ) from exc
        if not target.is_file():
            raise RuntimeError(
                f"deployed roll-forward path is not a file: {relative_path}"
            )
        files[relative_path] = hashlib.sha256(target.read_bytes()).hexdigest()
    return queue.build_rollforward_code_manifest(files)


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
                    "--property=WorkingDirectory",
                    "--property=ExecStart",
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
                "working_directory": parsed.get("WorkingDirectory"),
                "exec_start": parsed.get("ExecStart"),
                "probe_exit": result.returncode,
            }
        except Exception as exc:  # fail closed; expose only exception class
            states[unit] = {
                "load_state": None,
                "active_state": None,
                "working_directory": None,
                "exec_start": None,
                "probe_exit": None,
                "probe_error": type(exc).__name__,
            }
    for unit, state in states.items():
        expected_module = FORWARD_WRITER_UNIT_MODULES.get(unit)
        exec_start = str(state.get("exec_start") or "")
        state["target_mode_verified"] = bool(
            expected_module
            and state.get("working_directory")
            == str(DEPLOYED_SCRIPTS_ROOT)
            and f"path={DISPATCH_PYTHON} ;" in exec_start
            and (
                f"argv[]={DISPATCH_PYTHON} -m {expected_module} ;"
                in exec_start
            )
            and exec_start.count("argv[]=") == 1
        )
    verified = bool(
        set(states) == set(FORWARD_WRITER_UNITS)
        and all(
            state.get("load_state") == "loaded"
            and state.get("active_state") == "inactive"
            and state.get("probe_exit") == 0
            and state.get("target_mode_verified") is True
            for state in states.values()
        )
    )
    return bool(verified), states


def collect_status(
    *,
    writer_quiescence_verified: bool = False,
    writer_states: Mapping[str, object] | None = None,
    deployed_code_manifest: Mapping[str, object] | None = None,
) -> dict:
    unfinished = event_bus.list_unfinished_state_applies()
    authority_rows = [
        row
        for row in unfinished
        if is_committed_pickup_outbox_artifact(row)
    ]
    queue_status = queue.legacy_rollback_status()
    try:
        forward_fence = queue.forward_rollout_fence_status()
    except Exception:
        forward_fence = {
            "forward_fence_present": False,
            "forward_fence_valid": False,
            "forward_fence_error": "fence_status_unreadable",
            "forward_fence_id": None,
            "forward_fence_queue_sha256": None,
        }
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
    try:
        queue_records = queue.rollback_records_snapshot()
        queue_records_scan_ok = isinstance(queue_records, dict)
        if not queue_records_scan_ok:
            queue_records = {}
    except Exception:
        queue_records = {}
        queue_records_scan_ok = False
    forward_blocking_queue_records = 0
    forward_ignored_elastic_queue_records = 0
    for raw_order_id, record in queue_records.items():
        order_id = str(raw_order_id)
        order = orders_state.get(order_id)
        receipt_policy = queue.receipt_policy_snapshot(record)
        stable_unclaimed_elastic = bool(
            queue.rollback_record_is_unclaimed(
                record,
                order_id=order_id,
            )
            and receipt_policy is not None
            and isinstance(order, Mapping)
            and str(order.get("order_type") or "").strip().lower()
            == "elastic"
            and order.get("status")
            not in {"delivered", "returned_to_pool", "cancelled"}
            and not C.is_czasowka_order(dict(order))
            and not state_has_committed_pickup_artifact(order)
        )
        if stable_unclaimed_elastic:
            forward_ignored_elastic_queue_records += 1
        else:
            forward_blocking_queue_records += 1
    queue_record_count_matches_status = bool(
        queue_records_scan_ok
        and len(queue_records) == int(queue_status.get("records", -1))
    )
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
        row_event = (
            row.get("state_event") if isinstance(row, Mapping) else None
        )
        projected_order = project_time_event_order(
            current_order,
            row_event if isinstance(row_event, Mapping) else None,
        )
        if is_forward_authority_outbox_artifact(
            row,
            current_order,
            is_czasowka=bool(
                isinstance(current_order, Mapping)
                and C.is_czasowka_order(projected_order)
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
        and forward_fence["forward_fence_valid"]
        and state_scan_ok
        and queue_records_scan_ok
        and queue_record_count_matches_status
        and forward_blocking_queue_records == 0
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
        and writer_quiescence_verified
        and not forward_fence["forward_fence_present"]
        and queue_status["safe_empty_queue"]
        and state_scan_ok
        and active_committed_state_count == 0
        and not unbound_new_order_time_rows
    )
    safe_to_prepare = bool(
        common_safe and not queue_status["rollback_fence_present"]
    )
    rollback_target_code_verified = bool(
        queue_status["rollback_fence_present"]
        and queue_status["rollback_prepared"]
        and isinstance(deployed_code_manifest, Mapping)
        and queue_status.get("rollback_rollforward_code_manifest")
        == dict(deployed_code_manifest)
    )
    safe_for_code_revert = bool(
        common_safe
        and queue_status["rollback_fence_present"]
        and queue_status["rollback_prepared"]
        and rollback_target_code_verified
        and queue_status["pending_pre_policy_records"] == 0
        and queue_status["claimed_records"] == 0
        and queue_status["successor_records"] == 0
    )
    return {
        "schema": "rutcom_committed_authority.rollback_preflight.v5",
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
        "forward_fence": forward_fence,
        "queue_records_scan_ok": queue_records_scan_ok,
        "queue_record_count_matches_status": (
            queue_record_count_matches_status
        ),
        "forward_blocking_queue_records": (
            forward_blocking_queue_records
        ),
        "forward_ignored_elastic_queue_records": (
            forward_ignored_elastic_queue_records
        ),
        "writer_quiescence_verified": bool(writer_quiescence_verified),
        "writer_states": dict(writer_states or {}),
        "rollback_target_code_verified": rollback_target_code_verified,
        "safe_for_forward_deploy": safe_for_forward_deploy,
        "safe_to_prepare": safe_to_prepare,
        "safe_for_code_revert": safe_for_code_revert,
    }


def _print(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _cmd_status(args: argparse.Namespace) -> int:
    quiesced = False
    writer_states = None
    if args.quiesced:
        quiesced, writer_states = _probe_forward_writer_quiescence()
    status = collect_status(
        writer_quiescence_verified=quiesced,
        writer_states=writer_states,
    )
    if (
        quiesced
        and status["queue"]["rollback_fence_present"]
    ):
        deployed_code_manifest = _deployed_rollforward_code_manifest()
        status = collect_status(
            writer_quiescence_verified=quiesced,
            writer_states=writer_states,
            deployed_code_manifest=deployed_code_manifest,
        )
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


def _cmd_fence_forward(args: argparse.Namespace) -> int:
    if not args.apply or not args.quiesced:
        raise RuntimeError(
            "fence-forward requires both --apply and --quiesced after "
            "stopping all forward writer units"
        )
    before_quiesced, before_states = _probe_forward_writer_quiescence()
    if not before_quiesced:
        status = collect_status(
            writer_quiescence_verified=False,
            writer_states=before_states,
        )
        _print({"fenced": False, "status": status})
        return 2
    fence_receipt = queue.acquire_forward_rollout_fence()
    after_quiesced, after_states = _probe_forward_writer_quiescence()
    status = collect_status(
        writer_quiescence_verified=after_quiesced,
        writer_states=after_states,
    )
    result = {
        "fenced": bool(status["forward_fence"]["forward_fence_valid"]),
        "ready": bool(status["safe_for_forward_deploy"]),
        "fence_receipt": fence_receipt,
        "status": status,
    }
    _print(result)
    return 0 if result["ready"] else 3


def _cmd_release_forward_fence(args: argparse.Namespace) -> int:
    if not args.apply or not args.quiesced:
        raise RuntimeError(
            "release-forward-fence requires --apply and --quiesced"
        )
    if bool(args.authority_active) == bool(args.abort_off):
        raise RuntimeError(
            "release-forward-fence requires exactly one of "
            "--authority-active or --abort-off"
        )
    quiesced, writer_states = _probe_forward_writer_quiescence()
    if not quiesced:
        _print(
            {
                "released": False,
                "writer_quiescence_verified": False,
                "writer_states": writer_states,
            }
        )
        return 2
    flag_enabled = bool(C.decision_flag(FLAG))
    if args.authority_active and not flag_enabled:
        raise RuntimeError("authority-active acknowledgement mismatches OFF flag")
    if args.abort_off and flag_enabled:
        raise RuntimeError("abort-off acknowledgement mismatches ON flag")
    released = queue.release_forward_rollout_fence(args.fence_id)
    after = queue.forward_rollout_fence_status()
    result = {
        "released": bool(released and not after["forward_fence_present"]),
        "flag_enabled": flag_enabled,
        "after": after,
    }
    _print(result)
    return 0 if result["released"] else 3


def _cmd_prepare(args: argparse.Namespace) -> int:
    if not args.apply or not args.quiesced:
        raise RuntimeError(
            "prepare requires both --apply and --quiesced; quiesce all code "
            "writers and verify the effective OFF fingerprint first"
        )
    before_quiesced, before_writer_states = (
        _probe_forward_writer_quiescence()
    )
    before = collect_status(
        writer_quiescence_verified=before_quiesced,
        writer_states=before_writer_states,
    )
    if not before["safe_to_prepare"]:
        _print({"prepared": False, "before": before})
        return 2
    rollforward_code_manifest = _deployed_rollforward_code_manifest()
    receipt = queue.prepare_legacy_rollback(
        args.queue_backup,
        rollforward_code_manifest,
    )
    after_code_manifest = _deployed_rollforward_code_manifest()
    code_manifest_stable = after_code_manifest == rollforward_code_manifest
    after_quiesced, after_writer_states = (
        _probe_forward_writer_quiescence()
    )
    after = collect_status(
        writer_quiescence_verified=after_quiesced,
        writer_states=after_writer_states,
        deployed_code_manifest=after_code_manifest,
    )
    result = {
        "prepared": bool(
            after["safe_for_code_revert"]
            and code_manifest_stable
            and receipt.get("rollforward_code_manifest_sha256")
            == rollforward_code_manifest["manifest_sha256"]
        ),
        "queue_fence_receipt": receipt,
        "rollforward_code_manifest_stable": code_manifest_stable,
        "rollforward_code_manifest_sha256": rollforward_code_manifest[
            "manifest_sha256"
        ],
        "before": before,
        "after": after,
    }
    _print(result)
    return 0 if result["prepared"] else 3


def _cmd_release_fence(args: argparse.Namespace) -> int:
    if not args.apply or not args.quiesced:
        raise RuntimeError(
            "release-fence requires --apply and --quiesced"
        )
    quiesced, writer_states = _probe_forward_writer_quiescence()
    if not quiesced:
        _print(
            {
                "released": False,
                "writer_quiescence_verified": False,
                "writer_states": writer_states,
            }
        )
        return 2
    before = collect_status(
        writer_quiescence_verified=quiesced,
        writer_states=writer_states,
    )
    if (
        before["enabled_authority_flags"]
        or before["unfinished_authority_outbox"]
        or not before["queue"]["safe_empty_queue"]
    ):
        _print({"released": False, "before": before})
        return 2
    active_code_manifest = _deployed_rollforward_code_manifest()
    before = collect_status(
        writer_quiescence_verified=quiesced,
        writer_states=writer_states,
        deployed_code_manifest=active_code_manifest,
    )
    if (
        before["enabled_authority_flags"]
        or before["unfinished_authority_outbox"]
        or not before["queue"]["safe_empty_queue"]
        or not before["rollback_target_code_verified"]
    ):
        _print({"released": False, "before": before})
        return 2
    pre_release_manifest = _deployed_rollforward_code_manifest()
    if pre_release_manifest != active_code_manifest:
        _print(
            {
                "released": False,
                "code_manifest_stable": False,
                "before": before,
            }
        )
        return 2
    released = queue.release_legacy_rollback_fence(
        args.fence_id,
        active_code_manifest,
    )
    after_code_manifest = _deployed_rollforward_code_manifest()
    code_manifest_stable = after_code_manifest == active_code_manifest
    after_quiesced, after_writer_states = _probe_forward_writer_quiescence()
    after = collect_status(
        writer_quiescence_verified=after_quiesced,
        writer_states=after_writer_states,
        deployed_code_manifest=after_code_manifest,
    )
    result = {
        "released": bool(
            released
            and after_quiesced
            and code_manifest_stable
            and not after["queue"]["rollback_fence_present"]
        ),
        "code_manifest_stable": code_manifest_stable,
        "active_code_manifest_sha256": active_code_manifest[
            "manifest_sha256"
        ],
        "before": before,
        "after": after,
    }
    _print(result)
    return 0 if result["released"] else 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="read-only rollback preflight")
    status.add_argument("--quiesced", action="store_true")
    status.set_defaults(func=_cmd_status)

    forward_status = sub.add_parser(
        "forward-status",
        help="read-only dark-deploy compatibility preflight",
    )
    forward_status.add_argument("--quiesced", action="store_true")
    forward_status.set_defaults(func=_cmd_forward_status)

    fence_forward = sub.add_parser(
        "fence-forward",
        help="atomically fence coordinator enqueue before forward flag flip",
    )
    fence_forward.add_argument("--quiesced", action="store_true")
    fence_forward.add_argument("--apply", action="store_true")
    fence_forward.set_defaults(func=_cmd_fence_forward)

    release_forward = sub.add_parser(
        "release-forward-fence",
        help="release the exact forward fence after ON or an OFF abort",
    )
    release_forward.add_argument("--fence-id", required=True)
    release_forward.add_argument("--quiesced", action="store_true")
    release_forward.add_argument("--authority-active", action="store_true")
    release_forward.add_argument("--abort-off", action="store_true")
    release_forward.add_argument("--apply", action="store_true")
    release_forward.set_defaults(func=_cmd_release_forward_fence)

    prepare = sub.add_parser(
        "prepare",
        help="fence one exact empty queue and bind deployed roll-forward code",
    )
    prepare.add_argument("--queue-backup", required=True)
    prepare.add_argument("--quiesced", action="store_true")
    prepare.add_argument("--apply", action="store_true")
    prepare.set_defaults(func=_cmd_prepare)

    release = sub.add_parser(
        "release-fence",
        help="release exact fence after measured roll-forward and quiescence",
    )
    release.add_argument("--fence-id", required=True)
    release.add_argument("--quiesced", action="store_true")
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
