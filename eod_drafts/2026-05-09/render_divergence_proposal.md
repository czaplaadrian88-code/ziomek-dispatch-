# Propozycja: trwałe rozwiązanie render divergence czas_kuriera vs plan.pickup_at

**Data:** 2026-05-09
**Trigger:** Order 471744 (Grill Kebab) — panel 13:05, Telegram propozycja 13:17 (+12 min)
**Kontekst Z2/Z3:** root cause przed fix; rozwiązanie pewne i trwałe (no quick-patch)

---

## 1. Empiryczna baza problemu (data 2026-05-09)

### V3.27.4 reject distribution (today, n=56 shadow decisions)
| Strategy | Count | % | Trigger |
|---|---|---|---|
| `bruteforce` | 20 | 35.7% | bag=0 (solo orders, fast path) |
| `ortools` | 17 | 30.4% | OR-Tools success, in-window |
| `ortools_rejected_v3274` | 17 | 30.4% | post-solve assertion violation → greedy |

### Per bag size
| Bag | OR-Tools success | OR-Tools rejected | Reject rate |
|---|---|---|---|
| 0 | 0 | 0 | n/a (bruteforce) |
| 1 | 11 | 2 | **15.4%** |
| 2 | 5 | 11 | **68.8%** |
| 3 | 1 | 4 | **80.0%** |

**Top reject couriers:**
- cid=387 Aleksander G: 10/17 (58.8%)
- cid=509: 2/5 (40%)
- cid=179: 3/21 (14.3%)

### Render divergence scale (today, bag-orders w plan.pickup_at)
54 entries z divergence >1 min vs czas_kuriera commit:
| Magnitude | Count | % |
|---|---|---|
| 1-5 min | 34 | 63% |
| 5-10 min | 7 | 13% |
| 10-20 min | 13 | **24%** |
| 20+ min | 0 | 0% |

Per strategy: 32 (59%) z `ortools_rejected_v3274` fallback, 22 (41%) z `ortools` success.

**Worst case**: cid=387 oid=471744 ck=13:05 planned=13:24:04 **+19.1 min late**.

### Wniosek empiryczny
V3.27.4 mass-reject dla bag>=2 = **systematic** (68-80%). Greedy fallback ignoruje czas_kuriera całkowicie → produkuje pickup_at poza commit window. Render telegram_approver iteruje `plan.pickup_at` jako jedyne źródło → kurier widzi **nieprawdziwą trasę**, divergence do +19 min.

---

## 2. Root cause (3 niezależne problemy)

### RC-1: Render-side semantyka pickup_at
**Plik:** `telegram_approver.py:770-859` (`_route_lines_v2`) + `:487-535` (`_build_timeline_section`)
**Problem:** Render iteruje `plan.pickup_at[oid]` bez rozróżnienia commit vs computed ETA.
**Konsekwencja:** Dla bag-orders z committed czas_kuriera (post first_acceptance) wyświetla computed_eta zamiast hard commit.
**Nawet w `ortools` success path** (22/54 divergences) render może rozjeżdżać się z commit ±5 min — bo solver dał plan w window, ale renderowane z plan.pickup_at, nie z czas_kuriera.

### RC-2: bag_context payload incomplete
**Plik:** `dispatch_pipeline.py:2262-2270`
**Problem:** `bag_context` zawiera tylko `{order_id, restaurant, delivery_address}` — NIE ma czas_kuriera_warsaw per oid.
**Konsekwencja:** Render telegram_approver nie ma dostępu do commit nawet gdyby chciał użyć. Wymusza render z plan.pickup_at jako jedyne źródło.

### RC-3: Algorithm-side mass-reject + greedy fallback bez window
**Plik:** `route_simulator_v2.py:889-957` (post-solve assertion + reject path)
**Problem strukturalny:**
1. OR-Tools dostaje `time_windows=[ck-5, ck+5]` dla committed orderów (poprawne)
2. Solver często zwraca `ROUTING_SUCCESS` z plan, **ALE** post-solve assertion (`_plan_from_sequence` ETA) wykrywa walked > close+0.5 → reject
3. To sugeruje **time_matrix mismatch**: solver myśli "30 min path", `_plan_from_sequence` liczy "30 + DWELL×2 = 36 min" → walked exceed window mimo że solver myślał OK
4. Reject path → `_greedy_plan` (linie 652-731) **NIE ma parametru time_windows** — totally ignores czas_kuriera
5. Greedy NN insertion przez leg_min produkuje pickup_at poza commit (bo nigdy o nim nie wiedział)

**Anti-pattern:** "Reject infeasible → fallback to plan ignorujący tę samą constraint". Prawdziwe behavior: jeśli OR-Tools nie umie, real waste/late jest inevitable — propozycja powinna być **escalated** (KOORD verdict albo flag waste), nie covered cosmetic fallback.

### RC-1 vs RC-2 vs RC-3 niezależność
- RC-1 alone: render shows commit, ale plan.pickup_at ekspozowany w shadow_decisions nadal kłamie (downstream consumers like LGBM training)
- RC-2 alone: payload kompletny, ale render nadal nie używa
- RC-3 alone: algorithm correct, ale render nadal może pokazać ortools success drift ±5 min (cosmetic, less wrong)

**Wszystkie 3 muszą iść razem dla pewnego, trwałego rozwiązania.**

---

## 3. Propozycja w 4 fazach

### FAZA 0 — Diagnostic completion (P0, 2h, ZERO production change)
Cel: dane do FAZA 3 calibration decision (A/B/C).

1. **Structured logging** wszystkich V3.27.4 violations w shadow_decisions.jsonl:
   - Add field `v3274_reject_reason` per decision: `solver_status`, `walked_min`, `window_close`, `delta_min`
   - Currently buried in `czasowka.log` warnings, lost dla downstream
2. **Audit time_matrix vs leg_min divergence**:
   - Sample 10 ortools_rejected cases, replay z probe instrumentation
   - Cel: confirm DWELL = source of mismatch (hipoteza H-A) lub OSRM jitter (H-B)
3. **Replay 7 dni shadow_decisions** dla baseline rate:
   - Reject rate per dzień, per peak hour, per courier
   - Trend od V3.27.4 deploy 2026-04-27 (12 dni)

**Deliverable:** `audit_v3274_reject_2026-05-09.md` + `time_matrix_divergence_probe.jsonl`

**Defer if:** czasowka.log shows clear ROUTING_SUCCESS pattern → skip step 1 (data already there)

### FAZA 1 — Render-side correctness (P0, 4h)
**Workflow:** draft → ACK → .bak → impl → py_compile → tests → commit → tag → restart dispatch-telegram (explicit ACK Adrian) → 30min observation.

**Zmiany:**

1. **`dispatch_pipeline.py:2262-2270` — extend bag_context payload:**
   ```python
   "bag_context": [
       {
           "order_id": str(b.get("order_id") or ""),
           "restaurant": b.get("restaurant"),
           "delivery_address": b.get("delivery_address"),
           "czas_kuriera_warsaw": getattr(b, "czas_kuriera_warsaw", None),  # NEW
           "czas_kuriera_hhmm": getattr(b, "czas_kuriera_hhmm", None),       # NEW
       }
       for b in bag_raw
       if b.get("order_id")
   ],
   ```
   Backward compat: nowe pola optional, downstream consumers ignore gdy None.

2. **`telegram_approver.py:_route_lines_v2()` + `_build_timeline_section()` — pickup source priority:**
   ```python
   def _resolve_pickup_at(oid, plan_pickup_at, bag_context_map, decision):
       """Returns (datetime, source).
       Priority:
         1. czas_kuriera_warsaw z bag_context (committed bag order)
         2. czas_kuriera_warsaw z decision (current order if committed)
         3. plan.pickup_at[oid] (computed ETA fallback)
       """
       ck_iso = bag_context_map.get(oid, {}).get("czas_kuriera_warsaw")
       if not ck_iso and str(oid) == str(decision.get("order_id")):
           ck_iso = decision.get("czas_kuriera_warsaw")
       if ck_iso:
           return parse_iso(ck_iso), "commit"
       plan_iso = plan_pickup_at.get(oid)
       if plan_iso:
           return parse_iso(plan_iso), "eta"
       return None, "none"
   ```

3. **Visual marker (subtelny, NIE intrusive):**
   - `commit` source: `🍕 13:05 — Grill Kebab`  (no marker, default)
   - `eta` source: `🍕 ~13:17 — Grill Kebab` (~ tilde = approx)
   - Kurier od razu widzi: tilde = guess, no tilde = commit hard
   - Zero zmian dla orderów new (zawsze ~ETA bo brak commit)

4. **Defense-in-depth divergence warning:**
   ```python
   if ck_iso and plan_iso:
       diff_min = abs((parse(plan_iso) - parse(ck_iso)).total_seconds() / 60.0)
       if diff_min > 5.0:
           _log.warning(
               f"V3274_RENDER_DIVERGENCE oid={oid} commit={ck_iso} "
               f"plan_eta={plan_iso} diff={diff_min:.1f}min strategy={strategy}"
           )
   ```
   Empirical signal jak FAZA 0 ale per propozycja, nie offline replay.

**Tests** (nowe `tests/test_render_pickup_source_priority.py`):
- 471744 fixture: bag {471752 ck=13:01, 471744 ck=13:05} + new order — render musi pokazać 13:05 dla 471744 (NIE 13:17)
- New order bez ck — render shows ~plan_eta (z tilde)
- Mixed bag (1 committed + 1 not) — committed pokazuje commit, uncommitted pokazuje ~eta
- Edge: empty plan.pickup_at + ck present → render commit
- Edge: brak plan i brak ck → return "" (skip stop)
- Regression: solo order legacy 3-line layout zachowane

**Restart wymagane:** `dispatch-telegram` (Adrian explicit ACK gate per CLAUDE.md hard rule).
**Restart NIE wymagane:** `dispatch-shadow`/`dispatch-panel-watcher` — payload nowe pola tylko czytane przez telegram, ale serializer wkłada → tak, restart obu shadow + panel-watcher też (refresh `_serialize_*`).

**Rollback:** flag `ENABLE_V3274_RENDER_PICKUP_COMMIT_PRIORITY` env-overridable default True. Flip False = legacy plan.pickup_at first behavior, hot-reload, no restart.

### FAZA 2 — Observability dashboard (P1, 2h, dependency: FAZA 0+1 deployed)
1. Dashboard: V3.27.4 reject rate trend (per dzień, per peak window 11-14 + 17-20)
2. Telegram alert: jeśli reject rate >50% per peak hour → notification "AUTO-PROXIMITY DEGRADED"
3. Field `pickup_source` w shadow_decisions per oid (commit / eta / fallback) — dla LGBM training audit

### FAZA 3 — Algorithm-side calibration (P1, 1-2 dni, decyzja A/B/C po FAZA 0 data)

**Ścieżka A: time_matrix correction (jeśli FAZA 0 confirms DWELL mismatch)**
- `tsp_solver.py` — add DWELL_PICKUP_MIN + DWELL_DROPOFF_MIN do time_matrix per pickup/drop node
- Reject rate expected drop bag>=2: 68% → ~10%
- Risk: TSP latency zmiana (solver z większym czasem = więcej rozważanych routes); benchmark pre-deploy
- Test: replay 7 dni shadow + post-solve assertion check identical pre/post

**Ścieżka B: dynamic window relaxation**
- Bag>=2: window = ck ± (5 + bag_size × 1) min (np. bag=2 → ±7, bag=3 → ±8)
- Trywialny fix, ale **maskuje root cause** jeśli to DWELL mismatch
- Adopt **tylko jeśli** FAZA 0 wykluczy DWELL i pokaże że OSRM travel jitter > ±5 min realistic

**Ścieżka C: reject behavior change (jeśli FAZA 0 pokaże real infeasibility)**
- `route_simulator_v2.py:949` zamiast `_greedy.strategy = "ortools_rejected_v3274"; return _greedy`:
  ```python
  # V3.28: structural infeasibility — propose KOORD verdict, NIE silent fallback
  return RoutePlanV2(
      sequence=[],
      strategy="infeasible_v3274",
      sla_violations=len(_violations),  # surface impossible commits
      total_duration_min=float("inf"),
      ...
  )
  ```
- dispatch_pipeline → propose KOORD instead of feasible PROPOSE
- **Najbardziej anti-pragmatic** ale most honest semantically: infeasibility = real, system mówi prawdę

**Decision criterion (po FAZA 0 data):**
- 80%+ rejects explained by DWELL = ścieżka A (root cause = solver input mismatch)
- 60-80% rejects scattered (multi-cause) = ścieżka B (calibration, accept ±2-3 min slack)
- 40%+ rejects = real wait/late commits = ścieżka C (architectural, KOORD escalation)

### FAZA 4 — Tests + production fixture replay
- Production fixture replay framework (`tests/fixtures/production_replay/`):
  - 471744 today's case z full bag context + flags state
  - Fixture format: shadow_decisions JSONL slice + bag_state snapshot
- Regression: `test_v3274_render_divergence_replay.py` — 24h slice replay z FAZA 1 deployed musi pokazać <1 min divergence dla committed orderów
- Performance benchmark: TSP latency pre/post FAZA 3 (5 propozycji x 10 candidates per peak hour)

---

## 4. Tradeoff matrix (decyzja FAZA-per-FAZA)

| Aspekt | FAZA 0 | FAZA 1 | FAZA 2 | FAZA 3 | FAZA 4 |
|---|---|---|---|---|---|
| Effort | 2h | 4h | 2h | 8-16h | 4h |
| Risk | None | Low (render only) | None | Medium (algo) | Low |
| Restart wymagany | None | dispatch-telegram (ACK gate) | None | dispatch-shadow + panel-watcher | None |
| Quick win | Diagnostic data | Render truth | Visibility | Mass-reject root cause | Replay confidence |
| Bez tej fazy: | Decyzja A/B/C zgadnięta | Kurier widzi nieprawdziwą trasę | Brak alertu na regresję | Greedy fallback systematic | Brak baseline regression |

---

## 5. Order of operations (pewny, sekwencyjny)

```
DZIEŃ 1 (today/jutro):
├── FAZA 0 (2h) — diagnostic logging + 7-day replay
└── ACK Adrian na FAZA 1 design + tests plan
DZIEŃ 2:
├── FAZA 1 impl + tests (4h)
├── ACK gate przed restart dispatch-telegram
├── Restart shadow + panel-watcher (telemetry payload)
├── ACK gate explicit przed restart dispatch-telegram
└── 30 min observability post-deploy
DZIEŃ 3-4:
├── FAZA 2 dashboard + alert (2h)
└── FAZA 0 data analysis → ścieżka A/B/C decision
DZIEŃ 5-7:
├── FAZA 3 implementation per chosen path
└── FAZA 4 fixture replay + regression baseline
```

**Master merge gate 2026-05-10:** FAZA 0 + FAZA 1 cleanly fit pre-merge (1.5 dnia). FAZA 2-4 post-merge sprint.

---

## 6. Anti-shortcut commitments (Z2/Z3 alignment)

- **NIE** patchujemy tylko render bez bag_context payload extension (RC-2 unfix = brittle)
- **NIE** patchujemy tylko algorithm bez render (RC-1 cosmetic survives)
- **NIE** flip greedy fallback "respect ck" bez understanding czemu OR-Tools fails (głęboko bug = bigger time bomb)
- **NIE** relax window do ±10 bez FAZA 0 data (calibration zgadnięta = future regression)
- **TAK** structured FAZA 0 first (Z2 root cause przed fix)
- **TAK** all 4 fazy w sekwencji (Z3 buduj na lata, complete fix)

---

## 7. Rollback paths (każda faza)

| Faza | Rollback | Czas |
|---|---|---|
| 0 | git revert (logging only) | 30s |
| 1 | flag `ENABLE_V3274_RENDER_PICKUP_COMMIT_PRIORITY=False` (hot-reload) lub git revert + restart telegram | 30s flag / 5min revert |
| 2 | flag `ENABLE_V3274_REJECT_ALERT=False` | 30s |
| 3 | per-path: env flag + restart shadow | 5min |
| 4 | tests-only, no rollback | n/a |

---

## 8. Pending decyzje (Adrian decyduje)

1. **Czy FAZA 0 robić jutro lub po master merge gate 10.05?**
2. **FAZA 1 visual marker tilde `~13:17` vs explicit `(ETA)` postfix?** Tilde = mniej intrusive, ale subtelniejszy.
3. **FAZA 3 ścieżka A/B/C — kto decyduje?** Po FAZA 0 data w pełni mam evidence dla rekomendacji, ale Adrian zatwierdza.
4. **Restart dispatch-telegram dziś czy jutro pre-peak?** Default: jutro 08:00 Warsaw przed peak 11-14.
5. **Czy załączyć V3.27.4 reject rate jako KPI w shift summary daily report?**

---

**Status:** PROPOSAL READY. Waiting Adrian ACK przed FAZA 0 launch.
