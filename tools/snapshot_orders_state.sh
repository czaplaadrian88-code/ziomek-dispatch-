#!/bin/bash
# Daily snapshot orders_state.json — 7-day retention.
#
# Backstop dla Fazy 1 .prev (1-deep): w razie clobberu wykrytego po dniu
# (np. cichy zapis zer w arkuszu Średnie — incydent 2026-05-18) recovery
# = 1-line cp z najnowszego snapshota, zamiast 30s rebuild_state_from_events.
#
# Uruchamiany przez dispatch-state-snapshot.timer codziennie 03:00 UTC
# (= 05:00 Warsaw, przed cron daily_stats 06:00 UTC).
set -euo pipefail

SRC="/root/.openclaw/workspace/dispatch_state/orders_state.json"
DST_DIR="/root/.openclaw/workspace/dispatch_state/snapshots"
RETENTION_DAYS=7
TODAY="$(date -u +%Y-%m-%d)"

mkdir -p "$DST_DIR"

if [ ! -f "$SRC" ]; then
    echo "[$(date -u +%FT%TZ)] snapshot: source missing $SRC — skip" >&2
    exit 1
fi

DST="$DST_DIR/orders_state_${TODAY}.json"
TMP="$(mktemp -p "$DST_DIR" .snaptmp.XXXXXX)"
cp "$SRC" "$TMP"
sync -f "$TMP"
mv "$TMP" "$DST"

SIZE="$(stat -c %s "$DST")"
COUNT="$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));o=d.get('orders',d) if isinstance(d,dict) else d;print(len(o) if isinstance(o,(dict,list)) else 0)" "$DST" 2>/dev/null || echo "?")"
echo "[$(date -u +%FT%TZ)] snapshot OK: $DST size=${SIZE}B orders=${COUNT}"

find "$DST_DIR" -name "orders_state_*.json" -mtime "+${RETENTION_DAYS}" -delete
KEPT="$(find "$DST_DIR" -name "orders_state_*.json" | wc -l)"
echo "[$(date -u +%FT%TZ)] snapshot retention: keeping ${KEPT} files (max ${RETENTION_DAYS} days)"
