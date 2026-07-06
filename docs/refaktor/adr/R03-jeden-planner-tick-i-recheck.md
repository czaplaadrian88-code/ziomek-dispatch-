# ADR-R03: Jeden Planner (route-sim + feasibility) dla ticku I plan_recheck

Status: proponowany

## Kontekst
`plan_recheck` regeneruje sekwencje przez `simulate_bag_route_v2` **bez** `check_feasibility_v2` — komentarz wprost w kodzie: „nowa sekwencja może być GORSZA R6" (`plan_recheck.py:1019-1020`); ma też własny generator `_gen_one_bag_plan:658` obok `route_simulator_v2._simulate_sequence:559`. Dodatkowo ten sam kod recanon biega pod DWOMA procesami z różnym env (zmierzone 06.07: `ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION=1` tylko u plan-rechecka) → ta sama funkcja, różne zachowanie. To „drugi rdzeń omijający HARD" — sprzeczny z filarem F-2 („plan_recheck przez TEN SAM rdzeń → nie cofa").

## Decyzja
Docelowo (wariant B): wspólny moduł `Planner` = route-sim + feasibility, konsumowany przez tick ORAZ sweep; konfiguracja z WorldState/FlagSnapshot, nie z env procesu (koniec klasy env-rozjazdów dla tej ścieżki). Krótkoterminowo (kroki wczesne planu): (a) bramka feasibility na WYJŚCIU `_sweep` — sekwencja gorsza od starej w R6/committed nie zapisuje się; (b) parytet drop-inów env obu procesów recanon (albo jawna dokumentacja per-proces różnic z powodem).

## Konsekwencje
- Re-sekwencja nigdy nie pogarsza R6 (reguła nr 1 biznesu) — z konstrukcji, nie z monitora.
- Prawdopodobna redukcja oscylacji tras (q2_drift 2-29/d — hipoteza do potwierdzenia pomiarem po kroku (a)).
- Konsoliduje 1 z żywych bliźniaków (generatory planów) — kontrakt ① kanonu.
- Ryzyko przejściowe: bramka (a) może zamrażać plany częściej niż dziś — mierzyć odsetek odrzuconych sekwencji w shadow zanim flip.

## Źródła
`02-diagnoza.md` D4 + pomiar env-diff; `raw/01b-rdzen.md` §1/§6.3; `raw/01d-wspolbieznosc.md` R5; `ZIOMEK_ARCHITECTURE.md` F-2 + rejestr bliźniaków §4.
