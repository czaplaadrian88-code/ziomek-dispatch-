# A360-E1 — mapa kompletności call graphu

Data: 2026-07-12 UTC. Branch:
reliability/a360-e1-durable-outbox. Status: SOURCE FROZEN / RUNTIME HOLD.

Znaczenie:

- TAK — element został spięty w source i ma dowód testowy.
- N-D — świadomie niedotknięty w tym sprincie; wiersz podaje konkretny powód i
  bramkę. N-D nie oznacza przeoczenia.
- TAK / HOLD — kontrakt lub intent jest kompletny, lecz wykonanie runtime jest
  zakazane do osobnego sprintu.

## Producers i lifecycle writers

| miejsce | rola | writer / consumer | dotknięte TAK/N-D | powód | test / dowód |
|---|---|---|---|---|---|
| panel_watcher:new_order | NEW_ORDER z panelu | producer envelope, queue emit, order_state | TAK | jeden envelope dla emit+state; legacy follow-up tylko OFF | static producer graph 17/16; OFF mutation; golden state |
| panel_watcher:panel_initial_assignment | pierwszy ASSIGNED | producer envelope, audit, state, plan/activation/audit intents | TAK | jawny source i created_at współdzielone przez wszystkie role | default consumer matrix; panel assignment cluster |
| panel_watcher:panel_diff_assignment | diff ASSIGNED | producer envelope, audit, state, plan/activation/audit intents | TAK | pełny handoff bez inline follow-up ON | static call graph; dependency/receipt tests |
| panel_watcher:panel_reassignment | reassign ASSIGNED | producer envelope, audit, state, plan/activation/audit intents | TAK | revision-qualified ID chroni kolejne wersje tego samego źródła | source revision test; producer graph |
| panel_watcher:packs_fallback_assignment | assignment z packs fallback | producer envelope, audit, state | TAK | ten sam helper i polityka co pozostałe assignment paths | producer graph; assignment cluster |
| panel_watcher:cold_start_assignment | cold-start assignment | producer envelope, audit, state | TAK | brak fallbacku now(); OFF parity zachowana | test_v315_cold_start_scan; producer graph |
| panel_watcher:disappeared_delivered | disappearance -> DELIVERED | producer envelope, audit, state, SLA/geocode/plan intents | TAK | stan przed downstream ACK | terminal cluster; consumer matrix |
| panel_watcher:disappeared_return | disappearance -> RETURNED | producer envelope, audit, state, plan intent | TAK | wspólny envelope i state dependency | terminal cluster; consumer matrix |
| panel_watcher:packs_ghost_delivery | ghost -> DELIVERED | producer envelope, audit, state, downstream intents | TAK | brak effect-before-commit w durable path | crash-window tests; producer graph |
| panel_watcher:reconcile_delivery | reconcile -> DELIVERED | producer envelope, audit, state, downstream intents | TAK | created_at jawny z obserwacji; brak hydracji | consumer matrix; rebuild/reconcile tests |
| panel_watcher:reconcile_return | reconcile -> RETURNED | producer envelope, audit, state, plan intent | TAK | jedna koperta i osobne receipts | consumer matrix |
| panel_watcher:panel_status_resurrection | ORDER_RESURRECTED | producer envelope, audit, state | TAK | legalna korekta po terminalnym statusie; retencja całej historii HOLD | synthetic lifecycle/correction; retention negative |
| panel_watcher:reconcile_pickup | reconcile -> PICKED_UP | producer envelope, audit, state, SLA/plan intents | TAK | SLA i plan nie dzielą state receipt | independent receipts; consumer matrix |
| panel_watcher:ground_truth_pickup | ground-truth PICKED_UP | producer envelope, audit, state, SLA/plan intents | TAK | jawny event time i osobne ACK | time/order tests; producer graph |
| panel_watcher:committed_time_update | CZAS_KURIERA_UPDATED | producer envelope, audit, state, plan intent | TAK | data-only mutation jest state-owned, plan po ACK | default consumer matrix; pre-recheck parity |
| panel_watcher:pickup_time_update | PICKUP_TIME_UPDATED | producer envelope, audit, state, plan intent | TAK | sprzężony event/payload bez now() fallback | default consumer matrix; producer graph |
| panel_watcher:panel_unreachable | audit bez ordera | producer envelope, zero intents | TAK | publish terminal_at=created_at dla pustej topologii | zero-consumer terminal/duplicate test |
| dispatch_pipeline:pre_proposal_recheck | data-only CK przed propozycją | producer envelope, audit, state, plan intent | TAK | jeden envelope dla audit i state; OFF legacy semantics | test_v3271_pre_proposal_recheck; static graph |
| czasowka_scheduler:evaluation | re-evaluation NEW_ORDER | producer envelope, shadow_dispatch intent | TAK | celowo bez drugiego order_state writera | default consumer matrix; static graph |
| parcel_assign:manual_assignment | ręczny ASSIGNED paczki | producer envelope, audit, state, activation intent | TAK | jawny czas/source/policy, bez panel-only plan intent | consumer matrix; static graph |
| parcel_lane_merge:status_inbox | PICKED/DELIVERED z inbox | producer envelope, audit, state, SLA/geocode intents | TAK | identyczny provenance DB/state; e.ts nie jest zgadywanym czasem | parcel lane tests; producer graph |
| parcel_lane_merge:snapshot | NEW paczki | producer envelope, audit, state, shadow intent | TAK | jeden owner stanu | parcel lane E2E; consumer matrix |
| parcel_lane_merge:snapshot_retirement | ORDER_CANCELLED paczki | producer envelope, audit, state | TAK | terminalny event przed usunięciem legacy recordu | parcel lane retirement; consumer matrix |
| reconciliation:auto_resync | DELIVERED/RETURNED catch-up | producer envelope przez reconcile_worker, audit, state, downstream | TAK | factory wywołana raz; observed_at jawny; koperta reużyta | reconciliation durable handoff |
| event_bus.emit / emit_audit | wspólny writer queue/audit | producer compatibility boundary | TAK | OFF deleguje byte/state/status legacy; ON wymaga koperty i tylko publish | OFF mutation; transactional publish |

## Durable store, state mutations i integralność

| miejsce | rola | writer / consumer | dotknięte TAK/N-D | powód | test / dowód |
|---|---|---|---|---|---|
| event_envelopes | kanoniczny immutable envelope | writer publish; reader all consumers/rebuild | TAK | explicit ID/time/source/policy/version/payload hash | envelope required/roundtrip tests |
| event_dedup_ledger | trwały dedup horizon | writer publish/compact; reader duplicate/retention | TAK | duplicate sprawdza cały ledger; horizon policy-enforced | changed/missing ledger mutations; retention contract |
| event_outbox | intent per consumer | writer publish/status CAS; reader claim/recovery | TAK | pełna canonical topology, dependency i retry contract | every-field duplicate negatives; publish rollback |
| event_consumer_receipts | ACK/status per consumer | writer CAS; reader dependency/recovery/retention | TAK | receipt nigdy nie jest współdzielony przez plan/SLA/state | independent receipt matrix; missing receipt mutation |
| event_consumer_attempts | attempt ownership/timeline | writer begin/finish/recovery; reader failure audit | TAK | exact attempt/owner/token i monotoniczny czas | lease boundary, stale worker, finish-time tests |
| event_failure_journal | trwały audit-only failure | writer record_failure/quarantine; reader DLQ listing | TAK | zredagowane klasy/kody; nigdy retencjonowany | audit-only failure; malicious marker absence |
| event_retention_policies | trwały kontrakt policy/horizon/capacity | jawny writer; runtime reader | TAK / HOLD | schema i walidacja kompletne; żadna policy runtime nie jest wybrana | register/load/horizon/capacity tests |
| order_event_reducer.py | pure lifecycle reducer | consumer envelope; writer zwraca nowy record | TAK | zero I/O/clock/external effect; stale ordering fail-closed | golden lifecycle, stale mutation, duplicate fence |
| state_machine.commit_durable_state_claim | jedyny owner order_state | writer orders_state + ACK | TAK | state write przed ACK, idempotentny crash retry | crash before/after state; two state events ordering |
| orders_state durable receipts | lokalny fence state effect | writer reducer; reader retry/rebuild | TAK | bounded capacity; obrót tylko po terminalnym DB ACK lub zatwierdzonej kompaktacji | receipt capacity/compaction tests |
| coordinator_activations z legacy reducer | dawny external effect w state path | legacy OFF writer | TAK | pure durable reducer już go nie wywołuje; osobny intent po state ACK | consumer matrix; golden durable primitive |
| plan callbacks w panel_watcher | external follow-up | legacy OFF writer; durable plan intent | TAK / HOLD | OFF zachowuje inline legacy; ON producer nie wykonuje callbacku | should_run_followups OFF/ON tests; plan ordering |
| SLA direct lifecycle apply | drugi historyczny state executor | legacy sla_tracker | TAK / HOLD | durable mode failuje głośno bez workera; osobny SLA receipt nie zapisuje state | no-worker/source static test; consumer matrix |
| waiting_at / assigned_check_ts / operational cursors | metadata nie-lifecycle | panel/SLA/state_machine writers | N-D | nie zmieniają statusu FSM; OFF pozostaje legacy; przyszłe durable metadata wymaga osobnego kontraktu | test_waiting_at_persist; broad DEFAULT/STRICT |
| gastro_edit address/coords | ręczna admin mutation sprzężonych pól | direct state writer | N-D | poza lifecycle i zatwierdzonym write-setem; brak zgody na zmianę semantyki admin | istniejące broad regression; future boundary gate |
| state_machine.delete_order / prune | fizyczna retencja JSON | legacy maintenance writer | N-D / HOLD | bez durable snapshotu/tombstone nie wolno uznać state historii za odtwarzalną | order_state retention negative tests |

## Downstream consumer topology

| miejsce | rola | writer / consumer | dotknięte TAK/N-D | powód | test / dowód |
|---|---|---|---|---|---|
| order_state / reduce_order_state | stan kanoniczny | idempotent consumer | TAK | jedyny state owner, dependency root | golden state primitive; crash-window tests |
| shadow_dispatch / assess_new_order | propozycja/re-evaluation | confirm_before_retry consumer | TAK / HOLD | osobny receipt; runtime worker nie istnieje | consumer matrix; no-worker assertion |
| auto_koord / consider_auto_koord | panel NEW_ORDER follow-up | confirm_before_retry consumer | TAK / HOLD | tylko panel source; osobny receipt po state ACK | consumer matrix |
| coordinator_activation / consider_coordinator_activation | ASSIGNED follow-up | confirm_before_retry consumer | TAK / HOLD | wyjęty z pure reducer; osobny receipt | matrix; dependency test |
| plan / plan_on_assignment | zapis/inwalidacja planu | confirm_before_retry consumer | TAK / HOLD | osobny receipt po state, per-consumer ordering | assignment matrix; plan serialization |
| plan / plan_on_pickup | pickup plan update | confirm_before_retry consumer | TAK / HOLD | nie dzieli ACK z SLA | matrix; independent receipts |
| plan / plan_on_delivery | delivery plan advance | confirm_before_retry consumer | TAK / HOLD | osobna topologia i payload | matrix; duplicate field mutations |
| plan / plan_on_removal | return/cancel/reject cleanup | confirm_before_retry consumer | TAK / HOLD | panel-only source contract | matrix; topology tests |
| plan / plan_on_committed_change | CK/pickup time invalidation | confirm_before_retry consumer | TAK / HOLD | data mutation po state ACK | matrix; ordering tests |
| assignment_audit / check_panel_assignment | panel assignment audit | confirm_before_retry consumer | TAK / HOLD | osobny receipt, tylko panel source | matrix |
| sla / record_pickup | SLA pickup | confirm_before_retry consumer | TAK / HOLD | osobny receipt, nie wykonuje state reducer | matrix; independent receipts |
| sla / record_delivery | SLA delivery | confirm_before_retry consumer | TAK / HOLD | osobny receipt i attempt | matrix; crash unknown contract |
| delivery_geocode / enrich_delivery_coordinates | geocode po delivery | confirm_before_retry consumer | TAK / HOLD | osobny external-effect receipt | matrix |
| actual durable worker loop | claim/effect/ACK orchestration | przyszły runtime writer | N-D / HOLD | jawnie zabroniony w tym sprincie; brak timer/backoff/policy/health | static test: no worker/run/main/policy enabled |

## Replay, rebuild, reconcile i readers

| miejsce | rola | writer / consumer | dotknięte TAK/N-D | powód | test / dowód |
|---|---|---|---|---|---|
| tools/rebuild_state_from_events.py | disaster rebuild | read envelopes/state intents; secure writer | TAK | tylko exact durable state topology i pure reducer | rebuild exact-envelope test; atomic writer mutations |
| reconciliation/phantom_detector.py | last-event reader | durable state-ACK reader | TAK | outbox gap bez state ACK nie udaje wykonanego eventu | phantom reader gap/ACK test |
| reconciliation/auto_resync.py | reconcile writer | durable producer | TAK | jedna koperta, brak clock fallback, hard cap zachowany | durable handoff test; reconcile cluster |
| reconciliation/reconcile_worker.py | factory injection boundary | producer adapter | TAK | przekazuje lazy canonical factory, niczego nie flippuje | static producer graph |
| replay_dead_letter.py | failure inspection | read-only durable journal reader | TAK | output zredagowany; brak replay execution | audit-only failure listing test |
| replay_failed.py | legacy failed replay | legacy events writer | N-D | nie konsumuje durable outbox; trwałe failures wymagają przyszłej policy/worker | E0 replay tests; no worker source assertion |
| event_retry.py | E0 retry metadata/policy primitives | legacy queue writer/reader | TAK / HOLD | prerequisite schema zachowany; automatic retry i selected policy nadal OFF/None | E0+E1 cluster; shared migration guard |
| audit readers learning/parser-health/R-04 | legacy audit history | read audit_log | N-D | legacy schema/behavior OFF bez zmian; durable execution nie jest aktywne | full DEFAULT/STRICT parity |
| cleanup legacy queue/audit | historyczna retencja | legacy deleter | N-D | durable tables mają osobny fail-closed compact; OFF legacy parity wymaga braku zmiany | retention tests; full regression |

## Migration, retention i release boundaries

| miejsce | rola | writer / consumer | dotknięte TAK/N-D | powód | test / dowód |
|---|---|---|---|---|---|
| migrations/event_retry_metadata.py | E0 prerequisite schema | synthetic mutator, dry-run reader | TAK / HOLD live | wspólny C40 guard przed connect i przed BEGIN | CLI hardlink + connection exact/hardlink parity |
| migrations/durable_event_outbox.py | E1 schema | synthetic mutator, checker | TAK / HOLD live | exact table/index DDL, FK data audit, transactional apply | CHECK/FK/PK/index/orphan negatives; apply twice |
| synthetic_target_guard.py | source/prep mutation boundary | oba migration CLI/connection | TAK | explicit /tmp sandbox, no symlink/hardlink/known-live alias | C40 tests dla obu migracji |
| require_schema hot path | tani runtime contract | publish/claim/policy readers | TAK | verify_data=False; brak full FK scan na hot path | foreign_key_check spy test |
| explicit checker/apply | pełny schema+data audit | prep/migration reader/writer | TAK / HOLD live | verify_data=True i redacted FK violations | orphan outbox/receipt tests |
| retention_candidates/compact bez state | non-state cleanup | durable deleter | TAK / HOLD policy | complete topology/status/terminal/CAS fail-closed | retention corruption/CAS tests |
| retention całej historii order_state | state rebuild safety | przyszły snapshot/tombstone owner | N-D / HOLD | delivered/cancelled może się odrodzić lub dostać nowe assignment | terminal/resurrected retention negative |
| flags.json / runtime switch | activation | integrator/FLIPMASTER | N-D | twardy zakaz sprintu; source constant pozostaje False | git diff/write-set; static no-policy test |
| live DB apply / policy row | runtime schema/data | przyszły migration owner | N-D | wymaga backupu, dry-run kopii i osobnego ACK | C40 fail-closed tests |
| deploy/restart/systemd/timer | runtime execution | integrator | N-D | brak ACK i poza branch/source | zero live operations |

## Podsumowanie mapy

Każdy bezpośredni produkcyjny emit w pięciu plikach ma envelope handoff:
panel_watcher 17, dispatch_pipeline 1, czasowka_scheduler 1, parcel_assign 1,
parcel_lane_merge 3. Każdy odpowiadający lifecycle state call ma ten sam
handoff: panel_watcher 16, dispatch_pipeline 1, parcel_assign 1,
parcel_lane_merge 3. Jedyny direct create_order_envelope znajduje się w lazy
helperze event_bus; reconcile_worker przekazuje ten helper do auto_resync.

Wszystkie pozycje wykonawcze bez TAK są jawnie N-D/HOLD, ponieważ wymagają
osobnego kontraktu biznesowego, migracji, workera albo deployu. Nie ma
niezmapowanego aktywnego durable consumera: runtime pozostaje OFF.
