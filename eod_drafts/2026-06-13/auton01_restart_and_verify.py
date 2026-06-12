#!/usr/bin/env python3
"""AUTON-01 nocny restart dispatch-shadow + weryfikacja POST (okno 02-05 UTC).

Uruchamiany z at-joba. Bramka PRE-restart zweryfikowana w sesji 12.06 wieczór
(inwentaryzacja commitów / suita=baseline / krzywa weekend / fingerprint /
backupy — handoff sprint_timeline 2026-06-13). Ten skrypt wykonuje restart
TYLKO dispatch-shadow i raportuje wynik na Telegram Adriana.

Oczekiwany diff fingerprintu vs start 18:32:19 UTC:
  + ENABLE_AUTO_ASSIGN=0          (nowa flaga AUTON-01, kanon =false)
  + ENABLE_BUNDLE_SYNC_SPREAD 0→1 (hot re-flip 18:33 UTC, NIE zmiana restartowa)

Rollback: flagi hot w flags.json; twardy = git revert a7efd21 + restart
(backupy .bak-pre-auton01-2026-06-13 są).
"""
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

LOG = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-13/auton01_restart.log"


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def main():
    checks = []
    ok_all = True

    def check(name, ok, detail=""):
        nonlocal ok_all
        checks.append(f"{'✅' if ok else '❌'} {name}" + (f": {detail}" if detail else ""))
        if not ok:
            ok_all = False
        log(f"CHECK {name} ok={ok} {detail}")

    # 0. PRE: telegram MainPID (ma pozostać NIEZMIENIONY)
    _, tg_pid_before, _ = run("systemctl show dispatch-telegram -p MainPID --value")
    log(f"PRE telegram MainPID={tg_pid_before}")

    # 1. RESTART — tylko dispatch-shadow
    ts_restart = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rc, _, err = run("systemctl restart dispatch-shadow")
    check("restart wykonany", rc == 0, err[:120] if rc else "")
    time.sleep(75)

    # 2. POST: stan unitu
    _, sub, _ = run("systemctl show dispatch-shadow -p SubState --value")
    _, nrest, _ = run("systemctl show dispatch-shadow -p NRestarts --value")
    check("SubState=running", sub == "running", sub)
    check("NRestarts=0", nrest == "0", nrest)

    # 3. POST: fingerprint w logu — nowa flaga obecna, SYNC=1
    _, fp, _ = run(
        f"journalctl -u dispatch-shadow --since '{ts_restart}' | grep FLAG_FINGERPRINT | head -1")
    check("FLAG_FINGERPRINT zalogowany", bool(fp))
    check("ENABLE_AUTO_ASSIGN=0 w fingerprincie", "ENABLE_AUTO_ASSIGN=0" in fp)
    check("ENABLE_BUNDLE_SYNC_SPREAD=1 (re-flip 18:33)", "ENABLE_BUNDLE_SYNC_SPREAD=1" in fp)

    # 4. POST: ortools warm-up + login/health + brak błędów
    _, warm, _ = run(
        f"journalctl -u dispatch-shadow --since '{ts_restart}' | grep -i 'warm' | head -2")
    check("ortools warm-up", bool(warm), warm[-60:] if warm else "brak linii")
    _, errs, _ = run(
        f"journalctl -u dispatch-shadow --since '{ts_restart}' -p err | grep -v '^--' | head -5")
    check("zero błędów journalu", not errs, errs[:160])
    _, login, _ = run(
        f"journalctl -u dispatch-shadow --since '{ts_restart}' | grep -iE 'login|panel_client' | head -2")
    log(f"login lines: {login[:200]}")

    # 5. POST: dispatch-telegram NIETKNIĘTY
    _, tg_pid_after, _ = run("systemctl show dispatch-telegram -p MainPID --value")
    check("dispatch-telegram nietknięty (MainPID bez zmian)",
          tg_pid_after == tg_pid_before and tg_pid_after not in ("", "0"),
          f"{tg_pid_before}→{tg_pid_after}")

    # 6. POST: nowe pola w shadow_decisions (o 02-05 UTC zero ruchu — to
    # może być puste; pełna weryfikacja przy pierwszych zleceniach rano).
    _, fields, _ = run(
        f"tail -5 /root/.openclaw/workspace/dispatch_state/shadow_decisions.jsonl | grep -c would_auto_assign")
    note_fields = ("pola would_auto_assign już widoczne" if fields not in ("", "0")
                   else "brak nowych decyzji w oknie (zero ruchu nocą — sprawdzić rano)")
    log(f"would_auto_assign w tail: {fields} → {note_fields}")

    # 7. Telegram raport
    status = "✅ CZYSTY" if ok_all else "❌ PROBLEM — sprawdź log"
    msg = (
        f"🌙 AUTON-01: restart dispatch-shadow {ts_restart} UTC — {status}\n\n"
        + "\n".join(checks)
        + f"\nℹ️ {note_fields}\n"
        "Aktywowane restartem: telemetria would_auto_assign/auto_block_reasons "
        "(compute-zawsze) + egzekutor auto-assign OFF (ENABLE_AUTO_ASSIGN=false, "
        "killswitch hot). Zero zmian decyzyjnych.\n"
        "Rollback: git revert a7efd21 + restart (baki .bak-pre-auton01 są)."
    )
    try:
        from dispatch_v2 import telegram_utils
        telegram_utils.send_admin_alert(msg)
        log("TG wysłany")
    except Exception as e:
        log(f"TG FAIL: {e}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
