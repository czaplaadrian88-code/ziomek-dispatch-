# Plan naprawy objective OR-Tools — sprint „OBJ" (2026-05-17, v2)

Spina diagnozy: 474266 (naprawione E1-E3), 474253 (idle), 474297 (thermal/R6).
v2: domknięte 2 luki zgodności z regułami + E4 wciągnięte jako F4.

## Problem (jedno zdanie)
`_ortools_plan` minimalizuje WYŁĄCZNIE czas jazdy. Model nie zna: (1) kosztu
idle/czekania, (2) kosztu thermal/wieku odebranego jedzenia, (3) R6 (35 min)
jako constraintu, (4) priorytetu picked-up. P3D1 (proxy idle) jest zepsuty.

## Idea architektoniczna
Time dimension OR-Tools już śledzi skumulowany czas w każdym węźle (`CumulVar`).
To dźwignia — wszystko brakujące wyrażamy natywnie:
- **R6 + thermal** → `SetCumulVarSoftUpperBound` na węzłach DELIVERY: miękki
  deadline = `pickup_anchor + 35 min`. Picked-up order → anchor=`picked_up_at`
  (stare jedzenie ma deadline blisko/za nami → ogromna kara → solver wypycha
  dostawę na początek = zastępuje crude `lock_first`, graded). Soft, nie hard:
  gdy R6-doomed solver MINIMALIZUJE przekroczenie zamiast INFEASIBLE.
- **idle** → koszt SPAN trasy (`SetSpanCostCoefficient` na time dimension):
  czekanie nadyma span → solver unika idle. To „throughput per shift".
- **P3D1** → wyrzucić (zepsuty, dominuje objective szumem).

Feasibility R6 hard-gate ZOSTAJE (post-hoc) — TSP miękko prowadzi, feasibility
twardo bramkuje. `_simulate_sequence` nadal liczy realny zegar (źródło prawdy).

## Zgodność z regułami biznesowymi (audyt 2026-05-17)
HARD: R-DECLARED-TIME ✅ (`_simulate_sequence` clampuje pickup do ready_at),
R-SCHEDULE-AWARE ✅ (nietykane), R1/R8/bag-caps ✅ (liczone z bagu, niezależne
od sekwencji). R-35MIN-MAX ⚠ — patrz „kryterium R6" niżej. SOFT: R-NO-WASTE ✅
(plan to naprawia), R-BUFFER-OK ✅ (koszty, nie progi eliminujące),
R-PRIORYTETÓW/R-FLEET-LEVEL ✅ (osobna warstwa — scoring). FILOZ-4 ✅
(thermal graded — interleave taniego pickupu nadal OK; doprecyzowanie, nie złamanie).

## Fazy (każda: `.bak` → testy → commit+tag → restart dispatch-shadow off-peak → ACK)

### F0 — harness + instrumentacja + encoding checklist  [~0.5-1 dnia]
- **Offline replay harness:** replay N≈300 realnych cases z `shadow_decisions`
  przez stary vs nowy objective, diff sekwencji + metryk. Empirical-first
  (Lekcja #33). Zero prod.
- **Instrumentacja:** per-kandydat metryki `idle_total_min`, `max_thermal_age_min`,
  `r6_breach_max_min`, `route_span_min`.
- **Encoding checklist (5 miejsc — Lekcja #109, luka z 17.05 `dwell_`):** nowe
  metryki MUSZĄ wejść do: kod + testy + serializer `_AUTO_PROP_PREFIXES`
  (shadow_dispatcher) + learning_analyzer readers + dashboard. Brak = niewidoczny bug.
- **Kryterium akceptacji R6 (gate do F1):** harness potwierdza dobór współczynnika
  kary R6 taki, że ZERO przypadków gdzie solver wybiera breach R6 dla oszczędności
  jazdy → R6 efektywnie-twardy w solverze (technicznie soft, behawioralnie hard).
- Harness = zero prod. Instrumentacja = mały deploy (restart dispatch-shadow, ACK).
- ACK → F1.

### F1 — R6 soft upper bound na delivery (+ thermal priority)  [~0.5 dnia]
- `tsp_solver.solve_tsp_with_constraints`: nowy param `delivery_deadlines`
  `[(deadline_min, penalty_coeff)|None]` → `time_dimension.SetCumulVarSoftUpperBound`.
- `_ortools_plan`: dla każdego delivery deadline = anchor+35 (anchor: picked_up →
  `picked_up_at`; pending/new → `ready_at`), od `now`.
- Naprawia 474297 (82-min carry → solver wypycha picked-up na początek). Zastępuje
  `lock_first` w OR-Tools. Współczynnik kary wg kryterium R6 z F0.
- Testy: unit tsp_solver + replay F0 + shadow 24h. ACK → F2.

### F2 — koszt SPAN (idle) + usunięcie P3D1  [~2-3h]
- `tsp_solver`: param `span_cost_coeff` → `SetSpanCostCoefficientForAllVehicles`.
- `_ortools_plan`: przekaż span_cost_coeff; usuń budowę `cost_matrix` z P3D1
  (cost_matrix = time_matrix czyste). Flaga `ENABLE_V328_P3D1_IDLE_COST` → retire.
- Naprawia 474253 (idle 15 min pod restauracją). ACK → F3.

### F3 — best_effort: hard-R6 breach > próg → KOORD  [~1-2h]
- `dispatch_pipeline` best_effort: gdy najlepszy kandydat ma r6_breach > próg
  (~20 min — wysoko, by nie ruszać normalnych buforów R-BUFFER-OK) → verdict=KOORD.
- BUG-5: napraw null `r6_*` metryk (policz R6 też dla odrzuconych kandydatów).

### F4 — pozycja kuriera (proxy `last_picked_up_delivery`)  [~0.5-1 dnia, większe]
- Proxy modeluje kuriera w NIE-odwiedzonym jeszcze dropie → odległości do solvera
  zafałszowane (474266: Sioux „8 min" zamiast realnych 2). Bez tego nawet idealny
  objective (F1-F3) optymalizuje na złych wejściach.
- Kierunek: lepszy proxy gdy brak GPS — interpolacja na bieżącej nodze trasy
  (chain-eta aware) zamiast „kurier już w dropie". Dotyka `courier_resolver`.
- ACK → osobny design przed implementacją (większy, ryzykowny).

## Kalibracja
Współczynniki (R6 penalty_coeff, span_cost_coeff) — z harnessu F0. R6: 1 min
przekroczenia ≫ kilka km jazdy (kryterium akceptacji F0). span_cost umiarkowany
(nie zabija sensownych bundli). Iteracja na replay przed każdym shadow-deployem.

## Ryzyka
- Soft bounds zwiększają złożoność solve — limit 200ms; N≤15 OK, zmierzyć w F0.
- Złe współczynniki → over/under-correction — mityguje replay F0 + shadow.
- Span cost na edge-case'ach (1 stop) — testy.
- F2 usuwa P3D1 (aktualnie LIVE) → zmienia wszystkie propozycje; shadow-observe 24h.

## Poza zakresem
greedy `lock_first` — zostaje (fallback, rzadki po E3); nie ruszamy.
