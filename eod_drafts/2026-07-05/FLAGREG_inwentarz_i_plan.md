# P-FLAGREG (Sprint 2.5-PREP, tmux 18) — inwentarz flag poza rejestrem + plan dorejestrowania + lista GC

> 2026-07-05 ~18:05 UTC. Zadanie 2 handoffu `SPRINT25_PREP_HANDOFF_tmux18.md`. READ-ONLY etap (inwentarz);
> edycje rejestrów = osobne commity partiami w worktree (sekcja PLAN). Metoda: grep `decision_flag(`/`flag("` po
> żywym kodzie dispatch_v2 (bez .bak/wt-*/eod_drafts) + flags.json + ETAP4/NUMERIC/FP_EXTRA z common.py.
> Skrypt klasyfikatora: scratchpad sesji (`flagreg_classify.py`) — deterministyczny, do wglądu na życzenie.

## 1. REKONCYLIACJA LICZB (dashboard „112 + 5 dead" z 30.06 vs żywo 05.07)

| oś | 30.06 (FAZA1/B06) | żywo 05.07 | komentarz |
|---|---|---|---|
| flagi czytane w kodzie POZA `ETAP4_DECISION_FLAGS` | 112 | **114** (grep szerszy o artefakty testowe; bez nich ~105) | ETAP4 urósł 59→**100** (L0.1-min `a839a02` + kolejne fale), ale przybyły nowe flagi |
| dead-flag | 5 | **3 realne do GC** (+3 skeleton) | 2 z 5 skasowane 01.07 (D.4 `a839a02`): `ENABLE_PANEL_IS_FREE_AUTHORITATIVE`, `ENABLE_TRANSPARENCY_SCORING`; `ENABLE_SPEED_TIER_LOADING_PLANNED` już NIE istnieje w kodzie |
| sieroty flags.json (C-ORPHAN) | — | **0** (`flag_hygiene_check`) | domknięte |
| doc-coverage ref (C-FLAG-DRIFT) | baseline 70 | **77 niedok. / 157** (51,0%), baseline=77, dryf 0 | ratchet zielony; kurczenie baseline = cel |
| fingerprint | 63/≥90 | **102-104 per proces**, 5 rozjazdów (2 stale-process po at-202/203 — restart zrekoncyliuje; 3 JSON-DRIFT benign) | `flag_fingerprint_check` |

**Wniosek:** dashboard `ZIOMEK_INVARIANTS.md` (wiersz INV-FLAG-REGISTRY „112+5") jest opóźniony wobec żywych checkerów.
Wiersz aktualizuje sesja właścicielska INVARIANTS (tmux 17) — tu tylko odnotowane, pliku NIE dotykam (granica sesji).

## 2. KLASYFIKACJA 114 FLAG POZA ETAP4 (pełna tabela w §5)

| klasa | n | co z nią zrobić |
|---|---|---|
| **DECYZYJNA-KANDYDAT-ETAP4** | 29 | dorejestrować do `ETAP4_DECISION_FLAGS` + stała-fallback = steady-state (wzorzec L0.1-min); partiami z pełną regresją |
| **SHADOW-OBS** (metryki/shadow) | 19 | dorejestrować do `_FINGERPRINT_EXTRA_FLAGS` (observability; NIE sterują decyzją) |
| **ALERT/NOTIFY** | 9 | jw. `_FINGERPRINT_EXTRA_FLAGS` |
| **NUMERIC / PARAM** | 31 | boolowskie parametry service-scoped → zostają (rejestr = doc-ref w §5); numeryczne z flags.json → `FLAGS_JSON_NUMERIC_OVERRIDES` tylko gdy brak (weryfikacja per flaga) |
| **SERVICE-SCOPED** (1 unit, brak cross-proces) | 17 | NIE do ETAP4 (hot-reload cross-proces zbędny — precedens: werdykty service-scoped w `flag_registry.py`); rejestr = doc |
| **TEST-ARTEFAKT / TOOLS-ONLY** | 9 | wykluczyć z osi INV (nazwy czytane tylko w tests/tools); `A4_TEST_FLAG` w prod flags.json → GC za ACK |

⚠ **Czego świadomie NIE robię (poza zakresem / za ACK):**
- **D.3 (migracja env-frozen→flags.json)** — ODŁOŻONE per L0.1 (osobny pod-ACK, większy blast: para OR_TOOLS↔GROUPING sprzężona, USE_V2_PARSER reachability). Nie dodaję ŻADNYCH kluczy do flags.json (zakaz handoffu).
- Dorejestrowanie do ETAP4 **nie zmienia produkcji** (`decision_flag` zawsze: flags.json → stała modułu; ETAP4 = fingerprint + conftest-strip w testach + checkery). Zmienia SEMANTYKĘ TESTÓW (strip klucza) → stąd pełna regresja per partia i stałe-fallback ustawiane na steady-state (mina klasy COMMIT_DIVERGENCE: const≠json flipuje po cichu przy utracie klucza).

## 3. LISTA GC (dowody śmierci zweryfikowane 05.07 na żywym kodzie) — **KASOWANIE ZA ACK ADRIANA**

| # | flaga | dowód (grep 05.07, bez tests/.bak/wt-*) | proponowane GC |
|---|---|---|---|
| 1 | `ENABLE_CLUSTER_DROP_GROUPING_METRIC` | tylko własna definicja `common.py:3121` — 0 konsumentów, cross-repo 0 | usunąć definicję z common.py |
| 2 | `A4_TEST_FLAG` | w PROD flags.json (=False); czytana wyłącznie w `tests/test_a4_config_reload_pubsub.py` (+lista strip-guard) | usunąć klucz z flags.json (⚠ zmiana flags.json = wyłącznie Adrian/za ACK) + przepisać test na tmp-plik |
| 3 | `ENABLE_SPEED_TIER_LOADING_PLANNED` | 0 wystąpień w kodzie — JUŻ skasowana wcześniej | nic; skorygować dashboard |
| S1 | `ENABLE_MID_TRIP_PICKUP` (skeleton C6) | wpięta `commitment_emitter.py:94`, stała False, brak w flags.json → nieosiągalna hot | decyzja produktowa: usunąć skeleton C6 albo zostawić świadomie (backlog F2.2) |
| S2 | `ENABLE_PENDING_QUEUE_VIEW` (skeleton C7) | wpięta `pending_queue_provider.py:56/89` + `dispatch_pipeline.py:3372`, stała False | jw. |
| S3 | `DEPRECATE_LEGACY_HARD_GATES` (double-dead) | `scoring.py:228` + `feasibility_v2.py:1123`; stała False + martwy kwarg | ⛔ scoring.py = teren tmux 17 (S1) — GC dopiero PO zakończeniu S1, za ACK |

## 4. PLAN PARTII (edycje = worktree `wt-flagreg`, commity po jawnych ścieżkach, pełna regresja per partia)

1. **Partia A (29 kandydatów ETAP4):** dopisać do `ETAP4_DECISION_FLAGS` + stałe-fallback = wartość steady-state z flags.json (gdzie klucz istnieje) / obecny default (gdzie env-only). Wyjątki do ręcznego werdyktu w partii: `ENABLE_GRAFIK_ENTRY_SALVAGE` (czytelnik w deploy_staging), `RECONCILIATION_ENABLED`/`NEW_COURIER_AUTOPAIR_ENABLED`/`PARSE_CONTINUITY_GUARD_ENABLED` (de facto 1-serwisowe → możliwy werdykt service-scoped zamiast ETAP4).
2. **Partia B (19 SHADOW-OBS + 9 ALERT):** dopisać do `_FINGERPRINT_EXTRA_FLAGS` (rosnie fingerprint → restart procesów NIE wymagany, fingerprint-check pokaże COVERAGE-GAP do najbliższego restartu — znany, benign wzorzec jak przy at-202/203).
3. **Partia C (NUMERIC):** te z flags.json a spoza `FLAGS_JSON_NUMERIC_OVERRIDES` → dopisać do krotki (data-only; conftest wycina numeryczne? — zweryfikować przed commitem, jeśli strip obejmuje → regresja).
4. **Partia D (doc):** wpisy doc-ref w `ZIOMEK_LOGIC_REFERENCE.md` dla flag z §5 z „doc-ref=NIE" + sprzątnięcie `flag_doc_baseline.json` (stale wpisy); ratchet `flag_doc_coverage_check` po każdej partii.
5. Po partiach: re-run `flag_registry.py` / `flag_fingerprint_check.py` / `flag_doc_coverage_check.py` / `flag_effect_coverage_check.py` + pełny pytest vs baseline 4109/0; wpis w trackerze.

## 5. PEŁNA TABELA KLASYFIKACJI (114 flag, stan 05.07 ~18:00 UTC)

(kolumny: flaga | klasa | wartość w flags.json [— = brak klucza] | już w _FINGERPRINT_EXTRA | doc-ref w ZIOMEK_LOGIC_REFERENCE | czytelnicy prod max 3)

| flaga | klasa | flags.json | fp-extra | doc-ref | czytelnicy prod (max 3) |
|---|---|---|---|---|---|
| CZASOWKA_PROACTIVE_ENABLED | DECYZYJNA-KANDYDAT-ETAP4 | False |  | NIE | czasowka_proactive/evaluator.py:242; czasowka_proactive/handlers.py:185; czasowka_proactive/handlers.py:371 |
| ENABLE_CZASOWKA_CK_PASSIVE_GUARD | DECYZYJNA-KANDYDAT-ETAP4 | True |  | tak | panel_watcher.py:852; state_machine.py:783; state_machine.py:828 |
| ENABLE_DRIVE_SPEED_TIER_CORRECTION | DECYZYJNA-KANDYDAT-ETAP4 | False |  | tak | common.py:2492 |
| ENABLE_ELASTYK_CK_NO_BACKWARD | DECYZYJNA-KANDYDAT-ETAP4 | True |  | tak | panel_watcher.py:866; state_machine.py:842 |
| ENABLE_FIRMOWE_REJECT_ON_GEOCODE_FAIL | DECYZYJNA-KANDYDAT-ETAP4 | — |  | NIE | panel_watcher.py:1254; panel_watcher.py:1290; czasowka_scheduler.py:282 |
| ENABLE_GEOCODE_NEGATIVE_CACHE | DECYZYJNA-KANDYDAT-ETAP4 | — |  | NIE | geocoding.py:214 |
| ENABLE_GEOCODE_NOMINATIM_FALLBACK | DECYZYJNA-KANDYDAT-ETAP4 | True |  | NIE | geocoding.py:518 |
| ENABLE_GEOCODE_VERIFICATION_ENFORCE | DECYZYJNA-KANDYDAT-ETAP4 | True |  | NIE | geocoding.py:577 |
| ENABLE_GEOCODING_AUDIT_LOG | DECYZYJNA-KANDYDAT-ETAP4 | — |  | NIE | geocoding_audit.py:39 |
| ENABLE_GPS_ACCURACY_TELEPORT_FILTER | DECYZYJNA-KANDYDAT-ETAP4 | — |  | NIE | courier_resolver.py:813 |
| ENABLE_GRAFIK_ENTRY_SALVAGE | DECYZYJNA-KANDYDAT-ETAP4 | True |  | tak | deploy_staging/scripts/fetch_schedule.py:138 |
| ENABLE_GRAFIK_FULL_NAMES_SOURCE | DECYZYJNA-KANDYDAT-ETAP4 | — |  | NIE | courier_resolver.py:466 |
| ENABLE_LGBM_PRIMARY | DECYZYJNA-KANDYDAT-ETAP4 | — |  | tak | ml_inference.py:850 |
| ENABLE_ORDERS_STATE_PRUNE | DECYZYJNA-KANDYDAT-ETAP4 | True |  | NIE | prune_orders_state.py:37 |
| ENABLE_OSRM_TABLE_CELL_CACHE | DECYZYJNA-KANDYDAT-ETAP4 | True |  | NIE | osrm_client.py:474 |
| ENABLE_PANEL_DETAIL_PREFETCH | DECYZYJNA-KANDYDAT-ETAP4 | True |  | NIE | panel_detail_prefetch.py:127 |
| ENABLE_PANEL_PACKS_CID_MATCH | DECYZYJNA-KANDYDAT-ETAP4 | — |  | NIE | courier_resolver.py:1190 |
| ENABLE_PARCEL_LANE_LIVE | DECYZYJNA-KANDYDAT-ETAP4 | True |  | NIE | parcel_lane_merge.py:114 |
| ENABLE_PERF_LAZY_MEMBERS | DECYZYJNA-KANDYDAT-ETAP4 | True |  | tak | plan_manager.py:147 |
| ENABLE_PICKUP_TIME_MIRRORS_CK | DECYZYJNA-KANDYDAT-ETAP4 | True |  | tak | state_machine.py:904 |
| ENABLE_PRE_SHIFT_GRADIENT_PENALTY | DECYZYJNA-KANDYDAT-ETAP4 | — |  | NIE | dispatch_pipeline.py:5385 |
| ENABLE_R1_CORRIDOR_GRADIENT | DECYZYJNA-KANDYDAT-ETAP4 | False |  | NIE | dispatch_pipeline.py:4930 |
| ENABLE_REGEOCODE_SYNC_TEXT | DECYZYJNA-KANDYDAT-ETAP4 | True |  | tak | gastro_edit.py:157 |
| ENABLE_R_DECLARED_TRIPWIRE | DECYZYJNA-KANDYDAT-ETAP4 | True |  | tak | state_machine.py:501 |
| ENABLE_SPLIT_LAYER_GUARD | DECYZYJNA-KANDYDAT-ETAP4 | True |  | tak | dispatch_pipeline.py:257 |
| ENABLE_STATE_WRITE_GUARD | DECYZYJNA-KANDYDAT-ETAP4 | True |  | tak | state_machine.py:283 |
| NEW_COURIER_AUTOPAIR_ENABLED | DECYZYJNA-KANDYDAT-ETAP4 | True |  | NIE | new_courier_pairing.py:392 |
| PARSE_CONTINUITY_GUARD_ENABLED | DECYZYJNA-KANDYDAT-ETAP4 | True |  | NIE | parse_continuity_guard.py:250 |
| RECONCILIATION_ENABLED | DECYZYJNA-KANDYDAT-ETAP4 | True |  | tak | reconciliation/reconcile_worker.py:234 |
| ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW | SHADOW-OBS | True |  | tak | shadow_dispatcher.py:1124 |
| ENABLE_ADDRESS_TOWN_MISMATCH_SHADOW | SHADOW-OBS | True |  | tak | shadow_dispatcher.py:1186 |
| ENABLE_BEST_EFFORT_FASTEST_PICKUP_SHADOW | SHADOW-OBS | True |  | NIE | dispatch_pipeline.py:7152 |
| ENABLE_BEST_EFFORT_OBJM_SHADOW | SHADOW-OBS | True |  | NIE | common.py:2954; dispatch_pipeline.py:7113 |
| ENABLE_BUG4_RESEQ_SHADOW | SHADOW-OBS | True |  | tak | plan_recheck.py:1939 |
| ENABLE_ETA_QUANTILE_SHADOW | SHADOW-OBS | True |  | tak | dispatch_pipeline.py:5570; dispatch_pipeline.py:6207; dispatch_pipeline.py:6221 |
| ENABLE_FAIL03_K2_SHADOW | SHADOW-OBS | True |  | NIE | shadow_dispatcher.py:1017 |
| ENABLE_FEAS_CARRY_BLIND_SHADOW | SHADOW-OBS | True |  | tak | dispatch_pipeline.py:6604 |
| ENABLE_GPS_QUALITY_SHADOW | SHADOW-OBS | — |  | NIE | courier_resolver.py:812 |
| ENABLE_LGBM_TWOMODEL_SHADOW | SHADOW-OBS | True |  | tak | dispatch_pipeline.py:6715; ml_inference.py:850 |
| ENABLE_MIN_DELIVERED_AT_SHADOW | SHADOW-OBS | True |  | tak | dispatch_pipeline.py:6368 |
| ENABLE_OBJM_LEXR6_SELECT_SHADOW | SHADOW-OBS | False |  | tak | common.py:2910; dispatch_pipeline.py:6598 |
| ENABLE_PICKUP_DEBIAS_SHADOW | SHADOW-OBS | True |  | NIE | shadow_dispatcher.py:535 |
| ENABLE_PLN_OBJECTIVE_SHADOW | SHADOW-OBS | True |  | tak | dispatch_pipeline.py:6527 |
| ENABLE_PREP_BIAS_SHADOW | SHADOW-OBS | True |  | tak | shadow_dispatcher.py:491 |
| ENABLE_PREP_VARIANCE_ANOMALY_SHADOW | SHADOW-OBS | True |  | NIE | dispatch_pipeline.py:3045 |
| ENABLE_READY_AT_INSTRUMENTATION | SHADOW-OBS | True |  | NIE | sla_tracker.py:432 |
| ENABLE_REPO_COST_SHADOW | SHADOW-OBS | True |  | tak | dispatch_pipeline.py:5272 |
| OBSERVABILITY_PER_CANDIDATE_ENABLED | SHADOW-OBS | True |  | NIE | observability/candidate_logger.py:68; observability/candidate_logger.py:221 |
| AUTO_KOORD_TELEGRAM_INFO_ENABLED | ALERT/NOTIFY | False |  | NIE | panel_watcher.py:1388 |
| CZASOWKA_T0_ALERT_ENABLED | ALERT/NOTIFY | False |  | NIE | czasowka_proactive/evaluator.py:247 |
| ENABLE_BAG_TIME_ALERTS | ALERT/NOTIFY | False |  | NIE | sla_tracker.py:284; sla_tracker.py:511 |
| ENABLE_DATA_ALERTS | ALERT/NOTIFY | True |  | tak | observability/data_alerts.py:477 |
| ENABLE_FIRMOWE_KONTO_KOORD_ALERTS | ALERT/NOTIFY | False |  | NIE | czasowka_scheduler.py:555 |
| ENABLE_FIRMOWE_KONTO_TELEGRAM_PROPOSALS | ALERT/NOTIFY | False |  | NIE | shadow_dispatcher.py:1314; telegram_approver.py:2079 |
| ENABLE_NOTIFY_PRIORITY_ROUTING | ALERT/NOTIFY | True |  | NIE | notify_router.py:203 |
| ENABLE_STATE_PANEL_DIVERGENCE_ALERT | ALERT/NOTIFY | True |  | NIE | state_panel_monitor.py:144 |
| SHIFT_NOTIFY_ENABLED | ALERT/NOTIFY | False |  | NIE | telegram_approver.py:3174; telegram_approver.py:3273; telegram_approver.py:3339 |
| ALWAYS_PROPOSE_WOULD_REDIRECT_SHADOW | NUMERIC/PARAM? | True |  | tak | shadow_dispatcher.py:964 |
| BEST_EFFORT_ESC_TIER2_MAX_FREE_MIN | NUMERIC | 90 |  | NIE | dispatch_pipeline.py:779 |
| BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN | NUMERIC | 40 |  | tak | common.py:2972; dispatch_pipeline.py:7115; dispatch_pipeline.py:7125 |
| COD_WEEKLY_AUTOCREATE_BLOCK | NUMERIC/PARAM? | — |  | NIE | cod_weekly/config.py:63 |
| COD_WEEKLY_AUTOCREATE_DRY_RUN | NUMERIC/PARAM? | — |  | NIE | cod_weekly/config.py:68 |
| CZASOWKA_MIN_PROPOSAL_SCORE | NUMERIC | 60 |  | NIE | czasowka_proactive/evaluator.py:292 |
| CZASOWKA_PROACTIVE_MAX_WAIT_MIN | NUMERIC | 10 |  | NIE | czasowka_proactive/score_selector.py:191 |
| CZASOWKA_PROACTIVE_MIN_MARGIN | NUMERIC | 15 |  | NIE | czasowka_proactive/score_selector.py:190 |
| CZASOWKA_PROACTIVE_MIN_SCORE | NUMERIC | 30 |  | NIE | czasowka_proactive/score_selector.py:189 |
| CZASOWKA_PROACTIVE_SCORE_SHADOW | NUMERIC/PARAM? | True |  | NIE | czasowka_proactive/score_selector.py:169 |
| CZASOWKA_PROACTIVE_USE_ALL_CANDIDATES | NUMERIC/PARAM? | True |  | NIE | common.py:2247; czasowka_proactive/evaluator.py:135 |
| CZASOWKA_TRIGGER_TOLERANCE_MIN | NUMERIC | 1 |  | NIE | czasowka_proactive/evaluator.py:249; czasowka_proactive/evaluator.py:265 |
| DAILY_STATS_USE_PRESNAPSHOT | NUMERIC/PARAM? | — |  | NIE | daily_stats_sheets.py:162 |
| DATA_ALERTS_TELEGRAM | NUMERIC/PARAM? | — |  | tak | observability/data_alerts.py:479 |
| LEXQUAL_TIME_QUANT_MIN | NUMERIC | 0.0 |  | tak | objm_lexr6.py:61 |
| NEW_COURIER_AUTOPAIR_AUTOWRITE | NUMERIC/PARAM? | True |  | NIE | new_courier_pairing.py:267 |
| O2_CAPZ_DETOUR_MAX_MIN | NUMERIC/PARAM? | — |  | tak | route_simulator_v2.py:908 |
| O2_CAPZ_MIN_GAIN_MIN | NUMERIC/PARAM? | — |  | tak | route_simulator_v2.py:910 |
| O2_CAPZ_Z_MIN | NUMERIC/PARAM? | — |  | tak | route_simulator_v2.py:907 |
| O2_CAP_Z_MIN | NUMERIC/PARAM? | — |  | NIE | plan_recheck.py:752; route_simulator_v2.py:148 |
| OBSERVABILITY_FLEET_FILTER_LOGGING | NUMERIC/PARAM? | True |  | NIE | observability/candidate_logger.py:222 |
| ORDERS_STATE_PRUNE_DRY_RUN | NUMERIC/PARAM? | False |  | NIE | prune_orders_state.py:47 |
| ORDERS_STATE_PRUNE_RETENTION_HOURS | NUMERIC | 12 |  | NIE | prune_orders_state.py:41 |
| PANEL_PACKS_EMPTY_GUARD_MAX_PREV_AGE_S | NUMERIC | 180 |  | NIE | panel_watcher.py:2490 |
| PARSE_BLACKOUT_MIN_PREV | NUMERIC | 5 |  | NIE | parse_continuity_guard.py:252 |
| PARSE_DROP_PCT | NUMERIC | 70 |  | NIE | parse_continuity_guard.py:253 |
| PARSE_GUARD_CONFIRM_CYCLES | NUMERIC | 2 |  | NIE | parse_continuity_guard.py:254 |
| PLAN_GC_DRY_RUN | NUMERIC/PARAM? | — |  | tak | plan_recheck.py:2444 |
| PLAN_GC_MAX_AGE_H | NUMERIC/PARAM? | — |  | NIE | plan_recheck.py:2445 |
| PROPOSAL_FORMAT_V2 | NUMERIC/PARAM? | True |  | NIE | telegram_approver.py:1578; telegram_approver.py:1675 |
| RECONCILIATION_HEALTH_SELF_HEAL | NUMERIC/PARAM? | True |  | NIE | reconciliation/reconcile_log.py:53 |
| AUTO_KOORD_ON_NEW_ORDER_ENABLED | SERVICE-SCOPED(dispatch-panel-watcher) | True |  | NIE | panel_watcher.py:1379 |
| ENABLE_ABSENT_KEY | TEST-ARTEFAKT | — |  | NIE | tests/test_perf_lazy_members.py:40 |
| ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW_NONEXISTENT_XYZ | TEST-ARTEFAKT | — |  | NIE | tests/test_address_coords_mismatch.py:154 |
| ENABLE_COMMITTED_INVALIDATES_VIEW | SERVICE-SCOPED(dispatch-panel-watcher) | — |  | NIE | panel_watcher.py:617 |
| ENABLE_COORDINATOR_FORCE_TIME_RECHECK | SERVICE-SCOPED(dispatch-panel-watcher) | True |  | NIE | panel_watcher.py:2169 |
| ENABLE_ETAP4_NONEXISTENT_FLAG | TEST-ARTEFAKT | — |  | NIE | tests/test_etap4_flag_unification.py:90 |
| ENABLE_GLOBAL_ALLOC_WRITE | TOOLS-ONLY | True |  | NIE | tools/pending_global_resweep.py:305 |
| ENABLE_GPS_DELIVERY_VALIDATION | SERVICE-SCOPED(dispatch-sla-tracker) | True |  | NIE | sla_tracker.py:223 |
| ENABLE_INVALIDATE_PLAN_ON_BAG_CHANGE | SERVICE-SCOPED(dispatch-panel-watcher) | True |  | NIE | panel_watcher.py:569 |
| ENABLE_PANEL_AGREE | SERVICE-SCOPED(dispatch-panel-watcher) | — |  | NIE | panel_watcher.py:278 |
| ENABLE_PENDING_PROPOSALS_WRITE | SERVICE-SCOPED(dispatch-shadow) | True |  | NIE | shadow_dispatcher.py:1134 |
| ENABLE_PICKUP_FROM_GROUND_TRUTH | SERVICE-SCOPED(dispatch-panel-watcher) | True |  | NIE | panel_watcher.py:2085 |
| ENABLE_PROPOSAL_ETA_FLOOR_TO_COMMITTED | SERVICE-SCOPED(dispatch-telegram) | True |  | tak | telegram_approver.py:1437 |
| ENABLE_PROPOSAL_ETA_FLOOR_TO_PLAN | SERVICE-SCOPED(dispatch-telegram) | True |  | tak | telegram_approver.py:1443 |
| ENABLE_RESTAURANT_VIOLATIONS | SERVICE-SCOPED(dispatch-sla-tracker) | — |  | NIE | sla_tracker.py:383 |
| ENABLE_SAME_RESTAURANT_RACE_PROBE | SERVICE-SCOPED(dispatch-shadow) | True |  | tak | shadow_dispatcher.py:901 |
| ENABLE_TESTKEY_A | TEST-ARTEFAKT | — |  | NIE | tests/test_perf_lazy_members.py:38; tests/test_perf_lazy_members.py:71 |
| ENABLE_TESTKEY_B | TEST-ARTEFAKT | — |  | NIE | tests/test_perf_lazy_members.py:39; tests/test_perf_lazy_members.py:48; tests/test_perf_lazy_members.py:54 |
| ENABLE_UWAGI_ADDRESS_PARSER | SERVICE-SCOPED(dispatch-panel-watcher) | True |  | NIE | panel_watcher.py:1247 |
| ENABLE_V327_MULT_SIGN_GUARD | TEST-ARTEFAKT | — | tak | NIE | tests/test_bundle05_gates_hardening.py:64 |
| ENABLE_WAITING_AT_PERSIST | SERVICE-SCOPED(dispatch-panel-watcher) | True |  | NIE | panel_watcher.py:2025 |
| FAZA7_AGREEMENT_BUTTONS_ENABLED | SERVICE-SCOPED(dispatch-telegram) | True |  | NIE | telegram_approver.py:1553; telegram_approver.py:1733 |
| FLAG | TEST-ARTEFAKT | — |  | tak | tests/test_d3_flag_migration.py:188 |
| KEY | TOOLS-ONLY | — |  | NIE | tools/flag_hygiene_check.py:37 |
| MANUAL_KONIEC_COMMAND_ENABLED | SERVICE-SCOPED(dispatch-telegram) | True |  | NIE | telegram_approver.py:3430 |
| MANUAL_POPRAWA_COMMAND_ENABLED | SERVICE-SCOPED(dispatch-telegram) | False |  | NIE | telegram_approver.py:3696 |

---

## STATUS WYKONANIA (dopisane 05.07 ~19:50 UTC)

**Wykonane w worktree `wt-flagreg`, branch `fix/flagreg-sprint25` (commity `f27d97f` + `a06d597`), MERGE DO MASTERA ZA ACK:**
- Partia A' (7 flag decyzyjnych BEZ klucza w flags.json → ETAP4 + konsty-lustra) + Partia B (29 shadow-obs/alert → `_FINGERPRINT_EXTRA_FLAGS`) + 5 konst-luster + baseline effect-checkera +3.
- **Pełna regresja przeciw worktree (fakeroot `ZIOMEK_SCRIPTS_ROOT`): 4190 passed / 1 failed** — jedyny fail = `test_flag_effect_coverage` czytający ZAHARDKODOWANĄ ścieżkę baseline z GŁÓWNEGO checkoutu (worktree ma baseline +3; zweryfikowane ręcznie: new_gap=[] przeciw worktree-baseline). Po merge test zielony. Efektywnie **4190/0**.
- Metryka „czytane w prod poza WSZYSTKIMI rejestrami" (ETAP4∪FP_EXTRA∪NUMERIC∪INFRA): **114 → 62**.
- Merge wstrzymany świadomie: master = żywy kod oneshot-serwisów; nowe wpisy fingerprinta = znany benign „stale-process" szum w fingerprint-check do restartów, a pn 06.07 biegną weryfikacje at-205/206 — decyzja o terminie merge u Adriana.

**ZABLOKOWANE kolizją z tmux 17 (świadomie NIE zrobione):** rejestracja flag OBECNYCH w flags.json (66 szt.) do ETAP4/NUMERIC — każde skurczenie klasy survivors WYMUSZA edycję `tests/test_conftest_flag_strip_guard.py` (asercja „healed → zaktualizuj baseline W DÓŁ"), a to plik Sprintu 1 (tmux 17). Wykonać PO zamknięciu S1 jedną partią (lista = §5 wiersze z `flags.json≠—`).

**Pozostałe 62 poza rejestrami =** 66 json-owych zablokowanych j.w. minus przecięcia + parametry numeryczne/service-scoped (RECONCILIATION_* 10 szt., czasówka-proactive, PLAN_GC_*, O2_CAPZ_* — te wg werdyktów service-scoped mogą świadomie zostać poza ETAP4; doc-wpisy = Partia D, nie zaczęta).
