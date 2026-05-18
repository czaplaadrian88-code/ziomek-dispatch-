"""Sprint OBJ F3 / BUG-5 (2026-05-18) — pomiar R6 przed sla-return.

BUG-5 (diagnoza 474297): feasibility_v2 robił return na `sla_violations` PRZED
blokiem pomiaru R6 → kandydat sla-rejected miał r6_* metryki = null →
_r6_pov_count (dispatch_pipeline best_effort) widział 0, reason "r6_violations=0"
kłamał przy realnym 70-82 min carry.

Fix = reorder: blok pomiaru R6 (sam pomiar, bez return-ów) PRZED `if
plan.sla_violations > 0`. Test = causal source-regression: weryfikuje że
przypisanie metryk R6 wyprzedza sla-check, blok pomiaru nie jest zduplikowany,
a hard-reject R6 zostaje PO sla-check (kolejność werdyktów niezmieniona).
"""
import inspect

from dispatch_v2 import feasibility_v2


def _src():
    return inspect.getsource(feasibility_v2)


def test_bug5_comment_header_present():
    assert "Sprint OBJ F3 / BUG-5" in _src()


def test_bug5_r6_metrics_assigned_before_sla_check():
    """metrics['r6_per_order_violations'] przypisane PRZED `if plan.sla_violations > 0`."""
    src = _src()
    r6_assign = src.find('metrics["r6_per_order_violations"]')
    sla_check = src.find("if plan.sla_violations > 0:")
    assert r6_assign > 0 and sla_check > 0
    assert r6_assign < sla_check, (
        f"pomiar R6 musi być przed sla-check: r6_assign={r6_assign} "
        f"sla_check={sla_check}")


def test_bug5_r6_measurement_loop_not_duplicated():
    """Blok pomiaru R6 (r6_max_bag_time = 0.0) występuje DOKŁADNIE raz."""
    src = _src()
    assert src.count("r6_max_bag_time = 0.0") == 1


def test_bug5_r6_hard_reject_stays_after_sla_check():
    """R6 hard-reject (if r6_per_order_violations: return NO) zostaje PO sla-check."""
    src = _src()
    sla_check = src.find("if plan.sla_violations > 0:")
    r6_reject = src.find("if r6_per_order_violations:")
    assert sla_check > 0 and r6_reject > 0
    assert sla_check < r6_reject, (
        "sla-check musi pozostać przed R6 hard-reject (kolejność werdyktów)")
