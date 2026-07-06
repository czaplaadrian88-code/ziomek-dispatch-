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
