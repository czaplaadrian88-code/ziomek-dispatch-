# B5 — GPS / anchor reliability measurement (read-only)

**Data:** 2026-06-13 | **Tryb:** pomiar (READ-ONLY, zero zmian flag/danych/serwisów) | **Sprint:** B (audyt 2026-06-10, sekcja GPS-adoption)
**Skrypt pomiarowy:** `/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-13/sprintB/B5_pos_source_measure.py`
**Źródła (zweryfikowane na żywo, nie z pamięci):**
- `/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl` (current, 692 rek.) + `.jsonl.1` (rotated, 2184 rek.) → **2876 decyzji**, okno **2026-06-02 07:11 → 2026-06-13 15:08 UTC (~11 dni)**
- `/root/.openclaw/workspace/dispatch_state/gps_quality_shadow.jsonl` (GPS-02 shadow) → **105 rek.**, okno **2026-06-13 10:24 → 13:42 UTC (~3,3 h, JEDEN dzień)**
- `/root/.openclaw/workspace/dispatch_state/courier_last_pos.json` (store snapshot, 12 wpisów)

> **Kontekst (potwierdzony w `AUDIT_FIX_PLAN_2026-06-10.md` + `feedback_rules.md`):** flota CELOWO bez GPS (polityka treningowa Adriana, korekta 13.06 — „Brak GPS = celowa polityka"). Last-known-pos store **LIVE** (`ENABLE_COURIER_LAST_KNOWN_POS=True`). GPS-02 (filtr accuracy+teleport) **SHADOW** — flaga `ENABLE_GPS_ACCURACY_TELEPORT_FILTER` **NIE ustawiona → default OFF**; compute-shadow działa (`ENABLE_GPS_QUALITY_SHADOW` default ON). To pomiar środowiska, **bez rekomendacji flipu.**

---

## TL;DR (liczby, które się liczą)

| Metryka | Wartość | Uwaga |
|---|---|---|
| **Udział live GPS w wyborze (best courier)** | **16,3%** (468/2876) | reszta na kotwicach/fikcji |
| Udział live GPS wśród WSZYSTKICH kandydatów | 10,2% (1347/13242) | środowisko, nie tylko wybrany |
| **Kotwice (anchor: last_picked_up_*/last_assigned)** | **41,3%** best / 44,0% kandydatów | dominujące źródło pozycji |
| `post_wave` (pozycja PROJEKTOWANA — koniec trasy) | 19,9% best | label pochodny, NIE surowy fix |
| **Fikcja-centrum (no_gps/pre_shift/none → BIALYSTOK_CENTER)** | **19,7%** best / 33,1% kandydatów | brak realnej pozycji |
| **Rescue z last-known-store fire (best)** | **2,9%** (82/2876) | rzadko, ale działa |
| Decyzje z ≥1 kandydatem GPS | 37,7% (1085/2876) | ⅔ decyzji bez ŻADNEGO GPS |
| Decyzje BEZ żadnej realnej pozycji (all-fiction) | 13,5% (389/2876) | wybór po samej fikcji |
| **Unikalni kurierzy z live GPS przez 11 dni** | **5** (484/123/400/370/413) | 413 = tylko 3 trafienia |
| GPS-02 shadow would-reject | 2/105 (1,9%) | **oba low_accuracy, 0 teleport** |
| GPS-02 staleness store (rescue) p90/max | 16,3 / 21,7 min | TTL=25min — **inwariant trzyma (0 ≥25)** |

**Jednozdaniowo:** Live GPS prowadzi tylko **16,3%** decyzji i pochodzi w praktyce od **5 kurierów** (realnie 4) — system jedzie na kotwicach (41%) + fikcji-centrum (20%); last-known-store ratuje 2,9% wyborów ze świeżością median 6,2 min (TTL trzyma); telemetria GPS-02 ma **n=105 z 3,3 h jednego dnia** z **2 rejectami (0 teleportów)** — **stanowczo za mało i za krótko, by wnioskować o gotowości flipu GPS-02.**

---

## 1. Rozkład `pos_source` (kto faktycznie wybrany — BEST courier, 2876 decyzji)

### 1A. Surowe etykiety
| pos_source | n | % |
|---|---:|---:|
| `last_picked_up_pickup` | 638 | 22,2% |
| `last_assigned_pickup` | 614 | 21,3% |
| `post_wave` | 571 | 19,9% |
| `gps` | 476 | 16,6% |
| `no_gps` | 222 | 7,7% |
| `pre_shift` | 184 | 6,4% |
| `None` | 160 | 5,6% |
| `last_picked_up_delivery` | 8 | 0,3% |
| `last_picked_up_interp` | 3 | 0,1% |

### 1B. Buckety (taksonomia audytu)
| Bucket | n | % | Co to jest |
|---|---:|---:|---|
| **live_gps** | 468 | **16,3%** | świeży fix GPS (pos_from_store=False) |
| **last_known_store** | 82 | **2,9%** | rescue ze store (pos_from_store=True; lekcja #176) |
| **anchor** | 1189 | **41,3%** | pozycja z geometrii bagu/historii (punkt realnie odwiedzony) |
| **projected_post_wave** | 571 | 19,9% | **pozycja PROJEKTOWANA** = koniec planowanej trasy (nie surowy fix) |
| **fiction_center** | 566 | 19,7% | BIALYSTOK_CENTER (no_gps/pre_shift/none — brak realnej pozycji) |

> ⚠ **Uwaga o `gps` vs `live_gps`:** 476 surowych `gps` (16,6%) vs 468 `live_gps` (16,3%) — różnica 8 to przypadki, gdzie etykieta `gps` przyszła **ze store** (`pos_from_store=True`, ostatni dobry fix GPS odtworzony z last-known-pos). Te 8 trafia do bucketu `last_known_store`, nie `live_gps`. To poprawne: rescue jest rescue niezależnie od etykiety źródła.
> ⚠ **`post_wave` to NIE źródło pozycji** — to projekcja końca trasy (kurier „zaraz wraca, ≤15/30 min”, `dispatch_pipeline.py:3993`), używana do bonusu scoringu, nie do twardej lokalizacji. Trzymam ją osobno, żeby nie zawyżać „informed”.

---

## 2. Rozkład wśród WSZYSTKICH kandydatów (środowisko, 13 242 kandydatów = best + alternatywy)

| Bucket | n | % |
|---|---:|---:|
| anchor | 5832 | 44,0% |
| **fiction_center** | 4386 | **33,1%** |
| projected_post_wave | 1540 | 11,6% |
| **live_gps** | 1347 | **10,2%** |
| last_known_store | 137 | 1,0% |

**Wniosek:** w puli ocenianych kurierów **realny fix GPS ma tylko ~10%**, a aż **⅓ to fikcja-centrum**. Selektor wybiera best lepiej niż średnia puli (16,3% live_gps w best vs 10,2% w puli) — bo `_demote_blind_empty` + `ENABLE_BEST_EFFORT_POS_SOURCE_KEY` świadomie spychają blind+empty na dół. To działa zgodnie z projektem.

---

## 3. Last-known-pos rescue — jak często fire i jak wiarygodne

| Metryka | Wartość |
|---|---|
| Kandydaci uratowani ze store | 137 / 13 242 (**1,0%** kandydatów) |
| **BEST ze store** | 82 / 2876 (**2,9%** decyzji) |
| Decyzje z ≥1 kandydatem ze store | 95 / 2876 (3,3%) |
| Etykieta niesiona przez rescue | `last_picked_up_pickup`: 69, `last_assigned_pickup`: 5, `gps`: 8 |
| **Świeżość store-rescued best (`pos_age_min`)** | n=82, min 0,2 / **median 6,2** / mean 7,6 / **p90 16,3** / max 21,7 min |
| **Inwariant TTL=25 min** | ≥20 min: 4 wpisy · **≥25 min: 0** ✅ (store poprawnie prune'uje) |
| Store-best, gdy istniał kandydat z live GPS | **36** |

**Interpretacja:**
- Rescue to mechanizm **niskoczęstotliwościowy** (2,9% wyborów) — czyli „awaryjny”, nie codzienny chleb. Spójne z lekcją #176 (kurier traci pozycję do fikcji-centrum tylko w wąskiej luce GPS-lag).
- **Świeżość trzyma się TTL** — median 6,2 min, max 21,7 < 25 min. Zero przypadków powyżej TTL → mechanizm prune (`_save_last_known_pos`) i bramka wieku (`_rescue_from_last_pos`, age<TTL) działają.
- **36 przypadków „store-best przy istniejącym kandydacie GPS”** — NIE jest to anomalia per se: to znaczy, że kurier ze store wygrał scoringiem z kurierem mającym świeży GPS (bliższy/lepszy bag/tier), a nie że rescue „przebił” lepszą pozycję. Pozycja jest tylko jednym wejściem do scoringu. Surowy fakt podaję do oceny Adriana, bez interpretacji jako błąd.

---

## 4. Wiarygodność kotwic (staleness) — co da się, a czego NIE da się zmierzyć

| Bucket best | n (z `pos_age_min`) | median | mean | p90 | max |
|---|---:|---:|---:|---:|---:|
| live_gps | 81 | 0,7 | 1,2 | 3,4 | 4,7 min |
| **anchor (non-store)** | **0** | — | — | — | — |
| store-rescued | 82 | 6,2 | 7,6 | 16,3 | 21,7 min |

> 🔴 **LUKA POMIAROWA (istotna):** kotwice anchorowe (`last_picked_up_*`, `last_assigned_pickup`) **NIE logują `pos_age_min`** — to punkty geometrii (ostatni odwiedzony pickup/delivery), nie fix ze znacznikiem czasu. Z tego wynika, że **„jak stara / jak często błędna jest kotwica” NIE jest bezpośrednio mierzalne z shadow_decisions.** A to jest najliczniejszy bucket (41% best). To dokładnie pytanie, które AUDIT_FIX_PLAN E7 stawia jako fundament autonomii („zmierzyć WIARYGODNOŚĆ kotwic czasowych — eta_calibration per pos_source”) — i którego ten log NIE zaspokaja. Patrz „Czego brakuje”.

- Live GPS, gdy jest, jest **bardzo świeży** (median 0,7 min, max 4,7) — czyli fix od tych 4-5 kurierów jest realnie aktualny, nie zwietrzały.

---

## 5. Jakość pozycji na poziomie decyzji

| Metryka | Wartość |
|---|---|
| Decyzje z ≥1 kandydatem live-GPS | 1085 / 2876 (**37,7%**) |
| Decyzje z ≥1 kandydatem „informed” (gps/anchor/store/post_wave) | 2487 / 2876 (86,5%) |
| **Decyzje ALL-fiction (żaden kandydat bez realnej pozycji)** | **389 / 2876 (13,5%)** |

**Wniosek:** w **62,3% decyzji NIE ma w puli ani jednego kuriera z żywym GPS** — system MUSI polegać na kotwicach. W **13,5% decyzji nie ma nawet kotwicy** — wybór jedzie po samej fikcji-centrum (te przypadki to głównie pre-shift / start zmiany / cała flota no_gps). To jest empiryczne tło dla decyzji Adriana, że Ziomek ma umieć bez GPS.

---

## 6. GPS-02 (filtr accuracy + teleport) — telemetria SHADOW

| Metryka | Wartość |
|---|---|
| Rekordy | **105** |
| **Okno** | **2026-06-13 10:24 → 13:42 UTC (~3,3 h, JEDEN dzień)** |
| `filter_active` | `{False: 105}` ✅ czysty shadow, **zero efektu na flotę** |
| accept | `{True: 103, False: 2}` |
| **WOULD-REJECT (accept=False)** | **2 / 105 (1,9%)** → oba **low_accuracy**, **0 teleport** |
| has_accuracy_field | `{True: 105}` (wszystkie fixy mają accuracy) |
| accuracy_m | n=105, median 7,6 / mean 13,9 / p90 18,6 / **max 300,0** m (próg 150) |
| jump_km | n=52, median 0,41 / p90 1,63 / **max 3,28** km (próg teleportu 2,0) |
| implied_speed_kmh | n=50, median 26,0 / p90 72,6 / **max 496,1** km/h (próg 120) |
| anchor teleportu (wiek) | n=103, median 0,9 / max 5,1 min (użyteczna ≤8) |

**Rejecty (oba):**
- `kid=370 low_accuracy(300m>150m)` jump 1,09 km
- `kid=370 low_accuracy(200m>150m)` jump 3,28 km, speed 57,5 km/h

🔴 **TELEPORT — zero firingów, choć były ekstremalne prędkości:** zaobserwowano fixy z `implied_speed_kmh` **280 / 496 / 177 / 145 km/h** — i **WSZYSTKIE zostały ZAAKCEPTOWANE**, bo `jump_km` < 2,0 km (np. 0,25 / 0,87 / 1,83 / 0,84 km). To **poprawne działanie projektu**: bramka teleportu jest koniunkcją (`jump>2km AND speed>120`), a sub-kilometrowy skok przy ~0 dt to jitter w mieście, nie teleport. **Skutek pomiarowy:** reguła teleportu **nie odpaliła ani razu** → jej realna precyzja/odsetek false-positive jest **NIEZWERYFIKOWANY na danych** (brak ani jednego prawdziwego skoku >2 km przy >120 km/h w oknie).

🔴 **ZAKRES DANYCH GPS-02 = krytycznie wąski:**
- Plik `gps_quality_shadow.jsonl` **powstał dziś 10:24** (birthtime), choć `dispatch-shadow` wystartował **02:30 UTC**. Brak rotacji/backupu (jedyny plik). Najprostsze wyjaśnienie spójne z danymi: **GPS fixy są tak rzadkie** (flota GPS-off), że przed 10:24 żaden się nie pojawił; log jest append-only i powstaje przy pierwszym fixie.
- **Tylko 4 unikalne kid** w całym logu: 370 (34), 484 (27), 400 (27), 123 (17).
- Cross-check na 11 dniach shadow_decisions: **tylko 5 kurierów EVER** miało live GPS (484:649, 123:419, 400:163, 370:113, 413:3) — czyli GPS to garstka urządzeń, prawdopodobnie testowych/pojedynczych.

---

## 7. courier_last_pos.json — snapshot store (punkt w czasie)

- Wpisy: **12**
- Źródła: `last_picked_up_pickup: 5`, `gps: 4`, `last_assigned_pickup: 3`

Spójne z resztą: garść wpisów GPS (te same urządzenia), reszta kotwice. Store żyje i miesza źródła zgodnie z `_LAST_POS_GOOD_SOURCES`.

---

## Co to implikuje dla gotowości flipu GPS-02 (sam pomiar, BEZ rekomendacji)

> Zadanie B5 jawnie: *pure measurement — no recommendation to flip.* Poniżej tylko fakty wpływające na gotowość, do oceny Adriana.

1. **Sygnał do działania filtra jest minimalny.** W oknie shadow GPS-02 (3,3 h, n=105) filtr **odrzuciłby 2 fixy (1,9%)** — oba z tytułu słabej dokładności (200/300 m), **zero teleportów**. Efekt flipu na flotę byłby dziś **prawie zerowy** (2 fixy od 1 kuriera).
2. **Reguła teleportu jest nieprzetestowana empirycznie.** Mimo prędkości do ~496 km/h ani jeden przypadek nie spełnił koniunkcji (skok >2 km). Nie wiemy, jak filtr zachowa się na PRAWDZIWYM teleporcie — bo takiego w danych nie było. Precyzja/recall teleportu = brak danych.
3. **Baza GPS jest za mała i za skupiona, by kalibrować progi.** 4-5 urządzeń, ~3 h logu jednego dnia. To nie jest reprezentatywna próba floty (kurierzy celowo bez GPS). Kalibracja `GPS_ACCURACY_MAX_M=150` / `TELEPORT_*` na takim n byłaby przestrzeleniem w ciemno.
4. **Inwarianty bezpieczeństwa, które już teraz trzymają:** `filter_active=False` w 100% (czysty shadow), TTL store=25 min utrzymany (max 21,7), wszystkie fixy mają pole accuracy. Mechanika jest zdrowa — brakuje wyłącznie WOLUMENU/CZASU obserwacji.
5. **Najważniejsza luka dotyczy NIE GPS, lecz kotwic.** 41% decyzji jedzie na kotwicach bez zapisanego `pos_age_min` → ich „staleness/błędność” jest niemierzalna z tego logu. Zgodnie z korektą Adriana 13.06 + E7 w AUDIT_FIX_PLAN, fundamentem autonomii jest wiarygodność KOTWIC (eta_calibration per pos_source), nie adopcja GPS. **Ten strumień danych tego nie pokrywa.**

---

## Czego brakuje, żeby domknąć pomiar (dane + termin)

| # | Brak | Dlaczego blokuje | Co potrzebne / kiedy |
|---|---|---|---|
| D1 | **GPS-02 shadow ma tylko ~3,3 h / 1 dzień / 4 kid / 0 teleportów** | nie da się ocenić precyzji filtra (zwłaszcza teleportu) ani skalibrować progów | dłuższe okno shadow przy **realnym wolumenie GPS** — a ten zależy od apki v2 / włączenia GPS przez Adriana. Przy obecnej polityce GPS-off **może nie nadejść w mierzalnej ilości** — to trzeba nazwać wprost, nie czekać w nieskończoność. Minimum sensowne: ≥7 dni z ≥kilkudziesięcioma kurierami emitującymi GPS, ALBO replay historii `gps_history` (111 819 fixów wg `gps_quality_calib.md`) przez `assess_gps_quality` off-line. |
| D2 | **Kotwice (41% best) nie logują `pos_age_min`** | „jak stara/błędna kotwica” = niemierzalne; to fundament E7 | dodać telemetrię wieku kotwicy do shadow (osobny sprint, NIE ten) lub policzyć z `eta_calibration_shadow.jsonl` per pos_source (czy ETA z kotwicy trzyma limit 5 min odbioru). To zadanie E7, nie B5. |
| D3 | **Brak „ground truth” teleportu** | precyzja reguły teleportu = 0 obserwacji | potrzebny realny skok >2 km / >120 km/h w danych (rzadkie zdarzenie) lub syntetyczny replay z `gps_history`. |
| D4 | Powód, czemu plik GPS-02 powstał 10:24 a nie 02:30 | upewnić się, że to rzadkość fixów, NIE bug/truncate (gdyby był rotator, część danych przepadła) | potwierdzone pośrednio: brak rotatora (jedyny plik), append-only, fixy rzadkie. Gdyby Adrian chciał pewności 100% — sprawdzić logrotate/cron na `dispatch_state/*.jsonl` (poza zakresem read-only B5: to inspekcja konfiguracji, nie danych). |

---

## Reprodukcja

```bash
cd /root/.openclaw/workspace/scripts
/root/.openclaw/venvs/dispatch/bin/python \
  dispatch_v2/eod_drafts/2026-06-13/sprintB/B5_pos_source_measure.py
```

Skrypt jest READ-ONLY (otwiera pliki w trybie `r`, nic nie zapisuje, nie dotyka flag/serwisów). Bucketing w `bucket_of()`; taksonomia w stałych `LIVE_GPS/ANCHOR_SOURCES/PROJECTED/FICTION` na górze pliku — wprost do audytu/zmiany progu.

---

*Pomiar wykonany w trybie audytowym. Liczby skrzyżowane między shadow_decisions (best vs all-candidates), gps_quality_shadow i store snapshot — wzajemnie spójne (np. ta sama piątka kid GPS w obu źródłach). Żadnej liczby nie zmyślono; tam gdzie danych brak (staleness kotwic, teleport ground-truth) — jawnie oznaczone „nie zmierzone”.*
