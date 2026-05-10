# HERE Hybrid — Next Session Plan

**Stworzony:** 2026-05-08 wieczór (~22:00 UTC = 24:00 Warsaw)
**Autor:** CC sesja 08.05 evening (Adrian + Claude)
**Cel:** handoff dla CC sesji startującej **2026-05-09 wieczór lub później** (po master merge 10.05)
**Lokalizacja kodu:** `/root/.openclaw/workspace/scripts/dispatch_v2/`
**Lokalizacja PoC:** `dispatch_v2/eod_drafts/2026-05-08/here_poc/`

---

## 0. Quick start — przeczytaj w tej kolejności

1. **Ten plik** (cały, ~30 min czytania) — kompletny plan 4 faz post-merge
2. `dispatch_v2/eod_drafts/2026-05-08/here_poc/` — Phase 0 PoC code + results JSONL z 09.05 cron measurement
3. **GATE A report** — `/root/here_poc_gate_a_report_2026-05-09.txt` (lub Telegram message do Adriana 09.05 20:00 Warsaw) — verdykt PROCEED vs STOP
4. `dispatch_v2/osrm_client.py` — pattern do mirror'owania (MP-#13 3-warstwowy degraded mode + circuit breaker + RLock)
5. `dispatch_v2/dispatch_pipeline.py` — integration point dla Phase 2 (`assess_order` + `_classify_and_set_auto_route`)
6. Memory: `feedback_rules.md` (TOP-level zasady, **AIDER auto-run rule** dodana 08.05) + `lessons.md` (#80-#99)
7. Tasks #1-#7 (TaskList) — current status sprint

---

## 1. Co już zrobione (sesja 08.05 wieczór)

### Klucz HERE provisioning
- HERE Developer account Adrian, free tier 250k routing/mc + 30k map images/mc
- API key zapisany: `/root/.openclaw/workspace/.env` jako `HERE_API_KEY=` (43 char length)
- Curl test PASS: HTTP 200, Rukola→Akademicka 368s traffic / 317s freeflow

### PoC stub measurement
- Files: `dispatch_v2/eod_drafts/2026-05-08/here_poc/measure_delta.py` (180 LOC), `analyze.py` (100 LOC), `trips_sample.jsonl` (10 par Białystok)
- Single stub run done 21:42 UTC = offpeak Warsaw — 10/10 success, latency HERE p95 152-354ms (PASS ≤400), delta median peak 1.27 (FAIL ≥2.0 — BUT measurement w night, not real peak)
- Stale `results_<unixts>.jsonl` deleted, daily-file append mode aktywne

### Cron measurement scheduled (09.05)
- 8 jobs UTC 10:00-17:00 (Warsaw 12:00-19:00) wywołują `/root/here_poc_cron_run.sh` → `measure_delta.py --limit 10` → append do `results_2026-05-09.jsonl`
- 1 job UTC 18:00 (Warsaw 20:00) wywołuje `/root/here_poc_gate_a_report.sh` → `analyze.py` + Telegram self-notify do Adriana 8765130486
- atq state: 9 jobów (jobs 6-14) plus existing 3 (jobs 1, 2, 5)

### Architectural pivot trail (sesja 08.05)
1. **Original (CC):** OSRM primary + HERE jako "traffic-aware enhancement" na borderline cases (trigger logic, shortlist boost)
2. **Adrian pivot #1:** A/B Telegram side-by-side — oba silniki proponują kuriera, Adrian wybiera ręcznie button per propozycja, 3rd button "❌ ŻADEN" + visual map z 2 polylines
3. **Adrian pivot #2 (FINAL):** **shadow integration + archival ground truth analysis** — oba silniki liczą ETA passive w shadow, Telegram UX bez zmian, post-fact analiza z panel API `czas_doreczenia` decyduje który silnik lepszy + LGBM dostaje `here_eta_min` jako feature

Pivot #2 eliminuje: map renderer, sendPhoto migration, 3-button A/B markup, polyline conversion, visual ACK iteration, ~250 LOC dual_scoring, ~200 LOC telegram extension. **Phase 2 redukuje się z 3-4 dni do ~1-1.5 dnia.**

### Memory
- `feedback_rules.md` ma nową TOP-level rule: "AIDER zawsze odpalam ja sam via Bash, NIGDY copy-paste do Adriana" (Adrian explicit directive 2026-05-08)
- Workspace/CLAUDE.md `## TOOL LIMITATION` to legacy text — proponuję update gdy okazja

---

## 2. Aktualny stan branchu (08.05 22:00 UTC)

- Branch: `sprint-07-05-event-bus-opcja-c`, **+65 commits ahead** `master@10c754d`
- Master merge gate: **10.05** (PRE_MERGE_CHECKLIST_2026-05-10.md gotowy)
- HERE PoC code: w `eod_drafts/2026-05-08/here_poc/` — **NIE** część branchu produkcyjnego (eod_drafts to draft area, nie merged do master typically)
- Zero zmian w produkcyjnym dispatch_v2 kodzie z tytułu HERE — wszystko Phase 0 standalone

---

## 3. Decision tree po GATE A (start nowej sesji)

GATE A criteria (analyze.py output po 80 calls 09.05):
- **peak |delta_static| median ≥ 2.0 min** (HERE wykrywa NIE-trywialny ruch w peak vs OSRM × Adrian's static 2.8x mult)
- **AND lat_here p95 ≤ 400 ms** (latency feasible dla shadow integration w pipeline budget)
- **AND free tier OK** (extrapolacja z 80 calls/dzień × 365 = 30k/rok << 250k/mc, even przy 10× scale)

### Outcome A: PROCEED → Phase 1 here_client.py post-merge

Conditions met. Approach plan zgodnie z sekcją 4-7 niżej.

### Outcome B: STOP → write postmortem

HERE nie pokazuje przewagi w Białymstoku z analytical metric. Action items:
1. Write postmortem `dispatch_v2/eod_drafts/2026-05-09/here_postmortem.md` — wnioski dlaczego, contexts to revisit (Warsaw expansion w Q3+, calibracja `traffic.py` z sla_log.jsonl jako alternative quick-win)
2. Disable atq jobs jutrzejsze (jeśli pozostały), cleanup PoC dir
3. Update tasks #4-#7 → status `deleted` (wszystkie blocked by GATE A)
4. Free `HERE_API_KEY` z `.env` opcjonalnie (Adrian decyduje keep dla future Warsaw)

---

## 4. Phase 1 — `here_client.py` minimal (post-merge 11-12.05)

### 4.1 File breakdown

| File | LOC | Co |
|---|---|---|
| `dispatch_v2/here_client.py` (NEW) | ~180 | Mirror `osrm_client.py` 1:1: route_with_traffic + cache + circuit breaker + 3-warstwowy degraded mode |
| `dispatch_v2/common.py` (extend) | ~10 | Flagi: `ENABLE_HERE_SHADOW`, `HERE_API_TIMEOUT_S`, `HERE_CACHE_TTL_S`, `HERE_CIRCUIT_BREAKER_*`, `HERE_SHADOW_SHORTLIST_LIMIT` |
| `dispatch_v2/tests/test_here_client.py` (NEW) | ~250 | 15-20 tests: state machine, alerts, cache, circuit breaker, format conversion, defense-in-depth |

### 4.2 Mirror `osrm_client.py` patterns 1:1

Skopiuj strukturę z `osrm_client.py` linie 35-498. Zmiany pod HERE:

```python
HERE_BASE = "https://router.hereapi.com/v8/routes"
HERE_API_KEY = os.environ.get("HERE_API_KEY")  # albo C.flag-based, sprawdz pattern
CACHE_TTL_SECONDS = 5 * 60  # 5 min (NIE 60 jak OSRM — HERE traffic refresh ~5 min)
CACHE_MAX_SIZE = 5000

# === MP-#13 3-warstwowy degraded mode (mirror osrm_client) ===
_here_failures = 0
_here_circuit_open_until = 0.0
_here_last_success_ts = None
_here_degraded_since = None
_here_degraded_alert_sent = False
_here_recovery_alert_sent = False
_module_lock = threading.RLock()

CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_COOLDOWN_S = 60
```

Funkcje (mirror nazewnictwo z `osrm_client`):
- `_here_is_circuit_open()` — RLock read
- `_here_record_failure()` — increment, open circuit, MP-#13 L1+L2 alert entry
- `_here_record_success()` — reset, close circuit, MP-#13 L2 recovery alert
- `_mp13_send_alert_safe(msg)` — defense-in-depth Telegram (try/except, log warning na fail, NIE crash)
- `is_degraded()` / `degraded_since_ts()` / `cache_age_s()` — public accessors dla pipeline propagation
- `_cache_get()` / `_cache_set()` — RLock-protected cache (mirror OSRM)
- `route_with_traffic(from_ll, to_ll)` — main entry, returns dict z `duration_traffic_s`, `base_duration_s`, `length_m`, `traffic_overhead_min`, `here_fallback`
- `_haversine_fallback()` — degraded mode fallback (reuse `dispatch_v2.osrm_client.haversine`)

### 4.3 Tests (15-20, mirror `tests/test_osrm_client_*.py` + MP-#13 19 tests pattern)

| Test category | Count | Co |
|---|---|---|
| State machine (degraded entry/exit) | 5 | failure→circuit open, success→recovery, dedup alerts, flapping protection |
| Cache behavior | 3 | TTL respect, LRU eviction, RLock concurrent safety |
| Circuit breaker | 3 | threshold trigger, cooldown respect, fallback path |
| HTTP error handling | 3 | timeout, 401, 5xx — graceful degradation |
| Defense-in-depth | 2 | Telegram fail NIE crashnie route_with_traffic; missing API key fail-loud |
| Format parsing | 2 | HERE v8 summary fields validation, edge cases (no_routes, malformed) |

### 4.4 Workflow steps (per dispatch_v2/CLAUDE.md hard rules)

1. `cp dispatch_v2/here_client.py dispatch_v2/here_client.py.bak-pre-phase1-2026-05-11` (NIE — file nie istnieje, skip backup)
2. Create `here_client.py` via Write tool (180 LOC) — bo SELF, AIDER fail risk per Lekcja #91 + 08.05 evening AIDER timeout
3. `py_compile` + import check: `python -c "from dispatch_v2 import here_client; print(here_client.route_with_traffic.__name__)"`
4. Create `tests/test_here_client.py` (250 LOC) — można AIDER (post-Lekcja #97 pre-flight + scope ≤200 LOC w 1 file). Lub SELF jeśli AIDER risk preferowany.
5. Run testy: `cd dispatch_v2 && /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/test_here_client.py -v` — 15-20/15-20 PASS required
6. Add flags do `common.py` (cp .bak first)
7. Smoke E2E: `python -c "from dispatch_v2.here_client import route_with_traffic; r = route_with_traffic((53.1325,23.1688), (53.1158,23.1611)); print(r)"` — verify real HERE call works (consumes 1 free-tier call)
8. **NIE restart żadnego serwisu** — Phase 1 to dormant code, nikt go jeszcze nie używa
9. Commit + tag: `git add dispatch_v2/here_client.py dispatch_v2/tests/test_here_client.py dispatch_v2/common.py && git commit -m "Phase 1: here_client.py minimal (HERE Routing v8 + circuit breaker + 3-layer degraded mode mirror MP-#13)"`
10. Tag: `git tag here-phase-1-here-client-2026-05-11`
11. Update task #4 → `completed`, task #5 → `in_progress`

### 4.5 Estimated effort

- Code: 1.5h (mirror pattern, dobrze znany szablon)
- Tests: 1.5h (15-20 testów per pattern test_v327_*)
- Smoke + commit + tag: 0.5h
- **Total Phase 1: ~3.5h** (1 sesja CC)

---

## 5. Phase 2 — shadow integration (post-merge 12-13.05)

### 5.1 Integration point: `dispatch_pipeline.assess_order`

Lokalizacja: `dispatch_v2/dispatch_pipeline.py`, funkcja `assess_order` (~line 800-1500, sprawdz git blame). Wzór do mirror'owania: `_classify_and_set_auto_route` (Faza 7-AUTO-PROXIMITY) + `_apply_traffic_multiplier` (V3.26).

Logika:
```python
# w assess_order, po wybraniu shortlist top-K kandydatów (~line 1100):
if C.flag("ENABLE_HERE_SHADOW", default=False):
    _here_shadow_enrich_candidates(top_k_candidates, order, max_calls=C.flag("HERE_SHADOW_SHORTLIST_LIMIT", default=3))

def _here_shadow_enrich_candidates(candidates, order, max_calls):
    """Per top-N candidates run HERE.route_with_traffic parallel (max_calls cap).
    Defense: każdy call try/except, fail → skip enrichment dla tego candidate, NIE crash pipeline.
    Latency: ThreadPoolExecutor parallel = max(HERE_p95) ~400ms, akceptowalne post-shortlist.
    """
    from dispatch_v2 import here_client
    from concurrent.futures import ThreadPoolExecutor
    pickup_ll = order.get("pickup_coords")
    if pickup_ll is None: return  # defense gate L1 — firmowe konto fallback already handles
    with ThreadPoolExecutor(max_workers=max_calls) as pool:
        futures = {pool.submit(_here_call_safe, c, pickup_ll, order.get("delivery_coords")): c for c in candidates[:max_calls]}
        for f, c in futures.items():
            try:
                here_result = f.result(timeout=5.0)
                if here_result and "duration_traffic_s" in here_result:
                    c.decision_meta["here_eta_min"] = here_result["duration_traffic_s"] / 60
                    c.decision_meta["here_overhead_min"] = (here_result["duration_traffic_s"] - here_result.get("base_duration_s", here_result["duration_traffic_s"])) / 60
                    c.decision_meta["here_distance_m"] = here_result["length_m"]
                    c.decision_meta["here_fallback"] = here_result.get("here_fallback", False)
            except Exception as e:
                c.decision_meta["here_error"] = type(e).__name__
                # NIE re-raise, pipeline zostaje OSRM-only dla tego candidate

def _here_call_safe(candidate, pickup_ll, delivery_ll, pickup_ready_at, order_created_at):
    """Wrapper z full defense, mirror _mp13_send_alert_safe pattern.

    KRYTYCZNE (Phase 3 panel pairing requirement, Adrian directive #5):
    Returns TOTAL predicted ETA (order_created_at → delivered_at), NIE tylko leg drive sum.
    Comparable bezpośrednio z panel czas_doreczenia w Phase 3.

    Components:
      - leg1: courier_position → pickup (HERE traffic-aware drive time)
      - dwell_pickup: 2 min (DWELL_PICKUP_MIN per route_simulator_v2)
      - prep_wait: max(0, pickup_ready_at - (now + leg1_drive)) — gdy kurier dojeżdża zbyt wcześnie
      - leg2: pickup → delivery (HERE traffic-aware drive time)
      - dwell_delivery: 2 min (DWELL_DROPOFF_MIN per V3.27.3 rollback)
    """
    from dispatch_v2 import here_client
    from dispatch_v2.common import DWELL_PICKUP_MIN, DWELL_DROPOFF_MIN  # 2.0 + 2.0 baked-in
    if pickup_ll is None or delivery_ll is None: return None
    if here_client.is_degraded(): return None  # skip gdy circuit open
    try:
        leg1 = here_client.route_with_traffic(candidate.position_ll, pickup_ll)
        leg2 = here_client.route_with_traffic(pickup_ll, delivery_ll)
        # Total ETA computation (Phase 3 ground truth comparable)
        leg1_min = leg1["duration_traffic_s"] / 60.0
        leg2_min = leg2["duration_traffic_s"] / 60.0
        # Prep wait: gdy courier arrives przed pickup_ready_at (np. czasówka)
        arrival_pickup_ts = (datetime.now(timezone.utc).timestamp()
                              + leg1_min * 60.0)
        prep_wait_min = max(0.0, (pickup_ready_at - arrival_pickup_ts) / 60.0)
        total_eta_min = leg1_min + DWELL_PICKUP_MIN + prep_wait_min + leg2_min + DWELL_DROPOFF_MIN
        return {
            "duration_traffic_s": leg1["duration_traffic_s"] + leg2["duration_traffic_s"],  # drive only (legacy)
            "base_duration_s": leg1["base_duration_s"] + leg2["base_duration_s"],
            "length_m": leg1["length_m"] + leg2["length_m"],
            "total_eta_min": total_eta_min,  # NEW Phase 3 critical — pełen lifecycle
            "leg1_drive_min": leg1_min,
            "leg2_drive_min": leg2_min,
            "prep_wait_min": prep_wait_min,
            "dwell_total_min": DWELL_PICKUP_MIN + DWELL_DROPOFF_MIN,
            "here_fallback": leg1.get("here_fallback", False) or leg2.get("here_fallback", False),
        }
    except Exception:
        return None
```

**Analogiczne `_osrm_total_eta`** dla OSRM-side (replicate above pattern z `osrm_client.route()` zamiast `here_client.route_with_traffic()`). Plus consistent Phase 2 dorzuca **dwa nowe pola** do `decision_meta`: `osrm_total_eta_min` + `here_total_eta_min`. Phase 3 czyta TYLKO te dwa pola dla pairing analysis.

**Note:** pipeline już liczy something podobne w `_v327_eval_courier` lub `simulate_bag_route_v2` (predicted delivery time per candidate). Phase 2 może EITHER (a) replicate logic z explicit ETA helper, EITHER (b) extend istniejący `route_simulator_v2.simulate_bag_route_v2` żeby zwracał `total_eta_min` z explicit silnik field. Audit przy starcie sesji Phase 2 zdecyduje który approach mniej-LOC.

### 5.2 `decision_meta` schema additions (per candidate)

```json
{
  "here_eta_min": 14.3,           // total HERE traffic time courier→pickup→delivery (minutes)
  "here_overhead_min": 0.85,      // HERE traffic - HERE freeflow (minutes, 0 jeśli no traffic detected)
  "here_distance_m": 2913,        // total length meters HERE-suggested route
  "here_fallback": false,         // true gdy HERE haversine fallback fired (degraded)
  "here_error": null              // exception type name jeśli call failed (rare, defense)
}
```

Field `osrm_eta_min` powinno być **już** logowane (sprawdz current `decision_meta` schema w `shadow_dispatcher._serialize_result`). Jeśli nie — rozszerz `decision_meta` o `osrm_eta_min`, `osrm_traffic_mult`, `osrm_distance_m` w tym samym sprintcie żeby jednolite formaty.

### 5.3 Flag `ENABLE_HERE_SHADOW`

Hot-reload via `flags.json` (jak `AUTO_PROXIMITY_SHADOW_ONLY` per Faza 7 Etap 0). Default **False**:

```json
{
  "ENABLE_HERE_SHADOW": false,
  "HERE_SHADOW_SHORTLIST_LIMIT": 3,
  "HERE_API_TIMEOUT_S": 5.0
}
```

Po deploy code → flip flag True via `flags_admin.py set ENABLE_HERE_SHADOW true` (MP-#6 hot-reload pattern, zero restart wymagany).

### 5.4 Defense-in-depth (defensive layers)

1. **Circuit breaker** (`here_client._here_is_circuit_open()`) — skip HTTP gdy 3 kolejne fails w 60s
2. **Per-call timeout** 5s — gdyby HERE wieszał, ThreadPoolExecutor.result(timeout=5) przerywa
3. **Per-candidate try/except** — fail jednego candidate ≠ crash całego shortlist
4. **Coords validation** — pickup_ll/delivery_ll None → skip enrichment (firmowe konto edge case)
5. **Defense gate L1** w `here_client.haversine_fallback` — sentinel (0,0) detection mirror osrm_client (Lekcja #81)
6. **Async send_admin_alert** — Telegram unreachable → log warning, NIE crash (MP-#13 L2 mirror)
7. **Fallback gracefully** — gdy all defense layers fail, candidate dostaje `here_error="<exception_type>"` w decision_meta i pipeline kontynuuje OSRM-only

### 5.5 Tests (10-12 nowych)

- `test_here_shadow_integration.py`:
  - 3 tests: shortlist enrichment happy path (3 candidates × HERE call success → decision_meta populated)
  - 2 tests: HERE circuit open → decision_meta has `here_error="circuit_open"` lub field absent
  - 2 tests: per-candidate timeout → fail jednego candidate NIE crash innych
  - 2 tests: flag OFF → zero HERE calls (regression on V3.27 latency budget)
  - 2 tests: pickup_coords=None → skip enrichment (firmowe konto path)
  - 1 test: schema validation `decision_meta` zawiera wszystkie 5 fields gdy success

### 5.6 Deploy + restart

- Restart **dispatch-shadow** (pickup nowy `here_client` import + integration code) — graceful, ~7-10s downtime
- **NIE restart dispatch-telegram** (UX unchanged, brak zmian w `telegram_approver.py` w Phase 2)
- Smoke 30 min: monitor `journalctl -u dispatch-shadow -f | grep -E "here_eta_min|here_error"` — verify enrichment fires na real propozycji
- Flip flag True via `flags_admin.py` po smoke OK
- Update task #5 → `completed`, task #6 → `in_progress`

### 5.7 Estimated effort

- Code (`dispatch_pipeline.py` integration + helpers): 2h
- Tests: 1.5h
- Restart + smoke + flag flip: 1h
- **Total Phase 2: ~4.5h** (1 sesja CC, możliwe zamknąć w 1 dzień)

---

## 6. Phase 3 — panel pairing ETA accuracy analysis (REVISED post Adrian directive 2026-05-08 #5)

### 6.0 Co Adrian zlecił (chronologicznie)

**Directive #4 (multi-dim breakdown — STRUCTURE):**
> "porównać wśród aktywnych kurierów, którzy mają włączone GPS-y, z tym, jaki mają przypisany tier, z tym, jaki jest dzień tygodnia i godzina. Sprawdź w historii, co powinien wybrać. Szczególnie najważniejsze są piki."

**Directive #5 (panel pairing — METRIC):**
> "może sparować dane z panelu z propozycjami?"

**Synthesis:** zachowaj multi-dim breakdown (4 wymiary) jako *strukturę raportu*, ale zmień metric z "Top-1 match rate vs Adrian's tap" (proxy ground truth) na **`predicted_eta - actual_delivered_min`** (real ground truth z panelu). Eliminuje to (a) potrzebę reconstruction fleet snapshot i (b) Adrian-policy bias (Adrian klikał bez wiedzy HERE).

**Co dokładnie mierzymy:**
- Per delivered order: `predicted_total_eta_min` per silnik vs `actual_delivered_at - order_created_at`
- ETA error per silnik = |predicted - actual|
- Per-cell aggregation w 4-dim crosstab → który silnik trafia bliżej rzeczywistego dostarczenia

**4 dimensions reporting structure (Adrian #4):**
- `pos_source` ∈ {gps_active, pre_shift, no_gps, synthetic} — **PRIMARY filter = gps_active**
- `tier` ∈ {gold, std+, std, new}
- `weekday` ∈ {Mon-Sun} (weekend split per V3.27)
- `hour_bucket` ∈ {peak (weekday 15-17), shoulder, offpeak} — **focus PEAK**

### 6.1 Trigger: po ≥7 dni Phase 2 shadow data + delivered_at dostępne

`shadow_log.jsonl` zawiera N≥2000 row z `here_eta_min` + `osrm_eta_min` + `pos_source` + `tier` per candidate (Phase 2 schema enriched). Panel ma `delivered_at` per oid (już istnieje, V3.x R-DECLARED-TIME enforcement).

**Filter clean signal**: tylko orderów gdzie `proposed_kurier == accepted_kurier == delivered_kurier` (Adrian's AKCEPT path, nie INNY/KOORD interventions). To ~70-80% propozycji per Adrian's accept rate. Dla nich `actual_delivered_at` jest signal dla *wybranego* kuriera, czyli direct comparable z `predicted_eta_for_that_kurier`.

### 6.2 File: `dispatch_v2/here_shadow_replay.py` (NEW, ~200 LOC — radykalnie prostsze niż policy replay)

**Brak fleet snapshot reconstruction. Brak counterfactual scoring re-run. Tylko join + aggregation.**

Logika:
```python
def replay_period(start_ts, end_ts, output_report_path):
    """Panel pairing ETA accuracy analysis ostatnie N dni.

    1. Load shadow_log.jsonl rows (period) — proposed candidate per propozycja
       z osrm_eta_min + here_eta_min + pos_source + tier per candidate
    2. Filter: clean signal (proposed == accepted == delivered)
    3. Join z panel data lub sla_log.jsonl per oid → delivered_at
    4. Compute errors:
         actual_total_eta_min = (delivered_at - order_created_at) / 60
         osrm_error = predicted_OSRM_eta_min - actual_total_eta_min
         here_error = predicted_HERE_eta_min - actual_total_eta_min
    5. Bucket per (pos_source × tier × weekday × hour_bucket) cell
    6. Per cell: median |osrm_error|, median |here_error|, winner, sample size
    7. Output: peak heatmap + multi-dim breakdown + Phase 4 decision rules
    """
    rows = _load_shadow_log(start_ts, end_ts)
    clean = _filter_clean_signal(rows)  # proposed == accepted == delivered
    enriched = _join_panel_actuals(clean)  # delivered_at per oid
    cells = _multi_dim_aggregate(enriched)
    _write_report(cells, output_report_path)

def _load_shadow_log(start_ts, end_ts):
    """Read shadow_log.jsonl (rolling files), filter ts range, parse rows."""

def _filter_clean_signal(rows):
    """Keep tylko gdy proposed_kurier_cid == accepted_cid == delivered_cid.
    Eliminuje INNY/KOORD interventions które confounded by manual override.
    Zostają orderów gdzie Adrian's AKCEPT path zadziałał end-to-end (~70-80%).
    """

def _join_panel_actuals(rows):
    """Per oid: fetch delivered_at z panel_client.fetch_details (cached)
    LUB z sla_log.jsonl jeśli ma delivered_at field (verify schema).
    Drop rows bez delivered_at (np. orderów anulowanych).
    """

def _multi_dim_aggregate(enriched):
    """4-dim aggregation per (pos_source × tier × weekday × hour_bucket):
       {n, median_abs_osrm_error, median_abs_here_error, p95_osrm_error,
        p95_here_error, winner, here_advantage_pct}
    Where here_advantage_pct = (median_abs_osrm - median_abs_here) / median_abs_osrm × 100
    """

def _write_report(cells, path):
    """ASCII: peak heatmap (weekday × hour) + per-tier breakdown +
    Phase 4 LGBM v1.2 decision rules + caveat section."""
```

### 6.3 Panel API join (główny join, NIE pomocniczy)

**Sposoby uzyskania `delivered_at` per oid (preferred order):**

1. **`sla_log.jsonl`** — jeśli zawiera `delivered_at` field per row (verify schema na real entries). Najtaniej, no API calls. Per memory `sla_tracker` służy do tracking R6 BAG_TIME alerty — pewnie ma delivered_at.
2. **`panel_client.fetch_details(oid)`** — istniejący API call, returns `czas_doreczenia` (z `dispatch_v2/CLAUDE.md` Panel API reference). Batch 50/req, rate limit przyjazny. Cost: ~2k calls dla 7-day analysis = ~5-10 min total.
3. **`panel_html_parser`** zindexowane snapshoty — jeśli persistowane historycznie.

Decyzja: try (1) `sla_log` first (cheapest), fallback (2) `panel_client` jeśli schema brakuje. Audit przy starcie sesji Phase 3.

### 6.4 Output report — peak heatmap (priorytet)

```
HERE vs OSRM Panel Pairing ETA Accuracy Analysis
Period: 2026-05-12 → 2026-06-11 (30 days, N=6840 delivered orders, gps_active filter)

=== PEAK WINDOW (weekday 15-17, N=1683 delivered orders) — PRIORITY per Adrian directive ===

By tier × hour — HERE_advantage_pct ((|osrm_err| - |here_err|) / |osrm_err| × 100):
                15:00    16:00    17:00    avg
gold            +18%     +24%     +15%     +19%   ← HERE trafia ~20% bliżej delivered
std+            +14%     +21%     +18%     +18%
std             +9%      +12%     +10%     +10%
new             +3%      +5%      +4%      +4%    ← noise floor (low N)

By weekday × hour heatmap (HERE_advantage_pct, gold tier only):
            Mon   Tue   Wed   Thu   Fri   Sat   Sun
15:00       +12%  +15%  +18%  +21%  +35%  +3%   +1%   ← Fri peak największy efekt
16:00       +9%   +18%  +21%  +24%  +33%  +1%   -2%
17:00       +6%   +12%  +15%  +18%  +28%  -1%   -1%

=== ETA error magnitudes (gold tier × peak weekday) ===

Hour    n     |osrm_err| med   |osrm_err| p95   |here_err| med   |here_err| p95
15:00   312   3.8 min          9.4 min          3.0 min          7.1 min
16:00   478   4.2 min          10.8 min         3.2 min          7.6 min
17:00   421   4.1 min          11.2 min         3.4 min          8.3 min
              ↑ OSRM × static  ↑ HERE realtime  HERE traffic-aware

Interpretation: HERE średnio 1 min bliżej w peak gold, p95 ~3 min lepszy.

=== FULL DAY BREAKDOWN (gps_active, all tiers) ===

bucket    n     |osrm_err| med   |here_err| med   here_advantage_pct  WINNER
peak      1683  4.0 min          3.2 min          +20%                HERE
shoulder  3127  2.4 min          2.2 min          +8%                 push
offpeak   2030  1.4 min          1.5 min          -7%                 OSRM (cheaper, no benefit)

=== PHASE 4 LGBM v1.2 DECISION RULES (auto-derived) ===

Cell rules (where N>100, |here_advantage_pct|>15%):
  HERE_eta dominant feature: weekday peak × {gold, std+} × gps_active (advantage 18-35%)
  OSRM_eta dominant feature: offpeak × all tiers (advantage HERE -7% = HERE worse)
  Neutral cells: shoulder (advantage 8% = below threshold for retrain ROI)

LGBM v1.2 retrain trigger: TAK (peak weekday gold/std+ shows >15% HERE advantage)

CONCLUSION:
- HERE precyzyjniej trafia delivered_at w peak weekday × top-tier (gold/std+) — ~20% bliżej
- OSRM × static_mult dobrze działa offpeak (HERE bez przewagi)
- LGBM v1.2 should learn auto-switching per (tier × peak_indicator) cell
- Cost-benefit: HERE used dla ~30% propozycji (peak weekday gold/std+) → free tier OK

CAVEAT (sekcja 6.7): mierzymy ETA accuracy, NIE decision quality bezpośrednio.
Pełen decision-quality benchmark = LGBM v1.2 vs v1.1 NDCG@5 (Phase 4).
```

### 6.5 Telegram rollup (ostatni krok)

Skrypt wysyła do Adriana 8765130486 raport (mirror Phase 0 GATE A self-notify pattern z `here_poc_gate_a_report.sh`). Trigger:
- Manual: `python -m dispatch_v2.here_shadow_replay --period 7d --report telegram`
- Cron weekly Mon 06:00 UTC (rolling 7-day window): `at` job lub systemd timer (mirror `dispatch-cod-weekly.timer` pattern)

### 6.6 Schema implications dla Phase 2 (verify or extend)

Phase 3 panel pairing wymaga `decision_meta` per candidate containing:

| Field | Phase 2 dorzuca | Verify w shadow_log obecnie | Source |
|---|---|---|---|
| `osrm_eta_min` (PEŁEN total ETA: courier→pickup + dwell_pickup + pickup→delivery + dwell_delivery) | **Phase 2 dorzuca jako TOTAL, NIE leg-sum** | Częściowo (osrm_raw_duration_s istnieje per leg) | osrm_client.route() × 2 legs + dwell stałe |
| `here_eta_min` (analogicznie pełen total) | Phase 2 dorzuca (NEW, full total) | NIE | here_client × 2 legs + dwell stałe |
| `here_overhead_min`, `here_distance_m`, `here_fallback` | Phase 2 dorzuca | NIE | here_client output |
| `pos_source` (gps_active/pre_shift/no_gps/synthetic) | Verify, dodaj jeśli brak | Faza 7 logu już to ma per memory (`auto_route_context`) | fleet_snapshot per candidate |
| `tier` (gold/std+/std/new) | Verify, dodaj jeśli brak | Pewnie (Faza 7 + LGBM scoring używa tier) | courier_ranking.py |
| `proposed_kurier_cid` | Verify (pewnie istnieje, V3.x logi) | TAK | shadow_dispatcher decision |
| `accepted_kurier_cid` | W `learning_log` (Adrian's tap result) | Verify | learning_log join per oid |
| `delivered_kurier_cid` | W panel `czas_doreczenia` row | Fetch via panel_client lub sla_log | Phase 3 join |
| `delivered_at` | W panel | Fetch via panel_client lub sla_log | Phase 3 join |
| `order_created_at` (= `ts_utc` row) | Already in shadow_log | TAK | order_event |

**Action item dla Phase 2 sesji:** 
1. **WAŻNE:** `osrm_eta_min` + `here_eta_min` muszą być TOTAL (cały lifecycle order_ts → delivered_at), NIE tylko sum dwóch leg routes. Dorzucić dwell_pickup + dwell_delivery (2 min standardowo per `route_simulator_v2`).
2. Verify pos_source + tier w real `shadow_log.jsonl` entries — dodaj brakujące pola w `_serialize_candidate` w `shadow_dispatcher.py` jeśli któryś z dim filters missing.

### 6.7 Caveat — co panel pairing NIE mierzy

Mierzymy **ETA prediction accuracy** (`predicted - actual`), NIE **decision quality** (czy silnik wybiera lepszego kuriera). Argument indirect: silnik z lepszą ETA accuracy → scoring lepiej rangujące → lepsze decyzje. Ale NIE identyczne.

Pełen "decision quality" wymagałby counterfactual ("co byłoby gdyby kurier B został wybrany zamiast A") — a tego z panel data NIE wyciągniemy bez fleet simulation. **Zostawiamy ten benchmark dla Phase 4 LGBM v1.2** które naturalnie odkryje "kiedy silnik X jest lepszy" przez feature importance + per-cell NDCG@5 breakdown bez explicit counterfactual.

Plus secondary measurement issue: filter `proposed == accepted == delivered` eliminuje ~20-30% propozycji (INNY/KOORD path). To może bias dane (np. peak gdzie HERE wskazuje innego niż OSRM = Adrian klika INNY → ten record dropped). **Mitigation:** w raporcie pokażemy oba — pełen sample (incl. INNY/KOORD where actual_delivered known) + clean sample (proposed == accepted == delivered) — rozumiemy bias.

### 6.8 Estimated effort

- Code (`here_shadow_replay.py` panel pairing + multi-dim aggregate): 2.5h (NIE 4h jak prev — eliminuje state reconstruction + counterfactual scoring re-run)
- Schema verification + Phase 2 patch (osrm_eta_min as TOTAL not leg-sum): 1.5h
- Tests (6-8): 1h
- Cron + Telegram rollup: 0.5h
- First real run + report verification: 1h
- **Total Phase 3: ~6.5h** (1-2 sesje CC, prawdopodobnie 1 dzień)

---

## 7. Phase 4 — LGBM v1.2 vs v1.1 (REVISED post Adrian directive 2026-05-08 #4)

### 7.0 Co Adrian zlecił

Cytat: "Później będziemy wybierać między dwoma modelami, które tam były wcześniej trenowane, a później to się nazywa Mapy."

**Decyzja: 2 modele LGBM trained na różnych zestawach features ("dwie mapy silników"):**
- **v1.1** (current shadow per memory) — features OSRM-only baseline
- **v1.2** (NEW Phase 4) — features OSRM + HERE (`osrm_eta_min`, `here_eta_min`, `here_overhead_min`, plus interaction terms per Phase 3 dim cells)

### 7.1 Trigger: po Phase 3 raport pokazuje że HERE win margin >5pp w peak cells z N>100

Z mock heatmap section 6.4: peak weekday × gold/std+ × gps_active wykazuje +5-12pp margin → **PROCEED Phase 4 retrain v1.2**.

Jeśli Phase 3 pokaże <3pp margin we wszystkich cells → **DEFER Phase 4** (HERE shadow zostaje na "watching brief", nie warto retrain LGBM dla minimal gain).

### 7.2 Train-eval workflow

1. **Training set**: N≥10k samples z Phase 3 enriched data (sla_log + shadow_log + learning_log joined). Adrian-policy = label, OSRM+HERE features.
2. **Hold-out validation**: 20% (N≥2k). Compare v1.1 vs v1.2:
   - **NDCG@5** — primary metric (per memory: v1.0 NDCG@5=0.852, pa=88.45%)
   - **Per-cell breakdown**: v1.1 vs v1.2 NDCG@5 per (peak/shoulder/offpeak × gold/std+/std/new) — 12 cells
   - **Feature importance gain**: czy `here_eta_min` jest top-10 feature?
3. **Per-dim winning model selection** (advanced ensemble):
   - Cell gdzie v1.2 wins by >2pp NDCG@5 → "v1.2 dla tej cell"
   - Cell gdzie v1.1 dominuje (margin <2pp lub negative) → "v1.1 stays"
   - Granular routing per (tier × hour) cell przez ensemble dispatcher

### 7.3 Shadow deploy v1.2 alongside v1.1 (Faza 6 v1.1 pattern)

Mirror current Faza 6 LGBM shadow:
- v1.2 logged per propozycja w `validation_gate_lgbm.predict()` shadow mode
- 14-day shadow → comparison metrics
- Flip flag jeśli per-dim wins potwierdzą się NIE-trywialnie (>2pp NDCG@5 w ≥3 z 12 cells)

### 7.4 Pre-req: ≥4 tygodnie shadow data (N≥10k samples z Adrian-policy labels)

Phase 2 deploy ~12-13.05 → 4 tygodnie = ~10.06.2026. Phase 3 raport ~21.05 (po 7 dni). Jeśli Phase 3 pokaże PROCEED → Phase 4 train w pierwszej połowie czerwca, shadow ramp koniec czerwca, decision flip lipiec.

### 7.5 Effort estimate

Bundle z Faza 6 v1.2 sprint:
- Data join Phase 3 → LGBM training feature set: 2h
- Retrain v1.2 + hyperparam tune (lub reuse v1.1 hp): 3h (depends on dataset size)
- Hold-out eval + per-cell breakdown: 2h
- Shadow deploy + 14-day collection: passywne
- Decision report + flip flag (lub defer): 2h
- **Total Phase 4: ~9h** active work (rozłożone ~2 tygodnie)

---

## 8. Hard rules + workflow (per dispatch_v2/CLAUDE.md)

### 8.1 Workflow per krok (każda zmiana)

```
backup (.bak-pre-<scope>-<date>) → edit → py_compile → import check → tests → commit → tag → smoke → ACK Adrian → restart (jeśli wymagany)
```

### 8.2 NIGDY (hard NO)

- ❌ Restart dispatch-telegram bez **explicit ACK Adrian w czacie** (current sesji)
- ❌ Restart dispatch-shadow bez py_compile + import check + Adrian ACK (Phase 2 wymaga restart, MUST get ACK)
- ❌ `--no-verify` w git commit (skip hooks)
- ❌ `git reset --hard` bez confirmation
- ❌ AIDER copy-paste prompt do Adriana (per nowa feedback rule 08.05) — **odpalam sam via Bash**
- ❌ jq (nie zainstalowany na serwerze) — Python json zamiast
- ❌ `sed -i` dla edit (read-only OK) — Edit tool zamiast
- ❌ heredoc z `"` — single quotes lub `\"` escape
- ❌ Telegram leak z testów — `PYTEST_CURRENT_TEST` env check w `telegram_utils.send_admin_alert` (Lekcja #75)

### 8.3 ZAWSZE (hard YES)

- ✅ Pre-flight AIDER per Lekcja #97 przed pierwszym AIDER call w sesji
- ✅ `cp .bak-pre-<scope>-<date>` przed pierwszą edycją
- ✅ Atomic writes (temp + fsync + rename) dla shared state files
- ✅ Granular git tags per faza (`here-phase-N-<topic>-2026-05-DD`)
- ✅ Telegram restart ACK = explicit "tak restartuj" w chat sesji
- ✅ Empirical-first design (Lekcja #33+#82) — pre-implementation grep + read przed pisaniem
- ✅ Defense-in-depth: 2+ warstw obrony per silent killer (Lekcja #75 pattern)
- ✅ `.bak` pliki zostają ≥24h post-deploy (rollback safety)
- ✅ git log check przed commit (Lekcja #84 parallel CC collision protection)

### 8.4 Master merge gate (10.05)

**Status:** branch +65 commits ahead jak na 08.05. Phase 1+2 dorzucają ~6 commits. Pre-merge checklist `dispatch_v2/PRE_MERGE_CHECKLIST_2026-05-10.md` musi być wykonany **PRZED** rozpoczęciem Phase 1. Phase 1+ dzieje się na **POST-MERGE** branch (świeży master + Phase 1 commits).

Jeśli sesja startuje 09.05 po południu/wieczór: master merge wykonany Adrianem przed sesją CC. Phase 1 startuje na clean master + new feature branch `phase-1-here-client`.

---

## 9. Risks + mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| HERE rate limit unexpected (FREE tier inadequate) | Phase 2 disable | `here_client._here_record_failure` + alert wired do Telegram (MP-#13 mirror); `HERE_SHADOW_SHORTLIST_LIMIT` cap default 3 (NIE 10) |
| HERE API contract drift (v8 schema change) | parser break | Fail-loud format validation w `route_with_traffic`; defensive fallback to OSRM-only enrichment |
| Pipeline latency regression | Telegram propozycja delays | Phase 2 ThreadPoolExecutor parallel + max(HERE_p95)=400ms post-shortlist; flag OFF default; rollback hot-reload |
| `decision_meta` schema break (downstream consumers) | shadow_dispatcher / learning_analyzer crash | Verify `_serialize_result` schema BEFORE deploy; backwards-compat: nowe pola optional `dict.get()` reads |
| Adrian's session bandwidth — Phase 1+2 wymaga 7-8h pracy | Sesja przerwana mid-phase | Per-faza ACK gates + .bak files + git tags = każdy krok rollback-able; Phase 1 first (lower risk), Phase 2 separate session |
| AIDER fail (per 08.05 timeout pattern) | manual SELF write | Pre-flight diagnostic Lekcja #97 first; jeśli AIDER fail → SELF Write tool (Phase 1 ~180 LOC manageable) |
| Master merge nie wykonany 10.05 | Phase 1 blocked na branchu z +65 commits | Adrian's call: defer Phase 1 lub merge fast-track |
| Free tier HERE wyczerpany (large N propozycji w peakach) | Phase 2 fallback | `HERE_SHADOW_SHORTLIST_LIMIT` reducible flag; circuit breaker → degraded mode |

---

## 10. Tasks tracker (start sesji)

```
#1 [completed] Phase 0: HERE klucz verify + trips_sample.jsonl stub
#2 [completed] Phase 0: AIDER measure_delta.py + analyze.py
#3 [in_progress] Phase 0: PoC measurement run + GATE A review
        → 09.05 20:00 Warsaw Telegram raport
        → po raporcie status: completed (PROCEED) lub deleted (STOP)
#4 [pending, blocked by #3] Phase 1: here_client.py minimal
#5 [pending, blocked by #4] Phase 2: shadow integration w decision_meta
#6 [pending, blocked by #5] Phase 3: here_shadow_replay.py + 7-day archival
#7 [pending, blocked by #6] Phase 4: LGBM feature integration Q3 2026
```

---

## 11. Files to read for context (start sesji)

### Memory (auto-loaded via MEMORY.md)

- `feedback_rules.md` — TOP-level zasady, **AIDER auto-run rule** (08.05), patch workflow, safety, AIDER pre-flight
- `lessons.md` — #75 (Telegram leak 3-warstwowa obrona), #80-#83 (audit consumers, fail-loud sentinel, empirical fixture-first, late-binding architectural decision), #84 (visual ACK), #91 (Z2 supremacy override), #95 (AIDER token limit), #97 (AIDER pre-flight diagnostic), #98 (tail-read truncate)
- `tech_debt_backlog.md` — current sprint state + Master Plan TOP-15 LIVE

### Code (NIE auto-loaded, read on demand)

- `dispatch_v2/osrm_client.py` (497 LOC) — **PRIMARY MIRROR PATTERN** dla `here_client.py` Phase 1
- `dispatch_v2/dispatch_pipeline.py` `assess_order` (~line 800-1500) — integration point Phase 2
- `dispatch_v2/shadow_dispatcher.py` `_serialize_result` — `decision_meta` schema verification
- `dispatch_v2/common.py` — flag pattern (znajdź `ENABLE_V326_*`, `AUTO_PROXIMITY_*`), env override pattern
- `dispatch_v2/flags_admin.py` — hot-reload flag flip CLI (MP-#6 pattern)
- `dispatch_v2/eod_drafts/2026-05-08/here_poc/` — Phase 0 reference code (measure_delta.py + analyze.py + trips_sample.jsonl)

### Specs (precedensy)

- `dispatch_v2/eod_drafts/2026-05-06/faza_7_auto_proximity_design_spec.md` — Faza 7 design pattern (shadow mode + classifier + flag-gated)
- `dispatch_v2/PRE_MERGE_CHECKLIST_2026-05-10.md` — pre-merge gate procedure
- `dispatch_v2/AUDIT_2026-05-07/` — system architecture audit (jeśli scope expansion needed)

---

## 12. Adrian's directive trail (decision history)

**Sesja 08.05 wieczór** (chronologicznie):

1. **CC original recommendation (turn 1):** OSRM primary + HERE jako "traffic-aware enhancement" na borderline cases. Phase 2 = trigger logic + shortlist boost.
2. **Adrian: "wprowadzamy Twoją rekomendację"** → CC przygotował provisioning + Phase 0 PoC plan.
3. **Adrian: "mam klucz"** → CC verifikował + scheduled cron + stworzył `trips_sample.jsonl` stub.
4. **Adrian: "co robisz?"** → CC clarified status (czeka na AIDER copy-paste, ale Adrian explicit override następny).
5. **Adrian: "zapisz gdzieś na stałe w pamięci, że Ty sam go uruchamiasz zawsze!"** → CC zapisał feedback rule "AIDER auto-run via Bash" w `feedback_rules.md` jako TOP-level zasada. CC odpalił AIDER sam, AIDER timeout 300s, Z2 override → SELF Write.
6. **PoC stub 10/10 success** ale GATE A FAIL bo measurement w nocy. CC zaproponował 3 opcje, Adrian wybrał **B (cron 12-19 jutro)**.
7. **Adrian (turn n): "Czy po wdrożeniu tej hybrydy będziemy mogli porównać wyniki obu rozwiązań?"** → CC pivot do A/B Telegram side-by-side z 2 buttonami (OSRM/HERE). 
8. **Adrian: "trzeci przycisk ma być! że żaden nie jest dobry i trase jednego i drugiego przedstawić w przejżysty sposób"** → CC rozszerzył do 3 buttonów + map render PNG (HERE Map Image API + polyline overlay).
9. **Adrian (FINAL pivot): "a może jeszcze lepiej niech oba modele podają wartości trasy, a ziomek pobiera dane arhiwalne i sprawdza czas i który kurier został wybrany, zresztą tak samo przy nauce logiki?"** → CC accepted, eliminated map renderer + Telegram extension + 3-button markup, redukował Phase 2 do **shadow integration only** + Phase 3 archival replay + Phase 4 LGBM feature. **70% złożoności wycięte, lepsze dane (ground truth z panelu, nie Adrian's manual A/B clicks).**

**Pattern obserwowalny:** Adrian's terse pivot questions (~3 razy w 1 sesji) systematycznie eliminują over-engineering CC's pierwotnej rekomendacji. Domain knowledge + data-driven instinct trumps generic Warsaw-flavored architecture proposals. → Lessons archive candidate post Phase 3 results (gdy potwierdzone że approach był right).

---

## 13. Quick checklist start nowej sesji

```
[ ] read CLAUDE.md (workspace + dispatch_v2)
[ ] read MEMORY.md → project_overview, sprint_timeline, tech_debt_backlog
[ ] read this file (here_hybrid_next_session_plan.md)
[ ] check tasks: TaskList → status #1-#7
[ ] check Telegram: GATE A report 09.05 20:00 Warsaw odebrany?
[ ] decide: PROCEED Phase 1 lub STOP postmortem
[ ] git status + git log -5 (master merge 10.05 verify)
[ ] systemctl status dispatch-shadow dispatch-panel-watcher (current health)
[ ] grep for HERE_API_KEY w .env (still present)
[ ] aider preflight diagnostic (Lekcja #97, ~$0.0003)
[ ] start Phase 1 lub write postmortem
```

---

**KONIEC PLANU — sesja 08.05 wieczór close**
