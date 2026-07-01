# D05 — MAPA KONFLIKTÓW: frozen × floor × thermal (lane D, oś I)

**Faza 1 audytu spójności Ziomka · TRYB READ-ONLY · sesja tmux 2 · 2026-06-30 ~14:1x UTC**
**Agent:** D05-frozen-floor-thermal. **Cel:** graf interakcji reguł/flag z rodziny committed-time × R6-thermal × pre-shift-floor. Dla KAŻDEJ pary: rule_a, rule_b, natura, precedence_status, dowód.
**Wszystkie `plik:linia` ze ŚWIEŻEGO grepu DZIŚ** (HEAD recon `8024705`; linie dryfują — re-grepuj). Bazuje na A2 (rule-registry) + A3 (flag-registry) + A6 (twin-graf grupa 4/6) + seed TOP-3 (preshift-floor-audit #1).

---

## 0. TL;DR — 7 par konfliktowych, 1 rdzeń architektoniczny

**Rdzeń:** „czas odbioru" ma **4 niezależne autorytety** (frozen-committed R27 / pre-shift-floor shift_start / surowy-OSRM / debias) i **2 niezgodne kotwice termiczne** (ready_at vs pickup_at), **bez jednego chokepointu precedencji**. Każda powierzchnia (silnik-feasibility / silnik-TSP / silnik-plan_recheck / apka courier_orders / konsola fleet_state) wybiera inny podzbiór i inną kolejność → floor (shift_start) jest **no-op przeciw frozen**, a SLA-gate liczy luźniejszą kotwicę niż R6-gate w TEJ SAMEJ funkcji.

| # | rule_a | rule_b | natura | precedence_status |
|---|---|---|---|---|
| **K1** | frozen-R27 (committed nietykalny) | pre-shift floor (pickup ≥ shift_start) | **cicha-inwersja**: floor żyje na ścieżce OSRM, frozen ją omija → floor martwy gdy committed<shift_start | **silent-inversion** |
| **K2** | R6-thermal gate (anchor=pickup_ready_at) | SLA gate (anchor=plan.pickup_at) | **niespójna kotwica** tej samej wielkości (carry-time), 2 HARD-bramki, oba próg 35 | **defined-inconsistent** |
| **K3** | PACZKA_R6_THERMAL_EXEMPT (3 HARD sites) | O2-objektyw + SLA-count (bez exempt) | **sprzężenie-flag**: bramka zwalnia paczkę, ranking ją karze | **silent-inversion / undefined** |
| **K4** | early-bird/czasówka ≥60 (order-level KOORD) | pre-shift floor (courier-level feasibility) | **niezdefiniowana-precedencja** na granicy 60 min + released-czasówka<shift_start | **undefined** |
| **K5** | precedencja 4 clampów (frozen>floor>OSRM; debias-sierota) | — (meta-konflikt) | **brak jednego chokepointu**; debias shadow-only nie dożywa do floor | **undefined** |
| **K6** | R6 flat-35 (feasibility HARD) | tier-40 cap (best_effort objm) + bundle_calib flat-35 | **rozsyp progów** tej samej reguły R6 | **defined-inconsistent (N)** |
| **K7** | SLA-gate kolejność PRZED R6-gate (feasibility) | plan.sla_violations (luźny count) → `_o2_key` ranking | **cicha-inwersja-semantyki**: zdominowana bramka, ale jej count przecieka do selekcji | **silent-inversion** |

**Decyzje Adriana 30.06 (z preshift-floor-audit §8) — kotwica dla precedencji docelowej:** Q2 „deklaracja restauracji NIETYKALNA → nie dawaj zlecenia pre-shift kurierowi który nie zdąży (zmieniaj KTO, nie czas)"; Q1 „OBA: jedno źródło floor (commit+rendery) + twardsza feasibility"; Q2b „floor obejmuje pre_shift+no_gps". → Docelowo: **frozen/committed > floor** (committed wins), ale feasibility wyklucza pre-shift kuriera, który nie zdąży na committed. Dziś ani jedno ani drugie NIE jest egzekwowane spójnie.

---

## K1 — frozen-R27 (committed) ↔ pre-shift floor (shift_start) ★ HEADLINE (silent-inversion)

**rule_a — frozen-R27 (committed czas_kuriera NIETYKALNY, OSRM nie nadpisuje):**
- Silnik TSP (okno R27 ±5, SOFT): `route_simulator_v2.py:1071` `ENABLE_V3274_FROZEN_PICKUP_WINDOW` → `:1086` `window_open = max(0.0, open_min − V3274_FROZEN_PICKUP_WINDOW_MIN)`; OR-Tools `tsp_solver.py:263/290-311` `SetCumulVarSoftUpperBound` (3 miękkie boundy na węźle pickup: R27-window + committed-punctuality N5 + FRESH-ready).
- Apka render: `courier_api/courier_orders.py:872` `if config.FROZEN_PICKUP_ETA:` → `:874` `cand = plan_pickup_iso OR committed_iso`; `:888-893` `eta = frozen_iso`, OSRM NIE nadpisuje (`config.FROZEN_PICKUP_ETA` default ON `config.py:111`). Floor: `_committed_pickup_eta:641-660` = `max(predicted, czas_kuriera, gotowość)`.
- Konsola render: `fleet_state.py:509` `_pin_agreed_pickup = flag("PIN_AGREED_PICKUP_TIME")` → `:519-521` `pin_pickup` ⇒ `chosen = plan_pv` (frozen), OSRM użyty TYLKO `if chosen is None` (`:522`). `:544` „ODBIORY NIETKNIĘTE (frozen czas umówiony, fix 2026-06-19)".

**rule_b — pre-shift floor (pickup ≥ shift_start):**
- Silnik feasibility (departure clamp): `feasibility_v2.py:794-800` `if ENABLE_PRE_SHIFT_DEPARTURE_CLAMP and pos_source in (pre_shift,no_gps) and shift_start>now → earliest_departure = shift_start`. Działa na START SYMULACJI (route departure), NIE na wartości węzła pickup.
- Konsola clamp: `fleet_state.py:853-861` `if clamp_preshift_eta and not on_shift and shift_start → _depart_after = shift_start` → `_build_route(..., depart_after=_depart_after)` → `_eta_chain:264` `base = depart_after if depart_after>now else now`. **Działa TYLKO na łańcuch OSRM** (`_eta_chain`), NIE na frozen/pin.

**Natura — CICHA INWERSJA (cicha-inwersja-P / silent-inversion):** floor (shift_start) jest aplikowany WYŁĄCZNIE na ścieżce OSRM/departure. Frozen-pickup (R27 committed / pin) **omija OSRM**: `fleet_state.py:521-522` wybiera `plan_pv` zanim dotknie `osrm[i]` (gdzie żyje `depart_after`); `courier_orders.py:875-876` floruje frozen TYLKO do `gotowość`, NIE do shift_start („frozen nigdy < **gotowość**", nie shift_start). Gdy committed `czas_kuriera` < shift_start (LEGALNE: czasówka/elastyk committed pre-shift, first_acceptance), frozen **aktywnie zatrzaskuje** złą godzinę, a floor jest **no-op** (działa na innej ścieżce/wartości). Seed TOP-3 #1 (preshift-floor-audit): „Frozen-pickup AKTYWNIE broni złego czasu; floor MUSI trafić w wartość committed (chokepoint), uszeregowany względem frozen".

**precedence_status — UNDEFINED → de-facto silent-inversion:** nigdzie nie ma jawnej reguły „floor vs frozen". De-facto frozen > floor (frozen wybierany pierwszy, omija ścieżkę floor). Ale to NIE jest świadoma decyzja — to artefakt rozmieszczenia (floor na OSRM-path, frozen przed OSRM-path). Zgodne z Adrian Q2 KIERUNKIEM (committed nietykalny) ale BEZ drugiej połowy (feasibility ma wykluczyć pre-shift kuriera który nie zdąży — patrz K4) → dziś frozen broni czasu którego floor miał pilnować, bez bezpiecznika feasibility.

**Dowód:** `courier_orders.py:875-876` komentarz „frozen nigdy < gotowość" (NIE shift_start); `fleet_state.py:519-522` (plan_pv przed osrm); `fleet_state.py:264` (depart_after tylko base OSRM); `feasibility_v2.py:794-800` (clamp na earliest_departure, nie na committed). Asymetria: clamp pos_source-gated `(pre_shift,no_gps)` — `feasibility_v2.py:796` — vs frozen agnostyczny pozycji.

---

## K2 — R6-thermal anchor (pickup_ready_at) ↔ SLA-gate anchor (pickup_at) ★ (defined-inconsistent)

**rule_a — R6 thermal HARD gate (anchor = pickup_ready_at, gotowość jedzenia):**
- `feasibility_v2.py:1046` `anchor, anchor_src, is_picked = r6_thermal_anchor(o, is_new, plan.pickup_at, now)` → `route_simulator_v2.py:663-693` `r6_thermal_anchor`: picked_up→`picked_up_at`; inaczej→**`pickup_ready_at`** → tsp `pickup_at` → now.
- HARD-reject: `feasibility_v2.py:1105` `if _gate_bt > C.BAG_TIME_HARD_MAX_MIN(35) and not _paczki_only_mix and not _o_paczka_exempt` → `:1217-1223` `return ("NO", "R6_per_order_>35min ... thermal anchor=ready_at")`.
- Komentarz `feasibility_v2.py:1009-1011`: „Dlaczego NIE plan.pickup_at: TSP może projektować pickup later niż ready_at (np. +37 min gdy kurier zajęty), **maskując 70+ min real thermal**".

**rule_b — SLA HARD gate (anchor = plan.pickup_at, TSP-projected):**
- Wejście: `feasibility_v2.py:1135` `if plan.sla_violations > 0:` (count z `route_simulator_v2.py:635` `_count_sla_violations` — anchor `:648-656` `pickup_at[oid]` → `picked_up_at` → `now`).
- Detal+reject: `feasibility_v2.py:1156-1164` `if o.order_id in plan.pickup_at: pu = plan.pickup_at[oid]` → `:1166 if elapsed > sla_minutes` → `:1203-1209 return ("NO", "sla_violation ...")`. `DEFAULT_SLA_MINUTES = 35` (`feasibility_v2.py:53`).

**Natura — NIESPÓJNA KOTWICA (A1/N, dwie kotwice tej samej wielkości):** R6 i SLA mierzą TĘ SAMĄ wielkość fizyczną (carry-time jedzenia od gotowości do dostawy), oba HARD-bramki, oba próg 35 — ale R6 kotwiczy na `pickup_ready_at` (ostry/poprawny), SLA na `plan.pickup_at` (TSP-projected, optymistyczny). Gdy TSP planuje pickup później (kurier zajęty), SLA-elapsed < R6-elapsed → SLA luźniejsza. **`r6_thermal_anchor` docstring (`route_simulator_v2.py:664-667`) JAWNIE twierdzi „JEDNO źródło kotwicy termicznej R6 — route_simulator ORAZ feasibility MUSZĄ liczyć tym samym anchorem" — ale `_count_sla_violations` NIE woła `r6_thermal_anchor` (własna inline pętla pickup_at), a SLA-loop w feasibility (`:1156`) re-derywuje pickup_at.** To DOKŁADNIE kotwica, którą komentarz R6 25 linii wyżej (`:1009`) nazywa BŁĘDNĄ.

**precedence_status — DEFINED-INCONSISTENT:** kolejność w funkcji: SLA-gate (`:1135-1209`) PRZED R6-gate (`:1210-1223`). SLA luźniejsza → rzadko bije pierwsza → R6 jest realnym protektorem, SLA-gate **zdominowana** (patrz K7). Ale `plan.sla_violations` (count z luźnej kotwicy) przecieka do `_o2_key` rankingu (plan_recheck:690). 3 bliźniaki SLA-anchor (A6 grupa 4): `route_simulator._count_sla_violations` + `feasibility_v2` SLA-loop + `plan_recheck._o2_key` — wszystkie pickup_at; R6-anchor (route_simulator + feasibility) ready_at → **bliźniaki ROZJECHANE**. Przedmiot sprintu O2 review 02.07.

**Dowód:** `route_simulator_v2.py:648` (`pickup_at[oid]` w `_count_sla_violations`) vs `:683-686` (`pickup_ready_at` w `r6_thermal_anchor`); `feasibility_v2.py:1156` (SLA pickup_at) vs `:1046` (R6 ready via anchor); `feasibility_v2.py:1009-1011` (komentarz że pickup_at maskuje thermal).

---

## K3 — PACZKA_R6_THERMAL_EXEMPT (3 HARD sites) ↔ O2-objektyw + SLA-count (bez exempt) (silent-inversion / sprzężenie-flag)

**rule_a — paczka exempt z R6 35min (Adrian 2026-06-15; firmowe paczki = NIE gorące jedzenie):**
- Site 1 (R6 termik feasibility): `feasibility_v2.py:1050-1055` `_o_paczka_exempt = C.flag("ENABLE_PACZKA_R6_THERMAL_EXEMPT") and _is_paczka_sim(o)` → `:1080` `if (not _o_paczka_exempt) and bag_time>r6_max` + `:1105` `... and not _o_paczka_exempt` (nie liczy do r6_max, nie do violations).
- Site 2 (SLA-detail feasibility): `feasibility_v2.py:1152-1155` `if ENABLE_PACZKA_R6_THERMAL_EXEMPT and _is_paczka_sim(o): continue` (pomija jako SLA-violation).
- Site 3 (kanon `is_paczka_order`): `common.py:3479` `def is_paczka_order` + `:3415` `PACZKA_ADDRESS_IDS = frozenset({161,232,233,234,235,236})`. Flaga: `common.py:3429` `ENABLE_PACZKA_R6_THERMAL_EXEMPT` (env-default OFF; A3 effective=ON via flags.json).

**rule_b — kotwice termiczne BEZ exempt (przeciekają paczkę do rankingu/objektywu):**
- `_count_sla_violations` (`route_simulator_v2.py:635-660`) — **ZERO exempt** → `plan.sla_violations` LICZY paczkę jako naruszenie. Ten count: (a) bramkuje wejście do SLA-block `feasibility_v2.py:1135`, (b) zasila `_o2_key` `plan_recheck.py:690` `return (p.sla_violations, dur)` (gdy O2 OFF).
- `_compute_per_order_delivery_minutes` (O2 objektyw, `route_simulator_v2.py:696-728`) — woła `r6_thermal_anchor` ale **ZERO exempt** → `o2_score`/`max_carried_age` liczą thermal paczki. Konsumpcja: `_o2_key` `plan_recheck.py:687-689` `_over_z = (max_carried_age > Z); _o2 = o2_score` (gdy `ENABLE_O2_READY_ANCHOR_SWEEP` ON).

**Natura — SPRZĘŻENIE-FLAG + cicha-inwersja:** flaga exempt (HARD-gate carve-out) zaaplikowana w 3 miejscach bramki, ale warstwy RANKINGU/OBJEKTYWU (SLA-count → O2-key, O2-sweep) jej NIE honorują. Skutek: paczka NIE jest odrzucona (bramka OK), ale jest **rankowana jakby była gorąca/spóźniona** (niższy priorytet w `_o2_key` / `_sweep`). Bramka mówi „paczka wolna od R6", selekcja mówi „paczka łamie SLA" → inwersja intencji exempt w warstwie 7/9.

**precedence_status — UNDEFINED / silent-inversion + FLIP-MINA (C2/C3):** dziś O2 OFF (`ENABLE_O2_READY_ANCHOR_SWEEP=False`, `common.py:249`) → `_o2_key` używa `p.sla_violations` (count Z paczką). Przy FLIPie 02.07 (O2 ON) → `_o2_key` używa `o2_score`/`max_carried_age` (też Z paczką, bo `_compute_per_order_delivery_minutes` bez exempt). **To „4. site missing" z protokołu** (`ziomek-change-protocol.md:96` „3 HARD sites, brak w O2 — 4. site na flipie 02.07"). Flip O2 bez dodania exempt do `_compute_per_order_delivery_minutes` = regres rankingu paczek (C3 flaga sprzężona: `ENABLE_O2_READY_ANCHOR_SWEEP` MUSI iść z exempt-w-O2).

**Dowód:** `feasibility_v2.py:1050-1055/1080/1105/1152-1155` (exempt obecny); `route_simulator_v2.py:635-660` (`_count_sla_violations` bez exempt) + `:696-728` (`_compute_per_order_delivery_minutes` bez exempt); `plan_recheck.py:687-690` (`_o2_key` czyta sla_violations/o2_score/max_carried_age bez filtra paczki).

---

## K4 — early-bird/czasówka ≥60 (order-level KOORD) ↔ pre-shift floor (courier-level) (undefined-precedence)

**rule_a — early-bird/czasówka ≥60 → KOORD (order-level, PRZED pulą):**
- `dispatch_pipeline.py:3503-3548` `if pickup_at_for_early_bird ... minutes_ahead >= _early_bird_threshold_min()(60)` → `return PipelineResult(verdict="KOORD", reason="early_bird")` — **zwiera obwód PRZED budową puli feasibility** (`:2604` komentarz). Anchor: `:3497` `pickup_at_for_early_bird = RAW pickup_at_warsaw` (deklaracja restauracji), NIE committed/extended.
- Czasówka: `auto_koord.py:32` `CZASOWKA_THRESHOLD_MIN = 60`; `:41 is_czasowka(prep>=60)`; trzymana w Koordynator cid=26 (`common.py:1798`). `common.py:3413` „czasówki trzymają KONKRETNĄ godzinę".

**rule_b — pre-shift floor (courier-level, w feasibility):**
- Gate3 HARD-reject: `feasibility_v2.py:748-759` `too_early_min = shift_start − pickup_ref; if > V325_PRE_SHIFT_HARD_REJECT_MIN(30) → NO PRE_SHIFT_TOO_EARLY`. **Anchor `pickup_ref` = `pickup_ready_at`** (`:670`, gdy brak chain_eta), NIE raw pickup_at_warsaw, NIE committed.
- Warm-up soft `:760-763` `[shift−30, shift) → −20` (`V325_PRE_SHIFT_SOFT_PENALTY`, zerowany przez `ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY` ON).

**Natura — NIEZDEFINIOWANA PRECEDENCJA + 3 RÓŻNE KOTWICE CZASU:** early-bird (order-level, raw `pickup_at_warsaw`) odpala PRZED feasibility → pre-shift floor (courier-level, `pickup_ready_at`) **NIGDY nie biegnie dla zleceń ≥60 ahead**. Trzy różne referencje czasu w grze: `pickup_at_warsaw` (raw restauracja, early-bird) / `pickup_ready_at` (gotowość, pre-shift Gate3) / `czas_kuriera_warsaw` (committed kuriera, frozen). Na granicy 60 min: zlecenie 59-ahead → feasibility (pre-shift floor żyje); 61-ahead → KOORD (floor martwy). Po RELEASE czasówki (T-60/50/40 przez `czasowka_scheduler`) zlecenie wraca do feasibility z committed `czas_kuriera`, który może być < shift_start → wpada w K1 (frozen broni czasu).

**precedence_status — DEFINED-CONSISTENT dla coarse (early-bird wygrywa, trzyma zlecenie) ALE UNDEFINED dla released-czasówka<shift_start:** gdy czasówka uwolniona do pre-shift kuriera z committed<shift_start — żadna reguła nie rozstrzyga frozen-committed vs shift_start-floor (= K1). Adrian Q2: committed NIETYKALNY + wyklucz pre-shift kuriera który nie zdąży. Dziś: early-bird trzyma do T-60, potem feasibility dostaje committed, ale (a) pre-shift floor anchoruje na pickup_ready_at nie committed, (b) frozen broni committed w render → kurier może dostać czasówkę z odbiorem przed startem zmiany.

**Dowód:** `dispatch_pipeline.py:3497` (early-bird raw pickup_at_warsaw) + `:3503-3548` (KOORD przed pulą); `feasibility_v2.py:670` (Gate3 anchor pickup_ready_at) + `:748-759` (reject 30min); `auto_koord.py:32-42` (czasówka ≥60); `common.py:3413/3500` (czasówka konkretna godzina, R-DECLARED nadrzędne).

---

## K5 — Precedencja 4 clampów: frozen vs floor vs OSRM vs debias (undefined; meta-konflikt)

**4 clampy działające na czas odbioru, BEZ jednego chokepointu uszeregowania:**
1. **frozen (R27 committed)** — TSP soft-window `route_simulator_v2.py:1086` + render `courier_orders.py:872` / `fleet_state.py:519`. Nietykalny, omija OSRM.
2. **pre-shift floor (shift_start)** — feasibility departure `feasibility_v2.py:798` (earliest_departure) + konsola OSRM-path `fleet_state.py:857/264`. Działa na departure/OSRM-base.
3. **OSRM surowy** — baza jazdy (`_eta_chain`, `_compute_live_eta`).
4. **debias (PICKUP_DEBIAS_MIN=4.5, addytywny, pesymistyczny)** — `common.py:3131` → `shadow_dispatcher.py:555-566` `target_pickup_debiased_iso = _tgt_dt + timedelta(PICKUP_DEBIAS_MIN)`; serializowany `:633` jako `target_pickup_debiased`. **SHADOW-ONLY** (`ENABLE_PICKUP_DEBIAS_SHADOW`) — NIE modyfikuje żywego `eta_pickup_utc`.

**Natura — BRAK CHOKEPOINTU + SIEROTA debias:** każda powierzchnia bierze inny podzbiór i kolejność:
- silnik feasibility: floor(departure)=shift_start na SYM, committed frozen w TSP (soft-window), debias NIE (shadow).
- render apka `courier_orders.py:872`: floor = max(predicted, committed, gotowość) — frozen wygrywa, BEZ shift_start, BEZ debias.
- konsola `fleet_state.py:519`: frozen(plan_pv) PRZED OSRM(+depart_after); debias NIE.
- → precedencja efektywna: **frozen > floor > OSRM**, debias **w ogóle nie uczestniczy** (osierocony shadow-metric).
**Konsekwencja G (kalibracja):** debias (korekta na optymizm odbioru ~4,5 min) jest shadow-only → żywy floor/render używa SUROWEGO optymistycznego estymatu. Seed TOP-3 #2 (preshift-floor-audit): bazowy estymator odbioru optymistyczny ~18 min (load-aware), debias 4,5 ~6× za mały, a i tak nie żywy → „L1 się oszukuje". Floor (gdy zadziała) floruje do shift_start, ale estymata kuriera-na-odbiór pozostaje optymistyczna.

**precedence_status — UNDEFINED:** brak jawnej reguły kolejności; 4 clampy uszeregowane różnie per-powierzchnia, debias odłączony. Docelowy chokepoint (Adrian Q1 „jedno źródło floor commit+rendery"): jeden punkt `effective_pickup_at = clamp_order(committed_frozen, shift_start_floor, osrm, debias)` — NIE istnieje.

**Dowód:** `route_simulator_v2.py:1086`+`tsp_solver.py:263/290` (frozen TSP); `feasibility_v2.py:798` (floor departure); `courier_orders.py:641-660/872` (render floor=committed/gotowość); `fleet_state.py:264/519/857` (konsola frozen>OSRM-clamp); `shadow_dispatcher.py:555-566/633` (debias shadow-only); `common.py:3131` (PICKUP_DEBIAS_MIN=4.5).

---

## K6 — R6 flat-35 (feasibility HARD) ↔ tier-40 (best_effort) ↔ bundle_calib flat-35 (rozsyp progów N)

**rule_a — R6 płaski 35 (feasibility HARD-gate):** `feasibility_v2.py:1105` `_gate_bt > C.BAG_TIME_HARD_MAX_MIN` (`common.py:763 BAG_TIME_HARD_MAX_MIN=35`). DEFAULT_SLA_MINUTES=35 (`:53`). Płaski dla wszystkich tierów.
**rule_b — tier-40 cap (best_effort/objm selekcja):** `dispatch_pipeline.py:633` `_best_effort_objm_pick(..., cap_min=40.0)` + `:672` `_best_effort_objm_shadow(cap_min=40.0)` → `:666/711` `_safe = [c for c if newbag<=cap_min]`; `common.py:2651 BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN=40` („Tier-3 cap-stretch"). bag-cap tier też: `common.py:1326 HARD_TIER_BAG_CAP={gold:6,std+:6,std:5,slow:4,new:4}` (flaga OFF, `would_hard_cap` shadow `feasibility_v2.py:463`).
**rule_c — bundle_calib flat-35 (objektyw shadow):** `tools/bundle_calib_shadow.py:56 R6_MAX_MIN=35.0` → `:280 overage += max(0, age − R6_MAX_MIN)` PŁASKO (over-penalizuje T3, legalne do 40).

**Natura — ROZSYP PROGÓW (N) tej samej reguły R6:** ta sama reguła „świeżość ≤ R6" liczona 3 progami: 35 płaski (feasibility HARD) / 40 tier-3-stretch (best_effort selekcja) / 35 płaski (bundle_calib pomiar). Feasibility HARD-rejectuje przy 35, ale best_effort dopuszcza do 40 (tier-3) — więc kandydat z carry 38min: feasibility=NO, ale best_effort go bierze (always-propose, sentinel). bundle_calib mierzy go jako overage=3 (płasko), zawyżając „regres" dla T3.

**precedence_status — DEFINED-INCONSISTENT (świadoma asymetria HARD-35 vs best_effort-40, ale pomiar płaski = niespójny):** protokół `ziomek-change-protocol.md:39` „R6 cap TIER-AWARE 35 T1/2 / 40 T3 — NIE flat 35; bundle_calib over-penalizuje T3". To znana, świadoma asymetria gate-vs-stretch (always-propose), ALE bundle_calib (przyrząd bramkujący flip O2 02.07) nadal płaski → kłamiący pomiar dla T3 (klasa E/C9). Nie czysta sprzeczność reguł — rozsyp progów + mismeasure.

**Dowód:** `common.py:763` (35) vs `:2651` (40) vs `tools/bundle_calib_shadow.py:56/280` (35 płaski); `feasibility_v2.py:1105` (reject 35) vs `dispatch_pipeline.py:633/666` (best_effort cap 40).

---

## K7 — SLA-gate PRZED R6-gate, ale luźny count → `_o2_key` ranking (silent-inversion semantyki)

**rule_a — kolejność bramek w feasibility:** SLA-gate `feasibility_v2.py:1135-1209` (anchor pickup_at, luźny) wykonywany PRZED R6-gate `:1210-1223` (anchor ready_at, ostry). Oba 35.
**rule_b — `plan.sla_violations` (luźny count) zasila ranking:** `_count_sla_violations` (pickup_at) → `plan.sla_violations` → `plan_recheck.py:690 _o2_key = (p.sla_violations, dur)` (O2 OFF) → selekcja planu w `_sweep` (`:704-706`) i committed-tiebreak (`:722`). Także drugi inline klucz `plan_recheck.py:1670 (p.sla_violations, dur, seq)`.

**Natura — CICHA INWERSJA SEMANTYKI:** SLA-gate jest funkcjonalnie ZDOMINOWANA (luźniejsza niż R6 → rzadko bije pierwsza jako HARD-reject), więc wygląda na martwą/redundantną bramkę. ALE jej produkt uboczny `plan.sla_violations` (count na OPTYMISTYCZNEJ kotwicy pickup_at) NIE jest martwy — przecieka do `_o2_key` rankingu plan_recheck. → ranking planów używa luźnej kotwicy, której R6-gate (`feasibility_v2.py:1009`) explicite nazywa BŁĘDNĄ („maskuje 70+ min thermal"). Selekcja kanonu rankuje na kotwicy, którą bramka odrzuciła jako niewiarygodną.

**precedence_status — SILENT-INVERSION:** bramka R6 (ready) wygrywa jako HARD-reject, ale ranking O2 dziedziczy SLA (pickup_at). Niespójność kotwicy bramka-vs-ranking. Domknięcie = O2 ready-anchor sweep (`ENABLE_O2_READY_ANCHOR_SWEEP`, review 02.07) — przełącza `_o2_key` na o2_score (ready) ale wnosi K3 (brak paczka-exempt w O2).

**Dowód:** `feasibility_v2.py:1135` (SLA przed R6) + `:1210` (R6 po); `route_simulator_v2.py:648` (count pickup_at); `plan_recheck.py:690/704/722/1670` (sla_violations → ranking).

---

## TABELA POKRYCIA (coverage)

| Obszar / przyrząd / reguła | Zbadane? | Plik:linia (świeże) | Uwaga |
|---|---|---|---|
| frozen-R27 TSP soft-window | ✅ | `route_simulator_v2.py:1071-1088`, `tsp_solver.py:263/290-345` | 3 soft-boundy na pickup (R27/committed-N5/FRESH-ready) |
| frozen render apka | ✅ | `courier_orders.py:641-660/822-915` | `_committed_pickup_eta` floor=max(pred,ck,gotowość); FROZEN_PICKUP_ETA ON |
| frozen render konsola | ✅ | `fleet_state.py:509/519-522/544/624` | PIN_AGREED, plan_pv przed OSRM |
| pre-shift floor feasibility | ✅ | `feasibility_v2.py:748-765/789-800` | Gate3 reject 30min (pickup_ready_at) + departure clamp (shift_start) |
| pre-shift floor konsola | ✅ | `fleet_state.py:250-270/853-861` | depart_after tylko OSRM-chain |
| R6 thermal anchor | ✅ | `route_simulator_v2.py:663-693`, `feasibility_v2.py:1046/1105/1217` | ready_at, HARD-reject 35 |
| SLA anchor (3 bliźniaki) | ✅ | `route_simulator_v2.py:635-660`, `feasibility_v2.py:1135-1209`, `plan_recheck.py:690/1670` | pickup_at, HARD-reject + O2-key |
| PACZKA exempt (3 site) | ✅ | `feasibility_v2.py:1050-1055/1080/1105/1152-1155`, `common.py:3429/3479/3415` | obecny |
| PACZKA exempt brak w O2/count | ✅ | `route_simulator_v2.py:635-660/696-728`, `plan_recheck.py:687-690` | nieobecny → przeciek do rankingu |
| early-bird ≥60 KOORD | ✅ | `dispatch_pipeline.py:3497/3503-3548`, `common.py:430`, `auto_koord.py:32-42` | order-level, raw pickup_at |
| debias precedencja | ✅ | `common.py:3131`, `shadow_dispatcher.py:555-566/633` | shadow-only, sierota |
| R6 tier-40 cap | ✅ | `dispatch_pipeline.py:633/666/672/711`, `common.py:2651/1326` | best_effort 40 vs feasibility 35 |
| bundle_calib flat-35 | ✅ | `tools/bundle_calib_shadow.py:56/280` | tier-blind overage |
| flagi efektywne (frozen/floor/exempt) | ⚠ częściowo | A3 (FROZEN_PICKUP_ETA ON, CLAMP_PRESHIFT env-ON, PACZKA_EXEMPT ON, O2_SWEEP OFF, PRE_SHIFT_CLAMP ON) | cytuję A3, nie re-mierzyłem `systemctl show` |

## LUKI POKRYCIA (jawne, nie cisza)

- **Wartości LICZBOWE rozjazdu** (ile worków ma SLA-count≠R6-anchor; ile paczek przecieka do O2-rankingu; ile committed<shift_start dziennie) — NIE policzone (read-only inwentarz; to Faza C oracle / replay). Deklaruję istnienie ścieżki konfliktu z lektury, nie magnitudę.
- **`courier_api_panelsync/courier_orders.py`** (martwy fork 665L, A5/A6) — NIE re-grepowałem frozen/floor w nim; A6 oznaczył DEAD (nie serwowany). Bliźniak #14/#15 floor-audit.
- **Most paczki (parcel lane)** — czy `parcel_lane_merge`/`parcel_assign` mają własną ścieżkę frozen/floor — NIE prześwietlone (parcel natywny tor; A6 luka #2). PACZKA_ADDRESS_IDS pokrywa firmowe, nie parcel-lane (900M+id).
- **Apka Kotlin (`route_podjazdy`/RouteLogic)** — render serwerowy (courier_api), lokalny re-clamp NIE czytany (A6 luka #1).
- **`systemctl show -p Environment` per-serwis** dla FROZEN_PICKUP_ETA/CLAMP_PRESHIFT — NIE odpalony (cytuję A3/A5 effective); FROZEN_PICKUP_ETA/FALLBACK_HONEST_OSRM z `config.py` default (=ON), nie z procesu.
- **r07_chain_eta jako pickup_ref** (`feasibility_v2.py:666`, gdy `ENABLE_V326_R07_CHAIN_ETA`) — alternatywna kotwica Gate3 NIE prześledzona (flaga prawdopodobnie OFF; zakładam pickup_ready_at-path live).

## DEDUP / HANDOFF do Fazy D/E

- **K1+K4+K5 = jeden rdzeń „brak chokepointu effective_pickup_at"** (R4 z A6 distinct-root „one earliest-pickup floor" + frozen). NIE liczyć jako 3 niezależne chaosy — to JEDNA dziura: 4 clampy bez uszeregowania, floor na złej ścieżce. Domknięcie = jeden punkt clamp_order(committed, shift_start, osrm, debias) w warstwie 1/9 (Adrian Q1 „jedno źródło floor").
- **K2+K3+K7 = jeden rdzeń „SLA/R6 anchor + O2"** (R3 z A6 „one SLA/R6 anchor", grupa 4). 3 bliźniaki SLA-anchor RAZEM + paczka-exempt-w-O2 + O2-flip 02.07. NIE liczyć osobno.
- **K6 = N (rozsyp progów)** — pokrewny ale distinct: pomiar bundle_calib (przyrząd flip-gate) płaski-35 = klasa E (kłamie dla T3) → Faza C oracle MUSI segmentować per-tier przed flip O2.
- **Faza D precedencja docelowa:** frozen/committed > floor (committed wins per Adrian Q2) + feasibility wyklucza pre-shift-niezdążającego (R-LATE-PICKUP, NIE kara, Q1b). Floor MUSI trafić w wartość committed (chokepoint), uszeregowany WZGLĘDEM frozen — nie na ścieżce OSRM obok frozen.
