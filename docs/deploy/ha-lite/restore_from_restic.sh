#!/usr/bin/env bash
# HA-lite: odtworzenie systemu z off-site restic (Hetzner BX11) — narzędzie DR/RTO.
# Audyt SaaS 2026-06-18 (bus factor = 1) → DR drill 2026-06-20 (restore udowodniony) →
# ten skrypt automatyzuje mechaniczną część odtworzenia (RTO z godzin → minut).
#
# BEZPIECZEŃSTWO: domyślnie restore do KATALOGU SCRATCH i NIE dotyka żywych baz.
# Załadowanie dumpów do Postgresa wymaga JAWNEGO --load-db + nazwy bazy docelowej;
# odmawia nadpisania baz produkcyjnych (nadajesz_panel/papu) bez --force.
#
# Użycie:
#   restore_from_restic.sh                          # restore najnowszego snapshotu do scratch + raport
#   restore_from_restic.sh --target /sciezka        # restore do wskazanego katalogu
#   restore_from_restic.sh --snapshot <id>          # konkretny snapshot (domyślnie latest)
#   restore_from_restic.sh --load-db <DBNAME>       # + załaduj dump panelu do bazy DBNAME (musi NIE istnieć, chyba że --force)
#   restore_from_restic.sh --verify-only            # tylko sprawdź dostęp+integralność, bez restore
set -euo pipefail

export RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-/root/.restic_password}"
export RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-sftp:bx11-storage:backups/ziomek-restic}"
export HOME="${HOME:-/root}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-/root/.cache}"

TS="$(date +%Y%m%d_%H%M%S)"
TARGET="/root/restore_${TS}"
SNAP="latest"
LOAD_DB=""
FORCE=0
VERIFY_ONLY=0
PG_CONTAINER="papu-postgres"
PG_USER="papu"

while [ $# -gt 0 ]; do
  case "$1" in
    --target)    TARGET="$2"; shift 2;;
    --snapshot)  SNAP="$2"; shift 2;;
    --load-db)   LOAD_DB="$2"; shift 2;;
    --force)     FORCE=1; shift;;
    --verify-only) VERIFY_ONLY=1; shift;;
    *) echo "Nieznany arg: $1" >&2; exit 2;;
  esac
done

echo "== restic repo: $RESTIC_REPOSITORY =="
if [ ! -r "$RESTIC_PASSWORD_FILE" ]; then
  echo "FATAL: brak hasła restic ($RESTIC_PASSWORD_FILE). Na świeżym hoście WPISZ je z menedżera haseł off-machine — bez niego repo jest nieodszyfrowywalne." >&2
  exit 1
fi

echo "== dostęp do repo + ostatnie snapshoty =="
restic snapshots --latest 3 --compact

if [ "$VERIFY_ONLY" = "1" ]; then
  echo "== integralność (restic check --read-data-subset 5%) =="
  restic check --read-data-subset=5% || { echo "CHECK FAILED" >&2; exit 1; }
  echo "OK: repo osiągalne i spójne (próbka 5%)."
  exit 0
fi

echo "== restore snapshotu '$SNAP' → $TARGET =="
mkdir -p "$TARGET"
restic restore "$SNAP" --target "$TARGET"

echo "== co odtworzono (skrót) =="
du -sh "$TARGET" 2>/dev/null | awk '{print "  rozmiar:",$1}'
PANEL_DUMP="$(ls -t "$TARGET"/root/backups/nadajesz_panel/nadajesz_panel_*.sql.gz 2>/dev/null | head -1 || true)"
PAPU_DUMP="$(ls -t "$TARGET"/root/backups/papu/*.sql.gz 2>/dev/null | head -1 || true)"
echo "  dump panelu: ${PANEL_DUMP:-BRAK}"
echo "  dump papu:   ${PAPU_DUMP:-BRAK}"
echo "  systemd units: $(ls "$TARGET"/etc/systemd/system/ 2>/dev/null | wc -l)"
echo "  nginx sites:   $(ls "$TARGET"/etc/nginx/sites-available/ 2>/dev/null | wc -l)"
[ -n "$PANEL_DUMP" ] && { gunzip -t "$PANEL_DUMP" && echo "  integralność dumpu panelu: OK"; }

if [ -n "$LOAD_DB" ]; then
  echo "== ładowanie dumpu panelu do bazy '$LOAD_DB' (kontener $PG_CONTAINER) =="
  if [ "$LOAD_DB" = "nadajesz_panel" ] || [ "$LOAD_DB" = "papu" ]; then
    if [ "$FORCE" != "1" ]; then
      echo "ODMOWA: '$LOAD_DB' to baza PRODUKCYJNA. Użyj --force świadomie (NADPISZE żywe dane)." >&2
      exit 3
    fi
    echo "  ⚠ --force: ładuję do PRODUKCYJNEJ '$LOAD_DB'"
  fi
  [ -z "$PANEL_DUMP" ] && { echo "BRAK dumpu panelu do załadowania" >&2; exit 4; }
  if docker exec "$PG_CONTAINER" psql -U "$PG_USER" -lqt 2>/dev/null | cut -d'|' -f1 | grep -qw "$LOAD_DB"; then
    [ "$FORCE" != "1" ] && { echo "ODMOWA: baza '$LOAD_DB' już istnieje. --force by nadpisać." >&2; exit 5; }
  else
    docker exec "$PG_CONTAINER" createdb -U "$PG_USER" "$LOAD_DB"
  fi
  gunzip -c "$PANEL_DUMP" | docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$LOAD_DB" -v ON_ERROR_STOP=0 -q
  echo "  załadowano. Sanity:"
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$LOAD_DB" -tc \
    "select 'tabele='||count(*) from information_schema.tables where table_schema='public';" | sed 's/^/    /'
fi

cat <<EOF

== NASTĘPNE KROKI (pełna odbudowa na świeżym hoście) ==
1. Kod z GitHub: git clone ziomek-dispatch- + nadajesz (panel) + podlaskie-papu-backend (papu).
2. Sekrety: odtwórz /root/.openclaw/workspace/.secrets/* z MENEDŻERA HASEŁ (NIE w tym backupie).
3. .env: są w $TARGET/root/.openclaw/workspace/**/.env → skopiuj na miejsce.
4. Postgres (docker): postaw kontener $PG_CONTAINER, potem ./restore_from_restic.sh --load-db nadajesz_panel --force (i papu).
5. systemd: skopiuj $TARGET/etc/systemd/system/* → /etc/systemd/system/, systemctl daemon-reload, enable --now.
6. nginx: skopiuj $TARGET/etc/nginx/sites-available/* , dowiąż sites-enabled, nginx -t && reload.
7. DNS/floating IP: przełącz na nowy host (krok ADRIANA).
Pełny runbook: /root/HA_LITE_RUNBOOK_2026-06-21.md
EOF
echo "GOTOWE."
