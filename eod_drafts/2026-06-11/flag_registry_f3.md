# Rejestr flag (F3) — wygenerowany tools/flag_registry.py

Flag: **266** · rozjazdy: **10**

## Rozjazdy
- ⚠ CZASOWKA_MAX_EMIT_PER_TICK: env-frozen tylko w [dispatch-czasowka.service] — pozostałe unity silnika liczą defaultem (None). Zweryfikuj zamiar: domenowe per-proces OK (inwentarz ETAP4), flaga SILNIKA = rozjazd klasy Z-04.
- ⚠ CZASOWKA_RETROACTIVE_HOURS: env-frozen tylko w [dispatch-czasowka.service] — pozostałe unity silnika liczą defaultem (None). Zweryfikuj zamiar: domenowe per-proces OK (inwentarz ETAP4), flaga SILNIKA = rozjazd klasy Z-04.
- ⚠ CZASOWKA_TELEGRAM_DRYRUN: env-frozen tylko w [dispatch-czasowka.service] — pozostałe unity silnika liczą defaultem (None). Zweryfikuj zamiar: domenowe per-proces OK (inwentarz ETAP4), flaga SILNIKA = rozjazd klasy Z-04.
- ⚠ ENABLE_GPS_FREE_ANCHOR: env-frozen tylko w [dispatch-panel-watcher.service, dispatch-plan-recheck.service] — pozostałe unity silnika liczą defaultem (None). Zweryfikuj zamiar: domenowe per-proces OK (inwentarz ETAP4), flaga SILNIKA = rozjazd klasy Z-04.
- ⚠ ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE: env-frozen tylko w [dispatch-panel-watcher.service] — pozostałe unity silnika liczą defaultem (None). Zweryfikuj zamiar: domenowe per-proces OK (inwentarz ETAP4), flaga SILNIKA = rozjazd klasy Z-04.
- ⚠ ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP: env-frozen tylko w [dispatch-panel-watcher.service] — pozostałe unity silnika liczą defaultem (None). Zweryfikuj zamiar: domenowe per-proces OK (inwentarz ETAP4), flaga SILNIKA = rozjazd klasy Z-04.
- ⚠ ENABLE_PLAN_CANON_ORDER_INVARIANTS: env-frozen tylko w [dispatch-panel-watcher.service, dispatch-plan-recheck.service] — pozostałe unity silnika liczą defaultem (None). Zweryfikuj zamiar: domenowe per-proces OK (inwentarz ETAP4), flaga SILNIKA = rozjazd klasy Z-04.
- ⚠ ENABLE_PLAN_REAL_PICKED_UP_AT: env-frozen tylko w [dispatch-panel-watcher.service, dispatch-plan-recheck.service] — pozostałe unity silnika liczą defaultem (None). Zweryfikuj zamiar: domenowe per-proces OK (inwentarz ETAP4), flaga SILNIKA = rozjazd klasy Z-04.
- ⚠ ENABLE_PLAN_SEQUENCE_LOCK: env-frozen tylko w [dispatch-plan-recheck.service] — pozostałe unity silnika liczą defaultem (None). Zweryfikuj zamiar: domenowe per-proces OK (inwentarz ETAP4), flaga SILNIKA = rozjazd klasy Z-04.
- ⚠ USE_V2_PARSER: env-frozen tylko w [dispatch-panel-watcher.service] — pozostałe unity silnika liczą defaultem (None). Zweryfikuj zamiar: domenowe per-proces OK (inwentarz ETAP4), flaga SILNIKA = rozjazd klasy Z-04.

## Pełny inwentarz
| flaga | efektywna | źródło | default | flags.json | env |
|---|---|---|---|---|---|
| `A2_RELIABILITY_COEFF` | `'100'` | common.py default | `'100'` | `None` | — |
| `A2_RELIABILITY_FEED_PATH` | `'/root/.openclaw/workspace/dispatch_state/courier_reliability.json'` | common.py default | `'/root/.openclaw/workspace/dispatch_state/courier_reliability.json'` | `None` | — |
| `A2_RELIABILITY_MIN_GAP` | `'0.05'` | common.py default | `'0.05'` | `None` | — |
| `A4_TEST_FLAG` | `None` | common.py default | `None` | `False` | — |
| `ALWAYS_PROPOSE_WOULD_REDIRECT_SHADOW` | `None` | common.py default | `None` | `True` | — |
| `AUTO_KOORD_ON_NEW_ORDER_ENABLED` | `None` | common.py default | `None` | `True` | — |
| `AUTO_KOORD_TELEGRAM_INFO_ENABLED` | `None` | common.py default | `None` | `False` | — |
| `AUTO_PROXIMITY_ENABLED` | `None` | common.py default | `None` | `False` | — |
| `AUTO_PROXIMITY_SHADOW_ONLY` | `None` | common.py default | `None` | `True` | — |
| `AUTO_PROXIMITY_THRESHOLD` | `None` | common.py default | `None` | `'T1'` | — |
| `AUTO_ROUTE_WEAK_PICK_SCORE_FLOOR` | `None` | common.py default | `None` | `0.0` | — |
| `BAG_TIME_FIFO_TIE_PENALTY` | `'5.0'` | common.py default (brak klucza flags.json) | `'5.0'` | `None` | — |
| `BAG_TIME_MAX_PENALTY_PER_MIN` | `'0.7'` | common.py default (brak klucza flags.json) | `'0.7'` | `None` | — |
| `BAG_TIME_SUM_PENALTY_PER_MIN` | `'1.0'` | common.py default (brak klucza flags.json) | `'1.0'` | `None` | — |
| `BARTEK_USER_ID` | `None` | common.py default | `None` | `8753482870` | — |
| `BUNDLE_MAX_DELIV_SPREAD_KM` | `'8.0'` | common.py default | `'8.0'` | `None` | — |
| `CARRY_CHAIN_DINNER_END_HOUR_WARSAW` | `'21'` | common.py default | `'21'` | `None` | — |
| `CARRY_CHAIN_DINNER_START_HOUR_WARSAW` | `'17'` | common.py default | `'17'` | `None` | — |
| `CARRY_CHAIN_ETA_THRESHOLD_MIN` | `'15.0'` | common.py default | `'15.0'` | `None` | — |
| `CARRY_CHAIN_HARD_REJECT_STOPS` | `'2'` | common.py default | `'2'` | `None` | — |
| `CARRY_CHAIN_PENALTY_COEFF` | `'1.5'` | common.py default | `'1.5'` | `None` | — |
| `COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN` | `'10.0'` | common.py default | `'10.0'` | `None` | — |
| `COMMIT_RENDER_DIVERGENCE_TILDE_MIN` | `'3.0'` | common.py default | `'3.0'` | `None` | — |
| `COORDINATOR_DM_ROUTING_ENABLED` | `None` | common.py default | `None` | `True` | — |
| `CZASOWKA_MAX_EMIT_PER_TICK` | `{'dispatch-czasowka.service': '3'}` | env unitów (zamrożone przy starcie) | `None` | `None` | dispatch-czasowka.service: 3 (override.conf) |
| `CZASOWKA_MIN_PROPOSAL_SCORE` | `None` | common.py default | `None` | `60` | — |
| `CZASOWKA_PROACTIVE_ENABLED` | `None` | common.py default | `None` | `True` | — |
| `CZASOWKA_PROACTIVE_MAX_WAIT_MIN` | `None` | common.py default | `None` | `10` | — |
| `CZASOWKA_PROACTIVE_MIN_MARGIN` | `None` | common.py default | `None` | `15` | — |
| `CZASOWKA_PROACTIVE_MIN_SCORE` | `None` | common.py default | `None` | `30` | — |
| `CZASOWKA_PROACTIVE_SCORE_BASED` | `None` | common.py default | `None` | `False` | — |
| `CZASOWKA_PROACTIVE_SCORE_SHADOW` | `None` | common.py default | `None` | `True` | — |
| `CZASOWKA_PROACTIVE_USE_ALL_CANDIDATES` | `False` | common.py default | `False` | `True` | — |
| `CZASOWKA_RETROACTIVE_HOURS` | `{'dispatch-czasowka.service': '2'}` | env unitów (zamrożone przy starcie) | `None` | `None` | dispatch-czasowka.service: 2 (override.conf) |
| `CZASOWKA_T0_ALERT_ENABLED` | `None` | common.py default | `None` | `False` | — |
| `CZASOWKA_T40_ENABLED` | `None` | common.py default | `None` | `True` | — |
| `CZASOWKA_T50_ENABLED` | `None` | common.py default | `None` | `True` | — |
| `CZASOWKA_T60_ENABLED` | `None` | common.py default | `None` | `True` | — |
| `CZASOWKA_TELEGRAM_DRYRUN` | `{'dispatch-czasowka.service': '0'}` | env unitów (zamrożone przy starcie) | `None` | `None` | dispatch-czasowka.service: 0 (override.conf) |
| `CZASOWKA_TRIGGERS_MIN` | `None` | common.py default | `None` | `[60, 50, 40]` | — |
| `CZASOWKA_TRIGGER_TOLERANCE_MIN` | `None` | common.py default | `None` | `1` | — |
| `DIFFICULT_CASE_LOG_PATH` | `'/root/.openclaw/workspace/scripts/logs/difficult_case_log.jsonl'` | common.py default | `'/root/.openclaw/workspace/scripts/logs/difficult_case_log.jsonl'` | `None` | — |
| `DIFFICULT_CASE_SCORE_FLOOR` | `'-30.0'` | common.py default | `'-30.0'` | `None` | — |
| `ENABLE_A2_RELIABILITY_SOFT_SCORE` | `True` | flags.json (kanon hot-reload) | `False` | `True` | — |
| `ENABLE_AUTO_PROXIMITY_POST_SHIFT_5MIN` | `False` | common.py default | `False` | `None` | — |
| `ENABLE_AUTO_ROUTE_WEAK_PICK_ALERT` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_B3_WAIT_GRADIENT` | `False` | common.py default | `False` | `None` | — |
| `ENABLE_BAG_COORD_REPAIR` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_BAG_TIME_ALERTS` | `False` | common.py default | `False` | `False` | — |
| `ENABLE_BAG_TIME_FAIRNESS_SCORING` | `False` | common.py default | `False` | `False` | — |
| `ENABLE_BEST_EFFORT_POS_SOURCE_KEY` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_BEST_EFFORT_R6_KOORD_REDIRECT` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_BUG2_GAP_FROM_PLAN` | `False` | common.py default | `False` | `None` | — |
| `ENABLE_BUNDLE_DELIV_SPREAD_CAP` | `True` | flags.json (kanon hot-reload) | `False` | `True` | — |
| `ENABLE_BUNDLE_SYNC_SPREAD` | `False` | common.py default | `False` | `True` | — |
| `ENABLE_C2_NEG_GAP_DECAY` | `True` | flags.json (kanon hot-reload) | `False` | `True` | — |
| `ENABLE_CARRY_CHAIN_PENALTY` | `False` | common.py default | `False` | `False` | — |
| `ENABLE_CLUSTER_DROP_GROUPING_METRIC` | `False` | common.py default | `False` | `None` | — |
| `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` | `False` | flags.json (kanon hot-reload) | `True` | `False` | — |
| `ENABLE_COURIER_LAST_KNOWN_POS` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_CZAS_KURIERA_PROPAGATION` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_D2_STALE_SCHEDULE_SOFT` | `False` | common.py default | `False` | `None` | — |
| `ENABLE_DIFFICULT_CASE_KOORD_REDIRECT` | `False` | flags.json (kanon hot-reload) | `False` | `False` | — |
| `ENABLE_DRIVE_MIN_CALIBRATION_V2` | `None` | common.py default | `None` | `False` | — |
| `ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_DROP_TIME_CONSTRAINT` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_ETA_QUANTILE_SHADOW` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_EXCLUDE_BY_CID` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_F4_COURIER_POS_INTERP` | `True` | flags.json (kanon hot-reload) | `False` | `True` | — |
| `ENABLE_F4_COURIER_POS_PICKUP_PROXY` | `True` | flags.json (kanon hot-reload) | `False` | `True` | — |
| `ENABLE_F7_HIGH_RISK_BUCKET` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_FAIL03_K2_SHADOW` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_FAIL12_SCHEDULE_FAILOPEN` | `True` | flags.json (kanon hot-reload) | `False` | `True` | — |
| `ENABLE_FAIL12_STOREPOS_STRICT` | `True` | common.py default (brak klucza flags.json) | `True` | `None` | — |
| `ENABLE_FIRMOWE_KONTO_KOORD_ALERTS` | `None` | common.py default | `None` | `False` | — |
| `ENABLE_FIRMOWE_KONTO_TELEGRAM_PROPOSALS` | `None` | common.py default | `None` | `False` | — |
| `ENABLE_FLEET_LOAD_GOVERNOR` | `False` | common.py default | `False` | `True` | — |
| `ENABLE_FLEET_OVERLOAD_PENALTY` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_GEOCODE_VERIFICATION_ENFORCE` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_GPS_BBOX_GUARD` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_GPS_FREE_ANCHOR` | `{'dispatch-panel-watcher.service': '1', 'dispatch-plan-recheck.service': '1'}` | env unitów (zamrożone przy starcie) | `None` | `None` | dispatch-panel-watcher.service: 1 (unified-route-f3.conf)<br>dispatch-plan-recheck.service: 1 (gps-free-anchor.conf) |
| `ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE` | `{'dispatch-panel-watcher.service': '1'}` | env unitów (zamrożone przy starcie) | `None` | `None` | dispatch-panel-watcher.service: 1 (unified-route-f3.conf) |
| `ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP` | `{'dispatch-panel-watcher.service': '1'}` | env unitów (zamrożone przy starcie) | `None` | `None` | dispatch-panel-watcher.service: 1 (unified-route-f3.conf) |
| `ENABLE_INACTIVE_COURIER_GUARD` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_INTRA_RESTAURANT_GAP_LIMIT` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_INVALIDATE_PLAN_ON_BAG_CHANGE` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_KEBAB_KROL_DINNER_EXCLUSION` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_LATE_PICKUP_HARD_GATE` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_LATE_PICKUP_TIERING_SCORE_FIRST` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_LGBM_METRICS_READ` | `{'dispatch-shadow.service': '1'}` | env unitów (zamrożone przy starcie) | `False` | `None` | dispatch-shadow.service: 1 (override.conf) |
| `ENABLE_LGBM_PRIMARY` | `False` | common.py default | `False` | `None` | — |
| `ENABLE_LGBM_SHADOW` | `{'dispatch-shadow.service': '1'}` | env unitów (zamrożone przy starcie) | `False` | `None` | dispatch-shadow.service: 1 (override.conf) |
| `ENABLE_LOADAWARE_SELECTION_SHADOW` | `False` | common.py default | `False` | `None` | — |
| `ENABLE_NEW_COURIER_RAMP` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_NO_GPS_EMPTY_DEMOTE` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_OBJ_F3_BEST_EFFORT_R6_KOORD` | `True` | flags.json (kanon hot-reload) | `False` | `True` | — |
| `ENABLE_OBJ_PICKUP_FRESHNESS` | `False` | flags.json (kanon hot-reload) | `False` | `False` | — |
| `ENABLE_OBJ_R6_SOFT_DEADLINE` | `True` | flags.json (kanon hot-reload) | `False` | `True` | — |
| `ENABLE_OBJ_REPLAY_CAPTURE` | `{'dispatch-shadow.service': '1'}` | env unitów (zamrożone przy starcie) | `False` | `None` | dispatch-shadow.service: 1 (dispatch-shadow.service) |
| `ENABLE_OBJ_SPAN_COST` | `True` | flags.json (kanon hot-reload) | `False` | `True` | — |
| `ENABLE_ORDERS_STATE_PRUNE` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_OSRM_COORD_GUARD` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_PANEL_BG_REFRESH` | `{'dispatch-shadow.service': '1', 'dispatch-panel-watcher.service': '0'}` | env unitów (zamrożone przy starcie) | `None` | `None` | dispatch-shadow.service: 1 (override.conf)<br>dispatch-panel-watcher.service: 0 (override.conf) |
| `ENABLE_PANEL_IS_FREE_AUTHORITATIVE` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_PANEL_PACKS_BAG_RECONSTRUCTION` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_PANEL_PACKS_EMPTY_WRITE_GUARD` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_PANEL_PACKS_FALLBACK` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_PENDING_POOL` | `{'dispatch-shadow.service': '1'}` | env unitów (zamrożone przy starcie) | `None` | `None` | dispatch-shadow.service: 1 (override.conf) |
| `ENABLE_PICKED_UP_DROP_FLOOR` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_PICKUP_TIME_DETECTION` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_PLAN_CANON_ORDER_INVARIANTS` | `{'dispatch-panel-watcher.service': '1', 'dispatch-plan-recheck.service': '1'}` | env unitów (zamrożone przy starcie) | `None` | `None` | dispatch-panel-watcher.service: 1 (unified-route-f3.conf)<br>dispatch-plan-recheck.service: 1 (unified-route-f1-f2.conf) |
| `ENABLE_PLAN_REAL_PICKED_UP_AT` | `{'dispatch-panel-watcher.service': '1', 'dispatch-plan-recheck.service': '1'}` | env unitów (zamrożone przy starcie) | `None` | `None` | dispatch-panel-watcher.service: 1 (unified-route-f3.conf)<br>dispatch-plan-recheck.service: 1 (unified-route-f1-f2.conf) |
| `ENABLE_PLAN_SEQUENCE_LOCK` | `{'dispatch-plan-recheck.service': '1'}` | env unitów (zamrożone przy starcie) | `None` | `None` | dispatch-plan-recheck.service: 1 (unified-route-f1-f2.conf) |
| `ENABLE_PLN_OBJECTIVE_SHADOW` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_PREP_BIAS_SHADOW` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_PREP_BIAS_TABLE` | `False` | common.py default | `False` | `False` | — |
| `ENABLE_PREP_VARIANCE_ANOMALY_SHADOW` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP` | `True` | flags.json (kanon hot-reload) | `False` | `True` | — |
| `ENABLE_R04_ENFORCE` | `False` | common.py default | `False` | `None` | — |
| `ENABLE_R04_SHADOW` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_R1_CORRIDOR_GRADIENT` | `False` | common.py default | `False` | `False` | — |
| `ENABLE_R1_PROGRESSIVE_CLIP` | `True` | flags.json (kanon hot-reload) | `False` | `True` | — |
| `ENABLE_R1_WAVE_SCOPED_DIRECTIONALITY` | `False` | common.py default | `False` | `True` | — |
| `ENABLE_R5_PICKUP_DETOUR_PENALTY` | `False` | common.py default | `False` | `True` | — |
| `ENABLE_REPO_COST_LIVE` | `False` | common.py default | `False` | `False` | — |
| `ENABLE_REPO_COST_SHADOW` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_R_PACZKI_FLEX` | `False` | common.py default | `False` | `True` | — |
| `ENABLE_R_RETURN_TO_RESTAURANT_VETO` | `False` | common.py default | `False` | `True` | — |
| `ENABLE_SAME_RESTAURANT_RACE_PROBE` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_SAVED_PLANS` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_SAVED_PLANS_READ` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_SAVED_PLANS_READ_SHADOW` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_SLA_PREEXISTING_BYPASS` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_SOON_FREE_CANDIDATE` | `False` | common.py default | `False` | `False` | — |
| `ENABLE_STATE_PANEL_DIVERGENCE_ALERT` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_STATE_WRITE_GUARD` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_TELEGRAM_FREETEXT_ASSIGN` | `False` | common.py default | `False` | `None` | — |
| `ENABLE_UNIFIED_BAG_STATE` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_UWAGI_ADDRESS_PARSER` | `True` | common.py default | `True` | `True` | — |
| `ENABLE_V319E_PRE_PICKUP_BAG` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V319G_CK_DETECTION` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V319H_BUG1_DROP_PROXIMITY_FACTOR` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V319H_BUG2_WAVE_CONTINUATION` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V319H_BUG4_TIER_CAP_MATRIX` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V319H_CONTINUATION_GUARD` | `True` | flags.json (kanon hot-reload) | `False` | `True` | — |
| `ENABLE_V320_PACKS_GHOST_DETECT` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V324A_SCHEDULE_INTEGRATION` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V324B_CZASOWKA_SCHEDULER` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V325_NEW_COURIER_CAP` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V325_SCHEDULE_HARDENING` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V326_ANCHOR_BASED_SCORING` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V326_FLEET_LOAD_BALANCE` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V326_MULTISTOP_TRAJECTORY` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V326_OR_TOOLS_TSP` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V326_PO_DRODZE_STRICT` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V326_R06_BAG1_FIX` | `False` | common.py default | `False` | `None` | — |
| `ENABLE_V326_R07_CHAIN_ETA` | `False` | common.py default | `False` | `None` | — |
| `ENABLE_V326_SAME_RESTAURANT_GROUPING` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V326_SPEED_MULTIPLIER` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V326_TRANSPARENCY_RATIONALE` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V326_WAVE_GEOMETRIC_VETO` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V326_WAVE_VETO_NEW_DROP` | `False` | common.py default | `False` | `None` | — |
| `ENABLE_V3273_WAIT_COURIER_PENALTY` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V3273_WAIT_REJECT_FREE_COURIER_SKIP` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V3274_FROZEN_PICKUP_WINDOW` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V3274_RENDER_PICKUP_COMMIT_PRIORITY` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V327_BUG_FIXES_BUNDLE` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V327_MULT_SIGN_GUARD` | `True` | common.py default (brak klucza flags.json) | `True` | `None` | — |
| `ENABLE_V327_PRE_PROPOSAL_RECHECK` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V327_TSP_TIME_WINDOWS` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V327_WAIT_PENALTY` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V328_HEURISTIC_SHIFT_END_GUARD` | `True` | common.py default (brak klucza flags.json) | `True` | `None` | — |
| `ENABLE_V328_MASS_FAIL_FALLBACK` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_V328_TIME_MATRIX_DWELL` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_WAITING_AT_PERSIST` | `None` | common.py default | `None` | `True` | — |
| `ENABLE_WORKING_OVERRIDE` | `True` | common.py default | `True` | `None` | — |
| `ENABLE_WORKING_OVERRIDE_GRAFIK_CAP` | `True` | common.py default (brak klucza flags.json) | `True` | `None` | — |
| `ENABLE_ZOMBIE_PICKUP_AT_GUARD` | `None` | common.py default | `None` | `True` | — |
| `FAZA7_AGREEMENT_BUTTONS_ENABLED` | `None` | common.py default | `None` | `True` | — |
| `GPS_FEED_ALERT_ENABLED` | `None` | common.py default | `None` | `False` | — |
| `GPS_FEED_ALERT_SHADOW_ONLY` | `None` | common.py default | `None` | `True` | — |
| `GPS_FEED_MIN_FRESH_RATIO` | `None` | common.py default | `None` | `0.3` | — |
| `GPS_FEED_SUSTAIN_CYCLES` | `None` | common.py default | `None` | `2` | — |
| `KONIEC_AUTHORIZED_USER_IDS` | `None` | common.py default | `None` | `[8765130486, 8753482870]` | — |
| `LATE_PICKUP_HARD_MAX_MIN` | `'5.0'` | common.py default | `'5.0'` | `None` | — |
| `LATE_PICKUP_SOFT_CAP` | `'60.0'` | common.py default | `'60.0'` | `None` | — |
| `LATE_PICKUP_SOFT_COEFF` | `'1.5'` | common.py default | `'1.5'` | `None` | — |
| `LATE_PICKUP_SOFT_FREE_MIN` | `'5.0'` | common.py default | `'5.0'` | `None` | — |
| `LGBM_SHADOW_LATENCY_HARD_CAP_MS` | `'500'` | common.py default | `'500'` | `None` | — |
| `LGBM_SHADOW_LATENCY_SOFT_CAP_MS` | `'200'` | common.py default | `'200'` | `None` | — |
| `LOADGOV_BAG_MIN` | `'3'` | common.py default | `'3'` | `None` | — |
| `LOADGOV_BAG_PENALTY` | `'-40.0'` | common.py default | `'-40.0'` | `None` | — |
| `LOADGOV_DEFENSIVE_AT` | `'3.5'` | common.py default | `'3.5'` | `None` | — |
| `LOADGOV_EWMA_TAU_MIN` | `'15.0'` | common.py default | `'15.0'` | `None` | — |
| `LOADGOV_ORDER_FRESH_H` | `'3.0'` | common.py default | `'3.0'` | `None` | — |
| `LOADGOV_REARM_AT` | `'3.0'` | common.py default | `'3.0'` | `None` | — |
| `LOADGOV_TIGHTEN_AT` | `'2.7'` | common.py default | `'2.7'` | `None` | — |
| `MANUAL_KONIEC_COMMAND_ENABLED` | `None` | common.py default | `None` | `True` | — |
| `MANUAL_POPRAWA_COMMAND_ENABLED` | `None` | common.py default | `None` | `False` | — |
| `NEW_COURIER_AUTOPAIR_AUTOWRITE` | `None` | common.py default | `None` | `True` | — |
| `NEW_COURIER_AUTOPAIR_ENABLED` | `None` | common.py default | `None` | `True` | — |
| `NEW_COURIER_RAMP_DELIVERIES` | `'30'` | common.py default | `'30'` | `None` | — |
| `NEW_COURIER_RAMP_MALUS` | `'-20.0'` | common.py default | `'-20.0'` | `None` | — |
| `NEW_COURIER_RAMP_MAX_KM` | `'2.5'` | common.py default | `'2.5'` | `None` | — |
| `NEW_COURIER_RAMP_SOLO_MALUS` | `'-60.0'` | common.py default | `'-60.0'` | `None` | — |
| `OBJ_F3_R6_BREACH_KOORD_MIN` | `'20.0'` | common.py default | `'20.0'` | `None` | — |
| `OBJ_PICKUP_FRESHNESS_PENALTY_COEFF` | `'20.0'` | common.py default | `'20.0'` | `None` | — |
| `OBJ_PICKUP_FRESHNESS_THRESHOLD_MIN` | `'8.0'` | common.py default | `'8.0'` | `None` | — |
| `OBJ_R6_DEADLINE_PENALTY_COEFF` | `'100'` | common.py default | `'100'` | `None` | — |
| `OBJ_SPAN_COST_COEFF` | `'1.0'` | common.py default | `'1.0'` | `None` | — |
| `OBSERVABILITY_FLEET_FILTER_LOGGING` | `None` | common.py default | `None` | `True` | — |
| `OBSERVABILITY_PER_CANDIDATE_ENABLED` | `None` | common.py default | `None` | `True` | — |
| `ORDERS_STATE_PRUNE_DRY_RUN` | `None` | common.py default | `None` | `False` | — |
| `ORDERS_STATE_PRUNE_RETENTION_HOURS` | `None` | common.py default | `None` | `12` | — |
| `OSRM_MAX_SNAP_KM` | `'5.0'` | common.py default | `'5.0'` | `None` | — |
| `PANEL_PACKS_EMPTY_GUARD_MAX_PREV_AGE_S` | `None` | common.py default | `None` | `180` | — |
| `PARSER_DEGRADED` | `None` | common.py default | `None` | `False` | — |
| `PARSE_BLACKOUT_MIN_PREV` | `None` | common.py default | `None` | `5` | — |
| `PARSE_CONTINUITY_GUARD_ENABLED` | `None` | common.py default | `None` | `True` | — |
| `PARSE_DROP_PCT` | `None` | common.py default | `None` | `70` | — |
| `PARSE_GUARD_CONFIRM_CYCLES` | `None` | common.py default | `None` | `2` | — |
| `PROPOSAL_FORMAT_V2` | `None` | common.py default | `None` | `True` | — |
| `PYTHONPATH` | `{'dispatch-panel-watcher.service': '/root/.openclaw/workspace/scripts', 'dispatch-czasowka.service': '/root/.openclaw/workspace/scripts'}` | env unitów (zamrożone przy starcie) | `None` | `None` | dispatch-panel-watcher.service: /root/.openclaw/workspace/scripts (dispatch-panel-watcher.service)<br>dispatch-czasowka.service: /root/.openclaw/workspace/scripts (dispatch-czasowka.service) |
| `R1_PROGRESSIVE_CRITICAL_COS` | `'-0.7'` | common.py default | `'-0.7'` | `None` | — |
| `R1_PROGRESSIVE_CRITICAL_VAL` | `'-100.0'` | common.py default | `'-100.0'` | `None` | — |
| `R1_PROGRESSIVE_HEAVY_COS` | `'-0.5'` | common.py default | `'-0.5'` | `None` | — |
| `R1_PROGRESSIVE_HEAVY_VAL` | `'-60.0'` | common.py default | `'-60.0'` | `None` | — |
| `R1_PROGRESSIVE_MEDIUM_COS` | `'-0.3'` | common.py default | `'-0.3'` | `None` | — |
| `R1_PROGRESSIVE_MEDIUM_VAL` | `'-45.0'` | common.py default | `'-45.0'` | `None` | — |
| `R5_DETOUR_EXTREME_KM` | `'7.5'` | common.py default | `'7.5'` | `None` | — |
| `R5_DETOUR_FREE_THRESHOLD_KM` | `'0.5'` | common.py default (brak klucza flags.json) | `'0.5'` | `None` | — |
| `R5_DETOUR_PENALTY_PER_KM` | `4.0` | flags.json (kanon hot-reload) | `'8.0'` | `4.0` | — |
| `RECONCILIATION_AUTO_AGE_THRESHOLD_HOURS` | `None` | common.py default | `None` | `4` | — |
| `RECONCILIATION_AUTO_RESYNC_ENABLED` | `None` | common.py default | `None` | `True` | — |
| `RECONCILIATION_ENABLED` | `None` | common.py default | `None` | `True` | — |
| `RECONCILIATION_HARD_CAP_PER_RUN` | `None` | common.py default | `None` | `5` | — |
| `RECONCILIATION_HEALTH_SELF_HEAL` | `None` | common.py default | `None` | `True` | — |
| `RECONCILIATION_INTERVAL_MIN` | `None` | common.py default | `None` | `30` | — |
| `RECONCILIATION_LOOKBACK_DAYS` | `None` | common.py default | `None` | `30` | — |
| `RECONCILIATION_REVALIDATE_TRANSIENT` | `None` | common.py default | `None` | `True` | — |
| `RECONCILIATION_TELEGRAM_ALERT_ENABLED` | `None` | common.py default | `None` | `False` | — |
| `REPO_COST_MAX_PENALTY` | `'30.0'` | common.py default | `'30.0'` | `None` | — |
| `REPO_KM_FULL_SCALE` | `'4.0'` | common.py default | `'4.0'` | `None` | — |
| `SHIFT_BATCH_MIN_COURIERS` | `None` | common.py default | `None` | `3` | — |
| `SHIFT_BATCH_WINDOW_MIN` | `None` | common.py default | `None` | `10` | — |
| `SHIFT_NOTIFY_ENABLED` | `None` | common.py default | `None` | `False` | — |
| `SHIFT_NOTIFY_T30_REMINDER_ENABLED` | `None` | common.py default | `None` | `True` | — |
| `SHIFT_NOTIFY_T60_END_ENABLED` | `None` | common.py default | `None` | `True` | — |
| `SHIFT_NOTIFY_T60_START_ENABLED` | `None` | common.py default | `None` | `True` | — |
| `SHIFT_NOTIFY_TARGET_CHAT_ID` | `None` | common.py default | `None` | `-5149910559` | — |
| `SOON_FREE_MAX_MIN` | `'12.0'` | common.py default | `'12.0'` | `None` | — |
| `STRICT_BAG_RECONCILIATION` | `True` | common.py default | `True` | `None` | — |
| `STRICT_COURIER_ID_SPACE` | `True` | common.py default | `True` | `None` | — |
| `SYNC_SPREAD_BUNDLE_ZERO_MIN` | `'10.0'` | common.py default | `'10.0'` | `None` | — |
| `USE_V2_PARSER` | `{'dispatch-panel-watcher.service': '1'}` | env unitów (zamrożone przy starcie) | `None` | `None` | dispatch-panel-watcher.service: 1 (override.conf) |
| `V319H_GUARD_COSINE_THRESHOLD` | `'-0.3'` | common.py default | `'-0.3'` | `None` | — |
| `V326_WAVE_VETO_NEW_DROP_COS` | `'0.5'` | common.py default | `'0.5'` | `None` | — |
| `V326_WAVE_VETO_NEW_DROP_KM` | `'2.5'` | common.py default | `'2.5'` | `None` | — |
| `V3273_WAIT_COURIER_PER_MIN_PENALTY` | `'-8.0'` | common.py default | `'-8.0'` | `None` | — |
| `V327_BUNDLE_UNKNOWN_SCORE_MULT` | `'0.7'` | common.py default | `'0.7'` | `None` | — |
| `V328_MASS_FAIL_RATIO_THRESHOLD` | `'0.5'` | common.py default | `'0.5'` | `None` | — |
| `WORKING_OVERRIDE_DEFAULT_END` | `'24:00'` | common.py default | `'24:00'` | `None` | — |
| `commitment_level` | `None` | common.py default | `None` | `False` | — |
| `feasibility_check` | `None` | common.py default | `None` | `True` | — |
| `kill_switch_to_v1` | `None` | common.py default | `None` | `False` | — |
