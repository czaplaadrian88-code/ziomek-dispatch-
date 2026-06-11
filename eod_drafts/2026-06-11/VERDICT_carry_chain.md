# WERDYKT: ENABLE_CARRY_CHAIN_PENALTY (2026-06-11)

> **WERDYKT (5 linijek):**
> 1. **FLIP CZĘŚCIOWY — tylko soft-penalty; hard-reject ZNEUTRALIZOWAĆ przy flipie** (env `CARRY_CHAIN_HARD_REJECT_STOPS=999`), bo hard-reject łamie ALWAYS-PROPOSE: w 3/6 zaobserwowanych przypadków zdejmowałby WSZYSTKICH feasible kandydatów (pool 1/2/5) → nowy KOORD/cisza = 13,6% wszystkich KK-dinner PROPOSE; do tego strzela na kurierach 3–10 min od restauracji, czyli POZA wzorcem forensic (carry 15–30 min).
> 2. Soft-penalty jest bezpieczna (działa w score, NIE w feasibility — strukturalnie nie tworzy cisz), rzadka (0,98% best; ~2 propozycje/dzień) i trafna: top przypadki to dokładnie wzorzec forensic (kurier z cudzym jedzeniem w torbie wysyłany 15–21 min przez miasto po kolejny odbiór).
> 3. **Wymóg „14d shadow" NIGDY nie został spełniony i nie mógł** — flaga OFF gate'uje CAŁE obliczenie (dispatch_pipeline ~l.3597), więc pola `carry_chain_*` w shadow logu są w 100% zerowe (1929 best + 6870 alternatives, zero applied/penalty/hard_reject). Niniejsza analiza = rekonstrukcja offline 1:1 z `bag_context` + `carry_chain_drive_min_used` (stałe prod: coeff 1,5, próg 15 min, HR stops≥2, dinner 17–21).
> 4. KK-dinner exclusion (ON) problemu score'owego NIE pokrywa (działa tylko na ścieżce autonomii: ROUTE_ALERT zamiast AUTO; koordynator nadal dostaje propozycję z bestem wybranym bez kary), ale KK **zniknął z rozkładu soft-penalty (0/19 przypadków)** — kara generyczna ma dziś wartość na INNYCH restauracjach (Trzy Po Trzy, Kumar's, Raj, 500 Stopni).
> 5. Dłuższy shadow w status quo = bezcelowy (pola dalej będą 0). Po flipie soft pola zaczną się wypełniać → **7 dni realnej obserwacji** + tech-debt dla sesji silnika (telemetria-zawsze, split flag, ETAP4). Flip wykonuje sesja silnika/Adrian — szczegóły wykonawcze w §7.

---

## 1. Stan flagi i mechanika (zweryfikowane w kodzie 11.06)

- `flags.json`: `"ENABLE_CARRY_CHAIN_PENALTY": false` (od 27.05; dziś = 15 dni). **UWAGA: wpis w flags.json jest dla call-site'u MARTWY** — `dispatch_pipeline.py:3597` czyta `C.ENABLE_CARRY_CHAIN_PENALTY` = stałą modułu z env (`common.py:2761`, default "0"), zamrożoną przy imporcie. Flaga NIE jest w `ETAP4_DECISION_FLAGS`. Env nieustawiony w żadnym override.conf (sprawdzone: dispatch-shadow/czasowka/plan-recheck service.d) → OFF wszędzie, stan spójny.
- Semantyka (`common.py:2816-2902`, funkcje pure):
  - `carry_chain_penalty`: `chain_stops` = liczba pozycji bagu z INNĄ restauracją niż nowy pickup (strip+lower); applied gdy `stops≥1 AND eta_pickup > 15 min`; `penalty = −1,5 × eta` (p50 obserwowane −24 pkt).
  - `carry_chain_hard_reject`: `stops≥2 AND restauracja ∈ CARRY_RISK_LIST (tylko „kebab król", substring) AND 17–21 Warsaw` → verdict kandydata MAYBE→NO (`dispatch_pipeline.py:3968`). **Bez progu ETA** — strzela też na kurierze 3 min od KK.
  - Obie pod JEDNĄ flagą; przy OFF nic nie jest liczone → serializowane pola zawsze 0/false (komentarz w kodzie l.3966-3967 to potwierdza).

## 2. Dane i metoda

- Pliki: `logs/shadow_decisions.jsonl` + `.1`. **Realne okno: 02.06 07:11 → 11.06 11:20 UTC (~9,2 dnia)** — starsza rotacja (28.05–01.06) nie istnieje na dysku; pełnych 14d od 28.05 nie da się pokryć dostępnymi logami.
- 2254 rekordy (0 błędów parsowania): **1930 PROPOSE** + 324 KOORD; kandydatów 8800 (1930 best + 6870 alternatives; pola carry_chain obecne w 1929 best i 6870 alt).
- Rekonstrukcja: skrypt `/tmp/carry_chain_analysis.py` (+ `/tmp/carry_chain_results.json`, `/tmp/carry_pen_cases.jsonl`) — semantyka 1:1 z common.py, wejścia: `bag_context[].restaurant`, top-level `restaurant`, `carry_chain_drive_min_used` (serializowany bezwarunkowo), ts→godzina Warsaw. `bag_context` nigdy nie był ucięty względem `bag_size_before` (0 przypadków).

## 3. Pokrycie i rozkłady (zadanie 1)

| Metryka | best (n=1930) | kandydaci (n=8800) |
|---|---|---|
| zalogowane `carry_chain_applied=true` / `penalty≠0` / `hard_reject=true` | **0 / 0 / 0** | **0 / 0 / 0** |
| rekonstrukcja: chain stops≥1 | 1423 (**73,7%**) | 5533 (62,9%) |
| rekonstrukcja: penalty applied (stops≥1 i eta>15) | **19 (0,98%)** | 49 (0,56%) |
| \|penalty\| p50 / p90 / max | 24,0 / 25,3 / **31,8** pkt | 24,5 / 28,2 / 34,5 pkt |

Histogram stops (best): 0→507, 1→682, 2→513, 3→178, 4→47, 5→3. Carry chain jako zjawisko jest wszechobecny (73,7%), ale próg ETA 15 min tnie penalty do ~2 propozycji/dzień. Godziny przypadków z penalty (Warsaw): 11→1, 12→2, 13→3, **14→6**, 15→3, 16→2, 17→1, 18→1 — pik pokrywa się z HIGH_RISK bucket 14–17, NIE z dinner.

## 4. Flip-analiza (zadanie 2)

- **Czysty efekt kary** (argmax(score) vs argmax(score+penalty), izoluje penalty od warstwy selekcji): **2/1728 = 0,12%** decyzji z ≥2 kandydatami. Oba flipy 05.06 ~17:23/17:25 Warsaw, oba sensowne: Bartek O. (chain stops=1, eta 18 min, pen −27) → Jakub OL (bez kary); jeden z nich to **Kebab Król w dinner** (oid 478691) — kara zadziałałaby dokładnie na pierwotnym problemie.
- **Perspektywa realnych propozycji**: wśród 19 PROPOZYCJI, gdzie faktyczny best miał penalty, w **15/19 (79%)** widoczny kandydat wygrywałby po karze (~1,6/dzień). Zastrzeżenie: w 13/15 alternatywa miała wyższy surowy score JUŻ PRZED karą (best wybrany post-scoringowo: demote no_gps-empty / veto / OBJ) — tam kara tylko pogłębia rozjazd i finalny wybór nadal rozstrzyga warstwa selekcji. Realny efekt flipa ∈ [2 … 15] zmian / 9,2 dnia; bliżej dołu zakresu.
- Na czym: bag_size besta 1–2 (flipy argmax: bag=1), slot 13–17 Warsaw, restauracje rozproszone (bez koncentracji KK).

## 5. Czy KK exclusion załatwiło problem? (zadanie 3)

- **Udział KK-dinner wśród przypadków soft-penalty≠0: 0/19 = 0%.** KK zniknął z rozkładu kary — w obserwowanych chainach pod KK kurierzy mieli etę 3,3–10 min (< próg 15). 
- Exclusion (ON) ≠ rozwiązanie problemu wyboru kuriera: działa wyłącznie w `auto_proximity_classifier.py:616` (KK dinner → ROUTE_ALERT, human gate na ścieżce autonomii). Best nadal jest wybierany bez kary carry i propozycja idzie do koordynatora.
- Kara generyczna ma wartość poza KK — **top 5 restauracji wg sumy \|penalty\| (best, 9,2 dnia):**

| Restauracja | Σ\|pen\| | przypadki |
|---|---|---|
| Trzy Po Trzy Mickiewicza | 73,4 | 3 |
| Restauracja Kumar's | 47,1 | 2 |
| Raj | 46,1 | 2 |
| Galeria Biała 500 Stopni | 31,8 | 1 |
| Miejska Miska | 26,5 | 1 |

## 6. Hard-reject — symulacja i ręczny przegląd (zadania 1/4)

6 przypadków best-HR (wszystkie = KK dinner, stops=2, **eta 3,3–10 min** — kurier tuż obok KK; wzorzec forensic dotyczył carry 15–30 min):

| oid | Warsaw | best (kurier) | stops | eta | pool | skutek HR |
|---|---|---|---|---|---|---|
| 477905 | 02.06 17:01 | Łukasz W | 2 | 4,4 | 1 | **UTRATA — jedyny kandydat → BRAK KANDYDATÓW/KOORD** |
| 478196 | 03.06 19:57 | Patryk | 2 | 4,5 | 2 | **UTRATA — obaj kandydaci stops≥2** |
| 478691 | 05.06 17:25 | Jakub OL | 2 | 7,5 | 2 | zamiana na Bartka O. (sam z chain stops=1, eta 18 — wątpliwa) |
| 478676 | 05.06 17:48 | Dariusz M | 2 | 6,1 | 5 | **UTRATA — WSZYSCY 5 kandydatów stops≥2** |
| 479324 | 08.06 18:33 | Patryk | 2 | 10,0 | 5 | zamiana na czystego (Mateusz O, bag=0, score 67) — OK |
| 479375 | 08.06 20:10 | Andrei K | 2 | 3,3 | 6 | zamiana na czystego (Mateusz O, bag=0, score 130) — OK |

**3/6 = utrata propozycji; 3/22 KK-dinner-PROPOSE (13,6%) → nowe cisze/KOORD. Per dyrektywa ALWAYS-PROPOSE hard-reject w tej formie NIE kwalifikuje się do flipu.** (Soft-penalty tego ryzyka nie ma: nie dotyka feasibility — w 4/19 przypadków best z karą był jedynym kandydatem i propozycja by została.)

### Ręczny przegląd — top 10 przypadków soft-penalty (pełna lista 19: `/tmp/carry_pen_cases.jsonl`)

| oid | Warsaw | restauracja | kurier (best) | bag (obce) | stops | eta | pen | HR? | flip po karze → |
|---|---|---|---|---|---|---|---|---|---|
| 479312 | 08.06 16:50 | 500 Stopni | Piotr Zaw | doner kebab | 1 | 21,2 | −31,8 | nie | Mateusz O (alt już wyżej score) |
| 479314 | 08.06 16:59 | Miejska Miska | Piotr Zaw | doner, rany julek | 2 | 17,7 | −26,5 | nie | Mateusz O (jw.) |
| 478334 | 04.06 15:15 | Paradiso | Bartek O. | rukola, toriko | 2 | 16,9 | −25,3 | nie | brak alternatyw — propozycja zostaje |
| 479451 | 09.06 14:21 | Hacienda Pizza | Jakub OL | piwo kaczka sushi | 1 | 16,7 | −25,0 | nie | Mateusz O (jw.) |
| 478064×3 | 03.06 14:21–14:31 | Trzy Po Trzy | Tomasz Ch | doner kebab | 1 | 16,3 | −24,5 | nie | Szymon P — **3 ticki z rzędu ta sama propozycja z carry; wzorzec forensic 1:1** |
| 479440 | 09.06 13:44 | Rukola Kaczorowskiego | Bartosz Ch | kumar's | 1 | 16,3 | −24,5 | nie | Mateusz O (jw.) |
| 478333 | 04.06 15:15 | Chicago Pizza | Bartek O. | rukola, toriko | 2 | 16,1 | −24,2 | nie | Gabriel (margin 43) |
| 479432 | 09.06 13:16 | Arsenal Panteon | Michał K. | kumar's ×3 | 3 | 16,0 | −24,0 | nie | Mateusz O (jw.) |
| 479419 | 09.06 12:29 | Kumar's | Bartosz Ch | sweet fit & eat | 1 | 15,7 | −23,5 | nie | Michał K. (margin 54) |
| 478710 | 05.06 18:01 | Epic Pizza | Bartek O. | kumar's ×2 | 2 | 15,6 | −23,4 | nie | brak alternatyw — zostaje |

Ocena jakościowa: każdy przypadek to literalnie mechanizm z forensic KK (jedzenie innej restauracji czeka w torbie 15–21 min dojazdu + dalsza trasa). Flipy zwycięzcy są sensowne kierunkowo; tam gdzie alternatyw brak, soft-penalty niczego nie psuje.

## 7. Rekomendacja wykonawcza (dla sesji silnika / Adriana — NIE wykonano w tej analizie)

1. **Flip soft-only bez zmiany kodu:** w override.conf jednostek liczących (dispatch-shadow + dispatch-czasowka + dispatch-plan-recheck — spójność cross-proces, lekcja Z-04/Etap 4) ustawić `ENABLE_CARRY_CHAIN_PENALTY=1` **oraz** `CARRY_CHAIN_HARD_REJECT_STOPS=999` (stała env-overridable — neutralizuje hard-reject bez dotykania kodu) + restart; zaktualizować flags.json kosmetycznie (true) dla spójności dokumentacyjnej.
2. **Obserwacja 7d po flipie:** pola `carry_chain_*` zaczną się realnie wypełniać — monitorować częstość applied (oczekiwane ~2/dzień na best), brak wzrostu KOORD, R6 breach na restauracjach z §5.
3. **Tech-debt (przy najbliższym sprincie silnika):** (a) wynieść obliczenie carry PRZED gate flagi — telemetria zawsze, flaga gate'uje tylko wejście do score/verdict (wzorzec `ENABLE_REPO_COST_LIVE`); (b) rozdzielić flagi `ENABLE_CARRY_CHAIN_PENALTY` (soft) i `ENABLE_CARRY_CHAIN_HARD_REJECT` (default OFF; ewentualny redesign HR z progiem ETA i fallbackiem „najmniej obciążony kandydat zamiast ciszy"); (c) dopisać do `ETAP4_DECISION_FLAGS` (hot-reload flags.json cross-proces).
4. Hard-reject NIE flipować w obecnej formie (dowody §6). Jeśli problem KK-dinner wróci w realnym shadow po flipie soft — wrócić z redesignem HR, nie z flipem.

---
*Analiza: rekonstrukcja offline shadow 02–11.06 (9,2 d, 1930 PROPOSE, 8800 kandydatów); skrypty: `/tmp/carry_chain_analysis.py`, wyniki: `/tmp/carry_chain_results.json`, przypadki: `/tmp/carry_pen_cases.jsonl`. Zero zmian w flags/usługach/git.*
