# A1-SECURITY-PREP (Sprint 1 Z4, tmux 17) — 2026-07-05. NIC nie wykonane — dokumenty decyzyjne + kod staged.

## (a) Auth na `/stop` :8765 — STAGED, z rekomendacją WYGASZENIA zamiast łatania
**Stan faktyczny (zbieżny: runbook B2 + Sprint 0 tmux15 diagnoza 05.07):** `/stop`/`/start` żyje w legacy
`/root/gps_server.py` (PID 1010, @reboot crontab), proces **ZAWIESZONY** (accept-queue pełna, zero GPS od 10.06)
— endpoint dziś de facto nieosiągalny; kanoniczny `dispatch-gps` (:8766) nie ma `/stop`.
**Rekomendacja (za runbookiem „krok właściwy" = P5):** NIE inwestować w auth dla zawieszonego procesu —
**wygasić legacy** (2 linie @reboot z crontab + kill PID 1010/1006) wg planu tmux15
(`SPRINT0_ZAD3_codweekly_gpslegacy_raport.md`), po odpowiedzi Adriana czy zdalny „gastro stop" przez TG potrzebny.
**Jeśli Adrian zdecyduje ZOSTAJE** → staged patch (NIE zaaplikowany): `dispatch_state/gps_server_stop_auth.patch` —
token z `.secrets/gps_legacy.env` (`GPS_LEGACY_CONTROL_TOKEN`), `/stop|/start` wymaga `?auth=<token>`
(porównanie `hmac.compare_digest`), brak/zły token → 403 bez treści; brak env → 503 fail-closed (nie fail-open).
Aplikacja = `patch -p0 < ...` + wpis tokenu do .secrets + restart legacy — WYŁĄCZNIE za ACK, po FW kroku 0.

## (b) Czyszczenie tokenów z HISTORII git — JEDNOSTRONICOWY plan decyzyjny dla Adriana
**Problem:** tokeny TG (+ inne sekrety) żyją w HISTORII repo `ziomek-dispatch-` na GitHubie (prywatne, ale:
konto = 1 hasło od wycieku; rotacja C1 unieważnia stare tokeny, ale historia zdradza WZORZEC nazw/plików).
**Opcje:**
1. **NIC (po rotacji C1)** — stare tokeny martwe; koszt 0; zostaje ryzyko wzorca + tokeny jeszcze-nie-zrotowane.
2. **BFG Repo-Cleaner** (rekomendacja): `bfg --replace-text sekrety.txt` na świeżym `--mirror` klonie → force-push.
3. `git filter-repo` — precyzyjniejszy, wolniejszy w przygotowaniu.
**Sekwencja BFG (gdy ACK):** (0) NAJPIERW rotacja C1 (żeby historia czyściła JUŻ MARTWE sekrety); (1) zamrożenie
pushy wszystkich sesji (koordynacja tmux — okno ~30 min); (2) `git clone --mirror` + backup mirroru na BX11;
(3) plik `sekrety.txt` (tokeny z api_keys_inventory + grep `bot[0-9]*:` historii); (4) BFG + `git reflog expire
--expire=now --all && git gc --prune=now --aggressive`; (5) `git push --force` mirror; (6) **KAŻDA z ~6 sesji CC
i workterr'ów**: `git fetch && git reset --hard origin/master` (lokalne branche/worktree rebase na nową historię —
inaczej stary SHA wraca pushem!); (7) weryfikacja: `git log -S<token> --all` = 0 trafień.
**Wpływ na sesje/worktree:** wszystkie SHA po pierwszym dotkniętym commicie się ZMIENIĄ → otwarte branche
(l5-eta-load-aware itp.) wymagają rebase/cherry-pick; tagi historyczne re-tag. Dlatego okno = moment bez otwartych
prac (koniec sprintu). **Rollback:** mirror-backup z kroku 2 (push starej historii z powrotem).
**Decyzja Adriana:** opcja + termin okna. Koszt opcji 2: ~40 min + koordynacja.

## todo_master #8b — wpis dopisany (staged auth + plan BFG gotowe; wykonanie bramkowane Adrianem/FW krok 0).
