# RAW 01b — raport agenta f1-rdzen (rdzeń decyzyjny)

> Surowy raport subagenta read-only (Faza 1). Zweryfikowany na HEAD `fcf1342`. Synteza → `../01-stan-obecny.md`.

# RDZEŃ DECYZYJNY ZIOMKA — rekonesans read-only (HEAD `fcf1342`, master)

Zakres: gdzie fizycznie zapada decyzja, determinizm, źródła stanu, ETA, I/O w rdzeniu, weryfikacja kanonu. Linie z HEAD; repo mutuje (auto-push co h) → grepuj symbol jeśli dryf. FAKT = `plik:linia`; HIPOTEZA oznaczona jawnie.

---

## 1. RDZEŃ DECYZYJNY — mapa wewnętrzna

**Wejście = `dispatch_pipeline.assess_order`** (`dispatch_pipeline.py:3565`, **63 l.**, 3565–3627) — cienki wrapper: woła `_assess_order_impl`, potem czyta stan degradacji OSRM (`:3589-3592`) i loguje kandydatów do observability (`:3596-3623`). Sam nie decyduje.

**Serce = `_assess_order_impl`** (`:3629`, **~3785 l.**, 3629–7414) — MONOLIT (ostatnia funkcja pliku, brak defów po niej). Struktura:

| Etap | plik:linia | Co robi |
|---|---|---|
| default `now` | `:3644-3647` | `now=datetime.now(utc)` gdy nie podano |
| load-governor | `:3674-3718` | `_loadgov_compute` + (za flagą) **plik hysteresis + Telegram alert** |
| geokod defense (HARD) | `:3728-3746` | pickup_coords None/(0,0) → `SKIP no_pickup_geocode` |
| early-bird (HARD) | `:3783-3827` | ≥próg min naprzód → `KOORD`; opcjonalny **rekurencyjny** kontrfaktyk `_assess_order_impl(_bypass_early_bird=True)` (`:3796`) |
| pętla per-kurier | `:3869` / `:3884` | `_v327_eval_courier` (wrapper TLS OSRM) → `_v327_eval_courier_inner` (**~2145 l.**, 3884–~6028) |
| pula równoległa | `:6060-6077` | `ThreadPoolExecutor.map(_v328_eval_safe, list(fleet_snapshot.items()))` |
| selekcja | `:6287-6320` | filtr feasible + sort po score + adiustacje SOFT + demote + assert |
| tiering/lex/best-effort | `:6337-7188` | late-pickup tiery, objm_lexr6, best-effort |

**Wewnątrz `_v327_eval_courier_inner`** (ocena jednego kandydata):
- pozycja kuriera **ze snapshotu**: `courier_pos = _sanitize_courier_pos(cs.pos)` (`:3885`) — NIE żywy odczyt;
- soon-free probe `:3899`; pre-proposal recheck (**żywy fetch panelu**) `:3913`;
- bundling L1/L2 `:3929-3966` (haversine);
- **chain_eta (OSRM)** przed feasibility `:4066-4106` (`_drive_min_fn`→OSRM `:4077`);
- **`check_feasibility_v2(...)` (HARD)** `:4108-4120` — zwraca `(verdict, reason, metrics, plan)`; **plan/route-sim liczony WEWNĄTRZ feasibility**; dostaje `available_from=cs.available_from` (`:4120`, L4);
- **`scoring.score_candidate(...)` (SOFT)** `:4303-4311`;
- **drive_min (OSRM)** `:4320-4333` (do wait-penalty + display);
- eta_load_aware `:4377-4391`;
- `bonus_penalty_sum = sum(bonus_penalty_terms.values())` `:5451`; `final_score = score_result["total"] + ... + bonus_penalty_sum + ...` `:5513`.

**Feasibility** — `feasibility_v2.check_feasibility_v2` (`feasibility_v2.py:430`, **~966 l.**, 430→koniec pliku). Przyjmuje CAŁY stan argumentami (`courier_pos, bag, new_order, now, shift_*, pickup_ready_at, available_from, courier_tier, pos_source…`). Woła `simulate_bag_route_v2` (`:831` ścieżka główna; `:973` foodage-shadow).

**Route sim / TSP** — `route_simulator_v2.simulate_bag_route_v2` (`route_simulator_v2.py:251`, **~784 l.** do `_greedy_plan`). Solvery: `_greedy_plan` (`:1035`), `_ortools_plan` (`:1160`, OR-Tools). Macierz przez **OSRM `table` `:405`** + **OSRM `route` `:638`**. Jedyny import ortools = `tsp_solver.py`.

**Scoring** — `scoring.score_candidate` (`scoring.py:189`, **~99 l.**). ~19 żywych kar/bonusów w `bonus_penalty_terms` (m.in. r1_soft_pen, r5_soft_pen, r5_pickup_detour, r6_soft_pen, r8_soft_pen, r9_wait_pen, r9_stopover, r1_corridor, deliv_coloc, sync_spread, r_paczki_flex, fifo_violation, inter_wave_deadhead, coordinator_idle; reszta `*_shadow_delta` = telemetria).

**Selekcja** — `_selection_bucket` (`:2514`), `_best_effort_sort_key` (`:610`), `_best_effort_fastest_pickup_key` (`:641`), `_best_effort_objm_pick` (`:679`), `_demote_blind_empty` (`:2656`), `_assert_feasibility_first` (`:2543`, wołane **1×** `:6320`). Bliźniak kanoniczny lex = `objm_lexr6.py` (`lex_qual:29`, `bucket:83`, `pick:109` — pure).

**plan_recheck (poza tickiem, timer 5 min)** — `plan_recheck.py`: `_sweep` (`:771`) woła **`simulate_bag_route_v2` (`:776`, `:1970`)** + `_apply_canon_order_invariants` (`:1739`). **KLUCZOWE:** komentarz `:1019-1020` — „Regeneracja per-tick woła `simulate_bag_route_v2` … **NIE `check_feasibility_v2`** → nowa sekwencja może być GORSZA R6". Czyli plan_recheck współdzieli route-sim, ale **omija warstwę HARD**.

---

## 2. DETERMINIZM I POWTARZALNOŚĆ

**Zegar (`now`) — deterministyczny GDY podany, inaczej `datetime.now()`:** `dispatch_pipeline.py:3645`, `feasibility_v2.py:448`, `route_simulator_v2.py:278`, `plan_recheck.py:213/2127/2177`. Silnik shadow przekazuje `now` w dół (feasibility/route-sim biorą z argumentu) → pojedynczy tick spójny; default łapie tylko przy braku propagacji.

**OSRM na żywo (sieć + stan globalny modułu):** route-sim `table` `:405`, `route` `:638`; pipeline `route` `:4077` (chain_eta), `:4321` (drive_min); plan_recheck `table` `:958/1300/1480/1605`. `osrm_client.py`: HTTP `urllib`→`localhost:5001` (`:43`); **stan globalny procesu** — `_route_cache` (`:48`, TTL, `time.time()` `:456`), circuit-breaker `_osrm_circuit_open_until` (`:134/143`, `time.time()`). ⇒ wynik zależy od sieci/ciepłoty cache/circuitu — **nie kontrolowany dla replayu**.

**Flagi hot-reload — czytane z DYSKU w trakcie decyzji:** `common.load_flags()` robi `FLAGS_PATH.stat()` co wywołanie (lub co TTL 0.25 s przy `ENABLE_PERF_LAZY_MEMBERS`) — `common.py:54-77`. Komentarz `:25-27`: „`flag()`/`decision_flag()` wołane ~700×/decyzję". ⇒ zmiana `flags.json` **w środku ticku** zmienia zachowanie między kandydatami — **decyzja nie jest snapshotowana wobec flag**.

**Żywy fetch panelu:** `get_fresh_czas_kuriera_for_bag` (`:402`, wołane `:3913`, flaga `ENABLE_V327_PRE_PROPOSAL_RECHECK`) — HTTP do gastro + emisja eventu `CZAS_KURIERA_UPDATED` w środku oceny.

**Odczyt GPS/stanu w trakcie oceny:** NIE dla pozycji kuriera (bierze `cs.pos` ze snapshotu `:3885`). TAK dla plików pomocniczych (cache mtime/global, zamortyzowane): reliability `:1034`, district-map `:878/894`, speed-data `:1628`, restaurant-meta `:2952`.

**Iteracja dict/set (tie-break):** `fleet_snapshot.items()` w `.map` (`:6064`) — dict insertion-ordered ⇒ deterministyczny dla danego snapshotu. Tie-break selekcji stabilny: `_orig_order = {id(c): i for i,c in enumerate(feasible)}` (`:6337`), `feasible.sort` stabilny (`:6288`). **`random` — brak** w ścieżce rdzenia.
- HIPOTEZA: niedeterminizm wszedłby, gdyby `fleet_snapshot` był budowany z nieuporządkowanego źródła (set) — do weryfikacji: jak `courier_resolver.dispatchable_fleet` konstruuje kolejność.

**Mechanizm „zamrożonego zegara"/replay — BRAK.** Grep całego repo (poza tests): brak `frozen_clock/freeze_time/_now_provider/set_now/clock_override`. `DISPATCH_UNDER_PYTEST` służy TYLKO wyciszeniu file-logów w testach (`common.py:692-698`). `v3273` = etykieta bonusu scoringu, NIE zegar. Determinizm replayu opiera się wyłącznie na przekazaniu `now` + (niekontrolowanych) OSRM/flag.

---

## 3. ŹRÓDŁA PRAWDY O STANIE

Wszystko pod `/root/.openclaw/workspace/dispatch_state/` (POZA gitem, ADR-005; katalog `dispatch_v2/dispatch_state/` w repo = tylko epaka). Log decyzji = `scripts/logs/shadow_decisions.jsonl` (INNA lokalizacja).

| Plik (żywy stan) | Główni WRITERZY | Główni READERZY |
|---|---|---|
| `orders_state.json` (+`.lock`,`.prev`) | `state_machine`, `dispatch_pipeline`, `panel_watcher` | `courier_resolver`, `plan_recheck`, konsola, apka |
| `courier_plans.json` (+`.lock`,`.prev`) | `plan_manager`, `plan_recheck` | `shadow_dispatcher`, `panel_watcher`, konsola, apka |
| `courier_last_pos.json` | `courier_resolver` | `courier_resolver`, `plan_recheck` |
| `pending_proposals.json` | `panel_watcher`, `postpone_sweeper`, `pending_proposals_store` | telegram(martwy), toole |
| `events.db` (32 MB) | `event_bus` / silnik | konsumenci eventów |
| `geocode_cache.json` | `geocoding` | silnik, `address_mismatch`, konsola |
| `live_order_eta.json` | `live_eta_cache`, `plan_recheck` | konsola, apka |
| `courier_ground_truth.json` / `gps_delivery_truth.jsonl` / `decision_outcomes.jsonl` | walidacja GPS / silnik | kalibracja, toole (prawda fizyczna) |
| `loadgov_alert_state.json` | `_loadgov_save_alert_state` | `_loadgov_load_alert_state` (**w assess**) |

**ZDUBLOWANE byty (ta sama informacja, różni writerzy):**
1. **Pozycja kuriera — najgorsza duplikacja:** `gps_positions.json` (`gps_server`) + `gps_positions_pwa.json` (dual-write z apki) + `courier_last_pos.json` (`courier_resolver`) + `courier_api.db` (apka, cross-repo) + `fleet_position_history.jsonl`. „Gdzie jest kurier" w ≥4 magazynach, różni writerzy.
2. **Plan/kolejność:** `courier_plans.json` (silnik/`plan_manager`) vs `courier_api.db` (apka) — sekwencja worka w 2 repach; to samo K1 co route-order (5 kopii).
3. **Log decyzji rozdwojony fizycznie:** `shadow_decisions.jsonl` w `scripts/logs/`, a `r6_breach_shadow`/`c2_shadow_log`/`obj_replay` w `dispatch_state/` (ADR-005).

---

## 4. ETA — jak liczona i co wchodzi do HARD

**Silnik ETA:** `osrm_client.route/table` (localhost:5001, urllib) — źródło prawdy o czasach przejazdu. Fallback `haversine` (pure) × `HAVERSINE_ROAD_FACTOR_BIALYSTOK` + `get_fallback_speed_kmh(now)` (bucket-speed, `:3844`) gdy OSRM rzuci wyjątek (`:4326-4333`). Traffic: `osrm_client._apply_traffic_multiplier(result, now)` (`osrm_client.py:293`, bucket godzinowy).

**Do FEASIBILITY (HARD) wchodzą:**
- czasy przejazdu z **OSRM `table`** (przez `simulate_bag_route_v2` WEWNĄTRZ `check_feasibility_v2`) → egzekwują R6 (35/40) i SLA;
- `r07_chain_eta_utc` (OSRM chain, `:4104`) → R-01 MANDATORY gdy flaga;
- `pickup_ready_at` (`:3829`, m.in. `calib_maps.prep_bias_for` `:2074`), `available_from` (L4, `:4120`).

**Tylko PREZENTACJA / SOFT (po feasibility):**
- `drive_min` OSRM (`:4320`) → wait-penalty (scoring) + display ETA;
- `eta_load_aware.pickup_buffer_min` (`:4379`, flaga `ENABLE_ETA_LOAD_AWARE` OFF) → OBIETNICA odbioru; komentarz `:4373` wprost: **„NIE dotyka feasibility_v2 (HARD)"**;
- `calib_maps.eta_quantile_calibrate` (`:5597/6241/6255`) → tylko metryka `travel_min_cal` (telemetria);
- drive-speed tier / `get_fallback_speed_kmh` — fallback estymaty.

---

## 5. GRANICE RDZENIA — I/O WEWNĄTRZ ŚCIEŻKI DECYZYJNEJ (kluczowy produkt)

**Dziś rdzenia NIE da się wywołać czysto (stan+zlecenie→decyzja bez I/O).** Konkretne wywołania I/O w `assess_order`/feasibility/scoring/selekcji:

1. **Flagi z dysku (~700×/decyzję):** `common.load_flags()` → `stat()`+`open(flags.json)` (`common.py:66,69`). Bezpośrednio: `feasibility_v2.py:102,107,471`; `route_simulator_v2.py:1573`; wszędzie przez `C.flag/decision_flag`.
2. **OSRM sieć (urllib→:5001):** `route_simulator_v2.py:405` (`table`), `:638` (`route`); `dispatch_pipeline.py:4077` (chain_eta), `:4321` (drive_min).
3. **Żywy HTTP fetch panelu + emisja eventu:** `dispatch_pipeline.py:3913`→`:402` (`ENABLE_V327_PRE_PROPOSAL_RECHECK`).
4. **Zapisy shadow-log (`open(...,'a')`) w środku decyzji:** feasibility `_emit_r6_breach_shadow` `:368`, `_emit_c2_shadow_diff_event` `:409`; pipeline `_append_difficult_case_log` `:223`, `_append_split_layer_guard_log` `:245` (wołane `:2596,2642`), `_append_earlybird_t30_shadow` `:2776` (wołane `:3801`), feas-carry-blind `:1249`.
5. **Load-governor: plik + Telegram w assess:** `_loadgov_load_alert_state` `:2226` + `_loadgov_save_alert_state` `:2250` wołane `:3694/:3702`; `send_admin_alert` (sieć Telegram) `:3706` — za `ENABLE_FLEET_LOAD_GOVERNOR`.
6. **Odczyty plików cache (mtime/global, zamortyzowane, ale nadal syscall/open):** reliability `:1034`, district-map `:878,894`, speed-data `:1628`, restaurant-meta `:2952`.
7. **Wrapper (w `assess_order`, poza rdzeniem decyzji):** odczyt stanu OSRM `:3589-3592` + observability logger `:3596-3623`.

**Co JUŻ jest czyste (najłatwiejsze do odcięcia):**
- **`scoring.score_candidate` — w pełni czysta** (`scoring.py:189`): zero I/O, zero `now()`, zero sieci; tylko matematyka + stałe modułu. Cała warstwa 6.
- **Pozycja kuriera** — wchodzi argumentem (`cs.pos`, `:3885`); brak żywego odczytu GPS w ocenie.
- **`check_feasibility_v2`** — całość stanu przez argumenty (`now, bag, pos, shift_*, available_from, tier, pos_source`), ALE wewnątrz NIECZYSTA: czyta flagi (`:471`), woła OSRM przez route-sim (`:831`→`table`), pisze shadow-logi (`:368/409`). Odcięcie = wstrzyknąć macierz OSRM + snapshot flag + wypiąć shadow-writy.
- **`objm_lexr6.py`** i etap selekcji (`_selection_bucket`, sorty, `_orig_order`) — czysta kalkulacja nad listą kandydatów + odczyty flag.

Wzorzec do odcięcia: OSRM (macierz), flagi (snapshot) i `now` = trzy „wstrzykiwalne" wejścia; shadow-logi/Telegram/loadgov = efekty uboczne do wyniesienia do powłoki (filar F-2).

---

## 6. WERYFIKACJA TEZ `ZIOMEK_ARCHITECTURE.md` vs KOD

1. **„10 warstw, HARD przed SOFT" — POTWIERDZONE logicznie, fizycznie przeplecione.** feasibility (HARD `:4108`) liczone przed scoring (SOFT `:4303`) w obrębie każdego kuriera; werdykt po puli. ALE nie ma 10 sekwencyjnych bloków — warstwy 5-8 zaszyte w pętli per-kurier `_v327_eval_courier_inner` + selekcji. Zgodne z ADR-001 na poziomie kandydata.
2. **„`_assert_feasibility_first` egzekwuje HARD-przed-SOFT" — CZĘŚCIOWO (zgodnie z INV).** 1 call-site `:6320`; INV-LAYER-HARD-BEFORE-SOFT słusznie 🔴 (brak re-assertu na EMIT po mutacjach).
3. **„Route-order w 5 kopiach, plan_recheck przez ten sam rdzeń" — CZĘŚCIOWO.** plan_recheck współdzieli `simulate_bag_route_v2` (`:776/1970`), ale **NIE `check_feasibility_v2`** (komentarz `:1019-1020`) — filar F-2 („plan_recheck przez TEN sam rdzeń, nie cofa") jeszcze NIE zrealizowany; `_apply_canon_order_invariants` (`:1739`) = jedna z 5 kopii route-order.
4. **Kontrakt ⑤/F-2 „czysty rdzeń bez I/O" = OTWARTY** — sekcja 5 dowodzi żywego OSRM, dyskowych flag i side-effectów w rdzeniu. Zgodne z dashboardem entropii.

---

## TOP-5 WNIOSKÓW DLA ODCINANIA RDZENIA OD I/O

1. **Trzy wejścia do wstrzyknięcia dają ~90% czystości:** (a) macierz/route OSRM, (b) snapshot flag złapany RAZ na tick, (c) `now`. Feasibility i route-sim już dostają resztę stanu argumentami — brakuje tylko odcięcia OSRM (`:405/638/4077/4321`) i flag (`load_flags`).
2. **Flagi to ukryty, największy kanał niedeterminizmu** — ~700 `load_flags()`/decyzję z żywym `flags.json` (`common.py:54-77`). Snapshot flag na starcie ticku (jeden dict w dół) usuwa hot-reload w środku decyzji I zamyka lukę replayowalności — tańsze niż ruszanie OSRM.
3. **Efekty uboczne są już modularne — wystarczy je wypiąć, nie przepisywać:** wszystkie shadow-write/loadgov/Telegram idą przez nazwane helpery (`_emit_*`, `_append_*`, `_loadgov_*`, `send_admin_alert` `:368/409/223/245/2776/3706`) = gotowe punkty cięcia dla „powłoki efektów" (F-2).
4. **`scoring.score_candidate` = wzorzec docelowy (już czysty)** — dowód, że model „funkcja czysta + stan w argumentach" jest w tym repo osiągalny; `check_feasibility_v2` jest o krok (czyste argumenty, brudne wnętrze przez OSRM+flagi+logi).
5. **Uwaga: plan_recheck to DRUGI rdzeń, który omija HARD** (`simulate_bag_route_v2` bez `check_feasibility_v2`, `:1019`). Każde „odcięcie rdzenia" musi objąć OBIE ścieżki (tick + plan_recheck) razem — inaczej powstanie trzeci bliźniak. Pozycja i score już czyste; punkt zapalny = wspólny, wstrzykiwalny route-sim dla obu wywołań.

---
Uwaga metodyczna: rekonesans był READ-ONLY (Read/Grep/Glob/git log; zero zapisów, zero pytest, nie tknięto flags.json ani dispatch_state). Rozmiary funkcji liczone z zakresów defów. Jedyna otwarta HIPOTEZA (kolejność `fleet_snapshot` z `dispatchable_fleet`) oznaczona w sekcji 2.
