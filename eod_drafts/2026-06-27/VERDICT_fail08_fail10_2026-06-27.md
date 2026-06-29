# FAIL-08 / FAIL-10 — werdykt ETAP 0 (measure-first, 2026-06-27)

Adrian: „lecisz" (FAIL-08/10 z listy Ziomka). Oba findingi audytu 06-03 = P2, conf=medium,
when=5x/10x (głównie obawy SKALOWE). Measure-first PRZED kodem (protokół + MEMORY).

## FAIL-10 (reconcile throttling 25/10/5 nie skaluje 10×) — WERDYKT: **DEFER (brak bólu teraz)**
Pomiar na żywym ruchu (już kilkukrotnie > audytowego ~254/d; ostatnie dni ~1000-1700 decyzji,
4-7k wierszy kandydatów):
- **picked_up reconcile lag ~0,3 min median** (po korekcie offsetu TZ UTC↔Warsaw; n=11932), max ~32 min, **0% > 90 min** (próg BAG_STALE).
- osobny `dispatch-state-reconcile`: **backlog=0 w 4712/4730 cykli** (max 12, 2×), `dynamic_applied=False` — **adaptacyjny cap JUŻ ISTNIEJE** tam (reko FAIL-10 częściowo zrobione po audycie).
- panel_watcher 25/10/5: ~247 picked_up-reconcile zdarzeń/dobę rozłożonych na ticki = grubo pod budżetem 10/tick; zero dowodów wysycenia.
**Decyzja:** budowanie adaptacyjnych budżetów panel_watcher = przedwczesne (10× od TU, nie od audytu). Tani prep (opcjonalny, niska pilność): metryka `reconcile_backlog_depth` + alert w panel_watcher (state-reconciler już ją ma). Re-open gdy realny wolumen ×3-5 od dziś LUB lag p95 zacznie rosnąć.

## FAIL-08 (wolna restauracja kaskadowo opóźnia worek) — WERDYKT: **HARM NIEUDOWODNIONY → nie ruszać rdzenia; ew. shadow-harm pomiar**
- **Precondycja istotna:** multi-restauracyjne bundle = **19,9%** decyzji (41/206 próbka shadow) — 1/5 propozycji.
- **ALE wpływ (kaskada → breach co-bagowanego B) nieudowodniony.** Precondycja ≠ szkoda (wymaga realnego zacięcia restauracji A + przekroczenia thermal B). Backfill (`backfill_decisions_outcomes_v1.jsonl`) NIE ma czystego pola outcome pickup→delivery → breach co-bagu niemierzalny z tego źródła; realny pomiar wymaga stall-detekcji (GPS arrival vs pickup z `fleet_position_history`) + atrybucji breach per-order w worku = osobny build.
- **Reko short-term (prep_variance → decyzja bundlowania) NIE wpięta świadomie:** `ENABLE_PREP_VARIANCE_ANOMALY_SHADOW=False` (shadow-only, „NIE kara/reject/verdict"); landmine F1.8g: prep_variance NIE wolno doliczać do `pickup_ready_at`. Wpięcie = zmiana rdzenia bundling/scoring = wysokie ryzyko regresji.
**Decyzja:** NIE wdrażać fixu rdzenia bez dowodu szkody. Jeśli temat ma priorytet → najpierw **shadow-harm pomiar** (stall-detekcja + co-bag breach attribution) jako osobny read-only build; dopiero przy materialności ≥20% rozważać prep_variance-aware bundling (przez pełny protokół, z landmine F1.8g na uwadze).

## Meta
3. raz z rzędu audyt P2 06-03 (SCORE-03/04/06, teraz FAIL-08/10) po pomiarze = „realne-ale-immaterialne-teraz" lub „future-scale". Backlog P2 audytu jest w większości NIE-wart akcji przy obecnej skali. Rekomendacja: nie grindować P2 audytu na ślepo — wybierać po zmierzonym ROI / realnych skargach Adriana.
