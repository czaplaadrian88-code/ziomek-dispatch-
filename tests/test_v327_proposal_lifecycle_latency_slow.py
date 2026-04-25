"""V3.27 latency parallel test (Lekcja #24 — full lifecycle, NIE single call).

@pytest.mark.slow equivalent — naming z _slow suffix indicates osobny suite.
NIE blokuje normal test runs. Run explicit: python3 tests/test_v327_proposal_lifecycle_latency_slow.py

Pre-fix sequential 200ms × 10 candidates = 2000ms (post Big-Bang flip regression).
Post-V3.27 parallel ThreadPoolExecutor 10 workers, time_limit=200 ZACHOWANY.
Goal: p95 < 500ms wall time per proposal.

Mock fleet 10 candidates (mix bag=0/1/2/3), peak hour traffic mult=1.2.
20 iterations of full assess_order → measure latency → assert p95 < 500ms.
"""
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import dispatch_pipeline as DP  # noqa: E402
from dispatch_v2 import common as C  # noqa: E402

UTC = timezone.utc


class _MockCS:
    """Minimal CourierSnapshot duck-type."""
    def __init__(self, cid, pos, bag, name, pos_source="gps", pos_age_min=1.0):
        self.cid = cid
        self.pos = pos
        self.bag = bag
        self.name = name
        self.pos_source = pos_source
        self.pos_age_min = pos_age_min
        self.shift_start_min = 0
        self.shift_end_min = 600  # 10h
        self.tier_bag = "std"
        self.tier_cap_override = None


def _mock_fleet_10():
    """10 candidates mix bag sizes: 4×bag=0, 3×bag=1, 2×bag=2, 1×bag=3."""
    fleet = {}
    base = (53.13, 23.16)
    # bag=0
    for i in range(4):
        fleet[f"100{i}"] = _MockCS(
            cid=f"100{i}", pos=(base[0] + 0.005 * i, base[1] + 0.005 * i),
            bag=[], name=f"Empty{i}",
        )
    # bag=1 (one assigned, no picked_up)
    for i in range(3):
        fleet[f"200{i}"] = _MockCS(
            cid=f"200{i}", pos=(base[0] - 0.005 * i, base[1] - 0.005 * i),
            bag=[{
                "order_id": f"o100{i}",
                "status": "assigned",
                "pickup_coords": (53.135, 23.17),
                "delivery_coords": (53.125, 23.155),
                "pickup_ready_at": datetime(2026, 4, 25, 14, 5, tzinfo=UTC),
                "restaurant": "Test1",
                "delivery_address": "Sienkiewicza 5",
                "delivery_city": "Białystok",
            }],
            name=f"Bag1_{i}",
        )
    # bag=2 (one picked_up, one assigned)
    for i in range(2):
        fleet[f"300{i}"] = _MockCS(
            cid=f"300{i}", pos=(base[0] + 0.01 * i, base[1] - 0.005),
            bag=[
                {
                    "order_id": f"o200{i}a",
                    "status": "picked_up",
                    "pickup_coords": (53.135, 23.17),
                    "delivery_coords": (53.13, 23.155),
                    "picked_up_at": datetime(2026, 4, 25, 13, 50, tzinfo=UTC),
                    "restaurant": "Test1",
                    "delivery_address": "Mickiewicza 5",
                    "delivery_city": "Białystok",
                },
                {
                    "order_id": f"o200{i}b",
                    "status": "assigned",
                    "pickup_coords": (53.14, 23.18),
                    "delivery_coords": (53.12, 23.16),
                    "pickup_ready_at": datetime(2026, 4, 25, 14, 10, tzinfo=UTC),
                    "restaurant": "Test2",
                    "delivery_address": "Bojary 5",
                    "delivery_city": "Białystok",
                },
            ],
            name=f"Bag2_{i}",
        )
    # bag=3 (mix)
    fleet["4000"] = _MockCS(
        cid="4000", pos=(base[0] - 0.01, base[1]),
        bag=[
            {
                "order_id": "o300a",
                "status": "picked_up",
                "pickup_coords": (53.135, 23.17),
                "delivery_coords": (53.13, 23.155),
                "picked_up_at": datetime(2026, 4, 25, 13, 50, tzinfo=UTC),
                "restaurant": "Test1",
                "delivery_address": "Sienkiewicza 5",
                "delivery_city": "Białystok",
            },
            {
                "order_id": "o300b",
                "status": "assigned",
                "pickup_coords": (53.14, 23.18),
                "delivery_coords": (53.12, 23.16),
                "pickup_ready_at": datetime(2026, 4, 25, 14, 10, tzinfo=UTC),
                "restaurant": "Test2",
                "delivery_address": "Bojary 5",
                "delivery_city": "Białystok",
            },
            {
                "order_id": "o300c",
                "status": "assigned",
                "pickup_coords": (53.15, 23.19),
                "delivery_coords": (53.11, 23.17),
                "pickup_ready_at": datetime(2026, 4, 25, 14, 15, tzinfo=UTC),
                "restaurant": "Test3",
                "delivery_address": "Centrum 1",
                "delivery_city": "Białystok",
            },
        ],
        name="Bag3",
    )
    return fleet


def _mock_order_event():
    """Order event peak hour Sobota 14:00 UTC = 16:00 Warsaw → mult=1.2."""
    return {
        "order_id": "TEST_ORDER",
        "restaurant": "Mock Restaurant",
        "delivery_address": "Centrum 5",
        "delivery_city": "Białystok",
        "pickup_coords": (53.135, 23.17),
        "delivery_coords": (53.13, 23.16),
        "pickup_at_warsaw": "2026-04-25T16:10:00",
    }


def _mock_osrm_route(_a, _b):
    """Fast mock OSRM — no real HTTP calls. Returns reasonable distance."""
    return {
        "duration_s": 300.0,
        "duration_min": 5.0 * 1.2,  # peak mult applied
        "distance_m": 2000,
        "distance_km": 2.0,
        "osrm_fallback": False,
        "traffic_multiplier": 1.2,
    }


def _mock_osrm_table(origins, destinations):
    """Fast mock OSRM table — N×N matrix."""
    return [
        [
            {
                "duration_s": 300.0,
                "duration_min": 5.0 * 1.2,
                "distance_m": 2000,
                "distance_km": 2.0,
                "osrm_fallback": False,
            }
            for _ in destinations
        ]
        for _ in origins
    ]


def test_proposal_lifecycle_under_500ms_p95():
    """V3.27 Lekcja #24: full assess_order p95 < 500ms.

    Mock fleet 10 candidates, peak hour, 20 iter.
    """
    fleet = _mock_fleet_10()
    order = _mock_order_event()
    now = datetime(2026, 4, 25, 14, 0, tzinfo=UTC)  # 16:00 Warsaw peak

    latencies = []
    with patch("dispatch_v2.osrm_client.route", side_effect=_mock_osrm_route):
        with patch("dispatch_v2.osrm_client.table", side_effect=_mock_osrm_table):
            for _ in range(20):
                t0 = time.perf_counter()
                result = DP.assess_order(order, fleet, now=now)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                latencies.append(elapsed_ms)
                # sanity check: result is PipelineResult
                assert result is not None
                assert hasattr(result, "verdict")

    latencies.sort()
    p50 = statistics.median(latencies)
    p95 = latencies[int(len(latencies) * 0.95)]
    p99_max = latencies[-1]
    avg = statistics.mean(latencies)

    print(f"Latency stats (n={len(latencies)}): p50={p50:.1f}ms, p95={p95:.1f}ms, p99/max={p99_max:.1f}ms, avg={avg:.1f}ms")

    # V3.27 target: p95 < 500ms (Adrian's spec 6.5)
    assert p95 < 500, f"p95 {p95:.1f}ms exceeds 500ms target — parallel improvement insufficient"


def test_no_race_conditions_repeated_runs():
    """V3.27: 50 runs, no exception, all return valid result.

    Verifies thread-safety: OSRM cache RLock, OR-Tools per-call isolation.
    """
    fleet = _mock_fleet_10()
    order = _mock_order_event()
    now = datetime(2026, 4, 25, 14, 0, tzinfo=UTC)

    success_count = 0
    with patch("dispatch_v2.osrm_client.route", side_effect=_mock_osrm_route):
        with patch("dispatch_v2.osrm_client.table", side_effect=_mock_osrm_table):
            for _ in range(50):
                try:
                    result = DP.assess_order(order, fleet, now=now)
                    if result is not None:
                        success_count += 1
                except Exception as e:
                    raise AssertionError(f"Race condition or exception in run: {type(e).__name__}: {e}")

    assert success_count == 50, f"Expected 50 successful runs, got {success_count}"


if __name__ == "__main__":
    test_proposal_lifecycle_under_500ms_p95()
    print("test_proposal_lifecycle_under_500ms_p95: PASS")
    test_no_race_conditions_repeated_runs()
    print("test_no_race_conditions_repeated_runs: PASS")
    print("ALL 2/2 PASS")
