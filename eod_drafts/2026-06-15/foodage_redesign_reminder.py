#!/usr/bin/env python3
"""Reminder (at-job) — START sprintu food-age REDESIGN 18.06, przed at#142 (21.06).
NIE wykonuje sprintu (robota ręczna z ACK) — tylko przypomina na cichym kanale.
priority="low" → @DajeszBot + panel Powiadomienia (to plan/termin, nie incydent).
Użycie: foodage_redesign_reminder.py [--dry-run]
"""
import argparse
import sys

SCRIPTS = "/root/.openclaw/workspace/scripts"
sys.path.insert(0, SCRIPTS)

MSG = (
    "🔧 *START SPRINTU food-age REDESIGN* (dziś 18.06, przed at#142 21.06).\n"
    "Drill-down 15.06: obecny additive coeff3 = 41 regresji SLA "
    "(56% poważnych, 88% genesis) → NIE flipować.\n"
    "Fix = twarde ograniczenie span SLA *single-solve* w tsp_solver (NIE 2-solve veto = trap latencji).\n"
    "Spec: `dispatch_v2/eod_drafts/2026-06-15/SPRINT_PLAN_foodage_hard_sla_redesign.md`\n"
    "Bramki ACK per faza. Replay-walidacja MUSI być przed 21.06 09:00.\n"
    "Slip → przesuń at#142, nie flipuj zepsutego."
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.dry_run:
        print("[DRY-RUN] wiadomość (NIE wysłana):\n" + MSG)
        return
    from dispatch_v2.telegram_utils import send_admin_alert
    ok = send_admin_alert(MSG, source="foodage-redesign-reminder", priority="low")
    print(f"[reminder] wysłany={ok}")


if __name__ == "__main__":
    main()
