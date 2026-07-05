"""S2/O2 — testy narzędzia `tools/o2_k2_pick_parity` (pomiar dla FLIPMASTERA).

Golden: syntetyczne decyzje z RĘCZNIE znanym wynikiem — flip picku gdy kotwice
dają różne liczby, parytet gdy równe, INCONCLUSIVE poniżej progu, pominięcie
decyzji bez pokrycia kotwic (nie zgadujemy). Klucz = REALNY
`_best_effort_sort_key` (podmiana termu sla — indeks przypięty w
`test_o2_k2_best_effort_parity`).
"""
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2.tools import o2_k2_pick_parity as PP  # noqa: E402


def _cand(cid, *, now_b=0, ready_b=0, score=50.0, anchors=True):
    c = {
        "courier_id": cid, "score": score, "pos_source": "gps",
        "r6_per_order_violations": [], "r6_picked_up_violations": [],
        "plan": {"sla_violations": now_b, "total_duration_min": 10.0},
    }
    if anchors:
        c["sla_anchor_source"] = {
            "now_breach_oids": ["x"] * now_b,
            "ready_breach_oids": ["y"] * ready_b,
        }
    return c


def _dec(oid, cands, reason="best_effort (0 feasible)"):
    return {"ts": "2026-07-06T12:00:00+00:00", "order_id": oid,
            "reason": reason, "best": cands[0], "alternatives": cands[1:]}


def test_anchor_divergence_flips_pick_and_direction_counted():
    """A: now=1/ready=0, B: now=0/ready=1 → OFF wybiera B, ON wybiera A
    (ręcznie); kierunek-K2 ok (ON-pick ready-breach 0 ≤ OFF-pick 1)."""
    d = _dec("o1", [_cand("A", now_b=1, ready_b=0), _cand("B", now_b=0, ready_b=1)])
    s = PP.compute([d] * 10, min_n=10)
    assert s["n_best_effort"] == 10 and s["changed"] == 10, s
    assert s["verdict"] == "MEASURED"
    assert s["direction_ok"] == 10, s
    case = s["cases"][0]
    assert case["pick_off"] == "B" and case["pick_on"] == "A", case


def test_equal_anchors_full_parity():
    d = _dec("o2", [_cand("A", now_b=1, ready_b=1, score=60),
                    _cand("B", now_b=0, ready_b=0, score=50)])
    s = PP.compute([d] * 10, min_n=10)
    assert s["changed"] == 0 and s["changed_pct"] == 0.0, s


def test_below_min_n_is_inconclusive():
    d = _dec("o3", [_cand("A"), _cand("B", now_b=1, ready_b=0)])
    s = PP.compute([d] * 3, min_n=10)
    assert s["verdict"] == "INCONCLUSIVE" and s["n_best_effort"] == 3, s


def test_missing_anchor_coverage_skipped_not_guessed():
    d = _dec("o4", [_cand("A"), _cand("B", anchors=False)])
    s = PP.compute([d], min_n=1)
    assert s["n_best_effort"] == 0 and s["skipped_no_anchor_coverage"] == 1, s


def test_non_best_effort_decisions_ignored():
    d = _dec("o5", [_cand("A"), _cand("B")], reason="feasible=5 best=500")
    s = PP.compute([d] * 20, min_n=10)
    assert s["n_best_effort"] == 0, s
