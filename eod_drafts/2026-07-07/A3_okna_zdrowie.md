# A3 — Raport zdrowia trzech dojrzewających okien obserwacji

**Data:** 2026-07-07 (~18:10 Warsaw) · **Tryb:** POMIAROWY / READ-ONLY (grep+python; nic nie dotknięte: 0 zmian flag, 0 restartów, 0 Telegrama)
**Źródła:** `scripts/logs/shadow_decisions.jsonl` (697 rek., 2026-07-05T08:13 → 07-07T17:59), `dispatch_state/{mode_observer.jsonl, courier_ground_truth.json, gps_delivery_truth.jsonl, orders_state.json, eta_cell_residual_map.json}`
**Parytet metody:** korekty ETA liczone PRODUKCYJNĄ funkcją `calib_maps.eta_cell_residual_correct` — parytet 208/208 = **100%** (patrz §1).

---

## 1) conditional-ETA shadow (`ENABLE_ETA_CELL_RESIDUAL_CORRECTION` = OFF, obserwacja) — okno od 2026-07-07T06:47Z

Semantyka pola (potwierdzona kodem `shadow_dispatcher.py:546-564` + `calib_maps.py:206`):
`eta_cell_corrected_min = round(carry + cell_corr + rest_corr, 1)`, gdzie `carry = predicted_delivered_at − pickup_ready_at` [min], a **korekta = cell_corr + rest_corr** (addytywna, niezależna od bazy). Flaga OFF → korekta NIGDY nie zastosowana do obietnicy (`eta_cell_correction_flag=false` na **wszystkich** rekordach — obserwacja czysta, zgodnie z projektem).

| metryka | wartość |
|---|---|
| rekordów z polem w oknie | **218** (208 non-null + 10 null) |
| null (brak korekty) | 10 — wszystkie `no_plan` (KOORD/SKIP bez best-planu; oczekiwane) |
| **wielkość korekty [min]** | min **−7,18** · mediana **+3,23** · max **+7,50** · średnia +2,43 |
| znak korekty | **dodatnie 167 / ujemne 41** (0 zerowych) |
| skorygowane ETA [min] | 3,1 … 16,6 (med) … 42,8 |
| baza carry [min] | 3,7 … 13,4 (med) … 38,3 |
| absurdy | `|korekta|>10`: **0** · `skorygowane<0 lub >60`: **0** |
| parytet vs produkcja | **208/208 = 100%** (slot z `pickup_ready_at`, nazwa raw) |

**Korekta per (slot × solo/worek) — kierunkowo poprawna (spójna z mapą residuali):**
- **solo** → korekta DODATNIA (silnik systematycznie zaniża ETA solo): peak_lunch +3,2 · peak_dinner +3,4 · high_risk +4,8 · off +4,4
- **worek** → korekta UJEMNA (silnik zawyża ETA worka): −1,9 … −2,2 (mediany)
- Rozkład slotów (z `pickup_ready_at`): off 16 · peak_lunch 50 · high_risk 64 · peak_dinner 78 · Bundle: solo 167 / worek 41

**Trafienia w mapę `eta_cell_residual_map.json` (n_records=13272, 8 komórek + 52 restauracje):**
- warstwa KOMÓRKI: 208/208 trafień (każdy slot∈{off,peak_lunch,high_risk,peak_dinner} jest w mapie) — 10 nulli to brak planu, nie pudło mapy.
- warstwa RESTAURACJI (jak produkcja, nazwa raw): **HIT 170 / MISS 38**. Z 38 pudeł: **7 realnie poza mapą** (DRAPIEŻNIK, Nadajesz.pl ×3, Dr Tusz, Dentomax, Kurra — mosty/paczki/niegastro) + **31 „ofiar HTML-escape"** (patrz niżej).

**⚠ Znalezisko realne (drobne, nie-blocker):** 3 restauracje mają w logu nazwy HTML-escaped, których produkcyjny lookup NIE dopasowuje do odescape'owanych kluczy mapy → **tracą warstwę restauracji** (dostają tylko korektę komórki):
`Sweet Fit &amp; Eat`, `Sushi Rany Julek &amp; Pizza Majstry`, `Restauracja Kumar&#039;s`. To 31/208 = **15% rekordów bez doszlifowania restauracyjnego** (~±1–1,5 min niuansu). Korekta komórkowa działa nadal — więc dane są zdrowe do werdyktu, ale przed ewentualnym LIVE-apply warto odescape'ować nazwę na wejściu lookupu (fix u źródła: `result.restaurant` przed `eta_cell_residual_correct`, albo klucze mapy escaped-tolerant).

### WERDYKT 1: 🟢 **ZDROWE** — 208 korekt/dobę, sensowne co do wielkości (med +3,2 min, brak absurdów) i kierunku (solo+/worek−), parytet z produkcją 100%. Jedyny cień = 15% rekordów gubi warstwę restauracji przez HTML-escape (do zaadresowania przed flipem, nie psuje obserwacji).

---

## 2) mode-observer (okno 7 dni, domyka ~14.07) — `dispatch_state/mode_observer.jsonl`

Okno otwarte **dziś 07-07 06:25** → w logu **dopiero dzień 1 z 7** (216 ticków, 06:25–18:07, kadencja ~1/3 min).

| metryka | wartość |
|---|---|
| tryb (mode) | **S1: 216 / 216 = 100%** — **0× S2, 0× S3** |
| transition=true | **0** |
| sygnał L (load in-flight/aktywni) | min 0,00 · med 2,83 · max **4,33** · śr 2,70 |
| queue_pending | min 0 · med 2 · max 10 |
| latency_med_min | min 0 · med 2,8 · max 51,9 |
| defer_eligible | rozkład 0:28 · 1:58 · 2:61 · 3:35 · 4:14 · 5:9 · 6:4 · 8:1 (+5 wczesnych bez pola) — **zmienny, zdrowy** |

**Dlaczego tkwi w S1 (progi z `mode_layer.py`):** wejście S2 wymaga **2-z-3 sygnałów UTRZYMANYCH ≥10 min**: L≥6,0 · queue≥10 · latency≥5,0.
- L≥6,0: **0 ticków** (max 4,33 = 72% progu) — główny sygnał obciążenia ani razu nie sięgnął progu.
- queue≥10: **1 tick** (dotknął granicy raz).
- latency≥5,0: 57 ticków (często, ale to sygnał SAMOTNY).
- **2-z-3 jednocześnie (choćby chwilowo): 0 ticków** → brak szansy na „utrzymane ≥10 min" → 0 transition. Log w pełni spójny z FSM.

Interpretacja dla przyszłego werdyktu **E-4**: obserwator działa i zbiera zróżnicowany sygnał, ale **zdarzenie docelowe (eskalacja S2/S3) ma 0 próbek** — 07-07 był dniem normalnego obciążenia (L capnięte na 72% progu). E-4 nie ma na czym ocenić zachowania trybów podwyższonych. Zostało 6 dni okna; werdykt będzie miarodajny TYLKO jeśli w oknie trafi się realny dzień szczytowego stresu (2-z-3 utrzymane). Bez tego werdykt = „za mało zdarzeń".

### WERDYKT 2: 🟡 **CIENKIE DANE (na zdarzeniu docelowym)** — sam obserwator zdrowy (216 ticków, sygnały zmienne, kadencja OK), ale 100% S1 / 0× S2-S3 / 0 transition; obciążenie ani razu nie zbliżyło się do 2-z-3. Dzień 1/7 — potrzebny prawdziwy dzień szczytu, inaczej E-4 zostanie bez dowodu na tryby podwyższone.

---

## 3) GPS 5b — adopcja apki (🚨 ryzyko #1; werdykt pokrycia bramkuje flip O2/feas_carry)

Sygnał 5b = pole **`gps_arrived_at`** w `courier_ground_truth.json` (WDROŻONE 05.07, measurement-only, wymaga apki ≥ vc60). **LICZYMY DISTINCT kurierów (cid), nie rekordy. Flota ~62.**

| źródło | sygnał | DISTINCT cid (ost. ~3 dni) |
|---|---|---|
| `courier_ground_truth.json` — pole `gps_arrived_at` | **5b app-signal** | **2** → cid **179, 492** (8 rekordów) |
| … z tego `source=auto_geofence` (prawdziwy auto-fire 5b) | genuine 5b | **1** → cid **179** (3 rek., wszystkie 07-07) |
| … z tego `source=manual` (ręczny przycisk, nie auto-geofence) | — | 179 (×4, 07-06) + 492 (×1, 07-05) |
| `orders_state.json` — pole `gps_arrived_at` | — | **0** (pole żyje w ground_truth, nie w orders_state) |
| `sla_log.jsonl` | — | stały od 20.06 (nieaktualny, pominięty) |
| `gps_delivery_truth.jsonl` — server-side breadcrumb (NIE app-signal) | truth serwera | **3** (07-05:1, 07-06:2, 07-07:2; conf high 50 / low 14) |

Rozbicie 8 rekordów `gps_arrived_at`:
- cid 492 — 1× (07-05 17:19, manual)
- cid 179 — 7× (07-06 ×4 manual · 07-07 ×3 **auto_geofence** 15:08/16:41/17:57)

**Wersja apki:** `courier_app_version.json` → latest **vc72 / 0.9.58** (min vc1). Zdolność 5b (≥vc60) JEST w produkcji — ale **realny auto-geofence odpala 1 kurier (179), pierwszy raz dziś**. Reszta floty albo nie zaktualizowana/nie fired, albo tylko manual.

**Skala:** 2/62 ≈ **3%** floty dotknęło pola 5b; **1/62 ≈ 1,6%** przez prawdziwy auto-geofence. Kontekst: nawet server-side breadcrumb (mechanizm niezależny od apki) łapie tylko 3 kurierów/3 dni z detekcją geofence dostawy.

Implikacja: **werdykt pokrycia ~07-08.07 odczyta bliski-zeru** → **flip O2/feas_carry pozostaje ZABLOKOWANY** na braku pokrycia. To potwierdza ryzyko #1: adopcja praktycznie nie ruszyła; auto_geofence zaczął żyć dopiero dziś na 1 kurierze — trzeba wypchnąć/wymusić aktualizację apki i włączenie geofence na flocie, zanim werdykt pokrycia ma sens.

### WERDYKT 3: 🔴 **PROBLEM — adopcja praktycznie nie ruszyła.** 2 kurierów (179, 492) dotknęło `gps_arrived_at` w 3 dni, z tego 1 (179) przez prawdziwy auto_geofence i dopiero od 07-07. Wobec floty ~62 to ~1,6–3% — za mało na jakikolwiek werdykt pokrycia; flip O2/feas_carry zostaje bramkowany.

---

## TL;DR — 3 werdykty
1. **conditional-ETA:** 🟢 ZDROWE (208 korekt, med +3,2 min, 100% parytet, brak absurdów; drobny dług: 15% gubi warstwę restauracji przez HTML-escape).
2. **mode-observer:** 🟡 CIENKIE DANE (obserwator OK, ale 100% S1, 0× S2/S3, 0 transition; dzień 1/7 — potrzebny dzień szczytu na E-4).
3. **GPS 5b:** 🔴 PROBLEM — adopcja nie ruszyła: 2 kurierzy (1 genuine auto_geofence, od dziś) z floty ~62; flip O2/feas_carry dalej zablokowany.
