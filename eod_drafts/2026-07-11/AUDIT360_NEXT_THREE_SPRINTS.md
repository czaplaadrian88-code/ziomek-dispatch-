# AUDIT360 — kolejne trzy bezkolizyjne sprinty — przygotowanie 2026-07-11

Status: **RUNNING BRANCH-ONLY od 2026-07-11 23:11 UTC** w tmux71/72/73.
Kolejnosc wynika z dyrektywy
stabilnosc/jakosc/skala, potwierdzonego runtime oraz aktualnych bramek. Sprinty
maja rozlaczne rodziny plikow. Operacje live pozostaja osobnym etapem i nie sa
autoryzowane samym przygotowaniem karty.

R0/D1/H1 nie sa teraz poprawnym wyborem: `at-214` konczy 48-godzinny canary
13.07 12:15 UTC, a H1 dodatkowo wymaga B-01/B-02. PLAN czeka na H1. Ponizsze
trzy tory wykorzystuja czas bez omijania tych bramek.

## 1. Kolejnosc i efekt koncowy

| Priorytet | Sprint / effort | Problem potwierdzony teraz | Co zmieni po pelnym zakonczeniu |
|---:|---|---|---|
| 1 | `A360-SEC0 HOST-BOUNDARY-CREDENTIAL` / `max` | Host ma publiczne listenery na `0.0.0.0:8767`, `0.0.0.0:9222` i `[::]:9222`; lokalny UFW jest inactive, provider-side filtr pozostaje UNKNOWN. Audit ma takze otwarta rotacje ujawnionego credentialu. | CDP/port administracyjny przestanie byc publiczny, courier API bedzie dostepne tylko z zatwierdzonych zrodel, a stary credential zostanie uniewazniony bez zapisania wartosci w artefaktach. |
| 2 | `A360-E0 EVENT-RELIABILITY-FSM` / `max` | Backlog ma 106 historycznych `NEW_ORDER=failed`, bez jednego retry/DLQ contractu; zly pickup timestamp bywa zastapiony `now()`. Istniejaca branchowa Faza A `7eda1b0` + `32745f9` nie jest w masterze. | Bledy przejsciowe beda mialy limitowany retry/backoff, poison trafi do DLQ, a nielegalne przejscie lub uszkodzony czas zostana jawnie odrzucone/kwarantannowane zamiast zmieniac prawde SLA. |
| 3 | `A360-DATA0 PRIVATE-LEDGER-RETENTION` / `high` | `shadow_decisions.jsonl` ma mode 0644 i ok. 22,7 MB; world records z danymi adres/GPS powstaja codziennie. Retencja i pseudonimizacja nadal nie maja jednego writer-aware contractu. | Nowe artefakty beda domyslnie 0600, wrazliwe identyfikatory/adresy zostana ograniczone, a rotacja i retencja przestana rosnac bez limitu bez utraty kontrolowanego replayu. |

## 2. Sprint SEC0 — HOST-BOUNDARY-CREDENTIAL

### Zakres i mapa kompletności

| Miejsce | Rola | Writer/consumer | Dotkniete | Powod / test |
|---|---|---|---|---|
| listener `:9222` v4/v6 | granica CDP/admin | unit/container/proxy | TAK | ustalic ownera i bind; `ss` z hosta oraz kontrolowany test allowed/denied |
| listener `:8767` | courier API | systemd + reverse proxy/provider FW | TAK | minimalna allowlista i health owner/foreign |
| provider firewall | zewnetrzna granica | panel dostawcy | TAK po dostepie | brak dowodu nie moze stac sie `secure`; test z drugiej sieci |
| credential carrier | secret distribution | service/drop-in/store | TAK | rotacja bez odczytu/emisji wartosci, old-token negative test |
| dispatch engine/flags | decyzje Ziomka | runtime | N-D | nie nalezy do granicy hosta; zero flipa |

Planowany branch dla wersjonowanych narzedzi/runbooku:
`security/a360-sec0-host-boundary-truth`; planowany worktree:
`/root/a360_sec0_wt/dispatch_v2`. Zmiany systemowe nie sa maskowane commitem i
dostaja osobny transkrypt redacted.

### Bezpieczeństwo wydania

- Najpierw dowod ownera listenera, efektywnego bindu i provider-side rules;
  brak dowodu = `UNKNOWN`, nie `PASS`.
- Backup unitow/regul, druga dzialajaca sesja administracyjna i kill-switch
  uslugi sa wymagane przed zmiana sieci.
- Rotacja nie czyta ani nie drukuje starej/nowej wartosci. Rollback nie wraca
  do ujawnionego credentialu; tworzy kolejny nowy revision.
- Testy: `ss` v4/v6, lokalny health, allowed source, denied source,
  expired/old credential negative control, journal bez sekretu, NRestarts.
- Wymagany biznesowy ACK: rotacja credentialu, zmiana provider firewall/bindu
  i ewentualny restart API/proxy. Nie wykonywac w peaku.

## 3. Sprint E0 — EVENT-RELIABILITY-FSM

Z-P0-05 i Z-P1-01 musza miec jednego ownera: oba zmieniaja lifecycle eventu i
stanu. Rozdzielenie retry od FSM stworzyloby retry nielegalnego przejscia albo
podwojne writery statusu.

### Baza i write-set

- nowy branch: `reliability/a360-e0-event-fsm` z aktualnego mastera;
- worktree: `/root/a360_e0_wt/dispatch_v2`;
- istniejacy `/root/sprint2_wt/dispatch_v2` @ `32745f9` jest tylko materialem
  do semantic rebase/review, nie baza do slepego cherry-picku;
- rodziny plikow: `event_bus.py`, `event_retry.py`, migracja addytywna event
  store, `order_fsm.py`, lifecycle call-site'y w `state_machine.py`,
  `panel_watcher.py`, `parcel_lane_merge.py`, replay DLQ i dedykowane testy;
- zakaz: feasibility, scoring, selection, flags live, plan CAS i dane runtime.

### Zachowanie, testy i bramki

- Zdarzenie dostaje atomowy `attempt_count`, klase bledu, `next_retry_at` i
  terminalny DLQ; idempotency key blokuje podwojne efekty.
- FSM ma jedna jawna mape przejsc; zly timestamp nie dostaje `now()`, tylko
  quarantine/fail-loud z zachowaniem surowego dowodu w bezpiecznym polu.
- Mutation probes: usuniecie limitu prob, idempotency albo przejscia FSM musi
  zrobic RED. E2E obejmuje restart miedzy zapisem eventu i efektem.
- Migracja: addytywna, idempotentna, transakcyjna, dry-run i backward reader;
  zadnego live DB write bez ACK.
- Faza source/log-only moze pozostac default OFF. Retry policy (liczba prob,
  backoff, klasy transient/permanent) i wlaczenie workera wymagaja decyzji
  biznesowej; nie zgadywac.
- Rollback: worker OFF, drain zatrzymany, backward-compatible schema pozostaje,
  jawny revert kodu. Nie usuwac DLQ ani dowodow nielegalnych przejsc.

## 4. Sprint DATA0 — PRIVATE-LEDGER-RETENTION

Ten sprint zaczyna sie od inwentaryzacji wszystkich writerow. Sam
`logrotate/copytruncate` jest zabroniony dla kanonicznego ledgera, bo moze
zgubic albo rozdwoic zapis.

### Baza i write-set

- branch: `privacy/a360-data0-ledger-retention`;
- worktree: `/root/a360_data0_wt/dispatch_v2`;
- rodziny plikow: producenci `shadow_decisions` i `world_record`, wspolny
  atomiczny writer/rotator, schema/redactor, offline replay reader, dedykowany
  unit/timer/runbook oraz testy permissions/rotation/recovery;
- zakaz: Papu bridge, event/FSM, feasibility/scoring, flags decyzyjne i
  modyfikacja live corpus przed werdyktem `at-214`.

### Zachowanie, testy i bramki

- Nowe pliki maja `0600` od chwili atomowego utworzenia, nie dopiero po
  post-factum chmod.
- Adres/GPS/nazwisko/ID przechodza klasyfikacje producer→schema→reader;
  pseudonim musi byc stabilny tylko w zatwierdzonym zakresie replayu.
- Rotacja writer-aware: fd/reopen/rename protocol z crash tests; zero
  `copytruncate`, zero skanowania sekretow i zero PII w fixture/raporcie.
- Replay na zanonimizowanym golden corpusie ma parity decyzji; mutation redakcji
  i uprawnien musi zrobic RED. Mierzymy redukcje rozmiaru i czas appendu.
- B-05 ustala okres retencji oraz prawo do usuwania. Do decyzji wolno zbudowac
  schema, dry-run i raport `would-delete`; nie wolno kasowac danych live.
- Wszelki chmod/migracja/rotacja live wymaga backupu, dry-run i osobnego ACK.
  Rollback przywraca writer i reader kompatybilny wstecz; nie odtwarza
  skasowanych danych, dlatego delete pozostaje ostatnia bramka.

## 5. Macierz bezkolizyjności

| Lane | Glowny owner | Moze rownolegle | Nie moze rownolegle |
|---|---|---|---|
| SEC0 | host/network/credential | branch-only E0 i DATA0 | drugi operator sieci, credential, deploy/restart |
| E0 | event store + lifecycle FSM | SEC0 prep, DATA0 branch | PLAN/ENGINE oraz drugi writer `panel_watcher/state_machine` |
| DATA0 | ledger/world-record writer | E0, SEC0 prep | `at-214` w fazie live, drugi rotator/migracja danych |
| RELEASE | backlog/memory/handoff | nic | wszystkie lane'y; jeden integrator/FLIPMASTER |

Pelne suity E0 i DATA0 musza byc serializowane wspolnym flockiem, a ich
timestampy wejda do sensitivity `at-214`. SEC0 nie wykonuje zmian hosta w tym
samym oknie co restart/deploy ktorejkolwiek innej uslugi.

## 6. Kryterium startu

1. SEC0: swiezy ETAP 0, owner listenerow, druga sesja administracyjna i jawny
   ACK dla rotacji/sieci/restartu.
2. E0: swiezy master po tym handoffie, audit istniejacego `32745f9`, brak
   aktywnego ownera tych samych call-site'ow; development branch-only.
3. DATA0: development branch-only moze ruszyc, ale zadna zmiana live corpus,
   uprawnien ani rotacji przed odczytem `at-214`; delete dopiero po B-05.

Kazda sesja startuje poleceniem:

```text
codex --sandbox danger-full-access --ask-for-approval never
```

Okna uruchomil integrator po zielonym baseline 5143/24/8/0fail/0XPASS. Karta
nie przenosi ACK z zamknietego wydania A0/I1/N0: wszystkie trzy lane'y sa
branch-only, a operacje live pozostaja osobnymi bramkami.
