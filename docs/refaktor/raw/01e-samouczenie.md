# RAW 01e — raport agenta f1-samouczenie (mechanizmy samouczące + replay)

> Surowy raport subagenta read-only (Faza 1). Zweryfikowany na HEAD `fcf1342`. Synteza → `../01-stan-obecny.md`.

# MECHANIZMY SAMOUCZĄCE ZIOMKA — rekonesans read-only (HEAD fcf1342, master, 05.07)

Wszystkie `plik:linia` z HEAD (linie dryfują — weryfikowane grepem symbolu). Stan flag: `flags.json` (read-only) + defaulty `common.py` + env z `systemctl cat` (read-only). FAKT = potwierdzone w kodzie/flagach/systemd; HIPOTEZA oznaczona jawnie.

⚠ Korekta wobec pamięci `lgbm_roadmap.md` (60 dni): opisuje Fazę 6/7 jak świeżą — na HEAD to shadow-tło, NIC z ML nie wpięte w werdykt.

---

## 1. LightGBM — trzy równoległe tory, ZERO wpływu na decyzję

Działają JEDNOCZEŚNIE trzy modele LightGBM, wszystkie SHADOW (logują, nie zmieniają werdyktu). Leżą w DWÓCH różnych `ml_data_prep`:

| Tor | Model na dysku | Ładowanie | Flaga (efektywna) | Konsument |
|---|---|---|---|---|
| A. Pairwise ranker v1.1 (Faza 6, wybór kuriera) | `scripts/ml_data_prep/models/v1.1/lgbm_ranker.txt` (POZA repo) | `ml_inference.py:174` `lgb.Booster` | `ENABLE_LGBM_SHADOW` — **ON** (env `=1` w override dispatch-shadow) | `dispatch_pipeline.py:6692-6739` → `metrics["lgbm_shadow"]` + log `LGBM_SHADOW` |
| B. Dwumodel solo/bundle (A2, 20.06) | `dispatch_v2/ml_data_prep/models_twomodel/{solo,bundle}/` (W repo) | `ml_inference.py:687` per reżim | `ENABLE_LGBM_TWOMODEL_SHADOW` — **ON** (`flags.json`, `C.flag`) | `dispatch_pipeline.py:6748-6777` → `metrics["lgbm_twomodel_shadow"]` |
| C. ETA residual (R3, 18.06) | `scripts/ml_data_prep/models/eta_residual_v1/`+`_v2_drop/` (POZA repo) | `eta_residual_infer.py:66,95` | `ENABLE_ETA_R3_SHADOW`+`_DROP` — **ON** | `eta_calibration_logger.py:291-331` (off-hot-path) |

FAKTY:
- **Żaden model nie wpływa na decyzję.** `ENABLE_LGBM_PRIMARY`=OFF (`common.py:2677`, brak env, brak w flags.json). Twomodel (`flags.json:199`): „NIE konsumowany przez werdykt — arbitraż solo↔bundle nierozwiązany".
- Model rankuje TYLKO kandydatów już przepuszczonych przez feasibility HARD (BC nad selekcją reguł) — warstwa obserwacyjna równoległa do `scoring.py`. Featury v1.1: 42 cechy, top split-count `delta_dist_km`/`rank_by_dist`/`dist_to_pickup_km`.
- Skew prod (twomodel): „3 skew naprawione, parity 0/58385" (`flags.json:199`), router per-kandydat po stanie worka (`ml_inference.py:762`).
- **BRAK crona/timera retrenującego LGBM** — modele zamrożone (v1.1 z 01.05, twomodel+residual z 20.06). „Daily learning loop" z roadmapy NIE istnieje na HEAD. Trening = offline ręczny (`ml_data_prep/train_two_models.py`).
- HIPOTEZA (rozjazd kanonu): `ENABLE_LGBM_SHADOW` czytane jako stała modułu z env (`common.py:2676`, `getattr` w `dispatch_pipeline.py:6692`), NIE przez `C.flag()`. Kanon „3 światy/env martwy po D3" tej flagi nie objął — env `=1` w override JEST jej jedynym sterem. Niespójność sterowania, nie błąd działania.

---

## 2. Kalibracje żywe — 6 torów; wszystkie shadow/pomiar, JEDEN dotyka HARD

| Kalibracja | Generator (timer/cron) | Artefakt | Konsument w decyzji | Stan |
|---|---|---|---|---|
| ETA quantile (pred→real) | cron `35 4 * * *` `tools.eta_quantile_calib` | `dispatch_state/eta_quantile_map.json` | `calib_maps.eta_quantile_calibrate` (`:126`) | SHADOW + JEDNA gałąź HARD (niżej) |
| Prep-bias (deklaracja vs realna gotowość) | cron `15 4 * * *` `tools.restaurant_prep_bias` | `restaurant_prep_bias.json` | `calib_maps.prep_bias_for` (`:162`) | SHADOW (`ENABLE_PREP_BIAS_SHADOW`=ON); live `ENABLE_PREP_BIAS_TABLE`=OFF |
| ETA residual (LGBM) | timer `dispatch-eta-calibration` 30 min | model + `eta_calibration_log.jsonl` | `eta_calibration_logger.py` (MAE base vs corrected) | SHADOW pomiar; flip R3 = NO-GO (weekend +6,2%) |
| ETA load-aware (bufor optymizmu ODBIORU, K3) | `tools/eta_load_aware_calibrate.py` (ręczne) | `eta_load_aware_calib.json` | `eta_load_aware.pickup_buffer_min` w `dispatch_pipeline.py:4379` | SHADOW (`eta_la_*`); decyzja `ENABLE_ETA_LOAD_AWARE`=OFF (`common.py:460`) |
| Drive-speed tier (mnożnik tempa) | cron `25 4 * * *` `tools.build_speed_tiers` | tiery prędkości | `common.speed_mult_for_tier` (`:2551`) → route_sim leg_min | `ENABLE_DRIVE_SPEED_TIER_CORRECTION`=OFF → 1.0 (inert) |
| Bundle-calib O2 (λ=1.5) | timer `dispatch-bundle-calib-shadow` 5 min | `bundle_calib_shadow.jsonl` (re-scorowalny) | werdykt `dispatch-bundle-calib-review` (one-shot 02.07) | SHADOW korpus |

Dodatkowe tory samouczące:
- **Retro-learning** — timer `dispatch-retro-learning` 04:30 UTC, łańcuch: `retro_learning.py`→`retro_conclusions.json` (READ-ONLY feed kalibracyjny, ręczne wpięcie), `courier_reliability.py`→**`courier_reliability.json`** (breach/conf per cid), `eta_calibration_shadow.py`, `a2_selection_shadow.py`.
- **R-04 tier suggestions** — `ENABLE_R04_SHADOW`=ON / `ENFORCE`=OFF (`common.py:2667`). `tier_suggestions.json`; `courier_tiers.json` = źródło prawdy.
- **Auto-proximity Faza 7** (T1/T2/T3) — ŻYJE, wołany co decyzję (`dispatch_pipeline.py:3068`, 11 call-site'ów), ale `AUTO_PROXIMITY_ENABLED`=false + `SHADOW_ONLY`=true → tylko `auto_route` w logu (i tak `ENABLE_AUTO_ASSIGN`=false).

MARTWE/inertne DZIŚ: drive-speed tier (OFF), prep-bias→R6 (OFF), eta-load-aware decyzja (OFF), drive_min_calibration v2 (przesłanka = ARTEFAKT 05.06, `flags.json:86`), auto-proximity live (OFF), R3 flip (NO-GO), R04 enforce (OFF).

`forecast21_day`: NIE istnieje w silniku (grep pusty) — to artefakt PANELU (`nadajesz_clone/panel`, GRF-02), poza zakresem silnika.

---

## 3. Dane — źródła i częstotliwość

- **`courier_ground_truth.json`** (`courier_ground_truth.py:25`): jeden writer = courier-api. Reader-only w silniku. Pola: `picked_up_at`, `delivered_at`, **`gps_arrived_at`** (5b, 05.07 = fizyczny przyjazd pod adres dostawy, geofence dwell 30 s). GC `dispatch-ground-truth-gc` 1h. Konsumenci:
  - `panel_watcher.py:2085` `ENABLE_PICKUP_FROM_GROUND_TRUTH`=ON → nadpisuje `picked_up_at` prawdą GPS → **zasila kotwicę R6 (HARD)**.
  - `sla_tracker.py:221-228` `gps_delivered_at` — TYLKO telemetria.
  - 5b `gps_arrived_at`: measurement-only; werdykt pokrycia ~07-08.07 odblokowuje flipy O2/feas_carry.
- **`shadow_decisions.jsonl`** (`scripts/logs/`, ~84 MB): kanoniczny log, writer `shadow_dispatcher._serialize_result`. **Serializer L1.1 (od 01.07): deny-lista, nie allowlista** (`shadow_dispatcher.py:202` `_METRICS_EXCLUDE`, `:235` `_propagate_prefixed_metrics`) — KAŻDY klucz metrics serializowany oprócz 5 redundantnych. FAKT krytyczny: przed L1.1 allowlista gubiła 38 kluczy (14 HARD: `r6_gold4_gate_recovered`, `sla_violations` detail, `eta_source`, `c2_*`, `d2_*`) → kalibracja/oracle ślepe na wnętrze HARD.
- **`events.db`** (~32 MB): `event_bus`, GC `event_bus_cleanup`. Wejście dla `replay_failed.py`.
- **GPS store:** `courier_last_pos.json` (`courier_resolver`, no-GPS, TTL 25 min) + `courier_api.db` (stan apki). `dispatch-ziomek-pred-calibration` 3 min snapshotuje predykcje.
- **`obj_replay_capture.jsonl`** — AKTYWNIE zapisywany (env `ENABLE_OBJ_REPLAY_CAPTURE=1` w override): dokładne wejścia solvera (`obj_replay_capture.py:47`).

---

## 4. TABELA WPŁYWU — wyuczone wartości vs HARD/SOFT/display

| Mechanizm | Wyuczona wartość | Konsument | HARD/SOFT/display | plik:linia | Stan flagi |
|---|---|---|---|---|---|
| ETA-quantile → R6 gold≤4 | `eta_quantile_map.p80` | bramka R6 bag-time (`_gate_bt`) gold, bag+1≤4 | **HARD** (loosening) | `feasibility_v2.py:1123-1135` | `ENABLE_ETA_QUANTILE_R6_BAGCAP`=**ON** |
| ETA-quantile → SLA gate gold≤4 | `eta_quantile_map.p80` | bramka SLA (`_sla_gate_elapsed`) ready-anchor | **HARD** (bliźniak R6) | `feasibility_v2.py:1235-1242` | ON (wtórnie `ENABLE_SLA_GATE_READY_ANCHOR`) |
| Prep-bias → kotwica R6 | `restaurant_prep_bias.bias_med` | przesuw kotwicy termicznej (stricter) | **HARD** | `feasibility_v2.py:1093-1105` | `ENABLE_PREP_BIAS_TABLE`=OFF |
| GPS ground-truth → picked_up_at | `gps_picked_up_at` | `state.picked_up_at` → kotwica R6/SLA | **HARD** (pośrednio, przez stan) | `panel_watcher.py:2085` | `ENABLE_PICKUP_FROM_GROUND_TRUTH`=**ON** |
| Tier (semi-uczony) | `courier_tiers.json` | R6 35/40 tier-aware + gold-bag-cap=4 | **HARD** | `feasibility_v2.py` (courier_tier) | source-of-truth (R04 sugeruje shadow) |
| A2 reliability | `courier_reliability.json` breach/conf | kara/bonus SOFT do score | **SOFT** | `dispatch_pipeline.py:1431,6302` | `ENABLE_A2_RELIABILITY_SOFT_SCORE`=**ON** |
| New-courier ramp | `courier_reliability.n_delivered` | rampa kary nowego | **SOFT** | `dispatch_pipeline.py:1822` | `ENABLE_NEW_COURIER_RAMP`=**ON** |
| Drive-speed tier | `DRIVE_SPEED_MULT_BY_TIER` | mnożnik leg_min route_sim | SOFT+HARD (przez R6) gdyby ON | `common.py:2551-2563` | `ENABLE_DRIVE_SPEED_TIER_CORRECTION`=OFF (inert) |
| ETA load-aware | `eta_load_aware_calib.med_err` | przesuw `eta_pickup_utc`/`travel_min` (oś OBIETNICY) | promise (wait/extension) — NIE feasibility | `dispatch_pipeline.py:4388-4391` | `ENABLE_ETA_LOAD_AWARE`=OFF |
| ETA-quantile → travel_min_cal | `eta_quantile_map.p50` | metryka do logu/replayu | **display/shadow** | `dispatch_pipeline.py:5596,6241,6255` | `ENABLE_ETA_QUANTILE_SHADOW`=ON |
| LGBM A/B/C | rankingi modeli | `metrics.lgbm_*` | **display/shadow** | `dispatch_pipeline.py:6711,6767` | shadow (primary OFF) |
| Auto-proximity route | thresholdy T1 | `auto_route` w logu | **display/shadow** | `dispatch_pipeline.py:3068` | shadow-only |

**Wniosek:** JEDYNA żywa ścieżka „wyuczona wartość → HARD" to ETA-quantile map w bramce R6/SLA dla gold≤4 (loosening) + prep-bias→R6 gdyby flip (stricter, dziś OFF) + GPS-ground-truth→picked_up_at (pośrednio przez stan). Reszta wyuczonego wpływu = SOFT (`courier_reliability`) albo shadow.

---

## 5. Replay / odtwarzalność

Dwie rodziny:

(A) **`obj_harness` + `obj_replay_capture`** (replay SOLVERA, nie całego pipeline): capture zapisuje DOKŁADNE wejścia `simulate_bag_route_v2` (aktywne, env=1) → `obj_harness.py` ładuje jako zestaw masowy „100% wierności" (`:15`) + 3 ręczne Case'y. Determinizm CZĘŚCIOWY: `now` zamrożony per rekord; `courier_pos` z `courier_api.db`/gps_history; ALE `picked_up_at`=proxy `czas_kuriera` (`obj_harness.py:12-13`), a **OSRM wołany NA ŻYWO** (`:5001`+cache), nie nagrany snapshot.

(B) **Kontrfaktyczne `*_replay.py` + `bundle_calib_*`** (werdykty ON↔OFF, protokół ETAP 5): wejście = `shadow_decisions.jsonl` (przez `ledger_io`/`_rotated_logs`) LUB `eta_truth_map.build_rows` (join predykcja↔realny-kurier). Re-scoruje TE SAME kandydatów z flagą ON vs OFF; werdykt → `dispatch_state/*_verdict.txt` (`eta_load_aware_replay.py`, `bundle_calib_review.py`→Telegram). `bundle_calib_shadow.py` liczy obiektyw O2 λ=1.5 od gotowości jedzenia, macierz OSRM raz na worek, korpus re-scorowalny.

**Co UNIEMOŻLIWIA bit-w-bit replay (luki):**
1. OSRM nie jest nagrywany — `obj_harness` re-woła `osrm_client` (`route_simulator_v2`→`:5001`+cache); eviction/drift zmienia `route_km`. **Brak globalnego frozen-clock kill-switcha** (typu „v3273") — determinizm zegara wyłącznie z `now` w rekordzie.
2. `picked_up_at`=proxy w harness (`obj_harness.py:13`) — realny odbiór ≠ `czas_kuriera`.
3. Logrotate gubi ~29% okna — naiwny odczyt żywego `shadow_decisions.jsonl` traci 497/1707 oid w 7 dni (copytruncate); TYLKO `ledger_io`/`_rotated_logs` domyka ogon (`tools/ledger_io.py`).
4. Pokrycie fizyczne rzadkie — `gps_delivery_truth.jsonl` ~11,5% okna, paczki 0% w obu prawdach → etykieta `none` (`ledger_io.require_join_coverage` zamienia brak fizyki w błąd, nie „zielone").
5. Replay okien sprzed 01.07 ślepy na HARD — przed serializerem L1.1 log nie miał 14 kluczy HARD (`shadow_dispatcher.py:185-199`).
6. GPS point-in-time — re-rezolucja z `courier_api.db` w chwili replayu może dać inną pozycję (no-GPS fallback, TTL 25 min).

---

## TOP-5 faktów dla architektury docelowej

1. **Nic samouczącego nie steruje werdyktem — poza JEDNĄ mapą.** Cały ML (3× LGBM) + auto-proximity + R04 są shadow. Jedyny żywy „wyuczony → HARD" to `eta_quantile_map.json` w bramce R6/SLA gold≤4 (`feasibility_v2.py:1123`) + `courier_reliability.json` jako SOFT. Docelowo: mapy kalibracji to **wejście HARD-krytyczne z kontraktem świeżości/pokrycia**, nie „telemetria".

2. **Brak pętli retreningu — modele to zamrożone pliki.** LGBM trenowane ręcznie offline w sprintach; brak crona. „Continuous learning loop" z roadmapy nie istnieje. Jedyne realne „uczenie na żywo" to tabele kalibracji odświeżane nocnym cronem 04:15-04:35 UTC.

3. **Ground-truth GPS (5b) to nowy fundament pomiaru, jeszcze nie decyzji.** `gps_arrived_at` + `picked_up_at` z GPS już zasilają kotwicę R6 (przez `panel_watcher`), reszta measurement-only. Werdykt pokrycia 5b (~07-08.07) = bramka odblokowująca flipy. Jeden writer (courier-api) — poprawnie unika wyścigu.

4. **Serializer (deny-lista L1.1) = fundament odtwarzalności, dziurawy do 01.07.** Replay/kalibracja czyta `shadow_decisions.jsonl`; do 01.07 gubił 14 kluczy HARD. Każda nowa metryka HARD MUSI trafiać do logu „od urodzenia" (kontrakt ⑤). Warunek konieczny wiarygodnych werdyktów ON↔OFF.

5. **Bit-w-bit replay jest DZIŚ niemożliwy** — OSRM na żywo (nie nagrany), `picked_up_at` proxy, brak frozen-clock poza `now` z rekordu, logrotate gubi ~29% (mitygacja `ledger_io`). Werdykty flipów opierają się na kontrfaktycznym re-scoringu tych samych kandydatów z logu (deterministycznym względem zapisanych wartości), nie na wiernej re-symulacji świata. Docelowo: nagrywać snapshot macierzy OSRM + zamrożony zegar w `obj_replay_capture`.

---
Niezweryfikowane/HIPOTEZY oznaczone w treści (rozjazd sterowania `ENABLE_LGBM_SHADOW` env vs C.flag). Nie dociągałem: dokładnych metryk twomodel_report poza subset_sizes; treści `bundle_calib_review` werdyktu 02.07; wewnętrznej logiki `r04_evaluator`.
