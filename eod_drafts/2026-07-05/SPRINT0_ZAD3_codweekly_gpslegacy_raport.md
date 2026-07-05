# SPRINT 0 / Zadanie 3 (A0-OPS) — cod-weekly FAILED + duplikat GPS legacy (sesja tmux15, 05.07 wieczór)

## (a) dispatch-cod-weekly FAILED — STAN: FIX JUŻ W MASTERZE, jutrzejszy run bierze naprawiony kod

**Root-cause (nazwany, potwierdzony żywym stanem + raportem FALA1 z 02.07):** serwis pada, bo w arkuszu
'Wynagrodzenia Gastro' **brakuje ręcznie dodawanego bloku tygodnia** (payday + zakres) → `find_target`
candidates=[] → `NoTargetColumnError` → exit 1. Druga ścieżka (rzadsza): blok jest, ale kolumna już
wypełniona → empty_check FAIL (to zabezpieczenie przed dublem, nie utrata). Ostatni fail żywy:
**29.06 06:00:03 UTC** (nie 02.07 — stan `failed` na unicie to pozostałość tego runa; timer AKTYWNY,
następny trigger **pn 06.07 06:00 UTC**).

**Patch staged + test: JUŻ ZROBIONE I SCALONE.** Branch `fix/cod-weekly-diag` (lane FALA1 02.07,
commity `0346a9a` + `7afc431` + raport `6253fef`) **jest w masterze** (zweryfikowane
`git merge-base --is-ancestor`). Testy `tests/test_cod_weekly_missing_block.py` **8/8 PASS na
kanonicznej ścieżce** (sprawdzone dziś, dispatch-venv). ExecStart = `sheets-venv -m
dispatch_v2.cod_weekly.run_weekly --write` z `WorkingDirectory=/root/.openclaw/workspace/scripts`
(oneshot, świeży proces) → **poniedziałkowy run 06.07 06:00 UTC automatycznie wykonuje naprawiony kod
— żaden restart/merge nie jest potrzebny.**

**Co zmienia jutrzejszy run:** przy dalszym braku bloku → exit 1 jak dotąd (OnFailure działa), ale alert
jest AKTIONABLE (instrukcja dla Rafała: arkusz, komórki payday+zakres, nagłówki row2, komenda
backfillu). Auto-create bloku istnieje w kodzie, ale **za flagą `COD_WEEKLY_AUTOCREATE_BLOCK` default
OFF** — zero auto-zapisu do arkusza bez ACK. Preflight (`dispatch-cod-weekly-preflight.timer`) odpala
dziś 21:00 UTC i sprawdzi arkusz przed runem.

**Sekwencja deploy-za-ACK (decyzje Adriana):**
1. **NIC nie trzeba robić przed jutrem** — fix wjedzie sam o 06:00 UTC. (Opcjonalny kosmetyk:
   `systemctl reset-failed dispatch-cod-weekly.service` — czyści czerwony stan, zero wpływu na timer.)
2. **Flip auto-create (osobny ACK, off-peak):** drop-in
   `/etc/systemd/system/dispatch-cod-weekly.service.d/autocreate.conf` z
   `Environment=COD_WEEKLY_AUTOCREATE_BLOCK=1` (+najpierw `..._DRY_RUN=1` na podgląd) → `daemon-reload`.
   Procedura i limitacje: `eod_drafts/2026-07-02/FALA1_codweekly_raport.md` §4B.
3. **Backfill 4 przepadłych tygodni (18-24.05, 01-07.06, 08-14.06, 22-28.06) = OSOBNA decyzja
   Adriana — NIE ruszane** (pieniądze; 15-21.06 W OGÓLE nie ruszać — było już wypełnione).
   Gotowa procedura: FALA1 §4C (`--week YYYY-MM-DD:YYYY-MM-DD --write`, chronologicznie).

**Rollback:** flaga OFF (default) = brak zmiany zachowania zapisu; pełny revert `7afc431 0346a9a`
możliwy w każdej chwili (auto-create martwy przy OFF).

### ✅ FLIP AUTO-CREATE WYKONANY 05.07 ~18:15 UTC (ACK Adriana, najpierw DRY_RUN)
- **DRY_RUN zweryfikowany na 3 tygodniach:** (a) 22-28.06 i 01-07.06 — bloki JUŻ ISTNIEJĄ (CZ, CN)
  i są WYPEŁNIONE → empty-check poprawnie zablokował (exit 1, nic nie ruszone); (b) tydzień przyszły
  06-12.07 (payday 15-07, bez bloku) → auto-create DRY-RUN pokazał DOKŁADNIE poprawny blok:
  `DH @ DH1:DK2, wypłata=15-07-2026, zakres=06-12.07.2026, row2=[COD - Transport, Korekty, Wypłata,
  Saldo do przen.]`, nic nie zapisał, exit 1. Mechanika potwierdzona.
- **Drop-in zainstalowany:** `/etc/systemd/system/dispatch-cod-weekly.service.d/autocreate.conf`
  (`COD_WEEKLY_AUTOCREATE_BLOCK=1`, BEZ DRY_RUN) + `daemon-reload`; env efektywny potwierdzony
  `systemctl show`. Rollback: usunąć plik / `=0` + daemon-reload (oneshot bierze świeży env per run).
- **⭐ BACKFILL BEZPRZEDMIOTOWY:** wszystkie 4 „przepadłe" tygodnie mają już bloki **I SĄ WYPEŁNIONE**
  (zweryfikowane read-only: CF=18-24.05 56 wart., CN=01-07.06 64/65, CR=08-14.06 56 wart.,
  CZ=22-28.06 64/65) — arkusz nadrobiony ręcznie po 02.07. Lista z FALA1 nieaktualna. Ewentualna
  weryfikacja sum vs DB = opcjonalna decyzja Adriana; ZERO zapisów potrzebnych.
- **⚠ JUTRZEJSZY RUN 06:00 UTC (tydzień 29.06-05.07, split-month, payday 08-07-2026):** w arkuszu
  jest 1 z 2 bloków — `DD` pokrywa segment **29-30.06**; **BRAKUJE segmentu 01-05.07**. To ścieżka
  `AmbiguousTargetError`, której auto-create ŚWIADOMIE nie dotyka (ryzyko dubla przy częściowym
  bloku) → jeśli Rafał nie doda drugiego bloku do 06:00, run = exit 1 + aktionable alert (bez straty
  — dopisze się później przez `--week 2026-06-29:2026-07-05 --write`). Preflight dziś 21:00 UTC
  i last-call 05:00 UTC przypomną. Auto-create obsłuży od teraz tygodnie CAŁKIEM bez bloku
  (jak 06-12.07 w następny poniedziałek 13.07).

## (b) Duplikat GPS legacy @reboot — ✅ WYGASZONY 05.07 ~18:00 UTC (ACK Adriana)

**Wykonanie:** backup crontaba → `/root/crontab.bak-pre-gps-legacy-retire-20260705` (55 linii);
usunięte 2 linie `@reboot` (gps_server.py + dispatch_control.py; crontab 55→53, `fix_approvals.sh`
i GC zostały); `kill 1010 1006` (SIGTERM wystarczył mimo zawieszenia). **Weryfikacja:** procesy nie
istnieją; porty :8765/:8443 WOLNE; `dispatch-gps`/`courier-api`/`dispatch-shadow`/`dispatch-panel-watcher`
= active, nowy gps_server żywy na :8766, journal dispatch-gps 0 błędów; skrypty ZOSTAŁY na dysku
(zero kasowania). **Rollback:** przywrócić backup crontaba (`crontab /root/crontab.bak-...`) +
ręczny start procesów. Hardcoded token control-bota (finding L12) przestał być procesem nasłuchującym;
rewokacja tokena w BotFather = osobna pozycja rotacji sekretów (C1 w runbooku security).

## (b-ARCHIWUM) Plan wygaszenia sprzed wykonania

**Co to jest (zdiagnozowane):** dwa procesy z crontaba `@reboot` (żyją od bootu 27.05):
- **PID 1010 `/root/gps_server.py`** — legacy ingest GPS po PIN (HTTP :8765) → pisze
  `dispatch_state/gps_positions.json`. **Ostatni realny GPS: 10.06 12:03** (`/tmp/gps_server.log`) —
  25 dni zero ruchu (kurierzy są na apce → nowy `dispatch_v2.gps_server` systemd na :8766, pisze
  OSOBNY `gps_positions_pwa.json`). Proces **ZAWIESZONY** (accept-queue pełna: Recv-Q 6/backlog 5).
- **PID 1006 `/root/dispatch_control.py`** — stary kontroler Telegram stop/start (webhook :8443,
  hardcoded token — flagowany też przez audyt L12-bezpieczeństwo). **Webhook Telegrama = PUSTY**
  (`getWebhookInfo url=''`) → proces nie dostaje ŻADNYCH update'ów; log martwy od 28.05
  (JSONDecodeError traceback); accept-queue też pełna (zawieszony). Token używa tylko ten plik.

**Konsumenci — bezpieczeństwo wygaszenia:**
- `gps_positions.json` czyta tylko `courier_resolver._load_gps_positions()` jako **fallback** merge'a
  (primary = PWA); pozycje >60 min i tak są ignorowane, plik od 3+ tygodni nie wnosi nic żywego.
  `tools/gps_positions_gc.py` (cron 04:50) ma tę ścieżkę na liście GC — nieszkodliwe, może zostać.
- Flaga `/tmp/gastro_stop` (stop dispatchu) jest czytana przez `gastro_koordynator.py` — mechanizm
  NIEZALEŻNY od control-bota (flagę można stawiać ręcznie); wygaszenie bota nic tu nie psuje.
- Nginx: ZERO route'ów do :8765/:8766 w sites-enabled.

**Sekwencja wygaszenia (za ACK, poza peakiem, NIE koliduje z at-205/206/208):**
1. `crontab -e`: usunąć 2 linie `@reboot` (`gps_server.py` i `dispatch_control.py`); zostawić
   `fix_approvals.sh` i GC.
2. `kill 1010 1006` (SIGTERM wystarczy; procesy i tak zawieszone).
3. Weryfikacja: `ss -tlnp | grep -E '8765|8443'` puste; `systemctl status dispatch-gps` /
   courier-api bez zmian; skrypty ZOSTAJĄ na dysku (zero kasowania).
4. Rollback: przywrócić linie w crontab + `nohup python3 /root/gps_server.py ...` ręcznie.

**⚠ Do decyzji Adriana przy ACK:** czy zdalne „gastro stop/start" przez Telegram jest jeszcze
potrzebne? Jeśli tak — jest NIECZYNNE co najmniej od 28.05 (pusty webhook + zawieszony proces) i
wymaga osobnego sprintu (nowy mechanizm, bez hardcoded tokena), bo wygaszenie tylko formalizuje
istniejący stan martwy.
