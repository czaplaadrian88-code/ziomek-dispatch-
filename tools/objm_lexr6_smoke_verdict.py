#!/usr/bin/env python3
"""SMOKE-VERDICT objm-lexr6 Faza 2 — one-shot timer 2026-06-26 08:15 UTC (smoke +45 min).

Czyta sygnały ostatnich ~55 min (przez zahartowany monitor: rotacje + okno):
  - log_signals: 'OBJM_LEXR6_SELECT pick failed' (errors) + 'reorder' (reorders).
  - shadow_metrics: latencja p95 / KOORD% (informacyjnie).
Decyzja:
  - SELECT już OFF (ktoś cofnął) -> tylko zaraportuj, nic nie rób.
  - errors > 0  -> AUTO-ROLLBACK (SELECT=false + SHADOW=true) + disable monitor + Telegram STOP.
  - errors == 0 -> Telegram CLEAN: SELECT zostaje ON, wjeżdża w peak jako canary (monitor pilnuje).
Fail-open: każdy wyjątek -> Telegram „verdict błąd, sprawdź ręcznie", BEZ auto-rollbacku
(nie cofamy na ślepo gdy sami nie umiemy zmierzyć).
"""
import json, os, sys, subprocess, tempfile
from datetime import datetime, timezone, timedelta

SCRIPTS = "/root/.openclaw/workspace/scripts"
FLAGS = f"{SCRIPTS}/flags.json"
LOG = f"{SCRIPTS}/logs/objm_lexr6_smoke.log"
MONITOR_TIMER = "dispatch-objm-lexr6-canary-monitor.timer"
WINDOW_MIN = 55


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


def _rollback(reason):
    try:
        d = json.load(open(FLAGS))
        d["ENABLE_OBJM_LEXR6_SELECT"] = False
        d["ENABLE_OBJM_LEXR6_SELECT_SHADOW"] = True
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(FLAGS))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(d, indent=2, ensure_ascii=False))
        os.replace(tmp, FLAGS)
        subprocess.run(["systemctl", "disable", "--now", MONITOR_TIMER], check=False, timeout=30)
        _log(f"ROLLBACK done ({reason})")
        return True
    except Exception as e:
        _log(f"ROLLBACK FAIL {e!r}")
        return False


def main():
    try:
        sys.path.insert(0, SCRIPTS)
        from dispatch_v2.tools import objm_lexr6_canary_monitor as M
        since = datetime.now(timezone.utc) - timedelta(minutes=WINDOW_MIN)
        flags = M.flag_state()
        if not flags.get("select_on"):
            _log("SELECT już OFF — pomijam verdict")
            _tg("⚪ objm-lexr6 SMOKE VERDICT: SELECT już OFF (ktoś cofnął) — nic nie robię.")
            return 0
        log = M.log_signals(since)
        cur = M.shadow_metrics(since) or {}
        errs, reord, n = log["errors"], log["reorders"], cur.get("n", 0)
        p95 = cur.get("lat_p95")
        _log(f"verdict: errors={errs} reorders={reord} n={n} p95={p95}")

        if errs > 0:
            ok = _rollback(f"{errs}× pick-failed")
            tail = "ROLLBACK wykonany." if ok else "⚠ ROLLBACK NIE POWIÓDŁ SIĘ — cofnij ręcznie!"
            _tg(f"🔴 objm-lexr6 SMOKE STOP: {errs}× 'pick failed' w {WINDOW_MIN} min "
                f"(n={n}, reorders={reord}). {tail}", priority="high")
            return 0

        _tg(f"🟢 objm-lexr6 SMOKE CLEAN: 0 pick-failed, {reord} reorderów w {WINDOW_MIN} min "
            f"(n={n}, p95={p95}ms). SELECT zostaje ON → wjeżdża w lunch/dinner peak jako CANARY; "
            f"monitor pilnuje gate'y (STOP/WARN→tu). Rollback ręczny: ENABLE_OBJM_LEXR6_SELECT=false.")
        _log("SMOKE CLEAN wysłany")
        return 0
    except Exception as e:
        _log(f"VERDICT ERROR {e!r}")
        _tg(f"🟡 objm-lexr6 SMOKE VERDICT błąd ({e!r}) — NIE cofam auto, sprawdź ręcznie "
            f"(monitor dalej liczy gate'y).", priority="high")
        return 1


if __name__ == "__main__":
    sys.exit(main())
