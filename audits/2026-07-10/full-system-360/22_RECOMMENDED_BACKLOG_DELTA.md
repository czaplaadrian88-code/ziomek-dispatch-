# Rekomendowana delta backlogu

Poniższe wpisy są propozycją z audytu. Nie stają się kanonicznym backlogiem ani
decyzją biznesową bez triage Adriana.

| Proponowany ID | Priorytet | Źródło | Zakres | Definition of Done | Bramka |
|---|:---:|---|---|---|---|
| Z-AUD-TEST-HYGIENE | P1 jakości | TEST-11/12 | syntetyczne flags+systemd, cleanup `KNOWN_DIVERGENCES`/assertion/lifecycle; pięć script-tests przepiąć na fixture, live smoke wydzielić i dopiero wtedy ewentualnie kwarantannować dokładnym nodeid+powodem | post-migration oracle=`json-overrides-env/open`, `HERMETIC_STRICT=1` pełna suita 0 failed, jawna lista skipów | brak live; osobny commit |
| Z-AUD-R6-D3 | P1 decyzji | FEAS-01 | karta zgodności R6 35 vs gold p80 | zatwierdzona semantyka, OFF/ON replay, rollback hot, 2 dni obserwacji | HARD + flip ACK |
| Z-AUD-BESTEFFORT | P2 | FEAS-02 | unknown/sum proxy i jawny ALERT least-damage | golden cases `None`, per-order, saturation; always-propose zachowane | decyzja biznesowa |
| Z-AUD-PLANBOUNDARY | P2 | TRAS-01/02 | wspólny plan solver→store→powierzchnie | parity kolejności, metod i ETA; test wszystkich writerów | deploy/restart ACK |
| Z-AUD-PLAN-CAS-XREPO | P2 | DANE-01 | panelowy writer pod wspólnym CAS | deterministyczny race test, brak resurrect/lost update | cross-repo release |
| Z-AUD-API-OWNERSHIP | P2 security | BEZP-02/04 | wspólny ownership guard i minimalizacja pre-login | negatywne testy cudzej encji + UX decyzja katalogu | API deploy ACK |
| Z-AUD-REPLAY-ORACLE | P2 | CORE-01 | input/OSRM coverage i mutation tripwire | rozłączne mianowniki, missing-only fail, znany golden | bez flipu decyzji |
| Z-AUD-FLAG-NOOP | P2 | FLAG-01 | rzeczywisty consumer flagi carry-chain | ON≠OFF na fixture, flaga pozostaje OFF, registry/checkery zielone | późniejszy flip osobno |
| Z-AUD-FLOW-LIVE | P2 ops | OPS-02 | liveness panel/API + cisza decyzji w peak | kontrolowany negative control i sprawdzony alert route | restart ACK |
| Z-AUD-SSH-NET | P2 ops | OPS-01/05 | SSH listener ownership + provider/host firewall | druga sesja, provider proof, rollback dostępu | sieć/restart ACK |
| Z-AUD-RESTORE | P1 ops-quality | TOOL-RESTORE | izolowany restore game day | decrypt, strict DB restore, required paths, record counts, smoke, RTO/RPO | backup + izolacja |
| Z-AUD-SBOM | P2 | DEP/SUPPLY | SBOM i constraints per proces | `pip check`, licencje, CVE triage, reproducible env | bez automatycznego upgrade |

## Kolejność zależności

`Z-AUD-TEST-HYGIENE` i oracle instrumentów są fundamentem dowodowym. Następnie można
prowadzić niezależnie: (a) decyzję R6/best-effort, (b) plan boundary+CAS, (c)
API ownership, (d) replay. Prace SSH/firewall i restore są osobnymi oknami ops i
nie powinny dzielić release’u z silnikiem.

## Pozycje bez promocji

52 odzyskane `UNVERIFIED` pozostają w indeksie hipotez. Nie trafiają automatycznie
do backlogu wykonawczego; najpierw reprodukcja z `25_REPRODUCTION_INDEX.md`.
