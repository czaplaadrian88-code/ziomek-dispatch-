# A360-T0 TEST-TRUTH — niezależny odbiór commita `4e782e8`

Data: 2026-07-11 UTC

Tryb: offline/test-tooling; zero operacji live

Branch: `review/a360-t0-test-truth`

Werdykt: **ACCEPT / DONE** — dwie udowodnione luki TEST-12 naprawione,
Sprint 3 zintegrowany, a świeże pełne DEFAULT i STRICT mają 0 failed.

## Finalne domknięcie integratora — aktualna baza Sprintu 3

Integrator przeniósł `d30b344`, `e00f0cc` i `f015c9f` na czystą bazę
`a860c53` w branchu `integration/a360-wave1-close`. Historyczny ratchet
`ENABLE_STAGE_TIMING_OBSERVATION` jest już pełnym carrierem Sprintu 3, więc
zewnętrzny HOLD przestał istnieć.

Dowód po integracji:

- klaster T0/guard/registry/lifecycle: **52 passed**;
- pełny DEFAULT: **4941 passed, 24 skipped, 10 xfailed, 0 failed**;
- pełny `HERMETIC_STRICT=1`: **4891 passed, 74 skipped, 10 xfailed, 0 failed**;
- lifecycle: **505/505**, 0 błędów;
- Audit360 validator: **required=35, findings=110, tools=15, OK**;
- JSON, py_compile i `diff --check`: PASS.

Worktree integracyjny dostał taki sam read-only carrier symlink jak wydaniowy
worktree Sprintu 3; pytest kopiował flagi do `tmp_path`, a HERMETIC-GUARD nadal
blokował każdy zapis do żywego `flags.json`. Pierwszy bieg bez carriera dał
4933/9 failed i został jawnie zachowany jako negatywna kontrola setupu.

Zmiana jest wyłącznie testowo-dokumentacyjna. Nie wykonano flipa, deployu,
restartu ani zapisu danych live. Tmux57 został zamknięty po pushu brancha.

## Zakres odbioru

Odbiór wykonano względem bazy `70af4fa`. Zweryfikowano pełny diff 11 plików:
narzędzie rejestru flag, lifecycle metadata/checker, testy TEST-11, pięć klas
TEST-12 oraz jeden dokładny wpis kwarantanny dla jawnego live-smoke. Diff nie
dotyka `core/`, feasibility, scoringu, selekcji, planu, `flags.json`, runtime
state ani usług.

## ETAP 0 — stan zastany, wyłącznie read-only

- Lane: `/root/a360_t0_wt/dispatch_v2`, branch
  `review/a360-t0-test-truth`, HEAD przed aktualizacją raportu `e00f0cc`, status
  czysty. Zamrożona baza odbioru: `4e782e8e3c5e4a94522a5d560973208455a61ae1`;
  porównanie semantyczne: `70af4fa..4e782e8`.
- `tmux 57` jest aktywnym właścicielem lane'a T0. `tmux 60` jest aktywnym
  właścicielem integracji Sprintu 3; worktree
  `/root/sprint3_release_wt/dispatch_v2` nie był czytany ani modyfikowany.
- Master podczas odbioru przesunął się do `a860c53` (`sprint3-live-20260711`).
  Chroniony dirty `eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md` pozostał
  nietknięty.
- Stan usług odczytowo: `dispatch-shadow` active/running, PID 573430,
  `NRestarts=0`, wejście 10:27:21 UTC; `dispatch-panel-watcher` active/running,
  PID 3659486, `NRestarts=0`; `dispatch-sla-tracker` active/running, PID 2998575,
  `NRestarts=0`; `dispatch-telegram` inactive/dead zgodnie z kanonem. Nie było
  restartu, deployu ani sygnału do procesu.
- Timery sprawdzono przez `systemctl list-timers --all 'dispatch-*'`: 69 wpisów;
  cykliczne timery miały świeże odpalenia, a historyczne/review timery bez NEXT
  pozostają jawnie widoczne. `atq`: jeden zastany job 214 na 2026-07-13 12:15 UTC;
  nie zmieniono go.
- Freshness: współdzielony `flags.json` mtime 10:27:12 UTC; live
  `dispatch_state` mtime 10:50:12 UTC podczas odczytu. Treści live state nie
  czytano.
- Efektywny carrier flag: `USE_V2_PARSER=true` z `flags.json`, przy czym stary
  env `dispatch-panel-watcher=1` jest martwym carrierem przykrytym przez JSON.
  `ENABLE_STAGE_TIMING_OBSERVATION=true` pojawiło się w shared `flags.json` o
  10:27; frozen T0 widzi je jako sierotę, natomiast aktualny master Sprintu 3
  ma już symbol w `common.py` i listach ETAP4/fingerprint. To zewnętrzny drift
  czasowy, nie rozjazd do naprawy w allowliście T0.

## Mapa kompletności writer/consumer

| Miejsce | Rola | Writer / consumer | Dotknięte | Powód i test |
|---|---|---|---|---|
| `tools/flag_registry.py` | skan common/JSON/systemd/code | writer: jawne argumenty wejściowe; consumer: render, entropy i TEST-11 | TAK w `4e782e8` | DI wszystkich źródeł; synthetic known-answer i mutacja ignorująca `flags_path` |
| `tests/test_flag_registry_f3.py` | oracle TEST-11 | writer: `tmp_path` common/flags/systemd; consumer: `build_registry` | TAK w `4e782e8` | live fallback ma wywrócić semantykę; live-smoke wydzielony dokładnym nodeidem |
| `tools/flag_lifecycle_registry.json` | kuratorowany lifecycle | writer: merge-seed/kuracja; consumers: checker i testy ZP107 | TAK w `4e782e8` | `USE_V2_PARSER` closed, 504/504 curated |
| `tools/flag_lifecycle_check.py` + `test_flag_lifecycle_zp107.py` | walidacja lifecycle | writer: syntetyczne flags JSON w teście; consumer: CLI/check functions | TAK w `4e782e8` | generic known-drift zachowany, historyczny wyjątek V2 zamknięty |
| `tests/hermetic_quarantine.json` | zewnętrzna kwarantanna | writer: dokładny wpis; consumer: root `conftest.py` | TAK w `4e782e8` | tylko `test_build_registry_smoke_live`; pięć TEST-12 nie jest kwarantannowane |
| `test_resolve_cid_score_based.py` | TEST-12 alias/scoring | writer: syntetyczne `kids` i tmp debug JSONL; consumer: `resolve_cid`/shift state | TAK w `4e782e8` + fix `e00f0cc` | ukryty prod-write debug-logu skierowany do temp; anti-prod assert + mutation |
| `test_v319h_bug4_tier_cap_matrix.py` | TEST-12 tier loader | writer: tmp `courier_tiers.json`; consumer: `_load_courier_tiers` | TAK w `4e782e8` + tripwire `e00f0cc` | loader cache/mtime na syntetycznym pliku; przekierowanie live daje RED w STRICT |
| `test_v320_packs_ghost.py` | TEST-12 panel packs | writer: syntetyczne parsed/state/details, tmp recheck queue/lock; consumer: `_diff_and_emit` | TAK w `4e782e8` + fix `e00f0cc` | ukryty prod-write lockfile'a skierowany do temp; `pw.open` zwraca wyłącznie fixture KID |
| `test_v325_step_d_r03.py` | TEST-12 manual overrides | writer: cztery pliki temp; consumer: `manual_overrides.parse_command` | TAK w `4e782e8` + tripwire `e00f0cc` | komplet identity + override pod tmpdir; usunięcie trzech identity fixtures daje 12 RED |
| `test_v326_hotfix_parser.py` | TEST-12 parser/identity | writer: cztery pliki temp; consumer: parser i manual overrides | TAK w `4e782e8` + tripwire `e00f0cc` | komplet identity + override pod tmpdir; usunięcie trzech identity fixtures daje 25 RED |

Wszystkie pozycje są TAK. Nie ma konsumenta w core/pipeline/feasibility/scoring/
planie wymagającego zmiany; zachowanie runtime jest N-D, bo commit dotyczy
wyłącznie narzędzia read-only, rejestru i testów.

## Ustalenia

1. TEST-11 używa jawnych syntetycznych wejść `common.py`, `flags.json`, katalogu
   systemd i code-roots. Domyślne ścieżki hosta pozostają wyłącznie trybem
   operatorskim/live-smoke.
2. Pozostały carrier `USE_V2_PARSER` jest raportowany jako
   `json-overrides-env/open`; nieaktualny wyjątek `known-open` został usunięty
   po migracji i flipie z 2026-07-10.
3. Pięć klas TEST-12 dostało deterministyczne fixture aliasów, tierów,
   `kurier_ids` i identity. W STRICT testy wykonują się i przechodzą — nie
   zostały ukryte kwarantanną.
4. Kwarantanna dostała tylko dokładny nodeid jawnego read-only smoke
   `test_flag_registry_f3.py::test_build_registry_smoke_live`, z konkretnym
   powodem. HERMETIC-GUARD nie został osłabiony.
5. Szczegółowa kontrola anty-prod ujawniła dwie luki zamaskowane przez fail-soft:
   `test_resolve_cid_score_based` próbował pisać żywy
   `courier_match_debug.jsonl` poza lokalnymi context managerami, a
   `test_v320_packs_ghost` próbował drenować produkcyjny lockfile
   `coordinator_time_recheck`. HERMETIC-GUARD blokował oba zapisy, lecz kod
   testowany połykał wyjątki, więc testy pozostawały zielone.
6. Fix-forward kieruje oba efekty do procesowych katalogów tymczasowych. Wszystkie
   pięć TEST-12 ma jawny tripwire efektywnej ścieżki/fixture anty-prod.
7. Nie znaleziono zmiany zachowania runtime ani relacji HARD/SOFT.

## Niezależne dowody

- zmienione testy: **33 passed** DEFAULT;
- zmienione testy STRICT: **32 passed, 1 skipped** — wyłącznie jawny live-smoke;
- guard + registry/lifecycle cluster: **44 passed**;
- lifecycle checker repo-hermetic: **504/504 curated, 0 błędów**;
- validator pakietu Audit 360:
  **`AUDIT360_VALIDATE OK required=35 findings=110 tools=15`**;
- przed zewnętrznym driftem nośnika pełna suita DEFAULT: **4851 passed,
  24 skipped, 10 xfailed, 0 failed**;
- przed zewnętrznym driftem nośnika pełna suita `HERMETIC_STRICT=1`:
  **4801 passed, 74 skipped, 10 xfailed, 0 failed**;
- `py_compile`, JSON i `git diff --check 70af4fa..HEAD`: PASS;
- pięć TEST-12 z `ZIOMEK_SCRIPTS_ROOT` wskazującym worktree bez sąsiedniego
  `flags.json` i bez produkcyjnego `dispatch_state`: **5 passed**.

Pełne wyniki odtworzono z rozdzieleniem:
`PYTHONPATH=/root/a360_t0_wt` dla kodu worktree oraz
`ZIOMEK_SCRIPTS_ROOT=/root/.openclaw/workspace/scripts` dla współdzielonego,
read-only configu zgodnie z kontraktem harnessu.

## Kontrola negatywna

Po potwierdzeniu czystego worktree tymczasowo zmieniono
`build_registry(... flags_path=...)`, aby ignorował jawny `flags_path`.
`test_post_migration_json_overrides_env_is_open` przeszedł GREEN -> RED:
oczekiwany wpis `USE_V2_PARSER` zniknął z issues. Mutację cofnięto odwrotnym
patchem; test wrócił do GREEN, a worktree był czysty przed zapisaniem raportu.

Dla każdego z pięciu TEST-12 wykonano osobny probe przez usunięcie/przekierowanie
jego fixture na produkcyjny nośnik. Każdy przeszedł GREEN -> RED w STRICT:

- aliasy CID: odczyt żywego `kurier_ids.json` został zablokowany;
- tiery: żywy `courier_tiers.json` stał się niedostępny i oracle loadera padł;
- packs ghost: brak interceptora `kurier_ids` wywołał RED;
- oba parsery manual overrides: wyłączenie kompletu trzech źródeł identity
  wywołało odpowiednio 12 i 25 faili wewnętrznych.

Probe aliasów odsłonił dodatkowo próby zapisu debug-logu, a probe packs ghost —
próby zapisu lockfile'a force-recheck. Po fix-forward i odwróceniu wszystkich
mutacji DEFAULT, STRICT oraz STRICT bez kanonicznego config/state dały **5/5**.

## Historyczny zewnętrzny drift podczas odbioru — rozstrzygnięty

Po fix-forward pełna suita zobaczyła nowy klucz
`ENABLE_STAGE_TIMING_OBSERVATION` we współdzielonym `flags.json`. Klucz nie
istniał podczas pierwszych pełnych zielonych przebiegów i należy do integracji
Sprintu 3 w tmux 60. Zamrożony kod T0 nie zawiera jeszcze jego rejestracji w
`ETAP4_DECISION_FLAGS`, więc ratchet poprawnie czerwieni:

- natywny DEFAULT po fixie: **4850 passed, 1 failed, 24 skipped, 10 xfailed**;
- natywny STRICT po fixie: **4800 passed, 1 failed, 74 skipped, 10 xfailed**;
- jedyny fail w obu: `test_no_new_unstripped_flags_ratchet` z dokładnie jednym
  nowym kluczem `ENABLE_STAGE_TIMING_OBSERVATION`;
- po diagnostycznym wyłączeniu wyłącznie tego zewnętrznego nodeidu:
  DEFAULT **4850 passed, 0 failed, 1 deselected**; STRICT **4800 passed,
  0 failed, 1 deselected**.

Lane T0 nie miał prawa edytować tego testu, `common.py`, współdzielonego
`flags.json` ani worktree Sprintu 3. Dlatego pierwotny HOLD był poprawny.
Integrator następnie przyjął carrier Sprintu 3 na `a860c53` i powtórzył pełny
DEFAULT+STRICT; finalny wynik 0 failed zamknął tę bramkę.

### Jawna rozbieżność korzenia worktree

Wykonano także literalny wariant wymagany w zleceniu:
`ZIOMEK_SCRIPTS_ROOT=/root/a360_t0_wt PYTHONPATH=/root/a360_t0_wt`. Sam pkgroot
worktree nie zawiera sąsiedniego `flags.json`, którego wymagają stare testy
strip/doc-coverage i część script-runnerów. Wynik nie był zielony:

- DEFAULT: **4843 passed, 9 failed, 24 skipped, 9 xfailed**;
- STRICT: **4793 passed, 9 failed, 74 skipped, 9 xfailed**.

Trzy faile są bezpośrednim `FileNotFoundError: /root/a360_t0_wt/flags.json`,
trzy dalsze wynikają z pustego strip/doc coverage, a trzy script-runnery zmieniają
zachowanie bez nośnika flag. Nie utworzono sztucznego `flags.json` w pkgroot i nie
zamaskowano tych wyników. Kanoniczny harness repo rozdziela kod i carrier:
`PYTHONPATH=/root/a360_t0_wt`,
`ZIOMEK_SCRIPTS_ROOT=/root/.openclaw/workspace/scripts`; w tym wariancie pozostaje
wyłącznie opisany wyżej jeden zewnętrzny fail Sprintu 3.

## Dokładne skip/xfail/xpass

DEFAULT ma cztery modułowe skipy kolekcji:

- `tests/test_cod_weekly_preflight.py`
- `tests/test_cod_weekly_split_week.py`
- `tests/test_v325_step_a_r02.py`
- `tests/test_v325_step_c_r04.py`

oraz 20 dokładnych nodeidów runtime:

- `tests/test_canon_static_check_a1.py::test_defer_completion_guard_armed_when_defer_exists`
- `tests/test_cod_weekly.py::test_find_target_cod_columns`
- `tests/test_cod_weekly.py::test_find_target_column_auto`
- `tests/test_cod_weekly.py::test_validate_target_column`
- `tests/test_ml_twomodel.py::TestArtifactsExistAndServingParity::test_artifacts_present`
- `tests/test_ml_twomodel.py::TestArtifactsExistAndServingParity::test_gate_solo_pairwise_exceeds_80pct`
- `tests/test_ml_twomodel.py::TestArtifactsExistAndServingParity::test_old_model_collapses_on_solo`
- `tests/test_ml_twomodel.py::TestArtifactsExistAndServingParity::test_serving_reproduces_reported_solo_pairwise`
- `tests/test_ml_twomodel.py::TestArtifactsExistAndServingParity::test_solo_model_has_no_bundle_features`
- `tests/test_ml_twomodel.py::TestDatasetColumnReconstructionParity::test_loser_bag_size_category_reconstruction`
- `tests/test_ml_twomodel.py::TestDatasetColumnReconstructionParity::test_winner_bag_size_category_reconstruction`
- `tests/test_ml_twomodel.py::TestDatasetColumnReconstructionParity::test_winner_idle_category_reconstruction`
- `tests/test_ml_twomodel.py::TestDatasetColumnReconstructionParity::test_winner_idle_min_capped_reconstruction`
- `tests/test_ml_twomodel.py::TestParityReportArtifact::test_parity_report_present_and_flags_skews`
- `tests/test_ml_twomodel.py::TestTwoModelRoutingWithArtifacts::test_flag_on_produces_result`
- `tests/test_ml_twomodel.py::TestTwoModelRoutingWithArtifacts::test_router_splits_by_bag_state`
- `tests/test_mp11_jsonl_appender.py::test_permission_denied_raises_oserror`
- `tests/test_parser_v2_property_based.py::test_backward_compat_real_fixture`
- `tests/test_scoring_scenarios.py::test_scoring_scenarios_legacy_check_feasibility_removed`
- `tests/test_v3273_wait_courier.py::test_integration_468945_andrei_wait_12_6_min_real_log`

STRICT dodaje do powyższych dokładnie 50 nodeidów:

- `tests/test_eta_residual_infer.py::test_logger_flag_on_populates_and_consistent`
- `tests/test_flag_registry_f3.py::test_build_registry_smoke_live`
- `tests/test_geo05_district_adjacency.py::test_intra_city_adjacency_centroid_sanity`
- `tests/test_health_all_aggregator.py::test_build_all_snapshot_healthy`
- `tests/test_health_all_aggregator.py::test_build_all_snapshot_no_worker_heartbeat`
- `tests/test_pin_gps_commands.py::TestGpsInstructionHandler::test_ambiguous_courier_returns_disambig`
- `tests/test_pin_gps_commands.py::TestGpsInstructionHandler::test_with_name_personalized`
- `tests/test_pin_gps_commands.py::TestPinCommandHandler::test_pin_ambiguous_lists_options`
- `tests/test_pin_gps_commands.py::TestPinCommandHandler::test_pin_with_cid`
- `tests/test_pin_gps_commands.py::TestPinCommandHandler::test_pin_with_name`
- `tests/test_pin_gps_commands.py::TestResolveCourier::test_ambiguous_partial`
- `tests/test_pin_gps_commands.py::TestResolveCourier::test_canonical_dotless_match`
- `tests/test_pin_gps_commands.py::TestResolveCourier::test_cid_lookup`
- `tests/test_pin_gps_commands.py::TestResolveCourier::test_dotted_legacy_query_normalizes`
- `tests/test_prep_bias.py::test_real_log_smoke_if_present`
- `tests/test_prep_variance_anomaly_fail04.py::test_real_meta_loads_and_has_high_variance`
- `tests/test_r04_v2_evaluator.py::TestR04SchemaIntegrity::test_gold_promotion_blocked`
- `tests/test_r04_v2_evaluator.py::TestR04SchemaIntegrity::test_peak_window_hours`
- `tests/test_r04_v2_evaluator.py::TestR04SchemaIntegrity::test_schema_loadable`
- `tests/test_r04_v2_evaluator.py::TestR04V2Evaluator::test_A_bartek_gold_candidate`
- `tests/test_r04_v2_evaluator.py::TestR04V2Evaluator::test_B_mateusz_o_gold_part_time`
- `tests/test_r04_v2_evaluator.py::TestR04V2Evaluator::test_C_gabriel_gold`
- `tests/test_r04_v2_evaluator.py::TestR04V2Evaluator::test_D_adrian_r_std_plus_maintained`
- `tests/test_r04_v2_evaluator.py::TestR04V2Evaluator::test_E_dariusz_m_std_to_std_plus_promotion`
- `tests/test_r04_v2_evaluator.py::TestR04V2Evaluator::test_F_andrei_k_std_sustained_suppressed`
- `tests/test_r04_v2_evaluator.py::TestR04V2Evaluator::test_G_albert_dec_insufficient_data`
- `tests/test_r04_v2_evaluator.py::TestR04V2Evaluator::test_H_szymon_sa_new_insufficient`
- `tests/test_r04_v2_evaluator.py::TestR04V2Evaluator::test_I_michal_li_slow_to_std_promotion`
- `tests/test_r04_v2_evaluator.py::TestR04V2Evaluator::test_J_low_volume_insufficient`
- `tests/test_r04_v2_evaluator.py::TestR04V2Evaluator::test_K_speed_completeness_gate`
- `tests/test_roadfactor_gap.py::test_real_log_runs_and_drops_outliers`
- `tests/test_route_order_live_parity.py::test_live_route_order_parity_and_flag_pin`
- `tests/test_state_schema_validator.py::test_live_state_no_drift_summary`
- `tests/test_state_schema_validator.py::test_live_state_passes`
- `tests/test_state_schema_validator.py::test_synthetic_drift_dict_of_entries_fails`
- `tests/test_v325_pin_leak_defense.py::script_run`
- `tests/test_working_override_2026_06_01.py::test_10_synthetic_pos_when_no_gps`
- `tests/test_working_override_2026_06_01.py::test_11_flag_off_ignores_working`
- `tests/test_working_override_2026_06_01.py::test_12_working_ended_skipped`
- `tests/test_working_override_2026_06_01.py::test_13_real_shift_wins_over_working`
- `tests/test_working_override_2026_06_01.py::test_14_jest_only_no_grafik_add`
- `tests/test_working_override_2026_06_01.py::test_1_pracuje_adds_working`
- `tests/test_working_override_2026_06_01.py::test_2_nie_pracuje_removes_working`
- `tests/test_working_override_2026_06_01.py::test_3_pracuje_do_hour`
- `tests/test_working_override_2026_06_01.py::test_4_parse_bounds`
- `tests/test_working_override_2026_06_01.py::test_5_reset_clears_working`
- `tests/test_working_override_2026_06_01.py::test_6_unknown_cid_no_grafik_add`
- `tests/test_working_override_2026_06_01.py::test_7_offgrafik_with_working_dispatchable`
- `tests/test_working_override_2026_06_01.py::test_8_offgrafik_no_working_excluded`
- `tests/test_working_override_2026_06_01.py::test_9_cidkeyed_no_leak_to_other_courier`

Oba tryby mają te same 10 XFAIL:

- `tests/test_daily_stats_presnapshot.py::script_run`
- `tests/test_demote_tier_bucket_p4.py::test_offmode_preserves_demote_across_tiers`
- `tests/test_invariant_slots_l04.py::test_inv_coh_r_declared_tripwire_exists`
- `tests/test_invariant_slots_l04.py::test_inv_layer_hard_before_soft_reassert_after_readmit`
- `tests/test_invariant_slots_l04.py::test_inv_life_loadplan_pure_default`
- `tests/test_invariant_slots_l04.py::test_inv_src_equal_treatment_pre_shift_twin_parity`
- `tests/test_obj_food_age_bug5.py::test_fa_t2_flag_on_delivers_ready_before_unready_pickup`
- `tests/test_obj_food_age_bug5.py::test_fa_t5_override_is_the_live_toggle`
- `tests/test_reconcile_dry_run.py::script_run`
- `tests/test_v319d_read_integration.py::script_run`

XPASS: **0** w DEFAULT i **0** w STRICT.

## Ryzyka, rollback i live

- Zastane ostrzeżenia pytest oraz historyczne skip/xfail są poza zakresem T0;
  oceniono listę, nie samą sumę.
- Fix-forward dotyka wyłącznie pięciu dozwolonych plików TEST-12; kod produkcyjny,
  rejestr, lifecycle, kwarantanna i runtime pozostają nietknięte.
- Nie naprawiano ani nie maskowano zewnętrznego ratcheta Sprintu 3; jego
  disposition należy do tmux 60/integratora.
- Rollback przed merge: nie scalać lane'a. Po merge: revert commita fix-forward
  z tego odbioru, a dla całego T0 dodatkowo `git revert 4e782e8`.
- Nie wykonano flipa, migracji, zapisu runtime, restartu, deployu ani odczytu
  sekretów. Shared backlog i repo pamięci pozostają dla integratora G0.
