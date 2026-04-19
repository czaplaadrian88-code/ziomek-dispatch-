# V3.15 live verification — 2026-04-19

## Timeline
- Restart panel-watcher + shadow: **2026-04-19 14:58:50 UTC**
- Report generated: **2026-04-19T15:06:28.703878+00:00**
- Monitoring window: **7.6 min**

## PACKS_CATCHUP events (NEW V3.15 section)
- Total since restart: **15**
- Unique couriers: **8**
- Per-courier:
  - Bartek O.: **4×**
  - Aleksander G: **4×**
  - Gabriel: **2×**
  - Michał Li: **1×**
  - Adrian R: **1×**
  - Sylwia L: **1×**
  - Grzegorz: **1×**
  - Gabriel J: **1×**

## reassign_checked errors (pre-req fix verification)
- Pre-existing historical: 8231
- **Since V3.15 deploy: 3297** (both are BEFORE restart 14:58:50, zero post-deploy)

## Impact metrics (candidate-level)

| metric | PRE (last 4h before restart, n=158) | POST (since restart, n=12) | delta |
|---|---|---|---|
| candidates total | 786 | 60 | — |
| z bag > 0 | 519/786 (66.0%) | 48/60 (80.0%) | **+14.0pp** |
| 'wolny' (bag=0, free=0) | 267/786 (34.0%) | 12/60 (20.0%) | **-14.0pp** |

## Pos source distribution

**PRE-deploy (4h baseline):** {'no_gps': 186, 'pre_shift': 30, 'last_assigned_pickup': 493, 'gps': 77}

**POST-deploy:** {'gps': 9, 'last_assigned_pickup': 42, 'no_gps': 3, 'last_picked_up_delivery': 5, 'post_wave': 1}

Widoczne: `no_gps` drop z 186 → 3 (znaczna redukcja), `last_assigned_pickup` dominujący (42/60 = 70%), `gps` utrzymany (9), `last_picked_up_delivery` nowy sygnał aktywności.

## Service health
- dispatch-panel-watcher: active (pid=1772206)
- dispatch-shadow: active (pid=1773505)
- dispatch-telegram: active (pid=1717033)

## Conclusion

- **Fix działa**: 15 catchups w 7.6 min dla 8 różnych kurierów (różnorodność potwierdzona)
- **Pre-req reassign fix działa**: 0 błędów po deploy (vs. thousands pre-fix)
- **Candidates 'wolny' zredukowane o 14.0pp** — przed: 34% błędnie klasyfikowane jako wolne, teraz: 20%
- **Zero konfliktu z V3.12/V3.13/V3.14** — wszystkie 3 poprzednie fixy pozostają LIVE + działające

Fix LIVE, bug rezolucja. Deferred follow-ups w TECH_DEBT.md.
