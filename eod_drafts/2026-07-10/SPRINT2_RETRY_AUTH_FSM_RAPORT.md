# Sprint 2 — retry/DLQ, ochrona logowania i formalny FSM (Faza A)

Data wykonania: 2026-07-10

Zakres backlogu: Z-P0-05, Z-P0-06, Z-P1-01

Baza dispatch przed Sprintem 2: `b532697`

Repo API kurierów przed Sprintem 2: `a2ea4f2`

## Status końcowy

Sprint 2 Faza A jest zaimplementowany, wdrożony i wypchnięty na oba mastery:

- `dispatch_v2`: `a384d46` (retry/DLQ) oraz `c2bde58` (FSM),
  `origin/master=c2bde58`;
- `courier_api`: `cf4f7d2` (hardening auth) oraz `35adac9` (rollout per-IP),
  `origin/master=35adac9`.

To nie jest wdrożenie automatycznego retry ani egzekwującego FSM. Faza A daje
metadane, ręczne narzędzia i obserwację. Jedynym aktywnym ograniczeniem nowego
zachowania jest limiter logowania per-IP 20 nieudanych prób / 15 minut. Limiter
globalny pozostaje OFF. APK i `GET /api/couriers` nie zostały zmienione.

## Macierz anti-lie

| Element | Stan po sesji |
|---|---|
| Kod retry/DLQ | wdrożony i pushed |
| Migracja `events.db` | zastosowana, `ready=true` |
| Automatyczny retry | **OFF; brak workera/timera/polityki/flagi** |
| Ręczny replay DLQ | dostępny, wymaga jawnego potwierdzenia i powodu |
| FSM observer | **ON, log-only, fail-open** |
| FSM enforcement | **OFF i niewpięty** |
| Auth per-CID | 5 faili / 15 min, bez zmiany |
| Auth per-IP | **ON: 20 faili / 15 min** |
| Auth global | **OFF** |
| APK / `/api/couriers` | bez zmiany |

## Z-P0-05 — retry/DLQ eventów

Wykonano:

- addytywną migrację tabeli `events` o osiem pól audytowych:
  `attempt_count`, `last_error`, `next_attempt_at`, `last_failed_at`,
  `dead_lettered_at`, `replay_count`, `last_replayed_at`,
  `last_replay_reason`;
- indeksy `idx_events_retry_due` oraz `idx_events_dead_letter`;
- helpery do planowania retry, oznaczania błędu, przeniesienia do logicznego
  statusu `dead_letter`, jawnego requeue oraz agregatów kolejki;
- ręczne CLI `replay_dead_letter.py`; requeue wymaga `--confirm-requeue`, a
  biblioteczny helper przy `confirmed=False` jest no-op;
- migrator, który bez `--apply` wykonuje wyłącznie inspekcję read-only;
- testy atomowości, idempotencji migracji, odmowy bez potwierdzenia i default-OFF.

Zmiana zachowania: istniejący event bus zapisuje metadane zgodne z nowym
schematem, ale **nie pobiera automatycznie retry i nie uruchamia nowych prób**.
`AUTOMATIC_RETRY_ENABLED=False`. Nie istnieje worker, timer systemd, domyślne
`max_attempts`, backoff ani klucz w `flags.json`, który można bezpiecznie
„flipnąć”. Status `dead_letter` jest logicznym statusem w `events`, nie osobną
tabelą.

Snapshot po migracji: `failed=106`, `pending=14`, `processed=1389`; wszystkie
historyczne rekordy miały `attempt_count=0`, a kolejki `retry_scheduled` i
`dead_letter` były puste. 106 historycznych `failed` nie zostało backfillowanych,
ponowionych ani przeniesionych do DLQ.

Otwarte decyzje Fazy B:

1. które typy eventów są retryable;
2. liczba prób i backoff/jitter;
3. semantyka idempotencji i atomowy claim workera;
4. alerty wieku kolejki/DLQ i stop-loss;
5. osobna polityka dla 106 historycznych `failed`.

Bez tych decyzji nie budować ani nie uruchamiać automatycznego workera.

## Z-P0-06 — ochrona logowania kurierów

Wykonano w sąsiednim repo `courier_api`:

- `X-Forwarded-For` jest honorowane wyłącznie, gdy bezpośredni peer należy do
  `COURIER_LOGIN_TRUSTED_PROXY_CIDRS` (domyślnie loopback); wybór IP przechodzi
  łańcuch od prawej strony i nie ufa nagłówkowi od bezpośredniego klienta;
- check i zapis próby PIN są jedną transakcją SQLite, co zamyka race równoległych
  prób;
- istniejący limit per-CID 5/15 min ma pierwszeństwo;
- opcjonalne limitery per-IP i globalny są default-OFF w kodzie;
- produkcyjny drop-in włączył wyłącznie per-IP: 20/15 min; global pozostał OFF;
- po przekroczeniu limitu odpowiedź 429 jest taka sama dla poprawnego i błędnego
  PIN-u, a odrzucone próby nie przedłużają blokady;
- indeksy `idx_pin_ip_failed_ts` i `idx_pin_failed_ts` są tworzone
  idempotentnie przez `init_db`.

Zmiana zachowania: burst nieudanych loginów z jednego źródłowego IP zostaje
odcięty po 20 próbach w 15 minut. Może to objąć kilku poprawnych kurierów za
współdzielonym NAT, dlatego obserwować 429 i zgłoszenia terenowe. Nie wykonywać
produkcyjnego testu przez burst 20 złych PIN-ów.

Pełny stan i rollback auth:
`/root/.openclaw/workspace/scripts/courier_api/deploy/README_courier-login-rate-limit.md`.

## Z-P1-01 — formalny FSM zleceń

Wykonano:

- formalny katalog stanów, zdarzeń i dozwolonych przejść w `order_fsm.py`;
- walidator zwracający strukturalny werdykt, bez mutowania stanu;
- observer na ścieżce legacy `state_machine`, a także jawne źródła zdarzeń z
  panel watchera i parcel merge;
- jawne wyjątki reconcile/correction dla znanych ścieżek odzyskiwania po
  utracie zdarzenia;
- logowanie nieprawidłowego przejścia, brakującego źródła reconcile i błędu
  samego observera;
- testy macierzy przejść, źródeł reconcile, fail-open i braku wpływu na writer.

Zmiana zachowania: `ORDER_FSM_OBSERVER_ENABLED=True` powoduje wyłącznie logowanie.
`ORDER_FSM_ENFORCEMENT_ENABLED=False`, a kod zapisu celowo nie sprawdza tej
wartości. Zmiana samej stałej **nie może włączyć enforcementu**. Legacy state
machine pozostaje jedynym writerem i zachowuje dotychczasowe zachowanie także
przy werdykcie invalid lub wyjątku observera.

Faza B wymaga osobnej decyzji o semantyce przejść i zaprojektowania atomowego
`validate -> write` z trybem kwarantanny/rollbacku. Nie „flipować” enforcementu.

## Testy

- dispatch, celowane klastry retry/FSM po rebase: **198 passed**;
- courier auth: **23 passed, 1 skipped**;
- `PRAGMA quick_check`: `ok` dla `events.db` i `courier_api.db`;
- smoke `GET /api/ping`: `{"ok":true,"service":"courier-api"}`.

Nie należy raportować pełnej suity Sprintu 2 jako zielonej. Próba całej suity z
izolowanego worktree dała `4702 passed, 9 failed, 24 skipped, 7 xfailed,
2 xpassed`; dziewięć faili było zgodnych z brakującym worktree
`/root/sprint2_wt/flags.json`. Pełny kanoniczny rerun z prawidłowym snapshotem
flag pozostaje zadaniem weryfikacyjnym, nie dowodem regresji tej zmiany.

## Stan obserwacji Sprintu 1

Restart usług przy wdrożeniu Sprintu 2 przerwał techniczną ciągłość poprzedniego
48-godzinnego okna od `2026-07-10 06:10:36 UTC`. Jeżeli kryterium wymaga
ciągłości procesu, nowa kotwica dla `dispatch-shadow` i
`dispatch-panel-watcher` to `2026-07-10 06:46:29 UTC`, a najwcześniejszy koniec
to `2026-07-12 06:46:29 UTC`. Nie zaliczać starego terminu 06:10:36.

## Ochrona danych i dalsze granice

- `ZIOMEK_BACKLOG.md` nie został zmieniony w tej sesji.
- `daily_accounting/kurier_full_names.json` pozostaje zastaną lokalną zmianą i
  jest wykluczony z commitów.
- Nie czytać payloadów eventów do zwykłego monitoringu; wystarczą agregaty.
- W transkrypcie narzędzi tej sesji pojawiła się wartość istniejącego sekretu
  administracyjnego. Nie jest powtórzona w repo ani w tym raporcie. Rotację
  wykonać jako osobne zadanie po ACK; nie uruchamiać nieprzefiltrowanego
  `systemctl show ... Environment`.

Szczegóły wdrożenia, backupów i rollbacku są w
`SPRINT2_DEPLOY_AND_ROLLBACK_REPORT.md`.
