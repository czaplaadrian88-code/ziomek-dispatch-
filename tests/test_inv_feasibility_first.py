"""INV-FEASIBILITY-FIRST (audyt 2026-06-24, spec odporności §6.A): runtime strażnik gwarancji
P0 — żaden kandydat verdict='NO' nie może być w puli selekcji (HARD przed SOFT). Fail-loud
(log.error + metryka), fail-soft (nie crashuje).
"""
import logging

import dispatch_v2.dispatch_pipeline as DP


class _C:
    def __init__(self, cid, verdict):
        self.courier_id = cid
        self.feasibility_verdict = verdict
        self.metrics = {}


def test_clean_pool_no_log(caplog):
    pool = [_C("A", "MAYBE"), _C("B", "MAYBE")]
    with caplog.at_level(logging.ERROR):
        DP._assert_feasibility_first(pool, order_id="O1")
    assert "INV_FEASIBILITY_FIRST_VIOLATION" not in caplog.text


def test_no_verdict_in_pool_fires_loud(caplog):
    bad = _C("BAD", "NO")
    pool = [_C("A", "MAYBE"), bad, _C("B", "MAYBE")]
    with caplog.at_level(logging.ERROR):
        DP._assert_feasibility_first(pool, order_id="O2")
    assert "INV_FEASIBILITY_FIRST_VIOLATION" in caplog.text
    assert "BAD" in caplog.text
    assert bad.metrics.get("inv_feasibility_first_violation") is True


def test_fail_soft_never_raises():
    # zdegenerowane wejście (None, brak atrybutów) nie może wywrócić pętli decyzyjnej
    DP._assert_feasibility_first([None, object()], order_id="O3")
    DP._assert_feasibility_first([], order_id="O4")


def test_guard_is_wired_after_demote():
    # strażnik konformacji: wywołanie MUSI być w łańcuchu selekcji (po demote)
    import inspect
    src = inspect.getsource(DP)
    assert "_assert_feasibility_first(feasible, order_id)" in src, \
        "INV-FEASIBILITY-FIRST musi być wpięte w selekcję (po _demote_blind_empty)"
