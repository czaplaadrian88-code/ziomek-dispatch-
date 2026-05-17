# Sprint 3 — tier-aware czas jazdy w planie trasy (DESIGN)

**Data:** 2026-05-17
**Cel:** R6 (bramka BAG_TIME ≤ 35 min) ma liczyć czas bundla **tempem realnego kuriera**,
żeby Ziomek dokładając zlecenie widział, czy słaby kurier zdąży — i nie przepuszczał
over-bundli. Bundle zostają; dobierane pod tempo.

## Diagnoza (podstawa)

Pomiar bundli 13-17.05 (composition-clean — kurier wiózł dokładnie plan):
gold +1.0 / std+ +4.2 / std +5.6 / new +3.8 min niedoszacowania. Headline +13 min
to głównie composition growth (353/734 bundli dostało doklejone zlecenia).

Przyczyny:
1. **R6 czyta tier-blind szacunek czasu** → dla std/slow zaniża → przepuszcza za duże
   bundle / dokładanie.
2. **Plan trasy ślepy na tempo kuriera.** `route_simulator_v2.leg_min` (czas jazdy
   między stopami) = czysty OSRM, ZERO mnożnika tempa. `v326_speed_multiplier`
   (gold 0.889 / std 1.0 / slow 1.111 / new 1.3) zna tempo, ale wpięty tylko w
   scoring + `chain_eta` (a `chain_eta` wyłączone, `ENABLE_V326_R07_CHAIN_ETA=off`).

Sprint 1 (DWELL tier-aware, LIVE 2026-05-17) zaadresował CZĘŚĆ per-stop. Sprint 3
dokańcza: tier-aware także nogi jazdy.

## Architektura zmiany (symetryczna do Sprint 1 DWELL)

`leg_min(i,j)` w `route_simulator_v2.py:367` to closure wewnątrz
`simulate_bag_route_v2` — zwraca `dur_s/60.0`. Wpięcie:

1. **`common.py`** — tabela `DRIVE_SPEED_MULT_BY_TIER` + resolver `speed_mult_for_tier(tier)`.
   Obok `DWELL_BY_TIER` / `dwell_for_tier`.
2. **`route_simulator_v2.py`** — `simulate_bag_route_v2` dostaje param
   `drive_speed_mult: float = 1.0`. Closure `leg_min` mnoży wynik:
   `return (dur_s / 60.0) * drive_speed_mult`. Sentinel 9999.0 (fallback) NIE mnożony.
   Domyślnie 1.0 → zero zmiany zachowania (testy/inne callery nietknięte).
3. **`feasibility_v2.py`** — `check_feasibility_v2` ma już `courier_tier`; rozwiązuje
   `drive_speed_mult = C.speed_mult_for_tier(courier_tier)`, przekazuje do
   `simulate_bag_route_v2`. Metryka `drive_speed_mult` do obserwowalności.
4. **`dispatch_pipeline.py`** — bez zmian (tier `cs.tier_bag` już płynie).

`drive_speed_mult` jest route-constant (jeden kurier = jeden tier) — mnoży wszystkie
nogi jazdy. Mnożnik ruchu (V326_OSRM_TRAFFIC_TABLE) i tempo kuriera to dwa niezależne
czynniki multiplikatywne — stackują się poprawnie.

## Wartości — NIE surowa mapa v326 (podwójne liczenie)

`v326_speed_multiplier` był kalibrowany na CAŁKOWITYM czasie dostawy przy DWELL
płaskim 2.0. Po Sprincie 1 DWELL jest tier-aware (gold 2.5 ... slow 4.0) — część
tier-slowness siedzi już w DWELL. Zastosowanie pełnej mapy v326 do jazdy =
podwójne liczenie (szczeg. slow/new przeszacowane).

Dla `std` mnożnik v326 = 1.0 — drive-leg nic nie zmienia; std obsługuje Sprint 1.
Drive-leg dotyczy realnie gold (0.889) / slow (1.111) / new (1.3).

**Wartości muszą być zmierzone jako czysty współczynnik JAZDY**, z rezyduum PO
Sprincie 1 — `eta_calibration_log` (composition-clean, per tier) po odczycie
poniedziałkowym + kilku dniach. Do tego czasu tabela = 1.0 dla wszystkich (inert).

## Plan wdrożenia

- **3a (teraz):** plumbing — kod 1-4 wyżej, tabela wartości = 1.0 (inert).
  backup → edit → py_compile → testy regresji (zielone, bo default 1.0) → commit.
  **Bez deployu** — restart bez zmiany zachowania = zmarnowany restart.
- **3b (po danych, ~od wt 19.05):** kalibracja `DRIVE_SPEED_MULT_BY_TIER` z
  composition-clean rezyduum per tier po Sprincie 1. Deploy 3a+3b razem (jeden
  restart dispatch-shadow, off-peak, ACK).
- **Weryfikacja:** `eta_calibration_log` — composition-clean niedoszacowanie per
  tier ma zejść do ~0; odsetek bundli >35 min w dół.

## Ryzyka

- Podwójne liczenie z tier-DWELL → mitygacja: wartości z danych post-Sprint-1, nie
  z mapy v326.
- Za duży `drive_speed_mult` → obietnice zbyt pesymistyczne, R6 nadmiar KOORD →
  mitygacja: kalibracja krokami, monitoring pętlą uczącą.
- gold 0.889 < 1.0 skróciłby plan gold → mitygacja: gold composition-clean +1.0
  (plan NIE jest dla golda za długi) — wartość gold blisko 1.0, ostrożnie.
