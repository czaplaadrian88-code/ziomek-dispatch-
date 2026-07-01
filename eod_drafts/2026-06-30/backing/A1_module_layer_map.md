# OS 1 — MAPA MODUŁÓW × 10 WARSTW × SERWIS/TIMER (Faza A INWENTARZ, sesja tmux 2, READ-ONLY)

**Data:** 2026-06-30 · **Tryb:** read-only (zero edycji/restartów/flipów) · **HEAD recon:** `8024705`
**Zakres:** każdy istotny `.py` w `/root/.openclaw/workspace/scripts/dispatch_v2` (pomijam `tests/`, `eod_drafts/`, `__pycache__/`, `AUDIT_*/`) + cross-repo render-okołosystem (konsola `nadajesz_clone/panel`, `scripts/courier_api`, parcel lane). STOP na dyspozytorni — bez Mailek/Papu.
**Metoda:** `find/wc` (inwentarz LOC) + `grep -nE 'def '` świeży per moduł (linie DRYFUJĄ — wszystkie cytaty `plik:linia` z grepu z DZIŚ) + `ExecStart=` z `/etc/systemd/system/*.service` (kod→serwis) + `systemctl is-active` (stan żywy) + graf importów `dispatch_pipeline`/`shadow_dispatcher`/`plan_recheck`.
**Pokrycie:** 104 246 LOC łącznie. ~210 plików `.py` (engine repo) + 3 cross-repo render. Wszystkie sklasyfikowane do warstwy + serwisu + CORE/PERI poniżej; luki jawne w §8.

> **Legenda CORE/PERI:**
> **CORE-D** = na ścieżce decyzji `assess_order` (live, każda propozycja) lub egzekucja/zapis kanonu live.
> **DEC-ADJ** = liczy/renderuje decyzję poza głównym tickiem (recanon, plan-recheck, konsola, apka, parcel, auto-assign).
> **TELEM** = zasila decyzję danymi (flota/GPS/geo/ETA-kalibracja), nie decyduje.
> **INSTR** = shadow/monitor/review/at-job (mierzy, NIE wpływa live; Faza C odpali oracle).
> **PERI** = okołosystem niedecyzyjny (COD, księgowość, SMS, migracje, ML-trening, parser-health, reconcile, observ).
> **DEAD?** = podejrzenie K (martwy/szczątkowy/skeleton/retired) — do potwierdzenia w Fazie B/E.

---

## 1. 10 WARSTW → FUNKCJE-WĘZŁY (świeże linie) → moduł-gospodarz

| # | Warstwa (typ) | Funkcje-węzły (plik:linia, świeże) | Moduł(y)-gospodarz |
|---|---|---|---|
| **L1** | **wejście / ingest** (miękki) | `panel_client.parse_panel_html`; `panel_html_parser` (Universal-ID regex, L1 resilience); `panel_watcher._diff_and_emit:1071`; `state_machine.upsert_order:418`; `event_bus.emit`; `bag_state.build_courier_bag_state`; `coordinator_time_recheck` (kolejka force-recheck → `panel_watcher`); `parcel_lane_merge.run:112` | panel_client, panel_html_parser, panel_watcher, state_machine, event_bus, bag_state |
| **L2** | **geokod (HARD)** | `geocoding.geocode:450` / `geocode_restaurant:632` / `_normalize:251`; `osrm_client.haversine:399` / `route:555` / `table:640`; `geometry.haversine_km:11` / `bearing_deg:30` / `bag_centroid:46`; `pipeline_geometry._point_to_segment_km:16` / `_min_dist_to_route_km:36`; `districts_data.classify_trajectory`; `district_reverse_lookup` (kd-tree); `uwagi_address_parser` (firmowe pickup); `address_pin_memory` | geocoding, osrm_client, geometry, pipeline_geometry, districts_data, district_reverse_lookup, uwagi_address_parser, address_pin_memory |
| **L3** | **early-bird (HARD)** | `dispatch_pipeline._early_bird_threshold_min:2598` (KOORD ≥60 min ahead, flags.json hot); `dispatch_pipeline` early_bird KOORD branch (zwiera obwód PRZED pulą — EARLYBIRD-01 forward-shadow `:2604-2613`); `auto_koord.is_czasowka:41`; `czasowka_scheduler` (≥60 min = czasówka hold cid=26) | dispatch_pipeline, auto_koord, czasowka_scheduler |
| **L4** | **telemetria (flota/GPS/ETA)** | `courier_resolver.build_fleet_snapshot:755` / `dispatchable_fleet:1383` / `_rescue_from_last_pos:201` / `_load_last_known_pos:150` / `_post_shift_start_synthetic_eligible:1341`; `fleet_context.build_fleet_context`; `gps_server` (feed); `gps_quality` (accuracy/teleport filtr); `live_eta_cache` (per-decyzja ETA write); `chain_eta.compute_chain_eta:45`; `insertion_anchor.compute_insertion_anchor:44` | courier_resolver, fleet_context, gps_server, gps_quality, live_eta_cache, chain_eta, insertion_anchor |
| **L5** | **feasibility (HARD)** | `feasibility_v2.check_feasibility_v2:424` (+`_per_order_gate`, `_is_paczka_sim:1017`); `route_simulator_v2.simulate_bag_route_v2:243` / `_ortools_plan:975` / `_greedy_plan:850` (R6 BAG_TIME ≤35/40, frozen pickup window R27±5); `tsp_solver.solve_tsp_with_constraints:39`; `same_restaurant_grouper.compute` (pre-TSP); `traffic_v2_aggregator.aggregate_legs:36` | feasibility_v2, route_simulator_v2, tsp_solver, same_restaurant_grouper, traffic_v2_aggregator |
| **L6** | **scoring + ~19 kar (SOFT)** | `scoring.score_candidate:189` (s_dystans:34/s_obciazenie:37/s_kierunek:45/s_czas:57/compute_wait_penalty:61/compute_wait_courier_penalty:110/compute_idle_wait_soft_penalty:167); `wave_scoring.compute_wave_adjustment:320` (+6 boosts); `dispatch_pipeline._late_pickup_soft_penalty:511`; `pln_objective.compute_pln_value:155` (PLN obj — shadow); `prep_bias_anchor` (R6 kotwica korekta); `drive_min_calibration` (pos_source offset); `ml_inference` (LGBM shadow BC); `auto_proximity_classifier.classify_auto_route` | scoring, wave_scoring, dispatch_pipeline, pln_objective, prep_bias_anchor, drive_min_calibration, ml_inference, auto_proximity_classifier |
| **L7** | **selekcja (SOFT)** | `dispatch_pipeline._best_effort_objm_pick:633` / `_best_effort_objm_shadow:672` / `_best_effort_sort_key:564` / `_best_effort_fastest_pickup_key:595` / `_demote_blind_empty:2504` / `_late_pickup_score_first_key:533`; `objm_lexr6.lex_qual:29` / `pick:76` / `bucket:50` / `group_of:65` (KANON tie-break, importowany 5× lokalnie w pipeline: `:663,703,1224,1301,1369`); `pln_objective` (obj-min select shadow); `pending_pool` / `pending_queue_provider.get_pending_queue` (pula pending) | dispatch_pipeline, objm_lexr6, pln_objective, pending_pool, pending_queue_provider |
| **L8** | **werdykt KOORD (HARD)** | `dispatch_pipeline._classify_and_set_auto_route:2839` (auto_route ACK/AUTO/ALERT); `dispatch_pipeline.assess_order:3286` (verdict ASSIGN/KOORD/NO); `auto_assign_gate.evaluate_auto_assign:89` (G1-G14 bramki); `auto_assign_executor` (rate-cap, override-guard — `ENABLE_AUTO_ASSIGN` OFF); `auto_koord.needs_auto_koord:76` / `perform_auto_koord:96`; `validation_gate_lgbm` (agreement gate); `manual_overrides` (wykluczenia HARD) | dispatch_pipeline, auto_assign_gate, auto_assign_executor, auto_koord, validation_gate_lgbm, manual_overrides |
| **L9** | **zapis + kanon (HARD)** | `state_machine.upsert_order:418`; `plan_manager.load_plan:121` / `save_plan:163` / `advance_plan:258` / `invalidate_plan:213` / `insert_stop_optimal:536`; `plan_recheck._apply_canon_order_invariants:1478` / `recanon_courier` (4 handlery: assign/deliver/return/pickup) / `run_recheck:2017`; `route_podjazdy._canon_order_from_plan:141` / `order_podjazdy:190`; `shadow_dispatcher._serialize_result:505` / `_serialize_candidate:276` (zapis shadow_decisions); `pending_proposals_store`; `live_eta_cache` (write); `commitment_emitter`; `postpone_sweeper`; `prune_orders_state`; `global_alloc_store` (kanał konsoli); `reconciliation/*` | state_machine, plan_manager, plan_recheck, route_podjazdy, shadow_dispatcher, pending_proposals_store, live_eta_cache, global_alloc_store, postpone_sweeper, prune_orders_state, commitment_emitter, reconciliation/* |
| **L10** | **konsola / apka / Telegram (render)** | `telegram_approver.format_proposal:1566` (+proposal_sender, handle_message); `telegram/templates`; `telegram_utils.send_admin_alert`; `notify_router` (klasyfikacja alertów); **cross-repo:** `fleet_state._build_route:395` / `_eta_chain:250` (konsola); `feed.read_feed:294` / `_proposal_from_decision:195` (konsola feed+overlay); `courier_orders.build_view:1072` / `_attach_fallback_eta:822` (apka); `parcel_assign.assign_parcel:33`; `daily_briefing`; `courier_info` | telegram_approver, telegram/templates, telegram_utils, notify_router, [console] fleet_state+feed, [app] courier_orders, parcel_assign, daily_briefing, courier_info |

---

## 2. KOD → SERWIS/TIMER (master z `ExecStart=`, stan żywy `systemctl is-active`)

### 2a. Długo-żyjące serwisy (decyzja live + render)
| Serwis | is-active | ExecStart (moduł) | Rola / warstwy |
|---|---|---|---|
| `dispatch-shadow` | **active** | `-m dispatch_v2.shadow_dispatcher` | **GŁÓWNY tick decyzyjny**: importuje `assess_order` (L1-L9) + serializer (L9). Flagi flags.json hot. |
| `dispatch-panel-watcher` | **active** | `-m dispatch_v2.panel_watcher` | ingest (L1) + 4 recanon (L9) + reconcile + konsument `coordinator_time_recheck` + parcel. Drop-iny recanon/immediate-redecide. |
| `dispatch-plan-recheck.timer` | **active** (5 min) | `-m dispatch_v2.plan_recheck` | **K2 „cofacz"**: `run_recheck` regen kanonu co 5 min → `_apply_canon_order_invariants` (L9). 13 drop-inów route/canon env-frozen ON. |
| `dispatch-sla-tracker` | **active** | `-m dispatch_v2.sla_tracker` | SLA/R6 + delivered_at miernik (L9/INSTR). |
| `dispatch-gps` | **active** | `-m dispatch_v2.gps_server` | feed GPS (L4 TELEM). |
| `dispatch-telegram` | **inactive** (świadomie MUTED) | `-m dispatch_v2.telegram_approver` | render Telegram (L10). MUTED → `pending_proposals` 3-writer/no-lock „bezpieczny tylko bo dead" (klasa O). |
| `dispatch-czasowka.timer` | service inactive (oneshot/tick) | `-m dispatch_v2.czasowka_scheduler` | czasówki ≥60 min (L3) + KOORD T-60/50/40. |
| `dispatch-pending-pool.timer` | **active** | `-m dispatch_v2.pending_pool_sweeper` | sweeper puli pending (L7). |
| `dispatch-r04-evaluator.timer` | (timer) | `-m dispatch_v2.r04_evaluator` | tier suggestion shadow (L8 INSTR). |
| `dispatch-state-reconcile.timer` | (15 min) | `-m dispatch_v2.reconciliation.reconcile_worker` | reconcile orders_state↔panel (L9). |
| `dispatch-monitor-419.service` | (active) | `-m dispatch_v2.monitoring.detector_419` | health 419 (PERI). |
| `dispatch-state-panel-monitor.timer` | (timer) | `-m dispatch_v2.state_panel_monitor` | watchdog rozjazdu state↔panel (PERI). |
| `dispatch-new-courier-watch.timer` | (timer) | `-m dispatch_v2.new_courier_pairing` | auto-parowanie kurierów (TELEM/PERI). |
| `dispatch-postpone-sweeper.timer` | (timer) | `-m dispatch_v2.postpone_sweeper` | POSTPONE replan (L9). |
| `dispatch-orders-state-prune.timer` | (nocny) | `-m dispatch_v2.prune_orders_state` | GC terminalnych (L9 H/janitorial). |
| `dispatch-parcel-merge.timer` | (30 s) | `-m dispatch_v2.parcel_lane_merge` | parcel ingest→orders_state (L1/L9). |
| `dispatch-event-bus-cleanup.timer` | (timer) | `-m dispatch_v2.event_bus_cleanup` | GC event_bus (PERI). |
| `courier-api` (`scripts/courier_api`) | **active** | `main.py` (.venv courier_api) | backend apki — `courier_orders.build_view` (L10 cross-repo). |
| `courier-panel-sync.timer` | (timer) | `panel_sync.py --once --live` | sync apka↔panel (L1/L10 cross-repo). |
| `nadajesz-panel` | **active** | `uvicorn app.main:app :8000` | konsola koordynatora — `fleet_state._build_route` + `feed.read_feed` (L10 cross-repo). |
| `nadajesz-ordering` | (active) | `npm run start -p 3001` | front konsoli (render). |
| `nadajesz-parcel-shadow.timer` | (60 s) | `-m app.integrations.ziomek.parcel_dispatch_shadow` | parcel shadow (INSTR cross-repo). |
| `nadajesz-roster-sync.timer` | (timer) | `-m app.jobs.roster_sync` | roster cid↔nazwa (TELEM cross-repo). |
| `ziomek-time-route-monitor.timer` | (10 min) | `tools/ziomek_time_route_monitor.py` (panel .venv) | parytet kolejność konsola↔apka (INSTR cross-repo). |
| `gate-audit` (`scripts/courier_api`) | **active** | `gate_audit_poller.py` | bramka audytu (PERI cross-repo). |

### 2b. Timery-INSTRUMENTY (shadow/monitor/review/at-job — mierzą, NIE live)
Wszystkie `-m dispatch_v2.tools.*` lub `dispatch_v2.observability.*`. Pełna lista kod→timer (z `ExecStart=`):

| Timer/serwis | Moduł (`tools/` lub `observability/`) | Klasa |
|---|---|---|
| `dispatch-b-route-shadow{,-review}` | `tools.b_route_shadow` / `b_route_shadow_review` | INSTR (route-order shadow) |
| `dispatch-bundle-calib-{shadow,review}` | `tools.bundle_calib_shadow` / `bundle_calib_review` | INSTR (objektyw bundling, bramkuje flip 02.07) |
| `dispatch-carried-first-guard` (3 min) | `tools.carried_first_guard` | INSTR (strażnik carried-first, read-only jsonl) |
| `dispatch-checkpoint-tz-shadow` | `tools.checkpoint_tz_shadow` | INSTR (TZ parse) |
| `dispatch-courier-gps-commitment-shadow` | `courier_gps_commitment_shadow.py` (top-level) | INSTR (GPS commitment) |
| `dispatch-gps-commitment-shadow-report` | `courier_gps_commitment_report.py` | INSTR |
| `dispatch-reassign-global-select` (3 min) | `tools.reassignment_global_select` | INSTR→konsola overlay (global_alloc) |
| `dispatch-reassignment-shadow` (3 min) | `tools.reassignment_forward_shadow` | INSTR („duch przerzutu" — 59% fałszywych ratunków, MEMORY) |
| `dispatch-reassignment-shadow-eval` | `tools.reassignment_shadow_eval` | INSTR |
| `dispatch-objm-lexr6-canary-monitor` (10 min) | `tools.objm_lexr6_canary_monitor --notify` | INSTR (⚠ `--notify` w ExecStart — Faza A NIE uruchamia) |
| `dispatch-objm-lexr6-smoke-{flip,verdict,morning-summary}` | `tools.objm_lexr6_smoke_*` | INSTR |
| `dispatch-pickup-lateness-shadow` (5 min) | `pickup_lateness_shadow` (top-level) | INSTR (poślizg odbioru #2) |
| `dispatch-pickup-slip-{monitor,review}` | `tools.pickup_slip_monitor --days N` | INSTR |
| `dispatch-prep-bias-shadow-monitor` | `tools.prep_bias_shadow_monitor` | INSTR |
| `dispatch-freshness-shadow` | `tools.freshness_shadow_monitor --day …` | INSTR |
| `dispatch-pending-resweep-{shadow,review,watchdog}` (1 min) | `tools.pending_global_resweep{,_review,_watchdog}` | INSTR (greedy pile-on detektor) |
| `dispatch-ziomek-pred-calibration` (3 min) | `tools/ziomek_pred_calibration.py` | INSTR (kalibracja predykcji) |
| `dispatch-eta-calibration` | `eta_calibration_logger.py` (top-level) | INSTR/TELEM |
| `dispatch-decision-outcomes` | `tools.decision_outcomes` | INSTR |
| `dispatch-shadow-enrichment` | `tools.shadow_outcome_enricher --hours 6` | INSTR |
| `dispatch-daily-rule-report` | `tools/daily_rule_report.py --tail-days 21` | INSTR |
| `dispatch-faza7-kpi` | `tools.faza7_daily_kpi --telegram` | INSTR (⚠ `--telegram`) |
| `dispatch-fleet-position-snapshot` | `tools.fleet_position_snapshot` | TELEM/INSTR |
| `dispatch-later-promises-monitor` | `tools.monitor_later_promises` | INSTR |
| `dispatch-nogps-equal-watch` | `tools.nogps_equal_override_watch` | INSTR (równość no-gps) |
| `dispatch-koord-cascade` | `observability.koord_cascade_monitor` | INSTR |
| `dispatch-delivered-integrity` | `observability.delivered_integrity_monitor` | INSTR |
| `dispatch-downstream-crosscheck` | `observability.downstream_crosscheck_poll` | INSTR |
| `dispatch-liveness-probe` | `observability.liveness_probe --once` | INSTR |
| `dispatch-ground-truth-gc` | `observability.ground_truth_gc --apply` (⚠ `--apply`=GC, nie decyzja) | INSTR/janitor |
| `dispatch-watchdog` | `observability.watchdog` | INSTR |
| `dispatch-onfailure-alert@` | `observability.alert_onfailure %i` | INSTR (OnFailure) |
| `dispatch-retro-learning` | `tools/retro_learning.py --json-only` | INSTR |
| `dispatch-address-pin-aggregator` | `tools.address_pin_aggregator` | TELEM (address-pin) |
| `gps-delivery-validation` | `tools.gps_delivery_validation_review --write` | INSTR (ground-truth) |

### 2c. PERYFERIA (okołosystem niedecyzyjny)
| Timer/serwis | Moduł | Domena |
|---|---|---|
| `dispatch-cod-weekly{,-preflight,-lastcall}` / `dispatch-cod-panel-ingest` | `cod_weekly.run_weekly` / `panel_cod_ingest` (venv **sheets**) | COD Weekly. ⚠ `dispatch-cod-weekly.service` = **FAILED** (recon). |
| `dispatch-daily-accounting` | `daily_accounting.main` (venv sheets) | księgowość Obliczenia |
| `dispatch-drtusz-bridge` | `scripts/drtusz_bridge/bridge.py` (poza dispatch_v2) | most Dr Tusz (11 firm) |
| `dispatch-papu-bridge` | `scripts/papu_dispatch_bridge/bridge.py` (poza dispatch_v2) | most Papu (GRANICA — nie audytuję wnętrza Papu) |
| `dispatch-overrides-reset` | `scripts/manual_overrides_daily_reset.py` (poza dispatch_v2) | reset override |
| `dispatch-restic-backup` / `dispatch-state-snapshot` | `backup_restic.sh` / `snapshot_orders_state.sh` | backup |
| `dispatch-shift-notify.*` | `shift_notifications.worker` | **RETIRED `-2026-06-15`** (timer+service przemianowane) → klasa K |
| `nadajesz-*` (econ-rollup, ksef-cost, customer-sms, history-ingest, payment-capture, fc21-eval, overflow-*) | `nadajesz_clone/panel app.jobs.*` | konsola PERI cross-repo |

---

## 3. MASTER: MODUŁ × WARSTWY × SERWIS × CORE/PERI (engine repo, top-level)

> Kolumna „Warstwy" = numery L1-L10 których moduł dotyka; **pogrubiona** = primary. „Serwis" = gdzie ładowany live (import) lub uruchamiany.

| Moduł (LOC) | Warstwy | Serwis/Timer (ładuje/uruchamia) | CORE/PERI | Kluczowe węzły |
|---|---|---|---|---|
| **dispatch_pipeline.py** (7028) | **L3 L5 L6 L7 L8** + L2/L4/L9 hooks | shadow (import), plan_recheck (pośrednio), panel_watcher | **CORE-D** | `assess_order:3286`, `_classify_and_set_auto_route:2839`, `_best_effort_objm_pick:633`, `_demote_blind_empty:2504`, `_early_bird_threshold_min:2598`, `_late_pickup_*:496-533` |
| **feasibility_v2.py** (1311) | **L5** | shadow (via pipeline), czasowka, plan_recheck | **CORE-D** | `check_feasibility_v2:424`, `_is_paczka_sim:1017` |
| **route_simulator_v2.py** (1490) | **L5** + L4 | shadow (via pipeline), plan_recheck, instrumenty replay | **CORE-D** | `simulate_bag_route_v2:243`, `_ortools_plan:975`, `_greedy_plan:850` |
| **tsp_solver.py** (525) | **L5** | via route_simulator_v2 | **CORE-D** | `solve_tsp_with_constraints:39` |
| **scoring.py** (288) | **L6** | shadow (via pipeline), wave_scoring | **CORE-D** | `score_candidate:189`, `compute_wait_*:61/110/167` |
| **wave_scoring.py** (393) | **L6** | via pipeline (flag) | **CORE-D** (flag-gated) | `compute_wave_adjustment:320` (+6 boostów) |
| **objm_lexr6.py** (≈90) | **L7** | pipeline (5× local import), 4 instrumenty | **CORE-D** | `lex_qual:29`, `pick:76`, `bucket:50`, `group_of:65` — KANON tie-break |
| **pln_objective.py** (225) | **L6 L7** (shadow) | pipeline (import), obj_econ_replay | **CORE-D** (shadow) | `compute_pln_value:155`, `p_breach:126`, `opp_rate:138` |
| **courier_resolver.py** (1610) | **L4** | shadow, czasowka, replay_failed | **CORE-D** | `build_fleet_snapshot:755`, `dispatchable_fleet:1383`, `_rescue_from_last_pos:201`, `_post_shift_start_synthetic_eligible:1341` |
| **feasibility helpers — same_restaurant_grouper.py** (208) | **L5** | via pipeline (flag) | CORE-D | grouping pre-TSP |
| **traffic_v2_aggregator.py** (≈80) | L5/L6 | pipeline `:5663` (lokalny import w fn) | CORE-D? **DEAD?** | `aggregate_legs:36` (mtime 28.05; tylko 1 call-site lokalny — sprawdzić osiągalność) |
| **geocoding.py** (740) | **L2** | shadow, panel_watcher, state_machine | **CORE-D** | `geocode:450`, `geocode_restaurant:632`, `_normalize:251` |
| **osrm_client.py** (842) | **L2** | wszędzie (haversine/route/table) | **CORE-D** | `haversine:399`, `route:555`, `table:640` |
| **geometry.py** (≈60) | **L2** | pipeline_geometry, scoring | CORE-D | `haversine_km:11`, `bearing_deg:30`, `bag_centroid:46` (⚠ 2. kopia haversine) |
| **pipeline_geometry.py** (≈60) | **L2** | pipeline (`:34` import) | CORE-D | `_point_to_segment_km:16`, `_min_dist_to_route_km:36` |
| **districts_data.py** (1376) | **L2** | pipeline (classify_trajectory) | CORE-D (dane) | strefy/dzielnice + aliasy ulic |
| **district_reverse_lookup.py** (≈100) | **L2** | via districts | TELEM | kd-tree coord→district |
| **chain_eta.py** (262) | **L4** | pipeline (`:3789`) | CORE-D | `compute_chain_eta:45` |
| **insertion_anchor.py** (≈60) | **L4** | pipeline (`:3893`) | CORE-D | `compute_insertion_anchor:44` |
| **bag_state.py** (≈?) | **L1 L4** | pipeline (`:32`), shadow | CORE-D | `build_courier_bag_state` |
| **fleet_context.py** (≈?) | **L4** | pipeline (`:33`) | CORE-D | `build_fleet_context` |
| **state_machine.py** (1114) | **L1 L9** | shadow, panel_watcher, pipeline (events) | **CORE-D** | `upsert_order:418`, `update_from_event` |
| **event_bus.py** (688) | **L1 L9** | shadow, panel_watcher, pipeline | CORE-D (infra) | `emit`, `emit_audit` (SQLite idempotent) |
| **plan_manager.py** (643) | **L9** | plan_recheck, pipeline (saved-plans read) | **CORE-D** | `load_plan:121`, `save_plan:163`, `advance_plan:258`, `invalidate_plan:213`, `insert_stop_optimal:536` |
| **plan_recheck.py** (2108) | **L9** | **plan-recheck.timer** (5 min) + panel_watcher.recanon_courier | **CORE-D** (K2 cofacz) | `_apply_canon_order_invariants:1478`, `run_recheck:2017`, recanon (4 handlery) |
| **route_podjazdy.py** (232) | **L9 L10** | engine render kanonu + apka pośrednio | DEC-ADJ | `_canon_order_from_plan:141`, `order_podjazdy:190`, `pickup_runs:87`, `plan_drop_rank:125` |
| **shadow_dispatcher.py** (1800) | **L8 L9** + orchestr. | **dispatch-shadow** (entry) | **CORE-D** | `run:1506`, `_serialize_result:505`, `_serialize_candidate:276` |
| **panel_watcher.py** (2685) | **L1 L9** | **dispatch-panel-watcher** (entry) | **CORE-D** | `_diff_and_emit:1071`, recanon `:619/663/691/724`, konsument force-recheck |
| **panel_client.py** (819) | **L1** | panel_watcher, pipeline (recheck) | CORE-D | `parse_panel_html`, fetch detali |
| **panel_html_parser.py** (254) | **L1** | panel_client | CORE-D | Universal-ID regex (resilience L1) |
| **panel_detail_prefetch.py** (≈?) | **L1** | panel_watcher | TELEM | równoległy pre-fetch detali (P0 03.06) |
| **panel_roster.py** (210) | **L4** | resolver, new_courier | TELEM | cid↔nazwa roster |
| **coordinator_time_recheck.py** (≈?) | **L1 L9** | **panel_watcher** (konsument kolejki) | DEC-ADJ | force-recheck czas_kuriera/pickup_at (przycisk konsoli) |
| **manual_overrides.py** (474) | **L8** | pipeline, shadow | CORE-D | wykluczenia HARD kurierów |
| **auto_assign_gate.py** (243) | **L8** | pipeline (`:2887`) | **CORE-D** | `evaluate_auto_assign:89` (G1-G14) |
| **auto_assign_executor.py** (291) | **L8** | gate (egzekucja) — `ENABLE_AUTO_ASSIGN` **OFF** | CORE-D (INERT) | rate-cap, override-guard |
| **auto_koord.py** (281) | **L3 L8** | czasowka, panel_watcher | CORE-D | `needs_auto_koord:76`, `perform_auto_koord:96`, `is_czasowka:41` |
| **auto_proximity_classifier.py** (699) | **L6 L8** | pipeline (`:2851`) | CORE-D | `classify_auto_route` (T1/T2/T3) |
| **czasowka_scheduler.py** (763) | **L3 L5 L8** | **dispatch-czasowka.timer** (entry) | **CORE-D** | `main:596`, KOORD T-60/50/40, czasówka hold |
| **czasowka_uwagi.py** (≈?) | **L1** | czasowka | TELEM | deadline DOSTAWY z free-text uwagi |
| **pending_pool.py** (229) | **L7 L9** | shadow (`ENABLE_PENDING_POOL`), pending-pool.timer | CORE-D | persystencja puli pending |
| **pending_pool_sweeper.py** (≈?) | **L7** | **dispatch-pending-pool.timer** (entry) | DEC-ADJ | sweeper |
| **pending_queue_provider.py** (≈?) | **L7** | pipeline (`:3375`, flag `ENABLE_PENDING_QUEUE_VIEW=False`) | CORE-D **DEAD?** | SKELETON C7 |
| **pending_proposals_store.py** (≈?) | **L9** | shadow (Opcja B) | DEC-ADJ | zasila `pending_proposals.json` |
| **commitment_emitter.py** (≈?) | L6/L9 | flag `ENABLE_MID_TRIP_PICKUP=False` | **DEAD?** | SKELETON C6 |
| **live_eta_cache.py** (≈?) | **L4 L9** | shadow + plan_recheck (write) | DEC-ADJ | per-decyzja ETA dla apki+konsoli |
| **global_alloc_store.py** (≈?) | **L7 L9** | reassignment_global_select → konsola overlay | DEC-ADJ | kanał globalnej alokacji konsoli |
| **postpone_sweeper.py** (≈?) | **L9** | **dispatch-postpone-sweeper.timer** | DEC-ADJ | replan POSTPONE |
| **prune_orders_state.py** (≈?) | **L9** (janitor) | **dispatch-orders-state-prune.timer** | DEC-ADJ | GC terminalnych (H) |
| **parcel_lane_merge.py** (≈160) | **L1 L9** | **dispatch-parcel-merge.timer** (entry) | DEC-ADJ | `run:112` parcel→orders_state |
| **parcel_assign.py** (≈70) | **L10** | parcel (konsola→apka) | DEC-ADJ | `assign_parcel:33` |
| **sla_tracker.py** (689) | L9/INSTR | **dispatch-sla-tracker** (entry) | TELEM/INSTR | R6 + delivered_at miernik (#5a) |
| **gps_server.py** (365) | **L4** | **dispatch-gps** (entry) | TELEM | feed GPS |
| **gps_quality.py** (226) | **L4** | gps/pipeline (shadow) | TELEM | accuracy/teleport filtr |
| **fleet_context.py** | **L4** | pipeline | CORE-D | (jw.) |
| **telegram_approver.py** (4312) | **L10** | **dispatch-telegram** (entry, MUTED) | DEC-ADJ (render) | `format_proposal:1566`, proposal_sender, handle_message |
| **telegram_utils.py** (≈?) | **L10** | wszędzie (alerty) | infra | `send_admin_alert` (pytest-guard) |
| **notify_router.py** (212) | **L10** | alerty | DEC-ADJ | klasyfikacja/routing Telegram |
| **prep_bias_anchor.py** (≈?) | **L6** | pipeline (calib_maps), `ENABLE_PREP_BIAS_TABLE=False` | CORE-D (shadow) | korekta R6 kotwicy o bias kuchni |
| **drive_min_calibration.py** (≈?) | **L6** | pipeline, `ENABLE_DRIVE_MIN_CALIBRATION_V2` main=OFF/shadow=ON | CORE-D (shadow) | pos_source offset + floor |
| **eta_residual_infer.py** (262) | **L6** | shadow-only (R3 residual) | INSTR | inference ETA residual |
| **calib_maps.py** (205) | **L6** | pipeline (`:21`), shadow (`:26`) | CORE-D (shadow) | mapy ETA-quantile + prep-bias |
| **ml_inference.py** (858) | **L6** | shadow (`ENABLE_LGBM_SHADOW=1`) | CORE-D (shadow) | LGBM BC inference |
| **validation_gate_lgbm.py** (239) | **L8** | LGBM gate (shadow) | INSTR | agreement_rate gate |
| **r04_evaluator.py** (617) | **L8** | **dispatch-r04-evaluator.timer** | INSTR | tier suggestion (Phase 1 SHADOW) |
| **r04_apply.py** (336) | **L8** | semi-enforce ACK | DEC-ADJ (gated) | applier preview ACK |
| **speed_tier_tracker.py** (211) | L4 | nightly (brak consumera — `ENABLE_SPEED_TIER_LOADING_PLANNED=False`) | **DEAD?** | SKELETON C4 |
| **courier_ranking.py** (235) | L10/PERI | cron (Telegram daily) | PERI | ranking z sla_log |
| **courier_info.py** (250) | **L10** | telegram_approver (/pin /instrukcja) | DEC-ADJ | PIN lookup |
| **courier_admin.py** (≈?) | L1/PERI | new_courier | PERI | atomic roster 4-plik |
| **new_courier_pairing.py** (405) | L4/PERI | **dispatch-new-courier-watch.timer** | TELEM/PERI | auto-parowanie |
| **address_mismatch.py** (260) | L1/L2 | shadow (`ENABLE_ADDRESS_TOWN_MISMATCH_SHADOW`) | INSTR | ulica↔miasto detektor |
| **address_pin_memory.py** (237) | **L2** | pipeline/geocoding | TELEM | pamięć GPS adresu |
| **uwagi_address_parser.py** (262) | **L2** | panel_watcher (firmowe) | CORE-D | parser uwag→coords |
| **state_panel_monitor.py** (≈?) | PERI | **dispatch-state-panel-monitor.timer** | PERI | watchdog rozjazdu |
| **bootstrap_restaurants.py** (284) | L2/PERI | setup (oneshot) | PERI | bootstrap restauracji |
| **extract_restaurant_addresses.py** (≈?) | L2/PERI | tool | PERI | ekstrakcja adresów |
| **geocode_verify.py** / **geocoding_audit.py** | L2/INSTR | tool | INSTR | weryfikacja geokodu |
| **eta_calibration_logger.py** (413) | L6/INSTR | **dispatch-eta-calibration.timer** | INSTR | kalibracja ETA |
| **eta_error_report.py** / **eta_residual_infer.py** | INSTR | tool/shadow | INSTR | raport błędu ETA |
| **learning_analyzer.py** (533) | INSTR/PERI | cron/tool | INSTR | learning_log raport |
| **manual_overrides.py** | (jw. L8) | — | — | — |
| **flags_admin.py** (272) | infra | CLI (mutacje flags.json) | infra | atomic flags edit |
| **pickup_lateness_shadow.py** (208) | INSTR | **dispatch-pickup-lateness-shadow.timer** | INSTR | poślizg odbioru |
| **courier_gps_commitment_shadow.py** (259) / **_report.py** (221) | INSTR | dispatch-courier-gps-commitment-* | INSTR | GPS commitment |
| **replay_failed.py** (309) | L4/INSTR | offline debug (zna bug raw `build_fleet_snapshot`) | INSTR | replay |
| **daily_briefing.py** (643) | L10/PERI | cron | PERI | briefing |
| **daily_stats_sheets.py** (777) / **sync_courier_pay.py** / **td20_caller_report.py** | PERI | sheets cron | PERI | statystyki/wypłaty |
| **assistant_heartbeat.py** / **tg_heartbeat.py** (282) | infra | **dispatch-tg-heartbeat.timer** | PERI | heartbeat |
| **coordinator_activations.py** (≈?) | L8/TELEM | pipeline (cid=123 koordynator) | TELEM | activation state |
| **parse_continuity_guard.py** (366) / **parser_health*.py** (689/827/479) | L1/INSTR | panel_watcher `:8888/health/parser` | INSTR | parser health (3-warstwa) |
| **build_v319h_courier_tiers.py** (≈?) | offline | tool | PERI | budowa tierów |
| **gastro_edit.py** (316) | L10 | konsola/apka edit gastro | DEC-ADJ | edit zamówienia |
| **monitoring/detector_419.py** (208) | PERI | **dispatch-monitor-419** | PERI | health 419 |
| **monitoring/consumer_stuck_alert.py** (279) | INSTR | shadow (import) | INSTR | stuck alert |
| **monitoring/gps_feed_health.py** (377) | L4/INSTR | shadow (import) | TELEM/INSTR | GPS feed health |

---

## 4. CROSS-REPO (render decyzji — okołosystem, klasa J pierwszoplanowa)

| Repo / plik | Warstwa | Serwis | Węzły (świeże linie) | Twin-engine |
|---|---|---|---|---|
| `nadajesz_clone/panel/.../ziomek/fleet_state.py` | **L10** | `nadajesz-panel` (:8000) | `_build_route:395`, `_eta_chain:250`, TRUST_CANON_ORDER `:443`, PIN_AGREED_PICKUP_TIME `:509`, CLAMP_PRESHIFT_PICKUP_ETA `:755/853` | route-order ↔ `route_podjazdy._canon_order_from_plan` + `plan_recheck._apply_canon_order_invariants` |
| `nadajesz_clone/panel/.../ziomek/feed.py` | **L10** | `nadajesz-panel` | `read_feed:294`, `_proposal_from_decision:195`, `_load_global_alloc_fresh:31`, `_load_reassign_select_fresh:55`, `_load_reassign_proposals:239` | proposal render ↔ `shadow_dispatcher._serialize_result` + `global_alloc_store` |
| `scripts/courier_api/courier_orders.py` | **L10** | `courier-api` (:8767) | `build_view:1072`, `_attach_fallback_eta:822` (FROZEN_PICKUP_ETA `:872`), `build_eta_map:959`, `build_delivered:536` | ETA-chain ↔ `chain_eta.compute_chain_eta` + console `_eta_chain`; route ↔ `order_podjazdy` |
| `scripts/courier_api/status_store.py` | L1/L9 | `courier-api` | status apki | ↔ `state_machine` |
| `scripts/courier_api/panel_sync.py` | L1/L10 | `courier-panel-sync.timer` | sync apka↔panel | ↔ panel_watcher |
| `nadajesz_clone/panel app.integrations.ziomek.parcel_dispatch_shadow` | INSTR | `nadajesz-parcel-shadow.timer` | parcel shadow | ↔ `parcel_lane_merge` |
| `nadajesz_clone/panel tools/ziomek_time_route_monitor.py` | INSTR | `ziomek-time-route-monitor.timer` | parytet kolejność konsola↔apka | ↔ obie route-order |
| `scripts/drtusz_bridge/bridge.py` | L1 | `dispatch-drtusz-bridge` | most 11 firm | wstrzyk zleceń |
| `scripts/papu_dispatch_bridge/bridge.py` | L1 | `dispatch-papu-bridge` | most Papu | **GRANICA — nie audytuję wnętrza** |

**route-order rule = ≥6 powierzchni** (potwierdzone grep): engine `route_podjazdy`, `plan_recheck`, `live_eta_cache`, `shadow_dispatcher`; konsola `fleet_state`; apka `courier_orders` (+4 instrumenty: `route_reorder_replay`, `carried_first_guard`, `bundle_calib_shadow`, `b_route_shadow`). → **A2/J pierwszoplanowy dla Fazy B**.

---

## 5. PODSUMOWANIE CORE-DECYZJA vs PERYFERIA

- **CORE-D (ścieżka `assess_order` live + kanon):** dispatch_pipeline, feasibility_v2, route_simulator_v2, tsp_solver, scoring, wave_scoring, objm_lexr6, pln_objective, courier_resolver, same_restaurant_grouper, geocoding, osrm_client, geometry, pipeline_geometry, districts_data, chain_eta, insertion_anchor, bag_state, fleet_context, state_machine, event_bus, plan_manager, plan_recheck, shadow_dispatcher, panel_watcher, panel_client, panel_html_parser, manual_overrides, auto_assign_gate, auto_assign_executor(INERT), auto_koord, auto_proximity_classifier, czasowka_scheduler, pending_pool, uwagi_address_parser, calib_maps, ml_inference(shadow), prep_bias_anchor(shadow), drive_min_calibration(shadow). **≈38 modułów.**
- **DEC-ADJ (recanon/plan/render/parcel/auto):** route_podjazdy, coordinator_time_recheck, pending_pool_sweeper, pending_proposals_store, live_eta_cache, global_alloc_store, postpone_sweeper, prune_orders_state, parcel_lane_merge, parcel_assign, telegram_approver, notify_router, courier_info, gastro_edit, r04_apply, + cross-repo (fleet_state, feed, courier_orders, status_store, panel_sync). **≈20.**
- **TELEM:** gps_server, gps_quality, fleet_context, sla_tracker, panel_roster, panel_detail_prefetch, address_pin_memory, district_reverse_lookup, new_courier_pairing, czasowka_uwagi, coordinator_activations, monitoring/gps_feed_health. **≈12.**
- **INSTR (Faza C oracle):** ~140 w `tools/` + `observability/` + top-level shadow (pickup_lateness_shadow, courier_gps_commitment_shadow/_report, address_mismatch, eta_calibration_logger, eta_residual_infer, validation_gate_lgbm, r04_evaluator, learning_analyzer, parser_health*, replay_failed, reconciliation/*). **Najliczniejsza klasa.**
- **PERI:** cod_weekly/*, daily_accounting/*, sms/*, migrations/*, ml_data_prep/*, sprint2_analysis/*, daily_briefing, daily_stats_sheets, sync_courier_pay, td20_caller_report, bootstrap_restaurants, courier_admin, tg_heartbeat, detector_419, drtusz/papu bridge. **≈40+.**

---

## 6. ANTY-WZORCE ZAUWAŻONE MIMOCHODEM (zasila Fazę B — file:linia + klasa)

1. **A1/N — 2 kopie Haversine:** `osrm_client.haversine:399` vs `geometry.haversine_km:11` (różne sygnatury tuple). MEMORY notuje #12 „Haversine naprawiony w obu bliźniakach osrm+pipeline" — ale `geometry.haversine_km` to MOŻLIWA 3. kopia, sprawdzić czy ma guard fail-loud na None/(0,0).
2. **A2/J — route-order w ≥6 powierzchniach** (§4): `route_podjazdy._canon_order_from_plan:141`, `plan_recheck._apply_canon_order_invariants:1478`, `fleet_state._build_route:395`, `courier_orders` (apka), `live_eta_cache`, `shadow_dispatcher` + 4 instrumenty. Brak wspólnego importu cross-repo → parytet tylko przez golden-fixture/monitor. Kandydat #1 na PoC „one route-order module".
3. **A1 — objm_lexr6.lex_qual importowany 5× lokalnie w `dispatch_pipeline`** (`:663,703,1224,1301,1369`, aliasy `_OL`/`_olx`) + 4 instrumenty. Jedno źródło (dobre), ale 5 lokalnych call-site = ryzyko rozjazdu cap_min/bezpiecznika. (A2 powierzchnie selekcji.)
4. **K — moduły SKELETON/DEAD flag-OFF-na-zawsze:** `commitment_emitter.py` (`ENABLE_MID_TRIP_PICKUP=False` common.py:920), `pending_queue_provider.py` (`ENABLE_PENDING_QUEUE_VIEW=False` :921, lecz importowany w pipeline `:3375`), `speed_tier_tracker.py` (`ENABLE_SPEED_TIER_LOADING_PLANNED=False` :909, „brak consumera"). Kod żyje, gałąź nieosiągalna.
5. **K — retired-nie-usunięty:** `shift_notifications/worker.py` (886 LOC) — systemd `dispatch-shift-notify.{service,timer}.retired-2026-06-15`. Moduł w drzewie, serwis przemianowany na `.retired`.
6. **K/DEAD? — `traffic_v2_aggregator.aggregate_legs:36`** (mtime 2026-05-28) importowany TYLKO w `dispatch_pipeline:5663` wewnątrz funkcji — sprawdzić osiągalność gałęzi (czy ENABLE_* ją odcina).
7. **O — `pending_proposals.json` 3-writer/no-lock** „bezpieczny TYLKO bo `dispatch-telegram` inactive/MUTED" (recon §B). Writerzy: `telegram_approver`, `pending_proposals_store`, `pending_global_resweep` (PENDING_RESWEEP_LIVE=false). Re-enable Telegrama = regres bez zmiany kodu.
8. **D2 — env-frozen vs flags.json per-serwis:** 13 drop-inów `dispatch-plan-recheck.service.d` z `ENABLE_*=1` NIEOBECNYCH w flags.json (recanon/route/canon); `ENABLE_PANEL_BG_REFRESH=0` na panel-watcher vs `=1` na shadow. Efektywny stan różny per-proces — Faza B/D musi czytać `systemctl show -p Environment`, nie flags.json.
9. **K — śmieci `.bak` w drop-in dirs:** `override.conf.bak-pre-veto-retire-coeff100-2026-06-11` (shadow), `unified-route-f1-f2.conf.bak-…` (plan-recheck), `unified-route-f3.conf.bak-…` (panel-watcher). systemd czyta tylko `*.conf` — inertne, ale clutter.
10. **M — `dispatch-cod-weekly.service` = FAILED** (recon §B) — peryferyjny, ale cicha awaria w okołosystemie COD.
11. **L/J — `route_podjazdy.py` ŻYJE i w engine i jako pojęcie w konsoli** (`order_podjazdy` engine vs `_build_route` konsola) — to samo słowo „podjazdy/route" = 2 implementacje, parytet niepilnowany importem.
12. **H/janitor — read-with-side-effect kandydat:** `plan_manager.load_plan:121` (MEMORY/design H2 „load_plan mutuje przy odczycie") — do potwierdzenia w Fazie B.

---

## 7. HANDOFF DLA FAZ B/C/D/E/F

- **OSIE LEDGER:** ten plik = **oś PIONOWA** (moduł × warstwa × serwis). Faza B przeczesze MODUŁ × KLASA(A-O) używając kolumny CORE/PERI do priorytetu (CORE-D pierwsze). Kolumna „Warstwy" mówi która z 10 warstw egzekwuje regułę → wejście do klasy C (zła warstwa).
- **Faza B priorytet:** zacznij od ≈38 CORE-D; route-order (§4, ≥6 powierzchni) + objm_lexr6 (5 call-site) + 2× haversine = najgęstsze A1/A2/J.
- **Faza C (oracle):** lista INSTR z §2b = wejściowy rejestr przyrządów (≈40 timerów). ⚠ 3 mają `--notify`/`--telegram`/`--apply` w ExecStart (`objm-lexr6-canary-monitor`, `faza7-kpi`, `ground-truth-gc`) — Faza A ich NIE uruchamiała; Faza C odpala oracle 1:1 ostrożnie (read-only wariant).
- **Faza D (koherencja/flagi):** efektywny stan flag = 3-warstwowy merge (flags.json + drop-iny plan-recheck/panel-watcher/shadow). NIE wnioskuj z flags.json ani `os.environ` modułu. `auto_assign_executor` INERT (`ENABLE_AUTO_ASSIGN=OFF`) — gałąź L8 egzekucji martwa live.
- **Faza E/F (dedup→PoC):** 2 kandydaci PoC „one source" już widoczni: (a) **route-order module** (§4, ≥6 powierzchni, najwyższa dźwignia J), (b) **selection key** (objm_lexr6 + bucket + best_effort, L7). Oba CORE-D.
- **GRANICA:** STOP na `papu_dispatch_bridge` (most istnieje, wnętrza Papu nie audytuję). `dispatch-cod-weekly` FAILED i `shift-notify` retired = okołosystem do odnotowania, nie rdzeń.

## 8. LUKI POKRYCIA (jawne, nie cisza)

- **LOC niezgrep'owane per-funkcja dla ~60 modułów PERI/INSTR** (cod_weekly/*, daily_accounting/*, ml_data_prep/*, sprint2_analysis/*, ~120 plików `tools/*`): sklasyfikowane do klasy (INSTR/PERI) i serwisu z `ExecStart`, ale BEZ pełnej listy węzłów — świadomie poza budżetem OS-1 (oś pionowa = warstwy CORE). Faza C dostaje je jako rejestr przyrządów (§2b) z dokładnym module→timer.
- **Cross-repo wnętrze konsoli `nadajesz_clone/panel`** poza 3 plikami ziomek/ (`fleet_state`, `feed`, parcel_shadow) + 2 tools — reszta `app/` (jobs econ/ksef/sms/payment) = PERI cross-repo, wymienione zbiorczo (§2c), nie per-funkcja.
- **`is-active` nie sprawdzony dla wszystkich ~90 timerów** — sprawdzono kluczowe long-running (§2a head); timery oneshot pokazują `inactive` między tickami (normalne) — Faza C zweryfikuje `list-timers` vs rejestr.
- **LOC kilku CORE modułów** (`bag_state`, `fleet_context`, `pending_pool_sweeper`, `coordinator_time_recheck`, `commitment_emitter`, `live_eta_cache`, `global_alloc_store`) oznaczone „≈?" — moduł zidentyfikowany + warstwa + serwis pewne, dokładny LOC pominięty (był w pełnej liście `wc -l`, nie przepisany 1:1).
- **Nie weryfikowałem osiągalności gałęzi** (D3) dla flag-OFF modułów (`commitment_emitter`, `pending_queue_provider`, `traffic_v2_aggregator`) — oznaczone DEAD? jako hipoteza dla Fazy B, nie potwierdzony martwy kod.
