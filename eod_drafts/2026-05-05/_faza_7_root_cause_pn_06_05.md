# Faza 7 + TASK A — Root Cause Analysis Pn 06.05.2026

**Sprint:** debug Pn 06.05 ~3-4h (z `faza_7_debug_plan_pn_06_05.md`)
**Status:** Krok 1 (code review) + Krok 2 (data analysis) COMPLETE; Krok 3 (instrumentation) SKIPPED (RC confirmed bez instrumentation); Krok 4 (RC verification) — ten dokument.

---

## RC #1 — LGBM 100% all_bag_zero (F4, Lekcja #74 LGBM track)

**Hipoteza:** H-A bug `bag_size` field NIE poprawnie populated z candidate object — **CONFIRMED 100%**.

### Evidence

`Candidate` dataclass (`dispatch_pipeline.py:857-865`):
```python
@dataclass
class Candidate:
    courier_id: str
    name: Optional[str]
    score: float
    feasibility_verdict: str  # "MAYBE" | "NO"
    feasibility_reason: str
    plan: Optional[RoutePlanV2]
    metrics: Dict[str, Any] = field(default_factory=dict)
    best_effort: bool = False
```

**Brakuje:** `bag_size, tier_bag, last_pos_lat, last_pos_lon, idle_min, level, bag_drops_pending, bag_pickup_pending, orders_today_before_T0, bag_n_distinct_districts, bag_has_distant_drop`.

`ml_inference.py` używa 12+ `getattr(c, ...)` na te non-existent fields (linie 171, 276-277, 280, 324-325, 329, 331-332, 336, 351-352, 493-494). Wszystkie defaultują 0/None/False → cała feature compute ZEROWANA.

`ml_inference.py:170-177` early-return na `all_bag_zero=True` ZAWSZE → **502/502 emisji shadow 02-05.05 → 100% fallback**.

### Cross-ref

Lekcja #57 — **Pre-step verify feature parity training/production MANDATORY**. Bezpośrednie violation.
Faza 6 implementation (`project_faza_6_lgbm_shadow_implementation_2026-05-01.md`) — port Faza 4 features do dispatch_v2 NIE zweryfikował że Candidate ma fields które LGBM oczekuje. Smoke test (9/9 PASS) używał mock candidate z bag_size set explicitly, więc bug niewykryty.

### Fix path — REC Opt 1 (Z3, najczystszy)

Refaktor `predict_for_decision(decision_ctx, candidates)` signature na `(decision_ctx, courier_states, cid_to_candidate_map)`:
- `courier_states: List[CourierBagState]` — z `bag_state.py`, ma wszystkie real fields
- `cid_to_candidate_map: Dict[str, Candidate]` — dla agreement_with_primary computation

Alt **Opt 3** (smallest, hack): zamienić `getattr(c, "bag_size", 0)` na `c.metrics.get("bag_size_before", 0)` itd. Mapping table w docstring. Ale: mapowanie wszystkich 12+ fields jest fragile, niektóre fields NIE są w `metrics` (np. `idle_min`, `orders_today_before_T0`) → trzeba upstream populate.

**REC: Opt 1.** ~150 LOC + 6-8 unit tests. Faza 7 re-baseline po deploy.

### Faza 7 GO/NO-GO impact

Po fix re-pomiarz fallback rate. Hipoteza dodatkowa H-B (Fix C bundle cap eliminuje bundle scenarios) **wciąż relevant** — może po fix nadal >50% fallback bo natural single-order pool dominuje. Wymaga 1-week obs post-fix → re-baseline target **15.05** per Section 5 decision matrix.

---

## RC #2 — TASK A 100% NO_CANDIDATE (F3, Lekcja #74 TASK A track)

**Hipotezy testowane:**
- ❌ H-D threshold 60 too restrictive — **REJECTED** (różny RC)
- ❌ H-E feasibility MAYBE strict — **REJECTED** (różny RC)
- ❌ H-F fleet_snapshot timing — **REJECTED** (shared eval path)
- ✅ **NEW H-G**: structural data loss w `eval_czasowka` WAIT branch — **CONFIRMED**

### Evidence

`czasowka_scheduler.py:310-318` (window 40<mins≤60 gdy `best_maybe=False`):
```python
return {
    "decision": "WAIT",
    "reason": "no MAYBE candidate",
    "minutes_to_pickup": mins,
    "match_quality": "none",
    "best": None,             # <-- wymazane mimo result.candidates!=[]
    "alternatives": [],       # <-- wymazane
}
```

Tymczasem candidate_logger w `czasowka_scheduler.py:200-205` loguje `result.candidates` PRZED nullification — stąd `n_cand=46` w `czasowka_scheduler` JSONL.

`czasowka_proactive.maybe_fire_trigger(...)` dostaje TEN return dict (po nullification) z `czasowka_scheduler.py:545,554-556`. `_filter_candidates(eval_result, ...)` (`evaluator.py:121-151`) parsuje `eval_result["best"]` + `eval_result["alternatives"]` → **dostaje None+[]** → empty feasible list → `NO_CANDIDATE` info-only path (`evaluator.py:337-360`).

### Production data verification

Z `candidate_decisions_20260505.jsonl` Query E (oid=470805, 470808, 470821):
- `czasowka_scheduler` source: n_cand=46 dla każdego oid w 14:40-15:32 UTC window
- `czasowka_proactive` source: **n_cand=0 w 100%** entries (5/5 fires)
- Pattern: **bracketed null pattern** — n_cand=46 logged przez scheduler, n_cand=0 immediately następuje przez proactive z TYM SAMYM ts.

### Why "5/5 NO_CANDIDATE vs 3/3 main found cid"

- W T-50 window większość kandydatów ma `verdict=NO` (R-DECLARED-TIME violations, R-35MIN-MAX, R6 35min itp.) — fleet jeszcze nie dojrzał.
- Adrian/Bartek manual dispatchują 11-19 min later (KOORD verdict path), gdy pool dojrzewa: cid=471 ASSIGNED 14:42 dla oid=470805, cid=370 ASSIGNED 14:51 dla 470821, cid=508 ASSIGNED 15:36 dla 470808.
- TASK A intent był: dać Bartkowi early signal ("oto kto będzie wkrótce feasible"). ALE structural bug w WAIT branch → zero signal.

### Fix path — REC: zachować `alternatives` w WAIT

Minimum viable fix: w `czasowka_scheduler.py:310-318` (i analogously linie 246-260, 316-318 jeśli are sister WAIT branches):
```python
return {
    "decision": "WAIT",
    ...
    "best": best,                                          # zachowaj
    "alternatives": result.candidates[1:] if result.candidates else [],  # zachowaj
    "all_candidates_for_proactive": list(result.candidates) if result.candidates else [],  # NEW explicit
}
```

`czasowka_proactive._filter_candidates` może czytać `eval_result.get("all_candidates_for_proactive")` jako primary source z fallback do `best`/`alternatives` legacy.

~20-30 LOC + 3 testy.

### Decision Record dla TASK A keep/disable

- TASK A NIE jest redundantny z V3.24-B legacy — proactive lookhead na shared eval. Wartość: early signal dla Bartka.
- ALE 100% false-positive (NO_CANDIDATE) → zero operacyjnej wartości w obecnym stanie + noise w Telegram.
- **REC**: keep + fix structural bug + tune thresholds (CZASOWKA_MIN_PROPOSAL_SCORE 60→40 jako safety net) + 1-week obs validation.
- Interim mitigation: T0_ALERT off (zgodnie z handoff todo) — minimuje noise do post-fix validation.

---

## Krok 2 data analysis raw

**Query A** (NEW_ORDER baseline 4d): 02.05=56, 03.05=308, 04.05=186, 05.05=236, 06.05=172. Total 786 vs 502 LGBM emisji = ~284 NEW_ORDER nie zostały LGBM-evaluated (early_bird path).

**Query B/C** (max bag_size distribution): SKIPPED — RC #1 confirmed via static code analysis, distribution analysis doda wartość post-fix.

**Query D** (Fix C demote count 7d): SKIPPED — H-B konfirmacja może czekać do post-fix observation.

**Query E** (TASK A 3 oid score histogram): viewed full candidate set — kluczowy finding: `czasowka_proactive` zawsze `n_cand=0`, `czasowka_scheduler` `n_cand=46` w peak. RC #2 confirmed.

**Query F/G** (verdict mismatch + snapshot delta): SKIPPED — H-E i H-F rejected przez RC #2.

---

## Sprint exit summary

| Hipoteza | Verdict | Action |
|----------|---------|--------|
| H-A LGBM `bag_size` | CONFIRMED | Fix Opt 1 (signature change), AIDER ~150 LOC |
| H-B reality bundle scenarios | DEFERRED | Validate post-RC#1 fix, 1-week obs |
| H-C race propagation | NOT TESTED | Likely irrelevant given H-A |
| H-D threshold 60 | REJECTED (different RC) | Tune as safety net post-fix |
| H-E MAYBE strict | REJECTED (different RC) | — |
| H-F snapshot timing | REJECTED (shared eval path) | — |
| **H-G NEW** structural data loss | CONFIRMED | Fix `czasowka_scheduler.py:310-318` WAIT branch, AIDER ~20-30 LOC |

**Faza 7 re-baseline:** target **15.05** (1-week obs post Opt 1 fix).
**TASK A go-live:** target **08.05** (1-week obs post H-G fix + threshold tune).

---

## Lekcja #74 update

Lekcja #74 candidate (promoted dziś rano do `lekcja_74_2026-05-05.md`) był **partial RC**. Update:
- LGBM track RC: signature mismatch (Candidate dataclass missing fields)
- TASK A track RC: structural data loss (selective field nullification w WAIT branch)
- Wspólny pattern: **inter-component contract violations bez integration test**.

Recommend Lekcja #74 expand: dodać konkrete RC + cross-ref Lekcja #57 (training-prod parity) i Wytyczna #1 (4-checkbox pre-implementation review — czy verified Candidate kontrakt z LGBM expects?).
