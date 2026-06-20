# Cross-regime arbitraż — robustness czasowy + ustalenia projektowe (2026-06-20 wieczór)

Kontynuacja `arbitrage_forward.py` (commit `b393909`). Cel: czy przewaga V3 isotonic-P(win) nad baseline'ami to artefakt jednego okna, czy sygnał odporny czasowo.

## 1. Rolling-window robustness (5 rozłącznych okien forward 14d, `run(end_offset=...)`)

| end_offset | cutoff (forward start) | n MIXED | B1 solo-first | B2 unified | V3 isotonic | V3 − max(B1,B2) |
|---|---|---|---|---|---|---|
| 0  | 2026-04-07 | 1448 | 0,179 | 0,244 | **0,289** | +0,045 |
| 14 | 2026-03-23 | 1401 | 0,177 | 0,232 | **0,235** | +0,003 (wąsko) |
| 28 | 2026-03-09 | 1421 | 0,186 | 0,237 | **0,276** | +0,039 |
| 42 | 2026-02-23 | 1654 | 0,181 | 0,224 | **0,241** | +0,017 |
| 56 | 2026-02-09 | 1613 | 0,144 | 0,208 | **0,226** | +0,018 |

**Werdykt: V3 isotonic BIJE OBA baseline'y na MIXED w 5/5 rozłącznych okien** (Feb 9 → Apr 20, ~2,5 mies. held-out); V3 = najlepszy wariant arbitrażu w 5/5. Przewaga konsekwentnie dodatnia (+0,3 do +4,5pp, śr. ~+2,4pp). **Odwrócenie werdyktu „brak arbitrażu" NIE jest artefaktem jednego okna.** Caveat uczciwy: okno eo=14 marginalne (+0,3pp). Dane: `sweep_rows.json`.

## 2. Ustalenie projektowe A — regime-matched isotonic > all-candidate isotonic

Harness V3 (`_fit_pwin_calibrators`) fituje solo_cal/bundle_cal na score'ach WSZYSTKICH kandydatów (bez filtra reżimu). Niezależna re-derywacja **regime-matched** (solo_cal TYLKO z empty-kandydatów, bundle_cal TYLKO z bagged) dała **wyżej: V3 MIXED 0,314 vs 0,289** (eo=0). To projekt poprawniejszy — przy decyzji każdy kalibrator stosowany jest tylko do swojego reżimu, więc i fitowany powinien być na swoim reżimie. **Rekomendacja dla live-impl: regime-matched isotonic.** (Werdykt „bije baseline'y" trzyma się w obu wariantach.)

## 3. Ustalenie projektowe B — shadow `daa276f` NIE wystarcza do live-eval isotonic

Shadow loguje `lgbm_twomodel_shadow = {winner_cid, agreement_with_primary, regime_counts, latency_ms, n_candidates_scored}` — **brak per-kandydat surowych score'ów solo/bundle.** Czyli dzisiejszy live-shadow (at#157) waliduje RAW dwumodel (winner + agreement + reżimy + latency), ale **post-hoc isotonic na żywych decyzjach NIE jest możliwy bez additive pola** logującego per-kandydat `{courier_id, regime, solo_score, bundle_score}`. To precyzyjny wymóg Etapu 3→4 — zmiana ADDITIVE, do zrobienia PO at#157 (żeby nie kolidować z dzisiejszym deployem `daa276f`).

## Wniosek dla Fazy 7
Arbitraż isotonic = robustny mechanizm (5/5 okien), z poprawniejszym wariantem regime-matched. Następne kroki: (1) at#157 dziś — raw shadow live; (2) PO shadow: additive per-cand-score log + persist kalibratorów regime-matched + walidacja na realnych outcomes. Flip primary dopiero gdy live-outcomes (NIE mimikra) potwierdzą. Doc nadrzędny: `/root/ZIOMEK_FAZA7_TWOMODEL_SPRINT_2026-06-20.md`.
