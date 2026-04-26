"""V3.27.1 sesja 2 + sesja 3 fix Bug 1 — Pre-proposal czas_kuriera recheck tests.

Mechanizm 3 hybrydowy (Adrian sesja 2 spec):
- SKIP fetch dla świeżych assignments (<10 min)
- SKIP fetch dla świeżego cache (<5 min)
- FORCE fetch + parallel ThreadPoolExecutor + emit synth event przy zmianie
- ZERO max bag limit (Bartek peak bag=8-11 expected)

V3.27.1 sesja 3 fix Bug 1 — Lekcja #28 zaaplikowana:
- Mock raw response z REAL panel API schema (id, status_id, czas_kuriera HH:MM,
  czas_odbioru_timestamp anchor)
- Helper wywołuje REAL panel_client.normalize_order — łapie schema mismatch BEFORE
  deploy
- Edge case: status_id=7 (delivered) → normalize returns None → helper returns
  (None, None) → caller skip emit, zachowuje cached state value

Tests (10 cases):
1. test_recheck_disabled_baseline — flag=False → return cached, no fetch
2. test_recheck_skip_fresh_assignment — assigned <10 min → skip
3. test_recheck_skip_fresh_cache — last recheck <5 min → skip
4. test_recheck_force_fetch_old_emits_event — force fetch + emit z REAL schema
5. test_recheck_parallel_no_bag_limit — bag=10 → 10 parallel fetchy
6. test_recheck_fetch_failure_defensive — fetch raises → fallback cached, log WARN
7. test_recheck_cache_eviction — entries >1h evicted, fresh preserved
8. test_recheck_timeout_2s — verify timeout=2 passed do panel_client
9. test_recheck_no_change_no_emit — fresh == cached → no synth event
10. test_recheck_normalize_returns_none_skip_emit — NEW V3.27.1 sesja 3:
    status=7 (delivered) → normalize None → helper (None, None) → skip emit
"""
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2 import common, dispatch_pipeline, panel_client
from dispatch_v2.route_simulator_v2 import OrderSim


def _make_order_sim(oid, assigned_min_ago=20, ck_warsaw="2026-04-26T17:00:00+02:00",
                     courier_id="100"):
    """Build OrderSim z dynamic attrs jak _bag_dict_to_ordersim setuje."""
    sim = OrderSim(
        order_id=oid,
        pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17),
        picked_up_at=None,
        status="assigned",
        pickup_ready_at=None,
    )
    if assigned_min_ago is not None:
        assigned_at_dt = datetime.now(timezone.utc) - timedelta(minutes=assigned_min_ago)
        sim.assigned_at = assigned_at_dt.isoformat()
    else:
        sim.assigned_at = None
    sim.czas_kuriera_warsaw = ck_warsaw
    sim.courier_id = courier_id
    return sim


def _make_real_raw_response(oid, czas_kuriera_hhmm="17:30",
                              status_id=3, czas_odbioru_timestamp="2026-04-26 16:30:00"):
    """V3.27.1 sesja 3: REAL panel API schema (NIE fake `czas_kuriera_warsaw` klucz).

    Lekcja #28: real raw response używa `czas_kuriera` (HH:MM string).
    `czas_kuriera_warsaw` ISO jest computed downstream przez normalize_order.
    """
    return {
        "id": oid,
        "id_status_zamowienia": status_id,
        "czas_kuriera": czas_kuriera_hhmm,
        "czas_odbioru_timestamp": czas_odbioru_timestamp,
    }


def _reset_state():
    """Reset module state przed każdym testem."""
    dispatch_pipeline._v327_pre_recheck_last_seen.clear()
    dispatch_pipeline._v327_pre_recheck_call_counter = 0


def _set_flag(value: bool):
    common.ENABLE_V327_PRE_PROPOSAL_RECHECK = value


# ═══════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════

def test_recheck_disabled_baseline():
    """Flag=False → helper short-circuit, NO fetch attempts, return cached."""
    _reset_state()
    _set_flag(False)
    bag = [_make_order_sim("1001", assigned_min_ago=20, ck_warsaw="2026-04-26T17:00:00+02:00")]
    now = datetime.now(timezone.utc)

    fetch_count = [0]
    def _spy_fetch(oid, timeout=10):
        fetch_count[0] += 1
        return None
    with patch.object(panel_client, "fetch_order_details", side_effect=_spy_fetch):
        result = dispatch_pipeline.get_fresh_czas_kuriera_for_bag(bag, now)

    assert fetch_count[0] == 0, f"flag=False MUST NOT fetch, got {fetch_count[0]} calls"
    assert result == {"1001": "2026-04-26T17:00:00+02:00"}, f"got {result}"


def test_recheck_skip_fresh_assignment():
    """Order assigned <10 min temu → skip fetch, return cached."""
    _reset_state()
    _set_flag(True)
    bag = [_make_order_sim("1002", assigned_min_ago=5)]  # < AGE_MIN=10
    now = datetime.now(timezone.utc)

    fetch_count = [0]
    def _spy_fetch(oid, timeout=10):
        fetch_count[0] += 1
        return None
    with patch.object(panel_client, "fetch_order_details", side_effect=_spy_fetch):
        result = dispatch_pipeline.get_fresh_czas_kuriera_for_bag(bag, now)

    assert fetch_count[0] == 0, f"fresh assignment (5 min) MUST skip fetch, got {fetch_count[0]}"
    assert "1002" in result


def test_recheck_skip_fresh_cache():
    """Order recheck <5 min temu w in-memory cache → skip fetch."""
    _reset_state()
    _set_flag(True)
    bag = [_make_order_sim("1003", assigned_min_ago=20)]
    now = datetime.now(timezone.utc)
    dispatch_pipeline._v327_pre_recheck_last_seen["1003"] = now - timedelta(minutes=1)

    fetch_count = [0]
    def _spy_fetch(oid, timeout=10):
        fetch_count[0] += 1
        return None
    with patch.object(panel_client, "fetch_order_details", side_effect=_spy_fetch):
        result = dispatch_pipeline.get_fresh_czas_kuriera_for_bag(bag, now)

    assert fetch_count[0] == 0, f"fresh cache (1 min) MUST skip fetch, got {fetch_count[0]}"


def test_recheck_force_fetch_old_emits_event():
    """V3.27.1 sesja 3 fix Bug 1: REAL schema flow.

    Mock fetch_order_details boundary z REAL raw response (czas_kuriera HH:MM).
    Helper wywołuje REAL normalize_order → ISO Warsaw + HH:MM (oba pola).
    Emit synth event z source=pre_proposal_recheck + new_ck_hhmm populated.
    """
    _reset_state()
    _set_flag(True)
    # Cached value rożny od fresh fetch — wymusza emit
    bag = [_make_order_sim("1004", assigned_min_ago=20, ck_warsaw="2026-04-26T17:00:00+02:00")]
    now = datetime.now(timezone.utc)

    # REAL raw response (panel schema): czas_kuriera HH:MM, NIE fake ISO
    raw = _make_real_raw_response("1004", czas_kuriera_hhmm="17:30")

    fetch_count = [0]
    def _spy_fetch(oid, timeout=10):
        fetch_count[0] += 1
        return raw

    emit_calls = []
    def _spy_emit(*args, **kwargs):
        emit_calls.append(kwargs)
    apply_calls = []
    def _spy_apply(event):
        apply_calls.append(event)

    # MOCK panel HTTP boundary (fetch_order_details), ale REAL normalize_order
    with patch.object(panel_client, "fetch_order_details", side_effect=_spy_fetch), \
         patch("dispatch_v2.event_bus.emit", side_effect=_spy_emit), \
         patch("dispatch_v2.state_machine.update_from_event", side_effect=_spy_apply):
        result = dispatch_pipeline.get_fresh_czas_kuriera_for_bag(bag, now)

    assert fetch_count[0] == 1, f"old order MUST fetch, got {fetch_count[0]}"
    # Result: ISO Warsaw computed by normalize_order
    assert result["1004"] == "2026-04-26T17:30:00+02:00", \
        f"fresh ISO MUST be returned (computed via normalize), got {result}"
    assert len(emit_calls) == 1, f"MUST emit 1 synth event, got {len(emit_calls)}"
    payload = emit_calls[0]["payload"]
    assert payload["source"] == "pre_proposal_recheck"
    # KEY FIX V3.27.1 sesja 3: oba pola w payload
    assert payload["new_ck_iso"] == "2026-04-26T17:30:00+02:00", \
        f"payload MUST have ISO new_ck_iso, got {payload.get('new_ck_iso')}"
    assert payload["new_ck_hhmm"] == "17:30", \
        f"payload MUST have HH:MM new_ck_hhmm (state_machine sanity), got {payload.get('new_ck_hhmm')}"
    assert "_PRE_RECHECK_" in emit_calls[0]["event_id"]
    assert len(apply_calls) == 1, "MUST call state_machine.update_from_event"


def test_recheck_parallel_no_bag_limit():
    """Bag=10 → 10 parallel fetchy via ThreadPoolExecutor (NO max ceiling, Bartek peak)."""
    _reset_state()
    _set_flag(True)
    bag = [_make_order_sim(f"200{i}", assigned_min_ago=20) for i in range(10)]
    now = datetime.now(timezone.utc)

    fetch_count = [0]
    def _spy_fetch(oid, timeout=10):
        fetch_count[0] += 1
        return None  # fetch fail → helper returns (None, None) → skip emit
    with patch.object(panel_client, "fetch_order_details", side_effect=_spy_fetch):
        result = dispatch_pipeline.get_fresh_czas_kuriera_for_bag(bag, now)

    assert fetch_count[0] == 10, f"bag=10 MUST fetch 10 (no ceiling), got {fetch_count[0]}"
    assert len(result) == 10


def test_recheck_fetch_failure_defensive():
    """Panel fetch raises → fallback to cached, log WARN, no crash."""
    _reset_state()
    _set_flag(True)
    bag = [_make_order_sim("3001", assigned_min_ago=20, ck_warsaw="cached_value")]
    now = datetime.now(timezone.utc)

    def _failing_fetch(oid, timeout=10):
        raise ConnectionError("simulated panel down")
    with patch.object(panel_client, "fetch_order_details", side_effect=_failing_fetch):
        result = dispatch_pipeline.get_fresh_czas_kuriera_for_bag(bag, now)

    assert result["3001"] == "cached_value", f"fetch fail MUST fallback to cached, got {result}"


def test_recheck_cache_eviction():
    """Entries starsze niż EVICT_AGE_SEC=3600 evicted, fresh preserved."""
    _reset_state()
    _set_flag(True)
    now = datetime.now(timezone.utc)
    dispatch_pipeline._v327_pre_recheck_last_seen["old_oid"] = now - timedelta(hours=2)
    dispatch_pipeline._v327_pre_recheck_last_seen["fresh_oid"] = now - timedelta(minutes=1)

    evicted = dispatch_pipeline._v327_evict_old_pre_recheck_entries(now)

    assert evicted == 1, f"MUST evict 1 (old_oid), got {evicted}"
    assert "old_oid" not in dispatch_pipeline._v327_pre_recheck_last_seen
    assert "fresh_oid" in dispatch_pipeline._v327_pre_recheck_last_seen


def test_recheck_timeout_2s():
    """Verify że nasze wywołanie używa timeout=2.0 (NIE default 10), per Adrian Blocker 3 Opcja A."""
    _reset_state()
    _set_flag(True)
    bag = [_make_order_sim("4001", assigned_min_ago=20)]
    now = datetime.now(timezone.utc)

    captured_timeouts = []
    def _spy_fetch(oid, timeout=10):
        captured_timeouts.append(timeout)
        return None
    with patch.object(panel_client, "fetch_order_details", side_effect=_spy_fetch):
        dispatch_pipeline.get_fresh_czas_kuriera_for_bag(bag, now)

    assert len(captured_timeouts) == 1, f"MUST fetch once, got {captured_timeouts}"
    assert captured_timeouts[0] == 2, \
        f"MUST use timeout=2 (V327_PRE_PROPOSAL_RECHECK_FETCH_TIMEOUT_SEC), got {captured_timeouts[0]}"


def test_recheck_no_change_no_emit():
    """V3.27.1 sesja 3 fix: Fresh ISO == cached ISO → no synth event (avoid spam).

    Cached "2026-04-26T17:00:00+02:00" + fresh raw czas_kuriera="17:00"
    + czas_odbioru_timestamp anchor "2026-04-26 16:30:00" → normalize zwraca
    "2026-04-26T17:00:00+02:00" identyczne → no emit.
    """
    _reset_state()
    _set_flag(True)
    bag = [_make_order_sim("5001", assigned_min_ago=20, ck_warsaw="2026-04-26T17:00:00+02:00")]
    now = datetime.now(timezone.utc)

    # REAL raw z czas_kuriera="17:00" — normalize compute ISO identyczne z cached
    raw = _make_real_raw_response("5001", czas_kuriera_hhmm="17:00")

    def _spy_fetch(oid, timeout=10):
        return raw

    emit_calls = []
    def _spy_emit(*args, **kwargs):
        emit_calls.append(kwargs)

    with patch.object(panel_client, "fetch_order_details", side_effect=_spy_fetch), \
         patch("dispatch_v2.event_bus.emit", side_effect=_spy_emit), \
         patch("dispatch_v2.state_machine.update_from_event"):
        result = dispatch_pipeline.get_fresh_czas_kuriera_for_bag(bag, now)

    assert len(emit_calls) == 0, f"no change MUST NOT emit event, got {len(emit_calls)} calls"
    assert result["5001"] == "2026-04-26T17:00:00+02:00"


def test_recheck_normalize_returns_none_skip_emit():
    """V3.27.1 sesja 3 NEW edge case: status_id=7 (delivered) → normalize None →
    helper (None, None) → caller skip emit, zachowuje cached state value.

    Lekcja #28 satisfied: integration test catches schema-level edge case
    (IGNORED_STATUSES filter w normalize_order) BEFORE deploy.
    """
    _reset_state()
    _set_flag(True)
    bag = [_make_order_sim("6001", assigned_min_ago=20, ck_warsaw="cached_iso")]
    now = datetime.now(timezone.utc)

    # Order delivered w trakcie cycle (status=7 → IGNORED_STATUSES)
    raw_delivered = _make_real_raw_response("6001", czas_kuriera_hhmm="17:30", status_id=7)

    def _spy_fetch(oid, timeout=10):
        return raw_delivered

    emit_calls = []
    def _spy_emit(*args, **kwargs):
        emit_calls.append(kwargs)

    with patch.object(panel_client, "fetch_order_details", side_effect=_spy_fetch), \
         patch("dispatch_v2.event_bus.emit", side_effect=_spy_emit), \
         patch("dispatch_v2.state_machine.update_from_event"):
        result = dispatch_pipeline.get_fresh_czas_kuriera_for_bag(bag, now)

    # normalize_order zwraca None dla status_id=7 → helper (None, None) → skip emit
    assert len(emit_calls) == 0, \
        f"status delivered MUST skip emit (normalize None), got {len(emit_calls)} emits"
    assert result["6001"] == "cached_iso", \
        f"defensive: zachowuje cached value gdy normalize None, got {result}"


def main():
    _orig_flag = common.ENABLE_V327_PRE_PROPOSAL_RECHECK
    tests = [
        ('recheck_disabled_baseline', test_recheck_disabled_baseline),
        ('recheck_skip_fresh_assignment', test_recheck_skip_fresh_assignment),
        ('recheck_skip_fresh_cache', test_recheck_skip_fresh_cache),
        ('recheck_force_fetch_old_emits_event', test_recheck_force_fetch_old_emits_event),
        ('recheck_parallel_no_bag_limit', test_recheck_parallel_no_bag_limit),
        ('recheck_fetch_failure_defensive', test_recheck_fetch_failure_defensive),
        ('recheck_cache_eviction', test_recheck_cache_eviction),
        ('recheck_timeout_2s', test_recheck_timeout_2s),
        ('recheck_no_change_no_emit', test_recheck_no_change_no_emit),
        ('recheck_normalize_returns_none_skip_emit', test_recheck_normalize_returns_none_skip_emit),
    ]
    print('=' * 60)
    print('V3.27.1 sesja 2 + sesja 3 fix Bug 1 — Pre-proposal recheck tests')
    print('=' * 60)
    passed = 0
    failed = []
    try:
        for name, fn in tests:
            try:
                fn()
                print(f'  ✅ {name}')
                passed += 1
            except AssertionError as e:
                print(f'  ❌ {name}: {e}')
                failed.append(name)
            except Exception as e:
                import traceback
                print(f'  ❌ {name}: UNEXPECTED {type(e).__name__}: {e}')
                traceback.print_exc()
                failed.append(name)
    finally:
        common.ENABLE_V327_PRE_PROPOSAL_RECHECK = _orig_flag
        _reset_state()
    print('=' * 60)
    print(f'{passed}/{len(tests)} PASS')
    if failed:
        print(f'FAILED: {failed}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
