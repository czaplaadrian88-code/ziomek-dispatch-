# B05 — KLASA C: REGUŁA W ZŁEJ Z 10 WARSTW (wrong-layer)

**Agent:** B05-C-wrong-layer · **Lane:** B · **Tryb:** READ-ONLY (zero edycji/restartów/flipów). **Data:** 2026-06-30 ~14:1x UTC · **HEAD:** `8024705`.
**Zakres:** Klasa C taksonomii — reguła egzekwowana w niewłaściwej z 10 warstw szkieletu. 4 pod-typy z zlecenia:
- **C1** logika TYLKO-SOFT-w-score, nigdy-w-HARD-ani-selekcji (geometria rozjazdu: spread>R1 NIE rejectuje feasibility_v2).
- **C2** obliczenie-w-złej-warstwie (soon_free w scoringu L6, nie w puli L4).
- **C3** HARD-bypass PO guardzie feasibility (FEAS_CARRY_READMIT mutuje top[0] za `_assert_feasibility_first`).
- **C4** patch-na-renderze-gdy-źródło-w-silniku.

**Wszystkie `plik:linia` z ŚWIEŻEGO grepu 2026-06-30** (linie DRYFUJĄ — re-grep przed cytatem jako pewnik).
**10 WARSTW (kotwica):** L1 wejście · L2 geokod(HARD) · L3 early-bird(HARD) · L4 telemetria(flota/GPS/ETA) · L5 `check_feasibility_v2`(HARD) · L6 scoring+~19 kar(SOFT) · L7 selekcja(SOFT) · L8 werdykt KOORD(HARD) · L9 zapis+kanon(HARD) · L10 konsola/apka/Telegram(render).

---

## 0. TL;DR — co znalazłem (dedup do rootów Fazy E)

| ID | Pod-typ | Reguła | Warstwa ZAKODOWANA | Warstwa POPRAWNA | Stan | Sev | Root |
|---|---|---|---|---|---|---|---|
| C1-a | C1 | geometria→klucz selekcji | **brak** (lex_qual czysto czasowy) | L7 selekcja | **LIVE** | P1 | R1 / P0-A |
| C1-b | C1 | geometria→HARD bramka | L6 SOFT + L5 **metric-only** | L5 HARD (cap) | **LIVE** | P2 | R1 / P0-A |
| C1-c | C1 | geometry-blind escalation | L8 KOORD za-wąska (feasible≥2) | L8 (pool=0 też) | **LIVE** | P2 | R1 / P0-A |
| C2 | C2 | „zaraz-wolny" availability | L6 scoring (podmiana pozycji) | L4 pula/dispatchable_fleet | LATENT (flaga OFF) | P2 | R6-lookahead (seed) |
| C3 | C3 | re-admit carry-NO | L7 selekcja PO L8-guardzie | L5 feasibility | LATENT (flaga OFF, rolled-back) | P2 | wzorzec#10 |
| C4-a | C4 | floor pickup≥shift_start | L10 render-clamp (konsola) | L9 plan_recheck (+L5) | **LIVE patch / źródło OPEN** | P2 | R4 (floor) |
| C4-b | C4 | carried-first / route-order | L10 render ×3-4 KOPIE | L9 kanon (jedno źródło) | **LIVE** | P2 | R2 (route-order) |
| C-adj-1 | C(split) | R9 wait>20 / ext>60 / carry_chain / intra-gap = HARD | L6 scoring-block verdict-override | L5 check_feasibility_v2 | LIVE (część), SAFE-by-construction | P3 | hard-reject-in-scoring |
| C-adj-2 | C(split) | R-RETURN-„VETO" | L5 **metric-only** + L9 kanon | L5 (nazwa=HARD reject) | LIVE | P3 | return-veto-split (też I/B) |

**Dedup nadrzędny:** C1-a/b/c = JEDEN root (geometria nigdzie nie dożywa do wyboru pod scarcity = P0-A seed-audytu). C4-a zwija się do R4 (floor, 17 powierzchni — A6), C4-b do R2 (route-order, 5 kopii — A6/A5). NIE liczyć C1-a/b/c jako 3 chaosów — to 3 warstwy tej samej ślepoty. C3≠C-adj-1: C3 re-admituje NO z `candidates` (bypass), C-adj-1 liczy HARD w L6 ale ODSIEWA przed pulą (split-layer, nie bypass).

---

## 1. C1 — geometria rozjazdu: TYLKO-SOFT-w-score, nigdy HARD ani klucz selekcji

**Reguła biznesowa:** kierunkowa niespójność worka (rozjazd dostaw, przeciwne kierunki, cross-quadrant) ma być karana. **To kanoniczny C1 z taksonomii** (A2 „geometria-rozjazdu | WARSTWA SOFT-ONLY").

### 1.1 Producent metryk geometrii (gotowe w chwili selekcji, NIEczytane przez wybór)
`feasibility_v2.py` liczy i serializuje pełen zestaw osi geometrii:
- `:500-507` `deliv_spread_km` + `r1_violation_km` (spread − 8km).
- `:536` `r1_avg_pairwise_cosine` (kierunkowy rozjazd dostaw).
- `:547` `r1_new_drop_dist_km`.
- `:571-576` `pickup_spread_km` + `r5_violation_km`.

### 1.2 C1-a — KLUCZ SELEKCJI nie ma osi geometrii (SOURCE, LIVE)
`objm_lexr6.py:29-47` `lex_qual(c)` — kanoniczny klucz jakości selekcji (warstwa L7), wpięty 5× w pipeline (`_best_effort_objm_pick`, `_objm_lexr6_d2_pick`, `_feas_carry_readmit_pick`, `_best_effort_r6_would_redirect`):
```
r6 = objm(c, "objm_r6_breach_max_min")
base = (r6…, objm(c,"late_pickup_committed_max")||0, objm(c,"new_pickup_late_min")||0)
# + opcjonalnie prepend post_shift_overrun_penalty
```
**= czysto czasowe; ZERO `deliv_spread`/`cosine`/`km`.** Geometria policzona (1.1) jest gotowa na kandydacie, ale ŻADEN klucz selekcji jej nie czyta.
- **Warstwa zakodowana:** geometria żyje WYŁĄCZNIE jako SOFT-kara w `score` (L6): `dispatch_pipeline.py:4624` `bonus_r1_soft_pen = _r1_viol * R1_spread_per_km(-8.0)`; `:5239-5240` cross-quadrant `score *= 0.1`; `:4826-4831` `V326_WAVE_VETO_KM_THRESHOLD` bonus-veto.
- **Warstwa poprawna:** L7 selekcja (klucz `lex_qual`) lub L5 HARD (cap).
- **Dlaczego LIVE szkodzi:** ścieżka scarcity (`_best_effort_objm_pick`, `ENABLE_BEST_EFFORT_OBJM_R6_KEY` effective=ON) i feasible d2-pick (`ENABLE_OBJM_LEXR6_SELECT` ON) wybierają po `lex_qual` = czysto czasowo → kandydat z odbiorem w centrum (najniższy czas) wygrywa mimo dostaw zachód+wschód. Seed-oracle (`pending_global_resweep.jsonl` 30.06 10:04): best_effort 447 `deliv_spread=10.12`, `r1_cos=-0.987`, wybrany; spread>8km w 152/426 wpisów PO global_allocate.

### 1.3 C1-b — geometria NIE rejectuje w feasibility (SOURCE, LIVE)
`feasibility_v2.py:494` komentarz wprost: „R1 spread outlier — SOFT (NIE hard block, zweryfikowane audytem 2026-05-21)"; `:504-505` `if spread_km > R1_MAX_DELIV_SPREAD_KM:` → **tylko `metrics["r1_violation_km"]`, ZERO `return ("NO",…)`**. To samo:
- R3 `:498/502` `_dynamic_bag_cap` „również zsoftowany" (komentarz `:44` „za ostry") — liczony do metryki, nie bramkuje.
- R5 `:573-574` `pickup_spread_km > 2.5` → metric-only (`:578-579` „Galeria Biała JEST PO DRODZE").
- R7 `:486` `if bag and r7_ride_km > LONG_HAUL_DISTANCE_KM and r7_in_peak: return NO` — ale `LONG_HAUL_DISTANCE_KM=99.0` → **HARD-bramka geometrii istnieje, NIGDY nie odpala** (TODO C3 `:471` „refactor to soft" nigdy nie zrobiony). Klasa K nakłada się na C.
- **Warstwa poprawna:** L5 HARD (geometryczny cap, tier-aware — SOFT nie może osłabić HARD R6 35 T1/2 / 40 T3).
- **Status:** geometria HARD-bramka w L5 = **nie istnieje** (jedyna `LONG_HAUL` zneutralizowana stałą).

### 1.4 C1-c — jedyna L8-eskalacja geometrii za-wąska + override wyrzuca ostatni ślad (SYMPTOM, LIVE)
- `dispatch_pipeline.py:6443` `if len(feasible) >= 2:` → `_all_greedy_fallback AND _all_negative_cos` → KOORD `geometry_blind_fallback`. **Bramka wymaga feasible≥2 → NIE odpala przy pool_feasible=0** (scarcity, 43-45% ticków szczytu) ani przy feasible=1.
- `dispatch_pipeline.py:6751` best_effort sortuje `_best_effort_sort_key` (gdzie `-score` był 5-tym tie-breakiem niosącym geometrię), ALE `:6771-6787` override `ENABLE_BEST_EFFORT_OBJM_R6_KEY` (effective=ON) zastępuje `best` przez `_best_effort_objm_pick` (lex_qual = czysto czasowy) → **ostatni ślad geometrii (`-score`) wyrzucony**.
- **Warstwa poprawna:** L8 werdykt MUSI obejmować pool=0 (geometryczny KOORD-redirect pod scarcity).

**Dlaczego wracał (forensyka seed):** wszystkie ≥4 łatki „dyskryminacji pozycji" dodawały świadomość POZYCJI do tego samego czasowego klucza — NIGDY osi KIERUNKU/ROZJAZDU. Łatano złą oś.

---

## 2. C2 — soon_free: obliczenie/podmiana w scoringu (L6), nie w puli (L4)

**Reguła:** kurier kończący worek „zaraz" (≤12min) powinien być dostępnym kandydatem (busy→soon-free).

### 2.1 Gdzie ZAKODOWANE (źle — L6)
`dispatch_pipeline.py:3620-3627` wewnątrz `_v327_eval_courier_inner` (pętla eval per-kurier = warstwa scoring L6):
```
soon_free_probe = _soon_free_probe(cid, bag_raw, now)   # _soon_free_probe def :2342
if soon_free_probe.eligible and C.decision_flag("ENABLE_SOON_FREE_CANDIDATE"):
    courier_pos = tuple(soon_free_probe["last_drop_coords"])   # PODMIANA POZYCJI
    bag_raw = []; bag_sim = []                                  # PODMIANA WORKA
    soon_free_applied = True
```
To jest **podmiana tożsamości kandydata (pozycja+worek) w warstwie scoringu**, nie dodanie kandydata do puli. Komentarz `:3617-3619` przyznaje strukturalny defekt: *„substytucja zamiast drugiego kandydata (ten sam cid w mapach downstream nie może wystąpić 2×)"* — kurier może być rozważony ALBO jako busy ALBO jako soon-free, NIGDY oba, bo robi to in-place w eval-loop zamiast wyemitować projektowanego-dostępnego kandydata na poziomie puli.

### 2.2 Gdzie POWINNO (L4 — pula)
`courier_resolver.py:1383` `dispatchable_fleet()` — **żadnej gałęzi busy→zaraz-wolny**; jedyna projekcja dostępności to `pre_shift` (`:1526/1566/1578` start zmiany). Look-ahead dostępności dla zajętych kurierów = nieobecny w L4, scedowany na podmianę w L6.
- **Warstwa poprawna:** L4/L1 — uogólniona projekcja `free_at_min` dla WSZYSTKICH populacji (pre_shift + busy-z-planem + busy-bez-planu), z możliwością ODROCZENIA/REZERWACJI w feasibility/selekcji.

### 2.3 Stan
`ENABLE_SOON_FREE_CANDIDATE` effective=**OFF** (A3 §2c; recon C). Probe biega ZAWSZE (telemetria `soon_free_*`), podmiana inert. **LATENT** wrong-layer — struktura w złej warstwie obecna, ale dziś nieaktywna (`soon_free_applied=0/3290` na żywo wg seed). Mina przy flipie. dedup → R6-look-ahead seed-audytu (PLAUSIBLE).

---

## 3. C3 — HARD-bypass PO guardzie feasibility (FEAS_CARRY_READMIT)

**Reguła-strażnik P0:** `_assert_feasibility_first` — żaden `feasibility_verdict=='NO'` NIE w puli selekcji (SOFT nie obejdzie HARD).

### 3.1 Guard (jednorazowy, L8)
`dispatch_pipeline.py:2480` `def _assert_feasibility_first(feasible, order_id)` — FAIL-LOUD `INV_FEASIBILITY_FIRST_VIOLATION` gdy w `feasible` jest `feasibility_verdict=='NO'`. Wołany **RAZ** `:5938`.

### 3.2 Mutacja PO guardzie (L7, re-admituje NO)
`dispatch_pipeline.py:6266-6295` (≈360 linii PO guardzie 5938, w tym samym `assess_order`):
```
if C.decision_flag("ENABLE_FEAS_CARRY_READMIT"):
    _fcr = _feas_carry_readmit_pick(top, feasible, candidates, …)  # candidates = PEŁNA pula z NO!
    if _fcr_cand is not top[0]:
        _fcr_cand.feasibility_verdict = "MAYBE"          # :6278 — PRZEPISUJE predykat guarda
        top.pop(_fcr_idx); top.insert(0, _fcr_cand)      # :6291-6292
        if _fcr_cand not in feasible: feasible.insert(0, _fcr_cand)  # :6295
```
**To jedyna post-guard mutacja re-admitująca kandydata SPOZA `feasible`** (z `candidates`, czyli z NO). Komentarz `:6263-6265` przyznaje: „bramka candidata dalej zwraca NO; tu selekcja przenosi go, **promote verdict→MAYBE dla spójności … inwariantu**". Czyli readmit ŚWIADOMIE przepisuje to samo pole (`feasibility_verdict`), które guard sprawdza → nawet ponowne uruchomienie guarda by tego nie złapało. Guard ślepy poza swój call-site (wzorzec #10).

### 3.3 Kontrast — pozostałe post-guard mutacje są SAFE (NIE re-admitują NO)
- `:5996-6001` `_objm_lexr6_d2_pick(feasible)` → `feasible.pop/insert` — wybiera Z `feasible` (sam MAYBE), tylko reorder.
- `:6239` `_pln_pure_resort(top)` → `top.sort` (`:1092`) — resort istniejących, brak insert NO.
- `:5980-5987` tier-sort `feasible.sort` — reorder.
→ Tylko FEAS_CARRY_READMIT bierze `candidates` (z NO) jako wejście. To jest THE C3.

### 3.4 Stan + werdykt
- `ENABLE_FEAS_CARRY_READMIT` effective=**OFF** (A3; rolled-back hot 27.06). Instrument walidujący = **VOID** (A4 #3: „realny readmit 4/2816=0,14%", join w przestrzeni predykcji bez delivered_at). 
- Protokół OBALONE: „FEAS_CARRY_READMIT „łamie feasibility-first" — mutacja TYLKO obniża breach" → świadomie ACK jako bezpieczny PRZYPADEK. **Ale STRUKTURA wrong-layer pozostaje** (HARD-decyzja re-dopuszczenia w L7 zamiast w L5 feasibility) — LATENT, mina na flipie (C2 protokołu: „co flaga ODSŁANIA"). still_open jako latentny wzorzec.

---

## 4. C4 — patch na renderze (L10), gdy źródło w silniku

### 4.1 C4-a — floor `pickup ≥ shift_start` jako render-clamp konsoli (SYMPTOM, LIVE; źródło OPEN)
Konsola `nadajesz_clone/panel/backend/app/integrations/ziomek/fleet_state.py`:
- `:250` `_eta_chain(...)` re-implementuje OSRM-chain ETA; `:254` + `:755` `clamp_preshift_eta = flag("CLAMP_PRESHIFT_PICKUP_ETA")`; `:853-863` floruje odbiór pre-shift do `shift_start`.
- **Źródło defektu w SILNIKU:** `plan_recheck.py:554-594` regeneruje `courier_plans.json` co 5 min BEZ floor shift_start (A6 grupa 6 #5 „LEAK — najszersza dziura") → render konsoli łata floor PO fakcie.
- Pamięć wprost: „to 1 z 4 floorów — pas bezpieczeństwa, NIE pełny fix". **Render-patch LIVE, źródło (L9 plan_recheck leak) wciąż otwarte.**
- **Warstwa poprawna:** L9 (plan_recheck floor) + L5 (feasibility, już ma `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP`). dedup → R4 (17 powierzchni floor, A6 — NIE re-derywuję; tu tylko render-instancja konsoli jako C4).

### 4.2 C4-b — carried-first / route-order re-implementowane na renderze (SYMPTOM, LIVE)
Reguła kolejności jazdy (carried-first relax, no-return) ma źródło L9 `plan_recheck._apply_canon_order_invariants`, ale renderery re-implementują KOPIĘ:
- Konsola `fleet_state.py:395` `_build_route` + `:438-443` carried-first relax za `TRUST_CANON_ORDER`; parytet **statystyczny 95.9%** (`:866-867`), NIE z importu → 4,1% rozjazd z konstrukcji.
- Apka `courier_api/courier_orders.py:467` `_prioritize_carried_dropoffs` (3. kopia, fallback gdy `BUILD_VIEW_TRUST_CANON_ORDER` OFF) + `:822/872` `_attach_fallback_eta`/`FROZEN_PICKUP_ETA` (render trzyma czas committed) + `:794` `_compute_live_eta` (self-compute now+drive).
- **Warstwa poprawna:** L9 (jedno źródło kolejności), renderery = czysty odczyt. dedup → R2 (route-order, 5 kopii / 2 repo, monitor `ziomek_time_route_monitor` 44-75/d — A5/A6 J-class). Tu jako C4 (render re-koduje regułę silnika).

---

## 5. C-adjacent — HARD-reguła w warstwie SOFT (split-layer, niższa waga)

### 5.1 C-adj-1 — HARD rejecty liczone w bloku scoringu (L6), nie w `check_feasibility_v2` (L5)
Cztery HARD-rejecty są EWALUOWANE w bloku scoringu (`dispatch_pipeline.py:4420-4555`, po policzeniu planu/score) i APLIKOWANE jako verdict-override `:5610-5653` (`MAYBE→NO`), zamiast w L5 `check_feasibility_v2`:
- `:5637-5646` `v3273_wait_courier_hard_reject` — źródło `scoring.py:110-164` `compute_wait_courier_penalty` zwraca `(0.0, True)` dla wait>20min (`:150-151`, docstring `:129` „>20 min → HARD REJECT"). **HARD-bramka żyje w scoring.py (L6 SOFT)**; `feasibility_v2` jej NIE konsumuje (grep=0).
- `:5610-5612` `v324a_extension_hard_reject` (>60min); `:5619-5624` `carry_chain_hard_reject`; `:5650-5653` `intra_rest_gap_hard_reject`.
- **Warstwa poprawna:** L5 `check_feasibility_v2`. **SAFE-by-construction:** te override'y dzieją się WEWNĄTRZ `_v327_eval_courier_inner` PRZED złożeniem puli i PRZED guardem `:5938` → kandydat z verdict=NO jest poprawnie odsiany. Więc NIE jest to bypass (jak C3) — to split-layer (HARD logika rozsiana L5+L6). Ryzyko = N-kopii/which-layer ambiguity, nie żywy bug. dedup → hard-reject-in-scoring (osobny od C3).

### 5.2 C-adj-2 — R-RETURN-„VETO": L5 metric-only, egzekucja w L9 kanonie
- `feasibility_v2.py:904-914` `ENABLE_R_RETURN_TO_RESTAURANT_VETO` → TYLKO `metrics["return_to_restaurant_oid/return_to_restaurant"]`; komentarz `:904` „instrumentacja NIGDY nie przerywa feasibility". **Nazwa „VETO" sugeruje L5 HARD reject; ścieżka L5 = metric-only.**
- Realny zakaz w L9 kanonie: `plan_recheck.py:942` `_detect_departed_pickup_revisit` → `:1514-1519` `ENABLE_NO_RETURN_TO_DEPARTED_PICKUP` odrzuca permutacje z powrotem na odbiór.
- **Split:** reguła nazwana/instrumentowana w L5, egzekwowana w L9. dedup → return-veto-split (A2 klasuje też jako I/B — nazwa-vs-zachowanie + asymetria; tu warstwowy aspekt C).

---

## 6. MACIERZ REGUŁA → WARSTWA-ZAKODOWANA → WARSTWA-POPRAWNA → POPRAWNOŚĆ

| Reguła | Zakodowana | Poprawna | Werdykt C | Plik:linia (świeży) |
|---|---|---|---|---|
| geometria-rozjazdu → klucz selekcji | brak (lex_qual czasowy) | L7 | **C1-a** | `objm_lexr6.py:29-47` |
| geometria-rozjazdu → score | L6 SOFT | (też L5/L7) | C1 (kanon) | `dispatch_pipeline.py:4624/5239/4826` |
| R1/R3/R5 spread → bramka | L5 **metric-only** | L5 HARD cap | **C1-b** | `feasibility_v2.py:504/498/573` |
| R7 long-haul → bramka | L5 dead (LONG_HAUL=99) | L5 (soft, TODO) | **C1-b**+K | `feasibility_v2.py:486` |
| geometry-blind escalation | L8 za-wąska (feasible≥2) | L8 pool=0 też | **C1-c** | `dispatch_pipeline.py:6443` |
| soon-free availability | L6 podmiana pozycji | L4 pula | **C2** | `dispatch_pipeline.py:3620-3627` vs `courier_resolver.py:1383` |
| feas-carry re-admit | L7 PO L8-guardzie (bypass) | L5 feasibility | **C3** | `dispatch_pipeline.py:6266-6295` vs guard `:5938` |
| pre-shift floor | L10 render-clamp | L9 plan_recheck+L5 | **C4-a** | `fleet_state.py:853-863`; źródło `plan_recheck.py:554-594` |
| carried-first/route-order | L10 render ×3-4 | L9 kanon | **C4-b** | `fleet_state.py:395/438`, `courier_orders.py:467` |
| frozen pickup ETA | L10 apka render | L5/L9 R27 | **C4-b** | `courier_orders.py:822/872` |
| R9 wait>20 HARD | L6 scoring-block | L5 feasibility | **C-adj-1** | `scoring.py:150-151`→`dispatch_pipeline.py:5637` |
| ext>60 / carry_chain / intra-gap HARD | L6 scoring-block | L5 feasibility | **C-adj-1** | `dispatch_pipeline.py:5610/5619/5650` |
| R-RETURN-„VETO" | L5 metric-only + L9 | L5 (nazwa HARD) | **C-adj-2** | `feasibility_v2.py:904-914` + `plan_recheck.py:942` |

---

## 7. TABELA POKRYCIA (jawne — nie cisza)

**Zbadane moduły/symbole (świeży grep):**
- `objm_lexr6.py` (cały, lex_qual/bucket/pick) · `feasibility_v2.py` (R1/R3/R5/R7 metryki 471-576, R-RETURN-VETO 904-914) · `dispatch_pipeline.py` (geometria SOFT 4624/5239/4826, geometry_blind_fallback 6443, best_effort override 6745-6814, soon_free 3605-3627 + _soon_free_probe 2342, _assert_feasibility_first 2480/5938, FEAS_CARRY_READMIT 6266-6301 + _feas_carry_readmit_pick 1289, post-guard sąsiedzi 5980/5996/6239, verdict-override HARD 5610-5653) · `scoring.py` (compute_wait_courier_penalty 110-164) · `courier_resolver.py` (dispatchable_fleet 1383, pre_shift 1526-1581) · `plan_recheck.py` (no-return 942/1514, floor leak okolica 554-594) · konsola `fleet_state.py` (_eta_chain 250, _build_route 395, CLAMP 853, TRUST_CANON 443) · apka `courier_orders.py` (build_view 1072, _prioritize_carried_dropoffs 467, _attach_fallback_eta 822, FROZEN 872).

**Zbadane przyrządy/flagi (z A3/A4, nie re-mierzone systemctl w tej lane):** stany efektywne ENABLE_BEST_EFFORT_OBJM_R6_KEY=ON, ENABLE_OBJM_LEXR6_SELECT=ON, ENABLE_SOON_FREE_CANDIDATE=OFF, ENABLE_FEAS_CARRY_READMIT=OFF, CLAMP_PRESHIFT_PICKUP_ETA=ON(env), FROZEN_PICKUP_ETA=ON, TRUST_CANON_ORDER=ON.

**LUKI POKRYCIA (jawne + powód):**
1. **NIE runtime-zweryfikowałem** że C1 produkuje zły pick na konkretnym replay-case (read-only DoD); oparte na seed-oracle `pending_global_resweep.jsonl` (case 447, 30.06) — to PROXY-certyfikowane (button-truth), nie ground-truth. Faza C/E: odpalić oracle (brute-OSRM permutacji vs lex_qual pick).
2. **NIE wyliczyłem 17 powierzchni floor** dla C4-a — A6 grupa 6 / seed pre-shift-floor zrobiły to wyczerpująco; tu TYLKO render-instancja konsoli (`_eta_chain` clamp). dedup do R4, nie re-derywacja.
3. **NIE prześwietliłem `courier_api_panelsync/courier_orders.py`** (665L fork) pod kątem C4-kopii — A5/A6 oznaczyły MARTWY/niesserwowany; pominięty świadomie (klasa K).
4. **NIE czytałem `courier-app` Kotlin** lokalnego re-sortu/ETA — A6 luka #1 (render serwerowy przez courier_api; lokalny re-sort niezweryfikowany). Faza B/J.
5. **NIE potwierdziłem runtime** że C-adj-1 (scoring-layer HARD reject) NIGDY nie przepuszcza NO do puli — argument konstrukcyjny (override przed złożeniem puli + przed guardem), nie dowód na case. Oznaczone SAFE-by-construction, do runtime-verify gdyby eskalować.
6. **Most paczki / parcel lane** — NIE sprawdzony pod własną kopią route-order/floor (A6 luka #2). Granica zachowana (NIE Mailek/Papu).
7. **Wartości numeryczne progów** (R1=8, LONG_HAUL=99, SOON_FREE_MAX=12, cap=40) zinwentaryzowane jako rejestr; pełen rozsyp = klasa N (osobny agent).

**NIE-luki (świadomie poza C):** sentinele (0,0)/BIALYSTOK_CENTER = klasa M (most do C1/C4 floor, raportuje agent M). Asymetria bliźniaków pre-shift floor feasibility↔plan_recheck = klasa B (A6 R4). Dryf flag efektywnych = A3/D.

---

## 8. HANDOFF Faza E/F

- **C1-a/b/c = JEDEN root** „one selection key + geometria do wyboru" (R1 / P0-A seed). Konsolidacja: człon rozjazdu z JUŻ-serializowanych metryk do KANONU `lex_qual` jako tie-break PO osi R6 — dopiero PO `objm-lexr6-unify` (3 kopie + d2 + best_effort RAZEM). **SOFT geometria NIE może osłabić HARD R6 tier-aware** (35 T1/2, 40 T3). NIE liczyć jako 3 chaosy.
- **C2** zwija się do R6-look-ahead seed (PLAUSIBLE): fix = `free_at_min` w L4 dla wszystkich populacji + wycofać scoringowy probe. RAZEM wszyscy konsumenci `dispatchable_fleet`.
- **C3** = wzorzec#10 (HARD-bypass post-guard). Latent (flaga OFF). Przy ewentualnym re-flipie: instrument VOID → najpierw oracle (A4 #3), guard nie wystarczy (readmit przepisuje jego predykat). Rozważyć przeniesienie decyzji re-admit do L5 (feasibility zwraca MAYBE-z-carry-regret) zamiast L7-mutacji.
- **C4-a → R4** (floor, A6 grupa 6); **C4-b → R2** (route-order, A6 grupa 2 / A5 J). Render-patch = krawędź; źródło w L9. PoC „one route-order module" musi przepiąć 4 powierzchnie (silnik+konsola+courier_api+apka), inaczej kopia wraca.
- **C-adj-1/2** = niższy priorytet (SAFE/architektura). Dług: ujednolicić gdzie żyją HARD-rejecty (L5 vs L6 verdict-override) + nazwa „VETO" vs L5 metric-only (też I-class — A2/Faza D precedencja).
