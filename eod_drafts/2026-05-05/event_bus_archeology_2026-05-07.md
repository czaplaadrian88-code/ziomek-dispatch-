# Event Bus Design Archeology — Krok 1

**Data:** 2026-05-07 rano
**Trigger:** P2 worker_stuck investigation, V328_WORKER_STUCK alert false positive
**Method:** Read-only diagnostic, no code changes

## Stan faktyczny events.db

| event_type | pending | processed | Najstarszy pending |
|---|---|---|---|
| COURIER_ASSIGNED | 9587 | 0 | 2026-04-11 |
| CZAS_KURIERA_UPDATED | 2217 | 0 | 2026-04-26 |
| PANEL_UNREACHABLE | 15 | 0 | 2026-04-26 |
| ORDER_RETURNED_TO_POOL | 4 | 0 | 2026-05-04 |
| NEW_ORDER | 0 | 5611 | — |
| COURIER_PICKED_UP | 0 | 4629 | — |
| COURIER_DELIVERED | 0 | 10260 | — |

## Kluczowy insight — events.db ma DWA role mieszane w jednej tabeli

### Rola 1: QUEUE (z lifecycle pending→processed)
| Consumer | Konsumuje | mark_processed |
|---|---|---|
| shadow_dispatcher | NEW_ORDER | TAK |
| sla_tracker | COURIER_PICKED_UP, COURIER_DELIVERED | TAK |

### Rola 2: AUDIT LOG (append-only, query historyczne by event_type)
Nikt nie konsumuje queue-style. Dual-write pattern:
- `event_bus.emit()` zapisuje do events.db
- `state_machine.update_from_event()` wywoływane **synchronicznie inline** z tego samego call site (NIE przez event_bus polling)

Czytelnicy historyczni (NIE wywołują mark_processed):
- `learning_analyzer.py:460` — TIMEOUT_SUPERSEDED analysis (`WHERE event_type='COURIER_ASSIGNED'`)
- `parser_health_endpoint.py:198` — health counters
- `r04_evaluator.py` — per-courier 30d metrics
- `sprint2_analysis/sanity_checks.py` — sanity counts

### Dual-write call sites (12 × COURIER_ASSIGNED + 3 × CZAS_KURIERA_UPDATED + …)
Każdy z tych miejsc robi `emit(...) + update_from_event(...)` synchronicznie:
- `panel_watcher.py:495, 546, 591, 617, 650, 681, 791, 917, 984, 1009, 1073, 1141`
- `dispatch_pipeline.py:165` (CZAS_KURIERA_UPDATED pre-recheck)

## Co to znaczy operacyjnie

1. **NIE ma "missed processing"** — state_machine już zaktualizowane synchronicznie inline w momencie emit.
2. **`pending=11800` to architectural debt** — 4 typy są audit-only, "pending" status semantically mylny.
3. **`event_bus.cleanup()` istnieje** (retencja 48h dla processed_events) — **0 callers w całym kodzie**.
4. **`V328_WORKER_STUCK` alert** mierzy globalny `pending` count → false positive **zawsze** (alert pali nawet gdy worker NEW_ORDER bezbłędnie procesuje).

## Decision matrix — 3 ścieżki naprawy

### Opcja A — semantic fix (status='audit' lub immediate processed)
- Emit dla 4 audit-only typów ustawia status='processed' od razu (lub nowy 'audit')
- WORKER_STUCK alert mierzy tylko queue-typy
- Schedule cleanup() jako daily timer

**Effort:** ~1h. **Risk:** niski. **Z3 fit:** mediocre — ukrywa intencję, "processed" semantyka mylna; nie rozdziela ról.

### Opcja B — pełny event-driven model (usunąć dual-write)
- Worker konsumuje async, wywołuje `update_from_event` z poll loopa
- Usuń synchronous `update_from_event` calls (12+ miejsc w panel_watcher)
- Każdy event_type ma dedicated consumer

**Effort:** ~6-8h + intensive testing. **Risk:** WYSOKI — race conditions, ordering side-effects (np. `_save_plan_on_assign` natychmiast po assign), state_machine update opóźnione vs decyzja w pipeline. **Z3 fit:** czysta architektura ale za drogie ryzyko biznesowe.

### Opcja C — rozdzielenie ról: queue vs audit log (REKOMENDACJA Z3)
- **Nowa tabela `audit_log`** w events.db — append-only, INSERT-tylko, retention TTL (np. 90 dni)
- **Tabela `events`** zostaje dla queue typów: NEW_ORDER, COURIER_PICKED_UP, COURIER_DELIVERED (z lifecycle)
- `event_bus.emit_audit(...)` — nowa funkcja dla audit typów
- Refactor 12+ call sites: zmiana z `emit(...)` na `emit_audit(...)` dla 4 typów
- `update_from_event` inline NIE rusza się — pozostaje synchronous (zero ordering risk)
- Migracja: starsze 11800 pending events → przesunąć do audit_log lub mark processed jednorazowo
- Cleanup audit_log scheduler (daily timer, retention 90d)
- WORKER_STUCK alert sample tylko events queue table → mierzony właściwie
- learning_analyzer / r04_evaluator / parser_health → query audit_log (trivial 1-line change)

**Effort:** ~3-4h. **Risk:** średni — wymaga schema migration + 12+ call sites edit + testy. **Z3 fit:** ✅ rozdziela role, nie ukrywa, nie zmienia ordering, multi-tenant ready.

## Rekomendacja

**Opcja C.** Z2 supremacja — robimy raz porządnie. Nie band-aid (A), nie ryzykowny pełny refactor (B). Krok 2 (decision matrix) wymaga ACK Adriana zanim ruszymy z planem implementacji.

## Open questions dla Adriana
1. ACK Opcja C czy preferowana inna ścieżka?
2. Retention audit_log: 90 dni? (learning_analyzer 30d + r04_evaluator 30d window — 90 dni daje 2x bezpieczeństwa)
3. Migracja istniejących 11800 pending events: do audit_log + status='audit' czy `cleanup` mark_processed jednorazowo?
4. Czy aider/deepseek do bulk edit 12 call sites w panel_watcher (post design ACK)? Klasyczny mechaniczny refactor.
