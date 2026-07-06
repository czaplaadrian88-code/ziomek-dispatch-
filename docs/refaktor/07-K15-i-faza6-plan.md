# 07 — K15 (punkt scalenia) + Faza 6 (domknięcie programu) — plan wykonawczy koordynatora

**Delegacja Adriana (06.07, czat tmux 21):** „jak będą kończyć pakiety, zrób K15 i domknij program". Wykonawca: sesja-koordynator (tmux 21). Start = wykrycie OBU markerów `=== SPRINT_A_KONIEC ===` i `=== SPRINT_B_KONIEC ===` w dziennikach sprintów (addendum wysłane do sesji 06.07 ~15:15).

## Mechanika czuwania
Łańcuch ogniw tła (limit 10 min/ogniwo): każde ogniwo = sleep ~9 min → grep markerów w `eod_drafts/*/SPRINT_{A,B}_dziennik.md` + sanity (`systemctl is-active dispatch-shadow`, świeży błąd w journalu → eskalacja do Adriana zamiast czekania). Oba markery → START K15. Jeden marker + drugi sprint utknięty >12 h → raport Adrianowi z rekomendacją.

## K15 — wspólny Planner dla ticku i plan_recheck (ADR-R03, finał D4)
**Warunek wstępny:** Pakiet 2 zmergowany (core/candidates z wydzieloną ścieżką route-sim+feasibility) + K14 zmergowany (bramka + metryka `would_reject_reseq` w shadow). Własność plików wraca do koordynatora (sesje zakończone) — rdzeń = jeden właściciel, seryjnie.
1. **ETAP 0:** świeży master, pełny kanon zielony vs bieżący baseline, ratchet, `git log` obu sprintów, odczyt metryki K14 z shadow (odsetek would_reject — wejście do kalibracji), worktree `wt-k15`.
2. **Budowa:** `core/planner.py` — jedno wejście `plan_bag(world, bag, kandydat)` (route-sim + feasibility-check ze ścieżki K11); `plan_recheck._sweep`/`_gen_one_bag_plan` → delegacja do Plannera za flagą `ENABLE_PLAN_RECHECK_VIA_CORE` (OFF; konfiguracja z FlagSnapshot — env-rozjazd drop-inów recanon przestaje mieć znaczenie dla tej ścieżki). Konsolidacja bliźniaka generatorów (kontrakt ①) — stara ścieżka zostaje pod OFF do czasu flipu.
3. **Dowody:** testy charakteryzujące sweep PRZED; OFF = bajt-parytet sekwencji na golden-fixturach + korpus-parytet replayem; ON w SHADOW ≥2 dni na żywych sweepach (obie ścieżki liczą, stara wykonuje, diff sekwencji → jsonl); pełna regresja; ratchet.
4. **Flip `ENABLE_PLAN_RECHECK_VIA_CORE` = TYLKO za jawnym TAK Adriana** (przedstawię werdykt shadow-parytetu). Rollback hot.

## Faza 6 — domknięcie programu (po K15; flip K15 może być „w toku obserwacji" równolegle z 1-3)
1. **Baseline końcowy vs wejściowy:** pełna suita + ratchet + porównanie z `00-baseline.md` (tabela przed/po: testy, naruszenia, rozmiar `_assess_order_impl`, liczba kopii reguł).
2. **Weryfikacja diagnozy:** tabela D1-D10 ze statusem ROZWIĄZANE/ZŁAGODZONE/OTWARTE + dowód per pozycja (test/plik:linia/metryka). Cel twardy: D o wpływie 4-5 (D1/D2/D4/D5/D8) — D8 świadomie OTWARTE (wariant C = decyzja biznesowa Adriana, poza programem).
3. **Aktualizacja map kanonu (master):** `docs/ARCHITECTURE.md` (nowe moduły core/, effects_buffer, world_record/replay, przepływ F-2), `docs/CODEMAP.md` (§2 nowe pliki, §3 lookup: world_replay/devlint/effects), `ZIOMEK_ARCHITECTURE.md` §4 rejestr bliźniaków (lex_qual już ✅; generatory planów → ✅ po K15; aktualizacja liczników kontraktów ①-⑧), `ZIOMEK_LOGIC_REFERENCE` (komplet flag programu).
4. **`docs/refaktor/06-raport.md`:** co zrobiono (K01-K17 + naprawy obce), dowody kluczowe (replay 1:1 ×N, parytet korpusowy bramki), co świadomie odłożone → BACKLOG z uzasadnieniem (czasówka w WR, poison-alert→powłoka, perf D6 profiling, route-order cross-repo, common.py dalsza rozbiórka, wariant C/multi-tenant, HA), rekomendacje na 3 miesiące.
5. **Scalenie dzienników:** `SPRINT_A/B_dziennik.md` → sekcje w `05-dziennik.md` (lub linki) + near-missy sprintów → propozycje do protokołu #0 (reguła: sesja, która znalazła lukę, wpisuje ją — sprawdzę czy A/B to zrobiły, w razie czego dopiszę za nie z atrybucją).
6. **Pamięć + push:** memory refaktoru → status ZAKOŃCZONY + wskaźnik na 06-raport; MEMORY.md linia; push master + refaktor/architektura; propozycja Adrianowi tagu `refaktor-program-2026-07` (tag = za jego OK).

## Zasady niezmienne
Zero automatów flipujących; każdy flip/restart/instalacja = jawne TAK Adriana; merge seryjny; parytet korpusowy = definicja „bez zmiany zachowania"; wątpliwość → STOP i pytanie, nie zgadywanie.
