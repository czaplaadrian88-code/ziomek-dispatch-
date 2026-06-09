#!/usr/bin/env python3
"""at-job (2026-06-10 18:00 UTC = 20:00 Warsaw): przypomnienie do re-decyzji
demotacji wolnych kurierów (no_gps idle), gdy oba pomiary już są:
  - A2 reliability verdict (at-job #113, śr 10.06 07:00 UTC)
  - no_gps positioning harness (at-job #127, wt 09.06 21:00 Warsaw → /tmp/no_gps_verdict.log)

Wysyła nudge na Telegram Adriana. Pełny otwarty punkt: memory
[[feedback-bialystok-15min-idle-courier]]. To TYLKO przypomnienie — sama
re-decyzja/implementacja wymaga sesji Claude Code na boxie (live logi, kod,
cascade_harness, deploy).

Uruchom ręcznie:  cd /root/.openclaw/workspace/scripts &&
  /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/eod_drafts/2026-06-08/idle_demote_redecision_reminder.py
"""
import os

MSG = "\n".join([
    "⏰ RE-DECYZJA: demotacja wolnych kurierów (no_gps idle) — warstwa GPS buga „dwa kierunki\"",
    "",
    "Oba eksperymenty już raportowały:",
    "• A2 reliability — at-job #113 (śr 10.06 07:00 UTC); sprawdź shadow_decisions a2_reliability_delta + eod_drafts/2026-06-07/cascade_harness.py",
    "• no_gps positioning — at-job #127 (wt 09.06 21:00); /tmp/no_gps_verdict.log + eod_drafts/2026-06-08/no_gps_positioning_test.py",
    "",
    "Pytanie do rozstrzygnięcia: czy A2 + last-known-pos rescue wystarczyły, żeby wolni",
    "kurierzy przestali przegrywać z przeładowanymi? Jeśli NIE → un-demote on-shift-idle",
    "oparty o SYGNAŁ OBECNOŚCI (confirmed_for_shift z TASK B / heartbeat apki), NIE blanket.",
    "",
    "→ Odpal sesję Claude Code na boxie: 'wróć do demotacji wolnych kurierów'.",
    "Pełny kontekst: memory feedback-bialystok-15min-idle-courier.",
])


def main():
    try:
        import sys
        sys.path.insert(0, "/root/.openclaw/workspace/scripts")
        from dispatch_v2 import telegram_utils
        ok = telegram_utils.send_admin_alert(MSG)
        print("sent" if ok else "send FAILED")
    except Exception as e:
        print(f"reminder send fail: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
