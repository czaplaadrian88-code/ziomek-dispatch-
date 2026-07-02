# FALA-2 — cron-health-truth — raport (lane: cron-health-truth, 2026-07-02)

**Branch:** `fix/cron-health` (worktree `/root/.openclaw/workspace/wt-cron-health`, z `60084fa`)
**Cel:** koniec false-positive „failed"/„stale" w `observability/cron_health.py` — `is_stale`/`scan_stale` czytają PRAWDĘ z systemd zamiast ufać wyłącznie failure-only ledgerowi (audyt 2.0 motyw #2: „cron_health znaczy 3 zdrowe oneshoty jako failed").
**Tryb:** PRODUKUJĘ, NIE deployuję. Zero mutacji żywego systemu (kanon + /etc/systemd + żywy ledger czytane READ-ONLY; wszystko na kopiach w scratchpadzie). Deploy/merge/restart = koordynator za ACK.

---

## 1. ROOT-CAUSE false-positive'ów

Ledger `cron_health.json` jest **failure-only-autorytatywny**: `OnFailure` pisze faile, sukces piszą recordery (`ExecStopPost record_oneshot_success.sh` / self-register `liveness_probe` / inline). Jednostka, która **naprawdę zadziałała, ale jej recorder nigdy nie strzelił** (recorder dodany później, zepsuty, albo brak), zostaje zamrożona `status=failed` + `last_success=None`. `is_stale` (którego używa `watchdog.run_once` — realna ścieżka alertu) liczy ciszę od `last_success` (albo `last_updated` gdy None) → **werdykt STALE → FAŁSZYWY alert Telegram** dla jednostki, którą systemd zna jako zdrową dziś. To dokładnie „3 zdrowe oneshoty jako failed" z audytu 2.0.

FALA-1 (watchdog-close) zaadresowała to **reaktywnie** — dostawiła recordery (`ExecStopPost`), więc ledger sam się leczy przy NASTĘPNYM sukcesie. Ale to zostawia okno (między zamrożeniem a następnym biegiem+recorderem) i jednostki bez działającego recordera dalej kłamią. Mój lane dokłada **fix U ŹRÓDŁA**: werdykt nie ufa ślepo ledgerowi — cross-check z systemd.

## 2. CO ZMIENIONE (`observability/cron_health.py` — JEDYNY tknięty .py)

Nowe (read-only, fail-soft):
- **`systemd_probe(unit)`** — jeden `systemctl show --timestamp=unix -p LoadState,ActiveState,Result,ExecMainStatus,ExecMainExitTimestamp,ActiveEnterTimestamp,Type`. Zwraca `{available, active_state, result, exit_status, healthy(True/False/None), fresh_ts}`. `ActiveState` == `systemctl is-active`. Interpretacja: aktywny/uruchamiający się → healthy(now); oneshot `inactive`+`Result=success`+`ExecMainStatus∈{0,None}` → healthy (świeżość z `ExecMainExitTimestamp`, bo `ActiveEnterTimestamp` czyści się po deaktywacji oneshota); `failed`/nie-`success` → NOT healthy. **`LoadState!=loaded` → available=False** (gotcha: nieistniejąca jednostka raportuje `Result=success ActiveState=inactive` domyślnie — bez tego byłby false-„healthy").
- **`_run_systemctl(args, timeout)`** — jedyny styk z `subprocess` (cel monkeypatchu w testach). Każdy błąd/timeout/brak systemctl → `None` → caller degraduje do ledgera (nigdy nie rzuca).
- **`_parse_systemd_ts`** — `@<epoch>`/`` → aware UTC (odporne, bez parsowania stringów tz).
- **`_systemd_rescues_stale(unit, threshold, now)`** — JEDNO źródło reguły supresji dla `is_stale` I `scan_stale` (bez twin-drift, Przykazanie #0 bliźniaki): rescued=True gdy systemd potwierdza świeży sukces/bieg (fresh_ts w progu, albo aktualnie active), którego ledger nie zapisał.
- **`_systemd_truth_enabled()`** — env `CRON_HEALTH_SYSTEMD_TRUTH` (1/0). Unset → **ON w produkcji, OFF pod pytest** (hermetyczność całej istniejącej suity bez shellowania do hosta; ścieżki systemd testowane przez jawny opt-in). Wzorzec = telegram PYTEST-guard (lekcja #75).

Zmienione (kompatybilnie wstecz):
- **`is_stale(...)`** — wydzielony czysty `_is_stale_ledger` (dawna logika), potem: gdy ledger mówi stale I `_systemd_truth_enabled()` (lub `use_systemd=True`) → cross-check systemd; healthy+świeży → `False`. Dodany opcjonalny kwarg `use_systemd` (None→auto). Sygnatura zachowana.
- **`scan_stale(...)`** — wiersze dostają `stale_ledger` (surowy failure-only) OBOK `stale` (finalny, po systemd) + `systemd_healthy`/`systemd_state`. Jeden probe/jednostkę (reużyty), tylko dla ledger-stale. Klucze `unit/threshold_h/source/stale/hours_silent/status` bez zmian → wsteczna zgodność (istniejący `test_scan_stale_preview_matches_verdict` przechodzi).
- **CLI** — `--dry-run` pokazuje `would_alert_stale_ledger` vs `would_alert_stale` + `systemd_rescued` + kolumnę `systemd=healthy/unhealthy/unknown … RESCUED`; NOWY `--systemd-probe UNIT` (diagnostyka). **`--record-success`/`--sync-thresholds`/`--dry-run` z FALA-1 nietknięte** (100% wstecz).

**Watchdog i alert_onfailure NIE tknięte** — `watchdog.run_once` woła `cron_health.is_stale`, więc prawda systemd dociera do produkcji BEZ zmiany `watchdog.py` (fix u źródła, nie łatka na renderze).

## 3. KOMPLET RECORDERÓW SUKCESU (weryfikacja read-only /etc/systemd) — brak brakujących drop-inów

Sprawdzone WSZYSTKIE monitorowane cron-timery vs `*.service.d/cron_health_success.conf` na żywym hoście:
- **14 jednostek ma recorder** (drop-in z FALA-1 zainstalowany ~11:45; m.in. cod-weekly, cod-panel-ingest, downstream-crosscheck, retro-learning, faza7-kpi, plan-recheck…).
- **`dispatch-restic-backup.service`** — recorder **inline** w unicie (`ExecStopPost=…/record_oneshot_success.sh %n`), nie drop-in → OK.
- **`dispatch-liveness-probe.service`** — **self-register** (`liveness_probe.py:200 record_run_success`) → OK.
- **`dispatch-cod-weekly.service`** — ma recorder, ale **naprawdę pada** co pon. (finding B); systemd raportuje `Result=exit-code/failed` → mój cross-check **NIE ratuje** → nadal alertuje (poprawnie).

**Wniosek: żaden monitorowany cron-timer nie jest bez recordera sukcesu → ZERO brakujących drop-inów do stagingu.** Nic nie dodano do `deploy_staging/` („nic na wszelki wypadek", ETAP 3). Dodatkowo cross-check systemd **czyni kompletność recorderów mniej krytyczną** — werdykt jest prawdziwy nawet gdy recorder pęknie/zniknie.

## 4. DOWODY (nie deklaracje)

**Testy (kanoniczna kopia w scratchpadzie: worktree skopiowany jako `dispatch_v2/`, conftest-pin przepięty na kopię, `flags.json` zsymlinkowany — bo `tests/conftest.py` twardo pinuje kanon; C12(e)):**
- **3 pliki cron_health: 36/36 PASS** (12 nowych systemd-truth + 9 registrations + 14 watchdog).
- **Testy samo-lokalizujące** — importują `dispatch_v2.observability`, ZERO hardcode ścieżki worktree; potwierdzone że kopia importuje MÓJ kod (`systemd_probe present: True`).
- **MUTATION ×2 (C13, fizycznie zabite):**
  - M1 — odwrócenie świeżości `(now-fresh)<=threshold → >` w `_systemd_rescues_stale` → `test_rescue_freshness_polarity_mutation_guard` **FAIL** (mutant zabity).
  - M2 — polaryzacja healthy-gate `truth.healthy is True → is False` → `test_healthy_polarity_mutation_guard` **FAIL** (mutant zabity).
  - Po przywróceniu oba guardy zielone.
- **Behawioralne (nie obecność klucza):** werdykt liczony przez `watchdog.run_once` (realna ścieżka alertu) / `is_stale` / `scan_stale`; systemd tylko przez FAKE `_run_systemctl` (monkeypatch), NIGDY realny systemctl w testach.

**Smoke na ŻYWO (read-only, `--dry-run`):**
- **Live ledger DZIŚ:** `checked=15 would_alert_stale_ledger=0 would_alert_stale=0 systemd_rescued=0` (systemd on i off identycznie) — bo recordery FALA-1 wyleczyły 3 false-„failed" o 11:45. **Brak false-positive'ów do złapania W TEJ CHWILI** (porównanie z „dziś rano checked=15/would_alert_stale=0/no_threshold=0" — zgodne).
- **Odtworzenie stanu SPRZED FALA-1 (syntetyczny ledger, żywy host):** 3 zdrowe oneshoty zamrożone jako failed + cod-weekly naprawdę failed:
  - BEZ systemd (failure-only ledger): `would_alert_stale=3` ← **3 FAŁSZYWE alerty STALE = dokładnie motyw #2**.
  - Z systemd: `would_alert_stale=0 systemd_rescued=3` (3× `systemd=healthy/inactive RESCUED`). **Fix eliminuje całą klasę.**
- **Bezpieczeństwo (nie tłumię realnych awarii):** cod-weekly naprawdę failed + naprawdę stale (300h>192h) → `would_alert_stale=1`, `systemd=unhealthy/failed`, **nie rescued** → alertuje.
- `systemd_probe` na żywo: shadow(long-running)→healthy+fresh; retro-learning(oneshot)→healthy fresh_ts=04:30; cod-weekly→healthy=False; nieistniejąca→available=False/healthy=None (LoadState-guard działa).

**Liczby przed/po (na odtworzonym stanie sprzed FALA-1, 4 jednostki):**

| | would_alert_stale (ledger) | would_alert_stale (finalny) | systemd_rescued |
|---|---|---|---|
| BEFORE (systemd OFF) | 3 | 3 | 0 |
| AFTER (systemd ON) | 3 | **0** | **3** |

**PEŁNA REGRESJA (kanoniczna kopia `dispatch_v2/`, `PYTHONPATH` na kopię):**
- **`3919 passed, 23 skipped, 11 xfailed, 0 failed`** (107.75s).
- Baseline: `3907 passed / 0 failed / 23 skipped / 11 xfailed`. **Delta = +12 passed (moje testy), ZERO nowych FAILi, identyczne skipped/xfailed.**
- `py_compile observability/cron_health.py` OK.

## 5. DEPLOY-ZA-ACK (wykonuje KOORDYNATOR, off-peak)

1. Merge `fix/cron-health` do kanonu (review diff → `py_compile` → **pełna regresja z KANONU** po `git worktree remove`).
2. **Bez restartu, bez daemon-reload, bez Telegrama.** `cron_health` jest importowany świeżo przez `watchdog`/oneshoty per bieg → wejdzie po merge automatycznie (watchdog co 4h; następny bieg czyta nowy kod). Zachowanie ON domyślnie w produkcji (kill-switch niżej).
3. (Opcjonalnie, nadzorowany dowód po merge) `PYTHONPATH=/root/.openclaw/workspace/scripts /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.observability.cron_health --dry-run` → potwierdź liczby + kolumnę `systemd=`.

**Brak brakujących drop-inów / nic do zainstalowania w `deploy_staging/` z tego lane'u** (sekcja 3).

## 6. ROLLBACK

- **Kill-switch bez rewertu (hot, per-proces):** ustaw `CRON_HEALTH_SYSTEMD_TRUTH=0` w środowisku `dispatch-watchdog` (drop-in `Environment=`) → cross-check off → zachowanie failure-only-ledger (stan sprzed). ⚠ dodanie env do unitu = daemon-reload (koordynator, ACK).
- **Kod:** `git revert` commita cron_health — czysto (dodaje probe/CLI + opcjonalny kwarg + rozszerza scan_stale o pola; istniejące ścieżki werdyktu zachowane, `is_stale` degraduje do `_is_stale_ledger`).
- **Fail-safe wbudowany:** brak/timeout systemctl → automatyczny powrót do ledgera (bez akcji).

## 7. RYZYKA / POZA PARTYCJĄ

- **Zmiana zachowania żywego monitora** (watchdog): tłumi FAŁSZYWE STALE gdy systemd potwierdza zdrowie. Nie tłumi realnych (dowód sekcja 4). To pożądany efekt motywu #2, ale = zmiana ścieżki alertu → merge traktować jak deploy (C2), off-peak.
- **`monitor-419` rozjazd typu** (long_running w ledgerze vs `cron_timer 24h` w `_UNIT_METADATA`, `enabled=disabled`) — poza partycją, zgłoszone już przez FALA-1 §6. Mój cross-check go nie dotyczy (long_running pomijany w is_stale).
- **`--systemd-probe`/`--dry-run` kolumny** — diagnostyka, zero wpływu na decyzje.
- **Higiena testów w worktree** — `conftest.py` pinuje kanon (C12(e)); regresję robiłem na kopii w układzie `dispatch_v2/` (nazwa katalogu poprawna → testy `parents[2]` przechodzą natywnie, w przeciwieństwie do `wt-*`). Koordynator scala do kanonu i tam `pytest tests/` jest wiarygodny natywnie.

## 8. PLIKI

- Kod: `observability/cron_health.py`
- Testy: `tests/test_cron_health_systemd_truth.py` (NOWY, 12) + istniejące `tests/test_cron_health_{registrations,watchdog}.py` (bez zmian, przechodzą na moim kodzie)
- Raport: `eod_drafts/2026-07-02/cron-health_raport.md`
- `deploy_staging/`: NIC nowego (brak brakujących drop-inów — sekcja 3).
