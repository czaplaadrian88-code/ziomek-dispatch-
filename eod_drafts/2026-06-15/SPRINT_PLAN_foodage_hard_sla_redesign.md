# SPRINT PLAN — food-age REDESIGN: twardy invariant SLA (single-solve)

**Status:** ZAPLANOWANY (czeka na sesję wykonawczą — ACK per faza)
**Owner:** Adrian / sesja Ziomek
**Utworzony:** 2026-06-15 (po drill-downie regresji 41/5923)
**Okno docelowe:** **18.06** (po E7 17.06, przed BUG-A 19-20.06) — **TWARDY deadline: replay-walidacja przed 21.06 09:00 (at#151 = re-point #142 z 15.06: `--max 8000` nice, pełny werdykt o ogonie zamiast subsampla 1500)**
**Cel biznesowy:** naprawić BUG#5 (świeżość dostaw) BEZ łamania twardej reguły 35 min — która jest jedną z dwóch nienaruszalnych.

---

## 1. Dlaczego (kontekst z drill-downu 15.06)

Obecny design food-age = **ADDYTYWNY R6-soft (coeff100) + food-age-soft (coeff3)** na wymiarze Time. Flaga `ENABLE_OBJ_DELIVERY_FOOD_AGE` OFF w prod.

Drill-down `foodage_regression_drilldown.py` (n=5923 ortools-decyzji, okno 7d): **41 regresji SLA (0,69%)** — i to NIE łagodny ogon:
- **23/41 POWAŻNE** (>5 min ponad SLA, max +41,5), tylko 14 marginalnych.
- **88% GENESIS** (45/51 breachujących zleceń było <33 min pod OFF → ON tworzy breach ze SPOKOJNEGO planu; near-miss tylko 6). To NIE „doomed-bagi".
- **63% gorsze na OBU osiach** (mediana thermal-trade −1,8 → jedzenie i później, i starsze).
- Koncentracja bag≥3 (32/41). Przykłady: NEW 17→76 min, bag 9→39, 8→57, 7-bag 0→3 breachy.

**Root cause (hipoteza do potwierdzenia w Fazie 0):** R6 jest SOFT (kara, nie twarde ograniczenie). Łagodny food-age, **sumowany po wielu niesionych zleceniach** na dużym bagu, przebija karę R6 → solver akceptuje breach SLA, bo „opłaca się" termicznie. Soft nigdy nie GWARANTUJE SLA — tylko go „wycenia".

**Bazowy gate `foodage_offline_replay_review.py` dawał mylące „✅ KANDYDAT"** (liczył net+średnią, ślepy na rozkład). Dokręcony 15.06 (bramka genesis/serious) — teraz blokuje. Ale to tylko detektor; właściwy fix = ten sprint.

---

## 2. Cel techniczny (Definition of Done)

1. **Zero regresji SLA vs OFF z KONSTRUKCJI** (nie z nadziei na strojenie coeff).
2. **Single-solve** w typowym przypadku — ZERO powtórki trapu latencji inline-comparatora (p95 2,1→4,2 s, który wymusił rollback 14.06).
3. **Zachowany benefit food-age** na przypadkach SLA-neutralnych (Jakub OL nadal → plan B; resztkowy changed-rate + thermal dodatni na replayu).
4. Flaga OFF default → zero wpływu na prod do ACK.

---

## 3. Design — Opcja 1: TWARDE ograniczenie SLA w solve food-age (REKOMENDOWANE)

Gdy food-age ON, zamień ochronę SLA z **soft kary** na **twarde ograniczenie span** w `tsp_solver`:

- Zlecenie **odbierane w tym planie** (węzeł pickup w trasie): `CumulVar(Time, delivery) − CumulVar(Time, pickup) ≤ SLA(35)` — twardy span pickup→dostawa.
- Zlecenie **już odebrane** (`picked_up_at` w przeszłości, brak węzła pickup): `CumulVar(Time, delivery) ≤ offset(picked_up_at) + 35` — proste `SetMax` na CumulVar dostawy (stała).

Kotwica = **fizyczny odbiór** (pickup_at / picked_up_at) — DOKŁADNIE ta sama co `_count_sla_violations` (metryka, której nie wolno pogorszyć). ⚠ To inna kotwica niż R6-soft (`ready+35`) — patrz §6 ryzyka.

Food-age-soft (coeff3) zostaje JAKO JEDYNY miękki człon dostawy — ale teraz działa TYLKO w obszarze SLA-feasible (twarde ograniczenie wycina breachujące permutacje, solver nie może ich wybrać).

**Doomed-bag (model infeasible z twardymi ograniczeniami):** ≥1 zlecenie nie zmieści się w 35 min w ŻADNEJ kolejności → re-solve BEZ food-age (czysty R6 = plan OFF) dla tej decyzji. Drugi solve **tylko na infeasible** (rzadkie, bag już spóźniony) — średni przypadek zostaje single-solve. Gwarancja: ON ≤ OFF zawsze (feasible → ON breaches=0 ≤ OFF; infeasible → ON=OFF).

**Świadomy kompromis v1 (konserwatywny, bezpieczny):** twarde-na-WSZYSTKICH → mieszany bag (1 zlecenie doomed + reszta OK) cofa się do pełnego OFF, tracąc okazję thermal na zdrowych zleceniach. Refinement (twarde tylko na nie-doomed) = future work, nie v1.

### Odrzucone alternatywy
- **Opcja 2: post-hoc veto (policz OFF i ON, wybierz ON gdy sla_on≤sla_off)** — to 2 solve/decyzję = **dokładnie trap latencji wycofany 14.06**. ❌
- **Opcja 3: tylko niższy coeff dla bag≥3** — redukuje, nie GWARANTUJE; ogon zostaje. Dopuszczalne jako wtórna mitygacja, nie fix główny. ❌ jako samodzielne.

---

## 4. Fazy (ACK gate między każdą)

| Faza | Co | Wynik / gate |
|---|---|---|
| **0 — root-cause (read-only ~30min)** | Instrumentuj 1 case genesis z drill-downu (np. 7-bag 0→3, `[2026-06-14T17:32:32]`): wypisz trade objective OFF vs ON, potwierdź że suma food-age przebija R6. Zatwierdź kotwicę (pickup_at span). | Potwierdzony mechanizm + decyzja kotwicy. **ACK.** |
| **1 — design lock** | Spisz dokładnie: gdzie w `tsp_solver` dodać `solver.Add(span≤35)` / `SetMax`, jak wykryć infeasible, jak zrobić fallback-resolve. Sub-flaga `ENABLE_OBJ_FOOD_AGE_HARD_SLA` (default OFF) — komponuje się z `ENABLE_OBJ_DELIVERY_FOOD_AGE`. | Design ACK. |
| **2 — implementacja (flaga OFF)** | `tsp_solver.py` (twarde span/SetMax gdy flaga ON + fallback na infeasible) + `route_simulator_v2.py` (przekazanie) + `common.py`+`flags.json` (sub-flaga). Stary kod additive nietknięty (kompozycja, nie zamiana). | `py_compile` + import OK. |
| **3 — testy** | Jakub→B zachowany; **fixture genesis z drill-downu teraz SLA-safe**; doomed-bag → fallback==OFF; regresja OBJ/R6/tsp/span/freshness zielona. ⚠ Testy flipów PINUJĄ kontekst prod (`ENABLE_OBJ_R6_SOFT_DEADLINE/SPAN=True`, majority-of-N — lekcja FA-T2/T5). | Testy zielone. **ACK.** |
| **4 — replay-walidacja (GO/NO-GO)** | `foodage_regression_drilldown.py --max 6000` + bazowy `foodage_offline_replay_review.py`. **Oczekiwane: regresje SLA ≈ 0 (tylko doomed=OFF), serious=0, genesis=0; resztkowy benefit (changed-rate + thermal+) zachowany; latencja single-solve (brak regresu p95 na shadow).** | Liczby spełniają DoD → **ACK do flipa**. |
| **5 — flip + obs** | Za ACK: flip `ENABLE_OBJ_FOOD_AGE_HARD_SLA`+`ENABLE_OBJ_DELIVERY_FOOD_AGE` (hot). Shadow-first jeśli Adrian woli. 48h obs SLA/thermal/latencja. **at#151 (21.06) = niezależna re-walidacja na świeżych danych.** | Commit+tag, wpis sprint_timeline. |

---

## 5. Rollback
Flaga `ENABLE_OBJ_FOOD_AGE_HARD_SLA=false` (hot-reload, ~5s) → wraca obecny additive-soft (a `ENABLE_OBJ_DELIVERY_FOOD_AGE` i tak OFF). Twardy: `git revert` commitu sprintu + restart `dispatch-shadow`. Backup `.bak` per plik.

## 6. Ryzyka
- **Rozjazd kotwic (pickup_at vs ready+35):** twarde ograniczenie na pickup→del (fizyczne), R6-soft na ready+35. Mogą dać różne deadline'y gdy kurier czeka pod restauracją. Faza 0 musi potwierdzić, że twarde na pickup_at jest tym, co chroni metrykę `sla_violations` (TAK — ta sama kotwica) i że nie psuje legalnych czasówek.
- **CumulVar−CumulVar inequality w OR-Tools** — wspierane (`solver.Add`), ale dla już-odebranych pickup jest w przeszłości → degeneruje do `SetMax` (stała). Obsłużyć OBA przypadki.
- **Infeasible-fallback poprawność** — musi cofać do DOKŁADNIE planu OFF (nie „prawie"), inaczej cicha regresja. Test doomed-bag to pilnuje.
- **Niedeterminizm OR-Tools 200ms pod obciążeniem** — replay/testy na majority-of-N, nie 1 solve (lekcja).

## 7. Pliki (oczekiwane)
`tsp_solver.py` (twarde ograniczenia + fallback) · `route_simulator_v2.py` (przekazanie param) · `common.py` (sub-flaga + stała) · `flags.json` · `tests/test_obj_food_age_bug5.py` (+ fixture genesis) · walidacja: istniejące `eod_drafts/2026-06-15/foodage_regression_drilldown.py` + `eod_drafts/2026-06-14/foodage_offline_replay_review.py`.

## 8. Zależności / timing
- **Po** E7 17.06 (at#131, „główna konwergencja") — nie kolidować.
- **Unikać** 19-20.06 (BUG-A).
- **Przed** 21.06 09:00 (at#151 re-waliduje food-age na świeżych danych — ma walidować NAPRAWIONY design).
- **Slip-plan:** jeśli nie gotowe do 20.06 EOD → przesunąć at#151 (re-point joba), NIE walidować/flipować zepsutego designu pod presją daty.
