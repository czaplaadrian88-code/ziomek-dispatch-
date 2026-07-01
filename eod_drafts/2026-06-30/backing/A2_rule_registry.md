# OS 2 — REJESTR REGUŁ BIZNESOWYCH × WARSTWA EGZEKUCJI (Faza A / A2-rule-registry)

**Sesja tmux 2, READ-ONLY.** Numery linii ze ŚWIEŻEGO grepu 2026-06-30 (HEAD `8024705`, working tree silnika czysty). Linie DRYFUJĄ (≥3 żywe sesje na wspólnym repo) — każdy konsument re-grepuje.

**Co to jest:** dla KAŻDEJ reguły: nazwa | co mówi | WARSTWA (HARD/SOFT/SELEKCJA/KANON/DISPLAY) | plik:func który egzekwuje | RUNTIME-INWARIANT/strażnik (tak/nie) | klasa anty-wzorca (A1 N-kopii / B asymetria-bliźniaków / C SOFT-zamiast-HARD / I konflikt-precedencji / K martwy / L słownictwo / N rozsyp-progów / M sentinel). To OS dla Fazy D (graf konfliktów) i B (sweep bliźniaków).

**10 WARSTW (kotwica):** wejście → geokod(HARD) → early-bird(HARD) → telemetria → `check_feasibility_v2`(HARD) → scoring+~19 kar(SOFT) → selekcja(SOFT) → werdykt KOORD(HARD) → zapis+kanon → konsola/apka/Telegram.

**Pliki/rozmiary (ref dryfu):** common.py 3696L · feasibility_v2.py 1311L · dispatch_pipeline.py 7028L · scoring.py 288L · route_simulator_v2.py 1490L · plan_recheck.py 2108L · tsp_solver.py 525L.

---

## TABELA ZBIORCZA (skrót — szczegóły per-reguła niżej)

| # | Reguła | Co mówi | WARSTWA | Egzekutor (plik:linia/func) | Inwariant? | Klasa anty-wzorca |
|---|---|---|---|---|---|---|
| R1 | deliv-spread ≤ 8km | rozrzut dostaw w worku | **SOFT** (był HARD, zsoftowany) | `feasibility_v2.py:504` (metric `R1_MAX_DELIV_SPREAD_KM=8.0`) + kara `dispatch_pipeline.py:4624` `bonus_r1_soft_pen` | nie | A1 (metric+kara+R1_PROGRESSIVE_CLIP) / C |
| R2 | ghost-detection / corridor | duch dostarczony, korytarz 2.5km | SOFT/metric | `common.py:1218` (V3.20 packs reverse) | nie | — |
| R3 | dynamic bag cap | cap worka f(spread) | **SOFT** (zsoftowany) | `feasibility_v2.py:188` `_dynamic_bag_cap` (metric only) | nie | C |
| R4 | free-stop / po-drodze credit | premia za „po drodze" | SOFT (bonus) | `dispatch_pipeline.py:4140` `bonus_r4` | nie | — |
| R5 | pickup-spread ≤ 2.5km | rozrzut odbiorów mixed-rest | **SOFT/metric** | `feasibility_v2.py:573` (`R5_MAX_MIXED_PICKUP_SPREAD_KM=2.5`) + `ENABLE_R5_PICKUP_DETOUR_PENALTY`(OFF) `common.py:2766` | nie | C / D(flaga OFF) |
| **R6** | **BAG_TIME ≤ 35 min** | termik gotowość→dowóz | **HARD** (+SOFT-zona +SELEKCJA-tier) | `feasibility_v2.py:1105/1212` reject `R6_per_order_>35min` (`BAG_TIME_HARD_MAX_MIN=35` common.py:763) | **TAK** (INV-R6-ANCHOR-CONSISTENCY `r6_thermal_anchor`) | **A1+N** (płaski 35 vs tier-40; 3-bliźniaki SLA-anchor) |
| R7 | long-haul peak isolation | >X km w peaku reject | HARD-kształt, **MARTWY** | `feasibility_v2.py:486` (`LONG_HAUL_DISTANCE_KM=99.0` ⇒ nigdy) | nie | **K** (zneutralizowany stałą) |
| R8 | pickup-span czasowy | rozrzut T_KUR w worku | **SOFT** | `feasibility_v2.py:626` (`PICKUP_SPAN_HARD_*`=próg kary nie bramka) | nie | C/L (nazwa „HARD"=próg kary) |
| R9 | stopover + wait penalty | postój/idle kuriera | **SOFT** (+HARD-reject tail >20min) | `scoring.py:61/110` `compute_wait*`; `bonus_r9_*` `dispatch_pipeline.py:1718-1720` | nie | I (HARD tail w SOFT warstwie) |
| R-DECLARED-TIME | `czas_kuriera ≥ czas_odbioru` | deklaracja nietykalna | **deklarowana HARD — BRAK runtime-bramki** | egzekwowane POŚREDNIO: R27 frozen + `pickup_ready_at=czas_kuriera` `dispatch_pipeline.py:3486` | **NIE** | **I/C** (HARD w docach, brak inwariantu) |
| R-35MIN-MAX | = R6 | — | HARD | (patrz R6) | TAK | — |
| **R27 ±5** | committed pickup window | odbiór ±5 od `czas_kuriera` | **SOFT** (eskalacja, NIE INFEASIBLE) | `route_simulator_v2.py:1071` `ENABLE_V3274_FROZEN_PICKUP_WINDOW`; `tsp_solver.py:263` `SetCumulVarSoftUpperBound` | **TAK** (post-solve `V3274_OR_TOOLS_VIOLATION_CHECK` rs_v2:1479) | A1 (frozen w silniku + apka `ENABLE_FROZEN_PICKUP_ETA` + konsola `PIN_AGREED_PICKUP_TIME`) |
| geometria-rozjazdu | spread/cross-quadrant/wave-veto | kierunkowa niespójność | **SOFT-ONLY** (kanoniczny C1) | `dispatch_pipeline.py:4624` `R1_spread_per_km=-8`; `:5239` cross-quad mult 0.1/0.7/1.0; `:4826` `V326_WAVE_VETO_KM_THRESHOLD=3.0`→bonus | nie | **C1** (decyzja tylko jako SOFT-kara, nigdy HARD/klucz-selekcji) |
| pozycja-równość | no_gps/pre_shift = równe, ZERO kary | brak GPS nie karze | **SELEKCJA** | `dispatch_pipeline.py:2451` `_selection_bucket` (`ENABLE_NO_GPS_EQUAL_TREATMENT`+`_EQUAL_TREATMENT_BUCKET`+`PRE_SHIFT_EQUAL_NO_PENALTY`) | nie | **A1/B — 8 BLIŹNIAKÓW** (patrz niżej) |
| always-propose | NIGDY „BRAK KANDYDATÓW" | zawsze coś zaproponuj | **SELEKCJA/werdykt** | `dispatch_pipeline.py:595` `_best_effort_fastest_pickup_key`; `:633` `_best_effort_objm_pick`; `:2638` `_always_propose_on` | nie | A1 (`lex_qual` ×3) |
| kanon-kolejności | kolejność jazdy z kanonu | carried-first/no-return/sequence | **KANON** | `plan_recheck.py:1478` `_apply_canon_order_invariants`; `:1003` `_relax_carried_first` | TAK (`SEQUENCE_LOCK`+`CANON_ORDER_INVARIANTS` env-frozen) | **A1/B/C7 — 3-4 kopie cross-repo** |
| SLA-anchor | ready-anchor dla SLA/R6 | kotwica od gotowości | HARD+KANON | `route_simulator_v2.py:635` `_count_sla_violations`; `feasibility_v2.py:~1156` SLA-loop; `plan_recheck.py:683` `_o2_key` | TAK częściowo | **A1 — 3 bliźniaki ROZJECHANE** (feas wciąż pickup_at) |
| pre-shift floor | pickup ≥ shift_start | nie odbieraj przed startem | HARD (feasibility) — **BRAK w plan_recheck** | `feasibility_v2.py:794` `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP`; reject `:751` `V325_PRE_SHIFT_HARD_REJECT_MIN=30` | nie | **B/H** (gate w fazie A, brak w regenie) |
| early-bird ≥60min | KOORD gdy odbiór >60min naprzód | wstrzymaj wczesne | **HARD werdykt** | `dispatch_pipeline.py:2590` `EARLY_BIRD_THRESHOLD_MIN=60`; `:2598` `_early_bird_threshold_min` | nie | — |
| czasówka ≥60min | `order_type=='czasowka'⟺prep≥60` | twarda deklaracja restauracji | KANON/wejście | `common.py:3500` `order_dict.get('order_type')!='czasowka'`; `EARLY_BIRD_THRESHOLD_MIN` | nie | (OBALONE „3 definicje" — patrz audyt) |
| R-LATE-PICKUP | max 5min spóźnienia odbioru | committed→tier, nowy→przedłużenie | **SELEKCJA tier** (NIE hard-reject) | `dispatch_pipeline.py:4557-4616` `ENABLE_LATE_PICKUP_HARD_GATE`(ON); `telegram_approver.py:1393` propozycja | nie | **L/I** (nazwa „HARD_GATE", zachowanie SELEKCJA) |
| R-NO-WASTE | nie marnuj fali/po-drodze | gradient BUG-2 magnitude | SOFT gradient | `common.py:1681` `BUG2_WAVE_CONTINUATION_BONUS=30`; `:1761` | nie | — |
| R-FLEET-LEVEL (R-10) | równoważ flotę | bonus/kara od delty load | SOFT | `dispatch_pipeline.py:1447` `_v326_fleet_load_balance` (`ENABLE_V326_FLEET_LOAD_BALANCE`=ON) | nie | A1 vs LOADGOV (2 reguły load) |
| R-SCHEDULE-AWARE (V325) | sprawdź grafik | brak shift→reject | **HARD** | `feasibility_v2.py:661` `ENABLE_V325_SCHEDULE_HARDENING`; reject `:722` `v325_NO_ACTIVE_SHIFT` | TAK (FAIL12 failopen `:686`) | — |
| fleet-load-governor | governor floty (SP-B2) | cap/relaks pod obciążeniem | SOFT (flaga OFF) | `common.py:2103` `ENABLE_FLEET_LOAD_GOVERNOR`(OFF); telemetria `loadgov_*` | nie | K/D (flaga OFF, telemetry-only) |
| R-RETURN-TO-RESTAURANT-VETO | nie wracaj do restauracji z jedzeniem | zakaz powrotu | **split: feas=metric-only / kanon=zakaz** | `feasibility_v2.py:905` `detect_return_to_restaurant` (NIE przerywa feas!); kanon `ENABLE_NO_RETURN_TO_DEPARTED_PICKUP` | nie | **I/B** (nazwa VETO, feas nie vetuje) |
| paczka R6 thermal exempt | paczka nie podlega 35min | termik tylko jedzenie | **HARD exempt** | `feasibility_v2.py:1050` (R6 termik) + `:1152` (SLA bramka) `ENABLE_PACZKA_R6_THERMAL_EXEMPT` | nie | **A1 — 3 HARD-site + BRAK w O2** (4. site flip 02.07) |
| hard tier bag cap | cap worka per tier | gold6/std5/slow4/new4 | HARD (flaga OFF) | `feasibility_v2.py:464` `ENABLE_HARD_TIER_BAG_CAP`(OFF); `HARD_TIER_BAG_CAP` common.py:1326 | nie | D (flaga OFF, `would_hard_cap` shadow) |
| commit-divergence verdict gate | plan≠commit >10min → KOORD | rozjazd plan-commit | HARD werdykt (flaga OFF) | `dispatch_pipeline.py:6523` `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE`(OFF) | nie | D |
| difficult-case KOORD redirect | wszyscy <floor → KOORD | trudny case | HARD werdykt (flaga OFF) | `dispatch_pipeline.py:6609` `ENABLE_DIFFICULT_CASE_KOORD_REDIRECT`(OFF) | nie | D |
| P0 HARD-przed-SOFT | żaden NO w puli selekcji | feasibility przed scoringiem | **META-INWARIANT** | `dispatch_pipeline.py:2480` `_assert_feasibility_first` (wołany `:5938`) | **TAK** (fail-loud `INV_FEASIBILITY_FIRST_VIOLATION`) | — (strażnik, nie reguła) |

---

## SZCZEGÓŁY PER-REGUŁA

### R1 — delivery spread ≤ 8 km  | WARSTWA: SOFT (był HARD)
- **Co mówi:** rozrzut punktów dostawy w worku ≤ 8km (p90 czystej próbki Bartka).
- **Egzekutor:** `feasibility_v2.py:504` `if spread_km > R1_MAX_DELIV_SPREAD_KM` (`R1_MAX_DELIV_SPREAD_KM=8.0` feasibility_v2.py:90) — **TYLKO metryka** `r1_violation_km` (komentarz `:494`: „R1 spread outlier — SOFT, NIE hard block, zweryfikowane audytem 2026-05-21"). Kara: `dispatch_pipeline.py:4624` `bonus_r1_soft_pen = _r1_viol * R1_spread_per_km(-8.0)`.
- **Inwariant/strażnik:** nie.
- **Klasa:** **A1** (reguła w 2 miejscach: feasibility-metric + scoring-kara) + osobna flaga `ENABLE_R1_PROGRESSIVE_CLIP` (common.py:63) = 3. wariant. **C** (zsoftowany HARD → twarda granica deleguje do R6+SLA).

### R3 — dynamic bag cap  | WARSTWA: SOFT
- `feasibility_v2.py:188` `_dynamic_bag_cap(spread_km)` — komentarz `:498`: „R3 dynamic cap również zsoftowany". Liczony do metryki, NIE bramkuje. **Klasa C** (HARD→SOFT). Twarda granica worka = R6 + SLA + (opcjonalnie `ENABLE_HARD_TIER_BAG_CAP` OFF) + sanity cap `_bag_sanity_cap` (feasibility_v2.py:98).

### R5 — mixed-restaurant pickup spread ≤ 2.5 km  | WARSTWA: SOFT/metric
- `feasibility_v2.py:573` `if pickup_spread_km > R5_MAX_MIXED_PICKUP_SPREAD_KM` (`=2.5`, poluzowane z 1.8 p100 Bartka). TYLKO metryka `r5_violation_km` — komentarz `:579` jawnie: „Galeria Biała JEST PO DRODZE" więc spread nie bramkuje. Kara opcjonalna `ENABLE_R5_PICKUP_DETOUR_PENALTY` (common.py:2766 = **OFF**), `R5_DETOUR_PENALTY_PER_KM`/`R5_DETOUR_FREE_THRESHOLD_KM`.
- **Klasa:** C (SOFT, brak HARD) + D (flaga kary OFF).

### R6 — BAG_TIME ≤ 35 min (termik)  | WARSTWA: HARD (+SOFT-zona +SELEKCJA-tier)  ★ NAJWAŻNIEJSZA, NAJWIĘCEJ KLAS
- **Co mówi:** czas od gotowości jedzenia (T_KUR) do dowozu ≤ 35 min. „Jedzeniówka rządzi" (feasibility_v2.py:1015).
- **HARD-egzekutor:** `feasibility_v2.py:1105` `if _gate_bt > C.BAG_TIME_HARD_MAX_MIN and not _paczki_only_mix and not _o_paczka_exempt` → tracking; reject `feasibility_v2.py:1212` `return ("NO", f"R6_per_order_>35min ...")`. `BAG_TIME_HARD_MAX_MIN=35` (common.py:763). Picked-up delta reject: `feasibility_v2.py:~1245` `R6_picked_up_delta_>35min`.
- **READY-ANCHOR (kotwica termiczna):** `route_simulator_v2.py:663` `r6_thermal_anchor(order, is_new, plan_pickup_at, now)` → nowe/nieodebrane od `pickup_ready_at` (jedzenie czeka OD GOTOWOŚCI), picked-up od `picked_up_at`. **Inwariant: INV-R6-ANCHOR-CONSISTENCY** (route_simulator import feasibility_v2.py:33) = **TAK**.
- **TIER-AWARE (NIE płaski 35!):** Tier-3 cap-stretch = **40** (`BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN=40` common.py:2651) na ścieżce best_effort/objm. Bag-cap też tier (`HARD_TIER_BAG_CAP` gold6/std5/slow4/new4, common.py:1326, flaga OFF).
- **SOFT-zona (30,35]:** `feasibility_v2.py:1128` `r6_soft_penalty_c3_legacy = -3*(bt-30)` — **MARTWA** (patrz smell). ŻYWA SOFT-kara R6 = `dispatch_pipeline.py:1716` `bonus_r6_soft_pen` (-8/min, `BAG_TIME_SOFT_PENALTY_PER_MIN`).
- **Klasa:** **A1** (BAG_TIME_HARD_MAX_MIN=35 płaski w feasibility ↔ tier-40 w best_effort/objm ↔ `bundle_calib_shadow.py` `overage=max(0,age-35)` PŁASKO over-penalizuje T3) → też **N (rozsyp progów)**. SLA-anchor = **3 bliźniaki** (niżej).

### R7 — long-haul isolation w peak  | WARSTWA: HARD-kształt, MARTWY
- `feasibility_v2.py:486` `if bag and r7_ride_km > LONG_HAUL_DISTANCE_KM and r7_in_peak → return NO`. ALE `LONG_HAUL_DISTANCE_KM=99.0` (common.py:800, komentarz: „R7 wyłączone — 4.5km było za agresywne dla Białegostoku"). **Bramka istnieje, nigdy nie odpala.**
- **Klasa:** **K** (reguła zneutralizowana stałą, kod-zombie myli czytającego, że R7 żyje). TODO C3 (`feasibility_v2.py:471`) refactor-do-soft nigdy nie zrobiony.

### R8 — pickup span czasowy  | WARSTWA: SOFT
- `feasibility_v2.py:626` — `PICKUP_SPAN_HARD_BUNDLE2_MIN=15`/`BUNDLE3=30` (common.py:807) to **próg KARY, nie bramka feasibility** (komentarz `:628`). Kara `SOFT_PENALTY_PER_MIN=3`. **Klasa L** (nazwa stałej `*_HARD_*` myli — to SOFT) + C.

### R9 — stopover tax + wait penalty  | WARSTWA: SOFT (+HARD-reject tail)
- `scoring.py:61` `compute_wait_penalty` (kwadratowa tabela, sweet ≤20→0); `scoring.py:110` `compute_wait_courier_penalty` (V3.27.3, gradient od 6min). **Docstring `scoring.py:129`: „>20 min → HARD REJECT"** (`V3273_WAIT_COURIER_HARD_REJECT_MIN=20.0`). Kary: `dispatch_pipeline.py:1718` `bonus_r9_stopover`, `:1719` `bonus_r9_wait_pen`, `:1720` `bonus_v3273_wait_courier`.
- **Klasa I:** HARD-reject (>20min) zaszyty w warstwie SOFT-scoring zamiast w `check_feasibility_v2` — HARD logika w złej warstwie (weryfikacja Faza B: czy `reject=True` realnie wraca — body `scoring.py:153-164` które widziałem zwracało `(penalty, False)`; ⚠ do potwierdzenia oracle czy tail żyje).

### R-DECLARED-TIME — `czas_kuriera ≥ czas_odbioru_timestamp`  | WARSTWA: deklarowana HARD, BRAK runtime-bramki  ★ KONFLIKT dla Fazy D
- **Co mówi (doc REGULY_BIZNESOWE):** „(HARD) — `czas_kuriera ≥ czas_odbioru_timestamp` zawsze".
- **Egzekutor:** **BRAK dedykowanej bramki HARD w silniku.** Egzekwowane POŚREDNIO: (a) `pickup_ready_at=czas_kuriera` (`dispatch_pipeline.py:3486`), (b) R27 frozen window (committed nietykalny), (c) czasówka `order_type` (common.py:3494 „Czasówka NIE jest flex — R-DECLARED-TIME nadrzędne"). Wszystkie wzmianki R-DECLARED to KOMENTARZE (common.py:3410/3414/3494, dispatch_pipeline.py:3168), nie egzekucja.
- **Inwariant:** **NIE.**
- **Klasa:** **I/C** — reguła deklarowana HARD w docach, ale ŻADNA warstwa nie sprawdza `czas_kuriera ≥ czas_odbioru` jako twardej bramki/inwariantu. Kandydat #1 dla Fazy D (HARD bez runtime). Faza B: zweryfikować czy panel/`state_machine` clampuje przy zapisie.

### R27 ±5 — committed pickup window  | WARSTWA: SOFT (eskalacja, NIE INFEASIBLE)
- **Co mówi:** odbiór w oknie [czas_kuriera−5, czas_kuriera+5]; committed `czas_kuriera` po przypisaniu NIETYKALNY.
- **Egzekutor:** `route_simulator_v2.py:1071` `ENABLE_V3274_FROZEN_PICKUP_WINDOW` → `window_open = max(0, open−V3274_FROZEN_PICKUP_WINDOW_MIN(5))`; `tsp_solver.py:263/288/311` `SetCumulVarSoftUpperBound` (komentarz common.py:2547: „SOFT — NIGDY INFEASIBLE; sztywne ±5 = 7500 INFEASIBLE/d"). Nietykalność czasu: apka `ENABLE_FROZEN_PICKUP_ETA`, konsola `PANEL_FLAG_PIN_AGREED_PICKUP_TIME` (cross-repo).
- **Inwariant/strażnik:** **TAK** — post-solve assertion `route_simulator_v2.py:1479` `V3274_OR_TOOLS_VIOLATION` (dwustopniowy: TOLERANCE 0.5min / VIOLATION), fallback `_greedy_plan`.
- **Klasa:** A1 (frozen-window egzekwowany w 3 powierzchniach: silnik TSP + apka + konsola, bez wspólnego importu = **J cross-repo**).

### geometria-rozjazdu (spread km / cross-quadrant / wave-veto)  | WARSTWA: SOFT-ONLY  ★ KANONICZNY C1
- **Co mówi:** kierunkowa niespójność worka (rozjazd, przeciwne kierunki, cross-quadrant) ma być karana.
- **Egzekutor (wszystko SOFT):** `dispatch_pipeline.py:76` `R1_spread_per_km=-8.0` → `:4624` `bonus_r1_soft_pen`; `:4635` opposite-direction (`cos<-0.5` karać mocno); `:5239` cross-quadrant `score *= 0.1` (mult); `:4826` `V326_WAVE_VETO_KM_THRESHOLD=3.0` (common.py:2219) → `v326_wave_geometric_km` bonus-veto; `ENABLE_V326_WAVE_VETO_NEW_DROP` (OFF) os nowej dostawy.
- **Inwariant:** nie.
- **Klasa:** **C1 (kanoniczny przykład z taksonomii)** — logika decyzyjna geometrii ŻYJE WYŁĄCZNIE jako SOFT-kara w `score`, NIGDY w HARD-bramce `check_feasibility_v2` ani w kluczu selekcji. To dokładnie wzorzec, który Faza F ma adresować. Faza B: sweep wszystkich miejsc geometrii (≥4 SOFT terms).

### pozycja-równość (no_gps / pre_shift = równe, ZERO kary)  | WARSTWA: SELEKCJA  ★ A1 8-BLIŹNIAKÓW (najgęstsza klasa)
- **Co mówi (Adrian 29.06 HARD-zasada):** kurier bez GPS / przed zmianą liczony RÓWNO; ZERO kary score/feasibility/ranking. „Dotrze później" obsługuje LEGALNA ścieżka (clamp + R-LATE-PICKUP), nie ukryta kara.
- **Egzekutor (kanon):** `dispatch_pipeline.py:2451` `_selection_bucket` (informed 0 / no_gps,pre_shift→0 gdy `ENABLE_EQUAL_TREATMENT_BUCKET`/`ENABLE_NO_GPS_EQUAL_TREATMENT` ON / blind→2). F1.7 score-neutral: `dispatch_pipeline.py:5838` (km=śr.floty, ETA=max15,prep).
- **8 BLIŹNIAKÓW (z protokołu, re-grep 30.06):**
  1. F1.7 score-neutral `dispatch_pipeline.py:5838`
  2. `_selection_bucket` `:2451` (wpięty w `_late_pickup_score_first_key:546`/`_best_effort_sort_key:583`/objm `bucket_fn=_selection_bucket:1378`)
  3. `_demote_blind_empty:2504` / `_is_demotable_blind_empty:2467`
  4. **`_best_effort_fastest_pickup_key:595` = HARDCODED bucket informed0/blind2 BEZ flagi** (mina gdy awansowany z shadow) — **klasa B (asymetria)**
  5. `drive_min_calibration` OFFSET no_gps+6,5/pre_shift+15,3 (MAIN OFF, artefakt)
  6. `auto_assign_gate.py` G7 `pos_not_informed` (LATENTNE, `ENABLE_AUTO_ASSIGN` OFF)
  7. `tools/reassignment_forward_shadow.py` `_SYNTH_POS`/`a_late` (duch przerzutu konsoli, 59% fałszywych ratunków — **cross-repo J**)
  8. `feed.py` konsola quality_reassign bez filtra pewnej pozycji (cross-repo)
- **Flagi żywe:** `ENABLE_NO_GPS_EQUAL_TREATMENT`+`ENABLE_EQUAL_TREATMENT_BUCKET`+`ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY` (`dispatch_pipeline.py:2393/2407/2430`).
- **Inwariant:** **NIE** (brak runtime-strażnika równości — łatane ≥4×, wraca).
- **Klasa:** **A1** (reguła w 8 kopiach) + **B** (fix scalony w `_selection_bucket`, bliźniaki 4-8 zostają) + **M** (sentinel BIALYSTOK_CENTER `_BIALYSTOK_CENTER_FALLBACK=(53.1325,23.1688)` dispatch_pipeline.py:132 jako pozycja). NAJGĘSTSZA klasa A1 w silniku.

### always-propose (NIGDY „BRAK KANDYDATÓW")  | WARSTWA: SELEKCJA/werdykt
- **Co mówi:** feasible-first → najbliższy łamie R6 → best_effort (najszybszy wolny, może łamać R6, tag `auto_route=ALERT`/`best_effort`, score sentinel ≈−1e9) → KOORD TYLKO early-bird/czasówka ≥60min/pusta pula.
- **Egzekutor:** `dispatch_pipeline.py:595` `_best_effort_fastest_pickup_key`; `:633` `_best_effort_objm_pick` (cap_min=40, „JEDNO ŹRÓDŁO objm"); `:564` `_best_effort_sort_key`; `:2638` `_always_propose_on` (`ENABLE_ALWAYS_PROPOSE_ON_SATURATION` default False, ale best_effort fallback zawsze obecny); werdykt KOORD `:6491/6864/6900/6926` gdy `not _always_propose_on()`.
- **Inwariant:** nie.
- **Klasa:** **A1** — `lex_qual` w 3 kopiach (protokół: `_best_effort_objm_pick` 4-krotka / `objm_lexr6.lex_qual` warunkowo 4/3 / `_objm_lexr6_shadow` 3-krotka zamrożona). „Sentinel best-effort w konsoli = POPRAWNE, nie bug" (OBALONE jako problem).

### kanon-kolejności (route order canon)  | WARSTWA: KANON  ★ A1 3-4 KOPIE cross-repo
- **Co mówi:** kolejność JAZDY z kanonu (carried-first relax / no-return-to-departed-pickup / sequence-lock / odbierz-po-drodze).
- **Egzekutor (silnik):** `plan_recheck.py:1478` `_apply_canon_order_invariants(stops, orders_state, pos, now)`; `:1003` `_relax_carried_first`; `:1526` wołanie relax. Flagi env-frozen drop-in `dispatch-plan-recheck.service.d`: `ENABLE_PLAN_CANON_ORDER_INVARIANTS` (plan_recheck.py:368), `ENABLE_PLAN_SEQUENCE_LOCK`, `ENABLE_CARRIED_FIRST_RELAX`, `ENABLE_NO_RETURN_TO_DEPARTED_PICKUP`.
- **3-4 KOPIE (C7, cross-repo):** silnik `plan_recheck._apply_canon_order_invariants` ↔ konsola `nadajesz_clone/panel/fleet_state._order_from_plan_seq`/`_build_route` ↔ apka `courier-app route_podjazdy.order_podjazdy` ↔ `courier_api/courier_orders.py`. **Asymetria bliźniaków (B):** carried-first-relax dostała konsola (TRUST_CANON), apka NIE (force carried-first) → 44-75 rozjazdów/d, monitor `ziomek_time_route_monitor.jsonl`.
- **Inwariant:** TAK (sequence-lock + canon-invariants jako env-frozen ON) — ale BEZ wspólnego importu cross-repo (parytet tylko przez monitor).
- **Klasa:** **A1** (3-4 kopie) + **B** (relax w 1 z 2 powierzchni) + **J** (cross-repo bez wspólnego źródła) + **H** (recanon `retime-only`, nie potrafi prune — kurczenie worka wymaga `remove_stops` PRZED recanon).

### SLA-anchor (ready-anchor dla SLA/R6)  | WARSTWA: HARD + KANON  ★ A1 3 BLIŹNIAKI ROZJECHANE
- **Co mówi:** SLA/R6 kotwiczone od `pickup_ready_at` (gotowość), spójnie z `r6_thermal_anchor`.
- **3 BLIŹNIAKI (z protokołu, re-grep):**
  1. `route_simulator_v2.py:635` `_count_sla_violations` + `:663` `r6_thermal_anchor` (READY-anchored ✅)
  2. `feasibility_v2.py:~1156` SLA-loop — **WCIĄŻ kotwiczy na `pickup_at`** (znana luka, komentarz `feasibility_v2.py` „przedmiot sprintu O2 review 02.07")
  3. `plan_recheck.py:683` `_o2_key` (`sla_violations, total_duration_min`)
- **Sprzężenia (Faza D):** `ENABLE_ETA_QUANTILE_R6_BAGCAP` (gold≤4 p80 — JEDYNE >35 ready-anchored przechodzi) + `ENABLE_PACZKA_R6_THERMAL_EXEMPT` (3 HARD-site, brak w O2).
- **Inwariant:** TAK częściowo (INV-R6-ANCHOR tylko route_simulator).
- **Klasa:** **A1** (3 kopie, 1 rozjechana — feas na pickup_at) + N (kotwica niespójna między bliźniakami).

### pre-shift floor (pickup ≥ shift_start)  | WARSTWA: HARD (feasibility), BRAK w plan_recheck
- **Co mówi:** odbiór nie wcześniej niż start zmiany; pre_shift dostaje `earliest_departure=shift_start`.
- **Egzekutor:** `feasibility_v2.py:794` `if ENABLE_PRE_SHIFT_DEPARTURE_CLAMP and pos_source in (pre_shift,no_gps) and shift_start>now → earliest_departure=shift_start`; HARD-reject >30min przed startem `feasibility_v2.py:751` (`V325_PRE_SHIFT_HARD_REJECT_MIN=30` common.py:1972); warm-up soft `−20` `:762` (`V325_PRE_SHIFT_SOFT_PENALTY` — REDUNDANTNY z clampem, do zdjęcia per protokół).
- **LUKA (K2):** `plan_recheck` regen co 5min NIE re-aplikuje pre-shift floor → „leak odclampowuje co 5min" (preshift-pickup-floor-audit). Konsola: osobny `CLAMP_PRESHIFT_PICKUP_ETA` (render).
- **Inwariant:** nie.
- **Klasa:** **B/H** (gate w fazie A feasibility, brak w fazie B plan_recheck) — bliźniak feasibility↔plan_recheck rozjechany. 17 miejsc liczy czas odbioru, tylko 4 mają floor (audyt 30.06).

### early-bird ≥ 60min  | WARSTWA: HARD werdykt
- **Co mówi:** odbiór >60min naprzód → KOORD (wstrzymaj, nie proponuj kuriera teraz).
- **Egzekutor:** `dispatch_pipeline.py:2590` `EARLY_BIRD_THRESHOLD_MIN=60`; `:2598` `_early_bird_threshold_min()` (flags.json hot `EARLY_BIRD_THRESHOLD_MIN`); short-circuit KOORD PRZED budową puli (`:2604` komentarz). Shadow kontrfaktyk: `_bypass_early_bird` (`:3294`) → `earlybird_shadow.jsonl`.
- **Inwariant:** nie. **Klasa:** — (czysta). Uwaga L: „early-bird" PRZEKWALIFIKOWANE 29.06 (≥60min=czasówka=hold, redundancja z czasowka_scheduler — lekcja #196).

### czasówka ≥ 60min (`order_type=='czasowka' ⟺ prep≥60`)  | WARSTWA: KANON/wejście
- `common.py:3500` `return order_dict.get("order_type") != "czasowka"` (is-flex gate); `common.py:3494` „Czasówka NIE jest flex — R-DECLARED-TIME nadrzędne". Czasówka trzymana w Koordynator id_kurier=26. **OBALONE** w audycie: „czasówka 3 definicje" — u źródła `order_type=='czasowka'⟺prep≥60`, jedno źródło.

### R-LATE-PICKUP (max 5min spóźnienia odbioru)  | WARSTWA: SELEKCJA tier (NIE hard-reject)  ★ L/I słownictwo
- **Co mówi:** 2 osobne pomiary: COMMITTED bag-order (zadeklarowany czas_kuriera) spóźnienie >5 = złamana obietnica → demote do najniższego tieru; NOWY order >5 → NIE wyklucza, sygnał „przedłużyć czas odbioru".
- **Egzekutor:** `dispatch_pipeline.py:4557-4616` (`_LP_LIMIT=LATE_PICKUP_HARD_MAX_MIN=5.0` common.py:2824); `:4615` `if ENABLE_LATE_PICKUP_HARD_GATE: late_pickup_committed_breach = committed_max>5`; tier `:496` `_late_pickup_tier`; propozycja restauracji `telegram_approver.py:1393` „⏰ Proponowany czas odbioru HH:MM". Komentarz `:4567`: „Selekcja = tiering (NIE hard-reject) → ZAWSZE jest propozycja".
- **Inwariant:** nie.
- **Klasa:** **L** (nazwa flagi/stałej `ENABLE_LATE_PICKUP_HARD_GATE`/`LATE_PICKUP_HARD_MAX_MIN` sugeruje HARD-bramkę, a zachowanie = SELEKCJA-tier; myli nowe sesje) + **I** (interakcja z R27/R-DECLARED).

### R-NO-WASTE (gradient)  | WARSTWA: SOFT gradient
- `common.py:1681` `BUG2_WAVE_CONTINUATION_BONUS=30.0`; `:1761` `compute` z interleave gap_min (gradient anticipation); flaga `ENABLE_V319H_BUG2_WAVE_CONTINUATION` (ON). Magnitude per V3.19j-BUG2. **Klasa:** — (gradient zgodny z LESSON-QA-10).

### R-FLEET-LEVEL / R-10 FLEET-LOAD-BALANCE  | WARSTWA: SOFT
- `dispatch_pipeline.py:1447` `_v326_fleet_load_balance` (delta load: `<-threshold`→bonus, `>+threshold`→penalty; `V326_FLEET_LOAD_THRESHOLD=1.0`/`BONUS=15`/`PENALTY=15` common.py:2244). `ENABLE_V326_FLEET_LOAD_BALANCE` = **ON** (common.py:2238). Też `bonus` w scoring overload `scoring.py:240` (`OVERLOAD_PENALTY`).
- **Klasa:** **A1** — DWIE reguły load współistnieją: R-10 FLEET-LOAD-BALANCE (ON) vs SP-B2 `ENABLE_FLEET_LOAD_GOVERNOR` (OFF) — różne mechanizmy tego samego pojęcia „obciążenie floty". Kandydat Fazy D (która wygrywa?).

### R-SCHEDULE-AWARE (V325 SCHEDULE-HARDENING)  | WARSTWA: HARD
- `feasibility_v2.py:661` `if ENABLE_V325_SCHEDULE_HARDENING` (common.py:1968 = ON); reject `:722` `v325_NO_ACTIVE_SHIFT` (fail-CLOSED: brak shift_end → reject); pickup>shift_end → `PICKUP_POST_SHIFT` `:744`; pickup<shift_start−30 → `PRE_SHIFT_TOO_EARLY` `:757`.
- **Inwariant/strażnik:** TAK — FAIL-12 fail-open `feasibility_v2.py:686` `ENABLE_FAIL12_SCHEDULE_FAILOPEN` (grafik padł → shift_end=None ale kurier aktywny → nie reject; R6/SLA/post-shift dalej egzekwowane). `FAIL12_STOREPOS_BLOCKED` `:717`.
- **Klasa:** — (zdrowa HARD z fail-open guardem).

### fleet-load-governor (SP-B2-LOADGOV)  | WARSTWA: SOFT (flaga OFF)
- `common.py:2103` `ENABLE_FLEET_LOAD_GOVERNOR` = **OFF**; polityka cap/relaks za flagą (common.py:2090); telemetria `loadgov_*` serializowana ZAWSZE (`bonus_loadgov_shadow_delta` dispatch_pipeline.py:2303). Wpływ na committed-pickup loosening: `OBJ_COMMITTED_PICKUP_LOAD_THRESHOLD=4.5` (common.py:2556 — loadgov_ewma≥4.5 → tolerancja 5→10min).
- **Klasa:** **K/D** (flaga OFF, kod żywy, telemetry-only) — odróżnić od R-10 (ON). 2 reguły load = A1.

### R-RETURN-TO-RESTAURANT-VETO  | WARSTWA: split (feas=metric-only / kanon=zakaz)  ★ I/B słownictwo+asymetria
- **Co mówi:** kurier nie wraca do restauracji niosąc już jej dowóz (Case B 475698).
- **Egzekutor (feasibility):** `feasibility_v2.py:905` `if ENABLE_R_RETURN_TO_RESTAURANT_VETO → detect_return_to_restaurant(...)` → **TYLKO metryka** `return_to_restaurant_oid`/`return_to_restaurant` (komentarz `:904`: „instrumentacja NIGDY nie przerywa feasibility"). **Mimo nazwy VETO — feasibility NIE vetuje.** Realny zakaz powrotu = kanon `ENABLE_NO_RETURN_TO_DEPARTED_PICKUP` (plan_recheck/apka/konsola, LIVE 22.06).
- **Inwariant:** nie.
- **Klasa:** **I** (nazwa „VETO" = HARD, ścieżka feasibility = SOFT/metric; precedencja niejasna) + **B** (egzekucja rozdzielona: metric w feas, zakaz w kanonie — bliźniaki). Kandydat Fazy D.

### paczka R6 thermal exempt  | WARSTWA: HARD exempt  ★ A1 3-site + BRAK w O2
- **Co mówi:** firmowe paczki (Dr Tusz/tonery, Nadajesz.pl, `PACZKA_ADDRESS_IDS`) NIE są gorącym jedzeniem → wyłączone z R6 35min termik (Adrian 2026-06-15).
- **3 HARD-SITE (z protokołu):** `feasibility_v2.py:1050` (R6 termik exempt: `_o_paczka_exempt` → nie liczony do `r6_max_bag_time`) + `feasibility_v2.py:1152` (SLA bramka: paczka pominięta jako violation) + (3.) plan/`is_paczka_order`. **BRAK w O2** ready-anchor — 4. site dopiero na flipie 02.07.
- **Egzekutor flaga:** `ENABLE_PACZKA_R6_THERMAL_EXEMPT` (`feasibility_v2.py:1051/1152`, common.py:200).
- **Inwariant:** nie.
- **Klasa:** **A1** (3 site + 1 brakujący) + B (asymetria: feas-termik+feas-SLA mają, O2 nie).

### P0 — HARD-przed-SOFT (META-INWARIANT, nie reguła biznesowa)
- `dispatch_pipeline.py:2480` `_assert_feasibility_first(feasible, order_id)` (wołany `:5938`): żaden kandydat `feasibility_verdict=='NO'` NIE w puli selekcji. **FAIL-LOUD** `INV_FEASIBILITY_FIRST_VIOLATION` (log.error + metryka `inv_feasibility_first_violation`), fail-soft (nie crashuje). To strażnik P0 „SOFT nie obejdzie HARD".
- **Inwariant:** **TAK** (jedyny twardy runtime-strażnik warstwy). Uwaga wzorzec #10: mutacje `top[0]` ZA guardem (`FEAS_CARRY_READMIT`) są poza jego zasięgiem.

---

## SYNTEZA: macierz REGUŁA → WARSTWA → POPRAWNOŚĆ (dla Fazy C/D)

**HARD (twarda bramka feasibility):** R6 (✅ z inwariantem), R-SCHEDULE-AWARE/V325 (✅ z fail-open), pre-shift-reject 30min (✅ feas, ✗ plan_recheck), paczka-exempt (✅ 3-site), pickup_too_far (`feasibility_v2.py:652`), bag_full/sanity-cap, hard_tier_bag_cap (flaga OFF), early-bird KOORD, commit-divergence/difficult-case KOORD (flagi OFF).

**SOFT (kara w score, NIE bramkuje):** R1, R3, R5, R8, R9(stopover/wait), geometria-rozjazdu (C1!), R-NO-WASTE, R-FLEET-LEVEL/R-10, loadgov, R27 (soft window), R5-detour (OFF), sync-spread (OFF).

**SELEKCJA (tie-break/bucket, po scoringu):** pozycja-równość (8 bliźniaków!), R-LATE-PICKUP tier, always-propose/best_effort, demote-blind-empty, objm_lexr6.

**KANON (kolejność/plan po decyzji):** kanon-kolejności (3-4 kopie), carried-first-relax, no-return-to-departed, sequence-lock, SLA-anchor `_o2_key`.

**DEKLAROWANA-HARD-bez-runtime:** R-DECLARED-TIME (★ Faza D #1), R-RETURN-VETO (nazwa VETO, feas metric-only).

---

## SMELLS / ANTY-WZORCE zauważone mimochodem (zasila Fazę B/D — plik:linia + klasa)

1. `feasibility_v2.py:1128` — `r6_soft_penalty_c3_legacy` (-3/min) **MARTWY**: trafia tylko gdy `DEPRECATE_LEGACY_HARD_GATES=True` (stała=False, nigdy flipnięta) a live-caller `dispatch_pipeline.py:~2975` i tak NIE przekazuje kwargu. **Klasa K** (martwy kod logujący wartość).
2. `feasibility_v2.py:486` + `common.py:800` — R7 long-haul: `LONG_HAUL_DISTANCE_KM=99.0` ⇒ HARD-reject NIGDY nie odpala. **Klasa K** (reguła zneutralizowana stałą, kod-zombie myli że R7 żyje).
3. `common.py:763` `BAG_TIME_HARD_MAX_MIN=35` PŁASKI vs tier-40 (`BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN` common.py:2651) vs `bundle_calib_shadow.py overage=max(0,age-35)` płasko. **Klasa N** (ta sama reguła R6 liczona płasko 35 / tier-aware 40 / over-penalizuje T3).
4. `dispatch_pipeline.py:595` `_best_effort_fastest_pickup_key` — HARDCODED bucket informed0/blind2 BEZ flagi equal-treatment (reszta selekcji scalona w `_selection_bucket`). **Klasa B** (asymetria bliźniaków; mina gdy awansowany z shadow/log-only).
5. `dispatch_pipeline.py:3486/3168` (komentarze) — R-DECLARED-TIME (`czas_kuriera≥czas_odbioru`) deklarowane HARD w docach, BRAK runtime-bramki/inwariantu w silniku. **Klasa I/C**.
6. `feasibility_v2.py:905` `ENABLE_R_RETURN_TO_RESTAURANT_VETO` — „VETO" w nazwie, ścieżka feasibility metric-only („NIGDY nie przerywa feasibility"); realny zakaz w kanonie. **Klasa I/L** (nazwa-HARD vs zachowanie-SOFT, split enforcement).
7. `feasibility_v2.py:~1156` SLA-loop wciąż kotwiczy na `pickup_at` (nie ready-anchor) mimo `r6_thermal_anchor` w `route_simulator_v2.py:663`. **Klasa A1/N** (3 bliźniaki SLA-anchor rozjechane; O2 gap, review 02.07).
8. `dispatch_pipeline.py:4615` `ENABLE_LATE_PICKUP_HARD_GATE` + `LATE_PICKUP_HARD_MAX_MIN` — nazwa „HARD_GATE" ale zachowanie = SELEKCJA-tier (NIE hard-reject). **Klasa L** (mylące słownictwo → ryzyko że nowa sesja potraktuje jako bramkę).
9. `dispatch_pipeline.py:132` `_BIALYSTOK_CENTER_FALLBACK=(53.1325,23.1688)` + `:232` sentinel (0,0)→centrum jako pozycja decyzyjna no_gps. **Klasa M** (sentinel jako dane wchodzi do scoringu/ETA).
10. `feasibility_v2.py:73` `_end_of_day_salvage` — bramka ratunkowa końca dnia w 3 miejscach (`:731`/`:779`/`:1269`); ścieżka HARD-bypass. **Klasa A1** (jedna reguła salvage, 3 call-site).
11. `scoring.py:129` docstring „>20min → HARD REJECT" (`V3273_WAIT_COURIER_HARD_REJECT_MIN=20`) ale body `compute_wait_courier_penalty` `:153-164` zwraca `(penalty, False)` — **HARD-reject tail w warstwie SOFT, do potwierdzenia oracle czy żyje**. **Klasa I/K**.
12. `plan_recheck.py:368` `ENABLE_PLAN_CANON_ORDER_INVARIANTS` env-default OFF, LIVE ON drop-inem (D2 per-proces dryf) — stan flagi nie z modułu. **Klasa D** (env-frozen vs efektywny).

---

## POKRYCIE (jawne luki — nie cisza)

- **Cross-repo powierzchnie (J)** — konsola `nadajesz_clone/panel/fleet_state.py`, apka `courier-app/route_podjazdy`, `courier_api/courier_orders.py`: NIE otwierane tu (to OS A2-surfaces / klasa J, osobny agent Fazy A). Odnotowano TYLKO istnienie bliźniaków kanon-kolejności/pozycji z protokołu — pełny inwentarz cross-repo = handoff.
- **Efektywny stan flag per-proces** (drop-in merge `systemctl show`): NIE weryfikowany tu — cytuję deklaracje common.py + flags.json defaults z RECON. Pełna prawda flag = OS A3-flag-registry. Każde „ON/OFF" tu = deklaracja, nie `FLAG_FINGERPRINT`.
- **~19 SOFT kar scoringu** wyliczone prefiksem `bonus_` (dispatch_pipeline.py:1716-1721, 2302-2307, 4088-4148), ale NIE każda zmapowana 1:1 do nazwanej reguły biznesowej — część to termy kalibracyjne/bug-fix (`bonus_bug4_cap_soft`, `bonus_gps_age_discount`), nie „reguły". Mapowanie term→reguła = Faza B.
- **R9 wait HARD-reject tail** — nie potwierdzony oracle (body zwracał False); oznaczone do weryfikacji Fazy C.
- **ML/LGBM scoring** (shadow-only, `ENABLE_LGBM_SHADOW`) — poza zakresem rdzenia reguł (nie egzekwuje decyzji LIVE).
- **paczka-exempt 3. site** (plan/`is_paczka_order`) — zidentyfikowany z protokołu, dokładna linia nie re-grepowana w tej rundzie.

## HANDOFF dla Faz B/C/D/E/F

- **Faza B (sweep bliźniaków):** priorytety A1 — (a) pozycja-równość 8 bliźniaków (najgęstsza, file:linia wyżej), (b) kanon-kolejności 3-4 kopie cross-repo, (c) SLA-anchor 3 bliźniaki (1 rozjechany), (d) `lex_qual` ×3, (e) paczka-exempt 3-site, (f) R6 prog 35 płaski/40 tier/bundle_calib.
- **Faza C (oracle przyrządów):** reguło-zależne instrumenty do kalibracji — `r6_breach_shadow.jsonl`, `bundle_calib_shadow` (flat-35 mismeasure T3 = potwierdzony smell N), `best_effort_objm_shadow`, `earlybird_t30_shadow`, `reassignment_forward_shadow` (duch przerzutu, pozycja-równość bliźniak #7), `would_hard_cap`.
- **Faza D (graf konfliktów) — gotowe pary kandydatów:**
  - R-DECLARED-TIME (HARD-deklarowana, BRAK inwariantu) ↔ R27 (SOFT window) — kto egzekwuje deklarację?
  - R-LATE-PICKUP (nazwa HARD_GATE) ↔ rzeczywista warstwa SELEKCJA — precedencja vs R6/R27.
  - R-RETURN-VETO (nazwa VETO) ↔ feasibility metric-only ↔ kanon zakaz — gdzie egzekwowane, spójnie?
  - R-10 FLEET-LOAD-BALANCE (ON) ↔ SP-B2 LOADGOV (OFF) — 2 reguły load, która wygrywa.
  - pre-shift floor: feasibility HARD ↔ plan_recheck BRAK (K2 cofacz) — niespójność między ścieżkami.
  - R6 cap: płaski 35 (feasibility HARD) ↔ tier 40 (best_effort/objm) — over-penalizacja T3.
  - SLA-anchor: ready-anchor (route_simulator) ↔ pickup_at (feasibility) — niespójna kotwica.
- **Faza E (dedup):** R1/R3/R5/R8 to wspólny root „geometria/spread zsoftowana" (NIE 4 osobne problemy). pozycja-równość 8 bliźniaków = 1 root w 8 manifestacjach. kanon+SLA-anchor+paczka-exempt = rodzina „wielokrotne site bez wspólnego źródła".
- **Faza F (target):** kanoniczne kontrakty — (1) JEDEN moduł `lex_qual`/selekcji; (2) JEDEN `_apply_canon_order` cross-repo (golden-fixture parytet); (3) JEDEN SLA-anchor ready-based; (4) R-DECLARED-TIME jako runtime-inwariant; (5) ujednolicenie słownictwa HARD_GATE/VETO (L).
