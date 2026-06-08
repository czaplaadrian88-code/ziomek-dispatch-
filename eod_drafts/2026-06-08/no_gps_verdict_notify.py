#!/usr/bin/env python3
"""at-job: uruchamia harness pozycjonowania bez GPS i wysyła werdykt 3 pytań
(działa / różnica / na plus + część B parytet) na Telegram Adriana.

Uruchamiane przez `at` jutro ~21:00 Warsaw (19:00 UTC) po obu peakach.
Uruchom ręcznie z cwd=scripts/:
    cd /root/.openclaw/workspace/scripts
    /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/eod_drafts/2026-06-08/no_gps_verdict_notify.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "no_gps_positioning_test.py")


def main():
    try:
        r = subprocess.run([sys.executable, HARNESS],
                           capture_output=True, text=True, timeout=300)
        out = r.stdout or ""
        if r.returncode != 0:
            out += "\n[stderr]\n" + (r.stderr or "")[-800:]
    except Exception as e:
        out = f"HARNESS FAIL: {type(e).__name__}: {e}"

    # Telegram limit ~4096 — bierzemy ogon (sekcje [1..B] + VERDICT mieszczą się).
    msg = "🛰 Weryfikacja pozycjonowania BEZ GPS (last-known-pos store) — peak\n\n" + out[-3600:]
    try:
        from dispatch_v2 import telegram_utils
        ok = telegram_utils.send_admin_alert(msg)
        print("sent" if ok else "send FAILED")
    except Exception as e:
        print(f"notify import/send fail: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
