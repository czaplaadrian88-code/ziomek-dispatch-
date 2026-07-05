# HANDOFF → trzecia sesja (tmux 18): SPRINT 2.5-PREP — higiena + pomiar perf (05.07 → ~09.07)

> Od: sesja konsolidacyjna 05.07. Kontekst: `eod_drafts/2026-07-05/KONSOLIDACJA_STANU_0507.md` + memory `ziomek-status-konsolidacja-2026-07-05.md`.
> Cel: zadania plikowo ROZŁĄCZNE od Sprintu 0 (tmux 15) i Sprintu 1 (tmux 17), które przygotowują grunt pod Sprint 2 (okno flipów ~08-09.07) bez dotykania silnika. **To NIE jest Sprint 2 — żadnych flipów, żadnych zmian w rdzeniu.**

## ⛔ ZASADY (bez wyjątków)
1. **Protokół #0** (`memory/ziomek-change-protocol.md`) per zadanie zmieniające kod; testy bazowe zielone przed/po (`/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q`).
2. **ADR-007**: worktree per zadanie, commity po JAWNYCH ścieżkach, `git log -10` przed edycją, **NIE pushuj** (master ahead, push za Adrianem).
3. **RÓWNOLEGLE pracują:** tmux 15 (Sprint 0: `courier_api/`, `tests/golden/`, `tools/ziomek_time_route_monitor*`, unity `dispatch-cod-weekly*`) i tmux 17 (Sprint 1: `shadow_dispatcher.py`, `scoring.py`+nowy estymator, `plan_recheck.py`/`dispatch_pipeline.py` [asercje], `ZIOMEK_INVARIANTS.md`, testy inwariantów). ⛔ **ŻADNEGO z tych plików nie dotykasz.** Tracker/todo_master: dopisuj (append) i commituj od razu.
4. **ZERO flipów, restartów, deployów, pushów bez ACK Adriana.** Peak (Pn-Pt 11-14/17-20, So 16-21): zero obciążających operacji na żywym systemie. Nie koliduj z at-205/206/208 (Pn 06.07).
5. Po domknięciu każdej części: wpis do trackera `eod_drafts/2026-07-02/ZIOMEK_STAN_AUDYTY_1i2.md` + `memory/todo_master.md`.

## ZADANIE 1 — P-PERF: pomiar ogona peak p95 (effort: ŚREDNI; READ-ONLY — zero zmian kodu produkcyjnego)
**Kontekst:** po flipie PERF_LAZY p50 −27%, ale **peak p95 1810 ≈ baseline 1847** → ogon w peaku ma INNE źródło niż IO (werdykt at-207, `eod_drafts/2026-07-03/perf_verdict_at207.md`). Kandydaci: OR-Tools (200ms ceiling per call), OSRM, pool/równoległość, sufit 4 vCPU. `ENABLE_PERF_SLO_ALERT=true` już zbiera breache.
**Zrób:** measure-first: (a) analiza istniejących danych (`tools/perf_budget_report.py`, shadow_decisions latency, logi SLO-alertu) — dekompozycja p95 peaku na komponenty (TSP/OSRM/IO/inne) per decyzja; (b) jeśli trzeba dodatkowej telemetrii — TYLKO offline na replayu, nie w żywym procesie; (c) raport: gdzie siedzi ogon, 2-3 hipotezy naprawcze z szacunkiem zysku, rekomendacja dla fali perf (Sprint 3). Pomiary na żywych logach — czytanie OK zawsze; cięższe przeliczenia poza peakiem.
**Zakres:** `tools/` (nowe skrypty analityczne OK) + raport w `eod_drafts/2026-07-05/`. ⛔ zero zmian w plikach silnika.
**DoD:** `PERF_TAIL_DIAGNOSIS_raport.md` z liczbami + wpis w trackerze (kandydat fali P4).

## ZADANIE 2 — P-FLAGREG: rejestr flag (effort: ŚREDNI)
**Kontekst:** INV-FLAG-REGISTRY niedomknięty: **112 flag POZA rejestrem + 5 dead-flag** (dashboard `ZIOMEK_INVARIANTS.md`); rejestr z L0.1 (438 pozycji) istnieje, fingerprint-guard żywy. Miny flagowe = korzeń K6.
**Zrób:** (a) dorejestruj 112 flag (źródło: grep `C.flag(`/`flags.json`/env; per flaga: właściciel-warstwa, default, LIVE/shadow/dead, doc-ref) — mechanicznie, partiami z commitami; (b) 5 dead-flag → lista GC z dowodem śmierci (grep zero konsumentów) — **samo kasowanie za ACK**, tu tylko lista; (c) upewnij się, że ratchet `test_flag_doc_coverage` obejmuje nowe wpisy.
**Zakres:** rejestr flag (plik z L0.1 — znajdź przez CODEMAP/`tools/`), `ZIOMEK_LOGIC_REFERENCE.md` (doc flag), `tests/` (tylko testy rejestru). ⛔ NIE zmieniaj wartości w flags.json; NIE dotykaj kodu czytającego flagi.
**DoD:** braki 112→0 w checkerze, lista GC-kandydatów za ACK, wpis w trackerze (finding L0.1 domknięty w 100%).

## ZADANIE 3 — P-BRANCHGC: WD-14 porządek gałęzi (effort: NISKI)
**Kontekst:** audyt 14 wskazał **40 gałęzi do kasacji** (`b0181ed`, raport w `docs/audyt/`); gałęzie `fix/*` z fal zostają wg wcześniejszych decyzji.
**Zrób:** zweryfikuj listę 40 na żywo (czy zmergowane: `git branch --merged master`), oznacz rozbieżności, przygotuj JEDEN skrypt kasujący z listą jawną — **wykonanie za ACK Adriana** (kasowanie gałęzi = nieodwracalne bez refloga). ⛔ nie ruszaj worktree aktywnych sesji (`git worktree list` najpierw!).
**DoD:** skrypt + lista zweryfikowana w `eod_drafts/2026-07-05/`, wpis w todo_master.

## ZADANIE 4 — P-EXCEPT: bare-except → log+sentinel, TYLKO poza silnikiem (effort: NISKI-ŚREDNI; wypełniacz)
**Kontekst:** ~88 bare `except:` + ~135 silent `pass` maskują błędy (tech_debt P2).
**Zrób:** partiami po ~15-20 z pełną regresją po każdej partii, **WYŁĄCZNIE w plikach poza zakresami tmux 15/17 i poza rdzeniem decyzyjnym**: zacznij od `tools/`, `daily_accounting/`, mostów i skryptów pomocniczych. ⛔ NIE dotykaj: dispatch_pipeline, feasibility_v2, scoring, shadow_dispatcher, plan_recheck, plan_manager, courier_resolver, courier_api, panel_watcher, telegram_approver (bot uśpiony, ale plik nietykalny bez ACK). Rdzeń zostaje na osobną falę PO zakończeniu S1.
**DoD:** per partia: diff + regresja zielona + commit; licznik w trackerze (np. 88→X).

## Kolejność
Z1 (perf) najpierw — największa wartość dla planowania Sprintu 3. Z2 równolegle lub po. Z3/Z4 wypełniacze. Wszystko przygotowawcze — Sprint 2 właściwy (flipy) rusza ~08-09.07 po zielonym S1 + werdykcie 5b + ACK-ach Adriana, w INNEJ sesji.
