#!/usr/bin/env bash
# Deploy A2 dwumodel SHADOW — OFF-PEAK, bezpieczny (2026-06-20).
# Ładuje nowy kod (restart dispatch-shadow) + flipuje ENABLE_LGBM_TWOMODEL_SHADOW=true.
# Twarde bramki: py_compile + import PRZED restartem; flip TYLKO gdy serwis zdrowy po restarcie.
# NIE dotyka dispatch-telegram. NIE rusza B3 (ENABLE_NO_GPS_UNCERTAINTY_PENALTY).
# Wszystko logging-only (zero wpływu na decyzje) — dwumodel-shadow nie jest konsumowany przez werdykt.
set -uo pipefail

SCRIPTS=/root/.openclaw/workspace/scripts
PY=/root/.openclaw/venvs/dispatch/bin/python
FLAGS=$SCRIPTS/flags.json
STATUS=/root/TWOMODEL_SHADOW_DEPLOY_STATUS_2026-06-20.txt
cd "$SCRIPTS" || { echo "FAIL cd scripts" >"$STATUS"; exit 1; }

log(){ echo "[$(date -u '+%H:%M:%S')] $*" | tee -a "$STATUS"; }
: >"$STATUS"
log "=== deploy_twomodel_shadow start (off-peak) ==="
log "Warsaw: $(TZ=Europe/Warsaw date '+%H:%M')  UTC: $(date -u '+%H:%M')"

# Bramka peak: NIE deployuj 17-21 Warsaw (= 15-19 UTC). Abort jeśli odpalone w peaku.
H=$(date -u '+%H')
if [ "$H" -ge 15 ] && [ "$H" -lt 19 ]; then
  log "ABORT: peak window (UTC $H, Warsaw $(TZ=Europe/Warsaw date '+%H')). Nie restartuję w szczycie."
  exit 2
fi

# 1) py_compile (łapie zepsuty stan dysku z DOWOLNEJ sesji)
if ! $PY -m py_compile dispatch_v2/dispatch_pipeline.py dispatch_v2/shadow_dispatcher.py dispatch_v2/ml_inference.py dispatch_v2/eta_calibration_logger.py; then
  log "ABORT: py_compile FAILED — nie restartuję."
  exit 3
fi
log "py_compile OK"

# 2) import check (package context)
if ! $PY -c "import dispatch_v2.dispatch_pipeline, dispatch_v2.shadow_dispatcher, dispatch_v2.ml_inference" 2>>"$STATUS"; then
  log "ABORT: import FAILED — nie restartuję."
  exit 4
fi
log "import OK"

# 3) restart dispatch-shadow (NIE telegram)
log "restart dispatch-shadow..."
systemctl restart dispatch-shadow.service
sleep 25

# 4) bramka zdrowia: is-active + brak Traceback w starcie
if [ "$(systemctl is-active dispatch-shadow.service)" != "active" ]; then
  log "WARN: dispatch-shadow nie active po 1. restarcie — retry raz..."
  systemctl restart dispatch-shadow.service
  sleep 25
fi
ACTIVE=$(systemctl is-active dispatch-shadow.service)
if [ "$ACTIVE" != "active" ]; then
  log "FAIL: dispatch-shadow=$ACTIVE po retry. NIE flipuję flagi. Serwis wymaga ręcznej interwencji."
  journalctl -u dispatch-shadow.service --since "90 seconds ago" --no-pager | tail -25 >>"$STATUS"
  exit 5
fi
if journalctl -u dispatch-shadow.service --since "60 seconds ago" --no-pager | grep -qiE "Traceback|ModuleNotFound|ImportError|SyntaxError"; then
  log "FAIL: Traceback w starcie dispatch-shadow. NIE flipuję flagi."
  journalctl -u dispatch-shadow.service --since "60 seconds ago" --no-pager | grep -iE "Traceback|Error" | tail -15 >>"$STATUS"
  exit 6
fi
log "dispatch-shadow active + clean startup"

# 5) flip ENABLE_LGBM_TWOMODEL_SHADOW=true (hot-reload; B3 chroniony)
cp "$FLAGS" "$FLAGS.bak-pre-twomodel-shadow-flip-2026-06-20"
$PY - <<PYEOF
import json,os,tempfile
p="$FLAGS"; d=json.load(open(p))
assert d.get("ENABLE_NO_GPS_UNCERTAINTY_PENALTY") is True, "B3 trial flag changed — ABORT flip"
d["ENABLE_LGBM_TWOMODEL_SHADOW"]=True
fd,t=tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(p)))
with os.fdopen(fd,"w") as fh: fh.write(json.dumps(d,indent=2,ensure_ascii=False))
os.replace(t,p)
print("FLIP ENABLE_LGBM_TWOMODEL_SHADOW ->", json.load(open(p)).get("ENABLE_LGBM_TWOMODEL_SHADOW"))
print("B3 intact ->", json.load(open(p)).get("ENABLE_NO_GPS_UNCERTAINTY_PENALTY"))
PYEOF
log "flag flipped (hot-reload, dwumodel-shadow zacznie liczyć przy najbliższej decyzji)"

# 6) verify: poczekaj na log dwumodelu (informacyjnie, nie blokuje)
sleep 120
N=$(journalctl -u dispatch-shadow.service --since "150 seconds ago" --no-pager | grep -c "LGBM_TWOMODEL_SHADOW")
log "LGBM_TWOMODEL_SHADOW log lines (ostatnie 150s): $N (0 OK jeśli brak decyzji w oknie)"
journalctl -u dispatch-shadow.service --since "150 seconds ago" --no-pager | grep "LGBM_TWOMODEL_SHADOW" | tail -3 >>"$STATUS"
log "=== deploy_twomodel_shadow DONE (status: SUCCESS) ==="
