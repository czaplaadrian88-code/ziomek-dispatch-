# D03 — GRAF KONFLIKTÓW REGUŁ/FLAG (oś I) + sprzężenia flag (I4) + świadome inwersje P-1..P-7 (I5)

**Lane D · agent D03-flag-coupling-inversions · sesja tmux 2 · 2026-06-30 · READ-ONLY (zero edycji/flipów/restartów).**
**Wejście:** A2_rule_registry + A3_flag_registry_effective + ziomek-change-protocol (P-1..P-7, wzorce #1-#18, C2/C3/C9) + ZIOMEK_REGULY_KANON (§1 tabela rozstrzygania, §5 C1-C7/C-DT, §7 T1-T5, §9 L1-L6/M1-M2).
**Wszystkie `plik:linia` z ŚWIEŻEGO grepu DZIŚ (HEAD `8024705`).** Linie dryfują (≥3 żywe sesje) — re-grepuj.

**Czego TO jest:** dla każdej pary reguł/flag wchodzących w konflikt: `rule_a` ↔ `rule_b` | natura (inwersja HARD↔SOFT / sprzeczność / niezdefiniowana-precedencja / sprzężenie-flag / cicha-inwersja-P) | precedence_status (defined-consistent / defined-inconsistent / undefined / silent-inversion / ok) | dowód `plik:linia`. To OŚ, której poprzednie audyty (allocation_family, preshift-floor) NIE miały jako osobnej osi — one mapowały ŹRÓDŁA (K1-K7), ja mapuję INTERAKCJE.

---

## 0. TL;DR — 4 twarde wnioski osi I

1. **Najgroźniejsza klasa = CICHA INWERSJA-P PRZEZ RESET (I5).** Świadome decyzje Adriana („no-GPS = równo" C3, „pre-shift floor", „pin pre-shift ETA") są egzekwowane flagami, których **kod-default = polityka PRZED inwersją** (dyskryminuj / brak floor). `flags.json` reset/utrata → `decision_flag` spada na `const=False`/`env "0"` → **świadoma inwersja CICHO się cofa, zero alarmu.** To odwrotność „bezpiecznego ETAP4 fallback OFF": dla rodziny equal-treatment/clamp OFF = polityka-ZŁA, nie polityka-bezpieczna.
2. **Jedyna PRAWDZIWA inwersja maskująca json↔const = `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE`** (json `False` maskuje const `True`, common.py:2806). Usunięcie klucza = cichy FLIP gate ON = utrata ALWAYS-PROPOSE. Potwierdzone A3 §2b, re-grep dziś.
3. **HARD-softening NA ŻYWO sprzeczne z kanonem: `ENABLE_ETA_QUANTILE_R6_BAGCAP` (effective TRUE)** rozluźnia HARD R6=35 dla gold p80 (feasibility_v2:1089) — **wprost przeciw werdyktowi D3** („35 dla KAŻDEGO bez wyjątków, USUŃ recovery gold≤4"). Flaga ON wygrywa z kanonem = `defined-inconsistent`.
4. **Sprzężenia-maskujące (I4):** `ALWAYS_PROPOSE` maskuje 4 gałęzie KOORD; `OR_TOOLS↔GROUPING` (flip jednej = double-insert); `R6_DANGER_ZONE`(ON, poza fingerprint)↔`R6_SOFT_PEN_CAP`(flags.json) (bez capa −240000 na zombie); `CARRIED_FIRST_RELAX` OFF-shadow/ON-reszta (złamany parytet 4 procesów); `FEAS_CARRY_READMIT`↔`BEST_EFFORT_OBJM_R6_KEY` (bliźniacze re-admit). Każde = mina C2/C3 na flipie.

---

## 1. GRAF KONFLIKTÓW — TABELA GŁÓWNA (oś I)

> Kolumna **STATUS**: `def-cons`=defined-consistent (rozstrzygnięte i spójne), `def-incons`=defined-inconsistent (rozstrzygnięte ale kod/flaga łamie), `undef`=undefined (brak rozstrzygnięcia precedencji), `silent-P`=silent-inversion (reset cofa świadomą decyzję), `ok`=brak realnego konfliktu (np. tylko nazwa myli).

| # | rule_a | rule_b | natura | STATUS | dowód (świeży plik:linia) |
|---|---|---|---|---|---|
| **I-01** | no-GPS/pre_shift = RÓWNO (HARD-zasada Adrian C3) | pozycja-zależna selekcja/trasa (kod-default = dyskryminuj) | sprzężenie-flag + **cicha-inwersja-P** | **silent-P** | reguła trzymana 3 flagami: `NO_GPS_EQUAL_TREATMENT` env-default `"0"` (common.py:1108), `EQUAL_TREATMENT_BUCKET` `"0"` (common.py:1112), `PRE_SHIFT_EQUAL_NO_PENALTY` `False` (common.py:264); ON tylko w flags.json. 2 różne flagi konsultowane w 2 funkcjach: `_selection_bucket`→`_equal_bucket_on()` (dispatch_pipeline.py:2459) vs `_is_demotable_blind_empty`→`_equal_bucket_on`+`_no_gps_equal_on` (:2475/:2393). Reset flags.json → wszystkie 3 OFF → demote wraca. |
| **I-02** | `PRE_SHIFT_EQUAL_NO_PENALTY` (zeruje karę pre-shift) | `V325_PRE_SHIFT_SOFT_PENALTY=-20` (stała ŻYWA w kodzie, T4) | sprzężenie-flag + cicha-inwersja-P | **silent-P** | const `-20` common.py:1975; metryka pisana ZAWSZE feasibility_v2:763; konsumpcja w score gated `_apply_pre_shift_equal_gate` (dispatch_pipeline.py:2413→5108), **default OFF = no-op → kara −20 AKTYWNA dopóki flaga nie ON**. Flaga OFF/reset → −20 wraca przeciw regule „równo". |
| **I-03** | R6 = 35 dla KAŻDEGO (HARD, werdykt D3 „bez wyjątków") | `ENABLE_ETA_QUANTILE_R6_BAGCAP` (gold p80 >35 przechodzi) | **inwersja HARD↔SOFT** (flaga rozluźnia HARD) + sprzeczność-z-kanonem | **def-incons** | feasibility_v2.py:1089 `if C.flag("ENABLE_ETA_QUANTILE_R6_BAGCAP")…` → R6 na skalibrowanej p80 (komentarz :1084 „odzysk false-rejectów"); const `False` common.py:236, flags.json `True` → **effective TRUE** (A3 §2d ON-list). KANON D3: „USUŃ recovery gold≤4". Flaga ON = gold dostaje >35 = łamie kanon. |
| **I-04** | SLA-gate anchor = `pickup_at` (HARD, feasibility) | R6-thermal anchor = `pickup_ready_at` (HARD, route_simulator) | **sprzeczność** (2 HARD-bramki tej samej decyzji, RÓŻNA kotwica) | **undef** | `_count_sla_violations` route_simulator_v2.py:635 (ready) + SLA-loop feasibility_v2.py:~1156 (pickup_at) + `r6_thermal_anchor` :663 (ready). Split-brain T2/B1 (KANON §7,§9). Brak rozstrzygnięcia która kotwica wygrywa → O2 review 02.07. |
| **I-05** | `PACZKA_R6_THERMAL_EXEMPT` (3 HARD-site, paczka pomija R6/SLA) | `_count_sla_violations` kopia A (BEZ exempt) | sprzeczność/asymetria-bliźniaków | **def-incons** | exempt w feasibility_v2:1051 (termik) + :1152 (SLA-bramka); BRAK w route_simulator `_count_sla_violations` + BRAK w O2 (protokół C-Załącznik B „4. site na flipie 02.07"). Paczka liczona różnie per powierzchnia. |
| **I-06** | `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` flags.json=`False` | const env-default=`True` (common.py:2806) | **cicha-inwersja** (json maskuje const) | **silent-P** | const `"1"` common.py:2805-2806; flags.json `False` → effective False (A3 §2b). **Usunięcie klucza z flags.json → `decision_flag` spada na const True → gate FLIP ON → KOORD-redirect wraca, utrata ALWAYS-PROPOSE.** Jedyna prawdziwa inwersja maskująca json↔const w całym rejestrze. |
| **I-07** | `ENABLE_ALWAYS_PROPOSE_ON_SATURATION` (ON) | 4 gałęzie werdyktu KOORD (commit-div / difficult-case / geometry-blind / best_effort-KOORD) | **sprzężenie-flag** (always-propose MASKUJE gate'y) | **def-cons** (świadome) ⚠ mina-flip | `not _always_propose_on()` bramkuje KOORD na dispatch_pipeline.py:6491/6864/6900/6926; `_always_propose_on` :2638. Flip always-propose OFF → 4 uśpione gate'y KOORD budzą się (C2/C3 mina). |
| **I-08** | `ENABLE_V326_OR_TOOLS_TSP` (ON) | `ENABLE_V326_SAME_RESTAURANT_GROUPING` (ON) | **sprzężenie-flag** (wzorzec #13: flip jednej = double-insert) | **def-cons** ⚠ mina-flip | oba env-default `"1"` (common.py:2356, :3159), **NIE w flags.json, NIE w ETAP4, NIE w fingerprincie** (A3 §4). Grouping karmi OR-Tools TSP; rollback OR_TOOLS OFF bez GROUPING OFF → double-insert super-pickupa w greedy/bruteforce. C3 wymaga flip-w-parze. |
| **I-09** | `ENABLE_R6_DANGER_ZONE_PENALTY` (kara −24/min strefa 32-35, ON, POZA fingerprint) | `ENABLE_R6_SOFT_PEN_CAP` (cap, default-False, kanon=flags.json) | **sprzężenie-flag** (bez capa kara eksploduje) | **def-incons** (mina L2+L6) | DANGER_ZONE getattr env-const `"1"` common.py:774-775, używane przez `getattr` dispatch_pipeline:4234/6102 → **NIE `C.flag` = poza `flag_fingerprint`, operator nie wyłączy**; cap const `False` common.py:784, gated `C.flag("ENABLE_R6_SOFT_PEN_CAP")` dispatch_pipeline:4230. Bez capa `r6_soft_pen` do −240000 na zombie-pickup (KANON L6). |
| **I-10** | `R_RETURN_TO_RESTAURANT_VETO` (nazwa=VETO, feasibility = METRIC-ONLY) | `NO_RETURN_TO_DEPARTED_PICKUP` (kanon = realny zakaz HARD) | **sprzeczność** (nazwa-HARD vs zachowanie-SOFT, split-enforcement) | **def-incons** (D4 todo) | feasibility_v2.py:905-908 `detect_return_to_restaurant` → tylko metryka (komentarz „NIGDY nie przerywa feasibility"); realny veto plan_recheck.py:1519 `if ENABLE_NO_RETURN_TO_DEPARTED_PICKUP`. KANON D4: „HARD w trasie; zdejmij mylące VETO z nazwy soft-kary". |
| **I-11** | P-1: carried-first front-load (SOFT, kanon trasy) | R27/R-DECLARED committed-window (HARD) | **inwersja HARD↔SOFT** (cicha-inwersja-P P-1) | **def-incons** (fix env-frozen) | carried-first front PRZED committed-sort (plan_recheck), 110 odbiorów/dz po oknie (full-rule-audit P-1). Fix = `ENABLE_LEX_COMMITTED_WINDOW` APPLY (plan_recheck.py:458, env-default `"0"`, **drop-in ON, NIE w flags.json/fingerprincie**). KANON §1: carried-first wygrywa DOPÓKI ≤limit; committed nietykalny max 5min (C6). |
| **I-12** | P-2: R6=35 (HARD reject `verdict=NO`) | ALWAYS-PROPOSE (selekcja best_effort + sentinel) | **inwersja HARD↔SOFT** (cicha-inwersja-P P-2) | **def-cons** (świadome) | 20,6% propozycji łamie R6 (full-rule-audit P-2); best_effort fallback ZAWSZE obecny; KOORD tylko early-bird/czasówka ≥60min. Świadoma polityka Adriana (KANON §4 „sentinel = OK"). Dług = banner UX, nie kod. |
| **I-13** | `ENABLE_FEAS_CARRY_READMIT` (feasible-path, OFF) | `ENABLE_BEST_EFFORT_OBJM_R6_KEY` (best-effort-path, ON) | **sprzężenie-flag**/asymetria-bliźniaków | **def-incons** | bliźniacze ścieżki re-dopuszczenia carry-inclusive (A3 §4): feasible readmit OFF (rolled-back hot), best-effort ON. Protokół: ruszać RAZEM (MAPA KOMPLETNOŚCI). Dziś asymetryczne. |
| **I-14** | `CARRIED_FIRST_RELAX` OFF w `dispatch-shadow` | `CARRIED_FIRST_RELAX` ON w plan-recheck/panel-watcher/b-route | **sprzężenie-flag**/asymetria-procesów | **def-incons** (złamany parytet) | A3 §1d: shadow `(brak→OFF)`, plan-recheck=1, panel-watcher=1 (KANON §5.5/§6 „OFF w dispatch-shadow — złamany parytet"). Scoring shadow widzi inny relax niż kanon → propozycja-trasa ≠ finalny kanon. |
| **I-15** | objm `lex_qual` 4-krotka (canon, post-shift-aware) | `_objm_lexr6_shadow._lex_qual` 3-krotka (FROZEN) | **sprzężenie-flag** (cień przestanie odbijać selektor) | **silent-P/fragile** (L3/T3) | canon prepend `post_shift_overrun_penalty` gdy `ENABLE_POST_SHIFT_OVERRUN_PENALTY` (objm_lexr6.py:44-46); frozen shadow ZAWSZE 3-krotka (dispatch_pipeline ~:1122). Zgodne TYLKO bo flaga OFF (wiodące 0.0 no-op). Flip C7 → shadow kłamie (kłamiący przyrząd, klasa E). |
| **I-16** | pre-shift floor: feasibility HARD clamp (`PRE_SHIFT_DEPARTURE_CLAMP` ON) | plan_recheck regen co 5min = BRAK floor (K2) | sprzeczność/asymetria-bliźniaków | **def-incons** | clamp feasibility_v2:789-819 (ON); plan_recheck `_start_anchor`/`_earliest_committed_pickup_anchor` BEZ shift_start → „leak odclampowuje co 5min" (A6 grupa6 #5, preshift-floor-audit). 17 powierzchni, 4 floor. |
| **I-17** | R-10 `V326_FLEET_LOAD_BALANCE` (ON) | SP-B2 `FLEET_LOAD_GOVERNOR` (OFF) | sprzeczność (2 reguły load tego samego pojęcia) | **ok** dziś / **undef** na flip | balance env `"1"` common.py:2238 (LIVE); governor env `"0"` common.py:2103 (OFF). Dziś tylko balance aktywny. Re-enable governor bez koordynacji = podwójna kara load. |
| **I-18** | 3-4 PODATKI OBCIĄŻENIA stackują (D5): R9-stopover + BUG4-cap + loadgov (+OVERLOAD) | LEPSZY-obciążony-kurier ma dostać zlecenie | sprzeczność/rozsyp-progów (N) | **undef** (czeka werdykt Adrian) | `bonus_r9_stopover` −8×stopy (dispatch_pipeline:4330), `bonus_bug4_cap_soft` (:1721), `bonus_loadgov_shadow_delta` −40 (:4999), scoring `OVERLOAD_PENALTY` (scoring:249). KANON D5: „measure-first ile razy potrójna kara odbiera zlecenie LEPSZEMU obciążonemu" — nierozstrzygnięte. |
| **I-19** | `LATE_PICKUP_HARD_GATE` (nazwa=HARD) | rzeczywista warstwa = SELEKCJA-tier (NIE hard-reject) | sprzeczność-nazwy (L słownictwo) | **ok** (myli, brak realnego konfliktu) | common.py:2822 env `"1"` ON; zachowanie tier dispatch_pipeline:4615 (komentarz :4569 „tiering NIE hard-reject"). Ryzyko: nowa sesja potraktuje jako bramkę. |
| **I-20** | R-DECLARED-TIME (HARD, `czas_kuriera ≥ gotowość`) | R6 (gdy późny odbiór grozi dostawą >35) | inwersja-pozorna → rozstrzygnięta | **def-cons** | KANON §1 + werdykt C-DT: **R-DECLARED-TIME nadrzędne** (nie kłam o czasie); R6-breach → propozycja przesunięcia odbioru do restauracji (≥15min, wyjątkowo ≥10min przed). Spójne, rozstrzygnięte. |

---

## 2. I4 — MAPA SPRZĘŻEŃ FLAG „CO-MASKUJE-CO" (co odsłoni/uzbroi flip)

> Dla każdej flagi: co jej flip ODSŁANIA (uśpiony defekt) lub COFA (świadomą decyzję). To wejście do C2 (flip=pełny deploy) + C3 (flip w parach). „MASKUJE" = trzyma defekt/gałąź uśpioną; „TRZYMA" = jedyny powód że polityka działa.

| Flaga (stan) | Co MASKUJE / TRZYMA | Klasa | Konsekwencja flipu/resetu |
|---|---|---|---|
| `ENABLE_ALWAYS_PROPOSE_ON_SATURATION` (ON) | 4 gałęzie KOORD: commit-divergence-redirect, difficult-case-redirect, geometry-blind-fallback, best_effort→KOORD | I4 | OFF → 4 uśpione KOORD-redirect budzą się jednocześnie (dispatch_pipeline:6491/6864/6900/6926). |
| `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` (flags.json False) | const `True` (common.py:2806) — maskowanie odwrócone | I4+M | **Usunięcie KLUCZA z flags.json** (nie flip) → spada na const True → gate ON → KOORD wraca. Kruche na czyszczenie flags.json. |
| `ENABLE_V326_OR_TOOLS_TSP` (ON) | grouping double-insert super-pickupa w greedy/bruteforce (wzorzec #13) | I4 | OFF bez `SAME_RESTAURANT_GROUPING` OFF → double-insert. **Flip ZAWSZE w parze** (C3). Oba poza fingerprintem → parytet ręczny. |
| `ENABLE_R6_SOFT_PEN_CAP` (flags.json kanon; const False) | eksplozję `r6_soft_pen` do −240000 generowaną przez `R6_DANGER_ZONE_PENALTY` (−24/min) na zombie-pickup | I4 (L6) | Reset flags.json / brak klucza → cap znika → DANGER_ZONE bez ograniczenia. DANGER_ZONE sam poza fingerprint (getattr) → operator nie wyłączy źródła. |
| `ENABLE_NO_GPS_EQUAL_TREATMENT` + `EQUAL_TREATMENT_BUCKET` + `PRE_SHIFT_EQUAL_NO_PENALTY` (3× ON, flags.json) | starą politykę dyskryminacji pozycji (demote no_gps + bucket-2 + kara −20) | I4 (L1)+I5 | TRÓJCA sprzężona semantycznie (A3 §4). Reset → wszystkie 3 OFF → 3 mechanizmy dyskryminacji wracają RAZEM. Miss-1 (np. tylko BUCKET ON) → częściowa dyskryminacja (mina L1). |
| `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP` + `CLAMP_PRESHIFT_PICKUP_ETA`(konsola) (ON) | brak floor odbioru przed startem zmiany | I5 | Reset → floor znika → odbiory pre-shift sprzed startu (case 10:59 vs 11:00). CLAMP konsoli = env-ON (osobny proces). |
| `ENABLE_ETA_QUANTILE_R6_BAGCAP` (ON) | rozluźnienie HARD R6 dla gold p80 | I4 (sprzeczne z D3) | OFF → gold wraca do hard-35 (zgodne z D3, ALE D3 mówi „skalibruj prędkość gold ZANIM usuniesz cap"). Flip bez kalibracji speed → gold false-reject. |
| `ENABLE_OBJM_LEXR6_SELECT` (ON) | uśpioną inwersję P-4 (demote→tier-sort bez bucketa) | I4 (P-4) | full-rule-audit P-4: „ożyje z bugiem przy flip OBJM_LEXR6". Częściowo zmityzowane equal-treatment, ale frozen `_lex_qual` (I-15) wciąż 3-krotka. |
| `ENABLE_CARRIED_FIRST_RELAX` (ON plan-recheck/panel-watcher/b-route, OFF dispatch-shadow) | rozjazd scoring-shadow vs kanon | I4+B | Parytet 4 procesów ręczny (poza fingerprint, M1). Wyrównanie shadow=ON może zmienić propozycje. |
| `ENABLE_LEX_COMMITTED_WINDOW` (APPLY, drop-in ON) | inwersję P-1 (carried-first > committed) | I5 (P-1) | Reset drop-inu → P-1 wraca (carried-first znów bije committed). Poza flags.json+fingerprint. |
| `ENABLE_FEAS_CARRY_READMIT`↔`ENABLE_BEST_EFFORT_OBJM_R6_KEY` | bliźniacze re-admit (jeden OFF jeden ON) | I4+B | Flip readmit ON bez audytu best-effort = podwójne re-admituowanie carry-inclusive. |

---

## 3. I5 — ŚWIADOME INWERSJE P-1..P-7: KTÓRE MOŻE CICHO COFNĄĆ RESET

**Definicje P-1..P-7** (z `ziomek-full-rule-audit-2026-06-24`): świadome decyzje projektowe, gdzie SOFT/polityka celowo odwraca naiwną precedencję. Przykazanie #0: „nie cofaj inwersji P-1..P-7 bez ACK". **Pytanie osi I5: która z nich jest trzymana flagą, której kod-default = STAN PRZED inwersją → reset = ciche cofnięcie.**

| Inwersja | Co inwertuje | Flaga trzymająca | Kod-default | Reset cofa? | Severity |
|---|---|---|---|---|---|
| **P-1** carried-first > committed-window | SOFT carried bije HARD R27/R-DECLARED | `ENABLE_LEX_COMMITTED_WINDOW` (APPLY) = FIX inwersji; carried-first sam = drop-iny | APPLY: env `"0"` (plan_recheck:458), **drop-in ON**, NIE w flags.json/fingerprint | **TAK** — reset drop-inu → fix znika → P-1 (carried>committed) wraca | **P2** |
| **P-2** R6=35 SOFT na werdykcie | ALWAYS-PROPOSE neutralizuje HARD-reject KOORD | `ENABLE_ALWAYS_PROPOSE_ON_SATURATION` + best_effort baked | ON (flags.json/ETAP4); best_effort fallback baked-in | częściowo (best_effort baked zostaje; KOORD-redirect wraca) | P3 (świadome) |
| **P-3** greedy/bruteforce ślepy na okno/R6 | — | NO-GO (kosmetyczny, full-rule-audit pomiar) | — | n/d | — |
| **P-4** demote→tier-sort inwersja | uśpiona, budzi się na flip OBJM_LEXR6 | sprzężona z `ENABLE_OBJM_LEXR6_SELECT` (ON) + equal-treatment | equal-treatment env `"0"` (mityguje) | **TAK** — reset equal-treatment → P-4 budzi się z bugiem | P2 |
| **P-5** recanon-on-write (cancel-symetria) | fix asymetrii 4 handlerów | `ENABLE_RECANON_ON_WRITE` (panel-watcher drop-in ON) | env `"0"`, drop-in ON, tylko panel-watcher | **TAK** — reset → cancel-path bez recanon wraca | P2 |
| **P-6** SLA-gate ≈ duplikat R6 | asymetryczny pre-existing bypass | (warstwa B carry-blind, osobny temat) | — | pośrednio | P3 |
| **P-7** bonus_penalty_sum 19 termów | higiena (bit-identyczny) | baked (`bonus_penalty_terms` dict) | — | NIE (refaktor bez zachowania) | — |
| **(C3) no-GPS = równo** (29.06, post-P) | „demote no_gps" → „równo" | `NO_GPS_EQUAL_TREATMENT`+`EQUAL_TREATMENT_BUCKET`+`PRE_SHIFT_EQUAL_NO_PENALTY` | wszystkie env `"0"`/`False` | **TAK — NAJGROŹNIEJSZE** (3 flagi, reset = 3 dyskryminacje wracają) | **P1** |

**WNIOSEK I5:** inwersje cicho-cofnięte przez reset flags.json/drop-in = **P-1, P-4, P-5, oraz post-P „no-GPS=równo" (C3) — TA OSTATNIA NAJGORSZA** (3 sprzężone flagi, kod-default = pełna dyskryminacja, HARD-zasada Adriana). Mechanizm: `decision_flag()`/`os.environ.get(…,"0")` spada na kod-default gdy klucz znika; dla tych flag kod-default = polityka SPRZED świadomej inwersji. **Strukturalny brak bezpiecznika:** `flag_fingerprint()` widzi 63/≥90 flag — route/canon + equal-treatment-część poza nim (A3 §7, KANON M1) → reset NIE zostanie złapany porównaniem fingerprintów.

---

## 4. KONTRAST: ETAP4 „bezpieczny OFF" vs INWERSJE „groźny OFF"

Rdzeń niespójności precedencji na poziomie META:

- **ETAP4 wzorzec (≈40 flag):** const `False` = bezpieczny fallback; flags.json `True` = kanon. Reset → OFF = **bezpiecznie** (shadow-first, zero wpływu). To zaprojektowane (A3 §2a).
- **Rodzina inwersji (equal-treatment, clamp, lex-committed, recanon):** const/env `False` = **polityka-ZŁA** (dyskryminuj / brak floor / carried>committed). Reset → OFF = **cofnięcie świadomej decyzji Adriana, cicho**.

**To ten sam mechanizm `decision_flag` z PRZECIWNYM znaczeniem OFF.** Brak markera „ta flaga: OFF=safe vs OFF=policy-revert". Operator/conftest/reset traktuje wszystkie jednakowo → mina. Kandydat docelowy (Faza F): klasa flag „inversion-guard" — kod-default = polityka-PO-inwersji (nie przed), albo runtime-inwariant na te decyzje (np. assert „no_gps nie ma gorszego bucketa niż informed gdy equal-rule deklarowana").

---

## 5. POKRYCIE (coverage_declared) — co zbadałem oś I

**Reguły × konflikt (zbadane):** R6/R-35MIN (×ETA_QUANTILE, ×SLA-anchor, ×paczka-exempt, ×always-propose, ×danger-zone/soft-cap), R-DECLARED-TIME (×R6 C-DT, ×carried-first P-1), R27 committed (×carried-first, ×re-sekwencja C6), pozycja-równość (×3-flag-coupling, ×−20-penalty, ×P-4), R-RETURN-VETO (×NO_RETURN kanon), R-LATE-PICKUP (nazwa), R-10/loadgov (×D5 triple-tax), always-propose (×4 KOORD), pre-shift-floor (×plan_recheck K2).
**Flagi × sprzężenie (zbadane):** COMMIT_DIVERGENCE (json↔const), OR_TOOLS↔GROUPING, DANGER_ZONE↔SOFT_PEN_CAP, equal-treatment trójca, ETA_QUANTILE_R6_BAGCAP, CARRIED_FIRST_RELAX parytet 4-proces, FEAS_CARRY_READMIT↔BEST_EFFORT_OBJM, LEX_COMMITTED_WINDOW, OBJM_LEXR6↔frozen lex_qual, ALWAYS_PROPOSE.
**Inwersje P-1..P-7 + C3:** wszystkie 7 + post-P no-GPS zmapowane do flag + reset-podatności.
**Źródła:** A2 (reguły×warstwa), A3 (flagi efektywne 3-warstwa), A5 (3 systemy flag cross-repo), A6 (grupy 1/4/6 — anchor/floor/lex), KANON §1/§5/§7/§9, full-rule-audit P-1..P-7, świeży grep common.py/dispatch_pipeline.py/feasibility_v2.py/plan_recheck.py/objm_lexr6.py.

## 6. LUKI POKRYCIA (coverage_gaps — jawnie, nie cisza)

1. **Cross-repo precedencja flag „tej samej nazwy" (J)** — `TRUST_CANON_ORDER` istnieje 2× (`PANEL_FLAG_` konsola vs `BUILD_VIEW_TRUST_CANON_ORDER` courier_api), defaultują niezależnie (A5 §C7). NIE zmapowałem konfliktu precedencji konsola↔apka per-flaga (3 systemy flag, oś J — należy do D-cross-repo agenta, tu tylko cross-ref).
2. **C5 osiągalność gałęzi** — czy każda zmaskowana gałąź (np. 4 KOORD pod always-propose) jest realnie osiągalna przy ŻYWYM zestawie flag (`BUILD_VIEW_TRUST_CANON_ORDER` martwa bo `APP_ROUTE_FROM_CONSOLE` short-circuit, A6 grupa2). NIE prześledziłem control-flow każdej pary — to Faza C trace.
3. **Materialność na żywo** — ile RAZY dziś każdy konflikt realnie przełączył decyzję (np. ile gold>35 przeszło przez ETA_QUANTILE; ile −20 pre-shift jeszcze trafia). Deklaruję z lektury kodu + flagi effective, NIE z `grep -c` shadow_decisions. To Faza C oracle.
4. **`USE_V2_PARSER` cross-proces (J/D2)** — parser V2 panel-watcher vs V1 shadow (A3 §5) = potencjalny konflikt danych wejściowych, ale to dryf-flag (D-agent), nie konflikt-reguł (I). Cross-ref, nie zbadane tu.
5. **Numeryczne progi rozsypu (N)** — D5 triple-tax magnitudy (−8/−40/−24) zinwentaryzowane, ale Pareto „która kara dominuje per case" = pomiar (N-agent / Faza C), nie statyczna oś I.
6. **`czasowka_scheduler` env** — nie zmierzony `systemctl show` (A3 §9); jego ścieżka feasibility może mieć własny stan flag → potencjalny 4. proces w sprzężeniach. Cross-ref.

---

## 7. HANDOFF — Faza E (dedup) / F (target)

- **Dedup (E):** I-01/I-02/I-15 + P-4 zwijają się do **R1 „one selection key + equal-treatment"** (K1, A6 grupy 1/3/5). I-04/I-05 → **R3 „one SLA/R6 anchor"** (A6 grupa4). I-11/I-16 → **R4 „one earliest-pickup floor"** + P-1 (A6 grupa6). I-06/I-07/I-08/I-09 = czyste sprzężenia-flag (osobny root „flag-coupling-without-guard"). **NIE liczyć I-01..I-20 jako 20 niezależnych chaosów** — to ~6 rootów × manifestacje.
- **Target (F):** (1) klasa flag „inversion-guard" (kod-default = polityka-PO-inwersji) ALBO runtime-inwariant na decyzje C3/P-1/P-5; (2) wciągnąć route/canon + equal-treatment + OR_TOOLS/GROUPING + DANGER_ZONE do `flag_fingerprint`+flags.json (zamyka M1 + silent-revert); (3) JEDEN SLA/R6 anchor (ready) zamyka I-04/I-05; (4) flip-coupling rejestr (co-maskuje-co z §2) jako bramka C2/C3 PRZED każdym flipem; (5) usunąć const `-20` pre-shift (I-02) i mylące „VETO"/„HARD_GATE" (I-10/I-19).
- **Bramki czasowe dotknięte konfliktami:** 02.07 O2 review (I-04/I-05/I-03 D3) — flip `O2_READY_ANCHOR_SWEEP` MUSI rozstrzygnąć anchor + paczka-exempt 4. site RAZEM; flip C7 `POST_SHIFT_OVERRUN` (I-15) wymaga przepiąć frozen `_lex_qual` na canon.
