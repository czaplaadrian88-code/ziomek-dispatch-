# A360 R0 replay-truth — przepisanie na master

Status: **NO-OP KODOWY; R0 już na masterze**. Base `00be30ba`, źródło
`1b38447f`; model_tier=sol, effort=xhigh (HARD/R6).

## Co wnosi źródło i co już zdublowano

Gałąź ma 10 commitów: 6 kod/test i 4 docs; netto 3 tools, fixture, 5 testów i
2 dokumenty. R0 daje 5 rozłącznych klas (`INPUT_MISS`…`PARITY`), stały
mianownik, coverage/freshness, redakcję, fail-closed inputs, OSRM once i paired.

Całe 6 commitów kod/test jest już w `62baa9d1`; dokumenty odpowiadają
`667877db,28b90fc9,f07c7131,d34ea68e`. `git diff 1b38447f..master` jest pusty
dla gate, paired, fixture i obu docs. Pozostałe 6 plików ma wyłącznie nowszą
deltę mastera (197+/21-): siódme wejście `courier_last_pos`, `INPUT_MISSING`,
deterministyczny replay/OR-Tools i A8-2. Port starego hunka by je cofnął.
Diff D1 `3a8d25f4^..3a8d25f4` po ścieżkach R0 jest pusty.

## Mapa kompletności

| miejsce | rola | writer/consumer | dotknięte | powód | test |
|---|---|---|---|---|---|
| world_replay + gate + paired | oracle/verdict | W/C | N-D | `62baa9d1` | R0 cluster |
| world_record/last_pos | frozen input | W | N-D | master silniejszy | capture+redirect |
| ledger_io | ledger | C | N-D | rotation-aware bez zmiany | gate |
| firewall + pipeline + shadow | OD-07 twins | W/C | N-D | rozłączne z R0 | D1 boundaries+E2E |
| ten raport | evidence | C | TAK | dyspozycja no-op | diff/check |

N-D: shadow-jobs-registry — brak nowego joba; zapis poza klonem zabroniony.

## Dowody, ryzyko, rollback

regresja: **99 passed, 0 failed**, py3.12 (venv exec zablokowany), STRICT/tmp,
bez OR-Tools/live I/O.
e2e: replay→gate oraz D1 pipeline→serializer; 35 normalnie, 40 tylko Alarm,
possession→handoff. Default flagi D1=false; runtime wg ownera ON, nietknięty.
bajt-identycznosc: kod bez zmian; zero flag/decyzji/migracji/restartu.
Ryzyko: surplus nagranych OSRM nadal nie jest błędem; pełna suita/merge = CTO.
rollback: revert wyłącznie commita raportu; runtime bez rollbacku.
