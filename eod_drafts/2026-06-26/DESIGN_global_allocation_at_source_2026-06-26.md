# DESIGN — globalna alokacja U ŹRÓDŁA (B) — 2026-06-26

**Decyzja Adriana 26.06:** sama generacja propozycji ma być globalnie poprawna od 1. strzału.
3 wiszące → najpewniejsze najpierw, kolejne „zakładając że tamci mają tamte", LUB bundle 2-3
jednemu gdy dobra trasa i jest najlepszą opcją. Reguła propozycji (z kodu, NIE re-derywować):
feasible-first → najszybszy wolny feasible (best_effort fastest-pickup) → KOORD tylko early-bird
≥60min → 0 floty niemożliwe (panel zamawiania wtedy off).

## ETAP 0 — stan (zamknięty)
- Baseline testów: **11 failed / 3357 passed** (pre-existing: 8× courier_reliability, objm_lexr6
  flag_default_off [flaga ON=canary], flag_doc_coverage, working_override). = próg regresji.
- Flagi: ENABLE_PENDING_RESWEEP=ON (shadow), PENDING_RESWEEP_LIVE=OFF, ENABLE_OBJM_LEXR6_SELECT=ON.
- Żywa generacja: `shadow_dispatcher._tick` konsumuje event `NEW_ORDER` POJEDYNCZO →
  `assess_order(order_event, fleet, now)` → 1 propozycja per order, liczona RAZ przy narodzinach.
- Silnik globalny JUŻ istnieje: `tools/pending_global_resweep.global_allocate` (sekwencyjny greedy
  z wirtualną alokacją `_tentative_assign`, prawdziwy `assess_order`, 8/8 testów, 2 dni shadow).

## ETAP 0 — zmierzony gap (korpus 25-26.06, 998 ticków, 238 would_repropose)
- 114 unikalnych zleceń/2dni (~57/dz) by re-proponowano; 60% realne rozbicie pile-on;
  med poprawy ~2min. Najczęściej przeładowywani: Gabriel O (71×), Mateusz O (36×) = gold.
- **20/238 łamią regułę #2**: r6>35 MIMO pool_feasible>0 — wszystkie `proponowany_wypadl`/
  `rozjazd_kierunkow` + `auto_route=ALERT` (human-gated). 49/69 z r6>35 to poprawny best_effort
  (pool_feasible=0). Mechanizm podejrzany: `global_allocate` sortuje po SCORE (`_best_tuple`=
  `res.best.score`), a wirtualne doklejanie wcześniejszego zlecenia wypycha r6 wybranego >35,
  zamiast trzymać feasible-first jak live PROPOSE.

## ✅ NIEPEWNOŚĆ ROZSTRZYGNIĘTA (26.06, czytaniem kodu) — NIE MA selekcyjnego buga
`new_r6_min` w logu = `r6_max_bag_time_min` = MAX worka (z carried thermal-exempt, `ENABLE_PACZKA_R6_THERMAL_EXEMPT=True`),
NIE R6 nowego zlecenia. `dispatch_pipeline:5784 _winner=feasible[0]`, `feasible=[c if verdict=="MAYBE"]` →
silnik z konstrukcji feasible-first; global_allocate dziedziczy przez assess_order. „20/238" = legalne feasible
picks z wysokim bag-max, NIE naruszenia. **Faza A/B (fix selekcji) ZBĘDNA — selekcja już robi to czego Adrian chce.**
Pozostaje TYLKO Faza C (poniżej).

## ~~OTWARTA NIEPEWNOŚĆ~~ (nieaktualne — rozstrzygnięte wyżej)
Czy te 20 to:
- (A) realne złamanie feasible-first w `global_allocate` (sortuje po score, bierze infeasible nad
  feasible) → fix: wymusić feasible-first w selekcji silnika, lub
- (B) artefakt zapisu / R6 miękkie w tej konfiguracji (verdict MAYBE przy r6 40+) → wtedy „feasible"
  w logu ≠ „w regułach", inny fix.
Rozróżnienie wymaga ODTWORZENIA `assess_order` na stanie floty z tamtych timestampów — stan nie jest
replayowalny z obecnego logu (pending_global_resweep.jsonl nie zapisuje fleet snapshot).
**Potrzebne: dodać do collectora snapshot floty per tick (albo flag feasibility chosen) → 1-2 dni
zbierania → dopiero wtedy wiadomo CO naprawiać. Inaczej zgadywanie = anti-Z2.**

## PLAN (po ACK Adriana)
### Faza A (bezpieczna, autonomiczna): wzbogać collector o dowód root-cause
- `global_allocate` zapisuje per-alokacja: `chosen_feasibility_verdict`, `n_feasible_strict`
  (feasible wg R6/R-35 twardo), `best_feasible_cid/r6` (najlepszy feasible jeśli istniał).
- Flag-gated, shadow-only, zero live. 1-2 dni → werdykt A vs B → znamy fix u źródła.

### Faza B (po werdykcie root-cause): fix selekcji feasible-first w global_allocate
- Jeśli (A): `_best_tuple`/wybór per order = najlepszy FEASIBLE (mirror live PROPOSE), best_effort
  fastest-pickup TYLKO gdy 0 feasible. Testy: 20 case'ów → feasible. Bliźniaki: best_effort↔
  objm_lexr6, feasibility↔greedy↔plan_recheck — selekcja musi być spójna z live (nie 2. kopia reguł).

### SURFACE POTWIERDZONY (26.06): konsola czyta `shadow_decisions.jsonl`
`panel/.../integrations/ziomek/feed.py` — „żywa tablica dyspozytora, czyta REALNE decyzje silnika
z shadow_decisions.jsonl (verdict, best, alternatives, plan), panel = lustro tego co Ziomek policzył
(to samo co trafiało na Telegram)". Telegram OFF (inactive+disabled) — `shadow_decisions.jsonl` jest
JEDYNYM kanałem propozycji do konsoli. Pisze go `shadow_dispatcher._serialize_result` per NEW_ORDER.
`fleet_state.read_orders` listuje nieprzypisane BEZ proponowanego kuriera — proponowany kurier jest
w feed.py z shadow_decisions. (Telegram-aktuacja edit-msg = NIEAKTUALNA, Adrian 26.06.)

### Faza C (P0 live — osobny ACK + 2-dniowy replay): global re-emit na NEW_ORDER
- `shadow_dispatcher._tick`: gdy wpada NEW_ORDER → `global_allocate` nad CAŁYM zbiorem wiszących
  (orders_state planned ∧ bez courier_id) + nowe → dla KAŻDEGO wiszącego, którego alokacja się
  zmieniła, **re-emit wpisu w shadow_decisions.jsonl** (best = globalnie wyliczony kurier) → konsola
  (feed.py) pokazuje spójny podział. Brak Telegrama = brak race/edit-msg.
- Default OFF (flaga `ENABLE_GLOBAL_ALLOCATION`) = bajt-identyczne (per-order jak dziś).
- Reguły selekcji DZIEDZICZONE z assess_order (feasible-first → best_effort fastest-pickup →
  KOORD early-bird) — global_allocate już je woła; ZERO 2. kopii reguł.
- MAPA KOMPLETNOŚCI (wszystkie warstwy): feasibility_v2, dispatch_pipeline (feasible/best_effort/
  early_bird/objm_lexr6/a2/demote), shadow_dispatcher, telegram_approver, serializer A+B, plan_recheck,
  reassignment (nie kolidować z forward-shadow v2).
- ETAP 5: replay 2-dniowy dowód POZYTYWNEGO wpływu (mniej pile-on, 0 regresji R6/R-35, parytet).
- ETAP 6: backup→py_compile→test→ACK→1 restart (NIE w peaku bez OK; telegram NIGDY bez ACK).

## DECYZJE DLA ADRIANA (ACK przed kodem)
1. Czy lecę **Fazą A** (wzbogacenie collectora o dowód root-cause, shadow-only, zero ryzyka) teraz?
2. Akceptujesz sekwencję A → werdykt → B → (osobny ACK) C-live? Czy chcesz od razu projektować
   C-live aktuację (edit-msg vs suppress-duplikat)?
