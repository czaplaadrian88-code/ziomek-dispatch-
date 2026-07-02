# D.3 RECON — plan migracji flag env-frozen → flags.json+ETAP4 (do pod-ACK Adriana)

**Sesja tmux 8, 2026-07-02 noc. READ-ONLY recon (agent d3-recon, zweryfikowany przez koordynatora). ZERO zmian wykonanych — to jest PLAN.** Odłożone z fali L0.1 ([[ziomek-fala-l0-2026-07-01]] pkt 2).

## Ustalenie kluczowe (przesądza ryzyko całości)
**`dispatch-shadow` NIE importuje `plan_recheck` w ogóle** — `shadow_dispatcher.py` sięga po `panel_client` tylko leniwie (l.1547: `login()` + `fetch_order_details()`). Wszystkie 14 writer-flag route/kanon to env-frozen module-consty **wyłącznie w `plan_recheck.py`** (l.347-488 + 82); konsumenci w całości wewnątrz plan_recheck (poza offline-tools b_route_shadow/route_reorder_replay, które ustawiają WŁASNY env). `panel_watcher` importuje plan_recheck function-local (l.653/662/697/725/756) i woła `recanon_courier`/`redecide_courier` bramkowane na tych constach W PROCESIE panel-watchera.

**Skutek:** hipoteza „flaga ON w plan-recheck / OFF w shadow → migracja zmienia shadow" NIE materializuje się dla żadnej z 14. Migracja do flags.json=true = behawioralnie neutralna. **Prawdziwy hazard jest ODWROTNY:** `decision_flag()` (common.py:400) czyta flags.json → globals(common) → False i **NIE czyta env** — po migracji drop-iny są martwe; zapomnisz ustawić flags.json=true ORAZ stałą-fallback common.py=True → flaga spada OFF wszędzie naraz = regresja. Tego pilnuje ETAP4 (test ON≠OFF + inwariant + conftest).

## Typy procesów (który restart)
- `dispatch-plan-recheck`, `dispatch-carried-first-guard` = **oneshot timery** → zmiana łapie się next tick, bez restartu.
- `dispatch-panel-watcher` = Type=simple → **wymaga restartu** (raz na partię, off-peak).
- Po migracji flip WARTOŚCI = hot-reload flags.json, zero restartu (to jest zysk operacyjny migracji).

## 14+2 flagi route/kanon (env=1 w drop-inach plan-recheck / panel-watcher / carried-first-guard)
Profil wspólny: env-frozen `=="1"` default "0" w plan_recheck.py; osiągalność w shadow = **NIE**; migracja=true = bez zmiany zachowania; ryzyko NISKIE:
`ENABLE_GPS_FREE_ANCHOR` (l.347) · `ENABLE_GPS_FREE_ANCHOR_LAST_POS` (354) · `ENABLE_PLAN_REAL_PICKED_UP_AT` (359) · `ENABLE_PLAN_SEQUENCE_LOCK` (363; tylko p-r+guard) · `ENABLE_PLAN_CANON_ORDER_INVARIANTS` (368) · `ENABLE_NO_RETURN_TO_DEPARTED_PICKUP` (377) · `ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE` (394; tylko pw) · `ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP` (401; tylko pw) · `ENABLE_RECANON_ON_WRITE` (412; tylko pw) · `ENABLE_CARRIED_FIRST_RELAX` (425) · `ENABLE_CARRIED_AGE_TZ_FIX` (444) · `ENABLE_LEX_COMMITTED_WINDOW`+`_SHADOW` (457-458) · `ENABLE_RELAX_COLOC_PICKUP` (475) · `ENABLE_NONCARRIED_DROPOFF_REORDER` (488).

**⚠ 2× WYMAGA-DECYZJI (asymetria env):** `ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION` (l.389) i `ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH` (l.82) są ON w plan-recheck+guard, **BRAK w panel-watcher** → w pw czytają się OFF. Jeśli recanon/redecide w pw wchodzą w te gałęzie, migracja=true WŁĄCZY je w pw (spójność zapewne pożądana, ale to zmiana zachowania → replay + pod-ACK). Guard trzyma je ON tylko dla parytetu odczytu kotwic (read-only).

## USE_V2_PARSER
env-frozen `panel_client.py:93` default "0" (drugi odczyt: `parser_health_endpoint.py:689` — health-only); env: **tylko panel-watcher=1**. Gałąź decyzyjna = `parse_panel_html` (l.425-436) — woła ją decyzyjnie TYLKO panel-watcher; **shadow nie parsuje panelu tą ścieżką** (konsumuje stan pisany przez pw). Migracja=1 → v2 wszędzie, gdzie ktokolwiek woła parse_panel_html. **WYMAGA-DECYZJI, ryzyko średnie** (v1 ma znany latentny bug rollovera `46\d{4}`).

## Sprzężenie `ENABLE_V326_OR_TOOLS_TSP` + `ENABLE_V326_SAME_RESTAURANT_GROUPING`
env-frozen w **common.py** (l.2408 / 3211), **oba default "1"**, żaden unit ich nie nadpisuje → jednolicie ON we wszystkich procesach; brak w flags.json. Sprzężenie #13 POTWIERDZONE w kodzie: GROUPING buduje super-pickup (`route_simulator_v2.py:299`), deduplikuje go gałąź OR-Tools (l.436); GROUPING=ON przy OR_TOOLS=OFF (greedy/bruteforce) = double-insert. Migracja neutralna (jednolity ON), ale **PARA ATOMOWA — zawsze razem** + test sprzężenia.

## OUT-OF-SCOPE (NIE unifikować): INTENTIONAL_PER_PROCESS
`tools/flag_registry.py:122-127`: `ENABLE_PANEL_BG_REFRESH` (shadow=1/watcher=0 ZAMIERZONE) · `ENABLE_LGBM_SHADOW` · `ENABLE_LGBM_METRICS_READ` · `ENABLE_PENDING_POOL` · `ENABLE_OBJ_REPLAY_CAPTURE` · `ENABLE_LOADAWARE_SELECTION_SHADOW` · `PYTHONPATH`.

## Rekomendowana kolejność (fale; każda = pełny ETAP 0→7)
1. **Fala A (12 flag „bez zmiany"):** per flaga — klucz flags.json=true + stała-fallback common.py=True (intencja steady-state, L0.1) + `decision_flag()` w plan_recheck zamiast module-const + wpis ETAP4 + test ON≠OFF + usunięcie martwych drop-inów. Restart pw RAZ na całą partię (off-peak); plan-recheck/guard next tick. Standardowy ACK.
2. **Fala B:** para V326 OR_TOOLS+GROUPING atomowo (+ test pary).
3. **Fala C:** COMMITTED_PROPAGATION + LIVE_ETA_REFRESH — najpierw domknąć OPEN-1, potem **pod-ACK Adriana** (potencjalna realna zmiana pw).
4. **Fala D:** USE_V2_PARSER — mapa wołających + shadow-compare v1↔v2, **pod-ACK Adriana**, na końcu.

## OPEN (do domknięcia przed falą C/D)
1. Czy recanon/redecide w pw realnie wchodzą w gałęzie COMMITTED_PROPAGATION / LIVE_ETA_REFRESH (rozstrzyga: Fala C = zmiana czy no-op).
2. Pełna lista decyzyjnych wołających `parse_panel_html` poza pw (panel_html_parser / panel_roster / panel_detail_prefetch / dispatch_pipeline importują panel_client top-level).
3. Reachability dowodzona grepem importów (AST), nie runtime (świadomie nie importowano shadow_dispatcher — side-effect login). Dowód: brak top-level importu plan_recheck + shadow woła tylko login+fetch_order_details.

Źródła: `plan_recheck.py` (82, 347-488) · `common.py` (400, 2408, 3211) · `panel_client.py:93` · `route_simulator_v2.py:299/436` · `tools/flag_registry.py:122` · drop-iny `/etc/systemd/system/dispatch-{plan-recheck,panel-watcher,carried-first-guard}.service.d/`.
