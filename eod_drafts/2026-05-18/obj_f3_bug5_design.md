# Sprint OBJ F3 / BUG-5 — design dla świeżej sesji (2026-05-18)

BUG-4 (best_effort R6-breach → KOORD) DONE 18.05 commit `0e7e2e1` tag
`obj-f3-best-effort-r6-koord-2026-05-18`. Zostaje BUG-5 — odłożone na świeżą
sesję (decyzja Adriana: reorder rdzenia `feasibility_v2` nie na końcu długiej
sesji; Z2 + cognitive-fatigue z CLAUDE.md).

## Problem (BUG-5, diagnoza 474297)
`feasibility_v2.check_feasibility_v2`: kandydat odrzucony na plan-level
`sla_violations` robi `return ("NO", "sla_violation...", metrics, plan)`
(ok. linia 595) **PRZED** blokiem pomiaru R6 (ok. 602-679). Skutek:
`r6_max_bag_time_min`, `r6_worst_oid`, `r6_is_solo`, `r6_bag_size`,
`r6_per_order_violations`, `r6_picked_up_violations` — **nigdy nie ustawione**
dla kandydatów sla-rejected.

Konsekwencja w `dispatch_pipeline` best_effort: `_r6_pov_count(c)` czyta
`metrics.get("r6_per_order_violations")` → None → zwraca 0 → kandydat
sla-rejected wygląda na „0 naruszeń R6", sortuje się jako dobry, a
`reason="...r6_violations=0"` KŁAMIE (sugeruje R6 OK przy realnym 82-min carry).

UWAGA: `objm_r6_breach_max_min` (route_metrics) JEST ustawiane — blok `objm_`
(ok. 557-560) leci przed sla-return. BUG-5 dotyczy WYŁĄCZNIE natywnych
`r6_*` metryk. Bramka F3/BUG-4 używa `objm_` → jest już poprawna; BUG-5
naprawia sort `_r6_pov_count` + uczciwość reason stringów.

## Fix
Reorder w `feasibility_v2.py`: przenieść BLOK POMIARU R6 (pętla per-order
licząca `r6_*` metryki — ok. linie 614-679, sama instrumentacja `metrics[...]`,
**BEZ** rejectujących `return`ów) PRZED early-return `if plan.sla_violations
> 0` (ok. 571). Wtedy:
- sla-rejected kandydat zwraca `metrics` z kompletnymi `r6_*`;
- rejectujące `return`y R6 (`r6_per_order_violations` hard, `r6_picked_up_*`)
  zostają PO sla-returnie — sięgane tylko gdy sla nie odrzuciło (sla i R6
  oba → NO, kolejność bez wpływu na werdykt).

Blok pomiaru R6 czyta tylko `plan.predicted_delivered_at`, `bag`, `new_order`,
`now`, `C.BAG_TIME_HARD_MAX_MIN` — zero zależności od `sla_violations`.
Reorder bezpieczny semantycznie.

## Ryzyko / workflow
- ~60-linijowy reorder w rdzeniu feasibility — `.bak`, py_compile, import.
- Test kauzalny: kandydat z `sla_violations>0` → `metrics` MUSI mieć
  `r6_per_order_violations` (lista, nie None) + `r6_max_bag_time_min`.
- Regresja: `test_feasibility_c2/c3/integration`, `test_r6_anchor_v328`,
  `test_v327_*`, `test_v328_*`, `test_obj_f1/f2/f3`.
- Restart `dispatch-shadow` off-peak. Effort ~1h.
- Flagą można nie obejmować — to pure-observability reorder; ale dla
  bezpieczeństwa rozważyć kill-switch albo bardzo dokładną regresję.

## Po BUG-5: Sprint OBJ → F4
F4 = pozycja kuriera (proxy `last_picked_up_delivery` przekłamuje odległości
do solvera). Większy, ryzykowny — osobny design (plan F4).
