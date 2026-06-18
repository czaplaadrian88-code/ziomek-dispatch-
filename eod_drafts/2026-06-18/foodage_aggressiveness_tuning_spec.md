# Spec — strojenie agresywności food-age (PO flipie bazowym 21.06)

**Status:** DRAFT, NIE wdrażać. Wymaga: (a) stabilny flip bazowy `ENABLE_OBJ_FOOD_AGE_HARD_SLA` po job 154 (21.06), obserwacja ≥3-7 dni bez incydentów; (b) ACK Adriana. Powiązane: [[ziomek-objective-foodage-backtest-2026-06-18]].

## Dlaczego (uzasadnienie pomiarowe)
Real-bag backtest 18.06 (130 worków events.db) pokazał **sufit możliwości**: 58% worków ma świeższą kolejność dostępną (mediana max-food-age 15,4→9,3, p90 27,8→12,6, R6 5%→0%). Wdrożona **hybryda warm-start** bierze tylko bezpieczną część (phase4: ~14% zmienia trasę, 86% fallback) — z konstrukcji konserwatywna (zero-regresji). phase0: **62% regresji food-age @200ms = artefakt budżetu solvera** (znika @2000ms). ⇒ jest realny zapas do podkręcenia BEZ ruszania kodu.

## Dwie dźwignie (obie `flags.json` numeric-override, hot-reload, ZERO restart/kod)
1. **`OBJ_FOOD_AGE_HARD_SLA_ON_SOLVE_MS`** (default **100**). Budżet czasu warm-startowanego ON-solve. Więcej budżetu → mniej fallbacków z artefaktu budżetu (te 62%) → więcej worków faktycznie się przekłada na świeższe + mniej false-INFEASIBLE. Twardy span/bounds gwarantuje SLA niezależnie od jakości → **cięcie/dodanie czasu NIE regresuje SLA** (tylko jakość/latencja). Sweep: **{100→150→200→300}**.
2. **`OBJ_DELIVERY_FOOD_AGE_COEFF`** (default **3.0**). Waga kary food-age w celu OR-Tools. Wyżej → solver mocniej preferuje świeżość (bliżej czystego min-food-age = sufit). Ryzyko: handel z makespanem/idle. Sweep: **{3.0→4.0→5.0→6.0}**.

## Protokół walidacji (reuse istniejących harnessów)
Dla każdej kombinacji `(COEFF, SOLVE_MS)` z gridu (start: po jednej osi, potem 2D wokół najlepszej):
- **Engine replay** (`eod_drafts/2026-06-17/foodage_phase4_validation.py`, szerokie okno ≥7 dni, min-bag 3): G1 nowe regresje SLA (fa>base), changed-rate, mediana zysku thermal na zmienionych, **latencja p50/p95**.
- **Real-bag** (`eod_drafts/2026-06-18/realbag_objective_compare.py`): med/p90 max-food-age, worki R6>35, koszt makespanu.

### Bramki GO (per kombinacja)
- **G1 zero-regresji:** nowe regresje SLA ≤ baseline (≈0; twardy span to gwarantuje — weryfikacja).
- **G2 zysk:** med/p90 food-age niższe niż default (100ms/3.0) o istotną deltę; changed-rate rośnie ku sufitowi.
- **G3 latencja:** ⚠ szeroka walidacja 18.06: bazowy flip już daje **p95 494 ms** (base 232 → hardsla 494, +87 ms median) — tail blisko budżetu <500 ms. ⇒ **PREFERUJ knob COEFF (waga, ~zero kosztu latencji) nad SOLVE_MS (czas, podnosi tail)**. Cap p95 ≤ ~550 ms; SOLVE_MS rusz dopiero gdy p95 ma zapas. Peak-aware: mierz pod obciążeniem peak.
- **G4 koszt makespanu:** mediana Δmakespan ≤ ~+2 min (real-bag default FOODAGE = +1,2 min).
- Wybór = **Pareto-best**: max przechwycenia świeżości przy G1=0 i G3/G4 w granicach.

## Sekwencja wdrożenia (Z2/Z3 + reguła „PRZED każdym tematem")
1. Po stabilnym flipie bazowym (21.06) → uruchom `ENABLE_OBJ_DELIVERY_FOOD_AGE_SHADOW` (komparator OFF↔tuned w `metrics["food_age_shadow"]`, BEZ zmiany decyzji) na strojonej kombinacji.
2. 3-7 dni shadow → werdykt z liczbami (G1-G4) do panelu.
3. PASS + ACK → flip numeric-override w `flags.json` (hot-reload).
4. **Rollback:** przywróć wartość w `flags.json` (hot, bez restartu).

## Uwaga
NIE łączyć z niezweryfikowanym C (okno-czasówki dostawy) ani D (anti-bundle) — oba odłożone (marginalne empirycznie 18.06). To strojenie dotyczy WYŁĄCZNIE osi food-age/freshness, która ma udowodniony zwrot.
