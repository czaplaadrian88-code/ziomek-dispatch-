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

NOW = datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)
GIT_SHA = "7a63f8008abcdef0123456789abcdef012345678"
FLAG_FP = "ENABLE_AUTO_ASSIGN=True|fixture=v1"


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
        "stop_contract_sha256": "a" * 64,
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
    state = AC.empty_state()
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
                "generation": 1,
                "sources": {
                    "bag": "Candidate.metrics.bag_context",
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
    card, audit, _ = _valid_files(tmp_path)
    heartbeat = tmp_path / "monitor-heartbeat.json"
    heartbeat.write_text(
        json.dumps({"ts": NOW.isoformat(), "pid": 123, "checks": {}}),
        encoding="utf-8",
    )
    return {
        "authority_card_path": str(card),
        "authority_audit_path": str(audit),
        "authority_state_path": str(tmp_path / "card-state.json"),
        "code_git_sha": GIT_SHA,
        "flag_fp": FLAG_FP,
        "state_path": str(tmp_path / "auto-state.json"),
        "monitor_heartbeat_path": str(heartbeat),
        "shadow_decisions_path": str(tmp_path / "shadow.jsonl"),
        "commit_recheck_provider": (
            lambda _oid, _payload, now=None: _commit_snapshot()
        ),
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
        assign_runner=lambda *args: (runner_calls.append(args) or (True, "ok")),
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
        assign_runner=lambda *args: (calls.append(args) or (True, "ok")),
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
        assign_runner=lambda *args: (calls.append(args) or (True, "ok")),
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
        assign_runner=lambda *args: (calls.append(args) or (True, "ok")),
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
        assign_runner=lambda *args: (calls.append(args) or (True, "ok")),
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
        assign_runner=lambda *_args: (True, "ASSIGN_OK: fixture"),
        notifier=lambda _text: None,
        **paths,
    )
    assert out["executed"] is True
    state = AC.load_state(paths["authority_state_path"])
    assert state["executed_total"] == 1
    assert state["in_flight"] == "480300"
    assert state["pending_verification"] == ["480300"]
    assert state["executed_ts"] == [NOW.timestamp()]


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
        assign_runner=lambda *args: (calls.append(args) or (True, "ok")),
        notifier=lambda _text: None,
        **paths,
    )
    assert out["blocked"] == "monitor_heartbeat_stale"
    assert calls == []
    state = AC.load_state(paths["authority_state_path"])
    assert state["auto_off_latch"] is True
    assert state["auto_off_reason"] == "monitor_heartbeat_stale"


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
        assign_runner=lambda *args: (calls.append(args) or (True, "ok")),
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
