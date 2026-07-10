# Executive summary

## Werdykt

Ziomek ma mocny rdzeń procesu zmian, ale wiarygodność deklaracji nadal ustępuje
wiarygodności kodu i runtime. Największe ryzyko nie wynika z jednego algorytmu,
lecz z trzech klas rozjazdu: zabezpieczenie deklarowane jako HARD ma obejście,
flaga nie steruje faktycznym konsumentem albo przyrząd nie odtwarza pełnego
wejścia decyzji.

Odzyskany audyt zawierał 106 hipotez: 12 P1, 42 P2 i 52 P3 w pierwotnej
klasyfikacji. Po review Claude’a część P1 została obalona lub obniżona. Arytmetyka
w jego syntezie była niespójna z załącznikiem, dlatego ten pakiet zachowuje
status per finding i nie używa jednego „magicznego” totalu. Cztery dodatkowe
wpisy opisują proces odzysku; pakiet ma łącznie 110 rekordów.

## Najpilniejsze klasy ryzyka

1. R6: wyjątek dla gold≤4 i fail-open best-effort wymagają decyzji biznesowej oraz
   osobnego sprintu #0; audyt niczego nie flipuje.
2. Decyzja→plan: walidator i serializacja mogą odcinać plan solvera, po czym
   powierzchnie korzystają z rekonstrukcji inną ścieżką.
3. Replay: nocna bramka wykazała 23 miękkie różnice i 24 missy na 210 rekordach;
   nie może dziś certyfikować pełnego parytetu.
4. Granica API kuriera: autoryzacja własności zlecenia i publiczny katalog floty
   wymagają osobnej weryfikacji oraz naprawy.
5. Ops: kanał człowieka i API kuriera nie mają pełnego, niezależnego alarmowania.
6. Baseline: po flipie parsera bieg default ma TEST-11, a STRICT dodatkowo pięć
   niezakwarantannowanych script-tests czytających live stan; wcześniejsze
   „4847/0” jest przeterminowanym i nieporównywalnym dowodem.

## Co jest zdrowe

- Pięć głównych usług było active/running, bez restart-loopów podczas odczytu.
- Parser v2 raportował `healthy`, brak anomalii i brak failed eventów w ostatniej
  godzinie.
- Claim-ledger CHECK miał zero naruszeń w dostępnej karcie; karta słusznie
  zwróciła WAIT zamiast GO przy zbyt krótkim oknie.
- Formalny FSM pozostaje observerem fail-open; auto-retry nadal nie istnieje, więc
  raporty Sprintu 2 nie udają wdrożenia polityki, której nie ma.
- Sprint 4 domknął rejestr lifecycle flag, hermetyzację i kanoniczną tożsamość;
  audyt znalazł jednak stale-test po późniejszym flipie parsera.

## Czego audyt nie robi

Nie naprawia znalezionych problemów, nie zmienia polityk HARD/SOFT, nie wykonuje
restore drill na produkcji i nie ogłasza 52 wpisów `UNVERIFIED` jako faktów.
Proponowana kolejka jest wyłącznie delta do decyzji Adriana.
