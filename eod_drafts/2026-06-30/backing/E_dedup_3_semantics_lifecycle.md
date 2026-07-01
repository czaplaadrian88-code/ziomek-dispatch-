# FAZA E DEDUP — KLASTER R4-R6 (semantics + lifecycle + failure)

**Tryb:** READ-ONLY. **Data:** 2026-06-30. **Sesja:** tmux 2. **Klaster:** R4 Semantyka (F·L) + R5 Stres/awaria (M·G·O) + R6 Cykl-życia/zgnilizna (H·K).
**Wejście:** WF2_DIGEST.md (filtr klas F/G/H/K/L/M/O) + A6_twin_import_graph (grupa 7 eta_pickup + K5 sentinele) + drill B08/B21 (F) · B09 (G) · B10 (H) · B12/B13 (K) · B14 (L) · B15/B16 (M) · B18 (O) + werdykty C12/C13/C15/C18/C19. HEAD silnik `8024705`; cytaty `plik:linia` zweryfikowane świeżym grepem dziś (DRYFUJĄ — re-grep przed użyciem jako pewnik; spot-check potwierdził M-1 `:4823`, is_on_shift fail-open `:376/383/392/401`, G-1 `:1089/1098`, _esc_tier `:737/739/743`, coords_in_bialystok_bbox `:513`, gc_invalidated `:501`).
**Cel:** zwinąć INSTANCJE wskazujące TO SAMO ŹRÓDŁO w distinct-rooty (anty-double-count). Jeden root manifestuje się w wielu plastrach — inaczej zawyżę „chaos".

---

## ZASADA SCALANIA (anty-double-count)

- **Sentinele `(0,0)` = JEDEN root K5** (B15+B16 zgodne: „6 manifestacji, jeden root" / „5 sub-rootów K5"). NIE liczyć M-1..M-7 jako 7 chaosów — to brak-chokepointu-walidacji-coords + catch-all połykający fail-loud. Most do position-twins (A6 gr.3b) i floor (A6 gr.6 — TAM raportowane, tu tylko źródło danych).
- **Klasa F = TRZY distinct field-semantics-sources** (różne pola, nie to samo źródło w wielu miejscach): `eta_pickup` (kiedy-odbiór) ‖ `delivery_coords/address` (gdzie) ‖ `uwagi` (notes). B08 sam tak je rozdzielił. NIE scalać w jeden „chaos F".
- **Klasa L = naive-TZ-split to JEDEN root** (LN-1/2/3/4 + LN-x + checkpoint patched + warsaw-named-but-UTC = jedna konwencja-mina, NIE 6 chaosów). `tier`-overload / `shift_start`-midnight / enum-spelling = OSOBNE źródła słownictwa (distinct).
- **Klasa K (35, największa) zgrupowana w 2 rooty wg RYZYKA:** (a) martwy-kod-W-ścieżce-decyzji (skeletony+zneutralizowane-reguły+retired-branże+kłamiące-komentarze = C2-miny + mylą root-cause); (b) clutter-peryferii (.bak×326+ / orphan-tools / dead-config / misplaced / retired-grób / martwy-fork). Pod-rodzina „C3-deprecate-legacy nigdy nieaktywowana" (K1+K2) = jedna migracja, nie 2.
- **Klasa O = rodzina „os.replace bez fcntl" to JEDEN root** (O1/O3/O4/O10 lost-update RMW). CookieJar (O5) = osobny prymityw. O2/O6/O7 (load_plan side-effect / reconcile-lag / multi-timer) zwijają się do **plan-lifecycle (R6a/K2)**, nie do concurrency — most, nie nowy root.
- **Floor `pickup ≥ shift_start` (A6 R4 / 17 powierzchni) = ROOT E_dedup_1 #6** (cross-ref). Tu tylko fasetki: F2 (is_on_shift fail-open jako warstwa floor), L3 (shift_start midnight-anchor), H1b (`_start_anchor` dead-end). NIE re-derywuję 17 powierzchni.
- **SLA/R6-anchor (A6 gr.4) = ROOT E_dedup_1 #5** (cross-ref). Tu tylko F3 (kalibracja-zła-oś) która LUZUJE tę bramkę — co-design, nie ten sam root.

---

## 16 DISTINCT ROOTÓW (klaster R4-R6)

### — PODKLASTER R4 SEMANTYKA (F·L) —

### ROOT S1 — `eta-pickup-one-field-two-roles` (F · R4 · A6 GRUPA 7 / R5-DESIGN) — P2 OTWARTY ŹRÓDŁO
**Co:** `eta_pickup_utc` jest JEDNYM polem o DWÓCH rolach: twarda zmienna decyzyjna (kara scoringu `v324a_extension_penalty` + HARD-reject `verdict MAYBE→NO` przy ekstensji >60min + przez `target_pickup_at`→`time_arg`→committed `czas_kuriera` R27-frozen) ORAZ baza napisu `eta_pickup_hhmm`. Brak separacji display/decision → naiwna „zmiana napisu" = regres selekcji+feasibility+promesy (wzorzec #8).
- **Konsumenci decyzyjni:** `dispatch_pipeline.py:5174` extension_penalty → `:5610` hard-reject; `:5199` score; `:3189-3195` paczka-overrun (INNA kotwica `plan.pickup_at` — intra-engine anchor drift); cross-repo `Ops13Console.tsx:835` `time_arg` → `assign.py:42` `--time` → committed.
- **Skew dwóch komputacji (F1-C/F-4, PLAUSIBLE):** main-loop `eta_pickup_utc` (`:4057/4061/4077`) karmi extension PRZED post-loop nadpisem `c.metrics["eta_pickup_utc"]` dla no_gps `:5862` / pre_shift `:5877` → wartość scorująca ≠ serializowana/committed dla pre_shift/no_gps.
- **Display-floor overlay (F-3, A1/J fasetka):** „pokaż odbiór ≥ plan/committed/ready" re-implementowany w **6 powierzchniach × ≥7 flag** bez wspólnego importu (telegram ×2 dormant / apka FROZEN_PICKUP_ETA ×3 / konsola PIN_AGREED+CLAMP ×2 / engine-feasibility PRE_SHIFT_DEPARTURE_CLAMP ×1 / shadow PICKUP_DEBIAS) — display floored, decision-value surowy → „co widzę" ≠ „co policzono". Węższy aspekt niż A6 R4 floor (tam shift_start, tu plan/committed display).
- **Synthetic-eta most do K5 (F-10):** W6/W7 wpisują fikcyjną ETA (no_gps=now+15, pre_shift=now+shift_min) → `target_pickup_at` → committed (na autonomii).
- **Prowenancja zgubiona (B07-E2/C18 eta_source VOID):** `eta_source` (real-route vs fikcja) liczony w 6 site, **0 wystąpień w ledgerze** (oracle C18 ground-truth: 0/4-dni) → z `shadow_decisions` nie wiadomo czy zwycięska ETA = BIALYSTOK_CENTER fiction. (Primary E/R3-Prawda; tu bridge — to samo pole.)
- **instance_refs:** `dispatch_pipeline.py:5287`(write display+decision) · `dispatch_pipeline.py:5174`(extension) · `dispatch_pipeline.py:5610`(hard-reject) · `dispatch_pipeline.py:5877`(post-loop nadpis pre_shift) · `dispatch_pipeline.py:4067`(W3 r07 dead writer → K) · `nadajesz_clone/panel/frontend-shared/src/features/coordinator/Ops13Console.tsx:835` · `telegram_approver.py:872`(display-floor)
- **zwija findingi:** F-1·F-2·F-4·F-6(→K)·F-9·F-10·F1-A·F1-B·F1-C·B02-R5-eta_pickup·LU-1(eta_pickup_min jednostka)·B07-E2-eta-source(bridge E)·C18-eta_source-VOID
- **cel:** rozdzielić `eta_pickup_decision` (surowy, jedyne wejście extension+>60min-reject+target_pickup_at) od `eta_pickup_display` (floored, NIGDY z powrotem do decyzji) — NOWE pole obok (additive, wzorzec #8); jedna komputacja nie dwie; floor-overlay = JEDEN wspólny helper zamiast 6×7 flag; HARD-reject >60min do `check_feasibility_v2` (warstwa HARD) nie verdict-layer; serializuj `eta_source`.
- **why_recurs:** twin-scatter (display-floor narastał per-powierzchnia bo surowy pokazywał „za wcześnie") + N-D (jedno pole = dwie role, świadomy ale niedomknięty).

### ROOT S2 — `coupled-location-fields-async-write` (F · R4 · NOWY pod-root) — P2 OTWARTY ŹRÓDŁO
**Co:** `delivery_coords` (pin) i `delivery_address` (tekst) reprezentują TO SAMO w 2 formach; writer aktualizujący JEDNO bez drugiego = split-brain (HARD-geometria widzi nowy pin, SOFT-district stary tekst). + `delivery_coords` REUŻYTE jako pozycja kuriera → zatrucie propaguje w km/ETA/feasibility następnego ordera.
- `gastro_edit.regeocode_and_update:154/158` pisze coords ZAWSZE, address TYLKO gdy `ENABLE_REGEOCODE_SYNC_TEXT` (flags.json=true LIVE, ale const-default OFF + **leak: poza ETAP4/fingerprint** → usunięcie klucza = cichy rewert asymetrii). `state_machine:822/826` COURIER_DELIVERED tekst-zawsze/pin-warunkowo.
- **Cross-field reuse (F2-C):** `courier_resolver.py:740`/`:1004` `cs.pos=tuple(order["delivery_coords"])`, `pos_source=last_picked_up_delivery` → pole „gdzie dowieźć" = pozycja-kuriera. Most do F1/K5: zatruty `(0,0)`/sentinel pin → zła pozycja.
- **instance_refs:** `gastro_edit.py:154` · `gastro_edit.py:158`(SYNC_TEXT leak) · `state_machine.py:826` · `courier_resolver.py:740` · `feasibility_v2.py:499`(pin→R1 geom) · `same_restaurant_grouper.py:84`(tekst→district)
- **zwija findingi:** F2-A·F2-B·F2-C·B02-W2(haversine 6-kopii guard, bridge M)·B04-F10(twin haversine OK, resztka=ingest coords)
- **cel:** JEDEN writer-kontrakt „pisz parę (coords,address,city) atomowo albo świadomie N-D"; `ENABLE_REGEOCODE_SYNC_TEXT`→ETAP4+fingerprint→retire (zawsze-sync); pozycja-z-delivery przez `_valid`-guard (most do F1).
- **why_recurs:** path-asymmetry (regeocode pisze pin, tekst za flagą-leakiem) + cross-field reuse (jedno pole, dwie role przestrzenne).

### ROOT S3 — `uwagi-field-boundary-loss` (F/F3 · R4 · NOWY pod-root) — P2 OTWARTY ŹRÓDŁO
**Co:** `uwagi` (free-text) niesie 2 osadzone payloady decyzyjne (pickup-adres firmowego konta → coords; deadline czasówki). Persystowane w GŁÓWNEJ ścieżce NEW_ORDER (`state_machine:533-538`, Lekcja #80 naprawiona), ale **DROPOWANE w fallbacku `CorruptedTimestampError` `:495-514`** (brak kluczy `uwagi`/`uwagi_pickup_parsed`/`delivery_deadline_uwagi`) — wzorzec #1 (fix w 1 z N ścieżek) zastosowany do POLA. + parse pickup-z-uwagi TYLKO przy NEW_ORDER (#18 temporalna luka: edycja uwagi nie re-parsuje).
- **instance_refs:** `state_machine.py:533`(happy persist) · `state_machine.py:495`(fallback DROP) · `panel_watcher.py:1210`(parse pickup tylko NEW_ORDER) · `czasowka_uwagi.py:53`(deadline shadow, brak konsumenta decyzyjnego)
- **zwija findingi:** F3-A·F3-B·O8(#18 temporal-reachability, rozwiązana instancja address_mismatch jako wzorzec)
- **cel:** derywaty (`pickup_parsed`/`deadline`) liczone w JEDNYM miejscu które każda ścieżka persist wywołuje (happy∧fallback); sweep utrwalonego stanu zamiast hooka-przy-tworzeniu (#18).
- **why_recurs:** twin-scatter (2 ścieżki persist NEW_ORDER, fix w 1) + edge-patch (hook tylko at-create). Impact dziś NISKI (merge-upsert zachowuje istniejące); latentny P2 gdy deadline dostanie konsumenta decyzyjnego.

### ROOT L1 — `naive-datetime-tz-convention-split` (L · R4 · NOWY root) — P2 OTWARTY (latentna mina, 1 nawrót CONFIRMED) ŹRÓDŁO
**Co:** DWIE przeciwne konwencje dla naive-datetime: warstwa parse/boundary zakłada naive=**Warsaw** (poprawnie dla panelu), warstwa math/HARD-bramki zakłada naive=**UTC** (defensywnie). Wartość Warsaw-naive która OMINIE granicę i dotrze do math-layer = czytana jako UTC = **+2h w HARD-bramce** (feasibility shift-gate / route_simulator R6-anchor). Samodokumentowana mina (`plan_recheck:288` „interpretacja jako UTC = błąd +2h").
- DWA parsery `picked_up_at`: `state_machine:779` (naive→Warsaw, POPRAWNY) vs `plan_recheck._parse_dt` (naive→UTC, +2h). Konsola intra-file: `fleet_state._iso:100` (Warsaw) vs `_parse_ts:219` (UTC) na polach „Warsaw naive".
- **DOWÓD NAWROTU (PATCHED instancja, oracle C12 VALIDATED ground-truth):** checkpoint_tz — 4 miejsca `courier_resolver` parsowały GPS-checkpoint Warsaw-naive jako UTC; `ENABLE_CHECKPOINT_TS_WARSAW_PARSE` LIVE; oracle: age_ON−age_OFF = **DOKŁADNIE +120.0min** (588 checkpointów, ON nigdy age<0). To LN naprawione w 1 miejscu — pozostałe powierzchnie otwarte.
- Fasetki nazewnicze: `czas_kuriera_warsaw` nazwane „warsaw", płynie jako UTC; WARSAW const = 8 nazw (kosmetyka audytu); `event_bus._is_peak_window` aware-UTC now→UTC-hour vs okna Warsaw.
- **instance_refs:** `feasibility_v2.py:749`(shift-gate naive→UTC) · `plan_recheck.py:288`(self-doc +2h) · `state_machine.py:779`(naive→Warsaw poprawny) · `nadajesz_clone/panel/backend/app/integrations/ziomek/fleet_state.py:219`(_parse_ts UTC) · `tools/checkpoint_tz_shadow.py:6`(PATCHED nawrót) · `event_bus.py:107`
- **zwija findingi:** LN-1·LN-2·LN-3·LN-4(patched)·LN-x·L-WARSAW-NAMES·F-B19-09(nonhermetic-clock test, baseline-RED)·C12-checkpoint-tz-VALIDATED
- **cel:** typ/kontrakt „wszystkie czasy aware-UTC od granicy" + `assert no-naive` w math-layer; replikować inwariant `state_machine:61-95` (ISO≡HH:MM) na pary TZ; `_iso`/`_parse_ts` ujednolicić.
- **why_recurs:** twin-scatter (każdy parser re-wybiera konwencję, brak jednego typu/asercji) — checkpoint to DOWÓD nawrotu (klasa wraca w nowym module). Math-guardy dead w normalnym path → czekają na ominięcie granicy.

### ROOT L2 — `tier-token-overload` (L · R4 · NOWY root) — P2 OTWARTY ŹRÓDŁO
**Co:** token `tier` = **4 rozłączne znaczenia** bez glosariusza/single-source: (1) KLASA kuriera gold/std/slow (`feasibility_v2:355`), (2) POZIOM ESKALACJI 1/2/3 (`dispatch_pipeline:737/739` `_esc_tier∈{2,3}`), (3) WYMIAR SOLVERA OR-Tools tier-1/tier-2 (`route_simulator_v2:1260`), (4) TIER GPS (`common:600/611`). Kolizja krytyczna: „tier-3 cap=40" (eskalacja/niedobór) vs `tier=='slow'` (klasa) — DWA różne „tier-3" w sąsiednim kodzie. `_esc_tier=3` serializowany jako `best_effort_objm_esc_tier` OBOK courier-class `tier` → konsument joinujący po „tier" myli eskalację z klasą.
- **instance_refs:** `dispatch_pipeline.py:739`(_esc_tier=3) · `dispatch_pipeline.py:743`(serial obok tier) · `feasibility_v2.py:355`(tier=='gold' klasa) · `route_simulator_v2.py:1260`(solver tier) · `common.py:600`(GPS tier) · `common.py:2657`(cap=40 „tier-3" eskalacja)
- **zwija findingi:** LT-1
- **cel:** rozdzielić nazwy `tier`→{`courier_class`, `escalation_level`, `solver_dim`, `gps_tier`}; glosariusz single-source.
- **why_recurs:** N-D (overload słownictwa, świadomy ale bez kanonicznej definicji — MEMORY/CLAUDE.md już ostrzega „tier=DWIE rzeczy", realnie CZTERY).

### ROOT L3 — `shift-start-midnight-anchor` (L · R4 · fasetka A6 R4, OSOBNE źródło) — P2 OTWARTY ŹRÓDŁO
**Co:** `_shift_start_dt:1264` + `_minutes_to_pre_shift:1246` używają `now.replace(hour,minute)` = ZAWSZE dzisiejsza doba, BEZ obsługi przełomu północy. Asymetria vs bliźniak `_shift_end_dt:1278` który MA `24:00→+1` + komentarz o wczoraj. Zmiana nocna (start 22:00/23:00) odczytana po północy (now=00:30) → DZIŚ 23:00 = ~22h w przyszłości → fałszywy `pre_shift` → błędny clamp/`PRE_SHIFT_TOO_EARLY` HARD-reject. Realne (grafik pt/sb do 24:00, GRF-02).
- **instance_refs:** `courier_resolver.py:1264`(_shift_start_dt) · `courier_resolver.py:1246`(_minutes_to_pre_shift) · `courier_resolver.py:1278`(_shift_end_dt MA +1 — kontrast)
- **zwija findingi:** LM-1
- **cel:** `_shift_start_dt` symetryczny do `_shift_end_dt` (obsługa północy).
- **why_recurs:** twin-scatter (bliźniaki start/end rozjechane — end naprawiony, start nie). DISTINCT od A6 R4 floor (to date-anchoring HH:MM→datetime, nie brak-floor).

### ROOT L4 — `lexical-naming-units-rot` (L · R4 · grab-bag P3) — P3 OTWARTY ŹRÓDŁO
**Co:** rozsyp niejednoznacznego słownictwa/jednostek (audit-friction → latentne miny przy positive-matcherach):
- **enum `order_type` PL/EN (LE):** prod pisze „elastic" (EN); `czasowka_uwagi_oracle:153` positive-match `=="elastic"`; fixtury+`test_czasowka_dispatchable_fleet_fix` używają „elastyk" (PL). Maskowane live negacją (`!="czasowka"`), ale każdy positive-matcher = cichy miscount.
- **jednostka w prefiksie (LU):** `eta_pickup_min`(minuty-od-teraz) vs `_utc`(absolut) vs `_hhmm`(display) — ten sam prefiks; `czas_odbioru`(int min) vs `_timestamp`(datetime); `pickup_at`-rodzina ≥7 nazw bez sufiksu-TZ.
- **dual-60min:** `CZASOWKA_THRESHOLD_MIN=60` vs `EARLY_BIRD_THRESHOLD_MIN=60` (ta sama liczba, różne reguły; early-bird przekwalifikowane lekcja #196).
- **instance_refs:** `panel_client.py:692`(elastic) · `tools/czasowka_uwagi_oracle.py:153`(positive-match) · `common.py:3613`(eta_pickup_min jednostka) · `shadow_dispatcher.py:1098`(pickup_at ≥7 nazw) · `auto_koord.py:32`(dual-60)
- **zwija findingi:** LE-1·LU-1(∩S1)·LU-3·L-dual-60min
- **cel:** ujednolicić `order_type` enum (jeden język); WYMUSZONA konwencja sufiksu jednostki `_min`/`_utc`/`_hhmm`/`_timestamp`.
- **why_recurs:** N-D (rozsyp nazewniczy, dziś benign przez negację-pattern; mina przy każdym przyszłym positive-matcherze).

### — PODKLASTER R5 STRES/AWARIA (M·G·O) —

### ROOT F1 — `coord-sentinel-no-ingest-chokepoint` (M · R5 · K5 most) — P1 OTWARTY ŹRÓDŁO
**Co:** Sentinel `(0,0)` (null-island) PRODUKOWANY w warstwie danych BEZ fail-loud i BEZ walidacji u ingest; istnieje JEDEN kompletny walidator `common.coords_in_bialystok_bbox:513` (odrzuca None/NaN/(0,0)/poza-bbox) — **NIGDZIE u granicy INGEST**. Defensa = N łatek post-hoc (każda jedno call-site). Dwa ujścia tej samej trucizny (klasa B w M): `haversine()` RAISE → catch-all `_v328_eval_safe` → **ZAJĘTY kurier znika z puli** vs `osrm.route/table` → sentinel 9999min → leg infeasible → **kurier cicho wycięty**.
- **ŻYWY DOWÓD (B15, logi dziś):** 2046× `V328_CP_SOLVER_FAIL` + 14456× `COORD_GUARD`; **8 distinct ofiar 30.06** (cid=179×5, cid=492 Jakub W×3, smoking-gun `ll1=(0,0), ll2=real_pickup` = sygnatura `:4823`). Brak alertu/KOORD — jedyny ślad = ERROR w logu.
- **Punkty:** produkcja `dispatch_pipeline:3133-3135`(bag fallback `or (0,0)`)+`:3470`(new-order delivery); konsument-raise `:4823`(V326_WAVE_VETO truthy-guard `if _last_drop:` NIE łapie (0,0)) + `:2149`(repo_cost — kara dead-headu CICHO znika → kandydat TAŃSZY); catch-all `:5695`(`except Exception`→cichy drop, NIE odróżnia data-poison/niefeasible/PRAWDZIWY-bug NameError); ingest `gps_server:328`(range-check (0,0) PRZECHODZI); placeholder PERSYSTOWANY `panel_watcher._save_plan_on_assign:474/486/496` `{lat:0,lng:0}`→`courier_plans.json` (live: 11/79 stopów = (0,0)); 6 NIESPÓJNYCH definicji „czy coord sentinel" (`common:513` kompletny vs exact-(0,0) vs lat-alone vs range vs centroid-tol).
- **Cichy fail w renderze/masce:** firmowe FALLBACK_COORDS maskuje fail geokodu (M12); `feed._load_*_fresh except→{}` overlay znika cicho (M9); pos replay-label „gps" dla store-pos ≤25min (M11); BIALYSTOK_CENTER fikcja+kolizja (legalna pozycja ∧ trucizna-centroid) + 4 dup-stałe (M10).
- **instance_refs:** `dispatch_pipeline.py:4823`(wave_veto haversine (0,0)) · `dispatch_pipeline.py:5695`(_v328 catch-all) · `dispatch_pipeline.py:3133`(produkcja) · `dispatch_pipeline.py:2149`(repo_cost optymizm) · `osrm_client.py:570`(twin osrm sentinel 9999) · `common.py:513`(kompletny walidator NIE u ingest) · `panel_watcher.py:474`(placeholder persist) · `gps_server.py:328`(ingest range-check przepuszcza)
- **zwija findingi:** M-1·M-2·M-3·M-4·M-5·M-6·M-7(magic-score -1e9, osobny anty-wzorzec, mostly-mitigated Z-18)·B16-M1/M2/M3/M6/M10/M12·B16-M06(6 defs)·B02-W2(haversine guard)·B04-F10(twin OK, resztka ingest)
- **cel:** wpiąć `coords_in_bialystok_bbox` u KAŻDEGO ingest (gps_server, state_machine) = jeden chokepoint; truthy-guard `if coords:`→`_valid(coords)` we WSZYSTKICH bezguardowych callerach RAZEM (`:4823`+`:2149`+osrm); catch-all `:5695` rozróżnia data-poison (operator-visible alert) vs realny-bug (NIE połykać); domknąć u źródła geokodu (122 zatrute adresy, geocode-centroid guard).
- **why_recurs:** twin-scatter (defensa = N łatek post-hoc zamiast jednego walidatora u ingest → „(0,0) wraca", residual w komentarzu `:3939`) + path-asymmetry (haversine raise vs osrm sentinel = niespójna obsługa tej samej danej). **Most:** zasila A6 gr.3b (`_SYNTH_POS` sentinel-klasyfikator), gr.6 (BIALYSTOK fiction floor), P0-A (selekcja optymistyczna bo repo-km=None), P0-B (pula kurczy się bo couriers znikają).

### ROOT F2 — `schedule-fail-open-vs-fail-close-asymmetry` (M/K4 · R5 · fasetka floor) — P1 OTWARTY ŹRÓDŁO
**Co:** TE SAME zepsute dane grafiku (literówka „11.00" zamiast „11:00", pusta godzina, fetch 06:00→cała flota bez grafiku 00:00-06:00) dają **3 sprzeczne traktowania**: `is_on_shift` fail-OPEN CICHO (`return True` „na zmianie 24/7", `:376/383/392/401`, ZERO log.warning) ‖ `_shift_start_dt`/`_shift_end_dt` fail-CLOSE→None (floor `max(now,shift_start)` = no-op) ‖ feasibility FAIL12 fail-open-LUB-close GŁOŚNO (`log.warning` „SPRAWDŹ GRAFIK"). Poprawny wzorzec ISTNIEJE w tym samym systemie (FAIL12 „Z2 anti-silent-failure"), `is_on_shift` go NIE stosuje.
- **Skutek literówki „11.00":** kurier liczony on-shift (brak demote/warm-up) + floor martwy NA ZAWSZE cicho + feasibility próbuje NO_ACTIVE_SHIFT. 3 traktowania jednego defektu = I-konflikt + M-cisza.
- **instance_refs:** `/root/.openclaw/workspace/scripts/schedule_utils.py:376`(fail-open brak grafiku) · `schedule_utils.py:401`(except ValueError „11.00"→True) · `courier_resolver.py:1264`(_shift_start_dt fail-close None) · `feasibility_v2.py:701`(FAIL12 GŁOŚNO — wzorzec dobry)
- **zwija findingi:** B16-M4(is_on_shift fail-open)·B16-M5(dt-helpers≠is_on_shift)
- **cel:** `is_on_shift` z głośnym log.warning (jak FAIL12) + walidacja wpisów grafiku U ŹRÓDŁA (arkusz Google); jeden kontrakt fail-policy dla zepsutego wpisu.
- **why_recurs:** path-asymmetry (3 konsumenci tego samego defektu, każdy własna polityka; poprawny głośny wzorzec niereplikowany). Most do A6 R4 floor (warstwa #2 „is_on_shift fail-open") + L3 (shift_start) — NIE liczyć podwójnie z floor-agentem.

### ROOT F3 — `calibration-on-wrong-axis` (G · R5 · K3 optymistyczny-poślizg) — P1 OTWARTY ŹRÓDŁO
**Co:** Wszystkie ŻYWE kalibracje SKRACAJĄ ETA na osi o niskim/zerowym błędzie albo selekcyjnie-zatrutej → LUZUJĄ sufit R6/feasibility; wszystkie kalibracje na FIZYCZNEJ osi błędu są OFF/shadow/pod-wymiarowane. Net: silnik systematycznie OPTYMISTYCZNY na R6 (jedzenie siedzi DŁUŻEJ niż liczy), a jedyne żywe strojenie pogłębia optymizm.
- **LUZUJE (żywe, zła oś):** G-1 `ENABLE_ETA_QUANTILE_R6_BAGCAP=True` LIVE (`feasibility_v2:1089`) luzuje HARD-R6 gold≤4 mapą kwantylową delivery-ETA z PRÓBY SELEKCYJNEJ (matched_courier only — generator sam ostrzega `eta_quantile_calib:30`); 32,4% R6-rejectów `would_pass_calibrated`. G-3 `DRIVE_SPEED_MULT_BY_TIER<1.0` (gold 0.78) parked-landmine na nodze JAZDY (~0 błędu, 29.06) — flip = −18..22% nogi → R6 luźniejszy.
- **REAL oś OFF/pod-wymiar:** G-4 poślizg ODBIORU `pickup_slip_monitor` +18..+27 (oracle C13 VALIDATED: znak DODATNI=optymistyczny POTWIERDZONY) vs `PICKUP_DEBIAS_MIN=4.5` (pod-wymiar 4-6×, SHADOW only) + `drive_min_calibration` OFFSET assign→pickup +13..+35 (OFF main). G-5 `ENABLE_PREP_BIAS_TABLE` OFF — prep-bias +11..+13 (n=25912) NIE-skorygowany (+ dwie mapy prep: feasibility czyta ANTYK 20.06).
- **Przyrząd nie mierzy (G-2, E-class):** `r6_gold4_gate_recovered` (`:1098`) ustawiany LIVE ale **0 w serializerze i 0 w `shadow_decisions`** → luzowanie R6 niemierzalne; anomalia 66 gold≤4 would_pass jeszcze-rejected (wymaga oracle C).
- **instance_refs:** `feasibility_v2.py:1089`(G-1 LIVE luzuje R6) · `feasibility_v2.py:1098`(G-2 gate niewidoczny) · `common.py:2188`(G-3 drive_speed parked) · `common.py:3131`(G-4 PICKUP_DEBIAS 4.5 shadow) · `drive_min_calibration.py:52`(G-4 prawdziwa oś OFF) · `common.py:2061`(G-5 PREP_BIAS OFF)
- **zwija findingi:** G-1·G-2·G-3·G-4·G-5·G-6(repo_cost poza-G, dystans nie ETA)·C13-pickup_slip-VALIDATED·C07-drive_speed-VALIDATED(flaga-OFF→N/A)
- **cel:** kalibrować oś poślizgu-odbioru+prep (load-aware bufor ETA, review 04.07) zamiast skracać jazdę/delivery; G-1 bagcap + SLA-anchor + prep-bias rozstrzygnąć ŁĄCZNIE w O2 02.07 (inaczej luzowanie i pod-korekta pracują przeciw sobie); serializować `r6_gold4_gate_recovered`.
- **why_recurs:** N-D (kanoniczny K3 — żywe strojenie celuje w oś bez błędu / selekcyjnie-zatrutą; oś realnego błędu parked). Co-design z R3/SLA-anchor (E_dedup_1 ROOT5) — NIE ten sam root, ale luzuje tę samą bramkę.

### ROOT F4 — `shared-state-no-lock-rmw` (O · R5 · rodzina B) — P2 OTWARTY ŹRÓDŁO
**Co:** Stan współdzielony pisany przez ≥2 procesy przez `os.replace` BEZ fcntl → brak torn-read, ale **lost-update RMW NIEzabezpieczony** (A czyta `{x}`, B czyta `{x}`, A pisze `{x,a}`, B pisze `{x,b}` → `a` zgubione). Mityacje ad-hoc per plik (merge-by-ts / latest-wins / single-writer / „Telegram muted") zamiast JEDNEJ dyscypliny (fcntl LOCK_EX jak `plan_manager`/`state_machine` MAJĄ).
- O1 `pending_proposals.json` ≥2 RMW (store-shadow + postpone-timer LIVE lost-update; store+telegram współdzielą STAŁY `{path}.tmp` → re-enable Telegrama = uzbrojenie tmp-clobber BEZ zmiany kodu, C2-mina). O3 `courier_last_pos.json` (docstring KŁAMIE „multi-proces safe" = E lying-comment; merge-by-ts zawęża, nie eliminuje; zasila no_gps rescue). O4 `live_eta_cache.json` (shadow+plan_recheck). O10 `global_alloc.json` (STAŁY tmp + feed fail-soft `{}` = M silent-vanish).
- **instance_refs:** `pending_proposals_store.py:46`(STAŁY tmp) · `telegram_approver.py:1761`(współdzielony tmp) · `courier_resolver.py:196`(last_pos RMW, docstring `:172` kłamie) · `live_eta_cache.py:121` · `global_alloc_store.py:35`
- **zwija findingi:** O1·O3·O4·O10
- **cel:** JEDNA dyscyplina — fcntl LOCK_EX wrapper (jak plan_manager/state_machine) zamiast 4 ad-hoc mityacji; UNIKALNY tmp wszędzie; naprawić lying-docstring O3.
- **why_recurs:** twin-scatter (rozsyp mityacji zamiast jednej dyscypliny) + „safe only because muted" (postura nie kod). Self-healing (następny tick re-zapisuje) → niska zmierzona częstość, strukturalnie otwarte.

### ROOT F5 — `cookiejar-threadpool-shared-session` (O/C · R5 · known-landmine re-introduced) — P2 OTWARTY ŹRÓDŁO
**Co:** Współdzielony `opener`+CookieJar czytany w `_open_with_relogin:472` i `opener.open()` BEZ `_session_lock`, wołany z `ThreadPoolExecutor` (`dispatch_pipeline:427` pre-proposal recheck, N wątków na WSPÓLNYM openerze). 419/401 → `login(force=True)` podmienia opener w locie pod lockiem, ale inne wątki trzymają STARĄ referencję → wyścig cookies + kaskada 419. **Łamie własną regułę NIGDY** (CLAUDE.md „edit-zamowienie sekwencyjnie, nie ThreadPoolExecutor"). Bezpieczny fix ISTNIEJE (`panel_detail_prefetch:53` per-wątek opener) — niepodpięty na ścieżce recheck.
- **instance_refs:** `panel_client.py:472`(opener bez locka) · `dispatch_pipeline.py:427`(ThreadPoolExecutor recheck) · `panel_client.py:551`(fetch_order_details bez locka) · `panel_detail_prefetch.py:53`(bezpieczny wzorzec niepodpięty)
- **zwija findingi:** O5
- **cel:** przepiąć recheck na wzorzec `panel_detail_prefetch` (per-wątek opener+CookieJar) lub `_session_lock` na `.open()`.
- **why_recurs:** edge-patch (reguła NIGDY re-wprowadzona na nowej ścieżce; fix istnieje gdzie indziej, niezastosowany). Tolerowane od IV (1-retry 419 per-wątek self-healing).

### — PODKLASTER R6 CYKL-ŻYCIA/ZGNILIZNA (H·K) —

### ROOT R6a — `courier-plans-lifecycle` (H+K+D+O · R6 · K2 plan_recheck=cofacz) — P2 OTWARTY ŹRÓDŁO
**Co:** Pełny cykl życia `courier_plans.json` dziurawy: (1) GC napisany-niepodpięty; (2) read-with-side-effect; (3) recanon nie potrafi prune; (4) 2 timery last-writer-wins; (5) reconcile-lag karmi phantom.
- **H1 GC orphan:** `plan_manager.gc_invalidated:501` „Manual/cron hook — no auto-schedule", ZERO produkcyjnych callerów/timera → **33/47 wpisów = zombie** (najstarszy cid 414 @ 2026-04-28, >2 mies.); docstring `:214` „GC-able" = obietnica niespełniona (+ dead-function = K).
- **H2 read-side-effect:** `load_plan:121` default `invalidate_on_mismatch=True` PERSYSTUJE `invalidate_plan(ORDER_DELIVERED_ALL)` podczas READ; preview-reader (bag kandydata) ściga się z `advance_plan` (TOCTOU) → spurious DRZE plan → oscylacja carried-first (Jakub W/Piotr K 29.06). Mitygowane `ENABLE_LOAD_PLAN_PURE_READ` przy 2 callerach, **default param wciąż True** = opt-out (nowy caller = re-uzbrojenie).
- **H3 recanon nie-prune (K2):** `plan_recheck:1832` subset-gate `set(oids)<=covered` przepuszcza skurczony worek (delivered stop wciąż w `covered`); `_retime_stops` retimuje WSZYSTKIE bez reconcile statusu → phantom dropoff przeczasowany ALBO retime-abort — żaden wariant NIE prune; prune wyłącznie surgical-event (advance/mark_picked_up/remove). Upstream: `panel_watcher MAX_RECONCILE_PER_CYCLE=25` throttle < backlog.
- **H1b dead-end:** plan invalidated + nowe zlecenia + `_start_anchor=None` → `_gen_one_bag_plan` pomija → „tkwi invalidated"; mitygowane drop-in `ENABLE_GPS_FREE_ANCHOR_LAST_POS=1`, kod default OFF.
- **O7 multi-timer:** plan-recheck (5min) + panel-watcher (event) piszą przez `save_plan` (LOCK_EX zero-korupcji) ale `expected_version=None` → last-writer-wins; RÓŻNY env efektywny (panel-watcher bez SEQUENCE_LOCK/COMMITTED_PROPAGATION) → mogą policzyć INNY kanon. O6 reconcile-lag 15-90 **MIN** karmi phantom (rodzina V3.13/14/15).
- **Oracle C15 VOID:** `carried_first_guard` strażnik biegnie z PUSTYM env systemd → reużyte funkcje silnika czytają 14 flag env-only jako default-OFF → N-procesów=N-konfiguracji (CLEAN nieodróżnialny od disabled).
- **instance_refs:** `plan_manager.py:501`(gc_invalidated orphan) · `plan_manager.py:157`(load_plan side-effect default True) · `plan_recheck.py:1832`(recanon subset-gate nie-prune) · `plan_recheck.py:352`(H1b dead-end) · `plan_recheck.py:1838`(O7 timer save) · `panel_watcher.py:1876`(reconcile throttle 25) · `tools/carried_first_guard.py:5`(strażnik VOID empty-env)
- **zwija findingi:** H1·H1b·H2·H3·O2·O6·O7·panel_watcher-throttle·C15-carried_guard-VOID
- **cel:** podpiąć `gc_invalidated` do janitora (run_recheck/prune-timer) LUB usunąć martwą funkcję; odwrócić `load_plan` default na pure-read (opt-IN side-effect, fix U ŹRÓDŁA nie per-caller); recanon/retime z reconcile-statusu (prune delivered/cancelled, nie tylko surgical-event); `expected_version` CAS między timerami + parytet env.
- **why_recurs:** GC napisany-ale-niepodpięty (intencja↔rzeczywistość) + prune-tylko-surgical (lag→phantom) + read-side-effect-default-opt-out + path-asymmetry (2 timery różny env). 2/3 mitygowane flagami, defaulty NIE utwardzone.

### ROOT R6b — `unbounded-append-only-caches` (H+N · R6) — P3 OTWARTY ŹRÓDŁO
**Co:** 6 append-only cache/telemetry stores z ZERO eviction (vs 5 stanów ZDROWYCH z podpiętym GC): `geocode_cache.json` (3.2MB), `customer_dwell.json` (2.2MB), `address_pin_index.json` (1.3MB), `delivery_town_cache.json` (64KB), `courier_ground_truth.json` fakty-GPS (KEPT FOREVER, `ground_truth_gc` prune tylko status-only), `events.db` (30.5MB managed ale brak VACUUM? — DELETE bez zmniejszenia pliku).
- **instance_refs:** `geocoding.py`(geocode_cache append-only) · `observability/ground_truth_gc.py:41`(prune tylko status-only, fakty kept) · `dispatch_state/customer_dwell.json` · `dispatch_state/address_pin_index.json` · `dispatch_state/delivery_town_cache.json`
- **zwija findingi:** B10-§6 (6 cache bez eviction)
- **cel:** age-bound/LRU/cap per store; VACUUM dla events.db.
- **why_recurs:** N (rozsyp — różne stany, wspólny anty-wzorzec brak-bound). Wolny wzrost, niskie ryzyko korupcji, ale prawdziwie nieograniczone (geocode/dwell w hot-path → rozmiar=koszt I/O).

### ROOT R6c-1 — `dead-decision-code-misleads-and-arms-mines` (K · R6 · martwy-w-ścieżce-decyzji) — P2 OTWARTY ŹRÓDŁO
**Co:** Martwy/zneutralizowany kod W ŚCIEŻCE DECYZJI: szkoda odroczona/poznawcza — (a) C2-mina (flip flagi uzbraja 2-mies. nietestowany kod w gorącej ścieżce), (b) myli root-cause (zombie-reguła wygląda na żywą), (c) kłamiący komentarz. Pod-rodziny:
- **Skeletony Sprint-C F2.2 (nigdy nieaktywowane):** `commitment_emitter:82` (C6, ENABLE_MID_TRIP_PICKUP literał False), `pending_queue_provider` (C7, dispatch_pipeline:3372 dead-on-arrival), `speed_tier_tracker:904` (C4 + MYLĄCY komentarz „nightly producer" → INNY tool jest producentem).
- **Migracja „C3-deprecate-legacy" nigdy nieodpalona (K1+K2 = JEDEN root):** `r6_soft_penalty_c3_legacy` (`scoring:200/228` martwy kwarg+gałąź + `feasibility:1129` producent `-3/min` którego nikt nie czyta + `DEPRECATE_LEGACY_HARD_GATES=False` na zawsze + metryka `r6_soft_penalty_applied` ZAWSZE 0 = „kłamie 0") + R7 long-haul `LONG_HAUL_DISTANCE_KM=99` (reject fizycznie nieosiągalny, „myli że HARD-geometria żyje" → most P0-A).
- **Retired/superseded-nieusunięte:** F1.8e legacy else (`:5890`, V324A ON maskuje), B3 wait-gradient (`scoring:95`, env-frozen OFF, sentinel -1000 żywy), soon_free substitution (flags.json false), carry_chain (34d stalled OFF), W3 r07-chain-eta writer (`:4067` OFF, MEMORY ANULOWANE).
- **Kłamiący komentarz (K7, near-miss-generator):** `route_simulator_v2:139` „ENABLE_O2_READY_ANCHOR_SWEEP **ON**" gdy effective OFF → sesja planująca flip 02.07 może uwierzyć że żyje. Most do C9 (przyrząd-werdykt) + L (słownictwo).
- **Dead-but-ON flagi:** `ENABLE_PANEL_IS_FREE_AUTHORITATIVE` env-ON ZERO konsumentów (mylące); `ENABLE_CLUSTER_DROP_GROUPING_METRIC` declared-not-wired.
- **instance_refs:** `scoring.py:200`(r6_soft_c3_legacy martwy) · `feasibility_v2.py:1129`(producent 0) · `common.py:912`(DEPRECATE const False) · `feasibility_v2.py:486`(R7=99km zombie) · `route_simulator_v2.py:139`(komentarz kłamie ON) · `commitment_emitter.py:82`(skeleton) · `dispatch_pipeline.py:4067`(r07 dead writer) · `dispatch_pipeline.py:5890`(F1.8e legacy else)
- **zwija findingi:** K1·K2·K3·K4·K5·K6·K7·B13-K01/02/03/04/05·F-6·D1-2(panel_is_free)·D1-1(cluster_drop)·M1/M2(BUG2_GAP/WAVE_VETO_NEW minory)
- **cel:** usunąć migrację C3 (kwarg+gałąź+producent+stała, dowód bajt-identyczności); usunąć R7-zombie LUB reaktywować jako soft-geom; skeletony za flagą = oznaczyć „flip=full deploy nietestowanego"; naprawić kłamiący komentarz O2 (most C9 flip 02.07).
- **why_recurs:** N-D (martwy kod nie biega → nie produkuje błędu live; szkoda odroczona — C2-mina na flipie + mylenie root-cause). „C3-deprecate" = jedna niedokończona migracja w 4 site (NIE 4 chaosy).

### ROOT R6c-2 — `repo-clutter-retired-not-removed` (K · R6 · entropia peryferii) — P3 OTWARTY ŹRÓDŁO
**Co:** Czysty clutter/cleanup-dług w periferii (zero żywego wpływu na decyzję, ale zatruwa grep audytu + twin-graf):
- **`.bak` graveyard 326+** (dispatch top-level 176 + tools 37 + courier_api 41 + panel 72, Apr11→Jun30; polityka 24h-retencji MARTWA, top-level 176 nie było w żadnym A-dok) + 4 systemd drop-in `.bak`.
- **~45-50 orphan tools** (date-stamped sprint-jednorazówki + martwe probe/replay; anty-double-count: z 64 zero-ref **11 to DORMANT-INSTRUMENT** = NIE martwe, → A4/E) + `epaka_fetcher.py` (obcy projekt, misplaced).
- **shift_notifications RETIRED — potrójny grób:** worker 886L w drzewie + nested in-repo systemd/ + /etc retired×2 + ORPHAN drop-in dir bez unitu.
- **DEAD cross-repo fork:** `courier_api_panelsync/courier_orders.py:558` (665L, build_view nie-serwowany) = **DEAD member route-order R2** (E_dedup_1 ROOT1) — usunąć przy PoC, NIE równać jako 5. żywą kopię.
- **Martwe config keys:** `A4_TEST_FLAG` (test-leak do prod), `commitment_level` (tylko dead emitter, typ-mismatch).
- **Granica K↔E (NIE czyścić jako K):** `post_shift_overrun_forward_replay` orphan + … — ⚠ C18 oracle KORYGUJE: post_shift_overrun_forward_replay NIE jest void w świeżym stanie (klucz 1699/438 serializowany) — audyt alokacji deklarował void na podstawie nieświeżej analizy. Dormant-instrumenty mają rolę → Faza C/E, nie cleanup K.
- **Żywy objaw (M):** `dispatch-cod-weekly.service` FAILED (gspread env) — periferia, NIE martwy-kod sensu stricto.
- **instance_refs:** `courier_api_panelsync/courier_orders.py:558`(DEAD fork) · `shift_notifications/worker.py:1`(retired grób) · `flags.json:72`(A4_TEST_FLAG) · `tools/epaka_fetcher.py:1`(misplaced) · `tools/post_shift_overrun_forward_replay.py:1`(K↔E granica)
- **zwija findingi:** K-06·K-07·K-08·K-09·K-10·K-11·K-12·B13-K-13(cod-weekly→M)·B13-K-14(void-orphan→E)
- **cel:** czyszczenie po GO (326 .bak / shift-notify grób / drop-in .bak / epaka misplaced / cod-weekly fix vs kill / usunąć panelsync fork przy route-order PoC); dormant-instrumenty NIE ruszać jako martwe.
- **why_recurs:** N-D (polityka retencji nieegzekwowana; retired-nieusunięte; cross-project contamination). Niskie ryzyko, wysoka redukcja entropii grepów.

---

## MERGED COUNT

**~95 surowych instancji** (FAZA B: B08/B21 F · B09 G · B10 H · B12/B13 K · B14 L · B15/B16 M · B18 O + werdykty C12/C13/C15/C18/C19 + korelacje D) → **16 distinct rootów** w 3 podklastrach:

- **R4 Semantyka (7):** S1 eta_pickup-one-field-two-roles · S2 coupled-location-fields · S3 uwagi-boundary · L1 naive-tz-split · L2 tier-overload · L3 shift-start-midnight · L4 lexical-naming-rot.
- **R5 Stres/awaria (5):** F1 coord-sentinel-no-ingest-chokepoint (K5) · F2 schedule-fail-open-asymmetry · F3 calibration-wrong-axis (K3) · F4 shared-state-no-lock-rmw · F5 cookiejar-threadpool.
- **R6 Cykl-życia (4):** R6a courier-plans-lifecycle (K2) · R6b unbounded-caches · R6c-1 dead-decision-code · R6c-2 repo-clutter.

**Anty-double-count (kluczowe zwinięcia):**
- **K5 sentinele = 1 root F1** (M-1..M-7 + B16 M1-M12 + 6-defs = jeden brak-chokepointu, NIE 13 chaosów). M-magic-score `-1e9` = osobny anty-wzorzec mostly-mitigated (sub-nota F1).
- **K (35, największa) = 2 rooty wg ryzyka** (R6c-1 martwy-w-decyzji / R6c-2 clutter); „C3-deprecate-legacy" (K1+K2) = JEDNA migracja w 4 site. panelsync = DEAD member R2 (cross-ref E_dedup_1 ROOT1, NIE żywa 5. kopia).
- **F = 3 distinct pola** (eta_pickup/coords-address/uwagi), NIE jeden „chaos F".
- **O „os.replace bez fcntl" = 1 root F4** (O1/O3/O4/O10); O2/O6/O7 → R6a (plan-lifecycle), NIE concurrency.
- **L naive-tz = 1 root L1** (LN-1/2/3/4 + checkpoint patched = dowód nawrotu, NIE 6 chaosów); tier/shift_start/enum = osobne źródła.

**Cross-cluster (NIE rooty tu, jawne odesłania):**
- **Floor pickup≥shift_start** (17 powierzchni) = E_dedup_1 ROOT6; tu fasetki F2(is_on_shift)/L3(midnight)/H1b(_start_anchor).
- **SLA/R6-anchor** (2 inline-mirror) = E_dedup_1 ROOT5; F3 (kalibracja) LUZUJE tę bramkę — co-design O2 02.07.
- **eta_source prowenancja** = E/R3-Prawda (bridge w S1); position-equality no_gps/pre_shift = E_dedup_1 ROOT4 (most z F1 K5 + S1 synthetic-eta).
- **route-order cross-repo** (panelsync DEAD member) = E_dedup_1 ROOT1.

**Oracle-walidacja (Faza C, dla mojego klastra):** VALIDATED — checkpoint_tz (L1, +120min exact), pickup_slip (F3, znak DODATNI=optymistyczny), drive_speed (F3, flaga-OFF→N/A), gps_delivery (F3 fundament), eta_source compute-but-vanish (S1, 0/4-dni). VOID — carried_first_guard (R6a, empty-env N-procesów=N-konfiguracji), conftest-leak-fix (R6c-1, łatka-3-instancje nie fix-u-źródła, 62 survivors).
