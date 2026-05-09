"""V3.28 (2026-05-09) — render-side commit priority dla Telegram trasa.

FAZA 1 testy: _resolve_pickup_at + _route_lines_v2 + _build_timeline_section
preferują czas_kuriera_warsaw (commit) przed plan.pickup_at (computed ETA)
dla committed bag-orders. Tilde marker `~HH:MM` dla "eta" source, plain HH:MM
dla "commit" source.

Bug context: order 471744 today (2026-05-09): panel commit 13:05 vs render 13:17
(+12 min divergence). 24% propozycji magnitude 10-20 min. Wszystkie z greedy
fallback po V3.27.4 reject (bag>=2: 34-86% reject rate).

Manual stdlib runner — pytest not installed na serwerze.
"""
import sys
from datetime import datetime, timezone

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2 import telegram_approver as ta
from dispatch_v2 import common


def test_resolve_pickup_at_commit_priority():
    """Bag-order z czas_kuriera_warsaw → source=commit, plan.pickup_at ignored."""
    pickup_at = {"471744": "2026-05-09T11:17:29.700000+00:00"}  # plan ETA: 13:17 Warsaw
    bag_context_map = {
        "471744": {
            "order_id": "471744",
            "restaurant": "Grill Kebab",
            "czas_kuriera_warsaw": "2026-05-09T11:05:00+00:00",  # commit: 13:05 Warsaw
            "czas_kuriera_hhmm": "13:05",
        }
    }
    decision = {"order_id": "999", "restaurant": "Other"}

    dt, source = ta._resolve_pickup_at("471744", pickup_at, bag_context_map, decision)
    assert source == "commit", f"expected commit, got {source}"
    assert dt is not None
    # Verify dt = 13:05 Warsaw (commit), NOT 13:17 (plan eta)
    assert dt.hour == 11 and dt.minute == 5, f"expected 11:05 UTC (=13:05 Warsaw), got {dt}"


def test_resolve_pickup_at_eta_fallback_no_commit():
    """Bag-order BEZ czas_kuriera_warsaw → source=eta z plan.pickup_at."""
    pickup_at = {"NEW123": "2026-05-09T11:30:00+00:00"}  # plan ETA: 13:30 Warsaw
    bag_context_map = {}
    decision = {"order_id": "NEW123", "restaurant": "X"}

    dt, source = ta._resolve_pickup_at("NEW123", pickup_at, bag_context_map, decision)
    assert source == "eta", f"expected eta, got {source}"
    assert dt is not None
    assert dt.hour == 11 and dt.minute == 30


def test_resolve_pickup_at_decision_commit():
    """Current order z czas_kuriera_warsaw na decision (re-propose edge) → commit."""
    pickup_at = {"NEW123": "2026-05-09T11:30:00+00:00"}  # plan ETA: 13:30
    bag_context_map = {}
    decision = {
        "order_id": "NEW123",
        "czas_kuriera_warsaw": "2026-05-09T11:25:00+00:00",  # commit: 13:25
    }

    dt, source = ta._resolve_pickup_at("NEW123", pickup_at, bag_context_map, decision)
    assert source == "commit"
    assert dt.hour == 11 and dt.minute == 25, f"expected 11:25 UTC (commit wins over plan), got {dt}"


def test_resolve_pickup_at_no_data_returns_none():
    """Brak commit + brak plan → (None, "none")."""
    dt, source = ta._resolve_pickup_at("UNKNOWN", {}, {}, {"order_id": "OTHER"})
    assert dt is None
    assert source == "none"


def test_resolve_pickup_at_flag_off_legacy_behavior(monkeypatch=None):
    """Flag OFF → ignoruje commit, używa plan.pickup_at first (legacy)."""
    saved = common.ENABLE_V3274_RENDER_PICKUP_COMMIT_PRIORITY
    try:
        common.ENABLE_V3274_RENDER_PICKUP_COMMIT_PRIORITY = False
        pickup_at = {"471744": "2026-05-09T11:17:29.700000+00:00"}
        bag_context_map = {
            "471744": {"czas_kuriera_warsaw": "2026-05-09T11:05:00+00:00"}
        }
        decision = {"order_id": "OTHER"}
        dt, source = ta._resolve_pickup_at("471744", pickup_at, bag_context_map, decision)
        assert source == "eta", f"flag OFF expected eta legacy, got {source}"
        assert dt.minute == 17, f"flag OFF expected plan ETA 13:17, got {dt}"
    finally:
        common.ENABLE_V3274_RENDER_PICKUP_COMMIT_PRIORITY = saved


def test_route_lines_v2_renders_commit_for_bag_order():
    """End-to-end: _route_lines_v2 dla 471744 fixture renderuje 13:05 NIE ~13:17."""
    decision = {
        "order_id": "999_NEW",
        "restaurant": "New Pizza",
        "delivery_address": "Some Street 1",
    }
    best = {
        "pos_source": "no_gps",
        "effective_start_at": "2026-05-09T11:00:00+00:00",  # 13:00 Warsaw
        "bag_context": [
            {
                "order_id": "471744",
                "restaurant": "Grill Kebab",
                "delivery_address": "Plażowa 15/8",
                "czas_kuriera_warsaw": "2026-05-09T11:05:00+00:00",  # 13:05 commit
                "czas_kuriera_hhmm": "13:05",
            }
        ],
        "plan": {
            "sequence": ["999_NEW", "471744"],
            "pickup_at": {
                "999_NEW": "2026-05-09T11:00:00+00:00",  # 13:00 ETA
                "471744": "2026-05-09T11:17:29.700000+00:00",  # 13:17 ETA — should be hidden
            },
            "predicted_delivered_at": {
                "999_NEW": "2026-05-09T11:10:00+00:00",
                "471744": "2026-05-09T11:30:00+00:00",
            },
            "strategy": "ortools_rejected_v3274",
        },
    }
    now_utc = datetime(2026, 5, 9, 10, 25, 0, tzinfo=timezone.utc)
    lines = ta._route_lines_v2(decision, best, now_utc)
    text = "\n".join(lines)

    # Must show commit time 13:05 for 471744 (not ~13:17)
    assert "🍕 13:05 — Grill Kebab" in text, f"missing commit render: {text}"
    # Must NOT show ~13:17 (eta version)
    assert "~13:17" not in text, f"unexpected eta render leaking: {text}"
    # Current new order (no commit) renders with tilde
    assert "~13:00" in text, f"new order should be ~ETA: {text}"


def test_route_lines_v2_eta_when_no_commit_in_bag():
    """Bag-order BEZ czas_kuriera_warsaw → ~ETA fallback render."""
    decision = {
        "order_id": "NEW_999",
        "restaurant": "Restauracja",
        "delivery_address": "Adres 1",
    }
    best = {
        "pos_source": "no_gps",
        "effective_start_at": "2026-05-09T11:00:00+00:00",
        "bag_context": [
            {
                "order_id": "BAG_456",
                "restaurant": "Bag Pizza",
                "delivery_address": "Some Where 2",
                # NO czas_kuriera_warsaw — uncommitted bag-order
            }
        ],
        "plan": {
            "sequence": ["BAG_456", "NEW_999"],
            "pickup_at": {
                "BAG_456": "2026-05-09T11:05:00+00:00",
                "NEW_999": "2026-05-09T11:15:00+00:00",
            },
            "predicted_delivered_at": {
                "BAG_456": "2026-05-09T11:20:00+00:00",
                "NEW_999": "2026-05-09T11:25:00+00:00",
            },
            "strategy": "ortools",
        },
    }
    now_utc = datetime(2026, 5, 9, 10, 30, 0, tzinfo=timezone.utc)
    lines = ta._route_lines_v2(decision, best, now_utc)
    text = "\n".join(lines)

    # Both pickups should be ~ETA (no commits)
    assert "~13:05 — Bag Pizza" in text, f"bag pickup should be ~ETA: {text}"
    assert "~13:15" in text, f"new pickup should be ~ETA: {text}"


def test_build_timeline_section_commit_priority():
    """_build_timeline_section: commit wygrywa nad plan.pickup_at dla bag-order."""
    decision = {
        "order_id": "NEW",
        "restaurant": "New Place",
        "delivery_address": "Drop 1",
    }
    best = {
        "bag_context": [
            {
                "order_id": "B1",
                "restaurant": "Grill",
                "delivery_address": "Drop 2",
                "czas_kuriera_warsaw": "2026-05-09T11:05:00+00:00",  # commit 13:05
                "czas_kuriera_hhmm": "13:05",
            }
        ],
        "plan": {
            "sequence": ["B1", "NEW"],
            "pickup_at": {
                "B1": "2026-05-09T11:17:00+00:00",  # plan ETA 13:17
                "NEW": "2026-05-09T11:25:00+00:00",
            },
            "predicted_delivered_at": {
                "B1": "2026-05-09T11:30:00+00:00",
                "NEW": "2026-05-09T11:40:00+00:00",
            },
        },
    }
    out = ta._build_timeline_section(decision, best)
    # Commit 13:05 must appear, plan ETA 13:17 must NOT (hidden by commit-priority)
    assert "13:05 🍕 pickup Grill" in out, f"commit render missing: {out}"
    assert "13:17" not in out, f"plan ETA should be hidden: {out}"


def test_resolve_pickup_at_invalid_iso_graceful():
    """Malformed ISO → returns None, no exception."""
    dt, source = ta._resolve_pickup_at(
        "X",
        {"X": "not-an-iso-string"},
        {},
        {"order_id": "OTHER"},
    )
    assert dt is None
    assert source == "none"


def main():
    tests = [
        ("commit_priority", test_resolve_pickup_at_commit_priority),
        ("eta_fallback_no_commit", test_resolve_pickup_at_eta_fallback_no_commit),
        ("decision_commit", test_resolve_pickup_at_decision_commit),
        ("no_data_returns_none", test_resolve_pickup_at_no_data_returns_none),
        ("flag_off_legacy", test_resolve_pickup_at_flag_off_legacy_behavior),
        ("route_lines_v2_commit_render", test_route_lines_v2_renders_commit_for_bag_order),
        ("route_lines_v2_eta_no_commit", test_route_lines_v2_eta_when_no_commit_in_bag),
        ("timeline_section_commit", test_build_timeline_section_commit_priority),
        ("invalid_iso_graceful", test_resolve_pickup_at_invalid_iso_graceful),
    ]
    print("=" * 60)
    print("V3.28 FAZA 1: render pickup source priority (commit vs eta)")
    print("=" * 60)
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {name}: {e}")
            failed.append(name)
        except Exception as e:
            print(f"  FAIL {name}: UNEXPECTED {type(e).__name__}: {e}")
            failed.append(name)
    print("=" * 60)
    print(f"{passed}/{len(tests)} PASS")
    if failed:
        print(f"FAILED: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
