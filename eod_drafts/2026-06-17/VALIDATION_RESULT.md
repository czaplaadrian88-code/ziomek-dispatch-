# WALIDACJA objm-lexr6 D2-shadow — 2026-06-24T09:00:01

Decyzje z telemetrią (od flipu 17.06): **1435**
Flipy D2≠live: **174** (12.1% decyzji)
  z czego bierze kuriera feasible (na czas): 174

## Bramki (spec §6)
- G1 Σ(d_r6_breach+d_committed) = **-533 min** (cel ≤ -50) → ✅
     (R6-breach -402 min, committed -131 min)
- G2 regresje = 8/1435 = **0.56%** (cel < 1%) → ✅
- G3 próbka ≥ 200: **1435** → ✅
- KOSZT (do akceptacji Adriana): new-pickup-late -70 min, idle +52 min (ujemne = też zysk)

## WERDYKT: PASS — buduj Fazę 2 (live-flip za ACK)

⚠ Brama outcome (czy +new-late psuje R6 NOWYCH zleceń downstream) = osobny join z
backfill_decisions_outcomes_v1.jsonl — sprawdź ręcznie przed Fazą 2.
