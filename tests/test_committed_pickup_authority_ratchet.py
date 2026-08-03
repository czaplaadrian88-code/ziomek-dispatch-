"""Ratchet: committed pickup czasowki ma jednego ownera i jeden writer."""
import ast
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _source(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _production_sources() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*.py")
        if "tests" not in path.parts
        and ".claude" not in path.parts
        and "eod_drafts" not in path.parts
    ]


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _enclosing_function(
    node: ast.AST, parents: dict[ast.AST, ast.AST]
) -> str:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return "<module>"


def _literal_key(node: ast.AST) -> str | None:
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else None
    )


def _string_aliases(tree: ast.AST) -> dict[str, set[str]]:
    """Zbierz statyczne aliasy stringów także wewnątrz funkcji.

    To jest konserwatywny ratchet, nie interpreter Pythona: jeśli nazwa bywa
    związana z chronionym stringiem, każde jej użycie traktujemy jak potencjalne
    użycie tego stringa. Fałszywy alarm jest bezpieczniejszy niż cichy writer.
    """
    aliases: dict[str, set[str]] = {}
    assignments: list[tuple[list[ast.AST], ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if value is not None:
            assignments.append((targets, value))

    # Fixed point domyka A="PICKUP_"; B=A+"TIME_UPDATED" niezależnie od
    # kolejności ast.walk. Rebinding jest celowo unią konserwatywną.
    for _ in range(len(assignments) + 1):
        changed = False
        for targets, value in assignments:
            resolved = _resolved_strings(value, aliases)
            if not resolved:
                continue
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                known = aliases.setdefault(target.id, set())
                before = len(known)
                known.update(resolved)
                changed = changed or len(known) != before
        if not changed:
            break
    return aliases


def _resolved_strings(
    node: ast.AST, aliases: dict[str, set[str]]
) -> set[str]:
    literal = _literal_key(node)
    if literal is not None:
        return {literal}
    if isinstance(node, ast.Name):
        return aliases.get(node.id, set())
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolved_strings(node.left, aliases)
        right = _resolved_strings(node.right, aliases)
        return {prefix + suffix for prefix in left for suffix in right}
    if isinstance(node, ast.FormattedValue):
        if node.format_spec is not None:
            return set()
        values = _resolved_strings(node.value, aliases)
        if node.conversion in {-1, ord("s")}:
            return {str(value) for value in values}
        if node.conversion == ord("r"):
            return {repr(value) for value in values}
        if node.conversion == ord("a"):
            return {ascii(value) for value in values}
        return set()
    if isinstance(node, ast.JoinedStr):
        combined = {""}
        for part in node.values:
            values = _resolved_strings(part, aliases)
            if not values:
                return set()
            combined = {
                prefix + suffix
                for prefix in combined
                for suffix in values
            }
        return combined
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
        and len(node.args) == 1
        and not node.keywords
        and isinstance(node.args[0], (ast.List, ast.Tuple))
    ):
        separators = _resolved_strings(node.func.value, aliases)
        parts = [
            _resolved_strings(element, aliases)
            for element in node.args[0].elts
        ]
        if not separators or any(not values for values in parts):
            return set()
        combinations = {""}
        for values in parts:
            combinations = {
                prefix + "\0" + value
                for prefix in combinations
                for value in values
            }
        return {
            separator.join(value.split("\0")[1:])
            for separator in separators
            for value in combinations
        }
    return set()


def _subscript_keys(
    node: ast.AST, aliases: dict[str, set[str]]
) -> set[str]:
    return (
        _resolved_strings(node.slice, aliases)
        if isinstance(node, ast.Subscript)
        else set()
    )


def _mutation_sites(source: str, field: str) -> list[tuple[str, int]]:
    """Wykryj literalne dict/subscript/update/setdefault writer styles."""
    tree = ast.parse(source)
    parents = _parents(tree)
    aliases = _string_aliases(tree)
    sites: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        matched = False
        if isinstance(node, ast.Dict):
            matched = any(
                field in _resolved_strings(key, aliases)
                for key in node.keys
            )
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
            )
            matched = any(
                field in _subscript_keys(target, aliases)
                for target in targets
            )
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "dict":
                matched = any(
                    keyword.arg == field for keyword in node.keywords
                )
            elif isinstance(node.func, ast.Attribute):
                if node.func.attr == "update":
                    matched = any(
                        keyword.arg == field for keyword in node.keywords
                    )
                elif (
                    node.func.attr in {"setdefault", "__setitem__"}
                    and node.args
                ):
                    matched = field in _resolved_strings(
                        node.args[0], aliases
                    )
        if matched:
            sites.append((_enclosing_function(node, parents), node.lineno))
    return sites


def _event_producer_sites(source: str, event_type: str) -> list[tuple[str, int]]:
    """Znajdź dict/subscript/update/setdefault producentów typu eventu."""
    tree = ast.parse(source)
    parents = _parents(tree)
    aliases = _string_aliases(tree)
    sites: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        matched = False
        if isinstance(node, ast.Dict):
            matched = any(
                "event_type" in _resolved_strings(key, aliases)
                and event_type in _resolved_strings(value, aliases)
                for key, value in zip(node.keys, node.values)
            )
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            value = getattr(node, "value", None)
            matched = bool(
                value is not None
                and event_type in _resolved_strings(value, aliases)
                and any(
                    "event_type" in _subscript_keys(target, aliases)
                    for target in targets
                )
            )
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "dict":
                matched = any(
                    keyword.arg == "event_type"
                    and event_type in _resolved_strings(
                        keyword.value, aliases
                    )
                    for keyword in node.keywords
                )
            elif isinstance(node.func, ast.Attribute):
                if node.func.attr == "update":
                    # update({...}) ma osobny ast.Dict i zostanie policzone tam;
                    # tutaj domykamy wariant keywordowy.
                    matched = any(
                        keyword.arg == "event_type"
                        and event_type in _resolved_strings(
                            keyword.value, aliases
                        )
                        for keyword in node.keywords
                    )
                elif node.func.attr in {"setdefault", "__setitem__"}:
                    matched = bool(
                        len(node.args) >= 2
                        and "event_type" in _resolved_strings(
                            node.args[0], aliases
                        )
                        and event_type in _resolved_strings(
                            node.args[1], aliases
                        )
                    )
        if matched:
            sites.append((_enclosing_function(node, parents), node.lineno))
    return sites


def _semantic_literal_counter(value: str) -> Counter:
    """Ratchet także dla aliasu/stałej ukrywającej writer lub event type.

    Producer scanner dowodzi kształtu znanych konstrukcji. Ten drugi, niezależny
    oracle zamyka prosty bypass ``EVENT_TYPE = 'PICKUP_TIME_UPDATED'`` albo
    ``FIELD = 'committed_pickup_authority'``: każda nowa semantyczna stała w
    kodzie produkcyjnym zmienia przypięty Counter, nawet gdy jest później użyta
    przez ``ast.Name`` zamiast literału w dict/subscript.
    """
    sites = Counter()
    for path in _production_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = _parents(tree)
        aliases = _string_aliases(tree)
        relative = path.relative_to(ROOT)
        for node in ast.walk(tree):
            if (
                (
                    isinstance(node, ast.keyword)
                    and node.arg == value
                )
                or (
                    isinstance(node, (ast.Constant, ast.BinOp, ast.JoinedStr))
                    and value in _resolved_strings(node, aliases)
                )
            ):
                sites[(relative, _enclosing_function(node, parents))] += 1
    return sites


def _counter_sha256(counter: Counter) -> str:
    material = [
        (str(path), function, count)
        for (path, function), count in sorted(
            counter.items(), key=lambda item: (str(item[0][0]), item[0][1])
        )
    ]
    return hashlib.sha256(
        json.dumps(material, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_only_canonical_module_defines_rutcom_authority_policy():
    production_sources = _production_sources()
    resolver_definitions = [
        path
        for path in production_sources
        if "def resolve_czasowka_committed_observation(" in path.read_text(
            encoding="utf-8"
        )
    ]

    assert resolver_definitions == [ROOT / "committed_pickup_authority.py"]
    assert "_AUTOMATIC_FORWARD_RUTCOM_STATUS_IDS" in _source(
        "committed_pickup_authority.py"
    )
    assert "_PANEL_STATUS_IDS_BY_STATE" not in _source("state_machine.py")


def test_both_producers_and_defense_route_to_one_resolver():
    state = _source("state_machine.py")
    watcher = _source("panel_watcher.py")
    pipeline = _source("dispatch_pipeline.py")

    state_tree = ast.parse(state)
    state_parents = _parents(state_tree)
    pure_resolver_callers = [
        _enclosing_function(node, state_parents)
        for node in ast.walk(state_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "resolve_czasowka_committed_observation"
    ]

    assert pure_resolver_callers == [
        "resolve_czasowka_ck_observation",
        "resolve_czasowka_ck_observation",
        "resolve_czasowka_ck_observation",
    ]
    assert "resolve_czasowka_ck_observation(" in watcher
    assert "resolve_czasowka_committed_observation(" in pipeline
    assert "state_machine.resolve_czasowka_ck_observation(" in _source(
        "committed_pickup_apply.py"
    )
    assert "resolve_czasowka_committed_observation(" not in _source(
        "committed_pickup_apply.py"
    )
    assert "build_czasowka_manual_ck_pickup_event(" not in watcher
    assert "build_czasowka_manual_ck_pickup_event as" not in pipeline


def test_preproposal_policy_is_frozen_once_across_async_and_apply_boundaries():
    pipeline = _source("dispatch_pipeline.py")
    tree = ast.parse(pipeline)
    functions = {
        node.name: ast.get_source_segment(pipeline, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    snapshot = functions["_v327_committed_time_policy_snapshot"]
    fetch = functions["_v327_safe_fetch_czas_kuriera"]
    emit = functions["_v327_emit_pre_recheck_event"]
    bag = functions["get_fresh_czas_kuriera_for_bag"]

    assert "C.decision_flag(MANUAL_CK_AUTHORITY_FLAG)" in snapshot
    assert "C.decision_flag(RUTCOM_FORWARD_AUTHORITY_FLAG)" in snapshot
    assert fetch.index("_v327_committed_time_policy_snapshot()") < (
        fetch.index("_v327_safe_fetch_order_time(")
    )
    assert "C.decision_flag(" not in fetch
    assert "C.decision_flag(" not in emit
    assert "C.flag(" not in emit
    assert bag.index(
        "authority_policy = _v327_committed_time_policy_snapshot()"
    ) < bag.index("executor.submit(")
    assert bag.count("authority_policy=authority_policy") == 2
    assert "authority_policy=authority_policy" in emit

    boundary = _source("committed_pickup_apply.py")
    state = _source("state_machine.py")
    assert "authority_policy: CommittedPickupPolicySnapshot | None" in boundary
    assert "effective_policy.passive_guard_enabled" in boundary
    assert "policy_snapshot=authority_policy" in state
    assert "validate_committed_time_policy_source(" in state
    assert 'producer="pre_proposal_recheck"' in snapshot


def test_v6_queue_and_durable_boundary_bind_one_policy_without_live_retry():
    authority = _source("committed_pickup_authority.py")
    queue = _source("coordinator_time_recheck.py")
    state = _source("state_machine.py")
    boundary = _source("committed_pickup_apply.py")
    rollback = _source("tools/rutcom_committed_authority_rollback.py")
    state_tree = ast.parse(state)
    state_functions = {
        node.name: ast.get_source_segment(state, node) or ""
        for node in ast.walk(state_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    flag_reader = state_functions["_pickup_authority_flags"]

    assert 'RECEIPT_SCHEMA = "coordinator_time_recheck.v6"' in queue
    assert 'PRE_POLICY_RECEIPT_SCHEMA = "coordinator_time_recheck.v5"' in queue
    assert 'producer="coordinator_queue"' in queue
    assert "policy_snapshot = _coordinator_policy_snapshot()" in queue
    assert "def coordinator_time_authority_enabled(" in authority
    assert "def deserialize_coordinator_event_policy(" in authority
    assert "receipt_policy_snapshot(record)" in rollback
    assert "and receipt_policy is not None" in rollback
    assert (
        "receipt_policy.rutcom_forward_authority_enabled is False"
        not in rollback
    )
    assert "is_czasowka=is_czasowka" in state_functions[
        "resolve_czasowka_ck_observation"
    ]
    assert "serialize_committed_time_policy(effective_policy)" in boundary
    assert "state_event_metadata=state_event_metadata" in boundary
    assert "coordinator policy cannot apply authority" in boundary
    assert "passive_guard_enabled=passive_enabled" in boundary
    assert "rutcom_forward_authority_enabled=forward_enabled" in boundary
    assert "or claim_authorized" not in boundary
    assert "claimed_receipt_policy_off" in state
    assert "_COORDINATOR_RECEIPT_MAX_AGE" not in authority
    assert flag_reader.index("if durable_authorized:") < flag_reader.index(
        'flag("ENABLE_CZASOWKA_CK_PASSIVE_GUARD", True)'
    )
    assert flag_reader.index("if needs_receipt:") < flag_reader.index(
        'flag("ENABLE_CZASOWKA_CK_PASSIVE_GUARD", True)'
    )


def test_panel_policy_is_captured_before_io_and_bound_through_durable_apply():
    watcher = _source("panel_watcher.py")
    state = _source("state_machine.py")
    authority = _source("committed_pickup_authority.py")
    tree = ast.parse(watcher)
    functions = {
        node.name: ast.get_source_segment(watcher, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    snapshot = functions["_panel_committed_time_policy_snapshot"]
    diff = functions["_diff_and_emit"]
    cold = functions["_post_restart_cold_start_scan"]
    emitter = functions["_emit_and_apply_state"]

    assert 'producer="panel_watcher"' in snapshot
    assert "C.decision_flag(MANUAL_CK_AUTHORITY_FLAG)" in snapshot
    assert "C.decision_flag(RUTCOM_FORWARD_AUTHORITY_FLAG)" in snapshot
    assert diff.count("_panel_committed_time_policy_snapshot()") == 1
    assert diff.index("_panel_committed_time_policy_snapshot()") < diff.index(
        "durable_event_apply.drain_pending("
    )
    assert diff.index("_panel_committed_time_policy_snapshot()") < diff.index(
        "_pdp.prefetch_details("
    )
    assert diff.count("policy_snapshot=") >= 4
    assert diff.count("_committed_time_policy") >= 8
    assert "committed_time_policy=_committed_time_policy" in diff
    assert cold.index("_panel_committed_time_policy_snapshot()") < cold.index(
        "fetch_order_details("
    )
    assert "committed_time_policy=committed_time_policy" in cold
    assert "serialize_committed_time_policy(committed_time_policy)" in emitter
    assert "deserialize_committed_time_policy(" in state
    assert "COMMITTED_TIME_POLICY_SNAPSHOT_FIELD in event" in state
    assert "def validate_committed_time_policy_source(" in authority


def test_coordinator_authority_is_receipt_bound_end_to_end():
    queue = _source("coordinator_time_recheck.py")
    watcher = _source("panel_watcher.py")
    watcher_tree = ast.parse(watcher)
    watcher_parents = _parents(watcher_tree)
    replay_callers = [
        _enclosing_function(node, watcher_parents)
        for node in ast.walk(watcher_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_replay_claimed_time_event"
    ]

    assert 'RECEIPT_SCHEMA = "coordinator_time_recheck.v6"' in queue
    assert 'PRE_POLICY_RECEIPT_SCHEMA = "coordinator_time_recheck.v5"' in queue
    assert 'LEGACY_RECEIPT_SCHEMA = "coordinator_time_recheck.v4"' in queue
    assert 'ELIGIBLE_AT_FIELD = "eligible_at"' in queue
    assert "def claim_receipt(" in queue
    assert "def verify_claimed_event(" in queue
    assert "_ctr.pending_with_receipts()" in watcher
    assert "_ctr.ack_receipts(" in watcher
    assert "authority_receipt=" in watcher
    assert replay_callers == ["_diff_and_emit"]
    assert "_ctr.drain_with_receipts()" not in watcher
    assert "_ctr.drain()" not in watcher


def test_coordinator_queue_has_immutable_head_successor_and_safe_legacy_drain():
    queue = _source("coordinator_time_recheck.py")

    assert 'SUCCESSOR_FIELD = "successor"' in queue
    assert "def _same_head(" in queue
    assert "preserved[SUCCESSOR_FIELD] = next_receipt" in queue
    assert '"requested_at": successor_base["request_id"]' not in queue
    assert '"requested_at": successor_base["requested_at"]' in queue
    assert "ELIGIBLE_AT_FIELD: _utc_now().isoformat()" in queue
    assert "def prepare_legacy_rollback(" in queue
    assert "def release_legacy_rollback_fence(" in queue
    assert "coordinator time queue fenced for legacy code rollback" in queue
    assert "drainable = {" in queue
    assert "and receipt.get(\"claim\") is not None" in queue
    assert "def _receipt_ready(" in queue
    assert "def _fresh_receipt(" not in queue
    assert "projection[oid] = projection_now.isoformat()" in queue


def test_raw_coordinator_claim_gate_precedes_every_time_cas_and_writer():
    state = _source("state_machine.py")
    tree = ast.parse(state)
    functions = {
        node.name: ast.get_source_segment(state, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    pickup_status = functions["_pickup_time_event_status"]
    event_oracle = functions["event_effect_status"]
    state_writer = functions["update_from_event"]

    assert state.count("_legacy_time_claim_gate(event)") == 4
    assert pickup_status.index("_legacy_time_claim_gate(event)") < (
        pickup_status.index("time_event_cas_status(")
    )
    ck_oracle = event_oracle.split(
        'if etype == "CZAS_KURIERA_UPDATED":', 1
    )[1].split('if etype == "PICKUP_TIME_UPDATED":', 1)[0]
    assert ck_oracle.index("_legacy_time_claim_gate(event)") < (
        ck_oracle.index("time_event_cas_status(")
    )
    ck_writer = state_writer.split(
        'if etype == "CZAS_KURIERA_UPDATED":', 1
    )[1].split('if etype == "PICKUP_TIME_UPDATED":', 1)[0]
    pickup_writer = state_writer.split(
        'if etype == "PICKUP_TIME_UPDATED":', 1
    )[1]
    assert ck_writer.index("_legacy_time_claim_gate(event)") < (
        ck_writer.index("time_event_cas_status(")
    )
    assert pickup_writer.index("_legacy_time_claim_gate(event)") < (
        pickup_writer.index("_pickup_time_event_status(event, existing)")
    )


def test_reserved_source_and_legacy_key_have_one_canonical_oracle():
    authority = _source("committed_pickup_authority.py")
    state = _source("state_machine.py")
    apply = _source("committed_pickup_apply.py")
    key_block = apply[
        apply.index("def time_update_event_key(") : apply.index(
            "\ndef apply_event(", apply.index("def time_update_event_key(")
        )
    ]

    assert authority.count(
        "def pickup_payload_requires_coordinator_receipt("
    ) == 1
    assert authority.count(
        "def pickup_event_has_authority_artifact("
    ) == 1
    assert state.count("pickup_payload_requires_coordinator_receipt(") == 2
    assert state.count("pickup_event_has_authority_artifact(") == 2
    assert (
        'COMMITTED_PICKUP_EVENT_ID_MARKER = "_PICKUP_TIME_UPDATED_COMMITTED_"'
        in authority
    )
    assert "str(key).startswith(\"committed_\")" in authority
    assert "def is_committed_pickup_outbox_artifact(" in authority
    assert "TIME_EVENT_CAS_SCHEMA_FIELD" in key_block
    assert "CK_CHANGE_REVISION_OBSERVATION_FIELD" in key_block
    assert '"pickup_time_revision_at_observation"' in key_block


def test_code_rollback_is_mechanically_gated_across_queue_and_outbox():
    tool = _source("tools/rutcom_committed_authority_rollback.py")
    bus = _source("event_bus.py")
    queue = _source("coordinator_time_recheck.py")

    assert "def list_unfinished_state_applies(" in bus
    assert "event_bus.list_unfinished_state_applies()" in tool
    assert "is_committed_pickup_outbox_artifact" in tool
    assert "AUTHORITY_FLAGS = (" in tool
    assert "AUTHORITY_FLAGS != COMMITTED_PICKUP_AUTHORITY_FLAGS" in tool
    assert "state_machine.get_all_strict()" in tool
    assert "state_has_committed_pickup_artifact(order)" in tool
    assert "not enabled_authority_flags" in tool
    assert 'before["enabled_authority_flags"]' in tool
    assert "queue.prepare_legacy_rollback(args.queue_backup)" in tool
    assert "not args.apply or not args.quiesced" in tool
    assert "def _pre_v4_coordinator_time_row_blocks_forward(" in tool
    assert "def _pre_v16_assignment_ck_row_blocks_forward(" in tool
    assert "def _active_time_contract_incomplete(" in tool
    assert "committed_time_contract_is_complete(order)" in tool
    assert "is_forward_authority_outbox_artifact(" in tool
    assert "def _unbound_new_order_time_row_blocks_forward(" in tool
    assert "and not forward_authority_rows" in tool
    assert "and not unbound_new_order_time_rows" in tool
    assert "and not pre_v16_assignment_ck_rows" in tool
    assert "and active_incomplete_time_contract_count == 0" in tool
    assert "FORWARD_WRITER_UNITS = (" in tool
    assert "def _probe_forward_writer_quiescence(" in tool
    assert "and writer_quiescence_verified" in tool
    assert tool.count("_probe_forward_writer_quiescence()") >= 4
    assert 'forward_status.add_argument("--quiesced"' in tool
    assert 'status.add_argument("--quiesced"' in tool
    assert "queue.rollback_records_snapshot()" in tool
    assert "queue.rollback_record_is_unclaimed(" in tool
    assert "and forward_blocking_queue_records == 0" in tool
    assert "queue_record_count_matches_status" in tool
    assert "def rollback_records_snapshot(" in queue
    assert "def rollback_record_is_unclaimed(" in queue
    assert "def acquire_forward_rollout_fence(" in queue
    assert "def release_forward_rollout_fence(" in queue
    assert "def forward_rollout_fence_status(" in queue
    assert "coordinator time queue fenced for forward authority rollout" in queue
    assert 'and forward_fence["forward_fence_valid"]' in tool
    assert "def _cmd_fence_forward(" in tool
    assert "def _cmd_release_forward_fence(" in tool
    assert "projection[oid] = projection_now.isoformat()" in queue
    assert '"safe_for_forward_deploy": safe_for_forward_deploy' in tool
    assert 'sub.add_parser(\n        "forward-status"' in tool


def test_new_order_intent_is_outbox_bound_and_recovers_before_panel_io():
    watcher = _source("panel_watcher.py")
    apply = _source("committed_pickup_apply.py")
    state = _source("state_machine.py")

    emitter = watcher.split("def _emit_and_apply_state(", 1)[1].split(
        "\ndef _load_coords(", 1
    )[0]
    assert "state_payload = sanitized_state_payload" in emitter
    assert "payload = sanitized_payload" not in emitter

    recovery_start = watcher.index("# A pending initial receipt")
    recovery_end = watcher.index("html_order_ids =", recovery_start)
    recovery = watcher[recovery_start:recovery_end]
    assert "_resume_new_order_time_contract(" in recovery
    assert "current_state = state_get_all()" in recovery
    assert "current_state.pop(_blocked_oid, None)" in recovery
    for later_writer in (
        "_heal_missing_order_details(",
        "# 2. ZMIANY:",
        "# ================== PICKED_UP RECONCILE",
        "# ============ ORDER-TIME RE-CHECK",
    ):
        assert recovery_start < watcher.index(later_writer, recovery_end)
    assert "# This recovery consumes only orders_state" not in watcher

    assert "def verify_new_order_time_intent_receipt(" in apply
    assert 'current.get("last_lifecycle_event_id_new_order")' in apply
    assert "event_bus.get_state_apply_outbox(marker)" in apply
    assert 'row.get("state_status") == "applied"' in apply
    assert "initial_intent_claimed and not initial_intent_verified" in apply

    assert "pending_initial_intent = current.get(" in state
    assert "NEW_ORDER_TIME_INTENT_ID_FIELD" in state
    assert "blocked by pending NEW_ORDER intent" in state


def test_cold_start_and_null_pickup_cannot_bypass_canonical_time_owner():
    watcher = _source("panel_watcher.py")
    state = _source("state_machine.py")
    cold_start = watcher.split(
        "def _post_restart_cold_start_scan(", 1
    )[1].split("\ndef _should_skip_empty_packs_write(", 1)[0]
    pickup = watcher.split("def _diff_pickup_time(", 1)[1].split(
        "\ndef _panel_extract_status_id(", 1
    )[0]

    initialize_at = cold_start.index('"NEW_ORDER"')
    assign_at = cold_start.index('"COURIER_ASSIGNED"')
    assert "_build_order_details_payload(" in cold_start
    assert "if _oid_str not in current_state:" in cold_start
    assert initialize_at < assign_at
    assert "if not _initialized.state_ready:" in cold_start
    assert "_initialize_new_order_time_contract(" in cold_start
    assert "continue" in cold_start[
        cold_start.index("if not _initialized.state_ready:") : assign_at
    ]
    assert "if new_iso:" in pickup
    assert "policy_snapshot=transaction_policy" in pickup
    assert "_time_event_transaction_policy(" in pickup
    assert "if old_iso and new_iso and C.is_czasowka_order(old_state):" not in pickup
    assert "def _initialize_new_order_time_contract(" in watcher
    assert watcher.count("_initialize_new_order_time_contract(") >= 3
    assert "NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD" in watcher
    assert "def _new_order_time_authority_enabled(" in state
    assert "None if initial_time_owned" in state


def test_pruned_versioned_time_event_has_terminal_oracle():
    state = _source("state_machine.py")
    missing_branch = state.split("if not current:", 1)[1].split(
        "etype = event.get(\"event_type\")", 1
    )[0]

    assert "time_event_cas_is_versioned(event_type, payload)" in missing_branch
    assert 'return "superseded"' in missing_branch


def test_v13_review_findings_remain_closed_by_single_contract_owners():
    authority = _source("committed_pickup_authority.py")
    state = _source("state_machine.py")
    queue = _source("coordinator_time_recheck.py")
    bus = _source("event_bus.py")
    durable = _source("durable_event_apply.py")
    rollback = _source("tools/rutcom_committed_authority_rollback.py")
    watcher = _source("panel_watcher.py")

    assert "committed_ck_panel_baseline_at_observation" in authority
    assert '"parallel_ck_snapshot_stale"' in authority
    assert '"old_ck_iso": normalized_observation.get("old_ck_iso")' in authority
    assert "state_has_committed_pickup_artifact(existing)" in state
    assert "pickup_time_revision_at_observation" in watcher
    assert "def _legacy_time_claim_status(" in state
    assert "coordinator_time_recheck.verify_claimed_event(event)" in state
    assert 'CONTINUATION_DEPTH_FIELD = "continuation_depth"' in queue
    assert "< _MAX_LEGACY_CONTINUATION_DEPTH" in queue
    assert "def state_apply_outbox_row_is_terminal(" in bus
    assert "not state_apply_outbox_row_is_terminal(decoded)" in bus
    assert "event_bus.state_apply_outbox_row_is_terminal(row)" in durable
    assert "active_committed_state_count == 0" in rollback


def test_symbolic_flag_consumer_scan_is_a_required_seed_gate():
    seed = _source("tools/flag_lifecycle_seed.py")

    assert "ENGINE_SYMBOLIC_CONSUMERS = {" in seed
    assert "def _validate_symbolic_consumer_sources(" in seed
    assert "    _validate_symbolic_consumer_sources()" in seed


def test_authority_attestation_seals_final_downstream_markers():
    apply = _source("committed_pickup_apply.py")
    durable = _source("durable_event_apply.py")

    assert "def _authority_sealed_core(" in apply
    assert 'excluded = {"event_id", "committed_authority_attestation"}' in apply
    assert "state_event_sealer = _seal_authority_event" in apply
    assert "state_event_sealer(dict(requested_event))" in durable
    assert "requested_event.update(sealed_metadata)" in durable


def test_only_pickup_event_handler_writes_coupled_committed_fields():
    from dispatch_v2.committed_pickup_authority import (
        COMMITTED_PICKUP_COUPLED_FIELDS,
    )

    authority = _source("committed_pickup_authority.py")
    state = _source("state_machine.py")

    assert COMMITTED_PICKUP_COUPLED_FIELDS == (
        ("order_type", "old_order_type", "new_order_type"),
        ("prep_minutes", "old_prep_minutes", "new_prep_minutes"),
        (
            "decision_deadline",
            "old_decision_deadline",
            "new_decision_deadline",
        ),
        (
            "zmiana_czasu_odbioru",
            "old_zmiana_czasu_odbioru",
            "new_zmiana_czasu_odbioru",
        ),
    )
    assert authority.count("COMMITTED_PICKUP_COUPLED_FIELDS") >= 6
    assert "for state_field, _old_key, new_key in " in state
    assert "COMMITTED_PICKUP_COUPLED_FIELDS:" in state
    assert '"event_type": "PICKUP_TIME_UPDATED"' in authority
    assert 'update_fields["czas_kuriera_warsaw"] = new_pickup' in state
    assert '"committed_pickup_authority": committed_authority' in state
    assert '"committed_pickup_observed_at": payload.get("observed_at")' in state


def test_assignment_ck_policy_has_one_owner_and_durable_snapshot_boundary():
    authority = _source("committed_pickup_authority.py")
    state = _source("state_machine.py")
    durable = _source("durable_event_apply.py")
    state_tree = ast.parse(state)
    state_parents = _parents(state_tree)

    resolver_defs = [
        node
        for node in ast.walk(ast.parse(authority))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "resolve_czasowka_assignment_ck"
    ]
    resolver_callers = sorted(
        _enclosing_function(node, state_parents)
        for node in ast.walk(state_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "resolve_czasowka_assignment_ck"
    )
    policy_callers = sorted(
        _enclosing_function(node, state_parents)
        for node in ast.walk(state_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_assignment_ck_resolution"
    )

    assert len(resolver_defs) == 1
    assert resolver_callers == ["_assignment_ck_resolution"]
    assert policy_callers == ["event_effect_status", "update_from_event"]
    for field in (
        "ASSIGNMENT_CK_FORWARD_SNAPSHOT_FIELD",
        "ASSIGNMENT_CK_PASSIVE_SNAPSHOT_FIELD",
    ):
        assert durable.count(field) >= 3
        assert state.count(field) >= 3
    assert "CK_PASSIVE_SUPPRESSED" not in state
    assert "CK_COMMITTED_PARALLEL_WRITER_BLOCKED" not in state


def test_production_ast_has_one_provenance_writer_and_one_state_file_funnel():
    protected_fields = {
        "pickup_at_warsaw": {
            (Path("state_machine.py"), "update_from_event"): 3,
                (Path("panel_watcher.py"), "_diff_and_emit"): 2,
                (
                    Path("committed_pickup_authority.py"),
                    "build_new_order_time_intent",
                ): 1,
            (Path("shadow_dispatcher.py"), "_build_order_event"): 1,
            (Path("shadow_dispatcher.py"), "_tick"): 1,
            (Path("panel_client.py"), "normalize_order"): 1,
            (Path("panel_client.py"), "health_check"): 1,
            (Path("panel_watcher.py"), "_build_order_details_payload"): 1,
            (Path("dispatch_pipeline.py"), "_v327_safe_fetch_order_time"): 1,
            (Path("dispatch_pipeline.py"), "_evaluate_s2_slot"): 1,
            (Path("czasowka_scheduler.py"), "_eval_czasowka_impl"): 1,
            (Path("czasowka_scheduler.py"), "_emit_to_event_bus"): 1,
            (Path("tools/sequential_replay.py"), "_bag_entry"): 1,
            (Path("tools/sequential_replay.py"), "reconstruct_inflight"): 1,
            (Path("tools/route_order_golden_corpus_gen.py"), "o"): 1,
        },
        "czas_kuriera_warsaw": {
            (Path("state_machine.py"), "_r_declared_tripwire"): 1,
            (Path("state_machine.py"), "update_from_event"): 4,
                (Path("panel_watcher.py"), "_diff_and_emit"): 3,
                (
                    Path("committed_pickup_authority.py"),
                    "build_new_order_time_intent",
                ): 1,
            (Path("core/candidates.py"), "eval_courier_inner"): 2,
            (Path("shadow_dispatcher.py"), "_serialize_candidate"): 1,
            (Path("shadow_dispatcher.py"), "_build_order_event"): 1,
            (Path("shadow_dispatcher.py"), "_serialize_result"): 1,
            (Path("panel_client.py"), "normalize_order"): 1,
            (Path("obj_replay_capture.py"), "_ser_order"): 1,
            (Path("panel_watcher.py"), "_build_order_details_payload"): 1,
            (Path("dispatch_pipeline.py"), "_v327_safe_fetch_order_time"): 1,
            (Path("dispatch_pipeline.py"), "_evaluate_s2_slot"): 1,
            (Path("czasowka_scheduler.py"), "_eval_czasowka_impl"): 1,
            (Path("czasowka_scheduler.py"), "_emit_to_event_bus"): 1,
            (Path("tools/route_reorder_replay.py"), "ostate_and_stops"): 1,
            (
                Path("tools/monitor_refloor_peak_2026_05_31.py"),
                "evaluate_cid",
            ): 1,
            (Path("tools/sequential_replay.py"), "_bag_entry"): 1,
            (Path("tools/sequential_replay.py"), "reconstruct_inflight"): 1,
            (Path("tools/route_order_golden_corpus_gen.py"), "o"): 1,
            (Path("tools/b_route_shadow.py"), "_mine_from_bag"): 1,
            (Path("tools/decision_outcomes.py"), "join_and_compute"): 1,
            (Path("tools/decision_outcomes.py"), "load_outcomes"): 1,
            (Path("tools/benchmark_c7_normal_path.py"), "_candidate"): 1,
            (Path("tools/bundle_calib_shadow.py"), "_mine_from_bag"): 1,
            (Path("tools/verify_pickup_floor_peak.py"), "verify_committed_floor"): 1,
        },
        "czas_kuriera_hhmm": {
            (Path("state_machine.py"), "_r_declared_tripwire"): 1,
            (Path("state_machine.py"), "update_from_event"): 4,
                (Path("panel_watcher.py"), "_diff_and_emit"): 3,
                (
                    Path("committed_pickup_authority.py"),
                    "build_new_order_time_intent",
                ): 1,
            (Path("tools/ziomek_pred_calibration.py"), "run_tick"): 3,
            (Path("core/candidates.py"), "eval_courier_inner"): 2,
            (Path("shadow_dispatcher.py"), "_serialize_candidate"): 1,
            (Path("shadow_dispatcher.py"), "_build_order_event"): 1,
            (Path("shadow_dispatcher.py"), "_serialize_result"): 1,
            (Path("panel_client.py"), "normalize_order"): 1,
            (Path("panel_watcher.py"), "_build_order_details_payload"): 1,
            (Path("dispatch_pipeline.py"), "_v327_safe_fetch_order_time"): 1,
            (Path("czasowka_scheduler.py"), "_eval_czasowka_impl"): 1,
            (Path("czasowka_scheduler.py"), "_emit_to_event_bus"): 1,
        },
        "committed_pickup_authority": {
            (Path("state_machine.py"), "update_from_event"): 2,
        },
        "committed_pickup_panel_baseline_at_observation": {
            (
                Path("committed_pickup_authority.py"),
                "_build_pickup_event",
            ): 1,
            (Path("state_machine.py"), "update_from_event"): 2,
        },
        "committed_ck_panel_baseline_at_observation": {
            (
                Path("committed_pickup_authority.py"),
                "_build_pickup_event",
            ): 1,
            (Path("state_machine.py"), "update_from_event"): 2,
        },
        "pickup_time_revision": {
            (Path("state_machine.py"), "update_from_event"): 2,
        },
        "committed_authority_attestation": {
            (Path("committed_pickup_apply.py"), "_seal_authority_event"): 1,
        },
    }
    actual = {field: Counter() for field in protected_fields}
    guarded_state_writers = Counter()
    for path in _production_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = _parents(tree)
        relative = path.relative_to(ROOT)
        source = path.read_text(encoding="utf-8")
        for field in protected_fields:
            for function, _line in _mutation_sites(source, field):
                actual[field][(relative, function)] += 1
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_guarded_write"
            ):
                guarded_state_writers[
                    (relative, _enclosing_function(node, parents))
                ] += 1

    for field, expected in protected_fields.items():
        assert actual[field] == Counter(expected), field
    assert guarded_state_writers == Counter(
        {
                (Path("state_machine.py"), "upsert_order"): 3,
            (Path("state_machine.py"), "touch_check_cursor"): 1,
            (Path("state_machine.py"), "delete_order"): 1,
        }
    )


def test_pending_new_order_time_intent_has_closed_writer_set():
    """The pending receipt may only be captured, backfilled, or consumed."""
    actual = Counter()
    for path in _production_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = _parents(tree)
        relative = path.relative_to(ROOT)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Subscript)
                and isinstance(node.ctx, ast.Store)
                and isinstance(node.slice, ast.Name)
                and node.slice.id == "NEW_ORDER_TIME_INTENT_FIELD"
            ):
                continue
            actual[(relative, _enclosing_function(node, parents))] += 1

    assert actual == Counter(
        {
            (Path("panel_watcher.py"), "_emit_and_apply_state"): 1,
            (
                Path("state_machine.py"),
                "_merge_new_order_time_intent_backfill",
            ): 1,
            (Path("state_machine.py"), "update_from_event"): 3,
        }
    )


def test_event_producers_are_closed_over_known_funnels():
    expected = {
        "PICKUP_TIME_UPDATED": Counter(
            {
                (Path("panel_watcher.py"), "_diff_pickup_time"): 1,
                (
                    Path("committed_pickup_authority.py"),
                    "committed_pickup_event_id",
                ): 1,
                (
                    Path("committed_pickup_authority.py"),
                    "_build_pickup_event",
                ): 1,
            }
        ),
        "CZAS_KURIERA_UPDATED": Counter(
            {
                (Path("panel_watcher.py"), "_diff_czas_kuriera"): 1,
                (
                Path("dispatch_pipeline.py"),
                "_v327_emit_pre_recheck_event",
                ): 1,
            }
        ),
    }
    actual = {event_type: Counter() for event_type in expected}
    for path in _production_sources():
        relative = path.relative_to(ROOT)
        source = path.read_text(encoding="utf-8")
        for event_type in expected:
            for function, _line in _event_producer_sites(source, event_type):
                actual[event_type][(relative, function)] += 1

    assert actual == expected

    watcher = _source("panel_watcher.py")
    pipeline = _source("dispatch_pipeline.py")
    boundary = _source("committed_pickup_apply.py")
    assert "return apply_event(event, authority_policy=policy_snapshot)" in watcher
    assert "outcome = _durable_apply(" in pipeline
    assert "durable_event_apply.emit_and_apply(" in boundary


def test_mutation_scanner_catches_non_dict_writer_styles():
    snippets = {
        "subscript": 'state["committed_pickup_authority"] = value',
        "keyword_update": "state.update(committed_pickup_authority=value)",
        "dict_call": "state.update(dict(committed_pickup_authority=value))",
        "dict_update": 'state.update({"committed_pickup_authority": value})',
        "setdefault": (
            'state.setdefault("committed_pickup_authority", value)'
        ),
        "constant_alias": (
            'FIELD = "committed_pickup_authority"\n    state[FIELD] = value'
        ),
        "concatenated_key": (
            'state["committed_" + "pickup_authority"] = value'
        ),
        "static_fstring_key": (
            'state[f"committed_{\'pickup_authority\'}"] = value'
        ),
    }
    for style, snippet in snippets.items():
        assert _mutation_sites(
            f"def mutant(state, value):\n    {snippet}\n",
            "committed_pickup_authority",
        ), style


def test_semantic_literal_closure_counts_keyword_names(
    tmp_path, monkeypatch
):
    mutant = tmp_path / "mutant.py"
    mutant.write_text(
        "def mutant(value):\n"
        "    return dict(committed_pickup_authority=value)\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(globals(), "ROOT", tmp_path)

    assert _semantic_literal_counter(
        "committed_pickup_authority"
    ) == Counter({(Path("mutant.py"), "mutant"): 1})


def test_event_producer_scanner_catches_non_dict_styles():
    snippets = {
        "subscript": 'event["event_type"] = "PICKUP_TIME_UPDATED"',
        "keyword_update": (
            'event.update(event_type="PICKUP_TIME_UPDATED")'
        ),
        "dict_update": (
            'event.update({"event_type": "PICKUP_TIME_UPDATED"})'
        ),
        "setdefault": (
            'event.setdefault("event_type", "PICKUP_TIME_UPDATED")'
        ),
        "dict_call": 'dict(event_type="PICKUP_TIME_UPDATED")',
        "constant_alias": (
            'EVENT_TYPE = "PICKUP_TIME_UPDATED"\n'
            '    event["event_type"] = EVENT_TYPE'
        ),
        "key_and_value_alias": (
            'KEY = "event_type"\n'
            '    EVENT_TYPE = "PICKUP_TIME_UPDATED"\n'
            '    event[KEY] = EVENT_TYPE'
        ),
        "concatenated_key_and_value": (
            'event["event_" + "type"] = '
            '"PICKUP_TIME_" + "UPDATED"'
        ),
        "static_fstring_value": (
            'event["event_type"] = f"PICKUP_TIME_{\'UPDATED\'}"'
        ),
        "static_join_value": (
            'event["event_type"] = "".join('
            '("PICKUP_TIME_", "UPDATED"))'
        ),
    }
    for style, snippet in snippets.items():
        assert _event_producer_sites(
            f"def mutant(event):\n    {snippet}\n",
            "PICKUP_TIME_UPDATED",
        ), style


def test_protected_writer_scanner_catches_static_join_key():
    source = (
        "def mutant(state, value):\n"
        "    state[''.join(('committed_', 'pickup_authority'))] = value\n"
    )

    assert _mutation_sites(source, "committed_pickup_authority")


def test_semantic_literal_closure_blocks_constant_alias_bypass():
    expected = {
        "PICKUP_TIME_UPDATED": (
            "1f6392af8dd0cd581a96be921b9278f273662a33e312fc19bb8c9f0977a5b156"
        ),
        "CZAS_KURIERA_UPDATED": (
            "c9edda9bb32eca416a1aff4e5954ca33a9337a777d789cf6fdadd0edb5b4e9bd"
        ),
        "pickup_at_warsaw": (
            "2672d9f69eb106272ee116c4482ea2215f27e1957c4451886ec24bbea0bc8e48"
        ),
        "czas_kuriera_warsaw": (
            "5955ba81da75513c505ee3555eefeeef9854bec58836fec8ccaa7e18f0c47978"
        ),
        "czas_kuriera_hhmm": (
            "8b3ac2022a394820435167ac7286b3df6c63bd90b3f31a788451c5a8691d004d"
        ),
        "committed_pickup_authority": (
            "b8ee93c09667747c07e44a617ca56e66f09bda1de984618a1b470d1bcdce9b0a"
        ),
        "pickup_time_revision": (
            "9aac8f82f4cac792c672f2bbbc8f0e228699c20602090205b271dfe1ae3660fb"
        ),
        "committed_authority_attestation": (
            "0e006a4c4c5b1c76ea944177174d5e46bc7156f9572fb2c2544f734872dcc78b"
        ),
        "time_event_cas_schema": (
            "7f4357273e1c3c551d863828725cfbe94f2be8a4aba8a1202a5daf52efccae02"
        ),
        "ck_change_revision_at_observation": (
            "7f4357273e1c3c551d863828725cfbe94f2be8a4aba8a1202a5daf52efccae02"
        ),
        "v319g_ck_change_count": (
            "c01e145a0a8a8b5eb1a4d8c68efec4e1265e09ea3f2f1deafddbcde5c8638c53"
        ),
    }

    actual = {
        value: _counter_sha256(_semantic_literal_counter(value))
        for value in expected
    }
    assert actual == expected

    alias_tree = ast.parse(
        'EVENT_TYPE = "PICKUP_TIME_UPDATED"\n'
        'FIELD = "committed_pickup_authority"\n'
    )
    assert any(
        isinstance(node, ast.Constant)
        and node.value == "PICKUP_TIME_UPDATED"
        for node in ast.walk(alias_tree)
    )
    assert any(
        isinstance(node, ast.Constant)
        and node.value == "committed_pickup_authority"
        for node in ast.walk(alias_tree)
    )


def test_removed_legacy_boolean_cannot_return_as_second_authority_channel():
    production = "\n".join(
        path.read_text(encoding="utf-8") for path in _production_sources()
    )
    assert "committed_authority_authorized" not in production


def test_retired_external_ck_only_sources_have_no_production_writer():
    from dispatch_v2.committed_pickup_authority import (
        RETIRED_CZASOWKA_CK_ONLY_SOURCES,
    )

    assert RETIRED_CZASOWKA_CK_ONLY_SOURCES == frozenset(
        {"coordinator_edit", "first_acceptance", "ziomek_late_extension"}
    )
    forbidden = {"coordinator_edit", "ziomek_late_extension"}
    writers = []
    for path in _production_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "source"
                    and isinstance(value, ast.Constant)
                    and value.value in forbidden
                ):
                    writers.append((path.relative_to(ROOT), value.value))
    assert writers == []


def test_watcher_claims_both_force_event_paths_before_apply():
    watcher = _source("panel_watcher.py")
    block = watcher.split(
        "# ============ ORDER-TIME RE-CHECK", 1
    )[1].split("# ================== END ORDER-TIME RE-CHECK", 1)[0]
    claim_token = "_claim_forced_time_event("
    apply_token = "_apply_time_update_event("

    assert block.count(claim_token) == 2
    assert block.count(apply_token) == 2
    first_claim = block.index(claim_token)
    first_apply = block.index(apply_token)
    second_claim = block.index(claim_token, first_claim + 1)
    second_apply = block.index(apply_token, first_apply + 1)
    assert first_claim < first_apply < second_claim < second_apply


def test_coordinator_policy_and_initial_intent_retention_ratchet():
    watcher_tree = ast.parse(_source("panel_watcher.py"))
    watcher_parents = _parents(watcher_tree)
    policy_calls = Counter(
        _enclosing_function(node, watcher_parents)
        for node in ast.walk(watcher_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_time_event_transaction_policy"
    )
    assert policy_calls == Counter(
        {
            "_diff_czas_kuriera": 1,
            "_diff_pickup_time": 1,
            "_replay_claimed_time_event": 1,
            "_diff_and_emit": 2,
        }
    )

    state_source = _source("state_machine.py")
    pickup_resolver = state_source.split(
        "def resolve_czasowka_pickup_observation(", 1
    )[1].split("\ndef build_czasowka_manual_ck_pickup_event(", 1)[0]
    assert "if not is_czasowka:" in pickup_resolver
    assert "is_czasowka=True" not in pickup_resolver

    event_bus_tree = ast.parse(_source("event_bus.py"))
    event_bus_parents = _parents(event_bus_tree)
    retention_uses = Counter(
        _enclosing_function(node, event_bus_parents)
        for node in ast.walk(event_bus_tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id == "_INITIAL_TIME_INTENT_RETENTION_RELEASE_SQL"
    )
    assert retention_uses == Counter({"cleanup": 1, "cleanup_audit_log": 1})

    rollback = _source("tools/rutcom_committed_authority_rollback.py")
    assert (
        "receipt_policy.rutcom_forward_authority_enabled is False"
        not in rollback
    )
