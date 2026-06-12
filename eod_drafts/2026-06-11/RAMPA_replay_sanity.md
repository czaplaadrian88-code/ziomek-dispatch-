# SP-B2-RAMPA — replay sanity 7 dni (2026-06-04 → 2026-06-11)

**Data analizy:** 2026-06-11 ~11:30 UTC · **Autor:** agent analityczny (read-only)
**Zakres:** kontrfaktyczny replay nowej logiki rampy nowych kurierów (`_v325_new_courier_penalty`, flaga `ENABLE_NEW_COURIER_RAMP=true` w flags.json) na logach decyzji z ostatnich 7 dni.
**Ważne:** w chwili analizy działający proces `dispatch-shadow` (start 06:03 UTC) NIE ma jeszcze załadowanego kodu rampy (plik zmieniony 10:56 UTC, 0 linii `SP-B2-RAMPA` w journalu) → **cały log okna = stara logika**, kontrfakt czysty (0 rekordów do odsiania).

---

## 1. Dane i metodologia

| Element | Szczegół |
|---|---|
| Log decyzji | `/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl` (+ `.1` przez helper `tools/_rotated_logs.iter_jsonl_records`). ⚠ Realna ścieżka inna niż w zleceniu (`dispatch_state/` nie istnieje — log mieszka w `scripts/logs/`). |
| Log akcji | `/root/.openclaw/workspace/dispatch_state/learning_log.jsonl` (+ `.1`) — kontekst akcji koordynatora. |
| Tiery | `courier_tiers.json` — tier `new`: 492, 522-527, 529, 530, 531. |
| Liczniki dostaw | `courier_reliability.json` → `couriers.<cid>.n_delivered` (brak wpisu = 0). **Tryb A (litera zadania):** dzisiejszy plik. **Tryb B (sensitivity):** licznik odtworzony na dzień decyzji z korpusu `backfill_decisions_outcomes_v1.jsonl` (plik regenerowany daily 04:30 — liczniki w oknie były NIŻSZE niż dziś). |
| Identyfikacja kandydata 'new' | obecność `v325_new_courier_penalty` w serializowanym kandydacie (ground-truth zadziałania starej kary) LUB `dwell_tier`/`v326_speed_tier_used == "new"` (tier w chwili decyzji — odporne na re-tiering). |
| Kontrfakt score (wg zadania) | `raw = serialized_score − stara_kara`; kurs **rampowy** = `km_to_pickup ≤ 2.5 ∧ bag_size_before == 0 ∧ godz. Warsaw z ts ∉ [14,17)` → `score' = raw − 20`; inaczej `score' = −1e9`. Dla `deliveries ≥ 30` → stary gradient bez zmian (post-rampa). |
| Materializacja pełno-warstwowa | replika finalnego sortu: tier R-LATE-PICKUP (Opcja B) → bucket V3.16 (informed 0 / other 1 / blind+empty albo pre_shift 2) → `score − kara_za_późny_odbiór` → bramka werdyktu `MIN_PROPOSE_SCORE = −100`. **Walidacja: replika odtworzyła realnego zwycięzcę (best) w 49/49 (tryb A) i 130/130 (tryb B) rekordów in-ramp — 100 % wierności.** |

Uwagi metodyczne: (1) dla starych HARD-SKIP (bag≥2) score był NADPISANY −1e9, nie dodany — `raw` nieodtwarzalny, ale nowa logika daje tam również −1e9, więc wynik identyczny; (2) `alternatives` = serializowana czołówka feasible — wystarcza, bo flip rozstrzyga się w czołówce; (3) replikujemy tylko bramkę werdyktu `all_candidates_low_score`, pozostałe gate'y bez zmian.

---

## 2. Liczby główne — tryb A (dzisiejsze liczniki, litera zadania)

| Metryka | Wartość |
|---|---|
| Decyzje 7d (rekordy shadow) | **1 757** (1 598 unikalnych zleceń) |
| …z kandydatem tier='new' | **711** (40 %) — zdominowane przez post-rampowych 530/531/529 (n_delivered 215/120/101 ≥ 30 → rampa ich NIE zmienia) |
| …z kandydatem **in-ramp** (n_delivered < 30) | **49** — wszystkie = **cid 526 Bartosz Kl** (20 dostaw), wszystkie z niedzieli 07.06 |
| Kursy **rampowe** vs **poza rampą** (in-ramp) | **1 vs 48** (100 % blokad = `bag_niepusty`; 526 prawie zawsze rozpatrywany z 1-2 zleceniami w torbie) |
| **Flipy zwycięzcy — score-only (definicja zadania)** | **10** — wszystkie kierunek **new-WYPADA** (526 był best → spada); **0 × new-WYGRYWA** |
| Flipy — materializacja pełno-warstwowa | **0 realnych zmian kuriera na propozycji**; **6 × eskalacja PROPOSE→KOORD** (526 zostaje top-1 przez bucket informed, ale −1e9 < −100 ucina propozycję); 1 × kosmetyczna zamiana −1e9↔−1e9 wewnątrz KOORD; pozostałe 42 bez zmiany |
| Nowe ekspozycje dzięki rampie (malus −20 zamiast −30/−50) | **1** kurs rampowy (478916) — 526 i tak wygrywał po staremu; **0** przypadków „new wygrywa dzięki rampie" |

**Dlaczego score-flip ≠ realny flip:** klucz finalnego sortu (Opcja B z 31.05) ma bucket V3.16 **NAD** score. W 6/10 flipów rywale byli `pre_shift`/`no_gps`+pusta torba (bucket 2), a 526 informed (bucket 0) — z −1e9 nadal jest top-1, zmienia się tylko werdykt (PROPOSE→KOORD przez `MIN_PROPOSE_SCORE=−100`). W pozostałych 4 werdykt już był KOORD (bag=2 → stary −1e9) — zero zmiany.

## 3. Sensitivity — tryb B (liczniki na dzień decyzji)

Korpus backfill zaczyna się ~01.06 i wszyscy „nowi" przekraczali próg 30 dostaw W TRAKCIE okna (526: 0→20; 531: 22→120; 529: 21→101; 530: 0→215 ale tier 'new' dostał 06.06 przy liczniku już 65). Wierna symulacja „rampa włączona w oknie":

| Metryka | Wartość |
|---|---|
| Rekordy z kandydatem in-ramp | **130** (526: 49; **531 Piotr Ku: 81** — dni 06-06…06-08 przy liczniku 22) |
| Rampowy vs poza rampą | **2 vs 128** (blokady: bag_niepusty 114, dystans>2.5 km 13, slot 14-17 1) |
| Flipy score-only | **12 × new-wypada** (10×526 + 2×531), **0 × new-wygrywa** (2 „wygrywa" to kosmetyka −1e9↔−1e9 w KOORD) |
| Pełno-warstwowo | **7 × eskalacja PROPOSE→KOORD** (6×526 + 1×531 oid 479297), reszta bez zmiany operacyjnej |

Wniosek z sensitivity: nawet przy najszerszej interpretacji liczników wzorzec identyczny — rampa nie przestawia zleceń na innych kurierów, tylko zamienia część propozycji „nowy z torbą" na eskalacje KOORD.

## 4. Rozkład per kurier (tier 'new')

| cid | Kurier | n_delivered (dziś) | Wystąpienia 7d jako 'new' | In-ramp (A / B) | Rampowy / poza (B) | Flipy score (A / B) |
|---|---|---|---|---|---|---|
| 526 | Bartosz Kl | 20 | 49 | 49 / 49 | 1 / 48 (bag 48) | 10 wypada / 10 wypada |
| 531 | Piotr Ku | 120 | 272 | 0 / 81 | 1 / 80 (bag 66, dystans 13, slot 1) | 0 / 2 wypada |
| 530 | Bartosz Ch | 215 | 590 | 0 / 0 (tier 'new' nadany 06.06 przy liczniku 65) | — | 0 |
| 529 | Rafał Ja | 101 | 80 | 0 / 0 (wystąpienia dopiero od 06-09, licznik 46+) | — | 0 |
| 492 | Jakub Wysocki | 73 | 0 | — | — | — |
| 522 | Szymon Sa | 0 | 0 | — | — | — |
| 523/524/525/527 | Marcin By / Dawid Cha / Dawid Kr / Piotr Wr | 0 | 0 | — | — | — |

Kurierzy z licznikiem 0 (522-525, 527) **ani razu nie pojawili się w puli feasible** w oknie (brak zmian w grafiku) — bonus rampowy −20 nie miał na kim zadziałać.

Kontekst learning_log 7d: akcje koordynatora = PANEL_OVERRIDE 996, TIMEOUT_SUPERSEDED 1 430, PANEL_AGREE 17, ASSIGN_DIRECT 19; propozycje z `proposed_tier='new'` zaakceptowane wprost: 2 (obie cid 529).

## 5. Ręczny przegląd 10 flipów (tryb A) + bonusy

Słownik: „score-flip" = nowy zwycięzca wg czystych score'ów (definicja zadania); „materializacja" = wynik repliki pełnego sortu + werdyktu. „Człowiek" = finalne przypisanie z korpusu outcomes.

| # | oid | Restauracja → adres | h (Wwa) | 526: km / bag | Stary wybór (score) | Nowy wg score | Materializacja | Człowiek | Ocena |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 478917 | Chicago Pizza → Grunwaldzka 18 | 10:31 | 1.35 / 1 | 526 (−29, kara −50) | 520 Michał Rom (54, pre_shift) | PROPOSE→KOORD (526 zostaje top przez informed-bucket) | **dał 526, dowiózł** | **WĄTPLIWE** — krótka dorzutka (1.3 km) do 1 paczki; człowiek ją zaakceptował, rampa ucina i zostawia KOORD w martwą niedzielę rano. |
| 2 | 478918 | Grill Kebab → Popiełuszki 103a/4 | 11:09 | 1.95 / 1 | 526 (−59) | 520 Michał Rom (69, pre_shift) | PROPOSE→KOORD | **dał 526, dowiózł** | **WĄTPLIWE** — jw. (km<2, bag=1, człowiek się zgadzał z nowym). |
| 3 | 478919 | Chicago Pizza → Dziesięciny 29b | 11:11 | 1.23 / 2 | 526 (−1e9, HARD SKIP bag≥2) | 520 Michał Rom (94) | KOORD był i zostaje — **zero zmiany** | brak w korpusie | SENSOWNE — stara i nowa logika zgodne (bag≥2 nie dla nowego); „flip" czysto arytmetyczny. |
| 4 | 478920 | Sweet Fit & Eat → Kraszewskiego 1 | 11:20 | 6.35 / 2 | 526 (−1e9) | 484 Andrei K (32) | KOORD bez zmiany | brak | SENSOWNE — jw. |
| 5 | 478936 | Ogniomistrz → Grota-Roweckiego 57 | 12:25 | 5.12 / 1 | 526 (−64) | 413 Mateusz O (30) | PROPOSE→KOORD | **OVERRIDE → 484 Andrei** | SENSOWNE — 5 km dojazdu nowego z paczką = profil ryzyka H13; człowiek też nadpisał. |
| 6 | 478937 | Rany Julek → Piastowska 13/4 | 12:27 | 5.65 / 1 | 526 (−31) | 413 Mateusz O (47) | PROPOSE→KOORD | **człowiek dał 520 Rom** | SENSOWNE — jw., zgodne z decyzją człowieka. |
| 7 | 478939 | Pruszynka → 42 Pułku Piechoty 135 | 12:31 | 7.49 / 2 | 526 (−1e9) | 123 Bartek O. (−0.3) | KOORD bez zmiany | brak | SENSOWNE — bag=2 + 7.5 km; obie logiki zgodne. |
| 8 | 478991 | Retrospekcja → Konstytucji 3 Maja 22/65 | 14:20 | 5.41 / 1 | 526 (−44) | 503 Gabriel J (51) | PROPOSE→KOORD | **OVERRIDE → 354 Filip P** | SENSOWNE — daleko + torba + wejście w slot 14-17; człowiek nadpisał. |
| 9 | 478992 | Retrospekcja → Kraszewskiego 28a/12 | 14:21 | 5.41 / 1 | 526 (−8) | 503 Gabriel J (51) | PROPOSE→KOORD | **OVERRIDE → 207 Marek** | SENSOWNE — jw. |
| 10 | 479000 | Rany Julek → Szyszkowa 4 | 14:26 | 4.71 / 1 | 526 (−20, best-effort) | 484 Andrei K (−41) | KOORD był (best_effort_r6_breach 38.4 min) i zostaje | brak | SENSOWNE — zlecenie i tak było w eskalacji R6; rampa niczego nie psuje. |
| B1 | 478916 | Sweet Fit & Eat → Piłsudskiego 7/27 | 10:19 | **1.63 / 0 — RAMPOWY** | 526 (67.3, kara −10) | 526 (57.3, malus −20) | PROPOSE bez zmiany | **dał 526, dowiózł** | SENSOWNE — jedyny kurs rampowy okna: nowy dostaje krótki, pusty kurs i nadal go wygrywa (cel Z-18 zachowany). |
| B2 | 479297 (tryb B, 531) | Grill Kebab → Fregatowa 9 | 15:46 (08.06) | 1.19 / 1 | 531 Piotr Ku (−60) | 413 Mateusz O (104) | PROPOSE→KOORD | **OVERRIDE → 508 Michał Li** | SENSOWNE — człowiek i tak nadpisał; rampa oszczędziłaby koordynatorowi tę propozycję. |
| B3 | 479383 (tryb B, 531) | Sioux → Żubrów 18A/30 | 20:46 (08.06) | 2.75 / 1 | 531 (−102) | 515 Szymon P (124) | KOORD był (all_low_score) i zostaje | brak | SENSOWNE — bez zmiany operacyjnej. |

**Bilans przeglądu:** 10/12 przypadków sensownych (w tym 4 całkiem neutralne — werdykt KOORD był i zostaje), 2/12 wątpliwe (478917, 478918 — krótkie dorzutki bag=1 km<2, które człowiek akceptował i 526 je dowiózł). Tam, gdzie znamy decyzję człowieka przy dalekiej dorzutce (5 przypadków: #5, #6, #8, #9, B2) — **człowiek za każdym razem nadpisał nowego kuriera, czyli rampa pokrywa się z praktyką koordynatora**.

## 6. Ryzyka / obserwacje kalibracyjne (nie wymagają działania przed obserwacją live)

1. **Sentinel −1e9 nie zawsze „sortuje na koniec".** Klucz Opcji B ma bucket V3.16 nad score — gdy nowy jest **jedynym informed** (typowa niedziela rano: reszta floty pre_shift/no_gps), zostaje top-1 z −1e9 i wpada w `MIN_PROPOSE_SCORE=−100` → **PROPOSE zamienia się w KOORD**, a nie w propozycję następnego kuriera. Na tych 7d: 6-7 takich eskalacji. To napięcie z dyrektywą „zawsze proponuj" ([[feedback-always-propose-defer-pickup]]) w godzinach, gdy nowy jest jedynym aktywnym.
2. **Krótkie dorzutki bag=1.** Jedyny realny rozjazd z człowiekiem: bag==1 przy km≤2 (2 przypadki, oba dowiezione przez nowego). Kandydat do przyszłej kalibracji progu (np. dopuszczenie bag==1 przy bardzo krótkim dojeździe) — po tygodniu obserwacji live.
3. **Semantyka licznika dostaw.** `n_delivered` pochodzi z korpusu backfill (start ~01.06, `min_history=5`) — dla świeżych kurierów ≈ lifetime, ale tier 'new' bywa nadawany z opóźnieniem (530 dostał tier przy liczniku 65 → rampa nigdy go nie obejmie). Rampa obejmie tylko kurierów dodanych do tiers szybko po starcie — zgodne z intencją, ale warto wiedzieć.
4. **Bonus −20 nieprzetestowany na realnym świeżaku.** Kurierzy z licznikiem 0 (522-525, 527) nie pojawili się w puli ani razu; jedyne 2 kursy rampowe okna i tak wygrywały po staremu. Pierwszy prawdziwy test ścieżki „nowy widzialny na krótkich kursach" nastąpi przy najbliższym debiucie kuriera.

---

## WERDYKT

Na danych 04-11.06 rampa wygląda **bezpiecznie i zachowawczo**: dotyka realnie 49 decyzji (1 kurier in-ramp, Bartosz Kl 526; w wariancie liczników as-of 130 decyzji i 2 kurierów), generuje **0 nowych ekspozycji** nowych kurierów i **0 zmian przypisania na żywych kursach** — jedyny realny efekt to zamiana 6-7 propozycji „nowy z niepustą torbą" na eskalacje KOORD, co w 5/5 weryfikowalnych dalekich przypadków pokrywa się z faktycznymi override'ami koordynatora. Przykłady są sensowne (profil cięcia = bag>0 i dojazdy 5+ km, dokładnie kohorta ryzyka H13); jedyne 2 wątpliwe to krótkie dorzutki bag=1 km<2, które człowiek akceptował — do obserwacji pod kątem przyszłego poluzowania, oraz zjawisko PROPOSE→KOORD gdy nowy jest jedynym informed kurierem (napięcie z „zawsze proponuj") — warte monitoringu w pierwszym tygodniu live.

---

## UPDATE 11.06 ~13:50 (sesja A, po replayu)

Wskazane wyżej napięcie **PROPOSE→KOORD gdy zablokowany nowy jest jedyną opcją**
zostało zaadresowane PRZED restartem live: commit `b53e87e` dodaje **solo-guard**
w `_v325_new_courier_penalty` — gdy po blokadach rampy CAŁA pula feasible spada
poniżej `MIN_PROPOSE_SCORE` (-100), najlepszy zablokowany wraca na
`pre_block + NEW_COURIER_RAMP_SOLO_MALUS` (-60): mocno zdemotowany, ale
proposable (decyduje człowiek, nie cisza). Telemetria: `new_courier_ramp.solo_rescue=true`.
Eskalacje z replayu (6-7/tydz.) tym samym znikają z klasy ryzyka.
Druga obserwacja (krótkie dorzutki bag=1, km<2 akceptowane przez człowieka) —
zostaje do przeglądu po 1. tygodniu live (ewentualne poluzowanie `bag==0` →
`bag<=1 ∧ km<2` wymaga osobnego ACK).
