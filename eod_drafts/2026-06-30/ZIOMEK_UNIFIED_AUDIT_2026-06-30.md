# ZIOMEK — AUDYT ZUNIFIKOWANY (alokacja tras + odbiór-przed-zmianą) → droga do architektonicznego ideału

**Data:** 2026-06-30 · **Status:** scalony audyt read-only (zero kodu) — JEDEN dokument na prośbę Adriana.
**Źródła scalone (zachowane na dysku jako szczegółowe zaplecze):**
- **Audyt A — alokacja/geometria/global/pozycja** (sesja 4, „logika zaproponowanych tras"): `ZIOMEK_ROOTCAUSE_AUDIT_allocation_family.md` (37 agentów, 11 rootów, 5 CONFIRMED).
- **Audyt B — odbiór przed startem zmiany (pre_shift/no_gps) + sentinel(0,0)/V328** (sesja 2, „logi Drapieżnika"): `AUDYT_preshift_pickup_floor_HANDOFF_sesja2.md` + `memory/preshift-pickup-floor-audit-2026-06-30.md` (9+4 agentów).
- **Rama docelowa — audyt spójności (15 klas anty-wzorców)**: `ZIOMEK_COHERENCE_AUDIT_DESIGN.md` (zaakceptowana taksonomia, jeszcze nieodpalona).

---

## 0. META-WNIOSEK (najważniejsze) — to NIE jest „dużo bugów", to GARŚĆ chorób, które metastazują

Dwie sesje, **dwa różne objawy**, badane **niezależnie**, **zbiegły się w te same korzenie**:
- Sesja 4 (ja) startowała od „Ziomek proponuje Dawidowi 3 zlecenia w przeciwne strony pod scarcity".
- Sesja 2 startowała od „konsola pokazuje odbiór 10:59 dla kuriera ze zmianą 11:00" (Drapieżnik→Dawid, zlec. 484400).
- **Obie trafiły w:** brak jednego źródła reguły (kopie), `plan_recheck` jako „cofacz decyzji", optymistyczny estymator poślizgu odbioru, geometria/floor tylko SOFT/krawędź bez HARD/inwariantu, sentinele jako dane, 8 bliźniaków pozycji, miny flag, cross-repo dryf.

To jest twardy dowód na Twoje „jeden wielki chaos": **objawy są różne, choroba ta sama.** Naprawa pojedynczych objawów = nawrót (potwierdzone forensyką: rodzina łatana ≥4×). Ideał = wyleczyć FUNDAMENT raz, oba objawy znikają, a przyszłe objawy tej rodziny są prewencyjnie zablokowane.

---

## 1. WSPÓLNY KORZEŃ — 7 anty-wzorców, w które trafiły OBA audyty (nagłówek syntezy)

| # | Wspólny korzeń | Audyt A (alokacja) | Audyt B (pre-shift) | Klasa taksonomii |
|---|---|---|---|---|
| **K1** | **Brak JEDNEGO źródła reguły → N kopii** | kolejność trasy ×4-5, `lex_qual` ×3, 8 bliźniaków pozycji | „najwcześniej kurier może odebrać" liczone w **17 powierzchniach, tylko 4 mają floor**; 8 bliźniaków pozycji | A1·A2·B |
| **K2** | **`plan_recheck` = równoległy „cofacz decyzji"** | R3/R11: regeneruje co 5 min wołając `simulate_bag_route_v2` **bez `check_feasibility_v2`** → geometria nieliczona, zigzag wraca | leak #5: regeneruje `courier_plans.json` co 5 min **bez floor `shift_start`** → plan się SAM odclampowuje | B·C·H·O |
| **K3** | **Optymistyczny estymator ETA (poślizg odbioru load-blind)** | R8: `PICKUP_DEBIAS` płaski 4,5 (~6× za mały vs zmierzone +27,4), `PREP_BIAS_TABLE=OFF` | TOP-3 #2: „L1 się oszukuje" — liczy spóźnienie za małe → wybiera kuriera, który dotrze 18 min później | G |
| **K4** | **Reguła tylko SOFT/krawędź — brak HARD-bramki i RUNTIME-INWARIANTU** | P0-A: geometria liczona, serializowana, **nieczytana przez selekcję**; R3: spread>8 km nie rejectuje (`feasibility_v2:504`) | grep „pickup ≥ shift_start" = **ZERO strażnika/assert**; HARD dopiero przy −30 min | C·I + brak inwariantu |
| **K5** | **Sentinele / cicha awaria jako DANE** ⟵ **MOST PRZYCZYNOWY** | BIALYSTOK_CENTER fiction, score −1e9, geocode-centroid 0km | **bug#2: `delivery_coords=[0,0]` (truthy!) → haversine → `V328` wyrzuca ZAJĘTEGO kuriera z puli** (potwierdzone: 30 V328/journal dziś); 119 `except Exception` | M |
| **K6** | **Greedy bez global de-konflikcji + miny flag** | P0-B: `assess_order` per-order bez claim; de-pile live tylko dla PRZERZUTU; `PENDING_RESWEEP_LIVE=false` | miny flag default-OFF-w-kodzie vs ON-w-flags.json (`PRE_SHIFT_DEPARTURE_CLAMP`, `NO_GPS_EQUAL`, `EQUAL_BUCKET`, `CLAMP_PRESHIFT_PICKUP_ETA`) — reset flags.json = polityka się wywraca | B·D·I |
| **K7** | **Cross-repo/multi-proces dryf** | konsola↔apka osobne repo, brak wspólnego renderera (R11) | „brak wspólnego renderera czasu, gwarantowany dryf"; **realna kolizja na `fleet_state.py` dziś między sesjami 2 i 4** | J |

### ⭐ MOST PRZYCZYNOWY (K5 → K6 → K4) — dwa P0 są POŁĄCZONE
Sesja 2 udokumentowała: **Jakub (492) 10:51:58 → `V328_CP_SOLVER_FAIL` na sentinelu (0,0) → wypadł z puli → zostało pre-shiftowemu Dawidowi.** To jest DOKŁADNIE mechanizm scarcity, który mój audyt A wykorzystał: **sentinel(0,0)/V328 (K5) kurczy pulę → `pool_feasible=0` → geometria-ślepa best-effort selekcja (K4) + brak global de-konflikcji (K6) → pile-on Dawida w przeciwne strony.** Te same kurierzy (447 Dawid, 492 Jakub) w tym samym oknie (10:04-10:59). **Bug#2 nie jest osobny — jest GÓRNYM biegiem mojego P0.**

---

## 2. AUDYT A (skrót) — alokacja/geometria/global (pełne w `ZIOMEK_ROOTCAUSE_AUDIT_allocation_family.md`)
- **P0-A `objm_lexr6.py:40`** (zweryf. dziś) — `lex_qual` czysto czasowy; geometria rozjazdu liczona w `feasibility_v2:501/536`, nieczytana. Override `:6771-6787` wyrzuca ostatni ślad (score). LIVE oracle: 447 „best" 11×, spread 10-12,7 km, `live_flip` true=186 (78%).
- **P0-B `shadow_dispatcher.py:1118`** — per-order greedy bez claim; de-pile (`global_allocate`) live tylko dla przerzutu; `PENDING_RESWEEP_LIVE=false`. Oracle: 447 jako new_cid 127×, `g_maxpile`=7.
- **Wzmacniacze:** R6 brak look-ahead „zaraz wolny" (PLAUSIBLE), R8 ETA load-blind (=K3), R9 kalibracja na złej osi (R6-bagcap luzuje HARD R6), R11 kolejność ×4-5 (PLAUSIBLE, =K7).
- **OBALone (nie ruszać):** R5 pool-name-drop, R7 no-GPS-fiction (równość działa), R10 plan-no-prune (zombi inertne).

## 3. AUDYT B (skrót) — odbiór przed startem zmiany + sentinel(0,0) (pełne w handoffie sesji 2)
- **Root:** brak kanonu „pickup ≥ shift_start"; re-liczone w **17 powierzchniach, floor tylko w 4** (`#1 candidate eta`, `#3 plan departure-clamp`, `#7 telegram`, `#16 konsola CLAMP_PRESHIFT`). Reguła −30 HARD / −20 warm-up (zerowana `EQUAL_NO_PENALTY`) — „10:59 vs 11:00" = polityka, nie bug. Pre_shift = best w 7,4% propozycji (12-37/d).
- **Najszersza dziura = K2** (`plan_recheck` regeneruje bez floor → odclampowuje co 5 min).
- **Bug#2 (=K5):** `(0,0)` truthy → `V328` wyrzuca zajętego z puli; `_sanitize_courier_pos` chroni tylko pozycję kuriera, nie coords zlecenia. Fix u źródła = repair/reject (0,0) przy INGEST + jeden guard haversine.
- **TOP-3 kreatywne dziury:** (1) **frozen-pickup R27 broni złego czasu** — committed nietykalny; jeśli committed <shift_start, frozen zatrzaskuje złą godzinę → floor MUSI trafić w wartość committed (chokepoint), nie na OSRM; (2) optymistyczny estymator (=K3); (3) **grafik=SPOF** — `_shift_start_dt:1252` bez +1 (północ), fetch 06:00 → 00:00-06:00 cała flota `shift_start=None`, `is_on_shift` fail-open 24/7, literówka „11.00" w Sheecie = floor martwy cicho.
- **8 tropów systemowych:** stale-pos 25 min (rescue 5639×/d), 119 silent-except, plan_recheck-cofacz, spiętrzone inwersje równości (regresja V3.16 demote tylnymi drzwiami), TZ/zegar aktywna klasa, gotowość-floor sama niepewna, read-with-side-effect, multi-sesja shared-deploy.

### Decyzje Adriana (Audyt B — ZABLOKOWANE 30.06, przenoszę do wspólnego planu)
- **Q1 zakres = OBA:** jedno źródło floor (commit + rendery) **+ twardsza feasibility**.
- **Q2 frozen:** deklaracja restauracji NIETYKALNA → **nie dawaj zlecenia pre-shift kurierowi, który nie zdąży** (zmieniaj KTO, nie czas).
- **Q1b:** równość ZOSTAJE, `ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY` nietknięte — „nie zdąży" = FAKT FEASIBILITY/SPÓŹNIENIE (R-LATE-PICKUP), NIE nowa kara.
- **Q2b:** floor obejmuje pre_shift + no_gps (dla no_gps on-shift = no-op `max(now,shift_start)=now`).

---

## 4. MAPA NA TAKSONOMIĘ 15 KLAS (oba audyty zasilają — walidacja, że taksonomia jest trafna)
Dwie próbki już populują 10/15 klas — pełny audyt spójności wyczerpie resztę:

| Klasa | Instancje z A+B (dowód, że to systemowe, nie incydent) |
|---|---|
| **A1/A2 N-kopii** | lex_qual ×3, kolejność ×4-5 (A) · floor w 17 powierzchniach (B) |
| **B asymetria bliźniaków** | de-pile przerzut≠nowe (A) · floor 4/17, plan_recheck-leak (B) |
| **C naruszenie warstw** | geometria tylko SOFT (A) · floor na renderze nie u źródła, brak HARD-inwariantu (B) |
| **D dryf flag** | `BEST_EFFORT_OBJM` itd. (A) · miny default-OFF vs flags.json-ON (B) |
| **E kłamiące przyrządy** | fastest_pickup_shadow/post_shift_replay void (A) · — |
| **F semantyka pól** | eta_pickup display=decyzja (A) · committed `czas_kuriera` <shift (B) |
| **G kalibracja zła oś** | R8/R9 (A) · TOP-3 #2 optymistyczny poślizg (B) |
| **H cykl życia** | plan-no-prune (A) · plan_recheck-cofacz, gotowość-floor (B) |
| **I konflikt reguł** | HARD R6 vs SOFT geo (A) · warm-up −20 vs EQUAL_NO_PENALTY vs clamp vs HARD−30 (B) |
| **J cross-repo** | konsola↔apka (A) · brak wspólnego renderera + kolizja fleet_state (B) |
| **K martwy kod** | `r6_soft_penalty_c3_legacy` (A) · panelsync #14-15 martwy (B) |
| **L słownictwo/TZ** | „tier"×2 (A) · TZ today-only/naive, grafik północ, `time`-param (B) |
| **M sentinele/cicha awaria** | −1e9, centroid (A) · **(0,0)/V328, 119 silent-except (B) ← K5** |
| **N rozsyp progów** | R6 35 vs 35/40 (A) · `PRE_SHIFT` progi (B) |
| **O współbieżność** | pending_proposals 3-writer (A) · read-with-side-effect, stale-pos 25min (B) |

**Klasy słabo dotknięte przez 2 próbki → priorytet dla pełnego audytu:** E (kłamiące przyrządy — lane runtime-oracle), pełne L (TZ sweep), pełne K (martwy kod sweep cross-repo), N (rozsyp progów cały system).

---

## 5. ZUNIFIKOWANA ROADMAPA — wspólny FUNDAMENT leczy OBA objawy
Kluczowa obserwacja scalenia: roadmapa A (P0-A+P0-B) i B (L0-L6) **stoją na tym samym fundamencie**. Napraw fundament raz → oba objawy znikają + przyszłe się nie pojawią.

**FUNDAMENT (wspólny — buduj RAZ, oba audyty go potrzebują):**
- **F1. JEDNO źródło „dostępność/kotwica kuriera"** = `available_from = max(now, shift_start)` w `courier_resolver` (B:L0) — RAZ, dziedziczone wszędzie. Obejmuje pre_shift+no_gps.
- **F2. `plan_recheck` przestaje cofać** = regeneracja przez te same bramki co live: floor shift_start (B:L2) **ORAZ** `check_feasibility_v2`/geometria (A:R3). Najwyższy zwrot na nawroty (oba audyty: K2).
- **F3. Sanityzacja sentineli u ŹRÓDŁA** = repair/reject (0,0) przy INGEST coords + JEDEN guard haversine (B:bug#2 / A:centroid) → odbudowuje pulę (K5 most), zanim selekcja w ogóle rusza.
- **F4. Load-aware bufor poślizgu odbioru** w forward-ETA (A:R8 / B:TOP-3#2) — RAZ, NIGDY do renderu (landmine F1.8g). Karmi feasibility+selekcję prawdą czasu.
- **F5. Ujednolicenie reguły w kanonie** = geometria→`lex_qual` (A:P0-A, po `objm-lexr6-unify`) + kolejność→jeden moduł (A:R11/B:konsola↔apka) + 8 bliźniaków pozycji RAZEM.
- **F6. RUNTIME-INWARIANTY + STRAŻNIKI** (wzór `tools/carried_first_guard.py`): „pickup ≥ shift_start" (B:L6) + „żaden haversine na (0,0)" (B) + „selekcja czyta geometrię" / „HARD przed SOFT" (A). To JEDYNE co blokuje nawrót na zawsze (oba audyty: K4).
- **F7. HARDENING defaultów flag** = kod=intencja (B:L5), żeby reset flags.json nie wskrzeszał buga (K6) + wszystkie w `ETAP4_DECISION_FLAGS`.

**OBJAWY (na fundamencie):**
- **Objaw B (pre-shift):** L1 feasibility „nie wygrywa kto nie zdąży" (na F4, decyzja Q2) + L3 pas renderów na sclampowanym planie. Chokepoint = osobne pole `effective_pickup_at`, NIE nadpisuj deklaracji (zgodne z frozen R27, decyzja Q2).
- **Objaw A (alokacja):** P0-A geometria w selekcji (na F5) + P0-B live `global_allocate` z claim w `_tick` lustrujący `reassignment_global_select` (na F1). MUSZĄ iść razem (osobno = no-op).

**Sekwencja (zał. zwrot na nawroty + zależności):** F6-strażniki (shadow, MIERZ nawrót — zero ryzyka) → F3 sanityzacja sentineli (odbudowa puli) → F2 plan_recheck → F1 dostępność → F4 ETA → F5 kanon+bliźniaki → F7 hardening → objawy A+B. **Najgłębsze „nigdy nie wraca" = F1+F4+F6.**

**Deploy = P0** (silnik: feasibility/plan_recheck/courier_resolver/state_machine/selekcja + 8 bliźniaków) → protokół ETAP 0→7, off-peak >14:00, ACK Adriana, replay ON↔OFF z dowodem POZYTYWNEGO wpływu, parytet bliźniaków, pełna regresja.

---

## 6. CO DALEJ — pełny audyt spójności (15 klas) ma to DOKOŃCZYĆ
Te dwa audyty = 2 głębokie piony, które zwalidowały taksonomię i fundament. **Pełny audyt spójności** (`ZIOMEK_COHERENCE_AUDIT_DESIGN.md`, zaakceptowany, jeszcze nieodpalony) dorzuci: (a) wyczerpujący sweep WSZYSTKICH 15 klas × wszystkie moduły okołosystemu z ledger pokrycia 100%; (b) **lane runtime-oracle** (klasa E — kłamiące przyrządy, której 2 próbki ledwo dotknęły); (c) mapę konfliktów (oś I) systemowo; (d) **dashboard entropii** + zbieżną roadmapę z bramką „zero nowych kopii"; (e) pokazowy PoC konsolidacji. Fundament F1-F7 z tego scalenia = pierwszy wkład do tej roadmapy.

## 7. RECONCILE / CAVEATY (uczciwie)
- **Linie zweryfikowane dziś (dryf ±20-60 po commitach):** `feasibility_v2.py:748-757` (pre-shift −30, reguła Q1b) ✓ · `objm_lexr6.py:40` (lex_qual) ✓ · `plan_recheck` regen ~`:554-612` (`_gen_one_bag_plan:612`) ✓. **KAŻDY fix musi i tak re-grepować (ETAP 0).**
- **V328 mechanizm potwierdzony na żywo:** 30 zdarzeń/dzień w journalu dispatch-shadow (`V328_TSP_SETRANGE_OPEN_OOD` + wariant `CP_SOLVER_FAIL` sesji 2). Most K5 realny.
- **Zero sprzeczności między audytami** — to komplementarne piony (różne funkcje, ten sam plik/rodzina). Numeracja sesji 2 i moja różnią się bo badamy różne funkcje, nie te same linie.
- **Decyzje Adriana Q1/Q2/Q1b/Q2b (Audyt B) = ZACHOWANE** jako wspólny plan; mój P0-A+P0-B = ZGODNY (geometria/de-pile nie kolidują z floor — różne osie, wspólny fundament F1/F2/F6).
