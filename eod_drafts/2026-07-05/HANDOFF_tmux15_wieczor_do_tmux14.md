# HANDOFF sesja tmux15 (wieczór 05.07) → tmux14

> Od: tmux15 (Sprint 0 „Prawda i bramki" + tripwire Q1 + security-P0 sekcja C/host-FW).
> Zakres rozłączny z tmux17 (Sprint 1 serializer/inwarianty/L5) i tmux18 (perf/flag-GC) — te sesje mają własne handoffy.
> **Wszystko poniżej WYKONANE i zweryfikowane. Nic nie zostaje w połowie.** Master **+9 ahead origin — NIE pushowane (push za Adrianem).**

## 1. SPRINT 0 — całość ✅ (4 zadania)

**Zad.1 A0-GEOFENCE (GPS-5b adopcja):** courier-api zrestartowany 16:40 UTC (czysto, HEAD=merge 5b `e5b3dc0`), endpoint `POST /arrival` żywy (401 auth). **Adopcja RUSZYŁA 17:19:22 UTC** — 1. rekord `gps_arrived_at` (zlec. 485726, cid=492, app_geofence; pickup 17:11→arrived 17:19→delivered 17:23, kontrakt 5a nietknięty). vc61 z tmux16 zawiera 5b (brak kolizji). **Werdykt POKRYCIA ~07-08.07 liczyć OD 17:19 UTC 05.07** (nie od restartu). Licznik dziś wieczór: **1/550** (tylko cid=492 na vc60+; rośnie z adopcją apek). Odblokowuje flip O2 + pomiar feas_carry #483000. Memory [[gps5b-delivery-geofence-2026-07-05]].

**Zad.2 A0-ROUTEORDER (golden + następca monitora):** commit `5d24bc9`. Monitor `ziomek_time_route_monitor` wygasa SAM 10.07 — **NIE przedłużać**. Zbudowane: korpus golden 13→**25 case'ów** (klasy czasówka/paczka/carried/mix + 9 żywych worków, parytet 25/25), **3. noga SILNIK==GOLDEN** (pełny parytet silnik==konsola==apka) + tripwire strukturalny C9. Naprawione **2 bugi harnessu z 01.07** (klasa #17): klucz planu `sequence`→`stops` (case trust_canon testował fallback) + ck „HH:MM"→None (sort committed/sklejanie martwe w syntetykach). **Następca:** `tools/route_order_live_parity_check.py` + `tests/test_route_order_live_parity.py` **AKTYWOWANY default ON** (`d729603`, opt-out `ENABLE_ROUTE_ORDER_LIVE_PARITY=0`) — każda regresja weryfikuje żywy parytet + pin flag. ⚠ Korekta: rozjazdy trasy = **0/dzień od 29.06** (handoffowe „44-75/d" = stan 26-28.06 sprzed fixów). Memory [[route-order-golden-l6a-2026-07-01]]. Raport `SPRINT0_ZAD2_routeorder_golden_raport.md`.

**Zad.3 A0-OPS:** (a) **cod-weekly** — fix FALA1 był już w masterze; **flip auto-create WYKONANY za ACK** (drop-in `autocreate.conf`, DRY_RUN zweryfikowany na 3 tygodniach). **⭐ BACKFILL BEZPRZEDMIOTOWY** — 4 „przepadłe" tygodnie (CF/CN/CR/CZ) już wypełnione ręcznie (read-only verify). **⚠ JUTRO 06:00 UTC:** tydzień 29.06-05.07 = split-month, w arkuszu jest tylko segment 29-30.06 (kol. DD), BRAKUJE 01-05.07 → ścieżka Ambiguous (auto-create świadomie nie dotyka) → **potrzebny Rafał** albo exit1+aktionable (bez straty; dopisywalne `--week 2026-06-29:2026-07-05 --write`). (b) **GPS legacy WYGASZONY** — PID 1010 (`gps_server.py` :8765) + 1006 (`dispatch_control.py` :8443) ubite, 2 linie `@reboot` usunięte (backup `/root/crontab.bak-pre-gps-legacy-retire-20260705`). Raport `SPRINT0_ZAD3_codweekly_gpslegacy_raport.md`.

**Zad.4 A0-DOCS:** zrobione przez sesję konsolidacyjną (nie ruszane).

## 2. TRIPWIRE Q1 ✅ (za ACK) — `80b39cf`
Następca pionu Q1 wygasającego monitora: sygnał 6 `q1_missing_time` w `observability/data_alerts.py` (zlecenie assigned ≥10 min dwell i wciąż bez czasu odbioru → edge-trigger, log-only, Telegram OFF). Delty vs monitor świadome (dwell / tylko assigned / excl. cid=26 Koordynator). Wchodzi z tickiem 5-min bez restartu; potwierdzony żywy tick 18:35 (q1=0/15). 8 testów, plik 34/34. Monitor route-order może wygasnąć 10.07 **bez żadnej niezastąpionej klasy** (Q2 kryją R-DECLARED tripwire + frozen-pickup, oba ON).

## 3. SECURITY-P0 — sekcja C + host-firewall (wszystko za ACK)
- **C1 token @Nadajesz2Bot ✅** (`031afe1`+`58108e3`): Adrian zrewokował (stary token getMe=401), nowy w `.secrets/telegram_bots.env` (600), oba pliki na env-load, **0 hardcoded tokenów**. ⚠ prawdziwa nazwa bota = **@Nadajesz2Bot** („GastroBot"), NIE „NadajeszControlBot"; token gastro_koordynatora był już martwy. Repa PRIVATE potwierdzone → historia gita bez rewrite.
- **C2 hasło fleet-admina ✅** (`bf81d16`, off-peak): nowe silne hasło (26 alnum) w drop-inie + kopia `.secrets/courier_admin_fleet.txt` (600). Restart courier-api 19:02 czysty, sesje kurierów przeżyły, stare hasło→401. **B4b:** wyciek zredagowany z `courier_api.log`.
- **C3 klucz GMaps ✅** (`c373cd0`): Adrian ograniczył w Console (IP v4+v6 + API=Distance Matrix+Geocoding); CC zweryfikował z serwera Geocoding=OK, DistanceMatrix=OK. Klucz Papu `...OKiGmE` = osobny (follow-up przy Lokalce).
- **KROK 0 + SEKCJA-B host-iptables MOST ✅** (`b15e238`+`2ef98b6`): probe z zewnątrz potwierdził P0 (porty realnie publiczne). Zamknięte z internetu v4+v6: **631/3001/18789/5001/9222** (5001 przez `raw/PREROUTING` bo docker DNAT :5001→:5000; B1 skonsolidowany, dostał wreszcie @reboot). Nietknięte: 22/443/**8767** (apka przez nginx). Trwałość `dispatch_state/host_fw_drop_reboot.sh`+@reboot. **To MOST** — Sekcja A (Hetzner Cloud FW) go zastąpi.

## 4. ZOSTAJE ADRIANOWI (nie-CC, poza serwerem)
- **Hetzner Cloud Firewall (Sekcja A)** = docelowe, zastąpi host-most (broni przed dotarciem do maszyny). Przy okazji: **8766** (stary gps_server publiczny) — najpierw potwierdź czy PWA nie bije wprost, potem obejmij FW. SSH 22 opcjonalnie tylko z IP Adriana.
- **cod-weekly split 29.06-05.07** — Rafał dodaje segment 01-05.07 przed pn 06:00 UTC (albo dopisze się później).
- **klucz GMaps Papu** `...OKiGmE` — ta sama restrykcja przy pracach nad Lokalką.
- KROK 0 pozostałe: firewall to jedyny realny brakujący element.

## 5. STAN OGÓLNY
- Master **+9 ahead origin** (NIE push). Regresja kanonu na koniec dnia po moich zmianach: route-order 4191/0, Q1 4213/0 (baseline dryfował w górę przez równoległe sesje tmux17/18 — to normalne, pliki rozłączne).
- Jutro rano samo się zamelduje: cod-weekly 06:00 UTC (alert tak/tak), rosnący licznik `gps_arrived_at`.
- Tracker `eod_drafts/2026-07-02/ZIOMEK_STAN_AUDYTY_1i2.md` + `memory/todo_master.md` zaktualizowane (nagłówek 05.07 ~19:45).
- Backup memory: rutynowy (BX11 restic 03:30).
