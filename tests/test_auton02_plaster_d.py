"""AUTON-02 — testy profilu bramki „plaster D" + nowych twardych bramek G13/G14.

Projekt: eod_drafts/2026-06-30/AUTON02_PLASTER_D_DESIGN.md.
Zasada: profil D (REQUIRE_CLASSIFIER_AUTO=False + REQUIRE_MARGIN=False + pool>=2)
zdejmuje G2/G12, ALE twarde bramki (PROPOSE/czasówka/paczka/informed-pos/
late-pickup/R6/shift-end/parser-degraded/scarcity/sufit-score) ZOSTAJĄ.
"""
from types import SimpleNamespace

from dispatch_v2.auto_assign_gate import evaluate_auto_assign

INFORMED = (
    "gps", "last_assigned_pickup", "last_picked_up_delivery",
    "last_picked_up_recent", "last_delivered", "post_wave", "last_picked_up_pickup",
)

# Profile flagowe
STRICT = {}  # default = strict (oba True, pool 3)
D = {"AUTO_ASSIGN_REQUIRE_CLASSIFIER_AUTO": False,
     "AUTO_ASSIGN_REQUIRE_MARGIN": False,
     "AUTO_ASSIGN_MIN_POOL_FEASIBLE": 2}


def _cand(score=50.0, pos="gps", cid="101", best_effort=False, plan=None):
    return SimpleNamespace(
        courier_id=cid, name="Kurier Testowy", score=score,
        metrics={"pos_source": pos}, best_effort=best_effort, plan=plan,
    )


def _result(auto_route="AUTO", margin=22.0, pool=4, tier="std+", pos="gps",
            shift_end=False, parser_degraded=False, best=None, **extra):
    ctx = {
        "auto_route_pool_feasible": pool,
        "auto_route_pool_total": pool + 3,
        "auto_route_score_margin": margin,
        "auto_route_tier_best": tier,
        "auto_route_pos_source_best": pos,
        "auto_route_czasowka": False,
        "auto_route_best_effort": False,
        "auto_route_best_is_score_top": True,
        "auto_route_shift_end_edge": shift_end,
        "auto_route_parser_degraded": parser_degraded,
    }
    r = SimpleNamespace(
        verdict="PROPOSE",
        best=best or _cand(pos=pos),
        auto_route=auto_route,
        auto_route_reason=f"x|margin={margin}|tier={tier}",
        auto_route_context=ctx,
        pool_feasible_count=pool,
        candidates=[],
        pickup_extension_redirect=None,
        best_effort_r6_redirect=None,
        commit_divergence_redirect=None,
    )
    for k, v in extra.items():
        setattr(r, k, v)
    return r


def _ev(**kw):
    base = {"prep_minutes": 20, "address_id": 300}
    base.update(kw)
    return base


# ── CORE: profil D zdejmuje G2 (classifier=AUTO), strict blokuje ──
def test_d_drops_classifier_gate_strict_blocks():
    r = _result(auto_route="ACK", margin=22.0, pool=3)  # nie AUTO
    w_strict, b_strict = evaluate_auto_assign(r, _ev(), INFORMED, flags=STRICT)
    w_d, b_d = evaluate_auto_assign(r, _ev(), INFORMED, flags=D)
    assert w_strict is False
    assert any("classifier_not_auto" in x for x in b_strict)
    assert w_d is True, f"D powinno przejść, bloki={b_d}"
    assert not any("classifier_not_auto" in x for x in b_d)


# ── profil D zdejmuje G12 (margin) ──
def test_d_drops_margin_gate():
    r = _result(auto_route="AUTO", margin=5.0, pool=3)  # margin<15
    w_strict, b_strict = evaluate_auto_assign(r, _ev(), INFORMED, flags=STRICT)
    w_d, b_d = evaluate_auto_assign(r, _ev(), INFORMED, flags=D)
    assert any("margin" in x for x in b_strict)
    assert not any("margin" in x for x in b_d), f"D nie powinno blokować marginu: {b_d}"
    assert w_d is True


# ── ON≠OFF: ta sama decyzja, różny werdykt per profil ──
def test_profile_on_neq_off():
    r = _result(auto_route="ACK", margin=5.0, pool=2)
    w_strict, _ = evaluate_auto_assign(r, _ev(), INFORMED, flags=STRICT)
    w_d, _ = evaluate_auto_assign(r, _ev(), INFORMED, flags=D)
    assert w_strict != w_d
    assert w_strict is False and w_d is True


# ── G13 shift_end_edge: ZAWSZE blokuje (oba profile) ──
def test_g13_shift_end_blocks_both_profiles():
    r = _result(auto_route="AUTO", margin=22.0, pool=4, shift_end=True)
    for flags in (STRICT, D):
        w, b = evaluate_auto_assign(r, _ev(), INFORMED, flags=flags)
        assert w is False
        assert "shift_end_edge" in b
    # bez shift_end → przechodzi w D
    r2 = _result(auto_route="AUTO", shift_end=False)
    w2, b2 = evaluate_auto_assign(r2, _ev(), INFORMED, flags=D)
    assert "shift_end_edge" not in b2


# ── G14 parser_degraded: ZAWSZE blokuje (flaga lub ctx) ──
def test_g14_parser_degraded_blocks():
    r = _result(auto_route="AUTO")
    w_flag, b_flag = evaluate_auto_assign(r, _ev(), INFORMED, flags={"PARSER_DEGRADED": True, **D})
    assert w_flag is False and "parser_degraded" in b_flag
    r_ctx = _result(auto_route="AUTO", parser_degraded=True)
    w_ctx, b_ctx = evaluate_auto_assign(r_ctx, _ev(), INFORMED, flags=D)
    assert w_ctx is False and "parser_degraded" in b_ctx


# ── twarde bramki ZOSTAJĄ w D ──
def test_d_keeps_hard_gates():
    # scarcity pool<2
    r = _result(auto_route="ACK", pool=1)
    w, b = evaluate_auto_assign(r, _ev(), INFORMED, flags=D)
    assert w is False and any("scarcity_pool" in x for x in b)
    # paczka address_id
    r2 = _result(auto_route="ACK", pool=4)
    w2, b2 = evaluate_auto_assign(r2, _ev(address_id=161), INFORMED, flags=D)
    assert w2 is False and "paczka_firmowe" in b2
    # czasówka prep>=60
    r3 = _result(auto_route="ACK", pool=4)
    w3, b3 = evaluate_auto_assign(r3, _ev(prep_minutes=70), INFORMED, flags=D)
    assert w3 is False and "czasowka" in b3
    # pos nie-informed (blind)
    r4 = _result(auto_route="ACK", pool=4, pos="no_gps", best=_cand(pos="no_gps"))
    w4, b4 = evaluate_auto_assign(r4, _ev(), INFORMED, flags=D)
    assert w4 is False and any("pos_not_informed" in x for x in b4)
    # late-pickup redirect
    r5 = _result(auto_route="ACK", pool=4, pickup_extension_redirect={"x": 1})
    w5, b5 = evaluate_auto_assign(r5, _ev(), INFORMED, flags=D)
    assert w5 is False and "late_pickup_redirect" in b5
    # sufit score G11 zostaje (score>90)
    r6 = _result(auto_route="ACK", pool=4, best=_cand(score=120.0))
    w6, b6 = evaluate_auto_assign(r6, _ev(), INFORMED, flags=D)
    assert w6 is False and any("score_distrust_ceiling" in x for x in b6)


# ── happy path D: czysta decyzja przechodzi ──
def test_d_happy_path():
    r = _result(auto_route="ACK", margin=3.0, pool=3, pos="last_picked_up_pickup",
                best=_cand(score=55.0, pos="last_picked_up_pickup"))
    w, b = evaluate_auto_assign(r, _ev(), INFORMED, flags=D)
    assert w is True, f"bloki={b}"
