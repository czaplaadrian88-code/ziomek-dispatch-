# B21 — KLASA F DEEP: `eta_pickup_utc` / `eta_pickup_hhmm` + committed `czas_kuriera` + `target_pickup_at`

**Agent:** B21-F-eta-pickup-deep · **Lane B** · **Faza 1 audyt spójności, TRYB READ-ONLY** · sesja tmux 2 · 2026-06-30 ~14:0x UTC
**Cel:** pełna mapa WSZYSTKICH writerów + WSZYSTKICH konsumentów pola-rodziny „kiedy kurier odbierze". Gdzie *display* karmi *decyzję*. Floor/freeze flagi. To KOTWICA dla Fazy F — kontrakt `display ≠ decision-value` (DESIGN §4.6, protokół wzorzec #8, A6 GRUPA 7 / root R5).
**Metoda:** świeży `grep -rn` na żywym kodzie (linie DRYFUJĄ — re-grep przed użyciem jako pewnik) + Read kluczowych ciał + `systemctl`/flags.json dla stanu efektywnego. HEAD `8024705`, working tree silnika czysty.

---

## 0. ⚠ KOREKTA NAZWY POLA (od razu, by nie ścigać ducha)

Seed-zlecenie wymienia `effective_pickup_at`. **Takie pole NIE ISTNIEJE** — `grep -rn "effective_pickup_at"` w silniku + `nadajesz_clone/panel` + `courier_api` = **0 trafień**. Realna „effective pickup" (zmienna decyzyjna „absolutny moment odbioru") nazywa się **`target_pickup_at`** (F1.8, `shadow_dispatcher.py:535/548/629`). To ona karmi `time_arg` auto-assignu → committed `czas_kuriera`. Mapuję `effective_pickup_at` → `target_pickup_at` w całym dokumencie.

**RODZINA POLA (5 reprezentacji „kiedy odbiór"):**
| Pole | Typ | Rola | Źródło |
|---|---|---|---|
| `eta_pickup_utc` | ISO datetime | **decyzja** (extension_penalty, >60min hard-reject) ∧ źródło display | engine `dispatch_pipeline` metrics |
| `eta_pickup_hhmm` | HH:MM string | **display** (derived z `eta_pickup_utc`) | `shadow_dispatcher._eta_hhmm_warsaw` |
| `target_pickup_at` | ISO datetime | **decyzja** (`max(eta_pickup_utc, pickup_ready_at)`) → `time_arg` → committed `czas_kuriera` | `shadow_dispatcher` F1.8 |
| `czas_kuriera_warsaw` / `_hhmm` | ISO/HH:MM | **committed** (R27 frozen, nietykalny po przypisaniu) | panel → `panel_client`; też pisany przez console `shadow_quote` + auto-assign |
| `eta_pickup_min` | float min | **promesa restauracji** (`promised_pickup_at`) | console `adapter` (NIEZALEŻNE od engine) |

**Najważniejszy wniosek (Faza F):** to NIE „rozjazd kopii reguły" (A1) lecz **DRYF SEMANTYKI POLA (F)** — jedno `eta_pickup_utc` jest jednocześnie wartością-decyzyjną (karmi scoring + HARD-reject + committed) i bazą napisu. Naiwna „zmiana napisu" = regres selekcji+feasibility+promesy. Wokół tego NAROSŁA warstwa „display-floor/freeze" w ≥6 powierzchniach (każda z własną flagą, bez wspólnego importu), żeby *display* nie kłamał — ale *decision-value* zostaje surowy → **display floor ≠ decision-value**.

---

## 1. WRITERZY `eta_pickup_utc` (decyzja) — ŚWIEŻY grep

### 1a. Engine — kanoniczne źródło (`dispatch_pipeline._assess_order_impl`, def `:3350`)
| # | plik:linia (świeże) | Co pisze | `eta_source` | Gałąź / flaga |
|---|---|---|---|---|
| W1 | `dispatch_pipeline.py:4057` | `eta_pickup_utc = arrive_pickup_utc` = `plan.pickup_at[oid] − DWELL_PICKUP_MIN` | `"plan"` | gdy `plan` ma `pickup_at[oid]` |
| W2 | `dispatch_pipeline.py:4061` | `eta_pickup_utc = drive_arrival_utc` = `now + drive_min` (OSRM/haversine) | `"haversine"` | else (brak planu) |
| W3 | `dispatch_pipeline.py:4067` | `eta_pickup_utc = r07_chain_eta_utc` (override) | `"r07_chain_eta"` | `ENABLE_V326_R07_CHAIN_ETA` — **env-default „0" = OFF** (`common.py:3312`) → **GAŁĄŹ MARTWA LIVE** (klasa K, finding F-6) |
| W4 | `dispatch_pipeline.py:4077` | `eta_pickup_utc = now + travel_min` (`_sf_wait + drive_min`) | `"soon_free"` | `soon_free_applied` (SP-B2-ZARAZWOLNY) |
| W5 | `dispatch_pipeline.py:5287` | `enriched_metrics["eta_pickup_utc"] = eta_pickup_utc.isoformat()` | — | serializacja per-kandydat (do `c.metrics`) |
| W6 | `dispatch_pipeline.py:5862` | `c.metrics["eta_pickup_utc"] = no_gps_eta_utc` = `now + max(15, prep_remaining)` | `"no_gps_fallback"` | **POST-LOOP** `for c in candidates` (`:5856`), tylko `pos_source=="no_gps"` |
| W7 | `dispatch_pipeline.py:5877` | `c.metrics["eta_pickup_utc"] = shift_eta` = `now + shift_start_min` | `"pre_shift"` | **POST-LOOP** `:5856`, tylko `pos_source=="pre_shift"` (clamp do startu zmiany) |

⚠ **W6/W7 nadpisują `c.metrics["eta_pickup_utc"]` PO tym, jak W5 zaserializował per-kandydat i PO tym, jak konsument decyzyjny C1/C2 (extension_penalty + hard-reject) już go odczytał** — patrz finding **F-4** (ordering). W6/W7 = synthetic/sentinel-ish ETA (most do K5 / A6 GRUPA 3 pozycja-równość) — finding **F-10**.

### 1b. Cross-repo — RE-IMPLEMENTACJE (NIE importują engine — własny `eta_pickup_utc`)
| plik:linia | Repo | Co pisze | Uwaga |
|---|---|---|---|
| `shadow_quote.py:106` | konsola | `eta_pickup = now + drive_to_pickup` (tania ścieżka „najszybszy") | własny kalkulator, J |
| `shadow_quote.py:232` | konsola | `"eta_pickup_utc": _iso(pickup_eta)` | quote response |
| `shadow_quote.py:419/441/472/478` | konsola | `ep = plan.pickup_at[oid] or m.get("eta_pickup_utc")` → options | floor-to-plan po stronie konsoli |
| `parcel_dispatch_shadow.py:78` | konsola | `"eta_pickup_utc": q.get("eta_pickup_utc")` (z quote) | most paczki |
| `adapter.py:273` | konsola | `"eta_pickup_utc": now+8min` (stub fallback) | degrade |

---

## 2. KONSUMENCI DECYZYJNI (to czyni `eta_pickup` decision-value, NIE display)

| # | plik:linia (świeże) | Użycie DECYZYJNE | Źródło-pole | Flaga / stan efektywny |
|---|---|---|---|---|
| **C1** | `dispatch_pipeline.py:5173` | `v324a_extension_min = eta_pickup_utc − pickup_ready_at` → `extension_penalty()` → **kara scoringu** `v324a_extension_penalty` (gradient 0/−10/−50/−100/−200) dodana do `final_score` (`:5199`) | `eta_pickup_utc` (metrics) | `ENABLE_V324A_SCHEDULE_INTEGRATION` **env-default „1" = ON** (`common.py:1914`); **env-frozen, BRAK w flags.json/ETAP4/fingerprint** → klasa D (F-5) |
| **C2** | `dispatch_pipeline.py:5610` | `if v324a_extension_hard_reject and verdict=="MAYBE": verdict="NO"` — **HARD REJECT >60min** (`extension>V324_HARD_REJECT_EXTENSION_OVER_MIN=60` → `extension_penalty()` zwraca `None` `common.py:3378`) | `eta_pickup_utc` (przez C1) | j.w. — **HARD-reject żyje w warstwie VERDICT dispatch_pipeline, NIE w `feasibility_v2`** (F-5, klasa C „zła warstwa") |
| **C3** | `dispatch_pipeline.py:3189-3193` | `eta_pickup = plan.pickup_at[oid]`; `overrun = (eta_pickup − created) − PACZKA_PICKUP_SOFT_CAP_MIN(120)` → paczka soft/hard | **`plan.pickup_at` (NIE `eta_pickup_utc`!)** | INNA KOTWICA niż C1 — patrz F-1 (intra-engine anchor drift) |
| **C4** | `auto_assign_executor.py:191` | `time_arg = round((target_pickup_at − now)/60)` → `gastro_assign(time=…)` → **panel ustawia committed `czas_kuriera`** | `target_pickup_at` (= `max(eta_pickup_utc, ready)`) | **LATENTNE** — `ENABLE_AUTO_ASSIGN` OFF (flags.json); uzbroi się na autonomii (F-1, ścieżka eta→committed) |
| **C5** (cross-repo) | `shadow_quote.py:310-317` | `target_pickup = target_deliv − drive − buffer`; `order_event["czas_kuriera_warsaw"] = target_pickup`; **re-run `assess_order`** → wstrzykuje committed do silnika | własny back-solve (NIE engine `eta_pickup_utc`) | LIVE quote/parcel path; J — **2. writer committed `czas_kuriera`** (F-7) |
| **C6** (cross-repo) | `adapter.py:197/210` → `dispatch.py:102/245` | `eta_pickup_min = drive+2`; `delivery.promised_pickup_at = now + eta_pickup_min`; `delivery.pickup_eta = now + eta_pickup_min` | własny `eta_pickup_min` (3. reprezentacja) | DB restauracji; promesa NIEZALEŻNA od engine (F-8) |

**Konsument-target (cross-module):** `target_pickup_at` (`shadow_dispatcher.py:548` = `max(eta_dt, ready_dt)`) jest pisany RAZ przy propozycji i czytany przez C4 (auto-assign time) oraz `shadow_outcome_enricher.py:185` (telemetria). To „display-looking" pole (ISO w `best{}`), ale **`time_arg` z niego = realna deklaracja do panelu** → committed.

---

## 3. KONSUMENCI DISPLAY (czysty render — ale z FLOOR-em który ujawnia rozjazd)

| plik:linia | Powierzchnia | Co renderuje | Floor? |
|---|---|---|---|
| `shadow_dispatcher.py:291` (LOCATION A) / `:627` (LOCATION B) | serializer | `eta_pickup_hhmm = _eta_hhmm_warsaw(eta_pickup_utc)` | brak (czysty derive — display ZAWSZE śledzi decyzję, OK) |
| `telegram_approver.py:347` (header) | Telegram | `eta = c.get("eta_pickup_hhmm") or eta_drive_hhmm` | — |
| `telegram_approver.py:871-875` (`_candidate_line_v2`) | Telegram | `eta = max(eta_pickup_hhmm, committed_hhmm, plan_hhmm)` (leksykograficznie) | **FLOOR** (F-2/F-3) |
| `telegram_approver.py:1318/1437/1443` | Telegram | floor kandydatów do `ck_hhmm`/`plan.pickup_at` | **FLOOR** gated 2 flagami |
| `feed.py:189` | konsola (pula) | `eta_pickup_hhmm = raw.get(...)` **passthrough BEZ floora** | brak (F-9 — niespójność z route-view konsoli) |
| `parcel_overlay.py:66` | konsola (paczka) | `pick = _fmt_clock(eta_pickup_utc)` | brak |
| `courier_orders.py:753/872-893` | apka | `eta_pickup = pm.get("eta")`; w `_attach_fallback_eta` `st["eta"] = frozen_iso` (plan/committed, OSRM odrzucony) | **FREEZE** (`FROZEN_PICKUP_ETA`) |
| `fleet_state.py:507/853` | konsola (trasa) | pin committed / clamp pre_shift w `_build_route`/`_eta_chain` | **FREEZE/CLAMP** |

> ⚠ **Telegram = MUTED/dead** (`dispatch-telegram` inactive, świadome) → floor-flagi `ENABLE_PROPOSAL_ETA_FLOOR_TO_*` są LIVE w `flags.json` (`:219-220` =true) ale **dormantne** (nikt nie renderuje). Te same SEMANTYCZNIE floory żyją realnie w apce (`FROZEN_PICKUP_ETA`) i konsoli (`PIN_AGREED`/`CLAMP`). Re-enable Telegrama = uzbroi 3. kopię floora.

---

## 4. REJESTR FLAG FLOOR / FREEZE „display pickup ≥ plan/committed/ready" (≥6 powierzchni, ≥7 flag, BRAK wspólnego źródła)

| Flaga | Powierzchnia | plik:linia | Stan efektywny | Warstwa |
|---|---|---|---|---|
| `ENABLE_PROPOSAL_ETA_FLOOR_TO_PLAN` | Telegram | `telegram_approver.py:1443` | flags.json=true (dormant: TG muted) | DISPLAY |
| `ENABLE_PROPOSAL_ETA_FLOOR_TO_COMMITTED` | Telegram | `telegram_approver.py:1437` | flags.json=true (dormant) | DISPLAY |
| `ENABLE_FROZEN_PICKUP_ETA` (`config.FROZEN_PICKUP_ETA`) | apka | `courier_orders.py:872` | env-default „1" = **ON** | DISPLAY (render apki) |
| `ENABLE_PICKUP_READY_FLOOR` (`config.PICKUP_READY_FLOOR`) | apka | `courier_orders.py:877` | env-default „1" = **ON** | DISPLAY (wtórny floor do gotowości) |
| `ENABLE_FALLBACK_HONEST_OSRM_ETA` | apka | `courier_orders.py:883/895` | env-default „1" = ON | DISPLAY (rozjazd badge) |
| `PANEL_FLAG_PIN_AGREED_PICKUP_TIME` | konsola | `fleet_state.py:509` | drop-in (A5: `nadajesz-panel`) ON | DISPLAY (render trasy konsoli) |
| `CLAMP_PRESHIFT_PICKUP_ETA` | konsola | `fleet_state.py:755/853` | env ON 30.06 (MEMORY) | DISPLAY (clamp pre_shift) |
| `ENABLE_PRE_SHIFT_DEPARTURE_CLAMP` | **engine feasibility** | `feasibility_v2.py:~789` (A2) | flags.json=true | **DECYZJA** (clamp departure — JEDYNY floor w warstwie HARD) |
| `ENABLE_PICKUP_DEBIAS_SHADOW` | serializer | `shadow_dispatcher.py:562` | flags.json=true | SHADOW (target+4.5min, log-only, NIE zmienia decyzji) |

**To rodzina A1/J nałożona na F:** jedno pojęcie „nie pokazuj odbioru przed plan/committed/ready" liczone/floorowane w 6 powierzchniach × 7+ flag, **bez wspólnego importu** — parytet tylko przez `ziomek_time_route_monitor` (konsola↔apka) + golden-testy per powierzchnia. Cross-ref A6 GRUPA 6 (floor `pickup ≥ shift_start`, root R4 — 17 powierzchni) — TU węższy aspekt (floor display do plan/committed, nie do shift_start).

---

## 5. FINDINGS (file:linia świeże · źródło/objaw · łatane? · otwarte? · severity · dowód · dedup)

### F-1 [F · ROOT · P2 · OTWARTE] `eta_pickup_utc` = jedno pole, DWIE role (decyzja ∧ display), bez separacji
- **plik:** `dispatch_pipeline.py:5287` (write decyzja+display) → konsumenci C1 `:5173`, C2 `:5610`, C4 `auto_assign_executor.py:191`, C5 `shadow_quote.py:315`; display `shadow_dispatcher.py:291/627`.
- **źródło.** Wartość, która karmi karę scoringu (extension_penalty), HARD-reject (>60min) i — przez `target_pickup_at` — committed `czas_kuriera`, jest JEDNOCZEŚNIE bazą napisu `eta_pickup_hhmm`. Brak osobnego pola decyzyjnego i osobnego display.
- **dowód:** C1 czyta `eta_pickup_utc` do `extension_min` (`:5173`); C2 flipuje verdict `MAYBE→NO` (`:5610`); C4 `time_arg = target_pickup_at−now` → `gastro_assign(time=)` = committed. Protokół wzorzec #8 (ten dokładny przykład) + A6 GRUPA 7.
- **intra-engine anchor drift (sub):** C1 czyta `eta_pickup_utc` (metrics), C3 (paczka overrun `:3189`) czyta `plan.pickup_at[oid]` — **dwa konsumenci „czasu odbioru" na DWÓCH różnych polach** → ta sama decyzja-rodzina liczona od niespójnej kotwicy.
- **dedup:** R5-display-vs-decision-eta_pickup (DESIGN root R5). **Kontrakt Fazy F:** rozdziel `eta_pickup_decision` (surowy, decyzyjny) od `eta_pickup_display` (floored) — fix = NOWE pole obok (additive), nie podmiana.

### F-2 [F/C · P2 · OTWARTE] DISPLAY floored, DECISION-value surowy → napis ≠ to-co-zdecydowało
- **plik:** display floor `telegram_approver.py:872-875`, `courier_orders.py:872-893`, `fleet_state.py:507/853`; decyzja surowa `dispatch_pipeline.py:5173` (extension liczony z surowego `eta_pickup_utc`, NIE z floored).
- **objaw (symptom F-1).** Koordynator/restauracja/kurier widzą `eta` floorowany do `max(eta_pickup_hhmm, plan.pickup_at, committed, ready)`, ale silnik scoringował i hard-rejectował na SUROWYM `eta_pickup_utc` (drive-arrival / pre_shift=shift_start / no_gps=now+15). Display podniesiony, decyzja nie → rozjazd „co widzę" vs „co policzono".
- **dowód:** floor dodany świadomie bo surowy `eta_pickup_hhmm` „pokazywał odbiór za wcześnie" (`flags.json:218` komentarz: Patryk pre_shift 18:00 vs plan 18:07, #483301) — **załatano RENDER (display-floor), nie semantykę decision-value** (klasa C „patch na renderze" nałożona na F).
- **dedup:** R5-display-floor-overlay (⊂ F-1).

### F-3 [A1/J · P2 · OTWARTE] Floor/freeze „pickup ≥ plan/committed/ready" = 6 powierzchni × 7+ flag, brak wspólnego źródła
- **plik:** §4 tabela (telegram 2 flagi / apka 3 / konsola 2 / engine-feasibility 1 / shadow 1).
- **źródło.** Jedno pojęcie re-implementowane per powierzchnia z osobnymi flagami; parytet tylko przez monitor+golden, nie import. Każda powierzchnia może rozjechać (apka frozen do `plan_pickup_iso or committed`+ready; konsola pin+clamp; telegram max(hhmm,committed,plan); feasibility clamp departure).
- **dowód:** świeże grepy §4; A5 §C.8 (macierz kopii „Czas odbioru frozen" ×4 powierzchnie); A6 GRUPA 6 (root R4, 17 powierzchni floor).
- **dedup:** R4-floor / R5-floor-multiplicity. Cross-ref: węższy niż R4 (tu floor-do-plan/committed display, R4 = floor-do-shift_start cały cykl).

### F-4 [F/O ordering · P2 · PLAUSIBLE · OTWARTE] `eta_pickup_utc` pre_shift/no_gps nadpisany PO policzeniu extension_penalty/hard-reject
- **plik:** decyzja C1/C2 `dispatch_pipeline.py:5173/5610`; override W6/W7 `:5862/:5877` (oba w `_assess_order_impl` def `:3350`; override w POST-LOOP `for c in candidates :5856`, textowo PO `:5173`).
- **objaw.** Komentarz `:5163` twierdzi „Dla pre_shift kurier `eta_pickup_utc` = shift_start (clamp aktywny w post-loop override **L920+**)" — ale L920+ to **stała linia** (kod urósł do 7028 L; clamp jest realnie `:5877`, PO extension_penalty `:5173`). Jeśli per-kandydat extension liczy się z surowego drive-arrival, a `eta_pickup_utc` jest dopiero potem nadpisany na shift_start → **decyzja (extension/hard-reject) i serializowany/displayowany `eta_pickup_utc` to DWIE różne wartości dla pre_shift/no_gps**.
- **dowód:** awk — oba bloki w jednej funkcji `_assess_order_impl`; W6/W7 mutują `c.metrics[...]` w osobnej pętli `:5856` (4-space) po głównej pętli scoringu (8-space, `:5161-5199`). Stała „L920+" = dryf komentarza (smell).
- **status:** PLAUSIBLE — wymaga Fazy C trace (czy per-kandydat `eta_pickup_utc` dla pre_shift już = shift_start w `:5173`, czy surowy; możliwe że główny eval to inny przebieg). **NIE over-claim.**
- **dedup:** R5-eta-override-ordering (⊂ F-1).

### F-5 [C/L/D · P3 · OTWARTE] „>60min extension HARD-reject" w warstwie VERDICT (nie feasibility) + flaga env-frozen poza rejestrem
- **plik:** `dispatch_pipeline.py:5610` (`verdict MAYBE→NO`), gated `ENABLE_V324A_SCHEDULE_INTEGRATION` (`common.py:1914`, env-default „1" ON, **brak w flags.json/ETAP4/fingerprint**).
- **źródło.** HARD-reject (twarda bramka decyzji) żyje w warstwie scoring/verdict `dispatch_pipeline`, NIE w `check_feasibility_v2` (warstwa HARD) — klasa C „zła warstwa / HARD w SOFT-lokalizacji". Dodatkowo cała ścieżka decyzyjna eta→extension→reject zależy od flagi env-frozen niewidocznej w `flag_fingerprint` (A3 §7) → klasa D (parytet cross-proces niepilnowany; reset env = cichy flip).
- **dowód:** `:5608` komentarz sam pisze „delegowane do feasibility layer B5" ale egzekucja jest w `:5610` (dispatch_pipeline); protokół wzorzec #8 błędnie lokalizuje „HARD REJECT >60min (feasibility)" — realnie verdict-layer.
- **dedup:** extension-hard-reject-wrong-layer.

### F-6 [K · P3 · OTWARTE] Writer W3 (R-07 v2 CHAIN-ETA override) — gałąź MARTWA live
- **plik:** `dispatch_pipeline.py:4067` `eta_pickup_utc = r07_chain_eta_utc`, gated `ENABLE_V326_R07_CHAIN_ETA` env-default **„0" = OFF** (`common.py:3312`).
- **źródło.** `eta_source="r07_chain_eta"` nigdy nie powstaje na żywo (MEMORY: „R-07 CHAIN-ETA flip ANULOWANE — chain_eta pesymistyczny vs plan"). Kod-zombie myli czytającego że R-07 override żyje.
- **dowód:** flaga OFF + writer za nią; cała maszyneria `r07_chain_result`/`r07_chain_eta_utc` policzona ale override nieosiągalny.
- **dedup:** r07-chain-eta-dead.

### F-7 [J cross-repo · P2 · OTWARTE] Konsola `shadow_quote` = 2. writer committed `czas_kuriera` (back-solve z target-delivery, NIE engine eta)
- **plik:** `shadow_quote.py:310-317` (`target_pickup = target_deliv − drive − buffer`; `order_event["czas_kuriera_warsaw"] = target_pickup`; `assess_order(...)`).
- **źródło.** Konsola liczy moment odbioru BACK-SOLVE z żądanego czasu DOSTAWY (paczka/quote), wstrzykuje jako committed `czas_kuriera_warsaw` i re-uruchamia silnik → ten committed staje się R27 frozen-anchor (`route_simulator_v2.py:1070`). **Semantyka inna niż engine `eta_pickup_utc`** (forward: pozycja→odbiór), a oba lądują w tym samym polu committed → pole `czas_kuriera` ma 2 niezależnych writerów cross-repo (panel-derived + console-quote-derived).
- **dowód:** świeży Read `shadow_quote.py:298-319`; LIVE (parcel lane `ENABLE_PARCEL_LANE_LIVE`).
- **dedup:** console-quote-czas-kuriera-write. Cross-ref A5 §C.1 (konsola hybryda: quote→wspólny silnik).

### F-8 [J/A1 · P3 · OTWARTE] Restauracja: 3. reprezentacja `eta_pickup_min` → `promised_pickup_at` NIEZALEŻNA od engine
- **plik:** `adapter.py:187/197/210` (`eta_pickup_min = drive+2`) → `dispatch.py:102` `promised_pickup_at = now + eta_pickup_min`, `:245` `pickup_eta = now + eta_pickup_min`.
- **źródło.** Restauracyjna promesa odbioru liczona własnym adapterem konsoli (surowy OSRM dojazd + 2 min postoju), BEZ floora do gotowości/committed, NIEZALEŻNIE od engine `eta_pickup_utc`/`target_pickup_at` → restauracja widzi inną „kiedy odbiór" niż silnik zdecydował.
- **dowód:** świeży Read `adapter.py:183-215`; 10× `eta_pickup_min` w `panel/backend/app`.
- **dedup:** restaurant-eta_pickup_min-independent.

### F-9 [F · P3 · PLAUSIBLE · OTWARTE] Konsola: dwie ścieżki renderu z NIESPÓJNYM floorem
- **plik:** `feed.py:189` (pula/proposal — `eta_pickup_hhmm` passthrough BEZ floora) vs `fleet_state.py:507/853` (trasa — PIN_AGREED/CLAMP floor).
- **objaw.** Ten sam koordynator widzi w „puli propozycji" surowy `eta_pickup_hhmm` (dojazd / pre_shift=start zmiany), a w „trasie kuriera" floored committed/plan → wewnętrzna niespójność konsoli.
- **status:** PLAUSIBLE (czy feed renderuje ten sam moment co route — wymaga Fazy C porównania na żywym `ziomek_time_route_monitor`).
- **dedup:** console-feed-vs-route-floor (⊂ F-3).

### F-10 [M/F · P3 · OTWARTE] W6/W7 wpisują SYNTHETIC eta (no_gps=now+15, pre_shift=now+shift_min) → płyną do `target_pickup_at` → committed
- **plik:** `dispatch_pipeline.py:5854/5862` (no_gps `now+max(15,prep)`), `:5873/5877` (pre_shift `now+shift_min`) → `shadow_dispatcher.py:548` `target_pickup_at=max(eta,ready)` → C4 committed.
- **źródło.** Fikcyjna ETA (brak realnej pozycji) wchodzi jako wartość-decyzyjna do `target_pickup_at`, a stamtąd (na autonomii) do committed `czas_kuriera`. Most do K5 (sentinele) + A6 GRUPA 3 (pozycja-równość no_gps/pre_shift).
- **dowód:** świeży Read `:5845-5884`; `eta_source` znaczniki `no_gps_fallback`/`pre_shift`.
- **dedup:** no_gps-synthetic-eta-into-target (cross-ref R1 pozycja / K5).

---

## 6. TABELA POKRYCIA (jawnie — co zbadane, czego NIE)

| Obszar | Status | Dowód / luka |
|---|---|---|
| Engine writerzy `eta_pickup_utc` (7) | **ZBADANE** | grep + Read `dispatch_pipeline:4030-4080, 5150-5310, 5845-5890` |
| Engine konsumenci decyzyjni (extension/hard-reject/paczka/target) | **ZBADANE** | Read `:5160-5200, :5600-5612, :3189-3193`; `auto_assign_executor:188-200`; `common.extension_penalty:3338` |
| `target_pickup_at` (F1.8) writer + konsument | **ZBADANE** | `shadow_dispatcher:531-552`; `auto_assign_executor:191` |
| `eta_pickup_hhmm` display + floor flagi telegram | **ZBADANE** | `telegram_approver:343-361, 855-875, 1420-1451`; flags.json |
| Apka `courier_orders` frozen pickup | **ZBADANE** | Read `:718-759, :860-899`; `config.py:101-140` |
| Konsola `shadow_quote` committed write | **ZBADANE** | Read `:298-319` |
| Konsola `adapter`/`dispatch` promised_pickup_at | **ZBADANE** | Read `adapter:183-215`; grep `dispatch:102/245` |
| Stan efektywny flag (V324A/R07/floor) | **ZBADANE** | grep `common.py:1914/3312`; flags.json; courier_api config |
| `effective_pickup_at` (seed) | **ROZSTRZYGNIĘTE** | nie istnieje → `target_pickup_at` |
| **Console `fleet_state._eta_chain`/`_build_route` PEŁNE ciała pin/clamp** | **LUKA** | czytałem nagłówki+linie flag (`:250-260, 507-509, 755, 853`), NIE pełne ciało — magnituda floora = Faza B/C (A5 luka też to notuje) |
| **courier-app Kotlin lokalny re-sort/ETA odbioru** | **LUKA** | apka API-driven (serwer `courier_orders`), ale Kotlin `RouteLogic` może lokalnie formatować — NIE czytany (zgodne z A6 luka #1) |
| **Ordering F-4 (runtime)** | **LUKA (PLAUSIBLE)** | nie odpaliłem (read-only) — czy pre_shift extension liczy z surowego czy clamped eta = Faza C oracle/trace |
| **`time_arg` z konsoli przy akceptacji „ducha" (Ops13Console:661 z protokołu)** | **CZĘŚCIOWA LUKA** | protokół wskazuje frontend `Ops13Console:661` (TSX, poza zakresem .py); backendowy odpowiednik = C4 (auto_assign) + C5 (shadow_quote); pełny przepływ akceptacji UI→time→czas_kuriera = Faza B/J (cross-repo frontend) |
| **`czas_kuriera` rodzina (862 trafień)** | **CZĘŚCIOWA** | zbadane writerzy committed istotne dla eta (panel_client:682, shadow_quote:315, auto_assign time); pełna mapa 862× czas_kuriera = osobny agent (poza F-eta-pickup deep) |

---

## 7. HANDOFF dla Fazy F (kontrakt docelowy display≠decision)

1. **ROOT = R5 (DESIGN §4.6 / A6 GRUPA 7):** `eta_pickup_utc` rozdzielić na `eta_pickup_decision` (surowy, jedyne źródło dla extension_penalty + >60min reject + `target_pickup_at`) i `eta_pickup_display` (floored, do renderu). Fix = NOWE pole obok (additive), NIE podmiana (wzorzec #8). To rozbraja F-1/F-2/F-4 naraz.
2. **Floor/freeze = JEDNO źródło (rozbraja F-3):** wspólny helper „display pickup floor" importowany przez telegram/apkę/konsolę zamiast 7 flag w 6 powierzchniach; albo twardy golden-fixture parytet (jak `b-route route-flag-parity.conf`). Musi objąć WSZYSTKIE 6 powierzchni — inaczej kopia wraca.
3. **Anchor unify (F-1 sub + F-5):** C1 (`eta_pickup_utc`) i C3 (`plan.pickup_at`) czytają różne pola — ujednolicić kotwicę „czas odbioru decyzyjny". HARD-reject >60min przenieść do `check_feasibility_v2` (warstwa HARD) zamiast verdict-layer, ALBO świadomie udokumentować jako verdict-gate; `ENABLE_V324A_SCHEDULE_INTEGRATION` → ETAP4+fingerprint.
4. **Cross-repo committed writerzy (F-7/F-8):** `czas_kuriera` ma ≥3 writerów różnej semantyki (panel forward / console back-solve / restaurant adapter) — Faza D precedencja: który wygrywa gdy się rozjadą; czy `promised_pickup_at` restauracji == engine `target_pickup_at`.
5. **Sprzątanie (F-6):** writer W3 (R-07 chain-eta) za OFF-flagą = kandydat K (martwy override) — albo wskrzesić świadomie, albo usunąć.
6. **Faza C oracle (F-4):** trace na żywej próbce pre_shift/no_gps — czy extension_penalty/hard-reject użył surowego czy clamped `eta_pickup_utc`; porównaj serialized `eta_pickup_utc` vs `v324a_extension_min` w `shadow_decisions.jsonl`.

**NIE liczyć F jako kopii-reguły (A1):** to inny anty-wzorzec (jedno pole, dwie role). F-3 (floor-multiplicity) JEST A1/J i zwija się do R4/R5 — NIE double-count z A6 GRUPA 6.
