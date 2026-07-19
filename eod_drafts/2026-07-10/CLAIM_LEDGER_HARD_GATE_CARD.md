# KARTA GO/NO-GO — flip HARD inwariantu claim-ledger (Sprint B)
Wygenerowana automatycznie przez `at` 10.07. Detal: memory [[sprint-inwarianty-claim-ledger-2026-07-08]].

## WERDYKT: ✅ GO — 0 breach w 4231 wierszach przez 245.9 h (≥2 dni). Flip HARD za ACK; decyzja: raise vs drop-feral-claim

- Okno od: 2026-07-08T14:14:00+00:00 (flip CHECK ON, ACK Adriana 08.07)
- Wpisów resweepu z polem g_claim_ledger_breaches: 4231
- Pierwszy / ostatni wpis: 2026-07-08T14:14:14.671381+00:00 … 2026-07-18T20:11:11.579424+00:00  (245.9 h)
- Suma breach (fałszywek log-loud, oczekiwane 0): 0
- Przykłady breach: brak (0 FP)

## Jeśli GO — kroki flipa HARD (FLIPMASTER, za ACK Adriana):
1. Decyzja semantyki: raise wyjątek vs „drop feralnego claimu" (⚠ HARD w resweepie zatrzymałby tick).
2. Dopisać `ENABLE_CLAIM_LEDGER_INVARIANT_HARD=true` w flags.json (hot).
3. Monitor 1h + rollback = flaga false.

## PRZYPOMNIENIE — Sprint A (perf) też czeka:
Flip **A2** (deterministyczny budżet OR-Tools, branch `perf/p95-ortools` NIEzmergowany) — bramka: replay end-to-end przez route_simulator + 2 dni cienia + ACK. Detal: [[sprint-perf-p95-ortools-det-2026-07-08]].
