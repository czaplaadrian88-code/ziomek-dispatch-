# Sprint 2 — raport deployu i rollbacku Fazy A

Data: 2026-07-10

Zakres: Z-P0-05 retry/DLQ, Z-P0-06 auth, Z-P1-01 FSM

ACK: użytkownik jawnie rozszerzył zakres o migrację, restart, flip rekomendowanego
limitera per-IP, deploy, commit i push.

## Wynik

Kod obu repozytoriów jest na `master` i `origin/master`. Migracja retry/DLQ
została zastosowana do live `events.db`. Usługi ładujące kod zostały
zrestartowane i przeszły smoke. W auth aktywowano wyłącznie limiter per-IP;
automatyczny retry, limiter globalny i enforcement FSM pozostają OFF.

## Manifest repozytoryjny

### `dispatch_v2`

- `a384d46 feat(events): add phase-a retry and DLQ primitives`
  - `event_bus.py`, `event_retry.py`,
    `migrations/event_retry_metadata.py`, `replay_dead_letter.py`,
    `tests/test_event_retry_phase_a.py`;
- `c2bde58 feat(fsm): add phase-a order lifecycle observer`
  - `order_fsm.py`, `state_machine.py`, `panel_watcher.py`,
    `parcel_lane_merge.py`, `tests/test_order_fsm_zp101.py`,
    `tests/test_parcel_lane_merge.py`.

### `courier_api`

- `cf4f7d2 feat(auth): harden courier login phase A`
  - `auth.py`, `config.py`, `db.py`, `main.py`,
    `tests/test_courier_login_hardening.py`;
- `35adac9 ops(auth): enable recommended per-IP login limit`
  - `deploy/courier-login-rate-limit-per-ip.conf`.

Dokumentacyjny commit tej sesji należy identyfikować przez późniejszy `git log`;
raport nie wpisuje własnego niestabilnego hasha.

## Backup pre-migration

Katalog: `/root/sprint2_backups/20260710T064132Z` (`0700`). Pliki DB mają tryb
`0600`, a `integrity_check` obu kopii zwrócił `ok`.

| Plik | Rozmiar | SHA-256 |
|---|---:|---|
| `events.db.pre-sprint2` | 36 212 736 B | `60280a0c0aa4b706edeb6f80b5cae73e54be9461bf6bdb1ce1af1a406d4bbe90` |
| `courier_api.db.pre-sprint2` | 30 863 360 B | `856a6278c9c9474ae01367049921fb6e4b12d63318b089b2c612a6043a90090b` |

Pomocnicze pliki WAL/SHM kopii zostały usunięte po walidacji. Backup jest
punktem awaryjnym, nie domyślnym rollbackiem kodu.

## Migracja `events.db`

Kanon live: `/root/.openclaw/workspace/dispatch_state/events.db`.

- migracja addytywna zakończyła się `ready=true`;
- osiem kolumn i dwa indeksy wymienione w raporcie implementacyjnym istnieją;
- statusy zaraz po migracji: `failed=106`, `pending=14`, `processed=1389`;
- `attempt_count` pozostał `0` dla wszystkich zastanych rekordów;
- brak rekordów `retry_scheduled` i `dead_letter`;
- nie wykonano backfillu, replayu ani automatycznej próby;
- `PRAGMA quick_check` zwróciło `ok`.

Inspekcja migratora jest bezpieczna bez `--apply`:

```bash
cd /root/.openclaw/workspace/scripts
PYTHONDONTWRITEBYTECODE=1 \
  /root/.openclaw/venvs/dispatch/bin/python \
  -m dispatch_v2.migrations.event_retry_metadata \
  --db /root/.openclaw/workspace/dispatch_state/events.db
```

Nie dopisywać `--apply` do zwykłego preflightu. Obecnie ponowne `--apply` jest
idempotentne, ale pozostaje mutacją runtime.

## Usługi i smoke po deployu

| Usługa | Start UTC po deployu | PID weryfikacyjny | Stan |
|---|---|---:|---|
| `dispatch-shadow` | 2026-07-10 06:46:20 | 2998464 | active/running, `NRestarts=0` |
| `dispatch-panel-watcher` | 2026-07-10 06:46:29 | 2998574 | active/running, `NRestarts=0` |
| `dispatch-sla-tracker` | 2026-07-10 06:46:29 | 2998575 | active/running, `NRestarts=0` |
| `courier-api` | 2026-07-10 07:45:44 | 3047051 | active/running, `NRestarts=0` |

PID-y są historycznym dowodem tej sesji, nie oczekiwaniem dla przyszłego
preflightu. `GET http://127.0.0.1:8767/api/ping` zwrócił poprawny JSON health.

## Efektywne przełączniki

```text
AUTOMATIC_RETRY_ENABLED=False
ORDER_FSM_OBSERVER_ENABLED=True
ORDER_FSM_ENFORCEMENT_ENABLED=False
ENABLE_COURIER_LOGIN_RATE_LIMIT_PER_IP=1
COURIER_LOGIN_RATE_LIMIT_PER_IP_MAX_FAILED=20
ENABLE_COURIER_LOGIN_RATE_LIMIT_GLOBAL=0
```

Nie istnieje jednostka systemd auto-retry. `replay_dead_letter.py` jest ręcznym
CLI. Aktywny drop-in auth:
`/etc/systemd/system/courier-api.service.d/login-rate-limit.conf`; wersjonowany
szablon:
`courier_api/deploy/courier-login-rate-limit-per-ip.conf`.

## Rollback

### Retry/DLQ i FSM

Preferowany rollback Fazy A jest kodowy:

1. jawnie cofnąć odpowiedni commit;
2. zrestartować wyłącznie usługi importujące zmieniony kod po osobnym ACK;
3. pozostawić addytywne kolumny i indeksy — starszy kod je ignoruje.

Nie odtwarzać całego `events.db` tylko po to, aby usunąć kolumny. Restore z
`events.db.pre-sprint2` jest procedurą awaryjną wyłącznie przy realnym
uszkodzeniu: najpierw osobny ACK, potem zatrzymanie wszystkich writerów,
zachowanie kopii bieżącej bazy i ponowny `integrity_check`. Restore cofnąłby
wszystkie eventy zapisane po 06:41 UTC.

FSM nie ma flipa enforcementu do cofnięcia. Wyłączenie observera wymaga zmiany
`ORDER_FSM_OBSERVER_ENABLED` i restartu importujących procesów; zwykle lepszy
jest revert `c2bde58`.

### Auth per-IP

Rollback runtime wymaga usunięcia wyłącznie aktywnego drop-inu, reloadu systemd
i restartu API po osobnym ACK:

```bash
rm /etc/systemd/system/courier-api.service.d/login-rate-limit.conf
systemctl daemon-reload
systemctl restart courier-api.service
systemctl show courier-api.service \
  -p ActiveState -p SubState -p NRestarts -p DropInPaths --no-pager
curl -fsS http://127.0.0.1:8767/api/ping
```

Nie kasować `pin_attempts`; blokada wygasa naturalnie po 15 minutach. Sam
`git revert 35adac9` **nie usuwa** pliku z `/etc`, więc nie jest rollbackiem
runtime. Można cofnąć aktywację per-IP, pozostawiając poprawki trusted-proxy i
atomowości z `cf4f7d2`.

## Preflight następnej sesji

1. Odczytać ten raport i `SPRINT2_RETRY_AUTH_FSM_RAPORT.md`.
2. Sprawdzić `git status --short --branch` obu repo i ich `origin/master`.
3. Uruchomić read-only inspect migracji oraz wyłącznie agregaty statusów DB.
4. Sprawdzić ActiveState/SubState/NRestarts/ExecMainStartTimestamp usług.
5. Dla auth sprawdzić `DropInPaths` i `/api/ping`; nie testować limitem przez
   produkcyjny burst błędnych PIN-ów.
6. Nie uruchamiać nieprzefiltrowanego `systemctl show ... Environment`, ponieważ
   inne zmienne jednostki zawierają sekrety.
7. Przyjąć nową kotwicę ciągłej obserwacji Sprintu 1: 2026-07-10 06:46:29 UTC.

Otwarte decyzje: retry policy/worker/alerty/historyczne failed, semantyka i
enforcement FSM, obserwacja UX per-IP oraz rotacja sekretu ujawnionego w
transkrypcie narzędzi. Każda wymaga osobnego zakresu i ACK.

## Pliki wyłączone

`ZIOMEK_BACKLOG.md`, `daily_accounting/kurier_full_names.json` oraz dane runtime
nie są częścią commitów dokumentacyjnych. Zastana zmiana
`eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md` również należy do innej
sesji i ma pozostać poza commitem Sprintu 2.
