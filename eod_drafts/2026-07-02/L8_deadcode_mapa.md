# L8 — Mapa dead-code / sprzątania (recon READ-ONLY pod przyszłą falę L8)

**Pas:** L8-mapa (sprint FALA-2 audytu Ziomka, koordynator tmux 11) · **Data:** 2026-07-02 · **Branch:** `fix/l8-mapa` · **Autor:** agent recon (Fable/Opus)
**Charakter:** WYŁĄCZNIE recon z dowodami. ZERO edycji/kasowania kodu. Kasowanie/refaktor = przyszła fala L8 za ACK Adriana. Ten plik = jedyny deliverable.

> ⚠️ **Zasada nadrzędna raportu:** „nieimportowane przez silnik" ≠ „dead". `dispatch_v2/tools/` to w większości **przyrządy uruchamiane RĘCZNIE przez sesje** (audyty, replaye, verdykty) — sam ten agent uruchomił `tools/entropy_dashboard.py` i `tools/flag_registry.py`, oba są „nieosiągalne z silnika". Deadness `tools/` wymaga OSĄDU per-narzędzie (data ostatniego uruchomienia + czy bramka minęła), NIE grafu importów. Nie proponuję hurtowego kasowania `tools/`.

---

## 0. Baseline entropii (entropy_dashboard.py — do porównania PO fali L8)

Odpalone `tools/entropy_dashboard.py` (read-only, HEAD `60084fa`):

| # | Metryka | Dziś | Cel |
|---|---|---|---|
| 1 | copy-count (reguł >1 źródło) | 17 (≈90 inst.) | 0 |
| 2 | twin-divergence | ~13 | 0 |
| 3 | void-instrument | 19 VOID + 6 UNTESTED = 25/49 | 0 |
| 4 | dead-flag / rozjazdy flag | **6** | 0 |
| 5 | layer-violation | 7 | 0 |
| 6 | unresolved-conflict | 13 klastrów (64 par) | 0 |
| 7 | sentinel-as-data | 12 żywy silnik (+4 instr.) | 0 |
| 8 | threshold-sprawl | 10 rodzin (≈40 sites) | 0 |

Metryka bezpośrednio dotknięta L8: **#4 dead-flag = 6** (rozjazdy flag — ale patrz Klasa 3: należą do L0.1 rejestru, nie do L8-kasowania). L8 dead-code nie ma własnej metryki w dashboardzie — proponuję po fali dodać licznik „martwych modułów" (patrz §7).

**Metoda recon:** zbudowany graf osiągalności importów całego pakietu `dispatch_v2` (AST, 794 moduły łącznie z testami), **zaseedowany 90 realnymi entry-pointami** wyciągniętymi z `/etc/systemd/system` (ExecStart/ExecStartPre/Post), `crontab -l`, `atq` (200–206) oraz importów zewnętrznych konsumentów (`gastro_assign.py`, panel `nadajesz_clone/.../integrations/ziomek/*`, `courier_api/*`). Osiągnięto 192 moduły. Każdy kandydat dodatkowo krzyżowany z 140 basename'ami skryptów wołanych bezpośrednio (żeby nie pomylić „standalone entry-point" z „dead").

---

## 1. Martwe moduły `.py` — 39 kandydatów w rdzeniu (poza `tools/`)

**Dowód wspólny dla każdego:** (a) nieosiągalny z 90 entry-pointów w grafie importów; (b) basename NIE występuje w żadnym ExecStart/cron/at; (c) potwierdzenie liczby importerów `grep`. Fałszywki odrzucone poniżej.

### ✅ P1 — bezpieczne (ZERO importerów, ZERO entry, brak w testach lub tylko martwy test)
| Moduł | LOC | Dowód |
|---|---|---|
| `eta_error_report.py` | 185 | importerów (z testami) = **0**; nie w systemd/cron/at |
| `td20_caller_report.py` | 117 | importerów = **0**; jednorazowy raport TD-20 |

### 🟡 P2 — martwe wg grafu, ale wymagają potwierdzenia „nie odpala się ręcznie / nie jest planowaną funkcją"
Klastry funkcji nigdy-nie-wdrożonych albo jednorazowych analiz. Importowane najwyżej przez siebie nawzajem + testy.

| Moduł / klaster | LOC | Dowód / kontekst |
|---|---|---|
| **`ml_data_prep/` (7 plików)** | 2572 | żaden nie zaseedowany; `train_two_models`/`parity_ml_inference` odpalane ręcznie do LGBM shadow — testy `test_ml_twomodel.py` SKIPUJĄ gdy brak artefaktów (patrz Klasa 6). Cały katalog to eksperyment dwumodelowy. |
| **`sprint2_analysis/` (7 plików)** | 774 | jednorazowa analiza sprintu 2; `_common`/`data_inventory`/`override_patterns`/`report_builder`/`sanity_checks`/`tak_mystery`/`propose_uptime_analysis`. Zero entry. |
| **SMS-heartbeat klaster:** `tg_heartbeat.py` + `sms/{ovh,provider,stub}.py` | 282+370 | referują TYLKO siebie nawzajem + `test_mp9_sms_heartbeat`. `tg_heartbeat` NIE jest entry-pointem. Feature „MP9 SMS heartbeat" nigdy nie podpięty do systemd. |
| `learning_analyzer.py` | 533 | czyta `learning_log.jsonl` (log żywy — pisze go `shadow_dispatcher`), ale sam READER nieosiągalny z niczego żywego; tylko testy. Martwy analizator, log zostaje. |
| `r04_apply.py` | 336 | tylko test; `r04_evaluator` (żywy) go nie importuje. |
| `gastro_edit.py` (in-package) | 316 | tylko `test_regeocode_sync_text`; **żywy `gastro_edit` to `scripts/gastro_edit.py` (poza pakietem)** → in-package = przeniesiony/stary bliźniak. |
| `flags_admin.py` | 272 | tylko testy w pakiecie. ⚠ MEMORY wspomina panel `auto_assign_flag.py→flags_admin` — potwierdzić że panel nie woła go subprocess ZANIM ruszyć (surface administracji flag). |
| `speed_tier_tracker.py` | 211 | C4 standalone; żywy odpowiednik = `tools.build_speed_tiers` (cron). Prawdopodobnie zastąpiony. |
| `deploy_staging/scripts/gastro_assign.py` | ~120 | **IDENTYCZNY (md5) z żywym `scripts/gastro_assign.py`** — nieaktualizowany mirror staging, nigdzie nie wołany. |
| `validation_gate_lgbm.py` | ~? | tylko `test_e7_rotated_readers`; LGBM shadow logging-only. |
| `commitment_emitter.py` | 113 | C6 skeleton, flaga `ENABLE_MID_TRIP_PICKUP=False`, tylko test. |
| `parcel_assign.py` | 68 | tylko test; zastąpiony przez `parcel_lane_merge` (żywy) + panel `parcel_lane`. |
| `core/flags_io.py` | ~? | nieosiągalny; sprawdzić czy nie jest świeżą infra pod L0.1 (patrz ⚠ niżej). |
| `build_v319h_courier_tiers.py`, `extract_restaurant_addresses.py`, `bootstrap_restaurants.py` | ~? | jednorazowe skrypty setup/build (V3.19h tiers, ekstrakcja adresów, bootstrap restauracji). |
| `migrations/{legacy_audit_move_2026_05_07, migrate_couriers_2026-05-05}.py` | ~? | migracje jednorazowe już wykonane (05.2026). Konwencjonalnie zostawiane jako ślad — patrz P3. |
| `replay_failed.py` | ~? | offline debug tool (CLAUDE.md „Track C"), nie schedulowany. Manualny — raczej P3. |
| `shift_notifications/__main__.py` (+ cały moduł) | ~? | **usługa RETIRED: `dispatch-shift-notify.{service,timer}.retired-2026-06-15`, `is-active=inactive`.** Cały `shift_notifications/` to kandydat na archiwum (był seedem TYLKO z pliku `.retired`). |

### 🟩 P3 — ZOSTAW (świadome / chronione)
| Moduł | Powód |
|---|---|
| `wave_scoring.py` (393) | C5 nigdy-nie-flipnięte (`ENABLE_WAVE_SCORING`), CLAUDE.md: „NEVER modify wave_scoring.py without explicit ACK". Feature-w-poczekalni, nie dead-code. |
| `route_podjazdy.py` | referencja parytetu dla **L6.A golden route-order harness** (`test_route_order_golden`, `tools/route_order_golden_corpus_gen`). Artefakt audytowy — nie ruszać. |

**⚠ Fałszywki ODRZUCONE (poprawnie NIE oznaczone jako dead):**
- `auto_assign_gate.py` / `auto_assign_executor.py` / `auto_koord.py` / `global_alloc_store.py` — **żywe (gated)**: referują je `shadow_dispatcher.py` + `dispatch_pipeline.py`. AUTON-01 za `ENABLE_AUTO_ASSIGN=OFF`, przycisk LIVE. Osiągalne w grafie.
- `monitoring/consumer_stuck_alert.py` — NIE w systemd/cron, ALE log `consumer_stuck_alert_evaluations.jsonl` zapisany **dziś 12:42** → coś je żywo woła (osiągalne w grafie). Log = kandydat rotacji (Klasa 5), moduł NIE dead.
- `courier_gps_commitment_{shadow,report}.py`, `docs/deploy/ha-lite/backup_sentinel.py` — nieosiągalne z importów, ALE **są entry-pointami** (ExecStart woła je jako standalone `python X.py`). Żywe.

**LOC 39 kandydatów rdzenia razem: 9 249.** Realistyczny zysk P1 = ~300 LOC natychmiast; P2 = ~6–7k LOC po weryfikacji (głównie `ml_data_prep` + `sprint2_analysis` + SMS-klaster + shift_notifications).

---

## 2. Martwe funkcje/klasy w żywych modułach — DEFERRED (osobny pas)

Zgodnie z instrukcją „tylko oczywiste, lepiej mniej a pewnie": **nie przeprowadzałem agresywnego xref per-symbol** — rzetelne wykrycie martwych symboli w żywych modułach wymaga dedykowanego przebiegu (np. `vulture` + ręczna weryfikacja dynamicznych wywołań/`getattr`), a ryzyko fałszywek jest wysokie (Ziomek intensywnie używa `getattr`, flag-gated ścieżek, serializerów po nazwach kluczy). Jedyne PEWNE martwe symbole to te wewnątrz martwych modułów z §1 (trywialnie dead wraz z modułem). **Rekomendacja:** klasa 2 = własny mini-pas L8 z narzędziem, NIE część 1. iteracji.

---

## 3. Martwe flagi

**Wniosek: brak orphan-flag w `flags.json` do skasowania w L8.**
- `flags.json` ma **251 kluczy**; 34 „nieczytane w kodzie" to **wyłącznie klucze `_comment_*`** (JSON nie ma komentarzy — to świadome adnotacje dokumentacyjne). Pozostałe **217 realnych kluczy jest czytane** gdzieś w kodzie. Zero martwych flag boolowskich w pliku.
- Rejestr `tools/flag_registry.py` śledzi ~74 (podzbiór audytowany po L0) — rozjazd rejestr↔flags.json to zadanie **L0.1** (fingerprint/completion), NIE L8-kasowanie.
- **Metryka #4 „dead-flag = 6"** z dashboardu = te rozjazdy L0.1. Nie dubluj w L8.

**Podklasa realna dla L8 — flagi czytane WYŁĄCZNIE przez martwy kod (§1):** gdy skasujesz moduł, jego flaga staje się martwa. Kandydaci sprzężeni:
- `ENABLE_WAVE_SCORING` ↔ `wave_scoring.py` (ale P3 — zostaje)
- `ENABLE_MID_TRIP_PICKUP` ↔ `commitment_emitter.py`
- `ENABLE_SPEED_TIER_LOADING_PLANNED` ↔ `speed_tier_tracker.py`
- flagi SMS-heartbeat ↔ `tg_heartbeat`/`sms/*`

Kasować DOPIERO razem z modułem (fix u źródła — nie zostawiać osieroconej flagi).

---

## 4. Pliki `.bak-*` poza retencją — **NAJWIĘKSZY, NAJBEZPIECZNIEJSZY WIN**

Reguła repo = **retencja 24h** dla `.bak`. Stan faktyczny:
- **301 plików `.bak*` starszych niż 2 dni = 14.2 MB** (najstarszy `2026-04-11`, sięgają do `bootstrap_restaurants.py.bak-pre-bug12`).
- Wszystkich `.bak*` (dowolny wiek): **331 plików / 17.2 MB**.
- Rozkład: `./` (186), `tests/` (58), `tools/` (39), `daily_accounting/` (11), `cod_weekly/` (8), `reconciliation/` (6), `shift_notifications/` (5), `observability/` (5)…

**P1 — bezpieczne od ręki:** `.bak` nigdy nie są importowane; git trzyma pełną historię. Kasowanie `find . -name "*.bak*" -mtime +2` odzyskuje 14.2 MB i **znosi 301 plików szumu** (największy wpływ na entropię katalogu). ⚠ Zostaw `.bak` <24h (świeże rollbacki bieżących sesji) — użyj progu `-mtime +2` a nie hurtem, i **NIE ruszaj cudzych świeżych `.bak`** (multi-sesja, C1).

**`__pycache__`:** 42 katalogi / 1056 `.pyc` — regenerowalne, gitignored. Trywialny zysk, P1-trivial (`find -name __pycache__ -exec rm -rf`), ale niski priorytet (auto-odtwarzalne).

---

## 5. Cache / threshold / logi rosnące bez rotacji

**`dispatch_state/` = 1.2 GB.** Top „ciężary":
| Ścieżka | Rozmiar | Uwaga |
|---|---|---|
| `observability/` (katalog) | 341 MB | agregat — sprawdzić rotację per-plik |
| `v319c_read_shadow_log.jsonl.1` | 106 MB | **rotowany ale nie skasowany/skompresowany** (rotacja zostawia `.1`) |
| `learning_log.jsonl.1` (+ `.jsonl` 85 MB) | 101 MB | jw. — 186 MB w 2 plikach |
| `obj_replay_capture.jsonl` | 92 MB | pisze `feasibility_v2` (replay capture) — sprawdzić TTL |
| `consumer_stuck_alert_evaluations.jsonl` | 64 MB | pisze żywy `monitoring/consumer_stuck_alert.py` — **brak widocznej rotacji** |
| `events.db` | 31 MB | znany temat events.db GC (~10.07, DEPLOY-ZA-ACK) |
| `drive_min_enriched.jsonl` / `r6_breach_shadow.jsonl` / `courier_match_debug.jsonl` | 31/30/27 MB | shadow/debug logi — sprawdzić rotację |

⚠ **Duże nakładanie z istniejącym workstreamem:** działa już `dispatch-log-rotation.timer` (retention 14d, `--apply`), `ground_truth_gc`, `gps_positions_gc`, `event_bus_cleanup`. Kolejka DEPLOY-ZA-ACK (HANDOFF §6) ma „timer log-rotation + 1. `--apply` (~174 MB)" i „events.db kroki A–D (~10.07)". **Więc gros Klasy 5 to NIE nowy dead-code, tylko domknięcie istniejącej rotacji.** Część L8-specyficzna = pliki bez ŻADNEJ rotacji (`consumer_stuck_alert_evaluations`, `obj_replay_capture`, `courier_match_debug`) — dopiąć rotację/TTL u źródła.

**Progi „placeholder / kalibracja" (do przeglądu, NIE kasowania):**
- `drive_min_calibration.py:25/60` — `+10.0 placeholder dla post-F4-K2 (re-calibrate after LIVE)` + „re-calibrate gdy F4 K2 LIVE 1 month". **To realny threshold-do-rekalibracji — należy do L5 (ETA load-aware, F4), nie do L8-kasowania.**
- Reszta trafień `placeholder` (event_bus SQL `placeholders`, gps_server HTML input, plan_recheck/panel_watcher `(0,0)` sentinel-placeholdery) = albo legit (SQL/HTML) albo świadomy sentinel-guard (temat #7 sentinel, nie L8).

---

## 6. Testy-zombie

**Skip-markery (statycznie 40 wywołań `pytest.skip`, 9 `xfail` w żywych testach):**

**Trwale/legacy skipowane (kandydaci na usunięcie testu razem z legacy):**
- `test_v325_step_a_r02.py` / `test_v325_step_c_r04.py` — **module-level skip: „legacy V3.25 STEP A/C roster migration"**. Migracja roster dawno zrobiona → martwe testy. P2 (skasować z modułem migracyjnym).
- `test_scoring_scenarios.py` — module-level skip (legacy, NameError wg CLAUDE.md). P2.
- `test_ml_twomodel.py` — 4× skip „brak `twomodel_report.json`/`parity_ml_inference_report.json` — uruchom najpierw train_two_models.py". **Zawsze skipuje jeśli `ml_data_prep` nie odpalany** → zombie sprzężony z martwym klastrem ML (§1). P2.

**Warunkowe skipy — ZOSTAW (legit, powód aktualny):**
- `test_prep_bias` (brak realnego `ready_at_log.jsonl`), `test_geo05_district_adjacency` (brak `geocode_cache.json`), `test_preshift_window_penalty_2026_06_24` (okno czasowe / unik wrapu północy), `test_parser_v2_property_based` (prefix>=length edge), `test_parser_health_layer3` / `v328_layer4_*` (skip gdy moduł nie deployed do `/tmp`) — wszystkie mają aktualny powód środowiskowy. P3.

**xfail (świadome markery — NIE dead-code, tylko odnotować):**
- `test_invariant_slots_l04.py` — **5× `xfail(strict=True)` = RATCHET inwariantów z L0** (świadome sloty; „czerwony test markuje lukę"). ZOSTAW.
- `test_obj_food_age_bug5.py` — 2× `xfail(strict=False)` (in-flight food-age). ZOSTAW.
- `test_demote_tier_bucket_p4.py` — 1× `xfail` (P-4 OFF-mode klucz). ZOSTAW.

(Baseline raportuje 23 skipped / 11 xfailed przy pełnym runie — statyczna lista markerów zgodna co do rzędu; różnica = skipy warunkowe zależne od danych/czasu.)

---

## 7. Priorytety, zysk, proponowana 1. iteracja L8

### Podsumowanie liczbowe per klasa
| Klasa | P1 (od ręki) | P2 (weryfikacja live) | P3 (zostaw) |
|---|---|---|---|
| 1. Martwe moduły rdzeń | 2 (~300 LOC) | ~30 (~6.5k LOC) | ~4 (wave_scoring, route_podjazdy, migrations, replay_failed) |
| 1b. Tools nieschedulowane | 0 | 0 (toolbox — osąd per-narzędzie) | ~100 (analyst toolbox — nie hurtowo) |
| 2. Martwe funkcje | 0 | — (deferred, osobny pas) | — |
| 3. Martwe flagi | 0 | ~3–4 (sprzężone z modułami) | reszta = L0.1 |
| 4. `.bak` >2d | **301 plików / 14.2 MB** | — | świeże <24h |
| 5. Cache/logi | pycache 1056 plików | ~3 logi bez rotacji | reszta = istniejący rotation workstream |
| 6. Testy-zombie | 0 | ~4 testy legacy | reszta warunkowe/xfail |

### TOP-5 największych zysków
1. **`.bak` >2d: 14.2 MB / 301 plików** — P1, natychmiast, git = siatka bezpieczeństwa. Największy spadek szumu.
2. **`ml_data_prep/` (2572 LOC) + `sprint2_analysis/` (774 LOC)** — P2, ~3.3k LOC eksperymentów/jednorazówek.
3. **Logi bez rotacji: `consumer_stuck_alert_evaluations` 64M + `obj_replay_capture` 92M + `courier_match_debug` 27M** — P2, dopiąć TTL u źródła (~183 MB odzysku cyklicznie).
4. **SMS-heartbeat klaster (`tg_heartbeat`+`sms/*`, 652 LOC) + `shift_notifications/` (RETIRED)** — P2, całe wygaszone features.
5. **`learning_analyzer` (533) + `flags_admin` (272) + `gastro_edit`/`r04_apply`/`speed_tier_tracker`/`commitment_emitter`/`parcel_assign`** — P2, martwe pojedyncze moduły ~1.8k LOC.

### Proponowany zakres 1. iteracji L8 (najniższe ryzyko → najwyższy zysk)
1. **Krok A (P1, bez ACK-ryzyka, „warto zawsze"):** skasuj `.bak` >2d (`find … -mtime +2`, z pominięciem cudzych świeżych), wyczyść `__pycache__`. Zysk 14.2 MB / 301 plików, ryzyko ~0 (git history). Backup listy przed usunięciem.
2. **Krok B (P1 modułowe):** archiwizuj `eta_error_report.py` + `td20_caller_report.py` (0 importerów). `git rm` + tag rollback.
3. **Krok C (P2, wymaga potwierdzenia „nie odpala się ręcznie"):** przenieś do `archive/` (nie kasuj wprost) klastry: `ml_data_prep/`, `sprint2_analysis/`, SMS-heartbeat, `shift_notifications/` (retired), `speed_tier_tracker`, `commitment_emitter`, `parcel_assign`, `deploy_staging/scripts/gastro_assign.py`, `learning_analyzer`. Dla KAŻDEGO: potwierdź brak subprocess/manual-run (grep workspace + spytaj Adriana o `flags_admin`/`replay_failed`), skasuj sprzężoną flagę U ŹRÓDŁA, usuń test-zombie razem.
4. **Krok D (P2, cache):** dopnij rotację/TTL dla `consumer_stuck_alert_evaluations`, `obj_replay_capture`, `courier_match_debug` u źródła (spójnie z istniejącym `log_rotation`).
5. **NIE w 1. iteracji:** `tools/` (osąd per-narzędzie), Klasa 2 (osobny pas z narzędziem), events.db/log-rotation `--apply` (już w kolejce DEPLOY-ZA-ACK), `wave_scoring`/`route_podjazdy`/migrations (P3).

**Sugestia metryki po L8:** dodać do `entropy_dashboard.py` licznik „martwe moduły" (osiągalność z entry-pointów) + „`.bak` poza retencją", żeby regres był widoczny.

### Ryzyka / fałszywki które ODRZUCIŁEM (żeby przyszła fala nie skasowała żywego)
- `tools/*` — NIE dead mimo braku importu z silnika (toolbox ręczny: `entropy_dashboard`, `flag_registry`, replaye, verdykty). Deadness = data-ostatniego-uruchomienia, nie graf.
- `auto_assign_*`/`auto_koord`/`global_alloc_store` — żywe (gated AUTON-01), referują z `shadow_dispatcher`+`dispatch_pipeline`.
- `consumer_stuck_alert.py` — żywy (log pisany dziś), tylko log do rotacji.
- `courier_gps_commitment_*`, `backup_sentinel.py` — żywe entry-pointy (standalone ExecStart, nie import).
- `flags_admin.py` / `replay_failed.py` — martwe wg grafu, ale POTENCJALNIE wołane subprocess/ręcznie → P2 z twardym „spytaj przed ruchem".
- `core/flags_io.py` — sprawdzić czy to nie ŚWIEŻA infra L0.1 (nowszy commit) zanim uznać za martwe.
- `.bak` <24h — świeże rollbacki bieżących sesji, NIE ruszać (multi-sesja C1).

---

*Recon read-only zakończony. Kasowanie/archiwizacja = przyszła fala L8 za ACK Adriana (fix u źródła: moduł + jego flaga + jego test-zombie razem — „zmiana częściowa = niezakończona").*
