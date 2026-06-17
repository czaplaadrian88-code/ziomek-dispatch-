# FAZA 1 — DESIGN-LOCK: food-age hard-SLA (warm-start + twardy span)

**Bazuje na:** `PHASE0_ROOTCAUSE_VERDICT.md` (62% bud­żet / 38% strukturalne) + decyzja Adriana 17.06: **Hybryda**.
**Zasada:** kod TYLKO za sub-flagą `ENABLE_OBJ_FOOD_AGE_HARD_SLA` (default OFF), komponuje się z `ENABLE_OBJ_DELIVERY_FOOD_AGE`. Stary additive nietknięty. **Gwarancja DoD: ON ≤ OFF z konstrukcji.**
**Status:** czeka na ACK przed Fazą 2 (implementacja).

---

## 1. Fakty z kodu (zweryfikowane)
- `tsp_solver.py`: TIME_SCALE=100, SCALE=1000. `time_dimension` = `routing.GetDimensionOrDie` po `AddDimension(...,"Time")` (l.169). Pary pickup→drop: pętla l.344-363 (`AddPickupAndDelivery` + ordering). Soft R6/food-age: l.234-342.
- `route_simulator_v2.py`: `need_pickup = new_order.status != "picked_up"` (l.256); `bag_pickup_idxs_by_oid` = **tylko pending** bag items dostają węzeł pickup (l.269); **już-odebrane bag items = sam węzeł delivery (BEZ pickup)**. Solve+fallback: l.1162-1206.
- OR-Tools 9.15.6755: `ReadAssignmentFromRoutes` + `SolveFromAssignmentWithParameters` DOSTĘPNE.
- METRYKA `_count_sla_violations` (l.612): anchor `pickup_at`(TSP)|`picked_up_at`, breach `delivery−pickup>35`.

**Partycja twardego ograniczenia (czysta, wynika ze struktury węzłów):**
- zlecenie **odbierane w planie** (jest w `pickup_drop_pairs`: pending bag + new gdy need_pickup) → **span** `CumulVar(drop)−CumulVar(pickup) ≤ sla`.
- zlecenie **już-odebrane** (delivery node spoza par, `_picked`) → **SetMax** `CumulVar(drop) ≤ (picked_up_at−now)+sla`.

---

## 2. Zmiany w `tsp_solver.py` (sygnatura + 2 bloki)

### 2a. Nowe parametry `solve_tsp_with_constraints(...)`
```
delivery_sla_hard_span: bool = False,           # włącz twardy span na parach pickup→drop
delivery_sla_hard_bounds: Optional[List[Optional[float]]] = None,  # per-stop: bound[min od startu] dla JUŻ-ODEBRANych delivery (None=pomiń)
sla_minutes_hard: float = 35.0,                 # próg span (= sla)
warm_start_routes: Optional[List[List[int]]] = None,  # hint: lista tras (node-idx, bez depo) — zwykle 1 trasa
```

### 2b. Twardy span — w istniejącej pętli par (po l.363, w bloku `for pickup_idx, drop_idx in pickup_drop_pairs`)
```python
if delivery_sla_hard_span:
    _sla_scaled = int(round(sla_minutes_hard * TIME_SCALE))
    routing.solver().Add(
        time_dimension.CumulVar(drop_index) - time_dimension.CumulVar(pickup_index)
        <= _sla_scaled)
```
(pickup_index/drop_index już policzone w tej pętli)

### 2c. Twardy SetMax dla już-odebranych — NOWY blok po pętli par (przed `fixed_first_drop`, ~l.364)
```python
if delivery_sla_hard_bounds is not None:
    capacity_max = int(max_route_min * TIME_SCALE)
    for stop_idx in range(num_stops):
        b = delivery_sla_hard_bounds[stop_idx]
        if b is None:
            continue
        scaled = max(0, min(int(b * TIME_SCALE), capacity_max))
        time_dimension.CumulVar(manager.NodeToIndex(stop_idx)).SetMax(scaled)
```

### 2d. Warm-start — przy wywołaniu solvera (zamiast/obok `SolveWithParameters`)
```python
if warm_start_routes:
    routing.CloseModelWithParameters(search_parameters)
    initial = routing.ReadAssignmentFromRoutes(warm_start_routes, True)  # ignore_inactive_indices=True
    solution = (routing.SolveFromAssignmentWithParameters(initial, search_parameters)
                if initial is not None else
                routing.SolveWithParameters(search_parameters))
else:
    solution = routing.SolveWithParameters(search_parameters)
```
⚠ `ReadAssignmentFromRoutes` wymaga node-idx (NIE routing-idx) tras BEZ węzła startu (0). Gdy hint niespójny z ograniczeniami → `initial=None` → graceful fallback do zwykłego solve.

---

## 3. Orkiestracja w `route_simulator_v2.py` (przepływ ON≤OFF)

Gdy `ENABLE_OBJ_DELIVERY_FOOD_AGE` ON **i** `ENABLE_OBJ_FOOD_AGE_HARD_SLA` ON:

```
1. BASE solve (jak dziś: R6-soft+span+committed/fresh, BEZ food-age, BEZ hard-span) @200ms
   → base_solution. (To DOKŁADNIE dzisiejszy plan = SLA-safe fallback + źródło warm-startu.)
2. ON solve: te same kary + food-age + delivery_sla_hard_span=True + delivery_sla_hard_bounds
   + warm_start_routes=[base_solution.sequence] @ _ot_ms (warm-start → zbiega szybko).
3. Drabina fallbacku (gwarancja ON≤OFF):
   a. on_solution None/empty (infeasible: doomed-bag / over-constrained) → użyj base_solution.
   b. zbuduj on_plan; jeśli on_plan.sla_violations > base_plan.sla_violations
      (rzadki rozjazd time↔realizacja) → użyj base_plan.
   c. inaczej → użyj on_plan (zysk food-age, SLA ≤ base).
```

Gdy food-age ON ale hard-SLA OFF → ścieżka jak dziś (additive, l.1058-1093).
Gdy food-age OFF → bez zmian (single solve).

**`delivery_sla_hard_bounds` budowane obok food-age (l.1065+):** dla delivery node `_picked` → `(picked_up_at−now)+sla`; dla pending/new (w parach) → None (chroni je span). Kotwica = METRYKA (picked_up_at), NIE ready.

**Koszt latencji:** +1 solve TYLKO na ścieżce food-age-ON; warm-startowany → krótki. base = dzisiejszy koszt (nie dodatkowy). To NIE trap Opcji-2 (tam 2× pełny solve bez hintu).

---

## 4. Sub-flaga + stałe (`common.py` + `flags.json`)
- `ENABLE_OBJ_FOOD_AGE_HARD_SLA` → lista `ETAP4_DECISION_FLAGS` (kanon flags.json, default OFF w common.py).
- reuse `sla_minutes` (=35) jako próg span. Brak nowych magic-numberów.

## 5. Testy (Faza 3)
- **Jakub→B zachowany** (case SLA-neutralny: food-age nadal reorderuje, sla=0).
- **fixture GENESIS** z drill-downu (np. 17:32:11 bag=7): pod hard-SLA on_plan.sla_violations ≤ base. 
- **doomed-bag** (≥1 zlecenie nie zmieści 35 w żadnej kolejności) → infeasible → fallback == base (bajt-w-bajt sequence).
- **already-picked SetMax**: odebrane zlecenie nie przekracza picked_up_at+35.
- **warm-start niespójny** → initial=None → graceful (nie crash).
- regresja OBJ/R6/tsp/span/freshness/committed zielona. Testy PINUJĄ kontekst prod (`ENABLE_OBJ_R6_SOFT_DEADLINE/SPAN=True`, majority-of-N — lekcja FA-T2/T5 + niedeterminizm 200ms).

## 6. Replay-walidacja (Faza 4, GO/NO-GO) — bramki
- `foodage_phase0_artifact_vs_structural.py` zmodyfikowany: trzecia gałąź „hard-SLA ON" — oczekiwane: **regresje SLA ≈ 0** (artefakt+strukturalne pokryte), serious=0, genesis=0.
- `foodage_regression_drilldown.py --max 8000` na hard-SLA: regresje→0.
- resztkowy benefit food-age (changed-rate + thermal+) zachowany na SLA-neutralnych.
- latencja: brak regresu p95 na shadow (warm-start ma trzymać single-ish solve).

## 7. Ryzyka
- **Warm-start invaliduje pod hard-span** (base nie spełnia nowych ograniczeń bo base ich nie miał) → `ReadAssignmentFromRoutes` zwróci initial=None → graceful fallback do zwykłego solve @ _ot_ms. Mitygacja OK, ale wtedy tracimy przyspieszenie → monitorować ile % hint odrzucony.
- **Rozjazd time↔realizacja** (rzadki) → łapie post-solve guard 3b.
- **Niedeterminizm 200ms** → testy/replay majority-of-N.
- **CloseModelWithParameters wołane raz** — upewnić się że nie koliduje z istniejącą ścieżką (dziś `SolveWithParameters` zamyka model auto). Pod warm-start trzeba jawnego close.

## 8. Rollback
- `ENABLE_OBJ_FOOD_AGE_HARD_SLA=false` (hot) → wraca additive (a food-age i tak OFF). Twardy: `git revert` + restart dispatch-shadow. `.bak` per plik.

## 9. Pliki Fazy 2
`tsp_solver.py` (params + 3 bloki + warm-start) · `route_simulator_v2.py` (hard_bounds build + orkiestracja base/ON/fallback) · `common.py` (sub-flaga) · `flags.json` · `tests/test_obj_food_age_bug5.py` (+fixtures genesis/doomed/already-picked).
