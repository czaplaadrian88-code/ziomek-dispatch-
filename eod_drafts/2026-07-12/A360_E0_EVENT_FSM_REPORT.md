# A360-E0 EVENT-RELIABILITY-FSM — raport SOURCE / PHASE A

Data raportu: 2026-07-12 UTC. Branch: `reliability/a360-e0-event-fsm`.
Frozen base: `1cf6ae4bdc52223ff0accafdea5fdadd593c70cf`.

## Status bez nadinterpretacji

To jest wyłącznie stan SOURCE / PHASE A. Automatyczny retry, worker retry,
polityka wykonawcza, FSM enforcement i migracja runtime pozostają OFF. Nie było
deployu, restartu, flipa flagi ani zapisu do live state/log/DB. Branch ma twardy
HOLD przed ON i przed merge, dopóki poniższe blokery nie dostaną osobnego
projektu, testów oraz ACK.

## Semantic disposition starej Fazy A

- `7eda1b0` ma ten sam patch co obecny w frozen base `a384d46`: ALREADY PRESENT,
  VERIFY-CLOSE; bez cherry-picku i bez duplikowania symboli.
- `32745f9` ma ten sam patch co obecny w frozen base `c2bde58`: ALREADY PRESENT,
  VERIFY-CLOSE; bez cherry-picku i bez drugiej migracji.
- Nowa praca rozszerza istniejący kanon o atomowy failure transition, alias
  `next_retry_at`, sanitizację, jeden graf FSM, receipt stanu i narzędzia replay.

## Mapa kompletności

| miejsce | rola | writer / consumer | TAK / N-D | powód | test / dowód |
|---|---|---|---|---|---|
| `event_bus.py` | kanoniczny zapis queue/audit, emit, processed/failed | writer: panel, parcel, reconciliation; consumer API: shadow/SLA/replay | TAK | idempotency digest, jawny owner state effect, legacy `mark_processed` default oraz strict opt-in | `test_event_bus_*`, `test_event_retry_phase_a`, `test_event_reliability_fsm_e0` |
| `event_retry.py` | atomowy failure transition, retry/DLQ, aliasy, klasyfikacja | writer metadanych; reader due/stats/replay | TAK | OFF zawsze `failed`; ON wymaga policy+policy ID; extended SQLite code normalizowany do primary code | focused matrix OFF/ON, retry limit, alias, extended BUSY/LOCKED |
| `migrations/event_retry_metadata.py` | addytywna migracja event store | writer wyłącznie test/ACK DB; dry-run reader | TAK | transakcja, idempotencja, backfill idempotency i `next_retry_at`, conflict HOLD, zredagowany CLI | dry-run byte parity, apply twice, alias due golden, conflict rollback, secret-marker CLI |
| `order_fsm.py` | jeden graf `from + event -> to` | pure validator używany przez state i replay | TAK | `TRANSITION_RULES` generuje `FORMAL_FSM_EVENT_TYPES`; legal/reconcile/correction w jednym kanonie | golden history, illegal, timestamp, graph/event-bus parity |
| `state_machine.py` | legacy reducer, observer OFF, enforcement/receipt SOURCE | writer `orders_state`; consumer formal FSM | TAK (SOURCE) | OFF zachowuje legacy i fail-open read; ON jest fail-loud, ale zablokowany przez HOLD | byte parity OFF, observer read mutation, duplicate receipt, concurrent/crash tests |
| `panel_watcher.py` | rodzina lifecycle panelu | writer NEW/ASSIGNED/PICKED/DELIVERED/RETURNED oraz data-only CK/pickup | TAK | wszystkie emit→state pary prowadzą przez `apply_state_event`; OFF follow-up parity zachowana | panel families: assignment, terminal, ghost, cold-start, CK/pickup, waiting |
| `dispatch_pipeline.py` | pre-recheck data-only CK | writer audit + state effect | TAK | historyczny state write niezależny od audit INSERT zachowany przez `emitted=True` | `test_v3271_pre_proposal_recheck`, `test_pre_recheck_before_pool_k07` |
| `parcel_assign.py` | ręczny assignment paczki | writer ASSIGNED | TAK | OFF używa legacy API; SOURCE ON idzie przez wspólnego ownera | `test_parcel_assign` |
| `parcel_lane_merge.py` | NEW, status inbox, retirement paczki | writer NEW/PICKED/DELIVERED/CANCELLED | TAK | DB i state dostają identyczny provenance payload; `e.ts` tylko w event ID, nigdy timestamp | parcel E2E, identical payload, no-timestamp, ORDER_CANCELLED writer |
| `reconciliation/auto_resync.py`, `reconcile_worker.py` | terminal catch-up | writer DELIVERED/RETURNED; consumer state effect | TAK | wspólny adapter, dedup i dotychczasowy fail-soft błędu state zachowane | reconciliation, dry-run, transient self-heal, hard-cap |
| `shadow_dispatcher.py` | realny consumer `NEW_ORDER` | reader queue; writer processed/failed | TAK | przekazuje typed exception, output failure bez raw ID/text | broad targeted consumer paths; mark failure privacy tests |
| `sla_tracker.py` | realny consumer PICKED/DELIVERED | reader queue; writer SLA i processed/failed | TAK (SOURCE) | wspólny state adapter i bezpieczna klasyfikacja; dual-consumer ON pozostaje HOLD | poison/TZ test, failure sanitization, broad targeted |
| `replay_failed.py` | legacy failed replay | reader failed; jawny status flip | TAK | raw ID wyłącznie w pamięci do apply; output agregat/digest; logging/stdout/stderr sink; atomic 0600 no-follow | malicious markers, channel restore, permission/symlink/ancestor/hardlink |
| `replay_dead_letter.py` | DLQ inspect/requeue | reader DLQ; writer requeue | TAK | allowlist reason, rehash ref, metadata sanitization, formal FSM snapshot, unknown type fail-closed | list privacy, correction snapshot, corrupt type remains DLQ |
| `tools/rebuild_state_from_events.py` | twin offline rebuild | consumer utrwalonego envelope | TAK | przekazuje `event_id` i `created_at` do kompatybilnego reducer API | broad targeted + FSM/rebuild contract review |
| queue readers `get_pending`, `stats`, `cleanup` | kolejka i obserwowalność | shadow/SLA/health/cleanup | TAK | nowe statusy i `next_retry_at` są addytywne; legacy path pozostaje kompatybilny | event-bus cleanup, mirror, stats/retry tests |
| audit readers: learning/parser-health/R-04 | historyczne SELECT-y audit | consumers audit log | N-D | schema `audit_log` niezmieniona; brak nowego pola wymaganego przez readerów | audit schema/role tests; write-set ich nie dotyka |
| serializer/log/CLI output | evidence boundary | event bus, FSM observer, shadow, SLA, oba replay i migracja | TAK | tylko zamknięte klasy/kody/allowlist lub `unknown`; raw exception/ID/payload nie trafia do nowego outputu | secret/malicious marker tests i C25 channel test |
| bliźniaki: legacy/canonical retry time, queue/audit, panel/parcel/reconcile, live/rebuild, OFF/ON | parity cross-path | wszyscy powyżsi | TAK / HOLD ON | oba aliasy aktualizowane razem; call-site family spięte; ograniczenia ON jawnie w sekcji HOLD | alias mutation, EVENT_TYPES parity, byte parity OFF, broad targeted |
| `gastro_edit.py`, waiting/SLA metadata, plan callbacks | bezpośrednie pola nie-lifecycle | state metadata writers / downstream follow-up | N-D | nie zmieniają statusu przez nowy FSM; plan callbacks nie są w write-set; ich ON atomicity jest opisana jako HOLD | istniejące waiting/recanon/panel broad targeted |
| live flags/state/DB/log/systemd/backlog/memory | runtime i wspólne artefakty | produkcja | N-D | zakres zabrania zmian live i współdzielonej pamięci; wykonano zero operacji | status operacyjny poniżej |

## Dokładny write-set

Kod i narzędzia:

- `dispatch_pipeline.py`
- `event_bus.py`
- `event_retry.py`
- `migrations/event_retry_metadata.py`
- `order_fsm.py`
- `panel_watcher.py`
- `parcel_assign.py`
- `parcel_lane_merge.py`
- `reconciliation/auto_resync.py`
- `reconciliation/reconcile_worker.py`
- `replay_dead_letter.py`
- `replay_failed.py`
- `shadow_dispatcher.py`
- `sla_tracker.py`
- `state_machine.py`
- `tools/rebuild_state_from_events.py`

Testy:

- `tests/test_assignment_lag_fix.py`
- `tests/test_event_bus_audit_log.py`
- `tests/test_event_retry_phase_a.py`
- `tests/test_event_reliability_fsm_e0.py` (nowy)
- `tests/test_parcel_lane_merge.py`
- `tests/test_reconcile_dry_run.py`
- `tests/test_task2_czesc_a_panel_diff_terminal_emit.py`
- `tests/test_v315_cold_start_scan.py`
- `tests/test_v3271_pre_proposal_recheck.py`
- `tests/test_v328_replay_failed.py`
- `tests/test_waiting_at_persist.py`

Raport:

- `eod_drafts/2026-07-12/A360_E0_EVENT_FSM_REPORT.md` (nowy)

## Zachowanie domyślne i migracja

- `record_failure(enabled=False)` dla transient/permanent/illegal kończy jako
  historyczne `failed`, bez retry/DLQ, terminu i policy ID. Klasyfikacja jest
  tylko metadanymi.
- Retry/DLQ wymaga `enabled=True` oraz jawnych `RetryPolicy` i `policy_id`.
  Żadna opcja polityki nie jest wybrana; worker nie istnieje.
- `mark_processed()` domyślnie zachowuje historyczny kontrakt na legacy i po
  migracji. Restrykcyjny CAS jest osobnym opt-in przyszłego consumera retry.
- Migracja jest addytywna, idempotentna i testowana wyłącznie na bazach
  tymczasowych. Dry-run raportuje backfill aliasu i konflikty. Różne terminy
  `next_attempt_at`/`next_retry_at` oznaczają fail-loud HOLD; zero live apply.
- Parcel inbox zapisuje provenance `parcel_status_inbox` identycznie w DB i
  state envelope. Pole `e.ts` pozostaje wyłącznie częścią event ID: format,
  jednostka i znaczenie czasu są niezatwierdzone, więc nie jest mapowane na
  pickup/delivery timestamp i nie ma fallbacku `now()` w enforcement ON.

## Twarde HOLD przed ON / merge

1. `fsm_idempotency_keys` w `orders_state` jest nieograniczoną listą. Brak
   zatwierdzonego limitu, kompaktowania i dowodu zachowania dedupu po retencji.
2. Custom idempotency istnieje w `events`; po retencji rekordu jego ochrona
   znika. Trwały zakres i retention contract wymagają decyzji.
3. Content-hash fallback nie jest wystarczającą tożsamością. ON wymaga jawnego,
   kanonicznego `event_id` od każdego writera zamiast zgadywania z treści.
4. `COURIER_ASSIGNED`, `ORDER_RETURNED_TO_POOL`, `ORDER_CANCELLED` i data-only
   są audit-only. Gdy FSM je odrzuci, `mark_failed` nie ma wiersza `events`, więc
   nie powstaje trwały DLQ/receipt, a kolejny tick może odrzucać ponownie.
   Potrzebny jest failure journal albo zatwierdzona zmiana queue/audit.
5. Receipt jest atomowy tylko z `orders_state`. Legacy reducer
   `COURIER_ASSIGNED` uruchamia `coordinator_activations.activate` przed locked
   upsert. Przy race/`ConcurrentOrderEvent` efekt zewnętrzny może zajść bez
   zmiany stanu i bez receipt. ON wymaga pure state reducer + outbox albo osobnej
   transakcyjnej obsługi efektów.
6. Audit-only eventy nie mają workera gwarantującego replay po crashu pomiędzy
   zapisem audit a state effect. Nie ma podstaw do claimu exactly-once dla tego
   call graphu.
7. `stale_event` opiera się na `event.created_at`, lecz produkcyjne call-site'y
   budują state event bez tego pola. `event_bus` nadaje inny timestamp wewnątrz
   INSERT i zwraca tylko ID. Golden testy z ręcznym `created_at` nie dowodzą
   live ordering. Przed ON potrzebny jest jeden kanoniczny envelope DB+state
   albo hydratacja z trwałego rekordu oraz mutation starego, różnego event ID;
   nigdy fallback `now()`.
8. PICKED_UP/DELIVERED mają dwóch wykonawców tego samego state receipt: inline
   producer i `sla_tracker`. Gdy SLA wygra race, inline może dostać duplicate i
   pominąć plan follow-up; gdy inline wygra, SLA nadal potrzebuje własnych
   follow-upów. Jeden globalny receipt nie jest acknowledgement per consumer.
   Potrzebny jest pojedynczy owner stanu oraz per-consumer receipts/outbox albo
   rozdzielenie state effect od follow-up acknowledgement. Obecny crash-window
   test nie modeluje dwóch konsumentów.
9. Powyższe oznacza wprost: SOURCE chroni pojedynczy atomiczny zapis state, ale
   nie zapewnia exactly-once całego call graphu ani kompletnego retry systemu.

## Prywatność evidence

Failure metadata i CLI zwracają wyłącznie zamknięte klasy/kody oraz techniczny
digest `event_ref`. Digest nie jest pseudonimizacją ani ochroną low-entropy PII.
Surowe event/order/courier ID, payload, reason, exception text i traceback nie
wchodzą do nowego outputu replay ani tego raportu.

## Walidacja

- Frozen baseline DEFAULT integratora na tym base, 2026-07-11
  23:05:55Z–23:10:40Z: 5143 passed, 24 skipped, 8 xfailed, 0 failed/XPASS,
  147 warnings, 281.88 s. Znana zmienność to zegarowy self-skip
  `test_preshift_window`; ocena po liście, nie samej sumie.
- Focused po finalnych blockerach: 72 passed, 0 failed.
- Szeroki targeted po ostatnim review blockerze: 427 passed, 1 xfailed,
  0 failed w 45.53 s.
- Finalny DEFAULT i STRICT wykonano kolejno, bez przerwania, pod jednym
  wyłącznym `flock` na `/tmp/ziomek_full_regression.lock`. Lock był zajęty od
  2026-07-12T00:44:30Z do 2026-07-12T00:53:35Z.
- DEFAULT: UTC 2026-07-12T00:44:30Z–00:49:15Z; load average start
  `0.74 0.98 1.16`, end `1.68 1.42 1.31`; **5189 passed, 24 skipped,
  8 xfailed, 0 failed/XPASS, 147 warnings, 283.94 s**. Log 0600:
  `/tmp/a360_e0_default_20260712T004430Z_3222632.log`.
- Wobec frozen baseline DEFAULT: +46 passed, bez zmiany 24 skipów, 8 xfailów,
  147 warnings oraz bez failed/XPASS. Lista 24 skipów pozostała ta sama:
  `test_cod_weekly_preflight.py:23`; `test_cod_weekly_split_week.py:18`;
  `test_v325_step_a_r02.py:28`; `test_v325_step_c_r04.py:25`;
  `test_canon_static_check_a1.py:117`; `test_cod_weekly.py:91,167,442`;
  `test_ml_twomodel.py:311,317,323,332,343,351,375,384,389,485,621,645`;
  `test_mp11_jsonl_appender.py:95`; `test_parser_v2_property_based.py:147`;
  `test_scoring_scenarios.py:24`; `test_v3273_wait_courier.py:197`.
  Zegarowy `test_preshift_window` nie self-skipnął; nie wystąpił więc znany
  wariant +3 pass/-3 skip.
- Lista 8 xfailów również pozostała ta sama:
  `test_daily_stats_presnapshot.py::script_run`;
  `test_demote_tier_bucket_p4.py::test_offmode_preserves_demote_across_tiers`;
  cztery sloty w `test_invariant_slots_l04.py`
  (`test_inv_src_equal_treatment_pre_shift_twin_parity`,
  `test_inv_life_loadplan_pure_default`, `test_inv_coh_r_declared_tripwire_exists`,
  `test_inv_layer_hard_before_soft_reassert_after_readmit`);
  `test_reconcile_dry_run.py::script_run` oraz
  `test_v319d_read_integration.py::script_run`.
- HERMETIC_STRICT=1: UTC 2026-07-12T00:49:15Z–00:53:35Z; load average start
  `1.68 1.42 1.31`, end `2.17 1.90 1.53`; **5139 passed, 74 skipped,
  8 xfailed, 0 failed/XPASS, 147 warnings, 258.67 s**. Log 0600:
  `/tmp/a360_e0_strict_20260712T004430Z_3222632.log`.
- STRICT zachował wszystkie 24 skipy i wszystkie 8 xfaile DEFAULT. Jego
  dokładne +50 skipów to oczekiwana izolacja hermetyczna: po 1 w
  `test_eta_residual_infer`, `test_flag_registry_f3`,
  `test_geo05_district_adjacency`, `test_prep_bias`,
  `test_prep_variance_anomaly_fail04`, `test_roadfactor_gap`,
  `test_route_order_live_parity`, `test_v325_pin_leak_defense`; 2 w
  `test_health_all_aggregator`; 9 w `test_pin_gps_commands`; 14 w
  `test_r04_v2_evaluator`; 3 w `test_state_schema_validator`; oraz 14 w
  `test_working_override_2026_06_01`. Xfail listy DEFAULT/STRICT mają ten sam
  digest SHA256 `958132a7c024e48522fa29bf70b23e88a689a93c268e979f4d75913fada65aa5`.
- Powyższe UTC, load i wyniki są rekordem sensitivity dla at-214; samego joba
  at-214 nie odczytywano, nie zmieniano ani nie uruchamiano.
- `git diff --check`: 0 błędów. `py_compile` 16/16 zmienionych modułów i import
  check 16/16 przeszły w testowym środowisku, z bytecode kierowanym do `/tmp`.
- Checkery read-only: flag lifecycle 505/505 i 0 błędów; flag hygiene 0 sierot;
  flag-effect i flag-doc bez nowej luki; kanon 0 naruszeń, a wszystkie 10 sond
  mutation selftest zostały zabite.
- `tools/entropy_dashboard.py` wykonano po fundamencie: wartości AUTO to 1
  rozjazd flag oraz 11 sentinel sites (+4 instrument); pozostałe metryki są
  oznaczone przez narzędzie jako historyczny `AUDIT-BASELINE`. Sprint nie dodaje
  nowej flagi ani sentinela i nie przypisuje sobie spadku metryk bez re-oracle.
- Dodatkowy globalny `tools/devlint/ratchet_check.py` zwrócił exit 1:
  Ruff 661 przy historycznym baseline 608, choć mypy poprawił się 124→116.
  Diagnostyka całego jawnego write-setu znalazła 12 naruszeń Ruff; porównanie
  tych samych plików przez `git show` na frozen HEAD wykazało identyczne 12
  kodów i miejsc (po zmianie jedynie przesunięte linie), a nowy test ma 0.
  Jest to zastany globalny dryf ratchet baseline poza deltą E0, nie nowa
  regresja sprintu; baseline'u ani kodu po zaakceptowanym review nie zmieniano.

Środowisko wszystkich testów: `ZIOMEK_SCRIPTS_ROOT=/root/a360_e0_wt`,
`PYTHONPATH=/root/a360_e0_wt`, `DISPATCH_UNDER_PYTEST=1`; wyłącznie venv
`/root/.openclaw/venvs/dispatch/bin/python`. Testy używały tmp state/DB/log i nie
dotknęły produkcyjnego state/log/DB.

## Handoff wykonawczy

- Worktree: `/root/a360_e0_wt/dispatch_v2`.
- Branch: `reliability/a360-e0-event-fsm`.
- Frozen base: `1cf6ae4bdc52223ff0accafdea5fdadd593c70cf`.
- Commit implementacyjny, utworzony wyłącznie jawnymi pathspecami:
  `b2a602755a101a991074d06d3c4e819b09531c3e`
  (`fix(events): harden retry and FSM source contracts`).
- Ten raport trafia do osobnego commitu docs-only. Jego SHA jest z definicji
  ustalany dopiero po zapisaniu treści tego pliku; finalny `HEAD=origin` i pełny
  SHA są weryfikowane po pushu i przekazane w `FINAL_READY`, bez zgadywania
  samoreferencyjnego hasha w raporcie.
- Po commicie implementacyjnym jedyną zmianą worktree oczekującą na commit jest
  ten jawny raport docs-only.
- Carrier flags: `/root/a360_e0_wt/flags.json`, kopia 0444, SHA256
  `568436f3de693d048a73bf1a2ba5c23e65191a350ef2e566fa5b6f34ace848bf`;
  niezmienione.
- Efektywne stałe SOURCE: `AUTOMATIC_RETRY_ENABLED=False`,
  `SELECTED_RETRY_POLICY_ID=None`, `ORDER_FSM_OBSERVER_ENABLED=True`
  (log-only), `ORDER_FSM_ENFORCEMENT_ENABLED=False`; default
  `mark_processed(..., retry_consumer_enabled=False)`.
- Worker retry: nie istnieje / OFF. Polityka wykonawcza: żadna nie jest wybrana.
  Migracja runtime: niewykonana.
- Operacje live: zero deployów, restartów, flipów flag, migracji, zapisów state,
  zapisów log i zmian DB. PID/NRestarts/health po deployu: N-D, bo deployu nie
  było.
- Backup danych: nie tworzono, ponieważ nie było żadnego live write ani
  migracji. Addytywną migrację wykonywano tylko na bazach tymczasowych.
- Zastany dirty main
  `eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md` pozostał nietknięty.
  Osobno chroniony `daily_accounting/kurier_full_names.json` także pozostał
  nietknięty; nie ustalono ani nie deklaruje się, że był dirty. Bez stash,
  restore, stage ani nadpisania obu ścieżek.
- Bootstrapowa kontrola ownerów: tmux72 to ten worktree; tmux71 SEC0 i tmux73
  DATA0 miały rozłączne zakresy. Nie stwierdzono kolizji write-setu.
- Wspólny backlog, memory, systemd i at job 214 nie były edytowane. Merge/live
  pozostają zabronione przed werdyktem job 214 i rozwiązaniem HOLD.

## Rollback

Enforcement i worker pozostają OFF. Najpierw kill-switch/stałe pozostają OFF,
potem rollback kodu to jawny
`git revert b2a602755a101a991074d06d3c4e819b09531c3e`; commit raportu jest
docs-only i może być revertowany osobno. Addytywnego schematu nie należy cofać;
legacy reader pozostaje kompatybilny. DLQ/quarantine evidence nie może być
kasowane. Brak rollbacku backupu danych, ponieważ nie było live migracji ani
live write. Restart order i smoke po rollbacku są N-D bez deployu; przyszłe
wydanie wymaga osobnego ACK, kontrolowanego restartu właściwego procesu oraz
health/fingerprint/smoke zgodnie z ETAP 6–7.
