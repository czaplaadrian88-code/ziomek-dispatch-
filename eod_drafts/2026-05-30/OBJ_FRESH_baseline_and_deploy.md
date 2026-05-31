# Sprint OBJ FRESH — kara świeżości odbioru (LIVE 2026-05-30)

## Problem (diagnoza)
Objective TSP był ślepy na punktualność **odbioru**. Pickup miał tylko dolne
ograniczenie (`SetRange` podbija do `ready_at`), zero kary za odbiór PO gotowości
jedzenia. Każda **dostawa** i tak lądowała przed soft-deadlinem, więc solver
spokojnie parkował odbiór zajętego kuriera grubo po gotowości.
Case spustowy: Bartek / Sweet&Fit — plan projektował odbiór +7 min po gotowości.

## Pomiar replay (czyste a vs b, food-only, firmy Panel Bridge wykluczone)
Źródło: `shadow_decisions.jsonl` `best.plan.pickup_at[oid]` (projekcja planisty,
zero kontaminacji rzeczywistością) vs top-level `pickup_ready_at`.

**(a) luz planisty = projected_pickup − ready_at** (n=1632):
- mediana = **1.0 min** (clamp +1 = dwell, NIE planowana zwłoka)
- p75=7.0, p90=14.8, p95=19.5, max=49.7
- ogon: **>5 min = 30.6%**, **>10 min = 17.5%**, >15 min = 9.9%

**(b) szum rzeczywisty = actual − projected** (n=1473): mediana +4.8, mean +6.4,
p90 +22. Kara świeżości NIE rusza (b) — to traffic/dwell. Ale dla ogona ~18%
luz planisty (10–50 min) ≥ typowy szum → planista = dominująca dźwignia.

Wniosek: mediana czysta (clamp-to-ready), ale realny **ogon ~18%** odbiorów
projektowanych ≥10 min po gotowości → cel kary progowej.

## Rozwiązanie (commit 55cb32b, tag obj-pickup-freshness-2026-05-30)
Symetryczny do `delivery_soft_deadlines`: ten sam prymityw OR-Tools
`SetCumulVarSoftUpperBound` na węzłach **pickup**, bound = `ready_at + THRESHOLD`.
- `tsp_solver`: + param `pickup_freshness_penalties` + walidacja + apply block
- `route_simulator_v2._ortools_plan`: build flag-gated, pass do obu solve calls
- `common`: `ENABLE_OBJ_PICKUP_FRESHNESS` (default OFF), `THRESHOLD_MIN=8`, `COEFF=20`
- test `test_obj_fresh_pickup`: tie-break reorder (PROOF: 11.5→11.5 jazdy, max
  odbiór 7.0→6.0) + noop + walidacja długości + never-infeasible. 4/4 pass.

Kalibracja coeff: kara = `coeff×100` / min overshoot; 1 min jazdy = 1000 w
arc-cost → coeff=20 ≈ **2 min jazdy / min nieświeżości** ponad próg. Gentle (nie
dominuje R6=100). Próg 8 min odejmuje medianę → celuje tylko w ogon.

## Deploy (LIVE, decyzja Adriana: "wdrożyć od razu + pomiar w cieniu + flaga off za tydzień")
- `dispatch-shadow.service` (= LIVE producent `courier_plans.json`) drop-in
  `override.conf`: `Environment=ENABLE_OBJ_PICKUP_FRESHNESS=1`
- backup: `override.conf.bak-pre-obj-fresh-2026-05-30`
- `daemon-reload` + `restart dispatch-shadow` @ **2026-05-30 21:13 Warsaw** —
  graceful stop (processed=253), clean start, ortools warm-up OK, 0 błędów.
- flaga potwierdzona w runtime env; decyzje nadal serializują `pickup_at`.

## Pomiar w cieniu — pre/post tail
Baseline (pre-flip, cała historia) = liczby wyżej. Skrypt:
`eod_drafts/2026-05-30/measure_pickup_freshness_tail.py`.

Post-flip (uruchom +7 dni, ~2026-06-06):
```
python eod_drafts/2026-05-30/measure_pickup_freshness_tail.py \
    --since-iso 2026-05-30T19:13:00+00:00
```
**Werdykt "było warto":** ogon `>10 min` spada z 17.5% wyraźnie (cel <12%) BEZ
wzrostu `total_duration_min` / `sla_violations` (koszt jazdy). Confound: brak
czystego A/B (w pełni LIVE) — porównanie pre-historia vs post-7d, mix zamówień
może lekko zaburzać; ogon strukturalnie stabilny w peakach, duży spadek = sygnał.

## Rollback (flaga off)
Usuń linię `Environment=ENABLE_OBJ_PICKUP_FRESHNESS=1` z `override.conf` (lub
przywróć `.bak-pre-obj-fresh-2026-05-30`) → `daemon-reload` → `restart
dispatch-shadow`. Bez redeploy kodu. Kod zostaje (default OFF, deploy-safe).

## Follow-up
- Branch `obj-pickup-freshness-2026-05-30` — merge do master po werdykcie +7d.
- Jeśli ogon nie spada: podbić COEFF (env `OBJ_PICKUP_FRESHNESS_PENALTY_COEFF`)
  lub obniżyć próg (`OBJ_PICKUP_FRESHNESS_THRESHOLD_MIN`) — bez redeploy kodu.
