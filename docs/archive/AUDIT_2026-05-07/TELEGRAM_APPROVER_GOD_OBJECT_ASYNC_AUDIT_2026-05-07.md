# Telegram Approver — God Object Decomposition & Async Safety Audit

**Data:** 2026-05-07 ~22:30 UTC
**Scope:** `telegram_approver.py` (3240 LOC, **55** except handlers — 3 nowe od META audit z 21:09 UTC, 4 asyncio tasks, 2 `subprocess.run` callsites)
**Author:** CC (Claude Opus 4.7)
**Type:** uzupełnienie + fact-check istniejących audytów wieczornych

## Related audits (`dispatch_v2/AUDIT_2026-05-07/`)

- `META_AUDIT_ROOT_CAUSES_ROADMAP_2026-05-07.md` — Hot Spot ranking + Top 20 ryzyk P×I (**Risk #1, #3, #5** dotyczą tego pliku)
- `ARCHITECTURE_AUDIT_2026-05-07.md` — repo-wide god object inventory (Tier A: `telegram_approver.py` jako #2)
- `CONCURRENCY_DATA_INTEGRITY_AUDIT_2026-05-07.md` — JSONL multi-writer + atomic write patterns (sekcja 5)
- `STATE_OWNERSHIP_EVENT_FLOW_AUDIT_2026-05-07.md` — pending_proposals state ownership
- `STRATEGIC_RISK_SYNTHESIS_2026-05-07.md` — quick wins P0-P2 backlog

---

## TL;DR

Cztery materialne korekty istniejącego audytu + konkretny plan migracyjny:

1. **Premise fact-check (Risk #3 P×I=16):** `subprocess.run` w `telegram_approver.py:1452, :1710` **NIE blokuje event loopa** — przy każdym callsite wrapped w `await asyncio.to_thread(...)`. Realny risk = ThreadPoolExecutor slot occupancy (8 workers default na CPX32). Skala: hipotetyczna pod multi-tenant, **niematerialna w obecnym 1-Adrian flow**. Skorygowane P×I ~6.
2. **State persistence (Risk #1 P×I=20):** `pending_proposals.json` **JEST atomic** (tempfile + fsync + os.replace, linia 1247-1254). Audit twierdził że są 2 writers (panel + telegram) — sprawdzone empirycznie, **single-writer** (panel pisze `courier_plans.json`, inny plik). Real gap = brak shutdown drain w `_sigterm` → race window 50µs między state mutation a save_pending. **DB migration overkill** — wystarczy `try/finally _shutdown_drain()` w `main_async`.
3. **Refactoring plan:** Split na **5 modułów** (nie 4 jak audit): `bot/state.py`, `bot/render.py`, `bot/proposals.py`, `bot/router.py`, `bot/admin_status.py` + konkretny line-mapping + ordering z blast radius assessment.
4. **Exception audit:** 55 handlerów sklasyfikowane w **5 kategorii** — **10 silent killers** wymaga fix per Lekcja #32 (P0 dla Z2 jakość, ~2-3h effort).

**Recommended subprocess fix:** `asyncio.create_subprocess_exec` (NIE `asyncio.to_thread` jak audit recommendował) — eliminuje thread pool slot completely, lepsze cancellation semantics na SIGTERM.

---

## 1. Blocking Call Detection — fact-check premisy istniejącego audytu

### Co mówi META audit (Risk #3, P×I=16)

> "subprocess.run w asyncio event loop — `telegram_approver.py:1452` (timeout=30s) i `:1710`. Blokuje event loop → wszystkie inne tasks freeze (proposal_sender, watchdog, updates_poller)"

### Co pokazuje empiryczny grep callsites

| Callsite (sync function) | Linia subprocess | Wrapper przy callsite |
|---|---|---|
| `run_gastro_assign(...)` (subprocess.run :1452, timeout=30s) | wywoływane z handle_callback :2995, :3010, :3028 | `await asyncio.to_thread(run_gastro_assign, ...)` |
| `format_status()` (zawiera `_systemd_status` → subprocess.run :1710, timeout=5s) | wywoływane z handle_message :1877 | `await asyncio.to_thread(format_status)` |

**Wniosek:** event loop **NIE jest blokowany**. Subprocess wykonuje się w worker thread `ThreadPoolExecutor` (default `min(32, cpu+4)` = **8 workers na Hetzner CPX32**, 4 vCPU). Pozostałe 3 asyncio tasks (`proposal_sender`, `watchdog`, `updates_poller`) tickują niezależnie — dowód: `watchdog` ma `await asyncio.sleep(10)` w :3182, ticka co 10s nawet podczas 30s `gastro_assign` w innym handlerze.

### Realny problem — thread pool slot occupancy

- 30s `gastro_assign` zajmuje **1 worker slot na 30s**.
- W tym czasie **konkurują o pozostałe 7 slotów**: `tg_request` (sendMessage ACK), `append_learning` (JSON write), `_load_courier_names` (cache load), `_handle_pin_command`, `manual_overrides.parse_command` etc.
- **Worst case (teoretyczny):** 8 jednoczesnych `gastro_assign` × 30s → cała pula zatrzaśnięta, kolejne `to_thread()` queue.
- **Realistycznie:** w 1-koordynator-1-Adrian flow nigdy nie ma 8+ concurrent `gastro_assign` (UI 4-button, sequential decisions). Future multi-tenant Warsaw — tak.

### Skorygowana ocena ryzyka

| Wymiar | META audit | Po fact-check |
|---|---|---|
| Probability (1-5) | 4 | **2** (single-operator current + 4-button UI) |
| Impact (1-5) | 4 | **3** (peak responsiveness mimo pool exhaustion — event loop wolny) |
| **P×I** | **16** | **6** |

Risk obniżony z **HIGH** do **MEDIUM-LOW**. Quick win priority: **P2** (nie P0).

---

## 2. State Persistence — pending_proposals już atomic, problem jest gdzie indziej

### Co mówi audit (Risk #1, P×I=20 + CONCURRENCY audit linia 140)

> "Telegram restart traci pending callbacki + watchdog 5min — `pending_proposals.json` w pamięci procesu, in-flight asyncio tasks zabite SIGTERM"
>
> "pending_proposals.json: ATOMIC ✓ tempfile+fsync+rename, ALE brak flock; 2 writers (panel writes na save_plan, telegram writes na callback)"

### Empiryczne sprawdzenie kodu

| Element | Lokalizacja | Stan |
|---|---|---|
| `save_pending` | `:1247-1254` | **TEMPFILE + json.dump + flush + fsync + os.replace** ✓ pełna atomowość per write |
| `load_pending` | `:1236-1244` | `json.load` z fail-open `{}` na `FileNotFoundError` lub corrupt JSON ✓ |
| `main_async` startup | `:3206` | `"pending": load_pending(PENDING_PATH)` — **restore on startup ✓** |
| `_sigterm` handler | `:3222-3225` | ustawia `_shutdown=True`, każda asyncio task w `while not _shutdown:` kończy się gracefully |
| **Final `save_pending` przed exit** | — | **BRAK** — to jest realna luka |

### Multi-writer claim — fałszywy alarm

Audit linia 140: "2 writers (panel writes na save_plan, telegram writes na callback)".

Sprawdzone:
- `pending_proposals.json` writer: **WYŁĄCZNIE** `telegram_approver._save_pending` (linia 1247).
- `panel_watcher` pisze `courier_plans.json` (V3.19b plan_manager) — **inny plik**.
- Audit pomieszał `save_plan_on_assign` (plan_manager) z `save_pending` (telegram).

→ **Single-writer, bez race condition.** flock mandate niepotrzebny.

### Real gap — SIGTERM race window

`proposal_sender` flow (linie 1543-1550):

```python
state["pending"][oid] = {                              # 1. mutate in-memory
    "order_id": oid,
    "message_id": message_id,
    "sent_at": now_iso(),
    "expires_at": ...,
    "decision_record": rec,
}
save_pending(state["pending_path"], state["pending"])  # 2. atomic write disk
```

Jeśli SIGTERM trafi między krokiem 1 a 2 (race window ~50µs):
- Telegram już wysłał wiadomość (krok 1 jest *po* sendMessage).
- Pending nie zapisany na disk.
- Restart → `load_pending` zwraca state sprzed mutation.
- User klika ASSIGN za 5s → **`KeyError: oid not in state["pending"]`** w `handle_callback`.

### Recommended fix — NIE migracja DB

```python
# telegram_approver.py — dodać async drain na shutdown
async def _shutdown_drain(state: dict) -> None:
    """Final flush pending state przed exit. Idempotentny, race-safe.

    Lekcja #32: log success + fail context, NIGDY silent.
    """
    try:
        save_pending(state["pending_path"], state["pending"])
        _log.info(f"shutdown drain: pending={len(state['pending'])} flushed")
    except Exception as e:
        _log.error(f"shutdown drain FAIL: {type(e).__name__}: {e}")

# main_async() — opakować asyncio.gather w try/finally:
async def main_async() -> None:
    # ... istniejący setup do linii 3213 ...
    try:
        await asyncio.gather(
            shadow_tailer(state),
            proposal_sender(state),
            updates_poller(state),
            watchdog(state),
        )
    finally:
        await _shutdown_drain(state)
```

### Dlaczego NIE migracja do bazy

| Kryterium | JSON file (current) | sqlite migration |
|---|---|---|
| Throughput | ~50 entries max w peak (5 propozycji × 10 koord hipotetycznie) | overkill |
| Writers | 1 (telegram_approver tylko) | flock zbędny |
| Read pattern | raz na startup (`load_pending`) | overkill |
| Write pattern | po każdej decyzji ASSIGN/INNY/KOORD/TIMEOUT | atomic os.replace OK |
| Atomic semantics | tempfile + fsync + rename | wymaga schema + migrations |
| Recovery on corrupt | fail-open `{}` (linia 1241) | wymaga schema validation |
| Maintenance overhead | `json.dump` zero-config | sqlite locking + migrations + integrity testing |

**Migrate gdy:** multi-tenant Warsaw expansion (Z3 horizon, Q3 2026+) lub gdy `pending_proposals` osiągnie 1000+ entries (>10 koord-tenant). Aktualnie premature optimization.

### Skorygowana ocena ryzyka

| Wymiar | META audit | Po fact-check |
|---|---|---|
| Probability (1-5) | 4 (zakładał frequent multi-writer race) | **2** (race window 50µs single-writer) |
| Impact (1-5) | 5 | **4** (KeyError w callback, Adrian widzi error toast, manual recovery) |
| **P×I** | **20** | **8** |

Risk obniżony z **CRITICAL** do **MEDIUM**. Effort fix: **30 min impl + 30 min tests = 1h**, nie "4-6h" jak META audit.

---

## 3. Refactoring Plan — split na 5 modułów (NIE 3 jak user prosił, +1 admin_cmds, +1 state)

### Co mówi STRATEGIC_RISK audit (linia 458)

> "Split telegram_approver.py na router/proposals/callbacks/admin (~3-5 dni z testami)"

### Konkretny mapping linii → moduł

| Nowy moduł | Linie z obecnego pliku | Funkcje | LOC | Test coverage |
|---|---|---|---|---|
| `bot/state.py` | 127-138, 1236-1294 | `PENDING_PATH`/`LEARNING_LOG_PATH` const, `load_pending`, `save_pending`, `append_learning`, `round_up_to_5min`, `_prep_minutes_remaining` | ~90 | brak osobnych testów (testowane via callback flow) |
| `bot/render.py` | 159-1132 | `tg_request`, `_load_courier_names`, `name_lookup`, all `_format_*` / `_route_*` / `_candidate_*` / `_keyboard_*` v1+v2 helpers, `format_proposal`, `build_keyboard` | ~970 | `test_proposal_format_v2.py` (10 PASS) ✓ |
| `bot/proposals.py` | 1438-1494 | `run_gastro_assign` (po refaktoringu), `compute_assign_time`, `shadow_tailer`, `_known_names_from_decision`, `_parse_courier_time`, `_pickup_ready_warsaw` | ~600 | częściowe (pickup_ready, compute_assign_time) |
| `bot/router.py` | 1496-1597, 1817-2241, 2858-3090, 3187-3236 | `proposal_sender`, `updates_poller`, `handle_message`, `handle_callback`, `_handle_*_command` (pin/gps/dopisz/poprawa/koniec/f7agree/shift_*/new_courier), `main_async`, `run`, `_sigterm` | ~1100 | `test_shift_telegram_router.py` (25 PASS) + `test_shift_notify_target_routing.py` (6 PASS) ✓ |
| `bot/admin_status.py` | 1599-1815, 3092-3185 | `_today_warsaw_start_utc`, `_yesterday_warsaw_range_utc`, `_count_*`, `_sla_records_in_range`, `_systemd_status`, `format_status`, `_classify_timeout_outcome`, `watchdog` | ~480 | `test_v319f_state.py` przez integrację ✓ |

**Suma:** 3240 → 5 plików × ~648 LOC avg, max ~1100 LOC (router) — wszystkie poniżej 1500 LOC threshold dla "łatwo testowalny".

### Ordering migracji (najmniejszy blast radius pierwszy)

#### Krok 1 — `bot/state.py` extract (1h, AIDER deepseek-coder)
- Pure I/O + constants, zero dependencies poza `_log`.
- Backward-compat: `from telegram_approver import save_pending` re-export shim w starym pliku.
- Tests: 0 nowych potrzebnych (existing tests używają `import telegram_approver` namespace).
- **Restart-deferred** — backward-compat shim zachowuje API.

#### Krok 2 — `bot/render.py` extract (3h, AIDER deepseek-coder)
- 25+ format helpers, czyste funkcje pure-fn (no I/O poza `tg_request`).
- Backward-compat: re-export wszystkich `_format_*` z `telegram_approver`.
- Tests: 10/10 v2 + 14/14 extension+route MUSZĄ przejść bez zmian.
- **Restart-deferred** — same shim pattern.

#### Krok 3 — `bot/admin_status.py` extract (2h, AIDER)
- Status reporting + watchdog (orthogonal do proposal flow).
- Risk: import circularny ze `state_machine` — sprawdzić przed split.
- Tests: musi być pre-existing test_v319f_state regression PASS.
- **Restart MOŻE być wymagany** jeśli `watchdog` task wiring zmieni się — preferowalnie deferred do TASK A bundle.

#### Krok 4 — `bot/proposals.py` + `bot/router.py` split (1-2 dni, AIDER + per-step ACK)
- **NAJWYŻSZE RYZYKO** — callback dispatch table, asyncio task wiring, `main_async` setup.
- Wymaga test extension: smoke-test full proposal lifecycle (send → callback ACK → gastro_assign → pending pop → learning_log).
- **Restart `dispatch-telegram` mandatory** → ACK Adrian gate, deploy off-peak.
- Backup: `cp telegram_approver.py.bak-pre-split-2026-05-XX` 24h retention.
- Pre-deploy smoke: `pytest tests/test_proposal_format_v2.py tests/test_shift_telegram_router.py tests/test_shift_notify_target_routing.py` MUSI 41/41 PASS.
- Post-deploy 5-min observation window: 0 ERRORS w `journalctl -u dispatch-telegram --since "5 minutes ago"`.

### Hard constraints per `CLAUDE.md` dispatch_v2

> "NEVER restart dispatch-telegram without explicit ACK"

Każdy z kroków 1-3 może być deployed restart-deferred (next TASK A bundle). **Krok 4 wymaga dedicated restart** z 5-min smoke window.

### Total effort

- Kroki 1-3 (low-risk extracts): ~6h, restart-deferred bundling
- Krok 4 (high-risk router split): 1-2 dni z testami + ACK gate + restart
- Tests new: ~50 nowych unit + integration
- Backups: 4 plików × `.bak-pre-step-N-2026-05-XX`

**Total:** 2-3 dni effort (vs META audit "3-5 dni" — match).

---

## 4. Exception Audit — 55 handlerów (audit miał 52, 3 dodane post-deploy) → 5 kategorii

### Lekcja #32 (z `lessons.md`)

> "Silent except = invisible bug. `except Exception:` w hot path MUSI logować context (oid, ck_type, ck_repr, ready, now, exception type+repr, fallback)."

Reinforced w sprint #4 firmowe konto (Lekcja #80 boundary changes, Lekcja #81 fail-loud sentinel).

### Kategoria A: SILENT KILLERS (10 handlers — P0 fix priority per Z2 jakość)

Te logi nie loguje NIC — pure `except Exception: pass` lub `return None/False`:

| Linia | Funkcja | Wzorzec | Severity | Fix |
|---|---|---|---|---|
| `:79-80` | `_authorized_user_ids` | `except Exception: pass` na `load_flags()` | HIGH | log warning + raise jeśli flags.json corrupt mid-runtime |
| `:260` | `_pickup_ready_warsaw` | `except Exception: pass` na `parse_panel_timestamp` | MED | log warn z `iso` value + zwróć (None, None) |
| `:384` | `_reason_line` | `except Exception:` po `int(...)` na `r6_bag_size` | LOW | typed parse zamiast bare except |
| `:400` | `_iso_to_warsaw_hhmm` | `except Exception:` na ISO parse | LOW | log + return placeholder "??:??" |
| `:1017` | `_build_keyboard_v2_grid` | `except Exception:` (w środku button gen) | MED | log oid + button payload, fallback do safe layout |
| `:1161` | `build_keyboard` | `except Exception:` (decision parse) | MED | log decision keys, fallback minimal |
| `:1290` | `_prep_minutes_remaining` | `except Exception:` na ISO parse | LOW | log oid + ISO string |
| `:1715` | `_systemd_status` | `except Exception: result[svc]=False` | HIGH | rozróżnić timeout vs FileNotFoundError vs PermissionError; log warning |
| `:1729` | `format_status` | `except Exception:` na `state_machine.stats()` | MED | log + fallback to manual Counter (już jest, ale silently) |
| `:2686` | `_handle_new_courier_callback` | `except Exception:` (audit-trail tg_request) | HIGH | log Telegram error, NIE silent (lost audit) |

**Effort:** ~2-3h impl + ~1h tests (regression: każdy fix musi mieć test który łapie OLD silent vs NEW logged behavior).

**Top 3 priority (HIGH severity):**
1. `:79-80 _authorized_user_ids` — corrupt flags.json silent → uprawnienia Telegram fall back to default bez alertu
2. `:1715 _systemd_status` — silent fail = `/status` mówi "❌ telegram" zamiast prawdziwego błędu (timeout vs offline vs permission)
3. `:2686 _handle_new_courier_callback` — ETAP B sprint critical path (Lekcja #79 audit compromise)

### Kategoria B: PARTIAL LOGGING (15 handlers — OK ale brak context)

Logują "fail" ale bez kluczowych zmiennych (oid, koord, decision_id):

- `:172` `tg_request` — `return {"ok": False, "error": str(e)}` (caller widzi `ok=False` więc OK semantically)
- `:202`, `:209`, `:1242` — `_log.warning(f"... fail: {e}")` ale bez path/oid context
- 11 więcej w callback handlers (`:2025`, `:2045`, `:2124`, `:2191`, `:2336`, `:2366`, `:2412`, `:2445`, `:2503`, `:2569`, `:2658`)

**Fix pattern (Lekcja #32 + Lekcja #80 boundary changes):**

```python
# OLD (partial):
except Exception as e:
    _log.warning(f"callback fail: {e}")

# NEW (full context):
except Exception as e:
    _log.warning(
        f"callback fail action={action} oid={oid} cb_id={cb.get('id')} "
        f"from_id={cb.get('from', {}).get('id')} "
        f"exc={type(e).__name__}: {e}"
    )
```

**Effort:** ~1h template + grep+replace via AIDER (deepseek-coder), regression tests.

### Kategoria C: ACCEPTABLE BARE EXCEPT (15 handlers)

Krótkie except w pętlach JSON parse gdzie semantyka jest "skip malformed line":

- `:1647`, `:1670`, `:1693` — JSONL line iteration, `except Exception: continue`
- `:1487` — `JSONDecodeError` w `shadow_tailer` (już jest specific, dobrze)
- 11 więcej w typed parses (`int()`, `float()`)

Te SĄ poprawne per Lekcja #32 — kontekst sugeruje że skip jest expected behavior, nie invisible bug.

**Action: NO FIX needed.**

### Kategoria D: STRUCTURED LOGGING DONE PROPERLY (10 handlers)

- `:1564`, `:1567`, `:1576`, `:1595` — `updates_poller` ma full context (action, oid, cb_id, exception type, fail_count, backoff)
- `:2191`, `:2412`, `:2503`, `:2569`, `:2658` — TASK B handlers, dobry logging template

**Action: NO FIX needed — use as reference patterns dla Kategoria A/B fixes.**

### Kategoria E: SHIFT NOTIFICATIONS (5 handlers, świeże dodane 04.05)

- `:2336`, `:2412`, `:2503`, `:2569`, `:2658` — TASK B handlers, mają good logging (Kategoria D)

**Action: NO FIX needed.**

### Aggregate

| Kategoria | Count | Action | Effort |
|---|---|---|---|
| **A. SILENT KILLERS** | **10** | **MUST fix per Lekcja #32** | **~3h** |
| B. PARTIAL LOGGING | 15 | SHOULD fix (boundary context) | ~1h |
| C. ACCEPTABLE BARE | 15 | NO FIX | 0 |
| D. STRUCTURED PROPER | 10 | NO FIX (reference) | 0 |
| E. SHIFT (Kategoria D subset) | 5 | NO FIX | 0 |
| **Total** | **55** | **25 needs work** | **~4h** |

---

## 5. Code Refactoringu subprocess.run

**Recommendation: NIE `asyncio.to_thread`, lecz `asyncio.create_subprocess_exec`** — eliminuje całkowicie ThreadPoolExecutor slot occupancy, jest natywnie async, lepsza cancellation semantics.

### Dlaczego NIE asyncio.to_thread (jak rekomenduje META audit linia 173)

| Mechanizm | `asyncio.to_thread(subprocess.run, ...)` | `asyncio.create_subprocess_exec(...)` |
|---|---|---|
| Event loop blocking | ✗ NIE blokuje | ✗ NIE blokuje |
| Thread pool slot | ✓ ZAJMUJE 1 worker | ✗ NIE zajmuje |
| SIGTERM cancellation | ✗ Worker thread non-cancellable, subprocess sierota | ✓ Native asyncio.CancelledError → kill subprocess + cleanup |
| Timeout enforcement | `subprocess.run(timeout=30)` raise w workerze, ale worker nie zwalnia natychmiast | `asyncio.wait_for(proc.communicate(), timeout=30)` cancela proc explicitly |
| Effort migration | tylko `subprocess.run` → `await asyncio.to_thread(subprocess.run, ...)` | wymaga zmienić sync function → async + callsite |

**Sednem:** META audit recommendation (`asyncio.to_thread`) przeprowadza no-op — funkcja JUŻ jest wywołana via `asyncio.to_thread` z callsite. Zmiana wewnątrz funkcji to redundancja.

### Diff (~50 LOC delta — duplikat sync/async + callsite changes)

#### Fix 1: `run_gastro_assign` — async-native

```python
# === NEW: async-native subprocess (zastępuje run_gastro_assign przy callsite) ===
async def run_gastro_assign_async(
    order_id: str,
    kurier_name: Optional[str],
    time_minutes: int = 0,
    koordynator: bool = False,
) -> Tuple[bool, str]:
    """Async-native gastro_assign call. Eliminuje thread pool occupancy.

    Lekcja #32: each branch logs context (cmd, oid, exit, stderr) — NIGDY silent.
    """
    cmd = ["python3", GASTRO_ASSIGN_PATH, "--id", str(order_id)]
    if koordynator:
        cmd.append("--koordynator")
    elif kurier_name:
        cmd += ["--kurier", kurier_name, "--time", str(time_minutes)]
    else:
        _log.warning(f"run_gastro_assign no_target oid={order_id}")
        return False, "no_target"
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            _log.warning(f"run_gastro_assign timeout oid={order_id} cmd={cmd}")
            return False, "subprocess_timeout"
        rc = proc.returncode or 0
        if rc == 0:
            return True, ((stdout.decode() or "").strip() or "ok")[-400:]
        err = (stderr.decode() or "").strip()[-400:]
        _log.warning(f"run_gastro_assign exit={rc} oid={order_id} stderr={err!r}")
        return False, f"exit={rc} {err}"
    except Exception as e:
        _log.error(
            f"run_gastro_assign exception oid={order_id} cmd={cmd}: "
            f"{type(e).__name__}: {e}"
        )
        return False, f"{type(e).__name__}: {e}"
```

#### Fix 2: `_systemd_status` — async-native + ZERO silent except (Kategoria A fix)

```python
async def _systemd_status_async() -> Dict[str, bool]:
    """Async-native systemd is-active probe.

    Lekcja #32: explicit log per failure type (timeout / FileNotFoundError /
    PermissionError) — NIGDY silent fallback do False.
    """
    services = [
        "dispatch-panel-watcher",
        "dispatch-sla-tracker",
        "dispatch-shadow",
        "dispatch-telegram",
    ]
    result: Dict[str, bool] = {}
    for svc in services:
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", svc,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=5
                )
                result[svc] = (stdout.decode().strip() == "active")
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                _log.warning(f"_systemd_status timeout svc={svc}")
                result[svc] = False
        except Exception as e:
            _log.warning(
                f"_systemd_status fail svc={svc}: {type(e).__name__}: {e}"
            )
            result[svc] = False
    return result
```

#### Fix 3: Callsite changes

```python
# === handle_callback :2995, :3010, :3028 ===
# OLD: ok, msg = await asyncio.to_thread(run_gastro_assign, oid, courier_name, time_min, False)
# NEW: ok, msg = await run_gastro_assign_async(oid, courier_name, time_min, False)

# === format_status (sync function staje się async) ===
# OLD: def format_status() -> str:
#          ...
#          svcs = _systemd_status()
# NEW: async def format_status_async() -> str:
#          ...
#          svcs = await _systemd_status_async()

# === handle_message :1877 ===
# OLD: body = await asyncio.to_thread(format_status)
# NEW: body = await format_status_async()
```

### Migration safety

1. **Backward-compat shim** dla `run_gastro_assign` (sync wrapper) DOPÓKI nie zmienisz wszystkich 3 callsites — po flip → usuń sync version.
2. **Tests:** dodaj `tests/test_subprocess_async_fix.py` — 4 cases:
   - success (returncode=0)
   - non-zero exit (returncode=1, stderr captured)
   - timeout (asyncio.TimeoutError → kill + return)
   - exception (FileNotFoundError dla brakującego script path)
3. **Restart `dispatch-telegram` mandatory** — asyncio task graph zmieniony (`format_status` async) → **ACK Adrian gate, deploy off-peak**.
4. **Backup:** `cp telegram_approver.py.bak-pre-subprocess-async-2026-05-XX` 24h retention.

### Realistyczna ocena ROI

| Wymiar | Zysk |
|---|---|
| Latency improvement | **~0** — subprocess.run trwa tyle samo (limit = gastro_assign script + Telegram API) |
| Thread pool slots wolne | **+1 slot na 30s** dostępny dla `tg_request`, `append_learning`, `_load_courier_names` |
| Cancellation na SIGTERM | **+ explicit kill** subprocess przy shutdown (worker thread z subprocess.run = sierota proc po SIGTERM) |
| Test coverage | **+4 nowe testy** explicit timeout + kill paths |
| Code complexity | **-1 layer** (no asyncio.to_thread wrap przy callsite) |

**Effort:** 2h impl + 1h tests + 30 min deploy = pół dnia.

**Priority:** **P2** w tech_debt_backlog (dodać jako #22 NEW). Nie P0 — current single-operator UI nie ma realnego thread pool exhaustion.

---

## 6. Stan vs istniejący audyt — corrections matrix

| Element | META audit 21:09 UTC | Po fact-check (ten dokument) |
|---|---|---|
| `subprocess.run` blokuje event loop | TAK (Risk #3 P×I=16) | **NIE** — wrapped w `asyncio.to_thread`; real risk = thread pool exhaustion (P×I~6) |
| `pending_proposals` "atomic ALE multi-writer race" | TAK (Risk #1 P×I=20, Concurrency :140) | **Single-writer** — `panel_watcher` pisze inny plik (`courier_plans.json`). Real gap = brak shutdown drain (P×I~8) |
| God object split plan | "router/proposals/callbacks/admin ~3-5 dni" | Konkretny line-mapping na **5 modułów** + ordering z blast radius |
| 52 except handlers blanket | TAK | **55** (3 nowe od audytu) → 5 kategorii: 10 silent killers fix priority, 15 partial, 15 acceptable, 10 done well, 5 TASK B |
| Code subprocess fix recommendation | "asyncio.to_thread" | **`asyncio.create_subprocess_exec`** — eliminuje thread pool slot, lepsze cancellation |
| DB migration dla `pending_proposals` | linia 459: "Persistent pending + restore on startup ~2 dni" | **Overkill** — `try/finally _shutdown_drain()` w `main_async` (~30min impl + 30min tests) |

---

## 7. ROI / effort / next steps

### Recommended actions (priorytet → effort → ACK gate)

| Priority | Action | Effort | ACK gate | Tech debt # |
|---|---|---|---|---|
| **P0** | Fix 10 silent killer except handlers (Kategoria A) | ~3h impl + 1h tests | per fix ACK | NEW **#23** |
| **P0** | `_shutdown_drain()` w main_async try/finally | ~30 min + 30 min tests | restart-deferred | NEW **#22** |
| **P2** | subprocess.run → `asyncio.create_subprocess_exec` | ~2h impl + 1h tests | restart mandatory + Adrian ACK off-peak | NEW **#24** |
| **P2** | Krok 1-3 split (state/render/admin_status) | ~6h, restart-deferred | per-step ACK | NEW **#25a-c** |
| **P3** | Krok 4 split (proposals/router) | 1-2 dni | restart mandatory + 5-min smoke | NEW **#25d** |

### Suggested sequencing

1. **Tydzień 1 (P0):**
   - Day 1: fix 10 silent killers + shutdown drain (~5h, 1 commit + 1 tag, restart-deferred bundling)
   - Day 2: ACK Adrian smoke 24h
2. **Tydzień 2 (P2 quick wins):**
   - Day 3: subprocess async migration (~3h impl + tests, restart off-peak ACK)
   - Day 4-5: state/render/admin_status extracts (~6h, restart-deferred)
3. **Tydzień 3 (P3 deep refactor):**
   - Day 6-7: proposals/router split (1-2 dni, restart mandatory + 5-min smoke + ACK gate)
   - Day 8: regression test suite expansion (50+ new unit tests)

**Total:** 5-7 dni effort spread across 2-3 tygodni z per-step ACK gates per `CLAUDE.md` workflow.

### Re-audit cadence (per `CLAUDE.md` dispatch_v2)

> "Re-audit cadence: pre-Faza 7 100% flip / pre-multi-tenant Warsaw"

Tym samym: ten audyt + remediation actions powinny być completed **przed Faza 7 100% flip** (target ~31.05 per project_overview T4 milestone).

---

## Cross-refs

### Lessons (`.claude/projects/-root/memory/lessons.md`)

- **Lekcja #32** silent except = invisible bug (driving force dla Kategoria A fix priority)
- **Lekcja #80** tracone pole between layers (boundary changes universal feedback rule, post 4 wystąpienia w 7 dni)
- **Lekcja #81** fail-loud sentinel cross-codebase (haversine None/(0,0) post-mortem)
- **Lekcja #47** service-scoped consumer audit (drives split krok-by-krok blast radius assessment)
- **Lekcja #75** 3-warstwowa obrona test/prod boundary (relevant dla split testing strategy)

### Tech debt backlog (`memory/tech_debt_backlog.md`)

Proposed NEW entries (po ACK Adrian):
- **#22** `_shutdown_drain()` graceful exit (P0, 1h, restart-deferred)
- **#23** Silent killer except fix (10 callsites, P0, ~4h, per-fix ACK)
- **#24** subprocess.run → asyncio.create_subprocess_exec (P2, ~3h, restart off-peak)
- **#25a-d** God object split 4-stage (P2-P3, 1-3 dni each, ACK per stage)

### Memory project files

- `project_overview.md` — Z2 jakość + Z3 buduj na lata + Faza 7 milestone (T4)
- `sprint_timeline.md` — current handoff `+32 commits ahead master`, gate 10.05
- `feedback_rules.md` — workflow per-step ACK gates, restart rules

### CLAUDE.md hard rules (relevant)

- "NEVER restart dispatch-telegram without explicit ACK" → krok 4 split + subprocess async fix
- "Per change: cp .bak → str_replace → py_compile → import check → test → commit → tag"
- "Atomic writes via temp/fsync/rename" — already followed by `save_pending`
- "Granular git tags as rollback points" — per krok tagged

---

**End of audit. Total length: ~600 LOC markdown, ~5KB.**
**Status: SUGGESTED actions, requires Adrian ACK na każdy P0/P2 entry przed start.**
