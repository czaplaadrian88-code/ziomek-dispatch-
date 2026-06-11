"""TICK-OVERLAP-05 (2026-06-12): tracker headroomu ticka elapsed/interval."""
from dispatch_v2.panel_watcher import _TickOverlapTracker


def test_no_warning_under_threshold():
    t = _TickOverlapTracker()
    assert t.note(10.0, 20.0, now=1000.0) == ""
    assert t.last_ratio == 0.5
    frag = t.summary_fragment()
    assert "ratio_last=0.50" in frag and "over0.8=0/1" in frag


def test_warning_over_threshold_and_cooldown():
    t = _TickOverlapTracker()
    w1 = t.note(18.0, 20.0, now=1000.0)
    assert "TICK_OVERLAP ratio=0.90" in w1
    # cooldown 300 s — kolejne przekroczenie liczone, ale bez spamu
    w2 = t.note(19.0, 20.0, now=1100.0)
    assert w2 == ""
    # po cooldownie znów ostrzega
    w3 = t.note(25.0, 20.0, now=1400.0)
    assert "TICK_OVERLAP ratio=1.25" in w3
    frag = t.summary_fragment()
    assert "over0.8=3/3" in frag and "ratio_max=1.25" in frag


def test_summary_resets_window():
    t = _TickOverlapTracker()
    t.note(18.0, 20.0, now=1000.0)
    t.summary_fragment()
    frag = t.summary_fragment()
    assert "over0.8=0/0" in frag and "ratio_max=0.00" in frag


def test_zero_interval_safe():
    t = _TickOverlapTracker()
    assert t.note(5.0, 0.0, now=1000.0) == ""
    assert t.last_ratio == 0.0
