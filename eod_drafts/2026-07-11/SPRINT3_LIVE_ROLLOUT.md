# Sprint 3 — rollout live obserwowalnosci

Status: **LIVE SHADOW CANARY ON od 2026-07-11T10:27:12Z**. Kod, retencja i
obserwacja sa wdrozone; ETA pozostaje offline/unbound. Werdykt canary jest
PENDING po pelnym oknie 48 h.

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
- Privacy-fix aggregate-only replay: `292c9cd`.
- Tag rollback: `sprint3-rollback-prelive-20260711`.
- Zrodlowa praca wczorajsza: `sprint3/eta-observability-osrm` @ `85b9dc7`.
- Pliki implementacji Sprintu 3 sa zgodne ze zrodlowa galezia poza naprawa
  prywatnosci aggregate-only replayu `292c9cd`; konflikty backlogu i lifecycle
  rozwiazano na korzysc nowszego mastera z addytywnym wpisem nowej flagi.

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
- Pelna regresja finalnego commita po privacy-fixie:
  `4941 passed, 24 skipped, 10 xfailed, 147 warnings`.
- Pelna regresja `HERMETIC_STRICT=1`:
  `4891 passed, 74 skipped, 10 xfailed, 147 warnings`.
- Canon static: R6=35, R27=+/-5, alarm=40 — zielony.
- Lifecycle repo i live: 505/505 skurowanych, 0 bledow.
- Identity parity: worker i panel-roster 177/177, 0 mismatch.
- Entropia: bez regresji (17/13/25/1/7/13/11/10).
- OSRM direct probe: route/table/nearest `upstream_ok=true`, health `healthy`;
  cache/CB jawnie `process_local` dla PID reportera.
- Swiezy paired replay na zamrozonym oknie: 404 porownania w obu kolejnosciach,
  403 exact, 1 miekka roznica `pool_feasible+reason` (0,25%), 0 krytycznych,
  0 miss mismatch. Dodatkowy realny rerun po privacy-fixie: 202 porownania,
  200 exact, 2 miekkie (0,99%), 0 krytycznych/miss; stdout zawieral wylacznie
  jeden aggregate JSON. Wczorajszy laczny dowod: 808, 4 miekkie (0,50%), 0
  krytycznych i 0 miss mismatch.
- Domknieto near-miss prywatnosci harnessu: finalny JSON byl aggregate-only, ale
  transytywne loggery/printy pipeline wypisywaly identyfikatory operacyjne.
  `paired_flag_replay` wycisza teraz stdout/stderr/logging tylko na czas
  `replay_one`, zawsze przywraca prog logowania i ma negatywny test wycieku.

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

- Kanoniczna rozbieznosc okna: pierwsza interpretacja traktowala sobotni
  scoring-peak 11-14 jako blackout deployu. Weryfikacja D2 pokazala osobne
  kontrakty: scoring 11-14/17-20, ruch sobota 12-21, **ops-blackout sobota
  16-21**. Deploy `12:26-12:27 Europe/Warsaw` byl poza blackoutem.
- Preflight bez kolizji: master/origin `70af4fa`, release/origin `fa26c80`;
  aktywne lane'y Audit 360 mialy rozlaczny zakres. Jedyny dirty plik main to
  cudzy `eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md`; nietkniety.
- Backup 0600/0700: `/root/ziomek_backups/sprint3_live_20260711_1026UTC/`
  (`flags.json.pre-live`, `dispatch-code.bundle`). Rollback tag wskazuje
  `70af4fa`.
- Pierwsza proba fast-forward zostala bezpiecznie zatrzymana przez pusty
  `.git/index.lock` z 10.07 18:43 UTC. `lsof`, `fuser` i lista procesow
  potwierdzily brak wlasciciela; HEAD i `/etc` byly nietkniete. Usunieto tylko
  stale lock i ponowiono operacje.
- Master fast-forward `70af4fa -> fa26c80`; logrotate zainstalowany jako
  `/etc/logrotate.d/dispatch-stage-timing` mode 0644 i `logrotate --debug` PASS.
  Przed ON: flaga nieobecna/fingerprint 0, sidecar absent, import/canon/checkery
  repo+live zielone.
- Flaga ustawiona atomowo przez `flags_admin` o `10:27:12 UTC`; jeden
  kontrolowany restart tylko `dispatch-shadow.service`. Stary PID `3659231`,
  nowy PID `573430`, start `10:27:21 UTC`, `NRestarts=0`, active/running.
  Telegram, panel-watcher i courier-api nie byly restartowane.
- Efektywny fingerprint: `ENABLE_STAGE_TIMING_OBSERVATION=1`,
  `USE_V2_PARSER=1`. Parser `healthy/v2/anomaly=false`; OSRM direct
  route/table/nearest `upstream_ok=true`, scope reportera `process_local`.
- Sidecar powstal jako 0600. Pierwszy raport live: decision/valid 1/1,
  missing/orphan/duplicate/incomplete = 0; `service_wall_ms=2121,608`,
  `ledger_append_wall_ms=0,644`; zrodla OSRM obejmuja upstream/cache/mixed.
  `ERROR`, traceback i `STAGE_TIMING_SIDECAR_LOST` = 0. Jedyny tekst `failed`
  byl poprawnym finalem starego PID z `failed=0`.
- Canary start: `2026-07-11T10:27:12Z`; okno konczy sie
  `2026-07-13T10:27:12Z`. At-job **214** wykona stage report, direct OSRM,
  daemon telemetry i paired OFF/ON o `2026-07-13 12:15 UTC` (14:15 Warszawa,
  po lunch blackout). Output prywatny 0600 pod prefixem
  `dispatch_state/sprint3_canary_20260713.*`; bez auto-flipa i bez Telegrama.
- Enforcement/backpressure/ETA promotion/cache eviction: bez zmian. Otwarta
  decyzja B-07 pozostaje nierozstrzygnieta; tego rollout nie interpretuje jako
  promocji ETA.
