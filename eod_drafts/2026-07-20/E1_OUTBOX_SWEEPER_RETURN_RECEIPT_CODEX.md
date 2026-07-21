# E1 sweeper + RETURN receipt — autor Codex, 2026-07-20

`sol/xhigh`; base `2c72bb40`. Bez live/merge/deployu/restartu/flipa.

Mapa kompletności:

| miejsce | status / dowód |
|---|---|
| outbox + tick | TAK: 30 s/100, trwały fair retry obu lane, `completed`; przed fetch i przy awarii panelu |
| RETURN raw/current/snapshot | TAK: CID+autoryzacje w receipt przed callbackiem; retry, CAS, generation guard |
| ASSIGNED A→B (reassign+packs) | TAK: previous CID i ta sama trwała ścieżka release |
| plan-recheck | TAK: cleanup/recanon i writery gap-fill/refloor/invalidate/GC pod finalnym state/CAS fence; file+dir fsync |
| learning | TAK: exact proposal/czas/próg w receipt; SQLite projection |
| parcel-native | N-D: dotychczas nie mutuje `courier_plans` |
| AUTO_KOORD/pasywny CK/touch | N-D: inna semantyka, poza RETURN/ASSIGNED |
| systemd | N-D: użyto istniejącego ticku |
| repo `flags.json` | N-D: brak śledzonego pliku; const i rejestr mają default OFF; FLIPMASTER doda live `false` |

Testy: baseline `125`; klaster `193 passed`; sąsiednie `118 passed`; flag-check `512/512`, py_compile/diff PASS. ON≠OFF: `test_panel_sweeper_flag_on_differs_from_off_and_logs_metric`, `test_foreground_respects_sweeper_cooldown_without_delaying_new_target` i cooldown state. Sześć RETURN zachowano; `snapshot_cleanup_retry` dowodzi retry ticku.

Rollback: `ENABLE_STATE_OUTBOX_SWEEPER=false`; release także `ENABLE_REASSIGN_OLD_PLAN_RELEASE=false`; kod przez `git revert`. Rozpoczęte receipty celowo się domykają; schema SQLite jest addytywna.

Ryzyka: backlog >100 schodzi tickami; cofnięcie zegara wydłuży age-gate; JSONL/recanon są at-least-once — crash po efekcie przed receipt może dać deduplikowalny rekord lub dodatkowy `plan_version`, nie utratę cleanupu. Semantyka pozostałych 5 seam-case'ów zachowana. DoD HOLD: pełna suita/E2E/replay CTO i mechaniczny false-positive bliźniaków. CTO: live klucz OFF, suita, merge; deploy/restart/flip tylko z ACK.
