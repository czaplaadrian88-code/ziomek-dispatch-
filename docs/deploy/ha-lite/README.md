# HA-lite / Disaster Recovery — źródła i runbook

Pliki w tym katalogu są wersjonowanym źródłem procedury DR. W szczególności
wersja A360-DR0 pliku `restore_from_restic.sh` na branchu
`ops/a360-dr0-restore` **nie została zainstalowana** pod ścieżką skryptów live.
Nie ustalono też parytetu jej CLI z istniejącym skryptem live. Instalacja,
wykonanie ze ścieżki live albo użycie do failoveru wymaga osobnego przeglądu i
ACK. Ten sprint nie wykonuje deployu ani aktywacji usług.

## Granica źródło / live

| Artefakt | Status repo | Status live |
|---|---|---|
| `backup_restic.sh` | snapshot źródła z 2026-06-21 | osobny skrypt operacyjny i timer; niezmieniane w A360-DR0 |
| `restore_from_restic.sh` | nowe źródło A360-DR0, testowane syntetycznie | **NIEWDROŻONE; CLI i parytet niepotwierdzone** |
| `activate_pitr.sh`, `pitr_verify.sh` | historyczne snapshoty | poza zakresem A360-DR0 |
| `backup_sentinel.py` i unity | historyczne snapshoty | poza zakresem A360-DR0 |
| `HA_LITE_RUNBOOK_2026-06-21.md` | runbook uaktualniony o stan A360-DR0 | kopia live nie była aktualizowana |

## CLI źródła A360-DR0

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
`HOLD / NOT PROVEN`. Realny drill baz także pozostaje HOLD do zatwierdzonego,
bezpiecznego mechanizmu podania wymaganych danych prywatnych.

Katalog celowy musi być nowy i mieć tryb `0700`; raport ma `0600`. Skrypt
sprawdza jawny budżet i wolne miejsce przed rozpakowaniem, osobny budżet Docker,
wspólny filesystem oraz ekspansję dumpów po dekompresji. Brak któregokolwiek
dowodu kończy się RED przed utworzeniem volume.

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

## DR1 HOLD przed real drill

- Nie odczytano ponownie realnego repo po dodaniu provenance; zgodność bieżącego
  snapshotu z hostname/tag/path contract jest **NOT PROVEN**.
- Nowy `verify` capacity/concurrency guard ma dowód fake-only; realny verify nie
  był ponawiany.
- Producent backupu nadal nie pokazuje globu dla dwóch wymaganych unitów
  `backup-sentinel`.
- Brak filesystem quota, bezpiecznego wejścia danych prywatnych oraz
  app/import/health/start-order smoke.

Do zamknięcia tych punktów real `artifact`/`drill` i service RTO pozostają HOLD.
