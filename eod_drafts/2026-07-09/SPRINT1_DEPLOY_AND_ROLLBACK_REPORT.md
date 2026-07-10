# Sprint 1 — raport deployu, pełnego rollbacku i ponownego deployu

Data: 2026-07-09/10
Zakres: czynności po ACK następujące po raporcie implementacyjnym
Źródło wdrożenia: `/root/sprint1_wt/dispatch_v2`
Cel: `/root/.openclaw/workspace/scripts/dispatch_v2`

## Wynik

Sync kodu do live repo został wykonany dla jawnej listy 36 plików. Po późniejszej
instrukcji użytkownika zatrzymano dalsze mutacje aż do kolejnego jawnego ACK.
Po tym ACK wykonano pełny rollback: przywrócono 30 nadpisanych plików, usunięto
sześć nowych artefaktów Sprintu i zrestartowano dwa procesy, aby załadowały kod
sprzed wdrożenia. Nie wykonano flipa, zmiany danych runtime, commita ani pushu.

Polecenie restartu dwóch usług zostało rozpoczęte na podstawie wcześniejszego
`ack` oraz osobnej zgody narzędziowej. Nowa instrukcja zabraniająca restartu
nadeszła, gdy polecenie było już w toku. Oczekujące wywołanie przerwano, ale
read-only `systemctl show` potwierdził, że oba restarty zdążyły się zakończyć:

- `dispatch-shadow.service`: active/running, PID `2572594`, start
  `2026-07-09 21:50:05 UTC`;
- `dispatch-panel-watcher.service`: active/running, PID `2572894`, start
  `2026-07-09 21:50:22 UTC`;
- `NRestarts=0` dla obu oznacza brak automatycznego restart-loopa;
- timery plan-recheck, czasówka, bundle-calib-shadow i b-route-shadow pozostały
  active/waiting.

Po osobnym ACK rollbackowym wykonano drugi, kontrolowany restart już po
przywróceniu kodu. Stan końcowy:

- `dispatch-shadow.service`: active/running, PID `2712426`, start
  `2026-07-10 00:50:33 UTC`;
- `dispatch-panel-watcher.service`: active/running, PID `2712498`, start
  `2026-07-10 00:50:42 UTC`;
- `NRestarts=0` dla obu;
- timery plan-recheck, czasówka, bundle-calib-shadow i b-route-shadow:
  active/waiting.

Globalne `systemctl --failed` wskazało `ssh.socket` jako failed. Sprint, deploy i
rollback nie wykonywały żadnej operacji na tej jednostce.

## Weryfikacja deployu

- Dry-run rsync przed wdrożeniem wskazał wyłącznie pliki Sprintu 1.
- SHA-256 worktree kontra live: **36/36 zgodnych, 0 mismatch**.
- `git diff --check` live: OK.
- AST parse najważniejszych 10 modułów live: OK.
- HEAD repo pozostał `9ab45925b447eeaccccd5bb984a05a7c4f60e011`;
  nie powstał commit.
- Nie kopiowano `daily_accounting/kurier_full_names.json` ani
  `ZIOMEK_BACKLOG.md`.
- Pełna suita przed deployem: **4579 passed, 0 failed**.
- Replay 50 WR1: 0 różnic krytycznych; wynik identyczny z niezmienionym HEAD.

## Stan repo live po syncu

Tracked zmiany Sprintu są widoczne jako `M`; sześć nowych artefaktów Sprintu jako
`??`. Poza nimi pozostały dokładnie dwie zastane pozycje użytkownika:

```text
 M daily_accounting/kurier_full_names.json
?? ZIOMEK_BACKLOG.md
```

Chronionego JSON-a nie otwierano bezpośrednio, nie kopiowano, nie edytowano i nie
przywracano; obejmowały go jedynie repozytoryjne kontrole read-only (`git status`
oraz `git diff --check`).

## Artefakt rollbacku

Kopia 30 nadpisanych, wcześniej śledzonych plików znajduje się w:

```text
/root/sprint1_rollback_20260709_2140
```

Kopia zachowuje ścieżki względne i tryby plików. Nie zawiera chronionego JSON-a,
plików runtime ani sekretów dodanych przez Sprint.

Nowe pliki usunięte osobno podczas pełnego rollbacku:

```text
core/invariant_firewall.py
eod_drafts/2026-07-09/SPRINT1_FUNDAMENT_SPOJNOSCI_RAPORT.md
tests/test_geocode_cache_concurrency_zp002.py
tests/test_invariant_firewall.py
tests/test_invariant_firewall_wiring.py
tests/test_plan_cas_stale_write.py
```

## Procedura rollbacku — wykonana

Jawny ACK rollbackowy otrzymano. Wykonano kolejno:

1. Przywrócono 30 plików z `/root/sprint1_rollback_20260709_2140` do live z
   zachowaniem ścieżek i trybów.
2. Usunięto sześć nowych plików wymienionych wyżej. Nie usuwano innych plików
   ani katalogów.
3. `git diff --check` zakończył się kodem 0 bez komunikatów.
4. Status repo po pełnym rollbacku zawiera dokładnie dwie zastane pozycje:

   ```text
    M daily_accounting/kurier_full_names.json
   ?? ZIOMEK_BACKLOG.md
   ```

5. Po osobnym ACK zrestartowano `dispatch-shadow` i
   `dispatch-panel-watcher`; kontrola read-only potwierdziła ActiveState,
   PID-y, timestampy startu i brak restart-loopa podane wyżej.

Rollback nie wymaga cofania flag ani danych. Lockfile cache jest bezstanowy;
pliki planów/cache zachowują kompatybilny format. Nie należy przywracać ani
modyfikować żadnego pliku runtime.

## Ponowny deploy po ACK — wykonany 2026-07-10

Po jednoznacznym `deploy i restart ack` ponownie wdrożono tę samą listę 36
plików Sprintu z izolowanego worktree do live repo.

- SHA-256 źródło kontra live: **36/36 zgodnych, 0 mismatch**.
- Status live: dokładnie 36 pozycji Sprintu i dwie zastane pozycje użytkownika;
  brak plików nieoczekiwanych.
- `git diff --check`: OK.
- AST modułów produkcyjnych: **11/11 OK**.
- `dispatch-shadow.service`: active/running, PID `2959287`, start
  `2026-07-10 06:10:24 UTC`, `NRestarts=0`.
- `dispatch-panel-watcher.service`: active/running, PID `2959451`, start
  `2026-07-10 06:10:36 UTC`, `NRestarts=0`.
- Timery plan-recheck, czasówka, bundle-calib-shadow i b-route-shadow:
  active/waiting.
- W ramach samego deployu nie wykonano flipa flag ani zmiany danych runtime;
  repozytoryjny commit/push stanowi osobny, później autoryzowany handoff.

Globalna lista failed units zawierała `dispatch-night-guard.service`, którego
ostatnie wykonanie trwało od `01:15:02` do `01:17:34 UTC`, a więc zakończyło się
około pięciu godzin przed ponownym deployem, oraz `ssh.socket`. Nie wykonywano
operacji na żadnej z tych dwóch jednostek.

## Granica dalszych działań

Najnowszy stan live zawiera ponownie wdrożony Sprint 1. Użytkownik osobno
autoryzował aktualizację dokumentacji oraz selektywny commit/push na `master`;
handoff obejmuje kod i testy Sprintu, `ZIOMEK_BACKLOG.md` oraz oba raporty, ale
wyklucza `daily_accounting/kurier_full_names.json`. Implementacja pozostaje też
w izolowanym worktree, a kopia rollbackowa jest zachowana.

Okno obserwacji shadow trwa od `2026-07-10 06:10:36 UTC` do najwcześniej
`2026-07-12 06:10:36 UTC`. Dalszy deploy, restart, flip lub zmiana danych runtime
wymagają nowego, jednoznacznego ACK; `enforcement=NONE`, a B-01/B-02 pozostają
otwarte.
