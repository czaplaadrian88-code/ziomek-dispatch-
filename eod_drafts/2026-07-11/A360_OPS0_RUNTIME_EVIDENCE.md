# A360-OPS0 RUNTIME-SYSTEMD-EVIDENCE

Status: **TECHNICZNIE GOTOWE; POMIAR OKNA = UNKNOWN / NIE-REPREZENTATYWNY**

Branch: `ops/a360-ops0-runtime-evidence`
Zamrozona baza: `e0fd1e49f025a8960b2bfcd533b30a00d8abfc85`

## Werdykt

Powstalo read-only narzedzie, ktore laczy usluge z PID, czasem startu,
`NRestarts`, binarnym interpreterem z `/proc/PID/exe`, efektywnymi
niewrazliwymi properties systemd oraz metrykami procesu i cgroup. Wszystkie 10
objetych uslug bylo `loaded/active/running`, zachowalo PID i mialo
`NRestarts=0` w obu probkach.

Nie ma podstawy do tuningu live. Pierwszy odczyt byl `CONTAMINATED`, bo inny
lane trzymal `/tmp/ziomek_full_regression.lock` i dzialal pytest. Drugi byl
tylko `ELIGIBLE_SINGLE_SAMPLE`. Krotki odstep, sobotni ops-blackout i brak
pelnego, czystego okna oznaczaja, ze reprezentatywnosc pozostaje **UNKNOWN**.
Nie utworzono timera ani at-joba.

## Granica odczytu i prywatnosc

Do systemd trafia jedna komenda per usluga: `systemctl show` z zamknieta
allowlista: stan/load, `MainPID`, start, `NRestarts`, `ControlGroup`,
`FragmentPath`, `DropInPaths`, MemoryCurrent/Peak/Swap/High/Max,
`OOMScoreAdjust`, `Restart` i timeouty. Nie sa zamawiane `Environment`,
`EnvironmentFile`, `ExecStart` ani `ExecStop`.

Dozwolone odczyty kernela:

- `/proc/PID/exe`, `status` (`VmRSS`, `VmSwap`) i `stat` (`minflt`, `majflt`);
- cgroup `memory.current`, `memory.peak`, `memory.swap.current`,
  `memory.pressure` i `memory.stat` (`pgfault`, `pgmajfault`);
- `/proc/locks`, `/proc/loadavg`, `/proc/pressure/cpu` oraz procesowe `comm` i
  `cmdline` tylko do klasyfikacji pytest/mutation. Cmdline jest redukowane w
  pamieci do klasy narzedzia; argumenty i tokeny nigdy nie trafiaja do JSON.

Nie odczytywano tresci unitow/drop-inow, katalogu `/etc`, wartosci inline env,
EnvironmentFile, `/proc/*/environ`, plikow env/sekretow, venv/configu, danych
runtime ani PII. Test negatywny detonuje probe wyjscia poza allowliste i
potwierdza `command_lines_emitted=0`.

## Snapshoty live

| snapshot | timestamp UTC | jakosc | lock | pytest/mutation | reprezentatywny |
|---|---|---|---|---|---|
| S1 | 2026-07-11 17:13:45 | `CONTAMINATED` | held | pytest | NIE |
| S2 | 2026-07-11 17:15:18 | `ELIGIBLE_SINGLE_SAMPLE` | not held | brak wykrytego | NIE |

S1 i S2 byly read-only. W 93-sekundowym wycinku wszystkie PID/start/NRestarts
byly stabilne; `NRestarts` delta = 0. Brak przyrostu cgroup PSI `some/full`.
Pojedyncze major faults wystapily w watcherze i pierwszym backendzie Papu, ale
S1 byl skazony, wiec nie sa podstawa do werdyktu wydajnosciowego.

## Prawda per usluga — punkt S2

Wartosci pamieci sa w MiB. `proc swap` i `cgroup swap` maja rozna semantyke;
rozjazd nie jest bledem sam w sobie. PSI i faults sa licznikami kumulacyjnymi
od startu cgroup, nie porownaniem uslug o roznych czasach zycia.

### Dispatch

| usluga | PID | proc RSS | proc swap | cg current / peak / swap | High / Max | OOM | Restart | NRestarts |
|---|---:|---:|---:|---:|---:|---:|---|---:|
| dispatch-shadow | 573430 | 351.7 | 87.6 | 304.8 / 404.5 / 88.3 | 1200 / 1500 | -100 | on-failure | 0 |
| dispatch-panel-watcher | 3659486 | 196.1 | 13.4 | 224.8 / 270.3 / 13.8 | 1200 / 1500 | -100 | on-failure | 0 |
| dispatch-gps | 534721 | 10.6 | 8.3 | 3.5 / 11.6 / 9.6 | 180 / 250 | 0 | on-failure | 0 |
| dispatch-sla-tracker | 2998575 | 32.6 | 21.0 | 24.7 / 32.5 / 21.3 | 180 / 250 | 0 | on-failure | 0 |
| dispatch-monitor-419 | 534903 | 9.6 | 14.1 | 4.0 / 53.6 / 15.8 | 400 / 600 | 100 | always | 0 |

### Panel i API

| usluga | PID | proc RSS | proc swap | cg current / peak / swap | High / Max | OOM | Restart | NRestarts |
|---|---:|---:|---:|---:|---:|---:|---|---:|
| courier-api | 925329 | 108.9 | 7.1 | 93.7 / 106.6 / 7.1 | infinity / infinity | -800 | on-failure | 0 |
| nadajesz-panel | 2028171 | 292.0 | 518.7 | 288.0 / 400.9 / 519.9 | 400 / 600 | -800 | always | 0 |

### Papu

| usluga | PID | proc RSS | proc swap | cg current / peak / swap | High / Max | OOM | Restart | NRestarts |
|---|---:|---:|---:|---:|---:|---:|---|---:|
| papu-backend | 534809 | 49.2 | 112.3 | 40.4 / 165.4 / 112.4 | 400 / 600 | -800 | on-failure | 0 |
| papu-backend-2 | 534813 | 154.5 | 8.3 | 146.7 / 158.4 / 97.0 | 400 / 600 | -800 | on-failure | 0 |
| papu-notifications-worker | 534837 | 21.1 | 51.8 | 12.0 / 64.1 / 51.8 | infinity / infinity | -800 | always | 0 |

Interpreter z `/proc/PID/exe` dla wszystkich = `/usr/bin/python3.12`.
To dowodzi realnego binarnego interpretera, ale nie sciezki aktywacji venv;
bez zakazanego w tym sprincie odczytu pelnego ExecStart carrier venv pozostaje
`UNKNOWN`.

## Precedencja unit/drop-in

`FragmentPath` i `DropInPaths` sa `PROVEN` jako lista zastosowana przez managera
systemd. Wszystkie fragmenty wskazuja aktywne jednostki systemowe, a kazda
usluga ma 2-11 zastosowanych drop-inow. Nie ma zduplikowanego basename w jednej
liscie.

Po samych nazwach widac potencjalne nakladanie polityki `oom-protect` i
`resource_limits` dla:

- `dispatch-shadow`;
- `dispatch-panel-watcher`;
- `dispatch-gps`.

To jest kandydat do review precedencji, nie dowod sprzecznej wartosci.
Efektywne High/Max/OOM/Restart sa dowiedzione przez `systemctl show`, lecz
sprzecznosc tresci oraz martwe pliki poza `DropInPaths` pozostaja
`UNKNOWN_WITHOUT_CONTENT_INSPECTION`. Sprint zgodnie z zakresem nie skanowal
filesystemu systemd i nie czytal zadnego unitu/drop-inu.

## PROVEN / UNKNOWN / CONTAMINATED

**PROVEN:** 10 map usluga-PID-start-NRestarts, stan active/running, interpreter
binarny, efektywne properties, zastosowany fragment/drop-in list, RSS/swap,
cgroup current/peak/swap, PSI i page faults; brak zmiany PID i NRestarts w
probkach; zero zapisu live.

**UNKNOWN:** reprezentatywne zuzycie w normalnym i peakowym oknie, rate PSI i
faults w czystej godzinie, venv carrier, semantyczna zgodnosc drop-inow, martwe
pliki niezaladowane przez managera, bezpieczne docelowe limity i skutki
konkretnego tuningu. Brak pomiaru nigdy nie jest nazwany SAFE.

**CONTAMINATED:** S1 przez aktywny pytest i held full-regression lock. Dane S1
sluza tylko jako negative proof detektora oraz punkt stabilnosci PID; nie jako
profil obciazenia.

## Kandydaci osobnych sprintow — kolejnosc

1. **nadajesz-panel:** najpierw czyste okno godzinowe. Ma najwyzszy cgroup swap
   (~520 MiB), peak dotknal High (~401/400 MiB), a polityka to Restart=always i
   OOM=-800. Bez tego pomiaru nie zmieniac 400/600.
2. **Papu backend pair:** mierzyc obie instancje jednoczesnie i wyjasnic
   asymetrie RSS/swap oraz sklad cgroup. Ewentualna zmiana musi objac pare i
   zachowac sekwencyjny rollout/failover; nie stroic jednej w izolacji.
3. **dispatch-shadow + panel-watcher:** osobny review tresci polityk za nowym
   ACK, bo nazwy dwoch drop-inow sugeruja nakladanie precedencji. Dopiero potem
   czyste okno peak/off-peak i propozycja kontraktu per usluga.
4. **courier-api:** ma High/Max=infinity przy OOM=-800. Najpierw godzinny profil
   i dzieci cgroup; dopiero osobny sprint moze zaproponowac limit i jeden
   kontrolowany restart API.
5. **papu-notifications-worker:** infinity/infinity, Restart=always i ~52 MiB
   swap przy niskim current. Zweryfikowac cykl pracy oraz steady-state przed
   kontraktem.
6. **dispatch-gps, sla-tracker, monitor-419:** nizszy priorytet; przegladac
   usluga-po-usludze. GPS ma ten sam sygnal nakladania nazw polityk; monitor ma
   swiadome OOM=100/Restart=always.

Kazda zmiana MemoryHigh/Max, OOM, Restart, unitu lub drop-inu jest nowym
sprintem maintenance: backup, jawny diff, ACK, daemon-reload, jeden restart
wlasciwej uslugi, PID/NRestarts/health i sprawdzony rollback. Ten sprint nie
daje takiego GO.

## Procedura pozniejszego odczytu — bez automatu

1. Wybrac jawne okno poza ops-blackoutem i bez sztucznego obciazenia.
2. Potwierdzic brak held `/tmp/ziomek_full_regression.lock` oraz brak
   pytest/mutation w polu `window_quality`.
3. Uruchomic narzedzie recznie na poczatku i po co najmniej 60 minutach;
   snapshoty zapisywac prywatnie poza repo.
4. Odrzucic okno jako `CONTAMINATED` albo `UNKNOWN`, jezeli dowolny check jest
   nieznany/skazony, PID/start sie zmieni, `NRestarts` wzrosnie lub zabraknie
   metryki.
5. Dla czystej pary policzyc delty PSI/faults, current/peak/swap i opisac realny
   ruch. Powtorzyc osobno w zwyklym oraz uzgodnionym peakowym oknie; dopiero dwa
   zgodne profile sa kandydatem do projektu limitu.

Nie powstal timer, at-job ani automatyczna akcja po wyniku.

## Testy i rollback

- baseline przed edycja: **5087 passed / 27 skipped / 10 xfailed / 0 failed**;
- targeted hermetyczny: **8 passed**;
- finalny DEFAULT: **5095 passed / 27 skipped / 10 xfailed / 0 failed**;
- finalny `HERMETIC_STRICT=1`: **5045 passed / 77 skipped / 10 xfailed /
  0 failed**;
- `py_compile`, `diff --check`, kontrola write-set i push: sprawdzone przed
  finalnym przekazaniem lane'u.

Rollback: jawny `git revert` commita OPS0 usuwa tool, test i raport. Nie ma
flagi, migracji, danych, deployu, daemon-reloadu, restartu ani runtime do
cofania.
