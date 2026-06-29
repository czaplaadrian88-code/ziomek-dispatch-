# Niezależne deep-dive (CC main, cross-check dla workflow) — 2026-06-17

## Mechanizm potwierdzony niezależnie (3 case'y non-E2, non-coordinator)

### 481156 (16.06, r6_avoidable, REAL miscalibration — MOCNY)
- BEST=179 (pre_shift, **drive_min=63**, new_pickup_late=27.8, r6=37.4) score=**-176.5**
- DOM=531 (pre_shift ale **drive_min=3**, new_late=1.0, r6=15.7, 171 dostaw) score=**-315.7** (NIŻSZY!)
- Dlaczego 179>531 w score: 531 obciążony `bonus_v3273_wait_courier=-53.95` + `v325_new_courier_advantage=-71.75` + r8=-91.5.
  - ⚠️ 531 ma `new_courier_ramp.active=false, deliveries=171` ale dostaje `v325_new_courier_flag "🆕 NOWY advantage -71.8 penalty -50"` → **kara nowego kuriera na WETERANA (171 dostaw)** = podejrzany bug v325.
- Skutek: Ziomek proponuje kuriera **60 min dalej** bo bliski dostał kary wait+new. Outcome: final=529, 66min (179 NIE wzięty).
- Werdykt: **score_miscalibration_REAL**, oś: `bonus_v3273_wait_courier` + `v325_new_courier_advantage` (+ pre_shift drive=63 podejrzane).

### 480169 (12.06, r6_avoidable, MINOR)
- BEST=179 (drive 2.5, r6=28.3 OK, timing_gap_bonus=+25) score=-99.9
- DOM=484 (gps, r6=11.3) score=-108.75; 484 obciążony `bonus_v3273_wait_courier=-44.8`.
- Ta sama oś (wait_courier dominuje R6), ALE best r6=28.3 ≤35 → realnie OK. **minor_noise** (dominacja realna ale bez szkody).

### 480003 (11.06, r6_avoidable, FALSE POSITIVE)
- DOM=457 i 470 obie mają `v326_wave_veto=true` → SŁUSZNIE zdyskwalifikowane. 508 (best) poprawny.
- Werdykt: **hidden_disqualifier** — mój detektor dominacji ma tu false-positive (wave_veto nie jest jedną z 4 osi).

## Wniosek dla raportu
1. **Realny wzorzec ("dziura"):** kary wtórne `bonus_v3273_wait_courier` (idle przy restauracji, R-NO-WASTE) + `v325_new_courier_*` + `bonus_r8_soft_pen` (pickup-span) potrafią PRZEBIĆ pierwszorzędny cel (R6/dystans), powodując wybór dalszego/gorszego kuriera. To „score nie-docenia celu" = w istocie „score PRZE-ceniania kar wtórnych".
2. **Podejrzany bug:** `v325_new_courier_advantage` aplikowany do weterana (531: 171 dostaw, ramp inactive). Wymaga trace kodu v325.
3. **Detektor 140 ma false-positives** (wave_veto, inne *_hard_reject poza 4 osiami) → realny licznik < 140. Workflow (DISQ + adwersarz) odfiltruje. Szacunek realnych: kilkadziesiąt, skupione w pattern wait/new-courier.
4. **Saturacja tło:** mediana assign→delivery 49-56 min cały tydzień; KOORD 51,5%/48% (11-12.06). Wybory podejmowane w realnym przeciążeniu — większość „słabych" propozycji = best-effort w nasyceniu, nie regres.
