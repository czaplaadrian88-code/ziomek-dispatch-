# B02 — KLASA A1 (N-kopii-reguły) w pasmach STATE + SERIAL + CROSS

**Agent:** B02-A1-copies-STATE-CROSS · **Lane B** · **Tryb READ-ONLY** · **Data:** 2026-06-30 ~14:1x UTC · **HEAD `8024705`**
**Zakres modułów:** `state_machine`, `panel_watcher`, `panel_client`, `panel_html_parser`, `courier_resolver`, `bag_state`, `geocoding`, `osrm_client`, `shadow_dispatcher`, `sla_tracker`, `czasowka_scheduler`, `telegram_approver` + cross-repo `nadajesz_clone/panel` (`fleet_state`, `feed`), `scripts/courier_api` (`courier_orders`, panelsync), `courier-app` (Kotlin), `route_podjazdy`, `plan_recheck` (producent kanonu).
**Metoda:** świeży `grep -rn`/`sed` per grupa (linie zweryfikowane DZIŚ — DRYFUJĄ, re-grepuj). Każda instancja: plik:linia + źródło/objaw + łatane? + otwarte? + severity + dowód + dedup→root.
**Dedup (z A6, NIE re-derywuję):** grupy `lex_qual`/`bucket`/inline = TEN SAM root K1 selekcji (pasmo SELECTION, nie moje). Moje distinct-roots: **R2 route-order (CROSS), R4 floor (STATE/CROSS), R3 SLA-anchor (SERIAL/edge), R5 eta_pickup=display∧decision (SERIAL, F-class)** + wtórne A1 (parser V1/V2, haversine, pickup_ready, fleet-builder, serializer A+B).

---

## TL;DR — 6 twardych A1 mojego pasma (świeżo potwierdzone)

1. **Kolejność trasy/kanon = 5 powierzchni silnik/render + 4. kopia bundlingu (apka Kotlin) + DEAD panelsync — BRAK wspólnego importu cross-repo.** ➕ **DRUGI producent kanonu `panel_watcher._save_plan_on_assign:436` zapisuje `sequence` VERBATIM bez `_apply_canon_order_invariants`** (i wstrzykuje placeholder `(0,0)` coords). → root R2.
2. **Floor `pickup ≥ shift_start`: `available_from` = 0 trafień (single-source NIE istnieje), ZERO runtime-inwariantu, leak `plan_recheck._start_anchor:554` odclampowuje co 5 min, chokepoint `state_machine` COURIER_ASSIGNED:551 bez floor.** → root R4.
3. **`shift_start` LICZONY niezależnie per powierzchnia:** silnik `courier_resolver._shift_start_dt:1252` (datetime z grafiku) vs konsola `fleet_state` `sched.get("start")` HH:MM (:71) + `_hhmm_to_min` (:858) — OSOBNE źródło grafiku → cross-repo dryf. → root R4.
4. **SLA-anchor = 2 inline-lustra (`route_simulator._count_sla_violations:635` ↔ `feasibility_v2` SLA-loop:1135) + ASYMETRIA paczka-exempt (B ma, A NIE) + 3. kopia alertowa `sla_tracker._check_bag_time_alerts:267`.** Wszystkie kotwiczą na `pickup_at`, rozjazd z R6-thermal (`r6_thermal_anchor:663`=`pickup_ready_at`). → root R3.
5. **`eta_pickup` = JEDNO pole pełniące rolę display ∧ decision-value** (writers w pipeline 4057-5877, serializer derives `eta_pickup_hhmm`, ALE konsument `:5162` extension_penalty scoringu). → root R5 (F-class).
6. **Serializer LOCATION A (`_serialize_candidate`) + B (`_serialize_result.best`) — każda metryka w 2 miejscach albo znika z jednego** (`_propagate_prefixed_metrics` ratuje TYLKO prefiksowane; `sla_violations_*`/`would_hard_cap`/`c2_`/`d2_` = explicit-or-vanish). → A1 SERIAL, wzorzec #4/#16.

Wtórne (niższa severity): parser V1/V2 (2 impl., env-frozen per-proces), haversine ×6 (niespójny guard (0,0)), `pickup_ready_at` parse silnik vs telegram, fleet-builder `build_fleet_snapshot`↔`dispatchable_fleet` (dziś UNIFIED).

---

## ROOT R2 — KOLEJNOŚĆ TRASY / KANON (5 kopii + bundling ×4 + DEAD)  [CROSS/SERIAL, K1+K7]

**Reguła:** kolejność JAZDY = kanon Ziomka VERBATIM (carried-first-relax + no-return-to-departed-pickup + sequence). To NIE display — to realna kolejność (C8).

### Kopie (świeży grep, 3 repa)
| Rola | Plik:func:linia | Repo | Importuje kanon? |
|---|---|---|---|
| **ENGINE CHOKE** | `plan_recheck.py:1478` `_apply_canon_order_invariants` (+`_relax_carried_first:1003`, producent `_gen_one_bag_plan:612`, retime `_retime_one_bag_plan:1560`) | dispatch_v2 | — (źródło, „JEDYNY choke" wg testu) |
| **2. PRODUCENT (BEZ inwariantów)** | `panel_watcher.py:436` `_save_plan_on_assign` → `plan_manager.save_plan:510` | dispatch_v2 | ✗ — persystuje proposal `plan.sequence` VERBATIM, **NIE woła `_apply_canon_order_invariants`**; wstrzykuje placeholder `(0,0)` coords (l.~484/498) |
| render apka (engine) | `route_podjazdy.py:190` `order_podjazdy` / `:141` `_canon_order_from_plan` | dispatch_v2 | ✗ własna kopia |
| render KONSOLA | `fleet_state.py:395` `_build_route` / `:342` `_order_from_plan_seq` / `:250` `_eta_chain` | **panel (cross-repo)** | ✗ **0 importów `dispatch_v2`** (potwierdzone: jedyny hit `dispatch_v2` = docstring l.3) |
| render APKA-API | `courier_orders.py:1072` `build_view` → `route_podjazdy.order_podjazdy` (l.1116, gdy `APP_ROUTE_FROM_CONSOLE and mine`); else `_plan_stop_sequence:672`+`_prioritize_carried_dropoffs:467` | scripts/courier_api | ⚠ importuje `route_podjazdy` (l.38) TYLKO za flagą; inaczej własna kopia |
| render APKA Kotlin | `RouteLogic.kt:27` `buildSteps` (render `r.stopSequence` z serwera, ZERO re-sortu) + **własny bundling** `restaurantKey:23`/`pickupTogether:62` | courier-app | render kolejności serwerowej; **bundling „1 restauracja=1 wizyta" = 4. kopia** |
| render APKA-API DEAD | `courier_api_panelsync/courier_orders.py:558` `build_view` / `:366` `_plan_stop_sequence` / `:188` `optimize_route` | courier_api_panelsync | ✗ **MARTWA kopia** (665 vs 1285 L, worktree-fork) |

### Parytet / Stan
- Engine-choke: **GOLDEN-TEST** (`test_precedence_hierarchy_snapshot`/`test_route_podjazdy_trust_canon`).
- Konsola↔apka: **RUNTIME-MONITOR** `ziomek_time_route_monitor.jsonl` (10-min timer) — **JEDYNY mechanizm parytetu repo↔repo, brak wspólnego importu.**
- **DIVERGED** (twin #11, 44-75 rozjazdów/d wg protokołu; carried-first ostry objaw = 1 transient tick/d, self-heal — dług strukturalny). panelsync = DEAD (kandydat K).
- **2. producent (`_save_plan_on_assign`) = B-class:** zapisany plan czeka na re-inwarianty następnego ticku `plan_recheck` (5 min) → okno kanonu bez inwariantów + placeholder `(0,0)` coords (most do BUG#2/M sentinel).

**Severity:** P2 (DIVERGED, ale samo-zdrowieje/cosmetic-display w większości; 2. producent = okno 5-min). **Łatane:** częściowo (TRUST_CANON flagi ×3, `recanon-on-write`). **Otwarte:** TAK (brak wspólnego modułu kolejności cross-repo). **dedup→R2 „one route-order module".**

---

## ROOT R4 — FLOOR `pickup ≥ shift_start` (17 powierzchni, mój udział STATE/CROSS)  [K1+K2+K4]

**Reguła (BRAK kanonu):** „najwcześniej kurier odbierze" = `max(now, shift_start)`. NIE istnieje jedna definicja.

### Świeże potwierdzenia (dziś)
- `grep available_from --include=*.py` = **0 trafień** → **single-source `courier.available_from` NIE istnieje** (potwierdzone).
- `grep` runtime-guard „pickup ≥ shift_start"/assert = **0** → **ZERO inwariantu/strażnika** (potwierdzone).

### Powierzchnie MOJEGO pasma (z 17 audytu)
| # | Plik:linia (świeże) | Floor? | Uwaga |
|---|---|---|---|
| **#5 LEAK** | `plan_recheck.py:534` `_earliest_committed_pickup_anchor` + `:554` `_start_anchor` | **NIE** | anchor=committed/GPS/last_pos/last_event, **NIGDY shift_start** → regen `courier_plans.json` co 5 min ODCLAMPOWUJE. Najszersza dziura. |
| chokepoint | `state_machine.py:551` COURIER_ASSIGNED handler (`upsert_order:418`) | **NIE** | binding kurier↔zlecenie + zapis `pickup_at_warsaw` (czasówka authority :506/530) — committed może być < shift_start, **bez floor**. |
| (źródło danych) | `courier_resolver._shift_start_dt:1252` / `cs.shift_start` set `:1509/1556` | n/d | TU powinien żyć `available_from=max(now,shift_start)` (L0 audytu) — NIE istnieje. |
| #16 konsola | `fleet_state.py:755`+`:857` `CLAMP_PRESHIFT_PICKUP_ETA` (env ON 30.06) | **TAK (częściowy)** | floruje TYLKO ścieżkę OSRM, gdy `not on_shift and shift_start`. |
| #9 apka | `courier_orders.py:641` `_committed_pickup_eta` | **NIE** | committed/ready floor, nie shift. |
| #10 apka | `courier_orders.py:794` `_compute_live_eta` | **NIE** | self-compute now+drive. |
| #11 apka | `courier_orders.py:822` `_attach_fallback_eta` (FROZEN_PICKUP_ETA :872) | **NIE** | frozen committed omija OSRM/floor. |
| #14-15 panelsync | `courier_api_panelsync/courier_orders.py` | **NIE** | MARTWY bliźniak #10/#11. |

**Parytet:** **NIC** (każda powierzchnia re-liczy/pomija; testy UTRWALAJĄ „floor tylko committed/ready"). **Stan: DIVERGED by-construction.** Polityka pre_shift aktywnie produkuje takie przydziały (best=pre_shift 7,4%). HARD-reject dopiero `shift_start−30` → „10:59 przy 11:00" DOZWOLONE (case Drapieżnik 484400).
**Severity:** P1 (źródło, user-reported case; decyzje Adriana #8 zablokowane, roadmapa L0-L6 NIE wykonana). **Łatane:** tylko konsola fix `CLAMP_PRESHIFT_PICKUP_ETA` (1 z 4 floorów). **Otwarte:** TAK. **dedup→R4 „one earliest-pickup floor".**

### A1 powiązane: `shift_start` LICZONY niezależnie (STATE/CROSS, A1+B)
- Silnik: `courier_resolver._shift_start_dt:1252` / `_mins_to_shift_start:1235` (datetime z entry grafiku); feasibility inline tz-normalize `:749`.
- **Konsola: `fleet_state` buduje WŁASNY `shift_start` z `sched.get("start")` HH:MM (`:71`) + `_hhmm_to_min` (`:858`)** — OSOBNE źródło grafiku (konsola fetchuje własny schedule). → silnik (datetime/grafik) vs konsola (HH:MM/własny fetch) = cross-repo dryf definicji „start zmiany". **Severity P3** (oba czytają ten sam Sheet, ale osobnymi ścieżkami; rozjazd przy literówce/opóźnieniu fetcha). **dedup→R4.**

---

## ROOT R3 — SLA-ANCHOR (2 inline-lustra + asymetria paczka + 3. alert-kopia)  [SERIAL/L5-edge, K1+K3]

**Reguła:** naruszenie SLA = `predicted_delivered − pickup_anchor > sla_min`. Anchor: `pickup_at[oid]`→`picked_up_at`→`now`.

### Kopie (świeży grep + sed)
| Rola | Plik:func:linia | Kotwica | paczka-exempt? |
|---|---|---|---|
| kopia A | `route_simulator_v2.py:635` `_count_sla_violations` (pętla l.644-660) | inline `pickup_at→picked_up_at→now` | **NIE** (liczy paczkę jako violation) |
| kopia B | `feasibility_v2.py:1135` SLA-loop (l.1147-1185) | inline **IDENTYCZNA** logika (l.1156-1164) | **TAK** (`ENABLE_PACZKA_R6_THERMAL_EXEMPT` + `_is_paczka_sim` l.1153-1157) |
| konsument C | `plan_recheck.py:683` `_o2_key` (`p.sla_violations, dur`) | czyta PRECOMPUTED count (nie re-derywuje) | n/d |
| **3. kopia (alert)** | `sla_tracker.py:267` `_check_bag_time_alerts` → `bag_time_min = now − picked_up_at` (l.~309) | **TYLKO `now−picked_up_at`** (bez ready/pickup_at) | n/d (flaga `ENABLE_BAG_TIME_ALERTS` OFF) |
| (R6 odrębny anchor) | `route_simulator_v2.py:663` `r6_thermal_anchor` | **`pickup_ready_at`** (gotowość jedzenia) | INV-R6-ANCHOR |

### Parytet / Stan
- A↔B: **NIC** (ręczne lustro, brak wspólnej funkcji, brak golden-testu A≡B). **ASYMETRIA paczka-exempt POTWIERDZONA świeżym sed:** B (feasibility) pomija paczkę, A (route_simulator) liczy ją do `plan.sla_violations`.
- **Konkretny scenariusz rozjazdu:** paczka z `elapsed>35` → `_count_sla_violations` (A) inkrementuje `plan.sla_violations≥1` → feasibility wchodzi w `if plan.sla_violations > 0` (`:1135`) → pętla B POMIJA paczkę (exempt) → `violations_detail=[]`, `n_blocking=0`. Net: paczka odpala kosztowną ścieżkę SLA-detail ale kończy non-blocking; **`plan.sla_violations` count jest „kłamliwy" (liczy paczkę)** — karmi `_o2_key` (`plan_recheck`) i metrykę `sla_violations_count`.
- **DIVERGED vs R6:** SLA na `pickup_at` (TSP-projected) vs R6-thermal na `pickup_ready_at`. Dwie HARD-bramki tej samej decyzji, inny anchor (sprint O2, review 02.07).
**Severity:** P2 (asymetria paczka materialna na count/o2_key; ready-vs-pickup = znana luka O2). **Łatane:** NIE (otwarte do 02.07). **Otwarte:** TAK. **dedup→R3 „one SLA/R6 anchor".** ⚠ FAZA D: SLA-anchor ↔ R6-anchor = potencjalny KONFLIKT precedencji (I-class).

---

## ROOT R5 — `eta_pickup` = JEDNO pole, dwie role (display ∧ decision)  [SERIAL, F-class]

### Writers (silnik) + serializer (SERIAL)
| Plik:linia | Co pisze |
|---|---|
| `dispatch_pipeline.py:4057/4061/4077` | `eta_pickup_utc` = arrive_pickup / drive_arrival / now+travel |
| `dispatch_pipeline.py:4063-4067` | R-07 v2 CHAIN-ETA override (flaga) |
| `dispatch_pipeline.py:5287` | `metrics["eta_pickup_utc"]` = isoformat |
| `dispatch_pipeline.py:5862/5877` | clamp pre_shift/no_gps → shift_start |
| **SERIAL** `shadow_dispatcher.py:291/627` | `eta_pickup_hhmm` = `_eta_hhmm_warsaw(eta_pickup_utc)` (display DERIVED); `:537` reads utc |

### Konsument DECYZYJNY (czyni go decision-value, NIE display)
| Plik:linia | Użycie |
|---|---|
| `dispatch_pipeline.py:5162-5172` | `extension = eta_pickup_utc − pickup_ready_at` → **kara scoringu `extension_penalty`** (V3.24-A) |
| (cross-repo) `feed.py:189` | passthrough `eta_pickup_hhmm` (display); telegram `_candidate_line:347` `eta_pickup_hhmm` (display) |

**Parytet:** display `_hhmm` DERYWOWANY z decision `_utc` → display zawsze śledzi decyzję (OK). **RYZYKO odwrotne:** edycja „napisu" = zmiana decyzji (karmi scoring). Brak separacji pól (wzorzec #8).
**Stan: DRYF SEMANTYKI (F1)** — nie „rozjazd kopii", lecz „jedno pole = dwie role". **Severity P3** (latentne — dziś display wiernie śledzi; ryzyko na przyszłej edycji). **dedup→R5 „display≠decision" (klasa F, NIE liczyć jako kopię-reguły).**

---

## A1 SERIAL — Serializer LOCATION A + B (każda metryka ×2 albo znika)

- `shadow_dispatcher._serialize_candidate` (LOCATION A, alternatives) + `_serialize_result.best` (LOCATION B) + `_propagate_prefixed_metrics`.
- **Parytet TYLKO dla prefiksowanych** (`_AUTO_PROP_PREFIXES` ~38 prefiksów: `v325_/v326_/bonus_/objm_/late_pickup_/new_pickup_/...`). **Bez prefiksu** (`sla_violations_*`, `would_hard_cap`, `c2_`, `d2_`, `end_of_day_salvage_`, `eta_source`) = **explicit-or-vanish w OBU** (komentarz Z-09 l.202-204: late_pickup/new_pickup były explicit tylko w A, B ich nie miał — prefiks wyrównał).
- **Klasa A1** (reguła „metryka w logu" w 2 miejscach) + wzorzec #4/#16 (compute-not-serialized → cichy gate flip-walidacji). **Severity P3** (dziś mocno utrzymywane; ryzyko na nowej metryce). **Otwarte:** strukturalnie TAK (2 miejsca, brak jednego serializera). **dedup→A1-serializer (SERIAL).**

---

## WTÓRNE A1 (mój pasmo, niższa severity)

### W1 — HTML parser V1 vs V2 (2 implementacje, env-frozen per-proces)  [STATE, A1+B+D2+J]
- `panel_client.parse_panel_html:424` dispatcher: V1 (legacy regex, `:345` „DEPRECATED przy USE_V2_PARSER=1") vs V2 (`panel_html_parser.parse_panel_html_v2:47`), wybór po `USE_V2_PARSER:93` (`os.environ.get(...,"0")=="1"`, env-frozen).
- **`USE_V2_PARSER=1` TYLKO na panel-watcher** (drop-in). Callery `parse_panel_html`: `panel_watcher:2418` (V2), **`panel_client:792` (fetch detali — proces-zależny)**, `extract_restaurant_addresses:67` (PERI). → jeśli shadow/inny proces woła `panel_client` fetch → V1, panel-watcher → V2 = dwa parsery na ten sam panel.
- **Severity P3 PLAUSIBLE** (główny parse HTML tylko w panel-watcher=V2; ścieżka detali `:792` = latentna divergencja, nie potwierdziłem czy shadow ją wykonuje). **dedup→A1-parser (J/D2).**

### W2 — haversine ×6, niespójny guard sentinel (0,0)  [geo/CROSS, A1+M]
| Kopia | Guard None/(0,0)? |
|---|---|
| `osrm_client.haversine:399` | **TAK** (fail-loud None + (0,0), l.406-416) |
| `geometry.haversine_km:11` | **TAK** (ValueError None + (0,0), l.17-20) |
| `address_pin_memory.haversine_m:51` | ✗ inline; caller-side `_valid_point:61` filtruje (0,0) |
| **`courier_api/courier_orders._haversine:186`** (cross-repo) | **✗ BRAK guardu** (surowa matematyka; fallback macierzy dystansów `:297`) |
| `bootstrap_restaurants.haversine_m:53/61` / `geocode_verify.haversine_m:28` | PERI/INSTR |
- **Severity P3** (most do BUG#2/M; courier_api fallback na (0,0) = cichy zły dystans w apce, ale OSRM-first → rzadkie). **dedup→M sentinel (BUG#2), liczone jako A1-haversine tu tylko jako copy-count.**

### W3 — `pickup_ready_at` parse: silnik vs telegram  [STATE/render, A1]
- `dispatch_pipeline.get_pickup_ready_at:2930` (kanon) vs `telegram_approver._pickup_ready_warsaw:272` + `_parse_pickup_ready_prep_min:298` (własny parse render) + instrumenty (`sequential_replay._pickup_ready:401`, `ontime_lib._extract_pickup_ready:257`).
- **Severity P3** (telegram = display prep min; rozjazd parsowania → zły pokazany prep, nie decyzja). **dedup→A1-ready-anchor (cross-ref R3).**

### W4 — fleet-builder `build_fleet_snapshot` ↔ `dispatchable_fleet` (dziś UNIFIED)  [STATE, A1/B latent]
- `courier_resolver.build_fleet_snapshot:755` (raw, zostawia `shift_end=None` → Fail-CLOSED mina) vs `dispatchable_fleet:1383` (wzbogaca shift_end/shift_start z grafiku, woła snapshot `:1427`).
- **Wszyscy żywi konsumenci używają `dispatchable_fleet`:** `shadow_dispatcher:1118`, `czasowka_scheduler:340`, `replay_failed:136` (✓). **Historyczny bug zamknięty.** ⚠ **CLAUDE.md „replay_failed Track C deferred" = STALE — faktycznie naprawione** (świeży kod+komentarz l.132-136).
- **Severity P3 latent** (UNIFIED, ale brak strażnika by nowy konsument nie wziął raw snapshot — wzorzec #2 ryzyko). **dedup→A1-fleet-builder.**

### W5 — panel_watcher 4 handlery recanon (kompletność kanon-twin, NIE bug)
- `recanon_courier` z reason: `assign:619`, `deliver:663`, `return:691`, `pickup:724` + `redecide_courier` override:628/pickup:725. **Symetryczne** (P-5 cancel/return ZAMKNIĘTE `0426706`). Odnotowane jako kompletność, nie finding.

---

## TABELA POKRYCIA (jawne — nie cisza)

| Moduł / powierzchnia | Zbadane pod A1? | Wynik |
|---|---|---|
| `plan_recheck` (kanon producent + floor leak + _o2_key) | ✅ | R2 choke + R4 leak + R3 konsument |
| `panel_watcher` (2. producent kanonu + 4 recanon + parse caller) | ✅ | R2 2-producent (BEZ inwariantów) + W1 |
| `state_machine` (upsert chokepoint + COURIER_ASSIGNED) | ✅ | R4 chokepoint bez floor |
| `panel_client` + `panel_html_parser` (V1/V2) | ✅ | W1 parser 2-impl |
| `courier_resolver` (shift_start + fleet-builder + available_from) | ✅ | R4 source + W4 fleet + available_from=∅ |
| `shadow_dispatcher` (serializer A+B + eta_pickup derive + fleet) | ✅ | A1-serializer + R5 |
| `sla_tracker` (R6 bag_time alert) | ✅ | R3 3. alert-kopia |
| `route_simulator` `_count_sla_violations` / `r6_thermal_anchor` | ✅ | R3 kopia A (no paczka-exempt) |
| `feasibility_v2` SLA-loop | ✅ | R3 kopia B (paczka-exempt) |
| `telegram_approver` (eta_pickup display + pickup_ready parse + route) | ✅ | R5 konsument + W3 |
| `geocoding` / `osrm_client` / `geometry` / `address_pin_memory` (haversine) | ✅ | W2 haversine ×6 |
| `czasowka_scheduler` (fleet-builder) | ✅ | W4 (używa dispatchable_fleet ✓) |
| cross-repo `fleet_state` (route + shift_start + floor) | ✅ | R2 konsola + R4 #16 + shift_start indep |
| cross-repo `courier_orders` (route + floor + haversine) | ✅ | R2 apka + R4 #9/#10/#11 + W2 |
| cross-repo `courier_api_panelsync` | ✅ (head) | R2 DEAD (665 vs 1285 L) |
| cross-repo `courier-app` Kotlin RouteLogic | ✅ (grep) | R2 bundling 4. kopia (render-only kolejność) |
| `bag_state` | ⚠ częściowo | build_courier_bag_state nie prześwietlony pod własną kopię reguły A1 (czyta orders_state; brak własnej reguły kolejności/floor — niski priorytet) |
| `feed.py` (cross-repo) | ✅ (grep) | R5 passthrough eta_pickup_hhmm; overlay (klasa O/J — inny agent) |

### Luki pokrycia (świadome)
1. **Wartości LICZBOWE parytetu NIE udowodnione runtime** (czy A≡B bajtowo dla SLA-anchor poza paczką; czy konsola shift_start ≡ silnik na żywym oknie; `ziomek_time_route_monitor` mismatches==?) — to **Faza C (oracle)**, nie B. Read-only inwentarz.
2. **`courier-app` Kotlin** — czytany grepem (render `stopSequence` z serwera, ZERO `sortedBy/sortBy` na stopach) — potwierdzony jako render-kolejności + własny bundling; lokalny re-compute ETA Kotlin NIE prześwietlony pełnym kodem.
3. **`panel_client:792` ścieżka fetch detali pod shadow** — czy shadow realnie ją wykonuje (V1 vs V2 live divergence) = PLAUSIBLE, wymaga trace (Faza C).
4. **`bag_state.build_courier_bag_state`** — sklasyfikowane jako konsument orders_state (nie producent własnej reguły A1 kolejności/floor); pełny węzeł nie rozpisany.
5. **Most paczki** (`parcel_lane_merge`/`parcel_assign`) — czy ma własną kopię route-order/floor (natywny tor orders_state) — handoff Fazy B (nie w moim module-secie wprost).

### NIE-luki (świadomie poza zakresem B02)
- Selekcja `lex_qual`/`bucket`/8-bliźniaków-pozycji = pasmo SELECTION (root K1, inny agent — A6 dedup).
- Flagi efektywne per-proces (A3), przyrządy-prawda (A4/Faza C), sentinele-pełny-sweep (klasa M, osobny agent — tu tylko haversine copy-count + (0,0) most).
- Mailek/Papu (granica STOP na dyspozytorni).

---

## HANDOFF dla Faz D/E/F (anty-double-count)

| Root | Klasy | Kopie otwarte (moje pasmo) | Status |
|---|---|---|---|
| **R2 one route-order module** | A1/B/J (K1+K7) | 5 powierzchni + bundling ×4 + 2. producent `_save_plan_on_assign` bez inwariantów + DEAD panelsync | DIVERGED (44-75/d monitor), 2-producent okno 5-min |
| **R4 one earliest-pickup floor** | A1/A2/H (K1+K2+K4) | `available_from`=∅, 0 inwariant, leak plan_recheck:554, chokepoint state_machine:551, shift_start indep silnik↔konsola | DIVERGED by-construction (case 484400) |
| **R3 one SLA/R6 anchor** | A1/C/I (K1+K3) | 2 inline-lustra + paczka-asymetria (A no/B yes) + sla_tracker alert-kopia + anchor≠R6-anchor | FRAGILE+DIVERGED (O2 02.07) |
| **R5 display≠decision (eta_pickup)** | F | 1 pole, 2 role (extension_penalty) | DRYF SEMANTYKI (latent) |
| **A1-serializer** | A1 | LOCATION A+B, prefix-or-vanish | strukturalne (utrzymywane) |
| Wtórne | A1+B/D2/J/M | parser V1/V2, haversine ×6, pickup_ready parse, fleet-builder | latent/low |

**FAZA D (precedencja/konflikt):** R3 SLA-anchor (pickup_at) ↔ R6-anchor (ready) = dwie HARD-bramki, różny anchor → która wygrywa (I-class). R4 floor: feasibility HARD ↔ plan_recheck BRAK = niespójność ścieżek.
**FAZA E (dedup):** R2/R4 to rodzina „wielokrotne site bez wspólnego źródła cross-repo". R3 = rodzina „inline-lustro + asymetria flagi". NIE liczyć R5 jako kopię-reguły (F-class).
**FAZA F (kontrakty):** (1) JEDEN moduł kolejności importowany przez 3 repa LUB golden-fixture parytet (R2 — musi objąć 2. producent `_save_plan_on_assign`); (2) JEDNO `available_from=max(now,shift_start)` w `courier_resolver` + runtime-inwariant (R4); (3) JEDNA funkcja SLA-anchor ready-based dla 3 bliźniaków (R3); (4) `eta_pickup` display oddzielony od decision-value (R5).
