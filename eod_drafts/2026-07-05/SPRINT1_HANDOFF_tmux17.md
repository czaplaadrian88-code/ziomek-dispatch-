# HANDOFF → sesja tmux 17: SPRINT 1 „Fundament pod flipy" (start 05.07 wieczór → ~12.07)

> Od: sesja konsolidacyjna 05.07. Kontekst całości: `eod_drafts/2026-07-05/KONSOLIDACJA_STANU_0507.md` + memory `ziomek-status-konsolidacja-2026-07-05.md` + tracker `eod_drafts/2026-07-02/ZIOMEK_STAN_AUDYTY_1i2.md` (nagłówek 05.07).
> Cel sprintu: naprawić instrumenty (inwarianty VOID, serializer) i zbudować L5 — czyli wszystko, co FORMALNIE blokuje flipy Sprintu 2 (O2, PENDING_RESWEEP_LIVE, kalibracje). **Żadnych flipów w tym sprincie — tylko kod za flagami OFF + testy + dowody.**

## ⛔ ZASADY (bez wyjątków)
1. **Protokół #0**: `memory/ziomek-change-protocol.md` — wklej PROMPT, ETAP 0→7 per zadanie. Testy bazowe ZIELONE przed i po (`/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q`; ostatni zapisany baseline 4184/0 po L6.C — zweryfikuj żywy stan na starcie i zapisz swój baseline).
2. **ADR-007 multi-sesja**: worktree per zadanie, commit po JAWNYCH ścieżkach (nigdy `git add -A`), przed edycją `git log --oneline -10` + `git pull` na docs. Master ahead vs origin (push za Adrianem) — **NIE pushuj**.
3. **RÓWNOLEGLE pracuje tmux 15 (Sprint 0)** na: `courier_api/`, `tests/golden/`, `tools/ziomek_time_route_monitor*`, unitach `dispatch-cod-weekly*`. ⛔ **NIE dotykaj tych plików.** Tracker/todo_master edytujcie dopisując (append) i commitujcie od razu małymi commitami.
4. **ZERO flipów flag, restartów usług, deployów, pushów bez ACK Adriana.** Okna peak (Pn-Pt 11-14 i 17-20, So 16-21) = zero ciężkich operacji; **ciężkie replaye L5 puszczaj poza peakiem** (serwer 4 vCPU obsługuje żywy silnik).
5. ⚠ Świadomość deployowa: na masterze czeka już inertny kod L6.C (`d8328b2`) — najbliższy restart dispatch-shadow zbierze L6.C + Twoje zmiany RAZEM. Restart/flip = plan z Adrianem w Sprint 2, nie tutaj.
6. Nie koliduj z at-jobami Pn 06.07: at-205 12:40 (GC realny), at-206 14:30, at-208 19:30 — nie odwołuj, nie flipuj nic w ich oknach.
7. Po każdym domknięciu: wpis do trackera (`ZIOMEK_STAN_AUDYTY_1i2.md`, protokół na górze) + status w `memory/todo_master.md`. Sesja bez wpisu = NIEZAKOŃCZONA.

## ZADANIE 1 — A1-SERIALIZER: serializer −38 kluczy + INV-FLAG-CONFTEST-STRIP (effort: ŚREDNI; zrób PIERWSZE — odblokowuje kalibrację O2)
**Problem:** inwariant serializera = ⚠️VOID: serializer gubi 38 kluczy (14 HARD) — L1.1 (`85d92f7`, deny-lista `_METRICS_EXCLUDE`) domknęła część, ale stan VOID w dashboardzie `ZIOMEK_INVARIANTS.md` wskazuje, że kontrola NIE działa. To bramkuje kalibrację O2 (02.07: „napraw serializer PRZED"). Drugi VOID: INV-FLAG-CONFTEST-STRIP.
**Zrób:** ETAP 0 = ustal ŹRÓDŁO VOID (czemu strażnik nie mierzy: wyłączony? xfail? liczy zły plik?). Potem fix u źródła: **serializer A+B RAZEM** (`shadow_dispatcher._serialize_result` — obie lokalizacje: `_serialize_candidate` + inline best; mapa kompletności z protokołu #0) + test parytetu kluczy (każdy klucz decyzji obecny w jsonl; mutation-probe: usuń klucz → test PADA). Dla CONFTEST-STRIP: strażnik, że conftest nie odziera flags.json w testach mierzących flagi.
**Zakres plików:** `shadow_dispatcher.py` (TYLKO sekcje serializacji), `tests/` (nowe), `ZIOMEK_INVARIANTS.md` (status VOID→🟢TEST). ⛔ nie ruszaj `_tick`/logiki werdyktu/L6.C.
**DoD:** dashboard inwariantów: te 2 pozycje VOID→🟢, dowód liczbowy (grep świeżej decyzji: komplet kluczy), regresja pełna zielona.

## ZADANIE 2 — A1-INVARIANTS: carried_first_guard + global_allocate geometria VOID→TEST (effort: WYSOKI; po merge Zadania 1 — merge seryjny, najpierw Z1)
**Problem:** 2 pozostałe ⚠️VOID: `carried_first_guard` (parytet env — L0.2 robiła de-void, stan wrócił/niedomknięty?) i `global_allocate` geometria. VOID global_allocate **twardo blokuje flip `PENDING_RESWEEP_LIVE`** (bramka `live_gate_open()` z L6.C tego wymaga). Dalej: 21 pustych slotów 🔴 w kontraktach ①②③ (alokacja/feasibility) — w tym sprincie zacznij od kontraktu ① (alokacja).
**Zrób:** per inwariant ETAP 0 (czemu VOID), potem asercja/test z zębami (mutation-probe ×2 — wzorzec z L7.3). **TYLKO asercje/testy/strażniki — zero zmiany zachowania silnika.** Kod strażników za flagą OFF=inert jeśli w hot-path (wzorzec L7.1/L7.3).
**Zakres plików:** `ZIOMEK_INVARIANTS.md`, `tests/`, punkty zaczepienia w `plan_recheck.py`/`dispatch_pipeline.py` (tylko dodanie asercji; ⚠ L3 świeżo flipnięta — plan_recheck w oknie obserwacji, nic behawioralnego!). ⛔ nie ruszaj feasibility_v2/scoring (Zadanie 3 tam pracuje).
**DoD:** 4/4 VOID zlikwidowane (z Zadaniem 1), ≥5 slotów kontraktu ① zapełnionych, dashboard zaktualizowany, wpis w trackerze że bramka PENDING_RESWEEP_LIVE ma już podstawę pomiarową.

## ZADANIE 3 — A1-L5: budowa L5 ETA load-aware jako SHADOW (effort: WYSOKI; największe — można zacząć równolegle z Z1 w osobnym worktree)
**Problem:** L5 = jedyna NIEZBUDOWANA fala Fazy 3 (bramka 04.07 minęła). Korzeń K3: optymistyczny estymator poślizgu ODBIORU — bias med **−3,6 min**, rosnący ze scarcity (−5,1), tierem new (−6,4), skrajni kurierzy do −15 (`tools/eta_truth_map.py`, okno 28.06-02.07 n=554). Dostawa: bias ~0, ale rozrzut ±17 min.
**Zrób:** design wg zapisów fali L5 w audycie zunifikowanym (kalibracja na osi poślizgu odbioru, load-aware: obciążenie kuriera/scarcity/tier jako wejścia korekty). Implementacja za NOWĄ flagą `ENABLE_ETA_LOAD_AWARE` (default OFF) + **metryka shadow w shadow_decisions.jsonl** (stary vs skorygowany ETA per decyzja, bez wpływu na werdykt). Dowód: replay ≥3 dni — poprawa błędu ETA odbioru ON vs OFF (cel: bias med → ~0 bez pogorszenia p95), zmiany zwycięzcy policzone i ocenione. L4 już ON (04.07) — buduj na `resolve_available_from*` jako źródle dostępności, NIE twórz drugiego.
**Uwaga zależność:** Q2 feasibility „nie zdąży→nie dostaje" była odkładana „na F4/L5" (wpis L4 w trackerze) — zbadaj w ETAP 0, czy wchodzi w zakres, czy zostaje osobnym pasem; nie rozszerzaj scope bez zapisu.
**Zakres plików:** `scoring.py` + nowy moduł estymatora + `tests/` + tooling replay. ⛔ `feasibility_v2.py` NIETYKALNE (HARD); bliźniaki selekcji (`dispatch_pipeline` ↔ `objm_lexr6`) jeśli dotkniesz — RAZEM.
**DoD:** kod scalony flaga OFF, replay-dowód pozytywnego wpływu zapisany (plik werdyktu w dispatch_state/ jak `lexqual_geometry_replay_verdict.txt`), flip = rekomendacja za ACK w trackerze. Ciężkie replaye POZA peakiem.

## ZADANIE 4 — A1-SECURITY-PREP (effort: NISKI; tylko przygotowanie, wykonanie bramkowane Adrianem)
**Stan:** B4 LIVE, B1/B3 done 04.07. Skrypty staged w `dispatch_state/`: `apply_token_rotation.sh` (C1), `cdp_drop_reboot.sh` (@reboot = ręka Adriana), runbook `eod_drafts/2026-07-03/SECURITY_P0_RUNBOOK.md`.
**Zrób:** (a) zaprojektuj auth na `/stop` :8765 (kod staged za flagą/env, bez deployu); (b) przygotuj JEDNOSTRONICOWY plan czyszczenia tokenów z HISTORII git (BFG/filter-repo: co dokładnie, wpływ na ~6 sesji i worktree, sekwencja, rollback) — TYLKO dokument decyzyjny dla Adriana. ⛔ niczego nie wykonuj bez ACK; Hetzner FW krok 0 = wyłącznie Adrian.
**DoD:** kod auth staged + testy; dokument BFG w `eod_drafts/2026-07-05/`; wpis w todo_master #8b.

## Kolejność / kolizje wewnętrzne
Z1 → merge → Z2 (oba dotykają okolic shadow_dispatcher/testów inwariantów — SERYJNIE). Z3 równolegle od startu (osobny worktree, inne pliki). Z4 wypełniaczem. Kolizje z tmux 15: patrz zasada 3 — granica twarda.
