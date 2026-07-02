# FALA1 — watchdog-close — raport (lane: watchdog-close, 2026-07-02)

**Branch:** `fix/watchdog-close` (worktree `/root/.openclaw/workspace/wt-watchdog`, z `c6e2c13`)
**Cel:** trwałe domknięcie znalezisk A+B audytu 2.0 (§2 MASTER) — warstwa alertów staleości + `cod-weekly` + false-"failed" oneshoty.
**Tryb:** PRZYGOTOWANIE. 🔴 enable / daemon-reload / restart = koordynator za ACK Adriana. Zero mutacji żywego systemu (żywy host + kanon czytane READ-ONLY; wszystko jako staging w worktree; ledger tykany tylko na kopii w /tmp).

---

## 1. STAN ZASTANY (ground-truth, READ-ONLY)

- **Finding A — warstwa staleości była martwa 5 dni.** 3 monitory (`dispatch-watchdog` + `dispatch-delivered-integrity` + `dispatch-state-panel-monitor`) zbiorowo `disable` 26.06 19:43. Dziś re-enabled (§2 MASTER; watchdog żywy, next 06:50 UTC). **Root trwałości:** wszystkie 3 timery planowane WYŁĄCZNIE przez `OnUnitActiveSec` bez `OnCalendar`. `dispatch-watchdog.timer` ma nawet `Persistent=true`, ale to **NO-OP** — `Persistent=` działa tylko z `OnCalendar=` (systemd.timer(5)). Bez kotwicy wall-clock warstwa jest krucha na daemon-reload / zbiorowy disable.
- **Finding B — `dispatch-cod-weekly` całkiem odsłonięty (finanse COD).** JEDYNY dispatch-job bez `.service.d/` (brak OnFailure, brak recordera sukcesu, brak MemoryMax) i **niezarejestrowany w cron_health**. Journal potwierdza `Main process exited status=1/FAILURE` **co poniedziałek**: 08.06, 15.06, 22.06, **29.06** — awaria główna była CICHA na poziomie systemd (exit-1 bez alertu). Ostatni sukces >24 dni temu.
- **watchdog śledzi 14/~50 timerów.** W `cron_health.json` (20 wpisów): 8 cron-timerów z realnym progiem, 6 długobieżnych (thr N/A), a **6 cron-timerów ma `thr=None`** → watchdog ich NIE liczy (`is_stale` wymaga progu): `cod-panel-ingest`, `downstream-crosscheck`, `faza7-kpi`, `liveness-probe`, `restic-backup`, `retro-learning`. (Audytowe „7" liczy też `monitor-419` — patrz POZA PARTYCJĄ.)
- **3 false-"failed" zdrowe oneshoty.** `cod-panel-ingest`, `downstream-crosscheck`, `retro-learning` mają w ledgerze `status=failed` + `last_success=null` (zamrożone na starym failu), a systemd potwierdza że **DZIŚ kończą się sukcesem** (`Result=success`, `ExecMainStatus=0`, świeże "Deactivated successfully"). Brak im recordera sukcesu (`cron_health_success.conf`), więc ledger nie odzwierciedla rzeczywistości. `faza7-kpi`/`restic-backup` recorder już mają; `liveness-probe` sam się rejestruje.
- **Mechanizm recordera już istnieje (parytet):** `observability/record_oneshot_success.sh` woła `cron_health.record_run_success(unit)` przez `ExecStopPost` (gate `SERVICE_RESULT=success`). Używa go 44 innych jednostek (np. `cod-weekly-preflight.service.d/cron_health_success.conf`).

---

## 2. ZMIANY (przygotowane)

### 2a. `observability/cron_health.py` (JEDYNY tknięty .py)
- **Rejestr progów `_DEFAULT_STALE_THRESHOLDS_H`** (jedno źródło prawdy w module) — 7 jednostek (cod-weekly + 6× thr=None). Wartości spójne z `alert_onfailure._UNIT_METADATA` (wtórny fallback watchdoga).
- **Backfill progu w `record_run_success` / `record_run_failure` / `_ensure_unit`** — gdy wpis nie ma progu, a jest w rejestrze, prog wpisywany do ledgera. Dzięki temu `record_oneshot_success.sh` (przekazuje `expected_max_silence_h=None`) **sam populuje prog** przy każdym sukcesie → watchdog (niezmieniony) czyta go z `entry.expected_max_silence_h`.
- **CLI `python -m dispatch_v2.observability.cron_health`:**
  - `--record-success <unit>` — zapis sukcesu (dla oneshotów; równoważne `record_oneshot_success.sh`, ta sama funkcja).
  - `--sync-thresholds` — idempotentny zapis progów rejestru do ledgera + rejestracja brakujących (cod-weekly). **Threshold-only** — nie tyka `last_success`/`last_failure`, więc nie maskuje awarii ani nie fałszuje sukcesu.
  - `--dry-run` — READ-ONLY podgląd werdyktu staleości (rozdzielczość progu jak watchdog: ledger → rejestr → `_UNIT_METADATA`), bez zapisu i bez Telegrama.
- Zero zmian w `watchdog.py` / `alert_onfailure.py` (poza partycją) — progi docierają do watchdoga przez ledger.

### 2b. Tabela progów (cadence timera × margines)

| Jednostka | Cadence (timer) | Próg | Uzasadnienie |
|---|---|---|---|
| `dispatch-cod-weekly.service` | weekly Mon 06:00 (168h) | **192.0h** | 168h + 24h (1 pominięty poniedziałek), jak bliźniak `preflight` |
| `dispatch-cod-panel-ingest.service` | weekly Mon 08:30 (168h) | **192.0h** | 168h + 24h margines |
| `dispatch-faza7-kpi.service` | daily 06:00 (24h) | **25.0h** | 24h + 1h; == `_UNIT_METADATA` (spójność z fallbackiem) |
| `dispatch-restic-backup.service` | daily 03:30 (24h) | **25.0h** | 24h + 1h margines |
| `dispatch-retro-learning.service` | daily 04:30 (24h) | **25.0h** | 24h + 1h margines |
| `dispatch-downstream-crosscheck.service` | co 5 min | **1.0h** | 12× cadence; sieć na zawis (sam watchdog biega co 4h → krótszy prog i tak nie przyspieszy wykrycia) |
| `dispatch-liveness-probe.service` | co 2 min | **1.0h** | 30× cadence; sieć na zawis meta-monitora |

### 2c. `tests/test_cron_health_registrations.py` (NOWY, 9 testów, behawioralne C13)
Sterują fixture-ledgerem i asertują realny werdykt alert/no-alert przez `watchdog.run_once` / `is_stale` / `scan_stale` — nie samą obecność klucza. Pokrycie: rejestracja+idempotencja `--sync-thresholds`; backfill progu z sukcesu i z faila; (i) cod-weekly stale 9d → ALERT; (ii) zdrowy oneshot z `record_run_success` → NO-ALERT; false-"failed" → clear przez record_success; (iii) prog edge 191.9h/192.1h; **mutation-guard polaryzacji**; parytet `scan_stale` vs werdykt.

### 2d. STAGING drop-inów — `deploy_staging/etc/systemd/system/` (10 plików, ścieżki lustrzane)
- **Kotwice OnCalendar (finding A):** `dispatch-watchdog.timer.d/oncalendar.conf` (4h), `dispatch-delivered-integrity.timer.d/oncalendar.conf` (20 min), `dispatch-state-panel-monitor.timer.d/oncalendar.conf` (10 min). Wzorzec: `OnUnitActiveSec=` (pusty → **reset całej listy timerów**, monotonic+calendar, systemd.timer(5)) + re-deklaracja jednej bazy wall-clock + `OnBootSec`. **Bez podwójnego biegu** (jedna baza planowania). Watchdog dostaje `Persistent=true` (teraz działa: catch-up po downtime). Oczekiwany merge opisany w nagłówku każdego `.conf` (weryfikacja: `systemctl cat`).
- **cod-weekly parytet z bliźniakiem `preflight` (finding B):** `dispatch-cod-weekly.service.d/{onfailure,cron_health_success,resource_limits}.conf`.
- **Recordery false-"failed":** `dispatch-{cod-panel-ingest,downstream-crosscheck,retro-learning}.service.d/cron_health_success.conf` (1:1 z bliźniakami) + `dispatch-retro-learning.service.d/onfailure.conf` (retro nie miał OnFailure).
- `deploy_staging/README_INSTALL.md` — sekwencja instalacji + rollback.

**⚠ Odchylenie od zlecenia (świadome, do decyzji koordynatora):** zlecenie sugerowało nazwę `telegram-onfailure.conf`; użyłem `onfailure.conf` dla **1:1 parytetu nazw** z 50 bliźniaczymi drop-inami (`*/onfailure.conf`) — Przykazanie #0 „parytet bliźniaków". Treść dyrektywy 1:1. Trywialne do zmiany nazwy jeśli install-skrypt koordynatora oczekuje innej.

---

## 3. DOWODY (nie deklaracje)

**Weryfikacja bez zmian żywych** — testy przeciw kodowi worktree przez realną kopię pakietu (`conftest.py` twardo wstawia `_SCRIPTS_ROOT=/…/scripts` na sys.path[0], więc goły `pytest tests/` z worktree importuje KANON, nie moje zmiany — patrz POZA PARTYCJĄ).

- **Nowe testy: 9/9 PASS** przeciw kodowi worktree. Goły run (kanon, bez moich zmian) → 8/9 FAIL (kanon nie ma `sync_thresholds` itd.) = dowód, że testy realnie ćwiczą nowy kod.
- **MUTATION-CHECK:** fizyczne odwrócenie warunku progu w kopii (`is_stale`: `silence_h > threshold` → `< threshold`) → `test_stale_verdict_polarity_mutation_guard` **FAIL** (`assert False is True`) = mutant zabity, test sprawdza POLARYTET nie obecność.
- **PEŁNA REGRESJA (A/B na kopii baseline-comparable):**
  - BEZ moich zmian: `3709 passed, 23 skipped, 11 xfailed, 0 failed`.
  - Z moimi zmianami: `3718 passed (+9), 23 skipped, 11 xfailed, 0 failed`.
  - **Delta = +9 passed (moje testy), ZERO nowych FAILi.** Różnica xfail/xpass vs plik baseline (9 xf/2 xp → 11 xf/0 xp) jest **pre-existing niedeterminizmem środowiska** (identyczna z moimi zmianami i bez nich).
  - `py_compile` OK: `observability/cron_health.py` + test.
- **ETAP-5 na ŻYWYM ledgerze (kopia /tmp, READ-ONLY):**
  - **(A) cod-weekly łapane:** OnFailure — handler `dispatch-onfailure-alert@.service` istnieje → staged `onfailure.conf` znaczy, że exit-1 z 29.06 (potwierdzony w journalu) wysłałby Telegram. Staleość — cod-weekly stale 9d (216h) → `--dry-run` STALE @192h.
  - **(B) 3 false-"failed" clear:** `status=failed` → `record_run_success` → `status=ok`; `--dry-run would_alert_stale` spada.
  - **(C) BURST-CHECK (ile odpaliłoby DZIŚ po wdrożeniu):** przed = **3** (3 zamrożone false-"failed" z backfillem progu); po symulacji deployu (`--sync-thresholds` + `--record-success` ×3 zweryfikowanych-zdrowych) = **0**. **Wdrożenie nie spamuje.** cod-weekly rejestrowane z grace (`last_updated=now`) → nie stale przy deployu; jego bieżąca awaria łapana przez OnFailure w najbliższy poniedziałek.

---

## 4. DEPLOY ZA ACK (dokładna sekwencja — wykonuje KOORDYNATOR, off-peak, za ACK Adriana)

1. `cp -rv deploy_staging/etc/systemd/system/* /etc/systemd/system/`
2. `systemctl daemon-reload` 🔴
3. Weryfikacja merge (brak dublowania; oczekiwane wartości w nagłówku każdego `.conf`):
   - `systemctl cat dispatch-watchdog.timer dispatch-delivered-integrity.timer dispatch-state-panel-monitor.timer`
   - `systemctl cat dispatch-cod-weekly.service`
   - `systemctl list-timers --all | grep -E 'watchdog|delivered-integrity|state-panel-monitor'` → każdy JEDEN next-elapse
4. Rejestracja progów + cod-weekly w ledgerze (idempotentne, threshold-only):
   `PYTHONPATH=/root/.openclaw/workspace/scripts /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.observability.cron_health --sync-thresholds`
5. Zasianie 3 zweryfikowanych-zdrowych false-"failed" (czyści zamrożone "failed"; potem samo-odświeża przez ExecStopPost):
   `... --record-success dispatch-cod-panel-ingest.service` (i `dispatch-downstream-crosscheck.service`, `dispatch-retro-learning.service`)
6. Nadzorowany potwierdzający `--dry-run` → oczekiwane `would_alert_stale=0`.
7. (opcjonalnie, jeśli 3 monitory były `disable`) `systemctl enable --now dispatch-watchdog.timer dispatch-delivered-integrity.timer dispatch-state-panel-monitor.timer` — DZIŚ są już enabled/active (§2), więc krok głównie potwierdzający po daemon-reload.

**Bez restartu długobieżnych, bez Telegrama, bez peak.** Kod cron_health jest importowany świeżo przez oneshoty/watchdog per bieg → wejdzie po merge+daemon-reload bez restartu procesu.

---

## 5. ROLLBACK

- Drop-iny: `rm -rf` dodanych katalogów `*.d/` (lub konkretnych `.conf`) → `systemctl daemon-reload`. Bazowe timery/serwisy wracają do stanu sprzed.
- Ledger: progi bez żywego watchdoga są bezczynne; pełny revert = odtworzyć kopię `dispatch_state/cron_health.json` sprzed deployu (zróbcie backup przed krokiem 4).
- Kod: `git revert` commita cron_health (czysto — dodaje tylko rejestr/CLI/backfill, nie zmienia istniejących ścieżek werdyktu).

---

## 6. POZA PARTYCJĄ (do koordynatora / osobne wątki)

- **`monitor-419` (audytowe „7. thr=None"):** w ledgerze `type=long_running` (watchdog słusznie pomija), ale `_UNIT_METADATA` mówi `cron_timer 24h` (rozjazd typu) i jednostka `enabled=disabled` (nie wstanie po reboocie). Naprawa = enable (ACK) + ujednolicenie typu (edycja `alert_onfailure.py` — poza moim jedynym dozwolonym .py). Nie tknięte.
- **`restic-backup` bez `onfailure.conf`** (ma recorder). Drobna luka parytetu — flaga do domknięcia (trywialny drop-in).
- **cod-weekly root-cause u ŹRÓDŁA** (brak bloku tygodnia w arkuszu) = lane `cod-weekly-diag`. Ten lane dowozi tylko OBSERWOWALNOŚĆ (OnFailure + rejestracja + parytet), nie fix arkusza/`cmd_write`.
- **Wyzwalacz zbiorowego `disable` 26.06** (skutek uboczny prac liveness/telegram-off) — moje OnCalendar czyni schedule odpornym na daemon-reload, ale samego zdarzenia-wyzwalacza nie adresuję (governance).
- **Krucha ścieżka testów w worktree:** `conftest.py` twardo wstawia `_SCRIPTS_ROOT=/…/scripts` na sys.path[0] → goły `pytest tests/` z worktree testuje KANON. Dodatkowo `test_a2_selection_shadow.py` (15) i `test_courier_reliability.py` (8) liczą ścieżkę modułu przez `Path(__file__).resolve().parents[2]` (zakłada katalog nazwany `dispatch_v2`) → w worktree `wt-watchdog` rzucają `SkipTest` raportowany jako FAIL. To **artefakt nazwy worktree, nie regresja** (te 23 przechodzą na kopii w układzie kanonicznym i na kanonie). Regresję robiłem na realnej kopii `…/dispatch_v2` (układ baseline-comparable). Koordynator scala do kanonu i tam `pytest tests/` jest wiarygodny natywnie. Higiena testów (parents[2] → marker/rootdir) = kandydat do osobnego wątku.

---

## 7. PLIKI

- Kod: `observability/cron_health.py`
- Testy: `tests/test_cron_health_registrations.py`
- Staging: `deploy_staging/etc/systemd/system/**` (10 drop-inów) + `deploy_staging/README_INSTALL.md`
- Raport: `eod_drafts/2026-07-02/FALA1_watchdog_raport.md`
