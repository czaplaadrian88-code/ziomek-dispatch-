"""V3.28 #32 — Observability serializer regression guard (2026-05-11).

Sprint #32 audit ujawnił że P3-D4 (`r6_picked_up_delta_reject`) i P3-D5
(`r1_corridor_spread_mult`) były emitowane w pipeline `c.metrics` ALE drop'owane
przez allowlist `_serialize_candidate` + `_serialize_result.best` inline.

Empiryczny audit 11.05 17:32 Warsaw na shadow_decisions.jsonl: 127 records
post-P3-D4 deploy → 0 zawierało te metryki. Konsekwencja: 7-day FAZA 3 decision
tree window startujący 17:11 byłby observability-blind.

Lekcja #80 powtórzona: "Every new metric w pipeline → downstream consumer
checklist (LOCATION A + LOCATION B + tests)". Tests P3-D4/D5 sprawdzały tylko
source code presence (`assert "key" in src`), NIGDY downstream presence w
serialized output.

Te testy guard'ują future regressions — assertion że NEW keys faktycznie
trafiają do shadow_decisions.jsonl format.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

# Custom-runner pattern (Mailek-style — Mailek nie ma pytest setup ale dispatch_v2 ma)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2.dispatch_pipeline import Candidate, PipelineResult
from dispatch_v2.shadow_dispatcher import _serialize_candidate, _serialize_result


def _make_candidate(cid="123", metrics=None, score=10.0):
    """Minimal Candidate fixture."""
    return Candidate(
        courier_id=cid,
        name="Test Courier",
        score=score,
        feasibility_verdict="MAYBE",
        feasibility_reason="test",
        plan=None,
        metrics=metrics or {},
        best_effort=False,
    )


def _make_result(best, alts=None):
    """Minimal PipelineResult fixture."""
    return PipelineResult(
        order_id="999000",
        verdict="PROPOSE",
        reason="test feasible=2",
        best=best,
        candidates=[best] + (alts or []),
        pickup_ready_at=datetime(2026, 5, 11, 17, 30, tzinfo=timezone.utc),
        restaurant="Test Restaurant",
        delivery_address="Test ul. 1",
    )


# ---- LOCATION A: _serialize_candidate (alternatives) ----

def test_p3d4_r6_picked_up_delta_reject_in_alternatives():
    """REGRESSION GUARD: alternatives via _serialize_candidate include r6_picked_up_delta_reject."""
    alt = _make_candidate(cid="999", metrics={"r6_picked_up_delta_reject": True})
    out = _serialize_candidate(alt)
    assert "r6_picked_up_delta_reject" in out, "key missing from _serialize_candidate output"
    assert out["r6_picked_up_delta_reject"] is True, "value not propagated"


def test_p3d4_default_false_propagated():
    """Default False (no reject path) musi też trafić do output, NIE jako missing/None."""
    alt = _make_candidate(metrics={"r6_picked_up_delta_reject": False})
    out = _serialize_candidate(alt)
    assert out["r6_picked_up_delta_reject"] is False, "False default lost"


def test_p3d5_r1_corridor_spread_mult_in_alternatives():
    """REGRESSION GUARD: alternatives include r1_corridor_spread_mult."""
    alt = _make_candidate(metrics={"r1_corridor_spread_mult": 1.578})
    out = _serialize_candidate(alt)
    assert "r1_corridor_spread_mult" in out, "key missing from _serialize_candidate"
    assert out["r1_corridor_spread_mult"] == 1.578, "mult value not propagated"


def test_p3d5_baseline_1_0_propagated():
    """1.0 baseline (no spread mult) musi przejść do output."""
    alt = _make_candidate(metrics={"r1_corridor_spread_mult": 1.0})
    out = _serialize_candidate(alt)
    assert out["r1_corridor_spread_mult"] == 1.0


# ---- LOCATION B: _serialize_result.best inline ----

def test_p3d4_p3d5_in_best_serialization():
    """REGRESSION GUARD: best inline serialization (LOCATION B) include both keys.

    To krytyczny test bo best ma OSOBNĄ inline serialization (nie używa
    _serialize_candidate). Audit 11.05 ujawnił że oba sites trzeba updated."""
    best = _make_candidate(metrics={
        "r6_picked_up_delta_reject": True,
        "r1_corridor_spread_mult": 1.875,
    })
    result = _make_result(best)
    out = _serialize_result(result, event_id="evt_test", latency_ms=123.4)

    assert out["best"] is not None
    assert "r6_picked_up_delta_reject" in out["best"], "P3-D4 missing from best (LOCATION B)"
    assert out["best"]["r6_picked_up_delta_reject"] is True
    assert "r1_corridor_spread_mult" in out["best"], "P3-D5 missing from best (LOCATION B)"
    assert out["best"]["r1_corridor_spread_mult"] == 1.875


def test_alternatives_also_include_new_keys():
    """End-to-end via _serialize_result.alternatives (list comp uses _serialize_candidate)."""
    best = _make_candidate(cid="111", metrics={"r6_picked_up_delta_reject": False, "r1_corridor_spread_mult": 1.0})
    alt1 = _make_candidate(cid="222", metrics={"r6_picked_up_delta_reject": True, "r1_corridor_spread_mult": 1.5})
    alt2 = _make_candidate(cid="333", metrics={"r6_picked_up_delta_reject": False, "r1_corridor_spread_mult": 2.0})
    result = _make_result(best, alts=[alt1, alt2])
    out = _serialize_result(result, event_id="evt", latency_ms=100.0)

    assert len(out["alternatives"]) == 2
    for a in out["alternatives"]:
        assert "r6_picked_up_delta_reject" in a
        assert "r1_corridor_spread_mult" in a
    assert out["alternatives"][0]["r6_picked_up_delta_reject"] is True
    assert out["alternatives"][0]["r1_corridor_spread_mult"] == 1.5
    assert out["alternatives"][1]["r1_corridor_spread_mult"] == 2.0


def test_missing_metrics_gracefully_none():
    """Backward compat: stary candidate bez NEW keys → output ma None (NIE crash)."""
    legacy = _make_candidate(metrics={})  # pre-P3-D4/D5 candidate
    out = _serialize_candidate(legacy)
    # Klucze muszą być obecne (=schema stable), ale wartość = None (m.get default)
    assert "r6_picked_up_delta_reject" in out
    assert "r1_corridor_spread_mult" in out
    assert out["r6_picked_up_delta_reject"] is None
    assert out["r1_corridor_spread_mult"] is None


# ---- Custom-runner entry (Mailek-style) ----

if __name__ == "__main__":
    tests = [
        test_p3d4_r6_picked_up_delta_reject_in_alternatives,
        test_p3d4_default_false_propagated,
        test_p3d5_r1_corridor_spread_mult_in_alternatives,
        test_p3d5_baseline_1_0_propagated,
        test_p3d4_p3d5_in_best_serialization,
        test_alternatives_also_include_new_keys,
        test_missing_metrics_gracefully_none,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(tests)} PASS, {failed} FAIL")
    sys.exit(0 if failed == 0 else 1)
