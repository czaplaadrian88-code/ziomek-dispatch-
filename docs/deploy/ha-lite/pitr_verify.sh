#!/usr/bin/env bash
# HA-lite — poranna weryfikacja aktywacji PITR (po nocnym pitr-activate-oneshot).
# Czyta log + stan bazy, wysyła werdykt na Telegram (kanał admina dispatchu) i loguje.
# Host-local (cloud nie ma dostępu do tych plików/dockera). Uruchamiany przez
# jednorazowy timer pitr-verify-oneshot.timer (~07:00 Warszawa).
set -uo pipefail

LOG=/root/.openclaw/workspace/scripts/logs/pitr_activate.log
VLOG=/root/.openclaw/workspace/scripts/logs/pitr_verify.log
PYBIN=/root/.openclaw/venvs/dispatch/bin/python
ADIR_HOST=/var/lib/docker/volumes/ordering_app_papu_pgdata/_data/wal_archive
BDIR_HOST=/var/lib/docker/volumes/ordering_app_papu_pgdata/_data/base_backup

AM=$(docker exec papu-postgres psql -U papu -tAc "show archive_mode;" 2>/dev/null | tr -d '[:space:]')
AM=${AM:-"?"}
WAL=$(ls -1 "$ADIR_HOST" 2>/dev/null | wc -l | tr -d ' ')
BASE=$(ls -1 "$BDIR_HOST" 2>/dev/null | wc -l | tr -d ' ')
MARK=$(grep -aE "PITR AKTYWNE|ROLLBACK|NIEUDANA|FATAL|ODMOWA|ARCHIWIZACJA" "$LOG" 2>/dev/null | tail -4)
[ -z "$MARK" ] && MARK="(brak markerów — czy timer aktywacji odpalił? journalctl -u pitr-activate-oneshot)"

if [ "$AM" = "on" ] && [ "$WAL" -gt 0 ] && [ "$BASE" -gt 0 ]; then
  STATUS="✅ PITR AKTYWNE — archive_mode=on, WAL archiwizowane ($WAL segm.), base_backup OK ($BASE plik). RPO ~24h → minuty."
  NEXT="Działa. base_backup + wal_archive trafią off-site przy najbliższym restic 03:30."
else
  STATUS="⚠️ PITR NIEAKTYWNE — archive_mode=$AM, wal_archive=$WAL, base_backup=$BASE → prawdopodobny auto-rollback lub aktywacja nie ruszyła."
  NEXT="Diagnoza: journalctl -u pitr-activate-oneshot --no-pager; tail -30 $LOG; sprawdź archive_command + uprawnienia $ADIR_HOST. Baza działa normalnie (rollback przywraca stan sprzed)."
fi

MSG="🌙 Weryfikacja PITR (poranek 22.06)

$STATUS

Następny krok: $NEXT

Markery z logu aktywacji:
$MARK"

echo "[$(date -u +%FT%TZ)] $MSG" >> "$VLOG"
cd /root/.openclaw/workspace/scripts && \
  "$PYBIN" -c "import sys; from dispatch_v2 import telegram_utils as t; ok=t.send_admin_alert(sys.argv[1], source='pitr_verify', priority='high'); print('telegram_sent='+str(ok))" "$MSG" \
  2>&1 | tee -a "$VLOG"
