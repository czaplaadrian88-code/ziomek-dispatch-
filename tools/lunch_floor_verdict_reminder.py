#!/usr/bin/env python3
"""Przypomnienie (Adrian 2026-06-25: „remind me tomorrow to check the lunch peak verdict").

Czyta werdykt LUNCH peak committed-floor (plik pisany przez at-job 171 @ 12:25 ... nie,
@ 12:15 UTC: tools/verify_pickup_floor_peak.py --label "LUNCH 26.06") i wrzuca go na Telegram
jako jawne PRZYPOMNIENIE „sprawdź". Self-contained, uruchamiany ABSOLUTNĄ ścieżką (cwd-niezależny).
"""
import os
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

RESULT = "/root/.openclaw/workspace/scripts/logs/verify_pickup_floor_lunch.txt"


def main():
    if os.path.exists(RESULT):
        try:
            body = open(RESULT, encoding="utf-8").read().strip()
        except OSError as e:  # noqa: BLE001
            body = f"(nie udało się odczytać {RESULT}: {e})"
    else:
        body = (f"⚠ Plik {RESULT} nie istnieje — at-job 171 (12:15 UTC) mógł się nie odpalić. "
                "Sprawdź `atq` / `journalctl` / odpal ręcznie: "
                "python3 -m dispatch_v2.tools.verify_pickup_floor_peak --label 'LUNCH 26.06' "
                "--since 2026-06-26T09:00:00 --until 2026-06-26T12:00:00 --notify")

    msg = (
        "PRZYPOMNIENIE — sprawdz werdykt LUNCH peak (committed-floor, rewire 9352c23).\n"
        "Kluczowe: sekcja 'C) committed-floor LIVE' — cel BUG (POKAZANY przed umowionym) = 0.\n"
        "FAIL / niezerowy BUG => rollback flaga ENABLE_PROPOSAL_ETA_FLOOR_TO_COMMITTED=false (hot).\n\n"
        + body
    )
    print(msg)
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        send_admin_alert(msg, source="lunch_floor_verdict_reminder")
        print("\n[Telegram wysłany]")
    except Exception as e:  # noqa: BLE001
        print(f"\n[Telegram fail: {e}]")


if __name__ == "__main__":
    main()
