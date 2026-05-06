"""Z2 fix 2026-05-07 — PARSER_STUCK set-comparison detection tests.

Coverage:
- _build_entry stores order_ids as frozenset
- set_stuck=True (sets identical 5 cycle) + motion → REAL alert fires
- set_stuck=False (sets differ — rotation underneath) + motion → SUPPRESS
- set_stuck=None (legacy fallback, missing order_ids) + motion → legacy behavior
- order_ids absent in parsed → entry order_ids=None (no crash)
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import parser_health  # noqa: E402


@pytest.fixture(autouse=True)
def _block_real_telegram(monkeypatch):
    # Z2 fix 2026-05-07: prevent real send_admin_alert calls during tests.
    # Mirrors mock_telegram fixture w test_parser_health_layer3.py.
    from dispatch_v2 import telegram_utils
    monkeypatch.setattr(telegram_utils, "send_admin_alert", lambda text: True)


def _make_monitor(tmpdir: str):
    """Fresh monitor z tmp state. Reset module-level globals."""
    state_path = Path(tmpdir) / "ph.json"
    parser_health._monitor = None
    monitor = parser_health.ParserHealthMonitor(state_path=state_path, enabled=True)
    return monitor


def _record_with_set(monitor, cycle: int, count: int, order_ids: list, n_new=0, n_delivered=0, n_assigned=0):
    """Helper: record_tick z parsed.order_ids."""
    cycle_stats = {
        "cycle": cycle,
        "orders_in_panel": count,
        "new": n_new,
        "delivered": n_delivered,
        "ignored": 0,
        "errors": 0,
    }
    parsed = {"order_ids": order_ids, "assigned_ids": ["x"] * n_assigned}
    return monitor.record_tick(cycle_stats, parsed)


# ─── Test 1: _build_entry stores frozenset(order_ids) ──────────────────
def test_build_entry_stores_order_ids_as_frozenset():
    with tempfile.TemporaryDirectory() as td:
        m = _make_monitor(td)
        cycle_stats = {"cycle": 1, "orders_in_panel": 3, "new": 0, "delivered": 0}
        parsed = {"order_ids": ["100", "101", "102"], "assigned_ids": []}
        entry = m._build_entry(cycle_stats, parsed)
        assert "order_ids" in entry
        assert entry["order_ids"] == frozenset(["100", "101", "102"])
        assert isinstance(entry["order_ids"], frozenset)


# ─── Test 2: parsed=None → order_ids=None ───────────────────────────────
def test_build_entry_no_parsed():
    with tempfile.TemporaryDirectory() as td:
        m = _make_monitor(td)
        cycle_stats = {"cycle": 1, "orders_in_panel": 3, "new": 0, "delivered": 0}
        entry = m._build_entry(cycle_stats, None)
        assert entry["order_ids"] is None


# ─── Test 3: parsed bez order_ids → order_ids=None (no crash) ──────────
def test_build_entry_parsed_without_order_ids():
    with tempfile.TemporaryDirectory() as td:
        m = _make_monitor(td)
        cycle_stats = {"cycle": 1, "orders_in_panel": 3, "new": 0, "delivered": 0}
        parsed = {"assigned_ids": []}  # missing order_ids key
        entry = m._build_entry(cycle_stats, parsed)
        assert entry["order_ids"] is None


# ─── Test 4: set_stuck=True + motion → REAL miss alert fires ───────────
def test_alert_fires_when_set_identical_with_motion():
    with tempfile.TemporaryDirectory() as td:
        m = _make_monitor(td)
        # 5 cykli z IDENTYCZNYM order_ids set, ale motion (new+delivered) >= threshold
        # Symulujemy real parser miss: panel mówi że są deliveries i nowe, ale order_ids stuck.
        ids = ["100", "101", "102", "103", "104"]
        for i in range(5):
            alerts = _record_with_set(
                m, cycle=i, count=5, order_ids=ids,
                n_new=1, n_delivered=1, n_assigned=2 + (i % 2),  # assigned varies
            )
        # Last cycle: motion sum = 1+1+(2-2)=2 NIE wystarczy threshold=4
        # Zwiększyć motion aby przekroczyć
        for i in range(5, 10):
            alerts = _record_with_set(
                m, cycle=i, count=5, order_ids=ids,
                n_new=2, n_delivered=2, n_assigned=2 + (i % 3),
            )
        # Ostatni alert powinien być PARSER_STUCK z set_stuck=True
        parser_stuck = [a for a in alerts if a["type"] == "PARSER_STUCK"]
        assert len(parser_stuck) == 1, f"oczekiwany 1 alert PARSER_STUCK, got {len(parser_stuck)}"
        assert parser_stuck[0]["context"].get("set_stuck") is True
        assert "real parser miss confirmed" in parser_stuck[0]["message"].lower()


# ─── Test 5: set_stuck=False (rotation) + motion → SUPPRESS alert ──────
def test_no_alert_when_sets_differ_natural_rotation():
    with tempfile.TemporaryDirectory() as td:
        m = _make_monitor(td)
        # 5 cykli z TAKĄ SAMĄ wartością count=5 ale RÓŻNYM zbiorem (rotation)
        # cycle 0: {100..104}, cycle 1: {101..105}, etc. — count stały, zbiory różne
        for i in range(10):
            ids = [str(100 + i + j) for j in range(5)]  # rolling window
            alerts = _record_with_set(
                m, cycle=i, count=5, order_ids=ids,
                n_new=1, n_delivered=1, n_assigned=3,  # motion>=4 if assigned varies
            )
        # 5 last cycles: count=5 stuck, motion>=4, ALE sets differ → SUPPRESS
        parser_stuck = [a for a in alerts if a["type"] == "PARSER_STUCK"]
        assert len(parser_stuck) == 0, (
            f"NIE oczekiwany alert (rotation underneath), got {len(parser_stuck)}: "
            f"{[a['message'] for a in parser_stuck]}"
        )


# ─── Test 6: set_stuck=None + LOW motion sum (5 cykli) → NO alert ──────
def test_legacy_entries_low_motion_no_alert():
    """Sanity: legacy fallback + niska motion (sum 5 cykli < threshold) → NO alert."""
    with tempfile.TemporaryDirectory() as td:
        m = _make_monitor(td)
        # 5 cykli z motion=0 per cycle (sum=0). parsed=None → set_stuck=None fallback.
        for i in range(5):
            cycle_stats = {
                "cycle": i, "orders_in_panel": 5,
                "new": 0, "delivered": 0, "ignored": 0, "errors": 0,
            }
            alerts = m.record_tick(cycle_stats, None)
        parser_stuck = [a for a in alerts if a["type"] == "PARSER_STUCK"]
        assert len(parser_stuck) == 0, f"sum motion=0 NIE powinien alert (got {len(parser_stuck)})"


# ─── Test 7: legacy entries + high motion → fallback alert fires ───────
def test_legacy_entries_high_motion_fires_legacy_alert():
    with tempfile.TemporaryDirectory() as td:
        m = _make_monitor(td)
        # 5 cykli pre-fix (parsed bez order_ids ALE assigned_ids dostarczone)
        for i in range(5):
            cycle_stats = {
                "cycle": i, "orders_in_panel": 5,
                "new": 2, "delivered": 2, "ignored": 0, "errors": 0,
            }
            parsed = {"assigned_ids": ["x"] * (3 if i % 2 == 0 else 5)}  # varies 3/5/3/5/3
            alerts = m.record_tick(cycle_stats, parsed)
        # motion = 2+2+(5-3)=6 >=4 + set_stuck=None (parsed bez order_ids) → fallback fires
        parser_stuck = [a for a in alerts if a["type"] == "PARSER_STUCK"]
        assert len(parser_stuck) == 1, (
            f"Fallback motion-only powinien fire alert (motion=6 >=4), got {len(parser_stuck)}"
        )
        # Message powinien być w legacy format (bez "set IDENTYCZNY")
        assert "real parser miss confirmed" not in parser_stuck[0]["message"].lower()


# ─── Test 8b: REAL INCIDENT REPLAY — 02.05 pattern (zero delivered + PACKS_CATCHUP) ──
def test_alert_fires_real_incident_pattern_zero_delivered():
    """False-negative regression guard.

    Scenariusz 02.05.2026: parser stuck przez >12h, n_delivered=0 cały czas
    (deliveries NIE parsowane), motion przychodzi z PACKS_CATCHUP (assigned
    rośnie 47XXX) + n_new>0. Bez tego testu fix set-comparison mógłby tłumić
    real bug gdy delivered counter jest broken.

    Assert PARSER_STUCK fires z set_stuck=True przy n_delivered=0.
    """
    with tempfile.TemporaryDirectory() as td:
        m = _make_monitor(td)
        ids = ["470100", "470101", "470102", "470103"]  # broken parser, identyczne
        # 5 cykli STUCK_COUNT_TOLERANCE: order_ids identyczny, delivered=0,
        # n_new + assigned_variance dostarcza motion >= threshold=4
        for i in range(5):
            alerts = _record_with_set(
                m, cycle=i, count=4, order_ids=ids,
                n_new=1, n_delivered=0,         # PARSER MISS deliveries
                n_assigned=3 + (i % 3),         # PACKS_CATCHUP variance: 3,4,5,3,4 → max-min=2
            )
        # motion_total = sum_new(5) + sum_delivered(0) + assigned_var(2) = 7 >= 4
        parser_stuck = [a for a in alerts if a["type"] == "PARSER_STUCK"]
        assert len(parser_stuck) == 1, (
            f"REAL INCIDENT pattern (zero delivered + PACKS_CATCHUP motion) MUSI "
            f"odpalić alert, got {len(parser_stuck)}"
        )
        ctx = parser_stuck[0]["context"]
        assert ctx.get("set_stuck") is True, "set_stuck powinien być True (order_ids identyczny)"
        assert ctx.get("motion_delivered") == 0, "delivered=0 (parser miss)"
        assert ctx.get("motion_total") >= 4, f"motion_total {ctx.get('motion_total')} < threshold"
        assert "real parser miss confirmed" in parser_stuck[0]["message"].lower()


# ─── Test 8: set_stuck=True ALE motion=0 → NO alert (panel quiet) ──────
def test_no_alert_set_stuck_no_motion():
    with tempfile.TemporaryDirectory() as td:
        m = _make_monitor(td)
        ids = ["100", "101", "102"]
        for i in range(10):
            alerts = _record_with_set(
                m, cycle=i, count=3, order_ids=ids,
                n_new=0, n_delivered=0, n_assigned=2,  # zero motion
            )
        # set_stuck=True, ale motion=0 < threshold=4 → NO alert (panel quiet, off-peak)
        parser_stuck = [a for a in alerts if a["type"] == "PARSER_STUCK"]
        assert len(parser_stuck) == 0, "panel quiet (motion=0) NIE powinien fire alert"


# ─── Z2 fix #2 (2026-05-07): active_ids = order_ids - closed_ids ──────────
# Panel zwraca all-today's IDs w JS embedded `id: X` przez cały dzień.
# closed_ids (status 7/8/9) reprezentuje terminalne. active = order_ids - closed_ids
# = rzeczywiste live orders. Layer 2 STUCK + DELTA przełączone na active_*.
# Eliminuje false positives gdzie order_ids count plateauje wieczorem (panel design).


def _record_with_active(monitor, cycle: int, order_ids: list, closed_ids: list,
                         n_new=0, n_delivered=0, n_assigned=0):
    """Helper: record_tick z parsed.order_ids + parsed.closed_ids."""
    cycle_stats = {
        "cycle": cycle,
        "orders_in_panel": len(order_ids),
        "new": n_new,
        "delivered": n_delivered,
        "ignored": 0,
        "errors": 0,
    }
    parsed = {
        "order_ids": order_ids,
        "closed_ids": closed_ids,
        "assigned_ids": ["x"] * n_assigned,
    }
    return monitor.record_tick(cycle_stats, parsed)


# ─── Test 9: late-evening panel design — order_ids stuck, active shrinks → NO alert ──
def test_no_alert_late_evening_panel_keeps_delivered_in_order_ids():
    """Real-world scenario 06.05.2026 wieczór: ostatnie zlecenie wpadło, panel
    pokazuje 5 IDów przez kolejne cykle, ale po każdym delivery jeden ID
    przechodzi do closed_ids. order_ids count stuck na 5, active shrinks 5→4→3→2→1.
    Pre-fix: alert pali 17/dzień. Post-fix: zero alertów.
    """
    with tempfile.TemporaryDirectory() as td:
        m = _make_monitor(td)
        all_ids = ["500", "501", "502", "503", "504"]
        # 5 cykli: ten sam order_ids set (panel keeps), closed_ids rośnie sequentially
        for i in range(5):
            closed = all_ids[:i + 1]  # cycle 0: [500], cycle 1: [500,501], ...
            alerts = _record_with_active(
                m, cycle=i, order_ids=all_ids, closed_ids=closed,
                n_new=0, n_delivered=1, n_assigned=2,  # delivery motion każdy cykl
            )
        # Pre-fix: order_ids stuck 5 cycle + motion → alert fires (false positive).
        # Post-fix: active spada 4→3→2→1→0 → set_stuck=False → no alert.
        parser_stuck = [a for a in alerts if a["type"] == "PARSER_STUCK"]
        assert len(parser_stuck) == 0, (
            f"late-evening panel (closed_ids grows) NIE powinien fire stuck alert, "
            f"got {len(parser_stuck)}: {[a['message'] for a in parser_stuck]}"
        )


# ─── Test 10: real parser miss — active_ids identical mimo motion → ALERT ──
def test_alert_fires_active_set_stuck_real_miss():
    """Real parser miss scenario: active_ids set identical przez 5 cykli + motion.
    Każdy cykl: order_ids = closed_ids + same 4 active IDs (parser miss live orders).
    motion sum spełnia threshold. Set IDENTYCZNY → alert pali.
    """
    with tempfile.TemporaryDirectory() as td:
        m = _make_monitor(td)
        active_ids = ["600", "601", "602", "603"]  # 4 live, parser miss
        for i in range(5):
            # Każdy cykl: te same 4 active + rosnący closed (delivery motion)
            closed = [f"7{i:02d}{j}" for j in range(2)]  # 2 nowe closed per cycle
            order_ids = active_ids + closed
            alerts = _record_with_active(
                m, cycle=i, order_ids=order_ids, closed_ids=closed,
                n_new=1, n_delivered=2, n_assigned=3,  # motion: 1+2+0 = 3 + assigned_var
            )
        parser_stuck = [a for a in alerts if a["type"] == "PARSER_STUCK"]
        assert len(parser_stuck) == 1, (
            f"Real parser miss (active stuck + motion) MUSI fire alert, got {len(parser_stuck)}"
        )
        ctx = parser_stuck[0]["context"]
        assert ctx.get("set_stuck") is True
        assert ctx.get("stuck_value") == 4, f"active_orders=4 expected, got {ctx.get('stuck_value')}"


# ─── Test 11: DELTA_SPIKE używa active_orders, nie orders_in_panel ─────────
def test_delta_spike_uses_active_not_order_ids():
    """22:01 UTC scenario: panel daily rollover usuwa delivered z order_ids.
    Pre-fix: orders_in_panel 462→228 (-50%) → alert pali (false positive,
    bo to natural rollover). Post-fix: active był już niski przed rollover
    (większość delivered), więc delta na active jest mała → no alert.
    """
    with tempfile.TemporaryDirectory() as td:
        m = _make_monitor(td)
        # 5 cykli pre-rollover: order_ids=200 (180 closed + 20 active)
        for i in range(5):
            order_ids = [str(j) for j in range(200)]
            closed = [str(j) for j in range(180)]  # 180 delivered, 20 active
            _record_with_active(m, cycle=i, order_ids=order_ids, closed_ids=closed)
        # Cycle 6: panel rollover → order_ids=20 (just active), closed=[]
        # active orders steady at 20 — żadnej delty.
        alerts = _record_with_active(
            m, cycle=6, order_ids=[str(j) for j in range(180, 200)], closed_ids=[],
        )
        parser_delta = [a for a in alerts if a["type"] == "PARSER_DELTA_SPIKE"]
        assert len(parser_delta) == 0, (
            f"daily rollover (closed_ids cleanup) NIE powinien fire delta alert "
            f"gdy active_orders steady, got {parser_delta}"
        )


# ─── Test 12: brak closed_ids w parsed → fallback do order_ids (backward compat) ──
def test_active_falls_back_to_order_ids_when_no_closed():
    """Defense-in-depth: gdy parser nie dostarczył closed_ids (legacy / shadow path),
    active = order_ids (zachowanie identyczne jak pre-fix). Zero behavior change
    dla testów które nie deklarują closed_ids w parsed dict.
    """
    with tempfile.TemporaryDirectory() as td:
        m = _make_monitor(td)
        cycle_stats = {"cycle": 1, "orders_in_panel": 3, "new": 0, "delivered": 0}
        parsed = {"order_ids": ["100", "101", "102"], "assigned_ids": []}  # NO closed_ids
        entry = m._build_entry(cycle_stats, parsed)
        assert entry["order_ids"] == frozenset(["100", "101", "102"])
        assert entry["active_ids"] == frozenset(["100", "101", "102"]), \
            "active fallback do order_ids gdy brak closed_ids"
        assert entry["active_orders"] == 3


# ─── Runner ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [
        ("build_entry_stores_order_ids_as_frozenset", test_build_entry_stores_order_ids_as_frozenset),
        ("build_entry_no_parsed", test_build_entry_no_parsed),
        ("build_entry_parsed_without_order_ids", test_build_entry_parsed_without_order_ids),
        ("alert_fires_when_set_identical_with_motion", test_alert_fires_when_set_identical_with_motion),
        ("no_alert_when_sets_differ_natural_rotation", test_no_alert_when_sets_differ_natural_rotation),
        ("legacy_entries_low_motion_no_alert", test_legacy_entries_low_motion_no_alert),
        ("legacy_entries_high_motion_fires_legacy_alert", test_legacy_entries_high_motion_fires_legacy_alert),
        ("alert_fires_real_incident_pattern_zero_delivered", test_alert_fires_real_incident_pattern_zero_delivered),
        ("no_alert_set_stuck_no_motion", test_no_alert_set_stuck_no_motion),
        ("no_alert_late_evening_panel_keeps_delivered_in_order_ids", test_no_alert_late_evening_panel_keeps_delivered_in_order_ids),
        ("alert_fires_active_set_stuck_real_miss", test_alert_fires_active_set_stuck_real_miss),
        ("delta_spike_uses_active_not_order_ids", test_delta_spike_uses_active_not_order_ids),
        ("active_falls_back_to_order_ids_when_no_closed", test_active_falls_back_to_order_ids_when_no_closed),
    ]
    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{'=' * 50}\nResult: {passed}/{len(tests)} PASS, {failed} FAIL")
    sys.exit(0 if failed == 0 else 1)
