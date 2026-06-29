Confirmed: the frozen pickup window upper bound is set to `V327_DROP_TIME_WINDOW_MAX_MIN = 120.0` min for committed pickup nodes — i.e. the lower bound is hard (ck−5), the upper bound is loose (120 min, not ck+5). This is the load-bearing fact for the greedy/TSP asymmetry findings. Now writing the report.

---

# RAPORT SYNTEZY — Spójność i kolejność reguł Ziomka (audyt 2026-06-24)

Dla: Adrian · READ-ONLY · do wspólnego rozstrzygnięcia

---

## 1. STRESZCZENIE

Architektura egzekwowania reguł Ziomka jest **w trzonie wodoszczelna w jednym, najważniejszym wymiarze**: HARD-gate'y feasibility (`check_feasibility_v2` → `verdict='NO'`) są egzekwowane **przed** warstwą scoring/bonus i kandydat z `verdict='NO'` nigdy nie wchodzi do selekcji (`dispatch_pipeline.py:5471` filtruje `MAYBE` zanim cokolwiek się sortuje po score). Żaden SOFT bonus (R1/R5/R8/R4/LOADGOV/SYNCWORKA) **nie może** obejść twardej bramki — to potwierdzona gwarancja, nie założenie. Kolejność warstw (feasibility → scoring → selection → verdict → TSP → canon → persistence → display) jest zachowana, a delty rankingowe są poprawnie wycinane z gate-score (`_gate_score_excluding_ranking_deltas`), więc kara „kto wygrywa" nie wpycha decyzji w ciszę.

Najgroźniejsze klasy problemów to **nie błędy logiczne, lecz trzy świadome asymetrie + dług dokumentacyjny**: (a) **R6=35 min jest HARD na odsiewie, ale SOFT na werdykcie** — przez `ENABLE_ALWAYS_PROPOSE_ON_SATURATION=true` ~20,6% propozycji wychodzi łamiąc R6 bez eskalacji (decyzja Adriana, NIE bug); (b) **greedy/bruteforce fallback w TSP nie zna frozen-window ani R6** w pętli insertów — przy INFEASIBLE OR-Tools fallback może zwrócić sekwencję spoza [ck−5,ck+5] lub >35 min, a post-solve assertion już tylko loguje (E2, nie odrzuca); (c) **carried-first jest BEZWARUNKOWE i wyprzedza committed-time sort** w `_apply_canon_order_invariants` — formalnie SOFT (carried-first) wygrywa z HARD (R-DECLARED-TIME okno odbioru), 110 przypadków/dzień (pickup_lateness_shadow), z gotowym fixem lex-reorder czekającym na ACK. Dochodzi do tego **dryft dokumentacji vs żywe flagi** (HARD_TIER_BAG_CAP live ON wbrew ref, COMMIT_DIVERGENCE live OFF wbrew ref) i **zombie-flagi** (`feasibility_check` 0 odczytów). Żadna z tych rzeczy nie jest awaryjna; wszystkie wymagają decyzji projektowej, nie hot-fixa.

---

## 2. TABELA PRECEDENCJI — zamierzona kolejność vs faktyczna

| # | Reguła / warstwa | Zamierzona pozycja | Faktyczna (kod) | ✅/❌ | Dowód file:line |
|---|---|---|---|---|---|
| 1 | **HARD feasibility przed scoring/bonus** | bramka NO blokuje przed score | NO filtrowane przed sortem po score; score liczony zawsze ale bez znaczenia gdy NO | ✅ | `dispatch_pipeline.py:3478` (check_feasibility_v2), `:5471` (`feasible=[c if verdict=='MAYBE']`) |
| 2 | **Z1/Z2/Z3 kardynalne** | nadrzędne nad wszystkim | autonomia (Z1) nadpisuje quality-gate KOORD świadomie | ✅ (intencja) | `dispatch_pipeline.py:2394` (`_always_propose_on`), flags.json:184 |
| 3 | **R6=35 HARD per-order (odsiew)** | HARD, zawsze | `verdict='NO'` przy >35 min od kotwicy termicznej | ✅ | `feasibility_v2.py:1237`, `common.py:647` (`BAG_TIME_HARD_MAX_MIN=35`) |
| 4 | **R6=35 HARD na werdykcie** | (oczekiwane HARD) | **SOFT** — ALWAYS-PROPOSE neutralizuje KOORD; best_effort z bannerem | ❌ świadome | `dispatch_pipeline.py:5947,6324,6360,6386` (guard `and not _always_propose_on()`), flags.json:184 |
| 5 | **R-DECLARED-TIME / frozen window (TSP, OR-Tools)** | HARD [ck−5,ck+5] | dół twardy (ck−5), **góra luźna 120 min** | ⚠️ częściowe | `route_simulator_v2.py:988-990` (`window_close=V327_DROP_TIME_WINDOW_MAX_MIN=120.0`, common.py:2169) |
| 6 | **R-DECLARED-TIME w greedy/bruteforce fallback** | HARD | **brak świadomości frozen-window i R6** w insertach | ❌ luka | `route_simulator_v2.py:774-853` (greedy bez walidacji ck/R6), `:445-456` (fallback bez reject) |
| 7 | **Post-solve assertion (TSP)** | reject planu łamiącego okno | **tylko loguje** (E2 2026-05-17), plan zostaje | ❌ świadome | `route_simulator_v2.py:1371-1385` |
| 8 | **carried-first vs committed-time sort (canon)** | HARD committed > SOFT carried | **carried-first BEZWARUNKOWE, przed committed-sort** | ❌ inwersja | `plan_recheck.py:1178-1182` (carried front) przed `:1183-1199` (pickup sort) |
| 9 | **Demote blind-empty (V3.16) → selection** | informed > other > blind, OSTATNI | demote działa, ale OFF-mode tier-sort może cofnąć (gdy LEXR6 flip) | ⚠️ uśpione | `dispatch_pipeline.py:5500` (demote) vs `:5545` (OFF-sort bez bucketa) |
| 10 | **Delty rankingowe (LOADGOV/SYNC) wycięte z gate-score** | re-rank, nie wycisz | wycięte poprawnie dla LOADGOV+SYNCWORKA | ✅ | `dispatch_pipeline.py:2118-2141`, `:5943` |
| 11 | **Verdict-gates: quality vs operational** | quality respektuje autonomię, operational zawsze eskaluje | stale/geometry BEZ guardu, low_score/R6 Z guardem | ✅ (po klasyfikacji) | guard: `:5947,6324,6360,6386`; brak: `:5869,5909,5987,6072` |
| 12 | **First-match wins (sekwencja gate'ów)** | stale→geo→low_score→divergence→difficult→PROPOSE | early-return zachowany | ✅ | `dispatch_pipeline.py:5869→6173` |
| 13 | **EXCLUDE_BY_CID (fleet-build)** | przed scoringiem | odsiew na budowie floty | ✅ | `courier_resolver.py:1464` (`get_excluded_cids()`) |
| 14 | **Recanon-on-write (każdy zapis)** | atomowo z save | best-effort try/except OSOBNO; cancel-path BEZ recanon | ❌ asymetria | `panel_watcher.py:618/662/712` (z recanon) vs `:667-682` (cancel bez) |

**Werdykt na pytanie „czy trzyma reguły w odpowiedniej kolejności":** TAK na osi HARD-feasibility-przed-SOFT (najważniejsza, ✅). NIE na trzech osiach świadomych (R6-na-werdykcie ❌, frozen-window-w-greedy ❌, carried-first-vs-committed ❌). Te trzy to nie pomyłki kolejności w kodzie — to wybory projektowe, które łamią deklarowaną hierarchię HARD>SOFT i wymagają Twojej decyzji.

---

## 3. POTWIERDZONE PROBLEMY (sort: severity × skutek ÷ blast-radius)

### P-1. Carried-first wyprzedza committed-time okno odbioru — inwersja HARD/SOFT [e_inwersja, P1-skutek/P2-blast]
- **Reguła:** R-DECLARED-TIME / R27 (HARD, okno odbioru ±5) vs carried-first (SOFT, ochrona termiczna)
- **file:line:** `plan_recheck.py:1178-1182` (front carried bezwarunkowo) **przed** `:1183-1199` (sort pickupów wg committed `czas_kuriera`)
- **Dowód:** `front_carried = [...picked_up dropoffs...]` doklejane na początek niezależnie od `czas_kuriera`; sortowanie wg committed dotyczy TYLKO odbiorów, nie carried. Order A (picked_up, ck=17:30) ląduje przed B (assigned, ck=16:45).
- **Realny skutek:** 110 przypadków/dzień (`pickup_lateness_shadow.jsonl`), case cid=393 Michał K — odbiór pokazany +19 min względem okna. Carried-first chroni stygnące jedzenie (intencja Sioux 2026-06-22), ale formalnie SOFT bije HARD.
- **Kierunek fixu:** constrained lexicographic reorder (wariant „zero-harm D" z handoffu 24.06) — carried zostaje na froncie TYLKO gdy nie wypycha committed-odbioru poza okno; w przeciwnym razie odbiór „po drodze". **Wymaga replayu** (`carried_first_replay.py` per delay_tol, OOS 7-14 dni) przed flipem.

### P-2. R6=35 SOFT na werdykcie — ALWAYS-PROPOSE neutralizuje twardą bramkę jakości [b_jedna_sciezka/f_rozjazd, P1]
- **Reguła:** R6 / R-35MIN-MAX (deklarowane HARD)
- **file:line:** `feasibility_v2.py:1242` (HARD NO) vs `dispatch_pipeline.py:5947,6324,6360,6386` (guard `and not _always_propose_on()`); flags.json:184 `ENABLE_ALWAYS_PROPOSE_ON_SATURATION=true`
- **Dowód:** kandydaci R6>35 dostają `verdict='NO'`, ale `best_effort` budowany jest z `candidates[].plan!=None` niezależnie od verdict (`:6204-6215`); gdy `_always_propose_on()=True`, KOORD pomijane → PROPOSE z `best_effort=True` + banner ⚠️.
- **Realny skutek:** ~20,6% propozycji wychodzi łamiąc R6 (audit §13). Decyzja Adriana 2026-06-23: „autonomia na stałe, nie przywracamy eskalacji".
- **Kierunek fixu:** **NIE fix logiki** — to Twoja polityka. Realny dług = (a) ryzyko UX (operator widzi `best_effort_r6_breach_v2` i myśli że to KOORD); (b) brak tej bramki w dokumentacji `ZIOMEK_LOGIC_REFERENCE.md §5.5`. Kierunek: doprecyzować banner + dopisać do referencji. **Bez replayu** (zmiana dokumentacyjna). Powiązany lewar (zmiękczenie R6→42-45) = osobny temat z kampanii kalibracyjnej, wymaga shadow A/B.

### P-3. Greedy/bruteforce fallback TSP ślepy na frozen-window i R6 [f_rozjazd/e_inwersja, P1]
- **Reguła:** R27 frozen window + R6 per-order
- **file:line:** `route_simulator_v2.py:774-853` (greedy: sort po `leg_min`+`sla_violations`+`total_duration`, ZERO walidacji ck/R6 w insertach), `:445-456` (fallback bez R6/ck reject), `:1371-1385` (post-solve assertion tylko loguje, E2)
- **Dowód:** OR-Tools buduje `time_windows` z frozen ck (`:988-990`, dół=ck−5 twardy); greedy fallback przy INFEASIBLE konstruuje sekwencję wyłącznie na min-jeździe. Górne okno OR-Tools jest luźne (120 min, nie ck+5), więc nawet OR-Tools może planować odbiór późno; greedy w ogóle nie patrzy na ck.
- **Realny skutek:** asymetria systematyczna dla committed orderów (frozen ck = committed). Empirycznie ~2,2k V3274-flagów/dzień to ślad INFEASIBLE OR-Tools przy ciasnych ck-box. Display-layer (P-1, refloor) maskuje część wizualnie.
- **Kierunek fixu:** dodać committed/R6-aware tie-breaker do klucza sortowania greedy/bruteforce (analog soft-objective OR-Tools), LUB twardy post-solve reject committed-out-of-window dla greedy (przy zachowaniu E2 dla OR-Tools). **Wymaga replayu** (czy zmiana key nie psuje rankingu sekwencji; bag<2 z committed).

### P-4. Inwersja precedencji demote→tier-sort gdy LEXR6 flip (uśpione) [e_inwersja, P2-warunkowe]
- **Reguła:** R-LATE-PICKUP tiering + V3.16 demote blind-empty
- **file:line:** `dispatch_pipeline.py:5500` (demote: informed>other>blind) vs `:5545` (OFF-mode sort key `(_lp_tier, _orig_order)` — **bez bucketa demote**)
- **Dowód:** klucz sortu tier-first nie zawiera bucketa demote; blind_empty+tier=0 ma key `(0, idx)` < informed+tier=1 `(1, idx)` → blind_empty wraca na top mimo demote na dnie. W Opcji B (`:5540`) bucket jest w kluczu → problem nie istnieje.
- **Realny skutek:** dziś **uśpione** — `ENABLE_OBJM_LEXR6_SELECT=false` (flags.json:191) + `ENABLE_NO_GPS_EQUAL_TREATMENT=true` czyni demote ~martwym. Ryzyko materializuje się przy flip LEXR6 (planowany po canary).
- **Kierunek fixu:** dołożyć bucket demote do klucza OFF-mode sortu, ZANIM LEXR6 wejdzie live. **Bez replayu** (poprawka spójności klucza), ale przetestować w canary LEXR6.

### P-5. Recanon-on-write niesymetryczny — cancel-path bez recanon + best-effort osobny [f_rozjazd, P2]
- **Reguła:** RECANON-ON-WRITE (kanon = część każdego zapisu)
- **file:line:** `panel_watcher.py:618` (assign), `:662` (deliver), `:712` (pickup) — Z recanon; `:667-682` (cancel/return) — **BEZ recanon**. Recanon zawsze w osobnym `try/except` (best-effort), nie atomowo z `save_plan` (`:509`/`:618`).
- **Dowód:** deliver/pickup wołają `recanon_courier()`; `_remove_stops_on_return` po `remove_stops()` nie wywołuje recanon — tylko warning log. Surrogate coords `(0.0,0.0)` (`:485,495`) zapisywane przed recanon; gdy recanon padnie (GPS missing), plan persistuje niezkanonizowany.
- **Realny skutek:** rzadki (anulowanie zlecenia między dwoma dostawami tej samej restauracji → rozspojony plan do następnego ticku, ≤5 min). Okno otwiera się gdy flagi recanon ON (drop-in `dispatch-panel-watcher`).
- **Kierunek fixu:** dołożyć recanon do cancel-path (symetria 4 handlerów); rozważyć recanon **wewnątrz** save jako inwariant zamiast best-effort. **Bez replayu** (spójność architektoniczna), test e2e na cancel.

### P-6. SLA-gate ≈ duplikat R6 z asymetrią bypass [d_duplikat, P3]
- **file:line:** `feasibility_v2.py:1160-1234` (SLA, z pre-existing bypass `:1217-1220`) vs `:1237-1248` (R6 per-order, bez bypass)
- **Dowód:** oba egzekwują 35 min od pickup. SLA ma „pre-existing bypass", R6 nie. Audit §8 sklasyfikował jako „downgrade" (konserwatywny, brak strat, szum diagnostyczny).
- **Skutek:** redundancja + DRY-drift; kandydat przechodzący R6 rzadko pada na SLA. **Powiązane z warstwą B carry-blind** (najgorszy realny breach „wybaczony" przez pre-existing bypass — patrz best-effort carry-blind R6 23.06).
- **Kierunek:** ujednolicić jedną bramkę 35-min z jawnym, testowanym kontacktem bypass. `test_sla_preexisting_bypass.py` jest non-hermetyczny (zależny od live-flag TSP ordering) — uszczelnić. **Bez replayu**, ale ostrożnie (warstwa B otwarta).

### P-7. bonus_penalty_sum — 19 termów montowanych z 19 rozproszonych miejsc [c/higiena, P3]
- **file:line:** `dispatch_pipeline.py:4734` (montaż) z obliczeniami na `:3887,4279,4283,4387,3985,4026-4063,...`
- **Dowód:** addycja przemienna (zero wpływu matematycznego), ale audit pojedynczej kary wymaga śledzenia 19 osobnych sites.
- **Kierunek:** refactor do `bonus_dict={k:v}` + `sum(bonus_dict.values())`. **Higiena, nie bug.** Bez replayu (zachowanie identyczne).

---

## 4. MARTWY / REDUNDANTNY KOD + HIGIENA FLAG

| Element | Stan | file:line | Akcja |
|---|---|---|---|
| `feasibility_check` (flags.json) | **zombie** — 0 odczytów w `dispatch_v2/*.py` | flags.json:5 | usunąć z flags.json |
| `R7 long-haul peak` (99 km) | **martwy** — w Białymstoku ~15 km nigdy nie odpala; „parkowany lewar" pod Warszawę | `feasibility_v2.py:485-491`, `common.py:684` | zostawić (świadome) + dopisać komentarz „dormant, expansion-only" |
| `ENABLE_OBJM_LEXR6_SELECT` blok | **martwy-ale-on** (flaga OFF, kod żyje) | `dispatch_pipeline.py:5555-5566`, `:1180-1208` | zostawić do flip po canary; usunąć jeśli OFF >30 dni |
| `_no_gps_uncertainty_rescue` (B3) | **martwy** (flaga OFF + ścieżka wyłączona przez ALWAYS-PROPOSE) | `dispatch_pipeline.py:2261,5967`, flags.json:198 | zostawić (re-enablement) LUB usunąć — decyzja Adriana |
| `_demote_blind_empty` | **~inert** (NO_GPS_EQUAL_TREATMENT ON neutralizuje dla no_gps) | `dispatch_pipeline.py:2199-2208,5500`, flags.json:202 | zostawić (equal-treatment świadome); UWAGA: P-4 przy flip LEXR6 |
| `commit_divergence_gate` (~68 linii) | **dead-but-wired** (flaga OFF, kod kompletny) | `dispatch_pipeline.py:5987-6054`, flags.json:147 | rozstrzygnięcie: usunąć vs trzymać (patrz §5) |
| `difficult_case_redirect` | **dead-but-wired**, ale shadow-logging aktywny | `dispatch_pipeline.py:6072-6127`, flags.json:148 | trzymać (shadow zbiera telemetrię pod flip) |
| 34 flagi rzeczywiście nieczytane + ~24 tylko w tools/tests | **config bloat** (nie 61 jak w surowym znalezisku — liczba skorygowana) | flags.json (linie 22-113) | przegląd: usunąć 34 martwe; rozważyć separację tool-flag do osobnego `.conf` |
| `EXCLUDE_BY_CID` runtime-list | nie martwy, ale **udokumentowany jako stała** — jest hot-reload via `/stop` | `courier_resolver.py:1464`, `manual_overrides.py:127-150` | dopisać do ref „hot-reload via /stop Telegram" |
| `rule_weights.json` metadata | data `_updated:2026-04-16` fałszywa (plik z 2026-05-29) | `dispatch_state/rule_weights.json` | poprawić metadata (mylące przy decyzjach) |

**Dryft dokumentacja vs żywe flagi (do naprawy w `ZIOMEK_LOGIC_REFERENCE.md`):**
- `ENABLE_HARD_TIER_BAG_CAP` — ref mówi ⚪ OFF, **żywo `true`** (flags.json:197) → HARD bramka aktywna; operator debugujący odrzucenie bag=5 będzie zdezorientowany.
- `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` — ref 🟢 LIVE, **żywo `false`** (flags.json:147).
- `ENABLE_DIFFICULT_CASE_KOORD_REDIRECT` — ref 🟢, **żywo `false`** (flags.json:148).
> Blok „LIVE-STATE CORRECTION" już istnieje w ref, ale główne tabele go nie odzwierciedlają — zsynchronizować tabele z blokiem.

---

## 5. ⭐ WĄTPLIWOŚCI DO WSPÓLNEGO ROZSTRZYGNIĘCIA (decyzje projektowe, nie-z-kodu)

**W-1. carried-first vs committed-time — utrzymać inwersję czy wdrożyć lex-reorder?**
Opcje: (A) status quo — carried-first bezwarunkowe (chroni termikę, ale 110 odbiorów/dzień pokazanych po oknie); (B) wdrożyć constrained lex-reorder „zero-harm D" (carried na froncie tylko gdy nie wypycha committed-odbioru poza okno).
→ **Rekomendacja: B, ale dopiero po replayu** `carried_first_replay.py` per delay_tol + OOS 7-14 dni (zgodnie z Twoją regułą „udowodnij pomiarem przed flipem"). Fix gotowy architektonicznie. Carried-first HARD pozostaje dla świeżego jedzenia ≤20 min.

**W-2. R6=35 na werdykcie — autonomia na stałe czy soft-reintroduce eskalacji w skrajnych breach?**
Dziś: ALWAYS-PROPOSE neutralizuje KOORD dla R6 (świadome, audit §2). Pytanie nie „czy autonomia" (rozstrzygnięte: TAK), lecz: czy dla **ekstremalnych** breach (np. >55 min, niesiony-sunk) chcesz progowy KOORD-redirect, czy nadal czysty PROPOSE+banner?
→ **Rekomendacja: zostaw PROPOSE+banner** (spójne z „Ziomek ma się nauczyć działać bez GPS jak człowiek"), ale **uszczelnij komunikat** (banner ma jawnie mówić „R6 ZŁAMANE świadomie", nie wyglądać jak KOORD) i dopisz bramkę do `ZIOMEK_LOGIC_REFERENCE.md §5.5`. Osobno: kampania R6→42-45 (zmiękczenie standardu) = shadow A/B, nie tutaj.

**W-3. frozen-window górna granica (120 min) + greedy ślepota — domknąć czy zostawić E2?**
Dziś: dół ck−5 twardy, góra 120 min luźna (świadome anti-INFEASIBLE po order#474266); greedy fallback w ogóle nie zna ck. E2 (2026-05-17) świadomie nie odrzuca planu OR-Tools.
→ **Rekomendacja: zostaw E2 dla OR-Tools** (display-layer refloor maskuje), ale **dołóż committed/R6-aware tie-breaker do greedy/bruteforce** (P-3) — to realna luka, nie tylko prezentacja. Wymaga replayu na bag<2 z committed.

**W-4. dead-but-wired gate'y (commit_divergence ~68 l., difficult_case ~55 l.) — usunąć czy trzymać?**
Z2 (czystość) mówi „usuń", ale to opcje rollbackowe gdyby polityka autonomii się zmieniła.
→ **Rekomendacja: trzymać difficult_case** (ma żywy shadow-logging = wartość telemetryczna pod ewentualny flip); **commit_divergence** — bez shadow-loggingu, czysty dead code → albo dorobić shadow-log, albo usunąć. Decyzja: czy R-DECLARED-TIME-divergence ma kiedykolwiek wrócić jako KOORD?

**W-5. NO_GPS equal-treatment + demote ~martwy — utrzymać symetrię, czy demote PRE-selection?**
Dziś: no_gps traktowany równo (equal-treatment ON), demote ~inert. Zmierzyłeś on-time no_gps ≈ GPS (86-88% vs 87%, §15). Pytanie czysto projektowe: czy pre_shift/none (też bez pozycji) mają być wyłączone z demote jak no_gps (pełna symetria), czy asymetria jest celowa?
→ **Rekomendacja: zostaw** (zmierzone jako neutralne, „Ziomek uczy się bez GPS"). Tylko **dopisz w ref**, że demote jest świadomie martwy + napraw P-4 przed flip LEXR6 (inaczej demote nagle ożyje z bugiem).

**W-6. R7 99 km + zombie-flagi — sprzątać teraz czy parkować?**
→ **Rekomendacja: usuń `feasibility_check` (czysty zombie) + 34 martwe flagi teraz** (zero ryzyka); **R7 zostaw** z komentarzem „expansion-only" (parkowany lewar pod multi-city). Rozważ przeniesienie tool-flag do osobnego `.conf` (czytelność audytu).

---

## 6. ODPORNOŚĆ — spec inwariantów i bramek CI (bez implementacji)

Cel: wyłapać **KLASĘ** tych bugów (inwersja precedencji, drift flag, asymetria powierzchni), nie pojedyncze przypadki.

**A. Runtime-inwarianty (fail-loud, w hot-path / shadow):**
1. **INV-FEASIBILITY-FIRST:** assert że żaden kandydat z `feasibility_verdict=='NO'` nie trafia do `top`/selekcji. Punkt: tuż przed sortem `:5471`. Fail → log.error + metryka (nie crash).
2. **INV-GATE-SCORE-DELTA:** assert że KAŻDA nowa delta dopisywana do `final_score` (`:4792-4812`) jest albo na liście „ranking-only-exclude" (`_gate_score_excluding_ranking_deltas`), albo jawnie oznaczona „verdict-affecting". Dziś LOADGOV/SYNCWORKA wyłączone, ale `r1_progressive/v319h_guard/repo_cost/bundle_fit/fix_c` NIE — to luka (każda ujemna delta może wepchnąć gate-score < MIN_PROPOSE). Inwariant: lista delt = lista exclude ∪ lista verdict-safe; brak elementu poza sumą → fail-loud.
3. **INV-CANON-ON-WRITE:** assert że KAŻDY handler modyfikujący plan (assign/deliver/pickup/**cancel**) woła recanon, gdy flaga ON. Dziś cancel-path łamie (P-5). Inwariant: zbiór write-handlerów == zbiór recanon-callerów.
4. **INV-R6-ANCHOR-CONSISTENCY:** assert że anchor R6 w `_compute_per_order_delivery_minutes` (route_simulator) == anchor w `check_feasibility_v2` (feasibility) dla tego samego order (picked_up_at vs pickup_ready_at). Chroni przed dryftem między POD a hard-gate.

**B. Test konformności kolejności (hermetyczny, syntetyczny):**
- **TEST-PRECEDENCE-CARRIED:** worek {carried A ck=17:30, assigned B ck=16:45} — asertuj zamierzoną hierarchię (po decyzji W-1: albo carried-first absolutny, albo lex). Test ZAWODZI gdy ktoś zmieni kolejność warstw w `_apply_canon_order_invariants`.
- **TEST-GREEDY-FROZEN:** wymuś OR-Tools INFEASIBLE (ciasny ck-box) → asertuj że greedy fallback NIE umieszcza committed-pickup poza [ck−5, ck+5] (po decyzji W-3).
- **TEST-VERDICT-GATE-GUARDS:** snapshot-test mapujący KAŻDĄ bramkę verdict → {quality|operational} i obecność `_always_propose_on()` guard. Zmiana klasy bez aktualizacji testu → fail. Chroni przed przypadkowym dodaniem guardu do operational lub usunięciem z quality.
- **TEST-DEMOTE-TIER-BUCKET:** {blind_empty tier=0 score=50, informed tier=1 score=100} → asertuj że informed wygrywa w OBU trybach (Opcja A i OFF-mode). Łapie P-4 przed flip LEXR6.

**C. Bramki CI:**
1. **CI-FLAG-DRIFT:** skrypt diffujący `flags.json` (live) vs tabela stanów w `ZIOMEK_LOGIC_REFERENCE.md`. Rozjazd (jak HARD_TIER_BAG_CAP) → CI fail / warning. Jedno źródło prawdy.
2. **CI-ORPHAN-FLAG:** grep każdego klucza flags.json w `dispatch_v2/*.py` (core) + tools/tests. Klucz z 0 odczytów core-and-aux → fail (łapie `feasibility_check` i przyszłe zombie).
3. **CI-BONUS-COVERAGE:** statyczny check że każdy term w `bonus_penalty_sum` (`:4734`) ma odpowiadające obliczenie i jest w jednym z dwóch zbiorów (gate-affecting / ranking-only). Egzekwuje INV-GATE-SCORE-DELTA na poziomie kompilacji.
4. **CI-HERMETIC-GATE-TESTS:** oznaczyć testy zależne od live-flags (np. `test_sla_preexisting_bypass`) jako non-hermetic i wymusić wariant hermetyczny (patch flag jawnie) — inaczej zielony test nie dowodzi nic.

---

**Pliki kluczowe (wszystkie absolutne):**
`/root/.openclaw/workspace/scripts/dispatch_v2/dispatch_pipeline.py` (feasibility-first :3478/:5471, verdict-gates :5869-6173, gate-score-delta :2118-2141/:4792-4812, demote :5500/:5545) · `/root/.openclaw/workspace/scripts/dispatch_v2/feasibility_v2.py` (R6 :1237, SLA :1160) · `/root/.openclaw/workspace/scripts/dispatch_v2/route_simulator_v2.py` (frozen-window :988-990, greedy :774-853, post-solve E2 :1371-1385) · `/root/.openclaw/workspace/scripts/dispatch_v2/plan_recheck.py` (carried-first/committed :1178-1199) · `/root/.openclaw/workspace/scripts/dispatch_v2/panel_watcher.py` (recanon-on-write :618/662/712, cancel :667-682) · `/root/.openclaw/workspace/scripts/flags.json` (live: :147,148,184,191,197,198,202; zombie :5) · `/root/.openclaw/workspace/scripts/dispatch_v2/courier_resolver.py` (EXCLUDE_BY_CID :1464).