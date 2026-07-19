"""NOGPS-NEUTRAL-SCORE (2026-07-19) — flaga ENABLE_NO_GPS_NEUTRAL_SCORE_DIST.

BUG (memory ziomek-nogps-center-score-bug-2026-07-19): kurier bez GPS planowany
w BIALYSTOK_CENTER; ta fikcja zasilała SCORE (s_dystans=100·exp(-km/5) z centrum
≈ sufit), a F1.7 neutralizował tylko DISPLAY po zamrożeniu score → no-GPS 24.8%
puli / 50.5% zwycięzców (regresja ENABLE_NO_GPS_EQUAL_TREATMENT: demote zdjęty,
ukryty bonus centrum został).

FIX: dispatch_pipeline._nogps_neutral_score_pass — dla kandydatów z road_km
z pozycji-fikcji (metrics.road_km_from_synthetic_pos z core.candidates) i
pos_source ∈ POSITION_UNKNOWN_SOURCES: neutralny dystans = MEDIANA road_km
kandydatów o realnej kotwicy; shadow (bonus_nogps_neutral_*) ZAWSZE, apply do
score + display za flagą (default OFF → bajt-parytet).

Testy: funkcjonalne ON≠OFF na pass + kontrakt flagi + source-regression wpięć
(wzorzec test_always_propose_on_saturation — bramki głęboko w assess_order).
"""
import inspect
import math
from datetime import datetime, timezone
from types import SimpleNamespace

from dispatch_v2 import common
from dispatch_v2 import dispatch_pipeline as dp
from dispatch_v2.core import candidates as k11c
from dispatch_v2.core import selection as sel
from dispatch_v2.courier_resolver import POSITION_UNKNOWN_SOURCES
from dispatch_v2.scoring import W_DYSTANS, s_dystans


class FakeCand:
    def __init__(self, cid, km, pos_source, synth, sd=None, total=80.0,
                 verdict="MAYBE"):
        self.courier_id = cid
        self.feasibility_verdict = verdict
        self.feasibility_reason = ""
        self.plan = None
        self.score = total
        self.metrics = {
            "km_to_pickup": km,
            "pos_source": pos_source,
            "road_km_from_synthetic_pos": synth,
            "score": {
                "total": total,
                "components": {
                    "dystans": (sd if sd is not None
                                else (round(s_dystans(km), 2)
                                      if isinstance(km, (int, float)) else 0.0)),
                    "obciazenie": 100.0, "kierunek": 100.0, "czas": 100.0,
                },
            },
        }


def _flag(monkeypatch, on: bool):
    monkeypatch.setattr(
        dp.C, "decision_flag",
        lambda f: on if f == "ENABLE_NO_GPS_NEUTRAL_SCORE_DIST" else False)


def _pool_center_boost():
    """3 realne kotwice (2/4/9 km → mediana 4.0) + no-GPS z centrum 1.2 km,
    który na fikcji ma NAJWYŻSZY score (near-ceiling jak cid=179/413 landslide
    112 vs 4.1 z selection.py) — ale przewaga pochodzi tylko z centrum."""
    sd_c = round(s_dystans(1.2), 2)
    nogps = FakeCand("179", 1.2, "no_gps", True, sd=sd_c,
                     total=round(sd_c * W_DYSTANS + 100 * 0.7, 2))  # ~93.6
    gps_close = FakeCand("400", 2.0, "gps", False,
                         total=round(s_dystans(2.0) * W_DYSTANS + 100 * 0.7, 2))  # ~90.1
    gps_mid = FakeCand("500", 4.0, "gps", False, total=80.0)
    gps_far = FakeCand("509", 9.0, "gps", False, total=60.0)
    return nogps, gps_close, [nogps, gps_close, gps_mid, gps_far]


# ── kontrakt flagi ──────────────────────────────────────────────────────────

def test_flag_contract_default_off_and_registered():
    assert hasattr(common, "ENABLE_NO_GPS_NEUTRAL_SCORE_DIST")
    assert common.ENABLE_NO_GPS_NEUTRAL_SCORE_DIST is False
    assert "ENABLE_NO_GPS_NEUTRAL_SCORE_DIST" in common.ETAP4_DECISION_FLAGS


def test_shadow_keys_not_excluded_from_serializer():
    """bonus_* → auto-serializacja L1.1 (deny-lista); klucze nie mogą być wykluczone."""
    from dispatch_v2 import shadow_dispatcher as sd
    for k in ("bonus_nogps_neutral_km", "bonus_nogps_neutral_raw_km",
              "bonus_nogps_neutral_dist_delta", "bonus_nogps_neutral_applied"):
        assert k not in sd._METRICS_EXCLUDE
        assert k.startswith("bonus_")


# ── OFF: bajt-parytet decyzji + shadow compute-always ───────────────────────

def test_off_score_untouched_shadow_always(monkeypatch):
    _flag(monkeypatch, False)
    nogps, gps_close, pool = _pool_center_boost()
    before = [c.score for c in pool]
    km, applied = dp._nogps_neutral_score_pass(pool, order_id="T")
    assert applied == 0
    assert [c.score for c in pool] == before                      # decyzja nietknięta
    assert nogps.metrics["score"]["total"] == before[0]           # metrics score nietknięty
    m = nogps.metrics
    assert m["bonus_nogps_neutral_applied"] is False
    assert m["bonus_nogps_neutral_km"] == 4.0                     # mediana known
    assert m["bonus_nogps_neutral_raw_km"] == 1.2
    exp_delta = round(W_DYSTANS * (s_dystans(4.0) - m["score"]["components"]["dystans"]), 2)
    assert m["bonus_nogps_neutral_dist_delta"] == exp_delta       # shadow ZAWSZE
    assert "bonus_nogps_neutral_km" not in gps_close.metrics      # known nietykani


def test_off_winner_stays_nogps(monkeypatch):
    """OFF = dzisiejszy bug zachowany bajt-w-bajt: no-GPS z centrum wygrywa."""
    _flag(monkeypatch, False)
    nogps, _, pool = _pool_center_boost()
    dp._nogps_neutral_score_pass(pool, order_id="T")
    ranked = sorted(pool, key=lambda c: -c.score)
    assert ranked[0] is nogps


# ── ON: apply + winner flip (dowód spadku winner-share) ─────────────────────

def test_on_applies_median_and_flips_winner(monkeypatch):
    _flag(monkeypatch, True)
    nogps, gps_close, pool = _pool_center_boost()
    old_score = nogps.score
    old_sd = nogps.metrics["score"]["components"]["dystans"]
    km, applied = dp._nogps_neutral_score_pass(pool, order_id="T")
    assert km == 4.0 and applied == 1
    exp_delta = round(W_DYSTANS * (s_dystans(4.0) - old_sd), 2)
    assert exp_delta < 0                                          # centrum → mediana = strata
    assert math.isclose(nogps.score, old_score + exp_delta, abs_tol=1e-9)
    assert nogps.metrics["score"]["components"]["dystans"] == round(s_dystans(4.0), 2)
    assert nogps.metrics["score"]["total"] == round(old_score + exp_delta, 2)
    assert nogps.metrics["bonus_nogps_neutral_applied"] is True
    # winner flip: uczciwy score → GPS-bliski wygrywa (mixed-pool win → ~50%)
    ranked = sorted(pool, key=lambda c: -c.score)
    assert ranked[0] is gps_close
    assert ranked[0] is not nogps


def test_on_median_not_mean(monkeypatch):
    """Rozkład prawoskośny: mediana(2,3,20)=3.0, średnia=8.33 — MUSI być mediana."""
    _flag(monkeypatch, True)
    pool = [FakeCand("a", 2.0, "gps", False), FakeCand("b", 3.0, "gps", False),
            FakeCand("c", 20.0, "gps", False), FakeCand("n", 1.0, "no_gps", True)]
    km, _ = dp._nogps_neutral_score_pass(pool, order_id="T")
    assert km == 3.0


def test_on_even_pool_median_interpolates(monkeypatch):
    _flag(monkeypatch, True)
    pool = [FakeCand("a", 2.0, "gps", False), FakeCand("b", 6.0, "gps", False),
            FakeCand("n", 1.0, "no_gps", True)]
    km, _ = dp._nogps_neutral_score_pass(pool, order_id="T")
    assert km == 4.0


def test_on_real_anchor_nogps_untouched(monkeypatch):
    """no_gps z workiem, road_km z anchor/bag-tail (realna kotwica) —
    road_km_from_synthetic_pos=False → score NIE neutralizowany."""
    _flag(monkeypatch, True)
    anchored = FakeCand("508", 2.5, "no_gps", False, total=88.0)
    pool = [anchored, FakeCand("a", 4.0, "gps", False)]
    _, applied = dp._nogps_neutral_score_pass(pool, order_id="T")
    assert applied == 0
    assert anchored.score == 88.0
    assert "bonus_nogps_neutral_km" not in anchored.metrics
    # jego realny km WCHODZI do puli known → mediana z {2.5, 4.0}
    kmpool = [anchored, FakeCand("n", 1.0, "no_gps", True), FakeCand("a", 4.0, "gps", False)]
    km, _ = dp._nogps_neutral_score_pass(kmpool, order_id="T")
    assert km == 3.25


def test_on_post_wave_untouched(monkeypatch):
    """post_wave (F2.1c override) ∉ POSITION_UNKNOWN_SOURCES → nietknięty,
    nawet gdyby road był z centrum (edge: bag bez planu)."""
    _flag(monkeypatch, True)
    assert "post_wave" not in POSITION_UNKNOWN_SOURCES
    pw = FakeCand("300", 1.5, "post_wave", True, total=85.0)
    pool = [pw, FakeCand("a", 4.0, "gps", False)]
    _, applied = dp._nogps_neutral_score_pass(pool, order_id="T")
    assert applied == 0 and pw.score == 85.0


def test_on_pre_shift_score_neutralized(monkeypatch):
    """pre_shift ∈ POSITION_UNKNOWN_SOURCES — score z centrum też neutralizowany
    (display km=None zostaje w F1.7; tu tylko score)."""
    _flag(monkeypatch, True)
    ps = FakeCand("600", 1.2, "pre_shift", True, total=90.0)
    pool = [ps, FakeCand("a", 4.0, "gps", False), FakeCand("b", 6.0, "gps", False)]
    _, applied = dp._nogps_neutral_score_pass(pool, order_id="T")
    assert applied == 1
    assert ps.score < 90.0
    assert ps.metrics["bonus_nogps_neutral_applied"] is True


def test_on_empty_known_pool_fallback_5km(monkeypatch):
    _flag(monkeypatch, True)
    pool = [FakeCand("n1", 1.0, "no_gps", True), FakeCand("n2", 1.3, "no_gps", True)]
    km, applied = dp._nogps_neutral_score_pass(pool, order_id="T")
    assert km == 5.0 and applied == 2


# ── v2 (recenzja adwersaryjna pkt #2+#3): donorzy mediany = tylko MAYBE ────────

def test_on_single_donor_median_is_its_km(monkeypatch):
    """Dokładnie 1 wykonalny donor → mediana = jego km (bez interpolacji/fallbacku)."""
    _flag(monkeypatch, True)
    pool = [FakeCand("a", 3.7, "gps", False),
            FakeCand("n", 1.2, "no_gps", True)]
    km, applied = dp._nogps_neutral_score_pass(pool, order_id="T")
    assert km == 3.7 and applied == 1
    assert pool[1].metrics["bonus_nogps_neutral_km"] == 3.7


def test_on_donor_verdict_no_excluded_from_median(monkeypatch):
    """Donor z feasibility_verdict=='NO' (HARD-NO: post-shift/R-35MIN) NIE zasila
    mediany — nie jest konkurentem. Mediana z samych MAYBE: {2,4} → 3.0
    (z NO byłoby {2,4,20} → 4.0)."""
    _flag(monkeypatch, True)
    pool = [FakeCand("a", 2.0, "gps", False),
            FakeCand("b", 4.0, "gps", False),
            FakeCand("no", 20.0, "gps", False, verdict="NO"),
            FakeCand("n", 1.0, "no_gps", True)]
    km, applied = dp._nogps_neutral_score_pass(pool, order_id="T")
    assert km == 3.0 and applied == 1
    # HARD-NO nietykany (nie jest celem neutralizacji — ma realny km)
    assert "bonus_nogps_neutral_km" not in pool[2].metrics


def test_on_all_donors_verdict_no_fallback_5km(monkeypatch):
    """Realne kotwice istnieją, ale WSZYSTKIE HARD-NO → 0 donorów → fallback 5.0
    (mirror F1.7), neutralizacja no-GPS dalej działa."""
    _flag(monkeypatch, True)
    pool = [FakeCand("a", 2.0, "gps", False, verdict="NO"),
            FakeCand("b", 9.0, "gps", False, verdict="NO"),
            FakeCand("n", 1.0, "no_gps", True)]
    km, applied = dp._nogps_neutral_score_pass(pool, order_id="T")
    assert km == 5.0 and applied == 1
    assert pool[2].metrics["bonus_nogps_neutral_km"] == 5.0


def test_on_neutralized_nogps_verdict_no_still_shadowed(monkeypatch):
    """Cel neutralizacji z werdyktem NO: shadow/apply liczone (score spójny w
    serializacji), ale selekcja go i tak odfiltruje (E2E niżej to domyka)."""
    _flag(monkeypatch, True)
    pool = [FakeCand("a", 4.0, "gps", False),
            FakeCand("n", 1.0, "no_gps", True, verdict="NO")]
    km, applied = dp._nogps_neutral_score_pass(pool, order_id="T")
    assert km == 4.0 and applied == 1
    assert pool[1].metrics["bonus_nogps_neutral_applied"] is True


def test_on_off_delta_composes_with_equal_treatment_bucket():
    """Neutralizacja NIE dotyka pos_source/bucketów — equal-treatment składa się
    ortogonalnie (bucket z pos_source, score z pass). Kontrakt: pass nie pisze
    pos_source ani kluczy bucketowych."""
    src = inspect.getsource(dp._nogps_neutral_score_pass)
    assert 'm["pos_source"]' not in src
    assert "_selection_bucket" not in src


# ── source-regression: wpięcia głęboko w assess_order (wzorzec always-propose) ──

def test_pass_wired_before_display_loop():
    """Pass wywołany w bloku F1.7 PRZED pętlą nadpisującą display
    (shadow musi widzieć surowe road-km z centrum)."""
    src = inspect.getsource(dp)
    i_pass = src.find("_nogps_neutral_score_pass(\n        candidates, order_id)")
    i_loop = src.find('if ps == "no_gps":')
    assert i_pass != -1 and i_loop != -1
    assert i_pass < i_loop, "pass MUSI biec przed pętlą display F1.7"


def test_display_follows_applied_flag_no_gps_branch():
    """Branch no_gps: display km = neutral_km gdy applied, inaczej legacy fleet_avg."""
    src = inspect.getsource(dp)
    i_loop = src.find('if ps == "no_gps":')
    section = src[i_loop:i_loop + 800]
    assert 'bonus_nogps_neutral_applied' in section
    assert "_nogps_neutral_km" in section
    assert "fleet_avg_km" in section


def test_display_twin_other_synthetics_branch():
    """Bliźniak display: pozostałe syntetyki (pin/none/post_shift_synth/
    working_override) z applied → km = neutral_km (elif po pre_shift)."""
    src = inspect.getsource(dp)
    i_pre = src.find('elif ps == "pre_shift":')
    i_elif = src.find('elif c.metrics.get("bonus_nogps_neutral_applied")', i_pre)
    assert i_elif != -1, "brak bliźniaka display dla pozostałych syntetyków"


def test_candidates_source_classifies_synthetic_road_km():
    """core.candidates: klasyfikacja u źródła — is_position_known (F-3, jedno
    źródło) + anchor/bag-tail tracking + metryka w enriched_metrics."""
    src = inspect.getsource(k11c)
    assert "road_km_from_synthetic_pos = (" in src
    assert "is_position_known(getattr(cs, \"pos_source\", None))" in src
    assert "v326_anchor_used or _v326_bag_tail_used" in src
    assert '"road_km_from_synthetic_pos": road_km_from_synthetic_pos' in src


def test_no_divergent_copy_in_plan_recheck():
    """plan_recheck dziedziczy score przez kanon — NIE wolno mieć własnej kopii
    normalizacji no-GPS (wzorzec #2: fix w 1 ścieżce z N bliźniaczych)."""
    from dispatch_v2 import plan_recheck
    src = inspect.getsource(plan_recheck)
    assert "s_dystans" not in src
    assert "nogps_neutral" not in src
    assert "fleet_avg_km" not in src


# ── v2 (recenzja adwersaryjna pkt #9): E2E funkcjonalny pass → selekcja ───────
# Sekwencja jak w _assess_order_impl: _nogps_neutral_score_pass biegnie na puli
# PRZED select_and_emit (core/selection = prawdziwa selekcja: filtr MAYBE,
# sort, buckety, tiering, bramki werdyktu). Zero source-asercji — sam wynik.

_E2E_NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)


def _sel_ctx():
    return sel.SelectionContext(
        now=_E2E_NOW, order_event={"order_id": "E2E1"}, order_id="E2E1",
        restaurant="Testownia", delivery_address="Testowa 1",
        pickup_coords=(53.13, 23.16), delivery_coords=(53.14, 23.17),
        pickup_ready_at=None, new_order=SimpleNamespace(order_id="E2E1"),
        fleet_snapshot={}, v328_fail_causes={},
    )


def _real_cand(cid, km, pos_source, synth, verdict="MAYBE", sd=None, total=80.0):
    """Prawdziwy dp.Candidate (dataclass selekcji) z metrykami jak FakeCand."""
    f = FakeCand(cid, km, pos_source, synth, sd=sd, total=total, verdict=verdict)
    return dp.Candidate(
        courier_id=cid, name=f"K{cid}", score=f.score,
        feasibility_verdict=verdict, feasibility_reason="",
        plan=None, metrics=f.metrics,
    )


def _e2e_flags(monkeypatch, on: bool):
    """Środowisko flag jak live 19.07 (flags.json): ENABLE_NO_GPS_EQUAL_TREATMENT
    + ENABLE_EQUAL_TREATMENT_BUCKET ON → no_gps konkuruje CZYSTYM score w bucket 0
    (dokładnie tam manifestuje się bug centrum). Reszta = default. Nasza flaga =
    parametr `on`."""
    monkeypatch.setattr(
        dp.C, "decision_flag",
        lambda f: on if f == "ENABLE_NO_GPS_NEUTRAL_SCORE_DIST" else False)
    monkeypatch.setattr(
        dp.C, "flag",
        lambda name, default=False: True if name in (
            "ENABLE_NO_GPS_EQUAL_TREATMENT",
            "ENABLE_EQUAL_TREATMENT_BUCKET") else default)


def _e2e_pool():
    """Pula wzorowana na landslide 112-vs-4.1: no-GPS z centrum (1.2 km FIKCJI)
    ma najwyższy surowy score; GPS-bliski 2.0 km drugi; GPS 4.0/9.0 wypełniają
    rozkład; HARD-NO z absurdalnym score 120 i realnym km 0.5 — nie może ani
    wygrać, ani zasilić mediany. Donorzy MAYBE: {2.0, 4.0, 9.0} → mediana 4.0."""
    sd_c = round(s_dystans(1.2), 2)
    nogps = _real_cand("179", 1.2, "no_gps", True, sd=sd_c,
                       total=round(sd_c * W_DYSTANS + 100 * 0.7, 2))
    gps_close = _real_cand("400", 2.0, "gps", False,
                           total=round(s_dystans(2.0) * W_DYSTANS + 100 * 0.7, 2))
    gps_mid = _real_cand("500", 4.0, "gps", False, total=80.0)
    gps_far = _real_cand("509", 9.0, "gps", False, total=60.0)
    hard_no = _real_cand("999", 0.5, "gps", False, verdict="NO", total=120.0)
    return nogps, gps_close, [nogps, gps_close, gps_mid, gps_far, hard_no]


def test_e2e_off_selection_keeps_bug_and_hard_no_never_wins(monkeypatch):
    """OFF = bajt-parytet decyzji: no-GPS z centrum dalej wygrywa (bug zachowany),
    HARD-NO odfiltrowany przez selekcję mimo najwyższego score."""
    _e2e_flags(monkeypatch, False)
    nogps, gps_close, pool = _e2e_pool()
    km, applied = dp._nogps_neutral_score_pass(pool, order_id="E2E1")
    assert applied == 0
    res = sel.select_and_emit(_sel_ctx(), pool)
    assert type(res).__name__ == "PipelineResult"
    assert res.verdict == "PROPOSE" and res.pool_feasible_count == 4
    assert res.best is nogps
    assert res.best.feasibility_verdict == "MAYBE"
    assert res.best.courier_id != "999"


def test_e2e_on_pass_plus_selection_flips_winner(monkeypatch):
    """ON: neutralizacja medianą WYKONALNYCH donorów → zwycięzcą zostaje
    GPS-bliski (winner flip vs OFF); HARD-NO dalej nigdy nie wygrywa."""
    _e2e_flags(monkeypatch, True)
    nogps, gps_close, pool = _e2e_pool()
    km, applied = dp._nogps_neutral_score_pass(pool, order_id="E2E1")
    # Donorzy MAYBE {2,4,9} → mediana 4.0. HARD-NO (0.5 km) wykluczony —
    # z nim mediana spadłaby do 3.0 (dowód filtra wewnątrz E2E).
    assert km == 4.0 and applied == 1
    res = sel.select_and_emit(_sel_ctx(), pool)
    assert res.verdict == "PROPOSE"
    assert res.best is gps_close
    assert res.best is not nogps
    assert res.best.feasibility_verdict == "MAYBE"
    assert res.best.courier_id != "999"
