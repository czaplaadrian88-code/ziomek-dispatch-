#!/bin/bash
# B2 okno 2 dni — werdykt (uruchamiane at-jobem pon. 20.07 ~09:00 Warsaw).
# READ-ONLY: liczy markery + re-run replayu A/B. Wynik → plik + echo.
OUT=/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-07-18/B2_WINDOW_VERDICT.txt
{
echo "B2 WINDOW VERDICT — $(date -u +%FT%TZ) (okno od 2026-07-18 ~11:00 UTC restart pw)"
echo ""
echo "== ADOPT/REJECT z journala pw (czysta atrybucja panel-watcher) =="
echo "pw ADOPT:  $(journalctl -u dispatch-panel-watcher --since '2026-07-18 11:00' 2>/dev/null | grep -c COMMITTED_TIEBREAK_ADOPT)"
echo "pw REJECT: $(journalctl -u dispatch-panel-watcher --since '2026-07-18 11:00' 2>/dev/null | grep -c COMMITTED_TIEBREAK_REJECT)"
echo ""
echo "== total w file-logu plan_recheck (pw+tick razem; baseline sprzed okna: 1047/632) =="
echo "ADOPT:  $(grep -c COMMITTED_TIEBREAK_ADOPT /root/.openclaw/workspace/scripts/logs/plan_recheck.log)"
echo "REJECT: $(grep -c COMMITTED_TIEBREAK_REJECT /root/.openclaw/workspace/scripts/logs/plan_recheck.log)"
echo ""
echo "== zdrowie pw od restartu (ERROR/Traceback w journalu) =="
echo "errors: $(journalctl -u dispatch-panel-watcher --since '2026-07-18 11:00' 2>/dev/null | grep -cE 'ERROR|Traceback')"
echo ""
echo "== re-run replay A/B (OFF vs ON na bieżących workach) =="
cd /root/.openclaw/workspace/scripts && timeout 300 /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/eod_drafts/2026-07-18/b2_committed_ab_replay.py 2>&1 | head -14
echo ""
echo "WERDYKT (ręcznie po odczycie): pw ADOPT >0 = fix żyje w pw; errors 0 + brak skarg = bez regresji;"
echo "różnice OFF↔ON w replayu = ile mrugania fix eliminuje na bieżącym stanie."
} > "$OUT" 2>&1
echo "verdict -> $OUT"
