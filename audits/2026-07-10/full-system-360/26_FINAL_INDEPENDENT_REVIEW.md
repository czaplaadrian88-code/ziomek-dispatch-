# Finalna niezależna recenzja

## Mianownik

Odzyskano 106 findings Claude’a. Po zachowaniu werdyktu per finding ich aktualny
rozkład to: 46 `CONFIRMED`, 4 `REFUTED`, 3 `PARTIAL`, 1 `PLAUSIBLE` i 52
`UNVERIFIED`. Finalne severity odzyskanych wpisów: 1 P1, 43 P2, 58 P3 i 4
`NONE`. Pakiet dodaje cztery findings procesu odzysku: jeden `PARTIAL/P2` i trzy
`CONFIRMED/P2`, razem 110 rekordów.

Nie należy powtarzać dawnego skrótu `57 confirmed / 8 refuted / 1 plausible` jako
liczby findings. Była to mieszanka głosów reviewerów i nie miała wspólnego
mianownika z 106 wierszami.

## Ponowna kontrola 12 pierwotnych P1

| Finding | Werdykt na snapshotcie | Final | Korekta względem syntezy Claude’a |
|---|---|:---:|---|
| SPRI-01 | REFUTED | NONE | inline redecide jest aktywne; brak stałej luki do pięciu minut |
| CORE-01 | PARTIAL | P2 | niepełny snapshot istnieje, ale 22/23 soft diff ma OSRM miss; aktualna przyczyna nieudowodniona |
| FEAS-01 | CONFIRMED | P1 | jedyny utrzymany P1: konflikt bramki R6 z aktualnym kanonem D3 |
| FEAS-02 | PARTIAL | P2 | wadliwe proxy/unknown są realne; output jest `feasibility=NO/ALERT`, nie zwykłym feasible |
| TRAS-01 | CONFIRMED | P2 | zapis planu solvera odpada, lecz szybki redecide łagodzi lukę czasową |
| FLAG-01 | CONFIRMED | P2 | latentny no-op przyszłego flipa; zamierzony i faktyczny stan są dziś OFF |
| BEZP-01 | REFUTED | NONE | legacy listener nie działa w bieżącym snapshotcie |
| OPS-01 | CONFIRMED | P2 | ryzyko po reboot, nie aktywna awaria; stary proces pozostaje w cgroup |
| OPS-02 | CONFIRMED | P2 | brak bezpośredniego alarmu; `Restart=` łagodzi, ale nie zamyka problemu |
| OPS-05 | PARTIAL | P2 | host bind/backstop potwierdzone; bieżącego Cloud Firewall nie dowiedziono z hosta |
| OPS-06 | REFUTED wg zatwierdzonego handoffu | NONE | kopie kluczy są poza hostem; brak niezależnej możliwości potwierdzenia z tego hosta |
| DANE-01 | CONFIRMED | P2 | cross-repo writer panelu woła `save_plan` bez expected version przy aktywnym carrierze |

Jedenaście z dwunastu pierwotnych P1 ma świeżą ponowną kontrolę kodu/runtime.
OPS-06 pozostaje refutacją z autorytatywnego handoffu, a nie z lokalnego oracle.

## Findings sąsiadujące, które zmieniają plan naprawy

- TRAS-02 jest CONFIRMED/P2 i musi wejść razem z TRAS-01; samo rozszerzenie
  allow-listy odblokowałoby błędnie odwzorowaną kolejność stopów.
- BEZP-02 jest CONFIRMED/P2 samodzielnie; P1 powstaje dopiero jako łańcuch z
  publicznym wejściem. BEZP-04 po uwzględnieniu aktywnego limitera jest P3.
- TEST-11 jest CONFIRMED/P2: assertion jest historyczne, a helper czyta live
  `flags.json`, więc dotychczasowy post-flip baseline nie jest hermetyczny.
- TEST-12 jest CONFIRMED/P2: STRICT ujawnił pięć script-tests czytających live
  stan bez wpisów w aktualnej zewnętrznej kwarantannie.
- AUDIT-01 pozostaje w zamrożonym pakiecie jako PARTIAL/P2: treść i branch są
  odzyskane, natomiast commit, push i trwały handoff są zewnętrzną bramką wydania.

## Werdykt końcowy

Najmocniej udowodniony problem biznesowy to FEAS-01, ale audyt nie daje zgody na
flip ani zmianę HARD. Najmocniej udowodniony problem procesu dowodowego to
TEST-11. Pozostałe wysokie pozycje są P2, PARTIAL albo zależne od łańcucha.
52 `UNVERIFIED` nie może być komunikowane jako „52 kolejne bugi”.
