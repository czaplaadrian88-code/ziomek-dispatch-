# OPEN GATES

> GENERATED — edycja bezcelowa; źródłem prawdy jest kanoniczna baza SQLite.
> Źródło: `process-gates-ledger`
> Ledger SHA-256: `2d2e70c628b4a41adf70877dff458069084323d12a3bdba38fedf35371287751`
> Stan na: `2026-08-01T21:59:36Z`

Otwarte: **87** | po terminie: **56** | ALARM: **1**
Anomalie schedulera (także terminalne): **0**

| dni | ID | stan | owner | termin | notatka | alarm |
|---:|---|---|---|---|---|---|
| 57 | audit.fail03-k2 | WAIT_DATA | CTO | 2026-07-25 | ŚWIEŻA 2026-07-25 codex-sol-p… | — |
| 49 | audit.bug-a | WAIT_DATA | CTO | 2026-07-25 | ŚWIEŻA 2026-07-25 codex-sol-p… | — |
| 45 | audit.pln-tiebreak | APPLIED | CTO | 2026-08-06 | — | — |
| 38 | audit.c7-post-shift | APPLIED | CTO | 2026-07-25 | ŚWIEŻA 2026-07-25 codex-sol-p… | — |
| 21 | audit.data0 | OWNER_ACKED | CTO | 2026-07-26 | — | — |
| 21 | audit.dr1b | OWNER_ACKED | CTO | 2026-07-26 | — | — |
| 20 | audit.host-boundary-hold | OWNER_ACKED | OWNER | 2026-07-23 | ŚWIEŻA 2026-07-25 codex-sol-g… | — |
| 11 | eta.decision-time-log-flip | APPLIED | OWNER | 2026-07-23 | ŚWIEŻA 2026-07-25 codex-sol-g… | — |
| 10 | audit.cid400-pool-absence | APPLIED | CTO | 2026-07-28 | — | — |
| 10 | control.main-emergency-recovery | READY_FOR_OWNER | CTO | 2026-07-23 | ŚWIEŻA 2026-07-25 codex-sol-g… | — |

## Kontrola

- Najstarsza: 57 dni / audit.fail03-k2.
- Pominięte z tabeli: 77.
- Kolejność: dni wiszenia malejąco, potem ID rosnąco.
- Terminalne: CLOSED, REJECTED i SUPERSEDED nie są pokazywane.
- ŚWIEŻA = notatka audytowa nowsza niż ostatnie przejście FSM.
- ALARM oznacza brak terminalnego wyniku zarejestrowanego at-joba.
- Anomalia schedulera na terminalnej bramce pozostaje widoczna do exact cleanup.
- Odświeżenie: `process_debt_gate.py export --format open-gates`.
