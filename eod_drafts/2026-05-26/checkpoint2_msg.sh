#!/bin/bash
cd /root/.openclaw/workspace/scripts/dispatch_v2 && /root/.openclaw/venvs/dispatch/bin/python3 -c "
from dispatch_v2.telegram_utils import send_admin_alert
send_admin_alert('''🔴 CHECKPOINT #2 — pełna walidacja A/B przed/po (12 dni od diagnozy 26.05)

📋 Co porównać (tabele w sprint plan sekcja 6):
- Tydzień 19-25.05 (PRZED) vs 28.05-04.06 (PO BUG E hotfix) vs 02-07.06 (PO BUG B live)
- Cel: Σ bag_time -8%, max bag_time -5%, R6 breach -30%, best_effort PROPOSE -50%
- Próg regresji: SLA breach > +3% lub operator override > +3pp = ROLLBACK

📊 Decyzja:
- BUG B OK → flip flag A (Σ bag_time fairness) live od 08.06
- Regresja → flag B OFF hot-reload, debug

📁 Plan: dispatch_v2/eod_drafts/2026-05-26/SPRINT_PLAN_geometry_fairness_bugs.md

Następny opcjonalny: niedziela 14.06.2026 20:00 (BUG A live walidacja)''')
"
