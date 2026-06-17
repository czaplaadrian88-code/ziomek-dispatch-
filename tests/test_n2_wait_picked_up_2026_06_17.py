"""N2 (2026-06-17): hard-reject 'stygnące jedzenie' wg ODEBRANYCH, nie przypisanych.

Adrian ACK + replay dowodowy 481410/cid413. Reguła:
- kurier z odebranym (gorącym) jedzeniem (picked_up>=1) → hard-reject przy długim idle (bez zmian);
- kurier BEZ odebranego (0 picked_up) → BRAK hard-reject, ale ROSNĄCA kara soft powyżej 5 min.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2.scoring import (  # noqa: E402
    compute_wait_courier_penalty,
    compute_idle_wait_soft_penalty,
)


# --- rosnąca kara idle (empty-handed) ---

def test_idle_zero_below_threshold():
    assert compute_idle_wait_soft_penalty(0.0) == 0.0
    assert compute_idle_wait_soft_penalty(3.0) == 0.0
    assert compute_idle_wait_soft_penalty(5.0) == 0.0


def test_idle_grows_above_threshold():
    p10 = compute_idle_wait_soft_penalty(10.0)
    p21 = compute_idle_wait_soft_penalty(21.0)
    # rośnie (coraz bardziej ujemna) z czasem czekania
    assert p10 < 0.0
    assert p21 < p10
    # linearnie: (wait - 5) * per_min
    assert abs(p10 - (10.0 - C.V3273_WAIT_IDLE_SOFT_THRESHOLD_MIN) * C.V3273_WAIT_IDLE_SOFT_PER_MIN) < 1e-6


def test_idle_never_rejects():
    # to czysty soft — funkcja zwraca tylko liczbę, brak komponentu reject
    assert isinstance(compute_idle_wait_soft_penalty(60.0), float)


def test_idle_none_safe():
    assert compute_idle_wait_soft_penalty(None) == 0.0


# --- reżim hard-reject: gorące jedzenie zachowane, puste ręce bez reject ---

def test_hot_food_still_hard_rejects():
    # >=1 odebrane (gorące) + długi idle → hard reject (bez zmian)
    pen, reject = compute_wait_courier_penalty(21.0, bag_size_at_insertion=1)
    assert reject is True


def test_empty_hands_no_reject():
    # 0 odebrane → reguła stygnięcia się nie odpala (nic nie stygnie)
    pen, reject = compute_wait_courier_penalty(21.0, bag_size_at_insertion=0)
    assert reject is False
    assert pen == 0.0


def test_case_481410_413_flip():
    """Case docelowy: 413 (0 odebrane) wait 21,09 min.
    DZIŚ (licznik=len(bag)=1) → reject. PO FIX (licznik=picked_up=0) → brak reject
    + rosnąca kara soft ~ -64. Sprawdzamy obie warstwy."""
    wait = 21.09
    # po fix: licznik = picked_up = 0
    _, reject_fix = compute_wait_courier_penalty(wait, bag_size_at_insertion=0)
    assert reject_fix is False
    idle = compute_idle_wait_soft_penalty(wait)
    assert idle < 0.0            # idle karany
    assert idle > -200.0         # ale rozsądnie, nie eksplozja
    # stary licznik = len(bag) = 1 → reject (potwierdza że to licznik decydował)
    _, reject_old = compute_wait_courier_penalty(wait, bag_size_at_insertion=1)
    assert reject_old is True
