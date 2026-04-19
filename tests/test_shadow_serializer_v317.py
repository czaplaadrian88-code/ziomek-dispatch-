"""V3.17 Etap A — shadow_dispatcher plan dict serializer propagates per-stop timeline.

Weryfikuje że `_serialize_candidate` + inline best w `_serialize_result` dodają
`per_order_delivery_times`, `predicted_delivered_at`, `pickup_at` do plan dict.
Manual stdlib runner — pytest not installed on server.
"""
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2 import shadow_dispatcher
from dispatch_v2.route_simulator_v2 import RoutePlanV2


def _mk_plan(with_times=True):
    t_pu1 = datetime(2026, 4, 19, 15, 22, tzinfo=timezone.utc)
    t_dr1 = datetime(2026, 4, 19, 15, 31, tzinfo=timezone.utc)
    t_pu2 = datetime(2026, 4, 19, 15, 38, tzinfo=timezone.utc)
    t_dr2 = datetime(2026, 4, 19, 15, 45, tzinfo=timezone.utc)
    return RoutePlanV2(
        sequence=["O1", "O2"],
        predicted_delivered_at={"O1": t_dr1, "O2": t_dr2},
        pickup_at={"O1": t_pu1, "O2": t_pu2},
        total_duration_min=23.0,
        strategy="bruteforce",
        sla_violations=0,
        osrm_fallback_used=False,
        per_order_delivery_times=({"O1": 9.0, "O2": 7.0} if with_times else None),
    )


def _mk_candidate(plan):
    return SimpleNamespace(
        courier_id=123,
        name="Test",
        score=50.0,
        plan=plan,
        feasibility_verdict="feasible",
        feasibility_reason="ok",
        best_effort=False,
        metrics={},
    )


def test_serialize_candidate_includes_timeline_fields():
    cand = _mk_candidate(_mk_plan(with_times=True))
    out = shadow_dispatcher._serialize_candidate(cand)
    assert out["plan"] is not None, "plan must be serialized"
    p = out["plan"]
    assert p["per_order_delivery_times"] == {"O1": 9.0, "O2": 7.0}, f"got {p.get('per_order_delivery_times')}"
    assert p["predicted_delivered_at"] is not None
    assert "O1" in p["predicted_delivered_at"]
    assert p["pickup_at"] is not None
    assert p["pickup_at"]["O1"].startswith("2026-04-19T15:22"), f"got {p['pickup_at']['O1']}"
    assert p["pickup_at"]["O2"].startswith("2026-04-19T15:38")


def test_serialize_candidate_none_per_order_delivery_times_compact():
    """Backward-compat: brak per_order_delivery_times → None, no crash, other fields OK."""
    cand = _mk_candidate(_mk_plan(with_times=False))
    out = shadow_dispatcher._serialize_candidate(cand)
    assert out["plan"] is not None
    assert out["plan"]["per_order_delivery_times"] is None
    assert out["plan"]["predicted_delivered_at"] is not None
    assert out["plan"]["pickup_at"] is not None


def test_serialize_candidate_no_plan_no_crash():
    cand = SimpleNamespace(
        courier_id=1, name="X", score=0.0, plan=None,
        feasibility_verdict="infeasible", feasibility_reason="no", best_effort=False,
        metrics={},
    )
    out = shadow_dispatcher._serialize_candidate(cand)
    assert out["plan"] is None


def test_serialize_dt_map_utc_isoformat():
    t = datetime(2026, 4, 19, 15, 22, tzinfo=timezone.utc)
    out = shadow_dispatcher._serialize_dt_map({"A": t, "B": None})
    assert out == {"A": "2026-04-19T15:22:00+00:00"}, f"got {out}"


def test_serialize_dt_map_empty_none():
    assert shadow_dispatcher._serialize_dt_map({}) is None
    assert shadow_dispatcher._serialize_dt_map(None) is None


def test_serialize_dt_map_naive_datetime_assumed_utc():
    t = datetime(2026, 4, 19, 15, 22)
    out = shadow_dispatcher._serialize_dt_map({"A": t})
    assert out["A"].endswith("+00:00"), f"naive should become UTC, got {out['A']}"


def main():
    tests = [
        ('serialize_candidate_includes_timeline_fields', test_serialize_candidate_includes_timeline_fields),
        ('serialize_candidate_none_per_order_delivery_times_compact', test_serialize_candidate_none_per_order_delivery_times_compact),
        ('serialize_candidate_no_plan_no_crash', test_serialize_candidate_no_plan_no_crash),
        ('serialize_dt_map_utc_isoformat', test_serialize_dt_map_utc_isoformat),
        ('serialize_dt_map_empty_none', test_serialize_dt_map_empty_none),
        ('serialize_dt_map_naive_datetime_assumed_utc', test_serialize_dt_map_naive_datetime_assumed_utc),
    ]
    print('=' * 60)
    print('V3.17 Etap A: shadow_dispatcher serializer timeline fields')
    print('=' * 60)
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f'  PASS {name}')
            passed += 1
        except AssertionError as e:
            print(f'  FAIL {name}: {e}')
            failed.append(name)
        except Exception as e:
            print(f'  FAIL {name}: UNEXPECTED {type(e).__name__}: {e}')
            failed.append(name)
    print('=' * 60)
    print(f'{passed}/{len(tests)} PASS')
    if failed:
        print(f'FAILED: {failed}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
