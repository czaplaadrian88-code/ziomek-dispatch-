# FAZA 0 — root-cause food-age redesign — WERDYKT (read-only, 2026-06-17)

**Sprint:** `SPRINT_PLAN_foodage_hard_sla_redesign.md` Faza 0 (ACK gate).
**Status:** WYKONANE. Nic nie flipnięte, nic nie zapisane do prod-state, zero Telegrama.
**Narzędzia (read-only, durable):**
- `foodage_phase0_rootcause.py` — dekompozycja objektywu OFF↔ON + per-order kotwica metryki vs kary
- `foodage_phase0_artifact_vs_structural.py` — klasyfikacja regresji: budżet 200ms vs strukturalne
- wyniki: `foodage_phase0_artifact_result.txt`

---

## 1. Stan żywy (zweryfikowany)
- `ENABLE_OBJ_DELIVERY_FOOD_AGE` = **OFF** (prod). R6 coeff=100, food-age coeff=3, span coeff=1.0 (ON).
- R6-soft (`delivery_soft_deadlines`) i food-age (`delivery_food_age_penalties`) to OBA **soft** `SetCumulVarSoftUpperBound` → breach = koszt, nie zakaz.
- Limit solvera `V326_OR_TOOLS_TIME_LIMIT_MS = 200`.

## 2. Mechanizm — INNY niż zakładał spec
Spec: „R6 soft → suma food-age przebija R6 → solver akceptuje breach". **Pomiar to częściowo obala:**
- Case `17:32:11` bag=7: łączny koszt miękki ON (6519) ≫ OFF (514) — solver wybrał sekwencję gorszą na R6 **i** food-age. Gdyby to był czysty trade objektywu, solver by jej nie wziął.
- **Decydujący eksperyment (limit czasu):** ten sam case ON @200ms → **3 breache**; @1000ms+ → **0 breachy, identyczny czas (76.3) co OFF**. Food-age NIE wymusza poświęcenia SLA — to była **niezbieżność OR-Tools w 200ms** (food-age komplikuje objektyw → skrócone szukanie ląduje na złym lokalnym optimum na dużym worku).

## 3. Klasyfikacja regresji — PEŁNY KORPUS (n=77, okno 10-17.06, bag≥3, hi=2000ms ×3 prób)
```
eligible=7836 re-sim=7836 (BEZ próbkowania) | ortools-decyzji n=7741
regresje@200ms R=77 (0.99%)
ARTEFAKT BUDŻETU (znika @2000ms): 48/77 = 62.3%   bag {3:16,4:13,5:5,6:3,7:11}
STRUKTURALNE  (zostaje @2000ms): 29/77 = 37.7%   bag {3:12,4:12,5:4,7:1}
```
⇒ **DOMINUJE BUDŻET SOLVERA (62%)**, nie objektyw. Strukturalne 38% są realne ale **drobne** — niemal wszystkie `0→1` (lub `1→2`) breach, bag 3-4, dur@hi normalne (46-93 min, 1 outlier 131 = doomed). Wstępne n=9 (44/56) było mylące — pełna próba przesuwa wagę zdecydowanie na budżet.
**Wniosek:** premisa specu (R6-soft przegrywa z food-age) to MNIEJSZOŚĆ. Większość regresji znika gdy solver dostanie czas się zbiec / dobry start.

## 4. Kotwica — POTWIERDZONA (cel §4)
- METRYKA `_count_sla_violations`: anchor = `pickup_at`(TSP) | `picked_up_at`, breach gdy `delivery−pickup>35`.
- R6-soft + food-age: anchor = `picked_up_at` | `pickup_ready_at` (ready), deadline=anchor+35 / bound=anchor.
- Dowód rozjazdu (case 17:31:53): zlecenie 480851 ma metric-span 3.5 min, ale ready-span 41 min — to SAMO zlecenie. Twarde ograniczenie **musi** iść na **span pickup→delivery (kotwica metryki)**, nie na ready.

## 5. Fidelity time-dim ↔ realizacja — DE-RYZYKOWANE
Obawa: czy `solver.Add(span≤35)` w wymiarze Time solvera gwarantuje realny span≤35 (`_simulate_sequence` dokłada wait-until-ready)? Test limitu czasu pokazał: gdy solver ZBIEGA (2000ms), realny sla artefaktów → 0. Czyli Time-dim jest wierny realizacji **przy zbieżności**; anomalia „ON koszt≫OFF" była suboptymalnym planem 200ms, nie rozjazdem modelu.

---

## 6. WERDYKT designu (do ACK Adriana)
**Opcja 1 (twardy span SLA) POZOSTAJE właściwa** — adresuje OBA źródła:
- strukturalne → twardy `span≤35` zakazuje breacha który solver by wybrał;
- artefakt budżetu → over-constrained/200ms → infeasible → **fallback do planu OFF** (SLA-safe).

**Refinements POTWIERDZONE pomiarem (do design-locka Fazy 1):**
1. Kotwica = **TSP pickup→delivery span** (`solver.Add(CumulVar(del)−CumulVar(pick) ≤ 35·SCALE)`) dla odbieranych w planie + **`SetMax`** na CumulVar dostawy = `(picked_up_at−now)+35` dla już-odebranych. NIE kopiować kotwicy ready z R6-soft.
2. **Infeasible → fallback re-solve BEZ food-age i BEZ twardego span = plan OFF** (gwarancja ON≤OFF). Konieczne (doomed-bag + over-constrained 200ms).
3. **Post-solve guard realnego span** (tani, mamy `sla_violations`): po solve ON policz realny sla; jeśli >0 mimo twardego ograniczenia (rzadki rozjazd time↔realizacja) → fallback OFF. Trzyma single-solve w typowym przypadku, podwaja tylko na rzadkim rozjeździe.
4. Sub-flaga `ENABLE_OBJ_FOOD_AGE_HARD_SLA` (default OFF), komponuje się z `ENABLE_OBJ_DELIVERY_FOOD_AGE`.

**Alternatywa do rozważenia (tańsza, gdyby Adrian chciał):** skoro ~44% to budżet 200ms — **warm-start solve'u food-age sekwencją OFF** (hint OR-Tools `ReadAssignmentFromRoutes`): food-age startuje od znanego SLA-safe planu i może tylko go poprawić w swoim objektywie → zabija artefakt budżetu BEZ twardych ograniczeń. Strukturalne 56% nadal wymaga twardego span LUB świadomej akceptacji. Można złożyć: warm-start + twardy span (warm-start przyspiesza zbieżność pod dodatkowymi ograniczeniami).

**Czego NIE robić:** Opcja 2 (post-hoc veto = policz OFF i ON zawsze) = trap latencji 2-solve (wycofany 14.06). ❌
