# ADR-002: Shadow-first — flaga OFF → pomiar → flip za ACK

Status: obowiązuje (od ~kwietnia 2026; execution-authority boundary doprecyzowana przez ODR-002 2026-07-12)

## Kontekst
Każda zmiana decyzyjna Ziomka działa na żywej flocie kurierów — regres jest kosztowny operacyjnie (złe propozycje w peaku). Historycznie każda zmiana algorytmu wchodziła najpierw w cieniu: kod liczony, ale nie wpływa na decyzje, dopóki flaga jest OFF. To pozwala zmierzyć wpływ zanim się go włączy. Dodatkowo miernik/shadow bywa nieskalibrowany i KŁAMIE (klasa „artefakt-werdykt", near-miss bug#4 reseq), więc sama liczba z monitora nie wystarcza bez oracle.

## Decyzja
Każda decyzyjna zmiana: nowa flaga default OFF → pomiar w `shadow_decisions.jsonl` / replay ON↔OFF na korpusie → dowód POZYTYWNEGO wpływu (metryka docelowa mierzalnie lepsza, ≥2% materialność + NETTO rozliczenie regresji, nie tylko „brak regresji") + okno przypomnienia 2 dni → FLIP wyłącznie za explicit ACK Adriana. Rollback przygotowany PRZED flipem. Eval, shadow i canary wolno prowadzić tylko w zakresie bieżącej karty. Jeśli flip zwiększa execution authority, sam ACK na techniczny flip nie wystarcza: obowiązuje ODR-002 (hash-bound evidence, niezależna weryfikacja, owner-only approval/podpis i deterministic apply).

## Konsekwencje
- Wolno: przygotować kod+testy+replay w izolacji w granicach bieżącej karty. OFF wymaga udowodnionego parity; merge jest osobną operacją integracyjną pod właściwą bramką i sam nie nadaje authority.
- Nie wolno: flip flagi, `systemctl restart`, deploy silnika, praca w peak, restart Telegrama — bez explicit ACK; flip na „brak regresji" bez dowodu pozytywu; flip na liczbie przyrządu niezwalidowanego oracle-case'em (C9/C10).
- Nie wolno: zmienić karty, gate'a, evala lub progu, a następnie użyć tej zmiany do zatwierdzenia własnej promocji. Dokument, flaga, executor, zielony replay ani canary nie są execution authority.
- Flip/re-enable serwisu = PEŁNY protokół ETAP 0→7 (C2), nie „tylko flaga" — uśpione defekty uzbrajają się na tej dźwigni; sprawdź flagi sprzężone (C3).
- Rollback = flaga=false (hot-reload ~5 s, bez restartu) albo `.bak` albo `git revert` + restart; zawsze gotowy zawczasu.

## Źródła
`ODR-002-autonomy-authority-ownership-2026-07-12.md`; `memory/ziomek-change-protocol.md` ETAP 5-7 + C2/C3/C9/C10/C57; `CLAUDE.md` (WORKFLOW per-step ACK, rollback procedures); `MEMORY.md` „PRZED każdym tematem" (dowód pomiarem, net-szkodliwe→NIE rób); `workspace/scripts/wt-audyt/CLAUDE.md` Przykazanie #0.
