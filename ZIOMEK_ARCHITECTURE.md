# ZIOMEK — ARCHITEKTURA (kanon „czym Ziomek JEST i jak MA być budowany")

> **STATUS: ZATWIERDZONY przez Adriana 01.07.2026** (dowód: CLAUDE.md „Kanon architektury… zatwierdzony 01.07" + MEMORY; nagłówek DRAFT→ZATWIERDZONY zaktualizowany 03.07.2026 w audycie-porządkach, niezgodność N3). Dokument KONSTRUKTYWNY obok Przykazania #0.
> #0 mówi „jak bezpiecznie ZMIENIAĆ" (defensywnie). TEN dokument mówi „czym Ziomek JEST i do czego dąży" (mapa).
> Źródła: audyt spójności Fazy 1 (`eod_drafts/2026-06-30/FAZA1_00..06`) — READ dla dowodów; tu = promocja do kanonu.
> Para: [[ZIOMEK_INVARIANTS.md]] (co MUSI być zawsze prawdą + strażnicy) · [[ZIOMEK_DEFINITION_OF_DONE.md]] (1 ekran).
> **SEMANTYKA PRODUKTU z 12.07:** ODR-001 ma pierwszeństwo nad starszym opisem R6/R27/KPI/autonomii. R6 = possession→customer handoff; exit≠possession, arrival≠handoff; execution authority = macierz per klasa. **ODR-002:** tylko właściciel może zwiększyć authority; dokument, kod, flaga, eval ani agent nie są kartą wykonawczą. Runtime ma sprawdzać aktualną podpisaną kartę przed każdym wykonaniem i failować zamknięcie; brak tej ścieżki jest jawnym implementation gap, nie wyjątkiem od decyzji.

---

## 0. Diagnoza w jednym zdaniu (dlaczego ten dokument istnieje)
Ziomek **nie pali się błędami — jest KRUCHY**. Audyt (100 agentów, oracle) potwierdził: 18 z 19 rootów to dług STRUKTURALNY latentny, nie aktywne bugi. **Klasa łatana ≥4× wraca, bo naprawy trafiały w JEDEN bliźniak albo w KRAWĘDŹ (render/instrument), nigdy w źródło reguły żyjące w 8+ kopiach.** Wspólny korzeń większości = **K1 „brak jednego źródła prawdy".** Ten dokument definiuje stan docelowy, w którym to się nie powtarza.

---

## 1. PIPELINE — 10 warstw (szkielet decyzji)
Zlecenie przechodzi przez 10 warstw. HARD (nieprzekraczalne) przed SOFT (kary), potem selekcja, werdykt, zapis, powierzchnie.

```
1. wejście (panel_watcher)                    → normalizacja zlecenia
2. geokod (HARD)                              → coords ∈ bbox lub odrzut          [walidator common.py:513]
3. early-bird / czasówka (HARD)               → ≥60min naprzód → hold KOORD
4. telemetria                                 → wzbogacenie floty (dispatchable_fleet)
5. check_feasibility_v2 (HARD)                → baseline hybrid/proxy R6 (DRIFT); target ODR-001 possession→handoff 35/40 Alarm
6. scoring + ~19 kar (SOFT)                   → term + bonus_penalty_sum
7. selekcja (SOFT)                            → lex_qual / best_effort / bucket
8. werdykt KOORD (HARD)                        → quality-gate vs operational-gate
9. zapis + kanon (plan_manager, recanon)      → courier_plans.sequence
10. powierzchnie: konsola / apka / Telegram    → render kolejności+ETA
```
**Poza tickiem:** `plan_recheck` (timer 5min, re-sekwencja), `panel_watcher`/recanon (4 handlery assign/deliver/pickup/cancel), most paczki, cross-repo `nadajesz_clone/panel` + `courier_api` + `courier-app`.
⚠ **Reguła żyje CZĘSTO w kilku z tych warstw naraz** (feasibility↔greedy↔plan_recheck; silnik↔konsola↔apka). To jest źródło K1 — patrz §4 rejestr bliźniaków.

**Decision-time ETA (SOURCE-ONLY 21.07):** `decision_eta_log.py` jest wyłącznie
powłoką efektu po finalnym `PipelineResult` albo udanym CAS `save_plan`. Przy
fladze default OFF nie buduje rekordu ani nie wykonuje I/O; przy ON zapisuje
append-only snapshot wybranego CID i ocenionej puli do
`dispatch_state/decision_eta_log.jsonl`. Nie jest wejściem feasibility, scoringu,
selekcji ani renderu. Coverage względem kanonicznego ledgeru mierzy
`tools/decision_eta_coverage.py`; flip wymaga osobnego ACK ownera.

---

## 2. 6 FILARÓW stanu docelowego (jak MA być zbudowany — wzorzec dojrzałych dispatcherów)
Każdy filar leczy konkretny korzeń (K) i realizuje fundament (F) z roadmapy.

| # | Filar | Leczy | Zasada |
|---|---|---|---|
| **F-1** | **Jeden niezmienny WorldState/tick** | K1 | pozycje/czasy/`shift_start`/pula policzone RAZ (`available_from=max(now,shift_start)`); wszystkie warstwy KONSUMUJĄ, nie re-derywują. |
| **F-2** | **Czysty rdzeń + powłoka efektów** | K2 | `decide(world)->decisions` bez I/O, replayowalny; efekty (Telegram/push/kanon) osobno. `plan_recheck` przez TEN sam rdzeń → nie „cofa". |
| **F-3** | **Typy domenowe zamiast sentineli** | K5 | Pozycja = `Known(lat,lng,src,ts)` \| `Unknown(reason)` — NIGDY (0,0). Czas = `Agreed`(R27) \| `Estimated`. 1 walidator u INGEST (istnieje: `common.py:513`). |
| **F-4** | **Inwarianty jako kod, co tick** | nawroty | po `decide` → checker; złamanie = tick odrzucony/alarm, NIE wysłany. Jedyne co blokuje „naprawiane ≥4×" na zawsze. → [[ZIOMEK_INVARIANTS.md]] |
| **F-5** | **Jeden rejestr flag + self-test** | K6 | koniec env-default vs flags.json vs drop-in; sonda EFEKTYWNEGO stanu na starcie (`flag_fingerprint`). |
| **F-6** | **Złoty korpus replay + inwarianty = bramka CI** | regresje | `case_corpus` + `shadow_decisions.jsonl` jako gate; każda zmiana zielona ORAZ dowód pozytywnego wpływu. |

---

## 3. 8 KONTRAKTÓW = definicja „architektonicznego ideału" (z audytu FAZA1_04)
Cel = każdy kontrakt spełniony (metryka 0/1) i pilnowany runtime-inwariantem. Kolumna „dziś" = dashboard entropii.

| # | KONTRAKT | Metryka | Dziś→Cel |
|---|---|---|---|
| ① | **JEDNO źródło na regułę** (import, nie kopia) | kopii/regułę = 1 | 17 reguł >1-źródło → 0 |
| ② | **Kontrakt warstw egzekwowany** (HARD przed SOFT, SOFT nie osłabia HARD) | layer-violation = 0 | 7 → 0 |
| ③ | **Parytet bliźniaków z konstrukcji** (wspólny moduł lub golden-test) | twin-divergence = 0 | ~13 → 0 |
| ④ | **Prawda flag** (1 rejestr, sonda, zero martwych/maskujących) | dead-flag = 0 | 5 (+112 poza rejestrem) → 0 |
| ⑤ | **Prawda przyrządów** (każdy shadow/monitor oracle-skalibrowany przed zaufaniem; „flip tylko na validated") | void/untested = 0 przed flipem | 25/49 → 0 |
| ⑥ | **Brak dryfu semantyki** (display ⊥ decision-value; pola sprzężone pisane razem) | 0 pól-decyzyjnych-udających-display | eta 1-pole-2-role → 0 |
| ⑦ | **Kompletność cyklu życia** (każdy stan create/mutate/GC; zero read-with-side-effect) | 0 stanów bez GC | zombie 43 + load_plan-mutate → 0 |
| ⑧ | **Koherencja** (graf precedencji zdefiniowany; zero cichych inwersji; 1 chokepoint clampów) | unresolved-conflict = 0 | 13 klastrów (64 par) → 0 |

**Zasada anty-entropii (rozszerzenie #0):** żaden przyszły sprint NIE pogarsza żadnej z 8 metryk. Bramka „ZERO NOWYCH KOPII" na KAŻDEJ zmianie — konsoliduj, nie dodawaj. Pełne RED-checki → [[ZIOMEK_DEFINITION_OF_DONE.md]].

---

## 4. REJESTR BLIŹNIAKÓW (kuratorowany — to MUSI ruszać się RAZEM; ma MALEĆ)
K1 w praktyce: te same reguły żyją w N miejscach. Zmieniając którekolwiek — tknij WSZYSTKIE z wiersza. Cel każdej pozycji = 1 źródło.

| Reguła | Kopie DZIŚ | Cel | Bramka |
|---|---|---|---|
| **kolejność trasy (route-order)** | ✅ **ZUNIFIKOWANE KONSTRUKCJĄ — cel osiągnięty** *(Sprint C 08.07, deploy panelu ~19:51 za ACK; korekta wiersza 18.07 — poprzedni opis „konsola NIE importuje" był STALE)*: kanon `route_order.py` (PURE stdlib); `route_podjazdy` = re-eksport; apka deleguje (`ENABLE_APP_ROUTE_FROM_CONSOLE=1`+`ENABLE_ROUTE_ORDER_UNIFIED=1`); **konsola `fleet_state._build_route` DELEGUJE** (panel commit `9168cce`); plan_recheck deleguje `repair_dropoffs_after_pickups` (pełna ekstrakcja rdzenia P2 + producent P3 = osobny pod-ACK, F_poc_plan D.4 kroki 6-7). Strażnicy: INV-SRC/TWIN-ROUTE-ORDER (golden 3-nogi, mutation-probed RED) + `test_route_order_live_parity` w KAŻDEJ regresji (venv panelu, żywe worki + pin flag). Kotlin = render off-host (golden-export otwarty) | 1 moduł + golden-test parytetu ✅ | zamknięte (monitor wygasł 10.07 ZGODNIE Z PLANEM — zastąpiony siatką bez daty) |
| **selekcja `lex_qual`** | ✅ **ZUNIFIKOWANE — cel osiągnięty** *(potw. 06.07: kanon `objm_lexr6.lex_qual`, `dispatch_pipeline` deleguje ×6 bez własnego def; strażnik `test_objm_lexr6_unify_2026_06_25`)* | 1 `objm_lexr6.lex_qual` + test parytetu ✅ | zamknięte |
| **generatory planów: parametryzacja tier→(dwell,tempo) + wejście symulacji (silnik↔plan_recheck)** | ✅ **ZUNIFIKOWANE 06.07 (K15 refaktoru, ADR-R03)**: kanon `core/planner.py` (`tier_params` z jawnym przełącznikiem semantyk + `plan_bag(simulate_fn=…)`); feasibility deleguje ZAWSZE (1:1), plan_recheck za `ENABLE_PLANNER_UNIFIED` (OFF→flip za ACK; SHADOW-flag porównuje parametry na żywo). Poza unifikacją ŚWIADOMIE: sweep-logika/committed-tiebreak (osobna domena re-planera), bramka permutacji = L3 (potw. C15), bliźniaczy czytnik pozycji `_start_anchor` (odnotowany K16→WorldState) | 1 `core.planner` + testy semantyk ✅ (`test_planner_k15`) | parytet: bramka n=88 DIFF PUSTY |
| **równe traktowanie pozycji (no_gps/pre_shift)** | **8 bliźniaków** (F1.7 score-neutral, `_selection_bucket`, `_demote_blind_empty`, `_best_effort_fastest_pickup_key`, drive_min_calibration, `auto_assign_gate` G7, `reassignment_forward_shadow`, `feed.py`) | 1 `_selection_bucket` + reszta importuje | łatane ≥4×, wraca |
| **floor odbioru (`available_from`)** | **17 powierzchni / 4 z floorem** | 1 `available_from=max(now,shift_start)` w courier_resolver | F1/L4 |
| **R6 possession→handoff (35/40 Alarm)** | ~6 progów + wiele niespójnych anchorów | 1 domenowy interwał + dwa tryby, nigdy klasa kuriera | ODR-001; threshold/anchor-sprawl |
| **próg czasówka=60** | ~6 miejsc | 1 stała | threshold-sprawl |
| **SLA-anchor** | 3 (`route_simulator._count_sla_violations` + `feasibility_v2` SLA-loop + `plan_recheck._o2_key`) | co-design, 1 kotwica | O2 02.07 |

Pełne mapy: `FAZA1_01_mapa_antywzorcow.md` (rooty), `backing/A6_twin_import_graph.md`, `backing/F_poc_plan.md` (route-order świeży grep).

---

## 5. ŹRÓDŁA PRAWDY (gdzie żyje CO — dziś, po naprawie K1 = konsolidacja)
- **Intencja/reguły biznesowe:** najnowszy Owner Decision Record + `memory/ZIOMEK_REGULY_KANON.md`. `common.py`, `feasibility_v2.py`, scoring i pipeline są dowodem implementacji na konkretnym SHA, nie źródłem intencji.
- **Stan flag EFEKTYWNY:** NIE flags.json ani env-default — `flag_registry.py` (3 warstwy: common.py default ↔ systemd drop-iny ↔ flags.json hot-reload). Serwisy różne = env różny (`dispatch-shadow` ≠ `dispatch-plan-recheck` ≠ `dispatch-panel-watcher`).
- **Stan zleceń/planów:** `dispatch_state/orders_state.json`, `courier_plans.json` (atomic + fcntl).
- **Snapshot predykcji as-of decyzja:** `dispatch_state/decision_eta_log.jsonl`
  przez jeden writer `decision_eta_log.py`; źródła finalne: shadow selection,
  czasówka, reassignment, global resweep i commit planu. Brak rekordu = brak
  dowodu, nigdy zgoda na użycie późniejszej predykcji.
- **Prawda przyrządów:** `FAZA1_03_rejestr_przyrzadow.md` (validated/void/untested) — **czemu ufać przy flipie**.
- **Prawda fizyczna (ground-truth):** od ODR-001 dokładny event musi mieć wersjonowane provenance i fail-closed gate per event/source/cohort. `last_inside`/`picked_up_at`/`delivered_at`/arrival/click są nazwanymi observable/proxy; żaden nie staje się automatycznie physical possession lub customer handoff.
- **Execution authority:** ODR-002 jest źródłem intencji governance. Wzrost authority wymaga jawnej decyzji właściciela, evidence bundle z hashem, niezależnej weryfikacji, owner-only approval/podpisu i deterministycznego zastosowania. Runtime ma walidować bieżącą podpisaną kartę przed każdym wykonaniem; brak lub błąd karty/wersji/podpisu/danych → `recommend-only` albo `HOLD`. Do czasu wdrożenia tej ścieżki obowiązuje bardziej restrykcyjny istniejący stan per klasa; żaden dokument opisowy nie daje execute.

---

## 6. Jak używać tego dokumentu (dla nowej sesji)
1. Zmieniasz Ziomka → NAJPIERW Przykazanie #0 (`memory/ziomek-change-protocol.md`) — JAK.
2. Nie wiesz „gdzie ta reguła żyje" → §4 rejestr bliźniaków + §5 źródła prawdy.
3. Chcesz wiedzieć „co nie może się złamać" → [[ZIOMEK_INVARIANTS.md]].
4. Kończysz zmianę → [[ZIOMEK_DEFINITION_OF_DONE.md]] + re-run dashboardu entropii (liczby mają MALEĆ).
5. Budujesz nowy moduł → celuj w 6 filarów (§2): jeden WorldState, czysty rdzeń, typ zamiast sentinela, inwariant-strażnik, flaga w rejestrze, golden-replay.
