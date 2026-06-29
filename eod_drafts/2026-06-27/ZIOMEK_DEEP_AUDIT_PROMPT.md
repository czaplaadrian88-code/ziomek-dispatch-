# ZIOMEK — PROMPT do szeroko zakrojonego, dogłębnego audytu (read-only)

> Cel (Adrian, 2026-06-27, sesja nocna autonomiczna): prześwietlić Ziomka (dispatch_v2) szeroko i głęboko,
> znaleźć **dług techniczny, bugi, łączenia między warstwami, braki**, oraz **uzupełnić wiedzę** tak, żeby
> kolejne sesje DOKŁADNIE wiedziały co się dzieje, co jest istotne, czego nie wolno pomijać, jak reguły
> się przeplatają i jak poruszać się po systemie. Wynik karmi `memory/ziomek-change-protocol.md` (Przykazanie #0).

## Zasady twarde dla audytora (NIE łam)
1. **READ-ONLY.** Zero zmian kodu Ziomka, zero restartów, zero flipów flag, zero komend mutujących stan
   (panel/telegram/at/systemctl start|stop|restart). Wolno: `Read`, `grep`, `git log/show/blame`, `atq`,
   `systemctl list-timers/cat/show`, czytanie `/etc/systemd/**`, `flags.json`, logów. Audyt = raport + wiedza.
2. **Stan flagi = EFEKTYWNY w procesie**, nie env-default ani sam `flags.json`. Ustal który SERWIS odpala kod
   (`grep -rln run_X /etc/systemd/system`), czytaj jego `Environment=` + drop-iny `*.service.d/*.conf` +
   `FLAG_FINGERPRINT` z logu TEGO procesu. (Wzorzec #9 protokołu — np. `PLAN_SEQUENCE_LOCK` ON drop-inem pod
   `dispatch-plan-recheck`, NIE shadow.)
3. **„To tylko display" UDOWODNIJ grepem każdego konsumenta** (scoring? feasibility/hard-reject? committed/time_arg?
   inny solver?). Pole pokazywane w UI bywa zmienną decyzyjną (klasa `eta_pickup_utc`). (Wzorzec #8.)
4. **Adwersaryjna weryfikacja** każdego nietrywialnego findingu: większość „oczywistego martwego kodu" / „oczywistych
   bugów" UPADA przy weryfikacji (patrz `ZIOMEK_STRATEGIC_AUDIT_2026-06-23.md §9`). Domyślaj się że finding jest
   FAŁSZYWY, dopóki kod nie udowodni inaczej. Cytuj `plik:linia`.
5. **Tier-aware R6:** cap NIE jest płaski 35. T1/2=35 HARD, T3 cap-stretch=40. Każda logika overage/świeżości musi być
   tier-aware. **Always-propose:** Ziomek nigdy „BRAK KANDYDATÓW"; sentinel best-effort = POPRAWNE, nie bug.
   **R27 ±5 = SOFT.** **R6 ready-anchor** (od `pickup_ready_at`, nie TSP `pickup_at`) — znana luka w
   `_count_sla_violations` + `feasibility_v2:~1156` (sprint O2).
6. Istniejące dokumenty (`ZIOMEK_LOGIC_REFERENCE.md`, `ZIOMEK_MASTER_KB.md`, `TECH_DEBT.md`,
   `ZIOMEK_STRATEGIC_AUDIT_2026-06-23.md`, `memory/tech_debt_backlog.md`) traktuj jako **priors do weryfikacji**,
   nie prawdę. Część jest snapshotami (statyczne od 2026-05-10). Szukaj NOWEGO i potwierdzaj/obalaj stare.

## Repo / ścieżki
- Kod: `/root/.openclaw/workspace/scripts/dispatch_v2/`
- Flagi: `/root/.openclaw/workspace/scripts/flags.json` (220 kluczy)
- Live unity/timery: `/etc/systemd/system/dispatch-*`
- 10 warstw szkieletu: wejście → geokod(HARD) → early-bird(HARD) → telemetria → `check_feasibility_v2`(HARD) →
  scoring+19 kar(SOFT) → selekcja(SOFT) → werdykt KOORD(HARD) → zapis+kanon → konsola/apka/Telegram.

## Zakres (lanes) — każdy lane czyta GŁĘBOKO swoje pliki i zwraca: findings + navigation_notes + rule_interactions
1. pipeline-core (`dispatch_pipeline.py`, `scoring.py`, `wave_scoring.py`, `pln_objective.py`)
2. feasibility-hard (`feasibility_v2.py` — R6/anchor/tier caps/HARD gates)
3. route-sim-tsp (`route_simulator_v2.py`, `tsp_solver.py`, `chain_eta.py`, `insertion_anchor.py`, dwell)
4. osrm-traffic (`osrm_client.py`, `traffic_v2_aggregator.py`, `drive_min_calibration.py`)
5. plan-recheck (`plan_recheck.py`, `plan_manager.py`, `route_podjazdy.py`, kanon, dual-service)
6. shadow-serializer (`shadow_dispatcher.py`, `_AUTO_PROP_PREFIXES`, LOCATION A/B, propagacja metryk)
7. telegram-koord (`telegram_approver.py`, `pending_proposals_store.py`, `pending_pool*.py`, werdykt KOORD)
8. courier-resolver-gps (`courier_resolver.py`, `gps_quality.py`, last-known-pos, checkpoint TZ, `courier_ranking.py`)
9. panel-ingest-state (`panel_watcher.py`, `panel_client.py`, `state_machine.py`, parsery)
10. czasowka (`czasowka_scheduler.py`, `czasowka_proactive/`, `postpone_sweeper.py`)
11. console-app-parity (silnik↔konsola↔apka; panel `fleet_state`, `route_podjazdy`; display-feeds-decision)
12. ml-calibration (`ml_inference.py`, `validation_gate_lgbm.py`, `auto_proximity_classifier.py`, eta_calib*)
13. flags-config-systemd (`flags.json`, `common.py` flag-machinery, ETAP4_DECISION_FLAGS, env-frozen, dead flags, units)
14. twin-path-divergence (cross-cut: best_effort↔objm_lexr6, feasibility↔greedy↔plan_recheck, serializer A+B, lex_qual ×3)
15. recanon-symmetry (cross-cut: assign/deliver/pickup/cancel + P-5 cancel-bez-recanon)
16. metric-serialization-gaps (cross-cut: computed-not-serialized)
17. tests-shadow-coverage (`tests/` 401 plików, xfails, conftest, shadow-jobs reconcile, .bak/dead-code)
18. data-state-integrity (`orders_state.json`, `global_alloc_store.py`, pending stores, snapshot/reconcile, clobber)

## Klasyfikacja findingów
category ∈ {bug, tech-debt, cross-layer-coupling, gap, knowledge-gap, dead-code, risk}
severity ∈ {P0 (silent corruption/decision-corruption/incident-debug-kłamie), P1 (recurring/deadline), P2 (ulepszenie/blocker ramp-up), P3 (cleanup)}
Każdy finding: tytuł, plik:linia, dowód (co przeczytano/grep), wpływ, cross_layer?, twin_paths, protocol_relevant? +
protocol_note (co dopisać do Przykazania #0), rekomendacja (read-only — co kolejna sesja ma zweryfikować/zrobić przez
protokół), confidence.

## Deliverables
A. `eod_drafts/2026-06-27/ZIOMEK_DEEP_AUDIT_REPORT.md` — zweryfikowane findingi, posortowane severity, per-kategoria,
   z mapą cross-layer i twin-paths.
B. `eod_drafts/2026-06-27/ZIOMEK_AUDIT_PROTOCOL_ENRICHMENT.md` — propozycja additive do Przykazania #0:
   mapa nawigacji systemu, graf interakcji reguł, „czego nie wolno pomijać", gotchas, nowe wzorce zmiany-częściowej.
C. Aktualizacja `memory/` (additive, z backupem) — pointer w MEMORY.md + ewentualne dopiski do protokołu (oznaczone,
   z adnotacją „z audytu nocnego — zweryfikuj przed użyciem jako pewnik").
