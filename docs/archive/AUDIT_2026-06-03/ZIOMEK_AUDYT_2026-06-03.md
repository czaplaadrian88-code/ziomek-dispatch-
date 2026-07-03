# AUTONOMICZNY AUDYT ZIOMKA DISPATCHERA
**Data:** 2026-06-03 · **Zakres:** 114k LOC, 108 flag, ~120 MB logów decyzji · **Metoda:** 11 równoległych agentów audytu + adwersarialna weryfikacja P0/P1 (79 sub-agentów, 3,46 M tokenów) + niezależny przebieg empiryczny audytora po logach (709 decyzji z realnym outcome).

> **Status wyników:** 118 findingów (17 P0 / 51 P1 / 36 P2 / 14 P3). 68 zweryfikowanych adwersarialnie, 3 obalone (oznaczone). Każdy finding gruntowany dowodem `plik:linia` lub statystyką z logu.

---

## 0. JEDNO ZDANIE, KTÓRE TRZEBA ZROZUMIEĆ NAJPIERW

**Ziomek to dziś dojrzały, dobrze ufortyfikowany system DORADCZY (proposer), który w produkcji działa na ~1-2% realnej autonomii — przypisania robi człowiek w panelu. Cel „pełna autonomia czerwiec'26" jest niemożliwy do osiągnięcia nie dlatego, że kod jest słaby, lecz dlatego, że (a) ścieżka faktycznego auto-przypisania NIE ISTNIEJE w kodzie, oraz (b) człowiek dziś ręcznie maskuje 5 realnych luk jakościowych, które przy autonomii puściłyby błędne decyzje bez nadzoru.**

To przeformułowuje całe zadanie: droga do autonomii = najpierw ZBUDOWAĆ pętlę auto-assign, ale wcześniej ZAMKNĄĆ luki jakościowe, które dziś łapie człowiek. Reszta raportu pokazuje, które to luki i w jakiej kolejności.

---

## ETAP 1 — MAPA SYSTEMU

### 1.1. Przepływ decyzji (zweryfikowany w kodzie)

```
WEJŚCIE
 • panel_watcher.py → scrape gastro.nadajesz.pl (HTML+CSRF, tick 20s) → event_bus NEW_ORDER
 • courier_resolver.dispatchable_fleet() → snapshot floty:
     GPS (gps_positions_pwa.json) | pos_source: gps|last_delivered|last_picked_up|pin|no_gps
     tier_bag (courier_tiers.json) | shift z Google Sheets | bag z orders_state+panel_packs
 • osrm_client → route/duration + STATYCZNA tabela korków (dzień×godzina)
 • LGBM (ml_inference) → SHADOW only
 IGNOROWANE: korki live, pogoda, niezawodność kuriera, PRZYSZŁE zamówienia (pending_queue zbierany, nieużywany)
        │ shadow_dispatcher._tick → assess_order
        ▼
BRAMKI WCZESNE: G0 brak geo→SKIP · G1 odbiór ≥60min→KOORD early_bird
        ▼
CANDIDATE GEN (ThreadPool=10): per kurier → bundle detect L1/L2/L3 → chain_eta →
  check_feasibility_v2 HARD GATES: bag_full(≥8) · pickup_too_far(>15km) ·
  v325 schedule gates (fail-CLOSED) · R6 per-order >35min · shift_end_before_pickup
  [R1/R5/R8 = SOFT, tylko kara] → score (0-100 baza) → +~19 bonusów/kar → OBJ freshness V4
        ▼
SELECTION (klucz LEKSYKOGRAFICZNY, nie czysty score):
  (tier2_late_pickup, pos_source_bucket{informed=0/other=1/blind+pre_shift=2}, -(score−late_pen), orig_rank)
  + demote blind_empty + late-pickup tiering | SHADOW: late_pickup/r6_danger/selection_veto
        ▼
  feasible≥1 ─► 5-7 BRAMEK KOORD-REDIRECT (pierwsza trafia=return):
                state_stale · geometry_blind · score<-100 · commit_divergence>10min(ON)
                · difficult_case<-30(OFF) · best_effort_r6>35(ON) → inaczej PROPOSE
  feasible=0 ─► best_effort (sort r6→sla→duration) lub SOLO fallback → PROPOSE/KOORD
        ▼
AUTO-ROUTE CLASSIFIER (tylko na PROPOSE): EDGE→ALERT/ACK | T1: pool≥2 ∧ margin≥15 ∧
  tier∈{gold,std+} ∧ score≥50 → AUTO | inaczej ACK
        ▼
OUTPUT: shadow_decisions.jsonl
  • PROPOSE → kolejka Telegram (CZŁOWIEK klika ASSIGN)
  • KOORD/SKIP → tylko log (NIE idzie do Telegrama = CISZA dla operatora)
  • AUTO → renderuje TYLKO tekst „w trybie auto poszłoby samo" (telegram_approver.py:771) — NIC nie przypisuje
```

### 1.2. Co Ziomek IGNORUJE (sygnały, które człowiek-dispatcher wykorzystuje)
| Sygnał | Stan | Skutek |
|---|---|---|
| Korki real-time | Statyczna tabela dzień×godz. | R6 breach w peak / anomalie dni |
| Niezawodność kuriera | `courier_ground_truth.json` (92 rek.) **martwy w selekcji** | nie unika kuriera „w złym dniu" |
| Przyszłe zamówienia | `pending_queue` zbierany, **nie konsumowany** | greedy per-order, zgubiony bundling |
| Kalibracja błędu OSRM | `drive_min_calibration_log_v2` (1,4 MB) liczona, **nieaplikowana** do live ETA | systematyczne niedoszacowanie → 14% R6 breach |
| LGBM | NDCG@5=0.852, **tylko shadow** | rule-scoring zamiast nauczonego modelu |
| Skok/teleport GPS, accuracy | brak filtra | błędny fix wchodzi jako „świeży GPS" |

### 1.3. Konflikty kolejności bramek (zweryfikowane)
- **SELECTION (late-pickup score-first) może wybrać kuriera z gorszym R6, którego twarda bramka R6 NIE złapie** — bo `best_effort_r6` działa tylko na pustym `feasible`. Zwycięzca z R6=50min przechodzi jako PROPOSE (`r6_danger_shadow` zmienił zwycięzcę w 49/254 decyzji). → **GATE-02 / R6BREACH-01**.
- difficult_case (V5), commit_divergence (V4), low_score (V3) testują nakładające się „zła propozycja" — pierwsza wygrywa, reszta martwa.

---

## ETAP 2 — AUDYT HISTORYCZNYCH DECYZJI (empiryczny, z logów)

**Źródła:** `shadow_decisions.jsonl(.1)` (n=2641, 10 dni), `backfill_decisions_outcomes_v1.jsonl` (709 decyzji **z realnym outcome**), `sla_log.jsonl` (6813), `drive_min_calibration_log_v2`, `c2/r6/late shadow`.

### 2.1. Twarda prawda o jakości (709 decyzji z outcome — niezależny przebieg audytora)
| Metryka | Wartość | Komentarz |
|---|---|---|
| **Realne złamania reguły 35 min (R6)** | **14,0%** (97/693), p90=37,8 min, max=90,8 | Co 7. dostawa za zimna |
| **Systematyczny błąd predykcji ETA** | **mediana +11,1 min** (real − predykcja), p90 +30 | Ziomek chronicznie niedoszacowuje |
| **Pozycja z realnego GPS** | tylko **27%** | 73% na pozycji-proxy/fikcji |
| **Bliskie remisy #1 vs #2 (margin<5)** | **45%** | Prawie połowa rankingów = remis w szumie |
| **Ścieżka `ASSIGN_DIRECT` Ziomka** | **7/709 = 1,0%** | reszta: TIMEOUT_SUPERSEDED 406 + PANEL_OVERRIDE 296 |
| **SLA 35 min twarde przekroczenia** | 12,6% (834/6813), p95=43,5 min | spójne z 14% wyżej |

### 2.2. Konkretne złe decyzje (z `order_id`)
- **R6BREACH-01 (P0):** `r6_danger_shadow` (WYŁĄCZONY) wyłapałby **79 żywych picków łamiących 35 min**, gdzie w puli był kurier zgodny z regułą:
  - `oid=477544` LIVE=457 (Adrian, carry **110,9 min**) → SHADOW=484 carry 18,6 min (**save 92 min**)
  - `oid=477827` LIVE=370 carry 75 min → SHADOW=123 carry 8,8 min (**save 66 min**)
  - `oid=477832` LIVE=400 carry 56,8 → SHADOW=123 carry 15,2 (**save 42 min**)
  - Wszystkie verdict=PROPOSE, pool_feasible 4-7 (były lepsze opcje). W realu człowiek skorygował.
- **BUNDLE-01 (P0):** 126 worków łamiących R6 proponowanych zamiast eskalacji; symulator objektywu widzi breach w 72% z nich a i tak PROPOSE. `oid=477154` (Chicago Pizza) r6=61,4 min; `oid=476219` r6=53,2.
- **DETOUR-01 (P2):** `oid=477347` — bundle z detourem odbioru **9,1 km** z dodatnim score (zbędne ~15-25 min).
- **ZOMBIE-01:** `oid=476621` re-ewaluowany jako kandydat bundla z carry **1463 min (24h)** — zatruwa statystyki.
- **BIAS-01 (P1):** koncentracja na 3 kurierach: Ziomek 46,6% propozycji vs ludzie 31,5%. Skutek w `sla_log`: kurier 484=95 przekroczeń, 400=76, 393=61 — **przeciążone top-kurierki** to bezpośredni driver SLA breach.

### 2.3. Override człowieka — uczciwa interpretacja (finding SELECT-01 **OBALONY**)
Pierwotny finding głosił „0% zgodności" (0/296). **Weryfikacja go obaliła:** pole `actual_courier_id` jest zapisywane WYŁĄCZNIE przy override, więc 0/296 to tautologia pomiarowa. **Realna zgodność top-1 ≈ 15-18%**, override ≈ **84,6%** — co pokrywa się z udokumentowanym baseline (`rebuild_courier_whitelist.py`: „PANEL_OVERRIDE rate ≈ 75-85%"). Wniosek poprawiony: ranking #1 Ziomka NIE jest jeszcze production-grade (zgodność musi wzrosnąć do >80% zanim auto-assign top-1 będzie bezpieczny), ale to NIE jest „100% pomyłek". Korpus 333 niezgodności = najcenniejsze dane uczące, których brakuje.

---

## ETAP 3 — AUDYT REGUŁ I FLAG

### 3.1. Tabela reguł (wybór najważniejszych — pełna w sekcji RULE TABLE poniżej)
| REGUŁA | CEL | KORZYŚĆ | SKUTKI UBOCZNE | REKOMENDACJA |
|---|---|---|---|---|
| **AUTO_APPROVE_THRESHOLD=130 / MIN_GAP=10** | auto-zatwierdzenie | jedyny mechanizm autonomii | **ZERO call-site** — martwe stałe, autonomia 0% zaimplementowana | **ZBUDUJ ścieżkę auto-assign** albo usuń (to największa luka vs cel) |
| **shadow_mode:true (flags.json)** | „nie przypisuj, loguj" | miało chronić przed live-assign | **martwa flaga** — nigdzie nie czytana; `dispatch-shadow.service` to PRODUKCYJNY producent (220/254 PROPOSE→Telegram) | USUŃ lub udokumentuj że nazwa historyczna; mylące |
| **ENABLE_DRIVE_MIN_CALIBRATION_V2 (OFF)** | korekta optymistycznego OSRM | mniej spóźnień (OSRM 99,8% under-estimate dla no-GPS) | offset **płaski +281%** na trasach <10min (82% ruchu) → ryzyko masowego INFEASIBLE; logowany 2× (G5) → fałszywe metryki flip-gate | **NIE flipuj na ślepo 03.06**: napraw 2×-log, replay feasibility, offset proporcjonalny+cap |
| **ENABLE_OBJ_F3_BEST_EFFORT_R6_KOORD (ON)** | R6-breach → KOORD | nie proponuje rażąco spóźnionych | **sprzeczne z ZAWSZE-PROPONUJ**: 35% KOORD = cicha eskalacja | FLIP na PROPOSE+marker „⚠ R6 breach Xmin" (jak difficult_case) |
| **MIN_PROPOSE_SCORE=-100** | beznadziejny→KOORD | nie proponuje śmieci | sprzeczne z ZAWSZE-PROPONUJ; hardcode nieskalowalny | PROPOSE+marker „low confidence" lub próg adaptacyjny (percentyl) |
| **ENABLE_SELECTION_VETO_SHADOW (ON)** | veto kierunkowe | diagnoza pod-prąd-kierunku | **zmienia 0,9% decyzji** — czysty narzut | **RETIRE** (nie flip) — problem jest w KLUCZU selekcji, nie w kolejnej karze |
| **ENABLE_LATE_PICKUP_HARD_GATE (ON)** | odbiór >5min→NO | egzekwuje +5min, kasuje stare „+1h" | **bomba skali**: gdy odrzuci WSZYSTKICH→KOORD-cisza; dziś 0%, przy 5-10x masowo | dodać fallback defer-and-propose zamiast NO→KOORD |
| **BAG_TIME_HARD_MAX=35** | termika jedzenia (R6) | rdzeń jakości | globalna (pizza=lody=paczka); precedens R-PACZKI-FLEX | zostaw; przy multi-tenant per-cuisine override |

### 3.2. SHADOW-DEBT (wartość zamrożona w cieniu — kluczowe odkrycie)
Mechanizmy LICZONE ale NIEAKTYWNE, które zmieniłyby decyzje:
- `late_pickup_shadow` / `r6_danger_shadow` / `selection_veto_shadow` annotują **86% decyzji** (219/254), ale nie działają.
- `r6_danger` zmienił zwycięzcę w **21,9%** (100/457) → **realna wartość**, flip po walidacji.
- `late_pickup_shadow` zmienia **19,4%**.
- `selection_veto` zmienia **0,9%** → **RETIRE**, nie flip.
- `drive_min_calibration_v2`: 4706 wpisów, `main_path_active=FALSE` w 3000/3000 (nigdy nie zastosowany).

### 3.3. Higiena konfiguracji (ryzyko operacyjne przy skali zespołu)
- **CONFIG-DUAL-01:** dwa źródła prawdy — `flags.json` (108 kluczy) vs `override.conf` env (~20 flag). Kod `getattr(C,FLAG) or C.flag(FLAG)` → env wygrywa → `flags.json` KŁAMIE (np. `ENABLE_R1_PROGRESSIVE_CLIP` ON w env, OFF/nieobecny w flags.json). **Ten audyt sam musiał czytać override.conf.**
- **DEADFLAGS-01:** 14 z 29 flag OFF to martwy config (0 referencji): `load_balancing, deadhead_penalty, inter_courier_swap, end_of_shift_gravity, stability_penalty, priority_decay, tear_down_trigger, marginal_gain_threshold, confidence_score, decision_cooldown, flow_score, same_building, vector_filter, partial_split`.
- **R6FRESH-DUP-CONFIG-01:** `ENABLE_OBJ_R6_SOFT_DEADLINE` zdefiniowany DWUKROTNIE (service file + override.conf) — landmine: edycja jednego pliku nie zadziała.

---

## ETAP 4 — SYMULACJA AWARII

### 4.1. Wysokie obciążenie (50 / 100 / 200+ zamówień)
| Wąskie gardło | Próg pęknięcia | Dowód |
|---|---|---|
| **Panel scrape sekwencyjny** (`fetch_order_details` ~0,3s/zlec.) | **~2x** | tick 24 fetche=10-11,5s vs interwał 20s; mean 54,7 max 90 fetchów/tick |
| **OSRM table() bez cache** (świeży HTTP N×N/kandydat) | **~5x** | ~46-73 ms/kandydat liniowo |
| **orders_state.json 8 MB full-rewrite+fsync** per update, bez prune | **~5x** | 3505 zleceń akumulowanych; każdy upsert = R+W+fsync 8 MB pod LOCK_EX |
| **ThreadPool=10 / 4 vCPU** (OR-Tools 200 ms/kandydat) | **~5x** | przy 60 kurierach p95 2s→4-8s |
| **Latencja decyzji** już dziś p50 875 ms / p95 2s | margines mały | przy 12 kurierach — koliduje z regułą +5min przy gęstym peaku |
| **KOORD = cisza** (tylko PROPOSE→Telegram) | teraz | **7,3%** zleceń bez żadnej propozycji do operatora |
| **jsonl bez bounda** (consumer_stuck 27 MB poza logrotate) | ~10x / miesiące | disk-pressure |

> **Pozytyw:** `MAX_BAG_TSP_BRUTEFORCE=5` (5!=120) to **martwy kod na hot-path** (OR-Tools jest aktywny, cap 200 ms) — NIE jest zagrożeniem (TSP-BRUTEFORCE-07, obalono mit).

### 4.2. Braki kurierów (NAJGROŹNIEJSZA klasa — luka krytyczna)
- **FAIL-01 (P0):** kurier znika z workiem (`picked_up`) — **ZERO detekcji offline** (`grep courier_offline/heartbeat/last_seen/abandon = 0`). Zlecenia NIGDY nie wracają do puli (tylko panel status 8/9 to wyzwala). Jedzenie stygnie, klient czeka, **żaden alert**. Po 90 min znika z `cs.bag` ale staje się ghostem.
- **FAIL-02 (P0):** porzucony kurier (>90min picked_up) widziany jako **WOLNY** (`cs.bag=[]`) przy ZAMROŻONEJ pozycji → jeśli blisko nowej restauracji → wysoki score → **dostaje NOWE zlecenia**. Root cause: `courier_resolver.py:597` nie aplikuje `_bag_not_stale` do pozycji (a `:540` aplikuje do bagu). **Quick-fix.**
- **FAIL-06:** zero kurierów w strefie → R29 fallback = KOORD-cisza zamiast odroczenia z propozycją.
- **FAIL-07:** appka padła, kurier „na grafiku" → liczony jako dostępny z fikcyjną pozycją centrum.

### 4.3. Problemy restauracji
- **FAIL-04:** brak hard-guardu na zbyt krótki/błędny czas przygotowania — `RESTAURANT_PREP_VARIANCE_HARD_MIN` zdefiniowany ale **niepodłączony**. Kurier przyjeżdża za wcześnie, zegar termiczny startuje czekaniem.
- **FAIL-08:** wolna restauracja w worku kaskadowo opóźnia POZOSTAŁE zlecenia bundla (brak split-off).

### 4.4. Problemy lokalizacyjne i systemowe
- **PARSE-01 (P0):** częściowy/pusty parse HTML (HTTP 200) leci dalej — brak straży „nagłego spadku do 0". Panel zmienia layout → `order_ids=[]` → `fail_count=0` → CICHY blackout dispatchu. **To wzorzec incydentu 02.05 (16h+ blackout).**
- **GPS-01 (P0):** brak alarmu „cały feed GPS zamarł" — PWA server down w peak = cicha degradacja całej floty do proxy, bez sygnału.
- **FAIL-12:** awaria Google Sheet (grafik) → `NO_ACTIVE_SHIFT` **fail-CLOSED hard-reject całej floty** = masowe „brak kandydatów" przez cały ranny peak.
- **FAIL-09 (kaskada 10x):** peak + utrata panelu → pusty parse → packs cache stale → zajęci kurierzy widziani jako wolni → mass mis-dispatch.

---

## ETAP 5 — WERYFIKACJA GEOGRAFICZNA (Białystok)

- **GEO-01 (P1):** selekcja kuriera i bundling liczone **haversine × 1,37** (linia prosta), nie realną trasą (`dispatch_pipeline.py:2016` road_km, :1771 bundle L2, :1786 korytarz). ETA/feasibility używa OSRM, ale **WYBÓR kuriera jest ślepy na geometrię drogową**.
- **GEO-04 (P2):** zmierzony realny współczynnik OSRM/haversine na 12 parach = **1,16–1,81 (mediana 1,45 > 1,37)**. Pary przez bariery systematycznie >1,5: Starosielce↔Dojlidy 1,63, Antoniuk↔Nowe Miasto 1,64, Nowe Miasto↔Kleosin 1,81 → stała 1,37 **zaniża dystans cross-barrier o 14-32%**.
- **GEO-02 (P1):** **ZERO modelu barier** (rzeka Biała, tory PKP, mosty, jednokierunkowe, galerie, osiedla zamknięte — `grep river/rzeka/most/tory/one_way = 0`).
- **GEO-05 (P2):** mapa sąsiedztwa dzielnic ma błędy: `Sienkiewicza↔Wasilków` oznaczone jako sąsiednie mimo **6,8 km** (fałszywy bonus SIMILAR do worka); `Dojlidy↔Starosielce` (najgorszy ratio 1,63) za łagodnie jako SIDEWAYS zamiast OPPOSITE.
- **GEO-03 (P1):** kalibracja `drive_min` całkowicie **geo-ślepa** i **myli opóźnienie behawioralne kuriera** (czekanie na zmianę, 90% override) **z błędem routingu OSRM** — dopisuje płaski +30 min do każdego ETA niezależnie od rejonu.
> **Decyzje poprawne matematycznie, złe operacyjnie:** „najbliższy na mapie" kurier po drugiej stronie rzeki/torów. Dziś gryzie głównie w ścieżce SCORINGU (OSRM zdrowy 100% czasu → ETA OK), ale przy awarii OSRM cała flota spada na błędny 1,37.

---

## ETAP 6 — ANALIZA BUNDLOWANIA (najważniejsza część)

### 6.1. Stan faktyczny (empiryczny)
- **Bundling to dominujący tryb:** **63,4%** propozycji to worki (1513/2385).
- **Worki to główne źródło ryzyka R6:** **11,6% worków łamie 35 min vs 2,2% solo (5,3×)**.
- **R6 i R8 dla worków egzekwowane TYLKO jako SOFT** (kara/telemetria, nie odrzucenie) → **126 worków łamiących R6 idzie jako PROPOSE**.
- **System oceny worka jest w 80% nieaktywny:** 80,2% proponowanych worków ma `bonus_l1/l2/l3 = 0` — wygrywają bazowym score bliskości/tier.
- **Najmocniejsze bramki geometryczne WYŁĄCZONE:** V327 (cross-quadrant score×0.1), V326 (wave veto), intra-rest gap.
- **FIX_C (`ENABLE_BUNDLE_DELIV_SPREAD_CAP`) ON ale no-op** dla najgorszych worków (zeruje bonusy, których przeciw-kierunkowe worki i tak nie mają — 0 wystąpień).
- **R8 pickup span:** 25,6% worków przekracza twardy cap, mediana span **16,5 min** (jedzenie czeka), p90=35 min.
- **Najlepszy predyktor `objm_max_thermal_age_min`** (wiek jedzenia: mediana 26,6, p90 36, max **102 min**) liczony, **nieużywany jako bramka**.
- **Kierunek (cosine) NIE koreluje z R6 breach** (wszystkie kubełki ~11%) — dominujący driver to **rozrzut DOSTAW + czas niesienia**, nie rozrzut odbiorów.

### 6.2. Nowy system oceny bundle (propozycja — BUNDLE-06)
Zamiast „prawie wyłącznie odległość" → jeden `bundle_fit_score` scalający SYGNAŁY JUŻ LICZONE (koszt = scalenie, nie nowy compute):

```
bundle_value =  w1 · consolidation_km_saved        (= solo_baseline(new) − marginal_insertion_delta z route_simulator)
             +  w2 · consolidation_min_saved
             −  w3 · max(0, added_thermal_age_min − BAG_TIME_SOFT_MIN)   ← termika dostawy (DOMINUJĄCY driver)
             −  w4 · added_drop_detour_km
             −  w5 · pickup_span_min                                      ← R8 jako wejście, nie osobna kara
       × dir_multiplier(avg_cosine)                                       ← kierunek jako mnożnik, nie addytyw
```
+ **Forward-looking (Faza 3):** przy propozycji SOLO dla zlecenia z restauracji R → sprawdź `pending_pool` + okno ostatnich N min na drugie zlecenie z R; jeśli pickup w ±8 min → podbij kandydata do bundla (realizuje R-FLEET-LEVEL i odzyskuje przegapione zagęszczenia — BUNDLE-07).
+ **Twarda bramka:** `objm_r6_breach_count>0` dla wybranego worka I istnieje feasible alternatywa (solo/mniejszy worek) bez breachu → re-propozycja z odroczonym odbiorem (NIE KOORD-cisza).

---

## ETAP 7 — OCENA WG STANDARDÓW ENTERPRISE (Uber/Wolt/Glovo/DoorDash)

**Ziomek to PROPOSER, nie DISPATCHER.** Czego brakuje do poziomu samodzielnego dispatchingu:
| Warstwa enterprise | Stan u Ziomka | Finding |
|---|---|---|
| **Faktyczne auto-przypisanie** | NIE ISTNIEJE (AUTO = tekst) | AUTON-01 |
| **Continuous re-optimization / dynamic batching** (DoorDash) | brak — greedy per-order | AUTON-08, BUNDLE look-ahead |
| **Nauczony model ETA** | OSRM + ręczne mnożniki korka | AUTON-09, DATA-02 |
| **Offer/accept loop** (kurier akceptuje) | brak — push-assign | AUTON-10 |
| **Predictive positioning / rebalancing / surge** | brak | AUTON-10 |
| **Reliability/acceptance score kuriera** | `ground_truth` martwy | CB-01, DATA-03 |
| **Confidence model zamiast heurystyk** | progi C1-C6 (margin=15 placeholder) | AUTON-04 |

**Decyzje zbyt heurystyczne (powinny być scoringiem/modelem):** próg auto-approve `score_margin=15.0` (nieskalibrowany placeholder, a **42,9% decyzji to close-calls margin<5** → wymaga modelu prawdopodobieństwa, nie progu); wagi scoringu (timing_gap 5/10/15, stopover=8, bonus_r4 ×1.5, wait −10..−700) to magic-numbery bez backtestu.

---

## ETAP 8 — NOWA ARCHITEKTURA DECYZYJNA

### 8.1. Quick Wins (1-3 dni) — największy efekt/nakład
1. **FAIL-02:** aplikuj `_bag_not_stale` do pozycji w `courier_resolver.py:597` → porzucony kurier znika z puli. *(quick, P0)*
2. **GPS-01:** licznik fresh-GPS w `build_fleet_snapshot` + alert „<30% floty ma GPS przez 2 cykle". *(quick, P0)*
3. **STATE-RMW prune:** oneshot timer usuwający zlecenia terminalne (status 7/8/9 >6-12h) → 3505→kilkaset, zapis 16-50× szybszy. *(quick, P0 skala)*
4. **RECON-01:** flip `RECONCILIATION_TELEGRAM_ALERT_ENABLED=true` (zero kodu). *(quick)*
5. **DIVERG-01 + FLOOR-01:** commit_divergence (83% niepotrzebnych) i difficult_case score-floor → PROPOSE+marker zamiast KOORD. *(quick)*
6. **CONFIG hygiene:** usuń 14 martwych flag + `shadow_mode` + skonsoliduj dublet `OBJ_R6_SOFT_DEADLINE`; skrypt `effective_flags` (flags.json ⊕ env). *(quick)*
7. **JSONL logrotate:** dopisz consumer_stuck + 5 innych jsonl. *(quick)*
8. **FAIL-05:** walidacja świeżego GPS vs bbox przed `cs.pos`. *(quick)*

### 8.2. Średnie wdrożenia (1-4 tygodnie)
9. **R6BREACH-01 / GATE-02:** post-selection R6 guard — nie proponuj zwycięzcy z R6>35 jeśli istnieje kandydat ≤35. **Zamyka 14% breach i 79 przypadków/10dni.** *(rdzeń jakości + autonomii)*
10. **ALWAYS-PROPOSE:** flip wszystkich KOORD-cisza (best_effort_r6, low_score) → PROPOSE+marker. *(zamyka FAIL-03, 7,3% zleceń)*
11. **ETA fix (DATA-01 + GEO-03):** kalibracja drive_min liczona z `pickup→delivery` (czysty drive), behawioralny lag osobno; wymiar dystansu; wpięcie do live ETA za flagą (shadow→flip).
12. **Bundling (BUNDLE-02/06):** `bundle_fit_score` scalający istniejące sygnały + twarda bramka R6 dla worków + flip V327/V326 po replay.
13. **Selekcja (SEL-01 + FEAS-02):** kierunek do KLUCZA selekcji; no_gps zawsze bucket-2 (informed-z-bagiem bije fikcję BIALYSTOK_CENTER).
14. **CB-01:** `courier_recent_delay()` z `sla_log`+backfill → reliability soft-score.
15. **Latency pack (PANEL-SCRAPE-01 + OSRM-TABLE-03 + THREADPOOL-04):** zrównoleglić fetch detali (opener per-wątek), cache `table()`, pre-filtr puli do top-K, bg-login.
16. **GPS gradacja + skok-detekcja (GPS-02/03), PARSE-01 straż, PARSER_DEGRADED auto-set.**

### 8.3. Duże zmiany (1-3 miesiące)
17. **Pętla AUTO-ASSIGN (AUTON-01):** gate `verdict=PROPOSE ∧ auto_route=AUTO ∧ score≥próg ∧ gap≥próg ∧ brak ryzyka R6/late` → realne przypisanie panel API + **knock-back window 60s** + kill-switch. Najpierw canary (gold/std+, off-peak) z auto-rollback gdy R6 breach w AUTO-subset > baseline.
18. **orders_state → SQLite** (UPDATE per-row; `events.db` już daje odtwarzalność).
19. **Batch/window assignment:** okno agregacji 60-120s + periodic global re-solve floty (OR-Tools VRP) zamiast greedy per-order — realizuje look-ahead i R-FLEET-LEVEL.
20. **Schedule fail-soft (FAIL-12), orphan_bag_watchdog (FAIL-01).**

### 8.4. Game Changers (najbardziej podnoszą jakość)
- **Nauczony model ETA** (AUTON-09) na `drive_min_enriched` — fundament: poprawia feasibility, bundling, R6, AUTO-rate naraz.
- **Confidence model** (LGBM re-ranker, DATA-05) zastępujący progi C1-C6 — rdzeń bezpiecznej autonomii.
- **Continuous re-optimization** (batch matching min-cost) — odzyskuje bundling value, który rośnie nieliniowo z ruchem.

---

## ETAP 9 — RANKING TOP-20 (wg realnej wartości biznesowej)

Skala wpływu: ●●● duży / ●● średni / ● mały. ROI = wpływ ÷ trudność.

| # | Usprawnienie | Jakość | Bundle | Czas dost. | Skala | Autonomia | Trudność | ROI |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| 1 | **R6BREACH-01/GATE-02** — R6 guard na zwycięzcy selekcji | ●●● | ●● | ●●● | ●● | ●●● | śr | **★★★★★** |
| 2 | **FAIL-02** — porzucony kurier znika z puli | ●●● | ● | ●●● | ●● | ●●● | **niska** | **★★★★★** |
| 3 | **STATE-RMW prune** — orders_state → kilkaset | ● | ● | ● | ●●● | ●● | **niska** | **★★★★★** |
| 4 | **ALWAYS-PROPOSE** — KOORD-cisza → PROPOSE+marker | ●● | ● | ●● | ●● | ●●● | śr | **★★★★★** |
| 5 | **GPS-01** — alarm „feed GPS zamarł" | ●●● | ●● | ●● | ●●● | ●●● | **niska** | **★★★★★** |
| 6 | **ETA fix (DATA-01/GEO-03)** — kalibracja drive_min poprawna | ●●● | ●● | ●●● | ●● | ●●● | śr | **★★★★★** |
| 7 | **Bundling bundle_fit + R6 hard gate (BUNDLE-02/06)** | ●●● | ●●● | ●●● | ●● | ●● | duża | ★★★★ |
| 8 | **Pętla AUTO-ASSIGN (AUTON-01)** — realne auto | ● | ● | ● | ●●● | ●●● | duża | ★★★★ |
| 9 | **AUTON-04** — kalibracja progu C2 + predykat „safe-auto" | ● | ● | ● | ●● | ●●● | śr | ★★★★ |
| 10 | **Latency pack (scrape∥ + table cache + pre-filtr)** | ● | ● | ●● | ●●● | ●● | śr | ★★★★ |
| 11 | **SEL-01/FEAS-02** — kierunek+no_gps w kluczu selekcji | ●● | ●● | ●● | ● | ●● | śr | ★★★★ |
| 12 | **CB-01** — reliability/circuit-breaker kuriera | ●● | ● | ●● | ●● | ●●● | śr | ★★★★ |
| 13 | **DIVERG-01+FLOOR-01** — bramki KOORD→PROPOSE+marker | ● | ● | ● | ● | ●●● | **niska** | ★★★★ |
| 14 | **CONFIG hygiene** (dual-source, dead flags, dublet) | ● | – | – | ●● | ●● | **niska** | ★★★★ |
| 15 | **RECON-01** — flip alertu reconciliation | ● | – | ● | ●● | ● | **niska** | ★★★★ |
| 16 | **GEO-01/02** — OSRM w scoringu + model barier | ●● | ●● | ●● | ● | ● | śr | ★★★ |
| 17 | **Batch/window assignment (AUTON-08)** | ●● | ●●● | ●● | ●●● | ●● | duża | ★★★ |
| 18 | **Model ETA / LGBM re-ranker (AUTON-09/DATA-05)** | ●●● | ●● | ●●● | ●● | ●●● | duża | ★★★ |
| 19 | **orders_state → SQLite** | – | – | ● | ●●● | ●● | duża | ★★★ |
| 20 | **orphan_bag_watchdog + schedule fail-soft (FAIL-01/12)** | ●● | – | ●● | ●● | ●● | śr | ★★★ |

---

## ETAP 10 — RAPORT KOŃCOWY

### 10.1. Największe ryzyka
1. **Autonomia jest fikcją w kodzie** — cel czerwiec'26 nieosiągalny bez zbudowania ścieżki auto-assign (AUTO_APPROVE_* = martwe stałe).
2. **14% dostaw łamie twardą regułę 35 min** — dziś maskowane przez człowieka; przy autonomii = systematycznie zimne jedzenie bez nadzoru.
3. **Porzucenie kuriera = cichy dramat** (FAIL-01/02) — brak detekcji offline, ghost dostaje nowe zlecenia.
4. **Cisza zamiast propozycji** (FAIL-03) — 7,3% zleceń, sprzeczne z „ZAWSZE PROPONUJ".
5. **Cichy blackout danych** (PARSE-01, GPS-01) — częściowy parse/feed down bez sygnału.

### 10.2. Największe błędy obecnego Ziomka
- Twarda reguła R6 egzekwowana jako **SOFT dla worków** (63% decyzji) → 126 łamiących worków proponowanych.
- Klucz selekcji **ignoruje kierunek**; late-pickup tier-2 i **no_gps-empty z fikcyjnej pozycji** biją lepiej-skierowanego kuriera.
- **Circuit breaker = martwy kod** — nigdy nie karze świeżo spóźnionego kuriera.
- Kalibracja ETA **myli opóźnienie behawioralne z błędem routingu** i dopisuje płaski +30 min wszędzie.
- Bundle oceniany **prawie wyłącznie odległością**, model w 80% nieaktywny.

### 10.3. Najniebezpieczniejsze przypadki brzegowe
FAIL-01 (porzucony worek) · FAIL-02 (ghost jako wolny) · FAIL-12 (awaria grafiku = fail-CLOSED całej floty) · PARSE-01 (pusty parse = blackout) · FAIL-09 (kaskada peak+panel down).

### 10.4. Co wdrożyć NATYCHMIAST (ten tydzień)
TOP-6 quick-winów P0/P1 niskim nakładem: **FAIL-02, GPS-01, STATE-RMW prune, ALWAYS-PROPOSE, R6 guard (R6BREACH-01), DIVERG-01+FLOOR-01.** + flip `RECONCILIATION_TELEGRAM_ALERT`. + **NIE flipuj `DRIVE_MIN_CALIBRATION_V2` na ślepo 03.06** (najpierw napraw 2×-log i offset).

### 10.5. Co wdrożyć później
Bundling bundle_fit (#7), pętla AUTO-ASSIGN (#8), latency pack (#10), model ETA (#18), batch assignment (#17), SQLite (#19).

### 10.6. Jak doprowadzić Ziomka do poziomu autonomicznego dispatchera
**Faza 0 — Zamknij luki, które dziś łapie człowiek** (warunek konieczny): R6 guard, FAIL-01/02, ETA fix, ALWAYS-PROPOSE, CB-01. Bez tego autonomia = automatyzacja błędów.
**Faza 1 — Zmierz i podnieś zaufanie:** KPI `AUTO-rate` (cel 4%→20%→50%) + `final∈top3 Ziomka`; korpus 333 niezgodności jako dane uczące; kalibracja progu C2.
**Faza 2 — Zbuduj egzekucję:** ścieżka auto-assign za flagą + knock-back 60s + kill-switch; canary (gold/std+, off-peak) z auto-rollback na R6 breach.
**Faza 3 — Zastąp heurystyki modelami:** nauczony ETA, LGBM confidence re-ranker, batch/window re-optimization.
**Faza 4 — Enterprise:** offer/accept loop, predictive positioning, supply-demand balancing.

---

## ODPOWIEDZI NA 9 PYTAŃ KOŃCOWYCH

1. **Co może spowodować błędne decyzje?** Niedoszacowanie ETA (+11 min median → 14% R6 breach), pozycja kuriera z fikcji (73% proxy, no_gps-empty=BIALYSTOK_CENTER), klucz selekcji ślepy na kierunek, R6 soft dla worków, martwy circuit-breaker, geometria haversine w scoringu, ghost porzuconego kuriera.
2. **Gdzie reguły są niewystarczające?** R6/R8 dla worków tylko SOFT; brak gradientu obciążenia dla bag≥5; brak guardu prep restauracji; brak modelu barier geo; klucz selekcji bez kierunku; brak reliability kuriera; hardcode (EARLY_BIRD=60, MIN_PROPOSE=-100) nieskalowalny.
3. **Czego nie przewidziano?** Porzucenie kuriera/offline; awaria grafiku (fail-CLOSED); pusty parse (HTTP 200); zamarcie feedu GPS; skok GPS; kaskada peak+panel-down; zombie-zamówienia (24h carry).
4. **Błędy przy dużym obciążeniu?** Panel scrape pęka ~2x, OSRM/ThreadPool/state ~5x; latencja p95 2s→4-8s; KOORD-cisza i late-pickup-gate eskalują frakcję bez propozycji; jsonl bez bounda; reconciliation throttling.
5. **Decyzje poprawne ale nieoptymalne?** Greedy per-order (brak look-ahead/batch); 80% worków bez sygnału wartości; koncentracja na 3 kurierach; AUTO-rate 4% (większość rutynowych mogłaby iść auto); early_bird→KOORD zamiast schedulera.
6. **Utrata jakości przy wzroście ZAMÓWIEŃ?** Więcej worków → więcej R6 breach (soft gate); gęstsze spóźnienia → late-pickup-gate masowo odrzuca → KOORD-spike; przeciążenie top-kurierów; latencja > okno +5min.
7. **Utrata jakości przy wzroście KURIERÓW?** Latencja liniowa z flotą (ThreadPool=10, OSRM bez cache); capy absolutne (V325, bag=8, 15km) nie skalują; więcej no_gps fikcji; tiery z 20.04 nieaktualne; 10× urządzeń = 10× szum GPS bez filtra.
8. **Co wymaga człowieka, a powinno być auto?** 95,8% decyzji (kolejka Telegram); KOORD early_bird (46%, to scheduling nie decyzja); commit_divergence (83% zbędnych); komendy „pracuje/koniec/poprawa"; ręczne tiery/mapowanie restauracji.
9. **Jak osiągnąć maksymalną autonomię?** Patrz 10.6: najpierw zamknij luki jakościowe (Faza 0), zbuduj egzekucję auto-assign z bezpiecznikami (Faza 2), zastąp heurystyki modelami (Faza 3). Autonomia bez Fazy 0 = automatyzacja dzisiejszych 14% błędów.

---
*Pełny korpus 118 findingów z dowodami: `/root/_ziomek_audit_extract.md`. Surowy wynik workflow: `/tmp/claude-0/.../wu2csiq4g.output`.*
