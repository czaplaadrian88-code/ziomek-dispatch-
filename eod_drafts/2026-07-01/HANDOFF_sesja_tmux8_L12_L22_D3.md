# HANDOFF — sesja tmux 8 (noc 01/02.07): L1.2 + L2.2(+L2.3) + D.3-recon

**Masz GO Adriana na START PRACY w tym zakresie** (multi-agent dozwolony wg zasad niżej). Flipy/restarty NADAL za osobnym ACK. Koordynatorem fali L0 była sesja tmux 7 (zamknęła robotę ~22:35; commity `e41d598..131b555`).

## READ ORDER (zanim tkniesz cokolwiek)
1. Memory (auto-załadowane): wpis **[[ziomek-fala-l0-2026-07-01]]** + relay **[[ziomek-unified-audit-2026-06-30]]** (sekcja FAZA 3 — aktualny stan fal) + **[[ziomek-change-protocol]]** (PROMPT ETAP 0→7 — WKLEJ przy L2.2, to zmiana silnika).
2. `dispatch_v2/ZIOMEK_ARCHITECTURE.md` + `ZIOMEK_INVARIANTS.md` (kanon; INV-FEAS-R6-ONE-SOURCE świeżo przespecyfikowany — „tier" ≠ eskalacja!).
3. Raport buildu L0.3 (lista konsumentów do przepięcia) — sekcja niżej ma wyciąg; źródło prawdy = świeży grep.

## ⛔ 5 ZASAD RUCHU (twarde, na całą noc/poranek)
1. **ZERO restartów serwisów silnika do porannej weryfikacji** (L1.1 liczy świeże decyzje od restartu 01.07 21:27 — restart zeruje okno). Deploy L2.2 = najwcześniej po bramce O2 + weryfikacji L1.1, off-peak >14:00, jednym zbiorczym restartem, za ACK Adriana.
2. **Obszar O2/bundle_calib/R6 ZAMROŻONY do bramki** (review-timer 07:00, at-168 08:00). `bundle_calib_review` (własny join gps_truth) przepinasz DOPIERO PO 08:00. Nie dotykaj at-jobów 168/200/201.
3. **Panel (`nadajesz_clone/panel`, `/var/www/html`) = strefa tmux 2** (robi zakładkę analiz) — nie wchodź tam w ogóle.
4. **L3 (plan_recheck) NIE startuje** — czeka na ~dzień baseline'u zdrowego `carried_first_guard` (env naprawiony 22:31) + `pickup_floor_guard` (żywy od 22:31). Nie zaczynaj też L4/L5/L6.
5. **Multi-sesja/multi-agent:** agenci recon = read-only; buildy = rozłączne pliki, agenci BEZ KOMEND GIT (commituje koordynator sesji, jawnie po ścieżkach, po `tmux ls` + `git log -3` + `git status` na cudze zmiany). flags.json nie dotykać. Telegram nie dotykać. Linie DRYFUJĄ — świeży grep przed edycją.

## ZADANIE A — L1.2: przepięcie werdykt-tooli na `tools/ledger_io.py` (READ-side kanon, fala L0.3)
**Po co:** naiwny odczyt żywego `shadow_decisions.jsonl` gubi 29% okna 7d (497/1707 oid); 2 toole czytają MARTWE źródło. Kanon istnieje od dziś: `ledger_io` (iter rotation-aware + max_bytes-ogon, loadery prawd, join z etykietą physical_gps/button_proxy/none, `require_join_coverage` fail-loud, TTL-header). Testy wzorcowe: `tests/test_ledger_io.py`.

**Poprzeczka jakości (ustawiona przez l21_flip_review w fali L0):** każde przepięcie = **dowód bajt-identycznego wyniku** na dzisiejszym (nieprzerotowanym) oknie — stary kod (backup w scratchpadzie) vs nowy, liczby identyczne. Tool z at-joba/timera = klasa „artefakt-werdykt" (protokół C9): NIE zmieniaj semantyki werdyktu, tylko źródło odczytu.

**Kolejność wg ryzyka (z raportu L0.3; zweryfikuj świeżym grepem):**
- **TIER 1 (najpierw):** (1) `no_gps_eta_error.py` + `prep_bias_r6_replay.py` — WRONG-SOURCE: czytają MARTWY `dispatch_state/sla_log.jsonl` (zamrożony 20.06); kanon = ŻYWY `scripts/logs/sla_log.jsonl` (`ledger_io.LEDGER["sla"]`; jeśli potrzebny iter_sla — dodaj do ledger_io wzorem istniejących loaderów + test); (2) `pickup_slip_monitor.py` (karmi bramkę load-aware ETA 04.07!); (3) `daily_rule_report.py` (raport Adriana); (4) `objm_lexr6_peak_verdict` (at-200 03.07 18:10 — szczególna ostrożność, bajt-parytet OBOWIĄZKOWY); (5) `bundle_calib_review.py` własny join → **PO bramce 08:00**.
- **TIER 2:** replaye rotation-blind: `extract_bias_score_margins`, `monitor_later_promises`, `nogps_preshift_bucket_replay`, `obj_fresh_verdict_atrun`, `measure_bug1_eta_vs_freeat`, `post_shift_overrun_forward_replay`, `verify_obj_f1/f2/f4`, `verify_pickup_floor_peak`.
- **TIER 3:** ~15 tooli z hardcoded `[jsonl, jsonl.1]` (gubią `.2.gz`) → swap na ledger_io/_rotated_logs.
**Zasady:** tick-strażniki → `iter_shadow_decisions(cutoff, max_bytes=16MB)`; one-shot/at-joby → pełna ścieżka bez max_bytes. Konsola/apka/panel — NIE dotykać. Po całości: pełna regresja vs baseline **3683 passed / 9 xfailed** (te 9 xfailed = 4 stare + 5 slotów L0.4, NIE ruszać).

## ZADANIE B — L2.2 (+L2.3): catch-all `_v328_eval_safe` ROZRÓŻNIA — BUILD ONLY (flip za ACK)
**Po co (roadmapa L2.2, most K5):** dziś catch-all połyka wszystko jednakowo — trucizna danych, realny bug i infeasible wyglądają tak samo (cichy drop kuriera). Po L2.1 (flaga `ENABLE_COORD_SENTINEL_INGEST_GUARD` ON od 21:29) ingest jest czysty, ale resztkową truciznę i realne bugi trzeba WIDZIEĆ osobno.
**Zakres:** (a) `_v328_eval_safe` klasyfikuje przyczynę: data-poison (sentinel/coords — telemetria `coord_poison_*` z L2.1 jako sygnał) / real-bug (nieoczekiwany wyjątek) / infeasible (legalny brak); (b) ZBIORCZY operator-alert na data-poison (nie spam per-zdarzenie; wzór zbiorczości = istniejące alerty); (c) **L2.3 przy okazji, jeśli czyste:** `is_on_shift` fail-open → `log.warning` (wzór FAIL12; koniec cichego 24/7). **Rygor:** pełny protokół ETAP 0→7 (wklej PROMPT!), nowa flaga default OFF + ETAP4 + stała-fallback + test ON≠OFF + serializacja metryki + pełna regresja. **ŻADNEGO flipa/restartu tej nocy** — kod inert, propozycja flipa rano dla Adriana (prosty polski CO/WPŁYW/JAK BEZPIECZNIE).

## ZADANIE C — D.3 recon (READ-ONLY, zero zmian)
Przygotowanie migracji env-frozen→flags.json+ETAP4 (odłożonej z L0.1): per flaga **reachability-verify** — (1) czy kod czytający 14 writer-flag (drop-iny plan-recheck/panel-watcher) jest OSIĄGALNY w dispatch-shadow (import-graf + call-graf); (2) `USE_V2_PARSER`: czy shadow parsuje DECYZYJNIE czy tylko health_check (panel_client.py:93); (3) `ENABLE_V326_OR_TOOLS_TSP`+`ENABLE_V326_SAME_RESTAURANT_GROUPING` — potwierdź sprzężenie (flip jednej = double-insert, wzorzec #13) i że oba są "1" we wszystkich unitach. NIE unifikować `INTENTIONAL_PER_PROCESS` (flag_registry, common ~122-127). Deliverable: plan migracji per flaga z ryzykiem + co wymaga którego restartu — do pod-ACK Adriana.

## DoD sesji
Każde przepięcie z dowodem bajt-parytetu · L2.2 kod+testy+flaga OFF inert · D.3 raport · pełna regresja vs 3683/9xf · commity jawne ścieżki · wpis do memory (topic file + relay [[ziomek-unified-audit-2026-06-30]] FAZA 3 + linijka w MEMORY.md) · entropy_dashboard re-run (metryka wrong-source 2→0 ma się ruszyć w #3-void po L1.2) · rollback per commit gotowy. Częściowe = niezakończone. Wątpliwość → pytaj Adriana, nie zgaduj.
