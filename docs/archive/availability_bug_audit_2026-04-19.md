# Availability bug audit — 2026-04-19

**Bug (19.04 14:00-14:08 Warsaw):** 8 propozycji (#467070-#467077) pokazały
identyczną trójkę "wolnych" kandydatów (Michał Ro, Aleksander G, Gabriel J)
z `🟢 wolny` + "brak GPS" + `best=5333`, mimo że w panelu wszyscy trzej mieli
2-3 ordery w bagu.

## TL;DR (Konkluzja F — root cause)

**Bug strukturalny 3-warstwowy, dominuje (1):**

1. **PIN space collision w `build_fleet_snapshot`** (`courier_resolver.py:214`):
   fleet dodaje `kurier_piny.json` keys (PIN-y 4-cyfrowe) jako **osobnych kurierów**
   obok prawdziwych `courier_id`. Michał Ro istnieje jako DUPLIKAT:
   - `cid=518` (prawdziwy courier_id z `kurier_ids.json`, ma 3 ordery w orders_state)
   - `cid=5333` (PIN z `kurier_piny.json`, **pusty bag**, **no_gps fallback**)
   
   Fleet_snapshot(cid=5333) szuka orderów w `state_machine.get_all()` po
   `courier_id=5333` → żaden nie ma (wszystkie są pod `518`) → bag=0 → no_gps.
   
   **Learning_log zapisuje `chosen_courier_id=5333` — PIN, nie courier_id.**
   To także znaczy że Telegram propozycje (jeśli koordynator je naciśnie)
   próbują assignować cid=5333 do panel API → panel nie zna tego ID.

2. **No_gps tie-break degeneracja:** wszyscy no_gps kurierzy dostają
   `pos=BIALYSTOK_CENTER` (synthetic, constant) → identyczny `km_to_pickup`
   dla dowolnego pickup → identyczny score. 4 kandydaci z tym samym score
   81.00/77.96/... — top-3 jest arbitralne, nieinformacyjne.

3. **Panel_watcher lag** (secondary, data freshness): koordynator przypisywał
   ordery w panelu ~12:08-09 UTC, panel_watcher złapał batch dopiero o
   12:09:34 UTC. Propozycje 12:05-12:08 działały na orders_state bez tych
   bagów. To NIE strukturalny bug (panel_watcher działa co 20s), tylko
   data freshness edge case przy massowym ręcznym assign.

**Fix scope:** primary (1) + (2). (3) wymaga architekturalnej zmiany.

## Myth-busted: "best=5333 jako score cap"

Prompt zakładał że `5333` = score cap. Weryfikacja w `learning_log`:
scory są różne per propozycja (50.35, 57.61, 63.27, 77.96, 81.00, 81.19, 87.62).
**`5333` to `chosen_courier_id` — PIN Michała Ro** z `kurier_piny.json`.
Nie ma score capping.

## A. Gdzie liczona availability

`is_free` jako dedykowany field — **nie istnieje** w `CourierState`
(`courier_resolver.py:57-77`). Availability jest derywowana pośrednio:

- `free_at_min` w `dispatch_pipeline.py:381` — default `0.0` gdy brak
  bag_sim lub brak plan.predicted_delivered_at.
- `len(bag_sim) == 0` → `free_at_min=0` → telegram_approver wyświetla
  `🟢 wolny` (`telegram_approver.py:195`: `if free_at <= 0: tags.append("🟢 wolny")`).
- `r6_bag_size=0` w learning_log per candidate — confirmation że pipeline
  widzi pustą torbę.

**Brak direct `is_free` check.** Availability = derivation z `bag_size` + `free_at_min`.

## B. Fallback no_gps / pre_shift

`courier_resolver.py:324-329`:
```python
# 4. no_gps fallback: kurier wolny, brak GPS i brak historii.
#    Dajemy syntetyczną pozycję = centrum miasta.
cs.pos = BIALYSTOK_CENTER
cs.pos_source = "no_gps"
```

Komentarz **explicit** mówi "kurier wolny" — ale kod nie ustawia
`is_free` / `has_bag` flag bo nie istnieją. `bag` (pole CourierState)
też nie jest tu zerowane — bo wcześniej `cs.bag = active_bag` w L221.

Problem: **dla cid=5333 (PIN Michała Ro)**, `active_bag` z L218 jest pusty
(żaden order w orders_state nie ma `courier_id=5333`), więc cs.bag=[]
i pipeline legitymnie uznaje kuriera za wolnego. To _nie jest_ bug
fallbacku — to bug ID space'u (PIN≠courier_id).

`pre_shift` (L437-440): dozwolone gdy shift start < GRACE min. Pozycja
synthetic (BIALYSTOK_CENTER wg komentarza). Ten sam pattern.

## C. Source "best=5333"

Zweryfikowane: `5333` = PIN Michała Ro z `kurier_piny.json`. Zapisane w
`learning_log.jsonl` jako `best.courier_id`. Droga do tego:

1. `build_fleet_snapshot` L214: `all_kids = per_courier.keys() | names.keys() | piny.keys()`
2. Pin keys 4-digit dodane jako kids.
3. `cs = CourierState(courier_id=kid)` — PIN staje się courier_id w snapshot.
4. Dispatch_pipeline.assess_order iteruje po fleet (dispatch_pipeline.py:214
   i 674), dostaje cid=5333 jako kandydata.
5. Scoring → learning_log pisze `courier_id=5333`.

**92 historycznych wystąpień `5333` w `learning_log.jsonl`.** Pierwsze
`2026-04-14T09:00:03`, więc bug **co najmniej 5 dni**.

## D. Panel order data w pipeline

`panel_client.fetch_all_unassigned()` i `fetch_order_details()` udostępniają
pełną listę orderów panelowych. `panel_watcher` emituje events (NEW_ORDER,
COURIER_ASSIGNED, COURIER_PICKED_UP, COURIER_DELIVERED) do `event_bus.jsonl`.
State_machine stosuje eventy do `orders_state.json`.

**Luka**: `panel_watcher.tick` co 20s (L7). Między tickami, jeśli koordynator
przypisze order w panelu, pipeline **jeszcze nie wie**. Dziś @ 12:08:
koordynator przypisał 10 orderów, watcher zaciągnął batch o 12:09:34
(15-sec okno dla wszystkich assigned_at).

Pipeline **nie odczytuje panelu bezpośrednio** w momencie scoringu — polega
na orders_state. To cenne (deterministic, fast) ale wprowadza window
staleness przy massive assignments.

## E. Full roster 14:00-14:08 Warsaw (E.1 + E.2 + E.3)

### E.1 Bag state @ 12:05:00 UTC (ground truth via orders_state reconstruction)

orders_state.json teraz (po panel_watcher catch-up 12:09:34) ma te ordery
assigned. **Przed 12:05:00 UTC** (cutoff reconstruction: `assigned_at < cutoff
AND (delivered_at is None OR > cutoff)`) TYLKO 1 kurier miał real bag:

| cid | name | bag_size | details |
|---|---|---|---|
| 123 | Bartek O. | 1 | 467008 Grill Kebab assigned 10:28:47 |

**Wszyscy inni — pusty bag w orders_state @ 12:05.** Zgodne z
learning_log `bag_size=0` w propozycjach 12:05-12:08.

### E.2 Learning_log snapshot 12:05-12:09 UTC (pipeline view)

Wszystkie 8 propozycji pokazały identyczną top-4 (z drobnymi permutacjami):

| cid | name | pos_source | km_to_pickup | free_at | bag_size |
|---|---|---|---|---|---|
| **5333** | Michał Ro (PIN) | no_gps | 6.41-6.99 | 0.0 | 0 |
| 387 | Aleksander G | no_gps | 6.41-6.99 | 0.0 | 0 |
| 503 | Gabriel J | no_gps | 6.41-6.99 | 0.0 | 0 |
| 400 | Adrian R | no_gps | 6.41-6.99 | 0.0 | 0 |

km_to_pickup różni się **tylko per order** (zależy od pickup_coords),
**identyczne dla wszystkich 4 kurierów** w danej propozycji.

### E.3 Diff table — ground truth (panel 14:08) vs pipeline (12:05-12:09)

**Kluczowa** tabela pokazująca miss-klasyfikacje. Z orders_state odtworzone
bagi o `assigned_at 12:09:34-50` (widziane w panelu wg Adrian'a 14:08):

| cid (real) | name | panel bag @14:08 | pipeline snapshot @12:05 | MISS? |
|---|---|---|---|---|
| 520 | Michał Rom | 3 (467015, 467053, 467070) | bag=0 | **TAK** |
| 518 | Michał Ro | 3 (467052, 467076, 467077) | bag=0 | **TAK** |
| 387 | Aleksander G | 2 (467005, 467062) | bag=0 | **TAK** |
| 503 | Gabriel J | 2 (467045, 467065) | bag=0 | **TAK** |
| 400 | Adrian R | 2 (467049, 467061) + 1 (467042) | bag=0 | **TAK** |
| 484 | Andrei K | 3 (467047, 467048, 467067) | bag=0 | **TAK** |
| 413 | Mateusz O | 4 (467020, 467072, 467073, 467074, 467075) | bag=0 | **TAK** |
| 179 | Gabriel | 4 (467000, 467051, 467058, 467060) | bag=0 | **TAK** |
| 509 | Dariusz M | 4 (467055, 467057, 467063, 467064) | bag=0 | **TAK** |
| 123 | Bartek O. | 4 (467008, 467041, 467044, 467056) | bag=1 (467008 only) | częściowy |
| 508 | Michał Li | 2 (467034, 467054) | bag=0 | **TAK** |
| 500 | Grzegorz | 2 (467043, 467059) | bag=0 | **TAK** |
| 441 | Sylwia L | 2 (467039, 467050) | bag=0 | **TAK** |

**13/14 aktywnych kurierów MISS** (poza Bartek który miał 1/4 zidentyfikowane).
**Bug jest globalny** — dotyka wszystkich kurierów na zmianie, nie tylko
3 wymienionych w screenshocie Adrian'a.

## F. Pattern matching (root cause axis analysis)

### F.1 GPS axis
- Wszystkie MISS z E.3 → `pos_source=no_gps` w learning_log.
- Pytanie: dlaczego **wszyscy** no_gps mimo że część ma PWA/Courier App?
- Odpowiedź: `gps_positions_pwa.json` @ check pokazuje 1 entry (minimal fleet).
  Większość kurierów używa Courier App który jeszcze nie wysłał recent GPS
  w obrębie GPS_FRESHNESS_MIN window.
- Wniosek: F.1 nie różnicuje. **100% MISS = no_gps**.

### F.2 Hierarchy axis (pos_source)

| pos_source | MISS count | not_miss |
|---|---|---|
| gps | 0 | 0 (brak w top-4) |
| no_gps | 13 | 1 (Bartek partial) |
| pre_shift | 0 | 0 |

**100% MISS w "no_gps" branch.**

### F.3 Bag size axis

Bag sizes w panelu: 2, 3, 4. Wszyscy MISS. Pipeline widzi 0 dla wszystkich
(poza Bartek 1). **Nie pattern — full miss**.

### F.4 Status axis

Ordery w panelu mają status_id=3 ("dojazd"/assigned). W orders_state ci sami
kurierzy mają ordery z **label="assigned"**. Mapping poprawny.

Brak status_id=4/5/6 w propozycjach (te mają dokumentację ale w tym burst
nie pojawia się — assignments świeże).

### F.5 Courier_id axis

**Wszyscy kurierzy na zmianie MISS.** Jednak `top-4` propozycji pokazuje
tylko 3-4 konkretnych: Michał Ro (5333=PIN), Aleksander G (387), Gabriel J
(503), Adrian R (400). Dlaczego ci, nie inni?

Hipoteza: **feasibility filter** (`feasibility.check`) odrzuca większość —
np. Bartek, Andrei K, Mateusz O mogą być **odrzuceni** przez bag_size
constraint (`r4_bag_cap` lub `r1_delivery_spread`). Nie propagowani do top-4.

Ale ci top-4 są kierowani na pickup 6.4-6.9 km od synthetic pos (BIALYSTOK_CENTER).
To duża odległość — normalnie dyskwalifikująca. Tiebreak **arbitrarny**.

### Konkluzja F

**100% MISS = `pos_source=no_gps` + `bag_size=0` (pipeline view).**

Root cause ma 2 warstwy:

- **Strukturalny (dominant):** `build_fleet_snapshot:214` zalicza PIN-y jako
  osobnych kurierów → duplikat Michała Ro jako `cid=5333` z pustym bag.
  W ciężkich warunkach (all-fleet no_gps), duplikaty dominują top-ranking
  bo ich pos=BIALYSTOK_CENTER jest **czasem** bliżej niż prawdziwy courier
  z `last_delivered` posem (np. Bartek z GPS był na innym rogu miasta).

- **Tie-break degeneracja:** no_gps zwraca identyczny synthetic pos dla
  wszystkich → identyczny km_to_pickup w obrębie propozycji → tie. Tiebreak
  idzie na `courier_id` (arbitrary). Stąd "top-3" jest efektywnie ZESPÓŁ
  pierwszych 3 kurierów no_gps z fleet_snapshot ordered by cid. Nie informacyjny.

## G. Blast radius

Pliki do zmiany:
1. **`courier_resolver.py`** L214 — USUŃ `piny.keys()` z `all_kids`
2. **`courier_resolver.py`** L226-231 — zachowaj PIN jako fallback nazwy
   (legacy name lookup), ale nie jako source of kids
3. **`common.py`** — flag `STRICT_COURIER_ID_SPACE=True` (kill-switch)
4. **`scoring.py`** / `dispatch_pipeline.py`  — **NIE DOTYKAĆ** (ortogonalny
   do scoring logic)
5. Nie wymaga modyfikacji `feasibility_v2.py`, `wave_scoring.py` (C5), itd.

**Potential secondary fix (poza scope primary):**
6. `courier_resolver.py` fallback logic → gdy `cs.bag == []` ale w
   `orders_state` są ordery z **similar name** (np. "Michał Ro") a cid nie
   matches — warning + skip tego phantom kuriera. Ale to defense-in-depth,
   nie blocker.

**Test infrastructure:**
7. `tests/test_panel_aware_availability.py` — nowy plik z min 7 testami
   (fixture per prompt)
8. Istniejące 137+16 testów musi pozostać zielone.

**Estimate impact (key metric):**
Per learning_log ostatnie 24h:
- `5333` pojawia się **92 times** (ostatnie 5 dni).
- Szacowane że ~10-15% propozycji w okresach all-fleet-no_gps mają
  phantom PIN-kuriera w top-4.
- **Po fixie**: zniknie ~10-15% duplikatów z top-ranked, top-ranking
  przechodzi na prawdziwe cid (518 zamiast 5333), koordynator może
  wysłać faktyczny assign via panel API. Plus tiebreak między no_gps
  pozostaje degenerate — secondary fix może dodać deterministic tiebreak
  np. po last_delivered_at lub alphabetical.

**Dotyczy flag**: nowy `STRICT_COURIER_ID_SPACE`, default True (kill-switch).
Ortogonalny do wszystkich F2.2 Sprint C flag.

## Podsumowanie do KROKU 2 (plan)

Fix w 1 linii kodu: `courier_resolver.py:214` → usuń `piny.keys()` z `all_kids`.
Plus test regresji + flag + dokumentacja. Shadow delta policzy real impact.

Primary commit `fix-availability-id-space-*`, master tag
`f22-strict-bag-awareness-live`.
