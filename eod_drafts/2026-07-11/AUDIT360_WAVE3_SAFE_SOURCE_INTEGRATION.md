# Audyt 360 — Wave 3 safe source integration — 2026-07-11

Status: **SOURCE/TOOL ACCEPT; runtime activation N-D; D1/R0/DR1B/OPS tuning HOLD**

Wydanie: `a360-wave3-safe-source-integrated-20260711`
Rollback point: `a360-wave3-pre-safe-source-live-20260711` @ `308789a`
Branch integracyjny: `release/a360-wave3-safe-source-live`
Worktree: `/root/a360_wave3_safe_live_wt/dispatch_v2`

## Zakres i rzeczywisty wpływ

Do wspólnej bazy `308789a` zintegrowano wyłącznie dwa rozłączne składniki:

| Zakres | SHA źródłowy | SHA integracyjny | Stan po wydaniu |
|---|---|---|---|
| DR1A restore-prep | `b035523`, korekta `0cfa748` | `309330b`, `930dbea` | źródło/fake w masterze; NOT INSTALLED, NOT EXECUTED; DR1B HOLD |
| OPS0 runtime evidence | `1bb4699` | `daeff60` | ręczne narzędzie read-only w masterze; brak timera/konsumenta; profil UNKNOWN |
| C32 fix-forward DR | N-D | `1cdda89` | brak odczytu argv/environ w restore guardzie; source-only |

D1 `e193f2a` i R0 `1b38447` pozostają poza masterem. `at-214` importuje ich
ścieżki i musi wykonać się na niezmienionym silniku/sędzim; najwcześniejsza
decyzja merge jest po kompletnym ręcznym odczycie joba 13.07 12:15 UTC.

Zmiana nie dotyka selekcji, scoringu, feasibility, planu, flag, danych ani
serializerów decyzji. Nie ma aktywnego importera, unitu, timera, at-joba ani
otwartego uchwytu do nowych plików DR1A/OPS0. Z tego powodu restart nie wczytałby
niczego i byłby wyłącznie ryzykiem.

## Mapa kompletności

| Miejsce | Rola | Writer/consumer | Dotknięte | Dowód/test |
|---|---|---|---|---|
| `restore_from_restic.sh` | source restore | przyszły operator/DR1B | TAK | fake DR0+DR1A, bash-n, mutation |
| `backup_restic.sh` | source producenta backupu | przyszły installer DR1B | TAK | wspólny kontrakt locka; brak instalacji live |
| parent test gate | izolacja fake adapterów | pytest → shell | TAK | odziedziczony FD + comm/exe; zły/brak FD RED |
| host process guard | konflikt heavy-op | comm/cgroup/lock → restore | TAK | exact positive/negative + mutation |
| real backup/restore/adapters | runtime | systemd/operator | N-D | jawnie poza source-only; brak provisionera i adapterów |
| OPS0 tool | read-only evidence | operator ręczny | TAK | 8 testów; brak writerów/timera |
| D1/R0 | silnik i replay at-214 | canary/oracle | N-D | zamrożone do werdyktu at-214 |
| flags/systemd/data | runtime | procesy produkcyjne | N-D | zero zmian i brak konsumenta |

## C32 — root cause i granica dowodu

Pierwotny DR1A otwierał `/proc/$PPID/cmdline` oraz `/proc/*/cmdline`. Fix
`1cdda89` usuwa oba odczyty:

- test-mode wymaga triady env, zgodnego `comm/exe` i jednorazowej atestacji
  przekazanej odziedziczonym FD; sam env nie aktywuje fake'ów;
- źródła restore i oficjalnego backupu wymagają cooperative locka
  `/run/lock/ziomek/heavy-operation.lock` w root-only katalogu, z walidacją
  owner/mode/nlink/size oraz parytetem `dev:ino` ścieżka↔FD;
- sensor czyta wyłącznie dokładne `comm` oraz dokładne nazwy unitów z cgroup;
  błędy inne niż zniknięcie procesu są fail-closed;
- raport ma `command_lines_read=0`, `command_lines_emitted=0` i
  `process_environments_read=0`.

To nie jest pełne mutual exclusion live. Katalog/lock nie są provisioned,
operacyjny `/root/.openclaw/workspace/scripts/backup_restic.sh` jest innym
plikiem bez nowego locka, pozostali producenci nie zostali przepięci, a
niekooperujący `python -m pytest` ma `comm=python`. Te trzy punkty utrzymują
DR1B/LIVE na HOLD. Przed DR1B potrzebne są installer/wrappery, behavior+parity
test backupowego locka oraz realny negative control.

## Testy i parity względem baseline

Carrier flags w worktree: kontrolowany read-only sibling do kanonicznego
`flags.json`; pytest kopiuje go do hermetycznego tmp. Wszystkie ciężkie przebiegi
były serializowane przez `/tmp/ziomek_full_regression.lock`.

Konserwatywne przedziały host-load do sensitivity check at-214:
`[2026-07-11T18:48:54Z,18:52:48Z]` oraz
`[2026-07-11T19:02:23Z,19:26:23Z]`. Drugi przedział celowo obejmuje również
krótkie focused/checker runs pomiędzy zapisanymi testami.

| Bramka | Wynik |
|---|---|
| baseline `308789a` DEFAULT | 5087 passed, 27 skipped, 10 xfailed, 0 failed; 228.46 s |
| DR0+DR1A+OPS0 targeted STRICT | 177 passed, 0 failed; 160.92 s |
| C32 focused po utwardzeniu locka | 12 passed, 0 failed |
| final DEFAULT | 5126 passed, 27 skipped, 8 xfailed, 2 xpassed, 0 failed; 280.94 s |
| final STRICT | 5076 passed, 77 skipped, 8 xfailed, 2 xpassed, 0 failed; 264.73 s |
| lifecycle | 505/505, 0 błędów |
| canon/static selftest | PASS; 10/10 sond KILLED |
| bash-n / py_compile / diff-check | PASS |

Wzrost finalny to dokładnie +39 pass względem wspólnego baseline: +19 DR1A,
+8 OPS0 i +12 C32. Dwa XPASS to istniejące `strict=False` testy food-age
`test_fa_t2_flag_on_delivers_ready_before_unready_pickup` oraz
`test_fa_t5_override_is_the_live_toggle`. Ich własny kontrakt dokumentuje
niedeterminizm OR-Tools 200 ms i majority 5; na baseline były XFAIL, w obu
finalnych przebiegach XPASS. Nie dotykano ich kodu ani flag.

## Operacje live i rollback

Nie wykonano realnego restic, decrypt, Dockera, DB, instalacji adapterów,
provisioningu locka, systemd, timera, `daemon-reload`, tuningu, flipa, migracji,
zapisu danych ani restartu. Chroniony dirty
`eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md` pozostał nietknięty.

Rollback źródła: wrócić do taga
`a360-wave3-pre-safe-source-live-20260711` albo revertować kolejno commit
dokumentacyjny, `1cdda89`, `daeff60`, `930dbea`, `309330b`. Runtime rollback
jest N-D, ponieważ żaden aktywny konsument nie został zmieniony.

Post-release PID/NRestarts/parser/fingerprint zostaną wpisane po fast-forward
mastera; brak restartu jest oczekiwanym wynikiem, nie pominięciem deployu.
