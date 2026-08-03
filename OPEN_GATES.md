# OPEN GATES

> GENERATED — edycja bezcelowa; źródłem prawdy jest kanoniczna baza SQLite.
> Źródło: `process-gates-ledger`
> Ledger SHA-256: `b2f4c0cb18856d78f96224e5cedb85fffccb45ed01cccacccd09be8dbeb0d8d8`
> Stan na: `2026-08-03T03:10:34Z`

Otwarte: **93** | po terminie: **62** | ALARM: **2**
Anomalie schedulera (także terminalne): **0**

| dni | ID | stan | owner | termin | notatka | alarm |
|---:|---|---|---|---|---|---|
| 59 | audit.fail03-k2 | WAIT_DATA | CTO | 2026-07-25 | ŚWIEŻA 2026-07-25 codex-sol-p… | — |
| 51 | audit.bug-a | WAIT_DATA | CTO | 2026-07-25 | ŚWIEŻA 2026-07-25 codex-sol-p… | — |
| 47 | audit.pln-tiebreak | APPLIED | CTO | 2026-08-06 | — | — |
| 40 | audit.c7-post-shift | APPLIED | CTO | 2026-07-25 | ŚWIEŻA 2026-07-25 codex-sol-p… | — |
| 23 | audit.data0 | OWNER_ACKED | CTO | 2026-07-26 | — | — |
| 23 | audit.dr1b | OWNER_ACKED | CTO | 2026-07-26 | — | — |
| 22 | audit.host-boundary-hold | OWNER_ACKED | OWNER | 2026-07-23 | ŚWIEŻA 2026-07-25 codex-sol-g… | — |
| 12 | engine.czasowka-reclaim-shadow | APPLIED | CTO | 2026-07-24 | ŚWIEŻA 2026-07-25 codex-sol-p… | — |
| 12 | eta.decision-time-log-flip | APPLIED | OWNER | 2026-07-23 | ŚWIEŻA 2026-07-25 codex-sol-g… | — |
| 11 | audit.cid400-pool-absence | APPLIED | CTO | 2026-07-28 | — | — |

## Kontrola

- Najstarsza: 59 dni / audit.fail03-k2.
- Pominięte z tabeli: 83.
- Kolejność: dni wiszenia malejąco, potem ID rosnąco.
- Terminalne: CLOSED, REJECTED i SUPERSEDED nie są pokazywane.
- ŚWIEŻA = notatka audytowa nowsza niż ostatnie przejście FSM.
- ALARM oznacza brak terminalnego wyniku zarejestrowanego at-joba.
- Anomalia schedulera na terminalnej bramce pozostaje widoczna do exact cleanup.
- Odświeżenie: `process_debt_gate.py export --format open-gates`.
