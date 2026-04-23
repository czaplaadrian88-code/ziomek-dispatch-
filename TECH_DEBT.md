# TECH DEBT — Ziomek

## General rules (wpisane 2026-04-20)

### Flag bez konsumenta = `_PLANNED` suffix
Jeśli w `common.py` dodajesz feature flag ale consumer (kod który flagę czyta
w gałęzi decyzyjnej) nie istnieje jeszcze w prod — nazwa flagi MUSI kończyć się
na `_PLANNED`. Zapobiega footgun'om w roadmapie (flip flagi bez efektu bo brak
consumera). Przykład: `ENABLE_SPEED_TIER_LOADING_PLANNED` (2026-04-20: consumer
w `courier_resolver.build_fleet_snapshot` nie jest zaimplementowany, rename per
V3.19e pre-work).

Weryfikacja przy każdym dodawaniu flagi:
```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2
grep -rn --include=\*.py --exclude=common.py --exclude=\*.bak\* <FLAG_NAME> .
```
Jeśli grep zwraca tylko `tests/` albo pusto → dodaj `_PLANNED` suffix.

### Parse wrapper layer: log unhandled top-level keys
Parse wrappery (panel_client, gps_client, etc.) które projektują PODZBIÓR pól
z API response MUSZĄ logować unhandled top-level keys (debug level wystarczy).
Invisible data loss jest kosztowniejszy niż verbose log — precedens: Finding #1
V3.19f (`panel_client.fetch_order_details:289` zwracał `raw.get("zlecenie")` i
wywalał top-level `czas_kuriera` przez całą historię pipeline, blokując
czas_kuriera propagation do decision-making).

Wzorzec (panel_client.fetch_order_details po V3.19f):
```python
_known_top = {"zlecenie"}        # expected, handled elsewhere
_handled = {"czas_kuriera"}       # explicitly propagated
for k, v in parsed.items():
    if k in _known_top: continue
    if k in _handled: zlecenie[k] = v
    else: _log.debug(f"unhandled top-level key '{k}'")
```

### Deferred tickets

#### V3.19g — przedłużenia czas_kuriera trigger plan invalidation (deferred)
Gdy panel zmienia `czas_kuriera` po COURIER_ASSIGNED (np. coordinator "+15min"
button), courier_plans.json saved plan dla danego cid może mieć stale predicted
times. V3.19f zapisuje update przy kolejnym COURIER_ASSIGNED emit, ale plan nie
jest invalidated reactively. Full handling wymaga analizy:
- V3.19b plan_manager write hooks (invalidate_plan gdy pickup_ready zmienione?)
- V3.19d sticky sequence race conditions (re-run simulator gdy pickup_ready shift)
- Koszt implementacji 3-4h + regression risk na V3.19b/d stack.
**Priority:** low. Podnieść gdy V3.19f stable 2 tyg + metric pokazuje potrzebę.

### V3.19e + V3.19f LIVE w shadow mode flag=True (2026-04-20 20:08 UTC)
- `ENABLE_V319E_PRE_PICKUP_BAG=True` default (commit 4676b8c + tag v319ef-shadow-flip-live)
- `ENABLE_CZAS_KURIERA_PROPAGATION=True` default (same commit)
- Dispatch-shadow + panel-watcher PID post-flip: 2015775 / 2015777
- Dispatch-telegram NIE restartowany (off-air, koordynacja ręczna)
- Pierwsza real propozycja post-flip: oid=467526 @ 20:12:07, wszystkie 3 nowe
  klucze (v319e_r1_prime_hypothetical + czas_kuriera_warsaw + czas_kuriera_hhmm)
  OBECNE w serialized best. Zero errors.
- Real traffic side-by-side NIE UKOŃCZONE (low volume post-peak). Planowane
  jutro lunch peak 11-14 Warsaw per `/tmp/v319ef_v319g_jutro_handover.md`.

### V3.19g BAG cap discovery DONE (2026-04-20)
- 6-mo dataset `/root/v319g_dataset/*.csv`, 44,315 → 40,790 normalized rows, 42 couriers.
- Gold tier identified: Bartek O. / Mateusz O / Krystian / Gabriel (OPW_p90≥4).
- Raport: `/tmp/v319g_bag_cap_discovery.md` (301 linii).
- Preview: `/tmp/v319g_courier_tiers_preview.json` (37 eligible).
- **Design + impl PENDING** — jutrzejsza sesja (po side-by-side V3.19e/f).

### Outstanding tickets post-dzień-dzisiejszy
- **APK GPS** (MEDIUM, user: "na razie działa, nie ruszamy"). AndroidManifest ma
  defensive fixes, 4/8 kurierów działa; 4/8 bez GPS. Deferred — nie blokuje V3.19e/f.
- **Silent flags** — 1 renamed do `_PLANNED` (2026-04-20), pozostałe 3 OK
  (`ENABLE_TRANSPARENCY_SCORING`, `ENABLE_BUNDLE_VALUE_SCORING`, `ENABLE_PANEL_IS_FREE_AUTHORITATIVE`).
- **639 delivered bez delivery_coords** (30% historical). Fix: geocoding retry
  w state_machine + backfill script. Priority: low.
- **46 delivered bez delivered_at** — data integrity, fallback to updated_at
  na readerach. Priority: low.
- **V3.21 wave_scoring flip** — blocked na V3.19e/f production stable + BAG cap tiering.

### V3.19h 3 flags LIVE (2026-04-20 23:53 shadow → 2026-04-21 flip)

**Status update 2026-04-21:** 3 flags (BUG-1/2/4) flipped to True default
(commit 08de9fa). Live od 2026-04-20 22:30 UTC.

**Audit completed 2026-04-21** (replay-based on 6-mo CSV, 44k orders):
- **Stage 1** (~19:20 UTC): name resolution fix + feasibility gate fix
  (/tmp/v319h_audit/*.bak-pre-s1). Match top-1=4.79%, top-5=18.96%.
- **Stage 2 EXTREME** (~20:30 UTC per Adrian ACK): R4 bundle + R1/R5/R8
  adaptive + V3.19f pickup ladder. TSP/V3.19e SKIP (świadoma decyzja —
  TSP w audit historycznym = artificial scenario, prod V3.19d plans nie
  są w dataset).
- Dashboard: `/tmp/v319h_audit/dashboard.html` (exploratory Q&A tool)
- Exec summary: `/tmp/v319h_audit/EXEC_SUMMARY.md`
- **Decyzja produkcyjna:** HOLD V3.19h live. Replay fidelity bias
  (cold-start bag=0 candidates dominate bez pełnej TSP integracji)
  uniemożliwia produkcyjny go/no-go signal z tego audytu. Kolejny
  audit z live `shadow_decisions.jsonl` danymi sugerowany w 2 tyg.

### V3.19h shadow deploy (historical)

3 MVP implementations w shadow mode z `dispatch-shadow` restart
(panel-watcher nietknięty od 2026-04-20 17:17, dispatch-telegram
nietknięty od 2026-04-19 16:19).

| Bug | Commit | Tag | Flag default | Tests |
|---|---|---|---|---|
| BUG-4 tier×pora cap matrix | 4d1b609 | v319h-bug4-tier-cap-matrix-impl | ENABLE_V319H_BUG4_TIER_CAP_MATRIX=False | 49 (30+19) |
| BUG-1 SR × drop_proximity_factor | 5fe81fe | v319h-bug1-drop-proximity-impl | ENABLE_V319H_BUG1_DROP_PROXIMITY_FACTOR=False | 50 (32+18) |
| BUG-2 wave continuation bonus | a65bfb3 | v319h-bug2-wave-continuation-impl | ENABLE_V319H_BUG2_WAVE_CONTINUATION=False | 23 |

**Shadow deploy tag:** v319h-3bugs-shadow-deploy (smoke test green 2026-04-20 23:58 UTC).

**Zero behavior change przy deploy** — wszystkie 3 flagi False default.
Flip planowany na jutrzejszy lunch peak side-by-side 11-14 Warsaw 2026-04-21.

**7 nowych pól serializowanych:**
- BUG-4: `v319h_bug4_tier_cap_used`, `v319h_bug4_cap_violation`, `bonus_bug4_cap_soft`
- BUG-1: `v319h_bug1_drop_proximity_factor`, `v319h_bug1_sr_bundle_adjusted`
- BUG-2: `v319h_bug2_interleave_gap_min`, `v319h_bug2_continuation_bonus`

**Generated artifacts:**
- `dispatch_state/courier_tiers.json` (43 couriers, Gabriel cap_override per ACK)
- `dispatch_v2/districts_data.py` (28 osiedli Białegostoku + 4 outside-city)
- `dispatch_v2/build_v319h_courier_tiers.py` (one-off tier regenerator)

**Regression baseline:** 644 asserts PASS w 39 plikach (522 pre-V3.19h + 122 new).

### Session closures 2026-04-21

- **Albert Dec mapping:** PIN 8770 → cid=414 (kurier_piny.json updated,
  confirmed w shadow dispatcher SHADOW PROPOSE best=414 multiple events
  14:41-17:52 UTC). Courier-api auth logs empty 12h (APK possibly
  offline, not blocking).
- **Parser free-text disabled:** `ENABLE_TELEGRAM_FREETEXT_ASSIGN=0`
  default (commit 82b96f7). OPERATOR_COMMENT logging code present
  (`telegram_approver.py` × 5 occurrences). 0 entries w
  `learning_log.jsonl` since flip — Bartek nie pisał free-text w 12h,
  parser fix NOT_TESTED w realnych warunkach (brak event, nie fail).
- **V3.19g1 hotfix:** live (commit 16cf921 — removed local import of
  normalize_order in _diff_and_emit, unblocks shadow log).
- **Lekcje sesja:**
  - Python local import shadow globals (feedback_python_local_import_shadow.md)
  - CC overnight audit pivot do reduced-fidelity acceptable z honest caveats
  - CSV-based replay dla 6-mo ≠ production-grade audit (brak
    live shadow_decisions, TSP plans, courier_plans.json snapshots)

## V3.25 Sprint — 4 CRITICAL (23.04.2026, ~7h)

Z Q&A session 22.04. Pełen plik reguł (gdy Adrian upload):
`/tmp/v324_qa_rules_extracted_2026-04-22.md`.

### R-01 SCHEDULE-HARDENING (2h) — CRITICAL

V3.24-A niedeterminizm: cid bez mapping pass-through, dropoff >
shift_end+5min soft, pickup post-shift czasem przechodzi.

**Fix:** unconditional PRE-CHECK w `feasibility_v2.py`:
- cid not in kurier_ids.json → HARD REJECT
- No active shift → HARD REJECT
- Pickup < shift_start - 30min → HARD REJECT (PRE_SHIFT_BEYOND_TOLERANCE)
- Dropoff > shift_end + 5min → HARD REJECT (DROPOFF_POST_SHIFT)
- Pickup > shift_end → HARD REJECT (PICKUP_POST_SHIFT)

**Flag:** `ENABLE_V325_SCHEDULE_HARDENING=False` → shadow 30min → flip.

**Rollback:** flag False + restart dispatch-shadow.

### R-02 COURIER-SYNC + DISTRICTS-SCRAPE (2.5h) — CRITICAL

**Courier sync (3 nowi):**
- cid=522 = **Szymon Sadowski** (potwierdzony Q&A — NIE Grzegorz Rogowski
  jak CC Faza A błędnie zmapował, lesson QA-11)
- Kuba Olchowik (cid TBD — panel scrape)
- Grzegorz Rogowski (cid TBD — panel scrape)

**Tier changes:**
- Kuba OL (370) → Standard+ (z Standard)
- Krystian (61) → inactive=True (permanent OFF)

**Districts:** scrape http://www.info.bialystok.pl/osiedla/N/obiekt.php
N=1..28, diff z `districts_data.py`, update jeśli diff.

**Files affected:** kurier_ids.json, kurier_piny.json, courier_tiers.json,
schedule_utils.PANEL_TO_SCHEDULE, districts_data.py

**Rollback:** git revert + restart dispatch-shadow.

### R-03 TELEGRAM-OPS-PARSER (2h) — CRITICAL

**New file:** `telegram_ops_parser.py` + `/etc/systemd/system/dispatch-telegram-ops.{service,timer}`
(1 min tick).

**Komendy na grupie -5149910559:**
- `/zwolnij <cid>` — permanent exclude (manual_overrides_excluded.json)
- `/zostaje <cid> <hh:mm>` — dynamic shift extension (manual_overrides_extended.json)
- `/wraca <cid>` — zdjęcie blacklist/pauzy
- `/pauza <cid> <min>` — temporary pause (manual_overrides_paused.json)

**Auth:** only AUTHORIZED_OPS = [Adrian_telegram_id, Bartek_telegram_id]

**Integration:** `feasibility_v2.py` reads 3 override files PRE schedule check.

**Albert Dec migration:** wywal `COURIER_414_BLACKLIST_UNTIL` z quick patch,
zastąp wpisem w manual_overrides_excluded.json.

**Rollback:** `systemctl disable --now dispatch-telegram-ops.timer` +
git revert.

### R-04 NEW-COURIER-CAP gradient (0.5h) — CRITICAL

**Fix:** gradient penalty w `scoring.py` post-base-score:
- tier != "new" → 0
- bag_size >= 2 → -9999 (HARD SKIP)
- advantage >= 50 → -10
- advantage 20-50 → -30
- advantage < 20 → -50

**Flag:** `ENABLE_V325_NEW_COURIER_CAP=False` → shadow → flip.

**Rollback:** flag False.

---

## V3.26 Backlog — 7 HIGH (28-31.04, ~28h)

- R-05 SPEED-MULTIPLIER (6-10h, backtest 40k dataset)
- R-06 MULTI-STOP-TRAJECTORY (4-6h)
- R-07 PICKUP-COLLISION-CHECK (3-4h)
- R-08 PICKUP-EXTENSION-NEGOTIATION (5-6h, + Adrian tolerance table)
- R-09 WAVE-CONTINUATION-GEOMETRIC-VETO (2h)
- R-10 FLEET-LOAD-BALANCING (3h)
- R-11 TRANSPARENCY-DECISION-RATIONALE (4h)

## V3.27+ Backlog — 7 MEDIUM (maj)

R-12 restaurant-holding-detection, R-13 dedicated-courier,
R-14 natural-wave-continuation, R-15 match-source-attribution,
R-16 recent-delivery-decrement, R-17 tier-dynamic-assignment,
R-18 districts-complete-sync

## LOW Backlog (po Q4)

R-19 late-evening-simple-mode, R-20 post-wave-pos-downgrade,
R-21 extended-shift-awareness

---

## Success metrics V3.25 → V3.26 → V3.27

- Baseline post V3.19h: PANEL_OVERRIDE 81%
- **Post V3.25 cel:** <60% (4/10 Q&A cases resolved)
- **Post V3.26 cel:** <16% (8/10 Q&A cases resolved)
- **Post V3.27 cel:** <10% + wysoki trust

---

## 2026-04-22 — V3.19h live data analysis (C2/C3 validation)

Post-peak validation sesja. 26h live data (21.04 08:55 → 22.04 15:01 UTC).
Dane źródłowe: `scripts/logs/shadow_decisions.jsonl` (N=272 post-flip PROPOSE
effective) + `dispatch_state/learning_log.jsonl` (N=446 entries, 262
semi-strict outcomes). Methodology semi-strict (TIMEOUT_SUPERSEDED rozwiązany
przez orders_state proposed-vs-actual). Raporty:
- `/tmp/v319h_c2_clean_rates_2026-04-22.md` (clean rates + per-bug isolation)
- `/tmp/v319h_c3_quick_findings_2026-04-22.md` (over-promote + neg score + BUG-4 sub)

### ✅ V3.19h LIVE 21-22.04 → NIE rollback

Override rate post-flip **81.30%** (213/262) vs baseline-mixed (14-20.04)
**89.19%** (883/990). **+8pp improvement**, nie regresja.

Absolute 81% > target <25% jest **strukturalne** — workflow coordinator
bypassuje Telegram (TAK explicit=0, ASSIGN_DIRECT=2, w >95% cases silent
panel assign przed SLA timeout). Target <25% nieosiągalny via V3.19h alone;
wymaga osobnej inicjatywy (operator UX tool albo re-definicja metryki).

**Decyzja:** V3.19h flags stay True (BUG-1/2/4 default=True). Sample
n=259 effective. Zero modyfikacji produkcji z C2/C3 wniosków.

### 🟡 V3.19j-BUG2-MAGNITUDE — PRIORITY #1 (confirmatory signal)

C2 per-bug isolation: **BUG-2 fired (N=197) override rate 82.7% vs not_fired
(N=65) 76.9% → Δ +5.8pp**. Binary +30 bonus za szeroko rozdany — gradient
tabela per Adrian Q&A 22.04 (już w spec wyżej w tym pliku).

**Działania (bez zmian z poprzedniej definicji ticketu):**
- Implementacja `bug2_wave_continuation_bonus(gap_min)` gradient table
- Audit re-run z nowym bonus, expected BUG-2 fires drop 13% → 5-8%
- Top R4 klastry score breakdown rebalanced
- **WALIDACJA Z BARTKIEM przed implementation**

**Est:** 4-6h. **Blocking:** brak. **Status:** top priority post-V3.24.

### 🟡 V3.19j-BUG4-MAGNITUDE — NEW MEDIUM

C3-Q3 sub-isolation schema correction (cap_violation = **int** 0/1/2, nie bool):
**cap_violation > 0 (N=20) override rate 90.0% vs cap_violation == 0 (N=228)
83.3% → Δ +6.7pp**. V3.19h **correctly identifies overload** ale
`bonus_bug4_cap_soft` penalty magnitude niewystarczający — kurier z violation
dalej wygrywa scoring.

Tier×pora distribution (shadow, N=247 non-cold):
- `std/peak/4`: 107 (43%)
- `std/normal/3`: 85 (34%)
- `std+/peak/5` + `std+/normal/4`: 31 (13%)
- `gold/*`: 10 (4%) ← tylko 2 `gold/peak/6`
- `std/peak/3` + `std/off_peak/2`: 16

**Propozycja:** gradient penalty based on cap_violation count:
- violation=1: `-30` pkt (obecny range ~<-20)
- violation=2: `-50` pkt
- violation≥3: `-80` pkt (hard signal)

**Est:** 3-4h (function change w common + tests + audit re-run).
**Blocking:** brak; sekwencyjnie po V3.19j-BUG2-MAGNITUDE.

### 🟡 V3.19k-SCORE-FLOOR — NEW MEDIUM

C3-Q2 finding: **80/274 = 29.2% propozycji post-flip z score < 0** (threshold
acceptable noise = 5%). Top 5 worst scores:

| # | oid | score | proposed | actual | pos_source |
|---|---|---|---|---|---|
| 1 | 467795 | -446.46 | 515 Szymon P | 414 | **pre_shift** |
| 2 | 467747 | -411.70 | 414 Albert Dec | 393 | last_assigned_pickup |
| 3 | 467725 | -311.48 | 470 Piotr Zaw | 370 | last_assigned_pickup |
| 4 | 467724 | -302.78 | 470 Piotr Zaw | 470 (match) | last_assigned_pickup |
| 5 | 467539 | -292.35 | 457 Adrian Cit | 457 (match) | last_picked_up_delivery |

Case #1 `pos_source=pre_shift + score -446` duplikuje V3.24-SCHEDULE
uzasadnienie. Cases #4/#5 match actual==proposed mimo score -300 → coordinator
musiał zaakceptować (solo viable albo no alt).

**Propozycja:** hard floor `score < -150` trigger KOORD albo dodatkowy warning
line w Telegram. Precedent: V3.16 `_demote_blind_empty` inline post-scoring layer.

**Decision pending:** 7-dniowy backtest historical shadow_decisions na
expected behavior change przed hard block commit.

**Est:** 2-3h backtest + 2-3h implementation. **Blocking:** brak.

### 🟡 V3.19l-TIER-PROMOTE-INVESTIGATION — NEW LOW

C3-Q1 finding: top 10 proposed couriers per-oid dedup (N=274):

| cid | name | n_prop | % all | match_rate |
|---|---|---|---|---|
| 414 | Albert Dec | 55 | 20.1% | 18.2% |
| 470 | Piotr Zaw | 36 | 13.1% | 27.8% |
| 400 | Adrian R | 35 | 12.8% | 20.0% |
| 514 | Tomasz Ch | 31 | 11.3% | 19.4% |
| 393 | Michał K. | 23 | 8.4% | 30.4% |

Top 5 = **65.7%** wszystkich propozycji. **Zero Goldów w top 5** (Bartek O.
cid=123, Mateusz O cid=413, Krystian, Gabriel). Mateusz O #10 z 3.6%
udziałem. Match rates top 5: 18-30% — żaden top courier >30% match.

**Hipoteza:** scoring underweight Gold tier albo BUG-4 tier×pora cap
matrix za silnie ogranicza Goldów (std/peak/4 vs gold/peak/6 — delta cap=2 ale
bonus_bug4_cap_soft pref dla std). Analogicznie feasibility może pref
informed-pos candidates (last_picked_up_delivery vs gold z post_wave).

**Zakres (discovery):** 
- Per-tier match_rate audit w window post-flip
- BUG-4 cap_used distribution per tier
- Score distribution per tier (raw + penalty)

**Est:** 2-3h discovery. **Blocking:** brak. **NIE blokuje V3.24.**

### 🔴 V3.24-SCHEDULE-INTEGRATION — PRIORITY #1 BLOCKING

Podwójne uzasadnienie z C3:
- **Q1:** Albert Dec 414 = **20.1%** wszystkich propozycji (55/274), match 18.2%
- **Q2 case #1:** oid=467795 score=-446 pos_source=**pre_shift** (kurier
  przed zmianą, scoring syntetyczny cold-start bez walidacji grafiku)

Existing ticket wyżej w tym pliku (sekcja "V3.24-SCHEDULE") pokrywa problem.
Est 1.5-2 dni. **Start jutro.**

**UWAGA operacyjna:** po deploy V3.24 zdjąć Albert blacklist z
`manual_overrides.json` w tym samym kroku. Backup już istnieje:
`manual_overrides.json.bak-pre-albert-2026-04-22`.

---

## 2026-04-22 — session closure (audit V3.19h Q&A + live peak)

> **Ground truth dla wszystkich poniższych ticketów:**
> `/root/.openclaw/workspace/docs/REGULY_BIZNESOWE_2026-04-22.md`
>
> Formalne reguły biznesowe Ziomka (HARD + SOFT gradient + hierarchia
> priorytetów). Każdy V3.19j/V3.24+ ticket MUSI je respektować. Zmiana
> scoringu/feasibility bez zgodności z regułami = rework.
>
> **Pełen session handover (feature flags, git tags, audit metrics,
> Telegram log, open items):**
> `/root/.openclaw/workspace/docs/SESSION_CLOSE_2026-04-22.md`
>
> Read BEFORE touching any ticket — zawiera context co było zrobione
> kiedy + dlaczego oraz prerequisites dla next session (post-peak
> cleanup checklist + Bartek validation pending).

### V3.24-SCHEDULE — Schedule Integration (PILNY, HIGH priority)

**Problem (discovered 22.04 10:59):**
Ziomek proponuje kurierów poza ich godzinami pracy. Case live #467723 —
Albert Dec (K414) zaproponowany jako feasible kandydat o 10:59 mimo że
Albert pracuje od 12:00.

**Root cause:**
`courier_resolver.dispatchable_fleet` MA schedule check (uses
`schedule_today.json` + `PRE_SHIFT_WINDOW_MIN=50`), ale window 50min
to za szeroko. Albert przy shift_start=12:00 jest pre_shift-allowed
już od 11:10. Shadow @ 11:53 Warsaw: `PROPOSE best=414` = legit per
code ale niepożądane z Adrian perspective. Scoring/feasibility nie
re-sprawdza grafiku przed inclusion — polega tylko na fleet roster.

**Akcje:**
1. **Quick patch (deployed 22.04 ~13:00 UTC):** `manual_overrides.json`
   excluded list += "Albert Dec". `dispatchable_fleet:550-551` hard
   skip. Zero restart (manual_overrides.get_excluded re-loads per call).
   Backup: `manual_overrides.json.bak-pre-albert-2026-04-22`. Remove
   after 12:00 Warsaw (manual or Adrian via Telegram bot command).
2. **Properly V3.24:** Shorten `PRE_SHIFT_WINDOW_MIN` default → 15-20 min,
   OR make per-courier configurable. Sheets fetch już jest
   (schedule gid 533254920 w Spreadsheet `1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8`,
   load 06:00 i 08:00). Integracja feasibility: kurier feasible tylko
   w aktualnej zmianie (hard gate), gradient tolerance dla
   pre_shift <15 min z penalty.
3. **Cold-start tolerance refactor:** kurier 0-15 min do start =
   kandydat z -5 penalty; 15-30 min = z -15 penalty; >30 min = skip.

**Estimated effort:** 1.5-2 dni (window tuning + per-courier config +
feasibility integration + tests).

**Blocking:** brak — niezależny od V3.19j.

---

### V3.19j-BUG2-MAGNITUDE — BUG-2 magnitude tuning (HIGH priority)

**Problem (discovered 22.04 Q&A audytu):**
`common.bug2_wave_continuation_bonus(gap_min)` daje +30 binary dla
każdego `gap<0`, niezależnie od magnitude. Ekstremalny overlap
(gap=-44min, kurier dowozi przez 44 min po pickup ready) dostaje ten
sam bonus co mały overlap (gap=-7min, realistic interleave).

**Adrian rule (z Q&A):**
- gap 0 do -5min = ideal (pełen +30)
- gap -5 do -15min = bardzo dobry (+25)
- gap -15 do -30min = OK (+15)
- gap -30 do -45min = możliwe ale słabsze (+5)
- gap -45 do -60min = unikamy (-10)
- gap < -60min = bad (-30)

**UWAGA:** gradient, nie threshold. Próg NIE eliminuje kandydata —
tylko zmniejsza/odwraca bonus. Adrian: "im mniejszy waste tym lepszy,
ALE może być nawet 40 min jeśli najlepszy kandydat".

**Implementacja:**
```python
def bug2_wave_continuation_bonus(gap_min: float) -> float:
    if gap_min >= 0:
        return 0.0  # waste, nie anticipation
    abs_gap = abs(gap_min)
    if abs_gap <= 5:   return 30.0
    elif abs_gap <= 15: return 25.0
    elif abs_gap <= 30: return 15.0
    elif abs_gap <= 45: return 5.0
    elif abs_gap <= 60: return -10.0
    else:               return -30.0
```

**Validation:** re-run audit z nowym bonus, expect:
- BUG-2 fires drop from 13% (v5 post-feasibility-fix) → ~5-8%
- Top R4 klastry score breakdown rebalanced (extreme overlap kandydaci
  spadają w ranking)
- Match top-1 boost +1-2pp expected

**Estimated effort:** 4-6h (function change + tests + audit re-run + validation).

---

### V3.19j-DISTANCE-WEIGHT — Reweight road→restaurant penalty (MEDIUM priority)

**Problem (discovered 22.04 Q&A case #423809):**
W decyzjach gdzie 2+ kandydatów ma akceptowalny BUG-2 overlap
(`|gap|<15min`), Ziomek systematically chooses far candidate z marginal
timing improvement nad close candidate z adequate timing.

**Example:** Adrian Ba (1.96km, gap=-8min) TOTAL=148.64. Mateusz Bro
(5.16km, gap=-4min) TOTAL=209.59. Mateusz wygrał głównie przez
timing_gap +25 vs +15 (10pkt różnicy), ale road 5.16km vs 1.96km
nie miało wystarczającej penalty.

**Adrian rule (priorytet decyzyjny):**
1. **Najpierw:** kurier nie może DUŻO przedłużać czasu dla restauracji
   (BUG-2 magnitude)
2. **Potem:** bliskość do restauracji (road→restaurant)
3. **Potem:** R4 corridor (drop "po drodze")

**Implementacja:** nonlinear road_to_restaurant_penalty:
- 0-1km: 0
- 1-2km: -2 pkt/km
- 2-4km: -5 pkt/km
- 4-6km: -10 pkt/km
- 6+ km: -15 pkt/km

Apply jako tie-breaker po BUG-2 magnitude check.

**Validation:** re-run audit, expect decisions w "all-OK timing" zone
shift to closer candidates.

**Estimated effort:** 3-4h.

---

### V3.19i — Operator interface refactor (MEDIUM priority, deferred)

**Problem:** Ziomek ma 3 interfejsy odpowiedzi: zielony (zatwierdź) /
INNY / KOORD. Free-text "jakub ol ma po drodze" → "❓ Nie rozumiem."
Operator komentarze nie są przyswajalne podczas live peak.

**Akcje:**
1. Reaction handler 👍/👎 (message_reaction allowed_updates).
2. Re-design parsera: `/assign K414`, `/koord`, `/swap K414 K207`,
   `/skip`, `/stop`, `/koment <text>` komendy.
3. Multi-operator support (Adrian + Bartek concurrent).
4. **Dodano 22.04:** Pre-canned reasons — przy klik NIE/KOORD pojawia
   się dropdown ("za daleko" / "extreme overlap" / "kurier nie pracuje"
   / "inny lepszy").

**Estimated effort:** 1-2 dni.

---

### V3.23 — Czasówki proposal mode (HIGH priority, spec ready)

Spec gotowy w `/mnt/user-data/outputs/V3.23_CZASOWKI_SPEC.md` (485 L) —
wymaga deploy do `/root/.openclaw/workspace/docs/V3.23_CZASOWKI_SPEC_2026-04-21.md`
+ git tag `v323-spec-v1`.

Implementation **blocked na V3.24** (Schedule Integration) — bez
grafiku Ziomek nie wie kto jest dostępny dla czasówki.

---

### Dashboard v5.1 bugs (LOW priority, audit-only, zamknięte 22.04)

Discovered w Q&A audytu 22.04, **wszystkie naprawione w dashboard v5.1**:

- **Z2-A ACTUAL dup w alternatives** — dashboard mkCandCard dodaje
  "SAME PERSON as Alt #X" w ACTUAL panel + "SAME PERSON as ACTUAL
  panel above" w alt card gdy ⭐.
- **Z2-B Outcome threshold mismatch** — thresholds per spec sekcja 5.2:
  GOOD ≤5, OK 5-15, BAD 15-30, CRITICAL >30 OR cancelled. Było
  GOOD≤20 (my optimistic interpretation for urban travel). Re-classify
  + 43,397 counterfactual est_outcome labels auto-updated.
- **Z2-C Scoring TOTAL display mismatch** — dashboard ukrywał
  `r9_stopover`, `r9_wait_pen`, `R1/R5/R8 soft`, `base_total`
  breakdown, `bonus_l2`. Manual trace #424327: TOTAL math CORRECT w
  data; tylko display incomplete. Fix: `mkCandCard` teraz renderuje
  WSZYSTKIE non-zero components.

Zero prod impact — tylko `/tmp/v319h_audit/` dashboard rendering.

---

### Albert Dec assignment (DONE 21-22.04)

**Status:** ✅ deployed.

- PIN 8770 w kurier_piny.json + kurier_ids.json (commit
  `courier-albert-dec-pin-deployed-21apr`).
- Tier "std" w courier_tiers.json (added 22.04 ~09:00 UTC,
  cap_override peak=3 conservative for new courier).
- GPS opcjonalne (cold_start pos jeśli brak).
- Live verified 22.04 11:53 Warsaw: K414 pojawił się w shadow
  propozycji (best=414).

**Open issue:** schedule respect — Albert proposed pomimo godzin pracy
12:00+. Quick patch blacklist via `manual_overrides.excluded`
(deployed 22.04 ~13:00 UTC). Properly w **V3.24-SCHEDULE**.

---

### Lekcje techniczne dodane 22.04

**Lekcja #10 — Adrian rule changes mid-Q&A.** W Q&A audytu Adrian
zmienił interpretację swojej własnej reguły 3 razy w 30 min
(Mateusz/Marek/Adrian Ba po kolei preferowany). **Reguła:** Q&A na
complex business cases nie da spójnego signal w 1 sesji. Wymaga 2-3
iteracji (Adrian + Bartek razem) zanim reguła się stabilizuje. Active
learning loop NIE jest one-shot — ongoing process miesięcy.

**Lekcja #11 — Replay reconstruction has fundamental limits.** Roster
bias (3-day → ±3h fix), gap interpretation (BUG-2 binary signal),
missing scoring components (dashboard render bug) — żaden nie jest
"fundamental bug Ziomka", wszystko **artefakty replay
reconstruction**. **Reguła:** backtest ≠ production validation. Audit
jako research tool dla pattern discovery. Verdict produkcyjny =
live data only.

**Lekcja #12 — Adrian's domain knowledge > statistical inference.**
Audyt v5 sugerował "BUG-2 dinner_peak Grill Kebab/Rany Julek to top
kontrowersyjne klastry." Adrian w 30 sekund: "Albert pracuje od 12,
to bug." CC nie miał tego signal. **Adrian operational knowledge >>
historical analysis.** **Reguła:** live operational decisions Adriana
> każdy backtest verdict. Ziomek active learning = Adrian (+ Bartek)
decisions in production, nie historical Q&A.

### V3.19h deferred tickets

- **BUG-3 directional efficiency** — NOT_CONFIRMED z haversine proxy. Re-verify
  za ~2 tygodnie z real GPS tracks (OSRM route replay per wave).
- **4 kurierów 0% GPS** (Kacper Sa 502, Adrian Cit 457, Szymon P 515, Gabriel Je 517)
  — MEDIUM priority, właściciel "działa na razie". Deep-dive APK session later.
- **639 delivered bez delivery_coords** — 30% backfill target. Low priority.
- **V3.19g przedłużenia czas_kuriera invalidation** — blocked na V3.19h stable.
- **V3.21 wave_scoring flip** — blocked na V3.19h production stable + real GPS.
- **Panel-watcher SIGKILL fix** — timeout `TimeoutStopSec=120s` zastosowany
  (ba8792e), waiting natural restart aby apply (panel-watcher uptime 3h+
  od 2026-04-20 20:08:54, celowo zachowany clean).

### V3.19h bonus stack boundary monitoring (2026-04-21)
Max positive bonus stack realistic scenario po V3.19h impl:
- bonus_l1 (L1 same-rest) = 25 (max przy BUG-1 factor=1.0)
- bonus_l2 (L2 nearby pickup) = 20 max
- bonus_bug2_continuation (BUG-2) = 30 max
- timing_gap_bonus = 25 max
- **Total = 100 — boundary OK na dziś.**

R4 standalone = 150 (Bartek Gold weight 1.5 × raw 100 max) — pre-existing,
nie w V3.19h scope. Może dominować scoring gdy bundle_level3 TIER_A.

**Monitoring:** przy kolejnych dodatkach bonus (BUG-3 directional, V3.21
wave_scoring features, V3.22 BUNDLE_VALUE_SCORING) revisit cap. Może
trzeba:
- Podnieść cap do 150 (+50 headroom)
- Wprowadzić scaling / capping mechanizm (np. max positive sum = const)

Monitor post-flip: grep realnych score distributions w shadow_decisions.jsonl
co tydzień. Gdy median > 80 albo p99 > 150 → signal rosnącego bonus bloat.

### V3.19ef systemd timeout fix LIVE (2026-04-20)
Precedens: V3.19e restart 2026-04-20 17:17 UTC → panel-watcher SIGKILL bo
default TimeoutStopSec=15s za krótki (fetch_order_details HTTP timeouts +
cookie jar cleanup wymagają dłużej przy graceful SIGTERM).

Fix (daemon-reload only, zero service restart):
- `/etc/systemd/system/dispatch-panel-watcher.service`: TimeoutStopSec=15 → 120s.
- `/etc/systemd/system/dispatch-shadow.service`: explicit TimeoutStopSec=60s
  (było default 90s; graceful SIGTERM handler shadow loop ze sleep 5s wystarczy
  mniej niż default).
- Backup: `/etc/systemd/system/dispatch-*.service.bak-pre-v319ef-timeout`.
- Nowe timeouty zadziałają przy następnym naturalnym restarcie.

## 2026-04-20 — pre-peak sesja

### P0 — GPS BACKGROUND TRACKING BROKEN (priorytet najwyższy)
- **Problem:** Courier APK (pl.nadajesz.courier) przestaje wysyłać GPS **natychmiast po zminimalizowaniu aplikacji** na wszystkich telefonach, od początku istnienia aplikacji
- **Wpływ biznesowy:**
  - Bartek Gold Standard (R1 8km p90) kalibrowany na stale positions
  - Cała hierarchia pos_source oparta na starych punktach (>60 min)
  - Kurierzy muszą trzymać apkę w foreground → UX problem, rozładowuje baterię, rozpraszanie
  - **V3.21 wave_scoring flip ZABLOKOWANY** do czasu fix'a (wave scoring mocno zależy od real-time GPS)
- **Prawdopodobne root causes (do weryfikacji post-peak):**
  - Brak foregroundServiceType="location" w AndroidManifest (Android 14 requirement)
  - FGS notification nie ustawiony jako ongoing() → Android kills po onStop()
  - Brak REQUEST_IGNORE_BATTERY_OPTIMIZATIONS dialog / whitelisting w Doze mode
  - Upload coroutine uwiązana do activity lifecycle zamiast FGS scope
  - WakeLock nie acquired podczas GPS polling
  - Room write skipping gdy process zabity przez Android
- **Fix:** sesja deep-dive + build APK + test na urządzeniu, **PO peakiem 20.04.2026 (16:00+)** lub w innym nie-peak oknie
- **Workaround dzisiaj:** kurierzy trzymają apkę otwartą w foreground (nie ideał, ale działa)
- **Referencja kodu:** /root/courier-app/ (Kotlin+Compose), package pl.nadajesz.courier, backend :8767

### P1 — 70 zombie orders w orders_state.json
- Wynik 11 restartów panel-watcher + 2× SIGKILL wczoraj podczas V3.19 deploy (17:37, 20:17 UTC)
- Stuby status=planned z history=[NEW_ORDER only], brak courier_id/assigned_at/picked_up_at/delivered_at
- Range oid: 466976-467159, first_seen 2026-04-19 08:31-14:25 UTC
- 0/70 w courier_plans.json stops (cross-ref OK)
- **Obecnie SAFE:** guard ENABLE_PENDING_QUEUE_VIEW=False (common.py:282) blokuje ich przed dispatch_pipeline
- **Stają się GROŹNE przy:**
  - V3.21 flip (C5 wave_scoring) — jeśli będzie wire-up z pending_queue
  - V3.22 flip (C7 pending_queue) — bezpośrednio otwiera gate
- Backup state: /tmp/state_backup_pre_cleanup_20260420_081544/
- **Fix (przed C5/C7 flip):**
  1. Hard filter w state_machine.get_by_status: exclude not courier_id and first_seen < now - STALE_TTL (6h)
  2. One-shot soft-mark script: status=expired + event STALE_CLEANUP dla 70 zombie (audit trail)
  3. (Opcjonalnie) reconcile fetch z panelu dla potwierdzenia (404/status=7/8/9)

### P2 — Strukturalny fix: reconcile-on-startup w panel_watcher
- Bez tego KAŻDY restart panel-watchera może produkować zombie (precedens: 70 w 1 dzień)
- Dodać do panel_watcher startup hook:
  - Find orders status=planned + history<=1 + first_seen > 6h
  - Fetch panel dla każdego oid
  - Update status jeśli panel potwierdza delivered/cancelled
  - Mark expired jeśli 404 w panelu
- Zapobiega akumulacji długu między deployami
- **Fix razem z P1** przed C5/C7 flip

### P3 — COD Weekly: auto-tworzenie bloku payday
- Obecnie co poniedziałek 08:00 UTC job failuje gdy brak kolumny z payday=+3 dni w row 1 arkusza
- Workaround: Adrian ręcznie dopisuje datę → 5 min/tydzień + ryzyko zapomnienia (restauracje nie dostaną wypłat)
- Telegram alert działa OK: "Target column fail: Brak bloku z payday=X. Dodaj ręcznie w arkuszu datę wypłaty"
- **Fix:** w /root/.openclaw/workspace/scripts/dispatch_v2/cod_weekly/run_weekly.py dodać auto-append bloku kolumn dla target payday jeśli nie istnieje
- Estymacja: 30 min + test dry-run

### P4 — CLAUDE.md + project memory: update procedury gateway restart
- Obecnie w CLAUDE.md: "docker compose restart openclaw-gateway" (niepełne, nie działa z CWD poza /root/openclaw)
- Poprawnie: "cd /root/openclaw && docker compose restart openclaw-gateway" LUB "docker restart openclaw-openclaw-gateway-1"
- Container name: double-prefix (project=openclaw, service=openclaw-gateway) -> name=openclaw-openclaw-gateway-1
- Compose file: /root/openclaw/docker-compose.yml
- **Fix:** edit CLAUDE.md + /root/.openclaw/memory/project_f22_v319_v320_complete.md

### P5 — Gateway memory leak weryfikacja
- Wczoraj (19.04): 6× OOM kill między 12:50-15:51 UTC (V3.19 deploy chaos, RSS 760-980 MiB)
- Dziś (20.04): growth rate ~8 MiB/h w idle (baseline 07:59 UTC: 1020 MiB -> 10:34 Warsaw: 1025 MiB)
- **Hipoteza:** leak był triggered przez 11 restartów + intensywny debug podczas deploy, NIE jest systemowy
- **Fix = obserwacja przez tydzień:**
  - Jeśli growth <20 MiB/h stabilnie -> zamknąć jako solved (closed-root-cause: deploy chaos)
  - Jeśli spike się powtórzy (>50 MiB/h w normalnej pracy) -> deep dive (Node heapdump, profiling)
- Threshold operacyjny: 1.5 GiB = restart przed peakiem
- Restart procedure: cd /root/openclaw && docker compose restart openclaw-gateway
