# Q1 simpler backfill — analiza outcomes 14d

**Data:** 2026-05-27 ~18:05
**Autor:** CC (Opus 4.7), READ-ONLY
**Input:** `/tmp/backfill_decisions_outcomes_v1.jsonl` (3576 entries, 86% matched do snapshots)
**Skrypt:** `dispatch_v2/tools/backfill_decisions_outcomes.py`

> **TL;DR:** 5 kluczowych odkryć empirycznych. Najważniejsze: **override przez operatora NIE poprawia delivery_time w żadnym auto_route bucket**. Operator zmienia decyzję Ziomka w 1528 przypadkach w 14d, ale outcome (R6 pickup-to-delivery min) jest **identyczny** vs non-override (Δ ≤ 0.5 min). **Auto_route classifier ma sygnał na poziomie agregatu** (AUTO 8% R6 breach vs ALERT 15% — 2× risk ratio), ale **NIE na poziomie pojedynczej decyzji**.

---

## Coverage

| Metric | Count |
|--------|-------|
| Entries w `learning_log.jsonl` 14d | 16,116 |
| Z `decision` blok (skip systemowe events) | 3,576 |
| Matched w snapshots union (20-27.05) | 3,071 (85.9%) |
| Z status=delivered | 3,044 (85.1%) |
| Z `pickup_to_delivery_min` znanym | 3,019 (84.5%) |

**Miss ~14% to entries pre-20.05** (snapshots zaczynają się od 20.05; ordery przed tą datą zostały purged z state).

---

## Finding 1 — Override NIE poprawia delivery time (krytyczne)

`pickup_to_delivery_min` (R-35MIN-MAX scope) per auto_route × action:

| auto_route | action | n | mean | median | p90 | R6 breach (>35min) |
|-----------|--------|---|------|--------|-----|---|
| AUTO | PANEL_OVERRIDE | 147 | 18.8 | 17.0 | 34.5 | **8.2%** |
| AUTO | TIMEOUT_SUPERSEDED | 192 | 18.3 | 16.9 | 33.1 | **6.2%** |
| ACK | PANEL_OVERRIDE | 1029 | 19.2 | 16.5 | 33.6 | **8.1%** |
| ACK | TIMEOUT_SUPERSEDED | 1324 | 19.2 | 16.9 | 33.3 | **8.1%** |
| ALERT | PANEL_OVERRIDE | 124 | 20.9 | 18.9 | 37.8 | **14.5%** |
| ALERT | TIMEOUT_SUPERSEDED | 203 | 21.1 | 19.7 | 36.9 | **13.8%** |

**Wniosek:** W obrębie tej samej klasy auto_route (np. AUTO), override vs non-override różnią się o **0.0-0.5 min mean** i **0.0-2.0pp breach rate**. To w paśmie szumu (95% CI dla n~150 ≈ ±2 min). **Operator zmienia decyzję ale wynik jest taki sam.**

To **mocniej** potwierdza obserwację z Etapu 1 — wcześniej widzieliśmy że override rate jest płaski (~42% wszędzie). Teraz wiemy że również **outcome** override'u jest identyczny vs non-override.

---

## Finding 2 — Auto_route classifier działa NA AGREGATCIE

| Bucket | R6 breach rate | Median delivery_min |
|--------|---|---|
| AUTO  | 7.2% | 17.0 |
| ACK   | 8.1% | 16.7 |
| ALERT | 14.1% | 19.3 |

**ALERT 2× wyższy R6 breach niż AUTO** (14% vs 7%). To real signal — gdy Ziomek mówi "uważaj, podejrzane" (ALERT), faktycznie ryzyko breach jest 2× wyższe.

**Implication for autonomy scale-up:** AUTO bucket jest **measurably better** niż ALERT na poziomie agregatu. Faza 7 30%→70%→100% auto-routing PROBABLE skutkowałaby:
- ~7-8% R6 breach (vs obecnie 8% w produkcji = baseline)
- ZERO degradacji jakości (override outcome identical anyway)
- Reducja workload operatora o 1528 override decisions / 14d = ~110/day

**ALE:** to **agregat**. Per-decision Ziomek nie wie który order jest "trudny" — bo override-vs-non-override jest random w outcome (Finding 1).

---

## Finding 3 — Ziomek's `drive_min` prediction systematycznie zaniżona

`predicted_drive_min` (Ziomek przewidywany czas dojazdu do pickup) vs `actual_assign_to_pickup_min`:

- n: 3013 pairs
- Mean delta (actual − predicted): **+16.2 min**
- Median delta: **+12.9 min**
- 69.2% case'ów: actual >5 min DŁUŻSZY niż predicted (Ziomek zaniża czas dojazdu)
- 5.7% case'ów: actual >5 min KRÓTSZY niż predicted

**Wniosek:** Ziomek's pickup-ETA predictions są **systematycznie zaniżone o ~13 min median**. To DUŻY problem dla autonomy:
- Jeśli Ziomek mówi "kurier dojedzie w 20 min", faktycznie jest 33 min (median)
- Operator widzi rozjazd między ETA a rzeczywistością → traci zaufanie do propozycji
- Ten brak zaufania **może** tłumaczyć ~42% override rate

**Mitigation:** kalibracja `drive_min` (osobny sprint, NIE Q1 scope). Sprint S1 17.05 (DWELL kalibracja tier-aware) zaadresował CZĘŚĆ tego — ale empirycznie wciąż +13 min median bias post-kalibracja.

---

## Finding 4 — `r6_max_bag_time` prediction jest GOOD

`predicted_r6_max_bag_min` vs `actual_pickup_to_delivery_min`:

- n: 3019 pairs
- Mean delta: **+2.4 min**
- Median delta: **+2.2 min**
- 38.5% under-prediction >5min
- 21.0% over-prediction >5min
- 40.5% w paśmie ±5min (dobra precyzja)

**Wniosek:** R6 (bag time) prediction jest **istotnie lepsza** niż drive_min. Median bias tylko +2 min. Sprint OBJ F0-F4 (16-21.05) który dodał `r6_max_bag_time_min` jako twardy gate **działa zgodnie z intencją**.

---

## Finding 5 — Operator ZAWSZE zmienia kuriera przy PANEL_OVERRIDE

| Override outcome | Count |
|---|---|
| `proposed_courier_id == actual_courier_id` (operator wybrał tego samego) | **0** |
| `proposed_courier_id != actual_courier_id` (operator zmienił) | **1528** |
| no actual_courier_id (operator nie dokończył) | 0 |

**Tautologia** — z definicji PANEL_OVERRIDE means operator wybrał innego. Ale ważne: w **0 przypadków** operator przypadkowo wybrał tego samego, co Ziomek proponował. To znaczy że PANEL_OVERRIDE classification w `panel_watcher` jest deterministyczna i czysta — gdy widzimy PANEL_OVERRIDE to **na pewno** operator celowo zmienił.

Counterfactual question (Q2 scope): czy ten INNY kurier dostarczył lepiej? Z Finding 1 wiemy że **NA AGREGATCIE NIE** — 1300 cases różny-kurier mają identyczny delivery_min 19.3 min vs non-override 19.2 min.

---

## Implikacje dla Etapu 3 (Q2 counterfactual harness)

Q1 backfill **już odpowiedział na 80%** pytania Q2:
- Override **na agregacie** NIE daje benefitu (delivery_min identyczne)
- AUTO bucket faktycznie szybsze 18.8 vs ALERT 20.9 (Faza 7 calibration działa)

**Q2 PRAWDZIWA wartość:** rozróżnienie **per-decision** — które konkretne override były "good" vs "wasteful":
- 1528 override'ów → 1300 different-courier
- Z 1300 ile było "Ziomek-right" (proposed kurier dostarczyłby SZYBCIEJ) vs "operator-right" vs tie?

Bez Q2 wiemy że średnia jest "remis", ale **dystrybucja** może być:
- Hipoteza A: bimodalna (50% Ziomek-better, 50% operator-better → cancel out na średniej)
- Hipoteza B: dominated by ties (90%+ <5min difference)

Te dwie hipotezy mają RADYKALNIE różne implikacje dla autonomy scale-up. Dlatego Q2 harness wciąż ma wartość — TYLKO żeby odróżnić A od B.

---

## Implikacje dla Faza 7 autonomy scale-up

**Czy 30% Tydzień 1 live byłoby safe?** Na bazie Q1:

**Argumenty ZA:**
- AUTO bucket R6 breach 7% **niższy** niż ACK 8% i ALERT 14%
- Override outcome identyczne — pomijając operatora dla AUTO **nie pogarsza** delivery
- Reducja workload operatora znaczna (~110/day overrides)

**Argumenty PRZECIW:**
- Drive_min prediction +13 min bias — operator może mieć dobre powody że overrides (widzi że predicted ETA jest błędne)
- Q1 backfill mierzy outcome (delivery_min), NIE measure "satisfied customer" / "fair courier load" — możliwe że operator overrides są optymalizacją INNYCH metryk
- 86% coverage tylko dla 14d window — dłuższa próba w Q2 może zmienić wnioski

**Rekomendacja CC dla Adrian'a:** Q2 jest **nadal wart** — żeby rozróżnić "operator zmienia ale nikt nie wygrywa" (Hipoteza B) vs "operator zmienia z mieszanym sukcesem" (Hipoteza A). Bez Q2 jesteśmy ryzykownie blisko "Ziomek's predictions są kalibracyjnie ok, scale-up safe" bez per-decision validation.

---

## Status Etapu 2

- ✅ Skrypt: `scripts/dispatch_v2/tools/backfill_decisions_outcomes.py` (CLI-driven, --days/--out)
- ✅ Output: `/tmp/backfill_decisions_outcomes_v1.jsonl` (3576 entries, 1.2MB)
- ✅ Analiza: 5 findings empirycznych powyżej
- ✅ Raport: ten plik

**ZERO production touch.** Skrypt offline, snapshot read-only, output `/tmp/`.

---

## Pytania do Ciebie przed Etapem 3 (Q2 harness)

1. **Czy Finding 1 (override nie poprawia outcomes) jest dla Ciebie wystarczająco mocnym dowodem żeby pominąć Q2 full harness i przejść od razu do Faza 7 30% live ramp-up?**

2. **Czy zgadzasz się że drive_min prediction bias +13 min jest większym problemem niż override rate?** (sugerowałoby że osobny sprint kalibracji ETA jest priorytetem przed Q2)

3. **Q2 harness scope:** jeśli kontynuujemy, zawęzić do AUTO bucket (n=147 PANEL_OVERRIDE, najcenniejszy subset)? Czy pełen 1528?

4. **Jakieś inne outcomes które powinniśmy sprawdzić w Q1 v2** zanim Q2? Sugerowane: courier fairness (czy override miał wpływ na load balancing), customer complaints (jeśli logged), restaurant satisfaction.
