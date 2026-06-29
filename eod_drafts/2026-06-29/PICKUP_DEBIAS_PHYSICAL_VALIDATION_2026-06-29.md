# #5 pickup-debias — FIZYCZNA walidacja (29.06, top10 #5)

**Metoda:** join shadow_decisions.best (target_pickup_at raw + target_pickup_debiased) ↔
restaurant_dwell.json (arrived_at_restaurant = FIZYCZNY przyjazd kuriera pod restaurację z GPS).
Skrypt: `eod_drafts/2026-06-29/validate_pickup_debias.py` (read-only). n=58 (świeże okno).

## WYNIK
- bias RAW (physical − predykcja): **med +6,1 / mean +8,7 min** → predykcja odbioru SYSTEMATYCZNIE
  OPTYMISTYCZNA (kierunek debias POTWIERDZONY fizycznie).
- bias po DEBIAS +4,5: med +1,6 min → **stała 4,5 NIEDOSZACOWANA** (fizyka ~6).
- odbiory >5min po obietnicy: RAW 55% → DEBIASED 47% = **−8pp** (NIE „−47%").

## DEFLACJA NAGŁÓWKA (jak #3)
- „−47% spóźnień odbioru" (memory/OOS) = inflacja BUTTON-space (mierzone vs przyciskowy
  `picked_up_at`, sam ~192s przed GPS). FIZYCZNIE korzyść skromna (−8pp).
- Debias dodaje +const WSZYSTKIM kandydatom równo → NIE zmienia wyboru kuriera (ranking shift-
  invariant) → to NIE dźwignia jakości dyspozytorskiej, tylko UCZCIWOŚĆ OBIETNICY restauracji
  (lepsze planowanie prep, mniej fałszywych „spóźniony", potencjalnie świeższe przy odbiorze).

## STATUS
- live-apply NIE istnieje (czysto shadow) → to BUDOWA, P0-adjacent (committed czas_kuriera/R27/
  frozen-pickup/feasibility window). 
- ⚠ n=58 mała próbka, restaurant_dwell „arrived" = przyjazd (kurier może czekać na jedzenie) —
  kierunek robust, exact benefit potrzebuje okna danych.

## REKOMENDACJA
- Jeśli budować: (1) re-kalibruj stałą 4,5→~6 z DANYCH FIZYCZNYCH (restaurant_dwell, większe okno);
  (2) to feature UCZCIWOŚCI OBIETNICY (restauracja), nie quality-dispatch — wartość operacyjna,
  nie breach-reduction; (3) committed-time build = ostrożny sprint + ACK (R27/frozen-pickup/feas).
- Priorytet: NIŻSZY niż sądzono (nie „−47% flip-ready"). Rozważyć przed nim #8 (A2 coeff, tani
  tuning realnego R6) i #10 (EARLYBIRD GO). Decyzja = Adrian.
