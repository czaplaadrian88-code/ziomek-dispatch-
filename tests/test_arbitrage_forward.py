"""[ARB] Testy czystych helperów arbitrażu cross-regime.

CAŁKOWICIE SYNTETYCZNE / IN-MEMORY — bez parquet, bez lightgbm. Uruchamiane pod
SYSTEMOWYM python3 (numpy obecne, pyarrow/lightgbm NIE):

  cd /root/.openclaw/workspace/scripts/dispatch_v2
  python3 -m pytest tests/test_arbitrage_forward.py -q

Testowane czyste funkcje (importowane z ml_data_prep/arbitrage_forward.py):
  is_empty_bag, regime_of, classify_decision, regime_score, argmax_idx,
  top1_is_correct, apply_offset, zscore_standardize, apply_zscore.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Wstaw ml_data_prep na sys.path (harness leży tam; importujemy tylko czyste helpery).
ML_DATA_PREP = Path(__file__).resolve().parent.parent / "ml_data_prep"
if str(ML_DATA_PREP) not in sys.path:
    sys.path.insert(0, str(ML_DATA_PREP))

import arbitrage_forward as arb  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# (a) routing po stanie worka kandydata
# ─────────────────────────────────────────────────────────────────────────────
def test_routing_empty_bag_goes_solo():
    assert arb.is_empty_bag(0, 0, 0) is True
    assert arb.regime_of(arb.is_empty_bag(0, 0, 0)) == "solo"


def test_routing_any_bag_load_goes_bundle():
    # cokolwiek niezerowego w którymkolwiek polu → bundle
    assert arb.is_empty_bag(1, 0, 0) is False
    assert arb.is_empty_bag(0, 1, 0) is False
    assert arb.is_empty_bag(0, 0, 1) is False
    assert arb.regime_of(arb.is_empty_bag(2, 1, 0)) == "bundle"


def test_routing_none_and_nan_treated_as_empty():
    assert arb.is_empty_bag(None, None, None) is True
    assert arb.is_empty_bag(float("nan"), 0, 0) is True
    # routing per-kandydat: różni kandydaci tej samej decyzji mogą iść różnymi reżimami
    cands = [(0, 0, 0), (3, 1, 0), (0, 0, 0)]
    regimes = [arb.regime_of(arb.is_empty_bag(*c)) for c in cands]
    assert regimes == ["solo", "bundle", "solo"]


# ─────────────────────────────────────────────────────────────────────────────
# (b) klasyfikacja decyzji MIXED / PURE_SOLO / PURE_BUNDLE
# ─────────────────────────────────────────────────────────────────────────────
def test_subset_mixed():
    assert arb.classify_decision([True, False]) == "MIXED"
    assert arb.classify_decision([False, True, False]) == "MIXED"


def test_subset_pure_solo():
    assert arb.classify_decision([True, True, True]) == "PURE_SOLO"
    assert arb.classify_decision([True]) == "PURE_SOLO"


def test_subset_pure_bundle():
    assert arb.classify_decision([False, False]) == "PURE_BUNDLE"
    assert arb.classify_decision([]) == "PURE_BUNDLE"  # brak kandydatów → bundle (degenerat)


# ─────────────────────────────────────────────────────────────────────────────
# (c) top-1 wybiera wiersz label==1 przy ręcznie zadanych score'ach
# ─────────────────────────────────────────────────────────────────────────────
def test_top1_picks_label_one_via_regime_score():
    # 3 kandydatów; zwycięzca (label==1) ma najwyższy regime_score
    empties = [True, False, True]
    solo = [9.0, 0.0, 1.0]     # idx0 empty solo=9 (najwyższy w swoim reżimie)
    bundle = [0.0, 2.0, 0.0]   # idx1 bagged bundle=2
    labels = [1, 0, 0]
    eff = [arb.regime_score(e, s, b) for e, s, b in zip(empties, solo, bundle)]
    pick = arb.argmax_idx(eff)
    assert pick == 0
    assert arb.top1_is_correct(pick, labels) is True


def test_top1_incorrect_when_scores_favor_loser():
    empties = [True, False]
    solo = [1.0, 0.0]
    bundle = [0.0, 50.0]   # bagged loser dominuje
    labels = [1, 0]        # winner = idx0
    eff = [arb.regime_score(e, s, b) for e, s, b in zip(empties, solo, bundle)]
    pick = arb.argmax_idx(eff)
    assert pick == 1
    assert arb.top1_is_correct(pick, labels) is False


def test_argmax_first_on_tie_and_empty():
    assert arb.argmax_idx([5.0, 5.0, 5.0]) == 0
    assert arb.argmax_idx([]) == -1
    assert arb.top1_is_correct(-1, [0, 1]) is False
    assert arb.top1_is_correct(5, [0, 1]) is False  # out of range


# ─────────────────────────────────────────────────────────────────────────────
# (d) offset-δ arbitraż przesuwa zwycięzcę MONOTONICZNIE w miarę wzrostu δ
# ─────────────────────────────────────────────────────────────────────────────
def test_offset_delta_flips_winner_monotonically():
    # idx0 empty (solo=10, stałe), idx1 bagged (bundle=2 + δ). Rosnące δ → idx1 przejmuje.
    empties = [True, False]
    solo = [10.0, 0.0]
    bundle = [0.0, 2.0]

    # małe δ: solo wygrywa (10 > 2+δ dla δ<8)
    eff_low = arb.apply_offset(empties, solo, bundle, 0.0)
    assert arb.argmax_idx(eff_low) == 0

    # próg: δ=8 → 2+8=10 == 10 (remis → pierwszy = idx0 wciąż wygrywa argmaxem)
    eff_at = arb.apply_offset(empties, solo, bundle, 8.0)
    assert arb.argmax_idx(eff_at) == 0

    # duże δ: bagged przejmuje (2+δ > 10 dla δ>8)
    eff_high = arb.apply_offset(empties, solo, bundle, 20.0)
    assert arb.argmax_idx(eff_high) == 1

    # monotoniczność: gdy raz przeskoczy na idx1, dalszy wzrost δ NIE wraca na idx0
    prev_pick = 0
    flipped = False
    for delta in [0.0, 4.0, 7.9, 8.1, 12.0, 50.0]:
        pick = arb.argmax_idx(arb.apply_offset(empties, solo, bundle, delta))
        if pick == 1:
            flipped = True
        # po flipie nie wolno wrócić do idx0
        if flipped:
            assert pick == 1
        prev_pick = pick
    assert flipped is True


def test_offset_does_not_touch_solo_candidates():
    # δ przesuwa TYLKO bundle; solo score kandydata empty zostaje nietknięty
    empties = [True, True]
    solo = [3.0, 7.0]
    bundle = [99.0, 99.0]  # nieistotne, bo obaj empty
    for delta in [0.0, 100.0, -100.0]:
        eff = arb.apply_offset(empties, solo, bundle, delta)
        assert eff == [3.0, 7.0]
        assert arb.argmax_idx(eff) == 1


# ─────────────────────────────────────────────────────────────────────────────
# (e) poprawność standaryzacji zscore
# ─────────────────────────────────────────────────────────────────────────────
def test_zscore_standardize_basic():
    assert arb.zscore_standardize(2.0, 0.0, 2.0) == 1.0
    assert arb.zscore_standardize(0.0, 0.0, 2.0) == 0.0
    assert arb.zscore_standardize(-2.0, 0.0, 2.0) == -1.0


def test_zscore_zero_variance_guard():
    # std<=0 → 0.0 (bez dzielenia przez zero)
    assert arb.zscore_standardize(5.0, 5.0, 0.0) == 0.0
    assert arb.zscore_standardize(5.0, 1.0, -1.0) == 0.0


def test_zscore_arbitrage_flips_on_scale_mismatch():
    # SOLO żyje w skali ~[0,1], BUNDLE w skali ~[0,100]. Surowy argmax wybrałby bagged.
    # Po standaryzacji każdy względem SWOJEJ populacji — wygrywa ten wyżej we własnym rozkładzie.
    empties = [True, False]
    solo = [0.9, 0.0]       # empty kandydat: 0.9 (wysoko w skali solo)
    bundle = [0.0, 60.0]    # bagged kandydat: 60 (przeciętnie w skali bundle)
    labels = [1, 0]

    # surowy regime_score: bundle 60 >> solo 0.9 → źle (wybiera losera)
    raw = [arb.regime_score(e, s, b) for e, s, b in zip(empties, solo, bundle)]
    assert arb.argmax_idx(raw) == 1
    assert arb.top1_is_correct(arb.argmax_idx(raw), labels) is False

    # zscore: solo_mean=0.5 std=0.2 (0.9 → +2.0); bundle_mean=50 std=20 (60 → +0.5)
    eff = arb.apply_zscore(empties, solo, bundle,
                           solo_mean=0.5, solo_std=0.2,
                           bundle_mean=50.0, bundle_std=20.0)
    # solo z=+2.0, bundle z=+0.5 → empty wygrywa, top-1 poprawny
    assert arb.argmax_idx(eff) == 0
    assert arb.top1_is_correct(arb.argmax_idx(eff), labels) is True


# ─────────────────────────────────────────────────────────────────────────────
# bonus: spójność regime_score z routingiem (sanity)
# ─────────────────────────────────────────────────────────────────────────────
def test_regime_score_uses_correct_model_score():
    assert arb.regime_score(True, 7.0, 99.0) == 7.0    # empty → solo
    assert arb.regime_score(False, 7.0, 99.0) == 99.0  # bagged → bundle
