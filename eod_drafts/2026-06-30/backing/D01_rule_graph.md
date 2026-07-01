# D01 — GRAF INTERAKCJI REGUŁ / FLAG → PARY KONFLIKTOWE (lane D, oś I — konflikt-precedencji)

**Faza 1 audyt spójności Ziomka · sesja tmux 2 · READ-ONLY · 2026-06-30 ~14:00 UTC · HEAD `8024705`.**
Wszystkie `plik:linia` ze ŚWIEŻEGO grepu/sed dziś (linie DRYFUJĄ — ≥3 żywe sesje na repo). Zero edycji/restartów/flipów.

**Co to jest:** graf interakcji WSZYSTKICH reguł z A2_rule_registry + flag z A3_flag_registry → pary, które na siebie wpływają. Dla każdej: natura (inwersja HARD↔SOFT / sprzeczność / niezdefiniowana-precedencja / sprzężenie-flag / cicha-inwersja-P), `precedence_status` (defined-consistent / defined-inconsistent / undefined / silent-inversion / ok), dowód. Oś **I (konflikt-regul)** — nowa, czego A1-A6 nie miały explicite.

**Kluczowe źródło precedencji = `ZIOMEK_REGULY_KANON.md` §1 (TABELA ROZSTRZYGANIA) + §5 werdykty C1-C7/C-DT + §9 D1-D5.** To JEDYNE miejsce, gdzie precedencja jest formalnie zdefiniowana. Tam gdzie KANON milczy → `undefined`. Tam gdzie KANON definiuje, a KOD robi inaczej → `defined-inconsistent`.

**Pułapka „tier":** w tym dok „R6 tier-aware 35/40" = POZIOM ESKALACJI (Strategia 1-2-3, §3a KANON), NIE klasa kuriera. Werdykt C5: 35 normalnie, 40 TYLKO alarm (auto, dla WSZYSTKICH). Kod dziś ma 40 per-ścieżka (best_effort/objm) = NIEZGODNE.

---

## TL;DR — 7 najważniejszych ustaleń

1. **3 cichych inwersji HARD R6** (defined-inconsistent z KANONem): (a) `ENABLE_ETA_QUANTILE_R6_BAGCAP=True` luzuje R6 dla gold≤4 p80 — D3 mówi USUŃ; (b) R6 cap 35 (feasibility reject) ↔ 40 (best_effort/objm cap_min) bez bramki ALARM — C5 mówi 40=alarm-only; (c) SLA-anchor `pickup_at` ↔ R6-anchor `pickup_ready_at` — dwie HARD bramki spóźnienia liczą inny anchor (O2 sprint 02.07).
2. **R-DECLARED-TIME = HARD najwyższego priorytetu (22.04) BEZ runtime-inwariantu** — `grep` bramki `czas_kuriera ≥ czas_odbioru` = tylko KOMENTARZE (common.py:3410/3414/3494, dispatch_pipeline.py:3168). Egzekucja POŚREDNIA (R27 frozen + ready-anchor + czasówka). Precedencja zdefiniowana (C-DT), ale brak strażnika = `undefined-at-runtime`.
3. **geometria-rozjazdu SOFT NIE osłabia HARD R6 (P0 zachowany — OK) ALE selekcja jej NIE czyta** — `objm_lexr6.lex_qual` (l.29-49) = czysto czasowy `(post_shift?, r6_breach, committed_late, new_pickup_late)`, ZERO osi geometrii. Pod scarcity best_effort wyrzuca `score` (jedyny nośnik geometrii) → geometria = martwa oś. To NIE inwersja, to LUKA-precedencji (P0-A rootcause).
4. **3 podatki obciążenia żywe RAZEM bez zdefiniowanej precedencji** (`undefined`, D5 pending): R-10 `_v326_fleet_load_balance` (ON) + LOADGOV `bonus_loadgov_shadow_delta` (-40, ON) + stopover/bug4-cap. Mogą potrójnie karać tego samego przeładowanego.
5. **2 mylące nazwy „HARD" o zachowaniu SOFT/SELEKCJA** (L, ryzyko cichej-inwersji przez przyszłą sesję): `ENABLE_LATE_PICKUP_HARD_GATE` (const ON) → tier nie reject; `ENABLE_R_RETURN_TO_RESTAURANT_VETO` (ON) → metryka nie veto (realny zakaz = kanon, D4 „zdejmij VETO z nazwy").
6. **1 maskująca inwersja flagi** (silent-inversion): `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` const=`"1"`(True, common.py:2806) ↔ flags.json=False. Usunięcie klucza → cichy FLIP na ON → utrata always-propose.
7. **pre-shift floor: HARD w feasibility (clamp+reject>30min) ↔ BRAK w plan_recheck** (defined-inconsistent, K2 „cofacz") — regen `courier_plans` co 5 min ODCLAMPOWUJE. 17 powierzchni liczy odbiór, 4 mają floor.

---

## CZĘŚĆ 1 — GRAF: węzły (reguły/flagi) i krawędzie (interakcje)

**Węzły-reguły (z A2):** R1(SOFT), R3(SOFT), R5(SOFT), R6(HARD+tier+SOFT-zona), R7(martwy), R8(SOFT), R9(SOFT+HARD-tail), R-DECLARED-TIME(HARD-declared), R27(SOFT-window+frozen), geometria-rozjazdu(SOFT-only), pozycja-równość(SELEKCJA), always-propose(SELEKCJA/werdykt), kanon-kolejności(KANON), SLA-anchor(HARD+KANON), pre-shift-floor(HARD-feas), early-bird(HARD-werdykt), R-LATE-PICKUP(SELEKCJA-tier), R-RETURN-VETO(split), R-10/FLEET-LOAD(SOFT), LOADGOV(SOFT), paczka-R6-exempt(HARD-exempt), P0-HARD-przed-SOFT(meta-inwariant).

**Węzły-flagi sprzężone (z A3 §4):** ETA_QUANTILE_R6_BAGCAP, OR_TOOLS_TSP↔SAME_RESTAURANT_GROUPING, OBJM_LEXR6_SELECT↔_SHADOW, BEST_EFFORT_OBJM_R6_KEY↔FEAS_CARRY_READMIT, equal-treatment-trójca, COMMIT_DIVERGENCE_VERDICT_GATE(maskująca), R6_DANGER_ZONE_PENALTY(env-frozen), POST_SHIFT_OVERRUN_PENALTY(lex 4-krotka).

**Krawędzie konfliktowe** = 17 par (CZĘŚĆ 2). Klasyfikacja krawędzi:
- **oś-czasu vs oś-geometrii** (R6/SLA/lex_qual ↔ R1/geometria): różne osie, geometria mute.
- **anchor-split** (SLA pickup_at ↔ R6 ready_at; feasibility ↔ plan_recheck floor): ta sama oś, różny punkt liczenia.
- **próg-split** (R6 35 ↔ 40; ±5 ↔ ±10): ta sama reguła, różny próg bez bramki trybu.
- **nazwa vs zachowanie** (LATE_PICKUP_HARD_GATE, RETURN_VETO): L.
- **flaga maskuje/sprzęga** (COMMIT_DIVERGENCE, OR_TOOLS↔GROUPING, equal-trójca, lex 4-krotka): D/sprzężenie.
- **HARD-deklarowany bez runtime** (R-DECLARED-TIME): brak strażnika.
- **stack kar** (R-10+LOADGOV+stopover): undefined precedence.

---

## CZĘŚĆ 2 — PARY KONFLIKTOWE (szczegóły + dowód)

### K-D01 · R6-HARD-tier-aware ↔ geometria-rozjazdu-SOFT
- **rule_a:** R6 (HARD, `feasibility_v2.py:1105` reject `>BAG_TIME_HARD_MAX_MIN=35`; tier-stretch 40 best_effort).
- **rule_b:** geometria-rozjazdu (SOFT-only: `dispatch_pipeline.py:4624` r1_soft_pen, `:4635` opposite-dir, `:4826` wave-veto→bonus, `:5239-5240` cross-quad `score*=0.1`).
- **natura:** geometria SOFT — **NIE osłabia HARD R6 (I1=NIE, P0 zachowany)** ALE NIE jest czytana przez klucz selekcji. `objm_lexr6.lex_qual` (l.40-46) = `(post_shift?, objm_r6_breach_max_min, late_pickup_committed_max, new_pickup_late_min)` = ZERO osi geometrii. best_effort (`_best_effort_objm_pick` dispatch_pipeline.py:633) i objm-d2 nadpisują `_best_effort_sort_key` → ostatni ślad geometrii (był 5. tie-break przez `-score`) wyrzucony pod scarcity.
- **precedence_status:** **ok** (re I1: SOFT nie osłabia HARD). ⚠ ALE oś geometrii ma `undefined`-precedencję w selekcji = martwa-oś (P0-A case 447: Dawid 447 deliv_spread 10.12, r1_cos -0.987 wygrywa bo najbliższy ODBIÓR).
- **dowód:** `objm_lexr6.py:40-46` (lex_qual bez geometrii); rootcause `ZIOMEK_ROOTCAUSE_AUDIT_allocation_family.md` P0-A; `feasibility_v2.py:501-507` produkuje `deliv_spread_km`/`r1_violation_km` nieczytane przez selekcję.

### K-D02 · R1-spread≤8-SOFT(no-reject) ↔ R6-HARD
- **rule_a:** R1 `feasibility_v2.py:504` `if spread_km > R1_MAX_DELIV_SPREAD_KM(8.0)` → TYLKO `metrics["r1_violation_km"]`, NIE rejectuje (komentarz „SOFT, zweryfikowane audytem 2026-05-21").
- **rule_b:** R6 HARD (jw.).
- **natura:** R1 był HARD, **świadomie zsoftowany** (zsoftowany-HARD); twarda granica geometryczna deleguje całkowicie do R6+SLA (oś czasu). Inne osie (spread km) nie mają HARD-bramki. NIE inwersja (R1 nie osłabia R6 — różne osie).
- **precedence_status:** **defined-consistent** (R1 udokumentowany SOFT, intencja jawna). Ryzyko strukturalne: R6 przejście ≠ spread ograniczony → wąski `geometry_blind_fallback` KOORD (`dispatch_pipeline.py:6458`) wymaga feasible≥2 → nie odpala przy pool=0 (rootcause R3).
- **dowód:** `feasibility_v2.py:504-507`, komentarz `:494`; rootcause R3 (P1 CONFIRMED).

### K-D03 · ETA_QUANTILE_R6_BAGCAP(ON) ↔ R6-HARD-„35-bez-wyjątków"(C5/D3)  ⭐ CICHA INWERSJA HARD
- **rule_a:** `ENABLE_ETA_QUANTILE_R6_BAGCAP` — **flags.json=True (effective ON)** mimo const `feasibility_v2`-default OFF (common.py:236). `feasibility_v2.py:1089-1093` liczy R6 na skalibrowanej p80 ETA dla gold worek≤4 → „odzysk false-rejectów" → JEDYNE >35 ready-anchored co przechodzi HARD R6.
- **rule_b:** R6 HARD „35 dla KAŻDEGO" — KANON §5 **D3: „BEZ WYJĄTKÓW: 35 dla KAŻDEGO (40 tylko alarm). USUŃ recovery gold≤4."**
- **natura:** **inwersja HARD↔kalibracja** — SOFT/calib (p80) luzuje HARD R6 dla podzbioru floty. Werdykt Adriana D3 explicit: usunąć, gold realnie szybsi → skalibrować prędkość, nie luzować cap.
- **precedence_status:** **defined-inconsistent** — KANON D3 definiuje (35 bez wyjątków), KOD trzyma flagę ON luzującą. ⚠ flaga effective ON (zmierzone flags.json) mimo const OFF = dodatkowo D-dryf.
- **dowód:** `feasibility_v2.py:1084-1093`; common.py:120-125/236; flags.json `ENABLE_ETA_QUANTILE_R6_BAGCAP=True` (zmierzone); KANON §9 D3.

### K-D04 · R6-cap-35-feasibility-HARD ↔ tier-40-best_effort/objm(C5)  ⭐ PRÓG-SPLIT HARD
- **rule_a:** feasibility `feasibility_v2.py:1105/1219` reject płaski `>35`.
- **rule_b:** best_effort/objm cap-stretch **40** — `_best_effort_objm_pick(cap_min=40.0)` (dispatch_pipeline.py:633), `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN=40` (common.py:2651), guard new-order bag ≤ cap_min.
- **natura:** **ta sama reguła R6, dwa progi** (35 reject vs 40 dopuszczalne na ścieżce always-propose). KANON C5: „40 = TYLKO ALARM (auto, dla WSZYSTKICH), nie per-ścieżka/per-klasa". Kod stosuje 40 na best_effort/objm bez bramki trybu ALARM.
- **precedence_status:** **defined-inconsistent** — KANON C5 definiuje (40 alarm-only, włączane AUTO gdy Strategia 1+2 niewykonalne), KOD ma 40 bezwarunkowo na best_effort/objm. KANON §5 explicit: „Kod dziś ma 40 per-klasa — NIEZGODNE, do poprawy na alarm-only."
- **dowód:** common.py:2651, dispatch_pipeline.py:633/666/711; KANON §3 R6-row + §5 C5 + Pochodne-TODO #1.

### K-D05 · R27±5-SOFT-window ↔ committed-frozen-nietykalny(C6)
- **rule_a:** R27 ±5 SOFT — `route_simulator_v2.py:1071/1086` `window_open=max(0, open−V3274_FROZEN_PICKUP_WINDOW_MIN(5))`; `tsp_solver.py:263/288/311` `SetCumulVarSoftUpperBound` (NIGDY INFEASIBLE; sztywne ±5 = 7500 INFEASIBLE/d). Post-solve check `route_simulator_v2.py:1479` `V3274_OR_TOOLS_VIOLATION` → fallback greedy.
- **rule_b:** committed `czas_kuriera` NIETYKALNY po assign (`ENABLE_FROZEN_PICKUP_ETA`/`PANEL_FLAG_PIN_AGREED_PICKUP_TIME`).
- **natura:** **wartość HARD-zamrożona, tolerancja okna SOFT** — committed time nietykalny (HARD), ale TSP może planować odbiór poza ±5 z karą (SOFT). KANON §1: „committed nietykalny; przesunięcie max 5 min, nigdy więcej" (C6). ±10 tylko ALARM.
- **precedence_status:** **defined-consistent** (C6) — value HARD, window SOFT±5. ⚠ Caveat egzekucji: SOFT-window MOŻE przekroczyć ±5 w praktyce (kara, nie reject); post-solve check + greedy fallback to jedyny strażnik. „Max 5, nigdy więcej" egzekwowane miękko.
- **dowód:** route_simulator_v2.py:1071-1086/1479; tsp_solver.py:263; KANON §1+§3 R27 + C6.

### K-D06 · R-DECLARED-TIME-HARD-deklarowany ↔ R27/R6 (BRAK runtime-inwariantu)  ⭐ HARD bez strażnika
- **rule_a:** R-DECLARED-TIME — „(HARD) `czas_kuriera ≥ czas_odbioru` ZAWSZE; najwyższy priorytet (22.04)". `grep` bramki/inwariantu = **tylko KOMENTARZE** (common.py:3410/3414/3494, dispatch_pipeline.py:3168). ŻADNA warstwa nie sprawdza `czas_kuriera ≥ czas_odbioru` jako runtime-bramki.
- **rule_b:** R27 (SOFT-window) + ready-anchor + czasówka `order_type` — egzekutorzy POŚREDNI.
- **natura:** **HARD deklarowany bez runtime-inwariantu** — reguła nadrzędna (bije R6 wg C-DT) nie ma strażnika; egzekwowana przez efekt uboczny innych. C-DT: R-DECLARED nadrzędne, R6-breach → propozycja przesunięcia odbioru (dociera ≥15min, min 10min przed).
- **precedence_status:** **defined-consistent na poziomie KANONu (C-DT)** ALE **undefined-at-runtime** (brak inwariantu = nie da się udowodnić egzekucji; przyszła zmiana R27/ready-anchor cicho złamie R-DECLARED bez tripwire). Kandydat #1 Fazy D z A2.
- **dowód:** common.py:3410/3414/3494, dispatch_pipeline.py:3168 (komentarze); KANON §3 R-DECLARED + §1 wiersz + C-DT; A2 §R-DECLARED-TIME.

### K-D07 · always-propose(NIGDY brak-kandydatów) ↔ HARD-reject(feasibility-NO + scoring-layer rejects)
- **rule_a:** always-propose `_always_propose_on()` (dispatch_pipeline.py:2638, `ENABLE_ALWAYS_PROPOSE_ON_SATURATION=True` zmierzone); best_effort sentinel zawsze; KOORD redirect gated `not _always_propose_on()` (`:6491/6864/6900/6926`).
- **rule_b:** HARD-reject — (a) feasibility verdict NO; (b) **scoring-layer HARD rejects flipują MAYBE→NO** w `assess_order`: `v324a_extension_hard_reject` (`:5610`), `carry_chain_hard_rejected` (`:5619`), `v3273_wait_courier_hard_reject` (`:5637-5646`), `intra_rest_gap_hard_reject` (`:5650`).
- **natura:** **HARD-reject vs always-propose pogodzone best_effortem** (gdy wszyscy NO → sentinel). ⚠ Ale: (a) HARD-rejecty żyją w warstwie SCORINGU pipeline (l.4420-5651), nie w `check_feasibility_v2` = **klasa I/C zła-warstwa** (działają PRZED budową puli `:5905` więc meta-inwariant `_assert_feasibility_first` `:2482` trzyma); (b) `FEAS_CARRY_READMIT` (flaga OFF) re-admituje NO→MAYBE ZA guardem (wzorzec #10, `:6278`).
- **precedence_status:** **defined-consistent** (KANON §4 always-propose, sentinel=OK; KOORD wąsko early-bird/czasówka). Latentne ryzyko: FEAS_CARRY_READMIT mutacja za guardem (dziś OFF).
- **dowód:** dispatch_pipeline.py:2638/2482/5610-5651/5905/6278; KANON §4 always-propose; protokół wzorzec #10.

### K-D08 · R-LATE-PICKUP-nazwa-„HARD_GATE"(const ON) ↔ zachowanie-SELEKCJA-tier  ⭐ L (mylące słownictwo)
- **rule_a:** `ENABLE_LATE_PICKUP_HARD_GATE` const default `"1"`=ON (common.py:2822, „ON od 2026-05-31"), `LATE_PICKUP_HARD_MAX_MIN=5.0`.
- **rule_b:** zachowanie = SELEKCJA-tier — `dispatch_pipeline.py:4615-4616` ustawia `late_pickup_committed_breach` → `_late_pickup_tier` (`:496`, demote do najniższego tieru), **NIE hard-reject**. Komentarz „Selekcja = tiering (NIE hard-reject) → ZAWSZE jest propozycja".
- **natura:** **nazwa-HARD vs zachowanie-SELEKCJA** (L). Stała/flaga sugeruje bramkę feasibility; faktycznie tylko demote tieru. Zgodne z KANON no-GPS/C-DT („R-LATE-PICKUP = clamp + propozycja, NIE kara/reject").
- **precedence_status:** **defined-consistent** (zachowanie selekcyjne JEST zamierzone) ALE **ryzyko cichej-inwersji**: nowa sesja czytając „HARD_GATE" potraktuje jako bramkę → mógłaby dodać reject = złamać always-propose. L = bezpiecznik nazewniczy do zdjęcia.
- **dowód:** common.py:2822, dispatch_pipeline.py:4569/4615-4616/496; A2 §R-LATE-PICKUP.

### K-D09 · R-RETURN-nazwa-„VETO"(feasibility-metric-only, ON) ↔ kanon-no-return-HARD(D4)  ⭐ L+B split
- **rule_a:** `ENABLE_R_RETURN_TO_RESTAURANT_VETO` flags.json=True (effective ON). `feasibility_v2.py:905-914` → TYLKO `metrics["return_to_restaurant_oid"/"return_to_restaurant"]`, **NIE przerywa feasibility** (mimo „VETO" w nazwie).
- **rule_b:** realny zakaz = kanon `ENABLE_NO_RETURN_TO_DEPARTED_PICKUP` (plan_recheck.py:377-378/1518-1519, drop-in ON), HARD w trasie.
- **natura:** **nazwa-VETO(=HARD) vs ścieżka-feasibility-metric-only; egzekucja rozdzielona** (B split: metryka w feas, zakaz w kanonie). KANON D4: „HARD w trasie; **zdejmij mylące VETO z nazwy soft-kary**."
- **precedence_status:** **defined-consistent** (HARD egzekwowany w kanonie, D4 potwierdza intencję) ALE naming+split = L/B. Precedencja realna OK (kanon vetuje), nazwa kłamie.
- **dowód:** feasibility_v2.py:905-914, plan_recheck.py:377/1518; KANON §9 D4.

### K-D10 · R-10-FLEET-LOAD-BALANCE(ON) ↔ FLEET-LOAD-GOVERNOR(ON) ↔ stopover/bug4-cap  ⭐ undefined (D5)
- **rule_a:** R-10 `_v326_fleet_load_balance` (dispatch_pipeline.py:1447/1462, `ENABLE_V326_FLEET_LOAD_BALANCE` const=`"1"`=ON, ABSENT z flags.json → effective ON via const; delta od śr. floty ±15).
- **rule_b:** LOADGOV `_loadgov_compute` (`:2250`) + `bonus_loadgov_shadow_delta` (`:2303` „-40, LIVE"); `ENABLE_FLEET_LOAD_GOVERNOR` **flags.json=True (effective ON — A2 błędnie podał OFF!)**; kara bag≥3 przy ewma>2,7.
- **rule_c (3.):** stopover `bonus_r9_stopover` + bug4-cap.
- **natura:** **2-3 reguły OBCIĄŻENIA współistnieją żywe** — różne mechanizmy tego samego pojęcia; mogą potrójnie karać przeładowanego (odebrać zlecenie LEPSZEMU obciążonemu). Sprzeczność/coupling bez rozstrzygnięcia „która rządzi".
- **precedence_status:** **undefined** — KANON §9 **D5 EXPLICIT: „3 podatki obciążenia ⏳ czeka werdykt Adriana (rekalibracja vs jeden rządzący; measure-first ile razy potrójna kara odbiera zlecenie LEPSZEMU)".** Nierozstrzygnięte.
- **dowód:** dispatch_pipeline.py:1447/1462/2250/2303/3394; common.py:2238-2240; flags.json `ENABLE_FLEET_LOAD_GOVERNOR=True` (zmierzone — sprzeczne z A2); KANON §9 D5. **Korekta A2:** A2 §R-FLEET-LEVEL podał LOADGOV OFF — flags.json mówi True. Dryf inwentarza.

### K-D11 · pre-shift-floor-feasibility-HARD ↔ plan_recheck-BRAK(K2 cofacz)  ⭐ defined-inconsistent
- **rule_a:** feasibility HARD — `feasibility_v2.py:751` reject `>V325_PRE_SHIFT_HARD_REJECT_MIN(30)`, `:760` warm-up soft −20, `:794` `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP` → `earliest_departure=shift_start`.
- **rule_b:** plan_recheck regen co 5 min NIE re-aplikuje floor (A6 grupa 6 #5 leak `plan_recheck:554-594` `_start_anchor` committed-pickup BEZ shift_start).
- **natura:** **ta sama reguła „pickup ≥ shift_start", egzekwowana w fazie A (feasibility) ODCLAMPOWYWANA w fazie B (plan_recheck)** co 5 min = K2 cofacz. 17 powierzchni liczy odbiór, 4 mają floor.
- **precedence_status:** **defined-inconsistent** — reguła ma obowiązywać (clamp+reject), plan_recheck cicho ją cofa; brak runtime-inwariantu/`available_from` (grep PUSTE). ⚠ Niuans: pre_shift to ŚWIADOMA polityka warm-up (≤30min przed startem DOZWOLONE) — audyt 30.06 nazwał to „dziura definicyjna + polityka, NIE bug renderu". Floor ma obejmować pre_shift+no_gps wg decyzji.
- **dowód:** feasibility_v2.py:748-794; A6 grupa 6 (17 powierzchni); `AUDYT_preshift_pickup_floor`; KANON §3 R-SCHEDULE.

### K-D12 · SLA-anchor-pickup_at(HARD gate) ↔ R6-anchor-pickup_ready_at(HARD)  ⭐ anchor-split dwóch HARD
- **rule_a:** SLA HARD gate — `feasibility_v2.py:1135` `if plan.sla_violations > 0` → reject (blocking); anchor INLINE `:1156-1164` `pickup_at[oid]→picked_up_at→now`. Bliźniak `route_simulator_v2.py:635-660` `_count_sla_violations` (IDENTYCZNA logika, też `pickup_at`).
- **rule_b:** R6 HARD — anchor `route_simulator_v2.py:663` `r6_thermal_anchor` = picked_up_at→**`pickup_ready_at`**→tsp→now (jedzenie czeka OD GOTOWOŚCI).
- **natura:** **DWIE HARD bramki „spóźnienia" tej samej decyzji liczą RÓŻNY anchor.** Dla nowego ordera: SLA mierzy `delivered − pickup_at` (TSP-projected odbiór), R6 mierzy `delivered − pickup_ready_at` (gotowość). Kurier dojeżdża późno → pickup_at > pickup_ready_at → SLA elapsed < R6 elapsed → **R6 ostrzejszy**. Która „spóźnienie" rządzi? + asymetria paczka-exempt: feasibility SLA-loop MA exempt (`:1152`), `_count_sla_violations` (route_sim) NIE MA → rozjazd A↔B na paczkach.
- **precedence_status:** **defined-inconsistent / undefined** — KANON §7 T2 „R6 anchor split-brain (feasibility=ready vs SLA loop=pickup_at)"; protokół: „przedmiot sprintu O2 (review 02.07)". Dwa HARD, brak zdefiniowanej precedencji który anchor wygrywa.
- **dowód:** feasibility_v2.py:1135/1152/1156-1164; route_simulator_v2.py:635-660/663-671; A6 grupa 4; KANON §7 T2 + §9 B1; protokół Załącznik B „3 bliźniaki SLA-anchor RAZEM".

### K-D13 · COMMIT_DIVERGENCE_VERDICT_GATE: const-ON ↔ flags.json-OFF  ⭐ silent-inversion (maskująca)
- **rule_a:** const `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` = `_os.environ.get(...,"1")=="1"` = **True** (common.py:2805-2806). Konsument `dispatch_pipeline.py:6523` `C.decision_flag(...)` → plan≠commit >10min → KOORD.
- **rule_b:** flags.json `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE=False` (zmierzone) → maskuje const=True → effective False.
- **natura:** **maskująca inwersja flagi (cicha-inwersja-D/M)** — flags.json=False przykrywa const=True. Usunięcie klucza z flags.json → `decision_flag` spada na const True → **cichy FLIP na ON** = utrata dyrektywy ALWAYS-PROPOSE (KOORD-redirect wraca). Kruche.
- **precedence_status:** **silent-inversion** — efektywny stan (OFF) zależy od OBECNOŚCI klucza maskującego; brak go = odwrócenie bez zmiany kodu.
- **dowód:** common.py:2805-2806, dispatch_pipeline.py:6523; flags.json=False (zmierzone); A3 §2b (jedyna prawdziwa inwersja maskująca).

### K-D14 · OR_TOOLS_TSP ↔ SAME_RESTAURANT_GROUPING  (sprzężenie-flag, C3)
- **rule_a/b:** `ENABLE_V326_OR_TOOLS_TSP` (const `"1"`=ON, common.py:2356) ↔ `ENABLE_V326_SAME_RESTAURANT_GROUPING` (const `"1"`=ON, common.py:3159). Oba env-frozen, NIE w flags.json, NIE w ETAP4, NIE w fingerprincie.
- **natura:** **sprzężenie-flag (co-masking)** — grouping karmi OR-Tools TSP. Flip OR_TOOLS OFF BEZ GROUPING OFF → double-insert super-pickupa w legacy planerach (wzorzec #13, maskowane przez OR-Tools).
- **precedence_status:** **defined-consistent** (C3 jawnie: flip w parach) ALE **brak egzekucji** — parytet tylko ręczny; oba poza fingerprintem → flip jednej nie złapany.
- **dowód:** common.py:2356/3159; route_simulator_v2.py:299/438; A3 §4; protokół C3 + wzorzec #13.

### K-D15 · equal-treatment: EQUAL_TREATMENT_BUCKET ↔ NO_GPS_EQUAL_TREATMENT  (sprzężenie-flag, L1)
- **rule_a/b:** `_selection_bucket` (dispatch_pipeline.py:2451) patrzy na `ENABLE_EQUAL_TREATMENT_BUCKET`; `_is_demotable_blind_empty` (`:2467`) na `ENABLE_NO_GPS_EQUAL_TREATMENT`. Reguła „no-GPS równo" bramkowana DWIEMA flagami.
- **natura:** **sprzężenie-flag — jedna reguła, dwie flagi-bramki** (L1 mina). Zgodne TYLKO bo obie ON (+ `PRE_SHIFT_EQUAL_NO_PENALTY` = trójca). Flip jednej → cichy rozjazd (bucket równo, demote nie-równo lub odwrotnie).
- **precedence_status:** **silent-inversion (potencjalna)** — dziś spójne (obie ON), 1 flip od cichego rozjazdu dyskryminacji pozycji.
- **dowód:** dispatch_pipeline.py:2451/2467; KANON §9 L1; A3 §4 equal-treatment trójca.

### K-D16 · objm_lexr6.lex_qual: canon-4-krotka(POST_SHIFT) ↔ frozen-shadow-3-krotka  (sprzężenie-flag, L3)
- **rule_a:** kanon `objm_lexr6.lex_qual` (l.40-46) — ON `ENABLE_POST_SHIFT_OVERRUN_PENALTY` → 4-krotka (prepend post_shift_overrun); OFF → 3-krotka.
- **rule_b:** `_objm_lexr6_shadow._lex_qual` (dispatch_pipeline.py:~1122) — ZAWSZE 3-krotka (frozen baseline at#152).
- **natura:** **sprzężenie-flag / fragile-twin** — zgodne TYLKO bo `ENABLE_POST_SHIFT_OVERRUN_PENALTY=OFF` (wiodące 0.0 = no-op). C7 chce FLIP post-shift ON → cień przestanie odbijać selektor (kłamiący przyrząd E #15/L3).
- **precedence_status:** **fragile / silent-inversion-pending** — dziś bajt-identyczne, flip POST_SHIFT (C7) rozjedzie ranking shadow vs live.
- **dowód:** objm_lexr6.py:40-46; dispatch_pipeline.py:1097-1126; KANON §9 L3; A6 grupa 1; protokół „kruchość, 1. test protokołu".

### K-D17 · R6_DANGER_ZONE_PENALTY: const-ON, getattr, niesterowalna  (dryf-flag, L2)
- **rule_a:** `ENABLE_R6_DANGER_ZONE_PENALTY` const=`"1"`=ON (common.py:774-775, „ON od 2026-05-31"); kara w strefie 32-35 przez `getattr(C, "ENABLE_R6_DANGER_ZONE_PENALTY", False)` (dispatch_pipeline.py:4234, `BAG_TIME_DANGER_PENALTY_PER_MIN=16`).
- **rule_b:** flags.json (ABSENT) + `flag_fingerprint` (poza rejestrem).
- **natura:** **dryf-flag (env-frozen, niesterowalna z flags.json)** — kara R6-danger (strefa pod-cap 32-35) odczytywana `getattr` z const, nie `C.flag()` → operator NIE wyłączy hot, niewidoczna w fingerprincie. L2 mina.
- **precedence_status:** **dryf-flag / undefined-control** — stan efektywny ON niezmienny z kanonu hot; rozjazd per-proces możliwy.
- **dowód:** common.py:774-775, dispatch_pipeline.py:4234-4236; KANON §9 L2.

---

## CZĘŚĆ 3 — kandydaci CICHEJ INWERSJI P-1..P-7 (świadome inwersje zagrożone cichym cofnięciem)

Protokół + KANON §2/§5 mówią „nie cofaj inwersji P-1..P-7 bez ACK" (źródło: `ziomek-full-rule-audit-2026-06-24`, **NIE w moim zasięgu** — nie mam listy P-numerów). Kandydaci (świadome inwersje, których SOFT-zmiana mogłaby cicho cofnąć):
- **R1/R3/R5 zsoftowane z HARD** (audyt 2026-05-21) — cofnięcie = re-HARD geometrii/spread → 7500 INFEASIBLE/d ryzyko. (K-D02)
- **R27 ±5 SOFT zamiast sztywnego** (Adrian 22.06 D1) — cofnięcie na sztywne ±5 = INFEASIBLE flood. (K-D05)
- **ALWAYS-PROPOSE** odwraca „BRAK KANDYDATÓW→KOORD" — cofnięcie (np. usunięcie klucza COMMIT_DIVERGENCE, K-D13) = KOORD wraca. (K-D07/K-D13)
- **no-GPS/pre_shift równo** (22-29.06) — resztkowa kara `−20` (T4 KANON) + out-of-engine gates (reassignment_forward_shadow) = częściowe cofnięcie wciąż żywe. (K-D15)
- **ETA_QUANTILE_R6_BAGCAP** (recovery gold≤4) — to inwersja, którą D3 chce SKASOWAĆ (odwrotny kierunek: cofnięcie inwersji ZAMIERZONE). (K-D03)

⚠ **Nie mogę zmapować dokładnych P-numerów bez `ziomek-full-rule-audit-2026-06-24`** — zgłaszam jako kandydatów, nie pewnik. To luka pokrycia (CZĘŚĆ 5).

---

## CZĘŚĆ 4 — SYNTEZA precedence_status (macierz)

| Para | natura | precedence_status | I1 (SOFT osłabia HARD?) |
|---|---|---|---|
| K-D01 R6↔geometria | martwa-oś (geom nieczytana) | **ok** (P0 zachowany) | NIE — ale geom undefined w selekcji |
| K-D02 R1↔R6 | zsoftowany-HARD świadomy | defined-consistent | NIE (różne osie) |
| K-D03 ETA_QUANTILE↔R6 | inwersja HARD przez calib | **defined-inconsistent** (D3 usuń) | TAK (luzuje HARD R6 gold≤4) |
| K-D04 R6-35↔40 | próg-split | **defined-inconsistent** (C5 alarm-only) | częściowo (40 bez bramki alarm) |
| K-D05 R27±5↔frozen | value-HARD/window-SOFT | defined-consistent (C6) | NIE (value frozen) |
| K-D06 R-DECLARED↔R27/R6 | HARD bez runtime | defined(C-DT)/**undefined-runtime** | n/d (brak strażnika) |
| K-D07 always-propose↔HARD-reject | sentinel-pogodzenie | defined-consistent | NIE (#10 latent) |
| K-D08 LATE_PICKUP_HARD_GATE | nazwa-HARD/zach-SELEKCJA | defined-consistent + L-ryzyko | NIE |
| K-D09 RETURN-VETO | nazwa-VETO/feas-metric | defined-consistent(D4) + L/B | NIE (kanon vetuje) |
| K-D10 R-10↔LOADGOV↔stopover | stack 3 kar load | **undefined** (D5 pending) | n/d |
| K-D11 pre-shift-floor | faza-A↔faza-B | **defined-inconsistent** (K2) | n/d |
| K-D12 SLA-anchor↔R6-anchor | 2 HARD różny anchor | **defined-inconsistent/undefined** (T2/O2) | n/d |
| K-D13 COMMIT_DIVERGENCE | maskująca flaga | **silent-inversion** | n/d |
| K-D14 OR_TOOLS↔GROUPING | sprzężenie-flag | defined-consistent(C3)/brak-egzekucji | n/d |
| K-D15 equal-treatment-2flag | sprzężenie-flag | silent-inversion-potencjalna | n/d (1 flip→rozjazd) |
| K-D16 lex 4↔3-krotka | fragile-twin flaga | fragile/silent-pending(C7) | n/d |
| K-D17 R6_DANGER env-frozen | dryf-flag | undefined-control | n/d (kara ON niewidoczna) |

**Rozkład:** defined-consistent 6 · defined-inconsistent 4 · undefined 2 · silent-inversion 2 · ok 1 · fragile/sprzężenie 2.
**Twarde inwersje HARD (I1=TAK lub częściowo):** K-D03 (ETA_QUANTILE luzuje R6), K-D04 (40 bez alarm). Obie **defined-inconsistent z KANONem** (C5/D3) — to NIE „SOFT obchodzi HARD przypadkiem", to świadome rozluźnienia, które werdykt Adriana KAŻE zlikwidować/obwarować trybem ALARM. Reszta = brak-precedencji / nazwa / sprzężenie / brak-strażnika, NIE żywe naruszenie P0.

---

## CZĘŚĆ 5 — POKRYCIE (jawne luki, nie cisza)

**Zbadane (świeży grep/sed dziś):** R6 (feasibility_v2:1096-1247 + cap 40 best_effort), R1 (504-507), R5 (573), geometria (4624/4635/4826/5239), R27 (route_sim:1071-1086/1479, tsp:263-311), R-DECLARED (grep komentarzy), R-LATE-PICKUP (4569-4616/496), R-RETURN (905-914 + plan_recheck:377/1518), R-10/LOADGOV (1447/2250/2303/3394), pre-shift floor (748-794), SLA-anchor (_count_sla_violations 635-660 + feasibility 1135-1190 + r6_thermal_anchor 663-671), scoring-layer hard-rejects (4420-5651), always-propose (2638/6491-6926), objm_lexr6.lex_qual (29-49), flagi efektywne (flags.json zmierzone: 12 kluczy + 5 const-bodies common.py).

**Reguły/przyrządy A2/A3 wzięte z rejestru, NIE re-grepowane per-linia:** ~19 SOFT kar scoringu (mapowanie term→reguła = Faza B); paczka-exempt 3. site (plan/is_paczka_order); R9 wait HARD-tail confirmed consumed (`v3273_wait_courier_hard_reject:5637`) ale nie prześledzony do końca czy daje INFEASIBLE w puli czy tylko MAYBE→NO przed pulą (jest przed `:5905` → trzyma inwariant).

**LUKI (jawne):**
1. **P-1..P-7 dokładne numery** — `ziomek-full-rule-audit-2026-06-24` NIE w zasięgu; CZĘŚĆ 3 = kandydaci z lektury KANON §2/§5, nie mapa P-numerów. **Faza D/E: dociągnąć ten doc dla precyzyjnej osi cichej-inwersji-P.**
2. **Cross-repo precedencja flag** (konsola PANEL_FLAG_ vs courier_api BUILD_VIEW_ vs silnik) — A5 pokazał 3 niezależne systemy flag; TUTAJ skupiłem się na silniku. Pary cross-repo (TRUST_CANON_ORDER ×2-3 systemy) = A5/Faza J, nie re-derywowane.
3. **Wartości LICZBOWE konfliktu** (czy ETA_QUANTILE realnie przepuszcza >35 na żywo; ile worków double-karanych przez R-10+LOADGOV) — to oracle Fazy C (replay), NIE lektura. Oznaczone z kodu, nie udowodnione runtime.
4. **R6_DANGER_ZONE penalty wartość** — KANON L2 mówi „−24/min", kod `BAG_TIME_DANGER_PENALTY_PER_MIN=16` (dispatch_pipeline.py:4236). Drobny dryf doc↔kod, nie re-rozstrzygnięty (Faza E).
5. **Czy scoring-layer HARD rejecty (v324a/wait/intra/carry) realnie blokują CAŁĄ pulę** (→ best_effort) czy tylko pojedyncze kandydaty — prześledziłem że flipują MAYBE→NO przed `:5905`; pełny e2e (czy zostaje ktoś) = Faza C.

**NIE-luki (świadomie poza zakresem):** Mailek/Papu (granica). Sentinele jako klasa M (osobny agent). Floor 17-powierzchni pełna lista (A6 grupa 6 + audyt 30.06).

---

## CZĘŚĆ 6 — HANDOFF dla Faz E/F

- **Distinct-root dedup (anty-double-count):** K-D03+K-D04+K-D12 = rodzina **„R6/SLA HARD niespójny z KANONem 35-bez-wyjątków + anchor-split"** (R3 root z A6 + C5/D3/O2). NIE 3 osobne chaosy — jeden sprint O2 (02.07) + C5-pochodna #1.
- K-D01+K-D02 = **„geometria nie ma precedencji w selekcji"** (P0-A rootcause; fix = człon rozjazdu w kanonie lex_qual PO objm-unify, SOFT nie osłabia HARD R6).
- K-D06 = **„R-DECLARED-TIME jako runtime-inwariant"** (kandydat kontraktu Fazy F #4 z A2).
- K-D08+K-D09 = **ujednolicenie słownictwa HARD_GATE/VETO** (L, D4 + Faza F #5).
- K-D10 = **D5 pending Adrian** (3 podatki load) — NIE ruszać bez werdyktu.
- K-D13+K-D14+K-D15+K-D16+K-D17 = **rodzina flag-coupling/masking** → kanon stanu flag musi łączyć 3 warstwy (A3 §10); fingerprint rozszerzyć o route/canon + maskujące.
- **Faza C oracle priorytet:** K-D03 (ile >35 ETA_QUANTILE przepuszcza), K-D10 (double-load-penalty count), K-D12 (SLA vs R6 anchor delta na workach late). Wszystkie 3 napędzają flip/no-flip O2 02.07.
