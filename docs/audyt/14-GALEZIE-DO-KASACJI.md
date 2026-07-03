# 14 — WD-14: gałęzie zmergowane do mastera (kandydaci do kasacji)

Stan na 2026-07-03 ~13:50 (`git branch --merged master`, bez master i audyt/*). **40 gałęzi — kasacja po słowie Adriana** (`git branch -d` jest bezpieczny: usuwa TYLKO w pełni zmergowane; gałęzie trzymane przez żywe worktree'y git sam odrzuci).

```
auton/geo-districts
auton/gps02-accuracy-shadow
auton/legacy-test-fixes
auton/scale-01-caps-flags
auton/tier-gt-regen
auton/ziomek-hygiene
fix/alerty-danowe
fix/auton-blockers
fix/bug4-logger
fix/bug4-oracle
fix/cod-weekly-diag
fix/cron-health
fix/d3-fala-ab
fix/fingerprint-guard
fix/frozen-objektyw
fix/gc-observability
fix/grafik-h
fix/guard-teatr
fix/l01-registry
fix/l3-plan-recheck
fix/l4-available-from
fix/l71-r-declared-tripwire
fix/l73-split-layer-guard
fix/l74-feascarry-join
fix/l78-verdict-direction
fix/l8-iter1
fix/l8-iter2
fix/l8-iter3
fix/l8-mapa
fix/multicity
fix/o2-capz
fix/o2-odczyt
fix/pending-fcntl
fix/perf-lazy
fix/perf-slo
fix/sla-anchor
fix/telegram-delta
fix/tz-consolidate
fix/tz-drobnica
fix/watchdog-close
```
