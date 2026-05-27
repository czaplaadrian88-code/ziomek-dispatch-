# Finalna konsolidacja — diagnoza Ziomek + Faza 7 ramp-up plan

**Data:** 2026-05-27 ~19:00 Warsaw
**Coordinator:** CC Opus 4.7
**Etapy:**
- Etap 1: diagnostyka 5 propozycji chat-Claude'a (`/tmp/diag_propozycje_2026-05-27.md`)
- Etap 2: Q1 simpler backfill (`/tmp/diag_q1_backfill_2026-05-27.md`)
- Etap 3: Q1 v2 expanded — 3 agenty (`/tmp/diag_q1_v2_2026-05-27.md`)
- Etap 4: 4 agenty równolegle (Q2 narrower + whitelist + drive_min design + Kebab Król)
- ~~Etap pre-1: Q4 hook~~ ✅ LIVE w `~/.claude/settings.json`

**Wszystkie 4 agenty Etap 4 skończone.** Ten dokument konsoliduje wszystkie 6 raportów + 2 hooki.

---

## 1. Executive summary (jeśli czytasz tylko jedno — to to)

### Override rate jest **MASOWY** ale **NIESZKODLIWY dla outcome**
- **78.6% per unique order** operator zmienia decyzję Ziomka (Agent B, KRYTYCZNE recalibration mojego wcześniejszego 43% per-event)
- ALE **outcome (delivery_min) jest identyczny** override vs non-override (Etap 2)
- Operator overrides z **niemodelowanych dimensions** — system overrides ale nie poprawia jakości delivery

### Root cause autonomy gap = **3 współzależne problemy**

| # | Problem | Confidence | Evidence |
|---|---------|-----------|----------|
| 1 | **Drive_min bias +30 min** (pos_source reconstruction) — Ziomek nie widzi rzeczywistej pozycji kuriera | HIGH | Agent C: median +13 min globalnie, +30 dla last_*_pickup. Etap 2: 69% under-predict |
| 2 | **Carry/bag-stack invisible to model** — Ziomek nie widzi gdy kurier ma już bagaż z innej restauracji | HIGH (75%) | Agent D: Kebab Król R6 22.5% = carry penalty dinner peak. ML scorer ślepy (predicted 1/71, actual 16/71) |
| 3 | **Operator zna context dimensions** of model NIE widzi — scheduling/availability/restaurant-affinity | MEDIUM | Agent B latent: top operator-favorites (cid 370/400/393) mają n_proposed <30 ale 91-95% actuals to operator-force |

### Q2 counterfactual harness — **niewykonalny w obecnym stanie**
Agent A: simulator fidelity issue (95% tie rate) — Lekcja #11 trafiona. **Per-decision verdict niemożliwy** dopóki simulator nie jest skalibrowany. Jedyny real signal: courier_avoided subset 8.2%/8.2% (equal rate ziomek/operator-right — operator decision jest noisy ale ma sygnał).

### Faza 7 ramp-up — **DELAYED**, NIE pomijać sprintów pre-rampup

Rekomendacja: **3 sprinty pre-ramp** w sekwencji, każdy z konkretnym ACK gate:
- **Sprint 1: drive_min calibration** (8.5h CC, 14d shadow+monitor) → adresuje root cause #1
- **Sprint 2: carry/bag-stack visibility** (6-8h CC) → adresuje root cause #2 + Kebab Król fix
- **Sprint 3 (OPCJONALNY): operator-favorites investigation** (4-6h CC research) → adresuje root cause #3

**Total delay:** ~4-6 tygodni przed Faza 7 ramp-up. **ALE** każdy sprint dawał wymierny benefit niezależnie + redukował override rate fundamentalnie.

---

## 2. Pełna mapa findings z 6 raportów

### Etap 1 (5 propozycji chat-Claude'a)
- Quick Win #1 (reward vector): SIMPLER ALTERNATIVE — log 2 metryki, NIE 5
- Quick Win #2 (hindsight relabel): CONDITIONAL GO — wykonany w Etap 4 Agent A, **fidelity issue**
- Quick Win #3 (risk_score 5 features): ALREADY DONE — auto_proximity_classifier.py istnieje
- Quick Win #4 (context discipline): IMPLEMENTED — Q4 hook LIVE
- Quick Win #5 (bundle subtask splitter): SKIP — per-rule R1/R4/R5/R6/R8 już serializowane

### Etap 2 (Q1 simpler backfill)
- Override NIE poprawia delivery_time (cross-bucket, cross-action)
- Drive_min under-prediction +13 min median
- R6 prediction GOOD (+2 min median bias)
- AUTO/ACK/ALERT R6 breach 8/8/14% — classifier działa na agregat

### Etap 3 (Q1 v2 expanded — 3 agenty)
- Override rate **constant** ~42-44% **per event** wymiarach (confirmed 3 sources)
- Load-balancing hypothesis **REJECTED** empirycznie (+5.46 delta, operator wybiera bardziej obciążonego)
- Główny driver override = **trust per courier** (Top odrzucani 179/413/515, top wybierani 370/400/393)
- AUTO bucket safe w peak (R6 4-7%, wszystkie <10%)
- Kebab Król 22.5% R6 — exclusion candidate
- Paczki: 52 cases, +62% czas vs restaurants ale 0% hard breach — VETO_PACZKI_BRIDGE
- Mickiewicza czasówki: ZERO w sample — DEFER

### Etap 4 (4 agenty Q2/whitelist/drive_min/KK)

**Agent A (Q2 narrower):**
- 229 cases (AUTO 144 + avoided 50 + favorite 35)
- **95.1% tie** (fidelity issue Lekcja #11)
- Tylko courier_avoided subset ma real signal: 8.2%/8.2% (equal rate ziomek vs operator-right)
- Simulator underestimates A2D by ~23 min — potrzebuje calibration before useful

**Agent B (courier whitelist):**
- Baseline override **78.6% per unique order** (KRYTYCZNE recalibration)
- 85.4% z 1652 ordery proposed ≠ actual courier
- Adrian's strict criterion → **ZERO** kurierów qualifikuje
- Whitelist relative-to-baseline (tier-aware): **1 kurier** (Michał K. cid=393 std+)
- CONDITIONAL: 11 kurierów (incl. top-overridden 179/413/515 i top-favorite 370)
- BLACKLIST: 2 (cid=471 Łukasz W 96.5% override, cid=514 Tomasz Ch 21.6% R6)
- **LATENT (Adrian's CAVEAT zmaterializował się):** cid=400 Adrian R 95% actuals to operator-force, n_proposed=19 (Ziomek go nie proponuje)
- **Recommendation: NIE włączać Faza 7 AUTO yet**

**Agent C (drive_min calibration):**
- **TOP-1 root cause = `pos_source`**: no_gps +6.5 min, last_*_pickup/gps/post_wave **+30-35 min** (architektoniczny F4 problem)
- Rekomendacja: **Alt A — pos_source offset table + floor guard**
- 8.5h CC + 5h Adrian + 14 calendar days (7d shadow + 7d monitor)
- Realistic target: ~50% bias reduction (median +13 → +7 min)
- 60% target wymagałby F4 K2 interpolation LIVE flip (osobny sprint)

**Agent D (Kebab Król):**
- 44 KK orders 9d, observed breach 18.2% (cited 22.5%)
- **Root cause = carry/bag-stack penalty w dinner peak** (75% confidence)
- KK picked up + drive do innej restauracji + dostarcza drugi order pierwszy → KK siedzi 15-30 min w torbie
- Top breach courier: Andrei K (cid=484) — 43% breach KK (vs 15% baseline)
- Pattern: 0% lunch breach (n=9), 27% dinner (n=30) vs peer 7.7% dinner
- **Rekomendacja: conditional dinner-only exclusion** (17-21h), lunch zostaje w AUTO
- ML scorer ślepy: predicted 1/71, actual 16/71 — model nie nauczył się "central-pickup + dinner = carry-risk"
- Best_effort 3× peer rate dla KK — system widzi pool pusty ale dispatchuje

---

## 3. Cascade przyczynowo-skutkowy (zintegrowany model)

```
   pos_source=last_pickup (Ziomek's reconstruction)
                ↓
       Predicted drive_min +30 min underestimate
                ↓
   Ziomek proponuje kuriera ale ETA jest fantasy
                ↓
       Operator widzi GPS LIVE (panel)
                ↓
   Operator override (78.6% per order)
                ↓
   Operator wybiera kogoś bliższego (i czasem
   tego samego "trusted" kuriera: 370/400/393)
                ↓
   Outcome IDENTYCZNY (Etap 2): delivery_min ~18min
   PLUS: 95% per-decision tie (Q2 fidelity, Agent A)
                ↓
   Tymczasem: niewidzialne carry/bag-stack (Agent D)
   tworzy specific failure mode (KK dinner 27%)
                ↓
   Wynik: override masowy, outcome safe, autonomy
   ramp-up niemożliwy bez fix drive_min + carry visibility
```

---

## 4. **REKOMENDOWANY PLAN WDROŻENIA — Faza 7 path forward**

### Sprint 1 (pre-rampup, **mandatory**): Drive_min calibration
**Owner:** CC + Adrian gate
**Effort:** 8.5h CC + 5h Adrian + 14 calendar days (7d shadow + 7d monitor)
**Spec:** `/tmp/drive_min_calibration_design.md`

**Co:**
- Alt A: `pos_source` offset table + floor guard (`FLOOR_MIN = 8.0`)
- 6 cells: {no_gps, gps, pre_shift, last_assigned_pickup, last_picked_up_pickup, post_wave}
- Per-cell offset z empirycznych danych 14d
- Integration: `auto_proximity_classifier.py` + `common.py` (file:line w design doc)
- Flag: `ENABLE_DRIVE_MIN_CALIBRATION_V2` (default OFF shadow 7d, then flip)

**KPI:** median |actual − calibrated| z +13 → ~7 min (target 50% reduction)

**Verify:** shadow logging do `/dispatch_state/drive_min_calibration_log_v2.jsonl`, compare raw vs calibrated vs actual

**ACK gate przed Sprint 2:** Adrian zatwierdza wyniki shadow (7d) przed flip ON

### Sprint 2 (pre-rampup, **mandatory**): Carry/bag-stack visibility + Kebab Król fix
**Owner:** CC + Adrian gate
**Effort:** 6-8h CC + 2-3h Adrian
**Spec:** to be drafted (post Sprint 1)

**Co:**

**Quick win (30 min coding):** Kebab Król conditional dinner exclusion
```python
# w auto_proximity_classifier.py
if restaurant == "Kebab Król" and 17 <= warsaw_hour < 21:
    return ROUTE_ALERT, "kk_dinner_carry_risk"
```

**Główny sprint (5-7h):** Carry/bag-stack visibility feature
- Nowa feature: `secondary_pickup_in_chain` = czy kurier ma już bag z innej restauracji + ETA dla drugiego order > 15 min
- Dodać do score function: `bonus_carry_chain_penalty` per-restaurant calibration
- Dodać hard reject w R6 gdy bag-stack chain > 2 stops AND dinner peak
- Shadow log 14d

**KPI:** Kebab Król R6 breach 22% → <12% (peer baseline) w 14d post-deploy

**ACK gate przed Sprint 3 (or Faza 7):** Adrian zatwierdza po 14d

### Sprint 3 (pre-rampup, **opcjonalny**): Operator-favorites investigation
**Owner:** CC research + Adrian decyzje
**Effort:** 4-6h CC research + Adrian 2h knowledge sharing

**Co:**
- Investigate cid=400 Adrian R (95% actuals operator-force, n_proposed=19) — dlaczego Ziomek go nie proponuje?
- Check shift coverage, scheduling, restaurant-affinity patterns
- Możliwe outcomes:
  - (a) Adrian R jest pre-shift / off-shift gdy Ziomek dispatchuje → fix shift visibility
  - (b) Adrian R obsługuje specific restaurants (np. dla zaufanej restauracji owner mówi "tylko Adrian R") → fix restaurant-affinity feature
  - (c) Inny hard-coded operator rule → udokumentować

**KPI:** ≥3 operator-favorites z root cause identified i potential fix proposed

**ACK gate:** Adrian decyduje czy fix worthing przed Faza 7 czy odłóż

### Sprint 4 (final): Faza 7 ramp-up (POST Sprint 1+2)
**Owner:** Adrian decision + CC monitor

**Effort:** zero new code (config flag changes) + 14d monitoring

**Pre-conditions (MUST ALL PASS):**
1. ✅ Sprint 1 LIVE — drive_min calibration deployed, KPI median bias <8 min
2. ✅ Sprint 2 LIVE — carry visibility deployed, Kebab Król KPI <12%
3. ✅ Override rate **re-measured** w 14d post-sprint window. **Cel:** spadek z 78.6% do <60% per unique order
4. ✅ Updated whitelist re-built (`/tmp/courier_whitelist_proposed.{json,md}` regenerate)

**Konfiguracja flag (proposed, do akceptacji Adrian'a):**
```
AUTO_PROXIMITY_ENABLED: true   (po pre-condition 4)
AUTO_PROXIMITY_SHADOW_ONLY: false
AUTO_PROXIMITY_THRESHOLD: T1   (30% ramp)
AUTO_PROXIMITY_COURIER_WHITELIST: 393   (initially, expand after re-measure)
AUTO_PROXIMITY_RESTAURANT_BLACKLIST: ["Kebab Król"]  (dinner conditional already in Sprint 2)
AUTO_PROXIMITY_RESTAURANT_CONDITIONAL_OFFPEAK: ["Goodboy"]  (Agent 2 finding)
VETO_PACZKI_BRIDGE: true   (paczki forced ACK/ALERT)
VETO_CZASOWKA: true   (Mickiewicza defer)
```

**Daily monitoring KPI dla Tydzień 1:**
- R6 breach AUTO bucket ≤8% (baseline)
- Override rate AUTO whitelist <40% (target — empirically achievable if sprints work)
- Customer complaints / restaurant complaints (manual track via Telegram alerts)

**Rollback trigger:** instant flip `AUTO_PROXIMITY_ENABLED=false` jeśli ANY:
- R6 breach AUTO >12% w 24h
- Override rate AUTO whitelist >70% (sprintów nie pomogły)
- ≥3 customer complaints / day

**Tydzień 2 (T2 70%):** post Tydzień 1 OK + Adrian decision

**Tydzień 3 (T3 100% non-edge):** post Tydzień 2 OK + Adrian decision

---

## 5. Kalendar — szacunkowy timeline

| Tydzień | Działanie | CC time | Adrian time |
|---------|-----------|---------|-------------|
| 1 (28.05-03.06) | Sprint 1 deployment shadow | 8.5h | 1h ACK gate |
| 2 (04.06-10.06) | Sprint 1 shadow monitor + Sprint 2 design | 4h | 2h |
| 3 (11.06-17.06) | Sprint 1 flip ON + Sprint 2 deploy shadow | 6h | 2h |
| 4 (18.06-24.06) | Sprint 2 monitor + (opt) Sprint 3 research | 6h | 2h |
| 5 (25.06-01.07) | Sprint 2 flip ON + override rate re-measure | 4h | 2h |
| 6 (02.07-08.07) | **Faza 7 T1 30% LIVE** + monitoring | 4h | 3h |
| 7-8 (09.07-22.07) | T2 70% + monitoring | 4h | 2h |
| 9-10 (23.07-05.08) | T3 100% + monitoring | 4h | 2h |
| **Total** | | **~40h CC** | **~16h Adrian** |

**Faza 7 T1 LIVE: szacowany 02.07** (vs original "od razu" 28.05 = ~5 tygodni delay)

ALE w międzyczasie **drive_min + carry visibility deployed = independent quality wins** niezależnie od Faza 7 success.

---

## 6. Co już mamy LIVE / done

| Item | Status | File |
|------|--------|------|
| **Q4 hook** (session handoff dump) | ✅ LIVE in `~/.claude/settings.json` | `~/.claude/hooks/session_handoff_dump.py` |
| Backfill skrypt | ✅ Committable | `dispatch_v2/tools/backfill_decisions_outcomes.py` |
| 6 raportów diagnostic | ✅ /tmp/ | (lista poniżej) |

**Lista raportów:**
- `/tmp/diag_propozycje_2026-05-27.md` (Etap 1, 10KB)
- `/tmp/diag_q1_backfill_2026-05-27.md` (Etap 2, 8KB)
- `/tmp/diag_q1_v2_2026-05-27.md` (Etap 3 consolidation, 14KB)
- `/tmp/q1v2_agent1_operator.md` (12.8KB)
- `/tmp/q1v2_agent2_temporal.md` (8.7KB)
- `/tmp/q1v2_agent3_segments.md` (16.5KB)
- `/tmp/q2_narrower_results.jsonl` (229 entries, Agent A)
- `/tmp/q2_narrower_summary.md` (14KB, Agent A)
- `/tmp/courier_whitelist_proposed.{json,md}` (29KB, Agent B)
- `/tmp/drive_min_calibration_design.md` (580 linii, 22KB, Agent C)
- `/tmp/kebab_krol_diagnostic.md` (325 linii, 18KB, Agent D)
- `/tmp/diag_faza7_rampup_FINAL_2026-05-27.md` (ten plik)

---

## 7. Pytania finalne do Adrian'a

**Pytanie 1 — Plan ramp-up sequence (Sprint 1 → 2 → opt 3 → Faza 7):**
- (a) **Akceptuję cały plan** (5-6 tyg delay, 3 sprinty pre-rampup)
- (b) **Skip Sprint 3** (operator-favorites investigation), ramp-up po Sprint 1+2 (~4 tyg delay)
- (c) **Skip Sprint 2** (carry visibility), tylko drive_min + KK quick fix (~3 tyg delay)
- (d) **Pomijam wszystko, idziemy ramp-up od razu** z 78% override knowledge (NIE rekomenduję, ale to Twoja decyzja)

**Pytanie 2 — Sprint 1 (drive_min): startujemy w tym tygodniu?**
- (a) TAK — CC zaczyna implementację Alt A teraz (8.5h CC)
- (b) Najpierw chcę przeczytać `/tmp/drive_min_calibration_design.md` (580 linii) — daj 1-2 dni na review
- (c) Modify scope — alternatywa B (regression) zamiast A (offset)

**Pytanie 3 — Sprint 2 quick fix KK (30 min) standalone?**
Niezależnie od Sprint 1, możemy wdrożyć w 30 minut conditional KK dinner exclusion. Niska ryzyko, wysokia wartość. Robimy oddzielnie?

**Pytanie 4 — Anything else?**
Czy jest coś z raportów które chcesz że zostało zignorowane / pominięte / zbyt zoptymistycznie ocenione? Specific deep-dive request?

---

**Czekam na Twoje decyzje.** Wszystkie outputy są w `/tmp/`. Q4 hook (handoff dump) jest jedyny LIVE — reszta to design/diagnostic dokumenty gotowe do implementacji po Twoim ACK.
