# B06 — KLASA D: DRYF REALNOŚCI FLAG (deklarowane ≠ efektywne)

**Agent:** B06-D-flag-drift · **Lane B** · **READ-ONLY** · **2026-06-30 ~14:1x UTC · sesja tmux 2**
**HEAD:** `8024705` (working tree silnika czysty pod audyt). Wszystkie `plik:linia` ze ŚWIEŻEGO grepu DZIŚ (dryfują — re-grepuj przed użyciem).
**Maszyneria flag (zweryfikowana):**
- `common.flag(name, default)` (common.py:46-48) = `flags.json → przekazany default`. **NIE konsultuje stałej modułu.**
- `common.decision_flag(name)` (common.py:348-361) = `flags.json → globals()[stała modułu] → False`. **Konsultuje stałą.**
- `common.flag_fingerprint()` (common.py:364-371) = `ETAP4_DECISION_FLAGS (59) + _FINGERPRINT_EXTRA_FLAGS (4) = 63 flagi`.
- Wzorzec `C.flag(name, getattr(C, name, False))` (np. feasibility_v2.py:1051) = **emuluje decision_flag** (default = stała modułu) → masking stałej JEST materialny.

**Liczby zmierzone (import common w venv dispatch):** flags.json = 198 nie-`_` kluczy (125 `ENABLE_*` + 41 nie-ENABLE bool). ETAP4=59 (52 w json + **7 absent→stała**). NUMERIC=26 · INFRA=3 · FP_EXTRA=4. **Leak D4 = 71 `ENABLE_*` poza WSZYSTKIMI rejestrami.** Fingerprint=63. **19/19 route/canon+solver env-frozen POZA fingerprintem.**

**Relacja do Fazy A:** A3/A5/recon zinwentaryzowały D2 (drop-iny) + leak-count + COMMIT_DIVERGENCE. Moje EXTENSIONS: (1) **wykonałem sweep dead-flag którego A3 jawnie nie zrobiło** → 4 potwierdzone martwe + 3 skeleton-dead; (2) zweryfikowałem read-path COMMIT_DIVERGENCE = `decision_flag` (masking materialny) + odkryłem klasę silent-OFF-na-utracie-klucza dla FAIL12/PRE_SHIFT/PACZKA; (3) potwierdziłem D3 cross-repo courier_api na ŻYWYM kodzie (nie „historical near-miss"); (4) udowodniłem 13/16 decyzyjnych leak-flag ma żywego konsumenta w feasibility/scoring/selekcji.

---

## D1 — ZADEKLAROWANA-NIEPODPIĘTA (martwy kod flagi)

**Metoda:** sweep KAŻDEJ `ENABLE_*` (flags.json ∪ stałe common.py) → `grep -rl` konsumentów w `.py` poza common.py/tests/.bak (engine) + cross-repo (panel+courier_api). Flaga bez konsumenta = martwa.

| # | Flaga (def) | Default | Konsumenci | Werdykt |
|---|---|---|---|---|
| D1-1 | `ENABLE_CLUSTER_DROP_GROUPING_METRIC` common.py:2783-2784 | env `"0"`=OFF | **0 (tylko własna def)**, cross-repo 0 | **MARTWA** |
| D1-2 | `ENABLE_PANEL_IS_FREE_AUTHORITATIVE` common.py:1144 | env `"1"`=**ON** | **0**, cross-repo 0 | **MARTWA-ale-ON** (nazwa sugeruje że panel `is_free` jest autorytetem floty — NIC tego nie czyta; mylące) |
| D1-3 | `ENABLE_SPEED_TIER_LOADING_PLANNED` common.py:909 | literał `False` | **0** (skeleton `speed_tier_tracker.py` C4 — „brak consumera") | **MARTWA** |
| D1-4 | `ENABLE_TRANSPARENCY_SCORING` common.py:937 | literał `True` | **0** (sibling `_ROUTE`/`_REASON` żywe; F2.2 „Commit C DEFERRED: scoring breakdown") | **MARTWA-ale-True** (komentarz „Score decomposition" + `=True` sugeruje LIVE; nigdy wpięte) |
| D1-5 | `A4_TEST_FLAG` flags.json=False | — | tylko `tests/test_a4_config_reload_pubsub.py` | artefakt testowy w PROD flags.json |

**SKELETON-DEAD (flaga WPIĘTA, ale stała hardcoded False → gałąź nieosiągalna w prod — D1∩D3):**
| # | Flaga | Wpięcie | Powód martwoty |
|---|---|---|---|
| D1-6 | `ENABLE_MID_TRIP_PICKUP` common.py:920 (False) | `commitment_emitter.py:94` `if not ENABLE_MID_TRIP_PICKUP:` | C6 skeleton; stała False, NIE w flags.json → nie-flipowalna hot |
| D1-7 | `ENABLE_PENDING_QUEUE_VIEW` common.py:921 (False) | `pending_queue_provider.py:56/89` + `dispatch_pipeline.py:3372` | C7 skeleton; jw. |
| D1-8 | `DEPRECATE_LEGACY_HARD_GATES` common.py:912 (False) | `scoring.py:228` + `feasibility_v2.py:1123` | **DOUBLE-DEAD**: stała nigdy nie flipnięta ORAZ `scoring.py:197` „MARTWY kwarg" — live-caller nie przekazuje `r6_soft_penalty_c3_legacy` → gałąź legacy R6-soft martwa dwustronnie |

→ Wnioski dla Fazy E: A3 deklarowało „0 potwierdzonych martwych" (bo nie robiło sweepu). **Realnie ≥4 martwe + 3 skeleton + 1 test-artefakt.** D1-2/D1-4 GROŹNE bo default sugeruje aktywność (ON/True) wprowadzając w błąd czytającego protokół.

---

## D2 — ENV-FROZEN vs flags.json vs DROP-IN (stan ≠ jeden plik)

**Reguła:** `decision_flag`/`flag` widzą flags.json (hot). Flagi env-frozen (`_os.environ.get(...)` na poziomie modułu) ustalane RAZ przy imporcie z env PROCESU → różne per-serwis, NIEobecne w flags.json → hot-reload bezskuteczny, restart wymagany.

### D2-1 — SOLVER L5 env-frozen POZA flags.json I fingerprintem (CORE-D)
| Flaga (def) | flags.json | fingerprint | konsument | klasa |
|---|---|---|---|---|
| `ENABLE_V326_OR_TOOLS_TSP` common.py:2356 (env `"1"`) | **ABSENT** | **NIE** | route_simulator_v2 (TSP feasibility) | D2 |
| `ENABLE_V326_SAME_RESTAURANT_GROUPING` common.py:3159 (env `"1"`) | **ABSENT** | **NIE** | route_simulator_v2 (pre-TSP grouping) | D2 + C3 (karmi OR-Tools) |
| `USE_V2_PARSER` panel_client.py:93 (env `"0"`) | **ABSENT** | **NIE** | parser HTML panelu | D2 + **J** |

`USE_V2_PARSER` = `1` TYLKO na `dispatch-panel-watcher` (override.conf); inne procesy importujące `panel_client` (np. dispatch-shadow przy recheck) czytają własny env → default `0` = **parser V1**. Panel-watcher=V2, shadow=V1 = dwa parsery na ten sam panel (PLAUSIBLE niespójność danych wejściowych L1; CONFIRM = trace czy shadow parsuje HTML, Faza C).

### D2-2 — TWIN ASYMETRIA plan-recheck ↔ panel-watcher ↔ shadow (oba regenerują kanon) — ŚWIEŻY `systemctl show`
```
dispatch-shadow        : (ZERO route/canon env)
dispatch-plan-recheck  : CARRIED_FIRST_RELAX, PLAN_SEQUENCE_LOCK, PLAN_RECHECK_COMMITTED_PROPAGATION,
                         PLAN_RECHECK_LIVE_ETA_REFRESH, PLAN_CANON_ORDER_INVARIANTS
dispatch-panel-watcher : CARRIED_FIRST_RELAX, RECANON_ON_WRITE, PLAN_CANON_ORDER_INVARIANTS
                         (BRAK: PLAN_SEQUENCE_LOCK, COMMITTED_PROPAGATION, LIVE_ETA_REFRESH)
```
**panel-watcher recanon (`recanon_courier`/`redecide_courier`) regeneruje kanon BEZ SEQUENCE_LOCK / COMMITTED_PROPAGATION / LIVE_ETA_REFRESH**, które tick plan-recheck MA — mimo że drop-iny `unified-route-f3.conf` deklarują „spójność z tickiem". Ścieżka zdarzeniowa (write/pickup/override) vs tickowa (5min) mogą dać RÓŻNY kanon. Wszystkie env-frozen, ABSENT flags.json, ABSENT fingerprint. **shadow NIE MA żadnej** → gdyby liczył plan in-process, robiłby to bez niezmienników kanonu (B.3, PLAUSIBLE). Materialność = Faza B/C trace.

### D2-3 — FINGERPRINT = KŁAMIĄCY PRZYRZĄD PARYTETU (klasa E zasilająca D)
`flag_fingerprint()` pokrywa **63** flagi (ETAP4+EXTRA). POZA nim: **19 route/canon+solver env-frozen** (wszystkie z D2-1/D2-2) + **~25 decyzyjnych leak** (D4). Log startowy twierdzi „fingerprinty shadow/czasowka/plan-recheck MUSZĄ być identyczne" → **fałszywe zapewnienie**: drop-in dodany do jednego serwisu a nie do bliźniaka (np. SEQUENCE_LOCK tylko plan-recheck) NIE zostanie złapany przez porównanie fingerprintów. Instrument który ma WYKRYWAĆ D2 sam pokrywa <70% flag decyzyjnych.

### D2-4 — BASELINE TEST CZERWONY = ŻYWY DRYF FLAG-DOC
`tests/test_flag_doc_coverage::test_baseline_is_not_stale` FAIL (recon §F). 29.06 był ZIELONY (`257d315`). Przyczyna = dzisiejsze flagi AUTON-02 (`AUTO_ASSIGN_REQUIRE_*`) + force-recheck (`ENABLE_COORDINATOR_FORCE_TIME_RECHECK`) dorzucone przez równoległe sesje, niezreconciliowane do baseline doc-coverage. Mierzalny dryf deklarowane≠udokumentowane.

---

## D3 — ON-ALE-GAŁĄŹ-NIEOSIĄGALNA (short-circuit inną flagą)

### D3-1 — cross-repo: APP_ROUTE_FROM_CONSOLE cieniuje lokalny carried-first (courier_api) — ŻYWY KOD
`courier_orders.py` (effective env: `APP_ROUTE_FROM_CONSOLE=1`, `BUILD_VIEW_TRUST_CANON_ORDER=1`, `PLAN_AWARE_PODJAZDY=1`):
- `:1116` `if config.APP_ROUTE_FROM_CONSOLE and _route_podjazdy is not None and mine:` → TRUE → `order_podjazdy(..., trust_canon=BUILD_VIEW_TRUST_CANON_ORDER)` → `_console_done=True` (:1132).
- `:1136` `if not _console_done and _plan_is_active(plan):` → **CAŁY blok lokalnego fallbacku (`_plan_stop_sequence`/`_prioritize_carried_dropoffs`/`_reorder_pickup_steps_by_committed`, :1136-1177) NIEOSIĄGALNY** poza fail-soft (wyjątek w console-build).
- `:1158` `if config.PLAN_ORDER_INVARIANTS and not config.BUILD_VIEW_TRUST_CANON_ORDER:` → **DODATKOWO** wymaga `BUILD_VIEW=0`, ale jest `=1` → lokalny re-order carried-first **podwójnie martwy** pod obecną kombinacją 2 flag.

`BUILD_VIEW_TRUST_CANON_ORDER` SAMA NIE jest martwa (czytana :1120 jako `trust_canon`). Martwy jest **lokalny carried-first reorder** (`_prioritize_carried_dropoffs`+`_reorder_pickup_steps_by_committed`) — żyje tylko jako fail-soft na wyjątku. Anty-wzorzec [[feedback-source-not-patches]] „carried-first naprawiany 10× w złej gałęzi" = dokładnie ta nieosiągalna gałąź.

### D3-2 — auto_assign executor INERT (ENABLE_AUTO_ASSIGN OFF)
`auto_assign_executor.py:219` `if not C.decision_flag("ENABLE_AUTO_ASSIGN"): return None`. flags.json=`false`. Gate G1-G14 (`auto_assign_gate.evaluate_auto_assign`) + profil AUTON-02 (`AUTO_ASSIGN_REQUIRE_CLASSIFIER_AUTO`/`_REQUIRE_MARGIN` ETAP4-absent→stała True, + `AUTO_ASSIGN_*` NUMERIC) liczą TELEMETRIĘ na każdej decyzji, ale **gałąź EGZEKUCJI nieosiągalna dopóki gate OFF**. Latentne (by-design AUTON-01), ale 3 ETAP4-flagi + 4 numeryczne kształtują martwą-do-wykonania gałąź; pierwsze ON = NIEPRZETESTOWANE E2E (MEMORY).

---

## D4 — POZA ETAP4_DECISION_FLAGS (conftest-leak + brak parytetu)

**71 `ENABLE_*` w flags.json POZA wszystkimi rejestrami** (ETAP4/EXTRA/NUMERIC/INFRA). Skutek 2-stronny:
1. `conftest._isolate_flags_json` ICH NIE STRIPUJE → test mający stałą-modułu OFF i tak dziedziczy ŻYWY flags.json (prod-True) → regresja „cicho biega ON myśląc że OFF". **Dokładnie ta klasa przepuściła `ENABLE_BEST_EFFORT_OBJM_R6_KEY`** (komentarz common.py:156-161).
2. NIE w fingerprincie → brak parytetu cross-proces.

### D4-a — PODZBIÓR DECYZYJNY (leak GROŹNY) — udowodnione żywe konsumenty w rdzeniu
| Flaga (json=True) | Konsument decyzyjny | Rola |
|---|---|---|
| `ENABLE_EXCLUDE_BY_CID` | courier_resolver.py:1444 | **HARD filtr floty** |
| `ENABLE_INACTIVE_COURIER_GUARD` | courier_resolver.py:848 | filtr floty |
| `ENABLE_ZOMBIE_PICKUP_AT_GUARD` | courier_resolver.py:613 | filtr bag |
| `ENABLE_GPS_BBOX_GUARD` | courier_resolver.py:909 | zaufanie pos_source |
| `ENABLE_R6_SOFT_PEN_CAP` | dispatch_pipeline.py:4230 | cap kary R6 (scoring) |
| `ENABLE_OBJ_COMMITTED_PICKUP_PENALTY` | dispatch_pipeline.py:3402 | scoring committed |
| `ENABLE_BEST_EFFORT_POS_SOURCE_KEY` | dispatch_pipeline.py:6749 | bucket selekcji pos_source |
| `ENABLE_PLN_RESORT_WITHIN_TIER` | dispatch_pipeline.py:1041 | re-sort selekcji |
| `ENABLE_V3273_WAIT_REJECT_PICKED_UP_ONLY` | dispatch_pipeline.py:4429 | feasibility/scoring wait |
| `ENABLE_NEW_COURIER_RAMP` | dispatch_pipeline.py:1814 | scoring ramp |
| `ENABLE_R1_WAVE_SCOPED_DIRECTIONALITY` | feasibility_v2.py:843 | kierunkowość R1 |
| `ENABLE_LOAD_PLAN_PURE_READ` | dispatch_pipeline.py:2361 | plan read |
| `ENABLE_KEBAB_KROL_DINNER_EXCLUSION` | auto_proximity_classifier.py | HARD-reject warunkowy |
| `ENABLE_PICKUP_FROM_GROUND_TRUTH` | panel_watcher.py | anchor odbioru |
| `ENABLE_PICKUP_TIME_MIRRORS_CK` / `ENABLE_ELASTYK_CK_NO_BACKWARD` | state_machine.py / panel_watcher.py | czas CK |
| `ENABLE_OBJM_LEXR6_SELECT_SHADOW` (json=False) | (twin shadow OBJM) | bliźniak selekcji poza rejestrem |

→ **13/16 sprawdzonych ma żywego konsumenta w feasibility/scoring/selekcji/resolver.** To NIE shadow-only — to flagi które realnie sterują decyzją, a są poza izolacją testów i parytetem.

### D4-b — 41 nie-`ENABLE` bool też poza rejestrem
Decyzyjno-krytyczne: `kill_switch_to_v1`, `PARSER_DEGRADED`, `commitment_level`, `PENDING_RESWEEP_LIVE`. Reszta peryferyjna (telegram/recon/shift/czasowka-enabled). Brak izolacji conftest + parytetu.

---

## D5 — SPRZĘŻONE-MASKUJĄCE

### D5-1 — JEDYNA PRAWDZIWA INWERSJA: `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` (P1)
- def common.py:2805 `_os.environ.get("...","1")=="1"` → **env-default ON** (komentarz: „Default ON — strict safety. Env override dla replay/calibration").
- flags.json = **False**.
- read-path **zweryfikowany**: dispatch_pipeline.py:6523 `if (C.decision_flag("ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE") and ...)`.
- `decision_flag` = flags.json(False) → effective **OFF** (zgodne z dyrektywą ALWAYS-PROPOSE: gate KOORD-redirect wyłączony).
- **MINA:** usunięcie/utrata klucza z flags.json (reset, korupcja, edycja multi-sesji gubiąca klucz) → `decision_flag` spada na `globals()` = env-const **True** → **verdict-gate CICHO FLIPUJE ON** → KOORD-redirect wraca → utrata ALWAYS-PROPOSE dla rozjazdu plan-commit >10min. Const i flags.json kodują **PRZECIWNE intencje** (autor: ON-strict; Adrian: OFF-always-propose). Klasa **M+I+D5**. Jedyny przypadek `const=ON masked OFF` z 50 maskowań.

### D5-2 — „BEZPIECZNY FALLBACK OFF" NIE jest bezpieczny dla HARD/policy flag (P2)
49 pozostałych maskowań = wzorzec ETAP4 `const=OFF, json=ON` (zaprojektowany). ALE dla flag czytanych przez `decision_flag` LUB `C.flag(name, getattr(C,name,False))` (oba konsultują stałą) utrata klucza flags.json → **cichy OFF który COFA świadomą decyzję**:
| Flaga | read-path | const | utrata-klucza → |
|---|---|---|---|
| `ENABLE_FAIL12_SCHEDULE_FAILOPEN` | feasibility_v2.py:686 `decision_flag` | OFF | **fail-CLOSED**: brak grafiku → HARD-reject kurierów → „BRAK KANDYDATÓW" |
| `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP` | feasibility_v2.py:794 `decision_flag` | OFF | floor pre-shift znika → odbiór przed startem zmiany |
| `ENABLE_PACZKA_R6_THERMAL_EXEMPT` | feasibility_v2.py:1051 `C.flag(...,getattr-const)` | OFF | paczki traktowane jak gorące → R6-reject 35min |

→ „fallback OFF = bezpieczny" to MISNOMER dla flag fail-open/exempt/floor/policy. Dla flag czytanych przez `C.flag(name, <literał>)` (np. `NO_GPS_EQUAL_TREATMENT` pipeline:2393 default-literał, `R_RETURN_TO_RESTAURANT_VETO` feasibility:906 `,False`) stała modułu jest **w ogóle nie-konsultowana** → const=False obok json=True = **martwa stała / doc-drift** (ETAP4 wymaga stałej per flaga `test_all_etap4_flags_have_module_const`, ale runtime jej nie czyta → const wegetatywny, służy TYLKO conftest-strip+fingerprint+test).

### D5-3 — PARY SPRZĘŻONE (live XOR shadow, ale shadow-twin POZA rejestrem) (P3)
| Para | stan | uwaga |
|---|---|---|
| `ENABLE_OBJM_LEXR6_SELECT` (ETAP4 ON) ↔ `_SELECT_SHADOW` (leak OFF) | live XOR shadow OK | shadow-twin niewidoczny w fingerprincie |
| `ENABLE_BEST_EFFORT_OBJM_R6_KEY` (ETAP4 ON) ↔ `ENABLE_FEAS_CARRY_READMIT` (ETAP4 OFF) | bliźniacze re-admit (selekcja vs feasibility) | protokół: ruszać RAZEM |
| `ENABLE_DRIVE_MIN_CALIBRATION_V2` (leak OFF) ↔ `_V2_SHADOW` (leak ON) | main OFF/shadow ON | oba poza rejestrem |
| `ENABLE_PENDING_RESWEEP` (leak ON shadow) ↔ `PENDING_RESWEEP_LIVE` (bool OFF) | shadow/live | oba poza rejestrem |
| `ENABLE_PANEL_BG_REFRESH` shadow=1 / watcher=0 | per-proces zamierzone | poza fingerprintem |

---

## TABELA POKRYCIA (jawne, nie cisza)

| Obszar | Zbadane | Metoda | Wynik |
|---|---|---|---|
| Maszyneria flag (flag/decision_flag/fingerprint) | ✅ | Read common.py:40-371 | 3 read-path rozróżnione |
| Rejestry (ETAP4/EXTRA/NUMERIC/INFRA) | ✅ | parser AST tuple + set-algebra | 59/4/26/3; 71 leak; 7 ETAP4-absent |
| D1 dead-flag sweep | ✅ | grep -rl konsument per `ENABLE_*` (engine+cross-repo) | 4 martwe + 3 skeleton + A4_TEST_FLAG |
| D2 env-frozen | ✅ | `systemctl show -p Environment` ×3 świeży + grep def | twin-asym potwierdzona; OR_TOOLS/GROUPING/USE_V2 absent json+fp |
| D2 fingerprint gap | ✅ | set-diff 19 route/canon vs fingerprint(63) | 19/19 poza fp |
| D3 cross-repo courier_api | ✅ | Read courier_orders.py:1108-1177 + systemctl env | lokalny carried-first podwójnie nieosiągalny |
| D3 auto_assign INERT | ✅ | grep read-path executor:219 + flags.json | gate liczy, exec martwy (flaga OFF) |
| D4 leak decyzyjny | ✅ | grep konsument 16 flag w DECMODS | 16/16 żywe (13 w core, 3 w ingest/CK) |
| D5 masking | ✅ | skan const-vs-json 50× + read-path 6 flag | 1 inwersja + klasa silent-OFF |

### LUKI POKRYCIA (czego NIE zbadałem + powód)
1. **Pełny read-path (decision_flag vs C.flag-literał vs C.flag-getattr) dla WSZYSTKICH 59 ETAP4** — sprawdziłem 6 reprezentatywnych. Ile dokładnie ma `const` wegetatywny (C.flag-literał) vs materialny (decision_flag/getattr) = sweep dla Fazy E (kwantyfikacja D5-2). Powód: budżet; próbka wystarcza by udowodnić wzorzec.
2. **USE_V2_PARSER: czy shadow REALNIE parsuje HTML** = PLAUSIBLE nie CONFIRMED (trace Faza C). Powód: read-only, brak runtime-trace.
3. **D2-2 materialność** (czy panel-watcher recanon bez SEQUENCE_LOCK daje INNY kanon niż tick) = PLAUSIBLE; wymaga trace osiągalności gałęzi (Faza B/C). Powód: nie odpalam silnika.
4. **Konsola (`app/core/flags.py` DEFAULT_FLAGS) + courier_api (`config.py`)** = 3. i 2. system flag (A5 C.7) — zinwentaryzowane przez A5, NIE re-grepowałem dead-flag wewnątrz nich. Powód: granica, A5 pokrył; mój sweep dead = engine+spot cross-repo dla 4 martwych.
5. **`dispatch-czasowka` env** (CZASOWKA_TELEGRAM_DRYRUN=1) — A5 zmierzył; nie powtarzałem.
6. **Wartości NUMERIC per-proces** (26 FLAGS_JSON_NUMERIC_OVERRIDES) = klasa N (rozsyp progów), nie D — poza moim zakresem.

---

## HANDOFF / DEDUP (do Fazy E — anty-double-count)
- **Root „brak jednego źródła stanu flag / 3 systemy" (K1+K7):** D2-1/D2-2/D2-3/D4 zwijają się tu — NIE liczyć fingerprint-gap, leak, env-frozen jako 3 osobne chaosy; to jeden root „flags.json nie jest kanonem efektywnego stanu". Fix = rozszerzyć fingerprint+ETAP4 o route/canon/solver+leak ALBO 1 rejestr cross-system.
- **Root „martwy kod flag" (D1):** 4 martwe + 3 skeleton + DEPRECATE_LEGACY — osobny od dryfu; kandydat na czyszczenie (overlap z agentem klasy K).
- **COMMIT_DIVERGENCE inversion (D5-1):** distinct, P1 — własny root (M+I), NIE zwija się do K1.
- **silent-OFF-policy (D5-2):** distinct, zasila Fazę D (precedencja HARD) — fail-open/exempt/floor degradują na utracie klucza.
- **D3 courier_api:** zwija się do A6-root R2 „one route-order module" (cross-repo) — to gałąź flag-short-circuit TEGO samego rozjazdu route-order.
