"""Oracle kryterium replayu C7 po decyzji ownera z 2026-07-24."""

from dispatch_v2.tools import post_shift_overrun_forward_replay as replay


def test_replay_classifies_35_to_15_as_improvement_not_review():
    result = replay.classify_overrun_change(35.0, 15.0)

    assert result.classification == replay.IMPROVEMENT
    assert result.delta_min == -20.0


def test_replay_distinguishes_zero_as_the_best_class():
    result = replay.classify_overrun_change(15.0, 0.0)
    in_shift = replay.classify_overrun_change(15.0, -30.0)

    assert result.classification == replay.IDEAL
    assert result.delta_min == -15.0
    assert in_shift.classification == replay.IDEAL
    assert in_shift.delta_min == -15.0


def test_replay_keeps_worsening_bad_and_equality_unchanged():
    worse = replay.classify_overrun_change(15.0, 35.0)
    same = replay.classify_overrun_change(15.0, 15.0)

    assert worse.classification == replay.WORSENED
    assert worse.delta_min == 20.0
    assert same.classification == replay.UNCHANGED
    assert same.delta_min == 0.0


def test_source_ratchet_rejects_return_to_binary_grace_criterion():
    source = replay.classify_overrun_change.__doc__ or ""

    assert "after < before" in source
    assert "GRACE" not in source
