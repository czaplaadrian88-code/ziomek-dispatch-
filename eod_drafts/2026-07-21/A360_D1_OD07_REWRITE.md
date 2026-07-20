# A360 D1 — rewrite OD-07

Data: 2026-07-20/21. Base: `master@2c72bb4`. Cel: `engine/a360-d1-od07-rewrite`.
Autor: Codex. `model_tier=sol`, `effort=xhigh` (HARD R6, Alarm i dwa wpięcia);
dokładny wariant modelu nieatestowany przez interfejs CLI.

## Wynik i mapa kompletności

| Miejsce | Status | Co z D1 |
|---|---|---|
| `core/invariant_firewall.py` | PRZENIESIONE | Rozdział stanu fizycznego od wpływu decyzji, `EXEMPT_PREEXISTING` tylko przy nieworszącym, wersjonowanym kontrfaktyku. Nowe `rule_verdict.v3`; interwał wyłącznie physical possession→customer handoff; 35 normalnie, 40 tylko związany Alarm, `>40=PROHIBITED`; paczki zachowane. |
| `dispatch_pipeline.py` + `shadow_dispatcher.py` | PRZENIESIONE RAZEM | Jeden finalny hook i serializer/fallback v3; `enforcement=NONE`; brak mutacji werdyktu, score, best i planu. |
| flaga | PRZENIESIONE | `ENABLE_A360_D1_OD07_FIREWALL_EXEMPT_TRUTH`; fallback OFF, izolacja testów, fingerprint i lifecycle. OFF kieruje wprost do niezmienionego v1. |
| ready/picked/predicted/pickup-order heuristic starego D1 | ODRZUCONE | `picked_up_at`, food-ready, restaurant-exit, last-inside, klik, arrival i planowa dostawa nie są physical truth ani kontrfaktykiem. ON nie wykonuje nawet legacy ready-anchor. |
| D1-impact dla R27/SLA | ODRZUCONE | Poza OD-07; v3 nie przypisuje im fikcyjnej przyczynowości. |
| physical event/source/cohort, Alarm predicate, pre-decision oracle | UNBOUND/HOLD | Wymagane wersja+provenance+gate. Brak lub nazwana próba proxy daje `UNBOUND/HOLD`, nigdy fallback. `food_ready_age` jest `SEPARATE_UNBOUND`, próg `null`. |
| feasibility/route/plan/selection i H1 R6-HARD flip | N-D / osobna bramka | D1 jest obserwacyjny. Implementacja/enforcement pełnego INV-FEAS-R6-ONE-SOURCE wymaga osobnego H1 i decyzji ownera. |
| `flags.json=false` | UNBOUND integracyjnie | Świeży klon nie ma śledzonego `flags.json`; jedyny nośnik jest poza repo/live, którego zadanie zabrania dotykać. Kod i rejestr mają default `false`; CTO musi dodać klucz `false` w kontrolowanym kroku integracji przed jakimkolwiek flipem. |

Fałszywe bliźniaki checkera (pliki dotknięte wyłącznie hookiem/serializerem):
`N-D: auto_assign_gate.py — brak zmiany pozycji`; `N-D: core/selection.py — brak
zmiany selekcji`; `N-D: drive_min_calibration.py — brak zmiany pozycji`;
`N-D: tools/reassignment_forward_shadow.py — brak zmiany pozycji`;
`N-D: objm_lexr6.py — brak zmiany tie-break`; `N-D: core/candidates.py — brak
zmiany scoringu`; `N-D: scoring.py — brak zmiany scoringu`.

## Dowód ON≠OFF i testy

- OFF: serializowany v1 identyczny także przy podanym nowym evidence; brak nowych pól.
- ON bez zatwierdzonych eventów: v3 `physical_status=UNBOUND`, `status=HOLD`.
- ON z syntetycznym, wersjonowanym evidence: granice 35/40, Alarm, `>40`, klasy kuriera i brak wpływu `food_ready_age` pokryte.
e2e: `assess_order → final firewall → shadow_dispatcher._serialize_result` pokryte bez mutacji decyzji.

pozytywny-wplyw: ON↔OFF: OFF=v1 bajt-identyczny; ON≠OFF=v3 UNBOUND/HOLD zamiast proxy; korekta semantyczna, enforcement nadal NONE.

- Klaster: `47 passed in 2.16s` (tmp flags + collection-only log-dir shim; zero live I/O).
- `py_compile`, `git diff --check`, JSON i `flag_lifecycle_check.py`: OK.
- Mutation probe: `age<=baseline` zmienione na `>=` → test RED; po przywróceniu GREEN.
- Pełnej suity, merge, deployu, restartu ani flipa nie wykonano — zgodnie ze zleceniem.

## Ryzyko i rollback

ON będzie dziś celowo HOLD dla niepaczkowych R6, dopóki osobne adaptery eventów,
Alarmu i kontrfaktyku nie przejdą owner gate. Konsumenci muszą tolerować v1/v3.
Rollback: flaga OFF (hot reload po ACK) albo revert tego commita; brak migracji danych.
`.git` klonu roboczego jest read-only, więc commit wykonano w zastępczym świeżym
klonie, a przenośny bundle: `/tmp/engine-a360-d1-od07-rewrite.bundle`.
