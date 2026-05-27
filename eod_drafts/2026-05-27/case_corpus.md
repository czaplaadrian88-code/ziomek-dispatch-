# Case Corpus — 3 propozycje Adriana z 2026-05-27

**Sesja:** 2026-05-27
**Trigger:** Adrian przedłożył 3 kolejne propozycje Ziomka z prośbą o pełną diagnozę po koordynatach. Wszystkie zmapowane na bugi A/B/C z planu sprint 26.05 (`SPRINT_PLAN_geometry_fairness_bugs.md`).
**Cel pliku:** ground-truth corpus dla replay calibration (Faza 4 sprint planu) — po flipie BUG A+B / dodaniu commit_divergence verdict-gate sprawdzić czy te 3 trasy się odwróciły lub poszły do KOORD.

---

## Ground truth — koordynaty (źródło: `dispatch_state/geocode_cache.json`)

| Punkt | Lat, Lon | Dzielnica |
|---|---|---|
| Naleśniki Jak Smok (Lipowa 12) | 53.1333, 23.1546 | Centrum |
| Sushi RJ & Pizza Majstry (Lipowa 14) | 53.1334, 23.1540 | Centrum |
| Goodboy (Wiejska 55) | 53.1153, 23.1461 | Bojary (S) |
| Kaczorowskiego 14/222 | 53.1219, 23.1462 | Przydworcowe |
| Wyszyńskiego 4a/19 | 53.1297, 23.1395 | Przydworcowe (N) |
| Wyszyńskiego 8c/14 | 53.1283, 23.1406 | Przydworcowe (N) |
| Hacienda Pizza (Szarych Szeregów 2) | 53.1308, 23.0915 | Leśna Dolina |
| Szarych Szeregów 9b/2 | 53.1291, 23.0898 | Leśna Dolina |
| Retrospekcja (Wiejska 65) | 53.1146, 23.1472 | **Bojary** (NIE Nowe Miasto) |
| Pani Pierożek (Waszyngtona 38) | 53.1245, 23.1510 | Piaski |
| Transportowa 2d | 53.1136, 23.1276 | Antoniuk-Bojary S |
| Żeromskiego 3b/u2 | 53.1105, 23.1315 | Kawaleryjskie |
| 42 PP 72G | 53.1410, 23.2265 | Wysoki Stoczek (NE) |
| Uwędzony Beef & Chicken (KEN 40/u8) | 53.1516, 23.0883 | Słoneczny Stok (NW) |
| Rukola Sienkiewicza 52 | 53.1377, 23.1686 | Centrum |
| Proletariacka 19/Loku7 | 53.1394, 23.1532 | Centrum |

---

## CASE #10 — Adrian R K-400 · Goodboy → Wyszyńskiego 8c/14 (AUTO)

**Status:** 🟢 AUTO ← system uważa za pewny wybór, propozycja przeszła auto
**Trigger Adriana:** „dlaczego propozycja nie daje najpierw zrzutów wyszyńskiego kaczorowskiego i póxniej odbiór goodboya, to byłby najlepszy scenariusz, ponieważ jest to wszystko po drodze"

### Trasa Ziomka
```
🚖 16:32 — start (📍GPS)
🍕 16:35 — Naleśniki Jak Smok
🍕 16:43 — Sushi Rany Julek & Pizza Majstry
🍕 ~16:52 — Goodboy ← TA (nowy pickup)
📍 ~16:58 — Kaczorowskiego 14/222
📍 ~17:05 — Wyszyńskiego 4a/19
📍 ~17:12 — Wyszyńskiego 8c/14 ← TA (nowy drop)
```

### Dystanse OSRM (kluczowy fragment Sushi → ostatni drop)
- Sushi → **Goodboy = 3.28 km / 5.7 min** ← czysty detour na S (Goodboy 0.018° lat poniżej całego klastra)
- Goodboy → Kacz 14 = 1.44 km / 2.4 min
- Kacz 14 → Wysz 4a = 1.33 km / 2.7 min
- Wysz 4a → Wysz 8c = 1.40 km / 3.5 min (one-way street loop)
- **Σ = 7.45 km / 14.3 min**

### Trasa preferowana Adriana (Sushi → Wysz 4a → Kacz 14 → Goodboy → Wysz 8c)
- Sushi → Wysz 4a = 2.28 km / 5.2 min
- Wysz 4a → Kacz 14 = 2.26 km / 4.4 min
- Kacz 14 → Goodboy = 2.03 km / 3.2 min
- Goodboy → Wysz 8c = 2.01 km / 3.8 min
- **Σ = 8.58 km / 16.6 min** ← +1.13 km / +2.3 min vs Ziomek

### Bag times
| Order | Pickup | Drop | Bag time (Ziomek) | Bag time (Adrian) |
|---|---|---|---|---|
| Naleśniki → ? | 16:35 | ~16:58 (Kacz?) | 23 min | ~14 min (jeśli drop Kacz po Wysz 4a) |
| Sushi → ? | 16:43 | ~17:05 (Wysz 4a?) | 22 min | ~14 min |
| **Goodboy → Wysz 8c** | **16:52** | **~17:12** | **20 min** | **~8 min** |
| Σ bag time | | | **65 min** | **~36 min** |
| max bag time | | | **23 min** | **~14 min** |

### Diagnoza
**ROOT CAUSE: BUG A** (`ENABLE_BAG_TIME_FAIRNESS_SCORING=0`, shadow only).
Solver minimalizuje `total_min` (suma jazdy) i przy tym celu **Ziomek wygrywa** o 2.3 min. Ale brakuje:
- `Σ bag_time` — Adrian -29 min (45% redukcja)
- `max bag_time` — Adrian -9 min (40% redukcja)
- `FIFO weight` — bez tego Goodboy (najmłodszy) ma najdłuższy bag time = anty-FIFO

### Expected po flip BUG A
Po replay calibration z wagami `BAG_TIME_SUM_PENALTY_PER_MIN=1.0` + `BAG_TIME_MAX_PENALTY_PER_MIN=0.7`:
- Ziomek score: `-65*1.0 - 23*0.7 = -81.1` pkt z bag time bonus
- Adrian score: `-36*1.0 - 14*0.7 = -45.8` pkt
- Adrian wygrywa o 35.3 pkt → solver wybierze trasę Adriana

### Replay validation oczekiwany delta
- `current_choice == Ziomek_route` BASELINE (BUG A=OFF)
- `treatment_choice == Adrian_route` po flip (BUG A=ON, wagi calibrated)
- DELTA: +1 zmiana wyboru / -29 min Σ bag_time / -9 min max bag_time

---

## CASE #11 — Jakub OL K-370 · Pani Pierożek → Transportowa 2d/128 (ALERT)

**Status:** 🔴 ALERT — wymaga decyzji
**Trigger Adriana:** „retrospekca... jedzie na piaski po panią pierożek i potem spowrotem na transportową... i jechać z dowozem na 42 pp, słaba trasa"

### Trasa Ziomka
```
🚖 13:45 — start (📍GPS)
🍕 13:54 — Hacienda Pizza
📍 ~13:59 — Szarych Szeregów 9b/2
🍕 14:16 — Retrospekcja
🍕 ~14:20 — Pani Pierożek ← TA (nowy pickup)
📍 ~14:28 — Transportowa 2d/128 ← TA (nowy drop)
📍 ~14:48 — 42 Pułku Piechoty 72G
```

### Geografia (po lat-lon, NIE po intuicji „Nowe Miasto")
- Retrospekcja na **Wiejskiej 65 (Bojary, 53.1146)** — wbrew temu co Adrian napisał, NIE jest na Nowym Mieście
- 42 PP 72G na **Wysokim Stoczku (53.141, 23.227)** — FAR NE, 8.7 km od Transportowej
- Transportowa 2d na **Antoniuku S (53.114, 23.128)** — SW od Retrospekcji
- Pani Pierożek na **Piaskach (53.124, 23.151)** — N od Retrospekcji

### Dystanse OSRM (od Szarych 9b)
**Ziomek (Szarych 9b → Retrospekcja → Pierożek → Transportowa → 42 PP):**
- 8.7 + 2.4 + 3.7 + 13.4 = **28.2 min / 18.1 km**

**Alt (Szarych 9b → Pierożek → Retrospekcja → Transportowa → 42 PP):**
- 7.8 + 3.1 + 3.3 + 13.4 = **27.6 min / 17.5 km** ← -0.6 km / -0.6 min

### Bag times
| Order | Pickup | Drop | Bag time (Ziomek) | Bag time (alt) |
|---|---|---|---|---|
| Hacienda → Szarych 9b | 13:54 | ~13:59 | 5 min | 5 min |
| Retrospekcja → 42 PP | 14:16 | ~14:48 | **32 min** | ~17 min (jeśli pickup 14:23) |
| **Pierożek → Transportowa** | **~14:20** | **~14:28** | **8 min** | ~6 min |
| Σ bag time | | | **45 min** | **~28 min** |
| max bag time | | | **32 min** ← R6 borderline | **17 min** |

### Detour pickup-not-on-route (BUG B)
Pickup Pierożek wstrzelony między Retrospekcję a Transportową:
- Bez detour: Retrospekcja → Transportowa = 1.75 km / 3.3 min
- Z detour: Retrospekcja → Pierożek (1.52) → Transportowa (2.27) = **3.79 km / 6.1 min**
- **Detour cost: +2.04 km / +2.8 min**
- BUG B penalty (`R5_DETOUR_PENALTY_PER_KM=8.0`, `R5_DETOUR_FREE_THRESHOLD_KM=0.5`):
  - `-8.0 * max(0, 2.04 - 0.5) = -12.32 pkt`

### Diagnoza
**ROOT CAUSE: BUG A + BUG B**.
- BUG B: Pierożek pickup wpada w środek istniejącej trasy z detourem 2.04 km — solver tego nie widzi (penalty OFF)
- BUG A: alt-route ma Σ bag_time o 17 min mniej i max o 15 min mniej — solver nie widzi
- Ziomek wybiera Retrospekcja-first bo Pierożek ready 14:20 vs arrival z Szarych ~14:07 = 13 min `wait_courier` — penalty wait pcha pickup na ostatnią chwilę

### Expected po flip BUG A+B
Solver wybierze Pierożek-first nawet z `wait_courier` penalty 13 min, bo:
- BUG A oszczędność: 17 + 15*0.7 = 27.5 pkt
- BUG B oszczędność: 12.32 pkt (eliminacja detour z env-alt-route)
- Razem ~40 pkt vs ~13 pkt wait_courier penalty → alt-route wygrywa

### Replay validation oczekiwany delta
- BASELINE: Retrospekcja-first (Ziomek)
- TREATMENT: Pierożek-first (alt)
- DELTA: -17 min Σ bag_time / -15 min max bag_time / -2 km detour

---

## CASE #12 — Jakub OL K-370 · Uwędzony Beef → Żeromskiego 3b/u2 (ALERT)

**Status:** 🔴 ALERT, 1 candidate, 🟡 3 w bagu, marker BUG C aktywny w renderze
**Trigger Adriana:** „Sprawdź tez tą propozycję daje uwędzonego przed restrospekcją, ale dlaczego skoro spóźni się do restrospekcji przez to o 16min"

### Trasa Ziomka
```
🚖 13:38 — start (📍GPS)
🍕 13:32 — Rukola Sienkiewicza  (pre-start)
📍 ~13:42 — Proletariacka 19/Loku7
🍕 13:54 — Hacienda Pizza
📍 ~13:59 — Szarych Szeregów 9b/2
🍕 ~14:09 — Uwędzony Beef & Chicken ← TA (nowy pickup)
🍕 14:16⚠️plan~14:32 — Retrospekcja  ← BUG C marker LIVE
📍 ~14:26 — Żeromskiego 3b/u2 ← TA (nowy drop)
📍 ~14:49 — 42 Pułku Piechoty 72G
```

### Marker ⚠️plan~14:32 = BUG C marker LIVE od 26.05 (commit `e805cdb`)
- Panel commit `czas_kuriera_warsaw` Retrospekcja = 14:16
- Plan ETA z route_simulator_v2 = 14:32
- Divergence = **16 min** > `COMMIT_RENDER_DIVERGENCE_TILDE_MIN=3.0` → marker pokazany
- Marker działa zgodnie z planem: operator widzi „fikcję" commitu

### Geografia detour Uwędzony
- Uwędzony at Słoneczny Stok NW (53.152, 23.088)
- Po Szarych 9b courier byłby 4 min od Retrospekcji (S Bojary)
- Detour do Uwędzony (NW) = +6.7 min, potem powrót Uwędzony → Retrospekcja = 11.8 min
- Suma Szarych 9b → Uwędzony → Retrospekcja = **18.5 min** vs Szarych 9b → Retrospekcja = **8.7 min**
- Detour pickup = +9.8 min / +5.9 km

### Bag times analyzed (anchor = `per_order_delivery_times` POD)
| Order | Pickup commit | Plan pickup | Drop | POD bag time |
|---|---|---|---|---|
| Rukola → Proletariacka | 13:32 | — | ~13:42 | 10 min |
| Hacienda → Szarych 9b | 13:54 | — | ~13:59 | 5 min |
| Retrospekcja → 42 PP 72G | **14:16** | **14:32** | ~14:49 | **17 min (z plan)** lub **33 min (z commit)** |
| Uwędzony → Żeromskiego | 14:09 | 14:09 | ~14:26 | 17 min |

Retrospekcja POD = `(42PP_delivery_time - pickup_ready_at)` = `14:49 - 14:16 = 33 min` (anchor = pickup_ready_at, czyli commit). **POD = 33 min** ≤ R6 35 min → **BUG E hotfix R6 NIE odpala** (33 < 35). Stąd PROPOSE, nie KOORD.

### Alt routes (oba złe)
**Alt A: Retrospekcja-first (Szarych 9b → Retrospekcja → Uwędzony → Żeromskiego → 42 PP):**
- 8.7 + 11.7 + 11.5 + 14.8 = **46.7 min** (vs 37.6 obecnie)
- Retrospekcja bag time = 14:16 → 14:55 = **39 min** ← R6 BREACH
- Uwędzony arrival = 14:28 vs ready 14:09 = **19 min cold food**

**Alt B: skip Uwędzony, weź nowego kuriera dla niego:** może być najlepsze — to KOORD job.

### Diagnoza
**ROOT CAUSE: BUG C — verdict gate brakuje** (marker LIVE ale verdict still PROPOSE).
- BUG E hotfix nie łapie (POD 33 < 35)
- Marker pokazuje operatorowi „fikcję" ale system nie eskaluje do KOORD
- Pełna fixja: dodać `commit_divergence > N_min → KOORD` gate (TO IMPLEMENTOWANE w tej sesji)

### Expected po commit_divergence verdict-gate (default 10 min)
- Retrospekcja commit=14:16, plan_eta=14:32, divergence=16 min > 10 min próg
- → verdict=KOORD (zamiast PROPOSE)
- Operator dostaje banner: „Retrospekcja: commit 14:16, plan 14:32, divergence 16 min — przepisz do koordynatora"

### Replay validation oczekiwany delta
- BASELINE: PROPOSE (Ziomek wybiera Uwędzony-first)
- TREATMENT: KOORD (commit_divergence gate odpala dla Retrospekcji)
- DELTA: -1 PROPOSE / +1 KOORD (eskalacja zamiast fikcyjnego commitu)

---

## Validation harness — jak zmierzyć po flipie

### Faza 4 replay (BUG A+B):
```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2
# Baseline (flagi OFF):
ENABLE_BAG_TIME_FAIRNESS_SCORING=0 ENABLE_R5_PICKUP_DETOUR_PENALTY=0 \
  python3 tools/sequential_replay.py --rolling --start 2026-05-27 --duration-h 24 \
  --output replay_2026-05-27_baseline.json
# Treatment (flagi ON):
ENABLE_BAG_TIME_FAIRNESS_SCORING=1 ENABLE_R5_PICKUP_DETOUR_PENALTY=1 \
  python3 tools/sequential_replay.py --rolling --start 2026-05-27 --duration-h 24 \
  --output replay_2026-05-27_treatment.json
# Compare oba — KPI: Σ bag_time delta, max bag_time delta, FIFO violations delta, R6 breach delta
python3 tools/compare_replay.py replay_2026-05-27_baseline.json replay_2026-05-27_treatment.json
```

### Faza commit_divergence gate (TO BE IMPLEMENTED in next file):
```bash
# Baseline (flag OFF):
ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE=0 python3 tools/sequential_replay.py ...
# Treatment (flag ON, próg 10 min):
ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE=1 COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN=10.0 \
  python3 tools/sequential_replay.py ...
# Expected: Case #12 baseline=PROPOSE, treatment=KOORD
```

### Targeted check tych 3 case'ów
Jeśli mamy `shadow_decisions.jsonl` z 27.05 zawierający te 3 propozycje, można zrobić punktowy replay tylko dla nich:
```bash
python3 tools/replay_specific_orders.py \
  --order-ids "$(grep -E '(Goodboy|Pierożek|Uwędzony)' /var/log/dispatch-shadow.log | jq -r .order_id)" \
  --flag ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE=1
```

---

## Cross-ref

- Sprint plan bugów: `eod_drafts/2026-05-26/SPRINT_PLAN_geometry_fairness_bugs.md` (CASE A-G)
- BUG E hotfix: `dispatch_pipeline.py:3320-3373`, commit `b61fe66` + pod-anchor `8293ac8`
- BUG A+B shadow flagi: `common.py:1641-1670`
- BUG C marker: `telegram_approver.py:944-1025`, commit `e805cdb`
- Lekcje cross-ref: `lessons.md` #144 (scoping metryk), #145 (commit-priority maskuje plan ETA), #146 (best_effort R6 breach = KOORD), #147 (funkcja celu Σ+max bag_time + FIFO), #148 (gate anchor POD nie raw plan.pickup_at)
- Memory pointer: `MEMORY.md` "🔴 SPRINT GEOMETRY/FAIRNESS BUGS" sekcja
