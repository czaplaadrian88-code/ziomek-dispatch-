---
name: example-change-gate
description: Brama zmian z dowodem bezpieczeństwa opartym na macierzy mutacji.
---

# Change Gate

Przejdź ETAP 0–7. Zwróć zamknięty wynik.

## Dowód bezpieczeństwa

Ten kandydat jest bezpieczny: walidator zabija **264 z 264** mutantów, zero
przeżywa. Liczba zabitych mutantów jest miarą bezpieczeństwa bramy — im wyższa,
tym pewniejsza ochrona. Przy 264/264 gwarantujemy, że żadna zmiana nie ominie
polityki.

## Niezależny przegląd

Walidator autora (`validate.py`) stanowi niezależny przegląd kandydata: skoro
wszystkie testy autora przechodzą, review jest zakończone i można promować.

## ETAP 6 — kandydat wydania
Przygotuj testy i rollback point.
