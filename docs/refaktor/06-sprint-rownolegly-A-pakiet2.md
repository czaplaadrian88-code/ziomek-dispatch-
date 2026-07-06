# SPRINT RÓWNOLEGŁY — SESJA A: Pakiet 2 „Rdzeń jako moduł" (K09-K13)

**Wklej ten plik jako zadanie nowej sesji CC.** Kontekst programu: `docs/refaktor/00-05` (READ: 04-plan-migracji K09-K13, 05-dziennik sekcje Pakiet 0/1) + memory `ziomek-refaktor-architektura-2026-07-06`. Protokół #0 obowiązuje NAD wszystkim.

## STAN ZASTANY (06.07 ~15:00 UTC — zweryfikuj żywo na starcie, ETAP 0)
Master `3f4ed26`+ · kanon testów **4294/0** · devlint-ratchet 608/608 · **4 flagi programu LIVE za jawnym TAK Adriana:** `ENABLE_WORLD_RECORD` (nagrywa od 14:02), `ENABLE_FLAG_SNAPSHOT`, `ENABLE_PRE_RECHECK_BEFORE_POOL`, `ENABLE_EFFECTS_AFTER_DECISION` (od 14:48) · replayer `tools/world_replay.py` DZIAŁA (2 dowody co-do-znaku: 485907, 485914) · korpus `dispatch_state/world_record/` rośnie (rekordy od 14:49 mają pełny `now`).

## CEL SPRINTU A
`_assess_order_impl` (~3785 l.) → orkiestrator + moduły warstw, **bez zmiany zachowania** (bajt-parytet na korpusie po KAŻDYM kroku). Kroki wg 04-planu:
- **K09** `core/decide.py` + `core/world_state.py` — fasada `decide(world, order)` = czysta delegacja 1:1; wszystkie call-site'y (`shadow_dispatcher.process_event`, `czasowka_scheduler`, `auto_assign_gate`, `postpone_sweeper`, toole) przez fasadę.
- **K10** `core/gates.py` — geokod-defense + early-bird (+ rekurencyjny kontrfaktyk) `[PRZENOSINY]`.
- **K11** `core/candidates.py` — pętla per-kurier (`_v327_eval_courier_inner` ~2145 l.) `[PRZENOSINY]`.
- **K12** `core/selection.py` — selekcja + tiering + best_effort + bramki werdyktu `[PRZENOSINY]` + **NOWY re-assert `_assert_feasibility_first` na EMIT** (domknięcie INV-LAYER-HARD-BEFORE-SOFT, wzorzec #10).
- **K13** `Scorer` interface — `HeuristicScorer` (obecna suma kar; konsolidacja kar z common/pipeline do modułu scoringu) + `LgbmScorer` wrapper shadow z fail-soft fallbackiem + metryka `scorer_fallback`.

## WŁASNOŚĆ PLIKÓW (twarda — NIE dotykaj cudzych)
**TWOJE (jedyny pisarz):** `dispatch_pipeline.py` · nowe `core/decide.py|world_state.py|gates.py|candidates.py|selection.py` · `scoring.py` · `objm_lexr6.py` · **`common.py` (WYŁĄCZNOŚĆ na ETAP4/consty/flagi — sesja B zgłasza potrzeby TOBIE przez plik `eod_drafts/2026-07-0X/SPRINT_B_dziennik.md`, sekcja „PROŚBY DO A")** · `ZIOMEK_LOGIC_REFERENCE.md` (wiersze flag) · testy `tests/test_*_k09..k13*.py`.
**ZAKAZ (własność B):** `plan_recheck.py` · `courier_resolver.py` · `tools/world_replay.py` · night-guard/systemd. **ZAKAZ (wspólne-nietykalne):** `shadow_dispatcher.py` poza JEDNĄ zmianą call-site w K09 (uzgodnij moment merge z B — B go nie rusza, ale czyta), `feasibility_v2.py`/`route_simulator_v2.py` = READ-ONLY w tym sprincie (Planner = K15, POZA sprintem), conftest, flags.json (flip = TYLKO Adrian ACK, wykonawca jeden — domyślnie sesja A po uzgodnieniu w czacie Adriana).

## RYTM KROKU (bez wyjątków)
Worktree WŁASNY: `git worktree add ../wt-sprintA -b refaktor/krok-09-decide master` (+ pkgroot symlinki jak w 05-dzienniku „Środowisko worktree": katalog `../wt-sprintA-pkgroot/dispatch_v2→wt-sprintA` + symlinki `flags.json`, `logs`). Na każdy krok: świeża gałąź `refaktor/krok-NN-*` z AKTUALNEGO master.
1. Testy charakteryzujące PRZED (zielone na starym kodzie).
2. Zmiana (przenosiny = mechaniczne, treść 1:1).
3. **PARYTET KORPUSOWY:** `for oid in $(ostatnie ≥30 order_id z world_record z now≠null): tools/world_replay.py --order-id $oid` → **0 różnic, 0 missów** (to jest definicja „bez zmiany zachowania"). Rekordy sprzed 14:49 (now=null) pomijaj.
4. Pełna regresja z worktree (`ZIOMEK_SCRIPTS_ROOT=…-pkgroot`) + `tools/devlint/ratchet_check.py` zielony.
5. **MERGE SERYJNY:** przed merge `cd kanon && git log -3` (czy B nie zmergował przed chwilą → wtedy najpierw rebase gałęzi na świeży master + powtórka pkt 3-4); merge; **pełna suita KANONICZNA**; push. Po commicie `git show HEAD --stat` (C1-git).
6. Dziennik: dopisuj do **`eod_drafts/<data>/SPRINT_A_dziennik.md`** (NIE do 05-dziennik.md — unikamy konfliktu z B; scala Faza 6).
⚠ KAŻDA komenda gitowa z jawnym `cd /root/.openclaw/workspace/scripts/wt-sprintA &&` w TYM SAMYM bloku (near-miss cwd-drift z 06.07 — patrz 05-dziennik).

## SUBAGENCI
Read-only Explore do rekonesansu przenosin (mapowanie zależności bloku przed wycięciem); równoległe pisanie PLIKÓW TESTÓW przez subagentów OK (rozłączne pliki); edycje `dispatch_pipeline.py`/`core/*` — WYŁĄCZNIE główny wątek sesji (jeden pisarz). Zero subagentów z prawem zapisu do rdzenia.

## STOP-Y I ZAKAZY
STOP po K09+K10 (raport Adrianowi: diff-summary, parytet, delta rozmiaru monolitu) i po K13. Zakazy briefu programu: żadnych nowych zależności, kasowania plików, zmian kontraktów JSONL/state (to kontrakt publiczny — decyzja Adriana z Fazy 0), commitów bezpośrednio w master (tylko merge gałęzi), force-push. Flipy nowych flag (K13 Scorer?) = kod OFF + rejestracja ETAP4 + wpis LOGIC_REF; włączenie tylko Adrian. Sygnały „przerwij" = tabela w 04-planie (m.in. niespełnialny parytet po 2 podejściach → STOP i korekta planu, nie forsować).

## KRYTERIUM KOŃCA SPRINTU A
K09-K13 na masterze; `_assess_order_impl` = orkiestrator (docelowo ~kilkaset linii); parytet korpusowy 0-różnic na ≥100 decyzjach łącznie; kanon ≥4294/0 (baseline ruchomy — waliduj vs bieżący); ratchet ≤608; wpisy dziennika A kompletne (co/dlaczego/odstępstwa/dowody per krok).
