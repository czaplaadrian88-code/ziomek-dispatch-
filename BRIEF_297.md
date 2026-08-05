# BRIEF (sesja 297, CTO Fable) — AUTOMAT ARCHIWIZACJI OD-7 (retencja, shadow-first)

## KIM JESTEŚ / GDZIE PRACUJESZ
Agent-budowniczy. Worktree: `/root/worktrees/dispatch_v2/active/20260805-od7-archiver-297-cto`
(gałąź `wt/od7-archiver-297-cto-20260805`, base master `06e4d5c39`). Pracujesz WYŁĄCZNIE w tym worktree.
⛔ NIE wolno: pisać/kasować/zmieniać CZEGOKOLWIEK w `/root/.openclaw/workspace/dispatch_state/`,
`/root/.openclaw/workspace/scripts/logs/`, live `flags.json`; dotykać systemd/timerów; robić merge.
Merge + podpięcie timera + 1. bieg apply = CTO/owner, nie ty.

## KONTEKST (dlaczego to ważne)
Retencja (logrotate 100M) bezpowrotnie zjada dane uczenia (pool_feasible sprzed 23.07 stracone na zawsze).
Owner 03.08 ZATWIERDZIŁ politykę retencji OD-7 (RODO art. 5). Automat NIE istnieje — w ledgerze jest tylko
prune (`state.retention-prune-z04`). Ten automat ma ZATRZYMAĆ dalszą utratę danych: zamiast „rotate i strata"
ma być „archiwizuj wg polityki, kasuj dopiero po terminie archiwum".

## POLITYKA OD-7 (zatwierdzona przez ownera, NIE zmieniaj liczb)
- GPS: 3 miesiące żywe → 9 miesięcy archiwum → kasacja.
- world_record + logi decyzji (m.in. shadow_decisions): 1 miesiąc żywe → archiwum 12 miesięcy.
- events.db: 6 miesięcy żywe → archiwum bezterminowo.
- Maskowanie adresów/PII w logach po 3 miesiącach.

## ZADANIE
Zbuduj `tools/retention_archiver.py` + testy:
1. **Konfiguracja deklaratywna** (plik/reguły w kodzie z jednym źródłem polityki): wzorzec ścieżki → klasa
   retencji (żywe / archiwum / kasacja / maskowanie PII), wprost odwzorowująca politykę OD-7 wyżej.
   ZMAPUJ realne pliki: przejrzyj (READ-ONLY!) `/root/.openclaw/workspace/dispatch_state/`,
   `/root/.openclaw/workspace/scripts/logs/` i ustal, które artefakty należą do której klasy (GPS history,
   world_record, shadow_decisions, learning_log, events.db, courier_match_debug itd.). Niepewne = klasa
   `UNKNOWN` raportowana, NIGDY nie ruszana.
2. **Tryb domyślny = REPORT (shadow-first, twardy wymóg ownera):** narzędzie liczy i wypisuje CO by
   zarchiwizowało / skasowało / zamaskowało (plik, rozmiar, wiek, klasa, powód), zapisując raport WYŁĄCZNIE
   pod jawnie podane `--out <ścieżka>`. Tryb apply istnieje w kodzie, ale wymaga `--apply` + `--ack-token <token>`
   i NIE MA go jak uruchomić przypadkiem (bez tokenu = twardy exit z komunikatem, że 1. bieg wymaga ACK ownera).
3. **Archiwum:** katalog docelowy parametrem (`--archive-root`), format skompresowany (gzip/zstd), struktura
   `<archive-root>/<klasa>/<YYYY-MM>/...`, manifest jsonl (sha256 przed/po, rozmiary, ts) — archiwizacja musi być
   weryfikowalna i odwracalna do terminu kasacji. ⚠ Dysk jest na 93% — w raporcie policz bilans miejsca
   (ile zwolni żywe minus ile zajmie archiwum po kompresji); zaproponuj `--archive-root` w raporcie, nie twórz go.
4. **Maskowanie PII:** dla logów >3 mies. zaprojektuj przepis maskowania (adresy/nazwiska klientów) — w tej
   iteracji wystarczy DETEKCJA + raport pól do maskowania i implementacja maskera z testami na syntetykach;
   apply i tak gated.
5. **Bezpieczeństwo:** atomic writes (temp → fsync → rename), nigdy edycja żywego pliku w miejscu; pliki otwarte
   przez procesy (lsof) = skip z raportem; wszystko fail-closed (błąd = stop, nie kontynuacja po cichu).
6. **Testy hermetyczne** (tmp_path, syntetyczne drzewa plików o sfabrykowanych mtime): klasyfikacja, progi wieku,
   report vs apply gating, manifest+sha, maskowanie, fail-closed. `RuntimeError "HERMETIC-GUARD"` = napraw TEST.
7. **Jeden realny bieg REPORT** (bez apply) na żywych ścieżkach READ-ONLY z `--out` do worktree — to jest
   raport dla ownera do ACK 1. biegu. ⚠ Uruchamiasz gołym pythonem poza pytestem: upewnij się, że narzędzie
   w trybie report NIE MA żadnej ścieżki zapisu poza `--out` (przeczytaj kod przed biegiem, to twój dowód w raporcie).

## REGRESJA
`cd /root/worktrees/dispatch_v2/pkgroot/20260805-od7-archiver-297-cto/dispatch_v2 &&
/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q` — baseline PRZED, delta = tylko twoje testy, 0 failed.

## DELIVERABLES (NA DYSK, PRZED KOŃCEM — obowiązkowo)
1. Commit(y) na gałęzi worktree (jawny pathspec). 2. `RAPORT_AGENTA.md` w worktree: architektura, mapowanie
plików→klasy (z UNKNOWN), wynik biegu REPORT na żywych danych (ile GB do archiwum/kasacji/maskowania, bilans
miejsca), liczby testów, pełny SHA, otwarte pytania do ownera. 3. Raport REPORT-biegu (`--out`) w worktree.
