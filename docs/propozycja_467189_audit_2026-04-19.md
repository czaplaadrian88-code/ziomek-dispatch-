# Audit #467189 proposal selection — 2026-04-19 (V3.16)

**Bug @ 15:10:07 UTC:** Propozycja #467189 Rukola Kaczorowskiego → Magazynowa 5/4 wybrała BEST=Mateusz O (cid=413, no_gps, bag=0, score=53.31). Koordynator w panelu natychmiast override'ował na Bartek O. (cid=123, z aktywnym bagiem). Bug szerszy niż pojedynczy case — **PANEL_OVERRIDE rate 19.6% last 1h45min** (18/92 propozycji).

## A. Pipeline view #467189

**Propozycja @ 15:10:07** — top-4 kandydaci (pełen breakdown):

| # | Name | cid | score | pos_source | bag | km_pickup | bl2 | bl3 | penalty_sum |
|---|---|---|---|---|---|---|---|---|---|
| **BEST** | Mateusz O | 413 | **+53.31** | **no_gps** | **0** | 58.14 (synthetic) | None | False | **0** |
| ALT0 | Gabriel | 179 | -96.06 | last_assigned_pickup | 3 | 1.06 | Enklawa | False | -166.63 |
| ALT1 | Michał Rom | 520 | -154.96 | last_assigned_pickup | 3 | 8.16 | Rany Julek | True | -269.45 |
| ALT2 | Michał Li | 508 | -216.08 | gps | 3 | 6.86 | Chinatown Bistro | True | -326.52 |
| ALT3 | Dariusz M | 509 | -218.91 | last_picked_up_delivery | 3 | 4.17 | None | False | -264.24 |

**Actual:** Bartek O. (cid=123) — NIE w top-4. Bartek miał 3 świeże assignments @ 14:59:15-17 (bag=3), prawdopodobnie feasibility R4/R5 go odcięła.

## B. Skala PANEL_OVERRIDE (last 1h45min)

- **18 PANEL_OVERRIDE events** / 92 propozycji = **19.6% override rate**
- Top-proposed cidy (nadpisani):
  - cid=413 **Mateusz O: 7× (avg score +64.8)** — pozytywne scory ale koordynator odrzuca
  - cid=508 Michał Li: 5× (avg -115)
  - cid=441 Sylwia L: 3× (avg +10)
  - Inne: 3×

Top BEST w całym 92-propose window: **cid=508 Michał Li 62×**, **cid=413 Mateusz O 18×**. Razem 80/92 = 87% propozycji ma "dziwnych" BEST.

## C. Pattern matching

### C.1 pos_source axis
- Mateusz O **konsekwentnie no_gps** w każdym override
- Inne top-proposed: mixed (508 gps, 441 has GPS sometimes)
- **Główny czynnik differentiating**: no_gps fallback = synthetic BIALYSTOK_CENTER pos + `travel_min=max(15, prep_remaining)` (dispatch_pipeline L585-611)

### C.2 bag axis
- Mateusz O zawsze **bag=0** w PANEL_OVERRIDE propozycjach
- Actual pick zawsze **bag>=1** (Gabriel 3, Bartek 3, inni z aktywnymi)
- Koordynator **konsekwentnie wybiera kurierów z bagiem** dla bundling

### C.3 score axis
- Mateusz O baseline: **4 komponenty × weights = ~82** bo:
  - s_obciazenie(bag=0) = 100 × 0.25 = **25**
  - s_kierunek(empty) = 100 × 0.25 = **25**
  - s_czas(no oldest) = 100 × 0.20 = **20**
  - s_dystans(road_km~3.5) = ~40 × 0.30 = **12**
  - Σ = **82** baseline
- Z timing_gap_bonus -29.19 → **53.31**
- **bonus_penalty_sum = 0** (bag=0 → brak r8, r9, r6 penalty)

- Gabriel baseline: bag=3 → s_obciazenie=40×0.25=10 = total ~30-40 baseline
- Minus bonus_penalty_sum: **-166.63** (r8_soft -28, r9_stopover -24, r9_wait_pen **-114.43**)
- Net score: **-96**

**Asymmetria strukturalna**: scoring.py nagradza empty bag (s_obciazenie=100), ale nie karze "ślepej" pozycji (no_gps). Bag-kurierzy dostają r9_wait_pen ~ -100 za każdą minutę czekania poza free_at window.

## D. Root cause candidates — ranking (sekcja G z prompt)

### **#1 DOMINUJĄCY — H4: no_gps empty bag elevation bez penalty**

**Evidence:**
- 7/18 (39%) PANEL_OVERRIDE z proposed=413 Mateusz O (no_gps)
- Wszystkie z pozytywnymi scorami (+50 do +66)
- Bag-kurierzy z -100 do -300 przez penalty structure
- **scoring.py nie ma penalty dla pos_source=no_gps** — synthetic position dostaje full s_obciazenie=100 + s_kierunek=100 + s_czas=100 = 65 punktów wagi (vs bag-courier ~30-40)
- Scoring.py + dispatch_pipeline F1.7 no_gps fallback (L585-611) daje no_gps artificial travel_min=max(15, prep) — brak real distance punishment

### #2 secondary — H3: bundle "po drodze" bonus niewystarczający

Michał Li ALT2 miał bl2=Chinatown Bistro bl2_dist=0.0 (ten sam pickup!), bl3=True bl3_dev=1.57km. Bundle_bonus=47.9. Ale penalty -326 overwhelming. Nagroda istnieje, karę brak balans.

### #3 secondary — H2: feasibility za ciasna dla "po drodze"

Bartek O. z bag=3 (świeży 14:59) **nie w top-4 alt** — feasibility go odrzuciła (prawdopodobnie R4 bag cap lub R5 pickup_spread). Koordynator jednak uznał go za najlepszego.

### #4 low — H1: missing-new-assignment (V3.15 territory)

Post V3.15 deploy (14:58:50) — V3.15 packs fallback działa. Bartek bag=3 widoczny pipelineowi. To nie H1.

**Selected fix: #1 (H4) przez strategię Opcja C (prompt KROK 2 Opcja C).**

## E. Ryzyka

| # | Ryzyko | Prob | Mitigation |
|---|---|---|---|
| E.1 | Penalty/demotion zepsuje fair proposal dla legitymnego no_gps empty (kurier startuje zmianę) | M | Demotion tylko gdy istnieje feasible GPS/bag kandydat; samotny no_gps → jednak propose |
| E.2 | Kurierzy no_gps z bagami legitymnie delivering → fix ich nie dotyczy (tylko bag=0) | L | Warunek: `pos_source=no_gps AND r6_bag_size==0 AND ma alternatywa` |
| E.3 | C5 wave_scoring może wzmocnić no_gps → konflikt | L | Wave_scoring nie tknięty — dispatch_pipeline post-scoring demotion działa po wave |
| E.4 | Flag kill-switch niewystarczający — logika complex | L | `ENABLE_NO_GPS_EMPTY_DEMOTE=True` default + env override |
| E.5 | Demotion usuwa wszystkie empty no_gps propozycje gdy shift pusty | M | Fallback: jeśli wszyscy candidates są no_gps empty → zostaw, nie degraduj |

## F. Blast radius

### Pliki do zmiany (2 core + 1 test)

1. **`common.py`** (+10 lines) — flag `ENABLE_NO_GPS_EMPTY_DEMOTE=True` + env override
2. **`dispatch_pipeline.py`** (+25 lines) — po sortowaniu candidates, przed final best pick, demote no_gps+empty gdy istnieje GPS+bag kandydat feasible
3. **`tests/test_proposal_selection_v316.py`** (+200 lines) — 6+ testów

### NIE zmieniane

- `scoring.py` — zero zmian (penalty dodawane w dispatch_pipeline post-scoring)
- `feasibility_v2.py` — zero (escalacja boundary)
- `wave_scoring.py` — zero (C5 boundary)
- `courier_resolver.py` — zero

### Testy do aktualizacji

- Baseline 220/220 musi PASS
- 6 nowych testów

### Estimate impact

- PRE: 19.6% PANEL_OVERRIDE w okresie 1h45min
- POST oczekiwany: ~5-10% (demotion redukuje no_gps top-1 w 60-70% przypadków; koordynator rzadziej override)
- Fleet coverage: ~67% propozycji ma proposed=508 Michał Li (nie dotyczony fix — ma GPS+bag), 20% proposed=413 Mateusz O (dotyczy fix)

## G. Ranking (już w D)

Primary: **H4 no_gps empty bag elevation**
Secondary deferred: H3 bundle bonus balance, H2 feasibility R4/R5 dla bundles

## H. Blast radius (w F)

**Fix scope**: 2 pliki core + 1 test file. Ortogonalny do Sprint C. Zero konfliktu V3.12/V3.13/V3.14/V3.15.

Idę do KROK 2 PLAN.
