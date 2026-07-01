# FAZA F — STAN DOCELOWY rodziny **R1 Jedno-źródło** (A1 · A2 · J)

> **DRAFT — produkt syntezy (audyt READ-ONLY).** Zero kodu, zero flipów, zero restartów. Ten dokument definiuje KANONICZNY STAN DOCELOWY + PLAN KONSOLIDACJI dla rootów rodziny R1. Kod idzie OSOBNO protokołem ETAP 0→7 + ACK (PoC = wydzielony mini-sprint po akceptacji targetu). **Numery linii zweryfikowane świeżym grep DZIŚ 2026-06-30, HEAD silnik `8024705` — DRYFUJĄ, re-grepuj przed dotknięciem.**

**Sesja:** tmux 2 · **Data:** 2026-06-30 · **Tryb:** READ-ONLY
**Wejście:** `E_dedup_1_singlesource_placement.md` (ROOT1-9) + `E_dedup_2_truth_conflict.md` (R14 flag + R6 guard) + `B01/B02/B11/A3` + `AUDYT_preshift_pickup_floor_HANDOFF_sesja2.md` + werdykty adwersaryjne (flag-state CONFIRMED, carried-guard CONFIRMED, equal-treatment PLAUSIBLE) + świeże greppy.
**Kontrakty referencyjne (DESIGN §4):** ①JEDNO-źródło/regułę(kopie=1) ②kontrakt-warstw(HARD-przed-SOFT+inwarianty) ③parytet-bliźniaków(divergence=0) ④prawda-flag(dead=0,rejestr) ⑤prawda-przyrządów(void=0 przed flipem) ⑥brak-dryfu-semantyki ⑦kompletność-cyklu-życia ⑧koherencja(0-konfliktów).

---

## 0. ZAKRES — które rooty należą do R1 (+ granice anty-double-count)

Rodzina **R1 = naruszenie JEDNEGO ŹRÓDŁA PRAWDY** (klasy A1 ta-sama-reguła-N-kopii / A2 to-samo-pojęcie-N-powierzchni / J cross-repo-bez-importu). Z przetrwałych rootów (po dedup+adwersaryjna weryfikacja) do R1 należą:

| # | Root | Sev | Klasy | Werdykt | source | open | Rola w R1 |
|---|---|---|---|---|---|---|---|
| **R1-A** | `one-route-order-module` | P1 | A1,A2,B,J,K | CONFIRMED | TAK | TAK | **rdzeń** — kolejność jazdy w 5+ kopiach / 3 repa |
| **R1-B** | `earliest-pickup-floor-no-chokepoint` | P1 | A1,A2,H | CONFIRMED | TAK | TAK | **rdzeń** — `available_from` nie istnieje (0 trafień), 17 powierzchni |
| **R1-C** | `frozen-lexqual-shadow` | P2 | A1,B,E | PLAUSIBLE | **NIE** | TAK | **resztka** — 1 frozen inline klucza selekcji (silnik UNIFIED) |
| **R1-D** | `flag-state-3-layer-no-single-source` | P1 | D,E,**J** | CONFIRMED | TAK | TAK (harm LATENT) | **fundament** — stan-flag w 3 warstwach, brak rejestru (klasa J ⊂ R1) |

**Granice (NIE liczę podwójnie — cross-ref do innych rodzin):**
- **A1-rodzeństwo geometrii** (`geometry-blind-selection` C5.a/b: `R1_MAX_DELIV_SPREAD_KM`=`BUNDLE_MAX_DELIV_SPREAD_KM`=8.0 ×2, bearing ×2) — sama DUPLIKACJA stałej/formuły jest A1, ale geometria-ślepa-w-selekcji to **R2/C placement** (P0-A allocation-agent). Tu tylko cross-ref „1 stała `MAX_DELIV_SPREAD`" jako bramka anty-kopii.
- **A1-rodzeństwo SLA-anchor** (`r6-anchor-vs-sla-anchor` / `one-sla-r6-anchor`: 2 inline-lustra + 3 kopie `(sla_viol,dur)`) — kopie A1 są realne, ale rozstrzygnięcie precedencji anchor = **R7 koherencja** (R15/R16/R17). Tu cross-ref „1 `r6_thermal_anchor` helper".
- **`one-delivery-eta-source`** (ETA dostawy ×3-4 impl, J5) — był 6. kandydatem dedup (R1/A2 P2), **NIE przetrwał jako osobny persisted-root** (zwinięty do rodziny cross-repo-render-re-compute z R1-A). Trzymam go jako **bliźniaczy sub-target R1-A** (ta sama przyczyna: powierzchnia renderu RE-LICZY zamiast IMPORTOWAĆ kanon), oznaczony jawnie.
- **`carried-first-guard-empty-env-void`** (persisted, fam R3, CONFIRMED source open) — to INSTRUMENTALNA manifestacja R1-D (przyrząd czyta flagi silnika jako default-OFF bo ma pusty env). Jego `consolidation_target` (dedup_2 R6) = „jedno źródło stanu-flag dla N procesów" = DOKŁADNIE R1-D. Fix R1-D leczy go u źródła; raportowany pełniej w rodzinie R3-Prawda.
- **Selekcja `_selection_bucket` + 8 bliźniaków pozycji** = root `out-of-engine-position-gates` (allocation-agent, K1). Engine UNIFIED; otwarte resztki to out-of-engine gates — **NIE R1-rdzeń tu** (to R2/R3 placement+truth). Cross-ref.
- **STOP na dyspozytorni** — Mailek/Papu poza zakresem.

---

## 1. KANONICZNY STAN DOCELOWY (per root: twardy kontrakt + inwariant runtime)

### R1-A — `one-route-order-module` (P1, CONFIRMED źródło, OTWARTY)

**CO DZIŚ (entropia, świeżo zweryfikowane):**
- Kolejność JAZDY (carried-first-relax + no-return-to-departed-pickup + bundling „1 restauracja=1 podjazd") żyje w **5 żywych kopiach / 3 repa / 3 języki** bez wspólnego importu repo↔repo:
  - ŹRÓDŁO/choke: `plan_recheck.py:1478 _apply_canon_order_invariants` (def; użycia `:780`, `:1582`).
  - render silnik (apka-API): `route_podjazdy.py:190 order_podjazdy` (własna kopia kolejności, ETA-aware).
  - **2. PRODUCENT kanonu BEZ inwariantów:** `panel_watcher.py:436 _save_plan_on_assign` → `plan_manager.save_plan` zapisuje `plan.sequence` VERBATIM, **NIE woła `_apply_canon_order_invariants`** + wstrzykuje placeholder `(0,0)` coords → okno 5-min „plan bez inwariantów" (most do sentineli M).
  - render KONSOLA: `nadajesz_clone/.../fleet_state.py:395 _build_route` (KOPIA, **0 importów `dispatch_v2`/`route_podjazdy`** — potwierdzone grepem).
  - render APKA-API: `courier_api/courier_orders.py:1116` importuje `route_podjazdy` ZA flagą; inaczej własny `_plan_stop_sequence` (CICHY fail-soft `:35-41` → `_route_podjazdy=None`, połknięte).
  - render APKA Kotlin: `RouteLogic.kt:27 buildSteps` (render serwera) + **własny bundling** `restaurantKey`/`PICKUP_MERGE_MIN=54` (4. kopia bundlingu).
  - **MARTWA 5. kopia:** `courier_api_panelsync/courier_orders.py:558 build_view` (665 L vs 1285 L — 620 L różnicy; nieserwowana, `courier-panel-sync.service` biega `panel_sync.py`, NIE `main.py`).
- **Parytet ILUZORYCZNY/wygasający (klasa E + H):**
  - „Golden test" = **dwie ROZŁĄCZNE suity** (`test_route_podjazdy_trust_canon.py` w silniku vs `test_fleet_route.py` w panelu) — inne repo/venv/fixtury, **ZERO asercji `order_podjazdy(X) ≡ _build_route(X)`** na wspólnym wejściu. Docstringi kłamią „parytet TESTEM".
  - Jedyny realny parytet repo↔repo = runtime-monitor `ziomek_time_route_monitor.py:386`, który **SAM WYGASA `MONITOR_STOP_AFTER=2026-07-10`** (T-10 dni → `return 0` no-op). Po dacie: 0 importu + golden iluzoryczny + monitor martwy = **ZERO sieci parytetu**.
  - `PICKUP_MERGE_MIN=10` ręcznie skopiowany 5× (silnik `route_podjazdy.py:30` / konsola `fleet_state.py:88` / front `Ops13Console.tsx:182` / Kotlin `RouteLogic.kt:54`) — parytet komentarzem.
- **Bliźniak R1-A' (`one-delivery-eta-source`):** ETA dostawy liczona 3× niezależnie (silnik `chain_eta`, apka własny OSRM+haversine `courier_orders.py`, konsola własny OSRM `fleet_state._eta_chain`); wspólny kanał tylko `live_eta_cache` (read-when-fresh). Ta sama przyczyna co R1-A: render RE-LICZY zamiast IMPORTOWAĆ.

**STAN DOCELOWY (kontrakt ① + ③ + ⑦):**
1. **① JEDNO źródło kolejności-jazdy** = wspólny pakiet route-order (carried-first-relax + no-return + bundling), **źródło = silnik**, importowany przez 3 repa. Gdy import cross-repo niewykonalny (osobny venv/język) → **twardy golden-fixture equivalence** na WSPÓLNYM kanonicznym wejściu (kontrakt parytetu, nie iluzja). `copy-count` reguły kolejności = **1** (lub 1 + bliźniaki związane golden-fixture). To SAMO dla ETA-dostawy (R1-A'): jedno źródło chain-ETA cross-repo LUB `live_eta_cache` autorytatywny z fail-closed gdy stale.
2. **③ Parytet bliźniaków z konstrukcji** = test równoważności `order_podjazdy(X) ≡ _build_route(X) ≡ courier_orders-route(X)` na wspólnym zestawie wejść, **uruchamiany w CI obu repo** (NIE wygasający runtime-monitor). `twin-divergence = 0` dowiedziona testem.
3. **⑦ Kompletność cyklu życia:** usunąć MARTWY `courier_api_panelsync/courier_orders.py` (5. kopia, K). 2. producent `_save_plan_on_assign` MUSI wołać `_apply_canon_order_invariants` (lub być zlikwidowany na rzecz recanon-on-write następnego ticku z gwarancją okna). `PICKUP_MERGE_MIN` = **1 nazwana stała** (źródło importowane / golden-pinned, nie 5 literałów).
4. Cichy fail-soft importu apki (`:35-41`) → **fail-LOUD** (alert na utratę cross-repo importu, nie `print` połknięty) — inaczej zerwanie importu = cicha dywergencja trasy bez sygnału (klasa M).

**INWARIANT RUNTIME (tripwire, fail-loud):**
> `order_podjazdy(bag, plan) ≡ _build_route(plan, bag) ≡ courier_orders.build_view-route(...)` na wspólnym kanonicznym wejściu — egzekwowany **testem golden w CI**, nie monitorem z datą wygaśnięcia. Drugi tripwire: **żaden zapisany `courier_plans.json.sequence` nie omija `_apply_canon_order_invariants`** (2. producent objęty).

**BRAMKA „ZERO NOWYCH KOPII":** każdy krok USUWA powierzchnię/literał (panelsync −1, PICKUP_MERGE_MIN 5→1, monitor-wygasający → golden-CI). Żaden krok nie dodaje 6. renderu.

---

### R1-B — `earliest-pickup-floor-no-chokepoint` (P1, CONFIRMED źródło, OTWARTY)

**CO DZIŚ (entropia, świeżo zweryfikowane):**
- **NIE istnieje single-source** „najwcześniej kurier odbierze = `max(now, shift_start)`": `grep available_from --include=*.py` = **0 trafień** (potwierdzone DZIŚ); `grep` runtime-guard „pickup ≥ shift_start"/assert = **0**.
- **17 powierzchni** liczy czas-najwcześniejszego-odbioru, **tylko 4 mają floor** do `shift_start` (#1 candidate-eta, #3 plan-clamp `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP`, #7 telegram-dziedziczy, #16 konsola `CLAMP_PRESHIFT_PICKUP_ETA` — i ten floruje TYLKO ścieżkę OSRM-fallback).
- **Najszerszy leak (K2 „cofacz"):** `plan_recheck.py:534 _earliest_committed_pickup_anchor` + `:554 _start_anchor` kotwiczą committed/GPS/last_pos ale **NIGDY shift_start** → regen `courier_plans.json` co 5 min ODCLAMPOWUJE to, co `feasibility_v2.py:~794 PRE_SHIFT_DEPARTURE_CLAMP` sclampowało (asymetria faza-A↔faza-B).
- **Chokepoint bez floor:** `state_machine.py:~551` COURIER_ASSIGNED zapisuje committed (`pickup_at_warsaw`, czasówka authority) BEZ floor.
- **`shift_start` liczony NIEZALEŻNIE per powierzchnia:** silnik `courier_resolver._shift_start_dt:1252` (datetime z grafiku) vs konsola `fleet_state.py:63/858 _hhmm_to_min(sched.get("start"))` (HH:MM, własny fetch grafiku) → cross-repo dryf definicji „start zmiany".
- Render-łatka `fleet_state.py:~853 CLAMP_PRESHIFT_PICKUP_ETA` = **edge-patch** (floruje tylko OSRM, nie u źródła).

**STAN DOCELOWY (kontrakt ① + ② + ⑧):**
1. **① JEDNO źródło** `courier.available_from = max(now, shift_start)` policzone RAZ w `courier_resolver` (L0 roadmapy), obejmujące pre_shift+no_gps (dla no_gps on-shift = no-op bo `max(now,shift_start)=now`). `copy-count` „najwcześniejszy-odbiór" = **1**, konsumowane przez WSZYSTKIE 17 powierzchni (w t.cz. plan_recheck regen).
2. **② Kontrakt warstw / jeden chokepoint:** czas-odbioru przechodzi przez JEDEN chokepoint z jawną precedencją **frozen(R27) > floor(shift_start) > OSRM**; floor zapisany jako osobne pole `effective_pickup_at` (NIE nadpisuje deklaracji restauracji — zgodne z frozen). `state_machine` COURIER_ASSIGNED stosuje floor przy bindzie. **Usunąć render-clampy** (`CLAMP_PRESHIFT_PICKUP_ETA`, candidate-no_gps, telegram-legacy) — zastąpione sclampowanym planem ze źródła (L2/L3). `layer-violation` (floor-na-renderze) = **0**.
3. **⑧ Koherencja:** rozstrzygnąć **frozen↔floor** (R19, R7) — committed `<shift_start` (czasówka/elastyk pre-shift legalne) NIE może być „zatrzaśnięty" przez frozen broniący złego czasu; floor obejmuje wartość committed w chokepoincie. Rozstrzygnąć **3-way schedule** (R28): `is_on_shift` fail-OPEN (24/7) vs `_shift_start_dt` fail-CLOSE (None) vs FAIL12 — JEDNA polityka fail (fail-loud, nie cichy 24/7). `unresolved-conflict` (floor) = **0**.
4. `shift_start` = jedno źródło (silnik datetime) z którego konsola IMPORTUJE/odczytuje (nie własny HH:MM fetch) — usuwa cross-repo dryf definicji.

**INWARIANT RUNTIME (tripwire, fail-loud, L6):**
> Żaden `predicted_at` / `czas_kuriera` / render-pickup / `effective_pickup_at` **< `shift_start` przypisanego kuriera** (poza świadomym committed-deklaracja-restauracji oznaczonym jawnie). Strażnik na wzór `tools/carried_first_guard.py` (shadow-mierz-nawrót PRZED flipem). Sprzężony tripwire BUG#2: **żaden haversine na `(0,0)`** (sanityzacja coords u źródła ingestu).

**DECYZJE ADRIANA (30.06, ZABLOKOWANE — wbudowane w target):** Q1 zakres = **OBA** (jedno źródło floor + twardsza feasibility); Q2 = **nie dawaj zlecenia pre-shift kurierowi, który nie zdąży na odbiór** (zmieniaj KTO, nie czas — deklaracja restauracji nietykalna); Q1b = równość ZOSTAJE (`PRE_SHIFT_EQUAL_NO_PENALTY` nietknięte; „nie zdąży"=FAKT feasibility/spóźnienie R-LATE-PICKUP, NIE nowa kara); Q2b = floor obejmuje pre_shift+no_gps. ⚠ TWIST kalibracyjny: model spóźnienia OPTYMISTYCZNY (poślizg ~18min, rośnie z load) → L1 „odrzuć kto nie zdąży" musi liczyć na **load-aware buforze**, nie surowym ETA (cross-ref `calibration-on-wrong-axis` R5/G).

**BRAMKA „ZERO NOWYCH KOPII":** 17 re-liczeń → 1 `available_from` + 1 chokepoint + 1 inwariant; render-clampy USUWANE (nie dokładane). Każda powierzchnia konsumuje, nie re-liczy.

---

### R1-C — `frozen-lexqual-shadow` (P2, PLAUSIBLE, **source=NIE**, OTWARTY)

**CO DZIŚ (entropia, świeżo zweryfikowane):**
- Silnik selekcji **JUŻ UNIFIED**: kanon `objm_lexr6.py:29 lex_qual` + 5 importerów (golden-test `"def _lex_qual" not in src`); bucket pozycji `_selection_bucket` UNIFIED (6 konsumentów, golden). To NIE chaos.
- **Jedyna otwarta inline-resztka:** `dispatch_pipeline.py:1122 _objm_lexr6_shadow._lex_qual` = **3-krotka HARD-CODED**, NIE post-shift-aware, ZERO importu kanonu. Kanon warunkowo 3/4-krotka (prepend `post_shift_overrun_penalty` gdy `ENABLE_POST_SHIFT_OVERRUN_PENALTY` ON, `objm_lexr6.py:44`).
- Zgodne dziś TYLKO bo flaga OFF (wiodące 0.0 no-op) + `ENABLE_OBJM_LEXR6_SELECT_SHADOW=False` (cień nawet nie wołany `:6250`). **Podwójnie uśpiony.** Flip `POST_SHIFT` ON → cień rankuje INACZEJ niż live = **kłamiący przyrząd** (klasa E przy walidacji at#152/at-200, checkpoint 03.07).

**Dlaczego source=NIE:** adwersaryjna weryfikacja = **2 resztki JEDNEJ ukończonej unifikacji K1** (NIE 2 osobne chaosy). Engine UNIFIED; to „dokończ unifikację po walidacji", nie „posprzątaj bałagan". Severity P2/P3 latent.

**STAN DOCELOWY (kontrakt ① + ⑤ + ③):**
1. **① / ⑤** Po PASS checkpointu **at#152/at-200 (03.07)** → przepiąć `_objm_lexr6_shadow._lex_qual` na `objm_lexr6.lex_qual` (kanon) **albo usunąć cień**. `copy-count` klucza-jakości = **1** (ostatnia frozen inline znika).
2. **③ Parytet:** golden-test `shadow_lex_qual ≡ canon lex_qual` bajtowo przy **OBU** stanach `ENABLE_POST_SHIFT_OVERRUN_PENALTY` (zanim cień będzie użyty do walidacji-flipu). `void/untested` przyrządu = **0 przed flipem zależnym** (⑤).
3. Sub-A1 `post_shift_overrun_penalty` zakodowany 3 sposobami niespójnie (`_best_effort_sort_key:591` bezwarunkowy slot / `objm_lexr6.lex_qual:44` warunkowa krotka / frozen NIGDY / main `_late_pickup_score_first` brak) → ujednolicić tak, by selekcja-główna ≡ best-effort na osi post-shift (RAZEM z flipem POST_SHIFT).

**INWARIANT:** `shadow-selection-key ≡ canon-selection-key` przy każdym stanie flag — golden, egzekwowany ZANIM ktoś zaufa liczbie cienia do flip-justyfikacji.

**BRAMKA „ZERO NOWYCH KOPII":** przepięcie na kanon (−1 frozen), nie dopisanie 2. cienia. **Sekwencja:** NIE ruszać przed checkpointem 03.07 (świadomie zamrożony pod walidację — protokół C7 „G2b STOP = poprawna bramka, NIE bug").

---

### R1-D — `flag-state-3-layer-no-single-source` (P1, CONFIRMED źródło, OTWARTY, harm LATENT)

> Klasa J (cross-repo/multi-proces) ⊂ R1. To **fundament** — leczy też „kłamiący fingerprint" (E) i „instrument z pustym env" (`carried-first-guard-empty-env-void`).

**CO DZIŚ (entropia, świeżo zweryfikowane):**
- **Stan decyzyjny ≠ jeden plik:** 3 warstwy różne per-proces — (1) `flags.json` hot-reload (198 kluczy), (2) drop-iny systemd `Environment=` env-frozen, (3) stała modułu `common.py`/`plan_recheck.py`/`panel_client.py`. Precedencja `decision_flag()` = flags.json → stała → False (`common.py:348-361`).
- **`flag_fingerprint()` (`common.py:364`) widzi 63/≥90 flag** (ETAP4 59 + `_FINGERPRINT_EXTRA_FLAGS:322` 4). POZA fingerprintem: **23 route/canon env-frozen** (plan_recheck.py, `os.environ.get` at-import), `USE_V2_PARSER` (panel-watcher=V2 / shadow=V1), `OR_TOOLS_TSP`+`SAME_RESTAURANT_GROUPING` (sprzężone). → „fingerprinty identyczne = parytet" to **fałszywe zapewnienie** (klasa E, kłamiący przyrząd). Potwierdzone `/proc/<pid>/environ` shadow vs panel-watcher: REALNA divergencja env.
- **Conftest-leak D4 = 3× design:** 71 `ENABLE_*` + 41 bool POZA wszystkimi rejestrami (ETAP4/INFRA/NUMERIC/FINGERPRINT). ~25 DECYZYJNYCH (selekcja/scoring/feasibility/filtr floty) przeciekają prod-ON do testów „OFF".
- **Dead-but-ON:** `ENABLE_PANEL_IS_FREE_AUTHORITATIVE`, `ENABLE_TRANSPARENCY_SCORING` (0 konsumentów, inert).
- **1 inwersja maskująca:** `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` const env-default True (`common.py:2805`) maskowany flags.json=False → usunięcie klucza = cichy FLIP ON (utrata ALWAYS-PROPOSE). [klasa M+I, cross-ref R20]
- **Instrument-manifestacja (`carried-first-guard-empty-env-void`, CONFIRMED):** `tools/carried_first_guard.py` biega z PUSTYM env systemd → reużyte `plan_recheck._start_anchor`/`_apply_canon_order_invariants` czytają 14 route/canon flag jako default-OFF → `no_position` 87-88% (fikcja), claim „liczy IDENTYCZNIE jak silnik" = fałsz.

**STAN DOCELOWY (kontrakt ④ + ① + ③ + ⑤):**
1. **④ / ① JEDEN rejestr flag = kanon hot-reload** (styl ETAP4) obejmujący route/canon + solver (OR_TOOLS/GROUPING) + parser (USE_V2_PARSER) — koniec module-const-env-frozen dla flag DECYZYJNYCH. `copy-count` stanu-flagi-decyzyjnej = **1 źródło** (flags.json kanon, stała = jawny fallback OFF + inwariant testu). Migracja jak 10.06 (13 flag env→ETAP4).
2. **⑤ Fingerprint = WSZYSTKIE flagi decyzyjne** (nie 63/90). `void` (fałszywy-parytet) = **0**. Drop-in dodany do 1 serwisu a nie do bliźniaka MUSI być złapany porównaniem fingerprintów (np. `PLAN_SEQUENCE_LOCK` tylko plan-recheck dziś nie jest).
3. **④ Jeden punkt keyowania higieny:** conftest-strip + fingerprint + `flag_effect_coverage_check` + `flag_doc_baseline` keyowane z TEGO SAMEGO rejestru → flaga decyzyjna **automatycznie** objęta (koniec „łatka na 3 instancje" — `257d315` dodał 3 stałe, 62 survivors). `dead-flag = 0`, 100% w rejestrze.
4. **③ Instrumenty dziedziczą env silnika:** przyrząd reużywający funkcje silnika (carried_first_guard, …) MUSI mieć drop-in/jawny config = env silnika, nie pusty default. Leczy `carried-first-guard-empty-env-void` u źródła. Cross-repo „trust-canon" (3 systemy flag inaczej-nazwane: `ENABLE_BUILD_VIEW_TRUST_CANON_ORDER` / `PANEL_FLAG_TRUST_CANON_ORDER` / silnik=kanon) → wspólny rejestr lub jawna mapa parytetu.
5. Usunąć inwersję maskującą R20 (flagi fail-open/floor/exempt = const env-default ON jawnie + obecne w flags.json jako kanon; usunięcie klucza ≠ cichy flip).

**INWARIANT:** flaga decyzyjna ⇒ (a) w rejestrze, (b) w fingerprincie, (c) strippowana przez conftest, (d) jej efektywny stan per-proces = funkcja JEDNEGO źródła. Cross-proces fingerprint-parytet = REALNY (pokrywa wszystkie decyzyjne).

**BRAMKA „ZERO NOWYCH KOPII":** migracja env-frozen→rejestr USUWA warstwę (3→efektywnie 1 dla decyzyjnych), nie dodaje 4.; każda nowa flaga wchodzi przez rejestr (samo-zachowawcze).

---

## 2. PLAN KONSOLIDACJI (zależnościowo; każdy krok REDUKUJE ≥1 metrykę entropii; bramka „zero nowych kopii")

**Zasada anty-entropii:** konsoliduj-nie-dodawaj; każdy krok ściśle redukuje copy-count / twin-divergence / void-instrument / dead-flag / layer-violation / unresolved-conflict; strażnik-shadow MIERZY nawrót PRZED każdym flipem (lekcja: „nie deklaruj — udowodnij ON≠OFF + brak regresji + pozytywny wpływ"). Wszystko = P0 silnik/cross-repo → ETAP 0→7, off-peak po 14:00, ACK Adriana, replay ON↔OFF, parytet bliźniaków, pełna regresja `pytest tests/` vs baseline.

### FAZA 0 — FUNDAMENT: rejestr flag (R1-D) + strażniki-shadow (przed dotykaniem rdzenia)
> R1-D jest pierwszy, bo: (a) to keying-point, który czyni parytet/inwarianty R1-A i R1-B **wiarygodnymi** (inaczej golden-test biega na nieznanym stanie flag); (b) leczy „instrument z pustym env" → strażniki carried/floor będą mówić prawdę; (c) frozen-lexqual (R1-C) jest bramkowany stanem `POST_SHIFT` flagi.

- **0.1** Rozszerz `flag_fingerprint()` o route/canon (23) + USE_V2_PARSER + OR_TOOLS/GROUPING **albo** przenieś je do flags.json+ETAP4 (kanon hot-reload). *Redukuje: void-instrument (fałszywy-parytet) 1→0; dead-flag (rejestr) ↓.* Bramka: zero nowych flag poza rejestrem.
- **0.2** Keyuj conftest-strip + flag_effect + doc-baseline z rejestru ETAP4 (jeden punkt). Domknij `test_flag_doc_coverage::test_baseline_is_not_stale` (dziś CZERWONY = żywy dryf). *Redukuje: leak 62→0, dead-flag→0.*
- **0.3** Instrumenty reużywające funkcje silnika dostają drop-in = env silnika (carried_first_guard najpierw). *Redukuje: void-instrument (carried-guard) → validated.*
- **0.4** Usuń inwersję maskującą `COMMIT_DIVERGENCE_VERDICT_GATE` (const+json zgodne). *Redukuje: unresolved-conflict (silent-OFF) ↓.*
- **0.5** Zbuduj **strażnik floor-shadow** (`pickup ≥ shift_start`, wzór carried_first_guard) + **golden-equivalence harness route-order** w shadow — MIERZĄ nawrót, jeszcze nic nie zmieniają. *Przygotowuje dowód ON≠OFF dla Faz 1-2.*

### FAZA 1 — CZASOWO-KRYTYCZNE: parytet route-order (R1-A) PRZED 2026-07-10
> Monitor wygasa za 10 dni → sieć parytetu znika. Najpierw zabezpieczyć, potem konsolidować.

- **1.1** Zastąp wygasający `ziomek_time_route_monitor` (self-expiry `:386`) **golden-fixture equivalence test w CI obu repo** (`order_podjazdy(X) ≡ _build_route(X) ≡ courier_orders-route(X)` na wspólnym wejściu). *Redukuje: twin-divergence (mierzona, nie wygasająca); void/iluzoryczny-golden → realny.* Bramka: NIE dodawaj 2. monitora.
- **1.2** Usuń MARTWĄ `courier_api_panelsync/courier_orders.py` (5. kopia, 665 L). *Redukuje: copy-count 5→4, dead-code.*
- **1.3** `PICKUP_MERGE_MIN` → 1 nazwana stała (źródło importowane/golden-pinned). *Redukuje: copy-count progu 5→1.*
- **1.4** 2. producent `_save_plan_on_assign:436` woła `_apply_canon_order_invariants` (lub likwidacja na rzecz gwarantowanego recanon-on-write). *Redukuje: layer-violation (kanon bez inwariantów); domyka okno 5-min + placeholder (0,0).*
- **1.5** Fail-soft importu apki (`courier_orders.py:35-41`) → fail-LOUD. *Redukuje: silent-failure (M).*
- **1.6** (większy, opcja docelowa) Wyodrębnij wspólny pakiet route-order importowany przez 3 repa (źródło=silnik); to samo dla ETA-dostawy R1-A' (chain-ETA cross-repo / live_eta_cache autorytatywny). *Redukuje: copy-count 4→1.* Bramka: golden ON≠OFF + pełny e2e przez konsolę+apkę+Kotlin.

### FAZA 2 — NAJWYŻSZY HARM: floor odbioru (R1-B) — P0 engine
> User-reported (case Drapieżnik 484400), najinwazyjniejsze. Strażnik z 0.5 mierzy nawrót. Sekwencja per roadmapa L0-L6, najwyższy zwrot na nawrót = plan_recheck (L2).

- **2.1 (L2, najwyższy zwrot)** `plan_recheck._start_anchor:554` przestaje odclampowywać — floor `shift_start` w regenie `courier_plans.json`. *Redukuje: copy-count leak; zatrzymuje 5-min „cofacz".*
- **2.2 (L0)** JEDNO `courier.available_from = max(now, shift_start)` w `courier_resolver` + naprawa danych (GPS-przed-zmianą misclass, fail-open puste godziny). *Redukuje: copy-count 17→1 źródło.*
- **2.3 (L1, feasibility)** RULE-level „pre/no_gps nie wygrywa zlecenia którego nie zdąży" przez SPÓŹNIENIE na **load-aware buforze** (NIE surowy ETA — twist kalibracyjny), thin-fleet=wyjątek. Zgodne z Q2 (zmieniaj KTO). *Redukuje: unresolved-conflict (feasibility za-miękka).*
- **2.4 (chokepoint)** `state_machine` COURIER_ASSIGNED stosuje floor (`effective_pickup_at`, NIE nadpisuj deklaracji); jawna precedencja **frozen>floor>OSRM** w jednym miejscu → rozstrzyga R19 frozen↔floor. *Redukuje: layer-violation, unresolved-conflict (R19).*
- **2.5 (R28)** Jedna polityka fail grafiku (fail-loud, nie cichy 24/7) — `is_on_shift`/`_shift_start_dt`/FAIL12 spójne. *Redukuje: unresolved-conflict (R28 3-way).*
- **2.6 (L3)** Pas renderów (apka `_attach_fallback_eta`, restauracja `adapter`, telegram-legacy, konsola) na sclampowanym planie z L2 → **USUŃ render-clampy** (`CLAMP_PRESHIFT_PICKUP_ETA` etc.). *Redukuje: layer-violation 4-floory→0-łatek; copy-count.*
- **2.7 (L6)** RUNTIME INWARIANT + strażnik + test: „żaden pickup < shift_start przypisanego kuriera"; sprzężony BUG#2 guard haversine `(0,0)` u źródła ingestu. *Redukuje: nawrót→0 (jedyne co blokuje na zawsze).*
- **2.8** `shift_start` konsoli importuje/odczytuje źródło silnika (nie własny HH:MM fetch). *Redukuje: copy-count definicji shift_start 2→1.*

### FAZA 3 — RESZTKA BRAMKOWANA: frozen-lexqual (R1-C) — PO checkpoincie 03.07
- **3.1** Po PASS at#152/at-200 (03.07): golden-test `shadow_lex_qual ≡ canon` przy OBU stanach `POST_SHIFT`. *Redukuje: void/untested→0.*
- **3.2** Przepnij `_objm_lexr6_shadow._lex_qual:1122` na `objm_lexr6.lex_qual` (lub usuń cień); ujednolić `post_shift_overrun_penalty` 3-sposoby. *Redukuje: copy-count klucza-jakości →0 frozen (ostatnia inline).*
- Bramka: NIE ruszać przed 03.07 (świadomie zamrożony — protokół C7).

### Sekwencja zależności (skrót)
```
FAZA 0 (rejestr flag + strażniki)  ──┬──> FAZA 1 (route-order parytet, T-10 dni)
   [czyni parytet/inwarianty       │
    wiarygodnymi; leczy env-void]   ├──> FAZA 2 (floor odbioru, P0 engine)
                                    │
                                    └──> FAZA 3 (frozen-lexqual, po 03.07)
```
FAZA 0 PRZED wszystkim (fundament wiarygodności). FAZA 1 priorytet czasowy (monitor T-10). FAZA 2 najwyższy harm. FAZA 3 bramkowana zewnętrznie.

---

## 3. DASHBOARD ENTROPII — rodzina R1 (DZIŚ → CEL)

| Metryka | Root | DZIŚ (zmierzone) | CEL |
|---|---|---|---|
| copy-count kolejności-jazdy | R1-A | 5 żywych + bundling×4 + 1 dead | **1** (lub 1+golden-bound) |
| copy-count ETA-dostawy | R1-A' | 3-4 impl. | **1** (lub cache autorytatywny) |
| copy-count `PICKUP_MERGE_MIN` | R1-A | 5 literałów | **1** stała |
| twin-divergence route-order | R1-A | 44-75/d (monitor **wygasa 07-10**) | **0** (golden CI, nie wygasa) |
| copy-count „najwcześniejszy-odbiór" | R1-B | 17 powierzchni, 0 single-source | **1** `available_from` |
| floory shift_start / render-clampy | R1-B | 4/17 floory (1 to render-łatka) | 1 chokepoint, **0 łatek** |
| copy-count `shift_start` def | R1-B | 2 (silnik datetime / konsola HH:MM) | **1** |
| runtime-inwariant pickup≥shift_start | R1-B | **0** (grep=∅) | **1** strażnik fail-loud |
| copy-count klucza-jakości (frozen inline) | R1-C | 1 frozen `_lex_qual` (engine UNIFIED) | **0** (po 03.07) |
| flag-single-source (warstwy) | R1-D | 3 warstwy, per-proces | **1** rejestr (decyzyjne) |
| fingerprint pokrycie | R1-D | 63/≥90 (fałszywy parytet) | **wszystkie decyzyjne** |
| conftest-leak (flagi poza rejestrem) | R1-D | 71 ENABLE_* + 41 bool | **0** decyzyjnych |
| dead-but-ON flagi | R1-D | 2 (PANEL_IS_FREE, TRANSPARENCY_SCORING) | **0** |
| void-instrument (env-void) | R1-D/guard | carried_first_guard (pusty env) + monitor wygasa | **validated** |
| inwersja maskująca | R1-D | 1 (COMMIT_DIVERGENCE_GATE) | **0** |

**Reguła zdrowia (samo-zachowawcza):** żaden przyszły sprint nie może pogorszyć żadnej z tych liczb (rozszerzenie Przykazania #0 o checki anty-wzorców R1: „nowa powierzchnia renderu kolejności/ETA = RED; nowa flaga decyzyjna poza rejestrem = RED; nowe re-liczenie czasu-odbioru bez konsumpcji `available_from` = RED").

---

## 4. CROSS-REF / GRANICE / OTWARTE PYTANIA DO ADRIANA

**Sprzężenia z innymi rodzinami (rusza RAZEM lub gateuje):**
- R1-B (floor) ↔ **R7 koherencja** R19 (frozen↔floor), R28 (schedule 3-way), R16 (R6-cap 35/40) — precedencja anchor/clamp rozstrzygana TAM, mechanizm jednego-źródła TU.
- R1-A (route) ↔ **R6 lifecycle** (dead panelsync, monitor wygasa) + **R5 stres/M** (fail-soft import, placeholder (0,0)).
- R1-C (frozen-lex) ↔ **R3 prawda** (`objm-shadow-canary-twins-alltick` R3 dedup_2: peak_verdict all-tick zawyżka) — wspólny POST_SHIFT flip, ruszać razem.
- R1-D (flag) ↔ **R3 prawda** (`carried-first-guard-empty-env-void`, `serializer-allowlist`, fingerprint-lie) + **R7** (R20 silent-OFF, R22 governor flag-drift).
- A1-rodzeństwo geometrii/SLA-anchor (stałe 8.0×2, R6=35×5, (sla_viol,dur)×3) = realne kopie, ale rozstrzygnięcie = R2/R7 — TU tylko bramka „1 nazwana stała per pojęcie".

**OTWARTE PYTANIA (priorytet/inwersje — PYTAJ, nie zgaduj):**
1. **R1-A docelowy:** wspólny importowany pakiet route-order cross-repo (większy refaktor, ryzyko cross-venv/Kotlin) **vs** golden-fixture equivalence (mniejszy, utrzymuje 3 kopie ale wiąże je testem)? Rekomendacja-DRAFT: golden-fixture jako MINIMUM przed 07-10, wspólny pakiet jako cel docelowy (Faza 1.6).
2. **R1-B `effective_pickup_at`:** osobne pole (NIE nadpisuje deklaracji restauracji) — potwierdź że to zgodne z Q2/frozen (deklaracja nietykalna). Wstępnie wbudowane w target jako zgodne.
3. **R1-D migracja env→ETAP4:** czy route/canon 23-flagi mogą stać się hot-reload (jak 13 flag 10.06), czy któreś MUSZĄ zostać env-frozen (restart-gated) z powodów bezpieczeństwa?
4. **Kolejność Faza 1 vs Faza 2:** monitor route-order wygasa 07-10 (T-10) — czy Faza 1.1 (golden CI) idzie PRZED Fazą 2 (floor, większy harm ale brak deadline)? Rekomendacja-DRAFT: TAK (1.1 jest tania i czasowo-krytyczna).

---

## 5. POKRYCIE / CO NIE ROZSTRZYGNIĘTE (jawnie, nie cisza)

- **Wartości runtime parytetu NIE udowodnione** (że `order_podjazdy ≡ _build_route` bajtowo; że frozen `_lex_qual ≡ kanon` przy OBU POST_SHIFT; magnituda dzisiejszego rozjazdu route-order z `ziomek_time_route_monitor.jsonl`) — to **Faza C oracle / PoC**, nie ten dokument syntezy (read-only).
- **R1-A' (one-delivery-eta-source)** NIE był persisted-rootem — trzymam jako bliźniaczy sub-target R1-A (ta sama przyczyna), ale jego osobna „source-ness" wymagałaby re-weryfikacji jeśli Adrian chce go jako distinct.
- **frozen-lexqual source=NIE** — celowo target=„dokończ unifikację po walidacji", nie „posprzątaj chaos"; jeśli checkpoint 03.07 da WAIT, R1-C zostaje zamrożony (NIE forsować).
- **Numery linii dryfują** (≥3 sesje/dzień na repo) — zweryfikowane DZIŚ HEAD `8024705`, ale PoC/zmiana MUSI re-grepować (Przykazanie #0 ETAP 0).
- **PoC = osobny ACK** — ten dokument NIE wybiera/nie pisze PoC; kandydaci z designu: „one route-order module" (R1-A, czasowo-krytyczny) lub „one selection key" (już ~UNIFIED, R1-C resztka). Rekomendacja-DRAFT PoC: **R1-A golden-fixture equivalence** (najwyższa dźwignia × deadline 07-10 × niskie ryzyko — test, nie zmiana zachowania).
