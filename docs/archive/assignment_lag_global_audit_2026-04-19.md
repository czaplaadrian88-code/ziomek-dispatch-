# Assignment lag global audit — 2026-04-19 (V3.15)

**Bug (scenariusz 3 z poprzedniego statusu):** Panel assigna ordery do kurierów, pipeline `orders_state.json` ma `cid=None` przez X sekund/minut. Propozycje oparte na pipeline view pokazują kurierów z pełnymi bagami w panelu jako "wolnych".

## A. Mapa źródeł prawdy

```
Panel HTML  ──parse_panel_html──► {
                                    order_ids, assigned_ids, unassigned_ids,  ──► panel_watcher (konsumuje)
                                    rest_names, html_times, closed_ids,       ──► panel_watcher
                                    courier_packs {nick:[oid]},               ──❌ DEAD — nikt nie konsumuje
                                    courier_load {nick:"N/M"},                ──❌ DEAD
                                  }
Panel API   ──fetch_order_details──► raw JSON ──normalize_order──► orders_state ──► build_fleet_snapshot ──► scoring
```

**Potwierdzenie dead data (grep):**
```
panel_client.py:200-214   courier_packs budowane (regex na widok_kurier divs)
panel_client.py:238       zwracane w parsed dict
panel_watcher.py:153-155  konsumuje tylko order_ids/assigned_ids/rest_names
panel_watcher.py:158-507  zero wystąpień parsed["courier_packs"] poza construction
tests/test_reconcile_dry_run.py:76  mockowane jako {} (potwierdza że niekonsumowane)
```

`courier_packs` ma **ground truth mapping nick→order_ids** dostępny w **każdym tick** panel_watcher (co 20s). Jest dead data.

## B. Pomiar skali bugu — last 4h / 24h

### B.1 Metodologia

Dla każdej propozycji z `learning_log.jsonl`, dla każdego kandydata (best + alts) sprawdzam: czy w `orders_state.json` NOW istnieją ordery z `courier_id==candidate.cid` i `assigned_at` w oknie `[T_propose - 30min, T_propose + 5min]` **niebędące w bag_context kandydata**. Taki order = **pipeline nie widział go w momencie propozycji** ale my wiemy NOW że należał do tego kuriera.

**Uwaga**: metric niedoszacowuje case'y gdzie panel_watcher **nigdy** nie złapał assignment (przypadek Michała Li z #467164: ordery `cid=None` w state → nie liczone). Rzeczywista skala jest **większa** niż pokazana.

### B.2 Wyniki

| Window | PROPOSE | w/ missing BEST | w/ missing ANY | missing events | free+panel bag |
|---|---|---|---|---|---|
| last 4h | 158 | **5.7%** (9) | **15.8%** (25) | 219 | 36 |
| last 24h | 302 | 4.6% (14) | **28.5%** (86) | 391 | 53 |

### B.3 Per-courier (last 4h)

| cid | name | apps | missing | % bug |
|---|---|---|---|---|
| 179 | Gabriel | 114 | 75 | **65.8%** |
| 503 | Gabriel J | 48 | 23 | **47.9%** |
| 400 | Adrian R | 122 | 52 | 42.6% |
| 441 | Sylwia L | 48 | 18 | 37.5% |
| 387 | Aleksander G | 108 | 28 | 25.9% |
| 509 | Dariusz M | 77 | 14 | 18.2% |
| 518 | Michał Ro | 31 | 4 | 12.9% |
| 520 | Michał Rom | 24 | 3 | 12.5% |
| 484 | Andrei K | 28 | 2 | 7.1% |
| 508 | Michał Li | 72 | 0 | 0.0% (*) |

(*) Michał Li z user'a case **0% w tej metryce** bo jego ordery (467129/467131/467155) NIGDY nie dostały cid=508 w state — panel_watcher nie złapał assignmentu do teraz. Metric limited; rzeczywisty skala bug include takie "never caught" cases.

### B.4 Top missing order IDs (last 4h)

| order_id | rest | final_cid | assigned_at | × missing |
|---|---|---|---|---|
| 467000 | Rukola Kaczorowskiego | 179 | 12:09:34 | 11 |
| 467005 | Zapiecek | 387 | 12:09:34 | 10 |
| 467042 | Trójkąty i Kwadraty | 400 | 12:09:37 | 10 |
| 467045 | Rukola Sienkiewicza | 503 | 12:09:38 | 10 |
| 467049 | Trójkąty i Kwadraty | 400 | 12:09:39 | 10 |
| 467051 | Pani Pierożek | 179 | 12:09:40 | 10 |
| 467061 | Rany Julek | 400 | 12:09:43 | 10 |
| 467062 | Baanko | 387 | 12:09:44 | 10 |
| 467065 | Czebureki | 503 | 12:09:45 | 10 |
| 467071 | Rany Julek | 400 | 12:09:48 | 10 |

**Wszystkie z 12:09:34-48 UTC burst** (koordynator ręczny batch assign 10 orderów w 15s okno, patrz V3.13 audit). panel_watcher złapał po reconcile ~12:09:34 single tick, ale w kolejnych propozycjach pipeline **nadal nie widział ich przez lag 10-30s** między panel_watcher tick i następną propozycją.

## C. Pattern matching

### C.1 GPS axis
Kurierzy z missing eventsami (Gabriel, Adrian R, Sylwia L, itp.) mają GPS active (`pos_source=gps` w propozycjach). **Bug NIE jest no_gps-specific**.

### C.2 Nick axis
Gabriel (179) i Gabriel J (503) oba w topie missing eventsów. Nick collision "Gabriel" — istnieją dwa kurierzy o podobnych imionach. **Potencjalne ryzyko E.2** — nick-based lookup może błądzić między nimi. ALE: ich missing events to **różne order_ids** → nie jest to misresolution nicka, tylko oba mają rzeczywiste opóźnione assignments.

### C.3 Order status axis
Top missing orderów: `final_cid assigned_at ~12:09 UTC burst`, status teraz w state to `delivered` (reconcile dogonił). W momencie propozycji były `assigned` → bug dotyczy świeżych assignments tuż po burst panel_watcher tick.

### C.4 Time axis
Missing events skoncentrowane w 12:00-14:00 UTC (14:00-16:00 Warsaw peak). 219 events w 4h.

### C.5 Panel_watcher cycle
Top missing orders assigned_at 12:09:34-48 UTC (burst). Reconcile emit często wchodzi w kolejce, pipeline polling nie ma natychmiastowej widoczności. Fundamental: **polling 20s lag** + brak aktywnego pulla courier_packs.

## D. Root cause candidates — ranking

### **#1 (primary)**: `courier_packs` — parsed ale niekonsumowany → pipeline nigdy nie ma ground-truth courier-packs view

**Evidence:**
- A grep potwierdza brak konsumenta
- panel_packs build co 20s (każdy tick), dostępny w `parse_panel_html` output
- Byłby natural fallback gdy panel_watcher.reconcile opóźnia emit COURIER_ASSIGNED
- **B.3**: concentrated missing dla kurierów z aktywnymi bagami → mogłoby być rozwiązane gdy courier_packs konsumowany

### #2 polling/diff logic — lag 20s minimum

**Evidence:** C.4+C.5 (peak hour concentration, post-burst drift)

### #3 bag_context missing bag_oid — może być artifact learning_log schema

**Evidence:** weaker, sprawdzone — bag_context zawiera valid order_ids

### #4 reassign_checked UnboundLocalError (pre-existing z V3.13 session)

**Evidence:** błąd sypie się ~co tick w panel_watcher, mógłby skracać rate-limit reassignments. Już zdiagnozowany jako osobny bug, nie primary cause.

**Selected #1 — courier_packs consumer.**

## E. Ryzyka fixa

| # | Ryzyko | Prob | Mitigation |
|---|---|---|---|
| E.1 | Phantom amplification — panel HTML zombie entry wzbogaca bag | M | Guard: order_id musi być w state z status w `(planned/assigned/picked_up)` — jeśli brak w state, fetch_details najpierw |
| E.2 | Nick ambiguity — 2 kurierzy same display name | M | Strict: nick→cid przez reverse name lookup w kurier_ids; ambiguous (multiple matches) → skip + log warn |
| E.3 | Panel HTML layout change → regex crash | L | Try/except wokół completion, return empty dict on parse error |
| E.4 | V3.13 konflikt — PIN gdy name-lookup | L | V3.13 excludes PIN-keys z all_kids; nowy flow używa `kurier_ids.json` reverse (name→cid), który zwraca real cid nie PIN. No conflict |
| E.5 | V3.14 konflikt — stale >90min wykluczony, panel_packs chce go wzbogacić | M | **Panel_packs evidence NADPISUJE V3.14 stale filter** — jeśli panel NADAL pokazuje order jako aktywny u kuriera, znaczy że nie jest delivered. Explicit decision: `test_v14_overridden_by_panel_evidence` |
| E.6 | Race condition panel_packs parse vs orders_state update | M | Fix jest w panel_watcher (emitter side) — jedna ścieżka, sequential |

## F. Blast radius

### Pliki do zmiany

1. **`panel_watcher.py`** (main fix) — dodać courier_packs consumer section po Sekcji 2 reassign, przed RECONCILE. Dla każdego (nick, order_ids) z packs sprawdza mismatch z orders_state + emit COURIER_ASSIGNED dla missing. Budget rate-limit.
2. **`common.py`** — flag `ENABLE_PANEL_PACKS_FALLBACK=True` + env override.
3. **`courier_resolver.py`** — **prawdopodobnie NIE trzeba** (pipeline pozostaje na orders_state; fix jest w emitter).
4. **`tests/test_assignment_lag_fix.py`** — 10+ testów.

### Serwisy do restartu

- `dispatch-panel-watcher` (fix here) — YES
- `dispatch-shadow` — dla safety (importy)
- `dispatch-telegram` — NO (nie dotyka)

### Testy

- Nowe: min 10 (V3.15)
- V3.13 `test_panel_aware_availability.py` — 26/26 musi przejść
- V3.14 `test_bag_contents_integrity.py` — 25/25 musi przejść
- V3.12 `test_city_aware_geocoding.py` — 16/16
- Baseline legacy — 137

### Rollback

- Runtime: `ENABLE_PANEL_PACKS_FALLBACK=0` + restart panel-watcher
- Git: `git reset --hard 8500f80`
- Per plik: `.bak-pre-assignlag-2026-04-19` backups

## Konkluzja

**Bug globalny confirmed** (15.8% propose last 4h, 28.5% last 24h), per-courier rozkład nie uniform (Gabriel 65.8%), ale dotyka co najmniej **9 różnych kurierów** z GPS. Primary cause = `courier_packs` consumed dead data. Fix path = panel_watcher emit COURIER_ASSIGNED bazując na panel_packs ground truth.

Scope: **2 pliki core** (panel_watcher + common), 1 test file, 2 service restarts. Ortogonalny do V3.12/V3.13/V3.14 — rozszerza emitter, nie zmienia consumera. Oszacowane impact: eliminacja **~15-30%** missing assignment events (te capture'owane przez heurystykę; rzeczywisty may be higher).

Idę do KROK 2 PLAN.
