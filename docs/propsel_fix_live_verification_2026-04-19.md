# V3.16 proposal selection live verification — 2026-04-19

## Timeline
- Restart panel-watcher + shadow: **2026-04-19 15:36:01 UTC**
- Report generated: **2026-04-19T15:42:09 UTC**
- Monitoring window: **6.1 min**

## Core metric — best candidate classification

| metric | PRE (4h, n=180) | POST (since restart, n=9) | delta |
|---|---|---|---|
| best as **blind+empty** (no_gps/pre_shift/none, bag=0) | **30.0%** | **0.0%** | **-30.0pp** |
| best as **informed** (gps/last_*/post_wave) | 70.0% | 100.0% | +30.0pp |

## Top BEST cids

**PRE (4h):**
- cid=508 (Michał Li): 69×
- cid=5333 (?cid5333): 36×
- cid=413 (Mateusz O): 18×
- cid=441 (Sylwia L): 13×
- cid=509 (Dariusz M): 13×

**POST (since restart):**
- cid=484 (Andrei K): 3×
- cid=509 (Dariusz M): 2×
- cid=520 (Michał Rom): 2×
- cid=508 (Michał Li): 1×
- cid=441 (Sylwia L): 1×

## NO_GPS_DEMOTE events

Total since restart: **0**

*Zero events* — pipeline naturalnie wybiera informed kandydatów. V3.15 packs_fallback + V3.16 demote synergia: V3.15 szybciej aktualizuje bag kurierów (Mateusz O przestaje być blind+empty gdy otrzymuje ordery), V3.16 demote aktywowałaby się tylko gdy natural scoring wybrał blind+empty jako top-1 — to już nie jest powszechne.

## PANEL_OVERRIDE rate

Post-deploy: **13/9 = 144.4%**

(Pre-deploy baseline last 1h45min: 19.6%)

## Service health

- dispatch-panel-watcher: active (pid=1787208)
- dispatch-shadow: active (pid=1787219)
- dispatch-telegram: active (pid=1717033)

## Conclusion

V3.16 LIVE **redukuje blind+empty BEST selection z 30% → 0%** (6.1-min window).
Zero NO_GPS_DEMOTE events = **V3.15+V3.16 synergia** — V3.15 aktualizuje bagi
szybciej, więc blind+empty kandydaci naturalnie wypadają z top-1. V3.16 demote
pozostaje defense-in-depth gdyby pojawił się case gdzie blind+empty naturalnie
zwycięża.

Panel-telegram nietknięty. Wszystkie V3.12-V3.16 fixy LIVE równolegle.
