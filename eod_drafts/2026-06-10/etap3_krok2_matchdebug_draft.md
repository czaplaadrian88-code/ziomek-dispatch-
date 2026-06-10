# ETAP 3 / Krok 2 — odszumienie learning_log → courier_match_debug.jsonl (draft)

## Stan faktyczny (zmierzony 2026-06-10 18:55)
learning_log.jsonl: 8903 wpisów / 37.7 MB. MATCH_AMBIGUOUS 4271 + MATCH_NOT_FOUND
3664 + RESOLVE_CID_* 136 = 8071 wpisów = **90.7% LICZBY wpisów**, ale tylko
**1.14 MB = 3.0% bajtów** (objętość robią TIMEOUT_SUPERSEDED 20.8 MB/55% +
PANEL_OVERRIDE 15.2 MB/40% — pełne `decision` w środku). Notatka audytu
„≈90% pliku, 34 MB" myliła % wpisów z % bajtów — przeniesienie i tak poprawne
(konsumenci iterują po liniach; 90% linii to szum), korekta do memory w kroku 5.

## Writerzy do zmiany
1. `scripts/schedule_utils.py:_log_match_event` (MATCH_AMBIGUOUS/MATCH_NOT_FOUND)
   — plik POZA repo dispatch_v2 (untracked w workspace) → edit + .bak, bez commitu.
2. `dispatch_v2/shift_notifications/worker.py` 2 call-sites RESOLVE_CID_* →
   nowa funkcja `state.append_match_debug_log` (LEARNING_LOG/append_learning_log
   zostają nietknięte — generyczne, używane też w testach).

## Konsumenci learning_log (audit per lekcja #80 — ŻADEN nie liczy MATCH_*/RESOLVE_*)
- daily_briefing.py — agreement z {TAK,NIE,INNY,KOORD,TIMEOUT}; NIE per restauracja ✓
- learning_analyzer.py — HUMAN_ACTIONS/AGREEMENT_DENOM/TIMEOUT_ACTIONS (nazwane sety) ✓
- telegram_approver /status `_mp15_get_last_3_proposals` — target_actions set ✓
- validation_gate_lgbm.py — filtr decision.best.lgbm_shadow (MATCH_* nie mają) ✓
- tools/sequential_replay.build_roster — decision.best.courier_id +
  proposed/actual_courier_id (MATCH_*/RESOLVE_* nie mają tych pól) ✓
- sprint2_analysis/* — offline one-shot, nazwane akcje ✓
- panel/assistant (nadajesz_clone, scripts/assistant*) — grep: zero referencji ✓
- shift_notifications/state.py + schedule_utils.py — writerzy, nie czytają ✓

## Zmiany
- `schedule_utils.py`: nowy `MATCH_DEBUG_LOG_PATH=dispatch_state/courier_match_debug.jsonl`;
  `_log_match_event` pisze tam (mechanizm append bez zmian — rekordy <1KB, O_APPEND atomic).
- `shift_notifications/state.py`: `MATCH_DEBUG_LOG` + `append_match_debug_log()`
  (wspólny helper `_append_jsonl_to`).
- `shift_notifications/worker.py`: 2× RESOLVE_CID_* → append_match_debug_log.
- `tests/_shift_test_helpers.py`: redirect MATCH_DEBUG_LOG do tmpdir + expose.
- `tests/test_resolve_cid_score_based.py`: GRUPA 4 czyta match_debug_log.
- Stary learning_log NIETKNIĘTY (append-only, historia zostaje).

## Workflow
.bak × 5 → edit → py_compile → testy (resolve_cid + shift_notifications +
daily_briefing dry-run) → commit (pliki dispatch_v2) + tag
`learning-log-denoise-2026-06-10`; schedule_utils.py poza commitem (poza repo).
