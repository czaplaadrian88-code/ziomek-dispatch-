# CLAUDE.md — Ziomek Dispatcher (V3.14 Bag integrity TTL fix)

## Changelog

### V3.14 (2026-04-19 późny wieczór) — Bag integrity / stale cache fix
- **Bug 15:17 Warsaw**: propozycja #467117 Baanko pokazała Michała Rom z 3-order bagiem (Arsenal Panteon, Trzy Po Trzy, Paradiso) — wszystkie delivered w panelu 1-3h wcześniej. Real panel bag = {467099 Mama Thai, 467108 Raj}.
- **Root cause**: `panel_watcher.reconcile` ma lag 15-90 min (`MAX_RECONCILE_PER_CYCLE=25/tick × 20s` + FIFO closed_ids). Pipeline ufał `orders_state.status=assigned` bez TTL guard.
- **Shadow impact**: 36.3% propozycji last-4h miały phantom w BEST bag_context, 83.7% w jakimkolwiek kandydacie. 613 phantom entries / 4h. Top: #467009 Chicago Pizza 52×, Gabriel cid=179 z 160× phantom entries.
- **Fix** (3 commits + 4 tagów, master `f22-bag-integrity-live`):
  - `e3065fd` common — flag `STRICT_BAG_RECONCILIATION=True` (default) + `BAG_STALE_THRESHOLD_MIN=90` + env overrides
  - `487ba9c` courier_resolver — `_bag_not_stale()` helper + filter w `build_fleet_snapshot:218`; V3.13 test fixture `_mock_state` patched (assigned_at=now-10min) bo stary hardcoded 12:00 nie przechodził TTL
  - `d3d3409` tests — `test_bag_contents_integrity.py` 25/25 PASS (12 sections)
- **Reguła TTL**: `status=assigned + updated_at >90 min + brak picked_up_at → STALE` (wykluczony z bag). `status=picked_up + picked_up_at >90 min bez delivered` również stale. Czasówka z `pickup_at_warsaw` w przyszłości zachowana (legitymnie assigned). Brak timestamp → defensywnie zachowany.
- **Regresja**: 204/204 baseline (137 legacy + 16 city + 26 availability + 25 bag). Zero konfliktu z V3.13 (L211-234) bo V3.14 na L218 — ortogonalne.
- **Live post-deploy**: Michał Rom bag 3→1 (Paradiso 467070 z 12:09 UTC wykluczony jako stale 100+ min). Fleet total 44→27 (17 phantoms filtered).
- **Pending LIVE**: restart `dispatch-panel-watcher` + `dispatch-shadow` (ACK). `dispatch-telegram` nie wymaga.
- **Deferred**: (a) panel_watcher proactive reconcile (event webhook / priority queue), (b) orphan order 467070 Paradiso wciąż assigned w state 3h po — TTL chwilowo pokrywa ale source osobno.

### V3.13 (2026-04-19 wieczór) — Availability / PIN-space bug fix
- **Bug produkcyjny 14:00-14:08**: 8 propozycji #467070-#467077 pokazały identyczną trójkę "wolnych" kandydatów (Michał Ro cid=5333-PIN, Aleksander G, Gabriel J) mimo że panel pokazywał każdego z 2-3 orderami w bagach.
- **Root cause**: `courier_resolver.build_fleet_snapshot:214` zawierał `piny.keys()` w `all_kids` — PIN-y 4-cyfrowe z `kurier_piny.json` (Courier App logins) dodawane jako osobni kurierzy obok prawdziwych `courier_id` z `kurier_ids.json`. Michał Ro istniał jako cid=518 (prawdziwy, z bagiem) i cid=5333 (PIN, pusty → no_gps). Telegram propozycje wysyłały `chosen_courier_id=5333` (fałszywy ID), koordynator musiał ręcznie przypisywać pod 518.
- **Shadow impact**: 46% propozycji w ostatnich 4h miało PHANTOM PIN jako best, 61% w 24h, 48% all-time (1140 decisions). Top phantom PINs: Szymon P 107×, Andrei K 100×, Mateusz O 84×, Michał Ro 57×.
- **Fix** (3 commits + 4 tagów, master `f22-strict-bag-awareness-live`):
  - `1678d1f` common — flag `STRICT_COURIER_ID_SPACE=True` (default) + env override `STRICT_COURIER_ID_SPACE=0`
  - `32be76a` courier_resolver — exclude `piny.keys()` z `all_kids` gdy flag True; PIN pozostaje name-lookup fallback (L227-231)
  - `9b3e27f` tests — `test_panel_aware_availability.py` 26/26 PASS (fixture #467070-#467077, mock-based)
- **Regresja**: 153/153 baseline clean tests bez zmian (137 legacy + 16 city). Plus 26/26 nowych. Pre-existing failures (gspread, NameError itd.) niezmienione.
- **Pending LIVE**: restart `dispatch-panel-watcher` + `dispatch-shadow` (wymaga ACK). `dispatch-telegram` NIE wymaga (nie woła build_fleet_snapshot).
- **Secondary issues deferred**: (a) panel_watcher lag przy burst-assign (wymaga event webhook refactor), (b) no_gps tie-break degeneracja (BIALYSTOK_CENTER = identyczny km_to_pickup dla wszystkich), (c) PIN 9279 (Michał K.) leaked w courier_names.json.

### V3.12 (2026-04-19 południe) — City-Aware Geocoding Fix
- **Bug produkcyjny** (~10:53 Warsaw): #466975 Chicago Pizza→Kleosin fałszywie zbundlowane z #466978 Retrospekcja→Białystok jako "po drodze 0.3km" — realny dystans 5.33km. Michał Rom dostał top score 125.79 przez fałszywy `bundle_level2_dist=1.15km` od błędnie zgeokodowanego klienta Chicago Pizza.
- **Root cause 3-warstwowy**: (1) `panel_client.normalize_order` nie parsował miasta klienta (pole w panelu: `lokalizacja.name`, FK przez `id_location_to`), (2) `geocoding.geocode(addr, hint_city='Białystok')` hardcoded default, (3) `_normalize` dokleił `, białystok` do cache key → klient Kleosin cache'owany pod `"kraszewskiego 10a, białystok"` z coords Białegostoku.
- **Fix** (5 commitów + 6 tagów, master `f22-city-aware-geocoding-live`):
  - `9fe0980` panel_client — `delivery_city` + `pickup_city` + `id_location_to` z raw
  - `af01fcc` common — flag `CITY_AWARE_GEOCODING=True` (kill-switch default=True)
  - `5d9754c` geocoding — signature `geocode(addr, city=None)`, fail-loud gdy None+flag, legacy fallback gdy flag False (backward compat: stare cache keys `"street, białystok"` działają)
  - `c28daa6` callers — propagacja przez panel_watcher → shadow_dispatcher → state_machine; ev_payload NEW_ORDER niesie pickup/delivery_city
  - `b63c27e` tests — `test_city_aware_geocoding.py` 16/16 PASS (fixture #466975/466978, Warszawa-ready multi-city)
- **Regresja**: 137/137 baseline clean tests nienaruszone (4 pre-existing failures niezmienione).
- **Shadow delta** (`/tmp/city_fix_shadow_delta_2026-04-19.md`): 49 orders w state podejrzane, 8 cache entries corrupt (out-of-bbox).
- **Pending do LIVE**: restart `dispatch-panel-watcher` + `dispatch-shadow` (wymaga ACK). `dispatch-telegram` NIE wymaga (nie woła geocode). Cache invalidation 8 entries przez `tools/invalidate_city_bugged_geocodes.py --execute`.

### V3.11.1 (2026-04-19 rano) — Telegram Transparency OPCJA A LIVE
- **Korekta:** MVP z V3.11 (`ENABLE_TRANSPARENCY_ROUTE` tag `f22-transparency-mvp-live`) był DOCS-ONLY — flag nigdy nie istniała w `common.py`, `plan.sequence` nigdy nie trafił do `format_proposal`. Faktyczny LIVE dzisiaj.
- **Commit A** (`165fd38`, tag `f22-transparency-l2label-committed`): L2 label fix `🔗 blisko: X (0.95km)` → `🔗 po odbiorze z X → +0.95km` + 3 flagi (`ENABLE_TRANSPARENCY_ROUTE/REASON/SCORING`, all default True)
- **Commit B** (`1b87e79`, tag `f22-transparency-route-reason-committed`): reason line (`💡 najbliższy + fala z Eljot + wolny za 3min`) + route section (pickupy then drops wg `plan.sequence`) + downstream serializer checklist compliant (bag_context w obu lokacjach: `_serialize_candidate` + inline best)
- **Commit C DEFERRED:** scoring breakdown (`📊 95 = baza 70 + wave +17 + bundle +8`) wymaga propagacji per-component scoring z `scoring.py` + `wave_scoring.py` + 2 downstream serializers. Osobna sesja.
- 6 new unit tests PASS + 61 existing tests PASS (F21 44, C7 10, telegram-timeout 7). Zero regressions.
- Restarty: dispatch-shadow + dispatch-telegram, oba czyste, zero errors w 60s monitoring.

### V3.11 (2026-04-18 wieczór) — Sprint C skeleton COMPLETE
- **11 live wins w jednej sesji** (P1 + C1 + audit docs + C2 + C3 + C4 + C5 + C6 + C7 + geocoding 8/12 + Telegram transparency MVP)
- **137/137 testów PASS** (44 f21 baseline + 93 nowych F2.2 sprint C)
- Wszystkie feature flags F2.2 default False (current behavior preserved)
- Tag finalny: `f22-sprint-c-skeleton-complete`
- 12+ rollback tags per sprint
- **Telegram Transparency MVP LIVE** — propozycje pokazują ordered route sequence

### V3.10 (2026-04-18 popołudnie) — Sprint C day 1 closing
- 3 live wins: P1 TIMEOUT_SUPERSEDED, C1 per_order_delivery_times, geocoding 8/12
- Audit docs V3.9 committed
- 2 skeleton placeholders: C2, C4, C5 (1 of 6 features)

### V3.9 (2026-04-18 rano) — Post-F2.2-audit
- 7 raportów F2.2 w workspace/docs/
- 46,119 rows merged dataset (SCOPED 95.38% coverage, później 97.94% po geocoding)
- Architecture Spec dla Sprint C ready
- 108 kPLN/rok business case confirmed
- BAG_TIME_HARD_MAX=35 DEPRECATED (replaced by per-order 35min rule)
- SINGLETON p90 speed tier standardized

### V3.8 (17.04.2026)
- F2.1d COD Weekly LIVE (Auto COD Transport w Wynagrodzenia Gastro)
- Courier App (Nadajesz.pl) LIVE — Kotlin+Compose, FastAPI backend :8767
- Panel admin GPS: https://gps.nadajesz.pl/panel

### V3.7 (16.04.2026)
- F2.1b Decision Engine 3.0 COMPLETE (R1-R9 rules)
- 40 testów bazowych, FAZA A+B live

---

## Stan systemu na 2026-04-18 (V3.11 post-Sprint-C-skeleton)

### 6 serwisów dispatch live (active)
- `dispatch-panel-watcher.service`
- `dispatch-sla-tracker.service`
- `dispatch-shadow.service`
- `dispatch-telegram.service`
- `dispatch-gps.service` (legacy PWA :8766, dying)
- `nginx.service`

Plus:
- `courier-api.service` (FastAPI :8767) — courier app backend
- `dispatch-cod-weekly.timer` — F2.1d weekly cron

### Decision Engine 3.0 (F2.1b) LIVE — baseline rules R1-R9

Reguły bazowe (Bartek Gold Standard):
- **R1** delivery spread ≤ 8km
- **R2-R4** corridor 2.5km, dynamic bag cap, free stop +100
- **R5** pickup spread ≤ 1.8km
- **R6** BAG_TIME hard ≤ 35 min + soft zone 30-35 (`BAG_TIME_HARD_MAX=35`, kalibracja z p95=35.6)
- **R7** long-haul peak isolation (>4.5km, 14-17 Warsaw)
- **R8** pickup_span czasowy — DEFERRED F2.1c (wymaga T_KUR propagation)
- **R9** stopover -8/stop + wait penalty (-6/min over 5)

### F2.2 AUDIT — Architecture Spec ready

**Primary reference:** `workspace/docs/F2.2_SECTION_4_ARCHITECTURE_SPEC_2026-04-18.md`

**Kluczowe findings empiryczne:**
- OVERLAP 4908 cases (mid-trip pickup dataset dla C6)
- Speed tier FAST: 9 kurierów (SINGLETON p90 metric)
- Strong transitions: 220 pairs (restaurant_pair_affinity source)
- Weak transitions: 180 pairs
- Food-court zero-distance: 16 pairs
- TIER_A missed same-restaurant: 2187/rok = **108 kPLN/rok** (sekcja 3.3)
- PEAK regime: 11 cells (Sunday 13-19h dominant)

**Architektura docelowa:**
- Single hard gate: per-order delivery_time ≤ 35 min
- R1/R5/R6/R7/R8 hard → soft penalties
- Stretch bonus asymmetric per speed tier (FAST/NORMAL/SAFE)
- Context-aware weights (NORMAL vs PEAK, 11-cell lookup)
- Feature flags rollout, sequential C1→C7
- Rollback trivial: flag False + restart

### F2.2 Sprint C skeleton COMPLETE (shadow mode)

**Wszystkie flagi default False = current behavior preserved.**

#### Sprint C commits + tags

| Sprint | Commit | Tag | Status |
|---|---|---|---|
| P1 TIMEOUT_SUPERSEDED | 4d984ca | f22-prep-p1-live | LIVE (clean logging) |
| C1 per_order_delivery_times | ce7628e | f22-c1-live | LIVE |
| Audit docs V3.9 | 500fce4 | f22-audit-docs-committed | LIVE |
| C2 per-order 35min gate | eadf25f | f22-c2-shadow-live | SHADOW |
| C4 speed_tier_tracker | 8e9dcbe | f22-c4-tracker-committed | MANUAL RUN OK |
| C5 skeleton (1 of 6) | 222be21 | f22-c5-skeleton-committed | DEPRECATED by C5 full |
| V3.10 day1 docs | 11ae5c3 | f22-sprint-c-day1-docs | — |
| C3 R6 narrow soft zone | cc16755 | f22-c3-narrow-shadow-live | SHADOW |
| C5 FULL 6 features | 4fac50e | f22-c5-full-shadow-live | SHADOW |
| C6 commitment_emitter | — | f22-c6-skeleton-committed | SKELETON |
| C7 dispatch_pipeline integration | e0dc06e | f22-c7-skeleton-live | SKELETON |
| Telegram transparency MVP | — | f22-transparency-mvp-live | LIVE |
| **Sprint C skeleton complete** | — | **f22-sprint-c-skeleton-complete** | — |

#### Feature flags stan docelowy (`common.py`)

```python
# F2.2 Sprint C flags (all default False = current behavior preserved)
USE_PER_ORDER_GATE = False           # C2 — hard per-order 35min
ENABLE_C2_SHADOW_LOG = True          # C2 — observational diff logging (ON)
DEPRECATE_LEGACY_HARD_GATES = False  # C3 — R6 narrow soft zone
ENABLE_SPEED_TIER_LOADING = False    # C4 — courier tier read from JSON
ENABLE_WAVE_SCORING = False          # C5 — 6-feature adaptive scoring
ENABLE_C5_SHADOW_LOG = True          # C5 — observational diff logging (ON)
ENABLE_MID_TRIP_PICKUP = False       # C6 — commitment emit at state transitions
ENABLE_PENDING_QUEUE_VIEW = False    # C7 — pending_queue + demand_context params

# Telegram transparency (LIVE od 18.04.2026)
ENABLE_TRANSPARENCY_ROUTE = True     # MVP — ordered route sequence w proposal
ENABLE_TRANSPARENCY_SCORING = False  # OPCJA A full — DEFERRED do jutra
```

#### Shadow log files (observational data)

Obserwacyjne logi aktywne od restart dispatch-shadow.service 2026-04-18:
- `/root/.openclaw/workspace/dispatch_state/c2_shadow_log.jsonl` — diff events (C2 vs current SLA)
- `/root/.openclaw/workspace/dispatch_state/c5_shadow_log.jsonl` — wave_scoring diffs (|adjustment| ≥ 1.0)
- `/root/.openclaw/workspace/dispatch_state/learning_log.jsonl` — P1 clean timeout_outcome bucket

### F2.2 Sprint C file structure

Nowe pliki w `scripts/dispatch_v2/`:
- `wave_scoring.py` — 6 features (same_restaurant, food_court, pair_affinity, stretch_bonus, wave_continuation, context_peak_multiplier)
- `speed_tier_tracker.py` — standalone nightly script (stdlib only)
- `commitment_emitter.py` — 6 commitment levels C6 skeleton
- `pending_queue_provider.py` — C7 helper (get_pending_queue, compute_demand_context)

Zmodyfikowane:
- `common.py` — feature flags + constants
- `feasibility_v2.py` — C2 per-order gate + C3 narrow soft zone
- `scoring.py` — C3 soft penalties + C5 wave adjustment integration
- `dispatch_pipeline.py` — C7 kwarg-only signature extension
- `route_simulator_v2.py` — C1 per_order_delivery_times
- `telegram_approver.py` — P1 5-bucket timeout + MVP transparency route

### Telegram Transparency OPCJA A (LIVE 2026-04-19)

**Flagi w `common.py`** (all default True — od razu aktywne po restart):
- `ENABLE_TRANSPARENCY_ROUTE = True` — route section (pickupy then drops)
- `ENABLE_TRANSPARENCY_REASON = True` — natural-language reason line
- `ENABLE_TRANSPARENCY_SCORING = True` — score breakdown (C DEFERRED, flag aktywuje się po wdrożeniu scoring.py propagation)

**Historia:** MVP z V3.11 (tag `f22-transparency-mvp-live`) był DOCS-ONLY — flaga nigdy nie trafiła do kodu. Commit A+B z 2026-04-19 faktycznie wdraża.

**Problem biznesowy Adriana (2026-04-19):** Dotychczasowy label `🔗 blisko: Bar Eljot (0.95km)` był mylący — w naturalnym języku sugeruje że kurier odbiera z Bar Eljota, podczas gdy faktycznie znaczy że kurier ma już w bagu order z Bar Eljota i dokleja się do tej fali. Info o bliskości do restauracji z którą kurier NIE odbiera razem jest mylące. Plus Adrian chce wyjaśnienie logiki (CZEMU ten kurier) + poznać trasę.

**Aktualny format propozycji (Commit A + B LIVE):**
```
[PROPOZYCJA] #100
Rukola → Lipowa 23
🕐 Odbiór: 09:25 (gotowe)

🎯 Bartek O. (95.28) — 0.8 km, ETA 08:15 → deklarujemy 09:30  🟡 za 3 min  🔗 po odbiorze z Bar Eljot → +0.95km
   💡 najbliższy + fala z Bar Eljot + wolny za 3 min
🥈 Mateusz (87.00) — 2.5 km, ETA 08:18 → deklarujemy 09:30  🟢 wolny
   💡 wolny

📦 3 ordery w bagu:
🗺️ Kolejność:
   🍕 Rukola → Bar Eljot → Miejska Miska
   📍 Lipowa 23 → Legionowa 12 → Zachodnia 8

✓ feasible=23 best=Bartek
TAK / NIE / INNY / KOORD
```

**Pliki zmodyfikowane:**
- `dispatch_pipeline.py` — `bag_context` (order_id→restaurant/address mapping) w `enriched_metrics`
- `shadow_dispatcher.py` — `_serialize_candidate` (loc A) + inline best w `_serialize_result` (loc B) propagują `plan` + `bag_context`
- `telegram_approver.py` — `_reason_line()` + `_route_section()` + integracja w `format_proposal()`

**Route section logic:** N=1 (solo) → skip. N≥2 → pickupy (deduped w kolejności sequence) + drops (wg sequence).

**Reason line logic:** "najbliższy" gdy km_to_pickup ≤ min innych, "fala z X" z bundle_level1/2, "po drodze" z L3, "wolny [za N min]" z free_at_min.

**Commit C DEFERRED:** Scoring breakdown `📊 95 = baza 70 + wave +17 + bundle +8` wymaga:
1. `scoring.py` propaguje 4 nowe pola do candidate dict: `base_score`, `wave_adjustment`, `bundle_bonus`, `soft_penalty`
2. Downstream serializer checklist dla obu lokacji (A: `_serialize_candidate`, B: inline best w `_serialize_result`)
3. `_score_summary_line()` w `telegram_approver.py`
4. Nowy unit test `test_scoring_breakdown_invariant` (sum ≈ total ±0.5)

---

## Zasady współpracy (HARD) — V3.11 edition

### Podstawowe (bez zmian)
- **"Pytaj nie zgaduj"** — każde zgadywanie = 10-30 min debug
- Stopniowy rollout, nie big-bang. Shadow mode przed production.
- **Per krok .py:** draft → ACK → `cp .bak` → edit → `py_compile` → import check → test → commit
- **NIE restartuj systemd** bez py_compile + import check + mojej zgody
- Granular git tags jako rollback points (schemat: `f22-{sprint}-{step}-{status}`)
- NIE używaj `jq`, `sed` tylko do odczytu, heredoks tylko przy `str_replace`
- Warsaw TZ: `ZoneInfo("Europe/Warsaw")` jako `WARSAW`
- Atomic writes: temp → fsync → rename

### F2.2 implementation sessions (NOWE od V3.9)
- **Full patch workflow obowiązkowy** per każda zmiana kodu
- **Rollback plan** w każdej sesji = requirement, nie opcja
- **Feature flags default False** przy deploy
- **Shadow mode ≥5 dni** przed production flip dla C5/C6/C7 decyzyjnych
- **Per sesja:** czytaj `docs/F2.2_SECTION_4_ARCHITECTURE_SPEC` pierwszy
- **Downstream consumer checklist** dla każdej nowej metryki:
  - shadow_dispatcher `_serialize_candidate` (location A)
  - inline best serialization (location B)
  - learning_analyzer readers
  - test suite

### Autonomic mode dla CC (NOWE od V3.10)
CC może pracować autonomicznie jeśli otrzyma explicit autonomic mode prompt. Eskalacja **TYLKO** w 4 przypadkach:
1. Write poza zadeklarowany scope
2. Contradiction w wytycznych (pokazać konkret)
3. Fundamental assertion FAIL po 2 próbach safe recovery
4. >30 min bez progresu w jednej fazie

Progress updates co 5-10 min. STOPy tylko na commit/restart ACK.

### Critical new rule: downstream serializer checklist
Każda nowa metryka w `dispatch_pipeline` lub `feasibility_v2` wymaga sprawdzenia:
- [ ] `shadow_dispatcher._serialize_candidate` — location A
- [ ] inline best serialization — location B
- [ ] `learning_analyzer` readers
- [ ] test coverage

**Uczucie że "serializer jest już zrobiony"** = kodyfikuj checklist. Z F2.1b/c wynika że serializery miss ~2-3× per sprint.

---

## NIGDY

- Nie łam produkcji bez `cp .bak` + py_compile + testy
- Nie dodawaj `prep_variance` do `pickup_ready_at` (wyłączone F1.8g)
- Nie proponuj kuriera z `picked_up` jako bundle candidate (L1/L2)
- Nie używaj identycznego ETA dla wszystkich kandydatów
- Nie używaj GPS pozycji >60 min jako realnej
- **NIE restartuj `dispatch-telegram.service` bez explicit ACK** — bezpośrednio wysyła propozycje do bota. Jeden restart bug → koordynator ręcznie przypisuje do rana
- Nie używaj `urllib.request.install_opener` z nowym CookieJar w `get_last_panel_position` (invaliduje main session → HTTP 419)
- `edit-zamowienie` calls sekwencyjnie, nie ThreadPoolExecutor (CookieJar thread-safety)

## ZAWSZE

- Warsaw TZ via `ZoneInfo("Europe/Warsaw")` jako `WARSAW`
- Atomic writes: temp → fsync → rename
- Update `TECH_DEBT.md` na koniec sesji
- Batch z STOP po 5-8 krokach
- Feature flag dla każdej nowej decyzyjnej zmiany
- Downstream consumer checklist dla nowych metryk
- `cp outputs do docs/wave_audit_outputs/<data>/` (wytyczna #21)
- Downstream queries use SCOPED filter unless per-courier metric (wytyczna #23)

---

## Reference files dla sesji F2.2 Sprint C

### Primary design & data (workspace/docs/)
- `F2.2_SECTION_4_ARCHITECTURE_SPEC_2026-04-18.md` — **PRIMARY design doc**
- `F2.2_MERGE_REPORT_2026-04-18.md` — dataset 46119 rows baseline
- `F2.2_SECTION_3_1_WAVE_CHAINS_2026-04-18.md` — singleton p90 speed tier finding
- `F2.2_SECTION_3_2_TRANSITIONS_2026-04-18.md` — 220 strong + 180 weak pairs
- `F2.2_SECTION_3_3_MISSED_BUNDLING_2026-04-18.md` — **108 kPLN/rok business case**
- `F2.2_SECTION_3_5_PEAK_REGIMES_2026-04-18.md` — 11-cell Sunday dominant

### Handover + session resumes
- `F2.2_HANDOVER_2026-04-19.md` — **jutrzejsza sesja Q&A**
- `F2.2_SPRINT_C_HANDOVER_2026-04-19.md` — sprint C day1 detailed Q&A
- `project_memory/project_f22_sprint_c_complete_2026-04-18.md` — resume prompt

### Raw data (workspace/docs/wave_audit_outputs/2026-04-18/)
- `wave_audit_dataset_merged_2026-04-18.db` — SQLite 46119 rows
- `wave_audit_transitions_2026-04-18.csv` — 220+180 pairs
- `wave_audit_peak_regimes_2026-04-18.csv` — 11 cells
- `wave_audit_missing_canonicals_2026-04-18.csv` — geocoding queue
- 20+ more data artifacts

---

## JUTRZEJSZY SESJA (2026-04-19) priorytety

### Sesja 1 (1.5-2h) — Shadow review + flag flips
1. **Review shadow logs (15 min):**
   - `c2_shadow_log.jsonl` (24h data) — ile diffs vs current SLA (oczekiwane: ~0 per insight)
   - `c5_shadow_log.jsonl` — jakie adjustments meaningful
2. **Telegram Transparency Opcja A pełna (45-60 min):**
   - Dodaj scoring breakdown do propozycji (base + wave_adjustment breakdown + soft penalties)
   - Flaga `ENABLE_TRANSPARENCY_SCORING=True`
   - Testy + restart dispatch-telegram (z explicit ACK!)
3. **Speed_tier_tracker manual run (10 min):**
   - `python3 scripts/dispatch_v2/speed_tier_tracker.py`
   - Weryfikacja: Bartek/Mateusz/Gabriel = FAST zgodnie z 3.3
4. **Flag flip #1 (15 min):**
   - `USE_PER_ORDER_GATE=True` (po C2 review OK)
   - Restart dispatch-shadow, monitor 20 min
5. **Commit tag + docs update**

### Sekwencja flag flipów (następne dni)
1. `USE_PER_ORDER_GATE=True` — **jutro po review**
2. `DEPRECATE_LEGACY_HARD_GATES=True` — dzień +1 po obserwacji C2
3. `ENABLE_SPEED_TIER_LOADING=True` — **po cron setup** (systemd timer)
4. `ENABLE_WAVE_SCORING=True` — po 5+ dni shadow walidacji C5
5. `ENABLE_MID_TRIP_PICKUP=True` — po wave_scoring stable (+ integracja C6 z state_machine)
6. `ENABLE_PENDING_QUEUE_VIEW=True` — jako ostatni

### Co jeszcze jutro i w tygodniu
- 4 pending geocodes (Eatally HIGH vol=60, Chilli Chicken 48, Oregano Pizza 22, Atmosfera 7) — osobna sesja z `panel_client.address_id` join (wymaga context panel HTML)
- Speed_tier_tracker cron setup (systemd timer, 03:00 Warsaw = 02:00 UTC)
- C5 calibration na bazie 5-7 dni shadow data
- C6 state_machine integration (obecnie tylko skeleton, commitment levels nie emit w real-time)

---

## Parallel workstreams (nie blokują F2.2 core)

### GPS / Courier App
- Courier App (Kotlin+Compose, FastAPI :8767) aktywny — zastąpił PWA
- Legacy PWA (port 8766) zombifikowany — 2/30 kurierów
- Pozostałe 7 kurierów bez GPS (Gabriel, Grzegorz, Dariusz M, Szymon P, Adrian R, Mateusz O, Łukasz B) — onboarding do Courier App
- GPSLogger Traccar fallback — tylko jeśli app nie działa

### Business items
- Restimo API (quote-then-order decision pending)
- Warsaw expansion (miesiąc+5 after F2.2 live)
- R16/R17 restaurant violation alerts + `restaurant_violations.jsonl` event logging
- R27 declared-time compliance enforcement
- Full contrastive fit wag po 2 tyg clean ground truth

---

## Kontakty & infrastructure

### Serwer
- **IP:** 178.104.104.138 (Hetzner CPX22, Ubuntu 24.04, UTC)
- **Panel gastro:** gastro.nadajesz.pl (Laravel, CSRF tokens)
- **Panel admin GPS:** https://gps.nadajesz.pl/panel (admin/nadajesz2026), HTMX+Tailwind+Leaflet+SSE 5s

### Bots
- **@NadajeszBot** — proposals
- **@GastroBot / NadajeszControlBot** — stop/start control (port 8443 HTTPS)
- **Adrian Telegram ID:** 8765130486
- **Grupa ziomka:** -5149910559

### Ports
- 8443 HTTPS — NadajeszControlBot
- 8765 — legacy Traccar (fallback)
- 8766 — PWA gps_server (dead)
- 8767 — courier-api (active FastAPI)
- Nginx routing: /panel→:8767, /api/*→:8767, /gps→:8766 (legacy PWA), /apk/→static APK

### Runtime & services
- **AI runtime:** OpenClaw 2026.3.27 in Docker, model openai/gpt-5.4-mini (DeepSeek fallback)
- **Stop flag:** `/tmp/gastro_stop`
- **Exec approvals:** `openclaw approvals set` CLI (nie openclaw.json)

### APIs
- **Mapping:** Google Maps Distance Matrix API (active)
- **Geocoding:** Nominatim / OpenStreetMap (Google Geocoding API denied)
- **Schedule:** Google Sheets (Spreadsheet ID: `1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8`, gid `533254920`); fetch 06:00 i 08:00 daily
- **Courier App:**
  - APK https://gps.nadajesz.pl/apk/courier.apk
  - package `pl.nadajesz.courier`
  - Kotlin+Compose, Room 50k buffer
  - Upload coroutine 30s (NIE WorkManager)
  - Adaptive GPS 20/30/40s+50m
  - Watchdog WM 15min, BootReceiver→flag
  - Backend SQLite WAL, dual-write `gps_positions_pwa.json`
  - Auth: PIN `kurier_piny.json`, UUID token, 90min auto-logout

---

## Panel API reference (NadajeSz-specific)

### Order detail endpoint
- **POST** `/admin2017/new/orders/edit-zamowienie`
- Body: `_token + id_zlecenie`
- Returns: `{"zlecenie":{...}}`

### Order status mapping (`id_status_zamowienia`)
- 2 = nowe/nieprzypisane
- 3 = dojazd
- 4 = oczekiwanie pod restauracją
- 5 = odebrane
- 6 = opóźnienie
- 7 = doręczone
- 8 = nieodebrano (anulowane przez kuriera)
- 9 = anulowane

Panel watcher ignores statuses 7, 8, 9.

### Timestamp fields
- **`czas_odbioru_timestamp`** — Warsaw time (Europe/Warsaw, NOT UTC) — actual pickup time
- **`created_at`** — UTC (suffix Z)
- **`czas_odbioru`** — int prep minutes; **<60 = elastyk** (coordinator declares via 5-60 min dropdown); **≥60 = czasówka** (hard restaurant declaration, held in Koordynator id_kurier=26)
- **`czas_kuriera`** (top-level, HH:MM) — declared courier arrival at restaurant
- `dzien_odbioru` — pickup timestamp
- `czas_doreczenia` — delivery timestamp

### Key params
- **`time`** param w `/admin2017/new/orders/przypisz-zamowienie`: integer minutes from now (nie timestamp nie HH:MM)
- **`--keep-time`** flag musi re-fetch original `czas_odbioru` z `edit-zamowienie` i resend integer (sending `0` clears UI)

### Address extraction
- Restaurant address: `address.street`
- Restaurant name: `box_zam_name` from HTML

### Virtual courier
- `id_kurier=26` "Koordynator" = holding bucket dla scheduled orders (czasówka)

---

## Key learnings accumulated (V3.8 → V3.11)

### Infrastructure
- **Never restart systemd without `py_compile` and import check first**
- `jq` nie zainstalowany na serwerze — JSON manipulation musi być Python
- `urllib` CookieJar nie thread-safe — `edit-zamowienie` sekwencyjnie
- `get_last_panel_position` nigdy nie wolno wołać `urllib.request.install_opener` z nowym CookieJar (invaliduje main session → HTTP 419)
- Geocoding uses Nominatim/OpenStreetMap (Google denied; tylko Distance Matrix active)
- Subprocess calls z `gastro_scoring.py` muszą używać host path `/root/` nie Docker path `/home/node/`

### F2.2 Sprint C specific (NOWE)
- **Every new metric w dispatch_pipeline/feasibility_v2 needs downstream consumer checklist**:
  1. shadow_dispatcher `_serialize_candidate` (location A)
  2. inline best serialization (location B)
  3. learning_analyzer readers
  4. test suite
- **Feature flags default False przy deploy** = zero production impact przy shadow mode
- **Rollout gap 24-48h między flag flips** = ryzyko cascade fail jest realne, observability jest critical
- **Import chain analysis przed restart** — 2026-04-18 okazało się że tylko 1 service wymaga restart zamiast 3

### Process
- **"Pytaj nie zgaduj"** — pytaj gdy niejasne, zamiast zgadywać
- **Autonomic mode dopuszczalny** dla CC gdy jawnie zadeklarowany, z 4 explicit escalation triggers
- Granular git tags jako rollback points (`f22-{sprint}-{step}-{status}`)
- Per sesja minimum 3 `.bak` backups dla `rollback_plan`
- Warsaw TZ zawsze via `ZoneInfo("Europe/Warsaw")`
- Atomic writes via temp/fsync/rename

---

## Previous F2.1c priorities (DEPRECATED — superseded by F2.2 Sprint C)

Plan F2.1c z V3.7 (R8 pickup_span, learning_analyzer, AUTO_APPROVE flip, `_parse()` unified) — **DEFERRED**. 

Status 2026-04-18:
- **R8 pickup_span** — DEFERRED w V3.9, tylko jeśli reactivated (TODO comment w feasibility_v2.py)
- **Learning analyzer** — DEFERRED (requires F2.2-prep P1 fix, teraz done → może być re-enabled w tygodniu); complements F2.2, nie replaces
- **Auto-approve (R26)** — DEFERRED (depends on F2.2-prep P1 fix + shadow data dla accurate agreement rate). Concept NIE zastąpiony przez F2.2 — auto-approve to workflow automation (Ziomek → panel bez Adrian ACK), F2.2 to decision quality (co Ziomek proponuje). Po F2.2 live → niższy threshold (75% zamiast 85%) możliwy bo scoring lepszy.
- **`_parse()` unified fix + SLA regression test** — DEFERRED

Resume plan dla każdego z powyższych w TECH_DEBT.md.
