#!/usr/bin/env python3
"""Testy czystej logiki watchdoga przeglądu GO/NO-GO."""
import sys
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2.tools import pending_global_resweep_watchdog as W

TODAY = "2026-06-26"


def test_ran_today_success_is_silent():
    assert W.evaluate("0", "Fri 2026-06-26 07:00:03 UTC", "success", TODAY) is None


def test_did_not_run_alerts():
    msg = W.evaluate("", "", "", TODAY)
    assert msg and "NIE odpalił" in msg


def test_ran_yesterday_counts_as_not_today():
    msg = W.evaluate("0", "Thu 2026-06-25 07:00:03 UTC", "success", TODAY)
    assert msg and "NIE odpalił" in msg


def test_ran_today_but_failed_alerts():
    msg = W.evaluate("1", "Fri 2026-06-26 07:00:03 UTC", "failed", TODAY)
    assert msg and "BŁĘDEM" in msg


def test_ran_today_nonzero_status_alerts():
    msg = W.evaluate("203", "Fri 2026-06-26 07:00:03 UTC", "exit-code", TODAY)
    assert msg and "BŁĘDEM" in msg
