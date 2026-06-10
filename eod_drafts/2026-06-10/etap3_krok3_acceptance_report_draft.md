# ETAP 3 / Krok 3 — raport acceptance w daily_briefing.py (draft)

## Odkrycie przed implementacją
Spec zakładał „daily_briefing już ma crona" — NIEPRAWDA: zero wpisów w crontab/
systemd timers/at; log daily_briefing.log pusty (0 B) od 2026-05-08. Bez
harmonogramu DoD(3) „raport dzienny pokazuje acceptance_rate" nie zaistnieje
→ dopisuję 2 wpisy do user crontab (wzorzec istnieje: fetch_schedule,
daily_stats_sheets). To NIE jest nowy serwis. Rollback = usunąć 2 linie.
Backup: `crontab -l > /root/backups/crontab.bak-pre-etap3-briefing-2026-06-10`.

## Zmiany w daily_briefing.py
1. `_acceptance_line(lc)` — dzienny: `AGREE/(AGREE+OVERRIDE)` z Counter akcji
   (PANEL_AGREE liczy też source=telegram; ASSIGN_DIRECT NIE wchodzi do wzoru
   → zero podwójnego liczenia, edge c).
2. Sekcja „Acceptance 7d" w morning briefing (trailing 7 dni — tygodniowa
   widoczność bez nowego crona/serwisu):
   - overall AGREE/(AGREE+OVERRIDE)
   - per tier (AGREE: `proposed_tier` w rekordzie; OVERRIDE: `decision.best.
     dwell_tier` fallback `v319h_bug4_tier_cap_used.split('/')[0]`)
   - pora: peak 11-14/17-20 Warsaw (zgodnie z project_overview; NIE 12-14/18-20
     z klasyfikatora — Z-20) vs off, z ts rekordu
   - typ: czasówka = prep_min ≥ 60 (pickup_ready_at − order_created_at);
     elastyk < 60; brak danych → "?"
   - top-3 komponenty score OVERRIDE'owanych zwycięzców: z `decision.best`
     EMBEDOWANEGO w rekordzie PANEL_OVERRIDE (to ta sama decyzja co w
     shadow_decisions po order_id — pending_proposals.decision_record pochodzi
     z shadow; czytanie wprost z rekordu zamiast skanu wielkiego
     shadow_decisions.jsonl per briefing). Pola: numeric `bonus_*` bez
     {penalty_sum, *_raw, *_legacy, *shadow*} + timing_gap_bonus + bundle_bonus,
     ranking po |śr|, format „komponent śr ±X (n=Y)".
3. Evening: tylko dzienna linia acceptance.
4. Zero zmian w wysyłce/telegram_approver (cron = świeży proces; bez restartów).

## Testy
tests/test_daily_briefing_acceptance.py (pytest, tmp_path learning_log):
acceptance line z miksu AGREE/OVERRIDE; brak sekcji gdy 0 rekordów; tier/peak/typ
breakdown z syntetyków (AGREE-schema i OVERRIDE-schema); top-3 komponenty
(dominujący bonus_r4); filtr skip-pól.

## Crontab (komentarz: czasy UTC, Warsaw = UTC+2 lato)
0 6 * * *  cd /root/.openclaw/workspace/scripts && /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.daily_briefing morning  >> logs/daily_briefing_cron.log 2>&1
0 20 * * * cd /root/.openclaw/workspace/scripts && /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.daily_briefing evening >> logs/daily_briefing_cron.log 2>&1

## Workflow
.bak daily_briefing → edit → py_compile → pytest nowe + `--dry-run` oba tryby
→ commit + tag `briefing-acceptance-2026-06-10` → crontab (backup → dopis).
