# Opcja C — Implementation Plan

**Data:** 2026-05-07
**Cel:** rozdzielić queue vs audit log w event_bus, naprawić false-positive WORKER_STUCK, dodać retention
**Status:** AWAITING ACK (Adrian) przed pierwszym `cp .bak`

## Założenia (ACKed Adrianem)

- **Retention audit_log:** 90 dni (2× learning_analyzer/r04_evaluator window)
- **Legacy migration:** jednorazowy `mark_processed` dla 11823 pending → status='processed' (NIE move do audit_log; insert-only retroactively bez sensu, prościej oznaczyć jako historical-processed i pozwolić cleanup je usunąć w 48h via processed_events retention)
- **Tooling:** aider/deepseek dla bulk refactor 12+ call sites (mechaniczny rename)

## Architektura docelowa

```
events.db
├── events                    QUEUE table — lifecycle pending → processed
│                             types: NEW_ORDER, COURIER_PICKED_UP, COURIER_DELIVERED
│                             consumers: shadow_dispatcher, sla_tracker
├── processed_events          DEDUP cache (48h retention, istniejące)
└── audit_log (NEW)           AUDIT table — append-only, retention 90d
                              types: COURIER_ASSIGNED, CZAS_KURIERA_UPDATED,
                                     PANEL_UNREACHABLE, ORDER_RETURNED_TO_POOL
                              consumers (read-only): learning_analyzer,
                                     parser_health_endpoint, r04_evaluator,
                                     sprint2_analysis
```

### Schema audit_log

```sql
CREATE TABLE audit_log (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    order_id TEXT,
    courier_id TEXT,
    payload TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_audit_log_type ON audit_log(event_type);
CREATE INDEX idx_audit_log_created ON audit_log(created_at);
CREATE INDEX idx_audit_log_order ON audit_log(order_id);
```

NIE ma `status` ani `processed_at` — to append-only.

## Sequence (granular gates per krok)

### Etap 0 — pre-flight (15min)
1. Backup events.db: `cp events.db events.db.bak-pre-opcja-c-2026-05-07`
2. `git status` clean check
3. Identyfikacja branch: `sprint-07-05-event-bus-opcja-c` (nowy branch z master)
4. Tag baseline pre-change: `pre-opcja-c-baseline-2026-05-07`
5. **STOP for ACK** przed Etap 1

### Etap 1 — schema + event_bus.py (CC SELF, ~45min)
**Why SELF:** business logic + signature design + architectural decision (NIE bulk edit).

Zmiany w `event_bus.py`:
- Add `AUDIT_EVENT_TYPES = {"COURIER_ASSIGNED", "CZAS_KURIERA_UPDATED", "PANEL_UNREACHABLE", "ORDER_RETURNED_TO_POOL"}`
- Add `_init_audit_log_table()` — DDL na startupie
- Add `emit_audit(event_type, order_id, courier_id, payload, event_id)` — INSERT-only do `audit_log`, idempotent z `INSERT OR IGNORE`
- Add `cleanup_audit_log(retention_days=90)` — DELETE FROM audit_log WHERE created_at < now-90d
- Modify `cleanup(retention_hours=48)` — pozostaje dla queue table

Modify w `shadow_dispatcher.py` (`V328_WORKER_STUCK` alert):
- Filter `event_bus.get_pending_count(event_types=QUEUE_EVENT_TYPES)` zamiast globalnego count
- Add `QUEUE_EVENT_TYPES` const

Tests:
- `test_event_bus_audit_log.py` (new): emit_audit insert, idempotency, cleanup retention, schema, no-status guarantee
- `test_event_bus_existing.py`: weryfikuj że queue path niezmieniony
- Update `test_v328_worker_stuck.py` jeśli istnieje (filter check)

**Gate:** `py_compile` + import check + tests PASS → STOP for ACK

### Etap 2 — refactor 12+ call sites (AIDER/deepseek, ~30min)
**Why AIDER:** mechaniczny rename `emit("COURIER_ASSIGNED", ...)` → `emit_audit("COURIER_ASSIGNED", ...)`, identyczna sygnatura, bulk edit.

Pliki:
- `panel_watcher.py` — 12 emit sites (wszystkie 4 typy)
- `dispatch_pipeline.py` — 1 emit site (CZAS_KURIERA_UPDATED pre-recheck)

AIDER prompt-spec:
> Replace every `emit(<TYPE>, ...)` call where `<TYPE>` ∈ {COURIER_ASSIGNED, CZAS_KURIERA_UPDATED, PANEL_UNREACHABLE, ORDER_RETURNED_TO_POOL} with `emit_audit(<TYPE>, ...)`. Keep arguments identical. Update import line if `emit_audit` not imported.

Post-aider manual review (zgodnie z feedback memory):
- grep verification że ALL 16 sites updated (grep `emit("COURIER_ASSIGNED"` powinno = 0)
- import statements correct
- żadne `emit("NEW_ORDER")` / `emit("COURIER_PICKED_UP")` / `emit("COURIER_DELIVERED")` NIE ruszone

**Gate:** grep counts + py_compile wszystkich plików + import check → STOP for ACK

### Etap 3 — audit consumers update (CC SELF, ~30min)
**Why SELF:** każdy consumer ma inną SQL/aggregation logic, judgment.

Pliki + zmiany:
- `learning_analyzer.py:460` — `WHERE event_type='COURIER_ASSIGNED'` w events → audit_log table
- `parser_health_endpoint.py:198,208` — counters z events → join events + audit_log lub osobne queries
- `r04_evaluator.py` — events.db queries → check które są audit-only typy
- `sprint2_analysis/sanity_checks.py` — sanity counts (dodać audit_log row count)

Tests:
- Każdy consumer test PASS po zmianie
- Smoke test: r04_evaluator generates report bez błędów

**Gate:** wszystkie consumer tests PASS + 1 manual smoke run każdego skryptu → STOP for ACK

### Etap 4 — daily cleanup timer (CC SELF, ~20min)
- Nowy systemd unit `dispatch-event-bus-cleanup.service` (oneshot) + `.timer` (daily 04:00 UTC, off-peak)
- ExecStart: `python3 -m dispatch_v2.event_bus_cleanup` — wywołuje `cleanup()` (48h processed) + `cleanup_audit_log(90)` + log to journal
- Tests: dry-run mode dla skryptu

**Gate:** systemd-analyze verify + manual `--dry-run` clean → STOP for ACK

### Etap 5 — legacy migration jednorazowy (CC SELF, ~10min)
**Critical step.** Jeden raz, irreversible bez restore z .bak.

```python
# scripts/migrations/2026-05-07_opcja_c_legacy_audit_mark_processed.py
# Dla wszystkich pending events typu z AUDIT_EVENT_TYPES:
#   UPDATE events SET status='processed', processed_at=now()
# Następnie cleanup() retention 48h je wymiata naturally w next timer fire.
```

Pre-condition checks:
- Backup .bak istnieje (Etap 0)
- Etapy 1-4 deployed + verified stable 1h
- pending count 11823 ± migracji żywych (snapshot count + delta tolerance)

**Gate:** Adrian explicit ACK na execution + post-migration verify (`SELECT status, COUNT(*) FROM events GROUP BY status` → expected: pending~50 (queue only), processed=21000+) → STOP for ACK

### Etap 6 — deploy + observability (CC SELF, ~30min)
- Restart `dispatch-shadow` (musi załapać `_init_audit_log_table` na startup)
- Restart `dispatch-panel-watcher` (musi załapać `emit_audit` import)
- Smoke test 5min: nowe COURIER_ASSIGNED event → audit_log insert ✓ + brak w events ✓
- WORKER_STUCK alert: oczekuj zero alerts w 30min (bo queue pending realnie <100)
- Monitor heartbeat: pending count `event_bus=pending:X` powinno być małe (queue-only)

**Gate:** 30min clean obs + 0 ERROR + worker-stuck zero firings → tag `opcja-c-deployed-2026-05-07`

### Etap 7 — sprint close (CC SELF, ~15min)
- Memory note (lekcja #76 candidate: dual-write hidden role / queue vs audit separation)
- Sprint close memory file
- Branch merge plan (master gate analogiczny do F2/F3/F4 — 3-day clean obs przed push)

## Total estimate

| Etap | Effort | Tool |
|---|---|---|
| 0 pre-flight | 15min | SELF |
| 1 schema + event_bus.py | 45min | SELF |
| 2 refactor 12+ call sites | 30min | AIDER |
| 3 audit consumers | 30min | SELF |
| 4 cleanup timer | 20min | SELF |
| 5 legacy migration | 10min | SELF (z explicit ACK gate) |
| 6 deploy + obs | 30min | SELF |
| 7 sprint close | 15min | SELF |
| **Total** | **~3h15min** | mix |

## Risk assessment

| Risk | Mitigation |
|---|---|
| Schema migration corruption | .bak Etap 0 + DDL `IF NOT EXISTS` |
| Refactor missed call site → emit do events table dla audit type | grep verification post-aider + tests |
| Consumer SQL niedostosowany do audit_log → broken historical reads | per-consumer test PASS gate Etap 3 |
| Deploy out-of-order (panel_watcher new code, event_bus old) | restart oba w jednym oknie + verify import |
| Legacy migration race z live emits | snapshot count tolerance + run o 04:00 UTC off-peak |
| WORKER_STUCK alert filter bug → real stuck zostaje invisible | dodatkowy test `test_worker_stuck_real_pending_fires_alert` |

## Rollback per etap

| Etap | Rollback |
|---|---|
| 1 | `git revert` event_bus.py changes; ALTER TABLE DROP audit_log (jeśli istnieje) |
| 2 | `git revert` refactor commit (call sites wracają do `emit`) |
| 3 | `git revert` consumer changes; consumers znów query `events` table (działa nadal bo legacy data tam jest) |
| 4 | `systemctl disable --now dispatch-event-bus-cleanup.timer` |
| 5 | `cp events.db.bak-pre-opcja-c-2026-05-07 events.db` + restart wszystkich event_bus consumers |
| 6 | restart deploy z poprzednim tagiem |

## Pending decisions Adriana przed startem

1. ✅ ACK Opcja C — POTWIERDZONY
2. ✅ Retention 90d — przyjęte (state if not)
3. ✅ Legacy migration jednorazowy mark_processed — przyjęte
4. ✅ Aider dla Etap 2 — przyjęte
5. **Timing rozpoczęcia:** dziś (07.05 wt rano, off-peak ~9-11 UTC) czy jutro?
6. **Branch strategy:** osobna `sprint-07-05-event-bus-opcja-c` z merge do master post-3-day obs (analogicznie do F2/F3/F4)?
