# ZIOMEK — INVARIANTS (co MUSI być zawsze prawdą + strażnik, który to wymusza)

> **STATUS: ZATWIERDZONY przez Adriana 01.07.2026** (dowód: CLAUDE.md „Kanon architektury" + MEMORY; nagłówek zaktualizowany 03.07.2026, audyt N3). Kręgosłup egzekwowania (Filar F-4).
> **Zasada:** każdy inwariant = zdanie + strażnik, który się ŁAMIE przy złamaniu. Inwariant bez strażnika = życzenie.
> Zrekoncyliowane z oracle Fazy 1 (`FAZA1_03_rejestr_przyrzadow.md`). Para: [[ZIOMEK_ARCHITECTURE.md]] (8 kontraktów).

## Taksonomia siły egzekwowania (KLUCZOWE — nie mieszać)
- **✅ RT** — runtime-tripwire LIVE + oracle-validated: blokuje/alarmuje w produkcji, sprawdzony niezależną metodą.
- **🟢 TEST** — test regresji zielony: łapie przy `pytest`, ale NIE jest żywym tripwire'em (regres wejdzie live, złapie się dopiero w CI następnej zmiany).
- **⚠️ VOID** — strażnik/przyrząd ISTNIEJE, ale oracle pokazał, że KŁAMIE. **Gorszy niż brak** — daje fałszywą pewność. Priorytet naprawy = najwyższy.
- **🔴 SLOT** — brak strażnika. Nazwany dług, do zbudowania (fala F6/L0).

**Dziś (2026-07-05, Sprint 1 Z1+Z2): ~27 testów-strażników, ⚠️ VOID = 0 (wszystkie 4 zlikwidowane re-oracle C9 + mutation-probes — dowody w `eod_drafts/2026-07-05/`), zostaje 13 slotów 🔴 (gros: alokacja/feasibility + route-order w toku u tmux 15/27; 3 STALE zreklasyfikowane S28-B).** Fala F6 celuje w pozostałe 🔴.

> **Aktualizacja 2026-07-07 (B2 higiena/stabilność, +3 strażniki, +12 testów, wszystkie ZIELONE):** dozbrojono najważniejsze puste SEAMY w klasach FEASIBILITY/ALOKACJA (regression-guards istniejącego POPRAWNEGO zachowania, ZERO zmian silnika): (1) `test_inv_r6_dial_family` — rodzina termiczna 35-min = jeden dial (INV-FEAS-R6-ONE-SOURCE, część konsystencji; ⚠ referowany `test_overage_cap_equals_engine_dial` NIE ISTNIAŁ — slot był realnie pusty); (2) `test_inv_lexqual_geometry_group_subordination` — geometria K2 nie przeskakuje grupy tier×bucket w `objm_lexr6.pick()`; (3) `test_inv_carried_first_lock_first` — silnikowy `lock_first` (route_simulator) nigdy nie stawia nowego odbioru na czole niepustego worka. Każdy z mutation-probe (RED przy regresji). Raport: `eod_drafts/2026-07-07/B2_inwarianty.md`. **Ustalenie uboczne:** kilka slotów w dashboardzie jest NIEAKTUALNYCH (STALE) — armed przez L7.x/L6.C, a nadal oznaczone 🔴: INV-LAYER-HARD-BEFORE-SOFT (EMIT) i INV-LAYER-NO-VERDICT-OUTSIDE-L5 → `test_split_layer_guard_l73` (L7.3); INV-COH-R-DECLARED (chokepoint zapisu) → `test_r_declared_tripwire_l71` (L7.1). Weryfikacja/reklasyfikacja = osobny temat (nie ruszam kanonu bez ACK).

> **Aktualizacja 2026-07-07 (S28-B narzędzia/higiena — ZERO zmian silnika/flag):** (1) **reklasyfikacja 3 slotów STALE 🔴→🟢** z DOWODEM (guardy istnieją, 24 testy ZIELONE, mutation-probe w każdym): INV-LAYER-HARD-BEFORE-SOFT (pełny/EMIT) + INV-LAYER-NO-VERDICT-OUTSIDE-L5 → `test_split_layer_guard_l73` (L7.3); INV-COH-R-DECLARED (chokepoint) → `test_r_declared_tripwire_l71` (L7.1) — potwierdzenie ustalenia B2. Węższe siostrzane części (re-assert po FEAS_CARRY_READMIT / `_assert_` w selekcji) POZOSTAJĄ 🔴 = xfail-RATCHET B2 (fala silnika). (2) **INV-POS-NO-PRODUCE 🔴→🟡**: NOWY ratchet entropii `test_inv_pos_no_produce_ratchet` (baseline 10 producentów, kierunek malejący; pełne 🟢 = L2.1 flip). Raport: `eod_drafts/2026-07-07/S28B_inwarianty.md`. **Ustalenie:** czyste seamy „bez silnika" niemal wyczerpane (B2+S28-B) — reszta 🔴 wymaga fali silnika (już xfail-ratchetowana) lub jest w toku u tmux 15/27 (route-order).

---

## Kontrakt ① — JEDNO ŹRÓDŁO NA REGUŁĘ
- 🔴 **INV-SRC-ROUTE-ORDER**: `proj(silnik)==proj(konsola)==proj(apka)` (równość porządku `[(typ, sorted(order_ids))]`). Strażnik = golden-fixture equivalence w CI. ⏰ **deadline 07-10** (monitor `ziomek_time_route_monitor` wygasa). *(KOREKTA 06.07, pomiar monitorem: mismatch=0/d od 01.07 przy 100-619 sprawdzeniach/d — flagi trust-canon wszędzie ON robią parytet; „44-75/d" NIEAKTUALNE. Problem pozostaje KONSTRUKCYJNY: 4 kopie/3 repa trzymane flagami, nie konstrukcją.)*
- 🟢 **INV-SRC-AVAILABLE-FROM** *(slot uzbrojony 2026-07-05, Z2; źródło = L4 LIVE od 04.07)*: `available_from` liczone w 1 miejscu (`courier_resolver.resolve_available_from*`, flaga `ENABLE_AVAILABLE_FROM_SINGLE_SOURCE` ON). Strażnik = `test_l4_available_from` (25 testów: źródło+konsumenci #1/#3/#5+chokepoint, mutation ×2 przy budowie L4).
- 🟢 **INV-SRC-LEXQUAL** *(slot uzbrojony 2026-07-05, Z2; unifikacja 25.06 + L6.C C1 04.07)*: 3 kopie `lex_qual` → kanon `objm_lexr6.lex_qual`. Strażnik = `test_objm_lexr6_unify_2026_06_25` (pick==kanon OFF+ON, anty-redywergencja inline w pick I w cieniu, parytet cienia w obu stanach POST_SHIFT).
  - 🟢 **K2 selektor-subordynacja uzbrojona 2026-07-07 (B2)**: `test_inv_lexqual_geometry_group_subordination` — geometria (SOFT tie-break, `deliv_spread_km`) NIE przeskakuje TWARDEJ grupy tier×bucket w `objm_lexr6.pick()`; kandydat z gorszego tieru/bucketu z idealną geometrią I lepszym R6 NIE wygrywa (HARD>SOFT na warstwie selekcji, INV-LAYER-5). Mutation-probe: un-grouped `min(feasible, lex_qual)` wybrałby worse-tier=B, `pick()` (grouped) trzyma A. Uzupełnia `test_l6c_geometry_claim` (klucz) o kontrakt SELEKTORA (grupa).
- 🔴 **INV-SRC-EQUAL-TREATMENT**: brak GPS / pre_shift = identyczny bucket we WSZYSTKICH 8 bliźniakach. (łatane ≥4×)

## Kontrakt ② — KONTRAKT WARSTW (HARD przed SOFT)
- 🟢 **INV-FEAS-SHIFT-END**: heurystyka mass-fail V328 nie proponuje po końcu zmiany → `test_v328_heuristic_shift_guard`.
- 🟢 **INV-SEL-MULT-SIGN**: mnożnik score nie odwraca na ujemnym score → `test_v327_mult_sign_guard`.
- 🟢 **INV-LAYER-HARD-BEFORE-SOFT (pełny)** *(reklasyfikacja S28-B 2026-07-07 — dashboard był STALE; armed L7.3)*: `_assert_feasibility_first` re-assertowany na EMIT przez `_split_layer_emit_assert` (wspólny lejek `_classify_and_set_auto_route`). Strażnik = `test_split_layer_guard_l73` (INV-LAYER-1; flaga ON≠OFF bajt-parytet, mutation-probe: zdjęcie gardy OFF → RED). ⚠ węższa re-assert PO `FEAS_CARRY_READMIT` = xfail-RATCHET `test_invariant_slots_l04` SLOT 5 (wymaga fali silnika).
- 🟢 **INV-LAYER-NO-VERDICT-OUTSIDE-L5** *(reklasyfikacja S28-B 2026-07-07 — dashboard był STALE; armed L7.3)*: jeden setter `_set_feasibility_verdict` z gardą warstwy; zapis werdyktu poza L5 loguje naruszenie. Strażnik = `test_split_layer_guard_l73` (INV-LAYER-2; mutation-probe zdejmujący gardę → RED).
- 🔴 **INV-FEAS-R6-ONE-SOURCE** *(re-spec 2026-07-01 — pomiar B1 na 2718 worków OBALIŁ wersję „tier-aware 35 T1/2, 40 T3": myliła KLASĘ kuriera z POZIOMEM ESKALACJI; zgodność z KANON „35 normalnie / 40 TYLKO alarm")*: rodzina progów termicznych R6 (=35: `BAG_TIME_HARD_MAX_MIN`, `O2_OVERAGE_CAP_MIN`, bundle_calib…) → docelowo 1 źródło (L6.B2); każdy INSTRUMENT mierzy na TYM SAMYM dialu co dźwignia, którą kalibruje (bundle_calib↔`O2_OVERAGE_CAP_MIN`: 🟢 `test_overage_cap_equals_engine_dial`). Termiczna R6 jest PŁASKA (35, doktryna Adriana 2026-05-10, feasibility_v2 „35 min jedyną twardą regułą"); „40" = `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN` — cap SELEKCJI kuriera w eskalacji-3 (ratunek przy 0 feasible), inny mechanizm niż termika. Bag-size cap per KLASĘ (`HARD_TIER_BAG_CAP` gold6/std5/slow4, flaga OFF) = jeszcze inna oś (liczba zleceń, nie minuty).
  - 🟢 **DIAL-FAMILY consistency uzbrojona 2026-07-07 (B2)**: `test_inv_r6_dial_family` pilnuje, że rodzina termiczna trzyma JEDEN dial — `BAG_TIME_HARD_MAX_MIN == DEFAULT_SLA_MINUTES == C2_PER_ORDER_THRESHOLD_MIN == O2_OVERAGE_CAP_MIN` (=35) — ORAZ że eskalacja-3 `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN` (40) jest DISTINCT/luźniejsza, a bag-size caps to oś liczby (≠35). Łapie dial-drift „podbito jeden próg bez bliźniaków" = bramka≠scoring. ⚠ referowany wyżej `test_overage_cap_equals_engine_dial` **NIE ISTNIAŁ w repo** (slot był realnie pusty). Strukturalne 1-źródło (L6.B2, jeden `sla_anchor` na wszystkie) nadal otwarte — ten strażnik pina SPÓJNOŚĆ wartości, nie strukturalną unifikację.
- 🟢 **INV-FEAS-PICKUP-FLOOR** *(slot uzbrojony 2026-07-05, Z2 — silnik+monitor; „0 strażników" było stale)*: `pickup_eta ≥ max(now, shift_start)` — strażnik = `test_pickup_floor_guard` + żywy monitor `tools/pickup_floor_guard` (NIE-ŚLEPY po L4: resolucja shift_start kanonem `resolve_available_from*`). Zakres: silnik + monitor; powierzchnie renderów (konsola/apka display-floor) = osobny pas route-order/L3.
- 🔴 **INV-FEAS-NO-DOUBLE-BOOK**: kurier nie zaproponowany do 2 sprzecznych zleceń w 1 ticku (greedy pile-on, K6 — global de-konflikcja).

## Kontrakt ③ — PARYTET BLIŹNIAKÓW (z konstrukcji)
- 🔴 **INV-TWIN-ROUTE-ORDER**: golden-fixture equivalence (w toku — Sprint 0 tmux 15, deadline 07-10.07; L6.A golden 13/13 = fundament).
- 🟢 **INV-TWIN-LEXQUAL** *(slot uzbrojony 2026-07-05, Z2)*: wspólny moduł `objm_lexr6` + `test_objm_lexr6_unify_2026_06_25` (parytet 3 kopii z konstrukcji — patrz INV-SRC-LEXQUAL).
- 🟢 **INV-TWIN-SLA-ANCHOR** *(slot uzbrojony 2026-07-05, Z2; fala S1 02.07)*: wspólny moduł `sla_anchor.py` (3 bliźniaki RAZEM: `_count_sla_violations` + feasibility SLA-loop + `plan_recheck._o2_key`), flaga `ENABLE_SLA_ANCHOR_UNIFIED` — ⚠ **LIVE=true w flags.json** (potwierdzone 06.07 odczytem żywym; kod-default False MYLI — czytaj flags.json, wzorzec #9). Obserwacja S1 zdrowa (250/258 rekordów z `sla_anchor_source`, dial=35). Strażnik = `test_sla_anchor_unified` (OFF = fuzz 400/0 bajt-parytet; ON = te same decyzje + `sla_anchor_source`).

## Kontrakt ④ — PRAWDA FLAG
- 🟢/🔴 **INV-FLAG-REGISTRY**: 100% flag decyzyjnych w `ETAP4_DECISION_FLAGS`; sonda `flag_fingerprint` pokrywa wszystkie. Dziś: `flag_registry.py` istnieje, ALE **112 flag poza rejestrem + 5 dead-flag** → 🔴 do domknięcia.
- 🟢 **INV-FLAG-CONFTEST-STRIP** *(de-VOID 2026-07-05, Sprint 1 Z1)*: test z OFF nie biegnie cicho ON. Strażnik = `test_conftest_flag_strip_guard` (3 testy, mutation-probe ×2): (a) strip faktycznie usuwa WSZYSTKIE klucze z 3 list pokrycia, (b) niedecyzyjne klucze bajt-w-bajt, (c) **RATCHET** — klasa przeciekowa nie może urosnąć (baseline 134 znanych survivors z 2026-07-05, kierunek tylko w dół; nowa flaga w flags.json bez ETAP4 = czerwony test). Pełne zamknięcie (baseline→0) = INV-FLAG-REGISTRY (🔴 wyżej). Stary claim „naprawione 257d315" był VOID (łatka na 3 instancje). Dowód: `eod_drafts/2026-07-05/A1_SERIALIZER_reoracle_dowod.md`.

## Kontrakt ⑤ — PRAWDA PRZYRZĄDÓW (flip tylko na validated)
- 🔴 **INV-TRUTH**: każdy werdykt shadow/monitor JOIN `gps_delivery_truth`/`decision_outcomes` + tripwire `delta≥0` uzbrojony (struktura niemożliwa = harness pada, nie loguje jako dane).
- ✅ **VOID-y kontraktu ⑤ ZLIKWIDOWANE 2026-07-05 (Sprint 1 Z1+Z2 — re-oracle C9 + strażnicy z zębami; historia niżej):**
  *(STATUS 2026-07-02, L1.2: READ-side przyczyn część usunięta — WRONG-SOURCE martwy sla 3→0 [no_gps_eta_error, prep_bias_r6_replay, b_route_shadow_review real_joined 0→322] + 40 tooli rotation-aware; formalne zdjęcie VOID = re-oracle C9 przy następnym użyciu przyrządu. Szczegóły: adendum w `eod_drafts/2026-06-30/FAZA1_03_rejestr_przyrzadow.md`.)*
  - 🟢 `carried_first_guard` — *(de-VOID Z2 05.07)* przyczyna (pusty env → 91,7% fikcyjnych `no_position`) usunięta przez L0.2 (drop-in `engine-env-parity.conf` + `test_carried_first_guard_env_parity`) i D3 (gros flag → flags.json hot-reload = parytet z konstrukcji dla oneshot-procesu). **Re-oracle świeże okno od 02.07: 4901 rekordów, `no_position` = 0** (klasyfikacje realne: ok 4120 / plan_invalidated 462 / canon_divergence 235 / carried_first 83 / coverage_gap 1); timer żywy co 3 min. Mutation-probe ×2 (drop-in −1 flaga → parity FAIL; smell→False → detekcja FAIL). Dowód: `eod_drafts/2026-07-05/A1_INVARIANTS_devoid_dowod.md`.
    - 🟢 **SILNIKOWA ścieżka carried-first uzbrojona 2026-07-07 (B2)**: `test_inv_carried_first_lock_first` — `route_simulator_v2._sticky_sequence_plan` z niepustym workiem (`lock_first`) NIGDY nie enumeruje sekwencji z nowym ODBIOREM/dostawą na czole („kurier z jedzeniem nie zawraca do nowej restauracji"). Bliźniak silnikowy do `plan_recheck._coalesce_same_pickup_nodes` (pokrytego). Mutation-probe: pusty worek DOPUSZCZA odbiór na czole (lock warunkowy) — usunięcie `continue` w enumeracji → RED.
  - 🟢 `global_allocate` geometryczna jakość — *(de-VOID Z2 05.07)* certyfikator NIE-ślepy: rekordy alokacji niosą spread/km/r6/cos, werdykt `would_repropose` widzi czystą poprawę geometrii (rozjazd_kierunkow przy delta<margin), geometria dociera do jsonl. **Bramka `live_gate_open()` (L6.C) zakodowana + WPIĘTA w jedyną ścieżkę LIVE + testowana ON≠OFF** — flip `PENDING_RESWEEP_LIVE` bez `ENABLE_LEXQUAL_GEOMETRY_TIEBREAK` = HOLD. Strażnik = `test_global_allocate_geometry_guard` (5 testów; mutation-probe ×3: ucięty spread → FAIL, werdykt ślepy na geometrię → FAIL, ominięcie bramki → FAIL). Dowód: `eod_drafts/2026-07-05/A1_INVARIANTS_devoid_dowod.md`.
- 🟢 **serializer −38 kluczy — de-VOID 2026-07-05 (Sprint 1 Z1, re-oracle C9):** naprawa = L1.1 `85d92f7` (deny-lista), żywa od restartu shadow **03.07 13:18 UTC**; re-oracle na świeżym oknie n=229: `eta_source`/`c2_*`/`cs_tier_*`/`sla_minutes_used`/… = **221/229**, `sla_violations_*`=67/229, `r6_gold4_gate_recovered`=14/229; zera = klucze warunkowe (grep producentów) lub nazwy bez producenta (`eta_src`,`drive_source`). Strażnicy: `test_serializer_completeness_l11` (A) + **NOWY** `test_serializer_location_b_parity` (B funkcjonalnie na realnym `PipelineResult` + parytet zbiorów A↔B), mutation-probe ×2 zdane. ⚠ Kalibracja O2: okna ciąć od **2026-07-03T13:19Z** (starsze rekordy mają dziury). Dowód: `eod_drafts/2026-07-05/A1_SERIALIZER_reoracle_dowod.md`.
- ✅ **Kontr-dowód (oracle potwierdził że DZIAŁAJĄ — NIE ruszać jako void):** `post_shift_overrun`=457/2000, `would_hard_cap`=438/2000 LIVE.

## Kontrakt ⑥ — BRAK DRYFU SEMANTYKI
- 🟢 **INV-STATE-GT-RECONCILE**: status-only reassign-artefakt nie liczy się jako fakt GPS → `test_ground_truth_reconcile_guards`.
- 🔴 **INV-SEM-ETA-SPLIT**: `eta_pickup_decision` ⊥ `eta_pickup_display` (dziś 1 pole 2 role — karmi scoring+feasibility+committed).
- 🔴 **INV-SEM-COUPLED-WRITE**: writer aktualizujący `delivery_coords` pisze też `address`+`city` (para) — inaczej ciche kłamstwo utrwalone (near-miss 484269).

## Kontrakt ⑦ — KOMPLETNOŚĆ CYKLU ŻYCIA
- 🟢 **INV-LIFE-ZOMBIE**: order ze stale `picked_up_at` wykluczony z bagu niezależnie od statusu → `test_zombie01_pickup_guard`.
- 🟢 **INV-LIFE-INACTIVE**: kurier `inactive` wykluczony z floty na KAŻDEJ powierzchni → `test_tier01_inactive_courier_guard`.
- 🔴 **INV-LIFE-LOADPLAN-PURE**: `load_plan` = pure-read u źródła (dziś read-with-side-effect).
- 🔴 **INV-LIFE-RECANON-PRUNE**: każda tranzycja kurcząca worek (cancel/deliver/reassign-loser) woła prune PRZED recanon (`recanon` sam nie potrafi prune).

## Kontrakt ⑧ — KOHERENCJA (precedencja)
- 🟢 **INV-VERDICT-CLASSIFIED**: każda bramka KOORD ma klasę {quality|operational}; quality strzeżone → `test_verdict_gate_guards`.
- 🔴 **INV-COH-CLAMP-CHOKEPOINT**: 1 punkt precedencji clampów czasu (`effective_pickup_at`); frozen R27 ↔ floor ↔ OSRM rozstrzygane w jednym miejscu (dziś 13 klastrów konfliktów).
- 🟢 **INV-COH-R-DECLARED (chokepoint zapisu)** *(reklasyfikacja S28-B 2026-07-07 — dashboard był STALE; armed L7.1)*: tripwier `czas_kuriera ≥ czas_odbioru_timestamp` (R-DECLARED-TIME) w JEDYNYM funnelu commitu (`state_machine.upsert_order`) — fail-loud LOG+JSONL, NIGDY reject. Strażnik = `test_r_declared_tripwire_l71` (naruszenie→wpis / cisza / flaga OFF bajt-parytet / TZ-naive=Warsaw / mutation-probe kierunku nierówności → RED). ⚠ siostrzany `_assert_r_declared_time` w SELEKCJI = xfail-RATCHET `test_invariant_slots_l04` SLOT 4 (wymaga fali silnika).

---

## Klaster DANE/SENTINELE (Filar F-3, most K5 — dziś najgorętszy fizycznie 🔥)
- 🟢 **INV-POS-BBOX** (`test_bbox_guard_geocoding`) · 🟢 **INV-POS-GPS-TRUST** (`test_fail05_gps_bbox_guard`) · 🟢 **INV-POS-SENTINEL-NOPHANTOM** (`test_coord_poison_guard`) · 🟢 **INV-POS-BOOTSTRAP-PRESERVE** (`test_bootstrap_preserve_guard`) · 🟢 **INV-POS-UNIQUE-PICKUP** (`test_bug2_bootstrap_guard`).
- 🟢 **INV-STATE-NO-SILENT-EMPTY** (`test_state_write_guard`) · 🟢 **INV-STATE-DELIVERED-NO-SINK** (`test_delivered_sink_guard`) · 🟢 **INV-STATE-NO-EMPTY-OVERWRITE** (`test_fail09_packs_empty_write_guard`) · 🟢 **INV-STATE-NO-NOWISO** (`test_payload_fallback_guards`) · 🟢 **INV-STATE-PARSE-CONTINUITY** (`test_parse_continuity_guard`).
- 🟡 **INV-POS-NO-PRODUCE (kluczowy, F3/L2)** *(ratchet entropii armed S28-B 2026-07-07; pełne 🟢 = flip L2.1)*: ŻADNA ścieżka NIE *produkuje* (0,0)/BIALYSTOK_CENTER jako pozycji — wepnij ISTNIEJĄCY walidator `common.py:513` u INGEST (nie buduj nowego). Zweryfikowane oracle 30.06: **12 miejsc-trucizn w żywym silniku** (6 = `courier_resolver` no_gps/pre_shift, 4 = `dispatch_pipeline` defaulty, 2 = `chain_eta`), reszta z surowych 92 = fałszywki/obrona. 🔥 LIVE: 2046+14456 zdarzeń, 8 ofiar 30.06.
  - 🟡 **Ratchet KIERUNKU uzbrojony S28-B**: `test_inv_pos_no_produce_ratchet` — liczba producentów-placeholderów może TYLKO maleć (baseline zamrożony: 4× `or (0.0,0.0)` w `dispatch_pipeline` + 6× `.pos = BIALYSTOK_CENTER` w `courier_resolver`; NOWY producent > baseline → RED). Mutation-probe: syntetyczny producent → wykryty; guardy `!= (0.0,0.0)` NIE liczone; `.claude/worktrees` pominięte (lekcja S28-A). NIE eliminuje istniejących (to L2.1 flip), ale blokuje wzrost entropii — meta-reguła „entropia niżej". Pełne 🟢 = L2.1 (`ENABLE_COORD_SENTINEL_INGEST_GUARD`) flip + eliminacja fikcji no_gps (osobna fala, filar #3).
  - **L2.1 (2026-07-01) ZBUDOWANE, czeka na flip:** JEDEN walidator u ingest (gps_server POST / `state_machine.upsert_order` [pokrywa też parcel] / shadow-tick geocode-or-skip / read-side `_load_gps_positions`) + guardy konsumentów geometrii (`_coords_pass`: soon_free probe+serializer / wave-veto / repo-cost / bundle L2/L3 / coloc) + `_save_plan_on_assign` pisze REALNE coords z orders_state (koniec placeholderów K5b) + `feasibility._valid`→kanon. Flaga `ENABLE_COORD_SENTINEL_INGEST_GUARD` (OFF=legacy bajt-w-bajt). Strażnik: 🟢 `test_coord_sentinel_ingest_l21` (22, w tym e2e detonacji V328). Żywy łańcuch 01.07 (28 ofiar): plan-placeholder (0,0) → `_soon_free_probe` → haversine w SERIALIZERZE metryk → V328 eject. Telemetria: `coord_poison_bag_oids`/`coord_poison_new_delivery` (unconditional). PO flipie: BIALYSTOK_CENTER-fikcja (świadoma polityka no_gps) = zostaje → typ Unknown (filar #3, osobna fala); catch-all `_v328_eval_safe` rozróżnia = L2.2.

---

## Dashboard pokrycia (do śledzenia — 🔴 mają znikać, ⚠️ VOID najpilniej)
| Kontrakt/klaster | ✅RT/🟢TEST | ⚠️VOID | 🔴SLOT |
|---|---|---|---|
| ① jedno źródło | 2 | 0 | 2 |
| ② warstwy | 5 | 0 | 2 |
| ③ bliźniaki | 2 | 0 | 1 |
| ④ flagi | 2 | 0 | 1 |
| ⑤ prawda przyrządów | 2✅+3🟢 | 0 | 1 |
| ⑥ semantyka | 1 | 0 | 2 |
| ⑦ cykl życia | 2 | 0 | 2 |
| ⑧ koherencja | 2 | 0 | 1 |
| DANE/SENTINELE | 10 | 0 (carried→⑤) | 1 |
| **RAZEM** | **~30** | **0** | **13** |

**Wniosek:** ⚠️VOID = **0** (Sprint 1 Z1+Z2, 05.07: serializer + CONFTEST-STRIP + carried_first_guard + global_allocate — dowody `eod_drafts/2026-07-05/A1_{SERIALIZER_reoracle,INVARIANTS_devoid}_dowod.md`); **bramka `PENDING_RESWEEP_LIVE` ma podstawę pomiarową** (geometria w certyfikatorze + gate wpięty i testowany). Dług egzekwowania dalej w slotach ①②③ (7 z 16) — w toku: INV-SRC-ROUTE-ORDER (Sprint 0 tmux 15, deadline 07-10.07). Klasa DANE/STAN gęsto obstawiona.
