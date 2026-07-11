# Audyt 360 — Wave 3 launch i kontrakt bezkolizyjny — 2026-07-11

Status: **RUNNING w tmux 65/66/67; code/test/read-only, zero operacji live**.

## Baseline i dowod potrzeby

- Wave 2 jest zamknieta, jej wyniki sa zapisane i pushed. Sesje 62/63/64 nie
  istnieja, a w ich miejsce nie dziala zaden test ani proces wykonawczy.
- Baza wszystkich trzech lane'ow to czysty dispatch master `e0fd1e4`; glowny
  dirty `eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md` pozostaje poza
  zakresem i nie jest kopiowany, stage'owany ani zmieniany.
- D1 jest potrzebny, bo firewall nie odroznia odziedziczonego naruszenia od
  naruszenia wprowadzonego decyzja. DR1A jest potrzebny, bo DR0 dowiodl source/
  fake, ale nie ma jeszcze bezsekretowego kontraktu carriera i app-smoke. OPS0
  jest potrzebny, bo limity systemd/OOM nie maja zweryfikowanego kontraktu z
  efektywnym procesem oraz cgroup.
- `at-214` nadal jest zaplanowany na 2026-07-13 12:15 UTC. D1 moze byc
  rozwijany, ale nie moze byc scalony przed jego odczytem. R0 pozostaje HOLD.
- ETAP 0 o 16:56 UTC potwierdzil zdrowe dispatch-shadow, panel-watcher,
  courier-api i parser v2; wszystkie mialy `NRestarts=0`. Stary failed
  `dispatch-night-guard` pochodzi z biegu sprzed aktualnego mastera, a jego
  kolejny automatyczny bieg jest 2026-07-12 01:15 UTC. Nie resetowano unitu.

## Lane'y i granice kolizji

| Sprint | Tmux / effort | Branch / worktree / base | Dozwolony write-set | Twardo zakazane |
|---|---|---|---|---|
| A360-D1 FIREWALL-EXEMPT-TRUTH | 65 / `ultra` | `engine/a360-d1-firewall-exempt-truth`; `/root/a360_d1_wt/dispatch_v2`; `e0fd1e4` | `core/invariant_firewall.py`, jeden potwierdzony po mapie punkt wiring/serializer, testy firewalla, unikalny raport lane'a | scoring, feasibility, selection i plan; `flags.json`; wspolny backlog/pamiec; merge do master; deploy/restart/dane live |
| A360-DR1A RESTORE-PREP | 66 / `high` | `ops/a360-dr1a-restore-prep`; `/root/a360_dr1a_wt/dispatch_v2`; `e0fd1e4` | `docs/deploy/ha-lite/restore_from_restic.sh`, jego hermetyczne fake testy, HA-lite runbook jezeli wynika z mapy, unikalny raport lane'a | realny credential/restic/decrypt/Docker/DB; systemd/nginx/DNS; wspolny backlog/pamiec; deploy/restart |
| A360-OPS0 RUNTIME-SYSTEMD-EVIDENCE | 67 / `high` | `ops/a360-ops0-runtime-evidence`; `/root/a360_ops0_wt/dispatch_v2`; `e0fd1e4` | nowe read-only narzedzie `tools/runtime_systemd_evidence.py`, hermetyczne testy i unikalny raport lane'a | EnvironmentFile, `/proc/*/environ`, `.env`, sekret; `/etc`, unit/drop-in, runtime state; daemon-reload/restart/OOM |

Kazdy agent najpierw weryfikuje root cause i tworzy mape kompletności. Jezeli
mapa wykaze konieczny dodatkowy plik, zatrzymuje edycje i opisuje konflikt
integratorowi; nie rozszerza po cichu write-setu. Wspolne karty, backlog i repo
pamieci maja jednego writera: integratora w tmux58.

Kontrolowany sibling `flags.json` w kazdym worktree jest tylko symlinkiem do
kanonicznego carriera i nie jest plikiem repo ani write-setem sprintu. Testy
maja nadal przejsc przez root HERMETIC-GUARD i tymczasowe flags/state/logi.

## Weryfikacja startu

O 17:03 UTC wszystkie pane'y byly zywe (`pane_dead=0`), mialy foreground
`codex`, poprawny cwd i zaczely bootstrap bez edycji:

| Tmux | Pane PID | Zweryfikowany effort | CWD |
|---:|---:|---|---|
| 65 | 1409999 | `ultra` | `/root/a360_d1_wt/dispatch_v2` |
| 66 | 1410009 | `high` | `/root/a360_dr1a_wt/dispatch_v2` |
| 67 | 1410018 | `high` | `/root/a360_ops0_wt/dispatch_v2` |

## Wplyw na zachowanie Ziomka

- D1 nie zmienia wyboru kuriera. Daje prawdziwa diagnostyke `EXEMPT` dla stanu
  odziedziczonego i `VIOLATION` tylko dla nowego naruszenia, z metryka w realnym
  torze serializacji/jsonl.
- DR1A nie wykonuje restore. Buduje fail-closed, testowalny tor do pozniejszego
  game-day: syntetyczny carrier, provenance, fake import/health/start-order i
  exact cleanup.
- OPS0 niczego nie stroi. Zbiera niewrazliwa prawde o PID, RSS, swap, cgroup,
  PSI, restartach i precedencji konfiguracji, aby kolejny sprint nie ustawil
  limitu, ktory ubije dispatcher.

## Testy, ryzyka i rollback

- D1: goldeny EXEMPT/VIOLATION/PASS, mutation probes, parity decyzji/planu,
  test realnego serializer/jsonl, DEFAULT + HERMETIC_STRICT i entropy. Ryzyko
  skazenia at-214 jest ograniczone przez zakaz merge. Rollback: revert commita
  instrumentacyjnego; brak flagi i live.
- DR1A: fake carrier/restic/docker/app, negative controls, strict SQL,
  provenance/freshness/manifest, exact-run cleanup oraz DEFAULT + STRICT.
  Rollback: revert source/test; nie ma zasobow live do sprzatania.
- OPS0: fixture systemctl/proc/cgroup, negative control zakazanych odczytow,
  `UNKNOWN` zamiast zgadywanego `SAFE`, co najmniej dwa timestampowane odczyty
  read-only bez sztucznego obciazenia. Rollback: revert tool/report.

Targeted testy moga biec rownolegle. Pelne regresje D1/DR1A i ewentualna pelna
regresja OPS0 sa serializowane przez `/tmp/ziomek_full_regression.lock`. OPS0
nie moze nazwac pomiaru reprezentatywnym, gdy lock jest zajety albo trwa pytest/
mutation; taki odczyt ma status `CONTAMINATED/UNKNOWN`, bez tworzenia timera.

Sobota jest oknem blackout. Ten launch nie zawiera biznesowego ACK na flage,
HARD/SOFT, migracje, dane, deploy, restart ani DR1B. Kazda taka operacja wymaga
osobnego, swiezego zlecenia Adriana.
