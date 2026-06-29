# BLACK SWAN — Ziomek Dispatch Investigation

**Data:** 2026-06-14
**Tryb:** First-principles / black-swan (zakładamy scoring/ETA/batching/routing = zoptymalizowane; szukamy tego, czego jeszcze nie szukaliśmy)
**Metoda:** 5 równoległych głębokich rekonstrukcji żywego kodu + żywych flag + audytu Bartek 2.0 + symulatorów + korpusu 56 250 dostaw. Wszystkie liczby poniżej są zweryfikowane przeciwko kodowi/danym, nie z pamięci.
**Pytanie założycielskie:** jeśli +10-20% jest możliwe, a NIE leży w scoringu/ETA/batchowaniu/routingu — to gdzie leży?

---

## TEZA W JEDNYM ZDANIU

**Ziomek rozwiązuje problem *dopasowania zamówienia do kuriera w migawce teraźniejszości*. Prawdziwy problem to *przepływ wyczerpywalnego, ruchomego, niejednorodnego pola podaży (kurierzy w przestrzeni i czasie) przeciwko stochastycznemu polu popytu, gdzie każda decyzja jest nieodwracalną konsumpcją przyszłej elastyczności*. Brakujące 10-20% nie leży w jakości migawkowego dopasowania — leży w trzech wymiarach, których obecna architektura w ogóle nie reprezentuje: CZAS decyzji, PRZESTRZEŃ sieci, oraz WARTOŚĆ, którą optymalizujemy.**

---

## CO DISPATCHER NAPRAWDĘ ROBI DZIŚ (diagnoza architektoniczna)

Niezależnie od jakości scoring/ETA/batching/routing — silnik jest, na poziomie architektury, **zachłannym matcherem migawkowym jednego strzału**:

1. **Decyduje natychmiast.** Nowe zamówienie (poll 20 s) → `assess_order` na świeżej migawce floty → jedna `PROPOZYCJA` do człowieka. Świadome odroczenie istnieje **tylko** dla czasówek (≥60 min). Elastyki wiązane od razu.
2. **Decyduje na fikcyjnej teraźniejszości.** Pula kandydatów = kurierzy „dostępni wg GPS teraz". Bartek wybiera bezczynnego o medianie **51 min**; Ziomek proponuje bezczynnego o medianie **118 min** — pozycje sprzed godziny traktowane jako „teraz". **54% wyborów człowieka nie ma nawet w puli kandydatów Ziomka.**
3. **Decyduje raz, nieodwracalnie (po stronie silnika).** Po przypisaniu silnik **nigdy** nie przenosi zamówienia do innego kuriera — `plan_recheck`/redecide tylko przesekwencjonowują worek jednego cid. Człowiek koryguje **10%** przypisań na innego kuriera. Silnik koryguje **0%**.
4. **Jest ślepy na sieć.** Brak pojęcia „ile kurierów pokrywa rejon X". Brak anti-drain. Brak repozycjonowania. **Prognoza FC-21 istnieje, ale pętla jest otwarta** — zasila planowanie grafiku przez człowieka, nie zasila ani jednej decyzji przydziału (`pending_queue_provider.compute_demand_context()` ma `n_orders_last_15min` zahardkodowane na `0` z `# TODO`).
5. **Optymalizuje geometrię i naśladownictwo, nie wynik.** Funkcja celu = ręcznie ważona suma (dystans 0.30 / obciążenie 0.25 / kierunek 0.25 / czas 0.20) + ~25 bonusów, zdominowana przez R4 bundle (+150, gdy dystans maksuje na ~30). LGBM uczy się „którego kuriera wybrał człowiek", **nie** czy dostawa dojechała na czas. Żywa funkcja celu nie uczy się z niczego.

To nie są bugi. To jest **klasa problemu, którą zakodowano**: migawkowe dopasowanie. A świat jest przepływem.

---

## GDZIE SĄ TE 10-20% — TRZY OSIE, NA KTÓRYCH MIGAWKA ZAWODZI

### Oś 1 — CZAS: decydujemy za wcześnie, na zamrożonej i niepełnej teraźniejszości
Najlepiej udowodniona i najbardziej „już-zbudowana-ale-uśpiona" dźwignia.
- **Rolling late-binding (własna symulacja, 17.05): −51% best-effort (118→57)** przy wiązaniu na `odbiór − 15 min` zamiast przy utworzeniu. Infra istnieje (`pending_pool.py`, `FREEZE_LEAD_MIN=15`) — ale to **Faza 0, czysta obserwacja, ZERO wpływu na dispatch**.
- **„Zaraz wolny" (B2): 61% busy-picks Bartka to kurier kończący ≤12 min (med 7,4 min).** Flaga `ENABLE_SOON_FREE_CANDIDATE` = **OFF**.
- **Brak pętli korekcyjnej.** 12 849 zdarzeń `CZAS_KURIERA_UPDATED` = ciągłe re-timingowanie człowieka; silnik nie potrafi cofnąć złego przypisania, gdy 90 s później pojawia się lepszy układ.

**Reframe:** decyzja „KTO" jest już prawie optymalna. Brakuje decyzji „KIEDY". Moment wiązania to zmienna sterująca, której dziś nie ma.

### Oś 2 — PRZESTRZEŃ: dispatcher nie ma mapy własnej floty
Najmniej zbudowany i najbardziej różnicujący wymiar (tu Bartek 2.0 też patrzył słabo).
- **Drain jest niewidzialny w KPI.** Wyślij ostatniego kuriera z południa na marginalnie-lepszy kurs przez miasto → następne zamówienie z południa (statystycznie pewne za ~8 min w tej godzinie) to breach — ale przypisany do „brak kuriera", nie do decyzji drenującej sprzed 20 min. **Łańcuch przyczynowy przerwany w atrybucji.**
- **Repozycjonowanie = ukryta połowa wszystkich km** (mediana 3,56 km drop→następny odbiór).
- **Prognoza gotowa i zwalidowana, tylko niepodłączona.** `forecast21.py`/`fleet_forecast` liczą popyt per slot → lądują w UI panelu + `recommended_couriers` dla planisty. Zero przepływu do `scoring.py`.

**Reframe:** optymalizujemy alokację na polu popytu, którego nie widzimy, zużywając pole podaży, którego nie modelujemy.

### Oś 3 — WARTOŚĆ: optymalizujemy zgodność-z-koordynatorem i geometrię, nie wynik
- **Score anty-przewiduje wynik (własny audyt).** Breach płaski ~7-9% dla score −100…+90, **a przy score >90 skacze do 13-18%** (>120 → 18%). Klasa AUTO („bezpieczna autonomia") = **najgorszy** breach 10,1%. System najbardziej ufa propozycjom kończącym się najgorzej — bo dominujący R4 (+150) nagradza pakowanie worka niezależnie od ryzyka.
- **Funkcja celu PLN przerzuca ~50% decyzji, mediana +1,82 PLN/decyzję.** `pln_objective.py` skalibrowany (marża 6,33; km 0,90; breach 14 PLN; **jedzenie stojące >15 min czurnuje restauracje 8,2% vs 3,1% — silniejszy predyktor niż sam breach**) — ale `ENABLE_PLN_OBJECTIVE_SHADOW`, czyli telemetria, nigdy decyzja.
- **Zero zamkniętej pętli wyniku.** Macie 56k dostaw z realnym `delivered_at`; model wyniku `P(breach|cechy)` już AUC 0,64; żywa funkcja celu nie widzi żadnego z tych danych.

**Reframe:** to nie „dostrójcie scorer". Mechanizm scoringu jest dostrojony — celuje w zły target i nie ma sprzężenia, które by mu powiedziało, że trafia w zły.

---

## FUTURE COST ANALYSIS — dobra decyzja lokalnie = katastrofa globalnie

Ślad kosztu w czasie na realnym **CASE #10 (Goodboy)** z `case_corpus.md`:

| t | Zdarzenie | Koszt lokalny (co widzi scorer) | Koszt globalny (co dzieje się naprawdę) |
|---|---|---|---|
| t=0 | Solver dokłada Goodboy, **wygrywa o 2,3 min** total-drive | ✅ „najlepszy" wg dystansu/czasu | Σ bag-time **65 min** vs 36 (trasa Adriana), max worek 23 min, anti-FIFO (najmłodsze zlecenie czeka najdłużej) |
| +10 min | Kurier wpięty w detour 3,28 km na południe | — (niewidoczne) | Rejon, z którego go zabrano, traci pokrycie; jedzenie z 2 restauracji stygnie |
| +20 min | Worek dojeżdża do ściany R6 | — | Pierwszy breach „pojawia się" — przypisany do tej dostawy, nie do decyzji z t=0 |
| +30 min | Nowe zamówienie w zdrenowanym rejonie | — | „Brak kuriera" → breach #2. **Atrybucja: brak podaży. Prawda: drain z t=0** |
| +60 min | Przy obciążeniu >2,5/h efekt się propaguje | — | Prawo obciążenia: ≤2,5/h <8% breach; **>4/h → 22%+**. Lokalne 2,3 min uruchomiło kaskadę |

**Zachłanny argmax jest z definicji lokalnym optimum.** Potwierdza to kaskada:
> human **7,9%** breach | argmax **11,3%** (max worek **33!**) | +anti-overload 10,9% | load-aware 9,9% | **load-aware + PEŁNY ROSTER 8,0%** (max worek ≤3)

Człowiek bije argmax o ~30% względnie — nie lepszym scoringiem, tylko **myśleniem o całej godzinie floty zamiast o jednym zamówieniu**.

---

## NIEWIDZIALNE 20% (czego nie widać w KPI)
1. **Fikcyjna teraźniejszość** — decyzje na pozycjach GPS sprzed godziny (idle 118 vs realne 51 min). Mnożone przez każdą decyzję.
2. **Misatrybucja breachy drenażowych** — system uczy się złej lekcji, bo wini ostatnie przypisanie, nie decyzję drenującą 2-3 kroki wcześniej.
3. **Score anty-predyktywny na górze** — pewność systemu odwrotnie skorelowana z sukcesem.
4. **Sync-worka (rozrzut gotowości >10 min = 50% wszystkich breachy)** — objaw głębszej choroby: wiązanie zanim znana jest realna gotowość.

---

# FINAL REPORT — 3 RZECZY NA 1 MIESIĄC, KTÓRE ZMIENIAJĄ SPOSÓB MYŚLENIA DISPATCHERA

Wybrane jako **ortogonalne** (czas / przestrzeń / wartość); każda zmienia *klasę problemu*, nie dostraja parametr. Kolejność = priorytet wdrożenia. **~60% z tego już zbudowano i trzyma się za flagą** — miesiąc to dokończenie i podłączenie uśpionego + jeden greenfield.

## ① CZAS — Przestań decydować na zamrożonej, niepełnej teraźniejszości
### *Ciągła re-optymalizacja nad pełnym rosterem z wiązaniem w ostatnim odpowiedzialnym momencie*

**Zmiana myślenia:** z „przypisz przy utworzeniu, na GPS-puli, raz" → na „trzymaj w puli, re-optymalizuj przy każdym zdarzeniu, rozważaj WSZYSTKICH (w tym zaraz-wolnych), zamroź dopiero na `odbiór − 15 min`".

**Dowód:**
- *Symulacja:* `sequential_replay --rolling` → **−51% best-effort (118→57)** na 17.05.
- *Dane:* **54% wyborów człowieka poza pulą**; **61% busy-picks = zaraz-wolny ≤12 min**; człowiek koryguje **10%** przypisań, silnik 0%.
- *Sieć:* późne wiązanie pozwala współ-przybyłym zamówieniom batchować się naturalnie i likwiduje „fikcyjną teraźniejszość" (118→51 min idle), redukując drain u źródła.

**Zakres na miesiąc (infra ~60% gotowa):** Tydz.1-2 promocja `pending_pool` z Fazy 0 do Fazy 2 (freeze-at-pickup−15 + re-optymalizacja przy każdym `NEW_ORDER`). Tydz.2-3 włączenie `soon_free` jako pełnego kandydata (`SOON_FREE_MAX_MIN=12`). Tydz.3-4 pętla korekcyjna silnika (re-bind w puli do freeze) + shadow→canary→flip na replay.

**Oczekiwany zysk:** rzędu **10-20% redukcji breachy poniżej ściany przepustowości** (cząstkowo dowiedzione: best-effort −51%, argmax→load-aware+full-roster −29% rel.) + duża redukcja best-effort i jedzenia-stygnącego. *Uczciwie:* w dniach saturacji dominuje przepustowość, nie wiązanie — tu zysk mały.

**Ryzyko:** churn (zmiana kuriera przed odbiorem) myli kurierów → freeze-15 + limit re-bindów; replay już mierzy churn per zlecenie.

## ② PRZESTRZEŃ — Daj dispatcherowi mapę własnej floty
### *Stan podaży sieci + anti-drain + zamknięcie pętli prognozy*

**Zmiana myślenia:** z „dopasuj zamówienie do kuriera" → na „utrzymuj pokrycie pola miasta; każde przypisanie to nie tylko zysk na tym zleceniu, ale koszt pokrycia w rejonie, który opuszczasz". Miasto jako płyn, nie lista adresów.

**Dowód:**
- *Dane:* repozycjonowanie = **ukryta połowa km** (mediana 3,56 km dead-head); breache drenażowe schowane pod „brak kuriera".
- *Symulacja:* P4 w symulatorze B2.0 (kara repozycjonowania + per-slot cap) **bije człowieka 8/8** (crash 16.05: 26,8% → **14,9%**); prawo obciążenia (≤2,5/h <8% vs >4/h 22%) dowodzi: pokrycie rejonu > marginalny zysk na kursie.
- *Sieć:* z definicji — to JEST analiza całej sieci, której dziś nie ma.

**Zakres na miesiąc (jedyna pozycja greenfield → scoping warstwowy):** Tydz.1 siatka pokrycia (H3/heksy lub osiedla z `BIALYSTOK_DISTRICT_ADJACENCY`) + licznik idle-per-heks z żywych pozycji. Tydz.2 **kara anti-drain** (tanie, duży zwrot) — miękki penalty za zabranie ostatniego/przedostatniego kuriera z heksa o niezerowym prognozowanym popycie ≤20 min (zamyka pętlę FC-21 minimalnym kosztem). Tydz.3-4 **cień** repozycjonowania proaktywnego (sugeruj idle-kurierowi dryf ku prognozowanemu popytowi — bezczynny kurier kosztuje firmę ~0, płacony od dostawy, więc *interesy zbieżne*: dryf ku popytowi = więcej jego dostaw). Flip po walidacji.

**Oczekiwany zysk:** najwyższy sufit, najsłabiej dziś skwantyfikowany. **Pierwszy deliverable = pomiar (kontrfaktyk drenażowy, niżej)** — zmierzy ile breachy „brak kuriera" to samo-zadany drain.

**Ryzyko:** największy build; proaktywne repozycjonowanie wymaga akceptacji operacyjnej kurierów → najpierw cień + anti-drain, dopiero potem dryf.

## ③ WARTOŚĆ — Zamknij pętlę wyniku
### *Optymalizuj realny breach i PLN, nie zgodność-z-koordynatorem i geometrię*

**Zmiana myślenia:** z „wybierz tego, którego wybrałby koordynator / najlepszego geometrycznie" → na „minimalizuj oczekiwany REALNY breach + maksymalizuj PLN, ucząc się z tego, co naprawdę się wydarzyło". Dispatcher dostaje pamięć skutków własnych decyzji.

**Dowód:**
- *Dane:* score anty-przewiduje (7-9% → **13-18% przy score >90**); 56k dostaw z realnym `delivered_at`.
- *Symulacja:* `objective_funkcja.py` → PLN **przerzuca ~50% decyzji, +1,82 PLN/decyzję**; model wyniku już **AUC 0,64**; kaskada → osiągalny cel = **parytet z człowiekiem 7,9%**.
- *Sieć:* funkcja PLN wycenia food-sit (churn 8,2 vs 3,1%) i opportunity-cost — wartości sieciowe, nie per-zlecenie.

**Zakres na miesiąc (głównie promocja uśpionego):** Tydz.1 rekonstrukcja stub-a `outcome_model` (~150 linii, recepta w docstringach + `agent_econ`) na żywym feedzie 56k. Tydz.2-3 `pln_objective` z cienia do rankingu kandydatów (najpierw tie-breaker, potem waga). Tydz.3-4 nocna re-kalibracja wag z realnych wyników (pętla jak żywy `dispatch-retro-learning` 04:30 dla reliability) — funkcja celu zaczyna się uczyć.

**Oczekiwany zysk:** bezpośredni ekonomiczny (**+1,82 PLN × wolumen**) + kompresja breachy na górnych score'ach. Cel realny = zejście z ~10% do okolic 7,9% (parytet człowieka), którego argmax nie osiąga strukturalnie.

**Ryzyko:** funkcja celu na realnym wyniku może przeuczać się na rzadkie ogony (crash-days) → kalibracja na medianach + gate'y jak w istniejących cieniach.

---

## DLACZEGO TE TRZY, A NIE KOLEJNE OPTYMALIZACJE
Każda z czterech „zoptymalizowanych" rzeczy operuje **wewnątrz jednej migawkowej decyzji**. Te trzy rekomendacje atakują **strukturę samego podejmowania decyzji**: *kiedy* (czas), *na tle czego* (przestrzeń), *po co* (wartość). To trzy współrzędne, w których „dopasowanie w migawce" przestaje być „sterowaniem przepływem".

---

## NASTĘPNY KROK POMIAROWY (do potwierdzenia twardo)
Jedna analiza, której dane w pełni unoszą, a której jeszcze nie ma jako kod (stub `cascade_harness.py`/`outcome_model_and_cascade.py` — 29/54 linie, recepta w docstringach): **kontrfaktyk „co 20-30 min wcześniej uratowałoby ten breach"**. Złączenie `shadow_decisions.jsonl` (pula kandydatów + plany + `predicted_delivered_at`, do 13 alternatyw) → `events.db`/Postgres `delivery` (realny wynik). Zmierzyłoby jednocześnie zysk per oś: ile breachy miało feasible alternatywę w puli (③), ile poprzedził samo-zadany drain (②), ile uratowałby zaraz-wolny / późne wiązanie (①). ~150 linii do odbudowy + przebieg na 56k.

---

# ZAŁĄCZNIK — zweryfikowane dowody (kod, flagi, dane, narzędzia)

## A. Decyzja i wiązanie (oś CZAS)
- Główna pętla: `panel_watcher.py:2361/:2422` (poll **20 s**, `config.json polling.panel_interval_seconds:20`).
- Decyzja: `shadow_dispatcher.py:999/:1027/:1457` → `dispatch_pipeline.py:2305 assess_order`; werdykt PROPOSE/KOORD/SKIP/MAYBE.
- Commit = klik człowieka: `telegram_approver.py:4-8/:1476/:149` (`PROPOSE → Telegram → callback ASSIGN → gastro_assign subprocess`). Autonomia istnieje, OFF: `ENABLE_AUTO_ASSIGN=false`.
- Odroczenie tylko czasówki: `dispatch_pipeline.py:2498-2507` (EARLY_BIRD ≥60 min → KOORD); `czasowka_scheduler.py:189` (WAIT/EMIT/FORCE_ASSIGN, triggery **60/50/40**); `common.py:1546-1549`.
- Late-binding uśpiony: `pending_pool.py:190-208 compute_freeze_at` (`FREEZE_LEAD_MIN=15`), `pending_pool_sweeper.py:1-11` („Faza 0 = czysta obserwacja, ZERO wpływu"); spec `eod_drafts/2026-05-18/sprint_plan_rolling_late_binding.md:7-9`.
- Re-decyzja = przesekwencjonowanie 1 worka: `plan_recheck.py:799 redecide_courier`, `:466 _gen_one_bag_plan`, `:828-830` (filtr na 1 cid). Brak prymitywu commit/lock/freeze/reserve.
- Soon-free: `common.py:1750-1756` (`SOON_FREE_MAX_MIN=12`, `ENABLE_SOON_FREE_CANDIDATE`), `dispatch_pipeline.py:1661-1700 _soon_free_probe` — **substytucja OFF**, telemetria ON.

## B. Funkcja celu (oś WARTOŚĆ)
- Rdzeń: `scoring.py:196 score_candidate` = 0.30·dystans + 0.25·obciążenie + 0.25·kierunek + 0.20·czas; agregacja `dispatch_pipeline.py:4020`; sort `:4706` (argmax).
- Dominacja R4: `dispatch_pipeline.py:3091-3098` (`bonus_r4` do **+150**; dystans maksuje ~30).
- Bramki twarde → KOORD: `MIN_PROPOSE_SCORE=-100` (`:5092`, oceniane BEZ delt rankingowych przez `_gate_score_excluding_ranking_deltas:1636` — lekcja #188 z incydentu syncworki).
- PLN cień (gotowy, nie decyduje): `pln_objective.py` (`ENABLE_PLN_OBJECTIVE_SHADOW`; marża 6,33 / km 0,90 / breach 14 / food-sit). LGBM cień: NDCG@5=0,852, pairwise 88,45%, `ENABLE_LGBM_SHADOW` (nie `PRIMARY`).
- Sygnał uczący: LGBM trenuje na zgodności z koordynatorem (399k par), NIE na realnym wyniku. Żywa funkcja celu = ręczne wagi „Bartek Gold Standard", trenowane na niczym.

## C. Sieć / przestrzeń (oś PRZESTRZEŃ)
- Order-myopic: `scoring.py:184-251` (s_kierunek = geometria per-kandydat, nie pokrycie).
- „Fleet-level" = per-courier load, **default OFF**: `dispatch_pipeline.py:885-933 _v326_fleet_load_balance` (`ENABLE_V326_FLEET_LOAD_BALANCE`).
- Strefy = geokod/billing, nie dispatch: `common.py:1120-1293 drop_zone_from_address`; `BIALYSTOK_DISTRICT_ADJACENCY common.py:1138` (tylko bundling).
- Sygnał popytu realtime = stub OFF: `pending_queue_provider.py:65-100` (`n_orders_last_15min=0 # TODO`).
- Prognoza display-only (panel): `forecast21.py` (`recommend_shift_starts:552`), `fleet.py:217/:376/:392` (`upsert_shift_plan` → `recommended_couriers`). Panel = lustro: `integrations/ziomek/adapter.py:1-11` („Panel jest LUSTREM floty"). dispatch_v2 grep `fleet_forecast|forecast21|/api/fleet` = **0 trafień**.
- Anti-drain / repozycjonowanie / staging = **0 trafień** w całym dispatch_v2.

## D. Model kuriera (DRIVER DNA)
- Tier: `build_v319h_courier_tiers.py` → `dispatch_state/courier_tiers.json`. **Owner-declared, nie liczone.** 5 bag-tier (gold/std+/std/slow/new) + 3 speed + 3 bundle. 53 kurierów.
- Per-tier (NIE per-individual) w decyzji: speed mult 0.85-1.25 (`common.py:1794-1808`, ±7,5/−12,5 pkt), DWELL 1,5-6,5 min (`:1820-1831`), bag cap 2-6 (`:1069-1085`, tier×pora).
- Per-individual ŻYWE: A2 reliability (`tools/courier_reliability.py:161`, **dzienny 04:30** z 3001 dostaw, `ENABLE_A2_RELIABILITY_SOFT_SCORE` ON), ramp nowych (`ENABLE_NEW_COURIER_RAMP` ON, 30 dostaw), cap_override (12 cid).
- **Per-individual ZBIERANE ale NIEUŻYWANE:** `delivery_time_p90`, `orders_per_wave_p90`, `bundle_rate` (martwe w decyzji — 2 „gold" dostają identyczne ETA).
- **BRAK:** geo-affinity (home turf), fatigue/intra-shift (stan stały w zmianie), batch-tolerance konsumowana, krzywa overload (`max_concurrent_observed` = same NULL), acceptance/cancel-rate.

## E. Symulatory i dane (do backtestów)
- Replay: `dispatch_v2/tools/sequential_replay.py` (`--rolling`, freeze−15, byte-deterministic; dowiódł late-binding −51% best-effort). Wejście: `events.db` + `learning_log.jsonl` + `courier_tiers.json` + OSRM:5001.
- Symulator B2.0: `/root/bartek2_workdir/agent_sim/{sim_engine.py,run_all.py,gate.json}` (6 polityk REPLAY/P1-P4; P4 bije człowieka 8/8; GATE PASS 5/5 dni normalnych, kompresuje ekstrema −5,8 pp; haversine×1.37, bez OSRM). `python3 run_all.py`.
- PLN: `/root/bartek2_workdir/agent_econ/{objective_funkcja.py,econ_model.py}` (flip ~50%, +1,82 PLN/decyzję).
- Kaskada (STUB do odbudowy ~150 linii): `eod_drafts/2026-06-07/{cascade_harness.py,outcome_model_and_cascade.py}` — recepta w docstringach; wynik: human 7,9% | argmax 11,3% (max worek 33) | load-aware+full-roster 8,0% (≤3); AUC 0,51→0,64.
- Dane: `/root/bartek2_workdir/corpus.pkl` (**56 250 dostaw**, 2025-11→2026-06, breach deliv>35 = 10,0%, med deliv 18 min, med pickup-lateness 13 min); Postgres `nadajesz_panel.delivery` (57 336, żywy); `events.db audit_log` (56 713; ASSIGNED 28 887 / DELIVERED 7 368 / CZAS_KURIERA_UPDATED 12 849); `logs/shadow_decisions.jsonl` (pula do 13 alternatyw + plany + predicted_delivered_at, BEZ realnego wyniku → join do events.db/Postgres).
- Raport źródłowy: `/root/BARTEK_2.0_RAPORT_2026-06-11.md`. Przypadki: `eod_drafts/2026-05-27/case_corpus.md` (Goodboy/Pierożek/Uwędzony, BUG A/B/C).

## F. Liczby-kotwice (najmocniejsze)
- Score anty-predyktywny: breach 7-9% (−100..+90) → **13-18% (>90)**, >120 → 18%; AUTO breach 10,1%.
- Sync-worka: rozrzut gotowości 6,4% (≤2 min) → 10,6 → 21,9 → **52,5% (>20 min)**; >10 min = ~50% wszystkich breachy (n=9 715).
- Bartek = flow manager: **54%** picków poza pulą; **61%** busy-picks soon-free ≤12 min (med 7,4); koryguje **10%**; idle med 51 vs Ziomek 118 min; **7%** jego override'ów breach do 69 min (nie naśladować ślepo).
- Prawo obciążenia: ≤2,5/h <8%; >4/h 22%+; crash 16.05 = 384/11 = 32,8%; P4 sim 26,8%→14,9%, +4 kurierów→0,5%.
- Late-binding: −51% best-effort (118→57, 17.05); SLA-breach 51→46 (ściana przepustowości).
- Repozycjonowanie: med 3,56 km drop→następny odbiór = ukryta połowa km.
- Death zone realny **14-17** (nie doktrynalny 11-14/17-20).

---
*Raport wygenerowany w trybie first-principles na żywym kodzie/danych 2026-06-14. Wszystkie kotwice file:line i liczby zweryfikowane przez 5 równoległych rekonstrukcji. Następny krok do twardego sizingu: kontrfaktyk drenażowy (sekcja „Następny krok pomiarowy").*
