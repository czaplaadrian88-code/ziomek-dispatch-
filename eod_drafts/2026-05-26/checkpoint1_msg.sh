#!/bin/bash
cd /root/.openclaw/workspace/scripts/dispatch_v2 && /root/.openclaw/venvs/dispatch/bin/python3 -c "
from dispatch_v2.telegram_utils import send_admin_alert
send_admin_alert('''🔴 CHECKPOINT #1 — Geometry/Fairness Bugs A/B (5 dni od diagnozy 26.05)

📋 Co sprawdzić:
1. BUG E (best_effort R6 → KOORD) — 4 dni live od ~27.05. Czy mniej propozycji z R6 breach?
2. Shadow A+B+C — ile decyzji w shadow_decisions.jsonl z metrykami sum_bag_time / max / fifo / r5_detour?
3. Q&A: czy mniej wpadek typu Case D/E/F/G w Telegramie?

📁 Plan: dispatch_v2/eod_drafts/2026-05-26/SPRINT_PLAN_geometry_fairness_bugs.md sekcja 6
📊 Decyzja: flip flag B live (BUG B pickup detour) od 02.06 jeśli OK.

Następny checkpoint: niedziela 07.06.2026 20:00 (pełna A/B)''')
"
