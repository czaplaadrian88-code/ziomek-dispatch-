# A360-DR1A RESTORE-PREP — raport lane'a

Data: 2026-07-11
Wykonawca: tmux 66
Branch: `ops/a360-dr1a-restore-prep`
Base: `e0fd1e49f025a8960b2bfcd533b30a00d8abfc85`
Status: **SOURCE/FAKE ACCEPT; DR1B HOLD; zero operacji realnych**

Integration disposition: źródło zostało przyjęte do wydania master
`a360-wave3-safe-source-integrated-20260711`, ale nie zostało zainstalowane ani
uruchomione live. Przed integracją usunięto bloker C32: test-mode wymaga
jednorazowej atestacji FD; źródła restore/backup wymagają root-only locka,
który nie jest jeszcze provisioned live; sensor konfliktów używa wyłącznie
dokładnego `comm` i cgroup. Targeted STRICT po poprawce: **177/177**.
Operacyjny backup, pozostali producenci i runnery nie
mają jeszcze tego kontraktu, dlatego pełne mutual exclusion, DR1B i service
RTO/RPO pozostają HOLD.

## Problem i wynik

DR0 miał fail-closed artifact/schema restore, provenance, freshness, manifest,
budżety i exact-label cleanup, lecz nadal nie definiował bezsekretowego
carriera, egzekwowanej quota ani app/import/health/start-order. Zielony fake nie
mógł być podstawą realnego game-day.

DR1A dodaje trzy wersjonowane kontrakty:

- jednorazowy carrier podaje wartość wyłącznie przez stdin do openssl; wartość
  nie trafia do argv, env, pliku, logu ani raportu. Fake przyjmuje tylko jawny
  canary i odrzuca złą wartość;
- quota probe atestuje `run_id`, kanoniczny scratch, `enforced`, limit i użycie;
  quota i budżet są niezależne (obowiązuje ciaśniejszy), reserve jest
  obowiązkowa, a probe jest powtarzany przed pierwszą mutacją;
- app probe wykonuje dokładnie import panelu, Papu i dispatchu, health każdego
  komponentu i start-order `postgres,panel,papu,dispatch`.

Realne adaptery mają source-pinned ścieżki `/usr/local/libexec/a360-dr1-*`, ale
nie zostały utworzone ani zainstalowane. Ich brak zamyka realny run fail-closed.
Raport źródła ma schemat `a360-dr1a-restore-prep-report-v1` i zawsze utrzymuje
`dr1b_execution_gate=HOLD`; fake PASS nie awansuje do RTO/RPO.

## Mapa kompletności

| Miejsce | Rola | Writer/consumer | DR1A | Dowód |
|---|---|---|---|---|
| CLI/target | nowy prywatny scratch | operator → restore | TAK | brak nowych opcji live; target direct-child 0700 |
| snapshot | host/tag/path/freshness | restic → verifier | TAK | DR0 zachowany; złe provenance i stale są RED |
| manifest | core/private/unit/nginx | snapshot → verifier | TAK | stałe liczności; brak/decoy/symlink są RED |
| carrier | jednorazowe secret handoff | carrier → openssl stdin | TAK fake | 1 issuance; zły canary RED; brak wartości we wszystkich outputach |
| quota/reserve | twardy scratch limit | probe → capacity guards | TAK fake | contract/run/path/enforced/limit/used + re-probe |
| active backup | zakaz kolizji | host guard → restore | TAK | przed repo, check i pierwszą mutacją |
| SQL | dwa role-specific restore | dump → scratch PG | TAK | `-X`, `ON_ERROR_STOP=1`, single transaction, sentinele |
| app smoke | import/health/start-order | fake app → verifier | TAK fake | 7 etapów w dokładnej kolejności; każdy failure RED |
| cleanup | exact resource ownership | trap → fake Docker | TAK | exact name + scratch label + run_id; foreign label nietknięty |
| real restic/decrypt/DB | game-day | N-D | N-D | jawnie zakazane w fazie A |
| live/systemd/nginx/DNS/ruch | wydanie | N-D | N-D | zero deployu/restartu/aktywacji |
| service RTO/RPO | dowód operacyjny | DR1B → operator | HOLD | source/fake nie dowodzi realnego snapshotu ani usługi |

## Testy i mutation probes

Baseline przed edycją, pod globalnym `flock` i read-only sibling carrierem:

- DEFAULT: **5087 passed, 27 skipped, 10 xfailed, 0 failed** w 224.87 s.

- Combined targeted DR0+DR1A, `HERMETIC_STRICT=1`: **157 passed, 0 failed**
  w 153.61 s.
- Nowy klaster DR1A: **19 passed, 0 failed** (w combined).
- Pełny DEFAULT pod `flock`: **5106 passed, 27 skipped, 10 xfailed, 0 failed**
  w 278.38 s — dokładnie +19 pass względem baseline, lista skip/xfail bez zmian.
- Pełny STRICT pod `flock`: **5056 passed, 77 skipped, 10 xfailed, 0 failed**
  w 260.04 s — dokładnie +19 pass względem baseline Wave 2.
- Dedykowane mutation: **4/4 passed** (strict SQL, carrier, quota, app-stage).
- `bash -n`, `py_compile`, `git diff --check`: PASS.
- `flag_lifecycle_check.py`: **505/505, 0 błędów**.
- `tools/entropy_dashboard.py`: exit 0; brak nowej flagi, progu silnika,
  bliźniaka decyzyjnego ani wzrostu metryk.

Mutation probes mają wykazać:

1. zdjęcie porównania canary przepuszcza złą wartość — negatyw prawdziwego
   źródła pozostaje RED;
2. zdjęcie `quota.enforced is True` przepuszcza fałszywą atestację — test
   znanego wyniku wykrywa fail-open;
3. usunięcie etapu `dispatch_health` daje techniczny PASS, ale exact-order
   oracle pokazuje brak etapu;
4. istniejący DR0 mutation `ON_ERROR_STOP=1 → 0` nadal dowodzi, że strict SQL
   tripwire ma zęby.

## Runtime, rollback i bramka DR1B

Nie czytano sekretów ani realnego repo restic. Nie wykonano restic, decrypt,
Dockera, DB, kontenera, volume, instalacji live, systemd, nginx, DNS, ruchu,
deployu ani restartu. Nie powstał realny target/cache/proces. Rollback kodu to
jawny revert commita DR1A; nie wymaga restartu ani migracji. Fake cleanup usuwa
wyłącznie exact-name + oba labele + dokładny `run_id`; wildcard/prune nie
istnieje.

### Incydent odczytu process-list

Przed przetworzeniem zakolejkowanej korekty bezpieczeństwa lane trzy razy
uruchomił ad-hoc process-list z pełnym polem cmdline, filtrowanym pod katem klas
procesow backup/restore. Widoczny wynik zawieral wyłącznie kanoniczny pytest;
nie wyswietlono sekretu, tokenu, PII ani realnego procesu restic/decrypt/DB.
Sam fakt zamowienia pelnego pola byl jednak naruszeniem granicy — filtr outputu
nie cofa odczytu carriera. Po wymuszeniu korekty lane zobowiazal sie uzywac
wylacznie `comm`, unit/status/cgroup i niewrazliwych metadata; nie wykonano
kolejnej proby. Wspolny close zapisuje liczbe i disposition, a regula C32 w
repo memory zakazuje ad-hoc cmdline/environ. Dedykowany, zreviewowany kolektor
moze redukowac cmdline w pamieci tylko przy negative tescie braku emisji.

### Domknięcie C32 przed integracją

Release nie odziedziczył near-missu jako niedozwolonego odczytu. Źródło restore
nie zawiera `/cmdline` ani `/environ`; raport ma stałe liczniki zero. Złośliwy
fake proc z sekretem w obu carrierach nie jest używany ani emitowany, dokładne
`restic`/`pg_dump`/`pg_basebackup` i cztery unity backupu są RED, a podobne
nazwy nie dają false positive. Mutation usuwające unit dispatch-backup zmienia
werdykt i jest wykrywane przez oracle. Jest to dowód source/fake, nie instalacji
live ani kompletnego pokrycia dowolnego nieopakowanego runnera.

### Werdykt

**DR1B = HOLD.** Brakuje realnego verify provenance/manifest/freshness,
zreviewowanych i zainstalowanych adapterów carrier/quota/app, zatwierdzonego
secret-store, okna niskiego loadu poza sobotnim blackoutem oraz osobnego ACK
wymieniającego encrypted drill. Ten sprint daje GO wyłącznie dla jakości
source/fake DR1A, nie dla wykonania game-day i nie dowodzi realnego RTO/RPO.
