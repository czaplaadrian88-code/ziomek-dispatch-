# Replay + korpus: 2 case'y Sylwia + kalibracja Fix C (2026-05-29)

## Cel
Adrian zgłosił 2 propozycje gdzie Ziomek NIE dał Sylwii (441), a człowiek nadpisał → Sylwia:
- **476816** Baanko → Handlowa 7 (proposed Andrei K 484, score −14.36)
- **476825** Doner Kebab → Bema 101/42 (proposed Adrian Cit 457, score −33.36)

Plan: NIE kodować od razu — najpierw replay 2 case'ów + szerszy korpus, by skalibrować próg „co-pickup exemption" (wstępnie 0.5 km).

## Diagnoza 2 case'ów (z produkcyjnych shadow logów = ground truth)

**476816 Baanko — RACE condition (Fix C NIE zaangażowany):**
- 09:35:45 ASSIGNED 476815 (Baanko #1) → Sylwia
- 09:35:46 decyzja 476816 → `NO_GPS_DEMOTE`, proposal Andrei K
- 09:35:47 `PACKS_CATCHUP 476815 → Sylwia` (bag zarejestrowany 1 s PO decyzji)
- W chwili decyzji bag Sylwii pusty → wyglądała jak blind+empty pre-shift → demote.
- Drop-drop Pułaskiego↔Handlowa = **1.36 km** (idealny bundle, ta sama restauracja). Fix C tu nie strzelił.

**476825 Doner — Fix C decydujący dla TEGO case'u:**
- `FIX_C bundle_cap cid=441 deliv_spread=10.58km > 8.0 → zero bonus_l2=12.7 continuation=30.0` (−42.7 pkt)
- Bag Sylwii: Kumar's (→Mieszka I, E) + Grill Kebab (→NSZ, NW). Nowe: Doner (→Bema, centrum).
- co-pickup Doner→Kumar's = **0.72 km** (NIE <0.5 — wstępny próg by to PRZEGAPIŁ).
- Nowy drop Bema (centrum) leży MIĘDZY Mieszka I (E) a NSZ (NW): max-para = 7.72 km Mieszka↔NSZ, **dodanie Bemy = +0.0 km**. Spread 10.58 km istniał PRZED nowym zleceniem.

## Korpus (dispatch.log, 21 dni 08–29.05)
- 7390 FIX_C firings; **7211 genuine (≤30 km)**, **179 geocode-corrupt (>30 km, do 536 km)** ← Fix C strzela na śmieciowych coordach (osobny bug).
- 3522 PANEL_OVERRIDE (proposed≠actual).
- **496 / 464** = override DO kuriera, którego Fix C ukarał („wzorzec Sylwia"), ~24/dzień.

## Kalibracja — WSZYSTKIE warianty Fix C ODRZUCONE

Porównanie treatment (człowiek wybrał ukaranego kuriera, n=464) vs control (n=6458):

| metryka | treatment | control | dyskryminuje? |
|---|---|---|---|
| co-pickup dist p50 / p75 (km) | 0.24 / 0.74 | 0.33 / 0.75 | **NIE** |
| marginal spread dodany p75 (km) | 1.9 | 1.9 | **NIE** |
| nowy drop interior (≤1km dodaje) | 66% | 66% | **NIE** |
| bag już >8km przed nowym dropem | 53% | 50% | **NIE** |

- **Co-pickup exemption**: każdy próg (0.5–2.0 km) zwalnia 75–90% WSZYSTKICH firings, precision ~7% → praktycznie wyłącza Fix C.
- **Marginal/bag-already-wide rule**: zwalnia treatment i control w tym samym tempie (53% vs 50%) → zero dyskryminacji.

**Wniosek:** geometria bundla (pickup ani delivery) NIE odróżnia „dobrych" override'ów od tła. Override na ukaranego, szeroko-rozłożonego kuriera NIE jest wyjaśniony geometrią Fix C.

## Hipoteza „sweeper" też odrzucona
Treatment rozłożony na 32 kurierów (top-3 = 25%), proporcjonalnie do wolumenu override'ów. Fix-C-penalized = mniejszość (~16%) override'ów nawet u top kurierów (Michał K 44/251, Adrian R 40/253). Override'y są wielo-przyczynowe (grafik, tier, pojemność, wiedza lokalna), nie polityka jednego zamiatacza.

## Rekomendacje
1. **Race (Baanko)** = realny, izolowany bug → naprawić (świeże przypisania w snapshot bagu przed scoringiem / detekcja co-restaurant pending bundle). Niezależne od Fix C.
2. **NIE relaksować Fix C globalnie** — kalibracja pokazuje że każda relaksacja regresuje tyle samo control co treatment.
3. Doner-type = realny ale rzadki/idiosynkratyczny na poziomie korpusu. Definitywny test: **score-level replay A/B (Fix C on/off) na treatment** — jak często zerowanie Fix C JEST decydujące (flip winnera przeciw człowiekowi). Jeśli rzadko → Fix C zostaje. Jeśli często → potrzebny mechanizm capacity/fleet-aware (Economics M3 FleetLoad), NIE spread.
4. **Bonus bug:** guard sanity na deliv_spread (skip Fix C gdy >~30 km = zepsute coordy).

## Lekcja
Kalibracja PRZED kodem uchroniła przed złym fixem: intuicyjny „co-pickup exemption 0.5 km" (a) przegapiłby sam case Doner (0.72 km) i (b) wyłączyłby Fix C (precision 7%). Dwa kolejne warianty geometryczne też padły na korpusie. „Pytaj nie zgaduj" = mierz dyskryminację treatment/control przed dotknięciem hot-path.

## Score-level A/B (Fix C ON vs OFF) — self-driving harness, lunch peak 9-13 UTC, 3 dni (27-29.05)
Narzędzie: `sequential_replay` (naive/cold/warm), env `ENABLE_BUNDLE_DELIV_SPREAD_CAP` 1 vs 0. Determinizm PYTHONHASHSEED=0.
Caveat: harness ma własne bagi (≠ produkcja); mierzy use-case AUTONOMII (Ziomek sam), nie reprodukuje produkcyjnych human-loaded bagów.

| mode/flag | orders | best_effort | sla_breach | KOORD |
|---|---|---|---|---|
| cold ON | 226 | 16 | 1 | 28 |
| cold OFF | 226 | 17 | 1 | 29 |
| warm ON | 226 | 11 | 2 | 30 |
| warm OFF | 226 | 11 | 2 | 30 |

- Human-agreement (warm PROPOSE vs realne przypisanie): ON 10.9% = OFF 10.9% (identyczne).
- **Per-order flips gdy Fix C OFF: 0 toward_human, 0 away_human, 0 neutral** (warm).
- **Fix C FIRED OBFICIE** w symulacji (217 firings / 60 orders w jednym 4h dniu) — więc „brak różnicy" = realny sygnał, nie „nie testowane".

### Wniosek A/B
Fix C odpala często, ale zerowanie bonusu **prawie nigdy nie zmienia zwycięzcy** (0 flipów warm; cold OFF nawet +1 best_effort = minimalnie GORZEJ). Czyli Fix C jest w praktyce **mało-decydujący na wynik**. Case Doner (gdzie BYŁ decydujący, flip Sylwii o włos) = wyjątek, nie reguła, i jest sprzężony z human-wide-loading, którego autonomia nie tworzy.

## REKOMENDACJA FINALNA
1. **Race fix (Baanko)** — zrobić (poprawność), mały blast radius (12 decyzji/21d ≤10s).
2. **Geocode-corrupt guard** — zrobić jako „no-geometry na niemożliwych coordach" (179 firings/2.3%), NIE „przywróć bonus" (na śmieciu bonus też śmieć).
3. **Fix C — NIE ruszać.** Korpus: brak geometrycznego dyskryminatora (treatment≈control). A/B autonomii: prawie inert (0 flipów, removing go nie pomaga, lekko szkodzi). Produkcyjny wpływ na human-loaded bagi = niemierzalny bez nowego narzędzia (rekonstrukcja per-order stanu floty + GPS), a wszystkie dostępne dowody mówią „low impact, brak surgical fix".

Residualna decyzja dla Adriana: czy budować wierne per-order production-replay (z rekonstrukcją GPS) by zmierzyć produkcyjny wpływ Fix C — czy zaakceptować obecne dowody i zostawić Fix C.

## Race fix — weryfikacja: approach „decision-path orphan-resolve" JUŻ WDROŻONY (Faza 4)
`ENABLE_PANEL_PACKS_BAG_RECONSTRUCTION=True` (LIVE) → `_reconstruct_bag_from_panel_packs` (`courier_resolver.py:759`) odbudowuje bag kuriera z panel_packs gdy orders_state ma courier_id=None (lag V3.15). To DOKŁADNIE picked approach.
Baanko race przeszedł bo **panel_packs_cache też był stale** w sub-ticku decyzji (gałąź `else: cache stale`). Czyli cross-process persistence lag: orders_state.courier_id ORAZ panel_packs_cache oba o tick za późno w momencie decyzji 09:35:46 (PACKS_CATCHUP 09:35:47).
→ Picked approach = no-op (już jest). Realne opcje race: (a) pending-assignment buffer (panel_watcher pisze świeży plik „ostatnie Ns przypisań", decyzja merge'uje) — nowy mechanizm; (b) skrócenie okna u źródła; (c) accept low-ROI (12/21d, znika z autonomią — własne przypisania Ziomka emitują czyste eventy synchronicznie). Nie da się naprawić w czytelniku gdy persystowany sygnał sam jest stale.

## STAN KOŃCOWY SPRINTU (2026-05-29)
- Fix C: ZOSTAWIONE (decyzja Adriana, poparte korpusem+A/B).
- Geocode guard: ODRZUCONE (no-op decyzyjny; far/garbage outliers Fix C słusznie nie-bunduje; firings na przegrywających kandydatach). Osobny real item: upstream geocode robustness (brak city / Bema→Nowodworce).
- Race fix: picked approach (orphan-resolve) = już live (Faza 4); Baanko = cross-process stale-cache lag. DECYZJA PENDING: pending-assignment buffer vs defer.
- ZERO zmian w kodzie produkcyjnym w tym sprincie (wszystko unieważnione weryfikacją przed kodem). Lekcja #154 zapisana.

## Runda 2 kalibracji (2026-05-29) — „co-pickup ∧ R6-feasible" + fleet-context (na życzenie Adriana)
Produkcyjny route sim (`simulate_bag_route_v2`, sla=35) per case, treatment vs control sample.

| metryka | treatment | control | dyskryminuje? |
|---|---|---|---|
| R6-feasible (sla_violations==0) | 61% | 61% | **NIE** |
| tight co-pickup ≤1km | 78% | 81% | **NIE** |
| COMBO (tight ∧ R6-feasible) | 49% | 52% (wyżej!) | **NIE** |
| feasible-pool p50 / pool≤2 | 3 / 41% | 3 / 44% | **NIE** |

**5 dyskryminatorów testowanych, wszystkie negatywne** (co-pickup dist, marginal spread, bag-already-wide, co-pickup∧R6, fleet-pool). Control odpala „exemption" NAWET CZĘŚCIEJ (52% vs 49%) → człowiek ODRZUCA połowę ciasnych+feasible bundli → jego „tak/nie" jest świadomy i NIE-geometryczny.

## WERDYKT DEFINITYWNY: Doner-type NIE MA surgical static fix
Decyzja człowieka o bundlu na szeroko-rozłożonego kuriera jest NIEPRZEWIDYWALNA z żadnej logowanej cechy (geometria/feasibility/R6/pool floty). Każda statyczna reguła łapiąca Doner-type złapie ~tyle samo case'ów które człowiek ODRZUCA. Sterownikiem jest osąd fleet-orchestration nie ujęty w features (anticipation, wiedza o konkretnych kurierach, „designated wide-route"). Ścieżki: (a) bogatsze features oddające intencję człowieka (trudne, wiedza tacit), (b) uczyć z override-data (człowiek=label) — ale wymaga features które to przewidują, których korpus nie ma. → Z DZISIEJSZYMI features Doner-type zostaje human-override. Capacity-aware (M3 FleetLoad) to jedyny realny długoterminowy kierunek.

## PROBE WDROŻONY (Krok 1 race Baanko) — 2026-05-29 13:33 UTC
Commit `5f2c073`, tag `same-restaurant-race-probe-2026-05-29`. `shadow_dispatcher._probe_same_restaurant_race` (logging-only, flag `ENABLE_SAME_RESTAURANT_RACE_PROBE` flags.json hot-reload, try/except). Hook po `process_event` w `_tick`. 6/6 testów (`tests/test_same_restaurant_race_probe.py`). Restart dispatch-shadow off-peak (15:33 Warsaw) clean. 0 probe-fail.
Loguje `SAME_REST_RACE_PROBE oid=... orphan=<bool> visible_not_proposed=<bool> sibs=[...]` gdy nowe zlecenie z restauracji R + sibling z R w ostatnich 120s.
**Rozstrzyga fork:** orphan=True (sibling assigned ale cid=None/nie-w-bagu → wyścig danych, fix=proposal-time re-check) vs visible_not_proposed=True (sibling w bagu kuriera, kurier w puli, nie best → filtr/scoring np. pre_shift, NIE wyścig).
**PENDING ANALIZA:** zebrać realne captures (post-restart 13:33:08; filtruj test-leak 13:32:16 = fixtures 476815/476816) przez 1-2 peaki (~24-48h), policzyć rozkład orphan vs visible_not_proposed → wybrać precyzyjny fix (Krok 2). Uwaga hygiena: pytest zapisał 2 linie do prod shadow.log (module _log ma file handler — rodzina Lekcji #75); filtruj po ts≥restart.
