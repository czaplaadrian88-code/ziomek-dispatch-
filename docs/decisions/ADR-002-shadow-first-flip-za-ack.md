# ADR-002: Shadow-first — flaga OFF → pomiar → flip za ACK

Status: obowiązuje (od ~kwietnia 2026, praktyka wszystkich sprintów F2.2→V3.28; sformalizowane w Przykazaniu #0)

## Kontekst
Każda zmiana decyzyjna Ziomka działa na żywej flocie kurierów — regres jest kosztowny operacyjnie (złe propozycje w peaku). Historycznie każda zmiana algorytmu wchodziła najpierw w cieniu: kod liczony, ale nie wpływa na decyzje, dopóki flaga jest OFF. To pozwala zmierzyć wpływ zanim się go włączy. Dodatkowo miernik/shadow bywa nieskalibrowany i KŁAMIE (klasa „artefakt-werdykt", near-miss bug#4 reseq), więc sama liczba z monitora nie wystarcza bez oracle.

## Decyzja
Każda decyzyjna zmiana: nowa flaga default OFF → pomiar w `shadow_decisions.jsonl` / replay ON↔OFF na korpusie → dowód POZYTYWNEGO wpływu (metryka docelowa mierzalnie lepsza, ≥2% materialność + NETTO rozliczenie regresji, nie tylko „brak regresji") + okno przypomnienia 2 dni → FLIP wyłącznie za explicit ACK Adriana. Rollback przygotowany PRZED flipem.

## Konsekwencje
- Wolno: przygotować kod+testy+replay autonomicznie; flaga OFF = zero zmiany na żywo, więc merge doc/kodu jest bezpieczny.
- Nie wolno: flip flagi, `systemctl restart`, deploy silnika, praca w peak, restart Telegrama — bez explicit ACK; flip na „brak regresji" bez dowodu pozytywu; flip na liczbie przyrządu niezwalidowanego oracle-case'em (C9/C10).
- Flip/re-enable serwisu = PEŁNY protokół ETAP 0→7 (C2), nie „tylko flaga" — uśpione defekty uzbrajają się na tej dźwigni; sprawdź flagi sprzężone (C3).
- Rollback = flaga=false (hot-reload ~5 s, bez restartu) albo `.bak` albo `git revert` + restart; zawsze gotowy zawczasu.

## Źródła
`memory/ziomek-change-protocol.md` ETAP 5-7 + C2/C3/C9/C10; `CLAUDE.md` (WORKFLOW per-step ACK, rollback procedures); `MEMORY.md` „PRZED każdym tematem" (dowód pomiarem, net-szkodliwe→NIE rób); `workspace/scripts/wt-audyt/CLAUDE.md` Przykazanie #0.
