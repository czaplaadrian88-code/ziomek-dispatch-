#!/usr/bin/env python3
"""Meta-werdykt monitora refloor (wzór c2_monitor_atrun_verdict).

Odpalany przez `at` PO zakończeniu okna monitora (job 107, jutro obiad).
Czyta digest md + samples jsonl i wysyła JEDNO potwierdzenie na Telegram:
  - czy monitor dobiegł (digest md istnieje + ma blok z dzisiejszą datą),
  - ostatni werdykt (🟢/🟡/🔴/⚪),
  - liczbę sampli dziś + liczbę linii PICKUP_REFLOOR cid w logu.
Odporny na śmierć procesu monitora: jeśli digest brak → relay i tak mówi
co złapano w samples + każe zajrzeć do logu.

READ-ONLY. Tylko Telegram.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

CID = sys.argv[1] if len(sys.argv) > 1 else "123"
LABEL = sys.argv[2] if len(sys.argv) > 2 else "obiad-01.06"
REPORT_DIR = Path("/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-31")
DIGEST_MD = REPORT_DIR / f"refloor_peak_{CID}_digest.md"
SAMPLES = REPORT_DIR / f"refloor_peak_{CID}_samples.jsonl"
RECHECK_LOG = Path("/root/.openclaw/workspace/scripts/logs/plan_recheck.log")
MON_LOG = Path("/root/.openclaw/workspace/scripts/logs/refloor_monitor.log")


def _send(text):
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        send_admin_alert(text)
    except Exception as e:  # noqa: BLE001
        print(f"[tg-fail] {e}", file=sys.stderr)
    print(text)


def _last_digest_block():
    if not DIGEST_MD.exists():
        return None
    txt = DIGEST_MD.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n## ", txt)
    if len(blocks) < 2:
        return None
    return ("## " + blocks[-1]).strip()


def _today_sample_count():
    if not SAMPLES.exists():
        return 0, 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    n = live = 0
    for line in SAMPLES.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(rec.get("ts", "")).startswith(today):
            n += 1
            if rec.get("live_pickup_count", 0) > 0:
                live += 1
    return n, live


def _refloor_lines_today():
    if not RECHECK_LOG.exists():
        return 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pat = re.compile(rf"PICKUP_REFLOOR cid={re.escape(CID)}\b")
    n = 0
    for line in RECHECK_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(today) and "PICKUP_REFLOOR" in line and pat.search(line):
            n += 1
    return n


def main():
    block = _last_digest_block()
    n_today, n_live = _today_sample_count()
    refloor_n = _refloor_lines_today()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    digest_is_today = bool(block) and today in block

    if digest_is_today:
        head = block.splitlines()[0].replace("## ", "")
        verdict_line = next(
            (ln for ln in block.splitlines() if ln.strip().startswith(("🟢", "🟡", "🔴", "⚪"))),
            "(brak linii werdyktu)",
        )
        msg = (
            f"✅ REFLOOR MONITOR — wynik {LABEL} (cid={CID})\n"
            f"Monitor dobiegł ({head}).\n"
            f"{verdict_line}\n"
            f"Sample dziś={n_today} (z żywymi odbiorami={n_live}), "
            f"PICKUP_REFLOOR w logu dziś={refloor_n}."
        )
    else:
        msg = (
            f"⚠️ REFLOOR MONITOR — brak digestu z dziś {LABEL} (cid={CID}).\n"
            f"Proces monitora mógł nie dobiec. Złapane sample dziś={n_today} "
            f"(żywe odbiory={n_live}), PICKUP_REFLOOR dziś={refloor_n}.\n"
            f"Sprawdź ręcznie: {MON_LOG}"
        )
    _send(msg)


if __name__ == "__main__":
    main()
