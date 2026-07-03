# ADR-007: Multi-sesja na wspólnym repo — worktree + atomowy commit + deploy seryjny

Status: obowiązuje (C1/C1-git od 2026-06-30, C12 od 2026-07-02; potwierdzone realnymi near-missami)

## Kontekst
Na wspólnym repo+deploy biega równolegle KILKA sesji CC (zweryfikowane: ~6 procesów `claude`). Working-tree, `.git/index`, pliki `.bak`, flagi i serwisy są WSPÓLNE — kolizja jest domyślna (last-writer-wins). Near-missy: 6 plików AUTON-02 zgarnięte do cudzego commita przez wyprzedzający `git add` na wspólnym indeksie (C1-git 30.06); restart `nadajesz-panel` złapał cudzy `feed.py` w połowie zapisu → ~3 min feed 500 (27.06); `git build`/rsync na wspólny target nadpisuje cudzy żywy deploy.

## Decyzja
Równoległość TYLKO na rozłącznych plikach + izolacja worktree per sesja mutująca (`git worktree add ../wt-<lane>`, C12b — osobny indeks, zero wyścigu). `git add`+commit ATOMOWO po jawnych ścieżkach (C1-git) — NIGDY `git add -A`/`git add .` ani `git add` jako osobny wcześniejszy krok. Rdzeń silnika (`feasibility_v2`/`dispatch_pipeline`/`plan_recheck`/`courier_resolver`/`route_simulator_v2`/`scoring`) = jeden właściciel/fala, SERYJNIE (nie da się równoleglić). Deploy (merge/flip/restart) = seryjny, jeden na raz, ZA ACK. NIGDY nie cofać cudzego live.

## Konsekwencje
- Wolno: równolegle KOD+TESTY+REPLAY na rozłącznych plikach poza rdzeniem (read-only agenci bez worktree).
- Nie wolno: równolegle flip/restart/Telegram/deploy w peak (C2); `git add -A`; rewertować cudzy świeży deploy; przenosić rdzeń między lane'ami.
- Trzeba PRZED edycją: `tmux ls` + cudze świeże `.bak-*`/working-tree (C1 recon). PO commicie: `git show HEAD --stat` (czy Twoje pliki, czy nie zgarnięte). PRZED restartem współdzielonego backendu: sprawdź KAŻDY cudzy `M *.py` (`py_compile` + mtime stabilne ≥ kilka min).
- Testy agenta: samo-lokalizacja `Path(__file__).parents[1]`, NIGDY hardcode ścieżki worktree (po `worktree remove` martwa ścieżka wywala kolekcję regresji).

## Źródła
`memory/ziomek-change-protocol.md` C1/C1-git/C12 (a-f); `memory/feedback_multisession_shared_deploy.md` (7 kroków + near-missy 06-07/06-27); `docs/audyt/01-ZALEZNOSCI.md` ⚠#3 (repo mutuje na żywo).
