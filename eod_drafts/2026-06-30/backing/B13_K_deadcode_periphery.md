# B13 — KLASA K (martwy/szczątkowy kod) w STATE + TOOLS + CONFIG + CROSS-REPO + systemd

**Agent:** B13-K-deadcode-periphery · **lane B** · **Faza 1 audyt spójności Ziomka, sesja tmux 2, READ-ONLY.**
**Data:** 2026-06-30 ~14:0x UTC · HEAD `8024705` · working tree silnika czysty.
**DoD:** ZERO edycji/restartów/flipów/git/--apply. Wszystkie cytaty `plik:linia` ze ŚWIEŻEGO grepu dziś (linie dryfują).
**Zakres:** klasa K — kod/config/unity/pliki, które ŻYJĄ w drzewie ale NIE są wykonywane na żywo (martwe, szczątkowe, retired, skeleton, zneutralizowane stałą, osierocone, śmieci .bak), oraz periferia która jest „dead-functionality". STOP na dyspozytorni (nie Mailek/Papu).
**Metoda:** `ls/find` (inwentarz) + `grep -rn` świeży per moduł/flaga + `ExecStart=`/`atq`/`crontab`/`/etc/cron.d` (kto NAPRAWDĘ uruchamia) + reachability gałęzi flag-gated + korelacja z A1/A3/A4/A5/A6. Skrypt orphan-mapy: `scratchpad/orphan_map.sh`.

---

## 0. TL;DR — 13 instancji K, 0× P0/P1 (z definicji — martwy kod nie biega live)

Klasa K nie produkuje błędnego wyjścia na żywo. Jej szkoda jest **odroczona/poznawcza**: (a) **mina C2** — flip flagi skeletonu uzbraja 2-miesięczny nietestowany kod w gorącej ścieżce; (b) **myli root-cause** — zombie-reguła (R7) i skeletony wyglądają jak żywe feature'y; (c) **zatruwa twin-graf** — DEAD 5. kopia route-order w `courier_api_panelsync` to ten sam plik, który ktoś „naprawi" myśląc że jest live (J-pułapka); (d) **clutter** — 326+ `.bak` zmusza każdy grep audytu do filtrowania, .bak stary 2,5 mies. zamiast 24h retencji (CLAUDE.md). Jedyny **żywy** objaw peryferyjny = `dispatch-cod-weekly.service` **FAILED**.

| # | Instancja | plik:linia | Sev | Otwarte? |
|---|---|---|---|---|
| K-01 | `commitment_emitter.py` (113L) — pełny skeleton, `emit_commitment_event` 0 callerów | commitment_emitter.py:82 | P3 | TAK |
| K-02 | `pending_queue_provider.py` (100L) — osiągalny TYLKO przez martwą flagę `ENABLE_PENDING_QUEUE_VIEW=False` | dispatch_pipeline.py:3372 | P3 | TAK |
| K-03 | `speed_tier_tracker.py` (211L) skeleton + stale output + MYLĄCY komentarz „nightly" | common.py:904 | P3 | TAK |
| K-04 | R7 long-haul HARD-reguła zneutralizowana `LONG_HAUL_DISTANCE_KM=99.0` (reject nigdy) | feasibility_v2.py:486 | P3 | TAK |
| K-05 | `r6_soft_penalty_c3_legacy` martwa metryka (guard `DEPRECATE_LEGACY_HARD_GATES=False` nigdy flip) | feasibility_v2.py:1129 | P3 | TAK |
| K-06 | `courier_api_panelsync/courier_orders.py` (665L) — DEAD 5. kopia route-order, nie serwowana | courier_api_panelsync/courier_orders.py:558 | P3 | TAK |
| K-07 | `shift_notifications/` RETIRED w drzewie (worker 886L+4) + nested systemd/ + orphan /etc drop-in dir | shift_notifications/worker.py:1 | P3 | TAK |
| K-08 | `A4_TEST_FLAG`+`commitment_level` martwe klucze w flags.json | flags.json:72 | P3 | TAK |
| K-09 | systemd drop-in `.bak` ×4 (recon listował 3 — 4. = czasowka) | /etc/systemd/system/dispatch-czasowka.service.d/override.conf.bak-pre-notif-mute-2026-06-26 | P3 | TAK |
| K-10 | `.bak` graveyard 326+ (dispatch 176+37, courier_api 41, panel 72), Apr11→Jun30 | dispatch_v2/*.bak* | P3 | TAK |
| K-11 | ~45 osieroconych tools/*.py (0 timer/at/cron/import) — sprint-jednorazówki + martwe probe/replay | tools/ | P3 | TAK |
| K-12 | `epaka_fetcher.py` — narzędzie PROJEKTU EPAKA w dispatch_v2/tools/ (cross-project) | tools/epaka_fetcher.py:1 | P3 | TAK |
| K-13 | `dispatch-cod-weekly.service` = FAILED (COD weekly batch nie biega) — periferia M | (systemd unit) | P2 | TAK |

**Anty-double-count:** 64 tools mają 0 referencji, ale **11 to DORMANT-INSTRUMENT** (verdict/review/reminder z at-jobem SPENT lub on-demand) — NIE martwe-do-usunięcia (→ §6/§7, korelacja A4). Genuine graveyard ≈ 45-50.

---

## 1. SILNIK — moduły martwe / zneutralizowane (engine-tree, CORE-adjacent)

### K-01 — `commitment_emitter.py` (113 LOC) = PEŁNY SKELETON, 0 żywych callerów  [P3 source]
- **Flaga:** `ENABLE_MID_TRIP_PICKUP=False` (common.py:920, **literał False, nie env-read** → hard-OFF). C6 z F2.2 (2026-04).
- **Dowód deadness:** `grep emit_commitment_event` (publiczna fn, commitment_emitter.py:82) poza testami/bakami = **0 trafień**. Jedyne nie-testowe wzmianki nazwy modułu: docstring `pending_queue_provider.py:3` + komentarz `courier_ground_truth.py:9` — **żaden to import/wywołanie**. Moduł NIE jest importowany NIGDZIE w żywym kodzie (nawet w gałęzi flag-gated).
- **Powiązane martwe config:** flaga `commitment_level` w flags.json (linia 7) — jedyny konsument to ten martwy moduł (commitment_emitter.py:84/96/103) → **martwa para flaga↔skeleton** (→ K-08).
- **Klasa:** K (skeleton nigdy nie aktywowany) + C2-mina (flip `ENABLE_MID_TRIP_PICKUP`=ON uzbroiłby 113L nietestowanego kodu w state_machine rewake).
- **dedup_hint:** K-skeleton-F2.2 (C4/C6/C7 rodzina: K-01+K-02+K-03 razem — wszystkie skeletony Sprint C 2026-04 nigdy nie odpalone).

### K-02 — `pending_queue_provider.py` (100 LOC) — osiągalny TYLKO przez martwą flagę  [P3 source]
- **Flaga:** `ENABLE_PENDING_QUEUE_VIEW=False` (common.py:921, literał False).
- **Dowód:** import `from dispatch_v2.pending_queue_provider import get_pending_queue` jest pod `dispatch_pipeline.py:3375`, ale całość w bloku `if ENABLE_PENDING_QUEUE_VIEW:` (`dispatch_pipeline.py:3372`, import flagi `:3371`). Flaga literał-False → **gałąź nieosiągalna live**, import nigdy nie wykonany. Druga gałąź `compute_demand_context` `:3381` — j.w.
- **Różnica vs A1:** A1 oznaczył „importowany w pipeline :3375" jako wątpliwość — POTWIERDZAM że import istnieje, ale jest **dead-on-arrival** (flag-gated False). To NIE żywy konsument.
- **Klasa:** K (skeleton za martwą flagą) + C5-near-miss (flaga ON + konsument istnieje ≠ działa — tu odwrotnie: flaga OFF czyni konsumenta martwym).
- **dedup_hint:** K-skeleton-F2.2.

### K-03 — `speed_tier_tracker.py` (211 LOC) skeleton + stale output + MYLĄCY komentarz  [P3 source]
- **Flaga:** `ENABLE_SPEED_TIER_LOADING_PLANNED=False` (common.py:909). C4 „PLANNED (brak consumera)".
- **Dowód deadness:** 0 żywego importu modułu (tylko komentarz common.py + testy). Jedyny writer `courier_speed_tiers.json` to ten martwy skeleton → output **stale: Apr 18 18:53** (2,5 mies.).
- **★ MYLĄCY komentarz (klasa K+L):** `common.py:904` „C4: speed_tier_tracker.py produces courier_speed_tiers.json (nightly)" — **NIEPRAWDA**: nightly producer to INNY tool `tools/build_speed_tiers.py` (cron `25 4 * * *`, `/etc/cron.d`), który pisze `courier_speed_data.json` (NIE `_tiers`). Żywy konsument tierów = `dispatch_pipeline.py`/`courier_reliability.py` czytają `courier_speed_data.json`. Komentarz każe czytelnikowi szukać żywego pipeline'u w martwym module.
- **Klasa:** K (skeleton + stale output) + L (komentarz kłamie o producencie).
- **dedup_hint:** K-skeleton-F2.2; stale-output→M-cross-ref.

### K-04 — R7 long-haul: HARD-reguła zneutralizowana stałą (zombie)  [P3 source]
- **Egzekutor:** `feasibility_v2.py:486` `if bag and r7_ride_km > C.LONG_HAUL_DISTANCE_KM and r7_in_peak: return NO` — ale `LONG_HAUL_DISTANCE_KM=99.0` (common.py:800, komentarz „R7 wyłączone — 4.5km za agresywne"). **HARD-reject NIGDY nie odpala** (żaden przejazd w Białymstoku >99km). `metrics["r7_is_longhaul"]=...>99` zawsze False (`:484`). TODO C3 refactor-do-soft (`:471`) nigdy nie wykonany.
- **Dlaczego K istotne (nie tylko clutter):** R7 to JEDYNA HARD-bramka geometrii długiego przejazdu. Czytelnik feasibility widzi nazwany R7 z reject-branch i wnioskuje, że długodystansowe bundlingowanie jest twardo blokowane — **NIE jest**. Koreluje wprost z audytem rodziny alokacji (`ZIOMEK_ROOTCAUSE_AUDIT_allocation_family.md` P0-A: „geometryczna bramka HARD nie istnieje; geometry_blind_fallback za wąski"). R7-zombie = część dowodu „brak HARD-geometrii".
- **Klasa:** K (kod-zombie, reguła zneutralizowana stałą) — A2 smell #2 POTWIERDZONY świeżym grepem.
- **dedup_hint:** K-zombie-rule (samodzielny); cross-ref alloc-audit P0-A (brak HARD-geometrii).

### K-05 — `r6_soft_penalty_c3_legacy` = martwa metryka  [P3 source]
- **Egzekutor:** `feasibility_v2.py:1120-1132` (Z-21 higiena rename 2026-06-13). Wartość `round(-3.0*(bt-30),2)` liczona TYLKO gdy `DEPRECATE_LEGACY_HARD_GATES=True` — stała `common.py:912 = False`, **nigdy flipnięta** (komentarz `:1123` wprost: „stała=False, nigdy nie flipnięta"); live-caller i tak nie przekazuje kwargu. W produkcji metryka = 0.0 (else-branch `:1132`). Zastąpiona żywą `bonus_r6_soft_pen` (dispatch_pipeline) — patrz A2.
- **Klasa:** K (martwy kod logujący 0.0) — A2 smell #1 POTWIERDZONY.
- **dedup_hint:** K-legacy-gate (rodzina `DEPRECATE_LEGACY_HARD_GATES` — cała R1/R5/R6/R7/R8 „→soft" migracja nigdy nie odpalona; common.py:912).

> **Sprostowanie A1:** `traffic_v2_aggregator.aggregate_legs` (dispatch_pipeline.py:5663) **NIE jest martwy** — wpięty bezwarunkowo w `_v327_eval_courier` przed konstrukcją Candidate (`:5665` buduje `traffic_v2_shadow_route`). To ŻYWA telemetria shadow (BUG-D Faza 2b). A1 „DEAD?" → REFUTED.

---

## 2. CROSS-REPO — martwy fork (klasa K+J)

### K-06 — `courier_api_panelsync/courier_orders.py` (665L) = DEAD 5. kopia route-order  [P3 source, J-latent]
- **Dowód deadness:** worktree `courier_api_panelsync` (branch panel-sync `4ab1e6d`) ma własny `courier_orders.py` z `build_view:558`, `optimize_route:188`, `_plan_stop_sequence:366` = pełna własna kopia carried-first/route-order. ALE: live `courier-panel-sync.service` uruchamia `panel_sync.py --once --live`, a `panel_sync.py` importuje **config, db, panel_kurier, panel_lite** — **NIE courier_orders** (grep „courier_orders" w panel_sync.py = 0). `main.py` panelsync odwołuje się do courier_orders, ale **main.py panelsync nie jest serwowany** (courier-api.service biega z głównego `scripts/courier_api`, nie z worktree). → cały `courier_orders.py` (665L) panelsync jest martwy.
- **Dlaczego J-latent (nie tylko K):** A6 grupa 2 / A5 C.5 liczą ten plik jako **5. kopię** reguły kolejności trasy w twin-grafie. Martwa kopia to pułapka: (a) sesja „naprawiająca route-order razem we wszystkich kopiach" (protokół C7) trafi tu i zmarnuje pracę/wprowadzi rozjazd; (b) re-serwowanie worktree kiedykolwiek = stary kanon route-order na żywo.
- **Klasa:** K (martwa kopia/fork) + J (zatruwa twin-graf route-order — DEAD member grupy 2).
- **dedup_hint:** R2-route-order (DEAD member; NIE liczyć jako żywą 5. kopię — to martwy fork do usunięcia, NIE do równania).

---

## 3. systemd — retired / orphan / drop-in śmieci (klasa K)

### K-07 — `shift_notifications/` RETIRED, ale potrójny grób w drzewie  [P3 source]
- **Moduł w drzewie:** `dispatch_v2/shift_notifications/` z `worker.py` (886L), `grouping.py`, `state.py`, `telegram_send.py`, `__main__.py`, `__init__.py` + `state.py.bak-*`. mtime do 25.06 (ktoś jeszcze dotyka).
- **Unity RETIRED:** `/etc/systemd/system/dispatch-shift-notify.service.retired-2026-06-15` + `.timer.retired-2026-06-15` (renamed → systemd ich nie ładuje).
- **★ ORPHAN drop-in dir:** `/etc/systemd/system/dispatch-shift-notify.service.d/` ISTNIEJE (cron_health/onfailure/resource_limits) — ale unit `.service` zniknął (jest `.retired`) → **drop-in dir bez unitu** (potwierdzone: `for d in *.service.d; do [ -f ${d%.d} ] || echo ORPHAN` → jedyny orphan).
- **★ Nested in-repo systemd źródła:** `dispatch_v2/shift_notifications/systemd/{dispatch-shift-notify.service,dispatch-shift-notify.timer}` — kopie źródłowe retired unitów wciąż w repo.
- **Klasa:** K (retired-nie-usunięty: moduł 886L+ + orphan /etc dir + nested źródła + .bak).
- **dedup_hint:** K-retired-shift-notify (samodzielny, TASK B z 04.05 wycofany 15.06).

### K-09 — systemd drop-in `.bak` ×4 (recon/A5 listowały 3)  [P3 source]
- `dispatch-shadow.service.d/override.conf.bak-pre-veto-retire-coeff100-2026-06-11`
- `dispatch-plan-recheck.service.d/unified-route-f1-f2.conf.bak-pre-noreturn-2026-06-13`
- `dispatch-panel-watcher.service.d/unified-route-f3.conf.bak-pre-noreturn-2026-06-13`
- **★ NOWY (recon §C/A5 A.4 pominęły):** `dispatch-czasowka.service.d/override.conf.bak-pre-notif-mute-2026-06-26`
- systemd czyta tylko `*.conf` → `.bak` inertne, ale w katalogu route/canon-env (gdzie parytet flag jest krytyczny, A3 §1) clutter myli przy `diff` parytetu.
- **Klasa:** K (drop-in śmieci). **dedup_hint:** K-dropin-bak (rozszerza A5 A.4 o 4. plik).

---

## 4. `.bak` GRAVEYARD — magnituda (klasa K)  [K-10, P3 source]

| Lokalizacja | liczba `.bak*` | zakres dat |
|---|---|---|
| `dispatch_v2/` (top-level) | **176** | Apr 11 → **Jun 30** |
| `dispatch_v2/tools/` | **37** | (A4 §8 szacował „~30") |
| `scripts/courier_api/` | **41** | (A5 „40") |
| `nadajesz_clone/panel/backend/` | **72** | (A5 „72") |
| **SUMA cross-repo** | **326+** | — |

- **Polityka NIEEGZEKWOWANA:** CLAUDE.md deklaruje „backup files (24h retention)". Realnie .bak żyją **2,5 miesiąca** (najstarszy `bootstrap_restaurants.py.bak-pre-bug12` Apr 11; najnowszy `*-auton02-20260630`). A5 policzył tylko cross-repo (113); **top-level dispatch_v2 = 176 NIE było w żadnym A-dokumencie** → realny graveyard ~3× większy niż raportowano.
- **Szkoda:** każdy `grep -rn --include=*.py` audytu MUSI filtrować `\.bak` (robił to każdy agent A); ryzyko trafienia w stale kopię przy nieuważnym grep; 176 plików × ~kilkaset KB = MB clutteru w katalogu silnika.
- **Klasa:** K (śmieci-retencja). **dedup_hint:** K-bak-graveyard (samodzielny, systemowy — polityka, nie pojedynczy plik).

---

## 5. CONFIG bez konsumenta — martwe klucze flags.json (klasa K)  [K-08, P3 source]

- **`A4_TEST_FLAG: false`** (flags.json:72) — **klucz TEST-ONLY w produkcyjnym flags.json.** Jedyny konsument: `tests/test_a4_config_reload_pubsub.py:274` używa nazwy jako DYNAMICZNEGO test-flag do pub/sub reload. W żywym kodzie 0 konsumentów. Test-artefakt wyciekł do prod-configu.
- **`commitment_level: false`** (flags.json:7) — jedyny konsument to martwy `commitment_emitter.py` (K-01). Dodatkowo **typ mismatch**: flags.json daje bool `false`, a `commitment_emitter.py:96` oczekuje stringa z `VALID_LEVELS` → martwa I niespójna semantycznie config.
- **NIE martwe (sprostowanie A3 §3c):** `kill_switch_to_v1` (flags.json:3) jest ŻYWY — `panel_watcher.py:2621` `if flag("kill_switch_to_v1", False): sleep 30s` (emergency stop watchera). A3 listował go jako „decyzyjny-krytyczny poza rejestrem" — POTWIERDZAM żywy, NIE martwy.
- **Pełen sweep ENABLE_*:** `0` flag `ENABLE_*` w flags.json bez żywego konsumenta `.py` (skrypt Python, §scratchpad). Czyli skeletony (K-01/02/03) NIE pokazują się jako „martwa flaga" — ich flaga MA konsumenta (import w martwej/flag-gated gałęzi). Martwe są tylko non-ENABLE: `A4_TEST_FLAG`, `commitment_level`.
- **Klasa:** K (config bez żywego konsumenta). **dedup_hint:** K-dead-config (A4_TEST_FLAG samodzielny test-leak; commitment_level→K-01 skeleton).

---

## 6. TOOLS/ — osierocone (klasa K) + anty-double-count z DORMANT-INSTRUMENT

**145 tools/*.py · 64 z 0 referencji (timer/at/cron/import) · 45 z 1 · 35 z ≥2.** Skrypt: `scratchpad/orphan_map.sh`.

### 6a. ⚠ NIE wszystkie 0-ref to martwe — 11 to DORMANT-INSTRUMENT (legit)
At-job SPENT lub on-demand → pokazują 0-ref dziś, ale są żywymi przyrządami (korelacja A4 §2):
`bug4_reseq_verdict` (at-188 spent), `drive_speed_overshoot_verdict` (at-187 spent), `feas_carry_blind_review`/`feas_carry_readmit_postflip` (at-167/192 spent), `reassign_global_select_review`, `reassignment_notify_peak_review` (at-178), `refloor_verdict_relay`, `lunch_floor_verdict_reminder`, `merge_reminder_meta_verdict`, `merge_reminder_obj_fresh`, `obj_fresh_verdict_atrun`, `carried_age_tzfix_review`, `preshift_rescue_peak_review`, `verify_pickup_floor_morning_summary`. **NIE raportować jako martwe** — to dormant verdict-half żywych collectorów. (Niektóre VOID per alloc-audit — to klasa E, nie K; patrz §7.)
**On-demand utilities (też NIE martwe):** `validate_state_schema` (drift-detektor stanu), `flag_effect_coverage_check`/`flag_hygiene_check`/`flag_doc_coverage_check`/`flag_registry`/`flag_fingerprint` (flag-tooling, używane przez testy+checkery), `latency_alarm` (rec#6 regresja latencji), `invalidate_city_bugged_geocodes`/`purge_streetless_geocode_keys` (ops jednorazowe).

### 6b. GRAVEYARD ≈ 45-50 (genuine martwe jednorazówki)  [K-11, P3 source]
Closed-sprint scripts, 0 konsumenta, nigdy re-run:
- **Date-stamped sprint-jednorazówki:** `verify_obj_f1_2026-05-19`, `verify_obj_f2_2026-05-19`, `verify_obj_f4_2026-05-19`, `verify_obj_f4_k2_2026-05-21`, `calib_b3c2d2_2026_05_29`, `monitor_c2_peak_2026_05_30`, `monitor_latepickup_peak_2026_06_01`, `monitor_refloor_peak_2026_05_31`.
- **Probe/replay zamkniętych śledztw:** `base_amplify_probe`, `base_score_decompose`, `greedy_fallback_flip_probe`, `distance_reshape_replay`, `load_reshape_replay`, `deferral_value_replay`, `defer_hold_shadow`, `roadfactor_gap`, `warmstart_gap`, `soon_free_coverage`, `obj_econ_replay`, `loadgov_gate_replay`, `replay_feasibility`, `route_reorder_replay`, `measure_bug1_eta_vs_freeat`, `post_shift_overrun_forward_replay` (★ także VOID — §7), `prep_bias_decision_time_replay`, `prep_bias_r6_replay`, `fleet_t15_replay`, `infeasible_bags_probe`.
- **One-off analiza:** `no_gps_who`, `no_gps_eta_error`, `no_gps_rescue_coverage`, `outcome_check_driverA`, `pos_age_outcome`, `parser_stuck_peak_check`, `coeff_grid_analyze`, `coeff_gridsearch`, `calibration_screen`, `courier_speed_build`, `decision_outcome_join`, `weight_calibration`, `weekly_a2_digest`, `analyze_shadow_logs`, `analyze_traffic_v2_shadow`, `build_bundling_bias_corpus`, `failure_tail_analysis`, `shadow_signals_vs_tail`, `eta_r3_compare_variants`, `extract_bias_score_margins`, `fail03_outcome_join`, `prediction_reducibility_test`, `r6_overpessimism_test`, `tier_calibration_test`, `best_effort_escalation_report`, `osrm_traffic_v2_stats`, `osrm_fallback_smoke`, `sla_join_worker`.
- **Klasa:** K (osierocone jednorazówki — niskie ryzyko, czysty clutter). **dedup_hint:** K-orphan-tools-graveyard.

### K-12 — `epaka_fetcher.py` — narzędzie OBCEGO PROJEKTU w dispatch tools/  [P3 source]
- `tools/epaka_fetcher.py` docstring: „Epaka fetcher dla Ziomka — pobiera przesyłki (CSV) i prowizje z panelu epaka.pl". To projekt EPAKA (MEMORY `epaka-cennik-oferta-automation.md`), NIE dispatch. Siedzi w `dispatch_v2/tools/` = cross-project contamination (zła lokalizacja, nie martwy ale nie-tu).
- **Klasa:** K (misplaced/wrong-home). **dedup_hint:** K-misplaced (samodzielny).

---

## 7. KORELACJA z A4 — przyrządy-osierocone-I-VOID (granica K↔E)

A4 §8 (K-smell) i alloc-audit „WIĘCEJ BUGÓW" wskazują przyrządy które są JEDNOCZEŚNIE osierocone (0-ref, §6) ORAZ void (kłamią). To **granica K↔E** — raportuję jako K-cross-ref, bo „dead/orphaned" jest częścią problemu:
- `post_shift_overrun_forward_replay` — orphan (0-ref) + **VOID** (czyta `post_shift_overrun_min` z shadow_decisions, klucz `grep=0/282` = nigdy serializowany → werdykt niemożliwy). Martwy I kłamiący.
- `bug4_reseq_verdict` / `drive_speed_overshoot_verdict` — dormant (§6a) ale alloc-audit/A4 = void/N-A. Dormant-and-unvalidated.
- A4 §8 K: „~30 .bak w tools/" → potwierdzone 37 (§4). „objm_lexr6_canary bez durable logu" = klasa H (nie K).
- **Werdykt granicy:** te przyrządy NIE są „martwe-do-usunięcia" (mają rolę), ale ich liczby są martwe/void → należą do Fazy C oracle (E), nie do czyszczenia K. Tu odnotowane by NIE policzyć ich podwójnie jako „martwy kod" gdy są „kłamiący kod".

---

## 8. PERIFERIA — żywy objaw martwej funkcjonalności

### K-13 — `dispatch-cod-weekly.service` = FAILED  [P2 symptom, klasa M-periferia]
- `systemctl is-failed dispatch-cod-weekly.service` → **failed** (loaded/failed/failed). „Ziomek F2.1d COD Weekly — scrape panel + batch write Google Sheets". Przyczyna prawdopodobna = gspread env (CLAUDE.md: „test_cod_weekly fails gspread import error w env"; A1 §2c venv **sheets**).
- Nie „martwy kod" sensu stricto (kod istnieje, biega-i-pada), ale **martwa funkcjonalność w periferii** = COD weekly batch NIE wykonuje się. Cicha awaria (M) w okołosystemie COD — w zakresie „periphery" mojego lane'a.
- **NIE not-found:** brak unitów `not-found` (sprawdzone). Jedyny failed = cod-weekly.
- **Klasa:** M (cicha awaria periferii) — kind=symptom. **dedup_hint:** M-periphery-cod-weekly (cross-ref A1 §2c, recon §B).

---

## TABELA POKRYCIA (co zbadano — jawnie)

| Obszar | Zbadane | Metoda | Wynik |
|---|---|---|---|
| Skeletony silnika (C4/C6/C7) | ✅ commitment_emitter, pending_queue_provider, speed_tier_tracker | grep import + flag-reachability + wc | 3 martwe (K-01/02/03) |
| Zombie-reguły | ✅ R7 (LONG_HAUL=99), r6_soft_penalty_c3_legacy, DEPRECATE_LEGACY_HARD_GATES | grep stała + guard | 2 martwe (K-04/05) |
| traffic_v2_aggregator (A1 „DEAD?") | ✅ reachability :5663 | read context | ŻYWY (refuted) |
| Cross-repo fork | ✅ courier_api_panelsync (courier_orders 665L, panel_sync imports) | grep entry-point | DEAD 5. kopia (K-06) |
| systemd retired/orphan | ✅ shift-notify (.retired×2 + orphan dir + nested + module) | ls /etc + for-loop orphan | K-07 |
| systemd drop-in .bak | ✅ 4 pliki (shadow/czasowka/plan-recheck/panel-watcher) | find /etc | K-09 (4., recon miał 3) |
| .bak graveyard | ✅ dispatch 176+37, courier_api 41, panel 72 = 326+ | find -name *.bak* | K-10 (3× > A5) |
| flags.json martwe klucze | ✅ A4_TEST_FLAG, commitment_level + pełen ENABLE_* sweep | grep + python json scan | K-08 (0 dead ENABLE_*) |
| tools/ osierocone | ✅ 145 tools, orphan-mapa (timer/at/cron/import) | scratchpad/orphan_map.sh | 64 zero-ref → 11 dormant + ~45 graveyard (K-11) |
| epaka cross-project | ✅ tools/epaka_fetcher.py | head docstring | K-12 |
| Periferia failed unit | ✅ cod-weekly, not-found sweep | systemctl is-failed/list-units | K-13 (1 failed, 0 not-found) |
| Korelacja A4 (void-orphan) | ✅ post_shift_overrun_forward_replay i in. | cross-ref A4/alloc-audit | granica K↔E (§7) |

---

## LUKI POKRYCIA (jawne — nie cisza)

1. **Pełna lista 64 zero-ref tools NIE rozstrzygnięta 1:1** martwy-vs-on-demand — sklasyfikowałem heurystyką (nazwa verdict/review/reminder→dormant; data-stamped/probe/replay→graveyard) + spot-check 3 (latency_alarm/validate_state_schema/flag_effect_coverage = on-demand, NIE graveyard). ~5-8 „borderline" (np. `demand_forecast` code=1, `restaurant_prep_bias` cron=1 → ŻYWE; `sla_join_worker`, `osrm_fallback_smoke` niepewne) NIE prześwietlone treścią — graveyard ≈45-50 to przedział, nie dokładna liczba.
2. **Treść każdego graveyard-tool NIE czytana** — deadness z reference-grafu (0 timer/at/cron/import), NIE z analizy czy plik by się uruchomił. „Osierocony" ≠ „broken on run" (większość to prawdopodobnie działające jednorazówki bez konsumenta).
3. **Cross-repo dead-paths poza panelsync** — `courier_api` 41 .bak + `nadajesz_clone/panel` 72 .bak policzone zbiorczo (§4), NIE per-plik które są dead-fork vs zwykły backup. `ndj-client-panel`/`ndj-parcel`/`nadajesz-sms-wt` worktree (A5 §d, w `/root/`) NIE prześwietlone pod martwy kod (poza zakresem — feature-branche, nie dispatch-decyzja).
4. **`.retired` unity — czy systemd je jeszcze pamięta** (enabled symlink w `multi-user.target.wants`)? Nie sprawdzone — rename pliku zwykle wystarcza, ale residualny symlink możliwy. Faza C/ops może `systemctl list-unit-files | grep shift-notify`.
5. **Cron pełny** — sprawdziłem `crontab -l` + `/etc/cron.d/*`; NIE `/etc/crontab` per-user innych userów ani `/var/spool/cron` poza root — build_speed_tiers (root cron) złapany, inne usery poza zakresem (serwer single-user root).
6. **NIE odpalałem** żadnego tool/py_compile/import (READ-ONLY DoD) — „osierocony" dowodzi braku konsumenta, NIE że tool się nie kompiluje.
7. **Mailek/Papu** — GRANICA, poza zakresem (papu_dispatch_bridge zinwentaryzowany jako boundary w A5, nie tknięty).

---

## HANDOFF dla Faz D/E/F

- **Faza E (dedup):** K-01/02/03 = JEDEN root „skeletony Sprint-C F2.2 nigdy nieaktywowane" (C4/C6/C7, 2026-04). K-04/05 = root „migracja DEPRECATE_LEGACY_HARD_GATES nigdy nieodpalona" (R7+legacy-soft-pen). NIE liczyć 5 osobno — 2 rodziny. K-06 = DEAD member rootu **R2 route-order** (A6 grupa 2) — przy PoC „one route-order module" (Faza F) ten fork USUNĄĆ, nie równać.
- **Faza D (flagi):** martwa para `commitment_level`↔commitment_emitter + `A4_TEST_FLAG` test-leak → kandydaci do usunięcia z flags.json (klucz bez żywego konsumenta). Skeleton-flagi (`ENABLE_MID_TRIP_PICKUP`/`_PENDING_QUEUE_VIEW`/`_SPEED_TIER_LOADING_PLANNED`) = **miny C2** (flip uzbraja 2-mies. martwy kod) — oznaczyć w mapie sprzężeń jako „flip = full deploy nietestowanego skeletonu".
- **Faza C (oracle, granica K↔E):** §7 void-orphan instrumenty (`post_shift_overrun_forward_replay` reads key 0/282) — NIE czyścić jako K, naprawić/oznaczyć jako E PRZED użyciem do walidacji flipu (C9/C11).
- **Higiena (poza audytem, gdy GO):** 326+ `.bak` (polityka 24h martwa) + shift-notify potrójny grób + 4 drop-in .bak + epaka misplaced + cod-weekly FAILED = lista czyszczenia periferii. Niskie ryzyko, wysoka redukcja entropii grepów.
- **Korekty A-dokumentów:** A1 traffic_v2_aggregator „DEAD?" = ŻYWY; A5 `.bak` count pominął top-level dispatch_v2 (176); recon §C drop-in .bak = 4 nie 3 (czasowka); A3 `kill_switch_to_v1` = ŻYWY (panel_watcher:2621), `commitment_level` = MARTWY.
