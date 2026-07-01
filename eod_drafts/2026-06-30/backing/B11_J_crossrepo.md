# B11 — KLASA J (cross-repo / multi-proces / worktree dryf) — Faza 1 lane B

**Tryb:** READ-ONLY. **Data:** 2026-06-30 ~14:10 UTC. **Sesja:** tmux 2. **Agent:** B11-J-crossrepo.
**Bazuje na:** A5 (mapa serwisów/cross-repo) + A6 (graf bliźniaków) + A3 (flagi efektywne) — **NIE re-derywuje**, ROZSZERZA: czyta CIAŁA których A5 nie czytał (Kotlin, parcel, golden-testy, monitor, worktree-diff), kwantyfikuje rozjazdy.
**Wszystkie `plik:linia` ze ŚWIEŻEGO grep/diff/systemctl tego runu** (HEAD silnik `8024705`, courier_api master `c081e6a`, panelsync `4ab1e6d` branch `panel-sync-shadow`, nadajesz_clone `aced00a` branch `coordinator-console`). Linie DRYFUJĄ.

---

## TL;DR — co J dokłada PONAD A5/A6

1. **Parytet route-order konsola↔silnik to NIE „golden test".** Dwie ROZŁĄCZNE suity (`test_route_podjazdy_trust_canon.py` w silniku vs `test_fleet_route.py` w panelu) — inne repo, inny venv, inne fixtury, inne funkcje (`order_podjazdy` vs `_build_route`), ŻADNA nie cross-importuje drugiej. **Brak testu równoważności `order_podjazdy(X)==_build_route(X)`.** Jedyny realny parytet repo↔repo = runtime-monitor.
2. **Jedyny runtime-monitor parytetu SAM SIĘ WYŁĄCZA 2026-07-10** (`ziomek_time_route_monitor.py:385-391`, `MONITOR_STOP_AFTER=2026-07-10` w env → po dacie `return 0` no-op). Za 10 dni konsola↔silnik route-order zostaje BEZ JAKIEGOKOLWIEK parytetu (brak importu + golden iluzoryczny + monitor martwy).
3. **`PICKUP_MERGE_MIN = 10` ręcznie skopiowany 5× w 3 repo / 3 językach** (silnik py / panel-backend py / panel-front tsx / apka Kotlin) — parytet komentarzem.
4. **Apka↔silnik route = WSPÓLNY IMPORT** (`courier_orders.py:38 from dispatch_v2 import route_podjazdy`) **ale z CICHYM fail-soft** do lokalnej rozjeżdżalnej kopii (`:40-41` print+`None`, połknięte) — zerwanie cross-repo importu = cicha dywergencja trasy, zero alarmu.
5. **ETA liczona 3× niezależnie** (silnik chain_eta, apka własny OSRM+haversine, konsola własny OSRM) — parytet NIC; wspólny kanał tylko `live_eta_cache` (read-when-fresh).
6. **panelsync = martwy fork** courier_orders (665 vs 1285 L, **784 linie różnicy**), build_view serwowany TYLKO przez panelsync `main.py` którego ŻADEN unit nie uruchamia (biega tylko `panel_sync.py`).
7. **Worktree:** 3 kopie nadajesz_clone w `/root/` mają `fleet_state.py` rozjechany **481 linii**, `feed.py` **225 linii** od żywej `coordinator-console` — wspólny git-index → wyścig multi-sesja (recon C1 `78401ed`→`976afbf`).
8. **3 niezależne systemy flag** — ta sama reguła (TRUST_CANON) bramkowana inaczej-nazwaną, inaczej-default'owaną flagą per repo; fingerprint silnika tego nie widzi (A3 §7).

---

## (a) MACIERZ KOPII LOGIKI DECYZYJNEJ cross-repo (świeże linie)

| Reguła | SILNIK (dispatch_v2) | APKA (courier_api) | KONSOLA-backend (nadajesz_clone/panel) | KONSOLA-front (tsx) | APKA Kotlin (courier-app) | Parytet |
|---|---|---|---|---|---|---|
| **Kolejność trasy** (carried-first relax + no-return) | `route_podjazdy.py:190 order_podjazdy` / `:141 _canon_order_from_plan` (ŹRÓDŁO) | `courier_orders.py:1116-1118` **importuje** route_podjazdy (sys.path :35-38); fallback `:672 _plan_stop_sequence`/`:467 _prioritize_carried_dropoffs` | `fleet_state.py:395 _build_route`/`:342 _order_from_plan_seq` (KOPIA, brak importu) | `Ops13Console.tsx:2200` grupowanie kafli | `RouteLogic.kt:27 buildSteps` (renderuje `stopSequence` serwera VERBATIM, NIE re-sortuje) | apka↔silnik=IMPORT; konsola↔silnik=**monitor-only** |
| **Bundling „1 restauracja=1 wizyta"** próg | `route_podjazdy.py:30 PICKUP_MERGE_MIN=10` | (przez import route_podjazdy) | `fleet_state.py:88 PICKUP_MERGE_MIN=10` | `Ops13Console.tsx:182 PICKUP_MERGE_MIN=10` | `RouteLogic.kt:54 PICKUP_MERGE_MIN=10` + `:23 restaurantKey` (4-dec coloc) | **NIC** (komentarz „= fleet_state") |
| **ETA-chain stopów** | `chain_eta.compute_chain_eta` + route_simulator OSRM | `courier_orders.py:186 _haversine`/`:265 optimize_route`/`:794 _compute_live_eta`/`:822 _attach_fallback_eta` (własny OSRM+brute/NN) | `fleet_state.py:235 _osrm_leg_durations`/`:250 _eta_chain` (własny OSRM) | — | (render serwera) | **NIC** (wspólne: `live_eta_cache` read `:1245` gdy świeże) |
| **Trust-canon gate** | (jest kanonem) | `config.py:60 BUILD_VIEW_TRUST_CANON_ORDER` (env `ENABLE_BUILD_VIEW_TRUST_CANON_ORDER`, eff=1) | `flag("TRUST_CANON_ORDER")` ← `PANEL_FLAG_TRUST_CANON_ORDER` (eff=1) | — | — | 2 flagi inaczej-nazwane, brak rejestru |
| **„covers bag" gate** | w kodzie `route_podjazdy` (coverage check) | `config.py:60` (przez order_podjazdy) | `fleet_state.py:877 flag("TRUST_CANON_WHEN_COVERS_BAG")` ← `PANEL_FLAG_…=1` | — | — | gate w KODZIE (apka) vs we FLADZE (konsola) |
| **Pula/overlay przerzutu** | `reassignment_forward_shadow.py` (jsonl) | — | `feed.py:239 _load_reassign_proposals` (BRAK `_pos_trusted`) | — | — | Telegram filtruje `REASSIGN_FWD_NOTIFY_TRUSTED_ONLY`, konsola NIE |
| **Status-protokół gastro 2-9** | `state_machine` (ignoruje 7/8/9) | `status_store.py:24-27 {3:dojazd,5:odebrane,7:doreczone}` | (panel app) | — | (apka) | + panelsync `panel_kurier.py:131 change_status` (numeric) — magic-protokół, brak enum |

---

## (b) INSTANCJE (każda: źródło/objaw · łatane? · otwarte? · severity · dowód · dedup)

### J1 — Route-order: konsola = KOPIA bez importu, parytet tylko runtime-monitorem ⟶ R2
- **plik:linia:** `nadajesz_clone/panel/backend/app/integrations/ziomek/fleet_state.py:395` (`_build_route`) + `:342` (`_order_from_plan_seq`) vs silnik `dispatch_v2/route_podjazdy.py:190` (`order_podjazdy`).
- **źródło/objaw:** ŹRÓDŁO. **łatane:** częściowo (TRUST_CANON_WHEN_COVERS_BAG 29.06 zmniejsza rozjazd). **otwarte:** TAK (strukturalnie). **severity: P1.**
- **dowód:** `route_podjazdy.py` docstring (świeży): „⚠ Konsola koordynatora ma WŁASNĄ kopię-lustro (panel `fleet_state._order_from_plan_seq`/`_build_route`) i NIE importuje tego modułu (osobne repo/venv) — parytet apka↔konsola utrzymywany TESTEM (golden fixture), NIE wspólnym importem. Każda zmiana reguły kolejności = zmień OBA bliźniaki." Grep: `fleet_state.py` ma 0 importów `route_podjazdy`/`dispatch_v2`. Twin #11 historyczny 44-75 rozjazdów/dzień (protokół l.84).
- **dedup_hint:** R2 „one route-order module" (A6 GRUPA 2, K1+K7).

### J2 — „Golden test" parytetu jest ILUZORYCZNY (dwie rozłączne suity, brak equivalence) ⟶ R2
- **plik:linia:** silnik `tests/test_route_podjazdy_trust_canon.py:15` (`from dispatch_v2 import route_podjazdy as rp`, fixtury `_BARTEK_BAG`/`_C75_BAG`) vs panel `tests/test_fleet_route.py:8` (`from app.integrations.ziomek.fleet_state import _build_route`, fixtury własne „Jakub two chicago").
- **źródło/objaw:** ŹRÓDŁO (projekt testów). **łatane:** NIE. **otwarte:** TAK. **severity: P2.**
- **dowód:** Żaden plik nie cross-importuje drugiego; inne funkcje (`order_podjazdy(bag,plan,trust_canon=)` vs `_build_route(plan,bag,start,meta)`), inne sygnatury, inne fixtury. **Brak asercji `order_podjazdy(X) ≡ _build_route(X)` na wspólnym wejściu.** Docstringi OBU plików deklarują „parytet TESTEM (golden fixture)" — co sugeruje wspólny test równoważności, którego NIE MA. Każda suita pinuje tylko WŁASNĄ powierzchnię → drift na wejściu niepokrytym identycznie przez obie = niezłapany.
- **dedup_hint:** R2 — uzupełnia A6 GRUPA 2 „RUNTIME-MONITOR / NIC". A6 nazwał monitor jedynym mechanizmem; J2 dowodzi że „golden test" w docstringach = mylące (to NIE parytet).

### J3 — Jedyny cross-repo monitor parytetu SAM WYGASA 2026-07-10 ⟶ R2 + H
- **plik:linia:** `nadajesz_clone/panel/backend/tools/ziomek_time_route_monitor.py:385-391` (`stop_after=os.environ.get("MONITOR_STOP_AFTER")` → `if _now().date()>… : print("monitor wygasł") return 0`). Env zmierzony: `MONITOR_STOP_AFTER=2026-07-10`.
- **źródło/objaw:** ŹRÓDŁO (lifecycle). **łatane:** NIE. **otwarte:** TAK (T-10 dni). **severity: P1.**
- **dowód:** Monitor (`:44 from app…fleet_state import _build_route`, `:45 from dispatch_v2 import route_podjazdy as RP`) = JEDYNE miejsce importujące OBA i porównujące na żywych danych (A6 GRUPA 2: „JEDYNY mechanizm parytetu repo↔repo"). Timer `ziomek-time-route-monitor.timer` active+enabled DZIŚ, ale kod no-op po dacie → cichy zgon (tylko print). Po 07-10: J1 (kopia konsoli) bez importu + J2 (golden iluzoryczny) + monitor martwy = ZERO sieci.
- **dedup_hint:** R2 + H (luka cyklu życia przyrządu). Faza C: odczytać świeży `ziomek_time_route_monitor.jsonl` (mismatches==?) ZANIM wygaśnie.

### J4 — `PICKUP_MERGE_MIN=10` ręcznie skopiowany 5× (3 repo / 3 języki) ⟶ R2 (sub-bundling)
- **plik:linia:** silnik `route_podjazdy.py:30` · konsola-backend `fleet_state.py:88` · konsola-front `Ops13Console.tsx:182` · apka Kotlin `RouteLogic.kt:54`. (apka-py: brak własnej — przez import route_podjazdy.)
- **źródło/objaw:** ŹRÓDŁO. **łatane:** NIE. **otwarte:** TAK (dziś wszystkie =10 → FRAGILE). **severity: P2.**
- **dowód:** komentarz `route_podjazdy.py:30` „= fleet_state"; `fleet_state.py:87` „progiem PICKUP_MERGE_MIN we froncie (Ops13Console.tsx)". Grupowanie pickup-runs wykonuje się NIEZALEŻNIE 4× (silnik route_podjazdy, konsola `fleet_state._pickup_runs:305`, front `Ops13Console.tsx:2200`, Kotlin `RouteLogic.kt:40-48`). `restaurantKey` Kotlin (`:23` 4-dec lat/lon LUB name+address) ≠ klucz coloc silnika → przy double-grouping (serwer już grupuje, klient re-grupuje) rozjazd kafli.
- **dedup_hint:** R2 (bundling sub-rule); A1/N + J.

### J5 — ETA-chain: 3 niezależne implementacje live (+1 martwa) ⟶ R-ETA
- **plik:linia:** silnik `chain_eta.compute_chain_eta`/route_simulator · apka `courier_orders.py:186 _haversine`/`:258 _haversine_matrix`/`:265 optimize_route`/`:794 _compute_live_eta`/`:822 _attach_fallback_eta` · konsola `fleet_state.py:235 _osrm_leg_durations`/`:250 _eta_chain` · martwa: panelsync `courier_orders.py:441/:490`.
- **źródło/objaw:** ŹRÓDŁO. **łatane:** NIE. **otwarte:** TAK. **severity: P2.**
- **dowód:** Grep: apka i konsola NIE importują `chain_eta` — każda ma własne wywołanie OSRM (`/route/v1/driving`) + własny haversine fallback. Wspólny kanał = `live_eta_cache` (apka `:1245 from dispatch_v2 import live_eta_cache` READ; gdy świeże). Parytet liczenia = NIC (test nie wiąże); spójność tylko gdy wszyscy czytają cache zamiast liczyć.
- **dedup_hint:** R-ETA (A6 GRUPA 2 wiersz ETA-chain).

### J6 — Cross-repo import apki = CICHY fail-soft do rozjeżdżalnej lokalnej kopii ⟶ R2 + M
- **plik:linia:** `courier_orders.py:35-41` (`sys.path.insert(0,_SCRIPTS_DIR)`; `try: from dispatch_v2 import route_podjazdy as _route_podjazdy` `except: _route_podjazdy=None; print(...)`). Gate `:1116 if config.APP_ROUTE_FROM_CONSOLE and _route_podjazdy is not None and mine`.
- **źródło/objaw:** ŹRÓDŁO. **łatane:** NIE (świadomy fail-soft). **otwarte:** TAK. **severity: P2.**
- **dowód:** ImportError (np. cross-repo move, zła sys.path, błąd w route_podjazdy) → `_route_podjazdy=None`, tylko `print` (połknięte, nie alarm/Telegram) → trasa spada na lokalny `_plan_stop_sequence:672`/`_prioritize_carried_dropoffs:467` (kopia bez gwarancji parytetu z kanonem). „Bezpieczne" dla crashu, ale = cicha dywergencja kolejności jazdy bez sygnału.
- **dedup_hint:** R2 + M (cicha awaria sentinel/fallback).

### J7 — 3 niezależne systemy flag; „ta sama" reguła inaczej bramkowana per repo ⟶ K1-flag
- **plik:linia:** apka `courier_api/config.py:60 BUILD_VIEW_TRUST_CANON_ORDER` (env `ENABLE_BUILD_VIEW_TRUST_CANON_ORDER`, default „0", **eff=1**) · konsola `app/core/flags.py` + `PANEL_FLAG_TRUST_CANON_ORDER` (**eff=1**) · silnik = brak flagi (jest kanonem). „covers bag": konsola `flag("TRUST_CANON_WHEN_COVERS_BAG")` (eff=1) vs apka w kodzie `route_podjazdy`.
- **źródło/objaw:** ŹRÓDŁO. **łatane:** NIE. **otwarte:** TAK. **severity: P2.**
- **dowód:** `systemctl show` (świeży): courier-api ma 7× `ENABLE_*`, nadajesz-panel 3× `PANEL_FLAG_*`. Brak wspólnego rejestru; `flag_fingerprint()` silnika (A3 §7) pokrywa 63 flagi, NIE widzi route/canon ani cross-repo → „fingerprinty identyczne" = fałszywe zapewnienie parytetu cross-repo. Flaga decyzyjna „trust canon" istnieje 2× pod różnymi nazwami z niezależnym defaultem.
- **dedup_hint:** K1 (flag-drift, A5 C.7 / A3 §7). Cross-ref klasa D/E.

### J8 — panelsync = martwy fork courier_orders (784 L różnicy, nieserwowany) ⟶ R2 / K
- **plik:linia:** `courier_api_panelsync/courier_orders.py` (665 L; `build_view:558`, `_plan_stop_sequence:366`, `optimize_route:188`) vs główna `courier_api/courier_orders.py` (1285 L) — **diff = 784 linie różnicy**. Brak importu route_podjazdy, brak carried-first/trust-canon.
- **źródło/objaw:** OBJAW (artefakt worktree + brak wspólnego importu). **łatane:** NIE. **otwarte:** TAK. **severity: P2.**
- **dowód:** `courier_api_panelsync/main.py:19 import courier_orders`, `:257 courier_orders.build_view(...)` — ale `courier-panel-sync.service ExecStart=… panel_sync.py --once --live` (NIE main.py); `panel_sync.py` importuje tylko `config,db,panel_kurier,panel_lite` (brak courier_orders). Grep `/etc/systemd/system` po `courier_api_panelsync` = tylko `courier-panel-sync.service`. ⟹ panelsync `build_view` (665L) MARTWY (floor-audit: „zdegenerowany, MARTWY — nie serwowany"). Worktree dzieli `.git` courier_api → 2 checked-out kopie tego samego pliku.
- **dedup_hint:** R2 (martwa kopia route) / K (martwy kod do usunięcia).

### J9 — 6 worktree w 2 repach; kopie renderu konsoli rozjechane 481/225 linii ⟶ K7 + O
- **plik:linia:** nadajesz_clone 4 worktree: `coordinator-console` (LIVE, `aced00a`) + `/root/nadajesz-sms-wt` (`2911984`) + `/root/ndj-client-panel` (`71fdcaf`) + `/root/ndj-parcel` (`764a07a`). Diff `fleet_state.py` LIVE vs każda /root = **481 linii**; `feed.py` = **225 linii**. courier_api 2 worktree: główna (`c081e6a`) + `courier_api_panelsync` (`4ab1e6d`).
- **źródło/objaw:** ŹRÓDŁO (topologia worktree). **łatane:** NIE. **otwarte:** TAK. **severity: P2.**
- **dowód:** `git worktree list` (świeży) + `diff` line-count. 3 /root kopie identyczne między sobą (wspólny stary fork-point), rozjechane od żywej o ~40% pliku route-render. Wspólny git-index → wyścig multi-sesja (recon C1 near-miss `78401ed`→`976afbf`). Deploy na wspólne cele (`/var/www/html/admin-panel`, gps.nadajesz.pl) — MEMORY [[feedback-multisession-shared-deploy]]. Ryzyko: merge/deploy stałej gałęzi cofa 481-liniowe fixy carried-first/clamp/trust-canon.
- **dedup_hint:** K7 (cross-repo dryf) + O (wiele working-copy / wspólny indeks).

### J10 — Parcel lane: handoff JSON cross-repo, 2 venvy, DWIE niezależne bramki ⟶ K5/O parcel
- **plik:linia:** panel `app/integrations/ziomek/parcel_lane.py:166 write_shadow` → `orders_state.parcels_shadow.json` (`:46 shadow_path`). Silnik `parcel_lane_merge.py:112 run` czyta snapshot + `:134 sm.upsert_order(... PARCEL_LANE_NEW)`, brama `:114 C.flag("ENABLE_PARCEL_LANE_LIVE")` (flags.json). Panel-side write bramkowany `PANEL_FLAG_PARCEL_*`. Shadow propozycja: panel `parcel_dispatch_shadow.py:5` subprocess `assess_order` w venv Ziomka.
- **źródło/objaw:** ŹRÓDŁO (dual-gate). **łatane:** NIE. **otwarte:** TAK. **severity: P2.**
- **dowód:** Selekcja/route paczki DZIEDZICZY silnik (merge tylko `upsert_order` → normalny `assess_order`/`_selection_bucket`/`order_podjazdy`; shadow = subprocess realnego silnika) — **brak kopii route/bucket** (A6 gap #2 ROZSTRZYGNIĘTY: parcel NIE ma własnego toru selekcji). J-ryzyko = handoff: JSON multi-writer + DWIE bramki w DWÓCH repach dla JEDNEJ funkcji (panel pisze ⊥ silnik ingestuje) → brak jednego „parcel on/off".
- **dedup_hint:** K5/O (sentinel/handoff, A5 C.4). Niżej niż route-copy bo brak kopii decyzji.

### J11 — Konsola renderuje przerzuty BEZ filtra `_pos_trusted` który ma Telegram ⟶ R1
- **plik:linia:** `feed.py:239 _load_reassign_proposals` — filtr TYLKO `:258 if not d.get("quality_reassign"): continue` (brak `_pos_trusted`/`pos_source`). Telegram: flaga `REASSIGN_FWD_NOTIFY_TRUSTED_ONLY` (A3 §3c).
- **źródło/objaw:** ŹRÓDŁO (brak filtra w kopii konsoli). **łatane:** NIE. **otwarte:** TAK. **severity: P1.**
- **dowód:** `reassignment_forward_shadow` = 59% fałszywych „ratunków" ripujących no_gps/pre_shift (MEMORY/A6 GRUPA 3b). Telegram filtruje niezaufaną pozycję; konsola `feed.py` czyta `reassignment_shadow.jsonl` i pokazuje WSZYSTKIE `quality_reassign` w oknie TTL → koordynator widzi fałszywe przerzuty, których bot by nie wysłał. Cross-repo render shadow-instrumentu z inną polityką filtra niż primary.
- **dedup_hint:** R1 (out-of-engine position gates, A6 GRUPA 3b) — J jest powierzchnią renderu tego rootu.

### J12 — Status-protokół gastro (2-9) zakodowany niezależnie per repo ⟶ N
- **plik:linia:** apka `status_store.py:24-27 {3:"dojazd",5:"odebrane",7:"doreczone"}` · panelsync `panel_kurier.py:131 change_status` (numeric POST `/admin2017/gastro/kurier/change-status`) · silnik `state_machine` (ignoruje 7/8/9, CLAUDE.md Panel API).
- **źródło/objaw:** OBJAW (magic-protokół). **łatane:** NIE. **otwarte:** TAK. **severity: P3.**
- **dowód:** Brak wspólnego enum statusów; semantyka kodów 2-9 (dojazd/oczek./odebrane/opóźn./doręczone/nieodebr./anul.) zreplikowana w ≥3 repach. Znany kontrakt (udokumentowany), ale replikowany nie importowany → zmiana kodu w gastro dryfuje każdą kopię.
- **dedup_hint:** N (magic-protokół cross-repo). Niska — stabilny kontrakt zewn.

### J13 — USE_V2_PARSER cross-proces (panel-watcher=V2, shadow=V1) — PLAUSIBLE, defer A3 ⟶ D2
- **plik:linia:** `panel_client.py:93 USE_V2_PARSER = os.environ.get("USE_V2_PARSER","0")=="1"` (env-frozen). Zmierzony: `=1` TYLKO panel-watcher; shadow/inne → default V1.
- **źródło/objaw:** ŹRÓDŁO. **łatane:** NIE. **otwarte:** PLAUSIBLE (cross-proces, NIE czysto cross-repo). **severity: P2.**
- **dowód:** A3 §5 — niepotwierdzone czy shadow realnie `parse_panel_html`. Tu CROSS-REF (nie re-derywuję): dwa parsery na ten sam panel = ryzyko niespójności warstwy-1 wejścia. **Defer Faza C** (trace czy shadow parsuje HTML).
- **dedup_hint:** D2 (env per-proces, A3 §5) — boundary cross-proces, nie cross-repo.

---

## (c) ENV ROZJAZD MIĘDZY SERWISAMI (świeży `systemctl show -p Environment`)

| Serwis | repo/venv | Decyzyjny env (route/canon/flag) | Uwaga J/D |
|---|---|---|---|
| `courier-api` | courier_api/.venv | `ENABLE_APP_ROUTE_FROM_CONSOLE=1`, `ENABLE_BUILD_VIEW_TRUST_CANON_ORDER=1`, `ENABLE_PLAN_AWARE_PODJAZDY=1`, `ENABLE_LIVE_ETA_COURIER_GUARD=1`, `ENABLE_PICKUP_TIME_READY_FALLBACK=1`, `ENABLE_DELIVERY_DASH_WHEN_NO_PLAN=1`, `ENABLE_DELIVERED_TOO_FAST_GUARD=1` (+2 cred) | route = engine route_podjazdy (import). `BUILD_VIEW_TRUST_CANON_ORDER` przekazany jako `trust_canon=` (NIE martwy — C5 near-miss A6 nieaktualny dla tej ścieżki). |
| `nadajesz-panel` | panel/.venv→python3 | `PANEL_FLAG_TRUST_CANON_ORDER=1`, `PANEL_FLAG_TRUST_CANON_WHEN_COVERS_BAG=1`, `PANEL_FLAG_DELIVERY_DASH_WHEN_NO_PLAN=1` | route = własna kopia fleet_state; te 3 to JEDYNE PANEL_FLAG w env (reszta z `DEFAULT_FLAGS` dict, baked). |
| `ziomek-time-route-monitor` | panel/.venv | `MONITOR_STOP_AFTER=2026-07-10`, `PANEL_FLAG_TRUST_CANON_ORDER=1` | **self-expiry 10 dni** (J3). |
| `courier-panel-sync` | courier_api/.venv | `PANEL_SYNC_LIVE=1` | biega z workdir `courier_api_panelsync` ALE venv głównej courier_api → fork-kod + venv-główny. |
| `gate-audit` | courier_api/.venv | (pusty) | — |
| `nadajesz-parcel-shadow` | panel/.venv | (pusty — gate z flags.json silnika + PANEL_FLAG dict) | parcel dual-gate (J10). |
| silnik `dispatch-shadow` / `plan-recheck` / `panel-watcher` | dispatch/.venv | route/canon 14-16 flag env-frozen (A3 §1) — **shadow ich NIE ma** | A3 §1d/B.1 (twin plan-recheck↔panel-watcher: SEQUENCE_LOCK/COMMITTED_PROPAGATION/LIVE_ETA_REFRESH tylko plan-recheck). |

**Wzorzec:** ta sama reguła „render kanon verbatim" jest ON na 3 powierzchniach przez **3 różne mechanizmy** (`ENABLE_BUILD_VIEW_TRUST_CANON_ORDER` env / `PANEL_FLAG_TRUST_CANON_ORDER` env / silnik=kanon) — żaden wspólny rejestr; A3 §7 fingerprint tego nie obejmuje.

---

## (d) WORKTREE INWENTARZ (świeży `git worktree list`)

| Repo | Worktree | Ścieżka | Branch / HEAD | Rozjazd route-render vs LIVE |
|---|---|---|---|---|
| dispatch_v2 | (jedyny) | `scripts/dispatch_v2` | master `8024705` | — (czysto, 1 worktree) |
| nadajesz_clone | LIVE | `nadajesz_clone` | `coordinator-console aced00a` | baseline |
| nadajesz_clone | sms | `/root/nadajesz-sms-wt` | `feat/sms-customer-tracking 2911984` | fleet_state **481 L**, feed **225 L** |
| nadajesz_clone | client-panel | `/root/ndj-client-panel` | `feat/client-panel 71fdcaf` | fleet_state **481 L**, feed **225 L** |
| nadajesz_clone | parcel | `/root/ndj-parcel` | `feat/parcel-ordering 764a07a` | fleet_state **481 L**, feed **225 L** |
| courier_api | główna | `scripts/courier_api` | `master c081e6a` | baseline build_view 1285 L |
| courier_api | panelsync | `scripts/courier_api_panelsync` | `panel-sync-shadow 4ab1e6d` | courier_orders **784 L** różnicy (martwy, J8) |

---

## (e) DISTINCT-ROOT ROLLUP (anty-double-count dla Fazy E)

| Root | Instancje J | Status |
|---|---|---|
| **R2 — one route-order module** | J1 (kopia konsoli) · J2 (golden iluzoryczny) · J3 (monitor wygasa) · J4 (merge×5) · J6 (import fail-soft) · J8 (panelsync martwy) | DIVERGED — sieć parytetu krucha+wygasająca |
| **R-ETA — one ETA module** | J5 (3 implementacje) | DIVERGED — wspólne tylko live_eta_cache |
| **R1 — out-of-engine position gates** | J11 (feed bez `_pos_trusted`) | render-konsola rootu R1 (A6 GRUPA 3b) |
| **K1-flag / D / N** | J7 (3 systemy flag) · J12 (status-protokół) | rozsyp, brak rejestru/enum |
| **K7+O — worktree/multiproces** | J9 (6 worktree, 481/225 L) · J10 (parcel dual-gate) · J13 (USE_V2_PARSER) | dryf working-copy/handoff |

**NIE liczyć J1-J8 jako 6 chaosów** — to JEDEN root R2 manifestujący się w 6 mechanizmach kopii/parytetu. J3 (monitor wygasa) = najpilniejszy konkret (T-10 dni). J11 = powierzchnia R1 (raportowany też przez agenta pozycji).

---

## (f) DEKLARACJA POKRYCIA (jawne luki, nie cisza)

**ZBADANE (świeży kod/diff/systemctl):**
- Route-order: silnik `route_podjazdy.py` (pełne defy+docstring), konsola `fleet_state.py:250/342/395` (defy+flagi+regiony), apka `courier_orders.py` (defy + region short-circuit 1108-1150 + import 35-41), panelsync `courier_orders.py` (defy+diff), Kotlin `RouteLogic.kt` (CAŁY plik), front `Ops13Console.tsx` (grep merge/grupowanie).
- ETA-chain: apka/konsola grep importów+OSRM (potwierdzona niezależność od chain_eta).
- Bundling: `PICKUP_MERGE_MIN` świeży grep 5 powierzchni.
- Flagi efektywne: `systemctl show -p Environment` × 6 cross-repo serwisów + cross-ref A3 dla silnika.
- Parytet: OBA golden-testy (`test_route_podjazdy_trust_canon.py`, `test_fleet_route.py`) — potwierdzona rozłączność; monitor `ziomek_time_route_monitor.py` (import obu + self-expiry 385-391).
- Parcel: silnik `parcel_lane_merge.py`/`parcel_assign.py`, panel `parcel_lane.py`/`parcel_dispatch_shadow.py` (defy).
- Worktree: `git worktree list` × 3 repo + `diff` line-count.
- Status-mapping: `status_store.py`, panelsync `panel_kurier.py`.
- Overlay: `feed.py` (defy + brak `_pos_trusted`).

**LUKI (jawne + powód):**
1. **Runtime-diff `order_podjazdy` vs `_build_route`** na żywych danych — NIE zrobiony (lane C oracle; instrument `ziomek_time_route_monitor.jsonl` istnieje, NIE sparsowany read-only). Magnituda dzisiejszego rozjazdu = Faza C.
2. **Pełne ciała `_build_route`/`build_view`** — czytane nagłówki+regiony kluczowe, NIE linia-po-linii całe (~395-700 fleet_state, ~1072-1285 courier_orders). Lista RÓŻNIC reguł = Faza B/C.
3. **`parcel_overlay.py` (konsola)** — potwierdzono istnienie, NIE zweryfikowano czy kopiuje route/ETA (parcel render). Faza B.
4. **Kotlin poza `RouteLogic.kt`** — `RouteViewModel.kt`/`RouteScreen.kt` (render ETA/format) NIE czytane; route-ORDER potwierdzony jako server-trusting, ale lokalny ETA-display Kotlin nie audytowany.
5. **USE_V2_PARSER (J13)** — PLAUSIBLE, czy shadow parsuje HTML = niepotwierdzone (cross-proces, defer A3/Faza C).
6. **Env `dispatch-czasowka` / `reassign-global-select` / `carried-first-guard` timery** — NIE zmierzony `systemctl show` (A3 §9 ta sama luka).
7. **Front Ops13Console grouping `:2200`** — tylko grep, nie pełna lektura logiki klucza coloc vs Kotlin `restaurantKey`.

**NIE-luki (świadomie poza zakresem):** Mailek/Papu (granica STOP); `papu_dispatch_bridge`/`drtusz_bridge` (mosty boundary, nie analizowane pod route-copy); flagi efektywne silnika (A3); przyrządy-prawda (A4/Faza C); pozycyjne buckety engine (A6 GRUPA 3 — tu tylko render-konsola J11).
