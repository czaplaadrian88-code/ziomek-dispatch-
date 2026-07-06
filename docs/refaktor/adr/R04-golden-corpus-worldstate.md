# ADR-R04: Nagrywanie WorldState + macierzy OSRM = golden corpus; replay jako bramka CI

Status: proponowany (implementacja filaru F-6; realizuje decyzję Adriana „shadow+replay = staging")

## Kontekst
Bit-w-bit replay decyzji jest dziś NIEMOŻLIWY: OSRM wołany na żywo (nie nagrywany), brak frozen-clock, `picked_up_at`=proxy w harness, logrotate gubi ~29% okna bez `ledger_io` (raw/01e §5). Werdykty flipów = kontrfaktyczny re-scoring zapisanych kandydatów — wystarczające do flipów wag, ZA SŁABE do testów charakteryzujących rdzenia (nie odtworzą feasibility/sekwencji). Strangler (ADR-R02) wymaga dowodu bajt-parytetu starej i nowej ścieżki — potrzebny wierny korpus wejść.

## Decyzja
Powłoka nagrywa per decyzja: pełny WorldState (flota+zlecenia+FlagSnapshot+kalibracje+now) + macierz czasów OSRM użytą w tej decyzji (rozszerzenie istniejącego `obj_replay_capture`, format ADDITIVE). Replay = czyste `decide(world)` na nagraniu — deterministyczny z konstrukcji. Golden corpus (dobrane przypadki: happy-path, best_effort, czasówka, paczka, KOORD-y, carried-first) = bramka CI: każdy krok stranglera musi dać bajt-identyczne decyzje na korpusie (refaktor) albo jawnie udokumentowaną różnicę z dowodem pozytywnego wpływu (zmiana zachowania — pełny protokół #0 ETAP 5).

## Konsekwencje
- „Staging" przestaje być kontrfaktem — jest wierną re-symulacją; testy charakteryzujące przed każdym krokiem stają się wykonalne (wymóg briefu Fazy 5).
- Koszt dyskowy nagrania — mitygacja: sampling (100% przez okres migracji, potem np. 10%) + GC jak dla innych shadow-jsonl.
- Format nagrania = NOWY plik jsonl (nie zmiana istniejących kontraktów); rotacja przez istniejący mechanizm + odczyt rotation-aware (`ledger_io`).

## Źródła
`02-diagnoza.md` D9; `raw/01e-samouczenie.md` §5 + TOP-5; ADR-002 (shadow-first); protokół #0 ETAP 4-5.
