# ADR-009: Kanoniczna lokalizacja i tworzenie worktree

Status: obowiązuje od 2026-07-25 (decyzja właściciela)

Supersedes: ADR-007 wyłącznie w zakresie lokalizacji i sposobu tworzenia worktree.

## Kontekst
Audyt dysku 2026-07-24/25 wykazał 114 worktree `dispatch_v2` i 95 katalogów roboczych
bezpośrednio w `/root`. W ciągu 5 dni przybyło 27 worktree i żadne nie przeszło
przez rejestr. Nakazane w ADR-007 `git worktree add ../wt-<lane>` uruchomione z
`dispatch_v2` umieszcza kopię w kanonicznym drzewie `scripts/`; audytowe
`find scripts -name common.py` zwracało przez to 5 wyników.

## Decyzja
Nowe worktree powstają wyłącznie w
`/root/worktrees/<repo>/active/<data>-<zadanie>-<właściciel>/` i wyłącznie przez
`/root/projects/bin/new-worktree`. Zakazane są: `git worktree add ../wt-<lane>`,
katalogi robocze bezpośrednio w `/root` oraz jakakolwiek praca w `/tmp`.
`/usr/lib/tmpfiles.d/tmp.conf` zawiera `D /tmp … 30d`, więc zawartość `/tmp` jest
kasowana przy restarcie i po okresie nieużywania; 24.07 było tam 86 repozytoriów
git, w tym 65 z niezacommitowanymi zmianami.

Pozostała część ADR-007 obowiązuje bez zmian: atomowy commit po jawnych ścieżkach,
zakaz `git add -A`, seryjna praca nad rdzeniem silnika i deploy wyłącznie za ACK.

## Konsekwencje
- Jedna komenda tworzy worktree w rejestrowanej lokalizacji i buduje poprawny
  pkgroot z rodzeństwem `flags.json` oraz `logs`.
- `ZIOMEK_SCRIPTS_ROOT` musi wskazywać ten pkgroot. Testy uruchomione w worktree
  bez pkgroot po cichu importują żywe repo, więc mogą dać fałszywie zieloną bramkę.
- Istniejące worktree i gałęzie pozostają nietknięte; ich retencję nadal reguluje
  `/root/projects/LIFECYCLE.md`.

## Źródła
`/root/handover/AUDYT_ARCHITEKTURY_DYSKU_2026-07-24.md`;
`/root/projects/LIFECYCLE.md`; decyzja właściciela „Tak, zmień” z 2026-07-25.
