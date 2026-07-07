# HANDOFF — tmux 28 = WYKONAWCA Sprintu 28 (narzędzia · odporność · skala; ZERO silnika decyzyjnego)

**Jesteś wykonawcą.** Koordynator = główna sesja Claude. **START:** `dispatch_v2/CLAUDE.md` (Przykazanie #0) → `docs/CODEMAP.md`. **Pełny plan:** `eod_drafts/2026-07-07/SPRINTY_27_28_PLAN.md` (READ FIRST). venv: `/root/.openclaw/venvs/dispatch/bin/python`.

## ⛔ REGUŁY
- **ZERO flip / restart / merge-do-master / Telegram / dotykania `flags.json`.** Cały Twój zakres to narzędzia/testy — nie dotyka żywego silnika (zero ryzyka), ale merge i tak za ACK.
- **tmux 27 rusza silnik/predykcję/kanon** (`route_simulator`, `plan_recheck`, `route_order`, `calib_maps`, `candidates`…). **NIE wchodź w te pliki** — gwarancja bezkolizyjności 27↔28. Ty ruszasz TYLKO `tools/*`, `tests/*` (nazwy `test_inv_*`/`test_canon_*`/`test_perf_*`), checkery, `world_replay_gate`, `ZIOMEK_INVARIANTS.md`, perf.
- **Worktree per zadanie** (ADR-007). Regresję oceniaj po **LIŚCIE ID faili PRE vs POST** — ~25 faili = artefakt worktree ([[feedback-worktree-shared-pkgroot-false-fails]]). **Sprzątaj worktree po sobie.**
- Raporty → `eod_drafts/2026-07-07/S28x_*.md`. Gotowe do merge → zgłoś koordynatorowi.

## TWOJE 4 ZADANIA (rozłączne pliki)

### 28-A Hardening checkerów (worktree) — fix u źródła dzisiejszej lekcji
Dodaj `.claude` do `_EXCLUDE_DIRS` w `tools/canon_static_check.py` + analogicznie w `test_decide_facade_k09.py` i `test_tz_zoneinfo_consolidation.py` (skanują repo → liczą sąsiednie worktree jako duplikaty → fałszywe faile). Rozważ `test_courier_reliability`/`test_a2_selection_shadow` `REPO=parents[2]` hardcode. **Dowód:** te checkery przechodzą NAWET z aktywnym sztucznym sąsiednim worktree. → `S28A_checker_hardening.md`.

### 28-B Kolejne inwarianty (worktree)
Kontynuacja B2 (`eod_drafts/2026-07-07/B2_inwarianty.md` — nie dubluj; dashboard był miejscami STALE). Dozbroić 3-5 NASTĘPNYCH pustych slotów `ZIOMEK_INVARIANTS.md` **bez zmiany silnika** (regression-guard). Każdy mutation-probe RED. → `S28B_inwarianty.md`.

### 28-C World-replay wr0 bucket (worktree)
Schema-aware bucket w `tools/world_replay_gate.py`: `schema=wr0` → „POMINIĘTE" zamiast fałszywej „ROZNICA-KRYTYCZNA" (kontekst: `A2_worldreplay_minus40.md`) + test (wr0 pominięte / wr1 realna różnica zachowana, case 485927). Domknij (0,0)-coords wg `B1_przyrzady_fix.md`. → `S28C_monitory.md`.

### 28-D Perf peak p95 (read-only)
Zmierz ogon latencji peak p50-p99 z żywych danych (journalctl dispatch-shadow `latency_ms` w peakach) + diagnoza składników (OR-Tools/login CSRF/OSRM) + rekomendacja przed skalą/multi-city. Zaznacz co = zmiana silnika (→ tmux 27) vs infra vs config. ZERO zmian. → `S28D_perf_peak.md`.

**Kolejność:** wszystko można równolegle (rozłączne pliki). Start od razu — zero bramek czasowych, zero ryzyka dla silnika.
