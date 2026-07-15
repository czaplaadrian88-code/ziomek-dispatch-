# COM-P0-OOM-MASK-RECON-03 v1.0 — read-only reconciliation accepted

## Wynik

Owner zatwierdził dokładnie read-only runbook SHA-256
`160ea078811848fb9b1d4b8f0b436f4d8f1d353d9ffa77016b550ffe7210bd4a`.
Receipt 0600 SHA-256
`fce0987a028aa3f3c5c2bd6d0553458ae54f11e17009ec432a397f6beb7536c3`
przeszedł walidację JSON i został niezależnie porównany z runtime.

Nieblokujące caveaty: receipt nie ma osobnego mechanicznego JSON Schema i
normalizuje nazwę klasy OOM bez separatora `/`; semantyka oraz binding do
runbooku są zgodne. Worker identity `80:83.0`, siblingi i :9222 pochodzą z
owner message/runbooku oraz niezależnego postchecku, nie z pól receipt.

Formalna dyspozycja:

- mask: `RECONCILED_PRESERVE_FAIL_CLOSED`;
- apply: `AUTHORITY_VIOLATION_RECORDED_NOT_RATIFIED`;
- recovery: `HOLD_OFFLINE`;
- rollback: `DENIED_WITHOUT_NEW_HASH_BOUND_GATE`;
- phase: `READ_ONLY_RECONCILIATION_ACCEPTED_NOT_RECOVERY`.

To usuwa niejasność, czy obecną maskę zachować. Nie legalizuje stale-ACK apply,
nie zamyka COM-P0-CONTAIN-01 i nie daje prawa do unmask, enable, startu,
restartu, rollbacku, deployu ani zmian Compose/app/kanałów/pamięci.

## Niezależny postcheck

O `2026-07-15T16:50:13.513667300Z`:

- lokalny unit był root-owned symlinkiem do `/dev/null`, wants-link absent;
- systemd `masked/masked`, inactive/dead, MainPID0, historyczne NRestarts17,
  `NeedDaemonReload=no`, brak queued job;
- `systemctl is-enabled` wypisał `masked` z oczekiwanym rc1;
- porty 18789/18790 absent;
- gateway i CLI running=false, exited143, FinishedAt bez zmian od 14:54:32Z,
  restart policy `no`;
- Telegram/WhatsApp false/false przez bezstartowy CLI;
- naturalny watchdog tick 16:49:11Z success/no-op, bez restartu;
- pięć sibling services active/running, każde NRestarts0;
- browser działa z restart policy always, a publiczne :9222 pozostaje osobnym
  SEC1/N-D i nie zostało dotknięte.

MAIN wykonał tylko odczyty i aktualizację dokumentacji. Zero production
mutations w tej fazie.

## Klasyfikacja OOM i korekta chronologii

Potwierdzony mechanizm:

`V8_STARTUP_HEAP_EXHAUSTION_AFTER_CONTAINER_RECREATE / TRIGGER_NOT_ISOLATED`.

Dowody rozdzielają go od host/kernel OOM i Docker cgroup kill: brak kernel/cgroup
OOM, `OOMKilled=false`, exit134 i powtarzalny V8 `Reached heap limit` przy około
1,50–1,53 GiB old heap. Zwiększenie heap/memory nie wynika z tych dowodów.

Chronologia Docker koryguje wcześniejsze przypuszczenie: restart starego
kontenera po zmianie kanałów doszedł do normalnego startupu i przeżył do
zamierzonego replace. Pierwszy OOM wystąpił po utworzeniu nowego kontenera.
Późniejszy base-only kontener zawiódł identycznie, więc loopback override nie
jest wymagany do awarii i nie jest ustalonym root cause. Dokładny recreate
trigger pozostaje nieodizolowany, bo nie zachowano pełnej tożsamości obrazu i
configu zniszczonego starego kontenera.

## Granica następnej fazy

Causal reproduction wymaga osobno zatwierdzonego, disposable i
network-disabled środowiska, immutable kopii, resource sampling/heap profile
oraz braku produkcyjnych config/state/credentials/workspace mountów. Nie wolno
uruchamiać produkcyjnego unitu ani kontenerów. Nowa faza musi mieć nowy phase ID,
scope, runbook SHA, niezależny review, świeży jednorazowy gate i osobny owner
ACK.

## Artefakty i ochrona pracy

- runbook 0600:
  `/tmp/COM-P0-OOM-MASK-RECON-03_v1.0_READ_ONLY_RUNBOOK.md`;
- receipt 0600:
  `/tmp/COM-P0-OOM-MASK-RECON-03_v1.0_ACK_RECEIPT.json`;
- preimage backup pozostaje zachowany z SHA
  `b85e073df13ed6e00da1ea60f84ca54d2ef87100b6c7897748a27b815e307e8d`;
- chroniony obcy dirty
  `eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md` pozostał nietknięty;
- aplikacyjnego pytest nie uruchamiano: nie zmieniono kodu ani runtime.
