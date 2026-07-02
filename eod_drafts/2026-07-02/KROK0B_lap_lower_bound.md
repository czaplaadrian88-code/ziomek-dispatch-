# KROK 0B — LAP lower bound vs greedy (ile tracimy na greedy)

**Data:** 2026-07-02 · **Tryb:** READ-ONLY (zero zmian silnika/flag/systemd, zero zapisu do dispatch_state, zero commitów)
**Narzędzie:** `dispatch_v2/tools/greedy_vs_lap_replay.py` (CLI `--since/--until/--window`)
**Źródło danych:** `scripts/logs/shadow_decisions.jsonl`

---

## TL;DR

Silnik przypisuje kurierów **greedy** (każde zlecenie osobno bierze swojego najlepszego wg `score`, z lex-tiebreak). Policzyłem **dolną granicę** ile ten greedy traci względem globalnie optymalnego przypisania (LAP / algorytm węgierski) na tej samej puli kurierów i tym samym oknie czasowym.

- **W jednostkach objektywu silnika (`score`)** globalne LAP bije greedy w **~43% konkurujących okien** (5-min), łącznie **~54,7 tys. score / 6 dni ≈ 9,1 tys. score/dzień ≈ 46 score/zlecenie**.
- **Ale w FIZYCZNYCH minutach dojazdu do odbioru ta sama przetasówka to grosze: ~141 min / 6 dni ≈ 24 min/dzień ≈ 0,12 min/zlecenie.**
- Innymi słowy: greedy „myli się" wg wewnętrznej punktacji dość często, ale **niemal cała ta strata siedzi w miękkich preferencjach silnika (bundle/pos_source/R6-soft), a nie w czasie dojazdu.** Fizycznie klient tego nie czuje — problem jest, zgodnie z założeniem, **malutki** (mediana puli 3, kurierzy zgrupowani geograficznie → drugi-najlepszy ≈ najlepszy w minutach).

**To LOWER BOUND KIERUNKOWY, nie obietnica zysku** (uzasadnienie w OGRANICZENIACH).

---

## Główne liczby (okno 5 min — bazowe)

Zakres: **2026-06-27 .. 2026-07-02 (6 dni), 1181 zleceń** z niepustą pulą wykonalną (z 1266 rekordów NEW_ORDER; reszta = sentinel/brak kandydata).
Grupy wielo-zleceniowe (≥2 zlecenia w tym samym oknie): **348** · zleceń w grupach: **937** · pary-konflikty (≥2 zlecenia z tym samym `best_cid`): **260**.

| Metryka | Objektyw SCORE (silnik) | Objektyw ETA (czysty dojazd) |
|---|---|---|
| Δ total / 6 dni | **54 731 score** | 258,8 min |
| Δ / dzień | 9 122 score | 43,1 min |
| Δ / zlecenie (wszystkie 1181) | 46,3 score | 0,22 min |
| Grupy z zyskiem LAP>greedy | 149 / 348 (**42,8%**) | 71 / 348 (20,4%) |
| **Ta sama SCORE-optymalna przetasówka w MINUTACH dojazdu** | **141,1 min / 6 dni · 23,5 min/dzień · 0,12 min/zlecenie** | — |

Dwa objektywy, bo to dwie różne rzeczy:
- **SCORE** = greedy-po-score vs LAP-po-score. Ten sam objektyw po obu stronach → **czysta luka dopasowania** wg tego, co silnik realnie optymalizuje. Nagłówkowa liczba.
- **ETA** = greedy-po-eta vs LAP-po-eta (`travel_min_cal`). „Ile minut dojazdu można by wycisnąć, gdyby celem był sam dojazd" — górny wariant fizyczny, ale nie jest tym co silnik robi.
- **„Ta sama przetasówka w minutach"** = biorę DWA przypisania z objektywu SCORE i liczę ich sumę minut dojazdu. To najuczciwsza liczba fizyczna sprzęgnięta z realną decyzją: score-optymalna przetasówka zmienia dojazd tylko o **0,12 min/zlecenie** (a bywa że go POGARSZA — patrz przykład).

## Wrażliwość na okno grupowania

| Okno | Grupy | Δscore total | Δscore/zlec | Δmin (score-przetasówka) /zlec | Δmin (eta-objektyw) /zlec |
|---|---|---|---|---|---|
| 2 min | 245 | 42 426 | 35,9 | 0,065 | 0,096 |
| **5 min** | **348** | **54 732** | **46,3** | **0,119** | **0,219** |
| 10 min | 288 | 115 105 | 97,5 | 0,172 | 0,449 |

Im szersze okno, tym większa Δ — ale szersze okno **coraz mniej fizycznie prawdziwe** (zlecenia oddalone o 8 min raczej NIE są otwarte jednocześnie). Realny przedział fizyczny: **~0,06–0,17 min/zlecenie** dla przetasówki score-optymalnej; **~0,1–0,45 min/zlecenie** dla czystego objektywu dojazdu.

## Skąd bierze się Δ (walidacja, że to nie artefakt)

Rozbicie 348 grup (5 min):
- **295 grup „courier-rich"** (kurierów ≥ zleceń, wszyscy dostają przydział): Δscore **51 308** / Δmin **120,9** → **94% straty to czyste przetasowania w pełni obsadzalnych oknach**, nie kara za brak kuriera.
- 53 grup „courier-short" (zleceń > kurierów, ktoś nieobsłużony): Δscore tylko 3 424 / Δmin 20,2.

Fallback za „nieprzypisane" (najgorszy własny wykonalny kandydat) jest identyczny dla greedy i LAP, więc się skraca — potwierdzone: gros Δ pochodzi z grup, w których obie metody obsadzają wszystkich.

## Konkretny przykład konfliktu (2026-06-27T12:20)

Dwa zlecenia, oba chcą kuriera **508** jako top:

```
order 483715  best=508:   508 score108.9 eta15.6 | 484 score102.6 eta15.6 | 457 score52.4
order 483716  best=508:   508 score 77.4 eta20.0 | 457 score  4.3 eta18.3 | 484 score-2.0
GREEDY (wg przyjścia): 483715->508 , 483716->457   (508 zajęte przez pierwsze)
LAP  (globalnie):      483715->484 , 483716->508
Δscore = +66.7   Δmin_dojazd = -1.70  (LAP ma 1,7 min WIĘCEJ dojazdu!)
```

508 jest DUŻO cenniejsze dla 483716 (spadek do next-best −73 score) niż dla 483715 (spadek −6). Globalnie 508 powinno iść do 483716 — greedy oddał je „temu kto pierwszy". **Ale ta poprawka NIE skraca dojazdu (wręcz +1,7 min); zysk siedzi w miękkich członach score.** To ilustruje główny wniosek.

---

## OGRANICZENIA (każda liczba z gwiazdką)

1. **To LOWER BOUND KIERUNKOWY, nie obietnica zysku.** Przypisanie zmienia przyszły stan floty (bag+1, pozycja, `free_at`) → **ceteris-paribus jest nieprawdziwe**. LAP zakłada, że wiersze macierzy `koszt(zlecenie,kurier)` są niezależne, a nie są: gdy LAP daje kuriera innemu zleceniu, realne score wszystkich kolejnych decyzji by się zmieniło. Prawdziwy „zysk z LAP" byłby MNIEJSZY niż te liczby (część znika po przeliczeniu stanu).

2. **Poziom PROPOZYCJI (shadow), nie wykonania.** `shadow_decisions.jsonl` to propozycje silnika liczone równolegle, nie faktyczne przydziały. Nie wiemy, kogo koordynator/silnik realnie przypisał ani kiedy zlecenie przestało być „otwarte".

3. **„Jednoczesność" jest przybliżona oknem czasowym.** Rekordy to jednorazowe decyzje w momencie przyjścia zlecenia (brak re-ewaluacji w tym logu). Grupuję zlecenia w oknach tumbling (2/5/10 min) jako proxy „otwarte naraz i konkurujące o tę samą pulę". Brak w danych rzeczywistego czasu przydziału → nie wiem które zlecenia BYŁY faktycznie równocześnie nieobsadzone. Stąd wrażliwość na okno.

4. **Wiersze macierzy liczone w RÓŻNYCH momentach.** Każde zlecenie w grupie ma kandydatów wycenionych wg stanu floty ze swojej chwili przyjścia (sekundy–minuty różnicy), a te stany już odbijają wcześniejsze REALNE przydziały. To nie jest spójny snapshot jednej chwili → dodatkowy szum.

5. **`score` to złożony objektyw, nie minuty.** Nagłówkowa Δscore (46/zlec) brzmi duża, ale jednostki są miękkie (bundle bonus, R6-soft, pos_source, wave). Dlatego kluczowa jest kolumna „w minutach": ~0,12 min/zlecenie. Kto chce liczby fizycznej — bierze minuty, nie score.

6. **Kandydaci z score ≤ −1e6 (sentinele „infeasible") wyrzucone**; puste pule (85 zleceń) pominięte. Fallback dla nieobsłużonych = najgorszy własny wykonalny kandydat (arbitralny, ale symetryczny greedy↔LAP, więc się skraca; udział courier-short = mały, pkt „walidacja").

7. **Greedy modelowany jako „kolejność przyjścia + best wolny".** Realny silnik/koordynator może rozwiązywać konflikty inaczej (np. wyższy-score wygrywa). Wariant „score-priority" dawałby nieco inne, ale tego samego rzędu liczby (konflikt dotyczy 2 zleceń o zbliżonym top-score).

---

## CO BY TRZEBA DOLOGOWAĆ do pełnego (nie-lower-bound) pomiaru

Żeby zamienić kierunkowy lower bound w twardy pomiar realnego zysku:
- **Snapshot puli per TICK** (nie per-przyjście): dla każdego ticku lista wszystkich OTWARTYCH (nieobsadzonych) zleceń + pełna macierz `score/eta(zlecenie,kurier)` wyliczona w JEDNEJ chwili, z jednego stanu floty. Wtedy wiersze są spójne i „jednoczesność" nie jest zgadywana.
- **Znacznik faktycznego przydziału + czasu** (kto/kiedy dostał zlecenie) — żeby wiedzieć, które zlecenia realnie konkurowały.
- **Re-symulacja stanu** (bag/pozycja/free_at po każdym hipotetycznym przydziale LAP) — dopiero to znosi założenie ceteris-paribus i daje prawdziwą Δ zamiast górnej granicy.

---

## Werdykt

Greedy vs globalne LAP: **luka istnieje i jest częsta wg objektywu silnika (~43% konkurujących okien), ale fizycznie znikoma (~0,1–0,2 min dojazdu/zlecenie, ~24–43 min/dzień w agregacie)**, i to jako LOWER BOUND (realny zysk ≤ to). Strata żyje w miękkich członach score, nie w czasie dostawy. **Batch/LAP-solver nie jest priorytetem ROI** — problem malutki potwierdzony liczbami. Jeśli kiedyś wracamy do tematu, najpierw dołożyć logging per-tick (wyżej), bo bez niego każdy pomiar zostaje kierunkowy.

## Pliki
- Narzędzie: `/root/.openclaw/workspace/scripts/dispatch_v2/tools/greedy_vs_lap_replay.py`
- Raport: `/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-07-02/KROK0B_lap_lower_bound.md`
- Dane wejściowe: `/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl` (1266 rekordów, 06-27..07-02)
