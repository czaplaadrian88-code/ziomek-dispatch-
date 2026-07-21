# CZASOWKA-RECLAIM-SHADOW — evidence 2026-07-21

## Integracja v2 z masterem 95fff2e4

- Aktualny, czysty `master` repo live został odczytany bez mutacji i
  zaimportowany do tymczasowego gitdiru przez bundle:
  `95fff2e4d8c3eac2fd2dcbfcd272ec1c486dad14` (manifest v26, baseline
  `5691 passed, 0 failed`).
- `7e15cde53783441c839d9cc854947897e884b737` scalono na branchu
  `reclaim-merge`. Git nie zgłosił żadnego konfliktu tekstowego; lista
  konfliktów jest pusta.
- Semantyka obu stron pozostała obecna: upstreamowe guardy resweep
  (`status == planned` sprawdzany pod lockiem oraz telemetria
  `proposed_km/r6/spread`) i manifest v26 są zachowane, a komplet 23 ścieżek
  reclaimu znajduje się w wyniku scalenia.
- Pełna suita, wynik samo-recenzji i finalny SHA/bundle są dopisywane po
  zamrożeniu wyniku integracji.

## Zakres i decyzja

- Base: `323034299fbba20a2fb33a45819e26c91f10a27a`, clone `/root/cx-reclaim`.
- Model tier/effort: `sol` / `xhigh` (wariant dokładny nieatestowany); zakres ma
  durable outbox, przyczynowość i przyszłą granicę write.
- Kontrakt: `SPEC_czasowka_reclaim_2026-07-21.md` + pełny werdykt Sola.
- Tylko SHADOW. Nie wykonano deployu, restartu, flipu, odczytu/mutacji runtime,
  zapisu gastro ani planu. `ENABLE_CZASOWKA_RECLAIM_{SHADOW,LIVE}` są OFF.

## Spełnienie siedmiu wymagań

1. `czasowka_reclaim.evaluate_pickup_time_updated` stosuje dokładnie
   `old<=observed+60`, `new>=observed+60+5`, `new>old`. `60` pochodzi z jednego
   `common.CZASOWKA_PREP_MIN`, a `5` z env-overridable
   `CZASOWKA_RECLAIM_HYSTERESIS_MIN`. Receipt `created_at` ma pierwszeństwo
   przed fallbackiem replayu.
2. Jedyny wiring jest w durable downstream `PICKUP_TIME_UPDATED`, po
   `state_machine.update_from_event`. `drain_pending` odzyskuje tylko unresolved
   receipts; nie ma tick-scanu ani użycia `CZAS_KURIERA_UPDATED`.
3. Guard wymaga literalnego `status == "assigned"`, realnego CID innego niż
   `26`/None, `picked_up_at is None` i braku statusu terminal/picked. Każdy
   wynik guarda jest w rekordzie.
4. `COURIER_ASSIGNED` atomowo utrwala `assignment_event_id` i
   `pickup_at_at_assignment`; emitter czasu utrwala migawkę generacji i CID.
   Ewaluacja wymaga tej samej generacji i niezmienionego kuriera. Jawne pola
   `reclaim_exempt`/`reclaim_exempt_reason=manual_time_hold` są persistowane;
   brak heurystyki autora.
5. Osobny JSONL zawiera oid/cid, old/new/delta, oba progi, komplet guardów,
   rejection reason, firmowe/paczkę i pełną przyszłą akcję. Reader/CLI agreguje
   `would_reclaim` idempotentnie per `(oid,generacja)`. Stub LIVE ma stabilne
   ID tej samej intencji; state event atomowo zapisuje `planned`, `26`, previous
   CID, generation, reclaimed_at i reason. Brak producenta/callera LIVE.
   `LIVE_DOWNSTREAM_REQUIREMENTS` projektuje: lock+CAS, staged/live gastro z
   zachowaniem pickup, read-back przed retry, plan release+plan_version CAS,
   pending `locked_pop`, rekey legacy+proactive, limit/circuit breaker.
6. Obie flagi mają const-fallback OFF, ETAP4, wpis lifecycle i dokumentację.
   SHADOW jest `shadow`, LIVE `planned`; rollback to false/brak klucza.
7. Test syntetyczny obejmuje: dalekie `+15` NIE, `-20→+45` NIE, `+50→+80`
   TAK, progi równe TAK, sekundę poza każdym progiem NIE, mutation-probe znaku
   histerezy, picked-up/status, assignment po evencie, hold, zmieniony kurier,
   ON≠OFF, `(oid,generacja)`, firmowe/paczki oraz uśpioną semantykę eventu.

## Mapa kompletności ETAP 3

| miejsce | rola | writer/consumer | dotknięte | powód | test/dowód |
|---|---|---|---|---|---|
| `common.py` | próg, klasyfikator, flagi | writer kontraktu | TAK | jeden kanon 60 + histereza/flag OFF | boundary + flag tests |
| `panel_watcher._diff_pickup_time` + manual CK twin | event source | writer payloadu | TAK | snapshot generation+CID; stabilny event key | emitter test + pickup suite |
| `durable_event_apply` | outbox/recovery | writer receiptu | TAK | receipt-bound flagi i trwały observed_at | crash/recovery test + C3 |
| `state_machine` NEW/ASSIGNED | granica pól | writer | TAK | default exemption; assignment generation+pickup snapshot | assignment test |
| `lifecycle_downstream` | efekt po state | consumer | TAK | wyłącznie idempotentny SHADOW JSONL | wiring test |
| `czasowka_reclaim.py` + report | oracle/metryka | consumer+writer | TAK | pełne guardy, JSONL, distinct generation | 21 testów |
| `event_bus`/`order_fsm`/`event_effect_status`/handler | stub eventu LIVE | twins | TAK | allowlist + legal edge + CAS/no-op OFF | dormant event test |
| JSONL appender/rotation/logrotate | trwałość logu | boundary | TAK | append-once + rotacje/reporter | MP11 cluster |
| firmowe i paczki | decyzja scope | consumers | TAK | wykluczone z future LIVE, osobne liczniki | metric test |
| `plan_recheck` | active bag/R6 | consumer stanu | N-D | SHADOW nie zmienia state; LIVE CAS/release w osobnej karcie | kod: ACTIVE assigned/picked |
| `sla_tracker` | R6 | consumer stanu | N-D | SHADOW nie zmienia state; LIVE planned/26 nie jest picked | kod `get_by_status(picked_up)` |
| apka kuriera + konsola | render/plan version | consumer | N-D | brak state/plan write w SHADOW; refresh należy do LIVE | werdykt + brak callera |
| legacy scheduler | klasyfikacja + eval state | consumer | TAK/N-D | używa wspólnego 60; rekey generation+pickup dopiero LIVE | scheduler cluster |
| proactive scheduler/state | dedupe T-50/T-40 | consumer | N-D | rekey generation+pickup jawnie zaprojektowany, zakaz write teraz | `LIVE_DOWNSTREAM_REQUIREMENTS` |
| pending/global allocation | propozycje | consumer | N-D | `locked_pop`/cleanup dopiero LIVE | brak importu/callera |
| gastro staged/live | zewnętrzny writer | writer | N-D | jawnie zabroniony w tym etapie | grep: brak callera |
| packs/cold-start | twins/recovery | consumer | TAK/N-D | paczki liczone osobno; cold-start tylko unresolved outbox, bez scan | recovery test |

Mechaniczne N-D dla map bliźniaków skilla (zmiany w gate/watcherze są tylko
centralizacją stałej albo źródłem eventu; SHADOW nie zmienia decyzji/planu):

- N-D: `core/selection.py` — brak zmiany rankingu/wyboru; tylko wspólny próg w classifierze.
- N-D: `dispatch_pipeline.py` — brak zmiany feasibility/scoring/serializacji decyzji.
- N-D: `drive_min_calibration.py` — brak zmiany cech i kalibracji pozycji.
- N-D: `tools/reassignment_forward_shadow.py` — brak zmiany równego traktowania pozycji.
- N-D: `plan_recheck.py` — SHADOW nie zmienia statusu/CID/planu; LIVE ma osobny CAS/release.
- N-D: `route_order.py` — brak zmiany kanonu kolejności i stopów.
- N-D: `route_podjazdy.py` — brak zmiany kanonu podjazdów/display.

## ETAP 0–7 i testy

- E0: clone był czysty; base/branch/worktree sprawdzone. Host runtime i kanoniczny
  venv są niedostępne w sandboxie. Baseline hermetyczny: `121 passed`; osobny
  runner authority: `24 passed`.
- E1–E2: root cause = brak reakcji durable po legalnej zmianie pickup; HARD
  state/causality poprzedza eligibility i metrykę.
- E3: mapa powyżej. Nie zmieniono sygnatur publicznych; grep fake'ów N-D.
- E4: `py_compile` PASS; `git diff --check` PASS; lifecycle checker PASS;
  brak callera LIVE; append-once i recovery potwierdzone. E2E: realny
  `PICKUP_TIME_UPDATED -> durable outbox -> state -> downstream crash -> drain
  unresolved -> applied receipt` przechodzi i zachowuje ten sam observed_at.
- E5: ON↔OFF: OFF nie tworzy pliku ani rekordu; ON tworzy kompletną ewaluację,
  retry tego lifecycle eventu nie duplikuje JSONL, a drugi event tej samej
  generacji nie zwiększa `would_reclaim`. To pozytywny efekt celu SHADOW;
  decyzja/state pozostają identyczne. Flaga pozostaje OFF; wymagane okno
  obserwacji po ewentualnym flipie SHADOW: >=2 dni, potem karta częstości/FP.
- E6: brak merge/deploy/restart/flag flip; pełna suita i operacje live należą
  do CTO zgodnie ze zleceniem.
- E7: rollback: oba klucze `false`/brak; następnie `git revert <commit>` z
  bundle i ponowny test klastra. Brak migracji danych, restartu ani rollbacku
  danych.

Wyniki po zmianie (system Python + hermetyczny PYTHONPATH, bez OR-Tools):

- reclaim + FSM + return seam: `142 passed`;
- durable C3: `143 passed`;
- JSONL: `39 passed, 1 skipped`;
- scheduler/classifier/gates: `83 passed, 1 deselected`; wyłączony test
  subprocessu potwierdzony ręcznie `False|24|10` (sandbox blokuje jego
  hard-coded `/root/.openclaw/.../logs`);
- pickup detection: `14/14`; reclaim: `21/21`;
- flag checker `ok=true`, 0 errors. Temp re-seed `--merge` przerwano bez zmian
  po długim skanie; rejestr zwalidował checker.

Regresja klastra: `475 passed, 0 failed, 1 skipped` (+1 test subprocessu
sprawdzony równoważnym hermetycznym importem; w pliku pytest deselected).
To nie jest pełna suita: kanoniczny venv jest niedostępny w sandboxie, a pełną
suitę owner jawnie przydzielił CTO przed merge.

Linie wejściowe mechanicznej bramki (zakres pozostaje jawnie klastrowy):

regresja: klaster 475 passed, 0 failed, 1 skipped; pełna suita = CTO przed merge
e2e: durable PICKUP_TIME_UPDATED -> state -> crash downstream -> recovery tylko unresolved -> receipt applied
pozytywny-wplyw: ON zapisuje deduplikowany would_reclaim, OFF ma zero I/O i oba tryby nie zmieniają decyzji/state
rollback: flagi=false/brak kluczy, git revert commitu z bundle; brak migracji danych i restartu

Pełna suita, merge, ewentualny flip SHADOW i restart: CTO. LIVE pozostaje
oddzielnym etapem wymagającym karty i ACK ownera.
