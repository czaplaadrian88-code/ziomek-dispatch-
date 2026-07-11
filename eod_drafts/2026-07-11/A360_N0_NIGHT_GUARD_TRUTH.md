# A360-N0 NIGHT-GUARD-TRUTH — raport source-only

Data: 2026-07-11 UTC

Branch: `quality/a360-n0-night-guard-truth`

Worktree: `/root/a360_n0_wt/dispatch_v2`
Zamrożona baza: `307242d44080d98dd38143d5feae9304f9198a30`

Implementacja i ten raport: commit `f6a2e4e` (jawny 6-plikowy pathspec), push
`origin/quality/a360-n0-night-guard-truth` potwierdzony. Bez merge do mastera.

## Werdykt

Naprawa jest gotowa na branchu jako source/test/runbook, bez zmiany silnika i bez
operacji live. Nocny strażnik nie porównuje już mianownika wyłącznie z poprzednią
nocą. Fail-closed kontrakt przypina dokładne nodeidy oraz oczekiwane klasy
`skip/xfail/xpass`; usunięcie, podmiana nazwy albo nowy niezatwierdzony test jest
czerwone niezależnie od liczby kolejnych nocy.

Dwa bezpańskie `strict=False` food-age zostały zastąpione deterministycznym
kontraktem producer→solver. Finalna suita nie ma XPASS: `5139 passed, 27 skipped,
8 xfailed, 0 xpassed, 0 failed`.

## ETAP 0 i ochrona produkcji

- HEAD/branch/carrier potwierdzone: baza `307242d`, carrier `../flags.json` ma
  mode `0444`, SHA-256 `568436f3de693d048a73bf1a2ba5c23e65191a350ef2e566fa5b6f34ace848bf`;
  carrier nie był edytowany.
- tmux70 jest właścicielem tego worktree. tmux68 pracuje w
  `/root/a360_eta0_wt/dispatch_v2`, tmux69 w `papu_dispatch_bridge`; write-sety są
  rozłączne.
- `dispatch-night-guard.timer` jest active/waiting, następny bieg 12.07 01:15
  UTC. Service zachowuje stary `failed/exit-code`, `NRestarts=0`; nie wykonano
  `reset-failed`, startu ani instalacji.
- Najnowszy aggregate 11.07: 1 confirmed fail na starym HEAD; 10.07: 5 faili.
  Aktualny baseline integratora na tej bazie: `5126/27/8 xfail/2 xpass/0 fail`.
- `atq` zawiera wyłącznie at-214. Ten sprint nie tworzy joba ani timera.
- Zero odczytu sekretów, PII, raw state, EnvironmentFile/environ. Historia
  night-guarda była czytana wyłącznie jako aggregate-only.

Incydent proceduralny C32: jedna pomocnicza kontrola procesu omyłkowo użyła
`pgrep -a` i wyświetliła argumenty wyłącznie własnego procesu pytest oraz
night-guarda. Nie było sekretów ani PII. Metodę natychmiast porzucono; dalsze
kontrole używały tylko PID/`comm`/elapsed. Wspólnego protokołu nie edytowano,
zgodnie z wyłączną własnością integratora.

## Mapa kompletności

| miejsce | rola | writer/consumer | dotknięte | powód | test |
|---|---|---|---|---|---|
| pytest collect | producer nodeidów | `collect_suite` | TAK | dokładny mianownik przed biegiem | slow-shrink, replacement, manifest parity |
| pytest full | producer summary/exit | `run_pytest` | TAK | osobna klasa hard-error | hard-error known-answer, pełne suity |
| plugin pytest | producer outcome per nodeid | night-guard | TAK | skip/xfail/xpass bez payloadów | klasyfikacja + privacy probe |
| manifest v1 | wersjonowany oracle | collect/full/update CLI | TAK | brak pełzającego baseline | hash mutation, audited update |
| history jsonl | writer | `append_history` | TAK | schema v2 i baseline eligibility | hard-error między zielonymi |
| history jsonl | reader | `load_history`, flaky/entropy | TAK | hard-error nie zeruje pamięci | preserved streak/latest good |
| health scoreboard | consumer `verdict/pytest` | `tools/health_scoreboard.py` | N-D | pola legacy pozostają addytywnie | `test_health_scoreboard.py` |
| entropy | producer+trend consumer | `run_entropy`, history | TAK | porównanie do ostatniego valid entropy | dashboard exit 0 |
| flaky streak | history consumer/producer | `main` | TAK | hard-error zachowuje streak | known-answer hard-error |
| unit/timer | uruchomienie | systemd → night_guard | N-D | CLI domyślny kompatybilny, unity bez zmiany | metadata read-only |
| OnFailure | alert consumer | `dispatch-onfailure-alert@` | N-D | exit 1 i semantyka ALERT zachowane | main hard-error returns 1 |
| food-age engine | producer objective | `route_simulator_v2` | N-D | silnik poprawny; naprawiono oracle testu | payload/mutation probe |
| food-age tests | CI consumer | pytest/night-guard | TAK | usunięto losowy majority+strict=False | 10/10 modułu PASS |

## Kontrakt manifestu i historii

`tools/night_guard_suite_manifest.json`:

- `schema_version=1`, `manifest_version=3`;
- baza, owner, powód i UTC aktualizacji są obowiązkowe;
- `nodeids` są posortowane i unikalne, a SHA-256 jest walidowany;
- brak wpisu outcome oznacza dokładnie `passed`;
- stabilne skip/xfail są przypięte po nodeidzie;
- trzy zegarowe testy `test_preshift_window_penalty_2026_06_24.py` jawnie
  dopuszczają `passed|skipped`; nic innego nie ma tej furtki;
- XPASS, failed, error, not-run i każda niezatwierdzona zmiana outcome są RED.

Jawna aktualizacja wymaga zielonej pełnej suity bez XPASS, ownera, powodu i
pełnego base SHA. Niepoprawny istniejący manifest nie może zostać nadpisany.

Historia schema v2 zachowuje istniejące pola dla scoreboardu i dodaje
`suite_contract`, `hard_error` i `baseline_eligible`. Timeout, collect error,
ucięty output, brak summary, rozjazd RC↔summary lub brak aggregate reportu daje
`PYTEST-HARD`, `baseline_eligible=false` i nie zeruje flaky streak. Entropia jest
porównywana do ostatniego wpisu z poprawnym własnym baseline, nie koniecznie do
bezpośrednio poprzedniej linii.

## Food-age root cause i oracle

T2/T5 nie opisywały stabilnego kontraktu. Pięć 200-ms przebiegów OR-Tools
głosowało nad emergentną kolejnością, więc host-load przełączał wynik pomiędzy
XFAIL i XPASS. Właściwy kontrakt to wejście objective:

- OFF nie emituje `delivery_food_age_penalties`;
- override ON emituje dodatnie kary z kotwicą gotowości;
- R6 pozostaje osobnym, niezmienionym boundem;
- znana mutacja `decision_flag` stale-OFF gasi payload i jest wykrywana.

Nie zmieniono solvera, flag, progów, HARD/SOFT ani decyzji produkcyjnych.

## Known-answer i mutation probes

- powolny shrink przez kilka nocy: RED za każdym razem względem manifestu;
- hard-error między zielonymi: RED, nie jest baseline, streak=2 zachowany;
- stała kolekcja i zmiana skip listy: RED po dokładnych nodeidach;
- XPASS non-strict i strict-XPASS/fail: oba RED;
- stary nodeid zastąpiony nowym: równocześnie MISSING + UNEXPECTED;
- jawny update: wersja/owner/reason/base/hash walidowane;
- mutacja hasha manifestu: odrzucona;
- złośliwy `longrepr`: nie trafia do aggregate pluginu;
- food-age stale-OFF: mutation probe odróżnia martwy override.

## Testy i obciążenie hosta

| bieg | przedział UTC | wynik |
|---|---|---|
| targeted baseline food-age | 20:36:38–20:36:46 | 7 pass, 2 XPASS |
| manifest generation DEFAULT pod flock | 20:41:24–20:46:22 | zielony, manifest zapisany; 5138 pass, 27 skip, 8 xfail, 0 XPASS/fail |
| targeted final | po manifest v3 | 20 pass |
| final DEFAULT pod flock | 20:48:12–20:52:50 | 5139 pass, 27 skip, 8 xfail, 0 XPASS/fail |
| final STRICT pod flock | 20:52:58–20:57:23 | 5089 pass, 77 skip, 8 xfail, 0 XPASS/fail |
| post-review final DEFAULT pod flock | 20:59:40–21:04:20 | 5139 pass, 27 skip, 8 xfail, 0 XPASS/fail |
| post-review final STRICT pod flock | 21:04:25–21:08:53 | 5089 pass, 77 skip, 8 xfail, 0 XPASS/fail |

Konserwatywny nowy przedział host-load do sensitivity at-214:
`[2026-07-11T20:41:24Z,2026-07-11T21:08:53Z]`. Integrator powinien dopisać go
do wspólnego handoffu/registry; ten branch zgodnie z promptem ich nie edytuje.

Dodatkowo: py_compile/import PASS, manifest 5170 exact collected nodeids PASS,
`flag_lifecycle_check` 505/505 PASS, `git diff --check` PASS. Entropia bez
pogorszenia: #4=1, #7 live=11, instrument=4.

## Karta wydania — osobny ACK

Ten sprint nie został wydany. Po ACK integrator powinien:

1. odebrać commit i branch, zweryfikować `git show --stat` i carrier;
2. wdrożyć wyłącznie jawny source write-set do produkcyjnego checkoutu;
3. nie instalować unitów (źródła unitów nie zmieniono) i nie restartować usług;
4. nie resetować starego failed ani nie uruchamiać timera ręcznie bez osobnego
   polecenia; kolejny zaplanowany oneshot sam załaduje nowy source;
5. po biegu sprawdzić aggregate schema v2, exit/result oraz alert consumer;
6. zaktualizować wspólny backlog, pamięć i sensitivity at-214.

Rollback source: `git revert <commit>` i ponowny source deploy. Nie ma migracji,
flagi, danych ani restartu do cofnięcia. Historia schema v2 jest addytywna;
starsze wpisy v1 pozostają czytelne.
