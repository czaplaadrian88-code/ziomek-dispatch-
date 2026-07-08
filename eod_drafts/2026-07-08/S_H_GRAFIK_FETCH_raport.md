# RAPORT — SPRINT H (NAPRAWA) czerwony test bazowy `test_grafik_fetch_schedule[fetch]` (bug H1 None-sort)

**Sesja:** tmux 43. **Worktree:** `/root/.openclaw/workspace/scripts/wt-grafik-fetch` (branch `fix/grafik-fetch-nonesort`).
**Baseline:** master `8760ee6`. **Commit fixu:** `8c6864c`. **Status:** ✅ ZAMKNIĘTE (merge sekwencyjny + tag po ACK).

---

## 1. ETAP 0 — stan na żywo (potwierdzony)
- Worktree clean, branch `fix/grafik-fetch-nonesort`, HEAD @ `8760ee6`.
- **Pełna regresja PRZED fixem: `1 failed, 4490 passed, 27 skipped`** — jedyny FAIL:
  `tests/test_grafik_fetch_schedule.py::test_parity_live_equals_staged_mirror[fetch]` (= `test_grafik_fetch_schedule[fetch]` z handoffu). Zgodne z handoffem.
- Multi-sesja: tmux 34 (koordynacja) / 41 (F-coords) / 42 (I cod-weekly) — **żadna w domenie grafiku**; brak lsof na żywym pliku; mtime żywego 07:44 (brak równoległej edycji). **Nie cudzy niezwiązany diff.**

## 2. DIAGNOZA — źródło (ETAP 1)
Test `[fetch]` to **parytet** `_LIVE_FETCH` (`/root/.openclaw/workspace/scripts/fetch_schedule.py`) ↔ `_STAGED_FETCH` (`deploy_staging/scripts/fetch_schedule.py` w repo). Był 1 hunk rozjazdu:

| | linia sortu |
|---|---|
| ŻYWY (naprawiony, mtime Jul 8 07:44, `.bak-pre-none-sort-2026-07-08`) | `sorted(working, key=lambda x: x[1]['start'] or "99:99")` |
| STAGED (repo, stary) | `sorted(working, key=lambda x: x[1]['start'])` |

**Bug H1 (None-sort) u źródła:** przy `ENABLE_GRAFIK_ENTRY_SALVAGE` i literówce w godzinie startu (tylko `end` się parsuje) `parse_schedule` tworzy wpis `{"start": None, "end": ..., "parse_degraded": True}` (fetch_schedule.py l.156-162). Trafia on do `working`; stary `sorted(...['start'])` → **`TypeError` (None vs str) → wywala `main()` → BLOKUJE zapis całego grafiku** (log wyświetlania nie może wysadzić fetchu). Fix `or "99:99"` = wpisy degraded na koniec listy, bez crasha.

**Klasa:** stale-mirror (L8) — fix None-sort wpięty **u źródła** do żywego (ten sprint: bak z dziś, nazwany po sprincie), ale **niezmirrorowany** do repo → parytet czerwony. Naprawa KODU (nie testu): synchronizacja staged z żywym.

## 3. MAPA KOMPLETNOŚCI (ETAP 3) — bliźniaki
| Miejsce | Dotknięte? | Powód |
|---|---|---|
| `fetch_schedule.py` sort-po-start (l.201) | ✅ (żywy już + staged sync) | jedyny sort mogący trafić `start=None` |
| `schedule_utils.py` | N-D | l.425 `entry.get('start')` = `.get()`, **nie sort**; parytet `[utils]` zielony bez zmian — brak bliźniaczego None-sortu |
| test (asercja) | N-D | asercja poprawna (żąda bajt-identyczności live↔staged); naprawiono KOD, nie test |
| żywy plik `/scripts/fetch_schedule.py` | N-D | już naprawiony u źródła (07:44); restart N/D (skrypt cron-owy, nie usługa) |

## 4. DOWODY (ETAP 4)
- `md5sum` żywy == staged: `ef0a77f629416534d409ae0b57f05517` (bajt-identyczne), `diff` pusty.
- `py_compile` staged: OK.
- Test grafiku: **15/15 passed** (w tym `[fetch]` i `[utils]`).
- **Pełna regresja PO fixie: `4491 passed, 0 failed, 27 skipped, 8 xfailed, 2 xpassed`** (baseline: 4490/1). Czerwony naprawiony, **zero nowych regresji**.

## 5. DoD
1. ✅ `pytest tests/` **0 FAIL**.
2. ✅ Fix None-sort udokumentowany + drift live↔staged zsynchronizowany (bajt-identyczne).
3. ✅ Commit `8c6864c` PRZED końcem. Merge sekwencyjny + tag **po ACK Adriana**.

## 6. GRANICE / ROLLBACK
- **Nie tknięto:** silnika decyzyjnego, panelu GRF-02, współrzędnych (F), cod-weekly (I), `flags.json`. Żaden restart/deploy.
- **Rollback:** `git revert 8c6864c` (staged wróci na starą linię — parytet znów czerwony do czasu ponownej synchronizacji). Żywy plik pozostaje naprawiony niezależnie.
