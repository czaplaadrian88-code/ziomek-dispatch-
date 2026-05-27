# Faza 7 — Pre-flip checklist per sprint

**Owner:** Adrian Czapla
**Format:** CRYPTO-style precise. Each box = single fact verifiable in <2 min.
**Gate rule:** wszystkie boxy = TICK przed flip. Nawet jeden ✗ = STOP + diagnoza.

---

## Sprint 1 pre-flip — drive_min calibration (po 7d shadow)

Pre-condition: Sprint 1 shadow log live od _____ (Adrian fill date),
n_entries minimum 1500 (~7d normal traffic).

- [ ] Shadow log size sanity
  - `wc -l /root/.openclaw/workspace/dispatch_state/drive_min_calibration_log_v2.jsonl` ≥ 1500
  - File size ≥ 1.5 MB (sanity: zero-length rows = silent bug)
- [ ] Median |bias post-calibration| < 10 min
  - Run: `python3 -m dispatch_v2.tools.analyze_shadow_logs --days 7 --quiet && grep "median_cal" /tmp/shadow_weekly_summary_*.md | head -1`
  - Expected: `median_cal_bias` value w `[-10, +10]`
- [ ] Distribution per pos_source coverage ≥ 100 entries each
  - W markdown raporcie sekcja "Per pos_source" — kolumna `n_cal` ≥ 100 dla każdego z {gps, no_gps, last_assigned_pickup, last_picked_up_*, pre_shift, post_wave}
  - Wyjątek `gps` może być niżej (41 entries baseline z `drive_min_calibration_design.md`) — ale wtedy wymóg: median bias gps explicitly tested via spot-check 5 ordersów
- [ ] No errors w `journalctl -u dispatch-shadow --since "7 days ago" | grep -i "calibration\|drive_min"`
  - Empty output = OK
  - ANY traceback = ✗ + open ticket
- [ ] Test order via local script verifies calibration applied
  - Manual trigger via `replay_failed.py` lub local one-order assess
  - Inspect output: `predicted_drive_min` zwraca _calibrated_ value (compare to `raw_predicted` w log)
- [ ] Pre-flip backup created
  - `cp dispatch_v2/auto_proximity_classifier.py dispatch_v2/auto_proximity_classifier.py.bak-pre-drive-calib-2026-05-27`
  - Verify file exists post-cp
- [ ] Telegram alert sent to operator team
  - Template: patrz `/tmp/faza7_rollback_runbook.md` sekcja "Before flip"

**Flip command:**
```bash
python3 -c "import json,os,tempfile;p='/root/.openclaw/workspace/scripts/flags.json';d=json.load(open(p));d['ENABLE_DRIVE_MIN_CALIBRATION']=True;fd,t=tempfile.mkstemp(dir=os.path.dirname(p));open(fd,'w').write(json.dumps(d,indent=2,ensure_ascii=False));os.replace(t,p)"
```

**Post-flip 24h watch:**
- KPI dashboard daily — section 1 (override rate 24h) should NOT regress >+3pp
- KPI dashboard daily — section 4 (calibration bias) should show new `n_calibrated` growth

---

## Sprint 2.1 pre-flip — Kebab Król dinner exclusion (immediate, low risk)

Pre-condition: feature flag implemented (kod), shadow run minimum 30 min na real KK dinner order.

- [ ] Test KK order w lunch (12-15 Warsaw) — alert NOT triggered
  - Manual trigger or wait for live order from KK in 13:xx
  - Inspect log: `auto_route=ACK` (not KOORD), no "kk_dinner_exclusion" reason
- [ ] Test KK order w dinner (17-21 Warsaw) — alert TRIGGERED
  - Live order from KK in 18:xx-20:xx
  - Inspect log: `auto_route=KOORD` (or whatever Sprint 2.1 spec says), reason `kk_dinner_exclusion`
- [ ] Verify NON-KK restaurants in dinner — no change
  - 3 spot-check orders from non-KK restaurants 17-21
  - Inspect log: `auto_route` distribution unchanged vs pre-flip baseline
- [ ] KK dinner KPI baseline captured
  - Run: `python3 -m dispatch_v2.tools.faza7_daily_kpi --quiet && grep -A 5 "Kebab Król" /tmp/faza7_daily_kpi_*.md | head -10`
  - Save baseline value: dinner breach rate ~27% (z dzisiaj 2026-05-27)
- [ ] Flag exists in flags.json
  - `grep "ENABLE_KEBAB_KROL_DINNER_EXCLUSION" /root/.openclaw/workspace/scripts/flags.json` → present, value `false`
- [ ] No Telegram alert needed pre-flip (low-risk, isolated to 1 restaurant)

**Flip command:**
```bash
python3 -c "import json,os,tempfile;p='/root/.openclaw/workspace/scripts/flags.json';d=json.load(open(p));d['ENABLE_KEBAB_KROL_DINNER_EXCLUSION']=True;fd,t=tempfile.mkstemp(dir=os.path.dirname(p));open(fd,'w').write(json.dumps(d,indent=2,ensure_ascii=False));os.replace(t,p)"
```

**Post-flip 7d watch:**
- KK dinner breach rate trend
- Operator complaints w Telegram?
- If breach 7d post-flip < 15% → Sprint 2.1 SUCCESS, lock decision
- If breach 7d post-flip ≥ 20% → root cause was deeper than KK pickup lateness, rollback + re-investigate

---

## Sprint 2.2 pre-flip — carry chain penalty (po 14d shadow)

Pre-condition: Sprint 2.2 shadow log live od _____ , `carry_chain_shadow_log.jsonl`
existing.

- [ ] Carry chain shadow log analysis
  - `python3 -m dispatch_v2.tools.analyze_shadow_logs --days 14 --quiet`
  - In summary section 4: `n` ≥ 500 entries (14d), `would_block_rate` analysis OK
- [ ] would_block_rate sanity
  - W shadow log: `would_block_rate` should be ~15-25%
  - >50% = penalty too aggressive, calibrate before flip
  - <5% = penalty too soft (waste of complexity), reconsider
- [ ] KK dinner breach trend post-2.1 already declining
  - Run KPI daily for 7d post-Sprint 2.1 flip
  - KK dinner breach should trend down ↓
  - If still 25%+ post-2.1: Sprint 2.2 won't help alone — investigate alternatives
- [ ] No regressions w other restaurants
  - In shadow log: distribution `would_block` per restaurant — check no single non-KK restaurant captures >20% of blocks
  - Means: Sprint 2.2 mechanism is target-specific to carry-chain pattern, not collateral
- [ ] Pre-flip backup created
  - Backup target files per Sprint 2.2 spec
- [ ] Telegram alert sent to operator team
- [ ] Sprint 2.1 stable >=14d before this flip
  - No Sprint 2.1 rollback in last 14d
  - No KK regression w that window

**Flip command:**
```bash
python3 -c "import json,os,tempfile;p='/root/.openclaw/workspace/scripts/flags.json';d=json.load(open(p));d['ENABLE_CARRY_CHAIN_PENALTY']=True;fd,t=tempfile.mkstemp(dir=os.path.dirname(p));open(fd,'w').write(json.dumps(d,indent=2,ensure_ascii=False));os.replace(t,p)"
```

**Post-flip 14d watch:**
- R6 breach rate ACK route — should decline (carry chains broken = fewer 2nd-bag breaches)
- Throughput per courier — should NOT decline >10%
- KK dinner breach (composite Sprint 2.1 + 2.2) — should be <15%

---

## Faza 7 T1 pre-flip — AUTO routing live

Pre-condition: Sprint 1+2.1+2.2 wszystkie LIVE i stabilne ≥14d. Whitelist
refreshed ≤24h przed flipem.

- [ ] Override rate post Sprint 1+2 declined to < 60%
  - Was 78.6% baseline (Q1v2 + diag_q1_v2_2026-05-27.md)
  - Target post Sprint 1+2: <60%
  - Verify: `python3 -m dispatch_v2.tools.faza7_daily_kpi --quiet && grep -A 5 "Override rate" /tmp/faza7_daily_kpi_*.md`
  - Expected line: `| 7d | ... | ... | <60.0% |`
- [ ] Updated whitelist refreshed
  - `python3 -m dispatch_v2.tools.rebuild_courier_whitelist --days 14 --quiet`
  - Output JSON timestamp w `_meta.generated_at` w ostatnich 24h
  - WHITELIST bucket count ≥ 3 (jeśli 1-2 → calibration insufficient, more data needed)
  - Verify cross-reference: top WHITELIST cids match Q1v2 Agent 1 D.4 "operator favorites" (370, 393, 400)
- [ ] Daily KPI monitoring cron LIVE
  - `systemctl is-active dispatch-faza7-kpi.timer` → active
  - `ls -la /tmp/faza7_daily_kpi_*.md` → at least 7 daily reports (1 week of data)
  - Each daily report exists (no gaps)
- [ ] Operator team notified
  - Telegram message sent 24h przed flipem
  - Operator team ACK explicit (lub 24h silent OK)
- [ ] Telegram alert config tested
  - Test alert: send manual to operator chat, verify deliverable
  - Bot health: `curl -s http://localhost:8443/health` → 200 OK (NadajeszControlBot)
- [ ] Pre-flip backup of `flags.json`
  - `cp /root/.openclaw/workspace/scripts/flags.json /root/.openclaw/workspace/scripts/flags.json.bak-pre-faza7-T1-$(date -Iseconds)`
- [ ] All Sprint 1+2.1+2.2 flags LIVE and not in cooldown
  - `python3 -c "import json; f=json.load(open('/root/.openclaw/workspace/scripts/flags.json')); print({k:v for k,v in f.items() if any(s in k for s in ['DRIVE_MIN','KEBAB','CARRY_CHAIN','AUTO_PROXIMITY'])})"`
  - Expected: 3× True (Sprints) + AUTO_PROXIMITY_ENABLED=False (about to flip)
- [ ] T1 threshold values reviewed
  - W `common.py`: T1 = `min_pool_feasible=2, min_score_margin=15.0, tiers=(gold, std+), min_score=50.0, strict_gps=False`
  - Adrian explicit ACK na placeholderze lub na zaktualizowanych po shadow-week calibration
- [ ] Readiness signal all green
  - `python3 -m dispatch_v2.tools.faza7_daily_kpi --quiet` last line: `readiness=READY`
  - jeśli `NOT READY` → STOP, popraw symptom

**Flip command (T1 — najmniej agresywny ramp):**
```bash
python3 -c "import json,os,tempfile;p='/root/.openclaw/workspace/scripts/flags.json';d=json.load(open(p));d['AUTO_PROXIMITY_ENABLED']=True;d['AUTO_PROXIMITY_SHADOW_ONLY']=False;d['AUTO_PROXIMITY_THRESHOLD']='T1';fd,t=tempfile.mkstemp(dir=os.path.dirname(p));open(fd,'w').write(json.dumps(d,indent=2,ensure_ascii=False));os.replace(t,p)"
```

**Post-flip 1h hands-on observation:**
- Adrian sits w terminal: `journalctl -u dispatch-shadow -f`
- Watch for `auto_route=AUTO` decisions
- Operator team in Telegram — first AUTO override = trigger rollback?
- 1h zero AUTO override = continue silent watch ~24h
- 24h zero AUTO override + <5 AUTO total = T1 zbyt restrictive → next sprint T2 calibration

**T1→T2→T3 ramp pacing:**
- T1 stable 14d (≥30 AUTO decisions, override rate <30% in AUTO bucket) → flip to T2
- T2 stable 14d (≥80 AUTO, override <30%) → flip to T3
- T3 = "full AUTO" target — Faza 7 graduacja

---

## Quick gate summary (Adrian self-check przed każdym flipem)

```
Sprint:           [1 / 2.1 / 2.2 / Faza-7-T1]
Date pre-flip:    ____
Shadow window:    ____ d
Total boxes:      __ / __ (must be __ / __)
Failing boxes:    ____
Adrian sign-off:  ____ (initials + Warsaw timestamp)
```

---

## Cross-references

- Rollback procedures: `/tmp/faza7_rollback_runbook.md`
- KPI tool: `dispatch_v2/tools/faza7_daily_kpi.py`
- Whitelist tool: `dispatch_v2/tools/rebuild_courier_whitelist.py`
- Shadow analyzer: `dispatch_v2/tools/analyze_shadow_logs.py`
- Implementation report: `/tmp/sprint4_infra_IMPLEMENTATION_2026-05-27.md`
- Sprint 1 design: `/tmp/drive_min_calibration_design.md`
- Sprint 2 KK diagnostic: `/tmp/kebab_krol_diagnostic.md`
- Q1v2 baseline: `/tmp/diag_q1_v2_2026-05-27.md`
- Whitelist Agent B output (v2 dual lens): `/tmp/courier_whitelist_proposed.{json,md}`
