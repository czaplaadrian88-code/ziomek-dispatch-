# L8 iteracja 3 — raport (kasacja `sprint2_analysis/` + odsprzężenie fixtura TZ)

**Data:** 2026-07-03 · **Branch:** `fix/l8-iter3` (worktree `/root/.openclaw/workspace/scripts/wt-l8iter3`) · **Charakter:** build-only, chirurgiczna kasacja udowodnionego martwego kodu + odsprzężenie żywego testu, który go podtrzymywał. ZERO flipów/restartów/push. Kanon NIETKNIĘTY (edycje wyłącznie w worktree).

## Cel
Domknąć bloker z iter2: `sprint2_analysis/` (martwy) był podtrzymywany przez ŻYWY test `tests/test_tz_zoneinfo_consolidation.py::test_common_to_warsaw`, który ładuje `_common.py` po ścieżce i asertuje jego `to_warsaw`/`WARSAW`. iter3 = odsprzęgnąć test, potem `git rm`.

**Wynik:** `sprint2_analysis/` USUNIĘTY (21 plików / 1670 linii; z tego **774 LOC** w 7 plikach `.py`, reszta = run_all.sh 35 + logi + data-snapshoty + README). Test odsprzężony (usunięty jako bezprzedmiotowy — patrz decyzja). Regresja delta = **−1 passed** (dokładnie usunięty test), 0 kolateralnych.

## Decyzja o teście: `test_common_to_warsaw` USUNIĘTY (nie „przeniesiony fixture")

Rozstrzygnięcie dwóch opcji ze specu:

`_common.py` jest **SUBJEKTEM pod testem, nie generycznym fixturem.** Test nie używa `_common.py` jako pomocnika do zbadania czegoś innego — testuje WŁASNE `to_warsaw`/`WARSAW` tego pliku (zima → godzina 10 nie 11 = dowód ZoneInfo zamiast fixed +2). Należy do partycji (b) „LETNI parytet + ZIMOWA poprawność" — każdy test tej partycji waliduje konwersję TZ JEDNEGO konkretnego pliku po Audycie 2.0.

Skoro kasujemy cały `sprint2_analysis/` jako martwy kod (brak jakiegokolwiek konsumenta), to **nie ma już produkcyjnego zachowania do pilnowania** — test staje się bezprzedmiotowy. „Przeniesienie fixtura" do `tests/fixtures/` oznaczałoby utrzymywanie kopii martwego pliku i testu na muzealny idiom, który i tak jest pokryty gdzie indziej → **wzrost entropii**, sprzeczny z meta-regułą „każda sesja zostawia entropię NIŻSZĄ". Dlatego: usunięcie testu, nie przenoszenie.

**Brak luki w pokryciu:** idiom `to_warsaw` = `dt.astimezone(WARSAW).replace(tzinfo=None)` na `ZoneInfo("Europe/Warsaw")` jest NADAL pod strażą przez bliźniacze testy partycji plików **żywych** (`test_freshness_monitor_tz`, `test_reassignment_shadow_tw`, `test_monitor_refloor_hhmm`, `test_sequential_replay_expression_equivalence`, `test_enricher_*`). W miejscu usuniętego testu zostawiłem komentarz-nagrobek wyjaśniający tę logikę.

## Ratchet NIE osłabiony — dowód wstrzyknięcia (teeth)

Ratchet (`test_ratchet_no_new_fixed_offset_tz`) jest ORTOGONALNY do `test_common_to_warsaw` (osobna funkcja skanująca całe repo). Moje edycje ruszyły dwie rzeczy powiązane z partycją:
- usunięcie `test_common_to_warsaw`;
- zdjęcie `"sprint2_analysis/_common.py"` z `_MY_PARTITION` (plik nie istnieje → nigdy nie byłby offenderem; ratchet globalny i tak łapie każdy nowy fixed-offset niezależnie od członkostwa w partycji);
- korekta komentarza „6 plików partycji" → „5".

**Dowód, że ratchet dalej gryzie PO zmianach:** wstrzyknąłem `tools/_ratchet_injection_probe.py` z `timezone(timedelta(hours=2))` → `test_ratchet_no_new_fixed_offset_tz` **PADŁ** z:
```
E  AssertionError: NOWE fixed-offset TZ (...): tools/_ratchet_injection_probe.py
E  assert not {'tools/_ratchet_injection_probe.py'}
```
Po `rm` probe (bez residuum w git) → cały plik testu TZ **PASS 14/14**. Ratchet ma zęby, allowlista nietknięta.

## 3 dowody śmierci (delta-0 przed kasacją)

**(a) grep repo + `scripts/` poziom wyżej** (`--include=*.py,*.sh,*.json`, bez node_modules/.git/eod_drafts, bez samego katalogu `sprint2_analysis`):
- `dispatch_v2/event_bus.py:79` — **KOMENTARZ** (lista „Czytelnicy" dla `AUDIT_EVENT_TYPES`, nie import ani wywołanie);
- `tests/test_tz_zoneinfo_consolidation.py:152` — usuwany test;
- `tests/test_tz_zoneinfo_consolidation.py:253` — usuwany wpis `_MY_PARTITION`;
- (worktree `wt-fingerprint`/`wt-l8iter3` = kopie robocze; 14 trafień w `eod_drafts/` = dokumentacja).
Zero importerów/wywołań/subprocess/`-m`.

**(b) systemd + cron + at:**
- `grep /etc/systemd/system/` → NONE
- `crontab -l` → NONE
- `grep /etc/cron*` → NONE
- skan `atq`/`at -c` → żaden job nie referuje modułu.

**(c) compileall po kasacji** (`-x '(eod_drafts|\.bak|deploy_staging)'`) → **exit 0**.

Domknięcie kompletności: stały komentarz w `event_bus.py:79` referował `sprint2_analysis` jako czytelnika `AUDIT_EVENT_TYPES` — po kasacji to martwa referencja. Usunąłem ją z komentarza (comment-only, zero zachowania/importu) — kasacja bez dangling-referencji nawet w komentarzach.

## Regresja — delta vs baseline

| Bieg | passed | failed | skipped | xfailed |
|---|---|---|---|---|
| Baseline worktree (PRESENT, przed zmianami) | 4110 | 0 | 23 | 11 |
| Kanon baseline (podany, 11:15) | 4110 | 0 | 23 | 11 |
| **Final (REMOVED, po kasacji + odsprzężeniu)** | **4109** | **0** | **23** | **11** |

**Delta:** failed 0, skipped 0, xfailed 0; **passed −1 = dokładnie usunięty `test_common_to_warsaw`** (świadome, uzasadnione powyżej). Zero kolateralnej zmiany. Uwaga: worktree biegnie z katalogu `wt-l8iter3` BEZ artefaktu „24 failed" z iter2 (tamten wynikał z symlinkowego pkgroota o innej nazwie; bezpośredni bieg = czysto 4110/4109).

## LOC skasowane
- 7 plików `.py` = **774 LOC** (`_common.py` 59, `data_inventory.py` 77, `override_patterns.py` 213, `propose_uptime_analysis.py` 106, `report_builder.py` 101, `sanity_checks.py` 100, `tak_mystery.py` 118) — zgodne z ledger §238.
- + `run_all.sh` 35 + 10 logów + 2 data-snapshoty (`.md`) + README = **21 plików / 1670 linii** total (git `--stat`).

## BONUS — `deploy_staging/scripts/gastro_assign.py`: ZOSTAWIONY (opisany)
- **md5 staged == kanon-staged == ŻYWY** = `3b8ee5f3446ada390962154f886751e3` (identyczne).
- ŻYWY `/root/.openclaw/workspace/scripts/gastro_assign.py` MA fix ZoneInfo (l.26-27 `_WARSAW = ZoneInfo("Europe/Warsaw")`, l.260 `datetime.now(_WARSAW)`) → deploy 02.07 ~11:45 wszedł.
- **ALE:** `grep eod_drafts za deploy_staging/scripts/gastro_assign` → **6 trafień** (ZIOMEK_FINDINGS_LEDGER, l8-iter1/iter2, L8_deadcode_mapa, auton-blockers, FALA1_tz), a l8-iter1 opisuje staged jako „nośnik fixu pasa A". Zgodnie ze specem (JAKAKOLWIEK wątpliwość → ZOSTAW i opisz): **NIE usuwam** staged. ŻYWY `gastro_assign.py` NIE dotknięty.

## DoD
- Kanon `dispatch_v2` czysty (edycje wyłącznie w worktree).
- Registry/plan_recheck/telegram/flags.json/bug4* — NIE dotknięte.
- Zmienione w worktree: `git rm -r sprint2_analysis/` (21 plików) + `tests/test_tz_zoneinfo_consolidation.py` (usunięty test + `_MY_PARTITION` −1 + komentarz „5") + `event_bus.py` (comment-only).
