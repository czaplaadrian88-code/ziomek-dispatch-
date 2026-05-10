# Faza 7 GO/NO-GO Criteria — pre-decision report 2026-05-05

**Decision date:** Pt 08.05.2026 (planowany)
**Author:** read-only research agent (sandbox)
**Status draft:** PRE-DECISION — Adrian review wymagany

---

## 1. Faza 7 design recap

Faza 7 (per `faza_7_design_spec_2026-05-02.md` w memory) implementuje **Phase 2** architektury 4-phase BC + guardrails + continuous learning:

- **Flag flip:** `ENABLE_LGBM_PRIMARY=1` — LGBM staje się primary scorer, Ziomek (rule-based) demote do alt-comparator.
- **Winner promotion:** `dispatch_pipeline.py` po feasible filter promuje LGBM top-1 jako BEST (zamiast scoring.py top-1).
- **ALT Explorer:** Ziomek primary ranking renderowany jako "alt line" w Telegram gdy disagreement; gdy agreement — single BEST line.
- **Telegram UX:** V3.19i reason buttons rozszerzone o `LGBM_AGREE_WRONG`, `LGBM_DISAGREE_OK`, `LGBM_DISAGREE_INTUITION` dla feedback enrichment Phase 3.
- **Fallback mode:** `LGBM_PRIMARY_FALLBACK_TO_ZIOMEK=1` (default ON) — LGBM error → instant fallback do Ziomek, zero downtime.
- **Rollback:** flip `ENABLE_LGBM_PRIMARY=0`, 5s revert.

**Pre-conditions** (z spec):

> agreement>=75% AND fallback<=10% AND latency p95<=100ms AND sample>=200 decisions w 24-48h shadow obs

---

## 2. Pre-condition matrix

| Metric | Target | Current (2026-05-05 13:06 UTC) | Status |
|--------|--------|---------------------------------|--------|
| Agreement rate (LGBM top1 == Ziomek top1) | >=75% | **N/A — 0 agreement data points** (502/502 fallback) | **FAIL** |
| Fallback rate (LGBM no-pred → Ziomek) | <=10% | **100.0%** (502/502 `all_bag_zero`) | **CRITICAL FAIL** |
| Latency p95 LGBM eval | <=100ms | **0.04ms** (lazy fallback NIE pełen forward) | **PASS (z asterisk — fallback path only)** |
| Sample size (24-48h obs) | >=200 decisions | **502 emisje** w 02-05.05 (4 dni) | **PASS — sample size OK** |

**Verdict (raw matrix):** **NO-GO** — 2/4 criteria fail. Fallback rate jest dealbreaker.

---

## 3. Current state (2026-05-05 13:06 UTC) — observed data

### Flag deployment

- `common.py:1239-1240`: `ENABLE_LGBM_SHADOW = _os.environ.get("ENABLE_LGBM_SHADOW","0")=="1"`, `ENABLE_LGBM_PRIMARY = ... =="1"`.
- `/etc/systemd/system/dispatch-shadow.service.d/override.conf`: `Environment=ENABLE_LGBM_SHADOW=1` LIVE.
- `flags.json`: brak LGBM keys (env-driven only).
- Backup `override.conf.bak-pre-fix-c-flip-2026-05-01` istnieje — flag flip prawdopodobnie zsynchronizowany z Fix C deploy 01.05 ~14:57 lub krótko po.

### Decision logs (`/root/.openclaw/workspace/scripts/logs/dispatch.log`)

Ekstrakcja regex `LGBM_SHADOW oid=...`:

- **Total emisje:** 502
- **Per-day:** 02.05 = 2 (start), 03.05 = 288, 04.05 = 148, 05.05 = 64 (do 12:55 UTC)
- **Fallback distribution:**
  - `all_bag_zero`: 502 (100.0%)
  - `none` (real LGBM prediction): **0 (0.0%)**
  - `lgbm_error` / `feature_compute_error` / `latency_timeout` / `model_not_loaded`: 0
- **Agreement distribution:** 502/502 = `None` (LGBM nie scoruje gdy fallback)
- **Latency:** n=502, p50=0.03ms, p95=0.04ms, p99=0.06ms, max=0.16ms — wszystkie wartości to **early-return time z fallback path** (`ml_inference.py:170-177`), NIE pełny forward pass.
- **Pool size:** max=0, mean=0.00 — pool nigdy nie zawierał kandydata z `bag_size>=1`.

### Root cause `all_bag_zero` 100%

`ml_inference.py:170-177`:

```python
all_bag_zero = all((getattr(c, "bag_size", 0) or 0) == 0 for c in candidates)
if all_bag_zero:
    result.fallback_reason = "all_bag_zero"
    return result  # NO LGBM forward pass
```

Logika fallback weszła po **Faza 5 finding bundle bias 44.6% pairwise_acc dla single-order bag=0** (poniżej random) — defense gdy cały pool to bag=0 (pure pickup-distance ranking, brak sequence info do exploitu).

**Empirical reality post-deploy:** od 02.05 wieczór do 05.05 południe **żadna decyzja** z `LGBM_SHADOW` log linią NIE miała kandydata z `bag>=1`. To dwie hipotezy:

- **H1 (likely):** integration wpięta w `dispatch_pipeline.assess_order` — to entry point dla **NEW order** (gdzie new order ma 0 stops u kuriera w momencie wjścia do feasible). Filter kandydatów feasible dla new order = ich aktualne `bag_size`. W peak hours lunch/dinner istnieją kurierzy z `bag>=1` w panelu (V3.14 STRICT_BAG_RECONCILIATION, V3.15 packs_fallback) — ale czy są w feasible pool po SLA constraints to inna sprawa.
- **H2:** bag_size attribute pochodzi z `Candidate` objektu mid-pipeline, gdzie post-`build_fleet_snapshot` filter (gdzie phantom PIN-y są wykluczone, V3.13/V3.14) zwraca pure-empty kurierów. Wymagałoby grep `dispatch_pipeline.py` linii ~2200-2440 żeby potwierdzić jaki snapshot jest pasowany.

Bez Adriana ACK nie patcuje hipotezy. Ważny sygnał: **jeśli w prod 100% feasible pool to bag=0 — Faza 7 LGBM primary nie miałby co scorować**. To może być prawdziwy stan (po Fix C bundle cap 8km, wielu bundle scenarios eliminowanych pre-LGBM przez hard rules) ALBO bug detection logic.

---

## 4. Analysis script (pseudo-code, nie executed)

```python
# Pull last 7 days dispatch.log "LGBM_SHADOW oid=" lines
# Parse: oid, winner_lgbm, winner_current, agreement, fallback, latency_ms, pool_size

# Metric 1: Agreement %
real_decisions = [d for d in entries if d.fallback in (None, "NONE")]
if real_decisions:
    agreement_pct = sum(1 for d in real_decisions if d.agreement == "True") / len(real_decisions) * 100
else:
    agreement_pct = None  # ← current state: 0 real decisions

# Metric 2: Fallback %
fallback_pct = sum(1 for d in entries if d.fallback != "NONE") / len(entries) * 100

# Metric 3: Latency (only real decisions, fallback path nie reprezentatywne)
if real_decisions:
    p95 = sorted([d.latency_ms for d in real_decisions])[int(len(real_decisions)*0.95)]

# Metric 4: Sample size = total entries w window 24-48h
```

---

## 5. Decision matrix

| Result | Action |
|--------|--------|
| All 4 criteria met | **GO** Faza 7 sprint piątek 08.05 (4.5-6.5h) |
| 3/4 criteria met (one borderline) | **OBSERVE-MORE** — extend shadow 1 week, re-measure pt 15.05 |
| <=2/4 criteria met | **NO-GO** — debug ML pipeline (root cause `all_bag_zero` 100%, retrain albo extend feature scope) |

**Aktualny stan:** 2/4 (latency PASS asterisked, sample PASS) → **NO-GO** raw. Ale jeśli `all_bag_zero` to true reality (NIE bug), Faza 7 nie ma sensu w obecnej formie — LGBM primary nigdy nie wystrzeli, pipeline = passthrough do Ziomek.

---

## 6. Observation period extension scenarios

Wszystkie zakładają **debug all_bag_zero PRZEDe extending obs**:

| Scenariusz | Akcja | Decision date target |
|---|---|---|
| H1 confirmed (integration point too early — pre-bag candidates) | Re-wpiąć LGBM call later w pipeline (po V3.14/V3.15 reconciliation), retest 24h | 12.05 (Pn) |
| H2 confirmed (pool naturalnie bag=0 dominated post-Fix C) | Faza 5.2 retrain — model z bag=0 support albo Faza 7 design pivot (LGBM tylko gdy bag>=1, else passthrough) | 15.05 (Pt) |
| Real-life bag>=1 cases istnieją ale ich nie widać (logging gap) | Audit `dispatch_pipeline:2461-2496` LGBM emit guards + grep peak hours samples | 12.05 (Pn) |
| Sample <200 (po debug fix) | Extend obs 24-48h | +2-3 dni |
| Latency p95 >100ms (po real predictions widoczne) | Profile feature compute (likely OSRM cache miss + district kd-tree warm) | sprint extension |
| Agreement <75% (po real predictions) | 20-50 random samples manual review Adrian | gate Pt 22.05 |

---

## 7. 5 unknowns dla Adriana

1. **Czy `ENABLE_LGBM_SHADOW=1` deploy 01.05 wieczór był intencjonalny i kiedy dokładnie?** Backup `override.conf.bak-pre-fix-c-flip-2026-05-01` sugeruje flip w sąsiedztwie Fix C, ale memory mówi "default OFF". Audit: kiedy deploy faktycznie się stał i czy był ack'owany.
2. **`all_bag_zero` 100% — bug detection logic czy true reality?** 502 emisji w 4 dni z poolem 0/0 to wydaje się nie pasować do peak hour reality. CC nie patchuje bez ACK — Adrian musi wskazać czy oczekuje że post-Fix C pool faktycznie jest pure-empty czy expected bug w bag attribute fetch.
3. **Disagreement ground truth (jeśli kiedykolwiek będą real predictions):** Faza 6 finding bundle bias 44.6% acc dla bag=0 → przy wyższym disagreement % to LGBM może być right a Ziomek wrong. Jak Adrian planuje resolve disagreements — manual labeling sample czy A/B test outcome metrics (delivery time, customer rating)?
4. **Faza 7 sprint scope — czy 4.5-6.5h time-box wystarczy po debug?** Spec zakłada že shadow obs jest validated. Jeśli wymagamy +1 week debug + retrain, sprint piątek 08.05 jest unrealistic — proponuję re-baseline na 15.05 lub 22.05.
5. **Rollback assumption — `LGBM_PRIMARY_FALLBACK_TO_ZIOMEK=1` zachowuje SAFETY?** Jeśli LGBM error mid-peak, fallback to Ziomek powinien być transparent. Pytanie: czy fallback-on-error jest tested separately w smoke (fail injection)? Faza 6 ma 6 fallback paths defense-in-depth, ale post-flip primary inversion wymaga dodatkowego testu sentinel.

---

## 8. Rekomendacja dla Adriana

**NO-GO Faza 7 piątek 08.05 w obecnej formie.**

Plan B sequence:

1. **Pn 06.05** — debug `all_bag_zero` 100% root cause (CC pre-flight 30 min, memory check + grep dispatch_pipeline integration point + ml_inference candidate object reading).
2. **Wt-Śr 07-08.05** — fix wpięcia LGBM call do post-bag-reconciliation point (jeśli H1) lub Faza 7 design pivot dla bag=0 dominated reality (jeśli H2).
3. **Czw 08.05 wieczór** — re-measure 24h obs po debug; jeśli >=10% real predictions w peak hours, kontynuuj.
4. **Pt 15.05** — re-evaluate pre-condition matrix.

Alternatywa: **flip `ENABLE_LGBM_SHADOW=0` instant** (drop-in env override) jeśli Adrian woli zatrzymać shadow log noise dopóki debug nie skonkluduje. Backup override.conf już istnieje.

---

## Sources cited

- `/root/.openclaw/workspace/scripts/dispatch_v2/common.py:1239-1240` — flag definition
- `/root/.openclaw/workspace/scripts/dispatch_v2/dispatch_pipeline.py:2457-2507` — LGBM shadow integration block
- `/root/.openclaw/workspace/scripts/dispatch_v2/ml_inference.py:160-178` — `all_bag_zero` early-return path
- `/etc/systemd/system/dispatch-shadow.service.d/override.conf` — env var setting
- `/root/.openclaw/workspace/scripts/logs/dispatch.log` — 502 LGBM_SHADOW emissions 02-05.05
- `/root/.openclaw/workspace/dispatch_state/observability/candidate_decisions_2026050{4,5}.jsonl` — 486 decisions, 0 LGBM mentions (logging via stdout->journal->dispatch.log not jsonl observability)
- Memory: `faza_7_design_spec_2026-05-02.md`, `project_faza_6_lgbm_shadow_implementation_2026-05-01.md`, `project_faza_5_lgbm_training_2026-05-01.md`
