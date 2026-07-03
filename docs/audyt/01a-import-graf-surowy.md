# 01a - GRAF IMPORTOW (surowy) - zalacznik do 01-ZALEZNOSCI.md

Snapshot 2026-07-03. Skrypt AST `/tmp/import_graph2.py` po 815 `.py` (root pakietu `scripts/`, prefiks `dispatch_v2.`).
Uwaga: rezolwer AST mapowal `from dispatch_v2 import X` na `__init__` -> nadreprezentacja sierot; kolumna grep = korekta (patrz 01 sekcja 4 + DO WYJASNIENIA #6).

## Top huby (in-degree)
-  405  dispatch_v2.__init__
-   93  dispatch_v2.tools.__init__
-   85  dispatch_v2.common
-   58  dispatch_v2.telegram_utils
-   51  dispatch_v2.route_simulator_v2
-   48  dispatch_v2.dispatch_pipeline
-   21  dispatch_v2.feasibility_v2
-   15  dispatch_v2.osrm_client
-   13  dispatch_v2.courier_resolver
-   13  dispatch_v2.observability.__init__
-   11  dispatch_v2.shadow_dispatcher
-    9  dispatch_v2.core.jsonl_appender
-    9  dispatch_v2.panel_client
-    8  dispatch_v2.tools._rotated_logs
-    8  dispatch_v2.auto_proximity_classifier
-    8  dispatch_v2.shift_notifications.__init__
-    7  dispatch_v2.geocoding
-    7  dispatch_v2.czasowka_proactive.__init__
-    6  dispatch_v2.core.config_reload_subscriber
-    6  dispatch_v2.telegram_approver
-    6  dispatch_v2.panel_watcher
-    6  dispatch_v2.cod_weekly.config
-    5  dispatch_v2.state_machine
-    5  dispatch_v2.districts_data
-    5  dispatch_v2.telegram.__init__

## Top out-degree (najwiecej importuja)
-  21  dispatch_v2.dispatch_pipeline
-  14  dispatch_v2.panel_watcher
-  12  dispatch_v2.telegram_approver
-  11  dispatch_v2.shadow_dispatcher
-   9  dispatch_v2.sla_tracker
-   8  dispatch_v2.daily_accounting.main
-   8  dispatch_v2.tests.test_uwagi_defense_gates
-   7  dispatch_v2.tests.test_bug_d_distance_bin_traffic
-   7  dispatch_v2.cod_weekly.run_weekly
-   6  dispatch_v2.new_courier_pairing
-   6  dispatch_v2.czasowka_scheduler
-   6  dispatch_v2.shift_notifications.worker

## Cykle importow (SCC>1)
- dispatch_v2.auto_assign_gate <-> dispatch_v2.dispatch_pipeline
- dispatch_v2.panel_client <-> dispatch_v2.panel_html_parser
- dispatch_v2.sms.ovh <-> dispatch_v2.sms.provider <-> dispatch_v2.sms.stub

## Zewnetrzne / sibling importy (nie-stdlib, nie-dispatch_v2)
-  154  pytest
-   24  numpy
-   23  schedule_utils
-   17  lightgbm
-   11  pandas
-    7  ortools
-    7  twomodel_common
-    6  gspread

## Sieroty: surowy AST = 45 kandydatow -> po grep-weryfikacji 11 realnych (minus sprint2_analysis skasowane w trakcie audytu).

### 45 surowych kandydatow AST -> werdykt grep
(I = ma realnego importera = falszywy alarm AST; DEAD = brak importera; GONE = skasowany przez rownolegla sesje w trakcie audytu)
- [I] dispatch_v2.address_mismatch
- [I] dispatch_v2.address_pin_memory
- [I] dispatch_v2.assistant_heartbeat
- [I] dispatch_v2.auto_assign_executor
- [I] dispatch_v2.auto_koord
- [I] dispatch_v2.coordinator_time_recheck
- [I] dispatch_v2.courier_info
- [I] dispatch_v2.czasowka_proactive.handlers
- [I] dispatch_v2.deploy_staging.scripts.schedule_utils
- [DEAD] dispatch_v2.docs.deploy.ha-lite.backup_sentinel
- [I] dispatch_v2.drive_min_calibration
- [I] dispatch_v2.geocode_verify
- [I] dispatch_v2.global_alloc_store
- [I] dispatch_v2.gps_quality
- [I] dispatch_v2.live_eta_cache
- [I] dispatch_v2.manual_overrides
- [DEAD] dispatch_v2.ml_data_prep.bundle_geo_experiment
- [I] dispatch_v2.ml_data_prep.twomodel_common
- [I] dispatch_v2.notify_router
- [I] dispatch_v2.obj_replay_capture
- [I] dispatch_v2.objm_lexr6
- [I] dispatch_v2.panel_detail_prefetch
- [I] dispatch_v2.panel_roster
- [I] dispatch_v2.parse_continuity_guard
- [I] dispatch_v2.pending_pool
- [I] dispatch_v2.pending_proposals_store
- [I] dispatch_v2.pln_objective
- [I] dispatch_v2.prep_bias_anchor
- [I] dispatch_v2.reconciliation.auto_resync
- [I] dispatch_v2.reconciliation.phantom_detector
- [I] dispatch_v2.reconciliation.reconcile_log
- [I] dispatch_v2.route_podjazdy
- [I] dispatch_v2.sla_anchor
- [GONE] dispatch_v2.sprint2_analysis._common
- [GONE] dispatch_v2.sprint2_analysis.data_inventory
- [GONE] dispatch_v2.sprint2_analysis.override_patterns
- [GONE] dispatch_v2.sprint2_analysis.propose_uptime_analysis
- [GONE] dispatch_v2.sprint2_analysis.report_builder
- [GONE] dispatch_v2.sprint2_analysis.sanity_checks
- [GONE] dispatch_v2.sprint2_analysis.tak_mystery
- [I] dispatch_v2.telegram.templates
- [I] dispatch_v2.tools.ledger_io
- [DEAD] dispatch_v2.tools.verify_obj_f1_2026-05-19
- [DEAD] dispatch_v2.tools.verify_obj_f2_2026-05-19
- [DEAD] dispatch_v2.tools.verify_obj_f4_2026-05-19
