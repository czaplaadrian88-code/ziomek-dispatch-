# Kalibracja score-based selektora czasówek (shadow od 2026-06-10T20:40:00+00:00)
Wygenerowano: 2026-06-12T18:37:02.371310+00:00

- Czasówek z shadow-evalami T-60/T-50: **25** (82 evali)
- would_assign ≥1 raz: **0/25** (0%) — w T-60: 0, w T-50: 0 (cel ≥30%)
- Czasówek które doszły do FORCE_ASSIGN (T-40): 4

## Zgodność wyboru (sb_cid z ostatniego would_assign)
- vs FORCE_ASSIGN T-40 (ten sam silnik później): 0/0 (—)
- vs REALNY kurier (PANEL_AGREE/OVERRIDE): 0/0 (—)

## Powody odrzuceń (per eval)
- score_below_min: 53
- no_maybe_best: 16
- margin_below_min: 8
- wait_above_max: 5

## Rozkłady metryk (per eval)
- score:  n=66 min=-1000000034.5 med=-164.1 max=67.7
- margin: n=64 min=-1000000083.6 med=-183.4 max=708.3  (solo evali: 2; best≠score-top: 40)
- wait:   n=66 min=0.0 med=17.7 max=48.9

## Sensitivity progów (evale przechodzące, score>=30 + R6=0 stałe)
- margin>=15 solo=NO wait<=10 (START): 0
- margin>=10 solo=NO wait<=10: 0
- margin>=5 solo=NO wait<=10: 2
- margin>=15 solo=OK wait<=10: 0
- margin>=5 solo=OK wait<=15: 2
- margin>=0 solo=OK wait<=15: 2

## Per-order szczegół (would_assign=True)
---
## ⚠ NOTA 12.06 18:45 UTC (sesja live-testów) — NIE KALIBROWAĆ PROGÓW Z TEGO OKNA

1. **Skażenie:** 11.06 14:28 → 12.06 18:32 flagi SYNCWORKA (−150) + LOADGOV były LIVE
   (incydent KOORD 15,6%→50%, fix `30a01d2`). ETAP4 = wspólne flagi → silnik czasówki
   liczył score Z karą sync. Mediana sb_score −163,7 w tym oknie ≈ −150 kary.
2. **Znalezisko strukturalne (czyste okno W0 10.06 20:40→11.06 14:28, 27 evali):**
   median sb_score = −125,5, would_assign = 0/27. Nawet bez sync score na horyzoncie
   T-60/T-50 jest głęboko ujemny (kandydaci jeszcze zajęci → kary timing/wait/bag).
   **Próg score≥30 w obecnej semantyce NIE przejdzie nigdy** — KROK 3 wymaga innej
   bazy score (np. projekcja na T-0 / score bez komponentów zależnych od „teraz"),
   nie tylko strojenia progów. Sensitivity (margin≥0, solo OK, wait≤15) → 2/82 evali.
3. **Rekomendacja:** zebrać ≥3-4 dni czystych danych post-fix (od 12.06 18:33) i
   powtórzyć kalibrację; równolegle decyzja projektowa o bazie score do bramki
   (kandydat do E7 at#131 17.06).
