# Sprint 3 — rollout live obserwowalnosci

Status: **PREPARED / LIVE PENDING poza peakiem**. Ten dokument jest punktem
pre-deploy. Sekcja postflight zostanie uzupelniona po faktycznej operacji live;
do tego czasu produkcja pozostaje bez zmian.

## Zakres i zachowanie

- Z-P1-02: wersjonowany, offline ground truth ETA. KPI pozostaje `unbound`; nie
  ma promocji ETA ani zmiany obietnicy.
- Z-P1-03: stage timing za `ENABLE_STAGE_TIMING_OBSERVATION`, default OFF. ON
  dodaje pomiar i prywatny sidecar, ale nie dodaje backpressure, limitu kolejki,
  alertu decyzyjnego ani konsumenta w scoringu.
- Z-P2-06: prawdziwe provenance `upstream/cache/fallback`, bezposredni health i
  proces-local telemetria. Polityka cache/eviction pozostaje legacy.
- HARD/SOFT, feasibility, scoring, selection, kanon i dane biznesowe nie sa
  zmieniane. Nie ma migracji.

## Tozsamosc wydania

- Repo: `/root/.openclaw/workspace/scripts/dispatch_v2`.
- Branch wydaniowy: `release/sprint3-live-20260711`.
- Base/rollback SHA: `70af4fa`.
- Naprawa blokady Audit 360 TEST-TRUTH: `4e782e8`.
- Integracja Sprintu 3: `d9a456c`.
- Tag rollback: `sprint3-rollback-prelive-20260711`.
- Zrodlowa praca wczorajsza: `sprint3/eta-observability-osrm` @ `85b9dc7`.
- Wszystkie pliki implementacji i testow Sprintu 3 sa identyczne ze zrodlowa
  galezia; konflikty backlogu i lifecycle rozwiazano na korzysc nowszego mastera
  z addytywnym wpisem nowej flagi.

## ETAP 0 — baseline i dowod problemu

- Baseline aktualnego mastera przed naprawa audytu:
  `1 failed, 4849 passed, 24 skipped, 8 xfailed, 2 xpassed`; fail byl starym
  live-zaleznym werdyktem rejestru `USE_V2_PARSER`.
- Po naprawie TEST-TRUTH, przed integracja:
  `4851 passed, 24 skipped, 8 xfailed, 2 xpassed`.
- STRICT po naprawie: `4801 passed, 74 skipped, 8 xfailed, 2 xpassed`.
- Przed deployem `dispatch-shadow.service` byl active/running, PID `3659231`,
  `NRestarts=0`, start `2026-07-10 15:39:23 UTC`; parser health `healthy/v2`.
- Efektywny `USE_V2_PARSER=1`; nowa flaga nie istniala w `flags.json`, a sidecar
  nie istnial. Telegram pozostaje swiadomie wylaczony i nie jest dotykany.

## ETAP 1-4 — root cause i mapa kompletnosci

| Miejsce | Rola | Writer / consumer | TAK / N-D | Dowod |
|---|---|---|---|---|
| `common.py` | default OFF, izolacja, fingerprint | flags / wszystkie procesy | TAK | lifecycle 505/505 |
| `dispatch_pipeline.py` | spany assess/fanout/selection | pipeline / trace | TAK | testy ON/OFF |
| `core/candidates.py` | praca kandydatow | worker / trace | TAK | ContextVar w pool |
| `route_simulator_v2.py` | praca solvera | solver / trace | TAK | wszystkie strategie |
| `osrm_client.py` | provenance, cache, health | route/table / raport | TAK | upstream/cache/fallback |
| `shadow_dispatcher.py` | tick, join, ledger, ACK | event bus / sidecar | TAK | open/complete + pseudonimowy join |
| `observability/stage_timing.py` | schema i writer 0600 | dispatcher / report | TAK | fail-soft + symlink guard |
| `tools/stage_timing_report.py` | realny consumer | sidecar+ledger / operator | TAK | coverage/incomplete/percentyle |
| `tools/paired_flag_replay.py` | oracle ON/OFF | world records / rollout | TAK | oba porzadki + mutation probe |
| `deploy/stage-timing-logrotate.conf` | retencja | logrotate / sidecar | TAK | exact path, brak globu |
| ETA calibration/promocja | zmiana KPI | brak | N-D | semantyka B-07 nadal otwarta |
| backpressure/cache eviction | enforcement | brak | N-D | poza Faza A, brak martwych symboli |

## ETAP 5 — dowod parytetu i pozytywnego wplywu

- Focused cluster po scaleniu: `105 passed`.
- Pelna regresja finalnego commita:
  `4940 passed, 24 skipped, 10 xfailed, 147 warnings`.
- Pelna regresja `HERMETIC_STRICT=1`:
  `4890 passed, 74 skipped, 10 xfailed, 147 warnings`.
- Canon static: R6=35, R27=+/-5, alarm=40 — zielony.
- Lifecycle repo i live: 505/505 skurowanych, 0 bledow.
- Identity parity: worker i panel-roster 177/177, 0 mismatch.
- Entropia: bez regresji (17/13/25/1/7/13/11/10).
- OSRM direct probe: route/table/nearest `upstream_ok=true`, health `healthy`;
  cache/CB jawnie `process_local` dla PID reportera.
- Swiezy paired replay na zamrozonym oknie: 404 porownania w obu kolejnosciach,
  403 exact, 1 miekka roznica `pool_feasible+reason` (0,25%), 0 krytycznych,
  0 miss mismatch. Wczorajszy laczny dowod: 808, 4 miekkie (0,50%), 0
  krytycznych i 0 miss mismatch.

## Przyjete bramki canary (B-08)

- Retencja: `daily`, `rotate 30`, `maxsize 100M`, kompresja, mode 0600.
- Krytyczny drift (`verdict/best_cid/best_score`) = 0.
- OSRM miss mismatch = 0.
- Miekki drift `pool_feasible/reason` <= 1%.
- `STAGE_TIMING_SIDECAR_LOST` = 0, incomplete ticks = 0 po join grace.
- Coverage ledger-sidecar >= 99% po odcieciu granic okna.
- `service_wall_ms` p95 <= 2500 ms; `ledger_append_wall_ms` p95 <= 5 ms.
- `NRestarts` bez wzrostu, parser i OSRM health zdrowe, sidecar nie przekracza
  polityki wzrostu/rotacji. Przekroczenie = HOLD i hot rollback obserwacji.

## ETAP 6 — plan operacji live

1. Po peaku ponownie sprawdzic master/origin, tmux, worktree, procesy, health i
   efektywne flagi.
2. Zrobic prywatny backup `flags.json` i stanu instalacji logrotate.
3. Fast-forward master do commita wydaniowego i zainstalowac zwalidowany
   `/etc/logrotate.d/dispatch-stage-timing`; flaga nadal OFF, sidecar nie istnieje.
4. `py_compile`, import, checker i `logrotate --debug` na live path.
5. Atomowo ustawic `ENABLE_STAGE_TIMING_OBSERVATION=true` przez `flags_admin` i
   wykonac jeden kontrolowany restart tylko `dispatch-shadow.service`.
6. Sprawdzic PID, NRestarts, health, fingerprint, mode sidecara, join coverage i
   pierwsza proces-local telemetrie. Nie restartowac Telegrama ani panelu.
7. Ustawic jednorazowy werdykt T+48 h i zapisac go w rejestrze shadow-jobow.

## ETAP 7 — rollback gotowy przed deployem

1. Hot: `flags_admin set ENABLE_STAGE_TIMING_OBSERVATION false`; kolejny tick nie
   tworzy collectora, depth query ani wpisow sidecara.
2. Kod: `git revert d9a456c` (naprawa TEST-TRUTH `4e782e8` zostaje), potem jeden
   kontrolowany restart `dispatch-shadow.service` poza peakiem.
3. Retencja: usunac tylko `/etc/logrotate.d/dispatch-stage-timing` i sprawdzic
   `logrotate --debug`.
4. Dane: brak migracji; sidecar addytywny, nie wymaga przepisywania ledgerow.
5. Proba powrotu: flaga OFF, brak wzrostu sidecara przez kolejny niepusty tick,
   active/running, NRestarts bez wzrostu, parser health i kanon zielone.

## Postflight live

PENDING — zostanie wypelnione rzeczywistymi timestampami, PID, health,
fingerprintem, backupem, jobem T+48 h i wynikiem smoke po operacji.
