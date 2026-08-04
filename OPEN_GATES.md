# OPEN GATES

> GENERATED — edycja bezcelowa; źródłem prawdy jest kanoniczna baza SQLite.
> Źródło: `process-gates-ledger`
> Ledger SHA-256: `7b6711582b88b5829d1baabb720fe480cf48ef561293eaaeddabb8b63a5aecb5`
> Stan na: `2026-08-04T08:04:40Z`

Otwarte: **107** | po terminie: **67** | ALARM: **2**
Anomalie schedulera (także terminalne): **0**

| dni | ID | stan | owner | termin | notatka | alarm |
|---:|---|---|---|---|---|---|
| 60 | audit.fail03-k2 | WAIT_DATA | CTO | 2026-07-25 | ŚWIEŻA 2026-07-25 codex-sol-p… | — |
| 52 | audit.bug-a | WAIT_DATA | CTO | 2026-07-25 | ŚWIEŻA 2026-07-25 codex-sol-p… | — |
| 48 | audit.pln-tiebreak | APPLIED | CTO | 2026-08-06 | — | — |
| 41 | audit.c7-post-shift | APPLIED | CTO | 2026-07-25 | ŚWIEŻA 2026-07-25 codex-sol-p… | — |
| 24 | audit.data0 | OWNER_ACKED | CTO | 2026-07-26 | — | — |
| 24 | audit.dr1b | OWNER_ACKED | CTO | 2026-07-26 | — | — |
| 23 | audit.host-boundary-hold | OWNER_ACKED | OWNER | 2026-07-23 | ŚWIEŻA 2026-07-25 codex-sol-g… | — |
| 13 | audit.cid400-pool-absence | APPLIED | CTO | 2026-07-28 | — | — |
| 13 | engine.czasowka-reclaim-shadow | APPLIED | CTO | 2026-07-24 | ŚWIEŻA 2026-07-25 codex-sol-p… | — |
| 13 | engine.uwagi-envelope-v2 | OWNER_ACKED | CTO | 2026-07-25 | — | — |

## Kontrola

- Najstarsza: 60 dni / audit.fail03-k2.
- Pominięte z tabeli: 97.
- Kolejność: dni wiszenia malejąco, potem ID rosnąco.
- Terminalne: CLOSED, REJECTED i SUPERSEDED nie są pokazywane.
- ŚWIEŻA = notatka audytowa nowsza niż ostatnie przejście FSM.
- ALARM oznacza brak terminalnego wyniku zarejestrowanego at-joba.
- Anomalia schedulera na terminalnej bramce pozostaje widoczna do exact cleanup.
- Odświeżenie: `process_debt_gate.py export --format open-gates`.
