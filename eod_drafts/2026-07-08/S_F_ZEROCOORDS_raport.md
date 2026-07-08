# SPRINT F — NAPRAWA źródła (0,0)/COORD_GUARD (2026-07-08)

**Sesja tmux 41. Worktree `wt-zero-coords`, branch `fix/zero-coords-source`, baseline master `8760ee6`.**
**Zmiana za flagą OFF — NIC nie flipnięte/zrestartowane. flags.json NIETKNIĘTY. Wyłącznie kod+testy w worktree.**

## 1. DIAGNOZA (F1) — ŹRÓDŁO (0,0) namierzone i udowodnione reprodukcją

**Klasa zleceń:** paczki **firmowe** (`address_id=161`, restauracja „Nadajesz.pl", pickup „Piasta 13"). ~5–7/dzień.

**Łańcuch (fix U ŹRÓDŁA, nie łatka na guardzie):**
1. Parser uwag NIE wyciąga adresu nadawcy z paczkowego formatu uwag (P3 edge) → **świadomy `REJECT+FLAG`** (`ENABLE_FIRMOWE_REJECT_ON_GEOCODE_FAIL`, „nie podstawiam centrali") → `pickup_coords=None` **utrwalone** w orders_state (intencja: → KOORD). Potwierdzone w logu: `NEW_ORDER 486243/246/259 firmowe-konto aid=161: parser zwrócił None (P3 edge) — REJECT+FLAG`.
2. Mimo to zlecenie zostaje **przypisane** kurierowi (486259: NEW 09:37 → ASSIGNED cid=370 09:42).
3. Gdy taki order jest w worku kandydata i ma status **`assigned` (jeszcze NIE `picked_up`)**, `dispatch_pipeline._bag_dict_to_ordersim` próbuje runtime re-geokod (`_repair_bag_coords` → `geocode_restaurant('Nadajesz.pl','Piasta 13')`). **Gdy geokod padnie** (sieć/TTL 2 s pod obciążeniem peaku) → cichy fallback **`(0.0, 0.0)`**.
4. `route_simulator_v2` dokłada węzeł pickup nowego węzła (0,0) → `osrm_client.table(points, points)` → **COORD_GUARD** (sentinel 9999 → holder cicho wykluczany z puli = geometria-ślepy pile-on, resztkowa choroba L2.1).

**Dlaczego peak-only + sygnatura zawsze `table 2 invalid [(0,0),(0,0)]`:** 1 zły węzeł w `table(points,points)` → występuje w origins+destinations = dokładnie 2. Trafienia 11:00–12:58, potem 0 (te zlecenia dostarczone). Emitery: `dispatch.log`, `reassignment_forward_shadow`, `pending_global_resweep`, `reassignment_global_select`, `czasowka` — WSZYSTKIE wołają `assess_order`→`core.candidates`→`_bag_dict_to_ordersim` (jedno źródło).

**Reprodukcja (oracle, isolated route_simulator + przechwycony table()):**
| przypadek | OrderSim pickup | COORD_GUARD hits |
|---|---|---|
| firmowe `picked_up` (repair OFF) | (0,0) ale **węzeł pickup pominięty** | **0** |
| firmowe `assigned` + repair OK | (53.13,23.18) realny | 0 |
| firmowe `assigned` + repair FAIL | **(0,0)** | **1** ← źródło |
| firmowe jako NOWE (pickup=None) | — | 0 (bramka `geocode_defense`→SKIP/KOORD łapie) |

**Co ODRZUCONE jako źródło (dowody):** nowe zlecenie (bramka geocode_defense łapie None→KOORD); plan-stop placeholder (0,0) w courier_plans.json (`_save_plan_on_assign` K5b) — plan_recheck guarduje coords (`_coords_ok`→early-return), NIE dociera do table(); `_bag_dict_to_ordersim` dla firmowe z pełnym rekordem repairuje (restaurant obecny). Telemetria `coord_poison_bag_oids` MIJA tę klasę (czyta raw=None, a (0,0) wstrzykiwane dopiero w OrderSim) — tłumaczy „poison=0" w werdykcie at-201 mimo żywych trafień.

## 2. FIX (F2) — twardy fallback firmowe (decyzja Adriana, opcja A, 2026-07-08)

- **`common.py`**: nowa flaga `ENABLE_FIRMOWE_BAG_COORD_FALLBACK = False` (const OFF=legacy) + rejestracja w `ETAP4_DECISION_FLAGS` (strip conftest + fingerprint + parytet cross-proces).
- **`dispatch_pipeline.py`**: helper `_firmowe_bag_pickup_fallback(d)` — flaga ON + `aid∈FIRMOWE_KONTO_ADDRESS_IDS` → `FIRMOWE_KONTO_FALLBACK_COORDS` (centrala Nadajesz, w bbox); flaga OFF / nie-firmowe → `(0.0, 0.0)` (bajt-w-bajt legacy). Wpięty JAKO ostatnia deska ODBIORU: `_repair_bag_coords(d,"pickup") or pickup_c or _firmowe_bag_pickup_fallback(d)`. Log rate-limited `FIRMOWE_BAG_COORD_FALLBACK` (obserwowalność ETAP-5).
- **Zakres świadomie WĄSKI:** tylko ODBIÓR firmowy. Delivery firmowe zawsze geokodowane → zostaje `(0.0,0.0)` legacy (centrala jako DOSTAWA byłaby błędna). Nie-firmowe nierozwiązywalne → `(0,0)` legacy (guard OSRM = backstop). NOWE zlecenia bez zmian (bramka geocode_defense). `route_simulator`/`feasibility`/`ETA`/współbieżność `dispatch_pipeline` NIETKNIĘTE (zakres Sprintu B/D/C/A). **Guard COORD_GUARD ZOSTAJE — przestaje tylko mieć co łapać na tej klasie.**

## 3. MAPA KOMPLETNOŚCI (ETAP 3)
| miejsce klasy (0,0) | dotknięte? |
|---|---|
| `_bag_dict_to_ordersim` pickup `:3450` (jedyny builder bag-OrderSim; tools/sequential_replay też przez niego) | **TAK** (fix) |
| `_bag_dict_to_ordersim` delivery `:3452` | N-D (delivery firmowe zawsze geo; centrala-jako-dostawa błędna) |
| new-delivery `:3928`/`:1622` | N-D (bramka `geocode_defense` łapie pickup=None→SKIP/KOORD przed route_simulator) |
| `panel_watcher._save_plan_on_assign` placeholder (0,0) | N-D (plan_recheck guarduje coords→early-return, NIE dociera do table; K5b głośny placeholder = intencja L2.1) |
| bliźniaki OrderSim w `plan_recheck` (1004/1332/1512/1637/701) | N-D (guardują `_coords_ok`→early-return, NIGDY (0,0)) |
| flaga | ETAP4_DECISION_FLAGS + const OFF + `decision_flag()` |

## 4. DOWODY (ETAP 4/5)
- **Flaga ON≠OFF (behawioralnie):** `tests/test_firmowe_bag_coord_fallback_sprintf.py` 7/7. OFF: OrderSim pickup=(0,0), e2e table hit≥1. ON firmowe: pickup=centrala (w bbox), e2e **0 trafień**. ON nie-firmowe: (0,0). ON repair-OK: realny geokod (fallback nie wchodzi). Delivery: (0,0) legacy.
- **Dowód POZYTYWNEGO wpływu na źródło:** ten sam realny `simulate_bag_route_v2` w obu gałęziach → OFF strzela COORD_GUARD, ON eliminuje → trafienia tej klasy **→ 0**.
- **Checkery flag:** `test_flag_doc_coverage` + `test_flag_registry_f3` ZIELONE (15/15). `test_flag_effect_coverage::test_no_new_untested_decision_flag` — pada TYLKO w worktree (skanuje hardcoded KANON `dispatch_v2/tests`, gdzie testu jeszcze nie ma; gotcha C12(e)/perf-memory). Dowód że pokryty: skan worktree-testów → flaga w txt=True, poza listą untested → **po merge do kanonu przejdzie**.
- **PEŁNA regresja `pytest tests/`: 4496 passed, 2 failed, 27 skipped** (baseline `8760ee6` = 4490 pass, 1 fail). Bilans: 4490 + 7 nowych − 1 (flag_effect flip w worktree) = 4496. **Dwa faile = ZERO realnej regresji:** `test_grafik_fetch_schedule[fetch]` (cudzy/pre-existing, pada też na kanonie) + `test_flag_effect_coverage` (worktree-artefakt, znika po merge — dowód pokrycia w §4).

## 5. ROLLBACK
- Flaga const OFF = domyślnie legacy; ON dopiero po dopisaniu do flags.json (flip = osobny ACK, C2).
- Kod: `git revert <commit>` / `.bak-pre-firmowe-bag-fallback-2026-07-08` (common.py + dispatch_pipeline.py).

## 6. STAN / OTWARTE (za ACK)
- **FLIP** `ENABLE_FIRMOWE_BAG_COORD_FALLBACK=true` w flags.json = pełny deploy (C2): mierzy REALNY spadek COORD_GUARD tej klasy + brak regresji feasibility holdera; okno 2 dni; peak-only więc werdykt po ≥1 peaku. **NIE flipnięte w tej sesji.**
- Uwaga behawioralna do zmierzenia przy flipie: ON zmienia efektywną trasę TRZYMANEGO firmowego z 9999-infeasible na centrala-based → holder przestaje być cicho wykluczany (intencja anty-pile-on L2.1, ale to zmiana → replay ON↔OFF).
- Merge worktree→master: sekwencyjny za ACK (C12c). Po merge: re-run `test_flag_effect_coverage` z kanonu (worktree-artefakt znika).

## Commity
- `<HASH>` — Sprint F: firmowe bag-pickup fallback (flaga OFF) + test + rejestracja ETAP4.
