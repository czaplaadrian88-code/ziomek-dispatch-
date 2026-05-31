"""liveness_probe -> cron_health ledger reconcile tests (2026-05-31).

Root cause covered: cron_health ledger was failure-only (OnFailure handler wrote
failures, nothing wrote successes), so a unit that failed once stayed
status=failed forever after recovery -> permanent /health/all cron_timers=degraded
false positive. liveness_probe now records success on every confirmed-ok check.
"""
import json
from pathlib import Path
from unittest.mock import patch

from dispatch_v2.observability import cron_health, liveness_probe


def _write_failed_ledger(path: Path, unit: str, unit_type: str) -> None:
    data = {
        "units": {
            unit: {
                "type": unit_type,
                "status": "failed",
                "consecutive_failures": 9,
                "last_success": None,
                "last_failure": "2026-05-29T10:30:49+00:00",
                "last_failure_result": "timeout",
                "expected_max_silence_h": None,
                "last_updated": "2026-05-29T10:30:49+00:00",
            }
        },
        "_meta": {"schema_version": 1, "last_write_ts": "2026-05-29T10:30:49+00:00"},
    }
    path.write_text(json.dumps(data))


def test_record_ledger_success_clears_stuck_failed(tmp_path):
    """A recovered long_running unit flips failed -> active, failures reset."""
    p = tmp_path / "cron_health.json"
    _write_failed_ledger(p, "dispatch-telegram.service", "long_running")
    with patch.object(cron_health, "CRON_HEALTH_PATH", p):
        liveness_probe._record_ledger_success("dispatch-telegram.service", "long_running")
        entry = cron_health.load_health(p)["units"]["dispatch-telegram.service"]
    assert entry["status"] == "active"
    assert entry["consecutive_failures"] == 0
    assert entry["last_success"] is not None


def test_record_ledger_success_cron_timer_status_ok(tmp_path):
    """cron_timer recovery flips to status=ok (not active)."""
    p = tmp_path / "cron_health.json"
    _write_failed_ledger(p, "dispatch-liveness-probe.service", "cron_timer")
    with patch.object(cron_health, "CRON_HEALTH_PATH", p):
        liveness_probe._record_ledger_success("dispatch-liveness-probe.service", "cron_timer")
        entry = cron_health.load_health(p)["units"]["dispatch-liveness-probe.service"]
    assert entry["status"] == "ok"
    assert entry["consecutive_failures"] == 0


def test_record_ledger_success_fail_soft(tmp_path):
    """Ledger write errors must never propagate out of the probe."""
    with patch.object(cron_health, "record_run_success", side_effect=OSError("disk full")):
        # Must not raise.
        liveness_probe._record_ledger_success("dispatch-telegram.service", "long_running")


def test_ledger_units_map_excludes_non_systemd_thread():
    """parser-health-8888 is an in-process thread, not a ledger unit."""
    assert "parser-health-8888" not in liveness_probe._LEDGER_UNITS
    assert liveness_probe._LEDGER_UNITS["dispatch-telegram"] == (
        "dispatch-telegram.service", "long_running"
    )


if __name__ == "__main__":
    import sys
    import tempfile
    import traceback

    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            n = t.__code__.co_argcount
            if n:
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
    print(f"\n{passed}/{passed+failed} passed")
    sys.exit(0 if failed == 0 else 1)
