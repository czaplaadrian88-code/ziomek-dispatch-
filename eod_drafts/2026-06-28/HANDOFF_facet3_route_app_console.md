# HANDOFF → sesja „bug tras dostawczych" — faseta #3 (rozjazd konsola↔apka) JEST OTWARTA

**Od:** sesja AUDYT (nocny deep audit Ziomka, read-only).
**Do:** sesja pracująca nad „Sprawdzić bug z przydzielaniem tras dostawczych".
**Data:** 2026-06-28. **Status:** to NIE jest „parytet konsola↔apka już LIVE" — to otwarte **P2** (`cap-carried-relax-app-console-divergence`).

## Skąd ten handoff
Adrian poprosił o cross-check między sesjami. Zgadzamy się w 100% co do Twoich fasety #1 (re-opt churn → bug4 WAIT/NO) i #2 (carried-vs-coloc / O2 cap-Z → 02.07, fix `ENABLE_O2_READY_ANCHOR_SWEEP` zbudowany za flagą OFF). **Rozjazd jest tylko przy fasecie #3**, którą zamknąłeś jako „parytet LIVE".

## DOWÓD że #3 jest otwarta (liczba z monitora, nie lektura kodu — reguła C4)
`dispatch_state/ziomek_time_route_monitor.jsonl` → **74 rekordy `q3_route_mismatches` DZIŚ** (28.06). Przykład żywy:
- **cid 123 (Bartek), 12:03:** konsola = `[pickup 483911, pickup 483944, dropoff 483877(carried), dropoff 483944, dropoff 483911]`; **apka = `[dropoff 483877(carried) NAJPIERW, pickup 483911, pickup 483944, …]`**.
- Plan Bartka = `incremental v2294`, **NIE invalidated** → to NIE jest case `DROP_GEOMETRY_ON_INVALIDATED` (ten faktycznie jest LIVE, ale to fix wewnątrz konsoli, nie parytet konsola↔apka).

44–75 worków/dzień (monitor `q3_route_mismatches`, 25–28.06). To kolejność JAZDY kuriera, nie kosmetyka (reguła C8 — może rozjechać committed pickup R27).

## ROOT CAUSE (zweryfikowane u źródła, żywy stan flag z proc/drop-inów)
1. **`route_podjazdy.order_podjazdy:153-156`** twardo dowozi WSZYSTKIE niesione (picked_up) na sam przód: `order = [("dropoff",[oid]) for o in carried]` — **brak gałęzi raw-canon** (`grep TRUST_CANON/_order_from_plan_seq` w route_podjazdy = pusto). Strukturalnie nie potrafi wstawić niesionego po odbiorze.
2. **Apka idzie TĄ ścieżką, nie `_brute_optimize`.** `courier_orders.build_view:1105` `if config.APP_ROUTE_FROM_CONSOLE and _route_podjazdy is not None and mine:` → woła `route_podjazdy.order_podjazdy` i ustawia `_console_done=True`. `ENABLE_APP_ROUTE_FROM_CONSOLE=1` LIVE (proc courier-api + `podjazdy.conf`). `order_podjazdy` nie potrzebuje GPS, więc niekompletny GPS Bartka jej nie wywraca. **`_brute_optimize` (l.288/294) to FALLBACK** (tylko gdy `not _console_done`) — stąd Twoja notatka „apka liczy geograficznie" celuje w niewłaściwą ścieżkę dla kuriera z workiem.
3. **Konsola to OSOBNY bliźniak** `fleet_state._order_from_plan_seq` (319-343), bramka `trust_canon_ok and flag("TRUST_CANON_ORDER")`, `PANEL_FLAG_TRUST_CANON_ORDER=1` LIVE → renderuje kanon verbatim z relaxem („odbierz po drodze zanim dowieziesz niesione"). Silnik relax: `ENABLE_CARRIED_FIRST_RELAX=1` LIVE (dispatch-panel-watcher).
4. **Flaga która MIAŁA to naprawić jest MARTWA (reguła C5).** `ENABLE_BUILD_VIEW_TRUST_CANON_ORDER=1` LIVE, ale jej jedyny konsument `courier_orders.py:1146` siedzi pod `if not _console_done and _plan_is_active(plan):` — a branch z l.1105 ustawia `_console_done=True` wcześniej → gałąź nieosiągalna na żywej konfiguracji.

Czyli: 22.06 dodano carried-first-relax tylko do JEDNEJ z dwóch powierzchni (konsola dostała `_order_from_plan_seq`, `route_podjazdy` nie) — klasyczny twin fix-in-1-of-2 (anti-wzorzec #11). Docstring `route_podjazdy` „konsola deleguje tutaj" jest fałszywy (konsola ma własną kopię) — reguła C7.

## JAK DOKOŃCZYĆ z najwyższą starannością (PRZEZ PROTOKÓŁ, ETAP 0→7)
- **ETAP 0:** multi-sesja — ja (audyt) jestem **read-only i NIE ruszam route_podjazdy/courier_orders/fleet_state**, więc #3 jest Wasze, brak kolizji. Odczytaj at-review/`ziomek_time_route_monitor` jako baseline.
- **ETAP 1 (źródło, nie objaw):** fix w `route_podjazdy.order_podjazdy` — dodaj gałąź raw-canon = LUSTRO `fleet_state._order_from_plan_seq` (renderuj `plan_seq` wprost gdy plan pokrywa cały worek; pokrycie sprawdzane jak w konsoli `cov_drop>=need_drop and cov_pick>=need_pick`), bramkowaną tą samą intencją relaxu. NIE łataj rendera apki.
- **ETAP 3 (mapa kompletności — bliźniaki RAZEM, reguła C7):** kolejność trasy żyje w 3–4 kopiach: silnik `plan_recheck._apply_canon_order_invariants` ↔ konsola `fleet_state._order_from_plan_seq`/`_build_route` ↔ apka `route_podjazdy.order_podjazdy` ↔ `courier_api_panelsync/courier_orders.py`. Albo wszystkie razem, albo **unifikacja na jedną funkcję** (lepiej — kasuje dryf). Posprzątaj martwą `BUILD_VIEW_TRUST_CANON_ORDER` (żeby nie było flag-widm) + popraw kłamiące docstringi (route_podjazdy:3-6, courier_orders:1101).
- **ETAP 4 (dowód nie deklaracja):** test ON≠OFF na kolejności w scenariuszu relax (carried + kolokowany odbiór) + **parytet zmierzony: `q3_route_mismatches → 0`** na świeżym oknie. Udowodnij grepem, że `stop_sequence` to execution-order (slip committed R27), nie display (reguła C8).
- **ETAP 5:** replay ON↔OFF — apka == konsola == kanon na korpusie relax; brak regresji R27/R6.
- **ETAP 6:** backup→py_compile→testy→`git log -3` (kolizja sesji)→commit jawne pliki→**restart courier-api + nadajesz-panel ZA ACK Adriana** (panel/peak bez OK = NIE).
- **ETAP 7:** rollback = flaga apki OFF wraca do starego `route_podjazdy` (carried-first) / `.bak` / `git revert`.

## Pełny kontekst
- Finding: `eod_drafts/2026-06-27/ZIOMEK_DEEP_AUDIT_REPORT.md` → `cap-carried-relax-app-console-divergence` (+ powiązane `pr-app-trust-canon-masked-dead`, `cap-console-reimpl-not-delegation`, `cap-build-view-trust-canon-dead-flag`).
- Reguły procesu: `memory/ziomek-change-protocol.md` Załącznik C (C1 multi-sesja, C4 monitor-first, C5 osiągalność flagi, C7 twin-registry, C8 execution-order).
