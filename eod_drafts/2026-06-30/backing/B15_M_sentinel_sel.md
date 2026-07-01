# B15 — Klasa M (cicha awaria / sentinele jako dane) w SEL + ROUTE + FEAS

**Agent:** B15-M-sentinel-SEL · **Lane:** B · **Tryb:** READ-ONLY (zero edycji/restartów/flipów) · **Data:** 2026-06-30 ~15:30 UTC
**Zakres:** klasa M w warstwie selekcji (L7), trasy (L5/L9 route_simulator+plan), feasibility (L5) + geometria (L2 haversine/osrm) na ścieżce decyzyjnej `assess_order`.
**Metoda:** świeży `grep -nE` per moduł (linie DRYFUJĄ — re-grep przed użyciem) + `Read` hot-path + **walidacja na ŻYWYM logu** (`scripts/logs/*.log`, `shadow_decisions.jsonl`) — NIE z seed-doców.
**Definicja M (z taksonomii):** sentinel/wartownik wpadający w matematykę jako „dana"; cicha awaria (bare-except połykający błąd); fail-loud-guard zneutralizowany przez catch-all; brak fail-loud przy produkcji sentinela.

> **TL;DR — JEDEN root, 6 manifestacji.** Sentinel `(0,0)` jest **produkowany w warstwie danych** (`_bag_dict_to_ordersim` fallback `(0.0,0.0)`, new-order `delivery_coords or (0.0,0.0)`) i **wpada do losowego haversine bez guardu** → albo **ValueError → catch-all `_v328_eval_safe` → ZAJĘTY kurier znika z puli** (2046× w logu, **POTWIERDZONE LIVE dziś cid=179, cid=492**), albo **OSRM `route/table` → sentinel 9999 min → leg infeasible → kurier cicho wycięty** (14456× COORD_GUARD). `_sanitize_courier_pos` łata TYLKO pozycję kuriera w 3 z N miejsc; `_valid()` ratuje feasibility, ale **selekcyjny V326_WAVE_VETO `:4823` i repo-cost `:2147` używają truthy-guardu `if coords:` który NIE łapie `(0,0)`**. Fail-loud #81 jest realnie zneutralizowany przez `except Exception`. **Brak alertu/KOORD — jedyny ślad to ERROR w logu, którego nikt nie czyta.** Zwija się do **K5 „sentinele jako most"** (ziomek-unified-audit) — most do position-twins (A6 gr.3) i floor (A6 gr.6).

---

## 0. GROUND FACTS (świeże liczniki — read-only)

| Sygnał | Liczba (logi `scripts/logs/*.log`) | Świeżość |
|---|---|---|
| `except Exception` w `dispatch_pipeline.py` | **119** (+ 1 bare `except:` + 130 except-bloków łącznie) | grep dziś |
| `haversine sentinel (0,0)` (raise) | **3885** | wiele dni |
| `V328_CP_SOLVER_FAIL_PER_COURIER` (kurier wyrzucony) | **2046** | last = 2026-06-30 09:44:31 |
| `V328_POOL_PARTIAL_FAIL` (warning, BEZ alertu) | **2016** | dziś |
| `V328_OR_TOOLS_MASS_FAIL` (≥50% crash → heurystyka) | **41** | — |
| `COORD_GUARD` (osrm route/table (0,0)→sentinel) | **14456** | last = 2026-06-30 11:11:52 |
| **distinct (cid,order) wyrzucone przez V328 DZIŚ** | **8** — `cid=179`×5 orderów, `cid=492`×3 orderów | 2026-06-30 |

**Próbka żywa (smoking gun):**
```
2026-06-30 09:44:31 ERROR V328_CP_SOLVER_FAIL_PER_COURIER cid=179 order=484410
   exc=ValueError: haversine: sentinel (0,0) (ll1=(0.0, 0.0), ll2=(53.128252, 23.15241))
2026-06-30 11:11:52 ERROR COORD_GUARD #8: table 2 invalid coord(s) [(0.0,0.0),(0.0,0.0)] → sentinel cells 9999.0min
```
`ll1=(0,0)` = zatruta współrzędna; `ll2=(53.128,23.15)` = realny pickup. Sygnatura `(first_arg=(0,0), second_arg=real_pickup)` **dokładnie pasuje do `:4823`** (`haversine(tuple(_last_drop), tuple(_new_pickup))`). cid=492 = Jakub W — MEMORY notuje „Jakub 492 pozycja→sentinel (0,0) 10:51" (clamp-preshift case z dziś).

> ⚠ „100s/dzień" z briefu **POTWIERDZONE**: 2046 V328 + 14456 COORD_GUARD w logach (~7-14 dni) = rząd setek/dzień. Compute-but-vanish NIE jest tu problemem — to realne wyrzucenia z puli.

---

## 1. ŁAŃCUCH PRZYCZYNOWY (jeden root, dwa ujścia)

```
WARSTWA DANYCH (produkcja sentinela, BEZ fail-loud):
  _bag_dict_to_ordersim:3133-3135   deliv_c = _repair_bag_coords(...) or deliv_c or (0.0, 0.0)
  assess_order:3470                 delivery_coords = tuple(order_event.get("delivery_coords") or (0.0,0.0))
        │  (0,0) wstrzyknięte cicho, awaria odroczona do losowego konsumenta)
        ▼
KONSUMENT haversine (RAISE)              KONSUMENT osrm route/table (SENTINEL)
  :4823 V326_WAVE_VETO  ──┐               osrm_client.route/table:570  ──┐
  :2149 _compute_repo_cost│                 → _invalid_coord_result        │
  (guard `if x:` NIE łapie│                   duration = 9999 min          │
   (0,0) — truthy)        │                 (COORD_GUARD log rate-limited) │
        ▼                 │                        ▼                       │
  ValueError (#81 fail-loud)              leg = 9999 min → plan infeasible │
        ▼                 │                        ▼                       │
  _v328_eval_safe:5695 ───┘               kurier cicho NIE-feasible ───────┘
  `except Exception` →            (oba ujścia: BRAK alertu/KOORD, tylko log)
  ('fail', cid) → continue →
  KURIER WYRZUCONY Z PULI
```

**Asymetria-bliźniaków (klasa B w środku M):** ta SAMA dana `(0,0)` daje DWA różne ciche skutki zależnie od tego, którą funkcję geometrii trafi pierwszą — `haversine()` (raise→drop) vs `osrm.route/table()` (sentinel→infeasible). Niespójna obsługa tego samego sentinela = klasa B nałożona na M.

---

## 2. INSTANCJE (plik:linia świeży · źródło/objaw · łatane? · otwarte? · severity · dowód)

### M-1 — `dispatch_pipeline.py:4823` — V326_WAVE_VETO haversine na zatrutym `_last_drop=(0,0)` [P1, SOURCE, OPEN]
```
4819   _last_drop = _b.get("delivery_coords")          # surowo z bag_raw, BEZ _valid/_sanitize
4822   if _last_drop and _new_pickup:                  # ← truthy-guard: (0,0) JEST truthy → przechodzi
4823       v326_wave_geometric_km = haversine(tuple(_last_drop), tuple(_new_pickup))
```
- **To JEST live source** dzisiejszych V328. Sygnatura logu `ll1=(0,0), ll2=real_pickup` = arg1 `_last_drop`, arg2 `_new_pickup`. Brak `try` lokalnie → raise propaguje do `_v328_eval_safe`.
- Ironicznie: to SOFT wave-veto (geometryczny check „czy nowy drop nie odlatuje"), który CAŁĄ ewaluację kuriera wywala, gdy worek ma data-quality `(0,0)`.
- Guard `if _last_drop:` (l.4822) = dokładnie błąd z A6 cross-cutting / floor-audit BUG#2: „guarda `not coords` ale NIE `(0,0)`".
- **Dowód:** 8 distinct (cid,order) dziś; cid=492 (Jakub W, pozycja (0,0) 10:51) wyrzucony z 484397/400/404 — NIE bo niefeasible, bo worek/pozycja zatrute.
- **dedup_hint:** K5-coord-sentinel (root).

### M-2 — `dispatch_pipeline.py:5690-5701` — `_v328_eval_safe` catch-all połyka fail-loud → cichy drop [P1, SOURCE, OPEN]
```
5693   try:
5694       return ('ok', cid, _v327_eval_courier(cid, cs))
5695   except Exception as _e:                          # ← łapie WSZYSTKO: (0,0)-raise, NameError, KeyError, bug
5696       log.error(f"V328_CP_SOLVER_FAIL_PER_COURIER cid={cid} ...", exc_info=True)
5701       return ('fail', cid, _e)                     # → continue → kurier NIE w candidates
```
- **Neutralizuje fail-loud #81:** haversine RAISE (świadomy, głośny) zostaje zamieniony na **per-courier cichy drop**. Fail-loud→fail-silent-at-pool.
- **Nie odróżnia 3 światów:** (a) data-poison `(0,0)`, (b) realna niefeasybilność, (c) PRAWDZIWY bug w `_v327_eval_courier` (latentny NameError/KeyError). Wszystkie → ten sam cichy drop. To DOKŁADNIE klasa, która ukryła incydent 03.05 (CLAUDE.md V3.27.6 „rano": NameError `order` nie w scope → 60s damage; fix #28-Fix1 dodał ten per-courier except, ale kosztem „złe kursy znikają po cichu").
- **BRAK eskalacji:** `V328_POOL_PARTIAL_FAIL` (l.5726) = `log.warning` only — **zero `send_admin_alert`, zero KOORD, zero verdict-change** (potwierdzony grep: jedyni konsumenci `_v328_failed_couriers` to telemetria + mass-fail-fallback). Order leci dalej z ocalałymi; operator nie wie, że feasible kurier zniknął.
- Tylko ≥50% (`V328_MASS_FAIL_RATIO_THRESHOLD`) → heurystyka (l.5738, `log.critical`, też bez alertu operatora). Częściowy drop (1/10) = całkowicie niewidoczny.
- **dedup_hint:** K5-coord-sentinel + C-HARD-bypass (catch-all w hot-path); 119× `except Exception` w pliku — TO jest decyzyjnie krytyczny.

### M-3 — `dispatch_pipeline.py:3133-3135` + `:3470` — produkcja sentinela `(0,0)` bez fail-loud [P2, SOURCE, OPEN]
```
3133   pickup_c = _repair_bag_coords(d, "pickup") or pickup_c or (0.0, 0.0)
3135   deliv_c  = _repair_bag_coords(d, "delivery") or deliv_c or (0.0, 0.0)
3470   delivery_coords = tuple(order_event.get("delivery_coords") or (0.0, 0.0))
```
- **Tu sentinel jest WYTWARZANY.** Komentarz l.3127-3129 sam przyznaje: „(0,0) zostaje gdy repair zawiedzie — wtedy guard OSRM (table/route) sentineluje JAWNIE" — ale to **tylko** ścieżka osrm; ścieżka `haversine` (M-1) **raisuje**, nie „jawnie sentineluje".
- Produkcja `(0,0)` jest CICHA (żadnego warning przy fallbacku do `(0,0)`), awaria odroczona do losowego downstream-haversine. Anty-wzorzec „sentinel zamiast fail-fast w punkcie produkcji".
- New-order pickup JEST chroniony (l.3450 SKIP `no_pickup_geocode`), ale new-order **delivery** (l.3470) i **bag** (l.3133-3135) — NIE: lądują jako `(0,0)` w `OrderSim`.
- **dedup_hint:** K5-coord-sentinel (root, punkt-produkcji).

### M-4 — `dispatch_pipeline.py:2147-2151` — `_compute_repo_cost_km` truthy-guard + lokalny połyk → kara znika [P2, SOURCE, OPEN]
```
2147   if not drop_coords:           # ← (0,0) truthy → NIE łapie
2148       return None, None
2149   return round(haversine(tuple(drop_coords), tuple(pickup_coords)), 2), last_oid
2150   except Exception:
2151       return None, None          # ← (0,0)-raise połknięty LOKALNIE → repo_km = None
```
- Inaczej niż M-1: tu raise jest łapany **lokalnie** (kurier PRZEŻYWA), ale **kara dead-headu CICHO znika** (repo_km=None). Klasa M „cicha awaria → OPTYMISTYCZNA zła dana": kandydat z zatrutym workiem wygląda **tańszy** niż jest → faworyzowany w selekcji. Sprzężenie z P0-A audytu alokacji (selekcja ślepa geometrycznie — tu jeszcze i kłamliwie optymistyczna).
- **dedup_hint:** K5-coord-sentinel (objaw-optymizm) + sprzężenie z rodziną alokacji.

### M-5 — `osrm_client.py:542-575` (`route`) / `:_invalid_coord_result` — sentinel 9999/113 min jako „dana" + log rate-limited [P2, SOURCE, OPEN — TWIN M-1]
```
544   sentinel_min = OSRM_INVALID_COORD_SENTINEL_MIN
570   if ENABLE_OSRM_COORD_GUARD and not (coords_in_bbox(from_ll) and coords_in_bbox(to_ll)):
573       _coord_guard_log(...)                    # rate-limited: pierwsze 20×, potem co 100×
575       return _apply_traffic_multiplier(_invalid_coord_result(now), now)  # duration=sentinel_min
```
- DRUGIE ujście tego samego `(0,0)`: gdzie haversine raisuje, osrm `route/table` **zwraca sentinel-duration** (9999 min w `table`, `OSRM_INVALID_COORD_SENTINEL_MIN` w `route`). Ta liczba **wpływa jako realny leg** do route_simulator → plan infeasible → kurier cicho wycięty (nie crash, „po prostu niefeasible").
- `_coord_guard_log` (l.534-539) **rate-limited** → po 20. wystąpieniu loguje co 100. → **~99% zdarzeń coord-poison na tej ścieżce jest NIELOGOWANYCH** (14456 to już PO rate-limicie). Cicha awaria danych.
- Ryzyko 9999-min jako „dana": gdyby leg z sentinelem przeciekł do metryki uśrednianej / `predicted_delivered_at` (fake daleka przyszłość) — poison liczbowy. Tu głównie → infeasible (bezpieczniej niż M-1), ale niewidoczny.
- **dedup_hint:** K5-coord-sentinel (bliźniak ujścia osrm vs haversine — klasa B w M).

### M-6 — `dispatch_pipeline.py:3675-3677` + `:3911-3914` — lokalne `except: continue/None` połykają bundle-detect [P3, SYMPTOM, OPEN]
```
3675   try: dist = haversine(tuple(bag_pc), pickup_coords)
3676   except Exception: continue          # bundle L2 nearby-pickup cicho pominięty
3912   try: _l2_anchor_dist = haversine(_anchor.location, pickup_coords)
3913   except Exception: _l2_anchor_dist = None   # „po odbiorze z X" cicho nie-ustalony
```
- Te haversine SĄ lokalnie owinięte (kurier przeżywa), ale **bundling cicho znika** gdy bag/anchor zatruty → kandydat scorowany jakby bez bundla (utracona szansa kolokacji). Objaw tego samego `(0,0)`-rootu, niska waga.
- **dedup_hint:** K5-coord-sentinel (objaw bundle).

### M-7 — `dispatch_pipeline.py:1853/1890/1917` — `score = NEG_INF (-1e9)` sentinel [P3, SOURCE, MOSTLY-PATCHED]
```
1853   NEG_INF = -1e9
1890   cand.score = NEG_INF          # ramp off-profile block
1917   cand.score = NEG_INF          # bag hard-skip
```
- Magic-number `-1e9` jako score. **Wyciek do analityki JUŻ załatany** (Z-18, l.1891-1898: `v325_new_courier_penalty=None`, powód jako jawna etykieta `v325_skipped_reason`, NIE `-1e9` w polu penalty).
- **Reszta-ryzyka:** raw `score=-1e9` DALEJ serializowany do `shadow_decisions.jsonl` (`shadow_dispatcher.py:282 "score": c.score`). **W repo BRAK agregacji** score (grep `mean(|sum(.*score|statistics.` = PUSTE) → zero żywego poison-math. Ryzyko zewnętrzne: instrument/Faza C liczący mean/percentyl po polu `score` dostanie `-1e9` (kłamie). SOLO-GUARD (l.1960) przywraca pre_block gdy wszyscy < MIN_PROPOSE → sentinel nie wpycha w fałszywy KOORD.
- **dedup_hint:** M-magic-score (osobny od coord-sentinela; w większości zmitygowany).

### NIE-findingi (sprawdzone, guarded — deklaracja pokrycia, nie cisza)
- `route_simulator_v2.py:65/69` `float("inf")` (first_drop_arrival brak sequence) — guarded init dla sortu; **NIE** wpada w matematykę. OK.
- `route_simulator_v2.py:153` `_o2_primary` None→`inf` (o2_score nieobliczony→sort-na-koniec) — guarded przez `_under_z if _under_z else plans` fallback. Cichy „uncomputed=worst" ale bezpieczny. OK (granica M, niska waga).
- `route_simulator_v2.py:896/911` `(10**9, float("inf"))` init wyszukania wstawienia — guarded `if best_d_pos is not None` (l.~907). Nie przecieka. OK.
- `feasibility_v2.py:108-114` `_road_km`/`_valid` — feasibility JEST defensywna: **każde** wywołanie `_road_km` (l.182/204/242/281/474/518/592/598-599) bramkowane `_valid()` (`coord != (0,0) and coord[0] != 0.0`). Feasibility **nie** jest źródłem V328. OK.
- `geometry.py:11-20` + `osrm_client.py:399-419` haversine — fail-loud #81 dla None/(0,0) DZIAŁA (raisuje). Problem nie w haversine — w jego **bezguardowych callerach** (M-1) + catch-all (M-2).
- `tsp_solver.py:223/232` `V328_TSP_SETRANGE_NAN_INF/OOD` — to JAWNE guardy NaN/Inf w SetRange (głośne, log+skip). Dobry wzorzec, nie finding.
- `scoring.py:79-82` `except` (fleet_context.overload_delta) + `return 0.0` przy braku — fail-open do neutralnego 0, udokumentowane Lekcja #32. Granica M, ale neutralny default (nie optymistyczny w stronę propozycji). Niska waga.

---

## 3. DEDUP — do którego rootu się zwija

| Instancja | Root | Klasa | Uwaga anty-double-count |
|---|---|---|---|
| M-1, M-2, M-3, M-4, M-5, M-6 | **K5 — coord-sentinel `(0,0)`** | M (+B na M-5, +C na M-2) | TEN SAM root co A6 cross-cutting „sentinele jako most" + floor-audit BUG#2. **NIE liczyć jako 6 chaosów** — jedna prawda: brak chokepointu walidacji coords + catch-all połyka fail-loud. |
| M-7 | M-magic-score `-1e9` | M | Osobny anty-wzorzec (nie coord). W większości zmitygowany (Z-18). |

**Most do innych grup (A6):** K5 zasila position-twins (A6 gr.3b — `_SYNTH_POS` traktuje pin/pre_shift jak fikcję) i floor (A6 gr.6 — `BIALYSTOK_CENTER` fiction dla no_gps). To samo „sentinel-jako-dana". **Distinct root w rollupie ziomek-unified-audit = K5** (NIE R1-R5 z allocation-family rollupu — tamte to selekcja/route/anchor/floor; K5 jest osią ortogonalną „zatrute dane jako wartownik").

**Relacja do allocation-family audytu:** M-4 (repo-km cicho znika) i M-1/M-2 (drop feasible kuriera) **wzmacniają P0-B** (mała pula pod scarcity — bo couriers znikają) i **P0-A** (selekcja optymistyczna — bo repo-km=None). To NIE duplikat P0-A/B, to ich UKRYTY wzmacniacz w warstwie danych.

---

## 4. FIX-U-ŹRÓDŁA (szkic — NIE wykonany, runda diagnozy)

1. **Jeden chokepoint walidacji coords** przed KAŻDYM haversine w eval-path: zamienić truthy-guard `if coords:` (M-1 `:4822`, M-4 `:2147`) na `_valid(coords)` (już istnieje w feasibility) LUB sanitize-do-fail-loud-przy-ingest. Tknąć WSZYSTKIE bezguardowe callery RAZEM (`:4823`, `:2149`, + zweryfikować `:3912/3675` że to świadomy skip).
2. **M-2 catch-all musi rozróżniać:** data-poison `(0,0)` (→ napraw dane / pomiń order głośno) vs realny bug (→ NIE połykać per-courier, to maskuje regresje). Min.: gdy ≥1 drop z `ValueError sentinel (0,0)` → **operator-visible** sygnał (KOORD/alert „zatruty adres w worku kuriera X"), nie tylko `log.warning`.
3. **M-3 punkt-produkcji:** fallback `or (0.0,0.0)` zamienić na fail-loud/SKIP (jak new-order pickup `:3450`) albo na re-geokod z twardym markerem „data_quality_issue", żeby downstream wiedział.
4. **M-5 rate-limit:** policzyć ile UNIKALNYCH adresów-trucizn (nie 14456 zdarzeń) — `geocode-centroid guard` (ENABLE_BUNDLE_COLOC_CENTROID_GUARD ON) i 122 zatrute adresy z MEMORY to ten sam korpus; domknąć u źródła geokodu, nie w 17 konsumentach.
5. **M-7:** raw `score=-1e9` w serializerze → zastąpić `None` + jawnym `skipped_reason` (jak penalty już zrobione Z-18), żeby Faza C nie liczyła mean po `-1e9`.

> Wszystko za protokołem ETAP 0→7, flaga ON≠OFF, parytet bliźniaków (M-1 haversine ↔ M-5 osrm = ten sam `(0,0)`, MUSZĄ iść razem), HARD R6 nietknięty. **To runda DIAGNOZY — STOP przed kodem.**

---

## 5. TABELA POKRYCIA (jawne luki, nie cisza)

| Moduł | Sprawdzone | Metoda | Wynik |
|---|---|---|---|
| `dispatch_pipeline.py` (SEL L7 + eval-path 3440-5840) | ✅ hot-path | Read + grep + live log | M-1,2,3,4,6,7 |
| `feasibility_v2.py` | ✅ `_valid`/`_road_km` callers (30) | grep + Read | defensywne (nie-finding) |
| `route_simulator_v2.py` | ✅ inf/(10^9) sentinele | Read 55-72,148-156,892-915 | guarded (nie-finding) |
| `osrm_client.py` | ✅ haversine + route/table COORD_GUARD | Read 399-419,523-592 | M-5 + fail-loud #81 OK |
| `geometry.py` / `pipeline_geometry.py` | ✅ haversine_km guard | grep + Read | fail-loud OK (nie-finding) |
| `scoring.py` | ✅ jedyny except + sentinele | Read 74-85 + grep | neutralny default (granica M) |
| `tsp_solver.py` | ✅ V328_TSP guardy | grep | jawne guardy (nie-finding) |
| `objm_lexr6.py` | ✅ 0 except, 0 sentinel | grep | czyste |
| `_compute_repo_cost_km` | ✅ | Read 2108-2151 | M-4 |
| `shadow_dispatcher.py` (serializacja score) | ✅ `"score": c.score` | grep | karmi M-7 |

**LUKI (jawne):**
1. **`plan_recheck.py` 37× `except Exception`** — POLICZONE, nie zinspektowane ciało-po-ciele. Plan-recheck regeneruje kanon co 5 min (K2 „cofacz"); jeśli któryś except połyka błąd kanonu → cichy drift kolejności. **Faza B/E: przejrzeć 37 ciał pod kątem połykania (szczególnie wokół `_apply_canon_order_invariants:1478`).** Poza budżetem tego agenta (fokus SEL+ROUTE+FEAS hot-path).
2. **119× `except Exception` w dispatch_pipeline** — zinspektowane TYLKO hot-path eval (V328) + sentinel-sites. Pozostałe ~110 (telemetria/serializacja/fail-soft logi) NIE przejrzane 1:1 — większość to udokumentowane fail-soft (`_append_difficult_case_log:224`, loadgov, calib), ale pełny audyt = osobny przebieg.
3. **`plan_manager.py` (4) / `pln_objective.py` (5) / `pending_pool.py` (3) except** — policzone, nie zinspektowane (poza gorącą ścieżką selekcji-tie-break; pln_objective shadow-only).
4. **Dokładna linia-culprit pozostałych 1900+ V328** — pinned `:4823` dla dzisiejszej sygnatury (`_last_drop`), ale historyczne V328 mogły iść z `:3942`(sanitized)/`:4044`(except-branch)/insertion_anchor. Strukturalny finding (brak chokepointu) niezależny od dokładnej linii każdego z 2046.
5. **Cross-repo M-sentinele** (`fleet_state.py`, `courier_orders.py` — BIALYSTOK_CENTER/(0,0) w renderze) = poza SEL+ROUTE+FEAS silnika (J-class / inny agent). Parcel lane (`parcel_*`) nie prześwietlony pod M.
6. **NIE uruchamiałem silnika** (read-only) — mechanizm V328 potwierdzony trace kodu + ŻYWY log + 8 dzisiejszych ofiar, NIE repro. Liczbowy parytet „haversine-ujście vs osrm-ujście" = zadanie Fazy C oracle.

**NIE-luki (świadomie poza zakresem):** Mailek/Papu (granica), flagi efektywne (A3), przyrządy-prawda (A4/Faza C), warstwa renderu konsola/apka (J).
