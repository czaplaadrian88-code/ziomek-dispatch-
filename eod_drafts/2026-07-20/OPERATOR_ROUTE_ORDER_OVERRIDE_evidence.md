# Evidence — ENABLE_OPERATOR_ROUTE_ORDER_OVERRIDE (kandydat, ZERO LIVE)

**Data:** 2026-07-19/20 · **Branch:** `feat/operator-route-order` (od `master@7e57085`) ·
**Builder:** subagent silnika (sesja d17bde9a) · **Protokół:** #0 ETAP 0→7 (pełny) ·
**Status:** KANDYDAT — flaga default OFF, nic nie zrestartowane, nic nie flipnięte, bez push.

## CO + WPŁYW + JAK BEZPIECZNIE (prosto)

**CO:** Koordynator ustawia w konsoli kolejność podjazdów kuriera. Ziomek honoruje tę
kolejność w KANONIE (`courier_plans.json`) i przelicza czasy (ETA) dla nowej sekwencji.
Konsola i apka kuriera dostają nową kolejność przez istniejący kanon — bez zmian po ich
stronie.

**WPŁYW:** przy fladze OFF (stan kandydata) zachowanie silnika = 1:1 jak dotąd; jedynym
nowym efektem ubocznym jest telemetria wykrycia pliku override (cień przed flipem).
Przy fladze ON i ważnym wpisie: sekwencja stopów kuriera = sekwencja operatora.

**JAK BEZPIECZNIE:** flaga `ENABLE_OPERATOR_ROUTE_ORDER_OVERRIDE` (decision_flag,
hot-reload flags.json), default OFF; fail-open na każdą anomalię (brak/uszkodzony plik,
zły zbiór id, TTL) = zachowanie dotychczasowe; `czas_kuriera` nietykalny; rollback =
flaga OFF (hot, bez restartu) albo `git revert` jednego commita.

## ETAP 0 — stan na żywo + baseline

- master w chwili odcięcia gałęzi: `7e57085` (== hash z briefu zadania). Worktree:
  `scratchpad/wt-route-order-pkgroot/dispatch_v2` (pkgroot wg lekcji
  [[lekcja-ziomek-scripts-root-import]]; `git worktree list` potwierdza, zero worktree
  zagnieżdżonych w repo — reguła C70).
- **Baseline PEŁNEJ suity w TYM SAMYM harnessie (przed jakąkolwiek edycją):**
  `9 failed / 5197 passed / 27 skipped / 7 xfailed` (6:30 min).
  9 faili = dokładnie znany szum symlink-harnessu z lekcji (obustronny, nie-regresja):
  `test_conftest_flag_strip_guard` ×3, `test_flag_doc_coverage` ×3 (FileNotFound),
  `script_run` ×3 (f4_courier_pos_pickup_proxy, panel_aware_availability,
  panel_packs_bag_reconstruction). **Werdykt regresji = DELTA zbiorów awarii.**
- Multi-sesja: worktree własny, izolowany; żywe repo nietknięte (tylko `git worktree add`).
  Żadnych restartów, flipów, zapisów do żywego `dispatch_state/` ani `flags.json`.

## ETAP 1 — u źródła (warstwa 9: kanon/plan)

Wszyscy writerzy SEKWENCJI kanonu zbiegają się w dwóch funkcjach `plan_recheck`:

| Writer sekwencji | Ścieżka | Pokrycie pinem |
|---|---|---|
| tick 5 min (`run_recheck` → `_gap_fill_plans`, w tym sequence-lock retime) | `_gen_one_bag_plan` / `_retime_one_bag_plan` | hook w OBU |
| `redecide_courier` (override/pickup natychmiast) | `_gen_one_bag_plan` | hook |
| `recanon_courier` (RECANON-ON-WRITE) | `_retime_one_bag_plan` | hook |
| 4 handlery recanon `panel_watcher` (assign/pickup/deliver/return) | wołają `recanon_courier` (assign dodatkowo `redecide`) | pokryte pośrednio |
| surowe zapisy (`_save_plan_on_assign`, `_save_plan_from_pending`, `advance_plan`, `remove_stops`) | zapis zachowujący/kurczący kolejność + NATYCHMIASTOWY `recanon_courier` w tym samym handlerze | pokryte przez recanon (okno = pojedynczy handler) |

Hook = `operator_route_override.pin_stops(...)` wpięty **PO** `_apply_canon_order_invariants`
(F6) i **PRZED** re-czasowaniem, w obu writerach. To decyzja sekwencji u źródła — nie
łatka na renderze (konsola/apka nietknięte).

## ETAP 2 — HARD vs SOFT

- Pin jest NADRZĘDNY wobec **soft-heurystyk kolejności** (F6: carried-first floor,
  no-return coalesce, carried-first relax, lex-window, noncarried-min-drive) — dlatego
  nakładany PO F6; świadome inwersje (relax/no-return) nie są cofane w kodzie — flagi
  i ścieżki F6 bez zmian, pin je tylko przykrywa GDY operator jawnie zdecydował.
- **HARD-y zostają:** (1) `czas_kuriera` NIGDZIE nie jest pisany (R27 — zobowiązanie
  nietykalne; test dowodzi bajt-identyczność orders_state przed/po); (2) clamp
  `_retime_stops` „odbiór nie wcześniej niż committed" działa na sekwencji operatora;
  (3) `_floor_pickups_to_committed` (refloor-at-birth) bez zmian; (4) bramka zapisu L3
  (`ENABLE_PLAN_RECHECK_GATES`, compare-and-keep R6) NIE osłabiona — ocenia JUŻ
  spiętą sekwencję (świadomie: nie zdejmujemy HARD-gate'u zapisu dla pinu; patrz Ryzyka #2).
- Sekwencja operatora łamiąca okno committed (odbiór > czas_kuriera + 5 min po retime)
  → wykonana + JAWNIE zalogowana w `committed_breaches` zdarzenia `applied`
  (koordynator nadzoruje = jego decyzja). Zero cichej zmiany zobowiązań.
- Selekcja / scoring / feasibility NIETKNIĘTE (diff nie dotyka `core/*`,
  `dispatch_pipeline`, `feasibility_v2`, `scoring`, `objm_lexr6`).

## KONTRAKT WEJŚCIA (CTO — bez zmian) + doprecyzowanie semantyki

Plik: **`/root/.openclaw/workspace/dispatch_state/operator_route_overrides.json`**
(kanoniczny katalog żywego stanu silnika — ten sam, w którym `plan_recheck` czyta
`ORDERS_STATE_PATH = /root/.openclaw/workspace/dispatch_state/orders_state.json`;
CODEMAP §4 pułapka 1: katalog `dispatch_v2/dispatch_state/` w repo to NIE stan silnika).

```json
{"courier_overrides": {"<cid>": {"order_ids": ["<zid>", ...],
  "set_by": "<email>", "set_at": "<ISO8601>", "ttl_min": 120}}}
```

- Walidacja: `set(order_ids) == set(aktywnych zleceń kuriera)` (statusy
  assigned/picked_up), bez duplikatów; inaczej IGNOR + zdarzenie `rejected` z powodem.
- TTL: `now - set_at > ttl_min` → `expired`, zachowanie dotychczasowe. Brak/`≤0`/śmieć
  w ttl_min → default 120.
- Odczyt fail-open: brak pliku = zero kosztu i zero szumu; nie-JSON/zły kształt =
  `rejected reason=file_corrupt` (dedup) + zachowanie dotychczasowe.
- **KONTRAKT PRZYPIĘTY przez CTO w trakcie budowy (2026-07-19; panel-side kandydat
  `b5c4972`, gałąź `feat/route-order-panel` w nadajesz_clone) — implementacja ZGODNA
  punkt w punkt:**
  1. `order_ids` = permutacja ZLECEŃ aktywnego worka (sekwencja per-zlecenie, jak
     `orderedFor` konsoli) — NIE lista węzłów; PRZEPLOT pickup/dropoff wyprowadza
     SILNIK z zachowaniem zadanej kolejności dostaw. → Tak działa `_build_pinned`:
     dostawy DOKŁADNIE w kolejności operatora; odbiór zawsze przed swoją dostawą;
     KOLEJNE zlecenia tej samej restauracji = jeden podjazd (odbiory grupą → dostawy
     grupą). To lustro 1:1 projekcji `route_order._canon_order_from_plan`, więc
     konsola po zapisie renderuje stopy w kolejności operatora i jej badge „oczekuje
     na przeliczenie" gaśnie (dowód: `test_pin_transparent_for_surfaces_via_route_order`).
  2. ŻADNEGO częściowego honorowania: zbiór `order_ids` MUSI być identyczny ze zbiorem
     aktywnych zleceń kuriera; każda rozbieżność ⇒ IGNORUJ CAŁY override + zdarzenie
     `rejected/set_mismatch` (z `override_ids`+`active_ids` w evencie — konsola może
     z tego czytać „override unieważniony przez zmianę worka").
  3. TTL = `set_at + ttl_min` (default 120); po wygaśnięciu `expired` + telemetria,
     wpis NIE jest kasowany.
  4. **Silnik = WYŁĄCZNIE CZYTELNIK** pliku override (panel jedyny pisarz, atomic
     tmp+os.replace) — moduł silnika NIGDY nie pisze/nie czyści tego pliku; telemetria
     idzie do OSOBNEGO `operator_route_override_events.jsonl`. Reset = DELETE wpisu w
     panelu (brak wpisu ⇒ własna optymalizacja silnika — ścieżka „brak pliku/wpisu"
     = zero kosztu).
  5. `cid`/`zid` stringi; `set_at` ISO UTC z offsetem (mikrosekundy OK — parser
     `fromisoformat`).
  - Interpretacja pozycji NIESIONEGO: sekwencja operatora obejmuje też carried
    (dropoff-only) — jego pozycja jest honorowana (override nadrzędny wobec
    soft-heurystyk kolejności, w tym carried-first floor; F6 biega PRZED pinem i po
    wygaśnięciu override natychmiast przywraca carried-first). Dowód:
    `test_carried_position_honored`.
  Pin przestawia wyłącznie ISTNIEJĄCE węzły planu — multiset węzłów zachowany (tripwire
  w `_build_pinned`; naruszenie → fail-open `structure_fail`).

## B — która maszyneria przelicza ETA (i dowód, że działa dla nowej kolejności)

**`plan_recheck._retime_stops(stops, pos, anchor_departure, orders_state, now)`** —
istniejąca (F2/F6) maszyneria: macierz OSRM `/table` po punktach `pos→stop1→…→stopN`,
łańcuch czasów legów + `dwell_min` per stop + clamp committed na odbiorach. W
`_gen_one_bag_plan` pin woła ją bezpośrednio po przestawieniu (jak F6 po swoim
reorderze); w `_retime_one_bag_plan` pin przestawia stopy tuż PRZED istniejącym
wywołaniem `_retime_stops` (to samo wywołanie co dotąd liczy nową kolejność). Po
zapisie czasy trafiają do `courier_plans.json.stops[].predicted_at` — konsumowane
1:1 przez konsolę i apkę. Dowody: `test_pin_applies_operator_sequence` (retime path),
`test_gen_path_pins_sequence` (pełny `_gen`: realny OR-Tools TSP + F6 + pin + retime),
`test_czas_kuriera_untouched_and_breach_logged` (clamp ≥ committed na nowej kolejności).

## D — MAPA KOMPLETNOŚCI (ETAP 3, driver `ziomek-cto scope`, klasa kanon-plan-display)

| LP | Miejsce | Werdykt |
|---|---|---|
| 1 | `plan_recheck._apply_canon_order_invariants` (kanon inwariantów) | **TAK** — pin wpięty PO F6 w OBU callerach (`_gen_one_bag_plan`, `_retime_one_bag_plan`); F6 sam NIE zmieniany |
| 2 | 4 handlery recanon `panel_watcher` (assign/deliver/return/pickup) | **TAK (pośrednio, bez edycji)** — wszystkie wołają `recanon_courier` → `_retime_one_bag_plan` → pin; grep potwierdza brak innych writerów sekwencji |
| 3 | `route_order.py` (JEDNO źródło projekcji, silnik+apka) | **N-D + powód:** czysta projekcja kanonu (PURE, bez I/O) — pin działa na WEJŚCIU projekcji (kolejność stops w planie), projekcja renderuje ją verbatim przez `trust_canon`; dowód e2e testem projekcji |
| 4 | `route_podjazdy.py` (alias wsteczny apki) | **N-D + powód:** re-eksport `route_order` — zero logiki, zero kopii |
| 5 | konsola `fleet_state._build_route` (cross-repo) | **N-D + powód:** od Sprint C (08.07) DELEGUJE do `route_order.order_podjazdy` — zweryfikowane grepem na żywym pliku (`fleet_state.py:33` import, `:451` wywołanie). ⚠ wpis twins-registry „konsola ma własną kopię" jest NIEAKTUALNY (sprzed delegacji) — do poprawy przy aktualizacji rejestru skilla |
| 6 | apka kuriera Kotlin (render) | **N-D + powód:** konsumuje `stop_sequence` z courier_api, który czyta kanon — kolejność przychodzi z `courier_plans.json`; zero zmian po stronie apki (kontrakt cross-język bez zmian) |

Bliźniaki selekcji/feasibility (best_effort↔objm_lexr6, feasibility↔greedy↔plan_recheck):
**N-D + powód** — zmiana dotyczy wyłącznie sekwencji worka JUŻ przypisanego kuriera
(warstwa 9); wybór kuriera/feasibility/scoring nietknięte (zero diffu w tych plikach).
Serializer A+B (`shadow_dispatcher._serialize_result`): **N-D + powód** — zdarzenia pinu
powstają w procesach plan-recheck/panel-watcher (nie w ticku silnika), więc telemetria
idzie do DEDYKOWANEGO jsonl obok innych shadow-jsonl w `dispatch_state/` (wzorzec
`bug4_reseq_shadow.jsonl`), nie do `shadow_decisions.jsonl`; żadna nowa metryka decyzji
silnika nie powstaje.

## E — telemetria (ZAWSZE, też przy flag OFF)

Plik: `/root/.openclaw/workspace/dispatch_state/operator_route_override_events.jsonl`
(append O_APPEND, fail-soft). Zdarzenia `operator_route_override_{applied|rejected|expired}`:
`ts, event, cid, stops(liczba), flag_on, set_by, set_at` + per typ: `reason`
(file_corrupt/malformed/duplicate_ids/set_mismatch/foreign_stops/flag_off/structure_fail),
`would_apply=true` przy flag_off po PEŁNEJ walidacji (cień „zadziałałoby" przed flipem),
`ttl_min/age_min` (expired), `changed` + `committed_breaches[{oid, late_min}]` (applied —
emitowane wyłącznie po UDANYM zapisie planu, z finalnych czasów). Dedup powtórek
rejected/expired per (cid, set_at, powód) w procesie (tick co 5 min nie spamuje);
`applied` logowane przy każdym zapisie z pinem (świadomie — widać aktywność pinu).
Hermetyczność: pod pytestem moduł nie czyta/nie pisze żywych ścieżek domyślnych
(guard `DISPATCH_UNDER_PYTEST`/`PYTEST_CURRENT_TEST`; testy podpinają tmp przez monkeypatch).

## F — rejestr flag

- `common.py`: stała `ENABLE_OPERATOR_ROUTE_ORDER_OVERRIDE = False` + wpis w
  `ETAP4_DECISION_FLAGS` (fingerprint + conftest-strip + flag_registry).
- `tools/flag_lifecycle_seed.py --merge`: rejestr 506→**507** flag, „MERGE: zachowano
  pola kuracji dla 506 wpisów" (kuracja chroniona). `tools/flag_lifecycle_check.py`:
  **✅ 0 błędów**.
- `flag_doc_coverage_check` (REF z worktree + żywy flags.json): **„brak nowego driftu"**;
  flaga udokumentowana w `ZIOMEK_LOGIC_REFERENCE.md` (tabela flag, nowy wiersz).
- `flags.json` (żywy) NIETKNIĘTY — flip = osobna decyzja za ACK.

## G — testy (liczby)

- **Nowe:** `tests/test_operator_route_override.py` — **14/14 passed** (2.5 s):
  pin sekwencji + zdarzenie applied; e2e projekcja powierzchni (route_order
  trust_canon = kolejność operatora); ON≠OFF (ta sama sytuacja, inne sekwencje +
  `rejected/flag_off/would_apply` w cieniu); idempotencja (brak oscylacji F6↔pin);
  grupowanie sąsiadów tej samej restauracji w jeden podjazd; pozycja niesionego wg
  operatora; set_mismatch; duplicate_ids; TTL expired; uszkodzony plik fail-open;
  brak pliku = zero zdarzeń; czas_kuriera nietykalny + clamp + breach-log;
  pełny `_gen` (redecide, realny OR-Tools) ON i OFF.
- **Pełna regresja (ten sam harness co baseline, identyczne env — reguła C69/C70):**
  - Bieg #1 po zmianach: `10 failed / 5210 passed` — DELTA = **+1**:
    `test_flag_lifecycle_zp107.py::test_curation_complete_on_committed_registry`
    (mój świeży wpis rejestru bez pól kuracji). **Fix u źródła:** wpis skurowany
    (`curated_at=2026-07-19`, `lifecycle_seeded=false`, realne serwisy
    `dispatch-plan-recheck + dispatch-panel-watcher`, rollback=flags.json hot);
    `flag_lifecycle_check` → kuracja **507/507, 0 błędów**.
  - Bieg #2 (finalny): **`9 failed / 5211 passed / 27 skipped / 7 xfailed`** —
    **DELTA zbiorów FAILED vs baseline = 0 nowych, 0 zniknięć** (5211 = 5197
    baseline + 14 nowych testów). 9 faili = bajt-ten-sam zestaw szumu harnessu
    obecny na CZYSTYM `7e57085` (baseline zmierzony przed pierwszą edycją):
    `conftest_flag_strip_guard` ×3, `flag_doc_coverage` ×3 (FileNotFound),
    `script_run` ×3 (`f4_courier_pos_pickup_proxy`, `panel_aware_availability`,
    `panel_packs_bag_reconstruction`) — rodziny wprost wymienione w lekcji
    [[lekcja-ziomek-scripts-root-import]] jako obustronny artefakt symlink-harnessu.
  - ⚠ Liczba bezwzględna „5222/0/27/8" cytowana z log-u innej sesji pochodzi z
    INNEGO harnessu (inny pkgroot/env) — zgodnie z lekcją werdyktem jest DELTA w
    identycznym harnessie, nie liczba bezwzględna. Finalna suita na KANONIE
    (żywy checkout) należy do etapu apply na master, jak przy cherry-pickach.
- e2e przez dotknięte warstwy: zapis kanonu (plan_manager CAS) → recanon/redecide →
  retime OSRM → projekcja `route_order.order_podjazdy(trust_canon)` (= konsument
  konsoli i apki) — w testach 2 i 12 (nie tylko unit modułu).

## Ryzyka / znane ograniczenia (uczciwie)

1. **Okno surowego zapisu:** `_save_plan_on_assign`/`advance_plan`/`remove_stops` piszą
   plan bez pinu, ale w tym samym handlerze natychmiast biegnie `recanon_courier`
   (pin). Okno = pojedynczy handler; dodatkowo zmiana worka zwykle unieważnia override
   (set_mismatch) — to zamierzona semantyka kontraktu.
2. **L3 gate (gdy ON):** świeży REGEN ze spiętą sekwencją łamiącą R6 vs istniejący plan
   bez łamania → keep-existing (pin nie wejdzie tym writerem; wejdzie ścieżką retime,
   która bramki L3 nie ma). Świadomy wybór: nie osłabiamy HARD-gate'u zapisu. Jeżeli
   CTO chce „pin przebija L3" — osobna decyzja (dziś: bezpieczniej nie).
3. **bug4 reseq shadow:** przy aktywnym pinie „frozen" = sekwencja operatora, więc
   shadow może raportować „fresh lepszy" — OCZEKIWANE (override ≠ optimum solvera).
   Recenzent/przyszła sesja nie powinna tego „naprawiać" flipem.
4. **Detektor no-return (WARN `BACK_TO_DEPARTED_RESTAURANT`)** biega w F6 PRZED pinem —
   powrót wymuszony przez operatora nie jest logowany tym WARN-em (jest widoczny w
   zdarzeniu applied + to nadzorowana decyzja operatora).
5. Dedup zdarzeń jest per-proces — po restarcie serwisu jednorazowa powtórka
   rejected/expired (nieszkodliwe).
6. Zlecenia tej samej restauracji ROZDZIELONE w sekwencji operatora = dwa podjazdy
   (powrót) — jawna decyzja operatora, nie scalamy za jego plecami.
7. **Writer pliku override (konsola) = OSOBNY kandydat** (poza tym zakresem); kontrakt
   po stronie silnika gotowy i przetestowany na plikach.

## Co MUSI sprawdzić recenzent

1. Umiejscowienie hooków: PO F6, PRZED retime, w OBU writerach; `emit_applied` TYLKO po
   udanym `save_plan` (grep `operator_route_override` w plan_recheck).
2. Zgodność doprecyzowania semantyki (grupowanie sąsiadów tej samej restauracji) z tym,
   co będzie pisał panel — raport do CTO w deliverable; kontrakt sam w sobie niezmieniony.
3. Diff NIE dotyka selekcji/scoringu/feasibility/serializera (git show --stat).
4. DELTA pełnej regresji == 0 nowych faili vs baseline TEGO harnessu.
5. Fail-open na każdej ścieżce (pin_stops nigdy nie rzuca; wyjątek → stops bez zmian).
6. Hermetyczność: moduł inertny pod pytestem na domyślnych ścieżkach (istniejące testy
   recanon/gen nie widzą modułu; dowód = zielona pełna regresja).
7. Przed flipem: zasilić cień realnym wpisem (konsola lub ręczny plik) i odczytać
   `operator_route_override_events.jsonl` (would_apply) ≥1 dzień; flip za ACK Adriana.

## Rollback

- Hot: `ENABLE_OPERATOR_ROUTE_ORDER_OVERRIDE=false` w flags.json (albo brak klucza —
  default OFF) — zachowanie 1:1; ewentualnie usunąć plik override (fail-open).
- Kod: `git revert <commit>` (jeden commit, jawne ścieżki).

## Linie DoD (bramka mechaniczna drivera ziomek-cto)

regresja: DELTA vs baseline = 0 failed nowych i 0 zniknięć (pełna suita, harness pkgroot ZIOMEK_SCRIPTS_ROOT + -p no:cacheprovider; baseline czysty 7e57085 = 9 failed/5197 passed/27 skipped/7 xfailed, kandydat finalny = 9 failed/5211 passed/27 skipped/7 xfailed = 5197 + 14 nowych; 9 faili = bajt-identyczny obustronny szum harnessu: script_run ×3, flag_doc_coverage ×3, conftest_flag_strip_guard ×3 — potwierdzony na czystym masterze)
e2e: zapis kanonu (plan_manager CAS) → recanon_courier/redecide_courier → pin → _retime_stops (OSRM) → projekcja route_order.order_podjazdy(trust_canon) = konsument konsoli+apki; testy test_pin_transparent_for_surfaces_via_route_order + test_gen_path_pins_sequence (realny OR-Tools); nowe testy 14/14
pozytywny-wplyw: nowa zdolność ownera (kanon honoruje sekwencję operatora + przelicza ETA) — ON≠OFF udowodnione testami (pin zmienia zapisany kanon; OFF bajt-identyczny poza telemetrią wykrycia would_apply); okno cienia would_apply przed flipem, flip za ACK Adriana (ETAP 5/6 flipa poza zakresem kandydata)
rollback: flags.json ENABLE_OPERATOR_ROUTE_ORDER_OVERRIDE=false (hot-reload, bez restartu; default OFF w common.py) / git revert jednego commita / DELETE wpisu cid w panelu (brak wpisu = własna optymalizacja)
N-D: feasibility_v2.py — powód: pin działa w warstwie 9 (kanon worka JUŻ przypisanego kuriera) PO decyzji feasibility; HARD-checki i R6 nietknięte, żadna reguła feasibility nie zmienia się ani nie jest omijana (SOFT nie osłabia HARD)
N-D: route_simulator_v2.py — powód: TSP/symulator dalej liczy sekwencję bazową i czasy jak dotąd; pin przestawia GOTOWE węzły planu po F6, a czasy liczy _retime_stops — semantyka symulatora bez zmian (zero diffu)
N-D: core/candidates.py — powód: pętla per-kurier/scoring/selekcja kandydatów nie uczestniczy w pinie (override dotyczy worka już przypisanego); zero diffu, testy selekcji zielone w pełnej regresji
N-D: sla_anchor.py — powód: kotwice SLA/R6 nieruszone; pin nie zmienia anchorów ani progów, wyłącznie kolejność węzłów + przeliczenie predicted_at istniejącą maszynerią
N-D: panel_watcher.py — powód: jego 4 handlery recanon (assign/pickup/deliver/return) wołają plan_recheck.recanon_courier → _retime_one_bag_plan, w którym siedzi pin — pokrycie przez punkt zbiegu bez edycji handlerów (dowód: test_pin_applies_operator_sequence przechodzi przez recanon_courier reason=assign)
N-D: objm_lexr6.py — powód: bliźniak selekcji best-effort nietknięty — pin nie dotyka selekcji (warstwa 7), tylko kanonu (warstwa 9)
N-D: shadow_dispatcher.py — powód: serializer A+B bez zmian — zdarzenia pinu powstają w procesach plan-recheck/panel-watcher (poza tickiem silnika) i idą do dedykowanego operator_route_override_events.jsonl (wzorzec bug4_reseq_shadow.jsonl); diff nie dodaje kluczy metrics
N-D: route_order.py — powód: czysta projekcja kanonu (PURE) — pin działa na jej WEJŚCIU (kolejność stops w courier_plans); projekcja renderuje verbatim, dowód e2e testem projekcji
N-D: route_podjazdy.py — powód: alias re-eksportu route_order (zero logiki)
