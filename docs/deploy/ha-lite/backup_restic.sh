#!/bin/bash
# Daily restic backup → Hetzner Storage Box BX11 (SFTP, encrypted).
# Scope: dispatch_state + ML model v1.1 + datasets v2.0 + logs <14d + systemd units + flags + .env
#        + ordering_app/.env + /root/backups/papu (pg_dump'y z papu-db-backup.timer, audyt CTO 2026-05-20 #1)
#        + panel/backend/.env + /root/backups/nadajesz_panel (pg_dump'y z nadajesz-panel-backup.timer, audyt 2026-06-10)
# Retention: --keep-daily 7 --keep-weekly 4 --keep-monthly 6
# Cron: 03:30 Warsaw daily (= 01:30 UTC CEST / 02:30 UTC CET)
# Repo: sftp:bx11-storage:backups/ziomek-restic
# Passphrase: /root/.restic_password (mode 600); MUST be saved off-machine in password manager
set -euo pipefail

export RESTIC_PASSWORD_FILE=/root/.restic_password
export RESTIC_REPOSITORY="sftp:bx11-storage:backups/ziomek-restic"
# systemd uruchamia bez $HOME → restic nie znajduje cache ("unable to open cache").
# Bez cache backup działa, ale wolniej; ustawiamy jawnie by korzystać z lokalnego cache.
export HOME=${HOME:-/root}
export XDG_CACHE_HOME=${XDG_CACHE_HOME:-/root/.cache}

LOGDIR=/root/.openclaw/workspace/scripts/logs
LOGFILE="$LOGDIR/restic_backup.log"
mkdir -p "$LOGDIR"

ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "[$(ts)] $*" | tee -a "$LOGFILE"; }

trap_err() {
  local rc=$?
  log "FAIL exit=$rc"
  exit "$rc"
}
trap trap_err ERR

log "=== restic backup START ==="

# Build dynamic include list for "logs ostatnie 14 dni"
RECENT_LOGS=$(mktemp /tmp/restic_recent_logs.XXXXXX)
trap 'rm -f "$RECENT_LOGS"' EXIT
find /root/.openclaw/workspace/scripts/logs -type f -mtime -14 \
     ! -name 'restic_backup.log' \
     -print > "$RECENT_LOGS"
N_LOGS=$(wc -l < "$RECENT_LOGS" || echo 0)
log "logs<14d to back up: $N_LOGS files"

# Glob-expand wszystkie krytyczne jednostki systemd (services + timers + .d/ drop-ins).
# HA-lite audyt 2026-06-21: backup łapał TYLKO dispatch-* → panel/papu/courier/mailek
# nie były w off-site (odbudowa na świeżym hoście wymagałaby ręcznego odtwarzania
# nadajesz-panel.service, courier-api.service, nadajesz-ordering.service itd.).
shopt -s nullglob
SYSTEMD_PATHS=(/etc/systemd/system/dispatch-* /etc/systemd/system/nadajesz-* \
               /etc/systemd/system/papu-* /etc/systemd/system/courier-* \
               /etc/systemd/system/mailek-*)
shopt -u nullglob
log "systemd entries (dispatch+nadajesz+papu+courier+mailek): ${#SYSTEMD_PATHS[@]}"

restic backup \
  --tag daily \
  --tag scheduled \
  --exclude-caches \
  --exclude '*/__pycache__/*' \
  --exclude '*.pyc' \
  --exclude '*/.git/*' \
  --exclude '*.bak-*' \
  --exclude '*.backup' \
  --files-from-verbatim "$RECENT_LOGS" \
  /root/.openclaw/workspace/dispatch_state \
  /root/.openclaw/workspace/scripts/ml_data_prep/models/v1.1 \
  /root/.openclaw/workspace/scripts/ml_data_prep/data/datasets/v2.0 \
  /root/.openclaw/workspace/scripts/flags.json \
  /root/.openclaw/workspace/.env \
  /root/.openclaw/workspace/ordering_app/.env \
  /root/.openclaw/workspace/ordering_app/media \
  /root/.openclaw/workspace/nadajesz_clone/panel/backend/.env \
  /root/backups/papu \
  /root/backups/nadajesz_panel \
  /var/lib/docker/volumes/ordering_app_papu_pgdata/_data/base_backup \
  /var/lib/docker/volumes/ordering_app_papu_pgdata/_data/wal_archive \
  /etc/nginx/sites-available \
  "${SYSTEMD_PATHS[@]}" \
  2>&1 | tee -a "$LOGFILE"
  # UWAGA: /root/.openclaw/workspace/.secrets ŚWIADOMIE NIE backupowane off-site
  # (decyzja Adriana — hasła przez granicę zaufania). Muszą być w menedżerze haseł
  # off-machine, inaczej odbudowa na świeżym hoście wymaga ręcznego odtworzenia sekretów.

log "backup OK; applying retention"

# Zwolnij osierocone locki (po zabitym/równoległym procesie), inaczej forget pada exit=1
# mimo udanego snapshotu (incydent 2026-06-15). unlock usuwa locki >stale-threshold.
restic unlock 2>&1 | tee -a "$LOGFILE" || true

restic forget \
  --keep-daily 7 \
  --keep-weekly 4 \
  --keep-monthly 6 \
  --prune \
  2>&1 | tee -a "$LOGFILE"

log "retention OK"

# PITR (HA-lite 2026-06-21): przytnij zarchiwizowane segmenty WAL >8 dni (retencja >
# 7d nocnych dumpów/base — PITR sięga ~tydzień wstecz). Anty-zapchanie dysku, gdy
# archive_mode=on. No-op dopóki katalog pusty (archive_mode jeszcze off do okna nocnego).
WAL_ARCHIVE_DIR=/var/lib/docker/volumes/ordering_app_papu_pgdata/_data/wal_archive
if [ -d "$WAL_ARCHIVE_DIR" ]; then
  PRUNED=$(find "$WAL_ARCHIVE_DIR" -type f -mtime +8 -print -delete 2>/dev/null | wc -l)
  log "WAL archive prune (>8d): $PRUNED segmentów"
fi

# Light integrity check (full check is expensive over SFTP; do every 7th run via separate cron)
restic snapshots --compact 2>&1 | tee -a "$LOGFILE"

log "=== restic backup END ==="
