# KANDYDAT: Ziomek nie zalicza bundla dostawowo-skolokowanego (różne restauracje, ten sam adres dostawy)

**Data:** 2026-06-26 (peak — analiza read-only, deploy off-peak)
**Zgłaszający:** Adrian (case Dariusz Maruszak 509: Street Mama Thai + Raj)
**Protokół:** [[ziomek-change-protocol]] ETAP 0→7. **Status: 🟢 LIVE 26.06 — commit `35186f6` tag `bundle-deliv-coloc-2026-06-26`, flaga `ENABLE_BUNDLE_DELIVERY_COLOCATION` ON. Detal/rollback → [[bundle-delivery-colocation-2026-06-26]].**

> ⚠️ KOREKTA DIAGNOZY (po nakazie Adriana „najpierw znajdź problem"): pierwotnie celowałem w wave-veto. Pełny rekord `shadow_decisions` pokazał, że **wave-veto to objaw wtórny** — root cause to **pickup-centryczny kredyt bundla**.

---

## ROOT CAUSE (z rekordu decyzji 483610, best=509)
Silnik policzył geometrię DOBRZE: `deliv_spread=0.05` (37 m), `r6_max_bag_time=29.9` (zero naruszeń), predykcja dostawy **20:40** (NIE 21:13, R6 NIE 42). **Problem jest w SCORINGU bundla, nie w R6 ani trasie:**

| Kredyt bundla | Próg | Street Mama Thai + Raj | Bonus |
|---|---|---|---|
| **L1** `bonus_l1=25` | ta sama restauracja pickup | Street Mama Thai ≠ Raj | **0** |
| **L2** `bonus_l2≤20` | pickup **< 1,5 km** | pickupy 2,79 km od siebie | **0** |
| **L3** korytarz DOSTAWY <2 km | drop w korytarzu trasy | `False` (coords Street Mama Thai w bagu kulały — log `BAG_COORD_REPAIR Street Mama Thai` ×N) | **0 bonusu** (L3 zasila tylko „po drodze", nie ma `bonus_l3`) |
| `bonus_bug2_continuation` | gap fali OK | **wetowany** `V326_WAVE_VETO km_from_last_drop=4.16>3.0` | **0** |

**Wynik:** para dowożona pod **ten sam adres (37 m)** z restauracji **2,79 km od siebie** → `bundle_bonus=0.0`, `bundle_level1/2=None` → **score −65,7** → `would_auto_assign=False` (`auto_block: weak_pick_score=-65.7<0`). Ziomek **nie rozpoznał bundla → wycenił jak solo → nie auto-przypisał** → człowiek musiał wsadzić ręcznie. **To jest „kazał mu solo".**

**Jądro:** cały kredyt bundla (L1/L2) jest **PICKUP-centryczny**, a do tego wave-veto **karze** daleki pickup (`last_drop→new_pickup`). System jest **strukturalnie ślepy na kolokację DOSTAWY**. Dwa zlecenia pod ten sam adres z dwóch odległych restauracji = wzorcowy wspólny kurs (R6 29,9), a dostają zero kredytu i są wyceniane jak solo.

## Case (kotwice)
- **483598 Street Mama Thai** pickup **53.154/23.186** (północ; ≠ „Mama Thai Bistro"/„Mama Thai Street" z Kaczorowskiego 53.122/23.146) → 42 Pułku Piechoty 72a
- **483610 Raj** pickup 53.132/23.165 → 42 Pułku Piechoty 72E
- DROP↔DROP **0,037 km**; PICKUP↔PICKUP **2,79 km**; last_drop→Raj_pickup **4,16 km** (kotwica veta).

---

## ETAP 0 — STAN NA ŻYWO ✅
git `master@13749d2`, silnik czysty. Flaga veta `ENABLE_V326_WAVE_GEOMETRIC_VETO` env-frozen (`common.py:2100`, default ON), próg 3.0. Bundle: `bundle_level1/2` (`dispatch_pipeline.py:3403-3434`, pickup-centryczne), `bundle_level3` (`:3438-3448`, delivery-corridor, brak bonusu), `bonus_l1`(:3840)/`bonus_l2`(:3879). Services aktywne. Shadow: `dispatch-bundle-calib-shadow` (review 02.07) = sąsiedni (bag-resequence), NIE ten temat. Pełna regresja → off-peak.

## ETAP 1 — ŹRÓDŁO ✅
Warstwa = **scoring/SOFT, `dispatch_pipeline.py`**: klasyfikacja bundla (3403-3448) + bonusy (3840/3879) **ignorują kolokację dostawy**; `V326_WAVE_VETO` (4531) dobija continuation. NIE feasibility (R6/geometria OK), NIE plan/trasa (ETA OK 20:40), NIE display.

## ETAP 2 — HARD vs SOFT ✅
R6 = jedyny HARD (`common.py:1193`), tu NIE naruszony (29,9). Zmiana = dodanie/poprawa kredytu SOFT (bundle) — **nie osłabia HARD**. **Kolizja P-1..P-7: SPRAWDZONE — brak.** P-1 to warstwa kanonu/sekwencji (`_apply_canon_order_invariants` carried-first L1-L6, plan_recheck); moja zmiana to warstwa scoringu/selekcji. Różne warstwy, nie cofam świadomej inwersji. (Audyt: [[ziomek-full-rule-audit-2026-06-24]] / [[ziomek-resilience-spec-2026-06-24]].)

## ETAP 3 — MAPA KOMPLETNOŚCI (klasa: scoring/SOFT + nowa flaga + metryka)
| Miejsce | Dotknięte? | Powód |
|---|---|---|
| `bundle_level2` pickup `<1.5km` (`:3416-3434`) | **TAK** | dodać delivery-side kredyt: drop nowego <X km od drop w bagu → bonus (analog L2 na dostawie) |
| `bonus_l2` (`:3879`) | **TAK** | wyceniać delivery-colocation |
| `V326_WAVE_VETO` (`:4531`) | **TAK (gate)** | nie wetować continuation gdy drop nowego skolokowany z drop bagu (co-pickup, nie nawrót) |
| `FIX_C bundle_cap` (`:4585`) + `wave_veto_newdrop` (`:4568`) | **TAK (spójny gate)** | jeden gate „delivery-colocated bundle nie jest karany" |
| `bundle_level3` / `_min_dist_to_route_km` (`:3438`) | **do weryfikacji** | czemu False przy 37 m — coords bagu (BAG_COORD_REPAIR) lub courier_pos; może to jest prawdziwy punkt naprawy (L3→realny bonus) |
| `feasibility_v2._max_deliv_spread_km` | N-D | metryka OK (0.05) |
| `route_simulator_v2`/`plan_recheck` | N-D | ETA/R6 OK |
| Selekcja `_best_effort`/`objm_lexr6`/`_objm_lexr6_shadow` | **weryfikacja parytetu** | bonus wchodzi do score przed selekcją |
| Nowa flaga `ENABLE_BUNDLE_DELIVERY_COLOCATION` | **TAK** | `ETAP4_DECISION_FLAGS`+const OFF+`decision_flag()`(NIE env)+`_SHADOW`+3 checkery+test ON≠OFF |
| Serializer A+B / `_AUTO_PROP_PREFIXES` | **TAK** | nowe pola (`bundle_deliv_coloc_km`, `bonus_deliv_coloc`) → jsonl |

## ZASADA NADRZĘDNA (Adrian 2026-06-26) — bundle wymuszony przez 2 TWARDE reguły, NIE miękka geometria
Bundle ma wynikać z HARD, nie z bonusu za bliskość pickupów. Dwie najważniejsze reguły:
1. **R6** — dostawa ≤ 35 min od odbioru (`BAG_TIME_HARD_MAX_MIN=35`).
2. **Committed window** — odbiór w ±5 min od `czas_kuriera` (`V3274_FROZEN_PICKUP_WINDOW_MIN=5`, R-DECLARED-TIME).

Ziomek MUSI „wiedzieć", że ma być w Raju o committed czasie (±5). Skoro Darek jest już committed na Street Mama Thai (ten sam adres 42pp), honorowanie OBU committed-okien + R6 **wymusza bundle — nie ma innej możliwości**. To nie nagroda za bundle, to JEDYNE feasible rozwiązanie pod twardymi regułami.

**Dowód z case'u (wszystkie HARD spełnione przez bundle na 509):**
- Street Mama Thai `ck 20:13`, Raj `ck 20:22` (9 min, pickupy 2,79 km — wykonalne sekwencyjnie).
- 509: `late_pickup_committed_breach=False` (max 1,0 min ≤ 5) → **honoruje oba committed okna**.
- `r6_max_bag_time=29,9` (≤35, zero naruszeń) → **R6 czyste dla obu**.
- → bundle = jedyne rozwiązanie spełniające 2× committed + 2× R6; a Ziomek wycenił `-65,7` (słabo) i **nie auto-przypisał**.

**Fix = rozpoznanie WYMUSZONEGO bundla z twardych reguł:** gdy kurier honoruje committed-okno nowego (±5) **ORAZ** dostawa nowego skolokowana z dostawą w bagu (R6 obu ≤35) → **mandatory bundle** → silny/auto-assign, NIE słabe solo. Delivery-colocation jest MIARĄ R6-feasibility, nie samodzielnym bonusem. Gate: wave-veto/FIX_C/newdrop **nie karzą** pary wymuszonej committed+R6. Mocniejsze od miękkiego bonusu — oparte na HARD, więc measure-first nie obali jako „kosmetyki".

## ETAP 4-7 (plan)
- **4:** flaga decision_flag OFF; detekcja WYMUSZENIA: kurier `committed_breach≤5` dla nowego **I** `min haversine(new_drop, bag_drop) < X` (R6 obu ≤35); test ON≠OFF na case 509; metryka w jsonl; parytet selekcji; **pełna regresja vs baseline + e2e** (case 509 + nie-skolokowany order — bundle dla niego NIE może urosnąć).
- **5:** dowód, że ON daje **realnie wyższy score / auto-assign dla delivery-colocated bundli** bez psucia R-FLEET-LEVEL (replay ON↔OFF, materialność, +2 dni). [tu walidacja — PO znalezieniu problemu, nie zamiast].
- **6:** off-peak, `.bak`→compile→testy→1 restart `dispatch-shadow` (NIGDY telegram/peak bez OK).
- **7:** flaga=false / `.bak` / `git revert`.

## Pytania do Adriana (kształt fixu)
1. **Próg kolokacji dostawy X** — start 1,5 km (jak L2 pickup), czy ciaśniej (np. 0,5 km — „ten sam blok")?
2. **Punkt naprawy:** (A) nowy delivery-side bonus L2-analog, czy (B) naprawić istniejący **L3** żeby dawał realny `bonus_l3` (już jest delivery-corridor, tylko bez nagrody + coords-bug)? Rekomendacja: **A** (czysty, niezależny od courier_pos/coords-repair), L3 zostawić dla „po drodze".
3. Wave-veto/FIX_C gate przy delivery-colocation — **TAK** (twoje „rekomendacje": jeden spójny gate).
