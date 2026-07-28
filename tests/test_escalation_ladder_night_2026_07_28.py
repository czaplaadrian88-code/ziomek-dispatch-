"""RED-first oracle: drabina S1 -> S2 -> S3, noc 2026-07-28.

Każdy test jest niezależnym oraclem kontraktu, nie autowalidacją kształtu:
carry liczymy z literalnych timestampów, Alarm z kontrfaktycznej puli, a S2
z jawnej macierzy slot × flota.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as DP
from dispatch_v2 import shadow_dispatcher as SD
from dispatch_v2.core import carry_freshness as CF
from dispatch_v2.core import alarm_certificate as AC
from dispatch_v2.core import strategy2_probe as S2


NOW = datetime(2026, 7, 28, 20, 0, tzinfo=timezone.utc)


def _plan(**drops):
    return SimpleNamespace(
        predicted_delivered_at=drops,
        pickup_at={},
        sequence=list(drops),
    )


def _order(oid, *, picked=None, physical=None, physical_source=None):
    out = SimpleNamespace(
        order_id=oid,
        status="picked_up" if picked else "assigned",
        picked_up_at=picked,
        pickup_ready_at=NOW,
    )
    out.physical_possession_at = physical
    out.physical_possession_source = physical_source
    return out


# A — kanoniczny carry


def test_A_carry_uses_handoff_with_dropoff_dwell_and_bound_possession():
    """Mutation: arrival zamiast handoff dałby 32 min, a oracle wymaga 36."""
    possession = NOW
    plan = _plan(food=NOW + timedelta(minutes=36))
    order = _order(
        "food",
        picked=NOW + timedelta(minutes=2),  # klik-proxy nie może wygrać
        physical=possession,
        physical_source="gps_bag_sensor",
    )
    got = CF.evaluate_plan(plan, [order])
    item = got["orders"][0]
    assert item == {
        "order_id": "food",
        "carry_min": 36.0,
        "le_35": False,
        "le_40": True,
        "source": "bound",
        "possession_source": "gps_bag_sensor",
        "handoff_source": "predicted_delivery_with_dropoff_dwell",
    }
    assert got["max_carry_min"] == 36.0
    assert got["all_le_35"] is False and got["all_le_40"] is True


def test_A_unbound_pickup_is_explicit_proxy_never_bound():
    plan = _plan(food=NOW + timedelta(minutes=30))
    plan.pickup_at["food"] = NOW + timedelta(minutes=5)
    got = CF.evaluate_plan(plan, [_order("food")])
    item = got["orders"][0]
    assert item["carry_min"] == 25.0
    assert item["source"] == "proxy"
    assert item["possession_source"] == "planned_pickup_at"


def test_A_missing_possession_is_unevaluable_not_zero():
    got = CF.evaluate_plan(_plan(food=NOW + timedelta(minutes=20)), [_order("food")])
    assert got["status"] == "UNEVALUABLE"
    assert got["unknown_count"] == 1
    assert got["orders"][0]["carry_min"] is None


# B — kontrfaktyczny certyfikat Alarm


@dataclass
class _Candidate:
    courier_id: str
    carry: float | None
    feasibility_verdict: str = "MAYBE"
    feasibility_reason: str = "ok"

    def __post_init__(self):
        self.metrics = {
            "carry_eval": {
                "schema": "carry_eval.v1",
                "status": "EVALUATED" if self.carry is not None else "UNEVALUABLE",
                "max_carry_min": self.carry,
                "unknown_count": 0 if self.carry is not None else 1,
            }
        }


def test_B_counterfactual_normal_wins_over_any_load_signal():
    cert = AC.build(
        [_Candidate("c35", 35.0), _Candidate("c38", 38.0, "NO", "R6_per_order_>35min")],
        decision_order_id="o1",
        now=NOW,
    )
    assert cert["classification"] == "NORMAL"
    assert cert["alarm"] is False
    assert cert["counterfactual"]["le_35_count"] == 1


def test_B_alarm_requires_zero_le35_and_at_least_one_35_40():
    cert = AC.build(
        [_Candidate("c37", 37.0, "NO", "R6_per_order_>35min"),
         _Candidate("c44", 44.0, "NO", "R6_per_order_>35min")],
        decision_order_id="o2",
        now=NOW,
    )
    assert cert["classification"] == "ALARM_CANDIDATE"
    assert cert["alarm"] is True
    assert cert["counterfactual"]["le_35_count"] == 0
    assert cert["counterfactual"]["between_35_40_count"] == 1


def test_B_other_hard_reject_cannot_forge_alarm_candidate():
    cert = AC.build(
        [_Candidate("too_far", 37.0, "NO", "pickup_too_far (99 km)")],
        decision_order_id="o3",
        now=NOW,
    )
    assert cert["classification"] == "HARD_NO_CANDIDATE"
    assert cert["alarm"] is False
    assert cert["counterfactual"]["between_35_40_count"] == 0


def test_B_unknown_candidate_blocks_zero_le35_counterfactual():
    cert = AC.build(
        [_Candidate("unknown", None),
         _Candidate("c37", 37.0, "NO", "R6_per_order_>35min")],
        decision_order_id="o-unknown",
        now=NOW,
    )
    assert cert["classification"] == "UNEVALUABLE"
    assert cert["alarm"] is False
    assert cert["counterfactual"]["unknown_count"] == 1


def test_B_mutation_missing_counterfactual_is_not_a_certificate():
    forged = {
        "schema": "alarm_certificate.v1",
        "alarm": True,
        "classification": "ALARM_CANDIDATE",
        "decision_order_id": "o4",
        "observed_at": NOW.isoformat(),
        "valid_until": (NOW + timedelta(minutes=2)).isoformat(),
    }
    assert AC.validate(forged, NOW, decision_order_id="o4") is False


def test_B_forged_dict_cannot_open_loadgov_loose_window():
    from dispatch_v2.core import loadgov_snapshot

    snapshot = {"ewma": 99.0}
    tol, reason = loadgov_snapshot.window_tol_min(
        NOW, snapshot=snapshot, alarm_certificate={"alarm": True})
    assert tol == 5.0 and reason == "strict_no_alarm_certificate"
    cert_now = datetime.now(timezone.utc)
    cert = AC.build(
        [_Candidate("c37", 37.0, "NO", "R6_per_order_>35min")],
        decision_order_id="o-window",
        now=cert_now,
    )
    tol, reason = loadgov_snapshot.window_tol_min(
        cert_now, snapshot=snapshot, alarm_certificate=cert)
    assert tol == 10.0 and reason == "loose_alarm_certified"


def test_B_atomic_snapshot_is_scope_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ENABLE_ALARM_CERTIFICATE_SHADOW", True)
    cert = AC.build(
        [_Candidate("c37", 37.0, "NO", "R6_per_order_>35min")],
        decision_order_id="o5",
        now=NOW,
    )
    cert = AC.bind_scope(cert, ["o5", "bag1"])
    path = tmp_path / "alarm.json"
    assert AC.publish(cert, path=str(path)) == "published"
    assert AC.read(NOW, path=str(path), scope_order_ids={"o5", "bag1"}) is not None
    assert AC.read(NOW, path=str(path), scope_order_ids={"other"}) is None
    assert not list(tmp_path.glob("*.tmp"))


# C — sonda S2


def test_C_strategy2_searches_every_courier_and_returns_first_slot():
    calls = []

    def evaluate(slot, fleet):
        calls.append((slot, tuple(fleet)))
        if slot >= NOW + timedelta(minutes=15):
            return ["c2"]
        return []

    got = S2.probe(
        order_id="o6",
        created_at=NOW - timedelta(minutes=30),
        declared_ready_at=NOW,
        now=NOW,
        fleet_ids=["c1", "c2", "c3"],
        evaluate_slot=evaluate,
    )
    assert got["schema"] == "strategy2_probe.v1"
    assert got["found"] is True
    assert got["shift_min"] == 15.0 and got["courier_id"] == "c2"
    assert [x[0] for x in calls] == [
        NOW + timedelta(minutes=5),
        NOW + timedelta(minutes=10),
        NOW + timedelta(minutes=15),
    ]
    assert all(x[1] == ("c1", "c2", "c3") for x in calls)


def test_C_created_plus_90_is_hard_horizon():
    seen = []
    got = S2.probe(
        order_id="o7",
        created_at=NOW - timedelta(minutes=80),
        declared_ready_at=NOW,
        now=NOW,
        fleet_ids=["c1"],
        evaluate_slot=lambda slot, fleet: seen.append(slot) or [],
    )
    assert got["found"] is False
    assert seen == [NOW + timedelta(minutes=5), NOW + timedelta(minutes=10)]
    assert got["deadline_at"] == (NOW + timedelta(minutes=10)).isoformat()


# D — zamknięcie best-effort


def test_D_hard35_filters_breach_before_best_effort_repick():
    safe = _Candidate("safe", 34.0, "NO", "sla_violation")
    breach = _Candidate("breach", 37.0, "NO", "R6_per_order_>35min")
    allowed, alert, meta = AC.hard35_best_effort_choice(
        [breach, safe], alarm_certificate=None)
    assert allowed == [safe]
    assert alert is None
    assert meta["cap_min"] == 35.0


def test_D_no_safe_candidate_stays_visible_as_least_damage_alert():
    c37 = _Candidate("c37", 37.0, "NO", "R6_per_order_>35min")
    c42 = _Candidate("c42", 42.0, "NO", "R6_per_order_>35min")
    allowed, alert, meta = AC.hard35_best_effort_choice(
        [c42, c37], alarm_certificate=None)
    assert allowed == []
    assert alert is c37
    assert meta["reason"] == "no_candidate_within_carry_cap"


def test_D_valid_alarm_allows_35_40_but_never_over_40():
    cert_now = datetime.now(timezone.utc)
    cert = AC.bind_scope(AC.build(
        [_Candidate("c37", 37.0, "NO", "R6_per_order_>35min")],
        decision_order_id="o8", now=cert_now), ["o8"])
    c37 = _Candidate("c37", 37.0, "NO", "R6_per_order_>35min")
    c41 = _Candidate("c41", 41.0, "NO", "R6_per_order_>35min")
    allowed, alert, meta = AC.hard35_best_effort_choice(
        [c41, c37], alarm_certificate=cert)
    assert allowed == [c37] and alert is None
    assert meta["cap_min"] == 40.0 and meta["alarm"] is True


def test_D_selection_source_has_enforcement_before_legacy_always_propose():
    from dispatch_v2.core import selection
    src = inspect.getsource(selection.select_and_emit)
    enforce = src.index("ENABLE_HARD35_ENFORCE")
    legacy = src.index("ENABLE_BEST_EFFORT_R6_KOORD_REDIRECT")
    assert enforce < legacy


# Ratchety integracji, serializery A+B + defaults OFF


def test_ratchet_one_carry_owner_and_one_alarm_snapshot_writer():
    """Blokuje powrót proxy w konsumentach i drugiego writera certyfikatu."""
    from dispatch_v2 import feasibility_v2
    from dispatch_v2 import plan_recheck
    from dispatch_v2 import route_simulator_v2
    from dispatch_v2.core import selection

    assert "carry_freshness" in inspect.getsource(feasibility_v2)
    assert "carry_freshness" in inspect.getsource(plan_recheck)
    assert "carry_freshness" in inspect.getsource(route_simulator_v2)
    assert "hard35_best_effort_choice" in inspect.getsource(selection)
    assert "_alarm.publish" in inspect.getsource(SD._tick)
    assert ".publish(" not in inspect.getsource(DP._assess_order_impl)


def test_C_pipeline_probe_reuses_candidate_evaluator_in_probe_mode():
    src = inspect.getsource(DP._assess_order_impl)
    assert "_candidates.eval_courier" in src
    assert "shadow_probe=True" in src
    assert "list(_s2_fleet_index)" in src


def test_shadow_record_carries_all_three_versioned_metrics_A_and_B():
    carry = {
        "schema": "carry_eval.v1", "status": "EVALUATED",
        "orders": [], "max_carry_min": 34.0,
    }
    best = DP.Candidate("1", None, 1.0, "MAYBE", "ok", None,
                        metrics={"carry_eval": carry})
    alt = DP.Candidate("2", None, 0.0, "NO", "x", None,
                       metrics={"carry_eval": carry})
    result = DP.PipelineResult(
        "o9", "PROPOSE", "ok", best, [best, alt], NOW, None,
    )
    result.carry_eval = carry
    result.alarm_certificate = {"schema": "alarm_certificate.v1", "alarm": False}
    result.strategy2_probe = {"schema": "strategy2_probe.v1", "found": False}
    result.order_created_at = (NOW - timedelta(minutes=5)).isoformat()
    record = SD._serialize_result(result, "e1", 1.0)
    assert record["carry_eval"]["schema"] == "carry_eval.v1"
    assert record["alarm_certificate"]["schema"] == "alarm_certificate.v1"
    assert record["strategy2_probe"]["schema"] == "strategy2_probe.v1"
    assert record["order_created_at"] == result.order_created_at
    assert record["best"]["carry_eval"]["schema"] == "carry_eval.v1"
    assert record["alternatives"][0]["carry_eval"]["schema"] == "carry_eval.v1"


def test_flags_off_keep_new_shadow_fields_out_of_legacy_record(monkeypatch):
    for name in (
        "ENABLE_CARRY_CANON_V2",
        "ENABLE_ALARM_CERTIFICATE_SHADOW",
        "ENABLE_STRATEGY2_PROBE_SHADOW",
        "ENABLE_HARD35_ENFORCE",
    ):
        monkeypatch.setattr(C, name, False)
    best = DP.Candidate("1", None, 1.0, "MAYBE", "ok", None, metrics={})
    result = DP.PipelineResult(
        "off-parity", "PROPOSE", "ok", best, [best], NOW, None,
    )
    record = SD._serialize_result(result, "off", 1.0)
    for key in (
        "carry_eval", "alarm_certificate", "strategy2_probe",
        "hard35_enforcement",
    ):
        assert key not in record


def test_all_four_new_flags_default_off_and_registered():
    import json
    from pathlib import Path

    names = (
        "ENABLE_CARRY_CANON_V2",
        "ENABLE_ALARM_CERTIFICATE_SHADOW",
        "ENABLE_STRATEGY2_PROBE_SHADOW",
        "ENABLE_HARD35_ENFORCE",
    )
    registry = json.loads(
        Path("tools/flag_lifecycle_registry.json").read_text(encoding="utf-8")
    )["flags"]
    for name in names:
        assert getattr(C, name) is False
        assert name in C.ETAP4_DECISION_FLAGS
        assert registry[name]["default"] is False
