#!/bin/bash
# Start cienia D6a (at#219, 2026-07-18 19:05 UTC = 21:05 Warsaw, PO sobotnim peaku).
# Pre-ACK: GO Adriana na plan D1-D7 (cień 2 dni wymaga restartu dispatch-shadow,
# bo serving to nowy kod modułu). Fail-loud: każdy problem → SHADOW=false (hot)
# + raport; restart-back niepotrzebny (serving czysto obserwacyjny).
set -u
EOD=/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-07-18
REPORT=$EOD/ETA_SHADOW_START_REPORT.txt
LOG=/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl
{
echo "ETA-CALIB SHADOW START — $(date -u +%FT%TZ)"
systemctl restart dispatch-shadow
sleep 20
ST=$(systemctl is-active dispatch-shadow)
echo "dispatch-shadow: $ST"
ERR=$(journalctl -u dispatch-shadow --since '2 minutes ago' 2>/dev/null | grep -cE 'ERROR|Traceback')
echo "errors w journalu (2 min): $ERR"
if [ "$ST" != "active" ] || [ "$ERR" != "0" ]; then
  python3 -c "import json,os,tempfile; p='/root/.openclaw/workspace/scripts/flags.json'; d=json.load(open(p)); d['ENABLE_ETA_CALIB_PROMISE_SHADOW']=False; fd,t=tempfile.mkstemp(dir=os.path.dirname(p)); f=os.fdopen(fd,'w'); f.write(json.dumps(d,indent=2,ensure_ascii=False)); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(t,p)"
  echo "ABORT: restart niezdrowy -> SHADOW=false (hot); zostawiam do reki czlowieka"; exit 20
fi
echo "czekam na 1. rekord z eta_calib_promise_* (max 30 min; sobota ~21 = ruch jest)..."
DEADLINE=$(( $(date +%s) + 1800 ))
FOUND=0
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if tail -c 3000000 "$LOG" | grep -q 'eta_calib_promise_pickup_p80_min\|eta_calib_srv_skip'; then FOUND=1; break; fi
  sleep 30
done
if [ "$FOUND" = "1" ]; then
  echo "OK: metryki cienia obecne w shadow_decisions ($(date -u +%T)Z)"
  tail -c 3000000 "$LOG" | grep -o 'eta_calib_promise_pickup_p80_min[^,]*' | tail -3
  tail -c 3000000 "$LOG" | grep -o 'eta_calib_srv_skip[^,]*' | tail -3
  echo "CIEN URUCHOMIONY. Werdykt parytetu: at#220 (wt 05:30 UTC) -> eta_promise_parity.py"
else
  echo "UWAGA: 30 min bez metryk i bez skip — sprawdz flagi/ruch; SHADOW zostaje ON (obserwacyjny), do reki czlowieka"
fi
} > "$REPORT" 2>&1
echo "report -> $REPORT"
