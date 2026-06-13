"""Test reguły prune GC ground_truth (B6, 2026-06-13) — find_artifacts.

Lock: usuwaj TYLKO status-only (bez picked_up_at/delivered_at) dla zleceń TERMINALNYCH;
ZACHOWUJ wpisy z faktem GPS (kalibracja), nie-terminalne (w toku), sieroty.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dispatch_v2.observability import ground_truth_gc as gc  # noqa: E402


def _oids(arts):
    return sorted(a[0] for a in arts)


def test_prunes_status_only_for_terminal_incl_reassign():
    gt = {
        "A": {"courier_id": "370", "last_status_code": 3},                 # dojazd-only, delivered → prune
        "B": {"courier_id": "370", "last_status_code": 4},                 # odbior-only, reassign → prune
    }
    orders = {
        "A": {"courier_id": "370", "status": "delivered"},
        "B": {"courier_id": "530", "status": "delivered"},                 # reassign 370->530
    }
    assert _oids(gc.find_artifacts(gt, orders)) == ["A", "B"]


def test_keeps_fact_bearing_entry():
    """Wpis z picked_up_at / delivered_at ZOSTAJE (kalibracja), nawet dla delivered."""
    gt = {"P": {"courier_id": "370", "picked_up_at": 123},
          "D": {"courier_id": "370", "delivered_at": 456}}
    orders = {"P": {"courier_id": "530", "status": "delivered"},
              "D": {"courier_id": "370", "status": "delivered"}}
    assert gc.find_artifacts(gt, orders) == []


def test_keeps_nonterminal_in_progress():
    """Status-only dla zlecenia W TOKU (assigned/picked_up) ZOSTAJE."""
    gt = {"C": {"courier_id": "370", "last_status_code": 3}}
    orders = {"C": {"courier_id": "370", "status": "assigned"}}
    assert gc.find_artifacts(gt, orders) == []


def test_keeps_orphan_no_order():
    """Brak ordera w state → ZOSTAW (to domena GPS_ORPHAN shadow, nie GC)."""
    gt = {"Z": {"courier_id": "370", "last_status_code": 3}}
    assert gc.find_artifacts(gt, {}) == []


if __name__ == "__main__":
    fails = 0
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            try:
                _f(); print(f"  PASS  {_n}")
            except AssertionError as e:
                fails += 1; print(f"  FAIL  {_n}: {e}")
    print("ALL PASS" if not fails else f"{fails} FAIL")
    sys.exit(1 if fails else 0)
