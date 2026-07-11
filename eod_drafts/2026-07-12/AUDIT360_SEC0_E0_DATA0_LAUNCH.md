# AUDIT360 — SEC0/E0/DATA0 launch — 2026-07-12

Status: **RUNNING BRANCH-ONLY, ZERO LIVE**. Polecenie Adriana `dalej` uruchamia
prace analityczne i implementacyjne w osobnych worktree. Nie jest ACK na
firewall/bind/credential, migracje lub chmod danych live, retencje/delete,
flipy, systemd, deploy ani restart.

## 1. Wspolny ETAP 0

- dispatch base: `1cf6ae4bdc52223ff0accafdea5fdadd593c70cf`,
  master=origin przed startem;
- glowny checkout ma tylko chroniony, cudzy dirty
  `eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md`;
- frozen carrier per worktree: kopia 0444 `flags.json`, SHA256
  `568436f3de693d048a73bf1a2ba5c23e65191a350ef2e566fa5b6f34ace848bf`;
- live przed startem: shadow PID573430, watcher PID3659486, courier API
  PID925329 active/running, Result success, NRestarts0; flags bez zmiany;
- `atq` ma tylko 214, 13.07 12:15 UTC. R0/D1/H1 nadal HOLD.

Swiezy wspolny baseline, pod `/tmp/ziomek_full_regression.lock`:

- DEFAULT `2026-07-11T23:05:55Z..23:10:40Z`;
- 5143 passed, 24 skipped, 8 xfailed, 0 failed/XPASS, 147 warnings,
  281,88 s;
- +3 pass/-3 skip wobec poprzedniego biegu jest znanym zegarowym self-skip
  `test_preshift_window`; regresje oceniamy lista nodeidow, nie sama suma;
- caly przedzial trafia do sensitivity `at-214`.

## 2. Sesje i wlasciciele

| Tmux / effort | Sprint | Branch / worktree | Wlasciciel plikow | Twarda granica |
|---|---|---|---|---|
| 71 / `max` | `A360-SEC0 HOST-BOUNDARY-CREDENTIAL` | `security/a360-sec0-host-boundary-truth` / `/root/a360_sec0_wt/dispatch_v2` | nowe read-only tools, syntetyczne testy, runbook/template i raport SEC0 | zero zmiany hosta, sieci, provider FW, credentialu, /etc, Docker/systemd live |
| 72 / `max` | `A360-E0 EVENT-RELIABILITY-FSM` | `reliability/a360-e0-event-fsm` / `/root/a360_e0_wt/dispatch_v2` | event store/retry, FSM/lifecycle, migracja dry-run, replay/DLQ, testy i raport E0 | zero live DB/state, feasibility/scoring/plan CAS, policy/worker ON |
| 73 / `high` | `A360-DATA0 PRIVATE-LEDGER-RETENTION` | `privacy/a360-data0-ledger-retention` / `/root/a360_data0_wt/dispatch_v2` | writer/redactor/rotator/reader, dry-run retencji, templates, testy i raport DATA0 | zero odczytu tresci live PII, chmod/migracji/delete/deploy; brak merge przed at-214 |

Prompty 0600 i ich SHA256:

- SEC0 `/tmp/a360_sec0_prompt.md`:
  `18bf35d4a1bf8fb3943a7dd2592c1dbfc9e2ac4873fdcdd9519dca6512af22e3`;
- E0 `/tmp/a360_e0_prompt.md`:
  `facd941623f2a1dd0f7fe42d75128c7852285f306b337fab07456eb7f2ae3bac`;
- DATA0 `/tmp/a360_data0_prompt.md`:
  `787abaf2098eef2b15be27000abaab234db2f33a0ff8b24526ba39d6f34050aa`.

## 3. Problem, zmiana i bramki

### SEC0

Dowod: `0.0.0.0:8767`, `0.0.0.0:9222` i `[::]:9222` sluchaja; UFW jest
inactive, INPUT ACCEPT, a provider-side filtr jest `UNKNOWN`. Agent ma ustalic
ownera/provenance bez argv/env/secretow i zbudowac read-only audit, negative
controls oraz wykonywalny runbook. Po przyszlym, osobno zatwierdzonym wydaniu
zbędny publiczny bind zniknie, API dostanie minimalna granice, a ujawniony
credential zostanie zastapiony nowym revision. Ryzyko: odciecie hosta i uslug;
live wymaga drugiej sesji administracyjnej, backupu i ACK.

### E0

Dowod startowy: backlog ma 106 historycznych `NEW_ORDER=failed`, brak jednego
attempt/error/next-retry/DLQ, a zly pickup timestamp moze byc zastepowany
`now()`. Istnieje stary, clean/pushed material `7eda1b0` + `32745f9`, ktory
trzeba zreviewowac semantycznie na aktualnej bazie, nie cherry-picknac slepo.
Retry/DLQ i FSM maja jednego ownera. Po pelnym, osobno zatwierdzonym wydaniu
transient dostanie limitowany retry, poison DLQ, a illegal/timestamp quarantine
bez falszowania SLA. Retry policy, worker ON i migracja live pozostaja decyzja.

### DATA0

Dowod wyłącznie metadata: `shadow_decisions.jsonl` mode 0644 i ok. 22,7 MB;
world-record directory 0755, daily files 0644, dzisiejszy ok. 188 MB, poprzedni
do ok. 227 MB. Agent nie czyta tresci live. Buduje atomowe 0600, redakcje,
writer-aware rotate/reopen, backward reader i `would-delete` dry-run. Po
przyszlym wydaniu dane beda mniej dostepne i ograniczone rozmiarem bez utraty
kontrolowanego replayu. B-05 ustala retencje; zero delete przed decyzja i zero
zmiany corpus przed `at-214`.

## 4. Bezkolizyjnosc i testy

- SEC0 pisze nowe ops/security artefakty i nie dotyka lifecycle ani ledgerow.
- E0 jest jedynym ownerem `event_bus/state_machine/panel_watcher` w tej fali.
- DATA0 jest jedynym ownerem writerow/readerow ledger/world-record.
- Wspolny backlog, logic reference, audit queue, memory i handover edytuje tylko
  integrator.
- Pelne DEFAULT/STRICT sa serializowane jednym flockiem. Kazdy lane zapisuje
  UTC start/end do sensitivity `at-214`.
- Wymagane: mapa kompletnosci, golden+negative/mutation, targeted, pelny
  DEFAULT i STRICT, diff-check, checkery, entropy po fundamencie oraz
  niezalezny review integratora.

## 5. Rollback startu

Przed merge/deploy nie istnieje rollback runtime. Zatrzymanie sesji pozostawia
live bez zmian. Po commicie rollback branchowy to jawny `git revert` konkretnego
commita. Worktree i branche usuwac dopiero po odbiorze, push parity i trwalym
handoffie. Zadnego reset/stash/checkout cudzych zmian.
