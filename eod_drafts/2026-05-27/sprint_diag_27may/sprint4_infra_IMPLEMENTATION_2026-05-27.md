# Sprint 4 infra — IMPLEMENTATION report (2026-05-27)

**Owner:** Adrian Czapla
**Author:** CC infra agent (parallel z Sprint 1+2+3 implementation agents)
**Scope:** Monitoring + rollback infrastructure dla Faza 7 ramp-up (NIE production code modification)
**Status:** **5 z 5 deliverables DONE** (4 mandatory + 1 optional)

---

## 1. Files created

### Production tools (dispatch_v2/tools/)

| Path | LOC | Purpose |
|---|---:|---|
| `dispatch_v2/tools/rebuild_courier_whitelist.py` | 367 | Daily-rebuildable Faza 7 AUTO whitelist (adapter z `/tmp/build_whitelist.py`) |
| `dispatch_v2/tools/faza7_daily_kpi.py` | 313 | Daily KPI dashboard + readiness gate |
| `dispatch_v2/tools/analyze_shadow_logs.py` | 226 | Weekly summary z shadow logów (drive_cal + c2 + c5 + carry_chain) |

### Tests (dispatch_v2/tests/)

| Path | Tests | Status |
|---|---:|---|
| `dispatch_v2/tests/test_rebuild_courier_whitelist.py` | 9 | 9/9 PASS |
| `dispatch_v2/tests/test_faza7_daily_kpi.py` | 9 | 9/9 PASS |
| `dispatch_v2/tests/test_analyze_shadow_logs.py` | 7 | 7/7 PASS |
| **Total** | **25** | **25/25 PASS** w 0.99s |

### Documentation (/tmp/)

| Path | Lines | Purpose |
|---|---:|---|
| `/tmp/faza7_rollback_runbook.md` | ~200 | Krok-po-kroku rollback per Sprint, TTR <5 min |
| `/tmp/faza7_preflip_checklist.md` | ~220 | CRYPTO-style precise pre-flip per Sprint |
| `/tmp/sprint4_infra_IMPLEMENTATION_2026-05-27.md` | this | (ten plik) |

---

## 2. CLI usage examples per tool

### Tool 1 — rebuild_courier_whitelist
```bash
# Default (14d window, write to dispatch_state/courier_whitelist_v1.json):
python3 -m dispatch_v2.tools.rebuild_courier_whitelist

# Custom window + markdown sidecar:
python3 -m dispatch_v2.tools.rebuild_courier_whitelist \
    --days 30 \
    --out /tmp/whitelist_30d.json \
    --md /tmp/whitelist_30d.md

# Help:
python3 -m dispatch_v2.tools.rebuild_courier_whitelist --help
```

### Tool 2 — faza7_daily_kpi
```bash
# Today's report:
python3 -m dispatch_v2.tools.faza7_daily_kpi

# Specific date:
python3 -m dispatch_v2.tools.faza7_daily_kpi --date 2026-05-27 --out /tmp/kpi.md

# Cron-able quiet mode (default output path: /tmp/faza7_daily_kpi_YYYY-MM-DD.md):
python3 -m dispatch_v2.tools.faza7_daily_kpi --quiet
```

### Tool 3 — analyze_shadow_logs
```bash
# Default 7d window:
python3 -m dispatch_v2.tools.analyze_shadow_logs

# Custom window + output:
python3 -m dispatch_v2.tools.analyze_shadow_logs --days 14 --out /tmp/shadow_14d.md
```

---

## 3. Verification (Adrian może testować TERAZ)

### Step 1 — compile + import check
```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2
python3 -m py_compile tools/rebuild_courier_whitelist.py tools/faza7_daily_kpi.py tools/analyze_shadow_logs.py
# Expected: zero output (success)
```

### Step 2 — run full test suite
```bash
/root/.openclaw/venvs/dispatch/bin/python -m pytest \
    dispatch_v2/tests/test_rebuild_courier_whitelist.py \
    dispatch_v2/tests/test_faza7_daily_kpi.py \
    dispatch_v2/tests/test_analyze_shadow_logs.py \
    -x --tb=short
# Expected: 25 passed in <2s
```

### Step 3 — smoke run wszystkie 3 tools z real data
```bash
# Whitelist rebuild (14d window):
python3 -m dispatch_v2.tools.rebuild_courier_whitelist --days 14 --md /tmp/wl_smoke.md
# Expected: "=== BUCKETS === / WHITELIST: N / CONDITIONAL: N ..." + 2 file writes

# Daily KPI:
python3 -m dispatch_v2.tools.faza7_daily_kpi --whitelist /tmp/courier_whitelist_v1.json
# Expected: "Wrote: /tmp/faza7_daily_kpi_2026-05-27.md / override 7d=XX.X% readiness=NOT READY"

# Shadow analyzer:
python3 -m dispatch_v2.tools.analyze_shadow_logs --days 7
# Expected: "Wrote: /tmp/shadow_weekly_summary_2026-05-27.md / drive_n=0 c2_n=N c5_n=N carry_n=0"
```

### Step 4 — inspect first KPI report
```bash
cat /tmp/faza7_daily_kpi_2026-05-27.md
# Expected sections: 1. Override rate / 2. R6 breach / 3. Top 5 whitelist /
#                    4. drive_min calibration / 5. Kebab Król / 6. Readiness gate
```

### Step 5 — smoke read live data
Z dzisiejszego smoke runu (2026-05-27 19:00 UTC):
- Override rate 24h: **70.5%** (141/200)
- Override rate 7d: **77.6%** (1101/1418)
- Override rate 14d: **78.0%** (1523/1952) — confirms diag_q1_v2 baseline
- R6 breach AUTO bucket: **5.2%** (7/134) — OK
- R6 breach ALERT: **13.2%** (21/159)
- Top WHITELIST kurier: **cid 393 Michał K. (std+)** — confirms Q1v2 Agent 1 D.4 operator favorite
- Kebab Król dinner breach: **27.6%** (8/29) — confirms kebab_krol_diagnostic.md "26.7% dinner" finding
- **Readiness: NOT READY** (override>60%, KK dinner>15%)

---

## 4. Cron-able timer designs (.md only, NIE deploy)

W `/tmp/faza7_rollback_runbook.md` sekcja 6 zawiera kompletne pliki systemd dla:
- `dispatch-faza7-kpi.timer` — codziennie 06:00 Warsaw
- `dispatch-faza7-whitelist.timer` — codziennie 06:30 Warsaw (po KPI)

Deploy command sketch (NIE TERAZ — Adrian decision):
```bash
sudo cp dispatch-faza7-{kpi,whitelist}.{timer,service} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now \
    dispatch-faza7-kpi.timer dispatch-faza7-whitelist.timer
```

---

## 5. Final go-no-go checklist dla Faza 7 T1 ramp-up

Pełna checklist w `/tmp/faza7_preflip_checklist.md`. Critical path tutaj:

### Pre-Faza-7-T1 sekwencja (Adrian's critical path)

1. **Sprint 1 (drive_min calibration) LIVE shadow → wait 7d**
   - Implementation done przez Sprint 1 agent (kod w `auto_proximity_classifier.py`)
   - Shadow log `drive_min_calibration_log_v2.jsonl` rośnie
   - Po 7d: pre-flip checklist sekcja "Sprint 1"
   - Flip → 7d watch

2. **Sprint 2.1 (Kebab Król dinner exclusion) — immediate flip po Sprint 1 stable**
   - Implementation done przez Sprint 2 agent
   - Low risk, flag flip
   - 7d watch → KK dinner breach <15% target

3. **Sprint 2.2 (carry chain penalty) LIVE shadow → wait 14d**
   - Implementation done przez Sprint 2 agent
   - Shadow log `carry_chain_shadow_log.jsonl` rośnie
   - Po 14d: pre-flip checklist sekcja "Sprint 2.2"
   - Flip → 14d watch

4. **Faza 7 T1 — full ramp pre-flip**
   - Pre-cond: Sprint 1+2.1+2.2 stable ≥14d each
   - Override rate 7d < 60% (was 78%) — KEY GATE
   - Run `rebuild_courier_whitelist.py` → ≥3 WHITELIST cids
   - All 8 boxes in `faza7_preflip_checklist.md` sekcja "Faza 7 T1" → TICK
   - Operator team notified 24h prior
   - Flip + 1h hands-on watch

5. **T1 → T2 → T3 ramp**
   - T1 stable 14d (≥30 AUTO decisions, override AUTO bucket <30%) → T2
   - T2 stable 14d (≥80 AUTO, override <30%) → T3
   - T3 = full AUTO graduation

### Adrian go-no-go cards (krytyczne)

| Gate | Required value | Source | Now (2026-05-27) | Status |
|---|---|---|---|:---:|
| Override 7d | <60% | `faza7_daily_kpi.py` | 77.6% | ✗ NOT READY |
| Calibration bias | <10 min | Sprint 1 shadow log | n/a (not LIVE) | n/a |
| KK dinner breach | <15% | `faza7_daily_kpi.py` | 27.6% | ✗ NOT READY |
| WHITELIST size | ≥3 cids | `rebuild_courier_whitelist.py` | 1 (Michał K.) | ✗ NEED MORE |
| Sprint 1+2.1+2.2 LIVE | all True | `flags.json` | none flipped yet | ✗ pending |

Decyzja: **PRE-Faza-7-T1, wait for Sprints 1+2 LIVE + ≥14d stable each.**

---

## 6. Hard constraints respected

- [x] NIE git commit (working tree changes only)
- [x] NIE deploy systemd timers (designs in `.md` only)
- [x] NIE modyfikacja production code (`dispatch_v2/*.py` poza `tools/`)
- [x] Wszystkie skrypty z `--help` (idiomatic CLI)
- [x] Tests dla każdego skryptu w `dispatch_v2/tests/test_*.py`
- [x] Output convention: `/tmp/faza7_*.md` dla docs, `dispatch_v2/tools/*.py` dla scripts

---

## 7. Cross-references (dla nowych sesji CC)

- Sprint 1 design: `/tmp/drive_min_calibration_design.md`
- Sprint 2 KK diagnostic: `/tmp/kebab_krol_diagnostic.md`
- Q1v2 baseline: `/tmp/diag_q1_v2_2026-05-27.md`
- Whitelist Agent B (v2 dual lens): `/tmp/courier_whitelist_proposed.{json,md}`
- Faza 7 ramp-up FINAL diag: `/tmp/diag_faza7_rampup_FINAL_2026-05-27.md`
- Q2 narrower fidelity caveats: `/tmp/q2_narrower_summary.md`
- Rollback runbook: `/tmp/faza7_rollback_runbook.md`
- Pre-flip checklist: `/tmp/faza7_preflip_checklist.md`
- Memory:
  - `memory/lgbm_roadmap.md` — Faza 7 Etap 0 LIVE shadow + thresholds
  - `memory/sprint_timeline.md` — CURRENT HANDOFF (this sprint)
  - `memory/tech_debt_backlog.md` — P0-P3 sequence
