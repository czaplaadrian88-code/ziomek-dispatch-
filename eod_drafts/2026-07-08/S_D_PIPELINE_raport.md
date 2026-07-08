# SPRINT D — Ogon peak-p95 z KONTENCJI pipeline'u decyzji (nie solver)

**Sesja-wykonawca tmux 39/40 · 2026-07-08 · worktree `wt-pipeline-p95` (branch `perf/pipeline-contention-p95`, z master `8a13b77`).**
**Werdykt: UCZCIWY NEGATYW w pasie orkiestracji + skwantyfikowana realna dźwignia (= A2 Sprintu A).**
**Nic nie flipnięte / zrestartowane / dotknięte na żywo. flags.json nietknięty. Zero edycji kodu silnika — tylko 2 nowe narzędzia `tools/perf_*` (read-only).**

---

## 0. TL;DR

- **ETAP 0:** baseline `pytest tests/` = **4465 passed, 1 pre-existing FAIL** (`test_grafik_fetch_schedule[fetch]` — udokumentowany w [[sprint-perf-p95-ortools-det-2026-07-08]] jako cudza domena grafik, pada też na kanonie, nie-TZ). Zielony.
- **D1 (profil, read-only):** ogon **NIE siedzi w „kontencji puli do rozładowania"** — pula ThreadPoolExecutor **daje ~zero realnej równoległości** (`eff_cores≈1.0` przy KAŻDYM rozmiarze floty; 3 z 4 vCPU bezczynne w decyzji). Ogon = **wall-budget solvera OR-Tools (200 ms GLS)**: solve to **92 % wall / 85 % CPU** decyzji, a GLS kręci się do sufitu 200 ms w większości bez poprawy. OSRM już w pełni zdeduplikowane w decyzji (**0 % redundancji**), OSRM I/O ~15 % ścieżki.
- **D2 (lewar):** **brak bezpiecznej, parytetowej, materialnej redukcji kontencji w pasie orkiestracji/współbieżności.** OSRM-prewarm = martwy (0 % redundancji). Cap puli = net-ujemny (solve'y są wall-limited → mniej workerów = MNIEJ nakładania = gorszy wall). Thread→Process = tylko **+4 %** nad wątkami (wątki już wyciągają równoległość solve C++ przez zwalnianie GIL). **Jedyna materialna dźwignia = skrócić wall-budget solvera** — to **A2 Sprintu A** (`ENABLE_ORTOOLS_DET_TIME_LIMIT` → solution_limit), plik `tsp_solver.py` = poza moim pasem.
- **Prezent dla Sprintu A:** moje niezależne pomiary **potwierdzają parytet A2 i rozszerzają jego wartość na OGON**: skrócenie wall-budgetu 200→50 ms tnie p95 decyzji **−45 %** (356→195 ms) przy **parytecie 40/40 decyzji** i **103/103 tras** identycznych. A2 to nie tylko podłoga/determinizm — to dźwignia peak-p95. **Rekomendacja: skoordynować flip A2 jako lewar ogona (za ACK Flipmastera).**

---

## 1. Metoda i uczciwość pomiaru

- Narzędzia (nowe, w tym worktree, **read-only wobec żywych serwisów**):
  - `tools/perf_pipeline_contention_probe.py` — tryby `scaling` / `osrm` / `solversplit` / `timelimit`.
  - `tools/perf_tsp_parallel_ceiling.py` — sufit równoległości solvera (thread vs process) + parytet tras.
- Baza = **ta sama metoda co A-team** (`perf_lazy_harness`): replay REALNYCH zdarzeń NEW_ORDER z `dispatch_state/events.db`, **deterministycznie-syntetyzowana flota** (md5-seed), realna fasada `decide()` (pełny pipeline + pula), **realny OSRM :5001**. `PYTHONHASHSEED=0`, `nice -19`.
- **Caveat (uczciwie):** flota SYNTETYCZNA z rozkładem worków głównie 0-2 (kilka bag≥2). Realny peak ma większe worki (A1: bag4+ = 18 % ogona) → w produkcji solve CZĘŚCIEJ dobija sufit 200 ms → udział solvera w ogonie jest **≥** mój pomiar. Wniosek „ogon = wall-budget solvera" jest przez to **konserwatywny** (w produkcji silniejszy), a dźwignia A2 **≥** to co zmierzyłem.
- **Zgodność z A1 (bez podważania):** A1 (live shadow log) słusznie ustalił „ogon koreluje z flotą/obciążeniem, NIE z LICZBĄ solve'ów, solver=podłoga". Mój pomiar jest KOMPLEMENTARNY, nie sprzeczny: pokazuje, że w obrębie tej korelacji **dominującym CIĘTYM składnikiem jest wall-budget solve'a** (większe worki dobijają 200 ms; więcej kurierów w peaku → więcej takich solve'ów + więcej GIL-serializowanego glue Python). A1 mierzył KORELACJĘ, ja mierzę CZUŁOŚĆ na wall-budget (parytet-exact) — dwie metody, jeden kierunek.

---

## 2. D1 — GDZIE siedzi ogon (dowody, nie deklaracje)

### 2a. Pula ThreadPoolExecutor NIE zrównolegla — `eff_cores≈1.0` (3 rdzenie bezczynne)
`scaling` (n=50, repeats=2, 4 vCPU); `eff_cores = process_time/wall` (1.0 = 1 rdzeń realnie pracuje):

| fleet | wall_p50 | wall_p95 | wall_mean | cpu/dec | **eff_cores** | par_eff% |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 37.7 | 249.5 | 108.1 | 111.1 | 1.03 | 102.7 |
| 4 | 223.6 | 396.9 | 205.6 | 217.8 | 1.06 | 26.5 |
| 8 | 268.8 | 485.4 | 297.5 | 304.1 | 1.02 | 25.6 |
| 10 | 272.5 | 503.7 | 307.9 | 327.4 | 1.06 | 26.6 |
| 13 | 416.3 | 585.8 | 392.3 | 391.3 | 1.00 | 24.9 |

**`cpu/dec ≈ wall_mean` przy każdym rozmiarze → decyzja zużywa ~1 z 4 rdzeni.** Wall rośnie ~liniowo z liczbą kandydatów (108→392 ms). To OBALA hipotezę „kontencja w puli do rozładowania" — nie ma równoległości do odzyskania w procesie: praca jest wall-bound/GIL-serializowana.

### 2b. Solver = 92 % wall (`solversplit`, fleet=10, in-proc toggle pomiarowy)
| tryb | wall_mean | cpu/dec |
|---|---:|---:|
| ORTOOLS **ON** | 287.9 | 303.8 |
| ORTOOLS **OFF** (greedy) | 23.0 | 45.3 |
| **Δ solver** | **264.9 (92 %)** | 258.5 (85 %) |

Bez OR-Tools decyzja 10-kandydatów = ~23 ms. **Ogon to niemal wyłącznie OR-Tools.**

### 2c. cProfile: dominują pythonowe CALLBACKI OR-Tools, nie sam solve C++
Top self-time (fleet=10, 80 decyzji): `RoutingIndexManager_IndexToNode` 7.70 s (20.9 M wyw.) · `tsp_solver.time_cb` 4.27 s (7.5 M) · `IndexToNode` wrapper 2.56 s · `tsp_solver.dist_cb` 1.68 s · **`SolveWithParameters` 0.94 s**. Czyli marshalling C++↔Python callbacków ≫ sam solve. GLS z sufitem 200 ms wykonuje MILIONY round-tripów callbacków (każdy trzyma GIL) — to praca CPU serializowana GIL-em.

### 2d. OSRM: 0 % redundancji, ~15 % ścieżki (`osrm`, fleet=10)
- HTTP round-tripów / COLD-decyzję: mean **23.6**, DISTINCT coord-keys **23.6** → **REDUNDANT = 0 %**. Table-cell + route cache (`ENABLE_OSRM_TABLE_CELL_CACHE=true`) JUŻ deduplikują między kandydatami.
- Δwall cold-warm = **~44 ms** (~15 % ścieżki krytycznej). OSRM I/O jest MNIEJSZOŚCIĄ.

### 2e. Sufit równoległości solvera + PARYTET (`perf_tsp_parallel_ceiling`, 103 realne solve'e)
| tryb | wall_ms | speedup vs seq | parytet tras |
|---|---:|---:|---:|
| sequential | 20759 | 1.00× | 103/103 |
| thread(4) | 5576 | 3.72× | 103/103 |
| process(4) | 5331 | 3.89× | 103/103 |

Krzywa worker-count (thread): 4→**3.70×**, 6→**5.15×**, 8→**6.47×**, 10→**6.95×** (process do 8.42×), parytet 102-104/104 wszędzie. **Więcej workerów = szybciej** (solve'y wall-time-limited → więcej nakładania w tym samym oknie wall). **Cap puli by ZASZKODZIŁ.** Sekwencyjnie każdy solve pali pełne 200 ms; przy współbieżności jest głodzony CPU, robi MNIEJ przeszukiwania — **ale trasa ta sama** (dodatkowe przeszukiwanie GLS nic nie znajduje). Wątki już wyciągają równoległość solve C++ (zwalnianie GIL) → **process daje tylko +4 %** nad wątkami.

### 2f. Czułość ogona na wall-budget solvera + PARYTET (`timelimit`, in-proc monkeypatch, NIE flip)
| tl_ms | wall_p50 | wall_p95 | **decyzja = baseline 200 ms** |
|---:|---:|---:|---:|
| 200 | 248.1 | 355.9 | 40/40 |
| 120 | 184.0 | 305.8 | 40/40 |
| 80 | 130.2 | 237.8 | 40/40 |
| 50 | 122.5 | **194.5** | **40/40** |

**Skrócenie wall-budgetu 200→50 ms: p95 −45 % (356→195), p50 −51 % (248→123), przy 40/40 IDENTYCZNYCH decyzjach** (serializacja `_serialize_result`, rekurencyjny strip pól czasowych). To DOWÓD: ogon = zmarnowane przeszukiwanie GLS do sufitu wall, którego ucięcie NIE zmienia decyzji.

---

## 3. D2 — LEWAR: uczciwy negatyw w pasie + realna dźwignia

**Mapa kandydatów-lewarów i werdykt (każdy oceniony pomiarem, nie „na oko"):**

| Lewar (pas orkiestracji/współbieżności) | Werdykt | Dowód |
|---|---|---|
| Pre-warm wspólnych legów OSRM przed pulą | ❌ MARTWY | redundancja OSRM = **0 %** (§2d) — nie ma czego deduplikować |
| Cap workerów puli (`min(10,fleet)`→mniej) | ❌ NET-UJEMNY | solve'y wall-limited → mniej workerów = mniej nakładania = **gorszy** wall (§2e) |
| Thread→Process pool per-kandydat | ❌ MARGINALNY + kosztowny | tylko **+4 %** nad wątkami na solve'ach (§2e); a w pełnym pipeline dochodzą pickle EvalContext, brak współdzielenia cache OSRM między procesami (redundancja rośnie), fork/decyzję, **niedeterminizm ortools wall-limit** — parytet nie-exact. Zysk << ryzyko/koszt architektury |
| Raise cap workerów (>10) | ❌ HACK | zysk pochodzi z GŁODZENIA solve'ów (mniej przeszukiwania) — to robi A2 czysto (solution_limit); podbijanie capu ryzykuje parytet na krawędzi dużych worków |
| Memoizacja glue Python między kandydatami | ❌ NIEMATERIALNY | nie-solver = 8 % wall (§2b), część redundantna jeszcze mniejsza; < progu 2 %; `drop_zone_from_address` w `common.py` (współdzielone, ryzyko) |
| **Skrócić wall-budget solvera (solution_limit)** | ✅ **REALNA (−45 % p95, parytet 40/40)** | §2f — ale to **A2 Sprintu A**, plik `tsp_solver.py` = **poza moim pasem** (gałąź niezmergowana, granica anty-kolizyjna) |

**Wniosek D2:** zgodnie z DoD handoffu („Jeśli lewar nieosiągalny bezpiecznie w tym pasie → uczciwy negatyw jak Sprint 31 + wskazanie realnej dźwigni"): **w pasie orkiestracji/współbieżności NIE MA bezpiecznej, parytetowej, ≥2 % materialnej redukcji kontencji.** Ogon nie jest „kontencją do rozładowania" — jest **GIL-serializowaną, wall-time-limited pracą solvera**, którą pula już maksymalnie nakłada. Prawdziwe dźwignie leżą poza pasem:

1. **A2 (`ENABLE_ORTOOLS_DET_TIME_LIMIT`, solution_limit zamiast wall-clock) — DŹWIGNIA #1 na ogon.** Sprint A zbudował ją i zwalidował parytet 100 % na poziomie solve. **Mój pomiar dodaje: to tnie tylko podłoga p50/determinizm — to −45 % p95 OGONA przy parytecie 40/40 decyzji.** Rekomendacja: potraktować A2 jako lewar peak-p95 i skoordynować flip (Flipmaster + ACK) po jej rebase/merge. Sprint D de-ryzykuje i re-priorytetyzuje A2.
2. **Równoległość PROCESOWA (architektura) — DŹWIGNIA #2, warunkowa.** Odzyskuje 3 bezczynne rdzenie na GIL-serializowanym glue Python, ale: (a) w pełnym pipeline tylko +4 % nad wątkami na solve'ach (§2e), (b) parytet nie-exact póki ortools ma wall-limit — **domyka się dopiero PO A2** (solution_limit → determinizm → proces staje się parytet-exact). Kolejność: **A2, potem ewentualnie procesy.**
3. **Więcej vCPU (CPX32→większy)** — liniowo na glue Python; najprostsze, ale najdroższe operacyjnie; realne dopiero przy multi-city/Warsaw.

---

## 4. DoD / zgodność z protokołem #0

- **(0)** stan na żywo + baseline zielony (4465/1-pre-existing) ✅ · multi-sesja recon (Sprint C tmux38 route_order, Sprint A tsp_solver — rozłączne; C1) ✅
- **(1)** źródło, nie objaw: ogon zlokalizowany U ŹRÓDŁA (wall-budget GLS w `tsp_solver`), nie „załatany na renderze" ✅
- **(3)** mapa kompletności lewarów: WSZYSTKIE kandydaty w pasie ocenione pomiarem (§3), nic „na wszelki wypadek" ✅
- **(4) dowody nie deklaracje:** parytet 103/103 tras + 40/40 decyzji; eff_cores; solversplit; OSRM 0 %; py_compile obu tooli ✅; **PEŁNA regresja `pytest tests/`** — patrz §5.
- **(5)** pozytywny wpływ: realna dźwignia skwantyfikowana (−45 % p95 parytet-exact), ale należy do A2/Sprintu A → NIE mój flip.
- **(6)** commit PRZED końcem (§5), zero restartu/flipu. **(7)** rollback: N/D (nic nie wdrożone; tylko nowe narzędzia read-only, `git revert`/usuń plik).
- **ZERO flipów bez ACK.** ✅

## 5. Artefakty i weryfikacja
- Nowe narzędzia (worktree `tools/`): `perf_pipeline_contention_probe.py`, `perf_tsp_parallel_ceiling.py` — read-only, nieimportowane przez żywy silnik.
- Reprodukcja (cwd=pkgroot z symlinkiem `dispatch_v2→worktree`, C12(e)/(g)):
  ```
  PYTHONHASHSEED=0 ZIOMEK_SCRIPTS_ROOT=$PKG python -m dispatch_v2.tools.perf_pipeline_contention_probe {scaling|osrm|solversplit|timelimit}
  PYTHONHASHSEED=0 ZIOMEK_SCRIPTS_ROOT=$PKG python -m dispatch_v2.tools.perf_tsp_parallel_ceiling --workers 4
  ```
- **Regresja (worktree, po dodaniu narzędzi):** `1 failed, 4465 passed, 27 skipped, 10 xfailed` — **IDENTYCZNA z baseline** (jedyny FAIL = pre-existing `test_grafik_fetch_schedule[fetch]`, cudza domena). **Zero regresji** od moich zmian (dodałem wyłącznie 2 read-only narzędzia + ten raport; żaden plik silnika nietknięty). ✅
- **Commit:** `perf/pipeline-contention-p95` — 2 narzędzia + raport (jawne ścieżki, Co-Authored-By). Nic do merge poza raportem/toolami; **żaden flip nie należy do tego sprintu** (A2 = Sprint A/Flipmaster).
