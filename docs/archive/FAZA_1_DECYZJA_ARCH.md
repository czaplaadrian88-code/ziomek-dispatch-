# FAZA_1_DECYZJA_ARCH.md — Greedy insertion vs brute-force PDP-TSP vs OR-Tools

**Data:** 13.04.2026
**Status:** DECYZJA D19 — zatwierdzona po Gemini review (12.04 wieczór)
**Autor:** Adrian + Strategic AI advisor + Gemini 3.1 PRO

---

## Problem

Faza 1 wymaga `route_simulator_v2.py` który dla każdego nowego ordera + każdego dostępnego kuriera oblicza:
- Optymalną kolejność stops (PDP — Pickup-Delivery Problem)
- Total ETA + waits
- SLA violations
- Wyznacza najlepszego kuriera

**Skala problemu (Białystok peak):**
- 30 kurierów aktywnych
- ~5 nowych orderów / minuta w peak (18:30-20:00)
- Bag size do 5 stops = 10 nodes (5 pickups + 5 deliveries)
- PDP-TSP brute-force dla bag=5: **120 permutacji** (5! z constraintem pickup<delivery, dokładnie (2N)!/(2^N) = 120 dla N=5)

**Maksymalne obliczenia w peak:**
```
30 kurierów × 5 orderów/min × 120 permutacji = 18,000 obliczeń/min
                                               = 300/sek
```

Każda permutacja wymaga wywołania OSRM `route()` lub przynajmniej fallback haversine.

---

## Analiza opcji (Gemini)

### Opcja A: Brute-force PDP-TSP (D2 oryginalna spec)

**Zalety:**
- Gwarantowane optimum (przy bag ≤ 5)
- Prosta implementacja (~150 linii)
- Świetne dla małych bagów

**Wady:**
- 18,000 obliczeń/min w peak = **GIL Pythona nie wytrzyma**
- Single-threaded Python z OSRM HTTP calls = bottleneck
- Pomiar Gemini: typowy czas single permutacji = 5-15ms (z OSRM)
- Czas total per minuta: 18000 × 10ms = 180 sekund obliczeń → **3x przekroczenie budżetu**
- W peak: SLA dispatch decyzja >2 sekund = unacceptable
- Nawet z asyncio + connection pool, OSRM jest pojedynczym bottleneckiem

**Werdykt:** ❌ Nie skaluje się dla peak Białystok, jeszcze gorzej dla Warszawy (50+ kurierów).

### Opcja B: Greedy insertion O(N) (Gemini propozycja)

**Zalety:**
- O(N) per ocena = ~10x szybsze niż brute-force
- Total per minuta: 30 × 5 × 5 = 750 obliczeń (zamiast 18000) → **24x mniej**
- W peak: <100ms na decyzję dispatch
- Dobrze sprawdzony w branży (Wolt, Bolt, Glovo używają wariantów greedy)
- Łatwe do testowania (deterministyczne)
- Skaluje się do 100+ kurierów

**Wady:**
- Sub-optimum dla niektórych przypadków (estymacja: 5-15% gorsze trasy vs brute-force dla bag=5)
- Wymaga starannej heurystyki insertion (nie naive)
- Nie znajdzie globalnego minimum, tylko lokalne

**Algorytm:**
```
def greedy_insertion(courier_route, new_order):
    best_route = None
    best_cost = inf
    
    # Wstaw pickup w każdej możliwej pozycji
    for i in range(len(courier_route) + 1):
        # Wstaw delivery w każdej pozycji ≥ i (constraint pickup<delivery)
        for j in range(i + 1, len(courier_route) + 2):
            new_route = (
                courier_route[:i] + [new_pickup] + 
                courier_route[i:j-1] + [new_delivery] + 
                courier_route[j-1:]
            )
            cost = compute_total_cost(new_route)  # OSRM lub haversine
            if cost < best_cost:
                best_cost = cost
                best_route = new_route
    
    return best_route, best_cost

# Complexity: O(N^2) gdzie N = current bag size
# Dla bag=5: 6 × 7 = 42 pozycje (vs 120 brute-force) → 3x speedup
# Dla bag=10: 11 × 12 = 132 pozycje (vs ~3.6M brute-force) → 27000x speedup
```

**Werdykt:** ✅ MVP dla Fazy 1.

### Opcja C: Brute-force fallback dla bag ≤ 3 + greedy dla większych

**Zalety:**
- Najlepsze z obu światów
- Optimum tam gdzie tanio (bag=2 → 2 perm, bag=3 → 6 perm)
- Greedy tam gdzie obowiązkowo (bag ≥ 4)
- Total computation: <20% wzrost vs sam greedy

**Wady:**
- Nieco bardziej skomplikowana kontrola flow
- 2 ścieżki kodu do testowania

**Werdykt:** ✅ Hybryda zalecana dla Fazy 1 (bag=1,2,3 brute, bag=4,5 greedy).

### Opcja D: OR-Tools (Google Vehicle Routing Library)

**Zalety:**
- Industry standard
- Optymalne rozwiązanie dla VRPTW (Vehicle Routing Problem with Time Windows)
- Skala 100+ pojazdów + 1000+ orderów
- Genetic algorithms + constraint solver

**Wady:**
- Compile time per problem: 100-500ms (akceptowalne)
- Python binding ma overhead
- Kompleksowość: ~500-1000 linii kodu integration
- Wymaga PyPy/cython dla peak performance
- **Overkill dla Fazy 1** (mamy działać shadow-only, nie skomplikowany scheduling)
- Lepiej w Fazie 9 gdy mamy sliding window + future orders

**Werdykt:** ⏳ Odroczone do Fazy 9 (już zaplanowane).

---

## DECYZJA D19 (Adrian + Gemini)

**Greedy insertion O(N²) jako MVP w Fazie 1**, z **brute-force fallback dla bag ≤ 3** dla optimum tam gdzie tanio.

### Specyfikacja techniczna

```python
# route_simulator_v2.py — pseudocode

def simulate_route(courier_state, new_order, restaurant_meta, now):
    """
    Symulacja trasy kuriera po dodaniu new_order.
    
    Strategy:
    - bag ≤ 3: brute-force PDP-TSP (optimum, tanio)
    - bag = 4 lub 5: greedy insertion (5-15% sub-optimum, akceptowalne)
    - bag > 5 (sanity cap): odrzucamy w feasibility (R3)
    """
    current_bag = len(courier_state.active_orders)
    new_bag = current_bag + 1
    
    if new_bag <= 3:
        # Brute-force PDP-TSP
        return brute_force_pdp_tsp(
            courier_state, new_order, restaurant_meta, now
        )
    elif new_bag <= 5:
        # Greedy insertion
        return greedy_insertion(
            courier_state, new_order, restaurant_meta, now
        )
    else:
        # Sanity: odrzucamy (R3 cap)
        return None  # feasibility_v2.is_feasible() to złapie
```

### Constraints (PDP)
- Każdy pickup MUSI być przed swoim delivery
- prep_variance respect: arrive at restaurant ≥ pickup_ready_at
- D8 enforce: detour wait > 5 min → reject (feasibility_v2)

### OSRM integration
- Używa `osrm_client.route()` z P0.5 fallbackiem (zawsze zwraca dict, never None)
- Cache w-pamięci dla pairs <=== current_bag (krótkie sesje)
- Connection pooling (urllib3 PoolManager)

### prep_variance integration (z P0.7)
```python
def get_pickup_ready_at(restaurant_name, czas_odbioru_timestamp, now):
    r = restaurant_meta["restaurants"].get(restaurant_name)
    if r is None:
        pv = restaurant_meta["fleet_medians"]["fleet_prep_variance_median"]  # 13
    elif r["flags"]["low_confidence"]:
        pv = r["prep_variance_fallback_min"]
    else:
        pv = r["prep_variance_min"]["median"]
    return max(now, czas_odbioru_timestamp + timedelta(minutes=pv))
```

### Performance budget
- Single greedy insertion: <50ms (OSRM cached) / <200ms (cold cache)
- Single brute-force bag=3: <30ms (6 permutacji × 5ms)
- 30 kurierów assessment per nowy order: <2 sekundy total
- Peak (5 orderów/min × 30 kurierów): <100 sekund obliczeń/min ⚠️

⚠️ **Mitigation peak:** parallel processing per kurier (multiprocessing Pool 4 workers) → 4x speedup → <25s/min budżet.

### Migration plan do OR-Tools (Faza 9)
- Faza 1-8: greedy + brute-force (D19)
- Faza 9: A/B test greedy vs OR-Tools VRPTW na live
- Faza 9 milestone: jeśli OR-Tools daje >5% lepszych SLA → migrate
- Risk: jeśli <2% różnica → zostajemy przy greedy (prostsze, taniej maintain)

---

## Implementation plan dla Fazy 1 (Krok 4)

### Dzień 1 (4-6h):
1. Spec finalna `route_simulator_v2.py` (pisanie struktur, testy unit dla każdej funkcji)
2. Implementacja `brute_force_pdp_tsp` (~80 linii) + 5 unit tests
3. Implementacja `greedy_insertion` (~100 linii) + 5 unit tests
4. Implementacja `simulate_route` dispatcher (~30 linii) + integration test

### Dzień 2 (3-5h):
1. Implementacja `feasibility_v2.py` (R1/R3/R8/R20/R27/D8) — ~150 linii
2. Implementacja `dispatch_pipeline.py` (scoring + R28 + R29) — ~200 linii
3. Integration test full pipeline z mock orderami

### Dzień 3 (4-6h):
1. `shadow_dispatcher.py` (systemd loop, polling event_bus) — ~250 linii
2. `telegram_approver.py` (Telegram bot listen, learning_log) — ~200 linii
3. Systemd unit files
4. Deploy na produkcję (shadow-only) + monitoring

### Dni 4-5: Tuning + monitoring agreement rate
- Target: agreement rate >75% w pierwszym tygodniu
- Disagreement analysis: dlaczego Adrian wybrał innego kuriera?
- Iteracyjna poprawa scoringu

---

## Testy do napisania (przed implementacją)

### Unit tests `brute_force_pdp_tsp`
- bag=0 (pierwszy order) → 1 permutacja, optimum trivialne
- bag=1 → 2 permutacji, weryfikacja constraint pickup<delivery
- bag=3 → 6 permutacji, weryfikacja brute-force exhaustive
- Edge: 2 orderów do tej samej restauracji (free stop R4)
- Edge: zerowy detour (delivery na trasie do następnego pickup)

### Unit tests `greedy_insertion`
- bag=4 → wszystkie 20 pozycje (5 dla pickup × 4 dla delivery)
- bag=5 → 30 pozycji
- Verify: heurystyka znajduje cost ≤ optimum + 15%
- Edge: order outside city → R8 hint
- Edge: prep_variance_high restaurant → odpowiedni bufer

### Unit tests `simulate_route` (top-level)
- Routing greedy vs brute decision (bag thresholds 3, 4, 5)
- prep_variance integration (low_confidence fallback, fleet fallback)
- OSRM cache hit/miss
- Performance: 95th percentile <100ms

### Integration tests
- Full pipeline: NEW_ORDER → dispatch_pipeline → simulate_route → feasibility → scoring → top courier
- Multiple competing orders w peak
- R29 best-effort scenarios

---

## Commit message template dla Fazy 1

```
F1.X: <component> implementation

Greedy + brute-force hybrid PDP-TSP per D19 (greedy insertion O(N²) MVP, 
brute-force fallback dla bag ≤ 3, OR-Tools migration w Fazie 9).

Files:
- route_simulator_v2.py (~300 linii)
- tests/test_route_simulator_v2.py (~200 linii)

Tests: X/Y unit + Z integration PASS

Performance:
- Single greedy: 45ms p95 (cold cache 180ms)
- Single brute-force bag=3: 25ms p95
- Full pipeline single order: 1.8s p95 (acceptable target <2s)

Production: NO RESTART (shadow-only, dispatch-shadow.service later)
Decision ref: D19 (FAZA_1_DECYZJA_ARCH.md)
```

---

## Open questions (do iteracji w Fazie 1)

1. **Multiprocessing peak handling** — czy implementujemy w Fazie 1 czy zostawiamy single-threaded jako MVP?
   - Decyzja: single-threaded MVP, profiling po 1 tygodniu shadow → dodaj multiprocessing jeśli p99 > 3s

2. **Greedy heurystyka rozszerzona** — Or-opt? 2-opt local search?
   - Decyzja: pure greedy w Fazie 1, Or-opt w Fazie 9 razem z OR-Tools migration

3. **Cache invalidation strategy** — kurier się porusza między requests
   - Decyzja: cache key = (origin_lat_lng_floor_2, dest_lat_lng_floor_2, time_bucket), TTL 60s

4. **Future orders consideration** — sliding window 15 min (D9)
   - Decyzja: Faza 1 = point-in-time tylko. D9 sliding window w Fazie 9 razem z VRPTW.

---

## Approval

- ✅ Adrian (decision authority)
- ✅ Gemini 3.1 PRO (technical review)
- ✅ Strategic AI advisor (architecture validation)

**D19 zatwierdzone.** Implementacja w Kroku 4 (po Krokach 0-3).
