# B04 — KLASA B: ASYMETRIA BLIŹNIAKÓW (fix w 1 z N ścieżek wykonawczych)

**Agent:** B04-B-twin-asymmetry · **Lane:** B · **Tryb:** READ-ONLY · **Data:** 2026-06-30 ~14:1x UTC · **Sesja:** tmux 2
**HEAD:** `8024705` (working tree silnika czysty — `git status` brak `.py`). **Wszystkie `plik:linia` ze ŚWIEŻEGO grepu tego runu** (nie z seed-doców — linie dryfują przez commity sesji 3/4).
**Cel:** dla KAŻDEJ reguły z >1 ścieżką wykonawczą — czy fix jest we WSZYSTKICH bliźniakach, **KTÓRY bliźniak ZOSTAŁ** nienaprawiony.
**Metoda dedup (z A6):** nie liczę grup 1/3/5 jako 3 chaosy — to jeden root K1 (selekcja). Raportuję 5 distinct otwartych rozjazdów + bliźniaki cross-repo. Każda instancja → `dedup_hint` do rootu (R1 one-selection-key / R2 one-route-order / R3 one-SLA-R6-anchor / R4 one-floor / + P0-B de-pile / M coupled-field).

---

## TL;DR — 8 distinct bliźniaków, KTÓRY ZOSTAŁ

| # | Reguła / para | Fix poszedł do… | ZOSTAŁ (nienaprawiony bliźniak) | Live? | Sev | Root |
|---|---|---|---|---|---|---|
| F1 | pozycja-równość (8 bliźniaków) | engine 6/8 scalone na `_selection_bucket` | **out-of-engine gates: `reassignment_forward_shadow` `_SYNTH_POS`, `feed.py`, `auto_assign G7`, `drive_min`** | reassign+feed LIVE | P1 | R1/K1 |
| F2 | duch-przerzut → konsola vs Telegram | Telegram ma `_pos_trusted` filtr | **`feed.py:258` konsola pokazuje quality_reassign BEZ `_pos_trusted`** | LIVE | P1 | R1 |
| F3 | `lex_qual` klucz jakości | live-importery A-D + `_best_effort_objm_pick` na kanon `_OL.lex_qual` | **`_objm_lexr6_shadow._lex_qual:1122` zamrożony 3-krotka vs kanon warunkowo 4** | shadow | P2 | R1 |
| F4 | de-pile / global de-konflikcja | **PRZERZUT** (`reassignment_global_select`→`global_allocate` LIVE) | **NOWE zlecenie: shadow-only** (`PENDING_RESWEEP_LIVE=false`); `_tick` per-event bez claim | przerzut LIVE / nowe shadow | P1 | P0-B |
| F5 | pre-shift floor (pickup≥shift_start) | **feasibility** (clamp+reject) | **`plan_recheck` regen co 5min BEZ floor** (`_start_anchor:554`); 0 runtime-inwariantu | LIVE | P1 | R4/K2 |
| F6 | regen kanonu (env route/canon) | **plan-recheck** ma SEQUENCE_LOCK/COMMITTED_PROP/LIVE_ETA | **panel-watcher recanon NIE ma tych 3** (zmierzone systemctl A5) | LIVE | P2 | R2/D2 |
| F7 | carried-first relax konsola↔apka | console dostała `TRUST_CANON_WHEN_COVERS_BAG` (29.06 „parytet z apką") | **parytet trzymany 3 flagami w 3 systemach** — gdy ≥1 OFF/kanon-niepełny → oba spadają na LOKALNY carried-first | LIVE | P2 | R2 |
| F8 | geokod pola sprzężone coords↔tekst | `gastro_edit.regeocode` synchronizuje tekst gdy flaga ON | **flaga `ENABLE_REGEOCODE_SYNC_TEXT` default OFF w kodzie, poza ETAP4/fingerprint** (mina przy resecie flags.json) | flaga ON dziś | P2 | M/coupled |
| F9 | SLA/R6 kotwica | R6-thermal ready-anchored (`r6_thermal_anchor`) | **`_count_sla_violations:635` wciąż pickup_at; + paczka-exempt w R6+SLA, BRAK w O2** | LIVE | P2 | R3 |

**Bliźniaki ZWERYFIKOWANE jako PARYTET-OK (anty-fałszywka):** haversine (0,0) — `osrm_client.haversine` ORAZ `geometry.haversine_km` OBA mają guard (#12 kompletny, F10 niżej). Doc-drift seedów skorygowany (F11).

---

## F1 — POZYCJA-RÓWNOŚĆ: engine scalony, OUT-OF-ENGINE GATES zostały (root „klasa wraca ≥4×")

**Reguła (Adrian 29.06 HARD):** kurier bez GPS / pre_shift = LICZONY RÓWNO, ZERO kary bucketem (no_gps/pre_shift → bucket 0 gdy equal-treatment ON).

### Engine — 6/8 bliźniaków SCALONE na `_selection_bucket` (świeży grep)
- KANON `_selection_bucket(c)` **`dispatch_pipeline.py:2451`** (`_equal_bucket_on()` + ps∈{no_gps,pre_shift}→0).
- twin1 `_late_pickup_score_first_key` → `:546`; twin2 `_best_effort_sort_key` → `:583`; twin3 objm-d2 `bucket_fn=_selection_bucket` → `:1378`; twin5 `_pln_pure_resort` → `:1086`; twin6 `_objm_lexr6_shadow` → `:1119-1120`.
- twin4 **`_best_effort_fastest_pickup_key:618`** = `bucket = _selection_bucket(c)` — **⚠ SCALONE 29.06** (komentarz `:613-617` „Sprint3 NO-GPS-EQUAL… z JEDNEGO źródła"). **Seed `ziomek-change-protocol.md:44` pkt 4 + A2 §smell #4 STALE** twierdzą „HARDCODED bucket informed0/blind2 BEZ flagi" — to NIEAKTUALNE (A6 ma rację: scalone). → patrz F11.

### ZOSTAŁY — out-of-engine (NIC nie wiąże ich z `_selection_bucket`; ŻADEN test parytetu)
| Bliźniak | `plik:linia` (świeże) | Co robi | Stan |
|---|---|---|---|
| **DUCH PRZERZUTU** | `tools/reassignment_forward_shadow.py:64` `_SYNTH_POS={"none","pin","pre_shift",""}` | własna klasyfikacja „pozycja zgadnięta" | **PÓŁ-zrównane**: `no_gps` NIE w `_SYNTH_POS` (trusted), ale **`pre_shift` JEST** (untrusted) → asymetria z `_selection_bucket` (które traktuje OBA równo). `a_late=(a_cand is None) or a_measured_late` (`:256-260`) — pre_shift bez zmierzonego R6 = nie-late = ripuje |
| auto-assign G7 | `auto_assign_gate.py:163` `if pos_source not in tuple(informed_pos_sources): blocks.append(f"pos_not_informed:{pos_source}")` | blokuje auto-assign dla każdej pozycji nie-informed | **LATENT** (`ENABLE_AUTO_ASSIGN=false`) — ugryzie na autonomii: no_gps/pre_shift blokowane mimo równości w selekcji |
| drive_min calib | `drive_min_calibration.py:53-54` `OFFSET_TABLE{"no_gps":6.5,"pre_shift":15.3}` | dodaje minuty dojazdu no_gps/pre_shift = kara czasowa | **MAIN OFF** (`ENABLE_DRIVE_MIN_CALIBRATION_V2=false`/shadow ON) — artefakt, NIE flipować; mina re-flipu |

- **Parytet:** `grep selection_bucket|equal_bucket|NO_GPS_EQUAL tools/reassignment_forward_shadow.py` = **PUSTE** (potwierdzone). Brak importu, brak testu wiążącego.
- **Dlaczego wraca:** każda naprawa scala 1 kopię na `_selection_bucket`, gates zostają. Memory: `reassignment_forward` = **59% fałszywych ratunków** ripujących no_gps/pre_shift.
- **kind:** source · **still_open:** TAK (out-of-engine) · **dedup:** R1 (one-selection-key).

## F2 — KONSOLA `feed.py` pokazuje quality_reassign BEZ `_pos_trusted` (Telegram ma)

- `nadajesz_clone/panel/backend/app/integrations/ziomek/feed.py:258` `if not d.get("quality_reassign"): continue` — JEDYNY filtr to `quality_reassign`. **`grep _pos_trusted feed.py` = PUSTE.**
- Bliźniak z filtrem: `tools/reassignment_forward_shadow.py:444` `def _pos_trusted(...)` + `:459` `if trusted_only and not _pos_trusted(...)` (flaga `REASSIGN_FWD_NOTIFY_TRUSTED_ONLY`) — **Telegram/notify ścieżka odsiewa untrusted-pozycję, konsola NIE.**
- Skutek: rekord może mieć `quality_reassign=True` + `_pos_trusted=False` → konsola POKAZUJE koordynatorowi ratunek na zgadniętej pozycji, Telegram by go ukrył. Cross-repo asymetria (J).
- **kind:** source · **still_open:** TAK · **LIVE** (overlay konsoli) · **dedup:** R1.

## F3 — `_objm_lexr6_shadow._lex_qual` ZAMROŻONY 3-krotka vs kanon warunkowo 4

- KANON `objm_lexr6.lex_qual` **`objm_lexr6.py:29-47`**: base 3-krotka; gdy `ENABLE_POST_SHIFT_OVERRUN_PENALTY` ON → **prepend** `post_shift_overrun_penalty` = 4-krotka (`:44-46`).
- FROZEN inline `_objm_lexr6_shadow._lex_qual` **`dispatch_pipeline.py:1122-1126`**: ZAWSZE 3-krotka, NIE post-shift-aware.
- Bucket-część TEGO shadow już scalona (`:1119-1120`), więc rozjazd siedzi WYŁĄCZNIE w `_lex_qual`. Live-importery A-D + `_best_effort_objm_pick` używają kanonu `_OL.lex_qual` (`:665,667,710,1230,1250,1307,1339`) — UNIFIED.
- **Dziś zgodne TYLKO bo `ENABLE_POST_SHIFT_OVERRUN_PENALTY` effective=False** (A3 §2a). Flaga ON → cień rankuje INACZEJ niż live-selekcja → **kłamiący przyrząd (E)**.
- Świadomie zamrożony pod at#152 (docstring `objm_lexr6.py:12-16`), test unify go wyłącza. → NIE „bug do naprawy dziś", ale OTWARTY rozjazd strukturalny (część unifikacji).
- **kind:** source · **still_open:** TAK (frozen by design) · **is_patched:** częściowo (bucket scalony, lex nie) · **dedup:** R1.

## F4 — DE-PILE: zbudowany dla PRZERZUTU, NOWE zlecenie greedy bez claim (P0-B)

- Single-source de-konflikcji = `tools/pending_global_resweep.py:145` `global_allocate` + `:124` `_tentative_assign` (claim floty).
- **PRZERZUT — LIVE:** `tools/reassignment_global_select.py:15` importuje „ten sam sprawdzony silnik `global_allocate`" (`ENABLE_REASSIGN_GLOBAL_SELECT=true`).
- **NOWE zlecenie — shadow-only:** `pending_global_resweep.py:66` `FLAG_LIVE="PENDING_RESWEEP_LIVE"` default OFF; `:421` `_log.warning("PENDING_RESWEEP_LIVE=ON ale ścieżka live niewpięta — shadow-only")`. Faza C global-alloc = display-only.
- **Silnik nowego zlecenia BEZ claim:** `shadow_dispatcher.py:1118` `fleet={cs.courier_id: cs for cs in dispatchable_fleet()}` (RAZ), `:1141 for ev in events:`, `:1195 result=process_event(ev,fleet,meta)` → `:1108 assess_order(...)` — **flota niemutowana między eventami** (`assess_order`/`check_feasibility_v2` brak param claim/reserve).
- Skutek (oracle allocation-audit): JEDEN kurier (447) proponowany 127× / 32 distinct orderów; `g_maxpile_before`=7. To bliźniak: de-pile dla przerzutu, brak dla nowego.
- **kind:** source · **still_open:** TAK · **dedup:** P0-B (allocation family). ⚠ Adrian: P0-A geometria + P0-B de-pile MUSZĄ iść RAZEM (osobno = no-op).

## F5 — PRE-SHIFT FLOOR: feasibility clampuje, plan_recheck regen co 5min BEZ floor

- **Gate A (feasibility) — MA floor:** `feasibility_v2.py:790` „kurier z shift_start>now → earliest_departure=shift_start"; HARD-reject `:751` `too_early_min > V325_PRE_SHIFT_HARD_REJECT_MIN(30)`; warm-up `:760`.
- **Gate B (plan_recheck) — BRAK floor:** `plan_recheck.py:554` `_start_anchor` / `:534` `_earliest_committed_pickup_anchor` → `earliest_departure=anchor_departure` (committed-pickup, BEZ shift_start). Regen `courier_plans.json` co 5min (timer) **odclampowuje** → „leak najszersza dziura" (floor-audit #5).
- **0 runtime-inwariantu:** `grep -rE 'pickup.*>=.*shift_start|available_from' --include=*.py` (poza tests/eod) = **PUSTE** → brak strażnika, brak jednego `available_from`.
- Bliźniak feasibility↔plan_recheck rozjechany (wzorzec protokołu #3 „gate w fazie A, brak w fazie B").
- **kind:** source · **still_open:** TAK · **dedup:** R4 (one-floor) / K2 (plan_recheck=cofacz).

## F6 — REGEN KANONU: panel-watcher recanon BEZ 3 flag, które ma plan-recheck (env-twin)

- Oba REGENERUJĄ `courier_plans` (plan-recheck tick 5min; panel-watcher recanon on write/pickup/override).
- env-frozen (module-level `os.environ.get`): `plan_recheck.py:363 ENABLE_PLAN_SEQUENCE_LOCK`, `:389 ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION`, `:82 ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH`.
- **Zmierzone `systemctl show -p Environment` (A5 B.1):** te 3 ustawione ON **TYLKO plan-recheck (+b-route)**, **ABSENT na panel-watcher**. panel-watcher ma za to RECANON_ON_WRITE/IMMEDIATE_REDECIDE (których plan-recheck nie ma).
- Skutek: recanon zdarzeniowy (watcher) NIE stosuje sequence-lock / committed-propagation tie-breaka / live-eta-refresh → kanon zdarzeniowy ≠ tickowy. **Materialność PLAUSIBLE** (A5: wymaga trace osiągalności `_retime_one_bag_plan`).
- **kind:** source · **still_open:** TAK (PLAUSIBLE material) · **dedup:** R2 (route-order) + D2 (flaga service-scoped).

## F7 — CARRIED-FIRST relax konsola↔apka: parytet trzymany flagami w 3 systemach

- **Console (`fleet_state.py`):** `_order_from_plan_seq:342` + `_build_route:395`; dostała `TRUST_CANON_WHEN_COVERS_BAG` (`:370/386/877`, komentarz `:375` „2026-06-29, parytet z apką") — KOPIA relax silnika (NIE importuje dispatch_v2).
- **Apka (`courier_api/courier_orders.py`):** `build_view:1116` deleguje do `route_podjazdy.order_podjazdy(... trust_canon=BUILD_VIEW_TRUST_CANON_ORDER)`; lokalny fallback `_prioritize_carried_dropoffs:467` gdy `not BUILD_VIEW_TRUST_CANON_ORDER` (`:1158`) lub plan niepełny (`:1187`). `route_podjazdy.py:206 if trust_canon:` renderuje kanon TYLKO gdy pokrywa CAŁY worek.
- **Stan:** strukturalnie LARGELY ALIGNED 29.06 (covers-bag w obu). RESIDUAL = parytet trzymany **3 niezależnymi flagami w 3 systemach** (A5 C.7): `BUILD_VIEW_TRUST_CANON_ORDER` (courier_api config) + `TRUST_CANON_ORDER`+`TRUST_CANON_WHEN_COVERS_BAG` (panel PANEL_FLAG_). Gdy choć jedna OFF / kanon nie-pokrywa → OBA spadają na LOKALNY carried-first (`_build_route` vs `_prioritize_carried_dropoffs`) = rozjazd. Parytet = POMIAR 95.9% (`fleet_state.py:866`), NIE inwariant.
- Liczba „44-75/d" (twin #11) jest sprzed 29.06; bieżący parytet = `ziomek_time_route_monitor.jsonl` (932KB FRESH) — **C4/C10: werdykt LICZBĄ z monitora, NIE lektura** (Faza C oracle, nie odczytany tu).
- **kind:** symptom (largely fixed) · **still_open:** TAK (parytet niepotwierdzony liczbą) · **dedup:** R2.

## F8 — GEOKOD pola sprzężone coords↔tekst: regeocode synchronizuje TYLKO za flagą (mina)

- `gastro_edit.regeocode_and_update:154` `upsert={"delivery_coords": list(coords)}`; tekst (`delivery_address`/`delivery_city`) dopisywany **TYLKO** `:157 if C.flag("ENABLE_REGEOCODE_SYNC_TEXT", False) and (display_address...)`.
- Bliźniak symetryczny: `panel_watcher` (przy tworzeniu) pisze coords+tekst RAZEM (Załącznik A protokołu, near-miss 29.06 484269 „Można"≠„Mroźna" 4,26km + zły district bo `drop_zone_from_address` czyta tekst).
- **Stan:** `flags.json ENABLE_REGEOCODE_SYNC_TEXT = True` (zmierzone) → DZIŚ zsynchronizowane. ALE **default w kodzie OFF** (`:157` 2. arg `False`) + flaga w conftest-leak (A3 §3a, poza ETAP4/fingerprint) → **mina klasy D/M:** reset/utrata flags.json = asymetryczny writer wraca (coords bez tekstu → district mismatch utrwalony w stanie).
- **kind:** source · **still_open:** TAK (latent, flag-mina) · **is_patched:** TAK ale flag-gated · **dedup:** coupled-field (Załącznik A) / M.

## F9 — SLA/R6 KOTWICA: R6-thermal ready-anchored, `_count_sla_violations` wciąż pickup_at + paczka O2-gap

- **R6 thermal (ready) ✅:** `route_simulator_v2.py:663 r6_thermal_anchor` + `feasibility_v2.py:1046 anchor=r6_thermal_anchor(...)` (INV-R6-ANCHOR-CONSISTENCY, import `:33`) → ready/picked_up.
- **SLA-count (pickup_at) ✗ ZOSTAŁ:** `route_simulator_v2.py:635 _count_sla_violations` — anchor = `pickup_at[oid]` (TSP-projected) → `picked_up_at` → `now` (`:648-656`), **NIE ready**. Wynik `plan.sla_violations` konsumują: feasibility (`sla_violations_count` `:825`) + `plan_recheck._o2_key`. Ta sama „spóźnienie od odbioru" liczona DWOMA kotwicami (R6 ready / SLA pickup_at).
- **Paczka-exempt — N-of-M:** `_o_paczka_exempt` w R6-termik (`feasibility_v2.py:1050/1054/1080/1105`) + SLA-bramka (A2: `:~1152`), **BRAK w O2 ready-anchor sweep** (4. site, flip 02.07). Exempt naprawiony w R6+SLA, nie w O2.
- **kind:** source · **still_open:** TAK (O2 review 02.07) · **dedup:** R3 (one-SLA-R6-anchor).

## F10 — (PARYTET-OK) haversine (0,0): OBA bliźniaki guardują — #12 kompletny; resztka = ingest (M)

- `osrm_client.haversine:399` → guard `:408-411` None + `:411` `if ll1==(0.0,0.0) or ll2==(0.0,0.0): raise ValueError`. `geometry.haversine_km:11` → `:17-20` ten sam guard None+(0,0). **OBA mają → twin SYMETRYCZNY (#12 fix kompletny).**
- Resztka NIE-twin: `dispatch_pipeline._compute_repo_cost_km:2147 if not drop_coords` przepuszcza `[0,0]` (truthy) → `:2149 haversine(...)` RZUCA ValueError → połknięty `:2150 except: return None` = cicha utrata kary repo (nie crash). + ingest coords (0,0) bez repair u źródła = **M-class (sentinel), agent M** — cross-ref, NIE twin-asymetria.
- **kind:** symptom · **still_open:** NIE (twin OK) · **is_patched:** TAK · **dedup:** M-sentinel (cross-ref).

## F11 — (DOC-DRIFT) seedy A2/protokół STALE o 2 engine-twinach (anty-fałszywka)

Fix-zweryfikowany-kompletny — żeby przyszła sesja NIE re-flagowała:
- `_best_effort_fastest_pickup_key:618` używa `_selection_bucket` (scalone 29.06) — `ziomek-change-protocol.md:44 pkt4` + `A2 smell #4` wciąż mówią „HARDCODED informed0/blind2 BEZ flagi". STALE.
- `_best_effort_objm_pick:665/667/710` używa kanonu `_OL.lex_qual` — `ziomek-change-protocol.md:48` „zawsze 4-krotka z `_ps_pen`". STALE (unifikacja 25.06). Jedyna inline-resztka lex = `_objm_lexr6_shadow` (F3).
- A6 (świeży) ma rację w obu; seed-docy nie. → przy fixach R1 czytaj A6/świeży grep, nie protokół-tekst.
- **kind:** symptom (doc-drift) · **still_open:** NIE (kod naprawiony) · **dedup:** R1.

## F12 — (cross-ref J/K) `courier_api_panelsync` = fork courier_orders 665L vs 1285L

- A5 C.5: `courier_api_panelsync/courier_orders.py` (665L) ZDYWERGOWANY od `courier_api/courier_orders.py` (1285L); własny `_plan_stop_sequence:366`. Dwie checked-out kopie repo (worktree panel-sync `4ab1e6d`). Mapowanie statusów / carried-first zdublowane → rozjazd przy zmianie jednej. Floor-audit: „MARTWY — nie serwowany" build_view, ale panel_sync.py z niego biega.
- **kind:** source · **still_open:** TAK (martwy-ish twin) · **dedup:** J/K (worktree-fork) — pełna analiza = agent J/K, tu cross-ref.

---

## TABELA POKRYCIA (coverage — co sprawdzone świeżym grepem / co NIE)

### Sprawdzone (świeży grep/read tego runu)
| Obszar | Pliki:linie zweryfikowane |
|---|---|
| pozycja-bucket engine | `dispatch_pipeline.py:2451,546,583,618,1086,1119-1120,1378,2456-2477` + `objm_lexr6.py:50-62` |
| lex_qual kanon vs frozen | `objm_lexr6.py:29-47` vs `dispatch_pipeline.py:1122-1126`; importery `:665,667,710,1230,1250,1307,1339` |
| out-of-engine pozycja | `tools/reassignment_forward_shadow.py:64,256-260,444,459` (grep selection_bucket=∅); `auto_assign_gate.py:163`; `drive_min_calibration.py:53-54` |
| konsola feed | `feed.py:258,55,239-258,345-376` (grep _pos_trusted=∅) |
| carried-first console↔apka | `fleet_state.py:342,370,386,395,432,877`; `courier_orders.py:467,1072,1116-1191`; `route_podjazdy.py:141,190,206` |
| de-pile new vs przerzut | `pending_global_resweep.py:66,145,124,421`; `reassignment_global_select.py:15`; `shadow_dispatcher.py:1118,1141,1195,1108` |
| pre-shift floor twin | `feasibility_v2.py:748-790`; `plan_recheck.py:534,554,700,1669` (grep invariant=∅) |
| canon regen env twin | `plan_recheck.py:82,363,389,709,1923,1982,2097` + A5 zmierzone systemctl |
| SLA-anchor twin | `route_simulator_v2.py:635,663`; `feasibility_v2.py:1046,825`; paczka `:1031,1050,1054,1080,1105,1116` |
| geokod coords/tekst | `gastro_edit.py:129,154,157,303`; writers `delivery_coords` 20+ call-site grep; `flags.json` REGEOCODE_SYNC_TEXT=True |
| haversine (0,0) twin | `osrm_client.py:399-421`; `geometry.py:11-20`; `dispatch_pipeline.py:2108-2151,2991,3000` |

### NIE sprawdzone (jawna luka, nie cisza)
1. **courier-app Kotlin** `RouteLogic.kt` (`buildSteps`/`pickupTogether`/`restaurantKey`) — 4. kopia bundlingu (A5 C.3) NIE re-grepowana tym runem; render kolejności = serwerowy (pokryty przez route_podjazdy), ale lokalny `restaurantKey`/`pickupTogether` grouping NIE zweryfikowany kodem. **Faza B/J.**
2. **Parcel lane** (`parcel_lane_merge`/`parcel_assign`) — czy używa `_selection_bucket`/`order_podjazdy` czy własnej ścieżki route-order/bucket (A6 gap #2). NIE sprawdzone.
3. **Liczby/materialność RUNTIME** (read-only, ZERO oracle): carried-first parytet (`ziomek_time_route_monitor` mismatches==?), de-pile `would_repropose` %, `reassignment_forward` 59% — z seedów/memory, NIE re-zmierzone. F6 panel-watcher recanon osiągalność = PLAUSIBLE niepotraceowane.
4. **feed.py runtime incydencja** — asymetria LOGIKI (brak `_pos_trusted`) potwierdzona; ile rekordów `quality_reassign=True ∧ pos untrusted` realnie trafia na konsolę = NIE policzone (Faza C).
5. **SLA-bramka paczka-exempt linia** (`feasibility_v2:~1152`) — wzięta z A2, R6-termik exempt sites zweryfikowane świeżo (`:1050-1117`); dokładna linia SLA-exempt nie repinowana.
6. **courier_api_panelsync** diff funkcyjny 665 vs 1285L NIE re-zdiffowany (A5 nota).
7. **Granica:** STOP na dyspozytorni — Mailek/Papu poza zakresem; `papu_dispatch_bridge` = boundary.

---

## HANDOFF Faza E/F (anty-double-count)
- F1+F2+F3 = JEDEN root **R1 (one-selection-key)** — NIE 3 chaosy. Otwarte resztki R1: out-of-engine gates (F1) + konsola feed (F2) + frozen lex (F3). PoC „one selection key" MUSI objąć przepięcie/zrównanie `reassignment_forward_shadow._SYNTH_POS` (pre_shift!) + `feed.py` filtr + `auto_assign G7`, inaczej duch-przerzutu resuscytuje dyskryminację (wzorzec #2).
- F6+F7 = root **R2 (one-route-order)** — env-twin (F6) i render-twin (F7). PoC musi przepiąć 4 powierzchnie (silnik+konsola+courier_api+apka) RAZEM (A5 handoff #5).
- F5 = root **R4 (one-floor)** — bliźniak gate↔regen; najwyższy zwrot = L2 plan_recheck (floor-audit).
- F4 = **P0-B** (allocation) — wejść RAZEM z P0-A (geometria w `lex_qual`).
- F9 = root **R3 (one-SLA-R6-anchor)** — O2 review 02.07.
- F8 = coupled-field (coords↔tekst) / M-mina.
- F10/F11 = parytet-OK / doc-drift (anty-fałszywka, NIE liczyć jako otwarte).
- F12 = J/K (worktree-fork, cross-ref agentowi J).
