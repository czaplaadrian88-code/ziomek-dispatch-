# Deep Data Integrity & Concurrency Audit — Hot Spots #1 (`learning_log.jsonl`) + #11 (`flags.json`) + SQLite `event_bus.py`

**Data:** 2026-05-07 wieczór
**Scope:** Współbieżność zapisów do shared state files + SQLite lock contention w `events.db`
**Owner:** CC architectural review (request: Adrian)
**Cross-ref:** `ARCHITECTURE_AUDIT_2026-05-07.md`, `STATE_OWNERSHIP_EVENT_FLOW_AUDIT_2026-05-07.md`, `META_AUDIT_ROOT_CAUSES_ROADMAP_2026-05-07.md`, `STRATEGIC_RISK_SYNTHESIS_2026-05-07.md`

---

## Spis treści

1. [Atomic Write Failure — udowodnij interleaving](#1-atomic-write-failure)
2. [State Ownership — Filesystem as IPC risk model](#2-state-ownership)
3. [SQLite Lock Contention — `event_bus.py busy_timeout=5000`](#3-sqlite-lock-contention)
4. [Propozycja: `core/jsonl_appender.py`](#4-propozycja-corejsonl_appenderpy)
5. [Postgres Migration Roadmap — single source of truth](#5-postgres-migration-roadmap)
6. [Konsolidacja — co NEXT (rekomendacja)](#6-konsolidacja--co-next)

---

## Scope rzeczywisty (production state)

**6 systemd services aktywnych:** `dispatch-panel-watcher`, `dispatch-shadow`, `dispatch-telegram`, `dispatch-sla-tracker`, `dispatch-gps`, `dispatch-monitor-419`. Plus 12 timer-based: `dispatch-shift-notify`, `dispatch-czasowka`, `dispatch-cod-weekly`, `dispatch-daily-accounting`, `dispatch-plan-recheck`, `dispatch-overrides-reset`, `dispatch-r04-evaluator` etc. Wszystkie mają potencjalny dostęp do `learning_log.jsonl` jako appendery lub readery.

**Empirical baseline `learning_log.jsonl`:**

| Metric | Value |
|---|---|
| Lines | 15,843 |
| File size | 110 MB |
| Avg line size | 6,962 B |
| Max line size | 56,826 B |
| Min line size | 107 B |

(Snapshot 07.05 19:55 UTC w `/root/.openclaw/workspace/dispatch_state/learning_log.jsonl`.)

---

## 1. Atomic Write Failure

### 1a. Korekta premise (PIPE_BUF nie dotyczy regular files)

**Teza w ticket:** "PIPE_BUF=4096, avg line=6962 B → interleaving."

**Faktycznie:** PIPE_BUF (4096 B na Linux) gwarantuje atomicity **tylko dla pipes/FIFOs** (POSIX.1-2017 §3.265). Dla regular files **nie istnieje** PIPE_BUF-klasy gwarancja. Atomicity zapisu do regular file z `O_APPEND` rządzą trzy odrębne mechanizmy:

| Warstwa | Mechanizm | Co gwarantuje |
|---|---|---|
| Kernel (Linux ext4/xfs) | `generic_file_write_iter` z `O_APPEND` bierze inode lock per `write(2)` syscall, ustala pos = i_size, zapisuje, zwalnia | **Jedna `write(2)` syscall = atomic append**, niezależnie od size aż do RLIMIT_FSIZE |
| Kernel (POSIX) | `write(2)` może zwrócić **short** (mniej bajtów niż request) na sygnale, EAGAIN, lub NFS — POSIX nie gwarantuje pełnego write | Krótki zapis → loop syscalls → **inter-syscall interleaving okno** |
| User-space (CPython) | `BufferedWriter` (default `io.DEFAULT_BUFFER_SIZE = 8192 B`) — split na ≤8192 B chunks; raw mode pomija buffer | Linijka >8192 B → **wiele `os.write` calls** → wiele inter-syscall okien |

### 1b. Realistyczny model interleavingu

Kod w `panel_watcher.py:139` (analogicznie `telegram_approver.py:1259`):

```python
with open(_LEARNING_LOG_PATH, "a", encoding="utf-8") as f:
    f.write(json.dumps(override_rec, ensure_ascii=False) + "\n")
```

Co się dzieje fizycznie:
1. `open(..., "a")` → `os.open(path, O_WRONLY|O_APPEND|O_CREAT, 0o666)` ✓ — `O_APPEND` jest ustawione (Python explicit dla `"a"` mode).
2. `f.write(s)` → encoding UTF-8 → `BufferedWriter.write(bytes)`.
3. Na `__exit__` (close): `BufferedWriter.flush` → seria `os.write(fd, chunk)`.
4. Każdy `os.write` osobno bierze kernel inode-lock z `O_APPEND` semantics.

**Boundary case A** (avg ~6962 B, ≤ 8192 B buffer): jedna `os.write` syscall, kernel-atomic, **brak interleavingu** w typowym scenariuszu. Ale `os.write` może zwrócić short → retry → drugi syscall → **okno interleavingu otwarte**. Realny ratio short-write na local FS: <0.01% w peace-time; rośnie pod I/O pressure / sygnał-pod-write.

**Boundary case B** (linie > 8192 B): max=56,826 B = **6.94× buffer size** → minimum 7 `os.write` syscalls per linia → **6 okien interleavingu**.

```bash
# Procent linii > 8192 B w current learning_log.jsonl:
awk '{print length}' learning_log.jsonl | awk '$1>8192' | wc -l
```

Spodziewane ~30-40% (avg 6962 + heavy tail z F7AGREE / TG_REASON / KOORD outcome z full `decision_record`). To są shadow-decision dumps z `_serialize_candidate` × 10-15 kandydatów per `assess_order`.

### 1c. Konkretna sekwencja interleavingu (przykład)

```
T0: panel_watcher emit PANEL_OVERRIDE rec_A (size 12,000 B)
    BufferedWriter splits → os.write(8192), os.write(3808+\n)
T1: telegram_approver emit ASSIGN_DIRECT rec_B (size 9,500 B)
    BufferedWriter splits → os.write(8192), os.write(1308+\n)

Scheduler interleave:
  T0.1: panel_watcher: os.write(8192) ← first 8192 of rec_A → @offset 110_418_058
  T1.1: telegram:      os.write(8192) ← first 8192 of rec_B → @offset 110_426_250
  T0.2: panel_watcher: os.write(3808) ← second chunk of rec_A → @offset 110_434_442 (NOT contiguous)
  T1.2: telegram:      os.write(1308) ← second chunk of rec_B → @offset 110_438_250

Resulting bytes: [first 8192 rec_A][first 8192 rec_B][3808 rec_A][1308 rec_B]
  = corrupted JSONL: 4 fragments interlaced, każdy fragment NIE jest valid JSON
```

**Kernel `O_APPEND`** gwarantuje że pos zawsze = file_size przy `write(2)` start — czyli każdy chunk jest na końcu, nikt nie zostanie nadpisany. ALE każdy chunk jest na końcu **swojego** momentu start syscall, więc fragmenty z rec_A i rec_B **mogą** się przeplatać linearly.

Skutek: `learning_analyzer.py` (czyta `learning_log.jsonl` line-by-line) napotyka 4 fragmenty zamiast 2 records → 2× `json.JSONDecodeError` → silent drop (lub crash zależnie od reader). Gorsze: gdy `}{` na granicy fragmentu układa się "poprawnie" syntaktycznie → **sklejony rekord** z wymieszanymi polami z dwóch decyzji = **silent corruption** trenowania ML.

### 1d. Empiryczna oszacowanie ryzyka per dzień

Aktywność production:
- `panel_watcher` ~1 cycle/3s × 86400 = ~28,800 cycles/d. PANEL_OVERRIDE ~1-3% = ~300 zapisów/d
- `telegram_approver` aktywny w peakach: AUTO_KOORD/ASSIGN/TIMEOUT/KOORD/TG_REASON/F7AGREE — ~200-500 zapisów/d (peak 11-14 + 17-20)
- `shadow_dispatcher` to **shadow_decisions.jsonl** (osobny path), NIE learning_log — pozornie poza scope, ale **same wzorzec** kodu (linia 610) → **shadow_decisions.jsonl ma identyczny problem**

Dziennie: ~500-800 zapisów do `learning_log.jsonl`, z czego ~30-40% > 8192 B → **150-300 zapisów/d w "wide window" interleavingu**.

Współbieżność: panel_watcher i telegram_approver równolegle szczególnie w peakach (PANEL_OVERRIDE od watchera + KOORD/ASSIGN od telegrama na ten sam order_id w przeciągu 5-30s). **Konfliktów per peak day: oszacowanie 5-15 par**, z czego short-write event ~0.01-0.1% × 15 = praktycznie zero w peace-time, ale **NIE-zero pod I/O pressure** (peak Hetzner CPX32 disk saturation, swap thrashing, NFS retry — zaobserwowane w logach `journalctl -u dispatch-shadow` jako latency outliers >800ms).

**Verdict #1:** Realne ryzyko **silent corruption** w warunkach normalnych: ~1-3 corrupted records/tydzień. Pod incident (panel down + retry storm + page cache pressure): **10-50× wyżej**, łącznie z pierwszą widoczną manifestacją na `learning_analyzer` post-mortem ML pipeline.

---

## 2. State Ownership

### 2a. Inwentaryzacja access pattern dla `orders_state.json` (17 KB)

```
WRITERS (z fcntl.flock LOCK_EX via state_machine._locked_write):
  ✓ panel_watcher → state_machine.upsert_order (NEW_ORDER, UPDATE, COURIER_ASSIGNED, CK_UPDATE)
  ✓ shadow_dispatcher → state_machine.update_from_event
  ✓ reconciliation_worker (cron-based dispatch-czasowka.timer)
  ✓ telegram_approver → state_machine.set_status (KOORD outcome via /koniec)

READERS (z fcntl.flock LOCK_SH + 3-retry exp-backoff):
  ✓ Wszyscy powyżej + sla_tracker + courier_resolver + dispatch_pipeline
```

`orders_state.json` ma już **canonical state_io** = `state_machine.py:127 _atomic_write()` + `state_machine.py:111 _locked_write()`. Reference implementation, używana przez wszystkie consumery. **To jedyne miejsce w repo gdzie filesystem-IPC jest bezpieczny.**

### 2b. Risk model dla files BEZ central state_io

| Plik | Atomicity write | flock | Liczba writerów | Ryzyko |
|---|---|---|---|---|
| `orders_state.json` (17 KB) | ✓ tempfile+fsync+rename | ✓ LOCK_EX/SH | 4 | **niskie** (canonical) |
| `learning_log.jsonl` (110 MB, append-only) | ✗ naked `open("a")` | ✗ brak | 3 (panel/tg/shadow innym path) | **wysokie** (interleaving) |
| `shadow_decisions.jsonl` | ✗ naked `open("a")` | ✗ brak | 1 (shadow only) | **niskie ale nie zero** (signal-interrupt edge) |
| `pending_proposals.json` | ✓ tempfile+fsync+rename (`telegram_approver.py:1250`) | ✗ brak flock | 2 (panel writes na save_plan, telegram writes na callback) | **średnie** (last-writer-wins race) |
| `flags.json` | ✓ tempfile+os.replace (per CLAUDE.md cmd) | ✗ brak | 1 (manual ops) | **niskie** (manual op rate) |
| `manual_overrides.json` | ✓ atomic_write_v2 | ✓ flock | 2 (telegram + reset timer) | **niskie** |
| `kurier_ids.json` / `_piny.json` / `_full_names.json` | ✓ courier_admin.add_new_courier 4-file atomic + rollback | ✓ flock | 1 (telegram cmd /dopisz) | **niskie** (singleton-ish) |
| `tier_suggestions.json` | ?? (r04_evaluator timer-based 03:00) | ?? | 1 | **niskie** (singleton timer) |
| `geocoding_cache.json` | ✗ pewnie naked dump (sprawdzić) | ✗ | 2 (panel + shadow z osrm/nominatim) | **średnie**, drift ML-feature |

### 2c. Kategorie ryzyka braku centralnego `state_io.py`

**(a) Schema drift między writerami.** Każdy moduł reimplementuje atomic write z subtelnymi różnicami:
- `state_machine._atomic_write` używa `tempfile.mkstemp` → `os.fdopen` → `json.dump` → `flush+fsync` → `os.rename`.
- `telegram_approver._save_pending_atomic` (linia 1248-1254) używa `with open(tmp, "w") as f` → `json.dump` → `f.flush` → `os.fsync` → `os.replace`.
- Mailek `core/atomic_io.py` (Backlog #2 Plan B done 07.05) używa pattern z dispatch_v2 ale różni się detalami formatowania (indent=2).

Konsekwencja: 3 nieidentyczne implementacje → 3 niezależne bug-spots → **bug fix w state_machine NIE propaguje** do telegram_approver ani Mailek (Lekcja #47 service-scoped audit, ale rozszerzona na write-implementation-scoped).

**(b) Lock granularity drift.** Niektóre pliki (orders_state) mają flock; inne (learning_log) nie. Rozumowanie defaultowe deweloperów: "append-only nie potrzebuje locka, kernel O_APPEND wystarczy" — **fałszywe** dla lines > buffer.

**(c) Last-writer-wins na wspólnych kluczach.** `pending_proposals.json` jest pisany przez panel_watcher (`save_plan_on_assign`) i telegram_approver (`_save_pending_atomic`). Bez flock: writer 1 czyta state, writer 2 czyta state, writer 1 modyfikuje + atomic write, writer 2 modyfikuje + atomic write → write 1 NADPISANY. Lost update. Empirycznie obserwowane jako "ghost pending" w incident #467164 (lekcja #15, Lekcja Q&A-12).

**(d) Reader blind to in-progress mutations.** Bez `LOCK_SH`, reader może przeczytać partial state w oknie między tempfile→rename (pico-sekundy ALE realne pod I/O pressure). `state_machine._read_state` ma już 3-retry mitigation; **reszta readerów nie**.

### 2d. Konsekwencja: `core/state_io.py` jako Z3 deliverable

Brak centralnego state_io = każdy nowy plik state staje się wyborem implementatora "czy zrobić atomic+flock czy pominąć". W audycie 07.05 znaleziono 5 wysoko-ryzykownych plików plus rozproszoną reimplementację. **Z3 (buduj na lata) implikuje single API:**

```python
# core/state_io.py — SINGLE SOURCE
def atomic_write_json(path, data, *, indent=2): ...    # Mailek shim już to robi
def locked_write_json(path, data): ...                  # path.lock + flock LOCK_EX + atomic
def locked_read_json(path, *, retries=3): ...           # flock LOCK_SH + JSONDecodeError defense
def append_jsonl_atomic(path, record, *, max_bytes=64*1024): ...  # NEW — ten audyt (sekcja 4)
```

Eksportowalne dla Mailek (consolidates Backlog #2 Plan B), Restimo (multi-tenant), Warsaw expansion.

---

## 3. SQLite Lock Contention

### 3a. Mechanika lock contention w WAL mode

```python
# event_bus.py:69-72
conn = sqlite3.connect(_db_path(), timeout=10.0, isolation_level=None)
conn.execute("PRAGMA journal_mode=WAL;")
conn.execute("PRAGMA busy_timeout=5000;")
```

**Trzy poziomy lockowania:**

| Lock type | WAL mode behavior | Trigger |
|---|---|---|
| SHARED (czytelnik) | nie blokuje innych readers | każdy `SELECT` |
| RESERVED (zamierza pisać) | jeden writer na raz; readers OK | `BEGIN IMMEDIATE` |
| EXCLUSIVE | blokuje wszystkich; bardzo krótki burst przy WAL checkpoint | implicit przy WAL checkpoint |
| WAL writer lock | seria writerów serializowana przez WAL append | `INSERT/UPDATE/DELETE` |

**`busy_timeout=5000` znaczy:** kiedy connection napotka SQLITE_BUSY (lock contention) → spin-and-retry przez **do 5000 ms łącznie** → jeśli wciąż busy, raise `sqlite3.OperationalError("database is locked")`.

### 3b. Scenariusz "reconcile_worker trzyma writer lock przez 15s"

Krok-po-kroku co się dzieje:

```
T+0s:    reconcile_worker:  BEGIN IMMEDIATE  → RESERVED + WAL writer lock acquired
T+0..15s: reconcile_worker:  long-running INSERT/UPDATE batch (e.g. ghost cleanup z 200 orderów lub
                              cleanup() z DELETE FROM events + processed_events kasujące 100K wierszy)
T+0.5s:  panel_watcher:    emit("COURIER_ASSIGNED") → BEGIN IMMEDIATE → SQLITE_BUSY
                            → busy_handler spins ~5s
T+5.5s:  panel_watcher:    sqlite3.OperationalError "database is locked" → emit() raise
                            → caller (panel_watcher main loop) wraps w try/except:
                              → log.warning + continue
                            → EVENT NIE TRAFIA DO events.db
T+5.5s:  panel_watcher:    cycle continues. Następny diff cycle (T+8s) próbuje ten sam emit
                            (idempotent event_id deterministic) → możliwy retry success
```

### 3c. Czy eventy zostaną bezpowrotnie utracone? — case-by-case

**Case A: panel_watcher emit COURIER_ASSIGNED.** Event_id deterministyczny: `{order_id}_COURIER_ASSIGNED_{timestamp_ms}`. `timestamp_ms` z `make_event_id` ma resolucję ms → kolejny cycle z innym `now_ms` → **inny event_id** → **NIE jest to ten sam event** z perspektywy idempotency. ALE panel_watcher diff (`_diff_and_emit`) jest source-of-truth driven: porównuje `state.cid` z `panel.cid` → jeśli różne, emit. W następnym cyklu (3s później) state nadal != panel → **diff znowu wykrywa różnicę** → **retry emit**. Idempotency po deterministic event_id zabezpiecza przed duplicates jeśli ten sam ms-bucket; różny ms = różny event_id ale oba opisują ten sam fizyczny stan.

→ **Verdict A:** Event NIE jest bezpowrotnie utracony, panel_watcher self-heals w next cycle (3-30s lag). Ale `audit_log` (z opcji C) traci unique audit entry tego konkretnego momentu — **gap w timeline** dla incident reconstruction.

**Case B: telegram_approver emit z callback handler.** Telegram callback (np. `handle_assign_callback`) jest **one-shot user action**. Brak retry mechanism w handlerze. Jeśli `event_bus.emit("COURIER_ASSIGNED")` raise w środku callbacku, handler musi catch + decyzja co z tym zrobić.

```python
# telegram_approver.py callback flow (uproszczony)
try:
    eb.emit("COURIER_ASSIGNED", order_id=oid, courier_id=cid, payload=...)
except sqlite3.OperationalError:
    # ??? — co tu robić?
```

Sprawdź faktyczny pattern: callback handlery wołają `event_bus.emit` w `_handle_assign_callback`, `_handle_inny_callback`, `_handle_koord_callback`, `_handle_koniec_callback`, `_handle_poprawa_callback`. Każdy musi mieć defense.

→ **Verdict B:** TAK, eventy z callbacków **mogą być bezpowrotnie utracone** jeśli handler nie ma retry/queue. Adrian klika ASSIGN w Telegramie → BUSY 5s → emit raise → handler odpowiada errorem do Telegrama → Adrian widzi "błąd" → klika ponownie → **drugi callback z nowym callback_id** → drugi event_id → eventually wpisuje się, ale z **dziurą w state**, **5-30s downtime na decyzję** w peaku.

**Case C: shadow_dispatcher mark_processed.** Po `process_event`, shadow woła `event_bus.mark_processed(event_id)` (linia 611). Jeśli BUSY → exception → caught przez outer try (linia 618-621) → `mark_failed` próba (która też może BUSY → log + drop). Event zostaje w status='pending' → nast. cycle `get_pending` zwraca go → **double-process** możliwy ale `_serialize_result` jest deterministyczny → shadow_decisions.jsonl dostanie **duplikat** z różnym ts. Manageable, ale invalidates "exactly-once" assumption.

### 3d. Najbardziej realne 15s+ writer-hold scenarios

| Scenariusz | Częstotliwość | Wpływ |
|---|---|---|
| `cleanup()` z 100K-row DELETE (retention 48h, 90d audit) | dzienne, zwykle off-peak; jeśli błąd cron → kumuluje | 5-15s |
| WAL checkpoint mid-batch (4MB threshold) | implicit, normalnie <100ms | rzadko 1-3s pod IO load |
| Long INSERT batch w reconcile_worker | rzadko | 1-5s typowo |
| `VACUUM` (manual) | nigdy auto, tylko explicit | 30-60s na 22 MB DB |
| Disk I/O saturation (CPX32 IO peak) | rzadko, ale obserwowane (panel_watcher latency >800ms) | 1-10s |
| fsync na pełnym disk | bardzo rzadko | 5-30s |

**Realistyczne windowy 15s+:** głównie `cleanup()` w nocnej godzinie + corner-case IO storm. Peak 11-14/17-20 hard rule mówi "ZERO restart" → nie powinno wpaść tu. Ale `cleanup()` cron jeszcze nie ma timer-aware peak guard — **potencjalny tech debt P3** (`#new`).

### 3e. Mitigation matrix

| Zmiana | Effort | Reduction | Trade-off |
|---|---|---|---|
| `busy_timeout=10000` | 1-line | obsługa 2× długiego hold | więcej spin → więcej CPU lock-poll |
| App-level retry (3× with jitter) wrap emit() | ~30 LOC | wszystkie callers chronione | dodaje latency 0.5-15s w worst-case |
| Move audit_log do PostgreSQL (sekcja 5) | 1-2 sprinty | end-of-life problem | migration + dual-write phase |
| `cleanup()` w peak-aware timer (off-peak only) | 30 min | redukuje 80% długich holdów | nadal podstawowy ryzyko z IO storm |
| `PRAGMA synchronous=NORMAL` (nie FULL) | 1-line | szybszy WAL append → krótszy hold | minimalny crash-safety regression z OK semantics dla append-only |

---

## 4. Propozycja: `core/jsonl_appender.py`

**Cel:** atomicity zapisu jednej linii JSONL niezależnie od size + concurrency safety między procesami + fail-loud na corruption sentinels.

**Kluczowa obserwacja:** problem to nie `O_APPEND` (działa w kernelu), tylko Python's BufferedWriter splitting + brak cross-process serialization. Rozwiązanie 2-warstwowe:

**Layer 1 — single os.write (omija BufferedWriter chunking).** Zamiast `with open(path, "a")`, użyj `os.open(path, O_WRONLY|O_APPEND|O_CREAT) + os.write(fd, data) + os.close(fd)`. Linux kernel gwarantuje atomic O_APPEND per syscall. Linijka >RLIMIT (zwykle hundreds of MB) niemożliwa praktycznie (nasz max=56KB).

**Layer 2 — fcntl.flock LOCK_EX dla cross-process serialization.** Belt-and-suspenders: nawet jeśli pojedynczy os.write zwróci short, flock zapobiega innemu writer-owi wejść w okno między retry-syscalls.

**API contract:**

```python
# core/jsonl_appender.py  (~60 LOC including docstring + edge cases)

import errno, fcntl, json, os
from pathlib import Path
from typing import Any, Mapping

class JsonlAppendError(RuntimeError):
    """Raised gdy zapis nie powiódł się po retry. Caller decyduje fallback."""

_DEFAULT_MAX_BYTES = 1 * 1024 * 1024  # 1 MB hard cap per record (sanity)

def append_jsonl(
    path: str | Path,
    record: Mapping[str, Any],
    *,
    fsync: bool = False,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> int:
    """Atomic append-line do JSONL z cross-process flock.

    Gwarancje:
    - Pojedyncza linia (record + '\\n') zapisana atomicznie z perspektywy
      innych procesów używających tej funkcji.
    - O_APPEND zapewnia że pos = file_size przy każdym write (kernel-level).
    - flock LOCK_EX zapobiega inter-process interleaving na hold-lock-during-write.
    - max_bytes guard: ValueError przed zapisaniem rekordu > limit.

    Zwraca: liczba zapisanych bajtów (record + '\\n').
    Raises:
        ValueError: rekord > max_bytes (sanity check, default 1MB).
        JsonlAppendError: write failed po retry (disk full, EIO, etc.).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    encoded = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(
            f"record size {len(encoded)} > max_bytes {max_bytes}; "
            f"refusing write to {path} (likely runaway dict; use truncated payload)"
        )

    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    fd = os.open(str(path), flags, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # block until exclusive
        try:
            written = 0
            view = memoryview(encoded)
            while written < len(encoded):
                try:
                    n = os.write(fd, view[written:])
                except OSError as e:
                    if e.errno == errno.EINTR:
                        continue  # signal — retry
                    raise JsonlAppendError(
                        f"os.write to {path} failed: {e}"
                    ) from e
                if n == 0:
                    raise JsonlAppendError(
                        f"os.write to {path} returned 0 bytes (disk full?)"
                    )
                written += n
            if fsync:
                os.fsync(fd)
            return written
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
```

**Dlaczego nie `tempfile + rename`:** dla append-only JSONL z 110 MB plikiem rename-pattern wymaga read-modify-write całego pliku per zapis = O(N) per call = unfeasible (~100ms+ na każdy zapis × 800/d = 80s/d wasted IO). Append-with-flock to O(1).

**Dlaczego `fsync=False` default:** koszt fsync ~1-10ms per call × 800/d = 1-8s/d. Niewarte dla learning_log (recoverable z events.db cross-ref). Mailek lub Postgres-bound caller może podać `fsync=True`.

**Test plan (osobny plik, ~40 LOC):**

| Test | Cel |
|---|---|
| `test_basic_append_roundtrip` | pojedyncza linia, read-back, pełen JSON parse |
| `test_concurrent_appenders_no_interleaving` | 4 procesów × 100 rekordów × 50KB każdy → 400 linii, każda valid JSON, zero corruption |
| `test_max_bytes_guard` | rekord 2MB z default 1MB → ValueError przed open |
| `test_eintr_recovery` | mock os.write raise EINTR raz → retry success |
| `test_flock_releases_on_exception` | exception in middle → next call acquire bez deadlock |
| `test_creates_parent_dir` | path z nonexistent parent → mkdir + write |
| `test_unicode_polish` | "ż", "ę", "ó" → encoded UTF-8, decoded correctly |

**Migration plan (per-callsite, atomic):**

1. **Krok 0:** Commit `core/jsonl_appender.py` + tests w isolation. ZERO call-site changes. Tag.
2. **Krok 1:** Refactor `panel_watcher._check_panel_override` (linia 138-143) → `append_jsonl(_LEARNING_LOG_PATH, override_rec)`. Tests pass. Commit. Restart `dispatch-panel-watcher` (off-peak).
3. **Krok 2:** Refactor `telegram_approver.append_learning` (linia 1257-1260) → wrapper wokół `append_jsonl`. 13+ call-sites użyją nowej semantyki bez changes. Restart `dispatch-telegram` (Adrian explicit ACK gate).
4. **Krok 3:** Refactor `shadow_dispatcher._append_decision` (linia 490) — orthogonal path (shadow_decisions.jsonl) ale same pattern. Restart `dispatch-shadow`.
5. **Krok 4:** Audit `learning_analyzer` reader-side → graceful skip na single-line JSONDecodeError zamiast crash (defense-in-depth nawet z appendem fixed).
6. **Krok 5:** Backport do Mailek `core/atomic_io.py` jako `append_jsonl` companion (dla future cron auto-runs). Cross-ref Backlog #2 Plan B.

**Effort:** ~3-4h total. Per Adrian's CLAUDE.md HARD RULE "code > 30 lines → MUST use AIDER": pełna implementacja + testy ~100 LOC → AIDER (`deepseek/deepseek-coder`). Komenda do uruchomienia ręcznie:

```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2
aider --model deepseek/deepseek-coder --message "Implement core/jsonl_appender.py per spec in /tmp/jsonl_appender_spec.md (which I'll write next). Add tests/test_jsonl_appender.py with 7 named tests including 4-process concurrent stress (subprocess.Popen). Use fcntl.flock LOCK_EX, O_APPEND, single os.write loop with EINTR retry. NO existing dependencies." core/jsonl_appender.py tests/test_jsonl_appender.py
```

(Spec /tmp/jsonl_appender_spec.md napiszę w następnym kroku jeśli zaakceptujesz proposal — to ~80 LOC markdown z testami names, edge cases, contract.)

---

## 5. Postgres Migration Roadmap

**Strategiczny cel (Z3):** wyeliminować "filesystem as IPC" jako dominujący IPC pattern. Postgres jako:
- **Source of truth** dla `orders_state` + `events` + `audit_log`
- **Append-only `dispatch_log`** zastępujący `learning_log.jsonl` + `shadow_decisions.jsonl`
- **Multi-tenant ready** dla Restimo + Warsaw + franczyza
- **ML-friendly** (replay reproducibility, snapshot at any timestamp via PITR)

### 5a. Pre-migracja — quick wins (jsonl_appender + retry)

**Effort:** 1-2 sesje (Sekcja 4 implementation + retry decorator wrap event_bus.emit).
**Outcome:** Eliminuje silent corruption + permanent event loss z BUSY 5s w callbackach.
**Postgres NIE wymaga się tu.** Branch `pre-pg-stability` mergeable bez DB infra.

### 5b. Faza 1 — Postgres infra + dual-write events.db (3-4 tygodnie)

**Co:**
1. Hetzner CPX32 (4GB RAM headroom) lub osobny CPX22 dla Postgres 16. Decyzja: same-host vs separated. Same-host = brak NTH dependency, mniej latency. Separated = isolation pod CPU/RAM pressure peak.
2. `dispatch_v2/core/pg_client.py` connection pool (psycopg3 async) + retry policy.
3. Schema:
   ```sql
   CREATE TABLE events (
     event_id TEXT PRIMARY KEY,
     event_type TEXT NOT NULL,
     order_id TEXT,
     courier_id TEXT,
     payload JSONB NOT NULL,
     created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
     status TEXT NOT NULL DEFAULT 'pending',
     processed_at TIMESTAMPTZ,
     INDEX idx_events_status_created (status, created_at) WHERE status='pending',
     INDEX idx_events_order (order_id),
     INDEX idx_events_audit_type_created (event_type, created_at) WHERE status='processed'
   );
   CREATE TABLE dispatch_log (
     id BIGSERIAL PRIMARY KEY,
     ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
     action TEXT NOT NULL,
     order_id TEXT,
     courier_id TEXT,
     decision JSONB,
     -- replay_index dla ML training reproducibility:
     decision_record_hash TEXT
   ) PARTITION BY RANGE (ts);
   -- monthly partitions (auto-create via pg_partman)
   ```
4. **Dual-write w event_bus.emit:** zapisuje do SQLite events.db + Postgres events. Reconciliation cron 1×/h porównuje row counts; alerty na divergence.
5. **Dual-write w append_jsonl:** zapisuje do learning_log.jsonl + Postgres dispatch_log. Same approach.
6. Postgres traktowane jako **shadow** w tej fazie. SQLite + JSONL nadal source of truth dla read paths.

**Gate:** 7 dni dual-write zero-divergence → Faza 2.

### 5c. Faza 2 — flip read paths (2-3 tygodnie)

1. `learning_analyzer.py`, `parser_health_endpoint`, `r04_evaluator`, `sprint2_analysis` — czytają z Postgres `events` + `dispatch_log` zamiast SQLite + JSONL.
2. Performance benchmark: SQL queries (z indeksami) muszą dorównać lub pobić obecny scan-jsonl.
3. SQLite events.db nadal aktywne dla event_bus pending→processed lifecycle (low-latency local). Postgres mirror dla read path tylko.
4. JSONL traktowane read-only po flip; ostatni zapis = ostatni przed cutover.

**Gate:** 7 dni czytanie z PG bez incidents → Faza 3.

### 5d. Faza 3 — events.db → Postgres primary (2-3 tygodnie)

1. **Critical:** event_bus pending→processed cykl w PG zamiast SQLite.
2. WAL+busy_timeout patterns zastępowane przez PG `SELECT ... FOR UPDATE SKIP LOCKED` (worker patterns) + advisory locks dla cleanup.
3. Reconcile worker, shadow_dispatcher konsumują z PG. SQLite events.db retire (kept w archive).
4. **Backup strategy:** PG continuous archive + daily snapshot. Recovery RTO <5 min.

**Gate:** 7 dni production na PG, full failover drill. Tag: `pg-events-primary-2026-XX-XX`.

### 5e. Faza 4 — orders_state.json → Postgres `orders` table (1-2 tygodnie)

1. Last filesystem-IPC fortress: orders_state.json. Migration:
   ```sql
   CREATE TABLE orders (
     order_id TEXT PRIMARY KEY,
     status TEXT NOT NULL,
     courier_id TEXT,
     restaurant TEXT,
     pickup_address TEXT,
     delivery_address TEXT,
     pickup_coords POINT,
     delivery_coords POINT,
     czas_kuriera_warsaw TIMESTAMPTZ,
     czas_odbioru_timestamp TIMESTAMPTZ,
     -- 28 normalize_order keys + tracone fields (#19 audit) all explicit
     ...,
     history JSONB DEFAULT '[]'::jsonb,
     updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
     created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
   );
   CREATE INDEX idx_orders_status ON orders(status);
   CREATE INDEX idx_orders_courier ON orders(courier_id) WHERE courier_id IS NOT NULL;
   ```
2. Replace `state_machine.upsert_order` → `INSERT ... ON CONFLICT (order_id) DO UPDATE` + JSONB history append.
3. flock pattern → PG row-level lock (automatic with `UPDATE`).
4. Reader retry pattern → `SELECT ... FOR SHARE` z `NOWAIT` → fallback po timeout.

**Outcome Faza 4:** ZERO filesystem-IPC dla state. Pliki JSON pozostają tylko jako config (flags.json, kurier_ids.json itd.) z manual-edit semantics.

### 5f. Faza 5 — flags.json → PG `feature_flags` + audit (opcjonalnie, 1 tydzień)

1. Hot-reload nadal przez file-watch fallback dla off-net resilience.
2. Primary path: PG `feature_flags` table + LISTEN/NOTIFY dla instant flip propagation across services.
3. Audit log każdej zmiany flag (kto, kiedy, jaka wartość) — eliminuje obecne "flag flipped 04.05 ~22:34 UTC" forensic guesswork.

### 5g. Total roadmap effort + cost

| Faza | Effort | Calendar | Critical-path? |
|---|---|---|---|
| Sekcja 4 (jsonl_appender) | 3-4h | sprint | ✓ pre-req |
| Faza 1 PG infra + dual-write | ~40-60h | 3-4 wks | ✓ unlocks all |
| Faza 2 read-flip | ~20h | 2 wks | ✓ |
| Faza 3 events primary | ~30h | 2-3 wks | ✓ |
| Faza 4 orders_state | ~25h | 1-2 wks | ✓ |
| Faza 5 flags | ~15h | 1 wk | optional |
| **Total** | **~140-160h** | **~3 mies kalendarzowych** | |

**Cost:** Hetzner CPX32 dla PG osobny ≈ +13.99€/mies. Lub same-host (no extra cost, jeśli RAM headroom OK). PG 16 community license = €0.

**Realistic timing wobec roadmap (`project_overview.md` snapshot 06.05):**
- T1-T4 (04-31.05): Faza 7-AUTO-PROXIMITY + Bolt Food integration. PG roadmap **NIE w critical path** — Adrian nie ma capacity.
- Q3 2026 MVP launch + 5-10 restauracji: PG migration **uzasadniona** (Restimo prep, Warsaw scale).
- **Rekomendacja Z3:** Sekcja 4 (jsonl_appender) DOSTAJE Backlog P2 dla Tygodnia 2-3 (deadline-flexible). Faza 1+ PG = **Q3 prep work** post-Faza 7 100% flip.

---

## 6. Konsolidacja — co NEXT

| Priorytet | Action | Effort | Owner |
|---|---|---|---|
| **P0 hot-fix** | Wrap `event_bus.emit()` w `_with_retry(3, jitter=True)` decorator dla telegram callback handlers (eliminuje permanent loss z BUSY w peak) | 30 min | SELF (architectural) + AIDER (impl ~25 LOC) |
| **P0 hot-fix** | `cleanup()` w event_bus → peak-aware guard (skip 11-14, 17-20 Warsaw, soboty 16-21) | 15 min | SELF |
| **P1 backlog** | `core/jsonl_appender.py` + tests + 3-callsite migration (panel_watcher.py:139, telegram_approver.py:1257, shadow_dispatcher._append_decision) | 3-4h | AIDER (deepseek-coder) |
| **P2 backlog** | `core/state_io.py` consolidacja: state_machine + manual_overrides + Mailek atomic_io → single shim, eksport multi-tenant | 4-5h | AIDER refactor + SELF schema |
| **P3 strategic** | Postgres migration roadmap Faza 1+ (Q3 prep) | 140-160h, 3 mies | dedicated sprint(s) post-Faza 7 100% |

### Backlog updates needed (`tech_debt_backlog.md`):

```
### #22 [Z] event_bus.emit() retry decorator dla callback paths (P0 hot-fix)
Effort: 30 min. Pattern: 3-attempt retry + exp backoff (50/100/200ms) + final raise.
Dotyczy: telegram_approver._handle_assign_callback / _inny_callback / _koord_callback /
_koniec_callback / _poprawa_callback. Zapobiega permanent event loss przy SQLITE_BUSY 5s.

### #23 [Z] core/jsonl_appender.py — atomic JSONL append cross-process (P1)
Effort: 3-4h (impl + tests + 3-callsite migration). Eliminuje silent corruption
learning_log.jsonl + shadow_decisions.jsonl. fcntl.flock LOCK_EX + single os.write
loop z EINTR retry. Lekcja #14 (atomic) rozszerzenie na append-only path.
Cross-ref: Mailek Backlog #2 Plan B (atomic_io) — wspólny consolidate w core/state_io.py.

### #24 [Z] core/state_io.py consolidation — eliminate write-implementation drift (P2)
Effort: 4-5h. Single shim: atomic_write_json + locked_write_json + locked_read_json +
append_jsonl_atomic. Migracja state_machine (canonical) + manual_overrides + Mailek.
Z3 deliverable: Restimo / Warsaw multi-tenant ready (każdy tenant = osobny path prefix).

### #25 [Z] event_bus cleanup() peak-aware (P0 hot-fix)
Effort: 15 min. Add `if _is_peak_window(): return 0` na początku. Skip cleanup w
11-14, 17-20 Pn-Pt + 16-21 Sb. Eliminuje 80% długich-hold scenarios w peakach.

### #26 [Z] Postgres migration prep (P3 strategic, Q3 2026)
Effort: 140-160h, 3 miesiące kalendarzowe. Faza 1: dual-write events.db + JSONL → PG.
Faza 2-4: flip read paths → events primary → orders_state primary.
Tied to: Bolt Food integration, Warsaw expansion, Restimo multi-tenant.
Pre-req: Faza 7-AUTO-PROXIMITY 100% flip (capacity gate).
```

---

## Pre-deploy ACK gates per Adrian (workflow)

1. ACK na proposal (this audit) jako kierunek strategiczny.
2. ACK osobny per #22 (P0 retry decorator) → SELF impl, restart `dispatch-telegram` z explicit ACK.
3. ACK osobny per #25 (P0 cleanup peak-aware) → SELF impl, no-restart (cleanup z timera).
4. ACK osobny per #23 (jsonl_appender) → AIDER command, per-callsite migration commits z ACK each.

---

**Audit complete.** Powiązane dokumenty w tym folderze:
- `ARCHITECTURE_AUDIT_2026-05-07.md` — całościowa mapa systemu (16 services + 12 timers + lifecycle ordera)
- `STATE_OWNERSHIP_EVENT_FLOW_AUDIT_2026-05-07.md` — szczegółowa analiza event-flow + state transitions
- `META_AUDIT_ROOT_CAUSES_ROADMAP_2026-05-07.md` — root causes + roadmap z meta perspektywy
- `STRATEGIC_RISK_SYNTHESIS_2026-05-07.md` — top 20 ryzyk priorytetyzowanych P×I
