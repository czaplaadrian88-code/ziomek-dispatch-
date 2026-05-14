# Scoring rebalance #38 — replay validation pre-work (2026-05-14 noc)

**Status:** PRE-WORK done, **NIE deploy / NIE commit**. Awaits Adrian ACK + Fix 1 obs gate ~2026-05-20.

## Artefakty

- **Fixture:** `tests/fixtures/calibration_cases/2026-05-13_472791_pani_pierozek_picked_up_loss.json`
  - Źródło: `dispatch_state/learning_log.jsonl` filtered `action=PANEL_OVERRIDE order_id=472791 ts~09:57:03`
  - Pełny payload `decision` + `best` + meta (alternatives=0, serializer limitation)
- **Test:** `tests/test_v328_calibration_fixture_472791.py` (14 testów, 100% PASS)
  - Archive assertions: proposed=514 / actual=470 / score=-13.27 / bag=2 / R1+20 / pre_shift / pool_total=12 feasible=1
  - Forward markers (docstring): Fix 2 / Fix 3a / Fix 3b expected behaviors — manual gate w sprincie

## Gap discovered

`serializer alternatives=0` → Piotr K-470 (pool reject @ feasibility-stage) NIE jest w fixture. Pełna replay
(Piotr > Tomek post-Fix) wymaga **state-snapshot orders_state.json @ 11:54 13.05** — brak backupu w
`dispatch_state/`. Opcje przy sprincie Fix 2+3:
1. ~~BX11 restic restore `dispatch_state/orders_state.json` dla 2026-05-13 ~11:54 UTC~~ **NIEMOŻLIWE
   (verified 2026-05-14 00:55):** restic daily snapshots cadence = 01:30 UTC (latest `87ffbef0`
   z 13.05 01:30 UTC = 8h+ PRZED incydentem). Żaden snapshot nie matchuje 11:54 UTC state.
   Problem to **cadence**, nie age — retention 7d/4w/6mo nie pomaga. Daily backup z 14.05 01:30
   to state ~16h POST incident. **Opcja wyeliminowana.**
2. **Synthetic fleet snapshot** zbudowany manualnie z handoff narrative (Piotr K-470 bag=1
   picked_up Wiosenna, predicted_delivered=11:58 + Tomek K-514 bag=2 assigned Sweet Fit→Zachodnia W
   + Rany Julek→Wyszyńskiego N) — mniej rygorystyczne ale wystarczy dla acceptance.
   **Default choice w sprincie Fix 2+3.** **LIVE 2026-05-14 ~13:40 Warsaw:** fixture
   `tests/fixtures/calibration_cases/2026-05-13_472791_synthetic_fleet.json` (12 kurierów: 2 load-bearing
   514/470 + 10 stubów dla pool_total=12 parity). 7/7 sanity assertions PASS (`_meta`, order_new,
   fleet_snapshot, cids, Piotr picked_up+ETA=4, Tomek bag=2 assigned, expected_replay_outcomes
   payload z 3 etapami pre/Fix2/Fix2+3a+3b). Limitations explicit w fixture body (stubs placeholder,
   coords ±200m, OSRM live/mock w replay harness, Piotr restaurant unknown). **Committed 2026-05-14
   ~13:50 Warsaw** (Adrian ACK granted post-fixture sanity 7/7); replay execution + implementation
   Fix 2+3 nadal awaits Fix 1 obs gate ~20.05 + spec ACK.
3. Live replay przez `replay_failed.py --oid 472791` po Sprint #1 fleet fix (#1 DONE 2026-05-07
   commit `0aecbab`) — wymaga panel data retention check (zazwyczaj 7d only — `edit-zamowienie`
   API może już nie mieć detail dla 472791 do 20.05). **Backup opcja jeśli synthetic mało.**

## Hard gates przed sprintem Fix 2+3

- [ ] Fix 1 (pickup-label render) 7-day obs window **stable** ~2026-05-20
- [ ] Adrian ACK na Fix 2+3 spec (3 fundamental changes — `effective_start_pos` worst-case +
      `s_almost_free_bonus` + `MIN_PROPOSE_SCORE` recalibrate)
- [ ] Replay state-snapshot strategia decided (option 1/2/3 above)
- [ ] Obs serializer extended z `km_to_pickup_tail` + `s_almost_free_bonus` + `s_bag_pending_pickup_penalty`
      (Lekcja #109 — downstream test mandatory)

## Cross-ref

- Tech debt #38 (P0 post replay validation, NEW 2026-05-13)
- Lekcja #82 (empirical fixture-first parsing — extended teraz do scoring rebalance)
- Lekcja #116 (multi-source UI Fix 1 sibling, commit 1d87307)
- Sprint #472338 (P3-D5/D6 R1 corridor + trajectory smoothness) — `2026-05-10_472338_ogniomistrz_zigzag.json`
  pattern z którego skopiowane

## NIE zrobione (deferred do sprintu)

- Replay execution (wymaga state-snapshot)
- Numeric expected post-Fix scores dla Piotra (wymaga state-snapshot)
- Implementation Fix 2/3a/3b (BLOCKED hard gate)
