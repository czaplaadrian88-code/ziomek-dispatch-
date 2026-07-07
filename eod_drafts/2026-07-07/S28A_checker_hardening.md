# S28-A — Hardening checkerów/skanerów na sąsiedni worktree (ZERO silnika)

**Data:** 2026-07-07 · **Wykonawca:** tmux 28 · **Gałąź:** `s28a-checker-hardening` (worktree `wt-s28a`, od `master@d808808`) · **Commit:** `cfc1c6b`
**Zakres:** wyłącznie narzędzia/testy (checkery + skanery). ZERO zmian silnika, flag, restartów. **Gotowe do merge.**

## Problem (korzeń #2/#3 — [[feedback-worktree-shared-pkgroot-false-fails]])
Gdy pod repo żyje worktree sąsiedniej sesji (`.claude/worktrees/agent-*`, ADR-007), 3 skanery repo liczyły jego kopie plików jako duplikaty/offenderów → **fałszywy VETO/FAILED**; a 2 testy z `parents[2]`-hardcode padały gdy uruchamiane Z worktree (głębokość `.claude/worktrees/agent-X/tests/` → `parents[2]` mija `scripts/`). To źródło ~25 „faili worktree", które B1/B2 musieli za każdym razem tłumaczyć jako artefakt.

## Repro (na kanonie, PRZED fixem) — wszystkie 3 skanery false-fail
Utworzono sztuczny `.claude/worktrees/agent-synthetic-s28a/{common.py, plan_recheck.py, dispatch_pipeline.py, tools/legacy_tz.py}`:
| Checker | Wynik PRZED |
|---|---|
| `tools/canon_static_check.py` | ⛔ 2× RATCHET VETO (`.claude/.../common.py`, `.../plan_recheck.py`) |
| `test_decide_facade_k09::test_no_direct_assess_order_callsites_outside_facade` | FAILED (`.claude/.../dispatch_pipeline.py:1` offender) |
| `test_tz_zoneinfo_consolidation::test_ratchet_no_new_fixed_offset_tz` | FAILED (`.claude/.../tools/legacy_tz.py`) |

## Fix u źródła (5 plików)
1. **`tools/canon_static_check.py`** — `.claude` dodane do `_EXCLUDE_DIRS` (load_sources pomija zagnieżdżone worktree).
2. **`tests/test_decide_facade_k09.py`** — skan callsite'ów assess_order pomija `.claude/`; skan wyekstrahowany do `_scan_direct_assess_callsites(root)` (testowalny).
3. **`tests/test_tz_zoneinfo_consolidation.py`** — `_scan_fixed_offset(root=None)` pomija `.claude` w `os.walk`; `rel` liczony względem `root` (parametryzowalne).
4. **`tests/test_courier_reliability.py`** + 5. **`tests/test_a2_selection_shadow.py`** — `REPO = os.environ.get("ZIOMEK_SCRIPTS_ROOT", parents[2])` (lustro `conftest._SCRIPTS_ROOT`); harness worktree ustawia ZIOMEK_SCRIPTS_ROOT=pkgroot (symlink→worktree) → `MODULE_PATH` rozwiązuje się poprawnie.

## Dowody (DoD: „przechodzą NAWET z aktywnym sąsiednim worktree")
- **3 skanery z FIXEM + realny `.claude/worktrees/agent-*` obecny → wszystkie PASS** (canon_static_check exit 0; 2 testy 2 passed).
- **parytet `parents[2]` przed/po** (symulacja głębokości worktree):
  - STARE `parents[2]` = `…/.claude/worktrees` → MODULE_PATH istnieje? **False** (= SkipTest→FAILED, korzeń 25 faili).
  - NOWE (env) = `…/scripts` → MODULE_PATH istnieje? **True** (naprawione).
- **+3 durable regression-guardy** (mutation-probe RED każdy):
  - `test_ratchet_ignores_adjacent_claude_worktree` (canon: `.claude` pominięte, ale DRUGA definicja pod NIE-wykluczoną ścieżką `feasibility_v2.py` NADAL VETO).
  - `test_scan_ignores_adjacent_claude_worktree` (facade + tz: `.claude` pominięte, realny nowy offender NADAL łapany).
- **Pełna regresja `pytest tests/` (wt-s28a, pkgroot pełny): 4432 passed / 0 failed** (baseline kanon 4429 + 3 nowe guardy), 27 skipped, 8 xfailed, 2 xpassed. ⚠ Wcześniejszy run z NIEPEŁNYM pkgroot dał 6 „faili" (`test_conftest_flag_strip_guard` ×3 + 3 script-runnery) — **artefakt braku `flags.json` na poziomie pkgroot; po dopełnieniu pkgroot 6/6 PASS** (nie mój kod: baseline kanon = 0 faili).

## Wpływ
Przyszłe sprinty w worktree nie generują szumu 25 faili → regresja czytelna od razu (PRE vs POST po LIŚCIE ID). Lekcja `.claude`-exclusion zastosowana też w S28-B (ratchet producentów).

## Rollback
`git revert cfc1c6b` (same testy/narzędzia — zero ryzyka runtime). Backup: gałąź niezmergowana.
