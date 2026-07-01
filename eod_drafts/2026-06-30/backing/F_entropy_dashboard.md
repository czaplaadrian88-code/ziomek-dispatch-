# F — DASHBOARD ENTROPII ZIOMKA (liczby DZIŚ → cel)

**Faza F / audyt spójności · sesja tmux 2 · 2026-06-30 · TRYB READ-ONLY** (zero edycji silnika/restartów/flipów/git/--notify).
**HEAD silnika:** `8024705` (`git log -1` = 2026-06-30 10:23 UTC). **Ledger master:** `/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl` (mtime **2026-06-30T20:02 FRESH**).
**Wejście:** backing A3 (flagi efektywne) + A4/FAZA1_03 (przyrządy) + A6 (graf bliźniaków) + E_dedup_1/2/3 (anty-double-count) + FAZA1_02 (mapa konfliktów) + B17 (rozsyp progów) + 26 przetrwałych rootów (Faza F dashboard) — zdedupowane, policzone DZIŚ, część zweryfikowana świeżym grepem (sekcja §11).
**Po co:** to **stały miernik zdrowia** — 8 liczb entropii spójności. Każda mierzona TYM SAMYM sposobem co tu → re-run pokazuje progres. Wszystkie cele = **0 lub 1** (kontrakty stanu docelowego DESIGN §4).

---

## 0. TABELA GŁÓWNA — 8 METRYK ENTROPII (to jest miernik)

| # | METRYKA | DZIŚ | CEL | źródło-liczby |
|---|---|---|---|---|
| 1 | **copy-count** (reguł z >1 kopią / źródłem) | **17 reguł** (≈90 instancji) | **0 reguł z >1 źródłem** (1 kanon każda) | A6 7 grup + E_dedup_1/3 + B17 N1-N10 (§1) |
| 2 | **twin-divergence-count** (bliźniaki DIVERGED/FRAGILE) | **5 grup-kopii** + ~8 przyrząd/flaga/pole = **~13** | **0** (twin-divergence=0, kontrakt #3) | A6 STRESZCZENIE (5/7 grup) + E_dedup (§2) |
| 3 | **void-instrument-count** (przyrząd kłamie/proxy) | **19 VOID + 6 UNTESTED** = 25 / 49 werdyktów (→ 11 rootów) | **0** (void=0 przed flipem, kontrakt #5) | FAZA1_03 podsumowanie + E_dedup_2 §A + C18 (§3) |
| 4 | **dead-flag-count** (flaga ON/declared, 0 konsumentów) | **5** potwierdzonych | **0** (prawda-flag dead=0, kontrakt #4) | A3 §8 + E_dedup_2 R14 + E_dedup_3 R6c-1 + grep §11 (§4) |
| 5 | **layer-violation-count** (HARD/pula w złej warstwie) | **7 instancji** (2 rooty) | **0** (HARD-przed-SOFT, kontrakt #2) | E_dedup_1 ROOT9 + E_dedup_2 R24 + FAZA1_02 K-G/K-H (§5) |
| 6 | **unresolved-conflict-count** (precedencja nierozstrzygnięta) | **13 klastrów** (raw 64 pary) | **0** (koherencja, kontrakt #8) | FAZA1_02 (81 par / 64 problem. / 13 klastrów) (§6) |
| 7 | **sentinel-as-data-count** (sentinel jako realna dana) | **6 definicji** + **0/1 chokepoint** + ~12 sites; LIVE 2046+14456 zdarzeń/8 ofiar | **1 walidator u ingest, 0 sentinel-jako-dana** | E_dedup_3 F1 + B15/B16 + grep §11 (§7) |
| 8 | **threshold-sprawl-count** (próg w N miejscach) | **10 rodzin** (≈40 sites, 3 ścieżki override) | **0 rodzin rozsypanych** (1 stała + 1 override/pojęcie) | B17 N1-N10 + META + E_dedup_2 R25/R16/R26 (§8) |

**Czytanie miernika:** wszystkie 8 są DZIŚ > cel → system w stanie wysokiej entropii spójności. **Żadna metryka NIE jest 0/1.** Najgorętsze (P1, fizycznie żywe DZIŚ): #7 sentinel (2046+14456 zdarzeń, 8 ofiar 30.06), #3 void (25/49 przyrządów-prawdy niewiarygodnych), #6 konflikt (64 pary). Pełen rozkład + dowód niżej.

---

## 1. COPY-COUNT — reguły z więcej niż jednym źródłem

**Definicja:** ta sama REGUŁA/wartość zaimplementowana w >1 miejscu bez wspólnego importu (importer dzielący kanon NIE liczy się jako osobna kopia logiki — liczę OTWARTE kopie). **Cel = 1 kanon/regułę.**

| Reguła / wartość | kopie DZIŚ | otwarte (nie-unified) | root | cel |
|---|---|---|---|---|
| **Kolejność trasy / kanon** | 5 (1 engine-choke + route_podjazdy + fleet_state + courier_api + panelsync-DEAD) / 3 repa | 4 żywe, brak importu repo↔repo | ROOT1 (R1/J) | 1 pakiet wspólny |
| **Selekcja `lex_qual`** | 1 kanon + 5 importerów + **1 FROZEN inline** | 1 frozen (`_objm_lexr6_shadow._lex_qual`) | ROOT3 (K1 resztka a) | 1 (przepiąć po at-200) |
| **Bucket pozycji** | 1 kanon `_selection_bucket` + 7 engine-twins (UNIFIED) + **≥4 out-of-engine** | 4-5 (reassignment/a2/auto_assign-G7/feed.py) | ROOT4 (K1 resztka b) | 1 (związać gates) |
| **SLA/R6 anchor** | 4 komputacje kotwicy (route_simulator `_count_sla` + feasibility SLA-loop + r6_thermal + sla_tracker; +bundle_calib min()) | 2 inline-lustra + anchor≠R6-anchor | ROOT5/R15 (K1+K3) | 1 `r6_thermal_anchor` |
| **Najwcześniejszy odbiór (floor)** | **17 powierzchni** (4 floor / 13 brak / **0 `available_from`**) | 13 bez floor, 0 inwariantu | ROOT6 (K1+K2+K4) | 1 `available_from` |
| **ETA dostawy** | 3-4 niezależne impl. (chain_eta / apka / konsola / canon_eta) | 3-4 | ROOT2 (K1) | 1 chain-eta cross-repo |
| **R6 cap 35/40** | 6 sites (35 bare / 40 hot / 35 O2×2 / 35 bundle / p80) | 6 niezsynchr. | R16/N1 | 1 `r6_cap_for_tier()` |
| **czasówka = 60** | 6 sites (1 hot `EARLY_BIRD` / 5 bare) | 6 (1 hot ↔ 5 frozen) | R26/N2 | 1 `czasowka_threshold()` |
| **R27 ±5** | 5 stałych (committed-tol / late-pickup-hard / -soft / V3274-frozen) | 5 mieszany bare/env | R25/N4 | 1 stała |
| **pre-shift floor 30/20/60** | 5+ (dwa różne „30") | 5+ | N3 | 1 |
| **bag-cap-per-tier** | 2 macierze (BUG4 std4/slow3 vs HARD_TIER std5/slow4) | 2 rozbieżne | N5 | 1 tabela |
| **margin = 15** | 5 (3× flags.json + 2× hardcode) | 5 | R25/N9 | 1 `SCORE_MARGIN_CONFIDENT` |
| **8 km deliv-spread** | 2-3 (R1 bare + BUNDLE env + R3) | 2-3 | N10 | 1 `MAX_DELIV_SPREAD` |
| **DWELL fallback** | 2 (common ↔ route_simulator 1.0/3.5) | 2 (dziś zgodne) | N7 | 1 |
| **„is coord sentinel" def** | **6 niespójnych** (common:513 kompletny vs exact-(0,0) vs lat-alone vs range vs centroid-tol) | 6 | F1/B16-M06 | 1 walidator |
| **bearing (geometria)** | 2 (`wave_scoring:242` ↔ `geometry:30`) | 2 | ROOT7 | 1 |
| **`_gini`** | 2 (`sequential_replay:663` ↔ `daily_rule_report:21`) | 2 | C18 §3D | 1 util |

**TOTAL: 17 reguł/wartości z >1 źródłem** (≈90 surowych instancji). **CEL = 0 reguł z multiplikowanym źródłem.** Flagowy dowód „kopii": grep `available_from` = **0** (single-source floor NIE istnieje); route-order monitor 44-75 rozjazdów/dzień.

---

## 2. TWIN-DIVERGENCE-COUNT — bliźniaki już rozjechane / kruche

**Definicja:** zbiory-kopii w stanie DIVERGED (rozjechane) lub FRAGILE (zgodne dziś, następny term rozjedzie) — BEZ gwarancji parytetu. **Cel = 0** (twin-divergence=0, kontrakt #3 „parytet-bliźniaków").

**A6 — 7 grup-kopii, stan parytetu DZIŚ:**

| Grupa | Stan | Liczy się? |
|---|---|---|
| 2 Kolejność trasy | **DIVERGED** (twin #11, **44-75 rozjazdów/dzień** żywy monitor) | ✅ |
| 1 `lex_qual` | **FRAGILE** (shadow 3-krotka vs kanon 3/4-krotka; flip POST_SHIFT rozjedzie) | ✅ |
| 3 Bucket pozycji | engine UNIFIED, **gates DIVERGED** (4 out-of-engine) | ✅ |
| 4 SLA-anchor | **DIVERGED + FRAGILE** (pickup_at vs ready-anchor) | ✅ |
| 6 Floor odbioru | **DIVERGED by-construction** (13/17 bez floor) | ✅ |
| 5 `_bucket` inline | **UNIFIED** (28-29.06, zamknięte) | ✗ |
| 7 `eta_pickup` | DRYF SEMANTYKI (1 pole 2 role — NIE rozjazd-kopii) | ✗ (→ metryka semantyki) |

→ **5 z 7 grup-kopii DIVERGED/FRAGILE.**

**+ bliźniaki przyrząd / flaga / pole (rozjechane, poza grupami A6):**
- objm `peak_verdict` ALL-TICK vs `monitor` per-decyzja (×7-11 zawyżka, R3)
- naive-TZ Warsaw-parser vs UTC-parser (L1; **checkpoint_tz = DOWÓD nawrotu**, +120min exact)
- `_shift_start_dt` (brak północy) vs `_shift_end_dt` (ma +1) — L3
- `delivery_coords` vs `delivery_address` async-write split-brain (S2)
- bag-cap DWIE macierze (N5)
- `CARRIED_FIRST_RELAX` shadow OFF vs plan-recheck/watcher ON (env per-proces)
- `USE_V2_PARSER` panel-watcher=V2 vs shadow=V1 (PLAUSIBLE, cross-proces)
- `PLAN_SEQUENCE_LOCK` tylko plan-recheck, BRAK w watcher (env-twin asymetria)

**TWIN-DIVERGENCE-COUNT DZIŚ: 5 grup-kopii + ~8 przyrząd/flaga/pole = ~13 rozjechanych zbiorów-bliźniaków.** **CEL = 0.** Najmocniejszy żywy dowód: route-order 44-75/d (monitor `ziomek_time_route_monitor`, ⚠ SAM WYGASA 2026-07-10 = parytet zniknie). Mechanizm parytetu DZIŚ: golden-test (engine) lub runtime-monitor (cross-repo) — **brak wspólnego importu repo↔repo dla żadnej z 5 grup.**

---

## 3. VOID-INSTRUMENT-COUNT — przyrządy które kłamią / mierzą proxy

**Definicja:** przyrząd-werdykt (napędza flip/no-flip) którego liczba NIE jest dowodem (mierzy predykcję zamiast outcome, martwe pole, zła/nieświeża próbka, zły obiektyw inwariantu). **Cel = 0** (void=0 przed flipem, kontrakt #5).

**FAZA1_03 podsumowanie: 24 VALIDATED · 19 VOID · 6 UNTESTED (z 49 werdyktów).**
→ **VOID-INSTRUMENT-COUNT DZIŚ = 19 VOID (+ 6 UNTESTED) = 25 / 49 przyrządów-prawdy niewiarygodnych.** Po dedupie (E_dedup_2 §A) → **11 instrument-truth rootów** (feas_carry×3→1; reassign-ghost+a2+best_effort+wiring 4→1; objm peak+G2b+shadow 3→1; conftest 2→1; `_append_jsonl`-swallow ≥8→1; stale-.txt ≥6→1; verdict-source-trap 3→1).

**Reprezentatywne VOID (root → czym kłamie):**
| Przyrząd / root | czym kłamie | flip który bramkuje |
|---|---|---|
| feas_carry (×3: replay/postflip/blind) | benefit z PREDYKCJI, 0 joinu delivered_at; sentinel ~10000min dominuje regret | `ENABLE_FEAS_CARRY_READMIT` (napędził flip ON 27.06 → rollback) |
| reassignment_forward_shadow | 59% fałszywych „ratunków"; `_SYNTH_POS` niezrównany z silnikiem | autonomia przerzutu |
| best_effort_fastest_pickup_shadow | `getattr(best,"pos_source")` = **None×81/81** (blind-check martwy) | flip selekcji „najszybszy odbiór" |
| objm_lexr6_peak_verdict | headline ALL-TICK ×7-11 (monitor per-decyzja naprawiony, peak NIE) | objm peak verdict (at-200 03.07) |
| bug4_reseq_verdict | inwariant `delta>=0` zły obiektyw; własny gate pada 11.5% | flip reseq |
| reassign_global_select_review | certyfikuje LICZBĘ de-pile, ŚLEPY na geometrię (35% worków spread>8km) | `PENDING_RESWEEP_LIVE` |
| carried_first_guard | PUSTY env → 14 flag default-OFF; 90% rekordów fikcyjne `no_position` | siatka „carried-first wrócił?" |
| serializer `_AUTO_PROP_PREFIXES` | **38 kluczy ginie** z ledgera (eta_source, r6_gold4_gate, sla-detail, V328) | każdy flip czytający te metryki |
| min_delivered_at_verdict | ROTATION-BLIND (czyta tylko żywy plik) → fałszywe „INCONCLUSIVE" | min-delivered obiektyw |
| b_route_shadow_review | czyta ZAMROŻONY `dispatch_state/sla_log` (20.06) → real_joined=0 | b-route / B-lite |
| c5_shadow_log | 100% test-pollution (producent DEAD Z-22) | wave_scoring |

**Żywy dowód DZIŚ (grep ledgera §11):** `eta_source` = **0/2000** (R8 vanish POTWIERDZONY), `r6_gold4_gate_recovered` = **0/2000** (G-2 niemierzalny POTWIERDZONY). Kontr-dowód że nie wszystko void: `post_shift_overrun_min` = **457/2000** (C18: void-claim OBALONY → VALIDATED), `would_hard_cap` = **438/2000** (d23d8a1 fix LIVE). **CEL = 0** (każdy przyrząd join z `gps_delivery_truth.jsonl`/`decision_outcomes.jsonl` PRZED użyciem werdyktu do flipu).

---

## 4. DEAD-FLAG-COUNT — flaga ON/zadeklarowana, 0 konsumentów

**Definicja:** flaga effective-ON lub declared, ale 0 żywych konsumentów (martwa-ale-ON) ALBO martwy klucz config. **Cel = 0** (prawda-flag dead=0, kontrakt #4).

| Flaga / klucz | stan | konsumenci (grep §11) | klasa |
|---|---|---|---|
| `ENABLE_PANEL_IS_FREE_AUTHORITATIVE` | env-default ON | **0** (poza def) | dead-but-ON |
| `ENABLE_TRANSPARENCY_SCORING` | True | **0 realnych** (1 = sam def) | dead-but-ON |
| `ENABLE_CLUSTER_DROP_GROUPING_METRIC` | declared | declared-not-wired | dead-declared |
| `A4_TEST_FLAG` | flags.json | test-leak do prod (2 = test refs) | dead config key |
| `commitment_level` | bool | tylko martwy emitter (typ-mismatch) | dead config key |

**DEAD-FLAG-COUNT DZIŚ = 5 potwierdzonych** (dead-but-ON / declared-not-wired / martwy klucz). **CEL = 0.**

⚠ **Caveaty (jawnie):**
- A3 §8 deklaruje „**0 potwierdzonych martwych**" — ale to wąski sweep „DEAD-niepodpięta gałąź"; **pełny sweep flaga→callsite ODROCZONY** (osobny OS klasy K). 5 powyżej z dedupu (E_dedup_2 R14 + E_dedup_3 R6c-1), nie z A3.
- `ENABLE_BEST_EFFORT_OBJM_R6_KEY` (hist. „martwa #1") = **ŻYWA** (`dispatch_pipeline:6771`) — NIE liczona.
- **Sąsiednia metryka flag-drift (NIE dead, ale poza single-source):** **112 flag POZA wszystkimi rejestrami** (71 ENABLE_* + 41 bool, A3 §3) + **1 inwersja maskująca** (`COMMIT_DIVERGENCE_VERDICT_GATE` const=True maskowany flags.json=False) + fingerprint 63/≥90. Cel tej osi = 1 rejestr (ROOT11/R14). Trzymam ją osobno od dead-flag (dead≠poza-rejestrem).

---

## 5. LAYER-VIOLATION-COUNT — HARD-decyzja / przynależność-do-puli w złej warstwie

**Definicja:** logika HARD (reject / pula) żyje w warstwie SOFT (L6 score) lub obchodzi guard P0. **Cel = 0** (HARD-przed-SOFT, kontrakt #2 — HARD=L5 feasibility / pula=L4).

| # | Naruszenie | plik:linia | dziś | root |
|---|---|---|---|---|
| 1 | R9>20 HARD-reject liczony w L6 score → verdict-override MAYBE→NO | `dispatch_pipeline:5637` (`scoring:150`) | SAFE (monotonic, D01 „[ok]") | ROOT9 |
| 2 | v324a-ext / carry_chain / intra-gap HARD w L6 | `dispatch_pipeline:5637` | SAFE-by-construction | ROOT9 |
| 3 | `FEAS_CARRY_READMIT` promuje verdict=NO→MAYBE na top[0] ZA guardem | `dispatch_pipeline:6266` | **latentna mina** (flaga OFF) | ROOT9/R24 |
| 4 | soon-free busy→free look-ahead w L6 score, NIE L4 pula | `dispatch_pipeline:3620` | wrong-layer | ROOT9 |
| 5 | `R_RETURN_TO_RESTAURANT_VETO` metric-only feasibility, realny zakaz w L9 plan_recheck | `feasibility_v2:905` + `plan_recheck:1518` | split-enforcement | ROOT9/R21 |
| 6 | geometria SOFT-only w L6, **ZERO osi w HARD-feasibility i w `lex_qual`** | `feasibility_v2:504` + `objm_lexr6:29` | oś martwa pod scarcity | ROOT7 (P0-A) |
| 7 | `_assert_feasibility_first` JEDNORAZOWY @5938, łańcuch mutacji do :6301 NIE re-assert; `geometry_blind_fallback` KOORD bez always-propose checka | `dispatch_pipeline:5938/6453` | guard ślepy poza call-site | R24 |

**LAYER-VIOLATION-COUNT DZIŚ = 7 instancji → 2 rooty** (ROOT9 hard-feasibility-split-layer + R24 feas-first-guard-blind). **CEL = 0.** ⚠ Większość DZIŚ SAFE (monotonic / flaga OFF) = **latentne miny na flipie** — kontrakt #2 mówi: HARD w warstwie HARD niezależnie od „dziś bezpiecznie".

---

## 6. UNRESOLVED-CONFLICT-COUNT — precedencja nierozstrzygnięta / niespójna

**Definicja:** dwie reguły/flagi walczą, a precedencja jest undefined (kto wygrywa = nie wiadomo) LUB defined-inconsistent (różne progi/kotwice/warstwy) LUB silent-inversion (zachowanie cicho odwrócone). **Cel = 0** (koherencja, kontrakt #8).

**FAZA1_02: 81 par konfliktowych · 64 problematyczne** = **35 defined-inconsistent + 15 silent-inversion + 14 undefined** (17 zdrowych). Po dedupie → **13 klastrów (K-A..K-M):**

| Klaster | natura | klasa |
|---|---|---|
| K-A R6 dwie kotwice (thermal vs SLA) | undefined/inconsistent | A1·I·N |
| K-B R6 próg 35 vs 40 (6 stałych) | defined-inconsistent | N·I |
| K-C `ETA_QUANTILE_R6_BAGCAP` luzuje HARD-R6 | inwersja HARD↔SOFT | G·I |
| K-D pre-shift floor: feasibility clamp vs plan_recheck regen (K2 cofacz) | defined-inconsistent | B·C·H |
| K-E equal-treatment (engine) vs out-of-engine gates | sprzeczność+asymetria | B·I |
| K-F frozen-R27 broni złego czasu vs pre-shift floor | silent-inversion | F·I |
| K-G `_assert_feasibility_first` vs `FEAS_CARRY_READMIT` bypass | silent-inversion (#10) | C·I |
| K-H geometria SOFT-only vs `lex_qual` czysto-czasowy | inwersja+undefined | C·I |
| K-I `COMMIT_DIVERGENCE_VERDICT_GATE` const=True maskowany | silent-inversion | D·I |
| K-J R-DECLARED-TIME HARD bez runtime-inwariantu | undefined | I |
| K-K podwójny load (`FLEET_LOAD_BALANCE` vs `LOADGOV`) | inconsistent+sprzężenie | I·N |
| K-L nazwa-HARD vs zachowanie-SOFT (VETO/HARD_GATE misnomers) | inconsistent (mylące) | L·I |
| K-M kanon reguł SAM ze sobą sprzeczny (§4:86 vs §7:151) | sprzeczność wewnątrz-dokumentu | I |

**UNRESOLVED-CONFLICT-COUNT DZIŚ = 13 klastrów (raw 64 pary).** **CEL = 0.** Najgroźniejsze: **15 silent-inversion** (reset flagi / re-enable = regres BEZ zmiany kodu) + **14 undefined** (zwycięzca nieznany). 6 klastrów (K-A/B/D/E/F/H) zbiega się do TYCH SAMYCH korzeni co rodziny alokacji/pre-shift → naprawa fundamentu rozbraja większość.

---

## 7. SENTINEL-AS-DATA-COUNT — sentinel traktowany jako realna dana

**Definicja:** wartość-zaślepka `(0,0)`/`BIALYSTOK_CENTER`/`9999`/`~10000min`/`_SYNTH_POS` wpływa na decyzję jak realna dana (kurczy pulę, czyni worek tańszym, klasyfikuje pozycję). **Cel = 1 walidator u ingest, 0 sentinel-jako-dana downstream** (kontrakt: jeden chokepoint).

**1 root: `coord-sentinel-no-ingest-chokepoint` (F1, K5).** Składowe DZIŚ:
- **0/1 chokepoint u ingest** — istnieje JEDEN kompletny walidator `common.coords_in_bialystok_bbox:513` (odrzuca None/NaN/(0,0)/poza-bbox), ale **NIGDZIE u granicy INGEST** (`gps_server:328` range-check (0,0) PRZECHODZI).
- **6 niespójnych definicji „czy coord sentinel"** (common:513 kompletny vs exact-(0,0) vs lat-alone vs range vs centroid-tol) — cel 1.
- **~12 sites sentinel-jako-dana** (B16 M1-M12): produkcja `dispatch_pipeline:3133/3470` (`or (0,0)`) + persist placeholder `panel_watcher:474/486/496` (`{lat:0,lng:0}` → courier_plans, **live 11/79 stopów**) + konsumpcja-raise `:4823` (wave_veto truthy-guard nie łapie (0,0)) + `:2149` (repo_cost (0,0)→worek TAŃSZY) + catch-all `:5695` (cichy drop ZAJĘTEGO kuriera) + osrm sentinel `9999min` + `_SYNTH_POS` klasyfikator + `~10000min` objm dominuje regret feas-carry.
- **Mosty:** zasila position-twiny (A6 gr.3b), floor (BIALYSTOK fiction), P0-A (selekcja optymistyczna bo repo-km znika), P0-B (pula kurczy się bo couriers znikają).

**ŻYWY DOWÓD DZIŚ (B15, logi):** **2046× `V328_CP_SOLVER_FAIL` + 14456× `COORD_GUARD`**; **8 distinct ofiar 30.06** (cid=179×5, cid=492 Jakub W×3, sygnatura `ll1=(0,0)` `:4823`). **Brak alertu/KOORD** — jedyny ślad = ERROR w logu.

**SENTINEL-AS-DATA-COUNT DZIŚ: 6 niespójnych definicji · 0/1 walidator u ingest · ~12 sites · 2046+14456 zdarzeń/8 ofiar.** **CEL: 1 walidator u KAŻDEGO ingest, 0 sentinel-jako-dana downstream, truthy-guard `if coords:`→`_valid(coords)` we wszystkich callerach RAZEM.**

---

## 8. THRESHOLD-SPRAWL-COUNT — ten sam próg w N miejscach

**Definicja:** ta sama liczba progowa w N miejscach z RÓŻNYMI wartościami lub RÓŻNĄ ścieżką override (bare / env / flags.json-hot). **Cel = 1 nazwana stała + 1 ścieżka override / pojęcie.**

**B17: 10 rodzin (N1-N10) + META:**

| Rodzina | # sites | wartości | override | sev |
|---|---|---|---|---|
| N1 R6 cap ≤35/40 | **6** | 35/40/35/35/35/p80 | bare+env+flags.json-hot+instr | **P1** |
| N2 czasówka=60 | **6** | 60×6 | 1 hot + 5 bare | **P1** |
| N3 pre-shift floor 30/20/60 | 5+ | 30/30/60/-20/3.5 | bare+env | P2 |
| N4 committed ±5 (R27) | 5 | 5/5/5/5/10 | bare+env | P2 |
| N5 bag-cap-per-tier | 3 (2 macierze) | std 4vs5, slow 3vs4 | env+env+flaga | P2 |
| N6 dropoff-after-shift 5 | 2 (1 MARTWA) | 5/5 | bare+bare | P2 |
| N7 DWELL fallback | 2 | 1.0/3.5 ×2 | bare+bare | P3 |
| N8 wait docstring↔const | 1+doc | doc 5/6/20/-5 ≠ const 3/-/15/-8 | — | P2 (lying-doc) |
| N9 margin=15 | 5 | 15×5 | flags.json+hardcode | P3 |
| N10 8km spread | 2-3 | 8.0×3 | bare+env | P3 |

**THRESHOLD-SPRAWL-COUNT DZIŚ = 10 rodzin** (≈40 sites razem), **3 niespójne ścieżki override** (bare / env / flags.json-hot — `FLAGS_JSON_NUMERIC_OVERRIDES` pokrywa wybiórczo). Po dedupie → 3 rooty (K1 rozsyp-wartości + E/L lying-doc N8 + D override-path META). **CEL = 0 rodzin rozsypanych.** ⭐ P1: **N1** (baza HARD R6=35 BARE/zamrożona, wyjątek 40 HOT → rozjazd magnitude rośnie cicho) + **N2** (czasówka=60 hot tylko w 1/6 → bump `EARLY_BIRD`→45 zostawia zlecenia [45,60) wiszące w KOORD bez ścieżki czasówki).

---

## 9. MAPA: 26 PRZETRWAŁYCH ROOTÓW → 8 METRYK ENTROPII

Każdy root zasila ≥1 metrykę (anty-double-count: root liczony raz na metrykę-primary):

| Metryka | rooty (primary) |
|---|---|
| copy-count | one-route-order-module · earliest-pickup-floor · frozen-lexqual-shadow · numeric-threshold-scatter · r6-cap-35-vs-40 |
| twin-divergence | one-route-order-module · objm-shadow-canary-twins-alltick · frozen-lexqual-shadow · (floor, sla-anchor) |
| void-instrument | feas-carry-instruments-predict · objm-canary-alltick · bug4-reseq-misspec · carried-first-guard-empty-env · serializer-allowlist-vanish · verdict-reader-wrong-stale · dead-producer-orphan · post-shift-replay-ADVERSARIAL · instrument-append-jsonl-swallow · stale-txt-verdict-no-ttl |
| dead-flag | flag-state-3-layer-no-single-source · dead-producer-orphan-consumer |
| layer-violation | hard-feasibility-split-layer · geometry-blind-selection · name-vs-behavior-hard-misnomers |
| unresolved-conflict | r6-cap-35-vs-40 · frozen-committed-vs-preshift-floor · schedule-data-3way-failopen · paczka-r6-exempt-inverted · fleet-load-multi-mechanism · r-declared-time-hard-no-invariant |
| sentinel-as-data | (K5 most — no-global-deconflict-new-order · geometry-blind-selection · feeds floor/position) |
| threshold-sprawl | numeric-threshold-scatter-mixed-override · r6-cap-35-vs-40 · fleet-load-multi-mechanism |

**Wniosek:** 26 rootów mapuje się czysto na 8 osi entropii. R3-Prawda (void) ma najwięcej rootów (10) → metryka #3 najgęstsza. K1 „brak-jednego-źródła" jest wspólnym korzeniem copy-count + threshold-sprawl + część twin-divergence → **naprawa K1 zbija 3 metryki naraz.**

---

## 10. INTERPRETACJA (dla Adriana, prostym językiem)

8 liczb to **8 rodzajów bałaganu** w Ziomku. Każda ma cel 0 albo 1 — czyli „nie powinno być wcale" albo „powinno być dokładnie jedno źródło". DZIŚ ŻADNA nie jest na celu:
- **17 reguł** ma po kilka kopii zamiast jednej (np. „najwcześniej kurier odbierze" liczone w **17 miejscach**, jedno źródło NIE istnieje).
- **~13 par bliźniaków** się rozjechało (np. konsola pokazuje inną kolejność jazdy niż apka — **44-75 razy dziennie**).
- **25 z 49 przyrządów-prawdy** (połowa!) kłamie albo mierzy nie to co trzeba → **nie wolno na ich liczbie flipować**.
- **5 flag** jest włączonych ale nic nie robią.
- **7 miejsc** decyzję TWARDĄ podejmuje w warstwie miękkiej (dziś bezpieczne, mina na przyszłość).
- **64 pary reguł** walczą ze sobą bez ustalonego zwycięzcy (13 węzłów).
- **Zaślepka `(0,0)`** wpada do decyzji jak realna pozycja — **DZIŚ 2046+14456 razy, 8 kurierów ucierpiało**.
- **10 progów** (jak R6=35min, czasówka=60min) rozsypanych po ~40 miejscach z 3 różnymi sposobami zmiany.

To NIE 8 niezależnych pożarów — wiele zbiega się do **jednego korzenia: „nie ma jednego źródła prawdy"** (reguły, kotwicy, floor, progu, walidatora). Miernik = re-run tego dashboardu po każdej naprawie fundamentu → liczby mają spadać do 0/1.

---

## 11. ŚWIEŻO ZMIERZONE DZIŚ (grep 2026-06-30, weryfikacja — nie z seed-doców)

| Co | wynik | potwierdza |
|---|---|---|
| HEAD silnika | `8024705` (2026-06-30 10:23) | spójny z A3/backing |
| flags.json nie-komentarz keys | **198** | A3 §2 |
| `available_from` w dispatch_v2 | **0** | ROOT6 — single-source floor NIE istnieje |
| runtime-guard `pickup>=shift_start` | ~0 (1 trafienie, nie bramka) | ROOT6 — 0 inwariantu |
| `.bak` (dispatch top + tools) | **176 + 37 = 213** (+courier_api 41 + panel 72 = 326+) | E_dedup_3 R6c-2 |
| ledger mtime | **2026-06-30T20:02 FRESH** | A4 master-ledger |
| `eta_source` w 2000 lin. ledgera | **0** | R8 serializer-vanish POTWIERDZONY |
| `r6_gold4_gate_recovered` | **0** | G-2 niemierzalny POTWIERDZONY |
| `post_shift_overrun_min` | **457** | C18 void-claim OBALONY → VALIDATED |
| `would_hard_cap` | **438** | d23d8a1 fix LIVE POTWIERDZONY |
| `PANEL_IS_FREE_AUTHORITATIVE` konsumenci | **0** (poza def) | dead-but-ON |
| `TRANSPARENCY_SCORING` konsumenci | **1** (sam def) | dead-but-ON |
| `CLUSTER_DROP_GROUPING_METRIC` | **2** (declared-not-wired) | dead-declared |

---

## 12. CAVEATY / GRANICE POKRYCIA (jawnie, nie cisza)

1. **Liczby DRYFUJĄ** — numery linii i część countów z backing Fazy B/C/D (grep tych sesji); świeżo re-zweryfikowane TYLKO te w §11. Re-grep przed użyciem jako pewnik.
2. **copy-count „90 instancji"** = suma surowych kopii (importery dzielące kanon NIE liczone jako osobna logika); per-reguła „otwarte kopie" to liczba do zbicia, nie suma.
3. **void = 19** to FAZA1_03 podsumowanie 49 werdyktów; C18 adversarial przesunął 1 (post_shift_overrun void→VALIDATED), best_effort_fastest został VOID (inny powód). Dedup → 11 rootów. Raportuję OBA widoki (przyrząd vs root).
4. **dead-flag = 5** to dedup, NIE pełny sweep — A3 jawnie ODROCZYŁ pełną mapę flaga→callsite. Mogą istnieć dalsze martwe (osobny OS klasy K). 112-flag registry-leak = osobna oś (flag-drift), nie dead.
5. **sentinel 2046+14456** z B15 (logi 30.06) — nie re-policzone tu na żywym logu (kosztowny grep 54MB); cytat z backing. §11 potwierdza tylko `available_from`=0 i ledger-pola.
6. **twin-divergence „~13"** = 5 grup-kopii (A6, twarde) + ~8 przyrząd/flaga/pole (część PLAUSIBLE: USE_V2_PARSER nie potwierdzony czy shadow parsuje HTML). Liczba dolna pewna = 5.
7. **STOP na dyspozytorni** — zero metryk z Mailek/Papu. Cross-repo konsola/apka liczone TYLKO w zakresie route-order/floor/ETA (granica zachowana).
8. **To miernik, nie naprawa** — żaden flip/edit/restart. Wszystkie cele 0/1 = stan docelowy DESIGN §4, nie deklaracja że osiągalne w jednym sprincie.
