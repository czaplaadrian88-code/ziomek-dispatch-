# Sprint OBJ F4 — pozycja kuriera bez GPS: design (2026-05-18)

F1/F2/F3 DONE & LIVE. F4 = ostatnia faza planu OBJ. Plan v2 wyznacza dla F4
jawną bramkę: **„ACK → osobny design przed implementacją (większy, ryzykowny)"**.
Ten dokument = ten design. Implementacja = świeża sesja po ACK Adriana.

## Problem (L1 diagnozy 474266)
Kurier bez świeżego GPS → `courier_resolver.build_fleet_snapshot` krok 2:
`cs.pos = delivery_coords` ostatniego **picked_up** ordera, `pos_source=
last_picked_up_delivery`. Model stawia kuriera W NIEODWIEDZONYM JESZCZE dropie.

Realnie kurier jest W TRASIE do tego dropu — często bliżej kolejnego pickupu niż
proxy sugeruje. 474266: kurier odebrał na Borsuczej, jest ~2 min od Siouxa, ale
proxy mówi „stoi pod Borsuczą (peryferie)" → solver liczy jazdę Borsucza→Sioux
≈8 min → frozen window [0, 6.1] INFEASIBLE → kaskada (retry bez okien → V3274
reject → greedy `lock_first` ślepy). Skala: ~7,5k INFEASIBLE/dzień — proxy to
**główny winowajca** strukturalnego wyłączenia OR-Tools.

Dlaczego F4 jest fundamentem: nawet idealny objective (F1-F3) optymalizuje na
ZŁYCH WEJŚCIACH — `cs.pos` zafałszowane → cała macierz odległości skażona.

## Stan obecny — `courier_resolver` priorytet pozycji
1. GPS fresh (`age < GPS_FRESHNESS_MIN`) → `pos_source=gps`.
2. Aktywny bag: najnowszy `picked_up` → `delivery_coords` (`last_picked_up_delivery`);
   `assigned` → `pickup_coords` (`last_assigned_pickup`). **← tu siedzi L1.**
3. Recent activity (<30 min `delivered_at`/`picked_up_at`) → `delivery_coords`.
4. Fallback `BIALYSTOK_CENTER` (`no_gps`).

`cs.pos` zasila dalej `dispatch_pipeline` (m.in. `effective_start_pos`, macierz
solvera, `km_to_pickup`). Krok 2 dotyczy ~? floty (do zmierzenia — grep
`pos_source` w `shadow_decisions`).

## Klucz: jaki punkt + czas są PEWNE
`picked_up_at` = timestamp gdy kurier był przy **pickup_coords** (restauracja),
nie delivery. Obecny proxy bierze `delivery_coords` (gdzie kurier dopiero
DOJEDZIE) — to ekstrapolacja w przyszłość, nie estymata teraźniejszości.
Ostatnie pewne: kurier był w `pickup_coords` o `picked_up_at`.

## Opcje (rosnące ryzyko/wierność)

### Opcja A — proxy = `pickup_coords` ostatniego picked_up (minimal)
Krok 2 `picked_up`: `cs.pos = pickup_coords` zamiast `delivery_coords`.
- + Kurier BYŁ tam (o `picked_up_at`) — punkt rzeczywisty, nie ekstrapolacja.
  Restauracje zwykle centralniejsze niż peryferyjne dropy → mniejszy bias.
- + ~5 LOC, zero nowych zależności, niskie ryzyko.
- − Statyczny — gdy `picked_up_at` stare (kurier dawno odjechał), pickup też
  myli. Nie modeluje ruchu.

### Opcja C — interpolacja na nodze pickup→delivery (hybryda)
`cs.pos` = interpolacja liniowa `pickup_coords → delivery_coords` o frakcję
`elapsed / eta_leg`, gdzie `elapsed = now − picked_up_at`, `eta_leg` = OSRM
pickup→delivery. Frakcja clamp [0,1].
- + Modeluje ruch po realnej nodze; bez potrzeby pełnego planu kuriera.
- + Degraduje gracefully: elapsed≈0 → przy pickupie; elapsed≥eta → przy dropie.
- − 1 wywołanie OSRM w hot-path resolvera per kurier-no-gps (cache łagodzi).
- − Interpolacja liniowa po linii prostej ≠ realna trasa (przybliżenie kierunku).

### Opcja B — chain-eta interpolacja po pełnym planie (proper, ryzykowna)
Użyj `courier_plans.json` (V3.19b saved plans): od ostatniego pewnego punktu+czasu
przejdź planowaną sekwencję przy oczekiwanej prędkości, ustaw `cs.pos` na bieżącej
nodze. Najwyższa wierność.
- − Wymaga wpięcia `courier_plans` + OSRM legów do `courier_resolver` (dziś nie
  ma). Duża powierzchnia, hot-path, ryzyko latency. Plan bywa stale/nieaktualny.

## Rekomendacja
**Opcja C jako cel, Opcja A jako krok 1 (bezpieczny baseline).**
1. **Krok 1 (A):** flip krok-2 picked_up na `pickup_coords`. Flaga
   `ENABLE_F4_COURIER_POS_PICKUP_PROXY`. Natychmiastowa redukcja biasu 474266
   (pickup Borsuczej bliżej Siouxa niż drop Borsuczej), zero nowych zależności.
   Shadow-observe — zmierz spadek `INFEASIBLE windowed` w `route_simulator.log`.
2. **Krok 2 (C):** interpolacja pickup→delivery po `elapsed/eta_leg`. Osobna
   flaga `ENABLE_F4_COURIER_POS_INTERP`. OSRM przez istniejący `osrm_client`
   cache. Po shadow-verify Kroku 1.
- Opcja B odłożona — przewaga nad C nie uzasadnia wpięcia `courier_plans` do
  resolvera; rozważyć dopiero gdy C empirycznie niewystarczające.

## Ryzyka
- `courier_resolver.build_fleet_snapshot` to hot-path — latencja (Krok 2: OSRM).
  Benchmark wymagany; cache OSRM mityguje.
- `cs.pos` zasila WSZYSTKIE odległości — błąd proxy skażą każdy solve. Flaga +
  shadow-observe + replay obowiązkowe; deploy etapami (A potem C).
- Picked_up bez `delivery_coords`/`pickup_coords` (P0.4 data quality, już
  logowane) — interpolacja musi fail-soft do statycznego punktu.
- `pos_source` ma trafić do `shadow_decisions` (objm/serializer) — nowe wartości
  `last_picked_up_pickup` / `interp_leg` widoczne w logu (Lekcja #109).

## Test/rollout
- Replay 474266 (fixture w `obj_harness` FAITHFUL_CASES) — Krok 1: jazda
  courier→Sioux spada z ~8 do ~2-3 min, windowed solve FEASIBLE.
- Unit: interpolacja (elapsed=0 → pickup; elapsed≥eta → delivery; clamp).
- Regresja `courier_resolver` + `feasibility` + `obj_*`.
- Flaga default OFF, env ON po replay-pass; restart `dispatch-shadow` off-peak;
  shadow-observe 24h (rozkład `pos_source`, `INFEASIBLE windowed` rate).

## Effort / ACK
- Krok 1 (A): ~1h + replay + shadow. Krok 2 (C): ~3-4h + benchmark latencji +
  replay + shadow. Razem zgodne z planem „0.5-1 dnia".
- **ACK Adriana na:** (1) ścieżkę A→C (vs od razu C, vs B), (2) start
  implementacji Kroku 1. Świeża sesja — `courier_resolver` to wrażliwy rdzeń,
  nie na końcu długiej sesji (Z2 + cognitive-fatigue).

## DECYZJA Adriana 2026-05-18
**Ścieżka A→C — ACK.** Implementacja = świeża sesja (NIE w tej, długiej).
Następna sesja: zacznij od **Kroku 1 (Opcja A)** — flip krok-2 `picked_up`
w `courier_resolver` na `pickup_coords`, flaga `ENABLE_F4_COURIER_POS_PICKUP_PROXY`,
replay 474266 (windowed solve FEASIBLE), shadow-observe spadek `INFEASIBLE
windowed`. Po shadow-verify → Krok 2 (Opcja C, interpolacja). Opcja B poza zakresem.

## STATUS 2026-05-19 — Krok 1 DONE & LIVE
Krok 1 zaimplementowany i wdrożony — commit `7098fee`, tag
`obj-f4-k1-courier-pos-pickup-proxy-2026-05-18`, restart `dispatch-shadow`
18.05 23:32 UTC, env `override.conf` `ENABLE_F4_COURIER_POS_PICKUP_PROXY=1`.
Testy 16/16 + regresja 84/84.

**Korekta predykcji (replay na kodzie post-E1-E3):** kaskada INFEASIBLE→greedy
już nie istnieje (E1-E3 ją naprawiło). F4 nie „daje FEASIBLE" — daje uczciwą
pozycję, przez co solver UJAWNIA realny breach R6 carry-ordera, który stary
proxy maskował (replay 474266: r6_breach 12.9→21.5). F4+F3 razem eskalują
ciasne R6 do KOORD. Metryka shadow-verify zmieniona z „spadek INFEASIBLE"
na rozkład `pos_source` + F4-fire count + KOORD-rate (at-job #54, wt 19.05
21:00 UTC, `verify_obj_f4_2026-05-19.py`). Szczegóły: lekcja #130.

**Krok 2 (Opcja C):** start po werdykcie at-job #54.
