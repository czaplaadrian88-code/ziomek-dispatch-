# Faza 7-AUTO-PROXIMITY — Design Spec (rule-based, post-pivot 03.05)

**Data:** 2026-05-06
**Autor:** Claude Code (architectural draft + LIVE shadow deploy)
**Status:** ✅ **Etap 0 LIVE shadow od 2026-05-06 20:27 UTC** — pending tygodniowa kalibracja → Etap 2-3 (~15.05)
**Cel implementacji:** Tydzień 1-3 roadmap, 30% → 70% → 100% autonomy
**Deploy gate (30% LIVE Etap 3):** 15.05.2026 Pt off-peak (po 7-day shadow obs window + Adrian explicit ACK)

**Deployed components (commit `14b4e70`, tag `faza-7-auto-proximity-shadow-impl-2026-05-06`):**
- `auto_proximity_classifier.py` (NEW, 280 LOC pure function)
- `dispatch_pipeline.py` — `PipelineResult.auto_route` + `_classify_and_set_auto_route` helper z defense-in-depth
- `shadow_dispatcher.py` — auto_route fields w shadow log JSON
- `telegram_approver.py` — "🤖 PEWIEN" linijka w `format_proposal`
- `courier_resolver.py` — `_post_shift_start_synthetic_eligible` helper (Adrian decyzja A1)
- `common.py` — `ENABLE_AUTO_PROXIMITY_POST_SHIFT_5MIN` flag
- `flags.json` — `AUTO_PROXIMITY_ENABLED=false`, `SHADOW_ONLY=true`, `THRESHOLD=T1`

**Tests:** 21/21 classifier + 6/6 integration + 71/71 czasówka regression = 98/98 nowych+touched. PASS.

**Cross-refs (post-deploy):**
- Sprint close: `/root/.claude/projects/-root/memory/sprint_2026-05-06_evening_close.md`
- Project memory: `/root/.claude/projects/-root/memory/project_faza_7_etap0_shadow_live.md`

---

## 0. Scope statement (co NIE jest w scope)

Spec dotyczy **rule-based** AUTO-PROXIMITY (V3.27 baseline jako PRIMARY decision engine). To **NIE jest**:

- ❌ Faza 7 LGBM PRIMARY (CANCEL 03.05, kosmetyczna — `faza_7_design_spec_2026-05-02.md` deprecated)
- ❌ Faza 8 ALT Explorer (zbędne)
- ❌ Faza 10 A/B Test 30% Peak (zastąpione liniową progresją)
- ❌ Bundle optimization changes (Fix C bundle cap 8km LIVE od 01.05 — zostaje)

**W scope:**

- Decision branch w `dispatch_pipeline.py` po `verdict=PROPOSE` — klasyfikacja **AUTO_HIGH_CONF** vs **HUMAN_ACK** vs **ALERT** przed wysłaniem do shadow log
- Telegram UX 3 typy (informacyjny + countdown + ACK + alert)
- Auto-assign mechanism z 60s human override window w `telegram_approver.py`
- Granular flagi rollback (per-feature + global kill switch)

---

## 1. TL;DR

V3.27 zostaje primary scorer. Po `verdict=PROPOSE` z `dispatch_pipeline` dodajemy **klasyfikator confidence** (rule-based, deterministyczny) → 3 ścieżki:

| Ścieżka | Akcja | Telegram | Override |
|---|---|---|---|
| **AUTO** (high-conf) | Subprocess `run_gastro_assign` automatycznie po 60s | "🤖 AUTO ASSIGNED — {kurier} (60s przeszło)" + 8 reason buttons | 60s window: TAK / INNY / KOORD / 6 reason buttons → cancel + manual |
| **ACK** (low-conf / borderline) | Human decision required (obecny flow) | "⚠️ WYMAGA ACK — {kurier} ({reason_for_ack})" + standard keyboard | Bez timeoutu, human picks |
| **ALERT** (critical / degraded) | NIE auto-assign, NIE proposal — alert tylko | "🚨 ALERT — {reason}" | NIE applicable |

Cel Tydzień 1 (30%): ~30% propozycji przez AUTO, 65-68% ACK, 2-5% ALERT. Tydzień 2: 70%. Tydzień 3: 100% autonomy non-edge.

---

## 2. Klasyfikator confidence (rule-based, deterministyczny)

### 2.1 Wejście

`PipelineResult.verdict == "PROPOSE"` z dispatch_pipeline. Dane dostępne:

- `best: Candidate` — top-1 z scoring + feasibility
- `candidates: List[Candidate]` — top-N (sorted, `best == candidates[0]`)
- `pool_feasible_count: int`
- `pool_total_count: int`
- `restaurant`, `delivery_address`, `pickup_ready_at`
- `best.score`, `best.metrics` (zawiera `pos_source`, `bag_size`, `pickup_dist_km`, etc.)
- Per courier (z `fleet_snapshot`): `tier`, `shift_start`, `shift_end`, `pos_source`

### 2.2 Definicja AUTO_HIGH_CONF (Tydzień 1, gate 30%)

**Wszystkie warunki MUSZĄ być spełnione (AND):**

| # | Warunek | Próg T1 | Uzasadnienie |
|---|---|---|---|
| C1 | `pool_feasible_count >= 2` | min 2 | Brak realnej alternatywy = nie auto-assign (decyzja "i tak jeden") jest bezpieczna ale uczy złe wzorce); auto-assign powinien znaczyć "ten kurier wygrał z innymi" |
| C2 | Score margin top1 - top2 | `>= 15 pkt` | V3.27 typowa skala 0-100 z penalty -300; margin 15 = "wyraźna przewaga" (kalibracja po 1-week shadow) |
| C3 | `best.tier in {"gold", "std+"}` | gold lub std+ | Std/new tier = za mało historical evidence dla auto |
| C4 | `best.metrics["pos_source"] == "gps"` | strict GPS | `no_gps` / `pre_shift` / `none` = R-04 v2.0 disqualifies blind+empty (LIVE od 01.05) |
| C5 | Brak edge cases (patrz 2.3) | brak ANY | Dowolny edge case → ACK lub ALERT |
| C6 | `best.score >= 50` | absolute floor | Punkt poniżej 50 = pipeline already w trybie "best of bad", human powinien zobaczyć |

**Tydzień 2 (70%):** relax C2 do `>= 10`, C3 do `gold/std+/std`, C4 dopuszcza `no_gps` jeśli `tier=gold` AND nie peak. Re-kalibracja po Tydzień 1 obs (override rate >20% = za luźne, <5% = za sztywne).

**Tydzień 3 (100% non-edge):** relax C1 do `>=1` (solo wins OK), C6 do `>=30`. Edge cases ALERT/ACK pozostają.

### 2.3 Edge cases — wymuszają ACK lub ALERT

| Case | Wykrycie | Routing |
|---|---|---|
| Czasówka (`czas_odbioru >= 60`) | `new_order.czas_odbioru >= 60` | ACK (human waveline judgment) |
| `solo_fallback` | `best.metrics.get("solo_fallback")` | ACK (R1/R5/R8 ignored) |
| `best_effort` | `best.best_effort == True` | ACK (SLA violations w plan) |
| Bag overload soft | `best.bag_size >= bag_cap_soft_for_tier` | ACK |
| Schedule edge: kurier kończy `<=15 min` po pickup_ready | shift_end - pickup_ready_at <=15min | ACK |
| Świeży COURIER_ASSIGNED do innego (`<60s ago`) | event_bus query | ACK (race window safety) |
| Pool=0 feasible (KOORD/PROPOSE solo) | verdict path zostaje obecny | NIE w scope (KOORD już istnieje) |
| Czas_kuriera frozen z violation w plan | `plan.violations contains "frozen_window"` | ALERT (anomalia, escaluj) |
| Mass-fail (>50% candidates verdict=NO) | `pool_total_count - pool_feasible_count >= 0.5 * pool_total_count` AND `pool_total_count >= 4` | ALERT (regional brak kurierów / system zły) |
| Parser degraded (PARSER_STUCK active) | health endpoint :8888/health/parser != green | ALERT, halt AUTO globally |
| LGBM SHADOW disagreement >75% w 1h rolling | post-Faza 5.2 retrain, jeśli kiedyś przyjdzie | ACK (informacyjne, nie blokujące) |

### 2.4 Pseudokod klasyfikatora

```python
# Nowy moduł: dispatch_v2/auto_proximity_classifier.py
def classify_confidence(result: PipelineResult, fleet_snapshot, now, flags) -> str:
    """Returns: 'AUTO' | 'ACK' | 'ALERT'. Pure function. ~50 LOC."""
    if not flags.get("AUTO_PROXIMITY_ENABLED", False):
        return "ACK"  # global kill = standard flow
    if _parser_degraded():
        return "ALERT"
    if _mass_fail(result):
        return "ALERT"
    if _has_frozen_window_violation(result.best):
        return "ALERT"

    edge = _detect_edge_case(result, fleet_snapshot, now)
    if edge:
        return "ACK"  # log edge type for telemetry

    threshold = flags.get("AUTO_PROXIMITY_THRESHOLD", "T1")  # T1/T2/T3
    if not _meets_high_conf(result, threshold):
        return "ACK"

    return "AUTO"
```

**ZASADA:** classifier jest pure function, deterministic, side-effect-free. Telemetry emit (counter per route + per edge type) na poziomie `dispatch_pipeline`, NIE w classifier.

---

## 3. Integration point w `dispatch_pipeline.py`

### 3.1 Gdzie

Po linii ~2516 (verdict="PROPOSE" z full feasible pool). Wzbogacić `PipelineResult` o pole `auto_route: str` ∈ {"AUTO", "ACK", "ALERT"}:

```python
return PipelineResult(
    order_id=order_id,
    verdict="PROPOSE",
    auto_route=classify_confidence(...),  # NEW
    auto_route_reason=...,                 # NEW (debugging/telemetry)
    best=top[0],
    candidates=top,
    ...
)
```

### 3.2 Backward compat

`auto_route` z domyślem `"ACK"` jeśli flag OFF — istniejący telegram_approver flow nietknięty (czyta verdict, nie auto_route).

### 3.3 Shadow log enrichment

`shadow_dispatcher._serialize` (LOCATION A + LOCATION B per Lekcja #11) dodaje:

```json
{
  "auto_route": "AUTO|ACK|ALERT",
  "auto_route_reason": "high_conf_t1 | edge:czasowka | mass_fail | ...",
  "score_margin": 23.4,
  "tier_best": "gold"
}
```

Pozwala na 1-week obs window classification rate **przed** flagą flip = empirical calibration C1-C6 progów.

---

## 4. Telegram UX 3-typy (`telegram_approver.py`)

### 4.1 Format wiadomości

#### 4.1.1 AUTO ASSIGNED

```
🤖 AUTO ASSIGNED #469912
Bartek O. (gold, GPS) — score=87.2 (Δ+23 vs 2nd)

[restauracja] → [klient] (3.2km / 12 min)
Pickup ready: 14:32 (za 4 min)

⏱ Auto-assign za 60s — kliknij aby anulować:
[❌ ANULUJ] [TAK NOW] [INNY] [KOORD]
[6× reason buttons (V3.19i)]
```

Po 60s (lub click ANULUJ → cancel countdown):
- Bez click: edit message → "✅ AUTO ASSIGNED 14:32:45 (subprocess OK)"
- Click ANULUJ: edit → "🚫 AUTO ANULOWANY — wybierz manualnie:" + standard keyboard
- Click TAK NOW: assign natychmiast (skip 60s)
- Click INNY: standard inny flow
- Click KOORD: standard koord flow
- Click reason button: cancel auto + record reason + waiting for assign decision

#### 4.1.2 WYMAGA ACK (obecny flow, lekko zmodyfikowany header)

```
⚠️ WYMAGA ACK #469913 — {edge_reason_short}
Kacper Sa. (std+, GPS) — score=72.1 (Δ+8 vs 2nd)
{trasa, timeline, kandydaci — bez zmian z V3.19i}

[TAK] [INNY] [KOORD]
[6× reason buttons]
```

`edge_reason_short`: `"czasówka 60min"`, `"solo fallback"`, `"shift kończy 14:50"`, `"bag overload"`, `"borderline score"`.

#### 4.1.3 ALERT (nowy)

```
🚨 ALERT #469914 — {alert_reason}

Pool feasible: 0/12 (mass fail)
Restauracja: Mama Thai → Centrum
Pickup ready: 14:32

NIE proponowano kuriera. Wymagana ręczna decyzja w panelu.
[KOORD] [SKIP]
```

ALERT pisze TYLKO `KOORD` lub `SKIP` (no auto, no inny). Adrian/Bartek widzi powód → reaguje.

### 4.2 60s countdown mechanism

Implementacja w `proposal_sender` task pool (asyncio):

```python
# state["pending"][oid] dostaje pole "auto_route" + "auto_assign_at"
state["pending"][oid] = {
    ...,
    "auto_route": rec.get("auto_route", "ACK"),
    "auto_assign_at": (datetime.now(utc) + timedelta(seconds=60)).isoformat() if rec.get("auto_route") == "AUTO" else None,
}

# Nowy coroutine: auto_assign_executor(state) — tickuje co 5s, sprawdza pending z auto_route="AUTO":
async def auto_assign_executor(state):
    while not _shutdown:
        for oid, pending_rec in list(state["pending"].items()):
            if pending_rec.get("auto_route") != "AUTO":
                continue
            if pending_rec.get("cancelled_by_human"):
                continue  # ANULUJ button kliknięty
            if datetime.now(utc) >= datetime.fromisoformat(pending_rec["auto_assign_at"]):
                # Fire run_gastro_assign + edit message
                ok, msg = await asyncio.to_thread(run_gastro_assign, oid, kurier_name=..., time_minutes=...)
                # Edit Telegram message confirming
                # append_learning record source=auto_assigned
                state["pending"].pop(oid)
        await asyncio.sleep(5)
```

### 4.3 Override mechanics

- ANULUJ button → callback handler ustawia `pending_rec["cancelled_by_human"] = True` + edit message do "WYMAGA ACK manual" (przejście AUTO → ACK po cancel).
- TAK NOW → identyczne jak obecny TAK callback (immediate assign).
- KOORD/INNY/reason buttons → identyczne jak obecny flow, ale dodatkowo cancel countdown.

### 4.4 Idempotency

`run_gastro_assign` subprocess + state machine zapewnia że duplicate assign = no-op (panel rzuca błąd jeśli order już assigned). Dodatkowo: `state["pending"].pop(oid)` przed firing → race window 0s (cancel po pop = no-op).

---

## 5. Flagi (granular rollback per Lekcja #72)

```json
// flags.json (hot-reload, fresh per panel-watcher tick)
{
  "AUTO_PROXIMITY_ENABLED": false,           // global kill switch
  "AUTO_PROXIMITY_THRESHOLD": "T1",          // "T1" | "T2" | "T3"
  "AUTO_PROXIMITY_AUTO_ASSIGN_DELAY_SEC": 60,
  "AUTO_PROXIMITY_ALERT_ENABLED": true,      // ALERT path można wyłączyć osobno
  "AUTO_PROXIMITY_SHADOW_ONLY": true         // sklasyfikuj+log, NIE wykonuj auto-assign (default ON dla pre-deploy obs window)
}
```

**ENV override (instant):** `AUTO_PROXIMITY_DISABLE=1` → wymusza `enabled=False` bez rewrite flags.json (5s recovery via systemctl set-environment).

**Z3 design:** każdy próg progowy w flagach (NIE hardcoded const). T1/T2/T3 → tabela threshold w common.py:

```python
AUTO_PROXIMITY_THRESHOLDS = {
    "T1": {"min_pool": 2, "min_margin": 15.0, "tiers": ["gold", "std+"], "min_score": 50, "strict_gps": True},
    "T2": {"min_pool": 2, "min_margin": 10.0, "tiers": ["gold", "std+", "std"], "min_score": 40, "strict_gps": False},
    "T3": {"min_pool": 1, "min_margin": 5.0,  "tiers": ["gold", "std+", "std", "new"], "min_score": 30, "strict_gps": False},
}
```

---

## 6. Plan deploy (per CLAUDE.md per-step ACK gates)

### Etap 0: Pre-deploy (Tydzień 1, 06-13.05)

- [x] Spec ACK Adrian
- [ ] F4 LGBM 1-week obs window do 13.05 (pre-existing gate, niezależny)
- [ ] Implement `auto_proximity_classifier.py` (~50 LOC) — pure function + 8 unit testów
- [ ] Wire `auto_route` field do `PipelineResult` + serialize w shadow_dispatcher LOCATION A+B
- [ ] Flag default `AUTO_PROXIMITY_ENABLED=false` + `AUTO_PROXIMITY_SHADOW_ONLY=true`
- [ ] Deploy shadow-only: classifier liczy + loguje, NIE wykonuje auto-assign. Cel: empirical rates per route bez ryzyka prod.

### Etap 1: Calibration (14.05)

- Analiza 1-week shadow: distribution AUTO/ACK/ALERT, edge case frequencies, score margin histogram
- Adjust progi T1 jeśli AUTO rate << 30% lub >> 35%
- Adrian ACK kalibracji

### Etap 2: Telegram UX implementation (14-15.05)

- `auto_assign_executor` coroutine + ANULUJ button + edit message helpers
- Test mode (`TEST_MODE=1`): symuluj 60s countdown bez subprocess
- 5/5 unit testów + 1 integration test (mock subprocess)
- Bartek user_id w `KONIEC_AUTHORIZED_USER_IDS` jeśli jeszcze nie

### Etap 3: 30% LIVE (15.05 Pt off-peak deploy ~22:00 UTC)

Pre-conditions GO:
- F4 obs window decision: GO lub HOLD (niezależnie — AUTO_PROXIMITY nie zależy od LGBM)
- shadow obs distribution: AUTO 25-35%, edge cases <10%, ALERT <5%
- 0 critical incidents Tydzień 1 (parser, czasówka)
- Adrian explicit ACK ("flip enabled=true")

Deploy:
- `cp flags.json flags.json.bak-pre-auto-proximity-2026-05-15`
- Flip `AUTO_PROXIMITY_SHADOW_ONLY=false` + `AUTO_PROXIMITY_ENABLED=true` (atomic)
- Telegram restart **wymaga ACK Adrian** (proposal_sender + auto_assign_executor coroutines)
- Monitor 15min: pierwszy AUTO ASSIGNED?
- 1h obs: override rate, success rate

### Etap 4: 70% (Tydzień 2, ~21.05)

- Re-kalibracja po 5-day prod obs T1
- Flip `AUTO_PROXIMITY_THRESHOLD=T2`
- Re-deploy NIE wymaga restartu (flag hot-reload)

### Etap 5: 100% non-edge (Tydzień 3, ~28.05)

- `AUTO_PROXIMITY_THRESHOLD=T3`
- ALERT path zostaje (edge cases ALWAYS bypassują AUTO)

---

## 7. Go/No-Go criteria per gate

### Gate 30% LIVE (15.05)

| Metric | Target | Source |
|---|---|---|
| Shadow obs sample size | >=200 propozycji | shadow_dispatcher log |
| AUTO rate | 25-35% | classifier counter |
| ACK rate | 60-70% | classifier counter |
| ALERT rate | <5% | classifier counter |
| Edge case false positives (manual review) | <2 / 50 | Adrian sample audit |
| F4 LGBM obs incident-free | TAK | pre-existing gate |
| Parser health 7-day green | TAK | :8888/health/parser |
| Czasówki Fix 8 stable | TAK | dispatch-czasowka.timer |

### Gate 70% (Tydzień 2)

| Metric | Target |
|---|---|
| AUTO override rate (30% live obs) | <15% (Adrian/Bartek anulowali <15% auto-assignów) |
| AUTO success rate (run_gastro_assign exit=0) | >=98% |
| AUTO false positive (Adrian retrospective ocena) | <5% / 50 sample |
| Customer SLA delivered <35min | nie pogorszone vs pre-deploy baseline |
| Bartek dispatch hours/dzień | spadek vs baseline 8h |

### Gate 100% (Tydzień 3)

| Metric | Target |
|---|---|
| AUTO override rate | <8% |
| AUTO false positive | <3% |
| Bartek hours | <=2h/dzień |
| Adrian hours | <=30min/dzień |
| Edge ALERT actionable rate | >=80% (alert prowadzi do real action) |

### Auto-rollback triggers (any → instant `AUTO_PROXIMITY_ENABLED=false`)

- Override rate >25% w 1h rolling window
- 2+ kolejne `run_gastro_assign exit!=0` w 5min
- Parser STUCK alert active
- Critical service down (panel-watcher / shadow / telegram)
- Adrian Telegram cmd `/auto_off` (NEW handler do dodania w Etap 2)

---

## 8. Rollback procedury

### Soft rollback (per-feature)

```bash
# Wyłącz auto-assign, zostaw classification + ACK header enrichment
python3 -c "import json; p='/root/.openclaw/workspace/scripts/flags.json'; d=json.load(open(p)); d['AUTO_PROXIMITY_SHADOW_ONLY']=True; json.dump(d, open(p,'w'), indent=2, ensure_ascii=False)"
# Hot-reload — bez restart, pierwszy następny tick pickuje
```

### Hard rollback (global kill, 5s)

```bash
sudo systemctl set-environment AUTO_PROXIMITY_DISABLE=1
sudo systemctl restart dispatch-shadow.service  # py_compile + import check FIRST per CLAUDE.md
# Telegram NIE restart bez ACK Adrian
```

### Nuclear rollback (revert kod)

```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2
git revert <faza-7-auto-proximity-merge-commit>  # NEW commit, NIE reset --hard
sudo systemctl restart dispatch-shadow dispatch-panel-watcher
# Telegram restart Z ACK Adrian
```

---

## 9. Tests plan

### 9.1 Unit (`tests/test_auto_proximity_classifier.py`)

- 8 testów classifier pure function (T1/T2/T3 × edge cases × happy path)
- Property-based: classifier deterministic dla identycznych inputs
- Edge: pool=0, pool=1, score margin=0, czasowka=60, solo_fallback=True
- Mass-fail detection threshold

### 9.2 Integration (`tests/test_auto_proximity_pipeline.py`)

- Mock dispatch_pipeline.assess_order → assert PipelineResult.auto_route present
- Shadow_dispatcher serialize roundtrip (LOCATION A+B per Lekcja #11)
- Flag OFF → auto_route="ACK" zawsze

### 9.3 Telegram (`tests/test_auto_assign_executor.py`)

- 60s countdown fires subprocess
- ANULUJ przed 60s → no subprocess, edit message
- TAK NOW przed 60s → subprocess immediately
- Idempotency: re-fire same oid = no-op

### 9.4 Smoke (post-deploy)

- 5 min observability po flip ENABLED=true: pierwszy AUTO ASSIGNED w peak hour
- Zero `run_gastro_assign exit != 0`
- Zero NameError / ImportError w logach
- Lat p95 classify <5ms (pure function, brak external calls)

---

## 10. Risks + mitigations

| Risk | Mitigation |
|---|---|
| Classifier auto-assigns złego kuriera (np. shift edge case nie wykryty) | Edge case list 2.3 + Adrian sample audit przed flag flip + override rate trigger auto-rollback |
| Race: AUTO fires gdy panel już zmienił stan (kurier wziął inny order między PROPOSE a 60s) | Idempotency w panel + state-machine `<60s recent assignment` edge case → ACK |
| Telegram message edit fails (Adrian off-network) | run_gastro_assign nie zależy od edit success — message edit best-effort, append_learning canonical record |
| Bartek nieautoryzowany do ANULUJ | KONIEC_AUTHORIZED_USER_IDS lista — dodać Bartek user_id do ANULUJ uprawnień (separate flag) |
| Hot-reload flag race (mid-decision flip) | Flag czytany raz na początku assess_order, snapshot dla decyzji — nie pickup mid-flight |
| Mass-fail false positive (legitymny low pool nocą) | Threshold uwzględnia `pool_total_count >= 4` minimum — nocna 1-2 kurierów nie triggeruje |
| Classifier latency w hot path | <5ms target, pure function, no I/O — measure w smoke |
| Parser STUCK during AUTO | ALERT path + halt_auto flag (Etap 2 dodać `_parser_degraded()` check w classifier) |

---

## 11. Open questions (do ACK Adrian PRZED implementacją)

1. **C2 score margin próg T1=15:** kalibrować przed deploy w shadow obs, czy zaufać ekspercką wartość 15? (Z2 — ja proponuję shadow-first kalibrację, 15 jako placeholder)
2. **C4 strict GPS T1:** `no_gps`/`pre_shift` ZAWSZE → ACK w T1? Czy gold tier pre_shift OK dla auto? (proponuję: T1 strict, T2 relax dla gold)
3. **Czasówka routing:** ZAWSZE ACK czy TYLKO peak czasówki ACK? (proponuję ZAWSZE ACK w T1 — Bartek waveline)
4. **Bartek ANULUJ uprawnienia:** dodać Bartek user_id obok Adrian? Czy tylko Adrian?
5. **ALERT KOORD button:** czy ALERT może auto-przekazać do KOORD (kurier id=26 virtual) bez Adrian, czy zawsze human gate?
6. **`/auto_off` Telegram cmd:** dodać do scope Etap 2 czy oddzielnie?
7. **Telegram restart timing:** Etap 2 wymaga restart `dispatch-telegram` — kiedy off-peak window OK? (sobota rano? niedziela rano? — peak windows reference w memory)

---

## 12. Estymata wysiłku

| Etap | Wysiłek | Aider/Self |
|---|---|---|
| Etap 0 classifier + integration | 2-3h | classifier=Self (judgment), integration plumbing=Aider deepseek |
| Etap 0 shadow log enrichment | 30min | Aider (mechaniczny) |
| Etap 0 unit testy | 1-1.5h | Aider boilerplate |
| Etap 0 deploy shadow-only | 30min | Self |
| Etap 1 calibration (1 week obs analysis) | 1-2h | Self (judgment) |
| Etap 2 Telegram UX coroutines | 3-4h | Self (state machine) + Aider (helpers) |
| Etap 2 Telegram tests | 1.5h | Aider |
| Etap 3 prod deploy + smoke | 1h | Self |
| **Łącznie do gate 30% LIVE** | **10-13h** rozłożone Tydzień 1-2 | mix |
| Etap 4-5 (re-kalibracja) | 1h każda | Self |

---

## 13. Cross-references

- `project_ziomek_nadajesz_v3_2026-05-04.md` — pivot context
- `ZIOMEK_MASTER_KB.md` I.4 — high-level architektura
- `lekcja_72_2026-05-05.md` — granular flag rollback pattern (anchor design)
- `architektura_lgbm_continuous_learning.md` — Phase 1-4 ML roadmap (Faza 7 NIE używa LGBM, ale trzymamy roadmap żywy)
- `feedback_panel_session_singleton.md` — `dispatch-telegram` restart constraint
- `reference_peak_blackout_windows.md` — kiedy NIE deploy
- `dispatch_v2/dispatch_pipeline.py:2516+` — integration point
- `dispatch_v2/telegram_approver.py:905` — `proposal_sender`
- `dispatch_v2/telegram_approver.py:847` — `run_gastro_assign`

---

## 14. Decyzje architektoniczne (post-pivot Z1+Z2+Z3 alignment)

- **Z1 (autonomia):** Tydzień 4 target Bartek <=2h, Adrian <=30min — Etap 5 100% non-edge realizuje
- **Z2 (jakość):** classifier deterministic + 1-week shadow calibration + edge case list 11 wykrytych — root cause przed fix dla każdego false-positive
- **Z3 (na lata):** progi T1/T2/T3 w flagach, multi-tier threshold table, rule-based (NIE ML black-box) — multi-tenant ready (Restimo / Bolt Food kiedy będzie)

**Anti-pragmatic:** zero hardcoded thresholds w hot path, classifier pure function (testable in isolation), shadow-only mode mandatory przed flag flip.

---

**END OF SPEC.**

ACK Adrian wymagany dla:
- 7 open questions (sekcja 11)
- progi T1 (sekcja 2.2 tabela)
- estymata 10-13h (sekcja 12)
- deploy gate 15.05 vs alternative date

Po ACK: utworzę implementation tickets per etap + rozpocznę Etap 0 classifier draft.
