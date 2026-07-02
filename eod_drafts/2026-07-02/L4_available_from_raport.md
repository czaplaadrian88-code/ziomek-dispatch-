# FALA L4 — `available_from` jedno źródło dostępności kuriera (F1)

**Data:** 2026-07-02 · **Branch:** `fix/l4-available-from` (worktree `wt-l4`) · **Stan:** KOD+TESTY+DOWODY gotowe, ZERO deployu (rdzeń SERIAL → merge/deploy robi koordynator tmux 9).
**Flaga decyzyjna:** `ENABLE_AVAILABLE_FROM_SINGLE_SOURCE` (default OFF w common.py; NIE w flags.json — wpis = krok deploy).
**Kanon decyzji:** [[preshift-pickup-floor-audit-2026-06-30]] (17 miejsc, decyzje Q1/Q2/Q1b/Q2b) + ZIOMEK_UNIFIED_AUDIT F1 + INV-SRC-AVAILABLE-FROM/INV-FEAS-PICKUP-FLOOR.

---

## 1. STAN ZASTANY (ETAP 0 — re-grep 17 miejsc vs audyt 30.06; żywe liczby)

**Baseline testów (kanon d20bd27 = HEAD worktree):** `3781 passed / 0 failed / 23 skipped / 11 xfailed` — potwierdzone dwoma biegami (kanon bezpośrednio + copy-overlay), plik `FALA1_BASELINE.txt` w scratchpadzie był STARY (3709); aktualny = 3781.

**Dryf linii od audytu (30.06 → 02.07, potwierdzone grepem):**
- #1 candidate clamp: audyt `dispatch_pipeline:5869-5884` / no_gps `:5856` → dziś post-loop pętla `~6019-6062` (no_gps `~6021`, pre_shift `~6032`); metryki kandydata `~5462`; wywołania feasibility `3945` + `7196`.
- #3 departure clamp: audyt `feasibility_v2:789-819` → dziś `~793-826` (stara ścieżka + nowa L4); sygnatura param `available_from` `:440`. route_simulator `:273-277` bez zmian (generyczny konsument `earliest_departure`).
- #5 leak: audyt `plan_recheck:554-594` → dziś anchor `_gen_one_bag_plan` `~622-646`, `_retime_stops:843`, `_floor_pickups_to_committed:864`.
- chokepoint: `state_machine.COURIER_ASSIGNED` `~588-670` (`merged` `~636`).

**Guard „ślepy" — żywy dowód warstwy-2 (baseline L0.5):** `pickup_floor_guard --dry` na żywym stanie → `plans_active=6 unknown_plans=4`. Dekompozycja 6 aktywnych planów (02.07 ~08:35):
| cid | kurier | w dispatchable_fleet? | shift_start (grafik) | wynik |
|---|---|---|---|---|
| 470 | Piotr Zaw | TAK | 09:00 | rozwiązany |
| 447 | Dawid Kal | TAK | 11:00 | rozwiązany |
| 522 | Szymon Sa | NIE | wpis bez godzin (dzień wolny) | unknown |
| 101 | Rafał J | NIE | brak wpisu dziś | unknown |
| 533 | Marcin Pu | NIE | wpis bez godzin | unknown |
| 61 | Krystian | NIE | ex-kurier (INACTIVE) | unknown |
⭐ **Kluczowe ustalenie:** 4 „unknown" to **STALE PLANY** (utworzone 2026-04-22 / 05-03 / 06-19 / 06-29) dla kurierów, którzy NIE mają dziś zmiany (brak grafiku, brak working-override). `shift_start` jest **legalnie None** → „unknown" jest PRAWIDŁOWĄ klasyfikacją, nie ślepotą. To osobny dług: **plan-GC / cykl życia (kontrakt ⑦, INV-LIFE-RECANON-PRUNE / stale-plan)**, NIE resolucja shift_start.

**Multi-sesja (C1):** worktree izolowany (własny indeks git) → zero wyścigu `git add`. Rdzeń silnika = SERIAL, tylko L4 mutuje te pliki. Nie tknięto `flags.json` ani `schedule_utils.py`.

---

## 2. MAPA KOMPLETNOŚCI (17 miejsc audytu + chokepoint + guard)

| # | Miejsce | Klasa | L4 dotknięte? | Uwaga |
|---|---|---|---|---|
| **ŹRÓDŁO** | `courier_resolver.dispatchable_fleet` + helpery | source | ✅ TAK | `available_from_from_shift_start` (pure) + `resolve_shift_start*` + populacja `cs.available_from` (1 pętla, flag-gated) |
| **#1** | candidate ETA `dispatch_pipeline` post-loop | selekcja/ETA | ✅ TAK | `_l4_floor_candidate_eta` (wydzielony, testowalny) — floor eta→available_from; **domyka lukę no_gps** (audyt :5856) |
| **#3** | plan `feasibility_v2` departure-clamp | feasibility HARD | ✅ TAK | nowa ścieżka czyta `available_from` zamiast re-derywacji `shift_start>now & pos∈{pre_shift,no_gps}`; **domyka GPS-przed-zmianą** |
| **#3'** | `route_simulator_v2:273-277` | plan | ⚪ N-D | generyczny konsument `earliest_departure` — feasibility podaje mu available_from; sam nie re-derywuje → bez zmiany |
| **#5** | `plan_recheck._gen_one_bag_plan` anchor | kanon/plan (leak) | ✅ TAK | MINIMALNY floor anchoru ≥ available_from (najszersza dziura); pełna przebudowa = L3 |
| **chokepoint** | `state_machine.COURIER_ASSIGNED` | state | ✅ TAK | NOWE POLE `effective_pickup_at=max(deklaracja, available_from)`; deklaracja `czas_kuriera` NIETYKALNA (Q2/R27) |
| **strażnik** | `tools/pickup_floor_guard` | artefakt-werdykt | ✅ TAK | resolucja shift_start kanonicznym resolverem dla cid'ów z planów poza dispatchable_fleet (nie-ślepy) |
| #4 | `chain_eta.py` | render | ⚪ N-D (L3) | floruje do ready-time; nie do shift_start — pas renderów |
| #6 | `plan_manager.refloor_pickup` | plan | ⚪ N-D | floruje do committed; #5 anchor floor podnosi PRZED nim → spójne |
| #7 | telegram `_candidate_line` | render | ⚪ N-D (L3) | dziedziczy #1 (sclampowany kandydat); legacy gated OFF |
| #9-#13 | apka `_committed_pickup_eta`/`_compute_live_eta`/`_attach_fallback_eta`/plan-pass | render (cross-repo) | ⚪ N-D (L3) | pas renderów apki; dziedziczą po L2/effective_pickup_at |
| #14-#15 | panelsync bliźniak | render | ⚪ N-D | MARTWY (nie serwowany) |
| #16 | konsola `fleet_state._eta_chain` | render | ⚪ N-D (L3) | ma własny `CLAMP_PRESHIFT_PICKUP_ETA` (ON od 30.06); pełny floor = L3 |
| #17-#18 | konsola plan-path / committed-ready | render | ⚪ N-D (L3) | pas renderów konsoli |
| #20-#22 | `canon_eta`/`deliveries.restaurant_view`/`public_tracking` | render | ⚪ N-D (L3) | pas renderów klienta/tracking |
| **SLOT** | INV-FEAS-PICKUP-FLOOR / INV-SRC-AVAILABLE-FROM (xfail) | inwariant | ⚪ N-D | **BRAK istniejącego xfail-slotu** (invariant_slots_l04 ma 5 innych; grep tests/ = 0 dla pickup-floor). Nie dodaję — pełny inwariant „floor na KAŻDEJ powierzchni" wymaga L3 (rendery). Dokument. |

Zasada „bliźniaki RAZEM": (#1↔#3↔#5) engine-clamp ruszone RAZEM przez WSPÓLNĄ funkcję `available_from_from_shift_start` (parytet z konstrukcji, nie dyscypliny).

---

## 3. ZMIANY (pliki, flaga OFF = bajt-w-bajt)

**common.py** — flaga `ENABLE_AVAILABLE_FROM_SINGLE_SOURCE`: stała `= False` (`:319`) + wpis do `ETAP4_DECISION_FLAGS` (`:266`, wzór `ENABLE_COORD_SENTINEL_INGEST_GUARD` commit eb016c1). Rejestr flag = `ETAP4_DECISION_FLAGS` (flag_registry.py to czyta; brak osobnej listy). Czytana `C.decision_flag()`.

**courier_resolver.py** (ŹRÓDŁO):
- `CourierState`: `+ available_from` / `+ available_from_source` (`:363-364`, default None/"unset") + w `to_dict`.
- Helpery (`:1310-1404`): `available_from_from_shift_start(shift_start, now)` = **JEDYNA** definicja floora (pure); `resolve_shift_start(name, schedule, match_courier_fn)` (grafik po nazwie — te same funkcje co dispatchable_fleet); `resolve_shift_start_by_cid`; `resolve_available_from_by_cid`. Fail-soft (None/"unknown").
- `dispatchable_fleet` (`:1725-1734`): 1 pętla po `result`, flag-gated → `cs.available_from,source = available_from_from_shift_start(cs.shift_start, now)`. OFF → pola nietknięte.

**feasibility_v2.py** (#3): sygnatura `+ available_from` (`:440`); blok clamp (`:806-826`) — gdy flaga ON i available_from podane: `earliest_departure = available_from` (jeśli >now) + `af_clamp_applied`; `elif` = stara ścieżka bajt-w-bajt.

**dispatch_pipeline.py** (#1): `_l4_floor_candidate_eta(c)` (`:3401`, wydzielony pure-floor); metryki kandydata `available_from_utc`/`af_source` (`:5462`, gated pop `:5854` gdy source nie policzył → OFF ledger bajt-w-bajt); `available_from=getattr(cs,...)` przy obu `check_feasibility_v2` (`:3990`, `:7196`); post-loop floor gated (`:6065`,`:6117`).

**plan_recheck.py** (#5): floor anchoru `_gen_one_bag_plan` (`:632-646`) — flag-gated `resolve_available_from_by_cid(cid, now)`, `anchor_departure = max(base, available_from)` + log `L4_ANCHOR_FLOOR`. Fail-soft.

**state_machine.py** (chokepoint): `COURIER_ASSIGNED` (`:644-678`) — flag-gated `effective_pickup_at = max(deklaracja czas_kuriera, available_from)` do `merged` (+`effective_pickup_source`/`_af_source`); **czas_kuriera_warsaw/pickup_at NIENARUSZONE**.

**tools/pickup_floor_guard.py**: `_load_fleet_map(plan_cids)` (`:85`) dociąga shift_start kanonicznym resolverem dla cid'ów planów poza dispatchable_fleet; `evaluate` przekazuje cid aktywnych planów (`:374`). NIE flag-gated (pomiar). Env dropinu bez zmian (sprawdzone `systemctl cat` — nie wymaga nowych env).

**tests/test_l4_available_from.py** — 25 testów (nowy plik).

---

## 4. DOWODY

**Metoda testowa (C12e — WALIDOWANA):** conftest pinuje `_SCRIPTS_ROOT` na KANON → goły pytest z worktree testuje KANON. 123 pliki testów zakładają layout `scripts/dispatch_v2/tests` (`Path(__file__).resolve().parents[2]`); symlink-overlay łamie (`.resolve()` idzie za symlinkiem). Użyto **full-copy overlay** (`scratchpad/l4_rebuild_overlay.sh`): rsync worktree → `l4_ov/scripts/dispatch_v2` (realny), symlink pozostałych `scripts/` (schedule_utils, flags.json), sed conftest `_SCRIPTS_ROOT`→overlay. **Walidacja metody: copy-overlay niezmodyfikowanego worktree = `3781 passed / 0 failed` = kanon (C10 — instrument nie kłamie).**

**Testy L4 (25/25 PASS):**
- Źródło pure (4 przypadki): future→(shift_start,"shift_start"); on-shift→(now,"now_on_shift"); None→(now,"unknown"); naive→UTC.
- resolve_shift_start (4 resolucje): normalny wpis / puste godziny→None / brak wpisu→None / GPS-przed-zmianą (floor=shift_start, pos-agnostic).
- Źródło populacja: dispatchable_fleet ON → cs.available_from set; OFF → None/"unset".
- **Kill-test #1**: `_l4_floor_candidate_eta` podnosi eta→available_from (+25min); no-op gdy eta≥af; None bez available_from.
- **Kill-test #3** (ON≠OFF): ON clampuje GPS-przed-zmianą (`af_clamp_applied`, pickup≥shift); OFF (stara ścieżka) NIE clampuje gps na tych samych wejściach; pre_shift pickup≥shift_start; no_gps on-shift = no-op.
- **Kill-test #5**: ON anchor floored → pickup≥shift_start; OFF → pickup<shift_start (leak odtworzony).
- **Chokepoint**: effective_pickup_at=max(11:00 deklaracja, 11:30 available_from)=11:30 (source="available_from"); **czas_kuriera_warsaw/hhmm NIENARUSZONE**; OFF → brak pola.
- **Parytet bliźniaków**: #1 floor == #3 earliest_departure == #5 anchor == `available_from_from_shift_start(ss,now)` (jedna asercja równości).
- **MUTATION ×2 (C13, in-memory, `pytest.raises`):** (i) max→min w źródle → asercja `af==shift_start` PADA pod mutantem (baseline: prawdziwa fn zdaje); (ii) usunięcie floora #5 (=flaga OFF) → asercja `pickup≥af` PADA (baseline ON: floor działa). Oba dowodzą, że strażniki łapią regres.
- **Guard nie-ślepy**: kurier on-shift bez GPS (poza dispatchable_fleet) → resolver dociąga shift_start (nie „unknown").

**Replay ON↔OFF (ETAP 5, okno 14 dni, `ledger_io.iter_shadow_decisions`, `scratchpad/l4_replay.py`):**
- total decyzji: **3538**; best `pre_shift`=216, `no_gps`=409 → **625 dotkniętych**.
- 216 pre_shift: **WSZYSTKIE już clamped** (`v324a/pre_shift_clamp_applied` — bo `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP` ON w flags.json).
- 409 no_gps: brak shift_start w rekordzie (on-shift → available_from=now → floor=no-op).
- (a) LEAK eta_pickup<shift_start: **0** · (b) ON podniósłby eta: **0** · (c) zmiana zwycięzcy (ext>60 flip): **0** · (d) pula niezmieniona: 216/216 (assert — floor zmienia CZAS, nie członkostwo).

⭐ **WERDYKT REPLAYU (uczciwie):** na żywych `shadow_decisions` L4 dla #1/#3 = **KONSOLIDACJA (jedno źródło), NIE zmiana zachowania** — istniejący `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP` już clampuje pre_shift, a no_gps on-shift z definicji nie wymaga floora (available_from=now). Zero regresji (a=b=c=0, pula stała). **Realny NOWY zysk L4 leży POZA shadow_decisions:**
1. **#5 plan_recheck leak** — regeneracja co 5 min bez floora shift_start (najszersza dziura audytu); manifestuje się na pre-shiftowcu ze ŚWIEŻYM GPS (anchor=now) → mierzy `pickup_floor_guard` (surface plan/recheck_leak), nie shadow. Test #5 dowodzi mechanizmu.
2. **F1 „nigdy nie wraca"** — jedno źródło zamiast N re-derywacji: przyszły term/refaktor nie rozjedzie floora między powierzchniami.
3. **Domknięcia:** no_gps floor zdefiniowany, GPS-przed-zmianą pokryty (pos-agnostic), `effective_pickup_at` surface dla L3.
4. **Strażnik nie-ślepy** — mierzy realne naruszenia (dziś 4 unknown = stale plany, nie ślepota).

**Regresja pełna (ETAP 4):** copy-overlay z kodem L4 (flaga OFF) → **`3806 passed / 0 failed / 23 skipped / 11 xfailed`** = baseline 3781 + 25 nowych L4, **ZERO nowych FAILi, ZERO nowych xfail** (11 xfail = te same pre-existing + 5 slotów invariant_slots_l04, nietknięte — brak XPASS). `flag_effect_coverage` zielony (flaga ma test efektu). `serializer_completeness_l11` zielony (klucze af_* auto-serializują, nie w `_METRICS_EXCLUDE`).
> Uwaga metodyczna: pierwszy bieg overlay dał 1 FAIL `test_no_new_untested_decision_flag` — ARTEFAKT overlay (checker hardcode'uje `TESTS=/…/scripts/dispatch_v2/tests` = kanon, gdzie mojego pliku testu jeszcze nie ma). Dowód że post-merge na kanonie zdaje: wymuszenie `TESTS`=overlay-tests → `new_gap: []`, flaga `tested`. Rebuild-overlay sed'uje ten path — finalny bieg czysty.

---

## 5. DEPLOY ZA ACK (koordynator; NIE wykonane)

**Kolejność (ETAP 6, off-peak >14:00, ACK Adriana):**
1. Merge `fix/l4-available-from` → master (jawne ścieżki; po merge re-regresja z KANONU + po `git worktree remove`).
2. `.bak` dotkniętych plików → `py_compile` + import check → testy z kanonicznej ścieżki.
3. **Wpis flagi do `flags.json`:** `"ENABLE_AVAILABLE_FROM_SINGLE_SOURCE": false` (start OFF; hot-reload). To krok deploy — NIE zrobiony (worktree nie tyka flags.json).
4. **Restart 2 serwisów** (kod nowy; flaga wciąż OFF = bajt-w-bajt, bezpieczne pod obserwację): `dispatch-shadow` (feasibility #3 + candidate #1 + chokepoint via state_machine) + `dispatch-plan-recheck` (#5 anchor floor). ⚠ NIGDY telegram/peak bez OK.
   - Uwaga cross-proces: `#5` biega pod `dispatch-plan-recheck`, `#1/#3` pod `dispatch-shadow`, chokepoint (`state_machine`) pod `dispatch-panel-watcher` (COURIER_ASSIGNED z panel-diff) — przy flipie flaga=true dotyczy WSZYSTKICH (decision_flag z flags.json, spójne cross-proces). Rozważyć restart panel-watcher dla effective_pickup_at.
5. **FLIP** (osobny ACK, po ≥2 dniach obserwacji OFF-shadow): `flags.json` → `true` (hot-reload; restart nie wymagany dla decision_flag, ale spójność fingerprint = restart 2-3 serwisów).
6. **Obserwacja po flipie:** `grep -c '"af_clamp_applied"' shadow_decisions.jsonl` (świeże okno) > 0; `grep 'L4_ANCHOR_FLOOR' logs/*plan-recheck*` > 0; `pickup_floor_guard.jsonl` `viol_recheck_leak` → spadek na dniach z pre-shiftowcami; guard `shift_start_unknown_plans` (spadek gdy stale plany GC'owane osobno).
7. **Rollback:** flaga `false` (hot-reload, ~5s, bez restartu) / `.bak` / `git revert`. Flaga OFF = natychmiastowy powrót do starych ścieżek.

**⚠ Bramka wartości:** replay pokazał 0 metryki-docelowej ON↔OFF na obecnych danych (konsolidacja). Flip uzasadniony **wartością F1 (prewencja nawrotu) + #5 leak floor**, NIE poprawą metryki shadow. Decyzja flipu = koordynator/Adrian z tą świadomością (ETAP 5: dowód „bez regresji" ✅ mocny; dowód „pozytywny wpływ metryki" = strukturalny/leak-#5, nie shadow-liczbowy).

---

## 6. NASTĘPNE FALE (świadomie OUT — zostawione)

- **L3 pas renderów** (#16-18 konsola, #9-13 apka, restauracja adapter, #20-22 tracking, #7 telegram): floor do shift_start na renderach LUB poleganie na sclampowanym planie/`effective_pickup_at`. Po L3 → zdjąć xfail INV-FEAS-PICKUP-FLOOR (dziś slot NIE istnieje — dobudować RED-on-start w L3).
- **L5 / Q2 feasibility „nie zdąży→nie dostaje"**: pre_shift nie wygrywa zlecenia, którego odbioru nie zdąży (buduje na F4 load-aware ETA; dotyka HARD — osobny sprint+ACK). Równość Q1b ZOSTAJE (`ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY` NIETKNIĘTE).
- **⑦ stale-plan GC**: 4 aktywne plany dla off-shift kurierów (04-22…06-29) — plan-lifecycle/prune (INV-LIFE-RECANON-PRUNE), NIE L4. To źródło residual `unknown_plans`.
- **F3 sanityzacja (0,0)/bug#2**, **F7 hardening defaultów starych flag** (`PRE_SHIFT_DEPARTURE_CLAMP` etc. env-default OFF vs flags.json ON — reset flags.json wywraca politykę).
- **#5 pełna przebudowa** (regeneracja przez bramki feasibility/geometria + pure-read load_plan) — dziś TYLKO floor.

---
**Artefakty scratchpad:** `l4_rebuild_overlay.sh` (overlay), `l4_replay.py` (ETAP 5), `baseline_canon.txt`/`baseline_copyov.txt` (walidacja metody), `l4_regression_final.txt` (regresja).
