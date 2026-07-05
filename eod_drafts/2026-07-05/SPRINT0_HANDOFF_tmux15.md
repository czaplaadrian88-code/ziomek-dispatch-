# HANDOFF → sesja tmux 15: SPRINT 0 „Prawda i bramki" (start 05.07 wieczór → 08.07)

> Od: sesja konsolidacyjna 05.07 (raport: `eod_drafts/2026-07-05/KONSOLIDACJA_STANU_0507.md`, memory `ziomek-status-konsolidacja-2026-07-05.md`).
> Cel sprintu: odblokować łańcuch werdyktów (5b → O2/feas_carry/autonomia), zdążyć przed wygaśnięciem monitora route-order (07-10.07), naprawić najstarszy ops-dług (cod-weekly). **Bez tego kolejne sprinty pracują na fałszywych danych.**

## ⛔ ZASADY (bez wyjątków)
1. **Protokół #0**: `memory/ziomek-change-protocol.md` — wklej PROMPT, ETAP 0→7. Testy bazowe ZIELONE przed i po (`/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q`, baseline 4109/0/23skip/11xfail; po L6.C na tej maszynie może być wyżej — porównaj z ostatnim zapisem trackera 4184/0).
2. **ADR-007 multi-sesja**: worktree per zadanie, commit po JAWNYCH ścieżkach (nigdy `git add -A`), przed edycją `git log --oneline -10` + sprawdź cudze `.bak-*`. Master jest ahead 2 vs origin (L6.C+5b, push za Adrianem) — NIE pushuj.
3. **ZERO flipów flag, restartów usług i deployów bez ACK Adriana**; okna peak (Pn-Pt 11-14, 17-20; So 16-21) = zero restartów. Telegram nietykalny.
4. Po każdym domknięciu: zaktualizuj tracker `eod_drafts/2026-07-02/ZIOMEK_STAN_AUDYTY_1i2.md` (protokół aktualizacji na górze) + `memory/todo_master.md`.

## ZADANIE 1 — A0-GEOFENCE: adopcja GPS-5b (effort: WYSOKI, priorytet #1)
**Problem:** kod 5b LIVE end-to-end (apka vc60 `6093a56`+`5e44405`, backend `e5b3dc0` kill-switch `ENABLE_GPS_ARRIVAL_INGEST`, konsument `9b1e30c`), ale pokrycie `gps_arrived_at` w `courier_ground_truth` = **0/546** (pomiar 05.07 ~16:00 UTC). Restart courier-api zaplanowany ~18:05 UTC 05.07 — pokrycie-0 przed nim jest SPODZIEWANE.
**Zrób:** (a) po ~18:05 potwierdź restart courier-api czysty i endpoint `POST /arrival` żywy; (b) monitoruj licznik `gps_arrived_at` w ground_truth + `n_truth_gps_arrival` w oracle czasówek — musi ruszyć z 0 wraz z adopcją vc60 przez kurierów; (c) jeśli do rana 06.07 dalej 0 przy aktywnych kurierach na vc60 → diagnoza łańcucha (logi apki/courier_api: czy POST /arrival dochodzi, czy kill-switch env ustawiony, czy merge do ground_truth pisze) i fix U ŹRÓDŁA (jeśli w backendzie); fix wymagający nowej apki (vc61) = TYLKO raport, build za ACK.
**Zakres plików:** `courier_api/` + logi + `tools/` odczytowe. ⛔ NIE dotykaj silnika (dispatch_pipeline/feasibility/scoring) ani panelu.
**DoD:** licznik >0 rosnący ALBO raport z twardą diagnozą przyczyny + fix staged. Werdykt pokrycia ~07-08.07 liczy się OD adopcji — zapisz w [[gps5b-delivery-geofence-2026-07-05]] i trackerze.

## ZADANIE 2 — A0-ROUTEORDER: INV-SRC-ROUTE-ORDER przed 10.07 (effort: WYSOKI)
**Problem:** monitor `ziomek_time_route_monitor` wygasa ~10.07 SAM (decyzja: nie przedłużać go w obecnej formie); dziś 44-75 rozjazdów kolejności trasy/dzień; logika kolejności = 4 kopie w 3 repach (silnik/konsola/apka).
**Zrób:** rozszerz golden L6.A (`tests/golden/route_order_corpus.json`, 13/13) o parytet PEŁNEJ kolejności silnik==konsola==apka na świeżym korpusie (≥20 przypadków z carried/czasówki/paczki) + zaprojektuj trwałego następcę monitora (test/inwariant w CI zamiast timera). Konsolidacja 4 kopii do 1 źródła = OSOBNY sprint (nie teraz) — tu tylko SIATKA BEZPIECZEŃSTWA.
**Zakres plików:** `tests/` (nowe golden) + `tools/ziomek_time_route_monitor*`. ⛔ `dispatch_pipeline.py`/`plan_manager.py`/repo panelu = READ-ONLY.
**DoD:** golden rozszerzony zielony + następca monitora zakodowany (aktywacja za ACK) + wpis w trackerze.

## ZADANIE 3 — A0-OPS: cod-weekly FAILED + duplikat GPS legacy (effort: ŚREDNI)
**Problem:** `dispatch-cod-weekly` żywo w stanie failed od 02.07 (WD-13; diagnoza exit1 częściowo w `FALA1_codweekly_raport.md`); to PIENIĄDZE. Plus duplikat GPS legacy @reboot (PID 1006/1010) do wygaszenia.
**Zrób:** (a) diagnoza root-cause faila cod-weekly → patch staged + test; wykonanie/restart timera ZA ACK; ⚠ backfill 4 tygodni COD = OSOBNA decyzja Adriana, NIE ruszaj (15-21.06 w ogóle nie ruszać); (b) plan wygaszenia duplikatu GPS legacy (co to za proces, skąd @reboot, co go konsumuje) — wykonanie za ACK.
**Zakres plików:** unity/timery `dispatch-cod-weekly*` + skrypt COD + crontab (odczyt). ⛔ NIE dotykaj demonów silnika (dispatch-shadow/panel-watcher/gps/sla).
**DoD:** przyczyna nazwana, patch staged z testem, sekwencja deploy-za-ACK wypisana, tracker zaktualizowany.

## ZADANIE 4 — A0-DOCS: ✅ WYKONANE 05.07 przez sesję konsolidacyjną
Tracker (nagłówek 05.07 + §2 L3/L4/L5/V328), memory, sprint_timeline, todo_master — zrobione. Nic do roboty; jeśli Twoje zadania 1-3 zmienią stan — aktualizuj tracker sam (zasada: sesja bez wpisu do trackera = NIEZAKOŃCZONA).

## Kolejność / kolizje
Zadania 1-3 są rozłączne plikowo (courier_api / tests+tools / systemd-ops) — mogą iść równolegle (worktree per zadanie). Dziś wieczór (05.07, niedziela, peak So minął, Nd peak TBD — sprawdź ruch przed czymkolwiek „gorącym"): zacznij od Zadania 1 (a) — obserwacja po restarcie 18:05. Jutro (Pn 06.07) pamiętaj o cudzych at-jobach: at-205 12:40 (GC realny), at-206 14:30, at-208 19:30 — NIE koliduj z nimi i nie odwołuj ich.
