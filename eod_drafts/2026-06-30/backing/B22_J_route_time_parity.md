# B22 — KLASA J DEEP: PARYTET KOLEJNOŚCI-TRASY i CZASU-ODBIORU cross-repo

**Agent:** B22-J-route-time-parity · **lane B** · **READ-ONLY** · **2026-06-30 ~16:10 UTC**
**HEAD silnika:** `8024705` (working tree `.py` czysty). **Wszystkie `plik:linia` z ŚWIEŻEGO grepu DZIŚ** (linie dryfują — ≥3 sesje na wspólnym repo).
**Zakres:** kolejność-jazdy (route-order) + czas-odbioru/ETA (time) renderowane na 3 repach (silnik `dispatch_v2`, konsola `nadajesz_clone/panel`, apka `courier_api`) + apka Kotlin (granica — nieczytana) + monitor parytetu. STOP na dyspozytorni.
**Metoda:** świeży `grep -nE 'def …'` per powierzchnia · `systemctl show -p Environment` (efektywny stan flag per-proces, NIE flags.json) · odczyt+parsing ŻYWEGO `ziomek_time_route_monitor.jsonl` (1613 rek., per-dzień) · lektura ciał 3 rendererów czasu.

---

## TL;DR — 9 twardych faktów J

1. **Reguła kolejności-trasy = 1 writer kanonu + 5 kopii-rendererów w 3 repach, BEZ wspólnego importu.** Parytet trzymany 5 flagami × 3 systemy flag + 1 runtime-monitor + golden-testy — NIGDY z konstrukcji. (J1)
2. **★ Twin #11 (route-ORDER) ZAMKNIĘTY NUMERYCZNIE 29.06 — LIVE:** monitor `q3_route_mismatches` spadł **112 (23.06) → 0 (29.06) → 0 (30.06, 364 sprawdzeń/0 rozjazdów)**. To liczba z VALIDATED monitora (C4/C10), nie lektura. ALE parytet trzyma 5 flag ON — flip jednej = nawrót. (J2)
3. **★ BRAK wspólnego renderera CZASU — 3 niezależne implementacje ETA-chain z RÓŻNYMI stałymi dwell + clampem.** Silnik `chain_eta.compute_chain_eta` / konsola `_eta_chain` (płaskie +90s/stop, baza=max(now,shift_start)) / apka `_attach_fallback_eta` (+120s odbiór/+60s dostawa, baza=now BEZ clampu). Żaden nie importuje pozostałych. **ŻADEN instrument nie porównuje WARTOŚCI ETA per-stop** między powierzchniami. (J3)
4. **★ Świeża asymetria clampu pre-shift (DZIŚ 30.06):** konsola `_eta_chain` dostała `CLAMP_PRESHIFT_PICKUP_ETA` (baza=max(now,shift_start)), apka `_attach_fallback_eta` NIE (t=now). Apka fallback może pokazać odbiór sprzed startu zmiany. Twin-w-1-z-2 (klasa #11), wprowadzony DZIŚ. (J4)
5. **★ Kanon ma 2 WRITERÓW z RÓŻNYMI inwariantami** (rozstrzyga konflikt A5 vs recon — A5 ma rację): plan-recheck tick MA `SEQUENCE_LOCK`+`COMMITTED_PROPAGATION`+`LIVE_ETA_REFRESH`; panel-watcher recanon (odpala na KAŻDY write/pickup/override) NIE MA żadnego z 3. Oba piszą `courier_plans.json`, któremu ufają wszystkie renderery. Monitor tego NIE łapie (oba renderery czytają ten sam kanon). (J5)
6. **Monitor parytetu ma luki wierności (instrument E/J):** porównuje konsolę↔apkę, NIGDY przeciw kanonowi silnika; `start=None` (gałęzie pozycyjne nie odpalają); `route_console` hardkoduje `trust_canon_ok=True` → POMIJA `_resolve_invalidated_plan` → nie odtwarza konsoli dla planów `invalidated` (dokładnie case Jakub W/Piotr K z fixu 29.06). „0 mismatch" pod-certyfikuje. (J6)
7. **3 niezależne systemy flag, brak jednego rejestru:** konsola `PANEL_FLAG_*` (flags.systemd.env, brak hot-reload) / apka `config.ENABLE_*` (courier-api.service.d, brak hot-reload) / silnik `flags.json` (hot) + drop-iny plan-recheck/panel-watcher. „TRUST_CANON_ORDER" istnieje 2× (`PANEL_FLAG_` vs `BUILD_VIEW_`), defaultuje niezależnie. (J9)
8. **Stałe ręcznie zsynchronizowane komentarzem, nie importem:** `PICKUP_MERGE_MIN=10` w `fleet_state:88` ORAZ `route_podjazdy:30` („= fleet_state"); `_plan_pickup_clusters` zduplikowane konsola↔apka-silnik („lustro"). (J7)
9. **panelsync `courier_orders.py` (665L) = MARTWY fork route-order/time** — `courier-panel-sync` odpala `panel_sync.py`, który importuje `config/db/panel_kurier/panel_lite`, NIE `courier_orders`. (J8)

---

## (A) MACIERZ KOPII — KOLEJNOŚĆ TRASY (route-order)

Reguła: kolejność JAZDY = kanon `courier_plans.json` VERBATIM (carried-first-relax „odbierz po drodze zanim dowieziesz niesione" + no-return-to-departed-pickup). To NIE display — realna kolejność jazdy (C8).

| Rola | Plik:func:linia (świeże) | Repo / proces | Importuje kanon-choke? | Stan |
|---|---|---|---|---|
| **WRITER kanonu (tick)** | `plan_recheck._apply_canon_order_invariants:1478` (+`_relax_carried_first:1003`, woła `:1526`; build `:780`, retime `_retime_one_bag_plan:1582`) | dispatch_v2 / **dispatch-plan-recheck** (5min) | — (źródło) | pisze JSON |
| **WRITER kanonu (event)** | `plan_recheck.recanon_courier:1798` / `redecide_courier:1736` (woła te same invarianty, `:1582`) | dispatch_v2 / **dispatch-panel-watcher** (on write/pickup/override) | — (źródło) | **inny env — J5** |
| render apka-silnik | `route_podjazdy.order_podjazdy:190` + `_canon_order_from_plan:141` (carried-first relax `:146`) | dispatch_v2 | ✗ własna kopia (docstring `:10-12`) | render |
| render KONSOLA | `fleet_state._build_route:395` + `_order_from_plan_seq:342` + `_resolve_invalidated_plan:370` | **panel (cross-repo)** | ✗ **0 importów dispatch_v2** (potwierdzone grep) | render |
| render APKA-API (live) | `courier_orders.build_view:1072` → `_route_podjazdy.order_podjazdy:1118` (gdy `APP_ROUTE_FROM_CONSOLE`); else `_plan_stop_sequence:672`+`_prioritize_carried_dropoffs:467` | scripts/courier_api | ⚠ importuje `route_podjazdy:38` TYLKO za flagą | render |
| render APKA-API (**DEAD**) | `courier_api_panelsync/courier_orders.build_view:558` + `_plan_stop_sequence:366`+`optimize_route:188` | scripts/courier_api_panelsync | ✗ martwa | **K — niserwowana** |

**Parytet:** engine-choke→render = `GOLDEN-TEST` (`test_route_podjazdy_trust_canon`, `test_precedence_hierarchy_snapshot`). Konsola↔apka cross-repo = `RUNTIME-MONITOR` (`ziomek_time_route_monitor`) — JEDYNY mechanizm repo↔repo. panelsync = `NIC` (martwa).

### Flagi efektywne DZIŚ (systemctl show — wszystkie ON → parytet route-order):
- konsola `nadajesz-panel`: `PANEL_FLAG_TRUST_CANON_ORDER=1`, `PANEL_FLAG_TRUST_CANON_WHEN_COVERS_BAG=1` (fix 29.06), `PANEL_FLAG_CLAMP_PRESHIFT_PICKUP_ETA=1` (flags.systemd.env:116), `PIN_AGREED_PICKUP_TIME=True` (DEFAULT_FLAGS:21).
- apka `courier-api`: `ENABLE_BUILD_VIEW_TRUST_CANON_ORDER=1`, `ENABLE_APP_ROUTE_FROM_CONSOLE=1`, `ENABLE_PLAN_AWARE_PODJAZDY=1`, `ENABLE_PICKUP_TIME_READY_FALLBACK=1` (+ `FROZEN_PICKUP_ETA`/`PLAN_ORDER_INVARIANTS`/`FALLBACK_HONEST_OSRM_ETA` env-default ON).
- **C5 near-miss rozwiązany:** `BUILD_VIEW_TRUST_CANON_ORDER` była martwa (short-circuit `APP_ROUTE_FROM_CONSOLE`), teraz PRZEKAZANA do `order_podjazdy(..., trust_canon=…)` `courier_orders:1120` → wpięta.

---

## (B) MACIERZ KOPII — CZAS / ETA (time renderer) — ★ NAJWIĘKSZA OTWARTA LUKA J

Reguła: czas odbioru committed (`czas_kuriera`) po przypisaniu NIETYKALNY (R27 ±5); ETA per stop = baza + skumulowana jazda + dwell. **3 implementacje, 0 wspólnego importu, RÓŻNE stałe.**

| Renderer czasu | Plik:func:linia | Baza | Dwell/stop | Clamp pre-shift | Frozen-pickup |
|---|---|---|---|---|---|
| **SILNIK** | `chain_eta.compute_chain_eta:45` | `now_utc` + prep-scheduling | model speed×traffic mult (`:56-67`, NIE płaski) | — (clamp w `dispatch_pipeline` osobno) | — (R27 w route_simulator) |
| **KONSOLA** | `fleet_state._eta_chain:250` (woła `_build_route:495`) | `max(now, depart_after)` `:264` | **płaskie +90.0s** `:268` | ✅ `depart_after`=shift_start (`CLAMP_PRESHIFT_PICKUP_ETA`, 30.06) | `PIN_AGREED_PICKUP_TIME` w `_build_route:509` (osobna gałąź kolejności) |
| **APKA** | `courier_orders._attach_fallback_eta:822` | **`t=time.time()`** `:859` (now) | **+120s odbiór / +60s dostawa** `:917` | ❌ **BRAK shift_start** | ✅ `FROZEN_PICKUP_ETA` `:872` plan→committed + `PICKUP_READY_FLOOR` `:877` (floor do gotowości, NIE shift_start) |
| (apka 2. ścieżka) | `courier_orders._compute_live_eta:794` / `_committed_pickup_eta:641` | now+drive | — | ❌ | committed |

**Skutki rozjazdu (CONFIRMED z kodu):**
- **Dwell:** worek 1-odbiór+3-dostawy-carried → konsola 4×90=360s, apka 1×120+3×60=300s = **60s rozjazd ETA**; pickup-heavy 3+1 → konsola 360s vs apka 420s. ETA per stop rozjeżdża się z KOMPOZYCJĄ worka.
- **Clamp pre-shift:** konsola flooruje bazę do startu zmiany (30.06), apka fallback NIE → apka może pokazać odbiór sprzed zmiany (= dokładnie bug, który `CLAMP_PRESHIFT_PICKUP_ETA` naprawił w konsoli, NIEzmirrorowany w apce). Potwierdza floor-audit grupa 6 (#10/#11 „apka fallback bez floor").
- **Brak instrumentu:** monitor `q3` = identyczność KOLEJNOŚCI (z `start=None`!), `q2` = stabilność POLA `czas_kuriera_hhmm` tick-do-ticku. **ŻADEN tick nie porównuje WARTOŚCI ETA-chain per-stop konsola vs apka vs silnik.** „Brak wspólnego renderera czasu" = potwierdzone + nieobserwowane.

`PICKUP_MERGE_MIN=10` (próg sklejania odbiorów w podjazd) = **ręczna kopia**: `fleet_state:88` ORAZ `route_podjazdy:30` (komentarz „= fleet_state"). `_plan_pickup_clusters` zduplikowane: `fleet_state:277` (komentarz „Lustro route_podjazdy") + `route_podjazdy:57`.

---

## (C) KANON: 2 WRITERZY, RÓŻNE INWARIANTY (J5 — rozstrzyga konflikt Faza A)

`systemctl show -p Environment` (efektywny per-proces — NIE flags.json):

| Flaga kanonu | plan-recheck (tick 5min) | panel-watcher (recanon on write/pickup/override) |
|---|:--:|:--:|
| ENABLE_PLAN_CANON_ORDER_INVARIANTS | ✅ | ✅ |
| ENABLE_CARRIED_FIRST_RELAX | ✅ | ✅ |
| ENABLE_NO_RETURN_TO_DEPARTED_PICKUP | ✅ | ✅ |
| ENABLE_NONCARRIED_DROPOFF_REORDER / RELAX_COLOC_PICKUP | ✅ | ✅ |
| **ENABLE_PLAN_SEQUENCE_LOCK** | ✅ | ❌ |
| **ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION** | ✅ | ❌ |
| **ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH** | ✅ | ❌ |
| ENABLE_RECANON_ON_WRITE / IMMEDIATE_REDECIDE_ON_{OVERRIDE,PICKUP} | ❌ | ✅ |

**Rozstrzygnięcie:** A5 §B.1 (panel-watcher LACKS 3 flag) = POPRAWNE; recon ETAP0 §C (listował COMMITTED_PROPAGATION/LIVE_ETA_REFRESH na panel-watcher) = NIEŚCISŁE — efektywny env tego NIE potwierdza. **Konsekwencja:** plan kuriera regenerowany przez event-recanon (po pickupie/override — częściej niż 5-min tick) NIE stosuje sequence-lock / propagacji committed-czasu / live-ETA-refresh. Wszystkie 3 renderery wiernie renderują TEN (pod-inwariantny) kanon → są w parytecie ze sobą (monitor 0), ale SAM KANON migocze między 2 writerami. **Materialność = PLAUSIBLE** (A5: `_retime_one_bag_plan` może być sekwencyjnie-zachowawczy → SEQUENCE_LOCK moot; ale brak COMMITTED_PROPAGATION/LIVE_ETA_REFRESH = realna różnica) — wymaga trace Faza B/C.

---

## (D) MONITOR PARYTETU — analiza wierności (J6, instrument E/J, C9/C11)

Źródło: `nadajesz_clone/panel/backend/tools/ziomek_time_route_monitor.py` (czytane całe). Output: `dispatch_state/ziomek_time_route_monitor.jsonl` (940KB, mtime **16:02 FRESH**, 1613 rek. od 19.06). Timer `ziomek-time-route-monitor` 10min, venv panelu, `EnvironmentFile=flags.systemd.env` (42 PANEL_FLAG = pełna wierność flag konsoli).

### Liczba LIVE (per-dzień, route-MISMATCH + time-drift):
```
2026-06-23: route_checked= 469  route_MISMATCH=112  time_drift= 22
2026-06-24:             521              41               20
2026-06-25:             498              58               12
2026-06-26:             652              75               31
2026-06-27:             525              44                5
2026-06-28:             625              30               16
2026-06-29:             520     ★ MISMATCH=  0           29
2026-06-30:             364     ★ MISMATCH=  0           11   (96 ticków, 17 alertów)
```
**Route-ORDER twin (#11) ZAMKNIĘTY** 29-30.06 (fix `TRUST_CANON_WHEN_COVERS_BAG` 29.06). **time-drift (q2) WCIĄŻ żywy** (11-29/d).

### Co monitor MIERZY (i czego NIE):
- `route_console` = `_build_route(plan_doc, bag, **None**, {oid:{}})` `:204` — **start=None** (brak pozycji kuriera) + domyślne `trust_canon_ok=True`.
- `route_app` = `RP.order_podjazdy(bag, plan_doc, plan_aware, trust_canon)` `:220` — mirroruje courier-api flagi czytając drop-iny (`_plan_aware_flag_on`/`_build_view_trust_canon_flag_on`, fix #15).
- Porównuje TYLKO `(type, tuple(order_ids))` `:294` = identyczność KOLEJNOŚCI.

### Luki wierności (instrument pod-certyfikuje):
1. **Konsola↔apka, NIGDY ↔ kanon silnika.** Oba renderery czytają ten sam `courier_plans.json`; monitor nie re-uruchamia `_apply_canon_order_invariants`. Gdy OBA renderery dryfują od kanonu identycznie → `mismatch=0` (fałszywy parytet). Engine↔render pokrywają golden-testy, nie ten monitor.
2. **`start=None`** → gałęzie pozycyjne (`_build_route` OSRM-monotonic `:542`, carried-first OSRM, CLAMP wymaga pozycji+shift) NIE odpalają. Monitor certyfikuje parytet kolejności PLAN-DRIVEN, nie fallbacku pozycyjnego.
3. **`route_console` hardkoduje `trust_canon_ok=True`** (default param) → POMIJA `_resolve_invalidated_plan:370`. Live-konsola liczy `trust_canon_ok` przez ten resolver (`:874`) i przekazuje. Dla planów `invalidated` monitor NIE odtwarza konsoli — czyli case Jakub W/Piotr K (invalidated), który fix 29.06 adresował, jest poza zasięgiem monitora. „0 mismatch" pod-liczy rozjazd na invalidated.
4. **q2 time-drift NIE odróżnia legit od illegit:** czas zmieniony przez przycisk koordynatora force-recheck / edycję rutcom / fix closest-day-anchor (30.06) = liczony jako „drift" tak samo jak nawrót buga. 17 alertów dziś (część prawdopodobnie legalna).

Monitor JEST `VALIDATED` (A4 #11, „twin kolejność konsola↔apka") — ale dla NIE-invalidated, position-free, ORDER-identity. **Nie certyfikuje:** parytetu ETA-wartości, fallbacku pozycyjnego, invalidated-path, kanon↔render.

---

## (E) 3 SYSTEMY FLAG bramkujące JEDEN parytet (J9, D)

| System | Źródło | Hot-reload | Flagi parytetu route/time |
|---|---|:--:|---|
| **silnik** | `flags.json` + drop-iny `dispatch-plan-recheck.service.d`/`panel-watcher.service.d` | TAK (json) / env-frozen (drop-in) | CANON_ORDER_INVARIANTS, CARRIED_FIRST_RELAX, SEQUENCE_LOCK, NO_RETURN, COMMITTED_PROPAGATION, LIVE_ETA_REFRESH |
| **konsola** | `flags.systemd.env` (42 PANEL_FLAG) + `app/core/flags.py DEFAULT_FLAGS` | NIE | TRUST_CANON_ORDER, TRUST_CANON_WHEN_COVERS_BAG, PIN_AGREED_PICKUP_TIME, CLAMP_PRESHIFT_PICKUP_ETA, MONOTONIC_ROUTE_TIMES, LIVE_ETA_FRESH_OVERRIDE_ONLY |
| **apka** | `config.py` (env `courier-api.service.d`) | NIE | BUILD_VIEW_TRUST_CANON_ORDER, APP_ROUTE_FROM_CONSOLE, PLAN_AWARE_PODJAZDY, FROZEN_PICKUP_ETA, PLAN_ORDER_INVARIANTS, FALLBACK_HONEST_OSRM_ETA, PICKUP_TIME_READY_FALLBACK |

„TRUST_CANON_ORDER" = 2 niezależne flagi (`PANEL_FLAG_TRUST_CANON_ORDER` vs `ENABLE_BUILD_VIEW_TRUST_CANON_ORDER`), default niezależny → flip jednej de-synchronizuje parytet cicho. Brak jednego rejestru cross-repo (dashboard entropii MUSI objąć 3 systemy).

---

## FINDINGS (file:linia świeży · źródło/objaw · łatane? · otwarte? · severity · dowód · dedup)

| ID | klasa | file:linia | kind | summary | patched | open | sev |
|---|---|---|---|---|:--:|:--:|---|
| **B22-J1** | J/A1 | `plan_recheck.py:1478` + `route_podjazdy.py:190` + `fleet_state.py:395` + `courier_orders.py:1072` + panelsync | source | route-order = 1 writer + 5 render-kopii / 3 repa, 0 wspólnego importu; parytet 5 flag × 3 systemy + monitor + golden | nie | TAK | P2 |
| **B22-J2** | J | `ziomek_time_route_monitor.jsonl` | symptom | twin #11 route-ORDER rozjazd 112→0 (29-30.06), `TRUST_CANON_WHEN_COVERS_BAG` fix; parytet flag-held (fragile) | TAK | nie* | P3 |
| **B22-J3** | J/A1 | `chain_eta.py:45` / `fleet_state.py:250` / `courier_orders.py:822` | source | 3 niezależne renderery ETA, różne dwell (90 vs 120/60) + baza, 0 wspólnego importu, 0 instrumentu wartości | nie | TAK | P2 |
| **B22-J4** | B/J | `fleet_state.py:264` vs `courier_orders.py:859` | source | clamp pre-shift ETA: konsola TAK (30.06), apka fallback NIE → odbiór sprzed zmiany; twin-w-1-z-2 świeży | częśc. | TAK | P2 |
| **B22-J5** | B/D/J | `plan_recheck.py:1798` (panel-watcher env) | source | 2 writerzy kanonu różne inwarianty: tick MA SEQUENCE_LOCK+COMMITTED_PROP+LIVE_ETA_REFRESH, recanon NIE | nie | TAK | P2 |
| **B22-J6** | E/J | `ziomek_time_route_monitor.py:204` | source | monitor: konsola↔apka (nie↔kanon), start=None, trust_canon_ok=True hardkod → pomija invalidated; pod-certyfikuje | nie | TAK | P2 |
| **B22-J9** | D/J | `courier_api/config.py:60` / `flags.py:29` | source | 3 systemy flag bramkują 1 parytet, brak rejestru; TRUST_CANON ×2 flagi default niezależny | nie | TAK | P2 |
| **B22-J7** | A1/N | `fleet_state.py:88` / `route_podjazdy.py:30` | source | stałe ręcznie-mirror komentarzem (PICKUP_MERGE_MIN=10, _plan_pickup_clusters ×2) | nie | TAK | P3 |
| **B22-J8** | K | `courier_api_panelsync/courier_orders.py:558` | source | martwy 665L fork route-order/time (panel_sync.py nie importuje); inflacja copy-count + martwe floor-twiny | nie | TAK | P3 |
| **B22-J10** | F/J | `ziomek_time_route_monitor.py:250` (q2) | symptom | time-drift q2 11-29/d nieatrybuowany (legit vs illegit) + brak parytetu ETA-wartości per-stop | nie | TAK | P3 |

\* J2 objaw zamknięty (route-ORDER mismatch=0), ale STRUKTURA (J1) otwarta — parytet trzyma flaga, nie konstrukcja.

**DEDUP (do rootów A6/rollup):**
- J1+J2+J5+J7+J8 → **R2 „one route-order module"** (A6 grupa 2, K1+K7). J5 też rodzina **K2** (plan_recheck/recanon cofacz). J8 też **K** (martwy kod).
- J3+J4+J10 → **R5/R4-time** = nowy distinct sub-root „one time/ETA renderer + floor" (A6 grupa 6 floor #10/#11 apka bez floor + grupa 7 display≠decision). J4 = wzorzec #11 (twin-1-z-2) + grupa-6 floor.
- J6 → instrument-fidelity (klasa E, C9/C11) — NIE licz jako kopię-reguły; to „przyrząd pod-certyfikuje parytet, któremu się ufa".
- J9 → **D** (3 systemy flag) cross-ref A3/A5 §C.7 — NIE osobny chaos, ten sam „brak jednego rejestru".

---

## POKRYCIE

**coverage_declared (zbadane świeżym grepem/odczytem DZIŚ):**
- Silnik: `plan_recheck.py` (canon writer×2: `_apply_canon_order_invariants:1478`, `_relax_carried_first:1003`, `_retime_one_bag_plan:1560`, `_gen_one_bag_plan:612`, `recanon_courier:1798`, `redecide_courier:1736`, `run_recheck:2017`); `route_podjazdy.py` (cała, 232L: `order_podjazdy:190`, `_canon_order_from_plan:141`, `pickup_runs:87`, `plan_drop_rank:125`, `_plan_pickup_clusters:57`); `chain_eta.compute_chain_eta:45`.
- Konsola: `fleet_state.py` (`_build_route:395`, `_eta_chain:250`, `_order_from_plan_seq:342`, `_resolve_invalidated_plan:370`, `_plan_pickup_clusters:277`, `_pickup_runs:305`) — potwierdzone 0 importów dispatch_v2.
- Apka: `courier_orders.py` (`build_view:1072`, `_prioritize_carried_dropoffs:467`, `_plan_stop_sequence:672`, `optimize_route:265`, `_compute_live_eta:794`, `_attach_fallback_eta:822` ciało, `_committed_pickup_eta:641`, import `route_podjazdy:38`); `config.py` flagi route/time.
- panelsync: `courier_orders.py` (665L, build_view:558/_plan_stop_sequence:366/optimize_route:188) + `panel_sync.py` importy (potwierdzona martwota).
- Monitor: `ziomek_time_route_monitor.py` CAŁY + `ziomek_time_route_monitor.jsonl` (1613 rek., per-dzień policzone).
- Efektywny env: `courier-api`, `nadajesz-panel`, `ziomek-time-route-monitor`, `dispatch-plan-recheck`, `dispatch-panel-watcher` (systemctl show); `flags.systemd.env`, panel `DEFAULT_FLAGS`.

**coverage_gaps (jawne, nie cisza):**
1. **courier-app Kotlin** (`RouteLogic.kt buildSteps/pickupTogether`) — NIEczytany (granica; route renderowany serwerowo przez courier_api, ale lokalny re-sort/ETA w Kotlin niezweryfikowany — A6 luka #1). Faza B/J: czy Kotlin re-liczy kolejność/ETA lokalnie.
2. **Magnituda rozjazdu ETA LICZBOWO** — różnice dwell/clamp wywiedzione z KODU, NIE zmierzone runtime-replayem 3 rendererów na realnym worku (to Faza C oracle: uruchom `_eta_chain` vs `_attach_fallback_eta` vs `chain_eta` na tym samym bag+plan, porównaj per-stop). Read-only nie odpala.
3. **Materialność J5** (czy panel-watcher recanon realnie produkuje INNY kanon niż tick przez brak 3 flag) — env CONFIRMED, behawior PLAUSIBLE; wymaga trace osiągalności `_retime_one_bag_plan` (Faza B) + oracle (Faza C).
4. **Pełne ciała** `_build_route:395-655` / `_plan_stop_sequence` / `_compute_live_eta` — charakteryzowane z nagłówków+kluczowych slice'ów (`_eta_chain`, `_attach_fallback_eta` przeczytane w całości), nie linia-po-linii.
5. **parcel lane** (`parcel_lane_merge`/`parcel_assign`) route-order/time path — NIEprześwietlony (A6 luka #2); parcel ma natywny tor orders_state (klucz 900M+id).
6. **q2-drift atrybucja** — NIE rozdzielony legit (force-recheck/rutcom/closest-day) vs illegit per-event (wymaga join z `coordinator_time_recheck`/`v319g_ck_change_count` — Faza C).
