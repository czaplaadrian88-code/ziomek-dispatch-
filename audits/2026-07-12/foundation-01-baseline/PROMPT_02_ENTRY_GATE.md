# Brama wejścia do Promptu 02

Werdykt: **READY — tylko read-only discovery North Star i kanonu**.

Ten dokument nie jest Promptem 02 i nie rekonstruuje jeszcze docelowej architektury ani produktu.

## Potwierdzone wejścia

- zamrożony baseline kodu `c7de9f29127851a59507fac92d14f328336afe61`;
- jawna hierarchia zaufania: najnowsza decyzja właściciela produktu → runtime → kod/testy → kanon/ADR/handoff → backlog/docs → historia;
- rozdzielenie źródła, efektywnego runtime i zamierzonej semantyki;
- mapa głównych modułów, procesów, datastore’ów i cross-project boundaries;
- lokalizacja kanonu, pamięci, bieżących handoffów, audytów i materiałów historycznych;
- znane ograniczenia rekonstrukcji GPS/ETA/decyzji i testów;
- jawny rejestr konfliktów i danych wrażliwych.

## Źródła prawdopodobnego North Star i kanonu

Kolejność pracy dla następnego etapu:

1. aktualna jawna instrukcja właściciela i `memory:ZIOMEK_REGULY_KANON.md`;
2. `ZIOMEK_ARCHITECTURE.md`, `ZIOMEK_INVARIANTS.md` i `ZIOMEK_DEFINITION_OF_DONE.md`;
3. branchowe `docs/refaktor/adr/R01..R06` na `a359e909` oraz repo ADR-001..008, z jawnymi driftami;
4. historyczne źródła intencji: `memory:project_overview.md`, `ZIOMEK_MASTER_KB.md`, strategic audit i archiwalny SKILL;
5. kod, testy i efektywny runtime — osobno jako `implemented` i `effective`, nie intencja;
6. backlog, todo, top handoff i shadow jobs jako `current execution`, nie North Star;
7. audyt 360, Sprint 4 i EOD jako materiał dowodowy z SHA, nie automatyczny kanon;
8. stare prompty, archive i `CLAUDE.md:38+` wyłącznie jako historia lub antydowód.

## Konflikty do rozstrzygnięcia

- rozstrzygnięte R6 `35 normalnie / 40 tylko alarm, nigdy klasa` kontra drift dokumentów i miejsc kodu; otwarty trigger/exit alarmu;
- znaczenie „tier”: klasa kuriera kontra poziom eskalacji;
- status fizycznego `core/` kontra ADR-008;
- current truth flag parsera kontra inventory z 10 lipca;
- pojęcia physical pickup, restaurant last-inside, delivery arrival i customer handoff;
- relacja klików operatora, GPS observable i ground truth;
- które fragmenty `ZIOMEK_LOGIC_REFERENCE.md` są jeszcze aktualne;
- kompletność wdrożenia rozstrzygniętego Always-propose (jawny least-damage/ALERT przy istniejącej flocie) oraz jego techniczne granice.

## Pytania biznesowe, które mogą być nieuniknione

1. Czy potwierdzeniem KPI pickup ma być last-inside GPS, przyszły geofence exit, klik czy inne zdarzenie?
2. Czy KPI delivery oznacza arrival pod adres czy potwierdzone przekazanie klientowi?
3. Jakie są dokładne warunki wejścia/wyjścia z alarmu, jego scope i precedencja wobec declared time oraz committed pickup, przy rozstrzygniętym braku wyjątków klasowych?
4. Które ręczne korekty właściciela produktu są obowiązującą regułą, a które incydentalnym wyjątkiem?

## Warunki bezpieczeństwa następnego etapu

- pracować read-only i nie naprawiać konfliktów podczas samej rekonstrukcji;
- zachować baseline `c7de9f2` oraz oddzielnie odnotować późniejszy docs-only `c1b576e`;
- nie kopiować sekretów, nazw, adresów, koordynatów ani surowych decyzji;
- każde twierdzenie oznaczać jako fakt, wniosek, hipotezę albo niewiadomą;
- semantykę produktu opierać na jawnych decyzjach, nie na nazwie stałej czy starym promptcie;
- nie wykonywać flipa, migracji, restartu, deployu ani zmiany danych bez osobnego promptu i ACK.

## Granica werdyktu

`READY` nie oznacza gotowości do implementacji ani produkcyjnego wydania. Oznacza tylko, że źródła i konflikty są wystarczająco zmapowane do następnego etapu analitycznego. Do prac zmieniających zachowanie nadal blokują: niepełna reprodukowalność, brak restore proof, brak exact deployed build ID oraz nierozstrzygnięte konflikty kanonu.
