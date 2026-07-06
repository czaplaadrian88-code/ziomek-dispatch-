# SPRINT RÓWNOLEGŁY — SESJA B: Pakiet 3+4 bez K15 (K14 · K16 · K17)

**Wklej ten plik jako zadanie nowej sesji CC.** Kontekst: `docs/refaktor/00-05` (READ: 04-plan K14-K17, 05-dziennik Pakiet 0/1) + memory `ziomek-refaktor-architektura-2026-07-06`. Protokół #0 obowiązuje NAD wszystkim. Równolegle pracuje SESJA A (Pakiet 2, rdzeń `dispatch_pipeline`) — WASZE pliki są rozłączne; trzymaj się własności niżej.

## STAN ZASTANY (06.07 ~15:00 UTC — zweryfikuj żywo, ETAP 0)
Jak w spec-u A: master `3f4ed26`+, kanon 4294/0, ratchet 608/608, 4 flagi programu LIVE (WR/FLAG_SNAPSHOT/PRE_RECHECK/EFFECTS — jawne TAK Adriana), replayer działa (2 dowody), korpus rośnie (pełny `now` od 14:49).

## CEL SPRINTU B
- **K14 — bramka „re-plan nie pogarsza R6"** (`plan_recheck.py`): nowa sekwencja z `_sweep` przechodzi lekki check R6/committed vs stara; gorsza → zostaje stara + metryka `would_reject_reseq` do jsonl (measure-first!). Flaga `ENABLE_PLAN_RECHECK_FEAS_GATE` **OFF** (kod inertny; flip za ACK Adriana po 3-5 dniach shadow-metryki). Test charakteryzujący PRZED: przypadek „nowa sekwencja gorsza R6" (odtworzenie z komentarza `plan_recheck.py:~1019` — grepuj symbol).
- **K16 — hierarchia źródeł pozycji** (`courier_resolver.py`): JEDNO miejsce łączenia gps/pwa/last_pos z jawnym `pos_source` w budowie snapshotu (D7 od strony konsumenta; typ Known|Unknown minimalnie — F-3). Flaga `ENABLE_POS_SOURCE_HIERARCHY` OFF; kryterium: bajt-parytet snapshotu floty przy OFF (test) + ON≠OFF test. ⚠ ZNAJ klasę „równe traktowanie no-GPS" (protokół #0, 8 bliźniaków) — TEN krok NICZEGO w niej nie zmienia (tylko porządkuje odczyt źródeł); każde odstępstwo = STOP i pytanie do Adriana.
- **K17 — bramka korpusowa (finał K06):** (a) runner `tools/world_replay_gate.py` — iteruje po rekordach world_record z `now≠null` (okno konfigurowane), woła logikę `world_replay`, raport zbiorczy {n, zgodne, różnice, missy} → `dispatch_state/world_replay_gate_verdict.txt` + exit≠0 przy różnicach; (b) **bieg na korpusie z peakiem 06.07 17-20** (rano 07.07 korpus gotowy) → raport dla Adriana; (c) wpięcie do night-guard w trybie INFORMACYJNYM (bez exit 1) — eskalacja na egzekwujący = decyzja Adriana po 3 zielonych nocach. Night-guard = unit systemd: zmianę configu/unitu przygotuj jako plik w `systemd/` + instrukcja, INSTALACJA za ACK Adriana (zero samowolnych zmian w /etc).

## WŁASNOŚĆ PLIKÓW (twarda)
**TWOJE (jedyny pisarz):** `plan_recheck.py` · `courier_resolver.py` · `tools/world_replay.py` (utwardzenia) · NOWE `tools/world_replay_gate.py` · `systemd/` (pliki źródłowe night-guard) · testy `tests/test_*_k14|k16|k17*.py` · dziennik `eod_drafts/<data>/SPRINT_B_dziennik.md`.
**ZAKAZ (własność A):** `dispatch_pipeline.py`, `core/*`, `scoring.py`, `objm_lexr6.py`, **`common.py` — w tym ETAP4/consty/flagi: potrzebę rejestracji flag (K14/K16!) zgłoś sesji A wpisem w swoim dzienniku, sekcja „PROŚBY DO A" — A dopisuje, Ty konsumujesz przez `C.flag(name, False)` z literalnym defaultem do czasu rejestracji.** **READ-ONLY:** `feasibility_v2.py`, `route_simulator_v2.py`, `shadow_dispatcher.py`, conftest, flags.json (flip TYLKO Adrian).
⚠ K15 (wspólny Planner) = POZA sprintem — punkt scalenia po Pakietach 2 i 3, seryjnie.

## RYTM KROKU
Worktree WŁASNY: `git worktree add ../wt-sprintB -b refaktor/krok-14-reseq-gate master` + pkgroot (`../wt-sprintB-pkgroot/dispatch_v2→wt-sprintB` + symlinki `flags.json`, `logs`). Dalej identycznie jak spec A pkt 1-6: testy-przed → zmiana → **parytet korpusowy replayem przy OFF** (dla K14/K16: flaga OFF musi dawać 0 różnic na ≥30 decyzjach) → pełna regresja worktree + ratchet → **merge SERYJNY** (przed merge `git log -3` w kanonie; jeśli A świeżo zmergował → rebase + powtórka weryfikacji) → pełna suita KANONICZNA → push → dziennik B. KAŻDY git z jawnym `cd wt-sprintB &&` w tym samym bloku.

## SUBAGENCI
Read-only rekonesans (np. mapa writerów/readerów gps/pwa/last_pos przed K16 — tabela plik:linia) + testy przez subagentów na rozłącznych plikach. Edycje plan_recheck/courier_resolver — tylko główny wątek.

## STOP-Y
STOP po K14 (raport: metryka shadow + parytet) · po K16 · po biegu bramki K17 na peakowym korpusie (RAPORT PARYTETU dla Adriana — to jest formalna bramka K06 programu). Sygnały „przerwij" = 04-plan. Zero flipów/restartów/instalacji unitów bez jawnego TAK Adriana w czacie.

## KRYTERIUM KOŃCA SPRINTU B
K14+K16 na masterze (flagi OFF, dowody ON≠OFF + parytet-OFF na korpusie) · K17: runner na masterze + **raport parytetu z ≥1 pełnego peaku** + night-guard przygotowany (instalacja za ACK) · kanon zielony vs bieżący baseline · ratchet ≤608 · dziennik B kompletny.
