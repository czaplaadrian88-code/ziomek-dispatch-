# AUDYT „odbiór przed startem zmiany" (pre_shift/no_gps) + sentinel(0,0) — HANDOFF SESJA 2 → SESJA 4
**Data:** 2026-06-30 · **Od:** sesja tmux 2 („Sprawdź logi propozycji Drapieżnika") · **Do:** sesja tmux 4 („Debuguj logikę zaproponowanych tras dowozu")
**CEL:** Scal ten dokument ze swoim audytem w **JEDEN** dokument audytowy (Adrian kończy sesję 2, chce wszystko w sesji 4). Nic nie naprawiamy — to audyt + roadmapa. Pełna kopia w pamięci: `memory/preshift-pickup-floor-audit-2026-06-30.md` + `memory/clamp-preshift-pickup-eta-2026-06-30.md`.

---

## 0. SKĄD TO / METODA
- **Start:** case Drapieżnik→Dawid Kalinowski (zlec. **484400**): konsola pokazała ODBIÓR **10:59** dla kuriera ze zmianą **11:00** (odbiór minutę przed startem pracy = niemożliwy). Realnie: silnik clampował do 11:08, kurier odebrał 11:07. Wyzwalacz tego konkretnego case: Jakub (492) o 10:51:58 dostał `V328_CP_SOLVER_FAIL haversine sentinel (0,0)` → wypadł z puli → zostało pre-shiftowemu Dawidowi.
- **Metoda:** 9 agentów read-only (konsola / apka / silnik-pickup / reguły / wiarygodność danych / driver częstotliwości / zapis committed / pozostałe powierzchnie / wyczerpujący sweep+testy) + 4 agenty rundy weryfikacyjnej (kalibracja/bug#2/spójność/completeness — padły na przejściowy rate-limit serwera, dokończone własnym audytem plikowym) + własne greppy.

---

## 1. BUG #1 — ŹRÓDŁO (jedno zdanie + 5 warstw)
**Nie istnieje JEDNA definicja „najwcześniej kurier może odebrać".** Re-liczona/pominięta w **17 miejscach**, tylko **4 mają floor do `shift_start`**. To nie bug renderu — to dziura definicyjna + świadoma polityka pre_shift.

1. **Reguła:** brak kanonu „pickup ≥ shift_start". HARD-reject dopiero przy `shift_start−30min` (`feasibility_v2.py:747-765`, `common.py:1972 V325_PRE_SHIFT_HARD_REJECT_MIN=30`) + warm-up kara `−20` (`common.py:1975 V325_PRE_SHIFT_SOFT_PENALTY`) **którą zeruje** `ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY` → odbiór w `[shift−30, shift)` JAWNIE DOZWOLONY bez kary. „10:59 vs 11:00" = reguła, nie błąd.
2. **Dane:** `shift_start`/`pos_source` niewiarygodne, liczone inaczej per powierzchnia. Najgroźniejsze: GPS włączony przed zmianą + pusty/zepsuty wpis grafiku → `is_on_shift` fail-open=True (`schedule_utils.py:391`) → silnik widzi `pos_source=gps`+`shift_start=None` → floory milczą + brak kary. Apka grafiku NIE zna.
3. **Polityka = driver częstotliwości:** best=pre_shift w **7,4% propozycji (12-37/dzień)**, +no_gps ≈13,9%, ~84% to normalne wygrane score (NIE scarcity). Z PROJEKTU: okno 60min (`PRE_SHIFT_WINDOW_MAX_MIN=60`) + warm-up 30 + kara=0 (29.06) + equal-treatment bucket + departure clamp. ⚠ Hipoteza „GPS→(0,0)→pre_shift" w WIĘKSZOŚCI FAŁSZYWA (bbox-guard+rescue; `pos_from_store` w best 3,2%).
4. **Leak `plan_recheck` (najszersza dziura):** `plan_recheck.py:554-594` regeneruje `courier_plans.json` **co 5 min bez floor shift_start** (anchor tylko committed) → naprawiony plan SAM SIĘ ODCLAMPOWUJE; pass-through (apka/konsola/restauracja/tracking/kanon) dziedziczą zły czas.
5. **Brak wspólnego floor:** committed `czas_kuriera`/`pickup_at` może być zapisany <shift_start (czasówka, elastyk, first_acceptance; ORAZ panel restauracji `adapter.propose_assignment:187` pisze `promised_pickup_at=now+drive` do BAZY bez sprawdzenia zmiany). Chokepoint kurier↔zlecenie = `COURIER_ASSIGNED` (`state_machine.py:599-633`) — bez floor.

## 2. MAPA 17 POWIERZCHNI (floor do shift_start TAK=4 / NIE=13)
**TAK:** #1 candidate eta pre_shift `dispatch_pipeline.py:5869-5884` (no_gps POMINIĘTY :5856) · #3 plan `feasibility_v2.py:789-819`+`route_simulator_v2.py:273-277` (flaga `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP`) · #7 telegram (dziedziczy #1) · #16 konsola `fleet_state._eta_chain` (fix `CLAMP_PRESHIFT_PICKUP_ETA`, kod default OFF, ON env od 30.06; floruje tylko OSRM-fallback).
**NIE (13):** #4 `chain_eta.py` · **#5 plan_recheck (leak)** · #6 `plan_manager.refloor_pickup` · apka #9 `_committed_pickup_eta`/#10 `_compute_live_eta`/#11 `_attach_fallback_eta`/#12-13 plan-pass · panelsync #14-15 (MARTWY, nie serwowany) · konsola #17 plan-path/#18 committed-ready · #20 `canon_eta`/#21 `deliveries.restaurant_view`/#22 `public_tracking`.
**Powierzchnie pass-through (dodatkowo zweryfikowane, dziedziczą upstream):** `feed.py` (overlay propozycji konsoli), `services/integrations.py on_pickup_eta_lead` (webhook partnera), `tracking.py`/`tracking_map.py`, `auto_koord.py:176`.
**Klasa B (własny now+drive — wymagają floor):** konsola `_eta_chain` (FIX), apka `_attach_fallback_eta`+`_compute_live_eta`, restauracja `adapter.propose_assignment:187`, telegram legacy `_candidate_line:353` (gated OFF), panelsync (martwy). `shadow_quote.py:106`=B ale filtruje pre_shift/no_gps (niepodatne).
**NIE-powierzchnie czasu odbioru (potwierdzone):** `customer_sms.py` (SMS bez godziny, on_pickup), `alerts.py` ALR-01 (DOSTAWA), `sla_tracker.py`+`daily_briefing.py` (pomiar historyczny), `partners/stuart.py` (stały SLA). Apka Kotlin = API-driven (pokryta).

## 3. BUG #2 — sentinel (0,0) / V328 (OSOBNY, ale zasila #1)
Zatrute współrzędne ZLECEŃ (geocode-miss) `delivery_coords=[0,0]` (truthy!) wpadają w haversine w bloku metryk → `V328_CP_SOLVER_FAIL` wyrzuca ZAJĘTEGO kuriera z puli (setki/dzień, do 302 V328 / 394 sentinel). `_sanitize_courier_pos` (`dispatch_pipeline.py:231`) chroni TYLKO pozycję kuriera; pojedyncze haversine (np. `_compute_repo_cost_km:2147`) guardują `None` ale NIE `(0,0)`. `BAG_COORD_REPAIR` (3090) = łatka per-miejsce. **Fix u źródła = repair/reject (0,0) przy INGEST coords (state_machine/geocoding) + jeden guard haversine**, nie N miejsc.

## 4. KALIBRACJA — WERDYKT: neutralna (NIE współprzyczyna), ALE jeden twist
- `PICKUP_DEBIAS_MIN=4.5` ADDYTYWNY (odbiór później/pesymistycznie) → nie zepchnie poniżej startu zmiany. Clamp pre_shift twardo nadpisuje eta. ✅
- ⚠ **TWIST (TOP-3 #2 niżej):** bazowy estymator przyjazdu na odbiór jest OPTYMISTYCZNY (kalibracja 29.06: „poślizg odbioru ~18 min, rośnie z load"). To NIE psuje produkcji wprost, ale **podkopuje skuteczność fixu L1** (reguła „odrzuć kto nie zdąży" liczy spóźnienie za małe).

## 5. SPÓJNOŚĆ / SPRZECZNOŚCI / LUKI
- **MINY FLAG (default-OFF-w-kodzie vs ON-w-flags.json):** `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP`, `ENABLE_NO_GPS_EQUAL_TREATMENT`, `ENABLE_EQUAL_TREATMENT_BUCKET`, `CLAMP_PRESHIFT_PICKUP_ETA`(env) → reset/utrata flags.json = floor+polityka się wywracają.
- **Sprzeczność reguł:** warm-up −20 vs `EQUAL_NO_PENALTY`(zeruje) vs clamp vs HARD−30; kanon `ZIOMEK_REGULY_KANON.md:86` „równo ON" vs `:151` „kara wciąż w kodzie".
- **BRAK runtime-inwariantu** „pickup ≥ shift_start" — grep = ZERO strażnika/assert. To luka #1 dla „nigdy nie wraca".
- **8 bliźniaków pozycji** (`ziomek-change-protocol.md`): selection_bucket, demote, best_effort, drive_min_calibration, auto_assign_gate G7, reassignment_forward_shadow, feed.py, objm_lexr6 — muszą iść RAZEM.
- **Konsola↔apka = osobne repo, brak wspólnego renderera czasu** (`route_podjazdy.py` liczy tylko KOLEJNOŚĆ) → gwarantowany dryf.

## 6. TOP-3 KREATYWNE DZIURY (nieoczywiste, potwierdzone)
1. **Frozen-pickup (R27/incydent 19.06) AKTYWNIE broni złego czasu:** committed nietykalny, OSRM nie nadpisuje (`courier_orders.py:872-892`). Jeśli committed <shift_start (czasówka/elastyk pre-shift — legalne), frozen **zatrzaskuje** złą godzinę; floor na OSRM nie pomoże (frozen omija OSRM). **Pułapka na sam fix** — floor MUSI trafić w wartość committed (chokepoint), uszeregowany względem frozen. Linia 876 wprost: „frozen nigdy < **gotowość**" (nie shift_start).
2. **Model spóźnienia optymistyczny → L1 się oszukuje:** poślizg ~18 min (kalib. 29.06), debias `+4,5` kryje ułamek → silnik liczy spóźnienie pre-shiftowego za małe → L1 „zdąży" i go wybierze, choć realnie 18 min później. **L1 musi liczyć na load-aware buforze, nie surowym ETA.**
3. **Floor wisi na ręcznym arkuszu grafiku (SPOF):** `_shift_start_dt:1252` buduje „dziś" bez +1 (zmiana przez północ=zły dzień); grafik fetch 06:00 → **00:00-06:00 cała flota shift_start=None**; `is_on_shift` przy zepsutym/pustym wpisie = **fail-open 24/7**. Literówka „11.00" w Sheecie = floor martwy dla kuriera na zawsze, cicho. **Potrzeba walidacji wpisów + fail-CLOSED.**

## 7. DODATKOWE TROPY SYSTEMOWE (rodzą KOLEJNE bugi)
1. **Pozycja sprzed 25 min jak aktualna** (`LAST_KNOWN_POS_TTL_MIN=25`, rescue **5639×/dzień**) → ETA/„kto najbliżej" od starej pozycji. Cichy generator złych decyzji.
2. **119 `except Exception` w `dispatch_pipeline`** (feasibility 12, route_sim 12) — Lekcja #32 „silent except=invisible bug". Fabryka niewidzialnych bugów (V328 połykany 100s/dzień).
3. **`plan_recheck` = równoległy „cofacz decyzji"** — regeneruje z własnych kotwic, cofa wszystko czego nie odtworzy (shift-floor, carried-first, bundle, committed-anchor) co 5 min.
4. **Spiętrzone inwersje równości zdjęły CAŁE tarcie z pozycji syntetycznych** → regresja V3.16 „demote blind+empty" tylnymi drzwiami (pusty bag ~82 baseline może wygrać z realnym GPS).
5. **Cała warstwa zegara/TZ aktywna klasa** (today-only, naive/aware, dziś naprawiany `czas_kuriera` closest-day-anchor case 484392, checkpoint-TZ).
6. **Gotowość (`pickup_at_warsaw` z gastro) jako floor sama niepewna** (prep optymistyczny + `prep_bias +9`).
7. **read-with-side-effect** (ugryzł 29.06 `load_plan`) — sweep wskazany.
8. **Wielosesyjny shared-deploy** — dziś realna kolizja na `fleet_state.py` (sesje 2 i 4 i inne na wspólnych repo).

## 8. DECYZJE ADRIANA — ZABLOKOWANE (30.06)
- **Q1 zakres = OBA:** jedno źródło floor (commit+rendery) **+ twardsza feasibility**.
- **Q2 frozen-konflikt:** deklaracja restauracji NIETYKALNA → **nie dawaj zlecenia pre-shift kurierowi, który nie zdąży na odbiór** (zmieniaj KTO, nie czas).
- **Q1b (konflikt z 29.06 ROZSTRZYGNIĘTY):** równość ZOSTAJE, `ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY` NIETKNIĘTE — „nie zdąży" = FAKT FEASIBILITY/SPÓŹNIENIE (R-LATE-PICKUP), NIE nowa kara.
- **Q2b:** floor/reguła obejmuje OBA: pre_shift + no_gps (dla no_gps on-shift = no-op, `max(now,shift_start)=now`).

## 9. ROADMAPA „RAZ A DOBRZE" (L0-L6 + bug#2) — NIE WYKONANE
- **L0** jedno `available_from = max(now, shift_start)` w `courier_resolver` (RAZ) + naprawa danych (GPS-przed-zmianą misclass, fail-open puste godziny). Obejmuje pre_shift+no_gps.
- **L1** feasibility RULE-level: pre/no_gps nie wygrywa zlecenia którego nie zdąży vs kurier on-shift — przez SPÓŹNIENIE (na **load-aware buforze**, nie surowym ETA — patrz TOP-3 #2), thin-fleet=wyjątek. Zaostrz warm-up 30/−20.
- **L2** silnik clampuje WSZĘDZIE: `plan_recheck` (KLUCZ), candidate-no_gps, chokepoint `COURIER_ASSIGNED` (osobne pole `effective_pickup_at`, NIE nadpisuj deklaracji — zgodne z frozen TOP-3 #1).
- **L3** pas renderów (apka `_attach_fallback_eta`, restauracja `adapter`, telegram-legacy) na sclampowanym planie z L2 lub własny floor.
- **L4** testy: poprawić utrwalające „floor tylko committed/gotowość" (`test_courier_orders_plan.py`, `test_fleet_route.py:323-393`, `test_floor_pickups_at_birth`, `test_gps_free_anchor::committed_pickup_anchor`).
- **L5** HARDENING defaultów flag (kod=intencja, by reset flags.json nie wskrzeszał buga).
- **L6** RUNTIME INWARIANT + STRAŻNIK + TEST: „żaden predicted_at/czas_kuriera/render pickup < shift_start przypisanego kuriera" oraz „żaden haversine na (0,0)" — JEDYNE co realnie blokuje nawrót na zawsze (wzór: `tools/carried_first_guard.py`).
- **BUG #2** osobny tor: sanityzacja/repair (0,0) u źródła współrzędnych + jeden guard haversine.
**Sekwencja sugerowana:** L6-strażnik (shadow, MIERZ nawrót) → L2 plan_recheck → L0/L1 → L5 → L3 → Bug#2. Najwyższy zwrot na nawroty = L2 plan_recheck. Najgłębsze „nigdy" = L0+L1+L6.
**Deploy = P0 (silnik feasibility/plan_recheck/courier_resolver/state_machine + bliźniaki)** → protokół ETAP 0→7, off-peak po 14:00, ACK Adriana. Replay ON↔OFF, parytet bliźniaków, pełna regresja.

## 10. CO DONE / PENDING + POINTERY
- **DONE (LIVE):** konsola fix `CLAMP_PRESHIFT_PICKUP_ETA` (commit panel `1cf36cd`, tag `clamp-preshift-pickup-eta-2026-06-30`, flaga ON env, test ON≠OFF 4/4, regresja 0 nowych). To 1 z 4 floorów — pas bezpieczeństwa, NIE pełny fix.
- **PENDING:** całe L0-L6 + bug#2 (czeka na „go" Adriana).
- **Pamięć:** `memory/preshift-pickup-floor-audit-2026-06-30.md` (pełna mapa), `memory/clamp-preshift-pickup-eta-2026-06-30.md` (fix konsoli).
- **Raporty agentów (transkrypty):** `/tmp/claude-0/-root/10174bba-c495-4d45-867f-836aae62f044/tasks/{a66faa3b62f853866,a29f35b2d30d1e1a7,a6f0214ff10f8899f,a14aa1fbcba15d03b,ad70732997d1154c9,a60e8f08406f20543}.output`.

## 11. INSTRUKCJA SCALENIA (dla sesji 4)
Połącz to z Twoim dokumentem audytowym w JEDEN. Jeśli Twój audyt dotyczy „logiki zaproponowanych tras dowozu" — prawdopodobnie pokrywa się z sekcjami #1 (silnik), #2 (powierzchnie renderu trasy), #6/#7 (tropy). Uzgodnij rozbieżności (zwłaszcza numeracja linii — mogły się przesunąć po commitach innych sesji; zweryfikuj `git log` przed cytowaniem). Zachowaj DECYZJE (#8) i ROADMAPĘ (#9) jako wspólny plan. Konflikt/niejasność → pytaj Adriana.
