#!/usr/bin/env bash
# A360-DR0: fail-closed restore z off-site restic do izolowanego scratcha.
#
# Ten skrypt NIGDY nie przyjmuje istniejacego kontenera ani produkcyjnej bazy.
# W trybie drill sam tworzy kontener PostgreSQL + volume oznaczone run_id,
# bez sieci i portow, a po smoke usuwa oba zasoby. Odtworzone pliki zostaja
# w prywatnym scratchu do jawnego rollbacku operatora.
set -Eeuo pipefail
umask 077

export RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-/root/.restic_password}"
export RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-sftp:bx11-storage:backups/ziomek-restic}"
export HOME="${HOME:-/root}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-/root/.cache}"
unset PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD PGSERVICE PGSERVICEFILE PSQLRC

TEST_MODE=0
if [ "${DISPATCH_UNDER_PYTEST:-0}" = "1" ] && [ "${A360_TEST_MODE:-0}" = "1" ] \
  && [ -n "${PYTEST_CURRENT_TEST:-}" ]; then
  PARENT_CMDLINE="$(tr '\000' ' ' <"/proc/$PPID/cmdline" 2>/dev/null || true)"
  case "$PARENT_CMDLINE" in
    *pytest*) TEST_MODE=1 ;;
  esac
fi

if [ "$TEST_MODE" = "1" ]; then
  RESTIC_BIN="${A360_RESTIC_BIN:?A360_RESTIC_BIN is required in test mode}"
  DOCKER_BIN="${A360_DOCKER_BIN:?A360_DOCKER_BIN is required in test mode}"
  OPENSSL_BIN="${A360_OPENSSL_BIN:?A360_OPENSSL_BIN is required in test mode}"
  GZIP_BIN="${A360_GZIP_BIN:-gzip}"
  SQLITE_BIN="${A360_SQLITE_BIN:-sqlite3}"
  PYTHON_BIN="${A360_PYTHON_BIN:-python3}"
else
  PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
  export PATH
  RESTIC_BIN="restic"
  DOCKER_BIN="docker"
  OPENSSL_BIN="openssl"
  GZIP_BIN="gzip"
  SQLITE_BIN="sqlite3"
  PYTHON_BIN="python3"
fi

usage() {
  printf '%s\n' \
    'Uzycie:' \
    '  restore_from_restic.sh --mode verify [--snapshot ID]' \
    '  restore_from_restic.sh --mode artifact [--snapshot ID] [--target SCRATCH]' \
    '  restore_from_restic.sh --mode drill --pg-image IMAGE@sha256:DIGEST [opcje]' \
    '' \
    'Opcje:' \
    '  --papu-format auto|plain|encrypted  (domyslnie auto: globalnie najnowszy)' \
    '  --target PATH                       (nowy leaf pod A360 scratch root)' \
    '  --snapshot ID                       (domyslnie latest, przypinany do ID)' \
    '  --pg-image IMAGE@sha256:DIGEST       (wymagany tylko dla drill)' \
    '' \
    'Wymagane limity bajtow:' \
    '  A360_DR0_SCRATCH_BUDGET_BYTES       (verify, artifact i drill)' \
    '  A360_DR0_DOCKER_BUDGET_BYTES        (drill)'
}

PHASE="CLI"
FAIL_EMITTED=0
FINALIZED=0
TARGET_CREATED=0
CONTAINER_CREATED=0
VOLUME_CREATED=0
DOCKER_CLEANUP_ARMED=0
CLEANUP_OK=1
TARGET=""
OWNER_FILE=""
CONTAINER_NAME=""
VOLUME_NAME=""
RUN_ID=""
SNAP_META=""
STATS_META=""
VERIFY_CACHE=""

fail() {
  local reason="$1"
  local code="${2:-1}"
  FAIL_EMITTED=1
  printf 'RED phase=%s reason=%s\n' "$PHASE" "$reason" >&2
  exit "$code"
}

safe_remove_target() {
  [ "$TARGET_CREATED" = "1" ] || return 0
  [ -n "$TARGET" ] && [ -n "$OWNER_FILE" ] || return 1
  [ -f "$OWNER_FILE" ] && [ ! -L "$OWNER_FILE" ] || return 1
  local owner=""
  IFS= read -r owner < "$OWNER_FILE" || return 1
  [ "$owner" = "$RUN_ID" ] || return 1
  rm -rf --one-file-system -- "$TARGET"
  TARGET_CREATED=0
}

cleanup_docker() {
  local run_label="" scratch_label="" resource_name="" daemon_ok=1
  CLEANUP_OK=1
  [ "$DOCKER_CLEANUP_ARMED" = "1" ] || return 0

  if "$DOCKER_BIN" inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    resource_name="$($DOCKER_BIN inspect -f '{{.Name}}' "$CONTAINER_NAME" 2>/dev/null || true)"
    run_label="$($DOCKER_BIN inspect -f '{{ index .Config.Labels "a360.dr0.run_id" }}' "$CONTAINER_NAME" 2>/dev/null || true)"
    scratch_label="$($DOCKER_BIN inspect -f '{{ index .Config.Labels "a360.dr0.scratch" }}' "$CONTAINER_NAME" 2>/dev/null || true)"
    if [ "$resource_name" = "/$CONTAINER_NAME" ] \
      && [ "$run_label" = "$RUN_ID" ] && [ "$scratch_label" = "true" ]; then
      "$DOCKER_BIN" rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || CLEANUP_OK=0
      if [ "$CLEANUP_OK" = "1" ]; then
        if "$DOCKER_BIN" inspect "$CONTAINER_NAME" >/dev/null 2>&1 \
          || ! "$DOCKER_BIN" info >/dev/null 2>&1; then
          CLEANUP_OK=0
        else
          CONTAINER_CREATED=0
        fi
      fi
    else
      CLEANUP_OK=0
    fi
  else
    "$DOCKER_BIN" info >/dev/null 2>&1 || daemon_ok=0
    if [ "$daemon_ok" = "1" ] && ! "$DOCKER_BIN" inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
      CONTAINER_CREATED=0
    else
      CLEANUP_OK=0
    fi
  fi

  daemon_ok=1
  if [ "$CLEANUP_OK" = "1" ] && "$DOCKER_BIN" volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
    resource_name="$($DOCKER_BIN volume inspect -f '{{.Name}}' "$VOLUME_NAME" 2>/dev/null || true)"
    run_label="$($DOCKER_BIN volume inspect -f '{{ index .Labels "a360.dr0.run_id" }}' "$VOLUME_NAME" 2>/dev/null || true)"
    scratch_label="$($DOCKER_BIN volume inspect -f '{{ index .Labels "a360.dr0.scratch" }}' "$VOLUME_NAME" 2>/dev/null || true)"
    if [ "$resource_name" = "$VOLUME_NAME" ] \
      && [ "$run_label" = "$RUN_ID" ] && [ "$scratch_label" = "true" ]; then
      "$DOCKER_BIN" volume rm "$VOLUME_NAME" >/dev/null 2>&1 || CLEANUP_OK=0
      if [ "$CLEANUP_OK" = "1" ]; then
        if "$DOCKER_BIN" volume inspect "$VOLUME_NAME" >/dev/null 2>&1 \
          || ! "$DOCKER_BIN" info >/dev/null 2>&1; then
          CLEANUP_OK=0
        else
          VOLUME_CREATED=0
        fi
      fi
    else
      CLEANUP_OK=0
    fi
  elif [ "$CLEANUP_OK" = "1" ]; then
    "$DOCKER_BIN" info >/dev/null 2>&1 || daemon_ok=0
    if [ "$daemon_ok" = "1" ] && ! "$DOCKER_BIN" volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
      VOLUME_CREATED=0
    else
      CLEANUP_OK=0
    fi
  fi
  if [ "$CLEANUP_OK" = "1" ] && [ "$CONTAINER_CREATED" = "0" ] && [ "$VOLUME_CREATED" = "0" ]; then
    DOCKER_CLEANUP_ARMED=0
  fi
  [ "$CLEANUP_OK" = "1" ]
}

on_exit() {
  local rc="$1"
  local cleanup_failed=0
  trap - EXIT
  if [ "$FINALIZED" != "1" ]; then
    set +e
    if [ -n "$SNAP_META" ] && [ -f "$SNAP_META" ]; then
      rm -f -- "$SNAP_META"
    fi
    if [ -n "$STATS_META" ] && [ -f "$STATS_META" ]; then
      rm -f -- "$STATS_META"
    fi
    if [ -n "$VERIFY_CACHE" ] && [ -d "$VERIFY_CACHE" ] && [ ! -L "$VERIFY_CACHE" ]; then
      case "$VERIFY_CACHE" in
        "$SCRATCH_ROOT"/.verify_cache.*) rm -rf --one-file-system -- "$VERIFY_CACHE" ;;
        *) cleanup_failed=1 ;;
      esac
    fi
    cleanup_docker >/dev/null 2>&1 || cleanup_failed=1
    safe_remove_target >/dev/null 2>&1 || cleanup_failed=1
    if [ "$rc" -ne 0 ] && [ "$FAIL_EMITTED" != "1" ]; then
      printf 'RED phase=%s reason=unexpected_failure\n' "$PHASE" >&2
    fi
    if [ "$cleanup_failed" = "1" ]; then
      printf 'RED phase=CLEANUP reason=scratch_rollback_incomplete\n' >&2
      rc=90
    fi
  fi
  exit "$rc"
}
trap 'on_exit $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

MODE=""
SNAP="latest"
PAPU_FORMAT="auto"
PG_IMAGE=""
SCRATCH_ROOT="${A360_DR0_SCRATCH_ROOT:-/root/a360_dr0_scratch}"
REAL_MAX_RPO_SECONDS=93600
REAL_MAX_ARTIFACT_AGE_SECONDS=93600
REAL_MIN_FREE_RESERVE_BYTES=5368709120
REAL_MIN_MEMORY_BYTES=3221225472
REAL_MIN_PG_TABLES=50
REAL_REPOSITORY_CACHE_ALLOWANCE_BYTES=2147483648
if [ "$TEST_MODE" = "1" ]; then
  MAX_RPO_SECONDS="${A360_TEST_MAX_RPO_SECONDS:-$REAL_MAX_RPO_SECONDS}"
  MAX_ARTIFACT_AGE_SECONDS="${A360_TEST_MAX_ARTIFACT_AGE_SECONDS:-$REAL_MAX_ARTIFACT_AGE_SECONDS}"
  MIN_FREE_RESERVE_BYTES="${A360_TEST_MIN_FREE_RESERVE_BYTES:-$REAL_MIN_FREE_RESERVE_BYTES}"
  MIN_MEMORY_BYTES="${A360_TEST_MIN_MEMORY_BYTES:-$REAL_MIN_MEMORY_BYTES}"
  MIN_PG_TABLES="${A360_TEST_MIN_PG_TABLES:-$REAL_MIN_PG_TABLES}"
  REPOSITORY_CACHE_ALLOWANCE_BYTES="${A360_TEST_REPOSITORY_CACHE_ALLOWANCE_BYTES:-$REAL_REPOSITORY_CACHE_ALLOWANCE_BYTES}"
else
  # Profil real jest source-pinned. Zmienne srodowiskowe nie moga oslabic
  # wieku, rezerwy, pamieci ani minimalnej tozsamosci schematu.
  MAX_RPO_SECONDS="$REAL_MAX_RPO_SECONDS"
  MAX_ARTIFACT_AGE_SECONDS="$REAL_MAX_ARTIFACT_AGE_SECONDS"
  MIN_FREE_RESERVE_BYTES="$REAL_MIN_FREE_RESERVE_BYTES"
  MIN_MEMORY_BYTES="$REAL_MIN_MEMORY_BYTES"
  MIN_PG_TABLES="$REAL_MIN_PG_TABLES"
  REPOSITORY_CACHE_ALLOWANCE_BYTES="$REAL_REPOSITORY_CACHE_ALLOWANCE_BYTES"
fi
readonly MAX_RPO_SECONDS MAX_ARTIFACT_AGE_SECONDS MIN_FREE_RESERVE_BYTES
readonly MIN_MEMORY_BYTES MIN_PG_TABLES REPOSITORY_CACHE_ALLOWANCE_BYTES
PG_READY_TIMEOUT="${A360_DR0_PG_READY_TIMEOUT_SECONDS:-30}"
PAPU_BACKUP_KEY_FILE="${PAPU_BACKUP_KEY_FILE:-}"
SCRATCH_BUDGET_BYTES="${A360_DR0_SCRATCH_BUDGET_BYTES:-}"
DOCKER_BUDGET_BYTES="${A360_DR0_DOCKER_BUDGET_BYTES:-}"

# Wersjonowany minimalny kontrakt odtworzenia. Obecnosc plikow prywatnych i
# tozsamosci jest sprawdzana wylacznie przez metadane (typ, size, link count).
# Skrypt nigdy nie otwiera ich tresci i nie raportuje nazw ani wartosci.
REQUIRED_ARTIFACT_CONTRACT_VERSION="a360-dr0-required-artifacts-v1-20260711"
PRIVATE_CONFIG_BASENAME=".en""v"
REQUIRED_CORE_RELATIVE_PATHS=(
  "root/.openclaw/workspace/dispatch_state/orders_state.json"
  "root/.openclaw/workspace/dispatch_state/courier_plans.json"
  "root/.openclaw/workspace/dispatch_state/events.db"
  "root/.openclaw/workspace/scripts/flags.json"
)
REQUIRED_PRIVATE_METADATA_RELATIVE_PATHS=(
  "root/.openclaw/workspace/dispatch_state/kurier_ids.json"
  "root/.openclaw/workspace/dispatch_state/kurier_piny.json"
  "root/.openclaw/workspace/dispatch_state/courier_names.json"
  "root/.openclaw/workspace/dispatch_state/courier_tiers.json"
  "root/.openclaw/workspace/dispatch_state/grafik_full_names.json"
  "root/.openclaw/workspace/$PRIVATE_CONFIG_BASENAME"
  "root/.openclaw/workspace/ordering_app/$PRIVATE_CONFIG_BASENAME"
  "root/.openclaw/workspace/nadajesz_clone/panel/backend/$PRIVATE_CONFIG_BASENAME"
)
REQUIRED_SYSTEMD_RELATIVE_PATHS=(
  "etc/systemd/system/dispatch-shadow.service"
  "etc/systemd/system/dispatch-panel-watcher.service"
  "etc/systemd/system/dispatch-sla-tracker.service"
  "etc/systemd/system/dispatch-gps.service"
  "etc/systemd/system/dispatch-telegram.service"
  "etc/systemd/system/nadajesz-panel.service"
  "etc/systemd/system/nadajesz-ordering.service"
  "etc/systemd/system/courier-api.service"
  "etc/systemd/system/papu-backend.service"
  "etc/systemd/system/papu-backend-2.service"
  "etc/systemd/system/papu-notifications-worker.service"
  "etc/systemd/system/dispatch-restic-backup.service"
  "etc/systemd/system/dispatch-restic-backup.timer"
  "etc/systemd/system/nadajesz-panel-backup.service"
  "etc/systemd/system/nadajesz-panel-backup.timer"
  "etc/systemd/system/papu-db-backup.service"
  "etc/systemd/system/papu-db-backup.timer"
  "etc/systemd/system/backup-sentinel.service"
  "etc/systemd/system/backup-sentinel.timer"
)
REQUIRED_NGINX_RELATIVE_PATHS=(
  "etc/nginx/sites-available/gps-nadajesz"
  "etc/nginx/sites-available/lokalka"
  "etc/nginx/sites-available/bialystok-nadajesz"
)
EXPECTED_CORE_ARTIFACTS=4
EXPECTED_PRIVATE_METADATA_ARTIFACTS=8
EXPECTED_SYSTEMD_ARTIFACTS=19
EXPECTED_NGINX_ARTIFACTS=3
SNAPSHOT_PROVENANCE_CONTRACT_VERSION="a360-dr0-snapshot-provenance-v1-20260711"
EXPECTED_SNAPSHOT_HOSTNAME="Ziomek"
REQUIRED_SNAPSHOT_TAGS=("daily" "scheduled")
REQUIRED_SNAPSHOT_PATHS=(
  "/root/.openclaw/workspace/dispatch_state"
  "/root/.openclaw/workspace/scripts/flags.json"
  "/root/backups/papu"
  "/root/backups/nadajesz_panel"
  "/etc/nginx/sites-available"
)
EXPECTED_SNAPSHOT_TAGS=2
EXPECTED_SNAPSHOT_PATHS=5

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode)
      [ "$#" -ge 2 ] || fail "missing_mode_value" 2
      MODE="$2"
      shift 2
      ;;
    --snapshot)
      [ "$#" -ge 2 ] || fail "missing_snapshot_value" 2
      SNAP="$2"
      shift 2
      ;;
    --target)
      [ "$#" -ge 2 ] || fail "missing_target_value" 2
      TARGET="$2"
      shift 2
      ;;
    --papu-format)
      [ "$#" -ge 2 ] || fail "missing_papu_format_value" 2
      PAPU_FORMAT="$2"
      shift 2
      ;;
    --pg-image)
      [ "$#" -ge 2 ] || fail "missing_pg_image_value" 2
      PG_IMAGE="$2"
      shift 2
      ;;
    --force|--load-db|--panel-db|--papu-db|--pg-container)
      fail "unsafe_legacy_option_rejected" 2
      ;;
    -h|--help)
      usage
      FINALIZED=1
      exit 0
      ;;
    *)
      fail "unknown_option" 2
      ;;
  esac
done

case "$MODE" in
  verify|artifact|drill) ;;
  "") usage >&2; fail "explicit_mode_required" 2 ;;
  *) fail "invalid_mode" 2 ;;
esac
case "$PAPU_FORMAT" in
  auto|plain|encrypted) ;;
  *) fail "invalid_papu_format" 2 ;;
esac
[[ "$MAX_RPO_SECONDS" =~ ^[0-9]+$ ]] || fail "invalid_snapshot_age_limit" 2
[[ "$MAX_ARTIFACT_AGE_SECONDS" =~ ^[0-9]+$ ]] || fail "invalid_artifact_age_limit" 2
[[ "$MIN_FREE_RESERVE_BYTES" =~ ^[0-9]+$ ]] || fail "invalid_disk_reserve" 2
[[ "$MIN_MEMORY_BYTES" =~ ^[0-9]+$ ]] || fail "invalid_memory_limit" 2
[[ "$MIN_PG_TABLES" =~ ^[0-9]+$ ]] && [ "$MIN_PG_TABLES" -gt 0 ] || fail "invalid_pg_table_floor" 2
[[ "$PG_READY_TIMEOUT" =~ ^[0-9]+$ ]] || fail "invalid_pg_ready_timeout" 2
[ "$PG_READY_TIMEOUT" -ge 1 ] && [ "$PG_READY_TIMEOUT" -le 60 ] || fail "invalid_pg_ready_timeout" 2

validate_uint63() {
  "$PYTHON_BIN" -c '
import sys

try:
    value = int(sys.argv[1], 10)
except ValueError:
    raise SystemExit(1)
raise SystemExit(0 if 0 < value <= (2**63 - 1) else 1)
' "$1" >/dev/null 2>&1
}
checked_capacity() {
  "$PYTHON_BIN" -c '
import sys

try:
    payload = int(sys.argv[1], 10)
    factor = int(sys.argv[2], 10)
    reserve = int(sys.argv[3], 10)
except ValueError:
    raise SystemExit(1)
result = payload * factor + reserve
if payload < 0 or factor <= 0 or reserve < 0 or result > (2**63 - 1):
    raise SystemExit(1)
print(result)
' "$1" "$2" "$3" 2>/dev/null
}
validate_manifest_definition() {
  local expected="$1"
  shift
  [ "$#" -eq "$expected" ] || fail "required_contract_definition_invalid" 2
  local relative="" seen=$'\n'
  for relative in "$@"; do
    [ -n "$relative" ] && [[ "$relative" =~ ^[A-Za-z0-9._/-]+$ ]] \
      || fail "required_contract_definition_invalid" 2
    case "$relative" in
      /*|./*|../*|*/../*|*/..|*//* ) fail "required_contract_definition_invalid" 2 ;;
    esac
    case "$seen" in
      *$'\n'"$relative"$'\n'*) fail "required_contract_definition_invalid" 2 ;;
    esac
    seen+="$relative"$'\n'
  done
}
validate_provenance_definition() {
  local expected="$1" kind="$2"
  shift 2
  [ "$#" -eq "$expected" ] || fail "snapshot_provenance_definition_invalid" 2
  local value="" seen=$'\n'
  for value in "$@"; do
    [ -n "$value" ] && [[ "$value" =~ ^[A-Za-z0-9._/-]+$ ]] \
      || fail "snapshot_provenance_definition_invalid" 2
    if [ "$kind" = "path" ]; then
      case "$value" in
        /*) ;;
        *) fail "snapshot_provenance_definition_invalid" 2 ;;
      esac
      case "$value" in
        *//*|*/../*|*/..) fail "snapshot_provenance_definition_invalid" 2 ;;
      esac
    fi
    case "$seen" in
      *$'\n'"$value"$'\n'*) fail "snapshot_provenance_definition_invalid" 2 ;;
    esac
    seen+="$value"$'\n'
  done
}

validate_manifest_definition "$EXPECTED_CORE_ARTIFACTS" "${REQUIRED_CORE_RELATIVE_PATHS[@]}"
validate_manifest_definition "$EXPECTED_PRIVATE_METADATA_ARTIFACTS" "${REQUIRED_PRIVATE_METADATA_RELATIVE_PATHS[@]}"
validate_manifest_definition "$EXPECTED_SYSTEMD_ARTIFACTS" "${REQUIRED_SYSTEMD_RELATIVE_PATHS[@]}"
validate_manifest_definition "$EXPECTED_NGINX_ARTIFACTS" "${REQUIRED_NGINX_RELATIVE_PATHS[@]}"
[[ "$EXPECTED_SNAPSHOT_HOSTNAME" =~ ^[A-Za-z0-9._-]+$ ]] \
  || fail "snapshot_provenance_definition_invalid" 2
validate_provenance_definition "$EXPECTED_SNAPSHOT_TAGS" tag "${REQUIRED_SNAPSHOT_TAGS[@]}"
validate_provenance_definition "$EXPECTED_SNAPSHOT_PATHS" path "${REQUIRED_SNAPSHOT_PATHS[@]}"
validate_uint63 "$SCRATCH_BUDGET_BYTES" || fail "scratch_budget_required_or_invalid" 2
validate_uint63 "$REPOSITORY_CACHE_ALLOWANCE_BYTES" || fail "invalid_repository_cache_allowance" 2
if [ "$MODE" = "drill" ]; then
  validate_uint63 "$DOCKER_BUDGET_BYTES" || fail "docker_budget_required_or_invalid" 2
fi

if [ "$TEST_MODE" != "1" ] && [ "$SCRATCH_ROOT" != "/root/a360_dr0_scratch" ]; then
  fail "scratch_root_override_requires_test_mode" 2
fi
if [ "$MODE" = "drill" ]; then
  [ -n "$PG_IMAGE" ] || fail "pinned_pg_image_required" 2
  [[ "$PG_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || fail "unpinned_pg_image_rejected" 2
elif [ -n "$PG_IMAGE" ]; then
  fail "pg_image_only_valid_for_drill" 2
fi

for bin in "$RESTIC_BIN" "$PYTHON_BIN"; do
  command -v "$bin" >/dev/null 2>&1 || fail "required_tool_missing"
done
if [ "$MODE" != "verify" ]; then
  for bin in "$GZIP_BIN" "$SQLITE_BIN"; do
    command -v "$bin" >/dev/null 2>&1 || fail "required_tool_missing"
  done
fi
if [ "$MODE" = "drill" ]; then
  command -v "$DOCKER_BIN" >/dev/null 2>&1 || fail "isolated_docker_unavailable" 20
fi

PHASE="PREFLIGHT"
validate_private_input() {
  local path="$1" reason="$2" mode="" owner="" resolved=""
  [[ "$path" = /* ]] || fail "$reason"
  [ -f "$path" ] && [ ! -L "$path" ] && [ -r "$path" ] || fail "$reason"
  resolved="$(readlink -e -- "$path" 2>/dev/null)" || fail "$reason"
  [ "$resolved" = "$path" ] || fail "$reason"
  owner="$(stat -c '%u' -- "$path")" || fail "$reason"
  mode="$(stat -c '%a' -- "$path")" || fail "$reason"
  [ "$owner" = "$(id -u)" ] || fail "$reason"
  case "$mode" in
    400|600) ;;
    *) fail "$reason" ;;
  esac
}

monotonic_ms() {
  "$PYTHON_BIN" -c 'import time; print(time.monotonic_ns() // 1000000)'
}

validate_private_input "$RESTIC_PASSWORD_FILE" "restic_credential_unavailable_or_unsafe"
START_EPOCH="$(date +%s)"
START_MS="$(monotonic_ms)"
RUN_ID="${A360_TEST_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)_$$}"
[[ "$RUN_ID" =~ ^[A-Za-z0-9_-]{6,48}$ ]] || fail "invalid_run_id"
RUN_DB_TOKEN="${RUN_ID//-/_}"
CONTAINER_NAME="a360-dr0-pg-${RUN_ID}"
VOLUME_NAME="a360_dr0_pgdata_${RUN_DB_TOKEN}"
PANEL_DB="a360_dr0_panel_${RUN_DB_TOKEN}"
PAPU_DB="a360_dr0_papu_${RUN_DB_TOKEN}"

free_bytes_for_path() {
  local path="$1" probe="${2:-initial}"
  if [ "$TEST_MODE" = "1" ] && [ "$probe" = "repository_post" ] \
    && [ -n "${A360_TEST_FREE_BYTES_AFTER_REPOSITORY_CHECK:-}" ]; then
    printf '%s' "$A360_TEST_FREE_BYTES_AFTER_REPOSITORY_CHECK"
  elif [ "$TEST_MODE" = "1" ] && [ -n "${A360_TEST_FREE_BYTES:-}" ]; then
    printf '%s' "$A360_TEST_FREE_BYTES"
  else
    df -B1 --output=avail -- "$path" | awk 'NR == 2 {gsub(/ /, ""); print}'
  fi
}

HOST_GUARD_CALLS=0
assert_host_capacity() {
  HOST_GUARD_CALLS=$((HOST_GUARD_CALLS + 1))
  if [ "$TEST_MODE" = "1" ]; then
    TEST_CONFLICT="${A360_TEST_CONFLICT_PROCESS:-0}"
    if [ "$HOST_GUARD_CALLS" -gt 2 ]; then
      TEST_CONFLICT="${A360_TEST_THIRD_CONFLICT_PROCESS:-$TEST_CONFLICT}"
    elif [ "$HOST_GUARD_CALLS" -gt 1 ]; then
      TEST_CONFLICT="${A360_TEST_SECOND_CONFLICT_PROCESS:-$TEST_CONFLICT}"
    fi
    [ "$TEST_CONFLICT" = "0" ] || fail "concurrent_heavy_job_detected"
    LOAD1="${A360_TEST_LOAD1:-0}"
    CPU_COUNT="${A360_TEST_CPU_COUNT:-4}"
    MEM_AVAILABLE_BYTES="${A360_TEST_MEM_AVAILABLE_BYTES:-8589934592}"
  else
    if "$PYTHON_BIN" -c '
import glob
import os

for cmdline_path in glob.glob("/proc/[0-9]*/cmdline"):
    proc_dir = os.path.dirname(cmdline_path)
    try:
        with open(os.path.join(proc_dir, "comm"), "rb") as handle:
            comm = handle.read().strip()
    except OSError:
        continue
    if comm in {b"restic", b"pg_dump", b"pg_basebackup"}:
        raise SystemExit(0)
    try:
        with open(cmdline_path, "rb") as handle:
            argv = [part for part in handle.read().split(b"\0") if part]
    except OSError:
        continue
    if any(part == b"backup_restic.sh" or part.endswith(b"/backup_restic.sh")
           for part in argv):
        raise SystemExit(0)
    if not (comm.startswith(b"python") or comm.startswith(b"pytest")):
        continue
    if b"tests/" in argv and (comm.startswith(b"pytest") or b"pytest" in argv):
        raise SystemExit(0)
raise SystemExit(1)
'; then
      fail "concurrent_heavy_job_detected"
    fi
    LOAD1="$(awk '{print $1}' /proc/loadavg)"
    CPU_COUNT="$(nproc)"
    MEM_AVAILABLE_BYTES="$(awk '/^MemAvailable:/ {printf "%.0f", $2 * 1024}' /proc/meminfo)"
  fi
  [[ "$CPU_COUNT" =~ ^[0-9]+$ ]] && [ "$CPU_COUNT" -gt 0 ] || fail "host_capacity_probe_failed"
  [[ "$MEM_AVAILABLE_BYTES" =~ ^[0-9]+$ ]] || fail "host_capacity_probe_failed"
  "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if float(sys.argv[1]) <= int(sys.argv[2]) * 0.5 else 1)' \
    "$LOAD1" "$CPU_COUNT" || fail "host_load_too_high"
  [ "$MEM_AVAILABLE_BYTES" -ge "$MIN_MEMORY_BYTES" ] || fail "host_memory_too_low"
}

assert_host_capacity

[ ! -L "$SCRATCH_ROOT" ] || fail "scratch_root_symlink_rejected"
mkdir -p -- "$SCRATCH_ROOT"
chmod 0700 -- "$SCRATCH_ROOT"
[ "$(stat -c '%a' -- "$SCRATCH_ROOT")" = "700" ] || fail "scratch_root_permissions"
SCRATCH_CANON="$(readlink -m -- "$SCRATCH_ROOT")"
[ "$SCRATCH_CANON" = "$SCRATCH_ROOT" ] || fail "scratch_root_not_canonical"
exec 9>"$SCRATCH_ROOT/.a360_dr0_restore.lock"
chmod 0600 "$SCRATCH_ROOT/.a360_dr0_restore.lock"
flock -n 9 || fail "concurrent_restore_rejected"

if [ "$MODE" != "verify" ]; then
  if [ -z "$TARGET" ]; then
    TARGET="$SCRATCH_ROOT/restore_${RUN_ID}"
  fi
  [ ! -e "$TARGET" ] && [ ! -L "$TARGET" ] || fail "target_must_be_new"
  TARGET_CANON="$(readlink -m -- "$TARGET")"
  [ "$(dirname -- "$TARGET_CANON")" = "$SCRATCH_CANON" ] || fail "target_outside_scratch"
  [[ "$(basename -- "$TARGET_CANON")" =~ ^restore_[A-Za-z0-9_-]+$ ]] || fail "invalid_target_name"
  TARGET="$TARGET_CANON"
  mkdir -m 0700 -- "$TARGET"
  TARGET_CREATED=1
  OWNER_FILE="$TARGET/.a360_dr0_owner"
  printf '%s\n' "$RUN_ID" > "$OWNER_FILE"
  chmod 0600 "$OWNER_FILE"
elif [ -n "$TARGET" ]; then
  fail "target_not_valid_for_verify" 2
fi

if [ "$MODE" = "verify" ]; then
  VERIFY_CACHE="$(mktemp -d "$SCRATCH_ROOT/.verify_cache.XXXXXX")"
  chmod 0700 "$VERIFY_CACHE"
  export XDG_CACHE_HOME="$VERIFY_CACHE"
else
  mkdir -m 0700 "$TARGET/.cache"
  export XDG_CACHE_HOME="$TARGET/.cache"
fi

REPOSITORY_CACHE_REQUIRED_BYTES="$(checked_capacity "$REPOSITORY_CACHE_ALLOWANCE_BYTES" 1 "$MIN_FREE_RESERVE_BYTES")" \
  || fail "capacity_arithmetic_unsafe"
[ "$REPOSITORY_CACHE_REQUIRED_BYTES" -le "$SCRATCH_BUDGET_BYTES" ] \
  || fail "repository_cache_budget_exceeded"
REPOSITORY_PREFLIGHT_FREE_BYTES="$(free_bytes_for_path "$SCRATCH_ROOT")" \
  || fail "repository_cache_disk_probe_failed"
[[ "$REPOSITORY_PREFLIGHT_FREE_BYTES" =~ ^[0-9]+$ ]] \
  || fail "repository_cache_disk_probe_failed"
[ "$REPOSITORY_PREFLIGHT_FREE_BYTES" -ge "$REPOSITORY_CACHE_REQUIRED_BYTES" ] \
  || fail "repository_cache_disk_capacity_too_low"

PHASE="SNAPSHOT"
SNAP_META="$(mktemp "$SCRATCH_ROOT/.a360_snapshot.XXXXXX")"
chmod 0600 "$SNAP_META"
if [ "$SNAP" = "latest" ]; then
  # Nie uzywaj --latest przed walidacja provenance: nowszy obcy snapshot w tej
  # samej grupie moglby ukryc najnowszy poprawny kandydat.
  "$RESTIC_BIN" snapshots --json >"$SNAP_META" 2>/dev/null || fail "snapshot_resolution_failed"
else
  [[ "$SNAP" =~ ^[A-Za-z0-9]{4,64}$ ]] || fail "invalid_snapshot_id"
  "$RESTIC_BIN" snapshots --json "$SNAP" >"$SNAP_META" 2>/dev/null || fail "snapshot_resolution_failed"
fi
META_LINE="$($PYTHON_BIN -c '
import datetime as dt
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    rows = json.load(handle)
if not isinstance(rows, list) or not rows:
    raise SystemExit(1)
if sys.argv[2] == "explicit" and len(rows) != 1:
    raise SystemExit(1)

expected_hostname = sys.argv[3]
tag_count = int(sys.argv[4])
required_tags = set(sys.argv[5:5 + tag_count])
path_count_index = 5 + tag_count
path_count = int(sys.argv[path_count_index])
required_paths = set(sys.argv[path_count_index + 1:path_count_index + 1 + path_count])
if len(required_tags) != tag_count or len(required_paths) != path_count:
    raise SystemExit(1)

validated = []
for row in rows:
    snapshot_id = row.get("id") if isinstance(row, dict) else None
    snapshot_time = row.get("time") if isinstance(row, dict) else None
    hostname = row.get("hostname") if isinstance(row, dict) else None
    tags = row.get("tags", []) if isinstance(row, dict) else None
    paths = row.get("paths") if isinstance(row, dict) else None
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise SystemExit(1)
    if not isinstance(snapshot_time, str):
        raise SystemExit(1)
    if not isinstance(hostname, str):
        raise SystemExit(1)
    if tags is None:
        tags = []
    if not isinstance(tags, list) or not all(isinstance(item, str) for item in tags):
        raise SystemExit(1)
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        raise SystemExit(1)
    if len(tags) != len(set(tags)) or len(paths) != len(set(paths)):
        raise SystemExit(1)
    parsed = dt.datetime.fromisoformat(snapshot_time.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise SystemExit(1)
    if hostname != expected_hostname:
        continue
    if not required_tags.issubset(tags):
        continue
    if not required_paths.issubset(paths):
        continue
    validated.append((int(parsed.timestamp()), snapshot_id, snapshot_time))

if not validated:
    raise SystemExit(1)

if sys.argv[2] == "explicit":
    if len(validated) != 1:
        raise SystemExit(1)
    chosen = validated[0]
else:
    newest_epoch = max(item[0] for item in validated)
    newest = [item for item in validated if item[0] == newest_epoch]
    if len(newest) != 1:
        raise SystemExit(1)
    chosen = newest[0]
print("{}\t{}\t{}".format(chosen[1], chosen[2], chosen[0]))
' "$SNAP_META" "$( [ "$SNAP" = "latest" ] && printf latest || printf explicit )" \
  "$EXPECTED_SNAPSHOT_HOSTNAME" "$EXPECTED_SNAPSHOT_TAGS" "${REQUIRED_SNAPSHOT_TAGS[@]}" \
  "$EXPECTED_SNAPSHOT_PATHS" "${REQUIRED_SNAPSHOT_PATHS[@]}" 2>/dev/null)" \
  || fail "invalid_snapshot_metadata"
rm -f -- "$SNAP_META"
SNAP_META=""
IFS=$'\t' read -r SNAPSHOT_ID SNAPSHOT_TIME SNAPSHOT_EPOCH <<< "$META_LINE"
[[ "$SNAPSHOT_ID" =~ ^[A-Za-z0-9]{4,128}$ ]] || fail "invalid_snapshot_metadata"
SNAPSHOT_RPO_SECONDS=$((START_EPOCH - SNAPSHOT_EPOCH))
[ "$SNAPSHOT_RPO_SECONDS" -ge 0 ] || fail "snapshot_from_future"
[ "$SNAPSHOT_RPO_SECONDS" -le "$MAX_RPO_SECONDS" ] || fail "snapshot_stale"

PHASE="REPOSITORY_CHECK"
# Zamknij okno pomiedzy lista snapshotow a checkiem: backup mogl wystartowac
# po pierwszym preflight.
assert_host_capacity
"$RESTIC_BIN" check --read-data-subset=5% >/dev/null 2>&1 || fail "repository_integrity_failed"
REPOSITORY_CACHE_BYTES="$(du -sb -- "$XDG_CACHE_HOME" 2>/dev/null | awk '{print $1}')" \
  || fail "repository_cache_usage_probe_failed"
[[ "$REPOSITORY_CACHE_BYTES" =~ ^[0-9]+$ ]] \
  || fail "repository_cache_usage_probe_failed"
[ "$REPOSITORY_CACHE_BYTES" -le "$REPOSITORY_CACHE_ALLOWANCE_BYTES" ] \
  || fail "repository_cache_allowance_exceeded"
REPOSITORY_CACHE_ACTUAL_REQUIRED_BYTES="$(checked_capacity "$REPOSITORY_CACHE_BYTES" 1 "$MIN_FREE_RESERVE_BYTES")" \
  || fail "capacity_arithmetic_unsafe"
[ "$REPOSITORY_CACHE_ACTUAL_REQUIRED_BYTES" -le "$SCRATCH_BUDGET_BYTES" ] \
  || fail "repository_cache_budget_exceeded_after_check"
REPOSITORY_POSTCHECK_FREE_BYTES="$(free_bytes_for_path "$SCRATCH_ROOT" repository_post)" \
  || fail "repository_cache_disk_probe_failed"
[[ "$REPOSITORY_POSTCHECK_FREE_BYTES" =~ ^[0-9]+$ ]] \
  || fail "repository_cache_disk_probe_failed"
[ "$REPOSITORY_POSTCHECK_FREE_BYTES" -ge "$MIN_FREE_RESERVE_BYTES" ] \
  || fail "repository_cache_reserve_eroded"
if [ "$MODE" = "verify" ]; then
  rm -rf --one-file-system -- "$VERIFY_CACHE"
  VERIFY_CACHE=""
  printf 'PASS scope=repository_check provenance=matched snapshot_age_seconds=%s\n' "$SNAPSHOT_RPO_SECONDS"
  FINALIZED=1
  exit 0
fi

PHASE="SNAPSHOT_STATS"
STATS_META="$(mktemp "$SCRATCH_ROOT/.a360_stats.XXXXXX")"
chmod 0600 "$STATS_META"
"$RESTIC_BIN" stats --mode restore-size --json "$SNAPSHOT_ID" >"$STATS_META" 2>/dev/null \
  || fail "snapshot_stats_failed"
STATS_LINE="$($PYTHON_BIN -c '
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    row = json.load(handle)
size = row.get("total_size")
count = row.get("total_file_count")
if not isinstance(size, int) or size <= 0:
    raise SystemExit(1)
if not isinstance(count, int) or count <= 0:
    raise SystemExit(1)
print("{}\t{}".format(size, count))
' "$STATS_META" 2>/dev/null)" || fail "invalid_snapshot_stats"
rm -f -- "$STATS_META"
STATS_META=""
IFS=$'\t' read -r SNAPSHOT_LOGICAL_BYTES SNAPSHOT_LOGICAL_FILES <<< "$STATS_LINE"
[[ "$SNAPSHOT_LOGICAL_BYTES" =~ ^[0-9]+$ ]] || fail "invalid_snapshot_stats"
[[ "$SNAPSHOT_LOGICAL_FILES" =~ ^[0-9]+$ ]] || fail "invalid_snapshot_stats"

SCRATCH_FREE_BYTES="$(free_bytes_for_path "$SCRATCH_ROOT")" || fail "scratch_disk_probe_failed"
[[ "$SCRATCH_FREE_BYTES" =~ ^[0-9]+$ ]] || fail "scratch_disk_probe_failed"
SCRATCH_UNPACK_REQUIRED_BYTES="$(checked_capacity "$SNAPSHOT_LOGICAL_BYTES" 2 "$MIN_FREE_RESERVE_BYTES")" \
  || fail "capacity_arithmetic_unsafe"
[ "$SCRATCH_UNPACK_REQUIRED_BYTES" -le "$SCRATCH_BUDGET_BYTES" ] \
  || fail "scratch_budget_exceeded"
[ "$SCRATCH_FREE_BYTES" -ge "$SCRATCH_UNPACK_REQUIRED_BYTES" ] \
  || fail "scratch_disk_capacity_too_low"
SCRATCH_DEVICE="$(stat -c '%d' -- "$SCRATCH_ROOT" 2>/dev/null)" \
  || fail "scratch_disk_probe_failed"
[[ "$SCRATCH_DEVICE" =~ ^[0-9]+$ ]] || fail "scratch_disk_probe_failed"

DOCKER_ROOT=""
DOCKER_DEVICE=""
DOCKER_FREE_BYTES=0
DOCKER_EARLY_REQUIRED_BYTES=0
DOCKER_POST_DECOMPRESS_REQUIRED_BYTES=0
SCRATCH_DOCKER_SHARED_DEVICE=0
if [ "$MODE" = "drill" ]; then
  if [ "$TEST_MODE" = "1" ] && [ -n "${A360_TEST_DOCKER_ROOT:-}" ]; then
    DOCKER_ROOT="$A360_TEST_DOCKER_ROOT"
  else
    DOCKER_ROOT="$($DOCKER_BIN info --format '{{.DockerRootDir}}' 2>/dev/null)" \
      || fail "docker_root_probe_failed" 20
  fi
  [[ "$DOCKER_ROOT" = /* ]] && [ -d "$DOCKER_ROOT" ] && [ ! -L "$DOCKER_ROOT" ] \
    || fail "docker_root_probe_failed" 20
  [ "$(readlink -e -- "$DOCKER_ROOT" 2>/dev/null)" = "$DOCKER_ROOT" ] \
    || fail "docker_root_probe_failed" 20
  if [ "$TEST_MODE" = "1" ] && [ -n "${A360_TEST_DOCKER_FREE_BYTES:-}" ]; then
    DOCKER_FREE_BYTES="$A360_TEST_DOCKER_FREE_BYTES"
  else
    DOCKER_FREE_BYTES="$(free_bytes_for_path "$DOCKER_ROOT")" \
      || fail "docker_disk_probe_failed" 20
  fi
  [[ "$DOCKER_FREE_BYTES" =~ ^[0-9]+$ ]] || fail "docker_disk_probe_failed" 20
  DOCKER_DEVICE="$(stat -c '%d' -- "$DOCKER_ROOT" 2>/dev/null)" \
    || fail "docker_disk_probe_failed" 20
  if [ "$TEST_MODE" = "1" ] && [ -n "${A360_TEST_SAME_DEVICE:-}" ]; then
    case "$A360_TEST_SAME_DEVICE" in
      0) DOCKER_DEVICE="different-device" ;;
      1) DOCKER_DEVICE="$SCRATCH_DEVICE" ;;
      *) fail "docker_disk_probe_failed" 20 ;;
    esac
  fi
  DOCKER_EARLY_REQUIRED_BYTES="$(checked_capacity "$SNAPSHOT_LOGICAL_BYTES" 4 "$MIN_FREE_RESERVE_BYTES")" \
    || fail "capacity_arithmetic_unsafe" 20
  [ "$DOCKER_EARLY_REQUIRED_BYTES" -le "$DOCKER_BUDGET_BYTES" ] \
    || fail "docker_budget_exceeded_before_unpack" 20
  [ "$DOCKER_FREE_BYTES" -ge "$DOCKER_EARLY_REQUIRED_BYTES" ] \
    || fail "docker_disk_capacity_too_low_before_unpack" 20
  if [ "$DOCKER_DEVICE" = "$SCRATCH_DEVICE" ]; then
    SCRATCH_DOCKER_SHARED_DEVICE=1
    SHARED_DEVICE_REQUIRED_BYTES="$(checked_capacity "$SNAPSHOT_LOGICAL_BYTES" 6 "$MIN_FREE_RESERVE_BYTES")" \
      || fail "capacity_arithmetic_unsafe" 20
    [ "$SCRATCH_FREE_BYTES" -ge "$SHARED_DEVICE_REQUIRED_BYTES" ] \
      && [ "$DOCKER_FREE_BYTES" -ge "$SHARED_DEVICE_REQUIRED_BYTES" ] \
      || fail "shared_device_capacity_too_low_before_unpack" 20
  fi
fi

PHASE="EXTRACT"
RESTORE_START_MS="$(monotonic_ms)"
"$RESTIC_BIN" restore "$SNAPSHOT_ID" --target "$TARGET" >/dev/null 2>&1 || fail "snapshot_restore_failed"
chmod 0700 -- "$TARGET"
[ "$(stat -c '%a' -- "$TARGET")" = "700" ] || fail "target_permissions_changed"
TARGET_WORKING_SET_BYTES="$(du -sb -- "$TARGET" 2>/dev/null | awk '{print $1}')" \
  || fail "scratch_usage_probe_failed"
[[ "$TARGET_WORKING_SET_BYTES" =~ ^[0-9]+$ ]] || fail "scratch_usage_probe_failed"
[ "$TARGET_WORKING_SET_BYTES" -le "$SCRATCH_BUDGET_BYTES" ] \
  || fail "scratch_budget_exceeded_after_unpack"

STATE_ROOT="$TARGET/root/.openclaw/workspace/dispatch_state"
SCRIPTS_ROOT="$TARGET/root/.openclaw/workspace/scripts"
PANEL_DIR="$TARGET/root/backups/nadajesz_panel"
PAPU_DIR="$TARGET/root/backups/papu"
ORDERS_JSON="$STATE_ROOT/orders_state.json"
PLANS_JSON="$STATE_ROOT/courier_plans.json"
FLAGS_JSON="$SCRIPTS_ROOT/flags.json"
EVENTS_DB="$STATE_ROOT/events.db"

require_regular() {
  local path="$1"
  assert_no_symlink_components "$path"
  [ -f "$path" ] && [ ! -L "$path" ] || fail "required_artifact_missing_or_unsafe"
  [ "$(stat -c '%h' -- "$path")" = "1" ] || fail "hardlinked_artifact_rejected"
}
require_nonempty_regular() {
  local path="$1"
  require_regular "$path"
  local size=""
  size="$(stat -c '%s' -- "$path" 2>/dev/null)" \
    || fail "required_artifact_metadata_invalid"
  [[ "$size" =~ ^[0-9]+$ ]] && [ "$size" -gt 0 ] \
    || fail "required_artifact_empty"
}
require_directory() {
  local path="$1"
  assert_no_symlink_components "$path"
  [ -d "$path" ] && [ ! -L "$path" ] || fail "required_directory_missing_or_unsafe"
}
assert_no_symlink_components() {
  local path="$1" relative="" current="$TARGET" component=""
  case "$path" in
    "$TARGET"/*) relative="${path#"$TARGET"/}" ;;
    *) fail "artifact_outside_scratch" ;;
  esac
  IFS='/' read -r -a components <<< "$relative"
  for component in "${components[@]}"; do
    current="$current/$component"
    [ ! -L "$current" ] || fail "artifact_ancestor_symlink_rejected"
  done
  local resolved=""
  resolved="$(readlink -e -- "$path" 2>/dev/null)" || fail "required_artifact_missing_or_unsafe"
  case "$resolved" in
    "$TARGET"/*) ;;
    *) fail "artifact_outside_scratch" ;;
  esac
}
for required in "${REQUIRED_CORE_RELATIVE_PATHS[@]}"; do
  require_nonempty_regular "$TARGET/$required"
done
for required in "${REQUIRED_PRIVATE_METADATA_RELATIVE_PATHS[@]}"; do
  require_nonempty_regular "$TARGET/$required"
done
for required in "${REQUIRED_SYSTEMD_RELATIVE_PATHS[@]}"; do
  require_nonempty_regular "$TARGET/$required"
done
for required in "${REQUIRED_NGINX_RELATIVE_PATHS[@]}"; do
  require_nonempty_regular "$TARGET/$required"
done
for required_dir in "$PANEL_DIR" "$PAPU_DIR"; do
  require_directory "$required_dir"
done

select_newest() {
  [ "$#" -gt 0 ] || return 1
  local item="" mtime="" best="" best_mtime=-1 ties=0
  for item in "$@"; do
    [ -f "$item" ] && [ ! -L "$item" ] || continue
    mtime="$(stat -c '%Y' -- "$item")" || return 1
    if [ "$mtime" -gt "$best_mtime" ]; then
      best="$item"
      best_mtime="$mtime"
      ties=0
    elif [ "$mtime" -eq "$best_mtime" ]; then
      ties=1
    fi
  done
  [ -n "$best" ] && [ "$ties" = "0" ] || return 1
  printf '%s' "$best"
}

shopt -s nullglob
PANEL_CANDIDATES=("$PANEL_DIR"/nadajesz_panel_*.sql.gz)
PAPU_PLAIN_CANDIDATES=("$PAPU_DIR"/papu_*.sql.gz)
PAPU_ENCRYPTED_CANDIDATES=("$PAPU_DIR"/papu_*.sql.gz.enc)
PANEL_DUMP="$(select_newest "${PANEL_CANDIDATES[@]}")" || fail "panel_dump_missing_or_ambiguous"
case "$PAPU_FORMAT" in
  auto)
    PAPU_DUMP="$(select_newest "${PAPU_PLAIN_CANDIDATES[@]}" "${PAPU_ENCRYPTED_CANDIDATES[@]}")" || fail "papu_dump_missing_or_ambiguous"
    ;;
  plain)
    PAPU_DUMP="$(select_newest "${PAPU_PLAIN_CANDIDATES[@]}")" || fail "papu_plain_dump_missing_or_ambiguous"
    ;;
  encrypted)
    PAPU_DUMP="$(select_newest "${PAPU_ENCRYPTED_CANDIDATES[@]}")" || fail "papu_encrypted_dump_missing_or_ambiguous"
    ;;
esac
require_regular "$PANEL_DUMP"
require_regular "$PAPU_DUMP"
case "$PAPU_DUMP" in
  *.sql.gz.enc) PAPU_SELECTED_FORMAT="encrypted" ;;
  *.sql.gz) PAPU_SELECTED_FORMAT="plain" ;;
  *) fail "papu_dump_format_unknown" ;;
esac
if [ "$PAPU_FORMAT" != "auto" ] && [ "$PAPU_FORMAT" != "$PAPU_SELECTED_FORMAT" ]; then
  fail "papu_dump_format_mismatch"
fi

PHASE="ARTIFACT_INTEGRITY"
PANEL_SQL_BYTES="$($GZIP_BIN -dc -- "$PANEL_DUMP" 2>/dev/null | wc -c)" \
  || fail "panel_dump_integrity_failed"
if [ "$PAPU_SELECTED_FORMAT" = "plain" ]; then
  PAPU_SQL_BYTES="$($GZIP_BIN -dc -- "$PAPU_DUMP" 2>/dev/null | wc -c)" \
    || fail "papu_dump_integrity_failed"
else
  [ -n "$PAPU_BACKUP_KEY_FILE" ] || fail "papu_decrypt_key_unavailable"
  validate_private_input "$PAPU_BACKUP_KEY_FILE" "papu_decrypt_key_unavailable_or_unsafe"
  command -v "$OPENSSL_BIN" >/dev/null 2>&1 || fail "decrypt_tool_missing"
  PAPU_SQL_BYTES="$("$OPENSSL_BIN" enc -d -aes-256-cbc -pbkdf2 -pass "file:$PAPU_BACKUP_KEY_FILE" -in "$PAPU_DUMP" 2>/dev/null \
    | "$GZIP_BIN" -dc 2>/dev/null \
    | wc -c)" \
    || fail "papu_decrypt_or_integrity_failed"
fi
for count in "$PANEL_SQL_BYTES" "$PAPU_SQL_BYTES"; do
  [[ "$count" =~ ^[0-9]+$ ]] && [ "$count" -gt 0 ] || fail "sql_dump_empty"
done

json_object_count() {
  "$PYTHON_BIN" -c '
import json
import sys

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result

def reject_constant(_value):
    raise ValueError("non-finite number")

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle, object_pairs_hook=unique_object,
                        parse_constant=reject_constant)
if not isinstance(payload, dict):
    raise SystemExit(1)
print(len(payload))
' "$1" 2>/dev/null
}

ORDERS_COUNT="$(json_object_count "$ORDERS_JSON")" || fail "orders_json_invalid"
PLANS_COUNT="$(json_object_count "$PLANS_JSON")" || fail "plans_json_invalid"
FLAGS_COUNT="$(json_object_count "$FLAGS_JSON")" || fail "flags_json_invalid"
for count in "$ORDERS_COUNT" "$PLANS_COUNT" "$FLAGS_COUNT"; do
  [[ "$count" =~ ^[0-9]+$ ]] || fail "json_counter_invalid"
done

SQLITE_INTEGRITY="$($SQLITE_BIN -readonly "$EVENTS_DB" 'PRAGMA integrity_check;' 2>/dev/null)" || fail "sqlite_integrity_command_failed"
[ "$SQLITE_INTEGRITY" = "ok" ] || fail "sqlite_integrity_failed"
SQLITE_TABLES="$($SQLITE_BIN -readonly "$EVENTS_DB" "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';" 2>/dev/null)" || fail "sqlite_schema_query_failed"
[[ "$SQLITE_TABLES" =~ ^[0-9]+$ ]] && [ "$SQLITE_TABLES" -gt 0 ] || fail "sqlite_schema_empty"
SQLITE_REQUIRED_TABLES="$($SQLITE_BIN -readonly "$EVENTS_DB" "SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('events','processed_events','audit_log');" 2>/dev/null)" || fail "sqlite_required_schema_query_failed"
[ "$SQLITE_REQUIRED_TABLES" = "3" ] || fail "sqlite_required_schema_missing"
SQLITE_EVENTS_COLUMNS="$($SQLITE_BIN -readonly "$EVENTS_DB" "SELECT count(*) FROM pragma_table_info('events') WHERE name IN ('event_id','event_type','order_id','courier_id','payload','created_at','processed_at','status');" 2>/dev/null)" || fail "sqlite_required_columns_query_failed"
SQLITE_PROCESSED_COLUMNS="$($SQLITE_BIN -readonly "$EVENTS_DB" "SELECT count(*) FROM pragma_table_info('processed_events') WHERE name IN ('event_id','processed_at');" 2>/dev/null)" || fail "sqlite_required_columns_query_failed"
SQLITE_AUDIT_COLUMNS="$($SQLITE_BIN -readonly "$EVENTS_DB" "SELECT count(*) FROM pragma_table_info('audit_log') WHERE name IN ('event_id','event_type','order_id','courier_id','payload','created_at');" 2>/dev/null)" || fail "sqlite_required_columns_query_failed"
[ "$SQLITE_EVENTS_COLUMNS" = "8" ] \
  && [ "$SQLITE_PROCESSED_COLUMNS" = "2" ] \
  && [ "$SQLITE_AUDIT_COLUMNS" = "6" ] \
  || fail "sqlite_required_columns_missing"

CORE_REQUIRED_COUNT="${#REQUIRED_CORE_RELATIVE_PATHS[@]}"
PRIVATE_METADATA_REQUIRED_COUNT="${#REQUIRED_PRIVATE_METADATA_RELATIVE_PATHS[@]}"
SYSTEMD_COUNT="${#REQUIRED_SYSTEMD_RELATIVE_PATHS[@]}"
NGINX_COUNT="${#REQUIRED_NGINX_RELATIVE_PATHS[@]}"
RESTORED_FILE_COUNT="$(find "$TARGET" -type f -printf x | wc -c)"
RESTORED_BYTES="$(du -sb -- "$TARGET" | awk '{print $1}')"

artifact_age() {
  local mtime
  mtime="$(stat -c '%Y' -- "$1")" || return 1
  [ "$mtime" -le $((START_EPOCH + 300)) ] || return 1
  if [ "$mtime" -gt "$START_EPOCH" ]; then
    printf '0'
  else
    printf '%s' "$((START_EPOCH - mtime))"
  fi
}
PANEL_RPO_SECONDS="$(artifact_age "$PANEL_DUMP")" || fail "panel_artifact_time_invalid"
PAPU_RPO_SECONDS="$(artifact_age "$PAPU_DUMP")" || fail "papu_artifact_time_invalid"
[ "$PANEL_RPO_SECONDS" -le "$MAX_ARTIFACT_AGE_SECONDS" ] || fail "panel_dump_stale"
[ "$PAPU_RPO_SECONDS" -le "$MAX_ARTIFACT_AGE_SECONDS" ] || fail "papu_dump_stale"
# JSON i SQLite sa bezposrednio w snapshotcie: ich cutoffem backupowym jest
# czas snapshotu, nie mtime (plik mogl legalnie nie zmieniac sie od wielu dni).
SQLITE_RPO_SECONDS="$SNAPSHOT_RPO_SECONDS"
JSON_RPO_SECONDS="$SNAPSHOT_RPO_SECONDS"
RPO_WORST_SECONDS="$SNAPSHOT_RPO_SECONDS"
for age in "$PANEL_RPO_SECONDS" "$PAPU_RPO_SECONDS" "$SQLITE_RPO_SECONDS" "$JSON_RPO_SECONDS"; do
  [ "$age" -le "$RPO_WORST_SECONDS" ] || RPO_WORST_SECONDS="$age"
done
ARTIFACT_DONE_MS="$(monotonic_ms)"

PANEL_TABLES=0
PAPU_TABLES=0
PANEL_INVALID_INDEXES=0
PAPU_INVALID_INDEXES=0
PANEL_SCHEMA_SENTINELS=0
PAPU_SCHEMA_SENTINELS=0
SEPARATE_CONTAINER=0
SEPARATE_VOLUME=0
NETWORK_NONE=0
POSTGRES_SCHEMA_SMOKE_DONE_MS=0

pg_query_scalar() {
  local database="$1" query="$2"
  "$DOCKER_BIN" exec "$CONTAINER_NAME" psql -X -U postgres -d "$database" -Atqc "$query" 2>/dev/null
}

restore_postgres_role() {
  local role="$1" format="$2" dump="$3" database="$4"
  "$DOCKER_BIN" exec "$CONTAINER_NAME" createdb -U postgres "$database" >/dev/null 2>&1 \
    || fail "${role}_database_create_failed"
  if [ "$format" = "plain" ]; then
    "$GZIP_BIN" -dc -- "$dump" 2>/dev/null \
      | "$DOCKER_BIN" exec -i "$CONTAINER_NAME" psql -X -U postgres -d "$database" -v ON_ERROR_STOP=1 --single-transaction -q >/dev/null 2>&1 \
      || fail "${role}_strict_sql_restore_failed"
  else
    "$OPENSSL_BIN" enc -d -aes-256-cbc -pbkdf2 -pass "file:$PAPU_BACKUP_KEY_FILE" -in "$dump" 2>/dev/null \
      | "$GZIP_BIN" -dc 2>/dev/null \
      | "$DOCKER_BIN" exec -i "$CONTAINER_NAME" psql -X -U postgres -d "$database" -v ON_ERROR_STOP=1 --single-transaction -q >/dev/null 2>&1 \
      || fail "${role}_strict_sql_restore_failed"
  fi
}

if [ "$MODE" = "drill" ]; then
  PHASE="SCRATCH_POSTGRES"
  # Restic/dekompresja mogly trwac dlugo; zamknij race z nowa regresja lub
  # backupem, zanim powstanie jakikolwiek zasob Docker.
  assert_host_capacity
  CURRENT_DOCKER_ROOT=""
  if [ "$TEST_MODE" = "1" ] && [ -n "${A360_TEST_DOCKER_FREE_BYTES:-}" ]; then
    CURRENT_DOCKER_ROOT="${A360_TEST_DOCKER_ROOT:-$DOCKER_ROOT}"
    DOCKER_FREE_BYTES="${A360_TEST_DOCKER_FREE_BYTES_AFTER_DECOMPRESS:-$A360_TEST_DOCKER_FREE_BYTES}"
  else
    CURRENT_DOCKER_ROOT="$($DOCKER_BIN info --format '{{.DockerRootDir}}' 2>/dev/null)" \
      || fail "docker_root_probe_failed" 20
    DOCKER_FREE_BYTES="$(free_bytes_for_path "$CURRENT_DOCKER_ROOT")" \
      || fail "docker_disk_probe_failed" 20
  fi
  [ "$CURRENT_DOCKER_ROOT" = "$DOCKER_ROOT" ] || fail "docker_root_changed_during_preflight" 20
  [[ "$DOCKER_FREE_BYTES" =~ ^[0-9]+$ ]] || fail "docker_disk_probe_failed" 20
  SQL_TOTAL_BYTES="$(checked_capacity "$PANEL_SQL_BYTES" 1 "$PAPU_SQL_BYTES")" \
    || fail "capacity_arithmetic_unsafe" 20
  DOCKER_POST_DECOMPRESS_REQUIRED_BYTES="$(checked_capacity "$SQL_TOTAL_BYTES" 3 "$MIN_FREE_RESERVE_BYTES")" \
    || fail "capacity_arithmetic_unsafe" 20
  [ "$DOCKER_POST_DECOMPRESS_REQUIRED_BYTES" -le "$DOCKER_BUDGET_BYTES" ] \
    || fail "docker_budget_exceeded_after_decompress" 20
  [ "$DOCKER_FREE_BYTES" -ge "$DOCKER_POST_DECOMPRESS_REQUIRED_BYTES" ] \
    || fail "docker_disk_capacity_too_low_after_decompress" 20
  "$DOCKER_BIN" image inspect "$PG_IMAGE" >/dev/null 2>&1 || fail "pinned_pg_image_unavailable" 20
  if "$DOCKER_BIN" inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    fail "scratch_container_collision"
  fi
  if "$DOCKER_BIN" volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
    fail "scratch_volume_collision"
  fi
  # Od tej chwili trap zawsze rekoncyliuje dokladne nazwy i oba labele,
  # nawet gdy docker create/run utworzy zasob, ale zwroci non-zero.
  DOCKER_CLEANUP_ARMED=1
  "$DOCKER_BIN" volume create \
    --label a360.dr0.scratch=true \
    --label "a360.dr0.run_id=$RUN_ID" \
    "$VOLUME_NAME" >/dev/null 2>&1 || fail "scratch_volume_create_failed" 20
  VOLUME_CREATED=1
  [ "$($DOCKER_BIN volume inspect -f '{{ index .Labels "a360.dr0.scratch" }}' "$VOLUME_NAME" 2>/dev/null)" = "true" ] \
    || fail "scratch_volume_label_invalid"
  [ "$($DOCKER_BIN volume inspect -f '{{ index .Labels "a360.dr0.run_id" }}' "$VOLUME_NAME" 2>/dev/null)" = "$RUN_ID" ] \
    || fail "scratch_volume_run_id_invalid"
  "$DOCKER_BIN" run -d \
    --name "$CONTAINER_NAME" \
    --label a360.dr0.scratch=true \
    --label "a360.dr0.run_id=$RUN_ID" \
    --network none \
    --pull never \
    --cpus 1 \
    --memory 1g \
    -e POSTGRES_HOST_AUTH_METHOD=trust \
    -v "$VOLUME_NAME:/var/lib/postgresql/data" \
    "$PG_IMAGE" >/dev/null 2>&1 || fail "scratch_container_create_failed" 20
  CONTAINER_CREATED=1

  [ "$($DOCKER_BIN inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null)" = "true" ] || fail "scratch_container_not_running"
  [ "$($DOCKER_BIN inspect -f '{{ index .Config.Labels "a360.dr0.scratch" }}' "$CONTAINER_NAME" 2>/dev/null)" = "true" ] || fail "scratch_container_label_invalid"
  [ "$($DOCKER_BIN inspect -f '{{ index .Config.Labels "a360.dr0.run_id" }}' "$CONTAINER_NAME" 2>/dev/null)" = "$RUN_ID" ] || fail "scratch_container_run_id_invalid"
  [ "$($DOCKER_BIN inspect -f '{{.HostConfig.NetworkMode}}' "$CONTAINER_NAME" 2>/dev/null)" = "none" ] || fail "scratch_container_networked"
  [ "$($DOCKER_BIN inspect -f '{{len .HostConfig.PortBindings}}' "$CONTAINER_NAME" 2>/dev/null)" = "0" ] || fail "scratch_container_ports_present"
  [ "$($DOCKER_BIN inspect -f '{{len .Mounts}}' "$CONTAINER_NAME" 2>/dev/null)" = "1" ] || fail "scratch_container_mount_count_invalid"
  [ "$($DOCKER_BIN inspect -f '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' "$CONTAINER_NAME" 2>/dev/null)" = "$VOLUME_NAME" ] || fail "scratch_volume_attestation_failed"
  SEPARATE_CONTAINER=1
  SEPARATE_VOLUME=1
  NETWORK_NONE=1

  ready=0
  for ((attempt=0; attempt<PG_READY_TIMEOUT; attempt++)); do
    if "$DOCKER_BIN" exec "$CONTAINER_NAME" pg_isready -U postgres >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  [ "$ready" = "1" ] || fail "scratch_postgres_not_ready"

  PHASE="PANEL_RESTORE"
  restore_postgres_role "panel" "plain" "$PANEL_DUMP" "$PANEL_DB"
  PHASE="PAPU_RESTORE"
  restore_postgres_role "papu" "$PAPU_SELECTED_FORMAT" "$PAPU_DUMP" "$PAPU_DB"

  PHASE="POSTGRES_SMOKE"
  [ "$(pg_query_scalar "$PANEL_DB" 'SELECT 1;')" = "1" ] || fail "panel_connectivity_smoke_failed"
  [ "$(pg_query_scalar "$PAPU_DB" 'SELECT 1;')" = "1" ] || fail "papu_connectivity_smoke_failed"
  PANEL_TABLES="$(pg_query_scalar "$PANEL_DB" "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")" || fail "panel_table_count_failed"
  PAPU_TABLES="$(pg_query_scalar "$PAPU_DB" "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")" || fail "papu_table_count_failed"
  PANEL_INVALID_INDEXES="$(pg_query_scalar "$PANEL_DB" 'SELECT count(*) FROM pg_index WHERE NOT indisvalid;')" || fail "panel_index_smoke_failed"
  PAPU_INVALID_INDEXES="$(pg_query_scalar "$PAPU_DB" 'SELECT count(*) FROM pg_index WHERE NOT indisvalid;')" || fail "papu_index_smoke_failed"
  PANEL_SCHEMA_SENTINELS="$(pg_query_scalar "$PANEL_DB" "SELECT count(*) FROM (VALUES (to_regclass('public.delivery')), (to_regclass('public.status_event'))) AS required(rel) WHERE rel IS NOT NULL;")" || fail "panel_schema_identity_query_failed"
  PAPU_SCHEMA_SENTINELS="$(pg_query_scalar "$PAPU_DB" "SELECT count(*) FROM (VALUES (to_regclass('public.restaurants')), (to_regclass('public.orders'))) AS required(rel) WHERE rel IS NOT NULL;")" || fail "papu_schema_identity_query_failed"
  for count in "$PANEL_TABLES" "$PAPU_TABLES" "$PANEL_INVALID_INDEXES" "$PAPU_INVALID_INDEXES" "$PANEL_SCHEMA_SENTINELS" "$PAPU_SCHEMA_SENTINELS"; do
    [[ "$count" =~ ^[0-9]+$ ]] || fail "postgres_counter_invalid"
  done
  [ "$PANEL_TABLES" -ge "$MIN_PG_TABLES" ] && [ "$PAPU_TABLES" -ge "$MIN_PG_TABLES" ] \
    || fail "postgres_schema_below_floor"
  [ "$PANEL_INVALID_INDEXES" -eq 0 ] && [ "$PAPU_INVALID_INDEXES" -eq 0 ] || fail "postgres_invalid_index_detected"
  [ "$PANEL_SCHEMA_SENTINELS" -eq 2 ] || fail "panel_schema_identity_failed"
  [ "$PAPU_SCHEMA_SENTINELS" -eq 2 ] || fail "papu_schema_identity_failed"
  POSTGRES_SCHEMA_SMOKE_DONE_MS="$(monotonic_ms)"
fi

PHASE="CLEANUP"
cleanup_docker || fail "scratch_resource_cleanup_failed"
[ "$CONTAINER_CREATED" = "0" ] && [ "$VOLUME_CREATED" = "0" ] || fail "scratch_resource_cleanup_incomplete"
TOTAL_DONE_MS="$(monotonic_ms)"

PHASE="REPORT"
REPORT="$TARGET/a360_dr0_restore_report.json"
REPORT_TMP="$TARGET/.a360_dr0_restore_report.tmp"
TO_POSTGRES_SCHEMA_SMOKE_MS=0
if [ "$MODE" = "drill" ]; then
  TO_POSTGRES_SCHEMA_SMOKE_MS=$((POSTGRES_SCHEMA_SMOKE_DONE_MS - START_MS))
fi
TOTAL_RUN_MS=$((TOTAL_DONE_MS - START_MS))
ARTIFACT_RESTORE_MS=$((ARTIFACT_DONE_MS - RESTORE_START_MS))
SNAPSHOT_PREFIX="${SNAPSHOT_ID:0:12}"
PG_IMAGE_DIGEST_PREFIX=""
if [ -n "$PG_IMAGE" ]; then
  PG_IMAGE_DIGEST_PREFIX="${PG_IMAGE##*@sha256:}"
  PG_IMAGE_DIGEST_PREFIX="${PG_IMAGE_DIGEST_PREFIX:0:12}"
fi

"$PYTHON_BIN" -c '
import json
import os
import sys

path = sys.argv[1]
mode = sys.argv[2]
payload = {
    "schema": "a360-dr0-restore-report-v2",
    "status": "PASS",
    "mode": mode,
    "evidence_scope": (
        "artifact_and_postgres_schema_only"
        if mode == "drill" else "artifact_integrity_only"
    ),
    "full_service_recovery_proven": False,
    "service_rto": {
        "status": "HOLD",
        "proven": False,
        "seconds": None,
        "missing_evidence": [
            "application_import",
            "application_health",
            "service_start_order",
            "systemd_activation",
            "nginx_activation",
            "traffic_switch",
        ],
    },
    "snapshot": {
        "id_prefix": sys.argv[3],
        "time_utc": sys.argv[4],
        "age_seconds": int(sys.argv[5]),
        "provenance": {
            "contract_version": sys.argv[48],
            "matched": True,
            "required_tag_count": int(sys.argv[49]),
            "required_path_count": int(sys.argv[50]),
        },
    },
    "safety_profile": {
        "real_thresholds_source_pinned": True,
        "test_mode": bool(int(sys.argv[51])),
        "max_snapshot_age_seconds": int(sys.argv[52]),
        "max_artifact_age_seconds": int(sys.argv[53]),
        "min_free_reserve_bytes": int(sys.argv[54]),
        "min_memory_bytes": int(sys.argv[55]),
        "min_postgres_tables": int(sys.argv[56]),
    },
    "rpo": {
        "basis": "snapshot_time_plus_dump_mtime_estimate",
        "proven": False,
        "pitr_proven": False,
        "panel_seconds": int(sys.argv[6]),
        "papu_seconds": int(sys.argv[7]),
        "sqlite_seconds": int(sys.argv[8]),
        "json_worst_seconds": int(sys.argv[9]),
        "worst_case_upper_bound_seconds": int(sys.argv[10]),
    },
    "recovery_timing": {
        "scope": (
            "artifact_and_postgres_schema_only"
            if mode == "drill" else "artifact_integrity_only"
        ),
        "to_postgres_schema_smoke_seconds": (
            round(int(sys.argv[11]) / 1000.0, 3) if mode == "drill" else None
        ),
        "artifact_restore_seconds": round(int(sys.argv[12]) / 1000.0, 3),
        "total_run_seconds": round(int(sys.argv[13]) / 1000.0, 3),
    },
    "required_artifact_contract": {
        "version": sys.argv[37],
        "core_required": int(sys.argv[38]),
        "core_satisfied": int(sys.argv[38]),
        "private_metadata_required": int(sys.argv[39]),
        "private_metadata_satisfied": int(sys.argv[39]),
        "systemd_required": int(sys.argv[20]),
        "systemd_satisfied": int(sys.argv[20]),
        "nginx_required": int(sys.argv[21]),
        "nginx_satisfied": int(sys.argv[21]),
        "private_file_contents_read": False,
    },
    "capacity_preflight": {
        "run_budget_enforced": True,
        "filesystem_quota_enforced": False,
        "free_space_checked": True,
        "scratch_budget_bytes": int(sys.argv[40]),
        "scratch_unpack_required_bytes": int(sys.argv[41]),
        "scratch_free_bytes_at_preflight": int(sys.argv[42]),
        "docker_budget_bytes": int(sys.argv[43]) if mode == "drill" else None,
        "docker_early_required_bytes": int(sys.argv[44]) if mode == "drill" else None,
        "docker_post_decompress_required_bytes": (
            int(sys.argv[45]) if mode == "drill" else None
        ),
        "docker_free_bytes_at_last_preflight": (
            int(sys.argv[46]) if mode == "drill" else None
        ),
        "scratch_and_docker_share_device": (
            bool(int(sys.argv[47])) if mode == "drill" else None
        ),
    },
    "artifacts": {
        "restored_file_count": int(sys.argv[14]),
        "restored_bytes": int(sys.argv[15]),
        "snapshot_logical_file_count": int(sys.argv[31]),
        "snapshot_logical_bytes": int(sys.argv[32]),
        "panel_sql_uncompressed_bytes": int(sys.argv[33]),
        "papu_sql_uncompressed_bytes": int(sys.argv[34]),
        "required_json_files": 3,
        "orders_top_level_count": int(sys.argv[16]),
        "plans_top_level_count": int(sys.argv[17]),
        "flags_top_level_count": int(sys.argv[18]),
        "sqlite_table_count": int(sys.argv[19]),
        "systemd_required_file_count": int(sys.argv[20]),
        "nginx_required_file_count": int(sys.argv[21]),
        "panel_dump_format": "plain",
        "papu_dump_format": sys.argv[22],
    },
    "postgres": {
        "panel_table_count": int(sys.argv[23]),
        "papu_table_count": int(sys.argv[24]),
        "panel_invalid_index_count": int(sys.argv[25]),
        "papu_invalid_index_count": int(sys.argv[26]),
        "panel_schema_sentinel_count": int(sys.argv[35]),
        "papu_schema_sentinel_count": int(sys.argv[36]),
    },
    "isolation": {
        "separate_container": bool(int(sys.argv[27])),
        "separate_volume": bool(int(sys.argv[28])),
        "network_none": bool(int(sys.argv[29])),
        "scratch_resources_cleanup_verified": True,
        "pg_image_digest_prefix": sys.argv[30] or None,
    },
    "permissions": {"scratch": "0700", "report": "0600"},
}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=True, sort_keys=True, indent=2)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
' "$REPORT_TMP" "$MODE" "$SNAPSHOT_PREFIX" "$SNAPSHOT_TIME" "$SNAPSHOT_RPO_SECONDS" \
  "$PANEL_RPO_SECONDS" "$PAPU_RPO_SECONDS" "$SQLITE_RPO_SECONDS" "$JSON_RPO_SECONDS" "$RPO_WORST_SECONDS" \
  "$TO_POSTGRES_SCHEMA_SMOKE_MS" "$ARTIFACT_RESTORE_MS" "$TOTAL_RUN_MS" "$RESTORED_FILE_COUNT" "$RESTORED_BYTES" \
  "$ORDERS_COUNT" "$PLANS_COUNT" "$FLAGS_COUNT" "$SQLITE_TABLES" "$SYSTEMD_COUNT" "$NGINX_COUNT" \
  "$PAPU_SELECTED_FORMAT" "$PANEL_TABLES" "$PAPU_TABLES" "$PANEL_INVALID_INDEXES" "$PAPU_INVALID_INDEXES" \
  "$SEPARATE_CONTAINER" "$SEPARATE_VOLUME" "$NETWORK_NONE" "$PG_IMAGE_DIGEST_PREFIX" \
  "$SNAPSHOT_LOGICAL_FILES" "$SNAPSHOT_LOGICAL_BYTES" "$PANEL_SQL_BYTES" "$PAPU_SQL_BYTES" \
  "$PANEL_SCHEMA_SENTINELS" "$PAPU_SCHEMA_SENTINELS" \
  "$REQUIRED_ARTIFACT_CONTRACT_VERSION" "$CORE_REQUIRED_COUNT" "$PRIVATE_METADATA_REQUIRED_COUNT" \
  "$SCRATCH_BUDGET_BYTES" "$SCRATCH_UNPACK_REQUIRED_BYTES" "$SCRATCH_FREE_BYTES" \
  "${DOCKER_BUDGET_BYTES:-0}" "$DOCKER_EARLY_REQUIRED_BYTES" \
  "$DOCKER_POST_DECOMPRESS_REQUIRED_BYTES" "$DOCKER_FREE_BYTES" "$SCRATCH_DOCKER_SHARED_DEVICE" \
  "$SNAPSHOT_PROVENANCE_CONTRACT_VERSION" "$EXPECTED_SNAPSHOT_TAGS" "$EXPECTED_SNAPSHOT_PATHS" \
  "$TEST_MODE" "$MAX_RPO_SECONDS" "$MAX_ARTIFACT_AGE_SECONDS" "$MIN_FREE_RESERVE_BYTES" \
  "$MIN_MEMORY_BYTES" "$MIN_PG_TABLES" \
  || fail "report_write_failed"
chmod 0600 "$REPORT_TMP"
mv -f -- "$REPORT_TMP" "$REPORT"
[ "$(stat -c '%a' -- "$REPORT")" = "600" ] || fail "report_permissions_failed"
[ "$(stat -c '%a' -- "$TARGET")" = "700" ] || fail "target_permissions_failed"

FINALIZED=1
printf 'PASS scope=%s recovery_to_postgres_schema_smoke_ms=%s rpo_estimated_upper_bound_seconds=%s cleanup=verified\n' \
  "$( [ "$MODE" = "drill" ] && printf artifact_and_postgres_schema_only || printf artifact_integrity_only )" \
  "$( [ "$MODE" = "drill" ] && printf '%s' "$TO_POSTGRES_SCHEMA_SMOKE_MS" || printf not_applicable )" \
  "$RPO_WORST_SECONDS"
