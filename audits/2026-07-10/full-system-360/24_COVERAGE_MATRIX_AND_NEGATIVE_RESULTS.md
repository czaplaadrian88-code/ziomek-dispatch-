# Macierz pokrycia i wyniki negatywne

## Pokrycie

| Domena | Kod/mapy | Runtime | Test/oracle | Niezależny review | Ocena |
|---|---|---|---|---|---|
| Sprint 1/2 | pełny discovery | punktowe agregaty | baseline + CAS/FSM testy | SPRI-01 ponownie sprawdzony | PARTIAL |
| Core/decide | discovery | replay verdict | istniejące testy, brak pełnego golden | CORE-01 ponownie sprawdzony | PARTIAL |
| Feasibility/HARD | discovery | flagi + zredagowany kształt rekordu | brak nowego OFF/ON replay | FEAS-01/02 ponownie sprawdzone | PARTIAL |
| Bliźniaki/parytet | discovery | ograniczone | część parity tests | brak pełnej rewalidacji P2/P3 | PARTIAL |
| Trasy/ETA | discovery | agregaty/missy | testy solvera; brak end-to-end app parity | TRAS-01/02 ponownie sprawdzone | PARTIAL |
| Flagi | registry + consumers | efektywny post-flip snapshot | targeted TEST-11 fail | FLAG-01 ponownie sprawdzony | PARTIAL |
| Bezpieczeństwo | boundary code | metadane listenerów/limiterów | brak zewnętrznego skanu/fault | BEZP-01/02/04 sprawdzone | PARTIAL |
| Ops | unity/timery/mapy | świeży status/metadane | bez restart/fault | OPS-01/02/05 sprawdzone | PARTIAL |
| Dane/writerzy | writer map | bez payloadów PII | brak pełnego race harness | DANE-01 cross-repo sprawdzone | PARTIAL |
| Integracje | connection map | punktowo | bez live mutacji | stary single review | PARTIAL |
| Testy/replay/CI | narzędzia + unity | verdicty | 75 testów narzędzi + targeted fail | meta-audyt narzędzi | PARTIAL |
| Docs/ML/deps | mapy/manifests | bez CVE scan | bez leakage/SBOM run | stary single review | PARTIAL |

Discovery objął 12/12 domen. Pakiet ma 110 rekordów: 106 odzyskanych i 4
procesowe. Z 12 pierwotnych P1 jedenaście sprawdzono na aktualnym snapshotcie;
OPS-06 pozostaje refutacją opartą na zatwierdzonym handoffie o kopii kluczy poza
hostem, której nie da się niezależnie udowodnić z tego hosta. P2 nie przeszedł
pełnej ponownej kontroli, a 52 wpisy `UNVERIFIED` nie są faktami.

## Wyniki negatywne — czego nie znaleziono lub co obalono

| Teza | Wynik | Granica dowodu |
|---|---|---|
| nowa inwersja SOFT nad HARD w głównym lejku | nie znaleziona | statyczny call-path + istniejące testy |
| kill-switch legacy na starym listenerze nadal live | REFUTED | listener nieobecny w snapshotcie runtime |
| CAS assign tworzy zawsze lukę do 5 minut | REFUTED | inline redecide przy aktywnej fladze |
| klucze DR istnieją tylko na audytowanym hoście | REFUTED w handoffie | brak niezależnego provider/off-host proof |
| bieżące usługi są w restart-loopie | nie potwierdzono | pięć kluczowych usług miało `NRestarts=0` w chwili odczytu |
| parser v2 ma świeże anomalie/failed eventy | nie potwierdzono | krótki health window, nie gwarancja długoterminowa |
| każda różnica replay wynika z brakującego live input | NIEUDOWODNIONE | 22/23 różnic nakłada się z OSRM miss |
| publiczny katalog floty oznacza nieograniczony brute-force | OVERSTATED | per-IP limiter jest aktywny; disclosure pozostaje |

Wynik negatywny jest datowanym dowodem o określonym zakresie, nie trwałą gwarancją.
