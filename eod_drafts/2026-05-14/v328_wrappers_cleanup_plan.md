# `_v328_*` wrapper cleanup audit (2026-05-14 ~01:15 Warsaw)

**Trigger:** Sprint #37 v2 (11.05) wprowadził `monitoring/consumer_stuck_alert.py` reusable
abstraction (279 LOC, exports: `StuckAlertConfig`, `StuckAlertState`, `HeartbeatSnapshot`,
`compute_heartbeat`, `evaluate_stuck_alert`, `render_telegram_message`, `append_evaluation_log`).
Backlog item z MEMORY 11.05: "cleanup thin wrappery `_v328_*` po Sprint #37+1 stable 7d" (~18.05).

**Status:** AUDIT ONLY. NIE deploy / NIE delete. Action po D+7 stable window 2026-05-18.

## Stan use abstraction post-Sprint #37

| Module | Imports `consumer_stuck_alert`? | Has own `_v328_*` stuck logic? |
|---|---|---|
| `sla_tracker.py` | ✅ (Sprint #37 Phase B `07fc23a`) | NIE |
| `shadow_dispatcher.py` | ✅ | **TAK — 2 funkcje, DUPLIKAT LOGIC** |
| `parser_health_endpoint.py` | ❌ | **TAK — 8 funkcji, NIE migrowane** |

**Discovery:** `monitoring/consumer_stuck_alert.py:9` ma comment _"Backward-compat:
`shadow_dispatcher._v328_should_emit_stuck_alert` zostaje jako [shim]"_ — INTENT shim, ALE
realnie kod w shadow_dispatcher 801-880 to **FULL re-implementation** (40+30 LOC duplikat
algorytmu), nie thin delegation. Bug architectoniczny: comment kłamie o rzeczywistym kodzie.

## Klasyfikacja wszystkich `_v328_*` identyfikatorów

### Konstanty + flags (NIE wrappery — KEEP, version tracking)
```
ENABLE_V328_TIME_MATRIX_DWELL (FAZA 3, common.py)
ENABLE_V328_P3D1_IDLE_COST (P3-D1)
ENABLE_V328_MASS_FAIL_FALLBACK
V328_WORKER_STUCK_AGE_SEC / _PENDING_THRESHOLD / _PENDING_LOW_WATER / _SUSTAIN_CYCLES / _REALERT_INTERVAL_SEC
V328_DOWNSTREAM_WORKER_SLOW_AGE_SEC / _PIPELINE_SILENT_AGE_SEC / _FAILED_1H_THRESHOLD
V328_MASS_FAIL_RATIO_THRESHOLD
V328_CP_SOLVER_FAIL_PER_COURIER
V328_CZASOWKA_DRYRUN
V328_TSP_SETRANGE_OPEN_OOD / _NAN_INF
V328_P3D1_COST / _IDLE_WEIGHT
V328_WORKER_STUCK (event id constant)
```
**Action:** keep all. Version-tracking convention.

### Variables (NIE wrappery — local naming convention, KEEP)
`_v328_now`, `_v328_failed_couriers`, `_v328_fail_ratio`, `_v328_heuristic_results`,
`_v328_send_alert`, `_v328_alert_e`, `_v328_fb_outer_e`, `_v328_fb_cand`

**Action:** keep, prefix dla traceability sprint-tracking.

### Substantive helpers (NIE thin — KEEP)

| Symbol | File:line | Reason keep |
|---|---|---|
| `_v328_simple_heuristic_score` | `dispatch_pipeline.py:535` | V328_MASS_FAIL_FALLBACK specific scoring |
| `_v328_eval_safe` | `dispatch_pipeline.py:2537` | Local closure for parallel try/except |
| `_v328_faza3_dwell` | `route_simulator_v2.py` (FAZA 3 #25) | DWELL helper, flag-gated production logic |
| `_v328_query_events_stats` | `parser_health_endpoint.py:174` | events.db query, parser_health-specific |
| `_v328_parse_last_propose_age_from_journal` | `parser_health_endpoint.py:221` | journalctl parser, parser_health-specific |
| `_v328_parse_worker_age_from_log` | `parser_health_endpoint.py:258` | log file parser, parser_health-specific |
| `_v328_compute_downstream_status` | `parser_health_endpoint.py:292` | orchestrator combining all parser_health signals |

### CLEANUP CANDIDATES — Tier 1 (post-D+7 18.05)

| Symbol | File:line | LOC | Substitute (consumer_stuck_alert) | Test files impacted |
|---|---|---|---|---|
| `_v328_compute_heartbeat_state` | `shadow_dispatcher.py:801` | ~40 | `compute_heartbeat` + `HeartbeatSnapshot` | `test_v328_heartbeat_truthful.py` (10 calls), `test_v328_33_stuck_alert_telegram.py` (3 calls) |
| `_v328_should_emit_stuck_alert` | `shadow_dispatcher.py:840` | ~30 | `evaluate_stuck_alert` + `StuckAlertState` | `test_v328_33_stuck_alert_telegram.py` (2 calls) |

**Plan Tier 1 (effort ~2-3h):**
1. Migrate 15+ test cases do new API: `compute_heartbeat(...config)` zwraca `HeartbeatSnapshot` (dataclass z `.is_recovered` + `.worker_alive` properties); `evaluate_stuck_alert(state, snapshot, now, config)` zwraca `Tuple[bool, AlertKind, StuckAlertState]`.
2. Delete `_v328_compute_heartbeat_state` + `_v328_should_emit_stuck_alert` z `shadow_dispatcher.py`.
3. Update comment w `consumer_stuck_alert.py:9` (usunąć obsolete backward-compat note).
4. Run full regression (`pytest tests/test_v328_*`). Expected: 100% PASS post-migration.
5. Commit + tag `v328-37plus-shadow-shims-cleanup-2026-05-XX`.

**Gate Tier 1:** Sprint #37 v2 stable 7d (~2026-05-18) + zero `V328_WORKER_STUCK` false positives w real production over window. Adrian ACK.

### CLEANUP CANDIDATES — Tier 2 (Sprint #37+2 future, NIE 7-day cleanup)

`parser_health_endpoint.py` 4 funkcje overlap z `consumer_stuck_alert` pattern:

| Symbol | Line | Overlap z |
|---|---|---|
| `_v328_load_alert_state` | 72 | `StuckAlertState.load_or_init` (jeśli istnieje — TBD audit consumer_stuck_alert state I/O API) |
| `_v328_save_alert_state` | 81 | `StuckAlertState.persist` (jeśli istnieje) |
| `_v328_should_alert` | 92 | `evaluate_stuck_alert` (partial — parser_health może mieć extra gating) |
| `_v328_send_health_alert` | 118 | parser_health-specific Telegram path (kandydat ALE używa downstream_status, NIE pure StuckAlert) — keep candidate |

**Pre-condition Tier 2:**
1. Audit `consumer_stuck_alert.StuckAlertState` — czy ma persistence API? Z `class StuckAlertState` declaration (linie 111-119) widać tylko `@dataclass` z fields, brak load/save methods → wymaga rozszerzenia `consumer_stuck_alert.py` o `StuckAlertState.load(path)` + `.persist(path)` (4-fields JSON, atomic write).
2. Decide parser_health-specific signal merging (`events_db` + journal + log → 3 data sources). Pewnie keep `_v328_compute_downstream_status` jako parser_health-specific orchestrator który KONSUMUJE new consumer_stuck_alert API zamiast inline.

**Effort Tier 2:** ~3-4h (extend consumer_stuck_alert + migrate parser_health 4 funkcje + tests).
**Gate:** decyzja Adrian czy abstrakcja powinna pokrywać parser_health context (multi-signal, NIE pure event_bus consumer).

## Cross-ref

- Sprint #37 v2 commit `e3cfc4c` (Phase 1) + `07fc23a` (Phase B)
- MEMORY backup `2026-05-11_eod_12_sprints.tar.gz` — sprint history
- Lekcja #115 (recurring inline state machine = brak abstrakcji) — counter-anti-pattern, motywacja
- Tech debt #38 (P0) — orthogonalna, NIE blokuje

## Action timeline

| Date | Step |
|---|---|
| 2026-05-14 (now) | Audit doc this file ✓ |
| 2026-05-18 (D+7) | Sprint #37 stable verify (`grep V328_WORKER_STUCK` production logs 7d, expected 0 false-pos) |
| 2026-05-18 → 19 | Tier 1 cleanup sprint (~2-3h) — IF stable confirmed + Adrian ACK |
| TBD post-Faza 7 | Tier 2 parser_health migration (~3-4h) — gated by Adrian arch decision |

## NIE w scope cleanup

- Wszystkie `V328_*` konstanty (config naming) — KEEP
- `_v328_simple_heuristic_score` / `_v328_eval_safe` / `_v328_faza3_dwell` — substantive logic
- parser_health 4 utility funkcje (events_db / parsers / compute_downstream_status) — parser_health-specific
- Local variable names z `_v328_` prefix — naming convention
