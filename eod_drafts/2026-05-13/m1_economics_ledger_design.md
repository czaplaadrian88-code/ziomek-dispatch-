# M1 Order Economics Ledger V0 — Design Doc

**Data:** 2026-05-13
**Status:** DESIGN — implementacja gated by Faza 7 stable 14d gate (~2026-05-25)
**Master doc:** `~/.claude/projects/-root/memory/economics_engines_roadmap.md`
**Backlog entry:** `tech_debt_backlog.md` sekcja `M1`

---

## 1. Cel i scope V0

**Cel:** policzyć contribution margin per order z błędem <5% (vs ręczny reconcile z bankiem), żeby odblokować decyzje pricing/throttling/bundling.

**Anti-scope (V0 NIE robi):**
- Per-area / per-restaurant profitability UI (data zapisana, raporty Q3+)
- Parquet rollups (SQLite wystarczy do 30d + ~150d backfill)
- Auto-reconcile z bankiem (manual Excel do 20k/mc)
- Pricing decisions (shadow advisor osobno, PA-SHADOW Q3 post M1 reconcile clean)

---

## 2. Cost model — formuła zamknięta po decyzji Adrian 2026-05-13

```python
# constants (module-level, env-overridable)
COURIER_PLN_PER_MIN = 0.50          # = 30 zł/h ÷ 60 (Adrian decyzja 2026-05-13)
PAYMENT_FEE_PCT_RUTCOM = 0.02       # 2% gross — TODO verify with bank statement
PAYMENT_FEE_PCT_COD = 0.0           # COD = brak processing fee
OVERHEAD_ALLOCATION = "linear_per_order"   # vs "weighted_per_value" (v2)

def order_cost(order, courier_minutes, gross_value, payment_method, overhead_per_order_today):
    """V0 cost calculation per order. Bundling: per-order share = courier_minutes_share."""
    courier_time_cost = courier_minutes * COURIER_PLN_PER_MIN

    if payment_method == "online":  # BLIK, karta, P24
        payment_fee = gross_value * PAYMENT_FEE_PCT_RUTCOM
    else:  # COD
        payment_fee = 0.0

    return {
        "courier_time_cost": round(courier_time_cost, 2),
        "payment_fee": round(payment_fee, 2),
        "overhead_share": round(overhead_per_order_today, 2),
        "total_variable_cost": round(courier_time_cost + payment_fee + overhead_per_order_today, 2),
    }
```

**Daily overhead computation (nightly batch lub on-demand):**
```python
def overhead_per_order_today(date):
    # liniowo per order — decyzja Adrian 2026-05-13
    # weighted_per_value odroczone do v2
    total_overhead = sum([
        adrian_time_cost_pln_per_day,   # ZAŁOŻENIE: do dyskusji, na razie 0 (Adrian = founder, nie cost)
        infra_cost_pln_per_day,          # Hetzner + domain + cert / 30
        rutcom_fee_pln_per_day,          # FIXED PART jeśli jest; per-order część w payment_fee
    ])
    orders_today = count_completed_orders(date)
    if orders_today == 0:
        return 0.0
    return total_overhead / orders_today
```

**Courier minutes — definicja kluczowa:**

```
courier_minutes_for_order = (delivered_at - assigned_at) - bundle_overhead_share
```
- dla single order: `(delivered_at - assigned_at)` w minutach
- dla bundle (2 orderów): `total_route_time / 2` jako naive split V0; v1.1 może weighted by drop_distance share

**TODO przed implementacją:** zweryfikować z Rutcom/bank wyciągami:
1. Czy `payment_fee_pct_rutcom` = 2% jest poprawny (sprawdzić wyciąg za 1 tyg)
2. Jakie są fixed Rutcom fees per month → daily share
3. Czy są inne ukryte cost'y (sms, processing)

---

## 3. Schemat danych

### 3.1 Hot path — JSONL append-only
**Lokalizacja:** `/root/.openclaw/workspace/dispatch_state/order_economics.jsonl`

Każdy zakończony order (post-delivered event) → jedna linia JSON:
```json
{
  "schema": "order_economics_v1",
  "order_id": "472458",
  "ts": "2026-05-13T19:43:01.412Z",
  "completed_at": "2026-05-13T18:25:33.000Z",
  "restaurant_id": "pierogarnia_x",
  "courier_id": 421,
  "area_id": "BIA_CENTER",
  "revenue": {
    "delivery_fee_pln": 10.00,
    "commission_pln": 4.50,
    "gross_pln": 14.50
  },
  "variable_costs": {
    "courier_time_cost": 12.50,
    "courier_minutes": 25.0,
    "payment_fee": 0.29,
    "overhead_share": 1.20,
    "total_variable_cost": 13.99
  },
  "contribution_margin": 0.51,
  "context": {
    "was_bundled": false,
    "bundle_id": null,
    "was_scheduled": false,
    "fleet_load_at_dispatch": 0.43,
    "payment_method": "online",
    "drive_distance_km": 3.2,
    "restaurant_reliability_score": null
  }
}
```

**Append-only convention:** nigdy nie modyfikujemy past lines. Korekty → nowa linia z `correction_of: order_id` field i `correction_reason`.

### 3.2 Cold path — SQLite daily rollup
**Tabela:** `order_economics_daily` w `/root/.openclaw/workspace/dispatch_state/dispatch_state.db`

```sql
CREATE TABLE IF NOT EXISTS order_economics_daily (
    order_id        TEXT PRIMARY KEY,
    ts              TEXT NOT NULL,
    completed_at    TEXT NOT NULL,
    restaurant_id   TEXT,
    courier_id      INTEGER,
    area_id         TEXT,
    revenue_gross   REAL,
    revenue_delivery_fee REAL,
    revenue_commission REAL,
    cost_courier    REAL,
    cost_payment    REAL,
    cost_overhead   REAL,
    cost_total      REAL,
    contribution_margin REAL,
    courier_minutes REAL,
    drive_distance_km REAL,
    was_bundled     INTEGER,  -- 0/1
    bundle_id       TEXT,
    was_scheduled   INTEGER,  -- 0/1
    fleet_load_at_dispatch REAL,
    payment_method  TEXT,
    raw_json        TEXT      -- full JSONL line backup
);

CREATE INDEX idx_oed_completed_at ON order_economics_daily(completed_at);
CREATE INDEX idx_oed_restaurant ON order_economics_daily(restaurant_id);
CREATE INDEX idx_oed_courier ON order_economics_daily(courier_id);
CREATE INDEX idx_oed_was_bundled ON order_economics_daily(was_bundled);
CREATE INDEX idx_oed_was_scheduled ON order_economics_daily(was_scheduled);
```

**Rollup mechanism:** dzienny systemd timer 02:00 Warsaw czyta JSONL od poprzedniego dnia → INSERT OR IGNORE do SQLite (idempotent). JSONL retention: 30d (rotacja).

---

## 4. Moduły kodu

```
dispatch_v2/economics/                # NEW pakiet
├── __init__.py
├── cost_model.py                     # constants + order_cost() funkcja
├── overhead_calculator.py            # daily overhead allocation
├── order_economics.py                # writer (JSONL append)
├── margin_observer.py                # event hook na "order_delivered"
├── daily_rollup.py                   # systemd-driven JSONL → SQLite
├── pricing_advisor.py                # PA-SHADOW (Q3 — placeholder w V0)
└── reports.py                        # 3 raporty V0 (FastAPI endpoints)
```

### 4.1 Event hook (margin_observer.py)

Subskrybuje **istniejący event bus** (per A4 broadcast LIVE) na `order_delivered`:
```python
def on_order_delivered(event):
    order = load_order(event.order_id)
    courier_minutes = compute_courier_minutes(order)
    gross = order.gross_value_pln
    payment = order.payment_method

    cost = order_cost(order, courier_minutes, gross,
                       payment, overhead_per_order_today_cached())
    record = build_economics_record(order, cost)
    append_jsonl(record)
```

**Async/sync:** sync writer (JSONL append) — to <1ms operation per order. NIE blokuje delivery flow.

### 4.2 3 raporty V0 (FastAPI)

**Endpoint:** `/internal/economics/daily`
- Query params: `date_from`, `date_to`, `granularity=day|30min`
- Returns JSON:
  1. **CM/order distribution:** median, p25, p75, p90, min, max
  2. **% below break-even per 30-min window:** `count(cm<0) / count(all)`
  3. **Margin per active fleet hour:** `Σ revenue - Σ courier_cost / active_hours`

Renderowane jako Markdown table w Telegram bot (operator daily digest) + raw JSON for dashboards później.

---

## 5. Implementation plan — 4 sprints (2 tyg)

### Sprint M1.1 — Schemat + cost_model (3 dni)
- [ ] `cost_model.py` z constants + `order_cost()` + 5 unit tests (default, online vs COD, bundle split, zero-overhead day, edge case 0 courier_minutes)
- [ ] `overhead_calculator.py` + 2 unit tests
- [ ] SQLite table create + migrate script

### Sprint M1.2 — Writer + event hook (3 dni)
- [ ] `margin_observer.py` subskrypcja eventu (per A4 broadcast pattern)
- [ ] `order_economics.py` JSONL append-only writer
- [ ] Integration test: simulate `order_delivered` event → JSONL line poprawnie zapisany
- [ ] Backfill last 7 dni z order events (replay)

### Sprint M1.3 — Daily rollup + 3 raporty (3 dni)
- [ ] `daily_rollup.py` JSONL → SQLite z idempotent INSERT OR IGNORE
- [ ] Systemd timer `dispatch-economics-rollup.timer` daily 02:00 Warsaw
- [ ] `reports.py` 3 endpointy FastAPI
- [ ] Smoke test każdego raportu na 7-dniowym backfill

### Sprint M1.4 — Manual bank reconcile (5 dni — running, NIE blocking)
- [ ] Eksport: SQLite weekly aggregate → Excel template
- [ ] Adrian wkleja wyciąg bankowy → reconcile arkusz
- [ ] **Acceptance gate:** średnia delta < 3% przez 4 tyg consecutive

### Sprint M1.5 — Bundle profitability enrichment (1 dzień, post-S1)
- [ ] Gdy S1 Bundling LIVE → `was_bundled` / `bundle_id` populated od dnia 1 (NIE retrofit)

---

## 6. Acceptance criteria (must-pass przed ACK Adrian START PA-SHADOW)

| # | Criterion | Validation |
|---|---|---|
| A1 | JSONL writes dla 100% completed orderów | grep count == SQL count over 7d |
| A2 | Daily rollup idempotent (re-run produces same SQLite rows) | unit test + manual rerun |
| A3 | Median CM/order policzona, manual recheck 100 orderów < 5% błąd | spreadsheet validation |
| A4 | 3 raporty endpointy LIVE w FastAPI internal | curl smoke |
| A5 | Bank reconcile 4 tyg consecutive < 3% błąd | Excel template results |
| A6 | Bundle profitability enrichment hookable (placeholder OK do S1) | code review |
| A7 | Cost model unit tests 100% PASS | pytest |

---

## 7. Ryzyka implementacyjne M1

| # | Ryzyko | Mitygacja |
|---|---|---|
| R1 | `payment_fee_pct` źle oszacowany → systematyczny błąd | Tydzień 1: ręczna weryfikacja 1 tyg wyciągu pre-impl |
| R2 | `courier_minutes` per order w bundlu źle policzony → margin per bundled order kłamie | V0: naive split; flag `bundle_split_method='naive_v0'`. V1.1 weighted po S1 LIVE |
| R3 | Order events nie zawsze dochodzą (network/edge case) → missing rows | Backfill skrypt z `orders` table + alert na missing rate >2% |
| R4 | Adrian czas cost (overhead) — undefined | V0: 0 PLN, dyskusja w Sprint M1.4 |
| R5 | Rutcom fixed monthly fee — undefined | TODO check kontrakt; fallback ZAŁOŻENIE 200 zł/mc |

---

## 8. Open questions — status po Adrian 2026-05-13

1. ⚠️ **PENDING VERIFY** — Rutcom payment fee % per online order. **Adrian nie wie, Claude nie wie.** V0 zakładamy `PAYMENT_FEE_PCT_RUTCOM = 0.0` + flag `VERIFY_FROM_BANK_STATEMENT`. Manual reconcile Sprint M1.4 (4 tyg) wykryje rzeczywistą wartość — wtedy update constant.
2. ⚠️ **PENDING VERIFY** — Rutcom fixed monthly fee (jeśli istnieje). **Adrian nie wie.** V0 zakładamy `RUTCOM_FIXED_MONTHLY_PLN = 0`. Reconcile wykryje.
3. ✅ **CLOSED 2026-05-13** — `ADRIAN_TIME_COST_PLN_PER_DAY = 0` (founder ≠ cost, Adrian decyzja).
4. ✅ **CLOSED 2026-05-13** — `INFRA_COST_PLN_PER_MONTH = 300` → daily 10 zł → per-order ~0.038 zł (przy 265 ord/d).
5. ⏸️ **DEFERRED do S1 design Q3** — bundle courier_minutes split. V0 placeholder `naive_v0` (total_route_time / N) + flag `bundle_split_method`. Wyjaśnienie dla Adriana gdy S1 rusza: bundle = 2+ orderów na 1 kursie, jak rozliczyć minuty kuriera per-order (równo czy proporcjonalnie do dystansu jednego stopu).

**Constants module-level po decyzjach:**
```python
COURIER_PLN_PER_MIN = 0.50          # closed 13.05
PAYMENT_FEE_PCT_RUTCOM = 0.0        # PENDING VERIFY via reconcile
PAYMENT_FEE_PCT_COD = 0.0
RUTCOM_FIXED_MONTHLY_PLN = 0        # PENDING VERIFY via reconcile
INFRA_COST_PLN_PER_MONTH = 300      # closed 13.05
ADRIAN_TIME_COST_PLN_PER_DAY = 0    # closed 13.05 (founder ≠ cost)
OVERHEAD_ALLOCATION = "linear_per_order"  # closed 13.05
```

**Daily overhead (closed):**
```
overhead_per_day = INFRA_COST_PLN_PER_MONTH/30 + ADRIAN_TIME_COST_PLN_PER_DAY + RUTCOM_FIXED_MONTHLY_PLN/30
                 = 10 + 0 + 0 = 10 zł/dzień
overhead_per_order = 10 / count_completed_orders_today
                  ≈ 0.038 zł/order @ 265/d
```

To jest tymczasowo LOW vs reality — gdy reconcile pokaże gap, dodajemy `RUTCOM_FIXED_MONTHLY_PLN` i/lub korygujemy `PAYMENT_FEE_PCT`.

---

## 9. Cross-ref

- Master strategy: `~/.claude/projects/-root/memory/economics_engines_roadmap.md`
- Backlog: `~/.claude/projects/-root/memory/tech_debt_backlog.md` sekcja M1
- Decision context: `~/.claude/projects/-root/memory/sprint_timeline.md` CURRENT HANDOFF 2026-05-13
- Event bus pattern reference: A4 broadcast (commit `c52a4c2` audit follow-up 2026-05-09)
- Project conventions: `dispatch_v2/CLAUDE.md` 16-step patch workflow
