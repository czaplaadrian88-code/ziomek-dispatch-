# SPEC 2026-06-24 — Pre-shift floor (rygor) + okno/kara pre-shift + parytet konsola=apka=Ziomek

Zlecenie Adriana (po diagnozie case Orthdruk/Michał K 393, konsola 9:36 vs umówione 10:15 vs zmiana 10:00).

## Wymagania (ground truth od Adriana)
1. **RYGOR: żaden odbiór przed startem zmiany.** Odbiór (czas pokazywany, committed `czas_kuriera`, plan, ETA) nigdy < `shift_start`.
2. **Parytet PEŁNY konsola = apka = Ziomek** — ta sama kolejność stopów, te same czasy, ta sama trasa, te same ETA dostaw. Audyt 1:1 i domknięcie PRZED flipem.
3. **Pula pre-shift: okno do 60 min, kara gradientowa.**
   - `m` = minuty do startu zmiany.
   - `m ≤ 30` → **lekka kara** (chętnie brany; „≤30 dobrze, poniżej super"). Cel: restauracja nie czeka rano.
   - `30 < m ≤ 60` → **potężna kara, odblokowywana TYLKO przy dużym przeładowaniu floty** (inaczej praktycznie niedostępny).
   - Bilans: lepiej kurier weźmie do baga ~20 min i wiezie 20–25 min, niż restauracja czeka 40–50 min rano. Zacisk #1 i tak trzyma odbiór ≥ start zmiany.

## Stan obecny (zweryfikowany 24.06, read-only)
- **Ścieżka propozycji OK:** `dispatch_pipeline.py:3954` ETA odbioru pre-shift = `now + shift_start_min` (= start zmiany); kara `feasibility_v2.py:761` `V325_PRE_SHIFT_SOFT_PENALTY=-20` (strefa 0–30) → `dispatch_pipeline.py:1507`. Dlatego committed `czas_kuriera`=10:15 wyszło poprawnie.
- **Luka A (okno):** `courier_resolver.py:1544-1549` z `ENABLE_V324A_SCHEDULE_INTEGRATION` (ON) wpuszcza pre-shift dla DOWOLNEGO `mins>0` (brak capu). Working-override gałąź `:1503-1509` analogicznie.
- **Luka B (plan):** `plan_recheck._start_anchor` (`plan_recheck.py:486-516`) nie ma gałęzi pre-shift → przy świeżym GPS `earliest_departure=None` → zapisany plan startuje „teraz" → `predicted_at` odbioru = now+jazda (9:36). `route_simulator_v2.py:250-254` obsługuje `earliest_departure`, tylko nikt go nie podaje.
- **Luka C (konsola):** `fleet_state._build_route` (`PIN_AGREED_PICKUP_TIME` ON) pinuje surowy `predicted_at` z planu; **brak** zacisku „odbiór ≥ czas_kuriera". Apka TEN zacisk MA: `courier_orders.py:641 _committed_pickup_eta` (`FROZEN_PICKUP_ETA`) → `max(predicted, czas_kuriera)`. To jest rozjazd konsola≠apka.
- **Sygnał przeładowania:** `dispatch_pipeline._loadgov_compute` → `loadgov_load_ewma` (state `_LOADGOV_STATE`), progi `LOADGOV_TIGHTEN_AT=2.7`, `LOADGOV_DEFENSIVE_AT=3.5`.

## Zmiany

### #1 Rygor floor (odbiór ≥ shift_start) — defense-in-depth
- **plan_recheck._start_anchor**: nowa gałąź pre-shift — gdy kurier ma dzisiejszą zmianę z `shift_start > now`, `earliest_departure = max(shift_start, committed_pickup)` NIEZALEŻNIE od świeżości GPS. (mirror `dispatch_pipeline:3954` / `feasibility_v2:793`). Wymaga przekazania shift_start do plan_recheck (czyta grafik jak reszta — `load_schedule`).
- **feasibility_v2 Gate 3** (`:746-764`): strefa miękka 0–30 zostaje jako kara, ale dochodzi **twardy floor**: jeśli po symulacji pickup_ref < shift_start → podnieś do shift_start (z zaciskiem i tak nie spada; to pas bezpieczeństwa). Flag `ENABLE_PRE_SHIFT_PICKUP_HARD_FLOOR` (default ON).
- **Renderery (konsola+apka)**: finalny zacisk `ETA_odbioru = max(predicted_at, czas_kuriera, shift_start)`. Apka: dołożyć `shift_start` do istniejącego `_committed_pickup_eta`. Konsola: dodać cały zacisk.

### #2 Parytet konsola=apka=Ziomek (pełny, przed flipem)
- Harness `tools/console_vs_app_route.py` (rozszerzyć szkielet z `eod_drafts/2026-06-18/`): dla każdego aktywnego kuriera weź plan silnika → renderuj regułami konsoli (`fleet_state`) i apki (`courier_orders`) → diff: sekwencja, typ stopu, `predicted_at` per stop, dwell, ETA dostaw, nogi trasy.
- Domknąć każdą klasę rozjazdu. Znana #1: konsola bez committed-pickup clamp → dodać (jak apka). Reszta z raportu harnessa.
- **Bramka wyjścia: diff = 0 na żywej flocie.**

### #3 Okno + kara pre-shift
- **courier_resolver**: cap okna — wpuszczaj pre-shift tylko gdy `0 < mins ≤ PRE_SHIFT_WINDOW_MIN` (=60). Dotyczy obu gałęzi (V324A `:1544`, working-override `:1503`).
- **Kara gradientowa** (w scoring, gdzie jest `loadgov_load_ewma` + `shift_start_min`, czyli `dispatch_pipeline`):
  - `m ≤ PRE_SHIFT_NEAR_MIN` (=30): `pen = PRE_SHIFT_NEAR_PEN_PER_MIN * m` (np. -1.0/min → 0..-30). Lekka.
  - `PRE_SHIFT_NEAR_MIN < m ≤ PRE_SHIFT_WINDOW_MIN`: `pen = PRE_SHIFT_FAR_PEN` (np. -250, ~veto) JEŻELI `loadgov_load_ewma < PRE_SHIFT_FAR_UNLOCK_LOAD`; w przeciwnym razie relaks do umiarkowanej kary (np. kontynuacja gradientu ~-30..-60). Próg odblokowania `PRE_SHIFT_FAR_UNLOCK_LOAD` (start 3.5 = DEFENSIVE; do kalibracji).
  - Zastępuje stałe `V325_PRE_SHIFT_SOFT_PENALTY=-20`. Flag `ENABLE_PRE_SHIFT_GRADIENT_PENALTY`.

## Stałe (common.py, env-overridable; kalibracja przed flipem)
```
PRE_SHIFT_WINDOW_MIN = 60
PRE_SHIFT_NEAR_MIN = 30
PRE_SHIFT_NEAR_PEN_PER_MIN = -1.0
PRE_SHIFT_FAR_PEN = -250.0
PRE_SHIFT_FAR_UNLOCK_LOAD = 3.5
```
Flagi: `ENABLE_PRE_SHIFT_PICKUP_HARD_FLOOR`, `ENABLE_PRE_SHIFT_GRADIENT_PENALTY` (silnik), zacisk konsoli/apki za istniejącymi `PIN_AGREED_PICKUP_TIME`/`FROZEN_PICKUP_ETA` + nowy floor flag.

## Sekwencja (flipy POZA peakiem 11–14 / 17–20; per-krok ACK)
1. **Silnik SHADOW**: plan_recheck clamp + okno/kara — log-only (shadow plan vs live plan): ile odbiorów przesuwa się do ≥ shift, ile far-zone zbramkowanych przez loadgov. Replay 16.05/21-24.06.
2. **Harness parytetu** na żywo → fixy rendererów → diff=0.
3. Flip silnik (off-peak) → weryfikacja.
4. Flip rendererów konsola+apka (off-peak) → konsola==apka na żywo.

## Bezpieczeństwo
Per-krok `.bak`→edit→`py_compile`→testy→commit+tag→shadow→ACK→flip. Każda zmiana za flagą (rollback ~5s). Repo rozdzielne: dispatch_v2 (silnik), nadajesz_clone/panel (konsola), courier_api (apka) — commity per ścieżka, backup przed nadpisaniem (multi-session shared deploy).
```
```
