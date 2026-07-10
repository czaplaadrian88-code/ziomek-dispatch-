# Rozbieżności dokumentacja ↔ kod ↔ produkcja

| Deklaracja | Aktualny dowód | Ocena |
|---|---|---|
| baseline 4847/0 po Sprincie 4 | post-flip default 4846/1; STRICT 4792/6 z pięcioma niezakwarantannowanymi live-read script-tests | STALE |
| USE_V2_PARSER = known-open cross-service | JSON jest kanonem; env carrier to cleanup `open` | STALE |
| Audyt 360 ukończony w chwili przerwania Claude’a | istniały 2 raporty bez branchu/pakietu/handoffu; treść pakietu odtworzono później na branchu audytowym | HISTORICAL; CONTENT RECOVERED, RELEASE EXTERNAL |
| OPS-01 aktywna awaria P1 | ryzyko po reboot, obecnie oba listenery | OVERSTATED |
| OPS-02 P1 | luka alarmowania, usługi zdrowe i restartowalne | OVERSTATED |
| FEAS-02 zwykłe PROPOSE feasible | rekordy są `feasibility=NO`, `ALERT` | OVERSTATED |
| CORE-01 diffy przez brakujące pliki | 22/23 współwystępują z OSRM miss | UNPROVEN CAUSE |
| Sprint 2 = auto-retry/FSM enforcement | oba pozostają OFF/niewpięte | FALSE INTERPRETATION |

Rozbieżność jest findingiem, nie pretekstem do cichej korekty kanonu. Gdy dotyka
semantyki biznesowej, właściwy rezultat to jawne pytanie/ACK.
