# Ziomek — audyt root-cause rodziny „bezsensownych propozycji" (alokacja/geometria/global/pozycja)

**Data:** 2026-06-30 | **Metoda:** hybryda (12 plastrów triage + 2 forensyka → rank → deep-dive → adversarial oracle-verify, lane runtime per C11). 37 agentów, 4,76 mln tok., 79 gniazd → 11 distinct rootów (5 CONFIRMED, 2 PLAUSIBLE, 3 REFUTED, 1 deep-only/verify-padł).
**Zakres:** klasa błędu zgłoszona przez Adriana 30.06 — Ziomek pod obciążeniem proponuje JEDNEMU kurierowi (Dawid cid447) wiele zleceń w PRZECIWNE strony (spread 10–12,7 km vs R1=8 km), „jedno na raz, nie globalnie". **Runda DIAGNOZY — zero kodu.**
**Oracle na żywo:** `pending_global_resweep.jsonl` 2026-06-30 (447 jako new_cid 127×, 152/426 wpisów deliv_spread>8 km PO global_allocate, pool_feasible=0 w 43–45% ticków szczytu, 19× PANEL_OVERRIDE 447→koordynator).

---

## TL;DR — cała klasa to JEDNA prawda architektoniczna w 2 sprzężonych P0

Pod niedoborem floty (pool_feasible=0) alokacja NOWEGO zlecenia ma **dwa źródłowe braki, których żadna łatka nigdy nie tknęła u źródła** — i które MUSZĄ iść razem (każdy osobno = no-op na case 447):

- **P0-A — selekcja best-effort jest geometrycznie ŚLEPA.** Klucz wyboru zwycięzcy liczy TYLKO czas; rozjazd dostaw jest policzony i zapisany na kandydacie, ale **żaden klucz selekcji go nie czyta** — żyje tylko jako miękka kara w `score`, którą ścieżka scarcity wprost wyrzuca. → Dawid (odbiory w centrum = najniższy czas) wygrywa mimo dostaw zachód+wschód.
- **P0-B — brak globalnej de-konflikcji dla NOWEGO zlecenia.** `assess_order` liczy każde zlecenie osobno, zachłannie, bez claim/rezerwacji. Sekwencyjna wirtualna alokacja (de-pile) ISTNIEJE i działa live — ale TYLKO dla toru PRZERZUTU; dla nowego zlecenia jest shadow/display-only. → Ziomek wskazuje Dawida do 4 zleceń niezależnie, nie wiedząc, że już go obiecał.

Reszta to **wzmacniacze** (pula bez look-ahead, optymizm ETA) i **dług strukturalny** (kolejność trasy w 4–5 kopiach), nie sedno case'a 447.

---

## MAPA ŹRÓDEŁ (ranking)

### 🔴 P0-A — Geometria nigdzie nie dożywa do ścieżki wyboru pod scarcity (R1 + R3 + R4 = trzy widoki tego samego)
- **Źródło:** `objm_lexr6.py:40` — `lex_qual = (post_shift?, objm_r6_breach_max_min, late_pickup_committed_max, new_pickup_late_min)` = **czysto czasowe, ZERO osi geometrii**. Warstwa 7 (selekcja).
- **Producent danych, których nikt nie czyta:** `feasibility_v2.py:501/505/536/547` liczy i serializuje `deliv_spread_km`, `r1_violation_km`, `r1_avg_pairwise_cosine`, `r1_new_drop_dist_km` — gotowe w chwili selekcji, nieczytane.
- **R3 (P1, CONFIRMED):** `feasibility_v2.py:504` — spread > R1_MAX (8 km) **NIE rejectuje**, tylko pisze metrykę (komentarz: „SOFT, zweryfikowane audytem 2026-05-21"). Geometryczna bramka HARD nie istnieje; `geometry_blind_fallback` KOORD (`dispatch_pipeline.py:6443`) za wąski (wymaga feasible≥2 — nie odpala przy pool=0 — AND all-greedy AND all-cos<0).
- **R4 (P1, CONFIRMED):** geometria żyje wyłącznie jako SOFT w `score` (R1 corridor-mult, bundle deliv-coloc kredyt `compute_bundle_deliv_coloc ~2971`, spread-cap). Żywy ADDYTYWNY penalty geometrii = **brak** (FIX_C_ADDITIVE OFF, R1_CORRIDOR_GRADIENT OFF, BugZ ×0.1 = no-op na ujemnym score przez sign-guard). A best-effort i tak `score` nie czyta.
- **Konsumenci LIVE (wszystkie ślepe):** `_best_effort_objm_pick` (`dispatch_pipeline.py:665/710`, `ENABLE_BEST_EFFORT_OBJM_R6_KEY=true`), feasible d2-pick `_objm_lexr6_d2_pick:1355` (`ENABLE_OBJM_LEXR6_SELECT=true`), resweep `:1230/1250`. Override `:6771-6787` nadpisuje `_best_effort_sort_key` (gdzie geometria była 5-tym tie-breakiem przez `-score`) → ostatni ślad geometrii wyrzucony.
- **Dlaczego wracał:** łatano ZŁĄ OŚ. Wszystkie ≥4 łatki „dyskryminacji pozycji" (`_selection_bucket`, no_gps-equal, `_demote_blind_empty`, fastest_pickup) dodawały świadomość POZYCJI do tego samego czasowego klucza — **nigdy osi KIERUNKU/ROZJAZDU**. Pozycja kuriera ≠ rozjazd dostaw.
- **Oracle:** 30.06 10:04 best_effort 447 `deliv_spread=10.12`, `r1_cos=-0.987`, bag=3 wybrany; `best_effort_objm_live_flip` true=186 vs false=52 (78% picków zmienianych czysto-czasowym kluczem). 447 wygrywa z r6=38.4 GORSZYM niż 531=31.4 — decyduje bliskość ODBIORU (km_to_pickup 4.52 vs 7.36).
- **Ścieżka fix-u-źródła:** wprowadzić do **KANONU `lex_qual`** człon rozjazdu z JUŻ serializowanych metryk (zero nowego liczenia) jako tie-break PO osi R6 — ale dopiero PO dokończeniu `objm-lexr6-unify` (3 kopie + d2 + best_effort + `_best_effort_sort_key` RAZEM, inaczej rozjazd bliźniaków). SOFT geometria **nie może osłabić HARD R6 tier-aware** (35 T1/2, 40 T3).

### 🔴 P0-B — Brak globalnej de-konflikcji dla NOWEGO zlecenia (R2, CONFIRMED)
- **Źródło:** `shadow_dispatcher.py:1118` zdejmuje flotę RAZ, `:1141` pętla per-event, `:1195` `process_event→assess_order` **bez mutacji/claimu floty między eventami**. `assess_order:3286` i `check_feasibility_v2:424` nie mają parametru claim/reserve. Warstwa 7 (orkiestracja silnika).
- **Asymetria bliźniaków (sedno nawrotu):** sekwencyjna wirtualna alokacja `global_allocate` (`tools/pending_global_resweep.py:145` + `_tentative_assign:124`) jest single-source i **działa live dla PRZERZUTU** (`reassignment_global_select.py:54` import, `ENABLE_REASSIGN_GLOBAL_SELECT=true`). Dla NOWEGO zlecenia: **shadow-only** — `PENDING_RESWEEP_LIVE=false`, ścieżka live to czysty no-op warning (`:419-421`); Faza C global-alloc (`4812f28`) = **display-only**.
- **Dlaczego wracał:** fixy lądowały w warstwie 10 (konsola/overlay: `global_alloc.json`, `reassign_global_alloc.json` show/hide, flaga `pile_on` w feed.py) i jako POST-HOC timer (`would_repropose` co 1 min PO propozycji). Silnik przez cały czas dalej proponował per-order greedy — propozycja do Telegrama nigdy nie zobaczyła zde-konfliktowanej alokacji.
- **Oracle:** `pending_global_resweep.jsonl` 2026-06-30: `proposed_cid 447 = 127×`, 32 distinct ordery dotykają 447; `g_maxpile_before` sięga **7** (jeden kurier z 7 zleceniami); `would_repropose=true` w 166/426 (39%); spready>8 km **PO** global_allocate (g_maxpile_after=4) → de-pile pod scarcity dziedziczy ślepotę geometryczną.
- **Ścieżka fix-u-źródła:** przenieść de-konflikcję do warstwy 7 w gorącej pętli `_tick`, JEDNO źródło = istniejący `global_allocate` + claim `_tentative_assign`: zebrać batch równoczesnych NEW_ORDER (+ wiszące), wywołać `global_allocate` RAZ (rozjazdy → różni kurierzy), emitować TE alokacje jako propozycje. **Musi lustrować ten sam mechanizm co `reassignment_global_select`** (nie 2. kopia). **KRYTYCZNE: wejść RAZEM z P0-A** — sam de-pile pod pool=0 rozsypie pile-on na równie złe geometrycznie cele.

### 🟠 P0-amplifikator — Pula bez look-ahead „zaraz wolny" (R6, PLAUSIBLE)
- **Źródło:** `courier_resolver.py:1383` `dispatchable_fleet()` — **żadnej gałęzi busy→zaraz-wolny**; jedyna projekcja to pre_shift (start zmiany, `:1557`). `soon_free_probe` (`dispatch_pipeline.py:2342`) to scoringowa PODMIANA pozycji w warstwie 6 (zła warstwa), **efektywnie OFF** (`ENABLE_SOON_FREE_CANDIDATE=false`, `soon_free_applied=0/3290` na żywo, pokrycie ~9%).
- **Werdykt PLAUSIBLE (nie THE source):** gap realny i otwarty, podnosi częstość pool=0 (45% dziś), ALE w case 484462 pool_feasible=0 mimo **6** kurierów; właściwa odpowiedź **370 niewidoczna przez BRAK GPS** (luka DANYCH/obserwowalności, nie look-ahead). Współźródło „małej puli", nie operatywna przyczyna pile-onu 447.
- **Ścieżka fix-u-źródła:** uogólnić projekcję dostępności do `free_at_min` w **warstwie 1** dla wszystkich populacji (pre_shift + busy-z-planem + busy-bez-planu), z możliwością ODROCZENIA/REZERWACJI w feasibility/selekcji; wycofać scoringowy `soon_free_probe`. Wszyscy konsumenci `dispatchable_fleet` RAZEM (shadow/czasówka/postpone/replay/reassign).

### 🟡 Wzmacniacze czasowe (kłamią osie czasu, którymi rankuje lex_qual)
- **R8 (P1, CONFIRMED) — ETA load-blind optymizm.** `dispatch_pipeline.py:2948` `get_pickup_ready_at` bez buforu → `predicted_delivered_at` load-blind → R6 z optymistycznego pred → osie temporalne `lex_qual`. `PICKUP_DEBIAS` płaski 4,5 (~6× za mały vs zmierzone +27,4 ciasno-solo), `ENABLE_PREP_BIAS_TABLE=false`. Oracle: `pickup_slip_monitor.jsonl` n=684 monotoniczny z obciążeniem (ciasno +27,4 / luźno +6,2). **Wzmacniacz, nie nagłówek** — z idealnym ETA 447 (centrum) dalej najniższy breach. Fix: load-aware bufor poślizgu ODBIORU w forward-ETA (NIGDY z powrotem do renderu — landmine F1.8g).
- **R9 (P1, deep-only — verify padł na rate-limicie) — kalibracja celuje w złą oś.** `feasibility_v2.py:1089` — żywa bramka R6-bagcap (`ENABLE_ETA_QUANTILE_R6_BAGCAP=true`) **luzuje HARD R6** globalnym p80 na osi JAZDY, mimo że kalibracja 29.06 dowiodła „noga jazdy ~0 błędu, optymizm = poślizg odbioru". `DRIVE_SPEED_MULT_BY_TIER<1.0` żyje w common.py (mina re-flipu). Fix: korekta na osi poślizgu odbioru, nie jazdy; jedno źródło `calib_maps.eta_quantile_calibrate` dla 3 bliźniaków. **Dotyka HARD R6 → protokół+ACK.**

### 🟡 Dług strukturalny — Kolejność trasy w 4–5 kopiach (R11, PLAUSIBLE)
- **Źródło:** brak JEDNEJ funkcji kolejności — `plan_recheck._apply_canon_order_invariants:1478`, `route_podjazdy.order_podjazdy:190`, `courier_orders._prioritize_carried_dropoffs:467`, `fleet_state._build_route:395` + drugi producent kanonu `panel_watcher._save_plan_on_assign:478` (pisze BEZ inwariantów). Warstwa 9/10.
- **Werdykt PLAUSIBLE:** multicopy realny i obecny (oracle `carried_first_guard.jsonl`: 4× canon_divergence dziś), ale ostry objaw carried-first = **1 transient tick/dzień**, samozdrowieje; trzymany flagami trust_canon+invariants ON; recanon-on-write (`363efda`) częściowo domknął producenta. Dług strukturalny, nie otwarty hotfix.
- **Fix-u-źródła:** wydzielić wspólny moduł kolejności, wszyscy producenci przez niego, renderery = czysty odczyt, ujednolicić obsługę `invalidated` apka↔konsola.

---

## ✅ OBALONE (REFUTED) — NIE ruszać / już u źródła / ortogonalne

- **R5 — pool universe name-drop** (`courier_resolver.py:811`). Obserwacja prawdziwa (flota seedowana z per_courier∪names∪piny, grafik dołącza dopiero w filtrze), ale **0/14 on-shift zgubionych** (autopair seeduje). 370 zdropowany przez FILTR (shift 12:00 vs okno pre-shift 60 min), nie seeder. Latentna nota, nie operatywne źródło.
- **R7 — no-GPS position fiction** (`courier_resolver.py:1090`, 6× `cs.pos=BIALYSTOK_CENTER`). Równe traktowanie no-GPS **działa**; fikcja nie jest ścieżką szkody — 447 wygrywa REALNĄ pozycją + selekcją czasową; 370 to luka DANYCH (brak GPS), ortogonalna. Latentna: luka launderera F1.7 dla `working_override_synthetic` (0 wystąpień/tydz) — domknąć profilaktycznie.
- **R10 — plan ownership no-prune** (`plan_recheck.py:1832`). 8 phantomów = inertne zombi (0 retimów, tygodnie stare); recanon bail'uje na pustym oids; realna luka to GC planu gdy worek pusty, nie subset-gate. 0 szkodliwych mixed-bag na żywo.

---

## 🔬 „WIĘCEJ BUGÓW" — kłamiące/niewalidowane przyrządy tej rodziny (lane C11)

Ważne, bo na nich walidowałoby się fix — kłamiąca liczba przepchnie zły flip:
- `best_effort_fastest_pickup_shadow` (**LIVE**) → **void** — pisze dane flip-walidacji ze stale hardcoded bucketem (skaża no_gps/pre_shift).
- `_objm_lexr6_shadow` → **void** — zamrożony pre-equal-treatment bucket + 3-krotka lex_qual (inert dziś, mina przy re-flipie).
- `post_shift_overrun_forward_replay` → **void** — czyta klucz nigdy nieserializowany (`grep=0/282`); werdykt GO/NO-GO niemożliwy.
- `reassignment_forward_shadow` (`_SYNTH_POS` „duch przerzutu") → **void** — ~59% fałszywych ratunków (memory 29.06); poprzedni audyt go nie ruszył.
- `pending_global_resweep` (de-pile NEW) → **untested** — shadow-only, ścieżka live niezaimplementowana.
- `sequential_replay._determine_verdict` (`pile_ratio>pile_tol` + Gini fleet-gate, którego używa ETAP-5) → **untested**.
- `bug4 reseq shadow` → **void** — kanoniczny przykład C11 (read-only audyt go nie złapał).

---

## Wzorzec NAWROTU (forensyka) — dlaczego „naprawiane, a wraca"

Prawie każda naprawa do 29.06 trafiała w **POJEDYNCZY bliźniak** albo w **KRAWĘDŹ** (render/instrument/display), gdy reguła żyje w 8+ kopiach. Trzy mechanizmy (udokumentowane commitami):
1. **Rozsianie w bliźniakach** — `lex_qual`/`bucket` w 3+ kopiach; no-GPS-equal łatane 22→24→25→29.06 (ruszano 1 konsumenta).
2. **Patch na krawędzi** — carried-first „naprawiane 10×" na renderze; prawdziwe źródło (read-with-side-effect `plan_manager.load_plan`, V3.19b 19.04) trafione dopiero `b6caf6b` 29.06.
3. **Asymetria ścieżek** — de-pile zbudowany TYLKO dla PRZERZUTU (`1162ec1`) + global-alloc DISPLAY-only (`4812f28`); NOWE zlecenie dalej greedy.

**Co realnie zamyka klasę (commity które NIE wróciły):** `a8cdb95` (unify na `_selection_bucket`), `b6caf6b` (źródło nie render), `1162ec1`/`791b28c` (de-pile przerzutu u źródła), `geocode-centroid guard`, `Sprint1-3 no-GPS-equal`. Wspólny mianownik: jedno źródło reguły + trafienie u źródła + WSZYSTKIE bliźniaki RAZEM + strażnik nawrotu.

**Poprzedni audyt 86-agentowy (27/28.06) PRZEOCZYŁ oba P0** — traktował best-effort sentinel jako „correct-by-design" i nie zapytał, czy best-effort ma honorować geometrię; nigdy nie zauważył braku claim/de-pile dla NOWEGO zlecenia (potwierdza C11: read-only = ślepy na klasę „artefakt mierzy proxy / brak runtime-oracle").

---

## Rekomendacja kolejności (do osobnego ACK — to runda diagnozy)

1. **JEDEN sprint P0-A+P0-B razem** (osobno = no-op): człon geometrii w kanonie `lex_qual` (po `objm-lexr6-unify`) + live `global_allocate` z claim w `_tick` lustrujący `reassignment_global_select`. Za protokołem ETAP 0→7, flaga ON≠OFF, replay-dowód POZYTYWNEGO wpływu (spadek pile-onu 447 + spadek deliv_spread ON↔OFF), parytet bliźniaków, HARD R6 nietknięty.
2. **R6 look-ahead `free_at` w warstwie 1** + R7-data-gap (widoczność kuriera bez GPS) — osobny sprint (to ratuje przypadki typu 370).
3. **R8/R9 oś ETA** (load-aware poślizg odbioru; R9 dotyka HARD R6 → ACK).
4. **R11** ujednolicenie kolejności (upgrade strukturalny).
5. **Higiena przyrządów** — naprawić/oznaczyć void instrumenty PRZED użyciem ich do walidacji powyższych flipów.
