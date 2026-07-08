# A2 — Deterministyczny budżet solvera OR-Tools: parytet, determinizm, latencja + KARTA FLIPU

**Wykonawca:** tmux 36 (sprint PERF p95), 2026-07-08. **Worktree:** `wt-perf-p95` (branch `perf/p95-ortools`).
**Status:** flaga OFF (produkcja bajt-w-bajt), dowód parytetu ZEBRANY, **flip = FLIPMASTER + ACK Adriana** (NIE ja).

---

## 1. Co zrobione (u źródła, warstwa configu solvera — route_simulator NIETKNIĘTY)

Wprowadzono **deterministyczny budżet solvera** za nową flagą `ENABLE_ORTOOLS_DET_TIME_LIMIT`
(default **OFF**, bajt-w-bajt z dziś). Motyw (tmux 31): wall-clock `time_limit` (200 ms) daje
liczbę iteracji GLS zależną od obciążenia CPU → **ta sama sytuacja może dać inną trasę**
(~1,7 % podłogi niedeterminizmu replayu). ON → `solution_limit` (stała liczba rozwiązań GLS =
powtarzalna trasa); sufit wall-clock = **budżet callera** (zero-regresji, patrz §4).

**Pliki (tylko warstwa solvera / config — granice anty-kolizyjne dotrzymane):**
- `tsp_solver.py` — helper `_ortools_det_budget()` (lazy-import common, fail-soft None) + blok
  gated PO ustawieniu `time_limit`, PRZED warm-start `CloseModelWithParameters`. OFF = blok nie
  rusza params.
- `common.py` — flaga w `ETAP4_DECISION_FLAGS` (cross-proces fingerprint + izolacja conftest) +
  stała-fallback OFF + config `ORTOOLS_DET_SOLUTION_LIMIT=120` / `ORTOOLS_DET_WALL_CEILING_MS=0`.
- `tools/perf_ortools_det_parity.py` — harness pomiarowy (READ-ONLY).
- `tests/test_ortools_det_budget_a2.py` — 8 testów (flaga ON≠OFF, OFF bez zmian, ON determinizm,
  solution_limit realnie bite, HARD pickup-przed-drop trzymane).
- `tools/flag_effect_coverage_check.py` — checker higieny flag uczyniony `ZIOMEK_SCRIPTS_ROOT`-aware
  (default=KANON, byte-identyczny dla CI; env=worktree). Bez tego skanował KANON `tests/` (bez
  testu A2 z worktree) i fałszywie zgłaszał nową flagę jako „bez testu efektu" — klasa C12(e)
  (kod z worktree, testy z KANONU), spójne z tym jak conftest już rozwiązuje ten rozjazd.

**Kluczowe ograniczenie uszanowane:** `route_simulator_v2.py` (caller) = READ-ONLY. Flaga czytana
WEWNĄTRZ `tsp_solver` (`decision_flag`), nie przez nowy argument callera → route_simulator bez zmian.

---

## 2. WYNIK — parytet + determinizm + latencja (harness `perf_ortools_det_parity`)

Korpus: realistyczne PDP w skali Białegostoku (geometria SKUPIONA respektująca R1 spread≤8 km /
R5 pickup≤1,8 km — inaczej korpus mierzy patologie, które feasibility HARD-odrzuca zanim trafią
do solvera), rozmiary worka 2..8, połowa z oknami odbioru + twardym SLA 35 min. Seedowany
(powtarzalny). Budżet produkcyjny 200 ms (= dzisiejszy `V326_OR_TOOLS_TIME_LIMIT_MS`).

**Konfiguracja produkcyjna (sufit=budżet callera 200 ms, `ORTOOLS_DET_WALL_CEILING_MS=0`):**

Bieg definitywny: **200 casów × 3 powtórzenia**, sufit=budżet callera (`ceiling=0`), seed 20260708.

| Metryka | OFF (dziś) | ON (A2) | Werdykt |
|---|---|---|---|
| Parytet sekwencji ON↔OFF | — | **100,0 %** (200/200) | ✅ ta sama trasa |
| Determinizm run-to-run (200 ms) | 0 % niedet. | **0 %** niedet. | ✅ |
| Determinizm STRESS (5 ms wall-clock) | **1,5 %** niedet. (3/200) | **0 %** | ✅ mechanizm potwierdzony |
| Latencja solve p50 | **201,3 ms** | **94,0 ms** (−53 %) | ✅ podłoga cięta |
| Latencja solve p95 | 201,6 ms | **201,5 ms** (≤ OFF) | ✅ ZERO regresji ogona |
| Latencja solve max | 256,7 ms | **204,0 ms** (≤ OFF) | ✅ ciaśniejszy |

Per rozmiar worka: **100 % parytet na KAŻDYM** (bag 2..8). Bagi 2-6 (dominują w realu) — ON dużo
szybszy (27-150 ms vs 201 ms). Bagi 7-8 (rzadkie) — ON ~201 ms = cięte wall-clockiem IDENTYCZNIE
jak OFF (solution_limit nie zdąża → bajt-w-bajt).

> ⚠ **Dlaczego OFF ~201 ms KAŻDE wywołanie:** GLS nie dowodzi optymalności → przepala pełny budżet
> (potwierdza CLAUDE.md + pomiar A1). To właśnie tę „przepaloną" podłogę ON zdejmuje dla łatwych worków.

---

## 3. Powiązanie z A1 (gdzie realnie boli) — UCZCIWY zasięg A2

A1 (pomiar peak p95, READ-ONLY) wykazał: **peak p95 ogon (~1834-2090 ms) NIE jest napędzany
solverem** — skaluje z rozmiarem floty/kontencją (≤4 kur.: p95=1200 → 11+: p95=1977), a solve'y
są RÓWNOLEGŁE (0→5+ solve = +230 ms p50, nie +1000 ms — „sekwencyjnie" z CLAUDE.md OBALONE).
OR-Tools dokłada **~130-230 ms równoległej PODŁOGI** do decyzji bag≥2.

**Konsekwencja:** A2 trafia w **podłogę/p50 + determinizm**, a **NIE w peak-p95 ogon**. Zysk realny
(gdyby wyzerować solver, decyzja-p50 ~813→~694 ms = −15 %; A2 zdejmuje część tej podłogi + daje
przewidywalność), ale zespół MUSI wiedzieć: **dźwignia na peak-p95 ogon jest gdzie indziej**
(per-kandydat pipeline × flota: feasibility+scoring+OSRM+fetch panelu — poza zakresem A2, w tym
route_simulator/feasibility = READ-ONLY dla tego sprintu). Szczegóły: `A1_p95_peak_findings.md`.

---

## 4. Dlaczego sufit = budżet callera (a nie sztywne 200/30000)

`time_limit_ms` docierający do solvera jest ZMIENNY: warm-startowany solve food-age dostaje
KRÓTSZY budżet (`OBJ_FOOD_AGE_HARD_SLA_ON_SOLVE_MS`=100 ms, route_simulator:1573). Sztywny sufit
podniósłby ten krótki budżet → **regres na ścieżce warm-start**. Dlatego:
- `ORTOOLS_DET_WALL_CEILING_MS = 0` (default) = **ZOSTAW budżet callera** jako sufit → ON ≤ OFF
  latencja na KAŻDEJ ścieżce (też krótki warm-start); hard-bag cięty wall-clockiem IDENTYCZNIE
  jak OFF = bajt-w-bajt. **Produkcja: zero-regresji z konstrukcji.**
- `>0` (np. 30000) = tryb **OFFLINE-REPLAY determinism-first** (pełne wiązanie solution_limit dla
  determinizmu także hard-worków). ⚠ **NIE dla produkcji** — zmierzono: sufit 30 s → latencja
  bag 7-8 do **~24 s** (solution_limit=120 nie wiąże pod twardym SLA, sufit prawie się aktywuje).
  Ten tryb służy WYŁĄCZNIE reprodukowalnym dowodom parytetu w narzędziach replay (motyw tmux 31).

Sweep sufitu (120 casów): sufit 250 ms → parytet 100 % ale ON p95=251 ms (+50 ms ogona vs OFF 201);
sufit 200 ms/budżet callera → parytet 100 %, ON p95 ≤ OFF (zero-regresji). **Wybrany: budżet callera.**

---

## 5. KARTA FLIPU (dla FLIPMASTERA — NIE flipuj sam)

**Flaga:** `ENABLE_ORTOOLS_DET_TIME_LIMIT` (dziś OFF, poza flags.json → efektywnie OFF przez stałą).
**Co robi ON:** solver zatrzymuje się po `solution_limit`=120 rozwiązaniach GLS (powtarzalna trasa),
sufit = budżet callera (zero-regresji).

**Dowód POZYTYWNEGO wpływu (ETAP 5):**
- ✅ **Parytet decyzji 100 %** (200/200 sekwencji identycznych, wszystkie rozmiary worka) — „ta sama decyzja".
- ✅ **Latencja solve: p50 −53 % (201,3→94,0 ms), p95 201,5≤201,6 OFF, max 204≤257 OFF** — pozytyw, zero regresji ogona.
- ✅ **Determinizm run-to-run 0 %** (ON) + mechanizm potwierdzony (STRESS OFF niedet. > 0, ON=0).
- ✅ Pełna regresja Ziomka ZIELONA vs baseline (patrz `S_PERF_raport.md`).

**Zanim flip (protokół #0, warunki Adriana):**
1. **Replay na ŻYWYM korpusie przez route_simulator** (nie tylko solver-level synthetic) —
   potwierdzić parytet decyzji end-to-end (pełny pipeline: feasibility→scoring→selekcja) na
   realnych workach z okna. Mój harness mierzy WARSTWĘ SOLVERA; pełny pipeline to następny krok.
2. **Okno 2 dni w cieniu** (flaga ON w SHADOW/replay-tooling, nie produkcja) — potwierdzić brak
   dryfu decyzji na żywym strumieniu.
3. **C2/C3 przy flipie:** flaga sprzężona z `ENABLE_V326_OR_TOOLS_TSP` (OR-Tools musi być ON, jest)
   — solution_limit nie ma sensu bez solvera. Rollback OR-Tools OFF ⇒ A2 automatycznie martwa.
4. Deploy = 1 restart `dispatch-shadow` off-peak, ACK Adriana, rollback = flaga→False (hot-reload) /
   `git revert`. NIGDY telegram/peak bez OK.

**Werdykt wykonawcy:** A2 jest **flip-warty jako win na PODŁODZE/p50 + determinizm z zerową
regresją**, ALE — zgodnie z A1 — **NIE rozwiąże peak-p95 ogona** (inna dźwignia). Rekomendacja:
flip po pełnym replay przez route_simulator + 2 dni cienia (FLIPMASTER). Jeśli replay end-to-end
pokaże JAKIKOLWIEK materialny dryf decyzji → zostaje w cieniu (offline-replay determinism-only).

---

## 6. Rollback

- Flaga: `ENABLE_ORTOOLS_DET_TIME_LIMIT` już OFF (default). Hot-reload przez flags.json gdyby kiedyś ON.
- Kod: backupy `common.py.bak-pre-a2-ortools-det-2026-07-08`, `tsp_solver.py.bak-pre-a2-ortools-det-2026-07-08`;
  albo `git revert <commit A2>`.

## 7. Odtworzenie dowodu

```bash
PKG=<pkgroot z dispatch_v2→worktree, flags.json symlink>
ZIOMEK_SCRIPTS_ROOT=$PKG PYTHONPATH=$PKG /root/.openclaw/venvs/dispatch/bin/python \
  -m dispatch_v2.tools.perf_ortools_det_parity --cases 200 --repeats 3      # produkcja (sufit=budżet callera)
#   --ceiling 30000  → tryb offline-replay determinism-first (pokazuje blow-up latencji hard-worków)
```
