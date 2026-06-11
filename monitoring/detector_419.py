"""V3.28-MONITOR-001 — 419 storm detector.

Watches dispatch-shadow journalctl + dispatch-panel-watcher journalctl + watcher.log
file for "HTTP 419" / "csrf" / "unauthor" patterns. When sliding 60-second window
exceeds threshold (default 5), sends Telegram alert to Adrian admin chat with 300s
debounce to avoid alert spam.

Why separate from dispatch services: independent monitor process means a 419 storm
inside dispatch services (which previously took 38-64 min to notice) gets picked up
within ~10s by this detector. Defense-in-depth complementing V3.28 Phase 1
(default-OFF panel_bg_refresh).

Memory bounded: deque maxlen=200 prevents unbounded growth. Single Python process,
< 50 MB RSS expected.

Designed for systemd: Restart=always, logs to journalctl + /var/log/v328_monitor_419.log.
"""
from __future__ import annotations

import logging
import logging.handlers
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Tuple
from zoneinfo import ZoneInfo

# SP-B2-PEAKWIN / Z-20 (2026-06-11): astimezone() bez argumentu = strefa
# SYSTEMOWA (serwer chodzi w UTC) — alert podpisywał czas UTC jako "Warsaw".
_WARSAW_TZ = ZoneInfo("Europe/Warsaw")

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2.telegram_utils import send_admin_alert

WINDOW_SECONDS = 60
THRESHOLD_COUNT = 5
DEBOUNCE_SECONDS = 300
PATTERN = re.compile(r"HTTP 419|csrf|unauthor", re.IGNORECASE)
WATCHER_LOG = Path("/root/.openclaw/workspace/scripts/logs/watcher.log")
LOG_FILE = Path("/var/log/v328_monitor_419.log")

_events: Deque[Tuple[float, str]] = deque(maxlen=200)
_events_lock = threading.Lock()
_last_alert_at: float = 0.0
_last_alert_lock = threading.Lock()

_log = logging.getLogger("v328_monitor_419")


def _setup_logging() -> None:
    _log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    _log.addHandler(sh)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5_000_000, backupCount=3
        )
        fh.setFormatter(fmt)
        _log.addHandler(fh)
    except OSError as e:
        _log.warning(f"file handler unavailable: {e}")


def _record_event(source: str) -> None:
    now = time.time()
    with _events_lock:
        _events.append((now, source))


def _count_recent_events() -> int:
    cutoff = time.time() - WINDOW_SECONDS
    with _events_lock:
        return sum(1 for ts, _ in _events if ts >= cutoff)


def _events_by_source() -> dict[str, int]:
    cutoff = time.time() - WINDOW_SECONDS
    counts: dict[str, int] = {}
    with _events_lock:
        for ts, src in _events:
            if ts >= cutoff:
                counts[src] = counts.get(src, 0) + 1
    return counts


def _maybe_alert(count: int) -> None:
    global _last_alert_at
    with _last_alert_lock:
        now = time.time()
        if now - _last_alert_at < DEBOUNCE_SECONDS:
            return
        _last_alert_at = now

    by_src = _events_by_source()
    src_str = ", ".join(f"{k}={v}" for k, v in sorted(by_src.items()))
    warsaw_now = datetime.now(timezone.utc).astimezone(_WARSAW_TZ).strftime("%H:%M Warsaw")
    text = (
        f"🚨 Burza błędów sesji panelu — {count} razy w {WINDOW_SECONDS} sekund\n"
        f"O {warsaw_now} services traciły sesję CSRF z panelem.\n"
        f"Źródła: {src_str}.\n"
        "Najczęstsza przyczyna: dwa równoległe procesy odświeżają tę samą sesję login (race) "
        "albo nowo wdrożony moduł odpala własny background refresh.\n"
        "\n"
        "Co Ty masz zrobić:\n"
        "1) Sprawdź czy ostatnio coś zostało zdeployowane (git log --since=1h)\n"
        "2) Jeśli jeden ze services straszy logi → restart go: "
        "sudo systemctl restart dispatch-{shadow|panel-watcher}\n"
        "3) Jak nowy moduł wprowadza bg_refresh → wyłącz: ENABLE_PANEL_BG_REFRESH=0"
    )
    _log.warning(f"ALERT 419 storm: count={count} src={src_str}")
    ok = send_admin_alert(text)
    if not ok:
        _log.error("send_admin_alert failed (fall-through to journal log)")


def _checker_loop() -> None:
    while True:
        time.sleep(5)
        try:
            count = _count_recent_events()
            if count >= THRESHOLD_COUNT:
                _maybe_alert(count)
        except Exception as e:
            _log.error(f"checker_loop error: {type(e).__name__}: {e}")


def _tail_subprocess(args: list[str], source_label: str) -> None:
    while True:
        try:
            _log.info(f"starting tail for {source_label}: {' '.join(args)}")
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                if PATTERN.search(line):
                    _record_event(source_label)
            rc = proc.wait()
            _log.warning(f"tail {source_label} exited rc={rc}, restarting in 5s")
        except Exception as e:
            _log.error(f"tail {source_label} crash: {type(e).__name__}: {e}")
        time.sleep(5)


def _tail_file(path: Path, source_label: str) -> None:
    args = ["tail", "-F", "-n", "0", str(path)]
    _tail_subprocess(args, source_label)


def _tail_journal(unit: str, source_label: str) -> None:
    args = ["journalctl", "-u", unit, "-f", "-n", "0", "--output=cat"]
    _tail_subprocess(args, source_label)


def main() -> None:
    _setup_logging()
    _log.info(
        f"V3.28-MONITOR-001 starting: window={WINDOW_SECONDS}s "
        f"threshold={THRESHOLD_COUNT} debounce={DEBOUNCE_SECONDS}s"
    )

    threads = [
        threading.Thread(
            target=_tail_journal,
            args=("dispatch-shadow.service", "shadow_journal"),
            daemon=True,
            name="tail_shadow",
        ),
        threading.Thread(
            target=_tail_journal,
            args=("dispatch-panel-watcher.service", "watcher_journal"),
            daemon=True,
            name="tail_watcher_journal",
        ),
        threading.Thread(
            target=_tail_file,
            args=(WATCHER_LOG, "watcher_file"),
            daemon=True,
            name="tail_watcher_file",
        ),
        threading.Thread(target=_checker_loop, daemon=True, name="checker"),
    ]
    for t in threads:
        t.start()
        _log.info(f"thread started: {t.name}")

    while True:
        time.sleep(60)
        for t in threads:
            if not t.is_alive():
                _log.error(f"thread dead: {t.name} — exiting for systemd restart")
                sys.exit(1)


if __name__ == "__main__":
    main()
