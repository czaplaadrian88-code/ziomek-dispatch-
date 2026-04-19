"""Regresja V3.16 no_gps empty bag demotion (#467189 proposal selection fix).

Bug: Mateusz O (cid=413, no_gps, bag=0, score=+53.31) pokazywany jako BEST
mimo że bag-kurierzy (Gabriel cid=179, bag=3, score=-96) są preferowani
przez koordynatora. PANEL_OVERRIDE rate 19.6% (18/92 last 1h45min).

Fix: `_demote_blind_empty(feasible)` reorderuje feasible list — informed
first, blind+empty last. Guard "all blind": jeśli żadnego informed → nie
degraduj.
"""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common, dispatch_pipeline  # noqa: E402


class FakeCand:
    """Minimal Candidate-like object for unit testing demote helper."""
    def __init__(self, cid, score, pos_source, bag_size, bundle_dev=None):
        self.courier_id = cid
        self.score = score
        self.metrics = {
            "pos_source": pos_source,
            "r6_bag_size": bag_size,
        }
        if bundle_dev is not None:
            self.metrics["bundle_level3_dev"] = bundle_dev


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  ✅ {label}")
            results["pass"] += 1
        else:
            print(f"  ❌ {label}  {detail}")
            results["fail"] += 1

    # Baseline flag state
    importlib.reload(common)
    importlib.reload(dispatch_pipeline)
    assert common.ENABLE_NO_GPS_EMPTY_DEMOTE is True

    # ---------- TEST 1: regression #467189 Mateusz demoted ----------
    print("\n=== test 1: regression #467189 — Mateusz O (no_gps+empty) demoted ===")
    feasible = [
        FakeCand("413", 53.31, "no_gps", 0),                   # Mateusz O (BEST sorted)
        FakeCand("179", -96.06, "last_assigned_pickup", 3),    # Gabriel
        FakeCand("520", -154.96, "last_assigned_pickup", 3),   # Michał Rom
        FakeCand("508", -216.08, "gps", 3),                    # Michał Li
        FakeCand("509", -218.91, "last_picked_up_delivery", 3),  # Dariusz M
    ]
    result = dispatch_pipeline._demote_blind_empty(feasible, order_id="467189")
    expect("top-1 post-demote = Gabriel (179)", result[0].courier_id == "179",
           f"got {result[0].courier_id}")
    expect("Mateusz O (413) moved to last", result[-1].courier_id == "413")
    expect("informed candidates zachowują względny porządek",
           [c.courier_id for c in result[:4]] == ["179", "520", "508", "509"])

    # ---------- TEST 2: no_gps empty not top when informed alt exists ----------
    print("\n=== test 2: no_gps+empty demoted when any informed exists ===")
    for scenario_name, alt_pos, alt_bag in [
        ("GPS+empty bag", "gps", 0),
        ("GPS+bag", "gps", 2),
        ("last_assigned_pickup+bag", "last_assigned_pickup", 1),
        ("last_picked_up_delivery+bag", "last_picked_up_delivery", 3),
        ("last_delivered", "last_delivered", 0),
    ]:
        feasible = [
            FakeCand("BLIND", 80, "no_gps", 0),
            FakeCand("ALT", -10, alt_pos, alt_bag),
        ]
        result = dispatch_pipeline._demote_blind_empty(feasible, order_id="T")
        expect(f"[{scenario_name}] informed ALT promoted to top-1",
               result[0].courier_id == "ALT")

    # ---------- TEST 3: all blind empty → no demotion ----------
    print("\n=== test 3: wszyscy no_gps+empty → zostaw bez zmian (empty shift) ===")
    feasible = [
        FakeCand("A", 80, "no_gps", 0),
        FakeCand("B", 70, "no_gps", 0),
        FakeCand("C", 60, "pre_shift", 0),
    ]
    result = dispatch_pipeline._demote_blind_empty(feasible, order_id="T")
    expect("top-1 preserved (A)", result[0].courier_id == "A")
    expect("count preserved", len(result) == 3)
    expect("order preserved [A,B,C]",
           [c.courier_id for c in result] == ["A", "B", "C"])

    # ---------- TEST 4: no_gps with bag NOT demoted ----------
    print("\n=== test 4: no_gps+bag>0 NOT demoted (tylko bag=0 target) ===")
    feasible = [
        FakeCand("WITH_BAG", 50, "no_gps", 2),  # no_gps ale ma bag
        FakeCand("GPS_EMPTY", 30, "gps", 0),
    ]
    result = dispatch_pipeline._demote_blind_empty(feasible, order_id="T")
    expect("no_gps+bag zostaje top-1", result[0].courier_id == "WITH_BAG")

    # ---------- TEST 5: GPS+empty NOT demoted (tylko no_gps+empty) ----------
    print("\n=== test 5: GPS+empty bag NOT demoted ===")
    feasible = [
        FakeCand("GPS_E", 70, "gps", 0),
        FakeCand("BAG", -50, "last_assigned_pickup", 3),
    ]
    result = dispatch_pipeline._demote_blind_empty(feasible, order_id="T")
    expect("GPS+empty zostaje top-1 (not blind)", result[0].courier_id == "GPS_E")

    # ---------- TEST 6: flag False disables demotion ----------
    print("\n=== test 6: flag False → legacy behavior (no demotion) ===")
    orig = common.ENABLE_NO_GPS_EMPTY_DEMOTE
    common.ENABLE_NO_GPS_EMPTY_DEMOTE = False
    try:
        feasible = [
            FakeCand("BLIND", 80, "no_gps", 0),
            FakeCand("INFORMED", -10, "gps", 2),
        ]
        result = dispatch_pipeline._demote_blind_empty(feasible, order_id="T")
        expect("flag=False: blind zostaje top-1", result[0].courier_id == "BLIND")
    finally:
        common.ENABLE_NO_GPS_EMPTY_DEMOTE = orig

    # ---------- TEST 7: stable reorder preserves informed order ----------
    print("\n=== test 7: stable reorder zachowuje informed by-score ===")
    feasible = [
        FakeCand("BLIND", 90, "no_gps", 0),
        FakeCand("I1", 60, "gps", 1),
        FakeCand("I2", 40, "last_assigned_pickup", 2),
        FakeCand("I3", 20, "last_picked_up_delivery", 3),
    ]
    result = dispatch_pipeline._demote_blind_empty(feasible, order_id="T")
    expect("informed order preserved: I1→I2→I3",
           [c.courier_id for c in result[:3]] == ["I1", "I2", "I3"])
    expect("blind last", result[3].courier_id == "BLIND")

    # ---------- TEST 8: empty list → empty list ----------
    print("\n=== test 8: empty feasible list → empty ===")
    result = dispatch_pipeline._demote_blind_empty([], order_id="T")
    expect("empty → empty", result == [])

    # ---------- TEST 9: single blind candidate → preserved ----------
    print("\n=== test 9: single blind candidate (no alt) → preserved ===")
    feasible = [FakeCand("ONLY", 50, "no_gps", 0)]
    result = dispatch_pipeline._demote_blind_empty(feasible, order_id="T")
    expect("single blind preserved", len(result) == 1 and result[0].courier_id == "ONLY")

    # ---------- TEST 10: mixed blind + other (non-informed non-blind) ----------
    print("\n=== test 10: mixed blind + non-informed non-blind ===")
    # Scenario: 'other' categories (brak pos_source, neither blind nor informed category)
    feasible = [
        FakeCand("BLIND", 80, "no_gps", 0),
        FakeCand("OTHER", 30, "unknown_src", 2),  # not in BLIND nor INFORMED
    ]
    result = dispatch_pipeline._demote_blind_empty(feasible, order_id="T")
    # OTHER nie jest informed więc NIE promuje, blind zostaje (brak informed)
    expect("no informed → blind zostaje top-1", result[0].courier_id == "BLIND")

    # ---------- TEST 11: pre_shift+empty also demoted ----------
    print("\n=== test 11: pre_shift+empty also demoted (blind source) ===")
    feasible = [
        FakeCand("PRE", 70, "pre_shift", 0),
        FakeCand("INF", -20, "gps", 2),
    ]
    result = dispatch_pipeline._demote_blind_empty(feasible, order_id="T")
    expect("pre_shift+empty demoted, informed promoted",
           result[0].courier_id == "INF")

    # ---------- TEST 12: integration smoke — helpers importable ----------
    print("\n=== test 12: integration — helpers module-level ===")
    expect("_is_blind_empty_cand callable",
           callable(dispatch_pipeline._is_blind_empty_cand))
    expect("_is_informed_cand callable",
           callable(dispatch_pipeline._is_informed_cand))
    expect("_demote_blind_empty callable",
           callable(dispatch_pipeline._demote_blind_empty))
    expect("BLIND_POS_SOURCES includes no_gps",
           "no_gps" in dispatch_pipeline.BLIND_POS_SOURCES)
    expect("INFORMED_POS_SOURCES includes gps",
           "gps" in dispatch_pipeline.INFORMED_POS_SOURCES)

    # ---------- FINAL ----------
    total = results["pass"] + results["fail"]
    print()
    print("=" * 60)
    print(f"PROPOSAL_SELECTION V3.16: {results['pass']}/{total} PASS")
    print("=" * 60)
    sys.exit(0 if results["fail"] == 0 else 1)


if __name__ == "__main__":
    main()
