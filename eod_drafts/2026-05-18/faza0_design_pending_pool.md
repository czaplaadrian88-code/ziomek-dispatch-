# Faza 0 — design komponentów (pending pool + observation)

**Data:** 2026-05-18 | Sprint: rolling late-binding | Status: DESIGN (przed ACK)

Scope Faza 0 = **czysta obserwacja**. Pula pending lustruje rzeczywistość;
`tentative_cid`=null, `frozen`=false; ścieżka produkcyjna 100% bez zmian.
Gate 0 dowodzi że pula jest wiernym lustrem PRZED dodaniem logiki w Fazie 1.

## Komponenty

### 1. `pending_pool.py` — NEW, persystencja (~150 L)
Wzorzec: `plan_manager.py` (atomic temp→fsync→rename, fcntl LOCK_EX/SH).
Storage: `dispatch_state/pending_pool.json` + `pending_pool.json.lock`.

Schema — flat dict keyed by oid:
```
{
  "<oid>": {
    "order_id":        str,
    "created_at":      ISO UTC,     # NEW_ORDER first_seen
    "pickup_ready_at": ISO UTC,     # pickup_at_warsaw → UTC
    "freeze_at":       ISO UTC,     # max(created, pickup_ready − FREEZE_LEAD_MIN)
    "tentative_cid":   str | null,  # Faza 1 wypełni; F0 = null
    "churn_count":     int,         # 0 w F0
    "frozen":          bool,        # false w F0
    "frozen_at":       ISO | null,
    "removed_reason":  str | null,  # assigned_in_panel|delivered|cancelled|stuck
    "updated_at":      ISO UTC
  }
}
```
API: `load_pool()`, `upsert_order(oid, **fields)`, `remove_order(oid, reason)`,
`get_active()` (non-frozen, non-removed), `compute_freeze_at(created, pickup_ready)`.
Pure library — zero importów z dispatch_pipeline/panel_watcher (one-way, jak plan_manager).

### 2. Konsument NEW_ORDER — piggyback na `shadow_dispatcher`
DECYZJA: NIE nowy serwis. `shadow_dispatcher` już konsumuje NEW_ORDER z event_bus
→ dodajemy 1 wywołanie `pending_pool.upsert_order(...)` w `process_event`,
flag-gated `ENABLE_PENDING_POOL`. Unika nowego serwisu + zarządzania offsetem
subskrybenta event_bus. Dedykowany serwis = ewentualnie późniejsza faza (YAGNI F0).

### 3. `pending_pool_sweeper.py` — NEW (~80 L) + systemd timer
`dispatch-pending-pool.timer` co 1 min (wzorzec `dispatch-czasowka`). Robi:
- **Reconciliation:** dla każdego aktywnego oid czyta `state_machine.get(oid)` —
  status w {assigned, picked_up, delivered, cancelled, ignored} → `remove_order`.
- **Observation:** loguje zlecenia które przekroczyły `freeze_at` (F0: NIC nie emituje).
- **Stuck guard:** oid w puli a `now > pickup_ready + 30min` bez removal → log STUCK
  (Gate 0 sygnał — pula nie czyści się).

### 4. Shadow log `pending_pool_log.jsonl` — append-only audyt
Wpisy: `{ts, action: upsert|remove|freeze_cross|stuck, oid, ...}`.

### 5. `common.py` — flaga `ENABLE_PENDING_POOL` (env-overridable, default False)

### 6. Testy
- `test_pending_pool.py` — atomic write, concurrent fcntl, compute_freeze_at,
  upsert/remove/get_active.
- `test_pending_pool_sweeper.py` — reconciliation matrix (każdy status → poprawny
  removed_reason), stuck detection.

## Gate 0 (3 dni shadow)
- każdy NEW_ORDER trafia do puli (pool ∩ panel-NEW == panel-NEW)
- każde zlecenie wychodzi z puli z ważnym `removed_reason`
- zero STUCK
- licznik aktywnej puli ≈ panel pending count (tolerancja lag panel_watcher)

## Routing implementacji
- `pending_pool.py`, `pending_pool_sweeper.py` → AIDER (persystencja/scheduler,
  wzorce `plan_manager`/`czasowka_scheduler` istnieją; bezpośredni dostęp aider).
- `shadow_dispatcher.py` edycja (LIVE krytyczny serwis), systemd unity, `common.py`
  flaga, wiring, deploy → SELF, per-krok ACK.

## Otwarte — do potwierdzenia przed implementacją
- `FREEZE_LEAD_MIN = 15` (constant w `compute_freeze_at`) — łatwo zmienić, ale lock.
- Czy `pending_pool.json` w scope backupu BX11 restic — TAK (rekomendacja, jak inne
  dispatch_state krytyczne).

## Kolejność implementacji (per-krok ACK)
1. `common.py` flaga + `pending_pool.py` (AIDER) + testy → py_compile+test
2. `pending_pool_sweeper.py` (AIDER) + testy
3. systemd unity (service+timer, disabled)
4. `shadow_dispatcher.py` upsert hook (SELF, .bak, py_compile, import)
5. commit+tag → deploy: flaga ON + enable timer + restart shadow → Gate 0 obs
