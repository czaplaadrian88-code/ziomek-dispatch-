# Decision Record — TASK A keep / tune / disable

**Data:** 2026-05-06 (Pn)
**Author:** CC main (post Krok 2 RC verification `_faza_7_root_cause_pn_06_05.md`)
**Status:** PROPOSED — pending Adrian ACK

## Context

TASK A (czasowka_proactive proactive lookahead T-50/T-40/T-30) deployed 04-05.05 jako pre-condition dla Faza 7-AUTO-PROXIMITY (dać Bartkowi early signal dispatcha czasówek).

Live observation 5/5 fires 14:24-15:31 UTC 05.05: **100% NO_CANDIDATE**. Main dispatcher 3/3 unique oid znalazł kandydata 11-19 min later (manual KOORD path).

## RC

H-G CONFIRMED (`_faza_7_root_cause_pn_06_05.md`): **structural data loss** w `czasowka_scheduler.py:310-318` WAIT branch. `result.candidates` (46 entries logged przez scheduler) jest nullified do `best=None alternatives=[]` PRZED przekazaniem do `czasowka_proactive.maybe_fire_trigger`. `_filter_candidates` widzi pusto → 100% NO_CANDIDATE info-only.

Dodatkowy strukturalny finding: nawet po fix H-G, threshold `CZASOWKA_MIN_PROPOSAL_SCORE=60` może być nadal restrictive — w peak window większość kandydatów ma score 30-50 z `verdict=NO`.

## Options

### Opt A — Disable TASK A do post-fix validation
- Flag flip `CZASOWKA_PROACTIVE_ENABLED=false`
- Zero noise, zero risk
- Koszt: brak proactive lookhead dla Bartka, ale to i tak nie działało
- Re-enable after H-G fix + 1-week obs validation

### Opt B — Keep + fix H-G + tune thresholds (REC)
- AIDER fix `czasowka_scheduler.py:310-318` zachować candidates w WAIT branch (~20-30 LOC)
- AIDER tune `CZASOWKA_MIN_PROPOSAL_SCORE=60→40` jako safety net (~5 LOC + flag bump)
- T0_ALERT off (handoff todo) jako interim mitigation noise
- 1-week obs target re-validate **08.05 (Cz)**

### Opt C — Keep + tune only (skip H-G fix)
- Lower threshold do 30, allow `verdict=NO` z penalty
- Łatwy ale fundamentally broken — structural data loss niepokonany przez threshold tuning
- ❌ NOT RECOMMENDED

## Recommendation

**Opt B**.

Justification:
- Z2 quality > speed: H-G fix to root cause, threshold tuning to safety net. Bez fix #1, threshold tuning useless.
- Z3 buduj na lata: TASK A architecture sound (proactive lookhead na shared eval), tylko implementation bug. Disable byłoby premature.
- Lekcja #72 granular flag rollback: H-G fix LIVE z flag default OFF, gradual flip post-validation.
- Cross-ref Wytyczna #1 (4-checkbox pre-implementation review): Faza 7 + TASK A oba miały contract bugs nie wykryte w smoke tests bo używały mocks. Future implementations: integration test against production-shaped Candidate objects MANDATORY.

## Decision dependencies

- **Adrian ACK** na Opt B przed AIDER deploy
- **T0_ALERT off** zaaplikować rano (handoff Adrian todo) — zero blast radius, minimuje noise do fix LIVE
- **Branch**: `sprint-06-05-debug` isolated (per Section 6 Unknown #1 REC B)

## Cross-refs

- `_faza_7_root_cause_pn_06_05.md` (this sprint Krok 4 RC)
- `lekcja_74_2026-05-05.md` (evaluator-vs-main divergence pattern)
- `faza_7_debug_plan_pn_06_05.md` (sprint plan)
- Lekcja #57 (training-prod parity)
- Lekcja #72 (granular flag rollback)
- Lekcja #71 (state lifecycle decoupling)
- Wytyczna #1 (4-checkbox pre-implementation review — to add)
