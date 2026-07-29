"""RED-first oracle: drabina S1 -> S2 -> S3, noc 2026-07-28.

Każdy test jest niezależnym oraclem kontraktu, nie autowalidacją kształtu:
carry liczymy z literalnych timestampów, Alarm z kontrfaktycznej puli, a S2
z jawnej macierzy slot × flota.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
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


def test_F6_untrusted_physical_source_stays_proxy():
    plan = _plan(food=NOW + timedelta(minutes=20))
    got = CF.evaluate_plan(
        plan,
        [_order(
            "food",
            physical=NOW,
            physical_source="panel.picked_up_at",
        )],
    )
    item = got["orders"][0]
    assert item["carry_min"] == 20.0
    assert item["source"] == "proxy"
    assert item["possession_source"] == "panel.picked_up_at"


def test_F7_carry_cap_uses_raw_value_before_display_rounding():
    raw_minutes = 35.004
    plan = _plan(food=NOW + timedelta(minutes=raw_minutes))
    got = CF.evaluate_plan(
        plan,
        [_order(
            "food",
            physical=NOW,
            physical_source="gps_bag_sensor",
        )],
    )
    item = got["orders"][0]
    assert item["carry_min"] == 35.0
    assert item["le_35"] is False
    assert got["max_carry_min"] == raw_minutes
    assert got["all_le_35"] is False


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


def _s2_result(order_id: str, *, found: bool) -> dict:
    return {
        "schema": "strategy2_probe.v1",
        "status": "EVALUATED",
        "order_id": order_id,
        "found": found,
        "slot_at": (
            (NOW + timedelta(minutes=10)).isoformat() if found else None
        ),
        "shift_min": 10.0 if found else None,
        "courier_id": "s2-safe" if found else None,
        "feasible_courier_ids": ["s2-safe"] if found else [],
        "fleet_count": 3,
        "slots_checked": 2,
        "deadline_at": (NOW + timedelta(minutes=30)).isoformat(),
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
    pool = [
        _Candidate("c37", 37.0, "NO", "R6_per_order_>35min"),
        _Candidate("c44", 44.0, "NO", "R6_per_order_>35min"),
    ]
    s2 = _s2_result("o2", found=False)
    cert = AC.build(
        pool,
        decision_order_id="o2",
        now=NOW,
        strategy2_probe=s2,
    )
    assert cert["classification"] == "ALARM_CANDIDATE"
    assert cert["alarm"] is True
    assert cert["counterfactual"]["le_35_count"] == 0
    assert cert["counterfactual"]["between_35_40_count"] == 1
    assert AC.validate(
        cert, NOW, decision_order_id="o2",
        candidates=pool, strategy2_probe=s2,
    )


def test_F1_valid_alarm_never_opens_when_feature_flag_is_off(monkeypatch):
    pool = [_Candidate("c37", 37.0, "NO", "R6_per_order_>35min")]
    s2 = _s2_result("f1", found=False)
    cert = AC.build(
        pool, decision_order_id="f1", now=NOW, strategy2_probe=s2)
    monkeypatch.setattr(
        C, "decision_flag",
        lambda name: False if name == "ENABLE_ALARM_CERTIFICATE_SHADOW" else False,
    )

    assert AC.is_alarm(
        cert, NOW, candidates=pool, strategy2_probe=s2) is False


def test_F3_strategy2_safe_plan_invalidates_alarm_counterfactual():
    pool = [_Candidate("c37", 37.0, "NO", "R6_per_order_>35min")]
    s2 = _s2_result("f3", found=True)
    cert = AC.build(
        pool, decision_order_id="f3", now=NOW, strategy2_probe=s2)

    assert cert["alarm"] is False
    assert cert["classification"] == "NORMAL_STRATEGY2"
    assert cert["strategy2_probe_fingerprint"]
    assert AC.validate(
        cert, NOW, candidates=pool, strategy2_probe=s2)


def test_F4_validate_recomputes_counterfactual_from_real_pool():
    pool = [_Candidate("c37", 37.0, "NO", "R6_per_order_>35min")]
    s2 = _s2_result("f4", found=False)
    cert = AC.build(
        pool, decision_order_id="f4", now=NOW, strategy2_probe=s2)
    forged = {
        **cert,
        "counterfactual": {
            **cert["counterfactual"],
            "between_35_40_count": 2,
            "between_35_40_cids": ["c37", "forged"],
        },
    }

    assert AC.validate(
        forged, NOW, candidates=pool, strategy2_probe=s2) is False


def test_B_other_hard_reject_cannot_forge_alarm_candidate():
    cert = AC.build(
        [_Candidate("too_far", 37.0, "NO", "pickup_too_far (99 km)")],
        decision_order_id="o3",
        now=NOW,
        strategy2_probe=_s2_result("o3", found=False),
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


def test_B_forged_dict_cannot_open_loadgov_loose_window(monkeypatch):
    from dispatch_v2.core import loadgov_snapshot

    snapshot = {"ewma": 99.0}
    tol, reason = loadgov_snapshot.window_tol_min(
        NOW, snapshot=snapshot, alarm_certificate={"alarm": True})
    assert tol == 5.0 and reason == "strict_no_alarm_certificate"
    cert_now = datetime.now(timezone.utc)
    pool = [_Candidate("c37", 37.0, "NO", "R6_per_order_>35min")]
    s2 = {
        **_s2_result("o-window", found=False),
        "deadline_at": (cert_now + timedelta(minutes=30)).isoformat(),
    }
    cert = AC.build(
        pool,
        decision_order_id="o-window",
        now=cert_now,
        strategy2_probe=s2,
    )
    original_decision_flag = C.decision_flag
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: (
            True
            if name == "ENABLE_ALARM_CERTIFICATE_SHADOW"
            else original_decision_flag(name)
        ),
    )
    tol, reason = loadgov_snapshot.window_tol_min(
        cert_now,
        snapshot=snapshot,
        alarm_certificate=cert,
        alarm_candidates=pool,
        strategy2_probe=s2,
    )
    assert tol == 10.0 and reason == "loose_alarm_certified"


def test_B_atomic_snapshot_is_scope_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ENABLE_ALARM_CERTIFICATE_SHADOW", True)
    pool = [_Candidate("c37", 37.0, "NO", "R6_per_order_>35min")]
    s2 = _s2_result("o5", found=False)
    cert = AC.build(
        pool,
        decision_order_id="o5",
        now=NOW,
        strategy2_probe=s2,
    )
    cert = AC.bind_scope(cert, ["o5", "bag1"])
    path = tmp_path / "alarm.json"
    assert AC.publish(
        cert, path=str(path), candidates=pool, strategy2_probe=s2,
    ) == "published"
    assert AC.read(
        NOW, path=str(path), scope_order_ids={"o5", "bag1"},
        candidates=pool, strategy2_probe=s2,
    ) is not None
    assert AC.read(
        NOW, path=str(path), scope_order_ids={"other"},
        candidates=pool, strategy2_probe=s2,
    ) is None
    verified = AC.read(
        NOW, path=str(path), scope_order_ids={"o5", "bag1"})
    assert verified is not None
    assert AC.is_alarm(verified, NOW) is True
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


def test_F5_strategy2_soon_free_read_is_byte_pure(tmp_path, monkeypatch):
    from dispatch_v2 import plan_manager as PM

    plans_path = tmp_path / "courier_plans.json"
    lock_path = tmp_path / "courier_plans.lock"
    payload = {
        "7": {
            "invalidated_at": None,
            "stops": [{
                "order_id": "stale",
                "type": "dropoff",
                "predicted_at": (NOW + timedelta(minutes=5)).isoformat(),
                "coords": {"lat": 53.13, "lng": 23.16},
            }],
        },
    }
    plans_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    monkeypatch.setattr(PM, "PLANS_FILE", plans_path)
    monkeypatch.setattr(PM, "LOCK_FILE", lock_path)
    cache_sentinel = {
        "key": ("preexisting",),
        "data": {"preexisting": {"stops": []}},
    }
    monkeypatch.setattr(PM, "_perf_plans_cache", copy.deepcopy(cache_sentinel))
    before = plans_path.read_bytes()
    cache_before = copy.deepcopy(PM._perf_plans_cache)

    assert DP._soon_free_probe(
        "7", [{"order_id": "current"}], NOW, pure_read=True) is None
    assert plans_path.read_bytes() == before
    assert PM._perf_plans_cache == cache_before


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


def test_D_valid_alarm_allows_35_40_but_never_over_40(monkeypatch):
    cert_now = datetime.now(timezone.utc)
    pool = [_Candidate("c37", 37.0, "NO", "R6_per_order_>35min")]
    s2 = {
        **_s2_result("o8", found=False),
        "deadline_at": (cert_now + timedelta(minutes=30)).isoformat(),
    }
    cert = AC.bind_scope(AC.build(
        pool, decision_order_id="o8", now=cert_now,
        strategy2_probe=s2), ["o8"])
    c37 = _Candidate("c37", 37.0, "NO", "R6_per_order_>35min")
    c41 = _Candidate("c41", 41.0, "NO", "R6_per_order_>35min")
    original_decision_flag = C.decision_flag
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: (
            True
            if name == "ENABLE_ALARM_CERTIFICATE_SHADOW"
            else original_decision_flag(name)
        ),
    )
    allowed, alert, meta = AC.hard35_best_effort_choice(
        [c41, c37], alarm_certificate=cert,
        validation_candidates=pool, strategy2_probe=s2)
    assert allowed == [c37] and alert is None
    assert meta["cap_min"] == 40.0 and meta["alarm"] is True


def _hard35_selection_case(monkeypatch):
    from dispatch_v2.core import selection

    plan = SimpleNamespace(
        sequence=["hard35"], sla_violations=0,
        predicted_delivered_at={}, pickup_at={},
        total_duration_min=20.0, strategy="oracle",
    )
    carry = {
        "schema": "carry_eval.v1", "status": "EVALUATED",
        "orders": [], "max_carry_min": 35.01,
        "all_le_35": False, "all_le_40": True,
    }
    candidate = DP.Candidate(
        "c36", "C36", 10.0, "MAYBE", "ok", plan,
        {
            "bundle_level3_dev": None,
            "bag_size_before": 0,
            "r6_bag_size": 0,
            "pos_source": "gps",
            "new_pickup_late_min": 0.0,
            "late_pickup_committed_max": 0.0,
            "carry_eval": carry,
        },
    )
    monkeypatch.setattr(
        C, "decision_flag",
        lambda name: name == "ENABLE_HARD35_ENFORCE",
    )
    monkeypatch.setattr(DP, "_classify_and_set_auto_route", lambda *a, **k: None)
    ctx = selection.SelectionContext(
        now=NOW,
        order_event={"order_id": "hard35"},
        order_id="hard35",
        restaurant="R",
        delivery_address="D",
        pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17),
        pickup_ready_at=NOW,
        new_order=SimpleNamespace(order_id="hard35"),
        fleet_snapshot={},
        v328_fail_causes={},
        shadow_only=True,
    )
    return selection.select_and_emit(ctx, [candidate])


def test_F2_maybe_plan_over_35_hits_hard35_before_feasible_propose(monkeypatch):
    result = _hard35_selection_case(monkeypatch)
    assert result.verdict == "KOORD"
    assert result.reason.startswith("hard35_least_damage_alert")
    assert result.best.courier_id == "c36"


def test_F8_mutation_disabling_hard35_hook_is_killed(monkeypatch):
    real = _hard35_selection_case(monkeypatch)
    assert real.verdict == "KOORD"

    monkeypatch.setattr(
        AC,
        "hard35_best_effort_choice",
        lambda candidates, **kwargs: (
            list(candidates), None,
            {
                "schema": "hard35_enforcement.v1",
                "cap_min": 35.0,
                "alarm": False,
                "pool_count": len(list(candidates)),
                "within_cap_count": len(list(candidates)),
                "unknown_count": 0,
                "reason": "mutant_hook_disabled",
            },
        ),
    )
    mutant = _hard35_selection_case(monkeypatch)
    assert mutant.verdict == "PROPOSE"


def test_D_selection_has_single_final_hard35_proposal_boundary():
    from dispatch_v2.core import selection
    src = inspect.getsource(selection.select_and_emit)
    assert src.count("_hard35_proposal_boundary(") == 4


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
    assert "pure_read=shadow_probe" in inspect.getsource(
        __import__(
            "dispatch_v2.core.candidates",
            fromlist=["eval_courier_inner"],
        ).eval_courier_inner
    )
    assert "list(_s2_fleet_index)" in src


def test_F3_pipeline_builds_alarm_only_after_strategy2_result():
    src = inspect.getsource(DP._assess_order_impl)
    s2_result = src.index("_strategy2_probe = _s2.probe(")
    alarm_build = src.index("_alarm.build(", s2_result)
    assert s2_result < alarm_build
    assert "strategy2_probe=_strategy2_probe" in src[
        alarm_build:alarm_build + 320
    ]


def test_shadow_record_carries_all_three_versioned_metrics_A_and_B(
    monkeypatch,
):
    for name in (
        "ENABLE_CARRY_CANON_V2",
        "ENABLE_ALARM_CERTIFICATE_SHADOW",
        "ENABLE_STRATEGY2_PROBE_SHADOW",
    ):
        monkeypatch.setattr(C, name, True)
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


def test_F9_flags_off_serializer_is_byte_identical_to_frozen_baseline(monkeypatch):
    monkeypatch.setattr(SD, "now_iso", lambda: NOW.isoformat())
    monkeypatch.setattr(C, "ENABLE_R04_SHADOW", False)
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
    # Defense-in-depth: OFF parity must not depend only on well-behaved
    # producers.  A stale/replayed/future caller may hand the serializer a
    # populated artifact even though its owning flag is OFF.
    result.carry_eval = {
        "schema": "carry_eval.v1",
        "status": "EVALUATED",
        "max_carry_min": 34.0,
    }
    result.alarm_certificate = {
        "schema": "alarm_certificate.v1",
        "alarm": False,
    }
    result.strategy2_probe = {
        "schema": "strategy2_probe.v1",
        "status": "EVALUATED",
        "found": False,
    }
    result.order_created_at = NOW.isoformat()
    result.hard35_enforcement = {
        "schema": "hard35_enforcement.v1",
        "cap_min": 35.0,
    }
    best.metrics.update({
        "carry_eval": result.carry_eval,
        "hard35_enforcement": result.hard35_enforcement,
    })
    record = SD._serialize_result(result, "off", 1.0)
    leaked = {
        "carry_eval",
        "alarm_certificate",
        "strategy2_probe",
        "order_created_at",
        "hard35_enforcement",
    }.intersection(record)
    assert not leaked, f"feature-owned serializer fields leaked while OFF: {leaked}"
    nested_leaked = {
        "carry_eval",
        "hard35_enforcement",
    }.intersection(record["best"])
    assert not nested_leaked, (
        f"feature-owned candidate metrics leaked while OFF: {nested_leaked}"
    )
    # OFF-parity dowodzony ODPORNIE na środowisko: zbiór kluczy rekordu = zamrożony
    # baseline (klucze nie zależą od ortools/venv, w przeciwieństwie do hash wartości —
    # dawny hardcoded hash był policzony w okrojonym sandboxie i pękał w kanonicznym venv).
    # Razem z asercjami leaked/nested_leaked wyżej: przy OFF serializer NIE dodaje ŻADNEGO
    # nowego klucza względem bazy sprzed fundamentu eskalacji.
    FROZEN_OFF_KEYSET = {
        "alternatives", "auto_block_reasons", "auto_block_reasons_d",
        "auto_block_reasons_dprime", "auto_route", "auto_route_context",
        "auto_route_reason", "best", "best_effort_r6_redirect",
        "commit_divergence_redirect", "decision_meta", "delivery_address",
        "difficult_case_redirect", "effective_ready_shadow",
        "eta_cell_corrected_min", "eta_cell_correction_flag", "eta_defer_hint",
        "eta_unreliable", "eta_unreliable_meta", "event_id",
        "late_pickup_shadow", "latency_ms", "loadaware_shadow",
        "min_delivered_at_shadow", "mode", "mode_reason", "order_id",
        "pickup_extension_redirect", "pickup_ready_at", "pool_feasible_count",
        "pool_total_count", "prep_bias_min", "prep_variance_anomaly",
        "proposal_claims_count", "proposal_claims_relaxed", "r6_danger_shadow",
        "reason", "reserve_tiebreak_shadow", "restaurant", "rule_verdict",
        "ts", "v328_fail_causes", "verdict", "would_auto_assign",
        "would_auto_assign_d", "would_auto_assign_dprime",
    }
    assert set(record.keys()) == FROZEN_OFF_KEYSET, (
        "OFF keyset != baseline; nowe/utracone klucze: "
        f"{set(record.keys()) ^ FROZEN_OFF_KEYSET}")


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
