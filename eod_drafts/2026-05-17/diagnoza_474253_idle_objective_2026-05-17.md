# Diagnoza — order 474253: kurier stoi 15 min pod restauracją zamiast doręczać

**Order:** 474253 Rany Julek → Sybiraków 8/1 (czasówka, EVAL_9) ·
**Kurier:** 387 Aleksander G · **Decyzja:** 2026-05-17 15:58:56 UTC (17:58 Warsaw)
**UWAGA:** to PRZED deployem E1+E2+E3 (21:03 UTC). E1-E3 tego NIE naprawiają — inny bug.

## Co OR-Tools zrobił
`strategy=ortools` (plan solvera, nie greedy), `sla_violations=0`, score -13.7.
Sekwencja pełna: Sadowa(drop) → Pan Schabowy(pickup 18:19) → **Rany Julek(pickup
18:42)** → Stołeczna(drop 18:46) → Sybiraków(drop 18:56).
Adrian: powinno być Pan Schabowy → **Stołeczna(drop)** → Rany Julek → Sybiraków
— doręczyć w-ręku jedzenie podczas 23-min okna do gotowości Ranego Julka,
zamiast stać idle pod restauracją.

## Root cause — objective OR-Tools nie wycenia IDLE

OR-Tools minimalizuje `cost_matrix` = czas JAZDY (+ dwell). Czekanie kuriera na
gotowość pickupu to **slack w Time dimension — w objective DARMOWY**. Solver jest
więc obojętny między „dojedź do RJ 18:27 i stój 15 min" a „doręcz Stołeczną,
dojedź do RJ 18:42, stój 0" — dopóki suma jazdy podobna. R8 pickup_span=23.6 min
(dwa pickupy 23 min od siebie) — solver mostkuje tę lukę idle'em, bo idle nic nie
kosztuje w jego funkcji celu.

## P3D1 idle-cost miał to naprawić — jest strukturalnie zepsuty

`ENABLE_V328_P3D1_IDLE_COST` augmentuje `cost_matrix[i][j] += ready_min[j] −
time_matrix[i][j]` dla krawędzi do pickupów. Cztery wady:
1. **`time_matrix[i][j]` = pojedyncza krawędź**, nie skumulowany przyjazd —
   zakłada że kurier teleportuje się do `i` w t=0. Dla pickupów głęboko w trasie
   absurdalny over-estimate.
2. **Augmentuje KAŻDĄ krawędź do pickupu** jednakowo → brak gradientu „pickup
   późno vs wcześnie w trasie". Sekwencja A i B mają po jednej krawędzi do RJ —
   kara trafia obie, nie różnicuje idle-15-min od idle-0.
3. **Perwersyjny incentyw:** kara = `ready − leg(i,j)` → DŁUŻSZA krawędź dojazdu
   = MNIEJSZA kara. Solver nagradzany za nadkładanie drogi do niegotowego pickupu.
4. **Magnitudy absurdalne:** ten solve (N=6, bag=2) — `idle_estimate_min=176.9`
   na 9 krawędziach, przy realnej jeździe ~30 min. Augmentacja idle DOMINUJE
   objective ~6:1 → solver optymalizuje szum, nie trasę. (N=13: idle_estimate=1527.)

Dowód: log `V328_P3D1_COST oid=474253 N=6 idle_estimate_min=176.9`;
`bonus_r9_wait_pen_legacy=-161.62` (scoring widzi gigantyczny postój, ale aktywna
kara `bonus_r9_wait_pen_v327=0` — scoring też pod-karze, choć to osobna sprawa).

## Fix — kierunki (do ACK Adriana)
- **Q (quick):** `ENABLE_V328_P3D1_IDLE_COST=0` — P3D1 obecnie aktywnie SZKODZI
  (dominuje objective szumem). Wyłączenie = solver wraca do czystej jazdy.
- **Proper:** objective musi wyceniać REALNY wait. OR-Tools Time dimension zna
  `CumulVar` (skumulowany czas). Ukarać slack/wait przy pickupach (CumulVar-aware)
  ALBO penalizować całkowity SPAN trasy (makespan start→koniec — łapie idle
  globalnie, realizuje „throughput per shift" z `feedback_dispatch_idle_vs_drive`).
- P3D1 w obecnej formie → wyrzucić/przeprojektować.

## Zakres
Osobny bug od E1-E3 (tu solver działał — `strategy=ortools`). Niezależny od E4
(pozycja kuriera). Nowy item: „idle/wait w objective OR-Tools".
