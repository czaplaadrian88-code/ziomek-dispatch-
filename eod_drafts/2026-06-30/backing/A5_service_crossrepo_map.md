# A5 — MAPA SERWISÓW / DROP-INÓW + CROSS-REPO + WORKTREE (klasa J pierwszoplanowa)

**Faza A inwentarz, READ-ONLY. Data: 2026-06-30 ~13:40 UTC. Agent: A5-service-crossrepo-map (sesja tmux 2).**
Wszystkie `plik:linia` z ŚWIEŻEGO grep/systemctl tego runu. Zero edycji/restartów/flipów. Surowe dane: `scratchpad/{dropin_inventory.txt,effective_env.txt}`.

OS dla klasy **J** (cross-repo/multi-proces/worktree), zasila Fazę D (precedencja między ścieżkami) i dashboard entropii (twin-divergence, copy-count, dead-flag — w 3 systemach flag, nie 1).

---

## TL;DR — 7 twardych faktów J/K

1. **Konsola NIE importuje silnika dla renderu trasy.** `fleet_state.py` (1181 L) i `feed.py` (387 L) — pliki liczące/renderujące kolejność trasy, czas odbioru i pulę kandydatów — **NIE importują `dispatch_v2`**; mają WŁASNE `_build_route` (l.395) i `_eta_chain` (l.250) = KOPIA reguł silnika (carried-first, relax, OSRM-chain). Parytet utrzymywany TYLKO flagą `PANEL_FLAG_TRUST_CANON_ORDER` + pomiarem „≡ kanonowi w **95.9%** worków" (fleet_state.py:866) → 4,1% rozjazd z konstrukcji.
2. **Reguła carried-first / „jeden przystanek = jedna restauracja" żyje w 3-4 KOPIACH** parytetowanych flagami TRUST_CANON, nie wspólnym importem: silnik (`_apply_canon_order_invariants`), konsola (`fleet_state._build_route`), courier_api (`courier_orders.build_view` + `_prioritize_carried_dropoffs:467`), apka (`RouteLogic.buildSteps:27` + `pickupTogether:62`).
3. **Twin env-asymetria plan-recheck ↔ panel-watcher** (oba REGENERUJĄ kanon `courier_plans`): panel-watcher **nie ma** `ENABLE_PLAN_SEQUENCE_LOCK` / `ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION` / `ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH`, które plan-recheck MA — mimo komentarzy w drop-inach deklarujących „spójność z tickiem plan-recheck". Klasa B+D2.
4. **3 NIEZALEŻNE systemy flag, brak jednego rejestru (J/D):** silnik = `flags.json` (hot-reload) + env drop-iny per-serwis + `common.decision_flag()`; konsola = `app/core/flags.py DEFAULT_FLAGS` dict + env `PANEL_FLAG_*` (bez hot-reload); courier_api = `config.py ENABLE_*` (env z courier-api.service.d). Stan decyzyjny rozsypany na 3 repo.
5. **2 repa z worktree (NIE dispatch_v2):** `nadajesz_clone` (4 working-copies: główna `coordinator-console` + `ndj-client-panel` + `ndj-parcel` + `nadajesz-sms-wt`) oraz `courier_api` (2: główna + `courier_api_panelsync`). `courier_api_panelsync/courier_orders.py` = **665 L vs 1285 L** głównej = ZDYWERGOWANY fork logiki statusów. `dispatch_v2` = 1 worktree (czysto).
6. **Panel venv = systemowy python3** (`panel/backend/.venv/bin/python -> python3`) → nie zaimportuje ciężkiego silnika → zapisy planu/quote idą **subprocess do venva Ziomka** (`route.py:104`, `parcel_dispatch_shadow.py`, `shadow_quote.py`). Pure-python shimy (`committed_time`, `courier_block`, `courier_provision_bridge`) importują `dispatch_v2` in-process.
7. **Graveyard K cross-repo:** courier_api 40 `.bak`, panel backend 72 `.bak`, panel `flags.py` 11 `.bak`, drop-iny systemd 4 `.bak`, 2 retired-unity + **osierocony drop-in dir** `dispatch-shift-notify.service.d` (3 confy, unit `.retired-2026-06-15`).

---

## (a) SERWISY + DROP-INY + ŚMIECI (klasa K)

### A.1 — Żywe (active running) — 10 serwisów
| Serwis | venv / runtime | Moduł | Rola decyzyjna | WorkingDir |
|---|---|---|---|---|
| `dispatch-shadow` | dispatch | `dispatch_v2.shadow_dispatcher` | **silnik**: feasibility+scoring+selekcja+serializer | scripts |
| `dispatch-panel-watcher` | dispatch | `dispatch_v2.panel_watcher` | recanon/redecide on write/pickup/override; **REGENERUJE kanon** | scripts |
| `dispatch-gps` | dispatch | `dispatch_v2.gps_server` | feed GPS | scripts |
| `dispatch-sla-tracker` | dispatch | `dispatch_v2.sla_tracker` | SLA/R6 + delivered miernik | scripts |
| `dispatch-monitor-419` | dispatch | `dispatch_v2.monitoring.detector_419` | health/419 | (brak) |
| `courier-api` | **courier_api/.venv** | `main.py` (:8767) | **build_view** = autorytet kolejności apki | scripts/courier_api |
| `gate-audit` | **courier_api/.venv** | `gate_audit_poller.py` | audyt bramki 5-min dojazdu | scripts/courier_api |
| `nadajesz-panel` | **panel/backend/.venv** | `uvicorn app.main` (:8000) | **konsola koordynatora** (fleet_state/feed) | nadajesz_clone/panel/backend |
| `nadajesz-ordering` | **node/npm** | `npm start -p 3001` | front zamawiania (bialystok.nadajesz.pl) | **/opt/nadajesz-ordering** |
| `nadajesz-history-ingest` | (activating) | history ingest do panel DB | dociąga dowozy | — |

> `dispatch-telegram` = **dead** (świadomie MUTED — `pending_proposals` 3-writer/no-lock „bezpieczny tylko bo muted", klasa O). `dispatch-cod-weekly` = **FAILED** (peryferyjny COD).

### A.2 — Timery LIVE decyzja/instrument (z `systemctl list-timers`)
- **decyzja/kanon:** `plan-recheck` (5min, regen kanonu), `reassign-global-select` (3min), `parcel-merge` (30s), `pending-pool` (1min), `czasowka` (1min), `postpone-sweeper`, `state-reconcile` (15min).
- **instrument/cień (osobne procesy):** `reassignment-shadow` (3min), `ziomek-pred-calibration` (3min), `carried-first-guard` (3min), `pickup-lateness-shadow`, `bundle-calib-shadow`, `b-route-shadow`, `pending-resweep-shadow` (1min), `freshness-shadow`, `eta-calibration` (10min), `courier-gps-commitment-shadow`, `fleet-position-snapshot`, `objm-lexr6-canary-monitor` (10min), `ziomek-time-route-monitor` (10min — parytet konsola↔apka), `downstream-crosscheck`.
- **cross-repo/most:** `parcel-merge`(dispatch) + `nadajesz-parcel-shadow`(panel, 60s) + `courier-panel-sync`, `drtusz-bridge`, `papu-bridge` (granica).
- **at-joby/one-shot pending:** `bundle-calib-review` (Jul 2), `pickup-slip-review` (Jul 4), `pickup-slip-monitor` (22:30 dziś). Reconcile z [[shadow-jobs-registry]] = praca Fazy C.

### A.3 — Drop-iny per kluczowy serwis (treść w scratchpad/dropin_inventory.txt)
**`dispatch-shadow.service.d/`** (5): `onfailure.conf`, `oom-protect.conf`, `override.conf` (telemetria: PANEL_BG_REFRESH=1, LGBM_SHADOW, LGBM_METRICS_READ, PENDING_POOL — 13 flag DECYZYJNYCH PRZENIESIONYCH do flags.json 2026-06-10 „ETAP4"), `resource_limits.conf`. **ŚMIEĆ K:** `override.conf.bak-pre-veto-retire-coeff100-2026-06-11`.

**`dispatch-plan-recheck.service.d/`** (12 confów): `unified-route-f1-f2.conf` (PLAN_REAL_PICKED_UP_AT, **PLAN_SEQUENCE_LOCK**, PLAN_CANON_ORDER_INVARIANTS, NO_RETURN_TO_DEPARTED_PICKUP), `committed-propagation.conf` (**PLAN_RECHECK_COMMITTED_PROPAGATION**), `live-eta-refresh.conf` (**PLAN_RECHECK_LIVE_ETA_REFRESH**), `gps-free-anchor.conf` (GPS_FREE_ANCHOR), `gps-free-lastpos-anchor.conf`, `carried-first-relax.conf`, `carried-age-tzfix.conf`, `lex-committed-window.conf` (+SHADOW), `route-reorder-fix-mk.conf` (NONCARRIED_DROPOFF_REORDER + RELAX_COLOC_PICKUP), `cron_health_success.conf`, `onfailure.conf`, `resource_limits.conf`. **ŚMIEĆ K:** `unified-route-f1-f2.conf.bak-pre-noreturn-2026-06-13`.

**`dispatch-panel-watcher.service.d/`** (12 confów): `unified-route-f3.conf` (IMMEDIATE_REDECIDE_ON_OVERRIDE, GPS_FREE_ANCHOR, PLAN_REAL_PICKED_UP_AT, PLAN_CANON_ORDER_INVARIANTS, IMMEDIATE_REDECIDE_ON_PICKUP, NO_RETURN_TO_DEPARTED_PICKUP), `recanon-on-write.conf` (RECANON_ON_WRITE), `carried-first-relax.conf`, `carried-age-tzfix.conf`, `gps-free-lastpos-anchor.conf`, `lex-committed-window.conf`, `route-reorder-fix-mk.conf`, `override.conf` (**PANEL_BG_REFRESH=0**, USE_V2_PARSER=1), `onfailure.conf`, `oom-protect.conf`, `resource_limits.conf`. **ŚMIEĆ K:** `unified-route-f3.conf.bak-pre-noreturn-2026-06-13`.

**`dispatch-sla-tracker.service.d/`** (2): `onfailure.conf`, `resource_limits.conf` — ZERO env decyzyjnego (efektywny env: tylko PYTHONPATH).

**`dispatch-czasowka.service.d/`** (5): `override.conf` (**CZASOWKA_TELEGRAM_DRYRUN=1**, RETROACTIVE_HOURS=2, MAX_EMIT_PER_TICK=3), `cron_health_success.conf`, `onfailure.conf`, `resource_limits.conf`. **ŚMIEĆ K:** `override.conf.bak-pre-notif-mute-2026-06-26`.

**`dispatch-reassign-global-select.service.d/`** = **BRAK** (potwierdzone: env z unitu = PUSTY; flaga `ENABLE_REASSIGN_GLOBAL_SELECT` tylko z `flags.json`).

**`dispatch-reassignment-shadow.service.d/`** (3, wszystkie świeże 28-29.06): `quality-gate-shadow.conf` (REASSIGN_QUALITY_GATE), `reassign-bundling-only.conf` (REASSIGN_OSZCZ_BUNDLING_ONLY), `rescue-require-absent.conf` (REASSIGN_RESCUE_REQUIRE_HOLDER_ABSENT — Sprint2 NO-GPS-EQUAL, 197 fałszywych ratunków wyciszonych).

**`dispatch-b-route-shadow.service.d/`** (1): `route-flag-parity.conf` — RĘCZNY parytet 13 flag z plan-recheck + recepta re-weryfikacji (`diff <(systemctl show ...)`). To DOKUMENT klasy J (brak wspólnego configu → hand-copy).

**`courier-api.service.d/`** (8): `admin-cred.conf`, `build-view-trust-canon.conf` (BUILD_VIEW_TRUST_CANON_ORDER), `deliver-guard.conf`, `delivery-dash-no-plan.conf`, `pickup-time-fallback.conf`, `plan-aware-podjazdy.conf`, `podjazdy.conf`, `oom-protect.conf`.

**`nadajesz-panel.service.d/`** (6): `trust-canon-order.conf` (PANEL_FLAG_TRUST_CANON_ORDER), `trust-canon-covers-bag.conf` (PANEL_FLAG_TRUST_CANON_WHEN_COVERS_BAG), `delivery-dash-no-plan.conf` (PANEL_FLAG_DELIVERY_DASH_WHEN_NO_PLAN), `ksef-env.conf`, `wfirma-env.conf`, `oom-protect.conf`.

### A.4 — Śmieci/martwe unity (klasa K) — pełna lista
- **systemd .bak (4):** shadow/`override.conf.bak-...`, czasowka/`override.conf.bak-...`, plan-recheck/`unified-route-f1-f2.conf.bak-...`, panel-watcher/`unified-route-f3.conf.bak-...`.
- **systemd retired (2) + orphan dir:** `dispatch-shift-notify.service.retired-2026-06-15`, `dispatch-shift-notify.timer.retired-2026-06-15` + **`dispatch-shift-notify.service.d/`** (cron_health/onfailure/resource_limits — drop-in dir bez unitu = osierocony).
- **unity zarejestrowane lecz dead** (peryferia, NIE okołosystem decyzji): ~70 `dispatch-onfailure-alert@*` instancje, `dispatch-checkpoint-tz-shadow`, `dispatch-state-panel-monitor`, `dispatch-watchdog`, `dispatch-delivered-integrity`, `dispatch-nogps-equal-watch`, `ziomek-time-route-review.timer` (one-shot, ostatni 26.06).
- **.bak w kodzie (klasa K, cross-repo):** `courier_api` 40 (m.in. ~18× `courier_orders.py.bak-*`, ~12× `config.py.bak-*`), `panel/backend` 72 (m.in. `flags.py` 11×, `ziomek_time_route_monitor.py` 2×, `courier_block.py.bak-pre-assign-window-gate-20260630`), `courier_api_panelsync` 2 (`panel_kurier.py.bak`, `panel_sync.py.bak`).

---

## (b) RÓŻNICE ENVIRONMENT PER-SERWIS (D2/J) — efektywny stan procesu

Źródło: `systemctl show <svc> -p Environment` (agreguje WSZYSTKIE drop-iny). Pełny zrzut: `scratchpad/effective_env.txt`.

### B.1 — ⭐ TWIN DIVERGENCE: plan-recheck ↔ panel-watcher (oba regenerują `courier_plans`)
| Flaga (env-frozen) | plan-recheck | panel-watcher | b-route-shadow | Uwaga |
|---|:--:|:--:|:--:|---|
| ENABLE_PLAN_CANON_ORDER_INVARIANTS | ✅ | ✅ | ✅ | wspólny |
| ENABLE_PLAN_REAL_PICKED_UP_AT | ✅ | ✅ | ✅ | wspólny |
| ENABLE_NO_RETURN_TO_DEPARTED_PICKUP | ✅ | ✅ | ✅ | wspólny |
| ENABLE_CARRIED_FIRST_RELAX | ✅ | ✅ | ✅ | wspólny |
| ENABLE_NONCARRIED_DROPOFF_REORDER | ✅ | ✅ | ✅ | route-reorder-fix-mk |
| ENABLE_RELAX_COLOC_PICKUP | ✅ | ✅ | ✅ | route-reorder-fix-mk |
| ENABLE_GPS_FREE_ANCHOR | ✅ | ✅ | ✅ | |
| ENABLE_GPS_FREE_ANCHOR_LAST_POS | ✅ | ✅ | ✅ | |
| ENABLE_LEX_COMMITTED_WINDOW (+_SHADOW) | ✅ | ✅ | ✅ | |
| ENABLE_CARRIED_AGE_TZ_FIX | ✅ | ✅ | ✅ | |
| **ENABLE_PLAN_SEQUENCE_LOCK** | ✅ | ❌ | ✅ | **TYLKO plan-recheck+b-route** |
| **ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION** | ✅ | ❌ | ✅ | **TYLKO plan-recheck+b-route** |
| **ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH** | ✅ | ❌ | ✅ | **TYLKO plan-recheck+b-route** |
| ENABLE_RECANON_ON_WRITE | ❌ | ✅ | ❌ | TYLKO panel-watcher |
| ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE | ❌ | ✅ | ❌ | TYLKO panel-watcher |
| ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP | ❌ | ✅ | ❌ | TYLKO panel-watcher |

**Smell (B/D2):** panel-watcher robi `recanon_courier → _retime_one_bag_plan` (na write/pickup/override) i `redecide_courier → _gen_one_bag_plan` (override) — ale BEZ SEQUENCE_LOCK / COMMITTED_PROPAGATION / LIVE_ETA_REFRESH. Drop-iny `unified-route-f3.conf` + `route-reorder-fix-mk.conf` deklarują wprost „spójność z tickiem plan-recheck", a env NIE jest identyczny. **PLAUSIBLE** (nie CONFIRMED): hipoteza, że `_retime_one_bag_plan` jest sekwencyjnie-zachowawczy więc SEQUENCE_LOCK bezprzedmiotowy, ale brak COMMITTED_PROPAGATION/LIVE_ETA_REFRESH znaczy, że recanon zdarzeniowy NIE stosuje tie-breakera committed ani live-eta-refresh, które tick stosuje → ścieżka zdarzeniowa vs tickowa mogą dać RÓŻNY kanon. **Do potwierdzenia Fazą B/C** (trace, czy gałęzie osiągalne i materialne). To K2-rodzina („plan_recheck = cofacz") od strony env.

### B.2 — shadow vs panel-watcher: PANEL_BG_REFRESH (świadome, udokumentowane)
`dispatch-shadow` = `ENABLE_PANEL_BG_REFRESH=1`, `dispatch-panel-watcher` = `=0`. ZAMIERZONE (shadow override.conf komentarz: „watcher ma własny cykl loginu"). Wzorzec „config do 1 serwisu" — legalny, ale to przykład per-proces env, którego Faza D musi znać przy precedencji.

### B.3 — ⭐ dispatch-shadow LACKS canon/route env (D2 — flaga service-scoped)
`dispatch-shadow` efektywny env = TYLKO telemetria (LGBM_SHADOW, LGBM_METRICS_READ, OBJ_REPLAY_CAPTURE, PANEL_BG_REFRESH=1, PENDING_POOL). **NIE MA** `ENABLE_PLAN_CANON_ORDER_INVARIANTS`, `ENABLE_CARRIED_FIRST_RELAX`, `ENABLE_PLAN_SEQUENCE_LOCK` etc. — które plan-recheck/panel-watcher/b-route MAJĄ. **Smell PLAUSIBLE:** jeśli `shadow_dispatcher` kiedykolwiek liczy plan worka in-process (`_gen_one_bag_plan` w scoringu propozycji bag≥2), robi to BEZ niezmienników kanonu → propozycja-trasa silnika mogłaby różnić się od finalnego kanonu pisanego przez plan-recheck. Wymaga trace’u (Faza B), CZY shadow buduje plan czy tylko czyta zapisany. Te flagi domyślnie OFF w kodzie (potwierdza komentarz drop-inów „kod defaultuje OFF").

### B.4 — pozostałe profile env
- `dispatch-reassign-global-select`: **PUSTY env** (flaga z flags.json).
- `dispatch-czasowka`: `CZASOWKA_TELEGRAM_DRYRUN=1` (KOORD alerty czasówki = **dry-run**, klasa D — efektywne ≠ „live alert"), RETROACTIVE_HOURS=2, MAX_EMIT_PER_TICK=3.
- `dispatch-sla-tracker`, `dispatch-telegram`, `dispatch-monitor-419`, `dispatch-gps`: brak env decyzyjnego (PYTHONPATH/PYTHONUNBUFFERED).
- `gate-audit`: env pusty, ale **venv courier_api** + workdir courier_api (cross-repo — należy do repo courier_api, nie dispatch_v2).
- `courier-api`: 8× `ENABLE_*` (BUILD_VIEW_TRUST_CANON_ORDER, DELIVERED_TOO_FAST_GUARD, DELIVERY_DASH_WHEN_NO_PLAN, PICKUP_TIME_READY_FALLBACK, PLAN_AWARE_PODJAZDY, LIVE_ETA_COURIER_GUARD, APP_ROUTE_FROM_CONSOLE) + 2 creds.
- `nadajesz-panel`: 3× `PANEL_FLAG_*` (TRUST_CANON_ORDER, TRUST_CANON_WHEN_COVERS_BAG, DELIVERY_DASH_WHEN_NO_PLAN) + ksef/wfirma env.

### B.5 — Doc-drift recon (D)
Recon ETAP0 (sekcja C) listuje plan-recheck drop-iny BEZ `ENABLE_LEX_COMMITTED_WINDOW_SHADOW=1`, który jest w efektywnym env (plan-recheck l.26, panel-watcher l.50, b-route l.218). Drobny dryf „deklarowane≠efektywne".

---

## (c) CROSS-REPO INWENTARZ (J) — gdzie logika decyzyjna jest KOPIĄ vs wspólnym importem

### C.0 — Topologia repo/venv (4 runtime)
| Repo / katalog | git | venv | Serwisy | Rola |
|---|---|---|---|---|
| `scripts/dispatch_v2` | własny (HEAD 8024705, master) | `/root/.openclaw/venvs/dispatch` | shadow, panel-watcher, gps, sla, monitor-419, parcel-merge, wszystkie shadow/timery | **silnik** |
| `scripts/courier_api` | własny (worktree-host) | `courier_api/.venv` | courier-api, gate-audit, courier-panel-sync(*) | backend apki + autorytet build_view |
| `scripts/courier_api_panelsync` | **worktree courier_api** (branch panel-sync, `4ab1e6d`) | courier_api/.venv | courier-panel-sync (panel_sync.py) | odbicie statusów 3-7 do gastro |
| `nadajesz_clone/panel` | własny (coordinator-console, `222c713`) | `panel/backend/.venv` → **python3 systemowy** | nadajesz-panel, nadajesz-parcel-shadow | konsola koordynatora |
| `/opt/nadajesz-ordering` | **brak .git** (deploy-kopia ordering-site) | node/npm | nadajesz-ordering | front zamawiania |
| `courier-app` (`/root/courier-app`) | własny | Kotlin/Gradle (APK) | — | apka kuriera |
| `papu_dispatch_bridge` | — | /usr/bin/python3 | papu-bridge | **GRANICA — Papu, poza zakresem** |

### C.1 — KONSOLA `nadajesz_clone/panel/backend` (klasa J — render trasy = KOPIA)
| Plik | dispatch_v2? | Charakter | Dowód |
|---|---|---|---|
| `app/integrations/ziomek/fleet_state.py` (1181 L) | **NIE** | **KOPIA** `_eta_chain` (250) + `_build_route` (395): carried-first, relax, pin-pickup, OSRM-chain re-implementowane | grep: 0 importów dispatch_v2; parytet flagą TRUST_CANON_ORDER, „≡ 95.9%" (l.866) |
| `app/integrations/ziomek/feed.py` (387 L) | **NIE** | render puli z `shadow_decisions.jsonl` + **overlay** 3 kanałów JSON (`global_alloc.json` resweep + `reassign_global_alloc.json` reassign-select) z TTL-freshness fail-soft | `_load_global_alloc_fresh:31`, `_load_reassign_select_fresh:55` |
| `app/integrations/ziomek/route.py` (119 L) | subprocess | **SHIM** — `subprocess.run` (l.104) do venva Ziomka uruchamia `plan_manager` (CAS+lock+walidacja); apka czyta plik | l.15 `import subprocess`, l.86 string `from dispatch_v2 import plan_manager` |
| `app/integrations/ziomek/coordinator_time_recheck.py` (73 L) | subprocess | SHIM do `dispatch_v2.coordinator_time_recheck` | l.30 string |
| `app/integrations/ziomek/shadow_quote.py` | in-process/subprocess | importuje `assess_order`, `dispatchable_fleet`, `geocode` (quote = realny silnik) | l.332-334 |
| `app/integrations/ziomek/committed_time.py` | **import** | `from dispatch_v2.common import …` (pure-python) | l.27 |
| `app/integrations/ziomek/courier_block.py` | **import** | `from dispatch_v2 import manual_overrides` | l.72 |
| `app/integrations/ziomek/courier_provision_bridge.py` | **import** | `courier_admin` (KURIER_PINY/IDS), `panel_roster` | l.40-89 |
| `app/integrations/ziomek/{parcel_lane,parcel_dispatch_shadow}.py` | subprocess | parcel: shadow = subprocess assess_order; lane = sidecar pisze snapshot | poniżej C.4 |

**Werdykt J:** konsola = HYBRYDA. Zapis kanonu/quote/provision → **wspólny silnik** (subprocess lub in-process import = OK). Ale **RENDER trasy/ETA/puli (`fleet_state`+`feed`) = KOPIA** re-implementująca carried-first/relax/OSRM-chain + własny overlay. To rdzeń klasy J: render-decyzja nie importuje budowniczego kanonu; parytet statystyczny (95.9%) + flaga, nie z konstrukcji.

### C.2 — `courier_api` (apka) — autorytet build_view + 3. kopia carried-first
`courier_orders.py` (1285 L): `build_view` (1072) = autorytet kolejności apki; `_plan_stop_sequence` (672), `optimize_route` (265), `_prioritize_carried_dropoffs` (467) = **WŁASNY carried-first fallback** gdy `BUILD_VIEW_TRUST_CANON_ORDER=OFF` (l.1112 komentarz „OFF → lokalny carried-first"). `_compute_live_eta` (794), `_attach_fallback_eta`. → carried-first istnieje 3× (silnik/konsola/courier_api), parytet flagami TRUST_CANON; każda ma lokalny fallback rozjeżdżalny gdy kanon nieobecny/nie-pokrywa.

### C.3 — `courier-app` Kotlin (`/root/courier-app`) — RENDER serwera + 4. kopia bundlingu
`ui/screen/RouteLogic.kt`: `buildSteps` (27) iteruje `r.stopSequence` (**z serwera**, NIE re-sortuje — grep `sortedBy/sortBy` na stopach = 0 trafień) → render kolejności serwera. ALE ma WŁASNĄ regułę „jedna wizyta = jedna restauracja": `restaurantKey` (23), `pickupTogether` (62, okno pickup_time), grupowanie same-restaurant (l.40-45). → reguła bundlingu odbiorów = 4. kopia (silnik/konsola `fleet_state`/courier_api/apka). Kolejność = serwerowa (parytet OK), grupowanie-display = re-kodowane per powierzchnia.

### C.4 — MOST PACZKI (parcel lane) — handoff JSON cross-repo (J + O)
- panel sidecar `app/integrations/ziomek/parcel_lane.py` (194 L) → pisze `dispatch_state/orders_state.parcels_shadow.json`.
- silnik `dispatch_v2.parcel_lane_merge` (timer `parcel-merge` 30s, venv dispatch) → czyta snapshot, `state_machine.upsert_order` do ŻYWEGO orders_state (NIEOBECNA→utwórz, OBECNA→POMIŃ; Etap3c statusy apki 5/7). Brama `ENABLE_PARCEL_LANE_LIVE` (flags.json) — OFF=no-op.
- panel `parcel_dispatch_shadow.py` (143 L) → **subprocess assess_order** w venv Ziomka („ta sama ścieżka co /coordinator/quote").
**Smell J/O:** decyzja paczki rozdzielona na 2 repo + 2 venvy, spięta plikiem JSON (multi-writer) + DWIE flagi-bramki (panel `PANEL_FLAG_*` + silnik `ENABLE_PARCEL_LANE_LIVE` w flags.json) — brak jednej bramki.

### C.5 — `courier_api_panelsync` — worktree-fork logiki statusów (J + O)
`courier_api_panelsync/{courier_orders.py(665 L), status_store.py}` ZDYWERGOWANE od głównej (`courier_api/courier_orders.py` 1285 L; `status_store.py` też differ). `.git` → `worktrees/courier_api_panelsync`, branch panel-sync (`4ab1e6d feat(panel-sync): change-status wielokontowy cid 21+123`, clean). `courier-panel-sync.service` uruchamia `panel_sync.py` z TEGO worktree, ale **venvem głównego courier_api**. → dwie checked-out kopie repo courier_api z `courier_orders.py`/`status_store.py`; jedna karmi live API (build_view), druga panel-sync (odbicie statusów 3-7 do gastro). Ryzyko: mapowanie statusów zdublowane/rozjazd przy zmianie jednej kopii.

### C.6 — `/opt/nadajesz-ordering` — deploy-kopia bez gita (J/K, peryferyjne)
`nadajesz-ordering.service` biega z `/opt/nadajesz-ordering` (BRAK `.git`), RÓŻNI się od źródła `nadajesz_clone/ordering-site` (AGENTS.md, .env*, .next build artefakty differ). Customer-front zamawiania — **brak logiki decyzyjnej dispatchu** (grep stop_sequence/build_view/assess_order = 0). Odnotowane jako deploy-drift, niska istotność decyzyjna.

### C.7 — ⭐ 3 NIEZALEŻNE SYSTEMY FLAG (J/D — brak jednego rejestru)
| System | Źródło | Resolver | Hot-reload | Zakres |
|---|---|---|---|---|
| **silnik** | `flags.json` (~140) + env drop-iny per-serwis + `common.py` default | `common.decision_flag()` / `C.flag()` | TAK (flags.json) | shadow/plan-recheck/panel-watcher/czasowka/shadowy |
| **konsola** | `app/core/flags.py DEFAULT_FLAGS` dict + env `PANEL_FLAG_<NAME>` | `flag(name)` (l.127: env override → dict default) | NIE (env raz przy starcie; dict baked) | nadajesz-panel |
| **courier_api** | `config.py` (env `ENABLE_*` z courier-api.service.d) | `config.BUILD_VIEW_TRUST_CANON_ORDER` … | NIE | courier-api/gate-audit |

Konsola `flag()` zna nazwy bez prefiksu (`TRUST_CANON_ORDER`), wewn. czyta `PANEL_FLAG_TRUST_CANON_ORDER`. `DEFAULT_FLAGS` ma WIĘCEJ flag niż env (MONOTONIC_ROUTE_TIMES=True, LIVE_ETA_FRESH_OVERRIDE_ONLY=True, PICKUP_DELAY_NOTICE=False, PANEL_FLAG_SKIP_INVALIDATED_PLAN, SOONEST_UNDER_LOAD, AFTER_HOURS_BLOCK…) → display-decyzyjne flagi konsoli rozstrzygane wyłącznie po stronie panelu, niewidoczne w rejestrze silnika. **Dashboard entropii (dead-flag/copy-count) MUSI obejmować 3 systemy, nie 1.**

### C.8 — MACIERZ KOPII LOGIKI DECYZYJNEJ (rdzeń J)
| Reguła | silnik dispatch_v2 | konsola fleet_state | courier_api | apka Kotlin | Parytet przez |
|---|---|---|---|---|---|
| Kolejność trasy (carried-first + relax) | `_apply_canon_order_invariants` | `_build_route:395` (KOPIA) | `build_view:1072`/`_prioritize_carried_dropoffs:467` (KOPIA) | render `stopSequence` (serwer) | flagi TRUST_CANON (×3 silniki) + golden test |
| ETA-chain stopów | route_simulator/OSRM | `_eta_chain:250` (KOPIA) | `_compute_live_eta:794`/`_attach_fallback_eta` | render | MONOTONIC_ROUTE_TIMES + LIVE_ETA flagi |
| Bundling „1 restauracja = 1 wizyta" | same_restaurant_grouper | `_build_route` grupowanie | `_plan_stop_sequence` | `buildSteps`/`pickupTogether:62` | ręcznie ×4 |
| Czas odbioru frozen (committed/pin) | R27/route_simulator | pin-agreed-pickup w `_build_route` | `ENABLE_PICKUP_TIME_READY_FALLBACK`/frozen | render | PIN_AGREED + flagi per repo |
| Pula kandydatów / overlay | shadow_decisions + resweep/global_select (osobne procesy) | `feed._load_*_fresh` overlay (KOPIA merge) | — | — | TTL freshness fail-soft (O) |

---

## (d) WORKTREE INWENTARZ

| Repo | Worktree | Ścieżka | Branch / HEAD | Znaczenie |
|---|---|---|---|---|
| **dispatch_v2** | (jedyny) | `scripts/dispatch_v2` | master `8024705` | Czysto — ZERO dodatkowych worktree silnika. (Recon ETAP0: brak edycji .py.) |
| **nadajesz_clone** | główna | `nadajesz_clone` | `coordinator-console 222c713` | konsola LIVE (nadajesz-panel) |
| **nadajesz_clone** | `ndj-client-panel` | **`/root/ndj-client-panel`** | `feat/client-panel 71fdcaf` | panel klienta (poza workspace!) |
| **nadajesz_clone** | `ndj-parcel` | **`/root/ndj-parcel`** | `feat/parcel-ordering 764a07a` | zamawianie paczek |
| **nadajesz_clone** | `nadajesz-sms-wt` | **`/root/nadajesz-sms-wt`** | `feat/sms-customer-tracking 2911984` | SMS tracking klienta |
| **courier_api** | główna | `scripts/courier_api` | (host worktrees) | courier-api LIVE + build_view autorytet |
| **courier_api** | `courier_api_panelsync` | `scripts/courier_api_panelsync` | panel-sync `4ab1e6d` | **fork courier_orders.py 665 vs 1285 L** |

**Uwaga lokalizacji:** worktree nadajesz_clone leżą w `/root/` (NIE pod `/root/.openclaw/workspace`) — dlatego seed-find ich nie złapał; znalezione przez `git worktree list`. Każdy = osobna checked-out kopia panelu z potencjalnie zdywergowanymi `fleet_state.py`/`feed.py`/`flags.py`. **Wyścig wspólnego indeksu git** (recon C1, near-miss `78401ed`→`976afbf`) dotyczy multi-sesji na TYCH repach.

---

## SYNTEZA — ledger klas dla Fazy D/E + dashboard entropii

| Klasa | Instancje z A5 | Status |
|---|---|---|
| **J** (cross-repo/multi-proces/worktree) | fleet_state/feed = kopia renderu; carried-first ×3-4; ETA-chain ×3; bundling ×4; 3 systemy flag; parcel JSON-handoff 2-repo; panelsync fork; b-route hand-parity; 6 worktree w 2 repach | PIERWSZOPLANOWA — wiele CONFIRMED kopii, parytet statystyczny |
| **B** (asymetria bliźniaków) | plan-recheck vs panel-watcher (3 flagi route brak); carried-first fallback per powierzchnia | CONFIRMED env-asym; materialność PLAUSIBLE (Faza B/C) |
| **D** (dryf flag) | 3 niezależne systemy flag bez rejestru; shadow bez canon-env; czasowka DRYRUN; recon doc-drift LEX_SHADOW; konsola brak hot-reload | CONFIRMED rozsyp |
| **K** (martwy/szczątkowy) | 4 systemd .bak + 2 retired + orphan shift-notify dir; courier_api 40 + panel 72 + flags.py 11 .bak | CONFIRMED graveyard |
| **O** (współbieżność/wyścig) | pending_proposals 3-writer/no-lock (telegram muted); feed overlay 3 async JSON TTL; parcel JSON handoff; panelsync 2 working-copies; git index multi-sesja | CONFIRMED handoffy bez locka |
| **M** (sentinele/cicha awaria) | feed `_load_*_fresh` fail-soft → {} (overlay znika cicho gdy stale) | render-warstwa; pełny sweep = Faza B klasa M |

### Handoff — co Fazy B/C/D MUSZĄ wiedzieć z osi A5
1. **Faza D (precedencja):** OŚ KONFLIKTU ścieżek = `fleet_state._build_route` (konsola) ↔ `courier_orders.build_view` (apka) ↔ kanon silnika. Precedencja dziś = flaga TRUST_CANON per powierzchnia (3 niezależne przełączniki) → gdy choć jedna OFF lub kanon nie-pokrywa, render rozjeżdża się fallbackiem. Parytet = 95.9% POMIAR, nie inwariant.
2. **Faza D (flagi):** graf flag MUSI objąć 3 systemy (flags.json / panel DEFAULT_FLAGS+PANEL_FLAG_ / courier_api config.ENABLE_) — flaga „ta sama" (TRUST_CANON_ORDER) istnieje 2× (PANEL_FLAG_ vs BUILD_VIEW_) i defaultuje niezależnie.
3. **Faza B (twin):** zweryfikować czy panel-watcher recanon (`_retime_one_bag_plan`/`_gen_one_bag_plan`) materialnie różni się od plan-recheck ticku przez brak SEQUENCE_LOCK/COMMITTED_PROPAGATION/LIVE_ETA_REFRESH (trace osiągalności). I czy shadow buduje plan in-process bez canon-env (B.3).
4. **Faza C (instrument):** `feed` overlay (global_alloc.json, reassign_global_alloc.json) freshness-TTL + `b-route route-flag-parity` = przyrządy/parytety do odpalenia oracle; `ziomek-time-route-monitor` = istniejący monitor parytetu konsola↔apka (czytać werdykt).
5. **PoC „one route-order module" (Faza F):** musi przepiąć WSZYSTKIE 4 powierzchnie (silnik+konsola+courier_api+apka) — inaczej kopia wraca. Wzór parytetu = `b-route route-flag-parity.conf` (golden-fixture + recepta diff). Worktree-rozsyp (6 kopii w 2 repach) = ryzyko przy każdym przepięciu.

### Coverage / luki (jawnie)
- NIE czytałem PEŁNYCH ciał `_build_route`/`build_view` (tylko nagłówki+komentarze) → magnituda rozjazdu = Faza B/C.
- NIE odpaliłem runtime-diff kanon↔konsola↔apka (oracle = Faza C, instrument `ziomek-time-route-monitor` istnieje).
- NIE potwierdziłem czy `shadow_dispatcher` buduje plan in-process (B.3 PLAUSIBLE) — trace Faza B.
- NIE zdiffowałem funkcyjnie `courier_api_panelsync/courier_orders.py` (665 L) vs główną — trimmed czy zdywergowany — Faza B.
- `papu_dispatch_bridge` = GRANICA (Papu), zinwentaryzowany jako boundary, NIE analizowany.
- ~70 `dispatch-onfailure-alert@*` + peryferyjne dead-unity policzone zbiorczo, nie per-sztuka.
