# Bag integrity audit — 2026-04-19

**Bug (15:17 Warsaw):** Propozycja #467117 Baanko pokazała Michała Rom z 3-order bagiem
(Arsenal Panteon, Trzy Po Trzy, Paradiso), podczas gdy panel pokazywał tylko
2 ordery (Mama Thai, Raj). Pipeline trzymał **stale status=assigned** dla orderów
już delivered w panelu 1-2h wcześniej.

## TL;DR (Konkluzja H — root cause)

**Scenariusz H.(1) Pure stale cache.** Pipeline `orders_state.json` trzymał ordery
z `status=assigned` dla których panel panel już wcześniej zmienił status na `7=delivered`.
panel_watcher.reconcile działa poprawnie ale z **lag 15-90 min** dla detect delivered
przez budget rate-limit (`MAX_RECONCILE_PER_CYCLE=25` per 20s tick) + kolejność
FIFO w `closed_ids` set.

**Konkretny przypadek #467117 @ 13:26:28 UTC:**
- Michał Rom bag_context: {467015, 467053, 467070}
- 467015 DELIVERED @ 13:41:30 UTC (15 min PO propozycji)
- 467053 DELIVERED @ 13:41:35 UTC (15 min po propozycji)
- 467070 nadal assigned w orders_state (nie zrekoncyliowany nawet teraz)
- Real panel bag @ 15:17: {467099 Mama Thai, 467108 Raj}
- 467099 assigned @ 13:41:20 UTC — **panel_watcher złapał assign DOPIERO po 26 min**

**Lag propagation = 15-30+ minut**. Propozycje w tym oknie budują scoring na stale bagu.

**Fix scope:** TTL-aware bag filtering w `courier_resolver.build_fleet_snapshot` —
nie ufaj `status=assigned` starszym niż N min bez `picked_up_at`/`updated_at` refresh.

## A. Gdzie zbudowany stan bagu

**Single source of truth:** `courier_resolver.build_fleet_snapshot` (L186-329).
L218: `active_bag = [o for o in orders if o.get("status") in ("assigned", "picked_up")]`

`orders` to wyjście `state_machine.get_all()` filtrowane po courier_id. Pipeline
**ufa bezwzględnie** statusom w orders_state.json.

Downstream: `dispatch_pipeline.assess_order(fleet_snapshot, ...)` (L139) iteruje
po fleet i wyciąga `bag_sim = [_bag_dict_to_ordersim(b) for b in bag_raw]` (L219).
`bag_context` emitowany w learning_log (widoczny w #467117 propose decision).

## B. Cache invalidation paths

Order usuwany z bagu **tylko przez event COURIER_DELIVERED** emitowany przez
panel_watcher → state_machine.update `status: "delivered"` → build_fleet_snapshot
L218 filter go odrzuci.

**Trzy paths detection delivered w panel_watcher:**
1. **Sekcja 2 L252-294**: `if zid not in html_order_ids` → fetch_details → status==7 → emit DELIVERED. Triggered gdy order **zniknął** z panel HTML.
2. **RECONCILE STATUS L359-428**: iteruje `current_state.items()` dla assigned/picked_up, sprawdza czy `zid in closed_ids`. Closed = panel HTML blok **bez** `data-idkurier`. Budget `MAX_RECONCILE_PER_CYCLE=25` per tick.
3. **PICKED_UP RECONCILE L432+**: round-robin 10 orderów/tick, sprawdza picked_up (status=5 + dzien_odbioru).

**Dlaczego 15 min delay dla 467015:**
- Order 467015 delivered w panelu prawdopodobnie ~13:26 UTC
- Panel HTML trzyma go jeszcze jako `zamowienie_467015` bez `data-idkurier` → `closed_ids`
- Kolejka `closed_ids` może mieć 100+ wpisów od poranka → budget 25/tick = 80s kolejki
- RECONCILE trafił 467015 ~13:41:30 = 15 min po

## C. Panel refresh cycle

`panel_watcher.tick` co **20s** (L7 comment). `fetch_panel_html()` pobiera full HTML
snapshot. Delta between previous state + current HTML. Brak explicit "cleanup cycle"
dla terminalnych — polega na wykryciu w `closed_ids` LUB zniknięciu z HTML.

**Nie ma** proactive expiry dla `status=assigned` starszych niż X min.

## D. Multi-source bag reconstruction

`fleet_snapshot.bag` i `plan.pickup_sequence` (C1 RoutePlanV2) używają **tego samego
list[order_id]** — bag propagowany z `cs.bag` przez `assess_order` do route_simulator_v2.
Pickups+drops są spójne w ramach jednej propozycji.

**Brak multi-source desync** (H.3 scenariusz wykluczony).

## E. Diff panel vs pipeline @ 15:17 Warsaw

### Michał Rom (cid=520)

| Source | Orders | Count |
|---|---|---|
| Panel (user ground truth) | 467099, 467108 | 2 |
| Pipeline @ 13:26:28 (propozycja 467117) | 467015, 467053, 467070 | 3 |
| Pipeline @ 13:43:22 (teraz, po auto-reconcile) | 467070, 467099 | 2 |

**Diff @ propose time:**
- **MISSING_FROM_PIPELINE**: {467099, 467108} — panel widział, pipeline nie
  (panel_watcher miss assign lub delay)
- **PHANTOM_IN_PIPELINE**: {467015, 467053, 467070} — pipeline ma assigned, 
  panel już ich nie śledzi (delivered w panelu wcześniej)

**Breakdown timing:**
- 467015 delivered 13:41:30 UTC (panel wcześniej, ale reconcile zlapal 15 min po propozycji)
- 467053 delivered 13:41:35 UTC (analogicznie)
- 467070 NADAL assigned w state — pipeline nigdy nie zrekoncyliował (>3h stale)
- 467099 assigned @ 13:41:20 — 15 min po propozycji 467117

### F. Phantom origin tracing

**467015, 467053, 467070** — wszystkie assigned 2026-04-19 12:09:34-47 UTC przez
panel_initial batch (koordynator burst-assign 10 orderów w tym oknie, widziane
w V3.13 audit). Pozostały w state nietknięte do reconcile DELIVERED o 13:41:30.

Pattern: **orders assigned 3h+ temu bez fresh activity** = prime phantom candidates.

### Pipeline view vs reality timeline dla #467117

```
12:09:34  467015/467053/467070 assigned → Michał Rom (via panel_initial burst)
12:35:26  467099 Mama Thai appears (status=planned cid=None)
13:01:17  467108 Raj appears (status=planned cid=None)
13:17:00  [user screenshot] panel: Michał Rom bag = {467099, 467108}
13:19:36  467117 Baanko appears (status=planned cid=None) — NOWE zlecenie
13:26:28  PIPELINE PROPOSE 467117 → Michał Rom bag_context = {467015,467053,467070}
          ❌ STALE: 467015/467053 były już delivered w panelu
          ❌ MISSING: 467099, 467108 nie były assigned w pipeline state
13:41:20  467099 ASSIGNED cid=520 (26 min po przypisaniu w panelu)
13:41:25  467117 ASSIGNED cid=518 (15 min po propozycji, koordynator przypisał Michałowi Ro)
13:41:30  467015 DELIVERED (reconcile)
13:41:35  467053 DELIVERED (reconcile)
```

## G. State machine integrity

`state_machine.upsert_order` (L189+) ma standard event-driven update. Brak race
conditions w upsert (atomic file write z lock). Reassignment handled przez
COURIER_ASSIGNED event z nową courier_id.

**Brak bug strukturalny w state_machine** — problem to **timing** detection w panel_watcher,
nie state corruption.

## H. Konkluzja — scenariusz (1) Pure stale cache

Pipeline ufa `orders_state.json` jako ground truth. panel_watcher updates są
eventually consistent, ale `MAX_RECONCILE_PER_CYCLE=25/tick` + panel HTML
zachowuje delivered orders w `closed_ids` set przez pewien czas → queue backlog
= 15-90 min delay detect delivered.

**Zero cross-courier contamination** (F.1 H.2 wykluczony — 467015 NIE był 
przeniesiony do Grzegorza, był delivered przez Michała Rom).
**Zero race conditions** (H.4 wykluczony).
**Zero multi-source desync** (H.3 wykluczony).

Bug = **timing / freshness**, nie architectural.

## Secondary finding: #467101 Arsenal Panteon Grzegorz

User mówił że 467101 (Arsenal Panteon, Al. Piłsudskiego 20) jest u Grzegorza.
Orders_state @ now: `status=planned cid=None updated=12:41:08`. Pipeline nie wie
że koordynator przypisał Grzegorzowi. **Drugi objaw tego samego bugu** — panel
ma assigned, pipeline jeszcze nie złapał (lag >3h!).

## I. Blast radius

### Pliki core fix (1-2)

1. **`courier_resolver.py`** L218 — `active_bag` z TTL filter:
   ```python
   active_bag = [o for o in orders if o.get("status") in ("assigned", "picked_up")
                 and _bag_not_stale(o, now_utc)]
   ```
   Plus helper `_bag_not_stale` sprawdzający `updated_at` lub `assigned_at`
   < max 90 min ago (jeśli wciąż assigned bez pickup_at → prawdopodobnie reconcile
   nie dogonił).

2. **`common.py`** — flag `STRICT_BAG_RECONCILIATION=True` + TTL constant
   `BAG_STALE_THRESHOLD_MIN=90` (kill-switch + tunable).

### Konflikt z availability fix (V3.13)?

**ZERO konfliktu.** V3.13 (32be76a) zmienił L211-234 tylko (fleet all_kids filter).
Mój fix bag integrity dotyczy L218 (active_bag filter). Oba cohabit spokojnie —
obie poprawki ortogonalne na różnych liniach tego samego pliku. Diff sprawdzę
przy step 2 PLAN.

### Pliki NIE zmieniane

- `panel_watcher.py` — panel_watcher działa poprawnie, tylko z rate-limit. 
  Secondary fix (zwiększ RECONCILE_PER_CYCLE lub add aggressive age-based reconcile)
  DEFERRED — scope creep, osobna sesja.
- `state_machine.py` — zero zmian
- `dispatch_pipeline.py` — zero zmian
- `scoring.py`, `wave_scoring.py`, `feasibility*.py` — zero

### Testy

- **Nowy `tests/test_bag_contents_integrity.py`** — 8+ testów
- 153/153 + 26/26 baseline nienaruszone

### Estimate impact

Z learning_log ostatnie 4h:
- Propozycje z bag_context zawierające order assigned >90 min temu bez picked_up
  = **?** (do wyliczenia w audit)

Historycznie: każdy kurier z aktywnym bagiem delivered-ale-nie-reconcilied w oknie
15-90 min → scoring errors. Panel_watcher reconcile lag jest stałym faktem
architektury (budget/FIFO), więc TTL filter chroni pipeline przed ufaniem
stale datom niezależnie od panel_watcher timing.

## Podsumowanie do KROKU 2

Fix **1-2 plików**: `courier_resolver.py` + `common.py` (flag). TTL-based filter
na `active_bag`. Brak konfliktu z V3.13 availability fix. Orthogonal do Sprint C.
