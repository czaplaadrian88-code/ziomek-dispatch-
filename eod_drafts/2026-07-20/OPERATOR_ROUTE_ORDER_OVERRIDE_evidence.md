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
  (`ENABLE_PLAN_RECHECK_GATES`, compare-and-keep R6): BEZ pinu bajt-w-bajt jak dotąd;
  przy AKTYWNYM pinie REJECT nie blokuje zapisu (polityka przypięta v3 — pin wygrywa
  z bramką biznesową), a werdykt idzie GŁOŚNO do eventu applied (`l3_would_reject`)
  + WARNING; dodatkowo NIEZALEŻNA od L3 ewaluacja HARD po pinie (r6/no_return/grafik)
  raportuje każdy breach.
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

- **Nowe (v2):** `tests/test_operator_route_override.py` — **21/21 passed** (4.1 s):
  14 z v1 + 7 v2 (retime-fail abort w _gen; retime-fail event w ścieżce recanon;
  no-return w hard_breaches; ttl<=0→120; przyszły set_at→odrzut; cache mtime;
  recanon po raw-save re-nakłada pin) + wzmocnione asercje (R6>40/alarm40 przy
  odsuniętym niesionym; alert R27>10 w committed_breaches). v1 pokrywały:
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
  - Bieg #2 (v1): `9 failed / 5211 passed / 27 skipped / 7 xfailed` — DELTA=0.
  - Bieg #3 (v2 FINALNY, po fixach NO-GO Sola): **`9 failed / 5221 passed /
    24 skipped / 7 xfailed`** — **DELTA zbiorów FAILED vs baseline = 0 nowych,
    0 zniknięć** (5221 = 5197 baseline + 21 nowych testów + 3 warunkowe skipy,
    które w tym biegu przeszły; skipped 27→24 = zmienność warunkowych skipów,
    nie faili). 9 faili = bajt-ten-sam zestaw szumu harnessu
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
2. **L3 gate (LIVE):** [ZAKTUALIZOWANE v3 — decyzja CTO „pin przebija L3" WYDANA]
   przy aktywnym pinie REJECT nie blokuje zapisu; werdykt raportowany
   (`l3_would_reject` + WARNING + licznik `l3_regen_reject_pin_override`); bez pinu
   L3 bajt-w-bajt. Pozostałe ryzyko: koordynator może świadomie utrwalić sekwencję
   łamiącą R6 — widoczne w hard_breaches/alarm40 (nadzoruje człowiek).
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

## v2 — odpowiedź na NO-GO Sola (2026-07-19, polityka HARD przypięta przez CTO)

**Werdykt Sola:** NO-GO („pin omija bramki HARD, może zapisać plan po nieudanym
retime, deklarowany punkt zbiegu writerów nie jest kompletny") — pełny log:
`scratchpad/sol_route_order_engine_review.log`. **Polityka przypięta przez CTO:**
override WYKONUJEMY (koordynator nadzoruje — intencja ownera, precedens
carried-first relax), silnik (a) URUCHAMIA ewaluację HARD po pinie i (b) GŁOŚNO
raportuje naruszenia (wzorzec KOORD-grade z BUG C); VETO wyłącznie techniczne
(nie umiemy policzyć prawdziwych czasów).

| # Sol | Fix v2 | Dowód |
|---|---|---|
| 1. pin omija bramki HARD (retime zapisuje bezwarunkowo, _gen tylko L3-opcjonalny) | `_operator_pin_hard_report(final_stops, orders_state)` w OBU writerach po pinie+retime, NIEZALEŻNIE od flagi L3: r6 per-zlecenie (kotwica 1:1 z L3 przez NOWE `_l3_bag_time_ages` — `_l3_bag_time_max_min` DELEGUJE, zero drugiej kopii) + `alarm40` (poziom Alarmu OD-07) + no_return (`_detect_departed_pickup_revisit`, parytet z F6); wynik → `hard_breaches[]` w evencie applied + WARNING `OPERATOR-OVERRIDE HARD BREACH`; zero veta (polityka) | testy `test_carried_position_honored_with_hard_breach_logged` (r6>40, alarm40=True, relax utrwalony + zalogowany) i `test_no_return_breach_logged_in_applied` |
| 2. R27 >10 min bez ALERT | próg = `COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN` (10.0, wspólny z BUG C); wpis `committed_breaches[].alert` + pole `r27_alert` + WARNING `OPERATOR-OVERRIDE R27 ALERT` — zobowiązanie NIEZMIENIONE, bez veta | `test_czas_kuriera_untouched_and_breach_logged` (late ~17 min ⇒ alert=True, r27_alert=True) |
| 3. `_gen` zapisywał przestawione stopy ze STARYMI czasami przy retime=None | ABORT: pin przestawił + retime None ⇒ `return False` (poprzedni plan NIETKNIĘTY) + event `rejected/retime_failed` (bez dedupu — powtórki = czas awarii OSRM); reorder w zmiennej tymczasowej (wyjątek w bloku nie zostawia półproduktu); analogicznie ścieżka retime (zapisu nie było — dodany event) | `test_gen_retime_fail_aborts_keeps_plan` (plan pozostaje None) + `test_retime_path_retime_fail_event_plan_untouched` (plan_version bez zmian) |
| 4. writerzy poza chokepointem: GC + okno raw-save | **GC** (`_gc_courier_plans`, flaga `ENABLE_COURIER_PLANS_GC`, default dry_run): operacje = `invalidate_plan` (cały plan), `remove_stops` per terminal oid (plan_manager.py:429 — czysty filtr listy, WZGLĘDNA KOLEJNOŚĆ ZACHOWANA), `gc_invalidated` (sprzątanie unieważnionych) — GC NIE permutuje sekwencji, wyłącznie kurczy/unieważnia; skurczenie worka ⇒ następny pin widzi set_mismatch (kontrakt: override void przy zmianie worka). **Okno raw-save→recanon** (panel_watcher:557→685): pre-existing (sprzed pinu), przejściowe sekundy w tym samym handlerze; samo się goi — chokepoint re-nakłada pin | analiza kodu GC (remove_stops = list-comprehension filter) + test `test_recanon_after_raw_save_reapplies_pin` (surowy zapis nadpisuje pin ⇒ recanon przywraca) |
| 5. ttl_min<=0 dawało 0 (natychmiastowy expiry); przyszły set_at akceptowany | `_ttl_min`: brak/śmieć/<=0 ⇒ default 120; `set_at > now + 2 min` (skew) ⇒ `rejected/invalid_set_at` | `test_ttl_zero_defaults_to_120`, `test_future_set_at_rejected` |
| 6. koszt OFF: flaga po odczycie, brak cache | `pin_stops`: flaga czytana PRZED jakimkolwiek I/O pliku (load_flags = własny cache); `_load_doc` z cache po (mtime_ns, size) — parse WYŁĄCZNIE przy zmianie pliku, brak pliku = pojedynczy `os.stat`; corrupt też cache'owany (bez re-parse spamu). Cień would_apply przy OFF ZOSTAJE (wymóg E briefu) — koszt stat, nie parse | `test_doc_cache_parses_once_per_mtime` (2 biegi = 1 parse; bump mtime = +1) |
| 7. luki testowe (retime-fail, R6>40, R27>10, no-return, E2E) | 7 nowych testów (21 łącznie) — patrz kolumna „Dowód" wyżej; E2E przez panel_watcher = chokepoint recanon (handlery wołają `recanon_courier` — test raw-save→recanon odtwarza dokładnie sekwencję handlera assign) | plik testów, 21/21 |

Grafik w hard-report: N-D — retime startuje z kotwicy `_start_anchor` (w `_gen`
dodatkowo floor `available_from`), więc pin nie może wyprodukować czasu przed
startem zmiany; osobny wymiar „grafik" nie ma read-only odpowiednika w tej
warstwie (odnotowane w docstringu `_operator_pin_hard_report`).

## v3 — odpowiedź na re-review Sola (2026-07-20, 3 blokery + drobiazgi)

Log: `scratchpad/sol_engine_v2_rereview.log`. PASS-y Sola z v2 (R6/alarm40,
no-return delegacja, R27 ALERT, GC jako filtr, koszt hard-reportu) — bez zmian.

| # | Bloker/uwaga | Fix v3 | Dowód |
|---|---|---|---|
| 1 | L3-VETO: `ENABLE_PLAN_RECHECK_GATES` jest LIVE — L3 REJECT blokował zapis pinu | Przy AKTYWNYM pinie REJECT NIE blokuje: zapis następuje, werdykt idzie do applied (`l3_would_reject=True` + `l3_detail{fresh_r6,exist_r6}`) + WARNING `OPERATOR-OVERRIDE L3-REJECT OVERRIDDEN` + licznik `l3_regen_reject_pin_override`. Bez pinu — zachowanie L3 1:1. Veto zostaje wyłącznie techniczne | `test_l3_reject_overridden_by_pin` (fresh 37>35, existing 22<35 ⇒ REJECT; zapis nastąpił, event z detalem) |
| 2 | GRAFIK nieoceniany w hard-report | Breach `grafik` w `_operator_pin_hard_report`: KAŻDY stop z `predicted_at` > shift_end + tol; okno z TEGO SAMEGO źródła co feasibility — NOWE `courier_resolver.resolve_shift_end_by_cid` = kompozycja ISTNIEJĄCYCH `match_courier` + `_shift_end_dt` (lustro 1:1 `resolve_shift_start_by_cid`, zero kopii logiki); tolerancja = `V324_HARD_REJECT_DROPOFF_AFTER_SHIFT_MIN` (parytet V3.24-A). Working-override 'pracuje' może legalnie wydłużać pracę → wpis raportowy, nie veto (docstring). Start zmiany dalej N-D (kotwica `_start_anchor`+floor available_from wyklucza czas przed zmianą) | `test_grafik_breach_logged_in_applied` (shift_end=NOW ⇒ grafik breaches z excess>5) |
| 3a | STALE-ETA: F6-reorder + retime=None + pin changed=False ⇒ save ze starymi czasami | Flaga stanu `_f6_stale` w F6; przy aktywnym pinie retime finalnej sekwencji wymagany gdy `changed OR _f6_stale`; fail ⇒ veto techniczne (rejected/retime_failed), plan nietknięty. Pre-existing fallback F6 BEZ pinu = nietknięty (zmiana globalna F6 poza zakresem kandydata — jawnie odnotowane) | `test_gen_f6_stale_with_pin_unchanged_aborts` (F6 przestawia na kolejność pinu, retime pada ⇒ brak zapisu + event) |
| 3b | Brakująca komórka OSRM ⇒ cichy leg 0 min | `_retime_stops(..., strict_cells=True)` w ścieżkach pinu (gen-pin i retime-writer przy aktywnym ctx): nieprawidłowa komórka ⇒ None ⇒ veto techniczne. Default `strict_cells=False` = legacy bajt-w-bajt dla wszystkich dotychczasowych callerów (F2/F6/L3) | `test_missing_osrm_cell_vetoes_pin` (macierz z duration None ⇒ plan_version bez zmian + rejected/retime_failed) |
| 4 | TTL przyjmował Infinity/bool; naiwny set_at zgadywany jako UTC | `_ttl_min` v3: liczba CAŁKOWITA 1..1440; bool/NaN/Inf/ułamek/poza zakresem/brak ⇒ 120. `_iso_has_offset`: set_at bez jawnego offsetu ⇒ `rejected/invalid_set_at` | `test_ttl_bool_and_garbage_default_120` (bool@−60min DZIAŁA jako 120, nie wygasa jako 1.0; unit-asercje inf/nan/120.5/99999→120), `test_ttl_infinity_in_file_expires_old_entry` (Infinity w pliku ⇒ 120 ⇒ wpis −200min WYGASA), `test_set_at_without_offset_rejected` |
| 5 | would_apply przy OFF emitowane PRZED konstrukcją | Dry-run `_build_pinned` PRZED werdyktem flagi: strukturalny fail ⇒ `structure_fail` (niezależnie od flagi); `would_apply=True` tylko po pełnej walidacji + KONSTRUKCJI | `test_flag_off_structure_fail_not_would_apply` (duplikat węzła przy OFF ⇒ structure_fail, zero would_apply) |
| 6 | Cache (mtime_ns,size) — naprawa pliku ze stałą sygnaturą niewidoczna | **Udokumentowane ograniczenie (bez zmiany kodu):** sygnatura = (mtime_ns, size); teoretyczna edycja dająca identyczne oba pola nie odświeży cache do `touch`. Panel pisze atomicznie tmp+`os.replace` (nowy inode ⇒ świeży mtime_ns) — w praktyce nieosiągalne; content-hash = koszt pełnego odczytu per wywołanie, sprzeczny z celem cache. Operacyjnie: `touch` pliku wymusza re-parse | ta notatka + `test_doc_cache_parses_once_per_mtime` |

Uwaga Sola (nie-bloker, odnotowana): test raw-save→recanon dowodzi chokepointu
na ręcznej sekwencji zapisu (identycznej z handlerem assign), nie na samym
`panel_watcher._save_plan_on_assign` z jego połkniętym wyjątkiem recanon — pełne
e2e handlera wymaga fixture gastro-eventów (poza zakresem kandydata; okno i tak
domyka następny tick/zdarzenie, a wyjątek recanon jest logowany WARNING).

## v4 — runda 3 Sola (2026-07-20) + DECYZJA UPRASZCZAJĄCA CTO

Log: `scratchpad/sol_engine_v3_rereview.log`. Werdykt r3: NO-GO (węższy) —
strict_cells omijalny po F6; raport grafik niezgodny z feasibility.

| # | Bloker/uwaga r3 | Fix v4 | Dowód |
|---|---|---|---|
| 1 | F6-zatrute-czasy: legacy retime F6 zwraca listę z legami 0 min (None-cell) ⇒ `_f6_stale=False`, pin `changed=False` omijał strict | **DECYZJA UPRASZCZAJĄCA: pin aktywny ⇒ ZAWSZE strict retime FINALNEJ sekwencji** (usunięta ścieżka skip przy changed=False; `_f6_stale` zostaje jako telemetria INFO). Zabija całą klasę (zatrute legi, stale, półstany); piny rzadkie — koszt 1×/table pomijalny. Fail ⇒ rejected/retime_failed, plan nietknięty | `test_f6_poisoned_times_pin_unchanged_strict_veto` (spy: legacy zwraca zatrutą listę, strict=None; asercja że strict POBIEGŁ mimo changed=False ⇒ veto, plan None) |
| 2 | Wyjątek w strict-call (recanon) nie emitował rejected | try/except wokół retime w `_retime_one_bag_plan`: przy pinie wyjątek = ten sam los co None (WARNING **przed** eventem, rejected/retime_failed, return False — plan nietknięty); bez pinu wyjątek propaguje jak legacy 1:1 | `test_retime_exception_in_recanon_emits_rejected` (RuntimeError ⇒ False, plan_version bez zmian, event) |
| 3 | Grafik ≠ semantyka feasibility (grafik-only okno; +5 na pickupie; brak salvage) | **SEMANTYKA 1:1**: (a) okno = EFEKTYWNE `cs.shift_end` — nowe `courier_resolver.effective_shift_end` (pure) jest teraz JEDYNYM źródłem: delegują do niego OBA sity `dispatchable_fleet` (working-override FALLBACK + grafik; bajt-identycznie z konstrukcji: wo=None⇒`_shift_end_dt`, wo+nie-na-zmianie⇒`_effective_working_override_shift_end`) ORAZ nowy `resolve_effective_shift_end_by_cid` (te same wejścia: tiers-name, schedule_utils, `manual_overrides.get_working`, te same flagi hot ENABLE_WORKING_OVERRIDE(+_GRAFIK_CAP)); v3-owe grafik-only resolvery USUNIĘTE (zero martwego kodu); (b) PICKUP po shift_end = breach BEZ tolerancji, pod flagą `ENABLE_V325_SCHEDULE_HARDENING` jak feasibility; (c) DROPOFF > end + `V324_HARD_REJECT_DROPOFF_AFTER_SHIFT_MIN` pod `ENABLE_V324A_SCHEDULE_INTEGRATION`, wyciszany przez TEN SAM predykat `feasibility_v2._end_of_day_salvage(now)` (zero kopii) | `test_grafik_pickup_no_tolerance` (excess ~3.8<5 ⇒ breach), `test_grafik_salvage_suppresses_dropoff_breach` (salvage ⇒ zero grafik), `test_effective_shift_end_working_override_extends` (wo wydłuża w FALLBACK / realna zmiana wygrywa), zaktualizowany `test_grafik_breach_logged_in_applied` (dropoff, stop_type) |
| 4 | TTL: "60"/60.0 akceptowane; ślad L3 mógł zginąć w fail-soft I/O | `_ttl_min` v4: WYŁĄCZNIE int 1..1440, reszta ⇒ 120 + WARNING (dedup per wartość); wszystkie WARNINGi (L3-override, retime-fail, R27, HARD) logowane PRZED zapisem eventu | `test_ttl_strict_int_only` ("60"→120, 60.0→120, 60→60, 1441→120, 1→1); kolejność WARNING→emit w kodzie obu writerów |

Uwaga parytetu flag (świadoma): breach `grafik` liczony pod TYMI SAMYMI flagami
co bramki feasibility (V325 dla pickup, V324A dla dropoff) — gdy bramka w
silniku wyłączona, raport nie twierdzi „HARD breach" którego feasibility by nie
egzekwowało. Refaktor delegacji w `dispatchable_fleet` (2 linie → wspólna
funkcja) = bajt-identyczny z konstrukcji; weryfikacja pełną suitą (fleet ma
gęste pokrycie).

## v5 — runda 4 Sola (2026-07-20; rdzeń PASS, wąskie domknięcia)

Log: `scratchpad/sol_engine_v4_final.log`. Sol r4: delegacja `dispatchable_fleet`
= równoważna bez kontrprzykładu; strict-retime w obu writerach OK.

| # | Punkt r4 | Fix v5 | Dowód |
|---|---|---|---|
| 1 | NAZWA-ŹRÓDŁO: raport rozwiązywał kuriera z `courier_tiers`, flota z `_load_courier_names` (merge kurier_ids+courier_names) — stale tier-alias ⇒ brak matchu grafiku ⇒ wo 23:00 bez GRAFIK-CAP zamiast 14:00 | `resolve_effective_shift_end_by_cid` używa TEGO SAMEGO łańcucha co `cs.name` floty: `_load_courier_names()` + normalizacja zer wiodących (identycznie jak `build_fleet_snapshot`); courier_tiers WYPIĘTE z łańcucha | `test_name_chain_parity_sol_counterexample` (stale tiers-alias ignorowany ⇒ grafik zmatchowany ⇒ cap 14:00, nie 23:00) |
| 2 | Spójność odczytu / snapshot | Rozwiązanie okna = RAZ per wywołanie raportu (nie per stop — tak było od v3); loadery i semantyka „24:00"/północy w 100% delegowane (`_shift_end_dt`, `_effective_working_override_shift_end`, `schedule_utils`, `manual_overrides.get_working`, te same flagi hot). **Resztkowy race:** flip flagi/pliku MIĘDZY odczytem okna a odczytem salvage/flag w tym samym wywołaniu = inherentny dla telemetrii read-only (raport NIE egzekwuje — jedyny efekt to ewentualny pojedynczy wpis breach mniej/więcej); świadomie akceptowane | ta notatka (bez zmiany kodu poza pkt 1/3) |
| 3 | SALVAGE dla PICKUP: feasibility:743 dopuszcza odbiór po shift_end w oknie EOD-salvage | pickup-breach wyciszany TYM SAMYM predykatem `feasibility_v2._end_of_day_salvage(now)` co dropoff (zero kopii): `_hit(pickup) = V325 AND not salvage AND exc>0` | `test_grafik_pickup_salvage_suppressed` (pickup po końcu zmiany + salvage ⇒ zero grafik) |
| 4 | ZAKRES VETA (doprecyzowanie, pre-existing `mark_picked_up` pisze plan PRZED recanonem) | **Claim doprecyzowany:** przy strict-fail „plan nietknięty" znaczy: PIN NIE ZOSTAŁ ZASTOSOWANY — kolejność sprzed pinu zachowana, plan_version bez zmiany od próby pinu; LEGALNE zapisy statusowe (prune węzła odbioru po picked_up, status_at_plan_time) sprzed recanonu ZOSTAJĄ (to nie jest rollback stanu świata, tylko odmowa zapisu SEKWENCJI operatora) | `test_veto_scope_status_writes_persist` (mark_picked_up → strict-fail ⇒ kolejność post-status/pre-pin, status zachowany, plan_version bez zmian, event rejected) |

Obserwacja poza zakresem (kandydat do osobnej fali #0, NIE dotknięte):
pre-existing `resolve_shift_start_by_cid` (konsument: L4 anchor-floor) rozwiązuje
nazwę z courier_tiers — ten sam wzorzec rozjazdu nazw co pkt 1; nie ruszony tutaj
(konsument skalibrowany na obecnym zachowaniu; zmiana wymaga własnej mapy
kompletności i regresji).

## Linie DoD (bramka mechaniczna drivera ziomek-cto)

regresja: DELTA vs baseline = 0 failed nowych i 0 zniknięć (pełna suita, harness pkgroot ZIOMEK_SCRIPTS_ROOT + -p no:cacheprovider; baseline czysty 7e57085 = 9 failed/5197 passed/27 skipped/7 xfailed, kandydat v5 FINALNY = 9 failed/5238 passed/24 skipped/7 xfailed = 5197 + 38 nowych + 3 warunkowe skipy przeszły; obejmuje gęste pokrycie dispatchable_fleet po delegacji effective_shift_end — bajt-parytet potwierdzony suitą; 9 faili = bajt-identyczny obustronny szum harnessu: script_run ×3, flag_doc_coverage ×3, conftest_flag_strip_guard ×3 — potwierdzony na czystym masterze)
e2e: zapis kanonu (plan_manager CAS) → recanon_courier/redecide_courier/_gen_one_bag_plan → pin → _retime_stops (OSRM, strict dla pinu) → L3 (pin-override) → ewaluacja HARD po pinie (r6/no_return/grafik) → projekcja route_order.order_podjazdy(trust_canon) = konsument konsoli+apki; testy test_pin_transparent_for_surfaces_via_route_order + test_gen_path_pins_sequence + test_l3_reject_overridden_by_pin (realny OR-Tools) + test_recanon_after_raw_save_reapplies_pin (sekwencja handlera assign); nowe testy 38/38
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
