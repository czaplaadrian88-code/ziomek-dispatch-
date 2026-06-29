#!/usr/bin/env bash
# Weryfikacja A2 dwumodel-shadow po deployu at#157 (lokalnie, read-only). 2026-06-20.
# NIE flipuje flag, NIE restartuje, NIE rusza B3. Pisze raport do pliku.
set -uo pipefail
OUT=/root/TWOMODEL_SHADOW_VERIFY_2026-06-20.txt
PY=/root/.openclaw/venvs/dispatch/bin/python
: >"$OUT"
say(){ echo "$*" | tee -a "$OUT"; }

say "=== A2 dwumodel-shadow VERIFY @ $(date -u '+%H:%M UTC') / $(TZ=Europe/Warsaw date '+%H:%M Warsaw') ==="

say ""
say "--- 1) deploy status file ---"
if [ -f /root/TWOMODEL_SHADOW_DEPLOY_STATUS_2026-06-20.txt ]; then
  tail -6 /root/TWOMODEL_SHADOW_DEPLOY_STATUS_2026-06-20.txt | tee -a "$OUT"
  if grep -q "status: SUCCESS" /root/TWOMODEL_SHADOW_DEPLOY_STATUS_2026-06-20.txt; then
    say "VERDICT status-file: SUCCESS"
  else
    say "VERDICT status-file: ⚠ NIE-SUCCESS (sprawdź pełny plik!)"
  fi
else
  say "⚠ BRAK status file — deploy at#157 mógł się nie odpalić?"
fi

say ""
say "--- 2) dispatch-shadow active? ---"
ACT=$(systemctl is-active dispatch-shadow.service)
say "dispatch-shadow = $ACT $([ "$ACT" = active ] && echo OK || echo '⚠ PROBLEM')"

say ""
say "--- 3) flagi (oczekiwane: TWOMODEL_SHADOW=True, B3=True) ---"
$PY -c "import json;d=json.load(open('/root/.openclaw/workspace/scripts/flags.json'));print('ENABLE_LGBM_TWOMODEL_SHADOW=',d.get('ENABLE_LGBM_TWOMODEL_SHADOW'),'| B3_intact=',d.get('ENABLE_NO_GPS_UNCERTAINTY_PENALTY'))" 2>&1 | tee -a "$OUT"

say ""
say "--- 4) czy dwumodel loguje na ŻYWYCH decyzjach? (journal 30 min) ---"
N=$(journalctl -u dispatch-shadow.service --since "30 min ago" --no-pager 2>/dev/null | grep -c "LGBM_TWOMODEL_SHADOW")
say "LGBM_TWOMODEL_SHADOW log lines (30 min): $N"
journalctl -u dispatch-shadow.service --since "30 min ago" --no-pager 2>/dev/null | grep "LGBM_TWOMODEL_SHADOW" | tail -3 | tee -a "$OUT"
if [ "$N" -gt 0 ]; then
  say "VERDICT logging: ✅ dwumodel-shadow liczy na żywych decyzjach"
else
  say "VERDICT logging: ⏳ 0 linii w 30 min — albo cisza decyzyjna (off-peak), albo flaga/kod nie aktywne. Re-check przy ruchu."
fi
say "=== VERIFY DONE ==="
