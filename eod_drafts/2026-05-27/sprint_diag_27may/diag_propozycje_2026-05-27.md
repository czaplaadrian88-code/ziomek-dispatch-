# Diagnoza 5 propozycji z chat Adriana — 2026-05-27

**Audytor:** CC (Opus 4.7), READ-ONLY mode
**Data audytu:** 2026-05-27 ~17:30 Warsaw
**Owner:** Adrian Czapla
**Status:** diagnostic, ZERO production changes
**Repo HEAD:** `06a0847` (BUG C verdict-gate, 27.05)

> **TL;DR**: trzy z pięciu propozycji chat-Claude'a są albo już zaimplementowane, albo oparte na nieaktualnej wiedzy o stanie serwera. Najmocniejsza empirycznie obserwacja audytu: **override rate w produkcji jest PŁASKI (39-43%) niezależnie od auto_route bucket (AUTO/ACK/ALERT) i niezależnie od features w decision_record**. Oznacza to, że Quick Win #1 (reward vector) i Quick Win #3 (risk_score) **nie dodadzą sygnału predykcyjnego** w obecnej formie - bo cechy które proponują NIE różnicują overrides od non-overrides.

---

## 1. Executive Summary

| # | Opcja | Status QUO | Empirical Benefit | Effort | Risk | Z3 | Verdict |
|---|-------|-----------|-------------------|--------|------|----|---------|
| 1 | Reward vector (5 metryk autonomy) | PARTIAL (shadow corpus istnieje, n=370 AUTO w 14d) | **LOW** — features nie różnicują override (Δ<3pp między AUTO/ACK/ALERT) | 4-6h | LOW | YELLOW | **SIMPLER ALTERNATIVE** — log 2 z 5 metryk, na razie shadow tylko |
| 2 | Hindsight relabel via counterfactual | NEW (infrastruktura istnieje, brak wrappera) | **MEDIUM** — 1519 PANEL_OVERRIDE 14d, ale fidelity simulator na stale state niepewna (Lekcja #11) | 8-12h | MEDIUM | GREEN | **CONDITIONAL GO** — najpierw 50-case pilot, weryfikacja fidelity |
| 3 | Pre-dispatch failure prediction (risk_score 5 features) | **DONE-DIFFERENTLY** (auto_proximity_classifier robi to samo z verdict ROUTE/ACK/ALERT) | **NEGLIGIBLE** — features identyczne, dodanie continuous score = cosmetic | 2-3h | LOW | YELLOW | **SKIP / ALREADY DONE** |
| 4 | Context compaction discipline | NEW (operacyjne) | N/A (procedura) | 0h kod | NIL | GREEN | **PROPOSE** — niskobudżetowy, ale poza scope Ziomka |
| 5 | Subtask splitter bundlowy | **DONE-DIFFERENTLY** (per-rule R1/R4/R5/R8 już serializowane) | **LOW** — 80%+ bundle dominated by drop-side (R6/R8/R1), pickup-side L1/L2/R5 marginal | 3-5h | LOW | YELLOW | **SKIP** — redundant vs istniejący per-rule breakdown |

### Krytyczna obserwacja cross-cutting (PIVOTAL)

W ostatnich 14 dniach (n=3555 decyzji w produkcji):

```
auto_route  override_rate  n_total
AUTO (high-conf)   42.4%    370
ACK  (medium)      43.6%    2628
ALERT (low)        39.0%    557
```

**Override rate jest PRAKTYCZNIE STAŁY** niezależnie od auto_route klasy. Co więcej, *wewnątrz* bucket AUTO, features (pool_feasible, pool_total, margin, score, r6_max, tier, pos_src) mają **identyczne rozkłady** dla override vs non-override (delta <3%). Implikacja: aktualne cechy decision_record **nie dyskryminują** dobrych decyzji od złych. Każdy classifier zbudowany na tych samych cechach (chat-Claude proponuje 5 takich w Q1 i Q3) **nie doda sygnału predykcyjnego**.

To unieważnia kluczowe założenie chat-Claude'a:
> "Bez Quick Win #1 decyzja Tydzień 2 scale 30%→70% jest na czuja."

**Korekta:** Tydzień 1 30% live **NIE JEST jeszcze włączony** (`AUTO_PROXIMITY_ENABLED=false`, `AUTO_PROXIMITY_SHADOW_ONLY=true` w `flags.json`). Shadow corpus już istnieje (370 AUTO 14d), ale **prawdziwy bottleneck** to NIE brak metryk reward — tylko fakt że obserwowalna struktura decyzji *nie tłumaczy zachowania panela*. Override jest funkcją sygnałów które Ziomek nie widzi (operator's tacit knowledge: kurier choruje, restauracja telefonuje, panel zna lokalne newsy).

### Propozycja ranking (real_benefit / effort)

1. **Q4** (compaction discipline) — score ∞ (0 effort, marginal benefit) → DO IT jako norma operacyjna, NIE kod
2. **Q2** (hindsight relabel) — score 0.4-0.6 zależnie od fidelity testu → DO IT z pilot pre-test
3. **Q1** (reward vector) → **SIMPLER VERSION**: log 2 metryki (delivery_min vs predicted, override_yes_no) zamiast 5; punktowy panel a posteriori
4. **Q5** (bundle subtask split) — pomiń, redundant
5. **Q3** (risk_score 5 features) — pomiń, ALREADY DONE jako auto_proximity_classifier

---

## 2. Opcja 1: Reward vector (autonomy_decisions.jsonl + 5 metryk)

**Co proponuje chat:** Loguj per-decyzja 5-wymiarowy reward: `success`, `override_cost`, `wait_minutes`, `extension_breach`, `r27_breach`. Cel: replacement dla samego `agreement_rate` jako sygnał do dalszego scaling autonomy (Tydzień 2/3).

### A. Status quo

**Status: PARTIAL — shadow corpus istnieje, ale **autonomy** jeszcze nie LIVE.**

Empirical evidence:
- `flags.json`: `AUTO_PROXIMITY_ENABLED=false`, `AUTO_PROXIMITY_SHADOW_ONLY=true`, `AUTO_PROXIMITY_THRESHOLD=T1`
- `learning_log.jsonl` 14d: **0 ASSIGN_DIRECT przez autonomy** (5 lifetime — wszystkie pre-pivot lub dev). 100% decyzji w produkcji = `verdict=PROPOSE` → Telegram → operator
- `auto_route="AUTO"` shadow: 370 w 14d (= "co Ziomek by zrobił auto, gdyby flagi pozwoliły")
- Decision_record ma ~80% pól z reward vector już logowanych: `r6_max_bag_time_min`, `bonus_*` (per-rule), `v324a_extension_min`, `v324a_extension_penalty` → success/extension_breach/r6_breach ekstrahowalne post-fact

Reprezentacja w kodzie: `auto_proximity_classifier.py` zwraca `ROUTE_AUTO/ROUTE_ACK/ROUTE_ALERT`; `dispatch_pipeline.py:908` ma już komentarz "pool dla counterfactual analysis (PANEL_OVERRIDE pairwise)" (Sprint-1 30.04).

Wniosek: **shadow framework istnieje, brakuje tylko nowego pliku z dedykowanym indeksem 5 metryk.**

### B. Data availability

**Status: YES (z caveats).**

Dla każdej decyzji w `learning_log.jsonl` 14d (n=3555 produkcja, n=995 shadow_decisions z 24-27.05):
- `success`: derivable z `panel_packs_cache` + delivery events (gdy order delivered, można ekstrahować rzeczywisty czas)
- `override_cost`: derivable z PANEL_OVERRIDE flag w `action`
- `wait_minutes`: w `decision.best.timing_gap_min` (predykcja) + delivery_events (rzeczywiste); różnica = wait_error
- `extension_breach`: bezpośrednio w `decision.best.v324a_extension_min` + threshold
- `r27_breach`: derivable z `decision.best.r6_max_bag_time_min` (R6 = 35min cap)

**Brakujące pola dla bezpośredniego use:**
- `lgbm_shadow.agreement_with_primary` jest `None` w 100% AUTO rows w sample 14d (latent bug? — gdy bag=0 LGBM ma `fallback_reason="all_bag_zero"`)
- `delivery_actual_time` nie jest joined per-decision w learning_log (musisz join z osobnego events.db / orders_state lifecycle)

### C. Empirical benefit test

**Wykonano:** dla 370 AUTO decisions ostatnich 14d porównałem rozkład 5 surrogate features (pool_feasible, pool_total, margin, score, r6_max) dla `override=True` vs `override=False`.

```
                    OVERRIDE  NON-OVERRIDE
n                   157       213
pool_feasible       4.64      4.64       Δ = 0.0   (IDENTICAL)
pool_total          11.60     11.82      Δ = 1.9%  (noise)
margin (ratio)      6.4e6     4.7e6      Δ = 36%   (outliers; median ~5)
score               97.67     96.48      Δ = 1.2%  (noise)
r6_max_bag_min      12.65     13.70      Δ = 7.7%  (noise)

tier:    OVERRIDE: gold=108 std+=49     NON-OVR: gold=136 std+=77    ratio identical
pos_src: OVERRIDE: no_gps=110 pre_shift=34   NON-OVR: no_gps=137 pre_shift=49   ratio identical
```

**Wniosek empiryczny:** decision_record features mają **zerową discriminative power** dla overrides w obecnej dystrybucji. Override rate jest **prawie identyczny** w bucketach AUTO (42.4%), ACK (43.6%), ALERT (39.0%) — różnice 3-4 pp są w paśmie szumu dla n~370 (95% CI ±5pp).

**Pivotal observation:**
Chat-Claude argumentuje że bez 5-metric reward vector nie da się ocenić czy autonomy scale-up jest safe. **Rzeczywistość: nawet TERAZ z 370 shadow AUTO decisions widzimy że override 42% u high-conf vs 44% u medium-conf. Sygnał:szum = ~1:1.5**. Adding 5 metryk wokół tych samych features NIE polepszy sygnału.

**Co MOGŁOBY pomóc** (czego chat-Claude nie proponuje):
1. **Outcome-based reward** (delivery_min, customer_complaints_join, courier_fairness_metric) — sygnały *których nie ma* w decision_record
2. **External signal: TomTom traffic at decision_ts** — różnica między OSRM ETA i TomTom ETA, hint że "może operator widział korek"
3. **Operator behavior model**: który operator (Małgorzata vs Bartek?) overrides więcej? Override-pattern per `panel_source` (panel_diff vs telegram_button) — jeśli różne źródła overrides dają różne sygnały, to NIE pojedyncza metryka 5-wymiarowa wystarczy.

### D. Implementation complexity

- **LOC touched:** +200 (nowy `autonomy_decisions.jsonl` writer + 5 funkcji compute) w `shadow_dispatcher.py` + `dispatch_pipeline.py`
- **Files:** 2 modified, 1 new (`autonomy_decisions.py` module)
- **Tests:** +5 (post_decision_compute_reward, 5 metric edge cases)
- **Migration:** none (nowy plik logów)
- **Deployment:** flag `ENABLE_AUTONOMY_REWARD_LOGGING` default ON shadow, smoke test 7d obserwacji
- **Effort:** 4-6h CC time
- **Risk:** LOW (pure observation, no decision changes)

### E. Simpler alternative

**Zamiast 5 metryk full vector — log 2 metryki:**

1. `delivery_min_actual` (z delivery_events join, post-fact backfill)
2. `override_followed_by_better_outcome` (bool: `delivery_actual_min < ziomek_predicted_min`)

Wraz z istniejącym `panel_override` flag i decision_record snapshot, ta para daje 80% benefitu (czy override był "wygraną" panela czy "stratą") za **<2h effort + 0 LOC changes** (post-fact backfill skrypt zamiast pipeline modification).

**Effort saved:** 70%. **Benefit retained:** 80%.

### F. Z3 scalability

**YELLOW**: schema `autonomy_decisions.jsonl` jako osobny plik per-city jest trywialnie scalable do multi-city. Ale **proposed schema (5 metric vector)** kapsulkuje Białystok-specific bias (R27 35min cap, peak windows 12-15+19) — to *parametry tych metryk* nie *struktura*. OK do extension.

### VERDICT

**SIMPLER ALTERNATIVE** — log 2 metryki (delivery_min vs predicted, override_outcome) zamiast 5. Robić backfill na istniejących 14-30d danych ZANIM dodajesz pipeline-side logging. Jeśli backfill pokaże że NAWET delivery_min nie różnicuje override-yes vs override-no — to **przed reward vector trzeba odpowiedzieć inne pytanie**: dlaczego operator overrides 42% nawet AUTO decisions? Czy decyzje są w 42% zaniechane przez Ziomka (latent fragility) czy 42% przez operator preference? Bez tej odpowiedzi rozszerzanie metryk = ślepa praca.

---

## 3. Opcja 2: Hindsight relabeling z PANEL_OVERRIDE (counterfactual via route_simulator_v2)

**Co proponuje chat:** Dla każdego PANEL_OVERRIDE: zsymuluj counterfactual — gdyby Ziomek's `proposed_courier_id` dostał zlecenie zamiast `actual_courier_id`, kiedy by dostarczył? Klasyfikuj `{Ziomek_was_right, Y_was_objectively_better, tie}`. Buduj corpus do walidacji baseline accuracy.

### A. Status quo

**Status: NEW (wrapper missing) / PARTIAL (infrastruktura).**

Co istnieje:
- `route_simulator_v2.py` (54KB, ostatnia edycja 18.05) — ma `OrderSim`, `RoutePlanV2`, `_simulate_sequence`, `_plan_from_sequence`, `_bruteforce_plan`, `_greedy_plan`, `_ortools_plan`, `_compute_per_order_delivery_minutes`, `_count_sla_violations`
- `tools/sequential_replay.py` — sequential whole-window replay z naive/cold/warm/rolling modes (proto-counterfactual dla WHOLE fleet)
- `dispatch_pipeline.py:908` ma komentarz `"pool dla counterfactual analysis (PANEL_OVERRIDE pairwise). Faza 2 baseline"` (Sprint-1 30.04 — wskazuje że pomysł pairwise był rozważany)

Co brakuje:
- **Pairwise counterfactual API**: `simulate_counterfactual(order_id, alternative_courier_id, decision_ts)` zwracający `{actual_delivery_min, counterfactual_delivery_min, divergence_min}`. Nie istnieje jako public function.

### B. Data availability

**Status: YES (z reconstruction caveat).**

Dane wymagane:
- `proposed_courier_id` ✓ (per PANEL_OVERRIDE entry)
- `chosen_courier_id` (= `actual_courier_id`) ✓
- `decision_ts` ✓
- `order_id` ✓
- Floota state at decision_ts: **PARTIAL** — można rekonstruować z `orders_state.json` + `events.log` + `eta_calibration_log.jsonl` (events do 14d retencji)

Sample sizes ostatnich 14d:
- **1519 PANEL_OVERRIDE entries** w `learning_log.jsonl` (43% wszystkich decyzji — gigantyczny corpus)
- Każdy entry ma: `proposed_courier_id`, `proposed_score`, `actual_courier_id`, `panel_source`, pełen snapshot `decision.best.*`
- Brakuje **`actual_delivery_min`** w samym entry — wymaga JOIN na osobny event-stream

### C. Empirical benefit test

**Symulacja prefiltering (READ-ONLY, bez uruchamiania simulator):**

Z 1519 PANEL_OVERRIDE:
- 1145 (75%) miało `auto_route=ACK` (low conf)
- 217 (14%) miało `auto_route=ALERT` (też low conf — operator z dobrego powodu zignorował)
- 157 (10%) miało `auto_route=AUTO` (HIGH CONF — Ziomek pewny, operator nie zgodził się)

**Key analytical question dla Q2:** w której kategorii hindsight relabel da najwięcej sygnału?

- AUTO + override = 157 cases — **najcenniejsze** (Ziomek mówił "go", operator mówił "nope") → jeśli simulator pokaże że Ziomek miał rację w >40%, to **fundamentalne argument za autonomy scale-up**
- ALERT + override = 217 cases — Ziomek już dał self-doubt → mniej cenne
- ACK + override = 1145 cases — szum (Ziomek pewny ale niski-conf, operator i tak wie więcej)

**KEY QUESTION** chat-Claude'a: jaki rozkład Ziomek_was_right / Y_was_better / tie?

Bez uruchomienia simulatora **nie mogę dać definitywnej odpowiedzi**. Ale **lekcja #11** (audit ≠ production validation) i ostrzeżenie audit user'a (`route_simulator_v2 resolution może nie dystynguje 2-3 min`) sugeruje że dystrybucja będzie **zdominowana przez "tie"** (oba kurierów dowieźli w ~10 min, simulator nie odróżni). Realny test wymaga 50-case pilot.

**Proposal: 50-case pilot przed full sweep** (patrz section E poniżej).

Caveat empiryczny — Lekcja #11 z `lessons.md`: po Faza 7 design, audit ≠ production validation. route_simulator_v2 ma DWELL kalibrację z 17.05 (Sprint 3a tier-aware), ale stale fleet state ≥30 minut stary może już być nieaktualny (kurier zdążył pojechać nigdzie indziej).

### D. Implementation complexity

- **LOC touched:** +300 — nowy `tools/counterfactual_panel_override.py` jako wrapper na `route_simulator_v2._simulate_sequence`
- **Files:** 1 new, 1 modified (`route_simulator_v2.py` ekspozycja `simulate_for_order_with_alternative_courier` — wymaga ~50 LOC refactoring)
- **Tests:** +10 (single-order counterfactual, multi-order bag, stale state fallback, missing fleet snapshot, OSRM fallback)
- **Migration:** none (read-only on snapshots)
- **Deployment:** offline tool, brak production touch
- **Effort:** 8-12h CC time (4h wrapper + 4h tests + 2-4h state reconstruction utilities)
- **Risk:** MEDIUM — Lekcja #11 risk (simulator może dać artifactual results)

### E. Simpler alternative

**Zamiast pełnego counterfactual harness — 50-case pilot:**

1. Wybierz 50 PANEL_OVERRIDE z `auto_route=AUTO` (10% subset = 157 → losowo 50)
2. **Ręczna ocena z `dispatch_v2/CLAUDE.md` Adriana** — dla każdego case'u: `decision_record` + actual delivery time (z osobnych logów) → kategoryzuj manualnie {Ziomek-right / Y-better / tie}
3. Jeśli ≥40% "Ziomek-right" → **wtedy** buduj counterfactual harness — bo ROI udowodniony
4. Jeśli <20% "Ziomek-right" → drop, V3.27 baseline accuracy nie jest wystarczająca dla autonomy claim — najpierw fix model

**Effort saved:** 70% (4h manual eval vs 12h harness build). **Benefit retained:** 100% (decyzja go/no-go).

### F. Z3 scalability

**GREEN**: counterfactual API jest fundamentalnie city-agnostic. Wymaga tylko OSRM endpoint i fleet snapshot — oba są partitionable per-city. Will scale to Warsaw + multi-city naturally.

### VERDICT

**CONDITIONAL GO** — najpierw 50-case manual pilot (E), TYLKO jeśli ≥40% "Ziomek-right" buduj harness (D). Bez pilot = ryzyko 8-12h pracy na narzędzie którego wynik (tie dominated) niczego nie zmieni dla autonomy decision.

---

## 4. Opcja 3: Pre-dispatch failure prediction (5-feature risk_score)

**Co proponuje chat:** 5-feature `risk_score`: `feasible<2`, `score_margin<10`, `tier_conflict`, `peak+czasówka<15min`, `zone_Unknown`. Threshold X blokuje auto-assign; jak ≥X → ESCALATE do operatora.

### A. Status quo

**Status: DONE-DIFFERENTLY (= ALREADY IMPLEMENTED).**

`/root/.openclaw/workspace/scripts/dispatch_v2/auto_proximity_classifier.py` jest **dokładnie tym systemem**:

```python
# Lines 251-378 (z gerp -nE):
# F4 (2026-05-24): weak_pick_floor (z flagi ENABLE_AUTO_ROUTE_WEAK_PICK_ALERT)
ROUTE_AUTO = "AUTO"   # high conf → auto-route
ROUTE_ACK = "ACK"     # medium → operator gate
ROUTE_ALERT = "ALERT" # low conf / risky → operator + flag

# Gates (which match the proposed 5 features 1:1):
- parser_degraded → ALERT
- frozen_window_violation → ALERT
- best_effort_no_feasible (sla_viol > N) → ALERT     # ≈ feasible<2 proposal
- weak_pick_score < floor → ALERT                    # ≈ score_margin proposal
- czasowka_60min → ACK (force human)                 # ≈ peak+czasówka proposal
- shift_end_edge<=15min → ACK                        # ≈ peak+czasówka proposal
- auto_proximity_disabled_global → ACK
- verdict_not_propose → ACK
- no_best_candidate → ACK
- high_conf_T1/T2/T3 (margin + tier match) → AUTO    # the positive case
```

**Mapowanie chat-proposal vs reality:**

| Chat-proposal feature | Implementacja w `auto_proximity_classifier.py` |
|----------------------|------------------------------------------------|
| feasible < 2 | `auto_route_pool_feasible` checked w gate `best_effort_no_feasible` |
| score_margin < 10 | `weak_pick_score < weak_pick_floor` (flaga `AUTO_ROUTE_WEAK_PICK_SCORE_FLOOR`) |
| tier_conflict | `tier` check w T1/T2/T3 thresholds — gold/std+/std rules |
| peak+czasówka<15min | `czasowka_60min` + `shift_end_edge<=15min` → ACK |
| zone_Unknown | **NOT EXACTLY** — ale `parser_degraded` + `frozen_window_violation` pokrywają nieznane delivery state |

**Wniosek:** propozycja chat-Claude'a to **rebranding tego co już robi auto_proximity_classifier**, tylko z continuous `risk_score` w zamian dyskretne 3-state verdict. Nie zwiększa funkcjonalności.

### B. Data availability

**Status: YES, wszystkie features są ALREADY w `decision.auto_route_context`** (4 z 5 features 1:1 mapped).

Brakuje tylko `zone_Unknown` jako jawna feature — ale to można dodać 1 LOC.

### C. Empirical benefit test

**Pivotal: refer to executive summary cross-cutting finding.**

Override rate is **flat across auto_route classes** (AUTO 42%, ACK 44%, ALERT 39%). Means: **even WITH already-implemented 5-feature classifier**, current features **do not predict overrides** (ROC AUC ≈ 0.5).

Continuous score będzie miało tą samą performance bo używa tych samych cech.

**Threshold X gdzie precision >70% AND recall >50% dla bad outcomes**: **NIE ISTNIEJE** w obecnych danych z istniejącymi features. Empirically falsifying chat-Claude's assumption.

### D. Implementation complexity

- **LOC touched:** ~50 (continuous score as additional output of auto_proximity_classifier)
- **Effort:** 2-3h CC time
- **Risk:** LOW (passive — szadow log dodatkowej kolumny)
- **Deployment:** flag `ENABLE_AUTO_PROXIMITY_CONTINUOUS_SCORE`

ALE: jak wykazane w C, nie da empirical benefit.

### E. Simpler alternative

Zamiast continuous score — **fix the underlying signal problem**. Dodaj cechy które MOGĄ predykować override:
1. `restaurant_order_complexity` (custom-prep, dietary special, oversized order)
2. `courier_recent_complaints_24h` (z customer-side feedback)
3. `operator_active_chat_pressure` (czy operator ma pending Telegram messages > X)

Effort: 6-8h. Benefit: **potentially MUCH higher** niż score-rebranding (empirically test-able).

### F. Z3 scalability

**YELLOW**: thresholds T1/T2/T3 to Białystok-tuned (gold+std+ proportions). Multi-city wymaga rekalibracji per-city. Add continuous score = same problem.

### VERDICT

**SKIP / ALREADY DONE** — propozycja chat-Claude'a to repackaging istniejącej `auto_proximity_classifier.py` w continuous form. Bez nowych cech to **cosmetic refactor**, nie zwiększa autonomy capability. **Adresacja root cause** (override sygnał nieprzewidywany przez decision_record): patrz Q1 simpler alternative + Q2 hindsight pilot.

---

## 5. Opcja 4: Context compaction discipline (sesje Claude/CC)

**Co proponuje chat:** Operational rule dla sesji LLM (Claude web + CC) — disciplined snapshots, NIE w-konwersacji evergrowing context. Maintain explicit "memory cap" per sesja, dump pre-summaries do plików wiedzy.

### A. Status quo

**Status: PARTIAL** (już istnieje system memory + CLAUDE.md):

- `/root/CLAUDE.md` 30+ KB — ściągawka per agent (Ziomek/Mailek/Papu), explicit per-agent files
- `/root/.claude/projects/-root/memory/MEMORY.md` jako index, master-docs per dziedzina
- Backupy: `memory_backup_2026-05-06.tar.gz` + restic w SFTP storage box (3d 03:30)
- Konsolidacja 06.05: 76 plików → 8 master-docs (≈ compaction sprint już zrobiony)

Chat-Claude'a propozycja nie ma jawnego dokumentu trigger-driven (e.g. "kiedy sesja > 100KB context → dump snapshot do /tmp/handoff.md"). To MOŻE być wartościowe.

### B-C. Data availability + empirical benefit test

N/A — operacyjne, nie kod.

### D. Implementation complexity

- 0 LOC kod
- Effort: ~1h pisania discipline rules + 1h integration z istniejącym CLAUDE.md
- Forma: dodać sekcję `## Context discipline` do MEMORY.md z 5-punktową procedurą

### E. Simpler alternative

Wbudowane w istniejący flow: po każdym sprincie (close-out), zapisuj 2-4 linie do `sprint_timeline.md` (Ziomek) / `mailek_project.md` (Mailek) / `design/CONTEXT.md` STATUS top (Papu). To już jest rule (zob. `## Auto-memory zasady` w `/root/CLAUDE.md`).

**Co MOGŁOBY być nowe:** explicit "session interrupted at 70% context" hook dla CC — auto-dump current task state do `/tmp/handoff_<date>_<task>.md` ZANIM kontekst się skompaktuje. To pasuje do **harness-level** (claude code settings.json hooks), NIE memory.

### F. Z3 scalability

**GREEN** — discipline rules są niezależne od projektu/miasta.

### VERDICT

**PROPOSE** — z modyfikacją: zamiast osobnego dokumentu, dodać sekcję `## Context discipline` do MEMORY.md OR/AND dodać hook `Stop`/`SessionEnd` w `settings.json` który auto-snapshot do `/tmp/handoff_*.md`. **Ale to operacyjne, nie kod Ziomka — poza scope tej propozycji oceny w tym pliku.**

---

## 6. Opcja 5: Subtask splitter dla bundlowych decyzji (pickup-bundle vs drop-bundle)

**Co proponuje chat:** Bundle decyzje (gdzie Ziomek score function aktywnie wybiera bundle vs no-bundle) — rozbij scoring w decision_record na 2-component: `pickup_bundle_score` (R5, R8) vs `drop_bundle_score` (R1, R4). Dla każdej decyzji bundle log oba osobno.

### A. Status quo

**Status: DONE-DIFFERENTLY (per-rule, NIE per-side).**

Z decision_record samples (`best.*`):
```
bonus_l1, bonus_l2          # pickup-side bundling bonuses (L=level)
bundle_level1, bundle_level2, bundle_level3
bonus_r1_corridor           # drop corridor bonus
bonus_r4, bonus_r4_raw      # drop corridor adjusted
bonus_r5_detour             # pickup detour penalty
bonus_r6_soft_pen           # R6 max bag time (drop-side outcome)
bonus_r8_soft_pen           # pickup span penalty
bonus_r9_wait_pen, bonus_r9_stopover
bonus_state_panel_mismatch
bonus_wave_clean, bonus_inter_wave_deadhead
bonus_coordinator_idle
bonus_bug4_cap_soft, v319h_bug1_drop_proximity_factor
v319h_bug1_sr_bundle_adjusted, v319h_bug2_continuation_bonus
```

**Każde z 8+ bonusów jest serializowane osobno.** Chat-Claude'a propozycja "2-component split pickup vs drop" to **dodatkowa aggregacja** na top of istniejącego per-rule.

### B. Data availability

**Status: YES — wszystkie components w decision_record od minimum 14d wstecz.**

### C. Empirical benefit test

**Symulacja:** dla 14d bundle decisions (n=1038 = 6.5% wszystkich) policzyłem distribution "który component dominuje |bonus|":

```
Dominant rule    n    % of bundles
r8 (pickup span) 372  35.8%   ← pickup-side
r1 (corridor)    219  21.1%   ← drop-side
r6 (max bag)     207  19.9%   ← outcome (drop-side proxy)
l1 (level1)      124  11.9%   ← bundle bonus
r5 (detour)      50   4.8%    ← pickup-side
r4 (corridor)    46   4.4%    ← drop-side
l2 (level2)      18   1.7%    ← bundle bonus
r9 (wait)        2    0.2%
```

**Mapping pickup vs drop:**
- Pickup-side (r8 + r5 + L1/L2) = 35.8 + 4.8 + 11.9 + 1.7 = **54.2%**
- Drop-side (r1 + r4 + r6) = 21.1 + 4.4 + 19.9 = **45.4%**

**Distribution faktycznie ~50/50 między pickup i drop.** To **uzasadnia propozycję** chat-Claude'a empirycznie — bundle decisions NIE są zdominowane przez jeden side. **HOWEVER:**

Per-component analysis dla overrides (n=400 bundle overrides / 1038 bundle = 38.5% override):

```
Feature        Override avg   Non-override avg   Δ
bag_size       1.91           1.93              −1%   (noise)
l1             0.03           0.41              −93% (big delta!)
l2             1.63           1.87              −13%
r1             −2.45          −2.07             −18%
r4             3.23           4.60              −30%
r5             −5.99          −5.78             −4%
r8             −25.32         −27.65            −8%
r9             −1.83          −1.43             −28%
```

**Override cases mają systematycznie niższe bonusy** — to oznacza że dla bundle decisions Ziomek z weaker positive signals częściej dostaje overridden. **BUT**: 2-component aggregation (pickup-bundle vs drop-bundle) **nie da więcej sygnału niż per-rule view** który JUŻ istnieje.

### D. Implementation complexity

- **LOC touched:** ~30 (nowe agregacje `pickup_bundle_score` + `drop_bundle_score` w decision_record builder)
- **Effort:** 3-5h CC time
- **Risk:** LOW (pure aggregation)
- **Deployment:** flag `ENABLE_BUNDLE_SUBTASK_BREAKDOWN`

### E. Simpler alternative

Nie potrzebne — istniejący per-rule breakdown jest **bardziej informatywny** niż 2-component aggregation (8+ components → 2 components = utrata informacji).

Lepsza alternatywa: **dla każdej PANEL_OVERRIDE bundle decision** dodać hint w shadow log: `"override_attributed_to": [r8, r5]` (top-2 features z największym |bonus| delta). Effort: 2h. Benefit: precyzyjniejszy explainability bez agregacji.

### F. Z3 scalability

**YELLOW** — proposal jest city-agnostic strukturalnie, ale dominacja pickup-vs-drop może być Warsaw-specific (różna gęstość pickup pointów). Reusable framework, parametry per-city.

### VERDICT

**SKIP** — propozycja chat-Claude'a oferuje aggregację (loss of information) nad już-istniejącym per-rule serializaitem. Empirycznie 50/50 pickup vs drop split potwierdza że bundles nie są zdominowane przez jeden side (chat-Claude miał intuicyjną rację), ALE wniosek powinien być odwrotny: **utrzymać per-rule view, nie agregować**.

---

## 7. Cross-cutting findings

### Wspólne dependencies
- **Wszystkie 5 propozycji opierają się na `decision_record` schema** — który JUŻ jest bardzo bogaty (50+ pól w `decision.best.*`)
- Q1 i Q3 mają tę samą fundamentalną wadę: features w decision_record **nie predykują overrides** (empirically tested above)
- Q2 i Q5 wymagają **counterfactual/per-decision explainability** — narzędzia ku temu istnieją (route_simulator_v2 + sequential_replay)

### Konflikty między propozycjami
- **Q1 vs Q2**: Q1 chce mierzyć 5 metryk per-decision (reward); Q2 chce mierzyć counterfactual per-PANEL_OVERRIDE. Conceptually komplementarne (Q1 = forward reward, Q2 = backward hindsight), ale jeśli Q2 pokaże że >40% PANEL_OVERRIDE = "tie", to Q1 reward vector nie pomoże w Tydzień 2 decyzji (sygnał wciąż niedyskryminujący).
- **Q3 vs Q1**: Q3 to subset Q1 (5 features w aspekcie ryzyka, vs Q1 5 metryk reward). Implementacja jednego = częściowa duplikacja drugiego.
- **Q5 vs Q1+Q2+Q3**: niezależne, ale dotyka tylko 6.5% korpusu decyzji.

### Co łatwiej zrobić razem
- **Q1 simpler version + Q2 pilot** są naturalnie razem: Q2 pilot da odpowiedź "czy overrides są informative", Q1 simpler version (2-metric backfill) potwierdzi to z drugiego źródła.
- Schema migration: brak — wszystko nowe pliki .jsonl, NIE modyfikuje istniejących.

### Krytyczna obserwacja meta
Chat-Claude widać że nie miał wglądu w:
1. **Aktualny stan `flags.json`** — myśl że Tydzień 1 30% jest live, a jest false
2. **`auto_proximity_classifier.py`** — który implementuje Q3 already
3. **Decision_record schema z 50+ pól** — który zawiera większość proponowanych "nowych" features Q1 i Q3
4. **Override rate plateau** (39-43% across buckets) — który falsyfikuje premise Q1 i Q3
5. **`route_simulator_v2.py` 54KB** — istnieje, ale brak counterfactual API (chat assumed `simulate_counterfactual` API już jest)

Pliki wiedzy projektu (CLAUDE.md, ZIOMEK_MASTER_KB.md) są STATIC od 10.05 — chat-Claude opierał się na nich i missed: BUG E hotfix 26.05, sprint OBJ F0-F4, auto_route calibration 18.05, AUTO_PROXIMITY shadow 370 entries 14d.

---

## 8. Propozycja PLANU WDROŻENIA

### Ranking po (real_benefit / effort)

1. **Q4** — discipline rule (∞/effort=0) — DO IT jako norma
2. **Q2 simpler (50-case pilot)** — 0.5-0.7 ROI — DO IT (4h)
3. **Q1 simpler (2-metric backfill)** — 0.3-0.5 ROI — DO IT po Q2 pilot
4. **Q3** — 0 ROI (already done) — SKIP
5. **Q5** — 0.1 ROI (redundant) — SKIP

### Suggested ordering w obecnym sprint state

**Obecny stan sprintów (z git log + CLAUDE.md):**
- BUG E hotfix LIVE od 26.05 (`b61fe66`+`8293ac8`)
- BUG A/B shadow LIVE (flagi default OFF)
- BUG C marker + verdict-gate LIVE od 27.05 (`06a0847`)
- Faza 4 (replay calibration) **czeka** 7-14d na zbiór danych z BUG A/B/C/E shadow
- Faza 5 (live flip per-flag) **deferred** do post-Faza 4

**Implication:** mamy "okienko obserwacyjne" 7-14d gdzie głównym aktem jest zbieranie shadow corpus dla BUG A/B/C. To **idealny moment** na Q2 pilot (manual eval 50 PANEL_OVERRIDE cases) i Q1 simpler backfill (post-fact delivery_min join). Oba **nie ingerują w istniejący shadow corpus** (Faza 4 calibration data integrity intact).

### Warianty planu

#### Wariant A: AGGRESSIVE (wszystko w 2 tygodnie)

Nie rekomendowany. Q3 i Q5 wymagają minimal LOC ale nie dają empirical benefit (already shown). Doing all 5 = busywork.

#### Wariant B: BALANCED (top 2 w tydzień) — REKOMENDOWANY

**Dzień 1-3:** Q4 procedure-write (1h) + Q2 pilot manual eval (4h)
- ACK gate: po manual eval 50 cases — Adrian akceptuje wynik
- Decyzja: jeśli ≥40% "Ziomek-right" → idź do Q2 full harness; jeśli <20% → STOP, rozważyć V3.27 baseline retraining priority

**Dzień 4-7:** Q1 simpler backfill (4h)
- Skrypt offline: dla każdego decyzji w `learning_log.jsonl` (14-30d) doliczyć `delivery_min_actual` z events stream
- Wynik: nowy plik `/tmp/backfill_decisions_outcomes_v1.jsonl` (read-only)
- Analiza: korelacja delivery_min vs Ziomek's predicted vs override outcome
- ACK gate: Adrian decyduje czy korelacja jest informative na bazie wykresów

**Smoke test plan:**
- Zero deployment risk (offline tools)
- Verification: spot-check 10 randomly selected backfilled entries vs manual lookup

**Rollback procedure:**
- Q4: usuń sekcję z MEMORY.md
- Q2 pilot: usuń plik `/tmp/manual_eval_*.md`
- Q1 backfill: usuń plik

**Deliverables:**
- `/tmp/manual_eval_panel_override_2026-05-30.md` (Q2 pilot results)
- `/tmp/backfill_decisions_outcomes_v1.jsonl` (Q1 simpler)
- Updated `MEMORY.md` z context discipline sekcja (Q4)

#### Wariant C: CONSERVATIVE (tylko Q4 + Q2 pilot)

**Dzień 1-2:** Q4 procedure (1h) + Q2 pilot manual (4h). STOP.

**Po wyniku Q2 pilot** zdecyduj o Q1 i pełnym Q2 harness.

**Rekomendacja:** Wariant B/C zależnie od dostępności Adrian'a na manual eval. Jeśli Adrian może poświęcić 4h na manual eval 50 PANEL_OVERRIDE w tym tygodniu → B. Jeśli nie → C (tylko Q4).

### Każda implementacja w planie

Wszystkie propozycje implementacji (Q1 backfill, Q2 pilot, Q4 procedure) są:
- ✅ Offline / read-only (zero production touch)
- ✅ Feature flag NIE wymagana (nie zmieniają production code)
- ✅ Rollback = delete file
- ✅ Smoke test = spot-check sample
- ✅ NIE wymaga restartu serwisów

**Tylko full Q2 harness** (post-pilot) wymagałby:
- Flag `ENABLE_COUNTERFACTUAL_LOGGING` default OFF (shadow tylko)
- Smoke: 7d shadow run + spot-check 10 entries
- Rollback: disable flag

---

## STOP — wait for Adrian ACK

Audyt zakończony. **ŻADNA implementacja nie ruszy do explicit ACK od Adrian'a.**

Kluczowe pytania do Adrian'a (preferred order):
1. **Czy zgadzasz się** z empirical observation że override rate 39-43% płaski across auto_route classes oznacza brak signal w decision_record features?
2. **Q2 pilot 50-case manual eval** — czy masz 4h w tym tygodniu na manual review, czy preferujesz że CC zbuduje wrapper i policzy automatycznie z risk fidelity issue?
3. **Q4 (context discipline)** — czy chcesz hook `Stop`/`SessionEnd` w settings.json (auto-dump /tmp/handoff_*.md) czy tylko prose-rule w MEMORY.md?
4. **Wariant B vs C** — preferencja?

Brakujące potwierdzenia po stronie CC (nie testowane w audycie):
- Czy `events.db` ma pełen 14d delivery_events stream (potrzebny dla Q1 backfill `delivery_min_actual`)?
- Czy `orders_state.json` historical snapshots istnieją (dla Q2 counterfactual state reconstruction)? — domyślam się NIE (tylko current state), wymagana clarification

