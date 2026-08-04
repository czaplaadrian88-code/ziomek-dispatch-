# OPEN GATES

> GENERATED — edycja bezcelowa; źródłem prawdy jest kanoniczna baza SQLite.
> Źródło: `process-gates-ledger`
> Ledger SHA-256: `44c0a218c54743f8209735d710de3fe0fdba04c2d274e667e80a19d16f3b4762`
> Stan na: `2026-08-04T05:47:25Z`

Otwarte: **106** | po terminie: **66** | ALARM: **2**
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
| 13 | engine.czasowka-reclaim-shadow | APPLIED | CTO | 2026-07-24 | ŚWIEŻA 2026-07-25 codex-sol-p… | — |
| 13 | eta.decision-time-log-flip | APPLIED | OWNER | 2026-07-23 | ŚWIEŻA 2026-07-25 codex-sol-g… | — |
| 12 | audit.cid400-pool-absence | APPLIED | CTO | 2026-07-28 | — | — |

## Kontrola

- Najstarsza: 60 dni / audit.fail03-k2.
- Pominięte z tabeli: 96.
- Kolejność: dni wiszenia malejąco, potem ID rosnąco.
- Terminalne: CLOSED, REJECTED i SUPERSEDED nie są pokazywane.
- ŚWIEŻA = notatka audytowa nowsza niż ostatnie przejście FSM.
- ALARM oznacza brak terminalnego wyniku zarejestrowanego at-joba.
- Anomalia schedulera na terminalnej bramce pozostaje widoczna do exact cleanup.
- Odświeżenie: `process_debt_gate.py export --format open-gates`.
