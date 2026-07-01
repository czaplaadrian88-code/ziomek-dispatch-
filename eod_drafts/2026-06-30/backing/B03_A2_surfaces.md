# B03 — KLASA A2: TA SAMA DECYZJA × N POWIERZCHNI (lane B, sesja tmux 2, READ-ONLY)

**Agent:** B03-A2-surfaces · **Data:** 2026-06-30 ~14:10 UTC · **HEAD recon:** `8024705` · **Tryb:** read-only (zero edycji/restartów/flipów).
**Co to jest:** dla KAŻDEJ decyzji (czas-odbioru · committed-pickup · kolejność-trasy · carried-first · pula-kandydatów · ETA-dostawy) — WSZYSTKIE powierzchnie które ją LICZĄ lub RENDERUJĄ, wzdłuż 3 osi: **silnik↔konsola↔apka↔Telegram↔klient** · **nowe-zlecenie↔przerzut** · **feasibility↔greedy↔plan_recheck**. Plus mechanizm parytetu i status spójności.
**Wszystkie `plik:linia` ze ŚWIEŻEGO grepu dziś** (linie dryfują — ≥3 żywe sesje). Cross-repo: konsola `nadajesz_clone/panel/backend`, apka `scripts/courier_api`, klient `public_tracking`. STOP na dyspozytorni (NIE Mailek/Papu).

**Relacja do Fazy A (anty-double-count):** A6 zmapował GRAF BLIŹNIAKÓW (kopie reguł) i wyprowadził 5 distinct-rootów R1-R5. Ten OS patrzy z osi **POWIERZCHNI-DECYZJI** (nie kopii-reguły) i: (a) RE-POTWIERDZA route-order/carried-first/pool/floor świeżym grepem z lupą „która powierzchnia", (b) **DOKŁADA 2 A2-obrazy słabo pokryte przez A6** — **ETA-dostawy multi-powierzchnia** (A6 gr.7 dotyczył tylko `eta_pickup` display-vs-decyzja, NIE delivery-ETA cross-surface) i **committed-pickup: kontrakt R27 egzekwowany 4 RÓŻNYMI mechanizmami per powierzchnia**. Każda instancja ma `dedup_hint` zwijający do rootu A6 lub oznaczony NEW.

---

## 0. TL;DR — 8 faktów A2

1. **ŻADNA z 6 decyzji nie ma JEDNEGO źródła cross-surface.** Każda liczona/renderowana w 3-7 powierzchniach; parytet = flaga `TRUST_CANON`/`FROZEN`/`PIN` + monitor + golden-test, **nigdy wspólny import repo↔repo** (3 repa, 3 systemy flag — A5).
2. **ETA-DOSTAWY = 4 niezależne kopie obliczenia** (silnik `chain_eta`+`route_simulator.predicted_delivered_at` · konsola `_eta_chain` · apka `_compute_live_eta`/`_attach_fallback_eta` · klient `canon_eta`) + **warstwa-nakładka `live_eta_cache`** która sama ma **2 implementacje czytnika** (konsola czyta JSON `_load_live_eta`, apka czyta in-process `import live_eta_cache`). Cache = override-świeżościowy (TTL 8 min), NIE single-source → gdy wpis stale, KAŻDA powierzchnia spada na WŁASNE liczenie. To klasa „czasy się różnią/cofają" (incydent Adriana 19/22.06). **NEW (poza 5 rootami A6).**
3. **Committed-pickup R27 (frozen czas_kuriera) egzekwowany 4 RÓŻNYMI mechanizmami:** silnik = miękkie okno TSP `SetCumulVarSoftUpperBound` (`route_simulator`); apka = twardy floor `max(predicted, czas_kuriera, ready)` (`_committed_pickup_eta`); konsola = pin `PIN_AGREED_PICKUP_TIME` w `_build_route`; Telegram = priorytet źródła `czas_kuriera_warsaw` 4-poziomowy. **Brak wspólnego kontraktu** — każda powierzchnia „zamraża" inną arytmetyką. Dziś zgodne bo flagi ON wszędzie; strukturalnie FRAGILE. **NEW framing (R27 z A2-rule-registry miał 3 powierzchnie; tu 7 + ścieżka force-recheck).**
4. **KOLEJNOŚĆ-TRASY = 5 kopii / 2 repa** (re-potwierdzone): engine-choke `plan_recheck._apply_canon_order_invariants` ↔ render `route_podjazdy` ↔ konsola `fleet_state._build_route` ↔ apka `courier_orders.build_view` ↔ panelsync DEAD. Parytet repo↔repo = TYLKO `ziomek_time_route_monitor` (44-75/d). **= R2 A6.**
5. **APKA ma 6+ WŁASNYCH funkcji re-sekwencji trasy** aktywnych gdy `BUILD_VIEW_TRUST_CANON_ORDER` OFF (`optimize_route`/`_brute_optimize`/`_nn_optimize`/`_reorder_pickups_by_committed`/`_reorder_pickup_steps_by_committed`/`_prioritize_carried_dropoffs`/`_repair_dropoffs_after_pickups`) — to NIE „1 fallback" lecz pełny równoległy planer. **Rozszerza R2.**
6. **PULA-KANDYDATÓW: nowe-zlecenie i 2 z 3 ścieżek przerzutu dziedziczą JEDNĄ pulę** (`global_allocate`→prawdziwy `assess_order`), ALE `reassignment_forward_shadow` ma **WŁASNĄ fikcję pozycji** (`_SYNTH_POS` + `a_late=(a_cand is None)`) niezrównaną z silnikiem → 59% fałszywych „ratunków". **= R1 gr.3b A6** (oś pool zamiast bucket).
7. **CZAS-ODBIORU (floor pickup≥shift_start) = 17 powierzchni, 4 z floor** — re-potwierdzone z lupą A2; najszersza dziura = `plan_recheck` regen co 5 min odclampowuje. **= R4 A6** (floor-audit), tu cross-ref nie re-derywacja.
8. **Konsola `feed` scala 3 ASYNC kanały JSON** (surowy shadow + `global_alloc.json` resweep + `reassign_global_alloc.json` select) z TTL fail-soft → pula renderowana koordynatorowi = merge 3 źródeł, każde może „zniknąć cicho" (klasa M/O).

---

## 1. ⭐ MACIERZ GŁÓWNA — DECYZJA × POWIERZCHNIA (rdzeń OS)

Legenda komórki: **C**=liczy (compute, własna arytmetyka) · **R**=renderuje (czyta cudze + formatuje) · **R/override**=czyta cache jako nakładkę na własne C · **—**=nie dotyczy · **DEAD**=martwa kopia.

| Decyzja | SILNIK nowe-zlecenie | SILNIK feasibility/greedy | SILNIK plan_recheck (5min) | SILNIK przerzut (shadow/select) | KONSOLA (fleet_state/feed) | APKA (courier_orders) | TELEGRAM | KLIENT (public_tracking) | Parytet przez | Root |
|---|---|---|---|---|---|---|---|---|---|---|
| **czas-odbioru / eta_pickup** | **C** `dp.py:4057-4078` + clamp `:5862-5883` | **C** clamp `feas:789-819`+`rs:273` | **C** anchor `pr:534/554` (BRAK floor) | dziedziczy assess_order | **R/C** `_eta_chain:250` (CLAMP `:254`) | **C** `_committed_pickup_eta:641`+`_attach_fallback_eta:822` | **R** `:347/858/871` | — | flagi CLAMP/FROZEN + monitor | **R4** (floor) |
| **committed-pickup (R27 frozen)** | **C** źródło `pickup_ready_at=ck dp:3486`; pre-recheck `:260/356` | **C** okno TSP `rs:1071`+`tsp:263`+assert `:1423` | **C** committed anchor `pr:534` | dziedziczy | **R** pin `PIN_AGREED :509`+`committed_time.py` | **C** floor `_committed_pickup_eta:641`+frozen `:872`+`:1050` | **R** źródło 4-poziom `:503-548` | — | flagi FROZEN/PIN per repo (4 mechanizmy) | **R4+NEW** |
| **kolejność-trasy** | (kandydat) `rs._simulate_sequence`/`_ortools_plan`/`_greedy_plan` | **C** plan kandydata `rs:540/758` | **C ENGINE-CHOKE** `_apply_canon_order_invariants:1478` | select dziedziczy; shadow N/D | **C** `_build_route:395`+`_order_from_plan_seq:342` (TRUST_CANON `:443`) | **C** `build_view:1072`→`order_podjazdy` LUB 6 funkcji `:265/357/467/672` | **R** `_route_section:676` | — | ziomek_time_route_monitor + golden | **R2** |
| **carried-first** | — | w planie `rs` | **C** `_relax_carried_first:1003` (w canon-inv) | — | **C** `:432 carried`+TRUST_CANON relax | **C** `_prioritize_carried_dropoffs:467` (PLAN_ORDER_INV `:1158`) | (w route) | — | flagi TRUST_CANON + carried_first_guard | **R2** |
| **pula-kandydatów** | **C** `build_fleet_snapshot:755`→`dispatchable_fleet:1383`+rescue `:201` | **C** filtr `check_feasibility_v2`→feasible pool `dp:957`; always-propose `:595/633/2638` | (czyta plan) | **C** fwd-shadow WŁASNA fikcja `rfs:64/231`; select=`global_allocate` (real) | **R** `read_feed:294`+3 overlay `:31/55/239` | (server-driven) | **R** kandydaci + best_effort `:771` | — | assess_order single (select/resweep) / NIC (fwd-shadow) | **R1 gr.3b** |
| **ETA-dostawy / delivered** | **C** `predicted_delivered_at` (z `rs`) + `chain_eta:45` | **C** `_simulate_sequence` delivered_at | **C** regen + `live_eta_cache` write | dziedziczy | **C** `_eta_chain:250` + **R/override** `_load_live_eta:700` (TTL8) | **C** `_compute_live_eta:794`/`_attach_fallback_eta:822` + **R/override** import `:1245` | **R** drop `_drop_eta_hhmm_v2:1125` | **R** `canon_eta_map:37` (z courier_plans) | live_eta_cache (override, 2 czytniki) | **NEW** |

**Skróty plików:** `dp`=dispatch_pipeline.py · `feas`=feasibility_v2.py · `rs`=route_simulator_v2.py · `tsp`=tsp_solver.py · `pr`=plan_recheck.py · `rfs`=tools/reassignment_forward_shadow.py · konsola/apka/klient = cross-repo (pełne ścieżki w §2).

---

## 2. PER-DECYZJA — instancje (plik:linia świeży) + spójność + dedup

### A2-1 — CZAS-ODBIORU (eta_pickup: kiedy kurier dojeżdża po odbiór)
**Powierzchnie (8):**
- SILNIK compute (nowe): `dispatch_pipeline.py:4057` `eta_pickup_utc=arrive_pickup` / `:4061` drive_arrival / `:4077` now+travel; R-07 chain override `:4063-4067`; clamp pre_shift `:5877/5883`, no_gps `:5862`.
- SILNIK feasibility/greedy (clamp pre_shift): `feasibility_v2.py:789-819` `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP` → earliest_departure=shift_start; `route_simulator_v2.py:273-277` departure clamp.
- SILNIK plan_recheck (regen 5 min): `plan_recheck.py:534 _earliest_committed_pickup_anchor` / `:554 _start_anchor` — **BRAK shift_start floor** (leak K2).
- panel_client parser: `panel_client.py:578 _czas_kuriera_to_datetime` (closest-day anchor 30.06, fix `b97c35f`).
- KONSOLA render: `nadajesz_clone/panel/backend/app/integrations/ziomek/fleet_state.py:250 _eta_chain` (`CLAMP_PRESHIFT_PICKUP_ETA :254`, env ON 30.06).
- APKA render: `scripts/courier_api/courier_orders.py:641 _committed_pickup_eta`, `:822 _attach_fallback_eta` (`FROZEN_PICKUP_ETA :872`), `:1050 _attach_committed_to_pickups`, `PICKUP_TIME_READY_FALLBACK :522/1064`.
- TELEGRAM: `telegram_approver.py:347/871 eta_pickup_hhmm`; floor committed `:858`; R-LATE-PICKUP propozycja „Proponowany czas odbioru" `:~1393`.

**Spójność:** floor `pickup≥shift_start` policzony/pominięty niespójnie — 17 powierzchni, 4 z floor (pełna lista = A6 gr.6 / floor-audit). Najszersza dziura = `plan_recheck` regen odclampowuje co 5 min (#5 floor-audit). `available_from` jednego źródła NIE istnieje, runtime-inwariant NIE istnieje.
**dedup_hint:** R4 (one earliest-pickup floor) — cross-ref floor-audit + A6 gr.6, NIE re-derywacja.

### A2-2 — COMMITTED-PICKUP (R27: zamrożony czas_kuriera po przypisaniu)
**Powierzchnie (7) + ścieżka force-recheck:**
- SILNIK źródło: `dispatch_pipeline.py:3486 pickup_ready_at=czas_kuriera` (propagacja `:3149/3550/3557/3831/6423…`).
- SILNIK pre-proposal recheck (fetch świeży z panelu): `:260 _v327_safe_fetch_czas_kuriera` / `:356 get_fresh_czas_kuriera_for_bag` (ThreadPool fetch detali).
- SILNIK okno frozen (feasibility/greedy/TSP): `route_simulator_v2.py:1071 ENABLE_V3274_FROZEN_PICKUP_WINDOW`, `:44 set_committed_pickup_tolerance`, `tsp_solver.py:263 SetCumulVarSoftUpperBound`, post-solve assert `rs:1423/1445`.
- panel_client: `panel_client.py:578` closest-day (anty-wobble, force-recheck w obie strony).
- KONSOLA: `fleet_state.py:509 PIN_AGREED_PICKUP_TIME` (odbiór z czasem planu = nietykalny jak carried) + `committed_time.py:72 committed_leg_min`.
- APKA: `courier_orders.py:641 _committed_pickup_eta` (floor=`max(predicted, czas_kuriera, ready)`) + `FROZEN_PICKUP_ETA :872`.
- TELEGRAM: `telegram_approver.py:503-548` priorytet źródła `czas_kuriera_warsaw` (bag_context→decision→…).
- FORCE-RECHECK (ręczny): konsola `coordinator_time_recheck.py:30` (subprocess) → silnik `coordinator_time_recheck.enqueue` (kolejka `coordinator_time_recheck.json`, flock+TTL) → konsument `panel_watcher`.

**Spójność:** kontrakt „odbiór ±5 od czas_kuriera, committed nietykalny" egzekwowany **4 RÓŻNYMI arytmetykami** (miękkie okno TSP / twardy floor `max()` / pin / priorytet-źródła). Flagi `ENABLE_V3274_FROZEN_PICKUP_WINDOW`+`ENABLE_FROZEN_PICKUP_ETA`+`PANEL_FLAG_PIN_AGREED_PICKUP_TIME` ON wszędzie → dziś zgodne, ale brak wspólnego kontraktu = FRAGILE (zmiana jednej arytmetyki cicho rozjedzie). Dodatkowo 3 punkty PARSE/ANCHOR `czas_kuriera→datetime` (`_v327_safe_fetch` silnik vs `panel_client._czas_kuriera_to_datetime` closest-day vs force-recheck queue) — różne kotwice doby.
**dedup_hint:** R4 (anchor/floor rodzina) + NEW (frozen-contract-mechanism-varies — kandydat distinct sub-root „one committed-pickup contract").

### A2-3 — KOLEJNOŚĆ-TRASY (execution-order kuriera, NIE display — C8)
**Powierzchnie (5 kopii / 2 repa):**
- SILNIK ENGINE-CHOKE: `plan_recheck.py:1478 _apply_canon_order_invariants` (build `:780`, retime `:1582`, relax `:1526`).
- SILNIK kandydat-plan (feasibility/greedy): `route_simulator_v2.py:540 _simulate_sequence` / `:758 _plan_from_sequence` / `_ortools_plan` / `_greedy_plan`.
- SILNIK render kanonu (źródło apki): `route_podjazdy.py:190 order_podjazdy` + `:141 _canon_order_from_plan` (carried-first relax).
- KONSOLA: `fleet_state.py:395 _build_route` + `:342 _order_from_plan_seq` (`TRUST_CANON_ORDER :443`, `TRUST_CANON_WHEN_COVERS_BAG :375`).
- APKA: `courier_orders.py:1072 build_view` → `route_podjazdy.order_podjazdy` gdy `BUILD_VIEW_TRUST_CANON_ORDER :1120`; ELSE własny planer `:672 _plan_stop_sequence` + `:265 optimize_route`/`:294 _brute_optimize`/`:328 _nn_optimize` + `:357/428 _reorder_pickups…` + `:467 _prioritize_carried_dropoffs` + `:400 _repair_dropoffs_after_pickups`.
- APKA-DEAD: `scripts/courier_api_panelsync/courier_orders.py:558 build_view` (665 vs 1285 L, martwa).

**Spójność:** DIVERGED. Twin #11: konsola dostała carried-first-relax (22.06), apka force-carried-first → 44-75 rozjazdów/d. Parytet repo↔repo = TYLKO `ziomek_time_route_monitor.jsonl` (RUNTIME, brak wspólnego importu). Engine-choke ma golden-test (`test_precedence_hierarchy_snapshot`); cross-repo NIE. C5 near-miss: `BUILD_VIEW_TRUST_CANON_ORDER` bywała martwa bo `ENABLE_APP_ROUTE_FROM_CONSOLE=1` short-circuituje (`config.py:66`).
**dedup_hint:** R2 (one route-order module). Apka-6-funkcji = rozszerzenie R2. panelsync DEAD = K.

### A2-4 — CARRIED-FIRST (odbierz po drodze zanim dowieziesz niesione)
**Powierzchnie (4 + guard):**
- SILNIK: `plan_recheck.py:1003 _relax_carried_first` (wewnątrz `_apply_canon_order_invariants`) + `route_podjazdy.py:141 _canon_order_from_plan` (carried na początek, `:167`).
- KONSOLA: `fleet_state.py:432 carried=sorted(...picked_up)` + gałąź `:443 TRUST_CANON` (relax przez `_order_from_plan_seq`).
- APKA: `courier_orders.py:467 _prioritize_carried_dropoffs` (`PLAN_ORDER_INVARIANTS :1158`, TYLKO gdy NOT trust_canon).
- APKA-DEAD: panelsync.
- INSTR: `tools/carried_first_guard.py` (read-only strażnik, jsonl).

**Spójność:** podzbiór kolejności-trasy; ta sama dywergencja co A2-3 (relax w konsoli, force w apce). Flaga `ENABLE_CARRIED_FIRST_RELAX` env-frozen ON na plan-recheck+panel-watcher.
**dedup_hint:** R2 (subset). NIE liczyć osobno od A2-3.

### A2-5 — PULA-KANDYDATÓW (kto może dostać zlecenie)
**Powierzchnie:**
- SILNIK build (nowe): `courier_resolver.py:755 build_fleet_snapshot` → `:1383 dispatchable_fleet` (wzbogaca shift_end) + `:201 _rescue_from_last_pos` + `:1341 _post_shift_start_synthetic_eligible`.
- SILNIK feasibility filtr → pula: `check_feasibility_v2` → `dispatch_pipeline.py:957/1406 feasible.sort`; `pool_feasible_count`; always-propose `:595 _best_effort_fastest_pickup_key`/`:633 _best_effort_objm_pick`/`:2638 _always_propose_on`/`:2504 _demote_blind_empty`.
- SILNIK pending (odroczone): `pending_pool.py:107 upsert_order`; `pending_queue_provider.py:50 get_pending_queue` (**DEAD** — `ENABLE_PENDING_QUEUE_VIEW=False`).
- PRZERZUT (nowe↔przerzut oś):
  - `tools/reassignment_forward_shadow.py:141 _fleet_without_order` (A wyjęty) → woła PRAWDZIWY `assess_order`, ALE `:64 _SYNTH_POS={none,pin,pre_shift,""}` + `:231 _quality_gate` `a_late=(a_cand is None)` (`:260`) = **WŁASNA fikcja pozycji** niezrównana z `_selection_bucket`.
  - `tools/reassignment_global_select.py:184 select` → `:224 global_allocate` (= `pending_global_resweep.global_allocate:145`, prawdziwy `assess_order`, ZERO dryftu — dziedziczy pulę silnika).
- KONSOLA render: `feed.py:294 read_feed` + `:195 _proposal_from_decision` + overlay `:31 _load_global_alloc_fresh` (resweep) + `:55 _load_reassign_select_fresh` + `:239 _load_reassign_proposals` (`quality_reassign` BEZ filtra `_pos_trusted`).
- TELEGRAM: `telegram_approver format_proposal` kandydaci + best_effort banner `:771`.

**Spójność:** nowe-zlecenie + select + resweep = JEDNA pula (`assess_order`/`global_allocate`, dobre). ALE `reassignment_forward_shadow` = osobna fikcja pozycji → 59% fałszywych ratunków (`quality_reassign`); konsola `feed` renderuje je bez filtra pewnej pozycji (Telegram ma `_pos_trusted`, konsola nie). Konsola scala 3 ASYNC kanały JSON z TTL fail-soft (klasa M/O — overlay znika cicho gdy stale).
**dedup_hint:** R1 gr.3b (out-of-engine gates pozycji) — oś POOL zamiast bucket. fwd-shadow fiction + feed-bez-filtra = te same 2 z 8 bliźniaków pozycji.

### A2-6 — ETA-DOSTAWY (predicted_delivered_at: kiedy klient dostanie) ⭐ NEW
**Powierzchnie (4 kopie obliczenia + nakładka cache + 3. czytnik klienta):**
- SILNIK źródło: `dispatch_pipeline.py predicted_delivered_at` (z `route_simulator_v2._simulate_sequence` delivered_at) + `chain_eta.py:45 compute_chain_eta` (effective_eta dla propozycji, OSOBNA arytmetyka haversine×2.5 `:80`).
- SILNIK konsolidacja-writer: `shadow_dispatcher.py:1254 live_eta_cache.upsert(predicted_delivered_at, pickup_at, courier_id)` → `dispatch_state/live_order_eta.json` (+ writerzy `plan_recheck.py`, `global_alloc_store.py`).
- KONSOLA: `fleet_state.py:250 _eta_chain` (WŁASNY OSRM-chain) + **override** `:700 _load_live_eta` (czyta JSON, TTL `LIVE_ETA_MAX_AGE_MIN=8` `:94`, guard `LIVE_ETA_COURIER_GUARD :554`).
- APKA: `courier_orders.py:794 _compute_live_eta` + `:822 _attach_fallback_eta` (WŁASNY drive) + **override** `:1245 import dispatch_v2.live_eta_cache` → `:719 _plan_meta_for_order` (live_eta_map, guard `LIVE_ETA_COURIER_GUARD :743`).
- KLIENT: `public_tracking.py` → `canon_eta.py:37 canon_eta_map` (3. czytnik — z `courier_plans.json` delivery_eta, NIE z live_eta_cache).
- TELEGRAM: `telegram_approver.py:1125 _drop_eta_hhmm_v2`.

**Spójność:** `live_eta_cache` to ŚWIADOMA próba konsolidacji (docstring: „wszystkie 3 powierzchnie spójne"), ALE jest **nakładką-override świeżościową, nie single-source**: (a) base-compute = 4 kopie (silnik chain/rs + konsola `_eta_chain` + apka `_compute_live_eta` + klient `canon_eta`); (b) czytnik cache sam = **2 implementacje** (konsola JSON `_load_live_eta` vs apka in-process `import`); (c) gdy wpis >8 min / brak świeżej decyzji → KAŻDA powierzchnia spada na WŁASNE liczenie → rozjazd. Klient (`canon_eta`) w ogóle nie czyta cache → 4. niezależny czas. To architektoniczne źródło incydentów „czas dostawy się różni/cofa" (19/22.06).
**dedup_hint:** NEW — „one delivery-ETA source". Częściowo dotyka R5 (display≠decision eta_pickup gr.7), ale DISTINCT: tu multi-powierzchniowa TA SAMA wartość decyzyjna (delivery), nie display-vs-decision jednego pola.

---

## 3. OSIE PRZEKROJOWE (jak decyzje rozkładają się na 3 osie zlecenia)

### 3a. Oś silnik↔konsola↔apka↔Telegram↔klient
| Powierzchnia | importuje silnik? | własne C decyzji | Liczba decyzji liczonych lokalnie |
|---|---|---|---|
| SILNIK (dispatch_v2) | — (źródło) | wszystkie | 6/6 |
| KONSOLA (fleet_state/feed) | **NIE** (subprocess dla zapisu kanonu; render = kopia) | route-order, carried-first, eta_pickup, eta-dostawy | 4/6 C + 2 R |
| APKA (courier_orders) | częściowo (import `route_podjazdy`+`live_eta_cache`; reszta kopia) | route-order (6 funkcji), carried-first, committed-pickup, eta-dostawy | 4/6 C + 2 R |
| TELEGRAM (telegram_approver) | tak (in-engine) | — (render decision dict) | 0/6 C, 6/6 R |
| KLIENT (public_tracking) | częściowo (`canon_eta` z courier_plans) | eta-dostawy (3. czytnik) | 1/6 C |

### 3b. Oś nowe-zlecenie↔przerzut
- **Nowe:** `assess_order` (pełna ścieżka, 6/6 decyzji).
- **Przerzut select/resweep:** `global_allocate` → prawdziwy `assess_order` (DZIEDZICZY 6/6, zero dryftu). ✅
- **Przerzut fwd-shadow:** `reassignment_forward_shadow` → prawdziwy `assess_order` ALE z WŁASNĄ fikcją pozycji w quality-gate → rozjazd TYLKO na puli/pozycji (A2-5). ⚠

### 3c. Oś feasibility↔greedy↔plan_recheck (wewnątrz silnika, route+pickup)
- **feasibility_v2**: woła `route_simulator` → plan kandydata; ma pre_shift clamp (`:789`).
- **route_simulator**: DWA planery `_ortools_plan` + `_greedy_plan` (R27 frozen window w obu, `:1071`).
- **plan_recheck**: regen kanonu co 5 min (`_gen_one_bag_plan`/`_retime_one_bag_plan`) + `_apply_canon_order_invariants`; **anchor BEZ shift_start floor** (`:534/554`) = ścieżka B (regen) odclampowuje to, co ścieżka A (feasibility) sclampowała. **Twin-asymetria env:** plan-recheck MA `PLAN_SEQUENCE_LOCK`/`COMMITTED_PROPAGATION`/`LIVE_ETA_REFRESH`, panel-watcher recanon NIE (A3/A5 B.1) → ścieżka zdarzeniowa (write/pickup/override) vs tickowa mogą dać RÓŻNY kanon.

---

## 4. TABELA POKRYCIA (jawnie — co sprawdzone, co nie)

| Obszar | Status | Dowód |
|---|---|---|
| 6 decyzji × silnik | ✅ świeży grep | `dispatch_pipeline`/`feasibility_v2`/`route_simulator`/`plan_recheck`/`chain_eta`/`courier_resolver` |
| 6 decyzji × konsola | ✅ świeży grep | `fleet_state.py`, `feed.py`, `committed_time.py`, `coordinator_time_recheck.py`, `canon_eta.py`, `public_tracking.py` |
| 6 decyzji × apka | ✅ świeży grep | `courier_orders.py` (1285 L), `config.py` |
| 6 decyzji × Telegram | ✅ świeży grep | `telegram_approver.py` |
| przerzut (fwd-shadow / global_select / resweep) | ✅ świeży grep | `tools/reassignment_forward_shadow.py`, `tools/reassignment_global_select.py`, `tools/pending_global_resweep.py` |
| live_eta_cache czytniki cross-repo | ✅ potwierdzone | konsola `_load_live_eta` (JSON), apka `import live_eta_cache` (in-proc) |
| feasibility↔greedy↔plan_recheck floor-asymetria | ✅ potwierdzone (grep anchor bez floor) | `plan_recheck.py:534/554` |

**LUKI (jawne, nie cisza):**
1. **courier-app Kotlin** (`/root/courier-app`) — NIE czytany kodem. Floor-audit/A6: route-order render serwerowo (`buildSteps` iteruje `stopSequence`), ale Kotlin ma WŁASNY `pickupTogether`/`restaurantKey` (bundling-display) — czy re-liczy ETA/kolejność lokalnie = Faza B/J.
2. **Most paczki** (`parcel_lane_merge`/`parcel_assign`) — czy używa `_selection_bucket`/`order_podjazdy`/`live_eta_cache` czy własnej ścieżki (klucz `900M+id`) = niesprawdzone (parcel ma natywny tor) — Faza B.
3. **Wartości LICZBOWE rozjazdu** (ile % eta-dostawy różni się gdy cache stale; ile czas-odbioru rozjeżdża plan_recheck vs feasibility) — deklarowane z lektury, NIE policzone runtime = Faza C oracle (`ziomek_time_route_monitor` ma świeży 932KB do parsowania).
4. **Pełne ciała** `_build_route`/`build_view`/`_eta_chain` — czytane nagłówki+gałęzie kluczowe, NIE linia-po-linii każdego brancha = magnituda rozjazdu Faza B/C.
5. **`public_quote`/`public_orders`** (klient zamawianie) — zinwentaryzowane istnienie, nie prześwietlone pod kątem 5. kopii ETA — niska istotność (quote = subprocess assess_order wg A5 C.1).

**NIE-luki (świadomie poza zakresem):** Mailek/Papu (granica). Sentinele=klasa M (osobny agent). Floor 17-powierzchni = R4 floor-audit (cross-ref, nie re-derywacja). Kopie-reguły bucket/lex_qual = A6 gr.1/3 (nie powtarzam — ten OS to oś POWIERZCHNI).

---

## 5. SYNTEZA dla Faz D/E/F

- **Faza E (dedup):** 6 decyzji A2 zwijają się do: **R2** (route-order+carried-first, 5 kopii) · **R4** (czas-odbioru floor + committed-pickup anchor) · **R1 gr.3b** (pula: fwd-shadow fiction) · **NEW: ETA-dostawy** (one delivery-ETA source — kandydat 6. distinct-root obok 5 A6) · **NEW: committed-pickup-contract** (4 mechanizmy frozen — sub-root R4 lub osobny). NIE liczyć carried-first osobno od route-order.
- **Faza D (precedencja):** committed-pickup = 4 mechanizmy frozen → która arytmetyka wygrywa gdy się rozejdą (np. apka floor `max()` vs konsola pin vs silnik miękkie okno)? eta-dostawy = cache-override vs własne-compute precedencja gated 4 flagami (`LIVE_ETA_FRESH_OVERRIDE_ONLY`/`LIVE_ETA_COURIER_GUARD`/TTL × 2 repa).
- **Faza F (PoC „one X"):** A6 wskazał 2 PoC (route-order, selection-key). Ten OS DOKŁADA 3.: **„one delivery-ETA source"** — przepiąć wszystkie 4 base-compute na `live_eta_cache` jako PRAWDZIWY single-source (nie override), z 1 czytnikiem (nie 2) + klient `canon_eta`. I 4.: **„one committed-pickup contract"** — wspólny helper floor zamiast 4 arytmetyk. Każdy PoC MUSI przepiąć WSZYSTKIE powierzchnie (3 repa) inaczej kopia wraca (C7).
- **Faza C (oracle):** `ziomek_time_route_monitor` (route-order parytet konsola↔apka, 932KB świeży) + brak monitora dla eta-dostawy-stale-fallback (luka instrumentu — gdy cache stale, nikt nie mierzy rozjazdu 4 kopii). Rekomendacja-DRAFT: monitor delivery-ETA cross-surface analogiczny do time-route.
