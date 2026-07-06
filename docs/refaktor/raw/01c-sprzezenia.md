# RAW 01c — raport agenta f1-sprzezenia (efekty uboczne i sprzężenia)

> Surowy raport subagenta read-only (Faza 1). Zweryfikowany na HEAD `fcf1342`. Synteza → `../01-stan-obecny.md`.

# RAPORT — EFEKTY UBOCZNE I SPRZĘŻENIA (Faza 1, weryfikacja na HEAD)

**HEAD:** `fcf1342` (master, origin/master, 2026-07-05 23:31 UTC) · **Metoda:** READ-ONLY (Read/Grep/Bash-wc/git), zero zapisu, zero pytest. **Repo mutuje na żywo** (auto-push co godzinę + brudne drzewo runtime) → każda `plik:linia` = snapshot HEAD; weryfikowałem symbolem, nie ufałem liniom z docs. **Baza:** `docs/audyt/01`, `01a`, `03`, `CODEMAP.md` — poniżej WERYFIKACJA + korekty na HEAD.

**Werdykt ogólny:** docs 03.07 kierunkowo trafne, ale **kilka tez o bliźniakach już nieaktualnych** (lex_qual, _bucket, serializer — zunifikowane), a **jedna teza o flagach wprost odwrócona przez flags.json** (SLA-anchor). Rozmiary dryfnęły w górę.

---

## 1. ROZMIARY I KONCENTRACJA

### 1a. wc -l — 15 największych modułów silnika (HEAD) vs docs

| Moduł | HEAD (l.) | docs (03.07) | Dryf | Rola |
|---|---|---|---|---|
| `dispatch_pipeline.py` | **7414** | 7247 | +167 | selekcja+werdykt+scoring glue; największy plik repo |
| `telegram_approver.py` | 4348 | 4348 | 0 | approver (⚠ `dispatch-telegram` OFF od 26.06) |
| `common.py` | **4111** | 3985 | +126 | config+flagi+logger+geo+biznes (god-object) |
| `panel_watcher.py` | 2720 | 2720 | 0 | ingest gastro + 4 handlery recanon |
| `plan_recheck.py` | **2553** | 2501 | +52 | re-canon kolejności (timer 5min) |
| `shadow_dispatcher.py` | 1829 | — | — | SILNIK: `_tick`/`run` + serializer A+B |
| `courier_resolver.py` | 1753 | — | — | snapshot floty + no-GPS last-pos |
| `route_simulator_v2.py` | 1675 | — | — | PDP-TSP OR-Tools + DWELL |
| `feasibility_v2.py` | 1396 | — | — | check_feasibility_v2 (HARD) |
| `state_machine.py` | 1284 | — | — | upsert orders_state (26 ścieżek) |
| `osrm_client.py` | 842 | — | — | OSRM stdlib urllib |
| `panel_client.py` | 819 | — | — | gastro login/CSRF |
| `geocoding.py` | 740 | — | — | Nominatim + cache |
| `tsp_solver.py` | 525 | — | — | jedyny import ortools |
| `scoring.py` | **288** | — | — | ⚠ tylko 288 l. — realny scoring rozlany po `dispatch_pipeline`+`common.py` |

**FAKT:** monolity rosną między auto-pushami (dryf +167/+126/+52 na 3 dniach). `scoring.py` zaskakująco mały — ~19 kar SOFT siedzi w `dispatch_pipeline` i `common.py`, nie tu → nazwa myli.

### 1b. `common.py` — god-object POTWIERDZONY (szerszy niż „flagi+bbox")

**FAKT:** 40 def/class + **492 stałych UPPERCASE**. Miesza 6 trosk:
- config/flagi/logger: `load_config:43`, `flag:80`, `setup_logger:710`;
- czas/TZ: `now_utc/iso:674-678`, `parse_panel_timestamp:772`;
- geometria HARD: `coords_in_bialystok_bbox:844`, `drop_zone_from_address:1882`, `drop_proximity_factor:1995`;
- ruch/prędkość: `get_traffic_multiplier(_v2):980/1055`, `dwell_for_tier:2516`, `speed_mult_for_tier:2551`;
- **scoring biznesowy:** `bug4_soft_penalty:1699`, `bug2_wave_continuation_bonus:2114`, `post_shift_overrun_penalty:2215`, `bundle_score_multiplier:3624`, `extension_penalty:3753`, `carry_chain_penalty:4025`, `carry_chain_hard_reject:4079`;
- **rejestr flag decyzyjnych:** `ETAP4_DECISION_FLAGS` = tuple na **liniach 95–391**.

To nie „hub stałych" — plik jednocześnie ładuje config, konfiguruje logger, liczy strefy geo, mnożniki ruchu ORAZ trzyma reguły kar biznesowych. Zmiana dowolnej trosk dotyka pliku o in-deg ~85.

### 1c. Fan-in — graf 01a ZAWYŻONY testami

Spot-check (grep importów, **bez** `tests/`, `.bak`, `eod_drafts/`):

| Moduł | in-deg wg 01a | prod-only (grep) | Uwaga |
|---|---|---|---|
| `common` | 85 | **88** | zgodne — realny top-hub |
| `courier_resolver` | 13 | **17** | zgodne |
| `dispatch_pipeline` | 48 | **13** | ⚠ graf zawyżony testami |
| `route_simulator_v2` | 51 | **12** | ⚠ graf zawyżony (~39 to test/draft) |
| `feasibility_v2` | 21 | **2** | ⚠⚠ realnie 2 prod-importery — reszta testy |

**KOREKTA:** in-deg z `01a` liczy WSZYSTKIE 815 `.py` z testami (154× pytest). Dla sprzężenia produkcyjnego zawodne: `feasibility_v2` (rdzeń HARD) ma ~2 prod-importery, wygląda na hub 21. **HIPOTEZA:** mój grep może pomijać leniwe importy w ciele funkcji, więc realny prod-fan-in feasibility jest ≥2, ale nie 21. **Nie używaj in-deg z 01a jako miary sprzężenia produkcyjnego.**

---

## 2. LOGIKA ⨯ I/O

### 2a. Zliczenie I/O w modułach decyzyjnych (HEAD)

| Moduł | `open(` | `json.load` | subprocess | Werdykt |
|---|---|---|---|---|
| `dispatch_pipeline.py` | 13 | 7 | 1 | splecione |
| `courier_resolver.py` | 13 | 11 | 0 | najwięcej json.load w hot-path floty |
| `plan_recheck.py` | 6 | 4 | 0 | re-canon czyta orders_state sam |
| `shadow_dispatcher.py` | 4 | 2 | 0 | silnik |
| `state_machine.py` | 3 | 2 | 0 | źródło stanu |
| `feasibility_v2.py` | 2 | 0 | 0 | HARD-check pisze shadow log sam |
| `scoring.py` / `route_simulator_v2.py` / `chain_eta.py` | 0 | 0 | 0 | ✅ PURE |

**Pozytyw:** `scoring.py`, `route_simulator_v2.py`, `chain_eta.py`, `tsp_solver.py` = zero I/O.

### 2b. Reprezentatywne miejsca logika⨯I/O (plik:linia, HEAD)

- `dispatch_pipeline.py:878` — ścieżka `assess_order` otwiera **abs. ścieżkę na sztywno** `open("/root/.openclaw/workspace/dispatch_state/restaurant_coords.json")`, `:894` `restaurant_district_overrides.json` — decyzja o strefie ładuje plik inline.
- `dispatch_pipeline.py:1628-1629` — inline `open(_SPEED_DATA_PATH); json.load` w liczeniu prędkości.
- `dispatch_pipeline.py:2226-2227` + `:2272-2273` — **fleet load governor** czyta stan z dysku w logice decyzji (komentarz `:2218`: „świeży proces w pamięci nie wystarcza" — świadome, ale stan-przez-plik w hot-path).
- `dispatch_pipeline.py:2952-2953` — `RESTAURANT_META_PATH` json.load inline.
- `feasibility_v2.py:368` — **HARD-check** woła `_emit_r6_breach_shadow` (def `:327`), surowe `open(R6_BREACH_SHADOW_LOG_PATH,"a")`; `:409` analog. `C2_SHADOW_LOG_PATH`.
- `plan_recheck.py:148-149,204-205,2100,2159` — re-canon **wielokrotnie re-czyta orders_state z dysku** zamiast dostać wstrzyknięty.
- `courier_resolver.py:402/419/441/450/468/498/513/525` — 8× `open+json.load` (tiers/piny/ids/names/grafik/PWA/legacy) w budowie snapshotu floty.

### 2c. Atomic write vs zapis wprost

**FAKT — atomic (mkstemp+fsync+os.replace) STOSUJĄ:** `plan_manager.py` (replace×7,fsync×3), `state_machine.py` (mkstemp×2,fsync×4), `pending_proposals_store.py`, `geocoding.py`, `live_eta_cache.py`, `courier_resolver.py`, `global_alloc_store.py`. Kanon stanu (plany/orders/proposals) = atomiczny ✅.

**Zapis wprost (`open('a')` bez temp+rename):** wszystkie shadow-jsonl — `feasibility_v2.py:368,409`, `dispatch_pipeline.py:223,245,1249,2776`, `plan_recheck.py:219,2067`, `courier_resolver.py:293`. Append-only (mniej krytyczne), ALE **omijają `core/jsonl_appender.py`** (in-deg 9) — moduły HARD/selekcji piszą własnym `open('a')` zamiast wspólnego appendera.

---

## 3. „WIEDZĄ O SOBIE ZA DUŻO"

### 3a. Cykle importów — POTWIERDZONE, wszystkie leniwe

| Cykl | A→B | B→A | Mechanizm |
|---|---|---|---|
| `auto_assign_gate` ↔ `dispatch_pipeline` | `dispatch_pipeline.py:3123` import gate (w funkcji) | `auto_assign_gate.py:60` import pipeline (w funkcji) | **oba function-level** — cykl rozbrojony leniwie (landmine #47/#48) |
| `panel_client` ↔ `panel_html_parser` | `panel_client.py:438,447` import v2 (leniwie) | `panel_html_parser.py:229` import `parse_v1` z panel_client | wzajemny fallback v1↔v2, oba leniwe |
| `sms.ovh↔provider↔stub` | 3-węzłowy SCC | — | poza hot-path |

**FAKT:** żaden cykl nie jest import-time (brak deadlocku), ale każdy zależy od dyscypliny „import w funkcji". Przeniesienie na top-level = crash startu.

### 3b. Sięganie przez warstwy — BIDIREKCYJNE cross-repo

**Konsola → silnik (import biblioteki):**
- `nadajesz_clone/panel/backend/app/integrations/ziomek/committed_time.py:27` — `from dispatch_v2.common import (...)` (bezpośredni import wnętrza).
- `.../ziomek/route.py:85-86` — `sys.path.insert(0,'.../scripts'); from dispatch_v2 import plan_manager` **jako string do subprocess** (odpala silnik podprocesem z manipulacją sys.path).
- 10 plików w `integrations/ziomek/`.

**Silnik → konsola (odwrotny kierunek — MNIEJ oczywisty trop):**
- `sync_courier_pay.py:24` — silnik czyta `nadajesz_clone/panel/backend/.env` + read-only SELECT z bazy `nadajesz_panel`.
- `cod_weekly/panel_cod_ingest.py:33` — czyta panelowy `.env`.
- `tools/route_order_golden_corpus_gen.py:34` + `route_order_live_parity_check.py:41` — **odpalają panelowy `.venv/bin/python`** żeby uruchomić `fleet_state` konsoli.

**FAKT:** sprzężenie DWUKIERUNKOWE przez filesystem+import, nie API — źródło dryfu bliźniaków.

### 3c. INV-SRC-ROUTE-ORDER — logika kolejności w 3 repach / ~4 miejscach

| # | Repo/plik | Symbol |
|---|---|---|
| 1 (KANON) | `dispatch_v2/plan_recheck.py:1739` | `_apply_canon_order_invariants` |
| 2 | `dispatch_v2/route_podjazdy.py` (10 KB) | podjazdy carried-first relax (import przez apkę) |
| 3 | `courier_api/courier_orders.py:1096` `build_view` + `:1211` `_prioritize_carried_dropoffs` | kolejność w apce |
| 4 | `nadajesz_clone/panel/.../ziomek/fleet_state.py` `_build_route` | kolejność w konsoli |

**FAKT:** każda powierzchnia ma własną flagę „ufaj kanonowi": silnik `ENABLE_CARRIED_FIRST_RELAX`, apka `BUILD_VIEW_TRUST_CANON_ORDER` (`courier_api/config.py:62`), konsola `PANEL_FLAG_TRUST_CANON_ORDER`. ON→kanon; OFF→**każde robi własny carried-first reorder = ryzyko rozjazdu**. Pilnuje golden-test L6.A. „5. martwa kopia" (courier_api_panelsync) z kanonu §4 — **potwierdzam usuniętą** (brak na HEAD).

---

## 4. DUPLIKATY LOGIKI (bliźniaki) — status na HEAD

⚠ **Kilka tez z `03-DLUG §2b` / Przykazania #0 NIEAKTUALNYCH — unifikacje 06-24/25 weszły:**

| Bliźniak (z listy zadania) | Status HEAD | Dowód (plik:linia) |
|---|---|---|
| **lex_qual 3 kopie** (common/objm_lexr6/dispatch_pipeline) | 🟢 **ZUNIFIKOWANY** (teza STALE) | kanon `objm_lexr6.py:29 def lex_qual`; `dispatch_pipeline` **deleguje** `from dispatch_v2 import objm_lexr6 as _OL` ×6 (`:709,749,1168,1267,1344,1412`) — brak własnego `def`; `common.py` ma tylko **stałą kwantyzacji** (`:2993-3002`), nie funkcję |
| **inline `_bucket`** (_pln_pure_resort/_objm_lexr6_shadow/_best_effort_fastest_pickup_key) vs `_selection_bucket` | 🟢 **ZUNIFIKOWANY** | wszystkie wołają wspólny `_selection_bucket` (`dispatch_pipeline.py:2514`), 7× użyć; `objm_lexr6.bucket:83` przyjmuje `bucket_fn=` (wstrzyknięcie, nie kopia) |
| **serializer LOCATION A+B** (`_serialize_candidate`+`_serialize_result`) | 🟢 **WSPÓLNY helper** | oba w `shadow_dispatcher.py` (`:250`,`:478`) współdzielą `_propagate_prefixed_metrics` (`:235`, wołany `:474` kandydat + `:860` best); deny-lista po L1.1 |
| **SLA-anchor 3 bliźniaki** (`route_sim._count_sla_violations`/feasibility SLA-loop/`plan_recheck._o2_key`) | 🔴 **INLINE ŻYWE + PUŁAPKA FLAGI** | 3 inline: `route_simulator_v2.py:654`, `feasibility_v2.py:1184-1291`, `plan_recheck.py:754 _o2_key`. Unifikator `sla_anchor.py:29` za flagą. **⚠ `common.py:414 = False` + docstring „OFF=inline", ALE `flags.json:260 = true`** → ścieżka UNIFIED **LIVE**. Teza „default OFF" wprost odwrócona na HEAD |
| **feasibility ↔ greedy ↔ `plan_recheck._gen_one_bag_plan`** | 🟡 **OSOBNE, ŻYWE** | `plan_recheck.py:658 _gen_one_bag_plan` vs `route_simulator_v2.py:559 _simulate_sequence`+`:654` — dwa niezależne generatory planów |
| **best_effort ↔ objm_lexr6** | 🟡 częściowo zunif. | `dispatch_pipeline.py:679 _best_effort_objm_pick`+`:718 _..._shadow`; delegacja lex_qual do objm_lexr6 zamyka część dryfu |
| **4 handlery recanon** | 🟢 **JEDNO wejście, 4 spusty** | `panel_watcher.py` woła `plan_recheck.recanon_courier` 4×: `:654` assign, `:698` deliver, `:726` return, `:759` pickup → `plan_recheck.py:2137`; brak kopii logiki |

**Wniosek §4:** dług bliźniaków **zmalał** vs docs — 3/7 zunifikowane (lex_qual/bucket/serializer). Realnie żywe rozjazdy: **SLA-anchor** (z pułapką flagi), **plan-generatory** feasibility/plan_recheck, **route-order cross-repo** (§3c). **HIPOTEZA:** re-run `entropy_dashboard.py` pokazałby copy-count niższy niż 17 z 03.07.

---

## 5. FLAGI JAKO SPRZĘŻENIE (3 światy — ADR-004)

### 5a. Skala rejestru (policzone lekturą, bez uruchamiania)

- **`ETAP4_DECISION_FLAGS` = 106 flag** (tuple `common.py:95→391`, ~296 linii god-objectu = sam rejestr). ⚠ Podpowiedź zadania „42 vs ~24" **przestarzała** — rejestr urósł do 106.
- **`flags.json` = 229 kluczy.**
- **`flag_registry.py` (wg 03-DLUG, uruchomiony 03.07): 438 flag total · 127 decyzyjne · 221 flags.json · 12 env-frozen; 1 realny rozjazd (`USE_V2_PARSER`), 11 akceptowanych service-scoped.** (Nie uruchamiałem — cytuję z DŁUG.md; report liczy `len(rows)`/`sum(decision)`/`sum(env)` w `flag_registry.py:403-406`.)

**FAKT:** rozdźwięk skali — formalny rejestr (106 tuple ≈127 z numerics) << pełna populacja 438. Reszta = `os.environ.get` rozsiane po modułach (5b) = sprzężenie „ukryte".

### 5b. Env-frozen anty-wzorzec — WSZECHOBECNY

**FAKT:** dziesiątki `NAME = os.environ.get(...)` w **kolumnie 0** (poziom modułu = **zamrożone przy imporcie**, zmiana env bez efektu do restartu — lekcja #202 / C17):
- `common.py:40,1249,1294-1359` — cała rodzina geocode (`ENABLE_GEOCODE_CACHE_TTL/NEGATIVE_CACHE/BBOX_GUARD/VERIFICATION/DISTRICT_CHECK/CROSS_SOURCE/NOMINATIM_FALLBACK/FIRMOWE_REJECT`) ~12 flag zamrożonych env + `ENABLE_PENDING_POOL`.
- `plan_recheck.py:55,61,74,86,100,112,393` (`AUTO_INVALIDATE_STALE`, `ENABLE_GPS_DRIFT_INVALIDATION`, `ENABLE_PICKUP_REFLOOR`, `ENABLE_PLAN_FOR_ACTUAL_BAG`, `ENABLE_PLAN_REGEN_NEAR_PICKUP`, `..._COMMITTED_PROPAGATION`).
- `courier_resolver.py:318-320` (`TRACCAR_URL/USER/PASS`), `dispatch_pipeline.py:61,73`.
- **Cross-repo:** `courier_api/config.py:62 BUILD_VIEW_TRUST_CANON_ORDER = os.environ.get(...)` — apka też ma flagę route-order zamrożoną env.

**Ryzyko:** część nazw JEST też w `flags.json` (`ENABLE_PENDING_POOL`, flagi geocode) → **podwójne źródło**: jedno hot-reload (`C.flag`), drugie module-frozen. Wartość zależy od ścieżki odczytu = pułapka 3 światów.

### 5c. Konkretny dowód rozjazdu 3 światów (poza `USE_V2_PARSER`)

**`ENABLE_SLA_ANCHOR_UNIFIED`:** kod-default `common.py:414 = False`, docstring „OFF=inline bajt-w-bajt", ale `flags.json:260 = true`. Efektywnie **ON** (jak wcześniejsza mina `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE`, gdzie flags.json wygrywał). **Kto zakłada stan z komentarza/kod-defaultu — myli się.** Rozjazd między kod-defaultem a flags.json — rejestr go nie łapie (flags.json ma go „poprawnie").

---

## TOP-5 NAJCIĘŻSZYCH SPRZĘŻEŃ

1. **`common.py` (4111 l., in-deg 85) miesza config+logger+geometrię+mnożniki ruchu+kary biznesowe (carry-chain/bundle/extension)+106-elementowy rejestr flag** — każda zmiana reguły dotyka pliku, o którym „wie" cały silnik; scoring rozlany stąd, przez co `scoring.py` ma tylko 288 l.
2. **Route-order w 3 repach / 4 miejscach** (`plan_recheck._apply_canon_order_invariants` kanon + `route_podjazdy` + `courier_api.build_view` + konsola `fleet_state._build_route`), każde z własną flagą „trust-canon" (jedna env-frozen w apce) — rozjazd pilnowany tylko golden-testem, nie wspólnym kodem.
3. **Bidirekcyjne cross-repo:** konsola importuje `dispatch_v2.common`/`plan_manager` (wprost i przez subprocess z `sys.path.insert`), a silnik czyta `.env`/DB/`.venv` konsoli (`sync_courier_pay`, `panel_cod_ingest`, golden-tools) — 2 repa splecione przez filesystem, nie API.
4. **Pułapka 3 światów flag:** `ENABLE_SLA_ANCHOR_UNIFIED` kod=False / flags.json=true (unified LIVE mimo docstringu „OFF") + dziesiątki `os.environ.get` module-frozen dublujących klucze flags.json — stan flagi zależy od ścieżki odczytu i wymaga restartu, nie hot-reloadu.
5. **Cykle importów rozbrojone tylko leniwością** (`auto_assign_gate↔dispatch_pipeline`, `panel_client↔panel_html_parser`) — przeniesienie któregokolwiek importu na top-level = crash startu silnika; feasibility_v2 (rdzeń HARD) ma realnie ~2 prod-importery, więc graf in-deg=21 zwodniczo sugeruje większe sprzężenie.

---

## KOREKTY vs docs 03.07 (dla dalszej pracy)
- (a) lex_qual/`_bucket`/serializer **już zunifikowane** — nie licz jako żywych bliźniaków;
- (b) in-deg z `01a` zawyżony testami — dziel prod/test (feasibility_v2 realnie ~2, nie 21);
- (c) `ENABLE_SLA_ANCHOR_UNIFIED` czytaj z `flags.json:260`, nie z kod-defaultu — unified path jest LIVE;
- (d) `ETAP4_DECISION_FLAGS` = 106, nie 24/42.

## NIEZWERYFIKOWANE / HIPOTEZY (jawnie)
- **HIPOTEZA:** mój prod-fan-in grep może pomijać leniwe importy w ciele funkcji → realny fan-in feasibility_v2 ≥2, ale nie 21 (kierunek pewny, dokładna liczba nie).
- **NIEZWERYFIKOWANE:** liczniki `flag_registry.py` (438/127/221/12) — cytat z 03-DLUG, nie uruchamiałem narzędzia (zakaz).
- **HIPOTEZA:** re-run `entropy_dashboard.py` dałby copy-count <17 (bo lex_qual/bucket/serializer zunifikowane) — nie uruchamiałem.
- **NIEZWERYFIKOWANE:** czy `_gen_one_bag_plan` (plan_recheck) i `_simulate_sequence` (route_sim) dają IDENTYCZNE plany dla tego samego bagu — potwierdziłem tylko że to 2 osobne generatory, nie porównałem wyników.
