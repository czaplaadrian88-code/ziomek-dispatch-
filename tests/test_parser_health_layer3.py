"""V3.28 PARSER-RESILIENCE Layer 4 — Pytest suite parser_health Layer 2+3 + Layer 4 integration.

Konsoliduje 14 tests z mojej walidacji Layer 2/3 jako pytest suite + 2 nowe Layer 4 tests:
- TEST 15: Integration end-to-end (install + bootstrap + ticks + alert verification)
- TEST 16: Health endpoint contract (HTTP GET /health/parser schema verification)

Run:
    cd /root/.openclaw/workspace/scripts/dispatch_v2
    /root/.openclaw/venvs/dispatch/bin/python3 -m pytest tests/test_parser_health_layer3.py -v

Deploy: copy do /root/.openclaw/workspace/scripts/dispatch_v2/tests/test_parser_health_layer3.py
"""
import os
import sys
import json
import time
import tempfile
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
import importlib.util

sys.path.insert(0, "/root/.openclaw/workspace/scripts")


def _load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ph():
    """Layer 2 module (prefer /tmp/ pre-deploy, fallback dispatch_v2)."""
    paths = [
        "/tmp/v328_layer2_parser_health.py",
        "/root/.openclaw/workspace/scripts/dispatch_v2/parser_health.py",
    ]
    for p in paths:
        if os.path.exists(p):
            return _load_module(p, "parser_health")
    pytest.skip("parser_health module not found")


@pytest.fixture(scope="module")
def l3():
    """Layer 3 extension."""
    paths = [
        "/tmp/v328_layer3_parser_health_extension.py",
        "/root/.openclaw/workspace/scripts/dispatch_v2/parser_health_layer3.py",
    ]
    for p in paths:
        if os.path.exists(p):
            return _load_module(p, "parser_health_layer3")
    pytest.skip("parser_health_layer3 module not found")


@pytest.fixture(autouse=True)
def reset_singleton(ph):
    """Reset ParserHealthMonitor singleton + mock telegram between tests."""
    ph.reset_for_test()
    yield
    ph.reset_for_test()


@pytest.fixture
def mock_telegram(monkeypatch):
    """Capture send_admin_alert calls instead of real send."""
    from dispatch_v2 import telegram_utils
    sent = []
    monkeypatch.setattr(telegram_utils, "send_admin_alert", lambda text: (sent.append(text), True)[1])
    return sent


@pytest.fixture
def fresh_monitor(ph, l3, mock_telegram):
    """Fresh Layer 2 + Layer 3 monitor z unique state paths."""
    state_path = Path(tempfile.mktemp(prefix="ph_test_", suffix=".json"))
    known_path = Path(tempfile.mktemp(prefix="kw_test_", suffix=".json"))
    m = ph.ParserHealthMonitor(enabled=True, state_path=state_path)
    l3.KNOWN_IDS_WINDOW_PATH = known_path
    l3.install_layer3(m)
    m._known_ids_window = l3.KnownIdsWindow(state_path=known_path)
    m._test_state_path = state_path
    m._test_known_path = known_path
    yield m
    # Cleanup
    for p in (state_path, known_path):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


# ============================================================================
# Layer 2 backward compat (tests 1-9)
# ============================================================================

def test_01_healthy_no_alerts(fresh_monitor, mock_telegram):
    for c, n in enumerate([180, 195, 210, 225, 243], 1):
        fresh_monitor.record_tick({"cycle": c, "orders_in_panel": n}, {"assigned_ids": set(), "order_ids": []})
    assert len(mock_telegram) == 0


def test_02_stuck_alert(fresh_monitor, mock_telegram):
    """02.05 incident pattern: count stuck + panel motion (PACKS_CATCHUP fires dla 47XXXX).

    Post V3.28-LAYER2-MOTION-AWARE: alert fires tylko gdy motion present.
    Real 02.05: orders_in_panel stuck na 180 ALE assigned_ids growing (47XXXX assigned via packs path).
    """
    for c in range(1, 6):
        # Motion: delivered=1 per cycle + assigned growing 1→5 (mimics PACKS_CATCHUP for 47XXXX)
        cycle_stats = {"cycle": c, "orders_in_panel": 180, "delivered": 1}
        parsed = {"assigned_ids": set([str(i) for i in range(c)])}
        fresh_monitor.record_tick(cycle_stats, parsed)
    assert any("PARSER_STUCK" in a for a in mock_telegram)


def test_03_zero_output_alert(fresh_monitor, mock_telegram):
    for c, n in enumerate([180, 0, 0, 0], 1):
        fresh_monitor.record_tick({"cycle": c, "orders_in_panel": n}, {"assigned_ids": set(), "order_ids": []})
    assert any("PARSER_ZERO_OUTPUT" in a for a in mock_telegram)


def test_04_delta_spike_alert(fresh_monitor, mock_telegram):
    for c, n in enumerate([180, 175, 178, 180, 182, 100], 1):
        fresh_monitor.record_tick({"cycle": c, "orders_in_panel": n}, {"assigned_ids": set(), "order_ids": []})
    assert any("PARSER_DELTA_SPIKE" in a for a in mock_telegram)


def test_05_layer2_asymmetry_alert(fresh_monitor, mock_telegram):
    fresh_monitor.record_tick(
        {"cycle": 1, "orders_in_panel": 10},
        {"assigned_ids": set([str(i) for i in range(20)]), "order_ids": [str(i) for i in range(10)]},
    )
    assert any("PARSER_ASYMMETRY" in a for a in mock_telegram)


def test_06_cooldown(fresh_monitor, mock_telegram):
    """Cooldown test z motion (post-MOTION-AWARE fix). 7 cycles motion + count stuck → 1 alert + 6 suppressed."""
    for c in range(1, 8):
        # Motion: delivered=1 per cycle + assigned growing (ten sam pattern co test_02)
        cycle_stats = {"cycle": c, "orders_in_panel": 180, "delivered": 1}
        parsed = {"assigned_ids": set([str(i) for i in range(c)])}
        fresh_monitor.record_tick(cycle_stats, parsed)
    stuck_count = sum(1 for a in mock_telegram if "PARSER_STUCK" in a)
    assert stuck_count == 1, f"expected 1 alert + 6 cooldown'd, got {stuck_count}"


def test_07_disabled(ph, mock_telegram):
    m_disabled = ph.ParserHealthMonitor(enabled=False, state_path=Path(tempfile.mktemp()))
    alerts = m_disabled.record_tick({"cycle": 1, "orders_in_panel": 0}, {"assigned_ids": set()})
    assert alerts == [] and len(mock_telegram) == 0


def test_08_snapshot(fresh_monitor):
    for c, n in enumerate([180, 195, 210, 220, 243], 1):
        fresh_monitor.record_tick({"cycle": c, "orders_in_panel": n}, {"assigned_ids": set(), "order_ids": []})
    snap = fresh_monitor.get_health_snapshot()
    assert snap["status"] == "healthy"
    assert snap["cycles_recorded"] == 5


def test_09_persistence(ph, fresh_monitor):
    sp = fresh_monitor._test_state_path
    for c, n in enumerate([100, 110, 120], 1):
        fresh_monitor.record_tick({"cycle": c, "orders_in_panel": n}, {"assigned_ids": set()})
    m2 = ph.ParserHealthMonitor(enabled=True, state_path=sp)
    loaded = [c.get("orders_in_panel") for c in m2._cycles]
    assert loaded == [100, 110, 120]


# ============================================================================
# Layer 3 cross-validation (tests 10-14)
# ============================================================================

def test_10_asymmetry_detection_02may_incident(fresh_monitor, mock_telegram):
    """02.05 incident pattern reproduction — Layer 3 detection w 1 tick."""
    parsed = {
        "order_ids": ["469997", "469998"],
        "assigned_ids": set(["470053", "470055"]),
        "rest_names": {},
        "courier_packs": {},
        "closed_ids": set(),
    }
    alerts = fresh_monitor.cross_validate_parsed_dict(parsed)
    with fresh_monitor._lock:
        for a in alerts:
            fresh_monitor._maybe_send_alert(a)
    assert any("SET_ASSIGNED_ORPHAN" in a for a in mock_telegram)


def test_11_historical_known_no_false_alert(fresh_monitor, mock_telegram):
    fresh_monitor._known_ids_window.add({"470053", "470055"}, ts="2026-05-02T00:00:00+00:00")
    parsed = {
        "order_ids": ["469997", "469998"],
        "assigned_ids": set(["470053", "470055"]),
        "rest_names": {},
        "courier_packs": {},
        "closed_ids": set(),
    }
    alerts = fresh_monitor.cross_validate_parsed_dict(parsed)
    assert not any("SET_ASSIGNED_ORPHAN" in str(a) for a in alerts)


def test_12_packs_leak_alert(fresh_monitor, mock_telegram):
    parsed = {
        "order_ids": ["470001", "470002", "470003"],
        "assigned_ids": set(["470001"]),
        "rest_names": {},
        "courier_packs": {"Bartek O,": ["470002", "470099"]},
        "closed_ids": set(),
    }
    alerts = fresh_monitor.cross_validate_parsed_dict(parsed)
    with fresh_monitor._lock:
        for a in alerts:
            fresh_monitor._maybe_send_alert(a)
    assert any("PACKS_LEAK" in a for a in mock_telegram)


def test_13_window_expiration(fresh_monitor):
    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    fresh_ts = datetime.now(timezone.utc).isoformat()
    fresh_monitor._known_ids_window.add({"OLD_ID"}, ts=old_ts)
    fresh_monitor._known_ids_window.add({"FRESH_ID"}, ts=fresh_ts)
    known = fresh_monitor._known_ids_window.get_known()
    assert "OLD_ID" not in known
    assert "FRESH_ID" in known


def test_14_critical_cooldown_5min(fresh_monitor, mock_telegram):
    """Critical cooldown 5 min vs warning 30 min."""
    parsed_critical = {
        "order_ids": [],
        "assigned_ids": set([str(470100 + i) for i in range(10)]),
        "rest_names": {},
        "courier_packs": {},
        "closed_ids": set(),
    }
    # First alert
    alerts1 = fresh_monitor.cross_validate_parsed_dict(parsed_critical)
    with fresh_monitor._lock:
        for a in alerts1:
            fresh_monitor._maybe_send_alert(a)
    count_first = sum(1 for a in mock_telegram if "SET_ASSIGNED_ORPHAN" in a)

    # Simulate 5+ min passage
    fresh_monitor._last_alert_at["PARSER_SET_ASSIGNED_ORPHAN"] = time.time() - 350
    alerts2 = fresh_monitor.cross_validate_parsed_dict(parsed_critical)
    with fresh_monitor._lock:
        for a in alerts2:
            fresh_monitor._maybe_send_alert(a)
    count_second = sum(1 for a in mock_telegram if "SET_ASSIGNED_ORPHAN" in a)

    assert count_first == 1 and count_second == 2


# ============================================================================
# Layer 4 NEW tests (15-16)
# ============================================================================

def test_15_integration_end_to_end(fresh_monitor, mock_telegram, l3):
    """End-to-end: bootstrap + 5 healthy + injected anomaly + alert + state persisted."""
    # Bootstrap z synthetic data
    fresh_monitor._known_ids_window.add({"460001", "460002", "460003"})
    assert len(fresh_monitor._known_ids_window.get_known()) == 3

    # 5 healthy ticks
    for c, n in enumerate([3, 4, 5, 6, 7], 1):
        order_ids = [str(460000 + i) for i in range(1, n + 1)]
        l3.record_tick_full(
            fresh_monitor,
            {"cycle": c, "orders_in_panel": n},
            {"order_ids": order_ids, "assigned_ids": set(), "rest_names": {}, "courier_packs": {}, "closed_ids": set()},
        )
    assert len(mock_telegram) == 0, f"healthy ticks should not alert, got: {mock_telegram}"

    # Inject anomaly: assigned_ids spoza order_ids/historical
    parsed_anomaly = {
        "order_ids": [str(460000 + i) for i in range(1, 8)],
        "assigned_ids": set(["470100", "470101"]),  # spoza known
        "rest_names": {},
        "courier_packs": {},
        "closed_ids": set(),
    }
    l3.record_tick_full(
        fresh_monitor,
        {"cycle": 6, "orders_in_panel": 7},
        parsed_anomaly,
    )
    assert any("SET_ASSIGNED_ORPHAN" in a for a in mock_telegram)

    # Verify state persisted
    state_path = fresh_monitor._test_state_path
    assert state_path.exists()
    with open(state_path) as f:
        data = json.load(f)
    assert "cycles" in data
    assert len(data["cycles"]) >= 5  # at least 5 healthy + anomaly = 6


def _http_get_with_error_body(url, timeout=5):
    """urllib.request.urlopen ale czyta body NAWET gdy HTTPError. Zwraca (status, body_str)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        # 503/500 nadal mogą zwracać JSON body (defense-in-depth)
        body = e.read().decode("utf-8") if e.fp else "{}"
        return e.code, body


def test_16_health_endpoint_contract():
    """HTTP GET /health/parser → JSON schema verified, defense-in-depth tested.

    Test acceptable scenarios:
      - 200: status=healthy/degraded (parser_health module loaded + monitor active)
      - 503: status=critical/error (defense-in-depth — endpoint zwraca JSON nawet gdy module fail)
    Both must return valid JSON z required schema keys.
    """
    endpoint_mod = _load_module(
        "/tmp/v328_layer4_health_endpoint.py", "parser_health_endpoint"
    )
    # Random port — zero collision risk
    import socket
    sock = socket.socket(socket.AF_INET)
    sock.bind(("127.0.0.1", 0))
    test_port = sock.getsockname()[1]
    sock.close()

    started = endpoint_mod.start_health_endpoint(host="127.0.0.1", port=test_port)
    assert started, "Health endpoint failed to start"
    try:
        time.sleep(0.5)  # let server bind

        # Test 1: /health/parser — accept 200 OR 503 (oba returning valid JSON)
        t0 = time.time()
        status_code, body = _http_get_with_error_body(f"http://127.0.0.1:{test_port}/health/parser")
        elapsed_ms = (time.time() - t0) * 1000
        data = json.loads(body)
        assert status_code in (200, 503), f"unexpected status: {status_code}"

        # Schema verification (works dla both 200 i 503 — defense-in-depth)
        required_keys = {
            "endpoint_version", "checked_at", "status", "parser_version",
            "uptime_seconds", "known_ids_window_size", "cycles_recorded"
        }
        missing = required_keys - set(data.keys())
        assert not missing, f"Missing required keys: {missing} in {data}"

        # Status enum
        assert data["status"] in ("healthy", "degraded", "critical", "unknown", "error"), (
            f"invalid status: {data['status']}"
        )

        # Response time
        assert elapsed_ms < 1000, f"slow response: {elapsed_ms}ms > 1000ms"

        # Test 2: /health alias
        status_code, body = _http_get_with_error_body(f"http://127.0.0.1:{test_port}/health")
        alias_data = json.loads(body)
        assert alias_data.get("status") == "ok"

        # Test 3: 404 dla unknown path
        status_code, body = _http_get_with_error_body(f"http://127.0.0.1:{test_port}/unknown")
        assert status_code == 404, f"expected 404, got {status_code}"
    finally:
        endpoint_mod.stop_health_endpoint()


# ============================================================================
# Layer 2 motion-aware (V3.28-LAYER2-MOTION-AWARE) — tests 17, 18, 19, 20
# ============================================================================

def test_17_motion_aware_natural_plateau_no_alert(fresh_monitor, mock_telegram):
    """Natural plateau: panel quiet (no motion) — orders_in_panel stuck ALE NIE bug.

    Post-fix: PARSER_STUCK SUPPRESSED gdy motion=0.
    Pre-fix (legacy): would fire false positive.
    """
    for c in range(1, 11):  # 10 cycles, plenty time
        cycle_stats = {"cycle": c, "orders_in_panel": 180}  # no delivered, no new
        parsed = {"assigned_ids": set()}  # n_assigned=0 stałe
        fresh_monitor.record_tick(cycle_stats, parsed)
    assert not any("PARSER_STUCK" in a for a in mock_telegram), (
        f"Natural plateau should NOT alert. Got: {mock_telegram}"
    )


def test_18_motion_aware_real_stuck_with_motion(fresh_monitor, mock_telegram):
    """Real stuck: panel ma ruch (delivered/assigned growing) ALE order_ids stuck."""
    for c in range(1, 6):
        cycle_stats = {"cycle": c, "orders_in_panel": 180, "delivered": 2}
        parsed = {"assigned_ids": set([str(i) for i in range(4 + c)])}  # 5→9
        fresh_monitor.record_tick(cycle_stats, parsed)
    assert any("PARSER_STUCK" in a for a in mock_telegram), (
        f"Real stuck (motion present) MUST alert. Got: {mock_telegram}"
    )


def test_19_motion_aware_02may_rollover_pattern(fresh_monitor, mock_telegram):
    """02.05.2026 incident: order_ids stuck (regex 46\\d{4} broken) ALE assigned 47XXXX growing."""
    for c in range(1, 6):
        cycle_stats = {"cycle": c, "orders_in_panel": 180}
        # assigned grows 5→9 (47XXXX dodawane via PACKS_CATCHUP)
        assigned_set = set([str(470000 + i) for i in range(4 + c)])
        parsed = {"assigned_ids": assigned_set}
        fresh_monitor.record_tick(cycle_stats, parsed)
    assert any("PARSER_STUCK" in a for a in mock_telegram), (
        f"02.05 rollover pattern (assigned grows, order_ids stuck) MUST alert. Got: {mock_telegram}"
    )


def test_20_motion_aware_legacy_mode_disabled(ph, fresh_monitor, mock_telegram, monkeypatch):
    """Legacy fallback: ENABLE_PARSER_STUCK_MOTION_AWARE=0 → original behavior (alert na każdy stuck).

    monkeypatch target = `ph` fixture module (the loaded module instance fresh_monitor uses),
    NOT dispatch_v2.parser_health (different module path bo fixture loads from /tmp/ pre-deploy).
    """
    monkeypatch.setattr(ph, "ENABLE_PARSER_STUCK_MOTION_AWARE", False)
    for c in range(1, 6):
        cycle_stats = {"cycle": c, "orders_in_panel": 180}  # no motion
        parsed = {"assigned_ids": set()}
        fresh_monitor.record_tick(cycle_stats, parsed)
    assert any("PARSER_STUCK" in a for a in mock_telegram), (
        f"Legacy mode (motion-aware OFF) MUST alert. Got: {mock_telegram}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main(["-v", "--tb=short", __file__]))
