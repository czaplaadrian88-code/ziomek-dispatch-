---
name: example-change-gate
description: Brama zmian — przygotuj dowody i routing przed zmianą, bez nadawania authority.
---

# Change Gate

Przejdź ETAP 0–7 proporcjonalnie. Zapisz effect_boundary (write_set,
mutation_surface, read_only_no_effect). HARD przed SOFT. Wszystkie pola authority
pozostają false.

## ETAP 0 — baseline
Potwierdź repo, HEAD, worktree, dirty ownership, dowód problemu.

## ETAP 6 — kandydat wydania
Przygotuj py/import checks, testy, exact diff, rollback point, fakt ACK.
Nie instaluj, nie aktywuj, nie wykonuj deployu, restartu, flipa ani migracji.

## ETAP 7 — rollback i handoff
Przygotuj rollback odpowiadający granicy i próbę powrotu.

## Zwróć zamknięty wynik

Wylicz blocker_codes z zamkniętej tabeli relacji, wyprowadź dokładnie jedną
dyspozycję. Statyczny oracle autora oznacz jako AUTHOR_STATIC_ORACLE — nie
promuj go do niezależnego review. Żadna dyspozycja nie oznacza zgody live.
