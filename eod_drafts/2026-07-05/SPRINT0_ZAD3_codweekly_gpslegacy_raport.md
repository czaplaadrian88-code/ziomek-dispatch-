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

## (b) Duplikat GPS legacy @reboot — PLAN WYGASZENIA (wykonanie ZA ACK)

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
