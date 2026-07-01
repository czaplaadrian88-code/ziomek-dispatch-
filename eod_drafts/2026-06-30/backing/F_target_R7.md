# FAZA F — STAN DOCELOWY rodziny **R7 „Koherencja"** (klasa I + oracle-testów)

> **⚠️ DRAFT — produkt syntezy audytu READ-ONLY (sesja tmux 2).** Zero kodu, zero flipów, zero restartów, zero `--notify`, zero git. Ten dokument definiuje **KANONICZNY STAN DOCELOWY + PLAN KONSOLIDACJI** dla rootów rodziny R7 (oś I — „reguły walczą ze sobą", NAGŁÓWEK Adriana). Każda zmiana kodu = OSOBNY mini-sprint protokołem ETAP 0→7 + ACK Adriana. **Numery linii zweryfikowane ŚWIEŻYM grep DZIŚ — HEAD silnik `8024705` (2026-06-30) — DRYFUJĄ (≥3 żywe sesje/repo), re-grepuj przed dotknięciem jako pewnik.**

**Data:** 2026-06-30 · **Tryb:** READ-ONLY · **HEAD:** `8024705` (working tree `.py` czysty; zmienione tylko logi/jsonl)
**Wejście:** `E_dedup_2_truth_conflict.md` (R13/R16/R17/R19/R22/R23/R28 — klaster konflikt) + `D01_rule_graph.md` (17 par K-D01..K-D17) + `D02_precedence_paths.md` (20 par C1..C20 + 3 kanoniczne braki kontraktu §3) + `D03_flag_coupling.md` (I-01..I-20, mapa „co-maskuje-co", inwersje P-1..P-7) + `D04_equality_stack.md` (K-X-1..K-X-10, stos równości) + `D05_frozen_floor_thermal.md` (K1..K7 frozen×floor×thermal) + `E_dedup_1` (R15 anchor / R6 floor — cross-ref granicy) + werdykty adwersaryjne (`faE_equal-treatment…REFUTER`) + świeże greppy/dane/flags.json DZIŚ.
**Kontrakty referencyjne (DESIGN §4):** ①JEDNO-źródło/regułę(kopie=1) ②kontrakt-warstw(HARD-przed-SOFT+inwarianty) ③parytet-bliźniaków(divergence=0) ④prawda-flag(dead=0,rejestr) ⑤prawda-przyrządów(void=0 PRZED flipem) ⑥brak-dryfu-semantyki(display≠decision) ⑦kompletność-cyklu-życia(0-bez-GC) **⑧koherencja(0-nierozstrzygniętych-konfliktów) ← WIODĄCY dla R7.**

---

## 0. ZAKRES — które rooty należą do R7 (+ granice anty-double-count)

Rodzina **R7 = „Koherencja"** — oś I z taksonomii: **dwie reguły/ścieżki/flagi dotykają TEJ SAMEJ decyzji, a rozstrzygnięcie „kto wygrywa" jest niezdefiniowane (`undefined`), niespójne między ścieżkami (`defined-inconsistent`), albo cicho odwracalne (`silent-inversion`).** To jest oś, której poprzednie audyty (feature-zorganizowane) NIE miały explicite — one mapowały KOPIE reguł (A1/J), ZŁĄ WARSTWĘ (C), KŁAMIĄCE PRZYRZĄDY (E). R7 pyta **KTO WYGRYWA i czy to ROZSTRZYGNIĘTE SPÓJNIE we WSZYSTKICH ścieżkach.** Plus pod-soczewka **oracle-testów** (czy `pytest tests/`/przyrząd jest godnym zaufania arbitrem koherencji — manifestuje się tu jako adwersaryjna sprzeczność cross-audyt).

**Szkoda R7 jest STRUKTURALNA i LATENTNA-NA-FLIP (nie zawsze błędne wyjście live):** (a) **mina-na-flipie** — `defined-consistent (FRAGILE)` para jest zgodna TYLKO przy obecnym stanie flag; następny flip (O2 02.07, POST_SHIFT, governor re-enable) rozjeżdża ranking/bramkę; (b) **cicha-inwersja-na-reset** — świadoma decyzja Adriana (równość, floor, always-propose) trzymana flagą której kod-default = polityka SPRZED inwersji → utrata klucza flags.json = ciche cofnięcie BEZ alarmu; (c) **HARD bez egzekutora** — reguła deklarowana najwyższym priorytetem (R-DECLARED) nie ma runtime-bramki → przyszła zmiana cicho ją łamie; (d) **niespójność per-powierzchnia** — ten sam defekt danych (zły grafik) traktowany 3 sprzecznymi politykami jednocześnie; (e) **arbiter kłamie** — przyrząd bramkujący flip (bundle_calib flat-35) mierzy niespójnie z regułą (tier-40) → werdykt flip-no-flip na fałszywej osi.

**7 przetrwałych rootów fam=R7 (survivor-lista po dedup+adwersaryjna weryfikacja) — pogrupowane wg WZORCA koherencji (naturalna oś planu konsolidacji):**

| # | Root (E_dedup_2 id) | Sev | Klasy | Werdykt | source | Wzorzec koherencji (1 zdanie) |
|---|---|---|---|---|---|---|
| **R7-I-A** | `r6-cap-35-flat-vs-40-tier-plus-quantile` (R16) | **P1** | I,N,G | **CONFIRMED** | TAK | Ta sama reguła R6 (świeżość) liczona 6× z RÓŻNYMI progami (35/40/35/35/35-flat/p80) + quantile-recovery luzuje HARD R6 — bramka, selekcja i przyrząd-flip-gate nie zgadzają się co do progu. |
| **R7-I-B** | `paczka-r6-exempt-inverted-in-ranking` (R17) | P2 | I,B | **CONFIRMED** | TAK | `PACZKA_R6_THERMAL_EXEMPT` zwalnia paczkę z HARD-R6 w 3 sites, ale ranking/objektyw (SLA-count, O2-sweep) JĄ KARZE → exempt ODWRÓCONY w warstwie selekcji. |
| **R7-I-C** | `frozen-committed-vs-preshift-floor` (R19) | **P1** | I,M | **PLAUSIBLE** | TAK | 4 clampy czasu-odbioru (frozen-R27 / floor-shift_start / OSRM / debias) BEZ chokepointu precedencji; floor żyje TYLKO na ścieżce OSRM, frozen ją omija → floor martwy gdy committed<shift_start. |
| **R7-I-D** | `fleet-load-multi-mechanism-tax` (R22) | P2 | I,N,D | **CONFIRMED** | TAK | 2-3 reguły OBCIĄŻENIA żywe RAZEM (FLEET_LOAD_BALANCE ±15 + LOADGOV −40 + stopover/bug4-cap), która rządzi = NIEOKREŚLONE → potrójna kara możliwa; + flag-drift A2-vs-A3 governor + 2 tabele bag-cap. |
| **R7-I-E** | `r-declared-time-hard-no-runtime-invariant` (R23) | P2 | I | **CONFIRMED** | TAK | R-DECLARED-TIME deklarowana HARD/najwyższa precedencja (`czas_kuriera≥czas_odbioru` zawsze) ale ZERO runtime-bramki — TYLKO komentarze; egzekucja EMERGENTNA z R27+czasówka. |
| **R7-I-F** | `schedule-data-3way-failopen-failclose` (R28) | **P1** | I,M | **PLAUSIBLE** | TAK | TE SAME zepsute dane grafiku traktowane SPRZECZNIE w 3 miejscach: `is_on_shift` fail-OPEN cicho 24/7 ‖ `_shift_*_dt` fail-CLOSE None ‖ feasibility FAIL12 fail GŁOŚNO. |
| **R7-I-G** | `post-shift-replay-validated-vs-void-ADVERSARIAL` (R13) | P2 | E,I,H | **CONFIRMED** | NIE | Cross-audyt SPRZECZNOŚĆ: `post_shift_overrun_forward_replay` VALIDATED na świeżym ledgerze vs VOID-claimed w allocation-audit (stale) + `sequential_replay._determine_verdict` UNTESTED hardcoded lower-better = I-inwersja celu higher-better. |

> **Werdykty:** 3× P1 (R7-I-A CONFIRMED · R7-I-C/R7-I-F PLAUSIBLE) + 4× P2 (CONFIRMED). `source=NIE` tylko dla R7-I-G (meta-finding: nieświeża analiza audytu, NIE engine-bug — owns hygiene void-claim, nie silnik). Pozostałe `source=TRUE` (realna luka koherencji u źródła, nie render-patch).

**Wiodący kontrakt §4 dla CAŁEJ rodziny = §4.8 „koherencja (0-nierozstrzygniętych-konfliktów)"** — graf interakcji reguł ma zdefiniowaną, SPÓJNĄ precedencję we WSZYSTKICH ścieżkach; zero cichych inwersji; żadna reguła nie bije drugiej bez jawnego rozstrzygnięcia. Kontrakty wspierające: **§4.2** (kontrakt-warstw — HARD-przed-SOFT, runtime-inwarianty egzekwują precedencję: R7-I-A/E), **§4.1** (jedno-źródło — `r6_cap_for_tier()` / `r6_thermal_anchor` / `effective_pickup_at` helper zamiast N-kopii: R7-I-A/B/C), **§4.4** (prawda-flag — silent-revert/maskująca-inwersja: R7-I-A quantile / R7-I-C floor-flag / R7-I-D governor-drift), **§4.5** (prawda-przyrządów — bundle_calib flat-35 mierzy niespójnie / post-shift void-claim: R7-I-A/G).

**Granice (NIE liczę podwójnie — cross-ref do innych rodzin/agentów):**
- **`one-sla-r6-anchor` (E_dedup_1 ROOT5 / R15) = owner R1 (F_target_R1).** R15 = WSPÓLNY anchor-helper (`r6_thermal_anchor` konsumowany przez R6+SLA+O2+bundle_calib). **R7 NIE re-derywuje ekstrakcji helpera — przejmuje KONSEKWENCJĘ KOHERENCJI:** R7-I-A (próg 35/40 na tym anchorze) + R7-I-B (paczka-exempt na tym anchorze) wymagają, by helper był JEDEN i spójny. Ekstrakcja = R1; precedencja+kompletność-sites na nim = R7. **Ruszać RAZEM w sprincie O2 02.07** (inaczej oba no-op).
- **`earliest-pickup-floor-no-chokepoint` (E_dedup_1 ROOT6 / 17 powierzchni) = owner R1/pre-shift-agent (F_target_R1).** R1 owns ekstrakcję `available_from=max(now,shift_start)` + chokepoint czasu-odbioru. **R7-I-C owns PRECEDENCJĘ frozen↔floor↔OSRM↔debias** (uszeregowanie 4 clampów, Adrian Q1/Q2). Floor-chokepoint = R1; reguła „frozen>floor i floor obejmuje frozen<shift_start" = R7. Razem.
- **`flag-state-3-layer-no-single-source` (R14) + `commit-divergence-masking` (R20) = owner R3 (F_target_R3) / R1-D rejestr-flag.** R3/R1-D owns migrację rejestru flag (route/canon → ETAP4/fingerprint). **R7 owns INWARIANT-KOHERENCJI „flaga inwersji nie odwraca polityki na reset"** (klasa flag „inversion-guard" §1.3) — wspólny mechanizm, R7 dostarcza INV-COH-6 jako test-akceptacji, R3 dostarcza rejestr.
- **`numeric-threshold-scatter` (R25) = owner R3 (klasa N).** R25 owns rozsyp WARTOŚCI (8 rodzin progów, override-path-chaos). **R7-I-A owns aspekt KONFLIKTU** (35-flat-HARD ↔ 40-tier-best_effort ↔ p80-quantile = ta sama reguła, sprzeczny próg per-ścieżka). N-scatter (R25) = mechanizm; I-konflikt (R7-I-A) = manifestacja. `r6_cap_for_tier()` helper zamyka OBA.
- **`geometry-blind-selection` (R2, P0-A) + `one-selection-key`/`equal-treatment` (R1) = owner R2/R1.** D02 §3 „brak osi geometrii w kluczu selekcji" (C5) + D04 stos-równości (K-EQ-*) = TAM raportowane. **R7 cross-ref:** R7-I-D (load) interaguje z osią-obciążenia stosu-równości (K-EQ-4 back-door V3.16) — most, nie root R7. Equal-treatment FAR-veto = PYTANIE do Adriana (refuter: harm REFUTED, dług LATENT) — owns R1.
- **`calibration-on-wrong-axis` (R3-fam F3 / G) = owner calibration-agent.** R7-I-A quantile-recovery (ETA_QUANTILE_R6_BAGCAP) LUZUJE R6 na osi delivery-pesymizmu (zła oś); D3 mówi „skalibruj prędkość gold ZANIM usuniesz cap". **R7 owns KONFLIKT (luzowanie HARD vs kanon 35-bez-wyjątków); calibration owns OŚ (poślizg-odbioru zamiast delivery).** Co-design O2 02.07 (inaczej luzowanie i pod-korekta pracują przeciw sobie).
- **`bundle_calib O2 oracle` (R3 / C01) = owner R3 (instrument-truth).** R3 owns bramkę „flip tylko na validated instrument". **R7-I-A owns dlaczego bundle_calib KŁAMIE dla T3** (flat-35 ignoruje tier-40) — most do R3; fix (tier-aware overage) = część `r6_cap_for_tier()`.
- **STOP na dyspozytorni** — Mailek/Papu poza zakresem.

---

## 1. CROSS-CUTTING — KONTRAKT KOHERENCJI (produkt §4.8 + §4.2 + §4.1 + §4.4)

Stan docelowy R7 zaczyna się od JEDNEGO żywego artefaktu = **REJESTR KOHERENCJI / GRAF PRECEDENCJI** (analogiczny do REJESTRU PRAWDY w R3 / REJESTRU CYKLU-ŻYCIA w R6 / MACIERZY-warstw w R2): dla KAŻDEJ pary reguł/flag/ścieżek dotykających TEJ SAMEJ decyzji — jawny `rule_a × rule_b × natura × precedence_status × runtime-invariant-enforcing-it`. Dziś rejestr istnieje WYŁĄCZNIE jako proza w `ZIOMEK_REGULY_KANON.md §1` (TABELA ROZSTRZYGANIA) + werdykty C1-C7/C-DT/D1-D5 — ale (a) NIEKOMPLETNY (milczy o ~12 par), (b) NIEEGZEKWOWANY (kanon mówi „40=alarm-only", kod robi 40-per-ścieżka — `defined-inconsistent`), (c) BEZ runtime-tripwire (R-DECLARED „nadrzędne" bez bramki).

### REJESTR KOHERENCJI (szkielet kontraktu — precedence_status zweryfikowany świeżo DZIŚ; ◆=mój root, ○=cross-ref)

| Para (decyzja) | rule_a | rule_b | precedence_status DZIŚ | Kanon mówi | Kontrakt docelowy |
|---|---|---|---|---|---|
| ◆ **R6-cap próg** | feasibility HARD-reject `>35` (common.py:763) | best_effort/objm `cap_min=40` (common.py:2651, flags.json:205) + bundle_calib flat-35 (`:56`) + p80-quantile (feasibility:1089) | **defined-inconsistent** (C5/D3) | 40=TYLKO ALARM, 35 dla KAŻDEGO; USUŃ quantile gold≤4 | `r6_cap_for_tier()` 1 helper; 40 tylko alarm-gated; quantile na osi poślizgu |
| ◆ **paczka R6-exempt** | exempt 3 HARD-sites (feasibility:1050/1080/1105/1152) | `_count_sla_violations` + O2-sweep BEZ exempt (route_sim:635/696) | **silent-inversion** (K3) | exempt spójny we wszystkich warstwach | exempt w 1 anchor-helperze → auto-spójny; 4. site na flipie O2 |
| ◆ **czas-odbioru (4 clampy)** | frozen-R27 committed (route_sim:1086, courier_orders:872, fleet_state:519) | floor shift_start (feasibility:794 OSRM-departure, fleet_state:857 OSRM-chain) | **undefined → de-facto silent-inversion** (K1/C19) | committed nietykalny + wyklucz pre-shift-niezdążającego (Q2) | 1 chokepoint `effective_pickup_at=clamp_order(...)`; floor obejmuje frozen<shift_start |
| ◆ **obciążenie floty** | FLEET_LOAD_BALANCE ±15 (dispatch_pipeline:1462, ON) | FLEET_LOAD_GOVERNOR −40 (flags.json:165=true → effective ON) + stopover + bug4-cap | **undefined** (D5 pending) | ⏳ werdykt Adriana (rekalibracja vs jeden rządzący) | 1 reguła load rządzi; measure-first triple-tax; A3 governor sprostowany |
| ◆ **R-DECLARED-TIME** | `czas_kuriera≥czas_odbioru` HARD (najwyższy priorytet) | R27 SOFT-window + czasówka (egzekutorzy pośredni) | **defined(C-DT)/undefined-at-runtime** | R-DECLARED nadrzędne nad R6 | runtime-inwariant `czas_kuriera>=czas_odbioru` tripwire (fail-loud) |
| ◆ **grafik zepsuty** | `is_on_shift` fail-OPEN cicho 24/7 (schedule_utils:376/383/392/401) | `_shift_*_dt` fail-CLOSE None (courier_resolver:1252) + FAIL12 GŁOŚNO (feasibility) | **defined-inconsistent (3-way)** | — (brak werdyktu) | 1 polityka fail (open LUB close), fail-LOUD; walidacja u źródła (arkusz) |
| ◆ **post-shift void-claim** | C18: VALIDATED (ledger 457×/956 DZIŚ) | allocation-audit: VOID (0/282, stale) | **defined-inconsistent (cross-audyt)** | — | void-claim wymaga ŚWIEŻEGO grepa PRZED zapisem; `_determine_verdict` higher-better |
| ○ **SLA vs R6 anchor** | `_count_sla_violations` pickup_at (route_sim:635) | `r6_thermal_anchor` ready_at (route_sim:663) | **defined-inconsistent** (T2) | R6 ready ostrzejszy | **R1/R15** — 1 anchor-helper (most do R7-I-A/B) |
| ○ **geometria vs czas** | geometria SOFT-only (score) | `lex_qual` czysto-czasowy (objm_lexr6:29) | **undefined** (C5, P0-A) | — | **R2** — oś geometrii w kluczu selekcji |
| ○ **equal-treatment osi-krzyżowej** | `_selection_bucket` równo (dispatch:2451) | `_demote_blind_empty` oś-obciążenia (dispatch:2504) | **silent-inversion** (K-EQ-4) | równość ZOSTAJE (C3); FAR-veto = PYTANIE | **R1** — 1 oś-pozycji + ortogonalna oś-obciążenia |
| ○ **commit-divergence maska** | const env-default True (common.py:2806) | flags.json=False maskuje | **silent-inversion** (C7/K-X-8) | always-propose żyje | **R3/R1-D** — const→False + ETAP4 (most INV-COH-6) |
| ○ **lex_qual 3↔4-krotka** | kanon warunkowo 4-krotka (objm_lexr6:44) | shadow frozen 3-krotka (dispatch:1122) | **fragile** (C6, flip POST_SHIFT) | — | **R1/R3** — shadow≡kanon golden (most C7 flip) |
| ○ **nazwa-vs-zachowanie** | `*_HARD_GATE`/`*_VETO` (nazwa HARD) | zachowanie SELEKCJA/metryka | **defined-inconsistent** (C12/C13) | zdejmij VETO/HARD z nazwy (D4) | **R4** (semantyka) — nazwa=warstwa |

**Cel rejestru: kolumna „precedence_status" = `defined-consistent` (lub `ok`) dla KAŻDEJ pary — albo precedencja jawnie rozstrzygnięta i egzekwowana we WSZYSTKICH ścieżkach, albo jawnie `PENDING-ACK-Adriana+data` (D5 load, FAR-veto).** „Zdrowe" pary (C-OK1 scoring-L6 hard-rejecty monotonic `and verdict=="MAYBE"` — jedyny zweryfikowany czysty łańcuch HARD; K-D05 R27-value-frozen/window-SOFT; K-D20 R-DECLARED↔R6 C-DT rozstrzygnięte) = DOWÓD-WZORZEC że to osiągalne — target = „bądź jak te".

### INWARIANTY KOHERENCJI (docelowa suite — czerwone-na-start, zielone-po-konsolidacji)

- **INV-COH-1 (jeden próg per reguła, §4.1/§4.8):** ta sama reguła decyzyjna ma JEDEN helper-źródło; warianty (tier/tryb) są PARAMETREM helpera, nie N-kopiami. *Test:* `r6_cap_for_tier(tier, mode)` jest jedynym producentem progu R6; feasibility+best_effort+O2+bundle_calib importują; `grep "= 35\|= 40" | r6` = 1 definicja + N konsumentów. Dziś: 6 niezsynchronizowanych.
- **INV-COH-2 (precedencja zdefiniowana i spójna cross-ścieżka, §4.8):** dla każdej pary konfliktowej z rejestru precedence_status ∈ {defined-consistent, ok, PENDING-ACK+data}; ZERO `undefined`/`defined-inconsistent`/`silent-inversion` bez jawnej etykiety. *Test:* rejestr koherencji → 0 par bez rozstrzygnięcia; replay potwierdza że wszystkie ścieżki tej samej decyzji dają ten sam werdykt na worku spornym.
- **INV-COH-3 (HARD ma runtime-egzekutora, §4.2):** każda reguła zadeklarowana HARD/„nadrzędna" w kanonie ma runtime-bramkę/tripwire (fail-loud), NIE tylko komentarz. *Test:* `grep` reguł HARD z kanonu → każda ma odpowiadający `assert`/gate w hot-path; R-DECLARED `czas_kuriera>=czas_odbioru` tripwire istnieje (dziś `grep`=∅, tylko komentarze).
- **INV-COH-4 (jedna polityka fail per defekt-danych, §4.8+§4.2):** ten sam defekt wejścia (zły grafik, sentinel coords) ma JEDNĄ politykę fail (open LUB close) spójną cross-warstwa, fail-LOUD (log.warning). *Test:* literówka „11.00" w grafiku → jedno spójne traktowanie (nie „on-shift 24/7" ∧ „floor=None" ∧ „FAIL12 alert" jednocześnie); `is_on_shift` fail-open ma log.warning jak FAIL12.
- **INV-COH-5 (exempt/carve-out spójny we wszystkich warstwach, §4.8):** wyjątek od reguły (paczka-R6-exempt) zaaplikowany w bramce JEST honorowany w count/ranking/objektywie. *Test:* paczka liczona IDENTYCZNIE w gate (`feasibility:1105`) ∧ count (`_count_sla_violations`) ∧ ranking (`_o2_key`); golden A≡B na paczce.
- **INV-COH-6 (flaga-inwersji nie odwraca polityki na reset, §4.4+§4.8):** flaga trzymająca świadomą inwersję (równość, floor, always-propose, exempt) ma kod-default = polityka-PO-inwersji ALBO runtime-inwariant chroniący decyzję; utrata klucza flags.json ≠ cichy flip. *Test:* dla każdej flagi „inversion-guard" — `const-default == effective-policy` LUB inwariant runtime; usunięcie klucza z flags.json NIE odwraca werdyktu na worku testowym. Dziś: ≥6 flag (equal-treatment ×3, PRE_SHIFT_DEPARTURE_CLAMP, ETA_QUANTILE, COMMIT_DIVERGENCE) = OFF-policy-revert.
- **INV-COH-7 (arbiter koherencji mierzy spójnie z regułą, §4.5):** przyrząd bramkujący flip (bundle_calib, post-shift-replay, sequential_replay) mierzy TĄ SAMĄ definicją co reguła którą certyfikuje; void-claim wymaga świeżego grepa ledgera. *Test:* bundle_calib overage tier-aware (NIE flat-35 dla T3); `sequential_replay._determine_verdict` kierunek (higher/lower-better) zgodny z celem; void-claim ma timestamp+świeży-grep.

Mapowanie inwariant→root: **R7-I-A** → 1,2,6,7 · **R7-I-B** → 5 · **R7-I-C** → 2,4(coords),6 · **R7-I-D** → 1,2 · **R7-I-E** → 3 · **R7-I-F** → 4 · **R7-I-G** → 7.

### 1.3 META-KONTRAKT: klasa flag „inversion-guard" (rdzeń §4.4↔§4.8, most do R3/R1-D)

Najgroźniejsza klasa koherencji (D03 §4, D04 §0/§7) = **ten sam mechanizm `decision_flag()` z PRZECIWNYM znaczeniem OFF:**
- **ETAP4 wzorzec (≈40 flag):** const `False` = bezpieczny fallback; flags.json `True` = kanon. Reset → OFF = **bezpiecznie** (shadow-first). Zaprojektowane.
- **Rodzina inwersji (equal-treatment ×3, PRE_SHIFT_DEPARTURE_CLAMP, ETA_QUANTILE, COMMIT_DIVERGENCE):** const/env `False`/`"0"` = **polityka-ZŁA** (dyskryminuj / brak floor / 35-bez-recovery / KOORD-wraca). Reset → OFF = **cofnięcie świadomej decyzji Adriana, CICHO**.

**Zweryfikowane DZIŚ (mój zakres):** `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP` const `"0"` (common.py:2006-2007) ↔ flags.json:141=true (R7-I-C floor); `ENABLE_ETA_QUANTILE_R6_BAGCAP` const OFF ↔ flags.json:179=true (R7-I-A quantile); `ENABLE_FLEET_LOAD_GOVERNOR` const `"0"` (common.py:2103) ↔ flags.json:165=true (R7-I-D — A2 błędnie podał OFF). Brak markera „ta flaga: OFF=safe vs OFF=policy-revert" → operator/conftest/reset traktuje jednakowo = mina. **Kontrakt docelowy:** klasa-flag „inversion-guard" — kod-default = polityka-PO-inwersji (nie przed) ALBO runtime-inwariant na decyzję (INV-COH-6). Współdzielony z R3/R1-D (rejestr flag + fingerprint route/canon/equal-treatment); R7 dostarcza inwariant-akceptacji, R3 migrację.

---

## 2. STAN DOCELOWY PER ROOT (twardy kontrakt + inwariant runtime + bramka „zero nowych kopii")

### ▰ PODRODZINA 1 — ANCHOR/PRÓG KOHERENCJA (R6/SLA cluster; sprint O2 02.07)

### R7-I-A — `r6-cap-35-flat-vs-40-tier-plus-quantile` (P1, CONFIRMED, źródło, OTWARTY, SURVIVOR) — most R1/R15 + R3/R25 + calib

**CO DZIŚ (entropia, świeżo zweryfikowane — grep common.py/feasibility/bundle_calib + flags.json DZIŚ):**
- **6 sites, 4 ścieżki override** (N1): `BAG_TIME_HARD_MAX_MIN=35` (common.py:763, **BARE literał** — zmiana TYLKO kod+restart) / `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN=40` (common.py:2651, **flags.json:205=40 HOT**) / `O2_OVERAGE_CAP_MIN=35` (common.py:2661, env) / `O2_CAP_Z_MIN=35` (common.py:2662, env) / `R6_MAX_MIN=35.0` (bundle_calib_shadow.py:56, **literał lokalny**) / p80-quantile (feasibility:1089, `ENABLE_ETA_QUANTILE_R6_BAGCAP` flags.json:179=**true**).
- **3 niespójności koherencji:** (a) **inwersja tunable↔ważność** — baza HARD R6=35 BARE (najwyższa reguła, najmniej tunable), wyjątek-stretch 40 HOT → Adrian może podbić 40→45 na żywo, baza 35 zamrożona, nikt nie pilnuje `40==35+margines`; (b) **bundle_calib flat-35 mierzy spóźnienie paczki carry 38min jako overage=3** (płasko) IGNORUJĄC tier-40 → **over-penalizuje T3, a to WERDYKT bramkujący flip O2 02.07** (most R3 instrument-truth, C9=P0); (c) **quantile-recovery luzuje HARD R6** dla gold≤4 na p80 z PRÓBY SELEKCYJNEJ (matched-courier only — generator sam ostrzega `eta_quantile_calib:30`) → 32,4% R6-rejectów `would_pass_calibrated`; D3 kanon: „35 dla KAŻDEGO bez wyjątków, USUŃ recovery gold≤4, skalibruj PRĘDKOŚĆ gold ZANIM usuniesz cap".
- **Kanon C5 (`ZIOMEK_REGULY_KANON.md:123`):** „40=TYLKO ALARM (auto, dla WSZYSTKICH, gdy Strategia 1+2 niewykonalne); kod dziś ma 40 per-klasa best_effort/objm — NIEZGODNE, do poprawy na alarm-only". Kandydat carry 38min: feasibility=NO, best_effort go bierze (always-propose) — `defined-inconsistent`.

**STAN DOCELOWY (kontrakt §4.1 + §4.8 + §4.2 + §4.5):**
1. **§4.1** JEDEN helper `r6_cap_for_tier(tier, mode)` — jedyny producent progu R6, konsumowany przez feasibility (HARD), best_effort/objm (selekcja), O2-sweep (objektyw), bundle_calib (przyrząd). 35 = baza dla WSZYSTKICH; 40 = PARAMETR `mode=ALARM` gated bramką (auto: `pool_feasible==0`/Strategia-1+2-niewykonalne), NIE stały per-ścieżka (zgodne z C5). Inwariant relacji `cap(ALARM)==cap(NORMAL)+margines` w jednym miejscu.
2. **§4.5 (most R3, bramkuje flip O2)** `bundle_calib` overage TIER-AWARE (`overage=max(0, age−r6_cap_for_tier(tier))`) — przestaje kłamać dla T3; oracle O2 02.07 dostaje wierny pomiar (most C01/under_z segmentacja per-tier).
3. **§4.8 (most calibration, co-design)** quantile-recovery (ETA_QUANTILE_R6_BAGCAP) rozstrzygnięty ŁĄCZNIE z SLA-anchor + prep-bias w O2 02.07: albo USUŃ (D3) + skalibruj prędkość gold na osi poślizgu-odbioru (NIE delivery-pesymizmu, zła oś G-1), albo obwaruj trybem ALARM. Inaczej luzowanie (quantile) i pod-korekta (debias) pracują przeciw sobie.
4. **§4.4 (most R1-D)** `ENABLE_ETA_QUANTILE_R6_BAGCAP` = inversion-guard (const-default zgodny z intencją) + w ETAP4/fingerprint.

**INWARIANT RUNTIME:** jeden `r6_cap_for_tier()` konsumowany przez ≥4 powierzchnie (INV-COH-1); 40 osiągalne TYLKO w `mode=ALARM` (INV-COH-2); bundle_calib mierzy tym samym progiem co feasibility (INV-COH-7). *Test:* `grep "35\|40" | r6-cap` = 1 helper + N importów (0 bare-kopii); worek carry 38min T3: feasibility-cap==best_effort-cap==O2-cap==bundle_calib-cap (wszystkie 40 lub wszystkie 35-alarm); replay O2 ON↔OFF dowodzi bundle_calib nie kłamie dla T3.

**BRAMKA „ZERO NOWYCH KOPII":** 1 helper `r6_cap_for_tier()` zastępuje 6 stałych (−5 kopii), NIE 7. miejsce z literałem 35/40. **DOTYKA SILNIKA + bramkuje flip O2 02.07 → pełny protokół ETAP 0→7 + ACK + off-peak + replay ON↔OFF + parytet bliźniaków (feasibility↔best_effort↔O2↔bundle_calib) + pełna regresja.** RUSZA WSZYSTKIE 6 sites RAZEM (protokół: „flip O2 02.07 rusza wszystkie 6") + co-design z R15 anchor + calibration (oś poślizgu). Najwyższa materialność I-rootu (bramkuje datę 02.07).

---

### R7-I-B — `paczka-r6-exempt-inverted-in-ranking` (P2, CONFIRMED, źródło, OTWARTY, SURVIVOR) — most R1/R15

**CO DZIŚ (entropia, świeżo zweryfikowane — grep feasibility/route_sim/plan_recheck DZIŚ):**
- **exempt w 3 HARD-sites:** `_o_paczka_exempt` (feasibility:1050-1055) → `:1080` (nie liczy do r6_max) + `:1105` (`and not _o_paczka_exempt` w HARD-reject) + `:1152` (SLA-detail loop `continue`); kanon `is_paczka_order` (common.py:3479) + `PACZKA_ADDRESS_IDS={161,232..236}`; flaga `ENABLE_PACZKA_R6_THERMAL_EXEMPT` flags.json:183=**true** (firmowe paczki = NIE gorące jedzenie, Adrian 15.06).
- **ranking/objektyw BEZ exempt (przeciekają paczkę):** `_count_sla_violations` (route_sim:635-660) — **ZERO exempt** → `plan.sla_violations` LICZY paczkę jako naruszenie → (a) bramkuje wejście SLA-block feasibility:1135, (b) zasila `_o2_key` (plan_recheck:690, gdy O2 OFF). `_compute_per_order_delivery_minutes` (O2 objektyw, route_sim:696-728) — woła `r6_thermal_anchor` ale **ZERO exempt** → `o2_score`/`max_carried_age` liczą thermal paczki (gdy O2 ON).
- **Skutek:** paczka NIE odrzucona (bramka OK), ale **rankowana jakby spóźniona** (niższy priorytet w `_o2_key`/`_sweep`) → exempt ODWRÓCONY w warstwie selekcji 7/9. **„4. site missing"** (protokół `ziomek-change-protocol.md:96`): flip O2 02.07 (`ENABLE_O2_READY_ANCHOR_SWEEP` ON) BEZ dodania exempt do `_compute_per_order_delivery_minutes` = regres rankingu paczek (C3 flaga sprzężona).

**STAN DOCELOWY (kontrakt §4.8 + §4.1 + §4.3):**
1. **§4.1 (most R15)** exempt zaaplikowany w JEDNYM anchor-helperze (`r6_thermal_anchor`/anchor-aware count) → automatycznie spójny w feasibility-gate + SLA-count + O2-sweep. NIE 3-4 niezależne `_is_paczka_sim()` checki, lecz parametr helpera.
2. **§4.8 (4. site, sprzężone z O2-flip)** dodać exempt do `_compute_per_order_delivery_minutes` PRZED/Z flipem O2 02.07 (`ENABLE_O2_READY_ANCHOR_SWEEP` MUSI iść z exempt-w-O2 — C3 coupling). `_count_sla_violations` honoruje exempt (zamyka rozjazd A↔B na paczkach).
3. **§4.3** golden-test A≡B: paczka liczona IDENTYCZNIE w gate ∧ count ∧ ranking.

**INWARIANT RUNTIME:** paczka exempt z R6 jest exempt we WSZYSTKICH warstwach (gate==count==ranking==objektyw) — INV-COH-5. *Test:* worek z paczką: `plan.sla_violations` NIE liczy paczki; `_o2_key`/`o2_score` NIE demotują paczki; golden `_count_sla_violations(paczka)==feasibility-gate(paczka)`.

**BRAMKA „ZERO NOWYCH KOPII":** exempt w 1 anchor-helperze (most R15) → −3 rozsiane `_is_paczka_sim` checki; dodanie 4. site = przez helper, nie 4. niezależny `if is_paczka`. **DOTYKA SILNIKA + sprzężone z flipem O2 02.07 → pełny protokół + ACK; rusza Z R7-I-A i R15 anchor (jeden sprint O2).**

---

### ▰ PODRODZINA 2 — CZAS/FLOOR KOHERENCJA (precedencja clampów)

### R7-I-C — `frozen-committed-vs-preshift-floor` (P1, PLAUSIBLE, źródło, OTWARTY, SURVIVOR) — most R1 (floor-17) + R1-D (flag-guard)

**CO DZIŚ (entropia, świeżo zweryfikowane — D05 K1/K5 + grep DZIŚ):**
- **4 clampy czasu-odbioru BEZ chokepointu precedencji:** (1) **frozen-R27 committed** — TSP soft-window (route_sim:1086 `window_open=max(0,open−V3274_FROZEN_PICKUP_WINDOW_MIN(5))`) + render apka (`courier_orders.py:872` `FROZEN_PICKUP_ETA`, floor=`max(pred,ck,gotowość)`) + render konsola (`fleet_state.py:519` `PIN_AGREED_PICKUP_TIME`, `plan_pv` PRZED OSRM); (2) **floor shift_start** — feasibility departure-clamp (`feasibility_v2.py:794`, `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP` const `"0"` ↔ flags.json:141=true) DZIAŁA NA START SYMULACJI nie wartość węzła pickup; konsola `fleet_state.py:857` DZIAŁA TYLKO na łańcuch OSRM (`_eta_chain`); (3) **OSRM surowy**; (4) **debias** (PICKUP_DEBIAS_MIN=4.5, common.py:3131) — **SHADOW-ONLY, sierota** (nie dożywa do żywego floor).
- **CICHA INWERSJA (K1/C19):** floor (shift_start) aplikowany WYŁĄCZNIE na ścieżce OSRM/departure. Frozen **omija OSRM** (`fleet_state.py:521-522` wybiera `plan_pv` zanim dotknie `osrm[i]` gdzie żyje `depart_after`; `courier_orders.py:875-876` floruje frozen TYLKO do `gotowość`, NIE shift_start). Gdy committed `czas_kuriera<shift_start` (LEGALNE: czasówka/elastyk committed pre-shift) → frozen **aktywnie zatrzaskuje złą godzinę**, floor = no-op. Apka NIE ma floor shift_start w ogóle (C20, A6 gr.6 #10/#11 BRAK). + debias shadow-only → żywy floor floruje SUROWY optymistyczny estymat (~18min poślizg, debias 4,5 = 4-6× za mały).
- **Adrian decyzje 30.06 (kotwica precedencji docelowej, preshift-floor-audit §8):** Q2 „deklaracja restauracji NIETYKALNA → nie dawaj zlecenia pre-shift kurierowi który nie zdąży (zmieniaj KTO, nie czas)"; Q1 „OBA: jedno źródło floor (commit+rendery) + twardsza feasibility"; Q2b „floor obejmuje pre_shift+no_gps". → frozen/committed > floor (committed wins), ALE feasibility wyklucza pre-shift kuriera który nie zdąży. **Dziś ani jedno ani drugie egzekwowane spójnie.**
- **Werdykt PLAUSIBLE:** ścieżka konfliktu CONFIRMED z lektury; magnituda (ile committed<shift_start dziennie) = oracle Fazy C (read-only nie policzył).

**STAN DOCELOWY (kontrakt §4.8 + §4.1 + §4.4; most R1 floor-chokepoint):**
1. **§4.1 (most R1 ROOT6)** JEDEN chokepoint `effective_pickup_at = clamp_order(committed_frozen, shift_start_floor, osrm, debias)` w warstwie 1/9 — uszeregowanie JAWNE i jedno (R1 owns ekstrakcję `available_from`; R7 owns kolejność). Render apka+konsola czytają z chokepointu (NIE 4 powierzchnie własny podzbiór/kolejność).
2. **§4.8 (precedencja per Adrian Q1/Q2/Q2b)** `frozen/committed > floor` (committed nietykalny), ALE **floor OBEJMUJE frozen gdy committed<shift_start** (Q2b) → floor trafia w WARTOŚĆ committed (chokepoint), uszeregowany WZGLĘDEM frozen — nie na ścieżce OSRM obok. Druga połowa (Q2): feasibility WYKLUCZA pre-shift kuriera który nie zdąży na committed (R-LATE-PICKUP propozycja przedłużenia DO RESTAURACJI, zmieniaj KTO nie czas — NIE ukryta kara). debias dożywa do żywego eta (przestaje być sierotą shadow-only).
3. **§4.4 (most R1-D, INV-COH-6)** `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP`/`CLAMP_PRESHIFT_PICKUP_ETA` = inversion-guard (const-default zgodny z polityką floor-ON) + w fingerprint; reset flags.json NIE usuwa floor cicho.
4. **Bliźniaki RAZEM** (D05 §luki): konsola↔apka floor (C20) — apka dostaje floor shift_start; plan_recheck regen (most R1 K2 — NIE odclampowuje co 5min).

**INWARIANT RUNTIME:** `pickup_at >= shift_start` we WSZYSTKICH powierzchniach (silnik+konsola+apka, w t.cz. plan_recheck regen) — INV-COH-4(coords→time); precedencja `frozen>floor>OSRM>debias` JAWNA z jednego chokepointu (INV-COH-2); reset floor-flagi nie cofa floor (INV-COH-6). *Test:* worek committed<shift_start: silnik∧konsola∧apka pokazują ten sam `effective_pickup_at>=shift_start` LUB feasibility wykluczyła kuriera (jawny carve-out); 4 clampy uszeregowane z jednego punktu.

**BRAMKA „ZERO NOWYCH KOPII":** 1 chokepoint `clamp_order` (most R1) zastępuje 4 rozsiane clampy × 5 powierzchni; render czyta chokepoint (−4 własne kolejności). NIE 17. powierzchnia z własnym floor-patchem (usunąć render-clamp łatki — C4-a). **DOTYKA SILNIKA+KONSOLI+APKI (cross-repo) → pełny protokół + ACK + off-peak + parytet bliźniaków (silnik↔konsola↔apka golden) + replay. Rusza Z R1 floor-chokepoint (jeden sprint pre-shift-floor); precedencja per Adrian Q1/Q2/Q2b (już ACK-owane).**

---

### ▰ PODRODZINA 3 — OBCIĄŻENIE KOHERENCJA

### R7-I-D — `fleet-load-multi-mechanism-tax` (P2, CONFIRMED, źródło, OTWARTY) — most R3/A2-A3 (flag-drift) + R1 (equal-axis)

**CO DZIŚ (entropia, świeżo zweryfikowane — grep common.py/flags.json/dispatch_pipeline DZIŚ):**
- **2-3 reguły OBCIĄŻENIA żywe RAZEM:** R-10 `ENABLE_V326_FLEET_LOAD_BALANCE` (±15 score, dispatch_pipeline:1462, ON) + `ENABLE_FLEET_LOAD_GOVERNOR` (−40, **flags.json:165=true → EFEKTYWNY ON** mimo const `"0"` common.py:2103 — **A2 błędnie podał OFF: czytał const, nie efektywny** = dodatkowo flag-drift D) + stopover `bonus_r9_stopover` + bug4-cap → potrójna kara możliwa (odebrać LEPSZEMU obciążonemu).
- **Sprzężenie nieoczywiste:** `loadgov_ewma` KARMI relaksację FAR-veto pre_shift (`dispatch_pipeline.py:5101`) **MIMO że flaga LOADGOV decyzyjnie OFF-w-A2** → governor-telemetria żywa, używana w INNEJ regule. Governor dodatkowo ROZLUŹNIA okno committed-pickup R27 (`set_committed_pickup_tolerance` gdy `loadgov_ewma≥4.5` 5→10min — C10/K-X-7) → SOFT-load modyfikuje SOFT-R27.
- **N5 bag-cap DWIE tabele rozbieżne:** `BUG4_TIER_CAP_MATRIX` (peak-aware SOFT, ON: std 4/slow 3) vs `HARD_TIER_BAG_CAP` (flat HARD, flaga OFF: std 5/slow 4) → flip HARD_TIER ON = egzekwuje 5/4 gdy BUG4 SOFT karze od 4/3 = niespójna granica.
- **Kanon D5 (`ZIOMEK_REGULY_KANON.md`):** „3 podatki obciążenia ⏳ CZEKA WERDYKT ADRIANA (rekalibracja vs jeden rządzący; measure-first ile razy potrójna kara odbiera zlecenie LEPSZEMU obciążonemu)" — **NIEROZSTRZYGNIĘTE.**

**STAN DOCELOWY (kontrakt §4.8 + §4.1 + §4.4):**
1. **§4.8 (PENDING-ACK Adriana — D5)** target precedencji = JEDNA reguła load rządzi (która warstwa) — ALE decyzja NIE jest moja: **measure-first** (oracle: ile RAZY potrójna kara odbiera zlecenie LEPSZEMU obciążonemu), przedstaw liczby, ACK Adriana (rekalibracja vs jeden rządzący). Do werdyktu: rejestr koherencji ETYKIETUJE parę `PENDING-ACK-Adriana+data` (NIE silnie pickuje, NIE silnie triple-taxuje cicho).
2. **§4.4 (most R3/A2-A3, natychmiastowe)** sprostować A3 efektywny-stan governor (flags.json:165=true → ON, A2 błędne OFF) — flag-drift do domknięcia w rejestrze flag; `ENABLE_FLEET_LOAD_GOVERNOR` w fingerprint. Udokumentować/rozprząc `loadgov_ewma`-karmi-FAR-veto-mimo-OFF (sprzężenie nieoczywiste).
3. **§4.1** JEDNA tabela `bag_cap_per_tier` (N5: BUG4 SOFT vs HARD_TIER rozbieżne → jedna macierz, tryb SOFT/HARD = parametr).

**INWARIANT RUNTIME:** dla worka obciążonego kuriera — JEDNA reguła load rozstrzyga (nie 3 niezależne kary stackują bez precedencji) LUB para etykietowana PENDING-ACK (INV-COH-2); A3 governor-state == efektywny (INV-COH-6 flag-truth). *Test:* oracle „potrójna-kara-count" zmierzony; A3 sprostowany (governor ON); `bag_cap` z jednej tabeli.

**BRAMKA „ZERO NOWYCH KOPII":** sprostowanie A3 (doc) + jedna `bag_cap` tabela (−1 macierz) — natychmiast, low-risk. Reguła-rządząca = PENDING Adrian D5 (NIE ruszać silnika bez werdyktu — measure-first). **Flag-drift fix = doc/rejestr (R3). Triple-tax rozstrzygnięcie = oracle + ACK Adriana (D5), potem pełny protokół.**

---

### ▰ PODRODZINA 4 — HARD-BEZ-EGZEKUTORA

### R7-I-E — `r-declared-time-hard-no-runtime-invariant` (P2, CONFIRMED, źródło, OTWARTY)

**CO DZIŚ (entropia, świeżo zweryfikowane — grep runtime-gate DZIŚ = ∅):**
- **R-DECLARED-TIME** deklarowana HARD/najwyższa-precedencja (22.04: „`czas_kuriera ≥ czas_odbioru` ZAWSZE") ale **ZERO runtime-bramki/inwariantu** — `grep "czas_kuriera >= czas_odbioru\|>= czas_odbioru"` = **PUSTO DZIŚ** (zweryfikowane); jedyne trafienia = KOMENTARZE (common.py:3410/3414/3494, dispatch_pipeline.py:3168). Egzekucja EMERGENTNA z R27 frozen-window (SOFT) + czasówka + `pickup_ready_at=czas_kuriera`.
- **Kanon C-DT (`ZIOMEK_REGULY_KANON.md:126`):** „R-DECLARED-TIME nadrzędne (nie kłam o czasie); R6-breach → propozycja przesunięcia odbioru do restauracji (dociera ≥15min, wyjątkowo ≥10min przed)". Precedencja ZDEFINIOWANA w kanonie (C-DT), ale **brak strażnika = `undefined-at-runtime`** — przyszła zmiana R27/ready-anchor cicho złamie R-DECLARED bez tripwire. „Nadrzędne" bez runtime = pusta deklaracja precedencji.

**STAN DOCELOWY (kontrakt §4.2 + §4.8; C-DT jako kanon precedencji):**
1. **§4.2 (runtime-inwariant)** `czas_kuriera >= czas_odbioru` jako tripwire (fail-loud, log.warning+alert) w chokepoincie gdzie committed jest ustawiany (`pickup_ready_at=czas_kuriera`, dispatch_pipeline:3486) — egzekucja JAWNA nie emergentna. NIE hard-reject (zgodne always-propose), lecz tripwire wykrywający naruszenie + propozycja przesunięcia (C-DT, ≥15min).
2. **§4.8** R-DECLARED nadrzędne nad R6 udokumentowane w rejestrze koherencji jako `defined-consistent` z EGZEKUTOREM (nie tylko C-DT-proza).

**INWARIANT RUNTIME:** `czas_kuriera >= czas_odbioru` sprawdzane runtime (tripwire fail-loud) — INV-COH-3; przyszła zmiana R27/anchor która łamie R-DECLARED → fail-loud, nie cisza. *Test:* `grep` runtime-gate R-DECLARED = ≥1 (dziś 0); syntetyczny worek `czas_kuriera<czas_odbioru` → tripwire krzyczy.

**BRAMKA „ZERO NOWYCH KOPII":** 1 tripwire (najwyższa reguła dostaje strażnika), NIE N rozsianych assertów. **DOTYKA SILNIKA (dodanie tripwire w hot-path) → pełny protokół + ACK; ale TYLKO obserwacyjny na start (fail-loud log, nie reject) → niskie ryzyko, kandydat wcześniejszej fazy. Egzekucja = C-DT (już ACK-owane kanonem).**

---

### ▰ PODRODZINA 5 — FAIL-POLICY KOHERENCJA

### R7-I-F — `schedule-data-3way-failopen-failclose` (P1, PLAUSIBLE, źródło, OTWARTY) — most R5/M (sentinel/schedule)

**CO DZIŚ (entropia, świeżo zweryfikowane — grep schedule_utils/courier_resolver/feasibility DZIŚ):**
- **TE SAME zepsute dane grafiku (literówka „11.00" zamiast „11:00", pusta godzina, fetch 06:00→flota bez grafiku 00:00-06:00) → 3 SPRZECZNE traktowania:** (1) `is_on_shift` **fail-OPEN CICHO** — 4× `return True` (schedule_utils.py:376/383/392/401: „brak grafiku"/„nie znaleziono"/„brak godzin"/„błąd parsowania") **ZERO log.warning** (zweryfikowane DZIŚ: cztery `return True` bez loggera); (2) `_shift_start_dt`/`_shift_end_dt` **fail-CLOSE→None** (courier_resolver:1252/1269) → floor `max(now,shift_start)` = no-op; (3) feasibility **FAIL12 GŁOŚNO** (`log.warning` „SPRAWDŹ GRAFIK", wzorzec dobry „Z2 anti-silent-failure").
- **Skutek literówki „11.00":** kurier liczony on-shift 24/7 (brak demote/warm-up) **I** floor martwy cicho (shift_start=None) **I** feasibility próbuje NO_ACTIVE_SHIFT — niespójna decyzja per powierzchnia (3 traktowania jednego defektu = I-konflikt + M-cisza).
- **Poprawny wzorzec ISTNIEJE w tym samym systemie** (FAIL12 GŁOŚNO), `is_on_shift` go NIE stosuje (path-asymmetry).
- **Werdykt PLAUSIBLE:** ścieżka konfliktu CONFIRMED z lektury; częstość złych wpisów grafiku = nie zmierzona (oracle).

**STAN DOCELOWY (kontrakt §4.8 + §4.2; most R5 fail-loud):**
1. **§4.8** JEDNA polityka fail dla zepsutego wpisu grafiku (open LUB close), SPÓJNA cross-warstwa (`is_on_shift` ∧ `_shift_*_dt` ∧ feasibility-FAIL12 zgodne). Wybór polityki: per Adrian (fail-open warm-up vs fail-close exclude) — domyślnie fail-LOUD-open zgodny z always-propose + FAIL12, ale UDOKUMENTOWANY.
2. **§4.2 (most R5, fail-loud)** `is_on_shift` fail-open z `log.warning` (jak FAIL12, „Z2 anti-silent") — koniec cichego 24/7. Defekt grafiku = operator-visible.
3. **§4.1 (u źródła)** walidacja wpisów grafiku U ŹRÓDŁA (arkusz Google, GRF-02) — literówka „11.00" łapana przy ingest, nie 3× downstream.

**INWARIANT RUNTIME:** jeden defekt grafiku → jedno spójne traktowanie cross-warstwa + fail-LOUD (INV-COH-4). *Test:* wpis „11.00": `is_on_shift` loguje warning (nie cichy True); `is_on_shift`==`_shift_start_dt`-policy==FAIL12-policy (spójne); walidacja arkusza odrzuca „11.00".

**BRAMKA „ZERO NOWYCH KOPII":** 1 polityka fail + walidacja-u-źródła (−2 niezależne fallbacki) — `is_on_shift` dostaje log.warning (most R5 fail-loud, wspólny mechanizm). NIE 4. niezależny fallback. **`is_on_shift` + walidacja-arkusza DOTYKAJĄ ścieżki-decyzji (schedule→feasibility) → protokół + ACK; ale fail-loud-log = obserwacyjny low-risk start. Wybór polityki open/close = ACK Adriana (brak werdyktu kanonu).**

---

### ▰ PODRODZINA 6 — ADWERSARIALNE / META (oracle-testów)

### R7-I-G — `post-shift-replay-validated-vs-void-ADVERSARIAL` (P2, CONFIRMED, NIE-źródło, OTWARTY) — most R3 (instrument-truth) + R6 (TTL)

**CO DZIŚ (entropia, świeżo zweryfikowane — grep ledgera DZIŚ):**
- **SPRZECZNOŚĆ CROSS-AUDYT:** `post_shift_overrun_forward_replay` = **VALIDATED** (C18: świeży ledger; zweryfikowane DZIŚ `post_shift_overrun_min` **457×/956 linii = OBECNY**) vs **VOID** w `ZIOMEK_ROOTCAUSE_AUDIT_allocation_family` + B13-K-14 (0/282). Świeży grep ROZSTRZYGA: VOID-claims STALE (audyt-analiza nieświeża po F2-fix/unify a8cdb95 29.06). `best_effort_fastest_pickup_shadow` analogicznie void-claimed-ale-żywy.
- **`sequential_replay._determine_verdict` UNTESTED** (sequential_replay.py:742/775): ETAP-5 fleet-gate BEZ testu/żywego wołacza + **hardcoded lower-better = I-inwersja dla celu higher-better** (kierunek werdyktu sprzeczny z celem metryki).
- **Werdykt CONFIRMED, source=NIE:** to META-finding (nieświeża analiza audytu + przyrząd-kierunek), NIE engine-bug. Owns hygiene void-claim + kierunek-przyrządu, nie silnik.

**STAN DOCELOWY (kontrakt §4.5 + §4.8; most R3 instrument-truth + R6 TTL):**
1. **§4.5 (hygiene void-claim, C9/C11 lekcja)** void-claim („instrument VOID/martwy") wymaga ŚWIEŻEGO grepa ledgera (`scripts/logs/shadow_decisions.jsonl` — NIE `dispatch_state/`, trap master-ledger) PRZED zapisem; cross-audyt sprzeczność rozstrzygana re-grepem (VALIDATED na świeżym stanie, VOID-claims wycofane). Most R6: stale-`.txt`/audyt-analiza z timestamp+kadencja (TTL-marker — nieświeża analiza oznaczona).
2. **§4.5 (kierunek przyrządu, INV-COH-7)** `sequential_replay._determine_verdict` kierunek (higher/lower-better) ZGODNY z celem metryki (fail-loud gdy I-inwersja); UNTESTED → test lub etykieta DEAD/dormant.
3. **§4.8** rejestr koherencji: cross-audyt sprzeczność = `defined-inconsistent (meta)`, rozstrzygnięta świeżym grepem.

**INWARIANT RUNTIME:** void-claim ma timestamp+świeży-grep; kierunek przyrządu-werdyktu zgodny z celem metryki (INV-COH-7). *Test:* `post_shift_overrun_min` present w świeżym ledgerze → VALIDATED (nie VOID); `_determine_verdict` higher-better gdy cel higher-better.

**BRAMKA „ZERO NOWYCH KOPII":** konwencja void-claim-z-grepem (most R3) + naprawa kierunku `_determine_verdict` (1 przyrząd) — NIE 4. void-claim na słowo. **NIE-SILNIK (instrument/audyt-hygiene + przyrząd-kierunek) → niskie ryzyko, kandydat wcześniejszej fazy. Most do R3 instrument-truth (wspólna konwencja) + R6 TTL (stale-analiza marker).**

---

## 3. PLAN KONSOLIDACJI (zależnościowo; każdy krok REDUKUJE ≥1 metrykę entropii; bramka „ZERO NOWYCH KOPII")

**Zasada anty-entropii:** konsoliduj-nie-dodawaj; każdy krok ściśle redukuje `unresolved-conflict-count / threshold-scatter / silent-inversion-count / HARD-bez-invariantu / fail-policy-inconsistency / void-claim-bez-grepa` — NIGDY nie dodaje N-tej kopii/łatki/par. **Lekcja przewodnia R7 = „reguły walczą bo precedencja jest prozą-w-kanonie a nie kontraktem-z-egzekutorem; fix = rejestr-precedencji + runtime-inwariant U ŹRÓDŁA, NIE łatka per-ścieżka".** Wszystko dotykające kodu-decyzji = OSOBNY ACK + ETAP 0→7, off-peak, replay ON↔OFF, parytet bliźniaków, pełna regresja `pytest tests/` vs baseline.

> **Kolejność wymuszona naturą R7 + RYZYKIEM + BRAMKAMI CZASOWYMI:** najpierw FUNDAMENT (rejestr koherencji + INV-COH suite = czyni konflikty MIERZALNE), potem NISKIE-RYZYKO obserwacyjne/doc (void-claim hygiene, A3 governor-drift, R-DECLARED tripwire-log, fail-loud schedule-log), potem WYSOKIE-RYZYKO engine sprzężone z BRAMKAMI: **sprint O2 02.07** (R7-I-A + R7-I-B + R15 anchor + calibration — JEDEN sprint, bramkowany bundle_calib-oracle-fix R3), **sprint pre-shift-floor** (R7-I-C + R1 floor-chokepoint), na końcu PENDING-ACK (R7-I-D load D5, R7-I-F polityka fail). Sprzężenia (R15→R1, floor→R1, flag-guard→R1-D/R3, calib→calibration-agent) ruszane Z właścicielem.

### FAZA 0 — FUNDAMENT (czyni koherencję MIERZALNĄ — read/doc-only, brak ACK)
- **S0.1** Spisz **REJESTR KOHERENCJI** (§1) jako żywy artefakt: każda para reguł/flag/ścieżek → `precedence_status` + `runtime-invariant-enforcing-it` + `Adrian-verdict`. *Czyni `unresolved-conflict-count` MIERZALNYM.* Bramka: 0 par bez statusu/etykiety.
- **S0.2** Szkielet suite **INV-COH-1..7** (czerwone-na-start). *Czyni regres precedencji widocznym.* Read/doc-only.
- **S0.3** Etykietuj klasę-flag „inversion-guard" (§1.3) = WSPÓLNE z R3/R1-D rejestr-flag (NIE dubluję — R3 buduje rejestr+fingerprint route/canon/equal-treatment; R7 dokłada marker „OFF=safe vs OFF=policy-revert" + INV-COH-6). Bramka: każda flaga ma marker.

### FAZA 1 — NISKIE RYZYKO obserwacyjne / doc (NIE zmienia werdyktu live; lekki ACK)
- **S1.1 (R7-I-G)** Void-claim hygiene: konwencja „void-claim wymaga świeżego grepa `scripts/logs/shadow_decisions.jsonl`" (most R3 instrument-truth + R6 TTL-marker na stale-analizie); napraw kierunek `sequential_replay._determine_verdict` (higher-better) lub etykieta DEAD. *Redukuje: void-claim-bez-grepa, I-inwersja-kierunku.* Bramka: NIE 4. void-claim na słowo. **NIE-silnik, najwcześniejsza faza.**
- **S1.2 (R7-I-D część-1)** Sprostuj A3 governor efektywny-stan (flags.json:165=true → ON; A2 błędne OFF) + `ENABLE_FLEET_LOAD_GOVERNOR` w fingerprint (most R3 flag-drift); udokumentuj `loadgov_ewma`-karmi-FAR-veto-mimo-OFF. *Redukuje: flag-drift A2-vs-A3.* Doc/rejestr, low-risk.
- **S1.3 (R7-I-E)** R-DECLARED tripwire `czas_kuriera>=czas_odbioru` jako fail-loud LOG (obserwacyjny, NIE reject — zgodne always-propose) w chokepoincie committed. *Redukuje: HARD-bez-invariantu (R-DECLARED dostaje strażnika).* Spełnia INV-COH-3. **Dotyka silnika ale obserwacyjny → ACK lekki; egzekucja=C-DT już ACK.**
- **S1.4 (R7-I-F część-1)** `is_on_shift` fail-open z `log.warning` (most R5 fail-loud, wzór FAIL12) — koniec cichego 24/7. *Redukuje: M-cisza schedule.* Obserwacyjny (log, nie zmiana polityki). **Wybór polityki open/close = ACK Adriana (S3.4).**

### FAZA 2 — SPRINT O2 02.07 (WYSOKIE RYZYKO engine, BRAMKA CZASOWA, sprzężone R1+R3+calib)
> **Wszystko RAZEM (jeden sprint, protokół MAPA KOMPLETNOŚCI) — flip O2 rusza 6 sites R6-cap + 4 sites paczka-exempt + anchor + bundle_calib-oracle. Bramkowane bundle_calib-oracle-fix (R3 C01) + Adrian D3/C5.**
- **S2.1 (R7-I-A część-1, most R3 instrument PRZED engine)** `bundle_calib` overage TIER-AWARE (przestaje kłamać dla T3) — oracle bramkujący flip O2 dostaje wierny pomiar. *Redukuje: arbiter-kłamie (INV-COH-7).* Bramka: replay dowodzi bundle_calib==feasibility-cap dla T3.
- **S2.2 (R7-I-A część-2 + R15 anchor)** `r6_cap_for_tier()` JEDEN helper (35 baza / 40 alarm-gated) konsumowany przez feasibility+best_effort+O2+bundle_calib (most R15: na wspólnym `r6_thermal_anchor`). *Redukuje: threshold-scatter 6→1, R6-cap defined-inconsistent→consistent.* Bramka: 1 helper + N importów, 0 bare-kopii.
- **S2.3 (R7-I-B + R15)** paczka-exempt w JEDNYM anchor-helperze → auto-spójny feasibility+SLA-count+O2-sweep; 4. site (`_compute_per_order_delivery_minutes`) Z flipem O2 (C3 coupling). *Redukuje: exempt-inversion (INV-COH-5).* Bramka: golden A≡B na paczce.
- **S2.4 (R7-I-A część-3, co-design calibration)** quantile-recovery rozstrzygnięty ŁĄCZNIE: USUŃ (D3) + skalibruj prędkość gold na osi POŚLIZGU-ODBIORU (calibration-agent, NIE delivery-pesymizm) ALBO obwaruj trybem ALARM. *Redukuje: HARD-softening sprzeczny z D3.* **ACK Adriana D3 (kierunek już ACK kanonem).**
- *Bramka sprintu:* replay ON↔OFF dowodzi BEZ-regresji + parytet 4 bliźniaków (feasibility↔best_effort↔O2↔bundle_calib) + paczka golden + pełna regresja. **Najwyższa materialność (bramkuje datę 02.07).**

### FAZA 3 — SPRINT PRE-SHIFT-FLOOR (WYSOKIE RYZYKO engine+cross-repo, sprzężone R1)
> **R7-I-C precedencja Z R1 floor-chokepoint — Adrian Q1/Q2/Q2b już ACK.**
- **S3.1 (R7-I-C + R1 ROOT6)** JEDEN chokepoint `effective_pickup_at=clamp_order(committed_frozen, shift_start_floor, osrm, debias)` (R1 owns ekstrakcję; R7 owns precedencję frozen>floor + floor-obejmuje-frozen<shift_start + Q2 feasibility-wyklucza-niezdążającego). Render apka+konsola czytają chokepoint; debias dożywa do żywego eta. *Redukuje: 4-clampy-bez-precedencji→1-chokepoint, czas-odbioru undefined→consistent.* **Bliźniaki RAZEM** (konsola↔apka floor C20; plan_recheck regen most R1 K2). Bramka: silnik∧konsola∧apka golden `effective_pickup_at`.
- **S3.2 (R7-I-C część-2, most R1-D)** floor-flagi (`PRE_SHIFT_DEPARTURE_CLAMP`/`CLAMP_PRESHIFT`) = inversion-guard (const-default zgodny z floor-ON + fingerprint — INV-COH-6). *Redukuje: silent-revert floor.*
- *Bramka:* replay + parytet cross-repo (silnik↔konsola↔apka) + Adrian Q1/Q2/Q2b (już ACK).

### FAZA 4 — PENDING-ACK Adriana (NIE ruszać silnika bez werdyktu; measure-first)
- **S4.1 (R7-I-D część-2, D5)** Triple-tax load: oracle „ile razy potrójna kara odbiera zlecenie LEPSZEMU obciążonemu" → liczby → **ACK Adriana (rekalibracja vs jeden rządzący)**. Do werdyktu: para etykietowana PENDING. Po ACK: JEDNA reguła load rządzi + 1 `bag_cap` tabela. *Redukuje: load undefined→rozstrzygnięte.*
- **S4.2 (R7-I-F część-2)** Wybór polityki fail grafiku (open warm-up vs close exclude) = **ACK Adriana** (brak werdyktu kanonu) + walidacja-u-źródła arkusza. *Redukuje: fail-policy 3-way→1.*

**Metryki wyjścia (zielone = rodzina R7 domknięta):** `unresolved-conflict-count (undefined/defined-inconsistent/silent-inversion bez etykiety) = 0` · `R6-threshold-scatter = 1 helper` · `paczka-exempt-inversion = 0 (spójny wszystkie warstwy)` · `czas-odbioru-clampy = 1 chokepoint` · `HARD-bez-runtime-invariantu (R-DECLARED) = 0` · `schedule-fail-policy = 1 spójna + fail-loud` · `void-claim-bez-grepa = 0` · `flaga-inversion-guard bez markera = 0` · suite INV-COH-1..7 ZIELONA.

---

## 4. POKRYCIE / LUKI / ADWERSARIALNE (jawne, nie cisza)

**Zweryfikowane świeżym grep/danymi DZIŚ (HEAD `8024705`):** `BAG_TIME_HARD_MAX_MIN=35` (common.py:763 bare) · `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN=40` (common.py:2651 + flags.json:205) · `O2_OVERAGE_CAP_MIN/O2_CAP_Z_MIN=35` (common.py:2661-2662) · `R6_MAX_MIN=35.0` (bundle_calib:56) · ETA_QUANTILE feasibility:1089 (flags.json:179=true) · paczka-exempt feasibility:1050-1055/1080/1105/1152 (flags.json:183=true) · R-DECLARED runtime-gate `grep`=∅ (tylko komentarze) · `ENABLE_FLEET_LOAD_GOVERNOR` const `"0"` common.py:2103 ↔ **flags.json:165=true (effective ON)** · `ENABLE_V326_FLEET_LOAD_BALANCE` common.py:2238 · `is_on_shift` 4× `return True` BEZ log (schedule_utils:376/383/392/401) · `_shift_start_dt`/`_shift_end_dt` courier_resolver:1252/1269 · `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP` const `"0"` common.py:2006-2007 ↔ flags.json:141=true · `r6_thermal_anchor` route_sim:663 · FROZEN_PICKUP_WINDOW route_sim:1086 · `post_shift_overrun_min` **457×/956 ledger** (VALIDATED, refutuje VOID-claims).

**LUKI (jawne):**
1. **Wartości LICZBOWE rozjazdu** (ile worków ma SLA-count≠R6-anchor; ile paczek przecieka do O2-rankingu; ile committed<shift_start dziennie; ile RAZY triple-tax load odbiera zlecenie LEPSZEMU; ile gold>35 przepuszcza quantile) — NIE policzone (read-only inwentarz; to Faza C oracle/replay). Deklaruję ŚCIEŻKĘ konfliktu z lektury+flagi-effective, nie MAGNITUDĘ. To napędza materialność R7-I-A/C/D/F i flip-no-flip O2 02.07.
2. **`courier_api_panelsync` martwy fork** (665L) — NIE re-grepowałem frozen/floor/R6 w nim (A6 DEAD, nie serwowany; usuwany przy R1 PoC). Bliźniak floor-audit #14/#15.
3. **Most paczki (parcel lane 900M+id)** — czy `parcel_lane_merge`/`parcel_assign` mają własną ścieżkę frozen/floor/R6/anchor — NIE prześwietlone (A6 luka #2). PACZKA_ADDRESS_IDS pokrywa firmowe, nie parcel-lane.
4. **Apka Kotlin (`RouteLogic.kt`)** lokalny re-clamp/ETA — GRANICA cross-repo (render serwerowy courier_api pokryty); lokalna kopia NIE czytana (A6 luka #1) — dotyczy R7-I-C precedencji apki.
5. **`systemctl show -p Environment` per-serwis** dla efektywnego stanu flag (FROZEN_PICKUP_ETA/CLAMP_PRESHIFT/governor per dispatch-shadow vs plan-recheck vs panel-watcher) — cytuję A3/flags.json effective, NIE re-mierzyłem per-proces (most R3/R14 flag-3-layer). R7-I-D governor-drift = przykład.
6. **P-1..P-7 dokładne numery** świadomych inwersji (`ziomek-full-rule-audit-2026-06-24`) — NIE w zasięgu; D03 §3 mapuje kandydatów (no-GPS=równo C3 najgroźniejsze), nie pełną mapę P-numerów. R7-I-C floor = kandydat cichej-inwersji-na-reset.

**ADWERSARIALNE (uczciwie — przeciw moim własnym wnioskom):**
- **R7-I-G post-shift = VALIDATED, NIE VOID (potwierdzone DZIŚ):** świeży grep `post_shift_overrun_min` 457×/956 = OBECNY → VOID-claims allocation-audit STALE. To META-finding (`source=NIE`) — NIE engine-bug, owns hygiene void-claim, nie silnik. NIE nadinterpretować jako „przyrząd martwy".
- **R7-I-D FAR-veto / equal-axis = HARM REFUTED** (faE refuter): `v325_pre_shift_far_veto_kept`=18 rek., WSZYSTKIE PROPOSE (0 KOORD/stranded); 12× poprawnie zdemotowany pod lepszego REALNEGO, 6× jedyna opcja proponowany. „Usuń −1000" (opcja A) = NET-SZKODLIWA; opcja B (udokumentuj) JUŻ zrobiona (§7-T4). Load/equal-axis interakcja = LATENT debt, NIE aktywny P1 harm. R7-I-D triple-tax = measure-first (D5), nie zakładać harm.
- **R7-I-A quantile-recovery = ACK-owane 14.06 jako recovery false-reject** (gated gold≤4) — narusza P0 „SOFT-nie-osłabia-HARD" w LITERZE, ale ŚWIADOME; D3 chce USUNĄĆ ale „skalibruj prędkość ZANIM" → NIE flip-bez-kalibracji (gold false-reject). To `defined-inconsistent z kanonem` (świadome rozluźnienie do likwidacji/obwarowania), NIE cichy bug.
- **R7-I-C/R7-I-F = PLAUSIBLE nie CONFIRMED:** ścieżka konfliktu CONFIRMED z lektury+flagi, ale MATERIALNOŚĆ (magnituda) = oracle Fazy C. Precedencja docelowa R7-I-C = już ACK Adrian (Q1/Q2/Q2b); R7-I-F polityka = PENDING ACK (brak werdyktu).
- **Precedencja „defined-inconsistent" ≠ zawsze bug:** część par to ŚWIADOME asymetrie (R6 35-HARD vs 40-best_effort always-propose = tier-aware z projektu; R1 zsoftowany świadomie). Rozróżniam: `defined-inconsistent-z-kanonem` (kod łamie werdykt = fix) vs `defined-inconsistent-świadome` (asymetria z ACK = udokumentuj+egzekwuj, nie „napraw"). R7-I-A 40-per-ścieżka = pierwsze (C5 mówi alarm-only); R7-I-D load = drugie (D5 pending).

**NIE-luki (świadomie poza R7):** ekstrakcja anchor-helpera (R1/R15 — TYLKO precedencja+sites moja), floor-chokepoint ekstrakcja (R1 ROOT6 — TYLKO uszeregowanie clampów moje), geometria-w-selekcji (R2 P0-A), equal-treatment stos+FAR-veto (R1/D04 — most do R7-I-D load-axis), flag-rejestr-migracja+fingerprint (R3/R1-D — TYLKO INV-COH-6 marker mój), bundle_calib instrument-truth rdzeń (R3 — TYLKO tier-aware-overage część `r6_cap_for_tier`), oś-kalibracji poślizg-odbioru (calibration-agent — co-design quantile), nazwa-vs-zachowanie VETO/HARD_GATE (R4 semantyka), sentinele-produkcja K5 (sentinel-agent — most R7-I-F schedule), Mailek/Papu (granica).
