# ZIOMEK — STRATEGICZNY AUDYT + CHECKPOINT STANU (2026-06-23)

> **STATUS / READ FIRST.** To jest **checkpoint stanu na 2026-06-23** — pełny strategiczny audyt Ziomka
> (ETAP 1–10) + żywy stan flag + decyzje Adriana z tej sesji + spec-i gotowe do budowy + stan wykonania.
> Cel: **nie powtarzać tego audytu od zera.** Następna sesja zaczyna OD TEGO PLIKU.
>
> **Metoda:** read-only, ZERO dotknięcia produkcji. Rdzeń (scoring/feasibility/verdict/ETA/tiery/flagi)
> zweryfikowany ręcznie w kodzie + 10 agentów na żywym `flags.json` (2026-06-22/23),
> `shadow_decisions.jsonl` (**3162 realnych decyzji, 11–22.06**), `learning_log`, `eta_calibration_log`,
> z **adwersaryjną weryfikacją** najryzykowniejszych wniosków (która obaliła kilka przeszacowań — patrz ETAP 6/7).
>
> **Powiązane:** `ZIOMEK_LOGIC_REFERENCE.md` (mapa logiki, ma już blok „LIVE-STATE CORRECTION 2026-06-23”).
> Ten plik = warstwa STRATEGICZNA (ocena, nie tylko opis).

---

## 0. NAJWAŻNIEJSZE W JEDNYM ZDANIU

Ziomek to **dobrze zaprojektowany, przejrzysty silnik regułowy**, którego **żywa konfiguracja po cichu
przesunęła osobowość ku pełnej autonomii**: twarda reguła jakości (R6 35 min) jest twarda tylko przy
odsiewie kandydatów, a **miękka przy werdykcie** — eskalacja do człowieka jest świadomie wyłączona.
Realny lewar jakości jest **operacyjny/algorytmiczny po stronie Ziomka** (inferencja pozycji bez GPS,
świeżość, pomiar realnych dostaw), **nie** „oddawaj człowiekowi”.

---

## 1. ⚠️ ŻYWY STAN FLAG ≠ DOKUMENTACJA (drift wykryty 2026-06-22/23)

`flags.json` **wygrywa** nad stałą modułu (`common.decision_flag`, `:232`). Od czasu wygenerowania
`ZIOMEK_LOGIC_REFERENCE.md` (2026-06-21) flagi się rozjechały. **Ufaj tej tabeli, nie tagom w reference.**

| Flaga | reference | **DZIŚ (live)** | Efekt na decyzje |
|---|---|---|---|
| `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` | 🟢 LIVE | **⚪ OFF** | rozjazd plan-vs-`czas_kuriera` >10 min (zimne jedzenie) **NIE** idzie do KOORD |
| `ENABLE_DIFFICULT_CASE_KOORD_REDIRECT` | ⚪ OFF | ⚪ OFF | score <−30 nie eskaluje (bez zmian) |
| `ENABLE_HARD_TIER_BAG_CAP` | ⚪ OFF | **🟢 LIVE** | **NOWA twarda bramka**: gold/std+ 6 / std 5 / slow,new 4 |
| `ENABLE_FLEET_LOAD_GOVERNOR` | 🟡 SHADOW | **🟢 LIVE** | −40 do score przy saturacji (12,4% propozycji) |
| `ENABLE_BUNDLE_SYNC_SPREAD` | 🟡 SHADOW | **🟢 LIVE** | 0…−150 do score (59,5% propozycji) — to przez to log score wygląda fatalnie |
| `ENABLE_R5_PICKUP_DETOUR_PENALTY` | ⚪ OFF | **🟢 LIVE** | −4,0/km nad 0,5 km (`R5_DETOUR_PENALTY_PER_KM=4.0` z flags.json) |
| `ENABLE_A2_RELIABILITY_SOFT_SCORE` | 🟡 SHADOW | **🟢 LIVE** | kara reliability coeff 60 (zależna od danych) |
| `ENABLE_OBJ_COMMITTED_PICKUP_PENALTY` | — | **🟢 LIVE** | OR-Tools soft coeff 100, nigdy INFEASIBLE |
| `ENABLE_NO_GPS_UNCERTAINTY_PENALTY` (B3) | 🟢 trial | **⚪ OFF** | trial wycofany |
| `ENABLE_NO_GPS_EQUAL_TREATMENT` | — | **🟢 LIVE** | no_gps konkuruje na surowym score; `_demote_blind_empty` ~martwy |
| `ENABLE_ALWAYS_PROPOSE_ON_SATURATION` | — | **🟢 LIVE** | **linchpin**: każda bramka jakość→KOORD ma `and not _always_propose_on()` |

**Inwariant bezpieczeństwa:** delty LOADGOV/SYNCWORKA są **wycinane z gate-score** dla bramki low-score
(`_gate_score_excluding_ranking_deltas`, `dp:1975`) — przestawiają ranking, ale **nie wyciszą** propozycji
do KOORD. `MIN_PROPOSE_SCORE=−100` liczony jest na surowym score. ML (LGBM/R3/quantile/prep-bias) = czysty shadow.

---

## 2. DECYZJE ADRIANA (2026-06-23) — co ustalone, NIE re-otwierać

**Pytania (ETAP 10):**
1. **ALWAYS-PROPOSE = intencjonalne NA STAŁE.** Ziomek ma być w 100% autonomiczny; **NIE** przywracamy eskalacji do człowieka.
2. **NO_GPS_EQUAL_TREATMENT zostaje.** „Jeśli kurier dostępny — trzeba z niego korzystać”; nadpisania były z INNYCH powodów, nie z braku GPS.
3. **Override 84% ≠ miara jakości.** Ziomek pracuje w tle/shadow; w ciągu dnia jego propozycje **nie są brane do działania** (ludzie dispatchują ręcznie); czasem bywa lepszy od człowieka. → tę liczbę wycofuję jako „obciążającą”.
4. **Build outcome-join** (spec przed kodem).
5. **Defer-and-hold: TAK.** Mało pracy → przypisuj **od razu**; peak → przypisuj **po ~2 min**.
6. Wyjaśnić prosto „flip 89>90” (zrobione w czacie + §poniżej).

**Rekomendacje:**
1. Build outcome-join — **spec najpierw** (§14 SPEC A).
2. ALWAYS-PROPOSE zostaje (i tak działa w shadow). **Nie ruszać eskalacji.**
3. **Ziomek ma się NAUCZYĆ działać BEZ GPS, jak człowiek** — NIE „wymuszać GPS”. (lewar = lepsza inferencja pozycji)
4. Wyjaśnić defer-and-hold (§poniżej).
5. **Anulowane** (equal-treatment zostaje, p. #2).
6. **Wydajność (~900 ms + alarm latencji) — GO.**
7. **Rozbicie god-function / dług skali — GO** (najpierw plan).
8. **Świeżość — sprawdzić wyniki dokładniej, pewność że warto** (research RUNNING).
9. **Update reference doc — ZROBIONE** (blok korekty w `ZIOMEK_LOGIC_REFERENCE.md`).
10. **Cleanup — aider scratch ZROBIONE (7 MB)**; `.bak` archiwum = czeka na ACK.

---

## 3. ETAP 1 — MAPA DECYZYJNA

`assess_order()` (`dispatch_pipeline.py:2843` → `_assess_order_impl`, **3434 linie**):
```
NEW_ORDER
 ├ 1 Setup + walidacja pickup coords ─(brak)─► SKIP/no_pickup_geocode      [0× w 12 dni]
 ├ 2 Early-bird (pickup ≥60 min w przyszłości) ─► KOORD/early_bird          [~gros KOORD-ów]
 ├ 3 Load governor (saturacja EWMA, telemetria + alert)
 ├ 4 Per-courier eval (ThreadPool ≤10): feasibility_v2(HARD) → route_simulator_v2(TSP/ETA) → scoring
 │     → Candidate{score, MAYBE/NO, plan, ~254 pól}
 ├ 5 Selekcja: sort(-score) → 5 warstw re-sortu → _demote_blind_empty → late-pickup tiering
 ├ 6 Verdict cascade (first-match): state_stale / geometry_blind / low_score /
 │     commit_divergence[OFF] / difficult_case[OFF] → else PROPOSE
 └ 7 Brak feasible → best-effort (proponuj najmniej-zły) → solo fallback → KOORD
```
Graf modułów czysty/acykliczny: hub `dispatch_pipeline` → `scoring`, `feasibility_v2`,
`route_simulator_v2`(→`tsp_solver`,`same_restaurant_grouper`), liść `common`. Konsument: `shadow_dispatcher`
(read-only log) + `telegram_approver`. Panel/apka renderują kanon `courier_plans.json`.

---

## 4. ETAP 2 — REGUŁY (zweryfikowane w kodzie)

**Baza score** (`scoring.py:22-25`, Σwag=1.0): `0.30·dystans[100·exp(−km/5)] + 0.25·obciążenie[100·(1−bag/5),0@≥5]
+ 0.25·kierunek[100·(1−kąt/180)] + 0.20·czas[kara tylko 30→35 min]`.

**Bonusy/kary** (montaż `dp:4562` `bonus_penalty_sum` → `dp:4605` `final_score`), największe:
`bonus_r4` free-stop **0…+150** · `l1/l2` +25/+20 · kary wait **do −1000** (>20 min = HARD REJECT) ·
`r9_stopover −8·bag` · `return_to_restaurant −100` · `coordinator_idle −100` ·
`state_panel_mismatch −50·min(n,4)` · `v326_speed` gold +7,5/slow −12,5 · **LOADGOV −40 / SYNCWORKA 0…−150 (LIVE)**.

**Twarde bramki (feasibility `NO`):** **R6 termiczny 35 min per-order = jedyna twarda reguła jakości** ·
SLA 35 (≈duplikat R6) · **tier bag-cap (LIVE: gold/std+6/std5/slow,new4)** · bag sanity 8 ·
pickup reach 15 km×1,37 · grafik pre/post-shift · R7 99 km (**martwy** — Białystok ~15 km).
**R1 (spread 8 km) i R8 (pickup span) = SOFT** (świadomie; twarde zabiłyby przepustowość peaku).

---

## 5. ETAP 3 — JAK ZIOMEK MYŚLI (ranking de-facto)

1. **Wykonalność/bezpieczeństwo** — twarde bramki, R6 35 min na czele.
2. **Wartość bundla** (r4 +150, L1 +25) — **ale odpala tylko 2,2% decyzji**.
3. **Proximity do pickupu** (dystans 30% + pusta-baza ~90-97) — **dominuje WIĘKSZOŚĆ decyzji** (brak bundla do konkurowania).
4. **Obciążenie** (25% + stopover + tier-cap + 2 kary overload).
5. **Kierunek** (25%).
6. **Świeżość w bagu** (czas 20% + kary wait) — ale **NIC nie minimalizuje sumy wieku termicznego**, tylko próg 35.
7. Tier/prędkość, reliability (LIVE), pewność pozycji (demote — dziś rozluźniony przez equal-treatment).

**Warstwa werdyktu (zmiana charakteru):** prawie zawsze PROPOSE, niemal nigdy KOORD jakościowy.
Gate-score PROPOSE mediana **+24** (log −99 = artefakt SYNCWORKA −150), ale **20,6% propozycji łamie R6 35 min**.

---

## 6. ETAP 4 — OCENA PRIORYTETÓW

| Reguła | Właściwy priorytet? | Werdykt |
|---|---|---|
| R6 35 min | TAK na odsiewie, **NIE na werdykcie** | niedoceniona na końcu (20% propozycji łamie bez nadzoru) |
| ALWAYS-PROPOSE | **decyzja Adriana = zostaje** | przeceniona „nigdy nie milcz” > „nie wysyłaj zimnego” — ale to świadoma autonomia |
| Dystans (30%) | *de facto* za wysoki | przeceniony (bundle konkuruje 2,2% czasu) |
| Pewność pozycji (GPS) | **Adrian: equal-treatment zostaje** | Ziomek ma się nauczyć BEZ GPS (rec#3) — nie ważyć pewności, tylko lepiej inferować |
| Wartość bundla (r4/L1) | TAK gdy odpala | mechanika świetna; L2 (+20) za słabe by sterować |
| Świeżość (całość) | niedoceniona | nic nie minimalizuje wieku; shadow ~247 min/dz do odzysku (research) |
| Tier speed+DWELL | ~ok | lekki podwójny ciężar (minor; „double-count jazdy” OBALONY — `DRIVE_SPEED_MULT`=1.0) |
| SLA 35 | redundantny z R6 | szum diagnostyczny, zero strat |

---

## 7. ETAP 5 — IDEALNA HIERARCHIA (niezależnie)

1. Twarde bezpieczeństwo/wykonalność. 2. **Ryzyko spóźnienia/zimnego — jako GRADIENT** (w modelu pełnej
autonomii: Ziomek SAM je minimalizuje, bo nikt nie łapie). 3. **Inferencja pozycji** (lepsza, nie więcej GPS).
4. Przepustowość/wartość bundla. 5. Proximity (efektywność, nie sterownik). 6. Balans/fairness. 7. Tier/reliability (tie-break). 8. Koszt (km).
**Inwersja vs dziś:** proximity jest nadreprezentowany „z urzędu”; świeżość i inferencja-bez-GPS — niedoważone.

---

## 8. ETAP 6 — KONFLIKTY (po weryfikacji adwersaryjnej)

| # | Konflikt | Werdykt | Impakt |
|---|---|---|---|
| 1 | ALWAYS-PROPOSE + 2 bramki OFF rozbrajają eskalację jakości | ✅ POTWIERDZONY | **najwyższy** — ale to decyzja Adriana (autonomia), nie bug |
| 3 | Warstwy demote/tiering nadpisują zwycięzcę po score | ✅ potwierdzony | zamierzone (R-LATE-PICKUP), kruche; `_demote_blind_empty` ~martwy (equal-treatment) |
| 2 | R6(35)≈SLA(35) duplikat; commit_divergence(10) OFF | ⬇️ downgrade | brak strat (konserwatywne), szum diagnostyczny + DRY drift |
| 4 | Kara wait (−1000) może zabić bundle, omija gate-score | ⬇️ downgrade | tylko 1,1% propozycji; zwykle słusznie (zimne) |
| 6 | OVERLOAD(−20)+LOADGOV(−40) stack; dok. błędnie SHADOW | ⬇️ downgrade | OVERLOAD prawie nigdy na zwycięzcy; realne = korekta dok. (LOADGOV/SYNC LIVE) |
| 5 | „double-count tiera jazda+DWELL” | ❌ OBALONY | `DRIVE_SPEED_MULT`=1.0 celowo; reziduał speed-score vs DWELL minor |

---

## 9. ETAP 7 — MARTWY KOD (zweryfikowany — większość „oczywistych” upadła)

- **A (bezpieczne):** ⏹️ aider scratch (7 MB) — **USUNIĘTE 2026-06-23**.
- **B (test-gated/ACK):** `scoring.py:206` literal `×1.3` (6 testów go używa) · `r6_soft_penalty_c3_legacy` kwarg ·
  `speed_tier_tracker.py` (orphan, ale test live) · `.bak` **561 plików/42 MB** → archiwum „ostatnie 3” (ACK).
- **C (NIE ruszać):** `wave_scoring.py` — agent twierdził „git-DEAD”, **weryfikacja OBALIŁA** (commit nieweryfikowalny,
  hard-rule CLAUDE.md „NEVER modify without ACK”, 42 testy) · R7 99 km (parkowany lewar) ·
  `kill_switch_to_v1` (realnie tylko **pauzuje** 30 s, `gastro_trigger.sh` nie istnieje — fix tylko komentarz) ·
  wszystkie OFF-but-wired + shadow (odwracalne dźwignie).
- **Lekcja:** prawie nic nie jest „bezpiecznie usuwalne” — testy trzymają inertny kod (sygnał długu code-quality).

---

## 10. ETAP 8 — SCENARIUSZE (realne rekordy)

| # | Scenariusz | Co wygrało | Werdykt |
|---|---|---|---|
| 1 | 1 zlecenie, pusta flota (482710) | najbliższy gold gps | ✅ |
| 2 | 2 zlecenia (481606) | **+150 free-stop** (drop 0,45 km od trasy) | ✅ |
| 3 | 3 zlecenia (482216) | +75 free-stop ×0.7, korytarz cos 0.976 | ✅ (cienki margines) |
| 4 | bundle L1 (482051 Rany Julek) | **+25 L1 +150 r4**, ta sama restauracja, score 226 | ✅ optymalnie |
| 5 | saturacja 2/2 (482711) | pusta-baza gold | ✅ (zastrzeżenie: pojemność) |
| 6 | dużo kurierów (≥6) | **best-by-score nie najbliższy** (71%); demote ducha no_gps (85%) | ✅ |
| 7 | spóźniona restauracja (497 rek.) | kary wait + committed; **PROPOSE-z-banerem nie KOORD** | ⚠️ wyceniony, nieeskalowany (= autonomia) |
| 8 | lunch 11-14 (293) | **47% R6-breach na bag≥2**, 19% best-effort pula=0 | ⚠️ ściana pojemności |
| 9 | dinner 17-20 (385) | 59% solo, KOORD 1,3%, zdrowszy | ✅ (ogon late-kitchen p90 33 min) |

Mechanika bundla działa wzorcowo; w głębokiej puli wybiera najlepszego nie najbliższego. **Sufit = pojemność floty, nie algorytm.**

---

## 11. ETAP 9 — GOTOWOŚĆ (1-10)

| Wymiar | Ocena | Skrót |
|---|---|---|
| Logika dispatchu | **8** | przejrzysty score + R6 + cascade, mierzona, shadow-walidowana |
| Bundling | **7** | r4/L1/L2/wave/carried-first; R1/R8 słusznie miękkie |
| ETA | **6** | OSRM+traffic skalibrowane (jazda −1 min); poślizg ODBIORU ~nieredukowalny |
| Jakość kodu | **4** | funkcja **3434 l** (+1973 zagn.), 561 `.bak` |
| Spójność architektury | **5** | czysty graf+flagi+shadow; podkopane JSON-jako-baza i god-objects |
| Odporność na skalę | **3** | single-process, fan-out O(kurierzy), nadpisywanie całych JSON, **swap 3,1 GB przy 30 kurierach** |
| Multi-city | **3** | DEFAULT_CITY/bbox env; traffic/districts/road-factor/center zahardkodowane; brak CityConfig |
| Przepustowość 1000+/dz | **2** | **p50 898 ms / 14% <500 ms** (nie 375!); route-sim 8 ms; ~67 decyzji/min single-process |

**Korekta:** zamrożone liczby wydajności w CLAUDE.md nieaktualne — Hetzner **już** 4 vCPU, OR-Tools **przestał** być
wąskim gardłem; regresja latencji do ~900 ms przeszła **niezauważona** (brak alarmu). Testy: **3080/0**, 356 plików.

---

## 12. ETAP 10 — PYTANIA → ODPOWIEDZI ADRIANA

Patrz §2 (wszystkie 6 pytań rozstrzygnięte). Otwarte techniczne: serializacja `alternatives[]` przed finalnym
re-sortem (artefakt logu, nie błąd) — do ewentualnego porządku „zapisuj alternatives po finalnym sortowaniu”.

---

## 13. DANE EMPIRYCZNE (kluczowe liczby, 3162 decyzji 11–22.06)

- Verdict steady-state: ~94% PROPOSE / **~5-6% KOORD** (gros `early_bird`), **0 KOORD-ów jakościowych**, 0 AUTO, 0 SKIP.
- **20,6% PROPOSE łamie R6 35 min** (max 96 min); 16,2% z `sla_violations>0`; **18,8% PROPOSE z gate-score <−100**.
- Gate-score PROPOSE mediana **+24** (serializowany −99 = artefakt SYNCWORKA, wycinany z gate-score).
- **Override 84% płaski** (gps 77% / blind 92-94%) — **NIE miara jakości** (Ziomek w tle, p. §2.3).
- **GPS realny tylko 17%** pozycji zwycięzców; informed 63% / blind 16,5%.
- **Bundle 66%**; r4 free-stop odpala **2,2%** (prawdziwe multi-restauracyjne bundle rzadkie).
- `fail03` (najtrudniejsze): człowiek odracza odbiór **+21,6 min med → 76% on-time**, bierze kuriera Ziomka tylko **10%** → luka „assign now”.
- Lunch: 47% R6-breach na bag≥2, 19% best-effort pula=0. Dinner: 59% solo, zdrowszy.
- ETA: jazda+dwell mediana **−1 min** (skalibrowane); e2e **+8,75 min** = poślizg ODBIORU (prep ~+11 min, R²≈0).
- Świeżość (shadow, n<30): ~247 min/dzień do odzysku za ~4 min detour, 0 naruszeń committed — **weryfikowane głębiej (research)**.
- Perf: p50 898 ms, 14% <500 ms, route-sim 8 ms; nproc=4, swap 3,1 GB. God: pipeline 6276/3434/1973, telegram 4257.
  JSON-DB: 789 MB, `orders_state` nadpisywany w całości; jsonl bez rotacji 77/67/54 MB.

---

## 14. SPEC-i GOTOWE DO BUDOWY (czekają na ACK Adriana)

### SPEC A — Decision Outcome Join (rec#1, „buduj — najpierw napisz jak działa”)
**Cel:** zmierzyć FAKTYCZNĄ jakość decyzji Ziomka — per zlecenie połączyć (a) co zaproponował
(`shadow_decisions.jsonl`: best.courier_id, predicted_delivered_at, r6_max_bag_time, score, czas_kuriera) z
(b) co się NAPRAWDĘ stało (panel/`events.db`/`orders_state`: realny kurier, picked_up_at[status5],
delivered_at[status7], realny czas dostawy, realny R6). Baza istnieje częściowo w `eta_calibration_log.jsonl`.
**Klucz:** `order_id`. **Metryki (per zlecenie + agregat dzienny):** realny czas dostawy = delivered−picked;
**realna terminowość % (≤35 min)** [nie predykcja]; kalibracja predicted vs real; **kontrfaktyk:** gdy człowiek
wziął INNEGO kuriera — porównaj realny wynik vs predykcję Ziomka dla JEGO kuriera (oznaczone jako szacunek, bo
kurier Ziomka nie pojechał). **Forma:** nowy read-only `tools/decision_outcome_join.py` →
`dispatch_state/decision_outcomes.jsonl` (1 wpis/zlecenie po dostarczeniu) + dzienny raport; doczepić do
`dispatch-eta-calibration.timer`. **ZERO wpływu na decyzje.** **Etapy:** def klucza/pól → backfill 12 dni →
dzienny job → raport (terminowość realna %, R6 realny %, poślizg, „gdzie Ziomek≠człowiek i kto wyszedł lepiej”).

### SPEC B — Defer-and-Hold (rec#5 / Q5)
**Co to znaczy (proste):** dziś Ziomek przy nowym zleceniu NATYCHMIAST wskazuje kuriera. Defer-and-hold =
pozwolić mu czasem POCZEKAĆ chwilę, bo za 1-2 min może być lepsza opcja (kurier zwolni się bliżej, dojdzie
2. zlecenie z tej samej restauracji do bundla, świeższa pozycja). Człowiek tak robi w peaku.
**Reguła Adriana:** mało pracy → **przypisz od razu**; peak → **trzymaj ~2 min**, potem najlepszy z okna.
**Mechanizm:** użyć istniejącego `loadgov_ewma` (saturacja) + `dispatch-pending-pool`: `loadgov_ewma < próg`
→ propozycja od razu (jak dziś); `≥ próg` → zlecenie do pending-pool na `DEFER_HOLD_MIN=2.0` min, po oknie
decyzja liczona RAZEM z innymi z okna (lepszy bundling + świeższe pozycje), wybór najlepszego.
**Flagi:** `ENABLE_DEFER_HOLD` + `DEFER_HOLD_LOADGOV_THRESHOLD` + `DEFER_HOLD_MIN=2.0`. **Shadow-first:**
najpierw loguj „defer vs no-defer” (czy bundle/score lepszy), potem flip. **Wyjątek:** czasówki (hard time) i
early-bird NIE deferować. Bezpieczne wg R-BUFFER-OK (2 min ≤ akceptowalne).

---

## 15. STAN WYKONANIA / TODO (na 2026-06-23)

> **⏩ POSTĘP (sesja wykonawcza 2026-06-23):**
> - ✅ **#1 outcome-join** zbudowany+commit (`tools/decision_outcome_join.py`, tag `ziomek-outcome-join-2026-06-23`). REAL 14d: flota **88,8% on-time / R6 realny 11,2%** (NIE 20% z predykcji); Ziomek gdy wzięty **87,4% ≈ człowiek 89,3%**; ETA +5,4 min optymizm; kontrfaktyk: luka = pozycja bez GPS.
> - ✅ **REC#8 świeżość = NO-GO**: additive food-age net-szkodliwe (genesis breaches, robi jedzenie STARSZE); 247 min/dz = 1 dzień; hard-SLA niewpięte+latencja+nigdy-live. Nie flipować; wracać tylko przez `..._SHADOW=true` ≥30 tras (write/restart, osobny ACK).
> - ✅ **#2 defer-hold = MARGINALNE** (measure-first `tools/defer_hold_shadow.py`, tag `ziomek-defer-hold-shadow-2026-06-23`): same-rest bundle z 2-min hold ~6% (1/~15 opóźnionych), loadgov≥4.5 peak rzadki (44/2280); korzyść pozycji pokrywa lewar no-GPS pos_age. NIE budować hot-path teraz.
> - 🎯 **NOWY TOP-LEWAR (z #1/E + research no-GPS): no-GPS `pos_age_min` trust-discount** — źródła `last_*` wygrywają 61% dyspozytorni, ufane bez kary za wiek; worek-pochodne wyrzucają `pos_age_min`. Doklej wiek → zniżka zaufania w tie-breakach (NIE dosuwaj pozycji/ETA = landmina prep). Dotyka 39% wygranych, dane już logowane, shadow-first. Hook: `courier_resolver.py:978-997` + `dispatch_pipeline.py:546-609`.
> - ✅ **#3 wydajność: root-cause PRZYPIĘTY (cProfile)**: ~900 ms = **OR-Tools TSP solve 69%**, wąskie gardło = Python-callbacki `time_cb`/`dist_cb`→`IndexToNode` ~9 mln×/solve (NIE panel, NIE „stały narzut" z audytu — obalone). Alarm latencji zbudowany+commit `b2a4502` (`tools/latency_alarm.py`, łapie p50 1006/p95 2500 ms). Fix = top-K pre-filter (mniej solve'ów) lub C++ transit matrix — **hot-path TSP, ACK+shadow-A/B przed edycją**.
> - ❌ **No-GPS `pos_age` lewar = NO-GO (zmierzony, `tools/pos_age_outcome.py`)**: wiek pozycji NIE przewiduje gorszej dostawy (on-time 86-88% płasko 0-30 min; >30 min nie występuje — ladder kapuje staleness→no_gps). Premisa moot.
> - 🧭 **META-WNIOSEK Z CAŁEJ KAMPANII POMIAROWEJ:** świeżość · defer-hold · pos_age · (wcześniej) rule_weights · ETA-calib · prep_bias — **WSZYSTKIE mierzą się jako no-op/marginalne/szkodliwe.** Algorytm Ziomka JEST dobry (87% on-time gdy wzięty ≈ człowiek 89%, R6 realny 11%). **Realne lewary = OPERACYJNY (pojemność floty — ściana) + INŻYNIERYJNY (#3 perf fix, #4 skala) — NIE strojenie scoringu/pozycji.** To spójne z ETAP 4/9 audytu.

**ZROBIONE:**
- ✅ Audyt 10-etapowy + weryfikacja adwersaryjna (ten plik).
- ✅ Cleanup: aider scratch (7 MB) usunięty.
- ✅ `ZIOMEK_LOGIC_REFERENCE.md` — dopięty blok „LIVE-STATE CORRECTION 2026-06-23”.

**RESEARCH RUNNING (read-only agenci, rec#8 + rec#3):**
- 🔬 Świeżość — GO/NO-GO z pełnymi danymi (re-pomiar, regresje, koszt wdrożenia, werdykt adwersaryjny).
- 🔬 Nauka bez GPS — jak człowiek dispatchuje bez GPS + propozycje lepszej inferencji pozycji.

**SPEC GOTOWE, CZEKA NA ACK BUDOWY:**
- ⏳ Outcome-join (SPEC A) — rec#1.
- ⏳ Defer-and-hold (SPEC B) — rec#5.

**PLAN, CZEKA NA ACK:**
- ⏳ Wydajność: root-cause ~900 ms (sync panel/login, fleet-snapshot) + alarm latencji — rec#6 „dawaj”.
- ⏳ Rozbicie `_assess_order_impl` (3434 l) + JSON→SQLite (`orders_state`) + CityConfig — rec#7 „Rozbijamy” (duże, behavior-preserving, wysoki blast radius).
- ⏳ `.bak` archiwum „ostatnie 3/plik” (~35 MB) — rec#10, ACK na komendę.

**Workflow projektu obowiązuje:** draft→ACK→`.bak`→edit→`py_compile`→import→test→commit→tag→(restart z ACK)→verify.
**NIGDY** restart `dispatch-telegram` bez ACK. Peak Pn-Pt 11-14/17-20, Sob 16-21 — bez restartów.

---

## 16. POINTERY (file:line)

- Montaż score: `dispatch_pipeline.py:4562` (bonus_penalty_sum), `:4605` (final_score), gate-score `:1975`.
- Selekcja: `dp:5299-5396` (sort → `_demote_blind_empty :2045` → late-pickup tiering `:5342`).
- Verdict cascade: `dp:5672-6001`; best-effort `:6003-6276`; `_always_propose_on :2251`.
- Bramki: `feasibility_v2.py` R6 `:1237`, SLA `:1160`, tier-cap `:463`, pickup-reach `:650`.
- Baza score: `scoring.py:22-25`; wait penalties `:61/110/167`.
- Tiery: `common.py` speed `:1922`, DWELL `:1948`, bag-cap `:1167`, LOADGOV `:1900`, traffic `:478`.
- Świeżość: food-age `tsp_solver.py:347-378` + `route_simulator_v2.py:1065-1093` (OFF); monitory `feasibility_v2.py:942`, `tools/freshness_shadow_monitor.py`.
- No-GPS: `courier_resolver.py` (last-known-pos store `courier_last_pos.json`, `_rescue_from_last_pos`, BIALYSTOK_CENTER `:80`).
- Flagi: `flags.json` (kanon, hot-reload), resolver `common.py:35/46/232`.

## 17. FUTURE DECISIONS — do rozważenia w przyszłości (NIE robione; Adrian 2026-06-23: „zapisać, na razie kończymy")

Sesja wykonawcza 2026-06-23 **zamknięta**. Kampania pomiarowa udowodniła, że **algorytm Ziomka jest dobry**
(89% on-time floty, 87% gdy wzięty ≈ człowiek) i **większość lewarów scoringowych to no-opy** (świeżość /
defer-hold / no-GPS pos_age / rule_weights / ETA-calib / prep_bias — wszystkie zmierzone NO-GO/marginalne).
Zostały TYLKO 3 pozycje INŻYNIERYJNE/OPS — wszystkie wymagają świadomej decyzji „czy w ogóle warto",
bo **żadna nie jest pilna** (system działa stabilnie przy 180-300 zleceń/d):

1. **#3 PERF-FIX** (root-cause przypięty: OR-Tools Python-callbacki `time_cb`/`dist_cb`→`IndexToNode` ~9 mln×/solve = **69% latencji**; NIE panel). Opcje: (a) **top-K pre-filter** — OR-Tools-uj tylko top 3-5 kandydatów zamiast każdego bag≥2 → mniej solve'ów; (b) **C++ transit matrix** zamiast `RegisterTransitCallback`. **⚠ Payoff latencji NIEPEWNY** (200 ms cap może wiązać → fix poprawi jakość-na-ms, nie p50). **Przed budową OBOWIĄZKOWO: shadow-A/B** mierzący czy w ogóle tnie p50 + czy top-K nie zmienia zwycięzcy. Hot-path TSP (`tsp_solver.py` + trigger `route_simulator_v2.py` `V327_MIN_OR_TOOLS_BAG_AFTER`), wysoki blast radius. **Pytanie decyzyjne: czy ~900 ms realnie boli przy obecnym wolumenie — pewnie NIE → to inwestycja POD SKALĘ.**
2. **#4 SKALA** (pod multi-city / 1000+ zleceń/d): rozbicie `_assess_order_impl` (3434 l) na stage'e + `orders_state` JSON→SQLite + `CityConfig`. Duże, behavior-preserving, wysoki blast radius. **Tylko gdy realnie idziemy multi-city / 3-5× wolumen.** Dziś niepotrzebne.
3. **TANIE OPS WINS** (gdyby wracać, najmniejszy koszt/ryzyko): wpiąć `tools/latency_alarm.py` w timer co ~30 min + alert Telegram (regresja 900 ms przeszła niezauważona — to by łapało następną); `.bak` archiwum „ostatnie 3/plik" (~35 MB).

**Domyślna rekomendacja audytora:** nic z powyższych nie jest pilne; #3/#4 = inwestycje POD SKALĘ (robić gdy rośnie wolumen/miasta). Najtańszy realny zysk przy powrocie = **alarm-timer**. **Strojeń scoringu NIE ruszać** (zmierzone no-opy). Narzędzia pomiarowe (`tools/{decision_outcome_join,defer_hold_shadow,latency_alarm,pos_age_outcome}.py`) zostają — re-run kiedykolwiek by zweryfikować na świeższych danych.

## 18. KAMPANIA KALIBRACYJNA 2026-06-23 + ROADMAP (następne sesje: START STĄD przy kalibracji)

### 18.1 Zrobione — przesiew + WĄTEK WAG ZAMKNIĘTY
**Przesiew:** `tools/calibration_screen.py` → `dispatch_state/calibration_set_june.jsonl` (**3721 miarodajnych**).
Odsiewa: czasówki, nie-PROPOSE, **pick-Ziomka-realnie-nie-pracował** (aktywność sla_log ±90 min — Adriana
„był w domu", 8%=323), brak floty <2. Re-run na świeższych danych kiedykolwiek.

**WĄTEK WAG BAZOWYCH — ZAMKNIĘTY, NIE WRACAĆ. Werdykt: wagi (0.30/0.25/0.25/0.20) są dobrze ustawione.**
7 narzędzi read-only w `tools/`: calibration_screen · decision_outcome_join · pos_age_outcome ·
weight_calibration · load_reshape_replay · distance_reshape_replay · base_amplify_probe.
- **reshape** formuły obciążenia (flat-0-2/kara-3+) → zmienia **0,4%** picków.
- **reweight** dystansu (W 0.30→0.20 / decay 5→10) → **1-3%**.
- **amplify** (baza ×K) → zmieniłoby, ale ZERWAŁOBY 2,5% bundli niesionych free-stopem (100% on-time) + nadpisało kary wait → **GORZEJ**.
- **Mechanizm:** 97,5% picków = solo (baza+tier+wait decydują, marginesy ~88 pkt → odporne na tweaki); 2,5% = bundle (free-stop **+150** słusznie dominuje). Wagi correctly-powered.
- **Czemu mediana nasycona:** on-time ~90% NIEZALEŻNIE od wyboru dostępnego kuriera (zależy od prep restauracji + dystansu-do-DOSTAWY, nie który-kurier). Ziomek ≈ człowiek gdy wzięty (87% vs 89%, czas 16,9 vs 18,0).
- **METODA (działa też dla bonusów):** zmiana JEDNEGO członu = exact-recompute z zalogowanego `score`+cechy (`new = score + W·(s_new−s_old)`), bez re-runu silnika, re-rank feasible. Bonusy (`bonus_r4`/`v326_speed_score_adjustment`/`bonus_r9_wait_pen`/`bonus_l1` — serializowane) → identycznie.

### 18.2 ROADMAP — następne testy (Adrian 2026-06-23)
1. **BONUSY I KARY** (TE realnie ruszają picki, w przeciwieństwie do wag bazowych):
   - **tier (gold +7,5 / slow −12,5)** — dane: gold prawdopodobnie ZA SILNY (człowiek odchodzi od golda 190 vs 110). Najbardziej obiecujący.
   - **kary wait** (`bonus_r9_wait_pen` do −1000 / `v3273_wait_courier`) — czy poprawnie łapią stygnięcie; ważne dla OGONA (late restaurant).
   - **free-stop r4 (+150) / L1 (+25)** — magnituda; rządzą 2,5% bundli.
   - METODA: exact-recompute replay (jak load) na `calibration_set_june.jsonl` + outcome.
2. **TWARDE BRAMKI** (zmieniają FEASIBILITY = realny lewar, wyższe ryzyko):
   - **R6=35 termiczny** — czy próg dobry; czy over-rejectuje → wpycha w best-effort (18-20% peak orderów idzie best-effort pool=0)?
   - **bag-capy** (sanity 8 / tier gold6/std5/slow4) — czy wymuszają best-effort w saturacji?
   - **SLA=35** ≈ duplikat R6 (czy oba potrzebne).
   - METODA: z czystego zbioru — kandydaci R6/cap-odrzuceni vs czy best-effort fallback wyszedł GORZEJ niż odrzucony by wyszedł; replay z przesuniętym progiem + monitor R6/SLA.

### 18.3 MOJE PROPOZYCJE (potencjalnie najlepsze wyniki — uzasadnione kampanią)
1. **🎯 ANALIZA OGONA PORAŻEK — rekomendacja #1.** Mediana nasycona/dobra → cała wartość w ~11% ZŁYCH dostaw (realny R6 breach). Zbadać co je ODRÓŻNIA: saturacja (pool=0)? konkretne restauracje (wolny prep)? strefy/dystans? godziny? czasówki? **Wzorzec adresowalny** (restauracja X zawsze breach / strefa Y) → handle specjalnie. **Czysta saturacja** → capacity (nie-algorytm). Mówi GDZIE realnie działać — zamiast stroić nasyconą medianę.
2. **Committed pickup-time / pickup-debias.** +8,75 min e2e slip = optymizm zadeklarowanego `czas_kuriera`. Jest shadow `ENABLE_PICKUP_DEBIAS_SHADOW` (06-22). Kalibracja realistycznej OBIETNICY → mniej idle kuriera (stygnięcie), uczciwe ETA, lepiej gra z proaktywnym komunikatem. Zmienia co Ziomek OBIECUJE.
3. **OPERACYJNY (najwyższy impakt, nie-algorytm): real-time „gotowe" z restauracji.** Dominujący błąd (pickup-slip ~9 min) = LUKA DANYCH (Ziomek nie wie kiedy jedzenie REALNIE gotowe). Ping „gotowe" naprawia największe źródło błędu ETA + kasuje wait. Spójne z całą kampanią (operacyjny > scoring).
4. (Niżej) **efektywność/km** (bundlować więcej bez 3+); **ML two-model arbitraż** (shadow, nierozwiązany: solo 0.896 / bundle 0.642).

*Koniec checkpointu 2026-06-23.*
