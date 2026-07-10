# Routing, OSRM, geokod i cache

## Architektura

`route_simulator_v2` buduje plan, `tsp_solver` rozwiązuje PDP/TSP, `osrm_client`
dostarcza route/table z cache, circuit breakerem i haversine fallbackiem.
Strict health musi ominąć te mechanizmy, bo „system zwrócił wynik” nie oznacza
„OSRM jest zdrowy”.

## Główne findings

- `TRAS-01/02`: walidacja metody optymalizacji i serializacja stops tworzą wspólną
  granicę decyzja→plan. Naprawa tylko walidatora mogłaby odblokować zapis planu o
  błędnie odwzorowanych stopach, dlatego muszą być jednym sprintem.
- `TRAS-03`: null duration nie może być teleportem 0 min; bliźniaki powinny użyć
  jednego sentinela/fail-closed.
- `CORE-01`: recorder/replay ma missy OSRM i niepełne wejścia.
- geocode RMW po Sprincie 1 ma stały lockfile i merge; to pozytywny wynik.
- pin-memory fallback wymaga osobnego audytu jakości próbek i bbox, nie tylko
  testu dostępności kodu.

## Negatywne wyniki

W ograniczonym journal window po ostatnim restarcie nie było błędów shadow ani
panel-watchera. Nie wykonano kontrolowanej awarii OSRM; odporność oceniono z kodu
i istniejących testów, więc trust pozostaje częściowy.
