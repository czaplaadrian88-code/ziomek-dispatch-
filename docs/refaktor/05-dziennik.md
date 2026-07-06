# 05 — Dziennik implementacji (Faza 5)

Wpisy po każdym kroku: co / dlaczego / odstępstwa od planu / dowody. Konwencja commitów: `refactor(zakres): ...`; kroki na pod-gałęziach `refaktor/krok-NN-*`, merge do master po zielonej regresji.

---

## PAKIET 0 — „Siatka i rozbrojenie min" (06.07.2026, sesja tmux 21)

**Baseline wejściowy (kanon, 11:15 UTC):** 4245 passed / 0 failed. **Wyjściowy (kanon, ~11:55):** 4263 passed / **3 failed — NIE-NASZE** (patrz „Zdarzenia zewnętrzne").

### K01 — devlint ratchet (commit `95d0ff3`, merge FF)
- **Co:** `tools/devlint/` (ruff.toml E9/F/B/PLE bez E501; mypy.ini łagodny na 19 modułów rdzenia; `ratchet_check.py` z baseline.json; README z polityką). Venv narzędziowy `/root/.openclaw/venvs/devlint` (ruff 0.15.20, mypy 2.1.0) — venv silnika NIETKNIĘTY (ACK Faza 0 pyt. 6).
- **Baseline zastany:** ruff **608**, mypy **0** (kod nietypowany — mypy łagodny liczy tylko adnotowane; licznik zacznie chronić typowane moduły core/ od K09+).
- **Dowód działania ratchetu:** przy K04 złapał +1 F401 (nieużywany import w moim teście) → sprzątnięte przed commitem. Narzędzie gryzie.
- **Odstępstwa:** brak.

### K02 — postpone_sweeper schema-fix (commit `9ee1936`, merge FF)
- **Co:** `postpone_sweeper.py:103-106` — odczyt stanu na PŁASKI `{oid: rec}` + pole `courier_id` (schemat zweryfikowany na żywym `orders_state`: 194 rekordów, `courier_id='520'`). Gałąź `POSTPONE_RESOLVED` znów osiągalna → rozbrojona mina „duplikat propozycji dla przypisanego zlecenia po re-enable postpone/Telegrama" (D1/W1, deep-audit #1.8).
- **Testy:** nowy `test_postpone_sweeper_schema_k02.py` — repro **RED na starym kodzie → GREEN po fixie** + charakteryzujący no-op na pustym postponed (zachowanie live bez zmian) + sentinel koordynatora 26.
- **Odstępstwo/finding:** istniejący `test_postpone_auto_replan.py` mockował BŁĘDNY schemat (`{"orders":{...}}` + `cid`) — testy walidowały kod względem samego siebie, nie żywego stanu (lekcja #200). Mocki przepisane na żywy schemat.
- **Deploy:** sweeper = oneshot timer → świeży proces przejął kod od następnego ticku automatycznie; ścieżka live nadal no-op (`postponed_proposals.json={}`). Zero restartów.

### K03 — kanon zapisu ścieżek uśpionych (commit `50850d0`, merge FF)
- **Co:** (a) `global_alloc_store.write` — unikalny mkstemp zamiast współdzielonego `f"{path}.tmp"` (W5); (b) `courier_resolver._save_last_known_pos` — CAŁY cykl load→merge→prune→write pod `fcntl.LOCK_EX` na lockfile (W4: koniec lost-update ROZŁĄCZNYCH cid między procesami; merge-by-ts chronił tylko ten sam cid); (c) telegram delta-kanon = **N-D, już wykonane wcześniej** (dowód: `locked_set/pop/merge_missing` w użyciu w telegram_approver:1779/1788/4275, `save_pending` bez żywych callerów — grep).
- **Testy:** `test_write_canon_k03.py` (5): round-tripy + współbieżność wątkami (3 wątki × rozłączne cid → zero lost-update) + charakteryzujący merge-by-ts.
- **Deploy:** long-running daemony przejmą kod przy najbliższym restarcie (kolejka FLIPMASTERA, za ACK); oneshoty (plan-recheck/czasówka) od następnego ticku — **`.lock` już powstał w dispatch_state (11:46), mechanizm żywy**.

### K04 — world_record v0 (commit `ac9483c`, merge `f31fe86`)
- **Co (ADR-R04):** NOWY `world_record.py` (capture: pełny snapshot flags+sha1, flota (dataclass→json-safe), order_event, `now`, wyniki OSRM decyzji, mtimes 4 map kalibracyjnych, verdict; zapis `dispatch_state/world_record/world_record-YYYYMMDD.jsonl` przez `jsonl_appender`; retencja 14 d; anty-prod guard pod pytestem) + rekorder `route`/`table` w `osrm_client` (proces-globalny — łapie wątki puli kandydatów i cache-hity; nieaktywny = 1 if) + hook w `shadow_dispatcher.process_event` za `ENABLE_WORLD_RECORD` (**brak klucza w flags.json = OFF = delegacja 1:1; kod inertny do restartu**). Flaga udokumentowana w `ZIOMEK_LOGIC_REFERENCE.md`.
- **Testy:** `test_world_record_k04.py` (7): ON≠OFF, fail-soft (zapis pada → decyzja nietknięta), wyjątek decyzji propaguje a rekorder się domyka, rekorder z wątku, wrappery route/table, GC retencji.
- **Odstępstwa od planu:** (a) zamiast rozszerzać `obj_replay_capture` — osobny moduł (czystszy szew, capture solvera nietknięty); (b) **czasówka_scheduler = świadomie N-D w v0** (osobny proces; dołączy przy wspólnym WorldState w pakiecie 2); (c) rekorder proces-globalny zamiast contextvar (pula wątków NIE dziedziczy contextvarów; _tick ocenia sekwencyjnie → okno start/stop = 1 decyzja).
- **Flip (do FLIPMASTERA, za ACK Adriana):** dopisać `ENABLE_WORLD_RECORD: true` do flags.json + restart dispatch-shadow (można sprząc z najbliższym restartem z ich kolejki). Po flipie: 3-5 dni zbierania korpusu (≥1 peak) → bramka K06+.

### Zdarzenia zewnętrzne odnotowane w trakcie pakietu (multi-sesja)
1. **3 czerwone testy `test_state_schema_validator` = dryf ŻYWYCH danych, nie kod refaktoru** (obecne także na czystym masterze bez K04): wpis `485853` w `courier_ground_truth.json` ma TYLKO `{gps_arrived_at, gps_arrival_source: "app_geofence", courier_id: 179, updated_at}` — ścieżka geofence **5b** (courier-api, domena sesji 15) tworzy wpis bez `last_status_code`. **Skutek: night-guard dziś w nocy będzie czerwony na tym samym.** Do decyzji właściciela courier-api: dopisywać `last_status_code` przy tworzeniu wpisu geofence ALBO oznaczyć pole jako opcjonalne w baseline walidatora. Zgłoszone Adrianowi w STOP-ie pakietu 0.
2. Inna sesja zmergowała w trakcie `cd0bb25`+`efbf031` (pickup-buffer, flaga OFF) — pliki rozłączne z naszymi, merge K04 czysty.
3. Środowisko worktree: uzupełniono pkgroot o symlinki `flags.json` i `logs/` (bez nich conftest-strip i script-runnery fałszywie czerwone w biegu z `ZIOMEK_SCRIPTS_ROOT`).

**Stan po pakiecie 0:** wszystkie 4 kroki na masterze; zero restartów wykonanych; zero zmian zachowania live (K02/K03 dormant/równoważne, K04 inertne za flagą). Baseline dla pakietu 1 = 4263/0 + rozwiązany dryf 5b.

---

## PAKIET 1 — „Determinizm wejść" (start 06.07 po ACK; K05 ✅, K06 czeka na korpus, K07-K08 następne)

### Naprawy odblokowujące (przed K05, za zgodą Adriana w czacie)
- **Dryf 5b w ground_truth (commit `abbea71`, master):** `last_status_code` zdjęty z required w `tools/state_schema_baseline.json` (wersja 20260706) — od 5b wpis RODZI SIĘ z samym `gps_arrived_at` (case 485853), pole stało się lifecycle-key dokładnie wg filozofii zapisanej w `_about` baseline'u; konsumenci czytają przez `.get()`. Test syntetyczny przełączony na ofiarę `updated_at` (pisany każdym writerem). Walidator na żywych plikach: **exit 0**. Sesja 15 zamknięta → fix przejęty przez sesję refaktoru za zgodą Adriana. NIE dopisywaliśmy sztucznego statusu w apce — to fałszowałoby semantykę danych; zmienił się KONTRAKT, więc poprawiony kontrakt.
- **Rejestracja `PLAN_GC_DRY_RUN` (commit `9d08d7b`):** at-205 (12:40, flip-gate FLIPMASTERA, L3 GC-real) dopisał klucz do flags.json → strażnik-zapadka `test_no_new_unstripped_flags_ratchet` słusznie czerwony dla CAŁEJ suity. Zarejestrowana w `ETAP4_DECISION_FLAGS` + stała-fallback `True` (= default czytelnika `plan_recheck:2444`), zgodnie z instrukcją strażnika. Zero zmiany runtime (kanon=flags.json, live=false). ⚠ luka procesu do zgłoszenia FLIPMASTEROWI: scheduled_flip_gate dopisujący NOWY klucz powinien wymagać wcześniejszej rejestracji ETAP4 (inaczej każdy taki flip wywala suitę wszystkim).

### K05 — FlagSnapshot per tick (commity `9d08d7b`+`a26cc5c`, merge do master)
- **Co (ADR-R01):** `common.flags_snapshot_begin()/end()` — chokepoint w `load_flags()` (aktywny snapshot wygrywa nad dyskiem); pętla `shadow_dispatcher.run` owija `_tick` w begin/try/finally-end. Gate `ENABLE_FLAG_SNAPSHOT` (kanon flags.json, czytany ŻYWO w begin; **brak klucza = OFF = 1:1**; stała-fallback False wzorcem perf-lazy; NIE-decyzyjna). Obejmuje wszystkie odczyty flag()/decision_flag() we wszystkich modułach i wątkach puli kandydatów procesu shadow; inne procesy nietknięte.
- **Testy (6):** charakteryzujący hot-reload bez snapshotu, **ON≠OFF** (zmiana flags.json mid-tick niewidoczna pod snapshotem, widoczna po end), gate OFF/brak klucza, fail-soft na nieczytelnym flags.json, idempotencja end + wzorzec finally.
- **KOREKTA względem diagnozy D6 (uczciwość):** perf-lazy TTL (finding E audytu 2.0) jest **JUŻ LIVE** (`ENABLE_PERF_LAZY_MEMBERS=true`) — raport perf 04.07 (SLO czerwone) mierzony był PRZY nim. K05 nie jest więc głównym lekiem na D6; jego realna wartość = **spójność flag w decyzji + determinizm replayu**. D6 (perf) → osobne profilowanie w dalszym pakiecie.
- **Sprzątnięte przy okazji (polityka devlint):** martwy `from typing import List` w shadow_dispatcher (dryf po cudzym pickup-buffer) — ratchet 608/608.
- **Odstępstwo C1-git:** wspólny indeks zgarnął całość zmian common.py do pierwszego z dwóch commitów — treść poprawna, opisy się nakładają; bez przepisywania historii.
- **Flip K05 = za ACK, w oknie restartu shadow (razem z WR):** restart podnosi kod K04+K05 → klucz `ENABLE_WORLD_RECORD: true` → obserwacja → klucz `ENABLE_FLAG_SNAPSHOT: true` (oba hot).
- **Weryfikacja:** worktree 4272/0 · kanon po merge **4272/0** · ratchet 608/608 · py_compile OK.

### Korekta kursu od Adriana (06.07, po K05) — ZASADY FLIPÓW
**„Nic nie może się samo flipować; FLIPMASTERA nie ma (sesja zamknięta)."** Ustalenia: (a) jedyny samoczynny flip = at-205 12:40 (`PLAN_GC_DRY_RUN`→false), zaprogramowany 05.07 przez zamkniętą sesję; (b) pozostałe at-joby 206/208 = czyste RAPORTY (zweryfikowane w kodzie: verify NIE cofa flag — „nie auto-cofam bez pewności"); (c) dowód GC-real od 12:40: **0 skasowań, 0 błędów, plany zdrowe** → **Adrian ZATWIERDZIŁ pozostawienie GC realnego**. Od teraz: ŻADNYCH automatów zmieniających flagi; każdy flip = jawne TAK Adriana w czacie, wykonuje sesja ręcznie. Wszystkie odwołania do „FLIPMASTERA" w planie = nieaktualne (flipy wykonuje ta sesja za ACK).

### K07 — pre-proposal recheck PRZED pulą (commit `4719207`, merge do master)
- **Co (przygotowanie ADR-R02):** żywy HTTP fetch `czas_kuriera` wyprowadzony z oceny kandydata: `_k07_prefetch_fresh_ck` (RAZ na decyzję, przed pulą — unia worków CAŁEJ floty = dokładnie ten zbiór, który dziś fetchują kandydaci, bo worki są rozłączne per kurier; woła ISTNIEJĄCĄ `get_fresh_czas_kuriera_for_bag` → te same skip-reguły age/cache i synth-eventy, ZERO bliźniaka) + `_k07_apply_fresh_ck` (JEDNA reguła nadpisania dla ścieżki nowej I legacy — kontrakt ①). Pokrywa OBA wejścia (`shadow` i `czasowka`), bo siedzi w `_assess_order_impl`. Gate `ENABLE_PRE_RECHECK_BEFORE_POOL` (ETAP4; brak klucza=OFF=legacy 1:1; fail-soft → legacy). Closure-safety zweryfikowane (wywołania puli :6120+ PO przypisaniu :6116).
- **Rejestracja flag pod przyszły flip:** `ENABLE_WORLD_RECORD`/`ENABLE_FLAG_SNAPSHOT`/`ENABLE_PRE_RECHECK_BEFORE_POOL` dopisane do `ETAP4_DECISION_FLAGS` + stałe-fallback (lekcja PLAN_GC_DRY_RUN: klucz w flags.json bez rejestracji = czerwona zapadka dla wszystkich). Strażnik C-FLAG-EFFECT wymusił test efektu → dopisany uczciwy toggle `ENABLE_WORLD_RECORD` realnym mechanizmem flagi (tmp flags.json: OFF=zero zapisu / ON=nagranie).
- **Testy:** K07 7 (unia+dedup, jeden fetch/decyzję, gate, V327-off, fail-soft, reguła aplikacji 1:1, odporność na puste) + K04 8 (z toggle).
- **Weryfikacja:** kanon po merge **4277/0**; ratchet 608/608. Skipy 23→26 = 3 × `test_preshift_window_penalty` „okno czasowe (unik wrapu północy)" — warunkowe od godziny zegara, NIE od zmian.
- **Uwaga metodyczna (checker-krzyż):** `flag_effect_coverage_check` czyta testy z KANONICZNEJ ścieżki (hardcode), a ETAP4 z procesu — w biegu worktree daje fałszywe luki do czasu merge; werdykt ostateczny zawsze z kanonu po merge.

### FLIP `ENABLE_WORLD_RECORD` — WYKONANY 06.07 13:59-14:02 UTC (decyzja Adriana „tak, teraz")
Protokół: backup `flags.json.bak-pre-worldrecord-2026-07-06` → py_compile 6 modułów → **restart dispatch-shadow 13:59:29** (graceful: STOP totals 123/0; start czysty: ortools 55,3 ms, pre-warm login 5,2 s; podniósł kod K03/K04/K05/K07) → klucz `ENABLE_WORLD_RECORD: true` atomowo 14:00 (hot) → **DOWÓD 14:02: pierwszy rekord `world_record-20260706.jsonl`** — order 485904, verdict PROPOSE, **n_osrm=133** (route+table z pełnymi wynikami), flags_n=268 + sha1, fleet_n=12, calib mtimes (eta_quantile 04:35 / prep_bias 04:15 / reliability 04:30 / tiers 10:48). Latencja decyzji 2241 ms (norma). FLAG_FINGERPRINT od restartu pokazuje 3 nowe flagi (rejestracja ETAP4 = prawdomówność sondy). Jedyny ERROR w journalu = przedistniejący, celowy `COORD_GUARD` osrm (lekcja #140, sentinel na (0,0)) — nagrany do korpusu (dobrze: replay obejmie też degradacje). Rollback = usunięcie klucza (hot). **Korpus: start 06.07 14:02; bramka K06 ≈ 09-10.07 (≥1 pełny peak).** `ENABLE_FLAG_SNAPSHOT` NIE włączony (osobne TAK Adriana po obserwacji peaku). ⚠ systemd zgłasza „unit files changed on disk → daemon-reload" (pozostałość konsolidacji K3.7 innych sesji) — świadomie NIE wykonano (zmienia efektywną konfigurację wielu unitów; decyzja ops poza zakresem refaktoru).

### K08 — efekty uboczne PO decyzji (commit na krok-08, merge do master ~14:30 UTC)
- **Co (ADR-R02 „powłoka efektów"):** NOWY `effects_buffer.py` (begin/divert/flush; proces-globalny pod lockiem — wzorzec rekordera K04, bo pula wątków nie dziedziczy contextvarów; FIFO; fail-soft per wpis; cap 10k) + divert w helperach: `_append_difficult_case_log`/`_append_split_layer_guard_log`/`_append_earlybird_t30_shadow`/`_emit_feas_carry_blind` (pipeline) i `_emit_r6_breach_shadow`/`_emit_c2_shadow_diff_event` (feasibility — divert obejmuje SAM zapis przez wydzielone writery `_write_*_line`, event z `ts` budowany w miejscu zdarzenia = semantyka czasu bez zmian) + loadgov `_loadgov_save_alert_state`+alert Telegram. `assess_order` = begin/try-impl/finally-flush → pokrywa shadow, czasówkę i rekurencyjny kontrfaktyk early-bird; flush w finally = przy wyjątku efekty sprzed crasha wykonane (parytet z legacy).
- **Świadome N-D:** poison-alert V328 (`_pa_alert`) POZA divertem — jego WARTOŚĆ ZWROTNA steruje `last_sent_ts` (cooldown); ślepe odroczenie zmieniłoby semantykę stanu. Kandydat na K09 (alert-state przez powłokę z jawnym stanem).
- **Gate:** `ENABLE_EFFECTS_AFTER_DECISION` (ETAP4 + const False; brak klucza = OFF = bajt-parytet 1:1). Kolejność linii W OBRĘBIE decyzji może różnić się od wyścigu wątków legacy (dopuszczalne per plan K08); treść linii 1:1.
- **Testy (10):** FIFO, wątki→bufor, fail-soft per wpis, gate, helper pisze dopiero po flushu / OFF od razu, ordering ON w realnym wrapperze (monkeypatched impl: plik NIE istnieje w trakcie impl, istnieje po), flush-po-wyjątku, toggle realną flagą (C-FLAG-EFFECT).
- **Porządki przy okazji (ratchet):** martwy `import logging` (dispatch_pipeline — cudzy dryf po pickup-buffer) + martwy `cutoff` (tools/pickup_slip_model — cudzy cf88d82) → ratchet 608/608.
- **Weryfikacja:** kanon po merge **4289/0**; checker-krzyż flag-effect (worktree ETAP4 × kanoniczne testy) potwierdzony i rozwiązany merge'em jak przy K07.

### Plan wieczoru 06.07 (pre-zatwierdzone przez Adriana: „tak, włącz K05 po peaku")
Budziki łańcuchowe w tle (limit 10 min/ogniwo). ~15:45 UTC kontrola środka peaku (WR: liczba rekordów, błędy, latencja). **Po 18:00 UTC (koniec peaku 20:00 PL):** weryfikacja czystości peaku → flip `ENABLE_FLAG_SNAPSHOT: true` (hot, kod już w procesie od restartu 13:59) → dowód: FLAG_FINGERPRINT=1 + zdrowe ticki + wpisy world_record z flags.ENABLE_FLAG_SNAPSHOT=true → wpis tutaj. Flipy K07/K08 = OSOBNE TAK Adriana (nie dziś).

### K06a + replayer K06 (06.07 ~14:20-14:40 UTC) — PIERWSZY REPLAY 1:1 UDANY
- **K06a (`8eb499d`):** `_tick` przekazuje jawne `now` per zdarzenie (dotąd rekordy world_record miały `now=null` → zegar niereplayowalny). Semantyka 1:1 (impl wiąże 1 now/decyzję). ⚠ Fix-forward: 2 testy mockowały STARĄ sygnaturę `process_event` (fake bez `now`) — kontrakt wywołania zaktualizowany (klasa: sygnatura ma konsumentów w testach → mapa kompletności). ŻYWY proces przejmie K06a przy wieczornym restarcie.
- **Replayer `tools/world_replay.py` (K06):** zamrożenie flag z nagrania MECHANIZMEM K05 (`_FLAGS_SNAPSHOT_OVERRIDE`), OSRM serwowany z nagrania (FIFO per klucz + licznik missów), rehydracja `CourierState` (iso→dt, list→tuple po anotacjach), **pełny sandbox**: efekty K08 divertowane i ODRZUCANE (nie flushowane), world_record wyłączony, alerty no-op, `DISPATCH_UNDER_PYTEST=1` (mute file-logów). Join z `shadow_decisions` po order_id+ts, raport różnic pól (verdict/reason/best_cid/score/pool). 4 testy plumbingu.
- **DOWÓD ŻYWY (smoke na korpusie):** order **485907** — replay = zapis CO DO POLA: `PROPOSE / best=484 / score −113.46 / pool 5/10`, **osrm_misses=0** (mimo now=null!). Kontrakt ADR-R04 zademonstrowany w dniu budowy.
- **Weryfikacja:** kanon **4294/0** · ratchet 608/608.
- **Near-miss (klasa cwd-drift, do protokołu przy zamknięciu pakietu):** `git checkout -b` wykonany po `cd` do KANONU w poprzednim bloku poleceń → żywe repo na moment zeszło z mastera na gałąź (plikowo identyczne; cofnięte natychmiast `checkout master`). REGUŁA: komendy gałęziowe ZAWSZE z jawnym `cd <worktree> &&` w TYM SAMYM bloku.

---

## ZAMKNIĘCIE SESJI 06.07 ~14:45 UTC (Adrian: „skończmy tę sesję") — STAN PRZEKAZANIA
**Zrobione dziś:** Fazy 0-4 programu + Faza 5: Pakiet 0 KOMPLET (K01-K04) · Pakiet 1: K05/K06a/K06-replayer/K07/K08 KOMPLET deweloperka (wszystko na masterze). **`ENABLE_WORLD_RECORD` LIVE od 14:02** (zatwierdzone przez Adriana; nagrywa; NIE wyłączać — retencja 14 d sama sprząta). **Replay 1:1 UDOWODNIONY** (order 485907: pełna zgodność, 0 missów). Kanon: **4294/0**, ratchet 608/608. Poprawki obce domknięte: dryf 5b (`abbea71`), rejestracja PLAN_GC_DRY_RUN, 2×martwy kod.

**DO WYKONANIA przez następną sesję Ziomka (pre-zatwierdzone przez Adriana w tej sesji — cytaty w czacie 06.07):**
1. **Po peaku (20:00+ PL) / rano przed peakiem:** restart `dispatch-shadow` (podnosi K06a → rekordy z `now`; wzór dzisiejszego: backup flags → py_compile → restart → journal) **+ flip `ENABLE_FLAG_SNAPSHOT: true`** (hot; ACK Adriana: „tak, włącz K05 po peaku"). Dowód po: FLAG_FINGERPRINT=1, zdrowe ticki, world_record.flags pokazuje true.
2. **~09-10.07 bramka K06:** bieg `tools/world_replay.py` na korpusie z peakiem (rekordy z `now` ≠ null po restarcie) → raport parytetu → jeśli czysto, wpiąć do night-guard (K17 w planie).
3. Flipy `ENABLE_PRE_RECHECK_BEFORE_POOL` (K07) i `ENABLE_EFFECTS_AFTER_DECISION` (K08) = **OSOBNE TAK Adriana** (nie objęte dzisiejszym ACK).
4. Pakiet 2 (K09+ rdzeń-jako-moduł) po zamknięciu bramki K06.

**Reguły nadrzędne z tej sesji (Adrian, obowiązują wszystkich):** ŻADNYCH automatów zmieniających flagi; FLIPMASTER nie istnieje; każdy flip/restart = jawne TAK Adriana, wykonanie ręczne z backupem i dowodem. GC-real (PLAN_GC_DRY_RUN=false) ZATWIERDZONY.
