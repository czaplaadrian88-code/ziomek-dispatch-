# Audit360 — N0 follow-up LIVE i start V214 / SEC1 / E1

Status: **N0 FOLLOW-UP DONE/LIVE; 3 LANE'Y RUNNING**. Data: 2026-07-12 UTC.
Integrator i jedyny FLIPMASTER: tmux58.

## Wynik w skrócie

Pierwszy zwykły nocny bieg N0 nie ujawnił regresji testów. Zadziałał
fail-closed, ponieważ manifest v4 nie znał 11 nowych, zreviewowanych testów
SEC0. Przy okazji potwierdzono realną lukę prywatności: atomowy replace historii
odtwarzał plik jako `0644`. Follow-up zaktualizował dokładny mianownik do
manifestu v5 i wymusił `0600` przy każdym zapisie. Dokładny systemd E2E jest
zielony. Nie restartowano żadnego procesu decyzyjnego.

Po zwolnieniu pełnego locka uruchomiono trzy rozłączne sesje: V214 high,
SEC1 max w trybie SOURCE/PREP i E1 max branch-only.

## Problem, root cause i mapa kompletności N0

Zwykły run `dispatch-night-guard.service` trwał
`2026-07-12T01:15:01Z..01:19:56Z` i zakończył się exit 1. Zagregowany wpis miał
5154 passed, 24 skipped, 8 xfailed, zero fail/XPASS/error. Jedynym alertem był
dwukrotnie sprawdzony `SUITE-CONTRACT-UNEXPECTED(11)` dla nowego pliku
`tests/test_host_boundary_audit.py`. Guard poprawnie odmówił samoczynnego
poszerzenia mianownika.

Drugi problem był u źródła writera. `append_history()` zapisywał przez stały
temp i `os.replace`, ale nie ustawiał trybu nowego inode. Przy umask 022 żywy
`night_guard_history.jsonl` miał `0644`, mimo wcześniejszego backupu `0600`.

| Miejsce | Rola | Writer/consumer | Dotknięte | Dowód/test |
|---|---|---|---|---|
| `tools/night_guard.py::append_history` | atomowy writer historii | writer | TAK | `O_NOFOLLOW`, `fchmod(0600)`, fsync, replace, końcowy chmod |
| `tools/night_guard.py::load_history` | czytelnik historii | consumer | N-D | format JSONL bez zmiany; systemd E2E odczytał historię |
| `tools/night_guard_suite_manifest.json` | exact denominator/outcomes | producer contract | TAK | updater v5, 5183 nodeidy, 12 addytywnych, 0 usuniętych |
| `tests/test_night_guard_truth.py` | oracle prywatnego replace | test | TAK | stary plik i stale tmp `0644` -> wynik `0600`, treść zachowana |
| `tests/test_host_boundary_audit.py` | nowy zreviewowany coverage SEC0 | suite consumer | TAK | 11 nodeidów dodanych przez fail-closed updater |
| `dispatch-night-guard.service/.timer` | dokładny runtime consumer | systemd | TAK | E2E 08:25:10Z..08:30:02Z, Result success, timer waiting |
| OnFailure | negative path | consumer | N-D | realny run 01:15 wywołał OnFailure; nie wykonywano sztucznej awarii po fixie |
| decyzje/HARD/SOFT/flagi | hot path | N-D | N-D | trzy zmienione pliki nie są importowane przez procesy decyzyjne |

## Kod, testy i release

- Branch/push: `fix/a360-n0-manifest-mode-20260712`:
  - `16fa397aa8cd91049bcbede003fc4ab091b7092b` — prywatny writer historii;
  - `a9e6c7cf03f8bc461e4a29d94b55e87aa554d63a` — manifest v5.
- Kodowy punkt wydania master/origin przed tym docs-handoffem:
  - `8bf5f72` — fix writera;
  - `0891b06e9e894d88d6bd8a8b9dd9f837cf12a1e0` — manifest i zweryfikowany kod.
  Commit zawierający ten raport/backlog jest docs-only na wierzchu; jego SHA
  zapisuje pushowany CURRENT HANDOFF w repo memory.
- Rollback tag: `a360-n0-followup-pre-live-20260712` @ `43ad973`.
- Verified tag: `a360-n0-followup-live-verified-20260712` @ `0891b06`.

Dokładne testy:

- focused N0: 12 passed;
- odtworzenie pierwszego czerwonego updatera
  `[2026-07-12T08:06:58Z,08:12:12Z]`: 9 fail przez brak izolowanego carriera
  flag w nowym worktree, manifest niezmieniony; po instalacji kopii `0444`
  dokładnie te testy: 9 passed;
- updater DEFAULT `[08:12:53Z,08:17:36Z]`: manifest v5, 5183 nodeidy,
  5155 passed, 24 skipped, 8 xfailed, 147 warnings, zero fail/XPASS;
- focused N0+SEC0: 23 passed; exact collect względem v5: 0 missing/unexpected;
- STRICT `[08:18:54Z,08:23:18Z]`: 5105 passed, 74 skipped, 8 xfailed,
  147 warnings, zero fail/XPASS;
- flag lifecycle: 505/505, 0 błędów; entropy AUTO bez wzrostu
  (`flag_div=1`, `poison_live=11`);
- dokładny systemd E2E `[08:25:10Z,08:30:02Z]`: 5155/24/8,
  verdict `OK`, `contract_ok=true`, collected hash równy manifestowi,
  Result success, ExecMainStatus0, NRestarts0.

Wszystkie ciężkie przedziały powyżej są host-load do sensitivity at-214.
Pierwszy updater był nieważnym dowodem testowym, ale pozostaje rzeczywistym
obciążeniem hosta.

## Live, backup i rollback

Live zmieniły się tylko dwa pliki narzędzia/test-contractu na masterze i jeden
kontrolowany restart one-shot `dispatch-night-guard.service`. Historia po E2E
ma mode `0600`, owner root, manifest v5 i werdykt OK. Timer jest enabled,
active/waiting, następny run 2026-07-13 01:15 UTC.

Backup 0600 przed E2E:
`dispatch_state/backups/night_guard_history.pre-n0-followup-20260712T0824Z.jsonl`.
Rollback: zatrzymać one-shot/timer, jawnie revertować najpierw `0891b06`, potem
`8bf5f72`, pozostawić historię `0600` i przywrócić backup tylko przy zatrzymanym
writerze, jeżeli sama treść wymaga powrotu. Nie używać reset/checkout i nie
restartować `dispatch-telegram`.

`dispatch-shadow` PID573430, `dispatch-panel-watcher` PID3659486,
`dispatch-sla-tracker` PID2998575 i `courier-api` PID925329 pozostały
active/running, Result success, NRestarts0. Parser v2 jest healthy. Flagi mają
niezmienione mtime `2026-07-11 10:27:12Z` i SHA-256
`568436f3de693d048a73bf1a2ba5c23e65191a350ef2e566fa5b6f34ace848bf`.
Nie było flipa, migracji, zmiany danych biznesowych, sieci, credentialu ani
restartu shadow/watcher/SLA/API.

## Zamknięte i nowe sesje tmux

Jako potwierdzone zakończone zamknięto sesje 54, 55, 60, 68, 69 i 70. Nie
ruszono tmux50 z cudzym WIP, aktywnego tmux58 ani sesji o nieustalonym ownerze.

Trzy nowe sesje wystartowały `2026-07-12T08:34:14Z`, każda z base `0891b06`
i własnym carrierem flag `0444`:

1. tmux74, `gpt-5.6-sol high`, branch
   `evidence/a360-v214-canary-disposition`, worktree
   `/root/a360_v214_wt/dispatch_v2`. Tylko preflight/WAIT do realnego outputu
   at-214; bez wcześniejszego joba, live replayu, pełnej suity i zmian runtime.
2. tmux75, `gpt-5.6-sol max`, branch
   `security/a360-sec1-host-remediation`, worktree
   `/root/a360_sec1_wt/dispatch_v2`. Wyłącznie SOURCE/PREP; obecne ACK nie
   obejmuje firewall/provider/bindu/credentialu/kontenera ani restartu live.
3. tmux76, `gpt-5.6-sol max`, branch
   `reliability/a360-e1-durable-outbox`, worktree
   `/root/a360_e1_wt/dispatch_v2`. Branch-only envelope, failure journal,
   outbox i receipts; zero migracji/worker/flag/deploy/restart live.

Wspólne backlogi, memory, handover, merge, tagi i release pozostają wyłączną
własnością tmux58. Pełne testy wykonawców muszą używać
`/tmp/ziomek_full_regression.lock` i zapisywać dokładne przedziały UTC.

## Stan otwarty

- `atq` nadal ma tylko job 214 na 2026-07-13 12:15 UTC; nie ma auto-flipa.
- R0/D1/H1 pozostają HOLD do prawdziwego V214 i dalszych bramek.
- SEC1 live nadal wymaga operation-specific ACK i maintenance.
- E1 nie może wejść do mastera ani ON przed pełnym kontraktem, review oraz
  osobnymi bramkami migracji/policy/worker/deploy.
- Chroniony dirty `eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md`
  pozostał nietknięty.
