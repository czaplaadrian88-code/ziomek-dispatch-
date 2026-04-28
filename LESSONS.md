# LESSONS — Ziomek dispatch_v2

Architectural lessons learned per sprint, sequential numbering. Reguły mają
applicability dla future sprints — nie tylko historical record. Każda lekcja
zawiera Problem, Konsekwencje, Reguła, Identical pattern do (cross-references).

---

## Lekcja #28 (V3.27.1 sesja 2 ROLLBACK + sesja 3 vindication)

### Problem
Mock unit tests z fake schema klucz `{"czas_kuriera_warsaw": "..."}`
PASSED 9/9. Integration FAILED w produkcji — real panel API zwraca
raw response z `czas_kuriera` HH:MM, klucz `czas_kuriera_warsaw` NIE
istnieje w raw, jest computed downstream przez `panel_client.normalize_order()`.

### Konsekwencje
- Sesja 2 atomic flag flip 19:05 Warsaw → CASE C RED w 5 critical
  checks decision matrix
- Latency 6949ms (9.5x baseline) z error path amplification per emit
- 10 ERROR linii "skipping persist" + state_machine sanity FAIL
- Rollback w 5 min (env override pattern + git reset)

### Reguła
Mock unit tests z fake schema = false confidence. **Integration
tests z real `panel_client.normalize_order` flow REQUIRED** dla
wszystkich helpers wywołujących panel_client.

Pattern dla testów: mock external HTTP boundary (panel API raw
response), use real internal logic (normalize_order, validation).
Edge case test dla normalize_order None return (status 7/8/9
delivered/cancelled).

### Identical pattern do
- **Lekcja #1** (Parse wrapper invisible data loss V3.19f) —
  panel_client zwracał `raw.get("zlecenie")` bez innych top-level keys
- **Lekcja #18** (Empirical validation > unit test V3.27)

---

## Lekcja #29 (V3.27.1 sesja 3 NEW)

### Problem
Sesja 3 atomic flag flip post Bug 1 fix → latency 6748ms (RED)
mimo że Bug 1 fix DZIAŁA (zero state_machine errors).

### Diagnoza
`panel_client.fetch_order_details` używał login refresh co 22 min
(CSRF token expiry). Logowanie zajmuje 6-7s.

Pre-V3.27.1: panel_watcher async (off proposal latency path) —
login refresh niewidoczne dla user.

V3.27.1 sesja 3: pre_proposal_recheck używa fetch_order_details
**synchronicznie w dispatch_pipeline** (proposal latency path) →
login refresh propaguje do proposal latency.

### Smoking gun
5 proposals post-restart 19:06:
- 3 proposals (568, 280, 680ms) = no login = ✓
- 2 outliers (6748, 7604ms) = 100% korelacja z login refresh events

Math projection lunch peak: 50-100 props/h × 3 logins/h = **3-6%
outliers rate** (overnight verify 1/16 = 6% match).

### Reguła
**Sync calls w hot path mogą ujawnić latency istniejących
operacji niewidocznych off-path.**

Przy dodawaniu sync calls do hot path, **audit istniejących
architectural assumptions** call'owanego komponentu. Off-path
latency tolerance ≠ on-path tolerance.

Pre-deploy audit: dla każdego sync addition, prześledź call chain
od start do end, dla każdego service dependency identyfikuj
off-path overhead który teraz staje się on-path (login refresh,
connection pool init, timeout retries, periodic blocking ops).

### Fix progressive enhancement
- **A) Tolerate** (zero effort, partial — peak math 3-6% rate ok)
- **B) Pre-warm login startup** (5 min, eliminates first-proposal
  cold start) — sesja 4 jutro
- **C) Background login refresh thread** (30-60 min, complete fix,
  V3.28 strategic Warsaw expansion)

### Identical pattern do
- **Lekcja #20** (Strategic decision principle — quality + scaling
  > pragmatic shortcuts)
- **Lekcja #22** (Distance matrix z traffic multipliers — KAŻDA
  kalkulacja używana do TSP/scoring/ETA MUSI iść przez
  `get_traffic_multiplier()`)

---

## Cross-reference do TECH_DEBT.md "📚 LEKCJE V3.27"

Pełne lessons history w `TECH_DEBT.md` sekcji "📚 LEKCJE V3.27 (added)":
- #25 Mental simulation może być naivny (V3.27 Bug Y)
- #26 Domain knowledge > LLM/API confidence (V3.27 Filipowicza)
- #27 Hardware oversubscription dla parallel (V3.27 CPX22)
- #28 Mock tests passed ale integration FAIL (V3.27.1 sesja 2 Bug 1) ← above
- #29 Sync calls hot path ujawniają latency niewidoczną off-path
  (V3.27.1 sesja 3 panel_client login refresh) ← above

LESSONS.md = curated subset (krytyczne lekcje sesji), TECH_DEBT.md = full history
z context tickets/bug refs.

---

## Lekcja #30 (V3.27.3 sprint 27.04 wieczór)

**Recurring user decisions = explicit "CO JUŻ USTALONE" handoff section**

### Problem

Sesja 27.04 V3.27.3 ujawniła że recurring decisions nie były internalizowane
przez chat session. Konkretne case'y:

1. **Sweet-spot ambiguity:** "5 min sweet spot dla wait kuriera" (TASK B) vs
   "20 min sweet spot dla przedłużenia restauracji" (V327 wait penalty).
   Adrian musiał powtórzyć tę decyzję 4+ razy zanim została poprawnie
   utrwalona w specyfikacji TASK B.

2. **Glossary drift:** Terminy "breach" i "lateness" wszedłszy do CC słownictwa
   wprowadzały zamieszanie semantyczne. Adrian preferuje polskie terminy:
   "naruszenie zadeklarowanego czas_kuriera" zamiast "breach", "TSP planuje
   pickup poza R27 ±5" zamiast "lateness".

3. **Frozen vs new order distinction:** Adrian's zasada "czas_kuriera po
   przypisaniu = nietykalny" musiała być wyciśnięta z prompt-u 3 razy zanim
   została pełnie zoperacjonalizowana w V3.27.4.

### Konsekwencje

- Dłuższy session time (każda powtórka = 5-10 min back-and-forth)
- Adrian zmęczenie (sesja 14h+ tego dnia)
- Ryzyko bug-introduction gdyby jakaś decyzja została źle zinterpretowana

### Reguła

1. **Każdy nowy CC prompt zaczyna sekcją "CO JUŻ USTALONE"** z explicit listą
   decyzji + glossary terminów + timestamps.

2. **Plik wiedzy session-handoff** dostaje stałą sekcję "GLOSSARY V3.27.3+"
   i "KEY DECISIONS" z timestamps i context.

3. **Adrian preferencje terminologii:**
   - Polskie terminy nad angielskimi (gdzie sensowne semantycznie)
   - Explicit nad shortcuts ("zadeklarowany czas_kuriera" nie "ck", "naruszenie
     R27 ±5" nie "breach")
   - Zasady wymieniane explicitly nad assumed (np. "czas_kuriera po przypisaniu
     = nietykalny" jako wymagane wstęp do każdego TSP-related task)

4. **Pre-implementation verify per Lekcja #5/#19/#26:** każda decyzja Adrian'a
   confirmed explicitly w prompt PRZED implementacją (np. "Q1: flag default
   True czy False? Q2: detection logic Adrian's simple pattern OK?").

### Identical pattern do

- **Lekcja #5/#19/#26** (Pytaj nie zgaduj — pre-implementation grep + verify)
- **Lekcja #11** (Adrian decision matrix wymaga explicit pytań)
- **Lekcja #20** (Strategic principle — quality + scaling > shortcuts also
  applies do communication patterns)

---

## Lekcja #31 (V3.27.5 sprint 27.04 wieczór late)

**Chain of bugs: state machine handler + downstream consumer = double-fix wymagana**

### Problem

TASK H diagnoza #469099 (2026-04-27 wieczór) ujawniła **chain of bugs** — żadne pojedyncze
miejsce nie było w pełni odpowiedzialne, dwa współpracujące błędy tworzyły bug:

1. **State_machine bug:** `COURIER_ASSIGNED` handler unconditionally setting
   `status="assigned"` bez guard dla terminal states. Panel_diff post-PICKED_UP
   (race ~12-18s) nadpisywał status="picked_up" → "assigned", picked_up_at preserved.

2. **Downstream consumer bug:** `_bag_dict_to_ordersim` używał TYLKO field
   `status` jako primary signal picked_up vs not, ignorując picked_up_at SET.
   Przy state inconsistency (post-revert), simulator misclassyfikował picked-up
   jako assigned → pickup-node added do TSP graph.

3. **Cascade konsekwencja:** TSP frozen window (V3.27.4) correctly fired
   constraint (0,5) dla bogus pickup-node, INFEASIBLE → fallback bez constraints
   → plan z pickup_at dla picked-up orderów.

Bug rate: **13.4%** (185/1384 picked-up orders w 7 dni). Systematic, NIE edge case.

### Konsekwencje

- Plan trasy zawiera pickupy dla picked-up orderów godzinę temu (operator
  confusion + R6 SLA violation false positive).
- TSP fallback mask root cause — wygląda jak working solver ale plan jest bogus.
- Lunch peak validation under risk dopóki fix nie applied.

### Reguła

**Defense-in-depth across layer boundaries:**

1. **Każdy state machine handler nadpisujący status MUSI guard terminal states**
   (`picked_up`, `delivered`, `cancelled`). Pattern: `prev = get_order(oid)`,
   `if prev.status in TERMINAL: preserve`.

2. **Każdy downstream consumer MUSI prefer canonical signal nad derived**:
   - `picked_up_at != None` jest canonical (monotonic, terminal)
   - `status` jest derived (mutable, race-prone)
   - Use `is_picked_up = (status == "picked_up") OR (picked_up_at is not None)`

3. **PRE-FIX VERIFY OBLIGATORY** dla bug fixów:
   - Q1: Wszystkie consumers field X — czy fix at boundary protects all?
   - Q2: Wszystkie writers field X — czy są inne miejsca z tym pattern?
   - Q3: Counter-pattern (places already using correct signal)
   - Q4: Cycle frequency (timing/race characteristics)
   - Q5: **Historical bug rate** (count similar cases w 7-30 dni). >10% = systematyczny.

### Identical pattern do

- **Lekcja #1** (Invisible data loss — silent state mutation)
- **Lekcja #28** (Mock tests passed but integration FAIL — race conditions
  visible only w real shadow log replay)
- **Lekcja #30** (Recurring user decisions = explicit handoff)

---

## Lekcja #32: Silent except = invisible bug (V3.27.6, 2026-04-28)

### Co się stało

V3.27.4 frozen window violations (2/22 propozycji 9.1% applicable rate, #469099 +65min, #469150 +26.7min). Hipoteza H1 (czas_kuriera_warsaw NIE propaguje) REJECTED via izolowany repro — `dispatch_pipeline.py:949` poprawnie set'uje atrybut. Synthetic V3.27.4 fires correctly. Production NIE fires. ZERO INFEASIBLE retries logged.

Investigation `route_simulator_v2.py:781-800` ujawniło **silent `except Exception:`** w time_windows construction:
```python
try:
    open_min = max(0.0, (ready - now).total_seconds() / 60.0)
    ...
    if czas_kuriera_committed:
        time_windows.append((window_open, window_close))  # frozen 5-min
    else:
        time_windows.append((open_min, close_min))         # 60-min
except Exception:
    time_windows.append((0.0, V327_DROP_TIME_WINDOW_MAX_MIN))  # silent fallback (0, 120)
```

Każda exception w try block → fallback (0, 120) min effectively no constraint → V3.27.4 frozen window NIE applied dla tego pickup → TSP plan poza window without warning.

### Dlaczego to zła praktyka

- **Invisible silent failure** — fallback applied, NIE log entry, NIE error
- **Empirical evidence destroyed** — debugging niemożliwy bez instrumentacji
- **Defense-in-depth illusion** — code "wygląda jak ma fallback" ale fallback maskuje root cause
- **Hipothesis chase** — Adrian's H4 (slack relaxation) byłby reakcją na hipotezę bez evidence; rzeczywisty mechanizm prawdopodobnie był silent except

### Reguła

**Każdy `except Exception:` w hot path MUSI logować context** (oid, type, repr, exception type+repr). Pattern V3.27.6 FIX 2a:

```python
try:
    ...
except Exception as _exc:
    fallback = (0.0, MAX_MIN)
    time_windows.append(fallback)
    _log.warning(
        f"V3274_TIMEWINDOW_FALLBACK oid={_oid} ck_type={type(_ck).__name__} "
        f"ck_repr={repr(_ck)[:80]} ready={ready!r} now={now!r} "
        f"except={type(_exc).__name__}: {repr(_exc)[:120]} fallback_window={fallback}"
    )
```

**Effort = 5 min, value = empirical signal w produkcji bez osobnego probe sprintu.**

### Identical pattern do

- **Lekcja #1** (Invisible data loss — silent state mutation)
- **Lekcja #28** (Mock tests passed but integration FAIL)

---

## Lekcja #33: Empirical-first design — pushback nad hipothesis-first (V3.27.6, 2026-04-28)

### Co się stało

Adrian zaproponował V3.27.6 sprint: FIX 1 Path C robust detection + FIX 2 hard cumul constraint w tsp_solver. Hard cumul motywowany hipothezą H4 (slack 12000 pozwala OR-Tools "ExpandTime" obejść SetRange).

CC pre-implementation investigation `tsp_solver.py` ujawniło 3 fundamental assumption mismatches:

1. **`tsp_solver.solve_tsp_with_constraints` jest pure function bez OrderSim/decision_ts** — `node_to_order_map` z Adrian's plan NIE EXISTS w solver scope
2. **`time_dimension.CumulVar.SetRange(open, close)` (linia 153-156) IS already the hard cumul constraint** — V3.27.4 already używa tego mechanizmu via `time_windows` arg
3. **Slack semantyka OR-Tools**: `slack` w `AddDimension` to MAX WAIT na node, NIE bypasses CumulVar bounds. H4 hipoteza traci wagę po code review

CC zadał Q1+Q2+Q3 STOP-and-ask. Adrian acceptował pushback i zmienił scope:
- FIX 2 zamiast hard cumul → diagnostic logging (FIX 2a) + post-solve assertion (FIX 2b)
- "Empirical first — silent except w :799 to konkretny kod-fakt który widzisz, hard cumul był reakcją na hipotezę bez evidence"

Result: 1 sprint zamiast 2 (probe sprint jutro skipped), faster verdict (lunch peak 29.04 zamiast 30.04+), no scope creep.

### Reguła

**Pre-implementation investigation OBLIGATORY** dla architectural changes. Sequencja:

1. Read kod TARGET pliku (np. tsp_solver.py) PRZED implementacją
2. Verify każda assumption Adrian's plan'u: variable existence, scope, signature, semantyka API
3. Jeśli ANY uncertainty/mismatch → **STOP, ask, NIE zgaduj** (Adrian's explicit rule + Lekcja #5/#19/#26)
4. Pushback nad hipothesis fix gdy:
   - Konkretny code-fact znaleziony (silent except, type mismatch, race condition)
   - Hipoteza bez instrumentation evidence (mental simulation only)
   - Synthetic repro NIE potwierdza hipotezy (V3.27.4 fires correctly w synthetic)

### Pattern dla future sprints

- **PRE-IMPLEMENTATION INVESTIGATION** = osobny krok przed code, dokumentowany w sprint plan
- **STOP-and-ask Q1+Q2+Q3** gdy uncertainty — Adrian's rule, NIE zgaduj
- **Empirical-first design** = preferuj fix dla observed code-fact nad fix dla hipothesis
- **Diagnostic instrumentation** = często lepszy ROI niż defensive duplicate mechanism

### Identical pattern do

- **Lekcja #25** (Mental simulation może być naivny — verify w shadow nie tylko intuition)
- **Lekcja #26** (Domain knowledge > LLM/API confidence — Adrian's local knowledge wins)
- **Lekcja #30** (Recurring user decisions = explicit handoff)

---

## Lekcja #34: Restart-in-peak hard rule WYJĄTEK gdy Ziomek bezużyteczny (V3.27.6, 2026-04-28)

### Co się stało

V3.27.4 frozen window violations LIVE w produkcji (2 cases confirmed, possibly more silent). Adrian's standard rule: NIE restart dispatch-shadow w peak window (Pn-Pt 11-14, 17-20 Warsaw, sobota 16-21).

Rano 28.04 V3.27.4 probe instrumentation deploy → regresja (60 sec damage window, 2 propozycje stracone) — restart-in-peak risk materialized.

V3.27.6 sprint deploy timing decision:
- **Standard rule**: defer do post-peak 20:00+
- **Adrian's override**: peak NIE blokuje, "Ziomek z bugiem = bezużyteczny", restart-in-peak rule WYJĄTEK
- **Justification**: continuing bug (silent V3.27.4 violations) > restart cost (~5-10s downtime, 1-2 propozycje window)

Decision matrix:
- Bug severity: 9.1% applicable rate → moderate, ale Ziomek's value proposition broken (Adrian's eyeball NIE może verify czas_kuriera respekt)
- Restart cost: 5-10s downtime, ~1-2 propozycje per restart
- Risk regression: zmniejszony post-rano-incident (E2E smoke przez assess_order, static lint scope check, 57/57 tests)

### Reguła

**Restart-in-peak hard rule** standard MA wyjątek gdy:

1. **Ziomek z bugiem = bezużyteczny** (np. propozycje silent invalid → Adrian musi double-check każdą manualnie)
2. **Continuing bug > restart cost** quantitative (bug rate × peak duration > restart downtime × propozycje rate)
3. **Pre-deploy verification thorough** post-incident (E2E smoke, static lint, test coverage)

Wyjątek **wymaga explicit Adrian override** ("eksplicite peak NIE blokuje"). NIE auto-decydować.

### Identical pattern do

- **Lekcja #29** (Sync calls hot path = architectural exposure — fix priority bo blokuje hot path)
- **Lekcja #31** (Defense-in-depth across layers — multiple fix points lepiej niż jeden)

