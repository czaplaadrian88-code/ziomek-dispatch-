"""Standalone liveness probe for the 5 long-running Ziomek dispatch services.

Invoked once per run by a systemd timer (~every 2 min). DETECTION-ONLY: it
alerts on Telegram and NEVER restarts or otherwise touches any service.

Closes audit findings:
  * E1 — dispatch-watchdog skips long_running units (`unit_type == "long_running"
    -> continue`), so the 5 hot services had no liveness check at all.
  * E4 — parser_health's HTTP thread runs *inside* panel_watcher (fate-sharing);
    this probe is a separate process, so it can observe that thread independently.

Per-service signals are heterogeneous on purpose (anti alert-spam, Lekcja #76):
shadow/sla emit a 60s HEARTBEAT (log-age works); panel_watcher is best observed
via its packs-cache mtime; telegram is event-driven and silent off-peak so only
is-active is meaningful; gps is a request-driven PWA so is-active + a TCP accept.

Run as:  python -m dispatch_v2.observability.liveness_probe [--once] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

logger = logging.getLogger("liveness_probe")

STATE_PATH = "/root/.openclaw/workspace/dispatch_state/liveness_probe_state.json"
DEDUP_WINDOW_SEC = 1800  # do not re-alert the same unit within 30 min

SHADOW_HEARTBEAT_MAX_SEC = 300
SLA_HEARTBEAT_MAX_SEC = 300
PANEL_PACKS_PATH = "/root/.openclaw/workspace/dispatch_state/panel_packs_cache.json"
PANEL_PACKS_MTIME_MAX_SEC = 600
GPS_PORT = 8766
HEALTH_PORT = 8888
TCP_TIMEOUT_SEC = 2.0
JOURNAL_LINES = 400
GPS_TCP_FAIL_THRESHOLD = 2  # alert only after 2 consecutive TCP fails

# (unit, status, detail); status is one of "ok" | "down" | "unknown"
CheckResult = Tuple[str, str, str]


def _now() -> float:
    return time.time()


def _utcstamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def _run(cmd: list[str], timeout: float = 5.0) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except Exception as e:  # noqa: BLE001 - probe must never crash on subprocess
        logger.warning("subprocess fail %s: %s", cmd[:2], e)
        return -1, ""


def _journal_last_age(unit: str, needle: str, lines: int = JOURNAL_LINES) -> Optional[float]:
    """Age (sec) of the last journal line for `unit` containing `needle`, else None."""
    rc, out = _run(
        ["journalctl", "-u", unit, "-n", str(lines), "-o", "short-unix", "--no-pager"]
    )
    if not out:
        return None
    for line in reversed(out.splitlines()):
        if needle in line:
            try:
                ts = float(line.split()[0])
                return _now() - ts
            except (ValueError, IndexError):
                return None
    return None


def _file_mtime_age(path: str) -> Optional[float]:
    try:
        return _now() - os.stat(path).st_mtime
    except Exception:  # noqa: BLE001 - missing/unreadable -> unknown
        return None


def _is_active(unit: str) -> Optional[bool]:
    rc, out = _run(["systemctl", "is-active", unit])
    s = out.strip()
    if s in ("active", "activating", "reloading"):
        return True
    if s in ("inactive", "failed", "deactivating"):
        return False
    return None


def _tcp_ok(host: str, port: int, timeout: float = TCP_TIMEOUT_SEC) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:  # noqa: BLE001 - any connect failure means not accepting
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- state

def load_state() -> dict:
    state = {"last_alert": {}, "gps_tcp_fail_streak": 0}
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict):
            state["last_alert"] = data.get("last_alert", {}) or {}
            state["gps_tcp_fail_streak"] = int(data.get("gps_tcp_fail_streak", 0) or 0)
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001 - corrupt state -> start fresh, don't crash
        logger.warning("load_state fail (%s); using defaults", e)
    return state


def save_state(state: dict) -> None:
    d = os.path.dirname(STATE_PATH)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=d, prefix=".liveness_probe_state.", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(state, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, STATE_PATH)
    except Exception as e:  # noqa: BLE001 - save failure must not crash the probe
        logger.warning("save_state fail: %s", e)
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _should_alert(state: dict, unit: str, now: float) -> bool:
    last = state["last_alert"].get(unit)
    return last is None or (now - last) >= DEDUP_WINDOW_SEC


# ------------------------------------------------------------------- ledger reconcile

# Map liveness check unit-name -> (cron_health ledger key, unit_type). The
# cron_health ledger is failure-only (OnFailure handler writes failures, nothing
# wrote successes), so a unit that failed once stayed status=failed forever even
# after recovery -> permanent /health/all cron_timers=degraded false positive
# (2026-05-31). Recording success on every confirmed-ok check reconciles the
# ledger within one probe tick. parser-health-8888 is an in-process thread, not a
# systemd unit, so it is intentionally absent.
_LEDGER_UNITS = {
    "dispatch-shadow": ("dispatch-shadow.service", "long_running"),
    "dispatch-sla-tracker": ("dispatch-sla-tracker.service", "long_running"),
    "dispatch-panel-watcher": ("dispatch-panel-watcher.service", "long_running"),
    "dispatch-telegram": ("dispatch-telegram.service", "long_running"),
    "dispatch-gps": ("dispatch-gps.service", "long_running"),
}
SELF_LEDGER_UNIT = "dispatch-liveness-probe.service"


def _record_ledger_success(ledger_unit: str, unit_type: str) -> None:
    """Mark a confirmed-healthy unit as succeeded in cron_health (fail-soft)."""
    try:
        from dispatch_v2.observability import cron_health
        cron_health.record_run_success(ledger_unit, unit_type=unit_type)
    except Exception as e:  # noqa: BLE001 - ledger write must never crash the probe
        logger.warning("cron_health record_run_success fail for %s: %s", ledger_unit, e)


def _mark_alerted(state: dict, unit: str, now: float) -> None:
    state["last_alert"][unit] = now


# --------------------------------------------------------------------------- checks

def check_shadow() -> CheckResult:
    unit = "dispatch-shadow"
    age = _journal_last_age(unit, "HEARTBEAT")
    if age is None:
        return unit, "unknown", "no HEARTBEAT in journal window"
    if age > SHADOW_HEARTBEAT_MAX_SEC:
        return unit, "down", f"HEARTBEAT stale {int(age)}s>{SHADOW_HEARTBEAT_MAX_SEC}s"
    return unit, "ok", f"HEARTBEAT {int(age)}s"


def check_sla() -> CheckResult:
    unit = "dispatch-sla-tracker"
    age = _journal_last_age(unit, "HEARTBEAT")
    if age is None:
        return unit, "unknown", "no HEARTBEAT in journal window"
    if age > SLA_HEARTBEAT_MAX_SEC:
        return unit, "down", f"HEARTBEAT stale {int(age)}s>{SLA_HEARTBEAT_MAX_SEC}s"
    return unit, "ok", f"HEARTBEAT {int(age)}s"


def check_panel_watcher() -> CheckResult:
    unit = "dispatch-panel-watcher"
    active = _is_active(unit)
    if active is False:
        return unit, "down", "is-active != active"
    if active is None:
        return unit, "unknown", "is-active unknown"
    age = _file_mtime_age(PANEL_PACKS_PATH)
    if age is None:
        return unit, "unknown", "panel_packs_cache missing"
    if age > PANEL_PACKS_MTIME_MAX_SEC:
        return unit, "down", f"panel_packs_cache stale {int(age)}s>{PANEL_PACKS_MTIME_MAX_SEC}s"
    return unit, "ok", f"packs_cache {int(age)}s"


def check_telegram() -> CheckResult:
    # Deliberately NOT log/proposal-age: telegram is event-driven and silent
    # off-peak, so log-age would false-positive (Lekcja #76). It self-exits on
    # getUpdates MAX_FAILS, so crashes are covered by systemd OnFailure; the
    # probe only needs the active-state check here.
    unit = "dispatch-telegram"
    active = _is_active(unit)
    if active is False:
        return unit, "down", "is-active != active"
    if active is None:
        return unit, "unknown", "is-active unknown"
    return unit, "ok", "active"


def check_gps(state: dict) -> CheckResult:
    unit = "dispatch-gps"
    active = _is_active(unit)
    if active is False:
        state["gps_tcp_fail_streak"] = 0
        return unit, "down", "is-active != active"
    if active is None:
        return unit, "unknown", "is-active unknown"
    if _tcp_ok("127.0.0.1", GPS_PORT):
        state["gps_tcp_fail_streak"] = 0
        return unit, "ok", f"active, TCP {GPS_PORT} ok"
    streak = state.get("gps_tcp_fail_streak", 0) + 1
    state["gps_tcp_fail_streak"] = streak
    if streak >= GPS_TCP_FAIL_THRESHOLD:
        return unit, "down", f"TCP {GPS_PORT} fail x{streak}"
    return unit, "ok", f"active, TCP {GPS_PORT} transient fail x{streak} (tolerated)"


def check_health_endpoint() -> CheckResult:
    # Raw TCP connect only -- NOT an HTTP GET. A GET would invoke parser_health
    # do_GET, which fires the dormant downstream cross-check; that currently
    # false-positives as 'worker_stuck' off-peak (finding E3b). TCP accept proves
    # the health thread is alive without that side effect.
    unit = "parser-health-8888"
    if _tcp_ok("127.0.0.1", HEALTH_PORT):
        return unit, "ok", f"accepting :{HEALTH_PORT}"
    return unit, "down", f":{HEALTH_PORT} not accepting (parser_health thread dead)"


# --------------------------------------------------------------------------- alert

def _send_alert(unit: str, detail: str) -> bool:
    text = f"[ZIOMEK LIVENESS] {unit} DOWN -- {detail} @ {_utcstamp()}"
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        return bool(send_admin_alert(text))
    except Exception as e:  # noqa: BLE001 - alert path failure must not crash probe
        logger.error("send_alert fail for %s: %s", unit, e)
        return False


# --------------------------------------------------------------------------- main

def main() -> None:
    parser = argparse.ArgumentParser(description="Ziomek long-running services liveness probe")
    parser.add_argument("--once", action="store_true", help="single run (default behaviour)")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute and log results but do NOT send Telegram or write state")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] liveness_probe: %(message)s",
    )

    state = load_state()
    now = _now()

    checks = [
        check_shadow(),
        check_sla(),
        check_panel_watcher(),
        check_telegram(),
        check_gps(state),
        check_health_endpoint(),
    ]

    changed = False
    for unit, status, detail in checks:
        if status == "ok":
            logger.info("%s: OK %s", unit, detail)
            mapped = _LEDGER_UNITS.get(unit)
            if mapped and not args.dry_run:
                _record_ledger_success(*mapped)
            if unit in state["last_alert"]:
                del state["last_alert"][unit]  # re-arm so recovery->failure alerts immediately
                changed = True
        elif status == "unknown":
            logger.warning("%s: UNKNOWN %s (no alert)", unit, detail)
        elif status == "down":
            logger.error("%s: DOWN %s", unit, detail)
            if args.dry_run:
                continue
            if _should_alert(state, unit, now):
                if _send_alert(unit, detail):
                    _mark_alerted(state, unit, now)
                    changed = True
            else:
                logger.info("%s: DOWN suppressed (within %ds dedup window)", unit, DEDUP_WINDOW_SEC)

    summary = " ".join(f"{u}={s}" for u, s, _ in checks)
    logger.info("liveness-probe run done: %s", summary)

    # check_gps may mutate the streak even without alerting, so persist when not dry-run.
    # A completed run is itself this cron_timer's success signal.
    if not args.dry_run:
        _record_ledger_success(SELF_LEDGER_UNIT, "cron_timer")
        save_state(state)

    sys.exit(0)


if __name__ == "__main__":
    main()
