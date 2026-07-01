# FAZA E DEDUP — KLASTER R1-R2 (single-source + placement)

**Tryb:** READ-ONLY. **Data:** 2026-06-30. **Sesja:** tmux 2. **Klaster:** R1 Jedno-źródło (A1·A2·J) + R2 Umiejscowienie (B·C).
**Wejście:** WF2_DIGEST.md (FAZA B/C/D, filtr klas A1/A2/B/C/J) + A1_module_layer_map + A2_rule_registry + A6_twin_import_graph (+ drill B11_J_crossrepo). Linie zweryfikowane świeżym grep dziś (HEAD silnik `8024705`); cytaty `plik:linia` DRYFUJĄ.
**Cel:** zwinąć INSTANCJE wskazujące TO SAMO ŹRÓDŁO w distinct-rooty (anty-double-count). NIE rozbijam scalonego przez A6 K1 (lex_qual/bucket/inline = 1 selekcja).

---

## ZASADA SCALANIA (anty-double-count)

- A6 już scalił **grupy 1+3+5** (lex_qual / bucket-pozycji / inline-bucket) w JEDEN korzeń selekcji **K1**. NIE rozbijam ich na 3.
- ALE K1 ma **2 OTWARTE resztki** o RÓŻNYM celu konsolidacji i sev — raportuję jako 2 rooty (R-FROZEN-LEXQUAL + R-POS-GATES), **oba seed K1**, jawnie oznaczone „2 resztki jednej unifikacji" (NIE 2 chaosy).
- Route-order J1-J8 = **1 root w 6 mechanizmach** kopii/parytetu (NIE 6 chaosów). panelsync = martwa 5. kopia (K-member, do usunięcia, NIE liczona jako żywa).
- R6-cap-scatter (35 w 5 stałych / 40 tier) = A1-sibling rootu SLA/R6-anchor; **35-vs-40 N-analiza → klaster R3-Prawda/N** (cross-ref, nie re-derywuję tu).
- Sentinele (0,0)/BIALYSTOK_CENTER/_SYNTH_POS = **K5 most**, pełny raport agent M/sentineli; tu tylko jako wyzwalacz position-gates (R-POS-GATES) i pile-on (P0-B).
- `eta_pickup` display≠decision = **klasa F / klaster R4-Semantyka** (A6 grupa 7) — NIE tu (to nie kopia-reguły).

---

## 9 DISTINCT ROOTÓW (klaster R1-R2)

### ROOT 1 — `one-route-order-module` (R1/J · K1+K7) — P1 OTWARTY ŹRÓDŁO
**Co:** kolejność JAZDY (carried-first-relax + no-return-to-departed + bundling „1 restauracja=1 podjazd") żyje w **5+ kopiach / 3 repa / 3 języki** bez wspólnego importu repo↔repo.
- ŹRÓDŁO silnik `plan_recheck.py:1478` `_apply_canon_order_invariants` (jedyny choke) → render `route_podjazdy.py:190` `order_podjazdy` (własna kopia, NIE import) → konsola `fleet_state.py:395` `_build_route` (KOPIA bez importu) → apka `courier_orders.py:1116` (import route_podjazdy za flagą, inaczej własny `_plan_stop_sequence`) → **martwa** `courier_api_panelsync/courier_orders.py:558` (665 vs 1285 L).
- Parytet: golden-test ILUZORYCZNY (J2: dwie rozłączne suity, brak asercji `order_podjazdy(X)≡_build_route(X)`); jedyny runtime-monitor `ziomek_time_route_monitor.py:385` **SAM WYGASA 2026-07-10** (J3, T-10 dni); import apki = **cichy fail-soft** do lokalnej kopii (J6); `PICKUP_MERGE_MIN=10` ręcznie ×5 (J4); 3 systemy flag TRUST_CANON inaczej-nazwane (J7).
- **instance_refs:** `plan_recheck.py:1478` · `route_podjazdy.py:190` · `nadajesz_clone/panel/backend/app/integrations/ziomek/fleet_state.py:395` · `courier_api/courier_orders.py:1116` · `courier_api_panelsync/courier_orders.py:558` · `route_podjazdy.py:30` (PICKUP_MERGE_MIN) · `nadajesz_clone/panel/backend/tools/ziomek_time_route_monitor.py:385`
- **zwija findingi:** J1·J2·J3·J4·J6·J7·J8(K)·J9(O worktree 481/225L) · B02-R2-route-order-5copies · B02-R2-2nd-canon-producer · B02-R2-panelsync-dead · B03-A2-03 · B03-A2-04(apka fallback) · B03-A2-11(carried-first subset) · B03-A2-12 · C4-b · C11(q3 renderer-equivalence) · B13-K-06(dead panelsync)
- **cel:** wspólny pakiet route-order importowany przez 3 repa (źródło=engine) LUB twardy golden-fixture equivalence na wspólnym wejściu; usunąć martwy panelsync; PICKUP_MERGE_MIN 1 stała; parytet zanim monitor wygaśnie.
- **why_recurs:** twin-scatter + path-asymmetry (carried-first dostała konsola, apka NIE → 44-75/d) + cross-repo (brak importu, golden iluzoryczny, monitor wygasa).

### ROOT 2 — `one-delivery-eta-source` (R1/A2 · K1, 6. distinct-root) — P2 OTWARTY ŹRÓDŁO
**Co:** ETA dostawy liczona w **3-4 niezależnych implementacjach**; `live_eta_cache` to override-świeżościowy (TTL 8min), NIE single-source — gdy wpis stale każda powierzchnia spada na własne liczenie.
- silnik `chain_eta.compute_chain_eta:45` / route_simulator predicted_delivered_at · apka `courier_orders.py:794` `_compute_live_eta`/`:822` `_attach_fallback_eta` (własny OSRM+haversine) · konsola `fleet_state.py:250` `_eta_chain` (własny OSRM) · klient `canon_eta.py:37` (czyta courier_plans delivery_eta, NIE cache). Czytnik cache ZDUBLOWANY (konsola JSON / apka in-process import).
- **instance_refs:** `chain_eta.py:45` · `courier_api/courier_orders.py:794` · `nadajesz_clone/panel/backend/app/integrations/ziomek/fleet_state.py:250` · `nadajesz_clone/panel/backend/app/integrations/ziomek/canon_eta.py:37` · `nadajesz_clone/panel/backend/app/integrations/ziomek/feed.py:700`
- **zwija findingi:** B03-A2-01 · B03-A2-06(czytnik cache dubbed) · B03-A2-10(canon_eta 4. czytnik) · J5(3 implementacje ETA)
- **cel:** jedno źródło ETA-dostawy (wspólny chain-eta cross-repo) LUB `live_eta_cache` autorytatywne z fail-closed gdy stale (nie per-powierzchnia self-compute).
- **why_recurs:** twin-scatter (każda powierzchnia własny OSRM+fallback) + czytnik cache 2 implementacje.

### ROOT 3 — `frozen-lexqual-shadow` (R1/A1+E · K1 resztka a) — P2 OTWARTY (latent/FRAGILE) ŹRÓDŁO
**Co:** cień selekcji `_objm_lexr6_shadow._lex_qual` (`dispatch_pipeline.py:1122`) ZAMROŻONY jako 3-krotka HARD-CODED, kanon `objm_lexr6.lex_qual:29` warunkowo 3/4-krotka (prepend `post_shift_overrun_penalty` gdy `ENABLE_POST_SHIFT_OVERRUN_PENALTY` ON, l.44). Zgodne TYLKO bo flaga OFF (wiodące 0.0 no-op) → flip → cień rankuje INACZEJ niż live = kłamiący przyrząd (E #15).
- ⚠ bucket-część `_objm_lexr6_shadow` ZOSTAŁA scalona na `_selection_bucket` (l.1119) — rozjazd siedzi WYŁĄCZNIE w `_lex_qual` (lex-część). To JEDYNA otwarta inline-resztka klucza selekcji.
- **instance_refs:** `dispatch_pipeline.py:1122` · `objm_lexr6.py:44` · `dispatch_pipeline.py:1097`(_objm_lexr6_shadow) · `dispatch_pipeline.py:591`(_late_pickup_score_first vs slot 0.0 — postshift 3-ways)
- **zwija findingi:** B01-lexqual-frozen-inline · B01-postshift-3ways · B04-F3 · B07-E3b-objm-shadow-twin · C05(_objm_lexr6_shadow UNTESTED, SHADOW flag OFF) · M3-objm-postshift-4tuple-off · D01/D02/D03 silent-inversion lex_qual
- **cel:** przepiąć `_objm_lexr6_shadow._lex_qual` na `objm_lexr6.lex_qual` po PASS at#152/at-200 (03.07); golden-test shadow≡kanon przy OBU stanach POST_SHIFT.
- **why_recurs:** twin-scatter (1 frozen inline-kopia klucza jakości; by-design zamrożony pod walidację, nawrót na flipie).

### ROOT 4 — `out-of-engine-position-gates` (R1/A1+B+J+M · K1 resztka b) — P1 OTWARTY ŹRÓDŁO
**Co:** równość pozycji no_gps/pre_shift (Adrian C3 HARD) scalona w silniku na `_selection_bucket:2451` (8 engine-twins UNIFIED), ALE **3+ bramki POZA silnikiem** trzymają własną dyskryminację pozycji — „klasa wraca ≥4×" bo ŻADEN test nie wiąże ich z kanonem.
- `tools/reassignment_forward_shadow.py:64` `_SYNTH_POS={none,pin,pre_shift,""}` + `:260` `a_late=(a_cand is None)` (59% fałszywych ratunków; VALIDATED suppressed od env-flip 29.06 ale klasyfikator NIGDY niezrównany z silnikiem) · `auto_assign_gate.py:163` G7 `pos_not_informed` (LATENT, AUTO OFF) · `feed.py:239/258` overlay quality_reassign BEZ `_pos_trusted` (Telegram MA filtr) · cross-axis `_demote_blind_empty:2504`.
- **instance_refs:** `dispatch_pipeline.py:2451`(kanon _selection_bucket) · `tools/reassignment_forward_shadow.py:64` · `tools/reassignment_forward_shadow.py:260` · `auto_assign_gate.py:163` · `nadajesz_clone/panel/backend/app/integrations/ziomek/feed.py:239` · `dispatch_pipeline.py:2504`(_demote_blind_empty)
- **zwija findingi:** B01-g7-informed-check · B03-A2-05 · B04-F1 · B04-F2 · J11 · C04(_SYNTH_POS VOID, would-vs-quality dwie bramki) · D01/D02/D04 equality-inversion-stack(FAR-veto -1000 kept, warm-up -20, demote cross-axis)
- **cel:** związać `_SYNTH_POS`/auto_assign G7/feed.py z `_selection_bucket` (jedna polityka pozycji) + golden single-source out-of-engine; ruszać WSZYSTKIE 8 bliźniaków RAZEM.
- **why_recurs:** path-asymmetry (engine UNIFIED, gates DIVERGED; brak testu wiążącego) + twin-scatter (łatane ≥4×) + K5 most (_SYNTH_POS = sentinel-jako-klasyfikator).

### ROOT 5 — `one-sla-r6-anchor` (R1/A1+C+N · K1+K3) — P2 OTWARTY ŹRÓDŁO
**Co:** SLA-anchor = **2 inline-lustra** kotwiczące na `pickup_at` (TSP-projected), R6-thermal kotwiczy na `pickup_ready_at` (gotowość) — DWIE HARD-bramki tej samej decyzji liczą RÓŻNY anchor; +asymetria paczka-exempt.
- `route_simulator_v2.py:635` `_count_sla_violations` (pickup_at, BEZ paczka-exempt) · `feasibility_v2.py:1135/1156` SLA-loop (pickup_at, MA paczka-exempt :1152 → rozjazd A↔B na paczkach) · `route_simulator_v2.py:663` `r6_thermal_anchor` (ready_at, INV-R6-ANCHOR) · `plan_recheck.py:683/1670` `_o2_key` (czyta precomputed sla_violations) · `sla_tracker.py:267` (3. kotwica now−picked_up_at) · `bundle_calib_shadow.py:224` (4. wariant carried=min()).
- **instance_refs:** `route_simulator_v2.py:635` · `feasibility_v2.py:1135` · `route_simulator_v2.py:663` · `plan_recheck.py:683` · `sla_tracker.py:267` · `feasibility_v2.py:1152`(paczka-exempt asym)
- **zwija findingi:** B01-SLA-mirror-paczka · B01-SLA-anchor-vs-r6thermal · B01-o2key-3copies · B02-R3-sla-anchor-2mirrors · B02-R3-sla_tracker-3rd-anchor · B02-W3-pickup_ready-parse-twin · B04-F9 · C01(carried-anchor PARYTET-GAP) · D01/D02/D04/D05 anchor-split-brain
- **cel:** jeden ready-anchored SLA/R6 anchor (wspólna funkcja zamiast 2 inline-lustra); paczka-exempt spójny we wszystkich site (też O2 sweep, 4. site flip 02.07); golden A≡B.
- **why_recurs:** twin-scatter (2 inline-lustra ręczne, brak wspólnej funkcji/golden) + N (anchor niespójny). **A1-sibling (cross-ref klaster N):** R6=35 re-zakodowane 5× (`BAG_TIME_HARD_MAX_MIN`+`DEFAULT_SLA_MINUTES`+`C2_PER_ORDER_THRESHOLD_MIN`+`O2_OVERAGE_CAP_MIN`+`O2_CAP_Z_MIN`) + literal 35 w `plan_recheck:699/1668` (B01-r6cap-*) — 35-vs-40 tier = N/R3-Prawda.

### ROOT 6 — `earliest-pickup-floor-no-chokepoint` (R1/A1+A2+H · K1+K2+K4) — P1 OTWARTY ŹRÓDŁO
**Co:** NIE istnieje single-source `available_from=max(now,shift_start)` (grep `available_from`=∅, grep runtime-guard=∅). **17 powierzchni** liczy czas-najwcześniejszego-odbioru, **4 mają floor**, 0 runtime-inwariantu.
- **Najszerszy leak (K2 cofacz):** `plan_recheck.py:554` `_start_anchor`/`:534` `_earliest_committed_pickup_anchor` kotwiczą committed/GPS/last_pos ale NIGDY shift_start → regen `courier_plans.json` co 5min ODCLAMPOWUJE to co `feasibility_v2.py:794` (`PRE_SHIFT_DEPARTURE_CLAMP`) sclampowało. Chokepoint `state_machine.py:551` zapisuje committed BEZ floor. `shift_start` liczony NIEZALEŻNIE silnik `courier_resolver.py:1252` (datetime) vs konsola `fleet_state.py:858` (HH:MM) — cross-repo dryf definicji. Render-łatka `fleet_state.py:853` `CLAMP_PRESHIFT_PICKUP_ETA` floruje TYLKO ścieżkę OSRM.
- **Szersza rama (D05 4-clamp):** precedencja frozen(R27)>floor(shift_start)>OSRM bez chokepointu; committed-pickup R27 egzekwowany 4 arytmetykami / 7 powierzchni (B03-A2-02); frozen AKTYWNIE broni złego pre-shift czasu (czasówka/elastyk committed<shift_start) → floor na OSRM no-op.
- **instance_refs:** `courier_resolver.py:1383`(brak available_from) · `plan_recheck.py:554`(leak K2) · `feasibility_v2.py:794`(clamp — ma floor) · `state_machine.py:551`(chokepoint bez floor) · `nadajesz_clone/panel/backend/app/integrations/ziomek/fleet_state.py:858`(shift_start indep) · `nadajesz_clone/panel/backend/app/integrations/ziomek/fleet_state.py:853`(CLAMP render-łatka)
- **zwija findingi:** B02-R4-available_from-missing · B02-R4-plan_recheck-floor-leak · B02-R4-state_machine-chokepoint · B02-R4-shift_start-indep-console · B03-A2-02(committed-pickup 4 arytmetyki) · B03-A2-07 · B03-A2-09(feasibility↔plan_recheck) · C4-a(render-clamp konsoli) · H1b/H3(plan_recheck cofacz) · D01/D02/D04/D05 floor+4-clamp
- **cel:** jedno źródło `courier.available_from=max(now,shift_start)` + runtime-inwariant `pickup≥shift_start` we WSZYSTKICH 17 powierzchniach (w t.cz. plan_recheck regen); jeden chokepoint czasu-odbioru (frozen>floor>OSRM); usunąć render-clamp łatki.
- **why_recurs:** twin-scatter (każda powierzchnia re-liczy/pomija) + path-asymmetry (feasibility clampuje fazę A, plan_recheck regen odclampowuje fazę B) + edge-patch (CLAMP render-clamp = łatka na renderze nie u źródła).

### ROOT 7 — `geometry-blind-selection` (R2/C+A1+K · P0-A + K5) — P1 OTWARTY ŹRÓDŁO
**Co:** geometria rozjazdu = **SOFT-only w L6 score**, ZERO osi w HARD-feasibility i ZERO w kluczu selekcji `lex_qual` (czysto czasowy: R6-breach→committed-late→new-pickup-late). best_effort override wyrzuca ostatni ślad geometrii (niesiony przez −score) pod scarcity. Jedyna HARD-geom (R7) zneutralizowana `LONG_HAUL_DISTANCE_KM=99` (fizycznie nieosiągalne). Jedyna L8-eskalacja `geometry_blind_fallback:6443` wymaga feasible≥2 → NIE odpala przy pool=0.
- **instance_refs:** `objm_lexr6.py:29`(lex_qual czysto-czasowy) · `feasibility_v2.py:504`(R1 spread metric-only) · `feasibility_v2.py:486`(R7 dead 99km) · `dispatch_pipeline.py:6443`(eskalacja wymaga feasible≥2) · `feasibility_v2.py:90`+`common.py:2280`(2 stałe 8.0 deliv_spread) · `wave_scoring.py:242`+`geometry.py:30`(bearing 2 kopie)
- **zwija findingi:** C1-a · C1-b · C1-c · B01-geom-spread-2caps · B01-bearing-2copies · B13-K-04(R7 dead) · C10(finding G: global_allocate geometrycznie ślepy POTWIERDZONY) · D01/D02 geometria-SOFT-only
- **cel:** geometria jako oś w kluczu selekcji (lex_qual/objektyw) LUB reaktywować R7 jako HARD/soft-geom-bramkę; eskalacja geometry-blind działa też przy pool=0; 1 stała MAX_DELIV_SPREAD.
- **why_recurs:** N-D (kanoniczny C1 — decyzja geometrii istnieje WYŁĄCZNIE jako SOFT-kara w score, którą best_effort wyrzuca; świadomy dług, ale oś martwa pod scarcity).

### ROOT 8 — `no-global-deconflict-new-order` (R2/B+C+M · P0-B + K6 + K5) — P1 OTWARTY ŹRÓDŁO
**Co:** sekwencyjna de-konflikcja floty (global_allocate+claim) zbudowana i LIVE TYLKO dla PRZERZUTU; dla NOWEGO zlecenia silnik proponuje **per-event greedy bez claim** → pile-on jednego kuriera (case „Paweł Ściepko → 2 restauracje naraz").
- `tools/pending_global_resweep.py:421` = warning no-op (PENDING_RESWEEP_LIVE niewpięte). **Most do P0-A:** de-pile count VALIDATED (C10) ale geometria ŚLEPA — 35% worków de-pile R1-łamiące (spread>8km). **Most do K5:** sentinele kurczą pulę → geometria-ślepy pile-on: `dispatch_pipeline.py:5695` `_v328_eval_safe` catch-all wyrzuca zajętego kuriera, `:2147` `_compute_repo_cost_km` (0,0)→kara dead-headu znika→worek wygląda TAŃSZY.
- **instance_refs:** `tools/pending_global_resweep.py:421` · `objm_lexr6.py:29`(lex_qual geom-blind feeds pile-on) · `dispatch_pipeline.py:5695`(_v328 catch-all pool-shrink) · `dispatch_pipeline.py:2147`(repo_cost (0,0)) · `tools/reassignment_global_select.py`(de-pile count)
- **zwija findingi:** B04-F4 · C10(pending_global_resweep no-op P0-B; global_allocate geometria-VOID) · M-2 · M-4(sentinele→tańszy worek) · B07-E2-V328-massfail
- **cel:** jedna de-konflikcja globalna (claim) dla NOWEGO zlecenia jak dla przerzutu (PENDING_RESWEEP_LIVE engine-level); de-pile MUSI być geometria-aware (wejść RAZEM z P0-A, inaczej no-op).
- **why_recurs:** path-asymmetry (zbudowane dla przerzutu, nowe zlecenie shadow-only) + most P0-A(geom-blind) + K5(sentinel pool-shrink) — DEKLAROWANE w seed: „wejść RAZEM z P0-A geometria w lex_qual (osobno=no-op)".

### ROOT 9 — `hard-feasibility-split-layer` (R2/C+I · wzorzec#10 + P0-inwariant) — P2 OTWARTY (latentna mina) ŹRÓDŁO
**Co:** kontrakt P0 „HARD-przed-SOFT" rozmyty — HARD-decyzje i przynależność do puli przeciekają do warstwy scoringu L6 / re-admisji.
- `dispatch_pipeline.py:5637` HARD-rejecty R9>20 (`scoring.py:150` zwraca `(0.0,True)`) + v324a-ext + carry_chain + intra-gap liczone w L6, aplikowane jako verdict-override MAYBE→NO (C-adj-1: SAFE-by-construction, monotonic, D01 „[ok]") · `dispatch_pipeline.py:6266` FEAS_CARRY_READMIT promuje verdict=NO→MAYBE na top[0] ~360L ZA jednorazowym guardem `_assert_feasibility_first:5938` (C3: latentna mina na flipie, dziś flaga OFF) · `dispatch_pipeline.py:3620` soon-free busy→free look-ahead w L6 scoringu, nie L4 puli (C2) · `feasibility_v2.py:905` R-RETURN-VETO metric-only, realny zakaz L9 plan_recheck (C-adj-2: nazwa-vs-zachowanie).
- **instance_refs:** `dispatch_pipeline.py:5637`(R9 tail L6) · `dispatch_pipeline.py:6266`(FEAS_CARRY readmit za guardem) · `dispatch_pipeline.py:3620`(soon-free L6 nie L4) · `feasibility_v2.py:905`(R-RETURN metric-only split-enforce)
- **zwija findingi:** C-adj-1 · C-adj-2 · C2 · C3 · NON1(R9 tail LIVE-correction) · D02/D04 silent-inversion-P(HARD-bypass po guardzie)
- **cel:** HARD-decyzje + przynależność do puli w JEDNEJ warstwie (HARD=L5 feasibility / pula=L4); readmit i look-ahead NIE obchodzą guarda P0 (mutacja top[0] w jego zasięgu); nazwa=zachowanie (VETO/HARD_GATE — L słownictwo).
- **why_recurs:** path-asymmetry (HARD logika rozsiana L5+L6+L9; guard ślepy poza call-site). C-adj-1 dziś SAFE (monotonic), C3 readmit = mina pod flipem ENABLE_FEAS_CARRY_READMIT.

---

## MERGED COUNT

**~70 surowych instancji** (FAZA B+C+D, klasy A1/A2/B/C/J + seedy P0-A/P0-B) → **9 distinct rootów**:
- **5 z A6** (frozen-lexqual=ROOT3, position-gates=ROOT4, route-order=ROOT1, sla-r6-anchor=ROOT5, floor-17=ROOT6)
- **+1 6. kandydat** B03-A2-01 (one-delivery-eta-source=ROOT2)
- **+P0-A** geometry-blind (ROOT7) **+P0-B** no-global-deconflict (ROOT8)
- **+1 placement-C** hard-split-layer (ROOT9)

**Anty-double-count:**
- ROOT3+ROOT4 = **2 OTWARTE resztki JEDNEGO unified-K1-selekcja** (NIE 2 osobne chaosy; engine UNIFIED na `_selection_bucket`+`objm_lexr6.lex_qual`, otwarte tylko frozen lex-shadow + out-of-engine gates). A6 grupa 5 ⊂ ten korzeń (scalone, nie liczone).
- ROOT1 = **1 root w 6 mechanizmach** (J1-J8); panelsync = martwa 5. kopia (K, do usunięcia, NIE żywa).
- ROOT5 sibling R6-cap-35-scatter → **N/R3-Prawda** (cross-ref); ROOT7+ROOT8 sprzężone („wejść RAZEM").
- Sentinele = **K5 most** (agent M); `eta_pickup` display≠decision = **F/R4-Semantyka** (NIE tu).

**Cross-cluster (NIE rooty tu, jawne odesłania):** serializer A+B compute-but-vanish (B02-serializer / B07-E-ROOT) → E/R3-Prawda · haversine 6-kopii-guard (B02-W2) → K5/M · USE_V2_PARSER (B02-W1/J13) → D2/cross-proces · fleet-builder twin (B02-W4) = ZAMKNIĘTY (unified).
