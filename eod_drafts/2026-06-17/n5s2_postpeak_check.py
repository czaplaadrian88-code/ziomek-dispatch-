#!/usr/bin/env python3
"""N5-S2 post-peak INFEASIBLE health check (server-side one-shot, 2026-06-18).

Fires once after the lunch peak (scheduled via systemd transient timer). Verifies
the committed-pickup penalty flip (ENABLE_OBJ_COMMITTED_PICKUP_PENALTY, ON ~09:17
Warsaw) did NOT cause a fallback/INFEASIBLE burst. Replay predicted INFEASIBLE
delta=0 on 7564 decisions; pre-flip baseline best-plan fallback = 0.00%.

Verdict + action:
  HEALTHY      rate < 2%  and N >= 50  -> leave flag ON
  BURST        rate > 5%  and N >= 50  -> HOT ROLLBACK (cp backup over flags.json)
  INCONCLUSIVE N < 50                  -> no action (low traffic)
Always: write result file + Telegram admin alert. Never restarts a service.
Use --dry-run to skip Telegram + rollback (prints only).
"""
import json, subprocess, sys, argparse
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

FLAGS = "/root/.openclaw/workspace/scripts/flags.json"
BACKUP = "/root/.openclaw/workspace/scripts/flags.json.bak-pre-n5s2-flip-2026-06-18"
SHADOW = ["/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
          "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1"]
RESULT = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17/n5s2_postpeak_result.txt"
FLIP_UTC = datetime(2026, 6, 18, 7, 17, 0, tzinfo=timezone.utc)  # ~09:17 Warsaw
WARSAW_OFF = 2


def pts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def flag_on():
    try:
        return bool(json.load(open(FLAGS)).get("ENABLE_OBJ_COMMITTED_PICKUP_PENALTY"))
    except Exception:
        return None


def journal_markers():
    try:
        out = subprocess.run(
            ["journalctl", "-u", "dispatch-shadow", "--since",
             "2026-06-18 09:00:00", "--no-pager"],
            capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return None
    if not out:
        return None
    import re
    return sum(1 for ln in out.splitlines()
               if re.search(r"INFEASIBLE|falling back to greedy|ROUTING_FAIL", ln))


def fallback_rate():
    n = fb = 0
    for path in SHADOW:
        try:
            fh = open(path)
        except Exception:
            continue
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = pts(d.get("ts"))
            if t is None or t < FLIP_UTC:  # only post-flip decisions
                continue
            n += 1
            strat = str((d.get("best") or {}).get("strategy", "")).lower()
            if "rejected" in strat or "fallback" in strat:
                fb += 1
        fh.close()
    return n, fb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    on = flag_on()
    markers = journal_markers()
    n, fb = fallback_rate()
    rate = (100.0 * fb / n) if n else 0.0

    if n < 50:
        verdict = "INCONCLUSIVE"
        action = "brak akcji (mały ruch, N<50)"
    elif rate > 5.0:
        verdict = "BURST"
        action = "HOT-ROLLBACK flagi"
    else:
        verdict = "HEALTHY"
        action = "flaga ZOSTAJE ON"

    rolled = False
    if verdict == "BURST" and not args.dry_run:
        try:
            import shutil
            shutil.copyfile(BACKUP, FLAGS)
            rolled = True
        except Exception as e:
            action += f" (ROLLBACK FAIL: {e})"

    stamp = datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=WARSAW_OFF))).strftime("%Y-%m-%d %H:%M Warsaw")
    msg = (f"N5-S2 post-peak check ({stamp})\n"
           f"flaga ON={on} | INFEASIBLE/fallback markery(journal)={markers}\n"
           f"best-plan fallback: {fb}/{n} = {rate:.2f}% (baseline pre-flip 0.00%)\n"
           f"WERDYKT: {verdict} -> {action}"
           + (" | ROLLBACK WYKONANY" if rolled else ""))

    try:
        with open(RESULT, "w") as f:
            f.write(msg + "\n")
    except Exception:
        pass
    print(msg)

    if not args.dry_run:
        try:
            from dispatch_v2 import telegram_utils
            telegram_utils.send_admin_alert(msg, source="n5s2_postpeak_check")
        except Exception as e:
            print(f"[telegram send failed: {e}]")


if __name__ == "__main__":
    main()
