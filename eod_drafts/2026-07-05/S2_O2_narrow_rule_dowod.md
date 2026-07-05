# S2/O2 (tmux 17) — wąska reguła O2: DOWIEDZIONA + SKALIBROWANA, replay GO — 2026-07-05

## Werdykt sprintu
**Wąska reguła `detour≤X ∧ carried≤Z` JEST już zaimplementowana przez K1** (`ENABLE_O2_CAPZ_RESEQ`,
scalona 02.07) — zgodnie z handoffem NIE dublowano, tylko **dowiedziono i skalibrowano**. Replay na
czystym korpusie λ=0: **GO** (mocniejszy niż bramka 02.07). Pełny werdykt + rekomendacja K1→K2:
`dispatch_state/o2_narrow_rule_replay_verdict.txt`. Wszystko za flagami OFF; flags.json nietknięty.

## ETAP 0 (zamknięty wcześniej, tracker ~20:15)
Baseline 4213/0 · werdykt S1 odczytany (250/258 z `sla_anchor_source`, dial=35 — bramka K1 zdrowa) ·
**oracle bundle_calib_review zdany (C9)** z near-missem (1. wersja nie przypinała overage-ONLY — probe
λ·czas_late ujawnił dziurę w samym oracle → wzmocniony asymetrycznym czas_late).

## Dowód reguły (mapa kompletności — bez dublowania)
- **Implementacja**: `route_simulator_v2._capz_reseq_plan` — 4 osie: (a) detour≤`O2_CAPZ_DETOUR_MAX_MIN`(8.0),
  (b) carried≤`O2_CAPZ_Z_MIN`(20), (c) gain≥`O2_CAPZ_MIN_GAIN_MIN`(2.0), (d) sla nie rośnie; size-guard ≤8 stopów.
- **Trójka RAZEM z konstrukcji**: reseq w ogonie `simulate_bag_route_v2` na WYBRANYM planie każdej strategii →
  feasibility (`feasibility_v2:846-849` metryka `o2_capz`) i `plan_recheck._sweep` dziedziczą przez jeden return.
- **Bliźniak selekcji**: `objm_lexr6` nie robi własnego solve (grep: 0 hitów simulate/plan) → dziedziczy.
- **Carried-first = 0 naruszeń**: `lock_first` we wspólnej enumeracji (`_enumerate_valid_plans`, bajt-parytet
  z bruteforce) + gate (d) + testy A-first + korpus regress_o2=0. Inwersje P-1..P-7 nietknięte.
- **Testy**: istniejące `test_o2_capz_reseq_2026_07_02` **20/20** (4 osie + mutation-polarity ×2 + kompozycja
  L3/L4/paczka/quantile + K2-gate) — potwierdzone biegiem z worktree.

## Kalibracja X/Z (czyste okno) — defaulty POTWIERDZONE
Korpus λ=0 (`bundle_calib_shadow_l0.jsonl`, od 03.07 07:46, ~2.6 dnia z peakami So/Nd; 1117 multi uniq,
505 differs, under_z 100%, zero zmieszania λ): **Z=20 → policy-improved 10.2%, med ΔO2 +9.55 min,
detour med −2.06 (!), p90 +6.16 ≤ X=8; regress_o2=0**. Z=32/35 dają więcej improved (14.8/16.2%) kosztem
ochrony niesionego — rekomendacja review = **Z=20** (max ochrona, materialność ≫2%). Zmiany stałych: ZERO.

## Prereqi K2 (nowe testy, worktree `o2-narrow-rule`)
- `test_o2_k2_plan_recheck_parity` (4): pod K1=ON klucz porównań **sla-free z konstrukcji** → sekwencja
  **K1 przed K2 = wymóg**; pod K1=OFF K2-wrażliwy (dowód); pin tekstowy źródła; bug4-shadow(1972)=log-only.
- `test_o2_k2_best_effort_parity` (4): sort best_effort czyta `plan.sla_violations` (pole zmieniane przez K2)
  — parytet NIE-z-konstrukcji, hierarchia termów nietknięta. Korpus 03-05.07: **0 decyzji best_effort**
  (weekend) → pomiar korpusowy = warunek flipu K2 po poniedziałkowym peaku (dane per-kandydat kompletne:
  1467/1467 alternatives z obiema kotwicami — owoc L1.1).
- `test_bundle_calib_oracle` (3): przyrząd werdyktu zwalidowany (C9).

## Rekomendacja dla FLIPMASTER (tmux 20) — sekwencja za ACK Adriana
1. **K1** `ENABLE_O2_CAPZ_RESEQ=true` (dopisać klucz do flags.json + doc flag-ratchet): po potwierdzeniu
   **at-208 (Pn 06.07 19:30)** — ten sam odczyt na ≥3.5 dnia (dziś ~2.6 dnia = werdykt wstępny GO).
2. **K2** `ENABLE_SLA_GATE_READY_ANCHOR=true`: dopiero po (i) K1 ON, (ii) L3 ≥2 dni obs (od 06.07 12:35),
   (iii) pomiarze parytetu picku best_effort na poniedziałkowym peaku (n≥10). ⛔ NIE flipować surowego O2
   (pułap 23.5% freshness-blind — łamie carried-first; werdykt bramki 02.07 podtrzymany).
Rollback: klucz false w flags.json (hot). Ryzyko latencji peak: brak (size-guard ≤8 stopów).
