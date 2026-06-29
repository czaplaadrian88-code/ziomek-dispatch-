# #3 rozrzucanie — DOKŁADNY replay (29.06, top10 #3, ACK Adriana "buduj dokładny pomiar")

**Po co:** doc 28.06 dał ~26 kursów/dzień ale OSRM-IZOLOWANY (zakłada kuriera tylko z tą parą,
ignoruje inne worki + sekwencyjny napływ + KOSZT bundla). Adrian 29.06: "buduj dokładny pomiar"
PRZED sprintem rdzenia. Metoda: realne SEKWENCYJNE decyzje silnika z `shadow_decisions.jsonl`
(best=wybrany, alternatives=pula) — każda w swoim momencie z realnym stanem floty. NIE OSRM-model.
Skrypt: `eod_drafts/2026-06-29/measure_spread_replay.py` (read-only).

## Kalibracja przyrządu (C9 — ZANIM zaufano liczbie; pierwszy run dał FAŁSZYWE 0)
- BUG#1: filtr `feasibility=='YES'` → 0 trafień. PRAWDA: silnik WYBIERA `MAYBE` w 632/690 PROPOSE;
  carrying-bag kandydat = wielo-worek → soft-zone → 'MAYBE' = NORMALNY feasible, nie infeasible. 'YES' ~brak.
- BUG#2: sentinel ±1e9 (best_effort przeciekł) zawyżał Δscore. Fix: wyklucz |score|≥1e6.
- R6-OK bundle: carrying-MAYBE max_bag_time med 15,8min, 1768/1769 ≤35 → realnie świeżościowo wykonalne.

## WYNIK (3 dni 27-29.06, 690 PROPOSE = 230/d)
- wybrał WOLNEGO (bag=0): 304 (44%); z nich FEASIBLE carry-alt istniał: **229 (76/d)**, dedup 162 (54/d), **76% PEAK**.
- **Δscore (wolny − carry) med = +138** → w 96% silnik MOCNO wolał wolnego (bundle wg jego objektywu gorszy: objazd/świeżość).
- Gap-sensitivity (silnik ~obojętny = łatwy zysk):
  - **|Δ|≤30 = ~3/dzień** ← CZYSTE wygrane (tie-break, bez zmiany objektywu)
  - |Δ|≤60 = ~9/dzień · |Δ|≤100 = ~20/dzień (warte TYLKO jeśli rezerwa wyceniona ~60-100 pkt)
- Doc-owe ~26 ≈ próg ≤100 → zakłada wartość rezerwy ~100 pkt IGNORUJĄC koszt bundla.

## WERDYKT (uczciwy, refinuje doc 28.06)
1. **Czysta, bezsporna pula = ~3/dzień** (silnik ~obojętny). Lewar = MAŁY tie-break: gdy |Δ|≤próg
   i carry R6-OK → preferuj dołożenie do jadącego (oszczędza rezerwę). Niskie ryzyko, ~3/d głównie peak.
2. **Większy prize (do ~20/d) wymaga WYCENY REZERWY w objektywie** — silnik dziś NIE liczy kosztu
   spalenia wolnego (myopia per-zlecenie). To realna, ale WIĘKSZA zmiana o NIEPEWNYM zwrocie
   (bundle realnie kosztuje objazd/świeżość — trzeba walidować fizycznie przez #1 gps_delivery_truth).
3. ~26 z doc = górny limit, NIE "free wins". Sprint P0 rdzenia oczekujący 26 darmowych = błąd.

## REKOMENDACJA
- **Tani krok (warto, niskie ryzyko):** reserve-aware tie-break gdy silnik ~obojętny (|Δ|≤30-60) →
  dołóż do jadącego zamiast palić wolnego. ~3-9/d, głównie peak. Wąska reguła, OFF→shadow→ACK.
- **Duży krok (wstrzymać):** pełna wycena rezerwy w objektywie — najpierw fizyczna walidacja przez #1
  (czy bundle przy gap 30-100 realnie NIE psuje świeżości), potem decyzja. Niepewny ROI.
- Komplement runtime: live `reassign-bundling-only` (28.06) łapie znikomo (~20 "luźniejszy worek"/d, część sentinel).
