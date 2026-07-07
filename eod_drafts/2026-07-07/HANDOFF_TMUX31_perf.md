# HANDOFF — tmux 31 = WYKONAWCA: PERF „compute-zawsze" (szybkość silnika przed skalą)

**Jesteś wykonawcą.** Koordynator = główna sesja Claude (Adrian). **START:** `dispatch_v2/CLAUDE.md` (🧭 START TUTAJ + Przykazanie #0) → `docs/CODEMAP.md`. venv: `/root/.openclaw/venvs/dispatch/bin/python`. **READ FIRST:** `eod_drafts/2026-07-07/S28D_perf_peak.md` (diagnoza — Twój fundament) + `SPRINTY_27_28_PLAN.md`.

## KONTEKST (diagnoza 28-D, liczby [ZWERYFIKOWANE])
Koszt policzenia KAŻDEJ decyzji urósł od kwietnia: **p50 375→825 ms (2,2×), p95 624→1947 ms (3,1×)** — MIMO 2→4 vCPU. Problem to **NIE peak** — to strukturalny koszt bazowy „compute-zawsze": każdy kandydat = pełna symulacja trasy (OSRM `table` + OR-Tools/greedy, cap 200 ms/wywołanie, bag≥2, 10 workerów na 4 vCPU = oversubskrypcja ~2,5×). Skaluje z pulą: **pool 5→15 = +55% p50 (nadliniowo)**. Multi-city → większe pule → koszt eksploduje. Margines dziś wąski.

## ⛔ REGUŁY
- **ZERO flip / merge-do-master / restart / Telegram / flags.json bez ACK Adriana.** Worktree per zadanie, **commituj do gałęzi PRZED końcem**, sprzątaj po sobie. Fix u źródła. Regresja po LIŚCIE ID ([[feedback-worktree-shared-pkgroot-false-fails]]).
- **Twój zakres = SZYBKOŚĆ liczenia** (`feasibility_v2` filtr, `route_simulator` pruning symulacji, pętla oceny w `dispatch_pipeline`/`core/candidates`). NIE dotykaj: KOLEJNOŚCI trasy `route_podjazdy`/`plan_recheck`-render (**tmux 30**), WARTOŚCI ETA `calib_maps`/`eta_*` (**tmux 29**).
- **⚠ PUNKT STYKU z tmux 29/kalibracją:** jeśli musisz wejść w `core/candidates`, ruszasz TYLKO logikę *które* kandydaty liczyć (pruning/kolejność oceny) — **NIGDY wartości ETA** ani hooka kalibracji. Jeśli granica niejasna → pytaj koordynatora, nie zgaduj.

## ZADANIE (protokół #0; „warto = szybciej", „bez regresji = TA SAMA decyzja")
1. **ETAP 0 badawczy:** zmapuj pętlę oceny kandydatów (`dispatch_pipeline` → `feasibility_v2` → `route_simulator_v2`). Ustal empirycznie [liczbami], gdzie idzie czas: ile kandydatów, ile pełnych symulacji, ile wywołań OR-Tools, koszt `table` O(n²). Potwierdź diagnozę 28-D własnym pomiarem.
2. **Zaprojektuj pruning (dźwignia #1 z 28-D):** tani filtr feasibility/dominacji PRZED pełną symulacją trasy — odsiej kandydatów oczywiście gorszych (np. zdominowanych geometrycznie / poza zasięgiem / z góry przegrywających), żeby NIE liczyć im pełnego OR-Tools. Węższy zbiór feasible → mniej wywołań 200 ms.
3. **Zbuduj za flagą OFF** (`ENABLE_PERF_CANDIDATE_PRUNING` lub podobnie), OFF=zachowanie bit-identyczne.
4. **DOWÓD (kluczowy, protokół #0 ETAP 5):** replay ON vs OFF na żywym korpusie → (a) **decyzja IDENTYCZNA** (best candidate = ten sam; pruning NIE MOŻE zmienić wyniku, tylko przyspieszyć — inaczej to regresja jakości, NIE optymalizacja); (b) **latencja niższa** (zmierz p50/p95 ON vs OFF). Bez (a) = STOP.
5. Pełna regresja + e2e.

**DoD:** moduł pruningu za flagą OFF + dowód „decyzja identyczna ∧ latencja niższa" (liczby) w worktree, ZERO merge/flip. Raport → `eod_drafts/2026-07-07/S31_perf_pruning.md`. Zgłoś „GOTOWE do ACK" gdy dowód twardy. Jeśli pruning okaże się zmieniać decyzje (nie da się bezpiecznie) — zgłoś to wprost, nie forsuj.