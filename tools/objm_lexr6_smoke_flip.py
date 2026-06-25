#!/usr/bin/env python3
"""SMOKE-FLIP objm-lexr6 Faza 2 — wykonywany przez one-shot timer 2026-06-26 07:30 UTC
(=09:30 Warsaw, off-peak), za ACK Adriana 2026-06-25.

Robi (idempotentnie, fail-safe):
  1. Pre-flight: dispatch-shadow active, flags.json czytelny, SELECT obecnie OFF (inaczej skip).
  2. Backup flags.json -> .bak-pre-objm-lexr6-flip-<YYYYMMDD>.
  3. Atomic hot-reload: ENABLE_OBJM_LEXR6_SELECT=true + ENABLE_OBJM_LEXR6_SELECT_SHADOW=false
     (RAZEM — inaczej cień liczy się po mutacji). BEZ restartu.
  4. enable --now dispatch-objm-lexr6-canary-monitor.timer (gate'y co 10 min, STOP/WARN->Telegram).
  5. Telegram START.
Rollback ręczny / auto (verdict +45 min): ENABLE_OBJM_LEXR6_SELECT=false + SHADOW=true.
"""
import json, os, sys, subprocess, tempfile
from datetime import datetime, timezone

SCRIPTS = "/root/.openclaw/workspace/scripts"
FLAGS = f"{SCRIPTS}/flags.json"
LOG = f"{SCRIPTS}/logs/objm_lexr6_smoke.log"
MONITOR_TIMER = "dispatch-objm-lexr6-canary-monitor.timer"


def _log(msg):
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _tg(msg, priority="low"):
    try:
        sys.path.insert(0, SCRIPTS)
        from dispatch_v2.telegram_utils import send_admin_alert
        send_admin_alert(msg, priority=priority)
    except Exception as e:
        _log(f"[telegram pominięte: {e!r}]")


def _svc_active(name):
    try:
        r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=15)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def main():
    # 1. pre-flight
    if not _svc_active("dispatch-shadow.service"):
        _log("ABORT: dispatch-shadow nieaktywny")
        _tg("🔴 objm-lexr6 SMOKE ABORT: dispatch-shadow nieaktywny — flipu NIE wykonano.")
        return 1
    try:
        d = json.load(open(FLAGS))
    except Exception as e:
        _log(f"ABORT: flags.json nieczytelny {e!r}")
        _tg(f"🔴 objm-lexr6 SMOKE ABORT: flags.json nieczytelny ({e!r}).")
        return 1
    if d.get("ENABLE_OBJM_LEXR6_SELECT", False):
        _log("SKIP: SELECT już ON (idempotencja)")
        _tg("⚪ objm-lexr6 SMOKE SKIP: SELECT już ON — nic nie robię (idempotencja).")
        return 0

    # 2. backup
    bak = f"{FLAGS}.bak-pre-objm-lexr6-flip-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    try:
        with open(bak, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
        _log(f"backup -> {bak}")
    except Exception as e:
        _log(f"ABORT: backup fail {e!r}")
        _tg(f"🔴 objm-lexr6 SMOKE ABORT: backup flags fail ({e!r}).")
        return 1

    # 3. atomic flip
    d["ENABLE_OBJM_LEXR6_SELECT"] = True
    d["ENABLE_OBJM_LEXR6_SELECT_SHADOW"] = False
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(FLAGS))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(d, indent=2, ensure_ascii=False))
        os.replace(tmp, FLAGS)
        _log("FLIP OK: SELECT=True SHADOW=False (hot-reload)")
    except Exception as e:
        _log(f"ABORT: zapis flags fail {e!r}")
        _tg(f"🔴 objm-lexr6 SMOKE ABORT: zapis flags fail ({e!r}). Przywróć {bak}.")
        return 1

    # 4. enable monitor
    try:
        subprocess.run(["systemctl", "enable", "--now", MONITOR_TIMER], check=False, timeout=30)
        _log("monitor timer enabled")
    except Exception as e:
        _log(f"WARN: enable monitor fail {e!r}")

    # 5. Telegram START
    _tg("🐤 objm-lexr6 SMOKE START (09:30 Warsaw, off-peak) — SELECT=ON, SHADOW=OFF "
        "(hot-reload, BEZ restartu). Monitor co 10 min (STOP/WARN→tu), verdict +45 min "
        "(auto-rollback gdy pick-failed). Po smoke zostaje ON → lunch/dinner peak = canary. "
        "Rollback ręczny: ENABLE_OBJM_LEXR6_SELECT=false.")
    _log("SMOKE START wysłany")
    return 0


if __name__ == "__main__":
    sys.exit(main())
