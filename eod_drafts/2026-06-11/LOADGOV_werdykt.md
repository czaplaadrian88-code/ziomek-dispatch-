# SP-B2-LOADGOV — werdykt bramki flipu `ENABLE_FLEET_LOAD_GOVERNOR`

**Data:** 2026-06-11 ~12:30 UTC · **Zakres:** bramka flipu = dwa niezależne dowody + rozkład realnego load · **READ-ONLY** (zero zmian flag/serwisów)
**Co oceniamy (commit `5a39f14`, flaga OFF):** load floty = aktywne zlecenia / aktywni kurierzy, EWMA tau=15 min; przy flipie: **ewma>2,7 → kara −40 pkt** za dokładanie zlecenia kurierowi z workiem **≥3** (demote, NIE reject — ALWAYS-PROPOSE); **ewma>3,5 → jeden alert „tryb defensywny"** (hysteresis, re-arm <3,0).

---

## DOWÓD A — cascade harness (2026-06-07): wynik DOKUMENTOWANY, re-run niemożliwy

**Stan przyrządu:** `eod_drafts/2026-06-07/cascade_harness.py` i `outcome_model_and_cascade.py` to **stuby** — docstring z wynikami + `print("pełna implementacja w transkrypcie sesji 2026-06-07")`. Brakuje całej logiki wykonawczej (build ev/pick/deliv/assigns, concur, bagt, model `pr(bag,peak)`, pętla `sim(policy)`); transkrypt sesji nie istnieje na dysku. Zgodnie z zadaniem **nie odbudowywałem harnessu od zera** — używam wyników udokumentowanych w docstringach + werdyktu [[ziomek-autonomy-cascade-verdict]] i przenoszę ciężar eksperymentu na Dowód B.

**Drabina polityk z harnessu (n=2917 / n=2882, okno 24.05–06.06):**

| szczebel | model słaby (bag+peak) | model mocny (geometria+prep) |
|---|---|---|
| człowiek realny | 7,9% | 7,9% |
| **argmax (jak dziś, bez governora)** | **17,0%** (worek max 33) | **11,3%** (dolna granica) |
| **+anti-overload (kara za duży worek — analog governora)** | **15,1%** | **10,9%** |
| load-aware (kandydaci) | 13,6% | 9,9% |
| load-aware + pełny roster | **10,1%** (worek ≤3) | **8,0%** |

**Odczyt dla governora:** kara za duży worek bez logiki dystrybucji = szczebel „anti-overload": domyka **1,9 pp z 6,9 pp** drogi 17%→10,1% (**~28%**) w modelu słabym i **0,4 pp z 3,3 pp** (~12%) w mocnym. Werdykt 06-07 wprost: *„anti-overload bag-cap = tani CZĘŚCIOWY bezpiecznik, nie rozwiązanie"*. Zastrzeżenie porównania: harness karał bag≥4 bezwarunkowo; governor karze **bag≥3 i tylko przy load>2,7** — mechanizm ten sam (demote przeładowanych), kalibracja minimalnie ostrzejsza w peaku, nieaktywna poza nim.

**Uzupełnienie z realnej telemetrii (zamiast re-runu):** na 1940 realnych PROPOSE z 02–11.06 kara uderzyłaby w ZWYCIĘZCĘ (best z workiem ≥3 przy ewma>2,7) w **140 decyzjach (7,2% PROPOSE)**; w 81/83 przypadków z zserializowaną alternatywą worek<3 alternatywa była w zasięgu ≤40 pkt → **kara −40 realnie przestawia wybór** (w skali produkcyjnego scoringu −40 pkt > cały komponent dystansu 0,30×100=30 pkt — demotion w praktyce niemal-leksykograficzny).

## DOWÓD B — symulator zdarzeniowy (kopia agent_sim → /tmp, GATE 5/5)

**Metoda:** kopia `/root/bartek2_workdir/agent_sim/` → `/tmp/loadgov_sim/` (oryginał NIEtknięty). Dodana polityka **P1G = P1 (argmax-najbliższy, proxy dzisiejszego argmaxa) + produkcyjny governor**: load = aktywne zlecenia (utworzone, niedoręczone) / dispatchable kurierzy, EWMA tau=15 min licząc per tick 1 min; przy ewma>2,7 kandydat z workiem ≥3 dostaje karę demotującą (główny wariant = niemal-leksykograficzny, bo −40 pkt > rozstrzały scoringu — patrz Dowód A; wariant czuły 6,7 km-ekwiwalentu niżej). Zero rejectów. P3 (load-aware bez capa) i P4 (pełny BARTEK 2.0) = górna referencja. Sanity: P1/P3/P4 odtworzyły co do dziesiątki wyniki z `agent_sim/results.json`.

**breach% (dostawy >35 min od odbioru), scenariusz × polityka:**

| scenariusz | **P1 bez governora** | **P1G z governorem** | P3 load-aware | **P4 pełny load-aware** | governor domyka drogi P1→P4 | worek max P1→P1G |
|---|---|---|---|---|---|---|
| **A. KRACH 16.05** (384 zlec/11 kur; real 32,6%) | **35,8** | **26,3** | 29,2 | **14,9** | 9,5 pp z 21,0 (**45%**) | 33 → 12 |
| B. WALENTYNKI 14.02 (487/15) | 34,3 | 25,1 | 27,4 | 11,3 | 9,1 pp z 23,0 (40%) | 27 → 16 |
| C. WZORCOWY 03.05 (307/18) | 38,5 | 24,0 | 1,0 | 0,0 | 14,5 pp z 38,5 (38%) | 16 → 13 |
| D. PIĄTEK 22.05 (212/11) | 34,5 | 19,5 | 8,5 | 0,0 | 15,0 pp z 34,5 (43%) | 18 → 11 |
| E1. 16.05 + flota 15 | 30,0 | 19,6 | 13,5 | 0,5 | 10,3 pp z 29,4 (35%) | 22 → 9 |
| E2. 16.05 + flota 20 | 28,1 | 16,7 | 7,2 | 0,3 | 11,4 pp z 27,9 (41%) | 17 → 15 |
| E3. 03.05 ×2 wolumen (4,5 zlec/h) | 39,1 | 31,6 | 29,4 | 10,9 | 7,6 pp z 28,3 (27%) | 38 → 17 |
| E4. 14.02 awaria 3 kurierów 18:00 | 34,3 | 27,0 | 30,2 | 11,9 | 7,2 pp z 22,3 (32%) | 30 → 21 |

**Czułość mapowania kary** (−40 pkt → km): kara 6,7 km zamiast niemal-leksykograficznej daje na krachu **22,0%**, na walentynki **22,1%** — wynik **odporny na mapowanie** (zakres 22–26% vs 35,8 bez governora; słabsza kara bywa nawet lekko lepsza, bo zachowuje sens bliskości wewnątrz puli ukaranych).

**Wnioski B:**
1. **Governor nigdzie nie pogarsza** i tnie breach o **7–15 pp w każdym scenariuszu** (relatywnie −21…−43%). W przeciążeniu (A/B/E4) jest nawet **lepszy niż czyste P3** — kara-przy-progu działa jak miękki cap, a P3 bez capa w krachu degeneruje (zgodne z REPORT.md agent_sim i z odrzuceniem „anti-overload jako rozwiązania" w kaskadzie).
2. **Governor to ~1/3 drogi** (27–45%) od argmaxa do pełnego load-aware (P4). W spokojne dni (C/D) różnica do P4 pozostaje ogromna (24% vs 0%) — bo problemem argmaxa jest sam wybór po bliskości, nie tylko worki ≥3.
3. Worek max spada 33→12-21, ale **NIE do zdrowych ≤4 jak w P4** — przy ewma≫2,7 prawie wszyscy mają worek ≥3, kara się saturuje (wszyscy ukarani po równo → wraca wybór po bliskości wewnątrz przeciążonych). To strukturalny limit kary bez twardego capa i bez dystrybucji.
4. Na krachu governor był aktywny przy 95% przypisań (ewma_max=9,3) i zmienił wybór kuriera w 111/384 przypisań.

## Rozkład REALNEGO load (02–11.06, rekonstrukcja + walidacja)

**Zastrzeżenie źródła:** pola `loadgov_*` w shadow_decisions istnieją dopiero od deployu 11.06 ~12:15 UTC (**2 rekordy** w chwili analizy). Dla całego okna load **zrekonstruowany** per decyzja: aktywne zlecenia z `events.db audit_log` (zlecenie aktywne między pierwszym eventem a dostawą, guard świeżości 3h jak w produkcji) / `pool_total_count` decyzji (= dispatchable fleet przed feasibility — dokładnie mianownik governora); EWMA tau=15 po strumieniu decyzji. **Walidacja na 2 pierwszych produkcyjnych rekordach:** rekonstrukcja 2,31/2,23 vs produkcyjne 2,46 (kurierzy 13=13, zlecenia 30/29 vs 32) — rekonstrukcja **lekko konserwatywna** (~5–8% niedoszacowanie licznika), realne odsetki mogą być o ułamek wyższe.

**2160/2271 decyzji z określonym load (reszta = pusta flota → produkcyjnie load=None):**

| metryka | wartość |
|---|---|
| load chwilowy p50 / p90 / p99 / max | 1,82 / 3,42 / 4,27 / 5,33 |
| **decyzje przy EWMA > 2,7 (kara aktywna)** | **490 = 22,7%** (chwilowo >2,7: 25,4%) |
| **decyzje przy EWMA > 3,5 (strefa alertu)** | **155 = 7,2%** (chwilowo >3,5: 8,1%) |
| PROPOSE z karą uderzającą w zwycięzcę (best worek≥3 @ ewma>2,7) | 140 = **7,2% PROPOSE** |
| **alerty „tryb defensywny"** (hysteresis >3,5 / re-arm <3,0) | **6 w 9,3 dnia; 5 w ostatnich 7 dniach** — 02.06 17:16, 04.06 15:59, 06.06 22:12, 07.06 15:03, 07.06 19:28, 09.06 16:25 (Warsaw) |

Per dzień (% decyzji przy ewma>2,7): 02.06 **27,5%** · 03.06 4,3% · 04.06 **45,4%** · 05.06 23,2% · 06.06 2,0% · 07.06 **55,6%** (niedziela kaskady) · 08.06 0% · 09.06 20,2% · 10.06 0% · 11.06 (do 12:17) 0%. **Governor to mechanizm na ~3–4 ciężkie dni w tygodniu, w pozostałe śpi** — dokładnie taki profil powinien mieć bezpiecznik.

## WERDYKT DLA ADRIANA

Ta flaga to bezpiecznik na godziny przeciążenia: gdy na jednego pracującego kuriera przypada średnio więcej niż 2,7 aktywnych zleceń, Ziomek przestaje dokładać do toreb z 3+ zleceniami (kara −40 pkt — propozycje dalej wychodzą, tylko wskazują mniej obładowanych), a powyżej 3,5 dostajesz jeden alert „dzwoń po posiłki". Liczby: w symulatorze krachu 16.05 breach spada z 35,8% (bez) na 26,3% (z governorem), a pełny load-aware robi 14,9% — czyli **sam governor domyka ok. 1/3 drogi** (w kaskadzie 06-07 analogicznie: 17,0%→15,1%, pełny load-aware 10,1%); na realnych danych kara zadziałałaby w ~7% propozycji, a stan „przeciążenie" dotyczył 23% decyzji z ostatnich 9 dni. **Rekomendacja: TAK — flipnąć, z zastrzeżeniem**, że to częściowy hamulec, a nie rozwiązanie: pełne load-aware + twarde capy (P4/BARTEK 2.0) pozostają celem, bo governor nie utrzyma worków ≤4 w prawdziwym krachu. Po flipie obserwuj dwie rzeczy: (1) odsetek propozycji z `bonus_loadgov_shadow_delta≠0` i czy przy ewma>2,7 best przesuwa się na kurierów z workiem <3 (oczekiwane ~7% propozycji ze zmianą), (2) breach/committed-time w dni z ewma>2,7 vs analogiczne dni sprzed flipu (plus max worek w peaku). Alert >3,5 odpalałby się realnie **~5 razy w ostatnim tygodniu** (04.06, 06.06, 2× 07.06, 09.06) — to sygnał, nie spam.

---
*Repro: Dowód B `/tmp/loadgov_sim/run_loadgov.py` (wyniki `loadgov_results.json`), rozkład `/tmp/loadgov_real.py` (wyniki `/tmp/loadgov_real_out.json`). Oryginalny `agent_sim/` i pliki harnessu 06-07 nietknięte. Okno danych realnych: 02.06 07:11 → 11.06 12:17 UTC (starsze rotacje shadow_decisions niedostępne).*
