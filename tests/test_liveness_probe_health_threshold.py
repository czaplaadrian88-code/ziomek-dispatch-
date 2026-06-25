"""parser-health-8888 consecutive-fail threshold tests (2026-06-25).

Root cause covered: check_health_endpoint alerted on a SINGLE TCP-connect fail
with a hardcoded "(parser_health thread dead)" message. The :8888 endpoint is a
single-threaded HTTPServer (backlog 5) living inside panel_watcher on a 2-vCPU
box -> a busy tick can starve accept() past the 2s connect timeout for one tick
without the thread being dead (observed 2026-06-24 19:48:36, recovered next
tick). check_gps already tolerates a single transient (GPS_TCP_FAIL_THRESHOLD=2);
this brings the twin parser-health check to parity: alert only on >=2 consecutive
fails. A real thread death persists across ticks, so detection slips at most +1
tick (~2 min).
"""
import json
from pathlib import Path
from unittest.mock import patch

from dispatch_v2.observability import liveness_probe


def _fresh_state() -> dict:
    return {"last_alert": {}, "gps_tcp_fail_streak": 0, "health_tcp_fail_streak": 0}


# ---------------------------------------------------------------- threshold core

def test_first_fail_tolerated_no_down():
    """1st consecutive TCP fail -> status 'ok' (tolerated), streak=1, NOT 'down'."""
    state = _fresh_state()
    with patch.object(liveness_probe, "_tcp_ok", return_value=False):
        unit, status, detail = liveness_probe.check_health_endpoint(state)
    assert unit == "parser-health-8888"
    assert status == "ok"  # tolerated -> main() will NOT alert
    assert state["health_tcp_fail_streak"] == 1
    assert "transient" in detail


def test_second_consecutive_fail_triggers_down():
    """2nd consecutive TCP fail -> status 'down' (alert)."""
    state = _fresh_state()
    with patch.object(liveness_probe, "_tcp_ok", return_value=False):
        liveness_probe.check_health_endpoint(state)          # streak 1, ok
        unit, status, detail = liveness_probe.check_health_endpoint(state)  # streak 2
    assert status == "down"
    assert state["health_tcp_fail_streak"] == 2
    assert "parser_health thread dead" in detail


def test_success_resets_streak():
    """A successful connect resets the streak to 0."""
    state = _fresh_state()
    state["health_tcp_fail_streak"] = 1
    with patch.object(liveness_probe, "_tcp_ok", return_value=True):
        unit, status, detail = liveness_probe.check_health_endpoint(state)
    assert status == "ok"
    assert state["health_tcp_fail_streak"] == 0
    assert "accepting" in detail


def test_2026_06_24_transient_never_alerts():
    """The real 2026-06-24 19:48:36 pattern [fail, ok] must NEVER reach 'down'.

    Before the fix this single tick fired a Telegram alert; after the fix the
    tolerated first fail returns 'ok' and the next-tick recovery clears it.
    """
    state = _fresh_state()
    with patch.object(liveness_probe, "_tcp_ok", return_value=False):
        _, s1, _ = liveness_probe.check_health_endpoint(state)   # 19:48:36 transient
    with patch.object(liveness_probe, "_tcp_ok", return_value=True):
        _, s2, _ = liveness_probe.check_health_endpoint(state)   # 19:50:36 recovery
    assert s1 == "ok"
    assert s2 == "ok"
    assert state["health_tcp_fail_streak"] == 0  # never escalated to down


def test_sustained_outage_still_alerts():
    """A real dead thread (fails every tick) still escalates to 'down' on tick 2+."""
    state = _fresh_state()
    statuses = []
    with patch.object(liveness_probe, "_tcp_ok", return_value=False):
        for _ in range(4):
            _, s, _ = liveness_probe.check_health_endpoint(state)
            statuses.append(s)
    assert statuses == ["ok", "down", "down", "down"]


# ------------------------------------------------------------------ twin parity

def test_threshold_parity_with_gps():
    """parser-health threshold mirrors GPS (twin paths must stay symmetric)."""
    assert liveness_probe.HEALTH_TCP_FAIL_THRESHOLD == liveness_probe.GPS_TCP_FAIL_THRESHOLD == 2


# ----------------------------------------------------------------- state persist

def test_load_state_defaults_health_streak(tmp_path):
    """Missing state file -> health_tcp_fail_streak defaults to 0."""
    p = tmp_path / "state.json"
    with patch.object(liveness_probe, "STATE_PATH", str(p)):
        st = liveness_probe.load_state()
    assert st["health_tcp_fail_streak"] == 0


def test_load_state_backward_compat_missing_key(tmp_path):
    """Pre-2026-06-25 state file (no health key) loads without crash, default 0."""
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"last_alert": {}, "gps_tcp_fail_streak": 0}))
    with patch.object(liveness_probe, "STATE_PATH", str(p)):
        st = liveness_probe.load_state()
    assert st["health_tcp_fail_streak"] == 0
    assert st["gps_tcp_fail_streak"] == 0


def test_state_persist_roundtrip(tmp_path):
    """health_tcp_fail_streak survives save_state -> load_state (cross-tick memory)."""
    p = tmp_path / "state.json"
    with patch.object(liveness_probe, "STATE_PATH", str(p)):
        st = liveness_probe.load_state()
        st["health_tcp_fail_streak"] = 1
        liveness_probe.save_state(st)
        st2 = liveness_probe.load_state()
    assert st2["health_tcp_fail_streak"] == 1


def test_parser_health_still_not_a_ledger_unit():
    """Guard: parser-health-8888 stays out of the cron_health ledger (in-proc thread)."""
    assert "parser-health-8888" not in liveness_probe._LEDGER_UNITS


if __name__ == "__main__":
    import sys
    import tempfile
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            if t.__code__.co_argcount:
                with tempfile.TemporaryDirectory() as d:
                    t(Path(d))
            else:
                t()
            print(f"PASS {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed + failed} passed")
    sys.exit(0 if failed == 0 else 1)
