"""T5 — podpisana karta execution authority ``auto.canary.v1``.

Testy są hermetyczne: każdy artefakt karty, audytu i stanu żyje pod ``tmp_path``.
Nie czytają ani nie zapisują ``/var/lib`` ani produkcyjnego ``dispatch_state``.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from dispatch_v2 import common as C


REPO = Path(__file__).resolve().parents[1]


def _load_local(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


AC = _load_local("dispatch_v2.authority_card", REPO / "authority_card.py")
E = _load_local("auto_assign_executor_authority_wt", REPO / "auto_assign_executor.py")
REAL_FRESH_EXECUTION_FLAGS = E._fresh_execution_flags

NOW = datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)
OWNER_ACK_PHRASE = "ODBLOKOWUJE AUTO-CANARY 2026-07-29"
GIT_SHA = "7a63f8008abcdef0123456789abcdef012345678"
FLAG_FP = "ENABLE_AUTO_ASSIGN=True|fixture=v1"
STOP_CONTRACT_SHA256 = (
    "91997392295092fd6cd3bc0d54926261d2074e94e74f67731cf4cd50c2aff42d"
)


@pytest.fixture(autouse=True)
def _pin_execution_clock(monkeypatch):
    """Executor używa świeżego zegara; fixture utrzymuje deterministyczne karty."""
    monkeypatch.setattr(E, "_fresh_execution_now", lambda: NOW)
    monkeypatch.setattr(
        E,
        "_fresh_execution_flags",
        lambda: (True, FLAG_FP),
    )


def _body(**changes):
    body = {
        "class_id": "auto.canary.v1",
        "card_version": 1,
        "issued_at": (NOW - timedelta(minutes=10)).isoformat(),
        "valid_from": (NOW - timedelta(minutes=5)).isoformat(),
        "valid_until": (NOW + timedelta(days=2)).isoformat(),
        "scope": {
            "new_unassigned_only": True,
            "empty_bag_only": True,
            "solo_pickup_delivery_only": True,
            "normal_mode_only": True,
            "excluded_contexts": [
                "reassign", "alarm", "least_damage", "parcel",
                "multi_brand", "shared_pickup", "coordinator_override",
            ],
            "gps": {"required_source": "LIVE", "max_age_sec": 120},
            "no_gps_recommend_only_parity": True,
        },
        "limits": {
            "max_per_hour": 1,
            "max_in_flight": 1,
            "max_total": 3,
            "require_verification": True,
        },
        "stop_contract_sha256": STOP_CONTRACT_SHA256,
        "code_fingerprint": {
            "git_sha": GIT_SHA,
            "flag_fingerprint": FLAG_FP,
        },
        "owner_ack": {
            "phrase": "podpisuję kartę AUTO-canary 2026-07-29",
            "ts": (NOW - timedelta(minutes=5)).isoformat(),
        },
    }
    body.update(changes)
    return body


def _write_card(path: Path, body=None):
    body = body or _body()
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return body


def _write_audit(path: Path, body, **changes):
    row = {
        "ts": (NOW - timedelta(minutes=4)).isoformat(),
        "kind": "authority_card_signed",
        "class_id": body["class_id"],
        "card_sha256": AC.card_sha256(body),
        "pin_verified": True,
    }
    row.update(changes)
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    return row


def _valid_files(tmp_path):
    card = tmp_path / "card.json"
    audit = tmp_path / "audit.jsonl"
    body = _write_card(card)
    _write_audit(audit, body)
    return card, audit, body


def _verify(card, audit, **changes):
    args = {
        "card_path": str(card),
        "audit_path": str(audit),
        "now": NOW,
        "code_git_sha": GIT_SHA,
        "flag_fp": FLAG_FP,
    }
    args.update(changes)
    return AC.verify_card(**args)


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("missing_card", "card_missing"),
        ("missing_audit", "audit_missing"),
        ("pin_false", "pin_not_verified"),
        ("expired", "card_expired"),
        ("future", "card_not_yet_valid"),
        ("fingerprint", "git_sha_mismatch"),
        ("corrupt_card", "card_parse_error"),
    ],
)
def test_card_negative_matrix(tmp_path, setup, reason):
    card, audit, body = _valid_files(tmp_path)
    if setup == "missing_card":
        card.unlink()
    elif setup == "missing_audit":
        audit.unlink()
    elif setup == "pin_false":
        _write_audit(audit, body, pin_verified=False)
    elif setup == "expired":
        body["valid_until"] = (NOW - timedelta(seconds=1)).isoformat()
        _write_card(card, body)
        _write_audit(audit, body)
    elif setup == "future":
        body["valid_from"] = (NOW + timedelta(seconds=1)).isoformat()
        _write_card(card, body)
        _write_audit(audit, body)
    elif setup == "fingerprint":
        body["code_fingerprint"]["git_sha"] = "b" * 40
        _write_card(card, body)
        _write_audit(audit, body)
    elif setup == "corrupt_card":
        card.write_text("{nie-json", encoding="utf-8")

    verdict = _verify(card, audit)
    assert verdict.valid is False
    assert verdict.reason == reason


def test_no_matching_audit_row_is_denied(tmp_path):
    card, audit, _ = _valid_files(tmp_path)
    audit.write_text(
        json.dumps({"kind": "authority_card_signed", "class_id": "inna.klasa"})
        + "\n",
        encoding="utf-8",
    )
    assert _verify(card, audit).reason == "audit_row_missing"


def test_sha_mismatch_is_mutation_oracle(tmp_path):
    """Mutation-oracle SHA: usunięcie/odwrócenie porównania hashy robi ten test RED.

    Audyt podpisuje pierwotne bajty semantyczne, po czym body jest zmieniane bez
    nowego podpisu. Parser, schema, PIN, czas i fingerprint nadal są poprawne,
    więc jedynym powodem odmowy musi zostać ``sha_mismatch``.
    """
    card, audit, body = _valid_files(tmp_path)
    body["owner_ack"]["phrase"] += " TAMPER"
    _write_card(card, body)
    verdict = _verify(card, audit)
    assert verdict.valid is False
    assert verdict.reason == "sha_mismatch"


def test_canonical_json_hash_is_order_and_whitespace_independent(tmp_path):
    card, audit, body = _valid_files(tmp_path)
    card.write_text(
        json.dumps(dict(reversed(list(body.items()))), indent=4, ensure_ascii=False),
        encoding="utf-8",
    )
    assert _verify(card, audit).valid is True


def test_latest_signature_for_class_wins(tmp_path):
    card, audit, body = _valid_files(tmp_path)
    good = json.loads(audit.read_text(encoding="utf-8"))
    bad = dict(good, card_sha256="0" * 64)
    audit.write_text(
        json.dumps(good) + "\n" + json.dumps(bad) + "\n",
        encoding="utf-8",
    )
    assert _verify(card, audit).reason == "sha_mismatch"


def _state(**changes):
    state = {
        **AC.empty_state(),
        "initialized_for_card": AC.card_sha256(_body()),
        "initialized_at": NOW.isoformat(),
    }
    state.update(changes)
    return state


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (_state(executed_total=3), "max_total"),
        (_state(in_flight="480001"), "in_flight"),
        (_state(pending_verification=["480001"]), "pending_verification"),
        (
            _state(
                executed_total=1,
                executed_ts=[NOW.timestamp() - 100],
            ),
            "max_per_hour",
        ),
        (
            _state(
                auto_off_latch=True,
                auto_off_reason="tamper",
                auto_off_ts=NOW.isoformat(),
            ),
            "latch_on",
        ),
    ],
)
def test_limit_negative_matrix(state, reason):
    ok, got = AC.check_limits(state, NOW.timestamp(), _body()["limits"])
    assert ok is False
    assert got == reason


def test_corrupt_state_is_latched_fail_closed(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")
    state = AC.load_state(str(path))
    assert state["auto_off_latch"] is True
    assert state["auto_off_reason"] == "state_corrupt"
    assert state["synthetic"] is True


def test_corrupt_synthetic_state_cannot_be_cleared_or_persisted(
    tmp_path,
):
    """RED G3: korupcja wymaga reconcile, nigdy resetu budżetu przez clear."""
    state_path = tmp_path / "state.json"
    audit_path = tmp_path / "audit.jsonl"
    corrupt_bytes = b"{broken"
    state_path.write_bytes(corrupt_bytes)

    with pytest.raises(ValueError, match="stan uszkodzony.*reconcile"):
        AC.clear_latch(
            str(state_path),
            reason="owner ACK",
            operator="operator-test",
            owner_ack_phrase=OWNER_ACK_PHRASE,
            now=NOW,
            audit_path=str(audit_path),
        )

    assert state_path.read_bytes() == corrupt_bytes
    assert not audit_path.exists()


def test_orphan_temp_from_crash_is_latched_fail_closed(tmp_path):
    """Crash przed rename nie może wyglądać jak pusty, gotowy stan."""
    path = tmp_path / "state.json"
    (tmp_path / "state.json.tmp.123").write_text("{}", encoding="utf-8")
    state = AC.load_state(str(path))
    assert state["auto_off_latch"] is True
    assert state["auto_off_reason"] == "state_atomic_write_incomplete"


def test_atomic_state_save_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = _state(executed_total=1, executed_ts=[NOW.timestamp()])
    AC.save_state(str(path), state)
    assert AC.load_state(str(path)) == state
    assert not list(tmp_path.glob("state.json.tmp.*"))


def _scope_ok():
    return {
        "schema": "authority_scope.v1",
        "predicates": {
            "1_new_unassigned": {
                "event_type": "NEW_ORDER",
                "status_id": 2,
                "state_status": "planned",
                "prior_assignment_count": 0,
                "currently_assigned": False,
                "sources": {
                    "event_type": "event_bus.event_type",
                    "status_id": "event_bus.payload.status_id",
                    "assignment_history": "orders_state.history",
                    "current_assignment": "orders_state.courier_id",
                },
            },
            "2_empty_bag": {
                "bag_size": 0,
                "active_order_ids": [],
                "soon_free_applied": False,
                "generation": 1,
                "sources": {
                    "bag": "CourierState.bag@candidate_loop",
                    "generation": "Candidate.metrics.plan_expected_version",
                },
            },
            "3_solo_plan": {
                "n_pickups": 1,
                "n_deliveries": 1,
                "sources": {
                    "pickups": "RoutePlanV2.pickup_at",
                    "deliveries": "RoutePlanV2.sequence",
                },
            },
            "4_mode": {"mode": "normal", "source": "fixture.mode"},
            "5_exclusions": {
                key: {"value": False, "source": f"fixture.{key}"}
                for key in (
                    "reassign", "alarm", "least_damage", "parcel",
                    "multi_brand", "shared_pickup", "coordinator_override",
                )
            },
            "6_winner_position": {
                "pos_source": "gps",
                "age_seconds": 30,
                "contract": "LIVE",
                "sources": {
                    "position": "CourierState.pos_source",
                    "age": "CourierState.pos_age_sec",
                    "contract": "live_eta.classify_position_contract",
                },
            },
            "7_no_gps_parity": {
                "verified": True,
                "source": "fixture.structural_parity",
            },
        },
    }


def _record(scope=None):
    return {
        "event_id": "480300_NEW_ORDER_first",
        "verdict": "PROPOSE",
        "order_id": "480300",
        "authority_scope": scope if scope is not None else _scope_ok(),
        "commit_proposal": _commit_snapshot(),
        "best": {
            "courier_id": "101",
            "name": "Kurier Testowy",
            "score": 55.0,
            "target_pickup_at": (NOW + timedelta(minutes=12)).isoformat(),
        },
    }


def _commit_snapshot():
    return {
        "schema": "commit_proposal.v1",
        "proposal_computed_at": NOW.isoformat(),
        "order_generation": "sha256:order",
        "fleet": {"generation": "sha256:fleet", "available_cids": ["101"]},
        "proposal": {"winner_cid": "101"},
        "winner": {
            "active_order_ids": [],
            "bag_size": 0,
            "route_generation": 1,
            "route_signature": "sha256:route",
        },
        "hard_valid": True,
        "code_git_sha": GIT_SHA,
        "flag_fingerprint": FLAG_FP,
        "signature": "sha256:proposal",
    }


def _grant_owner_auth(monkeypatch, audit):
    row = {
        "ts": (NOW - timedelta(minutes=5)).isoformat(),
        "kind": "auto_assign_toggle",
        "ok": True,
        "value": True,
        "pin_verified": True,
    }
    with audit.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row) + "\n")
    monkeypatch.setattr(E, "COORDINATOR_AUDIT_PATH", str(audit))


def _executor_paths(tmp_path):
    card, audit, body = _valid_files(tmp_path)
    authority_state_path = tmp_path / "card-state.json"
    AC.initialize_state(
        str(authority_state_path),
        AC.card_sha256(body),
        NOW,
    )
    heartbeat = tmp_path / "monitor-heartbeat.json"
    heartbeat.write_text(
        json.dumps({
            "ts": NOW.isoformat(),
            "pid": 123,
            "checks": {"verdict": "OK", "reasons": []},
        }),
        encoding="utf-8",
    )
    return {
        "authority_card_path": str(card),
        "authority_audit_path": str(audit),
        "authority_state_path": str(authority_state_path),
        "code_git_sha": GIT_SHA,
        "flag_fp": FLAG_FP,
        "state_path": str(tmp_path / "auto-state.json"),
        "monitor_heartbeat_path": str(heartbeat),
        "shadow_decisions_path": str(tmp_path / "shadow.jsonl"),
        "commit_recheck_provider": (
            lambda _oid, _payload, now=None: _commit_snapshot()
        ),
        "identity_registry_provider": lambda: _IdentityRegistry("101"),
    }


def test_valid_card_and_scope_reach_quality_gate(tmp_path, monkeypatch):
    """Happy path karty kończy się dopiero na istniejącej bramce jakościowej."""
    paths = _executor_paths(tmp_path)
    _grant_owner_auth(monkeypatch, Path(paths["authority_audit_path"]))
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    runner_calls = []
    out = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=False),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (
            runner_calls.append(args)
            or (True, "ASSIGN_OK: fixture [verify_ok_kid=101]")
        ),
        notifier=lambda _text: None,
        **paths,
    )
    assert out is None
    assert runner_calls == []


def test_missing_scope_evidence_denies_without_execution(tmp_path, monkeypatch):
    paths = _executor_paths(tmp_path)
    _grant_owner_auth(monkeypatch, Path(paths["authority_audit_path"]))
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    calls = []
    out = E.maybe_execute(
        _record(scope={}),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (
            calls.append(args)
            or (True, "ASSIGN_OK: fixture [verify_ok_kid=101]")
        ),
        notifier=lambda _text: None,
        **paths,
    )
    assert out["blocked"] == "authority_card_scope_evidence_missing"
    assert calls == []


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("missing_card", "authority_card_card_missing"),
        ("sha_mismatch", "authority_card_sha_mismatch"),
        ("no_card_audit_row", "authority_card_audit_row_missing"),
        ("pin_false", "authority_card_pin_not_verified"),
        ("expired", "authority_card_card_expired"),
        ("future", "authority_card_card_not_yet_valid"),
        ("fingerprint", "authority_card_git_sha_mismatch"),
        ("max_total", "authority_card_max_total"),
        ("in_flight", "authority_card_in_flight"),
        ("pending", "authority_card_pending_verification"),
        ("latch", "authority_card_latch_on"),
        ("corrupt_card", "authority_card_card_parse_error"),
        ("corrupt_state", "authority_card_latch_on"),
    ],
)
def test_executor_negative_matrix_has_zero_execution(
    tmp_path, monkeypatch, case, expected
):
    paths = _executor_paths(tmp_path)
    card = Path(paths["authority_card_path"])
    audit = Path(paths["authority_audit_path"])
    state_path = Path(paths["authority_state_path"])
    body = json.loads(card.read_text(encoding="utf-8"))
    _grant_owner_auth(monkeypatch, audit)
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)

    def resign():
        rows = [
            row
            for row in audit.read_text(encoding="utf-8").splitlines()
            if json.loads(row).get("kind") != "authority_card_signed"
        ]
        signed = {
            "ts": (NOW - timedelta(minutes=4)).isoformat(),
            "kind": "authority_card_signed",
            "class_id": body["class_id"],
            "card_sha256": AC.card_sha256(body),
            "pin_verified": True,
        }
        audit.write_text(
            json.dumps(signed) + "\n" + "\n".join(rows) + "\n",
            encoding="utf-8",
        )

    if case == "missing_card":
        card.unlink()
    elif case == "sha_mismatch":
        body["owner_ack"]["phrase"] += " TAMPER"
        _write_card(card, body)
    elif case == "no_card_audit_row":
        rows = [
            row
            for row in audit.read_text(encoding="utf-8").splitlines()
            if json.loads(row).get("kind") != "authority_card_signed"
        ]
        audit.write_text("\n".join(rows) + "\n", encoding="utf-8")
    elif case == "pin_false":
        rows = [json.loads(row) for row in audit.read_text().splitlines()]
        for row in rows:
            if row.get("kind") == "authority_card_signed":
                row["pin_verified"] = False
        audit.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
    elif case == "expired":
        body["valid_until"] = (NOW - timedelta(seconds=1)).isoformat()
        _write_card(card, body)
        resign()
    elif case == "future":
        body["valid_from"] = (NOW + timedelta(seconds=1)).isoformat()
        _write_card(card, body)
        resign()
    elif case == "fingerprint":
        body["code_fingerprint"]["git_sha"] = "b" * 40
        _write_card(card, body)
        resign()
    elif case == "max_total":
        AC.save_state(str(state_path), _state(executed_total=3))
    elif case == "in_flight":
        AC.save_state(str(state_path), _state(in_flight="480001"))
    elif case == "pending":
        AC.save_state(
            str(state_path), _state(pending_verification=["480001"]))
    elif case == "latch":
        AC.save_state(
            str(state_path),
            _state(
                auto_off_latch=True,
                auto_off_reason="operator_stop",
                auto_off_ts=NOW.isoformat(),
            ),
        )
    elif case == "corrupt_card":
        card.write_text("{broken", encoding="utf-8")
    elif case == "corrupt_state":
        state_path.write_text("{broken", encoding="utf-8")

    calls = []
    out = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (
            calls.append(args)
            or (True, "ASSIGN_OK: fixture [verify_ok_kid=101]")
        ),
        notifier=lambda _text: None,
        **paths,
    )
    assert out["blocked"] == expected
    assert calls == []


def test_tamper_latches_and_fixed_card_stays_blocked(tmp_path, monkeypatch):
    paths = _executor_paths(tmp_path)
    audit = Path(paths["authority_audit_path"])
    card = Path(paths["authority_card_path"])
    _grant_owner_auth(monkeypatch, audit)
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    original = json.loads(card.read_text(encoding="utf-8"))
    tampered = json.loads(card.read_text(encoding="utf-8"))
    tampered["owner_ack"]["phrase"] += " TAMPER"
    _write_card(card, tampered)
    calls = []

    first = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (
            calls.append(args)
            or (True, "ASSIGN_OK: fixture [verify_ok_kid=101]")
        ),
        notifier=lambda _text: None,
        **paths,
    )
    assert first["blocked"] == "authority_card_sha_mismatch"
    assert AC.load_state(paths["authority_state_path"])["auto_off_latch"] is True

    _write_card(card, original)
    second = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (
            calls.append(args)
            or (True, "ASSIGN_OK: fixture [verify_ok_kid=101]")
        ),
        notifier=lambda _text: None,
        **paths,
    )
    assert second["blocked"] == "authority_card_latch_on"
    assert calls == []


def test_success_updates_card_counters_atomically(tmp_path, monkeypatch):
    paths = _executor_paths(tmp_path)
    _grant_owner_auth(monkeypatch, Path(paths["authority_audit_path"]))
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    monkeypatch.setenv("ALLOW_AUTO_ASSIGN_STATE_IN_TEST", "1")
    out = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *_args: (
            True,
            "ASSIGN_OK: fixture [verify_ok_kid=101]",
        ),
        notifier=lambda _text: None,
        **paths,
    )
    assert out["executed"] is True
    state = AC.load_state(paths["authority_state_path"])
    assert state["executed_total"] == 1
    assert state["in_flight"] == "480300"
    assert state["pending_verification"] == ["480300"]
    assert state["executed_ts"] == [NOW.timestamp()]


def test_reservation_is_durable_before_runner_and_replay_refuses_after_crash(
    tmp_path,
    monkeypatch,
):
    """RED/mutation G1: wycięcie pre-runner reservation usuwa marker i czerwieni."""
    paths = _executor_paths(tmp_path)
    audit = Path(paths["authority_audit_path"])
    auto_state_path = Path(paths["state_path"])
    marker = tmp_path / "external-effect.marker"
    _grant_owner_auth(monkeypatch, audit)
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    monkeypatch.setenv("ALLOW_AUTO_ASSIGN_STATE_IN_TEST", "1")
    monkeypatch.setattr(E, "_fresh_execution_now", lambda: NOW)
    runner_calls = []

    def crash_after_effect(*args):
        runner_calls.append(args)
        card_state = AC.load_state(paths["authority_state_path"])
        auto_state = json.loads(auto_state_path.read_text(encoding="utf-8"))
        assert card_state["in_flight"] == "480300"
        assert card_state["pending_verification"] == ["480300"]
        assert card_state["executed_total"] == 1
        assert "480300" in auto_state["assigned_orders"]
        assert auto_state["executed_total"] == 1
        marker.write_text("panel-side-effect", encoding="utf-8")
        raise RuntimeError("synthetic crash after panel side-effect")

    first = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=crash_after_effect,
        notifier=lambda _text: None,
        **paths,
    )
    assert first["runner_outcome"] == "unknown"
    assert marker.read_text(encoding="utf-8") == "panel-side-effect"

    # "Restart": drugi call nie ufa pamięci pierwszego executora, tylko dyskowi.
    second = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (
            runner_calls.append(args)
            or (True, "ASSIGN_OK: fixture [verify_ok_kid=101]")
        ),
        notifier=lambda _text: None,
        **paths,
    )
    assert second["blocked"] in {
        "authority_card_latch_on",
        "authority_card_in_flight",
        "authority_card_pending_verification",
    }
    assert len(runner_calls) == 1


def test_card_is_revalidated_with_fresh_time_after_solve_before_reservation(
    tmp_path,
    monkeypatch,
):
    """RED I1/I2: solve przesuwa czas poza valid_until; finalny gate ma to zobaczyć."""
    paths = _executor_paths(tmp_path)
    card = Path(paths["authority_card_path"])
    audit = Path(paths["authority_audit_path"])
    body = json.loads(card.read_text(encoding="utf-8"))
    body["valid_until"] = (NOW + timedelta(seconds=1)).isoformat()
    _write_card(card, body)
    rows = [
        json.loads(line)
        for line in audit.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("kind") != "authority_card_signed"
    ]
    rows.insert(0, {
        "ts": NOW.isoformat(),
        "kind": "authority_card_signed",
        "class_id": body["class_id"],
        "card_sha256": AC.card_sha256(body),
        "pin_verified": True,
    })
    audit.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    Path(paths["authority_state_path"]).unlink()
    AC.initialize_state(
        paths["authority_state_path"],
        AC.card_sha256(body),
        NOW,
    )
    _grant_owner_auth(monkeypatch, audit)
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    clock = {"now": NOW}
    monkeypatch.setattr(E, "_fresh_execution_now", lambda: clock["now"])
    solve_calls = []

    def solve_after_card_was_initially_valid(_oid, _payload, now=None):
        solve_calls.append(now)
        assert now == NOW
        clock["now"] = NOW + timedelta(seconds=2)
        return _commit_snapshot()

    paths["commit_recheck_provider"] = solve_after_card_was_initially_valid
    calls = []

    out = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (
            calls.append(args)
            or (True, "ASSIGN_OK: fixture [verify_ok_kid=101]")
        ),
        notifier=lambda _text: None,
        **paths,
    )

    assert out["blocked"] == "authority_card_card_expired"
    assert solve_calls == [NOW]
    assert calls == []
    assert not Path(paths["state_path"]).exists()


def test_runner_unknown_mutation_oracle_consumes_budget_latch_and_idempotency(
    tmp_path, monkeypatch
):
    """RED-first F1: brak sentinela po starcie runnera to wykonanie NIEZNANE."""
    paths = _executor_paths(tmp_path)
    _grant_owner_auth(monkeypatch, Path(paths["authority_audit_path"]))
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    monkeypatch.setenv("ALLOW_AUTO_ASSIGN_STATE_IN_TEST", "1")
    messages = []

    out = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *_args: (False, "timeout_45s"),
        notifier=messages.append,
        **paths,
    )

    assert out["executed"] is False
    assert out["runner_outcome"] == "unknown"
    card_state = AC.load_state(paths["authority_state_path"])
    assert card_state["executed_total"] == 1
    assert card_state["in_flight"] == "480300"
    assert card_state["pending_verification"] == ["480300"]
    assert card_state["auto_off_latch"] is True
    assert card_state["auto_off_reason"] == "runner_outcome_unknown"
    auto_state = json.loads(
        Path(paths["state_path"]).read_text(encoding="utf-8")
    )
    assert "480300" in auto_state["assigned_orders"]
    assert auto_state["executed_total"] == 1
    assert auto_state["executed_order_ids"] == ["480300"]
    assert any(
        "STAN NIEZNANY" in message and "reconcile 5b karty" in message
        for message in messages
    )


def test_runner_definitive_pre_send_refusal_rolls_back_reservation_with_audit(
    tmp_path, monkeypatch
):
    """Jawna odmowa pre-send zwraca całą rezerwację i zostawia trwały audyt."""
    paths = _executor_paths(tmp_path)
    _grant_owner_auth(monkeypatch, Path(paths["authority_audit_path"]))
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    monkeypatch.setenv("ALLOW_AUTO_ASSIGN_STATE_IN_TEST", "1")
    messages = []

    out = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *_args: (False, "blocked_pytest_context"),
        notifier=messages.append,
        **paths,
    )

    assert out["runner_outcome"] == "definitive_pre_send_refusal"
    card_state = AC.load_state(paths["authority_state_path"])
    assert {
        key: card_state[key]
        for key in AC.empty_state()
    } == AC.empty_state()
    assert card_state["initialized_for_card"] == AC.card_sha256(_body())
    auto_state = json.loads(
        Path(paths["state_path"]).read_text(encoding="utf-8")
    )
    assert "480300" not in auto_state["assigned_orders"]
    assert auto_state.get("executed_total", 0) == 0
    assert auto_state.get("executed_order_ids", []) == []
    audit_rows = [
        json.loads(line)
        for line in Path(paths["authority_audit_path"]).read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert audit_rows[-1]["kind"] == "reservation_rolled_back"
    assert audit_rows[-1]["oid"] == "480300"
    assert not any("STAN NIEZNANY" in message for message in messages)


def test_executor_reservation_rollback_merges_and_preserves_other_execution(
    tmp_path,
    monkeypatch,
):
    """Rollback G1 usuwa dokładne oid+ts, nie cudzy skorelowany budżet."""
    monkeypatch.setenv("ALLOW_AUTO_ASSIGN_STATE_IN_TEST", "1")
    state_path = tmp_path / "auto-state.json"
    other_ts = NOW.timestamp() - 30
    own_ts = NOW.timestamp()
    E._save_state(str(state_path), {
        "assigned_orders": {"OTHER": other_ts, "480300": own_ts},
        "executed": [other_ts, own_ts],
        "executed_total": 2,
        "executed_order_ids": ["OTHER", "480300"],
        "unrelated_writer_field": {"keep": True},
    })

    updated = E._rollback_executor_reservation(
        str(state_path),
        "480300",
        own_ts,
    )

    assert updated == {
        "assigned_orders": {"OTHER": other_ts},
        "executed": [other_ts],
        "executed_total": 1,
        "executed_order_ids": ["OTHER"],
        "unrelated_writer_field": {"keep": True},
    }


def test_record_success_rereads_state_and_latch_preserves_counters(tmp_path):
    """F6: oba writery mergują bieżący stan pod lockiem, nie martwy snapshot."""
    state_path = str(tmp_path / "card-state.json")
    stale = AC.empty_state()
    AC.save_state(
        state_path,
        _state(
            auto_off_latch=True,
            auto_off_reason="other_writer",
            auto_off_ts=NOW.isoformat(),
        ),
    )

    AC.record_success(state_path, stale, "480300", NOW)
    after_success = AC.load_state(state_path)
    assert after_success["auto_off_latch"] is True
    assert after_success["auto_off_reason"] == "other_writer"
    assert after_success["executed_total"] == 1

    AC.latch_auto_off(state_path, "must_not_replace_first_reason", NOW)
    after_latch = AC.load_state(state_path)
    assert after_latch["executed_total"] == 1
    assert after_latch["pending_verification"] == ["480300"]
    assert after_latch["auto_off_reason"] == "other_writer"


def test_verification_writer_releases_only_oid_until_max_total(tmp_path):
    """F3: weryfikacja zwalnia in-flight, ale zachowuje zużyty budżet."""
    state_path = str(tmp_path / "card-state.json")
    audit_path = str(tmp_path / "audit.jsonl")
    limits = {
        "max_per_hour": 3,
        "max_in_flight": 1,
        "max_total": 3,
        "require_verification": True,
    }
    state = AC.empty_state()

    for index in range(1, 4):
        oid = f"OID-{index}"
        state = AC.record_success(state_path, state, oid, NOW)
        expected_block = "in_flight" if index < 3 else "max_total"
        assert AC.check_limits(state, NOW.timestamp(), limits)[1] == (
            expected_block
        )
        state = AC.record_verification(
            state_path,
            oid,
            "operator-test",
            NOW,
            audit_path=audit_path,
        )
        if index < 3:
            assert AC.check_limits(state, NOW.timestamp(), limits) == (True, "ok")
        else:
            assert AC.check_limits(state, NOW.timestamp(), limits) == (
                False,
                "max_total",
            )

    rows = [
        json.loads(line)
        for line in Path(audit_path).read_text(encoding="utf-8").splitlines()
    ]
    assert [row["kind"] for row in rows] == [
        "authority_execution_verified",
        "authority_execution_verified",
        "authority_execution_verified",
    ]
    assert state["executed_total"] == 3
    assert state["pending_verification"] == []
    assert state["in_flight"] is None


def test_latch_clear_refuses_until_in_flight_and_pending_are_reconciled(tmp_path):
    """RED I5: ACK nie może zdjąć latcha przed verify-execution/reconcile."""
    state_path = str(tmp_path / "card-state.json")
    audit_path = str(tmp_path / "audit.jsonl")
    before = _state(
        executed_total=2,
        executed_ts=[NOW.timestamp() - 60, NOW.timestamp()],
        in_flight="OID-2",
        pending_verification=["OID-1", "OID-2"],
        auto_off_latch=True,
        auto_off_reason="runner_outcome_unknown",
        auto_off_ts=NOW.isoformat(),
    )
    AC.save_state(state_path, before)

    with pytest.raises(ValueError, match="verify-execution"):
        AC.clear_latch(
            state_path,
            reason="owner ACK przed reconcile",
            operator="operator-test",
            owner_ack_phrase=OWNER_ACK_PHRASE,
            now=NOW,
            audit_path=audit_path,
        )

    assert AC.load_state(state_path) == before
    assert not Path(audit_path).exists()


@pytest.mark.parametrize(
    "owner_ack_phrase",
    [
        "",
        "ODBLOKOWUJE AUTO-CANARY 2026-07-29.",
        "ODBLOKOWUJE AUTO-CANARY 2026-07-28",
    ],
)
def test_latch_clear_refuses_invalid_owner_ack_phrase_without_writes(
    tmp_path,
    owner_ack_phrase,
):
    """RED J1: brak, literówka i stara data nie mogą zdjąć latcha ani pisać audytu."""
    state_path = str(tmp_path / "card-state.json")
    audit_path = str(tmp_path / "audit.jsonl")
    before = _state(
        auto_off_latch=True,
        auto_off_reason="runner_outcome_unknown",
        auto_off_ts=NOW.isoformat(),
    )
    AC.save_state(state_path, before)

    with pytest.raises(ValueError, match="owner ACK phrase"):
        AC.clear_latch(
            state_path,
            reason="owner ACK po reconcile 5b i verify-execution",
            operator="operator-test",
            owner_ack_phrase=owner_ack_phrase,
            now=NOW,
            audit_path=audit_path,
        )

    assert AC.load_state(state_path) == before
    assert not Path(audit_path).exists()


def test_latch_clear_on_clean_state_changes_only_latch_and_is_audited(tmp_path):
    """Po reconcile czysty stan może zdjąć wyłącznie latch za ACK ownera."""
    state_path = str(tmp_path / "card-state.json")
    audit_path = str(tmp_path / "audit.jsonl")
    before = _state(
        executed_total=2,
        executed_ts=[NOW.timestamp() - 60, NOW.timestamp()],
        in_flight=None,
        pending_verification=[],
        auto_off_latch=True,
        auto_off_reason="runner_outcome_unknown",
        auto_off_ts=NOW.isoformat(),
    )
    AC.save_state(state_path, before)

    after = AC.clear_latch(
        state_path,
        reason="owner ACK po reconcile 5b i verify-execution",
        operator="operator-test",
        owner_ack_phrase=OWNER_ACK_PHRASE,
        now=NOW,
        audit_path=audit_path,
    )

    assert after == {**before, "auto_off_latch": False}
    row = json.loads(
        Path(audit_path).read_text(encoding="utf-8").strip()
    )
    assert row["kind"] == "authority_latch_cleared"
    assert row["reason"] == "owner ACK po reconcile 5b i verify-execution"
    assert row["operator"] == "operator-test"
    assert row["owner_ack_phrase"] == OWNER_ACK_PHRASE


def test_new_latch_sends_one_unthrottled_alert(tmp_path, monkeypatch):
    """F2: alert idzie wyłącznie na przejściu latch OFF→ON."""
    paths = _executor_paths(tmp_path)
    audit = Path(paths["authority_audit_path"])
    card = Path(paths["authority_card_path"])
    _grant_owner_auth(monkeypatch, audit)
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    body = json.loads(card.read_text(encoding="utf-8"))
    body["owner_ack"]["phrase"] += " TAMPER"
    _write_card(card, body)
    messages = []

    first = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        notifier=messages.append,
        **paths,
    )
    second = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        notifier=messages.append,
        **paths,
    )

    assert first["blocked"] == "authority_card_sha_mismatch"
    assert second["blocked"] == "authority_card_latch_on"
    latch_alerts = [
        message for message in messages if "AUTO-OFF ZATRZAŚNIĘTE" in message
    ]
    assert len(latch_alerts) == 1
    assert "sha_mismatch" in latch_alerts[0]


def test_executor_missing_heartbeat_denies_and_latches(tmp_path, monkeypatch):
    paths = _executor_paths(tmp_path)
    Path(paths["monitor_heartbeat_path"]).unlink()
    _grant_owner_auth(monkeypatch, Path(paths["authority_audit_path"]))
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    calls = []
    out = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (
            calls.append(args)
            or (True, "ASSIGN_OK: fixture [verify_ok_kid=101]")
        ),
        notifier=lambda _text: None,
        **paths,
    )
    assert out["blocked"] == "monitor_heartbeat_stale"
    assert calls == []
    state = AC.load_state(paths["authority_state_path"])
    assert state["auto_off_latch"] is True
    assert state["auto_off_reason"] == "monitor_heartbeat_stale"


@pytest.mark.parametrize("checks", [
    {"verdict": "ALARM", "reasons": ["counter_divergence"]},
    {},
    {"verdict": "UNKNOWN"},
    [],
])
def test_heartbeat_requires_explicit_ok_verdict(tmp_path, checks):
    """RED I7: świeżość procesu bez jawnego verdict=OK nie daje execute."""
    heartbeat = tmp_path / "heartbeat.json"
    heartbeat.write_text(
        json.dumps({"ts": NOW.isoformat(), "pid": 123, "checks": checks}),
        encoding="utf-8",
    )
    assert E.AAM.heartbeat_fresh(str(heartbeat), NOW) == (
        False,
        "monitor_verdict_not_ok",
    )


def test_executor_monitor_alarm_denies_latches_and_mutation_reopens(tmp_path, monkeypatch):
    """RED/mutation I7: usunięcie konsumpcji verdictu znów uruchamia runner."""
    real_root = tmp_path / "real"
    real_root.mkdir()
    paths = _executor_paths(real_root)
    Path(paths["monitor_heartbeat_path"]).write_text(
        json.dumps({
            "ts": NOW.isoformat(),
            "pid": 123,
            "checks": {
                "verdict": "ALARM",
                "reasons": ["counter_divergence"],
            },
        }),
        encoding="utf-8",
    )
    _grant_owner_auth(monkeypatch, Path(paths["authority_audit_path"]))
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    calls = []
    out = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (
            calls.append(args)
            or (True, "ASSIGN_OK: fixture [verify_ok_kid=101]")
        ),
        notifier=lambda _text: None,
        **paths,
    )
    assert out["blocked"] == "monitor_verdict_not_ok"
    assert calls == []
    state = AC.load_state(paths["authority_state_path"])
    assert state["auto_off_latch"] is True
    assert state["auto_off_reason"] == "monitor_verdict_not_ok"

    mutant_root = tmp_path / "mutant"
    mutant_root.mkdir()
    mutant_paths = _executor_paths(mutant_root)
    Path(mutant_paths["monitor_heartbeat_path"]).write_text(
        Path(paths["monitor_heartbeat_path"]).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _grant_owner_auth(
        monkeypatch,
        Path(mutant_paths["authority_audit_path"]),
    )
    monkeypatch.setattr(E.AAM, "heartbeat_fresh", lambda *_a, **_k: (True, "ok"))
    mutated_calls = []
    mutated = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (
            mutated_calls.append(args)
            or (True, "ASSIGN_OK: fixture [verify_ok_kid=101]")
        ),
        notifier=lambda _text: None,
        **mutant_paths,
    )
    assert mutated["executed"] is True
    assert len(mutated_calls) == 1


def test_executor_route_generation_stale_denies_without_latch(
    tmp_path, monkeypatch
):
    paths = _executor_paths(tmp_path)
    paths["commit_recheck_provider"] = lambda *_args, **_kwargs: {
        **_commit_snapshot(),
        "winner": {**_commit_snapshot()["winner"], "route_generation": 2},
    }
    _grant_owner_auth(monkeypatch, Path(paths["authority_audit_path"]))
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    calls = []
    out = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (
            calls.append(args)
            or (True, "ASSIGN_OK: fixture [verify_ok_kid=101]")
        ),
        notifier=lambda _text: None,
        **paths,
    )
    assert out["blocked"] == "commit_recheck_route_generation"
    assert calls == []
    assert AC.load_state(paths["authority_state_path"])[
        "auto_off_latch"
    ] is False


def test_pytest_guard_refuses_default_production_paths():
    verdict = AC.verify_card(
        now=NOW,
        code_git_sha=GIT_SHA,
        flag_fp=FLAG_FP,
    )
    assert verdict.valid is False
    assert verdict.reason == "pytest_prod_path_blocked"


class _IdentityRegistry:
    def __init__(self, resolved):
        self.resolved = resolved

    def resolve(self, _name, profile="worker", *, bare_key_strict=False):
        assert profile == "worker"
        assert bare_key_strict is True
        return self.resolved


def test_h1_ambiguous_runner_identity_is_denied_without_latch(
    tmp_path,
    monkeypatch,
):
    """RED H1: nazwa musi jednoznacznie wskazywać zamierzony canon_cid."""
    paths = _executor_paths(tmp_path)
    paths["identity_registry_provider"] = lambda: _IdentityRegistry(None)
    _grant_owner_auth(monkeypatch, Path(paths["authority_audit_path"]))
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    calls = []

    out = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (
            calls.append(args)
            or (True, "ASSIGN_OK: fixture [verify_ok_kid=101]")
        ),
        notifier=lambda _text: None,
        **paths,
    )

    assert out["blocked"] == "runner_identity_ambiguous"
    assert calls == []
    assert AC.load_state(paths["authority_state_path"])["auto_off_latch"] is False


def test_h1_runner_readback_mismatch_latches_and_keeps_reservation(
    tmp_path,
    monkeypatch,
):
    """RED H1: potwierdzenie innego CID jest unknown + reconcile, nie sukcesem."""
    paths = _executor_paths(tmp_path)
    _grant_owner_auth(monkeypatch, Path(paths["authority_audit_path"]))
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    monkeypatch.setenv("ALLOW_AUTO_ASSIGN_STATE_IN_TEST", "1")

    out = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *_args: (
            True,
            "ASSIGN_OK: fixture [verify_ok_kid=999]",
        ),
        notifier=lambda _text: None,
        **paths,
    )

    assert out["executed"] is False
    assert out["runner_outcome"] == "unknown"
    assert out["runner_msg"].endswith("verify_ok_kid=999]")
    state = AC.load_state(paths["authority_state_path"])
    assert state["executed_total"] == 1
    assert state["in_flight"] == "480300"
    assert state["auto_off_latch"] is True
    assert state["auto_off_reason"] == "runner_identity_mismatch"


def test_h1_missing_runner_readback_is_unknown_and_keeps_reservation(
    tmp_path,
    monkeypatch,
):
    """RED I3: na enforced lane sukces bez CID read-back jest stanem UNKNOWN."""
    paths = _executor_paths(tmp_path)
    _grant_owner_auth(monkeypatch, Path(paths["authority_audit_path"]))
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    monkeypatch.setenv("ALLOW_AUTO_ASSIGN_STATE_IN_TEST", "1")

    out = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *_args: (True, "ASSIGN_OK: fixture"),
        notifier=lambda _text: None,
        **paths,
    )

    assert out["executed"] is False
    assert out["runner_outcome"] == "unknown"
    state = AC.load_state(paths["authority_state_path"])
    assert state["executed_total"] == 1
    assert state["in_flight"] == "480300"
    assert state["pending_verification"] == ["480300"]
    assert state["auto_off_latch"] is True
    assert state["auto_off_reason"] == "runner_outcome_unknown"


def test_h2_oserror_after_child_start_is_unknown_and_never_rolls_back(
    tmp_path,
    monkeypatch,
):
    """RED H2: skutek, potem OSError z communicate => rezerwacja zostaje."""
    paths = _executor_paths(tmp_path)
    _grant_owner_auth(monkeypatch, Path(paths["authority_audit_path"]))
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    monkeypatch.setenv("ALLOW_AUTO_ASSIGN_STATE_IN_TEST", "1")
    monkeypatch.setattr(E, "_pytest_active", lambda: False)
    effect = tmp_path / "panel-effect"

    class StartedChild:
        returncode = 0

        def communicate(self, timeout=None):
            assert timeout == E.GASTRO_ASSIGN_TIMEOUT_SEC
            effect.write_text("assigned", encoding="utf-8")
            raise OSError("post-start read failure")

        def kill(self):
            pass

    def legacy_run(*_args, **_kwargs):
        effect.write_text("assigned", encoding="utf-8")
        raise OSError("post-start legacy run failure")

    monkeypatch.setattr(E.subprocess, "Popen", lambda *_a, **_k: StartedChild())
    monkeypatch.setattr(E.subprocess, "run", legacy_run)

    out = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        notifier=lambda _text: None,
        **paths,
    )

    assert effect.read_text(encoding="utf-8") == "assigned"
    assert out["runner_outcome"] == "unknown"
    state = AC.load_state(paths["authority_state_path"])
    assert state["executed_total"] == 1
    assert state["in_flight"] == "480300"
    assert state["auto_off_reason"] == "runner_outcome_unknown"


def test_h3_initialize_state_binds_card_atomically_and_is_idempotent(tmp_path):
    """RED H3: tor podpisu tworzy trwały marker konkretnej karty."""
    path = tmp_path / "card-state.json"
    first = AC.initialize_state(str(path), "b" * 64, NOW)
    second = AC.initialize_state(str(path), "b" * 64, NOW + timedelta(seconds=1))

    assert first == second
    assert first["initialized_for_card"] == "b" * 64
    assert first["initialized_at"] == NOW.isoformat()
    assert AC.load_state(str(path)) == first
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("card-state.json.tmp.*"))


def test_h3_missing_signed_state_is_latched_not_a_fresh_budget(
    tmp_path,
    monkeypatch,
):
    """RED/mutation H3: usunięcie state_missing gate znów uruchomi runner."""
    paths = _executor_paths(tmp_path)
    Path(paths["authority_state_path"]).unlink()
    _grant_owner_auth(monkeypatch, Path(paths["authority_audit_path"]))
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    calls = []

    out = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (calls.append(args) or (True, "ok")),
        notifier=lambda _text: None,
        **paths,
    )

    assert out["blocked"] == "authority_card_state_missing"
    assert calls == []
    state = AC.load_state(paths["authority_state_path"])
    assert state["auto_off_latch"] is True
    assert state["auto_off_reason"] == "state_missing"
    with pytest.raises(ValueError, match="utracona historia budżetu"):
        AC.clear_latch(
            paths["authority_state_path"],
            reason="mutation probe",
            operator="test",
            owner_ack_phrase=OWNER_ACK_PHRASE,
            now=NOW,
            audit_path=paths["authority_audit_path"],
        )

    # Mutation control: usuń oba guardy wiążące receipt ze stanem, odtwarzając
    # poprzedni empty_state-on-missing — ten sam przypadek znów dochodzi do runnera.
    mutated_root = tmp_path / "mutation"
    mutated_root.mkdir()
    mutated_paths = _executor_paths(mutated_root)
    Path(mutated_paths["authority_state_path"]).unlink()
    _grant_owner_auth(
        monkeypatch,
        Path(mutated_paths["authority_audit_path"]),
    )
    body = json.loads(
        Path(mutated_paths["authority_card_path"]).read_text(encoding="utf-8")
    )

    def missing_state_as_fresh_budget(**kwargs):
        return True, "ok", {
            "enforced": True,
            "state": AC.empty_state(),
            "state_path": kwargs["state_path"],
            "card_sha256": AC.card_sha256(body),
        }

    monkeypatch.setattr(E, "_authority_card_gate", missing_state_as_fresh_budget)
    mutated_calls = []
    mutated = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (
            mutated_calls.append(args)
            or (True, "ASSIGN_OK: fixture [verify_ok_kid=101]")
        ),
        notifier=lambda _text: None,
        **mutated_paths,
    )
    assert mutated["executed"] is True
    assert len(mutated_calls) == 1


def test_h4_clock_is_sampled_after_locks_for_heartbeat_and_proposal(
    tmp_path,
    monkeypatch,
):
    """RED H4: 61 s czekania na lock unieważnia wejściowy heartbeat."""
    paths = _executor_paths(tmp_path)
    body = json.loads(Path(paths["authority_card_path"]).read_text(encoding="utf-8"))
    AC.initialize_state(paths["authority_state_path"], AC.card_sha256(body), NOW)
    _grant_owner_auth(monkeypatch, Path(paths["authority_audit_path"]))
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    monkeypatch.setattr(
        E,
        "_fresh_execution_now",
        lambda: NOW + timedelta(seconds=61),
    )
    calls = []

    out = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (
            calls.append(args)
            or (True, "ASSIGN_OK: fixture [verify_ok_kid=101]")
        ),
        notifier=lambda _text: None,
        **paths,
    )

    assert out["blocked"] == "monitor_heartbeat_stale"
    assert calls == []


def test_h5_hot_flip_off_between_recheck_and_final_gate_stops_execution(
    tmp_path,
    monkeypatch,
):
    """RED/mutation H5: finalny odczyt źródłowy musi pokonać snapshot ticku."""
    paths = _executor_paths(tmp_path)
    body = json.loads(Path(paths["authority_card_path"]).read_text(encoding="utf-8"))
    AC.initialize_state(paths["authority_state_path"], AC.card_sha256(body), NOW)
    _grant_owner_auth(monkeypatch, Path(paths["authority_audit_path"]))
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    hot = {"enabled": True}

    def flip_during_recheck(_oid, _payload, now=None):
        hot["enabled"] = False
        return _commit_snapshot()

    paths["commit_recheck_provider"] = flip_during_recheck
    monkeypatch.setattr(
        E,
        "_fresh_execution_flags",
        lambda: (hot["enabled"], FLAG_FP),
    )
    calls = []
    out = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (
            calls.append(args)
            or (True, "ASSIGN_OK: fixture [verify_ok_kid=101]")
        ),
        notifier=lambda _text: None,
        **paths,
    )
    assert out["blocked"] == "flag_off_at_execution"
    assert calls == []

    # Mutation control: zamrożenie starego ON usuwa sygnał oracle i odpala runner.
    hot["enabled"] = True
    paths["commit_recheck_provider"] = lambda *_a, **_k: _commit_snapshot()
    monkeypatch.setattr(E, "_fresh_execution_flags", lambda: (True, FLAG_FP))
    mutated_calls = []
    mutated = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (
            mutated_calls.append(args)
            or (True, "ASSIGN_OK: fixture [verify_ok_kid=101]")
        ),
        notifier=lambda _text: None,
        **paths,
    )
    assert mutated["executed"] is True
    assert len(mutated_calls) == 1


def test_j2_heartbeat_aging_during_solve_stops_final_execution(
    tmp_path,
    monkeypatch,
):
    """RED/mutation J2: finalny gate musi ponowić heartbeat po fresh solve."""
    paths = _executor_paths(tmp_path)
    body = json.loads(Path(paths["authority_card_path"]).read_text(encoding="utf-8"))
    AC.initialize_state(paths["authority_state_path"], AC.card_sha256(body), NOW)
    _grant_owner_auth(monkeypatch, Path(paths["authority_audit_path"]))
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)
    clock = iter([NOW, NOW + timedelta(seconds=61)])
    monkeypatch.setattr(E, "_fresh_execution_now", lambda: next(clock))
    solve_calls = []

    def solve_while_heartbeat_ages(_oid, _payload, now=None):
        solve_calls.append(now)
        return _commit_snapshot()

    paths["commit_recheck_provider"] = solve_while_heartbeat_ages
    calls = []
    out = E.maybe_execute(
        _record(),
        SimpleNamespace(would_auto_assign=True),
        {"status_id": 2},
        now=NOW,
        assign_runner=lambda *args: (
            calls.append(args)
            or (True, "ASSIGN_OK: fixture [verify_ok_kid=101]")
        ),
        notifier=lambda _text: None,
        **paths,
    )

    assert solve_calls == [NOW]
    assert out["blocked"] == "monitor_heartbeat_stale"
    assert calls == []
    state = AC.load_state(paths["authority_state_path"])
    assert state["auto_off_latch"] is True
    assert state["auto_off_reason"] == "monitor_heartbeat_stale"


def test_h5_source_read_bypasses_active_flag_snapshot(tmp_path, monkeypatch):
    """Ratchet H5: snapshot ON nie może zasłonić źródłowego killswitcha OFF."""
    flags_path = tmp_path / "flags.json"
    flags_path.write_text(
        json.dumps({"ENABLE_AUTO_ASSIGN": False}),
        encoding="utf-8",
    )
    monkeypatch.setattr(C, "FLAGS_PATH", flags_path)
    monkeypatch.setattr(
        C,
        "_FLAGS_SNAPSHOT_OVERRIDE",
        {"ENABLE_AUTO_ASSIGN": True},
    )
    monkeypatch.setattr(E, "_fresh_execution_flags", REAL_FRESH_EXECUTION_FLAGS)

    assert C.decision_flag("ENABLE_AUTO_ASSIGN") is True
    enabled, fingerprint = E._fresh_execution_flags()
    assert enabled is False
    assert "ENABLE_AUTO_ASSIGN=0" in fingerprint


def test_h6_stop_contract_hash_is_bound_and_template_uses_canonical_value(tmp_path):
    """RED H6: dowolne 64 hex nie może udawać podpisanego kontraktu stopu."""
    card, audit, body = _valid_files(tmp_path)
    assert AC.template_body()["stop_contract_sha256"] == STOP_CONTRACT_SHA256
    assert AC.EXPECTED_STOP_CONTRACT_SHA256 == STOP_CONTRACT_SHA256
    assert _verify(card, audit).valid is True

    body["stop_contract_sha256"] = "0" * 64
    _write_card(card, body)
    _write_audit(audit, body)
    verdict = _verify(card, audit)
    assert verdict.valid is False
    assert verdict.reason == "stop_contract_mismatch"
