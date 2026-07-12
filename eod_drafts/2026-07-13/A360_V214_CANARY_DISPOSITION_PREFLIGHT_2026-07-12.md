# A360-V214 CANARY-DISPOSITION — preflight przed terminem joba

Status: **WAIT/PENDING — TO NIE JEST DISPOSITION**  
Preflight UTC: `2026-07-12T08:37:32Z..2026-07-12T08:39:05Z`  
Canary: `[2026-07-11T10:27:12Z, 2026-07-13T10:27:12Z)`  
Job: `at-214`, termin `2026-07-13T12:15:00Z`

## 1. Wynik dzisiejszego odczytu

O `2026-07-12T08:37:32Z` realne `atq` zawierało wyłącznie:

```text
214  Mon Jul 13 12:15:00 2026  a root
```

Nie istniał żaden prywatny artefakt pod prefiksem
`/root/.openclaw/workspace/dispatch_state/sprint3_canary_20260713*` ani
wcześniejszy output stage/OSRM/paired przeznaczony dla tego werdyktu. Pierwszy
odczyt istniejącego źródła prawdy został więc wykonany; wynik joba jeszcze nie
powstał. Nie uruchomiono joba wcześniej, nie użyto `at -c`, nie utworzono
równoległego joba/timera i nie wykonano paired replay na żywym corpusie.

Wniosek na dziś jest wyłącznie **WAIT/PENDING**. Nie ma podstaw do `GO`,
`HOLD` ani `NO-GO`; R0 `1b38447`, D1 `e193f2a` i H1 pozostają zamrożone.

## 2. Tożsamość i stan read-only

- Worktree: `/root/a360_v214_wt/dispatch_v2`.
- Branch: `evidence/a360-v214-canary-disposition`.
- Frozen base i HEAD:
  `0891b06e9e894d88d6bd8a8b9dd9f837cf12a1e0`.
- Base jest równy `origin/master` z chwili startu i ma tag
  `a360-n0-followup-live-verified-20260712`.
- Tmux 74 jest właścicielem V214. Tmux 58 pozostaje jedynym integratorem i
  FLIPMASTEREM; tmux 75/76 pracują w innych worktree SEC1/E1.
- `dispatch-shadow`, `dispatch-panel-watcher`, `dispatch-sla-tracker` i
  `courier-api` były `active/running`, `Result=success`, `NRestarts=0`.
- Efektywny, zredagowany fingerprint potwierdził
  `ENABLE_STAGE_TIMING_OBSERVATION=1`.
- Parser: `healthy`, v2, `anomaly_detected=false`, `error_count=0`.
- Nie wykonano flipa, restartu, deployu, migracji ani zapisu runtime.

## 3. Freshness i istniejące nośniki

Metadane o `2026-07-12T08:38Z`:

| Nośnik | Mode | Mtime UTC | Ocena preflight |
|---|---:|---|---|
| stage sidecar | `0600` | `2026-07-12T08:11:32Z` | istnieje i ma dzisiejszy przyrost |
| główny shadow ledger | `0644` | `2026-07-12T08:11:07Z` | istnieje; jest mianownikiem reportu |
| world record 12.07 | `0644` | `2026-07-12T08:11:07Z` | istnieje; frozen corpus rośnie |
| `flags.json` | `0600` | `2026-07-11T10:27:12Z` | kotwica startu canary bez późniejszej zmiany |

Luka od ostatniego wpisu do chwili preflight wynosiła około 27 minut. Nie jest
interpretowana jako utrata ani jako zdrowie canary przed końcem okna; job ma
rozliczyć ledgerowy denominator, coverage i grace na pełnym `[start,end)`.

Bezpośredni, lekki OSRM health preflight o `2026-07-12T08:38:54Z` zwrócił
`status=healthy`, `upstream_ok=true`; route/table/nearest były OK. Pola cache i
CB należały jawnie do świeżego procesu reportera (`state_scope=process_local`),
więc nie zostały użyte jako prawda demona.

## 4. Preflight narzędzi i oracla

Zweryfikowane źródła istniejące na frozen base:

| Miejsce | Rola | Writer/consumer | Dotknięte | Powód / test |
|---|---|---|---|---|
| `tools/stage_timing_report.py` | denominator, coverage, p95 | ledger + sidecar / operator | TAK, read-only | ledger kotwiczy `[since,until)`, grace tylko do joina; incomplete nie wchodzi do percentyli |
| `tools/paired_flag_replay.py` | oracle OFF/ON | frozen WR / operator | TAK, syntetyki | izolowana flaga, aggregate-only, oba porządki, critical vs soft, redakcja |
| `tools/osrm_health_report.py` | direct upstream truth | OSRM / operator | TAK, lekki probe | cache/CB jawnie process-local |
| daemon OSRM telemetry | prawda procesu shadow | shadow / job 214 | N-D dziś | output 214 jeszcze nie istnieje; odczytać po terminie |
| prywatne artefakty `sprint3_canary_20260713.*` | jedyne źródło disposition | job 214 / następca | N-D dziś | nie istnieją przed terminem |
| R0/D1/H1 | konsumenci disposition | integrator | N-D | zamrożone; zero merge/flipa/restartu |
| backlog/memory/registry/handover | wspólny handoff | tmux58 | N-D | write-set zastrzeżony dla integratora |

Dokładny lekki klaster syntetyczny:

```text
START_UTC=2026-07-12T08:39:03Z
ZIOMEK_SCRIPTS_ROOT=/root/a360_v214_wt \
PYTHONPATH=/root/a360_v214_wt \
DISPATCH_UNDER_PYTEST=1 \
/root/.openclaw/venvs/dispatch/bin/python -m pytest \
  tests/test_stage_timing_report_zp103.py \
  tests/test_paired_flag_replay_zp103.py \
  tests/test_osrm_health_cache_zp206.py -q
45 passed in 2.14s
END_UTC=2026-07-12T08:39:05Z
RC=0
```

Testy potwierdziły półotwarte okno, boundary grace bez fałszywej straty,
ledgerowy mianownik, jawne missing/orphan/duplicate/incomplete, odrzucenie
partial tick z percentyli, oba porządki wywołania paired, mutation/critical
classification, miss mismatch, redakcję stdout/stderr/logów oraz direct OSRM
health bez zazieleniania cachem. Pełnej suity nie uruchomiono zgodnie z
zakazem pracy przed terminem i w oknie odczytu.

## 5. Rejestr host-load do przyszłego sensitivity

Poniższa konserwatywna lista scala wszystkie jawne interwały przekazane w
registry/handoffach oraz nowy bieg N0 z frozen base. A0 `22:42:43Z..22:43:02Z`
jest lekki, ale pozostaje w provenance i w najbardziej konserwatywnym wariancie
`bez host-load`.

| # | Interwał UTC `[start,end)` |
|---:|---|
| 1 | `2026-07-11T17:03:00Z..18:02:00Z` |
| 2 | `2026-07-11T18:48:54Z..18:52:48Z` |
| 3 | `2026-07-11T19:02:23Z..19:26:23Z` |
| 4 | `2026-07-11T20:24:09Z..20:29:06Z` |
| 5 | `2026-07-11T20:41:24Z..21:08:53Z` |
| 6 | `2026-07-11T21:04:20Z..21:04:29Z` |
| 7 | `2026-07-11T21:08:53Z..21:09:01Z` |
| 8 | `2026-07-11T21:10:30Z..21:15:19Z` |
| 9 | `2026-07-11T21:15:19Z..21:19:47Z` |
| 10 | `2026-07-11T21:46:48Z..21:51:34Z` |
| 11 | `2026-07-11T21:51:43Z..21:56:05Z` |
| 12 | `2026-07-11T22:05:17Z..22:10:00Z` |
| 13 | `2026-07-11T22:10:00Z..22:14:25Z` |
| 14 | `2026-07-11T22:14:56Z..22:19:50Z` |
| 15 | `2026-07-11T22:27:03Z..22:31:48Z` |
| 16 | `2026-07-11T22:31:58Z..22:36:23Z` |
| 17 | `2026-07-11T22:36:47Z..22:41:41Z` |
| 18 | `2026-07-11T22:42:43Z..22:43:02Z` |
| 19 | `2026-07-11T23:05:55Z..23:10:40Z` |
| 20 | `2026-07-11T23:51:50Z..23:53:36Z` |
| 21 | `2026-07-11T23:54:41Z..23:55:03Z` |
| 22 | `2026-07-11T23:57:14Z..23:58:29Z` |
| 23 | `2026-07-12T00:00:44Z..00:05:25Z` |
| 24 | `2026-07-12T00:05:34Z..00:09:59Z` |
| 25 | `2026-07-12T00:10:08Z..00:14:47Z` |
| 26 | `2026-07-12T00:14:55Z..00:19:19Z` |
| 27 | `2026-07-12T00:44:30Z..00:49:15Z` |
| 28 | `2026-07-12T00:49:15Z..00:53:35Z` |
| 29 | `2026-07-12T08:25:10Z..08:30:02Z` — N0 follow-up |

Po złączeniu nakładających się wpisów: 24 interwały, łączna unia 12 392 s =
206,53 min = 7,17% pełnego canary. Ostatni N0 ma prywatny aggregate:
`verdict=OK`, 5155 passed, 24 skipped, 8 xfailed, 0 failed/XPASS,
`contract_ok=true`, manifest v5, czas pytest 277,99 s. To dowodzi natury
obciążenia, ale nie jest wynikiem canary.

Lekki syntetyczny preflight tej sesji `08:39:03Z..08:39:05Z` zachować w
provenance; nie jest pełną suitą ani live replayem i domyślnie nie wymaga
wycięcia z latency sensitivity.

## 6. Dokładna procedura wznowienia po terminie

Wznowić **dopiero po `2026-07-13T12:15:00Z`**:

1. Najpierw wykonać `atq`. Jeżeli 214 nadal istnieje, niczego nie uruchamiać —
   pozostać `WAIT/PENDING`.
2. Gdy 214 zniknie, jako pierwszy odczyt sprawdzić metadata/mode/mtime/size
   `sprint3_canary_20260713*`. Nie czytać surowego corpus przed prywatnymi
   aggregate artifacts.
3. Wymagać wszystkich oczekiwanych plików 0600, zgodnego schema/window i
   freshness po końcu canary. Brak, pusty lub częściowy zestaw = nie zgadywać.
4. Stage report: denominator `expected_rows>0`; coverage
   `matched/expected>=99%`; `missing/orphan/duplicate/incomplete/lost=0`;
   `decision_rows_gap_from_ticks=0`; mismatch/invariant violations=0;
   `service_wall_ms p95<=2500`; append p95 `<=5`.
5. Paired replay: odczytać osobno `first=off` i `first=on`; oba muszą mieć ten
   sam niezerowy `n`, zero errors, zero critical i miss mismatch, a miękki
   `pool_feasible/reason<=1%`. Porównać fieldsets i missy symetrycznie; nie
   uznawać jednego porządku za dowód obu.
6. OSRM: direct upstream oraz godzinny daemon artifact muszą być obecne i
   rozdzielone; process-local CLI nie zastępuje telemetrii demona.
7. Potwierdzić `NRestarts` względem baseline 0 oraz health parsera/OSRM.
8. Na tych samych wejściach policzyć dwa raporty: pełne okno oraz okno po
   odjęciu unii wszystkich zarejestrowanych host-load intervals. Nie zmieniać
   corpus, oracla ani progów między wariantami. Zapisać coverage i p95 obu.
9. Wynik po jobie może być wyłącznie `GO`, `HOLD` albo `NO-GO`, z dowodem.
   `GO` wymaga przejścia wszystkich progów w obu porządkach i odporności na
   sensitivity. Brak/nieświeżość/niepełny denominator albo niejednoznaczna
   sensitivity pozostają `HOLD`; nie zgadywać. `NO-GO` wymaga potwierdzonego
   przez zwalidowany oracle negatywnego wyniku, nie samego braku danych.
10. Zero auto-merge, flipa, restartu lub deployu. Integrator tmux58 aktualizuje
    registry, backlog, memory i dalsze bramki R0/D1/H1.

## 7. Rollback i granice

- Raport/preflight: rollback przez jawny `git revert` commita tego pliku.
- Runtime rollback: N-D — sesja niczego nie zmieniła na żywo.
- Hot rollback canary (`ENABLE_STAGE_TIMING_OBSERVATION=false`) istnieje, ale
  nie jest wykonywany przez tę sesję i wymaga decyzji integratora/ACK.
- Backup danych: N-D; brak zapisu runtime i brak migracji.
- Chronione/cudze pliki, w szczególności
  `CLAIM_LEDGER_HARD_GATE_CARD.md`, `daily_accounting/kurier_full_names.json`,
  backlog, memory, handover i shadow-jobs-registry, pozostały nietknięte.

Stan końcowy tego preflightu pozostaje **WAIT/PENDING**.
