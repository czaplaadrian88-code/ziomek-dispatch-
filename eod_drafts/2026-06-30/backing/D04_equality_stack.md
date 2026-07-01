# D04 — MAPA KONFLIKTÓW (oś I) + STOS INWERSJI RÓWNOŚCI POZYCJI

**Faza 1 audytu spójności Ziomka · lane D (graf konfliktów reguł/flag) · sesja tmux 2 · READ-ONLY · 2026-06-30 ~17:30 UTC**
**HEAD silnika:** `8024705` (working tree `.py` czysty). **Wszystkie `plik:linia` z ŚWIEŻEGO grepu DZIŚ** (linie dryfują — re-grepuj przed cytatem).
**Zasila:** Fazę D/E (precedencja + dedup), Fazę F (kontrakty docelowe). Bazuje na A2 (rejestr reguł) + A3 (flagi efektywne) + A6 (graf bliźniaków).
**Metoda:** graf par konfliktowych reguł/flag; każda para = `rule_a` × `rule_b` × natura × precedence_status × dowód (plik:linia + live). Centralny temat (zlecenie Adriana) = **STOS INWERSJI RÓWNOŚCI POZYCJI** — sprawdzony NA ŻYWO.

---

## 0. TL;DR — 5 wniosków lane D

1. **Stos równości pozycji (no_gps/pre_shift) jest WEWNĘTRZNIE SPÓJNY KIERUNKOWO** (wszystkie 7 mechanizmów wskazują „równo"), ale osiąga równość przez **SUPRESJĘ NA ŻYWEJ KARZE**, nie przez usunięcie kary u źródła. Kanon SAM to dokumentuje: `§4:86 „rowno ON"` vs `§7-T4:151 „pre-shift -20 kara wciąż w kodzie mimo EQUAL_NO_PENALTY"`. **Reset `flags.json` (const-default = `False`) wskrzesza karę CICHO** — brak runtime-inwariantu równości.
2. **Back-door regresji V3.16 = POTWIERDZONY na żywo, ale jako ŚWIADOMA POLITYKA, nie żywy bug.** Equal-treatment wyłącza `_demote_blind_empty` dla no_gps/pre_shift przez oś **POZYCJI** — co JEDNOCZEŚNIE zdejmuje ochronę osi **OBCIĄŻENIA** (pusty bag = `s_obciazenie≈100` baseline). F1.7 neutralizuje pozycję (km=śr.floty), ale **NIE** zdejmuje przewagi pustego baga. Live: ~14% zwycięzców w peaku = no_gps/pre_shift pusty-bag; `_demote_blind_empty` **NIE odpala na realnych zleceniach** (tylko fixtury replay 999/467189/474624) → friction zdjęty. To **cicha inwersja osi-krzyżowej** V3.16.
3. **EQUAL_NO_PENALTY zachowuje FAR-veto −1000** (`PRE_SHIFT_FAR_PEN`) — czyli pre_shift z startem zmiany >30 min dostaje ŻYWĄ karę −1000 mimo kanonu „NIGDY gorszy score". Bronione jako load-aware (loadgov relaksuje). To **defined-inconsistent** wobec deklarowanej równości dla sub-populacji.
4. **DWA źródła kary pre-shift** (flat `−20` w feasibility ↔ gradient `∝m / −1000` w pipeline): gradient NADPISUJE flat dla pre_shift (default ON) → flat `−20` to **martwa redundancja** gdy gradient ON. Plus EQUAL_NO_PENALTY zeruje OBA. Trzy warstwy na jedną decyzję.
5. **Najgroźniejsza CICHA inwersja flagowa POZA równością** = `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE`: const env-default `True` (common.py:2806), `flags.json=False` (l.148) maskuje → effective False. **Usunięcie klucza z flags.json = cichy FLIP na ON** = utrata dyrektywy ALWAYS-PROPOSE (KOORD-redirect wraca). Klasa M+I (A3 §2b — potwierdzone).

**Live stan flag (flags.json, hot-reload, dispatch-shadow):** WSZYSTKIE flagi równości ON — `ENABLE_NO_GPS_EQUAL_TREATMENT=true`(l.202) · `ENABLE_EQUAL_TREATMENT_BUCKET=true`(l.211) · `ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY=true`(l.234) · `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP=true`(l.141). Cały stos LIVE.

---

## 1. STOS INWERSJI RÓWNOŚCI POZYCJI — 7 MECHANIZMÓW (kanoniczna ścieżka, świeże linie)

Reguła kanonu (`ZIOMEK_REGULY_KANON.md §4:86`, Adrian 29.06 C3): **kurier BEZ GPS / przed zmianą = LICZONY RÓWNO; NIGDY gorszy score/feasibility/ranking/TRASA; jedyne tolerowane = 3-4 min niedoszacowania czasu; „dotrze później" = clamp + R-LATE-PICKUP, NIE kara.**

| # | Mechanizm | Plik:linia (świeże) | Warstwa | Co robi | Flaga (effective) |
|---|---|---|---|---|---|
| **E1** | HARD-reject >30min-przed-zmianą | `feasibility_v2.py:751` (`too_early_min > V325_PRE_SHIFT_HARD_REJECT_MIN=30`, common.py:1972) | L5 HARD | reject `PRE_SHIFT_TOO_EARLY` | bezwarunkowy (w `ENABLE_V325_SCHEDULE_HARDENING` ON) |
| **E2** | warm-up flat −20 | `feasibility_v2.py:760-763` (`0<too_early≤30` → `metrics["v325_pre_shift_soft_penalty"]=V325_PRE_SHIFT_SOFT_PENALTY=−20`, common.py:1975) | L5→L6 SOFT | zapis kary do metryki | bezwarunkowy zapis |
| **E3** | gradient pre-shift (NEAR ∝m / FAR −1000) | `dispatch_pipeline.py:3266` `_pre_shift_gradient_penalty`; FAR `:3283`; **NADPISUJE E2** `:5099-5104` | L6 SOFT | `m≤30`→`−1.0·m`; `m>30`→`−1000` (~veto) | `ENABLE_PRE_SHIFT_GRADIENT_PENALTY` env-default `"1"` ON (common.py:1987) |
| **E4** | departure-clamp | `feasibility_v2.py:794-800` (`pos_source∈{pre_shift,no_gps}` ∧ `shift_start>now` → `earliest_departure=shift_start`) | L5 KOREKTA | symulacja startuje od shift_start (realizm ETA, NIE kara) | `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP=true` |
| **E5** | EQUAL_NO_PENALTY gate | `dispatch_pipeline.py:2413` `_apply_pre_shift_equal_gate`, wołany `:5108`; zeruje `:2447`; **ZACHOWUJE FAR-veto** `:2443-2445` | L6 SUPRESJA | zeruje E2/E3-NEAR; KEEP FAR −1000 | `ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY=true` |
| **E6** | equal_treatment_bucket | `dispatch_pipeline.py:2451` `_selection_bucket`; `:2459` `ps∈{no_gps,pre_shift}→bucket 0` | L7 SELEKCJA | no_gps/pre_shift = bucket 0 (jak informed) | `ENABLE_EQUAL_TREATMENT_BUCKET=true` |
| **E7** | demote-exclusion | `dispatch_pipeline.py:2466` `_is_demotable_blind_empty`: `:2473` no_gps excl, `:2475` pre_shift excl; demote `:2504` wołany `:5934` | L7 SELEKCJA | no_gps/pre_shift WYŁĄCZONE z V3.16 demote | `_no_gps_equal_on`+`_equal_bucket_on` |
| (E0) | F1.7 score-neutral | `dispatch_pipeline.py:5838-5884` (no_gps: km=śr.floty `:5848/5859`, ETA=max(15,prep) `:5853`; pre_shift: ETA=shift_start clamp `:5877/5883`) | L6 NEUTRALIZACJA | usuwa fikcję BIALYSTOK_CENTER z km/ETA (oś POZYCJI) | bezwarunkowy |

**Kolejność wykonania (zweryfikowana, dispatch_pipeline `assess_order`):** E0 score-neutral (5838) → per-kandydat scoring (E2 czyt. 5095 → E3 nadpis 5099-5104 → E5 gate 5108 → `bonus_penalty_sum` 5137 → `final_score` 5199) → `feasible.sort(-score)` (5906) → v325/v326/a2/gps_age/multistop (5910-5925) → **E7 demote LAST** (5934, z wykluczeniami E6/E7) → `_assert_feasibility_first` (5938) → R-LATE-PICKUP tiering (5953).

**Wewnętrzna spójność feasibility (E1+E2+E4):** SPÓJNA — reject gdy >30 wcześnie, clamp+penalty gdy 0-30 wcześnie, brak gdy on-shift. Gradient zone (E1 0-30 / E2-warm). Tu precedencja = `ok`. Konflikt powstaje DOPIERO między tą spójną warstwą feasibility a DOWNSTREAM supresją + selekcją.

---

## 2. KONFLIKTY W STOSIE RÓWNOŚCI (par-po-parze)

### K-EQ-1 — warm-up −20 (E2) ↔ EQUAL_NO_PENALTY (E5) : „kara w kodzie" suppression-on-live-penalty
- **Natura:** cicha-inwersja-P (kara liczona u źródła, zerowana flagą downstream).
- **Dowód:** `feasibility_v2.py:763` ZAWSZE pisze `v325_pre_shift_soft_penalty=−20`; `dispatch_pipeline.py:5108` `_apply_pre_shift_equal_gate` zeruje go (gdy flaga ON) `:2447`. Źródło kary NIE usunięte — żyje, supresja na wierzchu. Const `ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY=False` (common.py:264); równość trzyma WYŁĄCZNIE `flags.json:234=true` (hot-reload). **Kanon §7-T4:151 nazywa to wprost: „pre-shift -20 kara wciąż w kodzie mimo EQUAL_NO_PENALTY".**
- **precedence_status:** silent-inversion. Runtime-precedencja DEFINED (gate po karze, deterministyczna), ALE **reset/utrata flags.json → const False → kara WSKRZESZONA cicho**; ZERO runtime-inwariantu „pre_shift bez kary" (A6 gr.6: grep guard = ∅). Klasa flag-miny (A.handoff #5: „MINY FLAG default-OFF-w-kodzie vs ON-w-flags.json").

### K-EQ-2 — warm-up −20 (E2) ↔ gradient (E3) : dwie formuły tej samej kary, jedna martwa
- **Natura:** sprzeczność/redundancja (dwa źródła kary pre-shift, gradient nadpisuje flat).
- **Dowód:** `dispatch_pipeline.py:5099-5104` — gdy `ENABLE_PRE_SHIFT_GRADIENT_PENALTY` ON (env-default ON, common.py:1987) ∧ `pos_source=="pre_shift"` → `_psp=_pre_shift_gradient_penalty(...)` NADPISUJE `bonus_v325_pre_shift_soft` ORAZ `metrics["v325_pre_shift_soft_penalty"]`. Formuły RÓŻNE: warm-up flat `−20` (feasibility) vs gradient `−1.0·m` (`m≤30`) → przy m=20 zbieżne (−20), przy m=30 rozjazd (−30). **Flat −20 (E2) = martwy dla pre_shift gdy gradient ON (default).** Dla `no_gps` gradient NIE odpala (gate `=="pre_shift"`) → no_gps używa tylko ścieżki feasibility (ale on-shift → too_early≤0 → 0).
- **precedence_status:** defined-consistent (nadpisanie deterministyczne) — ALE redundancja (martwy flat) = dług, klasa N (rozsyp formuł) + K (martwa gałąź flat dla pre_shift).

### K-EQ-3 — EQUAL_NO_PENALTY zachowuje FAR-veto −1000 (E3-far) ↔ kanon „ZAWSZE równo, NIGDY gorszy score" (§4:86)
- **Natura:** sprzeczność (deklarowana równość vs ŻYWA kara −1000 dla sub-populacji pre_shift z odległym startem).
- **Dowód:** `dispatch_pipeline.py:2443-2445` — gate JAWNIE zachowuje `pen ≤ PRE_SHIFT_FAR_PEN(−1000)+0.5` (`metrics["v325_pre_shift_far_veto_kept"]`). `_pre_shift_gradient_penalty:3283` zwraca `−1000` dla `m>PRE_SHIFT_NEAR_MIN(30)`. Czyli pre_shift ze startem zmiany >30 min od teraz = score −1000 = de-facto wykluczony = **gorszy ranking**, sprzeczny z literą §4:86 „NIGDY gorszy score/ranking". Docstring gate (`:2419-2423`) broni: load-aware (loadgov≥unlock → gradient relaksuje do ∝m, wtedy lekka i zdjęta) + „R-LATE-PICKUP do restauracji". To JEST reguła Adriana „dotrze później = zmieniaj KTO nie czas" (preshift-handoff Q2/§8) — ale FORMALNIE łamie „ZAWSZE równo".
- **precedence_status:** defined-inconsistent. Świadome (broni harm: 45-min czekanie klienta, replay 29.06), ale deklaracja kanonu „NIGDY gorszy" jest absolutna, a kod ma żywy −1000. Adrian C3 doprecyzowuje „3-4 min niedoszacowania" jako jedyną tolerancję — FAR-veto to inny mechanizm (wykluczenie, nie niedoszacowanie). Wymaga ACK czy „ZAWSZE równo" obejmuje FAR-veto.

### K-EQ-4 — equal-bucket + demote-exclusion (E6+E7) ↔ V3.16 demote (oś OBCIĄŻENIA) : ★ BACK-DOOR REGRESJI
- **Natura:** cicha-inwersja-P osi-krzyżowej (flaga osi-POZYCJI wyłącza ochronę osi-OBCIĄŻENIA).
- **Mechanizm:** `_demote_blind_empty` (V3.16) chroniło przed „pusty bag ~82 baseline wygrywa": pusty bag dostaje `s_obciazenie≈100×0.25 + s_kierunek≈100×0.25 + s_czas≈100×0.20 ≈ 70-82` BEZ kar, a bag-kurierzy tracą −100..−300 na r8/r9. Gate demote = `_is_blind_empty_cand` = **blind(pozycja) AND empty(obciążenie)** — DWIE osie sklejone. Equal-treatment wyłącza demote po osi POZYCJI (`:2473` no_gps, `:2475` pre_shift → `_is_demotable_blind_empty=False`) → ale to zdejmuje TAKŻE ochronę osi OBCIĄŻENIA. **F1.7 (E0) neutralizuje km/ETA (pozycja), ale NIE rusza `s_obciazenie` (pusty=100).** → synthetic-position pusty-bag kurier może wygrać z realnym-GPS bag-kurierem na przewadze obciążenia.
- **Dowód KODU:** `dispatch_pipeline.py:5927-5934` komentarz wprost przypomina oid=474624 (Mateusz O 413 score 112 vs Adrian R 400 score 4.1) — case który demote naprawiał; teraz no_gps wykluczony z demote.
- **Dowód LIVE (sprawdzone na żywo — zlecenie Adriana):**
  - flags.json: wszystkie 3 flagi równości ON (potwierdzone).
  - **PEAK slab `shadow_decisions.jsonl` (n=153 best-records, środek pliku):** pos_source best = `last_delivered 45 / post_wave 30 / last_assigned_pickup 22 / gps 13 / no_gps 12 / pre_shift 11 / None 10 / ...`; **no_gps∪pre_shift pusty-bag = 22/153 = 14%** zwycięzców. Spójne z seed-audytem (best=pre_shift 7,4% + no_gps ≈13,9%).
  - **`NO_GPS_DEMOTE` w `dispatch.log` dziś = 1155 fires, ale TYLKO 3 distinct order-id: `999`(87×, probe), `467189`(51×, fixtura V3.16 oryg.), `474624`(51×, fixtura sprint-diag) — ZERO realnych 484xxx.** → demote odpala WYŁĄCZNIE na replay/fixturach (kontrfaktyk), **NIE na żywych zleceniach** → friction realnie zdjęty dla live no_gps/pre_shift, zgodnie z polityką.
  - End-of-day slab (18:28) = 60% no_gps best, ale to skew GPS-off końca dnia (gps=1/160), NIE peak — odrzucam jako reprezentatywne.
- **precedence_status:** silent-inversion. Precedencja DEFINED (flagi wygrywają, demote off) i ŚWIADOMA na osi pozycji (Adrian C3 „równość ZOSTAJE", preshift-handoff Q1b). ALE **konsekwencja osi-obciążenia NIE była jawnie zdecydowana** — preshift-handoff §7-TROP#4 nazywa to „regresja V3.16 tylnymi drzwiami"; to inwersja cicha bo gate sklejał 2 osie. Net: równość pozycji ✅ (poprawna), ochrona pustego-baga ❌ (zdjęta ubocznie). Czy 14% pustych-bagów wygrywa SŁUSZNIE (fleet-balance: nie dokładaj zajętemu) vs FIKCYJNIE (median km maskuje realny dystans) — wymaga oracle Fazy C (join `gps_delivery_truth`), nie rozstrzygalne lekturą.

### K-EQ-5 — kanon §4:86 „rowno ON" ↔ kanon §7-T4:151 „kara w kodzie" : auto-sprzeczność kanonu
- **Natura:** sprzeczność wewnątrz-kanonu (sam dokument deklaruje równość ON i jednocześnie notuje żywą karę).
- **Dowód:** `ZIOMEK_REGULY_KANON.md:86` (tabela §4: „No-GPS = ZAWSZE równo … NIGDY gorszy") vs `:151` (§7 NAPIĘCIA: „T4 pre-shift -20 kara wciąż w kodzie mimo EQUAL_NO_PENALTY"). Kanon JEST świadomy długu (T4 = napięcie do pilnowania), ale operacyjnie czytelnik §4 dostaje „równo", a kod ma `−20`(E2)+gradient(E3)+FAR−1000 zerowane TYLKO flagą.
- **precedence_status:** defined-inconsistent (samo-udokumentowane). To meta-dowód, że stos osiąga równość supresją, nie u źródła — kandydat #1 dla Fazy F (kontrakt: „równość = brak kary U ŹRÓDŁA + runtime-inwariant", nie suppression-gate).

### K-EQ-6 — trójca flag równości : sprzężenie (częściowy flip = częściowa równość)
- **Natura:** sprzężenie-flag (3 flagi muszą iść RAZEM; każda gated osobno → stany pośrednie niespójne).
- **Dowód:** `ENABLE_NO_GPS_EQUAL_TREATMENT`(common.py:1108) + `ENABLE_EQUAL_TREATMENT_BUCKET`(:1112) + `ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY`(:264) — sterują RÓŻNYMI mechanizmami (E7 demote-excl / E6 bucket / E5 penalty-zero). A3 §4 listuje je jako „equal-treatment trójca sprzężona". Flip tylko jednej: np. EQUAL_NO_PENALTY ON ale EQUAL_TREATMENT_BUCKET OFF → kara zdjęta ale bucket DEMOTUJE → niespójność (równo w score, gorzej w selekcji). Każda gated `C.flag()` niezależnie → 8 kombinacji, tylko „wszystkie ON" = spójna równość.
- **precedence_status:** defined-consistent (przy wszystkich ON) / undefined dla stanów mieszanych. C3-sprzężenie protokołu: flip w parach/trójkach.

### K-EQ-7 — departure-clamp (E4) ↔ równość (E5/E6/E7) : sprzężenie realizm↔równość
- **Natura:** sprzężenie-flag (clamp = przeciwwaga realizmu ETA dla równości).
- **Dowód:** `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP=true` (feasibility_v2.py:794) sprawia że symulacja pre_shift startuje od `shift_start` (realna ETA). Gdyby clamp OFF a równość ON → pre_shift/no_gps dostają równe traktowanie ALE z fikcyjną ETA „startuje teraz" → optymizm (preshift-handoff TROP: „kurier startuje teraz" dla niepracującego). Clamp jest WARUNKIEM uczciwości równości. Para nie-jawnie sprzężona (różne flagi, różne warstwy L5 vs L6/L7).
- **precedence_status:** defined-consistent. Ale clamp ma osobny floor-leak: `plan_recheck` regen co 5min BEZ clampu (K-X-2 niżej) → realizm clampa cofany downstream.

---

## 3. KONFLIKTY POZA STOSEM RÓWNOŚCI (szerszy graf lane D)

### K-X-1 — R6 cap: flat 35 HARD ↔ tier-40 best_effort/objm ↔ kanon C5 „40=ALARM-only"
- **Natura:** sprzeczność (3 widoki tego samego progu R6).
- **Dowód:** `feasibility_v2.py:1219` reject `R6_per_order_>35min` (flat `BAG_TIME_HARD_MAX_MIN=35`, common.py:763) ↔ `dispatch_pipeline.py:633` `_best_effort_objm_pick(cap_min=40.0)` + `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN=40` (common.py:2651) ↔ **kanon C5 (`ZIOMEK_REGULY_KANON.md:123`): „40=TYLKO ALARM; normalnie 35 dla każdego … Kod dziś ma 40 per-klasa (best_effort/objm) — NIEZGODNE, do poprawy na alarm-only".**
- **precedence_status:** defined-inconsistent. Kanon: 40 = AUTO-eskalacja per-decyzja gdy Strategia 1+2 niewykonalne (sygnał `pool_feasible==0`). Kod: 40 = stały per-ŚCIEŻKA (best_effort zawsze stretchuje do 40), NIE alarm-gated. TODO C5 (kanon §5 poz.1) — niewykonane. Klasa N (rozsyp progów) + I (precedencja flat-35 vs tier-40 niezdefiniowana zależnie od ścieżki).

### K-X-2 — pre-shift floor: feasibility HARD-clamp ↔ plan_recheck BRAK (K2 cofacz)
- **Natura:** niespójność-ścieżek (clamp w fazie A feasibility, brak w fazie B regen).
- **Dowód:** `feasibility_v2.py:794` clamp `earliest_departure=shift_start` (faza decyzji) ↔ `plan_recheck.py:554-594` regen `courier_plans.json` co 5 min z `_earliest_committed_pickup_anchor` (committed-only, BEZ shift_start floor — A6 gr.6 #5). → naprawiony czas SAM SIĘ ODCLAMPOWUJE co tick. 17 powierzchni liczy czas-odbioru, 4 mają floor (A6 gr.6). `grep available_from = ∅`, `grep guard pickup≥shift_start = ∅`.
- **precedence_status:** defined-inconsistent (dwie ścieżki regenerujące kanon, jedna z floorem, druga bez) → DIVERGED by-construction. Distinct root R4 (A6). Faza F: jedno `available_from = max(now, shift_start)`.

### K-X-3 — SLA-anchor `pickup_at` ↔ R6-anchor `pickup_ready_at` (T2 split-brain)
- **Natura:** dwie HARD-bramki tej samej decyzji, różny anchor.
- **Dowód:** `route_simulator_v2.py:663` `r6_thermal_anchor` = ready-anchor (gotowość) z inwariantem INV-R6-ANCHOR-CONSISTENCY; `route_simulator_v2.py:635` `_count_sla_violations` + `feasibility_v2.py:~1156` SLA-loop = inline `pickup_at` (TSP-projected). Kanon §7-T2:149 „R6 anchor split-brain". + asymetria paczka-exempt (`ENABLE_PACZKA_R6_THERMAL_EXEMPT` w SLA-loop, brak w `_count_sla_violations`).
- **precedence_status:** undefined. Dwie HARD reguły (R6 termik vs SLA) kotwiczą inaczej — która wygrywa gdy się rozjadą = nierozstrzygnięte. O2 sprint 02.07 (at-168/200). Distinct root R3 (A6 gr.4).

### K-X-4 — R-DECLARED-TIME (deklarowana HARD) ↔ R27 (SOFT window) ↔ BRAK runtime-bramki
- **Natura:** HARD-bez-runtime (deklaracja HARD, egzekucja SOFT/pośrednia).
- **Dowód:** doc REGUŁY: „R-DECLARED-TIME (HARD): `czas_kuriera ≥ czas_odbioru_timestamp` zawsze". Grep runtime-guard `czas_kuriera>=czas_odbioru` = **∅** (jedyne trafienie `dispatch_pipeline.py:3168` = KOMENTARZ). Egzekucja pośrednia: R27 frozen window (SOFT, `route_simulator_v2:1071`) + `pickup_ready_at=czas_kuriera`. Kanon C-DT:126 „R-DECLARED-TIME nadrzędne (nie kłam o czasie)".
- **precedence_status:** undefined. HARD deklarowana, ale ŻADNA warstwa nie sprawdza nierówności jako bramki/inwariantu → de-facto egzekwuje SOFT R27. „Nadrzędne" (C-DT) bez runtime = pusta deklaracja precedencji. Kandydat Fazy F (runtime-inwariant R-DECLARED).

### K-X-5 — R-LATE-PICKUP nazwa `LATE_PICKUP_HARD_GATE` ↔ zachowanie SELEKCJA-tier ↔ R27 ±5
- **Natura:** nazwa-HARD vs zachowanie-SELEKCJA + kolizja progu 5 min z R27.
- **Dowód:** `ENABLE_LATE_PICKUP_HARD_GATE` (common.py:2823, ON) + `LATE_PICKUP_HARD_MAX_MIN=5.0` (common.py:2825) — nazwa sugeruje HARD-bramkę, a `dispatch_pipeline.py:5953` to TIERING (reorder, NIE hard-reject; komentarz `:5941` „NIE usuwa kandydatów → zawsze daje propozycje"). Próg 5 min == R27 ±5 (committed window) ale INNA warstwa (selekcja-tier vs soft-window TSP). Kanon C6:124 „committed nietykalny; przesunięcie max 5 min".
- **precedence_status:** defined-inconsistent. Nazwa „HARD_GATE" myli (klasa L) → ryzyko że nowa sesja potraktuje jako bramkę. Zachowanie = SELEKCJA. Współgra z R27/R-DECLARED ale w innej warstwie → precedencja „kto pierwszy karze za late-pickup" rozmyta między R27(soft TSP), R-LATE-PICKUP(tier), R-DECLARED(deklaracja).

### K-X-6 — R-RETURN-TO-RESTAURANT-VETO: nazwa VETO ↔ feasibility metric-only ↔ kanon zakaz
- **Natura:** split-enforcement (nazwa HARD-VETO, feasibility nie vetuje, kanon egzekwuje).
- **Dowód:** `feasibility_v2.py:905` `if ENABLE_R_RETURN_TO_RESTAURANT_VETO → detect_return_to_restaurant(:132)` = **TYLKO metryka** (komentarz „instrumentacja NIGDY nie przerywa feasibility"). Realny zakaz = kanon `ENABLE_NO_RETURN_TO_DEPARTED_PICKUP` (plan_recheck `:942-985`, kanon §5p3:100, drop-in ON).
- **precedence_status:** undefined. „VETO" w nazwie ≠ veto w feasibility; egzekucja rozdzielona feas(metric)↔kanon(zakaz) — gdy feasibility przepuszcza return-to-rest a kanon go potem nie wykryje (np. recanon nie odpalił), precedencja niezdefiniowana. Klasa I+L+B.

### K-X-7 — R-10 FLEET-LOAD-BALANCE (ON) ↔ SP-B2 LOADGOV (OFF) + loadgov_ewma żywy mimo OFF
- **Natura:** sprzężenie-flag (2 reguły obciążenia floty; loadgov_ewma używany mimo flagi OFF).
- **Dowód:** `ENABLE_V326_FLEET_LOAD_BALANCE` env-default `"1"` ON (common.py:2238) ↔ `ENABLE_FLEET_LOAD_GOVERNOR` env-default `"0"` OFF (common.py:2103). DWA mechanizmy „obciążenie floty". ⚠ `loadgov_ewma` (LOADGOV) KARMI relaksację FAR-veto pre_shift (`dispatch_pipeline.py:5101` `_pre_shift_gradient_penalty(..., loadgov_ewma)`) **mimo że flaga LOADGOV OFF** → governor-telemetria żywa, decyzyjnie używana w innej regule.
- **precedence_status:** defined-inconsistent. Która reguła load „wygrywa" gdy obie patrzą na obciążenie — V326 aktywna, LOADGOV OFF, ale ewma LOADGOV przecieka do pre_shift gate. Klasa A1 (2 reguły load) + D (flaga OFF, kod żywy).

### K-X-8 — ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE : const True ↔ flags.json False (maskująca)
- **Natura:** cicha-inwersja flagowa (flags.json maskuje const; usunięcie klucza → silent flip).
- **Dowód:** `common.py:2806` `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE = os.environ.get(...,"1")=="1"` (env-default **True**) ↔ `flags.json:148 = false`. `decision_flag()` precedencja flags.json→const → effective **False**. **Usunięcie klucza z flags.json → decision_flag spada na const=True → verdict-gate FLIP na ON** = KOORD-redirect wraca = utrata dyrektywy ALWAYS-PROPOSE. A3 §2b — JEDYNA prawdziwa inwersja maskująca w rejestrze.
- **precedence_status:** silent-inversion. Kruche: stan „OFF" trzymany WYŁĄCZNIE obecnością klucza-False; brak klucza ≠ default-OFF (default = ON). Klasa M+I.

### K-X-9 — pozycja-twins: engine UNIFIED ↔ out-of-engine gates DIVERGED (8 bliźniaków)
- **Natura:** asymetria-bliźniaków (równość w silniku, dyskryminacja w 3 gate'ach poza silnikiem).
- **Dowód:** 7 engine-twins na `_selection_bucket` (UNIFIED; twin 4 `_best_effort_fastest_pickup_key:595` zweryfikowany — używa `_selection_bucket` w ciele, NIE inline). 3 out-of-engine DIVERGED: `tools/reassignment_forward_shadow.py:64` `_SYNTH_POS={none,pin,pre_shift,""}`+`a_late` (własna fikcja, 59% fałszywych ratunków ripujących no_gps/pre_shift), `auto_assign_gate.py:160-164` G7 `pos_not_informed` (LATENT, `ENABLE_AUTO_ASSIGN` OFF), `feed.py` overlay quality_reassign bez `_pos_trusted`.
- **precedence_status:** defined-inconsistent. Silnik traktuje równo, gate-shadow/konsola/autonomia NIE → ta sama reguła egzekwowana niespójnie per powierzchnia → „klasa wraca ≥4×". A6 gr.3b. Distinct root R1 (one-selection-key, resztki out-of-engine).

### K-X-10 — flag-coupling C3: OR_TOOLS_TSP ↔ SAME_RESTAURANT_GROUPING
- **Natura:** sprzężenie-flag (flip jednej bez drugiej = double-insert super-pickupa).
- **Dowód:** oba env-default `"1"` ON (common.py:2356/3159 wg A3), NIE w flags.json/ETAP4/fingerprint. Protokół #13/C3: `ENABLE_V326_OR_TOOLS_TSP=False` odsłania grouping double-pickup w legacy greedy → MUSI flipnąć GROUPING OFF razem.
- **precedence_status:** defined-inconsistent (zależność jednokierunkowa nieegzekwowana — brak guard sprzężenia). Klasa C3+D (env-frozen, poza parytetem fingerprint).

---

## 4. GRAF PRECEDENCJI — synteza (kto bije kogo, gdzie zdefiniowane)

```
HARD floor (E1: reject>30min)  ──absolutny, flag-niezależny──►  zawsze wygrywa
        │
        ▼ (0-30min warm-up)
feasibility E2(-20) ──nadpisuje──► gradient E3(NEAR/FAR) ──suppress──► EQUAL_NO_PENALTY E5
        │                                    │ (FAR -1000 ZACHOWANY)          │
        │                                    ▼                                 ▼
        │                          KONFLIKT K-EQ-3 (vs §4:86)        KONFLIKT K-EQ-1 (kara-w-kodzie)
        ▼
selekcja: _selection_bucket E6 (równo) ──► _demote_blind_empty E7 (excl no_gps/pre_shift)
        │                                            │
        ▼                                            ▼
  out-of-engine gates DIVERGED (K-X-9)      KONFLIKT K-EQ-4 (back-door V3.16, oś obciążenia)
```

**Precedencja DEFINED-CONSISTENT (zdrowe):** E1>E2>E3>E5 runtime (deterministyczna kolejność wykonania); feasibility wewn. (E1/E2/E4); trójca równości przy wszystkich-ON; `_assert_feasibility_first` (P0 HARD-przed-SOFT, dispatch_pipeline:2480/5938).
**Precedencja DEFINED-INCONSISTENT:** K-EQ-3 (FAR-veto vs kanon), K-EQ-5 (kanon auto-sprzeczność), K-X-1 (R6 35/40/alarm), K-X-2 (floor feas/plan_recheck), K-X-7 (2 load rules), K-X-9 (twins), K-X-10 (OR-Tools/grouping).
**Precedencja UNDEFINED:** K-X-3 (SLA/R6 anchor), K-X-4 (R-DECLARED HARD bez runtime), K-X-6 (RETURN-VETO feas/kanon), stany mieszane trójcy równości.
**SILENT-INVERSION:** K-EQ-1 (reset→kara wraca), K-EQ-4 (oś-krzyżowa V3.16), K-X-8 (COMMIT_DIVERGENCE const-True maska).

---

## 5. ODPOWIEDZI NA PYTANIA ZLECENIA (wprost)

1. **„Czy stos równości spójny, która wygrywa?"** — KIERUNKOWO spójny (7 mechanizmów → „równo"), precedencja runtime DEFINED (E1 HARD-floor zawsze; potem supresja E5 zeruje E2/E3-NEAR; FAR-veto −1000 i HARD-30 przeżywają). ALE osiągnięty SUPRESJĄ NA ŻYWEJ KARZE, nie u źródła → kruche na reset flags.json (K-EQ-1) + auto-sprzeczny w kanonie (K-EQ-5).
2. **„Spiętrzone inwersje zdjęły CAŁE tarcie → regresja V3.16 tylnymi drzwiami?"** — **TAK, POTWIERDZONE NA ŻYWO**, ale jako ŚWIADOMA POLITYKA (Adrian C3/Q1b), nie żywy bug. Live: ~14% peak-zwycięzców = no_gps/pre_shift pusty-bag; `_demote_blind_empty` NIE odpala na realnych zleceniach (tylko fixtury 999/467189/474624). F1.7 neutralizuje POZYCJĘ, ale przewaga pustego-baga na osi OBCIĄŻENIA (`s_obciazenie≈100`) POZOSTAJE — to cicha inwersja osi-krzyżowej (gate demote sklejał blind∧empty; wyłączenie po pozycji zdjęło ochronę obciążenia). Czy 14% to harm vs fleet-balance-correct → oracle Fazy C (join gps_delivery_truth), nierozstrzygalne lekturą.
3. **„8 bliźniaków pozycji — wszystkie spójne?"** — NIE. 7 engine-twins UNIFIED na `_selection_bucket` (w tym twin 4 fastest_pickup zweryfikowany). 3 out-of-engine DIVERGED: `reassignment_forward_shadow._SYNTH_POS`, `auto_assign_gate G7` (latent), `feed.py` (bez `_pos_trusted`). K-X-9.
4. **„kanon :86 vs :151?"** — auto-sprzeczność kanonu (K-EQ-5): §4:86 „równo ON" deklaruje brak kary, §7-T4:151 notuje że kara `−20` WCIĄŻ w kodzie (feasibility:763), zerowana tylko flagą. Kanon świadomy długu (T4=napięcie), ale operacyjnie sprzeczny.

---

## 6. POKRYCIE (jawne luki — nie cisza)

**Zbadane świeżym grepem/lekturą DZIŚ:** pełny stos równości (E0-E7, 7 mechanizmów, kolejność wykonania zweryfikowana), 4 flagi równości w flags.json (effective ON), feasibility pre-shift blok (650-824), dispatch_pipeline gate/bonus/demote/F1.7 (2380-2540, 5090-5140, 5838-5940), kanon §4/§5/§7, 10 konfliktów poza-stosem (R6 cap, floor, SLA-anchor, R-DECLARED, R-LATE-PICKUP, RETURN-VETO, 2×load, COMMIT_DIVERGENCE, twins, OR-Tools/grouping). LIVE: flags.json + 2 slaby shadow_decisions (peak n=153 + eod n=160) + NO_GPS_DEMOTE order-id distribution (dispatch.log).

**LUKI (jawne):**
1. **Czy 14% pustych-bagów to harm vs fleet-balance-correct** — NIE rozstrzygnięte (wymaga oracle Fazy C: join `gps_delivery_truth.jsonl`, czy synthetic-median km maskuje realny dystans). Lane D = graf konfliktów, nie werdykt szkody.
2. **`s_obciazenie`/`s_kierunek` realna magnituda dla pustego baga** — NIE odczytane z `scoring.py` linia-po-linii (oparte na changelog V3.16 „~82 baseline"); dokładny breakdown = Faza B/C.
3. **order=999/467189/474624 KTÓRY proces** loguje NO_GPS_DEMOTE w dispatch.log (replay-tool vs shadow self-test) — NIE zidentyfikowany dokładnie (grep `999` w kodzie = brak hardcode w main-path); hipoteza = `nogps_preshift_bucket_replay` kontrfaktyk. Nie zmienia wniosku (zero realnych 484xxx).
4. **Stany mieszane trójcy równości** (np. tylko EQUAL_NO_PENALTY ON) — NIE testowane runtime; precedencja undefined deklarowana z lektury.
5. **Out-of-engine gates magnituda** (reassignment_forward_shadow 59%, feed.py) — z A6/protokołu, NIE re-zmierzona w tym runie (Faza C oracle).
6. **R27 ±5 vs R-LATE-PICKUP 5min** — czy DOKŁADNIE ten sam 5 czy zbieg — nie prześledzono pełnego flow obu w jednym case (deklarowane z lektury stałych).

**NIE-luki (świadomie poza D04):** wartości numeryczne parytetu A≡B (Faza C oracle), cross-repo konsola/apka render równości (A5/lane J), sentinele jako klasa M (osobny agent), Mailek/Papu (granica).

## 7. HANDOFF Faza E/F
- **Distinct root R1 (one-selection-key)** obejmuje stos równości — Faza F kontrakt: równość = **brak kary U ŹRÓDŁA** (zdejmij E2/E3-NEAR z feasibility, nie suppress flagą) + **runtime-inwariant** „pre_shift/no_gps bez kary pozycji" (wzór `carried_first_guard`) + przepięcie 3 out-of-engine gates (K-X-9). To rozwiązuje K-EQ-1+K-EQ-5+K-EQ-4(oś-pozycji) razem.
- **K-EQ-4 oś-OBCIĄŻENIA** = osobna decyzja: czy pusty-bag-baseline ma być neutralizowany (jak F1.7 robi z pozycją) — wymaga ACK Adriana (czy 14% to feature czy regresja). NIE scalać z osią-pozycji (różne osie, gate je sklejał — to był pierwotny błąd).
- **K-X-8 COMMIT_DIVERGENCE** = najprostszy fix: dodać klucz do ETAP4 albo wyrównać const default → False (usunąć minę maskującą).
- **K-EQ-3 FAR-veto vs §4:86** = PYTANIE do Adriana (czy „ZAWSZE równo" obejmuje FAR-veto −1000, czy FAR-veto = legalny mechanizm „zmieniaj KTO"). NIE zgadywać.
