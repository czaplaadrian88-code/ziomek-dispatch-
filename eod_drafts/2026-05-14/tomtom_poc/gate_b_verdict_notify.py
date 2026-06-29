"""GATE B — uruchamia analyze_realworld.py i wysyla werdykt na Telegram.

Odpalane przez lokalny at-job (2026-05-19 ~10:00 Warsaw) po ~3 dniach zbierania
forward-live. LOKALNY — NIE remote: dane (rw_results.jsonl, courier_api.db,
sla_log) sa na tym serwerze, nie w gicie.
"""
import datetime
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/root/.openclaw/workspace/scripts")


def main():
    r = subprocess.run([sys.executable, "analyze_realworld.py"],
                        capture_output=True, text=True, cwd=HERE)
    out = r.stdout
    if r.stderr.strip():
        out += "\n[STDERR]\n" + r.stderr
    stamp = datetime.date.today().isoformat()
    with open(os.path.join(HERE, f"analyze_verdict_{stamp}.txt"), "w",
              encoding="utf-8") as f:
        f.write(out)
    print(out)
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        msg = "GATE B — werdykt OSRM vs TomTom (po ~3 dniach forward-live)\n\n" + out[-3500:]
        ok = send_admin_alert(msg)
        print(f"telegram send_admin_alert={ok}")
    except Exception as e:
        print(f"telegram fail: {e}")


if __name__ == "__main__":
    main()
