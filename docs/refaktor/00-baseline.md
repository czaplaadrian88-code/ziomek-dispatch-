# 00 — Baseline (Faza 0 transformacji architektury)

**Data:** 2026-07-06 ~00:20-00:5x UTC · **HEAD:** `fcf1342` (`master` == `origin/master`)
**Gałąź robocza:** `refaktor/architektura` w worktree `/root/.openclaw/workspace/scripts/wt-refaktor-arch` (per ADR-007 — żywe drzewo `dispatch_v2/` zostaje na `master`, bo z niego biegną serwisy; ta sesja go NIE przełącza).
**Sesja:** tmux 21 („Transformacja architektury systemu dispatch Ziomek"). Ta sesja **nie pełni roli FLIPMASTER** — zero flipów flag, restartów, deployów.

---

## 1. Wyniki baseline (build / testy / linter / typecheck)

| Krok | Wynik | Dowód |
|---|---|---|
| Build (py_compile całego repo) | 🟢 CZYSTY | `venvs/dispatch/bin/python -m compileall -q -x '(eod_drafts\|__pycache__\|docs/)' .` → rc=0 |
| Testy (pełna suita) | 🟢 **4239 passed, 0 failed** (23 skipped, 8 xfailed, 2 xpassed, 139 warnings, 119,5 s) | `/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q` z kanonicznej ścieżki `dispatch_v2/` (nie worktree — wymóg protokołu #0 ETAP 0), exit 0; spójne z baseline 05.07 (4229/0 + testy dopisane 05.07 wieczorem) |
| Linter | ⚠️ **BRAK NARZĘDZIA** | brak ruff/flake8/pylint/black w `venvs/dispatch` (24 pakiety, celowo lean per ADR-006); brak configów (`pyproject.toml`, `ruff.toml`, `setup.cfg` nie istnieją) |
| Typecheck | ⚠️ **BRAK NARZĘDZIA** | brak mypy/pyright w venv i w systemie; brak configu |

- Interpreter kanoniczny: `/root/.openclaw/venvs/dispatch/bin/python` (Py 3.12.3, ortools 9.15). Systemowy `python3` = 123 fałszywe faile (ADR-006) — NIE używać.
- Znany flake pełnego biegu: `test_v326_hotfix_parser::script_run` (raz na kilka biegów; w izolacji zielony — nie traktować jako regresji; memory 05.07). W tym biegu nie wystąpił.
- **RYZYKO ODNOTOWANE:** brak lintera i typecheckera = brak siatki bezpieczeństwa na refaktor „na lata". Dodanie ich to NOWA zależność → wymaga zgody Adriana (ZAKAZ #3 briefu + ADR-006 lean-venv). Propozycja w pytaniach (dev-only, poza venv silnika).

## 2. Świeżość artefaktów nawigacyjnych (vs HEAD `fcf1342`, 05.07 wieczór)

| Artefakt | Ostatni commit | Ocena |
|---|---|---|
| `docs/CODEMAP.md` | `66ccb02` 03.07 | AKTUALNY (od 03.07 tylko commity docs/tracker/testy) |
| `docs/ARCHITECTURE.md` | `68b6d20` 03.07 | AKTUALNY — 10 warstw, przepływ Mermaid, punkty wejścia, 3 światy flag |
| `ZIOMEK_ARCHITECTURE.md` (kanon, zatw. Adrian 01.07) | `0b01e46` 03.07 | AKTUALNY |
| `ZIOMEK_INVARIANTS.md` | `ac99686` 05.07 (VOID 0/4) | AKTUALNY |
| `ZIOMEK_DEFINITION_OF_DONE.md` | `542dfa1` 03.07 | AKTUALNY |
| `docs/audyt/` (13 raportów Wielkiego Audytu-Porządków) | ostatni touch `8b5af8a` 05.07 | AKTUALNE |
| `docs/decisions/ADR-001..008` | 03.07 | ŻYWE; kluczowe dla tej pracy: ADR-006 (venvy), ADR-007 (multi-sesja/worktree), ADR-008 (rdzeń nie przenoszony) |
| `docs/archive/AUDIT_2026-05-07/` | archiwum | punkt odniesienia historyczny (maintainability 5/10, scalability 3/10 — stan z 07.05) |
| Tracker audytów | `eod_drafts/2026-07-02/ZIOMEK_STAN_AUDYTY_1i2.md` (nagłówek 05.07) | ŻYWY |

**Wniosek:** w Fazie 1 NIE skanuję repo od zera — startuję z CODEMAP/ARCHITECTURE/kanonu/audytów i weryfikuję ich tezy do poziomu `plik:linia` na HEAD.

## 3. Stan na żywo (ETAP 0 protokołu #0 — zweryfikowane 06.07 ~00:20 UTC)

- Serwisy silnika: `dispatch-shadow`, `dispatch-panel-watcher`, `dispatch-gps`, `dispatch-sla-tracker`, `dispatch-monitor-419` = running; ~30 timerów aktywnych.
- **2 unity FAILED = ZNANE z memory 05.07, nie ruszam** (działka sesji ops/FLIPMASTER): `dispatch-cod-weekly` (run dziś 06:00 do ręcznej weryfikacji), `dispatch-night-guard` (fixy w masterze `65d497c`; bieg dziś 01:15 zweryfikuje).
- at-joby: 205 (12:40), 206 (14:30), 208 (19:30) — trwałe, wykonają się same; konsumpcja werdyktów = FLIPMASTER.
- **MULTI-SESJA (C1 recon):** aktywne INNE sesje claude: **tmux 20 „Sprint 2 Flipmaster handoff — PRE-FLIGHT do K6"** (⚠ memory 05.07 twierdziła, że tmux 20 zamknięty — NIEAKTUALNE, sesja żyje) oraz **tmux 11 „Przejrzeć zadania audytów i zaplanować naprawy"**. Podział: ta sesja = wyłącznie `docs/refaktor/` + gałąź `refaktor/architektura`; flags.json/restarty/deploye = FLIPMASTER.
- Working tree żywego repo: `M daily_accounting/kurier_full_names.json` + 6 untracked w `eod_drafts/` — **nie moje, nietknięte**.
- `flags.json` mtime 05.07 19:13 (po flipie K2 `ENABLE_LEXQUAL_GEOMETRY_TIEBREAK`) — zgodne z memory; okno obserwacji K2 domyka się wt 07.07 ~18:52 UTC.

## 4. Skala REALNA (zmierzona z danych, nie deklarowana)

Unikalne `order_id`/dzień z `logs/shadow_decisions.jsonl` (+ rotacja `.1`):

| 28.06 | 29.06 | 30.06 | 01.07 | 02.07 | 03.07 | 04.07 | 05.07 |
|---|---|---|---|---|---|---|---|
| 275 | 236 | 229 | 254 | 191 | 224 | 212 | 286 |

→ **~190-290 zamówień/dzień, średnio ~240** (peaki So-Nd). Flota: **~62 kurierów** (memory 05.07), jednocześnie na zmianie mniej.

**Stack (ustalony z repo — do potwierdzenia przez Adriana):** Python 3.12; monolit wieloprocesowy pod systemd (5 serwisów długożyjących + ~30 timerów); stan silnika = pliki JSON w `/root/.openclaw/workspace/dispatch_state/` + logi JSONL (`shadow_decisions.jsonl`); OR-Tools 9.15 (TSP), LightGBM (scoring ML), OSRM :5001 (czasy przejazdu); HTTP wyłącznie stdlib `urllib` (celowo, ADR-006); **brak brokera, brak DB w silniku** (Postgres :5433 jest po stronie panelu nadajesz — inne repo). Flagi: 3 światy (ADR-004).

**Wniosek dla Fazy 3 (skala adekwatna):** to skala **setek zleceń/dzień w jednym mieście**, nie tysięcy/godzinę. Każdy element architektury docelowej musi być uzasadniony tą skalą + prognozą Adriana — domyślnie ŻADNEGO event busa/CQRS/partycjonowania bez dowodu potrzeby.

## 5. Zasady przyjęte na czas programu (fazy 0-6)

1. Protokół #0 (ETAP 0→7) obowiązuje NAD tym programem — przy każdej przyszłej zmianie kodu (Faza 5) pełna mapa kompletności, bliźniaki razem, flaga ON≠OFF, pełna regresja vs baseline **4239/0** (06.07 00:2x UTC).
2. Git: praca na `refaktor/architektura` (+ pod-gałęzie `refaktor/krok-NN-nazwa` w Fazie 5); commity `refactor(zakres): opis` / `docs(refaktor): ...`; commit ATOMOWO po jawnych ścieżkach (C1-git); ZAKAZ force push, kasowania plików, commitów na `master`.
3. Zero zmian w kodzie produkcyjnym do zakończenia i akceptacji Fazy 4. Fazy 0-4 = wyłącznie `docs/refaktor/`.
4. Eksploracja przez subagentów read-only; wyniki do plików w `docs/refaktor/`, decyzje tylko w sesji głównej.
5. Testy: zawsze `venvs/dispatch`; baseline i regresje z kanonicznej ścieżki; testy charakteryzujące pisane PRZED zmianą modułu bez pokrycia.
6. Rdzeń silnika (`feasibility_v2`/`dispatch_pipeline`/`plan_recheck`/`courier_resolver`/`route_simulator_v2`/scoring) = jeden właściciel/fala, seryjnie (ADR-007/ADR-008) — kroki Fazy 5 na rdzeniu nigdy równolegle z inną sesją.

## 6. Pytania do Adriana (STOP Fazy 0) — ✅ ODPOWIEDZI 06.07

> **Odpowiedzi Adriana (06.07, czat):**
> 1. **Skala docelowa: ~400 zamówień/dzień + MULTI-TENANT**; integracje wg tego, co opisane na serwerze (= artefakty `docs/integracje/00-08+99`, model Wolt Drive, pakiet IR v1).
> 2. Tick ~3 min **OK** — bez wymogu sekundowego.
> 3. **TAK** — formaty JSONL/state-files traktujemy jako kontrakt publiczny.
> 4. **TAK** — okna prac poza peakami/So-Nd; bez sztywnego deadline'u kalendarzowego.
> 5. **OK** — shadow+replay pełni rolę stagingu; nie budujemy osobnego środowiska.
> 6. **TAK** — ruff+mypy dev-only w OSOBNYM venv narzędziowym (venv silnika nietknięty).
> 7. **TAK** — podział ról potwierdzony (ta sesja: fazy 0-4 tylko docs; FLIPMASTER: flipy/restarty; Faza 5 po akceptacji planu i w uzgodnieniu z FLIPMASTEREM).

### Treść pytań (dla kontekstu)

1. **Skala docelowa 12 mies.** (zmierzona dziś: ~240/d, flota 62): ile zamówień/dzień i ilu kurierów zakładamy? Czy dochodzi drugie miasto / multi-tenant (SaaS z audytu 18.06, integracje Wolt-Drive z 05.07)?
2. **SLA / czas rzeczywisty:** jaki maksymalny czas od NEW_ORDER do propozycji przypisania jest akceptowalny (dziś tick ~co 3 min)? Czy w horyzoncie 12 mies. ma być „sekundowo"?
3. **Nietykalne:** poza publicznymi kontraktami (Panel API, formaty shadow_decisions.jsonl, schematy state-files), integracjami i wyciszonym Telegramem — czy coś jeszcze? Czy formaty JSONL/state-files traktować jako kontrakt publiczny (konsumuje je konsola/apka/narzędzia)?
4. **Budżet czasowy:** ile czasu kalendarzowego na całą migrację i ile na pojedyncze okno prac (poza peakami / So-Nd)?
5. **Staging:** nie istnieje osobne środowisko (jest replay harness + shadow-mode + dry-run drivery). Czy shadow+replay wystarcza jako „staging" (rekomendacja: TAK, to już utarta praktyka flipów), czy budować coś więcej?
6. **Lint/typecheck (dev-only):** zgoda na dodanie ruff+mypy jako narzędzi deweloperskich POZA venv silnika (osobny venv narzędziowy; zero nowych zależności runtime)? Rekomendacja: TAK — bez tego refaktor „na lata" nie ma siatki.
7. **Koordynacja sesji:** potwierdź podział — ta sesja robi fazy 0-4 (tylko docs), FLIPMASTER (tmux 20) trzyma flipy/restarty; kroki Fazy 5 wchodzą dopiero po Twojej akceptacji planu i w uzgodnieniu z FLIPMASTEREM.

---
*Artefakt Fazy 0. Następny: `01-stan-obecny.md` (Faza 1) — po odpowiedziach i „dalej" od Adriana.*
