# COM-P0-OOM-DISPOSABLE-REPRO-04 v1.1 — korekta projektu (DESIGN ONLY)

Status: `HOLD_DESIGN` / `DESIGN_ONLY_NOT_AUTHORIZED_FOR_EXECUTION`.
Nie tworzę hosta, obrazu, kontenera, sieci, wolumenu ani procesu. Produkcja
pozostaje `FAIL_CLOSED/HOLD_OFFLINE`.

## Siedem zamkniętych blockerów z v1.0

1. **Lifecycle i ledger:** executor ma obowiązek `docker create`, natychmiastowy
   durable write-ahead ledger (`fsync` pliku i katalogu), dopiero potem
   `inspect` efektywnej konfiguracji, a następnie `start`. Niezależny deadline
   killer działa poza kontenerem. Brak wpisu lub niezgodność ID kończy się
   `HOLD_LEDGER` i bezwarunkowym exact-ID cleanup.
2. **C65 authority:** nonce jest świeży, jednorazowy, hash-bound do runbooku,
   manifestu, hosta i cell-setu; consume jest atomowy pod flock z `fsync`, ma
   expiry i osobny owner ACK. Stary/cached/env ACK, sam opis lub nazwa pliku
   nie przechodzą. Revocation jest sprawdzany przed każdym create/start.
3. **Izolacja hosta/obrazu/runtime:** manifest wymaga clean `DOCKER_CONFIG`,
   `env -i` z allowlistą, brak proxy/credential-helpera, skan obrazu pod kątem
   sekretów, oddzielne OCI manifest/config/RootFS identities (image ID nie jest
   RepoDigest), pinned runtime/seccomp/AppArmor/userns, local bounded log
   driver/nofile oraz host-level egress deny. `network=none` jest konieczny,
   ale sam nie dowodzi izolacji hosta.
4. **Fixture consumer paths:** każdy fixture jest hash-bound i materializowany
   do faktycznej ścieżki konsumowanej przez aplikację (`config/state/sessions/
   memory/workspace`), nie tylko do `/repro-fixtures`. Materializer zapisuje
   manifest, inode/path map i testuje brak symlinków/escape; M0 jest jawnie
   synthetic schema i nie udaje prawdziwego OpenClaw schema.
5. **OVAT/oracle:** macierz jest zamrożonym A/B/A: identyczny warm-cache,
   kolejność, host/runtime/limits, dwa baseline controls przed i po każdej
   zmianie; każda komórka zmienia dokładnie jedną zmienną. READY wrapper/argv,
   classifier goldens/mutation probes i baseline V8 telemetry są artefaktami
   hash-bound. `--report-on-fatalerror` to osobny non-parity repeat; redaction
   diagnostyki jest obowiązkowa.
6. **Budżet:** limit obejmuje wszystkie creates, retries, diagnostic repeat,
   provisioning, cleanup i tool calls. Maksimum jest wyrażone osobno jako
   wall-clock i CPU-seconds; `3 GiB` oznacza memory cap, a swap policy jest
   jawna (brak niejawnego „4 GiB swap”). Cleanup reserve jest nieprzenoszalny.
7. **Crash-safe evidence/cleanup:** ledger ma stany durable
   `PLANNED→CREATED→INSPECTED→STARTED→TERMINAL→CLEANED`; recovery po crashu
   skanuje exact IDs z ledgeru, zatrzymuje tylko zasoby z potrójnym dopasowaniem
   (ID+label+manifest), a evidence seal następuje dopiero po cleanup receipt.
   Usunięcie jest weryfikowane po procesach, cgroup, mountach, sieciach,
   wolumenach i logach; TTL i prywatne 0600 artefakty mają receipt deletion.

Niezależny review v1.1 wykazał cztery doprecyzowania, które są już w template:
finite `max_creates` obejmujący A/B/A, verifier porównujący inspect receipt z
manifestem przed startem, jawny revocation source + UTC/monotonic clock policy,
stany `ABORTED/ORPHANED/RECONCILING` oraz hash-bound budget/provisioning.

## Statyczny execution manifest

Źródłem struktury jest plik `COM_P0_OOM_DISPOSABLE_REPRO_04_execution_manifest.template.json`.
To wyłącznie schema-like template z placeholderami; nie zawiera nonce, ACK,
hosta ani exact image i nie może być użyty do uruchomienia. Wypełnienie wymaga
provisioning phase, niezależnego review i świeżego owner ACK.

## Bramka przed execution-ready

Najpierw provisioning proposal, atestacja disposable hosta i exact image
offline, potem materializacja manifestu oraz dwa niezależne review. Jakikolwiek
brak dowodu daje `HOLD`, nie „best effort”. Reprodukcja nie może wpływać na
recovery; wynik nawet pozytywny nie autoryzuje unmask/start produkcji.
