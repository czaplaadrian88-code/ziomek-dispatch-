# Faza 7 — Rollback runbook (Sprint 1-3 + AUTO flag)

**Owner:** Adrian Czapla
**Author:** CC infra agent, Sprint 4
**Last update:** 2026-05-27
**Target time-to-rollback per item: <5 min** (single flag flip + verify)

---

## Quick-reference table

| Item | Action | Service touched | TTR | Risk if delayed |
|---|---|---|---:|---|
| Sprint 1 (drive_min calibration) | flag + restore backup | dispatch-shadow | ~5 min | bias bias drift, override↑ |
| Sprint 2.1 (KK dinner exclusion) | flag flip | dispatch-shadow + dispatch-panel-watcher | ~30 s | KK breach 22.5%→25%+ |
| Sprint 2.2 (carry chain penalty) | flag flip | dispatch-shadow | ~30 s | downstream R6 breach |
| Faza 7 AUTO flip | flag flip + Telegram alert | dispatch-shadow + dispatch-telegram | ~1 min | AUTO routes to wrong courier |
| Whitelist daily-rebuild cron | systemctl disable | (none) | ~10 s | stale whitelist |

---

## 1. Sprint 1 rollback — drive_min calibration

**Symptom triggers:**
- median |bias post-calibration| > 15 min for 2+ consecutive days (KPI gate at 10 min, allow 1-day spikes)
- override rate 7d climbed >5pp post-flip (correlation with operator distrust of calibrated ETA)
- ERROR `auto_proximity_classifier` in `journalctl -u dispatch-shadow --since "1h"`
- pos_source dropdown lopsided coverage (e.g. one of {gps, no_gps, last_*} dropped to <5% post-flip — calibration broken for that source)

**Rollback (Procedure A — flag-only, ~5s):**
```bash
python3 -c "import json,os,tempfile;p='/root/.openclaw/workspace/scripts/flags.json';d=json.load(open(p));d['ENABLE_DRIVE_MIN_CALIBRATION']=False;fd,t=tempfile.mkstemp(dir=os.path.dirname(p));open(fd,'w').write(json.dumps(d,indent=2,ensure_ascii=False));os.replace(t,p)"
```
Hot-reload — `dispatch-shadow` picks up next tick. NO restart needed.

**Rollback (Procedure B — full file revert, ~3 min):**
```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2
cp auto_proximity_classifier.py.bak-pre-drive-calib-2026-05-27 auto_proximity_classifier.py
python3 -m py_compile auto_proximity_classifier.py
python3 -c "from dispatch_v2 import auto_proximity_classifier; print('import OK')"
sudo systemctl restart dispatch-shadow
sleep 5
sudo systemctl status dispatch-shadow --no-pager | head -10
```

**Post-rollback verification:**
1. `journalctl -u dispatch-shadow --since "5 minutes ago" | grep -i error` → empty
2. Run `python3 -m dispatch_v2.tools.faza7_daily_kpi --quiet` and check section "4. drive_min calibration bias" — n_calibrated should freeze at last value pre-flip (no new entries)
3. Operator team Telegram message (template below)

---

## 2. Sprint 2.1 rollback — Kebab Król dinner exclusion

**Symptom triggers:**
- KK lunch (12-15) breach rate spiked >10% (n>=10 sample) — means exclusion misclassified
- Non-KK restaurants breach rate increased post-flip (collateral) — implementation accidentally excludes more than KK
- Telegram operator manual override on KK dinner orders increased — means exclusion is annoying ops

**Rollback (single flag, ~30s):**
```bash
python3 -c "import json,os,tempfile;p='/root/.openclaw/workspace/scripts/flags.json';d=json.load(open(p));d['ENABLE_KEBAB_KROL_DINNER_EXCLUSION']=False;fd,t=tempfile.mkstemp(dir=os.path.dirname(p));open(fd,'w').write(json.dumps(d,indent=2,ensure_ascii=False));os.replace(t,p)"
```

**Post-rollback verification:**
1. Single test order from KK in dinner (17-21 Warsaw) — Telegram proposal should fire (not blocked)
2. `python3 -m dispatch_v2.tools.faza7_daily_kpi --quiet` — KK dinner KPI should resume normal counts within 24h

---

## 3. Sprint 2.2 rollback — carry chain penalty

**Symptom triggers:**
- `would_block_rate` > 30% in `carry_chain_shadow_log.jsonl` (post live flip) — means penalty is too aggressive
- Throughput dip (orders/courier/hour declined >10% vs 7d-ago baseline)
- R6 breach rate ACK route increased post-flip (counterintuitive but possible — see lesson #131)

**Rollback (single flag, ~30s):**
```bash
python3 -c "import json,os,tempfile;p='/root/.openclaw/workspace/scripts/flags.json';d=json.load(open(p));d['ENABLE_CARRY_CHAIN_PENALTY']=False;fd,t=tempfile.mkstemp(dir=os.path.dirname(p));open(fd,'w').write(json.dumps(d,indent=2,ensure_ascii=False));os.replace(t,p)"
```

**Post-rollback verification:**
1. `python3 -m dispatch_v2.tools.analyze_shadow_logs --days 1 --out /tmp/post_rollback.md` — verify carry_chain entries continue (still shadow-logging but no longer enforcing)
2. Watch R6 breach rate ACK route for 24h post-rollback — should revert to baseline

---

## 4. Faza 7 AUTO flip rollback (FULL stop)

**Symptom triggers:**
- ANY AUTO proposal that operator overrides within 60s → manual hard alert
- Telegram operator team raises voice: "Ziomek wysyła samodzielnie złe!"
- R6 breach rate AUTO bucket spikes >15% (was ~5%) — auto-decision quality cratered
- Whitelist contains stale cids (kurier urlop / pijany / etc.)

**Rollback (single flag, ~1 min including Telegram alert):**
```bash
# Step 1 — flip flag
python3 -c "import json,os,tempfile;p='/root/.openclaw/workspace/scripts/flags.json';d=json.load(open(p));d['AUTO_PROXIMITY_ENABLED']=False;d['AUTO_PROXIMITY_SHADOW_ONLY']=True;fd,t=tempfile.mkstemp(dir=os.path.dirname(p));open(fd,'w').write(json.dumps(d,indent=2,ensure_ascii=False));os.replace(t,p)"

# Step 2 — verify hot-reload (no restart needed; next tick reads fresh)
journalctl -u dispatch-shadow --since "1 minute ago" | grep -i auto_proximity | tail -5

# Step 3 — Telegram alert to operator team (manual paste)
```

**Telegram alert template (copy-paste to operator chat):**
```
🚨 ZIOMEK AUTO ROUTING DISABLED
Powód: <wstaw symptom z runbooka>
Status: wszystkie propozycje WYMAGAJĄ ACK (jak dotychczas)
Akcja Adrian: diagnoza, ETA naprawy: TBD
```

**Post-rollback verification:**
1. Single test order — Telegram proposal shows `🟡 ACK` (not `🤖 PEWIEN`)
2. `journalctl -u dispatch-shadow -f` — wait for next decision, confirm `auto_route=ACK` w shadow log
3. Adrian reports back to operator team within 1h: root cause + ETA fix

**Time-to-restore (after fix):**
- ≥7d ponowne shadow observation post-fix przed re-flip
- Re-run pre-flip checklist (`/tmp/faza7_preflip_checklist.md` sekcja Faza 7)

---

## 5. Whitelist daily-rebuild rollback

**Symptom triggers:**
- Whitelist suddenly contains inactive cid (operator complains "Ziomek pyta o kuriera który nie pracuje")
- Whitelist size sudden change (e.g. 5 → 0 lub 5 → 25) — bug w aggregation

**Rollback:**
```bash
# Disable timer (if deployed)
sudo systemctl disable --now dispatch-faza7-whitelist.timer

# Restore last-good whitelist from backup
ls -lt /root/.openclaw/workspace/dispatch_state/courier_whitelist_v1.json.bak-* | head -3
cp /root/.openclaw/workspace/dispatch_state/courier_whitelist_v1.json.bak-<DATE> \
   /root/.openclaw/workspace/dispatch_state/courier_whitelist_v1.json
```

Classifier reads file on next tick (no restart needed if flag `AUTO_PROXIMITY_COURIER_WHITELIST_FROM_FILE` is hot-reload).

---

## 6. Cron timer design (NIE deploy — Adrian decyzja)

### dispatch-faza7-kpi.timer
```ini
# /etc/systemd/system/dispatch-faza7-kpi.timer
[Unit]
Description=Faza 7 daily KPI dashboard

[Timer]
OnCalendar=*-*-* 04:00:00  # 06:00 Warsaw
Persistent=true
Unit=dispatch-faza7-kpi.service

[Install]
WantedBy=timers.target
```

### dispatch-faza7-kpi.service
```ini
# /etc/systemd/system/dispatch-faza7-kpi.service
[Unit]
Description=Faza 7 daily KPI report
After=dispatch-shadow.service

[Service]
Type=oneshot
WorkingDirectory=/root/.openclaw/workspace/scripts
ExecStart=/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.faza7_daily_kpi --quiet
```

### dispatch-faza7-whitelist.timer
```ini
[Unit]
Description=Faza 7 daily whitelist rebuild

[Timer]
OnCalendar=*-*-* 04:30:00  # 06:30 Warsaw, AFTER KPI
Persistent=true
Unit=dispatch-faza7-whitelist.service

[Install]
WantedBy=timers.target
```

### dispatch-faza7-whitelist.service
```ini
[Unit]
Description=Faza 7 whitelist rebuild (auto)

[Service]
Type=oneshot
WorkingDirectory=/root/.openclaw/workspace/scripts
ExecStart=/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.rebuild_courier_whitelist --days 14 --md /tmp/courier_whitelist_$(date +%%Y-%%m-%%d).md --quiet
```

**Deploy (NIE TERAZ — czeka Adrian ACK):**
```bash
# Pre-flight: smoke run obu tools
python3 -m dispatch_v2.tools.faza7_daily_kpi --quiet
python3 -m dispatch_v2.tools.rebuild_courier_whitelist --quiet

# Install + enable
sudo cp dispatch-faza7-kpi.{timer,service} /etc/systemd/system/
sudo cp dispatch-faza7-whitelist.{timer,service} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dispatch-faza7-kpi.timer
sudo systemctl enable --now dispatch-faza7-whitelist.timer
```

---

## Communication template — operator team

**Before flip (Sprint 1 / 2.x / Faza 7):**
```
[Ziomek upgrade] Sprint <X> idzie LIVE za ~5 min.
Co się zmienia: <1 zdanie>
Co operator widzi: <1 zdanie>
Rollback w razie problemu: ~30s flag flip — pisz tutaj jeśli coś nie tak.
```

**Rollback (każdy sprint):**
```
[Ziomek rollback] Sprint <X> ZGASZONY o <HH:MM Warsaw>.
Powód: <symptom>
Stan obecny: jak przed flipem
ETA fix: TBD
```

---

## Cross-references

- Pre-flip checklist per sprint: `/tmp/faza7_preflip_checklist.md`
- Implementation report (this sprint): `/tmp/sprint4_infra_IMPLEMENTATION_2026-05-27.md`
- Tools: `dispatch_v2/tools/{rebuild_courier_whitelist,faza7_daily_kpi,analyze_shadow_logs}.py`
- KPI sample output: `/tmp/faza7_daily_kpi_2026-05-27.md` (po pierwszym uruchomieniu)
