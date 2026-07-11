# HA-lite / Disaster Recovery — źródła i runbook

Pliki w tym katalogu są wersjonowanym źródłem procedury DR. W szczególności
wersja A360-DR1A pliku `restore_from_restic.sh` na branchu
`ops/a360-dr1a-restore-prep` **nie została zainstalowana** pod ścieżką skryptów live.
Nie ustalono też parytetu jej CLI z istniejącym skryptem live. Instalacja,
wykonanie ze ścieżki live albo użycie do failoveru wymaga osobnego przeglądu i
ACK. Ten sprint nie wykonuje deployu ani aktywacji usług.

## Granica źródło / live

| Artefakt | Status repo | Status live |
|---|---|---|
| `backup_restic.sh` | snapshot źródła z 2026-06-21 | osobny skrypt operacyjny i timer; niezmieniane w A360-DR0 |
| `restore_from_restic.sh` | źródło DR0 rozszerzone o kontrakty DR1A, testowane wyłącznie syntetycznie | **NIEWDROŻONE; CLI i parytet niepotwierdzone** |
| `activate_pitr.sh`, `pitr_verify.sh` | historyczne snapshoty | poza zakresem A360-DR0 |
| `backup_sentinel.py` i unity | historyczne snapshoty | poza zakresem A360-DR0 |
| `HA_LITE_RUNBOOK_2026-06-21.md` | runbook uaktualniony o stan A360-DR0 | kopia live nie była aktualizowana |

## CLI źródła A360-DR1A

Uruchamiaj wyłącznie zatwierdzoną wersję ze wskazanego commita i wyłącznie w
nowym prywatnym scratchu. Budżety to jawne, dodatnie limity bajtów zatwierdzone
przez operatora; nie są filesystem quota.

```bash
A360_DR0_SCRATCH_BUDGET_BYTES="$APPROVED_SCRATCH_BUDGET_BYTES" \
  ./docs/deploy/ha-lite/restore_from_restic.sh --mode verify [--snapshot ID]

A360_DR0_SCRATCH_BUDGET_BYTES="$APPROVED_SCRATCH_BUDGET_BYTES" \
  ./docs/deploy/ha-lite/restore_from_restic.sh \
  --mode artifact [--snapshot ID] [--target /root/a360_dr0_scratch/restore_ID] \
  [--papu-format auto|plain|encrypted]

A360_DR0_SCRATCH_BUDGET_BYTES="$APPROVED_SCRATCH_BUDGET_BYTES" \
A360_DR0_DOCKER_BUDGET_BYTES="$APPROVED_DOCKER_BUDGET_BYTES" \
  ./docs/deploy/ha-lite/restore_from_restic.sh \
  --mode drill --pg-image IMAGE@sha256:DIGEST \
  [--snapshot ID] [--target /root/a360_dr0_scratch/restore_ID] \
  [--papu-format auto|plain|encrypted]
```

- `verify` sprawdza host/load/pamięć i konflikt z backupem, budżet/cache/free
  space, dostęp, provenance snapshotu, jego wiek i część danych repo. Powtarza
  guard konkurencji tuż przed `restic check`. Nie odtwarza plików ani baz.
- `artifact` odtwarza pliki do nowego scratcha i waliduje wersjonowany manifest,
  JSON, SQLite oraz dumpy. Nie tworzy zasobów Docker.
- `drill` dodatkowo tworzy własny kontener i volume bez sieci i portów,
  odtwarza dwie scratch DB, sprawdza schemat i usuwa zasoby po dokładnym labelu.
  Nie przyjmuje istniejącego kontenera ani wskazanej bazy.

Stare formy CLI nie są aliasami. Skrypt odrzuca je fail-closed.

## Zakres dowodu

Zielony `drill` dowodzi tylko odtworzenia artefaktów i schematów PostgreSQL w
izolacji. Nie dowodzi importu aplikacji, jej health, kolejności startu usług,
aktywacji systemd/nginx ani przełączenia ruchu. Pełny service RTO pozostaje
`HOLD / NOT PROVEN`. Realny drill baz także pozostaje HOLD. DR1A definiuje
granice carriera, quota i app-smoke, lecz nie instaluje ich realnych adapterów.

Katalog celowy musi być nowy i mieć tryb `0700`; raport ma `0600`. Skrypt
sprawdza jawny budżet, wymuszoną quota scratcha i wolne miejsce przed
rozpakowaniem, osobny budżet Docker, wspólny filesystem oraz ekspansję dumpów
po dekompresji. Quota jest ponownie atestowana przed pierwszą mutacją Docker.
Brak któregokolwiek dowodu kończy się RED przed utworzeniem volume.

Realny profil bezpieczeństwa jest przypięty w źródle i `readonly`: maksymalny
wiek snapshotu i dumpu 93 600 s, minimalna rezerwa 5 GiB, minimalna dostępna
pamięć 3 GiB oraz co najmniej 50 tabel na rolę. Produkcyjne zmienne środowiskowe
nie zmieniają tych pięciu progów; injection istnieje wyłącznie w hermetycznym
`TEST_MODE` pod nazwami `A360_TEST_*`, dodatkowo atestowanym przez aktywny
proces nadrzędny pytest i `PYTEST_CURRENT_TEST`.

Provenance ma osobny kontrakt `a360-dr0-snapshot-provenance-v1-20260711`:
snapshot musi mieć przypięty hostname producenta, oba tagi producenta i pięć
wymaganych ścieżek źródłowych. Dopiero spośród pasujących kandydatów wybierany
jest jednoznacznie najnowszy; jawny skrót ID musi wskazać dokładnie jeden rekord.
Raport ujawnia tylko wersję kontraktu i liczniki, nie listę ścieżek.

Cleanup jest uzbrojony przed pierwszym `volume create`. Po częściowym sukcesie
Dockera sprawdza dokładną nazwę, `a360.dr0.scratch=true` i dokładny `run_id`,
usuwa kontener przed volume i ponownie dowodzi nieobecności. Obcy albo
niepewny zasób daje RED 90 bez kasowania.

## Kontrakty DR1A (source-only)

DR1A przypina trzy root-owned adaptery pod stałymi ścieżkami
`/usr/local/libexec/a360-dr1-*`. Ten sprint ich nie tworzy ani nie instaluje,
więc realne uruchomienie pozostaje fail-closed. W hermetycznym `TEST_MODE`
ścieżki wskazują wyłącznie syntetyczne fake'i:

- `a360-dr1a-one-shot-carrier-v1-20260711` wydaje wartość dokładnie raz dla
  `run_id` i celu `papu_backup_decrypt`; wartość idzie przez stdin do openssl,
  nigdy przez argv, env, plik, stdout skryptu ani JSON raportu. Fake przyjmuje
  tylko hash jawnego canary. Realny adapter i jego secret-store wymagają
  osobnego security review i ACK DR1B.
- `a360-dr1a-scratch-quota-v1-20260711` atestuje dokładny `run_id`, kanoniczny
  scratch, twarde `limit_bytes/used_bytes` i `enforced=true`. Quota i budżet
  operatora są niezależnymi limitami; obowiązuje ciaśniejszy, a reserve i
  capacity są sprawdzane ponownie.
- `a360-dr1a-app-smoke-v1-20260711` wykonuje dokładnie:
  `panel_import → papu_import → dispatch_import → panel_health → papu_health →
  dispatch_health → service_start_order`, z oczekiwanym porządkiem
  `postgres,panel,papu,dispatch`. W DR1A jest to wyłącznie fake contract, nie
  dowód działania aplikacji.

Raport `a360-dr1a-restore-prep-report-v1` ma zawsze
`dr1b_execution_gate.status=HOLD`. Zielone fake'i dają GO jedynie dla jakości
źródła DR1A, nigdy dla realnego game-day.

## Bramka DR1B — GO/HOLD

- Nie odczytano ponownie realnego repo po dodaniu provenance; zgodność bieżącego
  snapshotu z hostname/tag/path contract jest **NOT PROVEN**.
- Nowy `verify` capacity/concurrency guard ma dowód fake-only; realny verify nie
  był ponawiany.
- Producent backupu nadal nie pokazuje globu dla dwóch wymaganych unitów
  `backup-sentinel`.
- Realne adaptery carrier/quota/app-smoke nie są zainstalowane ani
  zweryfikowane; fake PASS nie jest dziedziczony przez realny run.
- Sobota 16:00-21:00 Warszawa jest ops-blackoutem; każdy realny run wymaga
  ponownego preflightu poza blackoutem i przy niskim obciążeniu.

`GO` dla osobnej fazy DR1B istnieje dopiero, gdy wszystkie powyższe punkty są
zamknięte, source commit jest przypięty, adaptery przeszły niezależny review,
jest jawny ACK wymieniający encrypted drill/carrier/okno, a rollback exact
`run_id` został przećwiczony. W przeciwnym razie werdykt to `HOLD` z nazwą
brakującego kontraktu. DR1A kończy z `HOLD`.

Do zamknięcia tych punktów real `artifact`/`drill` i service RTO pozostają HOLD.
