# D02 — MAPA KONFLIKTÓW / GRAF PRECEDENCJI (Lane D, oś I — NOWA)

**Faza 1 audytu spójności Ziomka · sesja tmux 2 · READ-ONLY · 2026-06-30 ~14:1x UTC**
**Agent:** D02-precedence-paths. **Zasila:** Fazę D (koherencja) + Fazę F (kontrakty docelowe).
**Metoda:** graf interakcji reguł/flag z A2 (rule registry) + A3 (flag registry) + precedencja MIĘDZY ŚCIEŻKAMI (I3) z A5/A6. Każdy `plik:linia` z ŚWIEŻEGO grepu DZIŚ (linie dryfują — ≥3 żywe sesje). Zero edycji/flipów/restartów.

**Co to jest (oś I):** dla KAŻDEJ pary konfliktowej reguł/flag/ścieżek: `rule_a`, `rule_b`, **natura** (inwersja HARD↔SOFT / sprzeczność / niezdefiniowana-lub-niespójna-precedencja / sprzężenie-flag / cicha-inwersja-P), **precedence_status** (defined-consistent / defined-inconsistent / undefined / silent-inversion / ok), dowód `plik:linia`. **NOWE względem poprzednich audytów:** poprzednie znajdowały KOPIE reguł (A1/B/J) i ZŁĄ WARSTWĘ (C); ta oś pyta **KTO WYGRYWA gdy dwie reguły/ścieżki dotykają tej samej decyzji i czy to ROZSTRZYGNIĘTE SPÓJNIE.**

**Anti-double-count (z A6 distinct-root rollup):** grupy lex_qual/bucket/inline = TEN SAM root K1 (NIE 3 chaosy). Dedup_hint każdej pary zwija do 5 distinct otwartych rozjazdów + roots K1-K7 z [[ziomek-unified-audit-2026-06-30]]. Ta oś NIE re-derywuje kopii — mapuje ich PRECEDENCJĘ.

---

## 0. TL;DR — 6 twardych faktów precedencji

1. **`plan_recheck` (faza B, 5-min regen) MOŻE COFNĄĆ HARD-floor `feasibility` (faza A)** — pre-shift floor jest HARD w `feasibility_v2`, a `plan_recheck._start_anchor` regeneruje `courier_plans` kotwicząc TYLKO na committed (bez `shift_start`). To **defined-inconsistent**: ostatni-zapis-wygrywa, faza B odclampowuje fazę A co tick. (K2 „cofacz" = problem PRECEDENCJI, nie tylko bliźniaków.)
2. **Dwie HARD-bramki tej samej decyzji kotwiczą RÓŻNIE:** R6-thermal na `pickup_ready_at` (gotowość), SLA-loop na `pickup_at` (TSP-projekcja). + `ENABLE_ETA_QUANTILE_R6_BAGCAP` (ON) **rozluźnia HARD R6** dla gold≤4 na p80, a SLA-loop nie jest co-designowany → bag może przejść R6 a SLA liczy inaczej. **defined-inconsistent** (O2 sprint 02.07).
3. **`_assert_feasibility_first` (P0 guard, l.5938) NIE chroni `FEAS_CARRY_READMIT` (l.6266)** — readmit re-dopuszcza `verdict=NO`→`MAYBE` na `top[0]` ZA guardem. **silent-inversion** (wzorzec #10), ACK-SAFE bo flaga OFF — **latentna mina pod flipem**.
4. **`ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE`: const env-default `"1"`=True (common.py:2806) zamaskowana flags.json=False (l.148).** Usunięcie klucza z flags.json = **cichy FLIP na ON** → KOORD-redirect wraca → utrata dyrektywy ALWAYS-PROPOSE. **silent-inversion** krucha.
5. **DWIE reguły obciążenia floty ŻYWE naraz:** `ENABLE_V326_FLEET_LOAD_BALANCE` (±15) ON + `ENABLE_FLEET_LOAD_GOVERNOR` ON (**flags.json:165=true NADPISUJE const OFF common.py:2103 — A2 czytał const, nie efektywny**). Governor dodatkowo **rozluźnia okno committed-pickup R27** przy `loadgov_ewma≥4.5`. Podwójne liczenie obciążenia + sprzężenie z R27.
6. **Cross-repo (silnik vs konsola vs apka): precedencja kolejności/czasu UNDEFINED** — 3 niezależne flagi TRUST_CANON w 3 systemach flag, brak wspólnego importu, parytet = pomiar 95,9% / monitor 44-75 rozjazdów/d. Czas: committed/frozen NIETYKALNY na 3 powierzchniach, ale FLOOR (CLAMP_PRESHIFT) tylko na ścieżce OSRM konsoli — apka BEZ floor → ten sam kurier inny czas odbioru na 2 ekranach.

---

## 1. GRAF PRECEDENCJI MIĘDZY ŚCIEŻKAMI (I3) — kto wygrywa gdy ta sama decyzja liczona w wielu miejscach

### 1a. feasibility(A) → scoring(L6) → selekcja(L7) → werdykt(L8) → plan_recheck(B regen) — łańcuch decyzji w SILNIKU

**Kolejność egzekucji (świeże linie, `dispatch_pipeline.py`):**
1. `check_feasibility_v2` (L5 HARD) → per-kandydat `feasibility_verdict ∈ {MAYBE, NO}`.
2. scoring (L6) — w bloku scoringu **4 dodatkowe HARD-rejecty** patchują `verdict` na NO: `v324a_extension_hard_reject` (l.5610), `carry_chain_hard_rejected` (l.5619), `v3273_wait_courier_hard_reject` (l.5637-5646), `intra_rest_gap_hard_reject` (l.5650). **Wszystkie `if ... and verdict=="MAYBE"`** → monotoniczne (NIE przebijają wcześniejszego NO).
3. `_assert_feasibility_first(feasible, order_id)` (l.5938, P0 INV) — żaden `NO` w puli selekcji.
4. selekcja best_effort/objm (l.6751-6803), OBJM live-flip (l.6771).
5. **`FEAS_CARRY_READMIT` (l.6266)** — OSTATNIA mutacja `top`/`feasible`, ZA guardem #3.
6. werdykt KOORD-redirect (l.6443/6491/6523/6609/6864/6900).
7. `plan_recheck.run_recheck` (osobny proces, 5-min) — regen `courier_plans` z WŁASNYCH kotwic.

**Precedencja PER decyzja:**

| Decyzja | Ścieżka A | Ścieżka B | Kto wygrywa | Status |
|---|---|---|---|---|
| **HARD-reject (R6/SLA/wait/extension)** | feasibility L5 `verdict=NO` | scoring-L6 hard-rejecty `verdict=NO` (l.5610-5651) | **dowolny NO trzyma NO** (monotonic, `and verdict=="MAYBE"`) | **defined-consistent** ✅ (jedyny czysty łańcuch) |
| **selekcja zwycięzcy** | `_best_effort_sort_key` (carry-ślepy, geometria 5. tie-break) l.6751 | `_best_effort_objm_pick` (l.6771, OBJM live `ENABLE_BEST_EFFORT_OBJM_R6_KEY` ON) | **OBJM override nadpisuje sort_key** (l.6775) — wyrzuca ostatni ślad geometrii | **defined-inconsistent** (geometria ginie — patrz C5) |
| **re-dopuszczenie NO** | `_assert_feasibility_first` (l.5938) blokuje | `FEAS_CARRY_READMIT` (l.6266) promuje NO→MAYBE | **readmit ZA guardem wygrywa** (flaga OFF → latentne) | **silent-inversion** (C4) |
| **floor pickup ≥ shift_start** | feasibility clamp HARD (l.789-819) | `plan_recheck` regen bez floor (l.534-594) | **plan_recheck (ostatni zapis courier_plans) odclampowuje** | **defined-inconsistent** (C1, K2) |
| **kotwica R6 vs SLA** | `r6_thermal_anchor` ready (route_sim:663) | `_count_sla_violations` pickup_at (route_sim:635) | **obie HARD muszą przejść, ale liczą inny anchor** | **defined-inconsistent** (C2) |

### 1b. silnik ↔ konsola ↔ apka (render decyzji cross-repo) — kolejność i czas na EKRANIE KURIERA

**Kolejność jazdy (route-order):** brak wspólnego importu repo↔repo (A5 C.1/C.8). Każda powierzchnia ma WŁASNĄ kopię + własną flagę TRUST_CANON:
- silnik kanon: `plan_recheck._apply_canon_order_invariants:1478`
- konsola: `fleet_state._build_route:395` (`TRUST_CANON_ORDER` l.443, `TRUST_CANON_WHEN_COVERS_BAG` l.375/877)
- apka: `courier_orders.build_view:1072` → `route_podjazdy.order_podjazdy` GDY `APP_ROUTE_FROM_CONSOLE` (l.1116), inaczej własny `_plan_stop_sequence`; `BUILD_VIEW_TRUST_CANON_ORDER` (l.1120) **MARTWA bo `APP_ROUTE_FROM_CONSOLE=1` short-circuituje przed nią** (C5 near-miss, A6 grupa 2).
- **Precedencja = UNDEFINED:** „kto wygrywa" = która flaga ustawiona + który fallback odpali. Parytet = POMIAR 95,9% (fleet_state:866) + monitor `ziomek_time_route_monitor` 44-75/d, NIE inwariant. (C17)

**Czas odbioru na ekranie (committed vs OSRM vs floor):**
- **committed/frozen `czas_kuriera` = NAJWYŻSZA precedencja, NIETYKALNY** na 3 powierzchniach: konsola „ODBIORY zostają nietknięte — committed czas_kuriera to obietnica" (fleet_state:624) + `PIN_AGREED_PICKUP_TIME` (l.509); apka `config.FROZEN_PICKUP_ETA` (courier_orders:872, „OSRM go nie nadpisuje").
- **FLOOR (`CLAMP_PRESHIFT_PICKUP_ETA`) = niższa, TYLKO na ścieżce OSRM-chain konsoli** (fleet_state:857 `if clamp_preshift_eta and not si["on_shift"]`). Apka NIE ma floor `shift_start` w ogóle (A6 grupa 6 #10/#11 BRAK).
- **OSRM live = najniższa.**
- **KONFLIKT (C19):** jeśli committed `<shift_start` (czasówka/elastyk pre-shift = LEGALNE), frozen ZATRZASKUJE złą godzinę; floor (tylko na OSRM) NIE pomoże. Precedencja `frozen > floor > OSRM` jest **defined-inconsistent** — floor nie komponuje się z frozen, brak reguły „floor bije pre-shiftowy committed". (preshift-audit TOP-3 #1)

---

## 2. KATALOG PAR KONFLIKTOWYCH (20 par) — rule_a × rule_b × natura × precedence_status × dowód

> Legenda natury: **HS**=inwersja HARD↔SOFT · **SP**=sprzeczność · **UP**=niezdefiniowana/niespójna precedencja · **FC**=sprzężenie-flag · **PI**=cicha-inwersja-P.

### 🔴 P0/P1 — żywe, materialne

**C1 · pre-shift floor: feasibility(HARD) ↔ plan_recheck(regen bez floor)** · natura **UP** · **defined-inconsistent**
- rule_a: `feasibility_v2.py:789-819` `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP` (ON) — `earliest_departure=max(now,shift_start)` HARD.
- rule_b: `plan_recheck.py:534` `_earliest_committed_pickup_anchor` + `:554` `_start_anchor` — regen `courier_plans.json` co 5 min, kotwica TYLKO committed, ZERO `shift_start`/`available_from`.
- Kto wygrywa: **plan_recheck (ostatni writer kanonu)** odclampowuje co tick → naprawiony plan sam się cofa. Konsumenci (apka/konsola/tracking) dziedziczą zły czas.
- Dowód: `grep available_from --include=*.py`=∅ (A6 grupa 6); plan_recheck:351 komentarz `_start_anchor`. dedup→**floor-17-powierzchni (K1+K2+K4, R4)**.

**C2 · R6-anchor(ready) ↔ SLA-anchor(pickup_at) — dwie HARD-bramki, inny anchor** · natura **SP** · **defined-inconsistent**
- rule_a: `route_simulator_v2.py:663` `r6_thermal_anchor` (ready: `picked_up_at→pickup_ready_at→tsp→now`), inwariant INV-R6-ANCHOR-CONSISTENCY; konsument `feasibility_v2.py:1046`.
- rule_b: `route_simulator_v2.py:635` `_count_sla_violations` (pickup_at→picked_up_at→now) + lustro `feasibility_v2.py:1135-1166` SLA-loop.
- Kto wygrywa: **obie muszą przejść** (HARD AND), ale bag ready-anchored ≤35 może mieć SLA liczony na pickup_at → niespójny werdykt na worek czas_late>0. + paczka-exempt jest w SLA-loop (feasibility:1152) ale NIE w `_count_sla_violations` → `plan.sla_violations` liczy paczkę, per-order loop pomija.
- Dowód: route_sim:1297 komentarz; feasibility:1135 `if plan.sla_violations>0`. dedup→**SLA-anchor≠R6-anchor (K1+K3, R3)** (O2 02.07).

**C3 · ETA_QUANTILE_R6_BAGCAP(rozluźnia HARD R6) ↔ SLA-loop(nie rozluźniona)** · natura **HS+FC** · **defined-inconsistent**
- rule_a: `feasibility_v2.py:1089` `ENABLE_ETA_QUANTILE_R6_BAGCAP` (flags.json:179=**true**) — dla `gold` + bag≤4 bramkuje R6 na `_gate_bt=p80(bag_time)` zamiast surowego → **>35 ready-anchored PRZECHODZI**.
- rule_b: SLA-loop / inne tiery = legacy hard-35; naiwne ready-anchorowanie SLA „by je re-rejectowało" (protokół C).
- Kto wygrywa: kalibracja p80 rozluźnia HARD R6 (SOFT osłabia HARD — narusza P0 w literze, ALE ACK-owana 14.06 jako recovery false-reject, gated gold≤4); SLA nie co-designowana → para HARD-bramek niespójna.
- Dowód: feasibility:1085-1097 `r6_gold4_gate_recovered`. dedup→**SLA-anchor (K1+K3, R3)**.

**C4 · _assert_feasibility_first(P0 guard) ↔ FEAS_CARRY_READMIT(mutacja ZA guardem)** · natura **PI** · **silent-inversion**
- rule_a: `dispatch_pipeline.py:5938` `_assert_feasibility_first` — INV `feasibility_verdict!='NO'` w puli (fail-loud).
- rule_b: `dispatch_pipeline.py:6266` `ENABLE_FEAS_CARRY_READMIT` (flags.json=**false**) — `_feas_carry_readmit_pick` promuje odrzucony `verdict=NO`→`MAYBE` na `top[0]` (l.6278), pop+insert ZA guardem.
- Kto wygrywa: **readmit (l.6266 > guard 5938)** — re-dopuszcza NO. Dziś **latentne** (flaga OFF); komentarz „mutacja TYLKO obniża breach" = ACK-SAFE (protokół OBALONE). Mina pod flipem (C2-protokołu „flip=pełny deploy").
- Dowód: ordering 5938<6266; wzorzec #10. dedup→**HARD-bypass-po-guardzie (klasa C, wzorzec #10)**.

**C5 · geometria-rozjazdu(SOFT-only) ↔ lex_qual(czysto czasowy klucz) — geometria NIE MA jak wygrać pod scarcity** · natura **HS+UP** · **undefined**
- rule_a: `feasibility_v2.py:501-547` liczy+serializuje `deliv_spread_km`/`r1_violation_km`/`r1_avg_pairwise_cosine`, ALE R1 spread>8 km **NIE rejectuje** (metric-only); geometria żyje WYŁĄCZNIE jako SOFT-kara w `score`.
- rule_b: `objm_lexr6.py:40` `lex_qual=(r6_breach, committed_late, new_pickup_late)` = **ZERO osi geometrii**; best_effort/objm `score` NIE czyta.
- Kto wygrywa: **lex_qual (czas)** — jedyna HARD-eskalacja geometrii `geometry_blind_fallback` (l.6443) wymaga `feasible≥2 AND all greedy_fallback AND all cos<0` → **NIE odpala pod pool=0/mieszanym**. Geometria nie ma zdefiniowanej drogi by pobić czas.
- Dowód: l.6443-6453 wąska bramka; allocation-audit P0-A (case Dawid 447 spread 10,12 km wygrywa r6=38,4). dedup→**one-selection-key (K1, R1) + greedy-pile-on (K6)**.

**C16 · equal-treatment engine(no_gps/pre_shift RÓWNO) ↔ out-of-engine gates(dyskryminują)** · natura **SP** · **defined-inconsistent**
- rule_a: `dispatch_pipeline.py:2451` `_selection_bucket` (equal-treatment-aware, flags `NO_GPS_EQUAL_TREATMENT`+`EQUAL_TREATMENT_BUCKET`+`PRE_SHIFT_EQUAL_NO_PENALTY` ON) — no_gps/pre_shift konkurują PO SCORE.
- rule_b: `tools/reassignment_forward_shadow.py:64` `_SYNTH_POS={none,pin,pre_shift,""}` + `a_late=(a_cand is None)` (duch przerzutu, 59% fałszywych ratunków); `auto_assign_gate.py:160` G7 `pos_not_informed` (latent, AUTO_ASSIGN OFF); `feed.py` overlay bez `_pos_trusted`.
- Kto wygrywa: engine RÓWNO, ale shadow/gate/feed nadal karzą pozycję syntetyczną → klasa „wraca ≥4×". ŻADEN test nie wiąże out-of-engine z `_selection_bucket`.
- Dowód: A6 grupa 3b; protokół #2. dedup→**out-of-engine-gates-pozycji (K1, R1) + sentinele K5**.

**C19 · committed/frozen(NIETYKALNY) ↔ floor(tylko OSRM) ↔ OSRM — kolejność clampów na ekranie** · natura **UP** · **defined-inconsistent**
- rule_a: frozen committed `czas_kuriera` — konsola `fleet_state.py:624`, apka `courier_orders.py:872` `FROZEN_PICKUP_ETA`.
- rule_b: `CLAMP_PRESHIFT_PICKUP_ETA` — konsola `fleet_state.py:857` TYLKO `not on_shift` na ścieżce OSRM-chain.
- Kto wygrywa: **frozen > floor**; gdy committed `<shift_start` (legalne pre-shift) frozen zatrzaskuje złą godzinę, floor (na OSRM) nie sięga. Brak reguły kompozycji.
- Dowód: preshift-audit TOP-3 #1 „frozen nigdy < gotowość" (NIE shift_start). dedup→**floor-17-powierzchni (R4)** + frozen R27.

### 🟠 P2 — żywe lub latentne, mniejsza materialność

**C6 · lex_qual kanon(4-krotka gdy POST_SHIFT ON) ↔ _objm_lexr6_shadow(3-krotka frozen)** · natura **FC** · **defined-consistent (FRAGILE)**
- rule_a: `objm_lexr6.py:44-47` — `ENABLE_POST_SHIFT_OVERRUN_PENALTY` ON → prepend WIODĄCY term (4-krotka); OFF → 3-krotka.
- rule_b: `dispatch_pipeline.py:1122` `_objm_lexr6_shadow._lex_qual` — ZAWSZE 3-krotka HARD-CODED (frozen pod at#152).
- Kto wygrywa: dziś zgodne TYLKO bo POST_SHIFT OFF (0.0 no-op). Flip ON → cień rankuje INACZEJ niż live = kłamiący przyrząd. dedup→**frozen-_lex_qual-shadow (K1, R1)**.

**C7 · COMMIT_DIVERGENCE_VERDICT_GATE: const True ↔ flags.json False** · natura **PI** · **silent-inversion**
- rule_a: `common.py:2805-2806` const env-default `"1"`=**True**.
- rule_b: `flags.json:148`=**false** maskuje (decision_flag: json>const) → effective False.
- Kto wygrywa: flags.json (False) → gate OFF → always-propose żyje. **Usunięcie klucza = cichy FLIP ON** → KOORD-redirect (l.6523) wraca, utrata ALWAYS-PROPOSE. Krucha jedyna inwersja maskująca. dedup→**flag-masking (klasa D/M, A3 §2b)**.

**C9 · R-10 FLEET-LOAD-BALANCE(±15) ↔ FLEET-LOAD-GOVERNOR — DWIE reguły load obie ON** · natura **SP+FC** · **defined-inconsistent**
- rule_a: `dispatch_pipeline.py:1462` `ENABLE_V326_FLEET_LOAD_BALANCE` ON (delta load → ±15 score).
- rule_b: `ENABLE_FLEET_LOAD_GOVERNOR` — **const OFF (common.py:2103 `"0"`) ale flags.json:165=true → EFEKTYWNY ON** (A2 błędnie „OFF": czytał const); `bonus_loadgov_shadow_delta` „-40 LIVE" (l.2303).
- Kto wygrywa: oba aplikowane — podwójne liczenie obciążenia (±15 balance + −40 governor) różnymi mechanizmami tego samego pojęcia. dedup→**dwie-reguły-load (klasa A1/I) + flag-drift A2-vs-A3 (klasa D)**.

**C10 · FLEET-LOAD-GOVERNOR(rozluźnia) ↔ R27 ±5 committed window** · natura **FC** · **defined-inconsistent**
- rule_a: `common.py:2556` `OBJ_COMMITTED_PICKUP_LOAD_THRESHOLD=4.5` → `dispatch_pipeline.py:3404-3409` `set_committed_pickup_tolerance` gdy `loadgov_ewma≥4.5`.
- rule_b: R27 ±5 SOFT (`route_simulator_v2.py` frozen window, `tsp_solver.py:263` SoftUpperBound).
- Kto wygrywa: governor ROZLUŹNIA tolerancję committed-pickup (5→10 min) pod obciążeniem → SOFT-load modyfikuje SOFT-R27. Świadome (Adrian 50 zleceń/11 std≈4,5), ale sprzężenie nieoczywiste. dedup→**loadgov↔R27 sprzężenie (klasa I/FC)**.

**C11 · R-DECLARED-TIME(deklarowana HARD) ↔ R27(SOFT egzekwuje pośrednio)** · natura **HS+UP** · **undefined**
- rule_a: R-DECLARED-TIME „`czas_kuriera ≥ czas_odbioru` zawsze (HARD)" — **BRAK runtime-bramki/inwariantu** (tylko komentarze common.py:3494, dispatch_pipeline:3168).
- rule_b: R27 frozen + `pickup_ready_at=czas_kuriera` (dispatch_pipeline:3486) — de-facto egzekucja.
- Kto wygrywa: nikt nie sprawdza `czas_kuriera ≥ czas_odbioru` jako gate; egzekucja emergentna z R27+czasówka. HARD bez egzekutora. dedup→**HARD-bez-runtime (klasa I/C)**.

**C12 · R-RETURN-VETO(nazwa VETO, feasibility metric-only) ↔ NO_RETURN_TO_DEPARTED(kanon egzekwuje)** · natura **SP** · **defined-inconsistent**
- rule_a: `feasibility_v2.py:905-914` `ENABLE_R_RETURN_TO_RESTAURANT_VETO` (flags.json:86=true) — `detect_return_to_restaurant`→ TYLKO `metrics["return_to_restaurant"]`, „NIGDY nie przerywa feasibility".
- rule_b: `plan_recheck.py:1518-1519` `ENABLE_NO_RETURN_TO_DEPARTED_PICKUP` — realny zakaz w kanonie.
- Kto wygrywa: mimo nazwy „VETO" feasibility NIE vetuje; egzekucja w innej warstwie+fladze. Nazwa myli precedencję. dedup→**nazwa-VETO-vs-egzekucja (klasa I/L/B)**.

**C13 · LATE_PICKUP_HARD_GATE(nazwa HARD) ↔ zachowanie SELEKCJA-tier** · natura **HS** · **defined-inconsistent**
- rule_a: `common.py:2822` `ENABLE_LATE_PICKUP_HARD_GATE` ON + `LATE_PICKUP_HARD_MAX_MIN=5.0`.
- rule_b: `dispatch_pipeline.py:5655` „NIE hard-reject — kandydat zostaje feasible, spóźnienie rozstrzyga TIERING" + `_late_pickup_tier:496`.
- Kto wygrywa: zachowanie = SELEKCJA-tier (demote), NIE hard-reject. Nazwa `*_HARD_GATE` sugeruje bramkę → ryzyko że nowa sesja potraktuje jako HARD vs R6/R27. dedup→**mylące-słownictwo (klasa L/I)**.

**C14 · OR_TOOLS_TSP ↔ SAME_RESTAURANT_GROUPING — sprzężenie (flip jednej=double-insert)** · natura **FC** · **defined-consistent (sprzężone)**
- rule_a: `common.py:2356` `ENABLE_V326_OR_TOOLS_TSP` env-default ON.
- rule_b: `common.py:3159` `ENABLE_V326_SAME_RESTAURANT_GROUPING` env-default ON.
- Kto wygrywa: oba ON dziś OK; OR_TOOLS OFF **odsłania** grouping double-insert super-pickupa w greedy/bruteforce (wzorzec #13/C3). Oba env-frozen, **NIE w flags.json/ETAP4/fingerprint** → parytet niewidoczny. dedup→**flag-coupling OR_TOOLS↔GROUPING (klasa D/FC)**.

**C15 · equal-treatment(_selection_bucket) ↔ _demote_blind_empty(V3.16, osobny mechanizm)** · natura **SP** · **defined-inconsistent**
- rule_a: `_selection_bucket` equal-treatment ON — no_gps/pre_shift konkurują równo.
- rule_b: `dispatch_pipeline.py:~1812` `_demote_blind_empty` (V3.16) używa WŁASNYCH klasyfikatorów (`_is_blind_empty_cand:477`/`_is_informed_cand:490`), NIE `_selection_bucket`.
- Kto wygrywa: equal-treatment zdjęło tarcie z pozycji syntetycznych → „regresja V3.16 demote blind+empty tylnymi drzwiami: pusty bag ~82 baseline może wygrać z realnym GPS" (preshift-audit #4). Dwa mechanizmy, przeciwne kierunki. dedup→**one-selection-key (K1, R1) twin 7**.

**C20 · floor czasu: konsola CLAMP(ON) ↔ apka(BRAK shift_start floor)** · natura **B** · **defined-inconsistent**
- rule_a: konsola `fleet_state.py:857` `CLAMP_PRESHIFT_PICKUP_ETA` (16. powierzchnia ma floor).
- rule_b: apka `courier_orders.py` `_attach_fallback_eta`/`_compute_live_eta` (A6 grupa 6 #10/#11 — BRAK floor `shift_start`).
- Kto wygrywa: różny czas odbioru ten sam kurier konsola↔apka. 4/17 powierzchni floruje. dedup→**floor-17-powierzchni (R4) bliźniak konsola↔apka**.

### 🟡 P3 — kosmetyczne / niska materialność (odnotowane dla kompletności)

**C8 · always-propose(dyrektywa) ↔ geometry_blind KOORD(NIE sprawdza always_propose)** · natura **SP** · **defined-inconsistent**
- rule_a: `dispatch_pipeline.py:6491/6864/6900` KOORD-gates `and not _always_propose_on()` (ALWAYS_PROPOSE ON, flags.json:184).
- rule_b: `dispatch_pipeline.py:6453` `geometry_blind_fallback` zwraca KOORD **BEZ** `_always_propose_on()` checka.
- Kto wygrywa: geometry_blind eskaluje do KOORD nawet pod always-propose (asymetria), inne gates nie. Rzadko odpala (wąska bramka) → P3. dedup→**always-propose-asymetria (klasa I)**.

**C18 · APP_ROUTE_FROM_CONSOLE(short-circuit) ↔ BUILD_VIEW_TRUST_CANON_ORDER(martwa)** · natura **FC** · **defined-inconsistent**
- rule_a: `courier_orders.py:1116` `APP_ROUTE_FROM_CONSOLE` ustawia ścieżkę renderu PRZED...
- rule_b: `courier_orders.py:1120` `BUILD_VIEW_TRUST_CANON_ORDER` — konsumowana TYLKO w gałęzi za short-circuitem → **flaga ON ale nieosiągalna** (C5 near-miss).
- Kto wygrywa: APP_ROUTE_FROM_CONSOLE; BUILD_VIEW_TRUST_CANON martwa. dedup→**dead-flag-short-circuit (klasa D/K, C5)**.

**C-OK1 · scoring-L6 hard-rejecty ↔ feasibility-L5 — ZŁA WARSTWA ale precedencja SPÓJNA** · natura **HS** · **defined-consistent (ok)**
- rule_a: `feasibility_v2` L5 HARD (`verdict=NO`).
- rule_b: `scoring.py:150-151` `compute_wait_courier_penalty` zwraca `(0.0, True)` (>20 min) → `dispatch_pipeline.py:5637-5646` `verdict="NO"` + carry-chain/intra-gap/v324a (l.5610-5651) — 4 HARD-rejecty W WARSTWIE SCORINGU (L6).
- Kto wygrywa: **dowolny NO trzyma NO** (`and verdict=="MAYBE"`), wszystkie przed `_assert_feasibility_first`. Precedencja **defined-consistent** — ale HARD-logika żyje w warstwie SOFT (smell C, nie precedencji). **Rozstrzyga otwarte pytanie A2 smell #11: tail R9 wait ŻYJE (zwraca True l.151) i jest konsumowany (verdict=NO l.5644).** dedup→**HARD-w-warstwie-SOFT (klasa C, NIE złamana precedencja)**.

---

## 3. SYNTEZA — gdzie precedencja UNDEFINED / INCONSISTENT (dla Fazy F)

| precedence_status | pary | wzorzec |
|---|---|---|
| **undefined** | C5 (geometria vs czas), C11 (R-DECLARED bez runtime), C17 (route-order cross-repo) | brak reguły KTO wygrywa — emergentne/statystyczne |
| **silent-inversion** | C4 (FEAS_CARRY za guardem), C7 (commit-divergence masking) | flip/usunięcie klucza cicho odwraca — latentne miny |
| **defined-inconsistent** | C1, C2, C3, C9, C10, C12, C13, C15, C16, C19, C20, C8, C18 | reguła „kto wygrywa" istnieje ale niespójna między ścieżkami/warstwami |
| **defined-consistent (FRAGILE)** | C6 (lex_qual 3/4-krotka), C14 (OR_TOOLS↔GROUPING) | zgodne TYLKO przy obecnym stanie flag; następny flip rozjedzie |
| **defined-consistent (ok)** | C-OK1 (scoring-L6 hard-rejecty monotonic) | jedyny zweryfikowany czysty łańcuch HARD |

**3 KANONICZNE braki kontraktu precedencji (Faza F):**
1. **Brak JEDNEGO `available_from`/floor z runtime-inwariantem** → C1+C19+C20 (faza A vs B vs render). Najwyższy zwrot: floor w `plan_recheck` + chokepoint `COURIER_ASSIGNED` + strażnik „pickup ≥ shift_start".
2. **Brak JEDNEGO anchora R6/SLA** → C2+C3. Co-design `_count_sla_violations` + feasibility SLA-loop + `_o2_key` + ETA_QUANTILE + paczka-exempt RAZEM (O2 02.07).
3. **Brak osi GEOMETRII w kluczu selekcji + brak global de-konflikcji** → C5+C16 (P0-A/P0-B allocation-audit). Człon rozjazdu w kanonie `lex_qual` PO `objm-lexr6-unify`.

---

## 4. POKRYCIE (jawne — nie cisza)

**Zbadane (świeży grep DZIŚ):**
- Reguły A2: R1/R3/R5/R6/R7/R8/R9/R27, R-DECLARED, R-LATE-PICKUP, R-RETURN-VETO, R-10/LOADGOV, geometria, pozycja-równość, always-propose, kanon-kolejności, SLA-anchor, pre-shift-floor, early-bird, paczka-exempt, P0-feasibility-first, ETA_QUANTILE_R6, FEAS_CARRY_READMIT.
- Flagi A3: COMMIT_DIVERGENCE (inwersja maskująca), ETA_QUANTILE_R6, ALWAYS_PROPOSE, LATE_PICKUP_HARD_GATE, R_RETURN_VETO, FLEET_LOAD_GOVERNOR vs BALANCE (rozstrzygnięty efektywny ON), OR_TOOLS↔GROUPING, POST_SHIFT_OVERRUN, OBJ_COMMITTED_PICKUP_LOAD_THRESHOLD, TRUST_CANON ×3, APP_ROUTE_FROM_CONSOLE.
- Ścieżki I3: feasibility(L5)→scoring(L6)→selekcja(L7)→werdykt(L8)→plan_recheck(B); silnik↔konsola↔apka (route-order + czas committed/floor/OSRM); committed vs OSRM vs floor.
- Pliki otwarte na żywo: `feasibility_v2.py`, `route_simulator_v2.py`, `scoring.py`, `objm_lexr6.py`, `dispatch_pipeline.py`, `plan_recheck.py`, `common.py`, `flags.json`, cross-repo `fleet_state.py`+`courier_orders.py` (grep nagłówków+linii flag).

**NIE zbadane (luki + powód):**
1. **PEŁNE ciała cross-repo `_build_route`/`build_view`** (tylko nagłówki+linie flag grepem) — magnituda rozjazdu route-order = oracle Fazy C (`ziomek_time_route_monitor` świeża liczba NIE sparsowana, read-only).
2. **`czasowka_scheduler` precedencja czasówka(≥60) vs early-bird(≥60)** — A2 oznacza early-bird PRZEKWALIFIKOWANE (redundancja z czasowka_scheduler, lekcja #196) = **redundancja, nie konflikt** → nie liczę jako parę; pełna analiza czasówka-hold poza zakresem D02.
2b. **parcel lane precedencja** (`parcel_lane_merge`/`parcel_assign`) — czy paczka używa `_selection_bucket`/`order_podjazdy` czy własnej ścieżki (A6 luka #2) → nie prześwietlone (natywny tor orders_state).
3. **auto_assign_gate G1-G14 wzajemna precedencja** (`evaluate_auto_assign:89`) — LATENTNE (`ENABLE_AUTO_ASSIGN` OFF); G7 ujęte w C16, pełna macierz G-bramek = osobny agent (AUTON).
4. **Liczbowe rozstrzygnięcie „czy C2 materialny dziś"** (ile worków ma czas_late>0 z rozjazdem anchora) — to oracle Fazy C/E, NIE A/D read-only.
5. **P-1..P-7 mapowanie** — protokół referuje świadome inwersje P-1..P-7 (ziomek-full-rule-audit-2026-06-24) których NIE czytałem; oznaczyłem „cicha-inwersja-P" tam gdzie flip cicho odwraca (C4/C7), ale dokładny numer P-n nie zmapowany (brak dostępu do tej listy w seedach D02).
6. **courier-app Kotlin** lokalny re-sort/ETA — GRANICA cross-repo, render serwerowy (pokryty przez courier_api); lokalna kopia bundlingu (RouteLogic.kt) odnotowana w A5 C.3, nie pogłębiona.

**NIE-luki (świadomie poza zakresem):** Mailek/Papu (STOP na dyspozytorni). Przyrządy-prawda (A4/Faza C). Sentinele jako klasa M (osobny agent — tu tylko most K5 w C16/C19).

---

## 5. HANDOFF

- **Faza E (dedup):** wszystkie 20 par zwijają się do **5 distinct otwartych rozjazdów** (frozen lex_qual / out-of-engine gates / route-order cross-repo / SLA≠R6 anchor / floor-17) + 5 PRECEDENCJA-specyficznych NIE-double-countowanych z A6: **commit-divergence-masking (C7), FEAS_CARRY-post-guard (C4), always-propose-asymetria (C8), dwie-reguły-load (C9/C10), nazwa-vs-zachowanie (C12 VETO / C13 HARD_GATE)**. Te 5 to NOWE znaleziska osi I (poprzednie audyty miały tylko kopie/warstwę).
- **Faza F (kontrakty):** 3 braki kontraktu precedencji (§3) = wejście do PoC. Dodatkowo: ujednolicić słownictwo HARD_GATE/VETO (C12/C13), domknąć inwersje maskujące (C7 → przenieść const env-default na False żeby usunięcie klucza nie flipowało; C4 → guard świadom readmit).
- **Faza C/D (oracle/flagi):** zweryfikować C9 efektywny FLEET_LOAD_GOVERNOR (A2-vs-A3-vs-kod rozjazd = klasa D do domknięcia w rejestrze flag); odpalić `ziomek_time_route_monitor` (C17 liczba); potwierdzić materialność C2/C15 replayem.
