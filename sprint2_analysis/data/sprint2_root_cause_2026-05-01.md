# Sprint 2 Root Cause Analysis — 2026-05-01

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
Window: 2026-04-30 11:05:00 → 2026-05-01 13:44:35 (Warsaw)
Total entries: 1004

By action:
  PROPOSE: 0
  PANEL_OVERRIDE: 162
  TIMEOUT_SUPERSEDED: 221
  TG_REASON: 5
  ASSIGN_DIRECT: 9
  TIMEOUT: 0
  TIMEOUT_SKIP: 0
  TAK: 0
  NIE: 0
  INNY: 5
  OPERATOR_COMMENT: 1
  REPLY_OVERRIDE: 0
  KOORD: 1
  (every entry contains a PROPOSE decision; 'action' = OUTCOME)

Extended logging coverage (Sprint 1):
  pool_total_count present:    404 (40.2%)
  pool_feasible_count present: 404 (40.2%)
  alternatives len > 4:        39 (3.9%)
  alternatives len >= 12 (TOP_N=16 heuristic): 0 (0.0%)

TG_REASON distribution (since V3.19i deploy 2026-04-30 11:49:00):
  Bartosz: other: 1
  Adrian: better_bundle: 2, other: 1, dropzone_mismatch: 1

Sprint 2 readiness: GREEN  (PANEL_OVERRIDE n=162; thresholds GREEN>=30 / YELLOW>=15)
```

---

## TAK=0 Mystery

```
=== TAK=0 MYSTERY ===
Window: last 48h (2026-04-29 13:44:36 → 2026-05-01 13:44:36 Warsaw)
Total proposes (entries with decision): 708

Outcome distribution:
  TAK: 0
  NIE: 0
  INNY: 6
  KOORD: 1
  TG_REASON: 5
  PANEL_OVERRIDE: 299
  TIMEOUT_SUPERSEDED: 385
  ASSIGN_DIRECT: 11
  TIMEOUT_SKIP: 0
  REPLY_OVERRIDE: 0
  OPERATOR_COMMENT: 1

Telegram-click rate (TAK/NIE/INNY/KOORD/TG_REASON): 1.7%  (TAK=0)
Median time propose → panel_change: 303.0 s  (n=684)
% panel_change <30s after propose: 3.4%  (23/684)
% TIMEOUT_SUPERSEDED with assign <60s: 0.0%  (0/385)
% PROPOSE with panel-change <60s: 18.4%

DIAGNOSIS scores (relative weights):
  A) Adrian doesn't see Telegram (1 - TG-click rate): 98.3
  B) Race condition (panel <30s):                     3.4
  C) Fast assign panel-first (<60s of TIMEOUT_SUPER): 0.0
  D) Genuine ignore (TIMEOUT_SKIP share):             0.0

Verdict: TELEGRAM IGNORED IN PEAK — operator workload too high for TG approval
```

---

## Override Patterns (top 7)

```
=== OVERRIDE PATTERNS ===
Window: 2026-04-30 11:05:00 → 2026-05-01 13:44:36 Warsaw
Total entries: 1007 | PANEL_OVERRIDE: 162 (16.1%)

--- P1: Per-courier override rate (min 3 proposes) ---
  Top-5 most-overridden:
    400: 54%  (15/28)
    289: 48%  (19/40)
    515: 44%  (22/50)
    502: 44%  (42/96)
    470: 38%  (15/40)
  Bottom-5 least-overridden:
    123: 15%  (2/13)
    484: 25%  (4/16)
    370: 25%  (1/4)
    179: 33%  (11/33)
    393: 37%  (7/19)

--- P2: Score gap (proposed - chosen) ---
  n=46 | median=55.09 | mean=58.53 | stdev=54.69
  range: [0.0, 267.8]

--- P3: Strategy of proposed best vs override rate ---
  sticky: 50%  (1/2)
  ortools_rejected_v3274: 45%  (42/93)
  ortools: 39%  (49/127)
  bruteforce: 38%  (70/182)
  ?: 0%  (0/603)

--- P4: Bag size proposed vs chosen (override only) ---
  proposed median=1.0  chosen median=2.0

--- P5: Bundle proxy (level1/level2 presence) ---
  overrides with proposed-bundled: 10/162
  overrides with chosen-bundled:   10/162

--- P6: Restaurant override rate (top-10, min 3 proposes) ---
  Chinatown Bistro: 57%  (4/7)
  Doner Kebab: 50%  (2/4)
  Epic Pizza: 50%  (2/4)
  Bar Eljot: 50%  (2/4)
  Zapiecek: 50%  (2/4)
  Ogniomistrz: 50%  (5/10)
  Goodboy: 50%  (4/8)
  Karczma Maciejówka: 50%  (6/12)
  Miejska Miska: 46%  (6/13)
  Mama Thai Bistro: 43%  (13/30)

--- P7: Hour-of-day override rate (Warsaw) ---
  09h: 0%  (0/12)
  10h: 0%  (0/15)
  11h: 9%  (9/95)
  12h: 13%  (14/110)
  13h: 20%  (20/101)
  14h: 19%  (6/32)
  15h: 24%  (12/50)
  16h: 18%  (19/105)
  17h: 16%  (18/113)
  18h: 17%  (15/90)
  19h: 17%  (18/106)
  20h: 17%  (18/107)
  21h: 18%  (10/57)
  22h: 21%  (3/14)

--- Sprint 3 priority list (impact-ranked) ---
  1. P2: median gap 55.1 — chosen often LOWER score than proposed → operator domain knowledge missing in scoring
  2. P1: courier 400 54% override rate (15 cases) — courier-specific scoring penalty
  3. P6: restaurant 'Chinatown Bistro' 57% override (4 cases) — restaurant-specific bonus tweak
  4. P3: strategy 'sticky' 50% override — review TSP fallback path
```

---

## Propose Flow Uptime (NEW dimension)

```
=== PROPOSE FLOW UPTIME ===
Window: 2026-05-01 06:00:00 → 2026-05-01 13:44:37 Warsaw
Total propose entries today: 88

Per-hour count today vs baseline (last 7d avg):
  08h: today=  0 | baseline_avg=  0.4 ⚠ <50% baseline
  09h: today=  3 | baseline_avg=  1.4
  10h: today=  3 | baseline_avg=  6.1 ⚠ <50% baseline
  11h: today= 19 | baseline_avg= 16.4 [PEAK]
  12h: today= 25 | baseline_avg= 28.1 [PEAK]
  13h: today= 38 | baseline_avg= 31.6 [PEAK]
  14h: today=  0 | baseline_avg= 40.3 ⚠ <50% baseline
  15h: today=  0 | baseline_avg= 32.4 ⚠ <50% baseline
  16h: today=  0 | baseline_avg= 32.4 ⚠ <50% baseline
  17h: today=  0 | baseline_avg= 35.4 [PEAK] ⚠ <50% baseline
  18h: today=  0 | baseline_avg= 36.1 [PEAK] ⚠ <50% baseline
  19h: today=  0 | baseline_avg= 33.0 [PEAK] ⚠ <50% baseline
  20h: today=  0 | baseline_avg= 25.0 ⚠ <50% baseline
  21h: today=  0 | baseline_avg= 17.0 ⚠ <50% baseline
  22h: today=  0 | baseline_avg=  2.4 ⚠ <50% baseline
  23h: today=  0 | baseline_avg=  0.3 ⚠ <50% baseline

Stoppage windows detected (gap > 5 min): 13
  2026-05-01 09:05:51 → 2026-05-01 09:19:02: 13.2 min
  2026-05-01 09:19:02 → 2026-05-01 09:52:21: 33.3 min
  2026-05-01 09:52:21 → 2026-05-01 10:29:57: 37.6 min
  2026-05-01 10:29:57 → 2026-05-01 10:48:23: 18.4 min
  2026-05-01 10:48:23 → 2026-05-01 10:53:33: 5.2 min
  2026-05-01 10:53:33 → 2026-05-01 11:06:34: 13.0 min
  2026-05-01 11:07:20 → 2026-05-01 11:16:22: 9.0 min [PEAK]
  2026-05-01 11:16:22 → 2026-05-01 11:24:49: 8.5 min [PEAK]
  2026-05-01 11:33:02 → 2026-05-01 11:54:53: 21.8 min [PEAK]
  2026-05-01 11:58:59 → 2026-05-01 12:06:36: 7.6 min [PEAK]
  2026-05-01 12:16:04 → 2026-05-01 12:23:17: 7.2 min [PEAK]
  2026-05-01 12:46:24 → 2026-05-01 12:56:17: 9.9 min [PEAK]
  2026-05-01 13:16:05 → 2026-05-01 13:21:36: 5.5 min [PEAK]

Total stoppage min today: 190.3
Peak hours stoppage min: 69.6 (38.7% of peak window)
Today's uptime: 31.4%

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
