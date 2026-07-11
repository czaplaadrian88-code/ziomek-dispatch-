# AUDIT360 — start trzech bezkolizyjnych sprintow branch-only

Data startu: 2026-07-11 20:36 UTC

Integrator: tmux58

Baza dispatch: `307242d44080d98dd38143d5feae9304f9198a30`

Baza repo workspace/Papu: `51dfe90b9435db37bb8e5453ea87718f3eebfc46`

## 1. Dlaczego te trzy zadania

Najwyzsze niedomkniete lane'y ENGINE/PLAN nie sa obecnie wykonywalne bez
naruszenia bramek: R0 i D1 czekaja na pelny odczyt at-214 z 13.07, H1 dodatkowo
na decyzje B-01/B-02, a PLAN na H1. Nie zostaly ominiete ani zdegradowane.

Wybrane prace sa najwyzej priorytetowymi potwierdzonymi problemami, ktore mozna
naprawiac teraz bez dotykania badanego silnika, wspolnych writerow i produkcji:

| Kolejnosc | Sprint | Problem nadal obecny | Co zmieni gotowy kod | Effort |
|---:|---|---|---|---|
| 1 | `A360-A0 ETA-CALIBRATION-TRUTH` (`ALGO-01/02`) | Cechy godziny/load sa wyprowadzane z outcome, a promocja dopuszcza model do 2% gorszy na innym supportcie. | Model bedzie uzywal tylko cech dostepnych w chwili decyzji; champion i challenger beda oceniane parami na tym samym frozen holdoucie, a brak dowodu da HOLD. | max |
| 2 | `A360-I1 PAPU-BRIDGE-RECOVERY` (`INTE-03`) | Po 2xx bez read-back stan zapisuje `panel_zid=None`, usuwa probe i pozniej bezterminowo pomija backward sync. | Nastepny tick odzyska istniejace zlecenie idempotentnie albo pozostawi jawny retry/HOLD; nie powstanie drugi order, a poprawny read-back odblokuje zwrot kuriera/czasow. | high |
| 3 | `A360-N0 NIGHT-GUARD-TRUTH` (`TEST-04/05`) | Mianownik suity moze pelzac w dol, hard-error nie jest trwałym czerwonym stanem, a dwa `xfail(strict=False)` XPASS-uja bez ownera. | Straznik bedzie fail-closed po nodeidach i wersjonowanym manifeście; hard-error nie stanie sie baseline'em, a food-age dostanie deterministyczny kontrakt lub jawna kwarantanne z ownerem. | high |

Kazdy efekt opisuje zachowanie kodu po przyszlym, osobno zatwierdzonym wydaniu.
Sam ten start nie zmienia zachowania Ziomka live.

## 2. Sesje, branche i granice zapisu

| tmux | Branch / worktree | Dozwolony write-set | Wykluczenia |
|---|---|---|---|
| 68 | `evidence/a360-a0-eta-calibration-truth` w `/root/a360_eta0_wt/dispatch_v2` | `tools/eta_calibration/**`, lokalne testy, raport A0 | engine/core/common, flagi, state, systemd, wspolne docs |
| 69 | `integration/a360-i1-papu-bridge-recovery` w `/root/a360_papu_i1_wt` | `scripts/papu_dispatch_bridge/{bridge.py,panel.py,papu_client.py,tests/**,A360_I1_*.md}` tylko gdy konieczne | `restaurant_map.json`, `city_map.json`, live state, backend, inne mosty, wspolne docs |
| 70 | `quality/a360-n0-night-guard-truth` w `/root/a360_n0_wt/dispatch_v2` | `tools/night_guard.py`, manifest/helper, testy night-guard i food-age, raport N0 | engine/core/common, flagi, state, instalacja unity/timera, wspolne docs |

Macierz kolizji jest pusta: A0 i N0 sa w osobnych worktree oraz nie wspoldziela
plikow; I1 jest w innym repo i innej rodzinie integracyjnej. Backlog, memory,
timeline, registry i `/root/handover` edytuje wylacznie integrator.

Chroniony dirty w glownym dispatch checkout:
`eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md`. Nie zostal skopiowany,
zmieniony ani stage'owany. W glownym repo workspace pozostaja cudze
`scripts/papu_dispatch_bridge/restaurant_map.json` i
`scripts/papu_dispatch_bridge/DEPLOY_PROCEDURE.md`; worktree I1 ich nie przejal.

## 3. Baseline, testy i ochrona canary

Kanoniczny DEFAULT na dispatch `307242d`, przez venv i globalny lock:

- start: `2026-07-11T20:24:09Z`;
- koniec: `2026-07-11T20:29:06Z`;
- wynik: 5126 passed, 27 skipped, 8 xfailed, 2 xpassed, 0 failed;
- 147 warnings, 285.07 s.

Dwa XPASS sa jednym z jawnych wejsc N0, a nie ukryta zielona regresja. Kazdy
lane uruchamia targeted baseline przed edycja, known-answer i mutation probe.
Pelne DEFAULT/STRICT A0 i N0 musza przejsc przez
`flock -x /tmp/ziomek_full_regression.lock`; timestampy trafia do raportow i
sensitivity at-214. I1 nie uruchamia pelnej suity dispatch bez dowodu callera;
runtime import sprawdza systemowym Pythonem, bez instalacji zaleznosci.

## 4. Produkcja, bramki i rollback

Na starcie wykonano wyłącznie odczyt ETAP 0, utworzenie worktree/branchy,
zamrozonych carrierow 0444 i uruchomienie procesow Codexa. Nie wykonano:

- flipa flagi ani zmiany efektywnego srodowiska procesu;
- zapisu/migracji danych runtime;
- merge do mastera kodu lane'ow;
- deployu, instalacji unity/timera, daemon-reloadu ani restartu;
- HTTP do live Papu/panelu ani realnego restore.

Nie jest potrzebna nowa decyzja biznesowa do pracy branch-only. Osobny ACK i
ponowny ETAP 0 beda potrzebne przed wdrozeniem mostu, instalacja night-guarda,
jakimkolwiek podpieciem ETA, zmiana flagi lub restartem. R0/D1/H1 pozostaja na
dotychczasowym HOLD.

Rollback przed wydaniem jest repozytoryjny: przerwac sesje, zachowac raport,
a zatwierdzony commit lane'a cofnac przez `git revert <commit>`. Worktree mozna
usunac dopiero po odbiorze i pushu. Poniewaz nie ma zmiany live, rollback
runtime jest N-D.

## 5. Stan uruchomienia i odbior

Zweryfikowano, ze tmux68/69/70 maja `pane_current_command=codex`, poprawne cwd,
`pane_dead=0` i modele `gpt-5.6-sol` odpowiednio max/high/high. Wszystkie trzy
sesje rozpoczely obowiazkowy bootstrap. Pelne prompty:

- `/root/A360_A0_CODEX_PROMPT.md`;
- `/root/A360_I1_CODEX_PROMPT.md`;
- `/root/A360_N0_CODEX_PROMPT.md`.

Odbior kazdego sprintu wymaga mapy kompletnosci, testow/mutation, finalnego
SHA i pushu brancha, raportu host-load, dowodu braku live oraz gotowego revertu.
Status RUNNING nie oznacza DONE ani zgody na integracje.
