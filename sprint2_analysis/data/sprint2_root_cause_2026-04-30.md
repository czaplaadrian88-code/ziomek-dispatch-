# Sprint 2 Root Cause Analysis — 2026-04-30

## Executive Summary

- **Verdict:** _(fill from uptime + override + tak_mystery convergence)_
- **Recommended Sprint 3 priorities (top 3):**
  1. _(from override_patterns Sprint 3 priority list)_
  2. _(from override_patterns Sprint 3 priority list)_
  3. _(from override_patterns Sprint 3 priority list)_
- **Critical concerns:** _(uptime VERDICT below; if C → all override analysis is over-indexed on the small windows that DID propose)_

---

## Data Inventory

```
=== DATA INVENTORY ===
Window: 2026-04-30 11:05:00 → 2026-04-30 16:05:41 (Warsaw)
Total entries: 175

By action:
  PROPOSE: 0
  PANEL_OVERRIDE: 29
  TIMEOUT_SUPERSEDED: 65
  TG_REASON: 1
  ASSIGN_DIRECT: 0
  TIMEOUT: 0
  TIMEOUT_SKIP: 0
  TAK: 0
  NIE: 0
  INNY: 1
  OPERATOR_COMMENT: 1
  REPLY_OVERRIDE: 0
  KOORD: 0
  (every entry contains a PROPOSE decision; 'action' = OUTCOME)

Extended logging coverage (Sprint 1):
  pool_total_count present:    97 (55.4%)
  pool_feasible_count present: 97 (55.4%)
  alternatives len > 4:        2 (1.1%)
  alternatives len >= 12 (TOP_N=16 heuristic): 0 (0.0%)

TG_REASON distribution (since V3.19i deploy 2026-04-30 11:49:00):
  Bartosz: other: 1

Sprint 2 readiness: YELLOW  (PANEL_OVERRIDE n=29; thresholds GREEN>=30 / YELLOW>=15)
```

---

## TAK=0 Mystery

```
=== TAK=0 MYSTERY ===
Window: last 48h (2026-04-28 16:05:49 → 2026-04-30 16:05:49 Warsaw)
Total proposes (entries with decision): 693

Outcome distribution:
  TAK: 0
  NIE: 0
  INNY: 11
  KOORD: 0
  TG_REASON: 1
  PANEL_OVERRIDE: 287
  TIMEOUT_SUPERSEDED: 384
  ASSIGN_DIRECT: 9
  TIMEOUT_SKIP: 0
  REPLY_OVERRIDE: 0
  OPERATOR_COMMENT: 1

Telegram-click rate (TAK/NIE/INNY/KOORD/TG_REASON): 1.7%  (TAK=0)
Median time propose → panel_change: 302.9 s  (n=671)
% panel_change <30s after propose: 2.4%  (16/671)
% TIMEOUT_SUPERSEDED with assign <60s: 0.0%  (0/384)
% PROPOSE with panel-change <60s: 16.6%

DIAGNOSIS scores (relative weights):
  A) Adrian doesn't see Telegram (1 - TG-click rate): 98.3
  B) Race condition (panel <30s):                     2.4
  C) Fast assign panel-first (<60s of TIMEOUT_SUPER): 0.0
  D) Genuine ignore (TIMEOUT_SKIP share):             0.0

Verdict: TELEGRAM IGNORED IN PEAK — operator workload too high for TG approval
```

---

## Override Patterns (top 7)

```
=== OVERRIDE PATTERNS ===
Window: 2026-04-30 11:05:00 → 2026-04-30 16:06:11 Warsaw
Total entries: 176 | PANEL_OVERRIDE: 30 (17.0%)

--- P1: Per-courier override rate (min 3 proposes) ---
  Top-5 most-overridden:
    502: 48%  (10/21)
    393: 36%  (4/11)
    518: 29%  (10/34)
    470: 25%  (3/12)
    179: 25%  (1/4)
  Bottom-5 least-overridden:
    123: 15%  (2/13)
    470: 25%  (3/12)
    179: 25%  (1/4)
    518: 29%  (10/34)
    393: 36%  (4/11)

--- P2: Score gap (proposed - chosen) ---
  n=7 | median=95.07 | mean=82.23 | stdev=64.14
  range: [0.0, 166.4]

--- P3: Strategy of proposed best vs override rate ---
  bruteforce: 35%  (24/68)
  ortools: 21%  (4/19)
  ortools_rejected_v3274: 18%  (2/11)
  ?: 0%  (0/78)

--- P4: Bag size proposed vs chosen (override only) ---
  proposed median=0.0  chosen median=1

--- P5: Bundle proxy (level1/level2 presence) ---
  overrides with proposed-bundled: 0/30
  overrides with chosen-bundled:   2/30

--- P6: Restaurant override rate (top-10, min 3 proposes) ---
  Mama Thai Bistro: 50%  (5/10)
  Miejska Miska: 40%  (2/5)
  Pani Pierożek: 40%  (2/5)
  Sushi Rany Julek &amp; Pizza Majstry: 36%  (4/11)
  Sweet Fit &amp; Eat: 33%  (1/3)
  Rukola Sienkiewicza: 33%  (2/6)
  Paradiso: 33%  (1/3)
  Raj: 25%  (1/4)
  Grill Kebab: 25%  (2/8)
  Naleśniki Jak Smok: 25%  (1/4)

--- P7: Hour-of-day override rate (Warsaw) ---
  11h: 12%  (5/43)
  12h: 5%  (2/37)
  14h: 19%  (6/32)
  15h: 24%  (12/50)
  16h: 36%  (5/14)

--- Sprint 3 priority list (impact-ranked) ---
  1. P3: strategy 'bruteforce' 35% override — review TSP fallback path
  2. P1: courier 502 48% override rate (10 cases) — courier-specific scoring penalty
  3. P2: median gap 95.1 — chosen often LOWER score than proposed → operator domain knowledge missing in scoring
  4. P6: restaurant 'Mama Thai Bistro' 50% override (5 cases) — restaurant-specific bonus tweak
```

---

## Propose Flow Uptime (NEW dimension)

```
=== PROPOSE FLOW UPTIME ===
Window: 2026-04-30 06:00:00 → 2026-04-30 16:07:00 Warsaw
Total propose entries today: 107

Per-hour count today vs baseline (last 7d avg):
  07h: today=  0 | baseline_avg=  0.1 ⚠ <50% baseline
  08h: today=  0 | baseline_avg=  0.4 ⚠ <50% baseline
  09h: today=  3 | baseline_avg=  1.0
  10h: today=  6 | baseline_avg=  6.3
  11h: today= 16 | baseline_avg= 18.9 [PEAK]
  12h: today= 19 | baseline_avg= 29.3 [PEAK]
  13h: today=  0 | baseline_avg= 34.0 [PEAK] ⚠ <50% baseline
  14h: today= 26 | baseline_avg= 40.3
  15h: today= 29 | baseline_avg= 32.4
  16h: today=  8 | baseline_avg= 31.6 ⚠ <50% baseline
  17h: today=  0 | baseline_avg= 34.7 [PEAK] ⚠ <50% baseline
  18h: today=  0 | baseline_avg= 38.7 [PEAK] ⚠ <50% baseline
  19h: today=  0 | baseline_avg= 32.6 [PEAK] ⚠ <50% baseline
  20h: today=  0 | baseline_avg= 23.4 ⚠ <50% baseline
  21h: today=  0 | baseline_avg= 16.3 ⚠ <50% baseline
  22h: today=  0 | baseline_avg=  2.0 ⚠ <50% baseline
  23h: today=  0 | baseline_avg=  0.3 ⚠ <50% baseline

Stoppage windows detected (gap > 5 min): 10
  2026-04-30 09:28:31 → 2026-04-30 09:48:27: 19.9 min
  2026-04-30 09:50:52 → 2026-04-30 10:33:45: 42.9 min
  2026-04-30 10:44:12 → 2026-04-30 11:07:59: 23.8 min
  2026-04-30 11:19:02 → 2026-04-30 11:25:41: 6.6 min [PEAK]
  2026-04-30 11:27:22 → 2026-04-30 11:33:42: 6.3 min [PEAK]
  2026-04-30 11:34:33 → 2026-04-30 11:39:43: 5.2 min [PEAK]
  2026-04-30 11:39:43 → 2026-04-30 12:14:11: 34.5 min [PEAK]
  2026-04-30 12:18:48 → 2026-04-30 12:30:29: 11.7 min [PEAK]
  2026-04-30 12:32:39 → 2026-04-30 14:44:43: 132.1 min [PEAK]
  2026-04-30 14:49:40 → 2026-04-30 15:51:24: 61.7 min

Total stoppage min today: 344.7
Peak hours stoppage min: 196.4 (100.0% of peak window)
Today's uptime: 13.2%

VERDICT: C) Uptime issue (uptime <70%): focus na propose flow stability
```

---

## Sprint 3 Recommendations

1. _(highest-priority fix with effort + risk; reference Sprint 3 priority list above)_
2. _(2nd priority)_
3. _(3rd priority)_

## V3.28 Tickets to Plan

- **panel_client default OFF** — per-service env override audit (cross-ref Lekcja #47)
- **session pool architecture** — eliminate CSRF collision once-and-for-all (cross-ref feedback_panel_session_singleton)
- **propose flow monitoring** — alert if gap > 5 min between successive proposes during peak (driven by SCRIPT 4 findings today)

---

_Generated by report_builder.py from offline analysis. Live services untouched per HARD RULE C.1._
