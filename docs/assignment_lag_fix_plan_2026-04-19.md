# Assignment lag fix plan — 2026-04-19 (V3.15)

## Wybrana strategia: **Opcja B — panel_packs jako fallback trigger**

**Uzasadnienie:**
- Najbardziej minimalna zmiana. Pipeline (`courier_resolver`, `dispatch_pipeline`) pozostaje nietknięte. V3.13 + V3.14 zero ryzyka.
- Fix jest w **emitter** (panel_watcher) — naprawia źródło laga, nie adaptuje consumera do niedoskonałych danych.
- Panel_packs ground-truth per nick jest już parsowany, wystarczy konsumować.
- Rate-limited przez fetch_details budget — panel API nie przeciążony.

## Pliki do zmiany (2 core + 1 test)

1. **`common.py`** (+15 lines) — `ENABLE_PANEL_PACKS_FALLBACK=True` + `PACKS_FALLBACK_MAX_PER_CYCLE=10` env tunable
2. **`panel_watcher.py`** (+60 lines) — nowa sekcja consumer `parsed["courier_packs"]` po istniejących sekcjach ZMIANY/RECONCILE. Logic:
   - Reverse name→cid z `kurier_ids.json`
   - Dla każdego (nick, [oids]) w packs:
     - Resolve cid (ambiguous → skip + warn)
     - Dla każdego oid: sprawdź `orders_state[oid].courier_id`
     - Jeśli `cid_state != cid_packs` → fetch_details + emit COURIER_ASSIGNED + update_from_event
     - Budget limit per cycle
   - Log SOURCE_DIVERGENCE events (learning_log.jsonl) dla observability
3. **`tests/test_assignment_lag_fix.py`** (+300 lines) — 10+ testów per wymagania

## Feature flag

```python
ENABLE_PANEL_PACKS_FALLBACK = _os.environ.get("ENABLE_PANEL_PACKS_FALLBACK", "1") == "1"
try:
    PACKS_FALLBACK_MAX_PER_CYCLE = int(_os.environ.get("PACKS_FALLBACK_MAX_PER_CYCLE", "10"))
except (ValueError, TypeError):
    PACKS_FALLBACK_MAX_PER_CYCLE = 10
```

Default True, env `ENABLE_PANEL_PACKS_FALLBACK=0` rollback.

## Interakcja z V3.13/V3.14

- **V3.13 (PIN-space)**: fix V3.15 używa `kurier_ids.json` reverse (name→cid) — zwraca **real cid** (np. 518, nie PIN 5333). Zero konfliktu.
- **V3.14 (TTL stale)**: gdy panel_packs NADAL pokazuje order u kuriera (po tym jak V3.14 by filter'owało stale), to znaczy że order **NIE jest delivered** (panel ma authoritative view). V3.15 emit świeży COURIER_ASSIGNED → `updated_at` w state = now → V3.14 `_bag_not_stale` zwróci True (fresh). **Panel evidence NADPISUJE stale filter**. Explicit w test_v14_overridden_by_panel_evidence.

## Kolejność commitów + rollback tagów

```
step 1 → common.py flag                    tag: fix-assignlag-flag-committed
step 2 → panel_watcher.py consumer         tag: fix-assignlag-consumer-committed
step 3 → tests                              tag: fix-assignlag-tests-committed
step 4 → docs + master                      tag: f22-panel-packs-fallback-live-V3.15
```

## Plan testów (10+ asserts, KROK 4)

1. `test_missing_assignment_detected` — 5 kurierów z orders_state.cid=None + panel_packs entry → fix emit
2. `test_mass_assignment_recovery` — 20 orderów, panel_packs pełny, pipeline odzyskuje wszystkie
3. `test_no_cross_courier_contamination` — order cid=A w packs NIE trafia do B
4. `test_v13_preserved` — phantom PIN case — nadal blokowany (nowy flow używa kurier_ids, nie kurier_piny)
5. `test_v14_preserved` — stale assigned bez panel evidence nadal filtered
6. `test_v14_overridden_by_panel_evidence` — stale assigned ale w panel_packs → pozostaje (COURIER_ASSIGNED fresh)
7. `test_nick_ambiguity_skip` — 2 kurierzy "Gabriel" → skip + warn
8. `test_parse_error_graceful` — invalid parsed dict → fallback no-op
9. `test_source_divergence_logged` — event w learning_log przy mismatch
10. `test_budget_rate_limit` — > PACKS_FALLBACK_MAX_PER_CYCLE → tylko pierwsze N fetchowane
11. `test_regression_467164_michal_li` — fixture 3 ordery cid=None, packs ma "Michał Li":[...] → all emit
12. `test_performance_no_regression` — benchmark < 2x latency

## Deploy plan

Pre-deploy: py_compile + import + pytest. Dopiero po GREEN.

1. Restart `dispatch-panel-watcher.service` (fix lives here)
2. Restart `dispatch-shadow.service` (safety reload)
3. `dispatch-telegram.service` **NIE** (fix nie dotyka)
4. Smoke 2-5 min: tail log, sprawdź `NEW_ORDER` i `SOURCE_DIVERGENCE` counts

## Estimate impact

- Pre-fix (B.2): 15.8% propose/4h z missing w ANY cand
- Post-fix: oczekiwana redukcja ~50-80% missing events w kolejnych 4h
- Fetch_details extra: ~5-15/tick (w budgecie, panel API rate-safe)

## Risk/rollback per krok

- **Step 1 (flag)**: zero risk, additive
- **Step 2 (panel_watcher consumer)**: średnie ryzyko — nowa sekcja w tick loop. Mitigacja: try/except wokół całości; guard że parsed dict ma courier_packs key; budget limit.
- **Step 3 (tests)**: zero risk
- **Step 4 (docs)**: zero risk

Rollback: `ENABLE_PANEL_PACKS_FALLBACK=0` env + restart panel-watcher.

## Hard constraints confirmed

- ❌ dispatch-telegram NIE restartuje
- ❌ wave_scoring.py / feasibility_v2 NIE dotykam (Sprint C)
- ✅ Warsaw TZ, atomic writes, feature flag default True + env kill-switch
- ✅ V3.13/V3.14 rozszerzone, nie zastąpione
- ✅ learning_log schema niezmieniona (opcjonalne pole `source_divergence`)

## Go — no STOP, autonomic implementation
