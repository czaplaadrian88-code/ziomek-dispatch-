"""Test load-aware selection SHADOW (2026-06-07) — pure helper _compute_loadaware_shadow.
Log-only counterfactual: kogo wybrałaby dystrybucja load-aware (najmniej obłożony
z PEŁNEGO rosteru) vs argmax-best. Walidacja: zero mutacji, poprawny pick + split
feasible/roster. Patrz memory ziomek-autonomy-cascade-verdict.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dispatch_v2 import common, dispatch_pipeline  # noqa: E402


def _c(cid, bag, score, feas="MAYBE", pos="gps"):
    return SimpleNamespace(
        courier_id=cid, score=score, feasibility_verdict=feas,
        metrics={"bag_size_before": bag, "pos_source": pos},
    )


def test_flag_default_off():
    assert common.ENABLE_LOADAWARE_SELECTION_SHADOW is False


def test_none_when_no_candidates():
    assert dispatch_pipeline._compute_loadaware_shadow([], [], []) is None


def test_least_loaded_full_roster_and_feasible_split():
    a = _c("1", 3, 100.0)            # argmax-best: feasible, wysoki score, ciężki bag
    b = _c("2", 0, -5.0, feas="NO")  # najlżejszy OGÓLEM, ale INFEASIBLE
    d = _c("3", 1, 50.0)             # najlżejszy wśród FEASIBLE
    r = dispatch_pipeline._compute_loadaware_shadow([a, b, d], [a, d], [a])
    assert r["best_cid"] == "1" and r["best_bag"] == 3
    assert r["la_roster_cid"] == "2" and r["la_roster_bag"] == 0       # incl. infeasible
    assert r["la_feasible_cid"] == "3" and r["la_feasible_bag"] == 1   # tylko feasible
    assert r["changed_roster"] is True
    assert r["changed_feasible"] is True
    assert len(r["roster"]) == 3
    assert {x["cid"] for x in r["roster"]} == {"1", "2", "3"}


def test_no_change_when_best_is_least_loaded():
    a = _c("1", 0, 100.0)   # best ORAZ najlżejszy
    d = _c("3", 2, 50.0)
    r = dispatch_pipeline._compute_loadaware_shadow([a, d], [a, d], [a])
    assert r["la_roster_cid"] == "1" and r["changed_roster"] is False
    assert r["changed_feasible"] is False


def test_tie_break_by_score():
    a = _c("1", 1, 10.0)
    b = _c("2", 1, 90.0)   # równy bag → wygrywa wyższy score
    r = dispatch_pipeline._compute_loadaware_shadow([a, b], [a, b], [a])
    assert r["la_roster_cid"] == "2"
