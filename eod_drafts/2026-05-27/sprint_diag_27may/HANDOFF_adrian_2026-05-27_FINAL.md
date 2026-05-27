# Adrian — handoff post-implementation 4 sprintów (2026-05-27)

**Stan:** 4 agenty wdrożyły code w workspace. **NIC nie jest LIVE w runtime serwisów** — code w plikach, ale dispatch serwis ma starą wersję w pamięci do czasu restart.

**Twoje kroki przed live deploy są na końcu dokumentu (sekcja 6).**

---

## 1. Co zostało wdrożone (workspace)

### Sprint 1 — drive_min calibration (Agent W1) ✅
**Implementacja:** Alt A (pos_source offset table + floor guard)

| Plik | Status | LOC |
|------|--------|-----|
| NEW `dispatch_v2/drive_min_calibration.py` | utworzony | ~140 |
| NEW `dispatch_v2/tests/test_drive_min_calibration_v2.py` | utworzony, 26/26 PASS | ~290 |
| MOD `auto_proximity_classifier.py` | +3 helpery + hook | (backup: `.bak-pre-drive-calib-2026-05-27`) |
| MOD `flags.json` | +2 flagi (default OFF) | (backup) |
| NEW empty `dispatch_state/drive_min_calibration_log_v2.jsonl` | pre-created | 0 |

**Flagi:**
- `ENABLE_DRIVE_MIN_CALIBRATION_V2 = false` (main, shadow first)
- `ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW = true` (shadow log capture)

### Sprint 2 — KK dinner + carry visibility (Agent W2) ✅
**Implementacja:** dwuetapowa

| Plik | Status |
|------|--------|
| MOD `auto_proximity_classifier.py` | KK guard ZoneInfo DST-safe (backup `.bak-pre-sprint2-2026-05-27`) |
| MOD `common.py` | 4 env constants + 3 helpers + CARRY_RISK_LIST (backup) |
| MOD `dispatch_pipeline.py` | carry_chain integration w `_v327_eval_courier` (backup) |
| MOD `shadow_dispatcher.py` | shadow log propagation (backup) |
| MOD `flags.json` | +2 flagi |
| NEW `tests/test_kk_dinner_exclusion_v2.py` | 10/10 PASS |
| NEW `tests/test_carry_chain_penalty_v2.py` | 18/18 PASS |

**Flagi:**
- `ENABLE_KEBAB_KROL_DINNER_EXCLUSION = true` (default ON — niski risk, conditional)
- `ENABLE_CARRY_CHAIN_PENALTY = false` (shadow first, 14d wait)

### Sprint 3 — operator-favorites research (Agent W3) ✅
**Output:** `/tmp/operator_favorites_root_cause_2026-05-27.md` (18.9KB)

**KLUCZOWE FINDING — V3.16 silently undone bug:**
- `_demote_blind_empty` jest wywoływane PRZED V325/V326 funkcjami
- V325 (line 835) + V326 (line 643 + 2 inne) wszystkie wywołują `feasible.sort()` PO demote
- Re-sort niweczy V3.16 reorder
- Verified concrete case: oid=474624 (19.05) — log mówi `new_top_cid=400` po demote, shadow log pokazuje 413 jako finalny
- **NIE jest w `tech_debt_backlog.md`** — prawdopodobnie obniża quality scoringu od V3.25 release

**3 actions Sprint follow-up (NIE zaimplementowane w tym dispatch'u — research only):**
- **P0:** Przenieść `_demote_blind_empty` ZA V325/V326 (dispatch_pipeline.py:3151 zamiast 3139). ~30 min coding.
- **P1:** Recalibrate `V326_SPEED_MULTIPLIER_MAP` (common.py:1275) — std+=1.056→-2.8, slow=1.111→-5.55, trafia 4 z 5 LATENT targets.
- **P2:** Zmniejszyć max penalty `bonus_r1_corridor` z -35 do -10.

### Sprint 4 — infrastructure (Agent W4) ✅

| Plik | Status |
|------|--------|
| NEW `dispatch_v2/tools/rebuild_courier_whitelist.py` | 367 LOC, CLI (--days/--out/--md) |
| NEW `dispatch_v2/tools/faza7_daily_kpi.py` | 313 LOC, 6 sekcji + readiness gate |
| NEW `dispatch_v2/tools/analyze_shadow_logs.py` | 226 LOC, weekly summary |
| NEW `tests/test_rebuild_courier_whitelist.py` | 9/9 PASS |
| NEW `tests/test_faza7_daily_kpi.py` | 9/9 PASS |
| NEW `tests/test_analyze_shadow_logs.py` | 7/7 PASS |
| NEW `/tmp/faza7_rollback_runbook.md` | per-Sprint TTR <5min + Telegram templates |
| NEW `/tmp/faza7_preflip_checklist.md` | CRYPTO-style per Sprint |

**Smoke test na REAL data (19:00 UTC 27.05):**
- Override 7d = 77.6% (potwierdza Agent B 78.6%)
- KK dinner R6 breach = 27.6% (potwierdza Agent D 22.5%)
- WHITELIST z fresh rebuild = 1 cid (Michał K. std+)
- R6 AUTO = 5.2% / ACK = 8.1% / ALERT = 13.2%
- **Readiness gate = NOT READY**

---

## 2. Tests verification

```bash
cd /root/.openclaw/workspace/scripts
source /root/.openclaw/venvs/dispatch/bin/activate
python3 -m pytest dispatch_v2/tests/test_drive_min_calibration_v2.py \
  dispatch_v2/tests/test_kk_dinner_exclusion_v2.py \
  dispatch_v2/tests/test_carry_chain_penalty_v2.py \
  dispatch_v2/tests/test_rebuild_courier_whitelist.py \
  dispatch_v2/tests/test_faza7_daily_kpi.py \
  dispatch_v2/tests/test_analyze_shadow_logs.py -q
```

**Wynik:** **79/79 PASS** w 1.88s.

Agent W2 zweryfikował też pełną suitę regresji: **1560 passed / 41 failed / 7 skipped** vs baseline pre-Sprint 2 = 1531 passed / 42 failed → **ZERO REGRESJI** (failed pre-existing per `CLAUDE.md` "Known issues").

---

## 3. Flag config snapshot (final)

```json
{
  "AUTO_PROXIMITY_ENABLED": false,           // ramp-up flag, NIE LIVE
  "AUTO_PROXIMITY_SHADOW_ONLY": true,
  "AUTO_PROXIMITY_THRESHOLD": "T1",
  "ENABLE_DRIVE_MIN_CALIBRATION_V2": false,  // Sprint 1 main flag, OFF
  "ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW": true,  // shadow log ON
  "ENABLE_KEBAB_KROL_DINNER_EXCLUSION": true,    // Sprint 2.1 DEFAULT ON (low risk)
  "ENABLE_CARRY_CHAIN_PENALTY": false        // Sprint 2.2 OFF (14d shadow first)
}
```

---

## 4. ⚠ KRYTYCZNE — Code NIE jest LIVE bez service restart

**Stan obecny:**
- Code napisany w plikach `.py` ✅
- Tests PASS ✅
- Flagi ustawione ✅
- **ALE:** dispatch serwis runtime ma STARĄ wersję w pamięci Python (imports cached)
- `flags.json` jest hot-reload (load_flags() reads na każdy tick), ale **CODE wymaga import reload = service restart**

**Co to znaczy praktycznie:**
- `ENABLE_KEBAB_KROL_DINNER_EXCLUSION=true` w flags.json **nic nie robi** dopóki nie zrestartujesz serwisu — bo nowy kod (KK guard) nie jest jeszcze w pamięci
- `ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW=true` shadow log NIE pisze nic — bo `drive_min_calibration.py` nie jest jeszcze importowany w runtime serwisu
- Sprint 4 tools (`rebuild_courier_whitelist.py`, `faza7_daily_kpi.py`) — działają od razu, bo to CLI tools standalone (uruchamiasz je manually, nie są w persistent serwisie)

---

## 5. Pliki output do Twojego review

**Raporty implementation:**
- `/tmp/sprint1_drive_min_IMPLEMENTATION_2026-05-27.md` (10KB)
- `/tmp/sprint2_kk_carry_IMPLEMENTATION_2026-05-27.md` (14.6KB)
- `/tmp/sprint4_infra_IMPLEMENTATION_2026-05-27.md` (8.4KB)

**Research:**
- `/tmp/operator_favorites_root_cause_2026-05-27.md` (18.9KB)

**Operational docs:**
- `/tmp/faza7_rollback_runbook.md` (9.6KB) — TTR <5min per Sprint
- `/tmp/faza7_preflip_checklist.md` (10.3KB) — pre-flip każdego Sprintu

**Cumulative diagnostic (z Etapów 1-3):**
- `/tmp/diag_propozycje_2026-05-27.md` (Etap 1)
- `/tmp/diag_q1_backfill_2026-05-27.md` (Etap 2)
- `/tmp/diag_q1_v2_2026-05-27.md` (Etap 3 consolidation)
- `/tmp/diag_faza7_rampup_FINAL_2026-05-27.md` (Etap 4 pre-implementation plan)

---

## 6. **TWOJE KROKI — actionable handoff**

### Krok 1: Code review (przed jakimkolwiek deploy)
```bash
cd /root/.openclaw/workspace/scripts
# Sprint 1 review
diff dispatch_v2/auto_proximity_classifier.py.bak-pre-drive-calib-2026-05-27 \
     dispatch_v2/auto_proximity_classifier.py
# Sprint 2 review
diff dispatch_v2/auto_proximity_classifier.py.bak-pre-sprint2-2026-05-27 \
     dispatch_v2/auto_proximity_classifier.py
diff dispatch_v2/common.py.bak-pre-sprint2-2026-05-27 dispatch_v2/common.py
diff dispatch_v2/dispatch_pipeline.py.bak-pre-sprint2-2026-05-27 dispatch_v2/dispatch_pipeline.py
diff dispatch_v2/shadow_dispatcher.py.bak-pre-sprint2-2026-05-27 dispatch_v2/shadow_dispatcher.py
# New files
cat dispatch_v2/drive_min_calibration.py
cat dispatch_v2/tools/rebuild_courier_whitelist.py
cat dispatch_v2/tools/faza7_daily_kpi.py
```

**Czas:** ~30-45 min Twojego review.

### Krok 2: Decyzja o git commit
- (a) Commit wszystkie 4 sprinty razem (jeden commit z message "Sprint 1-4: drive_min calib + KK + carry visibility + infra")
- (b) Osobne commity per Sprint (lepsze dla rollback per sprint)
- (c) Tylko Sprint 4 tools (najmniejszy risk) + research, Sprint 1-2 hold dla późniejszego commitu po manualnej weryfikacji
- (d) Skip commit na razie — keep changes w workspace, test runtime manually najpierw

**Moja rekomendacja:** (b) osobne commity, bo każdy sprint ma osobny rollback path.

### Krok 3: Service restart (jeśli chcesz aktywować Sprint 1/2 code)
```bash
# Sprawdź który serwis runs LIVE
systemctl status dispatch.service dispatch-shadow.service 2>&1 | head -20
# Restart (decyzja Adrian'a — to widzialne dla operatora)
sudo systemctl restart dispatch-shadow.service  # lub dispatch.service
```

**WAŻNE:** restart powoduje 5-10s overhead w nowej propozycji decyzji. Nie rób w peak window (12-15 / 19-21 Warsaw).

### Krok 4: Verify post-restart (shadow mode)
```bash
# Sprint 1 — drive_min shadow log fills (czeka ~5 min na pierwszą decyzję)
wc -l /root/.openclaw/workspace/dispatch_state/drive_min_calibration_log_v2.jsonl
# Sprint 2.1 KK — następna KK dinner propozycja powinna mieć auto_route=ALERT
grep "kk_dinner_carry_risk_v2" /root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl
# Sprint 2.2 carry — shadow log w shadow_decisions.jsonl
grep "carry_chain_" /root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl | head -5
```

### Krok 5: Monitoring (Sprint 4 tools)
```bash
# Daily KPI dashboard
cd /root/.openclaw/workspace/scripts
source /root/.openclaw/venvs/dispatch/bin/activate
python3 -m dispatch_v2.tools.faza7_daily_kpi --days 1 --out /tmp/today.md
cat /tmp/today.md

# Whitelist rebuild
python3 -m dispatch_v2.tools.rebuild_courier_whitelist --days 7 \
  --out /root/.openclaw/workspace/dispatch_state/courier_whitelist_v1.json --md /tmp/whitelist.md
```

### Krok 6: Schedule flip (po 7-14d shadow)
Per `/tmp/faza7_preflip_checklist.md`:
- **Day 7 (2026-06-03):** Sprint 1 drive_min calibration flip ON (po verification checklist)
- **Day 7 (2026-06-03):** Sprint 2.1 KK exclusion już LIVE (default ON) — verify operating
- **Day 14 (2026-06-10):** Sprint 2.2 carry chain flip ON (po 14d shadow data)
- **Day 28-35 (2026-06-24 do 07-02):** Faza 7 T1 30% ramp-up (jeśli readiness gate=READY)

---

## 7. ⚡ Quick-win standalone (TY decydujesz natychmiast)

**P0 z Agent W3 — V3.16 silently undone fix.** NIE zostało zaimplementowane w tym dispatchu (research-only mandate), ale to **30 min coding** które natychmiast może poprawić quality scoringu:

W `dispatch_pipeline.py` przenieść wywołanie `_demote_blind_empty` z linii ~3139 do ~3151 (ZA V325/V326 funkcjami). Detail w `/tmp/operator_favorites_root_cause_2026-05-27.md` sekcja "Recommended actions".

**Czy chcesz że ja to zaimplementuję jako Sprint 5 (osobny dispatch)?** ~30 min code + tests + backup. To jest niezależny fix od Sprint 1-4 — może być deployed paralelnie.

---

## 8. Status tasks

- ✅ Task #1 — Sprint 1 drive_min calibration impl
- ✅ Task #2 — Sprint 2 KK + carry visibility
- ✅ Task #3 — Sprint 3 operator-favorites research
- ✅ Task #4 — Sprint 4 infrastructure
- ✅ Task #5 — Final consolidation (this document)

**Wszystkie 5 task'ów completed.**

---

**Twoja decyzja:** Co dalej?
- (a) Code review wszystkiego (Krok 1) zanim cokolwiek dotkniesz
- (b) Commit Sprint 4 tools (najmniejszy risk) najpierw, reszta wait
- (c) Implementuj P0 V3.16 fix jako Sprint 5 (natychmiast, niezależnie)
- (d) Restart serwisu shadow + verify Sprint 1 logging w działaniu
- (e) Wszystko hold do jutra rana — przemyśl plan

Czekam na Twoje instrukcje.
