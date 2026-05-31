"""Fix #6 477285 (2026-05-31) — R6-soft danger zone: progresywna kara near-limit.

Stary R6-soft = liniowy -8/min nad 30 → 33.9 min = -31.2, za słabe by Aleksander
(33.9/35, wciśnięty 3. order Kołłątaja) przegrał z Andreiem (29.1 min <30, 0 kary).
Fix #6: strefa 30-32 bez zmian (bufor R-BUFFER-OK), strefa 32-35 EKSTRA -16/min (∑-24)
→ 33.9 = -61.6 → score Aleksandra spada o -30.4 → Andrei (lepszy dowóz) wygrywa.
Adrian: „lepiej przedłużyć odbiór 15-20 min i zawieźć w 20 min niż wozić 35 min".
"""
import importlib
import inspect

from dispatch_v2 import common, dispatch_pipeline, shadow_dispatcher
from dispatch_v2.dispatch_pipeline import _r6_soft_penalty


# === flaga + stałe ===

def test_flag_and_constants_default():
    assert common.ENABLE_R6_DANGER_ZONE_PENALTY is True
    assert common.BAG_TIME_DANGER_MIN == 32.0
    assert common.BAG_TIME_DANGER_PENALTY_PER_MIN == 16.0


def test_flag_off_via_env(monkeypatch):
    monkeypatch.setenv("ENABLE_R6_DANGER_ZONE_PENALTY", "0")
    m = importlib.reload(common)
    assert m.ENABLE_R6_DANGER_ZONE_PENALTY is False
    monkeypatch.setenv("ENABLE_R6_DANGER_ZONE_PENALTY", "1")
    importlib.reload(common)


# === penalty math ===

def test_under_soft_zero():
    assert _r6_soft_penalty(29.1, 30, 8, True, 32, 16) == (0.0, 0.0)
    assert _r6_soft_penalty(30.0, 30, 8, True, 32, 16) == (0.0, 0.0)


def test_soft_zone_linear_unchanged():
    # 30-32 = bufor R-BUFFER-OK, bez ekstra kary (danger zaczyna się od 32)
    pen, leg = _r6_soft_penalty(31.0, 30, 8, True, 32, 16)
    assert pen == -8.0 and leg == -8.0
    pen, leg = _r6_soft_penalty(32.0, 30, 8, True, 32, 16)
    assert pen == -16.0 and leg == -16.0


def test_danger_zone_steeper():
    # 33 min: legacy -24, danger ekstra -16 → -40
    pen, leg = _r6_soft_penalty(33.0, 30, 8, True, 32, 16)
    assert leg == -24.0
    assert pen == -40.0
    # 33.9 (477285 Kołłątaja): legacy -31.2 → danger -61.6 (≈2×)
    pen, leg = _r6_soft_penalty(33.9, 30, 8, True, 32, 16)
    assert abs(leg - (-31.2)) < 0.01
    assert abs(pen - (-61.6)) < 0.01
    # 35 (hard limit): legacy -40 → danger -88
    pen, leg = _r6_soft_penalty(35.0, 30, 8, True, 32, 16)
    assert pen == -88.0 and leg == -40.0


def test_flag_off_equals_legacy():
    pen, leg = _r6_soft_penalty(33.9, 30, 8, False, 32, 16)
    assert pen == leg  # danger off → tylko liniowa
    assert abs(pen - (-31.2)) < 0.01


def test_none_zero():
    assert _r6_soft_penalty(None, 30, 8, True, 32, 16) == (0.0, 0.0)


# === 477285: flip Aleksander (33.9) -> Andrei (29.1) ===

def test_477285_danger_penalty_flips_winner():
    """Ekstra kara danger na Aleksandrze (33.9) MUSI przewyższyć przewagę score
    nad Andreiem (29.1, 0 kary R6), żeby wygrał lepszy dowóz."""
    alek_pen_new, alek_pen_leg = _r6_soft_penalty(33.9, 30, 8, True, 32, 16)
    andrei_pen_new, _ = _r6_soft_penalty(29.1, 30, 8, True, 32, 16)
    extra_on_alek = alek_pen_leg - alek_pen_new   # ile dodatkowo karzemy Aleksandra
    score_gap = 0.6 - (-0.5)                       # Aleksander 0.6 vs Andrei -0.5 (z replay)
    assert andrei_pen_new == 0.0                   # Andrei <30 → bez kary, score nietknięty
    assert extra_on_alek > score_gap, (
        f"ekstra kara danger ({extra_on_alek:.1f}) musi przewyższyć lukę score ({score_gap:.1f}) "
        f"żeby flip nastąpił")


# === source-regression: inline używa helpera + legacy + shadow + serializer ===

def test_inline_uses_helper_and_legacy():
    src = inspect.getsource(dispatch_pipeline)
    assert "bonus_r6_soft_pen, bonus_r6_soft_pen_legacy = _r6_soft_penalty(" in src
    assert '"bonus_r6_soft_pen_legacy":' in src  # serializowane do metrics


def test_r6_danger_shadow_computed_and_attached():
    src = inspect.getsource(dispatch_pipeline)
    assert "r6_danger_shadow" in src
    assert "R6_DANGER_DIVERGENCE" in src
    assert "_legacy_r6_score" in src
    assert "_result_pf.r6_danger_shadow = r6_danger_shadow" in src


def test_shadow_serializes_r6_danger():
    src = inspect.getsource(shadow_dispatcher)
    assert '"r6_danger_shadow"' in src
    assert '"bonus_r6_soft_pen_legacy"' in src
