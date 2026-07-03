# 04 — TESTY I URUCHAMIALNOŚĆ (Agent E)

**Data:** 2026-07-03 (~11:30–12:00 UTC) · **Zakres:** jak system startuje (systemd/env), inwentarz testów, PEŁNA regresja, pokrycie/strażniki · **Metoda:** READ-ONLY + jedyny „bieg" = `pytest` z kanonicznej ścieżki. Repo żywe: `/root/.openclaw/workspace/scripts/dispatch_v2`. Usługi NIE dotykane (tylko `status/show/list-timers/journalctl`).

> **UCZCIWOŚĆ NA WSTĘPIE (kluczowe):** regresję puściłem **dwa razy**. Pierwszy bieg `python3 -m pytest` (systemowy `/usr/bin/python3`) dał **123 failed** — to była **MOJA pomyłka interpretera**, nie regres: systemowy python NIE ma `ortools` (wszystkie 123 to `ModuleNotFoundError: No module named 'ortools'` w `tsp_solver.py:86`). Poprawny bieg **venv** (`/root/.openclaw/venvs/dispatch/bin/python`, tego używają WSZYSTKIE usługi i inne sesje) = **4109 passed / 0 failed**. Werdykt = TYLKO drugi bieg.

---

## 1. JAK URUCHAMIA SIĘ SYSTEM

### 1a. Wymagania środowiska (zweryfikowane dziś)
| Element | Stan | Uwaga |
|---|---|---|
| **Interpreter** | `/root/.openclaw/venvs/dispatch/bin/python` (3.12.3) | ⚠ **NIE `python3`** — systemowy `/usr/bin/python3` ma pytest i większość zależności, ale **brak `ortools`** (tylko venv). Każdy `ExecStart=` to venv. |
| OSRM `:5001` | 🟢 LIVE | `route/v1/driving` → `{"code":"Ok",...}` odpowiada. `0.0.0.0:5001`. |
| `flags.json` | 🟢 `/root/.openclaw/workspace/scripts/flags.json` | 21.6 KB, mtime dziś 01:20. Hot-reload przez `C.flag()`. |
| **Stan runtime** | 🟢 `/root/.openclaw/workspace/dispatch_state/` (POZA gitem) | ⚠ **NIE** repo `dispatch_v2/dispatch_state/` (to tylko `epaka_data/`). Guardy hardcodują abs. ścieżkę workspace (`carried_first_guard.py:29`). Tu szukać jsonl planów/guardów/shadow. |
| `.secrets/*.env` | obecne (nie czytałem wartości) | `nadajesz_admin`, `nadajesz_parcel_admin`, `gmaps`, `assistant_*`. |
| Porty | `:5001` OSRM · `:8767` courier-api · `:8765` legacy traccar · `:8888` health/parser (localhost) | |
| Konwencja pytest | conftest pinuje `_SCRIPTS_ROOT` na kanon; od 03.07 `DISPATCH_UNDER_PYTEST=1` blokuje pisanie testów do PROD-logów | |

### 1b. Usługi długobieżne (daemony) — 5, wszystkie `active/running`
| Unit | ExecStart (moduł) | enabled |
|---|---|---|
| dispatch-shadow | `python -m dispatch_v2.shadow_dispatcher` | enabled |
| dispatch-gps | `python -m dispatch_v2.gps_server` | enabled |
| dispatch-panel-watcher | `python -m dispatch_v2.panel_watcher` | enabled |
| dispatch-sla-tracker | `python -m dispatch_v2.sla_tracker` | enabled |
| dispatch-monitor-419 | `python -m dispatch_v2.monitoring.detector_419` | disabled (running) |

### 1c. Timery — mapa wg kadencji (95 plików unit `dispatch-*`/`gps-*` łącznie; ~60 timerów `enabled/active`)
| Kadencja | Reprezentatywne jednostki (ExecStart = `python -m dispatch_v2.…`) |
|---|---|
| **10 s** | parcel-merge (`parcel_lane_merge`) |
| **1 min** | czasowka (`czasowka_scheduler`) · pending-pool · pending-resweep-shadow · postpone-sweeper |
| **~2 min** | liveness-probe · fleet-position-snapshot |
| **3 min** | **carried-first-guard** (`tools.carried_first_guard`) · **pickup-floor-guard** (`tools.pickup_floor_guard`) · reassign-global-select (`tools.reassignment_global_select`) · reassignment-shadow · ziomek-pred-calibration · plan-recheck · shadow-enrichment · courier-gps-commitment-shadow · downstream-crosscheck · b-route-shadow · bundle-calib-shadow · pickup-lateness-shadow · address-pin-aggregator · drtusz-bridge · papu-bridge |
| **~5–10 min** | data-alerts · delivered-integrity · state-panel-monitor · objm-lexr6-canary-monitor |
| **30 min** | state-reconcile · eta-calibration · decision-outcomes · new-courier-watch · ground-truth-gc |
| **~godzina** | dispatch-watchdog (`observability.watchdog`, next 12:00) |
| **Dobowe** | daily-rule-report (21:30) · pickup-slip-monitor (22:30) · r04-evaluator (01:00) · restic-backup (01:31) · koord-cascade / state-snapshot / log-rotation (03:00) · orders-state-prune / event-bus-cleanup / faza7-kpi / overrides-reset (03:30–04:00) · retro-learning / freshness-shadow / prep-bias-shadow-monitor / proposal-churn (04:30–05:15) · later-promises-monitor · daily-accounting · gps-delivery-validation |
| **Tygodniowe** | cod-weekly-preflight (nd) · cod-weekly-lastcall · cod-weekly · cod-panel-ingest (pon) · pickup-slip-review (sob) · gps-commitment-shadow-report (pt) |

⚠ **1 unit `failed`: `dispatch-cod-weekly.service`** — „scrape panel + batch write Google Sheets", ostatni bieg pon 06-29 06:00 (znany problem gspread/Sheets, tygodniowy). Reszta usług czysta.

### 1d. Przepisy — jak uruchomić ręcznie (venv!)
```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2
VP=/root/.openclaw/venvs/dispatch/bin/python
# 1) PEŁNA regresja (kanon)                → $VP -m pytest tests/ -q
# 2) Silnik/tick propozycji (daemon)       → $VP -m dispatch_v2.shadow_dispatcher   (LIVE=systemd)
# 3) Replay nieudanych                     → $VP replay_failed.py --oid <ID> | --status failed --since <ISO>
# 4) Strażnik carried-first (one-shot)     → $VP -m dispatch_v2.tools.carried_first_guard
# 5) Miernik entropii (8 metryk)           → $VP -m dispatch_v2.tools.entropy_dashboard
# 6) Fingerprint flag (drift, CLI)         → $VP -m dispatch_v2.tools.flag_fingerprint_check --jsonl <P>
# 7) Daily accounting                      → $VP -m dispatch_v2.daily_accounting.main [--dry-run] [--target-date]
```

---

## 2. INWENTARZ TESTÓW

- **Kolekcja (dziś, venv):** **4140 testów** w 2.24 s (`--collect-only -q`).
- **Pliki:** **458** `test_*.py` w `tests/` + podkatalogi `fixtures/`, `golden/`. `conftest.py` obecny (izolacja flag `_isolate_flags_json`, guard telegramu, pin `_SCRIPTS_ROOT`).
- **Testy POZA `tests/`:** `daily_accounting/tests/` (4 pliki: car_lookup, eljot_exception, grid_capacity, bucket_logic) — moduł izolowany.
- **Grupy tematyczne (prefiks, top):** v328 (20), b2 (14), v326 (9), v327 (8), czasowka (8), auto (8), route (7), pending (7), obj/objm (13), panel (6), gps/courier/cod/bug (5 każda), sla/shadow/pickup/proposal (4).
- **Markery:** `xfail` w 4 plikach żywych (`test_invariant_slots_l04`, `test_demote_tier_bucket_p4`, `test_feasibility_guards_behavioral`, `test_obj_food_age_bug5`) → **11 xfailed w biegu**. `skip/skipif` w ~18 plikach (środowiskowe: gspread, ML, property-based, hermetyzacja).
- **L0 „xfail-ratchet" inwariantów — `tests/test_invariant_slots_l04.py` (5 slotów, `xfail(strict=True)`):** nazwany dług; XPASS = FAIL zmusza autora naprawy do zdjęcia markera. Sloty:

| Slot | Inwariant | Co jest łamane (dziś CZERWONE=xfail) |
|---|---|---|
| 1 | INV-SRC-EQUAL-TREATMENT | `pre_shift` inaczej traktowany w bliźniaku `reassignment_forward_shadow._SYNTH_POS` niż w kanonie `_selection_bucket` |
| 2 | INV-LIFE-LOADPLAN-PURE | `load_plan` default `invalidate_on_mismatch=True` = read-with-side-effect (persystuje invalidację przy odczycie) |
| 3 | INV-SRC-LEXQUAL | zamrożony cień `_objm_lexr6_shadow` (3-krotka) ≠ kanon `objm_lexr6.lex_qual` (4-krotka post-shift) → inny zwycięzca |
| 4 | INV-COH-R-DECLARED | brak tripwire `czas_kuriera ≥ czas_odbioru_timestamp` (grep strażników = 0) |
| 5 | INV-LAYER-HARD-BEFORE-SOFT | `_assert_feasibility_first` tylko 1 call-site, brak re-assertu na EMIT po `FEAS_CARRY_READMIT` |

---

## 3. PEŁNA REGRESJA — WYNIK

**Bieg kanoniczny (venv), `DISPATCH_UNDER_PYTEST=1`, `nice/ionice`:**

| Metryka | Dziś (venv) | Baseline | Werdykt |
|---|---|---|---|
| passed | **4109** | 4109 (commit `d2a7ae6`: „regresja 4109/0"); 4064 = stan 02.07 sprzed +45 testów 03.07 | ✅ zgodny |
| **failed** | **0** | 0 | ✅ **ZERO regresji** |
| skipped | 23 | — | środowiskowe |
| xfailed | 11 | 11 | ✅ ratchety trzymają |
| czas | **122.99 s (2:02)** | — | |

- Log: `/tmp/audyt_pytest_venv.log`. Suma wykonań 4109+23+11 = 4143 ≈ 4140 kolekcji.
- **Zero flaky** w tym biegu (znany 1 flaky z historii się nie ujawnił).
- Bieg-pomyłka (systemowy python, 123×`ortools` ModuleNotFound): `/tmp/audyt_pytest.log` — **zignorować, to nie regres**.
- **Współbieżność:** równolegle biegły 2–3 inne suity (sesje `wt-frozenobj`/`wt-fingerprint` z własnym `ZIOMEK_SCRIPTS_ROOT`=pkgroot — NIE dotykają kanonu). Mój venv-bieg = 0 failed mimo to → potwierdza brak kolizji stanu.

---

## 4. POKRYCIE I STRAŻNIKI

### 4a. Obszary silnika bez strażnika (potwierdzone)
Kanon `ZIOMEK_INVARIANTS.md` + `eod_drafts/2026-06-30/FAZA1_06_ledger_pokrycia.md` (ledger klasa×moduł, C11):
- **ALOKACJA/FEASIBILITY = 🔴 SLOT** — dług egzekwowania skupiony w kontraktach ①②③ (jedno-źródło / warstwy / bliźniaki): **12 z 21 slotów**. Klasa DANE/SENTINELE gęsto obstawiona (10× 🟢 TEST), alokacja słabo.
- Dashboard `ZIOMEK_INVARIANTS.md`: **~19 ✅RT/🟢TEST · 4 ⚠️VOID · 21 🔴SLOT**.
- ⚠️ **VOID (przyrząd kłamie — gorszy niż brak):** `carried_first_guard` (dokument: pusty env→fikcje `no_position`), `global_allocate` geometryczny (ślepy na spread), serializer gubił 38 kluczy (`eta_source`/`r6_*`). *Uwaga: część READ-side naprawiona L1.2 02.07; formalne zdjęcie VOID = re-oracle przy użyciu.*

### 4b. Inwarianty vs test (z `ZIOMEK_INVARIANTS.md`)
- **🟢 mają żywy zielony test:** INV-FEAS-SHIFT-END, INV-SEL-MULT-SIGN, INV-LIFE-ZOMBIE, INV-LIFE-INACTIVE, INV-STATE-GT-RECONCILE, INV-VERDICT-CLASSIFIED, cały klaster POS/STATE (10×), INV-COORD-SENTINEL-INGEST (L2.1).
- **🔴 CZERWONE/xfail (5 slotów L0.4)** — patrz tabela §2 (kontrakty ①②⑦⑧).
- **🔴 SLOT pusty (brak strażnika):** INV-SRC-ROUTE-ORDER (⏰ deadline 07-10, golden harness L6.A go łata), INV-SRC-AVAILABLE-FROM, INV-FEAS-PICKUP-FLOOR (żywy timer istnieje, brak testu-strażnika w CI), INV-FEAS-NO-DOUBLE-BOOK, INV-COH-CLAMP-CHOKEPOINT.

### 4c. Strażniki RUNTIME („testy na żywo") — tabela stanu DZIŚ
| Strażnik (timer) | Co pilnuje | Ostatni bieg | Zielony? |
|---|---|---|---|
| **carried_first_guard** (3 min) | kolejność kanon-z-pozycją vs żywa (carried-first/no-return) | `carried_first_guard.jsonl` **11:36:33** dziś | 🟢 `kind:ok risk:false`, `pos_source:last_event` (realny, NIE fikcja `no_position`) |
| **pickup_floor_guard** (3 min) | `pickup_eta ≥ max(now,shift_start)` na propozycjach/planach | `pickup_floor_guard.jsonl` **11:36:33** dziś | 🟢 `viol_proposal:0 viol_plan:0 viol_recheck_leak:0` (5 plans shift_start_unknown, 11 committed-skip) |
| **dispatch-watchdog** (~godz) | cron-health / stale services (MP-#4) | journal **08:01:26** dziś | 🟢 `checked=15 stale=0 alerted=0` (next 12:00) |
| flag_fingerprint_check | dryf flag KANON vs env/drop-in | **CLI on-demand**, NIE timer | n/d (nie żywy tripwire — przyrząd manualny) |
| pending-resweep-watchdog (timer) | zdrowie resweep | `active/elapsed` | zaplanowany |

---

## 5. ⚠ DO WYJAŚNIENIA

1. **Interpreter jako mina środowiskowa.** `python3` ≠ venv (brak `ortools` w systemowym) → naiwny `python3 -m pytest` = 123 fałszywe faile. Warto by CLAUDE.md/README testów krzyczał „ZAWSZE venv" (jest wzmianka, ale łatwo przeoczyć). Zweryfikowane biegiem: `ortools` tylko w `venvs/dispatch`.
2. **`dispatch-cod-weekly.service` = failed.** Nie badałem przyczyny (poza zakresem/READ-ONLY) — prawdopodobnie znany gspread/Sheets; do potwierdzenia przez właściciela COD.
3. **Izolacja PROD-logów pod pytest — niepełna weryfikacja.** Ustawiłem `DISPATCH_UNDER_PYTEST=1`; guardy jsonl (`carried_first_guard`/`pickup_floor_guard`) mają mtime z ŻYWEGO timera 11:36 (co 3 min), więc nie da się czysto rozdzielić czy mój bieg też pisał. Nie zaobserwowałem dowodu zapisu testów do PROD, ale nie mogę tego twardo potwierdzić.
4. **Baseline „4064/0" nieaktualny.** Bieżący = **4109/0** (git log `d2a7ae6`, +~45 testów z prac 03.07). Reprodukcja co do liczby.
5. **VOID vs bieżący stan.** `ZIOMEK_INVARIANTS.md` (DRAFT 07-01) mówi `carried_first_guard`=VOID, ale ŻYWY rekord dziś ma `pos_source:last_event` (realny) — sugeruje, że drop-in env-parity (01.07) działa. Rozjazd doc↔runtime = do reconcyliacji przy re-oracle (nie weryfikowałem replayem historii, tylko ostatni rekord).
6. **Współbieżne suity innych sesji** mogły marginalnie wydłużyć mój czas (2:02) — nie wpłynęły na wynik (0 failed), bo biegną z izolowanych pkgroot.

---
**Zweryfikowane DZISIEJSZYM biegiem:** kolekcja 4140 · regresja venv 4109/0/23skip/11xfail · OSRM :5001 live · guardy carried_first+pickup_floor+watchdog świeże/zielone · 5 daemonów running · cod-weekly failed · 5 slotów L0.4 xfail. **Tylko deklaracja z dokumentów (NIE zweryfikowane biegiem):** liczby VOID/oracle Fazy 1, „12 z 21 slotów", historia carried_first VOID.
