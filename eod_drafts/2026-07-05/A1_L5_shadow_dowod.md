# A1-L5 (Sprint 1 Z3, tmux 17) — ETA load-aware ZBUDOWANE jako SHADOW, flaga OFF — 2026-07-05

## Werdykt
**L5.1 zbudowana** (jedyna niezbudowana fala Fazy 3 — bramka 04.07 nadrobiona): kod scalony za flagą
`ENABLE_ETA_LOAD_AWARE` **default OFF** (ETAP4 + stała modułowa; NIE dopisana do flags.json), metryki shadow
w decyzjach, **replay-dowód POZYTYWNEGO wpływu out-of-sample: PASS**. Flip = rekomendacja ZA ACK (trade-off niżej).

## Co zbudowane (branch `l5-eta-load-aware`, commit `e020766`)
- `eta_load_aware.py` — bufor[min] = clamp(−med_błędu(segment), 0, 12); hierarchia (tier×solo/bundle)→tier→_global;
  min_n=30; segment pesymistyczny → 0; fail-soft (brak/zły plik → 0.0 = zachowanie bajt-identyczne). Cache po mtime.
- `tools/eta_load_aware_calibrate.py` — JEDYNY writer `dispatch_state/eta_load_aware_calib.json`; join = import
  `eta_truth_map.build_rows` (zero 2. kopii logiki pomiaru).
- Hook `dispatch_pipeline._v327_eval_courier_inner` (po finalizacji eta_pickup_utc, przed konsumentami):
  SHADOW zawsze → metryki `eta_la_buffer_min` + `eta_pickup_load_aware_utc` (auto-serializacja L1.1, A+B);
  ON → bufor przesuwa `eta_pickup_utc`/`travel_min` + tag `eta_source+="+load_aware"` (oś OBIETNICY:
  wait-penalty, extension V3.24-A, target_pickup/committed-propozycja).
- `tools/eta_load_aware_replay.py` — kontrfaktyczny dowód + werdykt-plik `dispatch_state/eta_load_aware_replay_verdict.txt`.

## Granice zakresu (świadome, zapisane — nie rozszerzać bez zapisu)
- **feasibility_v2 NIETKNIĘTE**: R6 GATE-STRICTER na buforowanej osi + **Q2 „nie zdąży→nie dostaje"** = OSOBNY pas
  bramkowany ACK (inwersja HARD — roadmapa L5 ⛔HARD; TWIST: Q2 MUSI liczyć na load-aware buforze, który od dziś istnieje).
- **no_gps/pre_shift** poza buforem (post-loop polityka max(15,prep)/clamp — równe traktowanie, nie oś K3).
- **Scarcity (pool_feasible)** logowana w pomiarze, ale NIE w buforze v1 (cyrkularność: feasibility→pool→bufor);
  segmentacja tier×solo/bundle pokrywa gros sygnału (std|solo −8.55 vs gold|bundle −2.5).
- **L5.2 (rozdział eta decision/display)** = następny increment fali (nie w tym sprincie).

## Kalibracja i dowód (anty-leakage)
- **Okno A (kalibracja)**: 28.06→02.07, n=644 → tabela 16 segmentów (std|solo med −8.55 n=37 · std|bundle −4.02 ·
  std+|bundle −3.9 · gold|bundle −2.5 · new −5.57 · unknown −0.16→bufor ~0).
- **Okno B (ewaluacja, ROZŁĄCZNE)**: 03.07→05.07, n=415, buffered 415/415:
  | | med | p10 | p90 | share|err|≤5 |
  |---|---|---|---|---|
  | RAW (OFF) | **−3.73** | −15.62 | +6.14 | 45.8% |
  | CORR (ON) | **+0.42** | −12.33 | +10.71 | **47.2%** |
  Kryteria: bias→0 ✅ · celność nie spada ✅ · ogon ≤ cap ✅ → **PASS**.
- **Trade-off do ACK przy flipie**: p90 rośnie +6.1→+10.7 (obietnice bardziej konserwatywne = kurier częściej
  przed obiecanym czasem; kierunek zgodny z K3/GATE-STRICTER, ale zmienia rozkład extension/wait penalty).
- Baseline pełny (28.06-04.07, n=925): med −4.0, ciasno −5.1, solo −6.0 (scratchpad eta_truth_map_baseline_z3.md).

## Dowody protokołu
- Testy 8/8 `test_eta_load_aware_l51`: moduł (hierarchia/min_n/pesymizm/clamp/fail-soft) + **e2e REALNY assess_order**
  (archetyp 472791): shadow-OFF metryki obecne + decyzja nietknięta; **ON≠OFF** (eta przesunięte, tag eta_source);
  bez tabeli = bajt-parytet. Mutation-probe ×2: (L1) gałąź ON wyłączona → e2e-ON FAIL ✅; (L2) metryka wycięta → 3 FAIL ✅.
- ⚠ near-miss procesu: probe-restore `git checkout` na NIEZACOMMITOWANEJ pracy zdjął hook razem z mutacją →
  reguła: **commit PRZED mutation-probe** (probe L2 powtórzony na zacommitowanym stanie).
- Pełna regresja z worktree: patrz tracker (wynik przy merge).

## Deploy/flip (NIC nie wykonane — zasady sprintu)
Kod inertny do restartu dispatch-shadow (razem z czekającym L6.C `d8328b2` — świadomość deployowa zasada 5).
Po restarcie: shadow metryki `eta_la_*` zaczną płynąć w ledgerze (flag OFF, decyzje nietknięte) → 2 dni obserwacji →
flip `ENABLE_ETA_LOAD_AWARE` (wpis do flags.json + doc w ZIOMEK_LOGIC_REFERENCE — ratchet flag-doc) ZA ACK Adriana
z jawnym trade-offem p90. Rollback: flaga OFF (hot) / usuń tabelę calib (fail-soft 0) / revert `e020766`.
Odświeżanie tabeli: re-run generatora na świeżym oknie (decyzja o cadence przy flipie).
