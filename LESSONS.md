# LESSONS — Ziomek dispatch_v2

Architectural lessons learned per sprint, sequential numbering. Reguły mają
applicability dla future sprints — nie tylko historical record. Każda lekcja
zawiera Problem, Konsekwencje, Reguła, Identical pattern do (cross-references).

---

## Lekcja #28 (V3.27.1 sesja 2 ROLLBACK + sesja 3 vindication)

### Problem
Mock unit tests z fake schema klucz `{"czas_kuriera_warsaw": "..."}`
PASSED 9/9. Integration FAILED w produkcji — real panel API zwraca
raw response z `czas_kuriera` HH:MM, klucz `czas_kuriera_warsaw` NIE
istnieje w raw, jest computed downstream przez `panel_client.normalize_order()`.

### Konsekwencje
- Sesja 2 atomic flag flip 19:05 Warsaw → CASE C RED w 5 critical
  checks decision matrix
- Latency 6949ms (9.5x baseline) z error path amplification per emit
- 10 ERROR linii "skipping persist" + state_machine sanity FAIL
- Rollback w 5 min (env override pattern + git reset)

### Reguła
Mock unit tests z fake schema = false confidence. **Integration
tests z real `panel_client.normalize_order` flow REQUIRED** dla
wszystkich helpers wywołujących panel_client.

Pattern dla testów: mock external HTTP boundary (panel API raw
response), use real internal logic (normalize_order, validation).
Edge case test dla normalize_order None return (status 7/8/9
delivered/cancelled).

### Identical pattern do
- **Lekcja #1** (Parse wrapper invisible data loss V3.19f) —
  panel_client zwracał `raw.get("zlecenie")` bez innych top-level keys
- **Lekcja #18** (Empirical validation > unit test V3.27)

---

## Lekcja #29 (V3.27.1 sesja 3 NEW)

### Problem
Sesja 3 atomic flag flip post Bug 1 fix → latency 6748ms (RED)
mimo że Bug 1 fix DZIAŁA (zero state_machine errors).

### Diagnoza
`panel_client.fetch_order_details` używał login refresh co 22 min
(CSRF token expiry). Logowanie zajmuje 6-7s.

Pre-V3.27.1: panel_watcher async (off proposal latency path) —
login refresh niewidoczne dla user.

V3.27.1 sesja 3: pre_proposal_recheck używa fetch_order_details
**synchronicznie w dispatch_pipeline** (proposal latency path) →
login refresh propaguje do proposal latency.

### Smoking gun
5 proposals post-restart 19:06:
- 3 proposals (568, 280, 680ms) = no login = ✓
- 2 outliers (6748, 7604ms) = 100% korelacja z login refresh events

Math projection lunch peak: 50-100 props/h × 3 logins/h = **3-6%
outliers rate** (overnight verify 1/16 = 6% match).

### Reguła
**Sync calls w hot path mogą ujawnić latency istniejących
operacji niewidocznych off-path.**

Przy dodawaniu sync calls do hot path, **audit istniejących
architectural assumptions** call'owanego komponentu. Off-path
latency tolerance ≠ on-path tolerance.

Pre-deploy audit: dla każdego sync addition, prześledź call chain
od start do end, dla każdego service dependency identyfikuj
off-path overhead który teraz staje się on-path (login refresh,
connection pool init, timeout retries, periodic blocking ops).

### Fix progressive enhancement
- **A) Tolerate** (zero effort, partial — peak math 3-6% rate ok)
- **B) Pre-warm login startup** (5 min, eliminates first-proposal
  cold start) — sesja 4 jutro
- **C) Background login refresh thread** (30-60 min, complete fix,
  V3.28 strategic Warsaw expansion)

### Identical pattern do
- **Lekcja #20** (Strategic decision principle — quality + scaling
  > pragmatic shortcuts)
- **Lekcja #22** (Distance matrix z traffic multipliers — KAŻDA
  kalkulacja używana do TSP/scoring/ETA MUSI iść przez
  `get_traffic_multiplier()`)

---

## Cross-reference do TECH_DEBT.md "📚 LEKCJE V3.27"

Pełne lessons history w `TECH_DEBT.md` sekcji "📚 LEKCJE V3.27 (added)":
- #25 Mental simulation może być naivny (V3.27 Bug Y)
- #26 Domain knowledge > LLM/API confidence (V3.27 Filipowicza)
- #27 Hardware oversubscription dla parallel (V3.27 CPX22)
- #28 Mock tests passed ale integration FAIL (V3.27.1 sesja 2 Bug 1) ← above
- #29 Sync calls hot path ujawniają latency niewidoczną off-path
  (V3.27.1 sesja 3 panel_client login refresh) ← above

LESSONS.md = curated subset (krytyczne lekcje sesji), TECH_DEBT.md = full history
z context tickets/bug refs.
