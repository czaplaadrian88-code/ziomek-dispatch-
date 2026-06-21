#!/usr/bin/env bash
# HA-lite — AKTYWACJA PITR (archive_mode + WAL archive + base backup) na wspólnym
# kontenerze papu-postgres (Lokalka/Papu + panel + glitchtip).
#
# ⚠️ PROD-AFFECTING: zawiera RESTART papu-postgres (~kilka sekund blip dla Lokalki
# i panelu). URUCHAMIAJ TYLKO W OKNIE NISKIEGO RUCHU (noc, ~02:00–05:00 Warszawa).
# Niedziela/peak (11-14, 18-21) = NIE.
#
# Bezpieczeństwo: pre-flight, weryfikacja że WAL faktycznie się archiwizuje, oraz
# AUTO-ROLLBACK (archive_mode=off + restart) jeśli weryfikacja zawiedzie.
#
# Wymaga jawnej zgody: --yes-restart-now
set -euo pipefail

C=papu-postgres
U=papu
ADIR=/var/lib/postgresql/data/wal_archive          # ścieżka WEWNĄTRZ kontenera
BDIR=/var/lib/postgresql/data/base_backup
HOST_ADIR=/var/lib/docker/volumes/ordering_app_papu_pgdata/_data/wal_archive

say(){ echo "[$(date -u +%H:%M:%SZ)] $*"; }
psql_(){ docker exec "$C" psql -U "$U" -tAc "$1"; }

[ "${1:-}" = "--yes-restart-now" ] || { echo "ODMOWA: to RESTARTUJE bazę produkcyjną. Uruchom: $0 --yes-restart-now (w oknie nocnym)."; exit 2; }

HOUR=$(TZ=Europe/Warsaw date +%H); DOW=$(TZ=Europe/Warsaw date +%u)
if [ "$HOUR" -ge 10 ] && [ "$HOUR" -lt 22 ]; then
  echo "⚠️ OSTRZEŻENIE: $(TZ=Europe/Warsaw date) — to dzień/peak. Przerwij (Ctrl-C w 10s) albo poczekaj na noc."; sleep 10
fi

say "== PRE-FLIGHT =="
docker inspect "$C" --format '{{.State.Status}}' | grep -qx running || { echo "FATAL: $C nie działa"; exit 1; }
[ "$(psql_ "show archive_mode;")" = "off" ] || { echo "archive_mode już != off — przerywam (sprawdź ręcznie)."; exit 1; }
docker exec "$C" test -d "$ADIR" || { echo "FATAL: brak $ADIR (uruchom prep)"; exit 1; }
say "pre-flight OK (running, archive_mode=off, katalog jest)"

say "== USTAWIANIE archive_command + archive_mode (ALTER SYSTEM) =="
# Bezpieczny archive_command: nie nadpisuj istniejącego segmentu, kopiuj lokalnie.
psql_ "ALTER SYSTEM SET archive_command = 'test ! -f ${ADIR}/%f && cp %p ${ADIR}/%f';" >/dev/null
psql_ "ALTER SYSTEM SET archive_mode = 'on';" >/dev/null
say "ustawione (jeszcze nieaktywne — archive_mode wymaga restartu)"

say "== RESTART $C (blip kilka sekund) =="
docker restart "$C" >/dev/null
say "czekam aż baza wstanie..."
for i in $(seq 1 30); do docker exec "$C" pg_isready -U "$U" -q 2>/dev/null && break; sleep 1; done
docker exec "$C" pg_isready -U "$U" -q || { echo "FATAL: baza nie wstała po restarcie!"; exit 1; }
say "baza żyje; archive_mode=$(psql_ 'show archive_mode;')"

say "== WERYFIKACJA: wymuszam switch WAL i sprawdzam czy segment trafił do archiwum =="
BEFORE=$(docker exec "$C" bash -c "ls -1 $ADIR | wc -l")
psql_ "select pg_switch_wal();" >/dev/null
psql_ "checkpoint;" >/dev/null
sleep 5
AFTER=$(docker exec "$C" bash -c "ls -1 $ADIR | wc -l")
say "segmenty w archiwum: przed=$BEFORE po=$AFTER"

if [ "$AFTER" -gt "$BEFORE" ]; then
  say "✅ ARCHIWIZACJA DZIAŁA. Robię początkowy base backup (PITR ma od czego startować)..."
  docker exec "$C" bash -c "rm -rf ${BDIR}/* && pg_basebackup -U $U -D ${BDIR} -Ft -z -Xfetch -P" 2>&1 | tail -3
  say "base backup gotowy w ${BDIR} (trafi off-site przy najbliższym restic 03:30)"
  say "GOTOWE — PITR AKTYWNE. RPO ≈ minuty (od najbliższego restic) / cykl archiwizacji."
  say "Rollback gdyby trzeba: ALTER SYSTEM SET archive_mode=off; docker restart $C"
else
  echo "❌ WERYFIKACJA NIEUDANA — segment NIE trafił do archiwum. AUTO-ROLLBACK..."
  psql_ "ALTER SYSTEM SET archive_mode = 'off';" >/dev/null
  docker restart "$C" >/dev/null
  for i in $(seq 1 30); do docker exec "$C" pg_isready -U "$U" -q 2>/dev/null && break; sleep 1; done
  echo "ROLLBACK wykonany (archive_mode=$(psql_ 'show archive_mode;')). Zbadaj archive_command/uprawnienia $ADIR."
  # best-effort alert (jeśli helper istnieje)
  [ -x /root/.openclaw/workspace/scripts/send_admin_alert.py ] && \
    python3 /root/.openclaw/workspace/scripts/send_admin_alert.py "PITR activation FAILED — auto-rollback OK" 2>/dev/null || true
  exit 1
fi
