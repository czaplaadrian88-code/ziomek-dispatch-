# OD-7 RETENTION ARCHIVER — raport REPORT

* run_id: `1afe6a20de67` · 2026-08-05T08:23:25.435137+00:00
* polityka: `OD-7/2026-08-03` sha256=26e6d0856dc6 (`/root/worktrees/dispatch_v2/active/20260805-od7-archiver-297-cto/tools/retention_od7_policy.json`)
* archive-root: NIE PODANY (raport nie tworzy katalogu)
* dysk żywy: 153 513.2 MB total, 12 309.1 MB wolne, 92.0 % zajęte

## Skan: 563 plików / 6 309.4 MB

| Akcja | Pliki | Rozmiar |
|---|---:|---:|
| SKIP_TOO_YOUNG | 39 | 2 802.0 MB |
| SKIP_NOT_ARCHIVABLE | 252 | 1 300.7 MB |
| REPORT_UNKNOWN | 250 | 997.7 MB |
| SQLITE_SNAPSHOT | 2 | 617.5 MB |
| SKIP_LIVE_APPEND | 11 | 328.9 MB |
| ARCHIVE | 9 | 262.5 MB |

| Klasa OD-7 | Pliki | Rozmiar |
|---|---:|---:|
| decision_logs | 39 | 1 828.4 MB |
| world_record | 15 | 1 354.2 MB |
| unknown | 250 | 997.7 MB |
| ops_logs | 195 | 972.1 MB |
| events_db | 1 | 560.8 MB |
| protected | 17 | 327.6 MB |
| gps | 8 | 267.7 MB |
| live_state | 38 | 0.9 MB |

## Bilans miejsca

* do archiwum TERAZ: 9 plików, 262.5 MB → po gzip ok. 35.1 MB
* snapshoty sqlite: 2 → ok. 115.0 MB
* zwolnione z żywego dysku przez TEN automat: 0.0 MB (kasowanie żywych plików należy do istniejących GC — patrz `live_delete_owner`)
* przyrost archiwum netto: ok. 150.1 MB

### Stan ustalony archiwum (przy dzisiejszym tempie)

| Klasa | Dziennie (po gzip) | Okno archiwum OD-7 | Docelowo |
|---|---:|---:|---:|
| world_record | 11.1 MB | 365 | 4 052.1 MB |
| decision_logs | 0.6 MB | 365 | 219.0 MB |

### Propozycja `--archive-root` (automat NIE tworzy katalogu)

Wzrost ok. 15.5 MB/dobę, stan ustalony ok. 5 670.3 MB. Żywy dysk (91 %+ zajęty) się NIE nadaje.

| Kandydat | Istnieje | Wolne na mouncie | Zajęte | Wystarczy |
|---|---|---:|---:|---|
| `/mnt/storagebox/archive` | tak | 1 029 403.8 MB | 1.8 % | TAK |
| `/mnt/storagebox/server-archives` | tak | 1 029 403.8 MB | 1.8 % | TAK |

## ⛔ KONFLIKTY POLITYKI (istniejący GC kasuje szybciej niż OD-7 pozwala)

| Reguła | OD-7 żywe [d] | GC kasuje po [d] | Owner kasowania |
|---|---:|---:|---|
| events.db | 180 | 2.0 | core/event_bus cleanup (dispatch-event-bus-cleanup.timer 04:00: processed 48h, audit_log 90d) |
| dec.observability_daily | 30 | 14.0 | observability/log_rotation.py --retention-days 14 (dispatch-log-rotation.timer 03:00) |
| wr.daily | 30 | 14.0 | world_record.py:_gc (RETENTION_DAYS=14, GC przy pierwszym zapisie dnia) |
| gps.positions_live | 90 | 1.0 | tools/gps_positions_gc.py (cron 04:50, TTL 24h) |

## Dług rotacji (żywe strumienie, których nie da się zarchiwizować w całości)

| Plik | Klasa | Rozmiar | Kto dziś kasuje |
|---|---|---:|---|
| `dispatch_state:gps_quality_shadow.jsonl` | gps | 169.7 MB | brak |
| `dispatch_state:learning_log.jsonl` | decision_logs | 85.5 MB | logrotate:/etc/logrotate.d/dispatch-v2 GRUPA B (learning_log, rotate 30); pozostale: brak |
| `dispatch_state:fleet_position_history.jsonl` | gps | 39.8 MB | brak |
| `dispatch_state:plan_recheck_log.jsonl` | decision_logs | 13.0 MB | logrotate GRUPA B-2 (rotate 30) |
| `dispatch_state:assignment_episode.jsonl` | decision_logs | 13.0 MB | logrotate:/etc/logrotate.d/dispatch-v2 GRUPA B (learning_log, rotate 30); pozostale: brak |
| `dispatch_state:decision_outcomes.jsonl` | decision_logs | 4.8 MB | logrotate:/etc/logrotate.d/dispatch-v2 GRUPA B (learning_log, rotate 30); pozostale: brak |
| `dispatch_state:backfill_decisions_outcomes_v1.jsonl` | decision_logs | 1.5 MB | logrotate:/etc/logrotate.d/dispatch-v2 GRUPA B (learning_log, rotate 30); pozostale: brak |
| `dispatch_state:gps_delivery_truth.jsonl` | gps | 1.3 MB | brak |
| `dispatch_state:decision_eta_log.jsonl` | decision_logs | 0.3 MB | core/jsonl_rotation.py + /etc/logrotate-dispatch-v2-jsonl.conf (rotate 30, maxage 30) |
| `dispatch_state:courier_gps_commitment_shadow.jsonl` | gps | 0.2 MB | brak |
| `dispatch_state:gps_positions_pwa.json.merge_shadow.jsonl` | gps | 0.0 MB | brak |

## Akcje do wykonania w APPLY

| Akcja | Plik | Klasa | Rozmiar | Wiek [d] | Powód |
|---|---|---|---:|---:|---|
| SQLITE_SNAPSHOT | `dispatch_state:events.db` | events_db | 560.8 MB | 0.01 | snapshot online bazy (Connection.backup, read-only) — archiwum bezterminowe; ŻADNE wiersze nie są kasowane |
| ARCHIVE | `dispatch_state:world_record/world_record-20260722.jsonl` | world_record | 97.6 MB | 13.35 | wiek 13.35d >= próg 11.0d |
| ARCHIVE | `dispatch_state:world_record/world_record-20260724.jsonl` | world_record | 62.6 MB | 11.35 | wiek 11.35d >= próg 11.0d |
| SQLITE_SNAPSHOT | `dispatch_state:courier_api.db` | gps | 56.7 MB | 0.0 | snapshot online bazy (Connection.backup, read-only) — archiwum 270 dni; ŻADNE wiersze nie są kasowane |
| ARCHIVE | `dispatch_state:world_record/world_record-20260723.jsonl` | world_record | 51.1 MB | 12.35 | wiek 12.35d >= próg 11.0d |
| ARCHIVE | `dispatch_state:observability/fleet_filter_20260724.jsonl` | decision_logs | 18.2 MB | 11.35 | wiek 11.35d >= próg 11.0d |
| ARCHIVE | `dispatch_state:observability/fleet_filter_20260722.jsonl` | decision_logs | 11.7 MB | 13.35 | wiek 13.35d >= próg 11.0d |
| ARCHIVE | `dispatch_state:observability/fleet_filter_20260723.jsonl` | decision_logs | 11.2 MB | 12.35 | wiek 12.35d >= próg 11.0d |
| ARCHIVE | `dispatch_state:observability/candidate_decisions_20260722.jsonl` | decision_logs | 3.5 MB | 13.35 | wiek 13.35d >= próg 11.0d |
| ARCHIVE | `dispatch_state:observability/candidate_decisions_20260724.jsonl` | decision_logs | 3.2 MB | 11.35 | wiek 11.35d >= próg 11.0d |
| ARCHIVE | `dispatch_state:observability/candidate_decisions_20260723.jsonl` | decision_logs | 3.2 MB | 12.35 | wiek 12.35d >= próg 11.0d |

## UNKNOWN — 250 plików / 997.7 MB (NIGDY nie ruszane, decyzja ownera)

| Plik | Rozmiar | Wiek [d] |
|---|---:|---:|
| `dispatch_state:r6_breach_shadow.jsonl` | 221.6 MB | 0.0 |
| `dispatch_state:v319c_read_shadow_log.jsonl.1` | 100.6 MB | 2.35 |
| `dispatch_state:consumer_stuck_alert_evaluations.jsonl.1` | 100.3 MB | 5.35 |
| `dispatch_state:drive_min_calibration_log_v2.jsonl` | 82.7 MB | 0.0 |
| `dispatch_state:drive_min_enriched.jsonl` | 63.0 MB | 0.04 |
| `dispatch_state:lex_window_ledger_v2_observations.jsonl` | 60.2 MB | 0.52 |
| `dispatch_state:lex_window_ledger_v2.jsonl` | 48.2 MB | 0.52 |
| `dispatch_state:obj_replay_capture.jsonl` | 44.8 MB | 0.0 |
| `dispatch_state:v319c_read_shadow_log.jsonl` | 44.6 MB | 0.0 |
| `dispatch_state:eta_calib_shadow.jsonl` | 36.3 MB | 0.13 |
| `dispatch_state:feas_carry_blind_shadow.jsonl` | 20.2 MB | 0.59 |
| `dispatch_state:eta_calibration_log.jsonl` | 18.5 MB | 0.01 |
| `dispatch_state:bug4_reseq_shadow.jsonl` | 13.9 MB | 0.53 |
| `dispatch_state:orders_state.pre-prune-2026-06-04.json` | 8.2 MB | 62.46 |
| `dispatch_state:carried_first_guard.jsonl` | 8.0 MB | 0.52 |
| `dispatch_state:pickup_lateness_shadow.jsonl` | 8.0 MB | 0.4 |
| `dispatch_state:pickup_floor_guard.jsonl` | 7.2 MB | 0.0 |
| `dispatch_state:consumer_stuck_alert_evaluations.jsonl` | 6.9 MB | 0.0 |
| `dispatch_state:customer_dwell.json` | 5.7 MB | 0.01 |
| `dispatch_state:ziomek_pred_calibration.jsonl` | 5.6 MB | 0.03 |

## PII — detekcja (liczniki trafień, BEZ wartości)

| Plik | Klasa | Trafienia |
|---|---|---|
| `events.db` ⚠ binarny | events_db | value:phone_pl=13874, value:postal_pl=51, value:street_pl=38, value:email=14 |
| `world_record/world_record-20260722.jsonl` | world_record | key:delivery_address:redact=110, key:address_id:pseudonymize=110, key:pickup_address:redact=90, key:sha:47c99d9b:redact=40, value:phone_pl=34, value:street_pl=7 |
| `world_record/world_record-20260724.jsonl` | world_record | key:delivery_address:redact=173, key:address_id:pseudonymize=173, key:pickup_address:redact=148, value:phone_pl=75, key:sha:47c99d9b:redact=50, key:street:redact=8 |
| `courier_api.db` ⚠ binarny | gps | value:email=13, value:phone_pl=9 |
| `world_record/world_record-20260723.jsonl` | world_record | key:delivery_address:redact=169, key:address_id:pseudonymize=169, key:pickup_address:redact=143, value:phone_pl=130, value:postal_pl=64, value:email=54 |
| `observability/candidate_decisions_20260722.jsonl` | decision_logs | key:delivery_address:redact=2876, value:phone_pl=13, value:street_pl=7 |
| `observability/candidate_decisions_20260724.jsonl` | decision_logs | key:delivery_address:redact=3095, value:postal_pl=49, value:phone_pl=10 |
| `observability/candidate_decisions_20260723.jsonl` | decision_logs | key:delivery_address:redact=2140, value:postal_pl=63, value:phone_pl=12 |

