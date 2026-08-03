# OPEN GATES

> GENERATED — edycja bezcelowa; źródłem prawdy jest kanoniczna baza SQLite.
> Źródło: `process-gates-ledger`
> Ledger SHA-256: `9e1dbcfa165adc7db6056ef405d34fbd2ec24173dd479a9b95e3dc11d30b992c`
> Stan na: `2026-08-03T12:42:53Z`

Otwarte: **96** | po terminie: **62** | ALARM: **2**
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
| 12 | audit.cid400-pool-absence | APPLIED | CTO | 2026-07-28 | — | — |
| 12 | control.main-emergency-recovery | READY_FOR_OWNER | CTO | 2026-07-23 | ŚWIEŻA 2026-07-25 codex-sol-g… | — |
| 12 | engine.czasowka-reclaim-shadow | APPLIED | CTO | 2026-07-24 | ŚWIEŻA 2026-07-25 codex-sol-p… | — |

## Kontrola

- Najstarsza: 59 dni / audit.fail03-k2.
- Pominięte z tabeli: 86.
- Kolejność: dni wiszenia malejąco, potem ID rosnąco.
- Terminalne: CLOSED, REJECTED i SUPERSEDED nie są pokazywane.
- ŚWIEŻA = notatka audytowa nowsza niż ostatnie przejście FSM.
- ALARM oznacza brak terminalnego wyniku zarejestrowanego at-joba.
- Anomalia schedulera na terminalnej bramce pozostaje widoczna do exact cleanup.
- Odświeżenie: `process_debt_gate.py export --format open-gates`.
