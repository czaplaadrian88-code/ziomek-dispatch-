# D5-ODBIOR — pomiar na KANONICZNYM targecie GPS (restaurant_last_inside_at)

**Data:** 2026-07-21 · **READ-ONLY** (zero zmian kodu/flag; jedyny zapis = ten katalog).
**Powod re-pomiaru:** Sol odrzucil poprzednia karte (5,40 / 51,6%) bo liczona na targecie
KLIKOWYM (`pickup_slip_koord_min = picked_up_at − czas_kuriera`). Kanon
`KPI_BINDING_V1` (`dispatch_v2/tools/eta_ground_truth.py:61`, `possession_event.field =
restaurant_last_inside_at`) wymaga GPS-proxy odbioru (ostatni punkt kuriera WEWNATRZ
geofence restauracji), a klik jest fallbackiem nizszej rangi.

## Definicja targetu GPS (zgodnie z eta_ground_truth.py)
- Zrodlo: `restaurant_dwell.json` (writer `restaurant_dwell_detector.py`), pole
  `departed_restaurant` = ostatni punkt wewnatrz geofence (`_iso(restaurant_last_inside)`
  w `eta_ground_truth.py:882`).
- **High-confidence** = `_geofence_confidence` (`:327`): `_source=="gps_geofence"`
  AND `_n_in_geofence>=2` AND `_min_dist_m<=_radius_m`.
- **Courier-match** (`_courier_matches`, `:340`): `dwell.courier_id == feat.courier_id`
  (guard przed bledna atrybucja po reassignie).
- `pickup_slip_GPS = restaurant_last_inside_at[Warsaw] − czas_kuriera[Warsaw, ten sam dzien]` [min].
- Kalibrator = najswiezsza predykcja per-order z `eta_calib_shadow.jsonl`
  (`pred_p50` = punkt centralny do MAE/bias; `pred_op` = obietnica P80 do late-band).
  Silnik = `eng_pickup_slip_min` (feature store). **Potwierdzone:** `real` w shadow ==
  `pickup_slip_koord_min` (klik) — kalibrator UCZONY/OCENIANY na kliku; tu oceniony na GPS.

## 1. Pokrycie GPS
| Warstwa | n |
|---|---|
| wpisy dwell (geofence) | 2764 |
| high-confidence | 2562 |
| + feat + czas_kuriera + \|slip\|≤180, courier-match | **1778** (0 mismatch, 341 bez cid dolaczone bez sprzecznosci) |
| **usable z predykcja kalibratora (zbior mierzony)** | **n=1734** (wspolny z silnikiem n=1527) |

Grubo ponad brame D4 (min_n=200).

## 2. Progi D5 (cytat KPI_BINDING_V1) vs zmierzone na GPS
| Kryterium | Prog | GPS (n=1734) | Werdykt |
|---|---|---|---|
| MAE | ≤ 6.0 | **5.13** (winz p99 5.02) | ✅ |
| poprawa vs silnik | ≥ 25% | **48.4%** (n=1527) | ✅ duzy zapas |
| late-band (odbior pozniej niz obietnica P80) | 15–22% | **19.4%** | ✅ |
| \|bias mediany\| | ≤ 1.5 | **+0.22** | ✅ |
| p90 \|bledu\| | ≤ 20 | **10.34** | ✅ |
| coverage | n≥200 | **n=1734** | ✅ |
| **RAZEM** | | | **✅ GO** |

Silnik (baseline) MAE na GPS = **9.80** na wspolnym zbiorze.

## 3. Klik → GPS: czy zmienia werdykt?
**NIE.** Na tych samych 1734 zleceniach kontrola klikowa: MAE 5.16, poprawa 45.3%,
late-band 15.8%, bias +1.19 — rowniez GO. Target GPS lezy **+0.85 min (mediana)** pozniej
niz klik (kurier klika „odebrane", potem opuszcza geofence). Ten maly shift lekko
**re-centruje** kalibrator: bias mediany +1.19 (klik) → **+0.22 (GPS)**, late-band
15.8% → **19.4%** (glebiej w pasmie [15,22]). MAE praktycznie bez zmian (5.16 → 5.13).

Poprzednie 5,40/51,6% z karty to inne (szersze) okno holdout; tu na zbiorze GPS-high-conf
z najswiezsza predykcja per-order wychodzi 5,13/48,4% — ten sam wniosek, **z zapasem**.

## 4. Werdykt i czego brakuje
- **WERDYKT PICKUP na wlasciwym (GPS) targecie: GO co do progow D5** — wszystkie 6 kryteriow
  spelnione z zapasem, target GPS nie pogarsza wyniku (marginalnie lepszy niz klik).
- **Zastrzezenia / czego brakuje:** (a) kalibrator jest UCZONY na kliku, wiec na GPS ma
  ~0 bias przypadkowo (przez maly +0.85 shift) — docelowo warto przeuczyc etykiete na
  GPS-possession by bias byl gwarantowany, nie fortunny; (b) pomiar to okno cienia
  07-07→21.07 (predykcje) ∩ high-conf geofence — 1734 z 3129 karty (GPS zaweza pule do
  zlecen z pewnym geofence); (c) courier-match odrzucil 0 wierszy (341 bez cid dolaczone),
  wiec atrybucja nie zaburza wyniku. **Flip = OSOBNA brama + ACK Adriana; nic nie flipowano.**

### Zrodla (read-only)
- Progi: `dispatch_v2/tools/eta_ground_truth.py` (KPI_BINDING_V1, `_geofence_confidence`, `_courier_matches`)
- Target GPS: `/tmp/d5snap/restaurant_dwell.json` (== zywy `dispatch_state/restaurant_dwell.json`)
- Truth/feature store: `/tmp/d5snap/eta_calib.db` (`eta_calib_features`)
- Predykcje kalibratora: `dispatch_state/eta_calib_shadow.jsonl` (`real`==klik potwierdzone)
- Skrypt + liczby: `measure.py` + `result.json` (obok)
