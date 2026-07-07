# S28-C — world_replay_gate: schema-aware bucket wr0 + potwierdzenie (0,0) chokepoint

**Data:** 2026-07-07 · **Wykonawca:** tmux 28 · **Gałąź:** `s28c-worldreplay-bucket` (worktree `wt-s28c`, od `master@d808808`) · **Commit:** `1ea7237`
**Zakres:** wyłącznie `tools/world_replay_gate.py` + testy. ZERO silnika, flag, restartów. **Gotowe do merge.** (Live timer `dispatch-world-replay-gate.timer` = INFORMACYJNY; podniesie fix przy następnym biegu po merge.)

## Problem (diagnoza A2_worldreplay_minus40)
Rekordy `schema=wr0` (nagrane PRZED deployem wr1 ~01:00 Warsaw 07.07) NIE mają `live_inputs` (loadgov EWMA, K07 prefetch). `world_record.py`: *„Bit-w-bit replay wymaga rekordu wr1"*. Replay liczy te wejścia od nowa w świeżym procesie → in-proc EWMA nieodtwarzalna → kara load-governora −40 nienałożona → replay o 40 wyżej niż zapis → **fałszywa `ROZNICA-KRYTYCZNA`**. Nocna bramka 02:00 na oknie 07-06 (same wr0) wypluła `WERDYKT: DIFFS n=88 ... krytyczne=12` — z czego ≥12 to czysty artefakt luki nagrywania, nie bug determinizmu.

## Fix u źródła
`_iter_window_records` pomija rekordy `schema ∈ {None, "wr0"}` (`_PRE_WR1_SCHEMAS`, nazwany + **forward-compatible**: przyszły wr2 przechodzi) → nowy bucket `skipped_pre_wr1` (jak `skipped_no_now`), NIE liczone do `roznice`. `run_gate` + `render_verdict_txt` raportują `pominiete schema<wr1: N`. wr1 certyfikowane bez zmian.

## Dowody
- **LIVE (read-only, realny record dir):**
  - okno 07-06: certyfikowalne(wr1)=**0**, pominięte now=null=9, **pominięte schema<wr1=88** (dokładnie te 88 wr0-with-now, które dawały 12 fałszywych krytycznych).
  - okno 07-07: certyfikowalne(wr1)=**233**, pominięte schema<wr1=0 (wszystko wr1 → certyfikowane normalnie).
  - Żywy plik werdyktu (bieg 02:00 07-07): `WERDYKT: DIFFS n=88 ... (krytyczne=12)` — **to jest właśnie problem, który fix eliminuje** (po merge → te 88 wr0 = POMINIĘTE, 0 fałszywych krytycznych).
- **Test** `test_world_replay_gate_schema_bucket` (3): wr0 pominięty / wr1 realna różnica ZACHOWANA (case 485927) + **mutation-probe** (ten sam rekord 485927 przetagowany na wr1 → różnica WRACA = dowód, że suppression bierze się WYŁĄCZNIE z tagu schematu) + render `pominiete schema<wr1: 1`.
- **K17 (istniejący test bramki)** zaktualizowany do 3-tuple + fixtury `schema="wr1"` (8 testów dalej zielone).
- **Pełna regresja `pytest tests/` (wt-s28c): 4432 passed / 0 failed** (baseline 4429 + 3 nowe schema-bucket).

## ZADANIE 2 — (0,0)-coords chokepoint: potwierdzone WYSTARCZAJĄCE (B1 verdict trzyma)
B1 zdiagnozował (0,0) jako świadomy placeholder z chokepointem COORD_GUARD (`osrm_client.table/route`) i NIE ruszał guarda (źródło niejednoznaczne, ≥5 miejsc konwencji). Potwierdzenie dla bramki:
- `route((0,0),..)` → `coord_invalid=True`, **deterministyczny** (r1==r2), sentinel 9999 min — **nie fikcyjna trasa, nie wyjątek**. `table` z (0,0) → komórki `coord_invalid`.
- Konsekwencja dla replayu: (0,0) daje TEN SAM sentinel w zapisie i w replayu → **replay==zapis (zero spurious różnicy), zero `BLAD`**. Żywy werdykt: `bledy=0`.
→ Chokepoint wystarcza; `osrm_client.py` NIETKNIĘTY (zgodnie z B1). Fałszywo-alarmowy szum COORD_GUARD w err_burst rozwiązany osobno w B1 (Zadanie 1 `scheduled_flip_gate`).

## Rollback
`git revert 1ea7237` (przyrząd off-line; brak wpływu na silnik/serwisy). Baseline: gałąź niezmergowana.
