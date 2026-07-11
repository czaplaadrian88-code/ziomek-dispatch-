# HA-lite / Disaster Recovery — runbook

Pierwotna data runbooka: 2026-06-21. Aktualizacja kontraktu restore: 2026-07-11
(A360-DR0). Celem jest bezpieczna odbudowa po awarii pojedynczego hosta. Sam
runbook nie zapewnia HA, automatycznego failoveru ani pełnego RTO usługi.

## 0. Stan dowodu

| Zakres | Status | Co rzeczywiście udowodniono |
|---|---|---|
| Historyczny drill 2026-06-20 | historyczny, nieporównywalny z obecnym CLI | starsza procedura odtworzyła wskazane struktury danych; nie jest dowodem obecnego full service RTO |
| A360-DR0 synthetic | PASS w ograniczonym zakresie | artifact restore + dwa izolowane PostgreSQL schema smokes + cleanup |
| A360-DR0 real repository check sprzed provenance v1 | historyczny PASS | dostęp, wiek i częściowy check repo; nie dowodzi nowego hostname/tag/path contract |
| A360-DR0 real provenance + capacity verify | **HOLD / NOT RUN** | nowy kontrakt ma wyłącznie dowód fake |
| A360-DR0 real DB drill | **HOLD** | brak zatwierdzonego bezsekretowego wejścia dla jednego z dumpów |
| Import/health/start-order aplikacji | **HOLD / NOT PROVEN** | brak izolowanego dowodu |
| Pełny service RTO i RPO obu baz | **HOLD / NOT PROVEN** | nie wykonano startu usług ani end-to-end health/ruchu |

Wynik PASS skryptu dotyczy wyłącznie pola `evidence_scope` w raporcie. Pole
`service_rto.status` pozostaje `HOLD`, dopóki osobny drill nie udowodni importu
aplikacji, health, kolejności startu oraz zależności zewnętrznych.

## 1. Granica źródło / live

Kanon źródłowy A360-DR0 to
`docs/deploy/ha-lite/restore_from_restic.sh` na zatwierdzonym commicie brancha
`ops/a360-dr0-restore`. Ta wersja **nie jest zainstalowana live**. Skrypt pod
operacyjną ścieżką workspace może mieć inne CLI i nie był zmieniany ani
uruchamiany w tym sprincie.

Nie kopiuj źródła do workspace i nie wykonuj go ze ścieżki live bez osobnego
ACK, review parytetu i planu rollbacku. Skrypt A360-DR0 nie jest narzędziem do
zasilenia istniejącej bazy, kontenera ani hosta failover.

## 2. Zakres off-site i wymagany manifest

Backup obejmuje stan dispatchu, konfigurację silnika, modele/datasets, media,
nocne dumpy dwóch ról PostgreSQL, prywatne pliki konfiguracyjne, unity systemd,
vhosty nginx i wybrane logi. Kod aplikacji pochodzi z osobnych repozytoriów.
Dane uwierzytelniające spoza backupu muszą zostać odtworzone z zatwierdzonego
zewnętrznego magazynu; ich brak utrzymuje service RTO na HOLD.

Kontrakt `a360-dr0-required-artifacts-v1-20260711` wymaga konkretnych, jawnie
wersjonowanych artefaktów:

- 4 artefaktów rdzenia stanu/configu;
- 8 artefaktów tożsamości i prywatnej konfiguracji, wyłącznie presence/metadata;
- 19 konkretnych unitów runtime i ciągłości backupu;
- 3 konkretne vhosty;
- po jednym jednoznacznie najnowszym dumpie dla każdej roli.

Niepusty katalog z przypadkowym plikiem nie spełnia kontraktu. Dla plików
prywatnych skrypt sprawdza tylko typ, brak symlinków/hardlinków i niezerowy
rozmiar. Nie otwiera ani nie raportuje ich treści, nazw wpisów, hashy czy
liczników zależnych od danych.

Kontrakt `a360-dr0-snapshot-provenance-v1-20260711` przypina hostname
producenta, oba tagi `daily`/`scheduled` i pięć krytycznych ścieżek wejściowych.
Skrypt pobiera kandydatów bez wstępnego globalnego `latest`, odrzuca obcą
provenance, a dopiero potem wybiera jednoznacznie najnowszy pasujący snapshot.
Jawny prefix ID musi zwrócić dokładnie jeden rekord. Raportuje wersję/status i
liczniki kontraktu, bez listy ścieżek.

Realne progi są przypięte i `readonly`: snapshot/dumpy maks. 93 600 s, rezerwa
min. 5 GiB, pamięć dostępna min. 3 GiB i min. 50 tabel na rolę. Produkcyjne
zmienne środowiskowe nie mogą ich osłabić; odrębne override'y istnieją tylko w
hermetycznym profilu testowym atestowanym przez proces nadrzędny pytest.

## 3. Twardy preflight pojemności

Wszystkie tryby, także `verify`, wymagają dodatniego, jawnego budżetu scratch w
bajtach; `drill` wymaga również budżetu Docker root. Są to limity pojedynczego
runu, a nie deklaracja aktywnego filesystem quota.

Przed listą snapshotów `verify` sprawdza niski load, dostępną pamięć, brak
aktywnego restic/pg_dump/pg_basebackup, budżet 2 GiB cache + rezerwę i wolne
miejsce. Guard konkurencji jest powtarzany tuż przed `restic check`. Po checku
rzeczywisty cache nie może przekroczyć allowance, operatorowego budżetu ani
naruszyć rezerwy; RED usuwa efemeryczny cache.

Przed rozpakowaniem skrypt:

1. pobiera logiczny rozmiar snapshotu;
2. wymaga scratch budget i wolnego miejsca na dwukrotność rozmiaru + rezerwę;
3. dla `drill` ustala Docker root i wymaga budżetu/wolnego miejsca na
   czterokrotność rozmiaru + rezerwę;
4. gdy scratch i Docker root dzielą filesystem, wymaga sumy obu payloadów z
   jedną rezerwą;
5. odrzuca overflow lub niewiarygodne metadane.

Po rozpakowaniu kontroluje rzeczywisty working set scratcha. Po pełnej
dekompresji obu dumpów ponownie wymaga budżetu i wolnego miejsca Docker na
trzykrotność sumy SQL + rezerwę. Dopiero potem wolno utworzyć volume.

## 4. Tryby źródła A360-DR0

Najpierw ustaw zatwierdzone limity jako liczby bajtów:

```bash
export A360_DR0_SCRATCH_BUDGET_BYTES="$APPROVED_SCRATCH_BUDGET_BYTES"
export A360_DR0_DOCKER_BUDGET_BYTES="$APPROVED_DOCKER_BUDGET_BYTES"
```

Repository check, bez restore i bez Docker:

```bash
./docs/deploy/ha-lite/restore_from_restic.sh --mode verify
```

Walidacja artefaktów w nowym scratchu, bez Docker:

```bash
./docs/deploy/ha-lite/restore_from_restic.sh \
  --mode artifact \
  --target /root/a360_dr0_scratch/restore_APPROVED_ID \
  --papu-format auto
```

Izolowane odtworzenie artefaktów i schematów PostgreSQL:

```bash
./docs/deploy/ha-lite/restore_from_restic.sh \
  --mode drill \
  --target /root/a360_dr0_scratch/restore_APPROVED_ID \
  --pg-image postgres@sha256:PINNED_DIGEST \
  --papu-format auto
```

Opcjonalnie można dodać `--snapshot ID`. Obraz musi być przypięty pełnym
digestem. Kontener nie ma sieci ani portów, montuje wyłącznie nowy volume i jest
usuwany wraz z volume po dokładnej nazwie, labelu scratch i `run_id`. Cleanup
jest uzbrojony przed pierwszym create, więc obejmuje też zasób utworzony przez
komendę, która zwróciła non-zero; po remove ponownie dowodzi nieobecności.
Skrypt nie przyjmuje nazw
istniejących baz lub kontenerów i nie ma trybu aktywacji produkcji.

## 5. Druga instancja i failover — osobny projekt/ACK

Prawdziwy failover wymaga osobnego hosta, repozytoriów kodu, zatwierdzonego
źródła danych prywatnych, jawnej kolejności startu i health checks. A360-DR0
może dostarczyć zwalidowany scratch artefaktów; nie kopiuje ich do ścieżek
systemowych i nie uruchamia usług.

Po wyniku artifact/schema PASS nadal obowiązują osobne bramki na:

1. provisioning i izolację hosta;
2. checkout przypiętych commitów aplikacji;
3. bezpieczne odtworzenie zewnętrznych danych prywatnych;
4. import/start-order/health smoke;
5. instalację unitów i konfiguracji proxy;
6. przełączenie ruchu lub DNS.

Żaden krok tej listy nie jest autoryzowany przez sam PASS skryptu restore.

## 6. Test kwartalny

Minimalny test kwartalny zaczyna się od `--mode verify`. `artifact` i `drill`
uruchamiaj tylko w nowym scratchu `0700`, przy niskim loadzie, z zatwierdzonymi
budżetami i przypiętym obrazem. Zapisz:

- commit źródła, prefix snapshotu i jego wiek;
- wersję manifestu oraz liczniki satisfied/required;
- zakres dowodu i czas do PostgreSQL schema smoke;
- wynik cleanupu kontenera/volume i owner marker targetu;
- brakujące dowody, które utrzymują service RTO/RPO na HOLD.

Nie nazywaj czasu artifact/schema smoke pełnym RTO.

### DR1 HOLD przed pierwszym real drill

1. Potwierdzić realnym `verify`, że bieżący snapshot spełnia provenance v1;
   historycznego PASS nie przenosić.
2. Potwierdzić realny capacity/concurrent-backup guard bez restore.
3. Domknąć producer gap dwóch wymaganych unitów `backup-sentinel` albo dowieść,
   że są obecne w snapshotcie.
4. Zapewnić filesystem quota lub zaakceptować jawny brak tej warstwy.
5. Zatwierdzić bezpieczne wejście danych prywatnych oraz osobny
   app/import/health/start-order smoke.

Do tego czasu `artifact`, `drill`, pełny RPO i service RTO są HOLD. To nie jest
stan DONE.

## 7. Rollback scratcha

Przy RED skrypt usuwa tylko nowy target ze zgodnym owner markerem oraz zasoby
Docker ze zgodną dokładną nazwą, labelem scratch i `run_id`; błąd cleanupu lub
brak dowodu nieobecności ma osobny kod 90. Nie używaj prune,
wildcard cleanup ani usuwania zasobów bez zgodnego labelu. Przy PASS target
pozostaje do jawnego przeglądu i ręcznego usunięcia po weryfikacji owner markera.

Ten runbook nie zmienia procedur PITR ani aktywacji backupów z 2026-06-21. Ich
uruchomienie, restart lub deploy pozostają poza zakresem i wymagają osobnego ACK.
