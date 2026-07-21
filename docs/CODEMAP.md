# CODEMAP — spis treści repo `dispatch_v2` (Ziomek)

**STATUS:** żywy · **Data:** 2026-07-21 (decision-time ETA log; audyt parytetu docs↔repo 2026-07-18) · **Autor:** Agent H + Codex.
**Cel:** mapa do szybkiego czytania — nowa sesja NIE musi skanować repo. Numery linii celowo pominięte (dryfują — **grepuj symbol**, nie linię). Ścieżki relatywne od korzenia repo `dispatch_v2/`; `../` = sąsiad w `scripts/`; stan/logi/pamięć = ścieżki absolutne (poza repo).
**Aktualizuj** gdy dochodzi/znika katalog lub kluczowy plik korzenia (patrz stopka). Kanon zachowania silnika = `ZIOMEK_ARCHITECTURE.md`; ten plik to TYLKO nawigacja.

---

## 1. Katalogi repo

| Katalog | Co robi | Kluczowe pliki (max 3) | Uwaga |
|---|---|---|---|
| `tools/` | Grab-bag: ~185 skryptów — monitory, werdykt-toole, jednorazowe audyty fal L0-L8 | `ledger_io.py`, `entropy_dashboard.py`, `carried_first_guard.py` | żywy + jednorazowce; ~19/49 przyrządów VOID (kłamie) |
| `tests/` | Główny pakiet regresji pytest (~5150+ testów; baseline 18.07: 5166/0) | `conftest.py`, `golden/`, `fixtures/` | ⚠ **TYLKO venv dispatch** (system python: 123 fałsz. faile) |
| `observability/` | „Oficjalne" monitory Fazy audytowej | `watchdog.py`, `data_alerts.py`, `cron_health.py` | żywy |
| `monitoring/` | 3 detektory (starsze, pre-audyt) | `detector_419.py`, `gps_feed_health.py`, `consumer_stuck_alert.py` | żywy; tematycznie nakłada się z `observability/` |
| `core/` | **Rdzeń decyzyjny silnika (refaktor 06.07)** + starsze utilsy event-busa | `decide.py` (fasada; klasa `WorldState` w osobnym `world_state.py`), `gates.py`, `candidates.py` (pętla per-kurier), `selection.py`, `scorer.py` (flaga OFF), `planner.py` (wspólna parametryzacja silnik↔plan_recheck, flaga OFF), `invariant_firewall.py` (718 l., sweep 17.07), `config_reload_subscriber.py` · starsze: `flags_io.py`, `jsonl_appender.py`, `broadcast_handlers.py` | żywy |
| `identity/` | **Tożsamość kuriera — rejestr kanoniczny CID (Sprint 4, 10.07)**: normalize/registry/collisions/report/onboarding; 2 legacy-resolvery odtworzone CELOWO 1:1 (unifikacja = Faza B) | `registry.py`, `report.py` (`--parity`), `onboarding.py` | żywy |
| `czasowka_proactive/` | Proaktywne harmonogramowanie czasówek | `evaluator.py`, `score_selector.py`, `state.py` | żywy (submoduł) |
| `cod_weekly/` | Tygodniowe rozliczenie COD → Google Sheets | (venv **sheets**) | żywy; ⚠ `dispatch-cod-weekly.service` pada co pon. |
| `daily_accounting/` | Rozliczenia dzienne + wypłaty kurierów | `main.py`, `tests/` (własny runner, NIE pytest) | żywy (venv sheets) |
| `shift_notifications/` | Worker powiadomień T-60/T-30 o zmianach (jednostki: `systemd/shift_notifications/`) | `worker.py` | żywy |
| `reconciliation/` | Worker rekoncyliacji stanu (jednostki: `systemd/reconciliation/`) | `reconcile_worker.py`, `auto_resync.py`, `phantom_detector.py` | żywy |
| `sms/` | Abstrakcja SMS (cykl importów ovh↔provider↔stub) | `ovh.py`, `provider.py`, `stub.py` | żywy |
| `telegram/` | ⚠ TYLKO szablony — **NIE bot** (bot = `telegram_approver.py` w korzeniu) | `templates.py` | żywy pakiet szablonów, myląca nazwa |
| `ml_data_prep/` | Offline pipeline LGBM two-model (arbitrage/bundle/solo/forward) | `*.pkl` (label_encoders — binaria w git) | żywy offline; „zero contact z live" |
| `config/` | Statyczna konfiguracja | `cities.json` | dane |
| `migrations/` | 3 migracje jednorazowe (05.05/05.07/10.07) | `event_retry_metadata.py` (inspect read-only; zapis tylko z `--apply`) | archiwum/ops |
| `dispatch_state/` | ⚠ **NIE stan silnika** — tylko dane epaki | `epaka_data/*.csv` | dane; kolizja nazw (patrz §4 pułapki) |
| `eod_drafts/` | Dzienniki „koniec dnia" — ~62 podkatalogi wg daty (raporty + skrypty + dane) | `2026-06-30/FAZA1_*`, `2026-07-02/AUDYT2/` | mieszane; ~32M, dane/jsonl churnują w git |
| `eod_drafts_a2/`, `audits/`, `ops/` | Dzienniki audytu A2 · artefakty audytów (`audits/2026-07-10` ≠ `docs/audyt/`) · ops/security | — | mieszane/archiwum |
| `docs/` | ŻYWA nawigacja + archiwum: `ARCHITECTURE.md`, `CODEMAP.md`, `HERMETIC_TESTS.md`, `decisions/` (ADR-001..008 + **ODR-001/002** decyzje właścicielskie 12.07), `audyt/`, `flags/`, `eta/`, `integracje/`, `pending-tests/`, `proposals/`, `runbooks/`, `deploy/` (kit HA-lite 21.06 — źródło żywego backup-sentinel!), `archive/` (kwiecień-maj + AUDIT_* + handoffy, od 03.07) | `CODEMAP.md`, `audyt/10-PLAN.md`, `archive/README.md` | żywy |
| `docs/archive/AUDIT_2026-05-07/` | Audyt architektury 07.05 (10 md, Tier A/B/C, top-20 ryzyk) | — | archiwum (przeniesiony z korzenia 03.07) |
| `docs/archive/AUDIT_2026-06-03/` | Audyt architektury 03.06 (3 md) | `ZIOMEK_AUDYT*`, `STATUS_ROADMAP*` | archiwum (przeniesiony 03.07) |
| `sprint2_analysis/` | Root-cause sprintu 2 (30.04, samodzielny) | — | ⚠ źródła USUNIĘTE z mastera 03.07 (`cbe566f`) — na dysku został pusty katalog z samym `__pycache__`; treść tylko w historii git |
| `systemd/` | Źródła jednostek: mirror dispatch-* + `reconciliation/` + `shift_notifications/` (od 03.07) | `README.md`, `*.service`, `*.timer` | żywy; staged kity: `deploy/`, `deploy_staging/`, `docs/deploy/` |
| `deploy/`, `deploy_staging/` | Jednostki „staged" (checkpoint-tz/reassignment/bundle-calib-shadow) | `README_INSTALL.md` | do weryfikacji: wdrożone czy martwe |

Pominięto szum: `.git`, `__pycache__`, `.pytest_cache`. ⚠ `.claude/skills/` to NIE szum: 3 kanoniczne skille (`ziomek-cto`, `run-dispatch-v2`, `ziomek-blind-review`) = domyślne narzędzia sesji od 17.07 (routing w `CLAUDE.md` START TUTAJ).

---

## 2. Kluczowe pliki korzenia (~35, rola z docstringu)

**Rdzeń silnika (pipeline `feasibility→scoring→selekcja→werdykt`):**
- `dispatch_pipeline.py` — per-order assessment (feasibility→scoring→rank→werdykt); **największy plik repo (~4629 l. po refaktorze 06.07** — rdzeń wyniesiony do `core/candidates.py` ~2307 l. + `core/selection.py` ~1233 l.; `_assess_order_impl` = orkiestrator ~550 l.); definicje selekcji `_selection_bucket`/`_best_effort_sort_key` nadal TUTAJ (core deleguje aliasami `_dp.*`, ADR-008)
- `common.py` — config/logger/paths/flagi (`C.flag()`), bbox geokodu, stałe R6; drugi największy (~4361 l., hub in-deg 85)
- `feasibility_v2.py` — check_feasibility_v2 (HARD, SLA-first, R6-breach 35/40 tier)
- `scoring.py` — score_candidate (~19 kar SOFT); `s_obciazenie`
- `route_simulator_v2.py` — Hybrid PDP-TSP (OR-Tools), hub in-deg 51; DWELL
- `tsp_solver.py` — solver OR-Tools (jedyny import ortools)
- `objm_lexr6.py` — selektor lex-helperów (bliźniak best-effort, canary `ENABLE_OBJM_LEXR6_SELECT`)
- `sla_anchor.py` — kotwica SLA (bliźniak feasibility+route_sim)
- `shadow_dispatcher.py` — **SILNIK**: pętla `_tick`/`run` (systemd `dispatch-shadow`); serializer `_serialize_result` → shadow log
- `decision_eta_log.py` — wspólny, fail-safe writer snapshotów ETA dokładnie w chwili finalnej decyzji/commitu planu; flaga `ENABLE_DECISION_ETA_LOG` default OFF
- `state_machine.py` — jedyne źródło prawdy o stanie zlecenia (upsert `orders_state`, 26 ścieżek); observer FSM Phase A log-only
- `order_fsm.py` — formalny validator cyklu życia + jawne wyjątki reconcile; w Phase A nie blokuje writera
- `plan_manager.py` — zapis/odczyt `courier_plans.json` (atomic); ładowanie planu
- `plan_recheck.py` — periodyczny re-canon kolejności (timer 5 min); `_apply_canon_order_invariants`
- `panel_watcher.py` — ingest z panelu gastro (event-driven poll); 4 handlery recanon
- `panel_client.py` — dostęp do `gastro.nadajesz.pl` (login/CSRF/edit; cykl z `panel_html_parser`)
- `courier_resolver.py` — snapshot floty GPS + fallback last-known-pos (no-GPS); `dispatchable_fleet`
- `osrm_client.py` — OSRM `:5001` przez stdlib urllib; haversine fail-loud
- `geocoding.py` — geokod (Nominatim) + cache; HARD bbox Białystok
- `chain_eta.py`, `calib_maps.py`, `live_eta_cache.py` — łańcuch ETA, mapy kalibracji, cache ETA
- `sla_tracker.py` — tracker SLA (daemon `dispatch-sla-tracker`); alerty R6 BAG_TIME
- `event_bus.py` — szyna zdarzeń (+`events.db`, GC `event_bus_cleanup.py`)
- `event_retry.py` — metadane/helpery retry i logicznego DLQ, automatyczny retry hard-OFF; ręczny operator: `replay_dead_letter.py`
- `wave_scoring.py` — scoring falowy (C5; ⚠ NIE modyfikować bez ACK)

**Autonomia / auto-assign (AUTON-01, uśpiony za `ENABLE_AUTO_ASSIGN`=OFF):**
- `auto_assign_gate.py` (cykl z pipeline), `auto_assign_executor.py` (dormant), `auto_koord.py`, `auto_proximity_classifier.py`, `coordinator_activations.py`, `coordinator_time_recheck.py`

**Monitory/health/shadow jako skrypty korzenia (NIE w `observability/`):**
- `parser_health.py` + `parser_health_layer3.py` + `parser_health_endpoint.py` (3-warstwowa rezyliencja parsera, `:8888/health/parser`), `parse_continuity_guard.py`, `courier_gps_commitment_shadow.py`, `pickup_lateness_shadow.py`, `eta_calibration_logger.py`, `eta_residual_infer.py`, `learning_analyzer.py`, `ml_inference.py`, `validation_gate_lgbm.py`, `replay_failed.py`

**Admin / bootstrap / jednorazowce:** `flags_admin.py`, `courier_admin.py`, `manual_overrides.py`, `new_courier_pairing.py`, `bootstrap_restaurants.py`, `prune_orders_state.py`, `gastro_edit.py`, `sync_courier_pay.py`

**Czasówki / uwagi (deadline z free-textu):** `czasowka_scheduler.py`, `czasowka_uwagi.py`, `uwagi_address_parser.py`, `address_mismatch.py`, `address_pin_memory.py`

**Paczki (parcel lane, sprint 29.06):** `parcel_assign.py`, `parcel_lane_merge.py`

**Bot:** `telegram_approver.py` (~4348 l., approver propozycji; ⚠ `dispatch-telegram` OFF od 26.06 — kanał człowieka = konsola)

---

## 3. GDZIE SZUKAĆ CZEGO (główny lookup)

| Temat | Plik(i) |
|---|---|
| Feasibility / HARD-check (R6 35/40) | `feasibility_v2.py` |
| Geokod HARD + bbox Białystok | `common.py` (bbox), `geocoding.py`, `osrm_client.py` |
| Scoring (~19 kar SOFT) | `scoring.py` + `dispatch_pipeline.py` |
| Selekcja / best-effort | `dispatch_pipeline.py` (`_selection_bucket`, `_best_effort_sort_key`) + bliźniak `objm_lexr6.py` |
| TSP / trasy / DWELL | `route_simulator_v2.py` + `tsp_solver.py` |
| Werdykt KOORD (best_effort_r6 / commit_divergence) | `dispatch_pipeline.py` + `shadow_dispatcher.py` |
| Plany / kanon kolejności / re-canon | `plan_manager.py` + `plan_recheck.py` (+4 handlery w `panel_watcher.py`) |
| Ingest z panelu gastro | `panel_watcher.py` + `panel_client.py` |
| Stan zleceń (źródło prawdy) | `state_machine.py` → `orders_state.json` (**workspace/dispatch_state/, POZA repo**) |
| Formalny FSM / obserwacja przejść | `order_fsm.py` + hook w `state_machine.py`; Phase A `observer=True`, `enforcement=False`, wyjątki reconcile wymagają jawnego source |
| Pozycje kurierów / no-GPS / last-known-pos | `courier_resolver.py` → `courier_last_pos.json` (workspace) |
| ETA / kalibracja | `chain_eta.py`, `eta_calibration_logger.py`, `calib_maps.py`, `live_eta_cache.py` |
| Decision-time ETA / dzienna coverage | `decision_eta_log.py` → `dispatch_state/decision_eta_log.jsonl`; bramka `tools/decision_eta_coverage.py` |
| Czasówki | `czasowka_scheduler.py` + `czasowka_proactive/` |
| Paczki (parcel lane) | `parcel_assign.py` + `parcel_lane_merge.py` |
| **Flagi silnika** | `flags.json` (`/root/.openclaw/workspace/scripts/flags.json`, hot-reload) + `common.flag()` |
| **Flagi panelu** | `../../nadajesz_clone/panel/backend/flags.systemd.env` ⚠ `systemctl show -p Environment` NIE pokazuje |
| Flagi apki | `../courier_api/config.py` defaults + drop-iny `.conf` |
| Autonomia / auto-assign (OFF) | `auto_assign_gate.py` + `auto_assign_executor.py` (`ENABLE_AUTO_ASSIGN`=OFF) |
| Event bus | `event_bus.py` + `core/jsonl_appender.py` (+`events.db`) |
| Retry / DLQ eventów | `event_retry.py` + `migrations/event_retry_metadata.py` + ręczne CLI `replay_dead_letter.py`; brak workera/timera i auto-retry |
| **Nagrywanie świata decyzji (LIVE)** | `world_record.py` (+recorder OSRM w `osrm_client.py`) → `dispatch_state/world_record/world_record-YYYYMMDD.jsonl` (retencja 14 d) |
| **Replay decyzji / bramka korpusowa** | `tools/world_replay.py` (1 decyzja, sandbox) + `tools/world_replay_gate.py` (korpus → `dispatch_state/world_replay_gate_verdict.txt`); **night-guard `dispatch-world-replay-gate.timer` LIVE od 06.07** (02:00 UTC, INFORMACYJNY — `ExecStart=-`, werdykt do pliku+`logs/world_replay_gate.log`, brak alertu; eskalacja na egzekwujący za ACK) |
| Efekty uboczne PO decyzji (LIVE) | `effects_buffer.py` (divert/flush; konsumenci: dispatch_pipeline, feasibility_v2) |
| Lint/typing ratchet (dev-only) | `tools/devlint/` (`ratchet_check.py`, venv `venvs/devlint`; polityka „nie gorzej") |
| Program refaktoru architektury | gałąź `refaktor/architektura` → `docs/refaktor/00-07` (raport końcowy `06-raport.md`) |
| Telegram bot (⚠ OFF od 26.06) | `telegram_approver.py` + `telegram/templates.py` |
| Testy | `tests/` — ⚠ **TYLKO** `venvs/dispatch/bin/python -m pytest` |
| Strażniki runtime | `tools/carried_first_guard.py` + `tools/pickup_floor_guard.py` + `observability/watchdog.py` |
| Werdykt-toole / ledger | `tools/ledger_io.py` (READ-kanon) → `shadow_decisions.jsonl` (**scripts/logs/**, NIE dispatch_state) |
| Entropia / miernik audytu | `tools/entropy_dashboard.py` (8 metryk) + `tools/flag_registry.py` + `docs/audyt/` |
| Kanon architektury / inwarianty / DoD | `ZIOMEK_ARCHITECTURE.md` + `ZIOMEK_INVARIANTS.md` + `ZIOMEK_DEFINITION_OF_DONE.md` |
| Protokół zmian (ETAP 0→7) | `/root/.claude/projects/-root/memory/ziomek-change-protocol.md` |
| Reguły biznesowe / priorytety | `memory/ZIOMEK_REGULY_KANON.md` + `memory/project_overview.md` |
| Historia decyzji (ADR) | `docs/decisions/` (tworzone w K2.3 audytu) |
| Mosty (papu / drtusz / epaka) | `../papu_dispatch_bridge/` + `../drtusz_bridge/` + `tools/epaka_fetcher.py` |
| Konsola koordynatora (gps.nadajesz.pl/admin) | `../../nadajesz_clone/panel/` (importuje `dispatch_v2` jako lib) |
| Apka kuriera (:8767) | `../courier_api/` (`courier_orders.py`, import `route_podjazdy`/`live_eta_cache`) |
| COD / rozliczenia | `cod_weekly/` + `daily_accounting/` (venv **sheets**) |
| SMS / powiadomienia zmian | `sms/` + `shift_notifications/` |
| Rekoncyliacja stanu | `reconciliation/` (`reconcile_worker`, `phantom_detector`) |
| Parser zdrowia (`:8888`) | `parser_health.py` (+`_layer3`/`_endpoint`) |

---

## 4. ⚠ Pułapki nawigacyjne (przeczytaj ZANIM zaufasz ścieżce)

1. **`dispatch_v2/dispatch_state/` ≠ żywy stan.** Zawiera TYLKO `epaka_data/`. Prawdziwy stan (orders_state, plany, shadow, ~1,1 GB) = `/root/.openclaw/workspace/dispatch_state/` (**POZA gitem**; `common.py` hardkoduje abs. ścieżkę).
2. **Logi rozdwojone.** `shadow_decisions.jsonl` fizycznie w `scripts/logs/`; reszta shadow-jsonl (`r6_breach`, `obj_replay`, `v319c_read`) w `workspace/dispatch_state/`.
3. **Pytest TYLKO w venv dispatch.** `/usr/bin/python3` nie ma `ortools` → 123 fałszywe faile (`ModuleNotFoundError`). Kanon: `/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/`.
4. **`EnvironmentFile` niewidoczny w `systemctl show -p Environment`** — flagi panelu (68, stan 18.07) siedzą w `flags.systemd.env`; sam `show` pokaże fałszywe OFF. Czytaj plik wprost.
5. **Kanon flag = 3 światy.** Silnik = `flags.json` (po migracji D3 02.07); panel = `flags.systemd.env`+inline `.conf`+`flags.py` defaults; apka = `.conf`+`config.py`. ⚠ Zapis „drop-iny NIE flags.json" (w `/root/CLAUDE.md`/`MEMORY.md`) jest NIEAKTUALNY dla silnika.
6. **Wiele CLAUDE.md w łańcuchu cwd.** Obowiązuje: **głowa `dispatch_v2/CLAUDE.md` (Przykazanie #0) + `/root/CLAUDE.md`**. NIEobowiązujące relikty routera aider: `workspace/CLAUDE.md`, ogon `dispatch_v2/CLAUDE.md` (~l.1624+), `/root/.claude/CLAUDE.md` (ruflo). Szczegóły: `docs/audyt/02-NIEZGODNOSCI.md §1a`.
7. **Jednostki systemd:** źródła w `systemd/` (+ podkatalogi per-moduł, patrz `systemd/README.md`); staged kity w `deploy/`, `deploy_staging/`, `docs/deploy/`. **Wdrożone = `/etc/systemd/system/`** — zawsze `systemctl cat` (bywają kopie i symlinki).
8. **`telegram/` to szablony, nie bot.** Bot = `telegram_approver.py` w korzeniu.
9. **Numery linii dryfują** (repo mutuje na żywo, auto-push co godzinę cronem). Zawsze **grepuj symbol**, nie ufaj `plik:linia` z docs/pamięci.

---

## 5. Indeks historii audytów

| Audyt | Gdzie | O czym |
|---|---|---|
| Architektura 07.05 | `docs/archive/AUDIT_2026-05-07/` (10 md) | mapa systemu, Tier A/B/C, top-20 ryzyk, 10 god-objects, maintainability 5/10 |
| Architektura 03.06 | `docs/archive/AUDIT_2026-06-03/` (3 md) | STATUS_ROADMAP + ZIOMEK_AUDYT + extract |
| Sprint 2 root-cause | historia git (`cbe566f^`) | 30.04-01.05, przestarzałe, self-contained |
| Faza 1 (spójność) | `eod_drafts/2026-06-30/FAZA1_00..06` | 26 rootów, 19/49 VOID przyrządów, roadmapa L0-L8, ledger pokrycia |
| Audyt 2.0 (niezawodność) | `eod_drafts/2026-07-02/AUDYT2/MASTER_synteza.md` | P0 security, regres perf 2×, 2 bomby TZ, martwe monitory |
| **TEN audyt (porządki+nawigacja)** | `docs/audyt/00-05` + `10-PLAN.md` | inwentaryzacja, zależności, dług, testy, niezgodności, inne projekty |

---

## 6. Jak aktualizować CODEMAP

- **Nowy katalog top-level w repo** → dopisz wiersz w §1 (co robi + max 3 pliki + uwaga żywy/archiwum/dane).
- **Nowy kluczowy plik korzenia** (importowany przez usługę/monitor) → dopisz do właściwej grupy w §2 (1 linia) i — jeśli wnosi nowy temat — do lookup §3.
- **Nowa pułapka / kolizja nazw / rozjazd doc↔kod** → §4.
- **Nowy audyt** → §5.
- Zasada: **1-3 linie na pozycję**, bez encyklopedii. Numerów linii NIE wpisuj (dryfują). Zmiana zachowania silnika → NIE tu, tylko protokół `ziomek-change-protocol.md`.
