# Peak Day Analysis — 2026-05-10 (Białystok)

**Stan na ~16:05 Warsaw / 14:05 UTC.** 204 ordery, 12 unique kurierów, dzień nigdy wcześniej nie obserwowany pod względem volumenu.

## 1. Volume + przepływ

```
Orders per godzina Warsaw:
  09:00    3
  10:00    7
  11:00   10
  12:00   30
  13:00   39
  14:00   56  ←  peak
  15:00   46
  16:00   13  (in flight)
  ─────────
  Total  204
```

Status końcowy: **delivered 147 / picked_up 20 / assigned 35 / returned_to_pool 2.** Stosunek bag-bundle: 65% pickupów z bag>=2, 35% solo. Dystrybucja bag size @ pickup: bag=1 37%, bag=2 31%, bag=3 18%, bag=4 9.5%, bag=5 3%.

## 2. Top kurierzy — kto pracował

| cid | imię | start | end | span | delivered | in_transit | queued |
|---|---|---|---|---|---|---|---|
| 509 | **Dariusz M** | 09:48 | 16:32 | 6.7h | 21 | 1 | 2 |
| 524 | Dawid Cha | 11:05 | 16:28 | 5.4h | 15 | 0 | 3 |
| 484 | Andrei K | 11:09 | 16:26 | 5.3h | 13 | 2 | 2 |
| 179 | **Gabriel** | 12:07 | 16:31 | 4.4h | 18 | 3 | 3 |
| 503 | Gabriel J | 12:24 | 16:31 | 4.1h | 10 | 2 | 3 |
| 123 | **Bartek O** | 13:01 | 16:29 | 3.5h | 16 | 3 | 1 |
| 500 | Grzegorz | 13:02 | 16:21 | 3.3h | 10 | 1 | 2 |
| 508 | Michał Li | 13:05 | 16:34 | 3.5h | 8 | 1 | 4 |
| 413 | **Mateusz O** | 13:11 | 16:32 | 3.4h | 16 | 0 | 7 |
| 400 | Adrian R | 13:14 | 16:33 | 3.3h | 8 | 3 | 1 |
| 409 | Mateusz Bro | 13:25 | 16:31 | 3.1h | 10 | 2 | 4 |
| 520 | Michał Rom | 15:16 | 16:31 | 1.2h | 3 | 1 | 1 |

**Wczesne shift:** Dariusz M startuje 09:48, Dawid Cha 11:05, Andrei K 11:09. **Lunch peak 13:00**: Bartek O / Mateusz O / Mateusz Bro / Michał Li / Adrian R / Grzegorz / Gabriel J — wszyscy zaczynają tłumnie. To ścisła korelacja z **typowym shift Pn-Pt** Adriana.

## 3. R-35MIN-MAX violations — KORE problem dnia

### Real carry (picked_up_at → delivered_at), n=147:

```
  p50=21.6 min  | p75=33.3  | p90=44.0  | p95=51.7  | p99=69.9  | max=72.3
  
   0-15min : 50 (34.0%) ✓
  15-25    : 35 (23.8%) ✓
  25-30    : 18 (12.2%) ✓
  30-35    : 11 (7.5%)  ✓
  ─────────────────────  77.5% pass
  35-45    : 21 (14.3%) ⚠
  45-60    :  7 (4.8%)  🔴
  >60      :  5 (3.4%)  🚨
  ─────────────────────  22.4% violations
```

### Thermal carry (czas_kuriera deklarowany ready → delivered_at), n=152:

```
  p50=27.6 min  | p75=39.1  | p90=55.8  | p95=58.9  | max=78.6
  
  <15      : 33 (21.7%)
  15-25    : 33 (21.7%)
  25-30    : 22 (14.5%)
  30-35    : 14 (9.2%)
  ─────────────────────  67.2% pass (jedzenie ≤35 min od restauracji)
  35-45    : 25 (16.4%) ⚠
  45-60    : 18 (11.8%) 🔴
  60-90    :  7 (4.6%)  🚨
  ─────────────────────  32.9% violations
```

**Thermal jest gorszy od carry** o 10pp — duża część violacji to **opóźnienie pickup**, nie tylko zigzag rozwózki. Jedzenie czeka 30+ min w restauracji bo kurier nie zdąża.

### Top 10 ekstremalne thermal violations dziś:

| thermal | carry | wait@rest | ck | pu | da | kurier | restauracja → adres |
|---|---|---|---|---|---|---|---|
| **78.6** | 72.3 | 6 | 14:26 | 14:32 | 15:44 | Mateusz O | Mama Thai Bistro → Skłodowskiej-Curie |
| **67.0** | 67.4 | 0 | 15:01 | 15:00 | 16:08 | Michał Li | Gym Fit Food → Henrykowo 26 |
| **66.1** | 69.9 | 0 | 14:41 | 14:37 | 15:47 | Andrei K | Kumar's → Kaczorowskiego |
| **65.1** | 45.2 | 19 | 15:26 | 15:45 | 16:31 | Michał Rom | Pruszynka → Chrobrego 6 |
| **65.0** | 51.2 | 13 | 09:35 | 09:48 | 10:39 | Dariusz M | Maison du cafe → Kraszewskiego |
| **62.2** | 62.5 | 0 | 11:06 | 11:05 | 12:08 | Dawid Cha | Bar Merino → lodowa 79a |
| **62.0** | 7.1 | **54** | 10:01 | 10:55 | 11:03 | Dariusz M | Sweet Fit & Eat → Kopernika |
| **58.9** | 44.5 | 14 | 15:02 | 15:16 | 16:00 | Adrian R | Sioux → Kasprzaka 19 |
| **58.6** | 25.7 | 32 | 15:33 | 16:05 | 16:31 | Gabriel J | Rany Julek → Jurowiecka 19 |

Dwa wzorce się rysują:
- **Bundle-zigzag carry** (top 3): Mateusz O 72 min, Michał Li 67 min, Andrei K 70 min — wszystko Trasa Złożona z 4-5 stops, jedzenie z 1. pickupu czeka aż Andrei zrobi cały tour.
- **Pre-pickup wait** (np. Sweet Fit&Eat 54 min czekania): jedzenie ready o 10:01, kurier nie podjechał do 10:55 — restauracja musi sygnalizować że jest gotowe a koordynator nie przypisuje wystarczająco szybko.

## 4. Ziomek vs Operator: 89.3% override

Z 205 propozycji dziś:

- **22 (10.7%)** — operator zgodził się z Ziomkiem
- **64 (31.2%)** — operator wybrał innego kuriera **z** puli Ziomka  
- **121 (58.5%)** — operator wybrał kuriera **całkowicie poza** pulą Ziomka
- **8 propozycji** Ziomek dał verdict=KOORD (zero feasible) — operator znajdował kandydata sam

**Auto_route distribution:**
- ALERT (mass_fail): 182/205 = 89%
- ACK (Ziomek pewny): 23/205 = 11%

**Pool feasible_count distribution (n=205):**
- p25=2, p50=4, p75=5, max=9
- 30% propozycji mają ≤2 feasible kandydatów

Adrian **systemowo nie ufa Ziomkowi** i ma rację: w 58.5% przypadków znajduje lepszego kuriera kompletnie poza Ziomkową pulą. To znaczy że **fast filters Ziomka są za agresywne**.

## 5. Główny wzorzec patologii: ranne propozycje "Bartek O 102.2"

**07:15 - 12:00 UTC:** Ziomek systematicznie proponuje **Bartek O (cid=123)** ze score 80-110 dla niemal każdego ordera. Operator za każdym razem wybiera **Dariusz M, Dawid Cha lub Andrei K**.

**Powód:** Bartek O zaczął realnie pracę o **13:01 Warsaw** (pierwszy fizyczny pickup). Do 13:00 nie miał aktywnego GPS-a, ale **był na grafiku** (Adrian wpisał shift 09:00-22:00 lub szeroko). Ziomek:
- pos_source=`no_gps` → fallback synthetic (BIALYSTOK_CENTER lub last_picked_up_delivery)
- pre_shift_clamp_applied=False
- shift_end z grafiku obecny → R-01 schedule check pass
- R6, R7, R8 dla bag=0 trywialnie pass
- score: ~100 (czysta tabela, bez konkurencji bagu)

**Adrian widzi na panelu**: Bartek O nie ma GPS = nie pracuje fizycznie. Przypisuje Dariuszowi M (który **ma** GPS).

**Brak reguły R-CHECK-COURIER-STATUS** (oznaczona jako "future" w project_overview) jest tu kluczowa.

## 6. Bundling — co Ziomek bundluje + co działa

### Same-restaurant clusters (29% wszystkich bundli):

```
9 orders   Rukola Sienkiewicza
7 orders   Sweet Fit & Eat
6 orders   Chicago Pizza
6 orders   Toriko
5 orders   Restauracja Kumar's
4 orders   Rany Julek
```

### Multi-restaurant repeated combos (top 5):

```
4× Ogniomistrz + Kumar's
3× Goodboy + Mama Thai Bistro + Retrospekcja
3× Grill Kebab + Raj
3× Chicago Pizza + Enklawa
3× Karczma Maciejówka + Mama Thai Bistro + Rukola Kaczorowskiego
```

**Wniosek:** Same-restaurant bundling działa strukturalnie (1 pickup, multi-drop) i powinien być score-faworyzowany. Niektóre cross-restaurant pary (Ogniomistrz+Kumar, Mama Thai+Retrospekcja+Goodboy) powtarzają się 3-4 razy = **stale lokalne korytarze**, można je preferować w bundle scoring.

## 7. Algorytmiczne błędy zaobserwowane DZIŚ

### A. R6 anchor uses TSP-pickup zamiast pickup_ready_at (P0)

`feasibility_v2.py:425-432` — dla orderów `assigned-but-not-picked-yet` używa TSP-projektowanego pickup time jako anchor R6 BAG_TIME. Skutek: **34 min "pass"** w shadow log, **70 min real thermal**. Zjawisko zmasowane: 32.9% delivered orderów ma thermal >35 min, czyli ~50 ordersów dziś z R-35MIN-MAX violation.

### B. R1 / R5 / R8 są SOFT mimo komentarzy "hard block" (P0)

W `feasibility_v2.py`:
- L234 "R1 spread outlier (hard block)" — brak return reject, tylko metric
- L256 "R8 (F2.1c) — pickup_span hard cap" — brak return reject
- R5 mixed-pickup — brak return reject

Przykład 472189: Mateusz Bro miał `pickup_span=53 min > 30`, `deliv_spread=10.09 km > 8`, `pickup_spread=3.16 km > 2.5` — wszystko soft penalty → score -1047, ale verdict=MAYBE. Powinien być twardy NO, wtedy Ziomek widziałby `feas=1` zamiast `feas=2`, czyli VERDICT=KOORD i Adrian dostałby alert "trzeba kogoś nowego" zamiast nonsensownej propozycji.

### C. Brak min_score_threshold dla PROPOSE (P1)

Z dziś: 472189 PROPOSE'owany ze score **-50** (Andrei) gdy alternatywa była **-1047** (Mateusz Bro). Ziomek wybrał "best of bad" zamiast escalować KOORD. Powinno być: `if best_score < THRESHOLD → KOORD reason="all_candidates_low_score"`.

### D. Brak GPS-active gate (P0 dla peak days)

Bartek O ranne 100+ propozycje dlatego że jest na grafiku. Powinno być: `if pos_source in {no_gps, pre_shift, none} AND last_seen_age > 30 min → strong demote (-100) lub exclude`. To dokładnie problem rozwiązywany przez `R-CHECK-COURIER-STATUS` z project_overview.

### E. Mass_fail rate 89% — Ziomek nie autonomous

Auto_route ALERT 182/205 = Ziomek przyznaje "nie wiem" w 89% przypadków. ACK tylko 11%. Faza 7-AUTO-PROXIMITY classifier działa zgodnie z safety, ale to oznacza że **Ziomek dziś nie odciąża operatora**.

## 8. Operacyjne wnioski

### Zbyt mała załoga
12 unique kurierów dla peak 56 ord/h = **4.7 orderów/kurier/h** = **12.7 min/order**. Przy R-35MIN-MAX = 35 min carry to znaczy że **prawie cała załoga jest na 100% capacity** od 14:00 z bag>=2 ciągle.

### Faktyczne start times vs grafik
Ziomek uważał że ma 14 dostępnych kurierów od 07:15 (per pool_total). Faktycznie:
- 09:48: Dariusz M (1 kurier aktywny)
- 11:00: Dariusz M + Dawid Cha + Andrei K (3 aktywni)
- 13:00: 11 aktywnych

Ranna propozycja powinna mieć pool ≤3 dopóki GPS-aktywni nie wzrośnie. Obecne pool=14 jest **fikcją grafiku** vs **rzeczywistym GPS**.

### Operator override pattern
Adrian/Bartek robią 89% nadpisań szczególnie:
- 100% nadpisań ranne (07:15-12:00) — Ziomek proponuje Bartek O, oni biorą Dariusza M / Dawid Cha
- W peak (13-15) ratio override spada (Ziomek bardziej trafny gdy więcej GPS-aktywnych)

To dane dla **Tygodnia 2 calibration Faza 7-AUTO-PROXIMITY** — thresholds T1 muszą uwzględniać GPS-active liczbę, nie pool size.

## 9. Actionable next steps

### P0 (do tygodnia)
1. **R6 anchor fix** — `feasibility_v2.py:425-432`. Dla `o.status != "picked_up" and o.pickup_ready_at`: użyj `pickup_ready_at` jako anchor zamiast `plan.pickup_at`. Test fixture: 472189 powinno być NO.
2. **R1/R5/R8 hard rejects** — przywrócić return path zgodnie z komentarzem. Jeśli soft był celowy (Bartek Gold edge cases), to oznaczyć w nagłówku, ale Mateusz Bro 53/30 = absurd.
3. **GPS-active gate / R-CHECK-COURIER-STATUS** — `pos_source ∈ {no_gps, pre_shift} AND no_recent_picked` → strong demote -100 lub exclude w fast filter. Eliminuje 80% rannego override pattern z Bartkiem O.

### P1 (do końca maja)
4. **min_score_threshold dla PROPOSE** — gdy `best.score < -30` → `verdict=KOORD reason=all_candidates_low_score`.
5. **Same-restaurant bundle bonus** — 27 same-restaurant bundli dziś, top 6 restauracji bundlują 27/152 = 18% wszystkich orderów. Score boost dla pickup z restauracji już mającej order w bagu kuriera.
6. **Telegram render uczciwy** — pokazuj `(spóźnienie X min)` przy każdym pickup time, nie surowy czas_kuriera.

### P2 (długoterminowe, post-Faza 7)
7. **Schedule sanity** — koordynator wpisuje "9-22" ale faktycznie kurier zaczyna 13:00. Auto-detect first-GPS i skracać effective_shift_start.
8. **Bundle pair priors** — uczyć stałe pary restauracji (Ogniomistrz+Kumar, Goodboy+Mama Thai+Retrospekcja) i nadawać bonus.

## 10. Co dziś dało nam najwięcej

**Najtwardsze dane:** 33 violations >35 min carry + 50 thermal >35 min violations = empiryczny dowód że **R-35MIN-MAX w obecnym kodzie nie jest hard rule**. R6 strzela tylko na orderach picked-up z TSP-pickup anchor, nie na assigned-but-not-picked.

**Override 89.3%** = empiryczny dowód że **fast filters odsiewają fałszywie negatywnie** (28% prawdziwie pracujących kurierów wykluczanych z puli).

**Bundling 65%** = peak dnia był obsłużony bundlami przez ~12 kurierów, p95 bag=4. Bartek O i Gabriel taszczyli max bag=5. Bez bundling dziś nie zamknęliby 204 orderów.

**Empirical foundation dla calibration Faza 7 T1 → T2** — thresholdy obecne (min_pool_feasible=2, score_margin=15) są nierealistyczne dla peak day. Po cleanupie P0 rerun calibration z dzisiejszymi danymi jako fixture.
