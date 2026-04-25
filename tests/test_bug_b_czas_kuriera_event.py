"""Bug B regression guard (sprint 2026-04-25):
event_bus.py EVENT_TYPES allowlist musi zawierać "CZAS_KURIERA_UPDATED".

Pre-fix: V3.19g1 incomplete deployment.
- state_machine.py:316 had handler for CZAS_KURIERA_UPDATED
- panel_watcher.py:1066 emitted event
- ALE event_bus.py:21 EVENT_TYPES set NIE zawierał — emit fail z ValueError
- watcher.log 2026-04-24 11:49+ pokazywał: "tick fail #1: Nieznany event_type:
  CZAS_KURIERA_UPDATED. Dozwolone: {NEW_ORDER, ORDER_READY, ...}"

Post-fix: panel czas_kuriera changes propagują do orders_state → fleet_snapshot
ma fresh czas_kuriera → plan timestamps używają rzeczywistych panel commitments.

Adrian's Bug B hipoteza dla #468404 case (Maison du cafe 10:29 panel manual)
CONFIRMED przez fix.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import event_bus  # noqa: E402


def test_czas_kuriera_updated_in_allowlist():
    assert "CZAS_KURIERA_UPDATED" in event_bus.EVENT_TYPES, (
        "CZAS_KURIERA_UPDATED missing z EVENT_TYPES — V3.19g1 incomplete deployment. "
        "panel_watcher emit'uje ten event, state_machine ma handler, ale event_bus "
        "rejects emit → orders_state stale czas_kuriera."
    )


def test_other_event_types_not_removed():
    """Sanity: dodanie new event NIE zniszczyło istniejących types."""
    required = {
        "NEW_ORDER", "ORDER_READY", "COURIER_PICKED_UP", "COURIER_DELIVERED",
        "COURIER_ASSIGNED", "COURIER_REJECTED_PROPOSAL", "ORDER_RETURNED_TO_POOL",
        "KOORDYNATOR_DEADLINE", "GPS_STALE", "PANEL_UNREACHABLE",
        "HEARTBEAT_STALL", "SHIFT_END_APPROACHING",
    }
    missing = required - event_bus.EVENT_TYPES
    assert not missing, f"Required event types removed: {missing}"


def test_event_count():
    """Sanity: 12 pre-existing + 1 new (CZAS_KURIERA_UPDATED) = 13."""
    assert len(event_bus.EVENT_TYPES) == 13, (
        f"Expected 13 event types post-fix, got {len(event_bus.EVENT_TYPES)}"
    )


if __name__ == "__main__":
    test_czas_kuriera_updated_in_allowlist()
    print("test_czas_kuriera_updated_in_allowlist: PASS")
    test_other_event_types_not_removed()
    print("test_other_event_types_not_removed: PASS")
    test_event_count()
    print("test_event_count: PASS")
    print("ALL 3/3 PASS")
