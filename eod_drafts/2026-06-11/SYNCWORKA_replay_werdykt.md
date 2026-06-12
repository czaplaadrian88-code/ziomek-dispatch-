# SP-B2-SYNCWORKA — replay kontrfaktyczny kary za spread gotowości worka (WERDYKT)

**Data analizy:** 2026-06-11 · **Analityk:** CC (read-only na logach)
**Flaga badana:** `ENABLE_BUNDLE_SYNC_SPREAD` (obecnie **OFF — shadow**)
**Mechanizm:** kara gradientowa za spread kotwic czasowych worka — węzły (7→0, 10→−30, 15→−80, ≥20→−150 pkt, interpolacja liniowa) + przy spreadzie >10 min zerowanie dodatnich `bundle_bonus` i `v319h_bug2_continuation_bonus`. Dowód H1 (mining 2e): spread ≤5 min → breach multi-rest 6,1% (jak same-rest); >10 min → worki niosą 50% wszystkich breachy.

---

## 1. Dane i okno

- Źródło: `logs/shadow_decisions.jsonl` + `.1` (helper `tools/_rotated_logs.py`; pliku `.2.gz` brak).
- Zadane okno: od 2026-05-28 (14 dni). **Realnie dostępne: 2026-06-02 07:27 UTC → 2026-06-11 11:29 UTC (~9,2 dnia)** — logrotate (daily+100M, copytruncate) uciął wszystko sprzed 06-02. Mieści się w widełkach 7-14 dni.
- Rekordy: `verdict=="PROPOSE"`, kandydaci = `best` + `alternatives` (top-16 feasible ≈ 100% puli).

## 2. Metodologia i walidacja

**Spread per kandydat z workiem** (`bag_context` niepusty):
- kotwica nowego zlecenia = ts decyzji + `time_to_pickup_ready_min` kandydata;
- kotwice worka = `bag_context[].czas_kuriera_warsaw` (ISO Warsaw → UTC; fallback `plan.pickup_at[oid]`);
- spread = max−min [min]; <2 kotwic → kandydat pominięty (delta 0; w praktyce 0 takich przypadków).

**Score kontrfaktyczny:** `score' = score + kara(spread) − (spread>10 ? max(0,bundle_bonus)+max(0,continuation) : 0)`; kandydat bez worka — bez zmian.

**Selekcja zwycięzcy — kluczowe odkrycie:** logowany `best` w 58% decyzji NIE jest argmaxem po samym `score`, bo live ranking to klucz złożony (Opcja B score-first, flagi ON): `(tier-2 na koniec, bucket pos_source informed/other/blind, −(score − kara_za_późny_odbiór), stabilny tie-break)`. Replay odtwarza ten klucz w całości (tier z `late_pickup_committed_breach`/`new_pickup_needs_extension`/`pickup_extension_redirect`, bucket z `pos_source`+bag size, kara late = min(60, 1,5×max(0, late−5))). **Walidacja: rekonstrukcja odtwarza logowanego zwycięzcę w 1921/1921 decyzji (100%)** — flip liczony dopiero po podmianie `score→score'` w TYM samym kluczu. Arytmetyka spreadu zweryfikowana ręcznie na rekordzie #477974 (kotwice 19:30/19:33/19:47,5 → 17,5 min ✓; 19:46/19:46/19:47,5 → 1,5 min ✓).

**Ograniczenia (uczciwie):** (a) dla zleceń worka już ODEBRANYCH live liczy `picked_up_at`, replay ma tylko `czas_kuriera_warsaw` (zwykle bliskie, ale przybliżenie); (b) kotwica nowego clampowana do „teraz" (`max(0, ready−now)`) — live używa surowej deklaracji, więc replay jest minimalnie konserwatywny; (c) 11 decyzji best-effort (feasibility NO, inny klucz sortu) wykluczono.

## 3. Liczby

| Metryka | Wartość |
|---|---|
| Decyzje PROPOSE w oknie (analizowane) | **1921** (+11 best-effort poza analizą) |
| …z ≥1 kandydatem z workiem | **1738 (90,5%)** |
| …gdzie zwycięzca (best) ma worek | 1450 (75,5%) |
| Kandydatów łącznie / z workiem | 8746 / 5554 (63,5%) |
| Best ukarany, ale **dalej wygrywa** | 1152 decyzji |
| Mediana delty besta gdy karany | **−150 pkt** (płaski ogon) |
| **FLIPY zwycięzcy** | **227 = 11,8% wszystkich PROPOSE** (13,1% decyzji z workiem) |

**Rozkład spreadów (kandydaci z workiem, n=5554):**

| ≤5 | 5-10 | 10-15 | 15-20 | >20 |
|---|---|---|---|---|
| 113 (2,0%) | 199 (3,6%) | 295 (5,3%) | 432 (7,8%) | **4515 (81,3%)** |

**Rozkład spreadów (best z workiem, n=1450):** ≤5: 46 · 5-10: 83 · 10-15: 109 · 15-20: 131 · **>20: 1081 (74,6%)**.

→ Worki w produkcji są chronicznie rozjechane: **4/5 kandydatów z workiem ma spread >20 min** — strefa gradientowa (7-20) pokrywa ledwie ~13% przypadków, reszta dostaje płaskie −150.

## 4. Dokąd uciekają flipy

| Metryka | Stary zwycięzca | Nowy zwycięzca |
|---|---|---|
| **Mediana spreadu** | **29,3 min** | **13,8 min** (z workiem); 10 flipów → kurier BEZ worka |
| Mediana km do odbioru | 2,60 | 2,88 (bez pogorszenia dojazdu) |
| Mediana r6_max_bag_time | 22,9 | 26,5 (+3,6 min — lekkie dociążenie) |
| Tiery late-pickup | t0=139 / t1=82 / t2=6 | t0=102 / **t1=119** / t2=6 (**+37 przedłużeń odbioru**) |

- **193/227 (85%)** flipów → zwycięzca z mniejszym lub zerowym spreadem; **173 (76%)** poprawa ≥5 min lub kurier wolny; mediana poprawy spreadu **13,0 min**.
- **34 (15%)** flipy → spread RÓWNY LUB GORSZY; **55 (24%)** to przetasowania w płaskim ogonie (oba worki >20 min) — tam kara nie różnicuje (23 min i 51 min dostają to samo −150), a o flipie decydują wtórne składniki (zerowanie bonusów, tier). To główna wada obecnej kalibracji.
- Mediana poświęconego score: ledwie 10,8 pkt. Flipy rozłożone równo po dniach (10-38/d), bez anomalii.

## 5. Ręczny przegląd 20 flipów (próbka równomierna po oknie)

| # | ts | oid | restauracja | stary best (cid · spread · score) | nowy best (cid · spread · score') | ocena |
|---|---|---|---|---|---|---|
| 1 | 06-02 10:02 | 477776 | Dr Tusz | Piotr Zaw (470) · 25,1 · 14,8 | **wolny** Bartek O. (123) · — · 11,7 | **Sensowne** — wolny kurier 1,75 km obok zamiast doklejki do worka rozjechanego 25 min. |
| 2 | 06-02 19:32 | 477974 | Rukola Sienkiewicza | Bartek O. (123) · 17,5 · −2,3 | Szymon P (515) · 1,5 · −22,1 | **Sensowne (wzorcowe)** — worek idealnie zsynchronizowany (1,5 min), bliżej (1,1 vs 3,1 km), kosztem ~10 min przedłużenia odbioru. |
| 3 | 06-03 15:12 | 478101 | Grill Kebab | Piotr Zaw (470) · 17,3 · 12,7 | Sylwia L (441) · 15,3 · −91,1 | **Wątpliwe** — zysk 2 min spreadu, dalej o 1,2 km i późniejszy odbiór; flip napędza zerowanie bonusu 30 pkt, nie realna poprawa. |
| 4 | 06-03 17:27 | 478140 | Pani Pierożek | Michał K. (393) · 23,3 · 4,5 | Dariusz M (509) · **51,0** · −166,6 | **Złe** — płaski ogon −150 nie różnicuje 23 vs 51 min; flip na worek rozjechany BARDZIEJ o 28 min. |
| 5 | 06-04 12:15 | 478253 | Rukola Sienkiewicza | Dariusz M (509) · 21,7 · 69,6 (0,5 km!) | Michał Rom (520) · 9,7 · −33,2 | **Kierunkowo sensowne** — świeższy worek i r6max 15,5 vs 29,0, ale drogo: rezygnacja z kuriera 500 m od restauracji na rzecz 4,9 km. |
| 6 | 06-04 14:15 | 478299 | Retrospekcja | Adrian R (400) · 58,0 · −19,7 (r6max 37!) | Grzegorz W (289) · 17,1 · −173,2 (8,9 km, odbiór +27 min) | **Graniczne** — wybór „mniej złego" w puli bez dobrych opcji; stary już łamał 35 min. |
| 7 | 06-04 18:45 | 478455 | Chicago Pizza | Tomasz Ch (514) · 56,8 · −6,3 | Michał Rom (520) · **62,8** · −158,2 | **Neutralne** — spread się nie poprawia (artefakt ogona), ale nowy i tak lepszy operacyjnie (1,5 vs 4 km; r6max 23 vs 37). |
| 8 | 06-04 21:05 | 478507 | Grill Kebab | Dariusz M (509) · 14,9 · 48,1 (1,1 km) | Jakub OL (370) · 11,9 · −95,1 | **Wątpliwe** — zysk 3 min, a mechanizm skasował aż 106,8 pkt bonusów bundlowych; wolny Gabriel (s'=118,8) przegrał tylko bucketem no_gps. |
| 9 | 06-05 15:05 | 478637 | Rany Julek | Mateusz O (413) · 26,4 · 8,2 | Bartek O. (123) · **41,4** · −156,4 | **Złe** — znów ogon: flip na worek rozjechany bardziej o 15 min (zerowanie 25,8 + tier zdecydowały). |
| 10 | 06-06 14:38 | 478810 | Paradiso | Grzegorz W (289) · 30,9 · 11,2 | Andrei K (484) · 25,9 · −144,1 | **Neutralne** — oba >20, zysk kosmetyczny 5 min, km/r6 porównywalne. |
| 11 | 06-06 17:14 | 478854 | Chinatown Bistro | Grzegorz W (289) · 21,0 · 5,8 | Bartosz Ch (530) · 5,0 · −69,5 (**km 0,0**) | **Sensowne (wzorcowe)** — kurier stoi pod restauracją z workiem zsynchronizowanym do 5 min. |
| 12 | 06-07 13:42 | 478967 | Pizza Dealer | Mateusz Bro (409) · 19,0 · 4,1 | Bartosz Kl (526) · 2,5 · −81,8 (0,9 km) | **Sensowne** — blisko i w pełni zsynchronizowany. |
| 13 | 06-07 17:34 | 479096 | Rukola Sienkiewicza | Mateusz Bro (409) · 9,7 · 11,1 (kara −27) | Gabriel J (503) · 1,7 · 0,9 | **Sensowne** — strefa gradientowa działa subtelnie: flip tylko dlatego, że gap score'u był mniejszy od małej kary. |
| 14 | 06-07 18:22 | 479121 | Enklawa | Mateusz Bro (409) · 32,5 · 5,8 (7,0 km) | Gabriel J (503) · 18,5 · −127,7 (1,4 km) | **Sensowne** — bliżej, świeższy worek, r6max 13,4 vs 34,6. |
| 15 | 06-08 14:48 | 479284 | Sushi Rany Julek | Jakub OL (370) · 9,5 · −7,8 | Adrian R (400) · 3,5 · −11,9 | **Sensowne** — drobna korekta na zsynchronizowanego i bliższego, koszt 4 pkt. |
| 16 | 06-08 18:29 | 479342 | Pizza Dealer | Andrei K (484) · 25,2 · 3,2 (3,4 km) | Bartosz Ch (530) · 5,2 · −57,4 (0,9 km) | **Sensowne** — bliski i zsynchronizowany wygrywa z rozjechanym. |
| 17 | 06-09 12:03 | 479411 | Pani Pierożek | Jakub OL (370) · 32,6 · 5,7 | Adrian R (400) · 17,6 · −141,7 | **Sensowne** — poprawa o 15 min, bliżej, r6max 14 vs 20 („mniej zły", ale wyraźnie). |
| 18 | 06-10 11:54 | 479609 | Raj | Michał K. (393) · 25,5 · 79,9 | Adrian Cit (457) · 7,5 · −1,4 (**0,4 km**) | **Sensowne** — kurier 400 m od restauracji ze zsynchronizowanym workiem bije wysoki score z workiem rozjechanym 25 min. |
| 19 | 06-10 16:42 | 479700 | Rukola Kaczorowskiego | Tomasz Ch (514) · 20,5 · 12,9 | Rafał Jankowski (529) · 5,5 · −112,1 (odbiór +22,5 min, r6max 34,8) | **Wątpliwe** — sync okupiony dużym przedłużeniem odbioru i krawędzią limitu 35 min. |
| 20 | 06-11 11:29 | 479803 | Rany Julek | Piotr Zawadzki (470) · 15,3 · 65,5 | **wolny** Tomasz Chodziutko (514) · — · 46,6 | **Sensowne** — wolny kurier przejmuje zamiast doklejki do worka rozjechanego 15 min. |

**Bilans próbki: 12× sensowne · 5× wątpliwe/złe · 3× neutralne/graniczne.** Wszystkie przypadki złe/wątpliwe mają wspólny mechanizm: **płaski ogon −150 powyżej 20 min** — gdy stary i nowy worek są oba >20 min, kara ich nie różnicuje i flip rozstrzygają wtórne składniki (zerowanie bonusów, tier), czasem NA GORSZY spread (#4: 23→51; #9: 26→41).

## 6. WERDYKT DLA ADRIANA

Ta flaga każe Ziomkowi unikać doklejania nowego zlecenia do worka, w którym jedzenie „rozjeżdża się w czasie" — im większy rozstrzał gotowości, tym mocniejsza kara w punktacji (nic nie jest twardo odrzucane, propozycja zawsze wychodzi). Na 9 dniach decyzji zmieniłaby zwycięzcę w **227 z 1921 propozycji (11,8%)** — w **85% flipów** nowy kurier ma worek wyraźnie lepiej zsynchronizowany (mediana spada z ~29 do ~14 min) albo jest całkiem wolny, a dojazd do restauracji praktycznie się nie pogarsza. Flipy wyglądają sensownie w ~3/4 przejrzanych ręcznie przypadków; problematyczna jest mniejszość (~15-24%), gdzie OBA worki są rozjechane powyżej 20 min — tam kara jest płaska (−150 dla każdego) i potrafi przerzucić zlecenie na worek jeszcze gorszy. **Rekomendacja: TAK — flipować**, bo zysk netto jest wyraźny, a ryzyko ogranicza się do segmentu, w którym i tak nie ma dobrych opcji; szybki follow-up po flipie: przedłużyć gradient powyżej 20 min (np. węzły 30→−200, 45→−250 w `SYNC_SPREAD_KNOTS`), żeby ogon też różnicował. Po flipie obserwować **2 metryki**: (1) udział worków multi-rest w naruszeniach 35-min/R6 (per H1 ma spaść z ~50%; licznik z shadow/SLA-trackera), (2) wolumen propozycji z przedłużeniem odbioru (`pickup_extension_redirect`, tier-1 — replay pokazuje wzrost z 82 do 119 w obrębie flipów) + odsetek nadpisań koordynatora na flipowanych propozycjach; eksplozja którejkolwiek = wrócić flagą OFF (rollback = sam przełącznik, bez restartu logiki).

---

*Warsztat: skrypty `/tmp/syncworka_replay2.py` (+v1), pełna lista flipów `/tmp/syncworka_flips_all2.json`, próbka `/tmp/syncworka_flips_sample2.json`. Walidacja selekcji: 100% odtworzenia live zwycięzcy (1921/1921). Żadne flagi/serwisy nie były dotykane.*
