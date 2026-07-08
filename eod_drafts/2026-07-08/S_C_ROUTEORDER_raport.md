# SPRINT C — INV-SRC-ROUTE-ORDER: jedno źródło kolejności trasy PRZEZ KONSTRUKCJĘ

**Sesja tmux 38 · 2026-07-08 · worktree `wt-routeorder-src` (branch `quality/inv-src-route-order`) · baseline master `8a13b77`.**
**Cel (handoff):** zamienić parytet-trzymany-flagami na parytet-z-konstrukcji + twardy strażnik CI zamiast wygasającego monitora `ziomek_time_route_monitor` (10.07).

---

## TL;DR (werdykt)

- **Golden-fixture equivalence net BYŁ już zbudowany** (Sprint 0/30, 01–07.07): 3 nogi (silnik/konsola/apka) na wspólnym korpusie + żywy następca monitora. Slot był **STALE-🔴** (klasa C15 — status dashboardu przeterminowany, nie „brak strażnika").
- **Domknąłem KONSTRUKCJĘ + DOWÓD:** 3 z 4 kopii zwinięte do kanonu `route_order.py` (silnik/plan_recheck/apka — apka OBA flagi LIVE); **panel = jedyna pozostała kopia**, golden-pinowana, delegacja gotowa jako flip-card (0-diff dowód).
- **Każda noga golden mutation-probed RED** (dispatch/panel/apka) → strażnik ma zęby (C13/C14), nie tekstowy. `test_route_order_live_parity` żywy ON w dispatch CI.
- **Sloty uzbrojone 🔴→🟢:** INV-SRC-ROUTE-ORDER (Kontrakt ①) + INV-TWIN-ROUTE-ORDER (Kontrakt ③) w `ZIOMEK_INVARIANTS.md`.
- **ZERO zmian silnika / flag / flags.json / deploy live.** Panel-delegacja = flip-card za ACK (niżej §5).

---

## §0. ETAP 0 — stan na żywo (protokół #0)

- **Baseline `pytest tests/` (worktree, pkgroot ZIOMEK_SCRIPTS_ROOT→worktree):** `4465 passed, 1 failed, 27 skipped, 10 xfailed` (140 s).
  - Jedyny fail = `test_grafik_fetch_schedule.py::test_parity_live_equals_staged_mirror[fetch]` — **nonhermetyczny parytet deploy-staging mirror vs żywy `fetch_schedule.py`**, klasa L8-mapy, ZERO związku z route-order. Pre-existing/środowiskowy (zależny od stanu `deploy_staging` innej sesji). To moja lista PRE; regresję oceniam po LIŚCIE ID (feedback: worktree-false-fails).
- **Multi-sesja recon (C1):** żywe tmux 34(koordynacja/kalibracja)/38(JA)/39(Sprint D perf-pipeline `wt-pipeline-p95`)/40(Adrian). Sprint D = pipeline concurrency/OR-Tools config = ROZŁĄCZNE z route_order.py+fleet_state.py. `fleet_state`/`courier_orders` czyste w swoich repach (sprawdzone `git status` przed każdym probem).
- **Harness:** sesyjny `pkgroot-routeorder` (symlink `dispatch_v2`→worktree, reszta→kanon) — pytest testuje KOD WORKTREE, nie kanon (C12e/KORZEŃ #1).

## §1. MAPA KOMPLETNOŚCI — 4 kopie kolejności trasy

Grep całościowy (3 repa, bez testów/bak):

| # | Moduł | Repo | Status po Sprint C |
|---|---|---|---|
| 1 | `route_order.py` `order_podjazdy`/`build_stop_sequence`/`repair_dropoffs_after_pickups` | dispatch | **KANON — JEDNO ŹRÓDŁO** (PURE stdlib, bez cyklu) |
| 2 | `route_podjazdy.py` | dispatch | re-eksport (ten SAM obiekt — `test_reexport_is_same_object`) — **0 kopii** |
| 3 | `plan_recheck._repair_dropoffs_after_pickups` | dispatch | **deleguje** → `_route_order.repair_dropoffs_after_pickups` (`test_plan_recheck_repair_delegates`). `_coalesce_same_pickup_nodes`/`_apply_canon_order_invariants` = odrębna operacja PLANU silnika (nie kopia projekcji) |
| 4a | apka `courier_orders` gałąź dominująca `console_podjazdy` | courier_api | **deleguje** `order_podjazdy` — `ENABLE_APP_ROUTE_FROM_CONSOLE=1` **LIVE** (drop-in `podjazdy.conf`) |
| 4b | apka `courier_orders` gałąź fallback | courier_api | **konwerguje** `_reorder_steps_to_canon`→`build_stop_sequence` — `ENABLE_ROUTE_ORDER_UNIFIED=1` **LIVE** (drop-in `route-order-unified.conf`) |
| 5 | **panel `fleet_state._build_route`** | nadajesz_clone/panel | **JEDYNA pozostała KOPIA** kolejności (osadzona w warstwie ETA); golden-PINOWANA (nie flagą). Delegacja = flip-card §5 |

**Zwinięte do kanonu: 3/4** (silnik+plan_recheck, apka×2 LIVE). **Zostaje: 1** (panel — z uzasadnieniem: osobne repo/venv, delegacja = live-deploy za ACK; dziś pinowana golden+live-parity).

## §2. STRAŻNIK CI — golden-fixture equivalence (3 nogi + żywy następca)

Wspólny korpus `tests/golden/route_order_corpus.json` (25 case'ów, ≥17 obowiązkowych klas + żywe worki). `proj = [(typ, sorted(order_ids))]`.

| Noga | Test | Repo/CI | Stan 08.07 |
|---|---|---|---|
| silnik==golden | `test_route_order_golden` + `test_route_order_unify_s30` | dispatch | 15 passed |
| konsola==golden | `test_route_order_parity_golden` | panel venv | 4 passed |
| apka delegacja | `test_route_order_unified_s30` | courier_api | 4 passed |
| **konsola==kanon ŻYWO + dryf flag** | `test_route_order_live_parity` (tool `route_order_live_parity_check.py`) | dispatch CI (ON od 05.07, opt-out `=0`) | **1 passed** (żywe worki, verdict OK) |

**Następca monitora:** `test_route_order_live_parity` odpala `_build_route` (konsola, panel venv) vs `order_podjazdy` (kanon) na ŻYWYCH workach + pilnuje dryfu flag porządkotwórczych vs meta korpusu. To pion Q3 wygasającego `ziomek_time_route_monitor` (MONITOR_STOP_AFTER=2026-07-10) BEZ daty wygaśnięcia.

## §3. DOWÓD — mutation-probe (C13/C14: strażnik ma ZĘBY)

Dyscyplina C14: worktree/pliki czyste (committed), `cp .bak` przed mutacją, restore, `md5` przed==po, `git diff` czysty. NIGDY `git checkout` na wspólnym repo panelu/apki (mógłby skasować pracę innej sesji).

| Probe | Mutacja | Wynik | Restore |
|---|---|---|---|
| silnik | `route_order.order_podjazdy` → `return order[::-1]` | `test_canon_order_matches_golden` + `test_order_matches_golden_corpus` **RED** (2 failed) | md5 MATCH, git diff czysty |
| konsola | `fleet_state._build_route` → `order = order[::-1]` | `test_console_route_matches_canon_golden` **RED** | md5 MATCH |
| apka | `_reorder_steps_to_canon` → `reordered = list(seq)` (skip reorder) | `test_reorder_steps_to_canon_matches_build_stop_sequence` + `test_reorder_carried_first` **RED** (2 failed) | md5 MATCH |

Wszystkie 3 nogi: **RED na wstrzykniętym rozjeździe, GREEN na parytecie** → DoD #3 spełniony.

## §4. Sloty inwariantów — 🔴→🟢

`ZIOMEK_INVARIANTS.md` (worktree):
- **INV-SRC-ROUTE-ORDER** (Kontrakt ① l.23) 🔴→🟢 — pełny opis kanonu+konsumentów+strażników+flip-card.
- **INV-TWIN-ROUTE-ORDER** (Kontrakt ③) 🔴→🟢 — parytet bliźniaków = golden equivalence.
- Dashboard: ① SLOT 2→1, ③ SLOT 1→0; RAZEM SLOT 12→10, RT/TEST ~31→~33. Wniosek + nota Sprint C 08.07 dodane.

## §5. FLIP-CARD (ZA ACK) — delegacja panelu `fleet_state._build_route`

**Cel:** zwinąć OSTATNIĄ kopię → pełna konstrukcja (jedno źródło, zero golden-pinu do utrzymania).
**Ryzyko:** live-deploy na WSPÓLNYM repo panelu (`nadajesz-panel.service` restart, `gps.nadajesz.pl/admin`) → **wymaga ACK + deploy seryjny (C12c)**. NIE deployuję sam.
**Bezpieczeństwo pattern:** panel JUŻ importuje `dispatch_v2` w runtime (`committed_time.py` → `from dispatch_v2.common import ...`, `courier_block.py` → `manual_overrides`) tym samym wzorcem `sys.path.insert("/root/.openclaw/workspace/scripts")` — delegacja NIE dodaje nowej zdolności, powiela istniejący LIVE pattern.

**DOWÓD 0-diff (także kolejność wewnątrz stopu — golden pilnuje tylko `sorted(oids)`):**
`tools/route_order_panel_delegation_0diff.py` (uruchamiać panel venv) porównał RAW `order` `_build_route` vs `order_podjazdy` na: **25 case'ów korpusu = 0 diff**, **9 żywych worków = 0 diff** → delegacja bajt-identyczna. Ten tool = walidator PRZED-deploy flip-cardu (musi zwrócić `0-DIFF OK` na wersji zdelegowanej).

**Patch (minimalny, tylko blok `order`):** w `fleet_state.py`:
1. Import (na górze, wzór `committed_time`):
```python
import sys
_SCRIPTS_DIR = "/root/.openclaw/workspace/scripts"
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from dispatch_v2 import route_order as _route_order
```
2. W `_build_route` zastąp blok obliczania `order` (dziś ~l.442-464: trust_canon path `_order_from_plan_seq`+coverage ORAZ fallback `_pickup_runs`+grupowanie) JEDNYM wywołaniem:
```python
        order = _route_order.order_podjazdy(
            bag, plan_doc,
            plan_aware=flag("PLAN_AWARE_PODJAZDY"),
            trust_canon=(trust_canon_ok and flag("TRUST_CANON_ORDER")))
        if not order:
            return [], ""
```
   (Zostaw `plan_pred`/`plan_drop_rank`/`plan_seq`/`by_oid` — używane w warstwie ETA niżej. `carried`/`to_pick` + helpery `_plan_pickup_clusters`/`_pickup_runs`/`_order_from_plan_seq` stają się martwe → usuń w follow-upie po zielonym golden.)

**Walidacja przed deploy:** `test_route_order_parity_golden` (panel) musi zostać 4/4 zielony + `verify_panel_delegation_0diff.py` = 0-DIFF na wersji zdelegowanej.
**Deploy runbook (za ACK):** `cp fleet_state.py .bak-pre-deleg-2026-07-08` → patch → panel-venv `py_compile` + `pytest tests/test_route_order_parity_golden.py` → `systemctl restart nadajesz-panel.service` → smoke `read_fleet()` 1 kurier z workiem → obserwacja.
**Rollback:** `cp .bak` + restart (soft) / rewers commita panelu.

**Apka:** flip-card `ENABLE_ROUTE_ORDER_UNIFIED` / `ENABLE_APP_ROUTE_FROM_CONSOLE` = **JUŻ WYKONANY** (oba `=1` LIVE w drop-inach courier-api). Zostaje tylko panel.

## §6. DoD checklist

1. ✅ Regresja `pytest tests/` zielona vs baseline (ta sama lista fail-ID: 1× env `grafik_fetch`; route-order testy 15+4+4+1 green) — patrz §7.
2. ✅ Mapa 4 kopii: 3 zwinięte / 1 (panel) zostaje z uzasadnieniem + flip-card.
3. ✅ Golden guard w CI: 3 nogi mutation-probed RED, GREEN na parytecie; sloty 🔴→🟢.
4. ✅ Karta flipu: apka JUŻ live; panel = §5 za ACK.
5. ✅ Commit przed końcem (worktree); merge do master seryjny za ACK; raport = ten plik.

## §7. Regresja końcowa (dowód, nie deklaracja)

| Bieg | Wynik | Fail-ID |
|---|---|---|
| **PRE** (baseline `8a13b77`, przed zmianą) | `4465 passed, 1 failed, 27 skipped, 10 xfailed` (140 s) | `test_grafik_fetch_schedule::test_parity_live_equals_staged_mirror[fetch]` |
| **POST** (po armowaniu slotów + raport + tool) | `4465 passed, 1 failed, 27 skipped, 10 xfailed` (136 s) | **ta sama** `test_grafik_fetch_schedule::…[fetch]` |

**LISTA fail-ID identyczna PRE==POST → 0 nowych regresji** (ocena po liście, nie po surowym „X failed" — feedback worktree-false-fails). Zmiany Sprint C = markdown + nowy tool (niekonsumowany przez regresję) → wynik identyczny, zgodnie z oczekiwaniem i UDOWODNIONY. Grafik-fail = nonhermetyczny mirror deploy-staging, poza zakresem. Testy route-order: silnik 15 + panel 4 + apka 4 + live-parity 1 = ZIELONE; każda noga mutation-probed RED (§3).

**Werdykt kanonu:** ostateczny werdykt bierz z KANONU (główny checkout) po merge — night-guard 01:15 mierzy kanon. W worktree artefaktów pkgroot brak (jedyny fail = realny env-fail, nie artefakt izolacji).

## §8. Rollback całości Sprint C

Zmiany Sprint C = **tylko dokumentacja** (`ZIOMEK_INVARIANTS.md` + ten raport) w worktree. Rollback = `git revert <commit>` na branchu / nie-merge do master. ZERO zmian kodu silnika/flag/live → zero ryzyka produkcyjnego. Panel-delegacja NIE wdrożona (flip-card).
