# A.1–A.6 — Badanie mechanizmu ETA w Ziomku

> **Faza:** ZADANIE A (badanie, wyłącznie odczyt) · **Data:** 2026-07-07 · **Autor:** Claude Code (sesja ETA-kalibracja)
> **Zakres:** jak Ziomek liczy ETA, jak to mierzy/koryguje, jaki jest ilościowy baseline jakości.
> **Zasady:** każdy fakt tagowany `[ZWERYFIKOWANE]` / `[HIPOTEZA]` / `[NIEZWERYFIKOWANE, bo…]`. Kurierzy pseudonimizowani (`KURIER_###`), zero imion/adresów.
> **Środowisko:** repo `/root/.openclaw/workspace/scripts/dispatch_v2/`; żywy stan `/root/.openclaw/workspace/dispatch_state/`; venv `/root/.openclaw/venvs/dispatch/bin/python`.

## Spis treści
- [Streszczenie wykonawcze](#streszczenie-wykonawcze)
- [A.0 Rekonesans](#a0-rekonesans)
- [A.1 Algorytm / model ETA](#a1-algorytm--model-eta)
- [A.2 Architektura i przepływ danych](#a2-architektura-i-przepływ-danych)
- [A.3 Procesy w tle poprawiające ETA](#a3-procesy-w-tle-poprawiające-eta)
- [A.4 Backlog i plany](#a4-backlog-i-plany)
- [A.5 Inwentaryzacja bazy historycznej](#a5-inwentaryzacja-bazy-historycznej)
- [A.6 Ilościowy baseline jakości obecnego ETA](#a6-ilościowy-baseline-jakości-obecnego-eta)
- [Największe ryzyka danych](#największe-ryzyka-danych)
- [Otwarte pytania do Adriana](#otwarte-pytania-do-adriana)
- [Aneks: ścieżki i komendy weryfikacyjne](#aneks-ścieżki-i-komendy-weryfikacyjne)

---

## Streszczenie wykonawcze

**Jak Ziomek liczy ETA `[ZWERYFIKOWANE]`.** Nie ma modelu ML w gorącej ścieżce. ETA to **deterministyczny spacer po trasie** ułożonej przez OR-Tools TSP (`route_simulator_v2._simulate_sequence`): `t = teraz`; na każdej nodze `t += czas jazdy`; przy odbiorze skok do `pickup_ready_at` (czekanie na jedzenie) + postój 1 min; przy dostawie kotwice zabezpieczające + postój zależny od klasy kuriera. Czas jazdy = **OSRM free-flow × mnożnik ruchu** (tabela godzina×dzień, np. Pn-Pt 15-17 = ×1.55) z fallbackiem haversine×2.5. Segmentacja L1 dojazd / L2 czekanie / L3 dostawa jest **liczona wewnętrznie, ale nie eksponowana** jako osobne ETA.

**Personalizacja per kurier już istnieje — ale tylko per KLASA (tier), nie per pojedynczy kurier `[ZWERYFIKOWANE]`.** Postój dostawy `DWELL_BY_TIER` (gold 1.5 / std+ 2.5 / std 4.5 / slow 6.5 / new 6.5 min) to residuum ETA uczone z `eta_calibration_log` (rekalibracja 2026-06-10 z 7496 rekordów). Mnożnik jazdy per-tier istnieje, ale wyłączony flagą.

**Kalibracja jest gęsta w POMIARZE, uboga w DZIAŁANIU `[ZWERYFIKOWANE]`.** Żywe pętle logują predykcję vs rzeczywistość co 3 min i co 30 min; codziennie przebudowują mapy korekcyjne. Ale niemal wszystkie korekty siedzą w **cieniu / za flagą OFF**; jedyny LIVE decyzyjny konsument mapy kalibracyjnej to kwantyl p80 dla poluzowania bramki R6-bagcap (`ENABLE_ETA_QUANTILE_R6_BAGCAP=true`). Model residualny LGBM zamrożony od 2026-06-18, brak auto-retreningu, brak online A/B, brak flipu wyzwalanego driftem.

**⚠ Kluczowe dla ZADANIA B: addytywna personalizacja per-kurier była JUŻ testowana i dostała werdykt NEGATYWNY `[ZWERYFIKOWANE]`.** Advisory E-7 (`/root/ziomek-advisory/07_REPLAY_RESULTS.md`): wszystkie 6 wariantów per-kurier POGARSZAJĄ MAE out-of-time (−0.7…−2.1%), bo wariancja wewnątrz-kurierska ≫ sygnał między-kurierski na poziomie pojedynczej dostawy. Zamiennik „GO" = korekta per-KOMÓRKA floty (daypart × solo/worek), MAE 10.39→10.04 (+3.4%), dziś w cieniu jako „ETA warunkowe" (+5.14% MAE hold-out, `ENABLE_ETA_CELL_RESIDUAL_CORRECTION=OFF`). Prawdziwa personalizacja per-kurier jest **zablokowana** na adopcji GPS floty (5b) — potrzebna instrumentacja per-leg (geofence).

**Baseline jakości (A.6) — liczby robust `[ZWERYFIKOWANE]`:**
- **End-to-end ETA dostawy przy przypisaniu** (obietnica z chwili decyzji, `eta_calibration_log`, matched, ogon obcięty |err|≤45): **bias +9.3 min** (dostawa później niż obiecano), **MAE 12.5 min**, mediana +8.0, **±10 min = 51%**, **±15 min = 67%**. (Surowo z ogonem: MAE 23.3, RMSE 351 — zdominowane artefaktami, patrz A.5.)
- **Żywe ETA dostawy (ostatnia predykcja przed dostawą)** jest DOBRE i lekko pesymistyczne: MAE **7.4 min**, bias −6.4, ±10 = 76%.
- **Noga odbioru — obietnica koordynatora (`czas_kuriera`) BIJE system:** koordynator MAE **7.1 min** vs system-przy-przypisaniu MAE **11.8 min** (Wilcoxon na sparowanych p<1e-16). Człowiek jest lepszy w przewidzeniu realnego odbioru.
- **Naiwny baseline (mediana kuriera)** czasu trwania dostawy: MAE **9.6 min** — mocny, system pokonuje go tylko marginalnie i nieistotnie (n=128, p=0.25).

Wniosek nadrzędny: największa dziura to **noga ODBIORU** (system optymistycznie zakłada, że kurier dojedzie po odbiór wcześniej niż realnie — advisory: ~18 min poślizgu pod obciążeniem), a nie noga jazdy/dostawy. To definiuje, gdzie ZADANIE B może realnie dołożyć wartość.

---

## A.0 Rekonesans

**Metoda `[ZWERYFIKOWANE]`.** Zgodnie z regułą repo („NIE skanuj repo") wszedłem po dokumentach nawigacyjnych (`CLAUDE.md` → `docs/CODEMAP.md` → `docs/ARCHITECTURE.md`), potem empirycznie po plikach ETA i danych. Repo na `master`, ostatni commit `5aa2f9f` (2026-07-07 20:43) dotyczy `calib_maps.eta_cell_residual_correct`.

**Zidentyfikowane punkty wejścia ETA (`CODEMAP.md §3` + grep) `[ZWERYFIKOWANE]`:**
- Rdzeń obliczeń: `route_simulator_v2.py` (`_simulate_sequence`, `simulate_bag_route_v2`, `_plan_from_sequence`), `tsp_solver.py` (OR-Tools), `osrm_client.py`, `common.py` (tabela ruchu + DWELL + stałe).
- Łańcuch/cache/kalibracja: `chain_eta.py`, `calib_maps.py`, `live_eta_cache.py`, `eta_residual_infer.py`, `ml_inference.py`.
- Loggery prawdy: `eta_calibration_logger.py`, `tools/ziomek_pred_calibration.py`, `drive_min_calibration.py`.
- Generatory map: `tools/eta_quantile_calib.py`, `tools/restaurant_prep_bias.py`, `tools/eta_cell_residual_build.py`, `tools/eta_load_aware_calibrate.py`, `tools/eta_r3_*`.
- Konsumpcja decyzyjna: `feasibility_v2.py` (R6/SLA), `dispatch_pipeline.py`, `scoring.py`, `shadow_dispatcher.py`.

---

## A.1 Algorytm / model ETA

### A.1.1 Rodzaj: arytmetyka na trasie, nie regresja/sieć `[ZWERYFIKOWANE]`

ETA nie jest liczone jednym wzorem end-to-end ani modelem. Jest **symulacją przejścia po sekwencji przystanków** (`route_simulator_v2.py:559 _simulate_sequence`). Sekwencję wybiera OR-Tools TSP dla worka ≥2 (`ENABLE_V326_OR_TOOLS_TSP=true`), a greedy dla worka <2 (`route_simulator_v2.py:1035 _greedy_plan`, `:1160 _ortools_plan`).

**Pseudokod przepisany wprost z kodu (`_simulate_sequence`, l. 571-651):**
```
t = now                                  # lub earliest_departure dla pre-shift (l.281-285)
dla idx w sequence:
    t += leg_min(current, idx)           # CZAS JAZDY nogi (patrz A.1.2)
    jeśli node == PICKUP:
        arrival_t = t                     # surowy przyjazd (do kary wait_courier)
        jeśli t < pickup_ready_at:         # jedzenie jeszcze się robi
            t = pickup_ready_at            # CZEKANIE pod restauracją (L2)
        t += DWELL_PICKUP_FLAT_MIN (=1.0)  # postój obsługi (l.589)
        pickup_at[oid] = t
    jeśli node == DELIVERY:
        # podłogi zabezpieczające (l.601-646):
        jeśli ENABLE_DROP_TIME_CONSTRAINT i order nieodebrany:
            t = max(t, pickup_ready_at + DWELL_PICKUP_FLAT)      # drop nie przed gotowością
        jeśli ENABLE_PICKED_UP_DROP_FLOOR i order picked_up:
            t = max(t, picked_up_at + osrm(pickup→drop) + DWELL_DROPOFF)  # realny drop niesionego
        t += DWELL_DROPOFF (per-tier, l.647)  # postój dostawy (L3)
        delivered_at[oid] = t
total_min = (t - now)
```
`predicted_delivered_at[oid]` = powyższe `t`; `per_order_delivery_times[oid]` = `(delivered_at − kotwica_termiczna_R6)` w minutach, gdzie kotwica = `pickup_ready_at` (nowe/nieodebrane) lub `picked_up_at` (niesione) — `r6_thermal_anchor` (l.702) `[ZWERYFIKOWANE]`.

### A.1.2 Czas jazdy nogi `leg_min` `[ZWERYFIKOWANE]`

`leg_min` = `(OSRM duration_s / 60) × drive_speed_mult` (`route_simulator_v2.py:416`). OSRM (`osrm_client.route`, `:5001`) zwraca **free-flow**; mnożnik ruchu aplikowany jest w `osrm_client._apply_traffic_multiplier` gdy `ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER` (definicja `common.py:3905`, `[NIEZWERYFIKOWANE co do wartości env — flaga czytana z env, nie z flags.json; historycznie ON]`). Fallback: `haversine_km × 2.5 × mnożnik` (`chain_eta.py:99-111`, mediana empiryczna 2.461 z 195 zleceń).

**Tabela mnożnika ruchu `V326_OSRM_TRAFFIC_TABLE` (`common.py:1034`), rekalibrowana 2026-06-05/06-12 z tropów GATE B `[ZWERYFIKOWANE]`:**

| Dzień | Przykładowe kubełki (godzina lokalna → ×) |
|---|---|
| Pn–Pt | 0-9:1.0 · 10-12:1.25 · 12-13:1.40 · **13-14:1.50** · 14-15:1.35 · **15-17:1.55** · 17-20:1.25 · 20-21:1.10 · 21-24:1.05 |
| Sobota | 0-12:1.0 · 12-13:1.30 · 13-16:1.20 · **16-17:1.55** · 17-18:1.45 · 18-21:1.25 |
| Niedziela | 0-11:1.0 · **11-12:1.50** · 12-13:1.40 · 13-15:1.35 · 15-16:1.45 · 16-19:1.30 · 19-20:1.15 |

Istnieje wariant `get_traffic_multiplier_v2` z korektą per-kubełek-dystansu (`common.py:1164`, `ENABLE_V326_DISTANCE_BIN_TRAFFIC_BOOST` — **OFF, shadow**).

### A.1.3 Postoje (DWELL) — residuum ETA per-tier, baked-in `[ZWERYFIKOWANE]`

`common.py:2662`. Postój **odbioru** = płaskie 1.0 min (czysta obsługa; czekanie na jedzenie liczone osobno). Postój **dostawy** `DWELL_BY_TIER` — **uczony z `eta_calibration_log`** (rekalibracja 2026-06-10, mediana błędu predykcji per tier na 7496 dopasowanych rekordach):

| tier | dropoff (min) | uzasadnienie w kodzie |
|---|---|---|
| gold | 1.5 | Ziomek przeszacowywał gold ETA o ~1.2 min |
| std+ | 2.5 | −0.5 |
| std | 4.5 | +1.0 |
| slow | 6.5 | niedoszacowanie ~2.3 min, breach 18% |
| new | 6.5 | niedoszacowanie + ogon p90 +23 min |

To najdalej idąca istniejąca „personalizacja" — **per KLASA kuriera, baked-in (bez flagi), zawsze aktywna** przez `dwell_for_tier` w produkcyjnej ścieżce feasibility (`feasibility_v2.py:842`).

### A.1.4 Mnożnik tempa jazdy per-tier — istnieje, WYŁĄCZONY `[ZWERYFIKOWANE]`

`DRIVE_SPEED_MULT_BY_TIER` (`common.py:2704`): gold 0.78 / std+ 0.82 / std 0.82 / slow 1.0 / new 1.0 (<1.0 = szybciej). Bramka `ENABLE_DRIVE_SPEED_TIER_CORRECTION` = **False w `flags.json`** → w praktyce mnożnik = 1.0 (inert). Rollback = flaga OFF (bez restartu).

### A.1.5 Które sygnały wejściowe są używane (tabela zmiennych) `[ZWERYFIKOWANE]`

| Sygnał | Rola w ETA | Źródło / pozyskanie | plik:linia |
|---|---|---|---|
| pozycja kuriera (GPS/last-pos/synthetic) | punkt startu nogi L1 | `courier_resolver.dispatchable_fleet` | `courier_resolver.py` |
| `pickup_ready_at` | czekanie L2 + kotwica R6 | panel gastro (`czas_odbioru` prep min) | `_simulate_sequence:582`, `r6_thermal_anchor:722` |
| **(a) czas żądany przez restaurację / (b) obietnica koordynatora `czas_kuriera`** | zamrożone okno odbioru R27 ±5; podłoga ETA odbioru (display) | panel (`czas_kuriera` HH:MM, R-DECLARED-TIME) | `feasibility_v2` (R-DECLARED), `ENABLE_PROPOSAL_ETA_FLOOR_TO_COMMITTED` |
| **(c) historyczny odbiór/dostawa** | rekalibracja DWELL/tabeli/map (offline), **NIE** w gorącej predykcji pojedynczej | `sla_log.jsonl`, `eta_calibration_log.jsonl` | A.3 |
| **(d) czas oczekiwania w restauracji** | tylko przez skok do `pickup_ready_at`; jako korekta = mapa prep-bias (SHADOW) | `restaurant_prep_bias.json` | `calib_maps.prep_bias_for` |
| OSRM free-flow duration/distance | rdzeń jazdy | OSRM `:5001` | `osrm_client.route` |
| mnożnik ruchu godzina×dzień | korekta jazdy | `V326_OSRM_TRAFFIC_TABLE` | `common.py:1034` |
| tier kuriera (klasa) | DWELL dostawy + (OFF) tempo jazdy | grafik/ranking | `DWELL_BY_TIER`, `speed_mult_for_tier` |
| rozmiar worka / bundling | sekwencja TSP, kotwice, cap R6 | `orders_state` | `route_simulator_v2` |

**Segmentacja:** ETA jest liczone **segmentami wewnątrz spaceru** (L1 dojazd do restauracji, L2 czekanie na wydanie, L3 dojazd + postój u klienta), ale wyjściem jest jeden `predicted_delivered_at` na zlecenie. Osobne, jawne ETA per segment **nie są dziś emitowane** `[ZWERYFIKOWANE]` — to kluczowa luka dla kalibracji per-leg (patrz A.4/B).

### A.1.6 Warstwy kalibracji nałożone na bazowe ETA `[ZWERYFIKOWANE]`

| Warstwa | Co robi | Flaga | Stan |
|---|---|---|---|
| kwantyl ETA (p80) | poluzowanie cap R6-bagcap dla gold | `ENABLE_ETA_QUANTILE_R6_BAGCAP` | **LIVE (jedyny decyzyjny)** `feasibility_v2:1141` |
| kwantyl ETA (`travel_min_cal`) | korekta travel dla no_gps/pre_shift | `ENABLE_ETA_QUANTILE_SHADOW` | LIVE-shadow `dispatch_pipeline:4249/4263` |
| prep-bias restauracja×slot | `effective_ready = ready + bias` | `ENABLE_PREP_BIAS_TABLE`=OFF / `_SHADOW`=ON | SHADOW |
| cell-residual (daypart×solo/worek + restauracja) | korekta ADDYTYWNA na obietnicę | `ENABLE_ETA_CELL_RESIDUAL_CORRECTION`=OFF | SHADOW (od 07-07 06:47) `shadow_dispatcher:560` |
| R3 residual LGBM (v1 + v2_drop) | korekta residualna | `ENABLE_ETA_R3_SHADOW`/`_DROP_SHADOW`=ON | SHADOW `eta_calibration_logger:290` |
| drive_min v2 (offset per tier×peak) | korekta jazdy | `ENABLE_DRIVE_MIN_CALIBRATION_V2`=OFF | SHADOW (`main_path_active=False` na 100%) |
| chain-ETA (R-07) | ETA łańcuchowa od czasu ustalonego | `ENABLE_V326_R07_CHAIN_ETA` (env, default 0) | **OFF** (anulowana — „pesymistyczna vs plan") |

---

## A.2 Architektura i przepływ danych

### A.2.1 Biblioteki i zależności `[ZWERYFIKOWANE]`
Venv `dispatch` (py3.12): **numpy 2.4.4, pandas 3.0.2, scikit-learn 1.8.0, lightgbm 4.6.0, scipy 1.17.1, ortools 9.15.6755**. HTTP wyłącznie przez stdlib `urllib` (brak `requests`/`httpx`).

### A.2.2 API zewnętrzne `[ZWERYFIKOWANE]`
- **OSRM self-hosted** `:5001` (Docker `ghcr.io/project-osrm/osrm-backend`, kontener `osrm-server`) — routing free-flow (jazda). Brak kluczy/limitów (self-host).
- **Nominatim/OpenStreetMap** — geokod + cache lokalny (`geocode_cache.json`).
- Google Distance Matrix — wzmiankowane w `CLAUDE.md` jako historyczne; **live router = OSRM** `[HIPOTEZA co do statusu Google — brak wywołań w gorącej ścieżce ETA]`.
- **Pogoda/ruch as-known: BRAK** live źródła ruchu/pogody — ruch to statyczna tabela godzinowa, pogody nie ma w ogóle `[ZWERYFIKOWANE — brak modułu pogodowego w grep]`. To istotny kandydat na cechę w ZADANIU B.

### A.2.3 Gdzie trzymane jest ETA `[ZWERYFIKOWANE]`
- Predykcja per decyzja → `logs/shadow_decisions.jsonl` (42 MB bieżący + 108 MB `.1`), w polu `best.plan.predicted_delivered_at` / `per_order_delivery_times`.
- Kanon planu/kolejności → `courier_plans.json` (atomic).
- Świeży cache ETA dla powierzchni → `dispatch_state/live_order_eta.json` (`live_eta_cache.py`, TTL 20 min) — spójność apka/konsola/Telegram.
- Stan zleceń → `orders_state.json`. Bazy SQLite: `courier_api.db` (apka/GPS), `events.db`, `fleet_analytics.db`. Postgres `nadajesz_panel@:5433` (konsola/analizy) `[NIEZWERYFIKOWANE co do zawartości — nie odpytano, kanon ETA = pliki JSONL]`.

### A.2.4 Przepływ end-to-end (diagram)
```
panel gastro ──poll HTML──> panel_watcher ──> orders_state.json
                                   │ event NEW_ORDER
                                   ▼
                       shadow_dispatcher._tick
                                   │
              dispatch_pipeline._assess_order_impl (core.decide)
                                   │
          feasibility_v2 (R6/SLA, kwantyl p80 R6-bagcap)   scoring
                                   │
              route_simulator_v2.simulate_bag_route_v2
                 └─ tsp_solver (OR-Tools)  ─ osrm_client(:5001)×tabela ruchu
                                   │  predicted_delivered_at / pickup_at
                                   ▼
        ┌── shadow_decisions.jsonl (kanon predykcji)
        ├── courier_plans.json (kolejność/plan)
        └── live_order_eta.json (cache świeżego ETA)
                                   │
              konsola gps.nadajesz.pl/admin  +  apka kuriera :8767
                                   │
   [OFFLINE, poza tickiem]  sla_log.jsonl (rzeczywisty odbiór/dostawa)
        └── eta_calibration_logger (30 min) + ziomek_pred_calibration (3 min)
             └── mapy: eta_quantile_map / restaurant_prep_bias / eta_cell_residual (cień)
```

---

## A.3 Procesy w tle poprawiające ETA

Stan zweryfikowany empirycznie 2026-07-07 (`systemctl`, mtime plików, liczenie rekordów; crony biegną w **UTC** — `CRON_TZ` nie honorowany).

### A.3.1 ŻYWE `[ZWERYFIKOWANE]`

| Proces | Jednostka | Harmonogram | Co robi | Dowód liveness | Wpływ |
|---|---|---|---|---|---|
| `eta_calibration_logger.py` | `dispatch-eta-calibration.timer` | 30 min | join predykcja↔rzeczywistość → `eta_calibration_log.jsonl` + R3 shadow | last 21:03:26 OK; **n_7d=1611** | pomiar |
| `ziomek_pred_calibration.py` | `dispatch-ziomek-pred-calibration.timer` | 3 min | rozjazd odbiór/dostawa (assign+last) → `ziomek_pred_calibration.jsonl` | last 21:07:32 OK; **n_7d=1603** | pomiar, zero wpływu |
| `eta_quantile_calib.py` | cron `35 4 * * *` (UTC) | dziennie 04:35 | mapa kwantylowa → `eta_quantile_map.json` | mtime 04:35:01 dziś | **live-shadow** (`travel_min_cal`) |
| `restaurant_prep_bias.py` | cron `15 4 * * *` | dziennie 04:15 | prep-bias restauracja×slot → `restaurant_prep_bias.json` | mtime 04:15:03 dziś | SHADOW (flag OFF) |
| `eta_calibration_shadow.py` | `dispatch-retro-learning.service` | dziennie 04:30 | breach_rate kalibracji na przeszłości → `eta_calibration_shadow.jsonl` | last 04:30:01 OK; n_7d=7 | shadow-eval |
| `prep_bias_shadow_monitor.py` | `dispatch-prep-bias-shadow-monitor.timer` | dziennie 05:00 | precision/recall prep-bias → `prep_bias_shadow_metrics.jsonl` | last 05:00:02 OK; n_7d=7 | shadow-eval |
| `drive_min_calibration.py` | inline w pipeline | live | offset/floor jazdy per tier×peak → `drive_min_calibration_log_v2.jsonl` | **n_7d=32423**, `main_path_active=False` na 100% | SHADOW |

### A.3.2 MARTWE / ZAMROŻONE / RĘCZNE `[ZWERYFIKOWANE]`

| Proces | Stan | Dowód |
|---|---|---|
| `tools/eta_cell_residual_build.py` | **brak schedulera** — build ręczny | mtime mapy 07-07 06:46 = flip T22, brak crona/timera/at |
| `tools/eta_load_aware_calibrate.py` | zamrożony + flaga OFF | `eta_load_aware_calib.json` mtime 07-05 18:02; `ENABLE_ETA_LOAD_AWARE=false` |
| `eta_residual_infer` (model LGBM) | **model zamrożony** | `eta_residual_v1/model.txt` 2026-06-18; `v2_drop` 2026-06-20; brak retreningu |
| `tools/eta_r3_forward_val/fix_skew/compare_variants` | ręczne (offline) | brak crona/timera |
| `tools/prep_bias_build.py`, `tools/restaurant_prep_delay_build.py` | legacy / tylko-pomiar | `prep_bias_table.json` Jun 20; delay build „T2.1 zero wpięcia" |
| `dispatch-bundle-calib-review.timer` | **MARTWY** (styczny do ETA) | inactive/dead, last 07-02 |

### A.3.3 Feedback loop / retraining / drift / A-B — co JEST, czego NIE MA `[ZWERYFIKOWANE]`
- **JEST:** ciągłe logowanie outcome (3 min / 30 min), dzienny rebuild map korekcyjnych, dzienny shadow-eval breach_rate, prymitywne shadow-A/B dwóch wariantów R3 (v1 vs v2_drop) w logu, offline replay-verdicts (`*_verdict.txt`).
- **NIE MA:** automatycznego retreningu modelu ETA (zamrożony od 06-18); schedulera dla `eta_cell_residual_build` i `eta_load_aware`; prawdziwego **online A/B** (wszystko to offline replay uruchamiany ręcznie); alertowania driftu ETA / auto-flipu (decyzja o flipie ręczna „po 5-7 dniach trendu").
- **Jednym zdaniem:** pętla *pomiaru* jest żywa i gęsta; pętla *sterowania* (retrening, wpięcie korekt, drift→flip, online A/B) jest w większości **ręczna lub wyłączona flagą**. Kalibracja działa w trybie obserwacyjnym.

---

## A.4 Backlog i plany

### A.4.1 ⚠ Werdykt E-7: per-kurier addytywny residual = NO-GO `[ZWERYFIKOWANE]`
`/root/ziomek-advisory/07_REPLAY_RESULTS.md`: 6 wariantów addytywnego residuala per-kurier POGARSZA MAE out-of-time (−0.7…−2.1%), także na residualu od predykcji silnika i w oknie stacjonarnym. Powód: **wariancja wewnątrz-kurierska ≫ sygnał między-kurierski** na poziomie pojedynczej dostawy. Analiza źródłowa `02d_courier_eta_analysis.md`: 51 dni / 13 969 dostaw, różnice per-kurier do **2×** w tym samym warunku, stabilne split-half — sygnał ISTNIEJE, ale nie da się go wyciągnąć addytywnie na poziomie pojedynczego zlecenia.

### A.4.2 GO: per-KOMÓRKA floty + restauracja = „ETA warunkowe" `[ZWERYFIKOWANE]`
- Korekta per-komórka (daypart × solo/worek): MAE 10.39→10.04 (**+3.4%**) — `07_REPLAY_RESULTS.md`, roadmapa `08_ROADMAP.md` W0.5.
- Pełne „ETA warunkowe" (solo/worek + restauracja): **hold-out paired bootstrap MAE +5.14%, 95% CI delty [4.06%; 6.23%] — nie obejmuje 0**; underest 30.5→29.8; breach 10.8% bez wzrostu (`HANDOFF_TMUX26_TURA2.md`).
- **Stan:** W CIENIU od 2026-07-07 06:47 UTC (`eta_cell_corrected_min` compute-always), `ENABLE_ETA_CELL_RESIDUAL_CORRECTION=OFF` → korekta NIE idzie w obietnicę. Mapa `eta_cell_residual_map.json` (v2, 8 komórek, 52 restauracje, 13 272 rekordy).
- ⚠ Niuans: liczba +5.14% mierzona PRZED fixem HTML-escape lookupu restauracji (3 restauracje z encjami, np. `Kumar&#039;s`) — karta MUSI być re-zebrana na świeżym oknie 2 d (`S27C_eta_fix.md`, commit `b3e91da`, dziś domknięty `5aa2f9f`).

### A.4.3 Pełny spec personalizacji per-kurier — istnieje, zablokowany `[ZWERYFIKOWANE]`
`06a_idea_backlog.md` TEMAT 11 (Q13): projekt `courier_eta_profile` C-01…C-10 — residual addytywny + EWMA 14 d + prior hierarchiczny (shrinkage k=20-25, min-n 30-40) + rozdział obietnica≠bramka + kwarantanna driftu (>5 min ∧ n≥40). **Werdykt: test E-7 obalił formę addytywną.** Ścieżka ratunkowa (W3.4): **per-leg instrumentacja z geofence (5b) → residual na DWELL, re-test po ≥30 d** — dziś zablokowana (adopcja GPS floty ~1 kurier). Trener `tools/pickup_slip_model.py` (LGBM + offsety per-kurier ze shrinkage, nowy kurier auto) istnieje, ale **timer nightly świadomie NIE zainstalowany** (OOS −1.7%).

### A.4.4 Kluczowa lekcja: optymizm = POŚLIZG ODBIORU, nie jazda `[ZWERYFIKOWANE]`
`ziomek-calibration-2026-06-29.md` + `eod_drafts/2026-06-29/calibration/INDEX.md`: wobec realnej bazy obietnicy **noga jazdy ma ~0 min błędu**; cały optymizm to **kurier dojeżdża po odbiór ~18 min PÓŹNIEJ** niż silnik założył (kolejkowanie pod obciążeniem). Wcześniejsze „jazda ~2× OSRM" = artefakt złej kolumny (`predicted_drive_min` = surowy OSRM ~0.5× realu, nie zasila obietnicy). Poślizg zależy bardziej od OBCIĄŻENIA niż od pory (`LOAD>CLOCK`, η² 0.053 vs 0.027). Dodatkowa śruba (`eod_drafts/2026-07-06/SLIP_DECOMPOSITION_raport.md`): **dorzucanie zleceń po decyzji** (+9.3 med; inna restauracja +14.8; <15 min do odbioru +39).

### A.4.5 ADR rządzące wdrażaniem `[ZWERYFIKOWANE]`
- **ADR-001** HARD przed SOFT; SOFT nigdy nie osłabia HARD → naprawiamy PREDYKCJĘ, nie luzujemy R6-35.
- **ADR-002** shadow-first, flip za ACK; „miernik bywa nieskalibrowany i KŁAMIE".
- **ADR-004** flagi 3 światy (silnik `flags.json` / panel / apka).

### A.4.6 Sporna furtka gold `[ZWERYFIKOWANE]`
`ENABLE_ETA_QUANTILE_R6_BAGCAP=true` (jedyna decyzyjna flaga ETA). Advisory T2.3: usunięcie furtki gold to decyzja polityczna, nie fix predykcji — ETA warunkowe odzyskuje tylko 27/127 (21%) „fast", bo prognozy fast(127) i late(37) się nakładają (niepewność nieredukowalna). Decyzja odłożona (todo_master: OFF teraz / zostaw 127:37 / re-pomiar po GPS+per-leg).

---

## A.5 Inwentaryzacja bazy historycznej

### A.5.1 Źródła prawdy (odkryte empirycznie) `[ZWERYFIKOWANE]`

| Plik | n | Zakres dat | Rola | Klucze czasowe |
|---|---|---|---|---|
| `sla_log.jsonl` (5 MB) | 14 945 | od 2026-05-08 | **rzeczywisty odbiór/dostawa** | `picked_up_at`, `delivered_at` (naiwny Warsaw), `delivery_time_minutes`=deliv−pick |
| `shadow_decisions.jsonl` (42+108 MB) | — | ~14 dni retencji + `.1` | **predykcje** per kandydat | `best.plan.predicted_delivered_at`, `per_order_delivery_times`, `pickup_at` (ISO UTC) |
| `eta_calibration_log.jsonl` (13 MB) | 14 532 | 2026-05-17 → 07-07 (51 dni) | join pred↔real (matched-courier) | `eta_error_min`, `real_delivery_min`, `predicted_delivery_min` |
| `ziomek_pred_calibration.jsonl` (2 MB) | 3 346 | 2026-06-23 → 07-07 | rozjazd odbiór/dostawa per-cid + obietnica | `rozjazd_odbior/dostawa_{assign,last}`, `czas_kuriera_hhmm` |
| `learning_log.jsonl` (54+107 MB) | — | żywy | trail TAK/NIE/KOORD | — |
| `/root/ziomek-advisory/data/panel_csv_oczekiwanie_mission.csv` | 6 737 (28 dni) | advisory | **stoper „oczekiwanie odbiór" Rutcom** (status 4) = wiarygodny czas prep/odbioru | z panelu, nie z apki |

### A.5.2 Mapowanie kluczowych czasów zlecenia `[ZWERYFIKOWANE]`
- (a) **czas żądany przez restaurację / (b) obietnica koordynatora** → `czas_kuriera` (HH:MM Warsaw) = zamrożona deklaracja odbioru; `czas_odbioru` (int prep min: <60 elastyk, ≥60 czasówka).
- (c) **faktyczny odbiór** → `picked_up_at` (sla_log / orders_state, naiwny Warsaw).
- (d) **faktyczna dostawa** → `delivered_at` (naiwny Warsaw).
- (e) **oczekiwanie w restauracji** → NIE liczone bezpośrednio per zdarzenie; proxy = skok do `pickup_ready_at`; wiarygodny pomiar tylko z **stopera Rutcom „oczekiwanie odbiór"** (status 4), nie z apki bez GPS.
- Kontekst: godzina/dzień/weekend (z timestampów) ✅; bundling/rozmiar worka ✅ (`bag_size`, `max_bag`); restauracja/kurier/strefa ✅; **pogoda/ruch live: BRAK** (tylko statyczna tabela); **dystans OSRM per zlecenie: NIE w logach kalibracji** (trzeba joinu z `shadow_decisions`); **liczba jednoczesnych zleceń floty: NIE zapisana wprost** (proxy: `n_shadow_records`, `bag_size`).

### A.5.3 Statystyki jakości i PUŁAPKI `[ZWERYFIKOWANE]`
`eta_calibration_log` (n=14 532): 119 restauracji, 53 kurierów; **matched_courier=True = 56%** (44% to fallback na `best` — realny kurier ≠ proponowany → pomijane w metryce); %NULL: `eta_error_min` 4.9%, `real_delivery_min` 2.1%, `restaurant` 1.0%.

`ziomek_pred_calibration` (n=3 346): 35 kurierów; **`rozjazd_odbior_last` 96.1% NULL, `pickup_pred_last` 95.2% NULL** — odbiór następuje szybciej niż 3-min tick zdąży złapać świeżą predykcję (analiza „last" dla odbioru = tylko n≈130, niereprezentatywna); `rozjazd_dostawa_last` tylko 2.8% NULL (dostawa daje czas na refresh). Rozkład klasy: 3 276 bundle vs 70 solo (kurierzy niemal zawsze wożą ≥2 → „solo" = rzadka anomalia jednego zlecenia).

**Pułapki jakości (fundament dla ZADANIA B):**
1. **Ogon artefaktów w `eta_calibration_log`:** 4.4% rekordów |err|>45 min, 2.3% >60, 0.8% >120. Ujemny ogon (53 rek. <−60 min) skupiony w **worku 4+ (42/53) i strategii `greedy_fallback`** — `predicted_delivered_at` osadzone daleko w przyszłości (replan/harmonogram) vs realna wcześniejsza dostawa. To rozsadza średnią i RMSE (351 min!). **Metryki muszą być robust** (mediana, MdAE, %±, trim).
2. **Kotwice niespójne:** `real_delivery_min` = pickup→deliver; `predicted_delivery_min` = ready→deliver → **nie wolno ich odejmować wprost** (różnica = poślizg odbioru). Czysta metryka end-to-end = `eta_error_min` (timestamp−timestamp).
3. **Naiwny-Warsaw-jako-UTC (+2h):** `picked_up_at/delivered_at` to naiwny Warsaw, `predicted_*` to ISO UTC — mieszanie stref daje ±2h błędy. Loggery to obsługują, ale każda nowa analiza musi (advisory pułapka).
4. **Klik/button-truth zawyża wiek dostawy** med +2.08 min; pickup-debias „−47%" = inflacja (fizycznie −8 pp) — wiarygodny czas tylko z GPS/`gps_delivery_truth.jsonl` lub stopera Rutcom.
5. **Backtest ≠ live:** raportowane redukcje (np. drive_min 13.64→7.88) pochodziły z BACKTESTU, nie z live (fałszywie zielona bramka).

---

## A.6 Ilościowy baseline jakości obecnego ETA

> Metody: `scipy 1.17.1`. Podział wyłącznie po czasie (loggery zbierają forward). Pseudonimizacja `KURIER_###` = ranking wg liczby zleceń. Skrypt: `/tmp/…/scratchpad/a6_baseline.py` (read-only).

### A.6.1 End-to-end błąd ETA dostawy (obietnica z chwili decyzji) `[ZWERYFIKOWANE]`
Źródło: `eta_calibration_log`, matched_courier=True, `eta_error_min` = `delivered_at − predicted_delivered_at` (dodatni = dostawa PÓŹNIEJ niż obiecano).

| Zbiór | n | bias | MAE | RMSE | mediana | ±5 min | ±10 min | ±15 min |
|---|---|---|---|---|---|---|---|---|
| ALL surowo (z ogonem) | 8 139 | +2.86 | 23.28 | **351** | +8.49 | 27.1% | 48.6% | 63.9% |
| **ROBUST (trim \|err\|≤45)** | 7 780 | **+9.34** | **12.46** | 16.1 | +8.03 | 28.3% | **50.9%** | **66.9%** |
| elastyk (robust\*) | ~7 400 | +9.3 | ~12.5 | — | +8.4 | 27% | 49% | 64% |
| czasówka (surowo) | 367 | +13.09 | 17.22 | 28.69 | +9.89 | 23.2% | 45.2% | 61.6% |

\* MdAE (mediana \|błędu\|) robust = **9.74 min**. **Interpretacja:** obietnica dostawy z chwili przypisania jest systematycznie **optymistyczna o ~8–9 min** i tylko co druga trafia w ±10 min. To baseline „do pobicia".

### A.6.2 Rozkład błędu wg kontekstu (elastyk, `eta_error_min`) `[ZWERYFIKOWANE]`
> ⚠ bias/RMSE w komórkach z ogonem (offpeak, h13-14, h17, h21, worek 4+, weekend) zniekształcone artefaktami z A.5.3 — czytać MEDIANĘ i %±.

**Wg pory (bucket):** peak MdAE~med +8.3, ±15=66% · shoulder med +9.2, ±15=62% · offpeak med +7.3 (bias −16 = artefakt).
**Wg godziny (mediana błędu, n≥30):** rośnie rano (h9 +13.0, h10 +9.3), spada wieczorem (h18 +7.0/±15=70%, h19 +6.7, h22 +6.1). Najgorsze trafienia: h9 (±15=50%), h15 (±15=57%).
**Wg dnia:** Pn–Pt mediana +8…+10 (±15 60-67%); weekend mediana +7 ale bias ujemny = ogon (Sob/Ndz mają najwięcej worków 4+).
**Wg bundlingu (bag_size):** solo(1) med +9.4 (±10=49%) · **worek 2 najlepszy: med +7.2, ±10=55%, ±15=70%** · worek 3 med +8.6 · **worek 4+ najgorszy: med +9.4, ±10=42%, bias −18 = ciężki ogon**.
**Wg total_duration planu (proxy dystansu):** <20 min → med +13.5 (±15=53%, najgorzej) · 20-35 → med +8.3 (±15=68%) · 35-50 → med +8.3 (±15=66%) · 50+ → med +8.3 ale ogon. Krótkie trasy = najbardziej niedoszacowane.

### A.6.3 Per-kurier `[ZWERYFIKOWANE]` (heterogeniczność = motyw ZADANIA B)
32 kurierów z n≥50 (z 49). Biasy end-to-end (elastyk) rozrzucone, ale **zaśmiecone ogonem** (KURIER_030 bias −148, KURIER_024 −100 = artefakty worka 4+). Wiarygodniejsza jest Część 2 (niżej, czysty logger):

Na czystym `ziomek_pred_calibration` (rozjazd DOSTAWY „last", n≥50, 21 kurierów) **wszystkie biasy tego samego znaku (−1.7…−9.1 min, pesymistyczne), różna wielkość** — np. KURIER_007 bias −1.71/MAE 4.43 (±5=75%) vs KURIER_018 bias −8.03/MAE 8.73 (±5=38%). To realny, spójny sygnał między-kurierski (potwierdza analizę advisory „różnice do 2×"), ale advisory E-7 pokazało, że **addytywna korekta per-kurier nie generalizuje out-of-time** — wariancja per-dostawa zjada sygnał.

### A.6.4 System vs obietnica koordynatora vs naiwny (Część 2, `ziomek_pred_calibration`) `[ZWERYFIKOWANE]`

**Noga ODBIORU** (rozjazd = real − pred; obietnica koordynatora = `picked_up − czas_kuriera`):

| Predykcja | n | bias | MAE | RMSE | ±5 min | ±10 min | ±15 min |
|---|---|---|---|---|---|---|---|
| Ziomek odbiór (assign) | 2 830 | +2.52 | 11.82 | 17.60 | 36.3% | 60.5% | 73.5% |
| Ziomek odbiór (last) | 130 | −4.45 | 9.77 | 13.74 | 39.2% | 66.2% | 79.2% |
| **Koordynator (`czas_kuriera`)** | 3 307 | +5.16 | **7.11** | 10.31 | **47.9%** | **77.1%** | **90.8%** |

→ **Obietnica koordynatora jest wyraźnie dokładniejsza od systemowego ETA odbioru** (MAE 7.11 vs 11.82; na sparowanych n=130 „last" koordynator +1.74 min lepszy, Wilcoxon p<1e-16). To najmocniejszy sygnał „gdzie człowiek bije model" — i wskazuje, że kalibracja nogi odbioru (poślizg) to główny target.

**Noga DOSTAWY** (rozjazd dostawy):

| Predykcja | n | bias | MAE | RMSE | ±10 min | ±15 min |
|---|---|---|---|---|---|---|
| dostawa assign (obietnica) | 2 879 | +6.45 | 15.91 | 23.68 | 44.3% | 61.0% |
| **dostawa last (żywe ETA)** | 3 253 | −6.35 | **7.39** | 11.12 | **76.1%** | **87.0%** |

→ Obietnica dostawy z chwili przypisania jest słaba (MAE 16, optymistyczna +6.5), ale **ciągle odświeżane „żywe ETA" zbiega do dobrego** (MAE 7.4, pesymistyczne −6.4). Powierzchnie (apka/konsola) pokazują „last" — stąd dobre wrażenie w praktyce, mimo słabej obietnicy początkowej.

**Naiwny baseline (mediana kuriera, leave-one-out) — czas trwania dostawy (deliver−pickup):**

| Predyktor | n | bias | MAE | RMSE | ±10 min | ±15 min |
|---|---|---|---|---|---|---|
| Naiwny (mediana kuriera LOO) | 3 305 | +2.20 | **9.63** | 13.13 | 62.5% | 83.0% |
| System (pred duration) | 128 | −2.31 | 7.97 | 11.35 | 72.7% | 83.6% |

→ Na sparowanych (n=128) system jest lepszy o 0.94 min, ale **nieistotnie** (paired t p=0.245). **Naiwna mediana kuriera to mocny baseline** (MAE 9.6) — każde nowe narzędzie musi go realnie pobić na hold-oucie, a nie zakładać.

### A.6.5 Podsumowanie baseline (liczby do pobicia w ZADANIU B)
| Metryka | Wartość | Uwaga |
|---|---|---|
| End-to-end ETA dostawy @assign, MAE (robust) | **12.5 min**, bias +9.3 | optymizm; ±10=51% |
| Żywe ETA dostawy @last, MAE | 7.4 min, bias −6.4 | już dobre, pesymistyczne |
| Obietnica dostawy @assign, MAE | 15.9 min, bias +6.5 | słaba (target) |
| ETA odbioru systemu @assign, MAE | 11.8 min | **gorsze od koordynatora** |
| Obietnica koordynatora (odbiór), MAE | 7.1 min | człowiek > model |
| Naiwny (mediana kuriera, dostawa), MAE | 9.6 min | mocny baseline |

---

## Największe ryzyka danych
1. **Ogon artefaktów** w `eta_calibration_log` (worek 4+, greedy_fallback) rozbija średnie/RMSE — baseline i ewaluacja B MUSZĄ być robust (trim/mediana/%±). `[ZWERYFIKOWANE]`
2. **56% pokrycia matched** — 44% dostaw realizuje kurier ≠ proponowany; predykcja dla realnego kuriera bywa niedostępna → mniejsza próba do uczciwej kalibracji. `[ZWERYFIKOWANE]`
3. **95% NULL na predykcji odbioru „last"** — noga odbioru słabo obserwowalna „na żywo" (odbiór za szybki na 3-min tick). `[ZWERYFIKOWANE]`
4. **Brak wiarygodnego per-leg** (kurier dojechał pod restaurację o której?) — dziś tylko klik/status, nie GPS; kalibracja poślizgu odbioru wymaga geofence (5b, adopcja ~1 kurier). `[ZWERYFIKOWANE]`
5. **Kotwice niespójne + strefy** (ready vs pickup; naiwny-Warsaw vs UTC) — łatwo o cichy błąd ±2h / ±poślizg. `[ZWERYFIKOWANE]`
6. **Brak live pogody/ruchu** — ruch to statyczna tabela; „as-known-at-prediction-time" dla ruchu/pogody nie istnieje jako dana. `[ZWERYFIKOWANE]`

## Otwarte pytania do Adriana
1. **Cel ZADANIA B wobec werdyktu E-7:** skoro addytywny per-kurier jest NO-GO out-of-time, czy narzędzie ma (a) skupić się na nodze ODBIORU (poślizg per-kurier×obciążenie — tam koordynator dziś bije system o ~4.7 min MAE), (b) rozszerzyć istniejące „ETA warunkowe" per-komórka o wymiar kuriera z twardym shrinkage, czy (c) być czysto obserwacyjne (tabela median per-kurier jako wgląd, nie delta predykcji)?
2. **Cel kalibracji:** kalibrujemy do rzeczywistego **doręczenia**, do **odbioru**, czy obu segmentów osobno (segmentacja L1/L2/L3)?
3. **Which quantile operacyjny** — kalibrować medianę (P50) czy asymetrycznie (P80/P90, koszt spóźnienia > koszt zbyt wczesnej obietnicy)?
4. **Baseline sukcesu** — pobić które ETA: obietnicę @assign (łatwe), żywe @last (trudne), czy koordynatora na odbiorze (najtrudniejsze)?
5. Czy wolno użyć **stopera Rutcom „oczekiwanie odbiór"** (panel CSV) jako prawdy dla nogi odbioru mimo cienkich danych GPS?

## Aneks: ścieżki i komendy weryfikacyjne
- Rdzeń ETA: `route_simulator_v2.py:559,251` · `common.py:1034,2662,2704` · `chain_eta.py` · `calib_maps.py`
- Loggery: `eta_calibration_logger.py` · `tools/ziomek_pred_calibration.py`
- Dane: `dispatch_state/{eta_calibration_log,ziomek_pred_calibration,sla_log(→scripts/logs)}.jsonl` · `logs/shadow_decisions.jsonl`
- Flagi: `flags.json` (`ENABLE_ETA_QUANTILE_R6_BAGCAP=true` decyzyjny; reszta ETA-shadow/OFF)
- Advisory: `/root/ziomek-advisory/{07_REPLAY_RESULTS,07b_GOLD_PATTERN_FOUND,08_ROADMAP,06a_idea_backlog}.md` · `HANDOFF_TMUX26_TURA2.md`
- Skrypt baseline A.6: `scratchpad/a6_baseline.py`
- Regresja bazowa (dla kontekstu): `/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q` (baseline 4109/0/23skip/11xfail wg ARCHITECTURE.md)
