"""T5 — maszynowe dowody zakresu karty ``auto.canary.v1``.

Negatywny oracle jest celowo fail-closed: brak któregokolwiek predykatu karty
nie może zostać zinterpretowany jako zgoda na AUTO.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from dispatch_v2 import authority_card
from dispatch_v2 import authority_scope
from dispatch_v2 import shadow_dispatcher
from dispatch_v2.tools import write_build_sha


NOW = datetime(2026, 7, 28, 7, 0, tzinfo=timezone.utc)
OID = "490900"
EVENT_ID = f"{OID}_NEW_ORDER_first"


def _result(*, metrics=None, best_effort=False):
    plan = SimpleNamespace(
        sequence=[OID],
        pickup_at={OID: NOW},
        predicted_delivered_at={OID: NOW},
        total_duration_min=12.0,
        strategy="ortools",
        sla_violations=0,
        osrm_fallback_used=False,
        per_order_delivery_times={OID: 12.0},
    )
    default_metrics = {
        "bag_size_before": 0,
        "bag_context": [],
        "plan_expected_version": 17,
        "pos_source": "gps",
        "pos_age_sec": 30.0,
        "paczka_is": False,
    }
    default_metrics.update(metrics or {})
    best = SimpleNamespace(
        courier_id="75",
        name="Test",
        score=100.0,
        feasibility_verdict="MAYBE",
        feasibility_reason="",
        plan=plan,
        metrics=default_metrics,
        best_effort=best_effort,
        traffic_v2_shadow_route=None,
    )
    return SimpleNamespace(
        order_id=OID,
        verdict="PROPOSE",
        reason="synthetic",
        best=best,
        candidates=[best],
        pickup_ready_at=NOW,
        restaurant=None,
        delivery_address=None,
        pool_total_count=1,
        pool_feasible_count=1,
        authority_scope=None,
    )


def _order_event():
    return {
        "event_id": EVENT_ID,
        "event_type": "NEW_ORDER",
        "status_id": 2,
        "order_id": OID,
        "address_id": "101",
        "order_type": "elastic",
    }


def _order_state():
    return {
        "status": "planned",
        "courier_id": None,
        "last_lifecycle_event_id_new_order": EVENT_ID,
        "history": [
            {"event": "NEW_ORDER", "status": "planned", "at": NOW.isoformat()},
        ],
    }


def _full_scope():
    evidence = {
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
                "generation": 17,
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
            "4_mode": {
                "mode": "normal",
                "source": "synthetic.authoritative_mode",
            },
            "5_exclusions": {
                "reassign": {"value": False, "source": "orders_state.history"},
                "alarm": {"value": False, "source": "synthetic.authoritative_mode"},
                "least_damage": {
                    "value": False,
                    "source": "Candidate.best_effort",
                },
                "parcel": {"value": False, "source": "common.is_paczka_order"},
                "multi_brand": {"value": False, "source": "synthetic.brand_contract"},
                "shared_pickup": {
                    "value": False,
                    "source": "synthetic.pickup_contract",
                },
                "coordinator_override": {
                    "value": False,
                    "source": "synthetic.override_contract",
                },
            },
            "6_winner_position": {
                "pos_source": "gps",
                "age_seconds": 30.0,
                "contract": "LIVE",
                "sources": {
                    "position": "CourierState.pos_source",
                    "age": "CourierState.pos_age_min*60",
                    "contract": "live_eta.classify_position_contract",
                },
            },
            "7_no_gps_parity": {
                "verified": True,
                "source": "synthetic.structural_parity_attestation",
            },
        },
    }
    return evidence


def _scope_contract():
    return authority_card.template_body()["scope"]


def _assert_no_pii_keys(value):
    forbidden = {
        "name",
        "courier_name",
        "restaurant",
        "address",
        "delivery_address",
        "pickup_address",
        "phone",
        "telephone",
    }
    if isinstance(value, dict):
        assert forbidden.isdisjoint({str(key).lower() for key in value})
        for nested in value.values():
            _assert_no_pii_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_pii_keys(nested)


def test_producer_emits_v1_with_honest_absences_and_no_pii():
    result = _result()
    block = authority_scope.build_authority_scope(
        result,
        _order_event(),
        _order_state(),
    )

    assert block["schema"] == "authority_scope.v1"
    predicates = block["predicates"]
    assert predicates["1_new_unassigned"]["prior_assignment_count"] == 0
    assert predicates["2_empty_bag"] == {
        "bag_size": 0,
        "active_order_ids": [],
        "generation": 17,
        "sources": {
            "bag": "Candidate.metrics.bag_context",
            "generation": "Candidate.metrics.plan_expected_version",
        },
    }
    assert predicates["3_solo_plan"]["n_pickups"] == 1
    assert predicates["3_solo_plan"]["n_deliveries"] == 1
    assert "absent" in predicates["4_mode"]
    assert predicates["5_exclusions"]["parcel"]["value"] is False
    assert "absent" in predicates["5_exclusions"]["multi_brand"]
    assert predicates["6_winner_position"]["age_seconds"] == 30.0
    assert predicates["6_winner_position"]["contract"] == "LIVE"
    assert "absent" in predicates["7_no_gps_parity"]
    _assert_no_pii_keys(block)


def test_producer_marks_predicates_absent_when_inputs_are_missing():
    result = _result(
        metrics={
            "bag_size_before": None,
            "bag_context": None,
            "plan_expected_version": None,
            "pos_source": None,
            "pos_age_sec": None,
            "paczka_is": None,
        }
    )
    result.best.plan = None
    block = authority_scope.build_authority_scope(result, {}, None)
    predicates = block["predicates"]

    for key in (
        "1_new_unassigned",
        "2_empty_bag",
        "3_solo_plan",
        "4_mode",
        "6_winner_position",
        "7_no_gps_parity",
    ):
        assert isinstance(predicates[key].get("absent"), str)
        assert predicates[key]["absent"]
    assert "absent" in predicates["5_exclusions"]["parcel"]


def test_producer_failure_is_observational_and_fails_closed(monkeypatch):
    def _raise(*_args):
        raise RuntimeError("synthetic classifier failure")

    monkeypatch.setattr(
        authority_scope.live_eta,
        "classify_position_contract",
        _raise,
    )
    result = _result()
    block = authority_scope.attach_authority_scope(
        result,
        _order_event(),
        _order_state(),
    )

    assert block is result.authority_scope
    assert block is result.best.metrics["authority_scope"]
    for evidence in block["predicates"].values():
        assert evidence == {
            "absent": "authority_scope producer error: RuntimeError"
        }


def test_authority_scope_reaches_top_level_and_serializer_locations_a_b():
    result = _result()
    block = authority_scope.attach_authority_scope(
        result,
        _order_event(),
        _order_state(),
    )

    location_a = shadow_dispatcher._serialize_candidate(result.best)
    record = shadow_dispatcher._serialize_result(
        result,
        EVENT_ID,
        latency_ms=1.0,
    )
    assert location_a["authority_scope"] == block
    assert record["best"]["authority_scope"] == block
    assert record["authority_scope"] == block
    assert "authority_scope" not in shadow_dispatcher._METRICS_EXCLUDE


def test_single_producer_callsite_ratchet():
    source = inspect.getsource(shadow_dispatcher)
    assert source.count(".attach_authority_scope(") == 1


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        ("1_new_unassigned", "scope_1_absent"),
        ("2_empty_bag", "scope_2_absent"),
        ("3_solo_plan", "scope_3_absent"),
        ("4_mode", "scope_4_absent"),
        ("5_exclusions", "scope_5_absent"),
        ("6_winner_position", "scope_6_absent"),
        ("7_no_gps_parity", "scope_7_absent"),
    ],
)
def test_check_scope_refuses_each_absent_predicate(predicate, expected):
    block = _full_scope()
    if predicate == "5_exclusions":
        block["predicates"][predicate]["multi_brand"] = {"absent": "no source"}
    else:
        block["predicates"][predicate] = {"absent": "no source"}
    ok, reason = authority_card.check_scope(
        {"authority_scope": block},
        None,
        None,
        _scope_contract(),
    )
    assert ok is False
    assert reason == expected


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda p: p["1_new_unassigned"].update(
                {"prior_assignment_count": 1}
            ),
            "scope_not_new_unassigned",
        ),
        (
            lambda p: p["2_empty_bag"].update(
                {"bag_size": 1, "active_order_ids": ["old"]}
            ),
            "scope_bag_not_empty",
        ),
        (
            lambda p: p["3_solo_plan"].update({"n_deliveries": 2}),
            "scope_not_solo_route",
        ),
        (
            lambda p: p["4_mode"].update({"mode": "alarm"}),
            "scope_not_normal_mode",
        ),
        (
            lambda p: p["5_exclusions"]["parcel"].update({"value": True}),
            "scope_excluded_parcel",
        ),
        (
            lambda p: p["6_winner_position"].update({"contract": "WARM"}),
            "scope_gps_not_live",
        ),
        (
            lambda p: p["7_no_gps_parity"].update({"verified": False}),
            "scope_no_gps_parity_unproven",
        ),
    ],
)
def test_check_scope_refuses_each_unsatisfied_predicate(mutate, expected):
    block = _full_scope()
    mutate(block["predicates"])
    ok, reason = authority_card.check_scope(
        {"authority_scope": block},
        None,
        None,
        _scope_contract(),
    )
    assert ok is False
    assert reason == expected


def test_check_scope_happy_path():
    ok, reason = authority_card.check_scope(
        {"authority_scope": _full_scope()},
        None,
        None,
        _scope_contract(),
    )
    assert (ok, reason) == (True, "ok")


def test_mutation_oracle_absent_refusal_is_material():
    """Mutation-oracle: usunięcie odmowy na ``absent`` musi zmienić werdykt."""
    block = _full_scope()
    # Pozostałe pola są celowo kompletne. Mutant usuwający wyłącznie obsługę
    # znacznika ``absent`` przepuści ten rekord i test zrobi się czerwony.
    block["predicates"]["7_no_gps_parity"]["absent"] = (
        "per-record evidence cannot prove policy parity"
    )
    fixed = authority_card.check_scope(
        {"authority_scope": block},
        None,
        None,
        _scope_contract(),
    )
    block["predicates"]["7_no_gps_parity"].pop("absent")
    without_absent = authority_card.check_scope(
        {"authority_scope": block},
        None,
        None,
        _scope_contract(),
    )
    assert fixed == (False, "scope_7_absent")
    assert without_absent == (True, "ok")


def test_build_sha_atomic_write_and_verify_are_hermetic(tmp_path):
    path = tmp_path / "BUILD_SHA"
    first = "a" * 40
    second = "b" * 40

    assert write_build_sha.write_sha(path, first) is True
    assert path.read_text(encoding="ascii") == f"{first}\n"
    assert write_build_sha.verify_sha(path, first) is True
    assert write_build_sha.verify_sha(path, second) is False
    assert write_build_sha.write_sha(path, first) is False

    assert write_build_sha.write_sha(path, second) is True
    assert path.read_text(encoding="ascii") == f"{second}\n"
    assert list(tmp_path.glob(".BUILD_SHA.*.tmp")) == []


def test_build_sha_verify_cli_returns_zero_or_one_hermetically(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "BUILD_SHA"
    expected = "c" * 40
    monkeypatch.setattr(authority_card, "BUILD_SHA_PATH", str(path))
    monkeypatch.setattr(write_build_sha, "git_head", lambda _repo: expected)

    path.write_text(f"{expected}\n", encoding="ascii")
    assert write_build_sha.main(
        ["--verify", "--repo", str(tmp_path)]
    ) == 0
    path.write_text(f"{'d' * 40}\n", encoding="ascii")
    assert write_build_sha.main(
        ["--verify", "--repo", str(tmp_path)]
    ) == 1


def test_build_sha_rejects_non_git_sha(tmp_path):
    with pytest.raises(ValueError):
        write_build_sha.write_sha(tmp_path / "BUILD_SHA", "not-a-sha")
