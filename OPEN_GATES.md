# OPEN GATES

> GENERATED — edycja bezcelowa; źródłem prawdy jest kanoniczna baza SQLite.
> Źródło: `/var/lib/ziomek-process-gates/gates.sqlite3`
> Ledger SHA-256: `4b9c557f8750be208bb20267ab46082c83c96c09b89f3bbf2f130fcc66e175b6`
> Stan na: `2026-07-24T19:14:21Z`

Otwarte: **33** | po terminie: **8** | ALARM: **0**

| dni | ID | stan | owner | termin | alarm |
|---:|---|---|---|---|---|
| 49 | audit.fail03-k2 | WAIT_DATA | CTO | 2026-07-25 | — |
| 41 | audit.bug-a | WAIT_DATA | CTO | 2026-07-25 | — |
| 37 | audit.pln-tiebreak | APPLIED | CTO | 2026-08-06 | — |
| 30 | audit.c7-post-shift | WAIT_DATA | CTO | 2026-07-25 | — |
| 13 | audit.data0 | OWNER_ACKED | CTO | 2026-07-26 | — |
| 13 | audit.dr1b | OWNER_ACKED | CTO | 2026-07-26 | — |
| 13 | audit.fsm | WAIT_DATA | CTO | 2026-07-26 | — |
| 12 | audit.host-boundary-hold | OWNER_ACKED | OWNER | 2026-07-23 | — |
| 2 | audit.cid400-pool-absence | APPLIED | CTO | 2026-07-28 | — |
| 2 | control.main-emergency-recovery | READY_FOR_OWNER | CTO | 2026-07-23 | — |

## Kontrola

- Najstarsza: 49 dni / audit.fail03-k2.
- Pominięte z tabeli: 23.
- Kolejność: dni wiszenia malejąco, potem ID rosnąco.
- Terminalne: CLOSED, REJECTED i SUPERSEDED nie są pokazywane.
- ALARM oznacza brak terminalnego wyniku zarejestrowanego at-joba.
- Odświeżenie: `process_debt_gate.py export --format open-gates`.
