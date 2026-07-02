# HINDSIGHT-BENCHMARK v0 — ile Ziomek zostawia na stole (dzień 2026-06-28)

**Lane:** AUDYT 2.0 · **Tryb:** READ-ONLY wobec produkcji (żaden kod/env/serwis/repo nietknięty; liczone własnym pure-python w scratchpadzie).
**Dzień:** 2026-06-28 (wybrany bo ma pełne dane po OBU stronach: geometria w `obj_replay_capture.jsonl` + gotowe wyniki r6/breach/dostawa w datasecie kalibracyjnym i `decision_outcomes.jsonl`; 06-29 nie ma w kalibracji).
**Skrypty (scratchpad):**
`/tmp/claude-0/-root/c0b0a378-efd7-4ee5-9307-8bf7cdd90424/scratchpad/{extract_geom.py, extract_fact.py, calib_speed.py, final.py}`
Dane pośrednie: `.../scratchpad/{geom_0628.json, work_0628.json, final_out.json, sweep.jsonl, sweep2.jsonl}`

---

## TL;DR — GAP w 3 liczbach (wersja UCZCIWA, z limitem spóźnienia odbioru ≤20 min)

Prosty plan „z pełną wiedzą o całym dniu" (heurystyka wstawiania + 1 przebieg relokacji, ten sam model jazdy co dla faktu) na tych samych 272 zleceniach i tej samej flocie 13 kurierów:

1. **Jazda: −20,3%** — 1766 → 1407 km, czyli **−359 km/dzień** i **−756 kurierominut ≈ 12,6 roboczogodzin/dzień**.
2. **Twarde przekroczenia R6 (>40 min): 8 → 0** — wszystkie 8 „spalonych" zleceń dnia było do uniknięcia samą alokacją/trasowaniem.
3. **Świeżość bez pogorszenia:** R6 mediana 15 → 13 min, średnia 16,6 → 15,6, miękkie breach (>35) 14 → 9. Cena: mediana spóźnienia odbioru 1,6 → 10 min (w limicie 20), ale ogon p90 spada 26 → 19 i liczba odbiorów >20 min spada 45 → 0.

**GAP jest zgrubny (v0): kierunek i rząd wielkości, nie werdykt.** Znaczna część gapu siedzi w SZCZYCIE 14-17h (tam realny system się dusi i tam robi wszystkie twarde breach).

> ⚠️ **Najważniejsza lekcja metodyczna v0:** R6 „czas w worku" JEST OGRYWALNY — jeśli optymalizatorowi pozwolić dowolnie odraczać odbiór, „wygrywa" fałszywe −36% km i pozornie lepszy R6, kupując to **medianą spóźnienia odbioru 40 min i 64% zleceń odebranych >20 min po gotowości** (zimne jedzenie). Uczciwy gap MUSI być mierzony z ograniczeniem spóźnienia odbioru. Szczegóły niżej.

---

## 1. Co to jest i po co

Pierwszy pomiar „ile zostawiamy na stole" = różnica między FAKTYCZNYM wykonaniem dnia a PROSTYM planem policzonym wstecz z pełną wiedzą o wszystkich zleceniach dnia naraz. To dolne/zgrubne oszacowanie potencjału decyzji dyspozytorskich (alokacja + kolejność), przy **zamrożonej flocie** (ci sami kurierzy, te same okna zmian, te same zlecenia). Nie mierzy „więcej kurierów = lepiej", tylko „czy TE decyzje dało się podjąć lepiej".

## 2. Metoda (świadomie prosta)

- **Zlecenia dnia:** 272 (przecięcie: geometria z `obj_replay_capture.jsonl` ∩ fakt z `decision_outcomes.jsonl`), każde z: `pickup_coords`, `delivery_coords`, `pickup_ready_at`, realnym kurierem, realnym odbiorem/dostawą, realnym r6/breach, tier (gold/std+/std).
- **Flota:** 13 kurierów, którzy realnie pracowali. Okno zmiany każdego = [pierwszy realny odbiór − 15 min, ostatnia realna dostawa + 10 min]. Start każdego w centrum Białegostoku (53,132·23,164) — wspólnie dla faktu i kontrfaktu.
- **Wspólny model jazdy (fair):** dystans = haversine × 1,37; czas = dystans / **28,5 km/h** (skalibrowane z 73 zleceń solo dnia — mediana implikowanej prędkości); postój **2 min** na stop. Ten sam model liczy FAKT i KONTRFAKT — porównujemy DECYZJE, nie szum świata.
- **FAKT (model):** odtwarzam realną sekwencję stopów (posortowaną po realnych znacznikach odbioru/dostawy per kurier) i przepuszczam przez model jazdy → model-km, model-R6, model-breach. NIE egzekwuję capów (fakt odtwarza rzeczywistość, która miała breach).
- **KONTRFAKT (plan wsteczny):** zlecenia w kolejności `ready`, wstawiam do kuriera minimalizując przyrost kosztu `koszt = jazda_min + α·Σr6 + λ·Σmax(0,r6−35)`; twarde: odbiór po `ready`, odbiór przed dostawą, **R6 ≤ 40 (HARD)**, koniec ≤ koniec zmiany, **odbiór ≤ ready + limit (HARD, patrz niżej)**. Potem 1 przebieg relokacji pojedynczych zleceń (dla r6>30). Bez OR-Tools, pure python, jeden proces, `nice(10)`.
- **GAP** = KONTRFAKT vs FAKT na identycznym modelu.

### Walidacja modelu jazdy
Model-FAKT R6 średnia **16,6** vs realny R6 z samych znaczników **17,5** (mediana 15 vs 16). Model jest fair, lekko optymistyczny — śledzi rzeczywistość z dokładnością ~1 min w środku rozkładu. (ALE nie widzi najgorszych przypadków — patrz §6.)

---

## 3. FAKT — realny dzień 2026-06-28

| metryka | wartość |
|---|---|
| zlecenia | 272 · kurierzy 13 · tier: 66 gold / 125 std+ / 81 std |
| R6 realny (znaczniki) | med **16,0** · śr **17,5** · p90 31,2 · **>35: 12 (4,4%)** · **>40: 8 (2,9%)** |
| R6 model (fair baseline) | med 15,0 · śr 16,6 · p90 30,2 · >35: 14 · >40: 8 |
| jazda (model) | **1766 km · 3718 kurierominut** |
| spóźnienie odbioru (odbiór − ready) | med **1,6 min** · p90 26,1 · >20 min: 45 zleceń (16%) · >30 min: 19 (7%) |

Dzień był w większości zdrowy: mediana świeżości 16 min, mały ogon 8 spalonych zleceń (>40 min w worku). To jest baseline do pobicia.

---

## 4. KONTRFAKT — pułapka vs uczciwy gap

### 4a. ⚠️ PUŁAPKA — optymalizacja BEZ limitu odbioru (nieważny wynik)
Gdy plan wolno odraczać odbiór dowolnie (α=0,3, tylko cap R6>40):
- km 1766 → **1137 (−36%)**, R6 mediana 14,5 — wygląda jak miażdżąca dominacja...
- ...ale **mediana spóźnienia odbioru = 40 min, średnia 80 min, p90 = 223 min, 64% zleceń odebranych >20 min po gotowości**. Rekordzista: odbiór **343 min** (5,7h!) po gotowości.

To artefakt: minimalizacja jazdy = batchowanie = odkładanie odbioru. R6 (worek) spada bo jedzenie „leży" w restauracji, a nie u kuriera. **Metryka R6 sama w sobie jest ogrywalna. Ten wynik odrzucam.**

### 4b. ✅ UCZCIWY GAP — z twardym limitem spóźnienia odbioru
Dokładam twarde ograniczenie „odbiór ≤ ready + 20 min" (odpowiednik reguły R-LATE-PICKUP / dyscypliny R27 — realny system trzyma odbiór krótko). Sweep α (waga świeżości):

| limit | α | cf_km | cf_dmin | R6 med | R6 śr | >35 | >40 | odbiór med | odbiór p90 | odbiór>20 | forced |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **FAKT** | — | **1766** | **3718** | **15,0** | **16,6** | **14** | **8** | **1,6** | **26,1** | **45** | — |
| 20 | 0,3 | 1379 | 2902 | 16,2 | 18,2 | 25 | 0 | 9,8 | 19,0 | 0 | 0 |
| **20** | **0,6** | **1407** | **2962** | **13,3** | **15,6** | **9** | **0** | **10,1** | **18,8** | **0** | **0** |
| 20 | 1,0 | 1497 | 3151 | 12,3 | 14,2 | 8 | 0 | 12,0 | 18,7 | 0 | 0 |
| 30 | 0,6 | 1379 | 2903 | 13,1 | 15,5 | 11 | 0 | 15,2 | 28,2 | 99 | 2 |

**Punkt kanoniczny = limit 20 / α=0,6** (najostrzejszy limit odbioru, 0 zleceń wypchniętych):
- Jazda **−20,3%** (359 km / 12,6 rbg mniej).
- Świeżość LEPSZA: R6 med 15→13,3, śr 16,6→15,6, miękkie 14→9, **twarde 8→0**.
- Terminowość odbioru: środek gorszy (med 1,6→10), ale **ogon LEPSZY** (p90 26→19; >20 min 45→0). Reality odbierała 16% zleceń >20 min późno — kontrfakt nikogo.

To „miękka dominacja": lepiej/równo na jeździe, świeżości i twardych breach; jedyny koszt to ~8 min wyższa mediana czasu leżenia w restauracji (w limicie 20 min = jedzenie ciepłe).

---

## 5. Rozkład godzinowy — gap żyje w SZCZYCIE (14-17h)

Godzina wg `ready` (Warsaw): średni R6 FAKT→CF, breach >35 / >40 FAKT→CF:

| godz | n | R6 fakt→cf | >35 f→cf | >40 f→cf |
|---|---|---|---|---|
| 12h | 23 | 13,8 → 12,2 | 1→0 | 0→0 |
| 13h | 29 | 15,3 → 15,7 | 1→0 | 0→0 |
| **14h** | **40** | **20,1 → 18,5** | 2→3 | **2→0** |
| **15h** | **33** | **20,2 → 16,3** | **5→0** | **2→0** |
| **16h** | **34** | 17,8 → 17,8 | 1→2 | **1→0** |
| **17h** | **25** | 17,9 → 15,5 | 3→2 | **2→0** |
| 19h | 15 | 12,0 → 14,5 | 0→0 | 0→0 |
| 20h | 19 | 12,2 → 10,8 | 0→0 | 0→0 |

- **Szczyt 14-17h** = 132 zlecenia (49% dnia), tam siedzą WSZYSTKIE 8 twardych breach realnego dnia — i wszystkie 8 znika w kontrfakcie. Tam realny R6 skacze do ~20 min, kontrfakt ściąga do ~16-18.
- **Spokój (19-22h):** kontrfakt bywa lekko GORSZY na R6 (np. 19h: 12→14,5) — bo trzyma zlecenia ~8 min pod batching, a przy pustej flocie nie ma czego batchować (zysk jazdy tam bliski zeru). Uczciwa niuansja: plan „z pełną wiedzą" nie jest za darmo lepszy wszędzie.

---

## 6. 5 najgorszych REALNYCH przypadków — decyzja vs wykonanie

| # zlec. | kurier | tier | R6 REAL | R6 model-fakt | R6 cf | diagnoza |
|---|---|---|---|---|---|---|
| 483878 | 526 | gold | **61,7** | 16,4 | 5,2 | **poślizg wykonania** — geometria łatwa (model 16), realne 62 min to NIE trasowanie (idle/GPS/prep/button) |
| 484064 | 492 | std | **50,3** | 40,7 | 28,8 | mieszany — model widzi trudne (41), routing ścina ~12 min |
| 483880 | 526 | gold | **50,0** | 14,1 | 11,5 | **poślizg wykonania** — model widzi 14, realne 50 poza modelem |
| 483967 | 409 | std+ | **46,4** | 55,1 | 35,3 | **trudna geometria** (long-haul) — model też >40; routing pomaga ~20 min, wciąż przy capie |
| 484048 | 471 | std+ | **41,6** | 27,9 | 15,4 | **decyzja** — routing ścina ~12 min |

**Wzorzec:** ~połowa najgorszego ogona to **poślizg wykonania** (model widzi zlecenie jako łatwe → 62/50 min NIE wynika z decyzji dyspozytora, tylko z rzeczywistości: przestój, brak GPS, długi prep, błąd przycisku). Druga połowa to **realne decyzje/geometria**, które hindsight-routing faktycznie poprawia o 12-20 min. Kurier 526 wpada 2× z realnym R6 ~50-62 przy model-R6 ~14-16 — jego problemy tego dnia były wykonawcze, nie decyzyjne. **Dyspozytor może naprawić tylko drugą połowę; pierwsza to temat na osobny audyt (GPS/idle/prep).**

---

## POKRYCIE

- **Dzień:** 2026-06-28, **272 zlecenia** = przecięcie geometrii (275 z obj_replay) i faktu (274 z decision_outcomes); 3 odpadły na braku coords/czasów. To ~98% dziennego wolumenu z logów.
- **Flota:** wszystkie 13 kurierów realnie pracujących; okna zmian z realnej aktywności.
- **FAKT:** realne znaczniki odbiór/dostawa/r6/breach (decision_outcomes, r6 z czystego UTC) + tier z datasetu kalibracyjnego.
- **Geometria:** realne `pickup_coords`/`delivery_coords`/`pickup_ready_at` per zlecenie z `obj_replay_capture.jsonl` (per-decyzyjny capture silnika).
- **Model jazdy skalibrowany na danych dnia** (28,5 km/h z 73 zleceń solo), zwalidowany vs realny R6 (Δ~1 min w medianie).
- **Kontrfakt:** heurystyka wstawiania + relokacja, z twardymi regułami R6≤40 + limit odbioru; sweep α∈{0,3;0,6;1,0} × limit∈{20;30}.
- **Uczciwość:** jawnie pokazana pułapka ogrywalności R6 (§4a) i pomiar spóźnienia odbioru po obu stronach.

## JAWNE LUKI

1. **Jasnowidzenie (przecina w drugą stronę!):** kontrfakt zna CAŁY dzień naraz; realny dyspozytor decyduje online, sekwencyjnie. Część gapu jest fizycznie nieosiągalna w czasie rzeczywistym → GAP **ZAWYŻA** to, co online-system mógłby złapać. (Kontra dla „dolnego oszacowania".)
2. **Heurystyka ≠ optimum:** wstawianie+relokacja to nie OR-Tools; prawdziwy optymalny plan byłby LEPSZY → w tym kierunku GAP jest DOLNYM oszacowaniem. Dwie luki (1) i (2) działają przeciwnie — traktować liczby jako rząd wielkości, nie punkt.
3. **Model jazdy zgrubny:** haversine×1,37 @ 28,5 km/h stała, brak realnego ruchu/korków, prep-time, per-restauracja dwell (stałe 2 min). Fair dla RÓŻNICY (ten sam model), ale wartości bezwzględne ≠ rzeczywistość. Model **nie widzi poślizgu wykonania** — najgorsze realne R6 (50-62 min) są poza modelem (§6), więc realny „ból dnia" jest większy niż model-fakt (16,6) pokazuje.
4. **Button-truth ±3 min:** realne znaczniki dostawy mają szum (`delta_button_minus_physical` ~1,7 min typowo, `gps_delivery_truth.jsonl`). Wpływa na realny R6, nie na porównanie model-model.
5. **Committed `czas_kuriera` prawie pusty** (2/272 w decision_outcomes) → „spóźnienie vs committed" zastąpione „spóźnienie vs ready" (odbiór − pickup_ready_at). To rozsądny proxy, ale nie dokładnie umówiony z klientem czas.
6. **Okna zmian z obserwacji, nie z grafiku:** [pierwszy odbiór−15, ostatnia dostawa+10]. Zamraża realną podaż pracy (cel), ale nie modeluje przerw/dostępności wewnątrz okna.
7. **Bundling faktu przybliżony** sekwencją znaczników czasu (nie realną trasą GPS); realne przeploty mogły być inne. Start w centrum dla wszystkich (pierwszy dojazd liczony obu stronom równo).
8. **Cap R6 35/40** użyty jako poziom eskalacji „normalnie/alarm" (interpretacja z kanonu reguł), nie jako klasa kuriera. Tier (gold/std) NIE zmienia capu w tym v0.
9. **Jeden dzień, jeden przebieg.** Brak przedziału ufności / wielu dni. v0 = sonda kierunku.

---

## Wniosek i sugerowany next (v1)

Nawet prymitywny plan-z-pełną-wiedzą, uczciwie ograniczony (odbiór ≤20 min), pokazuje że **~20% jazdy dnia było zbędne, a wszystkie 8 twardych przekroczeń świeżości było do uniknięcia samą alokacją** — bez pogorszenia netto usługi, z gapem skupionym w szczycie 14-17h. To realny, choć zgrubny sygnał, że w trasowaniu/batchowaniu pod obciążeniem siedzi kilkanaście roboczogodzin i cały ogon spalonych zleceń.

Najmocniejsza lekcja: **świeżość trzeba mierzyć z ograniczeniem spóźnienia odbioru — R6 sam w sobie jest ogrywalny** i każdy przyszły benchmark/optymalizator musi to pinować, inaczej „poprawia" metrykę zimnym jedzeniem.

Propozycje v1 (osobne, wg protokołu): (a) wielodniowy przebieg 7-14 dni dla przedziału i stabilności; (b) solver online/rolling-horizon (realistyczne jasnowidzenie okna 15-20 min zamiast całego dnia) → gap osiągalny w czasie rzeczywistym; (c) rozdzielić „gap decyzyjny" od „poślizgu wykonania" (join z `gps_delivery_truth` + idle/prep) — z §6 wynika, że ~połowa najgorszego ogona to wykonanie, nie decyzja; (d) OR-Tools/lepszy solver dla twardszego dolnego oszacowania.
