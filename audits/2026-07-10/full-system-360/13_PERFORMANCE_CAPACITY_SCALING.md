# Wydajność, pojemność i skala

## Stan pomiarów

Health scoreboard z 05:30 UTC pokazywał p95 1857 ms przy operacyjnym progu
2500 ms, ale odzyskany audyt cytował późniejsze peakowe p95 ponad 2 s i bardziej
ambitny SLO 1500 ms. Te liczby nie są sprzeczne: różnią się oknem i progiem.
Przyrząd musi zawsze podawać `[since, until)`, próbkę i definicję SLO.

Deterministyczny budżet OR-Tools poprawił ogon bez materialnej zmiany wyniku w
replayu, lecz nie udowadnia usunięcia wszystkich peak spikes. Wcześniejszy sprint
kontencji obalił prostą tezę, że ThreadPool/OSRM są głównym lewarem.

## Ryzyka pojemności

- p95 zależy od liczby kandydatów i kosztu solvera per kandydat;
- cap-Z może enumerować wiele sekwencji dla większego worka;
- duże recordy zwiększają koszt I/O i skracają okna narzędzi ogonowych;
- panel ma długotrwały RSS/swap wymagający profilu heap, nie samego restartu;
- jeden host i współdzielone usługi tworzą korelację awarii zasobów.

## Rekomendacja

Najpierw stage tracing z mianownikiem kanonicznego ledgera, potem budżet per etap i
backpressure. Nie optymalizować `except`, cache eviction ani solvera wyłącznie na
podstawie jednego p95 z innego okna.
