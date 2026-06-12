"""AUTON-01 — testy bramki auto-assign (czysta funkcja, wzorzec E5).

Projekt: eod_drafts/2026-06-13/AUTON01_DESIGN.md sekcja 3 (tabela G1-G11).
"""
from types import SimpleNamespace

import pytest

from dispatch_v2 import common as C
from dispatch_v2.auto_assign_gate import evaluate_auto_assign

INFORMED = (
    "gps", "last_assigned_pickup", "last_picked_up_delivery",
    "last_picked_up_recent", "last_delivered", "post_wave",
)


def _candidate(score=50.0, metrics=None, best_effort=False, plan=None, cid="101"):
    return SimpleNamespace(
        courier_id=cid,
        name="Kurier Testowy",
        score=score,
        metrics=dict(metrics or {"pos_source": "gps"}),
        best_effort=best_effort,
        plan=plan,
    )


def _result(
    verdict="PROPOSE",
    best=...,
    auto_route="AUTO",
    auto_route_reason="high_conf_T1|margin=22.0|tier=std+",
    ctx=...,
    **extra,
):
    if best is ...:
        best = _candidate()
    if ctx is ...:
        ctx = {
            "auto_route_pool_feasible": 4,
            "auto_route_pool_total": 9,
            "auto_route_score_margin": 22.0,
            "auto_route_tier_best": "std+",
            "auto_route_pos_source_best": "gps",
            "auto_route_czasowka": False,
            "auto_route_best_effort": False,
            "auto_route_best_is_score_top": True,
        }
    r = SimpleNamespace(
        verdict=verdict,
        best=best,
        auto_route=auto_route,
        auto_route_reason=auto_route_reason,
        auto_route_context=ctx,
        pool_feasible_count=(ctx or {}).get("auto_route_pool_feasible", 0),
    )
    for k, v in extra.items():
        setattr(r, k, v)
    return r


def _ev(**kw):
    base = {"prep_minutes": 20, "address_id": 300}
    base.update(kw)
    return base


# ---------------- happy path ----------------

def test_happy_path_passes():
    would, blocks = evaluate_auto_assign(_result(), _ev(), INFORMED)
    assert would is True
    assert blocks == []


def test_happy_path_with_bag_pos_source():
    best = _candidate(metrics={"pos_source": "last_delivered"})
    would, blocks = evaluate_auto_assign(_result(best=best), _ev(), INFORMED)
    assert would is True


# ---------------- G1 verdict ----------------

def test_koord_never_auto():
    would, blocks = evaluate_auto_assign(_result(verdict="KOORD"), _ev(), INFORMED)
    assert would is False
    assert any(b.startswith("verdict_not_propose:KOORD") for b in blocks)


def test_skip_never_auto():
    would, blocks = evaluate_auto_assign(_result(verdict="SKIP"), _ev(), INFORMED)
    assert would is False


def test_no_best_short_circuits():
    would, blocks = evaluate_auto_assign(_result(best=None), _ev(), INFORMED)
    assert would is False
    assert "no_best" in blocks


# ---------------- G2 klasyfikator ----------------

def test_classifier_ack_blocks():
    would, blocks = evaluate_auto_assign(
        _result(auto_route="ACK", auto_route_reason="C2_score_margin=3.1<15"),
        _ev(), INFORMED)
    assert would is False
    assert any(b.startswith("classifier_not_auto:ACK:C2_score_margin") for b in blocks)


def test_classifier_alert_blocks():
    would, blocks = evaluate_auto_assign(
        _result(auto_route="ALERT", auto_route_reason="best_effort_no_feasible"),
        _ev(), INFORMED)
    assert would is False
    assert any(b.startswith("classifier_not_auto:ALERT") for b in blocks)


# ---------------- G3 fail-closed bez kontekstu ----------------

def test_missing_context_fail_closed():
    would, blocks = evaluate_auto_assign(_result(ctx={}), _ev(), INFORMED)
    assert would is False
    assert "no_auto_route_context" in blocks


def test_none_context_fail_closed():
    would, blocks = evaluate_auto_assign(_result(ctx=None), _ev(), INFORMED)
    assert would is False
    assert "no_auto_route_context" in blocks


# ---------------- G4 czasówka ----------------

def test_czasowka_prep_minutes_blocks():
    would, blocks = evaluate_auto_assign(_result(), _ev(prep_minutes=90), INFORMED)
    assert would is False
    assert "czasowka" in blocks


def test_czasowka_czas_odbioru_field_blocks():
    ev = {"czas_odbioru": 120, "address_id": 300}
    would, blocks = evaluate_auto_assign(_result(), ev, INFORMED)
    assert "czasowka" in blocks


def test_czasowka_from_context_blocks():
    ctx = {
        "auto_route_pool_feasible": 4,
        "auto_route_tier_best": "std+",
        "auto_route_pos_source_best": "gps",
        "auto_route_czasowka": True,
    }
    would, blocks = evaluate_auto_assign(_result(ctx=ctx), _ev(), INFORMED)
    assert "czasowka" in blocks


def test_prep_59_is_not_czasowka():
    would, blocks = evaluate_auto_assign(_result(), _ev(prep_minutes=59), INFORMED)
    assert "czasowka" not in blocks


def test_garbage_prep_not_czasowka():
    would, blocks = evaluate_auto_assign(_result(), _ev(prep_minutes="abc"), INFORMED)
    assert "czasowka" not in blocks


# ---------------- G5 paczki / firmowe ----------------

def test_firmowe_161_blocks():
    would, blocks = evaluate_auto_assign(_result(), _ev(address_id=161), INFORMED)
    assert would is False
    assert "paczka_firmowe" in blocks


@pytest.mark.parametrize("aid", sorted(C.PACZKA_ADDRESS_IDS))
def test_paczka_accounts_block(aid):
    would, blocks = evaluate_auto_assign(_result(), _ev(address_id=aid), INFORMED)
    assert "paczka_firmowe" in blocks


def test_paczka_string_address_id_blocks():
    would, blocks = evaluate_auto_assign(_result(), _ev(address_id="161"), INFORMED)
    assert "paczka_firmowe" in blocks


def test_normal_restaurant_not_blocked_as_paczka():
    would, blocks = evaluate_auto_assign(_result(), _ev(address_id=300), INFORMED)
    assert "paczka_firmowe" not in blocks


def test_garbage_address_id_not_blocked():
    would, blocks = evaluate_auto_assign(_result(), _ev(address_id="xyz"), INFORMED)
    assert "paczka_firmowe" not in blocks


# ---------------- G6 rampa nowych ----------------

def test_new_courier_ramp_blocks():
    ctx = {
        "auto_route_pool_feasible": 4,
        "auto_route_tier_best": "new",
        "auto_route_pos_source_best": "gps",
        "auto_route_czasowka": False,
    }
    would, blocks = evaluate_auto_assign(_result(ctx=ctx), _ev(), INFORMED)
    assert would is False
    assert "new_courier_ramp" in blocks


# ---------------- G7 pozycja ----------------

@pytest.mark.parametrize("src", ["no_gps", "pre_shift", "none", None])
def test_blind_pos_sources_block(src):
    best = _candidate(metrics={"pos_source": src})
    ctx = {
        "auto_route_pool_feasible": 4,
        "auto_route_tier_best": "std+",
        "auto_route_pos_source_best": src,
        "auto_route_czasowka": False,
    }
    would, blocks = evaluate_auto_assign(_result(best=best, ctx=ctx), _ev(), INFORMED)
    assert would is False
    assert any(b.startswith("pos_not_informed:") for b in blocks)


def test_pos_from_store_blocks_even_with_gps_label():
    best = _candidate(metrics={"pos_source": "gps", "pos_from_store": True})
    would, blocks = evaluate_auto_assign(_result(best=best), _ev(), INFORMED)
    assert would is False
    assert "pos_from_store" in blocks


# ---------------- G8 late-pickup ----------------

def test_pickup_extension_redirect_blocks():
    would, blocks = evaluate_auto_assign(
        _result(pickup_extension_redirect={"suggested_pickup": "18:30"}),
        _ev(), INFORMED)
    assert would is False
    assert "late_pickup_redirect" in blocks


def test_late_pickup_committed_breach_blocks():
    best = _candidate(metrics={"pos_source": "gps", "late_pickup_committed_breach": True})
    would, blocks = evaluate_auto_assign(_result(best=best), _ev(), INFORMED)
    assert "late_pickup_committed" in blocks


def test_new_pickup_needs_extension_blocks():
    best = _candidate(metrics={"pos_source": "gps", "new_pickup_needs_extension": True})
    would, blocks = evaluate_auto_assign(_result(best=best), _ev(), INFORMED)
    assert "late_pickup_extension" in blocks


# ---------------- G9 R6 / commit-divergence ----------------

def test_r6_redirect_blocks():
    would, blocks = evaluate_auto_assign(
        _result(best_effort_r6_redirect={"reason": "r6"}), _ev(), INFORMED)
    assert "r6_redirect" in blocks


def test_commit_divergence_blocks():
    would, blocks = evaluate_auto_assign(
        _result(commit_divergence_redirect={"divergence_min": 12.0}), _ev(), INFORMED)
    assert "commit_divergence" in blocks


def test_best_effort_candidate_blocks():
    best = _candidate(best_effort=True)
    would, blocks = evaluate_auto_assign(_result(best=best), _ev(), INFORMED)
    assert "best_effort" in blocks


def test_plan_sla_violations_block():
    plan = SimpleNamespace(sla_violations=2)
    best = _candidate(plan=plan)
    would, blocks = evaluate_auto_assign(_result(best=best), _ev(), INFORMED)
    assert "plan_sla_violations" in blocks


def test_clean_plan_passes():
    plan = SimpleNamespace(sla_violations=0)
    best = _candidate(plan=plan)
    would, blocks = evaluate_auto_assign(_result(best=best), _ev(), INFORMED)
    assert would is True


# ---------------- G10 scarcity ----------------

def test_scarcity_pool_2_blocks():
    ctx = {
        "auto_route_pool_feasible": 2,
        "auto_route_tier_best": "std+",
        "auto_route_pos_source_best": "gps",
        "auto_route_czasowka": False,
    }
    would, blocks = evaluate_auto_assign(_result(ctx=ctx), _ev(), INFORMED)
    assert would is False
    assert "scarcity_pool:2" in blocks


def test_pool_at_min_passes(monkeypatch):
    monkeypatch.setattr(C, "AUTO_ASSIGN_MIN_POOL_FEASIBLE", 3)
    ctx = {
        "auto_route_pool_feasible": 3,
        "auto_route_tier_best": "std+",
        "auto_route_pos_source_best": "gps",
        "auto_route_czasowka": False,
    }
    would, blocks = evaluate_auto_assign(_result(ctx=ctx), _ev(), INFORMED)
    assert not any(b.startswith("scarcity_pool") for b in blocks)


def test_scarcity_threshold_flags_override():
    # próg hot z flags-dict ma pierwszeństwo nad stałą modułu
    would, blocks = evaluate_auto_assign(
        _result(), _ev(), INFORMED,
        flags={"AUTO_ASSIGN_MIN_POOL_FEASIBLE": 5})
    assert any(b.startswith("scarcity_pool:4") for b in blocks)


# ---------------- G11 sufit nieufności (Bartek 2.0 §4.1) ----------------

def test_score_above_ceiling_blocks():
    best = _candidate(score=120.0)
    would, blocks = evaluate_auto_assign(_result(best=best), _ev(), INFORMED)
    assert would is False
    assert any(b.startswith("score_distrust_ceiling:120.0") for b in blocks)


def test_score_at_ceiling_passes():
    best = _candidate(score=90.0)
    would, blocks = evaluate_auto_assign(_result(best=best), _ev(), INFORMED)
    assert not any(b.startswith("score_distrust_ceiling") for b in blocks)


def test_ceiling_flags_override():
    best = _candidate(score=80.0)
    would, blocks = evaluate_auto_assign(
        _result(best=best), _ev(), INFORMED,
        flags={"AUTO_ASSIGN_SCORE_DISTRUST_CEILING": 70.0})
    assert any(b.startswith("score_distrust_ceiling:80.0") for b in blocks)


# ---------------- akumulacja powodów (nie first-fail) ----------------

def test_multiple_blocks_accumulate():
    best = _candidate(score=150.0, best_effort=True,
                      metrics={"pos_source": "no_gps"})
    ctx = {
        "auto_route_pool_feasible": 1,
        "auto_route_tier_best": "new",
        "auto_route_pos_source_best": "no_gps",
        "auto_route_czasowka": False,
    }
    would, blocks = evaluate_auto_assign(
        _result(verdict="KOORD", best=best, auto_route="ALERT", ctx=ctx),
        _ev(prep_minutes=90, address_id=161), INFORMED)
    assert would is False
    expected_prefixes = [
        "verdict_not_propose", "classifier_not_auto", "czasowka",
        "paczka_firmowe", "new_courier_ramp", "pos_not_informed",
        "best_effort", "scarcity_pool", "score_distrust_ceiling",
    ]
    for pref in expected_prefixes:
        assert any(b.startswith(pref) for b in blocks), f"brak {pref} w {blocks}"


# ---------------- definicje kanonu ETAP4 ----------------

def test_flag_in_etap4_canon():
    assert "ENABLE_AUTO_ASSIGN" in C.ETAP4_DECISION_FLAGS


def test_numeric_overrides_registered():
    for k in ("AUTO_ASSIGN_MIN_POOL_FEASIBLE", "AUTO_ASSIGN_SCORE_DISTRUST_CEILING",
              "AUTO_ASSIGN_MAX_PER_HOUR", "AUTO_ASSIGN_OVERRIDE_COOLDOWN_MIN"):
        assert k in C.FLAGS_JSON_NUMERIC_OVERRIDES
        assert hasattr(C, k)


def test_module_default_is_off():
    assert C.ENABLE_AUTO_ASSIGN is False


def test_decision_flag_default_off():
    # conftest izoluje flags.json (klucz wycięty) → fallback = stała modułu OFF
    assert C.decision_flag("ENABLE_AUTO_ASSIGN") is False
