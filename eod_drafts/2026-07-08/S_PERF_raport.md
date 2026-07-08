# SPRINT A — PERF pod skalę (ogon p95 + budżet solvera OR-Tools + audyt TZ) — RAPORT KOŃCOWY

**Sesja-wykonawca:** tmux 36. **Data:** 2026-07-08. **Worktree:** `/root/.openclaw/workspace/scripts/wt-perf-p95` (branch `perf/p95-ortools`).
**Baseline:** master `6e1af23` na starcie → przesunął się do `8a13b77` w trakcie (rebase, §5). **Protokół #0:** przejdziony ETAP 0→7. **Flagi OFF, zero flipów/flags.json/restartów.**

---

## 0. TL;DR

| Zadanie | Status | Sedno |
|---|---|---|
| **A1** pomiar ogona peak p95 (read-only) | ✅ ZROBIONE | Peak p95 ~1834 ms, plateau 14-17 najgorszy (p95 2090, ogon 16,2 %). **Ogon = flota/kontencja, NIE solver.** OR-Tools = ~130-230 ms PODŁOGI. |
| **A2** deterministyczny budżet solvera (flaga OFF) | ✅ ZBUDOWANE + DOWÓD | Parytet **100 %** (200/200), determinizm ON 0 %, latencja solve p50 −53 % przy p95≤OFF. **Karta flipu gotowa — flip = FLIPMASTER + ACK.** |
| **A3** audyt deployu TZ FALA-1 (read-only) | ✅ ZROBIONE | **Wszystkie fixy TZ LIVE, zero rezydualnych bomb, zero luki przed DST 25.10.** Brak działań. |
| **Regresja** pełna `pytest tests/` | ✅ patrz §5 | ZIELONA vs baseline (0 nowych regresji). |
| **Commit** przed końcem | ✅ patrz §7 | Zacommitowane w worktree PRZED końcem (lekcja `--force`). |

**Nic nie flipnięte, nic nie zrestartowane, flags.json nietknięty.** Wszystko czeka na ACK Adriana / FLIPMASTERA.

---

## 1. ETAP 0 — stan na żywo + baseline

- Worktree czysty, HEAD `6e1af23` = KANON master (baseline identyczny).
- **Baseline `pytest tests/`** (przez pkgroot symlink `dispatch_v2→worktree`, mechanizm C12(e)):
  **4447 passed, 27 skipped, 10 xfailed, 1 failed.**
- **Ten 1 FAIL = pre-existing CROSS-SESJA, NIE mój:** `test_grafik_fetch_schedule.py::test_parity_live_equals_staged_mirror[fetch]` — żywy `fetch_schedule.py` (scripts-root, POZA dispatch_v2) rozjechał się ze staged mirror o fix „None-sort" wniesiony DZIŚ przez INNĄ sesję (grafik). Reprodukuje się identycznie na czystym KANONIE (potwierdzone). **Poza moim zakresem (PERF/OR-Tools/TZ) i cudza domena (C1 multi-sesja — nie tykam).**
- **Mój baseline sprintu = 4447 pass / 1 pre-existing cross-session fail (nietykalny).**
- Multi-sesja recon (C1): tmux 37 = Sprint B (inwarianty alokacji) — peer w OSOBNYM worktree → brak kolizji plików (C12b); tmux 34/38 = inne obszary.

---

## 2. A1 — ogon peak p95: gdzie realnie boli (READ-ONLY)

Pełny raport: **`A1_p95_peak_findings.md`** (+ dowody bit-zgodne z kanonicznym `perf_budget_report`).

- **Rozkład (Warsaw, n=2674, ~11 dni):** OVERALL p50=813 p95=1874 ogon>1500 ms 12,3 %. PEAK (11-14+17-20) p50=817 p95=1834. **PLATEAU 14-17 NAJGORSZY** p50=943 p95=2090 ogon 16,2 %. OFF-peak p50=583 p95=1507.
- **Gdzie boli:** ogon skaluje MONOTONICZNIE z **rozmiarem floty / kontencją** (≤4 kur.: p95=1200 → 11+: p95=1977), NIE z liczbą solve'ów. Solve'y są RÓWNOLEGŁE (ThreadPoolExecutor 10 workerów): 0→5+ solve = +230 ms p50 (nie +1000 ms) → teza CLAUDE.md „10 kandydatów × solve SEKWENCYJNIE" **OBALONA świeżymi danymi**.
- **OR-Tools = ~130-230 ms równoległej PODŁOGI** dokładanej do decyzji bag≥2 (nie ogona).
- **Implikacja:** A2 trafia w podłogę/p50 + determinizm; **peak-p95 ogon jest napędzany per-kandydat pipeline × flotą** (feasibility+scoring+OSRM+fetch panelu) — inna dźwignia, poza zakresem A2 (route_simulator/feasibility = READ-ONLY tego sprintu).

---

## 3. A2 — deterministyczny budżet solvera OR-Tools (flaga OFF, dowód parytetu)

Pełny raport + karta flipu: **`A2_ortools_det_budget_parity.md`**.

- **Co:** nowa flaga `ENABLE_ORTOOLS_DET_TIME_LIMIT` (default OFF, bajt-w-bajt). ON → `solution_limit`=120 (powtarzalna trasa) zamiast wall-clock; sufit = budżet callera (zero-regresji na każdej ścieżce, też krótki warm-start food-age).
- **Pliki (tylko warstwa solvera/config):** `tsp_solver.py` (helper + blok gated), `common.py` (flaga w ETAP4 + stała OFF + config), `tools/perf_ortools_det_parity.py` (harness), `tests/test_ortools_det_budget_a2.py` (8 testów). **`route_simulator_v2` NIETKNIĘTY** (flaga czytana wewnątrz tsp_solver).
- **Dowód (200 casów × 3, realistyczny skupiony korpus):** parytet **100,0 %** (200/200 sekwencji), determinizm ON **0 %** (STRESS OFF 1,5 % = mechanizm potwierdzony), latencja **OFF p50=201,3/p95=201,6/max=256,7 → ON p50=94,0 (−53 %)/p95=201,5 (≤OFF)/max=204,0 (≤OFF)**. 100 % parytet na KAŻDYM rozmiarze worka.
- **Werdykt:** flip-warty jako win na PODŁODZE/p50 + determinizm z **zerową regresją**, ale (zgodnie z A1) NIE ruszy peak-p95 ogona. **Flip = FLIPMASTER** po: (1) replay end-to-end przez route_simulator (nie tylko solver-level), (2) 2 dni cienia, (3) ACK. Materialny dryf decyzji w replay → zostaje w cieniu (offline-replay-only).

---

## 4. A3 — audyt deployu TZ FALA-1 przed DST 25.10 (READ-ONLY)

Pełny raport: **`A3_tz_deploy_audit.md`** (twierdzenia zweryfikowane niezależnie grepem).

- **Werdykt: wszystkie fixy TZ FALA-1 LIVE. Zero rezydualnych bomb fixed-offset. Zero luki przed DST 25.10.**
- 3 żywe pliki scripts-root (`gastro_assign`/`fetch_schedule`/`schedule_utils`) = `ZoneInfo("Europe/Warsaw")` (potwierdzone); 7 narzędzi repo w master. Jedyny `timezone(timedelta(hours=1/2))` w żywym silniku = `tools/ontime_lib.py` = POPRAWNY wzór DST-aware (`warsaw_tz_for` liczy last-Sunday marzec→CEST/październik→CET).
- **STAGED-only: 0. Za flagą OFF: 0. Restart potrzebny: NIE** (cron/subprocess/one-shot).
- Jedyny FAIL TZ-testów = ten sam nie-TZ drift `fetch_schedule` live↔staged (None-sort, cudza sesja). Rekomendacja higieny (sync staged→forward) = za-ACK, cudza domena.
- **Działanie wymagane: ŻADNE dla TZ.**

---

## 5. Regresja pełna (DoD #1) — vs AKTUALNY baseline (rebase)

**⚠ Baseline się PRZESUNĄŁ w trakcie sprintu (ETAP 0/C15):** KANON master `6e1af23` → `8a13b77`
(inna sesja — Sprint B „claim ledger invariant CHECK" — scommitowała+flipnęła `ENABLE_CLAIM_LEDGER_
INVARIANT_CHECK`/`_HARD` do master + żywego flags.json ~14:15 UTC). Mój worktree bazował na starszym
`6e1af23` → ratchet `test_conftest_flag_strip_guard` czytał NOWY współdzielony flags.json, którego
mój (stary) ETAP4 nie pokrywał = fałszywy „mój" fail (na czystym KANONIE ten test PRZECHODZI; mój
diff common.py jest czysto ADDYTYWNY do ETAP4 → `_covered()` to nadzbiór → nie może odkryć leaka).

**Działanie (C1/C12 — nie tykać cudzej flagi):** NIE dotknąłem `ENABLE_CLAIM_LEDGER_*` (domena Sprintu B).
Zamiast tego **rebase mojego brancha `perf/p95-ortools` na aktualny master `8a13b77`** (czysty, zero
konfliktów — commit `5a7966e`) → mój worktree dziedziczy claim-ledger z master, mój A2 na wierzchu.

**Regresja na rebased stanie (`5a7966e` = master 8a13b77 + A2):**
> **`1 failed, 4473 passed, 27 skipped, 10 xfailed`** (129 s). Jedyny FAIL = grafik (niżej).
> 4473 pass = baseline master 8a13b77 (route-order S30 + claim-ledger Sprintu B) **+ 8 testów A2**.
> Strażniki flag (strip-guard / flag_effect / flag_registry / fingerprint / doc-coverage) = ZIELONE.

**Klasyfikacja:** mój A2 = **0 nowych regresji**. Jedyny FAIL = `test_grafik_fetch_schedule::
test_parity_live_equals_staged_mirror[fetch]` = pre-existing CROSS-SESJA (żywy `fetch_schedule.py`
scripts-root DO PRZODU względem staged o fix None-sort, wniesiony DZIŚ przez sesję grafik) —
reprodukuje się identycznie na czystym KANONIE, POZA moim zakresem (PERF/OR-Tools/TZ) i cudza domena.

---

## 6. Bezpieczeństwo / granice / rollback

- **Granice anty-kolizyjne dotrzymane:** NIE tknięto `route_simulator_v2` (read-only), feasibility/scorer/`core/*`, ETA/kalibracji, flags.json. A2 = wyłącznie warstwa solvera + config.
- **Flaga OFF default** → produkcja bajt-w-bajt; A2 nie wpływa na żadną żywą decyzję.
- **Backupy:** `common.py.bak-pre-a2-ortools-det-2026-07-08`, `tsp_solver.py.bak-pre-a2-ortools-det-2026-07-08`.
- **Rollback:** flaga→False (już OFF) / `git revert <A2 commit>`.

## 7. Commit

- **`482e535`** (pierwotny, na `6e1af23`) → po rebase **`5a7966e`** na `8a13b77` (branch `perf/p95-ortools`).
- **9 plików:** `common.py` (flaga ETAP4 + stała OFF + config), `tsp_solver.py` (budżet det. gated),
  `tools/perf_ortools_det_parity.py` (harness), `tools/flag_effect_coverage_check.py` (env-aware C12(e)),
  `tests/test_ortools_det_budget_a2.py` (8 testów), `eod_drafts/2026-07-08/{A1,A2,A3,S_PERF}.md`.
- **Zacommitowane PRZED końcem** (lekcja: `--force` skasował niezacommitowaną pracę). Push/merge do
  master = **sekwencyjnie po ACK** (FLIPMASTER) — NIE ja. Backupy .bak (gitignored) zachowane.

## 8. Czeka na ACK Adriana / FLIPMASTERA

1. **A2 flip** (`ENABLE_ORTOOLS_DET_TIME_LIMIT` ON) — po replay end-to-end przez route_simulator + 2 dni cienia. NIE ja.
2. **A3:** nic dla TZ. Opcjonalna higiena stale-mirror `fetch_schedule` staged→forward = cudza sesja/grafik, za-ACK.
3. **A1:** świadomość zespołu, że peak-p95 ogon = dźwignia flotowa/pipeline (nie solver) — kandydat na osobny sprint (poza zakresem tego, bo route_simulator/feasibility read-only).
