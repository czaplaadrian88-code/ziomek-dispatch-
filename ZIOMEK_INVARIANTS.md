# ZIOMEK — INVARIANTS (co MUSI być zawsze prawdą + strażnik, który to wymusza)

> **STATUS: ZATWIERDZONY przez Adriana 01.07.2026** (dowód: CLAUDE.md „Kanon architektury" + MEMORY; nagłówek zaktualizowany 03.07.2026, audyt N3). Kręgosłup egzekwowania (Filar F-4).
> **Zasada:** każdy inwariant = zdanie + strażnik, który się ŁAMIE przy złamaniu. Inwariant bez strażnika = życzenie.
> Zrekoncyliowane z oracle Fazy 1 (`FAZA1_03_rejestr_przyrzadow.md`). Para: [[ZIOMEK_ARCHITECTURE.md]] (8 kontraktów).

## Taksonomia siły egzekwowania (KLUCZOWE — nie mieszać)
- **✅ RT** — runtime-tripwire LIVE + oracle-validated: blokuje/alarmuje w produkcji, sprawdzony niezależną metodą.
- **🟢 TEST** — test regresji zielony: łapie przy `pytest`, ale NIE jest żywym tripwire'em (regres wejdzie live, złapie się dopiero w CI następnej zmiany).
- **⚠️ VOID** — strażnik/przyrząd ISTNIEJE, ale oracle pokazał, że KŁAMIE. **Gorszy niż brak** — daje fałszywą pewność. Priorytet naprawy = najwyższy.
- **🔴 SLOT** — brak strażnika. Nazwany dług, do zbudowania (fala F6/L0).

**Dziś (2026-07-05, Sprint 1 Z1): ~21 testów-strażników (głównie 🟢 TEST, dane/sentinele/stan), 2 ⚠️ VOID pozostałe (carried_first_guard, global_allocate geometria — Zadanie 2 Sprintu 1), klasa ALOKACJA/FEASIBILITY = 🔴 SLOT-y.** Fala F6 celuje w 🔴 klasy feasibility + naprawę ⚠️ VOID.

---

## Kontrakt ① — JEDNO ŹRÓDŁO NA REGUŁĘ
- 🔴 **INV-SRC-ROUTE-ORDER**: `proj(silnik)==proj(konsola)==proj(apka)` (równość porządku `[(typ, sorted(order_ids))]`). Strażnik = golden-fixture equivalence w CI. ⏰ **deadline 07-10** (monitor `ziomek_time_route_monitor` wygasa; dziś 44-75 rozjazdów/d).
- 🔴 **INV-SRC-AVAILABLE-FROM**: `available_from` liczone w 1 miejscu = `max(now, shift_start)`; 17 powierzchni floor → 1. (F1/L4)
- 🔴 **INV-SRC-LEXQUAL**: 3 kopie `lex_qual` dają identyczny ranking (parytet). (bramka 03.07)
- 🔴 **INV-SRC-EQUAL-TREATMENT**: brak GPS / pre_shift = identyczny bucket we WSZYSTKICH 8 bliźniakach. (łatane ≥4×)

## Kontrakt ② — KONTRAKT WARSTW (HARD przed SOFT)
- 🟢 **INV-FEAS-SHIFT-END**: heurystyka mass-fail V328 nie proponuje po końcu zmiany → `test_v328_heuristic_shift_guard`.
- 🟢 **INV-SEL-MULT-SIGN**: mnożnik score nie odwraca na ujemnym score → `test_v327_mult_sign_guard`.
- 🔴 **INV-LAYER-HARD-BEFORE-SOFT (pełny)**: `_assert_feasibility_first` istnieje, ale tylko na 1 call-site → re-assert na EMIT (po mutacjach `FEAS_CARRY_READMIT`, wzorzec #10). Strażnik globalny brak.
- 🔴 **INV-LAYER-NO-VERDICT-OUTSIDE-L5**: `verdict=KOORD` tylko w warstwie 8; zakaz poza.
- 🔴 **INV-FEAS-R6-ONE-SOURCE** *(re-spec 2026-07-01 — pomiar B1 na 2718 worków OBALIŁ wersję „tier-aware 35 T1/2, 40 T3": myliła KLASĘ kuriera z POZIOMEM ESKALACJI; zgodność z KANON „35 normalnie / 40 TYLKO alarm")*: rodzina progów termicznych R6 (=35: `BAG_TIME_HARD_MAX_MIN`, `O2_OVERAGE_CAP_MIN`, bundle_calib…) → docelowo 1 źródło (L6.B2); każdy INSTRUMENT mierzy na TYM SAMYM dialu co dźwignia, którą kalibruje (bundle_calib↔`O2_OVERAGE_CAP_MIN`: 🟢 `test_overage_cap_equals_engine_dial`). Termiczna R6 jest PŁASKA (35, doktryna Adriana 2026-05-10, feasibility_v2 „35 min jedyną twardą regułą"); „40" = `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN` — cap SELEKCJI kuriera w eskalacji-3 (ratunek przy 0 feasible), inny mechanizm niż termika. Bag-size cap per KLASĘ (`HARD_TIER_BAG_CAP` gold6/std5/slow4, flaga OFF) = jeszcze inna oś (liczba zleceń, nie minuty).
- 🔴 **INV-FEAS-PICKUP-FLOOR**: `pickup_eta ≥ max(now, shift_start)` na każdej powierzchni (grep dziś = 0 strażników).
- 🔴 **INV-FEAS-NO-DOUBLE-BOOK**: kurier nie zaproponowany do 2 sprzecznych zleceń w 1 ticku (greedy pile-on, K6 — global de-konflikcja).

## Kontrakt ③ — PARYTET BLIŹNIAKÓW (z konstrukcji)
- 🔴 **INV-TWIN-ROUTE-ORDER** / **INV-TWIN-LEXQUAL** / **INV-TWIN-SLA-ANCHOR**: golden-fixture equivalence per rodzina (patrz rejestr bliźniaków w ARCHITECTURE §4). Cel = wspólny moduł zamiast dyscypliny ręcznej.

## Kontrakt ④ — PRAWDA FLAG
- 🟢/🔴 **INV-FLAG-REGISTRY**: 100% flag decyzyjnych w `ETAP4_DECISION_FLAGS`; sonda `flag_fingerprint` pokrywa wszystkie. Dziś: `flag_registry.py` istnieje, ALE **112 flag poza rejestrem + 5 dead-flag** → 🔴 do domknięcia.
- 🟢 **INV-FLAG-CONFTEST-STRIP** *(de-VOID 2026-07-05, Sprint 1 Z1)*: test z OFF nie biegnie cicho ON. Strażnik = `test_conftest_flag_strip_guard` (3 testy, mutation-probe ×2): (a) strip faktycznie usuwa WSZYSTKIE klucze z 3 list pokrycia, (b) niedecyzyjne klucze bajt-w-bajt, (c) **RATCHET** — klasa przeciekowa nie może urosnąć (baseline 134 znanych survivors z 2026-07-05, kierunek tylko w dół; nowa flaga w flags.json bez ETAP4 = czerwony test). Pełne zamknięcie (baseline→0) = INV-FLAG-REGISTRY (🔴 wyżej). Stary claim „naprawione 257d315" był VOID (łatka na 3 instancje). Dowód: `eod_drafts/2026-07-05/A1_SERIALIZER_reoracle_dowod.md`.

## Kontrakt ⑤ — PRAWDA PRZYRZĄDÓW (flip tylko na validated)
- 🔴 **INV-TRUTH**: każdy werdykt shadow/monitor JOIN `gps_delivery_truth`/`decision_outcomes` + tripwire `delta≥0` uzbrojony (struktura niemożliwa = harness pada, nie loguje jako dane).
- ⚠️ **VOID — do naprawy PRZED jakimkolwiek flipem na ich liczbie (oracle Fazy 1):**
  *(STATUS 2026-07-02, L1.2: READ-side przyczyn część usunięta — WRONG-SOURCE martwy sla 3→0 [no_gps_eta_error, prep_bias_r6_replay, b_route_shadow_review real_joined 0→322] + 40 tooli rotation-aware; formalne zdjęcie VOID = re-oracle C9 przy następnym użyciu przyrządu. Szczegóły: adendum w `eod_drafts/2026-06-30/FAZA1_03_rejestr_przyrzadow.md`.)*
  - `carried_first_guard` = **VOID** (biega z pustym env → 90% rekordów fikcyjne `no_position`). ← *to unieważnia moje wcześniejsze ✅ przy INV-ORDER-CANON.*
  - `global_allocate` geometryczna jakość = **VOID** (certyfikuje liczbę, ślepy na geometrię — 35% worków spread>8km po de-pile). **MUSI blokować flip `PENDING_RESWEEP_LIVE`.**
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
- 🔴 **INV-COH-R-DECLARED**: tripwire `czas_kuriera ≥ czas_odbioru_timestamp` (R-DECLARED-TIME) zawsze.

---

## Klaster DANE/SENTINELE (Filar F-3, most K5 — dziś najgorętszy fizycznie 🔥)
- 🟢 **INV-POS-BBOX** (`test_bbox_guard_geocoding`) · 🟢 **INV-POS-GPS-TRUST** (`test_fail05_gps_bbox_guard`) · 🟢 **INV-POS-SENTINEL-NOPHANTOM** (`test_coord_poison_guard`) · 🟢 **INV-POS-BOOTSTRAP-PRESERVE** (`test_bootstrap_preserve_guard`) · 🟢 **INV-POS-UNIQUE-PICKUP** (`test_bug2_bootstrap_guard`).
- 🟢 **INV-STATE-NO-SILENT-EMPTY** (`test_state_write_guard`) · 🟢 **INV-STATE-DELIVERED-NO-SINK** (`test_delivered_sink_guard`) · 🟢 **INV-STATE-NO-EMPTY-OVERWRITE** (`test_fail09_packs_empty_write_guard`) · 🟢 **INV-STATE-NO-NOWISO** (`test_payload_fallback_guards`) · 🟢 **INV-STATE-PARSE-CONTINUITY** (`test_parse_continuity_guard`).
- 🔴 **INV-POS-NO-PRODUCE (kluczowy, F3/L2)**: ŻADNA ścieżka NIE *produkuje* (0,0)/BIALYSTOK_CENTER jako pozycji — wepnij ISTNIEJĄCY walidator `common.py:513` u INGEST (nie buduj nowego). Zweryfikowane oracle 30.06: **12 miejsc-trucizn w żywym silniku** (6 = `courier_resolver` no_gps/pre_shift, 4 = `dispatch_pipeline` defaulty, 2 = `chain_eta`), reszta z surowych 92 = fałszywki/obrona. 🔥 LIVE: 2046+14456 zdarzeń, 8 ofiar 30.06.
  - **L2.1 (2026-07-01) ZBUDOWANE, czeka na flip:** JEDEN walidator u ingest (gps_server POST / `state_machine.upsert_order` [pokrywa też parcel] / shadow-tick geocode-or-skip / read-side `_load_gps_positions`) + guardy konsumentów geometrii (`_coords_pass`: soon_free probe+serializer / wave-veto / repo-cost / bundle L2/L3 / coloc) + `_save_plan_on_assign` pisze REALNE coords z orders_state (koniec placeholderów K5b) + `feasibility._valid`→kanon. Flaga `ENABLE_COORD_SENTINEL_INGEST_GUARD` (OFF=legacy bajt-w-bajt). Strażnik: 🟢 `test_coord_sentinel_ingest_l21` (22, w tym e2e detonacji V328). Żywy łańcuch 01.07 (28 ofiar): plan-placeholder (0,0) → `_soon_free_probe` → haversine w SERIALIZERZE metryk → V328 eject. Telemetria: `coord_poison_bag_oids`/`coord_poison_new_delivery` (unconditional). PO flipie: BIALYSTOK_CENTER-fikcja (świadoma polityka no_gps) = zostaje → typ Unknown (filar #3, osobna fala); catch-all `_v328_eval_safe` rozróżnia = L2.2.

---

## Dashboard pokrycia (do śledzenia — 🔴 mają znikać, ⚠️ VOID najpilniej)
| Kontrakt/klaster | ✅RT/🟢TEST | ⚠️VOID | 🔴SLOT |
|---|---|---|---|
| ① jedno źródło | 0 | 0 | 4 |
| ② warstwy | 2 | 0 | 5 |
| ③ bliźniaki | 0 | 0 | 3 |
| ④ flagi | 2 | 0 | 1 |
| ⑤ prawda przyrządów | 2✅+1🟢 | 2 | 1 |
| ⑥ semantyka | 1 | 0 | 2 |
| ⑦ cykl życia | 2 | 0 | 2 |
| ⑧ koherencja | 1 | 0 | 2 |
| DANE/SENTINELE | 10 | 0 (carried→⑤) | 1 |
| **RAZEM** | **~21** | **2** | **21** |

**Wniosek:** dług egzekwowania skoncentrowany w kontraktach ①②③ (alokacja/feasibility) — 12 z 21 slotów. Fala F6/L0 celuje TAM + naprawia pozostałe 2 ⚠️ VOID (`carried_first_guard`, `global_allocate` geometria — Sprint 1 Z2; fałszywa pewność). Klasa DANE/STAN już gęsto obstawiona. *(2026-07-05 Z1: serializer + CONFTEST-STRIP zdjęte z VOID — dowód `eod_drafts/2026-07-05/A1_SERIALIZER_reoracle_dowod.md`.)*
