# L8 iteracja 2 — raport (pas C: kasacja martwego C4 speed-tier + flaga PLANNED)

**Data:** 2026-07-02/03 · **Branch:** `fix/l8-iter2` (worktree `/root/.openclaw/workspace/wt-l8-iter2`) · **Charakter:** build-only, chirurgiczne kasowanie udowodnionego martwego kodu. ZERO flipów/restartów/push. Wznowienie przerwanego pasa (poprzednik padł na limicie ~23:16); re-weryfikacja dowodów wykonana OD ZERA.

## Cel (3 targety)
1. `speed_tier_tracker.py` (211 LOC) — C4 standalone „nightly", martwy odpowiednik żywego `tools.build_speed_tiers`.
2. `tests/test_speed_tier_tracker.py` (134 LOC, 9 testów) — osierocony test celu #1.
3. flaga `ENABLE_SPEED_TIER_LOADING_PLANNED` (`common.py`, 6 linii z komentarzem C4).

**Wynik:** #1+#2+#3 USUNIĘTE (345 LOC + 6 linii flagi). **`sprint2_analysis/` = STOP (NIE martwy)** — patrz niżej.

## ETAP 0 — re-weryfikacja dowodów OD ZERA (poprzednika nie zaufano)

### speed_tier_tracker.py — martwy, potwierdzony
- **Importery repo + `scripts/*.py` + testy:** ZERO. Jedyne trafienia po `speed_tier_tracker`:
  - `common.py` (flaga — komentarz „C4: speed_tier_tracker.py produces...", kasowany razem),
  - `eod_drafts/2026-06-13/regen_tier_stats.py` — **tylko komentarze** („wierna oryginałowi speed_tier_tracker.py", „jak speed_tier_tracker.BUNDLE_GAP_MIN"); `grep -E '^\s*(import|from).*speed_tier_tracker'` = pusto. Osobna reimplementacja, NIE importer.
  - `scripts/ml_data_prep/src/_dispatch_common_snapshot.py:358` — komentarz (zamrożony snapshot common.py), NIE import.
  - reszta = pliki `.md` (TECH_DEBT/CLAUDE/AUDIT/HANDOVER/ARCHITECTURE_SPEC) — dokumentacja.
- **systemd:** brak `speed_tier_tracker` w ExecStart (`grep /etc/systemd/system` = pusto).
- **Żywa ścieżka nocna:** `crontab` 04:25 `python -m dispatch_v2.tools.build_speed_tiers` — i `build_speed_tiers.py` **NIE importuje** `speed_tier_tracker` (`grep -E '^\s*(import|from).*speed_tier_tracker'` = pusto). Artefakt `courier_speed_tiers.json` robi wyłącznie żywa ścieżka. POTWIERDZONE.
- **atq (200-206):** żaden job nie referuje modułu. **subprocess/`-m` w workspace:** brak wywołań (same komentarze/docs).
- **Test:** `tests/test_speed_tier_tracker.py` = dedykowany osierocony test celu (9 testów) → kasowany razem u źródła.

### flaga ENABLE_SPEED_TIER_LOADING_PLANNED — martwa
- **Czytelnicy kodu:** ZERO poza własną definicją `common.py:1126`. Reszta trafień = docs (`CLAUDE.md`, `AUDIT_2026-06-03`, `TECH_DEBT.md`, `docs/TECH_DEBT.md`) — historyczne, zostawione.
- **flags.json:** NIE ma jej (grep „SPEED_TIER" → tylko `ENABLE_DRIVE_SPEED_TIER_CORRECTION` = INNA flaga). Brak w flags.json → wolno usunąć.
- **tools/flag_registry.py:** NIE ma jej. Registry NIE edytowany (partycja pasa L0.1). **Do synchronizacji przy merge = NIC** (flagi w rejestrze nie było, więc żaden wpis do zdjęcia). Odnotowane dla koordynatora.

### sprint2_analysis/ (7 plików) — **STOP, NIE martwy**
- `event_bus.py:79` = komentarz; `tools/retro_learning.py:352/468` = lokalna funkcja `a5_override_patterns` (kolizja substringu, NIE import).
- **BLOKER:** `tests/test_tz_zoneinfo_consolidation.py:152` `test_common_to_warsaw` ŁADUJE `sprint2_analysis/_common.py` po ścieżce (`_load_by_path(... "sprint2_analysis","_common.py")`) i asertuje `m.to_warsaw`/`m.WARSAW` (regresja konsolidacji TZ z Audytu 2.0). Test ZBIERANY na żywo (`pytest --co` = `1 test collected`, nie skip/xfail).
- Werdykt: `sprint2_analysis/` jest podtrzymany żywym testem TZ → **NIE do kasacji w tym pasie.** Trafienie = STOP dla celu (zgodnie ze specem).

## Wykonanie
- `git rm speed_tier_tracker.py tests/test_speed_tier_tracker.py` (jawnie).
- `common.py`: usunięty WYŁĄCZNIE blok flagi (6 linii: komentarz C4 + `ENABLE_SPEED_TIER_LOADING_PLANNED = False`); zero dangling ref (`grep` w worktree = pusto), komentarz `# Future flags` i otoczenie nietknięte.
- **compileall** (`-x '(eod_drafts|\.bak)'`) → exit 0.

## Regresja — dowód delta=0 (identyczny harness, PRESENT vs REMOVED)

Harness jak iter1: pkgroot w scratchpad (`dispatch_v2 → wt-l8-iter2` symlink + `flags.json → kanon`), `ZIOMEK_SCRIPTS_ROOT=pkgroot`. Potwierdzone: import `dispatch_v2` z worktree, `find_spec('dispatch_v2.speed_tier_tracker')` = `None` w stanie REMOVED / `True` w PRESENT. `common.py` (usunięcie flagi) IDENTYCZNY w obu biegach — delta izoluje samo kasowanie plików.

| Bieg | passed | failed | skipped | xfailed | xpassed |
|---|---|---|---|---|---|
| Pliki OBECNE (restore) | 4050 | 24 | 23 | 9 | 2 |
| Pliki USUNIĘTE (git rm) | 4041 | 24 | 23 | 9 | 2 |

**Delta:** failed **0**, skipped **0**, xfailed **0**, xpassed **0**; passed **−9** = DOKŁADNIE 9 testów osieroconego `test_speed_tier_tracker.py` (`pytest --co` = `9 tests collected`). Zero kolateralnej zmiany w JAKIMKOLWIEK innym teście. Kasowanie martwego modułu + jego własnego testu = delta-0 poza usuniętym własnym testem.

### O 24 „failed" — artefakt harnessu, NIE regresja
Jak w iter1: `test_courier_reliability.py` + `script_run` (`test_v319b_plan_manager`) rekonstruują ścieżkę absolutną zakładając katalog pakietu dosłownie `dispatch_v2`; pod symlinkiem `.resolve()` idzie do `wt-l8-iter2` → self-`SkipTest` liczony jako fail. Identyczne w obu biegach, NIE występuje na kanonie (katalog = `dispatch_v2`).

## DoD — czystość kanonu
`git -C /root/.openclaw/workspace/scripts/dispatch_v2 status --porcelain` — edycje wykonane wyłącznie w worktree; kanon nietknięty. Registry (L0.1), plan_recheck, telegram_approver, pending_proposals_store, tracker, ledger, flags.json, bug4* — NIE dotknięte.

## Kandydaci iter3 (NIE ruszane — wskazanie)
1. **`deploy_staging/scripts/gastro_assign.py`** (~120 LOC) — wg mapy md5-identyczny z żywym `scripts/gastro_assign.py`, mirror staging niewołany. Weryfikacja: md5 vs żywy + grep `deploy_staging` w ExecStart/cron. ⚠ NIE dotykać żywego `gastro_assign`.
2. Pozostałe P2/P3 z `L8_deadcode_mapa.md` §1 wymagające świeżej weryfikacji importerów+systemd+at.
- ⚠ `sprint2_analysis/` **wykreślone z kandydatów** — podtrzymane żywym testem TZ (patrz ETAP 0). Kasacja możliwa dopiero po odsprzężeniu `test_tz_zoneinfo_consolidation.py::test_common_to_warsaw` (osobny temat, wymaga ACK — dotyka regresji TZ).
