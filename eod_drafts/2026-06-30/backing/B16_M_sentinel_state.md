# B16 — KLASA M (sentinele / cicha awaria) w STATE + GEOCODE + SERIAL + CROSS

**Agent:** B16-M-sentinel-STATE · **Lane:** B · **Tryb:** READ-ONLY (zero edycji/restartów/flipów) · **Data:** 2026-06-30 ~14:0x UTC · **HEAD recon:** `8024705`
**Zakres zlecenia:** sentinele w warstwach STATE (state_machine/panel_watcher/panel_client/courier_resolver/gps), GEOCODE (geocoding/osrm/geometry/centroid-guard), SERIAL (shadow_dispatcher/plan persistence), CROSS (konsola feed/fleet_state). Cele jawne: `BIALYSTOK_CENTER` fiction, geocode-miss→(0,0), `FIRMOWE_FALLBACK_COORDS`, **fail-open vs fail-closed na TYCH SAMYCH danych**, sentinel w ingest, centroid-coloc guard.
**Metoda:** świeży `grep -rn`/`sed` per moduł (linie zweryfikowane DZIŚ — dryfują) + live-evidence z `dispatch_state/*.json` (read-only) + rekonstrukcja z A4/A5/A6 + preshift-floor-audit. Każda instancja: plik:linia + źródło/objaw + łatane? + otwarte? + severity + dowód + dedup_hint→root.
**Ważne sprostowanie linii seedowych:** seed mówił „is_on_shift fail-open 24/7 **schedule_utils:391**" — `schedule_utils.py` **NIE leży w dispatch_v2**, jest top-level `scripts/schedule_utils.py` (import przez `sys.path.insert` hack w `courier_resolver:1396-1398`). Świeże linie fail-open: **376/383/392/401**. Plik istnieje i działa.

---

## 0. TL;DR — 5 SUB-ROOTÓW KLASY M (most K5 do twin-grafu)

A6 nazwał sentinele „K5 most" zasilający position-twiny (gr.3) i floor (gr.6). Dezagreguję K5 na **5 distinct sub-rootów**:

| Sub-root | Istota | Instancje | Najgorsza |
|---|---|---|---|
| **K5a — (0,0) BEZ walidacji u INGEST** | null-island wpada w STATE bo bramki wejścia są zakresowe/truthy, nie bbox; broniony N× post-hoc | M1 gps_server · M2 state_machine · M7 N-łatek · M8 V328-eject | **P1** (busy courier wyrzucony z puli, ~302 V328/d) |
| **K5b — (0,0) placeholder PERSYSTOWANY** | silnik SAM pisze (0,0) jako placeholder do `courier_plans.json` | M3 `_save_plan_on_assign` | **P2** (LIVE: 11/79 stopów = (0,0)) |
| **K4/M — schedule fail-OPEN cichy ≠ fail-CLOSE głośny** | TE SAME zepsute dane grafiku raz przepuszczają 24/7 (cicho), raz odrzucają (głośno) | M4 is_on_shift · M5 dt-helpers ≠ is_on_shift | **P1** (literówka „11.00" = brak floor + brak post-shift, na zawsze, cicho) |
| **N/M — rozsyp DEFINICJI sentinela** | 6 niespójnych „czy ten coord jest poprawny" | M6 (6 wariantów) | **P2** (latentne, dziś benign) |
| **M — cicha awaria w renderze/markerze** | fail-soft→{} / replay-label / firmowe-mask gubią sygnał awarii | M9 feed · M11 pos-replay · M12 firmowe · M10 BIALYSTOK-dup | **P2** |

**Najmocniejszy wniosek:** istnieje JEDEN poprawny walidator współrzędnych — `common.coords_in_bialystok_bbox` (`common.py:513`, odrzuca None/NaN/(0,0)/poza-bbox) — ale **NIE jest wpięty na żadnej granicy INGEST** (gps_server, state_machine). Defensa (0,0) to N łatek post-factum (`_sanitize_courier_pos`, `BAG_COORD_REPAIR`, sanitize effective_start_pos, centroid-guard), z których każda chroni jedno call-site. To kanoniczny K5a „brak jednego źródła walidacji".

---

## 1. INWENTARZ SENTINELI (3 fikcje + ich definicje)

### 1a. `BIALYSTOK_CENTER = (53.1325, 23.1688)` — fikcja pozycji kuriera (no_gps/pre_shift)
Definiowany **4× jako osobna stała** (A1/N): `courier_resolver.py:110`, `chain_eta.py:28`, `dispatch_pipeline.py:132` (`_BIALYSTOK_CENTER_FALLBACK`), `bootstrap_restaurants.py:19`. Przypisywany jako `cs.pos` w **6 miejscach** courier_resolver: `:1090` (no_gps fallback), `:1464` (post_shift_5min synthetic), `:1473` (working_override synthetic), `:1527` (working_override pre_shift), `:1567`/`:1579` (pre_shift v324a/legacy). Intencjonalne (polityka równości no_gps — Adrian 29.06).
⚠ **Kolizja semantyczna (M):** ta SAMA wartość `(53.1325,23.1688)` jest JEDNOCZEŚNIE (a) legalną fikcją pozycji którą świadomie nadajemy ORAZ (b) **trucizną na liście `BUNDLE_COLOC_DEFAULT_CENTROIDS`** (`common.py:2296`) którą centroid-guard FILTRUJE. Realna restauracja/dostawa w centrum miasta (53.1325,23.1688) byłaby fałszywie potraktowana jako „defaultowy centroid" i wykluczona z coloc-bonusu.

### 1b. `(0,0)` null-island — geocode-miss / stale-GPS / missing-init
`geocoding.geocode` zwraca **None** na miss (NIE (0,0)) — czysto (`geocoding.py:404/430/532`). Źródła (0,0): (1) **gps_server ingest** akceptuje (M1), (2) **placeholder** w `_save_plan_on_assign` (M3), (3) zlecenia z zatrutym `delivery_coords=[0,0]` z paneli/innych ścieżek. `[0,0]` jest **truthy** (niepusta lista) → `if coords:` go NIE łapie.

### 1c. `FIRMOWE_KONTO_FALLBACK_COORDS = (53.13222, 23.16844)` — fikcja pickupu firmowego
`common.py:3509`. Podstawiany gdy parser uwag firmowych zwróci None LUB geocode fail: `panel_watcher.py:1235`/`:1273`, `czasowka_scheduler.py:288`. Także na `BUNDLE_COLOC_DEFAULT_CENTROIDS`. **M (mask):** realny-wyglądający coord MASKUJE awarię geokodu — feasibility liczy pool dla zlecenia którego adres realnie NIEZNANY.

### 1d. ⚠ 6 NIESPÓJNYCH DEFINICJI „czy coord = sentinel/poprawny" (M6, N-class)
| # | Funkcja:linia | Predykat | Surowość |
|---|---|---|---|
| 1 | `osrm_client.haversine:409` / `geometry.haversine_km:19` | `ll == (0.0, 0.0)` (DOKŁADNA krotka, oba 0) | tylko null-island |
| 2 | `dispatch_pipeline._sanitize_courier_pos:236` | `float(pos[0])==0.0 AND float(pos[1])==0.0` | tylko null-island |
| 3 | `dispatch_pipeline.compute_bundle_deliv_coloc:2986/3004` | `tuple==（0,0) OR coord[0]==0.0` (**sama LAT=0**) | szersze (lat-alone) |
| 4 | `common.coords_in_bialystok_bbox:513` | None/NaN/(0,0)/poza-bbox metropolii | **KOMPLETNE** |
| 5 | `gps_server:328` | `-90<=lat<=90 AND -180<=lon<=180` | **(0,0) PRZECHODZI** |
| 6 | `dispatch_pipeline._coloc_is_default_centroid:2951` | w `tol=0.06km` od BIALYSTOK/FIRMOWE | centroid-tol |

Sześć różnych odpowiedzi na to samo pytanie. #4 jest poprawne i kompletne — i **nigdzie u ingest**.

---

## 2. ⭐ FAIL-OPEN vs FAIL-CLOSE NA TYCH SAMYCH DANYCH (rdzeń zlecenia)

**Wejście:** zepsuty/niepełny wpis grafiku kuriera (literówka „11.00" zamiast „11:00", pusta godzina, brak w arkuszu, fetch 06:00 → 00:00-06:00 cała flota bez grafiku).

| Konsument | Plik:linia | Werdykt na zepsutym wpisie | Głośność |
|---|---|---|---|
| `is_on_shift` (schedule_utils) | `:376` brak grafiku · `:383` nie znaleziono · `:392` brak godzin · **`:401` `except ValueError` „11.00"** | **fail-OPEN → `return True` „na zmianie 24/7"** | **CICHO** (zwraca reason-string, ZERO log.warning) |
| `_shift_start_dt` / `_shift_end_dt` | `courier_resolver.py:1252` / `:1269` (`":" not in s` → None; `except` → None) | **fail-CLOSE → `None`** → `cs.shift_start/end=None` | cicho (debug) |
| feasibility shift_end=None | `feasibility_v2.py:686-720` FAIL12 | fail-OPEN (bag/gps) LUB fail-CLOSE `NO_ACTIVE_SHIFT` | **GŁOŚNO** (Z2 `log.warning` „SPRAWDŹ GRAFIK") |

**Skutek tej samej literówki „11.00":**
1. `is_on_shift` → `True` (kurier liczony JAKO on-shift; brak demote pre_shift, brak kary warm-up, eligible do post_shift_synthetic).
2. `_shift_start_dt`→None → floor `max(now, shift_start)` = **no-op** (preshift-audit BUG#1: „literówka 11.00 = floor martwy dla kuriera na zawsze, cicho").
3. `_shift_end_dt`→None → feasibility próbuje `NO_ACTIVE_SHIFT` → ratuje FAIL12 jeśli bag/gps (głośno).

**3 sprzeczne traktowania jednego defektu** = I-class konflikt + M-class cisza. **Poprawny wzorzec ISTNIEJE w tym samym systemie** (FAIL12 `log.warning` „Z2 anti-silent-failure: fail-OPEN MASKUJE realną awarię grafiku → GŁOŚNO", `feasibility_v2.py:701-705`) — `is_on_shift` go NIE stosuje. Wystarczyłby ten sam głośny log + walidacja wpisów u źródła (arkusz).

---

## 3. (0,0) — od INGEST do V328-EJECT (K5a, łańcuch)

```
gps POST {lat:0,lon:0}  ──[M1: gps_server:328 range-check PRZECHODZI]──►  _update_gps zapisuje (0,0)
panel/parcel delivery_coords=[0,0] ──[M2: state_machine:504/528 verbatim, :791 `if pickup_coords:` truthy]──► orders_state
                                                          │
                          ┌───────────────────────────────┘
                          ▼
        bag busy-kuriera zawiera order z coords (0,0)
                          │
   [GEOCODE warstwa: haversine FAIL-LOUD raise ValueError — osrm_client:419 / geometry:20]
                          │
                          ▼
   [M8: dispatch_pipeline:5697 _v328_eval_safe łapie ValueError → ('fail', cid) → KURIER POMINIĘTY]
                          │
                          ▼
        V328_CP_SOLVER_FAIL_PER_COURIER → ZAJĘTY kurier wypada z puli kandydatów
```

**Ironia fail-loud (M8/C):** guard haversine wprowadzony (Lekcja #81) by zabić *silent ~6285km*. Ale na warstwie DECYZJI ValueError staje się **fail-DESTRUCTIVE**: zamiast cichej złej liczby — cicho wyrzucony **dobry, zajęty kurier**. To literalny wyzwalacz case 484400 (preshift-audit §0: Jakub 492 `V328_CP_SOLVER_FAIL haversine sentinel (0,0)` → wypadł z puli → zlecenie zostało pre-shiftowemu Dawidowi).

**Defensa = N łatek post-hoc, NIE u źródła:**
- `_sanitize_courier_pos` (`:232`) — chroni TYLKO `cs.pos` (0,0)→BIALYSTOK.
- sanitize `effective_start_pos` (`:3939`) — osobna łatka dla bag-tail pos.
- `_repair_bag_coords`/`BAG_COORD_REPAIR` (`:3090`, flaga ON) — re-geokod bag-ordera per-miejsce.
- `compute_bundle_deliv_coloc` (`:2986/3004`) — własny check (0,0)/lat=0.
Cztery łatki, każda jedno call-site. `coords_in_bialystok_bbox` (kompletny) NIE u ingest. **To dlatego (0,0) wraca** (residual „9/7h post #28 cz.1", komentarz `:3940`).

**Live-evidence (read-only, 14:0x):** `orders_state.json` orders=280 deliv_zero=**0** pickup_zero=**0**; `courier_last_pos.json` zero=**0** → trucizna DZIŚ czysta (downstream defensy działają teraz), ale INTERMITENTNA (preshift-audit: „do 302 V328 / 394 sentinel/dzień"). Snapshot czysty ≠ brak luki ingest.

---

## 4. (0,0) PLACEHOLDER persystowany w planie (K5b — M3)

`panel_watcher._save_plan_on_assign:436` buduje body planu z **hard-coded `{"lat": 0.0, "lng": 0.0}`** dla `start_pos` (`:474`) i KAŻDEGO stopu (`:486` pickup, `:496` dropoff) — komentarz `:472` „lat/lng niestety nie w decision_record, użyj fallback (courier_resolver się dopisze przy next propose)". Body → `plan_manager.save_plan` → **`courier_plans.json`**.
**Live-evidence:** `courier_plans.json` plans=47 total_stops=79 **zero_coord_stops=11** plans_with_any_zero=**9**. Placeholder (0,0) FAKTYCZNIE persystuje w żywym kanonie planów.
**Otwarte / severity P2:** współrzędne stopu w planie NIE są dziś load-bearing (plan niesie sekwencję + predicted-times; rendery liczą coords z orders_state) → **PLAUSIBLE** niski impakt. ALE: każdy konsument planu który zrobiłby haversine na `stop.coords` trafi w sentinel; naprawa „przy next propose" = krucha, czasowa. Most do K5a (kolejny producent (0,0)).

---

## 5. CICHA AWARIA w renderze/markerze (M9-M12)

- **M9 — feed.py fail-soft→{} (CROSS):** `_load_global_alloc_fresh` (`:51-52`) i `_load_reassign_select_fresh` (`:75-76`) mają **`except Exception: return {}`**. Konflacja: plik LEGALNIE nieobecny (writer zdrowy, jeszcze nie firował) ≡ plik USZKODZONY (JSON parse fail) ≡ writer UMARŁ → wszystkie `{}`. Overlay (resweep pile-on, reassign-select) **znika CICHO** z konsoli koordynatora; brak alertu/markera. Jeśli `reassignment_global_select` (timer 3min) padnie → konsola traci rozbijanie pile-onów bez sygnału. Docstring chwali to jako cechę („NIGDY nie wywala feedu") — ale bare-except maskuje korupcję. **Otwarte P2.**
- **M11 — pos replay-label „gps" (SERIAL/M):** rescue z store (`courier_resolver:1080` `pos_from_store=True`) REPLAYUJE label `pos_source="gps"` (`_rescue_from_last_pos:201`, TTL 25min). `shadow_dispatcher:293-295` serializuje pos_source — komentarz: „bez tych pól nieodróżnialny od żywego fixa". Pozycja sprzed ≤25min udaje świeży fix. **Częściowo łatane** (`ENABLE_FAIL12_STOREPOS_STRICT` ON nie liczy store-pos jako dowodu pracy, `feasibility_v2:712-720`), ale label dalej myli inne konsumenty. P2.
- **M12 — FIRMOWE fallback MASKUJE fail (M):** `panel_watcher:1235/1273` podstawia `FIRMOWE_KONTO_FALLBACK_COORDS` gdy parser/geocode firmowego fail — feasibility dostaje realny-wyglądający pickup dla adresu którego NIE zgeokodował. By-design (eliminuje „BRAK KANDYDATÓW"), ale to sentinel-jako-dane: błąd geokodu staje się niewidzialny. P2.
- **M10 — BIALYSTOK dup + kolizja (A1/M):** stała w 4 modułach (§1a) + kolizja „fikcja vs trucizna". P2/P3 (maintainability + latentny false-exclude coloc dla realnego centrum).

---

## 6. KONTEKST POZYTYWNY (wzorce do naśladowania — NIE bug)
- `coords_in_bialystok_bbox` (`common.py:513`) — JEDYNY kompletny walidator (None/NaN/(0,0)/bbox). Cel docelowy: wpiąć u ingest.
- FAIL12 `log.warning` „GŁOŚNO" (`feasibility_v2:701`) — poprawny wzorzec fail-open-z-alertem; przeciwieństwo cichego is_on_shift.
- haversine fail-loud (osrm+geometry, oba bliźniaki MEMORY#12) — poprawne U ŹRÓDŁA MATEMATYKI; problem jest w warstwie DECYZJI (M8), nie w guardzie.
- `_coloc_is_default_centroid` + `ENABLE_BUNDLE_COLOC_CENTROID_GUARD` (flaga ON LIVE) — działający guard centroidu (122 zatrute adresy). Punktowy, ale poprawny.
- gps_quality teleport/accuracy filtr — „NIGDY karać BRAKU GPS" — świadoma polityka, nie sentinel-bug.

---

## 7. TABELA POKRYCIA (jawne luki, nie cisza — C11-c)

### Zbadane (świeży grep/read DZIŚ)
| Warstwa | Moduły | Co sprawdzone |
|---|---|---|
| STATE | `state_machine.py` (ingest 504/528/791/804/825), `panel_watcher.py` (_save_plan 436-512, _resolve_pickup_coords 114, 0,0-default 474/486/496), `panel_client.py` (coords origin), `courier_resolver.py` (BIALYSTOK ×6, rescue 1077-1090, pos_from_store, shift dt-helpers 1235-1290, import-fallback 1396-1410), `gps_server.py` (ingest 316-345), `gps_quality.py` (filtr) | sentinele (0,0)/BIALYSTOK, fail-open/close, ingest-guards |
| GEOCODE | `geocoding.py` (return None on miss), `osrm_client.py` (haversine 399-419, table/route sentinel 528-749), `geometry.py` (haversine_km 11-20), `common.py` (coords_in_bialystok_bbox 513, BUNDLE_COLOC centroids 2296, FIRMOWE 3509, FALLBACK constants), centroid-guard (`dispatch_pipeline` 2951/2973-3010) | 6 definicji sentinela, centroid-coloc guard, fail-loud |
| SERIAL | `shadow_dispatcher.py` (pos_source serialize 293/635/960, coords passthrough 1095), `plan_manager` (save_plan konsument), `courier_plans.json` (live-evidence) | (0,0) persist, pos-replay-label |
| CROSS | `nadajesz_clone/panel/backend/.../feed.py` (fail-soft 51/75), schedule_utils top-level (is_on_shift 374-409, load_schedule) | fail-soft→{}, schedule fail-open |
| FEASIBILITY (styk) | `feasibility_v2.py` FAIL12 (686-720, storepos-strict 411-421) | fail-open-z-alertem (wzorzec dobry) |
| LIVE-evidence | `orders_state.json`, `courier_last_pos.json`, `courier_plans.json`, `flags.json` | (0,0) snapshot, flagi guard ON |

### Flagi (efektywne, read-only)
`ENABLE_BUNDLE_COLOC_CENTROID_GUARD=True` (flags.json), `ENABLE_BAG_COORD_REPAIR` brak w flags.json → env-default `=1` ON (`common.py:2691`, atrybutowy `C.ENABLE_BAG_COORD_REPAIR` — NIE hot-reload przez `C.flag()` → D-dryf drobny), `ENABLE_FAIL12_SCHEDULE_FAILOPEN=True` (flags.json; common env-default OFF — flags.json wygrywa), `ENABLE_FAIL12_STOREPOS_STRICT` brak w flags.json → env-default `=1` ON.

### LUKI POKRYCIA (jawne)
1. **`fleet_state.py` (konsola) wewnętrzna obsługa sentinela** — NIE prześwietlona pod (0,0)/None coords w `_eta_chain`/`_build_route` (A5 pokrył ISTNIENIE kopii + parytet 95.9%; sentinel-handling konsoli per-linia = poza moim budżetem). **Faza B/C: czy konsola haversine'uje (0,0) stop-coords z planu (M3 styk).**
2. **`courier_api/courier_orders.py` (apka) sentinel** — `_compute_live_eta`/`_attach_fallback_eta` na (0,0)/None — nie zweryfikowane per-linia (cross-repo, A5 zinwentaryzował kopię). PLAUSIBLE most do M3.
3. **`courier_api_panelsync` fork (665 vs 1285 L)** — martwy bliźniak, sentinel-handling nie diffowany (A5: zdywergowany).
4. **parcel lane (0,0)** — `parcel_lane_merge`/`parcel_assign` nie sprawdzone czy sender_lat/lng (migracja parcel02) waliduje (0,0) — parcel ma natywny tor, sender geokod osobny. **Faza B: czy parcel ingest waliduje coords.**
5. **Materialność M3** (czy KTOŚ haversine'uje stop.coords z courier_plans.json) — deklarowana PLAUSIBLE z lektury, nie udowodniona trace'em konsumpcji. Faza C oracle.
6. **Rate (0,0)/V328 na żywo** — preshift-audit „302/dzień" NIE re-zwalidowane moim grepem ledgera (read-only snapshot orders_state=0 dziś). Faza C: `grep -c V328_CP_SOLVER_FAIL` w `scripts/logs/shadow_decisions.jsonl` peak.
7. **`panel_client.py` surowy parse coords** — origin (0,0) z paneli nie w pełni prześledzony do pola źródłowego (grep nie pokazał wprost (0,0) producenta w panel_client; (0,0) wchodzi głównie gps_server + placeholder).

### NIE-luki (świadomie poza zakresem)
Mailek/Papu (granica STOP na dyspozytorni). Sentinele pozycji jako BUCKET-twin gr.3 (osobny agent B-position). Floor 17-powierzchni (gr.6, osobny agent — tu tylko styk fail-open). Frozen `_lex_qual`, SLA-anchor, route-order cross-repo (inne agenty B).

---

## 8. DEDUP → ROOTY (anty-double-count dla Fazy E)
Wszystkie moje findingi zwijają się do **K5 (sentinel-most)** rozbitego na 5 sub-rootów (§0). Cross-ref do A6/preshift:
- **K5a (M1/M2/M7/M8)** ⊃ preshift-audit BUG#2 §3 („repair/reject (0,0) u INGEST"). Most do gr.3b (`_SYNTH_POS` sentinel-klasyfikator) i gr.6 (floor).
- **K5b (M3)** — nowy, nie w A6 wprost; producent (0,0) w persystencji planu.
- **K4/M (M4/M5)** ⊂ root floor (A6 gr.6 / preshift BUG#1 warstwa #2 „is_on_shift fail-open") + I-class konflikt precedencji. NIE liczyć podwójnie z floor-agentem — to JEGO dane od strony sentinela/fail-open.
- **N/M (M6)** — rozsyp progów sentinela; dołożyć do dashboardu entropii.
- **M-render (M9/M11/M12/M10)** — A5 zauważył M9 („feed `_load_*_fresh` fail-soft→{}"); reszta nowa.

**NIE są osobnym chaosem:** M7 (łatki) = symptom K5a. M5 = manifestacja M4+dt-helpers. M8 = symptom K5a (eject z (0,0)). M10 dup-stała ⊂ N.
